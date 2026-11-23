"""测试环境交给 AI：AI 会话交回的提议（采纳、忽略），以及「让 AI 来处理」时交给会话的那段话（环境、准备步骤、服务没起来时的情况）。

测试环境本身在 envs.py。分层见 tests/test_layers.py。
"""
import contextlib
import datetime as dt
import os
import re

from ..core.common import main_checkout, SCRIPT
from . import config as envconfig
from .views import env_show, STATUS_WORDS
from .prep import prep_view
from .values import all_secret_values, deps_view, env_scan, redact, withheld_note
from .clean import _clean_step, clean_service
from .store import _commit, _load, _log, _rel, _text, find_env, in_use, local_state, read_local, SECRET_WORDS, VAR_NAME, write_env
from ..testsets.edits import clean_config, testset_config
from ..testsets.store import read_set, result_path
from pathlib import Path

__all__ = ['env_propose', 'env_adopt', 'env_ignore', 'handoff']


PROPOSAL_KINDS = {'service': '启动命令', 'config': '配置文件', 'var': '环境变量', 'prep': '准备步骤', 'testset': '测试集设置', 'note': '说明'}


PROPOSE_SHAPE = ('{"summary": "…", "prep": [{"note", "command", "dir", "when": "always|changed", "watch": [...], "timeout", "why"}], '
                 '"services": [{"name", "command", "port", "dir", "accept": [{"name", "value"}], "why"}], "config": {"file", "why"}, "vars": [{"name", "value", "secret", "why"}], '
                 '"testsets": [{"id": "TS-001", "classpath": [...], "build": "…", "entry": "…", "why"}]}')


def env_propose(root, ref, payload, session=None):
    """A session hands back what it found: preparation steps (what it ran to make things work), services (name, command, port,
    dir), the config file, the environment variables needed (names; a value only for what is plainly not secret), and a test
    set's code-side settings (classpath, build, access code). Kept on this machine as 'AI 的建议' until the user adopts or ignores
    each item; added after the ones still waiting. Secret values are never taken."""
    home = main_checkout(root)
    env = find_env(home, ref)
    if not isinstance(payload, dict):
        raise ValueError('提议要是一个 JSON 对象：' + PROPOSE_SHAPE)
    items = []

    def add(kind, body, why):
        items.append(dict(body, kind=kind, why=_text(why, 400), state='pending'))
    for i, st in enumerate(payload.get('prep') or [], 1):
        add('prep', _clean_step(st, f'prep 第 {i} 条'), st.get('why') if isinstance(st, dict) else '')
    for s in payload.get('services') or []:
        if not isinstance(s, dict) or not _text(s.get('name')):
            raise ValueError('services 的每一项要有 name（后端、前端…）。')
        add('service', clean_service(s, 0), s.get('why'))
    if payload.get('config'):
        cfg = payload['config']
        rel = _rel(cfg.get('file') if isinstance(cfg, dict) else cfg, '配置文件')
        if not (Path(home) / rel).is_file():
            raise ValueError(f'配置文件 {rel} 不在项目里。')
        if not envconfig.config_kind(rel):
            raise ValueError(f'{rel} 不是认得的配置文件（application*.yml / .yaml、.properties、.env）。')
        add('config', {'file': rel}, cfg.get('why') if isinstance(cfg, dict) else payload.get('config_why'))
    dropped = []
    for v in payload.get('vars') or []:
        if not isinstance(v, dict) or not VAR_NAME.match(_text(v.get('name'))):
            raise ValueError(f'vars 的每一项要有合法的 name（字母、数字、下划线）：{v}')
        name = _text(v.get('name'), 100)
        secret = bool(v.get('secret')) or bool(SECRET_WORDS.search(name))
        body = {'name': name, 'secret': secret}
        if not secret and v.get('value') not in (None, ''):
            body['value'] = _text(v.get('value'), 2000)
        elif secret and v.get('value') not in (None, ''):
            dropped.append(name)
        add('var', body, v.get('why'))
    if payload.get('testsets'):
        for t in payload['testsets']:
            if not isinstance(t, dict) or not t.get('id'):
                raise ValueError('testsets 的每一项要有 id（测试集编号）。')
            ts = read_set(home, t['id'])
            fields = {k: t[k] for k in ('lang', 'entry', 'classpath', 'build') if k in t}
            if not fields:
                raise ValueError(f'testsets 里 {ts["id"]} 没有要改的设置（lang、entry、classpath、build）。')
            clean_config(ts, fields)  # 现在就检查一遍，采纳时按那时的设置再检查
            add('testset', {'set': ts['id'], 'title': ts.get('title') or '', 'fields': fields}, t.get('why'))
    if not items:
        raise ValueError('提议里没有内容：至少给 prep、services、config、vars、testsets 中的一样。')
    at = dt.datetime.now().isoformat(timespec='seconds')
    sid = session or os.environ.get('RELAYFORGE_SESSION_ID') or os.environ.get('CLAUDE_CODE_SESSION_ID') or ''
    with local_state(home) as loc:
        old = loc['proposals'].get(env['id']) or {}
        keep = [i for i in old.get('items') or [] if i.get('state') == 'pending']
        k = max([i.get('k') or 0 for i in old.get('items') or []], default=0)
        for item in items:
            k += 1
            item['k'] = k
            item['at'] = at
        summary = _text(payload.get('summary'), 1000)
        loc['proposals'][env['id']] = {'at': at, 'session': sid, 'summary': summary or old.get('summary') or '', 'items': keep + items}
    return {'id': env['id'], 'name': env['name'], 'items': len(items), 'waiting': len(keep) + len(items), 'dropped_secret_values': dropped}


def _item_words(i):
    if i['kind'] == 'prep':
        return f'准备步骤「{i.get("note")}」'
    if i['kind'] == 'testset':
        return f'测试集 {i.get("set")} 的设置'
    return f'{PROPOSAL_KINDS[i["kind"]]} {i.get("name") or i.get("file")}'


def _decide(root, ref, ks, adopt):
    home = main_checkout(root)
    env = find_env(home, ref)
    loc = read_local(home)
    prop = loc['proposals'].get(env['id'])
    if not prop:
        raise ValueError(f'{env["name"]} 没有 AI 的建议。')
    want = {int(str(k).lstrip('#')) for k in ks} if ks else {i['k'] for i in prop['items'] if i['state'] == 'pending'}
    hit = [i for i in prop['items'] if i['k'] in want]
    if not hit:
        raise ValueError('没有找到这几条建议。')
    done, sets = [], []
    for item in hit:
        if item['state'] != 'pending':
            continue
        if adopt and item['kind'] == 'testset':
            testset_config(home, item['set'], item['fields'], by='用户采纳了 AI 的建议，')
            sets.append(item['set'])
        elif adopt:
            _apply(env, item)
        item['state'] = 'adopted' if adopt else 'ignored'
        done.append(item['k'])
    env_items = [i for i in hit if i['k'] in done and i['kind'] != 'testset']
    if adopt and env_items:
        _log(env, '采纳了 AI 的建议：' + '、'.join(_item_words(i) for i in env_items))
        write_env(home, env)
    with local_state(home) as l2:
        cur = l2['proposals'].get(env['id'])
        if cur:
            states = {i['k']: i['state'] for i in prop['items']}
            for i in cur.get('items') or []:
                if i['k'] in done:
                    i['state'] = states[i['k']]
    if adopt and env_items:
        if any(i['kind'] == 'config' for i in env_items):
            with contextlib.suppress(ValueError):
                env_scan(home, env['id'])
        _commit(home, f'「{env["name"]}」采纳了 AI 的建议')
    return env_show(home, env['id'], reveal=True)


def _apply(env, item):
    kind = item['kind']
    if kind == 'service':
        services = env.setdefault('services', [])
        body = {'name': item['name'], 'command': item.get('command', ''), 'port': item.get('port'), 'dir': item.get('dir', '')}
        if item.get('accept'):
            body['accept'] = item['accept']
        for s in services:
            if s['name'] == item['name']:
                s.update(body)
                break
        else:
            services.append(body)
    elif kind == 'config':
        env['config'] = item['file']
    elif kind == 'var':
        vars_ = env.setdefault('vars', [])
        found = next((v for v in vars_ if v['name'] == item['name']), None)
        if found is None:
            v = {'name': item['name'], 'secret': bool(item.get('secret'))}
            if not v['secret']:
                v['value'] = item.get('value', '')
            vars_.append(v)
        elif not found.get('secret') and not item.get('secret') and item.get('value') is not None:
            found['value'] = item['value']
    elif kind == 'prep':  # 采纳后第一次执行仍要用户同意（命令的指纹还没同意过）
        steps = env.setdefault('prep', [])
        body = {k: item[k] for k in ('note', 'command', 'dir', 'when', 'watch', 'timeout')}
        same = next((p for p in steps if p['command'] == body['command'] and (p.get('dir') or '') == body['dir']), None)
        if same:
            same.update(body)
        else:
            nxt = int(env.get('next_prep') or 1)
            while any(p['id'] == f'P{nxt}' for p in steps):
                nxt += 1
            steps.append({'id': f'P{nxt}', **body})
            env['next_prep'] = nxt + 1


def env_adopt(root, ref, ks=()):
    return _decide(root, ref, ks, True)


def env_ignore(root, ref, ks=()):
    return _decide(root, ref, ks, False)


def _env_brief(home, env, loc):
    """The environment as a session may see it: names, non-secret values, the state of things; never a secret value."""
    lines = [f'当前测试环境：「{env["name"]}」（{env["id"]}）' + (f'：{env["note"]}' if env.get('note') else '') + '。']
    lines.append('- 怎么启动：' + ('；'.join(f'{s["name"]} `{s["command"]}`' + (f' 端口 {s["port"]}' if s.get('port') else '') + (f'（在 {s["dir"]}）' if s.get('dir') else '')
                                     + ('，验收时额外带 ' + '、'.join(f'{a["name"]}={a["value"]}' for a in s['accept']) if s.get('accept') else '')
                                     for s in env.get('services') or []) or '还没填'))
    lines.append(f'- 配置文件：{env.get("config") or "还没选"}')
    sec = (loc.get('secrets') or {}).get(env['id']) or {}
    vs = []
    for v in env.get('vars') or []:
        if v.get('secret'):
            vs.append(f'{v["name"]}（保密，' + ('这台电脑上填了值' if (sec.get('vars') or {}).get(v['name']) else '还没填值') + '，你看不到也不要去找）')
        else:
            vs.append(f'{v["name"]}={v.get("value", "")}')
    lines.append('- 环境变量：' + ('；'.join(vs) or '没有'))
    lines.append('- AI 的会话运行时带不带保密值：' + ('用户允许了，带' if loc['ai_secrets'].get(env['id']) else '不带（只有用户在应用里点的运行才带）'))
    steps = prep_view(home, env, loc)
    if steps:
        lines.append('- 准备步骤（程序在运行测试集之前按顺序执行）：')
        for st in steps:
            last = st.get('last')
            lines.append(f'  {st["n"]}. {st["note"]}：`{st["command"]}`' + (f'，在 {st["dir"]}' if st.get('dir') else '') + '，'
                         + (f'{"、".join(st["watch"])} 变了才执行' if st['when'] == 'changed' else '每次运行前执行')
                         + ('' if st['approved'] else '，用户还没同意执行') + (f'；上次{"成功" if last["ok"] else "失败"}（{last["at"]}）' if last else '；还没执行过'))
    else:
        lines.append('- 准备步骤：还没有')
    deps = deps_view(home, env, loc)
    if deps:
        lines.append('- 外部依赖（地址从配置文件读出，口令打码）：' + '；'.join(f'{d["name"]}（{d["type"]}）{d["address"] or "没配"}' + (f' {STATUS_WORDS[d["check"]["status"]]}' if d.get('check') else '')
                                                         for d in deps))
    return lines


def start_failed_words(home, svc, report):
    """The middle of the handoff text when a service did not start: which one, the command, the directory, the exit code or why,
    the tail of its log (already scrubbed by the app; scrubbed again by the caller)."""
    report = report if isinstance(report, dict) else {}
    code = report.get('code')
    reason = str(report.get('reason') or '').strip()
    tail = str(report.get('tail') or '').strip()[-6000:]
    lines = [f'命令：`{report.get("command") or svc.get("command")}`', f'执行目录：{report.get("cwd") or svc.get("dir") or home}']
    if svc.get('port'):
        lines.append(f'端口：{svc["port"]}（程序探测这个端口能不能连上，连上才算运行中）')
    lines.append('结果：' + (reason or (f'进程退出了，退出码 {code}' if code is not None else '没有启动起来')) + '。')
    if report.get('accept'):
        lines.append('验收时额外带的环境变量：' + '；'.join(f'{a["name"]}={a["value"]}' for a in report['accept'] if isinstance(a, dict) and a.get('name')))
    lines += ['日志的最后部分（保密值已打码）：', '```', tail or '（没有输出）', '```']
    return lines


def handoff(root, kind, ref=None, step=None, scope=None, service=None, report=None):
    """The words the app hands to a session for 「让 AI 来处理」, written by the program: what is broken (a test set that would
    not run, a preparation step that failed, dependencies that cannot be reached), the full error and the tail of the output, the
    environment's name and non-secret settings, what the session may do and how to hand back. Every secret value of every
    environment of the project is scrubbed from it. {title, text, env, set}"""
    home = main_checkout(root)
    loc = read_local(home)
    env = None
    head, body = '', []
    set_id = None
    rf = 'python3 ' + (str(SCRIPT) if re.match(r'^[\w./-]+$', str(SCRIPT)) else "'" + str(SCRIPT).replace("'", "'\\''") + "'")  # 这份 rf.py 自己的位置
    if kind == 'set':
        ts = read_set(home, ref)
        set_id = ts['id']
        rec = _load(result_path(home, ts['id'], scope)) or {}
        if rec.get('env'):
            with contextlib.suppress(ValueError):
                env = find_env(home, rec['env']['id'])
        title = f'处理：测试集 {ts["id"]}「{ts.get("title") or ""}」跑不起来'
        head = (f'测试集 {ts["id"]}「{ts.get("title") or ""}」（功能点 {ts.get("feature")}，语言 {ts.get("lang") or "python"}，接入代码 {ts.get("entry")}'
                + (f'，classpath {"、".join(ts["classpath"])}' if ts.get('classpath') else '') + (f'，build 命令 `{ts["build"]}`' if ts.get('build') else '')
                + f'）跑不起来：程序启动它的时候出错了，数据一条都没有执行到。')
        if rec:
            body += [f'上次运行：{rec.get("at")}，代码目录 {rec.get("place")}' + (f'，{"按 " + scope + " 眼里的数据" if scope else "按主线的数据"}') + '。',
                     '完整的错误（程序记下的，保密值已打码）：', '```', (rec.get('error') or '（没有记下错误）').strip(), '```']
            if rec.get('secrets_withheld'):
                body.append(withheld_note(rec['secrets_withheld']))
        else:
            body.append(f'还没有运行记录：先用 {rf} test run ' + ts['id'] + ' 跑一次看错误。')
        verify = f'{rf} test run {ts["id"]}' + (f' --req {scope}' if scope and not scope.startswith('F-') else f' --fix {scope}' if scope else '')
    elif kind == 'prep':
        env = find_env(home, ref or in_use(home, loc) or '')
        steps = prep_view(home, env, loc)
        st = next((x for x in steps if x['id'] == str(step or '').upper()), None) or next((x for x in steps if x.get('last') and not x['last']['ok']), None)
        if not st:
            raise ValueError(f'{env["name"]} 没有失败的准备步骤。')
        last = st.get('last') or {}
        title = f'处理：准备步骤「{st["note"]}」失败'
        head = f'测试环境「{env["name"]}」的准备步骤 {st["n"]}「{st["note"]}」' + ('失败了' if last and not last.get('ok') else '要处理') + '：运行测试集之前程序要先执行它，它不过，测试集就都跑不起来。'
        body += [f'命令：`{st["command"]}`', f'执行目录：{Path(last.get("place") or home) / st["dir"] if st.get("dir") else last.get("place") or home}',
                 f'什么时候执行：' + (f'{"、".join(st["watch"])} 变了以后' if st['when'] == 'changed' else '每次运行前') + f'；超时 {st["timeout"]} 秒']
        if last:
            body += [f'上次执行：{last.get("at")}，' + (f'退出码 {last.get("code")}' if last.get('code') is not None else '没有正常结束') + f'，用时 {last.get("ms")} 毫秒。',
                     '输出的最后部分（保密值已打码）：', '```', (last.get('tail') or '（没有输出）').strip(), '```']
            if last.get('withheld'):
                body.append(withheld_note(last['withheld']))
        verify = f'{rf} env prep-run {env["id"]} {st["id"]}（只执行用户同意过的命令），再 {rf} test run'
    elif kind == 'check':
        env = find_env(home, ref or in_use(home, loc) or '')
        deps = deps_view(home, env, loc)
        bad = [d for d in deps if (d.get('check') or {}).get('status') == 'bad']
        title = f'处理：测试环境「{env["name"]}」有连不上的依赖'
        head = f'测试环境「{env["name"]}」的连通检查里有 {len(bad)} 个依赖连不上（检查只做 TCP 连接）。'
        for d in bad or deps:
            c = d.get('check') or {}
            body.append(f'- {d["name"]}（{d["type"]}）{d["address"] or "没配"}，来自{"配置文件" if d["from"] == "config" else "手改"}' + (f'第 {d["line"]} 行' if d.get('line') else '')
                        + (f'：{STATUS_WORDS.get(c.get("status"), "还没检查")}' + (f'（{c["detail"]}）' if c.get('detail') else '') if c else ''))
        body.append('可能的办法：服务没启动就找出怎么在本机把它起来（需要 Docker 或安装软件的，按下面的规矩先问我）；地址不对就提议改配置或环境变量。')
        verify = f'{rf} env check {env["id"]}'
    elif kind == 'start':
        env = find_env(home, ref or in_use(home, loc) or '')
        svc = next((x for x in env.get('services') or [] if x['name'] == service), None)
        if not svc:
            raise ValueError(f'{env["name"]} 没有服务「{service}」。')
        where = f'需求 {scope} 的工作目录' if scope and scope != 'main' else '项目主目录（主线）'
        title = f'处理：服务「{svc["name"]}」启动失败'
        head = f'人工验收要从{where}把测试环境「{env["name"]}」的服务「{svc["name"]}」跑起来，没有启动成功。程序按「怎么启动」里的命令启动它（先执行要执行的准备步骤），带上这套环境的环境变量。'
        body += start_failed_words(home, svc, report)
        verify = '在同一个目录里用同样的命令试（你的会话里没有保密值，缺了它们起不来是正常的，说明一下就行）；不要留下在后台跑着的进程，试完停掉'
    else:
        raise ValueError('要说处理什么：--set 测试集编号、--prep 测试环境（--step 步骤）、--check 测试环境或 start 测试环境 --service 服务。')
    if env is None:
        ident = in_use(home, loc)
        env = find_env(home, ident) if ident else None
    lines = [f'【{title}】', head, *body, '']
    if env:
        lines += _env_brief(home, env, loc)
    else:
        lines.append('现在没有在用的测试环境：试出来的做法先用文字告诉我，我建好测试环境以后你再交回。')
    eid = env['id'] if env else 'ENV-001'
    lines += ['',
              '你要做的：找出原因，把路打通一次——在项目目录里试出能让它跑起来的做法（装依赖、编译、类路径、启动命令、端口、配置文件、环境变量名……）。',
              '以后每次运行由程序照着你交回的做法执行，不经过你；所以做法要写成准备步骤这样能重复执行的命令，不要依赖你这次会话里临时设的东西。',
              '不要改测试数据和期望；接入代码有错可以改，被测的业务代码不要为了迁就测试去改，觉得它有问题就停下来告诉我。',
              '密钥你看不到，也不要去找、不要写进任何文件：需要的保密变量只交回名字。你的会话里运行测试集和准备步骤时不带保密值，缺了它们跑不起来是正常的，说明一下就行。',
              f'不要运行 rf.py env save、adopt、ignore、approve、ai-secrets、reveal：设置由我在应用里采纳，第一次执行的命令要我看过点同意。',
              '',
              f'做完怎么交回：用 {rf} env propose {eid} --file - 交回（JSON 从标准输入给），可以有这几样：{PROPOSE_SHAPE}。'
              '准备步骤的 when 写 changed 时，watch 写哪些文件变了才要重新执行（比如 pom.xml、package-lock.json）；命令里不要带任何保密值。',
              f'交回以前可以自己验证：{verify}。最后用两三句话告诉我原因、你试过什么、提议了什么；我在应用里采纳以后' + ('再点「启动」。' if kind == 'start' else '点「再跑一次」。')]
    text = redact('\n'.join(lines), all_secret_values(home))
    return {'title': title, 'text': text, 'env': {'id': env['id'], 'name': env['name']} if env else None, 'set': set_id}

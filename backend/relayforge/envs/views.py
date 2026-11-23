"""测试环境的查看：列表、一个环境的详情（保密值打码、外部依赖、准备步骤的状态），以及命令行和交给会话时用的文字。

只读不写。测试环境的存储在 envs.py。分层见 tests/test_layers.py。
"""
import copy
import json
import sys

from ..core.language import display_text
from ..core.common import main_checkout
from . import config as envconfig
from .prep import prep_view
from .values import _scan_view, deps_view, env_scan, jwt_info, redact, secret_values
from .store import all_envs, ENVS, find_env, in_use, local_path, MASK, read_local, WHERE
from pathlib import Path

__all__ = ['env_list', 'env_show']


def _summary(home, env, loc):
    users = env.get('users') or []
    return {'id': env['id'], 'name': env.get('name'), 'note': env.get('note', ''), 'in_use': in_use(home, loc) == env['id'],
            'services': len(env.get('services') or []), 'vars': len(env.get('vars') or []), 'users': len(users),
            'secrets': sum(1 for v in env.get('vars') or [] if v.get('secret')) + sum(1 for r in (env.get('request') or {}).get('items') or [] if r.get('secret'))
                       + sum(1 for u in users for f in u.get('fields') or [] if f.get('secret')),
            'config': env.get('config') or '', 'updated': env.get('updated')}


def env_list(root):
    home = main_checkout(root)
    loc = read_local(home)
    envs = all_envs(home)
    return {'use': in_use(home, loc), 'envs': [_summary(home, e, loc) for e in envs], 'local': str(local_path(home))}


def _secret_item(item, value, reveal):
    out = dict(item)
    out['has_value'] = value not in (None, '')
    out['value'] = (value or '') if reveal else None
    return out


def env_show(root, ref=None, reveal=False):
    """One environment as the app and the sessions see it. Secret values are null unless reveal (the app only)."""
    home = main_checkout(root)
    loc = read_local(home)
    if ref is None:
        ref = in_use(home, loc)
        if not ref:
            raise ValueError(display_text('现在没有在用的测试环境；要说看哪一套。',
                                          'There is no active test environment. Select the environment to view.'))
    env = find_env(home, ref)
    if env['id'] not in loc['scans'] and env.get('config'):
        env_scan(home, env['id'])
        loc = read_local(home)
    sec = loc['secrets'].get(env['id']) or {}
    current_user = loc['user'].get(env['id'])
    users = env.get('users') or []
    if current_user not in {u['id'] for u in users}:
        current_user = users[0]['id'] if users else None
    out = {k: copy.deepcopy(env.get(k)) for k in ('id', 'name', 'note', 'services', 'config', 'created', 'updated', 'log')}
    out['file'] = f'{ENVS}/{env["id"]}.json'
    out['in_use'] = in_use(home, loc) == env['id']
    out['use'] = in_use(home, loc)
    out['vars'] = [_secret_item(v, (sec.get('vars') or {}).get(v['name']), reveal) if v.get('secret') else dict(v, has_value=v.get('value', '') != '')
                   for v in env.get('vars') or []]
    req = env.get('request') or {}
    out['request'] = {'items': [_secret_item(r, (sec.get('request') or {}).get(f'{r["in"]}:{r["name"]}'), reveal) if r.get('secret') else dict(r, has_value=r.get('value', '') != '')
                                for r in req.get('items') or []], 'before': req.get('before', ''), 'after': req.get('after', '')}
    out['users'] = []
    for u in users:
        fields = []
        for f in u.get('fields') or []:
            if f.get('secret'):
                value = ((sec.get('users') or {}).get(u['id']) or {}).get(f['key'])
                item = _secret_item(f, value, reveal)
                if reveal:
                    item['jwt'] = jwt_info(value)
            else:
                item = dict(f, has_value=f.get('value', '') != '', jwt=jwt_info(f.get('value')))
            if item.get('jwt') is None:
                item.pop('jwt', None)
            fields.append(item)
        out['users'].append({'id': u['id'], 'name': u['name'], 'note': u.get('note', ''), 'fields': fields, 'current': u['id'] == current_user})
    out['user'] = current_user
    out['deps'] = deps_view(home, env, loc)
    out['scan'] = _scan_view(home, env, loc['scans'].get(env['id']))
    out['config_candidates'] = envconfig.find_configs(home)
    if out['config'] and out['config'] not in out['config_candidates']:
        out['config_candidates'].insert(0, out['config'])
    prop = loc['proposals'].get(env['id'])
    out['proposal'] = copy.deepcopy(prop) if prop else None
    out['prep'] = prep_view(home, env, loc)
    out['ai_secrets'] = bool(loc['ai_secrets'].get(env['id']))
    out['envs'] = [_summary(home, e, loc) for e in all_envs(home)]
    out['revealed'] = bool(reveal)
    return out if reveal else redact(out, secret_values(home, env, loc))


def _read_payload(args):
    if args.file:
        raw = sys.stdin.read() if args.file == '-' else Path(args.file).read_text(encoding='utf-8')
    elif sys.stdin is not None and not sys.stdin.isatty():
        raw = sys.stdin.read()
    else:
        raise ValueError(display_text('没有收到数据：用 --file 文件，或从标准输入给 JSON（--file -）。',
                                      'Supply a file with --file or pass JSON on standard input with --file -.'))
    try:
        return json.loads(raw)
    except ValueError as error:
        raise ValueError(display_text(f'数据要是 JSON：{error}',
                                      f'Expected JSON data: {error}')) from None


STATUS_WORDS = {'ok': '能连上', 'bad': '连不上', 'none': '没配'}


def _show_text(e):
    lines = [f'{e["id"]} {e["name"]}' + ('（正在用）' if e['in_use'] else '') + (f'：{e["note"]}' if e.get('note') else ''), f'文件：{e["file"]}']
    lines.append('怎么启动：' + ('；'.join(f'{s["name"]} `{s["command"]}`' + (f' 端口 {s["port"]}' if s.get('port') else '') + (f'（在 {s["dir"]}）' if s.get('dir') else '') for s in e['services']) or '还没填'))
    lines.append(f'配置文件：{e["config"] or "还没选"}')
    for d in e['deps']:
        c = d.get('check')
        lines.append(f'  依赖 {d["name"]}（{d["type"]}）{d["address"]}，来自{"配置文件" if d["from"] == "config" else "手改"}'
                     + (f'，{STATUS_WORDS[c["status"]]}' if c else '') + (f'，{d["note"]}' if d.get('note') else ''))
    if e.get('scan') and e['scan'].get('error'):
        lines.append(f'  读配置文件：{e["scan"]["error"]}')
    for s in (e.get('scan') or {}).get('skipped') or []:
        lines.append(f'  跳过第 {s["line"]} 行：{s["why"]}')
    for v in e['vars']:
        lines.append(f'环境变量 {v["name"]} = ' + ((v['value'] if v['value'] is not None else (MASK if v['has_value'] else '（没填）')) + '（保密，只存本机）' if v['secret'] else v.get('value', '')))
    for r in e['request']['items']:
        lines.append(f'{WHERE[r["in"]]} {r["name"]}: ' + ((MASK if r['has_value'] else '（没填）') + '（保密）' if r['secret'] and r['value'] is None else r.get('value') or ''))
    for u in e['users']:
        lines.append(f'测试用户 {u["id"]} {u["name"]}' + ('（当前）' if u['current'] else '') + (f'：{u["note"]}' if u['note'] else '') + '；'
                     + '，'.join(f'{f["key"]}=' + (MASK if f['secret'] and f['value'] is None else str(f.get('value') or '')) for f in u['fields']))
    for st in e.get('prep') or []:
        last = st.get('last')
        lines.append(f'准备步骤 {st["n"]}（{st["id"]}）{st["note"]}：`{st["command"]}`' + (f'（在 {st["dir"]}）' if st.get('dir') else '')
                     + (f'，{"、".join(st["watch"])} 变了才执行' if st['when'] == 'changed' else '，每次运行前执行') + ('' if st['approved'] else '，用户还没同意执行')
                     + (f'，上次{"成功" if last["ok"] else "失败"}' if last else ''))
    lines.append('AI 的会话运行时带上保密值：' + ('用户允许了' if e.get('ai_secrets') else '不带（默认）'))
    if e.get('proposal'):
        pend = [i for i in e['proposal']['items'] if i['state'] == 'pending']
        if pend:
            lines.append(f'AI 的建议：{len(pend)} 条等用户在应用里采纳或忽略。')
    return '\n'.join(lines)

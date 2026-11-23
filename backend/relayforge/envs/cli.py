"""rf.py env …：测试环境的命令分发。出错写到标准错误、退出码 1。

测试环境本身在 envs.py，按需求准备验收实例在 accept.py；这里两边都要用，所以放在入口这一层。分层见 tests/test_layers.py。
"""
import json
import sys
from pathlib import Path

from . import config as envconfig
from ..core.common import main_checkout
from ..core.trust import require_app
from .handoff import PROPOSE_SHAPE, env_adopt, env_ignore, env_propose, handoff
from .edits import env_copy, env_new, env_pick_user, env_remove, env_rename, env_save, env_use
from .views import STATUS_WORDS, _read_payload, _show_text, env_list, env_show
from .prep import env_ai_secrets, env_approve, env_prep_run, prep_view
from .values import ai_values, env_check, env_resolve, env_scan, redact, secret_values
from .store import ID, find_env, in_use, read_local
from ..requirements.accept import clean, plan, scope_place


def env_command(root, args):
    """rf.py env …. Errors go to stderr with exit 1."""
    v = list(args.values)
    act = args.action
    try:
        if act == 'list':
            out = env_list(root)
            text = '\n'.join(f'{e["id"]} {e["name"]}' + ('（正在用）' if e['in_use'] else '') + (f'：{e["note"]}' if e['note'] else '')
                             + f'；{e["services"]} 个服务、{e["vars"]} 个环境变量、{e["users"]} 个测试用户' for e in out['envs']) or '还没有测试环境。'
        elif act == 'show':
            out = env_show(root, v[0] if v else None)
            text = _show_text(out)
        elif act == 'reveal':
            require_app('取保密值的明文')
            out = env_resolve(root, v[0] if v else None, args.user, reveal=True) if args.resolve else env_show(root, v[0] if v else None, reveal=True)
            text = json.dumps(out, ensure_ascii=False, indent=2)
        elif act == 'ai-values':
            out = ai_values(root, v[0] if v else None)
            text = json.dumps(out, ensure_ascii=False)
        elif act == 'resolve':
            out = env_resolve(root, v[0] if v else None, args.user)
            text = '\n'.join(f'{r["where"]} {r["name"]}: {r["value"]}' + (f'（没代进去：{"、".join(r["missing"])}）' if r['missing'] else '') for r in out['headers'] + out['query']) \
                or '这套环境没有请求的通用设置。'
        elif act == 'new':
            out = env_new(root, ' '.join(v), args.note)
            text = f'已新建测试环境 {out["id"]}「{out["name"]}」。'
        elif act == 'copy':
            if not v:
                raise ValueError('用法：env copy ENV-001 [--name 新名字]')
            out = env_copy(root, v[0], args.name)
            text = f'已复制出 {out["id"]}「{out["name"]}」。'
        elif act == 'rename':
            if len(v) < 2 and args.note is None:
                raise ValueError('用法：env rename ENV-001 新名字 [--note 说明]')
            ref = v[0] if v else ''
            cur = find_env(main_checkout(root), ref)
            out = env_rename(root, ref, ' '.join(v[1:]) or cur['name'], args.note)
            text = f'{out["id"]} 现在叫「{out["name"]}」。'
        elif act == 'rm':
            out = env_remove(root, v[0] if v else '')
            text = f'{out["id"]}「{out["name"]}」已删除，文件移到 {out["file"]}。'
        elif act == 'save':
            if not v:
                raise ValueError('用法：env save ENV-001 --file -（整份设置的 JSON，从标准输入给）')
            out = env_save(root, v[0], _read_payload(args))
            text = f'{out["id"]}「{out["name"]}」已保存。'
            if not args.json:
                out = None
        elif act == 'use':
            out = env_use(root, v[0] if v else '')
            text = f'现在用 {out["use"]}「{out["name"]}」。' if out['use'] else '现在不用测试环境。'
        elif act == 'user':
            if len(v) != 2:
                raise ValueError('用法：env user ENV-001 U1')
            out = env_pick_user(root, v[0], v[1])
            text = f'{out["env"]} 的当前测试用户是 {out["user"]}「{out["name"]}」。'
        elif act == 'scan':
            out = env_scan(root, v[0] if v else (in_use(main_checkout(root)) or ''))
            sc = out['scan']
            text = (f'读配置文件 {sc["file"]}：' + (sc['error'] or f'认出 {len(out["deps"])} 个依赖') + (f'，跳过 {len(sc["skipped"])} 处不支持的写法' if sc['skipped'] else '') + '。'
                    + ''.join(f'\n  {d["name"]}（{d["type"]}）{d["address"]}' for d in out['deps']))
        elif act == 'check':
            out = env_check(root, v[0] if v else (in_use(main_checkout(root)) or ''), args.timeout or envconfig.TIMEOUT)
            text = '\n'.join(f'{d["name"]}（{d["type"]}）{d["address"]}：{STATUS_WORDS[d["check"]["status"]]}' + (f'（{d["check"]["detail"]}）' if d['check'].get('detail') else '')
                             for d in out['deps']) or '没有依赖可以检查（先选配置文件，或手动加）。'
            secrets = secret_values(main_checkout(root), find_env(main_checkout(root), out['id']), read_local(main_checkout(root)))
            out, text = redact(out, secrets), redact(text, secrets)
        elif act == 'propose':
            if not v:
                raise ValueError('用法：env propose ENV-001 --file -（JSON：' + PROPOSE_SHAPE + '）')
            out = env_propose(root, v[0], _read_payload(args), args.session)
            text = f'已把 {out["items"]} 条建议交给用户（一共 {out["waiting"]} 条在等），等用户在应用里逐条采纳或忽略。' + (f'没有收下这些保密项的值：{"、".join(out["dropped_secret_values"])}（密钥由用户自己填）。' if out['dropped_secret_values'] else '')
        elif act == 'prep':
            home = main_checkout(root)
            env = find_env(home, v[0] if v else (in_use(home) or ''))
            out = {'id': env['id'], 'name': env['name'], 'place': str(Path(args.dir or home).resolve()), 'prep': prep_view(home, env, place=Path(args.dir or home).resolve())}
            text = '\n'.join(f'{st["n"]}. {st["id"]} {st["note"]}：`{st["command"]}`' + (f'（在 {st["dir"]}）' if st.get('dir') else '')
                             + f'；{st["due_why"]}' + ('' if st['approved'] else '；还没经用户同意执行') for st in out['prep']) or f'{env["name"]} 还没有准备步骤。'
        elif act == 'prep-run':
            home = main_checkout(root)
            ref = v[0] if v and ID.match(v[0]) else None
            place = args.dir
            if getattr(args, 'req', None) and not place:
                place = str(scope_place(home, args.req)[1])
            out = env_prep_run(root, ref, [x for x in v if not ID.match(x)], force=not getattr(args, 'due', False), place=place)
            text = '\n'.join([f'{r["n"]}. {r["note"]}：' + ('执行完了' if r['ok'] else '失败了') + f'（{r["ms"]} 毫秒）' for r in out['ran']]
                             + ([out['error']] if out['error'] else []) + ([] if out['ran'] or out['error'] else ['没有要执行的步骤。']))
            print(json.dumps(out, ensure_ascii=False) if args.json else text)
            return 1 if out['error'] else 0
        elif act == 'approve':
            if not v:
                raise ValueError('用法：env approve ENV-001 [P1 …] [--service 服务名 …]（都不写就是这套环境的全部准备步骤）')
            out = env_approve(root, v[0], v[1:], getattr(args, 'service', None) or ())
            text = f'{out["id"]}：同意执行 ' + '、'.join(out['approved'] + [f'服务「{n}」的启动命令' for n in out['services']]) + '。'
        elif act in ('plan', 'launch'):
            if act == 'launch':
                require_app('启动验收用的服务')
            ref = v[0] if v and ID.match(v[0]) else None
            out = plan(root, ref, getattr(args, 'req', None), getattr(args, 'service', None) or None, trusted=True if act == 'launch' else None,
                       prepare=act == 'launch', copy=getattr(args, 'copy', None) or 'reuse')
            text = (f'测试环境：{out["env"]["name"]}（{out["env"]["id"]}）' if out['env'] else out.get('why', '')) + f'\n代码目录：{out["place"]}\n临时目录：{out["scratch"]}' + ''.join(
                f'\n服务「{x["name"]}」：`{x["command"]}`，在 {x["cwd"]}' + (f'，端口 {x["port"]}' if x.get('port') else '') + ('' if x['approved'] else '，启动命令还没经用户同意')
                + ''.join(f'\n  验收时带 {a["name"]}={a["value"]}' for a in x['accept']) + (f'\n  {x["problem"]}' if x.get('problem') else '') for x in out['services'])
        elif act == 'accept-clean':
            out = clean(root, getattr(args, 'req', None))
            text = '已清掉：' + ('、'.join(out['removed']) or '没有要清的')
        elif act == 'ai-secrets':
            if len(v) != 2 or v[1] not in ('on', 'off'):
                raise ValueError('用法：env ai-secrets ENV-001 on|off')
            out = env_ai_secrets(root, v[0], v[1] == 'on')
            text = f'{out["id"]}：AI 的会话运行时' + ('带上保密值。' if out['ai_secrets'] else '不带保密值。')
        elif act == 'handoff':
            kinds = {'set': 'set', 'prep': 'prep', 'check': 'check', 'start': 'start'}
            if not v or v[0] not in kinds:
                raise ValueError('用法：env handoff set TS-001 [--req R-…|--fix F-…]、env handoff prep ENV-001 [--step P1]、env handoff check ENV-001、'
                                 'env handoff start ENV-001 --service 服务名 [--req R-…] [--file -（启动的情况 JSON：code、reason、tail、command、cwd）]')
            report = _read_payload(args) if v[0] == 'start' and args.file else None
            svc = (getattr(args, 'service', None) or [None])[0]
            out = handoff(root, v[0], v[1] if len(v) > 1 else None, args.step, args.req or args.fix, svc, report)
            text = out['text']
        elif act in ('adopt', 'ignore'):
            if not v:
                raise ValueError(f'用法：env {act} ENV-001 [建议的序号…]（不写序号就是全部）')
            out = (env_adopt if act == 'adopt' else env_ignore)(root, v[0], v[1:])
            text = f'{out["id"]}：' + ('已采纳。' if act == 'adopt' else '已忽略。')
            if not args.json:
                out = None
        else:
            raise ValueError(f'不认识的操作 {act}。')
    except (ValueError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(json.dumps(out, ensure_ascii=False) if args.json and out is not None else text)
    return 0

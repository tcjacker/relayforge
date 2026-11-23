"""rf.py branch … 和 rf.py publish …：分支和发布的命令分发。分层见 tests/test_layers.py。
"""
import json
import sys

from .branches import branch_list, branch_refresh, branch_switch, BranchError
from .publish import PUBLISH_CODES, publish_list, publish_pull, publish_run, publish_status, PublishError

__all__ = ['branch_command', 'publish_command']


def branch_command(root, args):
    """List cached branches or perform a branch operation requested by the app."""
    try:
        if args.action == 'list':
            result = branch_list(root)
        elif args.action == 'refresh':
            result = branch_refresh(root)
        else:
            result = branch_switch(root, args.name, remote=args.from_remote, local_name=args.local_name,
                                   expected_current=args.expected_current, confirmed_pending=args.confirmed_pending,
                                   expected_tracking=args.expected_tracking)
    except BranchError as error:
        result = {'ok': False, 'code': error.code, 'error': str(error), 'detail': error.detail}
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    if result.get('error'):
        detail = result.get('detail')
        print(result['error'] + (f'\ngit 说：{detail}' if detail else ''), file=sys.stderr)
    elif result.get('refresh_error'):
        print(result['refresh_error'], file=sys.stderr)
    elif not args.json and args.action == 'switch':
        print(f'已切换到 {result["to"]}。' if result['changed'] else f'已经在 {result["to"]} 上了。')
        if result.get('warning'):
            print(result['warning'], file=sys.stderr)
    elif not args.json:
        for row in result['branches']:
            mark = '* ' if row['current'] else '  '
            count = f'（{row["pending"]} 个需求待发布到 {row["upstream"]}）' if row['pending'] else ''
            blocked = f'  不能切换：{row["blocked"]}' if row['blocked'] else ''
            print(mark + row['name'] + count + blocked)
        for row in result['remotes']:
            print('  ' + row['name'])
    return 0 if result.get('ok', True) else 1


def publish_command(root, args):
    """发布。run 和 pull 只由用户在应用里点按钮触发；失败时 --json 在标准输出给 {ok: false, code, error, detail}，退出码见 PUBLISH_CODES。"""
    def fail(error):
        code = PUBLISH_CODES.get(error.code, 1)
        if args.json:
            print(json.dumps({'ok': False, 'code': error.code, 'error': str(error), 'detail': error.detail, **error.extra}, ensure_ascii=False))
        print(str(error) + (f'\ngit 说：{error.detail}' if error.detail else ''), file=sys.stderr)
        return code
    try:
        if args.action == 'status':
            st = publish_status(root, fetch=args.fetch, remote=args.remote)
            if args.json:
                print(json.dumps(st, ensure_ascii=False))
                return 0
            where = f'{st["upstream"]["ref"]}' if st['upstream'] else (f'{st["target"]["remote"]}/{st["target"]["branch"]}（还没设置上游，发布时会设置）' if st['target'] else '没有')
            print(f'分支 {st["branch"] or "（不在任何分支上）"}，上游 {where}，领先 {st["ahead"] if st["ahead"] is not None else "？"}，落后 {st["behind"] if st["behind"] is not None else "？"}。')
            un = st['unpublished']
            for r in un['requirements']:
                print(f'  需求 {r["id"]} {r["title"]}')
            for i in un['issues']:
                print(f'  问题 {i["num"]} {i["title"]}')
            print(f'  程序自己的事实提交 {un["facts"]} 个；其他提交 {un["others_total"]} 个')
            for o in un['others'][:10]:
                print(f'    {o["hash"]} {o["subject"]}')
            for b in st['blockers']:
                print('不能发布：' + b['text'])
            for w in st['warnings']:
                print('注意：' + w['text'] + ('（' + '、'.join(w['paths']) + '）' if w.get('paths') else ''))
            return 0
        if args.action == 'list':
            found = publish_list(root)
            if args.json:
                print(json.dumps(found, ensure_ascii=False))
            for r in [] if args.json else found:
                print(f'{r["id"]}  {r["created"]}  {r["remote"]}/{r["target"]}  ' + ('已发布' if r['published'] else '没推成') + f'  需求 {len(r["requirements"])} 个，问题 {len(r["issues"])} 个')
            if not found and not args.json:
                print('还没有发布过。')
            return 0
        if args.action == 'run':
            made = publish_run(root, args.remote)
            if args.json:
                print(json.dumps(made, ensure_ascii=False))
            else:
                print(f'已发布：{made["branch"]} → {made["remote"]}/{made["target"]}，{made["commits"]} 个提交，发布记录 {made["record"]}。')
            return 0
        made = publish_pull(root)
        print(json.dumps(made, ensure_ascii=False) if args.json else made['message'])
        return 0
    except PublishError as error:
        return fail(error)

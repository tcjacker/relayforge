"""rf.py design …（设计稿）和 rf.py fresh …（知识保鲜）的命令分发。分层见 tests/test_layers.py。
"""
import json
import sys
from pathlib import Path

from ..core.common import main_checkout
from .designs import current_designs
from .drafts import design_save, design_skip
from .fresh import feature_files, fresh_adopt, fresh_brief, fresh_done, fresh_drift, fresh_ignore, fresh_propose, fresh_show
from ..requirements.owners import own_requirement
from ..requirements.record import find_requirement
from ..sessions.record import here_source

__all__ = ['design_command', 'fresh_command']


def design_command(root, args):
    try:
        if args.action == 'save':
            if args.file:
                html = Path(args.file).read_text(encoding='utf-8', errors='replace')
            elif sys.stdin is not None and not sys.stdin.isatty():
                raw = getattr(sys.stdin, 'buffer', None)
                html = raw.read().decode('utf-8', errors='replace') if raw else sys.stdin.read()
            else:
                raise ValueError('没有收到 HTML。用法：rf.py design save --title "标题" --feature RF-101 <<\'HTML\' …整页 HTML… HTML')
            made = design_save(root, html, args.title, args.feature, args.ident, args.note, here_source(root, args.session, args.transcript))
            if args.json:
                print(json.dumps(made, ensure_ascii=False))
            else:
                print(('已保存新的设计稿 ' if made['created'] else '已修改设计稿 ') + f'{made["id"]}「{made["title"]}」→ {made["path"]}。'
                      f'之后按意见修改时加 --id {made["id"]} 覆盖同一份。应用里已经能看到它。')
        elif args.action == 'skip':
            reason = design_skip(root, ' '.join(args.values), here_source(root, args.session, args.transcript))
            print(f'已记下：这个需求跳过方案，理由是「{reason}」。之后要画设计稿，直接 design save 就行。')
        else:
            mine = own_requirement(root)
            req = find_requirement(root, mine[1]['id']) if mine else None
            drawn = req['designs'] if req else []
            current = current_designs(main_checkout(root))
            if args.json:
                print(json.dumps({'requirement': req['id'] if req else None, 'designs': drawn, 'current': current}, ensure_ascii=False))
                return 0
            if req:
                print(f'{req["id"]} {req["title"]} 的设计稿：')
                print('\n'.join(f'- {d["id"]} {d["title"]}（{d["status"]}，画的：{"、".join(d["features"] + ["新增：" + x for x in d["new"]])}）→ {d["path"]}' for d in drawn) or '- 还没有。')
                print()
            print('功能点现在的设计稿：')
            print('\n'.join(f'- {fid}：{v["id"]} {v["title"]} → {v["path"]}' for fid, v in sorted(current.items())) or '- 还没有。')
    except (ValueError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


# ---------- 知识保鲜（facts/fresh.py） ----------

def _payload(file):
    text = sys.stdin.read() if file in (None, '-') else Path(file).read_text(encoding='utf-8')
    try:
        return json.loads(text)
    except ValueError as error:
        raise ValueError(f'交回的内容不是 JSON：{error}。') from None


def fresh_command(root, args):
    """rf.py fresh …：功能点跟上代码。files / drift / brief / show 只读；propose 只有核对会话能用；adopt / ignore / done 只由用户（应用）触发。"""
    v = list(args.values)
    act = args.action
    try:
        if act == 'files':
            if not v:
                raise ValueError('用法：fresh files RF-101')
            mapping, _ = feature_files(root)
            files = mapping.get(v[0].upper(), {})
            out = {'id': v[0].upper(), 'files': [{'path': p, 'weight': w} for p, w in sorted(files.items(), key=lambda x: -x[1])]}
            text = '\n'.join(f'{f["path"]}（{f["weight"]}）' for f in out['files']) or f'{out["id"]} 还对应不上代码文件：还没有已完成的需求或修复改过它。'
        elif act == 'drift':
            out = fresh_drift(root, v[0] if v else None)
            text = '\n'.join(f'{r["id"]} {r["title"]}：' + ('对应不上代码' if r['unmapped'] else f'可能过时（{len(r["commits"])} 个提交：' + '、'.join(c['short'] for c in r['commits'][:5]) + '）' if r['stale'] else '没有变化')
                             for r in out) or '还没有功能点。'
        elif act == 'brief':
            text = fresh_brief(root, v[0] if v else '')
            out = {'text': text}
        elif act == 'propose':
            if not v:
                raise ValueError('用法：fresh propose RF-101 --file -（JSON：{"verdict": "changed|unchanged", "summary": "…", "items": [{"section", "op": "replace|add|remove", "old", "new", "why", "evidence"}]}）')
            out = fresh_propose(root, v[0], _payload(args.file), args.session)
            text = f'已交回 {out["feature"]} 的核对结果（{len(out["items"])} 条建议），等用户在应用里处理。不要自己改功能点文件。'
        elif act == 'show':
            out = fresh_show(root, v[0] if v else None)
            text = json.dumps(out, ensure_ascii=False, indent=1) if out else '没有等你处理的核对结果。'
        elif act in ('adopt', 'ignore'):
            if not v:
                raise ValueError(f'用法：fresh {act} RF-101 [建议序号 …]')
            out = (fresh_adopt if act == 'adopt' else fresh_ignore)(root, v[0], v[1:])
            text = f'{out["feature"]}：' + ('采纳了。' if act == 'adopt' else '忽略了。')
        elif act == 'done':
            out = fresh_done(root, v[0] if v else '')
            text = f'{out["id"]} 核对完，核对到 {out["checked_at"][:8]}。'
        else:
            raise ValueError(f'不认识的动作 {act}。')
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1
    print(json.dumps(out, ensure_ascii=False) if args.json else text)
    return 0

"""拦下时告诉会话该怎么做：Claude Code 的权限规则拦下一次调用时只说「被禁止」，不说该改用什么，会话常常换个写法再试。

应用给 Claude Code 的会话挂一个「执行前」钩子（PreToolUse，见 app/src/main/claude-settings.js），由 rf.py hook guard 运行这里的 main：
认出一定会被拦的调用，先替权限规则拦下，并说明该怎么做：
  - 只由用户触发的 rf.py 命令（core/trust.py 的 USER_COMMANDS、APP_ONLY）：每种会话都拦，说明该交给用户的是什么、会话自己能做什么（HINTS）；
  - 改记录目录（.relayforge 和数据外置的本机目录）里的文件：立项、排查、测试会话里拦（应用用 --facts 传进来），按改的是哪一类记录说明该用哪条命令；
  - 改项目里的文件：立项、排查会话只读（--project、--drafts）；测试会话改产品代码（--product）。
同一个会话、同一个目标被拦第二次起，说明里加一句「不要再换方式重试，停下来告诉用户」。
认不出的照常交给权限规则（不输出任何东西）。拦不拦以权限规则和 rf.py 自己的校验为准，这里只是把说明说清楚，所以只拦它们本来就会拦的。

Codex 的会话用同一套规则（codex_pre，由 rf.py hook codex 在 PreToolUse 时调用）：Codex 改文件的工具是 apply_patch，
输入是整个补丁（tool_input.command），从补丁里认出改了哪些文件再逐个核对；这个会话不能改哪些地方由应用放进环境变量 RELAYFORGE_GUARD（JSON）。
rf.py 的用户关口（trust.user_gate）拒绝时也带上同一份 HINTS。
这个文件不导入包里的任何东西：rf.py 遇到 hook guard / hook codex 的 PreToolUse 时直接按路径加载它，不加载整个程序，每次执行命令前只多几十毫秒。
"""
import json
import os
import re
import sys
import time
from pathlib import Path

__all__ = ['HINTS', 'hint_for', 'guard_decision', 'patch_files', 'codex_pre']

RF = 'python3 ' + str(Path(__file__).resolve().parent.parent.parent / 'rf.py')  # 这个程序自己的入口，说明里给的命令照抄就能用

_ASK_USER = '这一步由用户在应用里做：停下来告诉用户需要他做什么，不要自己找别的办法做。'
_APP_BOOK = '这是应用自己记账用的，会话里不用管它。'
_ISSUE = '发现问题用 issue new 记一条，由用户决定修复还是交给 AI 排查。'
_TEST_DATA = '改测试数据用 test add / edit / rm（改已经落定的数据会成为待用户同意的改动），同意不同意由用户在应用里点。'
_ENV = '测试环境由用户在应用里管理；你有建议就用 env propose 交回，由用户采纳。'

# (命令, 动作) -> 会话该怎么做。和 core/trust.py 的 USER_COMMANDS、APP_ONLY 一一对应（tests/test_guard.py 会核对）
HINTS = {
    ('req', 'start'): '立项由用户在应用里点「确认立项」。你把需求说明、要改的功能点、验收条件用 note say / feature / check 记全，然后告诉用户可以立项了。',
    ('req', 'done'): '完成由用户试过以后在应用里点「确认完成」。你做完了就停下来，告诉用户做了什么、怎么试。',
    ('req', 'drop'): _ASK_USER, ('req', 'set'): _ASK_USER, ('req', 'depend'): _ASK_USER, ('req', 'keep'): _ASK_USER, ('req', 'restore'): _ASK_USER, ('req', 'choose'): _ASK_USER,
    ('req', 'to-test'): '开发做完（计划里的步骤都用 note step 标成 done）会自动交给测试会话，不用自己交。',
    ('req', 'skip-test'): _ASK_USER,
    ('req', 'accept'): '人工验收的逐条结果由用户在应用里点。你可以用 req steps 把检查步骤写具体。',
    ('req', 'case-ok'): '改用例用 req case-edit / case-rm 加 --why 提给用户，由用户在应用里同意。',
    ('req', 'case-no'): '改用例用 req case-edit / case-rm 加 --why 提给用户，由用户在应用里同意。',
    ('req', 'case-save'): '改用例用 req case-edit / case-rm 加 --why 提给用户，由用户在应用里同意。',
    ('issue', 'confirm'): '修完用 issue handback 问题编号 --changed … --verify … 交回，修没修好由用户试过以后确认。',
    ('issue', 'unconfirm'): '修完用 issue handback 交回，确认和撤回由用户在应用里做。',
    ('issue', 'reject'): '修完用 issue handback 交回，退回由用户在应用里做。',
    ('issue', 'probe'): _ISSUE, ('issue', 'fix'): _ISSUE,
    ('issue', 'set'): '问题的设置由用户在应用里改。你有看法就写在交回或排查结论里。',
    ('issue', 'reply'): '回复问题由用户在应用里做。你有看法就写在交回或排查结论里。',
    ('issue', 'to-req'): _ASK_USER, ('issue', 'from-req'): _ASK_USER,
    ('issue', 'review'): _ASK_USER, ('issue', 'review-discard'): _ASK_USER, ('issue', 'review-apply'): _ASK_USER,
    ('test', 'approve'): _TEST_DATA, ('test', 'reject'): _TEST_DATA, ('test', 'save'): _TEST_DATA, ('test', 'seen'): _TEST_DATA,
    ('test', 'stale'): _ASK_USER, ('test', 'revive'): _ASK_USER, ('test', 'move'): _ASK_USER, ('test', 'config'): _ASK_USER,
    ('test', 'approve-build'): '构建命令要用户在应用里看过实际内容再同意：停下来告诉用户。',
    ('env', 'new'): _ENV, ('env', 'copy'): _ENV, ('env', 'rename'): _ENV, ('env', 'rm'): _ENV, ('env', 'save'): _ENV,
    ('env', 'use'): _ENV, ('env', 'user'): _ENV, ('env', 'adopt'): _ENV, ('env', 'ignore'): _ENV,
    ('env', 'approve'): '准备步骤和启动命令要用户在应用里看过实际内容再同意：停下来告诉用户。',
    ('env', 'ai-secrets'): '会话能不能带上保密值由用户在应用里决定，你不要设法取保密值。',
    ('env', 'reveal'): '保密值的明文只给用户看，你不要设法取。用 env show 看打码后的设置。',
    ('env', 'launch'): _APP_BOOK, ('env', 'accept-clean'): _APP_BOOK,
    ('req', 'test-kicked'): _APP_BOOK, ('req', 'test-told'): _APP_BOOK,
    ('publish', 'run'): '推送由用户在应用里点「发布」（推送可能触发部署）。你提交到当前分支就行，用 publish status 看还有什么没发布。',
    ('publish', 'pull'): '拉取远端由用户在应用里做。',
    ('branch', 'refresh'): _ASK_USER, ('branch', 'switch'): _ASK_USER, ('map', 'trunk'): _ASK_USER, ('fresh', 'adopt'): _ASK_USER, ('fresh', 'ignore'): _ASK_USER, ('fresh', 'done'): _ASK_USER,
    ('data', 'import'): _ASK_USER, ('data', 'migrate'): _ASK_USER,
    ('summarize', None): _APP_BOOK, ('archive', None): _APP_BOOK,
    ('subagent', 'decision'): '应用读取立项对话中的用户授权。开发会话用 subagent status 查看是否可委派。',
    ('split', None): '拆分建议照开场白里的说明写出来，由应用替用户检查、由用户确认。',
}


def hint_for(key):
    """What the session should do instead of a user-only command (key as in trust.USER_COMMANDS), or ''."""
    return HINTS.get(tuple(key)) or '' if key else ''


# 真正执行的 rf.py：在命令开头、; && | 换行 $( 之后，前面可以有 python3 [-B] 和路径；写在引号里、grep 里的不算（同 sessions/monitor.py）
RF_CMD = re.compile(r'(?:^|[;&|\n]|\$\()[ \t]*(?:[A-Za-z_]+=\S*[ \t]+)*(?:python3?(?:[ \t]+-[A-Za-z]+)*[ \t]+)?["\']?[^\s;&|"\']*rf\.py["\']?'
                    r'[ \t]+([a-z][\w-]*)(?:[ \t]+([a-z][\w-]*))?')

# 改了哪一类记录 -> 该用的命令
FACT_HINTS = [
    ('requirements', '需求文件只能用命令改：需求说明 {rf} note say "…"，要改的功能点 note feature "…"，验收条件 note check "…"（是你推断、用户没明说的加 --guess）；'
                     '测试用例用 req case-add / case-edit / case-rm。'),
    ('features', '功能点的说法用 {rf} note fact RF-xxx "新的说法" --replaces "旧说法里的一段话"，新功能点用 note new-feature "名称" --module M-xx。'),
    ('modules', '功能点的说法用 {rf} note fact RF-xxx "新的说法" --replaces "旧说法里的一段话"，新功能点用 note new-feature "名称" --module M-xx。'),
    ('journeys', '大图的内容用 {rf} note fact 改，或者告诉用户在大图编辑里改。'),
    ('decisions', '决定用 {rf} note decision "决定了什么" --reason "为什么" 记。'),
    ('sessions', '进展用 {rf} note doing / step / open 记。'),
    ('issues', '问题用 {rf} issue new 记，修复的结果用 issue handback 交回，排查结论用 issue finding 记。'),
    ('tests', '测试集用 {rf} test new / add / edit / rm 改。'),
    ('designs', '设计稿先写在草稿目录里，再用 {rf} design save --file 草稿文件 收进需求。'),
]
FACT_DEFAULT = '记录只能用 rf.py 的命令写（{rf} note …，写法见 .relayforge/FORMAT.md），不能直接改文件。'

EDIT_TOOLS = ('Edit', 'MultiEdit', 'Write', 'NotebookEdit')


def _under(path, folder):
    try:
        p, f = os.path.realpath(path), os.path.realpath(folder)
    except (OSError, ValueError):
        return False
    return p == f or p.startswith(f.rstrip('/') + '/')


def _edit_reason(path, facts, project, drafts, product, kind):
    if drafts and _under(path, drafts):
        return None
    for folder in facts:
        if _under(path, folder):
            rel = os.path.relpath(os.path.realpath(path), os.path.realpath(folder)).split(os.sep)
            text = next((t for name, t in FACT_HINTS if rel and rel[0] == name), FACT_DEFAULT)
            return 'record:' + (rel[0] if len(rel) > 1 else ''), text.format(rf=RF)
    if project and _under(path, project):
        if kind == 'probe':
            return 'project', f'排查会话只读，不能改项目里的文件。结论用 {RF} issue finding 记下，怎么修由用户决定。'
        tail = f'设计稿这类草稿写在 {drafts}，再用 {RF} design save --file 收进需求。' if drafts else ''
        return 'project', '立项中的需求只能读和聊，不能改项目里的文件；要改代码等用户确认立项以后在开发会话里改。' + tail
    for folder in product:
        if _under(path, folder):
            return 'product', f'测试会话不改产品代码。发现产品的问题用 {RF} req verdict problems --problem "…" 交回给开发会话去修。'
    return None


def guard_decision(data, facts=(), project=None, drafts=None, product=(), kind=None):
    """(target, reason) when this call will be refused anyway and the session should be told what to do instead; else None."""
    tool = str(data.get('tool_name') or '')
    given = data.get('tool_input') if isinstance(data.get('tool_input'), dict) else {}
    if tool in EDIT_TOOLS:
        path = given.get('file_path') or given.get('notebook_path')
        if not path:
            return None
        found = _edit_reason(str(path), facts, project, drafts, product, kind)
        return (f'{tool}:{os.path.realpath(str(path))}', found[1]) if found else None
    if tool == 'Bash':
        for m in RF_CMD.finditer(str(given.get('command') or '')):
            for key in ((m.group(1), m.group(2)), (m.group(1), None)):
                if key in HINTS:
                    what = ' '.join(x for x in key if x)
                    return f'rf:{what}', f'rf.py {what} 只能由用户在应用里触发，会话里一定会被拒绝。' + HINTS[key]
    return None


# Codex 的 apply_patch：*** Update File: / *** Add File: / *** Delete File: 后面是文件，改名时 *** Move to: 后面是新位置
PATCH_FILE = re.compile(r'^\*\*\* (?:Update File|Add File|Delete File|Move to): (.+?)\s*$', re.M)


def patch_files(patch, cwd):
    """The files an apply_patch input touches, as absolute paths (relative ones are under the session's directory)."""
    return [p if os.path.isabs(p) else os.path.join(cwd or '.', p) for p in PATCH_FILE.findall(str(patch or ''))]


def codex_pre(data, spec):
    """The same as guard_decision for a Codex PreToolUse payload: Bash as it is, apply_patch file by file as an Edit."""
    given = data.get('tool_input') if isinstance(data.get('tool_input'), dict) else {}
    if data.get('tool_name') == 'apply_patch':
        for path in patch_files(given.get('command') or given.get('patch') or given.get('input'), data.get('cwd')):
            found = guard_decision({'tool_name': 'Edit', 'tool_input': {'file_path': path}}, **spec)
            if found:
                return found
        return None
    return guard_decision(data, **spec)


REPEAT_NOTE = '这已经是第 {n} 次被拦下了：不要再换一种写法或工具重试，停下来把你想做什么、为什么告诉用户。'


def _count(session, target):
    """How many times this session has been stopped at this target, this one included. Kept next to the app's other local state."""
    if not session or not re.fullmatch(r'[\w.-]{1,100}', session):
        return 1
    folder = Path(os.environ.get('RELAYFORGE_HOME') or Path.home() / '.relayforge') / 'guard'
    file = folder / f'{session}.json'
    try:
        seen = json.loads(file.read_text(encoding='utf-8'))
        seen = seen if isinstance(seen, dict) else {}
    except (OSError, ValueError):
        seen = {}
    seen[target] = int(seen.get(target) or 0) + 1
    try:
        folder.mkdir(parents=True, exist_ok=True)
        file.write_text(json.dumps(seen, ensure_ascii=False), encoding='utf-8')
        for old in folder.glob('*.json'):  # 一周没动过的会话不再留
            if time.time() - old.stat().st_mtime > 7 * 86400:
                old.unlink()
    except OSError:
        pass
    return seen[target]


def _args(argv):
    out = {'facts': [], 'product': [], 'project': None, 'drafts': None, 'kind': None}
    it = iter(argv)
    for a in it:
        name = a[2:] if a.startswith('--') else None
        if name in out:
            value = next(it, None)
            if isinstance(out[name], list):
                out[name].append(value)
            else:
                out[name] = value
    return out


def _answer(data, found, session):
    """Print the deny for a found (target, reason), saying "stop and ask" from the second time on this target."""
    target, reason = found
    n = _count(session, target)
    if n >= 2:
        reason += REPEAT_NOTE.format(n=n)
    print(json.dumps({'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': 'deny', 'permissionDecisionReason': reason}},
                     ensure_ascii=False))


def codex_main(data):
    """rf.py hook codex, PreToolUse: the session's limits come from RELAYFORGE_GUARD. Never fails."""
    try:
        spec = json.loads(os.environ.get('RELAYFORGE_GUARD') or '{}')
        spec = {k: spec.get(k) for k in ('facts', 'project', 'drafts', 'product', 'kind') if spec.get(k) is not None} if isinstance(spec, dict) else {}
        found = codex_pre(data, spec)
        if found:
            _answer(data, found, str(os.environ.get('RELAYFORGE_SESSION_ID') or data.get('session_id') or ''))
    except Exception:
        pass
    return 0


def main(argv=None):
    """rf.py hook guard [--facts 目录]… [--project 目录] [--drafts 目录] [--product 目录]… [--kind 种类]：读钩子的输入，要拦就打印拦下的说明。从不失败。"""
    try:
        data = json.loads(sys.stdin.read() or '{}')
        if not isinstance(data, dict) or data.get('hook_event_name', 'PreToolUse') != 'PreToolUse':
            return 0
        found = guard_decision(data, **_args(sys.argv[1:] if argv is None else argv))
        if found:
            _answer(data, found, str(data.get('session_id') or ''))
    except Exception:
        pass
    return 0


if __name__ == '__main__':
    sys.exit(main())

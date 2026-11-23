"""大家都用的常量和小工具：项目根目录、日志、Git、本机的 RelayForge 目录、入口脚本的位置。"""

import contextlib
import datetime as dt
import functools
import hashlib
import json
import os
import re
import secrets
import subprocess
import time
from pathlib import Path
try:
    import fcntl
except ImportError:  # not on this platform; writes are then not serialised between processes
    fcntl = None


# 后端放在哪：PACKAGE 是 relayforge 这个包（本文件在它的 core/ 下面），SCRIPT 是入口 rf.py（仓库里的 backend/rf.py，或项目里的 .relayforge/bin/rf.py），
# TEMPLATES 是页面、FORMAT.md 这些大段文本。
PACKAGE = Path(__file__).resolve().parent.parent
SCRIPT = PACKAGE.parent / 'rf.py'
TEMPLATES = PACKAGE / 'templates'

DIR = '.relayforge'
TREES = '.worktrees'  # one working directory per requirement; outside .relayforge so that it reads as ordinary code
PENDING = '〔待确认〕'
REJECTED = '〔已否决〕'
REPLACED = '〔已替代〕'  # a statement the user replaced with a newer one: kept, no longer counts
PLACEHOLDER = '- 尚未讨论。'
FEATURE_HEADS = ['一句话定位', '在模块中的角色', '在产品中的位置', '用户旅程', '规则与边界', '还没定的事', '调整时要注意', '变更记录']
MODULE_HEADS = ['负责什么', '和其他模块的关系', '变更记录']
JOURNEY_HEADS = ['一句话说明', '步骤', '变更记录']
DECISION_HEADS = ['决定', '理由', '没有选的方案', '变更记录']
REQ_HEADS = ['需求说明', '要改的功能点', '验收条件', '变更记录']
KINDS = ('decision', 'structure', 'supersede', 'open-question')
CONTEXT_LIMIT = 9500
CHUNK_CHARS = 120000
MESSAGE_CHARS = 4000
GUARD = 'RELAYFORGE_CAPTURE'
PORT = 8770
MARKS = {'done': 'x', 'doing': '~', 'todo': ' ', 'dropped': '-'}


def today():
    return dt.date.today().isoformat()


def is_project(d):
    """A directory holding a .relayforge — but not the one this machine keeps its own data in (~/.relayforge)."""
    d = Path(d) / DIR
    if not d.is_dir():
        return False
    try:
        return d.resolve() != relayforge_home().resolve()
    except OSError:
        return True


def find_root(start=None):
    env = os.environ.get('CLAUDE_PROJECT_DIR')
    for base in [env, start or os.getcwd()]:  # a directory named by the caller is not second-guessed
        if not base:
            continue
        p = Path(base).resolve()
        for d in [p, *p.parents]:
            if (d / ID_FILE).is_file():
                link_facts(d, repair_only=True)  # 重新克隆、挪了位置、新开的工作目录：按项目 ID 接回外置的数据；不改指一个还能用的链接
            if is_project(d):
                return d
    return None


# 数据外置（见 FORMAT.md 的「数据放在哪」）：项目的记录放在本机 ~/.relayforge/projects/<项目 ID>/，不进 Git。
# 仓库里只提交一个 .relayforge.json（只有项目 ID）；项目目录和每个工作目录里的 .relayforge 是指向外置目录的链接，
# 不进 Git（写进 .gitignore 和 .git/info/exclude），所以切换分支、开工作目录都看到同一份数据。
ID_FILE = '.relayforge.json'


def project_id(root):
    try:
        data = json.loads((Path(root) / ID_FILE).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    ident = data.get('id') if isinstance(data, dict) else None
    return ident if isinstance(ident, str) and re.fullmatch(r'[\w.-]{4,80}', ident) else None


def new_project_id(root):
    return f'{slug(Path(root).resolve().name)[:40]}-{secrets.token_hex(4)}'


def facts_store(ident):
    """The directory on this machine that holds a project's records."""
    return relayforge_home() / 'projects' / ident


def is_external(root):
    """The project keeps its records outside the repository (its .relayforge is a link to them)."""
    return (Path(root) / DIR).is_symlink()


def is_legacy(root):
    """An older project whose records are still a directory inside the repository; this version does not open it."""
    d = Path(root) / DIR
    return d.is_dir() and not d.is_symlink() and not (Path(root) / ID_FILE).is_file() and is_project(root)


def link_facts(root, repair_only=False):
    """Make root/.relayforge a link to the project's external directory, creating that directory when it is not there.
    Leaves alone a .relayforge that is a real directory (an older project, or one in the middle of being migrated).
    repair_only（find_root 用）：只补上缺了的链接、修好断了的链接，不把一个正常指向别处的链接改指过来——
    否则只要外置根目录（RELAYFORGE_HOME）不同（比如测试用临时目录、别的进程），在仓库里找一次项目根目录就会把真实的记录链接改掉。"""
    root = Path(root)
    ident = project_id(root)
    if not ident:
        return None
    store = facts_store(ident)
    link = root / DIR
    if link.is_symlink():
        try:
            if link.resolve() == store.resolve():
                store.mkdir(parents=True, exist_ok=True)
                return store
        except OSError:
            pass
        if repair_only and link.exists():  # 指着一个还在的目录：不是坏链接，留给明确点名的命令（init、projects --add、--root）去改
            return None
        link.unlink()  # 指向别处（ID 改过、外置根目录换了）或者断了：按现在的 ID 重新指
    elif link.exists():
        return None
    store.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(store, target_is_directory=True)
    except FileExistsError:
        pass
    exclude_link(root)
    return store


def link_worktree(home, place):
    """A requirement's or fix's working directory sees the main checkout's records: its .relayforge links to the same place."""
    if not is_external(home):
        return
    link = Path(place) / DIR
    if link.is_symlink() or link.exists():
        return
    try:
        link.symlink_to((Path(home) / DIR).resolve(), target_is_directory=True)
    except OSError:
        pass


def exclude_link(root):
    """Keep the link out of Git for every checkout of the repository, whatever .gitignore the branch has."""
    common = git(root, 'rev-parse', '--git-common-dir').strip()
    if not common:
        return
    path = (Path(root) / common).resolve() / 'info' / 'exclude'
    try:
        have = path.read_text(encoding='utf-8') if path.exists() else ''
        if f'/{DIR}' not in have.splitlines():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(have.rstrip('\n') + ('\n' if have.strip() else '') + f'/{DIR}\n', encoding='utf-8')
    except OSError:
        pass


def log(root, text):
    try:
        with open(root / DIR / 'log.txt', 'a', encoding='utf-8') as f:
            f.write(f'{dt.datetime.now().isoformat(timespec="seconds")} {text}\n')
    except OSError:
        pass


def slug(title):
    return re.sub(r'[\\/:*?"<>|\s]+', '-', title.strip()).strip('-') or 'untitled'


def git(root, *args):
    try:
        done = subprocess.run(['git', '-C', str(root), '-c', 'core.quotepath=off', *args], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return ''
    return done.stdout if done.returncode == 0 else ''


def worktrees(root):
    """Every checkout of this repository that has a .relayforge directory; `root` comes first."""
    found = [Path(line[9:]) for line in git(root, 'worktree', 'list', '--porcelain').splitlines() if line.startswith('worktree ')]
    out = [Path(root).resolve()]
    for path in found:
        if is_project(path) and path.resolve() not in out:
            out.append(path.resolve())
    return out


def main_checkout(root):
    """The first checkout of the repository; worktrees for requirements hang under it. All facts live here (in .relayforge/
    on whatever branch it has checked out); requirement and fix worktrees only change code."""
    return _main_checkout(str(Path(root).resolve()))


@functools.lru_cache(maxsize=64)
def _main_checkout(root):
    first = next((line[9:] for line in git(root, 'worktree', 'list', '--porcelain').splitlines() if line.startswith('worktree ')), None)
    return Path(first).resolve() if first else Path(root).resolve()


# 进行中的需求带来的事实（见 FORMAT.md 的「进行中的事实」）。整个对象是需求新加的：文件头 in_progress 写需求编号；
# 改现有对象：新的陈述行首加〔进行中 R-…〕，要被它换掉的旧陈述行首加〔R-… 完成后替代〕，文件头 changing 列出在改它的需求。
# 确认完成时去掉标记、旧行改成〔已替代〕；放弃时新行改成〔已放弃 R-…〕，恢复时再改回来。
IN_PROGRESS = re.compile(r'〔进行中 (R-[^〕\s]+)〕')
DROPPED_LINE = re.compile(r'〔已放弃 (R-[^〕\s]+)〕')
TO_REPLACE = re.compile(r'〔(R-[^〕\s]+) 完成后替代〕')
HELD_REPLACE = re.compile(r'〔(R-[^〕\s]+) 放弃前要替代〕')


@contextlib.contextmanager
def facts_lock(home):
    """One writer at a time for the program's commits of .relayforge/ (and for issue files). The lock file sits in the
    shared .git so every worktree sees the same one."""
    common = git(home, 'rev-parse', '--git-common-dir').strip()
    place = (Path(home) / common).resolve() if common else Path(home) / DIR
    place.mkdir(parents=True, exist_ok=True)
    handle = open(place / 'relayforge-facts.lock', 'a')
    try:
        if fcntl:
            fcntl.flock(handle, fcntl.LOCK_EX)
        yield
    finally:
        handle.close()


def git_busy(home):
    """A merge, rebase, cherry-pick or revert the user has in progress in the main checkout: the program does not commit then."""
    for name in ('MERGE_HEAD', 'CHERRY_PICK_HEAD', 'REVERT_HEAD', 'rebase-merge', 'rebase-apply'):
        found = git(home, 'rev-parse', '--git-path', name).strip()
        if found and (Path(home) / found).exists():
            return True
    return False


def commit_facts(home, message, locked=False):
    """Commit what changed under .relayforge/ in the main checkout, on its current branch, and nothing else: the user's other
    changes, staged or not, stay as they are. The program does this at a few moments (立项, 确认完成, 放弃和恢复, the user's
    decisions about issues, a session stopping), not on every write. Returns False when nothing was committed; the files
    stay written and a later moment commits them along."""
    home = Path(home)
    if is_external(home):  # 数据外置的项目：记录不进 Git，写下去就算数
        return False
    if not git(home, 'rev-parse', '--git-dir').strip() or git_busy(home):
        return False
    with (contextlib.nullcontext() if locked else facts_lock(home)):
        run_git(home, 'add', '-A', '--', DIR)
        if not git(home, 'diff', '--cached', '--name-only', '--', DIR).strip():
            return False
        for _ in range(5):
            done = run_git(home, 'commit', '-q', '--no-verify', '-m', message, '--', DIR)
            if done.returncode == 0:
                return True
            if 'index.lock' not in (done.stderr or ''):
                log(home, f'facts commit failed: {(done.stderr or done.stdout).strip()[:200]}')
                return False
            time.sleep(0.3)  # another git command in the same repository holds the index for a moment
    return False


def branch_of(place):
    return git(place, 'rev-parse', '--abbrev-ref', 'HEAD').strip()


def run_git(place, *args):
    return subprocess.run(['git', '-C', str(place), '-c', 'core.quotepath=off', *args], capture_output=True, text=True)


def drafts_dir(ident=None):
    """Where a session writes a design draft before `design save --file` takes it into the requirement: outside every
    project, so that a session which may not touch the project can still be allowed to write here."""
    base = registry_path().parent / 'drafts'
    return base / ident if ident else base


def relayforge_home():
    return Path(os.environ.get('RELAYFORGE_HOME') or Path.home() / '.relayforge')


def secure_home():
    """$RELAYFORGE_HOME only for this user (700): it holds every project's records, session summaries and archives. It used to be
    created 755, so on a shared machine other local users could read them; tightened on every start. Subfolders need not be:
    nobody else can get past this one."""
    home = relayforge_home()
    try:
        if home.is_dir() and home.stat().st_uid == os.getuid() and home.stat().st_mode & 0o077:
            home.chmod(0o700)
    except (OSError, AttributeError):  # 没有 getuid 的系统、改不了权限：不挡命令
        pass


def registry_path():
    return relayforge_home() / 'projects.json'


def project_key(home):
    """A stable name for one project on this machine: its folder name and a hash of its path. Local data kept outside the
    repository (session archive, acceptance copies, test environments' local values) is filed under it."""
    home = Path(home).resolve()
    return f'{slug(home.name)[:40]}-{hashlib.sha1(str(home).encode("utf-8")).hexdigest()[:8]}'

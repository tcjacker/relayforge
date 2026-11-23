"""提交上的出处：需求和修复的工作目录里的每一个提交，由 git 自己带上它是哪个需求（或修复）、哪个会话做的。

  RelayForge-Requirement: R-20261118-ab12      需求的提交
  RelayForge-Fix: F-003                        修复的提交
  RelayForge-Issue: I-012                      这次修复带着的问题，一条一行
  RelayForge-Session: S-20261118-7f3a          做这个提交的会话（编号和 .relayforge/sessions/ 里的文件名一致）

怎么做到的（不靠 AI 记得）：每个需求或修复的工作目录（git worktree）有自己的 core.hooksPath，指向它私有目录
（.git/worktrees/名字/relayforge-hooks/）里的一套钩子。设置写在这个工作目录自己的配置里
（git config --worktree，要求共享配置里 extensions.worktreeConfig 为 true，这是 git 自带的机制），
所以项目主目录和别的工作目录的提交完全不受影响，用户在项目目录里的提交不会带这些行。

- 钩子目录里每个钩子名都有一份同样的小 shell 脚本。它先找用户自己的钩子（用户在共享配置、全局或系统配置里设的 core.hooksPath，
  没设就是仓库的 hooks 目录），原样运行：别的钩子 exec 过去，参数、标准输入和退出码都不变，所以用户的钩子照常运行、照常能拦住提交。
- prepare-commit-msg 是例外：先运行用户的（它改了提交说明，我们接着在改后的基础上加），再加上面的行。
  这个钩子 git commit -m、--amend、--no-verify、合并、拣选都会运行（--no-verify 只跳过 pre-commit 和 commit-msg），
  不管是 AI 的哪个工具、还是人在这个目录里提交。
- 已有同样的行就不再加（git interpret-trailers --if-exists addIfDifferent），所以 --amend 不会重复。
- 我们这一步出任何错都吞掉、退出码是 0：提交不会因为我们失败，最坏是没有这些行。用户的钩子拦住提交是它自己的事，照旧拦。
- 会话编号：应用启动会话时把会话标识放进环境变量 RELAYFORGE_SESSION_ID。实测（真实的 Claude Code，在需求工作目录里让它用 Bash 工具提交）：
  这个变量能传到 Bash 工具运行的命令里，git commit 触发的钩子也读得到；Claude Code 自己还会放 CLAUDE_CODE_SESSION_ID，作为第二选择。
  两个都没有时（比如人在终端里提交）用挂在这个需求或修复下最新的会话。会话标识换成编号：先看钩子目录里 trailers 文件记的（挂会话时写下的），
  没有就和程序给会话起编号的办法一样：今天的日期加标识的前四位。
- 钩子需要知道的（哪个需求、哪几条问题、挂了哪些会话）放在同一个目录的 trailers 文件里，由这里写：开出工作目录时、挂会话时、
  会话打开时（工作目录是以前开的也补上）。每次都整份重写，所以是幂等的。
- 不装的情况：git 太旧（不到 2.20）、不是单独的工作目录（项目主目录永远不装）、仓库是 bare 或设了 core.worktree、
  这个工作目录已经有自己的 core.hooksPath 而不是我们的。返回 {'ok': False, 'why': …}，什么都不改。

合并提交（req done、确认修好了）在项目主目录里由程序写，说明里直接带上需求或修复那几行，不经过钩子。
"""

import os
import re
import stat
import subprocess
from pathlib import Path

from ..core.common import branch_of, git, link_worktree, main_checkout, run_git
from ..requirements.owners import fix_of_branch, own_requirement
from .record import stable_tag

__all__ = ['install_trace_hooks', 'ensure_here', 'trailer_lines']

HOOK_DIR = 'relayforge-hooks'
DATA = 'trailers'
KEYS = {'requirement': 'RelayForge-Requirement', 'fix': 'RelayForge-Fix', 'issue': 'RelayForge-Issue', 'session': 'RelayForge-Session'}
# githooks(5) 里的钩子名。用户的钩子在 git 以后新增的名字下，只要在装的时候已经存在，也会补上
HOOK_NAMES = ['applypatch-msg', 'pre-applypatch', 'post-applypatch', 'pre-commit', 'pre-merge-commit', 'prepare-commit-msg', 'commit-msg', 'post-commit',
              'pre-rebase', 'post-checkout', 'post-merge', 'pre-push', 'pre-receive', 'update', 'proc-receive', 'post-receive', 'post-update',
              'reference-transaction', 'push-to-checkout', 'pre-auto-gc', 'post-rewrite', 'sendemail-validate', 'fsmonitor-watchman',
              'p4-changelist', 'p4-prepare-changelist', 'p4-post-changelist', 'p4-pre-submit', 'post-index-change']

SCRIPT = r'''#!/bin/sh
# RelayForge 写的钩子（需求或修复的工作目录用）：先原样运行你自己的同名钩子，prepare-commit-msg 再给提交说明加上
# RelayForge-Requirement / RelayForge-Fix / RelayForge-Issue / RelayForge-Session 几行。这一步出错不会让提交失败。
# 由 rf.py 重写，不要手工改。说明见 backend/relayforge/sessions/trailers.py。
# 不用外部程序认自己的位置和名字：PATH 坏了也不能让钩子出错
case "$0" in */*) here=${0%/*} ;; *) here=. ;; esac
name=${0##*/}

# 你自己的钩子目录：共享配置、全局、系统里的 core.hooksPath（这个工作目录自己的那个是我们的，不算）；都没设就是仓库的 hooks 目录
user_dir() {
  p=$(git config --local --get core.hooksPath 2>/dev/null) || p=$(git config --global --get core.hooksPath 2>/dev/null) || p=$(git config --system --get core.hooksPath 2>/dev/null) || p=
  if [ -n "$p" ]; then
    case "$p" in "~/"*) p="$HOME/${p#"~/"}" ;; esac
    case "$p" in /*) ;; *) p="$(git rev-parse --show-toplevel 2>/dev/null)/$p" ;; esac
    printf '%s\n' "$p"
  else
    c=$(git rev-parse --git-common-dir 2>/dev/null) && c=$(cd "$c" 2>/dev/null && pwd) && printf '%s\n' "$c/hooks"
  fi
}

mine=$(user_dir)/$name
[ "$mine" = "$here/$name" ] && mine=
[ -f "$mine" ] || mine=

if [ "$name" != "prepare-commit-msg" ]; then
  [ -n "$mine" ] && [ -x "$mine" ] && exec "$mine" "$@"
  exit 0
fi

if [ -n "$mine" ] && [ -x "$mine" ]; then
  "$mine" "$@" || exit $?
fi

add_lines() {
  msg=$1
  data="$here/trailers"
  [ -f "$msg" ] && [ -f "$data" ] || return 0
  set --
  while IFS= read -r line; do
    case "$line" in
      RelayForge-Requirement:\ *|RelayForge-Fix:\ *|RelayForge-Issue:\ *) set -- "$@" --trailer "$line" ;;
    esac
  done < "$data"
  [ $# -gt 0 ] || return 0
  sid=${RELAYFORGE_SESSION_ID:-${CLAUDE_CODE_SESSION_ID:-}}
  if [ -n "$sid" ]; then
    tag=$(awk -v s="$sid" '$1=="session" && $2==s {print $3; exit}' "$data")
    if [ -z "$tag" ]; then  # 这个会话还没挂到需求上（比如刚开）：和程序给会话起编号的办法一样，今天的日期加会话标识的前四位
      day=$(date +%Y%m%d); short=$(printf %s "$sid" | tr -cd '0-9a-zA-Z' | cut -c1-4)
      [ -n "$day" ] && [ -n "$short" ] && tag="S-$day-$short"
    fi
  else
    tag=$(awk '$1=="session" {t=$3} END {print t}' "$data")
  fi
  [ -n "$tag" ] && set -- "$@" --trailer "RelayForge-Session: $tag"
  git interpret-trailers --in-place --if-exists addIfDifferent --if-missing add "$@" "$msg"
}
( add_lines "$1" ) >/dev/null 2>&1 || true
exit 0
'''


def _git_version():
    out = subprocess.run(['git', '--version'], capture_output=True, text=True).stdout
    found = re.search(r'(\d+)\.(\d+)', out)
    return (int(found.group(1)), int(found.group(2))) if found else (0, 0)


def block(kind, ident, issues=(), session=None):
    """The trailer lines for a commit the program writes itself (the merge commit of a requirement or fix), as one paragraph."""
    lines = [f'{KEYS[kind]}: {ident}'] + [f'{KEYS["issue"]}: {i}' for i in issues]
    if session:
        lines.append(f'{KEYS["session"]}: {session}')
    return '\n'.join(lines)


def _is_linked_worktree(place):
    gitdir = git(place, 'rev-parse', '--absolute-git-dir').strip()
    common = git(place, 'rev-parse', '--git-common-dir').strip()
    if not gitdir or not common:
        return None
    common = str((Path(place) / common).resolve()) if not os.path.isabs(common) else str(Path(common).resolve())
    return Path(gitdir) if Path(gitdir).resolve() != Path(common) else None


def install(place, kind, ident, issues=(), sessions=()):
    """Put the commit hooks into one requirement or fix working directory. `sessions`: [(session uuid, tag)] in the order
    they were attached. Idempotent; never raises. Returns {'ok': bool, 'why': ...}."""
    try:
        return _install(Path(place), kind, ident, list(issues), list(sessions))
    except Exception as error:  # nothing here may get in the way of opening a working directory
        return {'ok': False, 'why': f'没装成：{error}'}


def _install(place, kind, ident, issues, sessions):
    if _git_version() < (2, 20):
        return {'ok': False, 'why': 'git 太旧（要 2.20 以上），没装提交钩子。'}
    private = _is_linked_worktree(place)
    if private is None:
        return {'ok': False, 'why': '这不是单独的工作目录（项目主目录永远不装）。'}
    if git(place, 'config', '--local', '--get', 'core.bare').strip() == 'true' or git(place, 'config', '--local', '--get', 'core.worktree').strip():
        return {'ok': False, 'why': '仓库是 bare 的或设了 core.worktree，没装提交钩子。'}
    folder = private / HOOK_DIR
    current = git(place, 'config', '--worktree', '--get', 'core.hooksPath').strip() if git(place, 'config', '--local', '--get', 'extensions.worktreeConfig').strip() == 'true' else ''
    if current and Path(current).resolve() != folder.resolve():
        return {'ok': False, 'why': '这个工作目录已经有自己的 core.hooksPath，没有去动它。'}
    # 用户自己的钩子里有 git 以后新增的名字：补一份，免得它们被我们的目录盖住
    names = list(HOOK_NAMES)
    for base in _user_hook_dirs(place):
        try:
            names += [n.name for n in Path(base).iterdir() if n.is_file() and not n.name.endswith('.sample') and os.access(n, os.X_OK) and '.' not in n.name]
        except OSError:
            pass
    folder.mkdir(parents=True, exist_ok=True)
    for name in dict.fromkeys(names):
        path = folder / name
        if not path.exists() or path.read_text(encoding='utf-8', errors='replace') != SCRIPT:
            path.write_text(SCRIPT, encoding='utf-8')
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    lines = [f'{KEYS[kind]}: {ident}'] + [f'{KEYS["issue"]}: {i}' for i in issues] + [f'session {sid} {tag}' for sid, tag in sessions]
    data = '\n'.join(lines) + '\n'
    target = folder / DATA
    if not target.exists() or target.read_text(encoding='utf-8') != data:
        target.write_text(data, encoding='utf-8')
    if current:
        return {'ok': True, 'why': '', 'dir': str(folder)}
    if run_git(place, 'config', '--local', 'extensions.worktreeConfig', 'true').returncode != 0:
        return {'ok': False, 'why': '没能打开 extensions.worktreeConfig。'}
    if run_git(place, 'config', '--worktree', 'core.hooksPath', str(folder)).returncode != 0:
        return {'ok': False, 'why': '没能写这个工作目录自己的 core.hooksPath。'}
    return {'ok': True, 'why': '', 'dir': str(folder)}


def _user_hook_dirs(place):
    out = []
    for scope in ('--local', '--global', '--system'):
        found = git(place, 'config', scope, '--get', 'core.hooksPath').strip()
        if found:
            found = os.path.expanduser(found)
            out.append(found if os.path.isabs(found) else str(Path(git(place, 'rev-parse', '--show-toplevel').strip()) / found))
            return out
    common = git(place, 'rev-parse', '--git-common-dir').strip()
    if common:
        out.append(str((Path(place) / common).resolve() / 'hooks'))
    return out


def ensure_here(root):
    """Install the hooks in the requirement or fix working directory `root` (a no-op anywhere else). The app calls this when it
    opens a session there, so working directories opened before this existed get them too."""
    home = main_checkout(root)
    root = Path(root).resolve()
    if root == home:
        return {'ok': False, 'why': '这是项目主目录，不装。'}
    # 迁移前开的工作目录：把迁移后的 main 合进来以后，分支里旧的 .relayforge/ 没了，这里接上指向本机记录的链接
    link_worktree(home, root)
    mine = own_requirement(root)
    if mine:
        meta = mine[1]
        ids = [str(x) for x in meta.get('sessions') or []]
        return install(root, 'requirement', meta['id'], (), [(s, stable_tag(home, s)) for s in ids])
    fix = fix_of_branch(home, branch_of(root))
    if fix:
        ids = [str(x) for x in fix.get('sessions') or []]
        return install(root, 'fix', fix['id'], fix.get('issues') or [], [(s, stable_tag(home, s)) for s in ids])
    return {'ok': False, 'why': '这个目录不是任何需求或修复的工作目录。'}


def parse(message):  # noqa

    """RelayForge-* lines of a commit message -> {'requirement': [...], 'fix': [...], 'issue': [...], 'session': [...]}."""
    found = {k: [] for k in KEYS}
    inverse = {v: k for k, v in KEYS.items()}
    for line in (message or '').splitlines():
        m = re.match(r'(RelayForge-(?:Requirement|Fix|Issue|Session)):[ \t]*(\S+)[ \t]*$', line)
        if m and m.group(2) not in found[inverse[m.group(1)]]:
            found[inverse[m.group(1)]].append(m.group(2))
    return found


install_trace_hooks = install
trailer_lines = parse

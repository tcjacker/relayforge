"""数据放在哪、导出和导入（见 FORMAT.md 的「数据放在哪」）。

数据外置的项目，记录放在本机 ~/.relayforge/projects/<项目 ID>/，不进 Git；仓库里只提交一个 .relayforge.json（项目 ID）。
换电脑、重装、要备份时用导出：把这份记录打成一个 .tar.gz；在另一台电脑上打开同一个仓库后导入，就接上了。

  data where [--json]                 这个项目的记录放在哪、是不是外置的、是不是还没迁移的旧项目
  data export 文件 [--json]            打包成 .tar.gz（不含程序副本、渲染出来的页面、日志、运行中的临时文件）
  data import 文件 [--replace] [--json] 用户：从导出的包恢复；本机已经有记录时要 --replace，原来那份改名留着，不删
  data migrate [--json]               用户：把旧项目（记录还是仓库里的 .relayforge/ 目录）迁到本机，提交一次
"""

import datetime as dt
import io
import json
import shutil
import tarfile
from pathlib import Path

from ..core.common import (
    DIR, ID_FILE, facts_store, git, git_busy, is_external, is_legacy, link_facts, main_checkout, new_project_id, project_id,
)
from .init import drop_repo_rules, install_program
from .publish import _run

__all__ = ['data_where', 'data_export', 'data_import', 'data_migrate', 'SKIP']

SCHEMA = 'relayforge.export.v1'
# 不打进包的：程序副本（应用自带）、页面和日志（重新生成）、运行中的临时文件
SKIP = {'bin', 'site', 'runs', 'log.txt', 'cursor.json', '.lock'}
MANIFEST = 'relayforge-export.json'


def data_where(root):
    home = main_checkout(root)
    ident = project_id(home)
    out = {'root': str(home), 'id': ident, 'external': is_external(home), 'legacy': is_legacy(home),
           'store': str(facts_store(ident)) if ident else None}
    if out['external']:
        out['store'] = str((home / DIR).resolve())
    return out


def _skipped(rel):
    first = rel.split('/', 1)[0]
    return first in SKIP or rel.endswith('.lock') or '__pycache__' in rel.split('/')


def data_export(root, target):
    home = main_checkout(root)
    if not is_external(home):
        raise ValueError('这个项目的记录还在仓库里（旧项目），不用导出：它们本来就跟着 Git 走。')
    store = (home / DIR).resolve()
    target = Path(target).expanduser().resolve()
    if target.is_dir():
        target = target / f'{project_id(home)}-{dt.datetime.now():%Y%m%d-%H%M%S}.tar.gz'
    target.parent.mkdir(parents=True, exist_ok=True)
    files = 0
    manifest = {'schema': SCHEMA, 'id': project_id(home), 'name': home.name, 'exported': f'{dt.datetime.now():%Y-%m-%d %H:%M:%S}'}
    with tarfile.open(target, 'w:gz') as tar:
        blob = json.dumps(manifest, ensure_ascii=False, indent=2).encode('utf-8')
        info = tarfile.TarInfo(MANIFEST)
        info.size = len(blob)
        tar.addfile(info, io.BytesIO(blob))
        for path in sorted(store.rglob('*')):
            rel = path.relative_to(store).as_posix()
            if path.is_symlink() or not path.is_file() or _skipped(rel):
                continue
            tar.add(path, arcname=f'data/{rel}', recursive=False)
            files += 1
    return {'ok': True, 'file': str(target), 'files': files, 'id': manifest['id']}


def _has_records(store):
    return store.is_dir() and any(p.suffix == '.md' for d in ('requirements', 'features', 'modules', 'issues') for p in (store / d).glob('*'))


def data_import(root, source, replace=False):
    """Restore an export into this project's external directory. The project is the one whose .relayforge.json is here;
    an export made for another project id is refused (it would mix two projects)."""
    home = main_checkout(root)
    ident = project_id(home)
    if not ident:
        raise ValueError(f'这个仓库里没有 {ID_FILE}，不是数据外置的项目，不能导入。')
    source = Path(source).expanduser().resolve()
    try:
        tar = tarfile.open(source, 'r:gz')
    except (OSError, tarfile.TarError) as error:
        raise ValueError(f'读不了这个包：{error}')
    with tar:
        try:
            manifest = json.load(tar.extractfile(MANIFEST))
        except (KeyError, ValueError, TypeError, AttributeError):
            raise ValueError('这不是 RelayForge 导出的包（里面没有说明文件）。')
        if manifest.get('schema') != SCHEMA:
            raise ValueError('这个包的格式认不出来，可能是更新的版本导出的。')
        if manifest.get('id') != ident:
            raise ValueError(f'这个包是项目 {manifest.get("id")} 的，和这个仓库的项目 {ident} 不是同一个，没有导入。')
        members = []
        for m in tar.getmembers():
            if m.name == MANIFEST:
                continue
            rel = m.name[len('data/'):] if m.name.startswith('data/') else None
            parts = Path(rel).parts if rel else ()
            if not rel or not m.isfile() or Path(rel).is_absolute() or '..' in parts or _skipped(rel):
                continue  # 只认包里 data/ 下的普通文件，不跟链接、不出目录
            members.append((m, rel))
        store = facts_store(ident)
        kept = None
        if _has_records(store):
            if not replace:
                raise ValueError('本机已经有这个项目的记录。要用包里的换掉，加 --replace（原来那份改名留着，不删）。')
            kept = store.with_name(f'{store.name}.before-import-{dt.datetime.now():%Y%m%d-%H%M%S}')
            store.rename(kept)
        scratch = store.with_name(store.name + '.importing')
        shutil.rmtree(scratch, ignore_errors=True)
        scratch.mkdir(parents=True)
        for m, rel in members:
            dest = scratch / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            with tar.extractfile(m) as src, open(dest, 'wb') as out:
                shutil.copyfileobj(src, out)
        if store.exists():
            if kept is None:  # 空的外置目录（还没有记录）：换成包里的
                shutil.rmtree(store)
        scratch.rename(store)
    if kept and (kept / 'bin').is_dir():
        shutil.copytree(kept / 'bin', store / 'bin')  # 程序副本不在包里：沿用原来那份，下面的初始化会再刷新
    link_facts(home)
    return {'ok': True, 'id': ident, 'files': len(members), 'store': str(store), 'kept': str(kept) if kept else None}


def _files(folder):
    return sorted(p.relative_to(folder).as_posix() for p in folder.rglob('*') if p.is_file() or p.is_symlink())


def data_migrate(root):
    """Move an older project's records (the .relayforge/ directory inside the repository) to this machine's store and commit once.

    - 整个目录原样复制（包括还没提交的改动和没加入 Git 的文件），核对文件一个不少，再动仓库；
    - 仓库里提交一次：从 Git 里去掉 .relayforge/，加上 .relayforge.json（项目 ID），.gitignore 加一行 /.relayforge；
      只提交这几样，项目里别的改动不动；
    - 仓库里原来那个目录不删，挪到本机 ~/.relayforge/projects/<ID>.repo-copy-时间/ 留着备查，原处换成指向本机记录的链接；
    - 以前写进仓库给 AI 的约定一并拿掉、在同一次提交里：AGENTS.md 里 RelayForge 标记之间的那一段（没别的内容就删掉文件）、
      只有 @AGENTS.md 一行的 CLAUDE.md、.claude/skills/ 下的 rf-sync、rf-confirm；用户自己写的内容不动，有没提交改动的文件不碰；
    - 中途失败就退回原样（目录挪回来、Git 的暂存区恢复、新建的东西删掉）。
    """
    home = main_checkout(root)
    base = home / DIR
    if is_external(home) or project_id(home):
        raise ValueError('这个项目已经是新的存放方式（记录在本机），不用迁移。')
    if not is_legacy(home):
        raise ValueError('这里没有旧项目的 .relayforge/ 目录，没有可迁移的。')
    if git_busy(home):
        raise ValueError('项目目录里有一次合并、变基或拣选还没做完，先把它做完再迁移。')
    staged = [line for line in git(home, 'diff', '--cached', '--name-only').splitlines() if line.strip()]
    others = [line for line in staged if not (line == DIR or line.startswith(DIR + '/') or line in ('.gitignore', ID_FILE))]
    if others:
        raise ValueError(f'暂存区里还有别的改动（{"、".join(others[:5])}），迁移要单独提交一次。先提交或取消暂存这些改动，再来迁移。')
    ident = new_project_id(home)
    store = facts_store(ident)
    if store.exists():
        raise ValueError(f'本机已经有 {store}，没有迁移。')
    stamp = dt.datetime.now().strftime('%Y%m%d-%H%M%S')
    scratch = store.with_name(ident + '.migrating')
    backup = store.with_name(f'{ident}.repo-copy-{stamp}')
    ignore = home / '.gitignore'
    ignore_before = ignore.read_text(encoding='utf-8') if ignore.exists() else None
    id_file = home / ID_FILE
    common_dir = git(home, 'rev-parse', '--git-common-dir').strip()
    exclude = (home / common_dir).resolve() / 'info' / 'exclude' if common_dir else None
    exclude_before = exclude.read_text(encoding='utf-8') if exclude and exclude.exists() else None
    shutil.rmtree(scratch, ignore_errors=True)
    scratch.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(base, scratch, symlinks=True)
    if _files(scratch) != _files(base):
        shutil.rmtree(scratch, ignore_errors=True)
        raise ValueError('复制出来的记录和仓库里的对不上，没有迁移，仓库没有动。')
    files = len(_files(scratch))
    scratch.rename(store)
    moved = untracked = False
    rules_changed, rules_notes = [], []
    try:
        code, _, err = _run(home, ['rm', '-r', '-q', '--cached', '--ignore-unmatch', '--', DIR], 60)
        if code != 0:
            raise ValueError('从 Git 里去掉 .relayforge/ 没有成功：' + (err.strip() or '没有给出原因'))
        untracked = True
        shutil.move(str(base), str(backup))
        moved = True
        id_file.write_text(json.dumps({'id': ident}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        if link_facts(home) is None or not is_external(home):
            raise ValueError('没能在项目里建好指向本机记录的链接。')
        have = ignore_before or ''
        if f'/{DIR}' not in have.splitlines():
            ignore.write_text(have.rstrip('\n') + ('\n' if have.strip() else '') + f'/{DIR}\n', encoding='utf-8')
        install_program(store, rules=True)
        dirty = {line[3:].strip('"') for line in git(home, 'status', '--porcelain', '--', 'AGENTS.md', 'CLAUDE.md', '.claude/skills').splitlines()}
        rules_changed, rules_notes = drop_repo_rules(home, keep=dirty)
        code, _, err = _run(home, ['add', '-A', '--', ID_FILE, '.gitignore', *rules_changed], 60)
        if code != 0:
            raise ValueError('暂存 .relayforge.json 和 .gitignore 没有成功：' + (err.strip() or '没有给出原因'))
        code, _, err = _run(home, ['commit', '-q', '-m', f'迁移：项目记录搬到本机 ~/.relayforge/projects/{ident}/，仓库里只留 .relayforge.json'], 120)
        if code != 0:
            raise ValueError('提交迁移没有成功：' + (err.strip() or '没有给出原因'))
    except Exception:
        # 退回原样：链接拿掉、目录挪回来、暂存区恢复、新建的东西删掉
        link = home / DIR
        if link.is_symlink():
            link.unlink()
        if moved and backup.exists() and not link.exists():
            shutil.move(str(backup), str(base))
        if untracked:
            _run(home, ['reset', '-q', '--', DIR, ID_FILE, '.gitignore', *rules_changed], 60)
        if rules_changed:
            _run(home, ['checkout', '-q', 'HEAD', '--', *rules_changed], 60)
        if id_file.exists() and not git(home, 'ls-files', '--', ID_FILE).strip():
            id_file.unlink()
        if ignore_before is None:
            if ignore.exists() and not git(home, 'ls-files', '--', '.gitignore').strip():
                ignore.unlink()
        else:
            ignore.write_text(ignore_before, encoding='utf-8')
        if exclude is not None:
            if exclude_before is None:
                exclude.unlink(missing_ok=True)
            else:
                exclude.write_text(exclude_before, encoding='utf-8')
        shutil.rmtree(store, ignore_errors=True)
        raise
    commit = git(home, 'rev-parse', '--short', 'HEAD').strip()
    return {'ok': True, 'id': ident, 'store': str(store), 'backup': str(backup), 'files': files, 'commit': commit,
            'removed_from_repo': rules_changed, 'notes': rules_notes}

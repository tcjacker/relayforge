"""会话记录另存到本机：需求确认完成或放弃、修复合并（问题解决）时，把挂在它上面的会话的原始聊天记录压缩复制一份。

Claude Code 把聊天记录放在 ~/.claude/projects/<目录名>/<会话 id>.jsonl（子代理的在旁边的 <会话 id>/subagents/ 下），
过一段时间会自己清理。想以后还能回到当时的聊天，就在完成的时候另存一份：

  ~/.relayforge/archive/<项目标识>/<R-… 或 F-…>/<会话 id>.jsonl.gz          主记录
  ~/.relayforge/archive/<项目标识>/<R-… 或 F-…>/<会话 id>/subagents/*.gz     子代理的记录（有的话）
  ~/.relayforge/archive/<项目标识>/<R-… 或 F-…>/manifest.json               每个会话从哪来、多大、什么时候存的

只在这台电脑上，不进仓库（聊天里可能有密钥，体积也大）：目录 700、文件 600。位置可以用环境变量 RELAYFORGE_ARCHIVE 换掉
（测试用），否则在 RELAYFORGE_HOME（默认 ~/.relayforge）下。项目标识是项目主目录的名字加它完整路径的一段散列。
仓库里只在需求文件头（archived、archive_missing）和修复记录 F-xxx.json 里记会话编号，加一行变更记录，不写本机路径。

复制失败从来不让确认完成、放弃或确认修好了失败：出错只记日志。rf.py archive sweep 把已经完成、还没存过（或原始记录
后来又变长了）的补存一次，应用打开项目时调用；原始记录已经不在的如实记成「原始记录已不在」。
"""

import datetime as dt
import gzip
import json
import os
import re
import shutil
from pathlib import Path

from ..core.common import DIR, commit_facts, log, main_checkout, relayforge_home, today
from ..core.documents import add_line, read_doc, write_doc
from .archivestore import archive_dir, archive_root, archive_state, project_key, read_manifest
from .record import stable_tag
from ..issues.record import issue_lock, read_probe, write_fix
from ..requirements.owners import all_fixes, read_fix

__all__ = ['archive_root', 'archive_dir', 'project_key', 'find_original', 'find_archived', 'archive_sessions', 'archive_requirement',
           'archive_fix', 'archive_sweep', 'archive_state']


def _private_dir(path):
    """Create the directory (and the archive root above it) readable by this user only."""
    root = archive_root()
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    for p in [path, *path.parents]:
        try:
            os.chmod(p, 0o700)
        except OSError:
            pass
        if p == root:
            break


def _claude_projects():
    return Path(os.environ.get('CLAUDE_CONFIG_DIR') or Path.home() / '.claude') / 'projects'


def _safe(sid):
    return bool(re.fullmatch(r'[0-9A-Za-z_.-]{4,80}', str(sid or ''))) and '..' not in str(sid)


def _codex_transcript(base, session_id):
    """应用的会话编号和 Codex 线程编号不同，从本机登记或开场白认出对应的记录。"""
    try:
        registry = json.loads((relayforge_home() / 'codex-sessions.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        registry = {}
    record = registry.get(session_id) if isinstance(registry, dict) else None
    if not isinstance(record, dict) or not isinstance(record.get('root'), str):
        hits = sorted(base.rglob(f'*{session_id}.jsonl'))
        return hits[-1] if hits else None

    root = Path(record['root']).resolve()
    marker = 'RelayForge 会话标识：' + session_id

    def matches(path, native=None):
        try:
            if not path.resolve().is_relative_to(base) or path.suffix != '.jsonl':
                return False
            with path.open(encoding='utf-8', errors='replace') as lines:
                first = json.loads(lines.readline())
                meta = first.get('payload') if isinstance(first, dict) and first.get('type') == 'session_meta' else None
                if not isinstance(meta, dict) or not isinstance(meta.get('cwd'), str) or Path(meta['cwd']).resolve() != root:
                    return False
                source = meta.get('source')
                if meta.get('thread_source') == 'guardian_review' or (isinstance(source, dict) and source.get('subagent')):
                    return False
                if native:
                    return meta.get('id') == native
                return bool(meta.get('id')) and any(marker in line for line in lines)
        except (OSError, ValueError, RuntimeError):
            return False

    saved, native = record.get('transcript'), record.get('nativeId')
    if isinstance(saved, str) and isinstance(native, str) and native and matches(Path(saved), native):
        return Path(saved)
    started = record.get('started')
    floor = started / 1000 - 2 if isinstance(started, (int, float)) else 0
    for path in sorted(base.rglob('*.jsonl'), reverse=True):
        try:
            recent = path.stat().st_mtime >= floor
        except OSError:
            continue
        if recent and matches(path):
            return path
    return None


def find_original(session_id):
    """The session's original transcript and its subagents' ({'main': Path, 'subagents': [Path]}), or None when gone."""
    if not _safe(session_id):
        return None
    hits = [p for p in _claude_projects().glob(f'*/{session_id}.jsonl') if p.is_file()]
    if hits:
        main = max(hits, key=lambda p: p.stat().st_size)
        subs = sorted(p for p in (main.parent / session_id / 'subagents').glob('*') if p.is_file() and p.suffix in ('.jsonl', '.json'))
        for p in sorted(main.parent.glob('agent-*.jsonl')):  # older Claude Code kept subagents next to the session, marked inside
            try:
                with open(p, encoding='utf-8', errors='replace') as f:
                    first = json.loads(f.readline() or '{}')
            except (OSError, ValueError):
                continue
            if isinstance(first, dict) and first.get('sessionId') == session_id:
                subs.append(p)
        return {'main': main, 'subagents': subs}
    codex = Path(os.environ.get('CODEX_HOME') or Path.home() / '.codex') / 'sessions'
    path = _codex_transcript(codex.resolve(), session_id) if codex.is_dir() else None
    return {'main': path, 'subagents': []} if path else None


def find_archived(session_id, home=None):
    """The kept copy of a session's transcript on this machine (the .jsonl.gz), or None. This project's first, then any."""
    if not _safe(session_id):
        return None
    root = archive_root()
    places = []
    if home:
        places.append(root / project_key(main_checkout(home)))
    places.append(root)
    for base in places:
        hits = sorted(base.glob(f'*/{session_id}.jsonl.gz') if base != root else root.glob(f'*/*/{session_id}.jsonl.gz'))
        if hits:
            return hits[-1]
    return None


def _gzip_copy(src, dest):
    tmp = dest.with_name(dest.name + '.part')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'wb') as raw, gzip.GzipFile(filename=src.name, mode='wb', fileobj=raw, compresslevel=6) as out, open(src, 'rb') as inp:
        shutil.copyfileobj(inp, out, 1 << 20)
    os.chmod(tmp, 0o600)
    os.replace(tmp, dest)


def _write_manifest(place, data):
    path = place / 'manifest.json'
    tmp = path.with_name('manifest.json.part')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def archive_sessions(home, ident, session_ids, refresh_only=False):
    """Copy the sessions' transcripts into this machine's archive for requirement or fix `ident`.
    Returns {'saved': [tags], 'missing': [tags], 'failed': [tags], 'copied': [tags]}: saved are those that have a kept copy now
    (copied: this call wrote it), missing those whose original is gone and that were never kept here. Never raises."""
    home = main_checkout(home)
    out = {'saved': [], 'missing': [], 'failed': [], 'copied': []}
    sids = [str(s) for s in dict.fromkeys(session_ids or []) if s]
    if not sids:
        return out
    try:
        place = archive_dir(home, ident)
        manifest = read_manifest(place)
    except Exception as error:  # noqa: BLE001 - never stop the caller
        log(home, f'archive {ident}: {error!r}')
        return dict(out, failed=[stable_tag(home, s) for s in sids])
    for sid in sids:
        tag = stable_tag(home, sid)
        try:
            found = find_original(sid)
            dest = place / f'{sid}.jsonl.gz'
            known = manifest.get(sid) or {}
            if not found:
                (out['saved'] if dest.exists() else out['missing']).append(tag)
                continue
            stat = found['main'].stat()
            fresh = dest.exists() and known.get('size') == stat.st_size and known.get('mtime') == int(stat.st_mtime)
            if fresh or (refresh_only and dest.exists() and known.get('size', 0) >= stat.st_size):
                out['saved'].append(tag)
                continue
            _private_dir(place)
            _gzip_copy(found['main'], dest)
            subs = []
            for sub in found['subagents']:
                rel = Path(sid) / 'subagents' / (sub.name + '.gz')
                _private_dir((place / rel).parent)
                _gzip_copy(sub, place / rel)
                subs.append(str(rel))
            manifest[sid] = {'tag': tag, 'source': str(found['main']), 'size': stat.st_size, 'mtime': int(stat.st_mtime),
                             'archived': dt.datetime.now().isoformat(timespec='seconds'), 'subagents': subs}
            _write_manifest(place, manifest)
            out['saved'].append(tag)
            out['copied'].append(tag)
        except Exception as error:  # noqa: BLE001 - a copy that fails must not fail 确认完成
            log(home, f'archive {ident} {sid}: {error!r}')
            out['failed'].append(tag)
    return out


def _merge_lists(meta, result):
    """Fold a result into the header lists; returns True when they changed."""
    before = ([str(x) for x in meta.get('archived') or []], [str(x) for x in meta.get('archive_missing') or []])
    saved = list(dict.fromkeys(before[0] + result['saved']))
    missing = [t for t in dict.fromkeys(before[1] + result['missing']) if t not in saved]
    for key, value in (('archived', saved), ('archive_missing', missing)):
        if value:
            meta[key] = value
        else:
            meta.pop(key, None)
    return (saved, missing) != before


def record_line(result):
    """The one line kept in the repository: which sessions are saved on this machine, whose original was already gone."""
    parts = []
    if result['saved']:
        parts.append('会话记录已存到本机：' + '、'.join(result['saved']))
    if result['missing']:
        parts.append('原始记录已不在：' + '、'.join(result['missing']))
    if result['failed']:
        parts.append('没能存下：' + '、'.join(result['failed']))
    return f'- {today()} ' + '；'.join(parts) + '。' if parts else None


def archive_requirement(home, ident, meta, sections):
    """Called while a requirement is finished or cancelled, before its file is written: copies its sessions and notes it in
    `meta` and `sections`. Never raises."""
    try:
        result = archive_sessions(home, ident, [str(x) for x in meta.get('sessions') or []])
        if _merge_lists(meta, result) or result['failed']:
            line = record_line(result)
            if line:
                add_line(sections, '变更记录', line)
        return result
    except Exception as error:  # noqa: BLE001
        log(main_checkout(home), f'archive {ident}: {error!r}')
        return None


def _fix_sessions(home, fix):
    ids = [str(x) for x in fix.get('sessions') or []]
    for one in fix.get('issues') or []:
        ids += [str(x) for x in (read_probe(home, one) or {}).get('sessions') or []]
    return list(dict.fromkeys(ids))


def archive_fix(home, fix):
    """Called when a fix has been merged (its issues resolved): copies its sessions (and the investigation sessions of its
    issues) and notes them in the fix record. Never raises; the caller writes the record."""
    try:
        result = archive_sessions(home, fix['id'], _fix_sessions(home, fix))
        _merge_lists(fix, result)
        return result
    except Exception as error:  # noqa: BLE001
        log(main_checkout(home), f'archive {fix.get("id")}: {error!r}')
        return None


def archive_sweep(root):
    """Save what finished work has not saved yet: requirements done or cancelled, fixes merged. Sessions whose original is
    gone are recorded as such once. Returns {'items': [{id, saved, missing, copied}], 'changed': bool}."""
    home = main_checkout(root)
    report, changed = [], False
    for path in sorted((home / DIR / 'requirements').glob('*.md')):
        try:
            meta, sections = read_doc(path)
        except OSError:
            continue
        if not meta.get('id') or meta.get('state') not in ('done', 'dropped') or not meta.get('sessions'):
            continue
        result = archive_sessions(home, meta['id'], [str(x) for x in meta['sessions']], refresh_only=True)
        new = _merge_lists(meta, result)
        if new:
            line = record_line({'saved': result['copied'] or [], 'missing': [t for t in result['missing']], 'failed': result['failed']})
            if line:
                add_line(sections, '变更记录', line)
            write_doc(path, meta, sections)
            changed = True
        if result['copied'] or new:
            report.append({'id': meta['id'], **{k: result[k] for k in ('saved', 'missing', 'copied', 'failed')}})
    for fid, fix in all_fixes(home).items():
        if fix.get('state') != 'merged':
            continue
        result = archive_sessions(home, fid, _fix_sessions(home, fix), refresh_only=True)
        with issue_lock(home):
            fresh = read_fix(home, fid) or fix
            new = _merge_lists(fresh, result)
            if new:
                write_fix(home, fresh)
                changed = True
        if result['copied'] or new:
            report.append({'id': fid, **{k: result[k] for k in ('saved', 'missing', 'copied', 'failed')}})
    if changed:
        commit_facts(home, '记录会话存档')
    return {'items': report, 'changed': changed}

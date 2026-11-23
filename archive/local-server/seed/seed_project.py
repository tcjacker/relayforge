"""Versioned project materials. PostgreSQL coordinates; Git owns published text."""
import hashlib
import json
from pathlib import Path
import re
import stat
import tempfile
from uuid import UUID

import seed_engine as engine
import seed_store as store


class GitFailure(ValueError):
    pass


class Conflict(ValueError):
    pass


class PathRejected(ValueError):
    pass


class InvalidState(ValueError):
    pass


class ValidationFailed(ValueError):
    pass


DOCUMENTS = (
    dict(id='context', path='PROJECT-CONTEXT.md', purpose='项目长期说明', roles=['main', 'worker'], editable=True),
    dict(id='shared', path='AGENTS.md', purpose='共享 Agent 指令', roles=['main', 'worker'], editable=True),
    dict(id='main', path='agents/main.md', purpose='主 Agent 指令', roles=['main'], editable=True),
    dict(id='worker', path='agents/worker.md', purpose='Worker 指令', roles=['worker'], editable=True),
    dict(id='readme', path='README.md', purpose='只读来源', roles=[], editable=False),
    dict(id='design', path='PROJECT-DESIGN.md', purpose='只读设计来源（非确认裁定）', roles=[], editable=False),
)
TEMPLATE = '# 项目上下文\n\n## 长期目标\n\n## 架构\n\n## 全局约束\n\n## 已确认裁定\n'


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


def list_documents():
    return json.loads(json.dumps(DOCUMENTS, ensure_ascii=False))


def document(ident):
    for doc in DOCUMENTS:
        if ident == doc['id']:
            return doc
    raise PathRejected('只接受登记文档 id')


def rebuild_context(manifest):
    """Validate evidence without Git/DB; return exact file bodies, including missing."""
    result = {}
    for item in manifest['files']:
        doc = document(item['id'])
        if item['path'] != doc['path'] or item['sha256'] != hashlib.sha256(
                (item['content'] or '').encode()).hexdigest():
            raise ValidationFailed('上下文摘要不匹配')
        if item['exists'] != (item['content'] is not None):
            raise ValidationFailed('上下文存在状态不匹配')
        result[item['path']] = item['content']
    if manifest['digest'] != digest({k: v for k, v in manifest.items() if k != 'digest'}):
        raise ValidationFailed('清单摘要不匹配')
    return result


class Project:
    def __init__(self, root=None, verifier=None, fault=None):
        self.root = Path(root or engine.ROOT).resolve()
        self.verifier = verifier or engine.verify
        self.fault = fault or (lambda point: None)
        store.project_init()
        initial = self.git('rev-parse', 'HEAD')
        state = store.project_start(dict(root=str(self.root), initial=initial,
                                         published=initial, documents=list_documents()))
        if state['root'] != str(self.root):
            raise Conflict('数据库已绑定其他仓库')

    def git(self, *args):
        try:
            return engine.git(self.root, *args)
        except RuntimeError as exc:
            raise GitFailure(str(exc)) from exc

    def sha(self, revision):
        # User-supplied commits must be full object ids. HEAD is internal only.
        if not isinstance(revision, str) or not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', revision):
            raise ValueError('需要完整提交 SHA')
        return self.git('rev-parse', '--verify', revision + '^{commit}')

    def _file(self, commit, doc):
        path = doc['path']
        for prefix in [path.split('/')[0]] if '/' in path else []:
            entry = self.git('ls-tree', commit, '--', prefix)
            if entry and not entry.startswith('040000 tree '):
                raise PathRejected('父路径不是普通目录')
        entry = self.git('ls-tree', commit, '--', path)
        if not entry:
            return dict(id=doc['id'], path=path, exists=False, blob=None,
                        sha256=hashlib.sha256(b'').hexdigest(), content=None)
        mode, kind, rest = entry.split(' ', 2)
        blob = rest.split('\t')[0]
        if mode not in ('100644', '100755') or kind != 'blob':
            raise PathRejected('只允许普通文件')
        # engine.git strips trailing whitespace; raw bytes preserve Markdown exactly.
        import subprocess
        data = subprocess.run(['git', 'cat-file', 'blob', blob], cwd=self.root,
                              capture_output=True, check=True).stdout
        return dict(id=doc['id'], path=path, exists=True, blob=blob,
                    sha256=hashlib.sha256(data).hexdigest(), content=data.decode('utf-8'))

    def _allowed(self, commit):
        state = store.project_get()
        return commit == state['initial'] or any(
            p['state'] == 'activated' and p['candidate_commit'] == commit
            for p in store.project_publications())

    def read_published(self, commit=None):
        self.recover_publications()
        sha = self.sha(commit or store.project_get()['published'])
        if not self._allowed(sha):
            raise InvalidState('提交尚未人工启用')
        return dict(commit=sha, files=[self._file(sha, d) for d in DOCUMENTS])

    def resolve_context(self, commit, role):
        if role not in ('main', 'worker'):
            raise ValueError('未知角色')
        published = self.read_published(commit)
        result = dict(format_version=1, role=role, commit=published['commit'], files=[
            f for f in published['files'] if role in document(f['id'])['roles']])
        return {**result, 'digest': digest(result)}

    def list_drafts(self):
        """Immutable snapshots, newest version first."""
        return store.project_drafts()

    def publication_diff(self, request_id):
        """Read only the registered, verified candidate; never bless it as published."""
        pub = self.get_publish(request_id)
        if not pub:
            raise InvalidState('发布请求不存在')
        candidate = pub.get('candidate_commit')
        if not candidate:
            return ''
        self._check_candidate(pub, candidate)
        return self.git('diff', '--no-ext-diff', '--no-textconv',
                        pub['base_commit'], candidate, '--',
                        *[d['path'] for d in DOCUMENTS if d['editable']])

    def get_draft(self):
        return store.project_draft()

    def save_draft(self, expected_version, contents, base_commit):
        base = self.sha(base_commit)
        source = store.project_get()['published']
        if base != self.git('rev-parse', 'HEAD'):
            raise Conflict('发布基线已变化')
        previous = self.get_draft()
        if (previous['version'] if previous else 0) != expected_version:
            raise Conflict('草稿版本已变化')
        # A patch is expanded into an immutable whole-package snapshot.
        bodies = {d['id']: self._file(source, d)['content'] for d in DOCUMENTS if d['editable']}
        if previous:
            bodies.update(previous['contents'])
        for ident, body in contents.items():
            doc = document(ident)
            if not doc['editable'] or (body is not None and not isinstance(body, str)):
                raise PathRejected('只读来源不可编辑，正文必须为文本或 null')
            bodies[ident] = body
        data = dict(version=expected_version + 1, base_commit=base, source_commit=source, contents=bodies)
        try:
            return store.project_save(expected_version, data)
        except RuntimeError as exc:
            raise Conflict(str(exc)) from exc

    def get_publish(self, request_id):
        return store.project_publication(str(UUID(request_id)))

    def list_publications(self):
        return store.project_publications()

    def _set(self, publication, **fields):
        publication.update(fields)
        store.project_transition(publication, publication['state'] == 'activated')
        return publication

    def _validate_context(self, contents):
        body = contents.get('context')
        if body is None:
            return
        body = body.replace('\r\n', '\n')
        headings = ['长期目标', '架构', '全局约束', '已确认裁定']
        if any(body.count('## ' + h + '\n') != 1 for h in headings):
            raise ValidationFailed('项目上下文必须包含四个唯一章节')
        decisions = body.split('## 已确认裁定\n', 1)[1].split('\n## ', 1)[0]
        for paragraph in re.split(r'\n\s*\n', decisions.strip()):
            if paragraph.strip() and not re.search(r'确认来源[：:]\s*\S+', paragraph):
                raise ValidationFailed('每项已确认裁定必须注明确认来源')

    def prepare_publish(self, request_id, draft_version, base_commit):
        request_id, base = str(UUID(request_id)), self.sha(base_commit)
        fingerprint = digest([draft_version, base])
        with engine.activation_lock(self.root):
            old = self.get_publish(request_id)
            if old:
                if old['fingerprint'] != fingerprint:
                    raise Conflict('发布请求标识已使用')
                return self._prepare(old) if old['state'] == 'preparing' else old
            draft = self.get_draft()
            if not draft or draft['version'] != draft_version or draft['base_commit'] != base:
                raise Conflict('草稿已过期或基线不匹配')
            if self.git('rev-parse', 'HEAD') != base or draft['source_commit'] != store.project_get()['published']:
                raise Conflict('main 或发布基线已变化')
            pub = dict(request_id=request_id, fingerprint=fingerprint, draft_version=draft_version,
                       base_commit=base, candidate_commit=None, contents=draft['contents'],
                       state='preparing', evidence=None, human_confirmation=None)
            try:
                pub = store.project_request(pub)
            except RuntimeError as exc:
                raise Conflict(str(exc)) from exc
            self.fault('request_saved')
            return self._prepare(pub)

    def _prepare(self, pub):
        try:
            self._validate_context(pub['contents'])
            ref = 'refs/seed-project/' + pub['request_id']
            existing = self.git('show-ref', '--verify', '--hash', ref) if self._ref_exists(ref) else None
            if existing:
                candidate = existing
            else:
                common = Path(self.git('rev-parse', '--git-common-dir'))
                if not common.is_absolute():
                    common = self.root / common
                parent = common / 'seed-project-publications'
                parent.mkdir(exist_ok=True)
                with tempfile.TemporaryDirectory(dir=parent) as temp:
                    path = Path(temp) / 'candidate'
                    self.git('worktree', 'add', '--detach', str(path), pub['base_commit'])
                    try:
                        for ident, body in pub['contents'].items():
                            doc = document(ident)
                            if not doc['editable']:
                                raise PathRejected('只读来源不可发布')
                            self._file(pub['base_commit'], doc)
                            target = path / doc['path']
                            self._safe_target(path, target)
                            if body is None:
                                if target.exists():
                                    target.unlink()
                            else:
                                target.parent.mkdir(parents=True, exist_ok=True)
                                target.write_text(body, encoding='utf-8')
                            if body is not None or self._file(pub['base_commit'], doc)['exists']:
                                engine.git(path, 'add', '--', doc['path'])
                        engine.git(path, '-c', 'user.name=Project Guide', '-c',
                                   'user.email=project-guide@localhost', 'commit', '--allow-empty',
                                   '-m', 'Project materials ' + pub['request_id'])
                        candidate = engine.git(path, 'rev-parse', 'HEAD')
                        self.git('update-ref', ref, candidate)
                        self.fault('candidate_committed')
                    finally:
                        self.git('worktree', 'remove', '--force', str(path))
            self._check_candidate(pub, candidate)
            self._set(pub, candidate_commit=candidate)
            with tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / 'verify'
                self.git('worktree', 'add', '--detach', str(path), candidate)
                try:
                    log = self.verifier(path, pub['base_commit'])
                finally:
                    self.git('worktree', 'remove', '--force', str(path))
            self.fault('verified')
            return self._set(pub, state='ready', evidence=dict(base=pub['base_commit'],
                candidate=candidate, contents_digest=digest(pub['contents']), log=log))
        except Exception as exc:
            self._set(pub, state='failed', error=str(exc))
            return pub

    def _ref_exists(self, ref):
        try:
            self.git('show-ref', '--verify', ref)
            return True
        except GitFailure:
            return False

    @staticmethod
    def _safe_target(root, target):
        for item in [*reversed(target.parents), target]:
            if item == root or root not in item.parents:
                continue
            if item.is_symlink() or (item.exists() and not (
                    item.is_dir() if item != target else stat.S_ISREG(item.lstat().st_mode))):
                raise PathRejected('工作树包含符号链接或非普通文件')

    def _check_candidate(self, pub, candidate):
        if self.git('rev-parse', candidate + '^') != pub['base_commit']:
            raise ValidationFailed('候选父提交不匹配')
        allowed = {d['path'] for d in DOCUMENTS if d['editable']}
        changed = self.git('diff-tree', '--no-commit-id', '--name-only', '-r',
                           '--no-renames', pub['base_commit'], candidate).splitlines()
        if not set(changed) <= allowed:
            raise PathRejected('候选修改越界')
        for ident, body in pub['contents'].items():
            if self._file(candidate, document(ident))['content'] != body:
                raise ValidationFailed('候选与冻结草稿不匹配')

    def activate_publish(self, request_id, expected_candidate, expected_base, human_confirmation):
        if not isinstance(human_confirmation, str) or not human_confirmation.strip():
            raise InvalidState('需要人工确认说明')
        with engine.activation_lock(self.root):
            pub = self.get_publish(request_id)
            if not pub or (pub['candidate_commit'], pub['base_commit']) != (expected_candidate, expected_base):
                raise Conflict('候选或基线已变化')
            if pub['state'] == 'activated':
                return pub
            if pub['state'] != 'ready':
                raise InvalidState('候选未通过验证')
            self._check_activation(pub)
            self._clean_base(pub)
            self._set(pub, state='activating', human_confirmation=human_confirmation)
            self.fault('intent_saved')
            return self._activate(pub)

    def _check_activation(self, pub):
        if self.git('rev-parse', 'refs/seed-project/' + pub['request_id']) != pub['candidate_commit']:
            raise ValidationFailed('候选引用不匹配')
        ev = pub['evidence']
        if not ev or (ev['base'], ev['candidate'], ev['contents_digest']) != (
                pub['base_commit'], pub['candidate_commit'], digest(pub['contents'])):
            raise ValidationFailed('验证证据不匹配')
        self._check_candidate(pub, pub['candidate_commit'])
        if self.get_draft()['version'] != pub['draft_version']:
            raise Conflict('草稿已过期')

    def _clean_base(self, pub):
        if self.git('branch', '--show-current') != 'main' or self.git('status', '--porcelain', '--untracked-files=all'):
            raise Conflict('main 工作区不干净')
        if self.git('rev-parse', 'HEAD') != pub['base_commit']:
            raise Conflict('main 已前移')

    def _activate(self, pub):
        self._check_activation(pub)
        if not pub['human_confirmation']:
            raise InvalidState('缺少人工授权')
        try:
            self.git('merge-base', '--is-ancestor', pub['candidate_commit'], 'refs/heads/main')
            included = True
        except GitFailure:
            included = False
        if not included:
            self._clean_base(pub)
            try:
                engine.activate_locked(dict(base=pub['base_commit'], candidate=pub['candidate_commit']), self.root)
            except RuntimeError as exc:
                raise GitFailure(str(exc)) from exc
            self.fault('git_activated')
        return self._set(pub, state='activated')

    def recover_publications(self):
        with engine.activation_lock(self.root):
            for pub in self.list_publications():
                if pub['state'] == 'preparing':
                    self._prepare(pub)
                elif pub['state'] == 'activating':
                    try:
                        self._activate(pub)
                    except ValueError as exc:
                        # Database failures propagate; only domain/Git conflicts become terminal.
                        self._set(pub, state='conflict', error=str(exc))
        return self.list_publications()

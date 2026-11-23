"""Pure rules plus real Git; persistence/concurrency lives in test_seed_project_pg."""
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import seed_engine as engine
import seed_project as project


class RepositoryCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        engine.git(self.root, 'init', '-b', 'main')
        (self.root / '.gitignore').write_text('.seed/\n__pycache__/\n')
        (self.root / 'README.md').write_text("source ' text\n\n")
        (self.root / 'PROJECT-DESIGN.md').write_text('草拟裁定：自动执行全部产品判断\n')
        (self.root / 'project.json').write_text('{"decision":"not confirmed"}')
        (self.root / 'test_base.py').write_text('import unittest\nclass Base(unittest.TestCase):\n def test_ok(self): self.assertTrue(True)\n')
        self.commit('initial')
        self.base = engine.git(self.root, 'rev-parse', 'HEAD')

    def commit(self, message):
        engine.git(self.root, 'add', '.')
        engine.git(self.root, '-c', 'user.name=Test', '-c', 'user.email=test@local', 'commit', '-m', message)

    def reader(self):
        obj = object.__new__(project.Project)
        obj.root = self.root
        return obj


class ProjectRulesTests(RepositoryCase):
    def test_exact_ids_and_no_path_registration(self):
        for bad in ('README.md', '/tmp/AGENTS.md', '../shared', 'agents/../AGENTS.md',
                    'shared\x00', './shared', '', 'project.json', 'agents//main.md'):
            with self.subTest(bad=bad), self.assertRaises(project.PathRejected):
                project.document(bad)
        self.assertEqual({d['path'] for d in project.list_documents() if d['editable']},
                         {'PROJECT-CONTEXT.md', 'AGENTS.md', 'agents/main.md', 'agents/worker.md'})

    def test_commit_read_preserves_body_and_missing(self):
        obj = self.reader()
        first = obj._file(self.base, project.document('readme'))
        self.assertEqual(first['content'], "source ' text\n\n")
        self.assertFalse(obj._file(self.base, project.document('shared'))['exists'])
        (self.root / 'README.md').write_text('second\n')
        self.commit('second')
        second = engine.git(self.root, 'rev-parse', 'HEAD')
        self.assertEqual(obj._file(second, project.document('readme'))['content'], 'second\n')
        self.assertEqual(obj._file(self.base, project.document('readme')), first)
        with self.assertRaises(ValueError):
            obj.sha('--all')

    def test_git_symlinks_directories_submodules_and_parent_modes(self):
        obj = self.reader()
        os.symlink('/tmp/outside', self.root / 'AGENTS.md')
        os.symlink('/tmp', self.root / 'agents')
        (self.root / 'PROJECT-CONTEXT.md').mkdir()
        (self.root / 'PROJECT-CONTEXT.md' / 'x').write_text('x')
        self.commit('unsafe')
        sha = engine.git(self.root, 'rev-parse', 'HEAD')
        for ident in ('shared', 'main', 'context'):
            with self.subTest(ident=ident), self.assertRaises(project.PathRejected):
                obj._file(sha, project.document(ident))
        engine.git(self.root, 'update-index', '--add', '--cacheinfo', '160000,' + self.base + ',README.md')
        engine.git(self.root, '-c', 'user.name=Test', '-c', 'user.email=test@local', 'commit', '-m', 'submodule')
        with self.assertRaises(project.PathRejected):
            obj._file(engine.git(self.root, 'rev-parse', 'HEAD'), project.document('readme'))

    def test_local_target_fifo_and_symlink_rejected(self):
        target = self.root / 'AGENTS.md'
        os.mkfifo(target)
        with self.assertRaises(project.PathRejected):
            project.Project._safe_target(self.root, target)
        target.unlink()
        os.symlink('/tmp', self.root / 'agents')
        with self.assertRaises(project.PathRejected):
            project.Project._safe_target(self.root, self.root / 'agents/main.md')

    def test_context_requires_sources_without_promoting_design(self):
        obj = self.reader()
        obj._validate_context({'context': project.TEMPLATE})
        with self.assertRaises(project.ValidationFailed):
            obj._validate_context({'context': project.TEMPLATE + '- 自动接受草稿\n'})
        obj._validate_context({'context': project.TEMPLATE + '- 人工确认\n  确认来源：用户消息 42\n'})
        with self.assertRaises(project.ValidationFailed):
            obj._validate_context({'context': 'arbitrary'})
        self.assertNotIn('not confirmed', project.TEMPLATE)

    def test_offline_manifest_detects_tampering(self):
        obj = self.reader()
        files = [obj._file(self.base, d) for d in project.DOCUMENTS if 'main' in d['roles']]
        data = dict(format_version=1, role='main', commit=self.base, files=files)
        manifest = {**data, 'digest': project.digest(data)}
        self.assertEqual(project.rebuild_context(manifest)['AGENTS.md'], None)
        manifest['files'][0]['content'] = 'tampered'
        with self.assertRaises(project.ValidationFailed):
            project.rebuild_context(manifest)

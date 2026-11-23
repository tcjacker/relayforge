"""Real PostgreSQL isolated schemas and Git worktrees; no model calls."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch
from uuid import uuid4

import seed_engine as engine
import seed_project as project
import seed_store as store
from test_seed_project import RepositoryCase


class Crash(BaseException):
    pass


@unittest.skipUnless(os.environ.get('SEED_TEST_PG') == '1', 'requires local PostgreSQL')
class ProjectPostgresTests(RepositoryCase):
    def setUp(self):
        super().setUp()
        self.schema = 'seed_test_' + uuid4().hex
        store.sql('CREATE SCHEMA ' + self.schema)
        self.env = patch.dict(os.environ, PGOPTIONS='-c search_path=' + self.schema)
        self.env.start()
        def cleanup():
            self.env.stop()
            store.sql('DROP SCHEMA ' + self.schema + ' CASCADE')
        self.addCleanup(cleanup)
        store.init()
        self.p = project.Project(self.root)

    def draft(self, expected=0, text="rule ' \\ 中文\n\n"):
        return self.p.save_draft(expected, {'context': project.TEMPLATE,
            'shared': text, 'main': 'main\n', 'worker': 'worker\n'}, self.base)

    def prepare(self, ident=None, version=1):
        return self.p.prepare_publish(ident or str(uuid4()), version, self.base)

    def enable(self, pub):
        return self.p.activate_publish(pub['request_id'], pub['candidate_commit'], self.base, '用户批准此候选')

    def race(self, *calls):
        barrier = threading.Barrier(len(calls))
        def call(fn):
            barrier.wait(10)
            try:
                return True, fn()
            except (ValueError, RuntimeError) as exc:
                return False, str(exc)
        with ThreadPoolExecutor(max_workers=len(calls)) as pool:
            return list(pool.map(call, calls))

    def child(self, action):
        code = 'import seed_project; p=seed_project.Project(' + repr(str(self.root)) + '); ' + action
        return subprocess.run([sys.executable, '-B', '-c', code], capture_output=True,
                              text=True, check=True).stdout

    def test_draft_atomic_race_restart_and_readonly_sources(self):
        initial = self.p.read_published()
        self.assertFalse(next(f for f in initial['files'] if f['id'] == 'context')['exists'])
        result = self.race(*[lambda: self.draft() for _ in range(4)])
        self.assertEqual(sum(ok for ok, _ in result), 1, result)
        saved = self.p.get_draft()
        self.assertEqual(saved, json.loads(self.child('import json; print(json.dumps(p.get_draft()))')))
        self.assertEqual(engine.git(self.root, 'rev-parse', 'HEAD'), self.base)
        self.assertFalse((self.root / 'AGENTS.md').exists())
        self.assertEqual(saved['contents']['context'], project.TEMPLATE)
        for ident in ('design', 'readme', '../AGENTS.md', '/AGENTS.md'):
            with self.assertRaises(project.PathRejected):
                self.p.save_draft(1, {ident: 'modified'}, self.base)
        store.init()
        self.assertEqual(self.p.get_draft(), saved)

    def test_real_verify_publish_history_roles_and_task_worktree_untouched(self):
        self.draft()
        task = self.root / '.seed/trees/running'
        engine.git(self.root, 'worktree', 'add', '--detach', str(task), self.base)
        (task / 'README.md').write_text('running task dirty')
        before = (engine.git(task, 'rev-parse', 'HEAD'), engine.git(task, 'diff'))
        pub = self.prepare()
        self.assertEqual(pub['state'], 'ready', pub)
        self.assertEqual(self.p.read_published()['commit'], self.base)
        with self.assertRaises(project.InvalidState):
            self.p.resolve_context(pub['candidate_commit'], 'main')
        self.enable(pub)
        manifest = self.p.resolve_context(pub['candidate_commit'], 'main')
        self.assertEqual(project.rebuild_context(manifest)['AGENTS.md'], self.p.get_draft()['contents']['shared'])
        self.assertNotIn('agents/worker.md', project.rebuild_context(manifest))
        self.assertIsNone(project.rebuild_context(self.p.resolve_context(self.base, 'main'))['AGENTS.md'])
        self.assertEqual(before, (engine.git(task, 'rev-parse', 'HEAD'), engine.git(task, 'diff')))
        self.assertEqual(self.enable(pub)['state'], 'activated')
        self.assertEqual(int(store.sql('SELECT count(*) FROM seed_project_events')), 1)

    def test_request_retries_stale_drafts_dirty_main_and_baseline(self):
        self.draft()
        pub = self.prepare()
        self.assertEqual(self.prepare(pub['request_id']), pub)
        with self.assertRaises(project.Conflict):
            self.prepare(pub['request_id'], 2)
        original = (self.root / 'README.md').read_text()
        (self.root / 'README.md').write_text('tracked edit to preserve')
        with self.assertRaises(project.Conflict):
            self.enable(pub)
        self.assertEqual((self.root / 'README.md').read_text(), 'tracked edit to preserve')
        (self.root / 'README.md').write_text(original)
        (self.root / 'untracked').write_text('keep')
        with self.assertRaises(project.Conflict):
            self.enable(pub)
        self.assertEqual((self.root / 'untracked').read_text(), 'keep')
        (self.root / 'untracked').unlink()
        self.draft(1, 'new rule')
        with self.assertRaises(project.Conflict):
            self.enable(pub)
        with self.assertRaises(project.Conflict):
            self.prepare()
        pub2 = self.prepare(version=2)
        (self.root / 'other').write_text('main advanced')
        self.commit('advance')
        with self.assertRaises(project.Conflict):
            self.enable(pub2)
        self.assertEqual((self.root / 'other').read_text(), 'main advanced')

    def test_competing_enables_only_one_wins(self):
        self.draft()
        a, b = self.prepare(), self.prepare()
        result = self.race(lambda: self.enable(a), lambda: self.enable(b))
        self.assertEqual(sum(ok for ok, _ in result), 1, result)
        self.assertEqual(int(store.sql('SELECT count(*) FROM seed_project_events')), 1)

    def test_failed_verification_never_becomes_source(self):
        self.draft()
        def fail(*args):
            raise RuntimeError('test failure')
        self.p.verifier = fail
        pub = self.prepare()
        self.assertEqual(pub['state'], 'failed')
        with self.assertRaises(project.InvalidState):
            self.enable(pub)
        with self.assertRaises(project.InvalidState):
            self.p.resolve_context(pub['candidate_commit'], 'worker')
        self.assertEqual(self.p.read_published()['commit'], self.base)

    def test_candidate_tampering_and_readonly_diff_rejected(self):
        self.draft()
        pub = self.prepare()
        path = self.root / '.seed/tamper'
        engine.git(self.root, 'worktree', 'add', '--detach', str(path), self.base)
        (path / 'README.md').write_text('bad')
        engine.git(path, 'add', 'README.md')
        engine.git(path, '-c', 'user.name=Test', '-c', 'user.email=t@local', 'commit', '-m', 'bad')
        with self.assertRaises(project.PathRejected):
            self.p._check_candidate(pub, engine.git(path, 'rev-parse', 'HEAD'))
        pub['evidence']['candidate'] = self.base
        store.project_transition(pub)
        with self.assertRaises(project.ValidationFailed):
            self.enable(pub)

    def test_failure_boundaries_recover_in_fresh_process(self):
        self.draft()
        for point in ('request_saved', 'candidate_committed', 'verified', 'intent_saved', 'git_activated'):
            with self.subTest(point=point):
                ident = str(uuid4())
                def fault(where):
                    if where == point:
                        raise Crash(where)
                self.p.fault = fault
                if point in ('intent_saved', 'git_activated'):
                    pub = self.prepare(ident, self.p.get_draft()['version'])
                    with self.assertRaises(Crash):
                        self.enable(pub)
                else:
                    with self.assertRaises(Crash):
                        self.prepare(ident)
                self.p.fault = lambda _: None
                self.child('p.recover_publications()')
                restored = self.p.get_publish(ident)
                if point in ('intent_saved', 'git_activated'):
                    self.assertEqual(restored['state'], 'activated')
                    self.assertEqual(self.p.read_published()['commit'], restored['candidate_commit'])
                    # Subsequent independent cases use the newly published base.
                    self.base = restored['candidate_commit']
                    self.draft(self.p.get_draft()['version'])
                else:
                    self.assertEqual(restored['state'], 'ready', restored)
                    self.assertEqual(self.p.read_published()['commit'], self.base)
                published = self.p.read_published()
                current = self.p.get_publish(ident)
                if current['state'] == 'activated':
                    for item in published['files']:
                        if project.document(item['id'])['editable']:
                            self.assertEqual(item['content'], current['contents'][item['id']])
                    project.rebuild_context(self.p.resolve_context(published['commit'], 'worker'))
                self.child('p.recover_publications(); p.recover_publications()')
                self.assertNotIn(self.p.get_publish(ident)['state'], ('preparing', 'activating'))
        self.assertEqual(int(store.sql('SELECT count(*) FROM seed_project_events')), 2)

    def test_git_success_then_main_advance_and_db_outage(self):
        self.draft()
        pub = self.prepare()
        def fault(point):
            if point == 'git_activated':
                raise Crash(point)
        self.p.fault = fault
        with self.assertRaises(Crash):
            self.enable(pub)
        (self.root / 'after').write_text('normal subsequent change')
        self.commit('after enabled candidate')
        with patch.object(store, 'project_transition', side_effect=RuntimeError('database unavailable')):
            with self.assertRaises(RuntimeError):
                project.Project(self.root).recover_publications()
        self.child('p.recover_publications()')
        self.assertEqual(self.p.get_publish(pub['request_id'])['state'], 'activated')
        self.assertEqual(self.p.read_published()['commit'], pub['candidate_commit'])
        self.assertEqual((self.root / 'after').read_text(), 'normal subsequent change')
        self.assertEqual(int(store.sql('SELECT count(*) FROM seed_project_events')), 1)

    def test_authorized_recovery_conflict_releases_pending(self):
        self.draft()
        pub = self.prepare()
        self.p.fault = lambda point: (_ for _ in ()).throw(Crash()) if point == 'intent_saved' else None
        with self.assertRaises(Crash):
            self.enable(pub)
        (self.root / 'dirty').write_text('keep')
        self.child('p.recover_publications()')
        self.assertEqual(self.p.get_publish(pub['request_id'])['state'], 'conflict')
        self.assertEqual(self.p.read_published()['commit'], self.base)
        self.assertEqual((self.root / 'dirty').read_text(), 'keep')
        self.draft(1)

    def test_new_explicit_base_after_normal_main_change(self):
        self.draft()
        first = self.prepare()
        self.enable(first)
        self.p.save_draft(1, {'worker': 'unpublished worker edit\n'}, first['candidate_commit'])
        (self.root / 'code.py').write_text('value = 2\n')
        self.commit('normal code change')
        new_base = engine.git(self.root, 'rev-parse', 'HEAD')
        self.assertEqual(self.p.read_published()['commit'], first['candidate_commit'])
        saved = self.p.save_draft(2, {'shared': 'updated rule\n'}, new_base)
        self.assertEqual(saved['contents']['worker'], 'unpublished worker edit\n')
        self.assertEqual(saved['source_commit'], first['candidate_commit'])
        second = self.p.prepare_publish(str(uuid4()), 3, new_base)
        self.assertEqual(second['state'], 'ready', second)
        self.p.activate_publish(second['request_id'], second['candidate_commit'], new_base, '确认新版')
        self.assertEqual((self.root / 'code.py').read_text(), 'value = 2\n')
        self.assertEqual(self.p.read_published()['commit'], second['candidate_commit'])

    def test_recovery_requires_main_ancestry_and_candidate_ref(self):
        self.draft()
        pub = self.prepare()
        self.p.fault = lambda point: (_ for _ in ()).throw(Crash()) if point == 'intent_saved' else None
        with self.assertRaises(Crash):
            self.enable(pub)
        # HEAD includes the candidate but main does not. Recovery must not certify it.
        engine.git(self.root, 'switch', '--detach', pub['candidate_commit'])
        self.child('p.recover_publications()')
        self.assertEqual(self.p.get_publish(pub['request_id'])['state'], 'conflict')
        self.assertEqual(self.p.read_published()['commit'], self.base)
        engine.git(self.root, 'switch', 'main')
        self.p.fault = lambda _: None
        other = self.prepare()
        engine.git(self.root, 'update-ref', 'refs/seed-project/' + other['request_id'], self.base)
        with self.assertRaises(project.ValidationFailed):
            self.enable(other)

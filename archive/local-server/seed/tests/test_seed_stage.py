"""Deterministic model/form/scheduling evidence; no live model calls."""
import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4

import seed
import seed_engine as engine
import seed_stage as rules
import seed_stage_web as web
import test_seed


def cards():
    a, b = str(uuid4()), str(uuid4())
    return [dict(id=a, title='A', goal='实现 A', acceptance='可验证 A', blockedBy=[]),
            dict(id=b, title='B', goal='实现 B', acceptance='可验证 B', blockedBy=[a])]


def stage_fixture():
    return dict(id=str(uuid4()), version=2, state='draft', goal='阶段目标', acceptance='阶段验收',
                cards=cards(), source='a'*40, tasks=[], authorized_version=None)


def events(card, stage, state='queued'):
    data = {**card, 'stage_id': stage['id'], 'draft_version': stage['version']}
    return [dict(seq=1, state=state, recorded_at='now', data=data)]


def params_for(stage):
    params = {'action': ['stage_save'], 'stage_id': [stage['id']], 'version': [str(stage['version'])],
              'card_id': [c['id'] for c in stage['cards']]}
    for c in stage['cards']:
        for key in ('goal', 'acceptance', 'title'):
            params[key+'_'+c['id']] = [c[key]]
        params['dep_'+c['id']] = c['blockedBy']
    return params


class StageRulesTests(unittest.TestCase):
    def test_errors_are_readable(self):
        for modify, message in [
            (lambda c: c[0].update(acceptance=''), '验收条件'),
            (lambda c: c[0].update(blockedBy=['missing']), '缺失依赖'),
            (lambda c: c[0].update(blockedBy=[c[0]['id']]), '自身'),
            (lambda c: c[0].update(blockedBy=[c[1]['id']]), 'A → B → A')]:
            with self.subTest(message=message):
                data = cards()
                modify(data)
                with self.assertRaisesRegex(ValueError, message):
                    rules.validate(data)

    def test_drafts_review_related_independent_and_legacy(self):
        stage = stage_fixture()
        a, b = stage['cards']
        # Put the blocked task first; it must not monopolize the queue.
        jobs = {b['id']: events(b, stage), a['id']: events(a, stage, 'review')}
        self.assertIsNone(rules.select_task(jobs, stage))
        stage.update(state='authorized', tasks=[a['id'], b['id']], authorized_version=stage['version'])
        self.assertIsNone(rules.select_task(jobs, stage))
        independent = {'seq': 1, 'state': 'queued', 'data': {'goal': 'old', 'acceptance': 'ok', 'related': [a['id']]}}
        jobs['legacy'] = [independent]
        self.assertEqual(rules.select_task(jobs, stage)[0], 'legacy')
        independent['state'] = 'answered'
        self.assertEqual(rules.select_task(jobs, stage)[0], 'legacy')
        jobs[b['id']][-1]['state'] = 'answered'
        for state in ('review', 'waiting', 'failed', 'closed'):
            jobs[a['id']][-1]['state'] = state
            self.assertEqual(rules.select_task(jobs, stage)[0], 'legacy')
        jobs[b['id']][0]['data'].update(blockedBy=[], related=[a['id']])
        self.assertEqual(rules.select_task(jobs, stage)[0], b['id'])
        jobs[b['id']][0]['data']['blockedBy'] = [a['id']]
        jobs[a['id']][-1]['state'] = 'activated'
        self.assertEqual(rules.select_task(jobs, stage)[0], b['id'])
        self.assertFalse(rules.ready(stage, jobs))
        jobs[b['id']][-1]['state'] = 'closed'
        self.assertFalse(rules.ready(stage, jobs))
        jobs[b['id']][-1]['state'] = 'activated'
        self.assertTrue(rules.ready(stage, jobs))

    def test_single_coordinator_version_gate_and_draft_no_worker(self):
        stage = stage_fixture()
        jobs = {c['id']: events(c, stage) for c in stage['cards']}
        with patch.object(seed.store, 'journal', return_value=jobs), patch.object(seed.store, 'stage_get', return_value=stage), \
             patch.object(engine, 'git', return_value='boot'), patch.object(engine, 'advance') as advance:
            self.assertFalse(seed.coordinate_once('boot'))
            advance.assert_not_called()
            stage.update(state='authorized', tasks=list(jobs), authorized_version=2)
            self.assertFalse(seed.coordinate_once('old-boot'))
            advance.assert_not_called()
            self.assertTrue(seed.coordinate_once('boot'))
            self.assertEqual(advance.call_args.args[0], stage['cards'][0]['id'])


class StageFormTests(unittest.TestCase):
    def test_edit_then_explicit_confirm_only_saved_version(self):
        stage = stage_fixture()
        params = params_for(stage)
        params['goal_'+stage['cards'][0]['id']] = ['edited <requirement>']
        params['confirm'] = ['yes']
        with patch.object(seed.store, 'stage_get', return_value=stage), \
             patch.object(seed.store, 'stage_save') as save, patch.object(seed.store, 'stage_authorize') as authorize:
            seed.action(params, 'boot')
            authorize.assert_not_called()
            edited = save.call_args.args[2]
            self.assertEqual(edited[0]['goal'], 'edited <requirement>')
            stage.update(cards=edited, version=3)
            params['version'] = ['3']
            seed.action(params, 'boot')
            authorize.assert_called_once_with(stage['id'], 3)

    def test_dependency_errors_and_stale_version_preserve_form(self):
        stage = stage_fixture()
        for modify, message in [
            (lambda p: p.update({"acceptance_"+stage['cards'][0]['id']: ['']}), '验收条件'),
            (lambda p: p.update({'dep_'+stage['cards'][0]['id']: ['missing']}), '缺失依赖'),
            (lambda p: p.update({'dep_'+stage['cards'][0]['id']: [stage['cards'][0]['id']]}), '自身'),
            (lambda p: p.update({'dep_'+stage['cards'][0]['id']: [stage['cards'][1]['id']]}), '循环依赖'),
            (lambda p: p.update({'version': ['1']}), '版本已更新')]:
            params = params_for(stage)
            params['goal_'+stage['cards'][0]['id']] = ['<keep input>']
            modify(params)
            with self.subTest(message=message), patch.object(seed.store, 'stage_get', return_value=stage), \
                 patch.object(seed.store, 'stage_save') as save:
                with self.assertRaises((ValueError, RuntimeError)) as caught:
                    seed.action(params, 'boot')
                self.assertIn(message, str(caught.exception))
                save.assert_not_called()
                rendered = web.render(stage, {}, seed.form, seed.field, params, str(caught.exception))
                self.assertIn('&lt;keep input&gt;', rendered)
                self.assertIn('输入已保留', rendered)
                for c in web.cards_from(params):
                    for dep in c['blockedBy']:
                        self.assertIn('value="'+web.esc(dep)+'" checked', rendered)
                self.assertNotIn('<keep input>', rendered)
                self.assertIn('name="version" value="'+params['version'][0]+'"', rendered)

    def test_stage_acceptance_is_separate(self):
        stage = stage_fixture()
        stage.update(state='authorized', authorized_version=2, tasks=[c['id'] for c in stage['cards']])
        jobs = {c['id']: events(c, stage, 'activated') for c in stage['cards']}
        rendered = web.render(stage, jobs, seed.form, seed.field)
        self.assertIn('子任务已启用，待阶段验收', rendered)
        self.assertNotIn('阶段验收已通过', rendered)
        stale = {'version': ['1'], 'note': ['<保留验收反馈>'], 'passed': ['no']}
        error_page = web.render(stage, jobs, seed.form, seed.field, stale, '阶段版本已更新')
        self.assertIn('&lt;保留验收反馈&gt;', error_page)
        self.assertIn('value="no" selected', error_page)
        jobs[stage['tasks'][0]][-1]['state'] = 'closed'
        self.assertNotIn('name="action" value="stage_accept"', web.render(stage, jobs, seed.form, seed.field))


class StageModelTests(unittest.TestCase):
    def test_real_adapter_command_is_read_only_and_structured(self):
        def execute(args, cwd, prompt, logs, phase, **kwargs):
            self.assertEqual(args[args.index('-s')+1], 'read-only')
            schema = json.loads(Path(args[args.index('--output-schema')+1]).read_text())
            self.assertEqual(schema, engine.STAGE_SCHEMA)
            Path(args[args.index('--output-last-message')+1]).write_text('{"tasks": []}')
        with TemporaryDirectory() as tmp, patch.object(engine.seed_run, 'execute', side_effect=execute):
            self.assertEqual(engine.model(Path(tmp), 'plan', False, engine.STAGE_SCHEMA), {'tasks': []})

    def test_snapshot_planning_persists_source_and_never_enqueues(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine.git(root, 'init', '-b', 'main')
            (root/'source.txt').write_text('committed')
            engine.git(root, 'add', '.')
            engine.git(root, '-c', 'user.name=Test', '-c', 'user.email=t@local', 'commit', '-m', 'source')
            sha = engine.git(root, 'rev-parse', 'HEAD')
            (root/'source.txt').write_text('uncommitted')
            stage = stage_fixture()
            stage.update(state='pending', version=0)
            result = {'tasks': [dict(c, id=str(i), blockedBy=[] if i == 0 else ['0']) for i,c in enumerate(stage['cards'])]}
            def model(path, prompt, writable, schema):
                self.assertFalse(writable)
                self.assertEqual((path/'source.txt').read_text(), 'committed')
                self.assertIn(sha, prompt)
                return result
            for output, final in [(model, 'draft'), (lambda *args: {'tasks': []}, 'failed'),
                                  (lambda *args: (_ for _ in ()).throw(RuntimeError('interrupted')), 'failed')]:
                with patch.object(engine, 'ROOT', root), patch.object(engine, 'model', side_effect=output), \
                     patch.object(engine.store, 'stage_update') as update, patch.object(engine.store, 'create') as create, \
                     patch.object(engine.store, 'append') as append, patch.object(engine, 'advance') as advance:
                    engine.plan_stage(stage)
                    self.assertEqual(update.call_args.args[2], final)
                    if final == 'draft':
                        saved = update.call_args.args[3]
                        rules.validate(saved['cards'])
                        self.assertEqual(saved['source'], sha)
                        self.assertEqual(saved['raw_plan'], result)
                    create.assert_not_called()
                    append.assert_not_called()
                    advance.assert_not_called()


# Inherit the existing real temporary Git/worktree harness, not the production repository.
class StageGitLifecycle(unittest.TestCase):
    setUp = test_seed.SeedTests.setUp
    record = test_seed.SeedTests.record
    advance = test_seed.SeedTests.advance

    def test_upstream_activation_restart_and_new_base(self):
        stage = stage_fixture()
        a, b = stage['cards']
        self.task = a['id']
        stage.update(state='authorized', tasks=[a['id'], b['id']], authorized_version=2)
        self.events = events(a, stage)
        jobs = {a['id']: self.events, b['id']: events(b, stage)}
        with patch.object(engine.store, 'journal', side_effect=lambda: copy.deepcopy(jobs)), \
             patch.object(engine.store, 'stage_get', return_value=stage):
            def worker_a(path, prompt):
                self.assertIn('阶段目标', prompt)
                (path/'feature.py').write_text('value = 2\n')
                return dict(status='done', summary='upstream delivered')
            self.advance(worker_a)
            self.assertEqual(self.events[-1]['state'], 'review')
            self.assertIsNone(rules.select_task(jobs, stage))
            data = rules.merged(self.events)
            engine.activate(data)
            self.record(self.task, self.events[-1]['seq'], 'activated', {'candidate': data['candidate']})
            with patch.object(seed.store, 'journal', return_value=jobs), patch.object(seed.store, 'stage_get', return_value=stage):
                self.assertFalse(seed.coordinate_once(self.base))
            new_base = engine.git(self.root, 'rev-parse', 'HEAD')
            self.task, self.events = b['id'], jobs[b['id']]
            def worker_b(path, prompt):
                self.assertIn(data['candidate'], prompt)
                self.assertIn('upstream delivered', prompt)
                self.assertEqual(engine.git(path, 'rev-parse', 'HEAD'), new_base)
                (path/'feature.py').write_text('value = 3\n')
                return dict(status='done', summary='downstream delivered')
            old_base = self.base
            self.base = new_base
            self.advance(worker_b)
            self.assertNotEqual(old_base, new_base)
            self.assertEqual(self.events[-1]['state'], 'review', self.events[-1])


class StageHTTPTests(unittest.TestCase):
    def test_post_validation_error_returns_editable_escaped_inputs(self):
        import threading
        from urllib.error import HTTPError
        from urllib.parse import urlencode
        from urllib.request import Request, urlopen
        stage = stage_fixture()
        params = params_for(stage)
        params.update(token=[seed.TOKEN], **{'acceptance_'+stage['cards'][0]['id']: [''],
                                             'goal_'+stage['cards'][0]['id']: ['<keep>']})
        with patch.object(store := seed.store, 'stage_get', return_value=stage), \
             patch.object(store, 'journal', return_value={}), patch.object(engine, 'git', return_value='boot'):
            server = seed.ThreadingHTTPServer(('127.0.0.1', 0), seed.Console)
            server.boot = 'boot'
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(f'http://127.0.0.1:{server.server_port}/action', urlencode(params, doseq=True).encode())
                with self.assertRaises(HTTPError) as caught:
                    urlopen(request, timeout=5)
                self.assertEqual(caught.exception.code, 409)
                page = caught.exception.read().decode()
                self.assertIn('验收条件为空', page)
                self.assertIn('&lt;keep&gt;', page)
                self.assertIn('保存草稿', page)
                self.assertIn('name="version" value="2"', page)
                bad = dict(params, token=['incorrect'])
                with self.assertRaises(HTTPError) as rejected:
                    urlopen(Request(request.full_url, urlencode(bad, doseq=True).encode()), timeout=5)
                self.assertEqual(rejected.exception.code, 403)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

"""Project material form contracts and isolated HTTP/PostgreSQL acceptance tests."""
import copy
from html.parser import HTMLParser
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from uuid import uuid4

import seed
import seed_material_web as web
import seed_project as ctx
import seed_store as store
import seed_ui
from test_seed_project import RepositoryCase

ATTACK = '</textarea></pre></script><script>window.seedXss=1</script><svg onload="window.seedXss=2">[click](javascript:alert(1)) ![x](data:text/html,bad)'


class Form(HTMLParser):
    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.params, self.active, self.textarea = {}, False, None
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'form':
            self.active = True
        if not self.active:
            return
        if tag == 'input' and a.get('name') and (a.get('type') != 'checkbox' or 'checked' in a):
            self.params[a['name']] = [a.get('value', '')]
        if tag == 'textarea':
            self.textarea = a['name']
            self.params[self.textarea] = ['']

    def handle_data(self, data):
        if self.textarea:
            self.params[self.textarea][0] += data

    def handle_endtag(self, tag):
        if tag == 'textarea' and self.textarea:
            # HTML's textarea leading newline rule.
            self.params[self.textarea][0] = self.params[self.textarea][0].removeprefix('\n')
            self.textarea = None
        if tag == 'form':
            self.active = False


class MemoryProject:
    def __init__(self):
        self.commit = 'a' * 40
        self.draft = None
        self.pubs = []
        self.original = {k: None for k in web.EDITABLE}
        self.original.update(readme='registered source\n', design='design proposal\n')

    def read_published(self, commit=None):
        return dict(commit=self.commit, files=[dict(id=d['id'], content=self.original[d['id']], exists=self.original[d['id']] is not None) for d in ctx.list_documents()])

    def get_draft(self): return copy.deepcopy(self.draft)
    def list_drafts(self): return [self.get_draft()] if self.draft else []
    def list_publications(self): return copy.deepcopy(self.pubs)
    def git(self, *args): return self.commit

    def save_draft(self, version, contents, base):
        if version != (self.draft['version'] if self.draft else 0):
            raise ctx.Conflict('草稿版本已变化')
        self.draft = dict(version=version+1, contents=contents, base_commit=base, source_commit=self.commit)
        return self.get_draft()


class MaterialFormTests(unittest.TestCase):
    def setUp(self): self.p = MemoryProject()

    def params(self): return Form(web.render(self.p, 'token')).params

    def test_seven_editors_preview_save_empty_and_exact_newlines(self):
        params = self.params()
        for key in web.SECTIONS:
            self.assertIn('section_' + key, params)
            params['section_' + key] = ['\n内容 ' + key + '\n\n']
        for key in ('shared', 'main', 'worker'):
            params['body_' + key] = ['' if key == 'main' else ATTACK + '\n\n']
        for key in web.EDITABLE:
            params['exists_' + key] = ['yes']
        params['action'] = ['material_preview']
        _, bodies = web.act(self.p, params)
        self.assertIsNone(self.p.draft)
        page = web.render(self.p, 'token', params, edited=bodies)
        self.assertNotIn('<script>', page)
        self.assertNotIn('<svg ', page)
        self.assertNotIn('href="javascript:', page)
        self.assertNotIn('src="data:', page)
        self.assertIn('&lt;script&gt;', page)
        params['action'] = ['material_save']
        web.act(self.p, params)
        self.assertEqual(self.p.draft['contents'], bodies)
        self.assertEqual(bodies['main'], '')
        self.assertTrue(bodies['worker'].endswith('\n\n'))
        self.assertEqual(self.p.commit, 'a'*40)
        self.assertEqual(web.contents_from(self.params()), bodies)

    def test_browser_crlf_transport_preserves_existing_lf_crlf_and_trailing_newlines(self):
        for newline in ('\n', '\r\n'):
            self.p.original['context'] = ctx.TEMPLATE.replace('\n', newline)
            self.p.original['shared'] = 'exact' + newline + newline
            form = self.params()
            browser = {k: [v.replace('\r\n', '\n').replace('\n', '\r\n') for v in values] for k, values in form.items()}
            bodies = web.contents_from(browser)
            self.assertEqual(bodies['context'], self.p.original['context'])
            self.assertEqual(bodies['shared'], self.p.original['shared'])
            browser['section_goal'] = ['new\r\n\r\n']
            self.assertIn('new' + newline + newline, web.contents_from(browser)['context'])
            ctx.Project._validate_context(None, bodies)

    def test_reject_paths_readonly_unknown_ids_duplicate_and_missing_bodies(self):
        for key in ('body_readme', 'body_design', 'body_../AGENTS.md', 'body_/tmp/a', 'path', 'ref', 'section_unknown'):
            params = self.params()
            params[key] = ['bad']
            with self.subTest(key=key), self.assertRaises(ctx.PathRejected):
                web.contents_from(params)
        for values in (None, ['a', 'b']):
            params = self.params()
            if values is None: del params['body_shared']
            else: params['body_shared'] = values
            with self.assertRaises(ValueError): web.contents_from(params)

    def test_strict_sections_preserve_unedited_bytes_and_offer_raw_repair(self):
        body = '# exact prefix\n\n' + ''.join('## ' + label + '\n\n' + key + '\n\n' for key, label in web.SECTIONS.items())
        self.p.original['context'] = body
        params = self.params()
        self.assertEqual(web.contents_from(params)['context'], body)
        params['section_goal'] = ['new\n']
        self.assertEqual(web.contents_from(params)['context'], body.replace('\ngoal\n\n', 'new\n', 1))
        self.p.original['context'] = body + '## 长期目标\nextra\n'
        page = web.render(self.p, 'token')
        self.assertIn('原文不能无损拆分', page)
        self.assertIn('name="body_context"', page)
        self.assertEqual(web.contents_from(Form(page).params)['context'], self.p.original['context'])

    def test_import_requires_literal_registered_fragment_no_decision_inference(self):
        params = self.params()
        params.update(action=['material_import'], import_source=['design'], import_target=['architecture'], import_commit=[self.p.commit], import_fragment=['design proposal'])
        notice, bodies = web.act(self.p, params)
        self.assertIn(self.p.commit, notice)
        self.assertIn('design proposal', bodies['context'])
        self.assertIsNone(self.p.get_draft())
        self.assertEqual(web.split_context(bodies['context'])[1]['decisions'][1], '')
        params['import_target'] = ['decisions']
        with self.assertRaises(ctx.PathRejected): web.act(self.p, params)
        params['import_target'] = ['goal']
        params['import_fragment'] = ['not in original']
        with self.assertRaises(ValueError): web.act(self.p, params)

    def test_stale_save_retains_input_and_exposes_latest_bundle(self):
        a, b = self.params(), self.params()
        for form, text in ((a, 'one'), (b, ATTACK)):
            form.update(action=['material_save'], exists_shared=['yes'], body_shared=[text])
        web.act(self.p, a)
        with self.assertRaises(ctx.Conflict): web.act(self.p, b)
        page = web.render(self.p, 'token', b, error='conflict')
        self.assertIn('服务器最新草稿', page)
        self.assertIn('&lt;script&gt;', page)
        self.assertNotIn('<script>', page)
        self.assertEqual(Form(page).params['body_shared'], [ATTACK])
        self.assertEqual(self.p.draft['contents']['shared'], 'one')

    def test_dirty_noscript_prepare_and_activate_refused_before_domain_mutation(self):
        form = self.params()
        form.update(action=['material_save'], exists_shared=['yes'], body_shared=['saved'])
        web.act(self.p, form)
        saved = self.params()
        for kind in ('material_prepare', 'material_activate'):
            dirty = copy.deepcopy(saved)
            dirty.update(action=[kind], body_shared=['unsaved'])
            with self.subTest(kind=kind), self.assertRaises(ctx.Conflict): web.act(self.p, dirty)
        self.assertEqual(self.p.list_publications(), [])

    def test_revision_covers_draft_publication_state_and_evidence(self):
        initial = web.signature(self.p)
        form = self.params()
        form['action'] = ['material_save']
        web.act(self.p, form)
        draft = web.signature(self.p)
        self.assertNotEqual(initial, draft)
        self.p.pubs = [dict(state='preparing', evidence=None)]
        before = web.signature(self.p)
        self.p.pubs[0].update(state='failed', error=ATTACK)
        self.assertNotEqual(before, web.signature(self.p))
        self.assertNotEqual(seed_ui.revision({}, None, 'main', materials_signature=draft), seed_ui.revision({}, None, 'main', materials_signature=initial))

    def test_service_revision_uses_full_domain_and_engine_root(self):
        with patch.object(ctx, 'Project', return_value=self.p) as project:
            expected = seed_ui.revision({}, None, 'main', 'boot', web.signature(self.p))
            self.assertEqual(seed.revision({}, None, 'main', 'boot'), expected)
            project.assert_called_once_with(root=seed.engine.ROOT)

    def test_service_revision_does_not_hide_complete_domain_failures(self):
        with patch.object(ctx, 'Project', side_effect=RuntimeError('database unavailable')):
            with self.assertRaisesRegex(RuntimeError, 'database unavailable'):
                seed.revision({}, None, 'main', 'boot')

    @unittest.skipUnless(shutil.which('node'), 'requires Node.js for JavaScript contract tests')
    def test_javascript_dirty_refresh_and_action_return_area(self):
        # Run the shipped JS with DOM doubles. This is not a real browser test.
        script = r'''
const assert = require('node:assert/strict');
const vm = require('node:vm');
const source = require('node:fs').readFileSync(process.argv[1], 'utf8');
async function check(area, unsaved = false) {
    const values = {area, section_goal: 'goal', section_architecture: 'arch',
        section_constraints: 'rules', section_decisions: '', body_shared: 'shared',
        body_main: 'main', body_worker: 'worker', exists_context: 'yes',
        exists_shared: 'yes', exists_main: 'yes', exists_worker: 'yes'};
    const listeners = {}, buttons = [{disabled: false}, {disabled: false}];
    const notices = {'material-dirty': {hidden: true}, 'material-refresh': {hidden: true}};
    const form = {addEventListener: (kind, fn) => listeners[kind] = fn,
        querySelector: selector => ({get value() { return area; },
            set checked(value) { if (value) values[selector.match(/name="([^"]+)"/)[1]] = 'yes'; }})};
    let returned, interval, reloads = 0;
    const document = {body: {dataset: {revision: 'old'}},
        addEventListener() {}, getElementById: id => notices[id] || null,
        querySelector: selector => selector === '[data-material-editor]' ? form :
            selector === '.materials' ? {dataset: {unsaved: String(unsaved)}} : null,
        querySelectorAll: selector => selector === '[data-publish]' ? buttons : []};
    class FormData {
        constructor() { this.values = {...values}; }
        keys() { return Object.keys(this.values).values(); }
        get(key) { return this.values[key] ?? null; }
    }
    vm.runInNewContext(source, {document, FormData, encodeURIComponent,
        location: {pathname: '/action', hash: '', reload: () => reloads++},
        history: {replaceState: (_, __, path) => returned = path},
        sessionStorage: {getItem: () => null},
        setInterval: fn => interval = fn,
        fetch: async () => ({ok: true, json: async () => 'new'})});
    assert.equal(returned, '/materials?area=' + area);
    assert.equal(buttons.every(b => b.disabled), unsaved);
    for (const key of ['section_goal', 'section_architecture', 'section_constraints',
                       'section_decisions', 'body_shared', 'body_main', 'body_worker']) {
        const before = values[key];
        values[key] += ' unsaved';
        listeners.input({target: {name: key}});
        assert(buttons.every(b => b.disabled), key);
        assert.equal(notices['material-dirty'].hidden, false);
        await interval();
        assert.equal(reloads, 0);
        assert.equal(notices['material-refresh'].hidden, false);
        values[key] = before;
        listeners.input({target: {name: key}});
        assert.equal(buttons.every(b => b.disabled), unsaved);
    }
    delete values.exists_worker;
    listeners.change();
    assert(buttons.every(b => b.disabled));
}
(async () => {
    await check('context'); await check('agents'); await check('agents', true);
})().catch(error => { console.error(error); process.exitCode = 1; });
'''
        result = subprocess.run(['node', '-e', script, str(Path(seed.__file__).parent / 'static' / 'seed_ui.js')],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


@unittest.skipUnless(os.environ.get('SEED_TEST_PG') == '1', 'requires local PostgreSQL')
class MaterialHTTPPostgresTests(RepositoryCase):
    def setUp(self):
        super().setUp()
        self.schema = 'seed_web_' + uuid4().hex
        store.sql('CREATE SCHEMA ' + self.schema)
        env = patch.dict(os.environ, PGOPTIONS='-c search_path=' + self.schema)
        env.start()
        self.addCleanup(lambda: (env.stop(), store.sql('DROP SCHEMA ' + self.schema + ' CASCADE')))
        store.init()
        factory = ctx.Project
        self.p = factory(self.root)
        for obj, key, value in ((seed.engine, 'ROOT', self.root), (seed.ctx, 'Project', lambda root=None: factory(self.root)), (seed.store, 'journal', lambda: {}), (seed.store, 'stage_get', lambda: None)):
            mock = patch.object(obj, key, value)
            mock.start()
            self.addCleanup(mock.stop)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), seed.Console)
        self.server.boot = self.base
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.url = 'http://127.0.0.1:' + str(self.server.server_port)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)

    def get(self, path='/materials?area=context'):
        with urlopen(self.url + path, timeout=30) as response:
            return response.read().decode()

    def post(self, params, kind):
        params = copy.deepcopy(params)
        params['action'] = [kind]
        try:
            with urlopen(Request(self.url + '/action', data=urlencode(params, doseq=True).encode()), timeout=60) as response:
                return response.status, response.read().decode()
        except HTTPError as error:
            return error.code, error.read().decode()

    def initial(self):
        params = Form(self.get()).params
        params['exists_context'] = ['yes']
        for key in ('shared', 'main', 'worker'):
            params['exists_' + key] = ['yes']
            params['body_' + key] = [key + '\n\n']
        return params

    def test_http_full_bundle_preview_save_prepare_confirm_history_and_reload(self):
        initial = self.get()
        for path in ('PROJECT-CONTEXT.md', 'AGENTS.md', 'agents/main.md', 'agents/worker.md', 'README.md', 'PROJECT-DESIGN.md'):
            self.assertIn(path, initial)
        self.assertIn('文件不存在', initial)
        self.assertIn('Agent 指令', self.get('/materials?area=agents'))
        params = self.initial()
        for key in web.SECTIONS:
            params['section_' + key] = ['\n确认来源：HTTP 验收\n' if key == 'decisions' else '\n' + key + '\n\n']
        status, page = self.post(params, 'material_preview')
        self.assertEqual(status, 200)
        self.assertIsNone(self.p.get_draft())
        self.assertEqual(self.post(params, 'material_save')[0], 200)
        self.assertEqual(self.p.read_published()['commit'], self.base)
        saved = Form(self.get()).params
        self.assertEqual(web.contents_from(saved), self.p.get_draft()['contents'])
        self.assertEqual(self.post(saved, 'material_prepare')[0], 200)
        ready = self.p.list_publications()[0]
        self.assertEqual(ready['state'], 'ready', ready)
        confirm = Form(self.get()).params
        self.assertEqual(self.post(confirm, 'material_activate')[0], 409)
        dirty = copy.deepcopy(confirm)
        dirty.update(confirmed=['yes'], confirmation=['HTTP human'], body_worker=['not saved'])
        code, page = self.post(dirty, 'material_activate')
        self.assertEqual(code, 409)
        self.assertIn('not saved', page)
        self.assertEqual(self.p.read_published()['commit'], self.base)
        confirm.update(confirmed=['yes'], confirmation=['HTTP human'])
        self.assertEqual(self.post(confirm, 'material_activate')[0], 200)
        self.assertEqual(self.p.read_published()['commit'], ready['candidate_commit'])
        self.assertIn('activated', self.get())
        self.p.recover_publications()
        self.assertEqual(len(self.p.list_drafts()), 1)
        self.assertEqual(self.p.publication_diff(ready['request_id']).count('diff --git'), 4)

    def test_two_stale_pages_save_prepare_and_activate_preserve_inputs(self):
        a = self.initial()
        b = copy.deepcopy(a)
        self.assertEqual(self.post(a, 'material_save')[0], 200)
        b['body_worker'] = ['stale human input ' + ATTACK]
        code, page = self.post(b, 'material_save')
        self.assertEqual(code, 409)
        self.assertEqual(Form(page).params['body_worker'], b['body_worker'])
        self.assertIn('服务器最新草稿', page)
        self.assertNotIn('<svg onload', page)
        self.assertEqual(self.post(a, 'material_prepare')[0], 409)
        saved = Form(self.get()).params
        self.assertEqual(self.post(saved, 'material_prepare')[0], 200)
        confirm = Form(self.get()).params
        changed = copy.deepcopy(confirm)
        changed['body_main'] = ['newer draft\n']
        self.assertEqual(self.post(changed, 'material_save')[0], 200)
        confirm.update(confirmed=['yes'], confirmation=['old approval'])
        code, page = self.post(confirm, 'material_activate')
        self.assertEqual(code, 409)
        self.assertIn('newer draft', page)
        self.assertEqual(self.p.read_published()['commit'], self.base)

    def test_failed_ready_records_survive_new_process_without_activation(self):
        self.post(self.initial(), 'material_save')
        saved = Form(self.get()).params
        # Domain verification failure must remain visible, never marked successful.
        with patch.object(seed.engine, 'verify', side_effect=RuntimeError('validation ' + ATTACK)):
            code, page = self.post(saved, 'material_prepare')
        self.assertEqual(code, 409)
        self.assertIn('failed', page)
        self.assertIn('&lt;script&gt;', page)
        saved = Form(self.get()).params
        saved['request_id'] = [str(uuid4())]
        self.assertEqual(self.post(saved, 'material_prepare')[0], 200)
        import subprocess, sys
        code = 'import json,seed_project; p=seed_project.Project(' + repr(str(self.root)) + '); p.recover_publications(); print(json.dumps([p.get_draft(),p.list_publications()]))'
        data = json.loads(subprocess.check_output([sys.executable, '-B', '-c', code], text=True))
        self.assertEqual(data[0]['version'], 1)
        self.assertEqual({p['state'] for p in data[1]}, {'failed', 'ready'})
        self.assertEqual(self.p.git('rev-parse', 'HEAD'), self.base)
        self.assertIn('failed', self.get())
        self.assertIn('ready', self.get())

    def test_forged_candidate_paths_xss_and_revision_consistency(self):
        params = self.initial()
        params['body_shared'] = [ATTACK + '\n\n']
        for key in ('body_../../tmp/x', 'body_readme', 'path'):
            bad = copy.deepcopy(params)
            bad[key] = ['bad']
            code, page = self.post(bad, 'material_save')
            self.assertEqual(code, 409)
            self.assertIn('提交原始输入', page)
        self.assertIsNone(self.p.get_draft())
        self.post(params, 'material_save')
        saved = Form(self.get()).params
        self.assertEqual(self.post(saved, 'material_prepare')[0], 200)
        confirm = Form(self.get()).params
        confirm.update(candidate_commit=['f'*40], confirmed=['yes'], confirmation=['human'])
        self.assertEqual(self.post(confirm, 'material_activate')[0], 409)
        page = self.get()
        self.assertNotIn(ATTACK, page)
        self.assertIn('&lt;script&gt;', page)
        import re
        revision = re.search('data-revision="([^"]+)"', page)[1]
        self.assertEqual(revision, json.loads(self.get('/api/revision')))
        home = self.get('/')
        self.assertIn('新建独立任务', home)
        self.assertIn('/materials?area=context', home)
        self.assertIn(revision, home)

    def test_http_parallel_old_pages_only_one_save_and_one_activation_win(self):
        from concurrent.futures import ThreadPoolExecutor
        a = self.initial()
        b = copy.deepcopy(a)
        b['body_worker'] = ['second page input\n']
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda form: self.post(form, 'material_save'), (a, b)))
        self.assertEqual(sorted(code for code, _ in results), [200, 409])
        loser = next(page for code, page in results if code == 409)
        self.assertIn('服务器最新草稿', loser)
        self.assertEqual(self.p.get_draft()['version'], 1)
        first = Form(self.get()).params
        second = copy.deepcopy(first)
        second['request_id'] = [str(uuid4())]
        with ThreadPoolExecutor(max_workers=2) as pool:
            prepared = list(pool.map(lambda form: self.post(form, 'material_prepare'), (first, second)))
        self.assertEqual([code for code, _ in prepared], [200, 200])
        publications = self.p.list_publications()
        self.assertEqual(len(publications), 2)
        confirmations = []
        for publication in publications:
            form = copy.deepcopy(first)
            form.update(request_id=[publication['request_id']], candidate_commit=[publication['candidate_commit']],
                        confirmed=['yes'], confirmation=['parallel HTTP human'])
            confirmations.append(form)
        with ThreadPoolExecutor(max_workers=2) as pool:
            enabled = list(pool.map(lambda form: self.post(form, 'material_activate'), confirmations))
        self.assertEqual(sorted(code for code, _ in enabled), [200, 409])
        self.assertEqual(sum(p['state'] == 'activated' for p in self.p.list_publications()), 1)
        self.assertEqual(int(store.sql('SELECT count(*) FROM seed_project_events')), 1)

    def test_dirty_main_and_forward_baseline_conflicts_remain_visible(self):
        self.post(self.initial(), 'material_save')
        saved = Form(self.get()).params
        self.post(saved, 'material_prepare')
        confirm = Form(self.get()).params
        confirm.update(confirmed=['yes'], confirmation=['human approval'])
        (self.root / 'README.md').write_text('uncommitted input\n')
        code, page = self.post(confirm, 'material_activate')
        self.assertEqual(code, 409)
        self.assertIn('main 工作区不干净', page)
        self.assertIn('ready', page)
        self.commit('independent main advance')
        code, page = self.post(confirm, 'material_activate')
        self.assertEqual(code, 409)
        self.assertIn('main 已前移', page)
        self.assertIn('显式采用当前 main', page)
        self.assertEqual(self.p.read_published()['commit'], self.base)
        self.assertEqual(self.p.get_draft()['version'], 1)

    def test_http_import_registered_source_empty_body_and_browser_newlines(self):
        form = self.initial()
        form.update(import_source=['readme'], import_target=['architecture'], import_commit=[self.base],
                    import_fragment=["source ' text"], body_main=[''])
        # Simulate browser application/x-www-form-urlencoded newline transport.
        form = {k: [v.replace('\n', '\r\n') for v in values] for k, values in form.items()}
        code, page = self.post(form, 'material_import')
        self.assertEqual(code, 200)
        self.assertIsNone(self.p.get_draft())
        imported = Form(page).params
        self.assertIn('README.md @ ' + self.base, imported['section_architecture'][0])
        self.assertEqual(imported['section_decisions'], [''])
        self.assertIn('data-unsaved="true"', page)
        self.assertEqual(self.post(imported, 'material_save')[0], 200)
        saved = self.p.get_draft()
        self.assertEqual(saved['contents']['main'], '')
        self.assertTrue(saved['contents']['worker'].endswith('\n\n'))
        self.assertEqual(self.p.read_published()['commit'], self.base)

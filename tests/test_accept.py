"""人工验收（后端）：服务「验收时额外带的环境变量」的保存和占位符、这次怎么启动（plan / launch：保密值只给应用、同意、准备步骤）、
项目副本（git clone --local、去掉 origin、盖上主目录的 .relayforge/、沿用和重新复制）、清掉临时目录、启动失败的「让 AI 来处理」、
逐条验收的记录和交回（退回进行中、日志打码、完成记录）、只由用户触发的命令要应用的标记。"""
import io
import json
import os
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'backend'))
import rf  # noqa: E402
from relayforge.requirements import accept as A  # noqa: E402
from relayforge.envs import store as E  # noqa: E402
from relayforge.envs import edits as EE  # noqa: E402
from relayforge.envs import prep as EP  # noqa: E402
from relayforge.envs import views as EV  # noqa: E402
from relayforge.envs import handoff as EH  # noqa: E402
from relayforge.requirements import testing as G  # noqa: E402
from tests.test_testsets import Base, git  # noqa: E402
from tests.app_fixture import FakeApp  # noqa: E402

SECRET = 'hunter2-very-secret'
DEV, TESTER = 'dev-accept-1', 'test-accept-1'


def trusted(on=True):
    return mock.patch('relayforge.core.trust.app_trusted', return_value=on)


class AcceptBase(Base):
    def setUp(self):
        super().setUp()
        (self.root / 'app').mkdir()
        (self.root / 'app' / 'serve.py').write_text('print("hi")\n', encoding='utf-8')
        git(self.root, 'add', '-A')
        git(self.root, 'commit', '-qm', 'app')
        self.env = EE.env_new(self.root, '本机')['id']
        EE.env_save(self.root, self.env, {'name': '本机', 'services': [
            {'name': '应用', 'command': 'python3 serve.py', 'dir': 'app', 'port': None,
             'accept': [{'name': 'APP_DATA', 'value': '{{scratch}}/userdata'}, {'name': 'APP_PROJECT', 'value': '{{project_copy}}'},
                        {'name': 'APP_TAG', 'value': '验收用 · 需求 {{req}}'}, {'name': 'APP_CODE', 'value': '{{worktree}}'}, {'name': 'APP_HOME', 'value': '{{project}}'}]},
            {'name': '后端', 'command': 'python3 -m http.server', 'port': 8765}],
            'vars': [{'name': 'MODE', 'value': 'local'}, {'name': 'DB_PASSWORD', 'value': SECRET, 'secret': True}],
            'prep': [{'note': '装依赖', 'command': 'echo prep >> prep.log', 'when': 'always'}]})

    def requirement(self, features='RF-101 加法：改一改', checks=('加法算对', '界面上显示结果')):
        req = rf.req_new(self.root, '改加法')
        path = self.root / req['file']
        meta, sections = rf.read_doc(path)
        sections['要改的功能点'] = [f'- {features}〔S-x#1〕']
        sections['验收条件'] = [f'- {c}〔S-x#1〕' for c in checks]
        rf.write_doc(path, meta, sections)
        rf.req_start(self.root, req['id'])
        return req['id'], Path(req['worktree'])

    def cli(self, *args, stdin=None, cwd=None):
        out, err = io.StringIO(), io.StringIO()
        old = os.getcwd()
        os.chdir(cwd or self.root)
        try:
            with redirect_stdout(out), redirect_stderr(err), mock.patch('sys.stdin', io.StringIO(stdin or '')):
                code = rf.main(list(args))
        finally:
            os.chdir(old)
        return code, out.getvalue(), err.getvalue()


class SettingsTests(AcceptBase):
    def test_accept_vars_are_saved_with_the_service_and_checked(self):
        env = EV.env_show(self.root, self.env)
        app = env['services'][0]
        self.assertEqual([a['name'] for a in app['accept']], ['APP_DATA', 'APP_PROJECT', 'APP_TAG', 'APP_CODE', 'APP_HOME'])
        self.assertNotIn('accept', env['services'][1])  # 没有就不写，老的服务原样
        saved = json.loads((self.root / '.relayforge' / 'envs' / f'{self.env}.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['services'][0]['accept'][0], {'name': 'APP_DATA', 'value': '{{scratch}}/userdata'})
        for bad, words in (([{'name': '1X', 'value': 'a'}], '字母'), ([{'name': 'API_TOKEN', 'value': 'a'}], '保密'), ([{'name': 'A', 'value': '1'}, {'name': 'A', 'value': '2'}], '重名')):
            with self.assertRaises(ValueError) as caught:
                EE.env_save(self.root, self.env, {'name': '本机', 'services': [{'name': '应用', 'command': 'x', 'accept': bad}]})
            self.assertIn(words, str(caught.exception))

    def test_auto_restart_switch_defaults_on_is_only_written_when_off_and_does_not_need_new_consent(self):
        env = EV.env_show(self.root, self.env)
        self.assertNotIn('auto_restart', env['services'][0])  # 老数据、没写：按开
        plan = A.plan(self.root, None, None, trusted=True)
        self.assertTrue(all(s['auto_restart'] is True for s in plan['services']))
        before = {s['name']: s['print'] for s in plan['services']}
        body = {'name': '本机', 'services': [dict(s, auto_restart=(False if s['name'] == '后端' else True)) for s in env['services']], 'vars': [], 'prep': []}
        EE.env_save(self.root, self.env, body)
        saved = json.loads((self.root / '.relayforge' / 'envs' / f'{self.env}.json').read_text(encoding='utf-8'))
        self.assertEqual([s.get('auto_restart') for s in saved['services']], [None, False])  # 开着的不写
        plan = A.plan(self.root, None, None, trusted=True)
        self.assertEqual({s['name']: s['auto_restart'] for s in plan['services']}, {'应用': True, '后端': False})
        self.assertEqual({s['name']: s['print'] for s in plan['services']}, before)  # 开关不改启动命令的指纹：不用重新同意
        with self.assertRaises(ValueError) as caught:
            EE.env_save(self.root, self.env, {'name': '本机', 'services': [{'name': '应用', 'command': 'x', 'auto_restart': 'maybe'}]})
        self.assertIn('代码变了自动重启', str(caught.exception))

    def test_ai_may_propose_accept_vars_and_the_user_adopts_them(self):
        EH.env_propose(self.root, self.env, {'services': [{'name': '前端', 'command': 'npm start', 'dir': 'web', 'accept': [{'name': 'DATA_DIR', 'value': '{{scratch}}/data'}], 'why': '分开数据'}]})
        EH.env_adopt(self.root, self.env)
        web = next(s for s in EV.env_show(self.root, self.env)['services'] if s['name'] == '前端')
        self.assertEqual(web['accept'], [{'name': 'DATA_DIR', 'value': '{{scratch}}/data'}])

    def test_fill_placeholders(self):
        self.assertEqual(A.fill('{{scratch}}/x {{ req }}', {'scratch': '/s', 'req': 'R-1'}), ('/s/x R-1', []))
        self.assertEqual(A.fill('{{nope}}/x', {'scratch': '/s'}), ('{{nope}}/x', ['nope']))


class PlanTests(AcceptBase):
    def test_plan_for_a_requirement_resolves_placeholders_and_masks_secrets(self):
        req, place = self.requirement()
        with trusted(False):
            p = A.plan(self.root, req=req)
        self.assertEqual(p['scope'], req)
        self.assertEqual(p['place'], str(place))
        app = p['services'][0]
        vals = {a['name']: a['value'] for a in app['accept']}
        base = A.places(self.root, req)
        self.assertEqual(vals['APP_DATA'], str(base['scratch']) + '/userdata')
        self.assertEqual(vals['APP_PROJECT'], str(base['copy']))
        self.assertEqual(vals['APP_TAG'], f'验收用 · 需求 {req}')
        self.assertEqual(vals['APP_CODE'], str(place))
        self.assertEqual(vals['APP_HOME'], str(self.root))
        self.assertEqual(app['cwd'], str(place / 'app'))
        self.assertEqual(app['opens'], None)  # 不是 RelayForge 的变量：界面就列出解析后的变量
        self.assertFalse(app['approved'])
        self.assertEqual(p['needs'], {'services': ['应用', '后端'], 'prep': ['P1']})
        self.assertNotIn('vars', p)
        self.assertNotIn(SECRET, json.dumps(p, ensure_ascii=False))
        self.assertFalse(base['scratch'].exists())  # 只看不做
        self.assertFalse(base['copy'].exists())

    def test_main_scope_and_relayforge_words(self):
        EE.env_save(self.root, self.env, {'name': '本机', 'services': [{'name': '应用', 'command': 'npm start', 'dir': 'app', 'accept': [
            {'name': 'RELAYFORGE_USER_DATA', 'value': '{{scratch}}/userdata'}, {'name': 'RELAYFORGE_PROJECT', 'value': '{{project_copy}}'}]}]})
        p = A.plan(self.root)
        self.assertEqual(p['scope'], 'main')
        self.assertIn('当前项目的副本', p['services'][0]['opens'])
        self.assertEqual(A.fill('需求 {{req}}', {'req': '主线'})[0], '需求 主线')
        EE.env_save(self.root, self.env, {'name': '本机', 'services': [{'name': '应用', 'command': 'npm start', 'dir': 'app', 'accept': [
            {'name': 'RELAYFORGE_USER_DATA', 'value': '{{scratch}}/userdata'}, {'name': 'RELAYFORGE_PROJECT', 'value': 'demo'}]}]})
        self.assertEqual(A.plan(self.root)['services'][0]['opens'], '演示项目')

    def test_unknown_placeholder_is_a_problem(self):
        EE.env_save(self.root, self.env, {'name': '本机', 'services': [{'name': '应用', 'command': 'x', 'accept': [{'name': 'A', 'value': '{{oops}}'}]}]})
        app = A.plan(self.root)['services'][0]
        self.assertEqual(app['missing'], ['oops'])
        self.assertIn('{{oops}}', app['problem'])

    def test_launch_needs_the_app_and_gives_values(self):
        req, place = self.requirement()
        code, out, err = self.cli('env', 'launch', '--req', req, '--json')
        self.assertEqual(code, 1)
        self.assertIn('只能由你在应用里做', err)
        with trusted():
            code, out, err = self.cli('env', 'launch', '--req', req, '--json')
        self.assertEqual(code, 0, err)
        p = json.loads(out)
        self.assertEqual(p['vars'], {'MODE': 'local', 'DB_PASSWORD': SECRET})
        self.assertIn(SECRET, p['secrets'])
        self.assertTrue(Path(p['scratch']).is_dir())
        copy = Path(p['copy']['path'])
        self.assertTrue((copy / '.git').is_dir())
        self.assertFalse(p['copy']['info']['reused'])
        # 会话里看：打码，没有值
        code, out, err = self.cli('env', 'plan', '--req', req, '--json')
        self.assertEqual(code, 0, err)
        self.assertNotIn(SECRET, out)
        self.assertNotIn('"vars"', out)

    def test_consent_for_start_commands_and_its_fingerprint(self):
        with trusted(False):
            with self.assertRaises(ValueError):
                EP.env_approve(self.root, self.env, services=['应用'])
        with trusted():
            made = EP.env_approve(self.root, self.env, services=['应用'])
        self.assertEqual(made['services'], ['应用'])
        self.assertEqual(made['approved'], [])  # 只说服务时不连带准备步骤
        p = A.plan(self.root)
        self.assertEqual([s['approved'] for s in p['services']], [True, False])
        self.assertEqual(p['needs']['prep'], ['P1'])
        # 命令变了要重新同意；目录变了也是
        EE.env_save(self.root, self.env, {'name': '本机', 'services': [{'name': '应用', 'command': 'python3 serve.py --x', 'dir': 'app'}]})
        self.assertFalse(A.plan(self.root)['services'][0]['approved'])
        with trusted():
            EP.env_approve(self.root, self.env, services=['应用'])
        EE.env_save(self.root, self.env, {'name': '本机', 'services': [{'name': '应用', 'command': 'python3 serve.py --x', 'dir': ''}]})
        self.assertFalse(A.plan(self.root)['services'][0]['approved'])
        # 和准备步骤的同意分开：同一条命令当准备步骤同意过，不算服务同意过
        self.assertNotEqual(A.service_print({'command': 'echo prep >> prep.log', 'dir': ''}), EP.cmd_print({'command': 'echo prep >> prep.log', 'dir': ''}))
        with trusted():
            code, out, err = self.cli('env', 'approve', self.env, '--service', '应用', '--json')
        self.assertEqual(code, 0, err)
        self.assertTrue(A.plan(self.root)['services'][0]['approved'])

    def test_prep_run_only_due_steps_in_the_working_directory(self):
        req, place = self.requirement()
        with trusted():
            EP.env_approve(self.root, self.env, ['P1'])
            code, out, err = self.cli('env', 'prep-run', self.env, '--req', req, '--due', '--json')
        self.assertEqual(code, 0, err)
        self.assertEqual([r['id'] for r in json.loads(out)['ran']], ['P1'])
        self.assertTrue((place / 'prep.log').exists())
        self.assertFalse((self.root / 'prep.log').exists())


class CopyTests(AcceptBase):
    def test_copy_is_a_clone_without_origin_with_the_current_facts(self):
        (self.root / '.relayforge' / 'notes-not-committed.md').write_text('还没提交\n', encoding='utf-8')
        (self.root / 'loose.txt').write_text('没提交的代码改动\n', encoding='utf-8')
        dest = A.places(self.root, 'main')['copy']
        info = A.make_copy(self.root, dest)
        self.assertFalse(info['reused'])
        self.assertEqual(info['branch'], 'main')
        self.assertEqual(git(dest, 'remote').strip(), '')  # 推不回去
        self.assertTrue((dest / '.relayforge' / 'notes-not-committed.md').exists())  # 主目录现在的事实
        self.assertFalse((dest / 'loose.txt').exists())
        self.assertNotIn(str(self.root), git(dest, 'worktree', 'list'))
        alt = dest / '.git' / 'objects' / 'info' / 'alternates'
        self.assertFalse(alt.exists())  # 对象是复制的，不借用真实仓库
        # 在副本里改、提交，真实项目不动
        (dest / 'calc.py').write_text('broken\n', encoding='utf-8')
        git(dest, 'commit', '-qam', '在副本里改')
        self.assertIn('return a + b', (self.root / 'calc.py').read_text(encoding='utf-8'))
        self.assertEqual(git(self.root, 'log', '--oneline').count('在副本里改'), 0)
        # 沿用上次的：副本里的改动还在；重新复制：回到项目现在的样子
        again = A.make_copy(self.root, dest)
        self.assertTrue(again['reused'])
        self.assertEqual((dest / 'calc.py').read_text(encoding='utf-8'), 'broken\n')
        fresh = A.make_copy(self.root, dest, fresh=True)
        self.assertFalse(fresh['reused'])
        self.assertIn('return a + b', (dest / 'calc.py').read_text(encoding='utf-8'))

    def test_never_overwrites_something_that_is_not_a_copy(self):
        dest = A.places(self.root, 'main')['copy']
        dest.mkdir(parents=True)
        (dest / 'mine.txt').write_text('x', encoding='utf-8')
        with self.assertRaises(ValueError):
            A.make_copy(self.root, dest)
        self.assertTrue((dest / 'mine.txt').exists())

    def test_clean_removes_scratch_and_copy(self):
        with trusted():
            p = A.plan(self.root, prepare=True)
        self.assertTrue(Path(p['scratch']).exists() and Path(p['copy']['path']).exists())
        code, out, err = self.cli('env', 'accept-clean', '--json')
        self.assertEqual(code, 1)  # 只由应用做
        self.assertIn('只能由你在应用里做', err)
        with trusted():
            code, out, err = self.cli('env', 'accept-clean', '--json')
        self.assertEqual(code, 0, err)
        self.assertFalse(Path(p['scratch']).exists() or Path(p['copy']['path']).exists())
        self.assertIn('accept', p['scratch'])
        self.assertTrue(p['scratch'].startswith(os.environ['RELAYFORGE_HOME']))  # 在本机数据目录下，不在仓库里


class HandoffTests(AcceptBase):
    def test_start_failure_handoff_has_command_dir_code_and_scrubbed_tail(self):
        req, place = self.requirement()
        report = {'code': 3, 'reason': '进程很快退出了（退出码 3）', 'tail': f'connecting with {SECRET}\nTraceback: boom', 'command': 'python3 serve.py', 'cwd': str(place / 'app')}
        code, out, err = self.cli('env', 'handoff', 'start', self.env, '--service', '应用', '--req', req, '--file', '-', '--json', stdin=json.dumps(report))
        self.assertEqual(code, 0, err)
        made = json.loads(out)
        self.assertIn('服务「应用」启动失败', made['title'])
        text = made['text']
        for words in ('python3 serve.py', str(place / 'app'), '退出码 3', 'Traceback: boom', f'需求 {req} 的工作目录', '再点「启动」'):
            self.assertIn(words, text)
        self.assertNotIn(SECRET, text)
        self.assertIn(E.MASK, text)


class MarkTests(AcceptBase):
    def setUp(self):
        super().setUp()
        self.req, self.place = self.requirement()
        rf.req_attach(self.root, self.req, DEV)

    def to_check(self):
        rf.note(self.place, DEV, None, 'plan', ['写代码'])
        rf.note(self.place, DEV, None, 'step', ['1', 'done'])
        G.req_skip_test(self.root, self.req, 'manual', trusted=True)
        self.assertEqual(rf.find_requirement(self.root, self.req)['stage'], 'check')

    def test_only_the_app_and_only_in_check(self):
        with self.assertRaises(ValueError) as caught:
            A.req_accept(self.root, self.req, 1, 'ok', trusted=False)
        self.assertIn('只能由你在应用里做', str(caught.exception))
        with self.assertRaises(ValueError):
            A.req_accept(self.root, self.req, 1, 'ok', trusted=True)  # 还在进行中
        self.to_check()
        code, out, err = self.cli('req', 'accept', self.req, '1', 'ok')
        self.assertEqual(code, 1)
        self.assertIn('只能由你在应用里做', err)
        with trusted():
            code, out, err = self.cli('req', 'accept', self.req, '1', 'ok', '--json')
        self.assertEqual(code, 0, err)
        with self.assertRaises(ValueError):
            A.req_accept(self.root, self.req, 9, 'ok', trusted=True)
        with self.assertRaises(ValueError):
            A.req_accept(self.root, self.req, 2, 'bad', say='', trusted=True)

    def test_ok_and_bad_are_recorded_and_bad_goes_back_with_the_log(self):
        self.to_check()
        A.req_accept(self.root, self.req, 1, 'ok', trusted=True)
        r = rf.find_requirement(self.root, self.req)
        self.assertEqual(r['stage'], 'check')
        c1 = r['testing']['checks'][0]
        self.assertEqual((c1['accept']['result'], c1['accept']['by']), ('ok', '用户 t'))
        self.assertIsNone(r['testing']['checks'][1]['accept'])
        log = '\n'.join(f'line {i}' for i in range(100)) + f'\npassword={SECRET}'
        with trusted():
            code, out, err = self.cli('req', 'accept', self.req, '2', 'bad', '--say', '点了「=」没反应', '--file', '-', '--json', stdin=log)
        self.assertEqual(code, 0, err)
        made = json.loads(out)
        self.assertEqual(made['result'], 'bad')
        say = made['say']
        for words in ('第 2 条验收条件不对', '界面上显示结果', '点了「=」没反应', 'line 99', 'req retest'):
            self.assertIn(words, say)
        self.assertNotIn('line 10\n', say)  # 只带最后几十行
        self.assertNotIn(SECRET, say)
        r = rf.find_requirement(self.root, self.req)
        self.assertEqual(r['stage'], 'build')  # 退回进行中
        meta, sections = rf.read_doc(self.root / r['file'])
        self.assertTrue(any('人工验收时说第 2 条验收条件不对' in l for l in sections['变更记录']))
        lines = sections['人工验收']
        self.assertRegex(lines[0], r'^- 通过｜加法算对｜\d{4}-\d\d-\d\d \d\d:\d\d｜用户 t$')
        self.assertRegex(lines[1], r'^- 这里不对｜界面上显示结果｜.*｜用户 t｜点了「=」没反应$')
        heads = list(sections)
        self.assertLess(heads.index('人工验收'), heads.index('变更记录'))
        self.assertNotIn(SECRET, (self.root / r['file']).read_text(encoding='utf-8'))
        # 修好以后照旧：开发会话 retest → 测试中
        app = FakeApp()  # 应用开的开发会话：签名的会话令牌（tests/app_fixture.py）
        try:
            with app.session('develop', self.req, DEV):
                G.req_retest(self.place, '修好了等号')
        finally:
            app.close()
        self.assertEqual(rf.find_requirement(self.root, self.req)['stage'], 'test')

    def test_bad_one_carries_which_commit_was_running_and_whether_the_code_changed(self):
        self.to_check()
        ran = '「后端」运行的是提交 abc1234；启动后代码改过了，运行的不是最新的，这次可能测到的是旧代码'
        with trusted():
            code, out, err = self.cli('req', 'accept', self.req, '2', 'bad', '--say', '点了没反应', '--ran', ran, '--file', '-', '--json', stdin='log')
        self.assertEqual(code, 0, err)
        say = json.loads(out)['say']
        self.assertIn('当时运行的服务：' + ran, say)
        self.assertLess(say.index('当时运行的服务'), say.index('当时服务的日志'))
        # 不给就没有这一行
        self.assertTrue(rf.find_requirement(self.root, self.req)['stage'] == 'build')

    def test_done_record_says_which_were_passed_and_which_were_not_clicked(self):
        self.to_check()
        A.req_accept(self.root, self.req, 1, 'ok', trusted=True)
        (self.place / 'calc.py').write_text('def add(a, b):\n    return b + a\n', encoding='utf-8')
        git(self.place, 'commit', '-qam', 'change')
        rf.req_done(self.root, self.req)
        meta, sections = rf.read_doc(self.root / rf.find_requirement(self.root, self.req)['file'])
        line = next(l for l in sections['变更记录'] if '人工验收：' in l)
        self.assertIn('第 1 条你点了通过', line)
        self.assertIn('第 2 条没有点', line)


if __name__ == '__main__':
    unittest.main()

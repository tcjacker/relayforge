"""测试用例（三个会话的协作分工）：用例的读写和编号、归类挪到立项、确认立项只提示不拦、立项以后改用例走用户确认（每条路径）、
测试会话补充用例、测试数据挂用例和「用例 → 数据 → 结果」的汇总、交回「可以验收了」的新前提、没有用例的老需求退回原来的做法、
谁能用哪条命令、req choose 放开开发会话。"""
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
from relayforge.core import identity  # noqa: E402
from relayforge.core import trust  # noqa: E402
from relayforge.requirements import testing as G  # noqa: E402
from relayforge.testsets import store as T  # noqa: E402
from relayforge.testsets import edits as TE  # noqa: E402
from relayforge.testsets import views as TV  # noqa: E402
from relayforge.testsets import runs as TR  # noqa: E402
from relayforge.requirements import usecases as U  # noqa: E402
from tests.app_fixture import FakeApp, app_marker  # noqa: E402
from tests.test_testsets import Base  # noqa: E402

INTAKE, DEV, TESTER = 'intake-session-1', 'dev-session-1', 'test-session-1'
ONE = dict(name='两个正数', cat='正常', given='计算器开着', when='算 1 加 2', then='得到 3')


class CaseBase(Base):
    def setUp(self):
        super().setUp()
        self.app = FakeApp()
        self.addCleanup(self.app.close)
        made = rf.req_new(self.root, '改加法')
        self.req, self.place = made['id'], Path(made['worktree'])
        path = self.root / made['file']
        meta, sections = rf.read_doc(path)
        sections['需求说明'] = ['- 加法要算对〔S-x#1〕']
        sections['要改的功能点'] = ['- RF-101 加法：改一改〔S-x#1〕']
        sections['验收条件'] = ['- 加法算对〔S-x#1〕', '- 结果显示在屏幕上〔S-x#1〕']
        rf.write_doc(path, meta, sections)
        rf.req_attach(self.root, self.req, INTAKE)

    def as_session(self, sid, kind):
        token = self.app.issue(sid, kind, self.req)
        identity.forget()
        patch = mock.patch.dict(os.environ, {'RELAYFORGE_SESSION_ID': sid, identity.SIG_ENV: token})
        patch.start()
        self.addCleanup(patch.stop)

    def leave(self):
        os.environ.pop('RELAYFORGE_SESSION_ID', None)
        os.environ.pop(identity.SIG_ENV, None)
        identity.forget()

    def found(self):
        return rf.find_requirement(self.root, self.req)

    def text(self):
        return (self.root / self.found()['file']).read_text(encoding='utf-8')

    def start(self):
        self.leave()
        rf.req_start(self.root, self.req)
        rf.req_attach(self.root, self.req, DEV)

    def to_test(self):
        rf.note(self.place, DEV, None, 'plan', ['写代码'])
        rf.note(self.place, DEV, None, 'step', ['1', 'done'])
        self.assertEqual(self.found()['stage'], 'test')
        rf.req_attach(self.root, self.req, TESTER, test=True, trusted=True)
        self.as_session(TESTER, 'test')

    def intake_cases(self):
        self.as_session(INTAKE, 'intake')
        U.case_add(self.place, 1, ONE)
        U.case_add(self.place, 1, dict(name='负数', cat='边界', given='计算器开着', when='算 -1 加 -2', then='得到 -3'))
        U.req_classify(self.place, ['1'], '给两个数就能比对结果', '数据')
        U.req_classify(self.place, ['2'], '要看屏幕上显示得对不对', '界面')

    def cli(self, *args, cwd=None):
        out, err = io.StringIO(), io.StringIO()
        old = os.getcwd()
        os.chdir(cwd or self.place)
        try:
            with redirect_stdout(out), redirect_stderr(err):
                code = rf.main(list(args))
        finally:
            os.chdir(old)
        return code, out.getvalue(), err.getvalue()


class IntakeTests(CaseBase):
    def test_cases_are_written_in_the_file_with_numbers_never_reused(self):
        self.intake_cases()
        cases = self.found()['testing']['cases']
        self.assertEqual([(c['n'], c['name'], c['cat'], c['source'], c['live']) for c in cases],
                         [(1, '两个正数', '正常', 'intake', True), (2, '负数', '边界', 'intake', True)])
        text = self.text()
        self.assertIn('- 用例 1｜加法算对｜两个正数｜正常｜计算器开着｜算 1 加 2｜得到 3｜立项｜在用', text)
        self.assertLess(text.index('## 验收归类'), text.index('## 测试用例'))
        self.assertLess(text.index('## 测试用例'), text.index('## 变更记录'))
        U.case_remove(self.place, 2)  # 立项中：随便删，删了的留着、编号不再用
        U.case_add(self.place, 1, dict(ONE, name='大数', when='算 100 加 200', then='得到 300'))
        cases = self.found()['testing']['cases']
        self.assertEqual([(c['n'], c['live']) for c in cases], [(1, True), (2, False), (3, True)])
        U.case_edit(self.place, 1, {'then': '屏幕上显示 3'}, check_n=1)
        self.assertEqual(self.found()['testing']['cases'][0]['then'], '屏幕上显示 3')
        self.assertEqual(self.found()['testing']['checks'][0]['cases'], [1, 3])
        with self.assertRaisesRegex(ValueError, '已经有一条叫「大数」'):
            U.case_add(self.place, 1, dict(ONE, name='大数'))
        with self.assertRaisesRegex(ValueError, '缺了「预期」'):
            U.case_add(self.place, 1, {'name': 'x', 'given': 'a', 'when': 'b'})
        with self.assertRaisesRegex(ValueError, '只能是 正常、边界、异常'):
            U.case_add(self.place, 1, dict(ONE, name='y', cat='随便'))
        with self.assertRaisesRegex(ValueError, '已经删了'):
            U.case_edit(self.place, 2, {'name': '复活'})
        made = U.case_add(self.place, 1, dict(ONE, name='代码味', when='调用 add_numbers() 读 calc.py'))
        self.assertIn('add_numbers()', made['hints'])  # 提醒用业务的话，不拦

    def test_classification_moved_to_intake_and_the_gate_only_hints(self):
        self.as_session(INTAKE, 'intake')
        from relayforge.facts.drafts import design_skip
        design_skip(self.place, '没有新界面', 'S-x#1')
        r = self.found()
        self.assertEqual(r['level'], 'acceptance')
        self.assertEqual([x['n'] for x in r['gate']['uncovered']], [1, 2])
        self.assertTrue(r['gate']['ok'])  # 至少一条验收条件的硬门槛不变；没有用例只提示
        self.assertIn('还有 2 条验收条件没有测试用例', r['gate']['hint'])
        step = next(x for x in r['ladder'] if x['key'] == 'cases')
        self.assertFalse(step['reached'])
        self.assertEqual(step['missing'], ['第 1 条验收条件的用例', '第 2 条验收条件的用例'])
        U.case_add(self.place, 1, ONE)
        U.req_classify(self.place, ['2'], '要看屏幕', '界面')  # 只能人工看的：不用写用例
        r = self.found()
        self.assertEqual((r['level'], r['gate']['uncovered'], r['gate']['hint']), ('cases', [], ''))
        self.assertEqual(r['testing']['checks'][1]['kind'], 'ui')
        self.leave()
        with self.assertRaisesRegex(ValueError, '当前会话不是'):
            U.req_classify(self.place, ['1'], '日常聊天不能归类', '数据')
        with self.assertRaisesRegex(ValueError, '当前会话不是'):
            U.case_add(self.place, 1, dict(ONE, name='聊天加的'))

    def test_starting_with_uncovered_conditions_is_allowed_and_recorded(self):
        self.as_session(INTAKE, 'intake')
        U.case_add(self.place, 1, ONE)
        self.leave()
        r = rf.req_start(self.root, self.req)
        self.assertEqual(r['state'], 'open')
        self.assertIn('1 条验收条件还没有测试用例：第 2 条', ' '.join(r['sections']['变更记录']))
        self.assertEqual(rf.read_doc(self.root / r['file'])[0]['case_flow'], 1)


class ProposalTests(CaseBase):
    def setUp(self):
        super().setUp()
        self.intake_cases()
        self.start()

    def test_every_change_after_start_waits_for_the_user(self):
        self.as_session(DEV, 'develop')
        with self.assertRaisesRegex(ValueError, '要用户同意：加上 --why'):
            U.case_edit(self.place, 1, {'then': '得到 4'})
        made = U.case_edit(self.place, 1, {'then': '得到 4'}, why='需求改成了先加一')
        self.assertTrue(made['pending'])
        self.assertEqual(self.found()['testing']['cases'][0]['then'], '得到 3')  # 没生效
        U.case_remove(self.place, 2, why='负数不用测')
        U.case_add(self.place, 1, dict(ONE, name='零', when='算 0 加 0', then='得到 0'), why='漏了零')
        U.req_classify(self.place, ['2'], '其实能比对输出的文字', '数据')
        self.leave()
        self.as_session(INTAKE, 'intake')  # 立项会话接着聊：一样要提
        made = U.case_edit(self.place, 2, {'name': '两个负数'}, why='名称更清楚')
        self.assertTrue(made['pending'])
        t = self.found()['testing']
        changes = t['case_changes']
        self.assertEqual([(c['k'], c['op'], c['actor'], c['state']) for c in changes],
                         [(1, 'edit', 'develop', 'pending'), (2, 'delete', 'develop', 'pending'), (3, 'add', 'develop', 'pending'),
                          (4, 'classify', 'develop', 'pending'), (5, 'edit', 'intake', 'pending')])
        self.assertEqual(changes[0]['old']['then'], '得到 3')
        self.assertEqual(changes[0]['new']['then'], '得到 4')
        self.assertEqual(t['pending_case_changes'], 5)
        self.assertIn('提出用例改动 1', ' '.join(self.found()['sections']['变更记录']))
        made = U.case_edit(self.place, 2, {'name': '负负得负'}, why='再换个名')  # 同一处、同一种会话还没处理的：换掉，不攒一串
        self.assertEqual(made['change'], 5)
        self.assertEqual(len(self.found()['testing']['case_changes']), 5)
        self.leave()
        with self.assertRaisesRegex(ValueError, '只能由你在应用里做'):
            U.case_decide(self.root, self.req, 1, True, trusted=False)
        for k in (1, 2, 3, 4):
            U.case_decide(self.root, self.req, k, True, trusted=True)
        U.case_decide(self.root, self.req, 5, False, trusted=True)
        t = self.found()['testing']
        cases = {c['n']: c for c in t['cases']}
        self.assertEqual(cases[1]['then'], '得到 4')
        self.assertFalse(cases[2]['live'])
        self.assertEqual((cases[3]['name'], cases[3]['source']), ('零', 'intake'))
        self.assertEqual(t['checks'][1]['kind'], 'data')
        self.assertEqual([c['state'] for c in t['case_changes']], ['approved'] * 4 + ['rejected'])
        with self.assertRaisesRegex(ValueError, '已经不同意了'):
            U.case_decide(self.root, self.req, 5, True, trusted=True)
        log = ' '.join(self.found()['sections']['变更记录'])
        self.assertIn('用户同意用例改动 1', log)
        self.assertIn('用户不同意用例改动 5', log)

    def test_user_commands_are_checked_by_the_program(self):
        self.as_session(DEV, 'develop')
        U.case_edit(self.place, 1, {'then': '得到 4'}, why='理由')
        for act in (['case-ok', self.req, '1'], ['case-no', self.req, '1']):
            code, _, err = self.cli('req', *act)
            self.assertEqual(code, 1)
            self.assertIn('只能由你在应用里做', err)
        self.leave()
        with app_marker():
            code, out, err = self.cli('req', 'case-no', self.req, '1', '--json')
            self.assertEqual(code, 0, err)
            self.assertEqual(json.loads(out)['change']['state'], 'rejected')
            with mock.patch('sys.stdin', io.StringIO(json.dumps({'op': 'edit', 'case': 1, 'then': '用户改的预期'}))):
                code, out, err = self.cli('req', 'case-save', self.req, '--json')
            self.assertEqual(code, 0, err)
        self.assertEqual(self.found()['testing']['cases'][0]['then'], '用户改的预期')  # 用户自己改不受限

    def test_retest_says_the_development_session_proposed_a_case_change(self):
        rf.note(self.place, DEV, None, 'plan', ['写代码'])
        rf.note(self.place, DEV, None, 'step', ['1', 'done'])
        rf.req_attach(self.root, self.req, TESTER, test=True, trusted=True)
        self.as_session(TESTER, 'test')
        G.req_steps(self.place, 2, '计算器', '算 1 加 2', '屏幕上显示 3')
        G.req_verdict(self.place, 'problems', '', ['用例 1「两个正数」｜得到 4｜改坏了'])
        self.assertEqual(self.found()['testing']['last']['problems'][0]['case'], 1)  # 问题按用例列
        self.leave()
        self.as_session(DEV, 'develop')
        U.case_edit(self.place, 1, {'then': '得到 4'}, why='需求就是先加一，用例写错了')
        G.req_retest(self.place, '代码没错')
        r = self.found()
        self.assertIn('用例改动 1', r['testing']['rounds'][0]['fixed']['note'])
        self.assertIn('等用户确认', ' '.join(r['sections']['变更记录']))


class TestSessionTests(CaseBase):
    def setUp(self):
        super().setUp()
        self.intake_cases()
        self.start()
        self.to_test()

    def test_supplements_are_free_and_the_intakes_are_proposed(self):
        made = U.case_add(self.place, 1, dict(ONE, name='零加零', when='算 0 加 0', then='得到 0'))
        self.assertFalse(made['pending'])
        c = next(x for x in self.found()['testing']['cases'] if x['n'] == 3)
        self.assertEqual(c['source'], 'test')
        self.assertIn('测试补充', self.text())
        self.assertFalse(U.case_edit(self.place, 3, {'name': '两个零'})['pending'])  # 自己补充的：直接改
        self.assertTrue(U.case_edit(self.place, 1, {'then': '得到 4'}, why='我觉得写错了')['pending'])  # 立项写的：提给用户
        self.assertTrue(U.case_remove(self.place, 2, why='多余')['pending'])
        made = U.req_classify(self.place, ['1'], '其实要看界面', '界面')  # 改别人归好的类：提给用户
        self.assertEqual(made['proposed'], [3])
        self.assertEqual(self.found()['testing']['supplements'], 1)
        self.leave()
        U.case_save(self.root, self.req, {'op': 'delete', 'case': 3}, trusted=True)  # 用户可以删测试补充的
        self.assertFalse(next(x for x in self.found()['testing']['cases'] if x['n'] == 3)['live'])

    def test_data_must_join_a_case_and_the_report_follows_the_results(self):
        G.req_steps(self.place, 2, '计算器', '算 1 加 2', '屏幕上显示 3')
        ts = TE.testset_new(self.place, '加法', 'RF-101', 'tests/rf_add.py')['id']
        with self.assertRaisesRegex(ValueError, '没有挂用例'):
            TE.testset_add(self.place, ts, [{'name': '正数', 'input': {'a': 1, 'b': 2}, 'expect': 3}])
        with self.assertRaisesRegex(ValueError, '用例 9 在需求'):
            TE.testset_add(self.place, ts, [{'name': '正数', 'input': {'a': 1, 'b': 2}, 'expect': 3, 'usecase': 9}])
        TE.testset_add(self.place, ts, [{'name': '正数', 'input': {'a': 1, 'b': 2}, 'expect': 3, 'usecase': 1}])
        self.assertEqual(T.read_set(self.root, ts)['cases'][0]['usecase'], f'{self.req}#1')
        report = TV.usecase_report(self.root, self.req)
        self.assertEqual((report['by_case'][1]['status'], report['by_case'][2]['status']), ('unrun', 'unlinked'))
        with self.assertRaisesRegex(ValueError, '用例 1「两个正数」对接了还没跑；用例 2「负数」还没对接'):
            G.req_verdict(self.place, 'ok', '都过了')
        code, _, err = self.cli('test', 'add', ts, '--name', '负数', '--cat', '边界', '--input', '{"a": -1, "b": -2}', '--expect', '-4', '--usecase', '2')
        self.assertEqual(code, 0, err)
        TR.testset_run(self.place)
        report = TV.usecase_report(self.root, self.req)
        self.assertEqual((report['by_case'][1]['status'], report['by_case'][2]['status']), ('pass', 'fail'))
        self.assertEqual(report['by_case'][2]['links'][0]['set'], ts)
        self.assertEqual(report['counts'], {'pass': 1, 'fail': 1, 'broken': 0, 'unrun': 0, 'unlinked': 0})
        self.assertEqual([c['n'] for c in report['conditions'][0]['cases']], [1, 2])
        listed = TV.testset_list(self.root, req=self.req)
        self.assertEqual(listed['usecases']['by_case'][1]['status'], 'pass')  # 面板读的同一份汇总
        shown = TV.testset_show(self.place, ts)
        self.assertEqual(shown['usecases'][f'{self.req}#1']['when'], '算 1 加 2')  # 查看器：数据旁边的用例三段话
        code, out, _ = self.cli('req', 'cases', '--json')
        self.assertEqual(json.loads(out)['by_case']['2']['status'], 'fail')
        code, out, _ = self.cli('req', 'cases')
        self.assertIn('用例 2「负数」〔边界〕前提：计算器开着；操作：算 -1 加 -2；预期：得到 -3 —— 有失败', out)

    def test_ok_needs_every_intake_data_case_joined_and_run_or_explained(self):
        G.req_steps(self.place, 2, '计算器', '算 1 加 2', '屏幕上显示 3')
        ts = TE.testset_new(self.place, '加法', 'RF-101', 'tests/rf_add.py')['id']
        TE.testset_add(self.place, ts, [{'name': '正数', 'input': {'a': 1, 'b': 2}, 'expect': 3, 'usecase': 1}])
        TR.testset_run(self.place)
        with self.assertRaisesRegex(ValueError, '用例 2「负数」还没对接'):
            G.req_verdict(self.place, 'ok', '都过了')
        with self.assertRaisesRegex(ValueError, '不能是空的'):
            G.req_verdict(self.place, 'ok', '都过了', unlinked=['2｜'])
        U.case_add(self.place, 1, dict(ONE, name='补充的', when='算 5 加 5', then='得到 10'))  # 测试补充的不在这条规矩里
        code, out, err = self.cli('req', 'verdict', 'ok', '--summary', '用例 1 通过', '--unlinked', '2｜负数要等计算器支持输入负号')
        self.assertEqual(code, 0, err)
        r = self.found()
        self.assertEqual(r['stage'], 'check')
        self.assertEqual(r['testing']['last']['unlinked'], [{'case': 2, 'why': '负数要等计算器支持输入负号'}])

    def test_who_may_use_which_command(self):
        """One table: intake / develop / test session / a daily chat / the user, for each case command after 立项."""
        expect = {  # (命令, 会话) -> 'direct' | 'propose' | 'refused'
            ('add', 'intake'): 'propose', ('add', 'develop'): 'propose', ('add', 'test'): 'direct', ('add', 'chat'): 'refused', ('add', 'user'): 'direct',
            ('edit', 'intake'): 'propose', ('edit', 'develop'): 'propose', ('edit', 'test'): 'propose', ('edit', 'chat'): 'refused', ('edit', 'user'): 'direct',
            ('rm', 'intake'): 'propose', ('rm', 'develop'): 'propose', ('rm', 'test'): 'propose', ('rm', 'chat'): 'refused', ('rm', 'user'): 'direct',
            ('classify', 'intake'): 'propose', ('classify', 'develop'): 'propose', ('classify', 'test'): 'propose', ('classify', 'chat'): 'refused', ('classify', 'user'): 'direct',
        }
        meta = rf.read_doc(self.root / self.found()['file'])[0]
        case = {'source': 'intake'}
        for (cmd, who), want in expect.items():
            op = {'rm': 'delete'}.get(cmd, cmd)
            actor = None if who == 'chat' else who
            try:
                got = U.decide(meta, actor, op, case, classified=True, has_cases=True)
            except ValueError:
                got = 'refused'
            self.assertEqual(got, want, (cmd, who))
        draft = dict(meta, state='draft')
        self.assertEqual(U.decide(draft, 'intake', 'edit', case), 'direct')
        with self.assertRaisesRegex(ValueError, '由立项会话写'):
            U.decide(draft, 'develop', 'edit', case)
        with self.assertRaisesRegex(ValueError, '已经结束'):
            U.decide(dict(meta, state='done'), 'user', 'edit', case)
        for key in (('req', 'case-ok'), ('req', 'case-no'), ('req', 'case-save'), ('req', 'choose')):
            self.assertIn(key, trust.USER_COMMANDS)
        for key in (('req', 'case-add'), ('req', 'case-edit'), ('req', 'case-rm'), ('req', 'cases'), ('req', 'classify')):
            self.assertIsNone(trust.command_key(*key))  # 会话的命令：谁能直接改由程序按会话令牌定


class OldRequirementTests(CaseBase):
    """没有「测试用例」这一节的需求（立项时还没有这一步）：测试会话照原来的做法。"""

    def setUp(self):
        super().setUp()
        self.start()
        meta_path = self.root / self.found()['file']
        meta, sections = rf.read_doc(meta_path)
        meta.pop('case_flow', None)
        rf.write_doc(meta_path, meta, sections)
        self.to_test()

    def test_test_session_writes_data_without_cases_and_classifies_itself(self):
        self.assertFalse(self.found()['testing']['has_cases'])
        ts = TE.testset_new(self.place, '加法', 'RF-101', 'tests/rf_add.py')['id']
        TE.testset_add(self.place, ts, [{'name': '正数', 'input': {'a': 1, 'b': 2}, 'expect': 3}])  # 不要求挂用例
        U.req_classify(self.place, ['1'], '程序能比对', '数据')
        U.req_classify(self.place, ['1'], '还是要看界面', '界面')  # 自己归的，照旧能改
        U.req_classify(self.place, ['2'], '要看屏幕', '界面')
        G.req_steps(self.place, 1, '计算器', '算 1 加 2', '显示 3')
        G.req_steps(self.place, 2, '计算器', '看屏幕', '显示 3')
        G.req_verdict(self.place, 'ok', '按验收条件测过了')  # 没有用例：没有「每条用例都对接」这一条
        self.assertEqual(self.found()['stage'], 'check')
        report = TV.usecase_report(self.root, self.req)
        self.assertEqual((report['has_cases'], report['unjoined_data']), (False, 1))


class ChooseDevelopmentTests(CaseBase):
    def test_development_choice_can_change_after_creation_from_the_app_only(self):
        code, _, err = self.cli('req', 'choose', self.req, '--development-cli', 'codex')
        self.assertEqual(code, 1)
        self.assertIn('只能由你在应用里做', err)
        with app_marker():
            code, out, err = self.cli('req', 'choose', self.req, '--development-cli', 'codex', '--development-model', 'gpt-5.4', '--json')
            self.assertEqual(code, 0, err)
            made = json.loads(out)
            self.assertEqual(made['session_choices']['development'], {'cli': 'codex', 'model': 'gpt-5.4'})
            self.assertTrue(made['testing_follows_development'])
            self.assertIn('测试会话跟着开发会话，也用这个选择', made['sections']['变更记录'][-1])
            code, _, err = self.cli('req', 'choose', self.req, '--intake-cli', 'codex')
            self.assertEqual(code, 1)
            self.assertIn('立项会话的不能改', err)
        self.start()
        made = rf.req_choose(self.root, self.req, 'development', 'claude', 'model-x', trusted=True)
        self.assertIn('已经开着的开发会话不受影响，下一次新开的开发会话用这个选择', made['sections']['变更记录'][-1])


if __name__ == '__main__':
    unittest.main()

"""测试阶段和测试会话（后端）：阶段流转的每一条边、老需求兼容、验收归类和检查步骤的读写、交回两种结论、跳过的记录、
开发会话改测试会话写的数据要用户确认、结束检查拦住测试会话改产品代码、只由用户触发的命令要应用的标记、完成记录和历史里的测试结论。"""
import io
import json
import os
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'backend'))
import rf  # noqa: E402
from relayforge.requirements import testing as G  # noqa: E402
from relayforge.requirements import usecases as U  # noqa: E402
from relayforge.testsets import store as T  # noqa: E402
from relayforge.testsets import decisions as TD  # noqa: E402
from relayforge.testsets import edits as TE  # noqa: E402
from relayforge.testsets import lifecycle as TL  # noqa: E402
from tests.test_testsets import Base, git  # noqa: E402
from tests.app_fixture import FakeApp, app_marker  # noqa: E402
from relayforge.core import identity  # noqa: E402

DEV, TESTER = 'dev-session-1', 'test-session-1'


class StageBase(Base):
    def setUp(self):
        super().setUp()
        self.req, self.place = self.requirement()
        rf.req_attach(self.root, self.req, DEV)
        self.app = FakeApp()  # 应用发签名的会话令牌（tests/app_fixture.py）
        self.addCleanup(self.app.close)

    def as_session(self, sid, kind=None):
        """The environment of a session the app opened for this requirement: a test session for TESTER-like ids, else its development session."""
        kind = kind or ('test' if sid.startswith('test') else 'develop')
        token = self.app.issue(sid, kind, self.req)
        identity.forget()
        patch = mock.patch.dict(os.environ, {'RELAYFORGE_SESSION_ID': sid, identity.SIG_ENV: token})
        patch.start()
        self.addCleanup(patch.stop)

    def leave_session(self):
        os.environ.pop('RELAYFORGE_SESSION_ID', None)
        os.environ.pop(identity.SIG_ENV, None)

    def stage(self):
        return rf.find_requirement(self.root, self.req)['stage']

    def found(self):
        return rf.find_requirement(self.root, self.req)

    def finish_plan(self, sid=DEV):
        rf.note(self.place, sid, None, 'plan', ['写代码', '自己跑一遍'])
        rf.note(self.place, sid, None, 'step', ['1,2', 'done'])

    def tester(self):
        rf.req_attach(self.root, self.req, TESTER, test=True, trusted=True)
        self.as_session(TESTER)

    def to_test(self):
        self.finish_plan()
        self.assertEqual(self.stage(), 'test')
        self.tester()

    def classify_all(self, kind='数据'):
        U.req_classify(self.place, ['1'], '程序能比对加法的结果', kind)
        if kind == '界面':
            G.req_steps(self.place, 1, '打开计算器', '输入 1 + 2', '显示 3')

    def cli(self, *args, cwd=None):
        out, err = io.StringIO(), io.StringIO()
        old = os.getcwd()
        os.chdir(cwd or self.place)
        try:
            with redirect_stdout(out), redirect_stderr(err):
                code = rf.main(['req', *args])
        finally:
            os.chdir(old)
        return code, out.getvalue(), err.getvalue()


class StageTests(StageBase):
    def test_finishing_the_plan_goes_to_testing_for_new_requirements(self):
        meta = rf.read_doc(self.root / self.found()['file'])[0]
        self.assertEqual(meta['test_flow'], 1)  # 立项时写下：这个需求有测试阶段
        self.assertEqual(self.stage(), 'build')
        self.finish_plan()
        r = self.found()
        self.assertEqual(r['stage'], 'test')
        self.assertIsNone(r['moves']['check'])  # 跳过测试也是一条路
        self.assertTrue(r['testing']['needs_kick'])  # 应用要开出测试会话
        self.assertEqual(r['testing']['round'], 1)

    def test_old_requirements_without_the_record_keep_the_old_rule(self):
        path = self.root / self.found()['file']
        meta, sections = rf.read_doc(path)
        meta.pop('test_flow')
        rf.write_doc(path, meta, sections)
        self.finish_plan()
        r = self.found()
        self.assertEqual(r['stage'], 'check')  # 老需求计划勾完仍然是待验收，不会集体跳到测试中
        self.assertFalse(r['testing']['needs_kick'])
        self.assertEqual(rf.stage_of('open', None, 2, 2), 'check')
        self.assertEqual(rf.stage_of('open', None, 2, 2, True, True), 'test')
        self.assertEqual(rf.stage_of('open', 'test', 0, 0), 'test')
        self.assertEqual(rf.stage_of('draft', 'test'), 'talk')

    def test_the_test_session_reads_only_the_development_sessions_plan_for_the_stage(self):
        self.to_test()
        rf.note(self.place, TESTER, None, 'plan', ['归类', '写测试集', '交回'])  # 测试会话自己的计划还没做完
        r = self.found()
        self.assertEqual((r['stage'], r['done'], r['steps']), ('test', 2, 2))
        self.assertEqual(r['test_sessions'], [TESTER])
        self.assertEqual(r['dev_sessions'], [DEV])
        self.assertIn(TESTER, r['sessions'])  # 会话记录、归档、提交钩子照旧都认它

    def test_problems_go_back_to_building_and_retest_comes_back(self):
        self.to_test()
        rf.req_test_mark(self.root, self.req, 'kicked', 1, trusted=True)
        self.classify_all()
        out = G.req_verdict(self.place, 'problems', '', [{'where': '第 1 条验收条件「加法算对」', 'what': '1 + 2 得到 4', 'judge': '改坏了'},
                                                         '测试集 TS-001 #2｜负数相加符号反了｜改坏了'])
        self.assertEqual((out['round'], len(out['problems'])), (1, 2))
        r = self.found()
        self.assertEqual(r['stage'], 'build')
        t = r['testing']
        self.assertEqual((t['last']['verdict'], t['to_tell'], t['needs_kick']), ('problems', 1, False))
        self.assertEqual(t['last']['problems'][1], {'k': 2, 'where': '测试集 TS-001 #2', 'what': '负数相加符号反了', 'judge': '改坏了'})
        self.assertIn('测试会话交回第 1 轮：有问题 2 条，退回进行中', ' '.join(r['sections']['变更记录']))
        with self.assertRaisesRegex(ValueError, '测试会话不改产品代码'):
            G.req_retest(self.place, '修好了')  # 测试会话不能自己说修好了
        self.leave_session()
        self.as_session(DEV)
        with self.assertRaisesRegex(ValueError, '只有这个需求的测试会话能做'):
            G.req_verdict(self.place, 'ok', '都过了')
        G.req_retest(self.place, '加法改回来了')
        r = self.found()
        self.assertEqual(r['stage'], 'test')
        self.assertEqual(r['testing']['rounds'][0]['fixed']['note'], '加法改回来了')
        self.assertTrue(r['testing']['needs_kick'])  # 第 2 轮：应用要让测试会话再测
        self.assertEqual(r['testing']['round'], 2)
        with self.assertRaisesRegex(ValueError, '已经在测试中'):
            G.req_retest(self.place, '再说一次')

    def test_ok_goes_to_acceptance_and_is_told_once(self):
        self.to_test()
        self.classify_all()
        with self.assertRaisesRegex(ValueError, '附一句总结'):
            G.req_verdict(self.place, 'ok', '')
        G.req_verdict(self.place, 'ok', '测试集 TS-001 两条都通过')
        r = self.found()
        self.assertEqual(r['stage'], 'check')
        self.assertEqual((r['testing']['to_tell'], r['testing']['line']), (1, '测试：1 轮测试会话的结论（第 1 轮可以验收）。'))
        rf.req_test_mark(self.root, self.req, 'told', 1, trusted=True)
        self.assertEqual(self.found()['testing']['to_tell'], 0)
        with self.assertRaisesRegex(ValueError, '不在测试中'):
            G.req_verdict(self.place, 'ok', '再来一次')

    def test_verdict_needs_every_condition_classified_and_ui_ones_have_steps(self):
        self.to_test()
        with self.assertRaisesRegex(ValueError, '第 1 条还没归类'):
            G.req_verdict(self.place, 'ok', '都过了')
        U.req_classify(self.place, ['1'], '要看计算器的界面', '界面')
        with self.assertRaisesRegex(ValueError, '还没写检查步骤'):
            G.req_verdict(self.place, 'ok', '都过了')
        with self.assertRaisesRegex(ValueError, '要写三样'):
            G.req_steps(self.place, 1, '打开计算器', '', '')
        G.req_steps(self.place, 1, '打开计算器', '输入 1 + 2', '显示 3')
        G.req_steps(self.place, 1, '同一页', '输入 -1 + -2', '显示 -3')
        c = self.found()['testing']['checks'][0]
        self.assertEqual((c['kind'], c['why'], len(c['steps'])), ('ui', '要看计算器的界面', 2))
        self.assertEqual(c['steps'][0], {'open': '打开计算器', 'do': '输入 1 + 2', 'see': '显示 3'})
        G.req_steps(self.place, 1, clear=True)
        self.assertEqual(self.found()['testing']['checks'][0]['steps'], [])
        G.req_steps(self.place, 1, '打开计算器', '输入 1 + 2', '显示 3')
        U.req_classify(self.place, ['1'], '其实程序能比对', '数据')  # 重新归类：换掉旧的那一行
        U.req_classify(self.place, ['1'], '还是要看界面', '界面')
        text = (self.root / self.found()['file']).read_text(encoding='utf-8')
        self.assertEqual(text.count('加法算对｜'), 2)  # 一行归类、一行检查步骤
        self.assertLess(text.index('## 验收归类'), text.index('## 检查步骤'))
        self.assertLess(text.index('## 测试结论') if '## 测试结论' in text else 0, text.index('## 变更记录'))
        with self.assertRaisesRegex(ValueError, '只有 1 条验收条件'):
            U.req_classify(self.place, ['2'], '没有这条', '数据')
        with self.assertRaisesRegex(ValueError, '归类只能是'):
            U.req_classify(self.place, ['1'], '理由', '随便')
        G.req_verdict(self.place, 'ok', '全靠人工验收：检查步骤写好了')
        r = self.found()
        self.assertTrue(r['testing']['all_ui'])
        self.assertEqual(r['stage'], 'check')

    def test_only_the_test_session_writes_steps_and_the_development_session_only_proposes_a_class(self):
        self.finish_plan()
        self.as_session(DEV)
        made = U.req_classify(self.place, ['1'], '理由', '数据')  # 开发会话认为归类不对：提给用户，不直接生效
        self.assertEqual((made['classified'], made['proposed']), ([], [1]))
        self.assertIsNone(self.found()['testing']['checks'][0]['kind'])
        with self.assertRaisesRegex(ValueError, '只有这个需求的测试会话能做'):
            G.req_steps(self.place, 1, 'a', 'b', 'c')
        with self.assertRaisesRegex(ValueError, '只由应用自己做'):
            rf.req_attach(self.root, self.req, DEV, test=True, trusted=False)  # 开发会话不能把自己登记成测试会话

    def test_user_hands_to_testing_and_skips_testing_only_from_the_app(self):
        with self.assertRaisesRegex(ValueError, '只能由你在应用里做'):
            G.req_to_test(self.root, self.req, trusted=False)
        with self.assertRaisesRegex(ValueError, '只能由你在应用里做'):
            rf.req_set(self.root, self.req, 'test', trusted=False)
        with self.assertRaisesRegex(ValueError, '只能由你在应用里做'):
            rf.req_set(self.root, self.req, 'check', trusted=False)
        code, _, err = self.cli('to-test', self.req)
        self.assertEqual(code, 1)
        self.assertIn('只能由你在应用里做', err)
        code, _, err = self.cli('skip-test', self.req)
        self.assertEqual(code, 1)
        r = G.req_to_test(self.root, self.req, trusted=True)
        self.assertEqual(r['stage'], 'test')
        self.assertIn('用户把需求交给测试（从进行中）', ' '.join(r['sections']['变更记录']))
        with self.assertRaisesRegex(ValueError, '已经在测试中'):
            G.req_to_test(self.root, self.req, trusted=True)
        r = G.req_skip_test(self.root, self.req, trusted=True)
        self.assertEqual(r['stage'], 'check')
        t = r['testing']
        self.assertEqual((t['skipped'], t['last']['verdict'], t['to_tell'], t['needs_kick']), (True, 'skipped', 0, False))
        self.assertIn('用户跳过了测试，从测试中直接进入待验收', ' '.join(r['sections']['变更记录']))
        with self.assertRaisesRegex(ValueError, '只有进行中或测试中'):
            G.req_skip_test(self.root, self.req, trusted=True)

    def test_the_app_marker_lets_the_cli_hand_on_and_register_test_sessions(self):
        code, _, err = self.cli('attach', self.req, 'someone', '--test')
        self.assertEqual(code, 1)
        self.assertIn('只由应用自己做', err)
        with app_marker():  # 在进程里调 rf.main：当成应用启动的
            code, out, err = self.cli('attach', self.req, TESTER, '--test')
            self.assertEqual(code, 0, err)
            code, out, err = self.cli('to-test', self.req, '--json')
            self.assertEqual(code, 0, err)
            self.assertEqual(json.loads(out)['stage'], 'test')
            code, out, err = self.cli('test-kicked', self.req, '1')
            self.assertEqual(code, 0, err)
        self.assertEqual(self.found()['test_sessions'], [TESTER])
        self.assertEqual(self.found()['testing']['kicked'], 1)
        self.as_session(TESTER)
        code, out, err = self.cli('classify', '1', '数据', '--why', '程序能比对')
        self.assertEqual(code, 0, err)
        self.assertIn('第 1 条归为「数据可验」', out)
        code, out, err = self.cli('steps', '1', '计算器', '输入 1+2', '显示 3')
        self.assertEqual(code, 0, err)
        code, out, err = self.cli('verdict', 'problems', '--problem', '第 1 条｜1+2 得 4｜改坏了', '--problem', '第 1 条｜2+2 得 5｜改坏了')
        self.assertEqual(code, 0, err)
        self.assertIn('有问题 2 条', out)
        self.assertEqual(self.stage(), 'build')

    def test_skip_from_building_and_manual_moves_through_req_set(self):
        r = rf.req_set(self.root, self.req, 'check', trusted=True)  # 进行中直接到待验收：记成跳过了测试
        self.assertEqual((r['stage'], r['testing']['last']['verdict']), ('check', 'skipped'))
        self.assertIn('用户跳过了测试，从进行中直接进入待验收', ' '.join(r['sections']['变更记录']))
        r = rf.req_set(self.root, self.req, 'test', trusted=True)  # 待验收再交给测试
        self.assertEqual(r['stage'], 'test')
        self.assertTrue(r['testing']['needs_kick'])
        self.assertIn('从待验收', ' '.join(r['sections']['变更记录']))
        r = rf.req_set(self.root, self.req, 'build')  # 测试中退回进行中：用户随时可以
        self.assertEqual(r['stage'], 'build')
        rf.req_set(self.root, self.req, 'test', trusted=True)
        r = rf.req_set(self.root, self.req, 'check', trusted=True)  # 测试中由用户改到待验收：手动放行
        self.assertEqual(r['testing']['last']['verdict'], 'manual')
        self.assertEqual(r['testing']['line'], '测试：跳过了测试（第 1 轮跳过测试，第 2 轮手动放行）。')
        draft = rf.req_new(self.root, '还在立项')
        self.assertIn('验收条件', rf.find_requirement(self.root, draft['id'])['moves']['test'])

    def test_completion_record_history_and_trace_carry_the_testing_outcome(self):
        (self.place / 'calc.py').write_text('def add(a, b):\n    return b + a\n', encoding='utf-8')
        git(self.place, 'commit', '-qam', '换个写法')
        self.to_test()
        self.classify_all()
        G.req_verdict(self.place, 'problems', '', ['第 1 条｜结果不对｜改坏了'])
        self.as_session(DEV)  # 开发会话（应用发的令牌）说修好了
        G.req_retest(self.place, '修了')
        self.as_session(TESTER)
        G.req_verdict(self.place, 'ok', '第二轮都过了')
        self.leave_session()
        done = rf.req_done(self.root, self.req)
        record = ' '.join(done['sections']['变更记录'])
        self.assertIn('测试：2 轮测试会话的结论（第 1 轮有问题 1 条，第 2 轮可以验收）。', record)
        shown = rf.history_show(self.root, self.req)
        self.assertEqual(shown['testing'], '测试：2 轮测试会话的结论（第 1 轮有问题 1 条，第 2 轮可以验收）。')
        self.assertIn('测试：2 轮', rf.show_text(shown))
        self.assertIn('测试：2 轮', rf.trace_text(rf.trace_ref(self.root, self.req)))


class TestDataTests(StageBase):
    """进入测试以后，开发会话改测试会话写的数据要用户确认；测试会话自己改不受限；用户在应用里改不受限。"""

    def setUp(self):
        super().setUp()
        self.to_test()
        ts = TE.testset_new(self.place, '加法', 'RF-101', 'tests/rf_add.py')
        TE.testset_add(self.place, ts['id'], [{'name': '正数', 'input': {'a': 1, 'b': 2}, 'expect': 3},
                                             {'name': '零', 'cat': '边界', 'input': {'a': 0, 'b': 0}, 'expect': 0}])
        self.ts = ts['id']

    def test_the_test_session_marks_and_freely_edits_its_own_data(self):
        data = T.read_set(self.root, self.ts)
        self.assertEqual(data['made_in'], 'test')
        self.assertIn('测试会话写的', data['log'][0])
        out = TE.testset_edit(self.place, self.ts, 1, {'expect': 3, 'name': '两个正数'})
        self.assertFalse(out['pending'])
        out = TE.testset_remove(self.place, self.ts, 2)
        self.assertFalse(out['pending'])

    def test_the_development_session_needs_the_user_to_agree(self):
        self.leave_session()
        self.as_session(DEV)
        with self.assertRaisesRegex(ValueError, '测试会话为这个需求写的数据'):
            TE.testset_edit(self.place, self.ts, 1, {'expect': 4})
        out = TE.testset_edit(self.place, self.ts, 1, {'expect': 4}, why='需求改成了先加一')
        self.assertTrue(out['pending'])
        out = TE.testset_remove(self.place, self.ts, 2, why='零不用测')
        self.assertTrue(out['pending'])
        data = T.read_set(self.root, self.ts)
        self.assertEqual([c['state'] for c in data['changes']], ['pending', 'pending'])
        self.assertEqual(data['cases'][0]['expect'], 3)  # 数据本身没变，等用户同意
        added = TE.testset_add(self.place, self.ts, [{'name': '大数', 'input': {'a': 100, 'b': 200}, 'expect': 300}])  # 新加不用确认
        self.assertEqual(len(added['added']), 1)
        TE.testset_edit(self.place, self.ts, 3, {'expect': 301})  # 开发会话自己加的：直接改
        self.assertEqual(T.read_set(self.root, self.ts)['cases'][2]['expect'], 301)
        TD.testset_approve(self.root, self.ts, data['changes'][0]['id'])  # 用户同意：随需求完成落定
        TD.testset_reject(self.root, self.ts, data['changes'][1]['id'])
        changes = T.read_set(self.root, self.ts)['changes']
        self.assertEqual([c['state'] for c in changes], ['approved'])
        TL.testsets_promote(self.root, self.req)
        cases = {c['n']: c for c in T.read_set(self.root, self.ts)['cases']}
        self.assertEqual((cases[1]['expect'], 2 in cases), (4, True))

    def test_data_the_development_session_added_to_an_old_set_stays_free(self):
        self.leave_session()
        self.as_session(DEV)
        other = TE.testset_new(self.place, '减法', 'RF-102', 'tests/rf_add.py')  # 开发会话建的：不受限
        TE.testset_add(self.place, other['id'], [{'name': '一', 'input': {'a': 1, 'b': 1}, 'expect': 2}])
        self.assertFalse(TE.testset_edit(self.place, other['id'], 1, {'expect': 3})['pending'])
        self.leave_session()
        self.as_session(TESTER)
        TE.testset_add(self.place, other['id'], [{'name': '测试会话加的', 'input': {'a': 2, 'b': 2}, 'expect': 4}])
        self.leave_session()
        self.as_session(DEV)
        self.assertTrue(TE.testset_edit(self.place, other['id'], 2, {'expect': 5}, why='理由')['pending'])  # 测试会话加的那一条要确认


class StopCheckTests(StageBase):
    """测试会话这一轮改了产品代码：结束检查拦住；接入代码、桥接文件、测试目录下的不拦；别的会话不管。"""

    def setUp(self):
        super().setUp()
        self.to_test()
        self.transcript = Path(self.tmp.name) / 'turn.jsonl'
        self.transcript.write_text(json.dumps({'type': 'user', 'message': {'content': '开始测'}}) + '\n', encoding='utf-8')

    def hook(self, name, sid=TESTER, **extra):
        return dict({'hook_event_name': name, 'session_id': sid, 'cwd': str(self.place), 'transcript_path': str(self.transcript)}, **extra)

    def stop(self, sid=TESTER, **extra):
        out = io.StringIO()
        with redirect_stdout(out):
            rf.hook_event(self.hook('Stop', sid, **extra))
        return json.loads(out.getvalue()) if out.getvalue().strip() else None

    def test_product_code_changed_by_shell_is_blocked(self):
        rf.hook_event(self.hook('UserPromptSubmit', prompt='开始测'))
        (self.place / 'calc.py').write_text('def add(a, b):\n    return 0\n', encoding='utf-8')  # 比如 sed -i 改的
        (self.place / 'tests' / 'rf_more.py').write_text('def run(input):\n    return 1\n', encoding='utf-8')
        said = self.stop()
        self.assertEqual(said['decision'], 'block')
        self.assertIn('测试会话不改产品代码：这一轮改了 calc.py', said['reason'])
        self.assertNotIn('rf_more.py', said['reason'])
        self.assertIn('req verdict problems', said['reason'])
        self.assertIsNone(self.stop(stop_hook_active=True))  # 第二次停下不再拦（Claude Code 的约定）

    def test_committed_product_code_is_blocked_and_undoing_is_not(self):
        (self.place / 'calc.py').write_text('def add(a, b):\n    return a - b\n', encoding='utf-8')  # 开发会话留下没提交的
        rf.hook_event(self.hook('UserPromptSubmit', prompt='开始测'))
        self.assertIsNone(self.stop())  # 开头就改着的，测试会话没动它：不算
        git(self.place, 'checkout', '--', 'calc.py')
        self.assertIsNone(self.stop())  # 撤回不算改
        rf.hook_event(self.hook('UserPromptSubmit', prompt='再测'))
        (self.place / 'feature.py').write_text('x = 1\n', encoding='utf-8')
        git(self.place, 'add', 'feature.py')
        git(self.place, 'commit', '-qm', '测试会话不该提交产品代码')
        said = self.stop()
        self.assertIn('feature.py', said['reason'])

    def test_entry_code_bridge_files_and_test_folders_are_fine(self):
        rf.hook_event(self.hook('UserPromptSubmit', prompt='开始测'))
        TE.testset_new(self.place, '加法', 'RF-101', 'probe/rf_entry.py')
        (self.place / 'probe').mkdir()
        (self.place / 'probe' / 'rf_entry.py').write_text('def run(input):\n    return 1\n', encoding='utf-8')
        (self.place / 'test_bridge_x.py').write_text(f'# {T.BRIDGE_MARK}\n', encoding='utf-8')
        (self.place / 'spec').mkdir()
        (self.place / 'spec' / 'a.txt').write_text('x', encoding='utf-8')
        self.assertIsNone(self.stop())

    def test_edit_tool_writes_count_without_a_snapshot_and_other_sessions_are_not_checked(self):
        rows = [{'type': 'user', 'message': {'content': '开始测'}},
                {'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'name': 'Edit', 'input': {'file_path': str(self.place / 'calc.py')}}]}}]
        self.transcript.write_text('\n'.join(json.dumps(r) for r in rows) + '\n', encoding='utf-8')
        (self.place / 'calc.py').write_text('def add(a, b):\n    return 7\n', encoding='utf-8')
        self.assertIn('calc.py', self.stop()['reason'])
        said = self.stop(sid=DEV)  # 开发会话改产品代码是本分（它这一轮没记进展，只会被叫去记）
        self.assertNotIn('测试会话不改产品代码', (said or {}).get('reason', ''))


if __name__ == '__main__':
    unittest.main()

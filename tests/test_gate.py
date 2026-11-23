"""合并闸门（testsets/gate.py、flows.py）：合并前在合并后的代码上跑选出来的回归测试集。
本需求的、依赖它的、主干旅程上的；这份代码上测过的复用；超预算的留给全量；主线上原来就失败的不挡；没过的挡住合并。"""
import contextlib
import io
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'backend'))
import rf  # noqa: E402
from relayforge.core.trust import USER_COMMANDS  # noqa: E402
from relayforge.facts.mapfacts import set_trunk  # noqa: E402
from tests.app_fixture import app_marker  # noqa: E402
from relayforge.testsets import edits as TE  # noqa: E402
from relayforge.testsets import gate as G  # noqa: E402
from relayforge.testsets import runs as TR  # noqa: E402
from tests.test_testsets import Base, git  # noqa: E402


class GateTests(Base):
    def feature(self, ident, title, depends=()):
        rf.write_doc(self.root / rf.DIR / 'features' / f'{ident}-{title}.md',
                     {'id': ident, 'type': 'feature', 'title': title, 'status': 'current', 'module': 'M-01', 'depends_on': list(depends)},
                     {h: [] for h in rf.FEATURE_HEADS})

    def journey(self, ident, features, trunk=False):
        meta = {'id': ident, 'type': 'journey', 'title': '主流程', 'module': 'M-01', 'status': 'current'}
        if trunk:
            meta['trunk'] = 'true'
        rf.write_doc(self.root / rf.DIR / 'journeys' / f'{ident}-主流程.md', meta,
                     {'步骤': [f'- 做一步｜看到结果｜用户｜{"、".join(features)}〔S-x#1〕']})

    def landed_on(self, feature, cases, title='别的'):
        ts = TE.testset_new(self.root, title, feature, 'tests/rf_add.py')
        TE.testset_add(self.root, ts['id'], cases)
        return ts['id']

    def work(self, place, text='def add(a, b):\n    return b + a\n'):
        (place / 'calc.py').write_text(text, encoding='utf-8')
        git(place, 'add', '-A')
        git(place, 'commit', '-qm', 'work')

    def test_the_three_kinds_and_what_is_left_alone(self):
        own = self.landed()
        self.feature('RF-103', '汇总', depends=['RF-101'])
        dep = self.landed_on('RF-103', [{'name': '汇总', 'input': {'a': 2, 'b': 2}, 'expect': 4}], '汇总')
        other = self.landed_on('RF-102', [{'name': '导出', 'input': {'a': 1, 'b': 1}, 'expect': 2}], '导出')
        self.journey('J-01', ['RF-102'])  # 不是主干：导出的测试集不跑
        rf.commit_facts(self.root, 'facts')
        rid, place = self.requirement()
        plan = G.gate_plan(self.root, rid, place)
        self.assertEqual({s['id']: s['kind'] for s in plan['sets']}, {own: 'own', dep: 'depends'})
        self.assertEqual(plan['journeys'], [])
        self.journey('J-01', ['RF-102'], trunk=True)
        plan = G.gate_plan(self.root, rid, place)
        self.assertEqual({s['id']: s['kind'] for s in plan['sets']}, {own: 'own', dep: 'depends', other: 'trunk'})
        self.assertEqual([j['id'] for j in plan['journeys']], ['J-01'])

    def test_the_merged_code_is_checked_even_when_the_program_merges_the_target_in_itself(self):
        self.landed()
        (self.root / 'helper.py').write_text('K = 0\n', encoding='utf-8')
        (self.root / 'calc.py').write_text('from helper import K\ndef add(a, b):\n    return a + b + K\n', encoding='utf-8')
        git(self.root, 'add', '-A')
        git(self.root, 'commit', '-qm', 'helper')
        rid, place = self.requirement()
        (place / 'notes.txt').write_text('需求自己的改动\n', encoding='utf-8')
        git(place, 'add', '-A')
        git(place, 'commit', '-qm', 'work')
        self.assertEqual(TR.testset_run(place)['totals']['fail'], 0)  # 分支上测过：都通过
        (self.root / 'helper.py').write_text('K = 1\n', encoding='utf-8')  # 目标分支上另一处改动，和分支不冲突
        git(self.root, 'commit', '-qam', 'break')
        with self.assertRaises(G.GateFailed) as caught:
            rf.req_done(self.root, rid)
        result = caught.exception.result
        self.assertFalse(result['ok'])
        self.assertEqual([f['kind'] for f in result['failing']], ['own'])
        self.assertEqual(result['failing'][0]['cases'], [1, 2])
        self.assertIn('没过合并前的检查', str(caught.exception))
        self.assertFalse((self.root / 'notes.txt').exists())  # 没合进去
        self.assertEqual(rf.load_requirements(self.root)[0]['state'], 'open')

    def test_a_result_on_this_very_code_is_reused_and_the_record_says_what_ran(self):
        self.landed()
        rid, place = self.requirement()
        self.work(place)
        TR.testset_run(place)
        with mock.patch.object(G, 'run_one', side_effect=AssertionError('不该再跑')):
            plan = G.gate_run(self.root, rid, place)
        self.assertTrue(plan['ok'])
        self.assertEqual((plan['ran'], plan['reused']), (0, 1))
        rf.req_done(self.root, rid)
        record = (self.root / rf.load_requirements(self.root)[0]['file']).read_text(encoding='utf-8')
        self.assertIn('合并前在合并后的代码上检查了 1 个测试集（本需求 1 个），其中 1 个这份代码上已经测过，都通过了；还没有标主干的用户旅程。', record)

    def test_over_the_budget_the_others_wait_for_a_full_run_but_its_own_always_run(self):
        own = self.landed()
        self.feature('RF-103', '汇总', depends=['RF-101'])
        dep = self.landed_on('RF-103', [{'name': '汇总', 'input': {'a': 2, 'b': 2}, 'expect': 4}], '汇总')
        rf.commit_facts(self.root, 'facts')
        rid, place = self.requirement()
        plan = G.gate_plan(self.root, rid, place, budget_ms=1)
        self.assertEqual([s['id'] for s in plan['sets']], [own])
        self.assertEqual([s['id'] for s in plan['left_out']], [dep])
        self.assertIn('1 个超出时间预算没跑，留给全量', G.gate_line(dict(plan, ok=True, reused=0, preexisting=[])))

    def test_what_already_failed_on_the_main_line_does_not_block(self):
        self.feature('RF-103', '汇总', depends=['RF-101'])
        dep = self.landed_on('RF-103', [{'name': '一直错', 'input': {'a': 1, 'b': 1}, 'expect': 3}], '汇总')
        rf.commit_facts(self.root, 'facts')
        TR.testset_run(self.root, everything=True)  # 主线上本来就失败
        rid, place = self.requirement()
        self.work(place)
        out = G.gate_run(self.root, rid, place)
        self.assertTrue(out['ok'])
        self.assertEqual([f['id'] for f in out['preexisting']], [dep])
        self.assertIn(f'{dep} 在主线上原来就失败，不算这次的', G.gate_line(out))

    def test_the_user_can_skip_it_and_the_record_says_so(self):
        self.landed()
        rid, place = self.requirement()
        self.work(place, 'def add(a, b):\n    return a - b\n')  # 改坏了
        with self.assertRaises(G.GateFailed):
            rf.req_done(self.root, rid)
        rf.req_done(self.root, rid, skip_gate=True)
        record = (self.root / rf.load_requirements(self.root)[0]['file']).read_text(encoding='utf-8')
        self.assertIn('用户选择跳过合并前的检查，直接合并。', record)

    def test_the_command_line_says_gate_in_json_and_exits_5(self):
        self.landed()
        rid, place = self.requirement()
        self.work(place, 'def add(a, b):\n    return a - b\n')
        out, old = io.StringIO(), os.getcwd()
        os.chdir(self.root)
        try:
            with app_marker(), contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                code = rf.main(['req', 'done', rid, '--json'])
        finally:
            os.chdir(old)
        self.assertEqual(code, 5)
        told = json.loads(out.getvalue())
        self.assertFalse(told['gate']['ok'])
        self.assertEqual(told['gate']['failing'][0]['kind'], 'own')

    def test_only_the_user_marks_a_journey_as_trunk(self):
        self.journey('J-01', ['RF-102'])
        rf.commit_facts(self.root, 'facts')
        self.assertEqual(set_trunk(self.root, 'j-01')['trunk'], True)
        self.assertEqual(G.trunk_features(self.root)[0], ['RF-102'])
        self.assertIn('trunk: true', next((self.root / rf.DIR / 'journeys').glob('J-01-*.md')).read_text(encoding='utf-8'))
        set_trunk(self.root, 'J-01', on=False)
        self.assertEqual(G.trunk_features(self.root), ([], []))
        with self.assertRaises(ValueError):
            set_trunk(self.root, 'J-09')
        self.assertEqual(USER_COMMANDS[('map', 'trunk')], '把用户旅程设为主干或取消')


if __name__ == '__main__':
    unittest.main()

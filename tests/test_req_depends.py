"""前置需求（depends_on）：设置和校验、立项和完成被拦、立项时把主线同步进分支、拆分时带上先后。"""
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'backend'))
import rf  # noqa: E402
from relayforge.requirements import splits  # noqa: E402
from unittest import mock  # noqa: E402
from tests import test_rf  # noqa: E402  模块名引用：不把 test_rf 的测试类导进来，免得这里再跑一遍


class DependBase(test_rf.Base):
    """A git project with requirements (the setup and helpers of test_rf.RequirementTests, without its tests)."""
    write, start, commit_in = test_rf.RequirementTests.write, test_rf.RequirementTests.start, test_rf.RequirementTests.commit_in

    def setUp(self):
        super().setUp()
        env = mock.patch.dict(os.environ, {'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@example.com', 'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@example.com'})
        env.start()
        self.addCleanup(env.stop)
        for args in (['init', '-q', '-b', 'main'], ['add', '-A'], ['commit', '-qm', 'init']):
            subprocess.run(['git', *args], cwd=self.root, check=True, capture_output=True)

    def req(self, title):
        return rf.req_new(self.root, title)

    def view(self, made):
        return rf.find_requirement(self.root, made['id'])

    def finish(self, made):
        """Start it (if needed), commit some code on its branch and confirm it done (merged into main)."""
        if self.view(made)['state'] == 'draft':
            self.start(made)
        self.commit_in(made['worktree'], f'{made["id"]}.txt', made['title'])
        return rf.req_done(self.root, made['id'])


class DependTests(DependBase):
    def test_setting_and_removing_a_prerequisite_is_checked_and_recorded(self):
        a, b, c = self.req('截止日期'), self.req('每日汇总'), self.req('周报')
        made = rf.req_depend(self.root, b['id'], a['id'])
        self.assertEqual([d['id'] for d in made['depends_on']], [a['id']])
        self.assertEqual(made['waiting'], [a['id']])
        self.assertIn('设为前置需求', made['sections']['变更记录'][-1])
        self.assertEqual(self.view(a)['dependents'], [{'id': b['id'], 'title': '每日汇总', 'state': 'draft', 'ready': False, 'need': ''}])
        for ident, other, why in ((b['id'], b['id'], '自己'), (b['id'], 'R-20000101-0000', '没有找到'), (b['id'], a['id'], '已经是前置需求'),
                                  (a['id'], b['id'], '互相等待')):
            with self.assertRaisesRegex(ValueError, why):
                rf.req_depend(self.root, ident, other)
        rf.req_depend(self.root, c['id'], b['id'])
        with self.assertRaisesRegex(ValueError, '互相等待'):  # a ← b ← c: a waiting for c closes the loop
            rf.req_depend(self.root, a['id'], c['id'])
        made = rf.req_depend(self.root, b['id'], a['id'], remove=True)
        self.assertEqual((made['depends_on'], made['waiting']), ([], []))
        self.assertIn('去掉了前置需求', made['sections']['变更记录'][-1])
        with self.assertRaisesRegex(ValueError, '不是'):
            rf.req_depend(self.root, b['id'], a['id'], remove=True)
        from relayforge.core.trust import command_key
        self.assertEqual(command_key('req', 'depend'), ('req', 'depend'))  # 只由用户触发

    def test_a_prerequisite_says_what_is_needed_from_it(self):
        """每条依赖写一句要用到对方的什么：记在「前置需求」一节，两边都看得到；去掉依赖时一起去掉。"""
        a, b = self.req('统一的日期工具'), self.req('每日汇总')
        made = rf.req_depend(self.root, b['id'], a['id'], need='要用到它的 parseDate，统一解析用户输入的日期')
        self.assertEqual(made['depends_on'][0]['need'], '要用到它的 parseDate，统一解析用户输入的日期')
        self.assertEqual(self.view(a)['dependents'][0]['need'], '要用到它的 parseDate，统一解析用户输入的日期')
        text = (self.root / made['file']).read_text(encoding='utf-8')
        self.assertIn(f'## 前置需求\n- {a["id"]}：要用到它的 parseDate，统一解析用户输入的日期\n\n## 变更记录', text)
        self.assertIn('（要用到：要用到它的 parseDate', made['sections']['变更记录'][-1])
        made = rf.req_depend(self.root, b['id'], a['id'], need='要用到 formatDate')  # 已经是前置需求：只改这一句
        self.assertEqual(made['depends_on'][0]['need'], '要用到 formatDate')
        self.assertIn('写明了要用到《统一的日期工具》', made['sections']['变更记录'][-1])
        made = rf.req_depend(self.root, b['id'], a['id'], remove=True)
        self.assertNotIn('## 前置需求', (self.root / made['file']).read_text(encoding='utf-8'))

    def test_a_requirement_waits_to_start_and_starts_with_its_prerequisites_code(self):
        a, b = self.req('截止日期'), self.req('每日汇总')
        rf.req_depend(self.root, b['id'], a['id'])
        self.write(b, '验收条件', '- 每天早上发一封〔S-test#1〕')
        view = self.view(b)
        self.assertEqual(view['moves']['build'], '要等《截止日期》确认完成后才能开始开发')
        self.assertIsNone(view['moves']['dropped'])
        with self.assertRaisesRegex(ValueError, '还不能立项：要等《截止日期》'):
            rf.req_start(self.root, b['id'])
        with self.assertRaisesRegex(ValueError, '要等《截止日期》'):
            rf.req_set(self.root, b['id'], 'build', trusted=True)
        self.finish(a)
        view = self.view(b)
        self.assertEqual((view['waiting'], view['moves']['build'], view['depends_on'][0]['state']), ([], None, 'done'))
        self.assertTrue(any('改动：' in line for line in view['depends_on'][0]['finished']))
        self.assertEqual(self.view(a)['dependents'], [{'id': b['id'], 'title': '每日汇总', 'state': 'draft', 'ready': True, 'need': ''}])  # 合并后据此提醒「可以开始了」
        started = rf.req_start(self.root, b['id'])
        self.assertEqual(started['start_sync']['ok'], True)
        self.assertGreaterEqual(started['start_sync']['commits'], 1)
        self.assertTrue((Path(b['worktree']) / f'{a["id"]}.txt').exists(), 'b starts with a\'s code')
        self.assertIn('同步进了这个需求的分支', ''.join(started['sections']['变更记录']))

    def test_starting_without_prerequisites_still_catches_up_when_it_can_fast_forward(self):
        a, b = self.req('截止日期'), self.req('不相干')
        self.finish(a)
        started = self.start(b)
        self.assertTrue(started['start_sync']['ok'])
        self.assertTrue((Path(b['worktree']) / f'{a["id"]}.txt').exists())
        c = self.req('刚建的')
        self.assertIsNone(self.start(c)['start_sync'])  # already up to date: nothing to do, nothing written

    def test_a_started_requirement_given_a_prerequisite_cannot_be_finished_before_it(self):
        a, b = self.req('统一的日期工具'), self.req('截止日期')
        self.start(b)
        made = rf.req_depend(self.root, b['id'], a['id'])
        self.assertIn('才能确认完成（已经在开发了）', made['sections']['变更记录'][-1])
        self.assertIsNone(made['moves']['check'])  # 已经开工：只拦完成
        self.assertEqual(made['moves']['done'], '要等《统一的日期工具》确认完成后才能确认完成')
        self.commit_in(b['worktree'], 'b.txt', 'b')
        with self.assertRaisesRegex(ValueError, '要等《统一的日期工具》确认完成后才能确认完成'):
            rf.req_done(self.root, b['id'])
        self.finish(a)
        subprocess.run(['git', 'merge', '-q', '--no-edit', 'main'], cwd=b['worktree'], check=True, capture_output=True)
        self.assertEqual(rf.req_done(self.root, b['id'])['state'], 'done')

    def test_a_cancelled_prerequisite_keeps_it_waiting_and_says_so(self):
        a, b = self.req('截止日期'), self.req('每日汇总')
        rf.req_depend(self.root, b['id'], a['id'])
        self.write(b, '验收条件', '- 每天早上发一封〔S-test#1〕')
        rf.req_drop(self.root, a['id'])
        view = self.view(b)
        self.assertEqual((view['waiting'], view['dropped_deps']), ([a['id']], [a['id']]))
        self.assertIn('已取消', view['moves']['build'])
        with self.assertRaisesRegex(ValueError, '已经取消'):
            rf.req_depend(self.root, self.req('另一个')['id'], a['id'])
        self.assertEqual(rf.req_depend(self.root, b['id'], a['id'], remove=True)['waiting'], [])
        with self.assertRaisesRegex(ValueError, '已经完成，不用等'):
            rf.req_depend(self.root, b['id'], self.finish(self.req('做完了'))['id'])


class SplitOrderTests(DependBase):
    """拆分建议的每一项写明和来源需求的先后（order），同意时一次建好需求和依赖。"""

    def propose(self, items):
        session = 'abcd1234-session'
        rows = [{'type': 'user', 'message': {'role': 'user', 'content': '这个太大了'}},
                {'type': 'assistant', 'message': {'role': 'assistant', 'content': '建议拆分：\n```relayforge-split\n'
                                                  + json.dumps(items, ensure_ascii=False) + '\n```'}}]
        self.transcript.write_text('\n'.join(json.dumps(r, ensure_ascii=False) for r in rows) + '\n', encoding='utf-8')
        return session

    def item(self, title, order=None, waits=None, takes=None, needs=None):
        """One item of a proposal. Unless `needs` is given, every order it has says what is needed (要用到 + 对方)."""
        out = {'title': title, 'reason': '可以单独交付', 'scope': title + '的范围', 'existing': ''}
        if order:
            out['order'] = order
        if waits:
            out['waits'] = waits
        if takes:
            out['takes'] = takes
        if needs is None:
            needs = {k: '要用到' + k for k in (waits or []) + (takes or [])}
            if order in ('after', 'before'):
                needs[splits.HERE] = '要用到当前需求'
        if needs:
            out['needs'] = needs
        return out

    def test_agreeing_creates_the_requirements_with_their_order(self):
        parent, other = self.req('截止日期'), self.req('导出')
        session = self.propose([self.item('每日汇总', 'after'), self.item('日期工具', 'before'), self.item('周报', 'none', [other['id']])])
        preview = splits.review(self.root, str(self.transcript), session, parent['id'])
        self.assertEqual(([i['order'] for i in preview['items']], preview['problem']), (['after', 'before', 'none'], ''))
        self.assertEqual(preview['items'][2]['waits_info'], [{'id': other['id'], 'title': '导出', 'need': '要用到' + other['id']}])
        done = splits.review(self.root, str(self.transcript), session, parent['id'], approve=preview['id'])
        made = {i['title']: i['requirement'] for i in done['items']}
        found = {r['id']: r for r in rf.load_requirements(self.root)}
        self.assertEqual([d['id'] for d in found[made['每日汇总']]['depends_on']], [parent['id']])
        self.assertEqual([d['id'] for d in found[parent['id']]['depends_on']], [made['日期工具']])
        self.assertEqual([d['id'] for d in found[made['周报']]['depends_on']], [other['id']])
        self.assertIn('同意拆分时，用户把', found[made['每日汇总']]['sections']['变更记录'][-1])

    def test_the_user_changes_the_order_in_the_app_before_agreeing(self):
        parent = self.req('截止日期')
        session = self.propose([self.item('每日汇总', 'after'), self.item('日期工具', 'before')])
        preview = splits.review(self.root, str(self.transcript), session, parent['id'])
        done = splits.review(self.root, str(self.transcript), session, parent['id'], approve=preview['id'], orders='{"0": "none"}')
        made = {i['title']: i for i in done['items']}
        self.assertEqual((made['每日汇总']['order'], made['日期工具']['order']), ('none', 'before'))
        self.assertEqual(self.view({'id': made['每日汇总']['requirement']})['depends_on'], [])
        with self.assertRaisesRegex(ValueError, '先后只能是'):
            splits.apply_orders(preview['items'], '{"0": "later"}', parent['id'])

    def test_an_order_that_would_wait_in_a_circle_creates_nothing(self):
        parent, other = self.req('截止日期'), self.req('导出')
        rf.req_depend(self.root, other['id'], parent['id'])  # 导出 already waits for 截止日期
        session = self.propose([self.item('日期工具', 'before', [other['id']])])  # 截止日期 → 日期工具 → 导出 → 截止日期
        preview = splits.review(self.root, str(self.transcript), session, parent['id'])
        self.assertIn('互相等待', preview['problem'])
        before = len(rf.load_requirements(self.root))
        with self.assertRaisesRegex(ValueError, '拆分建议的先后有问题'):
            splits.review(self.root, str(self.transcript), session, parent['id'], approve=preview['id'])
        self.assertEqual(len(rf.load_requirements(self.root)), before)

    def test_a_chat_splits_out_requirements_that_wait_for_each_other(self):
        """日常聊天里没有来源需求：一次拆出的几条之间的先后用「#序号」写，同意时按建好的编号记成前置需求。"""
        other = self.req('导出')
        session = self.propose([self.item('日期工具'), self.item('每日汇总', waits=['#1']),
                                self.item('周报', waits=['#2', other['id']])])
        preview = splits.review(self.root, str(self.transcript), session)
        self.assertEqual(preview['problem'], '')
        self.assertEqual(preview['items'][1]['waits_info'], [{'id': '', 'title': '日期工具', 'need': '要用到#1'}])
        self.assertEqual(preview['items'][2]['waits_info'], [{'id': '', 'title': '每日汇总', 'need': '要用到#2'},
                                                             {'id': other['id'], 'title': '导出', 'need': '要用到' + other['id']}])
        done = splits.review(self.root, str(self.transcript), session, approve=preview['id'])
        made = {i['title']: i['requirement'] for i in done['items']}
        self.assertEqual(done['items'][2]['waits_info'], [{'id': made['每日汇总'], 'title': '每日汇总', 'need': '要用到#2'},
                                                          {'id': other['id'], 'title': '导出', 'need': '要用到' + other['id']}])
        found = {r['id']: r for r in rf.load_requirements(self.root)}
        self.assertEqual([d['id'] for d in found[made['日期工具']]['depends_on']], [])
        self.assertEqual([d['id'] for d in found[made['每日汇总']]['depends_on']], [made['日期工具']])
        self.assertEqual([d['id'] for d in found[made['周报']]['depends_on']], [made['每日汇总'], other['id']])
        self.write(found[made['每日汇总']], '验收条件', '- 每天早上发一封〔S-test#1〕')
        with self.assertRaisesRegex(ValueError, '要等《日期工具》'):
            rf.req_start(self.root, made['每日汇总'])

    def test_items_of_one_proposal_that_wait_in_a_circle_create_nothing(self):
        session = self.propose([self.item('日期工具', waits=['#2']), self.item('每日汇总', waits=['#1'])])
        preview = splits.review(self.root, str(self.transcript), session)
        self.assertIn('互相等待', preview['problem'])
        before = len(rf.load_requirements(self.root))
        with self.assertRaisesRegex(ValueError, '拆分建议的先后有问题'):
            splits.review(self.root, str(self.transcript), session, approve=preview['id'])
        self.assertEqual(len(rf.load_requirements(self.root)), before)

    def test_a_wait_for_itself_or_for_an_item_that_is_not_there_is_not_a_proposal(self):
        for waits in (['#1'], ['#3'], ['#0'], ['1'], ['#a']):
            self.assertEqual(splits.proposal_items(json.dumps([self.item('日期工具', waits=waits), self.item('每日汇总')])), [], waits)
        self.assertEqual(splits.proposal_items(json.dumps([self.item('日期工具'), self.item('每日汇总', waits=['#1'])]))[1]['waits'], ['#1'])

    def test_an_item_written_twice_is_one_requirement_and_waits_point_to_it(self):
        session = self.propose([self.item('日期工具'), self.item('日期工具'), self.item('每日汇总', waits=['#2'])])
        preview = splits.review(self.root, str(self.transcript), session)
        self.assertEqual([i['title'] for i in preview['items']], ['日期工具', '每日汇总'])
        done = splits.review(self.root, str(self.transcript), session, approve=preview['id'])
        made = {i['title']: i['requirement'] for i in done['items']}
        self.assertEqual([d['id'] for d in self.view({'id': made['每日汇总']})['depends_on']], [made['日期工具']])

    def test_the_part_that_needs_a_prerequisite_is_split_out_and_takes_the_wait_with_it(self):
        """来源需求只有一部分要等前置需求：拆出去的那条接过这条依赖，来源需求马上能开始。"""
        upstream, parent = self.req('统一的日期工具'), self.req('截止日期')
        rf.req_depend(self.root, parent['id'], upstream['id'])
        self.write(self.view(parent), '验收条件', '- 能设截止日期〔S-test#1〕')
        session = self.propose([self.item('截止日期的时区换算', 'after', takes=[upstream['id']])])
        preview = splits.review(self.root, str(self.transcript), session, parent['id'])
        self.assertEqual((preview['problem'], preview['items'][0]['takes_info']), ('', [{'id': upstream['id'], 'title': '统一的日期工具', 'need': '要用到' + upstream['id']}]))
        done = splits.review(self.root, str(self.transcript), session, parent['id'], approve=preview['id'])
        made = done['items'][0]['requirement']
        self.assertEqual(sorted(d['id'] for d in self.view({'id': made})['depends_on']), sorted([parent['id'], upstream['id']]))
        source = self.view(parent)
        self.assertEqual((source['depends_on'], source['waiting'], source['moves']['build']), ([], [], None))
        self.assertIn(f'要等它的部分拆成了《截止日期的时区换算》（{made}），由那边来等', source['sections']['变更记录'][-1])
        self.assertEqual(rf.req_start(self.root, parent['id'])['state'], 'open')

    def test_a_split_untangles_two_requirements_that_would_wait_for_each_other(self):
        """《导出》在等《截止日期》，《截止日期》又有一部分要用到《导出》：不设前置需求，把那部分拆出去等《导出》。"""
        parent, other = self.req('截止日期'), self.req('导出')
        rf.req_depend(self.root, other['id'], parent['id'])
        with self.assertRaisesRegex(ValueError, '互相等待'):
            rf.req_depend(self.root, parent['id'], other['id'])
        session = self.propose([self.item('截止日期导出到日历', 'after', waits=[other['id']])])
        preview = splits.review(self.root, str(self.transcript), session, parent['id'])
        self.assertEqual(preview['problem'], '')
        done = splits.review(self.root, str(self.transcript), session, parent['id'], approve=preview['id'])
        made = done['items'][0]['requirement']
        self.assertEqual(sorted(d['id'] for d in self.view({'id': made})['depends_on']), sorted([parent['id'], other['id']]))

    def test_taking_a_prerequisite_the_source_does_not_have_creates_nothing(self):
        parent, other = self.req('截止日期'), self.req('导出')
        session = self.propose([self.item('截止日期导出', 'after', takes=[other['id']])])
        preview = splits.review(self.root, str(self.transcript), session, parent['id'])
        self.assertIn('不是这个需求的前置需求', preview['problem'])
        before = len(rf.load_requirements(self.root))
        with self.assertRaisesRegex(ValueError, '接不过去'):
            splits.review(self.root, str(self.transcript), session, parent['id'], approve=preview['id'])
        self.assertEqual(len(rf.load_requirements(self.root)), before)
        session = self.propose([self.item('聊天里拆的', takes=[other['id']])])  # 日常聊天没有来源需求：takes 不算数
        self.assertEqual(splits.review(self.root, str(self.transcript), session)['items'][0]['takes'], [])

    def test_every_order_says_what_is_needed_and_it_is_recorded(self):
        """拆分建议的每条先后都要写一句要用到对方的什么：漏写的不能同意；写了的记进新需求的「前置需求」一节。"""
        parent, other = self.req('截止日期'), self.req('导出')
        bare = self.propose([self.item('每日汇总', 'after', waits=[other['id']], needs={})])
        preview = splits.review(self.root, str(self.transcript), bare, parent['id'])
        self.assertIn('没写要用到对方的什么：《每日汇总》要等《导出》、《每日汇总》和当前需求的先后', preview['problem'])
        before = len(rf.load_requirements(self.root))
        with self.assertRaisesRegex(ValueError, '让 AI 在拆分建议的 needs 里补上'):
            splits.review(self.root, str(self.transcript), bare, parent['id'], approve=preview['id'])
        self.assertEqual(len(rf.load_requirements(self.root)), before)
        session = self.propose([self.item('每日汇总', 'after', waits=[other['id']],
                                          needs={other['id']: '导出的 JSON 格式', splits.HERE: '任务上的截止日期字段'})])
        preview = splits.review(self.root, str(self.transcript), session, parent['id'])
        self.assertEqual(preview['problem'], '')
        self.assertEqual((preview['items'][0]['order_need'], preview['items'][0]['waits_info'][0]['need']), ('任务上的截止日期字段', '导出的 JSON 格式'))
        done = splits.review(self.root, str(self.transcript), session, parent['id'], approve=preview['id'])
        made = self.view({'id': done['items'][0]['requirement']})
        self.assertEqual({d['id']: d['need'] for d in made['depends_on']}, {parent['id']: '任务上的截止日期字段', other['id']: '导出的 JSON 格式'})
        self.assertEqual(self.view(parent)['dependents'][0]['need'], '任务上的截止日期字段')

    def test_an_order_the_user_changed_needs_no_sentence_and_drops_the_ai_one(self):
        """用户在应用里改了和当前需求的先后：AI 那句是按原来的方向写的，不再用，也不要求补。"""
        parent = self.req('截止日期')
        session = self.propose([self.item('日期工具', 'before', needs={})])
        preview = splits.review(self.root, str(self.transcript), session, parent['id'])
        self.assertIn('和当前需求的先后', preview['problem'])
        done = splits.review(self.root, str(self.transcript), session, parent['id'], approve=preview['id'], orders='{"0": "after"}')
        made = self.view({'id': done['items'][0]['requirement']})
        self.assertEqual([(d['id'], d['need']) for d in made['depends_on']], [(parent['id'], '')])
        session = self.propose([self.item('周报', 'before', needs={splits.HERE: '周报的汇总接口'})])
        preview = splits.review(self.root, str(self.transcript), session, parent['id'])
        done = splits.review(self.root, str(self.transcript), session, parent['id'], approve=preview['id'], orders='{"0": "after"}')
        self.assertEqual(self.view({'id': done['items'][0]['requirement']})['depends_on'][0]['need'], '')

    def test_a_proposal_without_orders_keeps_the_id_it_had_before_orders_existed(self):
        items = splits.proposal_items(json.dumps([self.item('每日汇总')]))
        self.assertEqual(items[0]['order'], 'none')
        legacy = [{k: v for k, v in items[0].items() if k not in ('order', 'waits', 'takes', 'needs')}]
        import hashlib
        self.assertEqual(splits.proposal_key('s', 2, items), hashlib.sha256(json.dumps(['s', 2, legacy], sort_keys=True).encode()).hexdigest())
        self.assertEqual(splits.proposal_items(json.dumps([self.item('x', 'sideways')])), [])


if __name__ == '__main__':
    unittest.main()

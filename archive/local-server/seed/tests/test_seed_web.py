"""Version banner rendering and existing console controls."""
import unittest
from unittest.mock import call, patch

import seed


class VersionPageTests(unittest.TestCase):
    BOOT = '123456789' + 'a' * 31
    # The shared prefix also catches accidental comparison of shortened SHAs.
    MAIN = '123456789' + 'b' * 31

    def jobs(self):
        def event(state, seq, data):
            return dict(state=state, seq=seq, recorded_at='now', data=data)

        return {
            'running-task': [event('queued', 1, {'goal': '执行中的需求'}),
                             event('working', 2, {'progress': {
                                 'phase': 'Worker', 'started_at': '2026-01-01T00:00:00+00:00',
                                 'activity': '命令执行开始', 'last_activity_at': 'now',
                                 'run_id': 'run-example', 'log_path': '.seed/logs/example.jsonl'}})],
            'waiting-task': [event('queued', 1, {'goal': '待裁定需求'}),
                             event('waiting', 2, {'question': '如何继续？'})],
            'review-task': [event('queued', 1, {'goal': '待验收需求'}),
                            event('review', 2, {'candidate': self.MAIN, 'evidence': '验证记录'})],
        }

    def assert_versions_and_controls(self, page, main):
        self.assertIn('服务启动时加载的提交：<code>' + self.BOOT + '</code>', page)
        self.assertIn('main 当前提交：<code>' + main + '</code>', page)
        self.assertLess(page.index('aria-label="代码版本"'), page.index('name="goal"'))
        for text in ('最近活动', '命令执行开始', 'run-example', '.seed/logs/example.jsonl',
                     '等待你裁定', '如何继续？', '请选择处理方式', '提交选择',
                     '/diff/review-task', '查看候选代码差异', '验收通过，启用此提交',
                     'name="action" value="activate"', '退回修改', '验证记录',
                     "textarea:focus", '.some(x=>x.value)', '10000'):
            self.assertIn(text, page)

    def test_failure_is_recovery_not_product_decision(self):
        import seed_ui
        jobs = {'broken': [dict(state='failed', seq=2, recorded_at='now',
                               data={'goal': '开发任务', 'error': 'Agent 退出码 1'})]}
        page = seed_ui.task_detail('broken', jobs['broken'], None, jobs,
                                   seed.form, seed.field, seed.FIELDS)
        self.assertIn('执行故障，无需产品裁定', page)
        self.assertIn('交给 Agent 排查并修复', page)
        self.assertNotIn('你的决定或需要补充的信息', page)
        self.assertNotIn('裁定或重试说明', page)
        self.assertIn('Agent 退出码 1', page)

    def test_matching_versions_render_synced_and_preserve_controls(self):
        # HEAD deliberately differs: only the local main ref is relevant.
        def git(root, *args):
            self.assertEqual(root, seed.engine.ROOT)
            return self.BOOT if args == ('rev-parse', 'refs/heads/main') else self.MAIN

        with patch.object(seed.engine, 'git', side_effect=git) as read:
            page = seed.page(self.jobs(), self.BOOT)
        read.assert_called_once_with(seed.engine.ROOT, 'rev-parse', 'refs/heads/main')
        self.assert_versions_and_controls(page, self.BOOT)
        self.assertIn('运行版本已同步', page)
        self.assertNotIn('需要重启以加载新版本', page)
        self.assertNotIn('Ctrl-C', page)

    def test_changed_main_renders_restart_reason_and_service_instructions(self):
        # HEAD equals boot, so a HEAD comparison would miss the changed main.
        def git(root, *args):
            self.assertEqual(root, seed.engine.ROOT)
            return self.MAIN if args == ('rev-parse', 'refs/heads/main') else self.BOOT

        with patch.object(seed.engine, 'git', side_effect=git) as read:
            page = seed.page(self.jobs(), self.BOOT)
        read.assert_called_once_with(seed.engine.ROOT, 'rev-parse', 'refs/heads/main')
        self.assert_versions_and_controls(page, self.MAIN)
        self.assertNotIn('运行版本已同步', page)
        for text in ('需要重启以加载新版本', '当前 Project Guide 服务仍运行启动时加载的代码',
                     '暂停接手 queued（等待执行）和 answered（已回复，等待接手）队列',
                     '重启加载新代码后再继续接手', '启动本服务的终端按 Ctrl-C',
                     '原项目目录', '原数据库环境和原启动参数', 'python3 -B archive/local-server/seed/seed.py',
                     '--port', '原端口值'):
            self.assertIn(text, page)
        for text in ('重启电脑', '重启 PostgreSQL', 'kill ', '所有运行中任务已暂停'):
            self.assertNotIn(text, page)

    def test_refresh_reads_main_again_with_same_boot(self):
        with patch.object(seed.engine, 'git', side_effect=[self.BOOT, self.MAIN]) as read:
            before = seed.page({}, self.BOOT)
            after = seed.page({}, self.BOOT)
        self.assertEqual(read.call_args_list, [
            call(seed.engine.ROOT, 'rev-parse', 'refs/heads/main')]*2)
        self.assertIn('运行版本已同步', before)
        self.assertIn('需要重启以加载新版本', after)
        self.assertIn('服务启动时加载的提交：<code>' + self.BOOT + '</code>', after)
        self.assertIn('main 当前提交：<code>' + self.MAIN + '</code>', after)

    def test_git_read_failure_propagates_to_existing_error_handler(self):
        with patch.object(seed.engine, 'git', side_effect=RuntimeError('main unavailable')):
            with self.assertRaisesRegex(RuntimeError, 'main unavailable'):
                seed.page({}, self.BOOT)

    def test_version_values_are_html_escaped(self):
        with patch.object(seed.engine, 'git', return_value='<main>'):
            page = seed.page({}, '<boot>')
        self.assertIn('<code>&lt;boot&gt;</code>', page)
        self.assertIn('<code>&lt;main&gt;</code>', page)

    def test_successful_task_keeps_prior_error_in_history_only(self):
        rows = [dict(seq=1, state='queued', recorded_at='now', data={'goal': 'retry', 'acceptance': 'works'}),
                dict(seq=2, state='failed', recorded_at='now', data={'error': 'old timeout'}),
                dict(seq=3, state='activated', recorded_at='now', data={'candidate': self.MAIN})]
        with patch.object(seed.engine, 'git', return_value=self.BOOT):
            page = seed.page({'retry-task': rows}, self.BOOT)
        self.assertNotIn('<h3>中断原因</h3>', page)
        self.assertIn('old timeout', page)
        self.assertIn('技术信息与完整事件', page)

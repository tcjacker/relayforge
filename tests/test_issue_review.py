"""问题页的「刷新」：AI 在后台复查待处理问题，查完由用户勾选、一次确认处理（R-20261121-5b21）。"""
import json
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'backend'))
import rf  # noqa: E402
from relayforge.issues import review as review_module  # noqa: E402
from relayforge.issues import review as review_module  # noqa: E402
from tests.app_fixture import app_marker  # noqa: E402
from tests.test_issues import IssueBase, git  # noqa: E402

# 假的模型：按标准输入里「编号：#00x」回答这条问题的结论，没有编号的是最后的归并；每次调用记一行
STUB = '''import json, os, re, sys
payload = sys.stdin.read()
answers = json.load(open(os.environ["RF_REVIEW_ANSWERS"], encoding="utf-8"))
hit = re.search(r"编号：(#\\d+)", payload)
key = hit.group(1) if hit else "group"
with open(os.environ["RF_REVIEW_CALLS"], "a", encoding="utf-8") as f:
    settings = json.loads(sys.argv[sys.argv.index("--settings") + 1]) if "--settings" in sys.argv else None
    f.write(json.dumps({"key": key, "cwd": os.getcwd(), "token": os.environ.get("RELAYFORGE_APP_TOKEN"), "settings": settings}) + "\\n")
answer = answers.get(key, {"groups": []} if key == "group" else None)
if answer == "FAIL":
    print("模型出错了", file=sys.stderr)
    sys.exit(2)
print(json.dumps(answer, ensure_ascii=False))
'''


def said(verdict, **more):
    return dict({'verdict': verdict, 'detail': f'{verdict} 的依据', 'ran': f'{verdict} 时重跑了测试', 'fault': f'{verdict} 的故障'}, **more)


class ReviewBase(IssueBase):
    def setUp(self):
        super().setUp()
        stub = Path(self.tmp.name) / 'review_stub.py'
        stub.write_text(STUB, encoding='utf-8')
        self.answers = Path(self.tmp.name) / 'answers.json'
        self.calls = Path(self.tmp.name) / 'calls.jsonl'
        env = mock.patch.dict(os.environ, {'RELAYFORGE_EXTRACTOR': json.dumps([sys.executable, str(stub)]),
                                           'RF_REVIEW_ANSWERS': str(self.answers), 'RF_REVIEW_CALLS': str(self.calls)})
        env.start()
        self.addCleanup(env.stop)

    def issues(self, count):
        return [rf.issue_new(self.root, f'问题 {n}', f'描述 {n}', '你')['id'] for n in range(1, count + 1)]

    def say(self, **answers):
        self.answers.write_text(json.dumps({k.replace('n', '#', 1): v for k, v in answers.items()}, ensure_ascii=False), encoding='utf-8')

    def called(self):
        try:
            return [json.loads(x) for x in self.calls.read_text(encoding='utf-8').splitlines()]
        except OSError:
            return []

    def state(self, ident):
        found = rf.issue_show(self.root, ident)
        return found['state'], found['closed_as'], found['duplicate_of']

    def to_state(self, ident, state):
        path, meta, sections = rf.find_issue(self.root, ident)
        meta['state'] = state
        rf.write_doc(path, meta, sections)

    def entries(self, ident, kind):
        return [e for e in rf.issue_show(self.root, ident)['thread'] if e.get('t') == kind]


class ScopeTests(ReviewBase):
    def test_only_pending_issues_are_reviewed(self):  # 用例 1
        ids = self.issues(7)
        for ident, state in zip(ids[3:], ('fixing', 'review', 'resolved', 'closed')):
            self.to_state(ident, state)
        self.say(n001=said('still', steps='打开', observed='报错'), n002=said('unclear'), n003=said('gone'))
        job = rf.issue_review(self.root)
        self.assertEqual(job['state'], 'ready')
        self.assertEqual([x['num'] for x in job['items']], ['#001', '#002', '#003'])
        self.assertEqual((job['done'], len(job['items'])), (3, 3))
        self.assertEqual(sorted(x['key'] for x in self.called()), ['#001', '#002', '#003', 'group'])
        self.assertEqual({row['id'] for row in job['rows']}, set(ids[:3]))

    def test_nothing_pending_starts_no_ai(self):  # 用例 2
        for ident in self.issues(2):
            self.to_state(ident, 'resolved')
        job = rf.issue_review(self.root)
        self.assertEqual((job['state'], job['message']), ('empty', '当前没有待处理问题。'))
        self.assertEqual(self.called(), [])
        self.assertEqual(rf.issue_review_status(self.root), {'state': 'idle'})

    def test_investigation_runs_in_a_copy_of_the_commit_without_the_app_token(self):
        self.issues(1)
        (self.root / 'code.txt').write_text('committed\n', encoding='utf-8')
        git(self.root, 'add', 'code.txt')
        git(self.root, 'commit', '-qm', 'code')
        (self.root / 'code.txt').write_text('uncommitted\n', encoding='utf-8')
        self.say(n001=said('gone'))
        with mock.patch.dict(os.environ, {'RELAYFORGE_APP_TOKEN': 'secret'}):
            rf.issue_review(self.root)
        first = self.called()[0]
        self.assertNotEqual(Path(first['cwd']).resolve(), self.root)
        self.assertIsNone(first['token'])
        self.assertFalse(Path(first['cwd']).exists())  # 查完副本就删掉
        sandbox = first['settings']['sandbox']  # 能跑 shell 命令的复查放进系统的沙箱，只能写副本，不许跳出
        self.assertTrue(sandbox['enabled'])
        self.assertFalse(sandbox['allowUnsandboxedCommands'])
        self.assertEqual(Path(sandbox['filesystem']['allowWrite'][0]).resolve(), Path(first['cwd']).resolve())
        self.assertNotIn(str(self.root), sandbox['filesystem']['allowWrite'])

    def test_claude_gets_rules_that_keep_it_in_the_copy(self):
        rules = rf.issues.review._permissions('/tmp/copy', self.root)
        self.assertIn('Bash', rules['allow'])
        self.assertIn('Edit(//tmp/copy/**)', rules['allow'])
        self.assertIn(f'Edit(/{self.root}/**)', rules['deny'])
        self.assertIn('Bash(git commit:*)', rules['deny'])
        self.assertIn('Bash(python3 .relayforge/bin/rf.py issue review-apply:*)', rules['deny'])
        for rule in rules['allow'] + rules['deny']:  # Claude Code 对不上的写法：Write(路径)；* 和结尾的 :* 混用
            self.assertFalse(rule.startswith('Write('), rule)
            body = rule[5:-1] if rule.startswith('Bash(') else ''
            self.assertFalse(body.endswith(':*') and '*' in body[:-2], rule)


class BackgroundTests(ReviewBase):
    def cli(self, *args):
        script = Path(__file__).resolve().parent.parent / 'backend' / 'rf.py'
        return subprocess.run([sys.executable, str(script), 'issue', *args, '--root', str(self.root), '--json'],
                              capture_output=True, text=True, stdin=subprocess.DEVNULL)

    def test_app_starts_it_in_a_separate_process_and_reads_progress(self):
        self.issues(2)
        self.say(n001=said('gone'), n002=said('stale'))
        with app_marker(subprocess=True):
            started = self.cli('review')
        self.assertEqual(started.returncode, 0, started.stderr)
        first = json.loads(started.stdout)
        self.assertEqual((first['state'], len(first['items'])), ('running', 2))
        for _ in range(200):
            status = json.loads(self.cli('review-status').stdout)
            if status['state'] != 'running':
                break
            time.sleep(0.1)
        self.assertEqual((status['state'], status['id'], status['done']), ('ready', first['id'], 2))
        self.assertTrue(status['claimed'])
        self.assertEqual(self.cli('review-run', first['id']).returncode, 1)  # 只能接手一次

    def test_sessions_cannot_start_or_drop_a_review(self):
        self.issues(1)
        for action in ('review', 'review-discard'):
            done = self.cli(action)
            self.assertEqual(done.returncode, 1)
            self.assertIn('只能由你在应用里做', done.stderr)
        self.assertEqual(self.called(), [])


class OnlyOneReviewTests(ReviewBase):
    def test_running_review_is_returned_not_started_again(self):  # 用例 3
        ids = self.issues(5)
        job = {'id': 'RV-1', 'state': 'running', 'pid': os.getpid(), 'branch': 'main', 'commit': 'x', 'done': 2,
               'current': ids[2], 'items': [{'id': i, 'num': rf.num(i), 'title': '', 'status': 'queued'} for i in ids], 'rows': []}
        rf.issues.review._write_job(self.root, job)
        again = rf.issue_review(self.root)
        self.assertEqual((again['id'], again['done'], again['state']), ('RV-1', 2, 'running'))
        self.assertEqual(self.called(), [])

    def test_waiting_results_are_returned_not_reviewed_again(self):  # 用例 4
        self.issues(2)
        self.say(n001=said('gone'), n002=said('stale'))
        first = rf.issue_review(self.root)
        calls = len(self.called())
        again = rf.issue_review(self.root)
        self.assertEqual((again['id'], again['state']), (first['id'], 'ready'))
        self.assertEqual(len(self.called()), calls)

    def test_a_review_whose_process_died_counts_as_interrupted(self):
        self.issues(1)
        rf.issues.review._write_job(self.root, {'id': 'RV-1', 'state': 'running', 'pid': 999999999, 'items': [], 'rows': []})
        status = rf.issue_review_status(self.root)
        self.assertEqual(status['state'], 'failed')
        self.assertIn('中断', status['error'])
        self.say(n001=said('gone'))
        self.assertEqual(rf.issue_review(self.root)['state'], 'ready')


class ResultTests(ReviewBase):
    def test_five_kinds_each_with_evidence(self):  # 用例 5
        self.issues(5)
        self.say(n001=said('gone'), n002=said('stale'), n003=said('still', steps='步骤', observed='结果'), n004=said('still', steps='步骤', observed='结果'),
                 n005=said('unclear'), group={'groups': [{'keep': '#004', 'merge': ['#003'], 'reason': '同一个报错'}]})
        job = rf.issue_review(self.root)
        rows = {row['num']: row for row in job['rows']}
        self.assertEqual({n: r['kind'] for n, r in rows.items()},
                         {'#001': 'gone', '#002': 'stale', '#003': 'merge', '#004': 'still', '#005': 'unclear'})
        self.assertEqual(rows['#003']['target'], 'I-004')
        for row in rows.values():
            self.assertTrue(row['detail'] and row['ran'])
        self.assertEqual({n for n, r in rows.items() if 'stamp' in r}, {'#001', '#002', '#003'})

    def test_all_still_leaves_nothing_to_confirm(self):  # 用例 6
        self.issues(3)
        self.say(**{f'n00{i}': said('still', steps='s', observed='o') for i in (1, 2, 3)})
        job = rf.issue_review(self.root)
        self.assertEqual({row['kind'] for row in job['rows']}, {'still'})
        self.assertFalse(any('stamp' in row for row in job['rows']))

    def test_several_merge_into_one_original(self):  # 用例 7
        self.issues(3)
        self.say(n001=said('still', steps='s', observed='o'), n002=said('still', steps='s', observed='o'), n003=said('unclear'),
                 group={'groups': [{'keep': '#001', 'merge': ['#002', '#003', '#001'], 'reason': '同一个故障'}]})
        rows = {row['num']: row for row in rf.issue_review(self.root)['rows']}
        self.assertEqual([(n, r['target']) for n, r in rows.items() if r['kind'] == 'merge'], [('#002', 'I-001'), ('#003', 'I-001')])
        self.assertEqual(rows['#001']['kind'], 'still')

    def test_still_needs_steps_and_result(self):
        self.issues(1)
        self.say(n001=said('still'))
        job = rf.issue_review(self.root)
        self.assertEqual(job['state'], 'failed')
        self.assertIn('复现步骤', job['error'])


class ApplyTests(ReviewBase):
    def setUp(self):
        super().setUp()
        self.issues(5)
        self.say(n001=said('gone'), n002=said('stale'), n003=said('still', steps='步骤', observed='结果'),
                 n004=said('still', steps='打开设置', observed='报错 X'), n005=said('unclear'),
                 group={'groups': [{'keep': '#004', 'merge': ['#003'], 'reason': '同一个报错'}]})
        self.job = rf.issue_review(self.root)

    def apply(self, *ids):
        return rf.issue_review_apply(self.root, {'review': self.job['id'], 'selected': [f'I-00{i}' for i in ids]})

    def pending(self):
        return sorted(i['num'] for i in rf.issue_list(self.root)['issues'] if i['state'] == 'todo')

    def test_confirm_all_selected(self):  # 用例 8、11
        done = self.apply(1, 2, 3)
        self.assertEqual((sorted(done['applied']), done['skipped']), (['I-001', 'I-002', 'I-003'], []))
        self.assertEqual(self.state('#1'), ('closed', 'not_issue', None))
        self.assertEqual(self.state('#2'), ('resolved', None, None))
        self.assertEqual(self.state('#3'), ('closed', 'duplicate', 'I-004'))
        self.assertEqual(self.pending(), ['#004', '#005'])
        self.assertEqual(rf.issue_review_status(self.root), {'state': 'idle'})

    def test_unselected_stay_pending(self):  # 用例 9
        self.apply(1, 3)
        self.assertEqual(self.state('#2'), ('todo', None, None))
        self.assertEqual(self.pending(), ['#002', '#004', '#005'])

    def test_closing_results_changes_nothing(self):  # 用例 10
        rf.issue_review_discard(self.root)
        self.assertEqual(self.pending(), ['#001', '#002', '#003', '#004', '#005'])
        self.assertEqual(rf.issue_review_status(self.root), {'state': 'idle'})

    def test_still_and_unclear_cannot_be_confirmed(self):  # 用例 11
        with self.assertRaisesRegex(ValueError, '只能确认处理'):
            self.apply(4)
        self.assertEqual(self.pending(), ['#001', '#002', '#003', '#004', '#005'])

    def test_changed_after_review_is_skipped_others_go_through(self):  # 用例 12
        self.to_state('I-002', 'fixing')
        done = self.apply(1, 2)
        self.assertEqual(done['applied'], ['I-001'])
        self.assertEqual([x['id'] for x in done['skipped']], ['I-002'])
        self.assertIn('需要重新复查', done['skipped'][0]['reason'])
        self.assertEqual(self.state('#2'), ('fixing', None, None))

    def test_a_reply_after_review_also_counts_as_changed(self):
        rf.issue_reply(self.root, '#1', '其实还在')
        self.assertEqual([x['id'] for x in self.apply(1)['skipped']], ['I-001'])

    def test_original_changed_skips_the_merge(self):
        self.to_state('I-004', 'fixing')
        done = self.apply(3)
        self.assertEqual((done['applied'], [x['id'] for x in done['skipped']]), ([], ['I-003']))

    def test_old_review_cannot_be_applied(self):
        with self.assertRaisesRegex(ValueError, '重新刷新'):
            rf.issue_review_apply(self.root, {'review': 'RV-old', 'selected': ['I-001']})

    def test_every_reviewed_issue_keeps_the_conclusion(self):  # 用例 13
        self.apply(2)
        for ident, kind in (('I-002', 'stale'), ('I-004', 'still'), ('I-005', 'unclear'), ('I-003', 'merge')):
            entry = self.entries(ident, 'review')[-1]
            self.assertEqual(entry['kind'], kind)
            self.assertTrue(entry['text'] and entry['ran'])

    def test_still_keeps_steps_for_the_fix_session(self):  # 用例 14
        entry = self.entries('I-004', 'review')[-1]
        self.assertEqual((entry['steps'], entry['observed']), ('打开设置', '报错 X'))
        brief = rf.issue_brief(self.root, '#4')
        self.assertIn('打开设置', brief)
        self.assertIn('报错 X', brief)


class ChangedDuringReviewTests(ReviewBase):
    """复查查某一条时，这条（或它要并入的原单）又有了新回复：AI 没看到，旧结论不能拿来确认。"""

    def review_replying(self, **replies):
        """Run the review; while the model looks at issue #00n, reply replies['n00n'] on the issue named there."""
        real = review_module.run_model

        def model(home, instruction, payload, **more):
            for key, (target, text) in replies.items():
                if f'编号：#{key[1:]}' in payload:
                    rf.issue_reply(self.root, target, text)
            return real(home, instruction, payload, **more)

        with mock.patch.object(review_module, 'run_model', model):
            return rf.issue_review(self.root)

    def test_reply_while_reviewing_voids_the_conclusion(self):
        self.issues(2)
        self.say(n001=said('gone'), n002=said('gone'))
        job = self.review_replying(n001=('#1', '其实还在，补一张截图'))
        rows = {row['num']: row for row in job['rows']}
        self.assertEqual((rows['#001']['kind'], 'stamp' in rows['#001']), ('recheck', False))
        self.assertIn('重新复查', rows['#001']['detail'])
        self.assertEqual((rows['#002']['kind'], 'stamp' in rows['#002']), ('gone', True))
        self.assertEqual(self.entries('I-001', 'review')[-1]['kind'], 'recheck')
        with self.assertRaisesRegex(ValueError, '只能确认处理'):
            rf.issue_review_apply(self.root, {'review': job['id'], 'selected': ['I-001']})
        self.assertEqual(self.state('#1'), ('todo', None, None))

    def test_reply_while_reviewing_another_issue_counts_too(self):
        """#001 查完、整次复查还没结束时补的回复：AI 查 #001 时同样没看到。"""
        self.issues(2)
        self.say(n001=said('gone'), n002=said('gone'))
        job = self.review_replying(n002=('#1', '其实还在'))
        self.assertEqual({row['num']: row['kind'] for row in job['rows']}, {'#001': 'recheck', '#002': 'gone'})

    def test_original_changed_while_reviewing_drops_the_merge(self):
        self.issues(2)
        self.say(n001=said('still', steps='s', observed='o'), n002=said('stale'),
                 group={'groups': [{'keep': '#001', 'merge': ['#002'], 'reason': '同一个故障'}]})
        job = self.review_replying(n002=('#1', '原单补充：只在夜间模式下出现'))
        rows = {row['num']: row for row in job['rows']}
        self.assertEqual(rows['#001']['kind'], 'recheck')
        self.assertEqual((rows['#002']['kind'], rows['#002'].get('target')), ('stale', None))

    def test_the_brief_tells_the_fix_session_the_review_is_void(self):
        self.issues(1)
        self.say(n001=said('gone'))
        self.review_replying(n001=('#1', '其实还在'))
        self.assertIn('要重查', rf.issue_brief(self.root, '#1'))


class FailureTests(ReviewBase):
    def test_failure_midway_changes_nothing_and_can_start_again(self):  # 用例 15
        self.issues(4)
        self.say(n001=said('gone'), n002=said('stale'), n003='FAIL', n004=said('gone'))
        job = rf.issue_review(self.root)
        self.assertEqual((job['state'], job['done']), ('failed', 2))
        self.assertIn('#003', job['error'])
        self.assertIn('没有问题的状态被改动', job['error'])
        self.assertEqual({i['state'] for i in rf.issue_list(self.root)['issues']}, {'todo'})
        self.assertEqual(sum(len(self.entries(f'I-00{n}', 'review')) for n in range(1, 5)), 0)
        self.say(n001=said('gone'), n002=said('stale'), n003=said('gone'), n004=said('gone'))
        self.assertEqual(rf.issue_review(self.root)['state'], 'ready')

    def test_reviews_are_per_project(self):  # 用例 16
        self.issues(1)
        self.say(n001=said('gone'))
        rf.issue_review(self.root)
        other = self.root.parent / 'other'
        other.mkdir()
        git(other, 'init', '-q', '-b', 'main')
        self.assertEqual(rf.issue_review_status(other), {'state': 'idle'})
        self.assertEqual(rf.issue_show(self.root, '#1')['state'], 'todo')


if __name__ == '__main__':
    unittest.main()

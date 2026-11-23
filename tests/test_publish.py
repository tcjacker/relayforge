"""发布：状态、哪些提交带着哪些需求和问题、推送、发布记录、拉取。远端都是临时目录里的本地裸仓库，不碰网络。"""
import io
import json
import os
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'backend'))
import rf  # noqa: E402
from tests.app_fixture import as_app, in_repo_layout  # noqa: E402
from tests.test_issues import IssueBase, git  # noqa: E402


class PublishBase(IssueBase):
    def setUp(self):
        super().setUp()
        tmp = Path(self.tmp.name)
        self.remote = tmp / 'remote.git'
        git(tmp, 'init', '-q', '--bare', '-b', 'main', str(self.remote))
        git(self.root, 'remote', 'add', 'origin', str(self.remote))
        self.other = tmp / 'other'

    def published(self):
        git(self.root, 'push', '-q', '-u', 'origin', 'main')  # the fixture's own first push, into the local bare repository

    def remote_head(self, branch='main'):
        return subprocess.run(['git', '--git-dir', str(self.remote), 'rev-parse', branch], capture_output=True, text=True).stdout.strip()

    def remote_refs(self):
        return subprocess.run(['git', '--git-dir', str(self.remote), 'for-each-ref', '--format=%(refname)'], capture_output=True, text=True).stdout.split()

    def clone_other(self):
        git(Path(self.tmp.name), 'clone', '-q', str(self.remote), str(self.other))
        return self.other

    def push_from_other(self, name='theirs.txt'):
        place = self.clone_other() if not self.other.exists() else self.other
        git(place, 'pull', '-q', '--ff-only', 'origin', 'main')
        self.commit_in(place, name, 'x\n', f'远端的提交 {name}')
        git(place, 'push', '-q', 'origin', 'main')

    def finish_requirement(self, title='导出', code=True):
        made = rf.req_new(self.root, title)
        path = self.root / made['file']
        meta, sections = rf.read_doc(path)
        rf.add_line(sections, '验收条件', '- 能导出〔S-test#1〕')
        rf.write_doc(path, meta, sections)
        rf.req_start(self.root, made['id'])
        if code:
            self.commit_in(made['worktree'], 'export.py', 'print(1)\n', '写导出第一步')
            self.commit_in(made['worktree'], 'export2.py', 'print(2)\n', '写导出第二步')
        else:
            rf.apply_item(Path(made['worktree']), {'kind': 'structure', 'op': 'add-feature', 'title': '导出账单', 'module': '文档', 'one_liner': '导出。', 'stated_by': 'user'}, 'S-test#1', made['id'])
        return made, rf.req_done(self.root, made['id'])

    def fixed_issue(self):
        rf.issue_new(self.root, '启动时打印旧的', feature='RF-101')
        self.judge(kind='bug', reason='像是打印的内容没跟着改。')
        rf.issue_triage(self.root, 'I-001')
        self.commit_in(self.root, 'app.py', 'print("old")\n', '加 app')
        fix = rf.issue_fix(self.root, ['#1'])
        place = Path(fix['worktree'])
        self.commit_in(place, 'app.py', 'print("new")\n', '改成新的')
        rf.issue_handback(place, None, '改成打印新的', '运行 app.py', no_regression='这条测试只看交回流程，不看回归数据')
        return rf.issue_confirm(self.root, '#1')

    def run_cli(self, *args):
        """rf.py publish … as the app calls it: run and pull are the user's (the app's marker, tests/app_fixture.py)."""
        with redirect_stdout(io.StringIO()) as out, mock.patch('sys.stderr', io.StringIO()) as err:
            code = as_app(['publish', *args, '--root', str(self.root)])
        return code, out.getvalue(), err.getvalue()


class StatusTests(PublishBase):
    def test_counts_requirements_awaiting_acceptance_in_the_main_checkout(self):
        self.assertEqual(rf.publish_status(self.root)['pending_requirements'], 0)
        for title, state, stage in [
            ('立项中', 'draft', None), ('开发中', 'open', 'build'),
            ('待验收', 'open', 'check'), ('已完成', 'done', None), ('已取消', 'dropped', None),
        ]:
            made = rf.req_new(self.root, title)
            path = self.root / made['file']
            meta, sections = rf.read_doc(path)
            meta.update(state=state, manual_stage=stage)
            rf.add_line(sections, '验收条件', '- 可以验证。〔S-test#1〕')
            rf.write_doc(path, meta, sections)
        self.assertEqual(rf.publish_status(made['worktree'])['pending_requirements'], 1)

    def test_no_remote_is_a_blocker_not_a_crash(self):
        git(self.root, 'remote', 'remove', 'origin')
        st = rf.publish_status(self.root)
        self.assertEqual((st['state'], st['remotes'], st['upstream'], st['target']), ('no-remote', [], None, None))
        self.assertEqual([b['code'] for b in st['blockers']], ['no-remote'])
        self.assertFalse(st['can_publish'])
        code, out, err = self.run_cli('run', '--json')
        self.assertEqual((code, json.loads(out)['code']), (1, 'blocked'))
        self.assertIn('还没有远端', err)

    def test_one_remote_without_upstream_would_create_the_branch(self):
        st = rf.publish_status(self.root)
        self.assertEqual((st['state'], st['upstream']), ('no-upstream', None))
        self.assertEqual(st['target'], {'remote': 'origin', 'branch': 'main', 'creates': True, 'sets_upstream': True})
        self.assertTrue(st['can_publish'])
        self.assertEqual(st['blockers'], [])

    def test_several_remotes_need_a_choice_and_the_choice_must_exist(self):
        other = Path(self.tmp.name) / 'second.git'
        git(Path(self.tmp.name), 'init', '-q', '--bare', str(other))
        git(self.root, 'remote', 'add', 'second', str(other))
        st = rf.publish_status(self.root)
        self.assertTrue(st['needs_remote'])
        self.assertEqual([b['code'] for b in st['blockers']], ['pick-remote'])
        picked = rf.publish_status(self.root, remote='second')
        self.assertEqual((picked['target']['remote'], picked['blockers']), ('second', []))
        with self.assertRaises(rf.PublishError) as caught:
            rf.publish_run(self.root)
        self.assertEqual(caught.exception.code, 'blocked')
        bad = rf.publish_status(self.root, remote='nope')
        self.assertEqual(bad['blockers'][0]['code'], 'usage')
        self.assertEqual(self.remote_refs(), [])

    def test_ahead_behind_and_what_the_remote_looks_like_only_after_a_fetch(self):
        self.published()
        self.assertEqual(rf.publish_status(self.root)['state'], 'synced')
        self.commit_in(self.root, 'a.py', '1\n', '本地的提交')
        st = rf.publish_status(self.root)
        self.assertEqual((st['state'], st['ahead'], st['behind'], st['upstream']['ref']), ('ahead', 1, 0, 'origin/main'))
        self.assertEqual(st['unpublished']['others'][0]['subject'], '本地的提交')
        self.push_from_other()
        stale = rf.publish_status(self.root)
        self.assertEqual(stale['behind'], 0)  # behind is only as fresh as the last fetch
        fresh = rf.publish_status(self.root, fetch=True)
        self.assertEqual((fresh['fetch'], fresh['ahead'], fresh['behind'], fresh['state']), ({'ok': True}, 1, 1, 'diverged'))
        self.assertIn('diverged', [b['code'] for b in fresh['blockers']])
        self.assertFalse(fresh['can_publish'])
        self.assertTrue(fresh['last_fetch'])

    def test_behind_only_can_be_pulled_and_blocks_publishing(self):
        self.published()
        self.push_from_other()
        st = rf.publish_status(self.root, fetch=True)
        self.assertEqual((st['state'], st['behind'], st['ahead']), ('behind', 1, 0))
        self.assertEqual([b['code'] for b in st['blockers']], ['behind'])
        self.assertIn('1 个提交', st['blockers'][0]['text'])
        self.assertTrue(st['can_pull'])

    def test_a_failed_fetch_is_reported_not_hung(self):
        git(self.root, 'remote', 'set-url', 'origin', str(Path(self.tmp.name) / 'nowhere.git'))
        st = rf.publish_status(self.root, fetch=True)
        self.assertFalse(st['fetch']['ok'])
        self.assertEqual(st['fetch']['code'], 'network')
        self.assertIn('nowhere.git', st['fetch']['detail'] + str(st['remotes']))

    def test_blockers_and_warnings(self):
        self.published()
        (self.root / 'draft.py').write_text('x\n', encoding='utf-8')
        (self.root / 'app2.py').write_text('x\n', encoding='utf-8')
        st = rf.publish_status(self.root)
        loose = next(w for w in st['warnings'] if w['code'] == 'loose')
        self.assertEqual((loose['count'], sorted(loose['paths'])), (2, ['app2.py', 'draft.py']))
        self.assertEqual(st['blockers'], [])
        (self.root / '.git' / 'MERGE_HEAD').write_text(git(self.root, 'rev-parse', 'HEAD'), encoding='utf-8')
        self.assertEqual([b['code'] for b in rf.publish_status(self.root)['blockers']], ['busy'])
        (self.root / '.git' / 'MERGE_HEAD').unlink()
        git(self.root, 'checkout', '-q', '--detach')
        st = rf.publish_status(self.root)
        self.assertEqual((st['detached'], st['branch'], st['state']), (True, None, 'detached'))
        self.assertIn('detached', [b['code'] for b in st['blockers']])

    @in_repo_layout
    def test_waiting_fact_changes_are_a_note_and_do_not_count(self):
        self.published()
        feature = next((self.root / rf.DIR / 'features').glob('RF-101-*.md'))
        feature.write_text(feature.read_text(encoding='utf-8') + '\n', encoding='utf-8')
        st = rf.publish_status(self.root)
        self.assertEqual((st['ahead'], st['pending_facts']), (0, 1))
        self.assertEqual([w['code'] for w in st['warnings']], ['pending-facts'])


class MappingTests(PublishBase):
    @in_repo_layout
    def test_requirements_issues_facts_and_other_commits_are_told_apart(self):
        self.published()
        made, done = self.finish_requirement('导出')
        self.fixed_issue()
        self.commit_in(self.root, 'hand.py', '1\n', '我自己改的')
        feature = next((self.root / rf.DIR / 'features').glob('RF-101-*.md'))
        feature.write_text(feature.read_text(encoding='utf-8') + '\n', encoding='utf-8')
        self.assertTrue(rf.commit_facts(self.root, '记录事实'))
        st = rf.publish_status(self.root)
        un = st['unpublished']
        self.assertEqual([(r['id'], r['title']) for r in un['requirements']], [(made['id'], '导出')])
        self.assertEqual([(i['num'], i['title']) for i in un['issues']], [('#001', '启动时打印旧的')])
        self.assertEqual([o['subject'] for o in un['others']], ['我自己改的', '加 app'])
        self.assertGreaterEqual(un['facts'], 3)
        self.assertEqual(un['carried_commits'], 5)  # two merges and the commits on their branches (two for the requirement, one for the fix)
        self.assertEqual(un['facts'] + len(un['others']) + un['carried_commits'], un['total'])
        self.assertEqual(st['ahead'], un['total'])
        self.assertIn('merge_commit', rf.read_doc(self.root / done['file'])[0])
        self.assertIn('merge_commit', rf.read_fix(self.root, 'F-001'))

    def test_already_published_work_is_not_listed_again(self):
        made, _ = self.finish_requirement('导出')
        self.published()
        self.fixed_issue()
        un = rf.publish_status(self.root)['unpublished']
        self.assertEqual(un['requirements'], [])
        self.assertEqual(len(un['issues']), 1)

    @in_repo_layout
    def test_a_requirement_without_code_is_found_by_its_completion_commit(self):
        self.published()
        made, done = self.finish_requirement('只有事实', code=False)
        self.assertNotIn('merge_commit', rf.read_doc(self.root / done['file'])[0])
        un = rf.publish_status(self.root)['unpublished']
        self.assertEqual([r['id'] for r in un['requirements']], [made['id']])

    @in_repo_layout
    def test_work_finished_before_the_commit_was_recorded_is_found_by_the_merge_title(self):
        self.published()
        made, done = self.finish_requirement('导出')
        self.fixed_issue()
        path = self.root / done['file']
        meta, sections = rf.read_doc(path)
        meta.pop('merge_commit')
        rf.write_doc(path, meta, sections)
        fixfile = self.root / rf.DIR / 'issues' / 'fixes' / 'F-001.json'
        data = json.loads(fixfile.read_text(encoding='utf-8'))
        data.pop('merge_commit')
        fixfile.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        rf.commit_facts(self.root, '去掉记录的提交编号')
        un = rf.publish_status(self.root)['unpublished']
        self.assertEqual([r['id'] for r in un['requirements']], [made['id']])
        self.assertEqual([i['num'] for i in un['issues']], ['#001'])
        self.assertEqual(un['carried_commits'], 5)

    @in_repo_layout
    def test_without_an_upstream_what_is_not_on_the_remote_is_unpublished(self):
        self.finish_requirement('导出')
        un = rf.publish_status(self.root)['unpublished']
        self.assertEqual([r['title'] for r in un['requirements']], ['导出'])
        self.assertGreater(un['total'], 5)


class RunTests(PublishBase):
    @in_repo_layout
    def test_a_publish_pushes_and_leaves_nothing_unpublished_including_its_record(self):
        self.published()
        made, _ = self.finish_requirement('导出')
        self.fixed_issue()
        before = rf.publish_status(self.root)
        self.assertGreater(before['ahead'], 0)
        was = git(self.root, 'rev-parse', 'origin/main').strip()
        out = rf.publish_run(self.root)
        self.assertTrue(out['ok'])
        self.assertEqual(out['ahead'], 0)
        self.assertEqual(self.remote_head(), git(self.root, 'rev-parse', 'HEAD').strip())
        after = rf.publish_status(self.root)
        self.assertEqual((after['ahead'], after['state'], after['record']), (0, 'synced', None))
        self.assertEqual(after['unpublished']['total'], 0)
        records = rf.publish_list(self.root)
        self.assertEqual(len(records), 1)
        r = records[0]
        self.assertEqual((r['id'], r['published'], r['remote'], r['branch'], r['target']), (out['record'], True, 'origin', 'main', 'main'))
        self.assertEqual([x['id'] for x in r['requirements']], [made['id']])
        self.assertEqual([x['num'] for x in r['issues']], ['#001'])
        self.assertIn('加 app', [o['subject'] for o in r['others']])
        self.assertEqual(r['from'], was)
        self.assertNotEqual(r['to'], git(self.root, 'rev-parse', 'HEAD').strip())  # the record is committed after what it describes
        self.assertRegex(r['id'], r'^P-\d{8}-[0-9a-f]{4}$')
        self.assertEqual(git(self.root, 'status', '--porcelain'), '')

    def test_waiting_facts_are_committed_first_and_go_out(self):
        self.published()
        feature = next((self.root / rf.DIR / 'features').glob('RF-101-*.md'))
        feature.write_text(feature.read_text(encoding='utf-8') + '\n', encoding='utf-8')
        self.commit_in(self.root, 'a.py', '1\n', '本地的提交')
        rf.publish_run(self.root)
        self.assertEqual(rf.git(self.root, 'status', '--porcelain', '--', rf.DIR), '')
        self.assertEqual(self.remote_head(), git(self.root, 'rev-parse', 'HEAD').strip())
        self.assertEqual(rf.publish_status(self.root)['ahead'], 0)

    def test_nothing_to_publish_is_said_so(self):
        self.published()
        with self.assertRaises(rf.PublishError) as caught:
            rf.publish_run(self.root)
        self.assertEqual(caught.exception.code, 'nothing')
        self.assertEqual(rf.publish_list(self.root), [])

    @in_repo_layout
    def test_a_failed_push_leaves_one_record_that_the_next_attempt_reuses(self):
        self.published()
        self.commit_in(self.root, 'a.py', '1\n', '本地的提交')
        hook = self.root / '.git' / 'hooks' / 'pre-push'
        hook.write_text('#!/bin/sh\necho "检查没通过：缺少测试" >&2\nexit 1\n', encoding='utf-8')
        hook.chmod(0o755)
        with self.assertRaises(rf.PublishError) as caught:
            rf.publish_run(self.root)
        self.assertEqual(caught.exception.code, 'hook')
        self.assertIn('缺少测试', caught.exception.detail)
        first = caught.exception.extra['record']
        self.assertEqual(self.remote_head(), git(self.root, 'rev-parse', 'origin/main').strip())  # nothing went out
        st = rf.publish_status(self.root)
        self.assertEqual((st['ahead'], st['record']), (2, first))  # the commit and the record, waiting
        records = rf.publish_list(self.root)
        self.assertEqual([(r['id'], r['published']) for r in records], [(first, False)])
        self.commit_in(self.root, 'b.py', '2\n', '第二个本地的提交')
        with self.assertRaises(rf.PublishError):
            rf.publish_run(self.root)
        self.assertEqual(len(rf.publish_list(self.root)), 1)  # reused, not piled up
        hook.unlink()
        out = rf.publish_run(self.root)
        self.assertEqual(out['record'], first)
        records = rf.publish_list(self.root)
        self.assertEqual(len(records), 1)
        self.assertEqual((records[0]['published'], records[0]['attempts']), (True, 3))
        self.assertEqual({o['subject'] for o in records[0]['others']}, {'本地的提交', '第二个本地的提交'})
        self.assertEqual(rf.publish_status(self.root)['ahead'], 0)
        self.assertEqual(len(list((self.root / rf.DIR / 'publishes').glob('P-*.md'))), 1)

    def test_a_rejected_push_says_why_and_forces_nothing(self):
        self.published()
        self.commit_in(self.root, 'a.py', '1\n', '本地的提交')
        self.push_from_other()  # the remote moved; this checkout has not fetched
        theirs = self.remote_head()
        with self.assertRaises(rf.PublishError) as caught:
            rf.publish_run(self.root)
        self.assertEqual(caught.exception.code, 'rejected')
        self.assertIn('没有强推', str(caught.exception))
        self.assertIn('rejected', caught.exception.detail)
        self.assertEqual(self.remote_head(), theirs)
        code, out, err = self.run_cli('run', '--json')
        self.assertEqual((code, json.loads(out)['code']), (10, 'rejected'))
        self.assertEqual(len(rf.publish_list(self.root)), 1)

    def test_an_unreachable_remote_is_a_network_failure_with_its_own_exit_code(self):
        self.published()
        self.commit_in(self.root, 'a.py', '1\n', '本地的提交')
        git(self.root, 'remote', 'set-url', 'origin', str(Path(self.tmp.name) / 'gone.git'))
        code, out, err = self.run_cli('run', '--json')
        told = json.loads(out)
        self.assertEqual((code, told['code'], told['ok']), (12, 'network', False))
        self.assertTrue(told['record'])

    def test_a_remote_that_declines_is_reported_as_the_remotes_rule(self):
        self.published()
        self.commit_in(self.root, 'a.py', '1\n', '本地的提交')
        hook = self.remote / 'hooks' / 'pre-receive'
        hook.write_text('#!/bin/sh\necho "分支受保护" >&2\nexit 1\n', encoding='utf-8')
        hook.chmod(0o755)
        code, out, err = self.run_cli('run', '--json')
        self.assertEqual((code, json.loads(out)['code']), (14, 'remote-declined'))

    def test_remote_rules_when_there_is_no_upstream(self):
        with self.assertRaises(rf.PublishError):  # a remote that does not exist
            rf.publish_run(self.root, remote='second')
        out = rf.publish_run(self.root, remote='origin')
        self.assertEqual((out['created'], out['upstream_set']), (True, True))
        self.assertEqual(git(self.root, 'config', 'branch.main.remote').strip(), 'origin')
        self.assertEqual(git(self.root, 'config', 'branch.main.merge').strip(), 'refs/heads/main')
        self.assertEqual(self.remote_head(), git(self.root, 'rev-parse', 'HEAD').strip())
        self.assertEqual(rf.publish_status(self.root)['state'], 'synced')

    def test_the_single_remote_is_used_without_naming_it(self):
        out = rf.publish_run(self.root)
        self.assertEqual((out['remote'], out['target']), ('origin', 'main'))

    def test_with_an_upstream_another_remote_cannot_be_named(self):
        self.published()
        second = Path(self.tmp.name) / 'second.git'
        git(Path(self.tmp.name), 'init', '-q', '--bare', str(second))
        git(self.root, 'remote', 'add', 'second', str(second))
        self.commit_in(self.root, 'a.py', '1\n', '本地的提交')
        with self.assertRaises(rf.PublishError) as caught:
            rf.publish_run(self.root, remote='second')
        self.assertEqual(caught.exception.code, 'blocked')
        self.assertEqual(subprocess.run(['git', '--git-dir', str(second), 'for-each-ref'], capture_output=True, text=True).stdout, '')

    def test_other_branches_and_tags_never_go_out_whatever_the_git_config_says(self):
        self.published()
        git(self.root, 'branch', 'side')
        git(self.root, 'tag', '-a', '-m', 'v', 'v1')
        git(self.root, 'config', 'push.default', 'matching')
        git(self.root, 'config', 'push.followTags', 'true')
        self.commit_in(self.root, 'a.py', '1\n', '本地的提交')
        rf.publish_run(self.root)
        self.assertEqual(self.remote_refs(), ['refs/heads/main'])

    def test_a_blocker_stops_a_publish_before_anything_is_written(self):
        self.published()
        self.commit_in(self.root, 'a.py', '1\n', '本地的提交')
        self.push_from_other()
        rf.publish_status(self.root, fetch=True)
        with self.assertRaises(rf.PublishError) as caught:
            rf.publish_run(self.root)
        self.assertEqual(caught.exception.code, 'blocked')
        self.assertEqual(rf.publish_list(self.root), [])
        self.assertEqual(git(self.root, 'status', '--porcelain'), '')


class PullTests(PublishBase):
    def test_pull_fast_forwards(self):
        self.published()
        self.push_from_other()
        out = rf.publish_pull(self.root)
        self.assertEqual((out['ok'], out['pulled']), (True, 1))
        self.assertTrue((self.root / 'theirs.txt').exists())
        self.assertEqual(rf.publish_status(self.root)['behind'], 0)
        self.assertEqual(rf.publish_pull(self.root)['pulled'], 0)

    def test_pull_refuses_on_divergence_and_changes_nothing(self):
        self.published()
        self.commit_in(self.root, 'a.py', '1\n', '本地的提交')
        self.push_from_other()
        head = git(self.root, 'rev-parse', 'HEAD')
        code, out, err = self.run_cli('pull', '--json')
        told = json.loads(out)
        self.assertEqual((code, told['code']), (16, 'diverged'))
        self.assertIn('让 AI 来合并', told['error'])
        self.assertEqual(git(self.root, 'rev-parse', 'HEAD'), head)
        self.assertFalse((self.root / 'theirs.txt').exists())
        self.assertEqual(git(self.root, 'status', '--porcelain'), '')
        self.assertNotIn('Merge', git(self.root, 'log', '-1', '--format=%s'))

    def test_pull_needs_an_upstream(self):
        with self.assertRaises(rf.PublishError) as caught:
            rf.publish_pull(self.root)
        self.assertEqual(caught.exception.code, 'blocked')


class ListTests(PublishBase):
    def test_past_records_newest_first(self):
        self.published()
        self.commit_in(self.root, 'a.py', '1\n', '第一次')
        first = rf.publish_run(self.root)['record']
        self.commit_in(self.root, 'b.py', '1\n', '第二次')
        second = rf.publish_run(self.root)['record']
        self.assertNotEqual(first, second)
        self.assertEqual([r['id'] for r in rf.publish_list(self.root)], [second, first])
        code, out, _ = self.run_cli('list', '--json')
        self.assertEqual((code, [r['id'] for r in json.loads(out)]), (0, [second, first]))
        code, out, _ = self.run_cli('list')
        self.assertIn('已发布', out)


if __name__ == '__main__':
    unittest.main()

class DivergedMergeTests(PublishBase):
    """两边分叉以后：状态里给出两边都动过的文件；合并没做完是挡着发布的一件事，说明是哪种；合并提交以后检查远端，不再落后，可以发布。"""

    def diverge(self, conflict=True):
        self.published()
        self.commit_in(self.root, 'app.py', 'print("mine")\n', '本地改 app')
        self.commit_in(self.root, f'{rf.DIR}/shared.md', '本地的事实\n', '本地加事实')
        place = self.clone_other()
        git(place, 'config', 'user.email', 't@e.com'); git(place, 'config', 'user.name', 't')
        self.commit_in(place, 'app.py' if conflict else 'other.py', 'print("theirs")\n', '远端改 app')
        self.commit_in(place, f'{rf.DIR}/shared.md', '远端的事实\n', '远端加事实')
        git(place, 'push', '-q', 'origin', 'main')

    @in_repo_layout
    def test_divergence_names_the_files_both_sides_touched(self):
        self.diverge()
        st = rf.publish_status(self.root, fetch=True)
        self.assertEqual(st['state'], 'diverged')
        self.assertEqual((st['divergence']['both'], st['divergence']['facts_both']), (['app.py'], [f'{rf.DIR}/shared.md']))
        self.assertIsNone(st['busy'])

    @in_repo_layout
    def test_unfinished_merge_blocks_and_says_so_then_finishing_it_unblocks(self):
        self.diverge()
        rf.publish_status(self.root, fetch=True)
        merged = subprocess.run(['git', '-C', str(self.root), 'merge', '--no-edit', 'origin/main'], capture_output=True, text=True)
        self.assertNotEqual(merged.returncode, 0)  # conflicts
        st = rf.publish_status(self.root)
        self.assertEqual(st['busy'], 'merge')
        busy = [b for b in st['blockers'] if b['code'] == 'busy'][0]
        self.assertEqual(busy['kind'], 'merge')
        self.assertIn('合并还没做完', busy['text'])
        self.assertFalse(st['can_publish'])
        # what the AI would do: resolve both conflicts keeping both sides, commit the merge
        (self.root / 'app.py').write_text('print("mine")\nprint("theirs")\n')
        (self.root / rf.DIR / 'shared.md').write_text('本地的事实\n远端的事实\n')
        git(self.root, 'add', '-A'); git(self.root, 'commit', '-q', '--no-edit')
        after = rf.publish_status(self.root, fetch=True)
        self.assertEqual((after['busy'], after['behind'], after['state']), (None, 0, 'ahead'))
        self.assertTrue(after['can_publish'])
        done = rf.publish_run(self.root)
        self.assertTrue(done['ok'])
        self.assertEqual(self.remote_head(), git(self.root, 'rev-parse', 'HEAD').strip())
        self.assertIn('远端的事实', git(self.root, 'show', f'{self.remote_head()}:{rf.DIR}/shared.md'))

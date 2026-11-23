"""取消的需求留下的分支：没有自己提交的取消时一起删掉，有提交的按时间和数量清理，恢复时分支不在了就重新开一个。"""
import datetime as dt
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'backend'))
import rf  # noqa: E402
from relayforge.core.documents import parse_value, read_doc, write_doc  # noqa: E402
from relayforge.requirements import record  # noqa: E402
from tests import test_req_depends  # noqa: E402  模块名引用：不把那边的测试类导进来再跑一遍


class DroppedBranchTests(test_req_depends.DependBase):
    def branch(self, made):
        return subprocess.run(['git', 'rev-parse', '--verify', '--quiet', 'refs/heads/' + made['branch']], cwd=self.root,
                              capture_output=True, text=True).stdout.strip()

    def worked(self, title):
        """A requirement in development with a commit of its own on its branch."""
        made = self.req(title)
        self.start(made)
        self.commit_in(made['worktree'], f'{made["id"]}.txt', title)
        return made

    def dropped_on(self, made, day):
        path = self.root / made['file']
        meta, sections = read_doc(path)
        meta['dropped_at'] = day
        write_doc(path, meta, sections)

    def settings(self, **dropped):
        """The app's settings file, as the settings dialog saves it (rf.py reads the same one)."""
        home = Path(os.environ['RELAYFORGE_HOME'])
        home.mkdir(parents=True, exist_ok=True)
        (home / 'settings.json').write_text(json.dumps({'agent': 'claude', 'droppedBranches': dropped}), encoding='utf-8')

    def test_a_branch_without_commits_of_its_own_goes_with_the_cancelled_requirement_and_comes_back_on_restore(self):
        made = self.req('立项时就不做了')
        worktree = Path(made['worktree'])
        out = rf.req_drop(self.root, made['id'])
        self.assertFalse(worktree.exists())
        self.assertEqual(self.branch(made), '', 'nothing of its own on it: deleted with the requirement')
        self.assertTrue(out['branch_removed'])
        self.assertIn('分支上没有自己的提交，取消时一起删掉', ''.join(out['sections']['变更记录']))
        back = rf.req_restore(self.root, made['id'])
        self.assertEqual(back['state'], 'draft')
        self.assertTrue(Path(back['worktree']).exists())
        self.assertTrue(self.branch(made))
        self.assertEqual(back['branch_removed'], '')
        self.assertIn('原来的分支已经清理掉了', back['sections']['变更记录'][-1])

    def test_a_branch_with_commits_stays_after_cancelling_until_it_is_too_old(self):
        made = self.worked('做了一半')
        tip = subprocess.run(['git', 'rev-parse', '--short', made['branch']], cwd=self.root, capture_output=True, text=True).stdout.strip()
        rf.req_drop(self.root, made['id'])
        self.assertTrue(self.branch(made), 'its code is on the branch: kept for restoring')
        today = dt.date.today()
        self.assertEqual(record.prune_dropped_branches(self.root, today + dt.timedelta(days=record.DROPPED_KEEP_DAYS)), [])
        self.assertEqual(record.prune_dropped_branches(self.root, today + dt.timedelta(days=record.DROPPED_KEEP_DAYS + 1)), [made['id']])
        self.assertEqual(self.branch(made), '')
        view = self.view(made)
        self.assertEqual(view['branch_removed'], tip)
        self.assertIn(f'取消已超过 {record.DROPPED_KEEP_DAYS} 天，分支 {made["branch"]} 已删除（最后的提交是 {tip}', view['sections']['变更记录'][-1])

    def test_values_that_only_look_like_numbers_survive_reading_and_writing(self):
        for text in ('0754688', '007', '00', '-01', '-0'):
            self.assertEqual(parse_value(text), text)
        for text, number in (('0', 0), ('12', 12), ('-3', -3)):
            self.assertEqual(parse_value(text), number)
        path = self.root / 'round-trip.md'
        write_doc(path, {'branch_removed': '0754688'}, {})
        for _ in range(2):  # 读出来再写回去，值不能变
            meta, sections = read_doc(path)
            write_doc(path, meta, sections)
        self.assertEqual(read_doc(path)[0]['branch_removed'], '0754688')

    def test_a_numeric_commit_with_a_leading_zero_is_kept_whole(self):
        made = self.worked('提交号全是数字')
        real = record.git

        def fixed(home, *args):
            return '0754688\n' if args[:2] == ('rev-parse', '--short') else real(home, *args)

        rf.req_drop(self.root, made['id'])
        with mock.patch('relayforge.requirements.record.git', fixed):
            removed = record.prune_dropped_branches(self.root, dt.date.today() + dt.timedelta(days=record.DROPPED_KEEP_DAYS + 1))
        self.assertEqual(removed, [made['id']])
        self.assertEqual(self.view(made)['branch_removed'], '0754688')
        back = rf.req_restore(self.root, made['id'])
        self.assertIn('最后的提交是 0754688）', back['sections']['变更记录'][-1])

    def test_a_commit_older_versions_cut_short_is_read_back_from_the_change_log(self):
        made = self.worked('旧版本写坏的')
        rf.req_drop(self.root, made['id'])
        subprocess.run(['git', 'branch', '-D', made['branch']], cwd=self.root, check=True, capture_output=True)
        path = self.root / made['file']
        meta, sections = read_doc(path)
        meta['branch_removed'] = 754688  # 旧版本读成数字后写回文件的样子
        sections['变更记录'].append(f'- 2026-11-01 取消已超过 30 天，分支 {made["branch"]} 已删除（最后的提交是 0754688，git 回收之前还能用这个编号找回）。')
        write_doc(path, meta, sections)
        self.assertEqual(self.view(made)['branch_removed'], '0754688')
        back = rf.req_restore(self.root, made['id'])
        self.assertIn('最后的提交是 0754688）', back['sections']['变更记录'][-1])

    def test_only_the_latest_cancelled_branches_are_kept(self):
        made = [self.worked(f'第 {n} 个') for n in range(3)]
        for n, one in enumerate(made):
            rf.req_drop(self.root, one['id'])
            self.dropped_on(one, f'2026-11-1{n}')
        self.settings(keepMax=2)
        removed = record.prune_dropped_branches(self.root, dt.date(2026, 11, 20))
        self.assertEqual(removed, [made[0]['id']], 'the one cancelled first goes')
        self.assertEqual([bool(self.branch(m)) for m in made], [False, True, True])
        self.assertIn('取消的需求超过 2 个，较早取消的这个', self.view(made[0])['sections']['变更记录'][-1])

    def test_finishing_a_requirement_tidies_old_cancelled_branches(self):
        old = self.worked('很早取消的')
        rf.req_drop(self.root, old['id'])
        self.dropped_on(old, '2020-01-01')
        self.finish(self.req('现在做完的'))
        self.assertEqual(self.branch(old), '')
        self.assertTrue(self.view(old)['branch_removed'])
        self.assertIn(f'取消已超过 {record.DROPPED_KEEP_DAYS} 天', self.view(old)['sections']['变更记录'][-1])


    def test_the_days_and_the_count_come_from_the_settings(self):
        self.assertEqual(record.dropped_branch_limits(), (30, 20))
        self.settings(keepDays=7, keepMax=5)
        self.assertEqual(record.dropped_branch_limits(), (7, 5))
        self.settings(keepDays=0, keepMax='x')  # 不对的值：用默认的
        self.assertEqual(record.dropped_branch_limits(), (30, 20))
        made = self.worked('一周前取消的')
        rf.req_drop(self.root, made['id'])
        self.settings(keepDays=7, keepMax=5)
        today = dt.date.today()
        self.assertEqual(record.prune_dropped_branches(self.root, today + dt.timedelta(days=7)), [])
        self.assertEqual(record.prune_dropped_branches(self.root, today + dt.timedelta(days=8)), [made['id']])
        self.assertIn('取消已超过 7 天', self.view(made)['sections']['变更记录'][-1])

    def test_a_branch_the_user_keeps_is_never_cleaned_up(self):
        made = self.worked('先留着')
        with self.assertRaisesRegex(ValueError, '只有已取消的需求'):
            rf.req_keep(self.root, made['id'])
        rf.req_drop(self.root, made['id'])
        kept = rf.req_keep(self.root, made['id'])
        self.assertTrue(kept['keep_branch'])
        self.assertIn('设为保留', kept['sections']['变更记录'][-1])
        with self.assertRaisesRegex(ValueError, '已经在保留'):
            rf.req_keep(self.root, made['id'])
        far = dt.date.today() + dt.timedelta(days=3650)
        self.assertEqual(record.prune_dropped_branches(self.root, far), [])
        self.assertTrue(self.branch(made))
        self.assertFalse(rf.req_keep(self.root, made['id'], keep=False)['keep_branch'])
        self.assertEqual(record.prune_dropped_branches(self.root, far), [made['id']])
        with self.assertRaisesRegex(ValueError, '已经清理掉了'):
            rf.req_keep(self.root, made['id'])
        from relayforge.core.trust import command_key
        self.assertEqual(command_key('req', 'keep'), ('req', 'keep'))  # 只由用户触发


if __name__ == '__main__':
    unittest.main()

"""事实只在项目主目录里有一份：进行中的事实、放弃和恢复、合并只合代码、攒着提交、旧布局的搬家。"""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'backend'))
import rf  # noqa: E402
from tests.app_fixture import in_repo_layout  # noqa: E402
from relayforge.project import status  # noqa: E402

PAGE = '<!doctype html><html><body><h1>账单</h1><button>导出</button></body></html>'


def git(place, *args):
    return subprocess.run(['git', '-c', 'core.quotepath=off', *args], cwd=place, check=True, capture_output=True, text=True).stdout


def line(kind, content):
    return json.dumps({'type': kind, 'message': {'role': kind, 'content': content}}, ensure_ascii=False)


class LayoutBase(unittest.TestCase):
    SID = 'abcd1234-session'

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'project'
        self.root.mkdir()
        env = mock.patch.dict(os.environ, {
            'RELAYFORGE_HOME': str(Path(self.tmp.name) / 'home'),
            'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@example.com', 'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@example.com',
        })
        env.start()
        self.addCleanup(env.stop)
        for name in (rf.GUARD, 'CLAUDE_PROJECT_DIR', 'CLAUDE_CODE_SESSION_ID', 'RELAYFORGE_SESSION_ID', 'RELAYFORGE_EVENTS'):
            os.environ.pop(name, None)
        rf.init(self.root, hooks=False)
        base = self.root / rf.DIR
        rf.write_doc(base / 'modules' / 'M-01-账单.md', {'id': 'M-01', 'type': 'module', 'title': '账单', 'status': 'current'}, {'负责什么': ['- 管账单〔S-x#1〕'], '变更记录': []})
        rf.write_doc(base / 'features' / 'RF-101-账单列表.md', {'id': 'RF-101', 'type': 'feature', 'title': '账单列表', 'status': 'current', 'module': 'M-01', 'depends_on': []},
                     dict({h: [] for h in rf.FEATURE_HEADS}, **{'一句话定位': ['- 列出账单〔S-x#1〕'], '规则与边界': ['- 每页 20 条〔S-x#1〕']}))
        rf.write_summaries(self.root)
        git(self.root, 'init', '-q', '-b', 'main')
        git(self.root, 'add', '-A')
        git(self.root, 'commit', '-qm', 'init')
        self.transcript = Path(self.tmp.name) / f'{self.SID}.jsonl'
        self.transcript.write_text(line('user', '开始') + '\n', encoding='utf-8')

    def note(self, place, kind, *values, **kw):
        return rf.note(place, self.SID, self.transcript, kind, list(values), **kw)

    def started(self, title='导出'):
        made = rf.req_new(self.root, title)
        path = self.root / made['file']
        meta, sections = rf.read_doc(path)
        sections['验收条件'] = ['- 能导出〔S-x#1〕']
        rf.write_doc(path, meta, sections)
        rf.req_start(self.root, made['id'])
        return made, Path(made['worktree'])

    def commit_in(self, place, name, text, message='work'):
        (Path(place) / name).parent.mkdir(parents=True, exist_ok=True)
        (Path(place) / name).write_text(text, encoding='utf-8')
        git(place, 'add', '-A')
        git(place, 'commit', '-qm', message)

    def feature(self):
        return rf.read_doc(self.root / rf.DIR / 'features' / 'RF-101-账单列表.md')


class InProgressTests(LayoutBase):
    @in_repo_layout
    def test_what_a_requirement_brings_is_in_progress_until_it_is_done(self):
        made, place = self.started()
        other, _ = self.started('别的')
        print_ = io.StringIO()
        with redirect_stdout(print_):
            self.note(place, 'decision', '导出用 CSV', '导出的文件用 CSV')
            self.note(place, 'new-feature', '导出账单', module='M-01', reason='把账单导出成文件')
            said = self.note(place, 'fact', 'RF-101', '每页 50 条', replaces='每页 20 条')
        self.assertIn(f'标为需求 {made["id"]} 进行中', said)
        decision = rf.read_doc(next((self.root / rf.DIR / 'decisions').glob('D-0001-*.md')))[0]
        self.assertEqual(decision['in_progress'], made['id'])
        new = rf.read_doc(next((self.root / rf.DIR / 'features').glob('RF-102-*.md')))[0]
        self.assertEqual(new['in_progress'], made['id'])
        self.assertFalse((place / rf.DIR / 'features' / 'RF-102-导出账单.md').exists())  # the worktree's copy is not written
        meta, sections = self.feature()
        self.assertEqual(meta['changing'], [made['id']])
        self.assertEqual(sections['规则与边界'][0], f'- 〔{made["id"]} 完成后替代〕每页 20 条〔S-x#1〕')
        self.assertTrue(sections['规则与边界'][1].startswith(f'- 〔进行中 {made["id"]}〕每页 50 条'))
        self.assertEqual(rf.live_items(sections['规则与边界']), ['每页 20 条'])  # the current text stays what counts
        rf.write_summaries(self.root)  # regenerated at every commit moment (a session stopping, 立项, …)
        context = (self.root / rf.DIR / 'context.md').read_text(encoding='utf-8')
        self.assertNotIn('  - RF-102 导出账单：', context)  # not among the current features
        self.assertIn(f'功能点 RF-102 导出账单：{made["id"]} 新加的，尚未完成', context)
        self.assertIn(f'功能点 RF-101 账单列表：{made["id"]} 在改', context)
        rf.write_doc(self.root / rf.DIR / 'decisions' / 'D-0001-导出用-CSV.md', dict(decision, applies_to=['RF-101']), rf.read_doc(next((self.root / rf.DIR / 'decisions').glob('D-0001-*.md')))[1])
        brief_other = rf.find_requirement(self.root, other['id'])
        self.assertEqual(brief_other['brief']['decisions'], [])  # another requirement's decision in progress does not govern yet
        self.commit_in(place, 'export.py', 'print(1)\n')
        data = rf.collect(self.root)
        self.assertEqual(next(f for f in data['features'] if f['id'] == 'RF-102')['inProgress'], [made['id']])
        branch = next(b for b in data['now']['branches'] if b.get('requirement') == made['id'])
        self.assertEqual(branch['delta']['M-01']['features'], [{'id': 'RF-101', 'title': '账单列表', 'state': 'changed'}, {'id': 'RF-102', 'title': '导出账单', 'state': 'new'}])
        done = rf.req_done(self.root, made['id'])
        record = ' '.join(done['sections']['变更记录'])
        self.assertIn('新增：功能点 RF-102 导出账单；裁定 D-0001 导出用 CSV', record)
        self.assertIn('修改：功能点 RF-101 账单列表', record)
        meta, sections = self.feature()
        self.assertEqual(sections['规则与边界'][0], f'- {rf.REPLACED}每页 20 条〔S-x#1〕')
        self.assertTrue(sections['规则与边界'][1].startswith('- 每页 50 条'))
        self.assertIsNone(meta.get('changing'))
        self.assertNotIn('in_progress', rf.read_doc(next((self.root / rf.DIR / 'features').glob('RF-102-*.md')))[0])
        self.assertEqual(git(self.root, 'status', '--porcelain'), '')

    def test_dropping_puts_away_what_it_brought_and_restoring_brings_it_back(self):
        made, place = self.started()
        with redirect_stdout(io.StringIO()):
            self.note(place, 'new-feature', '导出账单', module='M-01')
            self.note(place, 'fact', 'RF-101', '每页 50 条', replaces='每页 20 条')
        rf.req_drop(self.root, made['id'])
        self.assertFalse(list((self.root / rf.DIR / 'features').glob('RF-102-*.md')))
        kept = rf.read_doc(next((self.root / rf.DIR / 'history').glob('RF-102-*.md')))[0]
        self.assertEqual(kept['dropped_with'], made['id'])
        meta, sections = self.feature()
        self.assertTrue(sections['规则与边界'][1].startswith(f'- 〔已放弃 {made["id"]}〕'))
        self.assertEqual(rf.live_items(sections['规则与边界']), ['每页 20 条'])
        self.assertIn('移到 history/', ' '.join(rf.find_requirement(self.root, made['id'])['sections']['变更记录']))
        self.assertEqual(git(self.root, 'status', '--porcelain'), '')
        rf.req_restore(self.root, made['id'])
        self.assertTrue(list((self.root / rf.DIR / 'features').glob('RF-102-*.md')))
        meta, sections = self.feature()
        self.assertEqual(sections['规则与边界'][0], f'- 〔{made["id"]} 完成后替代〕每页 20 条〔S-x#1〕')
        self.assertTrue(sections['规则与边界'][1].startswith(f'- 〔进行中 {made["id"]}〕'))
        self.assertEqual(meta['changing'], [made['id']])
        self.assertTrue(Path(rf.find_requirement(self.root, made['id'])['worktree']).is_dir())

    def test_without_a_requirement_a_changed_statement_is_current_at_once(self):
        with redirect_stdout(io.StringIO()):
            self.note(self.root, 'fact', 'RF-101', '每页 30 条', replaces='每页 20 条')
        sections = self.feature()[1]
        self.assertEqual(rf.live_items(sections['规则与边界']), ['每页 30 条'])
        with self.assertRaisesRegex(ValueError, '没有找到'):
            self.note(self.root, 'fact', 'RF-101', 'x', replaces='不存在的话')


class MergeTests(LayoutBase):
    @in_repo_layout
    def test_the_branch_copy_of_the_facts_never_lands_and_is_reported(self):
        made, place = self.started()
        self.commit_in(place, 'export.py', 'print(1)\n')
        stale = place / rf.DIR / 'features' / 'RF-101-账单列表.md'
        stale.write_text(stale.read_text(encoding='utf-8').replace('每页 20 条', '每页 99 条'), encoding='utf-8')
        git(place, 'commit', '-qam', 'edited the stale copy by mistake')
        before = self.feature()
        done = rf.req_done(self.root, made['id'])
        self.assertEqual(self.feature(), before)
        self.assertTrue((self.root / 'export.py').exists())
        self.assertIn('事实只以项目主目录的为准，这些没有合并进来', ' '.join(done['sections']['变更记录']))
        self.assertEqual(git(self.root, 'status', '--porcelain'), '')

    @in_repo_layout
    def test_uncommitted_facts_and_fact_only_commits_on_the_target_do_not_block(self):
        made, place = self.started()
        self.commit_in(place, 'export.py', 'print(1)\n')
        with redirect_stdout(io.StringIO()):
            self.note(self.root, 'open', '要不要加分页')  # a fact written in the main checkout, not committed yet
        rf.commit_facts(self.root, '记录')  # the target moved on with facts only
        with redirect_stdout(io.StringIO()):
            self.note(self.root, 'open', '要不要按月分')
        done = rf.req_done(self.root, made['id'])
        self.assertEqual(done['state'], 'done')
        self.assertIn('要不要按月分', git(self.root, 'show', f'HEAD:{rf.DIR}/product/open.md'))
        self.assertEqual(git(self.root, 'status', '--porcelain'), '')

    def test_code_on_the_target_without_a_conflict_is_merged_in_by_the_program(self):
        made, place = self.started()
        self.commit_in(place, 'export.py', 'print(1)\n')
        self.commit_in(self.root, 'other.py', 'x\n')
        branch = made['branch']
        done = rf.req_done(self.root, made['id'])
        self.assertEqual(done['state'], 'done')
        self.assertTrue((self.root / 'other.py').exists() and (self.root / 'export.py').exists())
        log = git(self.root, 'log', '--format=%s', '-3')
        self.assertIn(f'合并 main 到 {branch}', log)

    def test_code_on_the_target_that_conflicts_still_sends_it_back_to_its_session(self):
        made, place = self.started()
        self.commit_in(place, 'shared.py', 'theirs\n')
        self.commit_in(self.root, 'shared.py', 'mine\n')
        head = git(place, 'rev-parse', 'HEAD').strip()
        with self.assertRaises(rf.NeedsSync) as caught:
            rf.req_done(self.root, made['id'])
        self.assertEqual(caught.exception.files, ['shared.py'])
        self.assertEqual(git(place, 'rev-parse', 'HEAD').strip(), head)  # 程序没动分支，留给会话里的 AI

    @in_repo_layout
    def test_a_conflict_only_in_the_stale_facts_copy_does_not_stop_the_automatic_merge(self):
        made, place = self.started()
        self.commit_in(place, 'export.py', 'print(1)\n')
        stale = place / rf.DIR / 'features' / 'RF-101-账单列表.md'
        stale.write_text(stale.read_text(encoding='utf-8').replace('每页 20 条', '每页 99 条'), encoding='utf-8')
        git(place, 'commit', '-qam', 'edited the stale copy by mistake')
        real = self.root / rf.DIR / 'features' / 'RF-101-账单列表.md'
        real.write_text(real.read_text(encoding='utf-8').replace('每页 20 条', '每页 50 条'), encoding='utf-8')
        rf.commit_facts(self.root, '记录')
        self.commit_in(self.root, 'other.py', 'x\n')  # 目标分支上事实和代码都动了：事实冲突只在分支的旧副本上
        before = self.feature()
        self.assertEqual(rf.req_done(self.root, made['id'])['state'], 'done')
        self.assertEqual(self.feature(), before)
        self.assertTrue((self.root / 'other.py').exists() and (self.root / 'export.py').exists())

    def test_when_the_program_cannot_merge_it_in_the_directory_is_left_as_it_was(self):
        made, place = self.started()
        self.commit_in(place, 'export.py', 'print(1)\n')
        self.commit_in(self.root, 'other.py', 'x\n')
        head = git(place, 'rev-parse', 'HEAD').strip()
        from relayforge.requirements import record
        real = record.run_git
        def failing(where, *args):  # 比如没设 user.name：合并提交写不进去
            if args[:1] == ('commit',) and Path(where) == place:
                return subprocess.CompletedProcess(args, 1, '', 'error: boom')
            return real(where, *args)
        with mock.patch.object(record, 'run_git', failing):
            with self.assertRaises(rf.NeedsSync):
                rf.req_done(self.root, made['id'])
        self.assertEqual(git(place, 'rev-parse', 'HEAD').strip(), head)
        self.assertFalse((place / git(place, 'rev-parse', '--git-path', 'MERGE_HEAD').strip()).exists())  # 没留下做了一半的合并
        self.assertEqual(git(place, 'status', '--porcelain'), '')

    def test_a_requirement_that_only_drew_a_design_is_done_without_a_merge(self):
        made = rf.req_new(self.root, '只画图')
        place = Path(made['worktree'])
        path = self.root / made['file']
        meta, sections = rf.read_doc(path)
        sections.update({'需求说明': ['- 改版〔S-x#1〕'], '要改的功能点': ['- RF-101〔S-x#1〕'], '验收条件': ['- 看着对〔S-x#1〕']})
        rf.write_doc(path, meta, sections)
        drawn = rf.design_save(place, PAGE, '列表改版', ['RF-101'])
        rf.req_start(self.root, made['id'])
        done = rf.req_done(self.root, made['id'])
        self.assertEqual(done['designs'][0]['status'], 'current')
        self.assertIn('没有代码改动，不用合并', ' '.join(done['sections']['变更记录']))
        self.assertEqual(rf.current_designs(self.root)['RF-101']['id'], drawn['id'])
        self.assertFalse(place.exists())


class CommitTests(LayoutBase):
    @in_repo_layout
    def test_a_session_stopping_commits_the_facts_and_nothing_else(self):
        made, place = self.started()
        (self.root / 'mine.txt').write_text('x\n', encoding='utf-8')
        git(self.root, 'add', 'mine.txt')
        with redirect_stdout(io.StringIO()):
            self.note(place, 'doing', '在写导出')
        self.assertTrue(git(self.root, 'status', '--porcelain', '--', rf.DIR).strip())
        with redirect_stdout(io.StringIO()) as out:
            status.hook_event({'hook_event_name': 'Stop', 'cwd': str(place), 'session_id': self.SID, 'transcript_path': str(self.transcript)})
        self.assertEqual(out.getvalue(), '')
        self.assertEqual(git(self.root, 'status', '--porcelain'), 'A  mine.txt\n')
        self.assertIn('记录会话进展', git(self.root, 'log', '-1', '--format=%s'))
        self.assertTrue(all(n.startswith(rf.DIR + '/') for n in git(self.root, 'show', '--name-only', '--format=', 'HEAD').split()))

    @in_repo_layout
    def test_nothing_is_committed_in_the_middle_of_the_users_merge(self):
        (self.root / rf.DIR / 'product' / 'open.md').write_text('---\nid: OPEN\n---\n', encoding='utf-8')
        (self.root / '.git' / 'MERGE_HEAD').write_text(git(self.root, 'rev-parse', 'HEAD'), encoding='utf-8')
        self.assertFalse(rf.commit_facts(self.root, 'x'))
        (self.root / '.git' / 'MERGE_HEAD').unlink()
        self.assertTrue(rf.commit_facts(self.root, 'x'))


class OldLayoutTests(LayoutBase):
    """Leftovers of the old layout, where a requirement kept its file and facts on its own branch: they are not carried
    over (the user's decision) and must not get in the way."""

    @in_repo_layout

    def test_requirements_only_on_old_branches_are_ignored_without_errors(self):
        done = rf.req_new(self.root, '早就完成的')  # already in the main checkout: keeps showing
        meta, sections = rf.read_doc(self.root / done['file'])
        rf.write_doc(self.root / done['file'], dict(meta, state='done'), sections)
        rf.put_away(self.root, Path(done['worktree']), done['branch'], force_branch=True)
        ident, branch = 'R-20261101-0000', 'req/20261101-0000'
        place = rf.open_worktree(self.root, ident, branch)  # an old one: its file only on its branch, with a worktree left
        rf.write_doc(place / rf.DIR / 'requirements' / f'{ident}-旧的.md', {'id': ident, 'type': 'requirement', 'title': '旧的', 'status': 'current',
                     'state': 'open', 'branch': branch, 'sessions': []}, {'需求说明': ['- 旧〔S-old#1〕'], '要改的功能点': [], '验收条件': [], '变更记录': []})
        rf.write_doc(place / rf.DIR / 'decisions' / 'D-0001-旧裁定.md', {'id': 'D-0001', 'type': 'decision', 'title': '旧裁定', 'status': 'current'}, {'决定': ['- 旧〔S-old#1〕']})
        self.commit_in(place, 'old.py', 'x\n', 'old layout')
        gone = 'R-20261101-0001'  # an old cancelled one: only a branch left, no worktree
        other = rf.open_worktree(self.root, gone, 'req/20261101-0001')
        rf.write_doc(other / rf.DIR / 'requirements' / f'{gone}-取消的.md', {'id': gone, 'type': 'requirement', 'title': '取消的', 'status': 'current',
                     'state': 'dropped', 'branch': 'req/20261101-0001'}, {'需求说明': [], '要改的功能点': [], '验收条件': [], '变更记录': []})
        self.commit_in(other, 'gone.py', 'x\n', 'cancelled in the old layout')
        git(self.root, 'worktree', 'remove', str(other))
        found = {r['id']: r for r in rf.load_requirements(self.root)}
        self.assertEqual(set(found), {done['id']})
        self.assertEqual((found[done['id']]['state'], found[done['id']]['worktree']), ('done', None))
        self.assertFalse(list((self.root / rf.DIR / 'decisions').glob('D-*.md')))  # the old branch's facts are not read
        data = rf.collect(self.root)
        self.assertEqual([b['name'] for b in data['now']['branches']], [])
        rf.write_summaries(self.root)
        self.assertNotIn('旧裁定', (self.root / rf.DIR / 'context.md').read_text(encoding='utf-8'))
        with self.assertRaisesRegex(ValueError, '不属于任何需求'):
            self.note(place, 'say', '在旧工作目录里')
        made, there = self.started('新的')  # a new one works end to end next to the leftovers
        with redirect_stdout(io.StringIO()):
            self.note(there, 'decision', '新裁定', '新的决定')
        self.commit_in(there, 'new.py', 'print(1)\n')
        self.assertEqual(rf.req_done(self.root, made['id'])['state'], 'done')
        self.assertTrue(place.is_dir())  # the leftovers are not touched
        self.assertTrue(git(self.root, 'branch', '--list', branch).strip())
        self.assertTrue(git(self.root, 'branch', '--list', 'req/20261101-0001').strip())
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(rf.main(['req', 'list', '--root', str(place), '--json']), 0)
        self.assertEqual({r['id'] for r in json.loads(out.getvalue())}, {done['id'], made['id']})


if __name__ == '__main__':
    unittest.main()

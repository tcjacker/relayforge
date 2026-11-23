"""项目名称旁的分支选择（R-20261119-645d，后端 branches.py）：
列表里不出现为需求、修复开的分支；每个分支的待发布需求数和顶栏同一个口径；切换只动项目主目录，开着的需求照常做完、合进切过去的分支；
Git 拒绝时本地改动和当前分支原样不动；分支在别处签出、还提交着旧的 .relayforge/ 时先拦下；切换只由用户在应用里触发。"""
import json
import os
import unittest
from pathlib import Path

from tests.test_datastore import Base, git
import rf  # noqa: E402
from relayforge.project import branches  # noqa: E402
from relayforge.project import publish  # noqa: E402


class BranchBase(Base):
    def setUp(self):
        super().setUp()
        self.remote = self.base / 'remote.git'
        git(self.base, 'init', '-q', '--bare', '-b', 'main', str(self.remote))
        git(self.root, 'remote', 'add', 'origin', str(self.remote))
        git(self.root, 'push', '-q', '-u', 'origin', 'main')

    def branch(self, name, upstream=True, content=None):
        """A user branch from main, pushed (with upstream) unless told otherwise; `content` changes app.txt on it."""
        git(self.root, 'branch', name, 'main')
        if content is not None:
            place = self.base / ('tmp-' + name.replace('/', '-'))
            git(self.root, 'worktree', 'add', '-q', str(place), name)
            (place / 'app.txt').write_text(content, encoding='utf-8')
            git(place, 'commit', '-qam', f'{name} 改了 app.txt')
            git(self.root, 'worktree', 'remove', '--force', str(place))
        if upstream:
            git(self.root, 'push', '-q', '-u', 'origin', name)

    def start(self, req):
        path = next((self.root / rf.DIR / 'requirements').glob(f'{req["id"]}-*.md'))
        meta, sections = rf.read_doc(path)
        sections['验收条件'] = ['- 按钮在〔S-x#1〕']
        rf.write_doc(path, meta, sections)
        code, out, err = self.cli('req', 'start', req['id'], '--root', str(self.root), app=True)
        self.assertEqual(code, 0, out + err)
        return next(r for r in rf.load_requirements(self.root) if r['id'] == req['id'])

    def switch(self, name):
        code, out, err = self.cli('branch', 'switch', name, '--json', '--root', str(self.root), app=True)
        return code, json.loads(out.strip().splitlines()[-1]) if out.strip() else {}, err

    def listed(self):
        return {b['name']: b for b in branches.branch_list(self.root)['branches']}


class ListTests(BranchBase):
    def test_requirement_and_fix_branches_are_hidden_user_branches_are_shown(self):
        req = self.start(self.new_req())
        self.assertTrue(req['branch'].startswith('req/'))
        git(self.root, 'branch', 'fix/f-007', 'main')  # 修复的分支（记录已经不在了，也按名字认出来）
        git(self.root, 'branch', 'req/20200101-abcd', 'main')
        self.branch('feature/board')
        names = self.listed()
        self.assertEqual(sorted(names), ['feature/board', 'main'])
        self.assertTrue(names['main']['current'])
        self.assertEqual(branches.branch_list(self.root)['branches'][0]['name'], 'main')  # 当前分支在最前

    def test_the_count_is_the_same_as_the_top_bar(self):
        req = self.start(self.new_req())
        tree = Path(req['worktree'])
        (tree / 'app.txt').write_text('v2\n', encoding='utf-8')
        git(tree, 'commit', '-qam', 'work')
        code, out, err = self.cli('req', 'done', req['id'], '--root', str(self.root), app=True)
        self.assertEqual(code, 0, out + err)
        self.branch('develop')  # 没有待发布的
        git(self.root, 'branch', 'local-only', 'main')  # 没有上游：不报数
        names = self.listed()
        top = publish.publish_status(self.root)
        self.assertEqual(names['main']['pending'], len(top['unpublished']['requirements']))
        self.assertEqual(names['main']['pending'], 1)
        self.assertEqual(names['main']['upstream'], 'origin/main')
        self.assertEqual(names['develop']['pending'], 0)
        self.assertIsNone(names['local-only']['upstream'])
        self.assertEqual(branches.branch_list(self.root)['pending'], 1)


class SwitchTests(BranchBase):
    def test_a_requirement_opened_on_a_continues_after_switching_to_b_and_merges_into_b(self):
        req = self.start(self.new_req('在 A 上开的需求'))
        tree = Path(req['worktree'])
        (tree / 'app.txt').write_text('v2\n', encoding='utf-8')
        git(tree, 'commit', '-qam', 'A 上做了一半')
        self.branch('b')
        code, out, err = self.switch('b')
        self.assertEqual(code, 0, err)
        self.assertEqual(out, {'ok': True, 'from': 'main', 'to': 'b', 'changed': True})
        self.assertEqual(rf.branch_of(self.root), 'b')
        self.assertTrue((self.root / rf.DIR).is_symlink())  # 本机记录的链接没动
        # 原来的开发会话照常继续：工作目录还是它的分支，能提交、能记进展，记录还是同一份
        self.assertEqual(rf.branch_of(tree), req['branch'])
        (tree / 'more.txt').write_text('x\n', encoding='utf-8')
        git(tree, 'add', '-A')
        git(tree, 'commit', '-qm', '切走以后接着做')
        here = os.getcwd()
        os.chdir(tree)
        self.addCleanup(os.chdir, here)
        code, _, err = self.cli('issue', 'new', '切分支以后记的问题', '--desc', '在工作目录里', '--source', 'Agent')
        self.assertEqual(code, 0, err)
        os.chdir(here)
        self.assertTrue(list((self.store / 'issues').glob('*.md')))
        # 按现行规则确认完成：合进项目主目录现在签出的 b，b 的待发布数随之变成 1，main 不算
        code, out, err = self.cli('req', 'done', req['id'], '--root', str(self.root), app=True)
        self.assertEqual(code, 0, out + err)
        meta = rf.read_doc(next((self.root / rf.DIR / 'requirements').glob(f'{req["id"]}-*.md')))[0]
        self.assertEqual(meta['merged_into'], 'b')
        self.assertEqual((self.root / 'more.txt').read_text(encoding='utf-8'), 'x\n')
        names = self.listed()
        self.assertEqual(names['b']['pending'], 1)
        self.assertEqual(names['main']['pending'], 0)
        self.assertEqual(len(publish.publish_status(self.root)['unpublished']['requirements']), 1)

    def test_git_refusing_keeps_the_local_changes_and_the_branch(self):
        self.branch('b', content='b 上的内容\n')
        (self.root / 'app.txt').write_text('我还没提交的改动\n', encoding='utf-8')
        code, out, err = self.switch('b')
        self.assertEqual(code, 1)
        self.assertEqual(out['code'], 'dirty')
        self.assertIn('还没提交', out['error'])
        self.assertIn('app.txt', out['detail'])
        self.assertEqual(rf.branch_of(self.root), 'main')
        self.assertEqual((self.root / 'app.txt').read_text(encoding='utf-8'), '我还没提交的改动\n')

    def test_untracked_files_in_the_way_are_kept(self):
        git(self.root, 'branch', 'b', 'main')
        place = self.base / 'tmp-b'
        git(self.root, 'worktree', 'add', '-q', str(place), 'b')
        (place / 'new.txt').write_text('b 上的\n', encoding='utf-8')
        git(place, 'add', '-A')
        git(place, 'commit', '-qm', 'b 加了 new.txt')
        git(self.root, 'worktree', 'remove', '--force', str(place))
        (self.root / 'new.txt').write_text('我自己的\n', encoding='utf-8')
        code, out, _ = self.switch('b')
        self.assertEqual((code, out['code']), (1, 'untracked'))
        self.assertEqual((self.root / 'new.txt').read_text(encoding='utf-8'), '我自己的\n')
        self.assertEqual(rf.branch_of(self.root), 'main')

    def test_a_branch_checked_out_elsewhere_is_explained_before_trying(self):
        git(self.root, 'branch', 'other', 'main')
        place = self.base / 'elsewhere'
        git(self.root, 'worktree', 'add', '-q', str(place), 'other')
        row = self.listed()['other']
        self.assertIn('另一个工作目录', row['blocked'])
        code, out, _ = self.switch('other')
        self.assertEqual((code, out['code']), (1, 'elsewhere'))
        self.assertEqual(rf.branch_of(self.root), 'main')

    def test_a_branch_with_the_old_facts_directory_is_not_switched_to(self):
        git(self.root, 'branch', 'old', 'main')
        place = self.base / 'tmp-old'
        git(self.root, 'worktree', 'add', '-q', str(place), 'old')
        (place / rf.DIR).mkdir()
        (place / rf.DIR / 'FORMAT.md').write_text('旧的\n', encoding='utf-8')
        git(place, 'add', '-f', '-A')
        git(place, 'commit', '-qm', '旧布局')
        git(self.root, 'worktree', 'remove', '--force', str(place))
        self.assertIn('旧的 .relayforge/', self.listed()['old']['blocked'])
        code, out, _ = self.switch('old')
        self.assertEqual((code, out['code']), (1, 'old-layout'))
        self.assertTrue((self.root / rf.DIR).is_symlink())
        self.assertEqual(rf.branch_of(self.root), 'main')

    def test_requirement_branches_and_unknown_names_are_refused(self):
        req = self.start(self.new_req())
        code, out, _ = self.switch(req['branch'])
        self.assertEqual((code, out['code']), (1, 'managed'))
        code, out, _ = self.switch('nope')
        self.assertEqual((code, out['code']), (1, 'missing'))
        with self.assertRaises(branches.BranchError) as caught:  # 像选项的名字不当选项传给 git
            branches.branch_switch(self.root, '--orphan')
        self.assertEqual(caught.exception.code, 'missing')
        code, out, _ = self.switch('main')
        self.assertEqual((code, out['changed']), (0, False))

    def test_only_the_user_switches(self):
        self.branch('b')
        code, _, err = self.cli('branch', 'switch', 'b', '--root', str(self.root))
        self.assertEqual(code, 1)
        self.assertIn('只能由你在应用里做', err)
        self.assertEqual(rf.branch_of(self.root), 'main')
        code, out, _ = self.cli('branch', 'list', '--json', '--root', str(self.root))  # 列表只读，谁都能看
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)['current'], 'main')


if __name__ == '__main__':
    unittest.main()

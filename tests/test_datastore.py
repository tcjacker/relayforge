"""数据外置：记录放在本机 ~/.relayforge/projects/<项目 ID>/，不进 Git；仓库里只有 .relayforge.json。
主目录、需求的工作目录、重新克隆的仓库都读写同一份；切换分支不影响；导出、导入；旧项目认得出来。"""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'backend'))
import rf  # noqa: E402
from relayforge.core import common  # noqa: E402
from relayforge.project import datastore  # noqa: E402
from relayforge.project import publish  # noqa: E402
from tests.app_fixture import as_app  # noqa: E402


def git(place, *args):
    return subprocess.run(['git', '-c', 'core.quotepath=off', *args], cwd=place, check=True, capture_output=True, text=True).stdout


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.home = self.base / 'home'
        env = mock.patch.dict(os.environ, {'RELAYFORGE_HOME': str(self.home), 'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@example.com',
                                           'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@example.com'})
        env.start()
        self.addCleanup(env.stop)
        for name in (rf.GUARD, 'CLAUDE_PROJECT_DIR', 'CLAUDE_CODE_SESSION_ID', 'RELAYFORGE_SESSION_ID'):
            os.environ.pop(name, None)
        self.root = self.base / 'project'
        self.root.mkdir()
        git(self.root, 'init', '-q', '-b', 'main')
        (self.root / 'app.txt').write_text('v1\n', encoding='utf-8')
        git(self.root, 'add', '-A')
        git(self.root, 'commit', '-qm', 'first')
        rf.init(self.root, hooks=False)
        git(self.root, 'add', '-A')
        git(self.root, 'commit', '-qm', 'init')
        self.ident = json.loads((self.root / common.ID_FILE).read_text(encoding='utf-8'))['id']
        self.store = self.home / 'projects' / self.ident

    def cli(self, *argv, app=False):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = as_app(list(argv)) if app else rf.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def new_req(self, title='加一个按钮'):
        made = rf.req_new(self.root, title)
        req = next(r for r in rf.load_requirements(self.root) if r['id'] == made['id'])
        return req


class RulesTests(Base):
    """数据外置的项目不往仓库的 AGENTS.md / CLAUDE.md 里写约定（会干扰远程开发）；约定放在本机记录里，由应用交给会话。"""

    def test_init_leaves_agents_and_claude_alone(self):
        self.assertFalse((self.root / 'AGENTS.md').exists())
        self.assertFalse((self.root / 'CLAUDE.md').exists())
        rules = (self.store / 'CONVENTIONS.md').read_text(encoding='utf-8')
        self.assertIn('项目事实的写法', rules)
        self.assertNotIn('relayforge:begin', rules)
        self.assertNotIn('CONVENTIONS.md', git(self.root, 'ls-files'))

    def test_existing_agents_file_is_untouched(self):
        (self.root / 'AGENTS.md').write_text('# 我自己的规则\n- 用两个空格缩进\n', encoding='utf-8')
        (self.root / 'CLAUDE.md').write_text('自己写的\n', encoding='utf-8')
        rf.init(self.root, hooks=False)
        self.assertEqual((self.root / 'AGENTS.md').read_text(encoding='utf-8'), '# 我自己的规则\n- 用两个空格缩进\n')
        self.assertEqual((self.root / 'CLAUDE.md').read_text(encoding='utf-8'), '自己写的\n')

    def test_block_written_by_an_earlier_version_is_taken_out(self):
        from relayforge.project.init import CONVENTIONS
        (self.root / 'AGENTS.md').write_text('# 我自己的规则\n\n' + CONVENTIONS + '\n## 还有一段\n', encoding='utf-8')
        rf.init(self.root, hooks=False)
        text = (self.root / 'AGENTS.md').read_text(encoding='utf-8')
        self.assertNotIn('relayforge:begin', text)
        self.assertNotIn('项目事实的写法', text)
        self.assertIn('# 我自己的规则', text)
        self.assertIn('## 还有一段', text)

    def test_skills_are_not_written_into_the_repo_but_handed_over_with_the_rules(self):
        self.assertFalse((self.root / '.claude' / 'skills').exists())
        rules = (self.store / 'CONVENTIONS.md').read_text(encoding='utf-8')
        self.assertIn('## 常用操作', rules)
        self.assertIn('rf.py sync', rules)
        self.assertIn('rf.py pending', rules)
        self.assertNotIn('\nname: rf-sync', rules)

    def test_skills_written_by_an_earlier_version_are_taken_out_and_others_kept(self):
        from relayforge.project.init import SKILL
        skills = self.root / '.claude' / 'skills'
        (skills / 'rf-sync').mkdir(parents=True)
        (skills / 'rf-sync' / 'SKILL.md').write_text(SKILL, encoding='utf-8')
        (skills / 'mine').mkdir()
        (skills / 'mine' / 'SKILL.md').write_text('---\nname: mine\n---\n自己的\n', encoding='utf-8')
        (skills / 'rf-confirm').mkdir()
        (skills / 'rf-confirm' / 'SKILL.md').write_text('---\nname: other\n---\n同名目录但不是我们写的\n', encoding='utf-8')
        rf.init(self.root, hooks=False)
        self.assertFalse((skills / 'rf-sync').exists())
        self.assertTrue((skills / 'mine' / 'SKILL.md').is_file())
        self.assertTrue((skills / 'rf-confirm' / 'SKILL.md').is_file())


class LayoutTests(Base):
    def test_finding_the_project_does_not_repoint_a_working_link(self):
        # 另一个外置根目录（测试的临时目录、别的进程）里在这个仓库里找项目：链接指着的目录还在，就不改指；断了才修
        link = self.root / rf.DIR
        elsewhere = self.base / 'other-home'
        with mock.patch.dict(os.environ, {'RELAYFORGE_HOME': str(elsewhere)}):
            self.assertEqual(rf.find_root(self.root), self.root.resolve())
            self.assertEqual(link.resolve(), self.store.resolve())
            gone = self.base / 'gone'
            link.unlink()
            link.symlink_to(gone, target_is_directory=True)  # 断了的链接
            rf.find_root(self.root)
            self.assertEqual(link.resolve(), (elsewhere / 'projects' / self.ident).resolve())
        rf.link_facts(self.root)  # 明确点名的（init、projects --add、--root）照旧按现在的外置根目录改指
        self.assertEqual(link.resolve(), self.store.resolve())

    def test_records_live_outside_the_repository(self):
        link = self.root / rf.DIR
        self.assertTrue(link.is_symlink())
        self.assertEqual(link.resolve(), self.store.resolve())
        self.assertTrue((self.store / 'FORMAT.md').is_file())
        tracked = git(self.root, 'ls-files').split()
        self.assertIn(common.ID_FILE, tracked)
        self.assertFalse([f for f in tracked if f.startswith(rf.DIR + '/')])  # 一条记录都不进 Git
        self.assertEqual(git(self.root, 'status', '--porcelain'), '')
        self.assertIn(f'/{rf.DIR}', (self.root / '.git' / 'info' / 'exclude').read_text(encoding='utf-8'))

    def test_writing_a_requirement_commits_nothing(self):
        head = git(self.root, 'rev-parse', 'HEAD')
        req = self.new_req()
        self.assertTrue(list((self.store / 'requirements').glob(f'{req["id"]}-*.md')))
        self.assertFalse(rf.commit_facts(self.root, '记录事实'))
        self.assertEqual(git(self.root, 'rev-parse', 'HEAD'), head)
        self.assertEqual(git(self.root, 'status', '--porcelain'), '')

    def test_the_worktree_reads_and_writes_the_same_records(self):
        req = self.new_req()
        tree = Path(req['worktree'])
        self.assertTrue(tree.is_dir())
        found = common.find_root(tree)  # 第一次在工作目录里运行：接上链接
        self.assertEqual(found, tree.resolve())
        self.assertTrue((tree / rf.DIR).is_symlink())
        self.assertEqual((tree / rf.DIR).resolve(), self.store.resolve())
        here = os.getcwd()
        os.chdir(tree)
        self.addCleanup(os.chdir, here)
        code, out, err = self.cli('issue', 'new', '工作目录里记的问题', '--desc', '在工作目录里操作', '--source', 'Agent')
        self.assertEqual(code, 0, err)
        found = [p for p in (self.store / 'issues').glob('I-*.md') if '工作目录里记的问题' in p.read_text(encoding='utf-8')]
        self.assertEqual(len(found), 1)  # 写进的是外置的那一份，主目录看得到
        self.assertEqual(git(tree, 'status', '--porcelain'), '')

    def test_switching_branch_does_not_change_the_records(self):
        req = self.new_req()
        git(self.root, 'checkout', '-q', '-b', 'other')
        self.assertTrue(any(r['id'] == req['id'] for r in rf.load_requirements(self.root)))
        git(self.root, 'checkout', '-q', 'main')
        self.assertTrue(any(r['id'] == req['id'] for r in rf.load_requirements(self.root)))

    def test_a_moved_or_cloned_repository_finds_its_records_again(self):
        req = self.new_req()
        moved = self.base / 'moved'
        git(self.base, 'clone', '-q', str(self.root), str(moved))
        self.assertFalse((moved / rf.DIR).exists())
        self.assertEqual(common.find_root(moved), moved.resolve())
        self.assertTrue((moved / rf.DIR).is_symlink())
        self.assertTrue(any(r['id'] == req['id'] for r in rf.load_requirements(moved)))
        self.assertEqual(git(moved, 'status', '--porcelain'), '')  # 链接不进 Git（.gitignore 和 info/exclude）

    def test_a_real_directory_is_never_replaced(self):
        legacy = self.base / 'legacy'
        legacy.mkdir()
        rf.init(legacy, hooks=False)
        (legacy / rf.DIR).unlink()
        (legacy / rf.DIR).mkdir()
        (legacy / rf.DIR / 'keep.md').write_text('x', encoding='utf-8')
        self.assertIsNone(common.link_facts(legacy))
        self.assertFalse((legacy / rf.DIR).is_symlink())
        self.assertTrue((legacy / rf.DIR / 'keep.md').exists())

    def test_an_old_project_is_recognised(self):
        old = self.base / 'old'
        (old / rf.DIR / 'requirements').mkdir(parents=True)
        self.assertTrue(common.is_legacy(old))
        self.assertFalse(common.is_legacy(self.root))
        code, out, _ = self.cli('data', 'where', '--root', str(old), '--json')
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out)['legacy'])
        self.assertFalse((old / common.ID_FILE).exists())  # 只看，不改


class ExportImportTests(Base):
    def test_export_then_import_on_another_machine(self):
        req = self.new_req()
        archive = self.base / 'backup.tar.gz'
        code, out, err = self.cli('data', 'export', str(archive), '--root', str(self.root), '--json')
        self.assertEqual(code, 0, err)
        names = __import__('tarfile').open(archive).getnames()
        self.assertFalse([n for n in names if n.startswith('data/bin/') or n.startswith('data/site/')])
        other_home = self.base / 'home2'
        clone = self.base / 'clone'
        git(self.base, 'clone', '-q', str(self.root), str(clone))
        with mock.patch.dict(os.environ, {'RELAYFORGE_HOME': str(other_home)}):
            self.assertEqual(rf.load_requirements(clone), [])
            code, out, err = self.cli('data', 'import', str(archive), '--root', str(clone), '--json', app=True)
            self.assertEqual(code, 0, err)
            self.assertTrue(any(r['id'] == req['id'] for r in rf.load_requirements(clone)))
            # 本机已经有记录：不加 --replace 不换；加了以后原来那份改名留着
            code, out, err = self.cli('data', 'import', str(archive), '--root', str(clone), app=True)
            self.assertEqual(code, 1)
            self.assertIn('--replace', err)
            code, out, err = self.cli('data', 'import', str(archive), '--root', str(clone), '--replace', '--json', app=True)
            self.assertEqual(code, 0, err)
            kept = json.loads(out)['kept']
            self.assertTrue(Path(kept).is_dir())

    def test_import_refuses_another_projects_export(self):
        archive = self.base / 'backup.tar.gz'
        self.cli('data', 'export', str(archive), '--root', str(self.root))
        stranger = self.base / 'stranger'
        stranger.mkdir()
        git(stranger, 'init', '-q', '-b', 'main')
        rf.init(stranger, hooks=False)
        code, _, err = self.cli('data', 'import', str(archive), '--root', str(stranger), app=True)
        self.assertEqual(code, 1)
        self.assertIn('不是同一个', err)

    def test_import_is_refused_in_an_ai_session(self):
        archive = self.base / 'backup.tar.gz'
        self.cli('data', 'export', str(archive), '--root', str(self.root))
        with mock.patch.dict(os.environ, {'CLAUDECODE': '1'}):
            code, _, err = self.cli('data', 'import', str(archive), '--root', str(self.root))
        self.assertEqual(code, 1)
        self.assertIn('只能由你在应用里做', err)


class PublishTests(Base):
    def setUp(self):
        super().setUp()
        self.remote = self.base / 'remote.git'
        git(self.base, 'init', '-q', '--bare', '-b', 'main', str(self.remote))
        git(self.root, 'remote', 'add', 'origin', str(self.remote))
        git(self.root, 'push', '-q', '-u', 'origin', 'main')

    def finish(self, req, code_change=True):
        tree = Path(req['worktree'])
        if code_change:
            (tree / 'app.txt').write_text('v2\n', encoding='utf-8')
            git(tree, 'add', '-A')
            git(tree, 'commit', '-qm', 'work')
        else:  # 只带来记录：一条进行中的裁定
            rf.write_doc(self.root / rf.DIR / 'decisions' / 'D-0001-按钮放右边.md',
                         {'id': 'D-0001', 'type': 'decision', 'title': '按钮放右边', 'status': 'current', 'in_progress': req['id'], 'updated': '2026-11-20'},
                         {h: [] for h in rf.DECISION_HEADS})
        path = next((self.root / rf.DIR / 'requirements').glob(f'{req["id"]}-*.md'))
        meta, sections = rf.read_doc(path)
        sections['验收条件'] = ['- 按钮在〔S-x#1〕']
        rf.write_doc(path, meta, sections)
        for action in ('start', 'done'):
            code, out, err = self.cli('req', action, req['id'], '--root', str(self.root), app=True)
            self.assertEqual(code, 0, f'{action}: {out} {err}')

    def test_only_requirements_with_a_merge_count_as_unpublished(self):
        with_code, facts_only = self.new_req('有代码的'), self.new_req('只改记录的')
        self.finish(with_code)
        self.finish(facts_only, code_change=False)
        st = publish.publish_status(self.root)
        self.assertEqual([r['id'] for r in st['unpublished']['requirements']], [with_code['id']])
        self.assertEqual(st['unpublished']['facts'], 0)
        meta = rf.read_doc(next((self.root / rf.DIR / 'requirements').glob(f'{with_code["id"]}-*.md')))[0]
        self.assertEqual(meta.get('merged_into'), 'main')
        self.assertTrue(meta.get('merge_commit'))
        # 发布：不再多出一个事实提交，记录只存本机；推送成功后待发布归零、记录显示已发布
        ahead = st['ahead']
        code, out, err = self.cli('publish', 'run', '--json', '--root', str(self.root), app=True)
        self.assertEqual(code, 0, err)
        done = json.loads(out)
        self.assertEqual(done['commits'], ahead)
        after = publish.publish_status(self.root)
        self.assertEqual(after['unpublished']['requirements'], [])
        self.assertEqual(after['ahead'], 0)
        records = publish.publish_list(self.root)
        self.assertEqual(len(records), 1)
        self.assertTrue(records[0]['published'])
        self.assertEqual(git(self.root, 'status', '--porcelain'), '')

    def test_the_count_belongs_to_the_branch_it_was_merged_into(self):
        req = self.new_req()
        self.finish(req)
        self.assertEqual(len(publish.publish_status(self.root)['unpublished']['requirements']), 1)
        git(self.root, 'checkout', '-q', '-b', 'b', 'origin/main')
        git(self.root, 'push', '-q', '-u', 'origin', 'b')
        st = publish.publish_status(self.root)
        self.assertEqual(st['unpublished']['requirements'], [])  # B 上没有它的合并提交
        self.assertTrue(any(r['id'] == req['id'] for r in rf.load_requirements(self.root)))  # 记录照常看得到


if __name__ == '__main__':
    unittest.main()

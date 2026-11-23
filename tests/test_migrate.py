"""旧项目迁移（rf.py data migrate）：记录还是仓库里的 .relayforge/ 目录的项目，迁到本机 ~/.relayforge/projects/<ID>/。
整个目录原样搬过去（包括没提交的改动），仓库里只提交一次（去掉 .relayforge/、加 .relayforge.json 和 .gitignore 一行），
原来那个目录挪到本机留作备份；以前写进仓库给 AI 的约定（AGENTS.md 里那一段、CLAUDE.md、两个技能）一并拿掉，用户自己写的和别的改动不动；迁移前开着的需求照常做完；只由用户触发；失败退回原样。"""
import json
import os
import unittest
from pathlib import Path
from unittest import mock

from tests.test_datastore import Base, git
import rf  # noqa: E402
from relayforge.core import common  # noqa: E402
from relayforge.sessions.trailers import ensure_here  # noqa: E402


class MigrateBase(Base):
    def setUp(self):
        super().setUp()
        self.old = self.base / 'old'
        self.old.mkdir()
        git(self.old, 'init', '-q', '-b', 'main')
        (self.old / 'app.txt').write_text('v1\n', encoding='utf-8')
        (self.old / '.gitignore').write_text('node_modules/\n', encoding='utf-8')
        git(self.old, 'add', '-A')
        git(self.old, 'commit', '-qm', 'first')
        with mock.patch.dict(os.environ, {'RELAYFORGE_FACTS_IN_REPO': '1'}):
            rf.init(self.old, hooks=False)
        git(self.old, 'add', '-A')
        git(self.old, 'commit', '-qm', 'init（旧布局）')
        self.assertTrue(common.is_legacy(self.old))
        self.agents = (self.old / 'AGENTS.md').read_text(encoding='utf-8')

    def migrate(self, app=True):
        code, out, err = self.cli('data', 'migrate', '--root', str(self.old), '--json', app=app)
        lines = out.strip().splitlines()
        return code, (json.loads(lines[-1]) if lines else {}), err

    def snapshot(self):
        base = self.old / rf.DIR
        return {p.relative_to(base).as_posix(): p.read_bytes() for p in sorted(base.rglob('*')) if p.is_file()}


class MigrateTests(MigrateBase):
    def test_records_move_out_with_uncommitted_changes_and_one_commit_is_made(self):
        made = rf.req_new(self.old, '迁移前就有的需求')
        git(self.old, 'add', '-A')
        git(self.old, 'commit', '-qm', '需求')
        overview = self.old / rf.DIR / 'product' / 'overview.md'
        overview.write_text(overview.read_text(encoding='utf-8') + '\n<!-- 还没提交的改动 -->\n', encoding='utf-8')
        (self.old / rf.DIR / 'decisions' / 'D-0999-没加入git的.md').write_text('x\n', encoding='utf-8')
        (self.old / 'app.txt').write_text('我正在改的代码\n', encoding='utf-8')
        before = self.snapshot()
        head = git(self.old, 'rev-parse', 'HEAD').strip()

        code, out, err = self.migrate()
        self.assertEqual(code, 0, err)
        store = Path(out['store'])
        self.assertTrue((self.old / rf.DIR).is_symlink())
        self.assertEqual((self.old / rf.DIR).resolve(), store.resolve())
        self.assertTrue(str(store).startswith(str(self.home / 'projects')))
        self.assertFalse(common.is_legacy(self.old))
        self.assertEqual(json.loads((self.old / common.ID_FILE).read_text(encoding='utf-8'))['id'], out['id'])
        # 记录一个不少，包括没提交的改动和没加入 Git 的文件
        for rel, blob in before.items():
            if rel.startswith('bin/') or rel in ('FORMAT.md',):
                continue  # 程序副本和格式说明换成这个版本的
            self.assertEqual((store / rel).read_bytes(), blob, rel)
        self.assertTrue((store / 'CONVENTIONS.md').exists())
        self.assertTrue(any(r['id'] == made['id'] for r in rf.load_requirements(self.old)))
        # 仓库里原来那份挪到本机留着
        backup = Path(out['backup'])
        self.assertEqual({p.relative_to(backup).as_posix() for p in backup.rglob('*') if p.is_file()}, set(before))
        # 只提交了一次，只动了 .relayforge/、.relayforge.json、.gitignore
        self.assertEqual(git(self.old, 'rev-parse', 'HEAD~1').strip(), head)
        touched = set(git(self.old, 'show', '--name-only', '--format=', 'HEAD').split())
        self.assertTrue(touched)
        allowed = (common.ID_FILE, '.gitignore', 'AGENTS.md', 'CLAUDE.md', '.claude/skills/rf-sync/SKILL.md', '.claude/skills/rf-confirm/SKILL.md')
        self.assertTrue(all(p.startswith(rf.DIR + '/') or p in allowed for p in touched), touched)
        self.assertFalse([p for p in git(self.old, 'ls-files').split() if p.startswith(rf.DIR + '/')])
        self.assertIn('/.relayforge', (self.old / '.gitignore').read_text(encoding='utf-8').splitlines())
        self.assertIn('node_modules/', (self.old / '.gitignore').read_text(encoding='utf-8'))
        # 别的改动原样留着；以前写进仓库给 AI 的约定拿掉了（AGENTS.md 只有那一段，整个删掉；CLAUDE.md 只有 @AGENTS.md）
        self.assertEqual(git(self.old, 'status', '--porcelain'), ' M app.txt\n')
        self.assertIn('relayforge:begin', self.agents)
        for gone in ('AGENTS.md', 'CLAUDE.md', '.claude/skills/rf-sync/SKILL.md', '.claude/skills/rf-confirm/SKILL.md'):
            self.assertFalse((self.old / gone).exists(), gone)
            self.assertIn(gone, touched)
        self.assertEqual(sorted(out['removed_from_repo']), sorted(['AGENTS.md', 'CLAUDE.md', '.claude/skills/rf-sync/SKILL.md', '.claude/skills/rf-confirm/SKILL.md']))
        # 再迁一次：已经是新的方式了
        code, out, _ = self.migrate()
        self.assertEqual(code, 1)
        self.assertIn('不用迁移', out['error'])

    def test_what_the_user_wrote_in_agents_and_claude_stays(self):
        (self.old / 'AGENTS.md').write_text('# 我自己的规则\n- 两个空格缩进\n\n' + self.agents, encoding='utf-8')
        (self.old / 'CLAUDE.md').write_text('@AGENTS.md\n我自己加的一行\n', encoding='utf-8')
        git(self.old, 'commit', '-qam', '我自己的规则')
        code, out, err = self.migrate()
        self.assertEqual(code, 0, err)
        self.assertEqual((self.old / 'AGENTS.md').read_text(encoding='utf-8'), '# 我自己的规则\n- 两个空格缩进\n')
        self.assertEqual((self.old / 'CLAUDE.md').read_text(encoding='utf-8'), '@AGENTS.md\n我自己加的一行\n')
        self.assertEqual(git(self.old, 'status', '--porcelain'), '')

    def test_uncommitted_edits_to_agents_are_not_touched(self):
        (self.old / 'AGENTS.md').write_text(self.agents + '\n我正在改\n', encoding='utf-8')
        code, out, err = self.migrate()
        self.assertEqual(code, 0, err)
        self.assertEqual((self.old / 'AGENTS.md').read_text(encoding='utf-8'), self.agents + '\n我正在改\n')
        self.assertEqual(git(self.old, 'status', '--porcelain'), ' M AGENTS.md\n')
        self.assertTrue((self.old / 'CLAUDE.md').exists())  # AGENTS.md 还在，CLAUDE.md 也留着
        self.assertFalse((self.old / '.claude/skills/rf-sync/SKILL.md').exists())

    def test_a_requirement_opened_before_migrating_is_finished_afterwards(self):
        with mock.patch.dict(os.environ, {'RELAYFORGE_FACTS_IN_REPO': '1'}):
            made = rf.req_new(self.old, '迁移前开工的需求')
            path = next((self.old / rf.DIR / 'requirements').glob(f'{made["id"]}-*.md'))
            meta, sections = rf.read_doc(path)
            sections['验收条件'] = ['- 按钮在〔S-x#1〕']
            rf.write_doc(path, meta, sections)
            git(self.old, 'add', '-A')
            git(self.old, 'commit', '-qm', '需求')
            code, out, err = self.cli('req', 'start', made['id'], '--root', str(self.old), app=True)
            self.assertEqual(code, 0, out + err)
        req = next(r for r in rf.load_requirements(self.old) if r['id'] == made['id'])
        code, _, err = self.migrate()
        self.assertEqual(code, 0, err)
        tree = Path(req['worktree'])
        (tree / 'app.txt').write_text('v2\n', encoding='utf-8')
        git(tree, 'commit', '-qam', '迁移以后接着做')
        git(tree, 'merge', '-q', '--no-edit', 'main')  # 开发会话照常把 main 合进来
        self.assertFalse((tree / rf.DIR).exists())  # 分支里旧的 .relayforge/ 随 main 去掉了
        ensure_here(tree)
        self.assertTrue((tree / rf.DIR).is_symlink())  # 应用开会话时接上链接，会话照常用 .relayforge/bin/rf.py
        self.assertTrue((tree / rf.DIR / 'bin' / 'rf.py').exists())
        code, out, err = self.cli('req', 'done', made['id'], '--root', str(self.old), app=True)
        self.assertEqual(code, 0, out + err)
        self.assertEqual((self.old / 'app.txt').read_text(encoding='utf-8'), 'v2\n')
        self.assertTrue((self.old / rf.DIR).is_symlink())  # 合并没有把旧目录带回来
        self.assertFalse([p for p in git(self.old, 'ls-files').split() if p.startswith(rf.DIR + '/')])
        done = next(r for r in rf.load_requirements(self.old) if r['id'] == made['id'])
        self.assertEqual(done['merged_into'], 'main')

    def test_only_the_user_migrates(self):
        before = self.snapshot()
        code, _, err = self.migrate(app=False)
        self.assertEqual(code, 1)
        self.assertIn('只能由你在应用里做', err)
        self.assertTrue(common.is_legacy(self.old))
        self.assertEqual(self.snapshot(), before)

    def test_other_staged_changes_stop_it_before_anything_moves(self):
        (self.old / 'app.txt').write_text('暂存了的\n', encoding='utf-8')
        git(self.old, 'add', 'app.txt')
        stores = set((self.home / 'projects').iterdir())
        code, out, _ = self.migrate()
        self.assertEqual(code, 1)
        self.assertIn('app.txt', out['error'])
        self.assertTrue(common.is_legacy(self.old))
        self.assertEqual(set((self.home / 'projects').iterdir()), stores)

    def test_a_failed_commit_puts_everything_back(self):
        hook = self.old / '.git' / 'hooks' / 'pre-commit'
        hook.write_text('#!/bin/sh\necho 不让提交 >&2\nexit 1\n', encoding='utf-8')
        hook.chmod(0o755)
        (self.old / rf.DIR / 'decisions' / 'D-0999-没加入git的.md').write_text('x\n', encoding='utf-8')
        before, ignore, status = self.snapshot(), (self.old / '.gitignore').read_text(encoding='utf-8'), git(self.old, 'status', '--porcelain')
        agents = (self.old / 'AGENTS.md').read_text(encoding='utf-8')
        code, out, _ = self.migrate()
        self.assertEqual(code, 1)
        self.assertIn('提交迁移没有成功', out['error'])
        self.assertTrue(common.is_legacy(self.old))
        self.assertFalse((self.old / rf.DIR).is_symlink())
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.old / common.ID_FILE).exists())
        self.assertEqual((self.old / '.gitignore').read_text(encoding='utf-8'), ignore)
        self.assertEqual(git(self.old, 'status', '--porcelain'), status)
        self.assertEqual((self.old / 'AGENTS.md').read_text(encoding='utf-8'), agents)
        self.assertTrue((self.old / '.claude/skills/rf-sync/SKILL.md').exists())
        self.assertEqual([p.name for p in (self.home / 'projects').iterdir()], [self.ident])


if __name__ == '__main__':
    unittest.main()

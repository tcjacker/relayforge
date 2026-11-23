"""需求之间的冲突（rf.py conflicts）：进行中的需求和修复各改了哪些文件、谁和谁改到同一处、和合并目标或彼此合并会不会冲突。"""
import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'backend'))
import rf  # noqa: E402
from tests.test_issues import IssueBase, git  # noqa: E402


class ConflictTests(IssueBase):
    def started(self, title):
        made = rf.req_new(self.root, title)
        path = self.root / made['file']
        meta, sections = rf.read_doc(path)
        rf.add_line(sections, '需求说明', '- 说明〔S-test#1〕')
        rf.add_line(sections, '要改的功能点', '- RF-101 文档登记：改〔S-test#1〕')
        rf.add_line(sections, '验收条件', '- 能用〔S-test#1〕')
        rf.write_doc(path, meta, sections)
        rf.req_start(self.root, made['id'])
        return made

    def test_overlaps_and_trial_merges(self):
        self.commit_in(self.root, 'shared.py', 'a\nb\nc\n', '共用的文件')
        self.commit_in(self.root, 'other.py', 'x\n', '另一个文件')
        a, b, c = self.started('改导出'), self.started('改导入'), self.started('改列表')
        self.commit_in(a['worktree'], 'shared.py', 'a\nB-from-A\nc\n', 'A 改共用')
        self.commit_in(b['worktree'], 'shared.py', 'a\nB-from-B\nc\n', 'B 也改共用')
        self.commit_in(c['worktree'], 'other.py', 'y-from-C\n', 'C 改另一个')
        (Path(a['worktree']) / 'notes.md').write_text('还没提交\n', encoding='utf-8')
        (Path(b['worktree']) / 'notes.md').write_text('也没提交\n', encoding='utf-8')
        self.commit_in(self.root, 'other.py', 'z-on-main\n', '主分支也改了另一个')

        out = rf.conflicts(self.root)
        items = {i['id']: i for i in out['items']}
        self.assertEqual(set(items), {a['id'], b['id'], c['id']})
        self.assertEqual(items[a['id']]['committed'], ['shared.py'])  # 记录目录（.relayforge/）不算改动
        self.assertEqual(items[a['id']]['uncommitted'], ['notes.md'])
        self.assertEqual(items[a['id']]['base_conflicts'], [])  # 主分支没动 shared.py：和合并目标合并不冲突
        self.assertEqual(items[c['id']]['base_conflicts'], ['other.py'])  # 主分支后来也改了 other.py
        pairs = {frozenset((p['a'], p['b'])): p for p in out['pairs']}  # 一对里谁在前按编号排，编号带随机部分
        ab = frozenset((a['id'], b['id']))
        self.assertEqual(set(pairs), {ab})  # C 和谁都没改到同一个文件
        self.assertEqual(pairs[ab]['files'], ['notes.md', 'shared.py'])
        self.assertEqual(pairs[ab]['conflicts'], ['shared.py'])

        text = rf.conflicts_text(out)
        self.assertIn(f'{c["id"]} 改列表：改了 1 个文件（已提交）；和 ', text)
        self.assertIn('合并会冲突：other.py', text)
        first, second = sorted((a['id'], b['id']))
        self.assertIn(f'{first} 和 {second} 都改了：notes.md、shared.py；两个分支合并会冲突：shared.py', text)
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(rf.main(['conflicts', '--root', str(self.root), '--json']), 0)
        self.assertEqual(json.loads(buf.getvalue())['base'], out['base'])

    def test_a_shared_file_that_merges_cleanly_is_only_an_overlap(self):
        self.commit_in(self.root, 'shared.py', 'a\nb\nc\nd\ne\nf\ng\n', '共用的文件')
        a, b = self.started('改开头'), self.started('改结尾')
        self.commit_in(a['worktree'], 'shared.py', 'A\nb\nc\nd\ne\nf\ng\n', 'A 改开头')
        self.commit_in(b['worktree'], 'shared.py', 'a\nb\nc\nd\ne\nf\nG\n', 'B 改结尾')
        pair = rf.conflicts(self.root)['pairs'][0]
        self.assertEqual((pair['files'], pair['conflicts']), (['shared.py'], []))

    def test_nothing_in_progress(self):
        self.assertEqual(rf.conflicts(self.root)['items'], [])
        self.assertIn('没有进行中', rf.conflicts_text(rf.conflicts(self.root)))


class PredictTests(IssueBase):
    def started_with(self, title, features, start=True):
        made = rf.req_new(self.root, title)
        path = self.root / made['file']
        meta, sections = rf.read_doc(path)
        rf.add_line(sections, '需求说明', '- 说明〔S-test#1〕')
        for f in features:
            rf.add_line(sections, '要改的功能点', f'- {f} 某个功能点：改〔S-test#1〕')
        rf.add_line(sections, '验收条件', '- 能用〔S-test#1〕')
        rf.write_doc(path, meta, sections)
        if start:
            rf.req_start(self.root, made['id'])
        return made

    def test_history_predicts_the_files_and_who_it_may_run_into(self):
        self.commit_in(self.root, 'export.py', 'v0\n', '基础')
        self.commit_in(self.root, 'old.py', 'v0\n', '后来删掉的')
        done = self.started_with('导出改版', ['RF-101'])
        self.commit_in(done['worktree'], 'export.py', 'v1\n', '改导出')
        self.commit_in(done['worktree'], 'old.py', 'v1\n', '也改了旧文件')
        rf.req_done(self.root, done['id'])
        git(self.root, 'rm', '-q', 'old.py')
        git(self.root, 'commit', '-qm', '删掉旧文件')
        busy = self.started_with('导出加筛选', ['RF-102'])  # 不同的功能点，但已经在改 export.py
        self.commit_in(busy['worktree'], 'export.py', 'v2\n', '筛选')
        same = self.started_with('导出加合计', ['RF-101'])  # 同样要改 RF-101，还没动代码
        new = self.started_with('导出加分页', ['RF-101'], start=False)

        out = rf.predict(self.root, new['id'])
        self.assertEqual(out['features'], ['RF-101'])
        self.assertEqual([h['id'] for h in out['history']], [done['id']])
        self.assertEqual([p['path'] for p in out['predicted']], ['export.py'])  # old.py 已经删了，不算
        against = {a['id']: a for a in out['against']}
        self.assertEqual(set(against), {busy['id'], same['id']})
        self.assertEqual((against[busy['id']]['features'], against[busy['id']]['files']), ([], ['export.py']))
        self.assertEqual((against[same['id']]['features'], against[same['id']]['files']), (['RF-101'], []))
        text = rf.predict_text(out)
        self.assertIn('预计会动到的文件：export.py（1 次）', text)
        self.assertIn(f'可能撞上 {busy["id"]}《导出加筛选》：已经在改预计会动到的 export.py', text)
        self.assertIn(f'可能撞上 {same["id"]}《导出加合计》：同样要改 RF-101', text)
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(rf.main(['conflicts', '--predict', new['id'], '--root', str(self.root), '--json']), 0)
        self.assertEqual(json.loads(buf.getvalue())['id'], new['id'])
        with self.assertRaises(ValueError):
            rf.predict(self.root, 'R-00000000-0000')

    def test_no_history_says_so(self):
        new = self.started_with('全新的东西', ['RF-101'], start=False)
        out = rf.predict(self.root, new['id'])
        self.assertEqual((out['history'], out['predicted'], out['against']), ([], [], []))
        self.assertIn('预测不了', rf.predict_text(out))

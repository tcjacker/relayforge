"""测试集：数据格式、去重、执行器的三种结果、按需求运行、进行中的落定和撤掉、改旧数据的确认、过期、提示、结束检查、桥接文件。"""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'backend'))
import rf  # noqa: E402
from tests.app_fixture import app_marker, in_repo_layout  # noqa: E402
from relayforge.testsets import store as T  # noqa: E402
from relayforge.testsets import decisions as TD  # noqa: E402
from relayforge.testsets import edits as TE  # noqa: E402
from relayforge.testsets import views as TV  # noqa: E402
from relayforge.testsets import checks as TC  # noqa: E402
from relayforge.testsets import runs as TR  # noqa: E402


def git(place, *args):
    return subprocess.run(['git', '-c', 'core.quotepath=off', *args], cwd=place, check=True, capture_output=True, text=True).stdout


ENTRY = '''from calc import add
def run(input):
    print("被测代码打印的东西不会搅乱协议")
    return add(input["a"], input["b"])
'''


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve() / 'project'
        self.root.mkdir()
        env = mock.patch.dict(os.environ, {
            'HOME': str(Path(self.tmp.name) / 'home'), 'RELAYFORGE_HOME': str(Path(self.tmp.name) / 'home' / '.relayforge'),
            'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@example.com', 'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@example.com',
        })
        env.start()
        self.addCleanup(env.stop)
        for name in (rf.GUARD, 'CLAUDE_PROJECT_DIR', 'CLAUDE_CODE_SESSION_ID', 'RELAYFORGE_SESSION_ID', 'RELAYFORGE_TEST_PYTHON'):
            os.environ.pop(name, None)
        rf.init(self.root, hooks=False)
        base = self.root / rf.DIR
        rf.write_doc(base / 'modules' / 'M-01-账单.md', {'id': 'M-01', 'type': 'module', 'title': '账单', 'status': 'current'}, {'负责什么': [], '变更记录': []})
        for ident, title in (('RF-101', '加法'), ('RF-102', '导出')):
            rf.write_doc(base / 'features' / f'{ident}-{title}.md', {'id': ident, 'type': 'feature', 'title': title, 'status': 'current', 'module': 'M-01', 'depends_on': []},
                         {h: [] for h in rf.FEATURE_HEADS})
        (self.root / 'calc.py').write_text('def add(a, b):\n    return a + b\n', encoding='utf-8')
        (self.root / 'tests').mkdir()
        (self.root / 'tests' / 'rf_add.py').write_text(ENTRY, encoding='utf-8')
        git(self.root, 'init', '-q', '-b', 'main')
        git(self.root, 'add', '-A')
        git(self.root, 'commit', '-qm', 'init')

    def landed(self, cases=None):
        """A test set already in use on the main line (as a finished requirement would leave it)."""
        ts = TE.testset_new(self.root, '加法', 'RF-101', 'tests/rf_add.py')
        TE.testset_add(self.root, ts['id'], cases or [{'name': '正数', 'input': {'a': 1, 'b': 2}, 'expect': 3},
                                                     {'name': '负数', 'cat': '边界', 'input': {'a': -1, 'b': -2}, 'expect': -3}])
        rf.commit_facts(self.root, 'tests')
        return ts['id']

    def requirement(self, features='RF-101 加法：改一改'):
        req = rf.req_new(self.root, '改加法')
        path = self.root / req['file']
        meta, sections = rf.read_doc(path)
        sections['要改的功能点'] = [f'- {features}〔S-x#1〕']
        sections['验收条件'] = ['- 加法算对〔S-x#1〕']
        rf.write_doc(path, meta, sections)
        rf.req_start(self.root, req['id'])
        return req['id'], Path(req['worktree'])

    def cli(self, *args, cwd=None):
        out = io.StringIO()
        old = os.getcwd()
        os.chdir(cwd or self.root)
        try:
            with redirect_stdout(out), redirect_stderr(io.StringIO()):
                code = rf.main(['test', *args])
        finally:
            os.chdir(old)
        return code, out.getvalue()


class FormatTests(Base):
    def test_new_add_and_duplicates(self):
        ts = TE.testset_new(self.root, '  加法  ', 'RF-101', 'tests/rf_add.py', checks=['加法算对'])
        self.assertEqual((ts['id'], ts['title'], ts['in_progress']), ('TS-001', '加法', None))
        data = json.loads((self.root / '.relayforge/tests/TS-001.json').read_text(encoding='utf-8'))
        self.assertEqual((data['type'], data['feature'], data['entry'], data['lang'], data['status'], data['checks']), ('testset', 'RF-101', 'tests/rf_add.py', 'python', 'current', ['加法算对']))
        out = TE.testset_add(self.root, 'TS-1', [{'name': '正数', 'input': {'a': 1, 'b': 2}, 'expect': 3},
                                                {'name': '还是正数', 'input': {'b': 2, 'a': 1}, 'expect': 3},
                                                {'name': '矛盾', 'input': {'a': 1, 'b': 2}, 'expect': 4},
                                                {'name': '报错', 'cat': 'error', 'input': {'a': 1, 'b': 'x'}, 'raises': 'TypeError'}])
        self.assertEqual([c['n'] for c in out['added']], [1, 2])
        self.assertEqual(len(out['skipped']), 2)
        self.assertIn('完全相同', out['skipped'][0]['why'])
        self.assertIn('互相矛盾', out['skipped'][1]['why'])
        case = json.loads((self.root / '.relayforge/tests/TS-001.json').read_text(encoding='utf-8'))['cases'][1]
        self.assertEqual((case['cat'], case['raises'], case['by'], case['unseen']), ('异常', 'TypeError', 'ai', True))
        with self.assertRaisesRegex(ValueError, 'name'):
            TE.testset_add(self.root, 'TS-001', [{'input': 1, 'expect': 1}])
        with self.assertRaisesRegex(ValueError, 'cat'):
            TE.testset_add(self.root, 'TS-001', [{'name': 'x', 'cat': '奇怪', 'input': 5, 'expect': 1}])
        with self.assertRaisesRegex(ValueError, '功能点'):
            TE.testset_new(self.root, 'x', 'RF-999', 'tests/a.py')
        with self.assertRaisesRegex(ValueError, '代码目录'):
            TE.testset_new(self.root, 'x', 'RF-101', '.relayforge/tests/a.py')

    def test_compare_is_strict_json_equality(self):
        self.assertEqual(T.compare({'a': [1, 2.0]}, {'a': [1.0, 2]}), [])
        self.assertTrue(T.compare(True, 1))
        self.assertTrue(T.compare([1, 2], [1]))
        self.assertEqual(T.compare({'a': 1}, {'a': 1, 'b': 2}), ['返回值多出字段 b（实际是 2）'])

    def test_differences_read_as_plain_sentences(self):
        self.assertEqual(T.compare([1], []), ['返回值：数量不同，期望 1 项，实际 0 项'])
        self.assertEqual(T.compare([1, 2], [1, 3]), ['返回值的第 2 项：值不同，期望 2，实际 3'])
        self.assertEqual(T.compare({'items': [{'amount': 12}]}, {'items': [{'amount': 13}]}), ['返回值的 items 的第 1 项的 amount：值不同，期望 12，实际 13'])
        self.assertEqual(T.compare({'total': 3}, {'total': '3'}), ['返回值的 total：类型不同，期望是数字 3，实际是文字 "3"'])
        self.assertEqual(T.compare({'a': 1, 'b': None}, {'a': 1}), ['返回值缺少字段 b（期望是 null）'])
        self.assertEqual(T.compare([{'id': 'b1'}], [{'id': 'b1', 'note': 'x'}]), ['返回值的第 1 项多出字段 note（实际是 "x"）'])
        self.assertEqual(T.compare(None, 5), ['返回值：期望 null，实际 5'])
        self.assertEqual(T.compare(True, 1), ['返回值：类型不同，期望是布尔值 true，实际是数字 1'])
        self.assertEqual(T.path_words(('items', 0, 'amount')), '返回值的 items 的第 1 项的 amount')


class RunTests(Base):
    @in_repo_layout
    def test_pass_fail_exception_and_raises(self):
        ident = self.landed([{'name': '对', 'input': {'a': 1, 'b': 2}, 'expect': 3},
                             {'name': '期望写错', 'input': {'a': 2, 'b': 2}, 'expect': 5},
                             {'name': '抛异常', 'input': {'a': 1}, 'expect': 1},
                             {'name': '期望报错', 'cat': '异常', 'input': {'a': 1, 'b': 'x'}, 'raises': 'TypeError'}])
        out = TR.testset_run(self.root, [ident])
        s = out['sets'][0]
        self.assertEqual((s['result'], s['counts']), ('fail', {'pass': 2, 'fail': 2, 'broken': 0, 'total': 4}))
        by = {c['name']: c for c in s['cases']}
        self.assertEqual(by['期望写错']['actual'], 4)
        self.assertIn('期望 5，实际 4', by['期望写错']['diff'][0])
        self.assertIn('KeyError', by['抛异常']['error'])
        self.assertEqual(by['期望报错']['status'], 'pass')
        stats = json.loads((self.root / '.relayforge/tests/stats' / f'{ident}.json').read_text(encoding='utf-8'))
        self.assertEqual((stats['runs'], stats['failed'], stats['recent'][-1]['failing']), (1, 1, [2, 3]))
        # 完整结果不进 Git，计数进
        ignored = subprocess.run(['git', 'check-ignore', '-q', f'.relayforge/tests/results/{ident}/main.json'], cwd=self.root).returncode
        self.assertEqual(ignored, 0)
        self.assertEqual(subprocess.run(['git', 'check-ignore', '-q', f'.relayforge/tests/stats/{ident}.json'], cwd=self.root).returncode, 1)
        one = TR.testset_run(self.root, [ident], case=1)
        self.assertEqual((one['sets'][0]['partial'], one['sets'][0]['counts']['total']), (True, 1))
        self.assertEqual(T.read_stats(self.root, ident)['runs'], 1)  # 只跑一条不算一次运行

    def test_broken_when_the_code_cannot_load_crashes_or_hangs(self):
        ident = self.landed()
        entry = self.root / 'tests' / 'rf_add.py'
        for code, word in (('def run(input:\n', 'SyntaxError'), ('import os\ndef run(input):\n    os._exit(3)\n', '中途退出'),
                           ('import time\ndef run(input):\n    time.sleep(30)\n', '超过 1 秒'), ('x = 1\n', 'run(input)')):
            entry.write_text(code, encoding='utf-8')
            s = TR.testset_run(self.root, [ident], timeout=1)['sets'][0]
            self.assertEqual(s['result'], 'broken', code)
            self.assertIn(word, s['error'])
            self.assertTrue(all(c['status'] == 'broken' for c in s['cases']))
        self.assertEqual([h['key'] for h in TV.testset_list(self.root)['sets'][0]['hints']], ['broken'])

    def test_hints_never_failed_and_flaky(self):
        self.assertEqual([h['key'] for h in TV.hints({'runs': 20, 'failed': 0, 'recent': []})], ['never_failed'])
        self.assertEqual(TV.hints({'runs': 19, 'failed': 0, 'recent': []}), [])
        recent = [{'result': 'pass', 'failing': [], 'code': 'a', 'data': 'd'}, {'result': 'fail', 'failing': [2], 'code': 'a', 'data': 'd'},
                  {'result': 'pass', 'failing': [], 'code': 'b', 'data': 'd'}]
        self.assertEqual([h['key'] for h in TV.hints({'runs': 3, 'failed': 1, 'recent': recent})], ['flaky'])
        self.assertEqual(TV.hints({'runs': 2, 'failed': 1, 'recent': recent[1:]}), [])  # 代码变了，结果变了，不算

    def test_bridge_lets_unittest_run_the_sets(self):
        ident = self.landed()
        made = TR.testset_bridge(self.root)
        self.assertEqual(made['relative'], 'tests/test_relayforge_testsets.py')
        run = lambda: subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests'], cwd=self.root, capture_output=True, text=True)  # noqa: E731
        done = run()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn('Ran 1 test', done.stderr)
        TE.testset_add(self.root, ident, [{'name': '错的', 'input': {'a': 2, 'b': 2}, 'expect': 5}])
        done = run()
        self.assertNotEqual(done.returncode, 0)
        self.assertIn('错的', done.stderr)
        self.assertEqual(T.read_stats(self.root, ident)['runs'], 0)  # 桥接不记结果


class RequirementTests(Base):
    def test_requirement_run_has_its_own_and_old_sets_and_lands_on_done(self):
        old = self.landed()
        TE.testset_new(self.root, '导出', 'RF-102', 'tests/rf_add.py')  # 不在这个需求动到的功能点上
        rid, place = self.requirement()
        mine = TE.testset_new(place, '加法的新行为', 'RF-101', 'tests/rf_add.py', checks=['1'])
        self.assertEqual((mine['in_progress'], mine['requirement'], mine['checks']), (rid, rid, ['加法算对']))
        TE.testset_add(place, mine['id'], [{'name': '零', 'input': {'a': 0, 'b': 0}, 'expect': 0}])
        TE.testset_add(place, old, [{'name': '大数', 'input': {'a': 10, 'b': 20}, 'expect': 30}])  # 给落定的测试集加：这一条进行中
        main = TR.testset_run(self.root, everything=True)
        self.assertEqual(sorted(s['id'] for s in main['sets']), [old, 'TS-002'])
        self.assertEqual(next(s for s in main['sets'] if s['id'] == old)['counts']['total'], 2)  # 主线看不到进行中的那条
        self.assertIn(mine['id'], [x['id'] for x in main['skipped']])
        out = TR.testset_run(place)
        self.assertEqual(out['scope'], rid)
        self.assertEqual({s['id']: s['origin'] for s in out['sets']}, {mine['id']: 'new', old: 'existing'})
        self.assertEqual(next(s for s in out['sets'] if s['id'] == old)['counts']['total'], 3)
        self.assertEqual(out['by_origin']['new']['sets'], 1)
        listed = TV.testset_list(self.root, req=rid)
        self.assertEqual(sorted(r['id'] for r in listed['sets']), sorted([old, mine['id']]))
        # 完成：落定，完成记录里写测试集的结论
        (place / 'calc.py').write_text('def add(a, b):\n    return b + a\n', encoding='utf-8')
        git(place, 'add', '-A')
        git(place, 'commit', '-qm', 'work')
        rf.req_done(self.root, rid)
        data = json.loads((self.root / '.relayforge/tests' / f'{mine["id"]}.json').read_text(encoding='utf-8'))
        self.assertNotIn('in_progress', data)
        self.assertFalse(any('in_progress' in c for c in json.loads((self.root / '.relayforge/tests' / f'{old}.json').read_text(encoding='utf-8'))['cases']))
        record = (self.root / rf.load_requirements(self.root)[0]['file']).read_text(encoding='utf-8')
        self.assertIn('测试集：2 个（新建 1 个，动到的功能点原有 1 个），共 4 条；最近一次运行通过 4 条', record)
        self.assertEqual(TR.testset_run(self.root, everything=True)['totals']['total'], 4)
        self.assertEqual(rf.trace_ref(self.root, rid)['testsets'][0]['id'], mine['id'])

    def test_drop_shelves_and_restore_brings_back(self):
        old = self.landed()
        rid, place = self.requirement()
        mine = TE.testset_new(place, '新的', 'RF-101', 'tests/rf_add.py')
        TE.testset_add(place, old, [{'name': '大数', 'input': {'a': 10, 'b': 20}, 'expect': 30}])
        TE.testset_edit(place, old, 1, {'expect': 4}, why='需求改了行为')
        rf.req_drop(self.root, rid)
        self.assertFalse((self.root / '.relayforge/tests' / f'{mine["id"]}.json').exists())
        self.assertEqual(json.loads((self.root / '.relayforge/history/tests' / f'{mine["id"]}.json').read_text(encoding='utf-8'))['dropped_with'], rid)
        data = T.read_set(self.root, old)
        self.assertEqual(data['cases'][-1]['dropped_with'], rid)
        self.assertEqual(data['changes'][0]['state'], 'dropped')
        self.assertEqual(len(T.view(data, None)), 2)
        rf.req_restore(self.root, rid)
        self.assertTrue((self.root / '.relayforge/tests' / f'{mine["id"]}.json').exists())
        data = T.read_set(self.root, old)
        self.assertEqual((data['cases'][-1]['in_progress'], data['changes'][0]['state']), (rid, 'pending'))

    def test_changing_landed_data_waits_for_the_user(self):
        old = self.landed()
        rid, place = self.requirement()
        with self.assertRaisesRegex(ValueError, '--why'):
            TE.testset_edit(place, old, 1, {'expect': 4})
        made = TE.testset_edit(place, old, 1, {'expect': 4}, why='需求有意改了：1+2 现在要得 4')
        self.assertTrue(made['pending'])
        TE.testset_remove(place, old, 2, why='这种情况不再支持')
        self.assertEqual(T.read_set(self.root, old)['cases'][0]['expect'], 3)  # 主线上的数据不变
        self.assertEqual([c['expect'] for c in T.view(T.read_set(self.root, old), rid)], [4])  # 需求眼里：改了一条、删了一条
        self.assertEqual([c['expect'] for c in T.view(T.read_set(self.root, old), None)], [3, -3])
        run = TR.testset_run(place, [old])['sets'][0]
        self.assertEqual((run['counts']['total'], run['cases'][0]['status']), (1, 'fail'))
        shown = TV.testset_list(self.root)['sets'][0]['pending_changes']
        self.assertEqual([(c['op'], c['old'].get('expect'), (c['new'] or {}).get('expect'), c['by']) for c in shown], [('edit', 3, 4, rid), ('delete', -3, None, rid)])
        TD.testset_reject(self.root, old, 2)  # 不同意删除：丢弃
        TD.testset_approve(self.root, old, 1)  # 同意修改：随需求完成落定
        data = T.read_set(self.root, old)
        self.assertEqual(([c['state'] for c in data['changes']], data['cases'][0]['expect']), (['approved'], 3))
        self.assertEqual(len(T.view(data, rid)), 2)
        (place / 'calc.py').write_text('def add(a, b):\n    return a + b + (1 if (a, b) == (1, 2) else 0)\n', encoding='utf-8')
        git(place, 'add', '-A')
        git(place, 'commit', '-qm', 'work')
        rf.req_done(self.root, rid)
        data = T.read_set(self.root, old)
        self.assertEqual((data['changes'], [c['expect'] for c in data['cases']]), ([], [4, -3]))
        # 主线上 AI 提的改动：同意就直接改；用户自己保存不用确认
        TE.testset_edit(self.root, old, 2, {'expect': -4}, why='试试')
        TD.testset_approve(self.root, old, 3)
        self.assertEqual(T.read_set(self.root, old)['cases'][1]['expect'], -4)
        saved = TD.testset_save(self.root, old, {'cases': [{'n': 1, 'name': '正数', 'input': {'a': 1, 'b': 2}, 'expect': 3}, {'name': '新的', 'input': {'a': 5, 'b': 5}, 'expect': 10}]})
        self.assertEqual(saved['saved'], {'added': [3], 'changed': [1], 'removed': [2], 'voided': []})
        with self.assertRaisesRegex(ValueError, '完全相同'):
            TD.testset_save(self.root, old, {'name': '又一条', 'input': {'a': 5, 'b': 5}, 'expect': 10})

    def test_saving_what_the_user_sees_changes_nothing_and_a_user_edit_voids_the_ai_change(self):
        old = self.landed()
        rid, place = self.requirement()
        TE.testset_edit(place, old, 1, {'expect': 4}, why='需求改了')
        shown = TV.testset_show(self.root, old, rid)['cases_list']
        payload = {'cases': [{k: c[k] for k in ('n', 'name', 'cat', 'input', 'expect') if k in c} for c in shown]}
        saved = TD.testset_save(self.root, old, payload, scope=rid)  # 原样保存（JSON 标签里什么都没改）
        self.assertEqual(saved['saved'], {'added': [], 'changed': [], 'removed': [], 'voided': []})
        data = T.read_set(self.root, old)
        self.assertEqual(([c['expect'] for c in data['cases']], [c['state'] for c in data['changes']]), ([3, -3], ['pending']))  # 没同意的改动没被写进去
        self.assertTrue(all(c.get('unseen') and not c.get('edited') for c in data['cases']))
        saved = TD.testset_save(self.root, old, {'name': '正数', 'input': {'a': 1, 'b': 2}, 'expect': 5}, n=1, scope=rid)  # 用户自己改了这一条
        self.assertEqual((saved['saved']['changed'], saved['saved']['voided']), ([1], [1]))
        data = T.read_set(self.root, old)
        self.assertEqual((data['changes'], [c['expect'] for c in T.view(data, rid)]), ([], [5, -3]))

    def test_ai_cannot_touch_another_requirements_sets(self):
        rid, place = self.requirement()
        mine = TE.testset_new(place, '新的', 'RF-101', 'tests/rf_add.py')
        with self.assertRaisesRegex(ValueError, '只有它的会话能改'):
            TE.testset_add(self.root, mine['id'], [{'name': 'x', 'input': 1, 'expect': 1}])


class StaleTests(Base):
    def test_feature_gone_makes_sets_stale(self):
        ident = self.landed()
        (self.root / '.relayforge/history').mkdir(exist_ok=True)
        (self.root / '.relayforge/features/RF-101-加法.md').rename(self.root / '.relayforge/history/RF-101-加法.md')
        row = TV.testset_list(self.root)['sets'][0]
        self.assertTrue(row['stale'])
        self.assertIn('已下线或被替代', row['stale_reason'])
        out = TR.testset_run(self.root, everything=True)
        self.assertEqual((out['sets'], out['skipped'][0]['id']), ([], ident))
        with self.assertRaisesRegex(ValueError, '改挂靠'):
            TD.testset_revive(self.root, ident)
        TD.testset_move(self.root, ident, feature='RF-102')
        self.assertFalse(TV.testset_list(self.root)['sets'][0]['stale'])
        TD.testset_stale(self.root, ident, '不要了')
        self.assertEqual(TV.testset_list(self.root, stale=True)['sets'][0]['stale_reason'], '不要了')
        TD.testset_revive(self.root, ident)
        self.assertEqual(len(TR.testset_run(self.root, everything=True)['sets']), 1)


class StopAndCliTests(Base):
    def transcript(self, place, *blocks):
        path = Path(self.tmp.name) / 't.jsonl'
        rows = [{'type': 'user', 'message': {'content': '写测试'}}, {'type': 'assistant', 'message': {'content': list(blocks)}}]
        path.write_text('\n'.join(json.dumps(r, ensure_ascii=False) for r in rows), encoding='utf-8')
        return {'hook_event_name': 'Stop', 'cwd': str(place), 'transcript_path': str(path), 'session_id': 's'}

    def test_stop_check_wants_test_sets(self):
        rid, place = self.requirement()
        TE.testset_new(place, '新的', 'RF-101', 'tests/rf_entry.py')
        write = lambda rel: {'type': 'tool_use', 'name': 'Write', 'input': {'file_path': str(place / rel)}}  # noqa: E731
        for rel in ('tests/test_calc.py', 'tests/rf_entry.py', 'calc.py'):
            (place / rel).write_text('x = 1\n', encoding='utf-8')
        self.assertIn('没有用测试集', TC.test_stop_block(self.transcript(place, write('tests/test_calc.py'))))
        self.assertIsNone(TC.test_stop_block(self.transcript(place, write('tests/rf_entry.py'), write('calc.py'))))  # 接入代码不算
        used = {'type': 'tool_use', 'name': 'Bash', 'input': {'command': 'python3 .relayforge/bin/rf.py test run'}}
        self.assertIsNone(TC.test_stop_block(self.transcript(place, write('tests/test_calc.py'), used)))
        said = {'type': 'tool_use', 'name': 'Bash', 'input': {'command': 'python3 .relayforge/bin/rf.py note doing "这个测试要测并发，写不成测试集"'}}
        self.assertIsNone(TC.test_stop_block(self.transcript(place, write('tests/test_calc.py'), said)))
        data = self.transcript(place, write('tests/test_calc.py'))
        self.assertIsNone(TC.test_stop_block(dict(data, stop_hook_active=True)))
        TR.testset_bridge(place)
        self.assertIsNone(TC.test_stop_block(self.transcript(place, write('tests/test_relayforge_testsets.py'))))
        (self.root / 'tests' / 'test_calc.py').write_text('x = 1\n', encoding='utf-8')  # 主目录里的日常聊天不拦
        main = {'type': 'tool_use', 'name': 'Write', 'input': {'file_path': str(self.root / 'tests' / 'test_calc.py')}}
        self.assertIsNone(TC.test_stop_block(self.transcript(self.root, main)))

    def test_cli_json_and_history(self):
        code, out = self.cli('new', '加法', '--feature', 'RF-101', '--entry', 'tests/rf_add.py', '--json')
        self.assertEqual((code, json.loads(out)['id']), (0, 'TS-001'))
        code, out = self.cli('add', 'TS-001', '--name', '正数', '--input', '{"a": 1, "b": 2}', '--expect', '3')
        self.assertEqual(code, 0)
        code, out = self.cli('run', 'TS-001', '--json')
        self.assertEqual(json.loads(out)['totals']['pass'], 1)
        code, out = self.cli('list', '--json')
        row = json.loads(out)['sets'][0]
        self.assertEqual((row['cases'], row['last']['result'], row['unseen']), (1, 'pass', 1))
        code, out = self.cli('show', 'TS-001', '--json')
        shown = json.loads(out)
        self.assertEqual((shown['cases_list'][0]['result']['actual'], shown['entry_code']), (3, ENTRY))
        self.assertEqual(self.cli('seen', 'TS-001')[0], 1)  # 标为看过了只由你在应用里点
        self.assertEqual(TV.testset_list(self.root)['sets'][0]['unseen'], 1)
        with app_marker():
            self.cli('seen', 'TS-001')
        self.assertEqual(TV.testset_list(self.root)['sets'][0]['unseen'], 0)
        code, _ = self.cli('add', 'TS-001', '--name', 'x', '--input', 'not json', '--expect', '1')
        self.assertEqual(code, 1)
        found = rf.history_search(self.root, ['加法'], kinds=['test'])
        self.assertEqual(found['items'][0]['id'], 'TS-001')
        self.assertEqual(rf.history_search(self.root, [], features=['RF-101'], kinds=['test'])['total'], 1)
        self.assertIn('正数', rf.show_text(rf.history_show(self.root, 'TS-001')))


if __name__ == '__main__':
    unittest.main()


class FullRunTests(Base):
    """主线上的全量（test run --all）记一份摘要：合并前的检查只跑一部分，剩下的靠它；测试页和发布前都看得到它新不新。"""
    def test_a_full_run_is_recorded_and_goes_stale_when_the_main_line_moves(self):
        from relayforge.testsets.store import full_status
        from relayforge.project.publish import publish_status
        self.landed()
        warn = lambda: [w['text'] for w in publish_status(self.root)['warnings'] if w['code'] == 'full-tests']
        self.assertIsNone(full_status(self.root)['last'])
        self.assertIn('主线上还没跑过全量测试集', warn()[0])
        TR.testset_run(self.root, everything=True)
        st = full_status(self.root)
        self.assertEqual((st['stale'], st['behind'], st['last']['failing']), (False, 0, []))
        self.assertEqual(warn(), [])
        self.assertIn('full', TV.testset_list(self.root))
        TR.testset_run(self.root, ['TS-001'])  # 只跑一部分不算全量
        (self.root / 'calc.py').write_text('def add(a, b):\n    return a - b\n', encoding='utf-8')
        git(self.root, 'commit', '-qam', 'break it')
        st = full_status(self.root)
        self.assertEqual((st['stale'], st['behind']), (True, 1))
        self.assertIn('之后又合进来 1 次', warn()[0])
        TR.testset_run(self.root, everything=True)
        self.assertEqual(full_status(self.root)['last']['failing'], ['TS-001'])
        self.assertIn('全量测试集没过的：TS-001', warn()[0])

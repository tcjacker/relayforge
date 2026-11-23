"""问题模块：记、初判、改设置、修复和合并、转为需求、停下来时的检查。"""
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
from tests.app_fixture import as_app, in_repo_layout  # noqa: E402

STUB = '''import json, os, sys
sys.stdin.read()
path = os.environ["RF_TEST_ITEMS"]
if not os.path.exists(path):
    sys.exit(1)
print("好的：")
print(open(path, encoding="utf-8").read())
'''


def git(place, *args):
    return subprocess.run(['git', '-c', 'core.quotepath=off', *args], cwd=place, check=True, capture_output=True, text=True).stdout


class IssueBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve() / 'project'
        self.root.mkdir()
        stub = Path(self.tmp.name) / 'stub.py'
        stub.write_text(STUB, encoding='utf-8')
        self.answer = Path(self.tmp.name) / 'answer.json'
        env = mock.patch.dict(os.environ, {
            'RELAYFORGE_HOME': str(Path(self.tmp.name) / 'home'),
            'RELAYFORGE_EXTRACTOR': json.dumps([sys.executable, str(stub)]), 'RF_TEST_ITEMS': str(self.answer),
            'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@example.com', 'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@example.com',
        })
        env.start()
        self.addCleanup(env.stop)
        for name in (rf.GUARD, 'CLAUDE_PROJECT_DIR', 'CLAUDE_CODE_SESSION_ID', 'RELAYFORGE_SESSION_ID'):
            os.environ.pop(name, None)
        rf.init(self.root, hooks=False)
        base = self.root / rf.DIR
        rf.write_doc(base / 'modules' / 'M-01-文档.md', {'id': 'M-01', 'type': 'module', 'title': '文档', 'status': 'current'}, {'负责什么': [], '变更记录': []})
        for ident, title in (('RF-101', '文档登记'), ('RF-102', '文档列表')):
            rf.write_doc(base / 'features' / f'{ident}-{title}.md', {'id': ident, 'type': 'feature', 'title': title, 'status': 'current', 'module': 'M-01', 'depends_on': []},
                         dict({h: [] for h in rf.FEATURE_HEADS}, **{'一句话定位': [f'- {title}的说明〔S-x#1〕']}))
        rf.write_doc(base / 'journeys' / 'J-01-登记文档.md', {'id': 'J-01', 'type': 'journey', 'title': '登记文档', 'status': 'current', 'module': 'M-01', 'order': 1},
                     {'一句话说明': [], '步骤': ['- 打开文档登记｜看到列表｜用户｜RF-101、RF-102〔S-x#1〕'], '变更记录': []})
        git(self.root, 'init', '-q', '-b', 'main')
        git(self.root, 'add', '-A')
        git(self.root, 'commit', '-qm', 'init')

    def judge(self, **answer):
        self.answer.write_text(json.dumps(answer, ensure_ascii=False), encoding='utf-8')

    def log(self):
        return git(self.root, 'log', '--format=%s').splitlines()

    def commit_in(self, place, name, text, message='work'):
        (Path(place) / name).parent.mkdir(parents=True, exist_ok=True)
        (Path(place) / name).write_text(text, encoding='utf-8')
        git(place, 'add', '-A')
        git(place, 'commit', '-qm', message)


class RecordTests(IssueBase):
    @in_repo_layout
    def test_issues_get_sequential_numbers_and_are_committed_alone(self):
        (self.root / 'staged.txt').write_text('mine\n', encoding='utf-8')
        git(self.root, 'add', 'staged.txt')
        (self.root / 'AGENTS.md').write_text('user is editing\n', encoding='utf-8')
        first = rf.issue_new(self.root, ' 归档文档 仍出现在现行列表 ', '检查列表时看到\n同一份出现两次', '你')
        second = rf.issue_new(self.root, '按钮没有反应', source='Agent')
        self.assertEqual((first['id'], first['num'], second['id']), ('I-001', '#001', 'I-002'))
        self.assertEqual(first['title'], '归档文档 仍出现在现行列表')
        self.assertEqual(first['description'], ['检查列表时看到', '同一份出现两次'])
        self.assertEqual((first['state'], first['kind'], first['needs_triage']), ('todo', None, True))
        self.assertEqual(self.log(), ['init'])  # recording is not one of the moments the program commits
        self.assertIn('?? .relayforge/issues/', git(self.root, 'status', '--porcelain'))
        rf.issue_set(self.root, '#2', 'defer')  # a decision of the user's is
        self.assertEqual(self.log()[0], '问题 #002：以后再说')
        status = git(self.root, 'status', '--porcelain')
        self.assertIn('A  staged.txt', status)  # the user's staged file is still only staged
        self.assertIn(' M AGENTS.md', status)
        self.assertNotIn('.relayforge/issues', status)
        changed = git(self.root, 'show', '--name-only', '--format=', 'HEAD').split()
        self.assertTrue(changed and all(n.startswith('.relayforge/') for n in changed))
        meta, sections = rf.read_doc(self.root / first['file'])
        self.assertEqual((meta['type'], meta['source'], meta['state']), ('issue', '你', 'todo'))
        self.assertEqual(list(sections), rf.ISSUE_HEADS)
        self.assertIn('- 检查列表时看到〔I-001#1〕', sections['现象'])
        thread = rf.read_thread(self.root, 'I-001')
        self.assertEqual((thread[0]['t'], thread[0]['who'], thread[0]['n']), ('report', '你', 1))

    @in_repo_layout
    def test_an_issue_recorded_in_a_worktree_lands_in_the_main_checkout(self):
        made = rf.req_new(self.root, '导出')
        place = Path(made['worktree'])
        out = rf.issue_new(place, '列表卡住', source='Agent', feature='RF-101')
        self.assertTrue((self.root / out['file']).exists())
        self.assertFalse((place / out['file']).exists())
        self.assertEqual(rf.branch_of(self.root), 'main')
        self.assertEqual(git(place, 'status', '--porcelain'), '')
        # 改问题也一样：从需求的工作目录里改，改的是主目录；需求的分支上没有任何问题数据
        rf.issue_reply(place, '#1', '在需求的工作目录里补一句')
        rf.issue_set(place, '#1', 'defer')
        self.assertEqual([i['id'] for i in rf.issue_list(place)['issues']], ['I-001'])
        self.assertTrue(rf.issue_show(self.root, '#1')['deferred'])
        self.assertNotIn('.relayforge/issues/I-001', git(place, 'ls-tree', '-r', '--name-only', made['branch']))
        self.assertIn('.relayforge/issues/I-001.jsonl', git(self.root, 'ls-tree', '-r', '--name-only', 'main'))
        self.assertEqual(git(place, 'status', '--porcelain'), '')

    @in_repo_layout
    def test_issues_follow_whatever_branch_the_main_checkout_has_out(self):
        made = rf.req_new(self.root, '导出')
        git(self.root, 'checkout', '-q', '-b', 'dev')
        rf.issue_new(Path(made['worktree']), '在 dev 上记的', source='Agent')
        rf.issue_set(Path(made['worktree']), '#1', 'defer')
        self.assertIn('.relayforge/issues/I-001.jsonl', git(self.root, 'ls-tree', '-r', '--name-only', 'dev'))
        self.assertNotIn('.relayforge/issues/I-001.jsonl', git(self.root, 'ls-tree', '-r', '--name-only', 'main'))
        with self.assertRaisesRegex(ValueError, 'RF-999 在事实里不存在'):
            rf.issue_new(self.root, 'x', feature='RF-999')
        with self.assertRaisesRegex(ValueError, '标题'):
            rf.issue_new(self.root, '  ')
    @in_repo_layout
    def test_screenshots_stay_out_of_git(self):
        self.assertIn('.relayforge/issues/shots/', (self.root / '.gitignore').read_text(encoding='utf-8'))
        shot = Path(self.tmp.name) / 'a.png'
        shot.write_bytes(b'\x89PNG')
        made = rf.issue_new(self.root, '截断', images=[str(shot)])
        path = Path(made['thread'][0]['shots'][0])
        self.assertTrue(path.is_file())
        self.assertTrue(str(path).startswith(str(self.root / '.relayforge' / 'issues' / 'shots' / 'I-001')))
        self.assertTrue(rf.commit_facts(self.root, '记录'))
        self.assertNotIn('shots', git(self.root, 'ls-files'))
        self.assertEqual(git(self.root, 'status', '--porcelain'), '')
        with self.assertRaisesRegex(ValueError, '不是能用的图片'):
            rf.issue_reply(self.root, 'I-001', '再看', [str(Path(self.tmp.name) / 'missing.png')])

    def test_a_project_initialised_before_issues_gets_the_ignore_rule_locally(self):
        ignore = self.root / '.gitignore'
        ignore.write_text(ignore.read_text(encoding='utf-8').replace('.relayforge/issues/shots/\n', ''), encoding='utf-8')
        git(self.root, 'commit', '-qam', 'old project')
        shot = Path(self.tmp.name) / 'a.png'
        shot.write_bytes(b'\x89PNG')
        rf.issue_new(self.root, '截断', images=[str(shot)])
        self.assertIn('.relayforge/issues/shots/', (self.root / '.git' / 'info' / 'exclude').read_text(encoding='utf-8'))
        rf.commit_facts(self.root, '记录')
        self.assertEqual(git(self.root, 'status', '--porcelain'), '')

    def test_the_command_line_records_and_lists(self):
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(rf.main(['issue', 'new', '页面', '标题重复', '--desc', '两个空格', '--source', 'Agent', '--root', str(self.root), '--json']), 0)
            self.assertEqual(rf.main(['issue', 'list', '--root', str(self.root), '--json']), 0)
        made, listed = (json.loads(x) for x in out.getvalue().strip().splitlines())
        self.assertEqual((made['title'], made['source']), ('页面 标题重复', 'Agent'))
        self.assertEqual([i['id'] for i in listed['issues']], ['I-001'])
        self.assertEqual({f['id'] for f in listed['features']}, {'RF-101', 'RF-102'})
        with redirect_stdout(io.StringIO()), mock.patch('sys.stderr', io.StringIO()) as err:
            self.assertEqual(as_app(['issue', 'confirm', '#1', '--root', str(self.root)]), 1)
        self.assertIn('不能确认修好了', err.getvalue())


class TriageTests(IssueBase):
    def test_a_valid_judgement_is_written_and_a_reply_asks_for_another(self):
        rf.issue_new(self.root, '列表里看不到已解决的', '上周那条找不到了', '你', feature='RF-102')
        self.judge(kind='usage', reason='已解决的在另一个页签里。', howto='切到「已解决」页签就能看到。')
        made = rf.issue_triage(self.root, '#1')
        judge = made['thread'][-1]
        self.assertEqual((made['kind'], made['kind_by'], judge['t'], judge['howto'], judge['upto']), ('usage', 'ai', 'judge', '切到「已解决」页签就能看到。', 1))
        self.assertFalse(made['needs_triage'])
        payload = rf.triage_payload(self.root, *rf.find_issue(self.root, 'I-001')[1:], rf.read_thread(self.root, 'I-001'))
        self.assertIn('RF-102 文档列表', payload)
        self.assertIn('用户旅程 J-01 登记文档：第 1 步', payload)
        self.assertIn('上周那条找不到了', payload)
        self.assertIn('现行事实摘要', payload)
        replied = rf.issue_reply(self.root, 'I-001', '还是不行，页签里也没有')
        self.assertTrue(replied['needs_triage'])
        self.judge(kind='是问题', reason='看起来已解决的没有列出来，还需要复现确认。')
        again = rf.issue_triage(self.root, 'I-001')
        self.assertEqual((again['kind'], again['needs_triage']), ('bug', False))

    def test_an_answer_that_does_not_fit_writes_nothing_but_the_failure(self):
        rf.issue_new(self.root, '点了按钮没有反应')
        for bad in ({'kind': 'maybe', 'reason': 'x'}, {'kind': 'ask', 'reason': '不清楚'}, {'kind': 'usage', 'reason': '有'}, {'kind': 'bug', 'reason': ''}):
            self.judge(**bad)
            with self.assertRaisesRegex(ValueError, '没判出来'):
                rf.issue_triage(self.root, 'I-001')
        self.answer.unlink()
        with self.assertRaisesRegex(ValueError, '没判出来'):
            rf.issue_triage(self.root, 'I-001')  # the model could not be run at all
        made = rf.issue_show(self.root, 'I-001')
        self.assertEqual((made['kind'], made['needs_triage'], made['triage_failed']), (None, False, True))
        self.assertEqual([e['t'] for e in made['thread']], ['report'] + ['fail'] * 5)
        self.judge(kind='ask', reason='信息不够。', question='你点的是哪个按钮？')
        made = rf.issue_triage(self.root, 'I-001')  # 重试
        self.assertEqual((made['kind'], made['triage_failed'], made['thread'][-1]['question']), ('ask', False, '你点的是哪个按钮？'))

    def test_only_an_open_issue_is_judged(self):
        rf.issue_new(self.root, '想换主题色')
        rf.issue_set(self.root, 'I-001', 'dismiss', '暂时只有一套主题')
        self.judge(kind='request', reason='想多一套主题。')
        with self.assertRaisesRegex(ValueError, '不能初判'):
            rf.issue_triage(self.root, 'I-001')


class SettingTests(IssueBase):
    def setUp(self):
        super().setUp()
        rf.issue_new(self.root, '文档名称遮住了状态')
        self.judge(kind='bug', reason='看起来布局有问题，还需要复现。')
        rf.issue_triage(self.root, 'I-001')

    def test_the_user_decides(self):
        made = rf.issue_set(self.root, '#1', 'feature', 'RF-101')
        self.assertEqual((made['feature'], made['feature_title']), ('RF-101', '文档登记'))
        self.assertEqual(rf.issue_set(self.root, '#1', 'feature', 'none')['feature'], None)
        with self.assertRaisesRegex(ValueError, '不存在'):
            rf.issue_set(self.root, '#1', 'feature', 'RF-404')
        self.assertTrue(rf.issue_set(self.root, '#1', 'defer')['deferred'])
        self.assertFalse(rf.issue_reply(self.root, '#1', '其实挺烦的')['deferred'])  # a reply takes it back
        rf.issue_set(self.root, '#1', 'defer')
        self.assertFalse(rf.issue_set(self.root, '#1', 'undefer')['deferred'])
        made = rf.issue_set(self.root, '#1', 'kind', 'request')
        self.assertEqual((made['kind'], made['kind_by']), ('request', 'user'))
        self.assertEqual(made['thread'][-1]['text'], '你改了类别：可转需求')
        with self.assertRaisesRegex(ValueError, '只能手动改成'):
            rf.issue_set(self.root, '#1', 'kind', 'usage')
        with self.assertRaisesRegex(ValueError, '已有用法'):
            rf.issue_set(self.root, '#1', 'solved')
        made = rf.issue_set(self.root, '#1', 'dismiss', '只在示例项目里出现')
        self.assertEqual((made['state'], made['closed_as'], made['thread'][-1]['close']), ('closed', 'drop', True))
        made = rf.issue_set(self.root, '#1', 'reopen')
        self.assertEqual((made['state'], made['closed_as']), ('todo', None))
        meta, sections = rf.read_doc(self.root / made['file'])
        self.assertIn('不处理 · 只在示例项目里出现', ' '.join(sections['变更记录']))

    def test_a_dismissal_with_a_number_marks_a_duplicate(self):
        rf.issue_new(self.root, '同一份文档出现两次')
        with self.assertRaisesRegex(ValueError, '自己'):
            rf.issue_set(self.root, '#2', 'dismiss', '#2')
        with self.assertRaisesRegex(ValueError, '没有找到问题 #018'):
            rf.issue_set(self.root, '#2', 'dismiss', '#18')
        made = rf.issue_set(self.root, '#2', 'dismiss', '#1')
        self.assertEqual((made['state'], made['closed_as'], made['duplicate_of']), ('closed', 'duplicate', 'I-001'))
        self.assertEqual(made['thread'][-1]['text'], '标记为重复 · #001')

    def test_an_existing_usage_can_be_marked_solved(self):
        rf.issue_new(self.root, '怎么改状态')
        self.judge(kind='usage', reason='能改。', howto='右侧「状态」一栏。')
        rf.issue_triage(self.root, '#2')
        made = rf.issue_set(self.root, '#2', 'solved')
        self.assertEqual(made['state'], 'resolved')


class FixTests(IssueBase):
    def setUp(self):
        super().setUp()
        self.commit_in(self.root, 'app.py', 'print("old")\n', 'app')
        rf.issue_new(self.root, '启动时打印旧的', feature='RF-101')
        self.judge(kind='bug', reason='像是打印的内容没跟着改，还需要复现。')
        rf.issue_triage(self.root, 'I-001')

    def start(self):
        return rf.issue_fix(self.root, ['#1'])

    def test_a_fix_gets_its_own_branch_and_merges_when_the_user_confirms(self):
        fix = self.start()
        place = Path(fix['worktree'])
        self.assertEqual((fix['id'], fix['branch'], fix['issues']), ('F-001', 'fix/f-001', ['I-001']))
        self.assertEqual(place, self.root.resolve() / rf.TREES / 'F-001')
        self.assertEqual(rf.branch_of(place), 'fix/f-001')
        made = rf.issue_show(self.root, '#1')
        self.assertEqual((made['state'], made['fix']['id']), ('fixing', 'F-001'))
        self.assertEqual(made['thread'][-1]['text'], '你选择开始修复 · 修复会话 F-001')
        self.assertEqual(rf.issue_attach(self.root, 'F-001', 'abcd-1')['sessions'], ['abcd-1'])
        brief = rf.issue_brief(place)
        self.assertIn('启动时打印旧的', brief)
        self.assertIn('RF-101 文档登记', brief)
        self.assertIn('用户旅程 J-01 登记文档：第 1 步 打开文档登记', brief)
        (place / 'app.py').write_text('print("new")\n', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '没有提交'):
            rf.issue_handback(place, None, '改成打印新的', '运行 app.py', no_regression='这条测试只看交回流程，不看回归数据')
        git(place, 'commit', '-qam', 'fix print')
        with self.assertRaisesRegex(ValueError, '两项'):
            rf.issue_handback(place, None, '改了', '', no_regression='这条测试只看交回流程，不看回归数据')
        made = rf.issue_handback(place, None, '改成打印新的', '运行 python3 app.py，看到 new', no_regression='这条测试只看交回流程，不看回归数据')
        self.assertEqual((made['state'], made['report']['changed'], made['report']['verify']), ('review', ['改成打印新的'], ['运行 python3 app.py，看到 new']))
        made = rf.issue_reject(self.root, '#1', '还是打印 old')
        self.assertEqual((made['state'], made['thread'][-1]['text']), ('fixing', '还有问题 · 回到修复会话 F-001'))
        made = rf.issue_handback(place, '#1', '确认改的是入口', '再跑一次', no_regression='这条测试只看交回流程，不看回归数据')
        self.assertEqual(made['report']['changed'], ['确认改的是入口'])
        meta, sections = rf.read_doc(self.root / made['file'])
        self.assertIn(rf.REPLACED, sections['修复报告'][0])
        self.assertEqual(rf.branch_of(self.root), 'main')
        done = rf.issue_confirm(self.root, '#1')
        self.assertEqual(done['state'], 'resolved')
        self.assertIn('已合并进 main', done['thread'][-1]['text'])
        self.assertEqual((self.root / 'app.py').read_text(encoding='utf-8'), 'print("new")\n')
        self.assertFalse(place.exists())
        self.assertEqual(git(self.root, 'branch', '--list', 'fix/f-001'), '')
        self.assertEqual(git(self.root, 'status', '--porcelain'), '')
        self.assertIn('合并修复：问题 #001（F-001）', git(self.root, 'log', '--format=%s'))
        self.assertEqual(rf.read_fix(self.root, 'F-001')['state'], 'merged')

    def test_only_issues_judged_as_something_to_fix_start_and_the_kind_follows(self):
        rf.issue_new(self.root, '想要导出 PDF')
        with self.assertRaisesRegex(ValueError, '还没有判成'):
            rf.issue_fix(self.root, ['#2'])
        self.judge(kind='request', reason='想多做一件事。')
        rf.issue_triage(self.root, '#2')
        fix = rf.issue_fix(self.root, ['#2'])
        made = rf.issue_show(self.root, '#2')
        self.assertEqual((made['kind'], made['kind_by']), ('bug', 'user'))
        self.assertIn('你选择按问题直接修', made['thread'][-1]['text'])
        self.assertEqual(fix['id'], 'F-001')

    def test_a_conflict_goes_back_to_the_fix_and_the_confirmation_is_kept(self):
        fix = self.start()
        place = Path(fix['worktree'])
        self.commit_in(place, 'app.py', 'print("theirs")\n')
        rf.issue_handback(place, None, '改了打印', '运行看看', no_regression='这条测试只看交回流程，不看回归数据')
        self.commit_in(self.root, 'app.py', 'print("mine")\n')
        with self.assertRaises(rf.MergeConflict) as caught:
            rf.issue_confirm(self.root, '#1')
        self.assertIn('修复会话里', str(caught.exception))
        self.assertEqual(caught.exception.files, ['app.py'])
        made = rf.issue_show(self.root, '#1')
        self.assertEqual((made['state'], made['confirmed']), ('review', True))
        with redirect_stdout(io.StringIO()) as out, mock.patch('sys.stderr', io.StringIO()):
            self.assertEqual(as_app(['issue', 'confirm', '#1', '--root', str(self.root), '--json']), 3)
        self.assertEqual(json.loads(out.getvalue())['conflict'], True)
        subprocess.run(['git', 'merge', '-q', 'main'], cwd=place, capture_output=True)
        with self.assertRaises(rf.NeedsSync) as in_progress:
            rf.issue_confirm(self.root, '#1')
        self.assertTrue(in_progress.exception.merging)
        self.assertEqual(in_progress.exception.files, ['app.py'])
        (place / 'app.py').write_text('print("both")\n', encoding='utf-8')
        git(place, 'add', 'app.py')
        with redirect_stdout(io.StringIO()) as out, mock.patch('sys.stderr', io.StringIO()):
            self.assertEqual(as_app(['issue', 'confirm', '#1', '--root', str(self.root), '--json']), 3)
        told = json.loads(out.getvalue())
        self.assertEqual((told['merging'], told['files'], told['loose']), (True, [], []))
        self.assertIn('合并还没有提交', told['error'])
        git(place, 'commit', '-qm', 'resolve')
        self.assertEqual(rf.issue_confirm(self.root, '#1')['state'], 'resolved')
        self.assertEqual((self.root / 'app.py').read_text(encoding='utf-8'), 'print("both")\n')

    def test_like_a_requirement_a_fix_lands_only_once_it_has_the_latest_target_and_its_work_committed(self):
        fix = self.start()
        place = Path(fix['worktree'])
        self.commit_in(place, 'app.py', 'print("new")\n')
        rf.issue_handback(place, None, '改了打印', '运行看看', no_regression='这条测试只看交回流程，不看回归数据')
        self.commit_in(self.root, 'other.py', 'x\n')  # the target moved on, no conflict
        (place / 'more.py').write_text('y\n', encoding='utf-8')  # and something is not committed yet
        with redirect_stdout(io.StringIO()) as out, mock.patch('sys.stderr', io.StringIO()):
            self.assertEqual(as_app(['issue', 'confirm', '#1', '--root', str(self.root), '--json']), 3)
        told = json.loads(out.getvalue())
        self.assertEqual((told['sync'], told['conflict'], told['files'], told['loose']), (True, False, [], ['more.py']))
        self.assertEqual(rf.issue_show(self.root, '#1')['state'], 'review')
        self.assertEqual((self.root / 'app.py').read_text(encoding='utf-8'), 'print("old")\n')
        git(place, 'add', '-A'); git(place, 'commit', '-qm', 'more')
        # 目标分支合进来没有代码冲突：程序自己在修复的工作目录里合，不再回到修复会话
        self.assertEqual(rf.issue_confirm(self.root, '#1')['state'], 'resolved')
        self.assertTrue((self.root / 'other.py').exists())
        self.assertTrue((self.root / 'more.py').exists())

    def test_a_fix_that_changed_nothing_is_resolved_without_a_merge(self):
        fix = self.start()
        rf.issue_handback(Path(fix['worktree']), None, '复现不了，没有改', '按描述操作了一遍', no_regression='这条测试只看交回流程，不看回归数据')
        made = rf.issue_confirm(self.root, '#1')
        self.assertEqual(made['state'], 'resolved')
        self.assertIn('不用合并', made['thread'][-1]['text'])
        self.assertFalse(Path(fix['worktree']).exists())
        self.assertEqual(git(self.root, 'branch', '--list', 'fix/*'), '')

    def test_a_merge_waits_while_another_one_holds_the_lock(self):
        fix = self.start()
        self.commit_in(fix['worktree'], 'app.py', 'print("new")\n')
        rf.issue_handback(Path(fix['worktree']), None, '改了', '跑一下', no_regression='这条测试只看交回流程，不看回归数据')
        with rf.take_merge_lock(self.root, 'R-other'):
            with self.assertRaisesRegex(rf.MergeBusy, 'R-other 正在合并'):
                rf.issue_confirm(self.root, '#1')
        self.assertEqual(rf.issue_confirm(self.root, '#1')['state'], 'resolved')

    def transcript(self, *tools):
        path = Path(self.tmp.name) / f'fix-{len(tools)}-{tools[0][0]}.jsonl'
        lines = [json.dumps({'type': 'user', 'message': {'role': 'user', 'content': '修吧'}}, ensure_ascii=False)]
        lines += [json.dumps({'type': 'assistant', 'message': {'role': 'assistant', 'content': [{'type': 'tool_use', 'name': name, 'input': data}]}}, ensure_ascii=False)
                  for name, data in tools]
        path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        return path

    def test_a_fixing_session_that_changed_code_cannot_stop_without_handing_back(self):
        fix = self.start()
        place = Path(fix['worktree'])
        data = {'cwd': str(place), 'transcript_path': str(self.transcript(('Edit', {'file_path': str(place / 'app.py')}))), 'session_id': 'abcd-1'}
        reason = rf.fix_stop_block(data)
        self.assertIn('还没有交回修复报告', reason)
        self.assertIn('issue handback I-001', reason)
        self.assertIsNone(rf.fix_stop_block(dict(data, stop_hook_active=True)))
        self.assertIsNone(rf.fix_stop_block(dict(data, transcript_path=str(self.transcript(('Read', {'file_path': 'app.py'})))))) # only looked
        self.assertIsNone(rf.fix_stop_block(dict(data, cwd=str(self.root))))  # not a fix session
        rf.issue_handback(place, None, '没改', '看了一遍', no_regression='这条测试只看交回流程，不看回归数据')
        self.assertIsNone(rf.fix_stop_block(data))
        # the hook says both things at once when the note is missing too
        from relayforge.project import status
        with redirect_stdout(io.StringIO()) as out:
            rf.issue_reject(self.root, '#1', '还在')
            status.hook_event(dict(data, hook_event_name='Stop'))
        told = json.loads(out.getvalue())
        self.assertEqual(told['decision'], 'block')
        self.assertIn('进展还没记', told['reason'])
        self.assertIn('还没有交回修复报告', told['reason'])


class RegressionTests(IssueBase):
    """修好的问题要留下回归数据（issues/probes.py 的 regression_check）：交回时这次修复加的测试数据里至少一条跑过并通过，
    实在写不成就写清理由；以后再坏，合并闸门会拦住。"""
    def setUp(self):
        super().setUp()
        self.commit_in(self.root, 'greet.py', 'def word():\n    return "old"\n', 'greet')
        rf.issue_new(self.root, '问候语还是旧的', feature='RF-101')
        self.judge(kind='bug', reason='问候语没跟着改。')
        rf.issue_triage(self.root, 'I-001')
        fix = rf.issue_fix(self.root, ['#1'])
        self.place = Path(fix['worktree'])
        self.commit_in(self.place, 'greet.py', 'def word():\n    return "new"\n', 'fix')

    def test_hand_back_needs_a_regression_case_that_ran_and_passed(self):
        from relayforge.testsets import edits as TE, runs as TR
        with self.assertRaisesRegex(ValueError, '要留下回归数据'):
            rf.issue_handback(self.place, None, '改成新的问候语', '运行看到 new')
        self.commit_in(self.place, 'tests/rf_greet.py', 'from greet import word\ndef run(input):\n    return word()\n', 'entry')
        ts = TE.testset_new(self.place, '问候语', 'RF-101', 'tests/rf_greet.py')
        TE.testset_add(self.place, ts['id'], [{'name': '打招呼用新的', 'input': {}, 'expect': 'new'}])
        with self.assertRaisesRegex(ValueError, rf'还没有一条跑过并通过：{ts["id"]} #1（还没跑）'):
            rf.issue_handback(self.place, None, '改成新的问候语', '运行看到 new')
        self.assertEqual(TR.testset_run(self.place)['totals']['pass'], 1)
        made = rf.issue_handback(self.place, None, '改成新的问候语', '运行看到 new')
        self.assertEqual(made['state'], 'review')
        text = next((self.root / '.relayforge' / 'issues').glob('I-001*.md')).read_text(encoding='utf-8')
        self.assertIn(f'回归数据：{ts["id"]} 里 1 条（这次修复加的，已经跑过并通过）。', text)

    def test_when_it_cannot_be_a_test_set_the_reason_is_recorded(self):
        made = rf.issue_handback(self.place, None, '改成新的问候语', '打开页面看了', no_regression='只能看界面上的文字，没有可调用的函数')
        self.assertEqual(made['state'], 'review')
        text = next((self.root / '.relayforge' / 'issues').glob('I-001*.md')).read_text(encoding='utf-8')
        self.assertIn('没有留回归数据：只能看界面上的文字，没有可调用的函数', text)


class BatchFixTests(IssueBase):
    """一起修：几条问题一个修复（一条记录、一个分支、一个工作目录、一个会话），各自交回、各自确认，全部确认以后才合并。"""

    def setUp(self):
        super().setUp()
        self.commit_in(self.root, 'app.py', 'print("old")\n', 'app')
        for title in ('启动时打印旧的', '退出时没有保存', '列表排序不对'):
            made = rf.issue_new(self.root, title, feature='RF-101')
            self.judge(kind='bug', reason='像是没跟着改，还需要复现。')
            rf.issue_triage(self.root, made['id'])

    def start(self, *idents):
        fix = rf.issue_fix(self.root, list(idents or ['#1', '#2', '#3']))
        return fix, Path(fix['worktree'])

    def hand_all(self, place, *idents):
        for ident in idents or ['#1', '#2', '#3']:
            rf.issue_handback(place, ident, f'改了{ident}', f'验证{ident}', no_regression='这条测试只看交回流程，不看回归数据')
    def states(self):
        return {i['num']: i['state'] for i in rf.issue_list(self.root)['issues']}

    def test_one_record_one_branch_one_worktree_for_all_of_them(self):
        fix, place = self.start()
        self.assertEqual((fix['id'], fix['issues'], fix['branch']), ('F-001', ['I-001', 'I-002', 'I-003'], 'fix/f-001'))
        self.assertEqual(sorted(p.name for p in (self.root / rf.TREES).iterdir()), ['F-001'])
        self.assertEqual(git(self.root, 'branch', '--list', 'fix/*').replace('+', '').split(), ['fix/f-001'])
        self.assertEqual(self.states(), {'#001': 'fixing', '#002': 'fixing', '#003': 'fixing'})
        two = rf.issue_show(self.root, '#2')
        self.assertEqual(two['thread'][-1]['text'], '和 #001、#003 一起修 · 修复会话 F-001')
        self.assertEqual((two['batch'], two['fix']['id']), (3, 'F-001'))
        self.assertEqual([(x['num'], x['state']) for x in two['siblings']], [('#001', 'fixing'), ('#003', 'fixing')])
        self.assertFalse(two['confirmed_waiting'])
        listed = {i['num']: i for i in rf.issue_list(self.root)['issues']}
        self.assertEqual([x['num'] for x in listed['#003']['siblings']], ['#001', '#002'])
        self.assertEqual(rf.issue_list(self.root)['issues'][0]['fix']['issues'], ['I-001', 'I-002', 'I-003'])
        brief = rf.issue_brief(place)
        for title in ('启动时打印旧的', '退出时没有保存', '列表排序不对'):
            self.assertIn(title, brief)
        self.assertEqual(git(self.root, 'status', '--porcelain'), '')
        # a single issue still has no siblings and the old wording
        solo = rf.issue_new(self.root, '单独的')
        self.assertEqual((rf.issue_show(self.root, solo['id'])['batch'], rf.issue_show(self.root, solo['id'])['siblings']), (1, []))

    def test_mixed_eligibility_is_refused_naming_each_one_and_nothing_starts(self):
        rf.issue_new(self.root, '还没判过')  # #4: no kind
        asking = rf.issue_new(self.root, '信息不够')  # #5
        self.judge(kind='ask', reason='不清楚。', question='在哪个页面？')
        rf.issue_triage(self.root, asking['id'])
        rf.issue_set(self.root, '#3', 'defer')
        replied = rf.issue_reply(self.root, '#2', '补充一句，换了电脑也一样')  # a reply the judgement does not cover yet: still being analysed
        self.assertTrue(replied['needs_triage'])
        with self.assertRaises(ValueError) as caught:
            rf.issue_fix(self.root, ['#1', '#2', '#3', '#4', '#5'])
        said = str(caught.exception)
        self.assertTrue(said.startswith('这几条不能一起修'))
        for needle in ('#002 还在分析', '#003 放在「以后再说」里', '#004 还没有判成「是问题」', '#005 还没有判成「是问题」'):
            self.assertIn(needle, said)
        self.assertNotIn('#001', said)
        self.assertEqual(list((self.root / rf.DIR / 'issues' / 'fixes').glob('F-*.json')), [])
        self.assertEqual(git(self.root, 'branch', '--list', 'fix/*'), '')
        self.assertEqual(self.states()['#001'], 'todo')
        # one that is already being fixed cannot join another fix
        rf.issue_fix(self.root, ['#1'])
        with self.assertRaisesRegex(ValueError, '问题 #001 现在是处理中，不能开始修复'):
            rf.issue_fix(self.root, ['#1', '#3'])
        with self.assertRaisesRegex(ValueError, '没有找到问题 #099'):
            rf.issue_fix(self.root, ['#2', '#99'])

    def test_a_user_corrected_kind_is_fixable_and_request_kinds_switch_like_before(self):
        wish = rf.issue_new(self.root, '想要导出 PDF')
        self.judge(kind='request', reason='想多做一件事。')
        rf.issue_triage(self.root, wish['id'])
        rf.issue_fix(self.root, ['#1', '#4'])
        four = rf.issue_show(self.root, '#4')
        self.assertEqual((four['kind'], four['kind_by']), ('bug', 'user'))
        self.assertEqual(four['thread'][-1]['text'], '你选择按问题直接修，和 #001 一起修 · 修复会话 F-001')

    def test_confirming_one_does_not_merge_until_the_last_one_is_confirmed(self):
        fix, place = self.start()
        self.commit_in(place, 'app.py', 'print("new")\n')
        self.hand_all(place, '#1', '#2')
        one = rf.issue_confirm(self.root, '#1')
        self.assertEqual((one['state'], one['confirmed'], one['confirmed_waiting']), ('review', True, True))
        self.assertEqual(one['waiting_for'], ['I-002', 'I-003'])  # #003 has not even been handed back
        self.assertIn('等同批的 #002、#003', one['thread'][-1]['text'])
        self.assertEqual((self.root / 'app.py').read_text(encoding='utf-8'), 'print("old")\n')
        self.assertTrue(place.exists())
        self.assertEqual(git(self.root, 'status', '--porcelain'), '')
        again = rf.issue_confirm(self.root, '#1')  # pressing twice records nothing more
        self.assertEqual(len(again['thread']), len(one['thread']))
        rf.issue_handback(place, '#3', '改了#3', '验证#3', no_regression='这条测试只看交回流程，不看回归数据')
        two = rf.issue_confirm(self.root, '#2')
        self.assertEqual((two['state'], two['waiting_for']), ('review', ['I-003']))
        self.assertEqual([(x['num'], x['state'], x['confirmed']) for x in rf.issue_show(self.root, '#3')['siblings']], [('#001', 'review', True), ('#002', 'review', True)])
        self.assertFalse(rf.issue_show(self.root, '#3')['confirmed_waiting'])  # #003 itself is the one still waiting for the user
        self.assertEqual(self.states(), {'#001': 'review', '#002': 'review', '#003': 'review'})
        last = rf.issue_confirm(self.root, '#3')
        self.assertEqual(last['state'], 'resolved')
        self.assertEqual(self.states(), {'#001': 'resolved', '#002': 'resolved', '#003': 'resolved'})
        for num in ('#001', '#002'):
            self.assertIn('已合并进 main', rf.issue_show(self.root, num)['thread'][-1]['text'])
        self.assertEqual((self.root / 'app.py').read_text(encoding='utf-8'), 'print("new")\n')
        self.assertFalse(place.exists())
        self.assertEqual(rf.read_fix(self.root, 'F-001')['state'], 'merged')
        self.assertEqual(sum('合并修复' in l for l in self.log()), 1)
        self.assertIn('合并修复：问题 #001、#002、#003（F-001）', self.log())

    def test_rejecting_one_sends_only_it_back_and_keeps_the_others_confirmations(self):
        fix, place = self.start()
        self.commit_in(place, 'app.py', 'print("new")\n')
        self.hand_all(place)
        rf.issue_confirm(self.root, '#1')
        rf.issue_confirm(self.root, '#2')
        back = rf.issue_reject(self.root, '#3', '排序还是不对')
        self.assertEqual(back['state'], 'fixing')
        self.assertEqual(self.states(), {'#001': 'review', '#002': 'review', '#003': 'fixing'})
        self.assertEqual(rf.read_fix(self.root, 'F-001')['confirmed'], ['I-001', 'I-002'])
        self.assertEqual(rf.issue_show(self.root, '#1')['waiting_for'], ['I-003'])
        # rejecting a confirmed one takes back only its own confirmation
        rf.issue_reject(self.root, '#2', '又发现一处')
        self.assertEqual(rf.read_fix(self.root, 'F-001')['confirmed'], ['I-001'])
        self.assertEqual(self.states(), {'#001': 'review', '#002': 'fixing', '#003': 'fixing'})
        rf.issue_handback(place, '#3', '再改了#3', '再验证', no_regression='这条测试只看交回流程，不看回归数据')
        rf.issue_handback(place, '#2', '再改了#2', '再验证', no_regression='这条测试只看交回流程，不看回归数据')
        self.assertEqual(rf.issue_confirm(self.root, '#3')['state'], 'review')
        self.assertEqual(rf.issue_confirm(self.root, '#2')['state'], 'resolved')
        self.assertEqual(self.states(), {'#001': 'resolved', '#002': 'resolved', '#003': 'resolved'})

    def test_a_confirmation_can_be_taken_back_until_the_fix_is_merged(self):
        fix, place = self.start('#1', '#2')
        self.hand_all(place, '#1', '#2')
        with self.assertRaisesRegex(ValueError, '还没有确认过'):
            rf.issue_unconfirm(self.root, '#1')
        rf.issue_confirm(self.root, '#1')
        back = rf.issue_unconfirm(self.root, '#1')
        self.assertEqual((back['state'], back['confirmed'], back['confirmed_waiting']), ('review', False, False))
        self.assertEqual(back['thread'][-1]['text'], '你撤回了确认 · 回到等你确认')
        self.assertEqual(rf.read_fix(self.root, 'F-001')['confirmed'], [])
        rf.issue_confirm(self.root, '#2')
        self.assertEqual(rf.issue_show(self.root, '#2')['waiting_for'], ['I-001'])
        rf.issue_confirm(self.root, '#1')
        self.assertEqual(self.states()['#001'], 'resolved')
        with self.assertRaisesRegex(ValueError, '现在是已处理，不能撤回确认'):
            rf.issue_unconfirm(self.root, '#1')
        # not for something that is not waiting for confirmation
        fix2, place2 = self.start('#3')
        with self.assertRaisesRegex(ValueError, '现在是处理中'):
            rf.issue_unconfirm(self.root, '#3')

    def test_a_conflict_or_sync_goes_back_to_the_session_for_the_whole_fix_and_every_confirmation_stays(self):
        fix, place = self.start('#1', '#2')
        self.commit_in(place, 'app.py', 'print("theirs")\n')
        self.hand_all(place, '#1', '#2')
        self.commit_in(self.root, 'app.py', 'print("mine")\n')
        rf.issue_confirm(self.root, '#1')
        with self.assertRaises(rf.MergeConflict) as caught:
            rf.issue_confirm(self.root, '#2')
        self.assertEqual(caught.exception.files, ['app.py'])
        self.assertEqual(rf.read_fix(self.root, 'F-001')['confirmed'], ['I-001', 'I-002'])
        self.assertEqual(self.states(), {'#001': 'review', '#002': 'review', '#003': 'todo'})
        self.assertEqual(rf.issue_show(self.root, '#1')['waiting_for'], [])  # nobody is waiting for anyone: only the merge is missing
        with redirect_stdout(io.StringIO()) as out, mock.patch('sys.stderr', io.StringIO()):
            self.assertEqual(as_app(['issue', 'confirm', '#1', '--root', str(self.root), '--json']), 3)  # either one retries the merge
        self.assertTrue(json.loads(out.getvalue())['sync'])
        subprocess.run(['git', 'merge', '-q', 'main'], cwd=place, capture_output=True)
        (place / 'app.py').write_text('print("both")\n', encoding='utf-8')
        git(place, 'commit', '-qam', 'resolve')
        self.assertEqual(rf.issue_confirm(self.root, '#1')['state'], 'resolved')
        self.assertEqual(self.states(), {'#001': 'resolved', '#002': 'resolved', '#003': 'todo'})

    def test_the_stop_check_asks_for_every_issue_still_without_a_report(self):
        fix, place = self.start()
        data = {'cwd': str(place), 'transcript_path': str(FixTests.transcript(self, ('Edit', {'file_path': str(place / 'app.py')}))), 'session_id': 'abcd-1'}
        reason = rf.fix_stop_block(data)
        for num in ('#001', '#002', '#003'):
            self.assertIn(num, reason)
        self.assertIn('每一条各交回一次', reason)
        rf.issue_handback(place, '#2', '改了', '看了', no_regression='这条测试只看交回流程，不看回归数据')
        reason = rf.fix_stop_block(data)
        self.assertIn('#001、#003', reason)
        self.assertNotIn('#002', reason)
        rf.issue_handback(place, '#1', '改了', '看了', no_regression='这条测试只看交回流程，不看回归数据')
        self.assertIn('#003', rf.fix_stop_block(data))
        rf.issue_handback(place, '#3', '改了', '看了', no_regression='这条测试只看交回流程，不看回归数据')
        self.assertIsNone(rf.fix_stop_block(data))
        rf.issue_reject(self.root, '#2', '还在')  # sent back: the session has to report it again before it may stop
        self.assertIn('#002', rf.fix_stop_block(data))
        # which one to hand back must be said when several are open
        rf.issue_reject(self.root, '#1', '还在')
        with self.assertRaisesRegex(ValueError, '写明交回哪一条'):
            rf.issue_handback(place, None, '改了', '看了', no_regression='这条测试只看交回流程，不看回归数据')
    def test_a_batch_whose_branch_has_no_commits_is_resolved_together_without_a_merge(self):
        fix, place = self.start('#1', '#2')
        self.hand_all(place, '#1', '#2')
        self.assertEqual(rf.issue_confirm(self.root, '#1')['state'], 'review')
        done = rf.issue_confirm(self.root, '#2')
        self.assertEqual(done['state'], 'resolved')
        self.assertIn('不用合并', done['thread'][-1]['text'])
        self.assertIn('不用合并', rf.issue_show(self.root, '#1')['thread'][-1]['text'])
        self.assertEqual(self.states(), {'#001': 'resolved', '#002': 'resolved', '#003': 'todo'})
        self.assertFalse(place.exists())
        self.assertEqual(git(self.root, 'branch', '--list', 'fix/*'), '')

    def test_an_issue_in_an_unfinished_fix_cannot_be_dismissed_deferred_or_reopened_meanwhile(self):
        fix, place = self.start('#1', '#2')
        for action, value in (('dismiss', '不用了'), ('defer', ''), ('kind', 'request'), ('solved', ''), ('reopen', '')):
            with self.assertRaises(ValueError) as caught:
                rf.issue_set(self.root, '#1', action, value)
            self.assertIn('在修复 F-001 里（处理中）', str(caught.exception))
            self.assertIn('还有问题', str(caught.exception))
        self.hand_all(place, '#1', '#2')
        with self.assertRaisesRegex(ValueError, '在修复 F-001 里（待你确认）'):
            rf.issue_set(self.root, '#1', 'dismiss', '不用了')
        with self.assertRaisesRegex(ValueError, '不能转为需求|不能转为'):
            rf.issue_to_req(self.root, '#1')
        # linking a feature and replying stay possible, and a reply to a confirmed issue keeps the confirmation
        rf.issue_confirm(self.root, '#1')
        rf.issue_set(self.root, '#1', 'feature', 'RF-102')
        rf.issue_reply(self.root, '#1', '顺便说一句')
        self.assertTrue(rf.issue_show(self.root, '#1')['confirmed_waiting'])

    def test_manual_finish_keeps_batch_members_independent_and_records_both_outcomes(self):
        _, place = self.start()
        with redirect_stdout(io.StringIO()) as out:
            code = as_app(['issue', 'set', '#1', 'finish-resolved', '--reason=--未能复现\n已检查列表', '--json', '--root', str(self.root)])
        self.assertEqual(code, 0)
        done = json.loads(out.getvalue())
        self.assertEqual((done['state'], done['state_name'], done['fix']['state']), ('resolved', '已处理', 'fixing'))
        self.assertEqual(done['thread'][-1]['text'], '你将问题转入「已处理」 · --未能复现\n已检查列表')
        cancelled = rf.issue_set(self.root, '#2', 'finish-closed')
        self.assertEqual((cancelled['state'], cancelled['state_name']), ('closed', '已取消'))
        self.assertEqual(cancelled['thread'][-1]['text'], '你将问题转入「已取消」')
        with self.assertRaisesRegex(ValueError, '不能结束处理'):
            rf.issue_set(self.root, '#2', 'finish-resolved')
        self.assertEqual(rf.issue_show(self.root, '#2')['thread'], cancelled['thread'])
        self.assertEqual(rf.issue_show(self.root, '#3')['siblings'], [])
        rf.issue_handback(place, None, '修了排序', '检查了列表', no_regression='这条测试只看交回流程，不看回归数据')
        with self.assertRaisesRegex(ValueError, '不能结束处理'):
            rf.issue_set(self.root, '#3', 'finish-closed')
        rf.issue_confirm(self.root, '#3')
        self.assertEqual(self.states(), {'#001': 'resolved', '#002': 'closed', '#003': 'resolved'})
        self.assertEqual(rf.issue_show(self.root, '#1')['thread'], done['thread'])

    def test_manual_finish_of_last_issue_keeps_uncommitted_work_on_the_branch_and_puts_the_directory_away(self):
        """修复跟着结束：没提交的改动提交到修复的分支上（供追溯），工作目录收掉，不留在 .worktrees/ 下。"""
        _, place = self.start('#1')
        (place / 'app.py').write_text('print("work in progress")\n')
        out = rf.issue_set(self.root, '#1', 'finish-closed')
        self.assertEqual(out['fix']['state'], 'ended')
        self.assertFalse(place.exists())
        branch = out['fix']['branch']
        kept = subprocess.run(['git', 'show', f'{branch}:app.py'], cwd=self.root, capture_output=True, text=True).stdout
        self.assertEqual(kept, 'print("work in progress")\n')
        self.assertIn(f'1 个没提交的改动提交到了分支 {branch}，工作目录收掉了，分支保留', out['thread'][-1]['text'])
        self.assertEqual((self.root / 'app.py').read_text(), 'print("old")\n')
        self.assertEqual(rf.issue_set(self.root, '#1', 'reopen')['state'], 'todo')
        with self.assertRaisesRegex(ValueError, '不能结束处理'):
            rf.issue_set(self.root, '#1', 'finish-resolved')

    def test_the_command_line_lists_and_takes_back(self):
        fix, place = self.start('#1', '#2')
        self.hand_all(place, '#1', '#2')
        def run(*argv):
            with redirect_stdout(io.StringIO()) as out, mock.patch('sys.stderr', io.StringIO()) as err:
                code = as_app(['issue', *argv, '--root', str(self.root)])  # 这里点的是应用里的按钮（带应用的标记）
            return code, out.getvalue(), err.getvalue()
        self.assertEqual(run('confirm', '#1')[0], 0)
        code, out, _ = run('list', '--json')
        listed = {i['num']: i for i in json.loads(out)['issues']}
        self.assertEqual((listed['#001']['confirmed_waiting'], listed['#001']['waiting_for'], listed['#002']['confirmed_waiting']), (True, ['I-002'], False))
        self.assertEqual(run('unconfirm', '#1')[0], 0)
        self.assertEqual(run('unconfirm', '#1')[0], 1)
        code, out, err = run('fix', '#1', '#3')
        self.assertEqual(code, 1)
        self.assertIn('问题 #001 现在是待你确认', err)
        # handing back with the wording that names who is still out
        three = rf.issue_new(self.root, '第四条')
        self.judge(kind='bug', reason='像是。')
        rf.issue_triage(self.root, three['id'])
        fix2 = rf.issue_fix(self.root, ['#3', '#4'])
        code, out, _ = run('handback', '#3', '--changed', '改了', '--verify', '看了', '--no-regression', '这条测试只看交回流程')
        self.assertIn('同批还有 #004 没交回', out)


class ProbeTests(IssueBase):
    """交给 AI 排查：只读的排查会话，结论用 issue finding 记进对话，问题回到「等你决定」。"""

    def setUp(self):
        super().setUp()
        self.commit_in(self.root, 'app.py', 'print("old")\n', 'app')
        rf.issue_new(self.root, '导出按钮点了没有反应', feature='RF-101')
        self.judge(kind='ask', reason='信息不够。', question='在哪个页面？')
        rf.issue_triage(self.root, 'I-001')

    def finding(self, where=None, **extra):
        args = dict(kind='bug', text='导出按钮的点击处理没有绑定，点了没有任何动作。', steps='打开文档列表，点导出。', where='app.py 的 export()',
                    verified='按步骤在主目录里跑了一遍，确实没有反应。', inferred='多半是改版时漏了绑定，没看提交记录。')
        args.update(extra)
        return rf.issue_finding(where or self.root, '#1', **args)

    @in_repo_layout
    def test_handing_over_records_one_probe_and_attaches_sessions_in_the_main_checkout(self):
        wt = Path(self.tmp.name) / 'wt'
        git(self.root, 'worktree', 'add', '-q', str(wt), '-b', 'other')
        made = rf.issue_probe(wt, '#1')  # from anywhere: the record is in the main checkout
        self.assertEqual(made['probe']['sessions'], [])
        self.assertTrue((self.root / rf.DIR / 'issues' / 'probes' / 'I-001.json').exists())
        self.assertFalse((wt / rf.DIR / 'issues' / 'probes').exists())
        self.assertEqual(made['thread'][-1]['t'], 'sys')
        again = rf.issue_probe(self.root, 'I-001')  # a second time adds nothing
        self.assertEqual(len([e for e in again['thread'] if '交给 AI 排查' in (e.get('text') or '')]), 1)
        rf.issue_attach(wt, '#1', 'sess-1')
        rf.issue_attach(self.root, 'I-001', 'sess-1')
        self.assertEqual(rf.issue_show(self.root, '#1')['probe']['sessions'], ['sess-1'])
        with self.assertRaisesRegex(ValueError, '没有交给 AI 排查'):
            rf.issue_new(self.root, '另一条')
            rf.issue_attach(self.root, '#2', 'sess-2')
        self.assertIn('问题 #001：交给 AI 排查', self.log())

    @in_repo_layout
    def test_a_finding_is_its_own_thread_entry_sets_the_kind_and_asks_for_a_decision(self):
        rf.issue_probe(self.root, '#1')
        before = rf.issue_show(self.root, '#1')
        self.assertEqual(before['kind'], 'ask')
        made = self.finding()
        entry = made['thread'][-1]
        self.assertEqual((entry['t'], entry['who'], entry['kind']), ('finding', 'AI', 'bug'))
        for key in ('text', 'steps', 'where', 'verified', 'inferred'):
            self.assertTrue(entry[key], key)
        self.assertEqual((made['kind'], made['kind_by'], made['state']), ('bug', 'probe', 'todo'))
        self.assertFalse(made['needs_triage'])  # 等你决定：no further facts-only judgement is wanted
        self.assertFalse(made['triage_failed'])
        self.assertIn('AI 排查有了结论：是问题', (self.root / made['file']).read_text(encoding='utf-8'))
        self.assertIn('问题 #001：AI 排查的结论（是问题）', self.log())
        self.assertEqual(git(self.root, 'status', '--porcelain', '--', rf.DIR).strip(), '')

    def test_a_finding_makes_the_issue_fixable_and_every_kind_works(self):
        rf.issue_probe(self.root, '#1')
        self.finding()
        fix = rf.issue_fix(self.root, ['#1'])  # no facts-only triage is waiting any more
        self.assertEqual(fix['issues'], ['I-001'])
        rf.issue_new(self.root, '想要导出 PDF')
        rf.issue_probe(self.root, '#2')
        made = rf.issue_finding(self.root, '#2', 'request', '现在没有导出 PDF，要做得先定范围。', verified='看了界面和代码，确实没有。', inferred='工作量不小。')
        self.assertEqual((made['kind'], made['thread'][-1]['kind']), ('request', 'request'))
        rf.issue_new(self.root, '找不到归档')
        rf.issue_probe(self.root, '#3')
        made = rf.issue_finding(self.root, '#3', '已有用法', '在已解决页签里。', verified='在界面上点过，能看到。', inferred='没有。')
        self.assertEqual(made['kind'], 'usage')
        rf.issue_new(self.root, '偶尔卡住')
        rf.issue_probe(self.root, '#4')
        made = rf.issue_finding(self.root, '#4', 'ask', '试了三次都没卡住，需要知道卡住前做了什么。')
        self.assertEqual((made['kind'], made['thread'][-1].get('verified')), ('ask', None))

    def test_a_finding_must_say_what_was_verified_and_what_is_inferred(self):
        rf.issue_probe(self.root, '#1')
        for bad, why in ((dict(kind='怪'), '类别'), (dict(text=''), '结论是空的'), (dict(verified=''), '分开写'), (dict(inferred=''), '分开写'), (dict(steps=''), '怎么复现')):
            with self.assertRaisesRegex(ValueError, why):
                self.finding(**bad)
        self.assertEqual([e['t'] for e in rf.issue_show(self.root, '#1')['thread']].count('finding'), 0)

    def test_a_finding_is_only_for_a_todo_issue_that_was_handed_over(self):
        with self.assertRaisesRegex(ValueError, '没有交给 AI 排查'):
            self.finding()
        rf.issue_probe(self.root, '#1')
        self.finding()
        rf.issue_fix(self.root, ['#1'])
        with self.assertRaisesRegex(ValueError, '现在是处理中'):
            self.finding()
        with self.assertRaisesRegex(ValueError, '现在是处理中'):
            rf.issue_probe(self.root, '#1')
        rf.issue_new(self.root, '不处理的')
        rf.issue_probe(self.root, '#2')
        rf.issue_set(self.root, '#2', 'dismiss', '不要了')
        with self.assertRaisesRegex(ValueError, '现在是已取消'):
            rf.issue_finding(self.root, '#2', 'bug', 'x', 'x', verified='x', inferred='x')

    def test_a_reply_into_the_open_session_is_not_judged_again_and_the_finding_covers_it(self):
        rf.issue_probe(self.root, '#1')
        made = rf.issue_reply(self.root, '#1', '在列表页', to_probe=True)
        self.assertEqual((made['needs_triage'], made['thread'][-1].get('probe')), (False, True))
        plain = rf.issue_reply(self.root, '#1', '补一句')  # not into the session: judged again as before
        self.assertTrue(plain['needs_triage'])
        self.assertTrue(self.finding()['thread'][-1]['upto'] >= plain['thread'][-1]['n'])
        self.assertFalse(rf.issue_show(self.root, '#1')['needs_triage'])
        rf.issue_new(self.root, '别的')
        with self.assertRaisesRegex(ValueError, '没有开着的排查会话'):
            rf.issue_reply(self.root, '#2', '说一句', to_probe=True)

    def test_a_judgement_that_finished_after_the_finding_is_not_written_over_it(self):
        rf.issue_probe(self.root, '#1')
        rf.issue_reply(self.root, '#1', '再补一句')
        self.judge(kind='usage', reason='有用法。', howto='点一下。')
        real = rf.issues.triage.run_model
        def late(*a, **k):
            self.finding()  # the investigation records its finding while the facts-only call is still running
            return real(*a, **k)
        with mock.patch.object(rf.issues.triage, 'run_model', late), self.assertRaisesRegex(ValueError, '已经有了结论'):
            rf.issue_triage(self.root, '#1')
        made = rf.issue_show(self.root, '#1')
        self.assertEqual((made['kind'], made['thread'][-1]['t']), ('bug', 'finding'))

    def test_the_brief_of_a_fix_carries_the_finding(self):
        rf.issue_probe(self.root, '#1')
        self.finding()
        fix = rf.issue_fix(self.root, ['#1'])
        brief = rf.issue_brief(fix['worktree'], fix['id'])
        for needle in ('排查结论', '打开文档列表，点导出。', 'app.py 的 export()', '实际复现、验证过的：按步骤在主目录里跑了一遍', '只是推断、没验证的：多半是改版时漏了绑定'):
            self.assertIn(needle, brief)
        self.assertEqual(brief, rf.issue_brief(fix['worktree']))  # from inside the working directory too

    def test_the_brief_of_an_issue_is_for_the_investigation(self):
        shots = Path(self.tmp.name) / 'a.png'
        shots.write_bytes(b'\x89PNG')
        rf.issue_reply(self.root, '#1', '在列表页', [str(shots)])
        brief = rf.issue_brief(self.root, '#1')
        self.assertIn('问题 #001 导出按钮点了没有反应', brief)
        self.assertIn('在列表页', brief)
        self.assertIn('截图：', brief)
        self.assertIn('RF-101 文档登记', brief)
        self.assertIn('登记文档', brief)  # the journey
        self.assertNotIn('修复 F-', brief)

    def test_the_fix_prompt_in_the_triage_payload_knows_the_finding(self):
        rf.issue_probe(self.root, '#1')
        self.finding()
        home = self.root
        path, meta, sections = rf.find_issue(home, '#1')
        payload = rf.triage_payload(home, meta, sections, rf.read_thread(home, 'I-001'))
        self.assertIn('排查会话读过代码后的结论', payload)

    def test_the_command_line(self):
        def run(*argv):
            with redirect_stdout(io.StringIO()) as out, mock.patch('sys.stderr', io.StringIO()) as err:
                code = as_app(['issue', *argv, '--root', str(self.root)])  # 这里点的是应用里的按钮（带应用的标记）
            return code, out.getvalue(), err.getvalue()
        self.assertEqual(run('finding', '#1', '--kind', 'bug', '--text', 'x')[0], 1)  # not handed over yet
        self.assertEqual(run('probe', '#1')[0], 0)
        self.assertEqual(run('attach', '#1', 'sess-9')[0], 0)
        code, out, err = run('finding', '#1', '--kind', 'bug', '--text', '点击没有绑定', '--steps', '点导出', '--where', 'app.py',
                              '--verified', '复现了', '--inferred', '没有')
        self.assertEqual(code, 0, err)
        self.assertIn('不要自己开始修复', out)
        self.assertEqual(run('reply', '#1', '在列表页', '--to-probe')[0], 0)
        code, out, _ = run('brief', '#1')
        self.assertEqual(code, 0)
        self.assertIn('排查结论', out)
        code, out, _ = run('show', '#1', '--json')
        self.assertEqual(json.loads(out)['probe']['sessions'], ['sess-9'])


class ProbeStopTests(IssueBase):
    """排查会话停下来时的检查：干了不少活、结论只留在聊天里的，叫回去记下来。"""

    def setUp(self):
        super().setUp()
        rf.issue_new(self.root, '导出按钮点了没有反应')
        rf.issue_probe(self.root, '#1')
        rf.issue_attach(self.root, '#1', 'probe-sess')

    def transcript(self, *steps, last='我查完了。'):
        path = Path(self.tmp.name) / 't.jsonl'
        lines = [json.dumps({'type': 'user', 'message': {'role': 'user', 'content': '开始排查'}}, ensure_ascii=False)]
        for name, data in steps:
            lines.append(json.dumps({'type': 'assistant', 'message': {'role': 'assistant', 'content': [{'type': 'tool_use', 'name': name, 'input': data}]}}, ensure_ascii=False))
        lines.append(json.dumps({'type': 'assistant', 'message': {'role': 'assistant', 'content': [{'type': 'text', 'text': last}]}}, ensure_ascii=False))
        path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        return path

    def data(self, *steps, last='我查完了。', **extra):
        return dict({'cwd': str(self.root), 'transcript_path': str(self.transcript(*steps, last=last)), 'session_id': 'probe-sess', 'last_assistant_message': last}, **extra)

    LOOKS = (('Read', {'file_path': 'a.py'}), ('Grep', {'pattern': 'export'}), ('Bash', {'command': 'python3 app.py'}))

    def test_work_without_a_finding_or_a_question_is_sent_back_to_record_it(self):
        reason = rf.probe_stop_block(self.data(*self.LOOKS))
        self.assertIn('没有记下排查结论', reason)
        self.assertIn('issue finding I-001', reason)
        self.assertIn('不要改任何文件', reason)

    def test_what_is_left_alone(self):
        self.assertIsNone(rf.probe_stop_block(self.data(*self.LOOKS, stop_hook_active=True)))
        self.assertIsNone(rf.probe_stop_block(self.data(*self.LOOKS, last='原因还不清楚。你当时点的是哪个导出按钮？')))  # asked
        self.assertIsNone(rf.probe_stop_block(self.data(*self.LOOKS, last='还要问一句。\n是在列表页吗?')))
        self.assertIsNone(rf.probe_stop_block(self.data(*self.LOOKS[:1])))  # a quick look and a plain reply
        self.assertIsNone(rf.probe_stop_block(self.data()))
        asked = self.data(*self.LOOKS, ('AskUserQuestion', {'questions': []}))
        self.assertIsNone(rf.probe_stop_block(asked))
        recorded = self.data(*self.LOOKS, ('Bash', {'command': 'python3 /x/rf.py issue finding I-001 --kind bug --text x'}))
        self.assertIsNone(rf.probe_stop_block(recorded))
        self.assertIsNone(rf.probe_stop_block(dict(self.data(*self.LOOKS), session_id='some-other-session')))  # not a probe session
        self.assertIsNone(rf.probe_stop_block(dict(self.data(*self.LOOKS), cwd=self.tmp.name)))

    def test_a_conclusion_stated_in_the_chat_is_caught_even_after_little_work(self):
        said = '结论：根因是导出按钮没有绑定点击处理。'
        self.assertIn('没有记下排查结论', rf.probe_stop_block(self.data(*self.LOOKS[:1], last=said)))

    def test_it_is_part_of_the_stop_hook_and_stops_mattering_once_the_issue_moves_on(self):
        from relayforge.project import status
        data = self.data(*self.LOOKS, hook_event_name='Stop')
        with redirect_stdout(io.StringIO()) as out:
            status.hook_event(data)
        self.assertIn('没有记下排查结论', json.loads(out.getvalue())['reason'])
        rf.issue_finding(self.root, '#1', 'bug', '没绑定', steps='点导出', verified='复现了', inferred='没有')
        rf.issue_fix(self.root, ['#1'])
        self.assertIsNone(rf.probe_stop_block(self.data(*self.LOOKS)))  # being fixed now: not an investigation any more


class ConvertTests(IssueBase):
    def test_an_issue_becomes_a_draft_requirement(self):
        shot = Path(self.tmp.name) / 'a.png'
        shot.write_bytes(b'\x89PNG')
        rf.issue_new(self.root, '希望能导出 PDF', '月底要给团队看', '你', [str(shot)], feature='RF-102')
        rf.issue_reply(self.root, '#1', '范围先按当前筛选的结果')
        self.judge(kind='request', reason='想多做一件事。')
        rf.issue_triage(self.root, '#1')
        made = rf.issue_to_req(self.root, '#1')
        req, issue = made['requirement'], made['issue']
        self.assertEqual((req['state'], req['title']), ('draft', '希望能导出 PDF'))
        self.assertEqual(req['sections']['需求说明'], ['月底要给团队看', '范围先按当前筛选的结果'])
        self.assertEqual(req['sections']['要改的功能点'], ['RF-102'])  # 你关联的功能点，不是推断
        self.assertIn('由问题 #001 转来', ' '.join(req['sections']['变更记录']))
        self.assertEqual(len(made['images']), 1)
        self.assertTrue(Path(made['images'][0]).is_file())
        self.assertIn('范围先按当前筛选的结果', Path(made['thread_file']).read_text(encoding='utf-8'))
        self.assertEqual((issue['state'], issue['closed_as'], issue['requirement']), ('closed', 'requirement', req['id']))
        self.assertEqual(git(Path(req['worktree']), 'status', '--porcelain'), '')
        self.assertEqual(req['source_issue'], 'I-001')  # 需求面板上「查看来源问题」
        with self.assertRaisesRegex(ValueError, '转成需求'):
            rf.issue_set(self.root, '#1', 'reopen')

    @in_repo_layout
    def test_a_draft_requirement_becomes_an_issue_in_the_main_checkout(self):
        made = rf.req_new(self.root, '登记页闪一下')
        place = Path(made['worktree'])
        path = self.root / made['file']
        meta, sections = rf.read_doc(path)
        sections['需求说明'] = ['- 〔待确认〕打开登记页时闪一下〔S-real#7〕', '- 每次都会〔S-real#8〕']
        rf.write_doc(path, meta, sections)
        (self.root / 'unrelated.txt').write_text('someone else staged this', encoding='utf-8')
        git(self.root, 'add', 'unrelated.txt')
        issue = rf.issue_from_req(place, made['id'], 'RF-101')
        self.assertEqual((issue['source'], issue['source_requirement'], issue['feature'], issue['state']), ('你', made['id'], 'RF-101', 'todo'))
        self.assertTrue(issue['needs_triage'])
        file_meta, file_sections = rf.read_doc(self.root / issue['file'])
        self.assertEqual(file_sections['现象'], sections['需求说明'])  # 原话、原来的标记和出处
        self.assertIn('〔S-real#7〕', issue['thread'][0]['text'])
        self.assertIn('需求转为问题 #001：登记页闪一下', self.log())
        self.assertNotIn('.relayforge/issues', git(place, 'ls-tree', '-r', '--name-only', made['branch']))
        found = rf.find_requirement(self.root, made['id'])
        self.assertEqual((found['state'], found['converted_issue']), ('draft', 'I-001'))
        self.assertIn('转为问题 #001', ' '.join(found['sections']['变更记录']))
        self.assertEqual(git(self.root, 'diff', '--cached', '--name-only').strip(), 'unrelated.txt')  # 只提交了 .relayforge/ 下的
        self.assertNotIn('unrelated.txt', git(self.root, 'show', '--format=', '--name-only', 'HEAD'))
        self.assertIn(made['file'], git(self.root, 'show', '--format=', '--name-only', 'HEAD'))
        self.assertEqual(rf.issue_from_req(self.root, made['id'])['id'], 'I-001')  # 再转一次还是这一条
        other = rf.req_new(self.root, '已经在做的')
        self.write_check(other)
        rf.req_start(self.root, other['id'])
        with self.assertRaisesRegex(ValueError, '只有立项中的需求'):
            rf.issue_from_req(self.root, other['id'])
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(as_app(['issue', 'from-req', made['id'], '--root', str(self.root), '--json']), 0)
        self.assertEqual(json.loads(out.getvalue())['id'], 'I-001')

    def test_issues_recorded_on_the_target_do_not_send_a_requirement_back_to_its_session(self):
        made = rf.req_new(self.root, '导出')
        place = Path(made['worktree'])
        self.write_check(made)
        rf.req_start(self.root, made['id'])
        self.commit_in(place, 'export.py', 'print(1)\n')
        rf.issue_new(place, '顺手发现的', source='Agent')  # a commit on main, but only the program's own issue files
        self.assertEqual(rf.req_done(self.root, made['id'])['state'], 'done')
        self.assertTrue((self.root / 'export.py').exists())
        self.assertTrue(rf.issue_show(self.root, '#1'))

    def write_check(self, made):
        path = self.root / made['file']
        meta, sections = rf.read_doc(path)
        sections['验收条件'] = ['- 能用〔S-x#1〕']
        rf.write_doc(path, meta, sections)

    def test_the_brief_of_a_requirement_lists_open_issues_on_its_features(self):
        rf.issue_new(self.root, '文档列表排序乱了', feature='RF-102')
        rf.issue_new(self.root, '登记页闪一下', feature='RF-101')
        rf.issue_new(self.root, '已经不管了', feature='RF-102')
        rf.issue_set(self.root, '#3', 'dismiss', '不重要')
        made = rf.req_new(self.root, '列表加筛选')
        path = self.root / made['file']
        meta, sections = rf.read_doc(path)
        rf.add_line(sections, '要改的功能点', '- RF-102 文档列表：加筛选〔S-x#1〕')
        rf.write_doc(path, meta, sections)
        found = rf.find_requirement(self.root, made['id'])
        self.assertEqual([i['id'] for i in found['brief']['issues']], ['I-001'])
        self.assertIn('#001 文档列表排序乱了（RF-102 文档列表，待处理）', rf.brief_text(found))


class InitTests(IssueBase):
    def test_the_rules_for_agents_are_written(self):
        agents = (self.root / rf.DIR / 'CONVENTIONS.md').read_text(encoding='utf-8')  # 数据外置的项目：约定在本机记录里
        self.assertIn('issue new', agents)
        self.assertIn('--source Agent', agents)
        self.assertIn('rf.py issue confirm（确认修好了）、rf.py issue unconfirm（撤回确认）和 rf.py issue reject（还有问题）只由用户触发', agents)
        fmt = (self.root / rf.DIR / 'FORMAT.md').read_text(encoding='utf-8')
        self.assertIn('## 问题（issues/）', fmt)
        self.assertIn('- 问题：现象、修复报告、变更记录。', fmt)


if __name__ == '__main__':
    unittest.main()

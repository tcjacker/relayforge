"""追溯：需求和修复工作目录里的提交由 git 钩子带上出处（trailers），rf.py trace 和 rf.py why 从这些行（和老提交的合并关系）算出改动、会话和原因。"""
import io
import json
import os
import re
import stat
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
from tests.test_issues import git  # noqa: E402
from tests.test_publish import PublishBase  # noqa: E402

SID = '11112222-3333-4444-5555-666677778888'


def body(place, rev='HEAD'):
    return git(place, 'log', '-1', '--format=%B', rev)


def lines_of(place, key, rev='HEAD'):
    return re.findall(rf'^{key}: (.+)$', body(place, rev), re.M)


class TrailerBase(PublishBase):
    def open_requirement(self, title='导出', attach=True):
        made = rf.req_new(self.root, title)
        path = self.root / made['file']
        meta, sections = rf.read_doc(path)
        rf.add_line(sections, '验收条件', '- 能导出〔S-test#1〕')
        rf.add_line(sections, '需求说明', f'- 用户要把账单导出成文件〔S-test#2〕')
        rf.write_doc(path, meta, sections)
        rf.req_start(self.root, made['id'])
        if attach:
            rf.req_attach(self.root, made['id'], SID)
        return made

    def add_decision(self, made, sid_tag):
        base = self.root / rf.DIR / 'decisions'
        rf.write_doc(base / 'D-0001-用文件名区分.md', {'id': 'D-0001', 'type': 'decision', 'title': '用文件名区分', 'status': 'current', 'in_progress': None},
                     {'决定': [f'- 导出的文件按日期命名〔{sid_tag}#3〕'], '理由': [], '没有选的方案': [], '变更记录': []})

    def user_hook(self, name, text):
        hooks = self.root / '.git' / 'hooks'
        hooks.mkdir(exist_ok=True)
        path = hooks / name
        path.write_text('#!/bin/sh\n' + text, encoding='utf-8')
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return path


class TrailersOnCommits(TrailerBase):
    def test_every_commit_in_a_requirement_worktree_carries_its_trailers_and_the_main_checkout_none(self):
        made = self.open_requirement()
        wt = Path(made['worktree'])
        tag = rf.stable_tag(self.root, SID)
        with mock.patch.dict(os.environ, {'RELAYFORGE_SESSION_ID': SID}):
            self.commit_in(wt, 'a.py', '1\n', 'plain -qm')
            self.assertEqual(lines_of(wt, 'RelayForge-Requirement'), [made['id']])
            self.assertEqual(lines_of(wt, 'RelayForge-Session'), [tag])
            (wt / 'b.py').write_text('2\n')
            git(wt, 'add', '-A')
            git(wt, 'commit', '-q', '--no-verify', '-m', 'no verify')  # --no-verify does not skip prepare-commit-msg
            self.assertEqual(lines_of(wt, 'RelayForge-Requirement'), [made['id']])
            (wt / 'c.py').write_text('3\n')
            git(wt, 'add', '-A')
            (wt / 'msg.txt').write_text('从文件读的说明\n\n正文第二段\n')
            git(wt, 'commit', '-q', '-F', 'msg.txt')
            self.assertEqual(lines_of(wt, 'RelayForge-Session'), [tag])
            # amend, twice: still one of each
            git(wt, 'commit', '-q', '--amend', '--no-edit')
            git(wt, 'commit', '-q', '--amend', '-m', '改了说明')
            self.assertEqual(lines_of(wt, 'RelayForge-Requirement'), [made['id']])
            self.assertEqual(lines_of(wt, 'RelayForge-Session'), [tag])
            self.assertEqual(body(wt).count('RelayForge-'), 2)
            # a merge made inside the worktree (the target branch moved on): the merge commit carries them too
            self.commit_in(self.root, 'main_only.py', 'x\n', '主线上的提交')
            git(wt, 'merge', '--no-edit', 'main')
            self.assertEqual(len(git(wt, 'log', '-1', '--format=%P').split()), 2)
            self.assertEqual(lines_of(wt, 'RelayForge-Requirement'), [made['id']])
            self.assertEqual(lines_of(wt, 'RelayForge-Session'), [tag])
            # the commit the target branch brought in is not ours
            self.assertEqual(lines_of(wt, 'RelayForge-Requirement', 'HEAD^2'), [])
        # the user's own commit in the project directory: nothing added
        self.commit_in(self.root, 'mine.py', 'y\n', '我自己的提交')
        self.assertNotIn('RelayForge-', body(self.root))

    def test_the_main_checkout_is_left_alone(self):
        before = sorted(p.name for p in (self.root / '.git' / 'hooks').iterdir())
        made = self.open_requirement()
        config = git(self.root, 'config', '--local', '--list')
        self.assertNotIn('hookspath', config.lower())
        self.assertEqual(sorted(p.name for p in (self.root / '.git' / 'hooks').iterdir()), before)
        self.assertIn('hookspath', git(Path(made['worktree']), 'config', '--worktree', '--list').lower())
        self.assertFalse(rf.ensure_here(self.root)['ok'])
        self.assertFalse(self.root.joinpath('.git', 'relayforge-hooks').exists())
        self.assertEqual(subprocess.run(['git', '-C', str(self.root), 'config', '--worktree', '--get', 'core.hooksPath'], capture_output=True, text=True).stdout.strip(), '')

    def test_session_falls_back_to_the_newest_attached_one_without_the_environment_variable(self):
        made = self.open_requirement(attach=False)
        wt = Path(made['worktree'])
        self.commit_in(wt, 'a.py', '1\n', '还没挂会话')
        self.assertEqual(lines_of(wt, 'RelayForge-Session'), [])
        rf.req_attach(self.root, made['id'], 'aaaa0000-0000-0000-0000-000000000001')
        rf.req_attach(self.root, made['id'], SID)
        self.commit_in(wt, 'b.py', '2\n', '挂了两个会话')
        self.assertEqual(lines_of(wt, 'RelayForge-Session'), [rf.stable_tag(self.root, SID)])
        # a session the program has not seen yet: named like the program names one (today's date and the first four characters)
        with mock.patch.dict(os.environ, {'RELAYFORGE_SESSION_ID': 'zzzz9999-0000-0000-0000-000000000000'}):
            self.commit_in(wt, 'c.py', '3\n', '陌生的会话')
        self.assertEqual(lines_of(wt, 'RelayForge-Session'), [rf.session_tag('zzzz9999-0000-0000-0000-000000000000')])
        # Claude Code's own variable is the second choice (the app's variable wins when both are there)
        with mock.patch.dict(os.environ, {'CLAUDE_CODE_SESSION_ID': SID}):
            self.commit_in(wt, 'd.py', '4\n', 'Claude Code 自己的变量')
        self.assertEqual(lines_of(wt, 'RelayForge-Session'), [rf.stable_tag(self.root, SID)])
        with mock.patch.dict(os.environ, {'CLAUDE_CODE_SESSION_ID': SID, 'RELAYFORGE_SESSION_ID': 'zzzz9999-0000-0000-0000-000000000000'}):
            self.commit_in(wt, 'e.py', '5\n', '两个都有')
        self.assertEqual(lines_of(wt, 'RelayForge-Session'), [rf.session_tag('zzzz9999-0000-0000-0000-000000000000')])

    def test_a_fix_worktree_names_the_fix_its_issues_and_the_session(self):
        rf.issue_new(self.root, '启动时打印旧的', feature='RF-101')
        rf.issue_new(self.root, '退出时没有保存', feature='RF-101')
        self.judge(kind='bug', reason='像是没改。')
        rf.issue_triage(self.root, 'I-001')
        rf.issue_triage(self.root, 'I-002')
        self.commit_in(self.root, 'app.py', 'print("old")\n', '加 app')
        fix = rf.issue_fix(self.root, ['#1', '#2'])
        place = Path(fix['worktree'])
        rf.issue_attach(self.root, fix['id'], SID)
        self.commit_in(place, 'app.py', 'print("new")\n', '改成新的')
        self.assertEqual(lines_of(place, 'RelayForge-Fix'), [fix['id']])
        self.assertEqual(lines_of(place, 'RelayForge-Issue'), ['I-001', 'I-002'])
        self.assertEqual(lines_of(place, 'RelayForge-Session'), [rf.stable_tag(self.root, SID)])
        self.assertEqual(lines_of(place, 'RelayForge-Requirement'), [])
        git(place, 'commit', '-q', '--amend', '--no-edit')
        self.assertEqual(body(place).count('RelayForge-Issue'), 2)

    def test_a_worktree_opened_before_this_existed_gets_the_hooks_when_the_session_opens(self):
        made = self.open_requirement()
        wt = Path(made['worktree'])
        git(wt, 'config', '--worktree', '--unset', 'core.hooksPath')  # the state of an older working directory
        self.commit_in(wt, 'a.py', '1\n', '以前开的')
        self.assertEqual(lines_of(wt, 'RelayForge-Requirement'), [])
        with redirect_stdout(io.StringIO()) as out:
            code = rf.main(['trailers', 'install', '--root', str(wt), '--json'])
        self.assertEqual((code, json.loads(out.getvalue())['ok']), (0, True))
        self.commit_in(wt, 'b.py', '2\n', '开会话以后')
        self.assertEqual(lines_of(wt, 'RelayForge-Requirement'), [made['id']])
        # and again: idempotent
        with redirect_stdout(io.StringIO()):
            rf.main(['trailers', 'install', '--root', str(wt)])
        self.commit_in(wt, 'c.py', '3\n', '再装一遍')
        self.assertEqual(body(wt).count('RelayForge-Requirement'), 1)


class UserHooksStillRun(TrailerBase):
    def test_the_users_hooks_run_in_the_worktree_and_their_changes_are_kept(self):
        marks = Path(self.tmp.name) / 'marks.txt'
        self.user_hook('prepare-commit-msg', f'echo "prepare $2" >> {marks}\nprintf "\\n用户的钩子加的一行\\n" >> "$1"\n')
        self.user_hook('pre-commit', f'echo "pre-commit in $(basename "$PWD")" >> {marks}\n')
        self.user_hook('commit-msg', f'echo "commit-msg" >> {marks}\n')
        self.user_hook('post-commit', f'echo "post-commit" >> {marks}\n')
        made = self.open_requirement()
        wt = Path(made['worktree'])
        self.commit_in(wt, 'a.py', '1\n', '带着用户的钩子')
        text = marks.read_text()
        for expected in ('prepare message', 'pre-commit in ' + wt.name, 'commit-msg', 'post-commit'):
            self.assertIn(expected, text)
        self.assertIn('用户的钩子加的一行', body(wt))   # their change to the message is kept
        self.assertEqual(lines_of(wt, 'RelayForge-Requirement'), [made['id']])  # and ours is added after it
        # the project directory: their hooks run exactly as before, and nothing of ours
        marks.write_text('')
        self.commit_in(self.root, 'mine.py', 'y\n', '主目录里的提交')
        self.assertIn('pre-commit in project', marks.read_text())
        self.assertNotIn('RelayForge-', body(self.root))

    def test_a_failing_user_hook_still_stops_the_commit_in_the_worktree(self):
        made = self.open_requirement()
        wt = Path(made['worktree'])
        self.user_hook('pre-commit', 'echo "不让提交" >&2\nexit 1\n')
        (wt / 'a.py').write_text('1\n')
        git(wt, 'add', '-A')
        done = subprocess.run(['git', 'commit', '-qm', 'x'], cwd=wt, capture_output=True, text=True)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn('不让提交', done.stderr)
        # a failing prepare-commit-msg of the user's: also theirs to decide
        (self.root / '.git' / 'hooks' / 'pre-commit').unlink()
        self.user_hook('prepare-commit-msg', 'exit 3\n')
        self.assertNotEqual(subprocess.run(['git', 'commit', '-qm', 'x'], cwd=wt, capture_output=True, text=True).returncode, 0)

    def test_a_repository_that_sets_its_own_hooks_path_is_chained_too(self):
        marks = Path(self.tmp.name) / 'marks.txt'
        custom = self.root / '.githooks'
        custom.mkdir()
        hook = custom / 'pre-commit'
        hook.write_text(f'#!/bin/sh\necho "husky-style pre-commit in $(basename "$PWD")" >> {marks}\n')
        hook.chmod(0o755)
        git(self.root, 'add', '-A')
        git(self.root, 'commit', '-qm', '加钩子')
        git(self.root, 'config', 'core.hooksPath', '.githooks')
        made = self.open_requirement()  # opened after: the worktree branch off main has .githooks checked out
        wt = Path(made['worktree'])
        self.commit_in(wt, 'a.py', '1\n', '带着仓库自己的钩子目录')
        self.assertIn('husky-style pre-commit in ' + wt.name, marks.read_text())
        self.assertEqual(lines_of(wt, 'RelayForge-Requirement'), [made['id']])
        self.assertEqual(git(self.root, 'config', '--local', '--get', 'core.hooksPath').strip(), '.githooks')  # theirs, untouched


class NeverFailsACommit(TrailerBase):
    def test_a_broken_hook_environment_does_not_fail_the_commit(self):
        made = self.open_requirement()
        wt = Path(made['worktree'])
        folder = Path(git(wt, 'rev-parse', '--absolute-git-dir').strip()) / 'relayforge-hooks'
        # 1. none of the helper programs are on PATH (only git itself)
        tools = Path(self.tmp.name) / 'tools'
        tools.mkdir()
        (tools / 'git').symlink_to(subprocess.run(['which', 'git'], capture_output=True, text=True).stdout.strip())
        (wt / 'a.py').write_text('1\n')
        git(wt, 'add', '-A')
        env = dict(os.environ, PATH=str(tools), RELAYFORGE_SESSION_ID=SID)
        done = subprocess.run(['git', 'commit', '-qm', '没有任何工具'], cwd=wt, capture_output=True, text=True, env=env)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertNotIn('S--', body(wt))  # no half-made session line
        # 2. the data file is garbage / a directory
        (folder / 'trailers').write_text('\x00\x01 not a trailer\nRelayForge-Requirement: \nsession\n')
        self.commit_in(wt, 'b.py', '2\n', '数据文件坏了')
        (folder / 'trailers').unlink()
        (folder / 'trailers').mkdir()
        self.commit_in(wt, 'c.py', '3\n', '数据文件是个目录')
        # 3. the hook script itself is not executable or git cannot interpret trailers
        (folder / 'prepare-commit-msg').chmod(0o644)
        self.commit_in(wt, 'd.py', '4\n', '钩子没有执行权限')
        self.assertEqual(git(wt, 'log', '-1', '--format=%s').strip(), '钩子没有执行权限')
        self.assertEqual(len(git(wt, 'log', '--format=%s').splitlines()), 5)


class TraceTests(TrailerBase):
    def work(self, title, files, session=SID, commit_message=None):
        made = self.open_requirement(title, attach=False)
        rf.req_attach(self.root, made['id'], session)
        wt = Path(made['worktree'])
        with mock.patch.dict(os.environ, {'RELAYFORGE_SESSION_ID': session}):
            for name, text in files:
                self.commit_in(wt, name, text, commit_message or f'{title}：写 {name}')
        return made

    def test_trace_of_a_finished_requirement_from_trailers(self):
        made = self.work('导出账单', [('export.py', 'print(1)\n'), ('export_test.py', 'print(2)\nprint(3)\n')])
        self.add_decision(made, rf.stable_tag(self.root, SID))
        rf.req_done(self.root, made['id'])
        merge = rf.read_doc(self.root / made['file'])[0]['merge_commit']
        self.assertEqual(lines_of(self.root, 'RelayForge-Requirement', merge), [made['id']])  # the merge commit made by req done
        self.assertNotIn('RelayForge-Session', body(self.root, merge))
        t = rf.trace_ref(self.root, made['id'])
        self.assertEqual((t['kind'], t['state'], t['commit_count'], t['file_count']), ('requirement', 'done', 2, 2))
        self.assertEqual(t['merge_commit']['hash'], merge)
        self.assertEqual([c['via'] for c in t['commits']], ['trailer', 'trailer'])
        self.assertEqual({f['path']: (f['added'], f['deleted']) for f in t['files']}, {'export.py': (1, 0), 'export_test.py': (2, 0)})
        tag = rf.stable_tag(self.root, SID)
        self.assertEqual([s['tag'] for s in t['sessions']], [tag])
        self.assertEqual(t['commits'][0]['sessions'], [tag])
        self.assertFalse(t['published'])
        text = rf.trace_text(t)
        self.assertIn('提交 2 个', text)
        # not published, then published to the local bare remote
        self.published()
        self.assertTrue(rf.trace_ref(self.root, made['id'])['published'])
        self.assertEqual(rf.trace_ref(self.root, made['id'])['remotes'], ['origin/main'])
        self.assertIn('已发布', rf.trace_text(rf.trace_ref(self.root, made['id'])))

    def test_counts_are_written_into_the_completion_record(self):
        made = self.work('导出账单', [('export.py', 'print(1)\n'), ('export2.py', 'print(2)\n'), ('export3.py', 'print(3)\n')])
        rf.req_done(self.root, made['id'])
        text = (self.root / made['file']).read_text(encoding='utf-8')
        self.assertIn('改动：3 个提交、3 个文件', text)
        self.assertNotIn('export2.py', text)  # only the numbers; the lists are worked out by trace

    def test_a_fix_records_its_counts_and_trace_works_from_an_issue(self):
        self.fix_through()
        fix = rf.all_fixes(self.root)['F-001']
        self.assertEqual((fix['commits'], fix['files']), (1, 1))
        merge = fix['merge_commit']
        self.assertEqual(lines_of(self.root, 'RelayForge-Fix', merge), ['F-001'])
        self.assertEqual(lines_of(self.root, 'RelayForge-Issue', merge), ['I-001'])
        t = rf.trace_ref(self.root, '#1')
        self.assertEqual((t['kind'], t['id'], t['commit_count']), ('fix', 'F-001', 1))
        self.assertEqual(t['issues'][0]['num'], '#001')
        self.assertEqual(rf.trace_ref(self.root, 'F-001')['commit_count'], 1)

    def fix_through(self):
        rf.issue_new(self.root, '启动时打印旧的', feature='RF-101')
        self.judge(kind='bug', reason='像是没改。')
        rf.issue_triage(self.root, 'I-001')
        self.commit_in(self.root, 'app.py', 'print("old")\n', '加 app')
        fix = rf.issue_fix(self.root, ['#1'])
        place = Path(fix['worktree'])
        rf.issue_attach(self.root, fix['id'], SID)
        with mock.patch.dict(os.environ, {'RELAYFORGE_SESSION_ID': SID}):
            self.commit_in(place, 'app.py', 'print("new")\n', '改成新的')
        rf.issue_handback(place, None, '改成打印新的', '运行 app.py', no_regression='这条测试只看交回流程，不看回归数据')
        rf.issue_confirm(self.root, '#1')
        return fix

    def test_trace_by_commit(self):
        made = self.work('导出账单', [('export.py', 'print(1)\n')])
        first = git(Path(made['worktree']), 'rev-parse', 'HEAD').strip()
        rf.req_done(self.root, made['id'])
        t = rf.trace_commit(self.root, first[:8])
        self.assertEqual((t['owner'], t['id']), ({'kind': 'requirement', 'id': made['id'], 'how': 'trailer'}, made['id']))
        self.assertEqual(t['commit']['hash'], first)
        # a commit that belongs to nothing
        self.commit_in(self.root, 'mine.py', 'y\n', '我自己改的')
        lone = rf.trace_commit(self.root, 'HEAD')
        self.assertIsNone(lone['owner'])
        self.assertIn('不属于任何需求或修复', lone['text'])
        with self.assertRaises(ValueError):
            rf.trace_commit(self.root, 'nosuchrev')

    @in_repo_layout
    def test_legacy_history_without_trailers_is_found_through_the_merge_commit(self):
        made = self.work('导出账单', [('export.py', 'print(1)\n'), ('export2.py', 'print(2)\n')])
        wt = Path(made['worktree'])
        # what the history looks like for work finished before this existed: no hooks, a merge commit without trailers
        git(wt, 'config', '--worktree', '--unset', 'core.hooksPath')
        self.commit_in(wt, 'export3.py', 'print(3)\n', '老办法提交的')
        with mock.patch('rf.flows.trailer_block', lambda *a, **k: ''):
            rf.req_done(self.root, made['id'])
        self.assertNotIn('RelayForge-', body(self.root))
        t = rf.trace_ref(self.root, made['id'])
        self.assertEqual((t['commit_count'], t['file_count']), (3, 3))
        self.assertEqual(sorted({c['via'] for c in t['commits']}), ['merge', 'trailer'])
        # no merge_commit recorded either (finished even earlier): found by the merge commit's title
        path = self.root / made['file']
        meta, sections = rf.read_doc(path)
        meta.pop('merge_commit', None)
        rf.write_doc(path, meta, sections)
        again = rf.trace_ref(self.root, made['id'])
        self.assertEqual((again['commit_count'], again['file_count']), (3, 3))
        owner = rf.trace_commit(self.root, git(self.root, 'rev-parse', 'HEAD~1^2').strip())
        self.assertEqual(owner['owner']['id'], made['id'])

    def test_a_requirement_still_in_progress_is_traced_from_its_branch(self):
        made = self.work('导出账单', [('export.py', 'print(1)\n')])
        t = rf.trace_ref(self.root, made['id'])
        self.assertEqual((t['state'], t['merged'], t['commit_count'], t['file_count'], t['published']), ('open', False, 1, 1, None))
        self.assertIn('进行中', rf.trace_text(t))

    def test_trace_cli_and_errors(self):
        made = self.work('导出账单', [('export.py', 'print(1)\n')])
        rf.req_done(self.root, made['id'])
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(rf.main(['trace', made['id'], '--json', '--root', str(self.root)]), 0)
        self.assertEqual(json.loads(out.getvalue())['commit_count'], 1)
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(rf.main(['trace', made['id'], '--root', str(self.root)]), 0)
        self.assertIn(made['id'], out.getvalue())
        with mock.patch('sys.stderr', io.StringIO()):
            self.assertEqual(rf.main(['trace', 'R-nothing', '--root', str(self.root)]), 1)
            self.assertEqual(rf.main(['trace', '--root', str(self.root)]), 1)


class WhyTests(TrailerBase):
    def history(self):
        """R1 writes export.py (3 lines); R2 changes its last line; the user changes the first line by hand; R2 then renames it."""
        r1 = self.open_requirement('导出账单')
        rf.req_attach(self.root, r1['id'], SID)
        self.add_decision(r1, rf.stable_tag(self.root, SID))
        with mock.patch.dict(os.environ, {'RELAYFORGE_SESSION_ID': SID}):
            self.commit_in(r1['worktree'], 'export.py', 'line1\nline2\nline3\n', '写导出')
        rf.req_done(self.root, r1['id'])
        other = 'bbbb0000-0000-0000-0000-000000000002'
        r2 = self.open_requirement('导出加表头')
        rf.req_attach(self.root, r2['id'], other)
        with mock.patch.dict(os.environ, {'RELAYFORGE_SESSION_ID': other}):
            self.commit_in(r2['worktree'], 'export.py', 'line1\nline2\nline3-changed\n', '加表头')
        rf.req_done(self.root, r2['id'])
        self.commit_in(self.root, 'export.py', 'LINE1\nline2\nline3-changed\n', '我手工改了第一行')
        return r1, r2

    def test_a_file_touched_by_two_requirements_and_the_user(self):
        r1, r2 = self.history()
        w = rf.why(self.root, 'export.py')
        self.assertEqual([i['id'] for i in w['items']], [r2['id'], r1['id']])  # newest first
        self.assertEqual([c['subject'] for c in w['unlinked']], ['我手工改了第一行'])
        first = w['items'][1]
        self.assertEqual(first['title'], '导出账单')
        self.assertEqual(first['说明'], ['用户要把账单导出成文件'])
        self.assertEqual(first['验收条件'], ['能导出'])
        self.assertEqual(first['裁定'], ['D-0001 用文件名区分：导出的文件按日期命名'])
        self.assertEqual(first['sessions'][0]['tag'], rf.stable_tag(self.root, SID))
        text = rf.why_text(w)
        self.assertIn('不属于任何需求或修复', text)
        self.assertIn('用户要把账单导出成文件', text)
        self.assertIn('能导出', text)

    def test_a_line_range_names_only_the_commits_that_last_changed_those_lines(self):
        r1, r2 = self.history()
        only_first_requirement = rf.why(self.root, 'export.py:2')
        self.assertEqual([i['id'] for i in only_first_requirement['items']], [r1['id']])
        self.assertEqual(only_first_requirement['unlinked'], [])
        third = rf.why(self.root, 'export.py:3')
        self.assertEqual([i['id'] for i in third['items']], [r2['id']])
        mine = rf.why(self.root, 'export.py:1-1')
        self.assertEqual(mine['items'], [])
        self.assertEqual(len(mine['unlinked']), 1)
        both = rf.why(self.root, 'export.py:2-3')
        self.assertEqual({i['id'] for i in both['items']}, {r1['id'], r2['id']})
        self.assertEqual(both['from'], 2)
        self.assertEqual(both['to'], 3)

    def test_renames_are_followed_for_a_whole_file(self):
        r1, r2 = self.history()
        git(self.root, 'mv', 'export.py', 'exporter.py')
        git(self.root, 'commit', '-qm', '改名')
        w = rf.why(self.root, 'exporter.py')
        self.assertEqual({i['id'] for i in w['items']}, {r1['id'], r2['id']})
        self.assertEqual([c['subject'] for c in w['unlinked']], ['改名', '我手工改了第一行'])
        # blame follows the file across the rename for lines too
        self.assertEqual([i['id'] for i in rf.why(self.root, 'exporter.py:2')['items']], [r1['id']])

    def test_a_fix_is_explained_by_its_issue(self):
        rf.issue_new(self.root, '启动时打印旧的', desc='运行 app.py 看到的是旧内容', feature='RF-101')
        self.judge(kind='bug', reason='像是没改。')
        rf.issue_triage(self.root, 'I-001')
        self.commit_in(self.root, 'app.py', 'print("old")\n', '加 app')
        fix = rf.issue_fix(self.root, ['#1'])
        rf.issue_attach(self.root, fix['id'], SID)
        with mock.patch.dict(os.environ, {'RELAYFORGE_SESSION_ID': SID}):
            self.commit_in(fix['worktree'], 'app.py', 'print("new")\n', '改成新的')
        rf.issue_handback(Path(fix['worktree']), None, '改成打印新的', '运行 app.py', no_regression='这条测试只看交回流程，不看回归数据')
        rf.issue_confirm(self.root, '#1')
        w = rf.why(self.root, 'app.py')
        self.assertEqual([i['id'] for i in w['items']], [fix['id']])
        issue = w['items'][0]['issues'][0]
        self.assertEqual(issue['title'], '启动时打印旧的')
        self.assertTrue(any('旧内容' in x for x in issue['现象']))
        self.assertTrue(any('改成打印新的' in x for x in issue['修复报告']))
        self.assertEqual([c['subject'] for c in w['unlinked']], ['加 app'])

    def test_legacy_history_and_limits_and_errors(self):
        made = self.open_requirement('导出账单')
        wt = Path(made['worktree'])
        git(wt, 'config', '--worktree', '--unset', 'core.hooksPath')
        self.commit_in(wt, 'old.py', 'x\n', '老办法')
        with mock.patch('rf.flows.trailer_block', lambda *a, **k: ''):
            rf.req_done(self.root, made['id'])
        w = rf.why(self.root, 'old.py')
        self.assertEqual([(i['id'], i['commits'][0]['how']) for i in w['items']], [(made['id'], 'merge')])
        self.assertIn('按合并提交认的', rf.why_text(w))
        # a path given as an absolute one inside the project, and a missing one
        self.assertEqual(rf.why(self.root, str(self.root / 'old.py'))['path'], 'old.py')
        with self.assertRaises(ValueError):
            rf.why(self.root, 'nothing.py')
        # many requirements on one file: the output is cut to a sensible size
        for n in range(8):
            m = self.open_requirement(f'第 {n} 次', attach=False)
            self.commit_in(m['worktree'], 'many.py', f'v{n}\n', f'第 {n} 次改')
            rf.req_done(self.root, m['id'])
        big = rf.why(self.root, 'many.py')
        self.assertEqual((len(big['items']), big['items_more']), (6, 2))
        self.assertIn('另有 2 个需求或修复没有展开', rf.why_text(big))
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(rf.main(['why', 'many.py:1', '--json', '--root', str(self.root)]), 0)
        self.assertEqual(json.loads(out.getvalue())['path'], 'many.py')


if __name__ == '__main__':
    unittest.main()

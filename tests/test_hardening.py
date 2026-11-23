"""安全补强：只由用户触发的命令由程序校验（AI 会话里一律拒绝、人在终端里直接运行允许并留记录）、「是不是在 AI 会话里」的判断、
会话的身份来自应用签名的令牌（改环境变量冒充不了）、测试集的构建命令要用户同意、计划做完自动进测试中写一行变更记录。"""
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
from relayforge.core import identity  # noqa: E402
from relayforge.core import trust  # noqa: E402
from relayforge.envs import store as E  # noqa: E402
from relayforge.requirements import testing as G  # noqa: E402
from relayforge.requirements import usecases as U  # noqa: E402
from relayforge.testsets import store as T  # noqa: E402
from relayforge.testsets import edits as TE  # noqa: E402
from relayforge.testsets import runs as TR  # noqa: E402
from tests.app_fixture import FakeApp, app_marker, in_repo_layout  # noqa: E402
from tests.test_testsets import Base, git  # noqa: E402


class Tty(io.StringIO):
    def isatty(self):
        return True


AI_ENVS = [{'CLAUDECODE': '1'}, {'CLAUDE_CODE_ENTRYPOINT': 'cli'}, {'CODEX_THREAD_ID': 'th-1'}, {'CODEX_SANDBOX': 'seatbelt'},
           {'RELAYFORGE_SESSION_ID': 'sess-1'}, {'CLAUDE_PROJECT_DIR': '/p'}]
AI_CHAINS = [[(10, 'python3 rf.py req done R-1'), (9, '/bin/zsh -c ...'), (8, '/Users/me/.local/bin/claude --resume x'), (1, '/sbin/launchd')],
             [(10, 'python3 rf.py'), (9, 'bash'), (8, 'node /usr/local/lib/node_modules/@anthropic-ai/claude-code/cli.js'), (1, 'init')],
             [(10, 'python3 rf.py'), (9, 'bash'), (8, '/opt/homebrew/bin/codex exec'), (1, 'launchd')],
             [(10, 'python3 rf.py'), (9, 'bash'), (7, 'python3 /Applications/RelayForge.app/Contents/Resources/app.asar.unpacked/resources/pty_bridge.py claude'), (1, 'launchd')]]
HUMAN_CHAIN = [(10, 'python3 /home/me/claude-notes/rf.py req done R-1'), (9, '-zsh'), (8, 'login -pf me'), (7, '/System/Applications/Utilities/Terminal.app/Contents/MacOS/Terminal'), (1, '/sbin/launchd')]


def clean_env():
    """The environment without any session variable (a person's own terminal)."""
    return {k: v for k, v in os.environ.items() if k not in trust.AI_ENV_VARS}


class DetectTests(unittest.TestCase):
    def test_session_variables_and_ancestor_processes_mean_an_ai_session(self):
        for extra in AI_ENVS:
            self.assertTrue(trust.in_ai_session(dict(clean_env(), **extra), HUMAN_CHAIN), extra)
        for chain in AI_CHAINS:
            self.assertTrue(trust.in_ai_session(clean_env(), chain), chain)
        self.assertFalse(trust.in_ai_session(clean_env(), HUMAN_CHAIN))  # a folder with 'claude' in its name is not Claude Code
        self.assertFalse(trust.in_ai_session(dict(clean_env(), CLAUDE_CODE_SESSION_ID=''), HUMAN_CHAIN))  # the app clears it for its own calls

    def test_a_terminal_needs_both_ends_on_a_tty_and_no_session(self):
        self.assertTrue(trust.human_terminal(clean_env(), HUMAN_CHAIN, Tty(), Tty()))
        self.assertFalse(trust.human_terminal(clean_env(), HUMAN_CHAIN, io.StringIO(), Tty()))  # piped in
        self.assertFalse(trust.human_terminal(clean_env(), HUMAN_CHAIN, Tty(), io.StringIO()))  # piped out (a script, a hook)
        self.assertFalse(trust.human_terminal(dict(clean_env(), CLAUDECODE='1'), HUMAN_CHAIN, Tty(), Tty()))
        self.assertFalse(trust.human_terminal(clean_env(), AI_CHAINS[0], Tty(), Tty()))  # a pseudo-terminal opened inside a session

    def test_the_real_process_chain_starts_here(self):
        chain = trust.process_chain()
        self.assertEqual(chain[0][0], os.getpid())
        self.assertIn(os.getppid(), [p for p, _ in chain])

    def test_inside_a_sandbox_is_an_ai_session(self):
        with mock.patch.object(trust, 'process_chain', return_value=HUMAN_CHAIN), mock.patch.object(trust, 'sandboxed', return_value=True):
            self.assertIn('沙箱', trust.ai_session_reason(clean_env()))
            self.assertFalse(trust.human_terminal(clean_env(), None, Tty(), Tty()))
        with mock.patch.object(trust, 'process_chain', return_value=HUMAN_CHAIN), mock.patch.object(trust, 'sandboxed', return_value=False):
            self.assertIsNone(trust.ai_session_reason(clean_env()))


# 一个会话能做的伪造：给自己的 shell 写一份登记（登记目录用 RELAYFORGE_HOME 指到自己能写的地方）、自选一个标记，再从这个 shell 运行 rf.py
FORGE = '''
import hashlib, json, os, sys
sys.path.insert(0, sys.argv[1])
from relayforge.core import trust
token = 'forged-marker-0123456789'
apps = trust.apps_dir()
apps.mkdir(parents=True, exist_ok=True)
(apps / f'{os.getppid()}.json').write_text(json.dumps({'token_sha256': hashlib.sha256(token.encode()).hexdigest()}))
os.environ[trust.TOKEN_ENV] = token
print(json.dumps({'sandboxed': trust.sandboxed(), 'trusted': trust.app_trusted()}))
'''


@unittest.skipUnless(sys.platform == 'darwin' and Path('/usr/bin/sandbox-exec').exists(), '只有 macOS 有 Seatbelt 沙箱')
class SandboxTests(unittest.TestCase):
    """应用开的会话里的命令在沙箱里：沙箱里伪造的登记不算数，沙箱外（同一种伪造）算数，说明挡住它的正是沙箱。"""

    def setUp(self):
        if trust.sandboxed():
            self.skipTest('测试本身在沙箱里运行，比不出沙箱内外的差别')
        tmp = tempfile.TemporaryDirectory()  # 不用 enterContext：要支持 Python 3.10
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name).resolve()

    def forge(self, sandbox):
        backend = str(Path(__file__).resolve().parent.parent / 'backend')
        shell = f'{sys.executable} -c "$FORGE" {backend}; exit $?'  # 接 exit：sh 不 exec 成 python，python 的父进程是这个 sh
        argv = (['/usr/bin/sandbox-exec', '-p', '(version 1)(allow default)'] if sandbox else []) + ['/bin/sh', '-c', shell]
        env = dict(os.environ, RELAYFORGE_HOME=str(self.home), FORGE=FORGE)
        env.pop(trust.TOKEN_ENV, None)
        done = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    def report(self, sandbox, **extra):
        """Run report_unsandboxed in a real process (inside the sandbox or not) as a session's rf.py would; the events written."""
        events = self.home / f'events-{sandbox}-{len(extra)}.jsonl'
        code = (f'import sys; sys.path.insert(0, {str(Path(__file__).resolve().parent.parent / "backend")!r}); '
                'from relayforge.project import status; print(status.report_unsandboxed("note"))')
        argv = (['/usr/bin/sandbox-exec', '-p', '(version 1)(allow default)'] if sandbox else []) + [sys.executable, '-c', code]
        env = dict(os.environ, RELAYFORGE_SESSION_ID='sid-1', RELAYFORGE_SESSION_SIG='body.sig', RELAYFORGE_EVENTS=str(events), **extra)
        for name in ('RELAYFORGE_FROM_HOOK', 'RELAYFORGE_PROXIED'):
            if name not in extra:
                env.pop(name, None)
        done = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        lines = events.read_text(encoding='utf-8').splitlines() if events.exists() else []
        return done.stdout.strip(), [json.loads(x)['status'] for x in lines], done.stderr

    def test_a_session_outside_the_sandbox_tells_the_app(self):
        told, events, err = self.report(sandbox=False)
        self.assertEqual((told, events), ('True', ['unsandboxed']))
        self.assertIn('没有在系统的沙箱里运行', err)
        self.assertEqual(self.report(sandbox=True)[:2], ('False', []))  # 沙箱生效了：不报
        self.assertEqual(self.report(sandbox=False, RELAYFORGE_FROM_HOOK='1')[:2], ('False', []))  # 钩子本来就在沙箱外
        self.assertEqual(self.report(sandbox=False, RELAYFORGE_PROXIED='1')[:2], ('False', []))  # 应用替会话执行的

    def test_a_forged_registration_counts_only_outside_the_sandbox(self):
        self.assertEqual(self.forge(sandbox=False), {'sandboxed': False, 'trusted': True})
        self.assertEqual(self.forge(sandbox=True), {'sandboxed': True, 'trusted': False})


class NoTestBackdoorTests(Base):
    """The test seams (trust.parent_pid replaced by a mock inside the test process) are not reachable from outside: a simulated AI session
    that runs a user command from the command line is refused, whatever it puts in its environment."""

    def test_a_simulated_ai_session_is_refused_from_the_command_line_whatever_variables_it_sets(self):
        backend = Path(__file__).resolve().parent.parent / 'backend' / 'rf.py'
        session = dict(CLAUDECODE='1', CLAUDE_CODE_ENTRYPOINT='cli', CLAUDE_CODE_SESSION_ID='x', RELAYFORGE_SESSION_ID='y')
        guesses = [{}, {'RELAYFORGE_TEST': '1'}, {'RELAYFORGE_TESTING': '1'}, {'RELAYFORGE_TEST_PARENT_PID': str(os.getpid())},
                   {'RELAYFORGE_PARENT_PID': str(os.getpid())}, {'PYTHONUNITTEST': '1', 'UNITTEST': '1', 'RELAYFORGE_APP_TOKEN': 'x' * 40}]
        for extra in guesses:
            env = dict(clean_env(), **session, **extra)
            for argv in (['req', 'set', 'R-x', 'build'], ['req', 'start', 'R-x'], ['env', 'reveal', 'ENV-001']):
                with self.subTest(extra=sorted(extra), command=argv[:2]):
                    done = subprocess.run([sys.executable, str(backend)] + argv + ['--root', str(self.root)], env=env, stdin=subprocess.DEVNULL,
                                          capture_output=True, text=True, timeout=120)
                    self.assertEqual(done.returncode, 1, done.stdout)
                    self.assertIn('只能由你在应用里做', done.stderr)
        self.assertEqual(rf.load_requirements(self.root), [])

    def test_the_trust_module_reads_no_testing_switch(self):
        source = (Path(__file__).resolve().parent.parent / 'backend' / 'relayforge' / 'core' / 'trust.py').read_text(encoding='utf-8')
        self.assertNotIn('unittest', source)
        self.assertNotIn('PYTEST', source)
        names = set(__import__('re').findall(r"(?:environ|env)\.get\(([A-Za-z_'\"]+)", source))
        self.assertEqual(names, {'TOKEN_ENV', 'name'})  # the marker, and the session variables looked up by name in AI_ENV_VARS
        self.assertEqual(trust.parent_pid.__module__, 'relayforge.core.trust')


class UserCommandBase(Base):
    def run_cli(self, argv, environ=None, chain=None, tty=False, stdin=''):
        """rf.main as a command line started in `environ` under `chain` (ancestor processes), with or without a terminal."""
        out, err = (Tty() if tty else io.StringIO()), io.StringIO()
        env = mock.patch.dict(os.environ, environ or {}, clear=environ is not None)
        with env, mock.patch.object(trust, 'process_chain', return_value=chain or HUMAN_CHAIN), redirect_stdout(out), redirect_stderr(err), \
                mock.patch('sys.stdin', Tty(stdin) if tty else io.StringIO(stdin)):
            code = rf.main(list(argv) + ['--root', str(self.root)])
        return code, out.getvalue(), err.getvalue()


# 每一条只由用户触发的命令都给一组能被 argparse 接受的参数：校验在命令自己的检查之前，所以参数是否对得上不要紧
ARGS = {
    ('req', 'set'): ['R-x', 'build'], ('req', 'choose'): ['R-x', '--testing-follow'], ('req', 'accept'): ['R-x', '1', 'ok'], ('issue', 'reply'): ['#1', '话'], ('issue', 'reject'): ['#1', '不对'],
    ('test', 'approve'): ['TS-001', '1'], ('test', 'reject'): ['TS-001', '1'], ('env', 'rename'): ['ENV-001', '新名字'], ('env', 'user'): ['ENV-001', 'U1'],
    ('env', 'ai-secrets'): ['ENV-001', 'on'], ('summarize', None): ['R-x'], ('archive', None): ['sweep'], ('test', 'approve-build'): ['TS-001', '--print', 'build:' + '0' * 24],
    ('split', None): ['--session', 's', '--transcript', 'none.jsonl', '--approve', 'x'],
}


def argv_of(key):
    cmd, action = key
    default = {'req': ['R-x'], 'issue': ['#1'], 'test': ['TS-001'], 'env': ['ENV-001'], 'publish': []}.get(cmd, [])
    return [cmd] + ([action] if action else []) + ARGS.get(key, default)


class UserCommandTests(UserCommandBase):
    def test_every_user_command_is_refused_in_an_ai_session(self):
        keys = list(trust.USER_COMMANDS) + list(trust.APP_ONLY)
        self.assertGreaterEqual(len(keys), 45)
        before = git(self.root, 'rev-parse', 'HEAD')
        for key in keys:
            for env, chain in ((dict(clean_env(), CLAUDECODE='1'), HUMAN_CHAIN), (clean_env(), AI_CHAINS[1]), (dict(clean_env(), RELAYFORGE_SESSION_ID='s'), AI_CHAINS[3])):
                with self.subTest(command=' '.join(argv_of(key)), env=sorted(set(env) - set(clean_env())), chain=chain[2][1][:30]):
                    code, out, err = self.run_cli(argv_of(key), env, chain, tty=True)  # even with a (pseudo-)terminal
                    self.assertEqual(code, 1, out)
                    self.assertIn('只能由你在应用里做', err)
                    self.assertIn('AI 的会话里运行的', err)
        self.assertEqual(git(self.root, 'rev-parse', 'HEAD'), before)  # nothing was committed
        self.assertEqual(rf.load_requirements(self.root), [])
        self.assertFalse((self.root / rf.DIR / 'envs').exists() and list((self.root / rf.DIR / 'envs').glob('ENV-*.json')))

    def test_without_a_terminal_and_without_the_marker_it_is_refused_too(self):
        for key in list(trust.USER_COMMANDS) + list(trust.APP_ONLY):
            with self.subTest(key=key):
                code, _, err = self.run_cli(argv_of(key), clean_env(), HUMAN_CHAIN, tty=False)  # a script or a git hook
                self.assertEqual(code, 1)
                self.assertIn('只能由你在应用里做', err)

    def test_the_lists_cover_agents_md_and_the_deny_rules(self):
        from relayforge.project.init import conventions_text
        agents = conventions_text()  # 交给会话的约定（数据外置以后不再写进仓库的 AGENTS.md）
        for cmd, actions in (('req', ['start', 'done', 'drop', 'set']), ('issue', ['confirm', 'unconfirm', 'reject', 'probe', 'fix', 'set', 'reply']),
                             ('test', ['approve', 'reject', 'stale', 'revive', 'move', 'save', 'seen']), ('publish', ['run', 'pull']),
                             ('env', ['new', 'copy', 'rename', 'rm', 'save', 'use', 'user', 'reveal', 'adopt', 'ignore', 'approve', 'ai-secrets'])):
            for a in actions:
                self.assertIsNotNone(trust.command_key(cmd, a), f'{cmd} {a}')
        self.assertIn('rf.py summarize 和 rf.py archive', agents)
        self.assertIsNotNone(trust.command_key('summarize'))
        self.assertIsNotNone(trust.command_key('test', 'config'))
        # 刻意不校验的：rf-confirm 技能在你明确表态以后由 AI 执行的确认 / 否决
        self.assertIsNone(trust.command_key('confirm'))
        self.assertIsNone(trust.command_key('reject'))
        for key in (('req', 'new'), ('req', 'retest'), ('test', 'run'), ('test', 'new'), ('issue', 'new'), ('issue', 'handback'), ('issue', 'finding'), ('env', 'propose')):
            self.assertIsNone(trust.command_key(*key), key)  # the sessions' own commands stay open

    def test_a_person_in_a_terminal_may_and_the_change_log_says_so(self):
        req = rf.req_new(self.root, '终端里立项')
        path = self.root / req['file']
        meta, sections = rf.read_doc(path)
        sections['验收条件'] = ['- 能用〔S-x#1〕']
        rf.write_doc(path, meta, sections)
        code, out, err = self.run_cli(['req', 'start', req['id']], clean_env(), HUMAN_CHAIN, tty=True)
        self.assertEqual(code, 0, err)
        found = rf.find_requirement(self.root, req['id'])
        self.assertEqual(found['state'], 'open')
        lines = rf.read_doc(self.root / found['file'])[1]['变更记录']
        self.assertTrue(any('用户确认立项' in l and '（用户在终端里执行）' in l for l in lines), lines)
        # 同一条命令从应用里点：照常执行，不加这句
        code, _, err = self.run_cli(['req', 'set', req['id'], 'check'], clean_env(), HUMAN_CHAIN, tty=True)
        self.assertEqual(code, 0, err)
        last = rf.read_doc(self.root / found['file'])[1]['变更记录'][-1]
        self.assertIn('（用户在终端里执行）', last)
        with app_marker():
            code, _, err = self.run_cli(['req', 'set', req['id'], 'build'], None, HUMAN_CHAIN)
        self.assertEqual(code, 0, err)
        last = rf.read_doc(self.root / found['file'])[1]['变更记录'][-1]
        self.assertNotIn('终端', last)
        # 测试集、测试环境的变更记录也一样
        code, _, err = self.run_cli(['env', 'new', '本机'], clean_env(), HUMAN_CHAIN, tty=True)
        self.assertEqual(code, 0, err)
        env = json.loads((self.root / rf.DIR / 'envs' / 'ENV-001.json').read_text(encoding='utf-8'))
        self.assertIn('（用户在终端里执行）', env['log'][-1])

    def pty_run(self, env):
        """rf.py started for real (a subprocess) with both ends on a pseudo-terminal, in `env`: (exit code, stderr, what it printed)."""
        import select
        master, slave = os.openpty()
        backend = Path(__file__).resolve().parent.parent / 'backend' / 'rf.py'
        try:
            done = subprocess.run([sys.executable, str(backend), 'env', 'new', '终端里建的', '--root', str(self.root)], stdin=slave, stdout=slave,
                                  stderr=subprocess.PIPE, env=env, timeout=120, text=True)
        finally:
            os.close(slave)
        out = b''
        while select.select([master], [], [], 0.2)[0]:
            try:
                chunk = os.read(master, 4096)
            except OSError:
                break
            if not chunk:
                break
            out += chunk
        os.close(master)
        return done.returncode, done.stderr, out

    def test_a_real_pseudo_terminal_is_judged_by_the_real_process_chain(self):
        """rf.py started for real with both ends on a pseudo-terminal: refused when the session variables say it is in an AI session
        (always, wherever this test runs); with no variables, allowed only when none of the ancestor processes is an AI session."""
        code, err, out = self.pty_run(dict(clean_env(), CLAUDECODE='1'))
        self.assertEqual(code, 1, out)
        self.assertIn('只能由你在应用里做', err)
        self.assertIn('环境里有会话的变量 CLAUDECODE', err)
        env = clean_env()
        code, err, out = self.pty_run(env)
        if trust.in_ai_session(env):  # the tests run under an AI session: so does the command they started
            self.assertEqual(code, 1, out)
            self.assertIn('只能由你在应用里做', err)
            self.assertIn('上层进程', err)
        else:  # a person's own terminal
            self.assertEqual(code, 0, err)
            log = json.loads((self.root / rf.DIR / 'envs' / 'ENV-001.json').read_text(encoding='utf-8'))['log']
            self.assertIn('（用户在终端里执行）', log[-1])

    def test_the_app_marker_is_enough_and_a_wrong_one_is_not(self):
        with app_marker():
            code, _, err = self.run_cli(['env', 'new', '本机'], dict(os.environ, CLAUDECODE=''), HUMAN_CHAIN)
        self.assertEqual(code, 0, err)
        with mock.patch.dict(os.environ, {trust.TOKEN_ENV: 'x' * 40}):
            code, _, err = self.run_cli(['env', 'rm', 'ENV-001'], dict(os.environ), HUMAN_CHAIN)
        self.assertEqual(code, 1)
        self.assertIn('只能由你在应用里做', err)

    def test_app_only_commands_are_refused_in_a_terminal(self):
        for key in trust.APP_ONLY:
            with self.subTest(key=key):
                code, _, err = self.run_cli(argv_of(key), clean_env(), HUMAN_CHAIN, tty=True)
                self.assertEqual(code, 1)
                self.assertIn('只能由你在应用里做', err)

    def test_the_function_level_checks_do_not_trust_a_terminal_by_themselves(self):
        req, _ = self.requirement()
        with mock.patch.object(trust, 'process_chain', return_value=HUMAN_CHAIN), mock.patch.dict(os.environ, clean_env(), clear=True), \
                mock.patch('sys.stdin', Tty()), mock.patch('sys.stdout', Tty()):
            with self.assertRaisesRegex(ValueError, '只能由你在应用里做'):
                G.req_to_test(self.root, req)  # only the command line decides that a person typed it


class IdentityTests(Base):
    def setUp(self):
        super().setUp()
        self.req, self.place = self.requirement()
        rf.req_attach(self.root, self.req, 'dev-1')
        rf.req_attach(self.root, self.req, 'test-1', test=True, trusted=True)
        self.app = FakeApp()
        self.addCleanup(self.app.close)

    def kind(self):
        return G.session_kind(rf.read_doc(self.root / rf.find_requirement(self.root, self.req)['file'])[0])

    def test_kinds_come_from_the_signed_token(self):
        with self.app.session('test', self.req, 'test-1'):
            self.assertEqual(self.kind(), 'test')
            self.assertTrue(identity.caller()['verified'])
        with self.app.session('develop', self.req, 'dev-1'):
            self.assertEqual(self.kind(), 'develop')
        with self.app.session('develop', 'R-other', 'dev-9'):
            self.assertEqual(self.kind(), 'chat')  # another requirement's session
        with self.app.session('chat', None, 'chat-1'):
            self.assertEqual(self.kind(), 'chat')

    def test_rewriting_the_session_id_does_not_make_a_test_session(self):
        with self.app.session('develop', self.req, 'dev-1'):
            with mock.patch.dict(os.environ, {'RELAYFORGE_SESSION_ID': 'test-1', 'CLAUDE_CODE_SESSION_ID': 'test-1'}):
                identity.forget()
                self.assertEqual(self.kind(), 'develop')
                with self.assertRaisesRegex(ValueError, '只有这个需求的测试会话能做'):
                    G.req_steps(self.place, 1, '打开', '做', '看到')
                made = U.req_classify(self.place, ['1'], '理由', '数据')  # 开发会话改归类只能提给用户
                self.assertEqual(made['classified'], [])
        with mock.patch.dict(os.environ, {'RELAYFORGE_SESSION_ID': 'test-1'}):  # no token at all: an old session
            os.environ.pop(identity.SIG_ENV, None)
            identity.forget()
            self.assertEqual(self.kind(), 'chat')
            with self.assertRaisesRegex(ValueError, '重新打开这个会话'):
                U.req_classify(self.place, ['1'], '理由', '数据')

    def test_forged_tampered_foreign_and_unbound_tokens_are_the_lowest(self):
        good = self.app.issue('test-1', 'develop', self.req)
        body, sig = good.split('.')
        claims = identity.claims_of(good)
        claims['kind'] = 'test'
        import base64
        forged = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip('=') + '.' + sig
        other = FakeApp(pid=999999)  # another app, another key
        self.addCleanup(other.close)
        foreign = other.issue('test-1', 'test', self.req)
        unbound = self.app.issue('test-1', 'test', self.req, bind=999999)  # issued to another process, not an ancestor of this one
        for token, why in ((forged, '没有认'), (foreign, '没有认'), (unbound, '不是发给这个会话的'), ('garbage', '格式不对'), ('', '没有会话令牌')):
            with mock.patch.dict(os.environ, {identity.SIG_ENV: token}):
                identity.forget()
                me = identity.caller()
                self.assertEqual((me['kind'], me['verified']), ('chat', False), token[:20])
                self.assertIn(why, me['why'])
                self.assertEqual(self.kind(), 'chat')

    def test_without_the_app_running_every_token_is_the_lowest(self):
        token = self.app.issue('test-1', 'test', self.req)
        self.app.close()
        (trust.apps_dir() / f'{os.getpid()}.json').unlink()
        with mock.patch.dict(os.environ, {identity.SIG_ENV: token}):
            identity.forget()
            self.assertIn('找不到开这个会话的应用', identity.caller()['why'])

    def test_a_development_session_still_cannot_quietly_change_test_session_data(self):
        rf.note(self.place, 'dev-1', None, 'plan', ['写代码'])
        rf.note(self.place, 'dev-1', None, 'step', ['1', 'done'])  # 计划做完，进测试中
        with self.app.session('test', self.req, 'test-1'):
            ts = TE.testset_new(self.place, '加法', 'RF-101', 'tests/rf_add.py')
            TE.testset_add(self.place, ts['id'], [{'name': '正数', 'input': {'a': 1, 'b': 2}, 'expect': 3}])
        with self.app.session('develop', self.req, 'dev-1'), mock.patch.dict(os.environ, {'RELAYFORGE_SESSION_ID': 'test-1'}):
            identity.forget()
            out = TE.testset_edit(self.place, ts['id'], 1, {'expect': 4}, why='改一下')
        self.assertTrue(out['pending'])  # 要你确认，冒充不了测试会话
        with self.app.session('test', self.req, 'test-1'):
            out = TE.testset_edit(self.place, ts['id'], 1, {'expect': 5})
        self.assertFalse(out['pending'])  # 测试会话自己改不受限

    def test_retest_needs_the_development_session(self):
        rf.note(self.place, 'dev-1', None, 'plan', ['写代码'])
        rf.note(self.place, 'dev-1', None, 'step', ['1', 'done'])
        with self.app.session('test', self.req, 'test-1'):
            U.req_classify(self.place, ['1'], '程序能比对', '数据')
            G.req_verdict(self.place, 'problems', '', ['第 1 条｜不对｜改坏了'])
        with self.app.session('chat', None, 'chat-1'), mock.patch.object(trust, 'human_terminal', return_value=False):
            with self.assertRaisesRegex(ValueError, '不是这个需求的开发会话'):
                G.req_retest(self.place, '修了')
        with self.app.session('develop', self.req, 'dev-1'):
            G.req_retest(self.place, '修了')
        self.assertEqual(rf.find_requirement(self.root, self.req)['stage'], 'test')


class BuildConsentTests(UserCommandBase):
    def setUp(self):
        super().setUp()
        (self.root / 'src').mkdir()
        (self.root / 'src' / 'One.java').write_text('public class One { public static Object run(Object i) { return 3; } }\n', encoding='utf-8')
        ts = TE.testset_new(self.root, '构建', 'RF-101', 'src/One.java', classpath=['.'], build='touch built.txt')
        TE.testset_add(self.root, ts['id'], [{'name': '一条', 'input': None, 'expect': 3}])
        self.ts = ts['id']

    def run_set(self):
        return TR.testset_run(self.root, [self.ts])['sets'][0]

    def test_an_unagreed_build_is_not_run_and_says_why(self):
        s = self.run_set()
        self.assertEqual(s['result'], 'broken')
        self.assertIn('构建命令还没经你同意', s['error'])
        self.assertIn('touch built.txt', s['error'])
        self.assertFalse((self.root / 'built.txt').exists())
        # 桥接文件（项目自己的测试命令）也一样不执行
        out = TR.bridge_run(self.root, self.ts)
        self.assertEqual(out['result'], 'broken')
        self.assertFalse((self.root / 'built.txt').exists())
        # 应用先问：preflight 列出它，交给确认框（和准备步骤一样的样子）
        pre = TR.testset_run(self.root, [self.ts], preflight=True)
        item = pre['blocked'][0]
        self.assertEqual((item['kind'], item['set'], item['command'], item['abs_dir']), ('build', self.ts, 'touch built.txt', str(self.root.resolve())))

    def test_only_the_app_can_agree_and_only_to_what_was_shown(self):
        fp = TR.build_print(T.read_set(self.root, self.ts), self.root, self.root)
        code, _, err = self.run_cli(['test', 'approve-build', self.ts, '--print', fp], dict(clean_env(), CLAUDECODE='1'), AI_CHAINS[0], tty=True)
        self.assertEqual(code, 1)
        self.assertIn('只能由你在应用里做', err)
        code, _, err = self.run_cli(['test', 'approve-build', self.ts, '--print', fp], clean_env(), HUMAN_CHAIN, tty=True)
        self.assertEqual(code, 1)  # 终端也不行：要在应用里看过命令
        with self.assertRaisesRegex(ValueError, '只能由你在应用里做'):
            TR.testset_approve_build(self.root, self.ts, fp)
        with app_marker():
            with self.assertRaisesRegex(ValueError, '又变了'):
                TR.testset_approve_build(self.root, self.ts, 'build:' + '1' * 24)
            code, out, err = self.run_cli(['test', 'approve-build', self.ts, '--print', fp, '--json'], None, HUMAN_CHAIN)
        self.assertEqual(code, 0, err)
        if not self.java():
            return
        self.assertEqual(self.run_set()['result'], 'pass')
        self.assertTrue((self.root / 'built.txt').exists())
        # 命令改了：再要同意
        with app_marker():
            TE.testset_config(self.root, self.ts, {'build': 'touch built2.txt'})
        s = self.run_set()
        self.assertEqual(s['result'], 'broken')
        self.assertFalse((self.root / 'built2.txt').exists())

    def test_the_consent_is_by_command_and_directory(self):
        data = T.read_set(self.root, self.ts)
        req, place = self.requirement()
        self.assertEqual(TR.build_print(data, self.root, self.root), TR.build_print(data, self.root, place))  # this project's working directories count the same
        elsewhere = Path(self.tmp.name) / 'elsewhere'
        elsewhere.mkdir()
        self.assertNotEqual(TR.build_print(data, self.root, self.root), TR.build_print(data, self.root, elsewhere))
        self.assertNotEqual(TR.build_print(data, self.root, self.root), TR.build_print(dict(data, build='touch x'), self.root, self.root))
        with app_marker():
            TR.testset_approve_build(self.root, self.ts, TR.build_print(data, self.root, self.root))
        self.assertIsNone(TR.build_block(self.root, data, place))
        self.assertIn('还没经你同意', TR.build_block(self.root, data, elsewhere))
        local = json.loads(E.consent_path(self.root).read_text(encoding='utf-8'))  # 同意记在「你的决定」那个文件里
        self.assertEqual(next(v for k, v in local['approved'].items() if k.startswith('build:'))['command'], 'touch built.txt')
        self.assertEqual(oct(E.consent_path(self.root).stat().st_mode & 0o777), '0o600')

    def java(self):
        import shutil
        return bool(shutil.which('java') and shutil.which('javac'))


class AutoTestRecordTests(Base):
    def setUp(self):
        super().setUp()
        self.req, self.place = self.requirement()
        rf.req_attach(self.root, self.req, 'dev-1')

    def lines(self):
        return [l for l in rf.read_doc(self.root / rf.find_requirement(self.root, self.req)['file'])[1]['变更记录'] if '做完了计划' in l]

    @in_repo_layout
    def test_finishing_the_plan_is_written_once(self):
        rf.note(self.place, 'dev-1', None, 'plan', ['写代码', '跑一遍'])
        rf.note(self.place, 'dev-1', None, 'step', ['1', 'done'])
        self.assertEqual(self.lines(), [])
        commits = int(git(self.root, 'rev-list', '--count', 'HEAD'))
        rf.note(self.place, 'dev-1', None, 'step', ['2', 'done'])
        found = rf.find_requirement(self.root, self.req)
        self.assertEqual(found['stage'], 'test')
        meta = rf.read_doc(self.root / found['file'])[0]
        self.assertEqual(meta['manual_stage'], 'test')  # 之后以记录为准
        self.assertEqual(len(self.lines()), 1)
        self.assertRegex(self.lines()[0], r'^- \d{4}-\d\d-\d\d \d\d:\d\d 开发会话做完了计划（2 步），进入测试中。$')
        self.assertEqual(int(git(self.root, 'rev-list', '--count', 'HEAD')), commits + 1)
        rf.note(self.place, 'dev-1', None, 'step', ['2', 'done'])  # 再记一次：不重复
        rf.note(self.place, 'dev-1', None, 'doing', ['收尾'])
        self.assertEqual(len(self.lines()), 1)
        self.assertEqual(int(git(self.root, 'rev-list', '--count', 'HEAD')), commits + 1)
        rf.req_set(self.root, self.req, 'build')  # 你把它拖回进行中：计划还是做完的，但以记录为准，不再自动进测试
        rf.note(self.place, 'dev-1', None, 'step', ['1', 'done'])
        self.assertEqual(rf.find_requirement(self.root, self.req)['stage'], 'build')
        self.assertEqual(len(self.lines()), 1)

    def test_old_requirements_without_the_test_flow_are_left_alone(self):
        path = self.root / rf.find_requirement(self.root, self.req)['file']
        meta, sections = rf.read_doc(path)
        meta.pop('test_flow', None)
        rf.write_doc(path, meta, sections)
        rf.note(self.place, 'dev-1', None, 'plan', ['写代码'])
        rf.note(self.place, 'dev-1', None, 'step', ['1', 'done'])
        self.assertEqual(rf.find_requirement(self.root, self.req)['stage'], 'check')
        self.assertEqual(self.lines(), [])
        self.assertNotIn('manual_stage', rf.read_doc(path)[0])


if __name__ == '__main__':
    unittest.main()


class HomeFolderTests(unittest.TestCase):
    def test_the_local_records_folder_is_tightened_to_this_user_only(self):
        from relayforge.core import common
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp).resolve() / '.relayforge'
            (home / 'projects').mkdir(parents=True)
            home.chmod(0o755)  # 以前建出来的样子：别的本地用户能进去读项目记录
            with mock.patch.dict(os.environ, {'RELAYFORGE_HOME': str(home)}):
                common.secure_home()
            self.assertEqual(home.stat().st_mode & 0o777, 0o700)


class ProxyErrorTests(unittest.TestCase):
    def test_a_sandbox_that_blocks_the_app_socket_is_named_not_an_old_app(self):
        from relayforge.core import proxy

        class Blocked:  # 沙箱拦下连接时 connect 抛 PermissionError（EPERM）
            def __init__(self, *a):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def settimeout(self, t):
                pass

            def connect(self, path):
                raise PermissionError(1, 'Operation not permitted')
        with mock.patch.object(proxy.socket, 'socket', Blocked):
            why = proxy._no_answer('/x/app.sock')
        self.assertIn('沙箱不让连应用的 socket', why)
        self.assertIn('不要申请在沙箱外执行命令', why)
        with tempfile.TemporaryDirectory() as tmp:  # 没有人在听：应用退出了
            self.assertIn('应用可能已经退出', proxy._no_answer(str(Path(tmp).resolve() / 'gone.sock')))

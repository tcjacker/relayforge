"""引入 Agent 做衔接（后端）：测试环境的准备步骤（按需执行、第一次要用户同意、失败算跑不起来）、应用的标记（AI 的会话默认不带保密值、
不能替用户同意）、「让 AI 来处理」交过去的话（有错误和输出尾部、没有任何保密值）、AI 交回的准备步骤和测试集设置、测试集的 Java 设置。"""
import json
import os
import secrets
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'backend'))
import rf  # noqa: E402
from relayforge.envs import store as E  # noqa: E402
from relayforge.envs import edits as EE  # noqa: E402
from relayforge.envs import prep as EP  # noqa: E402
from relayforge.envs import values as EVal  # noqa: E402
from relayforge.envs import views as EV  # noqa: E402
from relayforge.envs import handoff as EH  # noqa: E402
from relayforge.testsets import store as T  # noqa: E402
from relayforge.testsets import edits as TE  # noqa: E402
from relayforge.testsets import views as TV  # noqa: E402
from relayforge.testsets import runs as TR  # noqa: E402
from relayforge.core import trust  # noqa: E402
from tests.app_fixture import app_marker, start_app_marker  # noqa: E402
from tests.test_envs import ENV_ENTRY, SECRETS, Base, git  # noqa: E402

RF = str(Path(__file__).resolve().parent.parent / 'backend' / 'rf.py')


class BridgeBase(Base):
    def setUp(self):
        super().setUp()
        base = self.root / rf.DIR
        rf.write_doc(base / 'modules' / 'M-01-账单.md', {'id': 'M-01', 'type': 'module', 'title': '账单', 'status': 'current'}, {'负责什么': [], '变更记录': []})
        rf.write_doc(base / 'features' / 'RF-101-加法.md', {'id': 'RF-101', 'type': 'feature', 'title': '加法', 'status': 'current', 'module': 'M-01', 'depends_on': []},
                     {h: [] for h in rf.FEATURE_HEADS})
        (self.root / 'tests').mkdir()
        (self.root / 'tests' / 'rf_env.py').write_text(ENV_ENTRY, encoding='utf-8')
        (self.root / 'pom.xml').write_text('<project/>\n', encoding='utf-8')
        git(self.root, 'add', '-A')
        git(self.root, 'commit', '-qm', 'entry')
        ts = TE.testset_new(self.root, '读环境变量', 'RF-101', 'tests/rf_env.py')
        TE.testset_add(self.root, ts['id'], [{'name': '密码', 'input': {'name': 'DB_PASSWORD'}, 'expect': SECRETS[0]},
                                            {'name': '地址', 'input': {'name': 'PAY_API'}, 'expect': 'http://localhost:9090'}])
        self.ts = ts['id']
        EE.env_new(self.root, '本机')
        EE.env_save(self.root, 'ENV-001', self.payload())

    def as_app(self):
        """This process acts as the app (a real marker; in-process the parent is replaced, see tests/app_fixture.py)."""
        return start_app_marker(self)

    def steps(self, *steps):
        EE.env_save(self.root, 'ENV-001', dict(self.payload(), prep=list(steps)))
        return E.find_env(self.root, 'ENV-001')['prep']

    def local_text(self):
        return E.local_path(self.root).read_text(encoding='utf-8')


class TrustTests(BridgeBase):
    def test_marker_needs_the_token_and_the_app_as_parent(self):
        self.assertFalse(trust.app_trusted())
        token = secrets.token_hex(16)
        trust.register(os.getpid(), token)
        env = dict(os.environ, **{trust.TOKEN_ENV: token})
        cmd = [sys.executable, RF, 'env', 'reveal', 'ENV-001', '--json', '--root', str(self.root)]
        done = subprocess.run(cmd, env=env, capture_output=True, text=True)  # 父进程是登记过的「应用」
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout)['vars'][0]['value'], SECRETS[0])
        wrong = subprocess.run(cmd, env=dict(env, **{trust.TOKEN_ENV: 'x' * 32}), capture_output=True, text=True)
        self.assertEqual(wrong.returncode, 1)
        self.assertIn('只能由你在应用里做', wrong.stderr)
        # AI 的 shell 带着偷来的标记也不行。后面接 `; exit $?`：只有一条命令时 sh 会直接 exec 成 rf.py，父进程又变回了登记过的「应用」
        shell = subprocess.run(['sh', '-c', ' '.join(f"'{c}'" for c in cmd) + '; exit $?'], env=env, capture_output=True, text=True)
        self.assertEqual(shell.returncode, 1, shell.stdout)
        self.assertNotIn(SECRETS[0], shell.stdout + shell.stderr)
        none = subprocess.run(cmd, env={k: v for k, v in env.items() if k != trust.TOKEN_ENV}, capture_output=True, text=True)
        self.assertEqual(none.returncode, 1)
        registered = (trust.apps_dir() / f'{os.getpid()}.json').read_text(encoding='utf-8')
        self.assertNotIn(token, registered)  # 登记文件里只有散列
        self.assertEqual(oct((trust.apps_dir() / f'{os.getpid()}.json').stat().st_mode & 0o777), '0o600')

    def test_marker_is_never_passed_to_the_code_under_test(self):
        self.as_app()
        (self.root / 'tests' / 'rf_env.py').write_text(ENV_ENTRY, encoding='utf-8')
        TE.testset_add(self.root, self.ts, [{'name': '标记', 'input': {'name': trust.TOKEN_ENV}, 'expect': None}])
        self.steps({'note': '看标记', 'command': f'echo "[${trust.TOKEN_ENV}]" > marker.txt'})
        EP.env_approve(self.root, 'ENV-001')
        out = TR.testset_run(self.root, [self.ts])
        self.assertEqual(out['sets'][0]['counts']['pass'], 3, out['sets'][0])
        self.assertEqual((self.root / 'marker.txt').read_text().strip(), '[]')


class SecretsForAiTests(BridgeBase):
    def test_ai_runs_do_not_get_secret_values_unless_allowed(self):
        out = TR.testset_run(self.root, [self.ts])  # 没有应用的标记：按 AI 的会话
        s = out['sets'][0]
        self.assertEqual(out['secrets_withheld'], ['DB_PASSWORD'])
        self.assertEqual([c['status'] for c in s['cases']], ['fail', 'pass'])
        self.assertIsNone(s['cases'][0]['actual'])
        self.assertIn('这次没有带保密值（DB_PASSWORD）', T.run_text(out))
        out = TR.testset_run(self.root, [self.ts], trusted=True)  # 用户在应用里点的
        self.assertEqual(out['sets'][0]['counts']['pass'], 2)
        self.assertEqual(out['secrets_withheld'], [])
        with self.assertRaisesRegex(ValueError, '只能由你在应用里做'):
            EP.env_ai_secrets(self.root, 'ENV-001', True)
        code, _, err = self.rf('env', 'ai-secrets', 'ENV-001', 'on')
        self.assertEqual(code, 1)
        self.as_app()
        code, _, err = self.rf('env', 'ai-secrets', 'ENV-001', 'on')
        self.assertEqual(code, 0, err)
        self.assertTrue(EV.env_show(self.root, 'ENV-001')['ai_secrets'])
        self.assertEqual(TR.testset_run(self.root, [self.ts], trusted=False)['sets'][0]['counts']['pass'], 2)
        EP.env_ai_secrets(self.root, 'ENV-001', False)
        self.assertFalse(EV.env_show(self.root, 'ENV-001')['ai_secrets'])
        self.assertNotIn('ai_secrets', E.env_path(self.root, 'ENV-001').read_text(encoding='utf-8'))  # 只在本机

    def test_broken_without_secrets_says_why(self):
        (self.root / 'tests' / 'rf_need.py').write_text('import os\nif not os.environ.get("DB_PASSWORD"):\n    raise RuntimeError("连不上数据库：没有口令")\ndef run(input):\n    return 1\n', encoding='utf-8')
        ts = TE.testset_new(self.root, '要口令', 'RF-101', 'tests/rf_need.py')
        TE.testset_add(self.root, ts['id'], [{'name': 'x', 'input': {}, 'expect': 1}])
        out = TR.testset_run(self.root, [ts['id']], trusted=False)
        self.assertEqual(out['sets'][0]['result'], 'broken')
        self.assertIn('这次没有带保密值（DB_PASSWORD）：在应用里运行，或在测试环境里勾选', out['sets'][0]['error'])
        self.assertEqual(TR.testset_run(self.root, [ts['id']], trusted=True)['sets'][0]['result'], 'pass')


class PrepTests(BridgeBase):
    def test_steps_are_stored_in_the_environment_file_with_ids(self):
        steps = self.steps({'note': '装依赖', 'command': 'npm ci', 'dir': 'web', 'when': 'changed', 'watch': 'web/package-lock.json\nweb/package.json', 'timeout': '300'},
                           {'command': 'mvn -q compile'})
        self.assertEqual(steps, [{'id': 'P1', 'note': '装依赖', 'command': 'npm ci', 'dir': 'web', 'when': 'changed', 'watch': ['web/package-lock.json', 'web/package.json'], 'timeout': 300},
                                 {'id': 'P2', 'note': 'mvn -q compile', 'command': 'mvn -q compile', 'dir': '', 'when': 'always', 'watch': [], 'timeout': 600}])
        self.assertIn('准备步骤', E.find_env(self.root, 'ENV-001')['log'][-1])
        steps = self.steps(steps[1])  # 删掉第一条：编号不再用
        steps = self.steps(steps[0], {'command': 'make'})
        self.assertEqual([s['id'] for s in steps], ['P2', 'P3'])
        for bad, msg in (({'command': 'x', 'when': 'changed'}, '要写看哪些文件'), ({'command': 'x', 'when': 'sometimes'}, '只能是 always'),
                         ({'command': 'x', 'dir': '/etc'}, '相对路径'), ({'command': 'x', 'dir': '../up'}, '相对路径'), ({'command': 'x', 'timeout': 99999}, '1 到 3600'),
                         ({'note': '只有说明'}, '没有命令')):
            with self.assertRaisesRegex(ValueError, msg):
                self.steps(bad)
        EE.env_save(self.root, 'ENV-001', self.payload())  # 不带 prep 的旧载荷：步骤保留
        self.assertEqual(len(E.find_env(self.root, 'ENV-001')['prep']), 2)

    def test_first_run_needs_the_users_consent_then_runs_when_due(self):
        self.steps({'note': '记一笔', 'command': 'echo run >> prep.log && cat lock.txt', 'when': 'changed', 'watch': ['lock.txt']},
                   {'note': '每次都跑', 'command': 'echo always >> always.log'})
        (self.root / 'lock.txt').write_text('v1', encoding='utf-8')
        pre = TR.testset_run(self.root, [self.ts], preflight=True)
        self.assertEqual([b['id'] for b in pre['blocked']], ['P1', 'P2'])
        self.assertEqual(pre['blocked'][0]['abs_dir'], str(self.root.resolve()))
        self.assertFalse((self.root / 'prep.log').exists())  # 预检不执行
        out = TR.testset_run(self.root, [self.ts], trusted=True)  # 用户点了运行，但还没同意这条命令
        s = out['sets'][0]
        self.assertEqual(s['result'], 'broken')
        self.assertIn('准备步骤 1「记一笔」还没经你同意', s['error'])
        self.assertIn('echo run >> prep.log', s['error'])
        self.assertFalse((self.root / 'prep.log').exists())
        with self.assertRaisesRegex(ValueError, '只能由你在应用里做'):
            EP.env_approve(self.root, 'ENV-001')  # AI 的会话不能替用户同意
        code, _, err = self.rf('env', 'approve', 'ENV-001')
        self.assertEqual(code, 1)
        self.as_app()
        code, out_text, err = self.rf('env', 'approve', 'ENV-001', 'P1', 'P2', '--json')
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out_text)['approved'], ['P1', 'P2'])
        out = TR.testset_run(self.root, [self.ts])
        self.assertEqual(out['sets'][0]['counts']['pass'], 2, out)
        self.assertEqual([r['id'] for r in out['prep']['ran']], ['P1', 'P2'])
        self.assertEqual((self.root / 'prep.log').read_text().count('run'), 1)
        out = TR.testset_run(self.root, [self.ts])  # lock.txt 没变：第一条不再执行，第二条每次都执行
        self.assertEqual([r['id'] for r in out['prep']['ran']], ['P2'])
        self.assertEqual(out['prep']['skipped'][0]['why'], 'lock.txt 没变，不用再执行')
        (self.root / 'lock.txt').write_text('v2', encoding='utf-8')
        out = TR.testset_run(self.root, [self.ts])
        self.assertEqual([r['id'] for r in out['prep']['ran']], ['P1', 'P2'])
        self.assertEqual((self.root / 'always.log').read_text().count('always'), 3)
        # 命令改了：又要同意
        env = E.find_env(self.root, 'ENV-001')
        self.steps(dict(env['prep'][0], command='echo changed >> prep.log'), env['prep'][1])
        self.assertEqual([b['id'] for b in TR.testset_run(self.root, [self.ts], preflight=True)['blocked']], ['P1'])
        # 结果和输出尾部只在本机
        view = EV.env_show(self.root, 'ENV-001')['prep']
        self.assertTrue(view[1]['last']['ok'])
        self.assertIn('v2', view[0]['last']['tail'])
        self.assertNotIn('prep_runs', E.env_path(self.root, 'ENV-001').read_text(encoding='utf-8'))
        self.assertNotIn('approved', E.env_path(self.root, 'ENV-001').read_text(encoding='utf-8'))

    def test_a_failing_step_makes_the_run_broken_and_hides_secrets(self):
        self.as_app()
        self.steps({'note': '装依赖', 'command': f'echo "connecting with $DB_PASSWORD"; echo boom 1>&2; exit 3'})
        EP.env_approve(self.root, 'ENV-001')
        out = TR.testset_run(self.root, [self.ts])
        s = out['sets'][0]
        self.assertEqual(s['result'], 'broken')
        self.assertEqual(s['counts']['broken'], 2)
        self.assertIn('准备步骤 1「装依赖」失败了（退出码 3）', s['error'])
        self.assertIn('boom', s['error'])
        self.assertIn('connecting with ••••••', s['error'])
        self.assertIn('准备步骤 1「装依赖」失败了', T.run_text(out))
        prep_part = json.dumps(E.read_local(self.root)['prep_runs'], ensure_ascii=False)
        self.assertNotIn(SECRETS[0], prep_part)
        self.assertIn('connecting with ••••••', prep_part)
        for p in list((self.root / rf.DIR / 'tests' / 'results').rglob('*.json')) + list((self.root / rf.DIR / 'envs').rglob('*.json')):
            self.assertNotIn(SECRETS[0], p.read_text(encoding='utf-8'), p)  # （测试数据本身的期望里有这个值，那是数据，不算）
        # AI 的会话里：不带保密值，错误里说明
        out = TR.testset_run(self.root, [self.ts], trusted=False)
        self.assertIn('connecting with \n', out['sets'][0]['error'] + '\n')
        self.assertIn('这次没有带保密值（DB_PASSWORD）', out['sets'][0]['error'])

    def test_manual_run_and_timeouts(self):
        self.steps({'note': '慢', 'command': 'sleep 5', 'timeout': 1}, {'note': '快', 'command': 'echo hi'})
        code, text, err = self.rf('env', 'prep-run', 'ENV-001')
        self.assertEqual(code, 1)
        self.assertIn('还没经你同意', text)
        self.as_app()
        EP.env_approve(self.root, 'ENV-001')
        code, text, err = self.rf('env', 'prep-run', 'ENV-001', 'P2')
        self.assertEqual(code, 0, err)
        self.assertIn('快：执行完了', text)
        out = EP.env_prep_run(self.root, 'ENV-001')
        self.assertIn('超过 1 秒没有执行完', out['error'])
        self.assertEqual(out['ran'][0]['code'], None)
        code, text, _ = self.rf('env', 'prep', 'ENV-001')
        self.assertIn('1. P1 慢：`sleep 5`；每次运行前都执行', text)


class HandoffTests(BridgeBase):
    def test_broken_set_handoff_has_the_error_and_no_secret(self):
        (self.root / 'tests' / 'rf_gone.py').write_text('from nowhere import thing\ndef run(input):\n    return 1\n', encoding='utf-8')
        ts = TE.testset_new(self.root, '导出', 'RF-101', 'tests/rf_gone.py')
        TE.testset_add(self.root, ts['id'], [{'name': 'x', 'input': {}, 'expect': 1}])
        TR.testset_run(self.root, [ts['id']], trusted=True)
        result = self.root / rf.DIR / 'tests' / 'results' / ts['id'] / 'main.json'
        rec = json.loads(result.read_text(encoding='utf-8'))
        rec['error'] += f'\n（假设被测代码把口令打印出来了：{SECRETS[0]}，还有请求头 {SECRETS[1]}）'  # 就算记录里漏了，交出去的话也要打码
        result.write_text(json.dumps(rec, ensure_ascii=False), encoding='utf-8')
        self.steps({'note': '编译', 'command': 'mvn -q compile', 'when': 'changed', 'watch': ['pom.xml']})
        out = EH.handoff(self.root, 'set', ts['id'])
        text = out['text']
        self.assertEqual(out['title'], f'处理：测试集 {ts["id"]}「导出」跑不起来')
        self.assertIn("No module named 'nowhere'", text)
        self.assertIn('接入代码 tests/rf_gone.py', text)
        self.assertIn('当前测试环境：「本机」（ENV-001）', text)
        self.assertIn('PAY_API=http://localhost:9090', text)
        self.assertIn('DB_PASSWORD（保密，这台电脑上填了值，你看不到也不要去找）', text)
        self.assertIn('1. 编译：`mvn -q compile`，pom.xml 变了才执行，用户还没同意执行', text)
        self.assertIn(f'python3 {RF} env propose ENV-001 --file -', text)
        self.assertIn('"testsets"', text)
        self.assertIn('不要运行 rf.py env save、adopt、ignore、approve、ai-secrets、reveal', text)
        for s in SECRETS:
            self.assertNotIn(s, text)
        code, cli, err = self.rf('env', 'handoff', 'set', ts['id'])
        self.assertEqual(code, 0, err)
        self.assertNotIn(SECRETS[0], cli)

    def test_prep_and_check_handoff(self):
        self.as_app()
        self.steps({'note': '装依赖', 'command': 'echo npm ERR! 404 left-pad; exit 1', 'dir': ''})
        EP.env_approve(self.root, 'ENV-001')
        EP.env_prep_run(self.root, 'ENV-001')
        text = EH.handoff(self.root, 'prep', 'ENV-001')['text']
        self.assertIn('准备步骤 1「装依赖」失败了', text)
        self.assertIn('npm ERR! 404 left-pad', text)
        self.assertIn('退出码 1', text)
        EE.env_save(self.root, 'ENV-001', dict(self.payload(), deps=[{'key': 'user:1', 'name': '支付服务', 'address': 'http://127.0.0.1:1', 'from': 'user'}]))
        EVal.env_check(self.root, 'ENV-001', timeout=0.5)
        out = EH.handoff(self.root, 'check', 'ENV-001')
        self.assertIn('有 1 个依赖连不上', out['text'])
        self.assertIn('支付服务（HTTP 接口）http://127.0.0.1:1', out['text'])
        for s in SECRETS:
            self.assertNotIn(s, out['text'])
        with self.assertRaisesRegex(ValueError, '要说处理什么'):
            EH.handoff(self.root, 'what')


class ProposeTests(BridgeBase):
    def test_prep_and_testset_settings_proposals(self):
        java = TE.testset_new(self.root, 'Java 的', 'RF-101', 'src/test/java/RfBills.java')
        EH.env_propose(self.root, 'ENV-001', {'summary': '第一次', 'vars': [{'name': 'SPRING_PROFILES_ACTIVE', 'value': 'local'}]})
        made = EH.env_propose(self.root, 'ENV-001', {
            'summary': '试出来了',
            'prep': [{'note': '编译', 'command': './mvnw -q -DskipTests compile', 'when': 'changed', 'watch': ['pom.xml'], 'why': '要先编译'}],
            'testsets': [{'id': java['id'], 'classpath': ['target/classes', 'lib/*'], 'build': 'mvn -q compile', 'why': '依赖在 lib 下'}]})
        self.assertEqual(made['waiting'], 3)  # 上次还没处理的留着
        prop = EV.env_show(self.root, 'ENV-001')['proposal']
        self.assertEqual([(i['k'], i['kind']) for i in prop['items']], [(1, 'var'), (2, 'prep'), (3, 'testset')])
        with self.assertRaisesRegex(ValueError, '只有 Java 的测试集用'):
            EH.env_propose(self.root, 'ENV-001', {'testsets': [{'id': self.ts, 'build': 'make'}]})
        with self.assertRaisesRegex(ValueError, '没有找到测试集'):
            EH.env_propose(self.root, 'ENV-001', {'testsets': [{'id': 'TS-099', 'build': 'make'}]})
        EH.env_adopt(self.root, 'ENV-001', [2, 3])
        env = E.find_env(self.root, 'ENV-001')
        self.assertEqual(env['prep'], [{'id': 'P1', 'note': '编译', 'command': './mvnw -q -DskipTests compile', 'dir': '', 'when': 'changed', 'watch': ['pom.xml'], 'timeout': 600}])
        self.assertFalse(EV.env_show(self.root, 'ENV-001')['prep'][0]['approved'])  # 采纳了也要用户同意才执行
        self.assertIn('采纳了 AI 的建议：准备步骤「编译」', env['log'][-1])
        ts = T.read_set(self.root, java['id'])
        self.assertEqual((ts['classpath'], ts['build']), (['target/classes', 'lib/*'], 'mvn -q compile'))
        self.assertIn('用户采纳了 AI 的建议，改了测试代码的设置', ts['log'][-1])
        states = [i['state'] for i in EV.env_show(self.root, 'ENV-001')['proposal']['items']]
        self.assertEqual(states, ['pending', 'adopted', 'adopted'])


class TestsetConfigTests(BridgeBase):
    def test_user_changes_java_settings(self):
        java = TE.testset_new(self.root, 'Java 的', 'RF-101', 'src/test/java/RfBills.java')
        out = TE.testset_config(self.root, java['id'], {'classpath': 'target/classes\n\nlib/*\n', 'build': 'mvn -q compile'})
        self.assertEqual(len(out['changed']), 2)
        ts = T.read_set(self.root, java['id'])
        self.assertEqual(ts['classpath'], ['target/classes', 'lib/*'])
        self.assertIn('用户改了测试代码的设置：classpath [] → ["target/classes","lib/*"]', ts['log'][-1])
        self.assertEqual(TE.testset_config(self.root, java['id'], {'classpath': ['target/classes', 'lib/*']})['changed'], [])
        TE.testset_config(self.root, java['id'], {'classpath': [], 'build': ''})
        ts = T.read_set(self.root, java['id'])
        self.assertNotIn('classpath', ts)
        self.assertNotIn('build', ts)
        for payload, msg in (({'lang': 'ruby'}, '语言只能是'), ({'entry': '../x.py'}, '不能放进'), ({'lang': 'python'}, 'Java 源文件'),
                             ({'build': 'a\nb'}, '只能是一条命令'), ({'what': 1}, '不认识的设置')):
            with self.assertRaisesRegex(ValueError, msg):
                TE.testset_config(self.root, java['id'], payload)
        with self.assertRaisesRegex(ValueError, '只有 Java'):
            TE.testset_config(self.root, self.ts, {'build': 'make'})
        code, text, err = self.rf('test', 'config', java['id'], '--file', '-', stdin=json.dumps({'entry': 'com.acme.RfBills', 'classpath': ['out']}))
        self.assertEqual(code, 1)  # 改设置只由你在应用里做
        self.assertIn('只能由你在应用里做', err)
        with app_marker():
            code, text, err = self.rf('test', 'config', java['id'], '--file', '-', stdin=json.dumps({'entry': 'com.acme.RfBills', 'classpath': ['out']}))
        self.assertEqual(code, 0, err)
        self.assertIn('接入代码 src/test/java/RfBills.java → com.acme.RfBills', text)
        self.assertEqual(TV.testset_show(self.root, java['id'])['classpath'], ['out'])


if __name__ == '__main__':
    unittest.main()

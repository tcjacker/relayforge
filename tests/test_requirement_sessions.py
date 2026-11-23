"""需求的会话配置里的第三项「测试」：默认跟着开发会话（不存字段、不复制值）、老需求同样处理、新建时可以单独选、建好以后只由用户改（req choose），
改了只对下一次新开的测试会话生效；req new / req choose 的命令行选项。"""
import io
import json
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'backend'))
import rf  # noqa: E402
from relayforge.requirements import record as R  # noqa: E402
from tests.app_fixture import app_marker  # noqa: E402
from tests.test_testsets import Base  # noqa: E402


class SessionChoiceTests(Base):
    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = rf.main(['req', *argv, '--root', str(self.root)])
        return code, out.getvalue(), err.getvalue()

    def head(self, req):
        return rf.read_doc(self.root / req['file'])[0]

    def test_the_testing_phase_is_the_third_one_and_is_optional(self):
        self.assertEqual(R.SESSION_PHASES, ('intake', 'development', 'testing'))
        plain = rf.req_new(self.root, '没单独选测试')
        self.assertNotIn('testing_cli', self.head(plain))
        self.assertEqual(plain['session_choices'], {})
        self.assertTrue(plain['testing_follows_development'])
        both = rf.req_new(self.root, '立项开发各选', intake_cli='claude', intake_model='sonnet', development_cli='codex', development_model='gpt-5.4')
        self.assertEqual(both['session_choices'], {'intake': {'cli': 'claude', 'model': 'sonnet'}, 'development': {'cli': 'codex', 'model': 'gpt-5.4'}})
        self.assertTrue(both['testing_follows_development'])
        self.assertNotIn('testing_cli', self.head(both))  # 「跟开发一样」没有复制一份开发的值
        self.assertNotIn('testing_model', self.head(both))

    def test_a_testing_choice_of_its_own_is_kept_and_checked_like_the_other_two(self):
        made = rf.req_new(self.root, '用别的模型测', development_cli='claude', development_model='sonnet', testing_cli='claude', testing_model='opus')
        self.assertEqual(made['session_choices']['testing'], {'cli': 'claude', 'model': 'opus'})
        self.assertEqual(made['session_choices']['development'], {'cli': 'claude', 'model': 'sonnet'})
        self.assertFalse(made['testing_follows_development'])
        head = self.head(made)
        self.assertEqual((head['testing_cli'], head['testing_model']), ('claude', 'opus'))
        default_model = rf.req_new(self.root, 'Codex 默认模型测', testing_cli='codex')
        self.assertEqual(default_model['session_choices']['testing'], {'cli': 'codex', 'model': ''})
        with self.assertRaisesRegex(ValueError, 'CLI'):
            rf.req_new(self.root, '坏的', testing_cli='gemini')
        with self.assertRaisesRegex(ValueError, '指定 CLI'):
            rf.req_new(self.root, '只有模型', testing_model='opus')
        self.assertEqual(len(rf.load_requirements(self.root)), 2)  # 坏的没有建出来

    def test_an_old_requirement_without_the_field_follows_development(self):
        old = rf.req_new(self.root, '老需求', development_cli='codex')
        path = self.root / old['file']
        meta, sections = rf.read_doc(path)
        for key in [k for k in meta if k.startswith('testing_')]:
            del meta[key]
        rf.write_doc(path, meta, sections)
        found = rf.find_requirement(self.root, old['id'])
        self.assertTrue(found['testing_follows_development'])
        self.assertNotIn('testing', found['session_choices'])
        self.assertEqual(found['session_choices']['development'], {'cli': 'codex', 'model': ''})
        # 没存任何 CLI 的更老的需求：session_choices 是空的，由应用沿用应用设置
        legacy = rf.req_new(self.root, '更老的需求')
        self.assertEqual(rf.find_requirement(self.root, legacy['id'])['session_choices'], {})

    def test_the_command_line_new_and_choose(self):
        code, out, err = self.cli('new', '命令行建的', '--development-cli', 'claude', '--development-model', 'sonnet', '--testing-cli', 'codex', '--testing-model', 'gpt-5.4', '--json')
        self.assertEqual(code, 0, err)
        made = json.loads(out)
        self.assertEqual(made['session_choices']['testing'], {'cli': 'codex', 'model': 'gpt-5.4'})
        with self.assertRaises(SystemExit):
            self.cli('new', '坏的', '--testing-cli', 'gemini')  # argparse 的 choices
        # 建好以后改：没有应用的标记、也不在终端里：拒绝
        code, _, err = self.cli('choose', made['id'], '--testing-follow')
        self.assertEqual(code, 1)
        self.assertIn('只能由你在应用里做', err)
        self.assertEqual(rf.find_requirement(self.root, made['id'])['session_choices']['testing']['cli'], 'codex')
        with app_marker():
            code, out, err = self.cli('choose', made['id'], '--testing-cli', 'claude', '--testing-model', 'opus', '--json')
            self.assertEqual(code, 0, err)
            self.assertEqual(json.loads(out)['session_choices']['testing'], {'cli': 'claude', 'model': 'opus'})
            code, out, err = self.cli('choose', made['id'], '--testing-follow', '--json')
            self.assertEqual(code, 0, err)
            after = json.loads(out)
            self.assertTrue(after['testing_follows_development'])
            self.assertNotIn('testing', after['session_choices'])
            self.assertEqual(after['session_choices']['development'], {'cli': 'claude', 'model': 'sonnet'})  # 开发的没动
            code, _, err = self.cli('choose', made['id'])
            self.assertEqual(code, 1)
            self.assertIn('用法', err)
        meta = self.head(made)
        self.assertNotIn('testing_cli', meta)
        self.assertNotIn('testing_model', meta)
        log = ' '.join(rf.find_requirement(self.root, made['id'])['sections']['变更记录'])
        self.assertIn('用户把测试会话的 CLI 和模型改为 Claude Code（opus）', log)
        self.assertIn('改为跟着开发会话', log)

    def test_the_change_is_for_the_next_testing_session_and_the_log_says_so_once_one_was_opened(self):
        made = rf.req_new(self.root, '测试中改', development_cli='claude')
        rf.req_choose(self.root, made['id'], 'testing', 'codex', '', trusted=True)
        log = ' '.join(rf.find_requirement(self.root, made['id'])['sections']['变更记录'])
        self.assertIn('Codex（默认模型）', log)
        self.assertNotIn('已经开着的测试会话不受影响', log)  # 还没有测试会话：不用多说
        path = self.root / made['file']
        meta, sections = rf.read_doc(path)
        meta['test_sessions'] = ['tester-1']
        rf.write_doc(path, meta, sections)
        again = rf.req_choose(self.root, made['id'], 'testing', 'claude', 'opus', trusted=True)
        self.assertEqual(again['session_choices']['testing'], {'cli': 'claude', 'model': 'opus'})
        self.assertEqual(again['test_sessions'], ['tester-1'])  # 已经开过的测试会话记录不动
        self.assertIn('已经开着的测试会话不受影响，下一次新开的测试会话用这个选择', again['sections']['变更记录'][-1])

    def test_only_testing_and_development_can_be_changed_and_only_by_the_user(self):
        made = rf.req_new(self.root, '规矩', intake_cli='claude', development_cli='claude')
        with self.assertRaisesRegex(ValueError, '只能由你在应用里做'):
            rf.req_choose(self.root, made['id'], 'testing', 'codex', '')
        with self.assertRaisesRegex(ValueError, '只能由你在应用里做'):
            rf.req_choose(self.root, made['id'], 'development', 'codex', '')
        with self.assertRaisesRegex(ValueError, '立项会话的不能改'):
            rf.req_choose(self.root, made['id'], 'intake', 'codex', '', trusted=True)
        with self.assertRaisesRegex(ValueError, '只有测试会话能选'):
            rf.req_choose(self.root, made['id'], 'development', follow=True, trusted=True)
        for bad in (dict(cli='gemini'), dict(cli='claude', model='bad model'), dict(cli=None)):
            with self.assertRaises(ValueError):
                rf.req_choose(self.root, made['id'], 'testing', bad.get('cli'), bad.get('model'), trusted=True)
        with self.assertRaisesRegex(ValueError, '不用再指定'):
            rf.req_choose(self.root, made['id'], 'testing', 'claude', follow=True, trusted=True)
        self.assertEqual(rf.find_requirement(self.root, made['id'])['session_choices'], {'intake': {'cli': 'claude', 'model': ''}, 'development': {'cli': 'claude', 'model': ''}})
        self.assertTrue(rf.find_requirement(self.root, made['id'])['testing_follows_development'])


if __name__ == '__main__':
    unittest.main()

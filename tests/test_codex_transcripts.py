"""停下来时的检查也读 Codex 的聊天记录：core/transcript.claude_shaped 把 Codex 的记录换成 Claude Code 的格式，
facts/notes.turn_weight、issues/probes.turn_facts、testsets/checks.turn_tests 照旧按 Claude Code 的格式读。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'backend'))
from relayforge.core.transcript import claude_shaped  # noqa: E402
from relayforge.facts.notes import turn_weight  # noqa: E402
from relayforge.issues.probes import turn_facts  # noqa: E402
from relayforge.testsets.checks import turn_tests  # noqa: E402

PLACE = '/work/R-1'


def item(payload, kind='response_item'):
    return {'timestamp': '2026-11-22T10:00:00Z', 'type': kind, 'payload': payload}


def said(role, text):
    return item({'type': 'message', 'role': role, 'content': [{'type': 'input_text' if role == 'user' else 'output_text', 'text': text}]})


def cell(code):
    return item({'type': 'custom_tool_call', 'name': 'exec', 'call_id': 'c', 'input': code})


def codex_turn(*middle, prompt='把导出修好'):
    """A Codex transcript: session start, the environment note, the user's words, then the given entries."""
    return [item({'cwd': PLACE}, 'session_meta'), said('user', '<environment_context>\n  <cwd>/work</cwd>\n</environment_context>'),
            item({'cwd': PLACE}, 'turn_context'), said('user', prompt), *middle]


class CodexTranscriptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def write(self, entries):
        path = Path(self.tmp.name) / 'rollout.jsonl'
        path.write_text(''.join(json.dumps(e, ensure_ascii=False) + '\n' for e in entries), encoding='utf-8')
        return str(path)

    def tools(self, path):
        return [(b['name'], b['input']) for o in claude_shaped(path) for b in o['message']['content'] if b.get('type') == 'tool_use']

    def test_every_way_codex_calls_a_tool_becomes_a_claude_tool_use(self):
        path = self.write(codex_turn(
            cell('text(await tools.exec_command({cmd:"cat \\"a b\\".txt",max_output_tokens:1000}))'),
            cell('text(await tools.exec_command({workdir:"/w",cmd:`git commit -m "x"`}))'),
            cell("text(await tools.exec_command({cmd:'ls -la'}))"),
            cell('text(await tools.apply_patch(`*** Begin Patch\n*** Update File: src/export.py\n@@\n-a\n+b\n*** Add File: /abs/new.md\n+x\n*** End Patch`))'),
            cell('text(await tools.apply_patch("*** Begin Patch\\n*** Delete File: old.py\\n*** End Patch"))'),
            cell('text(await tools.request_user_input_async({questions:[]}))'),
            cell('text(await tools.write_stdin({session_id:1,chars:""}))'),
            item({'type': 'function_call', 'name': 'exec_command', 'call_id': 'f', 'arguments': json.dumps({'cmd': 'npm test'})}),
            item({'type': 'function_call', 'name': 'shell', 'call_id': 'g', 'arguments': json.dumps({'command': ['bash', '-lc', 'make build']})}),
            item({'type': 'custom_tool_call', 'name': 'apply_patch', 'call_id': 'h', 'input': '*** Begin Patch\n*** Update File: b.py\n*** Move to: c.py\n*** End Patch'}),
        ))
        self.assertEqual(self.tools(path), [
            ('Bash', {'command': 'cat "a b".txt'}), ('Bash', {'command': 'git commit -m "x"'}), ('Bash', {'command': 'ls -la'}),
            ('Edit', {'file_path': f'{PLACE}/src/export.py'}), ('Edit', {'file_path': '/abs/new.md'}), ('Edit', {'file_path': f'{PLACE}/old.py'}),
            ('AskUserQuestion', {}), ('exec', {}), ('Bash', {'command': 'npm test'}), ('Bash', {'command': 'make build'}),
            ('Edit', {'file_path': f'{PLACE}/b.py'}), ('Edit', {'file_path': f'{PLACE}/c.py'})])

    def test_notes_injected_by_the_program_do_not_start_a_new_turn(self):
        path = self.write(codex_turn(
            cell('text(await tools.apply_patch(`*** Begin Patch\n*** Update File: src/export.py\n*** End Patch`))'),
            said('assistant', '改好了。'),
            said('user', '<hook_prompt hook_run_id="stop:1:x">〔RelayForge 程序〕这一轮的进展还没记。</hook_prompt>'),
        ))
        users = [o for o in claude_shaped(path) if o['type'] == 'user']
        self.assertEqual([o['message']['content'][0]['text'] for o in users], ['把导出修好'])
        self.assertEqual(turn_weight(path), (1, len('改好了。')))  # 钩子交回的提示之后，这一轮做过的改动还算数

    def test_a_new_user_turn_starts_counting_again(self):
        path = self.write(codex_turn(cell('text(await tools.exec_command({cmd:"echo x > a.txt"}))'), said('user', '再看看'),
                                     cell('text(await tools.exec_command({cmd:"cat a.txt"}))')))
        self.assertEqual(turn_weight(path), (0, 0))  # 只读不算改动

    def test_probe_and_test_checks_read_codex_turns(self):
        path = self.write(codex_turn(
            cell('text(await tools.exec_command({cmd:"rg export src"}))'),
            cell('text(await tools.exec_command({cmd:"python3 /x/.relayforge/bin/rf.py issue finding I-001 --kind bug --text \\"根因\\""}))'),
            cell('text(await tools.request_user_input({questions:[]}))'),
            said('assistant', '结论记下了。'), prompt='查一下导出'))
        self.assertEqual(turn_facts(path), (3, True, True, '结论记下了。'))
        path = self.write(codex_turn(
            cell('text(await tools.apply_patch(`*** Begin Patch\n*** Update File: tests/test_export.py\n*** Update File: src/export.py\n*** End Patch`))'),
            cell('text(await tools.exec_command({cmd:"python3 /x/.relayforge/bin/rf.py test run --req R-1"}))'),
            cell('text(await tools.exec_command({cmd:"python3 /x/.relayforge/bin/rf.py note doing \\"测试中\\""}))')))
        written, used, notes = turn_tests(path, PLACE)
        self.assertEqual((written, used), (['tests/test_export.py', 'src/export.py'], True))
        self.assertEqual(len(notes), 1)

    def test_claude_code_transcripts_pass_through_unchanged(self):
        lines = [{'type': 'user', 'message': {'role': 'user', 'content': '改一下'}},
                 {'type': 'assistant', 'message': {'role': 'assistant', 'content': [{'type': 'tool_use', 'name': 'Edit', 'input': {'file_path': '/w/a.py'}}]}}]
        path = self.write(lines)
        self.assertEqual(list(claude_shaped(path)), lines)
        self.assertEqual(turn_weight(path), (1, 0))


if __name__ == '__main__':
    unittest.main()

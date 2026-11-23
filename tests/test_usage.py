"""用虚构聊天记录验证用量汇总和回放，不启动模型。"""
import gzip
import io
import json
import os
from contextlib import redirect_stdout
from pathlib import Path

from tests.test_history import HistoryBase, GONE, SID, rf
from relayforge.issues.record import write_fix, write_probe
from relayforge.sessions.usage import usage_report

CODEX = 'cccc1111-2222-3333-4444-555566667777'
THREAD = 'dddd1111-2222-3333-4444-555566667777'


def jsonl(rows):
    return '\n'.join(json.dumps(row, ensure_ascii=False) for row in rows) + '\n'


def reply(mid, model, usage, **extra):
    return dict(type='assistant', message={'id': mid, 'model': model, 'usage': usage}, **extra)


def event(kind, **payload):
    return {'type': kind, 'payload': payload, 'timestamp': '2026-11-18T01:03:03Z'}


def codex_count(total=None, last=None):
    return event('event_msg', type='token_count', info={'total_token_usage': total, 'last_token_usage': last})


class UsageTests(HistoryBase):
    def test_usage_groups_requirement_sessions_and_does_not_double_count(self):
        req = rf.req_new(self.root, '统计演示')
        doc = self.root / req['file']
        meta, body = rf.read_doc(doc)
        rf.write_doc(doc, dict(meta, state='done', intake=[SID], sessions=[SID, SID, CODEX, GONE], test_sessions=[CODEX]), body)
        kept = rf.archive_dir(self.root, req['id']) / f'{SID}.jsonl.gz'
        kept.parent.mkdir(parents=True)
        first = {'input_tokens': 5, 'output_tokens': 2, 'cache_creation_input_tokens': 10, 'cache_read_input_tokens': 20}
        with gzip.open(kept, 'wt', encoding='utf-8') as f:
            f.write(jsonl([reply('msg-1', 'claude-sonnet', first), reply('msg-1', 'claude-sonnet', dict(first, output_tokens=8)),
                           reply('msg-2', 'claude-opus', {'input_tokens': 7, 'output_tokens': 3}),
                           reply('msg-3', '<synthetic>', {'input_tokens': 99})]))
        subs = kept.parent / SID / 'subagents'
        subs.mkdir(parents=True)
        with gzip.open(subs / 'agent-a.jsonl.gz', 'wt', encoding='utf-8') as f:
            f.write(jsonl([reply('msg-1', 'claude-sonnet', first, isSidechain=True),
                           reply('msg-4', 'claude-haiku', {'input_tokens': 3, 'output_tokens': 1}, isSidechain=True)]))
        rollout = Path(os.environ['CODEX_HOME']) / 'sessions' / f'rollout-{THREAD}.jsonl'
        rollout.parent.mkdir(parents=True)
        a = {'input_tokens': 100, 'cached_input_tokens': 60, 'output_tokens': 10, 'reasoning_output_tokens': 5}
        b = {'input_tokens': 140, 'cached_input_tokens': 70, 'output_tokens': 30, 'reasoning_output_tokens': 10}
        rollout.write_text(jsonl([
            event('session_meta', id=THREAD, cwd=str(self.root)),
            event('turn_context', model='codex-a'), codex_count(a, a),
            event('turn_context', model='codex-b'), codex_count(a, a),
            codex_count(b), codex_count(b, {'input_tokens': 999}),
            codex_count(last={'input_tokens': 1, 'output_tokens': 2}),
        ]), encoding='utf-8')
        registry = Path(os.environ['RELAYFORGE_HOME']) / 'codex-sessions.json'
        registry.write_text(json.dumps({CODEX: {'root': str(self.root), 'transcript': str(rollout), 'nativeId': THREAD}}))
        saved_registry = registry.read_bytes()
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(rf.main(['usage', req['id'], '--json', '--root', str(self.root)]), 0)
        report = json.loads(output.getvalue())
        self.assertEqual(registry.read_bytes(), saved_registry)
        self.assertEqual([(s['kind'], s['source']) for s in report['sessions']],
                         [('intake', 'archive'), ('test', 'original'), ('develop', None)])
        self.assertEqual(report['tokens'], {'input': 186, 'output': 44, 'cache_read': 90, 'cache_creation': 10, 'total': 230})
        self.assertEqual({g['model']: g['total'] for g in report['groups']},
                         {'claude-sonnet': 43, 'claude-opus': 10, 'claude-haiku': 4, 'codex-a': 110, 'codex-b': 63})
        self.assertEqual(usage_report(self.root, session=report['sessions'][1]['session'])['tokens']['total'], 173)

    def test_issue_combines_its_probe_with_shared_fix(self):
        fix_id = 'F-001'
        for ident, probe in [('I-001', SID), ('I-002', GONE)]:
            rf.write_doc(self.root / rf.DIR / 'issues' / f'{ident}.md',
                         {'id': ident, 'type': 'issue', 'fix': fix_id, 'state': 'resolved'}, {})
            write_probe(self.root, {'issue': ident, 'sessions': [probe]})
        write_fix(self.root, {'id': fix_id, 'issues': ['I-001', 'I-002'], 'sessions': [CODEX, CODEX]})
        self.assertEqual([s['session_id'] for s in usage_report(self.root, 'I-001')['sessions']], [SID, CODEX])
        self.assertEqual([s['session_id'] for s in usage_report(self.root, fix_id)['sessions']], [CODEX])
        self.assertIsNone(usage_report(self.root, session=rf.stable_tag(self.root, GONE))['sessions'][0]['tokens'])

    def test_replay_pages_keep_program_message_steps_and_status(self):
        path = self.say(self.root, SID, subagent=False)
        rows = []
        for i in range(35):
            said = ('Stop hook feedback\n〔RelayForge 程序 · 测试结论〕请核对 password=demo-secret\n'
                    if i == 1 else f'用户第 {i} 次发话\n')
            rows += [{'type': 'user', 'message': {'content': said + '长' * 1800}},
                     {'type': 'assistant', 'message': {'content': [
                         {'type': 'tool_use', 'name': 'Read', 'id': f'r{i}', 'input': {'file_path': '说明.md'}},
                         {'type': 'tool_use', 'name': 'Edit', 'id': f'e{i}', 'input': {'file_path': '记录.md'}},
                         {'type': 'tool_use', 'name': 'Bash', 'id': f'b{i}', 'input': {'command': 'false'}},
                     ]}},
                     {'type': 'user', 'message': {'content': [
                         {'type': 'tool_result', 'tool_use_id': f'r{i}', 'content': '工具输出不应出现'},
                         {'type': 'tool_result', 'tool_use_id': f'e{i}', 'is_error': True, 'content': 'blocked by a hook'},
                         {'type': 'tool_result', 'tool_use_id': f'b{i}', 'is_error': True, 'content': 'exit 1'},
                     ]}}]
        path.write_text(jsonl(rows), encoding='utf-8')
        first = rf.history_session(self.root, SID, steps=True, offset=0)
        second = rf.history_session(self.root, SID, steps=True, offset=first['next_offset'])
        turn = first['turns'][1]
        self.assertEqual(turn['speaker'], 'program')
        self.assertIn('测试结论', turn['said'])
        self.assertNotIn('demo-secret', json.dumps(first))
        self.assertNotIn('工具输出不应出现', json.dumps(first, ensure_ascii=False))
        self.assertEqual([(s['what'], s['status']) for s in turn['steps']], [('读', 'ok'), ('改', 'refused'), ('跑', 'failed')])
        self.assertEqual(first['turns'][-1]['n'] + 1, second['turns'][0]['n'])
        self.assertEqual(len(first['turns']) + len(second['turns']), 35)

    def test_codex_replay_uses_registered_opening_marker_and_groups_script_steps(self):
        folder = Path(os.environ['CODEX_HOME']) / 'sessions'
        folder.mkdir(parents=True)
        rollout = folder / f'rollout-{THREAD}.jsonl'
        command = """tools.apply_patch(`*** Begin Patch
*** Add File: 说明.md
+演示
*** End Patch`)
tools.exec_command({cmd:"false"})"""
        rollout.write_text(jsonl([
            event('session_meta', id=THREAD, cwd=str(self.root)),
            event('response_item', type='message', role='user', content=[{'type': 'input_text', 'text': '<environment_context>说明'}]),
            event('response_item', type='message', role='user', content=[
                {'type': 'input_text', 'text': f'启动说明\nRelayForge 会话标识：{CODEX}\n以「〔RelayForge 程序 · 来源 · 编号〕」开头'}]),
            event('response_item', type='custom_tool_call', name='exec', call_id='one', input=command),
            event('response_item', type='message', role='user', content=[
                {'type': 'input_text', 'text': '<hook_prompt>〔RelayForge 程序 · 测试 · m-abc〕\n请看验收结论</hook_prompt>'}]),
            event('response_item', type='custom_tool_call_output', call_id='one', output='Process exited with code 1'),
        ]), encoding='utf-8')
        (Path(os.environ['RELAYFORGE_HOME']) / 'codex-sessions.json').write_text(json.dumps({CODEX: {'root': str(self.root)}}))
        replay = rf.history_session(self.root, CODEX, steps=True, offset=0)
        opening, notice = replay['turns']
        self.assertNotIn('speaker', opening)
        self.assertEqual((opening['steps'][0]['what'], opening['steps'][0]['status']), ('脚本', 'failed'))
        self.assertRegex(opening['steps'][0]['detail'], r'改 说明\.md.*跑 false')
        self.assertEqual(notice['speaker'], 'program')
        self.assertIn('请看验收结论', notice['said'])
        self.assertNotIn('<hook_prompt>', notice['said'])

import io
import json
import os
import re
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
from relayforge.project import hooks  # noqa: E402
from relayforge.project import status  # noqa: E402

STUB = '''import json, os, sys
sys.stdin.read()
print("好的，结果如下：")
print(open(os.environ["RF_TEST_ITEMS"], encoding="utf-8").read())
'''


def line(kind, content, **extra):
    return json.dumps(dict({'type': kind, 'message': {'role': kind, 'content': content}}, **extra), ensure_ascii=False)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / 'project'
        self.root.mkdir()
        home = mock.patch.dict(os.environ, {'RELAYFORGE_HOME': str(Path(self.tmp.name) / 'home')})
        home.start()
        self.addCleanup(home.stop)
        rf.init(self.root, hooks=False)
        self.stub = Path(self.tmp.name) / 'stub.py'
        self.stub.write_text(STUB, encoding='utf-8')
        self.items_file = Path(self.tmp.name) / 'items.json'
        self.transcript = Path(self.tmp.name) / 'abcd1234-session.jsonl'
        patch = mock.patch.dict(os.environ, {
            'RELAYFORGE_EXTRACTOR': json.dumps([sys.executable, str(self.stub)]),
            'RF_TEST_ITEMS': str(self.items_file),
        })
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(self.tmp.cleanup)
        os.environ.pop(rf.GUARD, None)
        os.environ.pop('CLAUDE_PROJECT_DIR', None)

    def talk(self, count):
        rows = [line('user' if i % 2 == 0 else 'assistant', f'第 {i + 1} 句') for i in range(count)]
        self.transcript.write_text('\n'.join(rows) + '\n', encoding='utf-8')

    def run_capture(self, items):
        """Structure items stand for what initialization or the map editing session writes; the rest go through capture."""
        tag = rf.stable_tag(self.root, 'abcd1234-session')
        for item in items:
            if item.get('kind') == 'structure':
                rf.apply_item(self.root, item, f'{tag}#{item.get("evidence_at")}')
        rest = [i for i in items if i.get('kind') != 'structure']
        self.items_file.write_text(json.dumps({'items': rest}, ensure_ascii=False), encoding='utf-8')
        return rf.capture(self.root, str(self.transcript), 'abcd1234-session', 'test')

    def docs(self, kind):
        return {meta['id']: (path, meta, sections) for path, meta, sections in rf.all_docs(self.root, (kind,))}


class TranscriptTests(Base):
    def test_keeps_only_spoken_text(self):
        rows = [
            line('user', '先定一下目录。<system-reminder>内部提示</system-reminder>'),
            line('assistant', [{'type': 'thinking', 'thinking': '想一想'}, {'type': 'text', 'text': '建议放在 .relayforge。'},
                               {'type': 'tool_use', 'name': 'Bash', 'input': {}}]),
            line('user', [{'type': 'tool_result', 'content': '命令输出'}]),
            line('assistant', '子代理说的话', isSidechain=True),
            json.dumps({'type': 'attachment', 'x': 1}),
            '这一行不是 JSON',
            line('user', [{'type': 'text', 'text': '同意。'}, {'type': 'image', 'source': {}}]),
        ]
        self.transcript.write_text('\n'.join(rows), encoding='utf-8')
        got = rf.read_transcript(self.transcript)
        self.assertEqual([(m['role'], m['text']) for m in got],
                         [('user', '先定一下目录。'), ('assistant', '建议放在 .relayforge。'), ('user', '同意。')])

    def test_codex_messages_are_read_without_duplicate_events(self):
        rows = [
            {'type': 'session_meta', 'payload': {'id': 'native-id', 'cwd': str(self.root)}},
            {'type': 'event_msg', 'payload': {'type': 'user_message', 'message': '不要重复'}},
            {'type': 'response_item', 'timestamp': '2026-11-16T01:00:00Z', 'payload': {
                'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': '选择 Codex'}]}},
            {'type': 'response_item', 'payload': {'type': 'function_call', 'name': 'exec_command'}},
            {'type': 'response_item', 'payload': {'type': 'message', 'role': 'assistant',
                'content': [{'type': 'output_text', 'text': '已经保存。'}]}},
        ]
        self.transcript.write_text('\n'.join(json.dumps(row) for row in rows), encoding='utf-8')
        messages = rf.read_transcript(self.transcript)
        self.assertEqual([(m['role'], m['text']) for m in messages], [('user', '选择 Codex'), ('assistant', '已经保存。')])
        self.assertEqual(messages[0]['ts'], '2026-11-16T01:00:00Z')

    def test_codex_background_model_receives_instruction_and_payload_on_stdin(self):
        from backend.relayforge.core import model
        result = (0, '{"items": []}', '')  # model.py 经 procs.run 启动模型（超时连同后代一起停）
        with mock.patch.object(model, 'extractor_command', return_value=['codex', 'exec']), mock.patch.object(model.procs, 'run', return_value=result) as run:
            self.assertEqual(model.run_model(self.root, '请输出 JSON', '对话内容'), {'items': []})
        self.assertEqual(run.call_args.args[0], ['codex', 'exec', '-'])
        self.assertEqual(run.call_args.kwargs['input'], '请输出 JSON\n\n对话内容')

    def test_the_model_never_gets_the_app_marker_or_the_session_token(self):
        from backend.relayforge.core import model
        secrets = {'RELAYFORGE_APP_TOKEN': 'app-marker-0123456789', 'RELAYFORGE_SESSION_SIG': 'body.sig'}
        with mock.patch.dict(os.environ, secrets), mock.patch.object(model, 'extractor_command', return_value=['claude', '-p']), \
                mock.patch.object(model.procs, 'run', return_value=(0, '{}', '')) as run:
            model.run_model(self.root, '请输出 JSON', '对话内容')  # 不带 permissions 的（同步、整理）也一样
        env = run.call_args.kwargs['env']
        self.assertNotIn('RELAYFORGE_APP_TOKEN', env)
        self.assertNotIn('RELAYFORGE_SESSION_SIG', env)

    def test_counting_messages_reads_the_same_as_reading_them(self):
        rows = [{'type': 'user', 'message': {'content': f'第 {n} 句'}} for n in range(5)] + [{'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': '好'}]}}]
        self.transcript.write_text('\n'.join(json.dumps(r, ensure_ascii=False) for r in rows), encoding='utf-8')
        self.assertEqual(rf.count_messages(self.transcript), len(rf.read_transcript(self.transcript)))
        self.assertEqual(rf.count_messages(Path(self.tmp.name) / 'none.jsonl'), 0)

    def test_missing_transcript_is_empty(self):
        self.assertEqual(rf.read_transcript(Path(self.tmp.name) / 'none.jsonl'), [])

    def test_long_sessions_are_split_with_continuous_numbers(self):
        messages = [{'role': 'user', 'text': 'x' * 3000} for _ in range(100)]
        pieces = list(rf.chunks(messages, 11))
        self.assertGreater(len(pieces), 1)
        self.assertEqual(pieces[0][1], 11)
        self.assertEqual(pieces[-1][2], 110)
        for (_, _, hi), (_, lo, _) in zip(pieces, pieces[1:]):
            self.assertEqual(lo, hi + 1)


class DocumentTests(Base):
    def test_round_trip_keeps_header_and_sections(self):
        path = self.root / rf.DIR / 'features' / 'RF-101-示例.md'
        meta = {'id': 'RF-101', 'type': 'feature', 'title': '示例', 'status': 'current', 'module': 'M-01', 'order': 2,
                'depends_on': ['RF-100', 'RF-099'], 'superseded_by': None}
        sections = {'一句话定位': ['- 一句话。〔S-1#2〕'], '变更记录': []}
        rf.write_doc(path, meta, sections)
        got_meta, got_sections = rf.read_doc(path)
        self.assertEqual(got_meta, meta)
        self.assertEqual(got_sections, {'一句话定位': ['- 一句话。〔S-1#2〕'], '变更记录': [rf.PLACEHOLDER]})


class InitTests(Base):
    @in_repo_layout  # 旧布局才往 AGENTS.md 写约定；数据外置的见 test_datastore.RulesTests
    def test_creates_layout_and_conventions(self):
        base = self.root / rf.DIR
        for name in ('product', 'modules', 'features', 'decisions', 'documents', 'history', 'inbox', 'runs'):
            self.assertTrue((base / name).is_dir(), name)
        self.assertTrue((base / 'bin' / 'rf.py').exists())
        self.assertTrue((base / 'FORMAT.md').exists())
        self.assertIn('项目事实的写法', (self.root / 'AGENTS.md').read_text(encoding='utf-8'))
        self.assertEqual((self.root / 'CLAUDE.md').read_text(encoding='utf-8'), '@AGENTS.md\n')
        self.assertIn(f'{rf.DIR}/cursor.json', (self.root / '.gitignore').read_text(encoding='utf-8'))
        self.assertFalse((self.root / '.claude' / 'settings.json').exists())

    @in_repo_layout
    def test_program_copy_carries_the_license(self):
        # 许可证要求再分发时附带条款，项目里的程序副本也要带上；包里那份必须和仓库根目录的一致。
        repo = Path(__file__).resolve().parent.parent
        text = (repo / 'LICENSE').read_text(encoding='utf-8')
        self.assertEqual((repo / 'backend' / 'relayforge' / 'LICENSE').read_text(encoding='utf-8'), text)
        self.assertIn('FSL-1.1-ALv2', text)
        self.assertEqual((self.root / rf.DIR / 'bin' / 'LICENSE').read_text(encoding='utf-8'), text)

    @in_repo_layout  # 旧布局才往 AGENTS.md 写约定；数据外置的见 test_datastore.RulesTests
    def test_running_twice_does_not_duplicate_or_overwrite(self):
        agents = self.root / 'AGENTS.md'
        agents.write_text('# 我的约定\n\n保留这一行。\n\n' + agents.read_text(encoding='utf-8'), encoding='utf-8')
        (self.root / 'CLAUDE.md').write_text('我自己的说明\n', encoding='utf-8')
        _, notes = rf.init(self.root, hooks=False)
        text = agents.read_text(encoding='utf-8')
        self.assertEqual(text.count(rf.BEGIN), 1)
        self.assertIn('保留这一行。', text)
        self.assertEqual((self.root / 'CLAUDE.md').read_text(encoding='utf-8'), '我自己的说明\n')
        self.assertTrue(any('CLAUDE.md' in note for note in notes))
        self.assertEqual((self.root / '.gitignore').read_text(encoding='utf-8').count('cursor.json'), 1)

    def test_hooks_are_merged_into_existing_settings(self):
        settings = self.root / '.claude' / 'settings.json'
        settings.parent.mkdir(exist_ok=True)  # init already created it for the sync skill
        settings.write_text(json.dumps({'permissions': {'allow': ['Bash(ls)']}, 'hooks': {'Stop': [{'hooks': [{'type': 'command', 'command': 'echo hi'}]}]}}), encoding='utf-8')
        rf.init(self.root, hooks=True)
        rf.init(self.root, hooks=True)
        data = json.loads(settings.read_text(encoding='utf-8'))
        self.assertEqual(data['permissions'], {'allow': ['Bash(ls)']})
        self.assertEqual(len(data['hooks']['Stop']), 1)
        self.assertEqual(set(data['hooks']) - {'Stop'}, set(rf.HOOKS))
        self.assertEqual(len(data['hooks']['SessionStart']), 1)
        self.assertTrue(data['hooks']['PreCompact'][0]['hooks'][0]['async'])


class CaptureTests(Base):
    def test_stated_items_take_effect_and_inferred_ones_wait(self):
        self.talk(10)
        out = self.run_capture([
            {'kind': 'structure', 'op': 'add-feature', 'title': 'Lifecycle FSM', 'module': '场景生命周期', 'one_liner': '让场景按同一套阶段推进。', 'evidence': 'x', 'evidence_at': 1, 'stated_by': 'user'},
            {'kind': 'decision', 'title': '提交在前', 'statement': '整点提交先于跨日交接。', 'reason': '避免错位', 'applies_to': ['RF-101'], 'evidence': 'x', 'evidence_at': 3, 'stated_by': 'user'},
            {'kind': 'decision', 'title': '保留全部记忆', 'statement': '记忆全部保留。', 'evidence': 'x', 'evidence_at': 4, 'stated_by': 'inferred'},
            {'kind': 'open-question', 'target': 'RF-101', 'statement': '落在整点时先校验哪一条？', 'evidence': 'x', 'evidence_at': 5, 'stated_by': 'inferred'},
            {'kind': 'decision', 'statement': '编号超出范围的条目应被丢弃。', 'evidence': 'x', 'evidence_at': 99, 'stated_by': 'user'},
            {'kind': 'unknown', 'statement': '不认识的类型。', 'evidence_at': 2},
        ])
        self.assertEqual(out.parent.name, 'merged')
        record = json.loads(out.read_text(encoding='utf-8'))
        self.assertEqual(record['range'], [1, 10])
        self.assertEqual(len(record['items']), 3)

        modules, features, decisions = self.docs('modules'), self.docs('features'), self.docs('decisions')
        self.assertEqual(modules['M-01'][1]['title'], '场景生命周期')
        path, meta, sections = features['RF-101']
        self.assertEqual((meta['module'], meta['status'], path.name), ('M-01', 'current', 'RF-101-Lifecycle-FSM.md'))
        self.assertEqual(list(sections), rf.FEATURE_HEADS)
        self.assertRegex(sections['一句话定位'][0], r'^- 让场景按同一套阶段推进。〔S-\d{8}-abcd#1〕$')
        self.assertIn('落在整点时先校验哪一条？', sections['还没定的事'][0])
        self.assertEqual(sections['用户旅程'], [rf.PLACEHOLDER])

        self.assertEqual(decisions['D-0001'][1]['status'], 'current')
        self.assertEqual(decisions['D-0001'][1]['decided_by'], 'user')
        self.assertNotIn(rf.PENDING, decisions['D-0001'][2]['决定'][0])
        self.assertEqual(decisions['D-0002'][1]['status'], 'draft')
        self.assertTrue(decisions['D-0002'][2]['决定'][0].startswith('- ' + rf.PENDING))

        base = self.root / rf.DIR
        context = (base / 'context.md').read_text(encoding='utf-8')
        self.assertIn('RF-101 Lifecycle FSM：让场景按同一套阶段推进。', context)
        self.assertIn('D-0001 提交在前', context)
        self.assertNotIn('D-0002 保留全部记忆 →', context)
        self.assertIn('待你确认（1 个对象）', context)
        self.assertLessEqual(len(context), rf.CONTEXT_LIMIT + 60)
        self.assertIn('记忆全部保留。', (base / 'pending.md').read_text(encoding='utf-8'))
        self.assertEqual(json.loads((base / 'cursor.json').read_text(encoding='utf-8')), {'abcd1234-session': 10})

    def test_only_new_messages_are_processed_next_time(self):
        self.talk(4)
        self.run_capture([{'kind': 'decision', 'statement': '第一条决定。', 'evidence': 'x', 'evidence_at': 2, 'stated_by': 'user'}])
        self.assertIsNone(self.run_capture([{'kind': 'decision', 'statement': '不该出现。', 'evidence': 'x', 'evidence_at': 1, 'stated_by': 'user'}]))
        self.talk(6)
        self.run_capture([
            {'kind': 'decision', 'statement': '旧消息里的条目应被丢弃。', 'evidence': 'x', 'evidence_at': 2, 'stated_by': 'user'},
            {'kind': 'decision', 'statement': '第一条决定。', 'evidence': 'x', 'evidence_at': 5, 'stated_by': 'user'},
            {'kind': 'decision', 'statement': '第二条决定。', 'evidence': 'x', 'evidence_at': 6, 'stated_by': 'user'},
        ])
        statements = [s['决定'][0] for _, _, s in self.docs('decisions').values()]
        self.assertEqual(len(statements), 2)
        self.assertTrue(any('第二条决定。' in s and '#6〕' in s for s in statements))

    def test_superseding_moves_to_history_only_when_the_user_said_so(self):
        self.talk(8)
        self.run_capture([
            {'kind': 'decision', 'title': '所有请求都重试', 'statement': '所有 GET 都重试。', 'evidence': 'x', 'evidence_at': 1, 'stated_by': 'user'},
            {'kind': 'decision', 'title': '只留三天', 'statement': '记忆只保留三天。', 'evidence': 'x', 'evidence_at': 2, 'stated_by': 'user'},
        ])
        self.talk(12)
        self.run_capture([
            {'kind': 'decision', 'title': '只重试版本查询', 'statement': '只有版本查询重试。', 'supersedes': ['D-0001'], 'evidence': 'x', 'evidence_at': 9, 'stated_by': 'user'},
            {'kind': 'supersede', 'target': 'D-0002', 'statement': '可能不再适用。', 'evidence': 'x', 'evidence_at': 10, 'stated_by': 'inferred'},
        ])
        decisions = self.docs('decisions')
        self.assertNotIn('D-0001', decisions)
        old_meta, old_sections = rf.read_doc(self.root / rf.DIR / 'history' / 'D-0001-所有请求都重试.md')
        self.assertEqual((old_meta['status'], old_meta['superseded_by']), ('superseded', 'D-0003'))
        self.assertIn('被D-0003替代', old_sections['变更记录'][-1])
        self.assertEqual(decisions['D-0002'][1]['status'], 'current')
        self.assertIn(rf.PENDING, decisions['D-0002'][2]['变更记录'][-1])
        self.assertEqual(decisions['D-0003'][1]['supersedes'], ['D-0001'])
        context = (self.root / rf.DIR / 'context.md').read_text(encoding='utf-8')
        self.assertNotIn('D-0001', context)
        # a retired number is never reused
        self.talk(14)
        self.run_capture([{'kind': 'decision', 'statement': '又一条。', 'evidence': 'x', 'evidence_at': 13, 'stated_by': 'user'}])
        self.assertIn('D-0004', self.docs('decisions'))

    def test_a_later_decision_can_supersede_an_earlier_one_from_the_same_batch(self):
        self.talk(6)
        self.run_capture([
            {'kind': 'decision', 'title': '确认时写入 PRD', 'statement': '需求确认时就写入 PRD。', 'evidence': 'x', 'evidence_at': 1, 'stated_by': 'user'},
            {'kind': 'decision', 'title': '验收后才并入', 'statement': '验收通过才并入 PRD。', 'supersedes': ['确认时写入 PRD', '不存在的标题'], 'evidence': 'x', 'evidence_at': 5, 'stated_by': 'user'},
        ])
        decisions = self.docs('decisions')
        self.assertEqual(list(decisions), ['D-0002'])
        self.assertEqual(decisions['D-0002'][1]['supersedes'], ['D-0001'])
        old_meta, _ = rf.read_doc(next((self.root / rf.DIR / 'history').glob('D-0001-*.md')))
        self.assertEqual(old_meta['superseded_by'], 'D-0002')

    def test_rename_and_move_keep_the_number(self):
        self.talk(6)
        self.run_capture([
            {'kind': 'structure', 'op': 'add-feature', 'title': '小时推进', 'module': '时间与世界', 'evidence': 'x', 'evidence_at': 1, 'stated_by': 'user'},
            {'kind': 'structure', 'op': 'add-module', 'title': '场景生命周期', 'evidence': 'x', 'evidence_at': 2, 'stated_by': 'user'},
        ])
        self.talk(10)
        self.run_capture([
            {'kind': 'structure', 'op': 'rename', 'id': 'RF-101', 'title': '按小时推进', 'evidence': 'x', 'evidence_at': 7, 'stated_by': 'user'},
            {'kind': 'structure', 'op': 'move', 'id': 'RF-101', 'module': 'M-02', 'evidence': 'x', 'evidence_at': 8, 'stated_by': 'user'},
            {'kind': 'structure', 'op': 'rename', 'id': 'RF-101', 'title': '不该生效', 'evidence': 'x', 'evidence_at': 9, 'stated_by': 'inferred'},
        ])
        features = self.docs('features')
        self.assertEqual(list(features), ['RF-101'])
        path, meta, sections = features['RF-101']
        self.assertEqual((path.name, meta['title'], meta['module']), ('RF-101-按小时推进.md', '按小时推进', 'M-02'))
        self.assertTrue(any(rf.PENDING in line and '不该生效' in line for line in sections['变更记录']))

    def test_an_everyday_session_does_not_change_the_big_picture(self):
        self.talk(6)
        rf.apply_item(self.root, {'kind': 'structure', 'op': 'add-feature', 'title': '小时推进', 'module': '时间与世界', 'stated_by': 'user'}, 'S-test#1')
        self.items_file.write_text(json.dumps({'items': [
            {'kind': 'structure', 'op': 'add-module', 'title': '再加一个模块', 'evidence': 'x', 'evidence_at': 2, 'stated_by': 'user'},
            {'kind': 'structure', 'op': 'rename', 'id': 'RF-101', 'title': '改个名', 'evidence': 'x', 'evidence_at': 3, 'stated_by': 'user'},
            {'kind': 'supersede', 'target': 'M-01', 'statement': '不要了', 'evidence': 'x', 'evidence_at': 4, 'stated_by': 'user'},
            {'kind': 'decision', 'statement': '决定照常记。', 'evidence': 'x', 'evidence_at': 5, 'stated_by': 'user'},
        ]}, ensure_ascii=False), encoding='utf-8')
        before = {p.name: p.read_text(encoding='utf-8') for kind in ('modules', 'features') for p in (self.root / rf.DIR / kind).glob('*.md')}
        rf.capture(self.root, str(self.transcript), 'abcd1234-session', 'test')
        after = {p.name: p.read_text(encoding='utf-8') for kind in ('modules', 'features') for p in (self.root / rf.DIR / kind).glob('*.md')}
        self.assertEqual(before, after)
        self.assertEqual(list(self.docs('decisions')), ['D-0001'])
        self.assertNotIn('add-module', rf.INSTRUCTION)

    def test_extractor_failure_leaves_the_cursor_untouched(self):
        self.talk(4)
        self.items_file.write_text('没有 JSON', encoding='utf-8')
        with self.assertRaises(ValueError):
            rf.capture(self.root, str(self.transcript), 'abcd1234-session', 'test')
        self.assertFalse((self.root / rf.DIR / 'cursor.json').exists())


class RenderTests(Base):
    def test_page_carries_the_facts_and_lists_unregistered_documents(self):
        (self.root / 'docs').mkdir()
        (self.root / 'docs' / 'old-plan.md').write_text('# 旧计划\n', encoding='utf-8')
        self.talk(6)
        self.run_capture([
            {'kind': 'structure', 'op': 'add-feature', 'title': 'Lifecycle FSM', 'module': '场景生命周期', 'one_liner': '含有 </script> 的一句话。', 'evidence': 'x', 'evidence_at': 1, 'stated_by': 'user'},
            {'kind': 'decision', 'title': '保留全部记忆', 'statement': '记忆全部保留。', 'evidence': 'x', 'evidence_at': 2, 'stated_by': 'inferred'},
        ])
        page = (self.root / rf.DIR / 'site' / 'index.html').read_text(encoding='utf-8')
        self.assertTrue(page.startswith('<title>'))
        self.assertNotIn('<html', page)
        self.assertEqual(page.count('</script>'), 2)
        raw = page.split('<script type="application/json" id="data">', 1)[1].split('</script>', 1)[0]
        data = json.loads(raw)
        self.assertEqual([f['id'] for f in data['features']], ['RF-101'])
        self.assertEqual(data['features'][0]['sections']['一句话定位'][0].split('〔')[0], '- 含有 </script> 的一句话。')
        self.assertEqual([p['id'] for p in data['pending']], ['D-0001'])
        self.assertEqual(data['unregistered'], ['docs/old-plan.md'])
        self.assertEqual(data['last']['items'], 1)
        self.assertEqual((data['relations'], data['journeys']), ([], []))
        self.assertEqual(([l['name'] for l in data['layers']], data['moduleLayer']), (['未分层'], {'M-01': '未分层'}))
        self.assertNotIn('定下来的事', page)  # the big picture no longer ends with the board of decisions
        self.assertIn('用户旅程', page)
        self.assertIn(f'{rf.DIR}/site/', (self.root / '.gitignore').read_text(encoding='utf-8'))


class SessionTests(Base):
    STATE = {'goal': '做一个记账工具', 'doing': '写导入功能', 'changes': [{'at': 2, 'text': '先不做多币种'}],
             'plan': [{'title': '定数据结构', 'status': 'done', 'note': '', 'at': 1}, {'title': '导入账单', 'status': 'doing', 'note': '只支持 CSV', 'at': 3}]}

    def run_sync(self, state=None):
        if state is not None:
            self.items_file.write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')
        return rf.sync(self.root, str(self.transcript), 'abcd1234-session')

    def test_sync_writes_goal_plan_and_reads_only_new_messages(self):
        self.talk(4)
        path = self.run_sync(self.STATE)
        state = rf.read_session(path)
        self.assertEqual((state['goal'], state['doing'], state['upto']), ('做一个记账工具', '写导入功能', 4))
        self.assertEqual([(s['title'], s['status'], s['note'], s['at']) for s in state['plan']], [('定数据结构', 'done', '', 1), ('导入账单', 'doing', '只支持 CSV', 3)])
        self.assertEqual(state['changes'], [{'text': '先不做多币种', 'at': 2, 'when': ''}])
        self.items_file.write_text('不是 JSON', encoding='utf-8')
        self.assertEqual(self.run_sync(), path)
        self.talk(6)
        self.assertEqual(self.run_sync(dict(self.STATE, doing='写报表')), path)
        self.assertEqual(rf.read_session(path)['upto'], 6)
        self.assertEqual(rf.collect(self.root)['sessions'][0]['doing'], '写报表')

    def test_plan_and_changes_carry_the_time_of_their_message(self):
        rows = [line('user' if i % 2 == 0 else 'assistant', f'第 {i + 1} 句', timestamp=f'2026-03-0{i + 1}T08:30:00.000Z') for i in range(4)]
        self.transcript.write_text('\n'.join(rows) + '\n', encoding='utf-8')
        with mock.patch.dict(os.environ, {'TZ': 'UTC'}):
            __import__('time').tzset()
            state = rf.read_session(self.run_sync(self.STATE))
        self.assertEqual(state['changes'], [{'text': '先不做多币种', 'at': 2, 'when': '2026-03-02 08:30'}])
        self.assertEqual([s['when'] for s in state['plan']], ['2026-03-01 08:30', '2026-03-03 08:30'])
        self.assertEqual(state['plan'][1]['note'], '只支持 CSV')

    def test_name_and_end_survive_later_syncs(self):
        self.talk(4)
        path = self.run_sync(dict(self.STATE, title='记账工具'))
        self.assertEqual(rf.read_session(path)['title'], '记账工具')
        self.items_file.write_text('不是 JSON', encoding='utf-8')
        rf.sync(self.root, str(self.transcript), 'abcd1234-session', name='我的账本')
        rf.sync(self.root, str(self.transcript), 'abcd1234-session', end=True)
        state = rf.read_session(path)
        self.assertEqual((state['title'], state['named'], state['ended'], state['upto']), ('我的账本', 'user', rf.today(), 4))
        self.assertEqual(state['plan'][1]['note'], '只支持 CSV')
        self.talk(6)
        state = rf.read_session(self.run_sync(dict(self.STATE, title='模型换的名字')))
        self.assertEqual((state['title'], state['ended'], state['upto']), ('我的账本', None, 6))

    @in_repo_layout
    def test_sessions_of_other_worktrees_are_listed_and_synced_in_place(self):
        def run_git(*args):
            subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@example.com', *args], cwd=self.root, check=True, capture_output=True)

        run_git('init', '-q', '-b', 'main')
        run_git('add', '-A')
        run_git('commit', '-qm', 'init')
        other = Path(self.tmp.name) / 'other'
        run_git('worktree', 'add', '-q', str(other), '-b', 'feat')
        (other / 'note.txt').write_text('x', encoding='utf-8')
        subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@example.com', 'add', '-A'], cwd=other, check=True, capture_output=True)
        subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@example.com', 'commit', '-qm', 'work'], cwd=other, check=True, capture_output=True)
        self.talk(4)
        self.run_sync(dict(self.STATE, title='第一件事'))
        second = Path(self.tmp.name) / 'ffff0000-second.jsonl'
        second.write_text(line('user', '另一个会话') + '\n', encoding='utf-8')
        self.items_file.write_text(json.dumps(dict(self.STATE, title='第二件事'), ensure_ascii=False), encoding='utf-8')
        server = rf.serve(self.root, 0)
        __import__('threading').Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(rf.sync_command(other.resolve(), 'ffff0000-second', str(second), server.server_address[1], False), 0)
        self.assertIn(f'/#{rf.session_tag("ffff0000-second")}', out.getvalue())
        self.assertEqual(len(list((other / rf.DIR / 'sessions').glob('*.md'))), 0)  # a session in another worktree is written into the main checkout
        self.assertEqual(len(list((self.root / rf.DIR / 'sessions').glob('*.md'))), 2)
        data = rf.collect(self.root)
        self.assertEqual({(x['title'], x['worktree'], x['branch']) for x in data['sessions']}, {('第一件事', '', 'main'), ('第二件事', '', 'feat')})
        self.assertEqual([(b['name'], b.get('worktree')) for b in data['now']['branches']], [('feat', 'other')])

    def test_modules_a_session_touches_are_kept_when_they_exist(self):
        self.talk(2)
        rf.apply_item(self.root, {'kind': 'structure', 'op': 'add-module', 'title': '账单', 'stated_by': 'user'}, 'S-test#1')
        self.talk(4)
        rf.apply_item(self.root, {'kind': 'structure', 'op': 'add-feature', 'title': '导入', 'module': '报表', 'one_liner': '导入账单。', 'stated_by': 'user'}, 'S-test#3')
        state = rf.read_session(self.run_sync(dict(self.STATE, modules=['M-01', 'M-99', 'M-01'], features=['RF-101', 'RF-999'])))
        self.assertEqual((state['modules'], state['features']), (['M-01', 'M-02'], ['RF-101']))
        self.talk(6)
        self.assertEqual(rf.read_session(self.run_sync(dict(self.STATE, modules=['M-01'])))['features'], [])
        self.assertEqual(rf.collect(self.root)['sessions'][0]['modules'], ['M-01'])

    def test_a_bad_answer_keeps_the_previous_state(self):
        self.talk(4)
        path = self.run_sync(self.STATE)
        self.talk(6)
        with self.assertRaises(ValueError):
            self.run_sync({'items': []})
        self.assertEqual(rf.read_session(path)['upto'], 4)

    def test_server_serves_the_page_and_syncs(self):
        import threading
        import urllib.request
        self.talk(4)
        self.items_file.write_text(json.dumps(self.STATE, ensure_ascii=False), encoding='utf-8')
        server = rf.serve(self.root, 0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = f'http://127.0.0.1:{server.server_address[1]}'
        with redirect_stdout(io.StringIO()) as out:
            code = rf.sync_command(self.root, 'abcd1234-session', str(self.transcript), server.server_address[1], False)
        self.assertEqual(code, 0)
        self.assertIn('由本机服务处理', out.getvalue())
        self.assertIn('- [~] 导入账单｜只支持 CSV', out.getvalue())
        page = urllib.request.urlopen(base + '/').read().decode('utf-8')
        self.assertIn('做一个记账工具', page)
        tag = rf.load_sessions(self.root)[0]['id']
        self.assertIn('## 计划', urllib.request.urlopen(f'{base}/api/sessions/{tag}.md').read().decode('utf-8'))
        self.assertEqual(json.loads(urllib.request.urlopen(base + '/api/state').read())['sessions'][0]['id'], tag)

    def test_the_server_only_answers_this_machine_and_sync_only_takes_local_json(self):
        import http.client
        import threading
        server = rf.serve(self.root, 0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        port = server.server_address[1]

        def ask(method, path, headers=None, body=None):
            conn = http.client.HTTPConnection('127.0.0.1', port, timeout=10)
            conn.request(method, path, body=body, headers=headers or {})
            reply = conn.getresponse()
            status = reply.status
            reply.read()
            conn.close()
            return status
        self.assertEqual(ask('GET', '/api/state', {'Host': f'localhost:{port}'}), 200)
        # DNS 重绑定：网页把自己的域名指到 127.0.0.1，Host 里还是它的域名
        self.assertEqual(ask('GET', '/api/state', {'Host': f'evil.example:{port}'}), 421)
        sync = json.dumps({'transcript': str(self.transcript), 'session_id': 'x'})
        # 网页不经预检能发的只有表单类的 Content-Type
        self.assertEqual(ask('POST', '/api/sync', {'Content-Type': 'text/plain'}, sync), 415)
        self.assertEqual(ask('POST', '/api/sync', {'Content-Type': 'application/json', 'Origin': 'https://evil.example'}, sync), 403)
        self.assertEqual(ask('POST', '/api/sync', {'Content-Type': 'application/json', 'Content-Length': str(64 << 20)}), 413)

    def test_sync_falls_back_to_local_and_the_skill_is_installed(self):
        self.talk(2)
        self.items_file.write_text(json.dumps(self.STATE, ensure_ascii=False), encoding='utf-8')
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(rf.sync_command(self.root, 'abcd1234-session', str(self.transcript), 1, False), 0)
        self.assertIn('本机服务没有运行', out.getvalue())
        self.assertIn('rf.py sync', (self.root / rf.DIR / 'CONVENTIONS.md').read_text(encoding='utf-8'))  # 数据外置：技能的做法在约定里


class RequirementTests(Base):
    def setUp(self):
        super().setUp()
        env = mock.patch.dict(os.environ, {'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@example.com', 'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@example.com'})
        env.start()
        self.addCleanup(env.stop)
        for args in (['init', '-q', '-b', 'main'], ['add', '-A'], ['commit', '-qm', 'init']):
            subprocess.run(['git', *args], cwd=self.root, check=True, capture_output=True)

    @in_repo_layout
    def test_a_new_requirement_gets_its_own_branch_and_directory(self):
        made = rf.req_new(self.root, ' 导出  账单 ')
        self.assertEqual((made['title'], made['state'], made['sessions'], made['level']), ('导出 账单', 'draft', [], 'idea'))
        self.assertTrue(made['branch'].startswith('req/'))
        place = Path(made['worktree'])
        self.assertEqual(place.parent, self.root.resolve() / rf.TREES)
        self.assertEqual(rf.branch_of(place), made['branch'])
        self.assertTrue((self.root / made['file']).exists())
        long = rf.req_new(self.root, '很长的一句话' * 30)
        self.assertLess(len(Path(long['file']).name), 60)
        self.assertEqual(long['title'], '很长的一句话' * 30)
        self.assertEqual(rf.git(place, 'status', '--porcelain'), '')
        # the requirement file is in the main checkout, not on the branch; it is committed with the other facts later
        self.assertEqual(rf.git(self.root, 'status', '--porcelain', '-uall'), ''.join(f'?? {r["file"]}\n' for r in sorted([made, long], key=lambda r: r['file'])))
        self.assertNotIn(made['file'], rf.git(self.root, 'ls-tree', '-r', '--name-only', made['branch']))
        self.assertEqual(rf.branch_of(self.root), 'main')

    def test_requirements_are_listed_from_any_checkout_and_collect_sessions(self):
        first, second = rf.req_new(self.root, '导出'), rf.req_new(self.root, '对账')
        self.assertEqual(rf.req_attach(self.root, first['id'], 'aaaa-1'), rf.session_tag('aaaa-1'))
        self.assertTrue(rf.req_attach(Path(second['worktree']), first['id'], 'bbbb-2'))
        self.assertTrue(rf.req_attach(self.root, first['id'], 'aaaa-1'))
        for place in (self.root, Path(second['worktree'])):
            found = {r['title']: r for r in rf.load_requirements(place)}
            self.assertEqual(sorted(found), ['对账', '导出'])
            self.assertEqual(found['导出']['sessions'], ['aaaa-1', 'bbbb-2'])
            self.assertEqual(found['导出']['worktree'], first['worktree'])
        self.assertFalse(rf.req_attach(self.root, 'R-none', 'x'))

    def write(self, made, head, *lines):
        path = self.root / made['file']
        meta, sections = rf.read_doc(path)
        for text in lines:
            rf.add_line(sections, head, text)
        rf.write_doc(path, meta, sections)

    def start(self, made):
        self.write(made, '验收条件', '- 能导出〔S-test#1〕')
        return rf.req_start(self.root, made['id'])

    def commit_in(self, place, name, text, message='work'):
        (Path(place) / name).parent.mkdir(parents=True, exist_ok=True)
        (Path(place) / name).write_text(text, encoding='utf-8')
        for args in (['add', '-A'], ['commit', '-qm', message]):
            subprocess.run(['git', *args], cwd=place, check=True, capture_output=True)

    @in_repo_layout
    def test_confirming_a_requirement_merges_it_and_says_what_it_brought(self):
        made = rf.req_new(self.root, '导出')
        place = Path(made['worktree'])
        self.start(made)
        rf.apply_item(place, {'kind': 'structure', 'op': 'add-feature', 'title': '导出账单', 'module': '账单', 'one_liner': '导出。', 'stated_by': 'user'}, 'S-test#1', made['id'])
        self.assertEqual(rf.read_doc(next((self.root / rf.DIR / 'features').glob('RF-101-*.md')))[0]['in_progress'], made['id'])  # in the main checkout, still in progress
        self.commit_in(place, 'export.py', 'print(1)\n')
        (place / rf.DIR / 'sessions').mkdir(exist_ok=True)
        (place / rf.DIR / 'sessions' / 'S-x.md').write_text('---\nid: S-x\ntype: session\n---\n', encoding='utf-8')  # the stale copy in the worktree does not count
        done = rf.req_done(self.root, made['id'])
        self.assertEqual((done['state'], done['worktree']), ('done', None))
        self.assertEqual(done['completed_on'], done['updated'])
        self.assertTrue((self.root / 'export.py').exists())
        self.assertFalse(place.exists())
        self.assertEqual(rf.git(self.root, 'branch', '--list', made['branch']), '')
        self.assertEqual(rf.git(self.root, 'status', '--porcelain'), '')
        record = ' '.join(done['sections']['变更记录'])
        self.assertIn('用户实际操作后确认完成', record)
        self.assertIn('新增：模块 M-01 账单；功能点 RF-101 导出账单', record)
        self.assertIn('RF-101', {m['id'] for _, m, _ in rf.all_docs(self.root, ('features',))})
        with self.assertRaisesRegex(ValueError, '已经确认完成'):
            rf.req_done(self.root, made['id'])

    def test_unrelated_changes_in_the_project_directory_do_not_block_confirming(self):
        made = rf.req_new(self.root, '导出')
        place = Path(made['worktree'])
        self.start(made)
        self.commit_in(self.root, 'notes.txt', 'v1\n')
        self.commit_in(self.root, 'shared.py', 'v1\n')
        subprocess.run(['git', 'merge', '-q', 'main'], cwd=place, check=True, capture_output=True)
        self.commit_in(place, 'shared.py', 'v2\n')
        (self.root / 'notes.txt').write_text('editing\n', encoding='utf-8')
        (self.root / 'shared.py').write_text('editing\n', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '合并会覆盖：shared.py'):
            rf.req_done(self.root, made['id'])
        subprocess.run(['git', 'checkout', '--', 'shared.py'], cwd=self.root, check=True, capture_output=True)
        done = rf.req_done(self.root, made['id'])
        self.assertEqual(done['state'], 'done')
        self.assertEqual((self.root / 'shared.py').read_text(encoding='utf-8'), 'v2\n')
        self.assertEqual((self.root / 'notes.txt').read_text(encoding='utf-8'), 'editing\n')

    def test_a_requirement_is_not_confirmed_while_work_is_loose_or_clashes(self):
        made = rf.req_new(self.root, '导出')
        place = Path(made['worktree'])
        with self.assertRaisesRegex(ValueError, '还在立项'):
            rf.req_done(self.root, made['id'])
        self.start(made)
        with self.assertRaisesRegex(ValueError, '还没有任何改动'):
            rf.req_done(self.root, made['id'])
        (place / 'draft.py').write_text('x\n', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '1 个文件的改动没有提交'):
            rf.req_done(self.root, made['id'])
        self.commit_in(place, 'draft.py', 'theirs\n')
        self.commit_in(self.root, 'draft.py', 'mine\n')
        with self.assertRaisesRegex(rf.NeedsSync, '合并会冲突（draft.py）'):
            rf.req_done(self.root, made['id'])
        self.assertEqual(rf.load_requirements(self.root)[0]['state'], 'open')
        self.assertEqual(rf.git(place, 'status', '--porcelain'), '')
        subprocess.run(['git', 'checkout', '-q', '--detach'], cwd=self.root, check=True, capture_output=True)
        with self.assertRaisesRegex(ValueError, '不在任何分支上'):
            rf.req_done(self.root, made['id'])

    def test_an_uncommitted_new_file_with_the_same_name_blocks_before_anything_is_marked_done(self):
        made = rf.req_new(self.root, '图标')
        place = Path(made['worktree'])
        self.start(made)
        self.commit_in(place, 'icon/icon.png', 'png\n')
        self.commit_in(place, 'brand.py', 'icon = 1\n')
        (self.root / 'icon').mkdir()
        (self.root / 'icon' / 'icon.png').write_text('png\n', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '还没提交进 Git.*icon/icon.png'):
            rf.req_done(self.root, made['id'])
        self.assertEqual(rf.load_requirements(self.root)[0]['state'], 'open')
        self.assertEqual(rf.git(place, 'status', '--porcelain'), '')
        self.commit_in(self.root, 'icon/icon.png', 'png\n')
        # main moved on without a code conflict: the program merges it into the branch itself, then lands
        self.assertEqual(rf.req_done(self.root, made['id'])['state'], 'done')

    def test_a_failed_merge_takes_back_the_done_mark(self):
        made = rf.req_new(self.root, '导出')
        place = Path(made['worktree'])
        self.start(made)
        self.commit_in(place, 'export.py', 'print(1)\n')
        head = rf.git(place, 'rev-parse', 'HEAD').strip()
        from relayforge.requirements import record as requirements
        real = requirements.run_git
        def failing(where, *args):
            if args[:1] == ('merge',) and '--no-ff' in args:
                return subprocess.CompletedProcess(args, 1, '', 'error: boom')
            return real(where, *args)
        with mock.patch.object(requirements, 'run_git', failing):
            with self.assertRaisesRegex(ValueError, '合并进 main 时失败'):
                rf.req_done(self.root, made['id'])
        self.assertEqual(rf.git(place, 'rev-parse', 'HEAD').strip(), head)
        self.assertEqual(rf.load_requirements(self.root)[0]['state'], 'open')
        self.assertEqual(rf.req_done(self.root, made['id'])['state'], 'done')

    def test_a_requirement_left_marked_done_but_unmerged_can_be_confirmed_again(self):
        made = rf.req_new(self.root, '导出')
        place = Path(made['worktree'])
        self.start(made)
        self.commit_in(place, 'export.py', 'print(1)\n')
        path = self.root / made['file']
        meta, sections = rf.read_doc(path)
        meta.update(state='done', updated='2026-11-18')
        rf.add_line(sections, '变更记录', '- 2026-11-16 用户实际操作后确认完成，合并进 main。')
        rf.write_doc(path, meta, sections)
        done = rf.req_done(self.root, made['id'])
        self.assertEqual((done['state'], done['worktree']), ('done', None))
        self.assertEqual(done['completed_on'], '2026-11-16')
        self.assertTrue((self.root / 'export.py').exists())
        self.assertEqual(' '.join(done['sections']['变更记录']).count('用户实际操作后确认完成'), 1)
        with self.assertRaisesRegex(ValueError, '已经确认完成'):
            rf.req_done(self.root, made['id'])

    def test_a_requirement_merges_into_whatever_branch_the_project_directory_is_on(self):
        subprocess.run(['git', 'checkout', '-q', '-b', 'dev'], cwd=self.root, check=True, capture_output=True)
        self.commit_in(self.root, 'dev.txt', 'only on dev\n')
        made = rf.req_new(self.root, '导出')
        place = Path(made['worktree'])
        self.assertTrue((place / 'dev.txt').exists())  # new requirements branch off the project directory's branch
        self.start(made)
        self.commit_in(place, 'export.py', 'print(1)\n')
        done = rf.req_done(self.root, made['id'])
        self.assertIn('合并进 dev', ' '.join(done['sections']['变更记录']))
        self.assertEqual(rf.branch_of(self.root), 'dev')
        self.assertTrue((self.root / 'export.py').exists())
        self.assertNotIn('export.py', rf.git(self.root, 'ls-tree', '-r', '--name-only', 'main'))

    def test_a_conflict_is_reported_with_its_files_for_the_app_to_hand_to_the_ai(self):
        made = rf.req_new(self.root, '导出')
        place = Path(made['worktree'])
        self.start(made)
        self.commit_in(place, 'shared.py', 'theirs\n')
        self.commit_in(self.root, 'shared.py', 'mine\n')
        with self.assertRaises(rf.NeedsSync) as caught:
            rf.req_done(self.root, made['id'])
        self.assertEqual((caught.exception.base, caught.exception.files), ('main', ['shared.py']))
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(as_app(['req', 'done', made['id'], '--root', str(self.root), '--json']), 3)
        told = json.loads(out.getvalue())
        self.assertEqual((told['sync'], told['conflict'], told['base'], told['files']), (True, True, 'main', ['shared.py']))
        # the AI merges the target branch in and resolves it in the requirement's own directory; then it goes through
        subprocess.run(['git', 'merge', '-q', 'main'], cwd=place, capture_output=True)
        (place / 'shared.py').write_text('both\n', encoding='utf-8')
        subprocess.run(['git', 'commit', '-qam', 'resolve'], cwd=place, check=True, capture_output=True)
        self.assertEqual(rf.req_done(self.root, made['id'])['state'], 'done')
        self.assertEqual((self.root / 'shared.py').read_text(encoding='utf-8'), 'both\n')

    def test_nothing_lands_until_the_branch_has_the_latest_target_and_its_work_committed(self):
        made = rf.req_new(self.root, '导出')
        place = Path(made['worktree'])
        self.start(made)
        self.commit_in(place, 'export.py', 'print(1)\n')
        self.commit_in(self.root, 'other.py', 'x\n')  # the target moved on, no conflict
        (place / 'more.py').write_text('y\n', encoding='utf-8')  # and something is not committed yet
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(as_app(['req', 'done', made['id'], '--root', str(self.root), '--json']), 3)
        told = json.loads(out.getvalue())
        self.assertEqual((told['sync'], told['conflict'], told['files'], told['loose']), (True, False, [], ['more.py']))
        self.assertEqual(rf.find_requirement(self.root, made['id'])['state'], 'open')
        self.assertFalse((self.root / 'export.py').exists())
        subprocess.run(['git', 'add', '-A'], cwd=place, check=True)
        subprocess.run(['git', 'commit', '-qm', 'more'], cwd=place, check=True)
        # 改动都提交了、目标分支合进来也没有代码冲突：程序自己在工作目录里合，不再回到会话
        self.assertEqual(rf.req_done(self.root, made['id'])['state'], 'done')
        self.assertTrue((self.root / 'more.py').exists())
        self.assertTrue((self.root / 'other.py').exists())

    def test_only_one_requirement_merges_at_a_time(self):
        made = rf.req_new(self.root, '导出')
        self.start(made)
        self.commit_in(made['worktree'], 'export.py', 'print(1)\n')
        with rf.take_merge_lock(made['worktree'], 'R-other'):
            with self.assertRaisesRegex(rf.MergeBusy, 'R-other 正在合并'):
                rf.req_done(self.root, made['id'])
            with redirect_stdout(io.StringIO()) as out:
                self.assertEqual(as_app(['req', 'done', made['id'], '--root', str(self.root), '--json']), 4)
        self.assertEqual((json.loads(out.getvalue())['busy'], json.loads(out.getvalue())['holder']), (True, 'R-other'))
        # Leftover metadata, even if it names a live PID, does not hold the OS lock.
        rf.merge_lock_path(self.root).write_text(json.dumps({'id': 'R-old', 'pid': os.getpid()}), encoding='utf-8')
        self.assertEqual(rf.req_done(self.root, made['id'])['state'], 'done')

    def test_the_command_reports_what_it_made(self):
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(rf.main(['req', 'new', '导出', '--root', str(self.root), '--json']), 0)
            self.assertEqual(rf.main(['req', 'list', '--root', str(self.root), '--json']), 0)
        made, listed = (json.loads(x) for x in out.getvalue().strip().splitlines())
        self.assertEqual([r['id'] for r in listed], [made['id']])
        with redirect_stdout(io.StringIO()), mock.patch('sys.stderr', io.StringIO()) as err:
            self.assertEqual(rf.main(['req', 'new', '--root', str(self.root)]), 1)
        self.assertIn('标题', err.getvalue())


class IntakeTests(RequirementTests):
    """立项：清晰度、程序算出的影响、只有用户能过的那道门。"""

    def facts(self):
        base = self.root / rf.DIR
        rf.write_doc(base / 'modules' / 'M-01-账单.md', {'id': 'M-01', 'type': 'module', 'title': '账单', 'status': 'current'}, {'负责什么': [], '变更记录': []})
        rf.write_doc(base / 'modules' / 'M-02-报表.md', {'id': 'M-02', 'type': 'module', 'title': '报表', 'status': 'current'}, {'负责什么': [], '变更记录': []})
        for ident, title, module, deps in (('RF-101', '账单列表', 'M-01', []), ('RF-102', '导出账单', 'M-01', ['RF-101']),
                                           ('RF-103', '月度报表', 'M-02', ['RF-101']), ('RF-104', '年度报表', 'M-02', ['RF-103'])):
            rf.write_doc(base / 'features' / f'{ident}-{title}.md', {'id': ident, 'type': 'feature', 'title': title, 'status': 'current', 'module': module, 'depends_on': deps},
                         {h: [] for h in rf.FEATURE_HEADS})
        for ident, title, applies, status in (('D-0001', '金额用分', ['M-01'], 'current'), ('D-0002', '列表分页', ['RF-101'], 'current'),
                                              ('D-0003', '报表只读', ['M-02'], 'current'), ('D-0004', '旧的', ['RF-101'], 'superseded')):
            rf.write_doc(base / 'decisions' / f'{ident}-{title}.md', {'id': ident, 'type': 'decision', 'title': title, 'status': status, 'applies_to': applies},
                         {'决定': [f'- {title}的决定。〔S-x#1〕'], '理由': [], '没有选的方案': [], '变更记录': []})
        for args in (['add', '-A'], ['commit', '-qm', 'facts']):
            subprocess.run(['git', *args], cwd=self.root, check=True, capture_output=True)

    def find(self, made):
        return next(r for r in rf.load_requirements(self.root) if r['id'] == made['id'])

    def test_clarity_climbs_from_idea_to_acceptance(self):
        made = rf.req_new(self.root, '导出')
        self.assertEqual([(l['name'], l['reached'], l['later']) for l in made['ladder']],
                         [('想法', True, False), ('方向', False, False), ('方案', False, False), ('可验收', False, False), ('用例', False, False)])
        self.assertEqual(made['ladder'][1]['missing'], ['需求说明', '要改的功能点'])
        self.assertFalse(made['gate']['ok'])
        self.assertIn('验收条件', made['gate']['missing'])
        self.write(made, '需求说明', '- 财务要按月导出〔S-x#1〕')
        self.assertEqual(self.find(made)['level'], 'idea')
        self.assertEqual(self.find(made)['ladder'][1]['missing'], ['要改的功能点'])
        self.write(made, '要改的功能点', '- 〔待确认〕新增：导出账单〔S-x#2〕')
        self.assertEqual(self.find(made)['level'], 'direction')
        self.write(made, '验收条件', '- 〔已否决〕能导出 PDF〔S-x#3〕')
        self.assertEqual((self.find(made)['level'], self.find(made)['gate']['ok']), ('direction', False))
        self.write(made, '验收条件', '- Excel 能打开〔S-x#4〕')
        found = self.find(made)
        self.assertEqual((found['level'], found['gate']['ok'], found['gate']['missing']), ('direction', True, ''))  # 方案 may be skipped by the gate, not by the ladder
        self.assertEqual(found['gate']['uncovered'], [{'n': 1, 'text': 'Excel 能打开'}])  # 没有用例：只提示，不拦
        self.assertIn('没有测试用例', found['gate']['hint'])
        self.assertEqual(found['ladder'][2]['missing'], ['设计稿（没有界面就说明理由跳过）'])
        self.assertTrue(found['ladder'][3]['reached'])
        rf.design_skip(Path(made['worktree']), '只改导出的文件，没有界面')
        found = self.find(made)
        self.assertEqual(found['level'], 'acceptance')
        self.assertEqual((found['ladder'][2]['reached'], found['ladder'][2]['skipped']), (True, '只改导出的文件，没有界面'))

    def test_acceptance_without_a_direction_does_not_skip_the_ladder(self):
        made = rf.req_new(self.root, '导出')
        self.write(made, '验收条件', '- Excel 能打开〔S-x#1〕')
        found = self.find(made)
        self.assertEqual((found['level'], found['gate']['ok']), ('idea', True))
        self.assertTrue(found['ladder'][3]['reached'])

    @in_repo_layout
    def test_the_brief_names_what_exists_what_depends_on_it_and_which_decisions_apply(self):
        self.facts()
        made = rf.req_new(self.root, '导出')
        self.write(made, '要改的功能点', '- RF-101 账单列表：加筛选〔S-x#1〕', '- 〔待确认〕RF-999 不存在的〔S-x#2〕',
                   '- 新增：导出 PDF（属于 M-01）〔S-x#3〕', '- 新增：随便什么〔S-x#4〕', '- 〔已否决〕RF-104 年度报表〔S-x#5〕')
        b = self.find(made)['brief']
        self.assertEqual([(k['id'], k['title'], k['kind'], k['module']) for k in b['known']], [('RF-101', '账单列表', 'feature', 'M-01'), ('M-01', '账单', 'module', '')])
        self.assertEqual(b['unknown'], ['RF-999'])
        self.assertEqual(b['proposed'], [{'text': '新增：导出 PDF（属于 M-01）', 'module': 'M-01'}, {'text': '新增：随便什么', 'module': ''}])
        self.assertEqual([(d['id'], d['on']) for d in b['dependents']], [('RF-102', ['RF-101']), ('RF-103', ['RF-101'])])
        self.assertEqual([(d['id'], d['decision'], d['applies_to']) for d in b['decisions']],
                         [('D-0001', '金额用分的决定。', ['M-01']), ('D-0002', '列表分页的决定。', ['RF-101'])])

    @in_repo_layout
    def test_the_brief_command_answers_inside_the_requirement_directory(self):
        self.facts()
        made = rf.req_new(self.root, '报表')
        self.write(made, '要改的功能点', '- RF-103 月度报表：加一列〔S-x#1〕')
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(rf.main(['req', 'brief', '--root', made['worktree']]), 0)
        text = out.getvalue()
        for part in ('RF-103 月度报表（属于 M-02）', 'RF-104 年度报表：依赖 RF-103', 'D-0003 报表只读：报表只读的决定。', '清晰度：想法'):
            self.assertIn(part, text)
        self.assertNotIn('D-0001', text)
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(rf.main(['req', 'brief', made['id'], '--root', str(self.root), '--json']), 0)
        data = json.loads(out.getvalue())
        self.assertEqual([d['id'] for d in data['brief']['dependents']], ['RF-104'])
        with redirect_stdout(io.StringIO()), mock.patch('sys.stderr', io.StringIO()) as err:
            self.assertEqual(rf.main(['req', 'brief', '--root', str(self.root)]), 1)
        self.assertIn('不属于任何需求', err.getvalue())

    @in_repo_layout
    def test_only_an_acceptance_condition_opens_the_gate(self):
        made = rf.req_new(self.root, '导出')
        rf.req_attach(self.root, made['id'], 'aaaa-1')
        with self.assertRaisesRegex(ValueError, '还不能立项：还没有验收条件'):
            rf.req_start(self.root, made['id'])
        self.write(made, '验收条件', '- 〔已否决〕能导出 PDF〔S-x#1〕')
        with self.assertRaisesRegex(ValueError, '还不能立项'):
            rf.req_start(self.root, made['id'])
        self.assertEqual(self.find(made)['state'], 'draft')
        started = self.start(made)
        self.assertEqual((started['state'], started['intake'], started['sessions']), ('open', ['aaaa-1'], ['aaaa-1']))
        self.assertIn('用户确认立项，开始开发。', ' '.join(started['sections']['变更记录']))
        place = Path(made['worktree'])
        self.assertEqual(rf.git(place, 'status', '--porcelain'), '')
        self.assertIn('需求立项：导出', rf.git(self.root, 'log', '-1', '--format=%s'))  # 立项 commits the facts, in the main checkout
        self.assertEqual(rf.git(self.root, 'status', '--porcelain'), '')
        with self.assertRaisesRegex(ValueError, '已经立项'):
            rf.req_start(self.root, made['id'])
        with self.assertRaisesRegex(ValueError, '没有找到需求'):
            rf.req_start(self.root, 'R-none')
        with redirect_stdout(io.StringIO()), mock.patch('sys.stderr', io.StringIO()) as err:
            self.assertEqual(as_app(['req', 'start', made['id'], '--root', str(self.root)]), 1)
        self.assertIn('已经立项', err.getvalue())

    @in_repo_layout
    def test_dropping_a_draft_keeps_what_was_said_on_its_branch(self):
        made = rf.req_new(self.root, '导出')
        place = Path(made['worktree'])
        self.talk(1)
        rf.note(place, 'abcd1234-session', self.transcript, 'say', ['财务要按月导出'])  # left uncommitted, as the intake leaves it
        (place / 'scratch.py').write_text('x\n', encoding='utf-8')
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(as_app(['req', 'drop', made['id'], '--root', str(self.root)]), 0)
        self.assertIn('已放弃', out.getvalue())
        self.assertFalse(place.exists())
        cancelled = rf.load_requirements(self.root)
        self.assertEqual(len(cancelled), 1)
        self.assertEqual((cancelled[0]['id'], cancelled[0]['state'], cancelled[0]['worktree']), (made['id'], 'dropped', None))
        self.assertIn('财务要按月导出', cancelled[0]['sections']['需求说明'])
        self.assertFalse(rf.req_attach(self.root, made['id'], 'new-session'))
        self.assertEqual(rf.git(self.root, 'branch', '--list', made['branch']).strip(), made['branch'])
        kept = rf.git(self.root, 'show', f'HEAD:{made["file"]}')  # the record stays in the main checkout, committed
        self.assertIn('state: dropped', kept)
        self.assertIn('财务要按月导出', kept)
        self.assertIn('用户放弃了这个需求', kept)
        self.assertIn('连同 1 个没提交的代码改动', kept)
        self.assertEqual(rf.git(self.root, 'show', f'{made["branch"]}:scratch.py'), 'x\n')  # loose code is kept on the branch, not lost
        self.assertNotIn(rf.DIR, rf.git(self.root, 'show', '--name-only', '--format=', made['branch']))  # and only code goes there
        self.assertIn(f'{rf.DIR}/sessions/', rf.git(self.root, 'show', '--name-only', '--format=', 'HEAD'))
        self.assertIn('放弃需求：导出', rf.git(self.root, 'log', '-1', '--format=%s'))
        self.assertIn('放弃需求：导出（没提交的代码改动）', rf.git(self.root, 'log', '-1', '--format=%s', made['branch']))
        self.assertEqual(rf.git(self.root, 'status', '--porcelain'), '')
        with self.assertRaisesRegex(ValueError, '不能放弃'):
            rf.req_drop(self.root, made['id'])
        with self.assertRaisesRegex(ValueError, '不能立项'):
            rf.req_start(self.root, made['id'])
        with self.assertRaises(ValueError):
            rf.req_done(self.root, made['id'])

    def test_cancelled_records_survive_reload_and_override_inherited_copies(self):
        made = rf.req_new(self.root, '取消后保留')
        rf.req_attach(self.root, made['id'], 'abcd1234-session')
        place = Path(made['worktree'])
        self.talk(1)
        rf.note(place, 'abcd1234-session', self.transcript, 'say', ['只读内容'])
        # An inherited copy in main must not hide the cancellation on its own branch.
        target = self.root / made['file']
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text((self.root / made['file']).read_text(encoding='utf-8'), encoding='utf-8')
        other = rf.req_new(self.root, '另一项需求')
        result = rf.req_drop(self.root, made['id'])
        self.assertEqual((result['state'], result['sections']['需求说明']), ('dropped', ['只读内容']))
        for checkout in (self.root, Path(other['worktree'])):
            loaded = [r for r in rf.load_requirements(checkout) if r['id'] == made['id']]
            self.assertEqual(len(loaded), 1)
            self.assertEqual((loaded[0]['state'], loaded[0]['worktree']), ('dropped', None))
            self.assertEqual(loaded[0]['sessions'], ['abcd1234-session'])

    def test_a_started_requirement_can_be_dropped_but_a_finished_one_cannot(self):
        made = rf.req_new(self.root, '导出')
        self.start(made)
        self.commit_in(made['worktree'], 'export.py', 'print(1)\n')
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(as_app(['req', 'drop', made['id'], '--root', str(self.root)]), 0)
        self.assertIn('已放弃', out.getvalue())
        self.assertFalse(Path(made['worktree']).exists())
        self.assertIn('export.py', rf.git(self.root, 'ls-tree', '-r', '--name-only', made['branch']))
        self.assertIn('已经提交的代码', (self.root / made['file']).read_text(encoding='utf-8'))

    def test_the_stage_is_changed_by_hand_under_strict_rules(self):
        made = rf.req_new(self.root, '导出')
        loaded = rf.find_requirement(self.root, made['id'])
        self.assertEqual(loaded['stage'], 'talk')
        self.assertIn('验收条件', loaded['moves']['build'])
        self.assertIsNone(loaded['moves']['dropped'])
        with self.assertRaisesRegex(ValueError, '还没有验收条件'):
            rf.req_set(self.root, made['id'], 'build')
        self.write(made, '验收条件', '- 能导出〔S-test#1〕')
        moved = rf.req_set(self.root, made['id'], 'check', trusted=True)  # 立项中直接到待验收：先立项，再标待验收（跳过测试，要应用的标记）
        self.assertEqual((moved['state'], moved['stage']), ('open', 'check'))
        self.assertIn('已立项的需求不能退回立项中', moved['moves']['talk'])
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(as_app(['req', 'set', made['id'], 'build', '--root', str(self.root)]), 0)
        self.assertIn('现在是进行中', out.getvalue())
        again = rf.find_requirement(self.root, made['id'])
        self.assertEqual((again['state'], again['stage']), ('open', 'build'))
        self.assertIn('用户手动把状态从待验收改为进行中', ' '.join(again['sections']['变更记录']))
        self.assertEqual(rf.git(again['worktree'], 'status', '--porcelain', '--', made['file']), '')
        with self.assertRaisesRegex(ValueError, '不能退回立项中'):
            rf.req_set(self.root, made['id'], 'talk')
        rf.req_set(self.root, made['id'], 'dropped')
        gone = rf.find_requirement(self.root, made['id'])
        self.assertEqual(gone['stage'], 'dropped')
        self.assertTrue(all(gone['moves'].values()))
        with self.assertRaisesRegex(ValueError, '只读'):
            rf.req_set(self.root, made['id'], 'build')

    def test_a_manual_stage_overrides_the_plan(self):
        self.assertEqual(rf.stage_of('open', None, 3, 3), 'check')
        self.assertEqual(rf.stage_of('open', 'build', 3, 3), 'build')
        self.assertEqual(rf.stage_of('open', None, 1, 3), 'build')
        self.assertEqual(rf.stage_of('open', 'check', 0, 0), 'check')
        self.assertEqual(rf.stage_of('draft', 'check'), 'talk')
        self.assertIsNotNone(rf.stage_blocked('done', 'build', True))

    def test_active_requirements_can_be_cancelled_and_restore_their_exact_stage(self):
        made = rf.req_new(self.root, '导出')
        self.start(made)
        rf.req_attach(self.root, made['id'], 'abcd1234-session')
        place = Path(made['worktree'])
        self.commit_in(place, 'export.py', 'print("kept")\n')
        rf.req_set(self.root, made['id'], 'check', trusted=True)
        with mock.patch('relayforge.flows.today', return_value='2026-11-12'):
            cancelled = rf.req_drop(self.root, made['id'])
        self.assertEqual((cancelled['state'], cancelled['previous_state'], cancelled['previous_stage']), ('dropped', 'open', 'check'))
        self.assertEqual(cancelled['dropped_on'], '2026-11-12')
        self.assertFalse(place.exists())
        restored = rf.req_restore(self.root, made['id'])
        self.assertEqual((restored['state'], restored['stage'], restored['sessions']), ('open', 'check', ['abcd1234-session']))
        self.assertEqual(restored['dropped_on'], '')
        self.assertEqual((place / 'export.py').read_text(), 'print("kept")\n')
        self.assertEqual(rf.git(place, 'status', '--porcelain'), '')
        self.assertIn('用户恢复已取消的需求', ' '.join(restored['sections']['变更记录']))
        with self.assertRaisesRegex(ValueError, '只有已取消'):
            rf.req_restore(self.root, made['id'])
        with mock.patch('relayforge.flows.today', return_value='2026-11-18'):
            cancelled_again = rf.req_drop(self.root, made['id'])
        self.assertEqual(cancelled_again['dropped_on'], '2026-11-18')


    def test_restore_legacy_draft_and_refuse_overwriting_existing_directory(self):
        made = rf.req_new(self.root, '旧版取消')
        rf.req_drop(self.root, made['id'])
        place = Path(made['worktree'])
        rf.run_git(self.root, 'worktree', 'add', '-q', str(place), made['branch'])
        meta, sections = rf.read_doc(self.root / made['file'])
        meta.pop('previous_state'); meta.pop('previous_stage')
        rf.write_doc(self.root / made['file'], meta, sections)
        rf.run_git(place, 'add', '--all'); rf.run_git(place, 'commit', '-qm', '模拟旧版取消记录')
        rf.run_git(self.root, 'worktree', 'remove', str(place))
        place.mkdir(parents=True)
        (place / 'keep.txt').write_text('不要覆盖')
        with self.assertRaisesRegex(ValueError, '不会覆盖'):
            rf.req_restore(self.root, made['id'])
        self.assertEqual((place / 'keep.txt').read_text(), '不要覆盖')
        (place / 'keep.txt').unlink(); place.rmdir()
        restored = rf.req_restore(self.root, made['id'])
        self.assertEqual(restored['state'], 'draft')
        rf.req_drop(self.root, made['id'])
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(as_app(['req', 'restore', made['id'], '--root', str(self.root), '--json']), 0)
        self.assertEqual(json.loads(out.getvalue())['state'], 'draft')

    def test_requirements_written_before_the_intake_keep_working(self):
        made = rf.req_new(self.root, '旧需求')
        path = self.root / made['file']
        meta, sections = rf.read_doc(path)
        meta['state'] = 'open'
        rf.write_doc(path, meta, sections)
        found = self.find(made)
        self.assertEqual((found['state'], found['intake'], found['level']), ('open', [], 'idea'))
        with self.assertRaisesRegex(ValueError, '已经立项'):
            rf.req_start(self.root, made['id'])
        with self.assertRaisesRegex(ValueError, '还没有任何改动'):
            rf.req_done(self.root, made['id'])

    def test_the_format_tells_the_ai_to_leave_the_gates_to_the_user(self):
        text = (self.root / rf.DIR / 'FORMAT.md').read_text(encoding='utf-8')
        for part in ('draft 立项中', 'rf.py req brief', 'rf.py req start', 'rf.py req drop', 'dropped 已取消', 'AI 不得自己执行', 'rf.py design save', 'designs/'):
            self.assertIn(part, text)
        rules = (self.root / rf.DIR / 'CONVENTIONS.md').read_text(encoding='utf-8')  # 数据外置的项目：约定在本机记录里
        self.assertIn('rf.py req start（确认立项）', rules)
        self.assertIn('rf.py req drop（放弃）', rules)


PAGE_HTML = """<!doctype html>
<html><head><style>body { font: 14px system-ui } .row { display: flex }</style></head>
<body><header>账单</header><select id="kind"><option>全部</option><option>餐饮</option></select>
<svg viewBox="0 0 10 10"><use href="#dot"/></svg><a href="#top">回到顶部</a>
<script>document.getElementById('kind').addEventListener('change', function () { var x = { a: 'b' }; });</script>
</body></html>"""


class DesignTests(RequirementTests):
    """设计稿：存成事实，立项里画、改，完成后成为功能点现在的样子。"""
    facts, find = IntakeTests.facts, IntakeTests.find

    def draft(self, title='导出'):
        if not (self.root / rf.DIR / 'modules' / 'M-01-账单.md').exists():
            self.facts()
        made = rf.req_new(self.root, title)
        self.write(made, '需求说明', '- 财务要按月导出〔S-x#1〕')
        self.write(made, '要改的功能点', '- RF-101 账单列表：加导出按钮〔S-x#2〕')
        return made, Path(made['worktree'])

    @in_repo_layout
    def test_a_design_is_saved_as_facts_and_revised_in_place(self):
        made, place = self.draft()
        first = rf.design_save(place, PAGE_HTML, '账单列表加导出', ['RF-101', '新增：导出页（属于 M-01）'], note='列表右上角加导出按钮', source='S-x#3')
        self.assertTrue(first['created'])
        self.assertRegex(first['id'], r'^DS-\d{8}-[0-9a-f]{4}$')
        meta, sections = rf.read_doc(self.root / first['file'])  # saved into the main checkout, not the requirement's worktree
        self.assertEqual((meta['type'], meta['status'], meta['requirement'], meta['features'], meta['html']),
                         ('design', 'draft', made['id'], ['RF-101'], f'{first["id"]}.html'))
        self.assertEqual(sections['新增的功能点'], ['- 导出页（属于 M-01）〔S-x#3〕'])
        self.assertEqual(sections['画的是什么'], ['- 列表右上角加导出按钮〔S-x#3〕'])
        self.assertEqual((self.root / first['html']).read_text(encoding='utf-8'), PAGE_HTML + '\n')
        self.assertEqual(first['path'], str(self.root.resolve() / first['html']))
        self.assertFalse((place / first['html']).exists())
        self.assertEqual(rf.git(place, 'status', '--porcelain'), '')
        found = self.find(made)
        self.assertEqual(([d['id'] for d in found['designs']], found['level']), ([first['id']], 'design'))
        self.assertEqual(found['ladder'][2]['designs'], 1)
        again = rf.design_save(place, PAGE_HTML.replace('账单', '账单列表'), '账单列表加导出', [], source='S-x#5')  # same title: the same draft
        self.assertEqual((again['id'], again['created'], again['features'], again['new']), (first['id'], False, ['RF-101'], ['导出页（属于 M-01）']))
        renamed = rf.design_save(place, PAGE_HTML, '导出按钮', ['RF-101'], ident=first['id'])
        self.assertEqual(renamed['id'], first['id'])
        self.assertEqual([p.name for p in sorted((self.root / rf.DIR / 'designs').iterdir())], [f'{first["id"]}-导出按钮.md', f'{first["id"]}.html'])
        self.assertEqual(rf.read_doc(self.root / renamed['file'])[1]['新增的功能点'], ['- 尚未讨论。'])
        # not committed on every save: the facts are committed together at the next moment (a session stopping, 立项)
        self.assertTrue(rf.git(self.root, 'status', '--porcelain', '--', f'{rf.DIR}/designs').strip())
        self.assertTrue(rf.commit_facts(self.root, '记录'))
        self.assertEqual(rf.git(self.root, 'status', '--porcelain'), '')

    @in_repo_layout
    def test_a_design_must_open_offline_and_name_what_it_draws(self):
        made, place = self.draft()
        bad = {
            '': '设计稿是空的',
            'hello': '不像一份 HTML',
            '<div><img src="https://example.com/a.png"></div>': '不能引用外部网址',
            '<style>@import url("//fonts.example.com/x.css");</style><div></div>': '不能引用外部网址',
            '<div style="background: url(http://x.y/z.png)"></div>': '不能引用外部网址',
            '<script>fetch("https://api.example.com/bills")</script><div></div>': '不能引用外部网址',
            '<div></div><script src="app.js"></script>': '不能引用别的文件',
            '<link rel="stylesheet" href="style.css"><div></div>': '不能引用别的文件',
        }
        for html, said in bad.items():
            with self.assertRaisesRegex(ValueError, said):
                rf.design_save(place, html, '标题', ['RF-101'])
        for features, said in ((['RF-999'], 'RF-999 在事实里不存在'), ([], '要说明这份设计稿画的是哪个功能点'), (['随便'], '不是功能点')):
            with self.assertRaisesRegex(ValueError, said):
                rf.design_save(place, PAGE_HTML, '标题', features)
        with self.assertRaisesRegex(ValueError, '要有一个标题'):
            rf.design_save(place, PAGE_HTML, ' ', ['RF-101'])
        with self.assertRaisesRegex(ValueError, '不属于任何需求'):
            rf.design_save(self.root, PAGE_HTML, '标题', ['RF-101'])
        with self.assertRaisesRegex(ValueError, '没有找到设计稿'):
            rf.design_save(place, PAGE_HTML, '标题', ['RF-101'], ident='DS-20260101-0000')
        other, there = self.draft('另一个')
        theirs = rf.design_save(there, PAGE_HTML, '别人的', ['RF-102'])
        with self.assertRaisesRegex(ValueError, '不是这个需求画的设计稿'):  # visible (one copy of the facts), but not this requirement's to change
            rf.design_save(place, PAGE_HTML, '标题', ['RF-101'], ident=theirs['id'])
        self.assertFalse((place / rf.DIR / 'designs').exists())

    @in_repo_layout
    def test_skipping_the_design_is_visible_and_drawing_undoes_it(self):
        made, place = self.draft()
        with self.assertRaisesRegex(ValueError, '要写理由'):
            rf.design_skip(place, '  ')
        rf.design_skip(place, '只改导出格式 # 没有界面')
        step = self.find(made)['ladder'][2]
        self.assertEqual((step['reached'], step['skipped']), (True, '只改导出格式 ＃ 没有界面'))
        self.assertIn('跳过方案：只改导出格式', ' '.join(self.find(made)['sections']['变更记录']))
        rf.design_save(place, PAGE_HTML, '还是画一份', ['RF-101'])
        step = self.find(made)['ladder'][2]
        self.assertEqual((step['reached'], step['skipped'], step['designs']), (True, '', 1))
        with self.assertRaisesRegex(ValueError, '已经有设计稿'):
            rf.design_skip(place, '不用了')

    def complete(self, made, place, code):
        self.start(made)
        self.commit_in(place, code, 'x\n')
        return rf.req_done(self.root, made['id'])

    @in_repo_layout
    def test_completed_designs_become_what_features_look_like_and_older_ones_retire(self):
        made, place = self.draft()
        first = rf.design_save(place, PAGE_HTML, '账单列表加导出', ['RF-101', '新增：导出页（属于 M-01）'])
        rf.apply_item(place, {'kind': 'structure', 'op': 'add-feature', 'title': '导出页', 'module': 'M-01', 'one_liner': '导出。', 'stated_by': 'user'}, 'S-x#4', made['id'])
        done = self.complete(made, place, 'export.py')
        record = ' '.join(done['sections']['变更记录'])
        self.assertIn(f'设计稿：{first["id"]} 账单列表加导出（成为 RF-101、RF-105 现在的样子）', record)
        meta = rf.read_doc(next((self.root / rf.DIR / 'designs').glob(f'{first["id"]}-*.md')))[0]
        self.assertEqual((meta['status'], meta['features']), ('current', ['RF-101', 'RF-105']))
        self.assertEqual({k: v['id'] for k, v in rf.current_designs(self.root).items()}, {'RF-101': first['id'], 'RF-105': first['id']})
        self.assertEqual(done['designs'][0]['status'], 'current')
        self.assertIn(f'RF-101：{first["id"]} 账单列表加导出 → .relayforge/designs/{first["id"]}.html', (self.root / rf.DIR / 'context.md').read_text(encoding='utf-8'))

        second, there = self.draft('列表改版')
        b = self.find(second)['brief']
        self.assertEqual([(d['feature'], d['id'], d['html']) for d in b['designs']], [('RF-101', first['id'], f'.relayforge/designs/{first["id"]}.html')])
        with redirect_stdout(io.StringIO()) as out:
            rf.main(['req', 'brief', '--root', str(there)])
        self.assertIn(f'RF-101 账单列表：{first["id"]} 账单列表加导出 → {self.root.resolve()}/.relayforge/designs/{first["id"]}.html', out.getvalue())  # absolute: the session runs in its worktree
        with self.assertRaisesRegex(ValueError, '不是这个需求画的'):
            rf.design_save(there, PAGE_HTML, '改', ['RF-101'], ident=first['id'])
        newer = rf.design_save(there, PAGE_HTML.replace('账单', '账单（新）'), '列表改版', ['RF-101'])
        self.complete(second, there, 'list.py')
        current = {k: v['id'] for k, v in rf.current_designs(self.root).items()}
        self.assertEqual(current, {'RF-101': newer['id'], 'RF-105': first['id']})  # the older one still shows RF-105
        self.assertTrue(next((self.root / rf.DIR / 'designs').glob(f'{first["id"]}-*.md')))

        third, place3 = self.draft('导出页改版')
        last = rf.design_save(place3, PAGE_HTML, '导出页改版', ['RF-105'])
        done = self.complete(third, place3, 'page.py')
        self.assertIn(f'被替代的设计稿：{first["id"]} 账单列表加导出（由 {last["id"]} 替代，移到 history/）', ' '.join(done['sections']['变更记录']))
        old = next((self.root / rf.DIR / 'history').glob(f'{first["id"]}-*.md'))
        self.assertEqual((rf.read_doc(old)[0]['status'], rf.read_doc(old)[0]['superseded_by']), ('superseded', last['id']))
        self.assertTrue((self.root / rf.DIR / 'history' / f'{first["id"]}.html').exists())
        self.assertFalse((self.root / rf.DIR / 'designs' / f'{first["id"]}.html').exists())
        self.assertEqual({d['id']: d['status'] for d in next(r for r in rf.load_requirements(self.root) if r['id'] == made['id'])['designs']},
                         {first['id']: 'superseded'})

        data = rf.collect(self.root)
        self.assertEqual(data['featureDesigns'], {'RF-101': newer['id'], 'RF-105': last['id']})
        self.assertEqual({d['html'] for d in data['designs']}, {f'designs/{newer["id"]}.html', f'designs/{last["id"]}.html'})
        page = rf.render(self.root).read_text(encoding='utf-8')
        self.assertIn('"designBase": "../"', page)
        self.assertTrue((rf.render(self.root).parent / '..' / 'designs' / f'{last["id"]}.html').resolve().is_file())
        self.assertIn('"designBase": "rf/"', rf.page_html(self.root, 'project'))

    @in_repo_layout
    def test_the_server_hands_out_design_pages_in_a_sandbox(self):
        import threading
        import urllib.request
        self.facts()
        drawn = {'id': 'DS-20261114-abcd'}  # the server shows the main line, where finished designs live
        rf.write_doc(self.root / rf.DIR / 'designs' / 'DS-20261114-abcd-页.md', {'id': drawn['id'], 'type': 'design', 'title': '页', 'status': 'current',
                     'features': ['RF-101'], 'html': 'DS-20261114-abcd.html'}, {h: [] for h in rf.DESIGN_HEADS})
        (self.root / rf.DIR / 'designs' / 'DS-20261114-abcd.html').write_text(PAGE_HTML, encoding='utf-8')
        self.assertIn('rf/designs/DS-20261114-abcd.html', rf.page_html(self.root, 'x').replace('\\/', '/') + 'rf/' + rf.collect(self.root)['designs'][0]['html'])
        server = rf.serve(self.root, 0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        name = rf.register(self.root)['name']
        with urllib.request.urlopen(f'http://127.0.0.1:{server.server_address[1]}/p/{name}/rf/designs/{drawn["id"]}.html') as reply:
            self.assertIn('sandbox allow-scripts', reply.headers['Content-Security-Policy'])
            self.assertIn("default-src 'none'", reply.headers['Content-Security-Policy'])
            self.assertIn('账单', reply.read().decode('utf-8'))
        with self.assertRaises(urllib.error.HTTPError):
            urllib.request.urlopen(f'http://127.0.0.1:{server.server_address[1]}/p/{name}/rf/designs/..%2F..%2FAGENTS.md')

    @in_repo_layout
    def test_the_command_line_saves_from_a_file_lists_and_skips(self):
        made, place = self.draft()
        page = Path(self.tmp.name) / 'drafts' / '草稿.html'
        page.parent.mkdir()
        page.write_text(PAGE_HTML, encoding='utf-8')
        self.talk(3)
        args = ['--root', str(place), '--session', 'abcd1234-session', '--transcript', str(self.transcript)]
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(rf.main(['design', 'save', '--title', '导出', '--feature', 'RF-101', '新增：导出页', '--file', str(page), '--json'] + args), 0)
        made_one = json.loads(out.getvalue())
        self.assertEqual((made_one['features'], made_one['new']), (['RF-101'], ['导出页']))
        self.assertIn(f'〔{rf.session_tag("abcd1234-session")}#3〕', (self.root / made_one['file']).read_text(encoding='utf-8'))
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(rf.main(['design', 'save', '--title', '导出', '--id', made_one['id'], '--file', str(page)] + args), 0)
        self.assertIn(f'已修改设计稿 {made_one["id"]}', out.getvalue())
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(rf.main(['design', 'list'] + args), 0)
        self.assertIn(f'{made_one["id"]} 导出（draft，画的：RF-101、新增：导出页）', out.getvalue())
        with redirect_stdout(io.StringIO()), mock.patch('sys.stderr', io.StringIO()) as err:
            self.assertEqual(rf.main(['design', 'skip', '没有界面'] + args), 1)
        self.assertIn('已经有设计稿', err.getvalue())
        with redirect_stdout(io.StringIO()), mock.patch('sys.stderr', io.StringIO()) as err, mock.patch('sys.stdin', io.StringIO('<p>x</p>')):
            self.assertEqual(rf.main(['design', 'save', '--title', '外链', '--feature', 'RF-101', '--root', str(place)]), 1)
        self.assertIn('不像一份 HTML', err.getvalue())

    def test_writing_a_draft_outside_the_project_is_not_a_change(self):
        draft = rf.drafts_dir('R-1') / 'x.html'
        self.assertFalse(rf.changes_things({'type': 'tool_use', 'name': 'Write', 'input': {'file_path': str(draft)}}))
        self.assertTrue(rf.changes_things({'type': 'tool_use', 'name': 'Write', 'input': {'file_path': str(self.root / 'x.py')}}))
        self.assertTrue(rf.changes_things({'type': 'tool_use', 'name': 'Edit', 'input': {}}))
        self.assertTrue(str(rf.drafts_dir()).startswith(os.environ['RELAYFORGE_HOME']))

    @in_repo_layout
    def test_dropping_keeps_the_designs_in_history_and_restoring_brings_them_back(self):
        made, place = self.draft()
        drawn = rf.design_save(place, PAGE_HTML, '页', ['RF-101'])
        rf.drafts_dir(made['id']).mkdir(parents=True)
        (rf.drafts_dir(made['id']) / '页.html').write_text(PAGE_HTML, encoding='utf-8')
        rf.req_drop(self.root, made['id'])
        self.assertFalse(rf.drafts_dir(made['id']).exists())
        self.assertIn('账单', rf.git(self.root, 'show', f'HEAD:{rf.DIR}/history/{drawn["id"]}.html'))
        self.assertEqual(rf.read_doc(next((self.root / rf.DIR / 'history').glob(f'{drawn["id"]}-*.md')))[0]['dropped_with'], made['id'])
        self.assertFalse((self.root / drawn['html']).exists())
        back = rf.req_restore(self.root, made['id'])
        self.assertEqual([d['id'] for d in back['designs']], [drawn['id']])
        self.assertTrue((self.root / drawn['html']).exists())


class NoteTests(Base):
    SID = 'abcd1234-session'

    def say(self, *args, **kw):
        return rf.note(self.root, self.SID, self.transcript, args[0], list(args[1:]), **kw)

    def state(self):
        return rf.read_session(self.root / rf.DIR / 'sessions' / f'{rf.session_tag(self.SID)}.md')

    def test_the_session_writes_down_its_plan_and_progress(self):
        self.talk(3)
        self.say('goal', '导出账单')
        self.say('plan', '看清数据', '写导出', '试一遍')
        self.say('step', '1', 'done', '数据在 bills.json')
        self.say('step', '写导出', 'doing')
        self.say('doing', '在写导出')
        self.say('change', '不做批量了')
        state = self.state()
        self.assertEqual((state['goal'], state['doing'], state['upto']), ('导出账单', '在写导出', 3))
        self.assertEqual([(p['title'], p['status'], p['note'], p['at']) for p in state['plan']],
                         [('看清数据', 'done', '数据在 bills.json', 3), ('写导出', 'doing', '', 3), ('试一遍', 'todo', '', 3)])
        self.assertEqual([c['text'] for c in state['changes']], ['不做批量了'])
        self.say('plan', '写导出', '加一步')
        self.say('step', '1,2', 'done')
        self.assertEqual([p['status'] for p in self.state()['plan']], ['done', 'done'])
        self.say('step', '1', 'doing')
        self.say('step', '2', 'todo')
        self.assertEqual([(p['title'], p['status']) for p in self.state()['plan']], [('写导出', 'doing'), ('加一步', 'todo')])
        with self.assertRaisesRegex(ValueError, '没有「9」这一步'):
            self.say('step', '9', 'done')
        with self.assertRaisesRegex(ValueError, '少了内容'):
            self.say('doing')

    def test_decisions_and_open_questions_get_numbers_and_sources(self):
        self.talk(5)
        self.assertIn('D-0001', self.say('decision', '金额用分', '金额一律用分保存。', reason='避免小数误差'))
        self.assertIn('待确认', self.say('decision', '可能不做多币种。', guess=True))
        self.say('open', '要不要支持退款？')
        docs = self.docs('decisions')
        tag = rf.session_tag(self.SID)
        self.assertEqual(docs['D-0001'][2]['决定'], [f'- 金额一律用分保存。〔{tag}#5〕'])
        self.assertEqual(docs['D-0001'][1]['status'], 'current')
        self.assertEqual(docs['D-0002'][1]['status'], 'draft')
        self.assertIn(f'- 要不要支持退款？〔{tag}#5〕', (self.root / rf.DIR / 'product' / 'open.md').read_text(encoding='utf-8'))

    @in_repo_layout

    def test_requirement_sections_are_filled_from_inside_its_directory(self):
        env = mock.patch.dict(os.environ, {'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@example.com', 'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@example.com'})
        env.start()
        self.addCleanup(env.stop)
        for args in (['init', '-q', '-b', 'main'], ['add', '-A'], ['commit', '-qm', 'init']):
            subprocess.run(['git', *args], cwd=self.root, check=True, capture_output=True)
        made = rf.req_new(self.root, '导出')
        place = Path(made['worktree'])
        self.talk(1)
        with self.assertRaisesRegex(ValueError, '不属于任何需求'):
            self.say('check', '能打开')
        rf.note(place, self.SID, self.transcript, 'check', ['Excel 能打开', '中文不乱码'])
        rf.note(place, self.SID, self.transcript, 'feature', ['导出账单'], guess=True)
        found = next(r for r in rf.load_requirements(self.root) if r['id'] == made['id'])
        self.assertEqual(found['sections']['验收条件'], ['Excel 能打开', '中文不乱码'])
        self.assertEqual(found['sections']['要改的功能点'], ['〔待确认〕导出账单'])
        self.assertEqual(rf.read_session(self.root / rf.DIR / 'sessions' / f'{rf.session_tag(self.SID)}.md')['title'], '导出')  # in the main checkout
        self.assertFalse((place / rf.DIR / 'sessions' / f'{rf.session_tag(self.SID)}.md').exists())

    def stop(self, events=None, **extra):
        data = dict({'cwd': str(self.root), 'transcript_path': str(self.transcript), 'session_id': self.SID, 'hook_event_name': 'Stop'}, **extra)
        with mock.patch.dict(os.environ, {'RELAYFORGE_EVENTS': str(events)} if events else {}), redirect_stdout(io.StringIO()) as out:
            self.assertEqual(rf.stop_check(data), 0)
        return out.getvalue()

    def test_a_turn_that_did_work_may_not_end_without_a_note(self):
        events = Path(self.tmp.name) / 'events.jsonl'
        rows = [{'type': 'user', 'message': {'content': '加一个导出'}},
                {'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'name': 'Write', 'input': {}}]}},
                {'type': 'user', 'message': {'content': [{'type': 'tool_result', 'content': 'ok'}]}},
                {'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': '写好了'}]}}]
        self.transcript.write_text('\n'.join(json.dumps(r, ensure_ascii=False) for r in rows) + '\n', encoding='utf-8')
        answer = json.loads(self.stop(events))
        self.assertEqual(answer['decision'], 'block')
        self.assertIn('rf.py note', answer['reason'])
        self.assertLess(len(answer['reason']), 400)
        self.assertFalse(events.exists())
        self.assertEqual(self.stop(events, stop_hook_active=True), '')
        self.assertEqual(len(events.read_text(encoding='utf-8').splitlines()), 1)
        self.say('doing', '导出写好了')
        self.assertEqual(self.stop(events), '')
        self.assertEqual(len(events.read_text(encoding='utf-8').splitlines()), 2)

    def test_the_command_line_records_for_the_named_session(self):
        self.talk(1)
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(rf.main(['note', 'doing', '在看数据', '--root', str(self.root), '--session', self.SID, '--transcript', str(self.transcript)]), 0)
        self.assertIn('已记下', out.getvalue())
        self.assertEqual(self.state()['doing'], '在看数据')
        rf.note(self.root, 'ffff0000-other', self.transcript, 'plan', ['只有计划'])
        self.assertEqual(rf.read_session(self.root / rf.DIR / 'sessions' / f'{rf.session_tag("ffff0000-other")}.md')['title'], '第 1 句')

    def test_small_talk_ends_without_a_note(self):
        self.talk(2)
        self.assertEqual(self.stop(), '')

    def test_reading_and_talking_do_not_call_for_a_note(self):
        def turn(*blocks):
            rows = [{'type': 'user', 'message': {'content': '看看这个仓库'}}, {'type': 'assistant', 'message': {'content': list(blocks)}}]
            self.transcript.write_text('\n'.join(json.dumps(r, ensure_ascii=False) for r in rows) + '\n', encoding='utf-8')
            return self.stop()

        self.assertEqual(turn({'type': 'tool_use', 'name': 'Read', 'input': {}}, {'type': 'tool_use', 'name': 'Bash', 'input': {'command': 'ls -la && cat app.py | head'}},
                              {'type': 'tool_use', 'name': 'Bash', 'input': {'command': 'ls missing 2>/dev/null; grep -r x . 2>&1 | head; cat a >/dev/null'}},
                              {'type': 'tool_use', 'name': 'Bash', 'input': {'command': 'python3 /x/rf.py req brief > /dev/null'}}, {'type': 'text', 'text': '说' * 2000}), '')
        self.assertIn('block', turn({'type': 'tool_use', 'name': 'Bash', 'input': {'command': "printf '你好' > hello.txt"}}))
        self.assertIn('block', turn({'type': 'tool_use', 'name': 'Bash', 'input': {'command': 'git commit -m x'}}))


class PastSessionTests(Base):
    def test_sessions_held_in_the_project_are_listed_without_those_of_requirements(self):
        env = {'CLAUDE_CONFIG_DIR': str(Path(self.tmp.name) / 'claude'), 'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@example.com', 'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@example.com'}
        patch = mock.patch.dict(os.environ, env)
        patch.start()
        self.addCleanup(patch.stop)
        for args in (['init', '-q', '-b', 'main'], ['add', '-A'], ['commit', '-qm', 'init']):
            subprocess.run(['git', *args], cwd=self.root, check=True, capture_output=True)
        home = self.root.resolve()
        folder = Path(env['CLAUDE_CONFIG_DIR']) / 'projects' / re.sub(r'[^0-9a-zA-Z]', '-', str(home))
        folder.mkdir(parents=True)

        def write(name, text, cwd=home, age=0):
            rows = [{'type': 'queue-operation'}, {'type': 'user', 'isMeta': True, 'cwd': str(cwd), 'message': {'content': '<local-command>x</local-command>'}},
                    {'type': 'user', 'cwd': str(cwd), 'message': {'content': [{'type': 'text', 'text': text}]}}, {'type': 'assistant', 'message': {'content': '好'}}]
            path = folder / f'{name}.jsonl'
            path.write_text('\n'.join(json.dumps(r, ensure_ascii=False) for r in rows) + '\n', encoding='utf-8')
            os.utime(path, (path.stat().st_mtime - age, path.stat().st_mtime - age))

        write('aaaa-old', '先聊聊  导出\n怎么做', age=100)
        write('bbbb-new', '修一个错别字')
        write('cccc-req', '这是需求里的会话')
        write('dddd-elsewhere', '别的目录', cwd=Path(self.tmp.name))
        write('eeee-empty', '')
        write('ffff-tool', '标准输入里有两部分：这次会话')
        fixed = rf.map_session_id(self.root)
        write(fixed, '调整大图', age=-50)  # the map editing session is opened from the map, not listed with the chats
        made = rf.req_new(self.root, '导出')
        rf.req_attach(self.root, made['id'], 'cccc-req')
        found = rf.past_sessions(self.root)
        self.assertEqual([(s['session_id'], s['title']) for s in found], [('bbbb-new', '修一个错别字'), ('aaaa-old', '先聊聊 导出 怎么做')])
        self.assertEqual(rf.past_sessions(Path(made['worktree'])), found)
        self.assertEqual(rf.map_session_id(Path(made['worktree'])), fixed)  # one fixed session per project, from any checkout
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(rf.main(['map', 'session', '--json', '--root', str(self.root)]), 0)
        answer = json.loads(out.getvalue())
        self.assertEqual((answer['session_id'], answer['transcript']), (fixed, str(folder / f'{fixed}.jsonl')))
        with redirect_stdout(io.StringIO()) as out, mock.patch.dict(os.environ, {'CLAUDE_CODE_SESSION_ID': fixed}):
            self.assertEqual(rf.main(['map', 'source', '--root', str(self.root)]), 0)
        self.assertEqual(out.getvalue().strip(), f'{answer["tag"]}#2')


class ProjectListTests(Base):
    def test_an_initialised_project_can_be_added_and_listed_as_json(self):
        other = Path(self.tmp.name) / 'other'
        other.mkdir()
        with redirect_stdout(io.StringIO()) as out, mock.patch('sys.stderr', io.StringIO()):
            self.assertEqual(rf.main(['projects', '--add', str(other)]), 1)
        rf.init(other, hooks=False)
        rf.main(['projects', '--forget', 'other'])
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(rf.main(['projects', '--add', str(other), '--json']), 0)
        self.assertEqual(sorted(p['name'] for p in json.loads(out.getvalue())), ['other', 'project'])


class ConfirmTests(Base):
    def setUp(self):
        super().setUp()
        self.talk(8)
        self.run_capture([
            {'kind': 'structure', 'op': 'add-feature', 'title': '导入', 'module': '账单', 'one_liner': '导入账单。', 'evidence': 'x', 'evidence_at': 1, 'stated_by': 'user'},
            {'kind': 'structure', 'op': 'add-feature', 'title': '导出', 'module': '账单', 'one_liner': '导出账单。', 'evidence': 'x', 'evidence_at': 2, 'stated_by': 'inferred'},
            {'kind': 'structure', 'op': 'add-feature', 'title': '对账', 'module': '账单', 'one_liner': '自动对账。', 'evidence': 'x', 'evidence_at': 2, 'stated_by': 'inferred'},
            {'kind': 'decision', 'title': '只支持 CSV', 'statement': '导入只支持 CSV。', 'applies_to': ['RF-101'], 'evidence': 'x', 'evidence_at': 3, 'stated_by': 'inferred'},
            {'kind': 'decision', 'title': '金额用分', 'statement': '金额一律用分保存。', 'evidence': 'x', 'evidence_at': 4, 'stated_by': 'user'},
            {'kind': 'decision', 'title': '不做多币种', 'statement': '不做多币种。', 'evidence': 'x', 'evidence_at': 5, 'stated_by': 'inferred'},
            {'kind': 'supersede', 'target': 'D-0002', 'by': 'D-0003', 'statement': '可能改了', 'evidence': 'x', 'evidence_at': 6, 'stated_by': 'inferred'},
        ])

    def shape(self):
        return [(g['title'], [i['id'] for i in g['items']]) for g in rf.pending_groups(self.root)]

    def test_items_are_grouped_by_module(self):
        self.assertEqual(self.shape(), [('账单', ['RF-102', 'RF-103', 'D-0001']), ('通用', ['D-0002', 'D-0003'])])
        text = (self.root / rf.DIR / 'pending.md').read_text(encoding='utf-8')
        self.assertIn('## 账单（3 个）', text)
        self.assertIn('- 账单：3 个', (self.root / rf.DIR / 'context.md').read_text(encoding='utf-8'))

    def test_objects_kept_for_reference_do_not_wait_for_the_user(self):
        path, meta, sections = rf.find_doc(self.root, 'RF-103')
        meta['status'] = 'reference'
        rf.write_doc(path, meta, sections)
        self.assertEqual(self.shape()[0], ('账单', ['RF-102', 'D-0001']))

    def test_a_whole_module_is_confirmed_except_what_the_user_leaves_out(self):
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(rf.main(['confirm', '--root', str(self.root), '--module', '账单', '--except', 'RF-103', '--source', 'S-test#9']), 0)
        self.assertIn('已确认 2 个：RF-102、D-0001', out.getvalue())
        feature = self.docs('features')['RF-102']
        self.assertEqual(feature[1]['status'], 'current')
        self.assertEqual(feature[2]['一句话定位'], ['- 导出账单。〔S-20261113-abcd#2〕'.replace('S-20261113', rf.session_tag('abcd1234-session')[:10])])
        self.assertEqual(feature[2]['变更记录'][-1], f'- {rf.today()} 用户确认。〔S-test#9〕')
        decision = self.docs('decisions')['D-0001'][1]
        self.assertEqual((decision['status'], decision['decided_by']), ('current', 'user'))
        self.assertEqual(self.shape(), [('账单', ['RF-103']), ('通用', ['D-0002', 'D-0003'])])

    def test_confirming_a_proposed_replacement_carries_it_out(self):
        self.assertTrue(rf.confirm(self.root, 'D-0002', 'S-test#9'))
        self.assertNotIn('D-0002', self.docs('decisions'))
        meta = rf.read_doc(next((self.root / rf.DIR / 'history').glob('D-0002-*.md')))[0]
        self.assertEqual((meta['status'], meta['superseded_by']), ('superseded', 'D-0003'))

    def test_rejecting_keeps_the_record_out_of_the_way(self):
        self.assertTrue(rf.reject(self.root, 'RF-103', 'S-test#9'))
        self.assertTrue(rf.reject(self.root, 'D-0002', 'S-test#9'))
        self.assertFalse(rf.reject(self.root, 'RF-101', 'S-test#9'))
        gone = rf.read_doc(next((self.root / rf.DIR / 'history').glob('RF-103-*.md')))
        self.assertEqual((gone[0]['status'], gone[0]['superseded_by']), ('superseded', None))
        self.assertTrue(gone[1]['一句话定位'][0].startswith('- 〔已否决〕自动对账。'))
        kept = self.docs('decisions')['D-0002']
        self.assertEqual(kept[1]['status'], 'current')
        self.assertTrue(any(line.startswith('- 〔已否决〕') for line in kept[2]['变更记录']))
        self.assertEqual(self.shape(), [('账单', ['RF-102', 'D-0001']), ('通用', ['D-0003'])])


class ProjectTests(Base):
    def make(self, folder):
        root = Path(self.tmp.name) / folder / 'project'
        root.mkdir(parents=True)
        rf.init(root, hooks=False)
        return root

    def test_the_machines_own_data_folder_does_not_make_home_a_project(self):
        user = Path(self.tmp.name) / 'user'
        code = user / 'code'
        code.mkdir(parents=True)
        with mock.patch.dict(os.environ, {'RELAYFORGE_HOME': str(user / '.relayforge')}):
            rf.register(self.root)  # creates user/.relayforge/projects.json, like ~/.relayforge
            self.assertIsNone(rf.find_root(code))
            self.assertFalse(rf.is_project(user))
            self.assertTrue(rf.is_project(self.root))
            self.assertEqual(rf.main(['projects', '--add', str(code)]), 1)
            self.assertEqual([p['root'] for p in rf.projects()], [str(self.root.resolve())])

    def test_init_registers_each_project_once_under_a_distinct_name(self):
        other = self.make('elsewhere')
        rf.init(self.root, hooks=False)
        self.assertEqual([(p['name'], p['root']) for p in rf.projects()], [('project', str(self.root.resolve())), ('project-2', str(other.resolve()))])
        self.assertEqual(rf.project_of(other / 'docs'), None)
        import shutil
        shutil.rmtree(other)
        self.assertEqual([p['name'] for p in rf.projects()], ['project'])

    def test_one_server_shows_every_project_and_syncs_into_the_right_one(self):
        import threading
        import urllib.request
        other = self.make('elsewhere')
        self.talk(4)
        self.items_file.write_text(json.dumps(dict(self.STATE_B, title='第二个项目的事'), ensure_ascii=False), encoding='utf-8')
        server = rf.serve(None, 0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        port = server.server_address[1]
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(rf.sync_command(other.resolve(), 'abcd1234-session', str(self.transcript), port, False), 0)
        self.assertIn(f'http://127.0.0.1:{port}/p/project-2/#', out.getvalue())
        self.assertEqual(len(list((other / rf.DIR / 'sessions').glob('*.md'))), 1)
        self.assertEqual(len(list((self.root / rf.DIR / 'sessions').glob('*.md'))), 0)
        front = urllib.request.urlopen(f'http://127.0.0.1:{port}/')
        self.assertTrue(front.geturl().endswith('/p/project-2/'))
        self.assertIn('第二个项目的事', front.read().decode('utf-8'))
        state = json.loads(urllib.request.urlopen(f'http://127.0.0.1:{port}/p/project/api/state').read())
        self.assertEqual((state['project'], state['sessions']), ('project', []))
        self.assertEqual([(p['name'], p['active'], p['current']) for p in state['projects']], [('project-2', 1, False), ('project', 0, True)])

    STATE_B = {'goal': '另一个项目', 'doing': '开工', 'plan': [{'title': '第一步', 'status': 'doing', 'note': '', 'at': 1}], 'changes': []}


class HookTests(Base):
    def run_hook(self, name, payload):
        out = io.StringIO()
        with mock.patch('sys.stdin', io.StringIO(json.dumps(payload))), redirect_stdout(out):
            code = rf.run_hook(name)
        return code, out.getvalue()

    def test_session_start_prints_the_summary(self):
        code, out = self.run_hook('session-start', {'cwd': str(self.root), 'session_id': 's1'})
        self.assertEqual(code, 0)
        self.assertIn('现行事实摘要', out)

    def test_hooks_do_nothing_inside_a_capture_run_or_outside_a_project(self):
        with mock.patch.dict(os.environ, {rf.GUARD: '1'}):
            self.assertEqual(self.run_hook('session-start', {'cwd': str(self.root)}), (0, ''))
        self.assertEqual(self.run_hook('session-start', {'cwd': self.tmp.name}), (0, ''))

    def test_capture_hook_starts_a_detached_run(self):
        with mock.patch.object(hooks, 'spawn_capture') as spawn:
            self.run_hook('capture', {'cwd': str(self.root), 'session_id': 's1', 'transcript_path': '/t.jsonl', 'hook_event_name': 'PreCompact'})
            spawn.assert_called_once_with(self.root.resolve(), '/t.jsonl', 's1', 'pre-compact')
            spawn.reset_mock()
            self.run_hook('capture', {'cwd': str(self.root), 'session_id': 's1'})
            spawn.assert_not_called()

    def test_subagent_report_is_stored(self):
        self.run_hook('subagent-stop', {'cwd': str(self.root), 'session_id': 'abcd1234', 'agent_id': 'a49a/../x', 'agent_type': 'general-purpose',
                                         'agent_transcript_path': '/tmp/agent.jsonl', 'last_assistant_message': '查完了，结论是可行。'})
        files = list((self.root / rf.DIR / 'runs').rglob('*.md'))
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].name, 'a49ax.md')
        text = files[0].read_text(encoding='utf-8')
        self.assertIn('agent_type: general-purpose', text)
        self.assertIn('查完了，结论是可行。', text)

    def test_a_broken_hook_never_fails_the_session(self):
        with mock.patch.object(hooks, 'store_run', side_effect=RuntimeError('boom')):
            self.assertEqual(self.run_hook('subagent-stop', {'cwd': str(self.root)})[0], 0)
        self.assertIn('boom', (self.root / rf.DIR / 'log.txt').read_text(encoding='utf-8'))


class StatusTests(Base):
    """Hook payloads shaped like the ones a real Claude Code session sends, fed through the single entry point."""
    SID = 'fe86e3d2-d8d1-4dc7-8640-3b6b0d39dfdc'

    def setUp(self):
        super().setUp()
        # 有的负载故意不带 cwd、命令行入口不带 --root：钩子会从进程的当前目录往上找项目。在仓库里跑测试时那就是真实的仓库，
        # 而 RELAYFORGE_HOME 指着临时目录，找项目时会把真实仓库的 .relayforge 链接改到临时目录（测试结束就删了，链接就断了）
        here = os.getcwd()
        os.chdir(self.tmp.name)
        self.addCleanup(os.chdir, here)
        self.events = Path(self.tmp.name) / 'events.jsonl'
        patch = mock.patch.dict(os.environ, {'RELAYFORGE_EVENTS': str(self.events)})
        patch.start()
        self.addCleanup(patch.stop)
        self.t = 1000.0

    def send(self, name, after=0, session=None, **extra):
        self.t += after
        data = dict({'session_id': session or self.SID, 'hook_event_name': name, 'cwd': str(self.root)}, **extra)
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(rf.hook_event(data, now=self.t), 0)
        return out.getvalue()

    def sweep(self, after=0):
        self.t += after
        rf.hook_sweep(now=self.t)

    def lines(self):
        try:
            return [json.loads(x) for x in self.events.read_text(encoding='utf-8').splitlines()]
        except OSError:
            return []

    def last(self):
        return (self.lines() or [{}])[-1]

    def sub(self, agent='a1', **extra):
        return dict({'agent_id': agent, 'agent_type': 'general-purpose'}, **extra)

    def bg(self, *agents, shell=False):
        tasks = [{'id': a, 'type': 'subagent', 'status': 'running', 'description': 'x', 'agent_type': 'general-purpose'} for a in agents]
        return tasks + ([{'id': 'b1', 'type': 'shell', 'status': 'running', 'command': 'sleep 25'}] if shell else [])

    def test_a_prompt_means_running_and_a_plain_stop_means_finished(self):
        self.send('UserPromptSubmit', prompt='做点事')
        self.assertEqual(self.last()['status'], 'running')
        self.send('Stop', 5, last_assistant_message='好了，已经改完。', background_tasks=[])
        got = self.last()
        self.assertEqual((got['status'], got['kind'], got['text']), ('waiting', 'reply', '好了，已经改完。'))
        self.assertEqual(got['session_id'], self.SID)

    def test_a_last_line_that_asks_is_an_answer_wanted(self):
        self.send('UserPromptSubmit', prompt='x')
        self.send('Stop', last_assistant_message='有两种做法。\n\n**要用第一种吗？**')
        self.assertEqual((self.last()['kind'], self.last()['text']), ('answer', '要用第一种吗？'))

    def test_a_stop_while_a_subagent_runs_stays_running(self):
        self.send('UserPromptSubmit', prompt='x')
        self.send('SubagentStart', **self.sub())
        self.send('Stop', 3, last_assistant_message='launched', background_tasks=self.bg('a1', shell=True))
        self.assertEqual([x['status'] for x in self.lines()], ['running'])
        self.send('SubagentStop', 10, **self.sub(background_tasks=self.bg('a1')))
        self.assertEqual(len(self.lines()), 1, 'the last subagent ending does not announce by itself')
        # what the real CLI does next: wake the main agent with a task notification, then its own Stop
        self.send('UserPromptSubmit', 1, prompt='<task-notification>\n<task-id>a1</task-id>')
        self.send('Stop', 4, last_assistant_message='子代理做完了。', background_tasks=[])
        self.assertEqual([x['status'] for x in self.lines()], ['running', 'running', 'waiting'])
        self.assertEqual(self.last()['text'], '子代理做完了。')

    def test_subagents_are_paired_by_their_id(self):
        self.send('UserPromptSubmit', prompt='x')
        self.send('SubagentStart', **self.sub('a1'))
        self.send('SubagentStart', **self.sub('a2'))
        self.send('Stop', last_assistant_message='launched')  # no background_tasks field: the tracked set decides
        self.send('SubagentStop', **self.sub('a1'))
        self.assertEqual(rf.read_status(self.events)[self.SID]['agents'].keys(), {'a2'})
        self.send('SubagentStop', **self.sub('zzz'))  # unknown id changes nothing
        self.assertEqual(rf.read_status(self.events)[self.SID]['agents'].keys(), {'a2'})
        self.send('SubagentStop', 1, **self.sub('a2'))
        self.assertEqual(rf.read_status(self.events)[self.SID]['agents'], {})
        self.assertEqual(len(self.lines()), 1)

    def test_a_task_notification_does_not_forget_the_other_subagents(self):
        self.send('UserPromptSubmit', prompt='x')
        self.send('SubagentStart', **self.sub('a1'))
        self.send('SubagentStart', **self.sub('a2'))
        self.send('Stop', last_assistant_message='launched')
        self.send('SubagentStop', **self.sub('a1'))
        self.send('UserPromptSubmit', prompt='<task-notification>a1')
        self.send('Stop', last_assistant_message='一个做完了，另一个还在跑。')  # only the tracked set: a2 still running
        self.assertEqual([x['status'] for x in self.lines()], ['running', 'running'])

    def test_the_stop_payload_corrects_what_was_tracked(self):
        self.send('UserPromptSubmit', prompt='x')
        self.send('SubagentStart', **self.sub('a1'))  # its stop event was lost
        self.send('Stop', last_assistant_message='完事', background_tasks=[])
        self.assertEqual(self.last()['status'], 'waiting')

    def test_a_shell_running_in_the_background_does_not_hold_the_stop(self):
        self.send('UserPromptSubmit', prompt='x')
        self.send('Stop', last_assistant_message='服务开着了', background_tasks=self.bg(shell=True))
        self.assertEqual(self.last()['status'], 'waiting')

    def test_a_held_stop_is_announced_when_nothing_else_arrives(self):
        self.send('UserPromptSubmit', prompt='x')
        self.send('SubagentStart', **self.sub())
        self.send('Stop', last_assistant_message='等它做完')
        self.send('SubagentStop', 10, **self.sub())
        self.sweep(rf.STATUS_GRACE - 1)
        self.assertEqual(len(self.lines()), 1)
        self.sweep(2)
        self.assertEqual((self.last()['status'], self.last()['text']), ('waiting', '等它做完'))
        self.sweep(1000)
        self.assertEqual(len(self.lines()), 2, 'announced once')

    def test_a_subagent_that_never_stops_expires(self):
        self.send('UserPromptSubmit', prompt='x')
        self.send('SubagentStart', **self.sub())
        self.send('Stop', last_assistant_message='等它做完')
        self.assertEqual(rf.read_status(self.events)[self.SID]['due'], self.t + rf.STATUS_TTL)
        self.sweep(rf.STATUS_TTL - 1)
        self.assertEqual(len(self.lines()), 1)
        self.sweep(2)  # forgotten now; the grace period starts from here
        self.assertEqual(len(self.lines()), 1)
        self.sweep(rf.STATUS_GRACE)
        self.assertEqual(self.last()['status'], 'waiting')

    def test_due_tells_the_app_when_to_sweep(self):
        self.send('UserPromptSubmit', prompt='x')
        self.assertEqual(rf.read_status(self.events)[self.SID]['due'], self.t + rf.STATUS_STALE)
        self.send('SubagentStart', **self.sub())
        self.assertIsNone(rf.read_status(self.events)[self.SID]['due'])
        self.send('Stop', last_assistant_message='x')
        self.send('SubagentStop', 5, **self.sub())
        self.assertEqual(rf.read_status(self.events)[self.SID]['due'], self.t + rf.STATUS_GRACE)
        self.send('Stop', 1, last_assistant_message='y', background_tasks=[])
        self.assertIsNone(rf.read_status(self.events)[self.SID]['due'])

    def test_a_silent_running_session_is_eventually_shown_as_stopped(self):
        self.send('UserPromptSubmit', prompt='x')
        self.sweep(rf.STATUS_STALE - 1)
        self.assertEqual(self.last()['status'], 'running')
        self.sweep(2)
        self.assertEqual((self.last()['status'], self.last().get('kind')), ('idle', None))

    def test_helpers_after_the_session_finished_are_ignored(self):
        self.send('UserPromptSubmit', prompt='x')
        self.send('Stop', last_assistant_message='好了')
        before = len(self.lines())
        self.send('SubagentStart', 2, **self.sub('summary'))
        self.send('SubagentStop', 1, **self.sub('summary'))
        self.assertEqual(len(self.lines()), before)
        self.assertEqual(rf.read_status(self.events)[self.SID]['agents'], {})
        self.assertEqual(rf.read_status(self.events)[self.SID]['phase'], 'idle')
        self.send('UserPromptSubmit', 1, prompt='下一件')
        self.send('Stop', 1, last_assistant_message='又好了', background_tasks=[])
        self.assertEqual(self.last()['status'], 'waiting', 'an ignored helper does not hold the next stop')

    def test_a_new_prompt_resets_the_bookkeeping(self):
        self.send('UserPromptSubmit', prompt='x')
        self.send('SubagentStart', **self.sub('old'))
        self.send('Stop', last_assistant_message='等')
        self.send('UserPromptSubmit', prompt='算了，换个事')
        self.assertEqual(rf.read_status(self.events)[self.SID]['agents'], {})
        self.assertIsNone(rf.read_status(self.events)[self.SID]['held'])
        self.send('Stop', last_assistant_message='换好了')
        self.assertEqual(self.last()['text'], '换好了')

    def test_sessions_do_not_disturb_each_other(self):
        self.send('UserPromptSubmit', prompt='x')
        self.send('SubagentStart', **self.sub('a1'))
        self.send('UserPromptSubmit', session='other', prompt='y')
        self.send('Stop', session='other', last_assistant_message='别的会话好了')
        self.assertEqual(self.last()['session_id'], 'other')
        self.send('Stop', last_assistant_message='这个还有子代理')
        self.assertEqual(self.last()['session_id'], 'other')

    def test_permission_requests_are_approvals(self):
        self.send('UserPromptSubmit', prompt='x')
        self.send('PermissionRequest', tool_name='Bash', tool_input={'command': 'touch x'})
        got = self.last()
        self.assertEqual((got['status'], got['kind']), ('waiting', 'approve'))
        self.assertIn('Bash', got['text'])
        self.send('PostToolUse', 3, tool_name='Bash')
        self.assertEqual(self.last()['status'], 'running')
        n = len(self.lines())
        self.send('PostToolUse', 1, tool_name='Bash')
        self.assertEqual(len(self.lines()), n, 'only the first tool after an approval says anything')

    def test_a_subagents_permission_request_counts_for_its_session(self):
        self.send('UserPromptSubmit', prompt='x')
        self.send('PermissionRequest', **self.sub(tool_name='Monitor'))
        self.assertEqual((self.last()['session_id'], self.last()['kind']), (self.SID, 'approve'))

    def test_the_permission_notification_is_only_a_fallback(self):
        self.send('UserPromptSubmit', prompt='x')
        self.send('PermissionRequest', tool_name='Bash')
        self.send('Notification', 1, notification_type='permission_prompt', message='Claude needs your permission to use Bash')
        self.assertEqual(len(self.lines()), 2, 'the same question is not reported twice')
        self.send('PostToolUse', 60)
        self.send('Notification', 1, notification_type='permission_prompt', message='Claude needs your permission to use Edit')
        self.assertEqual((self.last()['status'], self.last()['kind']), ('waiting', 'approve'))

    def test_idle_reminders_are_ignored_by_type_not_by_words(self):
        self.send('UserPromptSubmit', prompt='x')
        self.send('Stop', last_assistant_message='好了')
        n = len(self.lines())
        self.send('Notification', 30, notification_type='idle_prompt', message='Claude is waiting for your input')
        self.send('Notification', 1, notification_type='auth_success', message='permission granted')
        self.assertEqual(len(self.lines()), n)

    def test_the_note_check_runs_first_and_a_blocked_stop_says_nothing(self):
        rows = [{'type': 'user', 'message': {'content': '加一个导出'}},
                {'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'name': 'Write', 'input': {}}]}},
                {'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': '写好了'}]}}]
        self.transcript.write_text('\n'.join(json.dumps(r, ensure_ascii=False) for r in rows) + '\n', encoding='utf-8')
        self.send('UserPromptSubmit', prompt='加一个导出')
        said = self.send('Stop', transcript_path=str(self.transcript), last_assistant_message='写好了')
        self.assertEqual(json.loads(said)['decision'], 'block')
        self.assertEqual([x['status'] for x in self.lines()], ['running'])
        self.assertEqual(rf.read_status(self.events)[self.SID]['phase'], 'running')
        again = self.send('Stop', transcript_path=str(self.transcript), stop_hook_active=True, last_assistant_message='还是写好了')
        self.assertEqual(again, '')
        self.assertEqual(self.last()['status'], 'waiting')

    def test_hooks_are_fail_soft(self):
        # 每个负载都带 cwd 指向一个不是项目的目录：没有 cwd 时钩子会从进程的当前目录往上找项目，
        # 在仓库里跑测试就会找到这个仓库本身，停下来时的「记录会话进展」提交就落在它头上
        away = self.tmp.name
        for broken in ({'hook_event_name': 'Stop', 'session_id': 5, 'background_tasks': 'x', 'last_assistant_message': 7, 'cwd': away},
                       {'hook_event_name': 'SubagentStart', 'session_id': self.SID, 'agent_id': ['x']},
                       {'hook_event_name': 'UserPromptSubmit', 'session_id': self.SID, 'prompt': None}, {}, {'hook_event_name': None}):
            with redirect_stdout(io.StringIO()):
                self.assertEqual(rf.hook_event(broken, now=self.t), 0)
        self.events.with_suffix('.jsonl.state').write_text('not json', encoding='utf-8')
        self.send('UserPromptSubmit', prompt='x')
        self.assertEqual(self.last()['status'], 'running', 'a damaged state file is replaced')
        with mock.patch.object(status, 'status_update', side_effect=RuntimeError('boom')):
            self.assertEqual(rf.hook_event({'hook_event_name': 'Stop', 'session_id': self.SID, 'cwd': away}), 0)
            self.assertEqual(rf.hook_sweep(), 0)
        with mock.patch.dict(os.environ, {'RELAYFORGE_EVENTS': str(Path(self.tmp.name) / 'no' / 'such' / 'events.jsonl')}):
            self.assertEqual(rf.hook_event({'hook_event_name': 'UserPromptSubmit', 'session_id': 'q'}), 0)
        with mock.patch.dict(os.environ, {rf.GUARD: '1'}):
            n = len(self.lines())
            self.send('Stop', last_assistant_message='内部调用')
            self.assertEqual(len(self.lines()), n)

    def test_the_command_line_entry_points(self):
        payload = json.dumps({'session_id': self.SID, 'hook_event_name': 'UserPromptSubmit', 'prompt': 'x'})
        with mock.patch('sys.stdin', io.StringIO(payload)):
            self.assertEqual(rf.main(['hook', 'event']), 0)
        self.assertEqual(self.last()['status'], 'running')
        self.assertEqual(rf.main(['hook', 'sweep']), 0)
        with mock.patch('sys.stdin', io.StringIO('not json')):
            self.assertEqual(rf.main(['hook', 'event']), 0)


class BootstrapTests(Base):
    ANSWER = {
        'overview': {'intro': ['记账小工具，按月汇总开销。'], 'audience': ['个人用户'], 'not': ['不做多人共享']},
        'modules': [
            {'title': '账目录入', 'group': '核心', 'paths': ['src/entry.py', 'src/'], 'does': ['记录每一笔开销'],
             'features': [{'title': '记一笔', 'statement': '输入金额和类别后保存'}, {'title': '', 'statement': '空名称'}]},
            {'title': '月度汇总', 'paths': ['report', 'nowhere/x.py'], 'does': ['按月统计'], 'features': []},
            {'title': '幽灵模块', 'paths': ['nothing/here'], 'does': ['不存在'], 'features': []},
            {'title': '账目录入', 'paths': ['src'], 'does': [], 'features': []},
        ],
    }
    HIDDEN = ('.env', 'TOKEN=abc', 'deploy.pem', 'secrets.json', 'node_modules', 'ignored.log')

    def make_repo(self):
        (self.root / 'src').mkdir()
        (self.root / 'report').mkdir()
        (self.root / 'src' / 'entry.py').write_text('def main():\n    print("hi")\n', encoding='utf-8')
        (self.root / 'report' / 'month.py').write_text('x = 1\n', encoding='utf-8')
        (self.root / 'README.md').write_text('# 记账\n按月汇总开销。\n', encoding='utf-8')
        (self.root / '.env').write_text('TOKEN=abc\n', encoding='utf-8')
        (self.root / 'deploy.pem').write_text('KEY', encoding='utf-8')
        (self.root / 'secrets.json').write_text('{}', encoding='utf-8')
        (self.root / 'node_modules').mkdir()
        (self.root / 'node_modules' / 'junk.js').write_text('x', encoding='utf-8')
        (self.root / 'ignored.log').write_text('log', encoding='utf-8')
        with open(self.root / '.gitignore', 'a', encoding='utf-8') as f:
            f.write('ignored.log\n')

    def run_bootstrap(self, *args):
        with redirect_stdout(io.StringIO()) as out, mock.patch('sys.stderr', io.StringIO()) as err:
            code = rf.main(['bootstrap', '--root', str(self.root), *args])
        return code, out.getvalue(), err.getvalue()

    def answer(self, value):
        self.items_file.write_text(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False), encoding='utf-8')

    def snapshot(self):
        return sorted((str(p.relative_to(self.root)), p.read_bytes()) for p in (self.root / rf.DIR).rglob('*') if p.is_file() and p.name != 'log.txt')

    def check_inventory(self):
        text = rf.bootstrap_inventory(self.root)
        self.assertIn('src/entry.py', text)
        self.assertIn('按月汇总开销', text)
        for hidden in self.HIDDEN:
            self.assertNotIn(hidden, text)

    def test_inventory_leaves_out_secrets_and_ignored_files_without_git(self):
        self.make_repo()
        self.check_inventory()

    def test_inventory_leaves_out_secrets_and_ignored_files_with_git(self):
        self.make_repo()
        subprocess.run(['git', 'init', '-q'], cwd=self.root, check=True)
        self.check_inventory()

    def test_inventory_respects_the_cap(self):
        self.make_repo()
        (self.root / 'README.md').write_text('说明' * 20000, encoding='utf-8')
        for i in range(400):
            (self.root / 'src' / f'file{i}.py').write_text('print(1)\n' * 3000, encoding='utf-8')
        text = rf.bootstrap_inventory(self.root, limit=20000)
        self.assertLessEqual(len(text), 20000)
        self.assertIn('src/', text)
        self.assertLessEqual(len(rf.bootstrap_inventory(self.root)), rf.BOOTSTRAP_CHARS)

    def test_answer_becomes_drafts_with_paths_and_pending_overview(self):
        self.make_repo()
        self.answer(self.ANSWER)
        code, out, _ = self.run_bootstrap()
        self.assertEqual(code, 0)
        self.assertIn('2 个模块', out)
        modules = self.docs('modules')
        self.assertEqual({m[1]['title'] for m in modules.values()}, {'账目录入', '月度汇总'})
        entry = next(m for m in modules.values() if m[1]['title'] == '账目录入')
        self.assertEqual(entry[1]['status'], 'draft')
        self.assertEqual(entry[1]['paths'], ['src/entry.py', 'src'])
        self.assertEqual(entry[1]['layer'], '核心')  # an answer's old-style group is taken as the layer
        self.assertIn(f'- {rf.PENDING}记录每一笔开销〔{rf.BOOTSTRAP_SOURCE}〕', entry[2]['负责什么'])
        features = self.docs('features')
        self.assertEqual([f[1]['title'] for f in features.values()], ['记一笔'])
        feature = next(iter(features.values()))
        self.assertEqual((feature[1]['status'], feature[1]['module']), ('draft', entry[1]['id']))
        self.assertIn(rf.PENDING, feature[2]['一句话定位'][0])
        overview = rf.read_doc(self.root / rf.DIR / 'product' / 'overview.md')[1]
        self.assertEqual(overview['产品介绍'], [f'- {rf.PENDING}记账小工具，按月汇总开销。〔{rf.BOOTSTRAP_SOURCE}〕'])
        self.assertEqual(len(overview['面向谁']) + len(overview['不做什么']), 2)
        pending = {i['id'] for g in rf.pending_groups(self.root) for i in g['items']}
        self.assertTrue({entry[1]['id'], feature[1]['id']} <= pending)

    def test_the_first_version_has_layers_journeys_and_relations_all_pending(self):
        self.make_repo()
        answer = json.loads(json.dumps(self.ANSWER, ensure_ascii=False))
        answer['layers'] = [{'name': '界面', 'about': '用户直接用到的'}, {'name': '统计', 'about': '背后算账'}, {'name': '界面', 'about': '重复的'}]
        answer['modules'][0]['layer'] = '界面'
        answer['modules'][1]['layer'] = '存储'  # a layer the list forgot is added after the others
        answer['modules'][0]['journeys'] = [
            {'title': '记一笔账', 'steps': [
                {'what': '打开记账页', 'see': '空的输入框', 'who': ['用户'], 'features': ['记一笔']},
                {'what': '保存', 'see': '月度汇总跟着变', 'who': ['用户', '月度汇总'], 'features': ['记一笔', '没有这个']},
            ]},
            {'title': '只有一步', 'steps': [{'what': '一步', 'see': '', 'who': ['用户']}]},
        ]
        answer['relations'] = [
            {'from': '账目录入', 'to': '月度汇总', 'kind': '读写数据', 'text': '写下的账目被拿去汇总', 'features': ['记一笔']},
            {'from': '月度汇总', 'to': '幽灵模块', 'kind': '调用', 'text': '对方没写进来'},
            {'from': '账目录入', 'to': '月度汇总', 'kind': '吵架', 'text': '类型不认识'},
        ]
        self.answer(answer)
        code, out, _ = self.run_bootstrap()
        self.assertEqual(code, 0)
        self.assertIn('3 层、2 个模块', out)
        self.assertIn('1 条模块关系、1 条用户旅程', out)
        modules = {m[1]['title']: m for m in self.docs('modules').values()}
        entry, report = modules['账目录入'], modules['月度汇总']
        feature = next(iter(self.docs('features')))
        self.assertEqual((entry[1]['layer'], report[1]['layer']), ('界面', '存储'))
        self.assertEqual(list(entry[2]), rf.MODULE_HEADS)
        self.assertEqual(entry[2]['和其他模块的关系'], [f'- {rf.PENDING}读写数据 {report[1]["id"]}：写下的账目被拿去汇总｜{feature}〔{rf.BOOTSTRAP_SOURCE}〕'])
        layers = rf.load_layers(self.root)
        self.assertEqual([(l['name'], l['about'], l['waiting']) for l in layers], [('界面', '用户直接用到的', True), ('统计', '背后算账', True), ('存储', '', True)])
        journeys = rf.load_journeys(self.root)
        self.assertEqual([(j['id'], j['title'], j['status'], j['module']) for j in journeys], [('J-01', '记一笔账', 'draft', entry[1]['id'])])
        self.assertEqual([(s['what'], s['see'], s['user'], s['modules'], s['features'], s['waiting']) for s in journeys[0]['steps']],
                         [('打开记账页', '空的输入框', True, [], [feature], True), ('保存', '月度汇总跟着变', True, [report[1]['id']], [feature], True)])
        data = rf.collect(self.root)
        self.assertEqual([(e['from'], e['to'], e['kind'], e['via']) for e in data['relations']], [(entry[1]['id'], report[1]['id'], 'data', [feature])])
        self.assertEqual(([l['name'] for l in data['layers']], data['moduleLayer'][entry[1]['id']]), (['界面', '统计', '存储'], '界面'))
        self.assertEqual(data['journeys'][0]['id'], 'J-01')
        pending = {i['id'] for g in rf.pending_groups(self.root) for i in g['items']}
        self.assertTrue({'J-01', 'LAYERS'} <= pending)
        self.assertTrue(rf.confirm(self.root, 'J-01', 'S-test#3'))
        self.assertEqual(rf.load_journeys(self.root)[0]['status'], 'current')
        self.assertFalse(rf.load_journeys(self.root)[0]['steps'][0]['waiting'])

    def test_older_modules_use_their_group_as_the_layer(self):
        rf.apply_item(self.root, {'kind': 'structure', 'op': 'add-module', 'title': '账单', 'stated_by': 'user'}, 'S-test#1')
        rf.apply_item(self.root, {'kind': 'structure', 'op': 'add-module', 'title': '报表', 'stated_by': 'user'}, 'S-test#1')
        path, meta, sections = rf.find_doc(self.root, 'M-01')
        meta['group'] = '核心'
        rf.write_doc(path, meta, sections)
        self.assertEqual([l['name'] for l in rf.load_layers(self.root, rf.collect(self.root)['modules'])], ['核心', '未分层'])
        self.assertEqual(rf.parse_step('- 做一件事｜M-01｜RF-101〔S-test#1〕')['see'], '')  # the shorter form without 看到什么

    def test_missing_paths_are_dropped(self):
        self.make_repo()
        self.answer(self.ANSWER)
        code, out, _ = self.run_bootstrap('--json')
        result = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual([m['paths'] for m in result['modules']], [['src/entry.py', 'src'], ['report']])
        self.assertEqual(result['dropped']['路径不存在'], 2)
        self.assertEqual(result['dropped']['模块没有可用路径'], 1)
        for bad in ('../x', '/etc/passwd', '.env', 'deploy.pem', 'node_modules/junk.js', '.relayforge'):
            self.assertIsNone(rf.repo_path(self.root, bad))

    def test_second_run_is_refused_without_force(self):
        self.make_repo()
        self.answer(self.ANSWER)
        self.assertEqual(self.run_bootstrap()[0], 0)
        before = self.snapshot()
        code, _, err = self.run_bootstrap()
        self.assertEqual(code, 1)
        self.assertIn('--force', err)
        self.assertEqual(self.snapshot(), before)
        self.answer({'modules': [{'title': '新的一块', 'paths': ['report'], 'does': ['再来一块'], 'features': []}]})
        self.assertEqual(self.run_bootstrap('--force')[0], 0)
        self.assertEqual(len(self.docs('modules')), 3)

    def test_malformed_answer_writes_nothing(self):
        self.make_repo()
        before = self.snapshot()
        for bad in ('这不是 JSON', '{"modules": "没有列表"}', '{"modules": [{"title": "甲", "paths": ["nope"]}]}', '{"overview": {}}'):
            with self.subTest(bad=bad):
                self.answer(bad)
                code, _, err = self.run_bootstrap()
                self.assertEqual(code, 1)
                self.assertIn('导入失败', err)
                self.assertEqual(self.snapshot(), before)
        self.assertEqual(len(self.docs('modules')), 0)


if __name__ == '__main__':
    unittest.main()

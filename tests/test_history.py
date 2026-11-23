"""历史：确认完成、放弃、修好时把会话记录另存到本机（不进仓库）；客观标签；总结和查漏（假的模型）；rf.py history 查询。"""
import gzip
import io
import json
import os
import re
import stat
import sys
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'backend'))
import rf  # noqa: E402
from tests.app_fixture import as_app, in_repo_layout  # noqa: E402
from tests.test_issues import IssueBase, git  # noqa: E402

SID = 'aaaa1111-2222-3333-4444-555566667777'
GONE = 'bbbb1111-2222-3333-4444-555566667777'


def transcript_lines(texts):
    """A Claude Code transcript: user and assistant text, a tool call and its (long) output in between."""
    out = []
    for i, (role, text) in enumerate(texts):
        if role == 'user':
            out.append({'type': 'user', 'message': {'role': 'user', 'content': text}, 'timestamp': f'2026-11-18T0{i % 10}:00:00Z'})
        else:
            out.append({'type': 'assistant', 'message': {'role': 'assistant', 'content': [
                {'type': 'tool_use', 'id': f't{i}', 'name': 'Bash', 'input': {'command': 'ls'}}]}})
            out.append({'type': 'user', 'message': {'role': 'user', 'content': [
                {'type': 'tool_result', 'tool_use_id': f't{i}', 'content': 'SECRET-TOOL-OUTPUT ' * 50}]}})
            out.append({'type': 'assistant', 'message': {'role': 'assistant', 'content': [{'type': 'text', 'text': text}]}, 'timestamp': f'2026-11-18T0{i % 10}:00:30Z'})
    return '\n'.join(json.dumps(x, ensure_ascii=False) for x in out) + '\n'


TALK = [('user', '导出要按月分文件'), ('assistant', '好的，我按月分文件。'), ('user', '文件名用中文月份，别用数字'), ('assistant', '改成了中文月份。')]


class HistoryBase(IssueBase):
    def setUp(self):
        super().setUp()
        self.claude = Path(self.tmp.name) / 'claude'
        env = mock.patch.dict(os.environ, {'CLAUDE_CONFIG_DIR': str(self.claude), 'CODEX_HOME': str(Path(self.tmp.name) / 'codex')})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop('RELAYFORGE_ARCHIVE', None)
        self.archive = Path(os.environ['RELAYFORGE_HOME']) / 'archive'

    def say(self, place, sid, texts=TALK, subagent=True):
        folder = self.claude / 'projects' / re.sub(r'[^0-9a-zA-Z]', '-', str(Path(place).resolve()))
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f'{sid}.jsonl'
        path.write_text(transcript_lines(texts), encoding='utf-8')
        if subagent:
            sub = folder / sid / 'subagents'
            sub.mkdir(parents=True, exist_ok=True)
            (sub / 'agent-a1.jsonl').write_text(transcript_lines([('user', '子代理：查一下导出'), ('assistant', '查完了')]), encoding='utf-8')
        return path

    def requirement(self, title='导出账单', finish=True, sessions=(SID, GONE)):
        made = rf.req_new(self.root, title)
        path = self.root / made['file']
        meta, sections = rf.read_doc(path)
        rf.add_line(sections, '需求说明', '- 用户要把账单导出成文件〔S-test#1〕')
        rf.add_line(sections, '要改的功能点', '- RF-101 文档登记：加导出〔S-test#1〕')
        rf.add_line(sections, '验收条件', '- 能导出〔S-test#1〕')
        rf.write_doc(path, meta, sections)
        rf.req_start(self.root, made['id'])
        for sid in sessions:
            rf.req_attach(self.root, made['id'], sid)
        if SID in sessions:
            self.say(made['worktree'], SID)
        self.commit_in(made['worktree'], 'export.py' if title == '导出账单' else f'{made["id"]}.py', 'print(1)\n', '写导出')
        if finish:
            rf.req_done(self.root, made['id'])
        return made

    def meta(self, made):
        return rf.read_doc(self.root / rf.find_requirement(self.root, made['id'])['file'])


class ArchiveTests(HistoryBase):
    def test_done_keeps_a_private_compressed_copy_outside_the_repository_and_only_session_numbers_inside(self):
        made = self.requirement()
        tag, gone = rf.stable_tag(self.root, SID), rf.stable_tag(self.root, GONE)
        place = rf.archive_dir(self.root, made['id'])
        self.assertTrue(str(place).startswith(str(self.archive)))
        self.assertFalse(str(place.resolve()).startswith(str(self.root.resolve())))
        kept = place / f'{SID}.jsonl.gz'
        original = next(self.claude.glob(f'projects/*/{SID}.jsonl'))
        self.assertEqual(gzip.open(kept, 'rt', encoding='utf-8').read(), original.read_text(encoding='utf-8'))
        self.assertTrue((place / SID / 'subagents' / 'agent-a1.jsonl.gz').exists())
        self.assertEqual(stat.S_IMODE(kept.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(place.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.archive.stat().st_mode), 0o700)
        meta, sections = self.meta(made)
        self.assertEqual((meta['archived'], meta['archive_missing']), ([tag], [gone]))
        line = next(l for l in sections['变更记录'] if '会话记录已存到本机' in l)
        self.assertIn(f'原始记录已不在：{gone}', line)
        text = (self.root / rf.find_requirement(self.root, made['id'])['file']).read_text(encoding='utf-8')
        self.assertNotIn(str(self.archive), text)
        self.assertNotIn(self.tmp.name, text)
        self.assertEqual(git(self.root, 'status', '--porcelain'), '')  # committed with the 完成 facts, nothing loose
        self.assertNotIn('.gz', git(self.root, 'ls-files'))
        # objective tags: from the requirement file and the work's files
        self.assertIn('RF-101', meta['tags'])
        self.assertIn('M-01', meta['tags'])
        self.assertNotIn('files', meta)  # like the 完成记录: no file lists in the file, history search --file works them out
        self.assertEqual([r['id'] for r in rf.history_search(self.root, files=['export.py'])['items']], [made['id']])
        listed = rf.find_requirement(self.root, made['id'])
        self.assertEqual((listed['archive_here'], listed['archive_missing'], listed['summarized']), ([tag], [gone], ''))

    @in_repo_layout
    def test_a_copy_that_fails_does_not_stop_done(self):
        with mock.patch('relayforge.sessions.archive._gzip_copy', side_effect=OSError('disk full')):
            made = self.requirement()
        meta, sections = self.meta(made)
        self.assertEqual(meta['state'], 'done')
        self.assertTrue(any('没能存下' in l for l in sections['变更记录']))
        self.assertNotIn('archived', meta)
        # the sweep later saves it
        out = rf.archive_sweep(self.root)
        self.assertEqual(out['items'][0]['copied'], [rf.stable_tag(self.root, SID)])
        self.assertEqual(self.meta(made)[0]['archived'], [rf.stable_tag(self.root, SID)])
        self.assertEqual(git(self.root, 'status', '--porcelain'), '')
        self.assertEqual(git(self.root, 'log', '-1', '--format=%s').strip(), '记录会话存档')

    def test_sweep_saves_what_was_missed_refreshes_longer_ones_and_is_quiet_the_second_time(self):
        made = self.requirement(sessions=(SID,))
        kept = rf.archive_dir(self.root, made['id']) / f'{SID}.jsonl.gz'
        commits = git(self.root, 'rev-list', '--count', 'HEAD')
        self.assertEqual(rf.archive_sweep(self.root), {'items': [], 'changed': False})
        self.assertEqual(git(self.root, 'rev-list', '--count', 'HEAD'), commits)
        # the user went on talking in the finished requirement's session: the sweep copies it again
        self.say(self.root, SID, TALK + [('user', '再补一句')], subagent=False)
        for p in self.claude.glob(f'projects/*/{SID}.jsonl'):
            if p.parent.name != re.sub(r'[^0-9a-zA-Z]', '-', str(self.root.resolve())):
                p.unlink()
        out = rf.archive_sweep(self.root)
        self.assertEqual(out['items'][0]['copied'], [rf.stable_tag(self.root, SID)])
        self.assertIn('再补一句', gzip.open(kept, 'rt', encoding='utf-8').read())
        # once Claude Code has cleaned the original up, the copy stays and nothing changes
        for p in self.claude.glob(f'projects/*/{SID}.jsonl'):
            p.unlink()
        self.assertEqual(rf.archive_sweep(self.root)['changed'], False)
        self.assertTrue(kept.exists())

    def test_dropping_a_requirement_keeps_its_sessions_too(self):
        made = self.requirement(finish=False, sessions=(SID,))
        rf.req_drop(self.root, made['id'])
        meta, sections = self.meta(made)
        self.assertEqual(meta['archived'], [rf.stable_tag(self.root, SID)])
        self.assertTrue((rf.archive_dir(self.root, made['id']) / f'{SID}.jsonl.gz').exists())

    def test_the_archive_location_can_be_moved_by_the_environment(self):
        other = Path(self.tmp.name) / 'elsewhere'
        with mock.patch.dict(os.environ, {'RELAYFORGE_ARCHIVE': str(other)}):
            made = self.requirement(sessions=(SID,))
            self.assertTrue(next(other.glob(f'*/{made["id"]}/{SID}.jsonl.gz')).exists())

    def test_a_resolved_issue_gets_its_tags_and_its_fix_and_probe_sessions_kept(self):
        self.commit_in(self.root, 'app.py', 'print("old")\n', 'app')
        rf.issue_new(self.root, '启动时打印旧的', feature='RF-101')
        self.judge(kind='bug', reason='打印没跟着改。')
        rf.issue_triage(self.root, 'I-001')
        fix = rf.issue_fix(self.root, ['#1'])
        place = Path(fix['worktree'])
        rf.issue_attach(self.root, 'F-001', SID)
        self.say(place, SID)
        (place / 'app.py').write_text('print("new")\n', encoding='utf-8')
        git(place, 'commit', '-qam', 'fix print')
        rf.issue_handback(place, None, '改成打印新的', '运行看到 new', no_regression='这条测试只看交回流程，不看回归数据')
        rf.issue_confirm(self.root, '#1')
        meta = rf.find_issue(self.root, 'I-001')[1]
        self.assertEqual(meta['state'], 'resolved')
        self.assertEqual([r['id'] for r in rf.history_search(self.root, files=['app.py'], kinds=['issue'])['items']], ['I-001'])
        self.assertEqual(meta['tags'], ['RF-101', 'M-01'])
        fixrec = rf.read_fix(self.root, 'F-001')
        self.assertEqual(fixrec['archived'], [rf.stable_tag(self.root, SID)])
        self.assertTrue((rf.archive_dir(self.root, 'F-001') / f'{SID}.jsonl.gz').exists())
        self.assertEqual(git(self.root, 'status', '--porcelain'), '')


ANSWER = {'topics': ['账单导出', '文件命名', 'RF-101'], 'summary': ['做了按月导出账单。', '文件名用中文月份。'],
          'decisions': [{'title': '中文月份命名', 'statement': '导出的文件名用中文月份', 'session': None, 'at': 3, 'evidence': '文件名用中文月份，别用数字'},
                        {'title': '编造的', 'statement': '这条指向不存在的消息', 'session': None, 'at': 99},
                        {'title': '没出处', 'statement': '这条没有会话', 'session': 'S-nope', 'at': 1}]}


class SummaryTests(HistoryBase):
    def reply_for(self, tag, **extra):
        a = json.loads(json.dumps(ANSWER, ensure_ascii=False))
        for d in a['decisions'][:2]:
            d['session'] = tag
        a.update(extra)
        return a

    @in_repo_layout
    def test_one_call_writes_topics_a_summary_and_hands_unrecorded_decisions_to_the_user(self):
        made = self.requirement()
        tag = rf.stable_tag(self.root, SID)
        seen = {}

        def model(home, instruction, payload):
            seen['payload'] = payload
            return self.reply_for(tag)
        out = rf.summarize(self.root, made['id'], run=model)
        payload = seen['payload']
        self.assertIn('用户要把账单导出成文件', payload)
        self.assertIn('【3】用户：文件名用中文月份，别用数字', payload)
        self.assertIn('（原始记录已不在）', payload)
        self.assertNotIn('SECRET-TOOL-OUTPUT', payload)
        self.assertEqual(out['topics'], ['账单导出', '文件命名'])  # a bare id is not a topic
        self.assertEqual(len(out['proposed']), 1)
        meta, sections = self.meta(made)
        self.assertEqual(list(sections)[-2:], ['总结', '变更记录'])
        self.assertEqual(sections['总结'], [f'- 做了按月导出账单。〔{made["id"]}#总结〕', f'- 文件名用中文月份。〔{made["id"]}#总结〕'])
        self.assertEqual(meta['topics'], ['账单导出', '文件命名'])
        self.assertEqual(meta['summary_decisions'], out['proposed'])
        did = out['proposed'][0]
        dpath, dmeta, dsections = rf.find_doc(self.root, did)
        self.assertEqual(dmeta['status'], 'draft')
        self.assertEqual(dsections['决定'], [f'- {rf.PENDING}导出的文件名用中文月份〔{tag}#3〕'])
        self.assertIn(did, [i['id'] for g in rf.pending_groups(self.root) for i in g['items']])
        self.assertEqual(git(self.root, 'status', '--porcelain'), '')
        self.assertEqual(git(self.root, 'log', '-1', '--format=%s').strip(), '需求总结：导出账单')
        listed = rf.find_requirement(self.root, made['id'])
        self.assertEqual((listed['summary'], listed['pending_decisions']), (['做了按月导出账单。', '文件名用中文月份。'], [did]))
        # again: the summary and topics are replaced, the same decision is not proposed twice
        again = rf.summarize(self.root, made['id'], run=lambda h, i, p: self.reply_for(tag, topics=['导出'], summary=['重新写的一句。']))
        self.assertEqual(again['proposed'], [])
        meta, sections = self.meta(made)
        self.assertEqual(sections['总结'], [f'- 重新写的一句。〔{made["id"]}#总结〕'])
        self.assertEqual(meta['topics'], ['导出'])
        self.assertEqual(meta['summary_decisions'], [did])
        self.assertEqual(sum(1 for _, m, _ in rf.all_docs(self.root, ('decisions',))), 1)
        self.assertEqual(sum('总结' in l for l in sections['变更记录'] if '程序' in l), 2)
        # a decision the user turned down is not proposed again either
        rf.reject(self.root, did, 'S-test#9')
        third = rf.summarize(self.root, made['id'], run=lambda h, i, p: self.reply_for(tag))
        self.assertEqual(third['proposed'], [])

    def test_no_model_or_a_bad_answer_leaves_the_requirement_done_and_unsummarized(self):
        made = self.requirement()
        with self.assertRaisesRegex(ValueError, '没总结出来'):
            rf.summarize(self.root, made['id'])  # the stub has no answer file: exits 1, prints nothing
        with self.assertRaisesRegex(ValueError, '格式不对'):
            rf.summarize(self.root, made['id'], run=lambda h, i, p: {'topics': [], 'summary': ['x']})
        meta, sections = self.meta(made)
        self.assertEqual(meta['state'], 'done')
        self.assertIsNone(meta.get('summarized'))
        self.assertNotIn('总结', sections)
        with self.assertRaisesRegex(ValueError, '还没有确认完成'):
            rf.summarize(self.root, self.requirement('另一个', finish=False, sessions=())['id'], run=lambda h, i, p: {})

    def test_the_command_runs_the_same_model_command_as_triage(self):
        made = self.requirement()
        self.judge(**self.reply_for(rf.stable_tag(self.root, SID)))
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(as_app(['summarize', made['id'], '--root', str(self.root), '--json']), 0)  # 程序（应用）在确认完成以后调用
        self.assertEqual(json.loads(buf.getvalue())['topics'], ['账单导出', '文件命名'])


class HistoryTests(HistoryBase):
    def setUp(self):
        super().setUp()
        self.done = self.requirement()
        rf.summarize(self.root, self.done['id'], run=lambda h, i, p: {'topics': ['账单导出', '踩坑：月份命名'], 'summary': ['按月导出。'], 'decisions': []})
        self.going = self.requirement('打印账单', finish=False, sessions=())

    def run_cli(self, *args):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = rf.main(['history', *args, '--root', str(self.root)])
        return code, buf.getvalue()

    def test_search_finds_requirements_by_words_tags_features_and_files_and_marks_work_in_progress(self):
        out = rf.history_search(self.root, ['账单'])
        ids = [r['id'] for r in out['items']]
        self.assertIn(self.done['id'], ids)
        self.assertIn(self.going['id'], ids)
        going = next(r for r in out['items'] if r['id'] == self.going['id'])
        self.assertIn('进行中', going['state'])
        self.assertEqual([r['id'] for r in rf.history_search(self.root, tags=['月份命名'])['items']], [self.done['id']])
        self.assertEqual([r['id'] for r in rf.history_search(self.root, files=['export.py'], kinds=['req'])['items']], [self.done['id']])
        self.assertIn(self.done['id'], [r['id'] for r in rf.history_search(self.root, features=['RF-101'])['items']])
        self.assertEqual(rf.history_search(self.root, ['账单', '没有这个词'])['items'], [])
        code, text = self.run_cli('search', '按月导出')
        self.assertEqual(code, 0)
        self.assertIn(f'需求 {self.done["id"]} 导出账单（已完成', text)
        self.assertIn('概括：账单导出、踩坑：月份命名', text)
        self.assertIn('总结：按月导出。', text)
        self.assertEqual(self.run_cli('search')[0], 1)

    def test_search_covers_decisions_issues_and_sessions(self):
        rf.apply_item(self.root, {'kind': 'decision', 'title': '账单按月', 'statement': '账单按月导出', 'stated_by': 'inferred'}, 'S-test#2')
        rf.issue_new(self.root, '导出的账单少一行')
        kinds = {r['kind'] for r in rf.history_search(self.root, ['账单'])['items']}
        self.assertTrue({'req', 'decision', 'issue'} <= kinds)
        d = next(r for r in rf.history_search(self.root, ['账单'], kinds=['decision'])['items'])
        self.assertIn('待确认', d['state'])
        rf.note(self.done['worktree'] or self.root, SID, None, 'doing', ['在核对导出的月份'])
        self.assertEqual([r['kind'] for r in rf.history_search(self.root, ['核对导出'])['items']], ['session'])

    def test_show_gives_the_whole_picture_of_a_requirement(self):
        code, text = self.run_cli('show', self.done['id'])
        self.assertEqual(code, 0)
        for part in ('需求说明：', '用户要把账单导出成文件', '验收条件：', '总结（', '按月导出。', '概括标签：账单导出', '涉及：RF-101', '提交：1 个提交、1 个文件',
                     f'{rf.stable_tag(self.root, SID)}', '本机有存档', '原始记录已不在'):
            self.assertIn(part, text)
        self.assertIn('还没确认完成', self.run_cli('show', self.going['id'])[1])
        self.assertEqual(self.run_cli('show', 'X-1')[0], 1)

    def test_session_renders_who_said_what_from_the_archive_without_tool_output(self):
        for p in self.claude.glob(f'projects/*/{SID}.jsonl'):
            p.unlink()  # Claude Code cleaned it up; the copy kept on this machine is read
        tag = rf.stable_tag(self.root, SID)
        code, text = self.run_cli('session', tag)
        self.assertEqual(code, 0)
        self.assertIn('本机存档，共 4 条消息', text)
        self.assertIn('【1】用户', text)
        self.assertIn('导出要按月分文件', text)
        self.assertIn('（用了工具：Bash）', text)
        self.assertIn('另有 1 个子代理的记录没有展开', text)
        self.assertNotIn('SECRET-TOOL-OUTPUT', text)
        text = self.run_cli('session', tag, '--grep', '中文月份')[1]
        self.assertIn('【3】', text)
        self.assertNotIn('【1】', text)
        self.assertEqual(json.loads(self.run_cli('session', tag, '--tail', '1', '--json')[1])['messages'][0]['n'], 4)
        missing = self.run_cli('session', rf.stable_tag(self.root, GONE))[1]
        self.assertIn('没有它的聊天记录', missing)
        self.assertEqual(self.run_cli('session', 'S-nope')[0], 1)

    def test_long_sessions_are_cut(self):
        sid = 'cccc1111-2222-3333-4444-555566667777'
        made = self.requirement('长的', sessions=(sid,), finish=False)
        self.say(made['worktree'], sid, [('user', '很长' * 3000)] * 30, subagent=False)
        out = rf.history_session(self.root, rf.stable_tag(self.root, sid))
        self.assertTrue(out['cut'])
        self.assertLessEqual(sum(len(m['text']) for m in out['messages']), rf.sessions.history.OUT_CAP)
        self.assertEqual(out['source'], 'original')


def _j(lines):
    return '\n'.join(json.dumps(x, ensure_ascii=False) for x in lines) + '\n'


def claude_work(place):
    """A Claude Code session with real work in it: reads, a command failing twice, an edit, a refused command, a note, a commit."""
    def use(i, name, data, ts, mid=None):
        return {'type': 'assistant', 'timestamp': ts, 'cwd': str(place), 'message': {'id': mid or f'm{i}', 'role': 'assistant', 'content': [{'type': 'tool_use', 'id': f't{i}', 'name': name, 'input': data}],
                'usage': {'input_tokens': 10, 'cache_read_input_tokens': 1000, 'cache_creation_input_tokens': 0, 'output_tokens': 50}}}

    def result(i, text, error=False, ts=None):
        return {'type': 'user', 'timestamp': ts, 'message': {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': f't{i}', 'content': text, 'is_error': error}]}}

    return _j([
        {'type': 'user', 'timestamp': '2026-11-18T01:00:00Z', 'cwd': str(place), 'message': {'role': 'user', 'content': '导出点了没反应，查一下'}},
        use(1, 'Read', {'file_path': f'{place}/export.py'}, '2026-11-18T01:00:10Z'),
        result(1, 'SECRET-TOOL-OUTPUT'),
        use(2, 'Bash', {'command': 'python3 -m unittest tests.test_export'}, '2026-11-18T01:01:00Z'),
        result(2, 'Exit code 1\nSECRET-TOOL-OUTPUT', True),
        use(3, 'Edit', {'file_path': f'{place}/export.py', 'old_string': 'a\nb', 'new_string': 'a\nb\nc'}, '2026-11-18T01:02:00Z'),
        result(3, 'ok'),
        use(4, 'Bash', {'command': 'python3 -m unittest tests.test_export'}, '2026-11-18T01:03:00Z'),
        result(4, 'Exit code 1\nSECRET-TOOL-OUTPUT', True),
        use(5, 'Bash', {'command': 'API_TOKEN=sk-very-secret npm install xlsx'}, '2026-11-18T01:04:00Z'),
        result(5, "The user doesn't want to proceed with this tool use. The tool use was rejected", True),
        use(6, 'Bash', {'command': 'python3 /x/.relayforge/bin/rf.py note doing "导出改成在主进程里做"'}, '2026-11-18T01:05:00Z'),
        result(6, 'ok'),
        use(7, 'Bash', {'command': 'git add -A && git commit -m "修好导出"'}, '2026-11-18T01:06:00Z', mid='same'),
        use(8, 'Bash', {'command': 'git status'}, '2026-11-18T01:06:00Z', mid='same'),  # the same reply split in two lines: usage counted once
        result(7, '[req/x 9c1e2f0abc] 修好导出\n 1 file changed'),
        result(8, 'clean'),
        {'type': 'assistant', 'isSidechain': True, 'message': {'role': 'assistant', 'content': [{'type': 'tool_use', 'id': 's1', 'name': 'Bash', 'input': {'command': 'SIDECHAIN'}}]}},
        {'type': 'assistant', 'timestamp': '2026-11-18T01:07:00Z', 'message': {'id': 'm9', 'role': 'assistant', 'content': [{'type': 'text', 'text': '导出修好了，测试还有一条没过。'}]}},
        {'type': 'user', 'timestamp': '2026-11-18T02:00:00Z', 'message': {'role': 'user', 'content': '文件名带上日期'}},
        use(10, 'Write', {'file_path': f'{place}/name.py', 'content': 'x\ny\n'}, '2026-11-18T02:00:30Z'),
        {'type': 'assistant', 'timestamp': '2026-11-18T02:01:00Z', 'message': {'id': 'm11', 'role': 'assistant', 'content': [{'type': 'text', 'text': '加上了。'}]}},
    ])


class StepTests(HistoryBase):
    def run_cli(self, *args):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = rf.main(['history', *args, '--root', str(self.root)])
        return code, buf.getvalue()

    def test_steps_list_what_each_turn_did_without_tool_output(self):
        sid = 'dddd1111-2222-3333-4444-555566667777'
        made = self.requirement('步骤', sessions=(sid, GONE), finish=False)
        path = self.say(made['worktree'], sid, subagent=False)
        path.write_text(claude_work(Path(made['worktree']).resolve()), encoding='utf-8')
        tag = rf.stable_tag(self.root, sid)
        out = rf.history_session(self.root, tag, steps=True)
        self.assertEqual(out['total_turns'], 2)
        first, second = out['turns']
        self.assertEqual((first['n'], first['reply_n'], second['n']), (1, 2, 3))  # 和不加 --steps 时的【n】一样
        self.assertEqual([(s['what'], s['status']) for s in first['steps']],
                         [('读', 'ok'), ('跑', 'failed'), ('改', 'ok'), ('跑', 'failed'), ('跑', 'refused'), ('记进展', 'ok'), ('跑', 'ok'), ('跑', 'ok')])
        self.assertEqual(first['steps'][0]['detail'], 'export.py')
        self.assertEqual(first['steps'][2]['detail'], 'export.py（+3 −2）')
        self.assertEqual(first['steps'][5]['detail'], 'doing "导出改成在主进程里做"')
        self.assertEqual(first['commits'], [{'commit': '9c1e2f0', 'subject': '修好导出'}])
        self.assertEqual(first['repeats'], [{'what': '跑', 'detail': 'python3 -m unittest tests.test_export', 'times': 2}])
        self.assertEqual((first['calls'], first['failed'], first['refused']), (8, 2, 1))
        self.assertEqual((first['read'], first['write'], first['seconds']), (7 * 1010, 7 * 50, 420))
        self.assertEqual(second['steps'], [{'what': '改', 'detail': 'name.py（写入 2 行）', 'status': 'pending', 'times': 1}])
        self.assertEqual(out['totals']['commits'], 1)
        code, text = self.run_cli('session', tag, '--steps')
        self.assertEqual(code, 0)
        for part in ('2 轮，工具调用 9 次，失败 2 次，被拒绝 1 次，提交 1 个', '【1】用户 ', '导出点了没反应', '  · 跑 python3 -m unittest tests.test_export ✗ 失败',
                     '⊘ 被拒绝', '  · 提交 9c1e2f0 修好导出', '！同一步失败了 2 次', '【2】AI：导出修好了', '【3】用户', '…没有结果'):
            self.assertIn(part, text)
        for gone in ('SECRET-TOOL-OUTPUT', 'sk-very-secret', 'SIDECHAIN', str(Path(made['worktree']).resolve())):
            self.assertNotIn(gone, text)
        self.assertIn('API_TOKEN=***', text)
        self.assertEqual([t['n'] for t in json.loads(self.run_cli('session', tag, '--steps', '--grep', '日期', '--json')[1])['turns']], [3])
        self.assertEqual([t['n'] for t in json.loads(self.run_cli('session', tag, '--steps', '--tail', '1', '--json')[1])['turns']], [3])
        self.assertIn('没有它的聊天记录', self.run_cli('session', rf.stable_tag(self.root, GONE), '--steps')[1])

    def test_why_shows_the_turn_that_made_each_commit(self):
        sid = 'eeee1111-2222-3333-4444-555566667777'
        made = self.requirement('步骤', sessions=(sid,), finish=False)
        place = Path(made['worktree']).resolve()
        with mock.patch.dict(os.environ, {'RELAYFORGE_SESSION_ID': sid}):
            self.commit_in(made['worktree'], 'export.py', 'a\nb\nc\n', '修好导出')
        full = git(made['worktree'], 'rev-parse', 'HEAD').strip()
        self.say(made['worktree'], sid, subagent=False).write_text(claude_work(place).replace('9c1e2f0abc', full[:10]), encoding='utf-8')
        rf.req_done(self.root, made['id'])  # 聊天记录随之存档，why 读的是存档
        tag = rf.stable_tag(self.root, sid)
        w = rf.why(self.root, 'export.py')
        turn = w['items'][0]['commits'][0]['turn']
        self.assertEqual((turn['session'], turn['turn'], turn['said'], turn['exact']), (tag, 1, '导出点了没反应，查一下', True))
        self.assertEqual([(s['what'], s['status']) for s in turn['steps']],  # 到 git commit 那一步为止，后面的 git status 不算
                         [('读', 'ok'), ('跑', 'failed'), ('改', 'ok'), ('跑', 'failed'), ('跑', 'refused'), ('记进展', 'ok'), ('跑', 'ok')])
        self.assertEqual(turn['repeats'], [{'what': '跑', 'detail': 'python3 -m unittest tests.test_export', 'times': 2}])
        text = rf.why_text(w)
        for part in (f'产生它的那一轮：会话 {tag} 【1】用户', '      · 跑 python3 -m unittest tests.test_export ✗ 失败', '⊘ 被拒绝',
                     '！同一步失败了 2 次', 'git commit -m "修好导出"'):
            self.assertIn(part, text)
        self.assertNotIn('git status', text)
        self.assertNotIn('SECRET-TOOL-OUTPUT', text)
        # 提交号对不上（后来 amend 或 rebase 过）就按标题认，并说明；都对不上就不给
        other = rf.commit_steps(self.root, [tag], '0' * 40, '修好导出')
        self.assertEqual((other['turn'], other['exact']), (1, False))
        self.assertIn('按提交标题认的', '\n'.join(rf.commit_steps_lines(other)))
        self.assertIsNone(rf.commit_steps(self.root, [tag], '0' * 40, '别的提交'))
        self.assertIsNone(rf.commit_steps(self.root, [rf.stable_tag(self.root, GONE)], full, '修好导出'))
        # git commit -q：输出里看不到提交号，就从命令里的 -m 拿标题，按标题认
        archived = rf.find_archived(sid, self.root)
        import gzip as gz
        quiet = claude_work(place).replace('git commit -m', 'git commit -q -m').replace('[req/x 9c1e2f0abc] 修好导出\\n 1 file changed', '')
        with gz.open(archived, 'wt', encoding='utf-8') as f:
            f.write(quiet)
        turn = rf.why(self.root, 'export.py')['items'][0]['commits'][0]['turn']
        self.assertEqual((turn['turn'], turn['exact'], len(turn['steps'])), (1, False, 7))
        self.assertIn('提交 （输出里没有提交号） 修好导出', rf.steps_text(rf.history_session(self.root, tag, steps=True)))

    def test_brief_recap_for_the_session_that_takes_over(self):
        sid = 'ffff1111-2222-3333-4444-555566667777'
        made = self.requirement('步骤', sessions=(sid, GONE), finish=False)
        place = Path(made['worktree']).resolve()
        self.say(made['worktree'], sid, subagent=False).write_text(claude_work(place), encoding='utf-8')
        tag = rf.stable_tag(self.root, sid)
        code, text = self.run_cli('session', sid, '--brief')  # 应用按会话的原始编号问
        self.assertEqual(code, 0)
        self.assertTrue(text.startswith(f'上一个会话 {tag} 最后 2 轮做了什么（程序从它的聊天记录算的，它一共 2 轮、提交 1 个；'), text)
        self.assertIn(f'rf.py history session {tag} --steps', text)
        self.assertIn('\n<<<\n【1】用户 ', text)
        self.assertIn('  · 读 export.py', text)  # 第 1 轮 8 步，正好都留下
        from relayforge.sessions import history as H
        with mock.patch.object(H, 'RECAP_STEPS', 3):  # 多了只留每轮最后几步：这一轮停在哪
            short = H.recap_text(rf.history_session(self.root, tag, tail=3, steps=True))
        self.assertIn('  · …前面还有 5 步\n  · 记进展', short)
        self.assertNotIn('读 export.py', short)
        for part in ('⊘ 被拒绝', '  · 提交 9c1e2f0 修好导出', '！同一步失败了 2 次', '【3】用户', '…没有结果', '>>>'):
            self.assertIn(part, text)
        self.assertNotIn('SECRET-TOOL-OUTPUT', text)
        self.assertEqual(self.run_cli('session', GONE, '--brief'), (0, '\n'))  # 没有记录：什么也不输出，开场白不带

    def test_steps_read_codex_sessions_too(self):
        sid = 'eeee1111-2222-3333-4444-555566667777'
        made = self.requirement('Codex', sessions=(sid,), finish=False)
        place = str(Path(made['worktree']).resolve())
        folder = Path(os.environ['CODEX_HOME']) / 'sessions' / '2026' / '11' / '18'
        folder.mkdir(parents=True, exist_ok=True)
        item = lambda ts, **p: {'timestamp': ts, 'type': 'response_item', 'payload': p}  # noqa: E731
        (folder / f'rollout-2026-11-18T01-00-00-{sid}.jsonl').write_text(_j([
            {'timestamp': '2026-11-18T01:00:00Z', 'type': 'session_meta', 'payload': {'id': sid, 'cwd': place}},
            item('2026-11-18T01:00:00Z', type='message', role='user', content=[{'type': 'input_text', 'text': '把测试跑快一点'}]),
            item('2026-11-18T01:00:05Z', type='function_call', name='exec_command', call_id='c1', arguments=json.dumps({'cmd': f'cd {place} && pytest -q'})),
            item('2026-11-18T01:01:00Z', type='function_call_output', call_id='c1', output='Process exited with code 1\nSECRET-TOOL-OUTPUT'),
            item('2026-11-18T01:02:00Z', type='custom_tool_call', name='apply_patch', call_id='c2', input='*** Begin Patch\n*** Update File: run.sh\n@@\n-a\n+b\n+c\n*** End Patch'),
            item('2026-11-18T01:02:01Z', type='custom_tool_call_output', call_id='c2', output='Success'),
            item('2026-11-18T01:03:00Z', type='custom_tool_call', name='exec', call_id='c3', input='text(await tools.exec_command({cmd:"git commit -qm \\"快一点\\"; git log --oneline -1"}));'),
            item('2026-11-18T01:03:02Z', type='custom_tool_call_output', call_id='c3', output=[{'type': 'input_text', 'text': '{"exit_code":0,"output":"abc1234 快一点\\n"}'}]),
            {'timestamp': '2026-11-18T01:03:03Z', 'type': 'event_msg', 'payload': {'type': 'token_count', 'info': {'last_token_usage': {'input_tokens': 2000, 'output_tokens': 30}}}},
            item('2026-11-18T01:04:00Z', type='message', role='assistant', content=[{'type': 'output_text', 'text': '快了一倍。'}]),
        ]), encoding='utf-8')
        out = rf.history_session(self.root, rf.stable_tag(self.root, sid), steps=True)
        (turn,) = out['turns']
        self.assertEqual([(s['what'], s['detail'], s['status']) for s in turn['steps']],
                         [('跑', 'cd . && pytest -q', 'failed'), ('改', 'run.sh（+2 −1）', 'ok'), ('跑', 'git commit -qm "快一点"; git log --oneline -1', 'ok')])
        self.assertEqual(turn['commits'], [{'commit': 'abc1234', 'subject': '快一点'}])
        self.assertEqual((turn['read'], turn['write'], turn['seconds'], turn['reply']), (2000, 30, 240, '快了一倍。'))
        self.assertNotIn('SECRET-TOOL-OUTPUT', json.dumps(out, ensure_ascii=False))

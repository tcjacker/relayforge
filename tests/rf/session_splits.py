"""Drive the application-facing split command in an isolated Git project."""

import json
import os
import secrets
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

BACKEND = Path(__file__).resolve().parents[2] / 'backend'
sys.path.insert(0, str(BACKEND))

from relayforge.core.documents import read_doc, write_doc
from relayforge.requirements.record import load_requirements, req_new
from relayforge.sessions.record import stable_tag
from relayforge.core.trust import TOKEN_ENV, register, scrubbed_environ


def transcript_message(agent, role, text):
    if agent == 'codex':
        return {'type': 'response_item', 'payload': {'type': 'message', 'role': role, 'content': [
            {'type': 'input_text' if role == 'user' else 'output_text', 'text': text}
        ]}}
    return {'type': role, 'message': {'content': [{'type': 'text', 'text': text}]}}


def run(input):
    with TemporaryDirectory() as directory, patch.dict(os.environ, {'RELAYFORGE_HOME': f'{directory}/local'}):
        home = Path(directory) / 'project'
        home.mkdir()
        (home / '.relayforge').mkdir()
        subprocess.run(['git', 'init', '-q', '-b', 'main', str(home)], check=True)
        subprocess.run(['git', '-C', str(home), '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                        'commit', '-q', '--allow-empty', '-m', 'Fixture'], check=True)
        parent = None
        if input.get('parent'):
            parent = req_new(home, input['parent']['title'])
            meta, sections = read_doc(parent['path'])
            meta['state'] = input['parent']['state']
            sections.update(input['parent']['sections'])
            write_doc(parent['path'], meta, sections)
        parent_before = Path(parent['path']).read_bytes() if parent else None
        existing = {title: req_new(home, title)['id'] for title in input.get('existing', [])}
        tags = {ident: title for title, ident in existing.items()}
        logfile = Path(directory) / 'session.jsonl'
        logfile.touch()
        root = parent['worktree'] if parent else home
        if parent:
            (Path(root) / '.relayforge').mkdir(exist_ok=True)
        session = input['session']
        command = [sys.executable, str(BACKEND / 'rf.py'), 'split', '--root', str(root),
                   '--session', session, '--transcript', str(logfile)]
        if parent:
            command.extend(['--parent', parent['id']])
        token = secrets.token_hex(24)
        register(os.getpid(), token)
        last = None
        views = []
        for step in input['steps']:
            if 'message' in step:
                message = step['message']
                text = message.get('text')
                if 'items' in message:
                    items = [dict(item, existing=existing[item['existing']]) if item.get('existing') else item
                             for item in message['items']]
                    text = (text or '') + '\n```relayforge-split\n' + json.dumps(items, ensure_ascii=False) + '\n```'
                with logfile.open('a', encoding='utf-8') as stream:
                    print(json.dumps(transcript_message(input['agent'], message['role'], text)), file=stream)
            if 'action' not in step:
                continue
            env = scrubbed_environ()
            if step['action'] != 'untrusted':
                env[TOKEN_ENV] = token
            args = command + (['--approve', last['id']] if step['action'] in ('approve', 'untrusted') else [])
            out = subprocess.run(args, env=env, cwd=root, capture_output=True, text=True)
            if out.returncode:
                views.append({'error': out.stderr.strip()})
                continue
            last = json.loads(out.stdout)
            views.append({'state': last['state'], 'items': [
                [item['title'], item['created'], tags.get(item['requirement'], bool(item['requirement']))]
                for item in last['items']
            ]} if last else None)

        drafts = []
        tag = stable_tag(home, session)
        for req in load_requirements(home):
            if req['id'] in tags or (parent and req['id'] == parent['id']):
                continue
            meta, sections = read_doc(req['path'])
            description = [line.replace(tag, '<session>') for line in sections['需求说明']]
            if parent:
                description = [line.replace(parent['id'], '<parent>') for line in description]
            drafts.append({'title': req['title'], 'stage': req['stage'], 'state': meta['state'],
                           'sections': description, 'can_start': req['gate']['ok'],
                           'worktree': bool(req['worktree'])})
        return {'views': views, 'drafts': sorted(drafts, key=lambda draft: draft['title']),
                'parent_unchanged': Path(parent['path']).read_bytes() == parent_before if parent else None}

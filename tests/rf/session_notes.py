"""Record progress through the Codex CLI path in an isolated project."""

import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

BACKEND = Path(__file__).resolve().parents[2] / 'backend'
sys.path.insert(0, str(BACKEND))

from relayforge.sessions.record import load_sessions


def run(input):
    with TemporaryDirectory() as directory:
        root = Path(directory)
        (root / '.relayforge').mkdir()
        codex = root / 'codex'
        transcripts = codex / 'sessions'
        transcripts.mkdir(parents=True)
        env = {key: value for key, value in os.environ.items()
               if key not in ('RELAYFORGE_SESSION_ID', 'CLAUDE_CODE_SESSION_ID')}
        env.update(CODEX_HOME=str(codex), CLAUDE_CONFIG_DIR=str(root / 'claude'),
                   RELAYFORGE_HOME=str(root / 'local'))
        for step in input['notes']:
            sid, progress = step['session'], step['doing']
            message = {'type': 'response_item', 'payload': {'type': 'message', 'role': 'user',
                       'content': [{'type': 'input_text', 'text': progress}]}}
            with (transcripts / f'rollout-{sid}.jsonl').open('a', encoding='utf-8') as stream:
                print(json.dumps(message), file=stream)
            subprocess.run([sys.executable, str(BACKEND / 'rf.py'), 'note', 'doing', progress,
                            '--root', str(root)], env=dict(env, CODEX_THREAD_ID=sid),
                           check=True, capture_output=True, text=True)
        sessions = sorted(load_sessions(root), key=lambda row: row['session_id'])
        return [{field: row[field] for field in ('session_id', 'title', 'doing', 'upto')} for row in sessions]

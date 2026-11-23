"""Exercise the issue CLI across the main checkout and a second worktree."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


CLI = Path(__file__).resolve().parents[2] / 'backend' / 'rf.py'


def run(data):
    with tempfile.TemporaryDirectory() as folder:
        place = Path(folder)
        main = place / 'main'
        worktree = main / '.worktrees' / 'development'
        main.mkdir()
        (main / '.relayforge').mkdir()
        env = {**os.environ, 'RELAYFORGE_HOME': str(place / 'home')}

        def git(*args):
            subprocess.run(['git', *args], cwd=main, check=True, capture_output=True)

        def issue(cwd, *args):
            result = subprocess.run([sys.executable, str(CLI), 'issue', *args, '--json'],
                                    cwd=cwd, env=env, text=True, capture_output=True)
            if result.returncode:
                raise ValueError(result.stderr.strip())
            return json.loads(result.stdout)

        git('init', '-q')
        git('-c', 'user.name=RelayForge', '-c', 'user.email=test@example.test', 'commit', '--allow-empty', '-qm', 'init')
        git('worktree', 'add', '-q', '-b', 'development', str(worktree))
        (worktree / '.relayforge').mkdir()
        sessions = main / '.relayforge' / 'sessions'
        sessions.mkdir()
        (sessions / 'session.md').write_text(
            f"---\nid: {data['session_tag']}\nsession_id: {data['session']}\n---\n", encoding='utf-8')
        transcript = place / 'session.jsonl'
        transcript.write_text('\n'.join(json.dumps(message) for message in data['messages']), encoding='utf-8')
        for arguments in data['existing']:
            issue(main, 'new', *arguments)
        sources_seen = [item['source'] for item in issue(worktree, 'list')['issues']]
        result = issue(worktree, 'new', *data['report'], '--session', data['session'], '--transcript', str(transcript))
        issues = issue(main, 'list')['issues']
        return {
            'sources_seen': sources_seen,
            'submitted': result['id'],
            'issues': [
                {'id': item['id'], 'source': item['source'], 'state': item['state'], 'duplicate_of': item['duplicate_of'],
                 'thread': [{key: value for key, value in entry.items() if key not in ('at', 'n', 'shots')}
                            for entry in item['thread']]}
                for item in issues
            ],
        }

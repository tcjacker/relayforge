"""Run the top-bar status query against an isolated Git project."""

import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'backend'))

from relayforge.core.documents import write_doc
from relayforge.project.publish import publish_status
from relayforge.sessions.record import write_session


def run(input):
    with TemporaryDirectory() as directory:
        home = Path(directory) / 'project'
        subprocess.run(['git', 'init', '-q', '-b', 'main', str(home)], check=True)
        for index, requirement in enumerate(input['requirements'], 1):
            meta = dict(requirement['meta'], id=f'R-fixture-{index}', type='requirement')
            if 'plan' in requirement:
                session = f'fixture-{index}'
                plan = [{'title': f'步骤 {n}', 'status': status} for n, status in enumerate(requirement['plan'], 1)]
                write_session(home, f'S-{session}', session, {'plan': plan}, 0)
                meta['sessions'] = [session]
            write_doc(home / '.relayforge' / 'requirements' / f'{meta["id"]}.md', meta, requirement['sections'])
        return publish_status(home)['pending_requirements']

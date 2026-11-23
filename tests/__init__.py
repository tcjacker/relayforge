"""Test suite; preserve sibling fixture imports under unittest discovery.

The tests must give the same result wherever they are started: in a person's terminal, or inside an AI session (Claude Code, Codex, a
session RelayForge opened) that has its variables in the environment, no terminal, and an AI program among the parent processes.
So the suite builds its own environment here, once, before any test runs:
  - no session variables (CLAUDE*, CODEX_*, RELAYFORGE_SESSION_*, the app's marker); the tests that are about "in an AI session"
    build that environment themselves (tests/test_hardening.py passes explicit variables and process chains);
  - who the parent process is, is decided in tests/app_fixture.py (trust.parent_pid replaced inside the test process), never by whoever started the run.
"""
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))


def _scrub_session_environment():
    for name in list(os.environ):
        if name.startswith(('CLAUDE', 'CODEX_', 'RELAYFORGE_SESSION_')) or name in ('RELAYFORGE_APP_TOKEN', 'AI_AGENT'):
            os.environ.pop(name, None)


_scrub_session_environment()

"""本机存档在哪里、存了什么：存档目录和清单的读取。

存档本身（把会话记录另存到本机、整理、补存）在 archive.py；需求列表只要知道「哪些会话在本机有副本」，
从这里读，不必导入整套存档逻辑。分层见 tests/test_layers.py。
"""
import json
import os
from pathlib import Path

from ..core.common import main_checkout, project_key, relayforge_home

__all__ = ['archive_root', 'archive_dir', 'read_manifest', 'archive_state']


def archive_root():
    return Path(os.environ.get('RELAYFORGE_ARCHIVE') or relayforge_home() / 'archive')


def archive_dir(home, ident):
    return archive_root() / project_key(main_checkout(home)) / ident


def read_manifest(place):
    try:
        data = json.loads((place / 'manifest.json').read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def archive_state(home, ident, tags):
    """For the panel: which of these sessions have a kept copy on this machine."""
    place = archive_dir(home, ident)
    manifest = read_manifest(place)
    here = {v.get('tag') for k, v in manifest.items() if isinstance(v, dict) and (place / f'{k}.jsonl.gz').exists()}
    return [t for t in tags if t in here]

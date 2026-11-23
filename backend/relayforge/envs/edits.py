"""测试环境的新建、复制、改名、删除、选用，以及在应用里保存整份设置（来自应用，先清洗再写）。

测试环境的存储在 envs.py。分层见 tests/test_layers.py。
"""
import contextlib
import copy
import json

from ..core.common import DIR, main_checkout, today
from .views import env_show
from .values import env_scan
from .clean import clean_payload, SECTION_WORDS
from .store import (
    _check_name, _check_no_values, _commit, _log, _ordered, all_envs, env_path, find_env, local_state, next_id, write_env,
)
from pathlib import Path

__all__ = ['env_new', 'env_copy', 'env_rename', 'env_remove', 'env_save', 'env_use', 'env_pick_user']


def blank(ident, name, note=''):
    return {'id': ident, 'type': 'testenv', 'name': name, 'note': note, 'services': [], 'config': '', 'deps': [], 'vars': [],
            'request': {'items': [], 'before': '', 'after': ''}, 'users': [], 'next_user': 1, 'prep': [], 'next_prep': 1, 'created': today(), 'updated': today(), 'log': []}


def env_new(root, name, note=''):
    home = main_checkout(root)
    name = _check_name(home, name)
    env = blank(next_id(home), name, str(note or '').strip())
    _log(env, '新建')
    write_env(home, env)
    with local_state(home) as loc:
        if not loc.get('use'):
            loc['use'] = env['id']  # 第一套环境建好就用它
    _commit(home, f'新建「{name}」（{env["id"]}）')
    return env_show(home, env['id'])


def env_copy(root, ref, name=None):
    home = main_checkout(root)
    src = find_env(home, ref)
    name = _check_name(home, name or _copy_name(home, src['name']))
    env = copy.deepcopy(src)
    env.update(id=next_id(home), name=name, created=today(), log=[])
    _log(env, f'从 {src["id"]}「{src["name"]}」复制')
    write_env(home, env)
    with local_state(home) as loc:  # 保密的值也一起复制（都在这台电脑上）
        if src['id'] in loc['secrets']:
            loc['secrets'][env['id']] = copy.deepcopy(loc['secrets'][src['id']])
        if src['id'] in loc['user']:
            loc['user'][env['id']] = loc['user'][src['id']]
    _commit(home, f'从「{src["name"]}」复制出「{name}」（{env["id"]}）')
    return env_show(home, env['id'])


def _copy_name(home, base):
    names = {e.get('name') for e in all_envs(home)}
    for i in range(1, 100):
        cand = f'{base} 的副本' + (f' {i + 1}' if i > 1 else '')
        if cand not in names:
            return cand[:40]
    return f'{base} 的副本'


def env_rename(root, ref, name, note=None):
    home = main_checkout(root)
    env = find_env(home, ref)
    old = env['name']
    env['name'] = _check_name(home, name, env['id'])
    if note is not None:
        env['note'] = str(note).strip()
    _log(env, f'改名：{old} → {env["name"]}' if old != env['name'] else '改了说明')
    write_env(home, env)
    _commit(home, f'「{old}」改名为「{env["name"]}」')
    return env_show(home, env['id'])


def env_remove(root, ref):
    """Not deleted: moved to .relayforge/history/envs/ with status removed. The secrets on this machine are kept (they are only here)."""
    home = main_checkout(root)
    env = find_env(home, ref)
    env['status'] = 'removed'
    env['removed'] = today()
    _log(env, '删除（移到 history/envs/）')
    shelf = Path(home) / DIR / 'history' / 'envs'
    shelf.mkdir(parents=True, exist_ok=True)
    _check_no_values(env)
    (shelf / f'{env["id"]}.json').write_text(json.dumps(_ordered(env), ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    env_path(home, env['id']).unlink()
    with local_state(home) as loc:
        if loc.get('use') == env['id']:
            loc['use'] = None
    _commit(home, f'删除「{env["name"]}」（{env["id"]}，移到 history/envs/）')
    return {'id': env['id'], 'name': env['name'], 'removed': True, 'file': f'{DIR}/history/envs/{env["id"]}.json'}


def env_use(root, ref):
    """Which environment is in use on this machine (test runs and, later, starting services use it). '' or none clears it."""
    home = main_checkout(root)
    if str(ref or '').strip().lower() in ('', 'none', '-'):
        with local_state(home) as loc:
            loc['use'] = None
        return {'use': None}
    env = find_env(home, ref)
    with local_state(home) as loc:
        loc['use'] = env['id']
    return {'use': env['id'], 'name': env['name']}


def env_pick_user(root, ref, user):
    home = main_checkout(root)
    env = find_env(home, ref)
    users = env.get('users') or []
    found = next((u for u in users if u['id'].upper() == str(user).upper() or u.get('name') == user), None)
    if not found:
        raise ValueError(f'{env["name"]} 里没有测试用户 {user}。' + ('有：' + '、'.join(f'{u["id"]} {u["name"]}' for u in users) if users else '还没有测试用户。'))
    with local_state(home) as loc:
        loc['user'][env['id']] = found['id']
    return {'env': env['id'], 'user': found['id'], 'name': found['name']}


def env_save(root, ref, payload):
    """The user saves the whole environment from the app. Secret values go to this machine only."""
    home = main_checkout(root)
    env = find_env(home, ref)
    new, secrets, keep = clean_payload(env, payload)
    new['name'] = _check_name(home, new['name'], env['id'])
    with local_state(home) as loc:
        old = loc['secrets'].get(env['id']) or {}
        merged = {'vars': dict(secrets['vars']), 'request': dict(secrets['request']), 'users': {k: dict(v) for k, v in secrets['users'].items()}}
        for name in keep['vars']:
            if name in (old.get('vars') or {}):
                merged['vars'][name] = old['vars'][name]
        for k in keep['request']:
            if k in (old.get('request') or {}):
                merged['request'][k] = old['request'][k]
        for uid, keys in keep['users'].items():
            for k in keys:
                if k in ((old.get('users') or {}).get(uid) or {}):
                    merged['users'].setdefault(uid, {})[k] = old['users'][uid][k]
        merged['vars'] = {k: v for k, v in merged['vars'].items() if v != ''}
        merged['request'] = {k: v for k, v in merged['request'].items() if v != ''}
        merged['users'] = {u: {k: v for k, v in f.items() if v != ''} for u, f in merged['users'].items()}
        merged['users'] = {u: f for u, f in merged['users'].items() if f}
        loc['secrets'][env['id']] = merged
        if loc['user'].get(env['id']) not in {u['id'] for u in new['users']}:
            loc['user'].pop(env['id'], None)
    changed = [word for key, word in SECTION_WORDS if json.dumps(new.get(key), sort_keys=True, ensure_ascii=False) != json.dumps(env.get(key), sort_keys=True, ensure_ascii=False)]
    if changed:
        _log(new, '用户保存：改了' + '、'.join(changed))
    write_env(home, new)
    if new.get('config') and (new.get('config') != env.get('config') or '环境变量' in changed):  # 占位符 ${…} 用环境变量代入，变了要重读
        with contextlib.suppress(ValueError):
            env_scan(home, env['id'])
    if changed:
        _commit(home, f'保存「{new["name"]}」（改了{"、".join(changed)}）')
    return env_show(home, env['id'], reveal=True)

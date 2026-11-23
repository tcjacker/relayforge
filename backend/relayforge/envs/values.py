"""测试环境用到的值：保密值和打码、读配置文件找外部依赖并检查连通、测试用户的 JWT、把变量代进请求的通用设置、测试集运行时带上的环境。

测试环境的存储在 envs.py。分层见 tests/test_layers.py。
"""
import base64
import datetime as dt
import json
import os
import re

from ..core.common import main_checkout
from . import config as envconfig
from .store import all_envs, find_env, HIDDEN, in_use, local_state, MASK, read_local, REDACT_MIN, TEMPLATE, WHERE
from ..core.identity import SIG_ENV, ask_app, claims_of, find_app
from ..core.trust import app_trusted
from pathlib import Path

__all__ = ['env_scan', 'env_check', 'env_resolve', 'env_for_run', 'ai_values', 'redact', 'jwt_info']


def _values(home, env, loc):
    """Every variable of the environment by name, secrets included (from this machine)."""
    sec = (loc.get('secrets') or {}).get(env['id']) or {}
    out = {}
    for v in env.get('vars') or []:
        out[v['name']] = (sec.get('vars') or {}).get(v['name'], '') if v.get('secret') else v.get('value', '')
    return out


def secret_values(home, env, loc):
    sec = (loc.get('secrets') or {}).get(env['id']) or {}
    vals = list((sec.get('vars') or {}).values()) + list((sec.get('request') or {}).values())
    for f in (sec.get('users') or {}).values():
        vals += list(f.values())
    return sorted({str(v) for v in vals if v is not None and v != MASK and len(str(v)) >= REDACT_MIN}, key=len, reverse=True)


def redact(obj, secrets):
    """Replace every secret value inside strings (keys and values) of a JSON-like structure."""
    if not secrets:
        return obj
    if isinstance(obj, str):
        for s in secrets:
            if s in obj:
                obj = obj.replace(s, MASK)
        return obj
    if isinstance(obj, list):
        return [redact(x, secrets) for x in obj]
    if isinstance(obj, tuple):
        return tuple(redact(x, secrets) for x in obj)
    if isinstance(obj, dict):
        return {redact(k, secrets): redact(v, secrets) for k, v in obj.items()}
    return obj


def env_scan(root, ref):
    """Read the environment's config file again; the result is kept on this machine (the file may be a local one)."""
    home = main_checkout(root)
    env = find_env(home, ref)
    rel = env.get('config') or ''
    rec = {'at': dt.datetime.now().isoformat(timespec='seconds'), 'file': rel, 'kind': None, 'deps': [], 'skipped': [], 'error': None, 'mtime': None}
    if not rel:
        rec['error'] = '还没有选配置文件。'
    else:
        path = Path(home) / rel
        try:
            rec['mtime'] = path.stat().st_mtime
            loc = read_local(home)
            kind, entries, skipped = envconfig.parse_config(path)
            rec['kind'] = kind
            rec['deps'] = envconfig.recognize(entries, _values(home, env, loc))
            rec['skipped'] = [{'line': no, 'why': why} for no, why in skipped]
            rec['entries'] = len(entries)
        except FileNotFoundError:
            rec['error'] = f'配置文件 {rel} 不在（这台电脑上没有这个文件）。'
        except (OSError, ValueError) as error:
            rec['error'] = str(error)
    with local_state(home) as loc:
        loc['scans'][env['id']] = rec
    return {'id': env['id'], 'name': env['name'], 'scan': _scan_view(home, env, rec), 'deps': deps_view(home, env, read_local(home))}


def _scan_view(home, env, rec):
    if not rec:
        return None
    out = {k: rec.get(k) for k in ('at', 'file', 'kind', 'error', 'entries')}
    out['skipped'] = rec.get('skipped') or []
    stale = rec.get('file') != (env.get('config') or '')
    if not stale and rec.get('file'):
        try:
            stale = (Path(home) / rec['file']).stat().st_mtime != rec.get('mtime')
        except OSError:
            stale = rec.get('mtime') is not None
    out['stale'] = bool(stale)
    return out


def deps_view(home, env, loc):
    """The dependency table: what the config file gives (from: config), with the user's own rows (from: user) replacing the
    config row of the same key or added after; each with the last connection check if it is still about the same address."""
    rec = (loc.get('scans') or {}).get(env['id']) or {}
    rows = []
    mine = {d['key']: d for d in env.get('deps') or []}
    for d in rec.get('deps') or []:
        if d['key'] in mine:
            continue
        rows.append(dict(d, **{'from': 'config'}))
    for d in env.get('deps') or []:
        typ, host, port, note = envconfig.split_address(d['address'])
        base = next((x for x in rec.get('deps') or [] if x['key'] == d['key']), None)
        rows.append({'key': d['key'], 'name': d.get('name') or (base or {}).get('name') or '服务',
                     'type': d.get('type') or (base or {}).get('type') or envconfig.TYPE_WORDS.get(typ or '', typ or '服务'), 'address': d['address'],
                     'host': host, 'port': port, 'line': None, 'note': note, 'from': 'user', 'replaces': bool(base),
                     'own': {'name': d.get('name') or '', 'type': d.get('type') or ''}})  # 存着的原样（界面编辑用，名称和类型可以空着跟配置文件走）
    checks = ((loc.get('checks') or {}).get(env['id']) or {})
    for r in rows:
        c = (checks.get('results') or {}).get(r['key'])
        if c and c.get('host') == r.get('host') and c.get('port') == r.get('port'):
            r['check'] = {'status': c['status'], 'detail': c.get('detail', ''), 'at': checks.get('at')}
        else:
            r['check'] = None
    return rows


def env_check(root, ref, timeout=envconfig.TIMEOUT):
    """TCP connection to every dependency (no login, no request, no data). Reads the config file first if it never was."""
    home = main_checkout(root)
    env = find_env(home, ref)
    loc = read_local(home)
    if env['id'] not in loc['scans'] and env.get('config'):
        env_scan(home, env['id'])
        loc = read_local(home)
    rows = deps_view(home, env, loc)
    results = envconfig.check_all(rows, timeout)
    at = dt.datetime.now().isoformat(timespec='seconds')
    with local_state(home) as l2:
        l2['checks'][env['id']] = {'at': at, 'results': {r['key']: dict(res, host=r.get('host'), port=r.get('port')) for r, res in zip(rows, results)}}
    rows = deps_view(home, env, read_local(home))
    counts = {k: sum(1 for r in rows if (r['check'] or {}).get('status') == k) for k in ('ok', 'bad', 'none')}
    return {'id': env['id'], 'name': env['name'], 'at': at, 'deps': rows, 'counts': counts}


def _b64(part):
    part = part + '=' * (-len(part) % 4)
    return base64.urlsafe_b64decode(part.encode('ascii'))


def jwt_info(token, now=None):
    """Read-only: the header and payload of a JWT (base64 only, the signature is not verified), its expiry and whether it has passed.
    None when the value does not look like a JWT."""
    t = str(token or '').strip()
    if t.lower().startswith('bearer '):
        t = t[7:].strip()
    parts = t.split('.')
    if len(parts) != 3 or not all(re.match(r'^[A-Za-z0-9_-]*$', p) for p in parts) or not parts[0] or not parts[1]:
        return None
    try:
        header = json.loads(_b64(parts[0]).decode('utf-8'))
        payload = json.loads(_b64(parts[1]).decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        return {'error': '看起来像 JWT，但解不出来。'}
    if not isinstance(header, dict) or not isinstance(payload, dict):
        return {'error': '看起来像 JWT，但内容不是 JSON 对象。'}
    out = {'header': header, 'payload': payload, 'exp': None, 'expires_at': None, 'expired': None, 'verified': False}
    exp = payload.get('exp')
    if isinstance(exp, (int, float)) and not isinstance(exp, bool):
        when = dt.datetime.fromtimestamp(exp, tz=dt.timezone.utc)
        out['exp'] = exp
        out['expires_at'] = when.astimezone().isoformat(timespec='minutes')
        out['expired'] = (now or dt.datetime.now(dt.timezone.utc)) >= when
    for k in ('iat', 'nbf'):
        v = payload.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out[f'{k}_at'] = dt.datetime.fromtimestamp(v, tz=dt.timezone.utc).astimezone().isoformat(timespec='minutes')
    return out


def env_resolve(root, ref=None, user=None, reveal=False):
    """Which headers and query parameters a request would carry with this environment and test user: {{NAME}} is the
    environment's variable, {{user.key}} the test user's value. Secret results are masked unless reveal (the app only)."""
    home = main_checkout(root)
    loc = read_local(home)
    ref = ref or in_use(home, loc)
    if not ref:
        raise ValueError('现在没有在用的测试环境；要说用哪一套。')
    env = find_env(home, ref)
    sec = loc['secrets'].get(env['id']) or {}
    users = env.get('users') or []
    uid = user or loc['user'].get(env['id']) or (users[0]['id'] if users else None)
    u = next((x for x in users if x['id'].upper() == str(uid or '').upper() or x.get('name') == uid), None)
    if user and not u:
        raise ValueError(f'{env["name"]} 里没有测试用户 {user}。')
    variables = {}
    secret_vars = set()
    for v in env.get('vars') or []:
        if v.get('secret'):
            variables[v['name']] = (sec.get('vars') or {}).get(v['name'], '')
            secret_vars.add(v['name'])
        else:
            variables[v['name']] = v.get('value', '')
    ufields, secret_fields = {}, set()
    for f in (u or {}).get('fields') or []:
        if f.get('secret'):
            ufields[f['key']] = ((sec.get('users') or {}).get(u['id']) or {}).get(f['key'], '')
            secret_fields.add(f['key'])
        else:
            ufields[f['key']] = f.get('value', '')
    rows = []
    for r in (env.get('request') or {}).get('items') or []:
        template = MASK if r.get('secret') and not reveal else ((sec.get('request') or {}).get(f'{r["in"]}:{r["name"]}', '') if r.get('secret') else r.get('value', ''))
        raw = (sec.get('request') or {}).get(f'{r["in"]}:{r["name"]}', '') if r.get('secret') else r.get('value', '')
        missing, secret = [], bool(r.get('secret'))
        refs = []

        def sub(m):
            nonlocal secret
            name = m.group(1)
            if name.startswith('user.'):
                key = name[5:]
                refs.append(name)
                if key in ufields and ufields[key] != '':
                    secret = secret or key in secret_fields
                    return str(ufields[key])
                missing.append(name)
                return m.group(0)
            refs.append(name)
            if name in variables and variables[name] != '':
                secret = secret or name in secret_vars
                return str(variables[name])
            missing.append(name)
            return m.group(0)
        value = TEMPLATE.sub(sub, raw)
        rows.append({'in': r['in'], 'where': WHERE[r['in']], 'name': r['name'], 'template': template, 'value': value if reveal or not secret else MASK,
                     'secret': secret, 'refs': refs, 'missing': missing})
    out = {'id': env['id'], 'name': env['name'], 'user': ({'id': u['id'], 'name': u['name']} if u else None),
           'headers': [r for r in rows if r['in'] == 'header'], 'query': [r for r in rows if r['in'] == 'query'],
           'scripts': {'before': bool(((env.get('request') or {}).get('before') or '').strip()), 'after': bool(((env.get('request') or {}).get('after') or '').strip())}}
    return out if reveal else redact(out, secret_values(home, env, loc))


WITHHELD = '这次没有带保密值（{names}）：在应用里运行，或在测试环境里勾选「允许 AI 的会话运行时带上保密值」。'


def withheld_note(names):
    return WITHHELD.format(names='、'.join(names)) if names else ''


def env_for_run(root, ref=None, none=False, trusted=None):
    """The environment a test run uses: the one named, else the one in use on this machine, else none. Returns None or
    {id, name, vars (values for the driver process and the preparation steps), secrets (every secret value, to scrub from
    records), withheld (secret variables left out), trusted, env (the settings)}.
    Secret values go in only when the user runs it from the app (trust.app_trusted) or the user allowed AI sessions to use them
    for this environment; otherwise (an AI session, or anything without the app's marker) only the non-secret variables."""
    if none:
        return None
    home = main_checkout(root)
    loc = read_local(home)
    ident = ref or in_use(home, loc)
    if not ident:
        return None
    env = find_env(home, ident)
    trusted = app_trusted() if trusted is None else bool(trusted)
    allowed = trusted or bool(loc['ai_secrets'].get(env['id']))
    if allowed and loc.get(HIDDEN):  # 在会话的沙箱里，你允许了带上保密值：经应用取（沙箱读不了保密值的文件）
        got = _values_from_app(env['id'])
        allowed = got is not None
        if allowed:
            loc['secrets'][env['id']] = got
    values = {k: v for k, v in _values(home, env, loc).items() if v != ''}
    secret_names = {v['name'] for v in env.get('vars') or [] if v.get('secret')}
    withheld = [] if allowed else sorted(n for n in secret_names if n in values)
    if withheld:
        values = {k: v for k, v in values.items() if k not in secret_names}
    return {'id': env['id'], 'name': env['name'], 'vars': values, 'secrets': secret_values(home, env, loc), 'withheld': withheld,
            'trusted': trusted, 'env': env}


def ask_app_to_run(argv):
    """Have the app run one command for this session (the same 'run' request as core/proxy.py, which sits a layer above):
    (exit code, stdout) or (None, '') when there is no session token, no app, or no answer."""
    token = os.environ.get(SIG_ENV) or ''
    _, path = find_app() if claims_of(token) else (None, None)
    answer = ask_app(path, {'op': 'run', 'token': token, 'argv': list(argv), 'stdin': None}, timeout=60) if path else None
    if not answer or not answer.get('ok'):
        return None, ''
    return int(answer.get('code') or 0), answer.get('stdout') or ''


def _values_from_app(ident):
    """The secret values of one environment, fetched by the app (rf.py env ai-values run on this session's behalf), or None."""
    code, out = ask_app_to_run(['env', 'ai-values', ident, '--json'])
    if code != 0:
        return None
    try:
        got = json.loads(out).get('secrets')
    except (ValueError, AttributeError):
        return None
    return got if isinstance(got, dict) else None


def ai_values(root, ref):
    """rf.py env ai-values ENV-001: the secret values of one environment, only when the user allowed AI sessions to use them
    (env ai-secrets). The app runs it for a session (core/proxy.py): the session's sandbox cannot read the secrets file.
    Not a way around anything: whoever can run it outside a sandbox can read that file anyway."""
    home = main_checkout(root)
    env = find_env(home, ref)
    loc = read_local(home)
    if loc.get(HIDDEN):
        raise ValueError('这里读不了保密的值（在会话的沙箱里）：要由应用代取。')
    if not loc['ai_secrets'].get(env['id']):
        raise ValueError(f'测试环境「{env["name"]}」没有允许 AI 的会话带上保密值：要用的话，在应用的测试环境里勾选。')
    return {'id': env['id'], 'secrets': loc['secrets'].get(env['id']) or {}}


def all_secret_values(home):
    loc = read_local(home)
    vals = set()
    for e in all_envs(home):
        vals.update(secret_values(home, e, loc))
    return sorted(vals, key=len, reverse=True)

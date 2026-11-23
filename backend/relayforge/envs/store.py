"""测试环境：一个项目可以有几套（本机、测试服…），每套一个文件，进 Git；标了保密的值、正在用哪一套、当前测试用户只存在这台电脑上。

  设置    .relayforge/envs/ENV-001.json            进 Git。名称、说明、怎么启动、用哪个配置文件、手改的依赖、环境变量、请求的通用设置、测试用户。
                                                    标了保密的项只留名字和 "secret": true，不留值。
  本机的部分不进 Git，权限 600（目录 700），项目标识和会话存档一样（archive.project_key）。按 AI 的会话能碰到的程度分成三个文件：
    $RELAYFORGE_HOME/envs/<项目标识>.json          保密的值。会话的沙箱读不了也写不了（app/src/main/claude-settings.js 的 sandbox）。
    $RELAYFORGE_HOME/env-consents/<项目标识>.json  你的决定：同意执行的命令（approved）、允许 AI 的会话带上保密值（ai_secrets）、
                                                   正在用哪一套（use）、每套当前的测试用户（user），以及哪些保密项填了值（secret_keys：结构和保密的值一样，值都换成打码）。
                                                   会话能读不能写：写得了就能冒充你同意，应用之后会在沙箱外按它执行。
    $RELAYFORGE_HOME/env-state/<项目标识>.json     上次读配置文件的结果、上次连通检查的结果、AI 交回的提议、准备步骤上次执行的情况。会话能读能写。
  local_state() 照旧给出合在一起的一份，只把改过的那部分写回它自己的文件。在沙箱里读不了保密的值时，secrets 里放的是打码的占位
  （按 secret_keys），能看出哪些填了，拿不到值；用户允许了 AI 的会话带上保密值时，运行前经应用取值（values.env_for_run）。
  以前三样在同一个文件里（$RELAYFORGE_HOME/envs/<项目标识>.json），第一次在沙箱外读到时拆开。

AI 的会话只能看打码的设置（env list、env show）、重新读配置（env scan）、检查连通（env check），以及用 env propose 交回建议；
写设置、取明文（env reveal）、采纳或忽略建议、选用哪一套和当前测试用户只由用户在应用里做（见 FORMAT.md 的「测试环境」）。
连通检查只做 TCP 连接，不登录、不发业务请求、不写数据。
"""

import contextlib
import copy
import json
import os
import re
from ..core.language import display_text
from pathlib import Path
try:
    import fcntl
except ImportError:  # not on this platform; writes are then not serialised between processes
    fcntl = None

from ..core.common import DIR, commit_facts, main_checkout, project_key, relayforge_home, today
from ..core.trust import terminal_note

__all__ = ['MASK']

ENVS = f'{DIR}/envs'
MASK = '••••••'
ID = re.compile(r'^ENV-\d{3,}$', re.I)
VAR_NAME = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')
FIELD_KEY = re.compile(r'^[A-Za-z_][A-Za-z0-9_.-]*$')
WHERE = {'header': '请求头', 'query': '地址参数'}
SECRET_WORDS = re.compile(r'(?i)(password|passwd|pwd|secret|token|api[_-]?key|private[_-]?key|credential|access[_-]?key|jwt)')
TEMPLATE = re.compile(r'\{\{\s*([A-Za-z_][\w.-]*)\s*\}\}')
REDACT_MIN = 3  # 比这更短的保密值不在输出里替换（太短的替换会把正常内容也打码），见 FORMAT.md


# ---------- 位置和读写 ----------

def envs_dir(home):
    return Path(home) / ENVS


def env_path(home, ident):
    return envs_dir(home) / f'{ident}.json'


def local_path(home):
    """The secret values (see the module docstring); the sessions' sandbox can neither read nor write it."""
    return relayforge_home() / 'envs' / f'{project_key(main_checkout(home))}.json'


def consent_path(home):
    return relayforge_home() / 'env-consents' / f'{project_key(main_checkout(home))}.json'


def state_path(home):
    return relayforge_home() / 'env-state' / f'{project_key(main_checkout(home))}.json'


CONSENT_KEYS = ('use', 'user', 'approved', 'ai_secrets', 'secret_keys')
STATE_KEYS = ('scans', 'checks', 'proposals', 'prep_runs')
HIDDEN = '_secrets_hidden'  # 只在内存里：这次读不了保密的值（在沙箱里），secrets 里是占位


def _private_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with contextlib.suppress(OSError):
        os.chmod(path.parent, 0o700)
    tmp = path.with_name(path.name + '.tmp')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write('\n')
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _read_part(path):
    """(data, readable): a missing or broken file is empty; a file the sandbox does not let us read is (None, False)."""
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {}, True
    except PermissionError:
        return None, False
    except (OSError, ValueError):
        return {}, True
    return (data if isinstance(data, dict) else {}), True


def _secret_keys(secrets):
    """Which secret items have a value: the same nesting as the secrets, every value replaced by MASK, empty ones left out."""
    if isinstance(secrets, dict):
        out = {k: _secret_keys(v) for k, v in secrets.items()}
        return {k: v for k, v in out.items() if v not in (None, '', {})}
    return None if secrets in (None, '') else MASK


def _split(data):
    """The three files' contents from the merged dict (see the module docstring)."""
    project = data.get('project')
    secret = {'project': project, 'secrets': data.get('secrets') or {}}
    consent = dict({k: data.get(k) for k in CONSENT_KEYS}, project=project)
    state = {k: v for k, v in data.items() if k not in ('secrets', HIDDEN, *CONSENT_KEYS)}
    return {'secret': secret, 'consent': consent, 'state': state}


def _dump(part):
    return json.dumps(part, sort_keys=True, ensure_ascii=False)


@contextlib.contextmanager
def local_state(home, write=True):
    """The machine-local part for this project (secrets, choices, last scan and check, proposals), under a lock.
    One merged dict as before; only the parts that changed are written back, each to its own file."""
    paths = {'secret': local_path(home), 'consent': consent_path(home), 'state': state_path(home)}
    lock_dir = paths['state'].parent  # 会话也能写的地方：沙箱里的 rf.py 也要拿这把锁
    try:
        lock_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        handle = open(lock_dir / '.lock', 'a')
    except PermissionError:
        if write:
            raise ValueError(display_text('这里写不了测试环境的本机状态（会话的沙箱不让写）：请在应用里操作。',
                                          'The sandbox blocks changes to local test environment state. Make the change in the app.')) from None
        handle = None  # 只读：Codex 的沙箱连锁文件也建不了，照样能看
    try:
        if fcntl and handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
        secret, readable = _read_part(paths['secret'])
        consent, _ = _read_part(paths['consent'])
        state, _ = _read_part(paths['state'])
        consent, state = consent or {}, state or {}
        old = readable and any(k not in ('project', 'secrets') for k in secret)
        if old:  # 以前的一个文件：你的决定和其余状态搬到各自的文件里（这两个文件还没有时）
            consent = consent or {k: secret.get(k) for k in CONSENT_KEYS if k in secret}
            state = state or {k: v for k, v in secret.items() if k not in ('project', 'secrets', *CONSENT_KEYS)}
        data = dict(state, **{k: consent.get(k) for k in CONSENT_KEYS})
        data['secrets'] = (secret or {}).get('secrets') if readable else None
        data.setdefault('project', str(main_checkout(home)))
        data['project'] = data.get('project') or str(main_checkout(home))
        for k in ('user', 'secrets', 'scans', 'checks', 'proposals', 'approved', 'prep_runs', 'ai_secrets', 'secret_keys'):
            if not isinstance(data.get(k), dict):
                data[k] = {}
        if readable:
            data['secret_keys'] = _secret_keys(data['secrets'])
        else:  # 在沙箱里：只知道哪些填了值，值本身拿不到
            data['secrets'] = copy.deepcopy(data['secret_keys'])
            data[HIDDEN] = True
        before = copy.deepcopy(_split(data))  # 深拷贝：块里改的是 data 里的同一批字典
        # 顺手整理：旧的一个文件要拆开；保密的值改过而 secret_keys 没跟上（比如旧版应用写的）要补上
        tidy = {'secret': old, 'state': old, 'consent': old or (readable and data['secret_keys'] != (consent.get('secret_keys') or {}))}
        yield data
        if readable:
            data['secret_keys'] = _secret_keys(data['secrets'])
        after = _split(data) if write else before
        for name in ('secret', 'consent', 'state'):
            changed = write and _dump(after[name]) != _dump(before[name])
            if not (changed or tidy[name]):
                continue
            if name == 'secret' and not readable:
                raise ValueError(display_text('保密的值只能由你在应用里改：AI 的会话（沙箱里）读不了也写不了。',
                                              'Edit secret values in the app. Sandboxed sessions cannot read or write them.'))
            try:
                _private_write(paths[name], after[name])
            except PermissionError:
                if not changed:  # 只是顺手整理，写不了（在沙箱里）就下次再说
                    continue
                raise ValueError(display_text('这一项只能由你在应用里改：AI 的会话（沙箱里）写不了你的决定，比如同意执行的命令、'
                                 '允许带上保密值、选用哪一套测试环境。',
                                              'Change approvals, secret access and the active test environment in the app.')) from None
    finally:
        if handle:
            handle.close()


def read_local(home):
    with local_state(home, write=False) as data:
        return copy.deepcopy(data)


def _load(path):
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def all_envs(home):
    out = []
    for p in sorted(envs_dir(home).glob('ENV-*.json')):
        data = _load(p)
        if data and data.get('id'):
            out.append(data)
    return out


def find_env(home, ref):
    """By id (ENV-001, case-insensitive) or by exact name."""
    ref = str(ref or '').strip()
    if not ref:
        raise ValueError(display_text('要说哪一套测试环境（编号 ENV-001 或名称）。',
                                      'Enter a test environment ID (such as ENV-001) or name.'))
    envs = all_envs(home)
    for e in envs:
        if e['id'].upper() == ref.upper() or e.get('name') == ref:
            return e
    raise ValueError(display_text(f'没有找到测试环境 {ref}。现有的：' + ('、'.join(f'{e["id"]} {e.get("name")}' for e in envs) or '还没有'),
                                  f'Test environment {ref} was not found. Existing environments: ' + (', '.join(f'{e["id"]} {e.get("name")}' for e in envs) or 'none')))


def write_env(home, env):
    env['updated'] = today()
    path = env_path(home, env['id'])
    path.parent.mkdir(parents=True, exist_ok=True)
    _check_no_values(env)
    path.write_text(json.dumps(_ordered(env), ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


ORDER = ('id', 'type', 'name', 'note', 'services', 'config', 'deps', 'vars', 'request', 'users', 'next_user', 'prep', 'next_prep', 'status', 'removed', 'created', 'updated', 'log')


def _ordered(env):
    return {**{k: env[k] for k in ORDER if k in env}, **{k: v for k, v in env.items() if k not in ORDER}}


def _check_no_values(env):
    """Last guard before the file goes into the repository: a secret item never carries a value."""
    for v in env.get('vars') or []:
        if v.get('secret') and 'value' in v:
            raise ValueError(display_text(f'内部错误：保密的环境变量 {v["name"]} 带着值，没有写。',
                                          f'Unable to save: secret environment variable {v["name"]} includes a value.'))
    for r in (env.get('request') or {}).get('items') or []:
        if r.get('secret') and 'value' in r:
            raise ValueError(display_text(f'内部错误：保密的{WHERE.get(r.get("in"), "")} {r["name"]} 带着值，没有写。',
                                          f'Unable to save: secret {r.get("in") or "request field"} {r["name"]} includes a value.'))
    for u in env.get('users') or []:
        for f in u.get('fields') or []:
            if f.get('secret') and 'value' in f:
                raise ValueError(display_text(f'内部错误：测试用户 {u.get("name")} 的保密项 {f["key"]} 带着值，没有写。',
                                              f'Unable to save: secret field {f["key"]} for test user {u.get("name")} includes a value.'))


def next_id(home):
    nums = [int(p.stem.split('-')[1]) for p in list(envs_dir(home).glob('ENV-*.json')) + list((Path(home) / DIR / 'history' / 'envs').glob('ENV-*.json'))
            if p.stem.split('-')[1].isdigit()]
    return f'ENV-{(max(nums) if nums else 0) + 1:03d}'


def _log(env, text):
    env.setdefault('log', []).append(terminal_note(f'{today()} {text}'))


def _commit(home, text):
    commit_facts(home, f'测试环境：{text}')


# ---------- 新建、复制、改名、删除、选用 ----------


def _check_name(home, name, own=None):
    name = str(name or '').strip()
    if not name:
        raise ValueError(display_text('给这套环境起个名字（比如：本机、测试服）。',
                                      'Name the environment, for example Local or Staging.'))
    if len(name) > 40:
        raise ValueError(display_text('名字太长了，40 个字以内。',
                                      'Use at most 40 characters for the environment name.'))
    for e in all_envs(home):
        if e.get('name') == name and e['id'] != own:
            raise ValueError(display_text(f'已经有一套叫「{name}」的环境（{e["id"]}）。',
                                          f'An environment named “{name}” already exists ({e["id"]}).'))
    return name


def in_use(home, loc=None):
    loc = loc if loc is not None else read_local(home)
    ident = loc.get('use')
    if ident and env_path(home, ident).exists():
        return ident
    return None


# ---------- 保存（整份，来自应用） ----------

def _text(v, cap=2000):
    return str(v if v is not None else '').strip()[:cap]


def _port(v, where):
    if v in (None, ''):
        return None
    try:
        p = int(str(v).strip())
    except ValueError:
        raise ValueError(display_text(f'{where}的端口要是数字：{v}',
                                      f'The port for {where} must be a number: {v}.')) from None
    if not 1 <= p <= 65535:
        raise ValueError(display_text(f'{where}的端口要在 1 到 65535 之间：{p}',
                                      f'The port for {where} must be between 1 and 65535: {p}.'))
    return p


def _rel(v, where):
    v = _text(v, 400).replace('\\', '/')
    if not v:
        return ''
    if v.startswith('/') or re.match(r'^[A-Za-z]:', v) or '..' in Path(v).parts:
        raise ValueError(display_text(f'{where}要写项目里的相对路径（不能是绝对路径，也不能有 ..）：{v}',
                                      f'Use a path relative to the project for {where}, without any .. segments: {v}.'))
    return v.strip('/')


# ---------- 准备步骤：数据 ----------

WHEN = {'always': '每次运行前', 'changed': '这些文件变了以后'}
PREP_TIMEOUT = 600
PREP_MAX = 3600
TAIL = 3000

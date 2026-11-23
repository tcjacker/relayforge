"""清洗应用保存过来的整份测试环境设置：服务、验收变量、准备步骤，逐项检查和规整，不合规的给出能看懂的原因。

测试环境的存储在 envs.py。分层见 tests/test_layers.py。
"""
import re

from ..core.language import display_text
from . import config as envconfig
from .store import _port, _rel, _text, FIELD_KEY, PREP_MAX, PREP_TIMEOUT, SECRET_WORDS, VAR_NAME, WHEN, WHERE


def clean_accept(items, where):
    """A service's 「验收时额外带的环境变量」: [{name, value}], values may use {{req}} {{worktree}} {{scratch}} {{project}} {{project_copy}}.
    Not for secrets (they go in the environment's variables, which the service gets anyway)."""
    if items in (None, ''):
        return []
    if not isinstance(items, list):
        raise ValueError(display_text(f'{where}要是一个列表。',
                                      f'{where} must be a list.'))
    out = []
    for a in items:
        if not isinstance(a, dict):
            raise ValueError(display_text(f'{where}的每一项要是对象 {{"name", "value"}}。',
                                          f'Each item in {where} must be an object with a name and value.'))
        name, value = _text(a.get('name'), 100), _text(a.get('value'), 1000)
        if not name and not value:
            continue
        if not VAR_NAME.match(name):
            raise ValueError(display_text(f'{where}的名称只能用字母、数字和下划线，不能以数字开头：「{name}」',
                                          f'Invalid name “{name}” in {where}. Use letters, digits and underscores, starting with a letter or underscore.'))
        if SECRET_WORDS.search(name):
            raise ValueError(display_text(f'{where}里的「{name}」看起来是保密的：保密的值放进环境变量（标保密），这里只放目录、标记这类不保密的设置。',
                                          f'“{name}” in {where} looks sensitive. Put secrets in environment variables and mark them as secret.'))
        out.append({'name': name, 'value': value})
    if len({a['name'] for a in out}) != len(out):
        raise ValueError(display_text(f'{where}里有重名的。',
                                      f'{where} contains duplicate names.'))
    return out


def clean_service(s, i, where='「怎么启动」'):
    name, command = _text(s.get('name'), 40), _text(s.get('command'), 1000)
    service = display_text(f'服务「{name}」', f'service “{name}”')
    body = {'name': name, 'command': command, 'port': _port(s.get('port'), service), 'dir': _rel(s.get('dir'), display_text(f'{service}的工作目录', f'{service} working directory'))}
    accept = clean_accept(s.get('accept'), display_text(f'{service}验收时额外带的环境变量', f'acceptance variables for {service}'))
    if accept:
        body['accept'] = accept
    # 代码变了自动重启：默认开，文件里只在关着时写 auto_restart: false（老数据没有这个字段就是开）
    flag = s.get('auto_restart', True)
    if flag is None or flag == '':
        flag = True
    if not isinstance(flag, bool):
        raise ValueError(display_text(f'服务「{name}」的「代码变了自动重启」只能是开或关。',
                                      f'Automatic restart for service “{name}” must be on or off.'))
    if not flag:
        body['auto_restart'] = False
    return body


def _clean_services(payload):
    """「怎么启动」：每个服务清洗一遍（clean_service），空行跳过，名称不能空、不能重复。"""
    services = []
    for i, s in enumerate(payload.get('services') or [], 1):
        if not isinstance(s, dict):
            raise ValueError(display_text('「怎么启动」的每一项要是对象。',
                                          'Each service must be an object.'))
        name, command = _text(s.get('name'), 40), _text(s.get('command'), 1000)
        if not name and not command and s.get('port') in (None, '') and not s.get('dir') and not s.get('accept'):
            continue
        if not name:
            raise ValueError(display_text(f'「怎么启动」第 {i} 个服务没有名称（后端、前端…）。',
                                          f'Enter a name for service {i}, such as Backend or Frontend.'))
        services.append(clean_service(s, i))
    if len({s['name'] for s in services}) != len(services):
        raise ValueError(display_text('「怎么启动」里有两个服务同名。',
                                      'Service names must be unique.'))
    return services


def _clean_deps(payload):
    """手改的依赖：只收用户写的（from user），地址打码后保存。"""
    deps = []
    for i, d in enumerate(payload.get('deps') or [], 1):
        if not isinstance(d, dict) or d.get('from', 'user') != 'user':
            continue
        address = _text(d.get('address'), 500)
        name = _text(d.get('name'), 40)
        if not address and not name:
            continue
        if not address:
            raise ValueError(display_text(f'手改的依赖「{name}」没有地址。',
                                          f'Enter an address for dependency “{name}”.'))
        deps.append({'key': _text(d.get('key'), 200) or f'user:{i}', 'name': name or '服务', 'type': _text(d.get('type'), 40),
                     'address': envconfig.mask_url(address)})
    return deps


def _clean_vars(payload, secrets, keep):
    """环境变量：保密的值放进 secrets（值为 null 的记进 keep，沿用本机已有的），其余照存。"""
    vars_ = []
    for v in payload.get('vars') or []:
        if not isinstance(v, dict):
            raise ValueError(display_text('环境变量的每一项要是对象。',
                                          'Each environment variable must be an object.'))
        name = _text(v.get('name'), 100)
        if not name and not _text(v.get('value')):
            continue
        if not VAR_NAME.match(name):
            raise ValueError(display_text(f'环境变量的名称只能用字母、数字和下划线，不能以数字开头：「{name}」',
                                          f'Invalid environment variable name “{name}”. Use letters, digits and underscores, starting with a letter or underscore.'))
        item = {'name': name, 'secret': bool(v.get('secret'))}
        if item['secret']:
            if v.get('value') is None:
                keep['vars'].add(name)
            else:
                secrets['vars'][name] = str(v.get('value'))
        else:
            item['value'] = str(v.get('value') if v.get('value') is not None else '')
        vars_.append(item)
    if len({v['name'] for v in vars_}) != len(vars_):
        raise ValueError(display_text('环境变量里有重名的。',
                                      'Environment variable names must be unique.'))
    return vars_


def _clean_request(payload, secrets, keep):
    """请求的通用设置：每项放在请求头或地址参数里；保密的值同环境变量。"""
    req = payload.get('request') if isinstance(payload.get('request'), dict) else {}
    items = []
    for r in req.get('items') or []:
        if not isinstance(r, dict):
            raise ValueError(display_text('请求的通用设置的每一项要是对象。',
                                          'Each shared request field must be an object.'))
        name = _text(r.get('name'), 200)
        if not name and not _text(r.get('value')):
            continue
        where = r.get('in') or 'header'
        if where not in WHERE:
            raise ValueError(display_text(f'「{name}」放在哪只能是请求头（header）或地址参数（query）。',
                                          f'Choose header or query for “{name}”.'))
        if not name or re.search(r'[\s:]', name) and where == 'header':
            raise ValueError(display_text(f'请求头的名称不能空、不能有空格和冒号：「{name}」',
                                          f'Invalid header name “{name}”. Enter a name without spaces or colons.'))
        item = {'in': where, 'name': name, 'secret': bool(r.get('secret'))}
        k = f'{where}:{name}'
        if item['secret']:
            if r.get('value') is None:
                keep['request'].add(k)
            else:
                secrets['request'][k] = str(r.get('value'))
        else:
            item['value'] = str(r.get('value') if r.get('value') is not None else '')
        items.append(item)
    seen = [(i['in'], i['name'].lower() if i['in'] == 'header' else i['name']) for i in items]
    if len(set(seen)) != len(seen):
        raise ValueError(display_text('请求的通用设置里有重复的（同一个位置、同一个名称）。',
                                      'Shared request fields must have unique names within each location.'))
    return {'items': items, 'before': str(req.get('before') or '')[:100000], 'after': str(req.get('after') or '')[:100000]}


def _clean_users(env, payload, secrets, keep):
    """测试用户：编号由程序给（U1、U2…，不重复用），每项名称规范，保密的值同环境变量。返回 (用户列表, 下一个编号)。"""
    users = []
    used_ids = {u['id'] for u in env.get('users') or []}
    nxt = int(env.get('next_user') or 1)
    for u in payload.get('users') or []:
        if not isinstance(u, dict):
            raise ValueError(display_text('测试用户的每一项要是对象。',
                                          'Each test user must be an object.'))
        name = _text(u.get('name'), 40)
        if not name:
            raise ValueError(display_text('每个测试用户要有名称（测试用户 A、管理员…）。',
                                          'Enter a name for every test user.'))
        uid = str(u.get('id') or '')
        if not re.match(r'^U\d+$', uid):
            while f'U{nxt}' in used_ids:
                nxt += 1
            uid = f'U{nxt}'
            nxt += 1
        used_ids.add(uid)
        fields = []
        for f in u.get('fields') or []:
            if not isinstance(f, dict):
                continue
            key = _text(f.get('key'), 60)
            if not key and not _text(f.get('value')):
                continue
            if not FIELD_KEY.match(key):
                raise ValueError(display_text(f'测试用户「{name}」的项名只能用字母、数字、下划线、点和横线，不能以数字开头：「{key}」',
                                              f'Invalid field name “{key}” for test user “{name}”. Use letters, digits, underscores, dots and hyphens, starting without a digit.'))
            item = {'key': key, 'secret': bool(f.get('secret'))}
            if item['secret']:
                if f.get('value') is None:
                    keep['users'].setdefault(uid, set()).add(key)
                else:
                    secrets['users'].setdefault(uid, {})[key] = str(f.get('value'))
            else:
                item['value'] = str(f.get('value') if f.get('value') is not None else '')
            fields.append(item)
        if len({f['key'] for f in fields}) != len(fields):
            raise ValueError(display_text(f'测试用户「{name}」里有重名的项。',
                                          f'Field names for test user “{name}” must be unique.'))
        users.append({'id': uid, 'name': name, 'note': _text(u.get('note'), 200), 'fields': fields})
    if len({u['id'] for u in users}) != len(users):
        raise ValueError(display_text('测试用户的编号重复了。',
                                      'Test user IDs must be unique.'))
    return users, nxt


def clean_payload(env, payload):
    """Check what the app sends and split it: the repository part (no secret values) and the secrets {vars, request, users}.
    A secret item whose value is null keeps the value stored on this machine."""
    if not isinstance(payload, dict):
        raise ValueError(display_text('要保存的设置要是一个 JSON 对象。',
                                      'Supply the settings as a JSON object.'))
    out = {k: env.get(k) for k in ('id', 'type', 'created', 'updated', 'log', 'next_user', 'next_prep')}
    out['type'] = 'testenv'
    out['name'] = _text(payload.get('name', env.get('name')), 40)
    out['note'] = _text(payload.get('note', env.get('note')), 200)
    out['services'] = _clean_services(payload)
    out['config'] = _rel(payload.get('config', env.get('config')), display_text('配置文件', 'configuration file'))
    out['deps'] = _clean_deps(payload)
    secrets = {'vars': {}, 'request': {}, 'users': {}}
    keep = {'vars': set(), 'request': set(), 'users': {}}
    out['vars'] = _clean_vars(payload, secrets, keep)
    out['request'] = _clean_request(payload, secrets, keep)
    out['users'], out['next_user'] = _clean_users(env, payload, secrets, keep)
    out['prep'], out['next_prep'] = clean_prep(env, payload.get('prep', env.get('prep')))
    return out, secrets, keep


def _clean_step(s, where):
    if not isinstance(s, dict):
        raise ValueError(display_text(f'{where}要是一个对象。',
                                      f'{where} must be an object.'))
    command = str(s.get('command') or '').strip()[:2000]
    if not command:
        raise ValueError(display_text(f'{where}没有命令。',
                                      f'Enter a command for {where}.'))
    when = str(s.get('when') or 'always').strip()
    if when not in WHEN:
        raise ValueError(display_text(f'{where}的「什么时候执行」只能是 always（每次运行前）或 changed（某些文件变了以后）：{when}',
                                      f'Invalid schedule for {where}: {when}. Use always (before every run) or changed (after a watched file changes).'))
    watch = s.get('watch') or []
    if isinstance(watch, str):
        watch = re.split(r'[\n,，]+', watch)
    if not isinstance(watch, list):
        raise ValueError(display_text(f'{where}的 watch 要是文件列表。',
                                      f'The watch field for {where} must be a list of files.'))
    watch = [w for w in (_rel(x, display_text(f'{where}要看的文件', f'files watched by {where}')) for x in watch if str(x or '').strip()) if w][:20]
    if when == 'changed' and not watch:
        raise ValueError(display_text(f'{where}写了「文件变了以后才执行」，要写看哪些文件（相对项目，比如 pom.xml、web/package-lock.json）。',
                                      f'Enter the files to watch for {where}, relative to the project directory, such as pom.xml or web/package-lock.json.'))
    raw = s.get('timeout')
    try:
        timeout = int(str(raw).strip()) if raw not in (None, '') else PREP_TIMEOUT
    except ValueError:
        raise ValueError(display_text(f'{where}的超时要写秒数：{raw}',
                                      f'Enter the timeout for {where} in seconds: {raw}.')) from None
    if not 1 <= timeout <= PREP_MAX:
        raise ValueError(display_text(f'{where}的超时要在 1 到 {PREP_MAX} 秒之间：{timeout}',
                                      f'The timeout for {where} must be between 1 and {PREP_MAX} seconds: {timeout}.'))
    note = _text(s.get('note'), 80) or command.splitlines()[0][:40]
    return {'note': note, 'command': command, 'dir': _rel(s.get('dir'), display_text(f'{where}的执行目录', f'working directory for {where}')), 'when': when, 'watch': watch if when == 'changed' else [], 'timeout': timeout}


def clean_prep(env, items):
    """The preparation steps as the user saves them: ordered, each {id, note, command, dir, when, watch, timeout}. ids (P1, P2…)
    are kept for existing steps and given by the program for new ones, never reused."""
    if items is None:
        items = []
    if not isinstance(items, list):
        raise ValueError(display_text('准备步骤要是一个列表。',
                                      'Preparation steps must be a list.'))
    nxt = int(env.get('next_prep') or 1)
    used = {p.get('id') for p in env.get('prep') or []}
    out, seen = [], set()
    for i, s in enumerate(items, 1):
        if isinstance(s, dict) and not str(s.get('command') or '').strip() and not str(s.get('note') or '').strip():
            continue
        step = _clean_step(s, display_text(f'准备步骤第 {i} 条', f'preparation step {i}'))
        pid = str(s.get('id') or '')
        if not re.match(r'^P\d+$', pid) or pid in seen or (pid not in used and int(pid[1:]) < nxt):
            while f'P{nxt}' in used or f'P{nxt}' in seen:
                nxt += 1
            pid = f'P{nxt}'
        nxt = max(nxt, int(pid[1:]) + 1)
        seen.add(pid)
        out.append({'id': pid, **step})
    return out, nxt


SECTION_WORDS = (('name', '名称'), ('note', '说明'), ('services', '怎么启动'), ('config', '配置文件'), ('deps', '手改的依赖'), ('vars', '环境变量'),
                 ('request', '请求的通用设置'), ('users', '测试用户'), ('prep', '准备步骤'))

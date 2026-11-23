"""测试环境的外部依赖：从项目自己的配置文件里读出数据库、缓存、外部服务的地址，并检查能不能连上（只做 TCP 连接）。

只用标准库。支持三种配置文件：
  - Spring 的 application*.yml / .yaml：一个够用的缩进式 YAML 读取（映射、列表、带引号和不带引号的值、注释、--- 分隔的多份文档）；
    锚点和别名（& *）、多行文本（| >）、行内的 { } 映射、标签（!!）不支持：跳过那一段并报告第几行、为什么。
  - .properties：key=value 或 key: value，# 和 ! 开头的是注释，行尾 \\ 续行。
  - .env（.env、.env.local、xxx.env）：KEY=value，可以有 export 前缀和引号。
认出来的键（认不出来的不猜）：
  - 数据库：spring.datasource.url、spring.datasource.*.url / jdbc-url、spring.r2dbc.url、DATABASE_URL、DB_URL、JDBC_URL、*_DATABASE_URL；
    DB_HOST + DB_PORT。
  - Redis：spring.data.redis.* / spring.redis.*（url，或 host + port）、REDIS_URL、REDIS_HOST + REDIS_PORT。
  - MongoDB：spring.data.mongodb.uri / host + port、MONGODB_URI、MONGO_URL。
  - RabbitMQ：spring.rabbitmq.host + port / addresses、AMQP_URL、RABBITMQ_URL。Kafka：spring.kafka.bootstrap-servers、KAFKA_BOOTSTRAP_SERVERS、KAFKA_BROKERS。
  - Elasticsearch：spring.elasticsearch.uris。
  - HTTP 地址：键以 url、uri、base-url、baseUrl、endpoint 结尾（或 .env 里以 _URL、_URI、_ENDPOINT 结尾）而且值是 http:// 或 https:// 开头的。
  - 其他：键以 .host 结尾、同一层有 .port 的，记成「TCP 服务」。
值里的 Spring 占位符 ${NAME:默认值}：先用这套环境的环境变量，没有就用默认值；都没有就留着，这一项算「没配」。
地址里的口令（user:password@）一律打码，不记、不显示。
"""

import concurrent.futures
import re
import socket
from pathlib import Path
from urllib.parse import urlsplit

__all__ = ['parse_config', 'config_kind', 'find_configs', 'recognize', 'split_address', 'check_target', 'check_all', 'mask_url']

KINDS = {'yaml': 'YAML', 'properties': 'properties', 'dotenv': '.env'}
DEFAULT_PORTS = {'mysql': 3306, 'mariadb': 3306, 'postgresql': 5432, 'postgres': 5432, 'sqlserver': 1433, 'oracle': 1521, 'redis': 6379, 'rediss': 6379,
                 'mongodb': 27017, 'amqp': 5672, 'amqps': 5671, 'kafka': 9092, 'http': 80, 'https': 443, 'elasticsearch': 9200}
TYPE_WORDS = {'mysql': 'MySQL', 'mariadb': 'MariaDB', 'postgresql': 'PostgreSQL', 'postgres': 'PostgreSQL', 'sqlserver': 'SQL Server', 'oracle': 'Oracle',
              'h2': 'H2', 'sqlite': 'SQLite', 'redis': 'Redis', 'rediss': 'Redis', 'mongodb': 'MongoDB', 'amqp': 'RabbitMQ', 'amqps': 'RabbitMQ',
              'kafka': 'Kafka', 'elasticsearch': 'Elasticsearch', 'http': 'HTTP 接口', 'https': 'HTTP 接口', 'tcp': 'TCP 服务'}
SKIP_DIRS = {'.git', 'node_modules', '.relayforge', '.worktrees', 'target', 'build', 'dist', '.venv', 'venv', '__pycache__', '.idea', '.gradle', 'out'}
TIMEOUT = 2.0


# ---------- 读配置文件 ----------

def config_kind(path):
    name = Path(path).name.lower()
    if name.endswith(('.yml', '.yaml')):
        return 'yaml'
    if name.endswith('.properties'):
        return 'properties'
    if name == '.env' or name.startswith('.env.') or name.endswith('.env'):
        return 'dotenv'
    return None


def find_configs(place, limit=60):
    """Candidate config files in the project, relative paths: Spring application*.yml/.yaml/.properties and .env files."""
    place = Path(place)
    found = []

    def walk(folder, depth):
        try:
            names = sorted(folder.iterdir(), key=lambda p: p.name)
        except OSError:
            return
        for p in names:
            if len(found) >= limit:
                return
            if p.is_dir():
                if depth and p.name not in SKIP_DIRS and not p.is_symlink():
                    walk(p, depth - 1)
                continue
            n = p.name.lower()
            spring = n.startswith(('application', 'bootstrap')) and config_kind(n) in ('yaml', 'properties')
            if spring or config_kind(n) == 'dotenv':
                found.append(p.relative_to(place).as_posix())
    walk(place, 6)
    return found


def _unquote(raw):
    v = raw.strip()
    if len(v) >= 2 and v[0] == v[-1] == '"':
        body = v[1:-1]
        return re.sub(r'\\(.)', lambda m: {'n': '\n', 't': '\t', '"': '"', '\\': '\\'}.get(m.group(1), m.group(1)), body)
    if len(v) >= 2 and v[0] == v[-1] == "'":
        return v[1:-1].replace("''", "'")
    return v


def _strip_comment(text):
    """Drop a trailing # comment that is not inside quotes (YAML: a # after a space)."""
    quote = None
    for i, ch in enumerate(text):
        if quote:
            if ch == quote:
                quote = None
        elif ch in '"\'':
            if i == 0 or text[i - 1] in ' \t:[,-':
                quote = ch
        elif ch == '#' and (i == 0 or text[i - 1] in ' \t'):
            return text[:i].rstrip()
    return text.rstrip()


def _yaml_scalar(raw):
    v = _strip_comment(raw).strip()
    if v in ('', '~', 'null', 'Null', 'NULL'):
        return None, None
    if v.startswith('[') and v.endswith(']'):  # a simple flow list: [a, b, "c"]
        inner = v[1:-1].strip()
        if any(ch in inner for ch in '[]{}'):
            return None, '行内嵌套的列表不支持'
        return [_unquote(x) for x in inner.split(',') if x.strip()] if inner else [], None
    if v[0] in '{':
        return None, '行内的 { } 映射不支持'
    if v[0] in '&*':
        return None, '锚点和别名（& *）不支持'
    if v.startswith('!'):
        return None, '标签（!!）不支持'
    if v in ('|', '>', '|-', '>-', '|+', '>+'):
        return None, '多行文本（| >）不支持'
    return _unquote(v), None


def parse_yaml(text):
    """A small indentation-based YAML reader, enough for Spring config. Returns (entries, skipped): entries are
    [(dotted key, value, line)], lists as key[0]; skipped is [(line, reason)]. Several documents (---) are read in order;
    a later document's key is listed after the earlier one (the later one wins when looking up)."""
    entries, skipped = [], []
    stack = []  # [(indent, key path, is_list_item_mapping)]
    counters = {}  # path -> next list index
    skip_below = None  # indent: skip lines deeper than this (inside an unsupported block)
    lines = text.splitlines()
    for no, line in enumerate(lines, 1):
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        if line.startswith(('---', '...')) and line[:3] in ('---', '...') and (len(line) == 3 or line[3] in ' \t'):
            stack, counters, skip_below = [], {}, None
            continue
        if line.startswith('%'):
            continue
        if '\t' in line[:len(line) - len(line.lstrip())]:
            skipped.append((no, '缩进里有制表符'))
            continue
        indent = len(line) - len(line.lstrip(' '))
        if skip_below is not None:
            if indent > skip_below:
                continue
            skip_below = None
        body = line.strip()
        while stack and stack[-1][0] >= indent:
            stack.pop()
        parent = stack[-1][1] if stack else ''
        if body.startswith('- ') or body == '-':
            idx = counters.get(parent, 0)
            counters[parent] = idx + 1
            path = f'{parent}[{idx}]'
            rest = body[1:].strip()
            if not rest:
                stack.append((indent, path))
                continue
            m = re.match(r'^("[^"]*"|\'[^\']*\'|[^:#\'"\s][^:#]*?)\s*:(\s+|$)(.*)$', rest)
            if m:
                # a mapping inside a list item: "- name: x" — its keys sit at the item's content indent
                item_indent = indent + len(body) - len(rest)
                stack.append((indent, path))
                key = _unquote(m.group(1))
                val, why = _yaml_scalar(m.group(3)) if m.group(3).strip() else (None, None)
                full = f'{path}.{key}'
                if why:
                    skipped.append((no, why))
                    skip_below = item_indent
                elif m.group(3).strip():
                    _emit(entries, full, val, no)
                else:
                    stack.append((item_indent, full))
                continue
            val, why = _yaml_scalar(rest)
            if why:
                skipped.append((no, why))
                skip_below = indent
            else:
                _emit(entries, path, val, no)
            continue
        m = re.match(r'^("[^"]*"|\'[^\']*\'|[^:#]+?)\s*:(\s+|$)(.*)$', body)
        if not m:
            skipped.append((no, '看不懂这一行（不是「键: 值」也不是列表项）'))
            skip_below = indent
            continue
        key = _unquote(m.group(1)).strip()
        if key.startswith(('&', '*', '!', '?')) or key == '<<':
            skipped.append((no, '锚点、别名、合并键（<<）或复杂键不支持'))
            skip_below = indent
            continue
        full = f'{parent}.{key}' if parent else key
        rest = m.group(3)
        if not _strip_comment(rest).strip():
            stack.append((indent, full))
            continue
        val, why = _yaml_scalar(rest)
        if why:
            skipped.append((no, why))
            skip_below = indent
            continue
        _emit(entries, full, val, no)
    return entries, skipped


def _emit(entries, key, val, no):
    if isinstance(val, list):
        for i, item in enumerate(val):
            entries.append((f'{key}[{i}]', item, no))
    else:
        entries.append((key, val, no))


def parse_properties(text):
    entries, skipped = [], []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        no = i + 1
        line = lines[i].lstrip()
        i += 1
        if not line or line[0] in '#!':
            continue
        while line.endswith('\\') and not line.endswith('\\\\') and i < len(lines):
            line = line[:-1] + lines[i].lstrip()
            i += 1
        m = re.match(r'^((?:\\.|[^=:\s])+)\s*[=:\s]\s*(.*)$', line)
        if not m:
            if re.match(r'^(?:\\.|[^=:\s])+$', line):
                entries.append((line, '', no))
                continue
            skipped.append((no, '看不懂这一行'))
            continue
        key = re.sub(r'\\(.)', r'\1', m.group(1))
        val = m.group(2).rstrip()
        entries.append((key, val, no))
    return entries, skipped


def parse_dotenv(text):
    entries, skipped = [], []
    for no, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('export '):
            line = line[7:].lstrip()
        m = re.match(r'^([A-Za-z_][A-Za-z0-9_.-]*)\s*=\s*(.*)$', line)
        if not m:
            skipped.append((no, '看不懂这一行（不是 KEY=值）'))
            continue
        val = m.group(2).strip()
        if val[:1] in '"\'':
            end = val.find(val[0], 1)
            if end < 0:
                skipped.append((no, '引号没有配对（多行的值不支持）'))
                continue
            val = _unquote(val[:end + 1])
        else:
            val = re.sub(r'\s+#.*$', '', val)
        entries.append((m.group(1), val, no))
    return entries, skipped


def parse_config(path):
    """(kind, entries, skipped) for one config file; raises ValueError when it cannot be read or is not a known kind."""
    path = Path(path)
    kind = config_kind(path)
    if not kind:
        raise ValueError(f'{path.name} 不是认得的配置文件：支持 application*.yml / .yaml、.properties、.env。')
    try:
        text = path.read_text(encoding='utf-8')
    except UnicodeDecodeError:
        text = path.read_text(encoding='latin-1')
    except OSError as error:
        raise ValueError(f'读不了 {path}：{error}') from None
    entries, skipped = {'yaml': parse_yaml, 'properties': parse_properties, 'dotenv': parse_dotenv}[kind](text)
    return kind, entries, skipped


# ---------- 认出依赖 ----------

PLACEHOLDER = re.compile(r'\$\{([^}:]+)(?::([^}]*))?\}')


def relaxed(name):
    """Spring's relaxed binding: spring.datasource.url <-> SPRING_DATASOURCE_URL."""
    return re.sub(r'[^A-Za-z0-9]+', '_', re.sub(r'\[(\d+)\]', r'_\1', name)).strip('_').upper()


def fill(value, env):
    """Replace ${NAME:default}: the environment's variable (exact or relaxed name), else the default. Returns (value, missing names)."""
    missing = []

    def sub(m):
        name, default = m.group(1).strip(), m.group(2)
        for k in (name, relaxed(name)):
            if k in env and env[k] not in (None, ''):
                return str(env[k])
        if default is not None:
            return default
        missing.append(name)
        return m.group(0)
    return PLACEHOLDER.sub(sub, str(value)), missing


def mask_url(text):
    """Hide a password written in an address (scheme://user:password@host, and jdbc password=... parameters)."""
    text = re.sub(r'(//[^/@:\s]*:)[^@/\s]*@', r'\1***@', str(text))
    return re.sub(r'(?i)((?:^|[;?&])(?:password|pwd|passwd)=)[^;&]*', r'\1***', text)


def split_address(address, kind_hint=None):
    """(type, host, port, note) from an address: a URL, a JDBC URL, or host:port. host is None when there is nothing to connect to."""
    a = str(address or '').strip()
    if not a:
        return kind_hint, None, None, '没配'
    low = a.lower()
    if low.startswith('jdbc:'):
        rest = a[5:]
        sub = rest.split(':', 1)[0].lower()
        if sub in ('h2', 'sqlite', 'hsqldb', 'derby'):
            if ':mem:' in low or sub == 'sqlite' or ':file:' in low or 'tcp://' not in low:
                return sub, None, None, '本机的嵌入式数据库，不用连'
        if sub == 'oracle':
            m = re.search(r'@(?://)?([^:/\s]+)(?::(\d+))?', rest)
            return 'oracle', (m.group(1) if m else None), int(m.group(2)) if m and m.group(2) else 1521, None if m else '看不出主机'
        if sub == 'sqlserver':
            m = re.match(r'sqlserver://([^:;/\\]+)(?:\\[^:;]+)?(?::(\d+))?', rest, re.I)
            return 'sqlserver', (m.group(1) if m else None), int(m.group(2)) if m and m.group(2) else 1433, None if m else '看不出主机'
        m = re.match(r'([a-z0-9]+)(?::[a-z]+)*://([^/?;]+)', rest, re.I)
        if not m:
            return sub, None, None, '看不出主机'
        hostport = m.group(2).split(',')[0].split('@')[-1]
        host, port = _hostport(hostport, DEFAULT_PORTS.get(sub))
        return sub, host, port, None
    if '://' in a:
        try:
            u = urlsplit(a.split(',')[0])
            scheme = u.scheme.lower()
            host = u.hostname
            port = u.port or DEFAULT_PORTS.get(scheme) or DEFAULT_PORTS.get(kind_hint or '')
        except ValueError:
            return kind_hint, None, None, '地址写得不对'
        scheme = {'postgres': 'postgresql', 'mongodb+srv': 'mongodb', 'rediss': 'redis'}.get(scheme, scheme)
        if not host:
            return scheme, None, None, '看不出主机'
        return scheme, host, port, None
    host, port = _hostport(a.split(',')[0], DEFAULT_PORTS.get(kind_hint or ''))
    if not host or not re.match(r'^[A-Za-z0-9._\-\[\]:]+$', host):
        return kind_hint, None, None, '看不出主机'
    if not port:
        return kind_hint, host, None, '没有端口'
    return kind_hint, host, port, None


def _hostport(text, default):
    text = text.strip()
    if text.startswith('['):
        m = re.match(r'^\[([^\]]+)\](?::(\d+))?', text)
        return (m.group(1), int(m.group(2)) if m.group(2) else default) if m else (None, None)
    if text.count(':') == 1:
        h, p = text.split(':')
        return h or None, int(p) if p.isdigit() else default
    return text or None, default


DB_KEYS = re.compile(r'^(spring\.datasource(\.[\w-]+)?\.(url|jdbc-url)|spring\.r2dbc\.url|spring\.flyway\.url|spring\.liquibase\.url)$')
HTTP_TAIL = re.compile(r'(\.|^)(url|uri|base-url|baseurl|base_url|endpoint|address|server-url|host)$', re.I)
ENV_DB = re.compile(r'^([A-Z0-9_]*_)?(DATABASE_URL|DB_URL|JDBC_URL|DATASOURCE_URL)$')


def recognize(entries, env=None):
    """Turn config entries into dependencies. Each: {key, name, type, address (password masked), host, port, line, note}.
    `env` is the environment's variables, used for ${...} placeholders. Only known keys; nothing is guessed."""
    env = env or {}
    flat = {}
    lines = {}
    for key, val, no in entries:
        if val is None or isinstance(val, (list, dict)):
            continue
        flat[key] = str(val)
        lines[key] = no
    low = {k.lower(): k for k in flat}
    used = set()
    deps = []

    def get(k):
        real = low.get(k.lower())
        return (real, flat[real]) if real else (None, None)

    def add(key, name, kind, address, keys=(), port_default=None):
        address, missing = fill(address, env)
        typ, host, port, note = split_address(address, kind)
        if missing:
            host, port, note = None, None, '地址里有没代入的占位符：' + '、'.join('${' + m + '}' for m in missing)
        if host and not port and port_default:
            port, note = port_default, None
        deps.append({'key': key, 'name': name, 'type': TYPE_WORDS.get(typ or kind, typ or kind or '服务'), 'address': mask_url(address), 'host': host, 'port': port,
                     'line': lines.get(key), 'note': note})
        used.update(keys or (key,))

    for k in list(flat):
        kl = k.lower()
        norm = kl.replace('_', '.') if k.isupper() else kl  # SPRING_DATASOURCE_URL in a .env
        if DB_KEYS.match(norm) or ENV_DB.match(k):
            add(k, '数据库', 'mysql', flat[k])
    # Redis / MongoDB / RabbitMQ：一个 url，或者 host 加 port
    for base, name, kind in (('spring.data.redis', '缓存', 'redis'), ('spring.redis', '缓存', 'redis'), ('spring.data.mongodb', '文档数据库', 'mongodb'),
                             ('spring.rabbitmq', '消息队列', 'amqp')):
        for tail in ('url', 'uri', 'addresses'):
            key, val = get(f'{base}.{tail}')
            if key:
                add(key, name, kind, val if '://' in val or tail == 'addresses' else val)
                break
        else:
            hk, hv = get(f'{base}.host')
            if hk:
                pk, pv = get(f'{base}.port')
                add(hk, name, kind, f'{hv}:{pv}' if pk else hv, (hk, pk) if pk else (hk,), DEFAULT_PORTS[kind])
    for env_key, name, kind in (('REDIS_URL', '缓存', 'redis'), ('MONGODB_URI', '文档数据库', 'mongodb'), ('MONGO_URL', '文档数据库', 'mongodb'),
                                ('MONGODB_URL', '文档数据库', 'mongodb'), ('AMQP_URL', '消息队列', 'amqp'), ('RABBITMQ_URL', '消息队列', 'amqp'),
                                ('CLOUDAMQP_URL', '消息队列', 'amqp')):
        key, val = get(env_key) if env_key in flat else (None, None)
        if key:
            add(key, name, kind, val)
    for prefix, name, kind in (('REDIS', '缓存', 'redis'), ('DB', '数据库', 'tcp'), ('DATABASE', '数据库', 'tcp'), ('MONGO', '文档数据库', 'mongodb'),
                               ('RABBITMQ', '消息队列', 'amqp')):
        hk = f'{prefix}_HOST'
        if hk in flat and hk not in used:
            pk = f'{prefix}_PORT'
            add(hk, name, kind, f'{flat[hk]}:{flat[pk]}' if pk in flat else flat[hk], (hk, pk) if pk in flat else (hk,), DEFAULT_PORTS.get(kind))
    for key_name in ('spring.kafka.bootstrap-servers', 'spring.kafka.bootstrap.servers', 'KAFKA_BOOTSTRAP_SERVERS', 'KAFKA_BROKERS'):
        key, val = get(key_name) if not key_name.isupper() else ((key_name, flat[key_name]) if key_name in flat else (None, None))
        if key and key not in used:
            add(key, '消息队列', 'kafka', val.split(',')[0])
    key, val = get('spring.elasticsearch.uris')
    if not key:
        key, val = get('spring.elasticsearch.uris[0]')
    if key:
        add(key, '搜索', 'elasticsearch', val.split(',')[0])
    # HTTP 地址：键看起来是地址、值是 http(s):// 开头的
    for k, v in flat.items():
        if k in used:
            continue
        filled, missing = fill(v, env)
        looks = HTTP_TAIL.search(k) or (k.isupper() and re.search(r'_(URL|URI|ENDPOINT|HOST|ADDR|ADDRESS)$', k))
        if looks and (re.match(r'^https?://', filled.strip(), re.I) or (missing and PLACEHOLDER.fullmatch(str(v).strip()) and not k.lower().endswith('host'))):
            add(k, _http_name(k), 'http', v)  # 整个值是没代进去的 ${…}：列出来，算「没配」
    # 其他：xxx.host 旁边有 xxx.port
    for k, v in flat.items():
        if k in used or not k.lower().endswith('.host'):
            continue
        pk = k[:-5] + '.port'
        if pk in flat and not re.match(r'^https?://', v):
            add(k, _http_name(k[:-5]), 'tcp', f'{v}:{flat[pk]}', (k, pk))
    return deps


def _http_name(key):
    base = re.sub(r'(?i)([._-]?(base[-_]?url|url|uri|endpoint|address|server-url|host|addr))$', '', key)
    base = re.sub(r'\[\d+\]', '', base)
    return base or key


# ---------- 连通检查 ----------

def check_target(host, port, timeout=TIMEOUT):
    """Only a TCP connection: no login, no request, no data. Returns (status, detail): ok | bad."""
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return 'ok', ''
    except socket.timeout:
        return 'bad', f'{timeout:g} 秒内没连上'
    except OSError as error:
        return 'bad', (error.strerror or str(error))


def check_all(deps, timeout=TIMEOUT):
    """For each dependency: {status: ok|bad|none, detail}. none = nothing to connect to (not configured, placeholder, embedded)."""
    out = [None] * len(deps)
    jobs = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        for i, d in enumerate(deps):
            if not d.get('host') or not d.get('port'):
                out[i] = {'status': 'none', 'detail': d.get('note') or '没配'}
                continue
            jobs[pool.submit(check_target, d['host'], d['port'], timeout)] = i
        for fut, i in jobs.items():
            status, detail = fut.result()
            out[i] = {'status': status, 'detail': detail}
    return out

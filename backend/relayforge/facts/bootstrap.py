"""导入已有项目：读一遍代码，写出第一版模块、功能点和产品介绍（都是待确认）。"""

import fnmatch
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from ..core.common import DIR, JOURNEY_HEADS, log, slug, today
from ..core.documents import add_line, all_docs, find_doc, item_line, next_id, read_doc, write_doc
from .mapfacts import LAYER_HEAD, RELATION_HEAD, RELATION_KINDS, layer_line, module_sections, relation_line, step_line
from .page import render
from .facts import apply_item, write_summaries
from ..core.model import run_model


BOOTSTRAP_CHARS = 60000      # the whole inventory; half of CHUNK_CHARS
BOOTSTRAP_SOURCE = '代码导入#0'  # source of every line bootstrap writes: "read from the code at import time"
MAX_MODULES, MAX_FEATURES, MAX_DOES, MAX_INTRO = 12, 8, 3, 5
MAX_RELATIONS, MAX_LAYERS, MAX_JOURNEYS, MAX_STEPS = 30, 6, 2, 8
SKIP_DIRS = {'node_modules', '__pycache__', 'dist', 'build', 'target', 'out', 'venv', 'vendor', 'coverage', 'site-packages'}
SOURCE_EXT = {'.py', '.js', '.jsx', '.ts', '.tsx', '.mjs', '.go', '.rs', '.java', '.kt', '.swift', '.rb', '.php', '.c', '.cc',
              '.cpp', '.h', '.hpp', '.cs', '.scala', '.lua', '.sh', '.vue', '.svelte', '.dart', '.ex', '.exs', '.sql'}
MANIFESTS = {'package.json', 'pyproject.toml', 'setup.py', 'setup.cfg', 'requirements.txt', 'cargo.toml', 'go.mod', 'pom.xml',
             'build.gradle', 'build.gradle.kts', 'gemfile', 'composer.json', 'makefile', 'dockerfile', 'docker-compose.yml',
             'package.swift', 'cmakelists.txt'}
ENTRY_NAMES = {'main.py', '__main__.py', 'app.py', 'manage.py', 'cli.py', 'server.py', 'index.js', 'index.ts', 'index.tsx',
               'main.js', 'main.ts', 'server.js', 'server.ts', 'app.js', 'app.ts', 'app.tsx', 'main.go', 'main.rs', 'lib.rs',
               'program.cs', 'main.swift', 'main.c', 'main.cpp', 'main.java', 'application.java'}
SECRET_NAMES = ('id_rsa', 'id_dsa', 'id_ecdsa', 'id_ed25519', 'credentials', 'secrets', '.netrc', '.npmrc', '.pypirc', 'htpasswd')
SECRET_EXT = ('.pem', '.key', '.p12', '.pfx', '.jks', '.keystore', '.crt', '.cer', '.asc', '.gpg', '.secret', '.secrets')
BOOTSTRAP_INSTRUCTION = '''标准输入是一个已有代码仓库的「清单」：目录概况、README、依赖/构建文件、可能的入口和几个大文件的开头。
请只根据这份清单，为这个项目写出最初的产品事实，供用户之后确认。要求：
1. 用用户的话来命名：写产品能做什么、给谁用，模块和功能点都按用户看得懂的说法起名，不要照文件名、目录名或类名原样搬。
2. 模块 3 到 8 个为宜，一个模块是一块相对独立的职责；功能点是模块里用户能感知到的一件事。
3. 每个模块的 paths 只写清单里出现过的仓库内路径（目录或文件，相对仓库根目录），一个模块可以对应多个路径。
4. 清单里看不出来、没有依据的内容一律不要写，宁缺毋滥；不要编造用户、规模、计划。
5. 每条陈述一句话，不超过 60 字。
6. layers 按这个项目的实际情况把模块分成 2 到 5 层，从上到下排：用户直接看到、直接操作的放最上面，背后的能力、数据、接进来的外部工具往下。
   每层 name 是层名（几个字），about 用一句话说这一层是什么；每个模块的 layer 写它所属那一层的 name。不要硬套固定的层，按项目来分。
7. relations 写模块之间的关系，一条一个方向：kind 只能是「调用」（一个模块让另一个去做事）、「读写数据」（一个写另一个读，或读写同一份数据）、
   「套用规则」（按另一个模块定的规则做校验）之一；text 用不超过 20 字说清具体做什么；features 写这条关系经过的功能点名称，可省略。
   from 和 to 写上面 modules 里的模块名。清单里看得出依据的才写。
8. 每个模块的 journeys 写这个模块的用户旅程：按用户使用的顺序，把它的功能点串成一条操作路径，1 到 2 条，每条 3 到 6 步。
   每步 what 写做什么（一句话），see 写做完看到什么，who 写谁来做（「用户」或模块名，可以几个），features 写这一步用到的功能点名称（可以是别的模块的）。
   用户碰不到的底层模块可以不写旅程。
只输出一个 JSON 对象，不要输出任何其他文字，格式：
{"overview":{"intro":["这是什么，一两条"],"audience":["给谁用，可省略"],"not":["明确不做什么，可省略"]},
 "layers":[{"name":"用户界面","about":"用户直接看到、直接操作的地方"}],
 "modules":[
  {"title":"模块名","layer":"层名","paths":["src/api","main.py"],
   "does":["这个模块负责什么，一到三条"],
   "features":[{"title":"功能点名称","statement":"一句话说明这个功能点是什么"}],
   "journeys":[{"title":"旅程名","steps":[{"what":"做什么","see":"看到什么","who":["用户"],"features":["功能点名称"]}]}]}
 ],
 "relations":[{"from":"模块名","to":"模块名","kind":"调用","text":"提交后启动执行","features":["功能点名称"]}]}'''


def looks_secret(rel):
    name = Path(rel).name.lower()
    return (name.startswith('.env') or name.endswith(SECRET_EXT) or name.startswith(SECRET_NAMES)
            or any(k in name for k in ('secret', 'private_key', 'private-key')))


def repo_files(root):
    """Relative paths of the project's files: git's view (ignored files left out), else a walk of the tree."""
    names = None
    try:
        done = subprocess.run(['git', '-C', str(root), 'ls-files', '-z', '--cached', '--others', '--exclude-standard'],
                              capture_output=True, text=True, timeout=60)
        if done.returncode == 0:
            names = [n for n in done.stdout.split('\0') if n]
    except (OSError, subprocess.SubprocessError):
        pass
    if names is None:
        ignored = []
        gi = root / '.gitignore'
        if gi.exists():
            ignored = [x.strip().strip('/') for x in gi.read_text(encoding='utf-8', errors='replace').splitlines()
                       if x.strip() and not x.startswith(('#', '!'))]
        names = []
        for here, dirs, files in os.walk(root):
            dirs[:] = sorted(d for d in dirs if not d.startswith('.') and d not in SKIP_DIRS and not any(fnmatch.fnmatch(d, g) for g in ignored))
            rel = Path(here).relative_to(root)
            names += [str(rel / f) if str(rel) != '.' else f for f in sorted(files) if not any(fnmatch.fnmatch(f, g) for g in ignored)]
    keep = []
    for n in names:
        parts = Path(n).parts
        if any(p.startswith('.') or p in SKIP_DIRS for p in parts[:-1]) or looks_secret(n) or n in ('AGENTS.md', 'CLAUDE.md'):
            continue
        if (root / n).is_file() and not (root / n).is_symlink():
            keep.append(n)
    return sorted(keep)


def read_head(root, rel, limit):
    """The first `limit` characters of a text file; None for binary or unreadable files."""
    try:
        with open(root / rel, 'rb') as f:
            raw = f.read(limit * 4 + 1)
    except OSError:
        return None
    if b'\0' in raw[:2048]:
        return None
    text = raw.decode('utf-8', errors='replace')
    return text[:limit] + ('\n…（后面省略）' if len(text) > limit else '')


def bootstrap_inventory(root, limit=BOOTSTRAP_CHARS):
    """A compact picture of the repository, built by the program alone, at most `limit` characters."""
    files = repo_files(root)
    sizes = {}
    for n in files:
        try:
            sizes[n] = (root / n).stat().st_size
        except OSError:
            sizes[n] = 0
    parts = [f'# 仓库清单\n共 {len(files)} 个文件，合计 {sum(sizes.values()) // 1024} KB。']

    dirs = {}
    for n in files:
        p = Path(n).parts
        for depth in (1, 2):
            if len(p) > depth:
                key = '/'.join(p[:depth])
                count, size = dirs.get(key, (0, 0))
                dirs[key] = (count + 1, size + sizes[n])
    top = sorted((k for k in dirs if '/' not in k), key=lambda k: -dirs[k][0])[:40]
    deep = [k for k in dirs if '/' in k and k.split('/')[0] in top and dirs[k.split('/')[0]][0] > 12]
    deep = sorted(deep, key=lambda k: -dirs[k][0])[:40]
    rootfiles = sum(1 for n in files if '/' not in n)
    lines = [f'- ./（根目录下的文件）：{rootfiles} 个'] if rootfiles else []
    lines += [f'- {k}/：{dirs[k][0]} 个文件，{dirs[k][1] // 1024} KB' for k in sorted(top + deep)]
    parts.append('## 目录概况\n' + '\n'.join(lines))

    shallow = sorted(files, key=lambda n: (len(Path(n).parts), n))[:300]
    parts.append('## 文件（浅层优先' + (f'，另有 {len(files) - 300} 个未列出' if len(files) > 300 else '') + '）\n' + '\n'.join(sorted(shallow)))

    for n in files:
        if Path(n).name.lower().startswith('readme') and len(Path(n).parts) == 1:
            head = read_head(root, n, 6000)
            if head:
                parts.append(f'## README（{n}）\n{head}')
            break
    manifests = [n for n in files if Path(n).name.lower() in MANIFESTS or n.lower().endswith('.csproj')]
    manifests = sorted(manifests, key=lambda n: (len(Path(n).parts), n))[:8]
    texts = [(n, read_head(root, n, 2500)) for n in manifests]
    if any(t for _, t in texts):
        parts.append('## 依赖和构建文件\n' + '\n'.join(f'### {n}\n{t}' for n, t in texts if t))
    entries = [n for n in files if Path(n).name.lower() in ENTRY_NAMES][:20]
    if entries:
        parts.append('## 可能的入口\n' + '\n'.join(f'- {n}' for n in entries))

    text = '\n\n'.join(parts)
    if len(text) > limit - 2000:
        return text[:limit - 20] + '\n…（清单已截断）'
    sources = sorted((n for n in files if Path(n).suffix.lower() in SOURCE_EXT), key=lambda n: -sizes[n])[:10]
    extra, used = ['## 最大的几个源文件的开头'], len(text)
    for n in sources:
        room = min(1500, limit - used - 200)
        head = read_head(root, n, room) if room > 200 else None
        if head:
            piece = f'### {n}（{sizes[n] // 1024} KB）\n{head}'
            extra.append(piece)
            used += len(piece) + 2
    return text + ('\n\n' + '\n\n'.join(extra) if len(extra) > 1 else '')


def clean_text(value, limit):
    if not isinstance(value, str):
        return ''
    return re.sub(r'\s+', ' ', value).strip().lstrip('-').strip()[:limit]


def clean_lines(value, cap, limit=120):
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    out = [clean_text(x, limit) for x in value]
    return list(dict.fromkeys(x for x in out if x))[:cap]


def repo_path(root, raw):
    """A repository-relative path that exists inside the project and is not hidden or secret; None otherwise."""
    if not isinstance(raw, str):
        return None
    rel = raw.strip().replace('\\', '/')
    while rel.startswith('./'):
        rel = rel[2:]
    rel = rel.rstrip('/')
    parts = Path(rel).parts
    if not rel or rel.startswith('/') or '..' in parts or any(p.startswith('.') or p in SKIP_DIRS for p in parts) or looks_secret(rel):
        return None
    target = root / rel
    try:
        if not target.exists() or root.resolve() not in [target.resolve(), *target.resolve().parents]:
            return None
    except OSError:
        return None
    return rel


def check_bootstrap_answer(root, answer, taken):
    """Validate the model's answer. Returns (plan, dropped); raises ValueError when nothing usable is left."""
    if not isinstance(answer, dict) or not isinstance(answer.get('modules'), list):
        raise ValueError('模型的回答里没有 modules 列表，没有写入任何内容。')
    dropped = {'路径不存在': 0, '模块没有可用路径': 0, '名称为空或重复': 0, '功能点被丢弃': 0}
    seen, plan = set(taken), []
    for raw in answer['modules']:
        title = clean_text(raw.get('title'), 40) if isinstance(raw, dict) else ''
        if not title or title.lower() in seen:
            dropped['名称为空或重复'] += 1
            continue
        paths = []
        for p in raw.get('paths') if isinstance(raw.get('paths'), list) else []:
            good = repo_path(root, p)
            if good is None:
                dropped['路径不存在'] += 1
            elif good not in paths:
                paths.append(good)
        if not paths:
            dropped['模块没有可用路径'] += 1
            continue
        seen.add(title.lower())
        features = []
        for f in raw.get('features') if isinstance(raw.get('features'), list) else []:
            ft = clean_text(f.get('title'), 40) if isinstance(f, dict) else ''
            if not ft or ft.lower() in seen or len(features) >= MAX_FEATURES:
                dropped['功能点被丢弃'] += 1
                continue
            seen.add(ft.lower())
            features.append({'title': ft, 'statement': clean_text(f.get('statement'), 120)})
        plan.append({'title': title, 'layer': clean_text(raw.get('layer') or raw.get('group'), 20), 'paths': paths,
                     'does': clean_lines(raw.get('does'), MAX_DOES), 'features': features, 'raw_journeys': raw.get('journeys')})
        if len(plan) >= MAX_MODULES:
            break
    if not plan:
        raise ValueError('模型的回答里没有一个模块能对上仓库里的路径，没有写入任何内容。')
    over = answer.get('overview') if isinstance(answer.get('overview'), dict) else {}
    intro = {head: clean_lines(over.get(key), MAX_INTRO) for head, key in (('产品介绍', 'intro'), ('面向谁', 'audience'), ('不做什么', 'not'))}
    layers, relations = check_map_answer(answer, plan, dropped)
    return {'modules': plan, 'overview': intro, 'layers': layers, 'relations': relations}, {k: v for k, v in dropped.items() if v}


def check_map_answer(answer, plan, dropped):
    """Layers, relations and each module's journeys, keeping only what names layers, modules and features from the plan."""
    drop = lambda what: dropped.__setitem__(what, dropped.get(what, 0) + 1)
    layers, names = [], set()
    for raw in answer.get('layers') if isinstance(answer.get('layers'), list) else []:
        raw = raw if isinstance(raw, dict) else {}
        name = clean_text(raw.get('name'), 20).replace('：', '').replace(':', '')
        if not name or name in names or len(layers) >= MAX_LAYERS:
            drop('层被丢弃')
            continue
        names.add(name)
        layers.append({'name': name, 'about': clean_text(raw.get('about'), 60)})
    for m in plan:
        if m['layer'] and m['layer'] not in names:
            if len(layers) < MAX_LAYERS:  # a module may name a layer the list forgot
                names.add(m['layer'])
                layers.append({'name': m['layer'], 'about': ''})
            else:
                m['layer'] = ''
    modules = {m['title'].lower(): m['title'] for m in plan}
    features = {f['title'].lower(): f['title'] for m in plan for f in m['features']}
    named = lambda pool, values: [pool[v.lower()] for v in (clean_text(x, 40) for x in (values if isinstance(values, list) else [])) if v and v.lower() in pool]
    relations, seen = [], set()
    for raw in answer.get('relations') if isinstance(answer.get('relations'), list) else []:
        raw = raw if isinstance(raw, dict) else {}
        a, b = modules.get(clean_text(raw.get('from'), 40).lower()), modules.get(clean_text(raw.get('to'), 40).lower())
        kind, text = clean_text(raw.get('kind'), 10), clean_text(raw.get('text'), 40).replace('｜', '，')
        if not a or not b or a == b or kind not in RELATION_KINDS or not text or (a, b, kind) in seen or len(relations) >= MAX_RELATIONS:
            drop('关系被丢弃')
            continue
        seen.add((a, b, kind))
        relations.append({'from': a, 'to': b, 'kind': kind, 'text': text, 'features': named(features, raw.get('features'))})
    for m in plan:
        m['journeys'] = []
        raws = m.pop('raw_journeys', None)
        for raw in raws if isinstance(raws, list) else []:
            raw = raw if isinstance(raw, dict) else {}
            title, steps = clean_text(raw.get('title'), 30), []
            for step in raw.get('steps') if isinstance(raw.get('steps'), list) else []:
                step = step if isinstance(step, dict) else {}
                what, see = clean_text(step.get('what'), 80).replace('｜', '，'), clean_text(step.get('see'), 80).replace('｜', '，')
                who = clean_lines(step.get('who'), 4, 40)
                actors = (['用户'] if any(w in ('用户', '你') for w in who) else []) + named(modules, who)
                if what and len(steps) < MAX_STEPS:
                    steps.append({'what': what, 'see': see, 'who': actors or ['用户'], 'features': named(features, step.get('features'))})
            if not title or len(steps) < 2 or len(m['journeys']) >= MAX_JOURNEYS:
                drop('旅程被丢弃')
                continue
            m['journeys'].append({'title': title, 'steps': steps})
    return layers, relations


def write_bootstrap(root, plan):
    made = []
    for m in plan['modules']:
        ident = apply_item(root, {'kind': 'structure', 'op': 'add-module', 'title': m['title'], 'stated_by': 'inferred'}, BOOTSTRAP_SOURCE)
        path, meta, sections = find_doc(root, ident)
        meta['paths'] = m['paths']
        if m['layer']:
            meta['layer'] = m['layer']
        for statement in m['does']:
            add_line(sections, '负责什么', item_line(statement, BOOTSTRAP_SOURCE, False))
        write_doc(path, meta, sections)
        ids = [apply_item(root, {'kind': 'structure', 'op': 'add-feature', 'title': f['title'], 'module': ident,
                                 'one_liner': f['statement'], 'stated_by': 'inferred'}, BOOTSTRAP_SOURCE) for f in m['features']]
        made.append({'id': ident, 'title': m['title'], 'paths': m['paths'], 'features': [i for i in ids if i != 'skip'],
                     'feature_titles': {f['title']: i for f, i in zip(m['features'], ids) if i != 'skip'}})
    relations, journeys = write_map(root, made, plan)
    overview = root / DIR / 'product' / 'overview.md'
    meta, sections = read_doc(overview) if overview.exists() else (
        {'id': 'OVERVIEW', 'type': 'doc', 'title': '产品介绍', 'status': 'draft'}, {'产品介绍': [], '面向谁': [], '不做什么': [], '变更记录': []})
    count = 0
    for head, statements in plan['overview'].items():
        for s in statements:
            add_line(sections, head, item_line(s, BOOTSTRAP_SOURCE, False))
            count += 1
    if count:
        add_line(sections, '变更记录', f'- {today()} 导入已有代码时，从代码里读出 {count} 条，等待确认。〔{BOOTSTRAP_SOURCE}〕')
        meta['updated'] = today()
        write_doc(overview, meta, sections)
    for m in made:
        m.pop('feature_titles', None)
    return made, count, relations, journeys


def write_map(root, made, plan):
    """The layers, the relations (into the modules they start from) and each module's journeys; all of it unconfirmed."""
    module_id = {m['title']: m['id'] for m in made}
    feature_id = {t: i for m in made for t, i in m['feature_titles'].items()}
    ids = lambda names, pool: [pool[n] for n in names if n in pool]
    if plan.get('layers'):
        path = root / DIR / 'product' / 'layers.md'
        meta, sections = read_doc(path) if path.exists() else (
            {'id': 'LAYERS', 'type': 'doc', 'title': '大图的层', 'status': 'draft', 'updated': today()}, {LAYER_HEAD: [], '变更记录': []})
        for layer in plan['layers']:
            add_line(sections, LAYER_HEAD, item_line(layer_line(layer['name'], layer['about']), BOOTSTRAP_SOURCE, False))
        add_line(sections, '变更记录', f'- {today()} 导入已有代码时，从代码里分出 {len(plan["layers"])} 层，等待确认。〔{BOOTSTRAP_SOURCE}〕')
        meta['updated'] = today()
        write_doc(path, meta, sections)
    written = 0
    for m in made:
        mine = [r for r in plan.get('relations') or [] if module_id.get(r['from']) == m['id'] and r['to'] in module_id]
        if not mine:
            continue
        path, meta, sections = find_doc(root, m['id'])
        sections = module_sections(sections)
        for r in mine:
            add_line(sections, RELATION_HEAD, item_line(relation_line(r['kind'], module_id[r['to']], r['text'], ids(r['features'], feature_id)), BOOTSTRAP_SOURCE, False))
            written += 1
        write_doc(path, meta, sections)
    journeys = []
    for m in plan['modules']:
        for order, j in enumerate(m.get('journeys') or [], 1):
            ident = next_id(root, 'journeys', 'J', 2, 1)
            meta = {'id': ident, 'type': 'journey', 'title': j['title'], 'status': 'draft', 'module': module_id[m['title']], 'order': order, 'updated': today()}
            sections = {h: [] for h in JOURNEY_HEADS}
            for step in j['steps']:
                who = [w if w == '用户' else module_id[w] for w in step['who'] if w == '用户' or w in module_id]
                add_line(sections, '步骤', item_line(step_line(step['what'], step['see'], who, ids(step['features'], feature_id)), BOOTSTRAP_SOURCE, False))
            add_line(sections, '变更记录', f'- {today()} 导入已有代码时，从代码里读出这条用户旅程，等待确认。〔{BOOTSTRAP_SOURCE}〕')
            write_doc(root / DIR / 'journeys' / f'{ident}-{slug(j["title"])}.md', meta, sections)
            journeys.append({'id': ident, 'title': j['title'], 'module': meta['module'], 'steps': len(j['steps'])})
    return written, journeys


def bootstrap(root, force=False):
    """Read the repository, ask the model for a first picture of the product, and write it as drafts."""
    existing = [m for _, m, _ in all_docs(root, ('modules', 'features'))]
    if existing and not force:
        raise ValueError(f'这个项目已经有 {len(existing)} 个模块和功能点，不再导入，以免和现有的大图重复。确实要追加时加 --force。')
    inventory = bootstrap_inventory(root)
    answer = run_model(root, BOOTSTRAP_INSTRUCTION, inventory)
    plan, dropped = check_bootstrap_answer(root, answer, {str(m.get('title')).lower() for m in existing})
    made, lines, relations, journeys = write_bootstrap(root, plan)
    write_summaries(root)
    render(root)
    return {'modules': made, 'features': sum(len(m['features']) for m in made), 'overview_lines': lines,
            'relations': relations, 'journeys': journeys, 'layers': [l['name'] for l in plan.get('layers') or []],
            'dropped': dropped, 'inventory_chars': len(inventory)}


def bootstrap_command(root, force, as_json):
    try:
        result = bootstrap(root, force)
    except Exception as error:
        log(root, f'bootstrap failed: {error!r}')
        print(f'导入失败：{error}', file=sys.stderr)
        return 1
    if as_json:
        print(json.dumps(result, ensure_ascii=False))
        return 0
    print(f'已从代码里读出 {len(result["layers"])} 层、{len(result["modules"])} 个模块、{result["features"]} 个功能点、{result["relations"]} 条模块关系、'
          f'{len(result["journeys"])} 条用户旅程、{result["overview_lines"]} 条产品介绍，都是草稿，等你确认。')
    for m in result['modules']:
        print(f'- {m["id"]} {m["title"]}（代码：{"、".join(m["paths"])}；{len(m["features"])} 个功能点）')
    if result['dropped']:
        print('已丢弃：' + '，'.join(f'{k} {v}' for k, v in result['dropped'].items()))
    print('下一步：用 rf.py pending 查看，或在对话里运行 /rf-confirm 按模块确认。')
    return 0

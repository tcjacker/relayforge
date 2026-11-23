"""初始化一个项目：建 .relayforge/、复制后端、写约定（旧项目写进 AGENTS.md，数据外置的项目放在本机记录的 CONVENTIONS.md）、FORMAT.md 和技能文件。"""

import json
import os
import shutil
from pathlib import Path

from ..core.common import (
    DIR, ID_FILE, PACKAGE, PORT, SCRIPT, TEMPLATES, TREES, is_external, link_facts, new_project_id, project_id, today,
)
from ..issues.record import SHOTS
from ..testsets.store import RESULTS
from ..core.documents import write_doc
from .registry import register
from ..facts.page import render
from ..facts.facts import write_summaries


BEGIN, END = '<!-- relayforge:begin -->', '<!-- relayforge:end -->'
CONVENTIONS = f'''{BEGIN}
## 项目事实的写法

项目的现行事实保存在 .relayforge/（指向本机 ~/.relayforge/projects/<项目 ID>/ 的链接，不进 Git），由程序解析并渲染成页面。写入或修改时遵守：

1. 一个对象一个文件：模块在 .relayforge/modules/，功能点在 .relayforge/features/，用户旅程在 .relayforge/journeys/，
   裁定在 .relayforge/decisions/，文档登记在 .relayforge/documents/，需求在 .relayforge/requirements/，
   设计稿在 .relayforge/designs/（一份设计稿是一个说明文件加一个同编号的 HTML 页面）。
2. 文件名是「编号-名称.md」。编号一旦创建不得修改；改名只改名称部分和文件头的 title。
3. 每个文件以 YAML 文件头开始，字段和取值见 .relayforge/FORMAT.md。
4. 正文只用规定的二级标题，名称和顺序不能改；标题下只写列表项，一行一条陈述。
5. 每条陈述行尾标出处〔会话编号#消息序号〕。不是用户明确说过的，行首加〔待确认〕。
6. 用户明确作出的决定可以直接写入；自己的推断必须带〔待确认〕，不得自行去掉这个标记。
7. 不要删除内容。被替代的文件移到 .relayforge/history/，把 status 改为 superseded
   并填写 superseded_by。
8. 依赖关系只写在功能点文件头的 depends_on 里，不要在正文里另写一份。
9. 「变更记录」只追加，不改旧行。
10. 仓库里 .relayforge/ 之外的已有文档，不要为了治理而移动或改写；
    要改变它的状态，修改 .relayforge/documents/ 下对应的登记文件。
11. .relayforge/inbox/、runs/、sessions/、context.md、pending.md、cursor.json 由程序生成，不要手工编辑。
12. 开工时只以 status 为 current 的文件为依据；.relayforge/history/ 里的内容，
    以及登记为 reference 或 superseded 的文档，不作为依据。
13. 干活的过程中边做边记：进展、计划、用户的决定、没定的问题，用
    python3 .relayforge/bin/rf.py note 记下来（写法见 .relayforge/FORMAT.md 的「边做边记」）。
    编号、出处和格式由这个命令生成，不要手工去写 sessions/ 里的文件。
14. 需求先立项再开发，开发完先测试再验收（见 .relayforge/FORMAT.md 的「需求」）：立项中 → 进行中 → 测试中 → 待验收 → 已完成。立项中的需求只聊、只记，不改代码；
    rf.py req start（确认立项）、rf.py req done（确认完成）、rf.py req drop（放弃）、rf.py req set（手动改状态）、rf.py req choose（改开发或测试会话的 CLI 和模型）、rf.py req to-test（交给测试）、rf.py req skip-test（跳过测试）、rf.py req accept（人工验收时逐条点通过或这里不对）
    和 rf.py req case-ok、case-no、case-save（同意、不同意用例的改动，用户自己改用例）只由用户触发，AI 不得自己执行；
    这些「只由用户触发」的命令程序自己也校验（见 .relayforge/FORMAT.md 的「只由用户触发的命令」），在 AI 会话里运行会被拒绝，不要想办法绕过。
    会话是哪一种（开发、测试…）只认应用签名的会话令牌（见 FORMAT.md 的「会话的身份」）；不要改写会话相关的环境变量，也不要去读 $RELAYFORGE_HOME/apps/。
    三个会话的分工：立项会话用业务的话写测试用例（rf.py req case-add，前提、操作、预期，挂在验收条件下面），并用 rf.py req classify 给验收条件归类（数据可验 / 只能人工看）；
    开发会话写代码，目标是让这些用例通过，不能改用例；测试中由这个需求的测试会话把用例对接成测试集里的数据（每条数据挂上用例编号）、执行、逐条用例报告结果，
    要看界面的用 rf.py req steps 写检查步骤，可以补充用例（测试补充的），不改产品代码，最后用 rf.py req verdict 交回「可以验收了」或「有问题」；
    开发会话修好测试交回的问题后用 rf.py req retest 再交给测试。立项以后任何会话认为用例写错了，用 req case-edit / case-rm / classify 加 --why 提给用户，不能自己改。
15. 设计稿用 python3 .relayforge/bin/rf.py design save 保存和修改（写法见 .relayforge/FORMAT.md 的「设计稿」），
    不要手工去写 designs/ 下的文件。改一个已经有设计稿的功能点时，从它现在的设计稿改起。
16. 大图（层、模块、功能点的归属、用户旅程、模块之间的关系）只在初始化和大图编辑会话里改（见 .relayforge/FORMAT.md 的「大图」）。
    日常的开发会话和聊天里即使聊到「加一个模块」，也不要去改 modules/、journeys/、product/layers.md 和模块之间的关系，请用户到大图里点「编辑」。
17. 使用中发现的毛病记成「问题」（见 .relayforge/FORMAT.md 的「问题」）。任何会话发现了和眼下的事无关的毛病，都可以用
    python3 .relayforge/bin/rf.py issue 登记，不用顺手去修。先用 issue list --json 查所有来源的旧单，用 issue show 核对候选的复现证据。
    确认重复时用 issue new "一句话现象" --source Agent --duplicate-of 原单编号 --steps "复现步骤" --observed "实际结果" 补原单。
    有候选但证据不足时用 issue new "一句话现象" --desc "怎么操作、看到了什么" --source Agent --possible-duplicate 候选编号 --duplicate-reason "相似之处和缺少的证据"。
    没有候选时用 issue new "一句话现象" --desc "怎么操作、看到了什么" --source Agent 正常登记。
    问题文件在项目主目录的 .relayforge/issues/ 下，只用 rf.py issue 读写，不要手工编辑。
    修复会话修好后用 rf.py issue handback 交回修复报告；rf.py issue confirm（确认修好了）、rf.py issue unconfirm（撤回确认）和 rf.py issue reject（还有问题）只由用户触发，AI 不得自己执行。
    用户把一条问题「交给 AI 排查」时开的是只读的排查会话（不改任何文件），查完用 rf.py issue finding 记结论，然后停下；
    rf.py issue probe、fix、set、reply 这些由用户决定的命令，排查会话不得自己执行。
18. 事实只有一份（见 .relayforge/FORMAT.md 的「数据放在哪」）：项目的记录放在本机 ~/.relayforge/projects/<项目 ID>/，不进 Git；
    仓库里只提交 .relayforge.json（项目 ID），项目主目录和需求、修复的工作目录（.worktrees/ 下）里的 .relayforge 都是指向它的链接，切换分支不影响记录。
    工作目录只改代码；rf.py 的命令在哪里运行都读写同一份记录。不要删除、替换 .relayforge 链接和 .relayforge.json。
    还没确认完成的需求带来的事实是「进行中」的（文件头 in_progress，或行首〔进行中 R-…〕），不作为依据，除非你就在做那个需求；
    需求里新加或修改功能点用 rf.py note new-feature / note fact，不要手工写。记录不进 Git，不要 git add .relayforge，也不要把记录复制进仓库。
19. 推送到远端（发布）只由用户在应用里点「发布」触发（见 .relayforge/FORMAT.md 的「发布」），推送可能触发部署。AI 不得推送：不运行 git push，
    也不运行 rf.py publish run 和 rf.py publish pull；可以用 rf.py publish status、publish list 看有什么还没发布。发布记录在 .relayforge/publishes/，由程序写，不要手工编辑。
20. 要改一段你不知道为什么这样写的代码之前，先运行 python3 .relayforge/bin/rf.py why 文件[:行|:起-止]：程序从 git 算出它最近是哪些提交改的、
    属于哪个需求或修复，给出当时的需求说明、验收条件、裁定和会话进展（见 .relayforge/FORMAT.md 的「追溯」）。需求和修复的工作目录里的提交，
    git 钩子会自动带上 RelayForge-Requirement / RelayForge-Fix / RelayForge-Issue / RelayForge-Session 几行，不要手工写、也不要删。
21. 想知道以前怎么做过、为什么这么定，用 python3 .relayforge/bin/rf.py history（只读）：history search 词… [--tag/--feature/--file/--kind] 查需求、问题、裁定、
    会话进展、总结和标签；history show 编号 看一条的完整摘要；history session 会话编号 看当时的聊天（存在这台电脑上，不进仓库）。
    标着进行中、待确认的不作为依据。rf.py summarize 和 rf.py archive 由程序在确认完成以后运行，不要自己运行。
22. 新测试一律写成测试集（见 .relayforge/FORMAT.md 的「测试集」）：测试数据放进 .relayforge/tests/ 下的 JSON，只用 python3 .relayforge/bin/rf.py test
    读写（new 新建、add 加数据、edit / rm 改删、run 运行、analyze 写分析），不要手工编辑；接入代码只读数据、调用被测的代码，不含任何数据，比对由程序做。
    每条数据写明覆盖哪种情况。改动或删除已经落定的数据（别的需求留下的）要写理由，等用户同意；进入测试以后，开发会话改测试会话写的数据也一样；rf.py test approve、reject、stale、revive、move、save、seen、approve-build 只由用户触发，AI 不得自己执行。
    测试集的构建命令（--build）第一次执行前要用户在应用里同意；没同意时测试集跑不起来，告诉用户在应用里运行一次、看过命令后点同意。
    实在写不成测试集的才写普通的代码测试，并用 rf.py note 说明原因。
23. 测试环境（见 .relayforge/FORMAT.md 的「测试环境」）只由用户在应用里设置：.relayforge/envs/ 下的文件只用 rf.py env 读写，不要手工编辑；
    标了保密的值只存在这台电脑上，不要去找、不要写进任何文件；会话里运行测试集和准备步骤时默认不带保密值。会话可以用 rf.py env list、show、scan、check、prep、plan、handoff 看和检查，
    用 rf.py env prep-run 执行用户同意过的准备步骤，用 rf.py env propose 交回建议（准备步骤、启动命令、配置文件、环境变量名、测试集的 classpath / build）；
    rf.py env new、copy、rename、rm、save、use、user、reveal、adopt、ignore、approve、ai-secrets、launch、accept-clean 和 rf.py test config 只由用户触发，AI 不得自己执行。
    人工验收的服务由应用启动和停止（不经过模型）；服务的「验收时额外带的环境变量」（accept）也用 env propose 交回建议。
    装依赖、编译这类项目目录里的事可以直接做；系统级的安装（brew、apt、sudo、npm -g、docker run 之类）每次都要用户同意，先说清装什么、为什么、装到哪。
{END}
'''

FORMAT = (TEMPLATES / 'FORMAT.md').read_text(encoding='utf-8')

CONFIRM_SKILL = f'''---
name: rf-confirm
description: 按模块批量处理 RelayForge 里等待用户确认的条目。用户说「确认一下待确认的」「把某某模块的都确认了」「这条不对」时使用。
---

# 按模块确认

待确认的条目是 AI 的推断，只有用户明确表态之后才能确认或否决。不要替用户做决定。

1. 运行 `python3 {DIR}/bin/rf.py pending`，它按模块列出所有等待确认的对象。
2. 一个模块一个模块地给用户看：模块名、有几个对象、每个对象一句话。不要一次把全部明细贴出来，先给各模块的数量，让用户挑从哪个开始。
3. 用户表态之后再执行：
   - 整个模块都认可：`python3 {DIR}/bin/rf.py confirm --module M-10`
   - 整个模块认可，个别除外：`python3 {DIR}/bin/rf.py confirm --module M-10 --except RF-113 RF-114`
   - 只认可几个：`python3 {DIR}/bin/rf.py confirm RF-108 D-0066`
   - 不认可：`python3 {DIR}/bin/rf.py reject RF-113`（不会删除，移到历史里备查）
4. 用户说要改而不是否决时，先否决原来的，再按用户的说法写入新的内容。
5. 做完告诉用户：确认了几个、否决了几个、还剩几个。
'''

SKILL = f'''---
name: rf-sync
description: 把当前会话同步到 RelayForge，整理出任务目标、正在处理的事、计划和进度，并刷新本机页面。用户说「同步一下」「更新进展」，或者问「现在的计划是什么」「做到哪一步了」时使用。
---

# 同步当前会话

1. 运行 `python3 {DIR}/bin/rf.py sync`。它会读这次会话里还没读过的消息，更新任务目标、正在处理的事和计划，一般需要一两分钟。
   本机服务在运行时由服务处理并刷新页面；没有运行时直接在本地处理，结果一样。
2. 命令会打印更新后的会话文件。向用户简要报告：任务目标、正在处理的事、还没做完的步骤。
3. 用户想给这个会话起名字时加 `--name "名字"`；用户说这件事做完了、要结束这个会话时加 `--end`。
4. 之后需要引用计划或进度时，以这个文件为准，不要凭记忆回答。只想读、不想重新同步时运行 `python3 {DIR}/bin/rf.py state`。
5. 发现文件里的内容和实际情况不符，告诉用户哪里不符；不要手工修改 {DIR}/sessions/ 下的文件。

想在浏览器里看：`python3 {DIR}/bin/rf.py serve`，然后打开 http://127.0.0.1:{PORT} 。一个服务显示本机所有登记过的项目，从哪个目录启动都一样。
'''

RULES = 'CONVENTIONS.md'  # 数据外置的项目：约定放在本机记录里，不写进仓库的 AGENTS.md


SKILL_NAMES = ('rf-sync', 'rf-confirm')


def _skill_body(text):
    """A skill without its front matter, the trigger line kept as the first line."""
    head, body = text.split('\n---\n', 1)
    when = next(line.split(':', 1)[1].strip() for line in head.splitlines() if line.startswith('description:'))
    return when + '\n' + body.strip('\n')


def conventions_text():
    """The conventions without the AGENTS.md markers, plus the two skills: what the app hands to each session it opens.

    数据外置的项目不往仓库写 .claude/skills/（用户自己用 Claude Code 开发时会被这两个技能打断，仓库外也没有 .relayforge），
    所以「同步一下」「确认待确认的」这两件事的做法也放进约定，由应用交给它开的会话。"""
    rules = CONVENTIONS.replace(BEGIN + '\n', '').replace(END, '').rstrip('\n')
    skills = '\n\n'.join(_skill_body(text) for text in (SKILL, CONFIRM_SKILL))
    return rules + '\n\n## 常用操作\n\n' + skills + '\n'


def _drop_old_skills(root, names=SKILL_NAMES):
    """Remove the skill files an earlier init wrote into the repo (only ours, recognised by name)."""
    gone = []
    for name in names:
        path = root / '.claude' / 'skills' / name / 'SKILL.md'
        try:
            text = path.read_text(encoding='utf-8')
        except OSError:
            continue
        if f'\nname: {name}\n' not in '\n' + text.split('\n---\n', 1)[0] + '\n':
            continue
        path.unlink()
        for folder in (path.parent, path.parent.parent, path.parent.parent.parent):
            try:
                folder.rmdir()  # 只删空的目录
            except OSError:
                break
        gone.append(name)
    return gone


def install_program(base, rules=False):
    """The program copy and the format in a project's records: rf.py with its package next to it (the project's hooks and conventions
    only know the entry .relayforge/bin/rf.py), FORMAT.md, and with `rules` the conventions the app hands to sessions."""
    (base / 'bin').mkdir(parents=True, exist_ok=True)
    script = base / 'bin' / 'rf.py'
    if SCRIPT != script.resolve():
        shutil.copyfile(SCRIPT, script)
        package = base / 'bin' / PACKAGE.name
        shutil.rmtree(package, ignore_errors=True)
        shutil.copytree(PACKAGE, package, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        # 许可证（FSL）要求再分发时附带条款；用户常把 .relayforge/ 提交进仓库，所以放一份在 rf.py 旁边。
        # 来源取包里的那份：打包后的应用只带 backend/，拿不到仓库根目录的 LICENSE。
        shutil.copyfile(PACKAGE / 'LICENSE', base / 'bin' / 'LICENSE')
    (base / 'FORMAT.md').write_text(FORMAT, encoding='utf-8')
    if rules:
        (base / RULES).write_text(conventions_text(), encoding='utf-8')


def drop_repo_rules(root, keep=()):
    """Take out what an earlier version wrote into the repository for AI sessions: the conventions between our markers in
    AGENTS.md (the file goes when nothing else is left), a CLAUDE.md that only says @AGENTS.md when AGENTS.md went with it,
    and our two skills under .claude/skills/. Anything the user wrote stays. Paths in `keep` (uncommitted changes) are left alone.
    Returns (changed paths relative to root, notes)."""
    root = Path(root)
    changed, notes = [], []
    agents, claude = root / 'AGENTS.md', root / 'CLAUDE.md'
    if 'AGENTS.md' not in keep and agents.is_file():
        text = agents.read_text(encoding='utf-8')
        if BEGIN in text and END in text and text.index(BEGIN) < text.index(END):
            cut = (text[:text.index(BEGIN)].rstrip('\n') + '\n' + text[text.index(END) + len(END):].lstrip('\n')).strip('\n')
            if cut:
                agents.write_text(cut + '\n', encoding='utf-8')
            else:
                agents.unlink()
            changed.append('AGENTS.md')
            notes.append('AGENTS.md 里以前写入的 RelayForge 约定已经拿掉' + ('' if cut else '（文件里没有别的内容，整个删掉）') + '，约定改放在本机的 .relayforge/CONVENTIONS.md。')
    if 'CLAUDE.md' not in keep and not agents.exists() and claude.is_file() and claude.read_text(encoding='utf-8').strip() == '@AGENTS.md':
        claude.unlink()
        changed.append('CLAUDE.md')
        notes.append('CLAUDE.md 只有一行 @AGENTS.md（以前初始化时加的），随 AGENTS.md 一起删掉。')
    gone = _drop_old_skills(root, [n for n in SKILL_NAMES if f'.claude/skills/{n}/SKILL.md' not in keep])
    if gone:
        changed.extend(f'.claude/skills/{n}/SKILL.md' for n in gone)
        notes.append(f'仓库里以前写入的技能 {"、".join(gone)} 已经拿掉（做法改放在本机的 .relayforge/CONVENTIONS.md，由应用交给会话）。')
    return changed, notes


HOOKS = {
    'SessionStart': {'command': 'hook session-start'},
    'PreCompact': {'command': 'hook capture', 'async': True},
    'SessionEnd': {'command': 'hook capture', 'async': True},
    'SubagentStop': {'command': 'hook subagent-stop', 'async': True},
}


def init(target, hooks):
    root = Path(target).resolve()
    base = root / DIR
    # 新项目的数据放在本机外置目录（见 common.link_facts）；仓库里已经有 .relayforge/ 目录的旧项目照旧，不在这里搬
    # RELAYFORGE_FACTS_IN_REPO=1：按旧布局建（记录是仓库里的 .relayforge/ 目录、跟着 Git 走），只给测试旧项目那条路用
    if os.environ.get('RELAYFORGE_FACTS_IN_REPO') != '1' and not (base.exists() and not base.is_symlink()):
        if not project_id(root):
            (root / ID_FILE).write_text(json.dumps({'id': new_project_id(root)}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        link_facts(root)
    for name in ('product', 'modules', 'features', 'journeys', 'decisions', 'documents', 'history', 'inbox', 'runs', 'sessions', 'bin'):
        (base / name).mkdir(parents=True, exist_ok=True)
        keep = base / name / '.gitkeep'
        if name != 'bin' and not any((base / name).iterdir()):
            keep.write_text('', encoding='utf-8')
    install_program(base)
    overview = base / 'product' / 'overview.md'
    if not overview.exists():
        write_doc(overview, {'id': 'OVERVIEW', 'type': 'doc', 'title': '产品介绍', 'status': 'draft', 'updated': today()}, {'产品介绍': [], '面向谁': [], '不做什么': [], '变更记录': []})

    notes = []
    agents = root / 'AGENTS.md'
    text = agents.read_text(encoding='utf-8') if agents.exists() else ''
    if is_external(root):
        # 数据外置的项目：约定不写进仓库的 AGENTS.md / CLAUDE.md（别处的会话、远程开发看到的仓库里没有 .relayforge，
        # 这些规则只会干扰它们）。约定放在本机的记录里（.relayforge/CONVENTIONS.md），应用开会话时交给会话。
        (base / RULES).write_text(conventions_text(), encoding='utf-8')
    else:
        if BEGIN in text and END in text:
            text = text[:text.index(BEGIN)] + CONVENTIONS.rstrip('\n') + text[text.index(END) + len(END):]
        else:
            text = (text.rstrip('\n') + '\n\n' if text.strip() else '') + CONVENTIONS
        agents.write_text(text, encoding='utf-8')
        claude = root / 'CLAUDE.md'
        if not claude.exists():
            claude.write_text('@AGENTS.md\n', encoding='utf-8')
        elif '@AGENTS.md' not in claude.read_text(encoding='utf-8'):
            notes.append('CLAUDE.md 已存在且没有引入 AGENTS.md；没有修改它。需要时请自行加一行 @AGENTS.md。')

    ignore = root / '.gitignore'
    have = ignore.read_text(encoding='utf-8') if ignore.exists() else ''
    extra = [line for line in (*([f'/{DIR}'] if is_external(root) else []), f'{DIR}/cursor.json', f'{DIR}/log.txt', f'{DIR}/site/', f'{TREES}/', SHOTS, RESULTS) if line not in have.splitlines()]
    if extra:
        ignore.write_text(have.rstrip('\n') + ('\n' if have.strip() else '') + '\n'.join(extra) + '\n', encoding='utf-8')

    if hooks:
        settings = root / '.claude' / 'settings.json'
        data = json.loads(settings.read_text(encoding='utf-8')) if settings.exists() else {}
        table = data.setdefault('hooks', {})
        for event, spec in HOOKS.items():
            command = f'python3 "$CLAUDE_PROJECT_DIR/{DIR}/bin/rf.py" {spec["command"]}'
            entries = table.setdefault(event, [])
            if not any(command == h.get('command') for e in entries for h in e.get('hooks', [])):
                hook = {'type': 'command', 'command': command}
                if spec.get('async'):
                    hook['async'] = True
                entries.append({'hooks': [hook]})
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    else:
        notes.append('没有安装钩子。确认要让 Claude Code 自动沉淀时，运行：python3 rf.py init --hooks')
    if is_external(root):
        # 数据外置的项目：以前写进仓库的约定和技能拿掉（做法已经在 CONVENTIONS.md 里，由应用交给会话）
        notes.extend(drop_repo_rules(root)[1])
    else:
        skill = root / '.claude' / 'skills' / 'rf-sync' / 'SKILL.md'
        skill.parent.mkdir(parents=True, exist_ok=True)
        skill.write_text(SKILL, encoding='utf-8')
        other = root / '.claude' / 'skills' / 'rf-confirm' / 'SKILL.md'
        other.parent.mkdir(parents=True, exist_ok=True)
        other.write_text(CONFIRM_SKILL, encoding='utf-8')
    notes.append(f'已登记为本机项目「{register(root)["name"]}」，在任何目录运行 rf.py serve 都能看到它。')
    write_summaries(root)
    render(root)
    return root, notes

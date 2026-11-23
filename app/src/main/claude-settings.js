// 启动 Claude Code 时用 --settings 带上的钩子和权限规则。
const fs = require('fs');
const path = require('path');
const { BUNDLED, PYTHON, QUOTED, RF_HOME } = require('./config');
const { state } = require('./state');
const install = require('./install-rules');

// 立项中的需求：Claude Code 只能读和聊，不能改项目里的任何文件（设置里禁止在项目目录下改文件，笔记本工具直接拿掉）。
// 唯一能写的地方是项目外面这个需求的草稿目录：设计稿先写在那里，再由 rf.py design save --file 收进需求里。
// 记录、查影响、保存设计稿的命令不用问你。
function draftsFor(id) {
  const dir = path.join(RF_HOME, 'drafts', id);
  try { fs.mkdirSync(dir, { recursive: true }); return fs.realpathSync(dir); } catch (_) { return dir; }
}
const real = (dir) => { try { return fs.realpathSync(dir); } catch (_) { return dir; } };
// 记录目录：项目里的 .relayforge，加上数据外置时它指向的本机目录（~/.relayforge/projects/<项目 ID>）。
// 权限规则按路径匹配，AI 用哪一个路径去改都要挡得住、放得行，所以两个都写。
const factDirs = (root) => [...new Set([`${real(root)}/.relayforge`, real(path.join(root, '.relayforge'))])];

// 停下来时还要先过一道检查：这一轮干了活却没记进展，就把它叫回去记；所以 Stop 不能异步，其余的不用等。
const APPROVAL_TIMEOUT = 3600; // 秒；比 core/approvals.py 等你的 WAIT_SECONDS 长
const hookOf = (wait) => [{ hooks: [{ type: 'command', command: `${PYTHON} ${QUOTED} hook event`, ...(wait ? {} : { async: true }) }] }];
// 执行前的检查（rf.py hook guard，见 backend/relayforge/core/guard.py）：权限规则拦下时只说「被禁止」，不说该怎么做，会话常常换个写法再试。
// 这里先认出一定会被拦的调用（只由用户触发的 rf.py 命令；这个会话不能改的记录、项目文件、产品代码），替权限规则拦下并说明该用哪条命令，
// 同一个目标第二次被拦就让它停下来问你。拦不拦仍以权限规则和 rf.py 自己的校验为准（它们照样在），这里只多给一句说明。
// 钩子要等它的结果（不能异步）；rf.py 遇到 hook guard 只加载 guard.py 一个文件，每次几十毫秒。
const shq = (text) => `'${String(text).replace(/'/g, "'\\''")}'`;
// 这个会话不能改哪些地方（和 hooks() 里 deny 的 Edit / Write 规则拦的是同一批）：Claude Code 的钩子按参数传，Codex 的钩子按环境变量传（codex-hooks.js）
function guardSpec({ drafts = null, probe = false, tester = false, place = null }) {
  const readOnly = !!(drafts || probe);
  return {
    facts: readOnly ? factDirs(state.project) : tester ? [...new Set([place, state.project].filter(Boolean).flatMap(factDirs))] : [],
    project: readOnly ? real(state.project) : null,
    drafts: drafts || null,
    product: tester && place ? [`${real(place)}/src/main`] : [],
    kind: probe ? 'probe' : drafts ? 'intake' : tester ? 'test' : null,
  };
}
function guardHooks({ facts = [], project = null, drafts = null, product = [], kind = null }) {
  const flags = [...facts.map((d) => `--facts ${shq(d)}`), ...(project ? [`--project ${shq(project)}`] : []), ...(drafts ? [`--drafts ${shq(drafts)}`] : []),
    ...product.map((d) => `--product ${shq(d)}`), ...(kind ? [`--kind ${kind}`] : [])];
  const command = [`${PYTHON} ${QUOTED} hook guard`, ...flags].join(' ');
  return [
    { matcher: 'Bash', hooks: [{ type: 'command', command }] },
    ...(facts.length || project || product.length ? [{ matcher: 'Edit|MultiEdit|Write|NotebookEdit', hooks: [{ type: 'command', command }] }] : []),
  ];
}
// 交给 AI 排查的会话：和立项一样不能改项目里的文件（项目主目录整个禁止 Edit 和 Write，笔记本工具拿掉），能写的只有项目外面它自己的草稿目录；
// 另外明确不许它碰「该由你决定」的命令（开始修复、改设置、回复、确认、退回、转需求、立项和完成），常见的改仓库状态的命令也不放行。
// 说明：会写文件的 shell 命令（比如 echo > 文件）没有被预先放行，默认要问你；它们不在这里一条条拦，这里拦的是上面列的。
const RF_FORMS = [BUNDLED, '.relayforge/bin/rf.py'];
const PROBE_DENY = [
  ...RF_FORMS.flatMap((rf) => [...['fix', 'set', 'reply', 'confirm', 'unconfirm', 'reject', 'to-req', 'from-req', 'probe', 'handback'].map((a) => `Bash(python3 ${rf} issue ${a}:*)`),
    ...['start', 'done', 'drop', 'set'].map((a) => `Bash(python3 ${rf} req ${a}:*)`)]),
  ...['add', 'commit', 'checkout', 'switch', 'merge', 'rebase', 'reset', 'stash', 'push', 'apply', 'restore', 'clean', 'cherry-pick', 'worktree'].map((a) => `Bash(git ${a}:*)`),
  'Bash(rm:*)', 'Bash(mv:*)',
];
// 推送（发布）只由你在应用里点「发布」触发：推送可能触发部署。应用开出的每一种会话（立项、开发、修复、排查、聊天）都禁止 git push 和
// rf.py publish run / publish pull；publish status、publish list 只读，放行。git 的全局选项写在 push 前面（git -C 目录 push）也要拦。
const PUBLISH_DENY = [
  'Bash(git push:*)', 'Bash(git * push*)', // * 和结尾的 :* 不能混用：混用时 * 被当成普通字符，规则永远对不上（Claude Code 启动时会警告）
  ...RF_FORMS.flatMap((rf) => ['run', 'pull'].map((a) => `Bash(python3 ${rf} publish ${a}:*)`)),
];
const PUBLISH_ALLOW = [`Bash(python3 ${BUNDLED} publish status:*)`, `Bash(python3 ${BUNDLED} publish list:*)`];
// 追溯：从提交或代码找到需求、会话和原因（rf.py trace、rf.py why）只读，每种会话都放行；项目里的入口写法也放行。
// usage 同样只读，给出 token 数和模型。改代码之前不知道一段代码为什么是这样，先用 rf.py why 看。
const TRACE_ALLOW = RF_FORMS.flatMap((rf) => ['trace', 'why', 'usage'].map((a) => `Bash(python3 ${rf} ${a}:*)`));
// 历史查询：以前怎么做过、为什么这么定（rf.py history search / show / session）只读，每种会话都放行，立项和排查这种只读会话也一样。
// rf.py summarize 和 rf.py archive 是程序在确认完成以后用的，不放行。
const HISTORY_ALLOW = RF_FORMS.flatMap((rf) => ['search', 'show', 'session'].map((a) => `Bash(python3 ${rf} history ${a}:*)`));
// 「让 AI 来合并」开的聊天：在项目主目录里把远端的合并进当前分支并提交，所以这几个 git 命令不用每次问；推送照旧被 PUBLISH_DENY 禁止
const MERGE_ALLOW = ['status', 'diff', 'log', 'show', 'add', 'merge', 'commit'].map((a) => `Bash(git ${a}:*)`);
// 测试集（rf.py test）：列表、查看只读，每种会话都放行；新建、加数据、改删、运行、写分析、生成桥接文件是开发和修复会话干活用的，只在这两种会话里放行。
// 同意或不同意 AI 的改动、标成过期、恢复、改挂靠、以用户身份保存、标为看过了，只由你在应用里点，每种会话都禁止。
const TEST_READ_ALLOW = RF_FORMS.flatMap((rf) => ['list', 'show'].map((a) => `Bash(python3 ${rf} test ${a}:*)`));
const TEST_WORK_ALLOW = RF_FORMS.flatMap((rf) => ['new', 'add', 'edit', 'rm', 'run', 'analyze', 'bridge'].map((a) => `Bash(python3 ${rf} test ${a}:*)`));
// 测试集查看器「让 AI 改」开的日常聊天：只放行改数据和运行、写分析用的几个（不新建测试集、不生成桥接文件）
const TEST_AI_ALLOW = RF_FORMS.flatMap((rf) => ['add', 'edit', 'rm', 'run', 'analyze'].map((a) => `Bash(python3 ${rf} test ${a}:*)`));
const TEST_DENY = RF_FORMS.flatMap((rf) => ['approve', 'reject', 'stale', 'revive', 'move', 'save', 'seen', 'config', 'approve-build'].map((a) => `Bash(python3 ${rf} test ${a}:*)`));
// 「让 AI 来处理」开的聊天：看测试集、运行测试集（验证它试出来的做法），不改测试数据
const TEST_FIX_ALLOW = RF_FORMS.map((rf) => `Bash(python3 ${rf} test run:*)`);
// 测试环境（rf.py env）：列表、查看（打码的）、重新读配置、检查连通只读，放行给开发和修复会话，以及「让 AI 读项目，帮我填」开的聊天；
// propose（把建议交回）在这几种会话里也放行。新建、复制、改名、删除、保存、选用、选当前测试用户、取明文、采纳或忽略建议只由你在应用里做，
// 每种会话都禁止；本机存保密值的目录也禁止读（这挡得住读文件的工具，挡不住 shell 命令，所以保密值文件本身是 600、只在本机）。
// 看准备步骤（prep）、手动执行一遍（prep-run，只执行你同意过的命令，会话里不带保密值）、「让 AI 来处理」的说明（handoff）也放行。
// 同意执行准备步骤（approve）、允许 AI 的会话带上保密值（ai-secrets）除了这里禁止，rf.py 还要应用的标记（app-token.js），会话里做不了。
// 人工验收：plan（这次怎么启动，保密的打码）只读，也放行；launch（取启动的全部设置，带保密值）和 accept-clean（清掉验收的临时目录和副本）只由应用做，禁止。
const ENV_READ_ALLOW = RF_FORMS.flatMap((rf) => ['list', 'show', 'scan', 'check', 'propose', 'prep', 'prep-run', 'handoff', 'plan'].map((a) => `Bash(python3 ${rf} env ${a}:*)`));
const ENV_DENY = [
  ...RF_FORMS.flatMap((rf) => ['new', 'copy', 'rename', 'rm', 'save', 'use', 'user', 'reveal', 'adopt', 'ignore', 'approve', 'ai-secrets', 'launch', 'accept-clean'].map((a) => `Bash(python3 ${rf} env ${a}:*)`)),
  `Read(/${RF_HOME}/envs/**)`,
];
// 测试阶段（rf.py req …，见 FORMAT.md 的「需求」）：交给测试、跳过测试只由你在应用里点，应用自己记的轮次（test-kicked / test-told）也一样，
// 每种会话都禁止（rf.py 还要应用的标记）。测试会话用的检查步骤、交回结论只在测试会话里放行，开发会话里禁止（归类挪到了立项：见 REQ_CASE_ALLOW）；
// 开发会话修好以后说「再测」（retest）只在开发会话里放行，测试会话里禁止。
// 人工验收的逐条「通过」「这里不对」（req accept）也一样：只由你在应用里点。
const REQ_USER_DENY = RF_FORMS.flatMap((rf) => ['to-test', 'skip-test', 'test-kicked', 'test-told', 'accept'].map((a) => `Bash(python3 ${rf} req ${a}:*)`));
// 其余只由你触发的命令（AGENTS.md 第 14、17、19、21 条；完整的清单在 backend/relayforge/core/trust.py 的 USER_COMMANDS）：立项、完成、放弃、恢复、手动改状态，
// 问题的确认、撤回、退回、排查、修复、改设置、回复、转需求，总结和补存会话记录，切换项目主目录的分支，确认拆分需求（split，应用替你检查和执行「同意拆分」）。每种会话都禁止；rf.py 自己也校验应用的标记，这里只是让会话少碰壁。
// 应用的登记目录（RF_HOME/apps）不禁读：会话里的 rf.py 要靠登记文件找到应用的 socket，才能验会话令牌、请应用代写记录
// （core/identity.py 的 find_app）。Claude Code 会把权限里的 Read 禁止也套到沙箱上，以前这里禁读，沙箱里的 rf.py 就找不到应用。
// 登记文件里只有进程号、socket 的位置和应用标记的 SHA-256，读到也冒充不了应用；写仍然不行（sandbox() 的 denyWrite）。
const USER_DENY = [
  ...RF_FORMS.flatMap((rf) => [...['start', 'done', 'drop', 'set', 'depend', 'keep', 'restore', 'choose', 'case-ok', 'case-no', 'case-save'].map((a) => `Bash(python3 ${rf} req ${a}:*)`),
    ...['confirm', 'unconfirm', 'reject', 'probe', 'fix', 'set', 'reply', 'to-req', 'from-req', 'review', 'review-run', 'review-discard', 'review-apply'].map((a) => `Bash(python3 ${rf} issue ${a}:*)`),
    `Bash(python3 ${rf} branch refresh:*)`,
    `Bash(python3 ${rf} summarize:*)`, `Bash(python3 ${rf} archive:*)`, `Bash(python3 ${rf} split:*)`, `Bash(python3 ${rf} data import:*)`, `Bash(python3 ${rf} data migrate:*)`, `Bash(python3 ${rf} branch switch:*)`,
    `Bash(python3 ${rf} map trunk:*)`, ...['adopt', 'ignore', 'done'].map((a) => `Bash(python3 ${rf} fresh ${a}:*)`)]),
  ...RF_FORMS.map((rf) => `Bash(python3 ${rf} subagent decision:*)`),
];
const REQ_TEST_ALLOW = RF_FORMS.flatMap((rf) => ['steps', 'verdict'].map((a) => `Bash(python3 ${rf} req ${a}:*)`));
// 测试用例（立项会话写、开发会话看、测试会话对接和补充）：写用例、改用例、删用例、给验收条件归类、看用例和结果，在需求的立项、开发、测试会话里都放行；
// 谁能直接改、谁只能提给你（立项以后改用例一律要你同意）由 rf.py 按会话令牌定。同意、不同意用例的改动和你自己改用例（case-ok、case-no、case-save）只由你在应用里点，每种会话都禁止（USER_DENY）。
const REQ_CASE_ALLOW = RF_FORMS.flatMap((rf) => ['case-add', 'case-edit', 'case-rm', 'cases', 'classify'].map((a) => `Bash(python3 ${rf} req ${a}:*)`));
const REQ_RETEST = RF_FORMS.map((rf) => `Bash(python3 ${rf} req retest:*)`);
// 需求的开发会话：测试集由测试会话写，开发会话不负责新建（不放行 test new，用的时候会问你），跑已有的、改数据（改测试会话写的要你同意）照旧放行
const TEST_DEV_ALLOW = RF_FORMS.flatMap((rf) => ['add', 'edit', 'rm', 'run', 'analyze', 'bridge'].map((a) => `Bash(python3 ${rf} test ${a}:*)`));
// 测试会话不改产品代码：Claude Code 的规则只能按路径挡，表达不了「除了测试目录和接入代码以外」，所以这里只挡得住确定是产品代码的地方
// （Java/Kotlin 的 src/main/**）、事实目录（只用 rf.py 写）和笔记本工具；其余靠结束检查兜底（rf.py hook event：这一轮改了产品代码就拦下，要它撤回、改为交回）。
// 事实目录不写规则：Edit(路径) 的禁止会连带用到沙箱上（shell 也写不了），测试会话里的 rf.py test run 要把运行结果写进事实目录；
// Write(路径) 规则又永远对不上。所以 Edit 工具改事实目录只靠执行前的检查拦（guardSpec 给测试会话的 facts）。
// 只写 Edit 规则：Claude Code 只拿 Edit(路径) 检查所有改文件的工具，Write(路径) 规则永远对不上（启动时会警告）
const testDeny = (place) => ['NotebookEdit', ...(place ? [`Edit(/${real(place)}/src/main/**)`] : [])];
// 安装的分级（install-rules.js）：项目里的安装和构建预先放行，只给开发、修复会话和「让 AI 来处理」的聊天；
// 系统级的每次都问（ask）、发布部署和保密值的途径禁止（deny），每种会话都一样
const INSTALL = install.rules(RF_HOME);
// 系统的沙箱（Claude Code 的 sandbox，macOS 上是 Seatbelt）：会话和你是同一个系统用户，上面的权限规则只按命令的写法匹配，换个写法就绕过去了；
// 应用的登记文件（app-token.js）也只是一个普通文件，会话能给自己写一份。沙箱里运行的命令跳不出沙箱，rf.py 在沙箱里一律不认应用的标记
// （backend/relayforge/core/trust.py 的 sandboxed），这才是真正的边界。所以：
// - 不许跳出沙箱（allowUnsandboxedCommands: false），开不了沙箱就不执行命令（failIfUnavailable）；
// - 能写的地方：工作目录（默认）、项目主目录（记录、工作目录共用的 .git）、常见的构建缓存，以及 RF_HOME 里会话自己的 rf.py 真要写的
//   几个子目录（RF_HOME_WRITABLE）。RF_HOME 别的地方一律不能写：那里大多是应用的控制文件——等你批准的请求和回答（approvals）、
//   程序发给会话的话（inbox）、上次没退干净的进程记录（run，应用启动时照着杀进程）、项目登记、合并队列……写得了就能自己批准自己、
//   冒充程序给别的会话传话、让应用替它杀进程。按「只放行这几个」写，以后新加的目录默认也不能写；
//   .git/hooks、.git/config、shell 的配置文件由 Claude Code 自己挡着（沙箱内置的保护）；
// - 不能写的地方（RF_HOME 之外、或者要特别说明的）：应用的登记目录（不能冒充应用和会话身份的 socket）、应用的设置、
//   项目的 config.json（里面的 extractor 是应用在沙箱外执行的命令）；
// - 不能读：保密的值；等你批准的请求（看不到请求的编号）、收件箱；进程记录的签名钥匙（keys，见 record-sign.js）；
// - 测试环境的本机部分（backend/relayforge/envs/store.py）：保密的值（RF_HOME/envs）读不了也写不了；你的决定（RF_HOME/env-consents：
//   同意执行的命令、允许带上保密值、选用哪一套）能读不能写，写得了就能冒充你同意，应用之后会在沙箱外按它执行；其余状态（env-state）能读能写；
// - 联网照旧（新的地址按会话的权限模式问你）；只放行应用的 socket（验会话令牌、代写记录），并允许在本机起服务、连 localhost。
const CACHES = ['.m2', '.gradle', '.npm', '.pnpm-store', '.yarn', '.bun', '.cargo', '.rustup', 'go', '.cache', '.nuget', '.ivy2', '.sbt', '.cocoapods',
  'Library/Caches'];
// RF_HOME 里会话自己的 rf.py 要写的：草稿（design save --file 之前）、数据外置的项目记录、测试环境里会话能改的那部分、测试集的编译缓存
const RF_HOME_WRITABLE = ['drafts', 'projects', 'env-state', 'cache'];
const RF_HOME_HIDDEN = ['envs', 'approvals', 'inbox', 'keys']; // 不能读
function sandbox(project) {
  const home = require('os').homedir();
  const rfHome = real(RF_HOME);
  const apps = path.dirname(require('./app-token').file);
  const under = (names) => names.map((n) => path.join(rfHome, n));
  return {
    enabled: true, allowUnsandboxedCommands: false, failIfUnavailable: process.platform === 'darwin',
    filesystem: {
      allowWrite: [...new Set([...(project ? [real(project)] : []), ...under(RF_HOME_WRITABLE), ...CACHES.map((d) => path.join(home, d))])],
      // RF_HOME 本来就不在能写的里面，这里再点名一遍：万一工作目录或项目放在了 RF_HOME 下面，也盖得住
      denyWrite: [...new Set([apps, real(apps), ...under(['envs', 'env-consents', 'approvals', 'inbox', 'run', 'keys', 'settings.json', 'projects.json']),
        ...(project ? factDirs(project).map((f) => `${f}/config.json`) : [])])],
      denyRead: under(RF_HOME_HIDDEN),
    },
    // socket 的位置以真正监听的为准：路径太长时 session-token.js 会改到临时目录下
    network: { allowUnixSockets: [require('./session-token').socketPath() || path.join(apps, process.pid + '.sock')], allowLocalBinding: true },
  };
}
// building：需求的开发会话、问题的修复会话（不是立项、排查、日常聊天、大图编辑）；testing：「让 AI 改」测试集开的日常聊天
// envFill：测试环境「让 AI 读项目，帮我填」开的聊天；envFix：「让 AI 来处理」开的聊天（测试集跑不起来、准备步骤失败、依赖连不上）
// more.role：'develop' 需求的开发会话，'test' 需求的测试会话（more.place 是它的工作目录），'fix' 问题的修复会话；不给时和以前一样（building 一律当开发和修复）
// more.req：这是一个需求的会话（立项、开发、测试），放行测试用例的命令（REQ_CASE_ALLOW）
// more.split：这个会话可以提出拆分需求（放行 req list，用来查重已有的需求）；以前第 8 个参数直接传 true/false，也认
const hooks = (drafts, probe, merging, building, testing, envFill, envFix, more = {}) => {
  if (!more || typeof more !== 'object') more = { split: !!more };
  const role = more.role || null;
  const tester = role === 'test' && !drafts && !probe;
  const work = (building || tester) && !drafts && !probe;
  const guard = guardHooks(guardSpec({ drafts, probe, tester, place: more.place }));
  return JSON.stringify({
  hooks: {
    UserPromptSubmit: hookOf(false), Stop: hookOf(true), SubagentStart: hookOf(false), SubagentStop: hookOf(false),
    // 无界面运行（sessions.js 的 headless）：没有终端弹确认框，钩子要等你在应用里点批准或拒绝（core/approvals.py），所以同步、给足时间
    PermissionRequest: more.headless ? [{ hooks: [{ type: 'command', command: `${PYTHON} ${QUOTED} hook event`, timeout: APPROVAL_TIMEOUT }] }] : hookOf(false),
    Notification: hookOf(false), PostToolUse: hookOf(false), PreToolUse: guard,
  },
  permissions: {
    // 问题：任何会话都能记一条、看问题；修复会话要用 brief 和 handback。确认和退回（issue confirm、issue reject）只由你在应用里点，不放行
    allow: [`Bash(python3 ${BUNDLED} note:*)`, `Bash(python3 ${BUNDLED} req brief:*)`, `Bash(python3 ${BUNDLED} design:*)`, `Bash(python3 ${BUNDLED} map:*)`,
      `Bash(python3 ${BUNDLED} issue new:*)`, `Bash(python3 ${BUNDLED} issue list:*)`, `Bash(python3 ${BUNDLED} issue show:*)`,
      `Bash(python3 ${BUNDLED} issue brief:*)`, `Bash(python3 ${BUNDLED} issue handback:*)`, ...PUBLISH_ALLOW, ...TRACE_ALLOW, ...HISTORY_ALLOW, ...TEST_READ_ALLOW,
      ...(role === 'develop' ? RF_FORMS.flatMap((rf) => ['status', 'run'].map((action) =>
        `Bash(python3 ${rf} subagent ${action}:*)`)) : []),
      ...(more.split ? [`Bash(python3 ${BUNDLED} req list:*)`] : []),
      ...(work ? (role === 'develop' ? TEST_DEV_ALLOW : TEST_WORK_ALLOW) : []),
      ...(testing && !work && !drafts && !probe ? TEST_AI_ALLOW : []),
      ...((work || envFill || envFix) && !drafts && !probe ? ENV_READ_ALLOW : []),
      ...(envFix && !work && !drafts && !probe ? TEST_FIX_ALLOW : []),
      ...((work || envFix) && !drafts && !probe ? INSTALL.allow : []),
      ...(tester ? REQ_TEST_ALLOW : []),
      ...(more.req || tester || role === 'develop' ? REQ_CASE_ALLOW : []),
      ...(role === 'develop' && work ? REQ_RETEST : []),
      // 排查会话记它的结论；别的会话用不上，不放行
      ...(probe ? [`Bash(python3 ${BUNDLED} issue finding:*)`] : []),
      ...(merging && !probe && !drafts ? MERGE_ALLOW : []),
      // 事实只在项目主目录里有一份：会话跑在需求或修复的工作目录里，读主目录的 .relayforge/ 不用问
      ...(state.project ? factDirs(state.project).map((f) => `Read(/${f}/**)`) : []),
      ...(drafts ? [`Edit(/${drafts}/**)`, `Bash(mkdir -p ${drafts})`] : [])],
    ask: INSTALL.ask,
    deny: [...(drafts || probe ? ['NotebookEdit', ...[real(state.project), ...factDirs(state.project)].map((d) => `Edit(/${d}/**)`), ...(probe ? PROBE_DENY : [])] : []), ...PUBLISH_DENY, ...TEST_DENY, ...ENV_DENY, ...INSTALL.deny,
      ...REQ_USER_DENY, ...USER_DENY, ...(tester ? [...REQ_RETEST, ...testDeny(more.place)] : role === 'develop' ? REQ_TEST_ALLOW : [])],
  },
  sandbox: sandbox(state.project),
  });
};

module.exports = { factDirs, draftsFor, hooks, sandbox, CACHES, RF_HOME_WRITABLE, RF_HOME_HIDDEN, guardHooks, guardSpec, REQ_USER_DENY, USER_DENY, REQ_TEST_ALLOW, REQ_CASE_ALLOW, REQ_RETEST, TEST_DEV_ALLOW, testDeny, PROBE_DENY, PUBLISH_DENY, PUBLISH_ALLOW, MERGE_ALLOW, TRACE_ALLOW, HISTORY_ALLOW, TEST_READ_ALLOW, TEST_WORK_ALLOW, TEST_AI_ALLOW, TEST_DENY, TEST_FIX_ALLOW, ENV_READ_ALLOW, ENV_DENY, INSTALL };

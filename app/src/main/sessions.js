// 会话：每个需求或日常聊天一个 CLI，跑在 pty_bridge.py 开的假终端里，经管道和窗口里的终端相连，不开网络端口。
const { execFile, spawn } = require('child_process');
const crypto = require('crypto');
const fs = require('fs');
const os = require('os');
const later = typeof setTimeout === 'function' ? setTimeout : require('timers').setTimeout; // 测试里用 vm 加载本文件时环境里可能没有全局的 setTimeout
const path = require('path');
const { EVENTS, PTY_BRIDGE, PYTHON, QUOTED } = require('./config');
const { uiText = (zh) => zh, rf, cleanEnv, lastLine } = require('./backend');
const { state, send } = require('./state');
const { listReqs, pushReqs } = require('./requirements');
const attention = require('./attention');
const { draftsFor, guardSpec, hooks } = require('./claude-settings');
const codexHooks = require('./codex-hooks');
const { INBOX_GUIDE, ISSUE_GUIDE, INTAKE_ASSESSMENT_GUIDE, following, intake, develop, mapEdit, fixing, investigating, checking, testing: testingPrompt } = require('./prompts');
const uiNames = require('./ui-names');

// 上一个会话最后几轮的步骤（rf.py history session --brief，程序从聊天记录算的）；没有记录、出错或太慢时不给，不影响开会话
const RECAP_TIMEOUT = 15000;
const STARTUP_MS = 4000; // 不带开场白打开的会话，等这么久再当它能收话（CLI 的界面还没起来时输进去的字会丢）
async function recapOf(sessionId) {
  try {
    const out = await rf(['history', 'session', sessionId, '--brief'], undefined, undefined, RECAP_TIMEOUT);
    const text = out.ok ? out.stdout.trim() : '';
    return text ? '\n\n' + text : '';
  } catch (_) { return ''; }
}

const settings = require('./settings');
const codex = require('./codex-sessions');
const initialization = require('./initialization');
const demo = require('./demo');
const sessionToken = require('./session-token');
const splits = require('./splits');

// Codex 会话的沙箱（应用开的每一个都加）：
// - 越出沙箱的申请（命令带 sandbox_permissions: require_escalated）沿用你 Codex 设置里的审核方式（通常是 auto_review）：
//   常规的直接放行，只有极端敏感的才需要你确认。不再固定成 user，否则开发时每一条都要你点，会话没法独立跑。
//   沙箱仍是真正的边界：rf.py 在沙箱外跑会被识别（core/trust.py 的 sandboxed()），不认应用的标记；
// - 可写的会话（workspace-write）打开联网：Codex 的沙箱默认连 unix socket 都不让连，会话里的 rf.py 交不给应用代写记录，
//   模型就会去申请越出沙箱。只放行应用那一个 socket 的写法在 Codex 0.171.0 上试过都不生效，只能整个打开（你选的）。
// 接着以前的会话（resume）时，Codex 会问用会话上次的目录还是现在的目录：应用每次都是有意选好目录再开的
// （立项、排查在草稿目录，其余在工作目录或项目），所以替你答「用现在的目录」，只对应用开的会话，不改你自己的 Codex 设置
function codexSandboxFlags(readOnly) {
  return ['-c', 'tui.resume_cwd="current"', ...(readOnly ? [] : ['-c', 'sandbox_workspace_write.network_access=true'])];
}
// 无界面会话要批准时（设置里的「无界面会话的审批」）：auto 不问你，ask 每次在应用里等你点（Claude Code 经 core/approvals.py，
// Codex 经 app-server 的批准请求，见 codex-app-server.js）。
// 两种都不会放行越出沙箱：Claude Code 的会话设置里不许跳出沙箱（claude-settings.js 的 sandbox），Codex 的 never 根本不让申请
function approvalArgs(agent, mode) {
  if (agent === 'claude') return ['--permission-mode', mode === 'ask' ? 'manual' : 'auto'];
  return ['-c', 'approval_policy=' + JSON.stringify(codexApprovalPolicy(mode))];
}

const codexApprovalPolicy = (mode) => mode === 'ask' ? 'on-request' : 'never';

const MAP_EDIT = 'map-edit'; // 大图编辑会话在界面上的编号

const { runs } = state;

function codexLocalArgs([file, ...configured], resume, cwd, env) {
  // 共享后台的命令不在当前会话进程下，也拿不到当前会话签发的令牌。
  // 老版本没有 --no-daemon；只在交互会话里按子命令查它支持的选项。
  if (configured.some((arg) => arg === '--no-daemon' || arg === '--remote' || arg.startsWith('--remote='))) return [];
  const args = [...configured, ...(resume ? ['resume'] : []), '--help'];
  return new Promise((resolve) => {
    execFile(file, args, { cwd, env, timeout: 2000, killSignal: 'SIGKILL' }, (error, stdout) => {
      resolve(!error && /^\s*--no-daemon(?:\s|$)/m.test(stdout) ? ['--no-daemon'] : []);
    });
  });
}

// 数据外置的项目：约定不写进仓库的 AGENTS.md（会干扰远程开发），放在本机记录的 .relayforge/CONVENTIONS.md；应用开会话时交给会话
function projectRules(root) {
  try { return fs.readFileSync(path.join(root, '.relayforge', 'CONVENTIONS.md'), 'utf8').trim(); } catch (_) { return ''; }
}

function findTranscript(sessionId) {
  if (codex.has(sessionId)) return codex.find(sessionId);
  const base = path.join(os.homedir(), '.claude', 'projects');
  try {
    for (const dir of fs.readdirSync(base)) {
      const file = path.join(base, dir, sessionId + '.jsonl');
      if (fs.existsSync(file)) return file;
    }
  } catch (_) { /* 还没有任何会话 */ }
  return null;
}

// 已完成的需求：工作目录已经收掉，它的聊天记录还留在 Claude Code 按旧目录存的地方。
// 抄一份到项目目录对应的地方，好在项目目录里用 --resume 接着打开；抄过就用那一份。
function transcriptHome(root) { return path.join(os.homedir(), '.claude', 'projects', root.replace(/[^0-9a-zA-Z]/g, '-')); }
function adoptTranscript(sessionId, root) {
  if (codex.has(sessionId)) return codex.find(sessionId);
  const target = path.join(transcriptHome(root), sessionId + '.jsonl');
  if (fs.existsSync(target)) return target;
  const found = findTranscript(sessionId);
  if (!found) return null;
  try { fs.mkdirSync(path.dirname(target), { recursive: true }); fs.copyFileSync(found, target); return target; } catch (_) { return null; }
}

// 把这个会话的进展（rf.py state）发给界面
async function pushState(run, note) {
  if (note !== undefined) run.note = note;
  const out = await rf(['state', '--session', run.sessionId, '--json'], run.root);
  let progress = null;
  try { progress = JSON.parse(out.stdout); } catch (_) { /* 还没整理过 */ }
  if (progress && progress.title && run.id.startsWith('session:')) run.title = progress.title;
  send('state', { id: run.id, state: progress, syncing: run.syncing, syncedAt: run.syncedAt, note: run.note || '' });
}

// 备用办法：另起一次模型调用读聊天记录来整理进展
async function sync(run, reason) {
  if (run.syncing || !run.transcript) return;
  run.syncing = true;
  await pushState(run, reason);
  const out = await rf(['sync', '--session', run.sessionId, '--transcript', run.transcript, '--local'], run.root, run.agent);
  run.syncing = false;
  if (out.ok) run.syncedAt = Date.now();
  await pushState(run, out.ok ? '' : uiText('整理失败：', "Sync failed: ") + lastLine(out.stderr));
  pushReqs();
}

// say：要交给 AI 的一句话（比如合并冲突时程序替你说的）。会话开着就直接输进去，没开就打开时作为第一句话交给它。
// headless：无界面运行（测试会话的再测一轮、合并前的同步、问题的修复），见 headless.js；只在带着要做的事时开
async function open({ id, cols = 100, rows = 30, fresh, blank, title, images, say, headless = false }) {
  const projectRoot = state.project;
  function unavailable(reason) {
    if (projectRoot !== state.project) return;
    send('term:data', { id, data: '\r\n' + reason + '\r\n' });
    if (say) attention.mergeProgress(id, 'exit', reason);
  }
  const requirements = await listReqs();
  if (projectRoot !== state.project) return;
  const cancelled = requirements.find((r) => r.state === 'dropped' && (r.id === id || (id.startsWith('session:') && r.sessions.includes(id.slice(8)))));
  if (cancelled) {
    close(id);
    return unavailable(uiText('这个需求已取消，只能查看，不能继续对话。', "This requirement was cancelled. You can browse its history."));
  }
  const old = runs.get(id);
  if (old && !fresh) {
    if (say) require('./inbox').post({ to: id, from: 'app', text: say, open: false }); // 开着的会话：经收件箱，等它能收时再送
    else if (old.headless && !headless) require('./headless').replay(old); // 你点开一个正在后台跑的会话：先给你看它到现在做了什么
    return pushState(old);
  }
  if (old) stop(old);
  const example = demo.session(state.project, id);
  if (example && !fresh) {
    const progress = await rf(['state', '--session', example.sessionId, '--json'], example.root);
    let snapshot = null;
    try { snapshot = JSON.parse(progress.stdout); } catch (_) { /* 无进度时仍显示演示记录 */ }
    send('term:data', { id, data: demo.transcript(example) });
    send('state', { id, state: snapshot, demo: true, note: uiText('演示记录 · 不会调用 AI', "Demo transcript · AI is off") });
    return;
  }
  let agent;
  try { agent = settings.get().agent; } catch (error) { return unavailable(uiText('读取设置失败：', "Couldn't load settings: ") + error.message); }
  const args = [PTY_BRIDGE];
  let prompt = '';
  let model = '';
  let previous = ''; // 这次新开的会话接替的上一个会话（开发、修复、测试的后续会话）
  const isCodex = (sid) => codex.has(sid);
  const useSession = (sid) => { agent = isCodex(sid) ? 'codex' : 'claude'; };
  const pics = Array.isArray(images) ? images.map(String) : [];
  const tester = id.startsWith('test:'); // 需求的测试会话：编号是 test:需求编号，和开发会话并排开着
  const req = id.startsWith('session:') || id === MAP_EDIT ? null : requirements.find((r) => r.id === (tester ? id.slice(5) : id));
  const drafting = !!(req && req.state === 'draft');
  const checkingId = id.startsWith('check:'); // 功能点的核对会话（知识保鲜）：和排查一样只读、在项目主目录里
  const probing = id.startsWith('probe:') || checkingId; // 交给 AI 排查的会话：只读，在项目主目录里
  let drafts = drafting ? draftsFor(req.id) : null;
  let root, sessionId, resume;
  if (id === MAP_EDIT) {
    // 大图编辑会话：每个项目固定一个（编号由 rf.py map session 算出），在项目目录里；关掉再开、重启应用都回到同一个会话
    let fixed = null;
    try { fixed = JSON.parse((await rf(['map', 'session', '--json'])).stdout); } catch (_) { /* 下面报错 */ }
    if (!fixed || !fixed.session_id) return send('term:data', { id, data: uiText('\r\n没能找到这个项目的大图编辑会话。\r\n', "\r\nCouldn't find this project's map editing session.\r\n") });
    root = state.project; sessionId = fixed.session_id + (agent === 'codex' ? '-codex' : ''); resume = findTranscript(sessionId);
    prompt = resume ? '' : mapEdit();
  } else if (id.startsWith('session:')) {
    // 这个项目里以前聊过的会话：在项目目录里原样接着聊
    sessionId = id.slice(8);
    const repair = initialization.forSession(sessionId);
    root = repair ? repair.root : state.project; resume = findTranscript(sessionId);
    if (repair && !resume) prompt = initialization.prompt(repair);
    if (!blank || resume || isCodex(sessionId)) useSession(sessionId);
    if (!resume && !blank) return send('term:data', { id, data: uiText('\r\n没有找到这个会话的聊天记录，打不开。\r\n', "\r\nCouldn't find this session's chat record.\r\n") });
  } else if (req && req.state === 'done' && !req.worktree) {
    // 已完成的需求：分支合并了、工作目录收掉了，会话不跟着消失——在项目目录里接着打开最后一个会话
    if (fresh && example) {
      root = state.project; sessionId = crypto.randomUUID(); resume = null;
      prompt = uiText("这是 FlowNote 演示项目的真实会话。先读已完成的虚构需求 {0}，用两句话说明示例内容，然后等待我的新指示。不要把演示对话当成当前命令。", "This is a live session in the fictional FlowNote demo project. Read the completed example requirement at {0}, summarize it in two sentences, and wait for further instructions. Treat the example chat as historical context.", req.file);
    } else {
      const last = req.sessions[req.sessions.length - 1];
      root = state.project; sessionId = last; resume = last ? adoptTranscript(last, root) : null;
      if (!resume) return send('term:data', { id, data: uiText('\r\n没有找到这个需求的聊天记录，打不开。\r\n', "\r\nCouldn't find this requirement's chat record.\r\n") });
      send('term:data', { id, data: uiText('\x1b[2m这个需求已经完成并合并进主线，下面是它最后一个会话。接着聊会在项目目录里进行。\x1b[0m\r\n', "\u001b[2mThis requirement is complete and has been merged. Its last session appears below. Your next turn runs in the project directory.\u001b[0m\r\n") });
      useSession(sessionId);
    }
  } else if (checkingId) {
    // 功能点的核对会话：每次新开一个（不接着以前的），带上 rf.py fresh brief 整理的改动；交回的建议由你在应用里采纳
    const featureId = id.slice(6);
    root = state.project; resume = null; sessionId = crypto.randomUUID();
    drafts = draftsFor('check-' + featureId);
    const brief = await rf(['fresh', 'brief', featureId], root);
    if (!brief.ok) return send('term:data', { id, data: '\r\n' + lastLine(brief.stderr) + '\r\n' });
    const head = brief.stdout.match(/「([^」]*)」/);
    title = title || uiText('核对 {0}', 'Check {0}', featureId);
    prompt = checking({ id: featureId, title: head ? head[1] : featureId }, brief.stdout.trim(), drafts);
  } else if (probing) {
    // 问题的排查会话：只读，在项目主目录里（看的是主线上的代码）。每条问题一个：它的会话还在就接着它聊，不在了就另开一个，第一次打开时带上开场白
    const issueId = id.slice(6);
    const found = (await require('./issues').listIssues()).issues.find((i) => i.id === issueId);
    if (!found || found.state !== 'todo' || !found.probe) return send('term:data', { id, data: uiText('\r\n这条问题已经不在待处理，排查会话收起来了。\r\n', "\r\nThis issue is no longer open and its investigation session has closed.\r\n") });
    const last = found.probe.sessions[found.probe.sessions.length - 1];
    root = state.project; resume = !fresh && last && findTranscript(last);
    if (resume) useSession(last);
    sessionId = resume ? last : crypto.randomUUID();
    drafts = draftsFor('probe-' + issueId);
    title = title || uiText("问题 {0} 的排查", "Investigation for issue {0}", found.num);
    if (!resume) {
      await rf(['issue', 'attach', issueId, sessionId]);
      const brief = await rf(['issue', 'brief', issueId], root);
      prompt = investigating(found, brief.ok ? brief.stdout.trim() : '', drafts, !!last);
    }
  } else if (tester) {
    // 测试会话：在这个需求的工作目录里，接着它最后一个测试会话；第一次打开时带上开场白（身份、归类、测试集或检查步骤、不改产品代码、交回结论）
    if (!req || req.state !== 'open' || !req.worktree) return send('term:data', { id, data: uiText('\r\n这个需求不在开发中，没有测试会话可开。\r\n', "\r\nThis requirement has no active test session.\r\n") });
    const tests = req.test_sessions || [];
    const last = tests[tests.length - 1];
    root = req.worktree; resume = !fresh && last && findTranscript(last);
    if (resume) useSession(last);
    else {
      // 测试会话新开时用这个需求单独选的测试 CLI 和模型；没单独选（含老需求）就跟着开发会话的（开发会话也没存的沿用应用设置）。
      // 只在新开时看这个选择：接着聊的测试会话用它开始时的，不受后来的修改影响
      const choice = req.session_choices?.testing || req.session_choices?.development;
      if (choice) { agent = choice.cli; model = choice.model; }
    }
    sessionId = resume ? last : crypto.randomUUID();
    title = title || uiText("「{0}」的测试", "Test session for “{0}”", req.title);
    if (!resume) {
      const tag = (await rf(['req', 'attach', req.id, sessionId, '--test'])).stdout.trim();
      prompt = testingPrompt(req, tag, !!last);
      previous = last;
    }
  } else if (id.startsWith('fix:')) {
    // 问题的修复会话：在修复自己的工作目录里，接着它最后一个会话；第一次打开时带上开场白（要修哪几条、现象、对话、功能点、规矩）
    const fixId = id.slice(4);
    const found = (await require('./issues').listIssues()).issues.filter((i) => i.fix && i.fix.id === fixId);
    const fix = found.length ? found[0].fix : null;
    if (!fix || fix.state !== 'fixing' || !fs.existsSync(fix.worktree)) return unavailable(uiText('这个修复已经结束，工作目录收掉了。', "This fix is complete and its worktree has closed."));
    const last = fix.sessions[fix.sessions.length - 1];
    root = fix.worktree; resume = !fresh && last && findTranscript(last);
    if (resume) useSession(last);
    else if (fix.session_choice) {
      agent = fix.session_choice.cli;
      model = fix.session_choice.model;
    }
    sessionId = resume ? last : crypto.randomUUID();
    if (!resume) {
      await rf(['issue', 'attach', fixId, sessionId]);
      const brief = await rf(['issue', 'brief', fixId], root);
      prompt = fixing(fix, found, brief.ok ? brief.stdout.trim() : '', !!last);
      previous = last;
    }
  } else {
    if (!req || !req.worktree) return unavailable(uiText('这个需求没有自己的工作目录，打不开。', "This requirement has no worktree to open."));
    const own = req.dev_sessions || req.sessions; // 测试会话不算：开发会话接着它自己的
    const last = own[own.length - 1];
    // 已经立项、最后一个会话还是立项会话：不接着它聊，另开一个开发会话
    const starting = req.state === 'open' && !!last && req.intake.includes(last);
    root = req.worktree; resume = !fresh && !starting && last && findTranscript(last);
    if (resume) useSession(last);
    else {
      const choice = req.session_choices?.[drafting ? 'intake' : 'development'];
      if (choice) { agent = choice.cli; model = choice.model; }
    }
    sessionId = resume ? last : crypto.randomUUID();
    const tag = resume ? '' : (await rf(['req', 'attach', id, sessionId])).stdout.trim();
    prompt = resume ? '' : drafting ? intake(req, tag, pics, own.length > 0, drafts) : starting || !own.length ? develop(req, tag) : following(req, tag);
    if (!resume && !drafting && !starting) previous = last;
  }
  // 另开一个会话接替同一件事（开发、修复、测试的后续会话）：把上一个会话最后几轮的步骤放进开场白，让它知道上次做到哪、卡在哪
  if (prompt && previous) prompt += await recapOf(previous);
  if (say) prompt = prompt ? prompt + '\n\n' + say : say;
  const queued = require('./inbox').takeForOpen(id, sessionId); // 它没开着时程序排给它的话，随开场白一起交过去
  if (queued) prompt = prompt ? prompt + '\n\n' + queued : queued;
  if (headless && !prompt) return; // 无界面运行只在有事要它做时开
  const command = settings.command(agent);
  if (agent === 'claude') { // 沙箱确认会生效才开（sandbox-check.js）：没生效时 RelayForge 挡不住会话冒充你、读保密值，界面上又看不出来
    const sandboxed = await require('./sandbox-check').check(command[0], root || state.project);
    if (projectRoot !== state.project) return;
    if (!sandboxed.ok) return unavailable(sandboxed.reason.replace(/\n/g, '\r\n'));
  }
  const env = cleanEnv();
  const localArgs = agent === 'codex' && !headless ? await codexLocalArgs(command, resume, root, env) : [];
  if ((await listReqs()).some((r) => r.state === 'dropped' && (r.id === id || (id.startsWith('session:') && r.sessions.includes(id.slice(8)))))) return;
  if (projectRoot !== state.project) return;
  // 需求和修复的工作目录：给它装上提交钩子（以前开的目录也补上），这里的每个提交由 git 自己带上需求或修复、会话的出处。失败不影响开会话
  if (root && root !== state.project && !probing && id !== MAP_EDIT && !id.startsWith('session:')) { try { await rf(['trailers', 'install'], root); } catch (_) { /* 没装上就没有这些行，不碍事 */ } }
  if (projectRoot !== state.project) return;
  // 会话拆分需求（splits.js）：立项、开发会话和日常聊天；测试会话只测这个需求，不提拆分
  const splitEnabled = !!(req && !tester && ['draft', 'open'].includes(req.state)) ||
    (id.startsWith('session:') && !initialization.forSession(sessionId));
  // 立项、排查的 Codex 会话（不许改项目）：Codex 的只读沙箱连应用的 socket 都连不上，记录、设计稿交不给应用写进需求；
  // 只读模式又没有联网的设置。所以改成在它自己的草稿目录里用可写沙箱加联网：项目照样读得到，能写的只有草稿目录（实测过）。
  // rf.py 按 CLAUDE_PROJECT_DIR 找项目（core/common.py 的 find_root），会话的说明里告诉它项目在哪
  const inDrafts = agent === 'codex' && (drafting || probing) && drafts ? drafts : null;
  const subagents = req && !tester && !probing && !id.startsWith('session:') ? require('./subagents') : null;
  const language = settings.get().language || 'en';
  const subagentGuide = subagents && (drafting ? subagents.intakeGuide(language) : subagents.developmentGuide(
    subagents.view(projectRoot, req, agent), `python3 ${QUOTED}`));
  const replyLanguage = language === 'zh-CN'
    ? '使用简体中文撰写每条面向用户的回复、进度和结论。保留命令、代码、标识符及引用原文。'
    : 'Use English for every user-facing reply, progress update, and conclusion. Preserve commands, code, identifiers, and quotations.';
  const sessionGuide = [
    replyLanguage + ' Follow any newer RelayForge response-language setting supplied in a turn hook.',
    uiNames.guide(language), // 提示词是中文的：英文界面上引用按钮和标签时要用英文界面上的名字
    projectRules(state.project), INBOX_GUIDE, !tester && ISSUE_GUIDE, splitEnabled && splits.guide, drafting && INTAKE_ASSESSMENT_GUIDE, subagentGuide,
    inDrafts && `## 你在哪里\n\n你的当前目录是这件事专用的草稿目录（${inDrafts}），能写的只有这里：设计稿先写在这里，再用 rf.py design save --file 收进需求。` +
      `项目在 ${root}，只读，不要尝试修改；读项目文件用这个绝对路径。rf.py 的命令照常运行，会找到这个项目。`].filter(Boolean).join('\n\n');
  args.push(...command);
  if (model) args.push('--model', model);
  if (agent === 'claude') {
    // 开发会话和修复会话用得上测试集的写命令（rf.py test new / add / run …），只在它们里面预先放行
    const building = !drafting && !probing && !tester && (id.startsWith('fix:') || (!!req && req.state === 'open' && !!req.worktree));
    const role = tester ? 'test' : id.startsWith('fix:') ? 'fix' : building ? 'develop' : null;
    const testing = !!(state.testChats && state.testChats.has(sessionId)); // 测试集查看器「让 AI 改」开的聊天
    const envFill = !!(state.envChats && state.envChats.has(sessionId)); // 测试环境「让 AI 读项目，帮我填」开的聊天
    const envFix = !!(state.envFixChats && state.envFixChats.has(sessionId)); // 「让 AI 来处理」开的聊天
    args.push('--settings', hooks(drafts, probing, !!(state.mergeChats && state.mergeChats.has(sessionId)), building, testing, envFill, envFix, { role, place: tester ? root : null, split: splitEnabled, req: !!(req && !probing && !id.startsWith('session:') && ['draft', 'open'].includes(req.state)), headless }), ...((drafting || probing || tester || subagents) ? ['--disallowedTools',
      [drafting || probing || tester ? 'NotebookEdit' : '', subagents ? 'Agent,Task' : ''].filter(Boolean).join(',')] : []));
    if (sessionGuide) args.push('--append-system-prompt', sessionGuide);
    args.push(...(resume ? ['--resume', sessionId] : ['--session-id', sessionId]));
    if (prompt) args.push(prompt);
  } else {
    if (resume) args.push('resume', codex.nativeId(sessionId));
    args.push(...localArgs);
    if (resume && prompt) args.push(prompt);
    args.push('--sandbox', drafting || probing ? (inDrafts ? 'workspace-write' : 'read-only') : 'workspace-write');
    args.push(...codexSandboxFlags((drafting || probing) && !inDrafts));
    if (subagents) args.push('--disable', 'multi_agent', '--disable', 'multi_agent_v2');
    args.push(...codexHooks.flags()); // 和 Claude Code 一样的钩子（codex-hooks.js）；这个会话不能改哪些地方放在环境变量里
    if (sessionGuide) args.push('-c', 'developer_instructions=' + JSON.stringify(sessionGuide));
    if (!resume) {
      codex.register(sessionId, inDrafts || root, title || (req && req.title), state.project, id.startsWith('session:'));
      for (const image of pics) args.push('--image', image);
      args.push((prompt || uiText('这是一个日常聊天会话，请等待我的指示。', "This is a general chat. Wait for my instructions.")) + '\nRelayForge 会话标识：' + sessionId + uiText('\n用 rf.py 记录进展时，当前工具是 Codex。', "\nWhen recording progress with rf.py, report Codex as the current tool."));
    }
  }
  // 会话的身份（session-token.js）：种类和属于谁由这里定，签了名放进会话的环境；rf.py 只认验过签名的，不认裸的会话编号
  const who = identity({ id, req, tester, probing, drafting });
  const sig = sessionToken.forSession({ session: sessionId, ...who });
  const run = { id, title, root, sessionId, agent, transcript: resume || null, seen: 0, syncedAt: 0, syncing: false, note: '', kind: who.kind, owner: who.owner,
    splitEnabled, splitParent: splitEnabled && req ? req.id : null };
  if (resume) { try { run.seen = fs.statSync(resume).size; } catch (_) { /* 当作没读过 */ } }
  const runEnv = { ...env, RELAYFORGE_LANGUAGE: language, ...require('./inbox').envFor(state.project), ...(agent === 'codex' ? codexHooks.env(guardSpec({ drafts, probe: probing, tester, place: tester ? root : null })) : {}), RELAYFORGE_EVENTS: EVENTS, RELAYFORGE_SESSION_ID: sessionId, [sessionToken.SIG_ENV]: sig,
    ...(inDrafts ? { CLAUDE_PROJECT_DIR: root } : {}) };
  const cwd = inDrafts || root; // 进程在哪个目录里跑；记录、代写、收拾进程照旧按 root（项目）
  if (headless) {
    const cli = args.slice(1); // 去掉 pty_bridge.py
    const [file, ...configured] = command;
    const approval = settings.get().headlessApproval;
    const readOnly = (drafting || probing) && !inDrafts;
    const sandbox = readOnly ? 'read-only' : 'workspace-write';
    const codexRun = agent === 'codex' ? {
      threadId: resume ? codex.nativeId(sessionId) : null, model, sandbox, approvalPolicy: codexApprovalPolicy(approval),
      prompt: resume ? prompt : cli.at(-1), images: resume ? [] : pics,
    } : null;
    if (resume && codexRun && !codexRun.threadId) {
      return unavailable(uiText('找不到要恢复的 Codex 会话编号。请检查原会话的聊天记录。', 'Could not find the Codex thread ID. Check the original transcript.'));
    }
    const hargs = agent === 'claude'
      ? ['-p', '--output-format', 'stream-json', '--verbose', ...approvalArgs('claude', approval), ...cli.slice(1)]
      : [...configured, 'app-server', '--listen', 'stdio://', '-c', `sandbox_mode="${sandbox}"`, ...codexSandboxFlags(readOnly),
        ...approvalArgs('codex', approval), ...codexHooks.flags(),
        ...(subagents ? ['-c', 'features.multi_agent=false', '-c', 'features.multi_agent_v2=false'] : []),
        ...(sessionGuide ? ['-c', 'developer_instructions=' + JSON.stringify(sessionGuide)] : [])];
    // 和交互会话一样登记、收拾这个会话起的进程（session-procs.js）。
    require('./headless').start(run, file, hargs, { cwd, env: runEnv }, sig, { ended: () => reapLater(run, false), codex: codexRun });
    if (realChild(run.term)) require('./session-procs').record(sessionId, { root, pid: run.term.pid });
    runs.set(id, run);
    attention.setPhase(id, 'running');
    pushState(run);
    pushReqs();
    return;
  }
  run.term = spawn(PYTHON, args, {
    cwd,
    env: { ...runEnv, TERM: 'xterm-256color', COLORTERM: 'truecolor', COLUMNS: String(cols), LINES: String(rows), RELAYFORGE_RESIZE_FD: '3' },
    stdio: ['pipe', 'pipe', 'pipe', 'pipe'],
  });
  sessionToken.bind(sig, run.term.pid, root); // 令牌只在这个会话进程和它下面的进程里算数
  for (const stream of [run.term.stdin, run.term.stdio[3]]) stream.on('error', () => {});
  run.term.stdout.on('data', (data) => send('term:data', { id, data }));
  run.term.stderr.on('data', (data) => send('term:data', { id, data }));
  function exited(code, reason) {
    sessionToken.unbind(sig);
    if (runs.get(id) !== run) return;
    clearTimeout(run.idleTimer);
    runs.delete(id);
    attention.stopped(id);
    attention.setPhase(id, 'exited');
    try { require('./monitor').ended(run); } catch (_) { /* 后台记录出错不影响会话 */ }
    send('term:exit', { id, code });
    attention.mergeProgress(id, 'exit', reason);
  }
  run.term.on('error', (error) => {
    send('term:data', { id, data: uiText('\r\n启动失败：', "\r\nSession couldn't start: ") + error.message + '\r\n' });
    exited(127, uiText('AI 会话启动失败。', "Couldn't start the AI session."));
  });
  run.term.on('exit', (code) => { exited(code, uiText('AI 会话已退出。', "The AI session exited.")); reapLater(run, false); }); // 会话自己退出（/exit）时留下的也收拾
  if (realChild(run.term)) require('./session-procs').record(sessionId, { root, pid: run.term.pid }); // 应用崩溃了，下次启动时照样收拾得到（session-procs.js 的 sweep）
  runs.set(id, run);
  // 会话现在在干什么（收件箱据此决定能不能往里送话）：带着开场白的马上就在干活；没带的等 CLI 把界面起来再算停着
  attention.setPhase(id, prompt ? 'running' : 'starting');
  if (!prompt) later(() => { if (runs.get(id) === run && attention.phaseOf(id) === 'starting') attention.setPhase(id, 'idle'); }, STARTUP_MS);
  send('split:view', { id, value: null });
  pushState(run);
  pushReqs();
}

// 会话的种类和属于谁（写进签名的会话令牌）：大图编辑、日常聊天（以前聊过的会话、已完成需求的会话、测试集和测试环境开的聊天）、
// 问题的排查、需求的测试、问题的修复、立项中的需求的立项会话、开发中的需求的开发会话
function identity({ id, req, tester, probing, drafting }) {
  if (id === MAP_EDIT) return { kind: 'map', owner: null };
  if (id.startsWith('session:')) return { kind: 'chat', owner: null };
  if (id.startsWith('check:')) return { kind: 'check', owner: id.slice(6) };
  if (probing) return { kind: 'probe', owner: id.slice(6) };
  if (tester) return { kind: 'test', owner: req ? req.id : null };
  if (id.startsWith('fix:')) return { kind: 'fix', owner: id.slice(4) };
  if (!req || (req.state === 'done' && !req.worktree)) return { kind: 'chat', owner: null };
  return { kind: drafting ? 'intake' : 'develop', owner: req.id };
}

// 关会话：先让 pty_bridge.py 给 CLI 发挂断，让它把聊天记录写完；过一会儿再把这个会话起的进程全部收拾掉（session-procs.js：
// Bash 工具在自己的会话里收不到挂断，nohup、& 起的服务早就不是它的后代了）。退出应用时等不到定时器，就地等一会儿再收拾
const REAP_GRACE_MS = 3000;
// 测试里的假终端不收拾：它的 pid 是编的（测试换掉的 child_process 里也没有 ChildProcess）
const realChild = (term) => { const CP = require('child_process').ChildProcess; return typeof CP === 'function' && term instanceof CP; };
const reopened = (sid) => [...runs.values()].some((r) => r.sessionId === sid); // 同一个会话又开起来了：新的那些进程不能收
// 收拾时要的：工作目录、还活着的 pty_bridge.py、别的开着的会话是不是也在这个目录（session-procs.js 的 reap）
const where = (run, closing = []) => ({ root: run.root, pid: run.term.exitCode === null && run.term.signalCode === null ? run.term.pid : null,
  shared: [...runs.values()].some((r) => r !== run && r.root === run.root && !closing.includes(r)) });
function reapLater(run, now) {
  if (!realChild(run.term)) return;
  const reap = () => { if (reopened(run.sessionId)) return; const sp = require('./session-procs'); sp.reap(run.sessionId, where(run)); sp.unrecord(run.sessionId); };
  if (now) { Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 1500); reap(); } else later(reap, REAP_GRACE_MS);
}
function stop(run, quitting = false) {
  clearTimeout(run.idleTimer);
  try { require('./monitor').ended(run); } catch (_) { /* 后台记录出错不影响关会话 */ }
  attention.seen(run.id);
  attention.stopped(run.id);
  runs.delete(run.id);
  attention.forgetPhase(run.id);
  const closed = run.stopHeadless ? run.stopHeadless() : run.term.kill();
  reapLater(run, quitting);
  return closed;
}
// 关掉这个编号开着的会话；关了返回 true
function close(id) { const run = runs.get(id); if (run) stop(run); return !!run; }
// quitting：应用要退出了，就地收拾（定时器等不到）；一次等完，不是每个会话各等一回
function stopAll(quitting = false) {
  const all = [...runs.values()];
  for (const run of all) stop(run, false);
  if (quitting && all.some((r) => realChild(r.term))) {
    Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 1500);
    const sp = require('./session-procs');
    for (const run of all) if (realChild(run.term)) { sp.reap(run.sessionId, where(run, all)); sp.unrecord(run.sessionId); }
  }
}

// 替你往会话里输一句话并回车：和在聊天框里粘贴一样，用括号粘贴，多行也不会提前发出去
function type(run, text) {
  run.term.stdin.write('\x1b[200~' + text + '\x1b[201~');
  setTimeout(() => { if (runs.get(run.id) === run) run.term.stdin.write('\r'); }, 300);
}

// 窗口里的终端打了字、改了大小
function input(id, data) {
  const run = runs.get(id);
  if (run && run.headless) return require('./headless').typedInto(run); // 后台跑着：不收键盘，告诉你怎么接管
  if (run) { run.term.stdin.write(data); attention.seen(id); }
}
function resize(id, cols, rows) { const run = runs.get(id); if (run && !run.headless) run.term.stdio[3].write(`${cols} ${rows}\n`); }
function syncNow(id) { const run = runs.get(id); if (run) sync(run, uiText('正在整理进展…', "Syncing progress…")); }

module.exports = { approvalArgs, codexSandboxFlags, MAP_EDIT, identity, findTranscript, pushState, sync, open, stop, close, stopAll, input, resize, syncNow, type };

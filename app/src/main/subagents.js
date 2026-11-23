// 委派入口只接受应用签名的开发会话。CLI、目录、参数和沙箱由应用决定。
const path = require('node:path');
const { randomUUID } = require('node:crypto');
const { spawn } = require('node:child_process');
const { rf, cleanEnv, lastLine } = require('./backend');
const { state } = require('./state');
const settings = require('./settings');
const auth = require('./cli-auth');
const { labels, effective } = require('../renderer/scripts/subagent-model');
const { validModel } = require('../renderer/scripts/requirement-sessions');

const intakeGuide = (language) => language === 'en' ?
  'To authorize delegation, ask the user to reply “This requirement can use subagents.” in this planning chat. ' +
  'They can append “, use Codex”, “, use Claude Code”, or “, follow settings” before the final period. ' +
  'To revoke authorization, the user can reply “Revoke subagent authorization.”. ' +
  'The app uses only the user’s explicit replies in this planning chat.' :
  '如果用户希望这个需求使用 subagent，请让用户在立项对话里明确回复「这个需求可以用 subagent」。' +
  '也可以回复「这个需求可以用 subagent，请用 Codex」或「这个需求可以用 subagent，请用 Claude Code」。' +
  '撤回请回复「撤回 subagent 授权」。应用只以用户在立项对话中的明确回复为准。';

function developmentGuide(record, command) {
  if (!record?.authorized) return '当前需求没有获得立项对话中的 subagent 授权。请由本会话自行完成开发工作。';
  return '当前需求已由用户在立项对话授权使用 subagent。可以自行决定何时委派。' +
    `用 ${command} subagent status 查看当前生效的 CLI、首选模型和可用模型。` +
    `用 ${command} subagent run "独立的子任务说明" 委派并取得结果。` +
    '根据子任务复杂度选模型。用户指定的模型合适时可以优先选用。选好后传 --model 名称 --reason "选择依据"，' +
    '并向用户说明依据。未传模型时使用合适的首选模型或 CLI 的默认模型。' +
    '一律通过这个入口委派。遇到所选 CLI 不可用时，把原因告诉用户，等用户处理或明确改选，之后重试同一任务。';
}

function projectKey(root) {
  return path.resolve(root);
}

function view(project, req, development = req.session_choices?.development?.cli || settings.get().agent) {
  const own = settings.requirementSubagent(projectKey(project), req.id);
  const defaults = settings.get().subagent;
  return { authorized: own.authorized === true, own: own.choice || {}, defaults, development,
    effective: effective(defaults, own.choice, development) };
}

const scans = new Map();
async function readIntake(project, req, findTranscript) {
  const key = projectKey(project);
  const entry = key + '\n' + req.id;
  const previous = scans.get(entry);
  const scan = Promise.resolve(previous).catch(() => {}).then(async () => {
    let changed = false;
    for (const [index, session] of req.intake.entries()) {
      const file = findTranscript(session);
      if (!file) continue;
      const result = await rf(['subagent', 'decision', file], project, undefined, 15000);
      if (!result.ok) throw new Error(lastLine(result.stderr));
      const decisions = JSON.parse(result.stdout);
      if (!decisions.length) continue;
      settings.updateRequirementSubagent(key, req.id, (stored) => {
        let updated = stored;
        for (const decision of decisions) {
          const cursor = updated.cursor;
          if (cursor && (index < cursor.index || (cursor.session === session && decision.number <= cursor.number))) continue;
          changed = true;
          updated = { ...updated, authorized: decision.authorized, cursor: { index, session, number: decision.number } };
          if (decision.cli !== undefined) {
            updated.choice = decision.cli ? { cli: decision.cli, model: null, source: '立项对话' } : {};
          }
        }
        return updated;
      });
    }
    return changed;
  });
  scans.set(entry, scan);
  try { return await scan; } finally { if (scans.get(entry) === scan) scans.delete(entry); }
}

async function checkCli(cli) {
  const status = await auth.status(cli);
  if (status.state === 'in') return;
  const actions = { missing: '尚未安装', out: status.note || '尚未登录', waiting: '正在等待登录授权', desktop: '由桌面应用提供，无法确认 CLI 已登录' };
  throw new Error(`Subagent 委派已暂停：${labels[cli]} ${actions[status.state] || '当前不可用'}。处理后可以重试，也可以为本需求明确改选 CLI。`);
}

async function validateDefault(input) {
  if (!input) return;
  const choice = settings.subagentChoice(input, ['follow', 'claude', 'codex']);
  if (choice.cli === 'follow') return;
  await checkCli(choice.cli);
  if (choice.model && !settings.catalogs()[choice.cli]?.some(([id]) => id === choice.model)) {
    throw new Error('请选择当前 CLI 可用的模型。');
  }
}

async function saveChoice(project, req, input) {
  const current = view(project, req);
  const cli = input?.cli || effective(current.defaults, {}, current.development).cli;
  const choice = settings.subagentChoice(input, ['', 'claude', 'codex'], cli);
  const target = effective(current.defaults, choice, current.development).cli;
  const model = choice.model;
  await checkCli(target);
  if (model && !settings.catalogs()[target]?.some(([id]) => id === model)) throw new Error('请选择当前 CLI 可用的模型。');
  const inherit = !choice.cli && choice.model == null;
  settings.updateRequirementSubagent(projectKey(project), req.id, (saved) => ({
    ...saved, choice: inherit ? {} : { ...choice, source: '右侧设置' },
  }));
  return view(project, req);
}

const MAX_TASK = 16000;
const MAX_OUTPUT = 8000;
const TIMEOUT = 600000;
const pending = new Map();

function command(cli, file, model, place) {
  const selected = model ? ['--model', model] : [];
  if (cli === 'codex') {
    return [file, ['--ask-for-approval', 'never', 'exec', '--json', '--ephemeral', '--ignore-user-config', '--ignore-rules',
      '--sandbox', 'workspace-write', '-c', 'sandbox_workspace_write.network_access=false', '-c', 'approvals_reviewer="user"',
      '--disable', 'multi_agent', '--disable', 'multi_agent_v2', '--disable', 'hooks', ...selected, '-']];
  }
  const { hooks, sandbox } = require('./claude-settings');
  const { permissions } = JSON.parse(hooks(null, false, false, false, false, false, false));
  permissions.allow = ['Bash'];
  permissions.deny.push('Edit', 'Write', 'NotebookEdit', 'Agent', 'Task');
  const scope = sandbox(place);
  scope.filesystem.allowWrite = [place];
  scope.filesystem.denyWrite.push(path.join(place, '.relayforge'));
  scope.failIfUnavailable = true;
  scope.network = { allowUnixSockets: [], allowLocalBinding: false };
  return [file, ['--print', '--output-format', 'json', '--no-session-persistence', '--setting-sources', '', '--strict-mcp-config',
    '--permission-mode', 'default', '--settings', JSON.stringify({ permissions, sandbox: scope }),
    '--tools', 'Read,Grep,Glob,Bash', ...selected]];
}

function resultOf(cli, stdout) {
  if (cli === 'claude') {
    const output = JSON.parse(stdout);
    return output.is_error ? null : output.result;
  }
  let result = null;
  for (const line of stdout.trim().split('\n')) {
    const event = JSON.parse(line);
    if (event.type === 'item.completed' && event.item?.type === 'agent_message') result = event.item.text;
    if (event.type === 'turn.failed' || event.type === 'error') return null;
  }
  return result;
}

function execute(cli, file, model, place, task, signal) {
  const [binary, args] = command(cli, file, model, place);
  const env = cleanEnv();
  for (const name of Object.keys(env)) if (name.startsWith('RELAYFORGE_')) delete env[name];
  const worker = randomUUID();
  env.RELAYFORGE_SESSION_ID = worker;
  const child = spawn(binary, args, { cwd: place, env, stdio: ['pipe', 'pipe', 'pipe'] });
  const processes = require('./session-procs');
  if (child.pid) processes.record(worker, { root: place, pid: child.pid });
  return new Promise((resolve, reject) => {
    let stdout = '', stderr = '', interrupted;
    function stop(reason) {
      if (interrupted) return;
      interrupted = reason;
      if (child.pid) require('./proc-tree').killTree(child.pid);
    }
    const cancel = () => stop('开发会话已关闭或取消，subagent 已停止。');
    const timer = setTimeout(() => stop('子任务超过 10 分钟，已停止。'), TIMEOUT);
    signal.addEventListener('abort', cancel, { once: true });
    if (signal.aborted) cancel();
    child.stdout.setEncoding('utf8').on('data', (chunk) => {
      if (stdout.length + chunk.length > (4 << 20)) stop('子任务输出过长，请缩小委派范围。');
      else stdout += chunk;
    });
    child.stderr.setEncoding('utf8').on('data', (chunk) => { stderr = (stderr + chunk).slice(-4000); });
    child.once('error', (error) => { interrupted = '启动失败：' + error.message; });
    child.once('close', (code) => {
      clearTimeout(timer);
      signal.removeEventListener('abort', cancel);
      if (child.pid) {
        processes.reap(worker, { shared: true });
        processes.unrecord(worker);
      }
      if (interrupted) return reject(new Error(interrupted));
      let result;
      try { if (code === 0) result = resultOf(cli, stdout); } catch (_) { /* CLI 没有完成标准输出 */ }
      if (typeof result === 'string' && result.trim()) {
        return resolve(result.length > MAX_OUTPUT ? result.slice(0, MAX_OUTPUT) + '\n（结果过长，已截断）' : result);
      }
      reject(new Error(stderr.trim() ? lastLine(stderr) : 'CLI 没有返回任务结果'));
    });
    child.stdin.on('error', () => {});
    child.stdin.end('完成下面的独立开发子任务。只在当前工作目录工作，不提交、不发布、不再委派。返回结果和改动的文件。\n\n' + task);
  });
}

async function delegate({ claims, place, action, request, signal }) {
  const project = state.project;
  const run = state.runs.get(claims.owner);
  if (claims.kind !== 'develop' || !run || run.sessionId !== claims.sid || run.root !== place) {
    throw new Error('只能在当前需求的开发会话中委派 subagent。');
  }
  const req = (await require('./requirements').listReqs(project)).find((r) => r.id === claims.owner);
  if (signal.aborted || state.project !== project || req?.state !== 'open' || req.worktree !== place) {
    throw new Error('需求的开发会话已经关闭或切换，请重新打开后重试。');
  }
  const configuration = () => view(project, req, run.agent);
  const current = configuration();
  if (!current.authorized) throw new Error('本需求没有立项对话中的 subagent 授权。');
  const { cli, model: preferredModel } = current.effective;
  const { task, model, reason } = request;
  if (action === 'run' && (typeof task !== 'string' || !task.trim() || task.length > MAX_TASK)) {
    throw new Error('请提供 1 到 16000 字符的子任务说明。');
  }
  if (action === 'run') {
    const validReason = typeof reason === 'string' && reason.trim() && reason.length <= 500;
    if ((reason != null && !validReason) || (model != null && (!validModel(cli, model) || !validReason))) {
      throw new Error('指定模型时，请同时提供有效的模型名称和 500 字以内的选择依据。');
    }
  }
  await checkCli(cli);
  if (action === 'status') {
    let models = settings.catalogs()[cli];
    if (!models) {
      try { models = (await require('./cli-tools').refresh(cli)).catalogs[cli]; } catch (_) { /* 可按 CLI 默认模型委派 */ }
    }
    return { cli, preferredModel, models: (models || []).slice(0, 200) };
  }
  if (model && !settings.catalogs()[cli]?.some(([id]) => id === model) && model !== preferredModel) {
    throw new Error('所选模型不在当前 CLI 的模型列表中，请先运行 subagent status。');
  }
  const file = settings.launch(cli).file;
  if (cli === 'claude') {
    const check = await require('./sandbox-check').check(file, place);
    if (!check.ok) throw new Error('Subagent 委派已暂停：' + check.reason);
  }
  if (signal.aborted || state.runs.get(run.id) !== run || JSON.stringify(configuration()) !== JSON.stringify(current)) {
    throw new Error('开发会话或 subagent 配置已变化，请重新检查后重试。');
  }
  const count = pending.get(run) || 0;
  if (count >= 4) throw new Error('已有 4 个子任务在运行，请等待其中一个完成。');
  pending.set(run, count + 1);
  const selected = model || preferredModel || '';
  try {
    const result = await execute(cli, file, selected, place, task, signal);
    return { cli, model: selected, reason: reason || (selected ? '按首选模型执行' : '按 CLI 默认模型执行'), result };
  } catch (error) {
    if (!signal.aborted) await checkCli(cli);
    throw new Error(labels[cli] + ' 子任务没有完成：' + error.message);
  } finally {
    const remaining = pending.get(run) - 1;
    if (remaining) pending.set(run, remaining);
    else pending.delete(run);
  }
}

module.exports = { intakeGuide, developmentGuide, view, readIntake, validateDefault, saveChoice, delegate, command, resultOf };

// 测试环境：测试页的「测试环境」、各处的「测试环境」选择框。具体的事都由 rf.py env 做，这里只是调用它、把结果交给界面。
// 界面编辑时要看到保密的值（密码框可以点一下显示），所以「查看」用的是只给应用的 env reveal；会话里只能用打码的 env show（见 claude-settings.js）。
// 「让 AI 读项目，帮我填」新开一个日常聊天（像发布里的「让 AI 来合并」），它用 env propose 把建议交回，界面显示成「AI 的建议」，你逐条采纳或忽略。
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const { uiText = (zh) => zh, rf, lastLine, parse } = require('./backend');
const { state, send } = require('./state');
const { envFilling, envHandling } = require('./prompts');
const Model = require('../renderer/scripts/tests-model');

const TIMEOUTS = { read: 30000, check: 60000, act: 60000 };
const ID = /^ENV-\d{3,}$/i;
const UID = /^U\d+$/;
const STEP = /^P\d+$/i;
const SET = /^TS-\d+$/i;
const OWNER = /^(R-[\w-]+|F-\d+)$/;

async function call(args, timeout = TIMEOUTS.read, input) {
  const out = await rf(['env', ...args, '--json'], undefined, undefined, timeout, input);
  const made = out.ok ? parse(out.stdout, null) : null;
  return made || { error: out.timedOut ? uiText('测试环境等了太久，已经停下。', "The test environment timed out and was stopped.") : lastLine(out.stderr) };
}

// 列表：选择框和左边的环境列表用；local 是本机文件的位置（文件监视用）
async function list() {
  const made = await call(['list']);
  if (!made.error && made.local) localFile = made.local;
  return made;
}
// 编辑区：一套环境的全部设置，保密的值是明文（只交给界面，不给会话）
function show({ id } = {}) {
  if (id && !ID.test(String(id))) return Promise.resolve({ error: uiText('测试环境的编号不对。', "Invalid test environment ID.") });
  return call(['reveal', ...(id ? [String(id)] : [])]);
}
// 整份保存：保密的值由 rf.py 存到本机，仓库里的文件不留值
function save({ id, payload } = {}) {
  if (!ID.test(String(id || ''))) return Promise.resolve({ error: uiText('要给测试环境的编号。', "Provide a test environment ID.") });
  return call(['save', String(id), '--file', '-'], TIMEOUTS.act, JSON.stringify(payload || {}));
}
// 你点的按钮：新建、复制、删除、选用哪一套、选当前测试用户、采纳或忽略 AI 的建议
function act({ action, id, name, note, user, ks, steps, on, services } = {}) {
  const env = String(id || '');
  const items = (Array.isArray(ks) ? ks : ks != null ? [ks] : []).map(Number).filter((n) => n > 0).map(String);
  let args;
  if (action === 'new') args = ['new', String(name || '').trim(), ...(note ? ['--note', String(note)] : [])];
  else if (action === 'use') args = ['use', env && ID.test(env) ? env : 'none'];
  else if (!ID.test(env)) return Promise.resolve({ error: uiText('要给测试环境的编号。', "Provide a test environment ID.") });
  else if (action === 'copy') args = ['copy', env, ...(name ? ['--name', String(name)] : [])];
  else if (action === 'rm') args = ['rm', env];
  else if (action === 'user') { if (!UID.test(String(user || ''))) return Promise.resolve({ error: uiText('先保存这个测试用户。', "Save this test user first.") }); args = ['user', env, String(user)]; }
  else if (action === 'adopt' || action === 'ignore') args = [action, env, ...items];
  // 同意执行准备步骤、允许 AI 的会话带上保密值：rf.py 认应用的标记（app-token.js），只有从这里调用才算数
  // 人工验收的启动命令（services：服务名）也在这里同意
  else if (action === 'approve') args = ['approve', env, ...(Array.isArray(steps) ? steps : []).map(String).filter((x) => STEP.test(x)), ...(Array.isArray(services) ? services : []).flatMap((n) => ['--service', String(n)])];
  else if (action === 'ai-secrets') args = ['ai-secrets', env, on ? 'on' : 'off'];
  else return Promise.resolve({ error: uiText('不认识的操作。', "Unknown operation.") });
  return call(args, TIMEOUTS.act);
}
function scan({ id } = {}) { return ID.test(String(id || '')) ? call(['scan', String(id)]) : Promise.resolve({ error: uiText('要给测试环境的编号。', "Provide a test environment ID.") }); }
function check({ id } = {}) { return ID.test(String(id || '')) ? call(['check', String(id)], TIMEOUTS.check) : Promise.resolve({ error: uiText('要给测试环境的编号。', "Provide a test environment ID.") }); }
// 预览：按已保存的设置，这次请求会带上哪些请求头和地址参数（保密的打码）
function resolve({ id, user } = {}) {
  if (!ID.test(String(id || ''))) return Promise.resolve({ error: uiText('要给测试环境的编号。', "Provide a test environment ID.") });
  return call(['resolve', String(id), ...(UID.test(String(user || '')) ? ['--user', String(user)] : [])]);
}

// 手动执行一遍准备步骤（全部，或几条）：只执行你同意过的；失败时 rf.py 退出码是 1，结果照样在标准输出里
async function prepRun({ id, steps } = {}) {
  if (!ID.test(String(id || ''))) return { error: uiText('要给测试环境的编号。', "Provide a test environment ID.") };
  const out = await rf(['env', 'prep-run', String(id), ...(Array.isArray(steps) ? steps : []).map(String).filter((x) => STEP.test(x)), '--json'], undefined, undefined, 60 * 60 * 1000);
  const made = parse(out.stdout, null);
  return made || { error: out.timedOut ? uiText('准备步骤等了太久，已经停下。', "Preparation timed out and was stopped.") : lastLine(out.stderr) };
}

// 「让 AI 来处理」：测试集跑不起来（kind set，ref 测试集编号）、准备步骤失败（prep，ref 环境、step 步骤）、依赖连不上（check，ref 环境）。
// 交过去的话由 rf.py env handoff 写（错误、输出尾部、环境的名字和不保密的设置、能做什么、怎么交回；没有任何保密值），
// 这里只决定交给谁：进行中的需求（或修复）交给它的开发会话；否则新开一个日常聊天（由界面开，像「让 AI 改」），这个聊天按「处理环境」放行（claude-settings.js 的 envFix）
// 人工验收的服务启动失败（start，ref 环境、service 服务名、req 需求编号或空的是主线）：启动的情况（命令、目录、退出码、日志尾部，已打码）由 services.js 给
async function aiHandle({ kind, ref, step, req, fix, service } = {}) {
  if (!['set', 'prep', 'check', 'start'].includes(kind)) return { error: uiText('不认识要处理什么。', "Unknown action.") };
  if (kind === 'set' ? !SET.test(String(ref || '')) : !ID.test(String(ref || ''))) return { error: uiText('要说处理哪个。', "Select what to investigate.") };
  const scope = fix && OWNER.test(fix) ? ['--fix', String(fix)] : req && OWNER.test(req) ? ['--req', String(req)] : [];
  let more = [], input;
  if (kind === 'start') {
    const report = require('./services').report(req && OWNER.test(req) ? String(req) : 'main', String(service || ''));
    if (!report) return { error: uiText('没有找到这个服务的启动情况。', "No startup details found for this service.") };
    more = ['--service', String(service), ...scope, '--file', '-'];
    input = JSON.stringify(report);
  }
  const made = await call(['handoff', kind, String(ref), ...(kind === 'prep' && STEP.test(String(step || '')) ? ['--step', String(step)] : []), ...(kind === 'set' ? scope : []), ...more], TIMEOUTS.read, input);
  if (made.error) return made;
  const say = envHandling(made);
  let target = { kind: 'chat' };
  if (kind === 'set') {
    const set = await require('./testsets').show({ id: ref, req, fix });
    if (!set.error) target = Model.aiTarget(set, { req, fix }, await require('./requirements').listReqs());
  } else if (fix && OWNER.test(fix)) target = { kind: 'fix', run: 'fix:' + fix, title: uiText('修复 ', "Fix session for ") + fix + uiText(' 的会话', "") };
  else if (req && OWNER.test(req)) {
    const r = (await require('./requirements').listReqs()).find((x) => x.id === req);
    if (r && r.state === 'open' && r.worktree) target = { kind: 'req', run: r.id, title: '「' + r.title + uiText('」的开发会话', "” development session") };
  }
  if (target.kind !== 'chat') {
    require('./testsets').tell(target.run, say);
    return { ok: true, kind: target.kind, run: target.run, title: target.title, handoffTitle: made.title };
  }
  const sid = crypto.randomUUID();
  state.envFixChats.set(sid, { project: state.project, kind, ref: String(ref) });
  return { ok: true, kind: 'chat', run: 'session:' + sid, sessionId: sid, title: made.title, say };
}

// 「让 AI 读项目，帮我填」：新开的日常聊天由界面开，这里只给出会话编号和第一句话，并记下这个聊天可以用 rf.py env propose（claude-settings.js）。
// 交给它的只有名字（环境变量名、服务名、配置文件），没有任何值
async function aiFill({ id } = {}) {
  if (!ID.test(String(id || ''))) return { error: uiText('要给测试环境的编号。', "Provide a test environment ID.") };
  const env = await call(['show', String(id)]);
  if (env.error) return env;
  const sid = crypto.randomUUID();
  state.envChats.set(sid, { project: state.project, env: env.id });
  return { ok: true, sessionId: sid, title: uiText('帮我填测试环境「', "Configure test environment “") + env.name + '」', say: envFilling(env) };
}

// 文件监视（watcher.js）：.relayforge/envs/ 下的设置、本机的保密值和建议一变，就告诉界面重读
let stamp = null;
let localFile = null;
function envsStamp(root, local) {
  const dir = path.join(root, '.relayforge', 'envs');
  let names = [];
  try { names = fs.readdirSync(dir).sort(); } catch (_) { /* 还没有测试环境 */ }
  let latest = 0;
  for (const name of names) { try { latest = Math.max(latest, fs.statSync(path.join(dir, name)).mtimeMs); } catch (_) { /* 刚被删掉 */ } }
  let mine = 0;
  if (local) { try { mine = fs.statSync(local).mtimeMs; } catch (_) { /* 还没有本机文件 */ } }
  return names.join(',') + '|' + latest + '|' + mine; // 删掉一套时文件少了一个，也算变了
}
function watch() {
  if (!state.project) return;
  const now = state.project + ':' + envsStamp(state.project, localFile);
  if (stamp !== null && now !== stamp) send('envs:changed', {});
  stamp = now;
}

module.exports = { list, show, save, act, scan, check, resolve, aiFill, aiHandle, prepRun, watch, envsStamp };

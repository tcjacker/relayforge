// 应用级设置，独立于项目；保存失败时保留原值。
const fs = require('fs');
const { uiText } = require('./locale');
const path = require('path');
const { RF_HOME, AGENT } = require('./config');
const { locateCli } = require('./cli-paths');
const { defaults } = require('../renderer/scripts/subagent-model');
const { validModel } = require('../renderer/scripts/requirement-sessions');
const file = path.join(RF_HOME, 'settings.json');

function subagentChoice(value, allowedCli, inherited = false) {
  const { cli, model } = value || {};
  if (!allowedCli.includes(cli) || !(model === '' || (inherited && model === null) ||
      validModel(cli || inherited, model)) || (cli === 'follow' && model !== '')) {
    throw new Error('请选择有效的 subagent CLI 和模型。');
  }
  return { cli, model };
}

function requirementSubagent(project, id) {
  const saved = read();
  return saved.requirementSubagents?.[project]?.[id] || {};
}

function updateRequirementSubagent(project, id, update) {
  const saved = read();
  const all = saved.requirementSubagents || {};
  const entries = all[project] || {};
  const previous = entries[id] || {};
  const next = update(previous);
  if (next !== previous) write({ ...saved, requirementSubagents: { ...all, [project]: { ...entries, [id]: next } } });
  return next;
}
function read() {
  try { return JSON.parse(fs.readFileSync(file, 'utf8')); } catch (error) {
    if (error.code === 'ENOENT') return {};
    throw error;
  }
}
// 已取消需求的分支保留多久、最多保留几个（rf.py 读同一个文件，见 requirements/record.py 的 dropped_branch_limits）
const DROPPED = { keepDays: { fallback: 30, top: 3650, name: ['保留天数', 'Days to keep'] }, keepMax: { fallback: 20, top: 1000, name: ['最多保留的个数', 'Maximum branches to keep'] } };
function droppedBranches(saved) {
  const value = saved && typeof saved === 'object' ? saved : {};
  return Object.fromEntries(Object.entries(DROPPED).map(([key, d]) =>
    [key, Number.isInteger(value[key]) && value[key] >= 1 && value[key] <= d.top ? value[key] : d.fallback]));
}
// 无界面会话（headless.js）要批准时：auto 不问你，沙箱里的命令直接执行；ask 每次在应用里等你点。两种都不放行越出沙箱（sessions.js 的 approvalArgs）
const APPROVALS = ['auto', 'ask'];
const LANGUAGES = ['en', 'zh-CN'];
const languageOf = (saved) => LANGUAGES.includes(saved.language) ? saved.language : 'en';
function language() {
  try { return languageOf(read()); } catch (_) { return 'en'; }
}
function get() {
  const saved = read();
  return { subagent: saved.subagent || defaults, language: languageOf(saved), agent: ['claude', 'codex'].includes(saved.agent) ? saved.agent : AGENT[0] === 'codex' ? 'codex' : 'claude', monitor: saved.monitor === true,
    droppedBranches: droppedBranches(saved.droppedBranches), headlessApproval: APPROVALS.includes(saved.headlessApproval) ? saved.headlessApproval : 'auto' };
}
function write(data) {
  fs.mkdirSync(RF_HOME, { recursive: true });
  const temp = file + '.tmp';
  fs.writeFileSync(temp, JSON.stringify(data, null, 2) + '\n', { mode: 0o600 });
  fs.renameSync(temp, file);
}
function save(value) {
  if (!value || !['claude', 'codex'].includes(value.agent)) throw new Error(uiText('请选择 Claude Code 或 Codex。', "Select Claude Code or Codex."));
  if (value.language !== undefined && !LANGUAGES.includes(value.language)) {
    throw new Error(uiText('请选择界面语言。', "Select a display language."));
  }
  const saved = read();
  let dropped = saved.droppedBranches;
  if (value.droppedBranches !== undefined) {
    dropped = {};
    for (const [key, d] of Object.entries(DROPPED)) {
      const n = Number(value.droppedBranches && value.droppedBranches[key]);
      if (!Number.isInteger(n) || n < 1 || n > d.top) throw new Error(uiText('已取消需求的分支：', "Cancelled requirement branches: ") + uiText(...d.name) + uiText('要填 1 到 ', " must be an integer from 1 to ") + d.top + uiText(' 之间的整数。', "."));
      dropped[key] = n;
    }
  }
  if (value.headlessApproval !== undefined && !APPROVALS.includes(value.headlessApproval)) throw new Error(uiText('无界面会话的审批方式要选「自动」或「每次问我」。', "Select Automatic or Ask every time for background session approvals."));
  const subagent = value.subagent === undefined ? saved.subagent : subagentChoice(value.subagent, ['follow', 'claude', 'codex']);
  write({ ...saved, subagent, agent: value.agent, ...(value.language ? { language: value.language } : {}), ...(typeof value.monitor === 'boolean' ? { monitor: value.monitor } : {}), // monitor：后台记录（monitor.js），默认关
    ...(dropped ? { droppedBranches: dropped } : {}), ...(value.headlessApproval ? { headlessApproval: value.headlessApproval } : {}) });
  return get();
}
function launch(agent) {
  const [name, ...args] = AGENT[0] === agent ? AGENT : [agent];
  return { ...locateCli(agent, name), args };
}
function command(agent) {
  const cli = launch(agent);
  return [cli.file, ...cli.args];
}
function catalogs() {
  const saved = read();
  return Object.fromEntries(['claude', 'codex'].filter((agent) => saved.modelPaths?.[agent] &&
    saved.modelPaths[agent] === launch(agent).file && Array.isArray(saved.models?.[agent]))
    .map((agent) => [agent, saved.models[agent]]));
}
function saveModels(agent, models, file) {
  const saved = read();
  write({
    ...saved,
    models: { ...saved.models, [agent]: models },
    modelPaths: { ...saved.modelPaths, [agent]: file },
  });
}
module.exports = { subagentChoice, requirementSubagent, updateRequirementSubagent, get, save, language, launch, command, catalogs, saveModels };

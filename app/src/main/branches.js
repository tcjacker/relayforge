// Branch operations share the publish gate, so a checkout cannot move HEAD during publication.
const { uiText = (zh) => zh, rf, lastLine, parse } = require('./backend');
const { state, send } = require('./state');
const publish = require('./publish');

async function call(action, args, project, timeout) {
  if (!project) return { error: uiText('还没有打开项目。', "No project is open.") };
  const out = await rf(['branch', action, ...args, '--json'], project, undefined, timeout);
  return parse(out.stdout, null) || { error: out.timedOut ? uiText('Git 分支操作超时了。', "The Git branch operation timed out.") : lastLine(out.stderr) };
}

function list(project = state.project) {
  return call('list', [], project, 20000);
}

function refresh() {
  const project = state.project;
  return publish.exclusive(uiText('刷新远端分支', "Refresh remote branches"), async () => {
    const result = await call('refresh', [], project, 65000);
    if (!result.error && project === state.project) await publish.refresh(true);
    return result;
  });
}

function switchTo({ name, remote, localName, tracking, current, pending } = {}) {
  const project = state.project;
  if (typeof name !== 'string' || !name.trim()) return { error: uiText('要选一个分支。', "Select a branch.") };
  if (name.startsWith('-')) return { error: uiText('没有叫 ', "No local branch named ") + name + uiText(' 的本地分支。', "."), code: 'missing' };
  const args = [name];
  if (remote === true) args.push('--from-remote');
  if (typeof localName === 'string') args.push('--local-name=' + localName);
  if (typeof tracking === 'string') args.push('--expected-tracking=' + tracking);
  if (current === null || typeof current === 'string') args.push('--expected-current=' + (current || ''));
  if (Number.isSafeInteger(pending)) args.push('--confirmed-pending=' + pending);
  return publish.exclusive(uiText('切换分支', "Switch branch"), async () => {
    const result = await call('switch', args, project, 70000);
    if (!result.ok) return { error: result.error, code: result.code || 'failed', detail: result.detail || '' };
    if (project !== state.project) return result;
    await publish.refresh(true);
    if (result.changed) send('notice', uiText('项目主目录已切换到 ', "Main checkout switched to ") + result.to + '。' + (result.warning || ''));
    return { ...result, list: await list(project) };
  });
}

module.exports = { list, refresh, switchTo };

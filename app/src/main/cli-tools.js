const { execFile, spawn } = require('node:child_process');
const os = require('node:os');
const path = require('node:path');
const readline = require('node:readline');
const { promisify } = require('node:util');
const { uiText = (zh) => zh, cleanEnv } = require('./backend');
const settings = require('./settings');
const { validModel } = require('../renderer/scripts/requirement-sessions');
const { version: appVersion } = require('../../package.json');

const run = promisify(execFile);
const packages = { codex: '@openai/codex', claude: '@anthropic-ai/claude-code' };
const versionPattern = /\d+\.\d+\.\d+(?:-[\w.-]+)?/;
const home = os.homedir();
const pendingLists = new Map();
const updating = new Set();

function executable(agent) {
  if (!Object.hasOwn(packages, agent)) throw new Error(uiText('请选择 Claude Code 或 Codex。', "Select Claude Code or Codex."));
  return settings.launch(agent);
}

async function command(file, args, timeout = 15000) {
  try {
    const { stdout } = await run(file, args, { cwd: home, env: cleanEnv(), timeout, killSignal: 'SIGKILL' });
    return stdout;
  } catch (error) {
    if (error.code === 'ENOENT') throw new Error(uiText('找不到 ', "Couldn't find ") + file + uiText('，请检查应用的 CLI 安装。', ". Check the app's CLI installation."));
    if (error.killed) throw new Error(uiText('CLI 操作超时，请稍后重试。', "The CLI operation timed out. Try again shortly."));
    const detail = (error.stderr || error.stdout || '').trim().split('\n').at(-1);
    throw new Error(detail || uiText('CLI 操作失败，请检查安装权限。', "The CLI operation failed. Check installation permissions."));
  }
}

function versionFrom(text) {
  const value = text.match(versionPattern)?.[0];
  if (!value) throw new Error(uiText('无法读取 CLI 版本号。', "Couldn't read the CLI version."));
  return value;
}

async function publication(url) {
  let response;
  try { response = await fetch(url, { signal: AbortSignal.timeout(12000), headers: { 'User-Agent': 'RelayForge' } }); }
  catch (_) { throw new Error(uiText('连接版本发布源失败，请检查网络后重试。', "Couldn't reach the release server. Check your network and retry.")); }
  if (!response.ok) throw new Error(uiText('版本发布源查询失败（HTTP ', "The release server returned HTTP ") + response.status + '）。');
  return response.json();
}

function installation(agent, file, resolved) {
  const brew = resolved.match(/^(.*)\/(Caskroom|Cellar)\/(codex|claude-code(?:@latest)?)\//);
  if (brew) {
    const [, prefix, folder, name] = brew;
    const cask = folder === 'Caskroom';
    return {
      latest: async () => {
        const info = await publication('https://formulae.brew.sh/api/' + (cask ? 'cask' : 'formula') + '/' + name + '.json');
        return cask ? info.version : info.versions.stable;
      },
      update: [path.join(prefix, 'bin', 'brew'), 'upgrade', cask ? '--cask' : '--formula', name],
    };
  }
  const pkg = packages[agent];
  const npmRoot = resolved.split('/lib/node_modules/' + pkg + '/');
  const npm = npmRoot.length === 2;
  if (npm || agent === 'claude') {
    return {
      latest: async () => (await publication('https://registry.npmjs.org/' + encodeURIComponent(pkg) + '/latest')).version,
      update: npm ? ['npm', 'install', '--global', '--prefix', npmRoot[0], pkg + '@latest'] : [file, 'install', 'latest'],
    };
  }
  return {
    latest: async () => (await publication('https://api.github.com/repos/openai/codex/releases/latest')).tag_name,
    update: [file, 'update'],
  };
}

function newer(candidate, current) {
  const [next, installed] = [candidate, current].map((value) => value.split('-')[0].split('.').map(Number));
  const firstDifferent = next.findIndex((part, index) => part !== installed[index]);
  return firstDifferent === -1 ? current.includes('-') && !candidate.includes('-') : next[firstDifferent] > installed[firstDifferent];
}

async function inspect(agent) {
  const { file, target, desktop } = executable(agent);
  if (!target) throw new Error(uiText('找不到 ', "Couldn't find ") + file + uiText('，请确认已经安装 CLI，或已在桌面应用里启用 Code。', ". Install the CLI or enable Code in the desktop app."));
  const current = versionFrom(await command(file, ['--version']));
  if (desktop) return { current, desktop, path: file, available: false };
  const source = installation(agent, file, target);
  const latest = versionFrom(String(await source.latest()));
  return { current, latest, path: file, available: newer(latest, current), source };
}

async function check(agent) {
  const { source, ...status } = await inspect(agent);
  return status;
}

async function update(agent) {
  executable(agent);
  if (updating.has(agent)) throw new Error(uiText('这个 CLI 正在更新。', "This CLI is already updating."));
  updating.add(agent);
  try {
    await pendingLists.get(agent)?.catch(() => {});
    const before = await inspect(agent);
    if (!before.available) return before.desktop ? before : { current: before.current, latest: before.latest, path: before.path, available: false };
    const [file, ...args] = before.source.update;
    await command(file, args, 180000);
    const { file: cli } = executable(agent);
    const current = versionFrom(await command(cli, ['--version']));
    if (newer(before.latest, current)) throw new Error(uiText('应用仍在使用 ', "The app is still using ") + current + uiText('，请检查 CLI 的安装路径和更新权限。', ". Check which CLI is installed and whether it can be updated."));
    return { current, latest: before.latest, path: cli, available: false };
  } finally { updating.delete(agent); }
}

// Open a metadata-only session. No user message or inference request is sent.
function modelList(agent) {
  const { file, args: configured, target } = executable(agent);
  if (!target) throw new Error(uiText('找不到 CLI，请确认已经安装 CLI，或已在桌面应用里启用 Code。', "CLI not found. Install it or enable Code in the desktop app."));
  const codex = agent === 'codex';
  const args = codex ? ['app-server']
    : ['-p', '--input-format', 'stream-json', '--output-format', 'stream-json', '--verbose',
      '--no-session-persistence', '--strict-mcp-config', '--setting-sources', 'user'];
  const child = spawn(file, [...configured, ...args], { cwd: home, env: cleanEnv(), stdio: ['pipe', 'pipe', 'pipe'] });
  return new Promise((resolve, reject) => {
    const lines = readline.createInterface({ input: child.stdout });
    const models = new Map();
    const cursors = new Set();
    const send = (message) => child.stdin.write(JSON.stringify(message) + '\n');
    let stderr = '';
    let finished = false;
    const timer = setTimeout(() => finish(new Error(uiText('读取 CLI 模型列表超时，请检查登录和网络。', "Loading CLI models timed out. Check your sign-in and network."))), 15000);
    function finish(error) {
      if (finished) return;
      finished = true;
      clearTimeout(timer);
      lines.close();
      child.stdin.destroy();
      if (child.pid) require('./proc-tree').killTree(child.pid); // 连同它起的子进程（只杀它本身会留下）
      else child.kill('SIGKILL');
      if (error) reject(error);
      else resolve({ file, models: [...models] });
    }
    function add(id, label) {
      if (id && id !== 'default' && validModel(agent, id) && !models.has(id)) {
        models.set(id, typeof label === 'string' && label.trim() ? label.trim() : id);
      }
    }
    const list = (cursor) => send({ id: 2, method: 'model/list', params: { includeHidden: false, ...(cursor ? { cursor } : {}) } });
    child.stdin.on('error', () => {});
    child.stderr.on('data', (chunk) => { stderr = (stderr + chunk).slice(-1000); });
    child.once('error', (error) => finish(new Error(error.code === 'ENOENT' ? uiText('找不到 CLI，请先安装。', "Install the CLI first.") : error.message)));
    child.once('close', () => finish(new Error(stderr.trim().split('\n').at(-1) || uiText('CLI 没有返回模型列表，请检查安装和登录。', "The CLI returned no models. Check your installation and sign-in."))));
    lines.on('line', (line) => {
      let message;
      try { message = JSON.parse(line); } catch (_) { return; }
      if (!message || typeof message !== 'object') return;
      if (codex) {
        if (message.id !== 1 && message.id !== 2) return;
        if (message.error) return finish(new Error(message.error.message || uiText('Codex 无法读取模型列表。', "Codex could not load its model list.")));
        if (message.id === 1) { send({ method: 'initialized' }); return list(); }
        const { data, nextCursor } = message.result || {};
        if (!Array.isArray(data)) return finish(new Error(uiText('无法识别 Codex 的模型列表。', "Codex returned an unrecognized model list.")));
        for (const entry of data) if (entry && !entry.hidden) add(entry.model || entry.id, entry.displayName);
        if (nextCursor) {
          if (cursors.has(nextCursor)) return finish(new Error(uiText('Codex 返回了重复的模型列表分页。', "Codex returned the same model list page twice.")));
          cursors.add(nextCursor);
          return list(nextCursor);
        }
      } else {
        if (message.type !== 'control_response' || message.response?.request_id !== 'relayforge-models') return;
        const entries = message.response.response?.models;
        if (!Array.isArray(entries)) return finish(new Error(uiText('Claude Code 无法读取模型列表，请检查安装和登录。', "Claude Code couldn't load its models. Check the installation and sign-in.")));
        for (const entry of entries) if (entry) add(entry.value, entry.displayName);
      }
      finish();
    });
    send(codex ? { id: 1, method: 'initialize', params: { clientInfo: { name: 'relayforge', title: 'RelayForge', version: appVersion } } }
      : { type: 'control_request', request_id: 'relayforge-models', request: { subtype: 'initialize' } });
  });
}

function refresh(agent) {
  executable(agent);
  if (updating.has(agent)) throw new Error(uiText('请等 CLI 更新完再刷新模型。', "Wait for the CLI update to finish before refreshing models."));
  if (!pendingLists.has(agent)) {
    pendingLists.set(agent, modelList(agent).then(({ file, models }) => {
      settings.saveModels(agent, models, file);
      return { catalogs: settings.catalogs(), count: models.length };
    }).finally(() => pendingLists.delete(agent)));
  }
  return pendingLists.get(agent);
}

async function discoverMissing() {
  const saved = settings.catalogs();
  await Promise.allSettled(Object.keys(packages).filter((cli) => !Object.hasOwn(saved, cli)).map(async (cli) => refresh(cli)));
  return settings.catalogs();
}

module.exports = { check, update, refresh, discoverMissing };

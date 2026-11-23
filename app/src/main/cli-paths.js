const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

function searchPaths(env = process.env, home = os.homedir(), platform = process.platform) {
  const inherited = (env.PATH || '').split(path.delimiter).filter((dir) => path.isAbsolute(dir));
  if (platform !== 'darwin') return inherited;
  return [...new Set([
    ...inherited, path.join(home, '.local', 'bin'), path.join(home, '.npm-global', 'bin'),
    env.VOLTA_HOME && path.join(env.VOLTA_HOME, 'bin'), path.join(home, '.volta', 'bin'),
    env.NVM_BIN, path.join(home, '.nvm', 'current', 'bin'), env.PNPM_HOME, path.join(home, 'Library', 'pnpm'),
    '/opt/homebrew/bin', '/usr/local/bin', '/usr/bin', '/bin',
  ].filter((dir) => dir && path.isAbsolute(dir)))];
}

function withCliPath(env) {
  return process.platform === 'darwin' ? { ...env, PATH: searchPaths(env).join(path.delimiter) } : env;
}

function executable(file) {
  try {
    fs.accessSync(file, fs.constants.X_OK);
    return fs.statSync(file).isFile();
  } catch (_) { return false; }
}

function subdirectories(directory) {
  try { return fs.readdirSync(directory, { withFileTypes: true }).filter((item) => item.isDirectory()).map((item) => item.name); }
  catch (_) { return []; }
}

function claudeDesktopCopies(home) {
  const root = path.join(home, 'Library', 'Application Support', 'Claude', 'claude-code');
  const versions = subdirectories(root).filter((name) => /^\d+\.\d+\.\d+$/.test(name))
    .sort((a, b) => b.localeCompare(a, 'en', { numeric: true }));
  return versions.flatMap((version) => {
    const directory = path.join(root, version);
    const layouts = [directory, ...subdirectories(directory).map((name) => path.join(directory, name))];
    return layouts.flatMap((base) => [
      path.join(base, 'claude.app', 'Contents', 'MacOS', 'claude'), path.join(base, 'claude'),
    ]);
  });
}

function desktopCandidates(agent, home, applications) {
  if (agent === 'codex') {
    return applications.flatMap((directory) => [
      path.join(directory, 'ChatGPT.app', 'Contents', 'Resources', 'codex-cli', 'bin', 'codex'),
      path.join(directory, 'ChatGPT.app', 'Contents', 'Resources', 'codex-cli', 'CodexCLI.app', 'Contents', 'MacOS', 'codex'),
      ...['Codex.app', 'ChatGPT.app'].map((bundle) => path.join(directory, bundle, 'Contents', 'Resources', 'codex')),
    ]);
  }
  return [
    ...applications.flatMap((directory) => ['claude', 'claude-code/claude'].map((name) => path.join(directory, 'Claude.app', 'Contents', 'Resources', name))),
    ...claudeDesktopCopies(home),
  ];
}

function desktopOwner(agent, file, target) {
  const layouts = agent === 'codex'
    ? /\/(?:ChatGPT|Codex)\.app\/Contents\//
    : /\/Claude\.app\/Contents\/|\/Library\/Application Support\/Claude\/claude-code\//;
  return layouts.test(file) || layouts.test(target) ? agent === 'codex' ? 'ChatGPT / Codex' : 'Claude' : null;
}

function firstExecutable(candidates) {
  for (const file of candidates) {
    if (!executable(file)) continue;
    try { return { file, target: fs.realpathSync(file) }; }
    catch (_) { /* This installation may have been replaced while looking it up. */ }
  }
  return null;
}

function locateCli(agent, command, options = {}) {
  const { home = os.homedir(), platform = process.platform, paths = searchPaths(process.env, home, platform),
    applications = ['/Applications', path.join(home, 'Applications')] } = options;
  const standalone = path.isAbsolute(command) ? [command] : paths.map((dir) => path.join(dir, command));
  const found = firstExecutable(standalone) || (platform === 'darwin' && firstExecutable(desktopCandidates(agent, home, applications)));
  if (!found) return { file: command, target: null, desktop: null };
  return { ...found, desktop: platform === 'darwin' ? desktopOwner(agent, found.file, found.target) : null };
}

module.exports = { locateCli, withCliPath };

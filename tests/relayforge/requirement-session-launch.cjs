const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');
const { createRequire } = require('node:module');
const { execFileSync } = require('node:child_process');
const { EventEmitter } = require('node:events');
const repo = path.resolve(__dirname, '../..');
const backend = path.join(repo, 'backend');
const python = process.env.RELAYFORGE_PYTHON || 'python3';
const { uiText } = require('../../app/src/main/locale');

function load(name, mocks) {
  const file = path.join(repo, 'app/src/main', name + '.js');
  const local = createRequire(file);
  const context = vm.createContext({
    require: (id) => Object.hasOwn(mocks, id) ? mocks[id] : local(id),
    module: { exports: {} },
    clearTimeout,
  });
  vm.runInContext(fs.readFileSync(file, 'utf8'), context, { filename: file });
  return context.module.exports;
}

async function run(input) {
  const fixture = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'rf-session-launch-')));
  const project = path.join(fixture, 'project');
  const appHome = path.join(fixture, 'app');
  fs.mkdirSync(project);
  try {
    const env = {
      PATH: process.env.PATH,
      RELAYFORGE_HOME: path.join(project, '.local'),
      GIT_AUTHOR_NAME: 'test fixture', GIT_AUTHOR_EMAIL: 'test@example.invalid',
      GIT_COMMITTER_NAME: 'test fixture', GIT_COMMITTER_EMAIL: 'test@example.invalid',
    };
    const exec = (file, args, data) => execFileSync(file, args, { cwd: project, env, input: data, encoding: 'utf8' });
    exec('git', ['init', '-qb', 'main']);
    exec('git', ['commit', '--allow-empty', '-qm', 'Initialize fixture']);
    fs.mkdirSync(path.join(project, '.relayforge'));
    const rf = async (args, cwd = project) => {
      try { return { ok: true, stdout: exec(python, [path.join(backend, 'rf.py'), ...args, '--root', cwd]) }; }
      catch (error) { return { ok: false, stderr: error.stderr || error.message }; }
    };
    const state = { project, runs: new Map() };
    const deps = {
      './backend': { rf, uiText, parse: JSON.parse, lastLine: (value) => value.trim().split('\n').at(-1), cleanEnv: () => ({}) },
      './state': { state, send() {} },
    };
    const requirements = load('requirements', deps);
    const made = await requirements.newReq(input.title, input.choices, input.catalogs);
    if (made.error) return { error: made.error };
    const launches = [];
    const knownSession = input.resumeTesting;
    const transcript = path.join(project, 'codex-session.jsonl');
    if (knownSession) fs.writeFileSync(transcript, '');
    const claudeSettings = load('claude-settings', { ...deps, './config': { ...require('../../app/src/main/config'), RF_HOME: appHome } });
    const sessions = load('sessions', {
      ...deps,
      child_process: { execFile: (_file, _args, _options, done) => done(null, input.codexHelp || ''), spawn: (_file, args, options) => {
        const model = args.indexOf('--model');
        const flag = args.indexOf('--sandbox');
        const sandbox = flag < 0 ? null : args[flag + 1];
        // 用例关心仓库是否只读。立项 Codex 只在仓库外的草稿目录可写，并通过钩子保护项目。
        const guard = JSON.parse(options.env.RELAYFORGE_GUARD || '{}');
        const projectReadOnly = sandbox === 'workspace-write' && guard.project === project &&
          guard.drafts === options.cwd && path.dirname(options.cwd) === path.join(appHome, 'drafts');
        launches.push({ cli: args[1], model: model < 0 ? '' : args[model + 1],
          sandbox: projectReadOnly ? 'read-only' : sandbox,
          ...(Object.hasOwn(input, 'codexHelp') ? { localCodex: args.includes('--no-daemon') } : {}),
          ...(knownSession ? { resumed: args[2] === 'resume' ? args[3] : null } : {}) });
        const child = new EventEmitter();
        child.stdin = new EventEmitter();
        child.stdout = new EventEmitter();
        child.stderr = new EventEmitter();
        child.stdio = [null, null, null, new EventEmitter()];
        child.kill = () => {};
        return child;
      } },
      './config': { PYTHON: python, PTY_BRIDGE: 'pty_bridge.py' },
      './requirements': { listReqs: async () => (await requirements.listReqs()).map((req) =>
        knownSession ? { ...req, test_sessions: [knownSession] } : req), pushReqs() {} },
      './settings': { get: () => ({ agent: input.defaultCli }), command: (agent) => [agent] },
      './attention': { seen() {}, stopped() {}, setPhase() {}, forgetPhase() {}, phaseOf: () => 'idle' },
      './claude-settings': { ...claudeSettings, hooks: () => '{}' },
      './sandbox-check': { check: async () => ({ ok: true }) },
      './inbox': { takeForOpen: () => '', envFor: () => ({}) },
      './prompts': { intake: () => '', develop: () => '', following: () => '', testing: () => '' },
      './demo': { session: () => null },
      './codex-sessions': { register() {}, has: (id) => !!knownSession && id === knownSession, find: () => transcript, nativeId: () => knownSession },
      './initialization': {},
    });
    if (!knownSession) {
      await sessions.open({ id: made.id, cols: 80, rows: 24 });
      sessions.close(made.id);
    }
    // 仅在这个测试专用的临时仓库内调用后端的立项逻辑。
    exec(python, ['-c', `
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from relayforge.core.documents import add_line, read_doc, write_doc
from relayforge.requirements.record import find_requirement, req_start

ident = sys.argv[2]
root = Path.cwd()
record = root / find_requirement(root, ident)['file']
meta, sections = read_doc(record)
add_line(sections, '验收条件', '- ' + json.load(sys.stdin))
write_doc(record, meta, sections)
req_start(root, ident)
`, backend, made.id], JSON.stringify(input.check));
    await sessions.open({ id: knownSession ? `test:${made.id}` : made.id, cols: 80, rows: 24 });
    const reloaded = (await requirements.listReqs()).find((req) => req.id === made.id);
    return { launches, saved: reloaded.session_choices };
  } finally {
    fs.rmSync(fixture, { recursive: true, force: true });
  }
}

module.exports = { run };

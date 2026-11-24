// 接入代码：真实的 newReq、立项、sessions.open，只把子进程换成假的、记下启动的 CLI 和模型。
// steps 依次是 dev（开发会话打开）、test（测试会话第一次打开）、reopen（关掉开发会话再打开）。
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
  const context = vm.createContext({ require: (id) => Object.hasOwn(mocks, id) ? mocks[id] : local(id), module: { exports: {} }, clearTimeout });
  vm.runInContext(fs.readFileSync(file, 'utf8'), context, { filename: file });
  return context.module.exports;
}

async function run(input) {
  const project = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'rf-handoff-')));
  try {
    const env = { PATH: process.env.PATH, RELAYFORGE_HOME: path.join(project, '.local'),
      GIT_AUTHOR_NAME: 't', GIT_AUTHOR_EMAIL: 't@example.invalid', GIT_COMMITTER_NAME: 't', GIT_COMMITTER_EMAIL: 't@example.invalid' };
    const exec = (file, args, data) => execFileSync(file, args, { cwd: project, env, input: data, encoding: 'utf8' });
    exec('git', ['init', '-qb', 'main']);
    exec('git', ['commit', '--allow-empty', '-qm', 'init']);
    fs.mkdirSync(path.join(project, '.relayforge'));
    const rf = async (args, cwd = project) => {
      if (args[0] === 'req' && args[1] === 'attach' && args.includes('--test')) return { ok: true, stdout: 'T-1' }; // 登记测试会话只由应用做，这里不测
      try { return { ok: true, stdout: exec(python, [path.join(backend, 'rf.py'), ...args, '--root', cwd]) }; }
      catch (error) { return { ok: false, stderr: error.stderr || error.message }; }
    };
    const state = { project, runs: new Map() };
    const deps = { './backend': { rf, uiText, parse: JSON.parse, lastLine: (v) => v.trim().split('\n').at(-1), cleanEnv: () => ({}) }, './state': { state, send() {} } };
    const requirements = load('requirements', deps);
    const made = await requirements.newReq(input.title, input.choices, input.catalogs);
    if (made.error) return { error: made.error };
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
    const launches = [], native = new Map(), registered = new Set();
    const transcript = path.join(project, 'codex.jsonl');
    fs.writeFileSync(transcript, '');
    const sessions = load('sessions', {
      ...deps,
      child_process: { execFile: (_f, _a, _o, done) => done(null, ''), spawn: (_f, args) => {
        const m = args.indexOf('--model');
        launches.push({ cli: args[1], model: m < 0 ? '' : args[m + 1], resumed: args[2] === 'resume', resumedId: args[3] });
        const c = new EventEmitter();
        Object.assign(c, { stdin: new EventEmitter(), stdout: new EventEmitter(), stderr: new EventEmitter(), stdio: [null, null, null, new EventEmitter()], kill() {} });
        return c;
      } },
      './config': { PYTHON: python, PTY_BRIDGE: 'pty_bridge.py' },
      './requirements': { listReqs: async () => (await requirements.listReqs()), pushReqs() {} },
      './settings': { get: () => ({ agent: input.defaultCli || 'claude' }), command: (a) => [a] },
      './attention': { seen() {}, stopped() {}, setPhase() {}, forgetPhase() {}, phaseOf: () => 'idle' },
      './claude-settings': { ...load('claude-settings', deps), hooks: () => '{}' },
      './sandbox-check': { check: async () => ({ ok: true }) },
      './inbox': { takeForOpen: () => '', envFor: () => ({}) },
      './ui-names': { guide: () => '' }, './prompts': { intake: () => '', develop: () => '', following: () => '', testing: () => '' },
      './demo': { session: () => null },
      './codex-sessions': { register(id) { registered.add(id); }, has: (id) => registered.has(id), find: () => transcript, nativeId: (id) => 'native-' + id },
      './initialization': {},
    });
    const close = (id) => sessions.close(id);
    let devId = null;
    for (const step of input.steps) {
      const id = step === 'test' ? `test:${made.id}` : made.id;
      await sessions.open({ id, cols: 80, rows: 24 });
      if (step === 'dev') devId = [...registered].at(-1) || null;
      close(id);
    }
    return { launches: launches.map((l) => ({ cli: l.cli, model: l.model, resumed: l.resumed ? l.resumedId === (devId && 'native-' + devId) : false })) };
  } finally {
    fs.rmSync(project, { recursive: true, force: true });
  }
}

module.exports = { run };

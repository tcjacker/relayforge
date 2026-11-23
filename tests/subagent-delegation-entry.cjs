const childProcess = require('node:child_process');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');
const { randomUUID } = require('node:crypto');
const { createRequire } = require('node:module');
const { promisify } = require('node:util');

const exec = promisify(childProcess.execFile);
const repo = path.resolve(__dirname, '..');
const main = path.join(repo, 'app', 'src', 'main');
const local = createRequire(path.join(main, 'subagents.js'));
const ATTEMPT_TIMEOUT = 25000;

async function load(name, dependencies) {
  const file = path.join(main, name + '.js');
  const module = { exports: {} };
  vm.runInNewContext(await fs.readFile(file, 'utf8'), {
    module, require: (id) => Object.hasOwn(dependencies, id) ? dependencies[id] : local(id),
    process, Buffer, setTimeout, clearTimeout,
  }, { filename: file });
  return module.exports;
}

async function run(input) {
  const dir = await fs.realpath(await fs.mkdtemp(path.join(os.tmpdir(), 'rf-subagent-test-')));
  const app = local('./session-token').issuer();
  try {
    return await exercise(input, dir, app);
  } finally {
    app.close();
    await fs.rm(dir, { recursive: true, force: true });
  }
}

async function exercise(input, dir, app) {
  const worktree = path.join(dir, 'worktree');
  await fs.mkdir(worktree);
  const bundled = path.join(repo, 'backend', 'rf.py');
  const config = { ...local('./config'), RF_HOME: path.join(dir, 'app-state'), BUNDLED: bundled };
  const env = local('./cli-paths').withCliPath(Object.fromEntries(
    ['HOME', 'PATH', 'TMPDIR', 'LANG', 'USER'].filter((name) => name in process.env).map((name) => [name, process.env[name]]),
  ));
  await exec('git', ['init', '-q', worktree], { env, timeout: 10000 });

  const req = {
    id: randomUUID(), state: 'open', worktree, intake: [],
    session_choices: { development: { cli: input.development } },
  };
  const other = { id: randomUUID(), intake: [] };
  const session = { id: req.id, sessionId: randomUUID(), root: worktree, agent: input.development };
  const state = { project: dir, runs: new Map([[req.id, session]]) };
  const settings = await load('settings', { './config': config });
  const backend = {
    cleanEnv: () => ({ ...env }),
    lastLine: (value) => value.trim().split('\n').at(-1),
    // 调用同一个立项文本解析器，测试项目使用独立设置和临时对话文件。
    rf: async (args) => {
      const script = [
        'import json, sys',
        'from relayforge.requirements.subagents import intake_decisions',
        'print(json.dumps(intake_decisions(sys.argv[1])))',
      ].join('\n');
      const { stdout } = await exec(config.PYTHON, ['-B', '-c', script, args[2]], {
        cwd: path.dirname(bundled), env, timeout: 10000,
      });
      return { ok: true, stdout };
    },
  };
  const auth = await load('cli-auth', { './settings': settings, './backend': backend });
  const claudeSettings = await load('claude-settings', { './config': config, './state': { state } });
  const processes = await load('session-procs', { './config': config });
  const authOverrides = {};
  const started = [];
  const binaries = Object.fromEntries(['claude', 'codex'].map((cli) => [settings.launch(cli).file, cli]));
  const subagents = await load('subagents', {
    './backend': backend,
    './claude-settings': claudeSettings,
    './cli-auth': { status: (cli) => authOverrides[cli] ? { state: authOverrides[cli] } : auth.status(cli) },
    './requirements': { listReqs: async () => [req, other] },
    './session-procs': processes,
    './settings': settings,
    './state': { state },
    'node:child_process': {
      spawn(file, args, options) {
        const child = childProcess.spawn(file, args, options);
        child.once('spawn', () => started.push(binaries[file] || file));
        return child;
      },
    },
  });
  for (const [record, messages] of [[other, input.otherIntake], [req, input.intake]]) {
    if (!messages?.length) continue;
    const transcript = path.join(dir, record.id + '.jsonl');
    await fs.writeFile(transcript, messages.map(({ role, text }) => JSON.stringify({
      type: role, message: { content: text },
    })).join('\n'));
    record.intake.push(record.id);
    await subagents.readIntake(dir, record, () => transcript);
  }
  for (const cli of input.requireLoggedIn || []) {
    const status = await auth.status(cli);
    if (status.state !== 'in') throw new Error(`测试前提未满足：${cli} 的状态为 ${status.state}`);
  }
  if (input.choice?.model) {
    const { cli, model } = input.choice;
    settings.saveModels(cli, [[model, model]], settings.launch(cli).file);
  }
  if (input.choice) await subagents.saveChoice(dir, req, input.choice);

  // 只在隔离的签发器内模拟开发会话，子任务用真实 CLI 进程执行。
  app.setDelegate(subagents.delegate);
  const { token } = app.issue({ session: session.sessionId, kind: 'develop', owner: req.id });
  app.bind(token, process.pid, worktree);
  const attempts = [];
  for (const transition of input.attempts) {
    Object.assign(authOverrides, transition.auth);
    if (transition.choice) await subagents.saveChoice(dir, req, transition.choice);
    const offset = started.length;
    const signal = AbortSignal.timeout(ATTEMPT_TIMEOUT);
    try {
      const answer = await app.answer(JSON.stringify({ op: 'subagent', token, action: 'run', task: input.task }), signal);
      if (!answer.ok) {
        attempts.push({ error: answer.error, started: started.slice(offset) });
        continue;
      }
      const { cli, result } = answer.result;
      attempts.push({ cli, result: result.trim(), started: started.slice(offset) });
    } catch (error) {
      attempts.push({
        error: signal.aborted ? `子任务在 ${ATTEMPT_TIMEOUT / 1000} 秒内未返回。` : error.message,
        started: started.slice(offset),
      });
    }
  }
  return { attempts };
}

module.exports = { run };

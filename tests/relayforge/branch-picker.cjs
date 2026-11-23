const { execFile } = require('node:child_process');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { setTimeout: delay } = require('node:timers/promises');
const { promisify } = require('node:util');

const exec = promisify(execFile);
const root = path.resolve(__dirname, '../..');
const backend = path.join(root, 'backend/rf.py');

function git(project, env, ...args) {
  return exec('git', args, { cwd: project, env, timeout: 10000 }).then(({ stdout }) => stdout.trim());
}

async function prepare(project, env, fixture) {
  // Use the existing demo builder to start a recognized project without running an agent.
  await exec('python3', [path.join(root, 'test-project/build_demo.py'), '--target', project,
    '--backend', backend], { env, timeout: 30000 });
  const call = (...args) => git(project, env, ...args);
  const baseline = await call('rev-parse', 'HEAD');
  for (const [name, branch] of Object.entries(fixture.branches || {})) {
    await call('switch', '-q', '-C', name, baseline);
    for (const [file, content] of Object.entries(branch.files || {})) {
      await fs.writeFile(path.join(project, file), content);
      await call('add', '--', file);
    }
    if (branch.commit) await call('commit', '-qm', branch.commit);
    for (const requirement of branch.unpublished || []) {
      const work = 'fixture/' + requirement.id;
      await call('switch', '-q', '-c', work);
      await call('commit', '--allow-empty', '-qm', requirement.title);
      await call('switch', '-q', name);
      await call('merge', '-q', '--no-ff', '-m', requirement.title, work);
      await call('branch', '-D', work);
      const anchor = await call('rev-parse', 'HEAD');
      const record = path.join(project, '.relayforge/requirements', requirement.id + '-fixture.md');
      await fs.writeFile(record, [
        '---', 'type: requirement', 'status: current', 'state: done',
        'id: ' + requirement.id, 'title: ' + requirement.title, 'merge_commit: ' + anchor, '---', '',
      ].join('\n'));
    }
  }
  await call('switch', '-q', fixture.current);
  const managed = {};
  for (const title of fixture.managed || []) {
    const { stdout } = await exec('python3', [backend, 'req', 'new', title, '--json'],
      { cwd: project, env, timeout: 10000 });
    managed[title] = JSON.parse(stdout).branch;
  }
  for (const name of fixture.occupied || []) {
    await call('worktree', 'add', '-q', path.join(path.dirname(project), 'occupied', name), name);
  }
  for (const [name, heads] of Object.entries(fixture.remotes || {})) {
    // Populate the bare repository directly so the test can exercise real fetches offline.
    const remote = path.join(path.dirname(project), name + '.git');
    await call('clone', '-q', '--bare', '--shared', project, remote);
    const remoteGit = (...args) => git(remote, env, ...args);
    for (const ref of (await remoteGit('for-each-ref', '--format=%(refname)', 'refs/heads')).split('\n')) {
      if (ref) await remoteGit('update-ref', '-d', ref);
    }
    for (const [branch, source] of Object.entries(heads)) {
      await remoteGit('update-ref', 'refs/heads/' + branch, source ? await call('rev-parse', source) : baseline);
    }
    await call('remote', 'add', name, remote);
    await call('fetch', '-q', '--no-tags', name);
  }
  for (const [branch, upstream] of Object.entries(fixture.tracking || {})) {
    await call('branch', '--set-upstream-to=' + upstream, branch);
  }
  for (const [ref, source] of Object.entries(fixture.newRemoteHeads || {})) {
    const slash = ref.indexOf('/');
    const remote = path.join(path.dirname(project), ref.slice(0, slash) + '.git');
    await git(remote, env, 'update-ref', 'refs/heads/' + ref.slice(slash + 1),
      source ? await call('rev-parse', source) : baseline);
  }
  for (const name of fixture.offline || []) {
    const remote = path.join(path.dirname(project), name + '.git');
    await fs.rename(remote, remote + '.offline');
  }
  for (const [file, content] of Object.entries(fixture.dirty || {})) {
    await fs.writeFile(path.join(project, file), content);
  }
  return managed;
}

async function run(input) {
  const temp = await fs.mkdtemp(path.join(os.tmpdir(), 'rf-branch-picker-'));
  const project = path.join(temp, 'project');
  const env = {
    PATH: process.env.PATH,
    RELAYFORGE_HOME: path.join(temp, 'home'),
    RELAYFORGE_PROJECT: project,
    RELAYFORGE_USER_DATA: path.join(temp, 'profile'),
    GIT_CONFIG_NOSYSTEM: '1',
    GIT_CONFIG_GLOBAL: os.devNull,
    LC_ALL: 'C',
  };
  try {
    await fs.mkdir(env.RELAYFORGE_HOME);
    const managed = await prepare(project, env, input.fixture);
    const steps = input.steps.map((step) =>
      step.searchManaged ? { ...step, type: managed[step.searchManaged] } : step);
    const { stdout } = await exec(require('../../app/node_modules/electron'),
      [__filename, JSON.stringify(steps)],
      { cwd: root, env, timeout: 18000, killSignal: 'SIGKILL' });
    return JSON.parse(stdout.trim());
  } finally {
    await fs.rm(temp, { recursive: true, force: true, maxRetries: 5 });
  }
}

async function interact(contents, step) {
  const script = `(${function (step) {
    const visible = (node) => !!node && node.checkVisibility({ visibilityProperty: true });
    const candidates = [...document.querySelectorAll(step.selector)];
    const node = step.label === undefined ? candidates[0] : candidates.find(
      (item) => item.querySelector('.branch-name')?.textContent.trim() === step.label);
    const shown = visible(node);
    if (step.read === 'visible') return { value: shown };
    if (step.watch && node) {
      const state = { opened: [] };
      new MutationObserver((records) => {
        for (const record of records) {
          if (record.oldValue === null) state.opened.push(record.attributeName);
        }
      }).observe(node, { attributes: true, attributeFilter: [step.watch], attributeOldValue: true });
      window.branchPickerTestWatch = state;
      return {};
    }
    if (step.read === 'opened') return { value: window.branchPickerTestWatch.opened };
    if (!shown) return null;
    if (step.click || step.type !== undefined) {
      if (node.disabled) return null;
      if (step.click) node.click();
      else {
        node.value = step.type;
        node.dispatchEvent(new Event('input', { bubbles: true }));
      }
      return {};
    }
    const read = (item) => {
      if (!visible(item)) return null;
      const value = step.attribute ? item.getAttribute(step.attribute) : item.textContent.trim();
      return step.capture ? value?.match(new RegExp(step.capture, 'm'))?.[1] ?? null : value;
    };
    const value = step.collect
      ? [...node.querySelectorAll(step.collect)].filter(visible).map(read)
      : read(step.child ? node.querySelector(step.child) : node);
    return { value };
  }})(${JSON.stringify(step)})`;
  const deadline = Date.now() + 5000;
  while (Date.now() < deadline) {
    const result = await contents.executeJavaScript(script);
    if (result) return result.value;
    await delay(30);
  }
  throw new Error('等待界面元素超时：' + JSON.stringify(step));
}

function start(steps) {
  const { app } = require('electron');
  const project = process.env.RELAYFORGE_PROJECT;
  const before = new Map();
  app.once('browser-window-created', (_event, win) => {
    win.webContents.once('did-finish-load', async () => {
      try {
        const output = {};
        for (const step of steps) {
          const call = (...args) => git(project, process.env, ...args);
          let value;
          if (step.mark) before.set(step.mark, await call('rev-parse', step.ref));
          else if (step.distance) value = await call('rev-list', '--left-right', '--count',
            before.get(step.distance) + '...' + step.ref);
          else if (step.git) value = await call(...step.git);
          else if (step.file) value = await fs.readFile(path.join(project, step.file), 'utf8');
          else value = await interact(win.webContents, step);
          if (step.as) output[step.as] = value;
        }
        process.stdout.write(JSON.stringify(output) + '\n');
        app.quit();
      } catch (error) {
        process.stderr.write(error.stack + '\n');
        app.exit(1);
      }
    });
  });
  require('../../app/src/main');
}

if (process.type === 'browser' && process.env.RELAYFORGE_PROJECT) start(JSON.parse(process.argv.at(-1)));
module.exports = { run };

// 测试集接入：确认完成以后的合并队伍（app/src/main/merge.js）。
// 数据给出 rf.py req done 依次的回答和一串操作（enqueue / settled 种类 / retry [强行]），这里照着调用，
// 报告每次「重新检查并合并」的结果、rf.py req done 跑了几次、最后合并框显示的阶段和「AI 还在干活」。
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');

const ANSWERS = {
  ok: { ok: true, stdout: JSON.stringify({ id: 'R-a', state: 'done' }), stderr: '' },
  behind: { ok: false, stdout: JSON.stringify({ sync: true, conflict: false, base: 'main', files: [], loose: [], error: '还没包含 main 的最新内容' }), stderr: '' },
};
const tick = () => new Promise((resolve) => setImmediate(resolve));

function describe(result) {
  if (!result) return null;
  if (result.confirm) return '要你确认';
  if (result.merging) return result.merging.phase;
  if (result.error) return '出错';
  return result.state === 'done' ? '已合并' : '其他';
}

exports.run = async function ({ answers, steps }) {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'rf-merge-set-'));
  const queue = answers.map((name) => ANSWERS[name]);
  const sent = [];
  let doneCalls = 0;
  const mocks = {
    './summary': { start() {} },
    './backend': {
      exec: async () => ({ stdout: 'main\n' }),
      rf: async () => { doneCalls++; return queue.shift() || ANSWERS.behind; },
      parse: (text, fallback) => { try { return JSON.parse(text); } catch (_) { return fallback; } },
      lastLine: (x) => String(x || '').trim().split('\n').pop(),
    },
    './config': { RF_HOME: home },
    './state': { state: { project: '/project' }, send: (channel, payload) => sent.push([channel, payload]) },
    './requirements': { pushReqs() {} },
    './issues': { pushIssues() {} },
    './services': { stopScope: async () => {} },
  };
  try {
    const context = vm.createContext({ require: (id) => mocks[id] || require(id), module: { exports: {} }, setTimeout, clearTimeout, console });
    vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'app', 'src', 'main', 'merge.js'), 'utf8'), context);
    const merge = context.module.exports;
    const close = () => true;
    const retries = [];
    for (const [op, arg] of steps) {
      if (op === 'enqueue') await merge.enqueue('R-a', close);
      else if (op === 'settled') merge.settled('R-a', arg);
      else if (op === 'retry') retries.push(describe(await merge.retry('R-a', !!arg)));
      for (let i = 0; i < 4; i++) await tick();
    }
    const pushes = sent.filter((x) => x[0] === 'merge');
    const shown = pushes.length ? pushes[pushes.length - 1][1].find((x) => x.id === 'R-a') : null;
    return {
      retries,
      doneCalls,
      merged: sent.some((x) => x[0] === 'merge:finished'),
      phase: shown ? shown.phase : '不在队伍里',
      busy: shown ? !!shown.busy : false,
    };
  } finally {
    fs.rmSync(home, { recursive: true, force: true });
  }
};

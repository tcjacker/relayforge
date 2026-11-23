// 后台跑一轮应有上限，即使一直有输出也不能无限占着合并队列。
const HEADLESS_LIMIT_MS = 60 * 60_000;
// 完整测试通常要十分钟，给没有中间输出的命令多留一点时间。
const HEADLESS_SILENCE_MS = 15 * 60_000;
// 交互会话可能在跑较长的命令，在半小时的钩子兜底之前留时间提醒用户。
const INTERACTIVE_SILENCE_MS = 20 * 60_000;
// 给唤醒钩子和聊天记录落盘留余量，重送后也用同样的时间等回执。
const RECEIPT_WAIT_MS = 3 * 60_000;

const { uiText } = require('./locale');

const headless = new WeakMap();
const interactive = new WeakMap();

function startHeadless(run, stop, now = Date.now()) {
  headless.set(run, { stop, checked: now, output: now, active: 0, silent: 0, waiting: false });
}

function touch(run, now = Date.now()) {
  const clock = headless.get(run);
  if (!clock) return;
  clock.output = now;
  clock.silent = 0;
}

function watchHeadless(run, waiting, now = Date.now()) {
  const clock = headless.get(run);
  if (!clock) return;
  // 请求出现和消失都在轮询中才知道，边界上的这段时间也留给审批。
  if (!waiting && !clock.waiting) {
    clock.active += Math.max(0, now - clock.checked);
    clock.silent += Math.max(0, now - Math.max(clock.checked, clock.output));
  }
  clock.checked = now;
  clock.waiting = waiting;
  if (waiting) return;

  let reason;
  if (clock.active >= HEADLESS_LIMIT_MS) {
    reason = uiText('后台会话已运行 60 分钟，本轮已停止。', 'The background session ran for 60 minutes and was stopped.');
  } else if (clock.silent >= HEADLESS_SILENCE_MS) {
    reason = uiText('后台会话已有 15 分钟没有输出，本轮已停止。',
      'The background session had no output for 15 minutes and was stopped.');
  }
  if (!reason) return;
  headless.delete(run);
  clock.stop(reason);
}

function watchInteractive(run, phase, size, remind, now = Date.now()) {
  if (phase?.phase !== 'running') {
    interactive.delete(run);
    return;
  }
  let activity = interactive.get(run);
  if (activity?.phase !== phase) {
    activity = { phase, size: run.seen, at: phase.since, reminded: false };
    interactive.set(run, activity);
  }
  if (size !== null) {
    if (size > activity.size) {
      activity.at = now;
      activity.reminded = false;
    }
    activity.size = size;
  }
  if (activity.reminded || now - activity.at < INTERACTIVE_SILENCE_MS) return;
  activity.reminded = true;
  remind(run.id, uiText('会话可能卡住了', 'The session may be stuck'),
    uiText('会话已连续干活至少 20 分钟，近 20 分钟聊天记录没有增加。',
      'The session has been running for at least 20 minutes with no new chat activity.'), 'stuck');
}

function deliveryDue(message, ready, now) {
  const elapsed = now - Date.parse(message.retried || message.sent);
  return !message.retryWarned && elapsed >= RECEIPT_WAIT_MS && (message.retried || ready);
}

function checkDelivery(messages, ready, checkReceipt, now) {
  const due = messages.filter((m) => deliveryDue(m, ready, now));
  if (!due.length) return null;
  checkReceipt();
  const retry = [], unread = [];
  const at = new Date(now).toISOString();
  for (const message of due) {
    if (message.state !== 'sent') continue;
    if (message.retried) {
      message.retryWarned = at;
      unread.push(message.id);
    } else {
      message.retried = at;
      retry.push(message);
    }
  }
  return { retry, unread };
}

module.exports = { startHeadless, touch, watchHeadless, watchInteractive, checkDelivery,
  HEADLESS_LIMIT_MS, HEADLESS_SILENCE_MS, INTERACTIVE_SILENCE_MS, RECEIPT_WAIT_MS };

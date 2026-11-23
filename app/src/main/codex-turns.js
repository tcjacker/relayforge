// Codex 的交互会话把轮次完成事件写到 JSONL；按新增字节读取，避免把历史轮次当成当前合并已完成。
const fs = require('fs');

function readTurn(run, size) {
  let fd;
  try { fd = fs.openSync(run.transcript, 'r'); } catch (_) { return null; }
  let outcome = null;
  const truncated = run.seen > size;
  let offset = truncated ? 0 : run.seen;
  let pending = truncated ? Buffer.alloc(0) : run.turnTail || Buffer.alloc(0);
  if (truncated) run.turnActive = false;
  const chunk = Buffer.alloc(64 * 1024);
  try {
    while (offset < size) {
      const bytes = fs.readSync(fd, chunk, 0, Math.min(chunk.length, size - offset), offset);
      if (!bytes) break;
      offset += bytes;
      pending = Buffer.concat([pending, chunk.subarray(0, bytes)]);
      for (let end; (end = pending.indexOf(10)) !== -1;) {
        const line = pending.subarray(0, end);
        pending = pending.subarray(end + 1);
        let event;
        try { event = JSON.parse(line.toString('utf8')); } catch (_) { continue; }
        if (event.type !== 'event_msg') continue;
        const type = event.payload?.type;
        if (type === 'task_started' || type === 'turn_started') {
          run.turnActive = true;
          outcome = 'running';
        } else if (run.turnActive && ['task_complete', 'turn_complete', 'turn_aborted'].includes(type)) {
          run.turnActive = false;
          outcome = type === 'turn_aborted' ? 'exit' : 'reply';
        }
      }
    }
    run.turnTail = pending;
  } catch (_) { return null; } finally { fs.closeSync(fd); }
  return outcome;
}

module.exports = { readTurn };

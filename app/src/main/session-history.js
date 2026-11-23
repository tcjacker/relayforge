// 用量和回放都由 rf.py 读取本机记录，主进程只校验参数和交回结果。
const { rf, parse, lastLine, uiText } = require('./backend');

async function read(args) {
  const out = await rf([...args, '--json'], undefined, undefined, 20000);
  if (out.timedOut) return { error: uiText('读取会话记录超时了。', 'Loading the session record timed out.') };
  return out.ok ? parse(out.stdout, { error: uiText('读不懂会话记录。', 'Could not read the session record.') })
    : { error: lastLine(out.stderr) };
}

function usage(ref) {
  if (typeof ref !== 'string' || !/^(R-[\w-]+|[FI]-\d+)$/.test(ref)) {
    return { error: uiText('请提供需求、修复或问题编号。', 'Enter a requirement, fix, or issue ID.') };
  }
  return read(['usage', ref]);
}

function replay(session, offset) {
  if (typeof session !== 'string' || !/^[0-9a-zA-Z_.-]{8,80}$/.test(session) || session.includes('..') ||
      !Number.isSafeInteger(offset) || offset < 0) {
    return { error: uiText('会话编号或回放位置有误。', 'Invalid session ID or replay position.') };
  }
  return read(['history', 'session', session, '--steps', '--offset', String(offset)]);
}

module.exports = { usage, replay };

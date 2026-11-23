// 后台 Codex 的 stdio JSON-RPC 连接由当前会话独占，工具进程仍在会话令牌绑定的进程树里。
const { createInterface } = require('node:readline');
const { uiText } = require('./locale');
const { killTree } = require('./proc-tree');
const { version } = require('../../package.json');

const RPC_TIMEOUT = 30000;
const CLOSE_TIMEOUT = 3000;
const textInput = (text) => ({ type: 'text', text, text_elements: [] });
const rejected = () => uiText('Codex 已经结束这一轮，或正在等你处理卡片。',
  'Codex has finished this turn or is waiting for your response.');

function connect(child, { cwd, model, sandbox, approvalPolicy, threadId: resumeId, prompt, images = [] }, handlers) {
  const pending = new Map();
  const requests = new Map();
  const items = new Map();
  let serial = 0, threadId, turnId, closing = false, resultCode = 1, closeTimer;
  const closed = new Promise((resolve) => child.once('close', resolve));
  const lines = createInterface({ input: child.stdout, crlfDelay: Infinity });
  const write = (message) => child.stdin.write(JSON.stringify(message) + '\n');

  function rpc(method, params) {
    if (closing) return Promise.reject(new Error(rejected()));
    return new Promise((resolve, reject) => {
      const id = ++serial;
      const timer = setTimeout(() => {
        settle(id, null, new Error(uiText('Codex 没有回答 {0}，请重试。', 'Codex did not answer {0}. Try again.', method)));
      }, RPC_TIMEOUT);
      pending.set(id, { resolve, reject, timer });
      try { write({ id, method, params }); } catch (error) { settle(id, null, error); }
    });
  }

  function settle(id, result, error) {
    const request = pending.get(id);
    if (!request) return;
    pending.delete(id);
    clearTimeout(request.timer);
    if (error) request.reject(error);
    else request.resolve(result);
  }

  function stop(code, force = false) {
    if (!closing) {
      closing = true;
      resultCode = code;
      clearRequests();
      // 先关闭输入，让 Codex 写完聊天记录；服务端没有退出时再收拾整棵树。
      if (!force) {
        child.stdin.end();
        closeTimer = setTimeout(kill, CLOSE_TIMEOUT);
      }
    }
    if (force) kill();
    return closed;
  }

  function kill() {
    if (child.exitCode == null && child.signalCode == null) killTree(child.pid);
    // 子进程异常退出后，后代也可能还持有输出管道，不能让接管一直等。
    child.stdout.destroy();
    child.stderr.destroy();
  }

  function clearRequests() {
    for (const cleanup of requests.values()) cleanup();
    requests.clear();
  }

  function fail(error) {
    if (closing) return;
    handlers.error(error.message);
    stop(1, true);
  }

  function answer(id, result, error) {
    if (!closing) write(error ? { id, error: { code: -32601, message: error } } : { id, result });
  }

  function approval(method, p) {
    const item = items.get(p.itemId);
    switch (method) {
      case 'item/commandExecution/requestApproval': {
        const host = p.networkApprovalContext;
        const what = [p.command, host && `${host.protocol}://${host.host}`].filter(Boolean).join('\n');
        return { tool: 'Bash', what, why: [p.cwd, p.reason].filter(Boolean).join('\n'),
          unsupported: p.kind === 'writeStdin',
          decision: (allow) => ({ decision: allow ? 'accept' : 'decline' }) };
      }
      case 'item/fileChange/requestApproval':
        return { tool: 'Edit', what: (item?.changes || []).map((change) => `${change.kind.type} ${change.path}\n${change.diff}`).join('\n'),
          why: p.reason, decision: (allow) => ({ decision: allow && !p.grantRoot ? 'accept' : 'decline' }),
          unsupported: !!p.grantRoot };
      case 'item/permissions/requestApproval':
        return { tool: uiText('申请权限', 'Request permissions'), what: JSON.stringify(p.permissions, null, 2),
          why: [p.cwd, p.reason].filter(Boolean).join('\n'),
          decision: (allow) => ({ permissions: allow ? p.permissions : {}, scope: 'turn' }) };
      default: return null;
    }
  }

  function requestResolved(id) {
    const cleanup = requests.get(id);
    requests.delete(id);
    if (cleanup) {
      cleanup();
      handlers.requestResolved();
    }
  }

  function serverRequest({ id, method, params: p = {} }) {
    const request = approval(method, p);
    if (p.threadId !== threadId || !turnId || p.turnId !== turnId) {
      if (request) return answer(id, request.decision(false));
      return answer(id, null, uiText('当前会话无法处理这个请求。', 'The current session cannot handle this request.'));
    }
    if (requests.has(id)) return;
    if (request) {
      // 卡片必须能展示实际要批准的动作。会话级文件授权没有对应按钮。
      if (!request.what || request.unsupported) return answer(id, request.decision(false));
      const clear = handlers.approval(request, (allow) => {
        requests.delete(id);
        answer(id, request.decision(allow));
      });
      requests.set(id, clear);
    } else if (method === 'item/tool/requestUserInput' && p.questions?.length && !p.questions.some((q) => q.isSecret)) {
      const clear = handlers.questions(p.questions, (answers) => {
        requests.delete(id);
        answer(id, { answers });
      });
      if (clear) requests.set(id, clear);
      else answer(id, null, uiText('已有一个问题正在等用户回复。', 'Another question is already waiting for an answer.'));
    } else {
      answer(id, null, uiText('后台会话不支持这个请求，请在终端里接管。',
        'This request needs an interactive terminal. Take over in terminal.'));
    }
  }

  function receive(message) {
    const { id, method, params: p = {} } = message;
    if (id != null) {
      if (method && !closing) return serverRequest(message);
      if (!method) settle(id, message.result, message.error && new Error(message.error.message));
      return;
    }
    if (closing || !threadId || p.threadId !== threadId) return;
    if (method === 'turn/started' && !turnId) {
      turnId = p.turn.id;
      handlers.started();
      return;
    }
    if (method === 'serverRequest/resolved') return requestResolved(p.requestId);
    if (!turnId || (p.turnId || p.turn?.id) !== turnId) return;
    if (method === 'item/started' && p.item.type === 'fileChange') items.set(p.item.id, p.item);
    if (method === 'item/completed') {
      items.delete(p.item.id);
      handlers.item(p.item);
    }
    if (method === 'error') handlers.error(p.error.message);
    if (method === 'turn/completed') {
      turnId = null;
      handlers.completed(p.turn);
      stop(p.turn.status === 'completed' ? 0 : 1);
    }
  }

  lines.on('line', (line) => {
    let message;
    try { message = JSON.parse(line); } catch (_) { return; }
    try { receive(message); } catch (error) { fail(error); }
  });
  child.stdin.on('error', fail);
  child.on('error', fail);
  child.once('close', () => {
    closing = true;
    clearTimeout(closeTimer);
    clearRequests();
    for (const id of pending.keys()) settle(id, null, new Error(rejected()));
    handlers.closed(resultCode);
  });

  async function begin() {
    await rpc('initialize', { clientInfo: { name: 'relayforge', title: 'RelayForge', version } });
    if (closing) return;
    write({ method: 'initialized' });
    const settings = { cwd, ...(model && { model }), sandbox, approvalPolicy, approvalsReviewer: 'user' };
    const result = resumeId
      ? await rpc('thread/resume', { ...settings, threadId: resumeId, excludeTurns: true })
      : await rpc('thread/start', { ...settings, ephemeral: false });
    const sandboxType = sandbox === 'read-only' ? 'readOnly' : 'workspaceWrite';
    if (result.sandbox?.type !== sandboxType || result.approvalsReviewer !== 'user' || result.approvalPolicy !== approvalPolicy) {
      throw new Error(uiText('Codex 没有使用要求的沙箱或本人审批设置。请检查 Codex 的设置后重试。',
        'Codex did not apply the required sandbox or approval settings. Check Codex settings and try again.'));
    }
    const { thread } = result;
    if (!thread?.id || (resumeId && thread.id !== resumeId)) {
      throw new Error(uiText('Codex 返回了无效的会话编号。', 'Codex returned an invalid thread ID.'));
    }
    threadId = thread.id;
    handlers.thread(thread);
    const input = [textInput(prompt), ...images.map((path) => ({ type: 'localImage', path }))];
    const { turn } = await rpc('turn/start', { threadId, input });
    if (!turnId && !closing) {
      turnId = turn.id;
      handlers.started();
    }
  }
  begin().catch(fail);

  return {
    closed,
    stop: () => stop(1, true),
    steer(text) {
      if (!turnId || closing || requests.size) return null;
      return rpc('turn/steer', { threadId, expectedTurnId: turnId, input: [textInput(text)] });
    },
  };
}

module.exports = { connect };

// 无界面运行的会话：测试会话的再测一轮、合并前的同步、问题的修复——这几类不用人盯着，由主进程在后台跑一轮（不开终端），
// 事件和批准变成程序能直接处理的数据（设计见 docs/design/会话通道与需求冲突-设计.md 第一部分「第二阶段」）。
// - 怎么跑：sessions.open({ headless: true }) 用和交互会话同一套参数，Claude Code 加 -p --output-format stream-json，
//   Codex 用 app-server 的 stdio JSON-RPC（codex-app-server.js），和交互终端共用原生线程编号。
// Codex CLI 0.171.0：用 app-server --help 找到 generate-json-schema --out，然后核对生成的 ClientRequest、
// ServerRequest、ServerNotification、各方法的 Params/Response。握手 initialize → initialized，thread/start 或
// thread/resume（threadId）返回 thread.id/path；turn/start 用 threadId/input，turn/steer 另带 expectedTurnId，
// turn/interrupt 用 threadId/turnId；接管时收拾整棵进程树。通知是 turn/started、item/started、item/completed、
// turn/completed（turn.status/error）、error、serverRequest/resolved。批准请求是 item/commandExecution/requestApproval、
// item/fileChange/requestApproval（decision: accept/decline）和 item/permissions/requestApproval（permissions/scope）；
// item/tool/requestUserInput 用各问题编号回 answers。
// - 你点开它：先把它到现在做了什么（这里整理成一行行的文字）给你看；它在跑时终端不收键盘，要接管点「在终端里接管」（hitl.js）。
// - 要你批准：Claude Code 通过钩子、Codex 通过 RPC 等你在应用里点（hitl.js）。最后一句是在问你，记成「在问你」；
//   否则算说完了。都按会话停下来通知原来的流程（attention.mergeProgress：合并重试、测试阶段、收件箱、后台记录……）。
const { uiText } = require('./locale');
const { spawn } = require('child_process');
const { state, send } = require('./state');
const attention = require('./attention');
const sessionToken = require('./session-token');
const stuck = require('./stuck');

const LOG_CAP = 200000; // 整理出来的过程最多留多少字（从前面丢）
const SHOWN = 160; // 一步里的命令或说明最多显示多少字

const one = (text, cap = SHOWN) => { const t = String(text || '').replace(/\s+/g, ' ').trim(); return t.length > cap ? t.slice(0, cap) + '…' : t; };
const asks = (text) => { const tail = String(text || '').split('\n').filter((l) => l.trim()).pop() || ''; return /[?？]/.test(tail); };

function say(run, text) {
  const data = String(text).replace(/\r?\n/g, '\r\n') + '\r\n';
  run.log = (run.log + data).slice(-LOG_CAP);
  send('term:data', { id: run.id, data });
}

// Claude Code 的 stream-json：一行一个事件
function fromClaude(run, e) {
  if (e.type === 'system' && e.subtype === 'init') return say(run, uiText('\x1b[2m（后台运行开始）\x1b[0m', "\u001b[2m(Background run started)\u001b[0m"));
  if (e.type === 'assistant' && e.message && Array.isArray(e.message.content)) {
    for (const b of e.message.content) {
      if (b.type === 'text' && b.text && b.text.trim()) { run.last = b.text; say(run, b.text.trim()); }
      if (b.type === 'tool_use') {
        const i = b.input || {};
        say(run, '\x1b[2m· ' + b.name + ' ' + one(i.command || i.file_path || i.description || i.pattern || '') + '\x1b[0m');
      }
    }
    return;
  }
  if (e.type === 'user' && e.message && Array.isArray(e.message.content)) {
    for (const b of e.message.content) if (b.type === 'tool_result' && b.is_error) say(run, '\x1b[31m  ✗ ' + one(typeof b.content === 'string' ? b.content : JSON.stringify(b.content), 120) + '\x1b[0m');
    return;
  }
  if (e.type === 'result') {
    if (typeof e.result === 'string' && e.result.trim()) run.last = e.result;
    return say(run, uiText("\u001b[2m（这一轮做完了{0}{1}）\u001b[0m", "\u001b[2m(Turn complete{0}{1})\u001b[0m", e.duration_ms ? uiText('，用时 ', ", took ") + Math.round(e.duration_ms / 1000) + uiText(' 秒', " s") : '', e.is_error ? uiText('，出错了', ", with errors") : ''));
  }
}

// Codex 的过程按完成的条目显示，避免重复输出流式片段。
function fromCodex(run, item) {
  if (item.type === 'agentMessage' && item.text?.trim()) {
    run.last = item.text;
    say(run, item.text.trim());
  } else if (item.type === 'commandExecution') {
    const error = item.exitCode ? uiText('  ✗ 退出码 {0}', '  ✗ Exit code {0}', item.exitCode)
      : item.status === 'failed' || item.status === 'declined' ? uiText('  ✗ 未执行成功', '  ✗ Command did not succeed') : '';
    say(run, '\x1b[2m' + uiText('· 跑 ', '· Ran ') + one(item.command) + (error ? '\x1b[0m\x1b[31m' + error : '') + '\x1b[0m');
  } else if (item.type === 'fileChange') {
    say(run, '\x1b[2m' + uiText('· 改 ', '· Changed ') + one(item.changes.map((change) => change.path).join('、')) + '\x1b[0m');
  }
}

function start(run, file, args, { cwd, env }, sig, { ended = () => {}, codex } = {}) {
  const hitl = require('./hitl');
  Object.assign(run, { headless: true, log: '', last: '', buffer: '' });
  hitl.clearQuestion(run.id);
  // Claude 的批准由钩子等文件。Codex 的钩子继续报状态，但批准只等 app-server 的 RPC。
  const child = spawn(file, args, { cwd, env: codex ? env : { ...env, ...hitl.envFor(state.project) },
    stdio: [codex ? 'pipe' : 'ignore', 'pipe', 'pipe'] });
  run.term = child;
  stuck.startHeadless(run, (reason) => {
    say(run, '\x1b[33m' + reason + '\x1b[0m');
    require('./sessions').stop(run);
    send('term:exit', { id: run.id, code: 124, headless: true, kind: 'exit' });
    attention.mergeProgress(run.id, 'exit', reason);
    hitl.setStuck(run, reason);
  });
  sessionToken.bind(sig, child.pid, cwd); // 令牌只在这个进程和它下面的进程里算数
  child.stderr.on('data', (chunk) => {
    stuck.touch(run); // 有输出就不算卡住（stuck.js）
    const t = chunk.toString('utf8').trim();
    if (t) say(run, '\x1b[2m' + one(t, 400) + '\x1b[0m');
  });
  let done = false;
  const finished = (code, failure) => {
    if (done) return;
    done = true;
    sessionToken.unbind(sig);
    if (failure) say(run, uiText('\x1b[31m后台运行没能开始：', "\u001b[31mCould not start the background run: ") + failure + '\x1b[0m');
    if (state.runs.get(run.id) !== run) return; // 被接管或关掉了（sessions.stop 已经收拾过）
    if (codex) {
      attention.readEvents(); // Stop 钩子的状态可能还在等 watcher 的下一次轮询。
      if (state.runs.get(run.id) !== run) return;
      require('./inbox').markSeen(run);
    }
    state.runs.delete(run.id);
    ended(); // 这一轮起的进程过一会儿收拾掉（sessions.js 的 reapLater；同一个会话又开起来了就不收）
    attention.stopped(run.id);
    // 钩子报过的（attention.js 记在 hookKind）以钩子为准；钩子不能用（Codex 没信任）时看最后一句是不是在问你
    const kind = run.hookKind || (code !== 0 && (codex || !run.last) ? 'exit'
      : asks(run.last) && run.last !== run.answered ? 'answer' : 'reply');
    if (kind === 'answer') hitl.setQuestion(run, run.last);
    else if (codex) hitl.clearQuestion(run.id);
    try { require('./monitor').ended(run); } catch (_) { /* 后台记录出错不影响会话 */ }
    send('term:exit', { id: run.id, code, headless: true, kind });
    // 钩子报过「说完了」的不再报一次，免得合并之类的流程走两遍
    if (run.hookKind) attention.setPhase(run.id, kind);
    else attention.mergeProgress(run.id, kind, kind === 'exit' ? uiText('AI 会话在后台没跑完就退出了。', "The Agent exited before the background run finished.") : one(run.last, 120));
    // 这一轮跑着时又排进来、钩子没交出去的话：说完了就再跑一轮交给它（在问你时等你回复，回复时一起带上）
    if (kind === 'reply') require('./inbox').afterHeadless(run.id);
  };
  if (codex) {
    let trusted = true;
    try { trusted = require('./codex-trust').trusted(); } catch (_) { /* 查不了就不提示 */ }
    if (!trusted) say(run, uiText(
      '\x1b[33mCodex 的钩子还没生效：这一轮没有执行前检查和状态提醒。重新打开应用会再试一次。\x1b[0m',
      '\x1b[33mCodex hooks are not active: this turn has no pre-execution checks or status updates. Reopen the app to retry.\x1b[0m'));
    const error = (message) => {
      if (message && run.codexError !== message) say(run, '\x1b[31m' + uiText('出错了：', 'Error: ') + one(message, 300) + '\x1b[0m');
      run.codexError = message;
    };
    child.stdout.on('data', () => stuck.touch(run)); // app-server 的每条消息都算有动静；读消息的是 codex-app-server.js 自己
    const connection = require('./codex-app-server').connect(child, { cwd, ...codex }, {
      thread(thread) {
        if (thread.path) run.transcript = thread.path;
        require('./codex-sessions').bindThread(run.sessionId, thread);
      },
      started() {
        say(run, '\x1b[2m' + uiText('（后台运行开始）', '(Background run started)') + '\x1b[0m');
        require('./inbox').flush(run.id);
      },
      item(item) {
        fromCodex(run, item);
        if (item.type === 'agentMessage' && item.text?.trim()) {
          const question = asks(item.text) ? item.text : '';
          if (!question && hitl.waitingForReply(run.id)) {
            hitl.offerReply(run, '');
            require('./inbox').flush(run.id);
          } else if (question) hitl.offerReply(run, question);
        }
      },
      error,
      completed(turn) {
        error(turn.error?.message);
        if (turn.status === 'completed') say(run, '\x1b[2m' + uiText('（这一轮做完了）', '(Turn complete)') + '\x1b[0m');
      },
      approval: (request, decide) => hitl.requestApproval(run, request, decide),
      questions: (questions, reply) => hitl.requestAnswers(run, questions, reply),
      requestResolved: () => hitl.resumeAfterApproval(run),
      closed: (code) => finished(code),
    });
    run.steer = (text, program) => program && hitl.waitingForReply(run.id) ? null : connection.steer(text);
    run.stopHeadless = connection.stop;
    run.headlessClosed = connection.closed;
  } else {
    child.stdout.on('data', (chunk) => {
      stuck.touch(run);
      const lines = (run.buffer + chunk.toString('utf8')).split('\n');
      run.buffer = lines.pop();
      for (const line of lines) {
        let e;
        try { e = JSON.parse(line); } catch (_) { if (line.trim()) say(run, line); continue; }
        try { fromClaude(run, e); } catch (_) { /* 认不出的事件不显示 */ }
      }
    });
    child.on('error', (error) => finished(127, error.message));
    child.on('exit', (code) => finished(code == null ? 1 : code));
  }
  return child;
}

// 你点开一个正在后台跑的会话：把到现在做的给你看
function replay(run) {
  send('term:data', { id: run.id, data: uiText('\x1b[2m（这个会话正在后台跑，下面是它到现在做了什么；要自己接着聊，点「在终端里接管」）\x1b[0m\r\n', "\u001b[2m(This session is running in the background. Its activity appears below. Select Take over in terminal to continue the conversation.)\u001b[0m\r\n") + (run.log || '') });
}

// 你在它的终端里打字：后台跑着时不收，只说一次怎么办
function typedInto(run) {
  if (run.typedNote) return;
  run.typedNote = true;
  say(run, uiText('\x1b[33m这个会话正在后台跑，终端不收键盘。要自己接着聊，点右下角的「在终端里接管」；要回答它的问题，在那里回复。\x1b[0m', "\u001b[33mThis background session is read-only here. Select Take over in terminal at the bottom right to continue the conversation or answer a question.\u001b[0m"));
}

module.exports = { start, replay, typedInto, fromClaude, fromCodex, asks };

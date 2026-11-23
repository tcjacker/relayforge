// 逐条验收（需求面板在待验收时每条验收条件的「通过」「这里不对」）和测试页人工测试的「发现不对 → 连同日志记成问题」。
// 「通过」「这里不对」只由你在应用里点：rf.py req accept 要应用的标记（app-token.js），会话里禁止（claude-settings.js 的 REQ_USER_DENY）。
// 「这里不对」：rf.py 记下这一条、把需求退回进行中，写好交给开发会话的话（你的那句话、验收条件和检查步骤、当时的日志尾部，保密值打码），
// 这里只把话交给开发会话（开着就输进去，没开就让界面在后台打开它，这段话作为第一句）。
const { uiText = (zh) => zh, rf, lastLine, parse } = require('./backend');
const services = require('./services');

const REQ = /^R-[\w-]+$/;
const LOG_LINES = 60;

async function mark({ req, n, result, say } = {}) {
  if (!REQ.test(String(req || ''))) return { error: uiText('要说是哪个需求。', "Provide a requirement ID.") };
  if (!['ok', 'bad'].includes(result)) return { error: uiText('只能是通过或这里不对。', "Mark the criterion as passed or incorrect.") };
  const k = Number(n);
  if (!(k >= 1)) return { error: uiText('要说是第几条验收条件。', "Select an acceptance criterion.") };
  const words = String(say || '').trim();
  if (result === 'bad' && !words) return { error: uiText('写一句哪里不对。', "Describe what went wrong.") };
  const log = result === 'bad' ? services.tail(req, LOG_LINES) : '';
  const ran = result === 'bad' ? await services.runLine(req) : ''; // 当时运行的是哪个提交、启动后代码有没有改过
  const out = await rf(['req', 'accept', req, String(k), result, ...(result === 'bad' ? ['--say', words, ...(ran ? ['--ran', ran] : []), '--file', '-'] : []), '--json'], undefined, undefined, 60000, log);
  const made = out.ok ? parse(out.stdout, null) : null;
  if (!made) return { error: lastLine(out.stderr) };
  if (made.result === 'bad' && made.say) require('./testsets').tell(req, made.say);
  require('./requirements').pushReqs();
  return { ...made, log: !!log };
}

// 人工测试（测试页，对着项目主目录）：一句话 + 当时的日志尾部记成一条问题（现有的新建问题）
async function issue({ scope, text } = {}) {
  const words = String(text || '').trim();
  if (!words) return { error: uiText('写一句哪里不对。', "Describe what went wrong.") };
  const where = scope && scope !== 'main' ? uiText('需求 ', "Requirement ") + scope + uiText(' 的代码', " code") : uiText('主线（项目主目录）', "Main checkout");
  const log = services.tail(scope || 'main', LOG_LINES);
  const ran = await services.runLine(scope || 'main');
  const desc = uiText("人工测试时发现，对着{0}：{1}", "Found in manual testing against {0}: {1}", where, words) + (ran ? uiText("\n\n当时运行的服务：{0}", "\n\nServices running at the time: {0}", ran) : '') + (log ? uiText("\n\n当时服务的日志（最后 {0} 行以内，保密值已打码）：\n{1}", "\n\nService logs (up to {0} lines, with secrets redacted):\n{1}", LOG_LINES, log) : uiText('\n\n（当时没有服务的日志）', "\n\n(No service logs were available)"));
  return require('./issues').newIssue({ title: words.slice(0, 80), desc });
}

module.exports = { mark, issue };

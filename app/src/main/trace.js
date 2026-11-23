// 追溯：一个需求或问题的改动（提交、文件、会话、有没有发布）。全是 rf.py trace --json 算的，只读，不调用模型。
const { rf, lastLine, parse, uiText = (zh) => zh } = require('./backend');

async function get(ref) {
  const text = String(ref || '').trim();
  if (!/^(R-[\w-]+|F-\d+|I-\d+|#\d+)$/i.test(text)) return { error: uiText('要给需求、修复或问题的编号。', "Enter a requirement, fix, or issue ID.") };
  const out = await rf(['trace', text, '--json'], undefined, undefined, 20000);
  const made = out.ok ? parse(out.stdout, null) : null;
  return made && !made.error ? made : { error: out.timedOut ? uiText('读取改动超时了。', "Loading changes timed out.") : lastLine(out.stderr) };
}

module.exports = { get };

// 离线演示模板和它生成的独立仓库。浏览预置记录不会启动模型。
const fs = require('fs');
const path = require('path');
const { uiText = (zh) => zh, exec } = require('./backend');
const { HOME_REPO, BUNDLED, PYTHON, RF_HOME } = require('./config');
const source = path.join(HOME_REPO, 'test-project');
const template = fs.existsSync(path.join(source, 'build_demo.py')) ? source : path.join(process.resourcesPath || '', 'test-project');
// 打包后 HOME_REPO 正好是应用的资源目录，source 也存在；演示项目不能生成在应用包里：写进去会破坏签名，装在只读位置时还会失败。
const insideApp = Boolean(process.resourcesPath) && path.resolve(template) === path.resolve(process.resourcesPath, 'test-project');
const target = template === source && !insideApp ? path.join(source, '.runtime', 'flownote') : path.join(RF_HOME, 'test-project');
function read(root) {
  try {
    const data = JSON.parse(fs.readFileSync(path.join(root, '.relayforge', 'demo.json'), 'utf8'));
    return data.schema === 'relayforge.demo.v1' ? data : null;
  } catch (_) { return null; }
}
async function ensure() {
  if (read(target)) return target;
  if (!fs.existsSync(path.join(template, 'build_demo.py'))) return null;
  const out = await exec(PYTHON, ['-B', path.join(template, 'build_demo.py'), '--target', target, '--backend', BUNDLED], HOME_REPO);
  if (!out.ok) throw new Error(out.stderr || uiText('演示项目准备失败。', "Couldn't prepare the demo project."));
  return target;
}
function info(root) {
  const data = read(root);
  return data ? { name: data.name, notice: data.notice, requirements: Object.keys(data.requirements).length } : null;
}
function session(root, id) {
  const record = (read(root) || {}).sessions?.[id];
  if (!record) return null;
  let place = path.resolve(root, record.root);
  if (place !== path.resolve(root) && !place.startsWith(path.resolve(root) + path.sep)) return null;
  if (!fs.existsSync(place)) {
    // 完成后工作目录收起，示例会话记录随需求合并进项目目录。
    const folder = path.join(root, '.relayforge', 'requirements');
    if (!id.startsWith('R-') || !fs.existsSync(folder) || !fs.readdirSync(folder).some((name) => name.startsWith(id + '-') && name.endsWith('.md'))) return null;
    place = path.resolve(root);
  }
  return { ...record, root: place };
}
function chats(root) {
  return Object.entries((read(root) || {}).sessions || {}).filter(([, record]) => record.kind === 'chat').map(([, record]) => ({
    session_id: record.sessionId, title: record.title, first: record.first, blank: true, demo: true, updated: uiText('演示记录', "Demo record"),
  }));
}
function transcript(record) {
  return uiText('\r\n\x1b[36m演示记录 · 不会调用 AI\x1b[0m\r\n\r\n', "\r\n\u001b[36mDemo transcript · AI is off\u001b[0m\r\n\r\n") + record.messages.map(([role, text]) =>
    '\x1b[' + (role === 'user' ? '33' : '32') + 'm' + (role === 'user' ? uiText('用户', "User") : 'AI') + '\x1b[0m\r\n' + text + '\r\n').join('\r\n') + uiText('\r\n\x1b[2m以上是预置的虚构记录。点击「开始真实会话」后才会使用所选工具。\x1b[0m\r\n', "\r\n\u001b[2mThis is a sample conversation. Select Start a live session to use your Agent.\u001b[0m\r\n");
}
module.exports = { target, ensure, info, session, chats, transcript };

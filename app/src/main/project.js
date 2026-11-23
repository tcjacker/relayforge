// 项目：本机登记过的项目可以直接切换，也可以打开一个新的目录；没初始化的目录先替你初始化。
const { app, dialog, Menu } = require('electron');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { HOME_REPO, RF_HOME_REAL, YES, USER_DATA, WINDOW_TAG, INDEX_HTML, windowTitle } = require('./config');
const { uiText = (zh) => zh, exec, rf, rfBundled, lastLine, parse } = require('./backend');
const { state, send } = require('./state');
const { pushReqs } = require('./requirements');
const sessions = require('./sessions');
const initialization = require('./initialization');
const demo = require('./demo');
const appMenu = require('./app-menu');

// ~/.relayforge 是这台电脑记项目登记和草稿的地方，不是一个项目；不排除的话，
// 主目录下任何还没初始化的目录都会被当成「主目录这个项目」，打开其他目录就一直停在主目录。
// 数据外置（rf.py 的 common.link_facts）：仓库里提交 .relayforge.json（项目 ID），记录在 ~/.relayforge/projects/<ID>/，
// 项目里的 .relayforge 是指向它的链接。重新克隆、挪了位置时还没有链接，也认作项目，打开时由 rf.py 接上。
const ID_FILE = '.relayforge.json';
function isProject(dir) {
  const mark = path.join(dir, '.relayforge');
  if (fs.existsSync(path.join(dir, ID_FILE))) return true;
  try { return fs.statSync(mark).isDirectory() && fs.realpathSync(mark) !== RF_HOME_REAL; } catch (_) { return false; }
}
// 旧项目：记录还是仓库里的 .relayforge/ 目录，没有项目 ID。这个版本不打开它，只提示需要迁移。
function isLegacy(dir) {
  const mark = path.join(dir, '.relayforge');
  try { return !fs.existsSync(path.join(dir, ID_FILE)) && !fs.lstatSync(mark).isSymbolicLink() && isProject(dir); } catch (_) { return false; }
}
function findRoot(start) {
  for (let dir = path.resolve(start); ; dir = path.dirname(dir)) {
    if (isProject(dir)) return dir;
    if (dir === path.dirname(dir)) return null;
  }
}
const LEGACY_TEXT = '这个项目的记录还放在仓库的 .relayforge/ 目录里，是旧版本的存放方式。这个版本把记录放在本机 ~/.relayforge/projects/ 下，要先迁移才能打开。';
const MIGRATE_DETAIL = [
  '迁移会做这些事：',
  '· 把仓库里的 .relayforge/ 整个复制到本机 ~/.relayforge/projects/<项目 ID>/（包括还没提交的改动），核对一个不少；',
  '· 提交一次：从 Git 里去掉 .relayforge/，加上 .relayforge.json（项目 ID），.gitignore 加一行 /.relayforge；',
  '· 仓库里原来那个目录不删，挪到本机 ~/.relayforge/projects/ 下留着备查；',
  '· 以前写进仓库给 AI 的约定一并拿掉：AGENTS.md 里 RelayForge 那一段（没有别的内容就删掉文件）、只有 @AGENTS.md 一行的 CLAUDE.md、.claude/skills/ 下的 rf-sync 和 rf-confirm；约定改由应用交给它开的会话；',
  '· 你自己写在 AGENTS.md、CLAUDE.md 里的内容和项目里别的改动都不动。',
  '',
  '迁移以后，旧版本的应用打不开这个项目。中途失败会退回原样。',
].join('\n');
// 旧项目：问一声，用户点「迁移」才迁（rf.py data migrate 只由用户触发，带应用的标记）。迁好了返回 true。
async function migrateLegacy(root) {
  if (!isLegacy(root)) return true;
  const name = path.basename(root);
  if (!(await ask(uiText("「{0}」需要迁移", "“{0}” needs migration", name), uiText(LEGACY_TEXT, 'This project stores its records in .relayforge/ inside the repository. Migrate them to ~/.relayforge/projects/ before opening the project.')
    + '\n\n' + uiText(MIGRATE_DETAIL, [
      'Migration will:',
      '• Copy .relayforge/ to ~/.relayforge/projects/<project ID>/ and verify the copy, including uncommitted changes.',
      '• Commit the removal of .relayforge/ from Git, add .relayforge.json with the project ID, and ignore /.relayforge.',
      '• Move the original records directory to ~/.relayforge/projects/ as a backup.',
      '• Remove RelayForge-generated instructions and skills from the repository. The app supplies instructions to its sessions.',
      '• Preserve your own changes and instructions in AGENTS.md and CLAUDE.md.',
      '',
      'Open the migrated project with this or a newer version. If migration fails, RelayForge restores the original files.',
    ].join('\n')), uiText('迁移', "Migrate")))) return false;
  const out = await rf(['data', 'migrate', '--json'], root, undefined, 180000); // rf() 带应用的标记
  const made = parse(out.stdout, null);
  if (!out.ok || !made || !made.ok) {
    const why = (made && made.error) || (out.timedOut ? uiText('迁移等了太久，已经停下。', "Migration timed out and was stopped.") : lastLine(out.stderr));
    if (!YES) dialog.showErrorBox(uiText("「{0}」没有迁移", "Couldn't migrate “{0}”", name), why);
    return false;
  }
  if (!YES) await dialog.showMessageBox(state.win, { type: 'info', message: uiText("「{0}」迁移好了", "“{0}” migrated", name), detail: uiText("记录在 {0}（{1} 个文件），已提交 {2}。\n仓库里原来那份留在 {3}，确认没问题以后可以自己删掉。", "Saved {0} ({1} files) in commit {2}.\nThe original directory is backed up at {3}. You can remove it after checking the migration.", made.store, made.files, made.commit, made.backup), buttons: [uiText('好', "OK")] });
  return true;
}

// 记住上次用的项目，下次启动直接打开
const statePath = () => path.join(app.getPath('userData'), 'state.json');
function lastProject() { try { return JSON.parse(fs.readFileSync(statePath(), 'utf8')).project; } catch (_) { return null; } }
function remember(root) { try { fs.mkdirSync(path.dirname(statePath()), { recursive: true }); fs.writeFileSync(statePath(), JSON.stringify({ project: root })); } catch (_) { /* 记不住也不影响使用 */ } }

async function known() {
  const out = await rfBundled(['projects', '--json'], os.homedir());
  try { return JSON.parse(out.stdout); } catch (_) { return []; }
}

async function ask(message, detail, yes) {
  if (YES) return true;
  const pick = await dialog.showMessageBox(state.win, { type: 'question', message, detail, buttons: [yes, uiText('取消', "Cancel")], defaultId: 0, cancelId: 1 });
  return pick.response === 0;
}

// 刚导入、还没有大图的项目
const fresh = new Set();
const seeding = new Set();
// 数据外置的项目不往仓库里写 AGENTS.md / CLAUDE.md（约定在本机记录的 CONVENTIONS.md 里）
const initFiles = (root) => ['.relayforge/FORMAT.md', '.relayforge/bin/rf.py', ...(external(root) ? ['.relayforge/CONVENTIONS.md'] : ['AGENTS.md', 'CLAUDE.md'])];
function hasInitFiles(root) { return initFiles(root).every((name) => fs.existsSync(path.join(root, name))); }
function hasUsableProject() { return Boolean(state.project && hasInitFiles(state.project)); }
function hasInitFacts(root) {
  return ['modules', 'features'].every((name) => {
    try { return fs.readdirSync(path.join(root, '.relayforge', name)).some((file) => file.endsWith('.md')); } catch (_) { return false; }
  });
}
function initStatus(root = state.project) {
  const failure = initialization.get(root);
  return { root, running: seeding.has(root), failure };
}
function pushInitialization(root = state.project) {
  if (root !== state.project) return;
  send('project:initialization', initStatus(root));
  appMenu.refresh();
}
function failed(root, stage, out) {
  initialization.fail(root, stage, out);
  pushInitialization(root);
  return root;
}

async function prepare(dir) {
  const found = findRoot(dir);
  if (found && !(await migrateLegacy(found))) return null;
  if (found) {
    if (!fs.existsSync(path.join(found, '.relayforge'))) await rfBundled(['projects', '--add', found], found); // 按项目 ID 接回本机那份记录
    return found;
  }
  const top = (await exec('git', ['-C', dir, 'rev-parse', '--show-toplevel'], dir)).stdout.trim();
  if (!top) { dialog.showErrorBox(uiText('打不开这个目录', "Couldn't open this directory"), uiText('它不是一个 Git 仓库。先在这个目录里运行 git init 并提交一次，再来打开。', "Initialize a Git repository with git init and make an initial commit, then open it again.")); return null; }
  if (!(await exec('git', ['-C', top, 'rev-parse', '--verify', '--quiet', 'HEAD'], top)).ok) { dialog.showErrorBox(uiText('打不开这个目录', "Couldn't open this directory"), uiText('这个仓库还没有任何提交。先提交一次，再来打开。', "Make an initial commit before opening this repository.")); return null; }
  const go = await ask(uiText("「{0}」还没有初始化 RelayForge", "Set up RelayForge in “{0}”?", path.basename(top)),
    uiText('初始化只在项目里添加 .relayforge.json（项目 ID）并改 .gitignore，提交一次；不改 AGENTS.md、CLAUDE.md，也不往 .claude/ 里加东西。项目的记录和给 AI 的约定放在本机 ~/.relayforge/projects/ 下，不进 Git。项目里其他没提交的改动不会被动到。\n\n之后会让 AI 读一遍代码，生成大图的第一版草稿（模块、功能点、产品介绍），都标为待确认。', "Setup adds .relayforge.json with the project ID, updates .gitignore, and commits those files. Project records and Agent instructions are stored locally under ~/.relayforge/projects/.\n\nAI then reads the code and drafts a project map with modules, features, and an overview for your review."), uiText('初始化并提交', "Set up and commit"));
  if (!go) return null;
  const made = await rfBundled(['init', top], top);
  if (!made.ok) return failed(top, uiText('初始化项目文件', "Create project setup files"), made);
  const files = [ID_FILE, ...(external(top) ? [] : ['AGENTS.md', 'CLAUDE.md', '.claude/skills']), '.gitignore'];
  const staged = await exec('git', ['-C', top, 'add', '--', ...files], top);
  if (!staged.ok) return failed(top, uiText('暂存初始化文件', "Stage setup files"), staged);
  const saved = await exec('git', ['-C', top, 'commit', '-q', '-m', '初始化 RelayForge', '--', ...files], top);
  if (!saved.ok) return failed(top, uiText('提交初始化文件', "Commit setup files"), saved);
  fresh.add(top);
  return top;
}

// 刚导入的项目还没有大图：让 AI 读一遍代码，写出第一版模块、功能点和产品介绍，全部是草稿，等用户确认
async function seed(root) {
  if (!fresh.delete(root) || seeding.has(root)) return;
  seeding.add(root); pushInitialization(root);
  try {
    const out = await rfBundled(['bootstrap', '--root', root, '--json'], root);
    let made = null;
    try { made = JSON.parse(out.stdout); } catch (_) { /* 下面记录完整的失败原因 */ }
    if (!out.ok || !made || !Array.isArray(made.modules)) return failed(root, uiText('生成大图草稿', "Draft project map"), out);
    if (external(root)) { /* 数据外置：大图草稿写下去就算数，不进 Git */ } else {
    const staged = await exec('git', ['-C', root, 'add', '--', '.relayforge'], root);
    if (!staged.ok) return failed(root, uiText('暂存大图草稿', "Stage project map draft"), staged);
    const committed = await exec('git', ['-C', root, 'commit', '-q', '-m', '导入：读代码生成的大图草稿（待确认）', '--', '.relayforge'], root);
    if (!committed.ok) return failed(root, uiText('提交大图草稿', "Commit project map draft"), committed);
    }
    initialization.clear(root);
    if (root === state.project) {
      send('notice', uiText("大图草稿好了：{0} 个模块、{1} 个功能点，都等你确认。到「首页」里看。", "The draft project map has {0} modules and {1} features ready for review on the Home page.", made.modules.length, made.features));
      pushReqs();
    }
  } catch (error) { failed(root, uiText('生成大图草稿', "Draft project map"), { stderr: error.message }); }
  finally { seeding.delete(root); pushInitialization(root); }
}

function repairInitialization() {
  if (seeding.has(state.project)) return { error: uiText('初始化仍在进行，请等待结果。', "Setup is still running. Wait for it to finish.") };
  try { return initialization.repair(state.project); } catch (error) { return { error: error.message }; }
}
async function checkInitialization() {
  const root = state.project;
  try {
    if (!hasInitFiles(root)) return { error: uiText('初始化文件仍不完整，请在 AI 修复会话中继续处理。', "Setup files are still incomplete. Continue in the AI repair session.") };
    if (!hasInitFacts(root)) return { error: uiText('还没有完整的大图草稿，请让 AI 检查导入结果。', "The project map draft is incomplete. Ask AI to inspect it.") };
    const rendered = await rfBundled(['render', '--root', root], root);
    if (!rendered.ok) return { error: uiText('大图仍无法生成：', "Couldn't render the project map: ") + lastLine(rendered.stderr) };
    const changes = await exec('git', ['-C', root, 'status', '--porcelain', '--', ...(external(root) ? [ID_FILE] : []), '.relayforge/FORMAT.md', '.relayforge/bin', '.relayforge/modules', '.relayforge/features', '.relayforge/journeys', '.relayforge/product', ...(external(root) ? [] : ['AGENTS.md', 'CLAUDE.md', '.claude/skills']), '.gitignore'], root);
    if (!changes.ok || changes.stdout.trim()) return { error: uiText('初始化文件仍有未提交的改动，请在修复会话里检查并提交相关文件。', "Setup files still have uncommitted changes. Check and commit them in the repair session.") };
    initialization.clear(root); pushInitialization(root);
    if (root === state.project) { pushReqs(); send('notice', uiText('初始化检查通过，可以开始使用项目了。', "Setup checks passed. The project is ready to use.")); }
    return { ok: true };
  } catch (error) { return { error: error.message }; }
}

function external(root) { try { return fs.lstatSync(path.join(root, '.relayforge')).isSymbolicLink(); } catch (_) { return false; } }

async function openProject(dir) {
  const root = await prepare(dir);
  if (!root || root === state.project) return false;
  const { runs } = state;
  if (runs.size && !(await ask(uiText("切换到「{0}」？", "Switch to “{0}”?", path.basename(root)), uiText("现在开着的 {0} 个会话会关掉，切回来后点需求可以接着聊。", "{0} open sessions will close. You can resume them when you return to this project.", runs.size), uiText('切换', "Switch")))) return false;
  const previous = state.project;
  if (previous) {
    require('./merge').pause(previous);
    sessions.stopAll();
    require('./services').stopAll(uiText('切换项目', "Project switched")); // 切走项目：这个项目里为验收启动的服务全部停掉
  }
  appMenu.setWindowReady(false);
  require('./conflicts').reset(); // 需求之间的冲突：上一个项目的结果不再显示
  state.project = root;
  await rfBundled(['projects', '--add', root], root);
  remember(root);
  const { win } = state;
  if (win) {
    win.setTitle(windowTitle(path.basename(root)));
    if (previous) win.reload();
    else win.loadFile(INDEX_HTML, { query: { language: require('./settings').language() } });
  }
  return true;
}
global.rfOpenProject = openProject;

async function pickFolder() {
  const pick = await dialog.showOpenDialog(state.win, { title: uiText('选择项目目录', "Choose a project directory"), properties: ['openDirectory'] });
  return pick.canceled ? null : pick.filePaths[0];
}

// 界面要项目的名字时，顺便看看是不是刚导入、该生成大图草稿了
function info() {
  const root = state.project;
  // 旧版本只显示过失败消息，没有保存失败记录；重新打开时也要能进入恢复流程。
  if (!fresh.has(root) && !seeding.has(root) && !initialization.get(root) && (!hasInitFiles(root) || !hasInitFacts(root))) {
    failed(root, uiText('检查项目初始化', "Check project setup"), { stderr: uiText('项目的初始化文件或大图草稿不完整。旧版本可能未保存失败详情，请检查 .relayforge/log.txt 和当前工具配置后修复。', "Setup files or project map drafts are incomplete. Check .relayforge/log.txt and the selected CLI before repairing setup.") });
  }
  seed(root);
  if (!demo.info(root)) require('./summary').sweep(root); // 已经完成、还没存到本机的会话记录补存一次（后台，不等它）；演示项目不用
  return { name: path.basename(root), root, initialization: initStatus(), demo: demo.info(root), tag: WINDOW_TAG, userData: USER_DATA ? app.getPath('userData') : null };
}

async function showMenu() {
  const items = (await known()).map((p) => ({ label: p.name + (isLegacy(p.root) ? uiText('（需要迁移）', " (migration required)") : ''), sublabel: p.root, type: 'checkbox', checked: path.resolve(p.root) === state.project, click: () => openProject(p.root) }));
  Menu.buildFromTemplate([...items, ...(items.length ? [{ type: 'separator' }] : []),
    ...(initialization.get(state.project) ? [{ label: uiText('重试初始化（AI 协助）…', "Retry setup with AI…"), click: () => send('project:initialization', initStatus()) }, { type: 'separator' }] : []),
    { label: uiText('打开其他目录…', "Open another directory…"), click: async () => { const dir = await pickFolder(); if (dir) openProject(dir); } }]).popup({ window: state.win });
}

// 启动时优先使用显式指定的项目，否则准备演示项目；不可用时再回退到最近项目和目录选择。
async function choose() {
  // RELAYFORGE_PROJECT=demo：打开演示项目（验收实例常用）
  const named = (process.env.RELAYFORGE_PROJECT || '').trim();
  const demoAsked = named === 'demo';
  const asked = demoAsked ? null : named || process.argv.slice(2).find((a) => !a.startsWith('-') && a !== '.');
  let project = asked && fs.existsSync(asked) ? await prepare(asked) : null;
  if (!project && (!asked || demoAsked)) {
    try { project = await demo.ensure(); } catch (error) { dialog.showErrorBox(uiText('演示项目准备失败', "Couldn't prepare the demo project"), error.message); }
  }
  project = project || [lastProject(), HOME_REPO].map((dir) => dir && fs.existsSync(dir) && (findRoot(dir) || (initialization.get(path.resolve(dir)) && path.resolve(dir)))).find((dir) => dir && !isLegacy(dir)) || null;
  while (!project) {
    const dir = await pickFolder();
    if (!dir) return null;
    project = await prepare(dir);
  }
  rfBundled(['projects', '--add', project], project);
  remember(project);
  return project;
}

module.exports = { isLegacy, LEGACY_TEXT, choose, info, showMenu, openProject, hasUsableProject, repairInitialization, checkInitialization };

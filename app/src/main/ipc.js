// 界面和主进程之间的所有通道都在这里登记（另一头是 src/preload/index.js 里的 window.shell）。
// invoke 的用 handle，要回答；send 的用 on，不回答。
const { ipcMain: electronIpc } = require('electron');
const { pathToFileURL } = require('url');
const { uiText = (zh) => zh } = require('./locale') || {};
const { INDEX_HTML } = require('./config');
const project = require('./project');
const requirements = require('./requirements');
const sessions = require('./sessions');
const attention = require('./attention');
const designs = require('./designs');
const images = require('./images');
const settings = require('./settings');
const monitor = require('./monitor');
const cliTools = require('./cli-tools');
const cliAuth = require('./cli-auth');
const merge = require('./merge');
const issues = require('./issues');
const publish = require('./publish');
const branches = require('./branches');
const trace = require('./trace');
const sessionHistory = require('./session-history');
const summary = require('./summary');
const testsets = require('./testsets');
const envs = require('./envs');
const testing = require('./testing');
const inbox = require('./inbox');
const services = require('./services');
const acceptance = require('./acceptance');
const splits = require('./splits');
const { state, send } = require('./state');

// 只认主窗口顶层页面（应用自己的 index.html）发来的：这些通道能取明文、推送、同意执行命令，界面哪里漏了转义、或者别的页面
// （设计稿、iframe、导航到别处）拿到 window.shell，都不能借它调用。不是主窗口发来的：invoke 的回答一个错误，send 的不理
const INDEX_URL = pathToFileURL(INDEX_HTML).href;
function fromMainWindow(event) {
  const win = state.win;
  if (!event || !win || (win.isDestroyed && win.isDestroyed()) || event.sender !== win.webContents) return false;
  const frame = event.senderFrame;
  return !!frame && frame === win.webContents.mainFrame && String(frame.url || '').split(/[?#]/)[0] === INDEX_URL;
}
const ipcMain = {
  handle: (channel, fn) => electronIpc.handle(channel, (event, ...args) => {
    if (!fromMainWindow(event)) throw new Error(uiText("不认这个来源的请求：{0}", "This request came from an unrecognized source: {0}", channel));
    return fn(event, ...args);
  }),
  on: (channel, fn) => electronIpc.on(channel, (event, ...args) => { if (fromMainWindow(event)) fn(event, ...args); }),
};

function cliAction(action) {
  return async (_e, agent) => {
    try { return await action(agent); }
    catch (error) { return { error: error.message }; }
  };
}

function register() {
  attention.onSettled(merge.settled); // AI 解决冲突这一轮说完了：自动再试着合并
  attention.onSettled(monitor.settled); // 后台记录：会话说完一轮就记下来（设置里打开时）
  attention.onPhase(inbox.phaseChanged); // 会话收件箱：会话能收话了就把排着的送过去
  attention.onSettled(require('./fresh').settled); // 功能点的核对会话说完了：大图和建议对话框重读
  attention.onSettled(require('./conflicts').settled); // 需求之间的冲突：会话说完一轮（可能刚改了文件）就重算
  ipcMain.handle('settings:get', () => { try { return { ...settings.get(), catalogs: settings.catalogs() }; } catch (error) { return { error: uiText('读取设置失败：', "Couldn't load settings: ") + error.message }; } });
  ipcMain.handle('settings:save', async (_e, value) => {
    try {
      await require('./subagents').validateDefault(value?.subagent);
      const saved = settings.save(value);
      require('./locale').setLanguage(saved.language);
      require('./app-menu').relabel(saved.language);
      if (state.project) requirements.pushReqs();
      monitor.setEnabled(saved.monitor); // 后台记录的开关只在这里和启动时更新
      return saved;
    } catch (error) { return { error: uiText('保存设置失败：', "Couldn't save settings: ") + error.message }; }
  });
  ipcMain.handle('cli:check', cliAction(cliTools.check));
  ipcMain.handle('cli:update', cliAction(cliTools.update));
  ipcMain.handle('cli:models', cliAction(cliTools.refresh));
  ipcMain.handle('cli:discover-models', () => cliTools.discoverMissing());
  ipcMain.handle('cli:auth-status', cliAction(cliAuth.status));
  ipcMain.handle('cli:login', cliAction(cliAuth.login)); // 等到浏览器授权完成、失败、超时或取消才回答
  ipcMain.handle('cli:login-cancel', cliAction(cliAuth.cancel));

  // 项目
  ipcMain.handle('project:info', () => project.info());
  ipcMain.handle('project:repair', () => project.repairInitialization());
  ipcMain.handle('project:check-initialization', () => project.checkInitialization());
  ipcMain.on('project:menu', () => project.showMenu());

  // 需求和以前的聊天
  ipcMain.handle('conflicts:predict', (_e, id) => require('./conflicts').predict(id).catch(() => null)); // 立项中的需求：按历史预测会撞上谁
  ipcMain.handle('conflicts:get', () => { const c = require('./conflicts'); if (!c.current()) c.schedule(0); return c.current(); }); // 还没算过就马上算，算完推 conflicts
  ipcMain.handle('reqs:list', async () => {
    const project = state.project;
    const found = await requirements.listReqs(project);
    if (project !== state.project) return [];
    testing.follow(found).catch(() => {});
    return requirements.withSubagent(project, found);
  });
  ipcMain.handle('reqs:subagent-save', async (_e, id, input) => {
    const project = state.project;
    try {
      const req = (await requirements.listReqs(project)).find((item) => item.id === id);
      if (state.project !== project || !req || !['draft', 'open'].includes(req.state)) throw new Error('这个需求当前不能修改 subagent 设置。');
      const result = await require('./subagents').saveChoice(project, req, input);
      if (state.project === project) requirements.pushReqs();
      return result;
    } catch (error) { return { error: error.message }; }
  }); // 记下打开时的样子，之后的变化才算「刚进入」
  ipcMain.handle('testing:open', (_e, id) => testing.open(String(id || ''))); // 点开一个需求：该开的测试会话、该交的结论补上
  ipcMain.handle('sessions:list', requirements.listSessions);
  ipcMain.handle('inbox:list', (_e, id) => typeof id === 'string' ? inbox.messagesFor(id) : []);
  ipcMain.handle('inbox:queued', () => inbox.queuedForProject());
  ipcMain.handle('inbox:withdraw', (_e, to, id) => typeof to === 'string' && typeof id === 'string' && inbox.withdraw(to, id));
  ipcMain.handle('reqs:new', (_e, title, choices) => requirements.newReq(title, choices, settings.catalogs()));
  ipcMain.handle('reqs:case-save', (_e, id, payload) => requirements.saveCase(id, payload));
  ipcMain.handle('reqs:case-decide', (_e, id, k, ok, change) => requirements.decideCase(id, k, ok, change));
  ipcMain.handle('reqs:start', (_e, id) => requirements.startReq(id, sessions.close));
  ipcMain.handle('reqs:done', (_e, id) => merge.enqueue(id, sessions.close)); // 排队合并，有冲突回到会话里解决，见 merge.js
  // 后台运行的会话需要你：批准或拒绝、回复它的问题、在终端里接管（hitl.js）
  ipcMain.handle('hitl:list', () => require('./hitl').snapshot());
  ipcMain.handle('hitl:decide', (_e, id, allow, message) => require('./hitl').decide(String(id || ''), !!allow, message ? String(message) : ''));
  ipcMain.handle('hitl:reply', (_e, id, text) => require('./hitl').reply(String(id || ''), String(text || '')));
  ipcMain.handle('hitl:takeover', (_e, id) => require('./hitl').takeover(String(id || '')));
  ipcMain.handle('merge:abort', (_e, id) => merge.abort(id));
  ipcMain.handle('merge:retry', (_e, id, force, skipGate) => merge.retry(id, !!force, !!skipGate)); // force：AI 还在干活时你确认了仍然合并；skipGate：跳过合并前的检查
  ipcMain.handle('merge:list', () => merge.list(sessions.close));
  ipcMain.handle('reqs:drop', (_e, id) => requirements.dropReq(id, sessions.close));
  ipcMain.handle('reqs:set', (_e, id, to) => requirements.setReq(id, to, sessions.close));
  ipcMain.handle('reqs:restore', (_e, id) => requirements.restoreReq(id));
  ipcMain.handle('reqs:depend', (_e, id, other, remove) => requirements.dependReq(id, other, !!remove));
  ipcMain.handle('reqs:keep', (_e, id, keep) => requirements.keepReq(id, !!keep));
  ipcMain.handle('map:render', requirements.renderMap);
  // 用户旅程设为主干（合并前必跑它用到的功能点的测试集）：只由你在大图里点，rf.py 那边也只认应用的标记
  // 知识保鲜：哪些功能点可能过时、开核对会话、采纳或忽略它的建议、核对完（fresh.js）
  ipcMain.handle('fresh:overview', () => require('./fresh').overview());
  ipcMain.handle('fresh:check', (_e, id) => require('./fresh').check(String(id || '')));
  ipcMain.handle('fresh:show', (_e, id) => require('./fresh').show(String(id || '')));
  ipcMain.handle('fresh:adopt', (_e, id, ks) => require('./fresh').adopt(String(id || ''), ks));
  ipcMain.handle('fresh:ignore', (_e, id, ks) => require('./fresh').ignore(String(id || ''), ks));
  ipcMain.handle('fresh:done', (_e, id) => require('./fresh').done(String(id || '')));
  ipcMain.handle('map:trunk', (_e, id, on) => requirements.setTrunk(String(id || ''), !!on));

  // 会话和终端
  ipcMain.on('run:open', (_e, request) => {
    const root = state.project;
    sessions.open(request).catch((error) => {
      if (state.project !== root) return;
      send('term:data', { id: request.id, data: uiText('\r\n启动会话失败：', "\r\nCouldn't start session: ") + error.message + '\r\n' });
      send('term:exit', { id: request.id, code: 127 });
      if (request.say) merge.settled(request.id, 'exit', uiText('AI 会话启动失败。', "Couldn't start the AI session."));
    });
  });
  ipcMain.on('term:input', (_e, { id, data }) => sessions.input(id, data));
  ipcMain.on('term:resize', (_e, { id, cols, rows }) => sessions.resize(id, cols, rows));
  ipcMain.on('sync:now', (_e, id) => sessions.syncNow(id));
  ipcMain.on('viewing', (_e, id) => attention.setViewing(id));
  ipcMain.handle('split:approve', (_e, { id, key, orders }) => splits.approve(id, key, (run, text) => require('./inbox').post({ to: run.id, from: 'splits', key: 'split:' + key, text, open: false }), orders));

  // 问题
  ipcMain.handle('issues:list', () => issues.pushIssues());
  ipcMain.handle('issues:review', () => issues.reviewIssues());
  ipcMain.handle('issues:review-status', () => issues.reviewStatus());
  ipcMain.handle('issues:review-discard', () => issues.discardReview());
  ipcMain.handle('issues:review-apply', (_e, request) => issues.applyIssueReview(request || {}));
  ipcMain.handle('issues:new', (_e, request) => issues.newIssue(request || {}));
  ipcMain.handle('issues:reply', (_e, request) => issues.reply(request || {}));
  ipcMain.handle('issues:set', (_e, request) => issues.setIssue(request || {}));
  ipcMain.handle('issues:retry', (_e, id) => issues.retry(id));
  ipcMain.handle('issues:fix', (_e, ids, choice) => issues.startFix(ids, choice));
  ipcMain.handle('issues:probe', (_e, id) => issues.probe(id));
  ipcMain.handle('issues:confirm', (_e, id) => issues.confirm(id, sessions.close));
  ipcMain.handle('issues:unconfirm', (_e, id) => issues.unconfirm(id));
  ipcMain.handle('issues:reject', (_e, request) => issues.reject(request || {}));
  ipcMain.handle('issues:to-req', (_e, id) => issues.toReq(id));
  ipcMain.handle('issues:from-req', (_e, request) => issues.fromReq(request || {}));

  // 发布：只有用户在对话框里点了按钮才会推送；一次只做一件事
  ipcMain.handle('publish:current', () => publish.refresh());
  ipcMain.handle('publish:status', (_e, request) => publish.status(request || {}));
  ipcMain.handle('publish:run', (_e, request) => publish.run(request || {}));
  ipcMain.handle('publish:pull', () => publish.pull());
  ipcMain.handle('publish:ai-merge', () => publish.aiMerge());
  ipcMain.handle('publish:list', () => publish.list());
  ipcMain.handle('branch:list', () => branches.list());
  ipcMain.handle('branch:refresh', () => branches.refresh());
  ipcMain.handle('branch:switch', (_e, request) => branches.switchTo(request || {}));

  // 改动：已完成的需求、已解决的问题带来了哪些提交和文件（只读）
  ipcMain.handle('trace:get', (_e, ref) => trace.get(ref));
  ipcMain.handle('session:usage', (_e, ref) => sessionHistory.usage(ref));
  ipcMain.handle('session:replay', (_e, session, offset) => sessionHistory.replay(session, offset));
  // 总结：已完成的需求的总结和概括标签；「重新总结」在后台跑 rf.py summarize
  ipcMain.handle('summary:run', (_e, id) => summary.start(id));
  ipcMain.handle('summary:state', () => summary.view());

  // 测试集：列表、查看、运行由程序调用 rf.py test（不经过模型）；同意、不同意、过期、恢复、改挂靠、保存、看过了只在你点按钮时调用
  ipcMain.handle('tests:list', (_e, request) => testsets.list(request || {}));
  ipcMain.handle('tests:show', (_e, request) => testsets.show(request || {}));
  ipcMain.handle('tests:run', (_e, request) => testsets.run(request || {}));
  ipcMain.handle('tests:act', (_e, request) => testsets.act(request || {}));
  ipcMain.handle('tests:save', (_e, request) => testsets.save(request || {}));
  ipcMain.handle('tests:ai', (_e, request) => testsets.aiEdit(request || {}));
  ipcMain.handle('tests:before-done', (_e, id) => testsets.beforeDone(id));
  ipcMain.handle('tests:config', (_e, request) => testsets.config(request || {}));

  // 测试环境：查看给界面明文（只给界面）；保存、新建、复制、删除、选用、采纳建议只在你点按钮时调用；连通检查只做 TCP 连接
  ipcMain.handle('envs:list', () => envs.list());
  ipcMain.handle('envs:show', (_e, request) => envs.show(request || {}));
  ipcMain.handle('envs:save', (_e, request) => envs.save(request || {}));
  ipcMain.handle('envs:act', (_e, request) => envs.act(request || {}));
  ipcMain.handle('envs:scan', (_e, request) => envs.scan(request || {}));
  ipcMain.handle('envs:check', (_e, request) => envs.check(request || {}));
  ipcMain.handle('envs:resolve', (_e, request) => envs.resolve(request || {}));
  ipcMain.handle('envs:ai', (_e, request) => envs.aiFill(request || {}));
  // 「让 AI 来处理」和手动执行准备步骤（同意执行、允许 AI 带上保密值在 envs:act 里，只有你点了才调用）
  ipcMain.handle('envs:handle', (_e, request) => envs.aiHandle(request || {}));
  ipcMain.handle('envs:prep-run', (_e, request) => envs.prepRun(request || {}));

  // 人工验收和人工测试的服务：启动、停止、重启、日志由程序直接管（services.js），不经过模型；启动前要你同意的命令走 envs:act 的 approve
  ipcMain.handle('svc:view', (_e, request) => services.view(request || {}));
  ipcMain.handle('svc:plan', (_e, request) => services.plan(request || {}));
  ipcMain.handle('svc:start', (_e, request) => services.start(request || {}));
  ipcMain.handle('svc:stop', (_e, request) => services.stop(request || {}));
  ipcMain.handle('svc:restart', (_e, request) => services.restart(request || {}));
  ipcMain.handle('svc:fresh', (_e, request) => services.refresh({ scope: (request || {}).scope, trigger: 'open' })); // 打开人工验收标签：运行的不是最新代码就重启
  ipcMain.handle('svc:clear', (_e, request) => services.clearLogs(request || {}));
  ipcMain.handle('svc:open', (_e, request) => services.open(request || {}));
  // 逐条验收的「通过」「这里不对」（只在你点按钮时调用，rf.py 还校验应用的标记）、人工测试里「发现不对 → 记成问题」
  ipcMain.handle('accept:mark', (_e, request) => acceptance.mark(request || {}));
  ipcMain.handle('accept:issue', (_e, request) => acceptance.issue(request || {}));

  // 设计稿和图片
  ipcMain.on('design:open', (_e, { file, title }) => designs.openDesign(String(file || ''), String(title || '')));
  ipcMain.handle('image:pick', images.pickImages);
  ipcMain.handle('image:save', (_e, request) => images.saveImage(request));
}

module.exports = { register };

// 启动：显示项目名，然后按原来的顺序把各部分的事件绑上、开始加载。
// 全部放在这一个脚本里同步做完，免得主进程的消息在各个脚本加载的间隙里到达。
// macOS 上没有系统标题栏，顶栏要给红黄绿留位置；全屏时红黄绿收起，留白跟着去掉。
if (window.shell.platform === 'darwin') document.body.classList.add('mac');
window.shell.onFullscreen(function (full) { document.body.classList.toggle('fullscreen', !!full); });
window.shell.onAbout(function (version) {
  var dialog = $('about-dlg');
  $('about-version').textContent = version;
  if (!dialog.open) dialog.showModal();
});
var noProject = new URLSearchParams(location.search).has('empty');
$('project').addEventListener('click', function () { window.shell.projectMenu(); });
window.shell.onAppMenuAction(function (action) {
  if (document.querySelector('dialog[open]')) return;
  if (action === 'settings') { $('settings').click(); return; }
  if (noProject) return;
  if (action === 'new-req' && view('reqs') !== false) openNew();
  if (action === 'new-issue' && view('issues') !== false) openNewIssue();
});

if (noProject) {
  $('project').textContent = uiText('打开项目…', "Open project…");
  ['rail', 'map', 'status', 'sync'].forEach(function (id) { $(id).hidden = true; });
  $('no-project').hidden = false;
  wireSettings();
} else {
  window.shell.projectInfo().then(function (p) {
    if (p.tag) { document.title = p.tag + ' · RelayForge · ' + p.name; $('instance-tag').hidden = false; $('instance-tag').textContent = p.tag; $('instance-tag').title = p.tag + (p.userData ? uiText('\n数据目录：', "\nData directory: ") + p.userData : ''); if (p.userData) $('instance-tag').appendChild(el('small', '', uiText('数据目录 …/', "Data directory …/") + p.userData.split(/[\\/]/).filter(Boolean).slice(-3).join('/'))); }
    $('project').textContent = uiText('项目：', "Project: ") + p.name + ' ▾'; $('project').title = p.root + uiText('\n点这里切换或打开项目', "\nClick to switch or open a project"); showInitialization(p.initialization); showProjectDemo(p.demo); });
  wireTerminal();
  wireInbox();
  wireBoard();
  wireStageMenu();
  wireReqs();
  wireNewReq();
  wireDesigns();
  wirePanel();
  wireRequirementSubagent();
  wireAttention();
  wireLayout();
  wireMap();
  wireFresh();
  wireActions();
  wireMerge();
  wireHitl();
  wireCompose();
  wireIssues();
  wireIssueReview();
  wireTests();
  wireTestViewer();
  wireCases();
  wireEnvs();
  wireBench();

  wireSettings();
  wirePublish();
  wireBranches();
  wireTrace();
  wireSessionHistory();
  wireSummary();

  wireDemo();
  view('map');
}

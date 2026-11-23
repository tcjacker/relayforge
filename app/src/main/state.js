// 主进程里大家共用的东西：当前项目、主窗口、开着的会话，以及往界面发消息的 send()。
const state = {
  win: null,     // 主窗口
  project: null, // 当前项目的根目录
  runs: new Map(), // 需求编号或 session:会话编号 -> 正在跑的会话
  mergeChats: new Map(), // 「让 AI 来合并」开的聊天：会话编号 -> { project }（只记在内存里，应用重开就忘了）
  testChats: new Map(), // 测试集查看器「让 AI 改」开的聊天：会话编号 -> { project, set }（同上，只记在内存里）
  envChats: new Map(), // 测试环境「让 AI 读项目，帮我填」开的聊天：会话编号 -> { project, env }（同上）
  envFixChats: new Map(), // 「让 AI 来处理」开的聊天：会话编号 -> { project, kind, ref }（同上）
};

function send(channel, payload) { if (state.win && !state.win.isDestroyed()) state.win.webContents.send(channel, payload); }

module.exports = { state, send };

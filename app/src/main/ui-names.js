// 提示词里引用的界面按钮和标签：中文写的提示词里，它们的中文名字；英文界面上显示的是英文。
// 英文界面的会话要用英文界面上的名字回复，不能照抄提示词里的中文（问题 #039：立项会话把「确认立项，开始开发」原样写进英文回复）。
// 每个英文名字都是界面源码里真的显示的译文（html-copy.js 的 staticEnglish，或界面脚本里 uiText 的英文）；test_app_english_ui.cjs 会核对。
const NAMES = [
  ['确认立项，开始开发', 'Approve and start development'],
  ['让 AI 改', 'Ask AI to edit'],
  ['让 AI 来处理', 'Ask AI to investigate'],
  ['让 AI 读项目，帮我填', 'Ask AI to inspect the project'],
  ['让 AI 来合并', 'Ask AI to merge'],
  ['检查远端', 'Check remote'],
  ['跳过检查，仍然合并', 'Skip check and merge anyway'],
];

// 中文界面不需要；英文界面给出对照，附在会话的回复规则后面（sessions.js）
function guide(language) {
  if (language === 'zh-CN' || !NAMES.length) return '';
  return '英文界面里，下面这些按钮和标签的名字是英文的。提示词是中文写的，你的英文回复里提到它们时，要用英文界面上的名字，不要照抄中文：'
    + NAMES.map(([zh, en]) => `「${zh}」在英文界面上是「${en}」`).join('；') + '。';
}

module.exports = { NAMES, guide };

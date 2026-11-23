// 进程启动时用设置初始化语言，保存设置后更新原生窗口和通知的文案。
let language = 'zh-CN';

function setLanguage(value) { language = value; }

function uiText(zh, en, ...values) {
  const text = language === 'en' ? en : zh;
  return values.length ? text.replace(/\{(\d+)\}/g, (_, index) => String(values[index])) : text;
}

module.exports = { setLanguage, uiText };

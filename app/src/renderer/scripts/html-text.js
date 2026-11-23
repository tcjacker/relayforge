// 拼 HTML 时把文字转义（& < > " ' 都转，属性值里也安全）。界面和各个 *-model.js 共用这一份。
// 普通脚本，在 index.html 里最先加载；*-model.js 在 Node 的测试里被 require 时，从这里 require。
var HtmlText = (function () {
  var MAP = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  function esc(x) { return String(x == null ? '' : x).replace(/[&<>"']/g, function (c) { return MAP[c]; }); }
  return { esc: esc, text: uiText };
})();
if (typeof module !== 'undefined' && module.exports) module.exports = HtmlText;

// 用位置参数格式化文案。调用方仍需照常转义动态 HTML 内容。
function uiText(zh, en, ...values) {
  var english = typeof location !== 'undefined' && typeof URLSearchParams !== 'undefined' &&
    new URLSearchParams(location.search).get('language') === 'en';
  var text = english ? en : zh;
  return values.length ? text.replace(/\{(\d+)\}/g, function (_, index) { return String(values[index]); }) : text;
}

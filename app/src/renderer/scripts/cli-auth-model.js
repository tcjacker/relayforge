// 设置窗口每个 CLI 一行右边显示什么（R-20261121-1f0c）：不碰 DOM，Node 测试直接比对。
// 一行的状态 entry.state：checking 检查中、in 已登录、out 没登录、missing 未安装、desktop 由桌面应用提供、
// waiting 等待浏览器授权、failed 登录失败或超时。
var CliAuthModel = (function () {
  var uiText = (typeof HtmlText !== 'undefined' ? HtmlText : require('./html-text')).text;
  var NAMES = { claude: 'Claude', codex: 'ChatGPT / Codex' };

  function view(entry, agent) {
    var e = entry || { state: 'checking' };
    switch (e.state) {
      case 'in':
        return { tag: { kind: 'ok', text: uiText('已登录', "Signed in") }, button: null, sub: { text: e.account || '', error: false } };
      case 'out':
        return { tag: null, button: { action: 'login', text: uiText('登录', "Sign in") }, sub: { text: e.note || '', error: false } };
      case 'missing':
        return { tag: { kind: 'bad', text: uiText('未安装', "CLI not installed") }, button: null, sub: { text: '', error: false } };
      case 'desktop':
        return {
          tag: { kind: 'plain', text: uiText('在桌面应用里登录', "Sign in from the desktop app") }, button: null,
          sub: { text: uiText('由 ', "Provided by ") + (e.desktop || NAMES[agent] || '') + uiText(' 桌面应用提供，登录状态以桌面应用为准。', ". Check your sign-in status in its desktop app."), error: false },
        };
      case 'waiting':
        return { tag: { kind: 'wait', text: uiText('等待浏览器授权…', "Waiting for browser authorization…") }, button: { action: 'cancel', text: uiText('取消', "Cancel") }, sub: { text: '', error: false } };
      case 'failed':
        return { tag: null, button: { action: 'login', text: uiText('重新登录', "Sign in again") }, sub: { text: e.reason || uiText('登录没有完成。', "Sign-in was not completed."), error: true } };
      default:
        return { tag: { kind: 'checking', text: uiText('检查中…', "Checking…") }, button: null, sub: { text: '', error: false } };
    }
  }

  // 查状态的回答（或出错）变成一行的状态：查不到也给「登录」按钮，不会一直停在检查中
  function fromStatus(result) {
    if (!result || result.error) return { state: 'out', note: uiText('没能查到登录状态', "Couldn't check sign-in status") + (result && result.error ? '（' + result.error + '）' : '') + uiText('，可以重新登录。', ". You can try signing in again.") };
    return result;
  }

  // 登录命令的结果变成一行的状态：取消回到「登录」，失败或超时显示原因和「重新登录」
  function fromLogin(result) {
    if (!result || result.error) return { state: 'failed', reason: (result && result.error) || uiText('登录没有完成。', "Sign-in was not completed.") };
    if (result.state === 'cancelled') return { state: 'out' };
    if (result.state === 'in') return result;
    return { state: 'failed', reason: result.reason || uiText('登录没有完成。', "Sign-in was not completed.") };
  }

  return { view: view, fromStatus: fromStatus, fromLogin: fromLogin };
})();
if (typeof module !== 'undefined') module.exports = CliAuthModel;

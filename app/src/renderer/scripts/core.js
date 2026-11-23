// 界面共用的小工具和状态。所有脚本都是普通脚本，按 index.html 里的顺序加载，共用同一个全局作用域。
var $ = function (id) { return document.getElementById(id); };
var el = function (tag, cls, text) { var n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };
var reqs = [], past = [], current = null, terms = {}, states = {}, waiting = {}, running = {};
// 这个需求有没有能打开的会话：有自己的工作目录，或者已经完成、留着聊过的会话
var hasChat = function (r) { return !!(r && r.state !== 'dropped' && (r.worktree || (r.state === 'done' && r.sessions.length))); };
var WAIT = {
  approve: uiText('等你批准', 'Approval required'), answer: uiText('在等你回答', 'Waiting for your answer'),
  reply: uiText('回复完了', 'Replied'), test: uiText('测试交回了', 'Testing reported issues'),
  stuck: uiText('可能卡住了', 'May be stuck'), unread: uiText('程序消息未读', 'Unread program message'),
};
// 点了「确认完成」以后的合并队伍（主进程 merge.js 推过来）：编号 -> { phase, ahead, base, files, note }
var merging = {};
// 「重新检查并合并」：AI 还在会话里干活（合并、跑测试）时，主进程先回 confirm，问过你再强行合并；你取消就什么都不做（返回 null）
var retryMerge = function (id, where) {
  return window.shell.retryMerge(id).then(function (result) {
    if (!result || !result.confirm) return result;
    if (!window.confirm(where + uiText('里的 AI 还在干活（多半在跑测试），这一轮还没说完。它说完以后程序会自动检查并合并。\n\n现在合并会把这个会话收掉，没跑完的测试不再跑下去，之后可以自己再跑。\n\n仍然现在合并？', " is still running. RelayForge will check and merge once this turn finishes.\n\nMerging now closes this session and stops any running tests. You can run the tests again later.\n\nMerge now?"))) return null;
    return window.shell.retryMerge(id, true);
  });
};
var mergeText = function (m) { return m.phase === 'syncing' ? uiText('AI 在开发会话里合并 ', "The development session is merging ") + (m.base || uiText('目标分支', "the target branch")) : m.phase === 'stuck' ? uiText('合并卡住了，要你处理', "Merge needs your help") : m.phase === 'merging' ? uiText('正在合并', "Merging") : uiText('排队等合并', "Queued for merge") + (m.ahead ? uiText('（前面还有 ', " (") + m.ahead + uiText(' 个）', " ahead)") : ''); };
var tab = 'reqs', chosen = { reqs: null, chats: null }; // 「需求」和「日常聊天」各记各的选中项

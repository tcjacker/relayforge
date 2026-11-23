// 徽标只更新相关元素，收件箱刷新时不用重画会话列表或终端。
var inboxQueued = new Set();
var inboxPanels = new Map();

function refreshInboxBadges(container = document) {
  container.querySelectorAll('[data-inbox-run]').forEach(function (node) {
    node.hidden = !inboxQueued.has(node.dataset.inboxRun);
  });
}

function showInbox(panelId, id) {
  const panel = $(panelId);
  if (panel.dataset.session === (id || '')) return;
  panel.dataset.session = id || '';
  panel.hidden = !id;
  panel.open = false;
  const title = panel.querySelector('summary');
  title.innerHTML = HtmlText.esc(uiText('程序消息', 'Program messages')) + InboxModel.badge(id);
  refreshInboxBadges(panel);
  panel.querySelector('.inbox-list').replaceChildren();
  inboxPanels.delete(panel);
}

async function loadInbox(panel) {
  const id = panel.dataset.session;
  if (!id || !panel.open) return;
  const list = panel.querySelector('.inbox-list');
  const request = window.shell.inboxList(id);
  inboxPanels.set(panel, request);
  try {
    const messages = await request;
    if (inboxPanels.get(panel) !== request) return;
    const expanded = new Set(Array.from(list.querySelectorAll('details[open]'), (node) => node.dataset.inboxBody));
    const scroll = list.scrollTop;
    list.innerHTML = InboxModel.render(messages, expanded);
    list.scrollTop = scroll;
  } catch (_) {
    if (inboxPanels.get(panel) === request) list.textContent = uiText('读取程序消息失败。请收起面板再打开。', "Couldn't load program messages. Close the panel and reopen it.");
  }
}

function wireInbox() {
  const panels = [$('session-inbox'), $('issue-inbox')];
  const changes = new Map();
  let loading = true;
  window.shell.onInboxChanged(function ({ id, queued }) {
    if (loading) changes.set(id, queued);
    if (queued) inboxQueued.add(id); else inboxQueued.delete(id);
    refreshInboxBadges();
    panels.filter((panel) => panel.dataset.session === id).forEach(loadInbox);
  });
  window.shell.inboxQueued().then(function (ids) {
    inboxQueued = new Set(ids);
    for (const [id, queued] of changes) {
      if (queued) inboxQueued.add(id); else inboxQueued.delete(id);
    }
    refreshInboxBadges();
  }).catch(function (error) { console.error('读取排队中的程序消息失败', error); }).finally(function () {
    loading = false;
    changes.clear();
  });
  for (const panel of panels) {
    const fit = panel.id === 'issue-inbox' ? refitFix : refit;
    new ResizeObserver(function () { requestAnimationFrame(fit); }).observe(panel);
    panel.addEventListener('toggle', () => loadInbox(panel));
    panel.addEventListener('click', async function (event) {
      const button = event.target.closest('[data-inbox-withdraw]');
      if (!event.isTrusted || !button || button.disabled) return;
      button.disabled = true;
      try {
        if (await window.shell.inboxWithdraw(panel.dataset.session, button.dataset.inboxWithdraw)) return;
        say(uiText('暂时无法撤回。请查看消息的最新状态。', 'Unable to withdraw the message. Check its current status.'), true);
        loadInbox(panel);
      } catch (_) {
        say(uiText('撤回程序消息失败，请重试。', "Couldn't withdraw the program message. Try again."), true);
      } finally { button.disabled = false; }
    });
  }
}

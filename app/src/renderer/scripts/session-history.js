// 需求面板、已解决问题共用用量块和只读回放对话框。
var sessionUsageCache = new Map();
var replayRequest = null;
var sessionUsageMarkup = '';

function usageOf(ref) {
  var entry = sessionUsageCache.get(ref);
  if (!entry || (!entry.pending && Date.now() - entry.at > 30000)) {
    entry = { pending: true, data: entry && entry.data };
    sessionUsageCache.set(ref, entry);
    var received = function (data) {
      var previous = entry.data;
      Object.assign(entry, { data: data, pending: false, at: Date.now() });
      if (JSON.stringify(previous) === JSON.stringify(data)) return;
      if (current === ref) panelUsage();
      if (ref.startsWith('I-') && curIssue()?.id === ref) renderIssueDetail();
    };
    window.shell.sessionUsage(ref).then(received, () => received({ error: uiText('无法读取用量。', 'Could not load usage.') }));
  }
  return entry.data;
}

function panelUsage() {
  var r = byId(current), box = $('req-usage');
  box.hidden = !r;
  var markup = r ? SessionHistoryModel.usage(usageOf(r.id)) : '';
  if (markup !== sessionUsageMarkup) {
    sessionUsageMarkup = markup;
    box.innerHTML = markup;
  }
}

function issueUsageHtml(i) {
  return i.state === 'resolved' ? '<section class="trace issue-trace">' + SessionHistoryModel.usage(usageOf(i.id)) + '</section>' : '';
}

async function readReplay(session, offset) {
  var dlg = $('session-replay'), body = $('session-replay-body'), more = $('session-replay-more');
  var request = { session, offset };
  replayRequest = request;
  more.hidden = true;
  if (!offset) {
    body.innerHTML = '';
    body.parentElement.scrollTop = 0;
  }
  $('session-replay-status').textContent = uiText('正在读取…', 'Loading…');
  if (!dlg.open) dlg.showModal();
  var data = await window.shell.sessionReplay(session, offset).catch(() => ({ error: uiText('无法读取会话记录。', 'Could not load the session record.') }));
  if (replayRequest !== request || !dlg.open) return;
  $('session-replay-status').textContent = data.session || '';
  body.insertAdjacentHTML('beforeend', SessionHistoryModel.replay(data));
  request.next = data.next_offset;
  more.hidden = request.next == null;
}

function wireSessionHistory() {
  document.addEventListener('click', function (event) {
    var button = event.target.closest('[data-replay-session]');
    if (button) readReplay(button.dataset.replaySession, 0);
  });
  $('session-replay-more').addEventListener('click', function () {
    if (replayRequest?.next != null) readReplay(replayRequest.session, replayRequest.next);
  });
  setInterval(function () {
    var r = byId(current);
    if (r && (r.state === 'open' || r.state === 'draft') && $('req-usage').checkVisibility()) panelUsage();
  }, 30000);
}

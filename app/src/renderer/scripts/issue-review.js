// 问题页的「刷新」：让 AI 在后台复查待处理问题（主进程 issues.js 读进度推过来，界面只管画）。
// 关掉窗口复查照样进行，问题页顶上一直显示进度，列表里标出复查中、已复查；查完可以勾选、一次确认处理。
var IREVIEW = { status: { state: 'idle' }, applying: false };

// 列表每一行后面的复查标记（issues.js 画行时调用）
function iReviewMark(i) { return IssueReviewModel.rowMark(IREVIEW.status, i.id); }

function wireIssueReview() {
  var M = IssueReviewModel;
  var dialog = $('issue-review'), body = $('issue-review-results'), status = $('issue-review-status');
  var branch = $('issue-review-branch'), apply = $('issue-review-apply'), close = $('issue-review-close');
  var discard = $('issue-review-discard'), start = $('issue-review-start'), strip = $('issue-review-strip'), notice = $('issue-review-notice');

  function checked() {
    return Array.from(body.querySelectorAll('input:checked'), function (input) { return input.value; });
  }

  function updateApply() {
    var count = checked().length;
    apply.disabled = !count || IREVIEW.applying;
    apply.textContent = IREVIEW.applying ? uiText('正在处理…', "Applying…") : count ? uiText('确认处理 ', "Apply to ") + count + uiText(' 条', " issues") : uiText('确认处理', "Apply review");
  }

  function paintButton() {
    var b = M.button(IREVIEW.status);
    start.classList.toggle('running', b.running);
    start.title = b.title;
    start.querySelector('span').textContent = b.label;
    start.querySelector('i').hidden = !b.dot;
    // 在查或者有结果没看时，切到别的标签页也留着按钮（样式表按这个属性决定）
    $('issues').dataset.review = IREVIEW.status.state;
  }

  function paintStrip() {
    var s = M.strip(IREVIEW.status);
    if (!s || dialog.open) { strip.hidden = true; return; }
    strip.className = s.cls;
    strip.innerHTML = s.html;
    strip.hidden = false;
  }

  function paintDialog() {
    if (!dialog.open) return;
    var s = IREVIEW.status;
    branch.textContent = (s.branch || uiText('（不在任何分支上）', "(detached HEAD)")) + (s.commit ? ' · ' + s.commit.slice(0, 12) : '');
    discard.hidden = s.state !== 'ready';
    close.textContent = s.state === 'running' ? uiText('放到后台', "Run in background") : uiText('关闭', "Close");
    if (s.state === 'running') {
      status.textContent = '';
      body.innerHTML = M.progressHtml(s);
      apply.hidden = true;
    } else if (s.state === 'ready') {
      status.textContent = '';
      var keep = body.querySelector('[data-review-result]') && body.dataset.review === s.id ? new Set(checked()) : null;
      body.innerHTML = '<div data-review-result>' + M.resultsHtml(s) + '</div>';
      body.dataset.review = s.id;
      if (keep) body.querySelectorAll('input').forEach(function (input) { input.checked = keep.has(input.value); });
      apply.hidden = !M.pickable(s).length;
      updateApply();
    } else if (s.state === 'failed') {
      status.textContent = uiText('复查失败。', "Review failed.") + (s.error || '');
      body.replaceChildren();
      apply.hidden = true;
    } else {
      body.replaceChildren();
      apply.hidden = true;
    }
  }

  function paint() {
    paintButton(); paintStrip(); paintDialog();
    if (!$('issues').hidden) renderIssueRows();
  }

  function openDialog() {
    notice.hidden = true;
    if (!dialog.open) dialog.showModal();
    paint();
  }

  async function startOrOpen() {
    // 正在查、有结果没处理：只打开，不再开一次复查
    if (M.active(IREVIEW.status)) { openDialog(); return; }
    start.disabled = true;
    try {
      var job = await window.shell.reviewIssues();
      if (job.error) throw new Error(job.error);
      if (job.state === 'empty') {
        notice.textContent = job.message || uiText('当前没有待处理问题。', "There are no open issues.");
        notice.className = 'small';
        notice.hidden = false;
        return;
      }
      IREVIEW.status = job;
      openDialog();
    } catch (error) {
      notice.textContent = uiText('复查没有开始。', "The review did not start.") + error.message;
      notice.className = 'small ierror';
      notice.hidden = false;
    } finally {
      start.disabled = false;
    }
  }

  window.shell.onIssueReview(function (s) {
    IREVIEW.status = s || { state: 'idle' };
    if (IREVIEW.status.state === 'idle' && dialog.open && !IREVIEW.applying) dialog.close();
    paint();
  });

  start.addEventListener('click', startOrOpen);
  strip.addEventListener('click', async function (event) {
    var act = event.target.closest('[data-review]');
    if (!act) return;
    if (act.dataset.review === 'open') openDialog();
    if (act.dataset.review === 'dismiss') await window.shell.discardReview();
  });
  body.addEventListener('change', updateApply);
  body.addEventListener('click', function (event) {
    var go = event.target.closest('[data-review="goto"]');
    if (!go) return;
    event.preventDefault();
    dialog.close();
    view('issues', go.dataset.id);
  });
  close.addEventListener('click', function () { dialog.close(); });
  dialog.addEventListener('cancel', function (event) { if (IREVIEW.applying) event.preventDefault(); });
  dialog.addEventListener('close', paint);
  // 不处理，关掉结果：问题的状态都不变，下次点「刷新」重新复查
  discard.addEventListener('click', async function () {
    var done = await window.shell.discardReview();
    if (done && done.error) { status.textContent = done.error; return; }
    dialog.close();
  });
  apply.addEventListener('click', async function () {
    var s = IREVIEW.status, ids = checked();
    if (s.state !== 'ready' || IREVIEW.applying || !ids.length) return;
    IREVIEW.applying = true;
    close.disabled = discard.disabled = true;
    body.querySelectorAll('input').forEach(function (input) { input.disabled = true; });
    updateApply();
    try {
      var done = await window.shell.applyIssueReview({ review: s.id, selected: ids });
      if (done.error) throw new Error(done.error);
      dialog.close();
      notice.textContent = M.appliedNote(done);
      notice.className = 'small' + (done.skipped && done.skipped.length ? ' warn' : ' ok');
      notice.hidden = false;
    } catch (error) {
      status.textContent = uiText('确认处理失败。', "Couldn't apply the review.") + error.message;
      body.querySelectorAll('input').forEach(function (input) { input.disabled = false; });
    } finally {
      IREVIEW.applying = false;
      close.disabled = discard.disabled = false;
      updateApply();
    }
  });
  window.shell.reviewStatus();
  paint();
}

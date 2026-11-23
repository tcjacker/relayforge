// 「总结」块：已完成（和已取消）的需求的右边面板里。内容由 SummaryModel 画，后台总结的进度由主进程 summary.js 推来。
var SUMJOB = { running: [], queued: [], failed: {} };

function panelSummary() {
  var box = $('req-summary'), r = byId(current);
  var h = r ? SummaryModel.html(r, SUMJOB) : '';
  box.hidden = !h; box.innerHTML = h;
}

function wireSummary() {
  window.shell.onSummary(function (job) { SUMJOB = job || SUMJOB; panelSummary(); });
  window.shell.summaryState().then(function (job) { if (job) { SUMJOB = job; panelSummary(); } });
  $('req-summary').addEventListener('click', function (e) {
    var b = e.target.closest('[data-summarize]');
    if (!b) return;
    b.disabled = true;
    window.shell.summarize(b.dataset.summarize).then(function (job) { if (job && !job.error) SUMJOB = job; panelSummary(); });
  });
}

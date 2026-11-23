// 往聊天框里放内容：按「粘贴」的方式送进去，Claude Code 会把它当成一整段，不会逐行提交
var canCompose = function () { return !!(current && (pastOf(current) || hasChat(byId(current)))); };
var paste = function (text) { var key = typeof termOf === 'function' ? termOf(current) : current; if (!canCompose() || !terms[key]) return; window.shell.input(key, '\x1b[200~' + text + '\x1b[201~'); terms[key].term.focus(); };
var attach = function (paths) { paths = paths.filter(Boolean); if (paths.length) paste(paths.map(function (p) { return /[^\w./-]/.test(p) ? "'" + p.replace(/'/g, "'\\''") + "'" : p; }).join(' ') + ' '); };
var isImage = function (f) { return /^image\//.test(f.type) || /\.(png|jpe?g|gif|webp)$/i.test(f.name || ''); };
var toPaths = function (files) {
  return Promise.all(files.filter(isImage).map(function (f) {
    var known = window.shell.pathOf(f);
    return known || f.arrayBuffer().then(function (bytes) { return window.shell.saveImage(f.name || 'image.png', bytes); });
  }));
};
var chat = $('chat'), depth = 0;
var hasFiles = function (e) { return canCompose() && e.dataTransfer && Array.prototype.indexOf.call(e.dataTransfer.types, 'Files') >= 0; };

// 在右边选中文字，旁边出现「填到聊天框」
var quote = $('quote'), picked = '';

// 绑定事件、开始加载：由 main.js 按顺序调用
function wireCompose() {
  window.rfAttach = attach;

  $('image').addEventListener('click', function () { window.shell.pickImages().then(attach); });
  window.addEventListener('dragover', function (e) { e.preventDefault(); });
  window.addEventListener('drop', function (e) { e.preventDefault(); });
  chat.addEventListener('dragenter', function (e) { if (hasFiles(e)) { depth++; chat.classList.add('over'); } });
  chat.addEventListener('dragleave', function () { if (--depth <= 0) { depth = 0; chat.classList.remove('over'); } });
  chat.addEventListener('drop', function (e) { depth = 0; chat.classList.remove('over'); if (canCompose()) toPaths(Array.prototype.slice.call(e.dataTransfer.files)).then(attach); });
  document.addEventListener('paste', function (e) {
    var files = Array.prototype.slice.call((e.clipboardData || {}).files || []).filter(isImage);
    if (!$('issues').hidden || $('issue-dlg').open) return; // 问题页有自己的粘贴（issue-actions.js）
    if (!files.length || (!canCompose() && !dlg.open)) return;
    e.preventDefault(); e.stopPropagation();
    toPaths(files).then(dlg.open ? addDrafts : attach);
  }, true);
  document.addEventListener('mouseup', function (e) {
    if (e.target === quote) return;
    setTimeout(function () {
      var sel = window.getSelection(), text = sel ? String(sel).trim() : '';
      if (!canCompose() || !text || !sel.rangeCount || !$('side').contains(sel.getRangeAt(0).commonAncestorContainer)) { quote.hidden = true; return; }
      var box = sel.getRangeAt(0).getBoundingClientRect();
      picked = text;
      quote.hidden = false;
      quote.style.top = Math.max(8, box.top - 30) + 'px';
      quote.style.left = Math.min(window.innerWidth - 110, Math.max(8, box.left)) + 'px';
    }, 0);
  });
  quote.addEventListener('click', function () {
    paste('「' + picked.replace(/\s*\n\s*/g, ' ') + '」 ');
    quote.hidden = true; window.getSelection().removeAllRanges();
  });
}

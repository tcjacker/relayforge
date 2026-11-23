// 三栏之间可以拖动调整宽度，下次打开还是上次的宽度
var main = document.querySelector('main');
var widths = { left: 240, right: 380 };
try { var saved = JSON.parse(localStorage.getItem('widths') || '{}'); if (saved.left) widths.left = saved.left; if (saved.right) widths.right = saved.right; } catch (e) {}
function applyWidths() {
  var room = Math.max(0, main.clientWidth - 320); // 中间的聊天至少留 320
  widths.left = Math.max(160, Math.min(widths.left, 520, Math.max(160, room - 240)));
  widths.right = Math.max(240, Math.min(widths.right, 720, Math.max(240, room - widths.left)));
  main.style.setProperty('--left', widths.left + 'px'); main.style.setProperty('--right', widths.right + 'px');
}

// 绑定事件、开始加载：由 main.js 按顺序调用
function wireLayout() {
  applyWidths();
  window.addEventListener('resize', applyWidths);
  [['split-left', 'left', 1], ['split-right', 'right', -1]].forEach(function (spec) {
    var bar = $(spec[0]), startX = 0, startW = 0;
    bar.addEventListener('pointerdown', function (e) { startX = e.clientX; startW = widths[spec[1]]; bar.setPointerCapture(e.pointerId); bar.classList.add('drag'); e.preventDefault(); });
    bar.addEventListener('pointermove', function (e) {
      if (!bar.hasPointerCapture(e.pointerId)) return;
      widths[spec[1]] = startW + (e.clientX - startX) * spec[2];
      applyWidths(); refit();
    });
    var end = function (e) { if (!bar.hasPointerCapture(e.pointerId)) return; bar.releasePointerCapture(e.pointerId); bar.classList.remove('drag'); try { localStorage.setItem('widths', JSON.stringify(widths)); } catch (err) {} refit(); };
    bar.addEventListener('pointerup', end); bar.addEventListener('pointercancel', end);
    bar.addEventListener('dblclick', function () { widths[spec[1]] = spec[1] === 'left' ? 240 : 380; applyWidths(); refit(); try { localStorage.setItem('widths', JSON.stringify(widths)); } catch (err) {} });
  });
}

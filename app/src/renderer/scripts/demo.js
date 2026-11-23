function showProjectDemo(demo) { $('demo-guide').hidden = !demo; }
function showDemoSession(on) { $('demo-session').hidden = !on; }
function wireDemo() {
  window.shell.onState(function (p) {
    if (terms[p.id]) { terms[p.id].demo = !!p.demo; terms[p.id].term.options.disableStdin = !!p.demo; }
    if (p.id === current) { showDemoSession(!!p.demo); refit(); }
  });
  $('demo-live').addEventListener('click', function () {
    var t = terms[current];
    if (!t || !t.demo) return;
    t.demo = false; t.term.options.disableStdin = false; t.term.reset();
    delete states[current]; showDemoSession(false); panel(); refit();
    window.shell.open({ id: current, fresh: true, blank: !!pastOf(current), title: (byId(current) || pastOf(current) || {}).title, cols: t.term.cols, rows: t.term.rows });
    t.term.focus();
  });
  $('map-demo-live').addEventListener('click', function () {
    if (!mapEdit || !mapEdit.term) return;
    mapEdit.term.options.disableStdin = false; mapEdit.term.reset(); $('map-demo-live').hidden = true;
    window.shell.open({ id: MAP_EDIT, fresh: true, cols: mapEdit.term.cols, rows: mapEdit.term.rows });
    mapEdit.term.focus();
  });
}

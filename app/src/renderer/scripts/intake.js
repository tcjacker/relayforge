// 立项：清晰度由程序从需求文件算出，从「想法」一级一级往上走
var levelName = function (r) { var l = (r.ladder || []).filter(function (x) { return x.key === r.level; })[0]; return l ? l.name : ''; };
function levelRow(r) {
  var row = el('div', 'steps');
  (r.ladder || []).forEach(function (l) { var i = el('i', l.later ? 'later' : l.skipped ? 'skipped' : l.reached ? 'on' : ''); i.title = l.name + (l.later ? uiText('（之后支持）', " (later)") : l.skipped ? uiText('：跳过了', ": skipped") : l.reached ? '' : uiText('：还没到', ": upcoming")); row.appendChild(i); });
  row.appendChild(el('span', 'level', uiText('清晰度：', "Clarity: ") + levelName(r)));
  return row;
}
function ladder(r) {
  $('ladder').replaceChildren.apply($('ladder'), (r.ladder || []).map(function (l) {
    var li = el('li', (l.reached ? 'reached' : '') + (l.skipped ? ' skipped' : '') + (l.later ? ' later' : '') + (l.key === r.level ? ' now' : ''));
    if (l.key === r.level) li.setAttribute('aria-current', 'step');
    li.appendChild(el('span', 'mark', l.later ? '–' : l.skipped ? '↷' : l.reached ? '✓' : '○'));
    li.appendChild(el('b', '', l.name));
    li.appendChild(el('small', '', l.later ? l.need + uiText('，之后支持，现在跳过', " · deferred") : l.skipped ? uiText('跳过了：', "Skipped: ") + l.skipped : l.key === 'design' && l.designs ? l.designs + uiText(' 份设计稿，在下面点开看', " designs available below") : l.reached ? l.need : uiText('还差：', "Still needed: ") + (l.missing.length ? l.missing.join('、') : l.need)));
    return li;
  }));
}
function assessmentHighlight(block) {
  var section = el('section', 'assessment-highlight');
  section.appendChild(el('h2', '', block.title));
  if (block.text) section.appendChild(el('p', '', block.text));
  if (block.scenarios) {
    var cards = section.appendChild(el('div', 'assessment-scenarios'));
    block.scenarios.forEach(function (scenario) {
      var card = cards.appendChild(el('article', ''));
      card.appendChild(el('h3', '', scenario.title));
      card.appendChild(el('p', '', scenario.text));
    });
  }
  if (block.steps) {
    var path = section.appendChild(el('ol', 'assessment-path'));
    block.steps.forEach(function (step) { path.appendChild(el('li', '', step)); });
  }
  return section;
}

function intakeAssessment(r) {
  var box = $('intake-assessment');
  if (!r) {
    box.replaceChildren();
    delete box.dataset.requirement;
    delete box.dataset.content;
    return;
  }
  var a = r.intake_assessment || {}, designs = r.worktree ? r.designs || [] : [];
  var same = box.dataset.requirement === r.id;
  var content = JSON.stringify([a, designs.map(function (d) { return [d.id, d.title]; })]);
  if (same && box.dataset.content === content) return;
  var previous = box.querySelector('.assessment-other');
  var focused = same && previous && previous.firstElementChild === document.activeElement;
  box.dataset.requirement = r.id;
  box.dataset.content = content;
  var nodes = [];
  if (a.risk) {
    var risk = a.risk, confirmed = risk.status === 'confirmed';
    var warning = el('section', 'assessment-risk' + (confirmed ? ' confirmed' : ''));
    warning.appendChild(el('h2', '', (confirmed ? uiText('已确认高风险', "High risk confirmed") : uiText('风险待核实', "Risk needs verification")) + ' · ' + risk.title));
    warning.appendChild(el('p', '', risk.consequence));
    warning.appendChild(el('p', '', (confirmed ? uiText('核实依据：', "Evidence: ") : uiText('缺少的证据：', "Missing evidence: ")) + risk.evidence));
    nodes.push(warning);
  }
  (a.highlights || []).forEach(function (highlight) { nodes.push(assessmentHighlight(highlight)); });
  if (a.decisions && a.decisions.length) {
    var decision = el('section', 'assessment-decision');
    decision.appendChild(el('h2', '', uiText('现在需要你决定', "Needs your decision")));
    decision.appendChild(impactList(a.decisions));
    nodes.push(decision);
  }
  designs.forEach(function (design) {
    var button = el('button', 'obj assessment-design', uiText('设计稿 · ', "Design · ") + design.title);
    button.type = 'button';
    button.onclick = function () { openTab(byId(r.id), design.id); };
    nodes.push(button);
  });
  var other = el('details', 'assessment-other');
  other.open = !!(same && previous && previous.open);
  other.appendChild(el('summary', '', uiText('查看其他评估', "View other assessments")));
  var groups = other.appendChild(el('dl', ''));
  [
    [uiText('AI 已决定 · 无需确认', "AI decisions · No approval needed"), a.decided, uiText('暂未记录 AI 已决定的事项。', "No AI decisions recorded.")],
    [uiText('待细化', "To refine"), a.refine, uiText('暂未记录待细化的事项。', "Nothing to refine yet.")],
  ].forEach(function (group) {
    groups.appendChild(el('dt', '', group[0]));
    var body = groups.appendChild(el('dd', ''));
    body.appendChild(group[1] && group[1].length ? impactList(group[1]) : el('p', 'muted', group[2]));
  });
  nodes.push(other);
  box.replaceChildren.apply(box, nodes);
  if (focused) other.firstElementChild.focus({ preventScroll: true });
}

function impact(r) {
  var body = $('impact-body'), b = r.brief, out = [];
  var block = function (head, lines, cls) {
    if (!lines.length) return;
    out.push(el('h3', cls || '', head), impactList(lines));
  };
  if (b) {
    block(uiText('引用的功能点和模块', "Referenced features and modules"), b.known.map(function (k) { return k.id + ' ' + k.title + (k.kind === 'module' ? uiText('（模块）', " (module)") : k.module ? '（' + k.module + '）' : '') + (k.status === 'current' ? '' : uiText('（待确认）', " (awaiting confirmation)")); }));
    block(uiText('新增的功能点', "New features"), b.proposed.map(function (x) { return x.text + (x.module ? '' : uiText('（还没说归到哪个模块）', " (no module assigned)")); }));
    block(uiText('事实里没有这些编号', "IDs missing from project facts"), b.unknown, 'warn');
    block(uiText('依赖它们的功能点，可能受影响', "Dependent features that may be affected"), b.dependents.map(function (d) { return d.id + ' ' + d.title + uiText(' ← 依赖 ', " ← depends on ") + d.on.join('、'); }), 'warn');
    if (b.decisions.length) {
      var previous = body.querySelector('.impact-decisions');
      var details = el('details', 'impact-decisions');
      details.dataset.requirement = r.id;
      details.open = !!(previous && previous.dataset.requirement === r.id && previous.open);
      details.appendChild(el('summary', '', uiText('管着它们的现行裁定，注意别冲突', "Applicable decisions to check for conflicts")));
      details.appendChild(impactList(b.decisions.map(function (d) { return d.id + ' ' + d.title + (d.decision ? '：' + d.decision : ''); })));
      out.push(details);
    }
    var istate = { todo: uiText('待处理', "Open"), fixing: uiText('处理中', "In progress"), review: uiText('待你确认', "Awaiting confirmation") };
    block(uiText('这些功能点身上还没解决的问题', "Unresolved issues on these features"), (b.issues || []).map(function (i) { return '#' + i.id.slice(2) + ' ' + i.title + '（' + i.feature + '，' + (istate[i.state] || i.state) + (i.deferred ? uiText('，以后再说', " · discuss later") : '') + '）'; }), 'warn');
  }
  if (!out.length) out.push(el('p', 'empty', uiText('要改的功能点写下编号以后，这里列出依赖它们的功能点和相关的裁定。', "List the features this requirement will change to see their dependents and related decisions.")));
  body.replaceChildren.apply(body, out);
}
function impactList(lines) {
  var ul = el('ul', 'plain');
  lines.forEach(function (t) { ul.appendChild(el('li', '', t)); });
  return ul;
}

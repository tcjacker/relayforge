// 右侧面板
var MARK = { done: '✓', doing: '●', todo: '○', dropped: '–' };
var splitViews = new Map(), splitApproving = new Set(), splitOrders = new Map(); // splitOrders：建议编号 → { 序号: after|before|none }，你在右侧改过的先后
function panelSplit(open) {
  var id = current, split = open && splitViews.get(id), box = $('split-proposal'), receipt = $('split-receipt');
  var done = split && split.state === 'done', items = split && split.items || [];
  var created = items.filter(function (entry) { return entry.created; }).length;
  var pending = split && split.state === 'pending';
  var adding = items.filter(function (entry) { return !entry.requirement; }).length;
  var wasHidden = receipt.hidden;
  box.hidden = !split;
  receipt.hidden = !done;
  if (wasHidden !== receipt.hidden) requestAnimationFrame(refit);
  if (!split) return;
  $('split-heading').textContent = done ? uiText('拆分结果', "Split results") : uiText('拆分建议', "Split proposal");
  $('split-count').textContent = done ? uiText('已创建 ', "Created ") + created + uiText(' 条立项中需求', " requirements in planning") + (items.length > created ? uiText('，复用 ', ", reused ") + (items.length - created) + uiText(' 条已有需求', " existing requirements") : '') : pending ?
    uiText('建议新增 ', "Proposes ") + adding + uiText(' 条需求', " new requirements") + (items.length > adding ? uiText('，复用 ', " and reuses ") + (items.length - adding) + uiText(' 条已有需求。', " existing requirements.") : '。') + (adding ? uiText('确认后加入「立项中」。', "Approval moves new requirements to Planning.") : '') : '';
  $('split-items').replaceChildren.apply($('split-items'), items.map(function (entry) {
    var row = el('li', '');
    if (done) {
      var link = el('button', 'obj', entry.title + ' · ' + entry.requirement);
      link.type = 'button';
      link.onclick = function () { view('reqs'); select(entry.requirement); };
      row.appendChild(link);
      if (split.parent || entry.order !== 'none') row.appendChild(el('small', 'muted', DepsModel.ORDER_WORDS[entry.order] || DepsModel.ORDER_WORDS.none));
    } else {
      row.appendChild(el('b', '', entry.title + (entry.requirement ? ' · ' + entry.requirement : '')));
      row.appendChild(el('small', 'muted', entry.scope));
      if (split.parent && pending) row.appendChild(splitOrderPicker(split, entry));
    }
    // 和当前需求的先后要用到什么；还要等的（已有的需求，或者这次一起拆出的另一条）；从当前需求接过的前置需求。每条都写要用到什么
    var needOf = function (w) { return w.need ? uiText('（要用到', " (needs ") + w.need + '）' : ''; };
    var picked = (splitOrders.get(split.id) || {})[String(entry.key).split('-').pop()]; // 你改过先后：AI 那句按原来的方向写的，不再显示（同意时也不用）
    if (entry.order_need && (!picked || picked === entry.order)) row.appendChild(el('small', 'muted', (entry.order === 'before' ? uiText('当前需求要用到它的：', "This requirement needs: ") : uiText('要用到当前需求的：', "Needs from the current requirement: ")) + entry.order_need));
    (entry.waits_info || []).forEach(function (w) { row.appendChild(el('small', 'dep-note dep-wait', (split.parent ? uiText('还要等', "Also waits for ") : uiText('要等', "Waits for ")) + '《' + w.title + '》' + needOf(w))); });
    (entry.takes_info || []).forEach(function (w) {
      row.appendChild(el('small', 'dep-note dep-ok', uiText('接过《', "Takes over from “") + w.title + '》' + needOf(w) + '：' + (done ? uiText('这个需求不再等它', "This requirement no longer waits for it") : uiText('同意后这个需求不用再等它', "Approval removes this dependency"))));
    });
    return row;
  }));
  $('split-problem').hidden = !pending || !split.problem;
  $('split-problem').textContent = split.problem || '';
  $('split-approve').hidden = !pending;
  $('split-approve').disabled = splitApproving.has(id);
  $('split-error').hidden = !split.error;
  $('split-error').textContent = split.error || '';
  receipt.textContent = done ? $('split-count').textContent + '。' + items.map(function (entry) {
    return entry.title + '（' + entry.requirement + '）';
  }).join('、') : '';
}
// 拆分建议里每一条和当前需求的先后：AI 先选好，你可以改；同意时一起交给程序（splitOrders）
function splitOrderPicker(split, entry) {
  var index = String(entry.key).split('-').pop(), chosen = (splitOrders.get(split.id) || {})[index] || entry.order || 'none';
  var box = el('div', 'split-order'); box.setAttribute('role', 'group'); box.setAttribute('aria-label', '「' + entry.title + uiText('」和当前需求的先后', "” and the current requirement"));
  ['after', 'before', 'none'].forEach(function (order) {
    var b = el('button', order === chosen ? 'on' : '', DepsModel.ORDER_WORDS[order]); b.type = 'button'; b.setAttribute('aria-pressed', String(order === chosen));
    b.addEventListener('click', function () {
      var mine = splitOrders.get(split.id) || {}; mine[index] = order; splitOrders.set(split.id, mine); panel();
    });
    box.appendChild(b);
  });
  return box;
}
var item = function (cls, mark, title, note) {
  var li = el('li', cls); li.appendChild(el('span', 'mark', mark));
  var body = el('span'); body.appendChild(el('b', '', title));
  if (note) body.appendChild(el('small', '', note));
  li.appendChild(body); return li;
};
// 和其他需求的改动（conflicts-model.js）：会冲突的、改到同一处的，每条带全部文件和怎么办。只在进行中的需求上显示
var conflictsData = null, predictions = {};
// 立项中的需求还没改代码：按历史预测（rf.py conflicts --predict），点开时问一次主进程（它一分钟内不重算），回来再画
function predictionFor(r) {
  var hit = predictions[r.id];
  if (!hit || Date.now() - hit.at > 60000) {
    predictions[r.id] = { at: Date.now(), data: hit ? hit.data : null };
    window.shell.predictConflicts(r.id).then(function (data) { predictions[r.id] = { at: Date.now(), data: data }; if (current === r.id) panel(); });
  }
  return predictions[r.id].data;
}
function panelOverlap(r) {
  var draft = !!(r && !r.loose && r.state === 'draft');
  var pred = draft ? predictionFor(r) : null;
  var lines = draft ? ConflictsModel.predictLines(pred) : r && !r.loose && r.state === 'open' ? ConflictsModel.panelLines(conflictsData, r.id) : [];
  $('overlap').hidden = !lines.length;
  if (!lines.length) return;
  $('overlap-hint').textContent = draft ? ConflictsModel.predictSummary(pred) : ConflictsModel.summary(conflictsData, r.id);
  $('overlap-list').replaceChildren.apply($('overlap-list'), lines.map(function (n) {
    var li = el('li', 'overlap ' + n.cls);
    li.appendChild(el('b', '', n.text));
    if (n.files.length > 3) li.appendChild(el('small', 'muted', n.files.join('、')));
    li.appendChild(el('p', 'muted', n.hint));
    return li;
  }));
}
// 前置需求（deps-model.js 算说法）：列出来、能去掉；没有时缩成一行「＋ 添加前置需求」。立项中、进行中可以改，完成和取消以后只读
var depsPicking = null, depsBusy = false;
function panelDeps(r) {
  var box = $('deps'), list = (r && r.depends_on) || [], editable = !!(r && !r.loose && (r.state === 'draft' || r.state === 'open'));
  box.hidden = !r || r.loose || (!editable && !list.length);
  if (box.hidden) return;
  $('deps-hint').textContent = !list.length ? '' : r.state === 'draft' ? uiText('它们确认完成后，这个需求才能开始开发', "Development can start after the prerequisites are complete") : r.state === 'open' ? uiText('它们确认完成后，这个需求才能确认完成', "This requirement can be marked complete after the prerequisites are complete") : '';
  $('deps-list').replaceChildren.apply($('deps-list'), list.map(function (d) {
    var li = el('li', 'dep-row' + (d.state === 'dropped' ? ' bad' : d.state === 'done' ? ' ok' : ''));
    var link = el('button', 'link', d.title); link.type = 'button'; link.title = uiText('打开《', "Open “") + d.title + '》';
    link.addEventListener('click', function () { select(d.id); });
    li.appendChild(link);
    if (d.need) li.appendChild(el('small', 'dep-need', uiText('要用到：', "Needs: ") + d.need));
    li.appendChild(el('span', 'chip', DepsModel.depState(d)));
    if (editable && d.state !== 'done' && d.state !== 'dropped') li.appendChild(splitAwayButton(r, d, false));
    if (editable) {
      var x = el('button', 'icon dep-x', '✕'); x.type = 'button'; x.title = uiText('去掉这条前置需求', "Remove prerequisite"); x.setAttribute('aria-label', uiText('去掉前置需求《', "Remove prerequisite “") + d.title + '》');
      x.addEventListener('click', function () { setDependency(r.id, d.id, true); });
      li.appendChild(x);
    }
    return li;
  }).concat((r.dependents || []).length && editable ? [el('li', 'dep-row muted', uiText('在等它：', "Waiting for this: ") + r.dependents.map(function (x) { return '《' + x.title + '》' + (x.need ? uiText('（要用到', " (needs ") + x.need + '）' : ''); }).join('、'))] : []));
  var sync = DepsModel.syncNote(r);
  $('deps-sync').hidden = !sync || r.state !== 'open';
  if (sync) { $('deps-sync').textContent = sync.text; $('deps-sync').className = 'dep-note ' + sync.cls; }
  var dropped = DepsModel.waitingOn(r).filter(function (d) { return d.state === 'dropped'; });
  $('deps-dropped').hidden = !editable || !dropped.length;
  if (dropped.length) $('deps-dropped-note').textContent = uiText('前置需求', "Prerequisite ") + dropped.map(function (d) { return '《' + d.title + '》'; }).join('、') + uiText('已取消，这个需求等不到它了。', " was cancelled and can no longer complete.");
  $('deps-add').hidden = !editable;
  $('deps-add').textContent = depsPicking === r.id ? uiText('收起', "Collapse") : uiText('＋ 添加前置需求', "＋ Add prerequisite");
  $('deps-picker').hidden = !editable || depsPicking !== r.id;
  if (depsPicking === r.id) depsOptions(r);
}
function depsOptions(r) {
  $('deps-options').replaceChildren.apply($('deps-options'), DepsModel.pickerOptions(r, reqs, $('deps-search').value).map(function (o) {
    var li = el('li', o.why ? 'no' : '');
    var b = el('button', '', o.title); b.type = 'button'; b.disabled = !!o.why || depsBusy;
    if (!o.why) b.addEventListener('click', function () { setDependency(r.id, o.id, false); });
    li.appendChild(b);
    li.appendChild(el('small', 'muted', o.why || o.stage));
    if (o.cycle) li.appendChild(splitAwayButton(r, o, true));
    return li;
  }));
}
// 「把要等它的部分拆出去」：交给这个需求的会话（立项中是立项会话，进行中是开发会话），它提拆分建议，你在右侧同意
function splitAwayButton(r, d, cycle) {
  var b = el('button', 'dep-split', cycle ? uiText('改为拆出去', "Split instead") : uiText('拆出去', "Split out"));
  b.type = 'button';
  b.title = cycle ? uiText('不设这条前置需求，把这个需求里要用到《', "Split the work that needs “") + d.title + uiText('》的部分拆成新需求，由它来等', "” into a new requirement with that dependency")
    : uiText('把这个需求里要等《', "Split the work waiting for “") + d.title + uiText('》的部分拆成新需求，由它来等；这个需求不再等它', "” into a new requirement so the current requirement can proceed");
  b.addEventListener('click', function () {
    if (cycle) { depsPicking = null; $('deps-search').value = ''; }
    sayTo(r.id, DepsModel.splitAway(d, cycle));
    panel();
  });
  return b;
}
// 替你往一个会话里说一句话：开着的由主进程直接输进去，没开的在后台打开它、这句作为第一句话
function sayTo(id, say) {
  if (terms[id] && !terms[id].dead) window.shell.open({ id: id, say: say });
  else { terminal(id, say); show(typeof termOf === 'function' ? termOf(current) : current); list(); }
}
async function setDependency(id, other, remove) {
  if (depsBusy) return;
  depsBusy = true; $('deps-error').hidden = true;
  try {
    var result = await window.shell.dependReq(id, other, remove);
    if (result && result.error) { $('deps-error').textContent = result.error; $('deps-error').hidden = false; }
    else if (!remove) { depsPicking = null; $('deps-search').value = ''; }
  } catch (error) { $('deps-error').textContent = error.message; $('deps-error').hidden = false; }
  finally { depsBusy = false; if (id === current) panel(); }
}

// 每个需求右边正在看哪个阶段标签：{ viewing, stage }（stage-tabs-model.js）。选中需求时清掉，打开就停在它现在的阶段
var stageView = {};
function viewTab(id, tabName) { var r = byId(id); if (r) { stageView[id] = { viewing: tabName, stage: stageOf(r) }; if (id === current) panel(); } }
function panel() {
  var s0 = pastOf(current), p = states[current] || {};
  var r = byId(current) || (s0 && { title: s0.title, loose: true, state: 'open', branch: '', sessions: [], sections: s0.first ? { '开头说的话': [s0.first] } : {} }), open = !!(r && (hasChat(r) || r.loose));
  $('empty').hidden = !!r; $('panel').hidden = !r;
  $('empty').textContent = tab === 'chats' ? uiText('选中一个聊天后，这里显示它的进展。', "Select a chat to see its progress here.") : uiText('选中一个需求后，这里显示它的内容和进展。', "Select a requirement to see its details and progress.");
  $('sync').disabled = !open || !!p.syncing; $('sync').classList.toggle('busy', !!(open && p.syncing));
  $('image').hidden = !open || !!p.demo;
  if (typeof showDemoSession === 'function') showDemoSession(!!p.demo);
  $('sync').disabled = $('sync').disabled || !!p.demo;
  $('run-it').disabled = !!p.demo;
  $('done').disabled = !!(typeof TS !== 'undefined' && TS.running['done:' + current]); // 确认完成前正在跑测试集（tests.js）
  var draft = !!(r && r.state === 'draft'), stage = r && !r.loose ? stageOf(r) : null;
  var tabsOn = StageTabsModel.hasTabs(r, stage), viewing = null;
  if (tabsOn) { stageView[r.id] = StageTabsModel.sync(stageView[r.id], stage); viewing = stageView[r.id].viewing; }
  var laterTab = tabsOn && StageTabsModel.isLater(stage, viewing);
  $('panel').dataset.view = !tabsOn ? 'all' : laterTab ? 'later' : viewing;
  $('later').hidden = !laterTab;
  if (laterTab) $('later').replaceChildren(el('b', '', StageTabsModel.LATER[viewing][0]), document.createTextNode(StageTabsModel.LATER[viewing][1]));
  var ready = open && stage === 'check';
  var resume = stage === 'done' && !!r.worktree; // 旧版已标完成但合并尚未结束
  var m = r && merging[current];
  $('accept').hidden = !ready && !resume && !m;
  // 进行中：AI 的计划还没全部勾完。给两个看得见的手动动作：交给测试、跳过测试直接验收，不用去右键菜单里找
  var building = open && stage === 'build' && !m && !p.demo;
  var flow = !!(r && r.testing && r.testing.flow);
  $('to-check').hidden = !building;
  if (building) $('to-check-note').textContent = uiText('还在进行中', "In development") + (r.steps ? uiText('：计划 ', " · Plan: ") + (r.done || 0) + ' / ' + r.steps + uiText(' 步', " steps") : '') + '。'
    + (flow ? uiText('AI 把计划做完会自动进入测试中，由测试会话把立项写的用例对接成测试数据来测；你觉得可以了，也可以现在就交给测试，或者跳过测试直接验收。', "When the plan is done, the test session will run the cases from planning. You can send it to testing now or skip straight to acceptance.") : uiText('AI 把计划做完会自动进入待验收；你觉得可以了，也可以交给测试，或者跳过测试直接验收。', "When the plan is done, the requirement moves to acceptance. You can send it to testing now or skip straight to acceptance."));
  testingPanel(r, stage, open && !m && !p.demo);
  if (typeof sessionChoicesPanel === 'function') sessionChoicesPanel(r, stage);
  $('run-it').hidden = !ready;
  if (typeof benchRunButton === 'function') benchRunButton(); else $('run-hint').hidden = true;
  if (!ready) $('run-hint').hidden = true;
  $('done').hidden = !ready || !!m;
  var doneBlock = r && !r.loose ? DepsModel.doneBlock(r) : ''; // 开发中加了前置需求：它们完成前不能确认完成
  $('done').disabled = $('done').disabled || !!doneBlock;
  $('done-note').hidden = $('done').hidden || !doneBlock;
  $('done-note').textContent = doneBlock ? doneBlock + uiText('：这个需求用到了它，要等它先合进主线。', ": This requirement depends on it and must wait for it to merge first.") : '';
  $('resume-merge').hidden = !resume || !!m;
  $('merge-box').hidden = !m;
  if (m) {
    var gateIds = m.gate && m.gate.failing && m.gate.failing.length ? m.gate.failing.map(function (f) { return f.id; }).join('、') : '';
    $('merge-state').textContent = mergeText(m) + (m.phase === 'syncing' && gateIds ? uiText('。合并后的代码没过合并前的检查（', ". The merged code failed the pre-merge check (") + gateIds
      + uiText('），已经交给开发会话修', "). The development session is fixing it") + (m.busy ? uiText('，还在干活', " · Working") : '') + uiText('；它这一轮说完，程序再检查，过了就自动合并回去。', ". RelayForge checks again when this turn finishes and merges if it passes.") : m.phase === 'syncing' ? (m.files.length ? uiText('，有冲突（', ", conflicts (") + m.files.join('、') + '）' : '') + uiText('。AI 在开发会话里把 ', ". The development session is merging ") + m.base + uiText(' 合并进这个需求、解决冲突、跑测试', " into this requirement, resolving conflicts, and running tests") + (m.busy ? uiText('，还在干活', " · Working") : '') + uiText('；它这一轮说完，程序检查好就自动合并回去', ". RelayForge will check and merge when this turn finishes") + (m.busy ? uiText('，不用点按钮', "") : '') + '。' : m.phase === 'stuck' ? uiText('。处理好以后点「重新检查并合并」。', ". When it is resolved, select Check and merge.") : m.phase === 'waiting' ? uiText('。同一时间只合并一个需求，轮到它会自动开始。', ". RelayForge merges one requirement at a time and will start this one when its turn comes.") : '…');
    $('merge-note').textContent = m.note || ''; $('merge-note').hidden = !m.note;
    $('merge-retry').hidden = m.phase !== 'syncing' && m.phase !== 'stuck';
    $('merge-skip').hidden = !(m.phase === 'stuck' && m.gate && !(m.gate.blocked || []).length); // 是测试没过才给跳过；等你同意准备步骤的不给
  } else $('merge-skip').hidden = true;
  $('intake').hidden = !draft;
  if (typeof showRequirementSubagent === 'function') showRequirementSubagent(draft || building ? r : null);
  $('impact').hidden = $('gate').hidden = !draft || !open;
  var previousIntake = $('intake-assessment').dataset.requirement;
  if (draft && previousIntake !== r.id) $('side').scrollTop = 0;
  if (draft || previousIntake) intakeAssessment(draft ? r : null);
  $('status').textContent = !r ? '' : p.note || (p.syncedAt ? uiText('已整理 ', "Last synced ") + new Date(p.syncedAt).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }) : '');
  panelSplit(open && !p.demo && (!r || r.state !== 'dropped'));
  panelUsage();
  if (!r) { $('req-trace').hidden = true; if ($('req-summary')) $('req-summary').hidden = true; return; }
  $('title').textContent = r.title;
  var issueId = r.converted_issue || r.source_issue;
  var issueLink = $('req-issue-link');
  issueLink.hidden = !issueId;
  if (issueId) {
    issueLink.textContent = (r.converted_issue ? uiText('查看转换后的问题 ', "View converted issue ") : uiText('查看来源问题 ', "View linked issue ")) + '#' + String(issueId).replace(/^I-/, '');
    issueLink.onclick = function () { view('issues', issueId); }; // 问题页：停在这一条，所在的页签也跟着切过去
  }
  $('req-state').textContent = r.loose ? uiText('不属于任何需求', "No linked requirement") : r.state === 'dropped' ? uiText('已取消 · 可恢复', "Cancelled · Can be restored") : r.state === 'done' ? uiText('已完成', "Completed") : draft ? uiText('立项中', "Planning") : stage === 'check' ? uiText('待验收', "Ready for acceptance") : stage === 'test' ? uiText('测试中', "Testing") : uiText('进行中', "In development");
  stageSteps(r, stage, viewing);
  panelDeps(r);
  panelOverlap(r);
  $('branch').textContent = r.loose ? uiText('在项目目录里接着聊', "Continue chatting in the project directory") : r.state === 'dropped' ? uiText('已归档 · 不能编辑或继续对话', "Archived · Read-only") : r.state === 'done' ? (r.merged_into ? uiText('已合入 ', "Merged into ") + r.merged_into : uiText('已合并', "Merged")) + uiText(' · 接着聊在项目目录里', " · Continue in the project directory") : uiText('分支 ', "Branch ") + r.branch;
  if (draft) ladder(r);
  if (draft && open) {
    impact(r);
    var choice = r.session_choices && r.session_choices.development;
    $('development-session-choice').hidden = !choice;
    $('development-session-choice').textContent = choice ? uiText('开发会话 · ', "Development session · ") + RequirementSessions.describe(choice) : '';
    var waitingFor = DepsModel.startBlock(r); // 前置需求还没完成：拦住立项，说明在等谁；立项会话照常能聊
    $('start').disabled = !r.gate.ok || !!waitingFor;
    $('gate-note').textContent = waitingFor ? waitingFor + uiText('。现在可以先把需求聊清楚。', ". You can clarify the requirement now.") : typeof CasesModel !== 'undefined' ? CasesModel.gateNote(r) : (r.gate.ok ? '' : r.gate.missing); // 还有验收条件没有用例：提示，不拦
    $('gate-note').classList.toggle('gate-warn', !!waitingFor || !!(r.gate.ok && r.gate.uncovered && r.gate.uncovered.length));
  }
  $('req-body').replaceChildren.apply($('req-body'), reqSections(r, stage, tabsOn, ready, m));
  if (typeof panelSummary === 'function') panelSummary();
  if (typeof panelTests === 'function') panelTests(); // 测试集的两块：放在验收条件后面（tests.js）
  if (typeof panelCaseChanges === 'function') panelCaseChanges(); // 待确认的用例改动（cases.js）
  if ($('req-tests')) $('req-tests').dataset.tab = 'test'; // 这两块是别的文件放进需求内容里的：标上它们属于哪个标签
  if ($('req-case-changes')) $('req-case-changes').dataset.tab = 'talk';
  panelTrace();
  // 测试中（或正在看测试标签）：显示测试会话在做什么，用同一套进展显示
  var tp = (tabsOn ? viewing === 'test' : stage === 'test' || (typeof termOf === 'function' && termOf(current) !== current)) ? states['test:' + current] : null;
  var s = open ? (tp ? tp.state : p.state) : null;
  $('doing-head').textContent = tp ? uiText('测试会话正在处理', "Test session activity") : uiText('正在处理', "Current work");
  $('progress-block').hidden = !s; $('no-progress').hidden = !!s || !open;
  if (!s) return;
  $('doing').textContent = s.doing || uiText('还没有整理出来。', "No progress recorded yet.");
  var plan = s.plan || [], live = plan.filter(function (x) { return x.status !== 'dropped'; }), done = live.filter(function (x) { return x.status === 'done'; }).length;
  $('progress').textContent = live.length ? done + ' / ' + live.length : '';
  $('bar').style.width = live.length ? Math.round(done / live.length * 100) + '%' : '0';
  $('plan').replaceChildren.apply($('plan'), plan.map(function (x) { return item(x.status, MARK[x.status] || '○', x.title, x.note); }));
  var changes = s.changes || [];
  $('changes-head').hidden = !changes.length;
  $('changes').replaceChildren.apply($('changes'), changes.map(function (x) { return item('', '↪', x.text, x.when); }));
}

// 需求内容：立项里是需求说明、要改的功能点、验收条件（带用例）；验收里再列一次验收条件（待验收时带逐条「通过 / 这里不对」）；完成里是完成记录。已取消的不分标签，照旧从上到下
function reqSections(r, stage, tabsOn, ready, m) {
  var mark = function (node, tabName) { if (tabsOn) node.dataset.tab = tabName; return node; };
  var out = [];
  var labels = { 需求说明: 'Description', 要改的功能点: 'Affected features', 验收条件: 'Acceptance criteria',
    开头说的话: 'Opening message' };
  var heading = function (head, label, tabName) {
    var node = el('h2', '', label);
    node.dataset.section = head;
    return mark(node, tabName);
  };
  var lines = function (head, mode) {
    var ul = el('ul', 'plain');
    (r.sections[head].length ? r.sections[head] : [uiText('还没有聊到。', "No discussion yet.")]).forEach(function (line) {
      var gone = /^〔(已替代|已否决)〕/.test(line); // 被换掉或否决的：留着，但划掉、变淡
      var li = el('li', r.sections[head].length ? (gone ? 'gone' : '') : 'empty');
      var box = li;
      var check = head === '验收条件' && !gone ? checkOf(r, line) : null;
      if (check) { li.className += ' ck'; box = li.appendChild(el('span', 'ck-text')); }
      line.split(/\b((?:RF|M|D)-\d+)\b/).forEach(function (part, i) {
        if (i % 2 && r.state === 'done') { var a = el('button', 'obj', part); a.type = 'button'; a.dataset.obj = part; a.title = uiText('在大图里看', "View on the project map"); box.appendChild(a); }
        else box.appendChild(document.createTextNode(part));
      });
      if (check) checkBadge(li, check);
      if (mode === 'accept') { // 验收标签：不放用例，待验收时逐条点「通过」「这里不对」；之后只留当时的结果
        if (check && ready && !m && typeof acceptBox === 'function') acceptBox(li, r, check);
        else if (check && typeof BM !== 'undefined' && BM.markOf(check).text) li.appendChild(el('span', 'ck-mark', BM.markOf(check).text));
      } else if (check && typeof caseBlock === 'function') caseBlock(li, r, check); // 这条验收条件下面的测试用例（cases.js）
      ul.appendChild(li);
    });
    return ul;
  };
  Object.keys(r.sections).filter(function (head) { return head !== '变更记录' || r.state === 'done' || r.state === 'dropped'; }).forEach(function (head) {
    if (head === '变更记录') {
      var label = r.state === 'dropped' ? uiText('取消记录', 'Cancellation record') : uiText('完成记录', 'Completion record');
      out.push(heading(head, label, 'done'), mark(lines(head), 'done'));
    } else {
      out.push(heading(head, uiText(head, labels[head] || head), 'talk'), mark(lines(head), 'talk'));
    }
  });
  if (tabsOn && r.sections['验收条件']) {
    var h = mark(el('h2', '', ready ? uiText('逐条验收', "Check each criterion") : uiText('验收结果', "Acceptance results")), 'check');
    h.id = 'req-criteria';
    out.push(h);
    if (ready) out.push(mark(el('p', 'muted small', uiText('去「人工验收」把程序跑起来自己试，然后逐条点「通过」或「这里不对」（不强制点完，确认完成时如实记下哪几条点了）。', "Start the app in Manual acceptance and try it. Mark the criteria you checked as passed or incorrect. The completion record will show which checks you marked.")), 'check'));
    out.push(mark(lines('验收条件', 'accept'), 'check'));
  }
  return out;
}

// 阶段标签：立项 › 开发 › 测试 › 验收 › 完成。蓝底是当前阶段，下划线是正在看的标签；跳过了测试的，「测试」一格标成跳过。已取消的不分标签
function stageSteps(r, stage, viewing) {
  var box = $('stage-steps');
  var at = STEP_NAMES.map(function (x) { return x[0]; }).indexOf(stage);
  box.hidden = !r || r.loose || at < 0;
  if (box.hidden) return;
  var skipped = !!(r.testing && r.testing.skipped && !(r.testing.rounds || []).some(function (x) { return x.verdict === 'ok'; }));
  var nodes = [];
  STEP_NAMES.forEach(function (x, i) {
    if (i) nodes.push(el('i', '', '›'));
    var cls = i < at ? (x[0] === 'test' && skipped ? 'skip' : 'done') : i === at ? 'cur' : 'todo';
    var node = el('button', cls + (x[0] === viewing ? ' on' : ''));
    node.type = 'button'; node.setAttribute('role', 'tab'); node.setAttribute('aria-selected', String(x[0] === viewing));
    node.appendChild(el('span', cls, (i < at ? (cls === 'skip' ? '↷ ' : '✓ ') : '') + x[1]));
    if (i === at) node.setAttribute('aria-current', 'step');
    node.title = StageTabsModel.tip(stage, x[0], skipped);
    node.onclick = function () { viewTab(r.id, x[0]); $('side').scrollTop = 0; };
    nodes.push(node);
  });
  box.replaceChildren.apply(box, nodes);
}
// 验收条件的一行对应哪一条（程序归类时用的是去掉标记和出处的原文）
function checkOf(r, line) {
  var t = r.testing, text = String(line).replace(/〔[^〕]*〕/g, '').trim();
  if (!t || !t.checks) return null;
  return t.checks.filter(function (c) { return c.text === text; })[0] || null;
}
// 归类的小标记：数据可验 / 看界面；看界面的展开能看到检查步骤
function checkBadge(li, c) {
  if (!c.kind) return;
  var tag = li.appendChild(el('span', 'ck-kind ' + c.kind, c.kind === 'data' ? uiText('数据可验', "Data check") : uiText('看界面', "Visual check")));
  tag.title = (c.kind === 'data' ? uiText('能用数据验证：测试会话写测试集', "The test session writes a runnable test set") : uiText('要看界面或要人操作：写成人工验收的检查步骤', "Check the UI or perform the action manually using the acceptance steps")) + (c.why ? uiText('。理由：', ". Reason: ") + c.why : '');
  if (c.kind !== 'ui') return;
  var d = li.appendChild(el('details', 'ck-steps'));
  d.appendChild(el('summary', '', c.steps.length ? uiText('检查步骤（', "Check steps (") + c.steps.length + uiText(' 步）', " steps)") : uiText('还没写检查步骤', "No check steps yet")));
  var ol = d.appendChild(el('ol'));
  c.steps.forEach(function (s) {
    var it = ol.appendChild(el('li'));
    it.appendChild(el('b', '', s.open)); it.appendChild(document.createTextNode('：' + s['do'] + ' '));
    it.appendChild(el('span', '', uiText('→ 应该看到：', "→ Expected: ") + s.see));
  });
}
// 测试阶段的几块：测试中（测试会话在做什么、跳过测试）、测试交回的问题（退回进行中时）、待验收时的测试结论
function testingPanel(r, stage, live) {
  var t = r && !r.loose && r.testing, last = t && t.last;
  var testing = !!(t && live && stage === 'test');
  $('testing-box').hidden = !testing;
  if (testing) {
    var tp = states['test:' + r.id], doing = (tp && tp.state && tp.state.doing) || t.doing;
    $('testing-note').textContent = uiText('测试中（第 ', "Testing (round ") + t.round + uiText(' 轮）：', "): ") + (doing ? uiText('测试会话在做：', "Test session activity: ") + doing : t.sessions.length ? uiText('测试会话在测，进展记下来以后显示在这里。', "Test progress will appear here as it is recorded.") : uiText('测试会话还没开始，打开「测试」标签就会开出它。', "Open the Testing tab to start the test session."))
      + uiText(' 它没参与开发，把立项写的用例对接成测试数据来跑（没有用例的老需求按验收条件写测试集），看界面的写成人工验收的检查步骤，不改产品代码。', " The independent test session turns planning cases into test data or uses the acceptance criteria for older requirements. It writes manual steps for visual checks and leaves product code alone.");
  }
  var bad = !!(t && live && stage === 'build' && last && last.verdict === 'problems');
  $('test-problems').hidden = !bad;
  if (bad) {
    var ol = el('ol');
    last.problems.forEach(function (x) { var li = ol.appendChild(el('li')); li.appendChild(el('b', '', x.where)); li.appendChild(el('small', '', uiText('现象：', "Observed: ") + x.what)); li.appendChild(el('small', '', uiText('测试会话的判断：', "Test session finding: ") + x.judge)); });
    $('test-problems').replaceChildren(el('h3', '', uiText('测试交回的问题（第 ', "Issues reported by testing (round ") + last.n + uiText(' 轮，', ", ") + last.at + '）'), ol,
      el('p', 'muted', last.fixed ? uiText('开发会话说修好了：', "Development session reports a fix: ") + last.fixed.note : uiText('已经交给开发会话。它修好以后会交给测试会话再测一轮；你也可以在下面直接交给测试或跳过测试。', "Sent to development. After fixing the issues, it will send them for another test round. You can also start or skip testing below.")));
  }
  var result = !!(t && (stage === 'check' || stage === 'done') && last);
  $('test-result').hidden = !result;
  if (stage === 'check' && t && !result && live) { // 老需求没有测试结论：也说一句逐条验收在哪里点
    $('test-result').hidden = false; $('test-result').className = 'test-box';
    $('test-result').replaceChildren(el('p', '', uiText('待验收：在下面每条验收条件旁边逐条点「通过」或「这里不对」；不强制点完，确认完成时如实记下哪几条点了。', "Ready for acceptance. Mark the criteria you checked as passed or incorrect. The completion record will show which checks you marked.")));
  }
  if (result) {
    $('test-result').className = 'test-box ' + (last.verdict === 'ok' ? 'ok' : '');
    $('test-result').replaceChildren(el('h3', '', uiText('测试结论 · ', "Test verdict · ") + String(t.line || '').replace(/^测试：/, '').replace(/。$/, '')),
      el('p', last.verdict === 'ok' ? '' : 'skipped', last.verdict === 'ok' ? uiText('测试会话交回：可以验收了。', "The test session says this is ready for acceptance.") + (last.summary ? last.summary : '') : uiText('跳过了测试：', "Testing skipped: ") + (last.summary || uiText('用户直接进入待验收', "Moved directly to acceptance")) + '。'),
      el('p', 'muted', uiText('去「人工验收」把程序跑起来自己试，然后在下面每条验收条件旁边逐条点「通过」或「这里不对」（不强制点完）。', "Start the app in Manual acceptance and try it. Mark each criterion you checked as passed or incorrect.")));
  }
}

// 绑定事件、开始加载：由 main.js 按顺序调用
function wirePanel() {
  window.shell.onState(function (p) { states[p.id] = p; if (p.id === current || p.id === 'test:' + current) panel(); });
  window.shell.onSplit(function (p) {
    if (p.value) splitViews.set(p.id, p.value); else splitViews.delete(p.id);
    if (p.id === current) panel();
  });
  $('split-approve').addEventListener('click', async function () {
    var id = current, split = splitViews.get(id);
    if (!split || split.state !== 'pending' || splitApproving.has(id)) return;
    splitApproving.add(id);
    this.disabled = true;
    try {
      var result = await window.shell.approveSplit(id, split.id, splitOrders.get(split.id) || {});
      if (result.error && splitViews.get(id) === split) split.error = result.error;
    } catch (error) {
      if (splitViews.get(id) === split) split.error = error.message;
    } finally {
      splitApproving.delete(id);
      if (id === current) panel();
    }
  });
  $('deps-add').addEventListener('click', function () {
    depsPicking = depsPicking === current ? null : current; $('deps-error').hidden = true; panel();
    if (depsPicking) $('deps-search').focus();
  });
  $('deps-search').addEventListener('input', function () { var r = byId(current); if (r) depsOptions(r); });
  $('deps-drop-link').addEventListener('click', async function () { // 一条一条去掉：setDependency 同一时间只做一件
    var r = byId(current); if (!r) return;
    var gone = DepsModel.waitingOn(r).filter(function (d) { return d.state === 'dropped'; });
    for (var i = 0; i < gone.length; i++) await setDependency(r.id, gone[i].id, true);
  });
  $('deps-drop-req').addEventListener('click', function () { var r = byId(current); if (r) changeStage(r, 'dropped'); });
  $('sync').addEventListener('click', function () { var id = termOf(current); window.shell.syncNow(id); if (terms[id]) terms[id].term.focus(); });
  panel();
}

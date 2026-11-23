// 需求详情的阶段标签页（model 层，不碰 DOM；测试在 Node 里直接调用）。
// 阶段条 立项 › 开发 › 测试 › 验收 › 完成 就是标签：点哪个，下面只显示那个阶段的内容。
// 阶段（stage）是需求现在走到哪：talk / build / test / check / done；标签（tab）是右边正在看哪个。已取消（dropped）和不属于需求的聊天不分标签。
var StageTabsModel = (function () {
  var uiText = (typeof HtmlText !== 'undefined' ? HtmlText : require('./html-text')).text;
  var ORDER = ['talk', 'build', 'test', 'check', 'done'];
  var NAMES = { talk: uiText('立项', "Planning"), build: uiText('开发', "Development"), test: uiText('测试', "Testing"), check: uiText('验收', "Acceptance"), done: uiText('完成', "Complete") };
  var idx = function (s) { return ORDER.indexOf(s); };

  // 还没走到的阶段：只有一句说明，没有按钮
  var LATER = {
    build: [uiText('确认立项后开始开发', "Approve planning to start development"), uiText('会开一个新的开发会话，照着立项写的验收条件和用例写代码。它在做什么、计划做到第几步，会显示在这里。', "The development session will implement the acceptance criteria and cases from planning. Its progress and plan will appear here.")],
    test: [uiText('开发做完后进入测试', "Testing starts after development"), uiText('测试会话没参与开发，它把立项写的用例对接成测试数据来跑，逐条报告结果。测试中的进展和结论会显示在这里。', "An independent test session will turn the planning cases into runnable tests and report each result here.")],
    check: [uiText('测试交回「可以验收了」后进入待验收', "Acceptance follows the test verdict"), uiText('到时候在这里把程序跑起来自己试，逐条点「通过」或「这里不对」，最后确认完成。', "Run the app here, try it yourself, mark each criterion as passed or incorrect, and mark the requirement complete.")],
    done: [uiText('确认完成后合并回去', "Mark complete to merge"), uiText('确认完成后会合并并显示完成记录和这次需求的总结，会显示在这里。', "After merging, the completion record and requirement summary will appear here.")],
  };

  // 页面上每一块属于哪些标签（块的名字就是它在页面里的 id）。没列出来的（标题、状态、分支、CLI 和模型、阶段标签）固定在上方，所有标签都有。
  var BLOCKS = {
    intake: ['talk'], 'req-sections': ['talk'], 'req-case-changes': ['talk'], impact: ['talk'], gate: ['talk'],
    'progress-block': ['build', 'test'], 'no-progress': ['build', 'test'], 'test-problems': ['build'], 'to-check': ['build'],
    'testing-box': ['test'], 'test-result': ['test'], 'req-tests': ['test'],
    'req-criteria': ['check'], accept: ['check', 'done'],
    'req-record': ['done'], 'req-summary': ['done'], 'req-trace': ['done'],
  };

  var hasTabs = function (r, stage) { return !!r && !r.loose && idx(stage) >= 0; };
  // 打开一个需求默认停在哪个标签：它现在的阶段
  var defaultTab = function (stage) { return idx(stage) >= 0 ? stage : null; };
  var isLater = function (stage, tab) { return idx(tab) > idx(stage); };

  // 阶段变了，右边怎么办：正看着原来的阶段就跟到新阶段，看着别的标签不动（阶段条上的蓝底跟着阶段走，和这里无关）
  function follow(prevStage, nextStage, viewing) {
    if (!prevStage || prevStage === nextStage) return viewing || nextStage;
    return !viewing || viewing === prevStage ? nextStage : viewing;
  }
  // 每个需求记着：{ viewing 正在看的标签, stage 上一次见到的阶段 }；每次重画前用它算出这一次正在看哪个
  function sync(state, stage) {
    if (!state) return { viewing: stage, stage: stage };
    return { viewing: follow(state.stage, stage, state.viewing), stage: stage };
  }
  // 中间切到测试会话，右边跟着切到「测试」；中间切到别的，右边不动。右边点标签不影响中间（那条路不经过这里）
  function middleSwitched(state, mid, stage) {
    if (mid !== 'test') return state;
    return { viewing: 'test', stage: stage };
  }

  // 现在这个标签里按钮有哪些（程序按阶段和标签算，页面照着显示）
  function buttons(stage, tab) {
    if (isLater(stage, tab)) return [];
    if (tab === 'talk') return stage === 'talk' ? ['start'] : [];
    if (tab === 'build') return stage === 'build' ? ['to-test', 'skip-test'] : [];
    if (tab === 'test') return stage === 'test' ? ['skip-test'] : [];
    if (tab === 'check') return stage === 'check' ? ['run-it', 'done', 'accept-mark'] : [];
    return [];
  }
  // 这个标签里显示哪些块（块名见 BLOCKS）；还没到的阶段只有一句说明
  function blocks(stage, tab) {
    if (isLater(stage, tab)) return ['later'];
    var out = Object.keys(BLOCKS).filter(function (b) { return BLOCKS[b].indexOf(tab) >= 0; });
    // 有些块只在特定阶段才有内容
    if (stage !== 'talk') out = out.filter(function (b) { return b !== 'intake' && b !== 'impact' && b !== 'gate'; });
    return out;
  }
  // 每个标签的说明：阶段条上的提示
  function tip(stage, tab, skipped) {
    var at = idx(stage), i = idx(tab);
    return i === at ? uiText('当前阶段', "Current stage") : i > at ? uiText('还没到这个阶段', "Upcoming stage") : tab === 'test' && skipped ? uiText('跳过了测试', "Testing skipped") : uiText('已经过了这个阶段', "Previous stage");
  }
  // 验收条件在两个标签里各出现一次：立项里带用例，验收里带逐条「通过 / 这里不对」（待验收时）
  function criteriaMode(tab) { return tab === 'talk' ? 'cases' : tab === 'check' ? 'accept' : null; }

  // 开发中点「交给测试」/「跳过测试直接验收」前：AI 的计划做到哪了。全做完返回 null（不用额外确认）；
  // 没有计划返回 { none: true }；否则 { left 还差几步, total 共几步 }。已放弃（dropped）的步骤不算
  function planGap(plan) {
    var live = (plan || []).filter(function (x) { return x.status !== 'dropped'; });
    if (!live.length) return { none: true, left: 0, total: 0 };
    var left = live.filter(function (x) { return x.status !== 'done'; }).length;
    return left ? { none: false, left: left, total: live.length } : null;
  }
  // 弹窗里的一句话：说明计划还差多少；skip 时再加一句不会跑测试
  function gapLine(gap, skip) {
    var head = gap.none ? uiText('还没有记录计划。', "No plan has been recorded yet.") : uiText('开发计划还差 ', "The plan still has ") + gap.left + uiText(' 步没做完（共 ', " unfinished step(s) (of ") + gap.total + uiText(' 步）。', ").");
    return head + (skip ? uiText('跳过测试直接验收，不会跑测试。', " Skipping goes straight to acceptance and no tests will run.") : uiText('现在交给测试，测试会话会照现有的代码来测。', " Sending it to testing now means the test session works with the code as it is."));
  }

  return { planGap: planGap, gapLine: gapLine, ORDER: ORDER, NAMES: NAMES, LATER: LATER, BLOCKS: BLOCKS, hasTabs: hasTabs, defaultTab: defaultTab, isLater: isLater, follow: follow, sync: sync, middleSwitched: middleSwitched,
    buttons: buttons, blocks: blocks, tip: tip, criteriaMode: criteriaMode };
})();
if (typeof module !== 'undefined' && module.exports) module.exports = StageTabsModel;

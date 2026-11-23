// 会话提出建议，应用从对应聊天记录读到用户确认后才建卡。
const { QUOTED } = require('./config');
const { rf, lastLine } = require('./backend');
const { pushReqs } = require('./requirements');
const { state, send } = require('./state');

const guide = `会话拆分需求：在立项、开发或日常聊天中发现能独立交付和验收的工作时，主动建议拆出。先用 python3 ${QUOTED} req list --json 查已有需求；同一事项填写对应编号。回复中说明拟新增几条、各条做什么、可回复「同意拆分」或点右侧同名按钮。紧接正文附一个 relayforge-split 代码块，正文使用 JSON 数组，每项格式如下：
\`\`\`relayforge-split
[{"title":"需求名称","reason":"为何可单独交付","scope":"拟议范围","existing":"已有需求编号；没有就填空字符串","order":"after","waits":[],"takes":[],"needs":{}}]
\`\`\`
order 写这一项和当前需求的先后：after 等当前需求确认完成后再开发（它要用到当前需求的成果时）、before 当前需求要等它完成（它是当前需求要先有的公共部分时）、none 互不依赖；不在某个需求里聊时写 none。waits 列出它还要等的需求（没有就是空数组）：已有需求写编号，这次一起提出的另一条写「#序号」（按上面数组的顺序，从 1 开始，比如 "#1"；不能等自己，不能互相等待）。在日常聊天里一次拆出几条有先后的需求时，就用「#序号」把先后写出来。takes 列出这一项从当前需求接过去的前置需求（没有就是空数组）：当前需求在等某个前置需求、而只有一部分真的要用到它时，把这部分拆出去并在 takes 里写那个前置需求的编号，同意以后由新需求来等它，当前需求不再等它，留下的部分马上能做；只能写当前需求现有的前置需求，不在某个需求里聊时不写。用户想设的前置需求会和当前需求互相等待时，不要去设它，把要用到它的部分拆出去，在 waits 里等它。needs 给每条先后写一句要用到对方的什么（具体到接口、数据、页面或规则，比如「parseDate，统一解析用户输入的日期」）：键是 waits、takes 里写的编号或「#序号」；和当前需求有先后时键写「当前需求」（after 写这一项要用到当前需求的什么，before 写当前需求要用到这一项的什么）。每条先后都要写，漏写的用户不能同意。当前需求被别的需求等着时（req list --json 里它的 dependents，带着各自要用到什么），在正文里逐样说明这些被等的东西留在哪一项；等着的需求不会自动改，由用户调整。在回复正文里也用一句话说明先后和理由。有先后时（和当前需求的、几条之间的、等已有需求的），在 relayforge-split 块前面画一张先后图：会话显示在终端里，只用纯文本，不要用 Mermaid 之类要渲染的格式；箭头从先做的指向后做的，一条链一行，比如「日期工具 →（parseDate）→ 每日汇总 → 周报」，箭头上的括号写要用到什么；一条要等好几条时，每条前置各画一行指向它，比如再加一行「导出（R-20261118-abcd，已有）→ 周报」；当前需求写成「当前需求」，没有先后的写在最后一行「互不依赖：甲、乙」。全都互不依赖时不画。和当前需求的先后用户可以在右侧改，几条之间的先后建好以后在需求的「前置需求」里改。只在实际提出拆分建议时附这个块。由应用读取聊天记录、处理用户确认并回报编号。收到「同意拆分」时无需再发建议或执行 req new。拆分确认只授权建卡，原需求的范围和验收条件要另经用户确认才可调整。`;

function update(run, value) {
  if (state.runs.get(run.id) !== run || JSON.stringify(value) === JSON.stringify(run.split || null)) return;
  run.split = value;
  send('split:view', { id: run.id, value });
}

async function review(run, approve, orders) {
  if (!run.splitEnabled || !run.transcript || run.splitBusy) return;
  run.splitBusy = true;
  const project = state.project;
  const args = ['split', '--session', run.sessionId, '--transcript', run.transcript];
  if (run.splitParent) args.push('--parent', run.splitParent);
  if (approve) args.push('--approve', approve);
  if (approve && orders && Object.keys(orders).length) args.push('--orders', JSON.stringify(orders)); // 你在右侧改过的先后
  try {
    const result = await rf(args, run.root, run.agent);
    if (state.project !== project || state.runs.get(run.id) !== run) return;
    if (!result.ok) throw new Error(lastLine(result.stderr));
    const value = JSON.parse(result.stdout);
    if (value && value.state === 'done' && (!run.split || run.split.state !== 'done' || run.split.id !== value.id)) pushReqs();
    update(run, value);
    return {};
  } catch (error) {
    if (!approve) {
      update(run, { ...run.split, error: error.message });
    }
    return { error: error.message };
  } finally {
    run.splitBusy = false;
  }
}

async function approve(id, key, type, orders) {
  const run = state.runs.get(id);
  if (!run || !run.split || run.split.state !== 'pending' || run.split.id !== key) {
    return { error: '拆分建议已变化，请查看当前会话。' };
  }
  const result = await review(run, key, orders && typeof orders === 'object' ? orders : null);
  if (!result) return { error: '正在检查会话，请稍后再试。' };
  if (!result.error && state.runs.get(id) === run && run.split.state === 'done' && run.split.id === key) {
    const receipt = run.split.items.map((item) => item.requirement + (item.created ? '（已创建，立项中）' : '（已复用）')).join('、');
    const words = { after: '等这个需求完成后再开发', before: '这个需求要等它完成', none: '互不依赖' };
    const order = run.split.items.map((item) => item.title + '：' + (words[item.order] || words.none) +
      ((item.takes_info || []).length ? '，接过' + item.takes_info.map((t) => '《' + t.title + '》').join('、') + '（这个需求不再等它）' : '')).join('；');
    type(run, '我已在右侧同意拆分。应用已处理这些需求：' + receipt + '。先后：' + order +
      '。请检查这个需求的验收条件里有没有属于拆出去那部分的，列出建议移过去的条目和理由，等我确认后再改；然后继续当前会话原本的任务，拆出去的部分不用在这里做。');
  }
  return result;
}

module.exports = { guide, review, approve };

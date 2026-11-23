// 接入代码：只读数据、调用被测代码。需求保存的选择 + 应用默认 CLI -> 需求上只读显示的两行文字。
const path = require('node:path');
const RS = require(path.join(__dirname, '../../app/src/renderer/scripts/requirement-sessions.js'));

function run(input) {
  return RS.choiceLines(input.choices, input.defaultCli);
}

module.exports = { run };

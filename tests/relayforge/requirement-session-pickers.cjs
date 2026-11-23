const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { EventEmitter } = require('node:events');
const scripts = path.join(__dirname, '../../app/src/renderer/scripts');

class Select extends EventEmitter {
  replaceChildren(...options) {
    this.options = options;
    this.value = options[0]?.value ?? '';
  }
  addEventListener(event, callback) { this.on(event, callback); }
}

function inspect(input) {
  const window = {};
  const context = vm.createContext({
    window,
    $() {},
    Option: class { constructor(label, value) { Object.assign(this, { label, value }); } },
  });
  vm.runInContext(fs.readFileSync(path.join(scripts, 'html-text.js'), 'utf8'), context);
  vm.runInContext(fs.readFileSync(path.join(scripts, 'requirement-sessions.js'), 'utf8'), context);
  context.RequirementSessions = window.RequirementSessions;
  vm.runInContext(fs.readFileSync(path.join(scripts, 'new-req.js'), 'utf8'), context);

  const pickers = Object.fromEntries(Object.entries(input.initial).map(([phase, choice]) => {
    const cli = new Select();
    const model = new Select();
    const picker = context.sessionPicker(cli, model, (input.follows || []).includes(phase));
    picker.reset(choice.cli);
    if (!(input.follows || []).includes(phase)) model.value = choice.model;
    return [phase, { cli, model, picker }];
  }));
  const changed = pickers[input.change.phase];
  changed.cli.value = input.change.cli;
  changed.cli.emit('change');
  return JSON.parse(JSON.stringify(Object.fromEntries(Object.entries(pickers).map(([phase, { cli, model, picker }]) => [
    phase, { choice: picker.choice(), models: model.options.map((option) => option.value),
      cliOptions: cli.options.map((option) => option.value), modelDisabled: !!model.disabled },
  ]))));
}

function run(input) {
  // TS-004 只核对各阶段选中的 CLI、模型和随 CLI 联动的模型列表。
  return Object.fromEntries(Object.entries(inspect(input)).map(([phase, { choice, models }]) => [phase, { choice, models }]));
}

module.exports = { run, inspect };

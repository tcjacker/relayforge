(function () {
  var uiText = (typeof HtmlText !== 'undefined' ? HtmlText : require('./html-text')).text;
  const catalog = {
    claude: { label: 'Claude Code', models: { '': uiText('默认模型', "Default model"), sonnet: 'Sonnet', opus: 'Opus' } },
    codex: { label: 'Codex', models: { '': uiText('默认模型', "Default model"), 'gpt-5.4': 'GPT-5.4', 'gpt-5.4-mini': 'GPT-5.4 mini' } },
  };
  const defaultModel = ['', uiText('默认模型', "Default model")];
  let live;

  function describe(choice) {
    const cli = catalog[choice.cli];
    const option = optionsFor(choice.cli).find(([id]) => id === choice.model);
    return cli.label + ' · ' + (option ? option[1] : choice.model);
  }

  function setCatalogs(models) { live = models; }

  function optionsFor(cli, models = live) {
    if (!Object.hasOwn(catalog, cli)) return [];
    return models ? [defaultModel, ...(models[cli] || [])] : Object.entries(catalog[cli].models);
  }

  function validModel(cli, model) {
    const pattern = cli === 'claude' ? /^[a-z][a-z0-9_.:-]*(?:\[1m\])?$/i : /^[a-z][a-z0-9_.:-]*$/i;
    return typeof model === 'string' && pattern.test(model);
  }

  function valid(choice, models) {
    return !!choice && optionsFor(choice.cli, models).some(([id]) => id === choice.model);
  }

  function flags(phase, choice) {
    return ['--' + phase + '-cli', choice.cli, ...(choice.model ? ['--' + phase + '-model', choice.model] : [])];
  }

  // 测试会话的选择是可选的：没给、或者给 { follow: true }，就是「跟开发一样」——不传任何参数，由后端不存这个字段（不复制开发会话的值）。
  function isFollow(choice) { return choice == null || choice.follow === true; }

  function createArgs(choices, models) {
    const testing = choices.testing;
    if (!isFollow(testing) && !valid(testing, models)) throw new Error(uiText('请选择测试会话的 CLI 和模型，或者选「跟开发一样」。', "Select a CLI and model for testing or choose Same as development."));
    return ['intake', 'development'].flatMap((phase) => {
      const choice = choices[phase];
      if (!valid(choice, models)) throw new Error(uiText('请选择立项和开发会话各自的 CLI 和模型。', "Select a CLI and model for both planning and development."));
      return flags(phase, choice);
    }).concat(isFollow(testing) ? [] : flags('testing', testing));
  }

  function fixArgs(choice, models) {
    if (!valid(choice, models)) throw new Error(uiText('请选择修复会话的 CLI 和模型。', "Select a CLI and model for the fix session."));
    return flags('fix', choice);
  }

  // 一个需求的测试会话实际用什么：单独设置过的用它，没有就跟着开发会话（开发会话也没存就沿用应用设置：choice 为 null）。
  function testingChoice(choices) {
    const own = choices && choices.testing;
    if (own) return { follows: false, choice: own };
    return { follows: true, choice: (choices && choices.development) || null };
  }

  const CODEX_TESTING_NOTE = uiText('用 Codex 测试时，程序对它的限制比 Claude Code 少。', "Codex testing sessions have fewer RelayForge restrictions than Claude Code sessions.");

  // 需求上只读显示的两行：开发会话、测试会话各用什么。没存过的写应用默认的；测试没单独选的写「跟开发一样（…）」。
  function choiceLines(choices, defaultAgent) {
    const fallback = { cli: Object.hasOwn(catalog, defaultAgent) ? defaultAgent : 'claude', model: '' };
    const own = choices && choices.development;
    const dev = own || fallback;
    const testing = testingChoice(choices);
    return {
      development: own ? describe(own) : uiText('应用默认（', "App default (") + describe(fallback) + '）',
      testing: testing.follows ? uiText('跟开发一样（', "Same as development (") + describe(dev) + '）' : describe(testing.choice),
    };
  }

  const api = { catalog, describe, optionsFor, setCatalogs, validModel, createArgs, fixArgs, testingChoice, isFollow, choiceLines, CODEX_TESTING_NOTE };
  if (typeof module === 'object' && module.exports) module.exports = api;
  else window.RequirementSessions = api;
})();

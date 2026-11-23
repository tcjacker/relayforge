(function () {
  const labels = { claude: 'Claude Code', codex: 'Codex' };
  const automatic = '按任务复杂度选择';
  const defaults = { cli: 'follow', model: '' };

  function effective(globalChoice = defaults, own = {}, development) {
    const inherited = globalChoice.cli === 'follow' ? development : globalChoice.cli;
    const cli = own.cli || inherited;
    const model = own.model != null ? own.model : cli === inherited ? globalChoice.model : '';
    return { cli, model: model || '', source: own.source || (globalChoice.cli === 'follow' ? '跟随开发会话' : '设置默认') };
  }

  function available(statuses) {
    return Object.keys(labels).filter((cli) => statuses[cli]?.state === 'in');
  }

  const api = { labels, automatic, defaults, effective, available };
  if (typeof module === 'object' && module.exports) module.exports = api;
  else window.SubagentModel = api;
})();

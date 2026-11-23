# FlowNote · 轻量任务板（演示项目）

这是 RelayForge 的示例项目。产品资料、需求状态、会话和验证记录均为虚构演示数据，不代表真实用户决定或验收结果。

直接用浏览器打开 `src/index.html` 即可使用。可新增任务、切换状态、筛选优先级，并导出 JSON。数据仅保存在当前浏览器，无服务器和账号系统。

- `docs/product.md`：产品范围和示例验收。
- `docs/architecture.md`：界面、任务规则与本地数据的关系。
- `docs/demo-guide.md`：在 RelayForge 里演示各项能力的路线。
- `.relayforge/`：程序生成的产品事实、需求和进度示例。

代码自检：`python3 -B -m unittest discover -s tests`。

需要真实开发时，在 RelayForge 示例会话上点击「开始真实会话」。所有工作发生在这份演示仓库中，不影响 RelayForge 源码。

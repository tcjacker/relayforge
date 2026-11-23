# RelayForge 开发规范

给在这个仓库里写代码的人和 AI 看。每条都对应代码里真实的结构、自动检查，或者踩过的坑。
标了「（自动检查）」的规则，违反时测试会失败。其余几条测试查不出来，要靠写代码时自己注意。

## 1. 每次改动的流程

1. 先在分支上改，不要直接在 main 上提交。合并回 main 用 `git merge --no-ff`，合并说明写「合并：……」。
2. 提交说明用中文：第一行说清做了什么，正文按条目写改了哪些地方、为什么改。
3. 提交前把三样都跑过：
   ```bash
   python3 -B -m unittest discover -s tests -p 'test_*.py'   # 后端，约 10 分钟
   node --test tests/test_app_*.cjs                           # 桌面端
   cd app && npm run lint                                     # ESLint，test_app_lint.cjs 也会跑
   ```
   只跑单个测试文件不算数。搬文件、改模块路径这类改动，经常是在别的测试里才暴露问题。
4. 如果改了界面脚本的加载顺序，或者改了桌面应用调用的后端命令，要用真实应用打开演示项目看一眼：
   `cd app && npm start`。自动化检查可以参考 `tests/home_ui_entry.cjs`。
5. 测试失败时先查原因，不要为了让测试通过去改断言的含义。
   测试本身写错了（比如 macOS 的临时目录路径问题），可以改测试，但要在提交说明里写清楚。

## 2. 后端：放在哪个目录

后端在 `backend/relayforge/`，只用 Python 标准库，要求 Python 3.10 及以上。目录按业务分包：

| 包 | 放什么 |
|---|---|
| `core/` | 通用：路径和小工具（common）、文档读写、会话记录解析、调用后台模型、应用的标记（trust）、会话身份、代写记录。不认识需求、问题这些业务概念 |
| `facts/` | 项目的事实和大图：模块、功能点、旅程、设计稿、决定、进行中的改动、边做边记、整理会话、读代码起草大图、页面 |
| `requirements/` | 需求：记录（record）、工作目录归谁（owners）、拆分、立项评估，以及测试阶段：用例、测试轮次、逐条验收 |
| `issues/` | 问题：记录、初判、修复、交给 AI 排查、和需求互转、复查 |
| `testsets/` | 测试集：存储（store）、查看、编辑、用户的决定、落定、停下来时的检查、运行、桥接文件 |
| `envs/` | 测试环境：存储、清洗、保密值和外部依赖、准备步骤、查看、编辑、交给 AI、读配置文件 |
| `sessions/` | 会话：记录、每一轮的步骤、历史、追溯、提交上的出处、另存到本机、总结 |
| `project/` | 项目：登记、初始化、数据外置、发布、分支、本机服务、钩子、状态 |
| `flows.py` | 跨业务的用户动作：需求的确认完成、放弃、恢复、改状态，问题的确认修好 |
| `cli.py` | `rf.py` 的参数定义和总分发；各业务子命令的分发写在各包的 `cli.py` 里 |

新代码按下面的顺序判断放在哪里：

1. 属于哪个业务，就放进那个包。
2. 一个用户动作要串起好几个业务（比如确认完成要合并代码、落定测试集、另存会话），就放进 `flows.py`。
3. 命令行参数的解析和分发，放进对应包的 `cli.py`。
4. 完全不涉及业务概念，才放进 `core/`。

另外三条约定：

- 包里的文件名不带包名前缀：写 `testsets/views.py`，不写 `testsets/testset_views.py`。
- 包的 `__init__.py` 只写一段说明这个包放什么的文档。唯一的例外是 `testsets/__init__.py`，原因见第 4 节。
- 文件里用 `# ---------- 节标题 ----------` 分段时，标题要和下面的内容一致；代码移走后，记得改标题或删掉。

## 3. 后端：分层和依赖

每个模块属于下面五层之一，只能导入同层或下层的模块。函数里面的导入也算。（自动检查：`tests/test_layers.py`）

```
基础 → 记录（requirements/owners）→ 领域（各业务的数据）→ 能力（在数据上做事）→ 流程和入口（flows、各个 cli、server、hooks、init……）
```

- 新模块必须在 `tests/test_layers.py` 的 `LAYERS` 里登记，否则测试失败。层次和目录无关，每个模块各自登记。
- `KNOWN`（重构前遗留的越层导入）已经清空，不要再往里加。新出现的越层导入要直接改掉。
- `COMPAT` 只用来放对外兼容、必须保留的例外，每条都要写明原因。现在只有一条：`testsets.store → testsets.runs`。
- **不要用函数内的延迟导入去绕开循环导入。** 出现循环，说明有代码放错了层。正确的做法是把两边共用的底层部分抽出来，放到更低的层。仓库里已有的例子：
  - `requirements/owners.py`：判断「这个工作目录属于哪个需求或修复」。
  - `requirements/testrecords.py`：纯粹解析需求文件里的测试记录。
  - `sessions/steps.py`：从会话记录里读出每一轮做了什么。
- 下划线开头的名字是**包内私有**：同一个包里的模块可以互相使用，别的包不能导入。要给别的包用，就起一个公开的名字。（自动检查）
- 文件变大时，按职责拆分，不要按行数机械地切。超过大约 600 行，或者需要分很多节，就该考虑了。
  - 先看各节之间的相互引用：经常是几个底层辅助函数放在了上层的节里，挪下去以后就能干净地拆开。
  - 拆的时候，只拆出那些「原模块剩下的代码不依赖它」的部分。
- `relayforge/__init__.py` 会把各模块的名字汇总导出，供 `import rf` 使用，测试大量依赖这个统一的命名空间。
  - 新模块如果需要在 `rf.xxx` 上能直接用，就给它写 `__all__`，再加进 `__init__.py`。
  - 没有 `__all__` 的模块不要整体导出，否则它导入的所有名字都会被一起导出。
  - 只需要导出个别名字时，就在 `__init__.py` 里点名导入。
  - 模块名不要和它导出的函数同名，比如不要有 `issue_triage.py` 里定义 `issue_triage()`：汇总导出时，函数会覆盖同名的模块。

## 4. 对外接口：改了会影响别处

| 接口 | 谁在用 | 要注意什么 |
|---|---|---|
| `.relayforge/bin/rf.py` 和旁边的整个包 | 每个用户项目。`init` 把包整个复制过去（`project/init.py` 的 `install_program`） | 入口名不能改；`core/common.py` 的 `PACKAGE` 必须指向包的根目录；`drivers/`、`templates/` 在包根目录下 |
| `relayforge.testsets.bridge_sets`、`bridge_run` | 用户仓库里由 `rf.py test bridge` 生成的 Python 桥接文件，旧版本生成的也还在 | 永远要能从 `relayforge.testsets` 导入，由 `testsets/__init__.py` 转发 |
| `rf.py` 的命令、参数和 `--json` 输出 | 桌面应用主进程（`app/src/main/*.js` 通过 `rf()` 调用） | 改命令或输出的字段，要同时改主进程，并跑 Node 测试 |
| `core/trust.py` 的 `USER_COMMANDS`、`APP_ONLY` | 主进程按同一份清单判断哪些命令只能由用户触发；`test_app_hardening.cjs` 用正则从源码里读这份清单 | 保持 `('命令', '动作')` 这种字面写法，两边一起改 |
| `core/proxy.py` 的 `PROXIED` 和 `app/src/main/proxy.js` 的 `PROXIED` | 会话经应用代写记录 | 两边一起改（`test_app_proxy.cjs` 会核对） |
| `.relayforge/` 的数据格式 | `templates/FORMAT.md` 会复制进每个项目，AI 会话照着它写数据 | 改格式时同步改 FORMAT.md，并考虑旧项目里已有的数据 |
| 按文件路径读源码的测试 | 比如 `test_hardening.py`、`test_app_hardening.cjs` 会读 `core/trust.py` | 移动文件后要搜一遍 |

移动文件或改模块路径之后，要搜这些地方：
- `backend/` 和 `tests/`，包括 `tests/rf/` 子目录；
- JS 测试里内嵌的 Python 代码；
- `test-project/build_demo.py`；
- 注释和文档里写的文件路径。

## 5. 安全和权限：不能绕过

- **只由用户触发的命令**（立项、完成、放弃、同意执行命令、取出明文等）由 `rf.py` 自己在执行前校验，见 `core/trust.py` 的 `user_gate`、`require_app`。
  - 新增这类命令，要登记进 `USER_COMMANDS`；只认应用标记的，还要登记进 `APP_ONLY`。
  - 不要加任何能让 AI 会话绕过校验的开关、环境变量或测试后门。`test_hardening.py` 会检查 `trust.py` 不读取测试用的开关。
- **系统的沙箱是真正的边界。** 会话和用户是同一个系统用户，权限规则只按命令的写法匹配，登记文件也只是一个普通文件，都挡不住有意绕过。
  - 应用开的 Claude Code 会话带 `sandbox` 设置（`claude-settings.js` 的 `sandbox()`），后台复查的模型也一样（`issues/review.py` 的 `_sandbox`）；Codex 会话用它自己的沙箱。
  - 沙箱里运行的 `rf.py` 一律不认应用的标记（`core/trust.py` 的 `sandboxed()`）。
  - `RF_HOME` 在沙箱里按「只放行这几个」写（`claude-settings.js` 的 `RF_HOME_WRITABLE`）：应用的控制文件（批准、收件箱、进程记录、登记、合并队列……）
    会话一律写不了。新加的目录默认也写不了；会话自己的 `rf.py` 真要写的，才加进这个清单。
  - Codex 会话的越出沙箱申请沿用用户自己的 Codex 审核设置（通常 auto_review），应用不再固定成 `user`：开发时每条都要人点，会话没法独立跑。
    沙箱仍然挡住越权的副作用（rf.py 在沙箱外会被 `sandboxed()` 识别）。无界面会话按设置用 `approval_policy="never"`（自动）或 `on-request`（每次问我）；
    见 `sessions.js` 的 `codexSandboxFlags`、`approvalArgs`。无界面会话的「自动」审批只是不问沙箱里的命令。
  - 应用照着执行的文件，不能只靠沙箱：`run/` 下的进程记录由应用签名（`record-sign.js`），清理时只认签名对得上的。
  - 沙箱没生效时界面上看不出来，所以有两道检查：开 Claude Code 会话前查版本和会削弱沙箱的设置（`sandbox-check.js`，不过就不开）；
    会话里的 `rf.py` 发现自己不在沙箱里，报给应用提醒你（`project/status.py` 的 `report_unsandboxed`）。
    `MIN_CLAUDE` 只能改成实际验证过沙箱设置会生效的版本。不要给会话放开跳出沙箱（`allowUnsandboxedCommands`、`excludedCommands`），也不要让会话能写应用的登记目录、应用的设置和项目的 `config.json`（这些内容会被应用在沙箱外使用）。
- **应用的标记**（`RELAYFORGE_APP_TOKEN`）只放进应用自己调用 `rf.py` 时的环境变量。开会话、执行用户配置的命令时，要用 `cleanEnv()`（主进程）或 `scrubbed_environ()`（后端）去掉它。
- **保密值**不进仓库。测试环境的设置分成仓库里的部分和本机的部分。日志、运行结果、交给会话的文字，都要经过 `redact` 打码。
- **执行用户配置的命令**（准备步骤、服务启动命令、构建命令）之前，必须先由用户在应用里看过实际内容并同意，系统按指纹记住这次同意。

## 6. 进程

- 启动外部命令（模型、驱动、构建、准备步骤）用 `core/procs.py` 的 `run()`，不要直接 `subprocess.run(timeout=…)` 或 `proc.kill()`：
  超时只杀直接子进程会留下孙子进程（shell 里的 mvn、Claude 的 Bash、自己开了新会话的服务）。`run()` 超时或被叫停时按父子关系收拾整棵树。
- 应用这一侧同理：要停掉一个子进程，用 `proc-tree.js` 的 `killTree()`；会话留下的进程按会话编号收拾（`session-procs.js`）。

## 7. 数据和报错

- 记录统一写在项目主目录里（`main_checkout`）。提交时只提交 `.relayforge/` 下的文件（`commit_facts`），不碰用户自己的改动。数据外置的项目不进 Git。
- 同一份数据可能被多个进程同时写，要用已有的锁：`facts_lock`、`issue_lock`、`tests_lock`、合并锁。
- 给用户看的错误，抛 `ValueError`，用中文写清楚「为什么不行、该怎么办」。命令行把它打印到标准错误，退出码 1。
- 后端需要调用 git 时，用 `core/common.py` 里的 `git()` 或 `run_git()`。注意 `git()` 出错时返回空字符串；需要区分「出错」和「没有输出」时，用 `run_git()`。

## 8. 桌面端

- 主进程（`app/src/main/`）通过 `rf()` 调用后端，不要在 JS 里重写后端已有的逻辑。
- 界面和主进程之间的通道，两头都要登记：主进程在 `ipc.js`，界面在 `preload/index.js`。
- 窗口保持 `contextIsolation: true`、`nodeIntegration: false`。新开的窗口参照 `designs.js` 里设计稿窗口的设置：开 sandbox、拦截导航和弹出新窗口。
- 界面脚本（`app/src/renderer/scripts/`）都是普通脚本，按 `index.html` 里的顺序加载，共用同一个全局作用域：
  - 新脚本要加进 `index.html` 的正确位置：依赖谁，就放在谁的后面。
  - 用到没定义的名字，ESLint 会报错。（自动检查：`app/eslint.config.js`、`tests/test_app_lint.cjs`）
  - 两个脚本不能声明同一个顶层名字。（自动检查）
  - 不要用 `eslint-disable` 绕过检查。确有必要时，在同一行写明原因。
- 用字符串拼 HTML 时，所有动态内容一律用 `HtmlText.esc` 转义（model 文件里是 `esc`），不要再自己写一个转义函数。
- `*-model.js` 不碰 DOM，Node 测试会直接 `require` 它们。它们依赖 `html-text.js` 时，沿用现有的写法：`typeof HtmlText !== 'undefined' ? HtmlText : require('./html-text')`。
- 依赖装在各自的工作目录里（`cd app && npm ci`）。不要用符号链接共享 `node_modules`，也不要提交 `node_modules`、`.electron-runtime`。

## 9. 测试

- 新功能、修 bug，都要带测试。
- Python 测试里的临时目录要先 `.resolve()`：macOS 的 `/var` 实际指向 `/private/var`，程序拿到的是解析后的路径。
- 补丁要打在「使用方查找这个名字的模块」上，比如 `mock.patch('relayforge.flows.today')`，而不是这个函数原本定义的模块。函数搬家时，补丁要跟着改。
- 要模拟「经过一层 shell」时，不要用只有一条命令的 `sh -c`：shell 会直接 exec 成那个程序，中间那一层就不存在了。在命令后面接 `; exit $?`。
- 测试里起的小服务，要处理 socket 的 `error` 事件：程序探测端口时会马上断开连接，没处理就会崩溃。
- 按 `index.html` 顺序加载界面脚本的测试，加载列表要和 `index.html` 保持一致。

## 10. Git

- `git switch` 切到一个曾经跟踪过某个路径的提交时，git 可能删掉同一位置上被忽略的目录。`app/node_modules` 就这样被删过一次。切换分支后，如果应用起不来，先检查依赖目录还在不在。
- 不要提交指向本机绝对路径的符号链接。

## 11. 语言和风格

- 注释、界面文字、报错、文档都用中文。面向用户时，用产品里的词：需求、问题、测试集、测试环境、会话。代码里的标识符用英文。
- 注释写「为什么这样做」，不要复述代码在做什么。
- 新写的代码一行尽量不超过 130 个字符。现有的长行不必专门去改。

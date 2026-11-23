# 项目资料领域接口

`seed_project.Project(root=仓库主工作树)` 使用现有 `seed_store.sql()` 的 PostgreSQL 配置。
一个 schema 对应一个仓库；初始化只登记当前 HEAD 为初始来源，记录固定文档表，
不写 Git、不生成规则、不从设计稿、project.json 或示例导入确认裁定。
数据库需调用 `store.init()`；服务启动会在协调线程启动前恢复未完成发布。

## 记录与权威来源

- 文档：`id, path, purpose, roles, editable`。可编辑 id 为 context、shared、main、worker，
  路径分别是 PROJECT-CONTEXT.md、真实 AGENTS.md、agents/main.md、agents/worker.md。
  readme 和 design 只作为只读来源，不注入角色规则。不能注册额外路径。
- 草稿：整包 `version, base_commit, source_commit, contents`。contents 以文档 id 为键，字符串保留完整换行；
  null 表示文件不存在。每次保存产生不可变快照，首次 expected_version=0。
- 发布：`request_id`（UUID）、输入 fingerprint、draft_version、base_commit、candidate_commit、
  冻结 contents、state、evidence、human_confirmation、error（失败时）。证据绑定基线、候选和整包摘要。
- 上下文清单：format_version=1、role、commit、固定顺序 files、digest。
  文件记录 id/path/exists/blob/sha256/content；完整正文可离线重建。
  数据库清单只用作运行证据，发布正文始终从 Git 对象读取。

## 调用契约

```python
import seed_project
p = seed_project.Project()
source = p.read_published()             # commit + files；缺失是 exists=False/content=None
saved = p.save_draft(0, {"context": seed_project.TEMPLATE}, source["commit"])
publication = p.prepare_publish(request_uuid, saved["version"], saved["base_commit"])
# 网页展示候选 diff、证据及人工确认表单；调用者负责确认来自人类。
p.activate_publish(request_uuid, publication["candidate_commit"],
                   publication["base_commit"], "用户确认来源或说明")
manifest = p.resolve_context(publication["candidate_commit"], "worker")
files = seed_project.rebuild_context(manifest)  # 无 Git/数据库也可验证、重建
```

`list_documents()` 是模块函数，返回登记表副本。实例还提供 `get_draft()`、
`get_publish(request_id)`、`list_publications()`、`recover_publications()`。
`save_draft(expected_version, contents, base_commit)` 接受文档补丁，展开成完整快照；
null 为明确删除。base_commit 必须是当前 main HEAD；source_commit 记录草稿所据的已发布资料提交。
main 有其他正常代码提交时，调用者可读取新 HEAD 并显式保存新草稿基线；资料正文仍来自已发布指针。
发布时 main HEAD 必须仍等于冻结基线，前移会明确冲突，调用者不得自动 rebase。
`read_published(commit=None)` 默认读取持久化发布指针；显式提交必须是完整 SHA，
且是登记初始提交或人工启用的发布提交。任意历史提交不会自动获得已发布身份。
`resolve_context(commit, role)` 支持 main/worker，固定一次提交读取适用正文；
未发布、验证失败和仅 ready 的候选均拒绝。

PROJECT-CONTEXT.md 采用四个唯一二级标题：长期目标、架构、全局约束、已确认裁定。
裁定章节每个非空段落必须包含“确认来源：具体来源”。空模板不包含裁定。
标题和来源格式只能证明记录完整；确认真实性由调用方保留的人类交互证据负责。

## 状态及恢复

| 状态 | 后续 | 条件 |
| --- | --- | --- |
| preparing | ready / failed | 专属 detached worktree 生成整包候选；干净验证 worktree 运行原基线和候选测试 |
| ready | activating | 草稿未过期、证据一致、main 干净且基线一致，人工确认同一候选 |
| activating | activated / conflict | 授权先持久化，再 ff-only，最后发布指针与唯一事件同事务记账 |
| failed / conflict | 终态 | 保留草稿与记录，使用新请求重新准备 |
| activated | 终态 | 相同启用重试直接返回，不重复事件 |

同 request_id 同输入返回同记录；不同输入抛 Conflict。相同 expected_version 并发保存仅一个成功。
不同候选竞争同一 main 基线仅一个可启用；未跟踪文件也使 main 判脏。
Git common-dir 的 seed-activation.lock 同时保护资料和现有任务启用，不依赖 psql 会话锁。
草稿写入、发布登记及发布记账使用 PostgreSQL 协调行的短事务锁。

恢复由调用者显式调用，发布读取也先恢复。preparing 可复用 refs/seed-project/UUID，
重新检查精确差异、冻结正文并重跑验证。无人工确认不会推进 main。
activating 若候选已包含于 main 历史，则补记一次；否则只有 main 仍干净且位于基线才重试。
无法满足条件进入 conflict，不永久占用中间状态。数据库不可用会抛错，恢复可再次执行。
恢复不会 reset main，不读取实时文件拼出混合版本，不访问任务 `.seed/trees`。

领域错误为 Conflict、PathRejected、InvalidState、ValidationFailed、GitFailure；数据库等基础设施失败可抛 RuntimeError。
候选验证失败作为 failed 记录返回（含 error），调用方必须检查 state，不能据候选 SHA 推断成功。
允许文件使用 Git mode/type 检查，拒绝符号链接、目录、submodule；新文件目标也检查非普通文件和符号链接父目录。

## 验证范围

`python3 -B -m unittest discover -v`：现有流程及领域规则；真实模型测试须另行 opt-in。
`SEED_TEST_PG=1 python3 -B -m unittest -v test_seed_pg test_seed_project_pg`：
每测试独立 schema、真实 PostgreSQL 连接与 Git worktree；包含并发、正文往返、
新进程草稿恢复、五个发布故障边界、新进程恢复、Git 成功后 main 前移及数据库暂时不可用。
故障为模拟进程中断（BaseException），恢复在新 Python 进程执行；不调用真实模型。

2026-11-06 本次实现验证结果：全量 discover 57 项，38 项通过、19 项按 PostgreSQL/真实模型开关跳过；
另行启用 `SEED_TEST_PG=1` 的 journal、stage、project 两组数据库模块共 18 项全部通过。
显式新基线保留未发布文档编辑的增强断言另行复跑 1 项通过。`git diff --check` 通过。
测试使用临时 Git 仓库、独立 PostgreSQL schema；未调用真实模型，未在当前实现工作树创建提交或切换分支。

## 网页适配器（2026-11-07）

`seed_material_web.py` 只通过 `seed_project as ctx` 的 `Project` 访问资料。
`seed.py` 的 `/materials?area=context` 和 `/materials?area=agents` 提供四个资料章节、
共享规则、主 Agent 和 Worker 入口；`/action` 的 `material_*` 动作沿用 token、Host、
请求大小及 pending_posts 重启协调。无 JavaScript 也可完成预览、保存、准备与人工启用。
网页不接受路径或任意 Git ref，不维护 project.json 副本，不注入 Agent 简报。

新增只读领域接口：

- `Project.list_drafts()`：通过 `store.project_drafts()` 按版本倒序返回不可变整包快照。
- `Project.publication_diff(request_id)`：从登记发布记录取得基线和候选，先校验候选父提交、
  固定允许路径及冻结正文，再返回差异；候选仍不能通过 read_published() 读取。

四章节解析遇到重复、缺失或非预期二级标题会显示原文修复编辑器，拒绝静默拆分。
表单带有编辑快照，以恢复浏览器 CRLF 传输前未改动的正文和原始换行约定；
它仅用于表单往返，发布仍核对数据库的整包版本、正文、基线和候选。
上下文验证接受 LF/CRLF，不改写实际存储正文。
来源片段必须出自登记已发布原文，导入后在正文记录来源路径与完整提交；
导入不会保存，也不允许以“已确认裁定”为导入目标。

预览采用受限 Markdown 子集（标题、段落、代码块）。原生 HTML、链接、图片均按文本显示，
不生成可执行协议链接。所有正文、差异、错误与历史记录均 HTML 转义。
准备和启用会比较同一表单中所有可编辑文档与数据库已保存整包，
前端同时禁用有未保存修改的发布操作。并发冲突返回 409，保留原输入、原版本并展示最新记录。
服务端记录 failed/ready/activated，页面不会把保存草稿或失败候选显示为生效。
main 前移时，必须人工选择新的完整基线并另存新草稿，不自动重试或 rebase。
资料签名进入主页及 `/api/revision`；打开资料编辑器时仅提示服务器有更新，不自动刷新覆盖输入。

验证记录见 [project-materials-web-verification.md](project-materials-web-verification.md)。

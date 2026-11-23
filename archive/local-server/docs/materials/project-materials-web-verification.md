# 项目资料网页验证记录

日期：2026-11-07。工作树：8bfa07ff-49e4-4c3d-952e-d9e7f3bb06e0。

实现文件：seed.py、seed_ui.py、seed_ui.js、seed_ui.css；新增 seed_material_web.py。
领域最小补充：seed_project.py、seed_store.py。测试新增 test_seed_material_web.py，
test_seed_restart.py 的隔离服务 stub 同步领域入口参数及资料刷新签名。

## 自动化验证

- `python3 -B -m unittest discover -v`：本轮最终 75 项，49 项通过，26 项按 PostgreSQL/真实模型开关跳过；日志 materials-unittest-followup.log。
- `SEED_TEST_PG=1 python3 -B -m unittest -v test_seed_pg test_seed_project_pg test_seed_material_web`：33 项全部通过（真实 PostgreSQL、临时 Git 仓库与 HTTP，115.896 秒）。
- `git diff --check` 和 `node --check seed_ui.js` 通过。

新增 16 项测试：9 项表单/渲染/JavaScript 规则测试及 7 项真实 HTTP/PostgreSQL 测试。
覆盖七个编辑入口、整包预览/保存/验证/人工启用/历史，空文件与缺失文件区分、
尾部空行、浏览器 CRLF 传输、无法无损拆分的原文修复、登记来源片段导入，
脚本/闭合标签/SVG/危险协议文本转义，未知或只读 id/路径参数拒绝，
禁用 JS 的 HTTP 提交未保存内容不能启用、伪造候选拒绝、旧页面保留冲突输入，
两个并行 HTTP 页面仅一次保存与启用成功，main 脏状态/基线前移拒绝，
新进程继续读取 failed/ready 草稿与发布记录、未人工确认不推进 main、
资料页与主页和 API 刷新签名一致。原有任务/阶段/重启回归均保留。

数据库测试全部使用临时 Git 仓库和独立 schema，候选验证执行临时仓库的 unittest；
未在当前实现仓库发布、创建提交、切分支或推送，未修改其他已有工作树。
本机数据库/loopback 默认沙箱不可访问，获工具自动审查允许后在隔离测试中执行。

## 浏览器与模型验证边界

真实浏览器尚未完成：`cua.getState()` 返回浏览器访问 host 不可用，随后
`cua.getApp('com.google.Chrome')` 返回 “Computer Use was not approved to use Google Chrome”。
未绕过该限制，未将 HTTP 测试视为浏览器运行结果。仅为浏览器创建的临时服务已停止并清理。
按已确认的人工作业安排，由用户在同类临时仓库完成七个入口编辑、dirty 禁用、预览不执行脚本、
保存/准备/人工启用/历史和刷新保留输入的实际浏览器验证；不使用生产仓库。

`codex --version` 成功（0.164.0），`import seed` 成功。
未调用真实模型，未声明执行器 app-server 或真实模型冒烟通过。
上次失败日志保留在主仓库 `.seed/logs/abd48905b58844be87138f9b07316052.jsonl`，
其中为 app-server 初始化 Operation not permitted。

完整本轮日志保存在 `docs/verification/`。

## 续验交接（2026-11-07）

按用户已确认的 human_verify 决策，由用户完成真实浏览器验收。本工作树修改是待验收实现，
尚未提交；不存在本轮新建的候选提交 SHA。不要把资料发布流程生成的候选 SHA 当作本实现的提交。
未自动启用当前项目。上轮 33 项 PostgreSQL/HTTP 通过证据保留不覆盖。

本轮修复 Agent 指令页 POST 预览/报错后地址丢失 `area=agents` 的问题。
新增 Node.js 执行实际 seed_ui.js 的 DOM 替身契约测试：七个编辑区逐一修改时禁用发布，
恢复原内容时解除禁用，导入/冲突标记持续禁用，取消文件勾选也视为修改；
资料变化提示刷新且不自动覆盖输入，并检查两个入口返回地址。它不是浏览器证据。

### 在隔离环境启动人工验收

在正常 Mac 终端进入**本工作树目录**，确认本机 PostgreSQL 和 `psql` 可用，沿用自动测试的
PGDATABASE/PGHOST/PGUSER 等环境变量。下述命令复用测试夹具：创建临时 Git 仓库、随机独立 schema、
随机 loopback 端口，载入当前工作树的网页代码；不启动 Agent 调度，也不调用真实模型。
只有测试临时仓库会创建提交及发布候选，不操作当前项目及其他已有工作树。

```sh
python3 -B - <<'PY'
import threading
from http.server import ThreadingHTTPServer
from unittest.mock import patch
import seed
from test_seed_material_web import MaterialHTTPPostgresTests

case = MaterialHTTPPostgresTests('test_http_full_bundle_preview_save_prepare_confirm_history_and_reload')
failure = None
try:
    case.setUp()
    print('验收 URL:', case.url + '/materials?area=context', flush=True)
    print('隔离仓库:', case.root, 'schema:', case.schema, flush=True)
    print('命令：fail=模拟后续验证失败；normal=恢复验证；restart=重启 HTTP 监听；quit=清理退出', flush=True)
    while True:
        command = input('验收> ').strip()
        if command == 'quit':
            break
        if command == 'fail' and failure is None:
            failure = patch.object(seed.engine, 'verify', side_effect=RuntimeError('人工验收注入验证失败'))
            failure.start()
        elif command == 'normal' and failure is not None:
            failure.stop()
            failure = None
        elif command == 'restart':
            port = case.server.server_port
            case.stop()
            case.p.recover_publications()
            case.server = ThreadingHTTPServer(('127.0.0.1', port), seed.Console)
            case.server.boot = case.base
            case.thread = threading.Thread(target=case.server.serve_forever, daemon=True)
            case.thread.start()
            print('HTTP 已重启，原 URL 不变', flush=True)
finally:
    if failure is not None:
        failure.stop()
    case.doCleanups()
PY
```

终端保持运行，再打开输出的 URL。记录浏览器名称/版本、操作时间、隔离仓库和 schema。
`quit` 会删除隔离数据；退出前保存截图和反馈。此处 `restart` 验证 HTTP 监听重建，
**不代表整个 Python 进程重启**；跨进程持久化由自动化测试
`test_failed_ready_records_survive_new_process_without_activation` 覆盖，真实浏览器跨进程续验仍未验证。
启动失败时保留终端完整错误，停止验收；不要改用生产服务执行下述发布步骤。

### 逐步操作及预期结果

| 编号 | 操作 | 预期结果与留证 |
| --- | --- | --- |
| B1 | 从主页分别进入“项目资料”和“Agent 指令”，检查七个锚点及登记来源 | 四章节对应 PROJECT-CONTEXT.md；共享/主 Agent/Worker 分别对应 AGENTS.md、agents/main.md、agents/worker.md；可见完整发布 SHA、source_commit、草稿版本及基线。初始缺失文件明确标注，不误称已存在。截图两个入口。 |
| B2 | 四个章节及三个指令各填写不同标记，如 browser-goal、browser-shared；裁定填写“确认来源：本次人工验收记录 B2”；确认文件勾选 | 编辑后显示未保存提示，准备发布和启用禁用；切换锚点不会丢失同包内容。保存前不要点导航离开页面。 |
| B3 | 点击“预览编辑内容”，展开预览及“已发布原文与当前编辑差异” | 编辑正文保留，Markdown 标题及代码块可见；预览不增加草稿版本，已发布原文及 SHA 不变。Agent 页地址保留 area=agents，刷新后仍在该入口（未保存正文不会承诺跨手动刷新保留）。 |
| B4 | 展开来源，复制 README.md 中的 `source ' text`，选择来源和架构章节并导入 | 来源片段和路径/完整 SHA 出现在架构编辑区，裁定章节无自动新增；仍属未保存，发布禁用。保存一次，看到“草稿已保存，尚未生效”，版本递增；发布 SHA 不变。 |
| B5 | 主 Agent 内容留空但保留文件勾选；Worker 末尾输入两个换行，保存并刷新 | 主 Agent 是存在的空文件，Worker 尾部空行保留；七个编辑区内容可恢复查看。取消文件勾选会明确表示删除，并禁用发布直到保存。 |
| B6 | 在 Worker 中粘贴下方攻击样本，预览、保存、展开差异、历史及原文 | 样本按文字呈现，无弹窗、无 SVG/图片事件执行；浏览器控制台 `window.seedXss` 为 undefined。截图预览及差异。不要将该样本填入裁定章节。 |
| B7 | 保存后点击“准备发布已保存整包”，等待完成 | 候选状态为 ready 才出现人工启用确认；显示 request_id、已保存版本、完整基线/候选 SHA、冻结差异及验证日志。发布指针仍未改变。截图完整确认信息。 |
| B8 | 在同一页面修改任一指令而不保存 | 人工启用及准备发布立即禁用，提示先保存；撤回到原正文后才能继续。禁用 JavaScript 并从另一个旧表单提交改过正文的启用请求，也应被 409 拒绝并保留输入（自动化已覆盖）。 |
| B9 | 对 B7 冻结差异逐项核对，只在此隔离仓库勾选人工确认并填写验收说明，然后点启用 | 未勾选确认不能启用；明确确认后状态 activated，发布指针等于该候选；已发布原文对应七个输入。展开历史，能看到绑定版本、确认说明和已发布内容。截图前后 SHA。 |
| B10 | 同一草稿版本打开 A/B 两个页签；A 修改保存，B 输入另一标记保存 | B 返回冲突，原输入仍在，服务器最新草稿及发布状态只读可见；不能静默覆盖 A。旧页准备/启用均被拒绝；不要直接刷新冲突页面，先复制用户输入。 |
| B11 | 在 B 保留冲突或未保存输入；A 再保存；B 等待至少 12 秒 | B 出现服务器更新提示，输入和冲突详情不被自动刷新覆盖。清空编辑再检查两个入口和主页 `/api/revision` 对应的版本变化（自动化已校验签名一致）。 |
| B12 | 保存新草稿，终端输入 fail，再准备发布；然后输入 normal，重新载入并准备 | 第一条记录 failed，错误可见，不显示验证成功且不推进发布；正常重新准备得到新的 ready 请求，旧失败记录仍可查看。 |
| B13 | 保持 failed 和 ready 记录，终端输入 restart，再刷新 | 草稿、失败及待启用记录仍在；未勾选人工确认的 ready 不自动推进发布。人工记录这是 HTTP 重启，不登记为跨进程浏览器通过。 |
| B14 | 缩窄浏览器窗口到约 375px，展开长路径、SHA、正文、差异和历史；回到主页 | 内容可读且可滚动，按钮可操作，任务/阶段入口和版本信息仍显示。现有任务与阶段提交行为由自动回归验证；本隔离夹具不运行调度，不能据此宣称真实模型或任务执行通过。 |

攻击样本（直接粘贴为正文，不在控制台执行）：

```text
</textarea></pre></script><script>window.seedXss=1</script><svg onload="window.seedXss=2"><img src=x onerror="window.seedXss=3">[click](javascript:alert(1)) ![x](data:text/html,bad)
```

### 待验证与失败反馈

当前 B1–B14 全部是**待用户执行**，没有真实浏览器截图或通过结论。
Node DOM 替身、HTTP 和 PostgreSQL 自动化证据各自保留，不能代替真实浏览器。
真实模型/app-server 冒烟、浏览器跨 Python 进程重启，以及当前项目的实际启用均未执行。

失败请按以下格式回复，并在 `quit` 前留存截图/终端日志：

```text
步骤：B__；浏览器/版本：__；时间：__
隔离 URL / schema：__
原草稿版本 / 服务器最新版本：__ / __
request_id / 状态：__ / __
发布 SHA / 基线 SHA / 候选 SHA：__ / __ / __
操作与预期：__
实际现象、HTTP 状态、页面错误或控制台报错：__
输入是否保留 / 发布指针是否变化：__ / __
附件：截图、相关日志（不包含表单 token）
```

若全部通过，反馈浏览器信息、已执行编号及留证位置；由既有人工验收流程处理本实现的候选，
本工作树保持未提交状态，不自动创建、提交或启用实现候选。

本轮默认沙箱首次全量运行 74 项（新增 JS 测试载入前）：46 通过、26 跳过、2 项本地监听 PermissionError。
两项为 test_seed_restart.ReloadTests.test_active_execution_finishes_then_new_version_continues_same_service
及 test_seed_stage.StageHTTPTests.test_post_validation_error_returns_editable_escaped_inputs；
获准允许 loopback 后全量 75 项通过（26 跳过），未改写或跳过这两项测试。
人工验收命令已实际启动并立即 quit，退出码 0；见 materials-manual-fixture.log。
该启动检查没有打开浏览器、没有发布候选，日志内临时 URL 已关闭。

本轮真实 PostgreSQL/HTTP 复核：
`SEED_TEST_PG=1 python3 -B -m unittest -v test_seed_pg test_seed_project_pg test_seed_material_web`：
34 项全部通过，119.174 秒；日志 `docs/verification/materials-postgres-followup.log`。
`git diff --check`、`node --check seed_ui.js` 均通过。保留已有修改，无切分支、提交、推送或当前项目启用。

## 原始基线重启兼容性修复（2026-11-07）

从 `f65ad07` 原样提取 `test_seed_restart.py`，捕获其临时服务输出确认：
子进程在 `seed.py` 顶层导入 `seed_material_web` 时抛出
`ModuleNotFoundError`。原始夹具仅复制 seed.py 和有限领域/UI stub，
因此候选测试新增 stub 无法修复 verify 实际运行的原始基线。
复现日志：`docs/verification/materials-baseline-restart-before.log`。

修复把资料网页适配器加载延迟到资料动作、页面及完整领域刷新签名调用。
旧隔离领域只有恢复接口时沿用四参数 revision；完整领域仍绑定 `engine.ROOT`，
计算全部资料签名，数据库或适配器错误正常暴露，不静默降级。
启动恢复使用 `Project()` 的默认 `engine.ROOT`，保留原有恢复机制和旧构造契约。

重启回归夹具恢复为原始的最小 stub，不复制资料适配器；进程异常退出时
断言附带完整输出。另补两项测试，验证完整领域刷新仍绑定服务仓库、
并且领域故障不会被兼容分支隐藏。原始重启测试两项通过，子进程日志：
`docs/verification/materials-baseline-restart-child.log`。

本轮未修改 verify 的基线提取和验证规则，未跳过原始重启测试。
人工浏览器验收继续按上面的 B1–B14 执行，仍未验证、不自动启用。
当前实现没有新建提交，交由既有流程生成实现候选；不把隔离测试的提交作为实现候选。

候选验证命令：调用未修改的
`seed_engine.verify(Path.cwd(), 'f65ad07')`；从该基线提取 10 个原始测试文件，
59 项通过（19 项按既有开关跳过），随后当前完整套件 77 项通过（26 项按既有开关跳过）。
完整输出见 `docs/verification/materials-candidate-verification.log`。
`python3 -B -m unittest -v test_seed_material_web.MaterialFormTests`：11 项通过；
`git diff --check`、`node --check seed_ui.js` 通过。
执行器仅检查 `codex --version`（0.164.0），未调用真实模型或声称其冒烟通过。

真实 PostgreSQL/HTTP 复核命令：
`SEED_TEST_PG=1 python3 -B -m unittest -v test_seed_pg test_seed_project_pg test_seed_material_web`，
34 项全部通过（121.574 秒），见 `docs/verification/materials-postgres-restart-fix.log`。
该进程在新增两项无数据库契约测试前已加载测试模块；这两项另在上述 77 项完整套件和
11 项表单套件中通过。所有数据库用例沿用独立 schema 和临时 Git 仓库。
本轮保留已有实现，未切分支、提交、推送、修改其他已有工作树或启用当前项目。

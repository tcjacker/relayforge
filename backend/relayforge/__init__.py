"""RelayForge 的后端：.relayforge/ 下事实文件的读写、需求、问题、测试集、测试环境、会话、钩子、页面和命令行。

目录按业务分包：
  core/          通用：路径和小工具、文档读写、会话记录解析、调用后台模型、应用的标记（trust）、会话身份、代写记录
  facts/         项目的事实和大图：模块、功能点、旅程、设计稿、决定、进行中的改动、边做边记、整理会话、读代码起草大图、页面
  requirements/  需求：记录和状态（record）、工作目录归谁（owners）、拆分、立项评估，以及测试阶段：用例、测试轮次、逐条验收
  issues/        问题：记录、初判、修复、交给 AI 排查、和需求互转、复查
  testsets/      测试集：存储、查看、编辑、用户的决定、随需求落定、停下来时的检查、运行、桥接文件、命令行
  envs/          测试环境：存储、清洗、保密值和外部依赖、准备步骤、查看、编辑、交给 AI、读配置文件、命令行
  sessions/      会话：记录、每一轮的步骤、历史、追溯、提交上的出处、另存到本机、总结
  project/       项目：登记、初始化、数据外置、发布、分支、本机服务、钩子、状态
  flows.py       跨业务的用户动作：需求的确认完成、放弃、恢复、改状态，问题的确认修好
  cli.py         rf.py 的命令行

层次和目录无关，每个模块属于五层之一，只能导入同层或下层（tests/test_layers.py 检查，函数里的导入也算）：
  基础（core 里的大部分）→ 记录（requirements/owners）→ 领域（各业务的数据）→ 能力（在数据上做事）→ 流程和入口（flows、cli、各个 cli）
入口是包旁边的 rf.py。这里把各模块的名字汇到一起，方便 `import rf` 的地方（比如测试）直接用。
"""
from .core.common import *  # noqa: F401,F403
from .core.documents import *  # noqa: F401,F403
from .facts.mapfacts import *  # noqa: F401,F403
from .core.transcript import *  # noqa: F401,F403
from .core.model import *  # noqa: F401,F403
from .facts.designs import *  # noqa: F401,F403
from .facts.overview import *  # noqa: F401,F403
from .sessions.record import *  # noqa: F401,F403
from .project.registry import *  # noqa: F401,F403
from .facts.page import *  # noqa: F401,F403
from .facts.inflight import *  # noqa: F401,F403
from .sessions.archivestore import *  # noqa: F401,F403
from .requirements.owners import *  # noqa: F401,F403
from .requirements.testrecords import *  # noqa: F401,F403
from .facts.facts import *  # noqa: F401,F403
from .requirements.record import *  # noqa: F401,F403
from .facts.drafts import *  # noqa: F401,F403
from .facts.notes import *  # noqa: F401,F403
from .facts.extract import *  # noqa: F401,F403
from .issues.record import *  # noqa: F401,F403
from .issues.triage import *  # noqa: F401,F403
from .issues.fixes import *  # noqa: F401,F403
from .issues.probes import *  # noqa: F401,F403
from .issues.convert import *  # noqa: F401,F403
from .testsets.store import *  # noqa: F401,F403
from .testsets.views import *  # noqa: F401,F403
from .testsets.edits import *  # noqa: F401,F403
from .testsets.decisions import *  # noqa: F401,F403
from .testsets.lifecycle import *  # noqa: F401,F403
from .testsets.bridges import *  # noqa: F401,F403
from .testsets.checks import *  # noqa: F401,F403
from .testsets.runs import *  # noqa: F401,F403
from .project.publish import *  # noqa: F401,F403
from .sessions.trailers import *  # noqa: F401,F403
from .sessions.trace import *  # noqa: F401,F403
from .sessions.archive import *  # noqa: F401,F403
from .sessions.summary import *  # noqa: F401,F403
from .sessions.steps import *  # noqa: F401,F403
from .sessions.history import *  # noqa: F401,F403
from .sessions.monitor import *  # noqa: F401,F403
from .sessions.recorder import *  # noqa: F401,F403
from .flows import *  # noqa: F401,F403
from .project.status import *  # noqa: F401,F403
from .project.hooks import *  # noqa: F401,F403
from .project.init import *  # noqa: F401,F403
from .project.server import *  # noqa: F401,F403
from .facts.bootstrap import *  # noqa: F401,F403
# 这几个模块没有 __all__，下面这些名字以前是借道 cli.py 的导入进到 rf 里的；现在直接从所在的模块点名导出
from .requirements.testing import req_retest, req_skip_test, req_steps, req_test_mark, req_to_test, req_verdict  # noqa: F401
from .requirements.usecases import case_add, case_decide, case_edit, case_remove, case_save, req_cases, req_classify  # noqa: F401
from .issues.review import issue_review, issue_review_apply, issue_review_discard, issue_review_run, issue_review_status, review_job_path  # noqa: F401
from .project.branches import BranchError, branch_list, branch_refresh, branch_switch  # noqa: F401
from .cli import *  # noqa: F401,F403

from .requirements.conflicts import conflicts, conflicts_text, in_progress, predict, predict_text  # noqa: F401

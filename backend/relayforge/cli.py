"""命令行：rf.py 的子命令和参数。"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

from .core.common import DIR, ID_FILE, PORT, find_root, is_project, link_facts, log, main_checkout, registry_path, secure_home
from .core.documents import all_docs, pending_groups
from .core.transcript import read_transcript
from .sessions.record import current_transcript, here_source, load_sessions, stable_tag
from .project.registry import project_summaries, projects, read_registry, register
from .facts.page import render
from .facts.facts import confirm, merge, reject, write_summaries
from .requirements.record import (
    REQ_STATES, MergeBusy, NeedsSync, SESSION_PHASES, STAGES, brief_text, find_requirement, load_requirements,
    past_sessions, req_attach, req_depend, req_keep, req_new, req_choose, req_start,
)
from .facts.cli import design_command
from .issues.cli import issue_command
from .project.cli import branch_command, publish_command
from .requirements.subagents import intake_decisions, request as subagent_request
from .requirements.cli import req_cases_command, req_testing
from .testsets.gate import GateFailed
from .flows import req_done, req_drop, req_restore, req_set
from .facts.notes import NOTE_KINDS, note, stop_check
from .project.status import codex_event, hook_event, hook_sweep, report_unsandboxed
from .facts.extract import capture
from .testsets.cli import test_command
from .envs.cli import env_command
from .project.hooks import hook_input, run_hook
from .sessions.usage import usage_report, usage_text
from .sessions.trace import trace_commit, trace_ref, trace_text, why, why_text
from .sessions.trailers import ensure_here
from .sessions.archive import archive_sweep
from .sessions.summary import summarize
from .sessions.monitor import MONITOR_KINDS, monitor, monitor_text
from .sessions.recorder import monitor_record
from .sessions.history import (
    HISTORY_KINDS, history_search, history_session, history_show, RECAP_TURNS, recap_text, search_text, session_text,
    show_text, steps_text,
)
from .project.init import init
from .project.server import serve, sync_command
from .facts.bootstrap import bootstrap_command
from .facts.mapfacts import map_session_id, set_trunk
from .facts.cli import fresh_command
from .core.proxy import forward, proxied, should_forward
from .core.guard import codex_main as guard_codex, main as guard_main
from .core.trust import gate_command, require_app, reset_actor, set_actor
from .requirements.accept import req_accept
from .requirements.conflicts import conflicts, conflicts_text, predict, predict_text
from .project.datastore import data_export, data_import, data_migrate, data_where
from .requirements.splits import review


def main(argv=None):
    parser = argparse.ArgumentParser(description='RelayForge capture')
    sub = parser.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('init')
    p.add_argument('target', nargs='?', default='.')
    p.add_argument('--hooks', action='store_true')
    p = sub.add_parser('hook')
    p.add_argument('name', choices=['session-start', 'capture', 'subagent-stop', 'stop', 'event', 'sweep', 'guard', 'codex'])
    p = sub.add_parser('note', help='会话边做边记：进展、计划、决定、需求内容')
    p.add_argument('kind', choices=NOTE_KINDS)
    p.add_argument('values', nargs='*')
    p.add_argument('--guess', action='store_true', help='这是推断，不是用户明确说的')
    p.add_argument('--reason', help='decision 的理由；new-feature 的一句话定位')
    p.add_argument('--head', help='fact：写进哪一节（功能点默认「规则与边界」）')
    p.add_argument('--replaces', help='fact：要换掉的旧陈述里的一段话')
    p.add_argument('--module', help='new-feature：属于哪个模块')
    p.add_argument('--root')
    p.add_argument('--session')
    p.add_argument('--transcript')
    p = sub.add_parser('capture')
    p.add_argument('--root')
    p.add_argument('--transcript', required=True)
    p.add_argument('--session', required=True)
    p.add_argument('--trigger', default='manual')
    p = sub.add_parser('split', help='应用检查会话拆分建议及用户确认')
    p.add_argument('--root')
    p.add_argument('--session', required=True)
    p.add_argument('--transcript', required=True)
    p.add_argument('--parent')
    p.add_argument('--approve', help='用户点击「同意拆分」时显示的建议编号')
    p.add_argument('--orders', help='同意拆分时用户改过的先后：JSON 对象，建议里的序号（从 0 起）→ after / before / none')
    p = sub.add_parser('serve')
    p.add_argument('--root')
    p.add_argument('--port', type=int, default=PORT)
    p.add_argument('--host', default='127.0.0.1', help='默认只在本机可访问；:: 表示所有网卡')
    p = sub.add_parser('sync')
    p.add_argument('--root')
    p.add_argument('--session')
    p.add_argument('--transcript')
    p.add_argument('--port', type=int, default=PORT)
    p.add_argument('--local', action='store_true')
    p.add_argument('--name', help='给这个会话起名字；写 auto 表示交还给模型起名')
    p.add_argument('--end', action='store_true', help='标记这个会话已经结束')
    p = sub.add_parser('pending')
    p.add_argument('--root')
    p = sub.add_parser('bootstrap', help='导入已有项目：读一遍代码，写出第一版待确认的大图')
    p.add_argument('--root')
    p.add_argument('--json', action='store_true')
    p.add_argument('--force', action='store_true', help='项目里已有模块或功能点时仍然追加')
    for name in ('confirm', 'reject'):
        p = sub.add_parser(name)
        p.add_argument('ids', nargs='*')
        p.add_argument('--root')
        p.add_argument('--source', help='出处，默认是当前会话的最新一条用户消息')
        if name == 'confirm':
            p.add_argument('--module', action='append', default=[], help='确认这个模块名下的全部待确认对象')
            p.add_argument('--all', action='store_true')
            p.add_argument('--except', dest='skip', nargs='*', default=[])
    p = sub.add_parser('projects')
    p.add_argument('--forget', metavar='NAME', help='从登记表里去掉一个项目（不删除任何文件）')
    p.add_argument('--add', metavar='PATH', help='把一个已经初始化过的项目登记进来')
    p.add_argument('--json', action='store_true')
    p = sub.add_parser('req', help='需求：new 新建（同时开分支和工作目录，先立项）、list 列出、brief 程序算出的影响、attach 把会话挂到需求上、start 用户确认立项、done 用户确认完成并合并、drop 用户取消需求、restore 恢复取消前的状态、set 用户手动改状态（talk/build/test/check/done/dropped）、depend 用户设置前置需求（需求编号 前置需求编号 [--remove]）；'
                       '测试阶段：classify 测试会话给验收条件归类、steps 测试会话写人工验收的检查步骤、verdict 测试会话交回结论（ok / problems）、retest 开发会话说修好了再测、'
                       'to-test 用户交给测试、skip-test 用户跳过测试直接验收、accept 用户逐条验收（需求编号 序号 ok|bad --say "哪里不对"）（这三个只由应用触发）；'
                       '测试用例：case-add 加一条（验收条件序号 --name --cat --given --when --then）、case-edit 改一条、case-rm 删一条（立项以后改立项写的要 --why，等用户同意）、cases 看用例和对接的结果；'
                       'case-ok、case-no 用户同意 / 不同意用例的改动、case-save 用户在应用里改用例（只由应用触发）')
    p.add_argument('action', choices=['new', 'list', 'brief', 'attach', 'start', 'done', 'drop', 'restore', 'set', 'depend', 'keep', 'choose', 'classify', 'steps', 'verdict', 'retest', 'to-test', 'skip-test', 'test-kicked', 'test-told', 'accept',
                                          'case-add', 'case-edit', 'case-rm', 'cases', 'case-ok', 'case-no', 'case-save'])
    p.add_argument('values', nargs='*')
    for phase in SESSION_PHASES:
        p.add_argument(f'--{phase}-cli', choices=['claude', 'codex'], help='新建时指定会话的 CLI')
        p.add_argument(f'--{phase}-model', help='新建时指定会话的模型，省略则由 CLI 决定')
    p.add_argument('--testing-follow', action='store_true', help='choose：测试会话改回跟着开发会话的 CLI 和模型')
    p.add_argument('--remove', action='store_true', help='depend：去掉这条前置需求')
    p.add_argument('--off', action='store_true', help='keep：取消保留，分支和别的已取消需求一样到期清理')
    p.add_argument('--need', help='depend：这个需求要用到前置需求的什么（一句话）；对已有的前置需求只改这一句')
    p.add_argument('--name', help='case-add、case-edit：用例的名称（覆盖哪种情况）')
    p.add_argument('--cat', help='case-add、case-edit：正常、边界、异常')
    p.add_argument('--given', help='case-add、case-edit：前提（业务的话）')
    p.add_argument('--when', help='case-add、case-edit：操作（业务的话）')
    p.add_argument('--then', help='case-add、case-edit：预期（业务的话）')
    p.add_argument('--check', help='case-edit：改挂到第几条验收条件')
    p.add_argument('--unlinked', action='append', default=[], help='verdict ok：一条没对接的用例「用例编号｜为什么对接不了」，可以写几次')
    p.add_argument('--root')
    p.add_argument('--skip-gate', action='store_true', help='done：用户在应用里选择跳过合并前的检查（只由用户触发）')
    p.add_argument('--json', action='store_true')
    p.add_argument('--test', action='store_true', help='attach：这是测试会话（只由应用登记）')
    p.add_argument('--why', help='classify：为什么这样归类；case-add、case-edit、case-rm、classify 在立项以后：为什么要改（等用户同意）')
    p.add_argument('--clear', action='store_true', help='steps：清掉这条验收条件的检查步骤，重写')
    p.add_argument('--summary', help='verdict ok：一句话总结')
    p.add_argument('--problem', action='append', default=[], help='verdict problems：一条问题「哪条｜现象｜判断」，可以写几次')
    p.add_argument('--file', help='verdict problems：问题清单的 JSON 文件（- 表示标准输入）；accept bad：当时的日志尾部（- 表示标准输入）')
    p.add_argument('--say', help='accept bad：一句话说哪里不对')
    p.add_argument('--ran', help='accept bad：当时运行的是哪个提交、启动后代码有没有改过（应用给）')
    p = sub.add_parser('design', help='设计稿：save 保存或修改一份（HTML 从标准输入给，或用 --file）、skip 说明这个需求为什么不需要设计稿、list 列出')
    p.add_argument('action', choices=['save', 'skip', 'list'])
    p.add_argument('values', nargs='*', help='skip 的理由')
    p.add_argument('--title', help='设计稿的标题')
    p.add_argument('--feature', action='extend', nargs='+', default=[], help='画的功能点：现有的编号，或「新增：名称」；可以写多个')
    p.add_argument('--id', dest='ident', help='修改这个需求已有的一份设计稿')
    p.add_argument('--note', help='一句话说明画的是什么')
    p.add_argument('--file', help='从这个文件读 HTML，不用标准输入')
    p.add_argument('--root')
    p.add_argument('--session')
    p.add_argument('--transcript')
    p.add_argument('--json', action='store_true')
    p = sub.add_parser('issue', help='问题：new 记一条（会话里记的加 --source Agent）、list 列出、show 看一条、triage 初判、reply 回复、set 改设置、'
                       'fix 开始修复、attach 挂会话、brief 修复要知道的事、handback 修复会话交回修复报告、confirm 用户确认修好了、unconfirm 用户撤回确认、reject 用户说还有问题、to-req 转为需求、'
                       'from-req 立项中的需求转为问题、probe 交给 AI 排查、finding 排查会话记下排查结论、review 让 AI 复查待处理问题（后台跑完）、review-run 后台进程接手复查（程序用）、review-status 复查的进度和结果、review-discard 关掉复查结果、review-apply 确认所选结果')
    p.add_argument('action', choices=['new', 'list', 'show', 'triage', 'reply', 'set', 'fix', 'attach', 'brief', 'handback', 'confirm', 'unconfirm', 'reject', 'to-req', 'from-req', 'probe', 'finding', 'review', 'review-run', 'review-status', 'review-discard', 'review-apply'])
    p.add_argument('values', nargs='*')
    p.add_argument('--desc', help='new：补充说明，看到了什么、怎么操作的')
    p.add_argument('--source', choices=['你', 'Agent', '测试页'], help='new：谁记的；在会话里记的写 Agent')
    p.add_argument('--image', action='extend', nargs='+', default=[], help='new、reply、reject：截图文件')
    p.add_argument('--feature', help='new、from-req：相关的功能点编号')
    p.add_argument('--op', action='append', default=[], help='new：测试页记下的一步操作（可以写多个）')
    p.add_argument('--duplicate-of', help='new：Agent 已确认重复的原单编号，补充复现证据')
    p.add_argument('--possible-duplicate', help='new：Agent 可能重复的候选编号，新单保留候选提示')
    p.add_argument('--duplicate-reason', help='new：为什么怀疑重复、哪些证据还不够')
    p.add_argument('--observed', help='new、finding：按复现步骤实际看到的故障')
    p.add_argument('--verified-fix', help='finding：按原步骤验证修复通过后，填写当前分支上对应的完整修复提交编号')
    p.add_argument('--fix-cli', choices=['claude', 'codex'], help='fix：修复会话的 CLI')
    p.add_argument('--fix-model', help='fix：修复会话的模型，省略则由 CLI 决定')
    p.add_argument('--changed', help='handback：改了什么')
    p.add_argument('--verify', help='handback：怎样验证')
    p.add_argument('--no-regression', help='handback：实在写不成回归数据时，写清为什么（记进修复报告）')
    p.add_argument('--kind', help='finding：排查的结论类别 bug|request|usage|ask')
    p.add_argument('--text', help='finding：结论，一两句话')
    p.add_argument('--steps', help='new、finding：怎么复现')
    p.add_argument('--where', help='finding：问题在哪（文件、函数）')
    p.add_argument('--verified', help='finding：实际复现、验证过的')
    p.add_argument('--inferred', help='finding：只是推断、没验证的')
    p.add_argument('--to-probe', action='store_true', help='reply：这句话交给开着的排查会话，不再做只看产品事实的初判')
    p.add_argument('--reason', help='set finish-resolved / finish-closed：手动结束处理的备注')
    p.add_argument('--root')
    p.add_argument('--session')
    p.add_argument('--transcript')
    p.add_argument('--skip-gate', action='store_true', help='confirm：用户在应用里选择跳过合并前的检查（只由用户触发）')
    p.add_argument('--json', action='store_true')
    p = sub.add_parser('test', help='测试集：new 新建、add 加数据、edit 改一条、rm 删一条（改已经落定的数据要用户确认）、run 运行、analyze 写分析、list 列表、show 看一个、'
                       'bridge 生成让项目自己的测试命令（unittest/pytest、node --test、JUnit 5）也跑到测试集的桥接文件（bridge-run 是桥接文件自己调用的）；只由用户（应用）触发：stale 标成过期、revive 恢复、move 改挂靠、approve 同意改动、reject 不同意改动、save 以用户身份保存数据、seen 标为看过了、config 改语言、接入代码、classpath、build；approve-build 同意执行构建命令（只认应用的标记）')
    p.add_argument('action', choices=['new', 'add', 'edit', 'rm', 'run', 'analyze', 'list', 'show', 'bridge', 'bridge-run', 'stale', 'revive', 'move', 'approve', 'reject', 'save', 'seen', 'config',
                                      'approve-build'])
    p.add_argument('values', nargs='*', help='new：名称；其余：测试集编号，edit/rm 再跟数据序号，approve/reject 再跟改动编号')
    p.add_argument('--feature', help='new：挂在哪个功能点（必填）；run、list：只要这个功能点的；move：改挂到这个功能点')
    p.add_argument('--entry', help='new：接入代码的路径，相对代码根目录（必填）')
    p.add_argument('--lang', help='new：接入代码的语言 python、node、java（不写就按 --entry 的扩展名）；bridge：只生成这种语言的桥接文件')
    p.add_argument('--classpath', action='append', default=[], help='new：Java 测试集的类路径（可选，用户或 AI 明确给；可以写多个，也可以用系统的路径分隔符连起来）')
    p.add_argument('--build', help='new：Java 测试集每次运行前先运行的编译命令（可选，比如 mvn -q compile；不写就不替用户编译）。第一次执行前要用户在应用里同意')
    p.add_argument('--print', dest='fingerprint', help='approve-build：确认框里看过的那条构建命令的指纹（命令或目录变了就不算同意）')
    p.add_argument('--check', action='append', default=[], help='new：对应的验收条件（原文，或这个需求的第几条）；可以写多个')
    p.add_argument('--name', help='add、edit：这一条覆盖哪种情况（add 必填）')
    p.add_argument('--cat', help='add、edit：正常、边界、异常')
    p.add_argument('--input', help='add、edit：输入，JSON')
    p.add_argument('--expect', help='add、edit：期望输出，JSON')
    p.add_argument('--raises', help='add、edit：期望它报错，报错信息里含这段话（代替 --expect）')
    p.add_argument('--usecase', help='add、edit：这条数据对接的是这个需求的哪条测试用例（用例编号）；有用例的需求，测试会话加数据必须写')
    p.add_argument('--file', help='add：一个 JSON 数组（或 {"cases": [...]}）批量加；save：要保存的数据；- 表示标准输入')
    p.add_argument('--why', help='edit、rm：改动或删除已经落定的数据时的理由（必填，等用户确认）')
    p.add_argument('--text', help='analyze：分析，哪些过了、哪些没过、为什么')
    p.add_argument('--req', help='run、list、show、save：按这个需求眼里的数据（默认是当前工作目录所属的需求）；move：改挂到这个需求，none 表示不挂')
    p.add_argument('--fix', help='run、list、show、save：按这个修复眼里的数据')
    p.add_argument('--all', action='store_true', help='run：全部在用的测试集（按主线的数据）')
    p.add_argument('--case', help='run：只跑这一条；save：只保存这一条（new 表示新加）；seen：只标这一条')
    p.add_argument('--dir', help='run：在这个代码目录里跑（默认：需求或修复的工作目录，或当前目录）')
    p.add_argument('--timeout', type=int, help='run：一个测试集最多跑多少秒（默认 120）')
    p.add_argument('--stale', action='store_true', help='list：只列过期的')
    p.add_argument('--reason', help='stale：为什么标成过期')
    p.add_argument('--path', help='bridge：桥接文件写到哪（相对代码根目录）')
    p.add_argument('--env', help='run：用这套测试环境（编号或名称）的环境变量；不写就用这台电脑上正在用的那套')
    p.add_argument('--no-env', action='store_true', help='run：不带测试环境的环境变量')
    p.add_argument('--prep-check', action='store_true', help='run：不运行，只说测试环境里有哪些要执行的准备步骤还没经用户同意（应用先问你用）')
    p.add_argument('--root')
    p.add_argument('--session')
    p.add_argument('--transcript')
    p.add_argument('--json', action='store_true')
    p = sub.add_parser('env', help='测试环境：list 列表、show 看一套（保密的值打码）、scan 重新读配置文件、check 检查依赖能不能连上（只做 TCP 连接）、'
                       'resolve 这次请求会带上哪些请求头和地址参数（保密的打码）、propose 会话把读项目得到的建议交给用户；只由用户（应用）触发：new 新建、copy 复制、'
                       'rename 改名、rm 删除（移到 history）、save 整份保存（标准输入 JSON）、use 选用哪一套、user 选当前测试用户、reveal 取明文、adopt / ignore 采纳或忽略 AI 的建议、'
                       'approve 同意执行准备步骤、ai-secrets 允许或不允许 AI 的会话运行时带上保密值（这三样要应用的标记）；prep 看准备步骤、prep-run 手动执行一遍（只执行同意过的）、'
                       'handoff 「让 AI 来处理」交给会话的话；ai-values 应用替会话取保密值（只在你允许 AI 的会话带上保密值时）；人工验收：plan 这次怎么启动（保密的打码）、launch 应用启动服务前取启动的全部设置（要应用的标记）、accept-clean 清掉验收的临时目录和项目副本')
    p.add_argument('action', choices=['list', 'show', 'scan', 'check', 'resolve', 'propose', 'new', 'copy', 'rename', 'rm', 'save', 'use', 'user', 'reveal', 'adopt', 'ignore',
                                      'prep', 'prep-run', 'approve', 'ai-secrets', 'handoff', 'plan', 'launch', 'accept-clean',
                                      'ai-values'])
    p.add_argument('values', nargs='*', help='new：名称；rename：编号 新名字；user：编号 用户编号；adopt/ignore：编号 建议的序号…；其余：编号或名称')
    p.add_argument('--note', help='new、rename：一句说明')
    p.add_argument('--name', help='copy：新的名字')
    p.add_argument('--user', help='resolve、reveal --resolve：以哪个测试用户（默认是当前测试用户）')
    p.add_argument('--resolve', action='store_true', help='reveal：给出代入变量以后的明文请求头和参数')
    p.add_argument('--file', help='save、propose：JSON 文件，- 表示标准输入')
    p.add_argument('--timeout', type=float, help='check：每个依赖最多等几秒（默认 2）')
    p.add_argument('--dir', help='prep、prep-run：在这个代码目录里（默认项目主目录）')
    p.add_argument('--step', help='handoff prep：哪一条准备步骤（默认是失败的那条）')
    p.add_argument('--req', help='handoff set：按这个需求眼里的结果；plan、launch、prep-run、handoff start、accept-clean：这个需求的工作目录（默认项目主目录）')
    p.add_argument('--service', action='append', help='approve：同意这个服务的启动命令；plan、launch：只看这个服务；handoff start：哪个服务（可以写几次）')
    p.add_argument('--due', action='store_true', help='prep-run：只执行这次要执行的（和运行测试集之前一样），不是全部')
    p.add_argument('--copy', choices=['reuse', 'fresh'], help='launch：项目副本沿用上次的（默认）还是重新复制')
    p.add_argument('--fix', help='handoff set：按这个修复眼里的结果')
    p.add_argument('--root')
    p.add_argument('--session')
    p.add_argument('--json', action='store_true')
    p = sub.add_parser('publish', help='发布：status 看现在有什么没发布、list 以前的发布记录；run 用户把当前分支推送到远端、pull 用户拉取远端的新提交（只快进）')
    p.add_argument('action', choices=['status', 'run', 'pull', 'list'])
    p.add_argument('--remote', help='run、status：当前分支还没有上游时，推送到哪个远端（只有一个远端时可以不写；有好几个必须写）')
    p.add_argument('--fetch', action='store_true', help='status：先从远端抓取，看到最新的领先落后（有超时，不会问口令）')
    p.add_argument('--root')
    p.add_argument('--json', action='store_true')
    p = sub.add_parser('branch', help='项目主目录的分支：list 查看本地和已获取的远端分支，refresh 获取远端分支，switch 切换分支')
    p.add_argument('action', choices=['list', 'refresh', 'switch'])
    p.add_argument('name', nargs='?')
    p.add_argument('--from-remote', action='store_true')
    p.add_argument('--local-name')
    p.add_argument('--expected-current')
    p.add_argument('--expected-tracking')
    p.add_argument('--confirmed-pending', type=int)
    p.add_argument('--root')
    p.add_argument('--json', action='store_true')
    p = sub.add_parser('data', help='数据放在哪：where 看这个项目的记录放在哪；export 打包备份；import 用户从导出的包恢复（本机已有记录时要 --replace）；migrate 用户把旧项目的记录迁到本机')
    p.add_argument('action', choices=['where', 'export', 'import', 'migrate'])
    p.add_argument('file', nargs='?')
    p.add_argument('--replace', action='store_true')
    p.add_argument('--root')
    p.add_argument('--json', action='store_true')
    p = sub.add_parser('trace', help='追溯（只读）：一个需求、修复或问题带来了哪些提交、改了哪些文件、挂了哪些会话、有没有发布；--commit 从一个提交出发')
    p.add_argument('ref', nargs='?', help='需求 R-…、修复 F-… 或问题 I-033 / #033')
    p.add_argument('--commit', help='从这个提交出发：它属于哪个需求或修复')
    p.add_argument('--root')
    p.add_argument('--json', action='store_true')
    p = sub.add_parser('usage', help='用量（只读）：按会话种类和模型统计需求、修复、问题或单个会话的 token')
    p.add_argument('ref', nargs='?', help='需求 R-…、修复 F-… 或问题 I-…')
    p.add_argument('--session', help='会话编号或原始会话 ID')
    p.add_argument('--root')
    p.add_argument('--json', action='store_true')
    p = sub.add_parser('why', help='为什么这样写（只读）：一个文件或几行代码最近是哪些提交改的，各属于哪个需求或修复，给出它的需求说明、验收条件、裁定、会话（或问题的现象和修复报告）')
    p.add_argument('spec', help='文件、文件:行 或 文件:起-止')
    p.add_argument('--root')
    p.add_argument('--json', action='store_true')
    p = sub.add_parser('history', help='历史查询（只读）：search 在需求、问题、裁定、会话进展、总结和标签里查；show 一条的完整摘要；session 当时的聊天（本机存档或还在的原始记录）')
    p.add_argument('action', choices=['search', 'show', 'session'])
    p.add_argument('values', nargs='*', help='search：要查的词；show：R-… I-… D-… S-…；session：会话编号')
    p.add_argument('--tag', action='append', default=[], help='search：只要带这个标签的（概括标签或 RF-/M-/D-/I- 编号）')
    p.add_argument('--feature', action='append', default=[], help='search：只要涉及这个功能点的')
    p.add_argument('--file', action='append', default=[], help='search：只要改过或提到这个文件的')
    p.add_argument('--kind', action='append', default=[], choices=list(HISTORY_KINDS), help='search：只查这一类')
    p.add_argument('--limit', type=int, default=20, help='search：最多列几条')
    p.add_argument('--grep', help='session：只看含这个词的消息（--steps 时是轮次）')
    p.add_argument('--tail', type=int, help='session：只看最后几条（--steps 时是几轮）')
    p.add_argument('--steps', action='store_true', help='session：步骤视图，按轮次列出读了什么、跑了什么、改了哪些文件、失败和被拒绝的、提交、用时和 token')
    p.add_argument('--offset', type=int, help='session --steps：从第几轮之后开始列（0 为从头开始）')
    p.add_argument('--brief', action='store_true', help='session：续接用的简要步骤（最后 3 轮，每轮最后 8 步）；没有记录时什么也不输出')
    p.add_argument('--root')
    p.add_argument('--json', action='store_true')
    p = sub.add_parser('monitor', help='会话监控：不带动作时从会话记录算出 Agent 有没有照开场白的规矩做、哪些工具用了哪些没用、在哪里打转（只读）；'
                       'record 是程序用的：会话说完一轮时，应用（设置里打开了「后台记录」）把新说完的几轮追加到 ~/.relayforge/monitor/项目/')
    p.add_argument('action', nargs='?', choices=['record'])
    p.add_argument('--agent', choices=['claude', 'codex'], help='record：会话用的 CLI')
    p.add_argument('--transcript', help='record：会话记录的路径（不给就按会话 ID 找）')
    p.add_argument('--waits', help='record：应用看到的等批准，JSON：[{"at": 开始时间, "seconds": 秒}]')
    p.add_argument('--closed', action='store_true', help='record：会话关掉了，最后一轮没说完也记下')
    p.add_argument('--since', help='只看这一天以后还有动静的会话（2026-11-01）')
    p.add_argument('--kind', action='append', default=[], choices=list(MONITOR_KINDS), help='只看这一种会话')
    p.add_argument('--owner', help='只看这个需求、修复或问题的会话（R-… F-… I-…）')
    p.add_argument('--session', help='只看这一个会话（S-… 或会话的 ID）')
    p.add_argument('--root')
    p.add_argument('--json', action='store_true')
    p = sub.add_parser('conflicts', help='需求之间的冲突（只读）：进行中的需求和修复各改了哪些文件、谁和谁改到同一处、和合并目标或彼此合并会不会冲突（git merge-tree 预演，不碰工作目录）；--predict 需求编号：按历史推测这个需求会动到哪些文件、可能撞上哪些进行中的需求')
    p.add_argument('--predict', help='需求编号：立项时按历史预测')
    p.add_argument('--root')
    p.add_argument('--json', action='store_true')
    p = sub.add_parser('summarize', help='程序用：确认完成以后，给需求写总结和概括标签，聊到了但没记下的决定交给用户确认（由应用在后台调用）')
    p.add_argument('ref', help='需求编号 R-…')
    p.add_argument('--root')
    p.add_argument('--json', action='store_true')
    p = sub.add_parser('archive', help='程序用：sweep 把已经完成、还没存到本机的会话记录补存一份（应用打开项目时调用）')
    p.add_argument('action', choices=['sweep'])
    p.add_argument('--root')
    p.add_argument('--json', action='store_true')
    p = sub.add_parser('trailers', help='需求和修复的工作目录里，每个提交由 git 钩子自动带上 RelayForge-Requirement / -Fix / -Issue / -Session 几行；install 给当前工作目录装上（打开会话时应用会调用）')
    p.add_argument('action', choices=['install'])
    p.add_argument('--root')
    p.add_argument('--json', action='store_true')
    p = sub.add_parser('sessions', help='列出在这个项目目录里聊过、又不属于任何需求的 Claude Code 会话')
    p.add_argument('--root')
    p.add_argument('--json', action='store_true')
    p = sub.add_parser('fresh', help='功能点跟上代码：files 对应哪些代码文件、drift 哪些可能过时、brief 给核对会话看的、show 等你处理的核对结果；'
                       'propose 核对会话交回建议（只有核对会话能用）；只由用户（应用）触发：adopt / ignore 采纳或忽略建议、done 核对完')
    p.add_argument('action', choices=['files', 'drift', 'brief', 'propose', 'show', 'adopt', 'ignore', 'done'])
    p.add_argument('values', nargs='*')
    p.add_argument('--file', help='propose：JSON 文件，- 表示标准输入')
    p.add_argument('--session')
    p.add_argument('--root')
    p.add_argument('--json', action='store_true')
    p = sub.add_parser('map', help='大图编辑会话：session 这个项目固定的那个会话，source 当前这条消息的出处（写在改过的陈述行尾）')
    p.add_argument('action', choices=['session', 'source', 'trunk'])
    p.add_argument('values', nargs='*', help='trunk：用户旅程编号（只由用户触发：设为主干，合并前必跑它用到的功能点的测试集）')
    p.add_argument('--off', action='store_true', help='trunk：不再是主干')
    p.add_argument('--root')
    p.add_argument('--json', action='store_true')
    p = sub.add_parser('subagent', help='需求开发会话委派 subagent')
    p.add_argument('action', choices=['decision', 'status', 'run'])
    p.add_argument('task', nargs='?')
    p.add_argument('--model')
    p.add_argument('--reason')
    p.add_argument('--root')
    p = sub.add_parser('state')
    p.add_argument('--root')
    p.add_argument('--session', help='只看这个会话')
    p.add_argument('--json', action='store_true', help='输出 JSON，没有同步过时输出 null')
    for name in ('merge', 'status', 'render'):
        p = sub.add_parser(name)
        p.add_argument('--root')
    args = parser.parse_args(argv)
    secure_home()  # 本机记录的目录只给你自己（以前建出来是 755）
    report_unsandboxed(args.cmd)  # 会话里的 rf.py 发现自己不在沙箱里：告诉应用，提醒你
    # 记录写不进去（Codex 的沙箱不让写项目目录之外的外置记录）：把这条命令交给开这个会话的应用代写（proxy.py）
    action = getattr(args, 'action', None)
    if proxied(args.cmd, action if isinstance(action, str) else None):
        here = Path(args.root).resolve() if getattr(args, 'root', None) else find_root()
        if should_forward(args.cmd, action, here):
            return forward(sys.argv[1:] if argv is None else list(argv))
    # 只由用户触发的命令：程序自己校验（trust.py），不只靠会话的权限规则。没有应用的标记、又不是人在终端里直接运行的，拒绝
    try:
        actor, _ = gate_command(args.cmd, getattr(args, 'action', None))
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1
    handle = set_actor(actor)
    try:
        return run_command(args)
    finally:
        reset_actor(handle)


def run_command(args):
    if args.cmd == 'subagent':
        try:
            if args.action == 'decision':
                result = intake_decisions(args.task)
            else:
                result = subagent_request(args.action, task=args.task, model=args.model, reason=args.reason)
            print(json.dumps(result, ensure_ascii=False))
            return 0
        except (TypeError, ValueError) as error:
            print(str(error), file=sys.stderr)
            return 1
    if args.cmd == 'init':
        root, notes = init(args.target, args.hooks)
        print(f'已初始化 {root / DIR}')
        for remark in notes:
            print('提示：' + remark)
        return 0
    if args.cmd == 'hook':
        if args.name == 'event':
            return hook_event(hook_input())
        if args.name == 'sweep':
            return hook_sweep()
        if args.name == 'codex':  # 平常由 rf.py 入口直接处理（PreToolUse 不加载整个程序）；这里只在被当作模块调用时用到
            data = hook_input()
            if not os.environ.get('RELAYFORGE_SESSION_ID'):
                return 0  # 不是 RelayForge 开的会话
            return guard_codex(data) if isinstance(data, dict) and data.get('hook_event_name') == 'PreToolUse' else codex_event(data)
        if args.name == 'guard':  # 平常由 rf.py 入口直接走 core/guard.py（不加载整个程序）；这里只在被当作模块调用时用到
            return guard_main([])
        return stop_check(hook_input()) if args.name == 'stop' else run_hook(args.name)
    if args.cmd == 'projects':
        if args.add:
            place = find_root(Path(args.add).resolve())
            if not place or not is_project(place):
                print('这个目录还没有初始化，先运行 init。', file=sys.stderr)
                return 1
            register(place)
        if args.json:
            print(json.dumps(project_summaries(), ensure_ascii=False))
            return 0
        if args.forget:
            kept = [p for p in read_registry() if p['name'] != args.forget]
            registry_path().write_text(json.dumps({'projects': kept}, ensure_ascii=False, indent=2), encoding='utf-8')
        rows = project_summaries()
        for row in rows:
            print(f'{row["name"]}\t进行中的会话 {row["active"]}\t最近同步 {row["last"] or "还没有"}\t{row["root"]}')
        if not rows:
            print('还没有登记任何项目。在项目目录里运行 init。')
        return 0
    root = Path(args.root).resolve() if getattr(args, 'root', None) else find_root()
    if root and (root / ID_FILE).is_file():
        link_facts(root)  # --root 直接点名的：同样按项目 ID 接回外置的数据
    if args.cmd == 'serve' and (not root or not is_project(root)):
        root = None  # started outside a project: show whatever is registered
    elif not root or not is_project(root):
        print('没有找到 .relayforge/，请先运行 init。', file=sys.stderr)
        return 1
    # 事实只在项目主目录里有一份：在需求或修复的工作目录里运行也读写主目录。here 只用来认出「我在哪个需求或修复里」和找聊天记录。
    here, home = root, (main_checkout(root) if root else None)
    if args.cmd == 'split':
        try:
            require_app('确认拆分需求')
            print(json.dumps(review(home, args.transcript, args.session, args.parent, args.approve, args.orders), ensure_ascii=False))
        except (OSError, ValueError) as error:
            print(str(error), file=sys.stderr)
            return 1
        return 0
    if args.cmd == 'capture':
        try:
            out = capture(home, args.transcript, args.session, args.trigger)
        except Exception as error:
            log(root, f'capture failed: {error!r}')
            print(f'沉淀失败：{error}', file=sys.stderr)
            return 1
        print(f'已写入 {out}' if out else '没有新的消息需要处理。')
        return 0
    if args.cmd == 'bootstrap':
        return bootstrap_command(home, args.force, args.json)
    if args.cmd == 'pending':
        groups = pending_groups(home)
        for group in groups:
            print(f'## {group["title"]}' + (f'（{group["id"]}）' if group['id'] else '') + f'：{len(group["items"])} 个')
            for item in group['items']:
                print(f'- {item["id"]} {item["title"]}' + ('［整个对象待确认］' if item['status'] == 'draft' else ''))
                for line in item['lines']:
                    print('    ' + re.sub(r'〔[^〕]*〕', '', line.lstrip('- ')).strip())
            print()
        print(f'共 {sum(len(g["items"]) for g in groups)} 个对象待确认。' if groups else '没有待确认的条目。')
        return 0
    if args.cmd in ('confirm', 'reject'):
        source = args.source
        if not source:
            found, session_id = current_transcript(root)
            messages = read_transcript(found) if found else []
            last = max((i + 1 for i, m in enumerate(messages) if m['role'] == 'user'), default=0)
            source = f'{stable_tag(home, session_id)}#{last}' if session_id and last else 'S-unknown#0'
        groups = pending_groups(home)
        targets = list(args.ids)
        if args.cmd == 'confirm':
            wanted = set(args.module)
            unknown = wanted - {g['id'] for g in groups} - {g['title'] for g in groups}
            if unknown:
                print('这些模块没有待确认的对象：' + '、'.join(sorted(unknown)), file=sys.stderr)
            targets += [item['id'] for g in groups if args.all or g['id'] in wanted or g['title'] in wanted for item in g['items']]
            targets = [t for t in dict.fromkeys(targets) if t not in set(args.skip)]
        if not targets:
            print('没有指定要处理的对象。', file=sys.stderr)
            return 1
        act = confirm if args.cmd == 'confirm' else reject
        done = [t for t in targets if act(home, t, source)]
        missed = [t for t in targets if t not in done]
        write_summaries(home)
        render(home)
        left = sum(len(g['items']) for g in pending_groups(home))
        print(('已确认' if args.cmd == 'confirm' else '已否决') + f' {len(done)} 个：' + ('、'.join(done) or '无') + f'。出处记为 {source}。还剩 {left} 个待确认。')
        if missed:
            print('没有处理（找不到，或它没有待确认的内容）：' + '、'.join(missed))
        return 0
    if args.cmd == 'serve':
        server = serve(home, args.port, args.host)
        names = '、'.join(p['name'] for p in projects()) or '还没有登记项目'
        scope = f'项目：{names}；' + ('只在本机可访问' if args.host in ('127.0.0.1', 'localhost', '::1') else '没有认证，同一网络里的机器都能访问')
        print(f'RelayForge 页面：http://127.0.0.1:{server.server_address[1]}  （{scope}，Ctrl-C 停止）', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        return 0
    if args.cmd == 'sync':
        return sync_command(root, args.session, args.transcript, args.port, args.local, args.name, args.end)
    if args.cmd == 'req':
        try:
            if args.action == 'new':
                fields = {f'{phase}_{field}': getattr(args, f'{phase}_{field}')
                          for phase in SESSION_PHASES for field in ('cli', 'model')}
                made = req_new(root, ' '.join(args.values), **fields)
                print(json.dumps(made, ensure_ascii=False) if args.json else f'已新建 {made["id"]} {made["title"]}，分支 {made["branch"]}，工作目录 {made["worktree"]}')
            elif args.action == 'done':
                try:
                    made = req_done(root, ' '.join(args.values), skip_gate=args.skip_gate)
                except GateFailed as error:  # 合并闸门挡住了：应用把没过的交回需求的会话，或者等你同意准备步骤
                    if args.json:
                        print(json.dumps({'error': str(error), 'gate': error.result}, ensure_ascii=False))
                    print(str(error), file=sys.stderr)
                    return 5
                except (NeedsSync, MergeBusy) as error:  # the app reacts to these: send the work back to the requirement's session, or wait its turn
                    if args.json:
                        sync = isinstance(error, NeedsSync)
                        print(json.dumps({'error': str(error), 'sync': sync, 'conflict': sync and bool(error.files), 'busy': not sync,
                                          'base': getattr(error, 'base', None), 'files': getattr(error, 'files', []), 'loose': getattr(error, 'loose', []),
                                          'merging': getattr(error, 'merging', False), 'holder': getattr(error, 'holder', None)}, ensure_ascii=False))
                    print(str(error), file=sys.stderr)
                    return 3 if isinstance(error, NeedsSync) else 4
                print(json.dumps(made, ensure_ascii=False) if args.json else f'{made["id"]} {made["title"]} 已确认完成并合并。')
            elif args.action == 'drop':
                made = req_drop(root, ' '.join(args.values))
                print(json.dumps(made, ensure_ascii=False) if args.json else f'{made["id"]} {made["title"]} 已放弃。聊过的内容提交在分支 {made["branch"]} 上（{made["commit"]}），需要时可以从 Git 里找回。')
            elif args.action == 'restore':
                made = req_restore(root, ' '.join(args.values))
                print(json.dumps(made, ensure_ascii=False) if args.json else f'{made["id"]} 已恢复。')
            elif args.action == 'start':
                made = req_start(root, ' '.join(args.values))
                print(json.dumps(made, ensure_ascii=False) if args.json else f'{made["id"]} {made["title"]} 已确认立项，可以开始开发。')
            elif args.action == 'set':
                if len(args.values) != 2:
                    raise ValueError('用法：req set 需求编号 状态（talk 立项中、build 进行中、test 测试中、check 待验收、done 已完成、dropped 已取消）')
                made = req_set(root, *args.values)
                print(json.dumps(made, ensure_ascii=False) if args.json else f'{made["id"]} {made["title"]} 现在是{STAGES[made["stage"]]}。')
            elif args.action == 'depend':
                if len(args.values) != 2:
                    raise ValueError('用法：req depend 需求编号 前置需求编号 [--remove]（前置需求确认完成后，这个需求才能开始开发）')
                made = req_depend(root, *args.values, remove=args.remove, need=args.need)
                print(json.dumps(made, ensure_ascii=False) if args.json else f'{made["id"]} {made["title"]} 的前置需求：'
                      + ('、'.join('《' + d['title'] + '》' for d in made['depends_on']) or '没有'))
            elif args.action == 'keep':
                if len(args.values) != 1:
                    raise ValueError('用法：req keep 需求编号 [--off]（已取消的需求：保留它的分支，不自动清理）')
                made = req_keep(root, args.values[0], keep=not args.off)
                print(json.dumps(made, ensure_ascii=False) if args.json else f'{made["id"]} {made["title"]} 的分支'
                      + ('设为保留。' if made['keep_branch'] else '不再保留，到期会清理。'))
            elif args.action == 'choose':
                if args.intake_cli or args.intake_model:
                    raise ValueError('建好以后只能改开发会话和测试会话的 CLI 和模型，立项会话的不能改。')
                wants = [x for x in (('development', args.development_cli, args.development_model, False) if args.development_cli or args.development_model else None,
                                     ('testing', args.testing_cli, args.testing_model, args.testing_follow) if args.testing_cli or args.testing_model or args.testing_follow else None) if x]
                if len(args.values) != 1 or not wants:
                    raise ValueError('用法：req choose 需求编号 --development-cli claude|codex [--development-model 模型]，'
                                     '或 --testing-cli claude|codex [--testing-model 模型]，或者 --testing-follow（测试跟开发会话一样）')
                for phase, cli_, model_, follow in wants:
                    made = req_choose(root, args.values[0], phase, cli_, model_, follow)
                print(json.dumps(made, ensure_ascii=False) if args.json else f'{made["id"]}：已改。' + ('测试会话跟着开发会话。' if made['testing_follows_development'] else ''))
            elif args.action == 'brief':
                made = find_requirement(root, ' '.join(args.values))
                if made['state'] == 'done':
                    raise ValueError('这个需求已经完成，不再计算影响。')
                print(json.dumps({k: made[k] for k in ('id', 'title', 'state', 'level', 'ladder', 'gate', 'brief', 'intake_assessment')}, ensure_ascii=False) if args.json else brief_text(made))
            elif args.action == 'accept':
                if len(args.values) != 3:
                    raise ValueError('用法：req accept 需求编号 序号 ok|bad [--say "哪里不对"] [--file -（当时的日志尾部）]')
                log = (sys.stdin.read() if args.file == '-' else Path(args.file).read_text(encoding='utf-8')) if args.file else ''
                made = req_accept(root, *args.values, say=args.say or '', log=log, ran=args.ran or '')
                print(json.dumps(made, ensure_ascii=False) if args.json else (f'第 {made["n"]} 条：通过。' if made['result'] == 'ok' else made['say']))
            elif args.action in ('case-add', 'case-edit', 'case-rm', 'cases', 'case-ok', 'case-no', 'case-save'):
                made, said = req_cases_command(root, args)
                print(json.dumps(made, ensure_ascii=False) if args.json else said)
            elif args.action in ('classify', 'steps', 'verdict', 'retest', 'to-test', 'skip-test', 'test-kicked', 'test-told'):
                made, said = req_testing(root, args)
                print(json.dumps(made, ensure_ascii=False) if args.json else said)
            elif args.action == 'attach':
                tag = req_attach(root, *args.values, test=args.test) if len(args.values) == 2 else None
                if not tag:
                    print('用法：req attach 需求编号 会话编号；需求要有自己的工作目录。', file=sys.stderr)
                    return 1
                print(tag)
            else:
                found = load_requirements(root)
                if args.json:
                    print(json.dumps(found, ensure_ascii=False))
                for r in [] if args.json else found:
                    print(f'{r["id"]} {r["title"]}（{REQ_STATES.get(r["state"], r["state"])}，分支 {r["branch"]}，{len(r["sessions"])} 个会话）')
                if not found and not args.json:
                    print('还没有需求。')
        except ValueError as error:
            print(str(error), file=sys.stderr)
            return 1
        return 0
    if args.cmd == 'note':
        found, session_id = (Path(args.transcript), args.session) if args.transcript and args.session else current_transcript(root, args.session)
        if not session_id:
            print('没有找到当前会话，记不了。', file=sys.stderr)
            return 1
        try:
            print(note(root, session_id, found, args.kind, args.values, args.guess, args.reason, args.head, args.replaces, args.module))
        except (ValueError, IndexError) as error:
            print(str(error), file=sys.stderr)
            return 1
        return 0
    if args.cmd == 'design':
        return design_command(root, args)
    if args.cmd == 'issue':
        return issue_command(root, args)
    if args.cmd == 'test':
        return test_command(root, args)
    if args.cmd == 'env':
        return env_command(root, args)
    if args.cmd == 'publish':
        return publish_command(root, args)
    if args.cmd == 'branch':
        return branch_command(root, args)
    if args.cmd == 'data':
        try:
            if args.action == 'migrate':
                out = data_migrate(home)
                text = f'已迁移 {out["files"]} 个文件到 {out["store"]}，提交 {out["commit"]}。仓库里原来那份留在 {out["backup"]}'
            elif args.action == 'where':
                out = data_where(root)
                text = f'记录放在 {out["store"]}' if out['external'] else '这个项目的记录还在仓库的 .relayforge/ 里（旧项目，需要迁移）。'
            elif not args.file:
                raise ValueError(f'要给出文件：rf.py data {args.action} 文件')
            elif args.action == 'export':
                out = data_export(home, args.file)
                text = f'已导出 {out["files"]} 个文件到 {out["file"]}'
            else:
                out = data_import(home, args.file, args.replace)
                text = f'已导入 {out["files"]} 个文件。' + (f'原来那份留在 {out["kept"]}' if out['kept'] else '')
        except (ValueError, OSError) as error:
            if args.json:
                print(json.dumps({'ok': False, 'error': str(error)}, ensure_ascii=False))
            print(str(error), file=sys.stderr)
            return 1
        print(json.dumps(out, ensure_ascii=False) if args.json else text)
        return 0
    if args.cmd == 'trace':
        try:
            out = trace_commit(home, args.commit) if args.commit else trace_ref(home, args.ref) if args.ref else None
            if out is None:
                raise ValueError('用法：trace R-… | F-… | I-033，或 trace --commit 哈希。')
        except ValueError as error:
            print(str(error), file=sys.stderr)
            return 1
        print(json.dumps(out, ensure_ascii=False) if args.json else out.get('text') or trace_text(out))
        return 0
    if args.cmd == 'usage':
        try:
            out = usage_report(home, args.ref, args.session)
        except ValueError as error:
            print(str(error), file=sys.stderr)
            return 1
        print(json.dumps(out, ensure_ascii=False) if args.json else usage_text(out))
        return 0
    if args.cmd == 'why':
        try:
            out = why(home, args.spec)
        except ValueError as error:
            print(str(error), file=sys.stderr)
            return 1
        print(json.dumps(out, ensure_ascii=False) if args.json else why_text(out))
        return 0
    if args.cmd == 'history':
        try:
            v = list(args.values)
            if args.offset is not None and (args.action != 'session' or args.brief):
                raise ValueError('--offset 只用于 history session --steps。')
            if args.action == 'search':
                out = history_search(home, v, args.tag, args.feature, args.file, args.kind, args.limit)
                text = search_text(out)
            elif args.action == 'show':
                out = history_show(home, v[0] if v else '')
                text = show_text(out)
            else:
                if args.brief:
                    out = history_session(home, v[0] if v else '', None, RECAP_TURNS, True)
                    text = recap_text(out)
                else:
                    out = history_session(home, v[0] if v else '', args.grep, args.tail, args.steps, offset=args.offset)
                    text = (steps_text if args.steps else session_text)(out, args.grep)
        except ValueError as error:
            print(str(error), file=sys.stderr)
            return 1
        print(json.dumps(out, ensure_ascii=False) if args.json else text)
        return 0
    if args.cmd == 'monitor':
        try:
            if args.action == 'record':
                try:
                    waits = json.loads(args.waits) if args.waits else []
                except ValueError:
                    raise ValueError('--waits 要是 JSON 数组。')
                out = monitor_record(home, args.session, (args.kind or ['chat'])[-1], args.owner, args.agent, args.transcript,
                                     waits if isinstance(waits, list) else [], args.closed)
                print(json.dumps(out, ensure_ascii=False) if args.json else f'记下了 {out["written"]} 轮：{out["file"]}')
                return 0
            out = monitor(home, args.since, args.kind, args.owner, args.session)
        except ValueError as error:
            print(str(error), file=sys.stderr)
            return 1
        print(json.dumps(out, ensure_ascii=False) if args.json else monitor_text(out))
        return 0
    if args.cmd == 'conflicts':
        try:
            out = predict(home, args.predict) if args.predict else conflicts(home)
        except ValueError as error:
            print(str(error), file=sys.stderr)
            return 1
        print(json.dumps(out, ensure_ascii=False) if args.json else (predict_text if args.predict else conflicts_text)(out))
        return 0
    if args.cmd == 'summarize':
        try:
            out = summarize(home, args.ref)
        except ValueError as error:
            print(str(error), file=sys.stderr)
            return 1
        print(json.dumps(out, ensure_ascii=False) if args.json else f'{out["id"]} 已总结：{"、".join(out["topics"])}。' + (f'交给用户确认的决定：{"、".join(out["proposed"])}。' if out['proposed'] else ''))
        return 0
    if args.cmd == 'archive':
        out = archive_sweep(home)
        print(json.dumps(out, ensure_ascii=False) if args.json else '\n'.join(f'{i["id"]}：存了 {len(i["copied"])} 个，原始记录已不在 {len(i["missing"])} 个' for i in out['items']) or '没有要补存的。')
        return 0
    if args.cmd == 'trailers':
        out = ensure_here(here)
        print(json.dumps(out, ensure_ascii=False) if args.json else ('提交钩子已装好。' if out['ok'] else '没有装：' + out['why']))
        return 0
    if args.cmd == 'sessions':
        found = past_sessions(home)
        if args.json:
            print(json.dumps(found, ensure_ascii=False))
        for s in [] if args.json else found:
            print(f'{s["updated"]}  {s["session_id"]}  {s["title"]}')
        if not found and not args.json:
            print('这个项目目录里没有找到 Claude Code 的会话。')
        return 0
    if args.cmd == 'fresh':
        return fresh_command(root, args)
    if args.cmd == 'map':
        if args.action == 'trunk':
            made = set_trunk(root, ' '.join(args.values), not args.off)
            print(json.dumps(made, ensure_ascii=False) if args.json else f'用户旅程 {made["id"]} ' + ('已设为主干。' if made['trunk'] else '不再是主干。'))
            return 0
        if args.action == 'source':
            source = here_source(root)
            if not source:
                print('没有找到当前会话。', file=sys.stderr)
                return 1
            print(source)
            return 0
        sid = map_session_id(root)
        found, _ = current_transcript(root, sid)
        out = {'session_id': sid, 'tag': stable_tag(home, sid), 'transcript': str(found) if found and found.stem == sid else None}
        print(json.dumps(out, ensure_ascii=False) if args.json else f'{out["tag"]}  {sid}' + ('' if out['transcript'] else '（还没开过）'))
        return 0
    if args.cmd == 'state':
        sessions = load_sessions(home)
        mine = args.session or os.environ.get('CLAUDE_CODE_SESSION_ID')
        pick = next((x for x in sessions if x.get('session_id') == mine), None if args.session else sessions[0] if sessions else None)
        if args.json:
            print(json.dumps(pick, ensure_ascii=False))
            return 0
        if not pick:
            print('还没有同步过会话。先运行 sync。')
            return 0
        print((home / pick['file']).read_text(encoding='utf-8'))
        return 0
    if args.cmd == 'render':
        print(f'已生成 {render(home)}')
        return 0
    if args.cmd == 'merge':
        print(f'已合并 {merge(home)} 个条目。')
        return 0
    counts = {kind: sum(1 for _ in all_docs(home, (kind,))) for kind in ('modules', 'features', 'decisions', 'documents')}
    waiting = len(list((home / DIR / 'inbox').glob('*.json')))
    print(f'模块 {counts["modules"]}，功能点 {counts["features"]}，裁定 {counts["decisions"]}，文档登记 {counts["documents"]}；收件箱待合并 {waiting} 个。')
    return 0

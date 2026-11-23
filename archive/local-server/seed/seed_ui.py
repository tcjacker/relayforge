"""Project overview, task list and focused task dialogs for the local console."""
from datetime import datetime, timezone
import html
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import seed_decision
import seed_stage as rules
import seed_stage_web

HERE = Path(__file__).resolve().parent
LABELS = {'queued': '等待执行', 'working': '执行中', 'planned': '规划完成',
          'answered': '已回复，排队中', 'waiting': '等待你裁定', 'failed': '执行中断',
          'review': '等待验收', 'activated': '已启用', 'closed': '已关闭'}
NEEDS_INPUT = {'waiting', 'failed', 'review'}
ACTIVE = {'working', 'planned'}


def esc(value):
    return html.escape(str(value), quote=True)


def icon(name):
    paths = {
        'grid': '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
        'tasks': '<path d="m3 6 2 2 4-4M12 6h9M3 13h5m4 0h9M3 20h5m4 0h9"/>',
        'inbox': '<path d="M4 4h16l2 12v4H2v-4L4 4Z"/><path d="M2 15h6l2 3h4l2-3h6"/>',
        'clock': '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
        'check': '<path d="m5 12 4 4L19 6"/>',
        'arrow': '<path d="M5 12h14m-5-5 5 5-5 5"/>',
        'plus': '<path d="M12 5v14M5 12h14"/>',
        'close': '<path d="m6 6 12 12M18 6 6 18"/>',
        'spark': '<path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5L12 3Z"/>',
    }
    return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + paths[name] + '</svg>'


def badge(state):
    return f'<span class="badge state-{esc(state)}"><i></i>{esc(LABELS.get(state, state))}</span>'


def short_title(data):
    title = data.get('title') or data.get('goal', '未命名任务').split('。')[0]
    return title if len(title) <= 72 else title[:71] + '…'


def when(value):
    try:
        stamp = datetime.fromisoformat(value).astimezone(ZoneInfo('Asia/Shanghai'))
        return stamp.strftime('%m月%d日 %H:%M')
    except (ValueError, TypeError):
        return str(value)


def dialog(ident, title, body, size=''):
    return (f'<dialog id="{esc(ident)}" class="{size}" aria-label="{esc(title)}">'
            '<div class="dialog-heading"><div>'
            f'<h2>{esc(title)}</h2></div><button type="button" class="icon-button" data-close aria-label="关闭">'
            + icon('close') + '</button></div><div class="dialog-body">' + body + '</div></dialog>')


def version(boot, current):
    synced = boot == current
    body = ('<details class="version" aria-label="代码版本"><summary><span class="status-dot '
            + ('green' if synced else 'amber') + '"></span>'
            + ('运行版本已同步' if synced else '正在切换到新版本')
            + '<span class="mono">' + esc(boot[:9]) + '</span></summary><div class="version-content">'
            '服务启动时加载的提交：<code>' + esc(boot) + '</code><br>'
            'main 当前提交：<code>' + esc(current) + '</code>')
    if not synced:
        body += ('<p>当前 Project Guide 服务仍运行启动时加载的代码。暂停接手 queued（等待执行）和 answered（已回复，等待接手）队列，'
                 '当前执行结束后会自动重启加载新代码后再继续接手。</p><p>如果仍提示需要重启以加载新版本，在启动本服务的终端按 Ctrl-C，随后在原项目目录，沿用原数据库环境和原启动参数，'
                 '重新执行 <code>python3 -B archive/local-server/seed/seed.py</code>。自定义端口须保留 <code>--port</code> 参数及原端口值。</p>')
    return body + '</div></details>'


def progress_card(progress, state):
    elapsed = progress.get('elapsed_seconds', 0)
    if state in ACTIVE and progress.get('outcome') not in ('completed', 'interrupted'):
        elapsed = max(0, int((datetime.now(timezone.utc) - datetime.fromisoformat(progress['started_at'])).total_seconds()))
    live = (' data-elapsed-start="' + esc(progress['started_at']) + '"') if state in ACTIVE and progress.get('outcome') not in ('completed', 'interrupted') else ''
    return ('<div class="run-card"><div class="run-heading"><span class="agent-mark">' + icon('spark') + '</span><strong>'
            + esc(progress['phase']) + '</strong><span class="muted"' + live + '>' + str(elapsed) + ' 秒</span></div>'
            '<p>最近活动 · ' + esc(progress.get('activity', '等待活动')) + '</p><small>'
            + esc(when(progress.get('last_activity_at') or progress['started_at'])) + '</small></div>')


def attention_summary(state, data):
    if state == 'failed':
        return ('执行故障，无需产品裁定', ('自动化验收测试未通过，候选代码尚未启用。系统会自动安排 Agent 排查，最多两轮；超过上限后保留故障供维护者处理。' if 'FAILED (' in data.get('error', '') else '任务因执行错误停止，尚未提交新的验收结果。需要开发 Agent 排查原因。'), '查看故障与恢复')
    if state == 'waiting':
        return ('需要你决定', data.get('question') or 'Worker 未提供具体问题，请查看交接记录后要求补充。', '查看问题并回复')
    return ('等待你验收', data.get('summary') or '候选改动已准备好，请检查结果与验证证据。', '查看结果与验收')


def task_detail(task, rows, stage, jobs, form, field, fields):
    data = rules.merged(rows)
    state, seq = rows[-1]['state'], rows[-1]['seq']
    progress = data.get('progress')
    if state in ('answered', 'queued'):
        progress = None  # A previous run is history, not current queue activity.
    body = '<div class="detail-status">' + badge(state) + '<span class="muted">' + esc(when(rows[-1]['recorded_at'])) + '</span></div>'
    if state in ('answered', 'queued'):
        running = [short_title(rules.merged(r)) for ident, r in jobs.items() if ident != task and r[-1]['state'] in ACTIVE]
        reason = ('当前只有一个 Worker，正在执行“' + running[0] + '”，本任务等待它结束。') if running else '调度器将检查依赖和可用 Worker，满足条件后自动接手。'
        body += '<section class="action-brief"><h3>' + ('回复已保存，无需再次提交' if state == 'answered' else '任务已进入队列') + '</h3><p>' + esc(reason) + '</p><p>不需要再次点击启动。等待执行不表示已经完成。</p>'
        if state == 'answered':
            body += '<p><strong>已提交的处理意见：</strong>' + esc(data.get('reply', '')) + '</p>'
        body += '<button type="button" class="button secondary" onclick="location.reload()">刷新最新进展</button></section>'
    if state in NEEDS_INPUT:
        title, explanation, _ = attention_summary(state, data)
        body += '<section class="action-brief"><h3>' + esc(title) + '</h3><p>' + esc(explanation) + '</p>'
        if state == 'failed':
            body += '<p><strong>下一步：</strong>可点击下方“交给 Agent 排查并修复”，系统会保留现有代码和失败记录，让 Agent 找原因、修复并重新验证。你无需先修代码或判断日志。</p>'
        if state == 'waiting':
            body += '<p><strong>Agent 建议：</strong>' + esc(data.get('recommendation') or '尚未提供建议，可要求 Worker 补充选项及影响。') + '</p>'
        body += '</section>'
    body += '<h3>需求</h3><p class="prose">' + esc(data['goal']) + '</p><h3>验收条件</h3><pre>' + esc(data.get('acceptance', '未填写')) + '</pre>'
    if data.get('stage_id'):
        reasons = rules.blockers(task, jobs, stage)
        if reasons:
            body += '<p class="notice">' + esc('；'.join(reasons)) + '</p>'
    if progress:
        body += progress_card(progress, state)
    visible_fields = ['evidence']
    if state == 'waiting':
        visible_fields += ['question', 'recommendation']
    if state == 'failed' and data.get('error'):
        body += '<details class="disclosure"><summary>查看原始错误日志（技术信息）</summary><pre>' + esc(data['error']) + '</pre></details>'
    for key in visible_fields:
        if data.get(key):
            body += '<div class="detail-block"><h3>' + esc(fields.get(key, key)) + '</h3><pre>' + esc(data[key]) + '</pre></div>'
    if state == 'waiting':
        decision = seed_decision.card(data)
        choices = '<p class="prose">' + esc(decision.get('background', '')) + '</p><p><strong>推荐理由：</strong>' + esc(decision.get('recommendation', '请先补充推荐理由')) + '</p><fieldset class="decision-options"><legend>请选择处理方式</legend>'
        for option in decision['options']:
            choices += '<label class="decision-option"><input required type="radio" name="choice" value="' + esc(option['id']) + '"><span><strong>' + esc(option['label']) + '</strong><small>' + esc(option['impact']) + '</small></span></label>'
        choices += '<label class="decision-option"><input required type="radio" name="choice" value="other"><span><strong>其他意见</strong><small>上面的选项不合适时，填写你的想法。</small></span></label></fieldset><label>其他意见（仅选择其他时填写）<textarea name="other" maxlength="12000"></textarea></label>'
        body += form('decision', '提交选择', choices, task, seq)
    if state == 'failed':
        body += form('reply', '交给 Agent 排查并修复', '<input type="hidden" name="reply" value="请先诊断失败原因，再修复并重新验证。保留已有工作和检查点，不跳过失败测试，不降低验收标准；确需用户操作时解释具体步骤。">', task, seq)
        body += '<details class="disclosure"><summary>补充恢复说明</summary>' + form('reply', '保存说明并重试', field('reply', '已修复什么，或执行时需要注意什么'), task, seq) + '</details>'
    if state == 'review':
        body += '<div class="review-actions"><a class="button secondary" href="/diff/' + esc(task) + '" target="_blank" rel="noopener">查看候选代码差异</a>'
        body += form('activate', '验收通过，启用此提交', task=task, seq=seq) + '</div>'
        body += '<details class="disclosure"><summary>退回修改</summary>' + form('reply', '退回修改', field('reply', '需要补充什么'), task, seq) + '</details>'
    body += '<details class="disclosure"><summary>计划与交付记录</summary>'
    for key in ('plan', 'summary', 'diff', 'checkpoint'):
        if data.get(key):
            body += '<h3>' + esc(fields.get(key, key)) + '</h3><pre>' + esc(data[key]) + '</pre>'
    body += '</details><details class="disclosure"><summary>技术信息与完整事件</summary>'
    body += '<p>任务 <code>' + esc(task) + '</code></p>'
    for key in ('base', 'candidate'):
        if data.get(key):
            body += '<p>' + esc(fields.get(key, key)) + ' <code>' + esc(data[key]) + '</code></p>'
    if progress:
        body += '<p>运行 <code>' + esc(progress['run_id']) + '</code></p><p>日志 <code>' + esc(progress['log_path']) + '</code></p>'
    for event in rows:
        if set(event['data']) == {'progress'}:
            continue
        body += '<section class="event"><small>' + esc(when(event['recorded_at'])) + ' · ' + esc(LABELS.get(event['state'], event['state'])) + '</small><pre>' + esc(json.dumps(event['data'], ensure_ascii=False, indent=2)) + '</pre></section>'
    body += '</details>'
    if state in ('waiting', 'failed', 'review', 'queued'):
        body += '<details class="disclosure danger"><summary>关闭任务</summary>' + form('close', '关闭此任务', task=task, seq=seq) + '</details>'
    return dialog('task-' + task, short_title(data), body, 'task-dialog')


def revision(jobs, stage, current, boot=None, materials_signature=None):
    state = {'tasks': {task: rows[-1]['seq'] for task, rows in jobs.items()},
             'stage': stage, 'main': current, 'boot': boot or current, 'materials': materials_signature}
    return hashlib.sha256(json.dumps(state, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def render(jobs, boot, current, stage, params, error, form, field, fields, materials_signature=None):
    counts = {key: sum(rows[-1]['state'] in states for rows in jobs.values()) for key, states in {
        'active': ACTIVE, 'queued': {'queued', 'answered'}, 'attention': NEEDS_INPUT, 'done': {'activated'}}.items()}
    records = sorted(jobs.items(), key=lambda pair: (pair[1][-1]['state'] not in NEEDS_INPUT,
                     pair[1][-1]['state'] not in ACTIVE, -list(jobs).index(pair[0])))
    body = ['<div class="app"><aside class="sidebar"><a class="brand" href="/"><span class="brand-mark">P</span><strong>Project Guide</strong></a>'
            '<div class="project-switch"><span class="project-avatar">PG</span><div><strong>Project Guide</strong><small>本地项目</small></div></div>'
            '<p class="nav-label">工作空间</p><nav aria-label="项目导航">'
            '<a class="nav-item selected" href="#overview">' + icon('grid') + '项目概览</a>'
            '<a class="nav-item" href="#tasks" data-nav-filter="all">' + icon('tasks') + '任务<span>' + str(len(jobs)) + '</span></a>'
            '<a class="nav-item" href="#tasks" data-nav-filter="attention">' + icon('inbox') + '待处理<span>' + str(counts['attention']) + '</span></a>'
            '<a class="nav-item" href="#tasks" data-nav-filter="done">' + icon('clock') + '已启用记录</a><a class="nav-item" href="/materials?area=context">项目资料</a><a class="nav-item" href="/materials?area=agents">Agent 指令</a></nav>'
            '<div class="sidebar-bottom"><button type="button" class="help-button" data-open="help-dialog">使用说明 ' + icon('arrow') + '</button>'
            '<p><span class="status-dot green"></span>本地运行<span>1 个 Worker</span></p></div></aside>'
            '<div class="workspace"><header class="topbar"><span>工作空间 <b>/</b> Project Guide</span>' + version(boot, current) + '</header>'
            '<main id="overview"><div class="page-heading"><div><h1>项目概览</h1></div>'
            '<div class="heading-actions"><button type="button" class="button secondary" data-open="task-create-dialog">' + icon('plus') + '新建任务</button>'
            '<button type="button" class="button" data-open="stage-dialog">' + icon('spark') + ('查看阶段' if stage else '规划阶段') + '</button></div></div>']
    if boot != current:
        body.append('<div class="restart-notice"><span class="status-dot amber"></span>新版本已启用，当前执行结束后将自动切换并继续处理队列。<button type="button" data-version>查看指引</button></div>')
    body.append('<section class="stage-overview"><div><p class="eyebrow">当前阶段</p>')
    if stage:
        names = {'pending': '等待规划', 'planning': '正在规划', 'draft': '草稿待确认', 'failed': '规划中断', 'authorized': '执行中', 'accepted': '已通过验收'}
        body.append('<h2>' + esc(stage['goal']) + '</h2><p class="stage-description">' + esc(stage['acceptance']) + '</p>'
                    '<button type="button" class="text-button" data-open="stage-dialog">' + esc(names.get(stage['state'], stage['state'])) + ' · 查看阶段 ' + icon('arrow') + '</button></div>')
        total = len(stage['tasks']) if stage.get('tasks') else len(stage.get('cards', []))
        done = sum(jobs.get(t, [{}])[-1].get('state') == 'activated' for t in stage.get('tasks', []))
        body.append('<div class="stage-progress"><strong>' + str(done) + '<span> / ' + str(total) + '</span></strong><p>子任务已启用</p><div class="progress-track"><i style="width:' + str(int(done / total * 100) if total else 0) + '%"></i></div></div>')
    else:
        body.append('<h2>下一步，想完成什么？</h2><p class="stage-description">设定一个阶段目标，让主 Agent 拆解任务。你确认计划后，Worker 开始开发。</p>'
                    '<button type="button" class="text-button" data-open="stage-dialog">创建阶段目标 ' + icon('arrow') + '</button></div>'
                    '<div class="stage-illustration" aria-hidden="true"><span>' + icon('tasks') + '</span><div><i></i><i></i><i></i></div></div>')
    body.append('</section><section class="metrics" aria-label="项目状态">')
    for key, label, symbol in [('active', '执行中', 'spark'), ('queued', '等待执行', 'clock'), ('attention', '待处理事项', 'inbox'), ('done', '已启用', 'check')]:
        body.append('<button type="button" class="metric" data-filter="' + key + '"><span class="metric-label">' + icon(symbol) + label + '</span><strong>' + str(counts[key]) + '</strong></button>')
    body.append('</section><div class="content-grid"><section class="task-panel" id="tasks"><div class="panel-heading"><h2>开发任务</h2><span class="muted">' + str(len(jobs)) + ' 项</span></div>'
                '<div class="tabs" role="tablist" aria-label="筛选任务">')
    for key, label, number in [('all', '全部', len(jobs)), ('active', '执行中', counts['active']), ('attention', '待处理', counts['attention']), ('done', '已启用', counts['done'])]:
        body.append('<button type="button" role="tab" aria-selected="' + ('true' if key == 'all' else 'false') + '" data-filter="' + key + '">' + label + '<span>' + str(number) + '</span></button>')
    body.append('</div><div class="table-heading"><span>任务</span><span>状态</span><span>最近更新</span><span></span></div><div class="task-list">')
    dialogs = []
    for task, rows in records:
        data, last = rules.merged(rows), rows[-1]
        state = last['state']
        groups = 'all ' + ('active ' if state in ACTIVE else '') + ('attention ' if state in NEEDS_INPUT else '') + ('done ' if state == 'activated' else '') + ('queued ' if state in ('queued', 'answered') else '')
        body.append('<button type="button" class="task-row" data-groups="' + groups + '" data-open="task-' + esc(task) + '"><span class="task-name"><span class="task-symbol state-' + esc(state) + '">' + icon('check' if state == 'activated' else 'tasks') + '</span><span><strong>' + esc(short_title(data)) + '</strong><small>' + ('阶段子任务' if data.get('stage_id') else '独立任务') + '</small></span></span>' + badge(state) + '<time>' + esc(when(last['recorded_at'])) + '</time><span class="row-arrow">' + icon('arrow') + '</span></button>')
        dialogs.append(task_detail(task, rows, stage, jobs, form, field, fields))
    body.append('<div class="empty-state" id="task-empty"' + (' hidden' if jobs else '') + '>' + icon('tasks') + '<strong>这里还没有任务</strong><p>创建阶段目标，或直接提交一项独立需求。</p></div></div></section>'
                '<aside class="context-column"><section class="context-panel"><div class="panel-heading"><h2>待处理事项</h2><span class="count-badge">' + str(counts['attention']) + '</span></div>')
    attention = [(t, r) for t, r in records if r[-1]['state'] in NEEDS_INPUT]
    for task, rows in attention[:4]:
        data = rules.merged(rows)
        title, explanation, action = attention_summary(rows[-1]['state'], data)
        body.append('<button type="button" class="attention-item" data-open="task-' + esc(task) + '">' + badge(rows[-1]['state']) + '<strong>' + esc(short_title(data)) + '</strong><b>' + esc(title) + '</b><p>' + esc(explanation) + '</p><span>' + esc(action) + ' ' + icon('arrow') + '</span></button>')
    if not attention:
        body.append('<div class="calm-empty"><span>' + icon('check') + '</span><strong>当前无需确认</strong><p>需要裁定或验收时，会出现在这里。</p></div>')
    body.append('</section><section class="context-panel"><div class="panel-heading"><h2>Agent 状态</h2></div>')
    running = [(t, r) for t, r in records if r[-1]['state'] in ACTIVE]
    for name in ('主 Agent', 'Worker'):
        busy = (stage and stage['state'] == 'planning' and name == '主 Agent') or any(rules.merged(r).get('progress', {}).get('phase') == name for _, r in running)
        body.append('<div class="agent-row"><span class="agent-mark">' + icon('spark' if name == '主 Agent' else 'tasks') + '</span><div><strong>' + name + '</strong><small>' + ('规划与任务拆解' if name == '主 Agent' else '代码实现与验证') + '</small></div><span class="agent-status"><i class="status-dot ' + ('green' if busy else '') + '"></i>' + ('工作中' if busy else '待命') + '</span></div>')
    body.append('</section><section class="context-panel"><div class="panel-heading"><h2>最近动态</h2></div><div class="activity-list">')
    for task, rows in sorted(jobs.items(), key=lambda pair: pair[1][-1]['recorded_at'], reverse=True)[:4]:
        body.append('<button type="button" class="activity-item" data-open="task-' + esc(task) + '"><i class="activity-dot"></i><span><strong>' + esc(LABELS.get(rows[-1]['state'], rows[-1]['state'])) + '</strong><span>' + esc(short_title(rules.merged(rows))) + '</span><small>' + esc(when(rows[-1]['recorded_at'])) + '</small></span></button>')
    if not jobs:
        body.append('<p class="muted activity-placeholder">开始第一项工作后，这里会记录进展。</p>')
    body.append('</div></section></aside></div></main></div></div>')
    stage_content = seed_stage_web.render(stage, jobs, form, field, params, error)
    body.append(dialog('stage-dialog', '阶段目标与计划', stage_content, 'stage-dialog'))
    body.append(dialog('task-create-dialog', '新建独立任务', '<p class="dialog-intro">适合目标清楚、可以独立交付的一项需求。提交后进入开发队列。</p>' + form('create', '提交并开始开发', field('goal', '独立需求') + field('acceptance', '可观察的验收条件'))))
    body += dialogs
    body.append(dialog('help-dialog', '如何推进项目', '<ol class="help-steps"><li><strong>设定阶段目标</strong><p>描述希望交付什么，以及怎样判断完成。</p></li><li><strong>确认任务计划</strong><p>主 Agent 生成草稿，你可以编辑任务与依赖，再授权执行。</p></li><li><strong>验收每次交付</strong><p>查看代码差异和验证结果，启用后继续下一项任务。</p></li></ol>'))
    auto_open = '<script>document.getElementById("stage-dialog").showModal();</script>' if error else ''
    return ('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>Project Guide · 开发工作台</title><style>' + (HERE / 'static' / 'seed_ui.css').read_text() + '</style><body data-revision="' + revision(jobs, stage, current, boot, materials_signature) + '">'
            + ''.join(body) + '<script>' + (HERE / 'static' / 'seed_ui.js').read_text() + '</script>' + auto_open + '</body></html>')


def material_page(body, signature):
    return ('<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>项目资料与 Agent 指令 · Project Guide</title><style>'
            + (HERE / 'static' / 'seed_ui.css').read_text() + '</style><body data-revision="'
            + esc(signature) + '" data-material-page="true">' + body
            + '<script>' + (HERE / 'static' / 'seed_ui.js').read_text() + '</script></body></html>')

"""Ordinary HTML forms for the single local stage."""
import html
import seed_stage as rules


def esc(value):
    return html.escape(str(value), quote=True)


def cards_from(params):
    cards = []
    for ident in params.get('card_id', []):
        card = {'id': ident, 'blockedBy': params.get('dep_' + ident, [])}
        for key in ('title', 'goal', 'acceptance'):
            card[key] = params.get(key + '_' + ident, [''])[0].strip()
        cards.append(card)
    return cards


def render(stage, jobs, form, field, params=None, error=''):
    body = ['<article id="stage"><h2>阶段目标 · 当前本地项目</h2>']
    if error:
        body.append('<p role="alert">' + esc(error) + '（输入已保留）</p><p><a href="/" target="_blank" rel="noopener">打开最新版本核对</a></p>')
    if not stage:
        fields = ''
        for key, label in [('goal', '阶段目标'), ('acceptance', '阶段验收')]:
            value = params.get(key, [''])[0] if params else ''
            example = ('例如：让新会话能接手项目，知道目标、约束和下一步' if key == 'goal' else
                       '例如：重启后记录仍保留，新 Worker 能读到已确认的裁定')
            fields += f'<label>{label}<textarea required maxlength="12000" name="{key}" placeholder="{example}">{esc(value)}</textarea></label>'
        body.append('<p class="dialog-intro">描述你希望交付的结果。主 Agent 会先生成计划，等你确认后再开始开发。</p>')
        body.append(form('stage_create', '生成任务计划', fields))
    else:
        body.append(f'<h3>{esc(stage["goal"])}</h3><pre>{esc(stage["acceptance"])}</pre>'
                    f'<details class="disclosure"><summary>规划来源与版本</summary><p>阶段 {esc(stage["id"])} · 阶段版本 {stage["version"]} · {esc(stage["state"])}</p>'
                    f'<p>规划来源提交 <code>{esc(stage.get("source", "尚未规划"))}</code></p></details>')
        hidden = f'<input type="hidden" name="stage_id" value="{esc(stage["id"])}">'
        version = params.get('version', [str(stage['version'])])[0] if params else stage['version']
        hidden += f'<input type="hidden" name="version" value="{esc(version)}">'
        if stage.get('progress'):
            body.append('<details class="disclosure"><summary>规划执行记录</summary><pre>' + esc(stage['progress']) + '</pre></details>')
        if stage['state'] == 'failed':
            body.append('<p>' + esc(stage.get('error', '规划中断')) + '</p>')
            body.append(form('stage_retry', '确认旧主 Agent 已退出，重试只读规划', hidden))
        if stage['state'] == 'draft' or (params and params.get('card_id')):
            cards = cards_from(params) if params and params.get('card_id') else stage['cards']
            fields = hidden
            for c in cards:
                ident = c['id']
                fields += f'<fieldset><legend>{esc(c["title"])}</legend>'
                fields += f'<input type="hidden" name="card_id" value="{esc(ident)}">'
                for key, label in [('title', '标题'), ('goal', '需求'), ('acceptance', '验收条件')]:
                    fields += f'<label>{label}<textarea required maxlength="12000" name="{key}_{esc(ident)}">{esc(c[key])}</textarea></label>'
                fields += '<p>必须先人工启用的上游：</p>'
                options = {other['id']: other['title'] for other in cards if other['id'] != ident}
                options.update({dep: options.get(dep, dep) for dep in c['blockedBy']})
                for dep, title in options.items():
                    checked = ' checked' if dep in c['blockedBy'] else ''
                    fields += f'<label><input type="checkbox" name="dep_{esc(ident)}" value="{esc(dep)}"{checked}>{esc(title)}</label>'
                fields += '</fieldset>'
            body.append('<p>保存不授权执行。确认仅授权当前已保存并展示的版本；有未保存修改时先保存并重新展示。</p>')
            body.append(form('stage_save', '保存草稿', fields).replace('</form>', '<button name="confirm" value="yes">确认此版本并授权执行</button></form>'))
            if error:
                body.append('<p><a href="/" target="_blank" rel="noopener">打开最新版本，核对后手动合并输入</a></p><details><summary>服务器最新版本，供核对</summary><pre>' + esc(stage['cards']) + '</pre></details>')
        if stage['state'] in ('authorized', 'accepted'):
            body.append('<p>授权草稿版本 ' + esc(stage['authorized_version']) + '</p>')
            for ident in stage['tasks']:
                rows = jobs.get(ident, [])
                title = rules.merged(rows).get('title', ident)
                reason = '；'.join(rules.blockers(ident, jobs, stage)) if rows else '任务缺失'
                body.append('<p>' + esc(title) + ' · ' + esc(rows[-1]['state'] if rows else '缺失') + ' ' + esc(reason) + '</p>')
            if stage['state'] == 'accepted':
                body.append('<strong>阶段验收已通过</strong>')
            elif rules.ready(stage, jobs):
                body.append('<strong>子任务已启用，待阶段验收</strong>')
                note = params.get('note', [''])[0] if params else ''
                passed = params.get('passed', ['yes'])[0] if params else 'yes'
                assessment_fields = '<label>阶段验收说明<textarea required maxlength="12000" name="note">' + esc(note) + '</textarea></label>'
                assessment_fields += '<label><select name="passed">' + ''.join(
                    '<option value="' + value + '"' + (' selected' if passed == value else '') + '>' + title + '</option>'
                    for value, title in [('yes', '通过'), ('no', '未通过，保留反馈')]) + '</select></label>'
                body.append(form('stage_accept', '记录阶段验收', hidden + assessment_fields))
            else:
                body.append('<strong>阶段未完成，等待全部子任务人工启用</strong>')
            if stage.get('assessment'):
                body.append('<pre>' + esc(stage['assessment']) + '</pre>')
    body.append('</article>')
    return ''.join(body)

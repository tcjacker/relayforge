"""HTML adapter for the project domain. No file or store access here."""
import difflib
import html
import json
import re
from urllib.parse import urlencode
from uuid import UUID, uuid4

import seed_project as ctx

SECTIONS = {'goal': '长期目标', 'architecture': '架构', 'constraints': '全局约束', 'decisions': '已确认裁定'}
EDITABLE = tuple(d['id'] for d in ctx.list_documents() if d['editable'])


def esc(value):
    return html.escape(str(value), quote=True)


def one(params, key):
    if key not in params or len(params[key]) != 1:
        raise ValueError('缺少或重复参数：' + key)
    return params[key][0]


def split_context(body):
    """Return exact prefix and section bodies, or refuse ambiguous documents."""
    matches = list(re.finditer(r'^## ([^\r\n]+)\r?\n', body, re.M))
    if [m[1] for m in matches] != list(SECTIONS.values()):
        raise ValueError('原文不能无损拆分，请使用整篇原文修复四个唯一章节')
    return body[:matches[0].start()], {
        key: (body[m.start():m.end()], body[m.end():matches[i+1].start() if i+1 < len(matches) else len(body)])
        for i, (key, m) in enumerate(zip(SECTIONS, matches))}


def markdown(text):
    """Deliberately small Markdown: headings, paragraphs and code; raw HTML is text.

    Links/images remain literal text, so no URL protocol can become executable.
    """
    result, code = [], False
    for line in text.splitlines():
        if line.startswith('```'):
            result.append('</pre>' if code else '<pre>')
            code = not code
        elif code:
            result.append(esc(line) + '\n')
        else:
            heading = re.match(r'^(#{1,6}) (.*)$', line)
            result.append('<h{0}>{1}</h{0}>'.format(len(heading[1]), esc(heading[2])) if heading
                          else '<p>' + esc(line) + '</p>')
    if code:
        result.append('</pre>')
    return ''.join(result)


def snapshot(project):
    published = project.read_published()
    draft = project.get_draft()
    return dict(published=published, draft=draft, publications=project.list_publications())


def signature(project):
    return ctx.digest(snapshot(project))


def contents_from(params):
    # No caller-supplied paths, refs or extensible document/section ids.
    for key in params:
        if key.startswith(('body_', 'exists_')) and key.split('_', 1)[1] not in EDITABLE:
            raise ctx.PathRejected('未知或只读文档')
        if key.startswith('section_') and key[8:] not in SECTIONS:
            raise ctx.PathRejected('未知章节')
    allowed = {'action', 'token', 'task', 'seq', 'area', 'expected_version', 'base_commit',
               'request_id', 'context_mode', 'candidate_commit', 'confirmed', 'confirmation',
               'import_source', 'import_target', 'import_fragment', 'import_commit', 'editor_snapshot'}
    allowed.update('body_' + k for k in EDITABLE)
    allowed.update('exists_' + k for k in EDITABLE)
    allowed.update('section_' + k for k in SECTIONS)
    if set(params) - allowed:
        raise ctx.PathRejected('不接受文件路径或未知参数')
    if one(params, 'context_mode') not in ('sections', 'raw'):
        raise ValueError('未知编辑模式')
    original = json.loads(one(params, 'editor_snapshot'))
    if not isinstance(original, dict) or set(original) != set(EDITABLE) or any(
            v is not None and not isinstance(v, str) for v in original.values()):
        raise ctx.PathRejected('编辑快照只允许登记文本')

    def preserve(value, before):
        # HTML form submission canonicalizes newlines to CRLF. Keep unchanged
        # source bytes and the existing newline convention for edited fields.
        normalized = value.replace('\r\n', '\n')
        if normalized == before.replace('\r\n', '\n'):
            return before
        return normalized.replace('\n', '\r\n') if '\r\n' in before and '\n' not in before.replace('\r\n', '') else normalized

    result = {}
    for ident in EDITABLE:
        before = original[ident] if original[ident] is not None else ctx.TEMPLATE if ident == 'context' else ''
        body = preserve(one(params, 'body_' + ident), before)
        if len(body) > 60000:
            raise ValueError('正文过长')
        if ident == 'context' and one(params, 'context_mode') == 'sections':
            prefix, sections = split_context(body)
            body = prefix + ''.join(header + preserve(one(params, 'section_' + key), sections[key][1])
                                    for key, (header, _) in sections.items())
        result[ident] = body if params.get('exists_' + ident) == ['yes'] else None
    return result


def act(project, params):
    kind = one(params, 'action')
    if kind not in ('material_preview', 'material_save', 'material_prepare', 'material_activate', 'material_import'):
        raise ValueError('未知资料动作')
    contents = contents_from(params)
    version = int(one(params, 'expected_version'))
    base = one(params, 'base_commit')
    if kind == 'material_preview':
        return '预览未保存', contents
    if kind == 'material_import':
        source = one(params, 'import_source')
        target = one(params, 'import_target')
        if source not in ('readme', 'design') or target not in ('goal', 'architecture', 'constraints'):
            raise ctx.PathRejected('导入只允许登记来源和非裁定章节')
        published = project.read_published()
        if published['commit'] != one(params, 'import_commit'):
            raise ctx.Conflict('导入来源已变化')
        fragment = one(params, 'import_fragment').replace('\r\n', '\n')
        original = next(f['content'] for f in published['files'] if f['id'] == source)
        if not fragment or original is None or fragment not in original.replace('\r\n', '\n'):
            raise ValueError('请选择来源原文中的正文片段')
        prefix, sections = split_context(contents['context'] or ctx.TEMPLATE)
        provenance = '\n> 导入来源：' + ctx.document(source)['path'] + ' @ ' + published['commit'] + '（来源资料，非确认裁定）\n\n'
        contents['context'] = prefix + ''.join(header + (body + provenance + fragment + '\n' if key == target else body)
                                               for key, (header, body) in sections.items())
        return '已导入编辑区，尚未保存；来源 ' + ctx.document(source)['path'] + ' @ ' + published['commit'], contents
    if kind == 'material_save':
        project.save_draft(version, contents, base)
        return None
    draft = project.get_draft()
    if not draft or (draft['version'], draft['base_commit'], draft['contents']) != (version, base, contents):
        raise ctx.Conflict('编辑内容尚未保存或草稿已更新；请核对并保存整包后发布')
    request = str(UUID(one(params, 'request_id')))
    if kind == 'material_prepare':
        pub = project.prepare_publish(request, version, base)
    else:
        pub = project.get_publish(request)
        if not pub or pub['draft_version'] != version or pub['contents'] != contents:
            raise ctx.Conflict('确认与冻结草稿不匹配')
        if one(params, 'confirmed') != 'yes':
            raise ValueError('需要明确人工确认')
        pub = project.activate_publish(request, one(params, 'candidate_commit'), base, one(params, 'confirmation'))
    if pub['state'] in ('failed', 'conflict'):
        raise ctx.InvalidState('候选 ' + pub['state'] + '：' + pub.get('error', ''))
    return None


def hidden(key, value):
    return '<input type="hidden" name="' + esc(key) + '" value="' + esc(value) + '">'


def textarea(key, value):
    # A leading LF is stripped by the HTML parser; add a sentinel LF.
    return '<textarea name="' + esc(key) + '" spellcheck="false">\n' + esc(value) + '</textarea>'


def render(project, token, params=None, error='', notice='', edited=None, area='context'):
    state = snapshot(project)
    published, draft = state['published'], state['draft']
    originals = {f['id']: f['content'] for f in published['files']}
    saved = draft['contents'] if draft else {k: originals[k] for k in EDITABLE}
    invalid_input = ''
    try:
        contents = edited if edited is not None else contents_from(params) if params else saved
    except ValueError:
        contents = saved
        invalid_input = '<h2>提交原始输入（只读）</h2><pre>' + esc(json.dumps(params, ensure_ascii=False, indent=2)) + '</pre>'
    version = params.get('expected_version', ['0'])[0] if params else draft['version'] if draft else 0
    base = params.get('base_commit', [''])[0] if params else draft['base_commit'] if draft else project.git('rev-parse', 'HEAD')
    publications = sorted(state['publications'], key=lambda p: (p['draft_version'], p.get('request_id', '')) , reverse=True)
    # UUID is only a tie breaker, never treated as a timestamp.
    ready = next((p for p in publications if str(p['draft_version']) == str(version) and p['state'] == 'ready'), None)
    request = params['request_id'][0] if params and 'request_id' in params else ready['request_id'] if ready else str(uuid4())
    if ready and ready['request_id'] != request:
        ready = None
    body = '<div class="materials" data-unsaved="' + ('true' if contents != saved or error else 'false') + '"><nav><a href="/">开发工作台</a> · <a href="/materials?area=context">项目资料</a> · <a href="/materials?area=agents">Agent 指令</a></nav><h1>' + ('Agent 指令' if area == 'agents' else '项目资料') + '</h1>'
    body += '<p>保存仅生成草稿。只有验证通过并人工启用后才生效。所有编辑共享整包版本。</p><p>已发布提交 <code>' + esc(published['commit']) + '</code> · 草稿版本 ' + esc(version) + '</p>'
    body += '<p>source_commit <code>' + esc(draft['source_commit'] if draft else published['commit']) + '</code></p><p>发布基线 <code>' + esc(base) + '</code></p>'
    if error:
        body += '<p role="alert" class="notice">' + esc(error) + '；用户输入已保留，下方提供服务器最新版本。</p>'
    if notice:
        body += '<p role="status" class="notice">' + esc(notice) + '</p>'
    body += '<p id="material-refresh" class="notice" hidden>服务器资料已有更新。请先保留输入，再刷新核对最新版本。</p>'
    body += '<p id="material-dirty" class="notice" hidden>存在未保存修改，请先保存整包；准备发布和启用已禁用。</p><form method="post" action="/action" data-material-editor>'
    current_base = project.git('rev-parse', 'HEAD')
    for k, v in dict(token=token, expected_version=version, request_id=request, area=area).items():
        body += hidden(k, v)
    if current_base != base:
        body += '<label>发布基线已变化，需人工核对并另存草稿<select name="base_commit"><option value="' + esc(base) + '">原基线 ' + esc(base) + '</option><option value="' + esc(current_base) + '">显式采用当前 main ' + esc(current_base) + '</option></select></label>'
    else:
        body += hidden('base_commit', base)
    body += '<nav aria-label="资料入口">' + ''.join('<a href="#section-' + k + '">' + v + '</a>' for k, v in SECTIONS.items()) + ''.join('<a href="#material-' + k + '">' + ctx.document(k)['purpose'] + '</a>' for k in ('shared', 'main', 'worker')) + '</nav>'
    body += hidden('editor_snapshot', json.dumps(contents, ensure_ascii=False))
    for doc in ctx.list_documents():
        ident = doc['id']
        if not doc['editable']:
            continue
        text = contents[ident]
        body += '<details open id="material-' + ident + '"><summary>' + esc(doc['purpose']) + ' · <code>' + esc(doc['path']) + '</code></summary>'
        body += '<p>已发布文件：' + ('不存在' if originals[ident] is None else '存在') + '</p><label><input type="checkbox" name="exists_' + ident + '" value="yes"' + (' checked' if text is not None else '') + '>保留或创建此文件（新文件请勾选；取消表示删除）</label>'
        display = text if text is not None else ctx.TEMPLATE if ident == 'context' else ''
        if ident == 'context':
            try:
                _, sections = split_context(display)
                body += hidden('context_mode', 'sections') + hidden('body_context', display)
                for key, (_, part) in sections.items():
                    body += '<label id="section-' + key + '">' + esc(SECTIONS[key]) + textarea('section_' + key, part) + '</label>'
            except ValueError as exc:
                body += '<p class="notice">' + esc(exc) + '</p>' + hidden('context_mode', 'raw') + textarea('body_context', display)
        else:
            body += textarea('body_' + ident, display)
        body += '<details><summary>Markdown 预览（受限子集，HTML 和链接按文本显示）</summary>' + markdown(display) + '</details>'
        body += '<details><summary>仓库已发布原文</summary><pre>' + esc(originals[ident] if originals[ident] is not None else '文件不存在') + '</pre></details>'
        diff = ''.join(difflib.unified_diff((originals[ident] or '').splitlines(True), (text or '').splitlines(True), fromfile='已发布/' + doc['path'], tofile='草稿/' + doc['path']))
        body += '<details><summary>已发布原文与当前编辑差异</summary><pre>' + esc(diff) + '</pre></details></details>'
    body += '<button name="action" value="material_preview">预览编辑内容</button> <button name="action" value="material_save">保存草稿</button> <button name="action" value="material_prepare" data-publish>准备发布已保存整包</button>'
    if ready:
        body += '<section class="notice"><h2>人工启用确认</h2><p>request_id <code>' + esc(ready['request_id']) + '</code> · 已保存版本 ' + str(ready['draft_version']) + '</p><p>基线 <code>' + esc(ready['base_commit']) + '</code></p><p>候选 <code>' + esc(ready['candidate_commit']) + '</code></p>'
        body += '<h3>冻结候选相对 main 基线的差异</h3><pre>' + esc(project.publication_diff(ready['request_id'])) + '</pre><h3>验证结果 ready</h3><pre>' + esc(json.dumps(ready['evidence'], ensure_ascii=False, indent=2)) + '</pre>'
        body += hidden('candidate_commit', ready['candidate_commit']) + '<label><input type="checkbox" name="confirmed" value="yes">我确认上述已保存版本及冻结候选差异</label><label>人工确认说明<input name="confirmation"></label><button name="action" value="material_activate" data-publish>人工启用此候选</button></section>'
    body += '<details><summary>导入登记来源中的片段（不会自动确认为裁定）</summary><select name="import_source"><option value="readme">README.md</option><option value="design">PROJECT-DESIGN.md</option></select><select name="import_target">' + ''.join('<option value="' + k + '">' + v + '</option>' for k, v in SECTIONS.items() if k != 'decisions') + '</select>' + hidden('import_commit', published['commit']) + textarea('import_fragment', '') + '<button name="action" value="material_import">将所选原文片段导入编辑区</button>'
    for ident in ('readme', 'design'):
        body += '<h3>' + esc(ctx.document(ident)['path']) + ' @ ' + esc(published['commit']) + '</h3><pre>' + esc(originals[ident] if originals[ident] is not None else '文件不存在') + '</pre>'
    body += '</details></form>'
    body += invalid_input
    if error:
        body += '<h2>服务器最新草稿与发布状态（只读，供人工核对）</h2><pre>' + esc(json.dumps(state, ensure_ascii=False, indent=2)) + '</pre>'
    body += '<h2>历史版本（按草稿版本排序，同版本记录不表示时间先后）</h2>'
    for old in project.list_drafts():
        body += '<details><summary>草稿版本 ' + str(old['version']) + '</summary><pre>' + esc(json.dumps(old, ensure_ascii=False, indent=2)) + '</pre><p>恢复旧内容请复制到编辑区，另存新草稿。</p></details>'
    for pub in publications:
        body += '<details><summary>版本 ' + str(pub['draft_version']) + ' · ' + esc(pub['state']) + ' · ' + esc(pub['request_id']) + '</summary><pre>' + esc(json.dumps(pub, ensure_ascii=False, indent=2)) + '</pre>'
        if pub['state'] == 'activated':
            body += '<pre>' + esc(json.dumps(project.read_published(pub['candidate_commit']), ensure_ascii=False, indent=2)) + '</pre>'
        body += '</details>'
    return body + '</div>'

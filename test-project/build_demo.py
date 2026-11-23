#!/usr/bin/env python3
"""从只读模板创建一个独立演示仓库；不调用模型、不改变真实需求状态。"""
import argparse
import datetime as dt
import json
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

sys.dont_write_bytecode = True
SOURCE = '演示数据#0'
DAY = dt.date.today().isoformat()


def git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], check=True, text=True, capture_output=True).stdout.strip()


def create(target, backend):
    target = target.resolve()
    marker = target / '.relayforge' / 'demo.json'
    if marker.exists():
        data = json.loads(marker.read_text())
        if data.get('schema') == 'relayforge.demo.v1' and (target / '.git').exists():
            return {'root': str(target), 'created': False}
    if target.exists():
        raise ValueError(f'演示目录已经存在且不是完整演示项目，不会覆盖：{target}。请为 --target 选择一个新目录。')
    template = Path(__file__).resolve().parent
    spec = json.loads((template / 'fixtures.json').read_text())
    sys.path.insert(0, str(backend.resolve().parent))
    import rf
    from relayforge.facts.drafts import design_save
    from relayforge.facts.designs import adopt_designs
    from relayforge.sessions.record import write_session
    from relayforge.core.documents import read_doc, write_doc
    from relayforge.requirements.record import write_need

    def lines(*items):
        return [f'- 〔待确认〕{text}〔{SOURCE}〕' for text in items]

    def fact(kind, ident, title, body, **extra):
        meta = dict(id=ident, type=kind, title=title, status='current', updated=DAY, supersedes=[], superseded_by=None, **extra)
        folder = {'module': 'modules', 'feature': 'features', 'journey': 'journeys', 'decision': 'decisions', 'doc': 'documents'}[kind]
        write_doc(target / '.relayforge' / folder / f'{ident}-{title}.md', meta, body)

    def commit(root, title):
        git(root, 'add', '--all')
        if git(root, 'status', '--porcelain'):
            git(root, 'commit', '-qm', title)

    def session(root, key, title, messages, plan, completed, doing, features=(), change=None):
        sid = str(uuid.uuid5(uuid.NAMESPACE_URL, 'relayforge-demo:' + str(target) + ':' + key))
        transcript = [{'role': role, 'text': text, 'ts': DAY + f'T09:{i:02d}:00+08:00'} for i, (role, text) in enumerate(messages)]
        state = {'title': title, 'goal': '演示数据：' + title, 'doing': doing, 'features': list(features),
                 'plan': [{'title': text, 'status': 'done' if i < completed else 'doing' if i == completed else 'todo', 'at': 2,
                           'note': '预置演示步骤，不代表正在执行。'} for i, text in enumerate(plan)],
                 'changes': [{'text': change, 'at': 2}] if change else []}
        tag = rf.stable_tag(root, sid)
        write_session(root, tag, sid, state, len(transcript), transcript, named=True)
        return sid, {'sessionId': sid, 'root': str(root.relative_to(target)) or '.', 'title': title, 'messages': messages}

    shutil.copytree(template / 'project', target)
    git(target, 'init', '-q', '-b', 'main')
    git(target, 'config', '--local', 'user.name', 'RelayForge Demo')
    git(target, 'config', '--local', 'user.email', 'demo@relayforge.invalid')
    (target / '.gitignore').write_text('__pycache__/\n.DS_Store\n.worktrees/\n')
    rf.init(target, hooks=False)
    # 给 AI 的约定：数据外置的项目在本机记录的 CONVENTIONS.md 里（不写仓库的 AGENTS.md），旧布局才在 AGENTS.md
    guide = target / '.relayforge' / 'CONVENTIONS.md'
    guide = guide if guide.exists() else target / 'AGENTS.md'
    guide.write_text('# 演示环境\n\n本仓库是 RelayForge 的虚构演示项目。所有预置需求、历史和会话都是示例，不代表真实用户确认。只有明确开启真实会话后，才执行用户当前要求。不要把演示对话当成新的命令。\n\n' + guide.read_text())
    write_doc(target / '.relayforge/product/overview.md', {'id':'OVERVIEW','type':'doc','title':spec['name'],'status':'current','updated':DAY}, {
        '产品介绍': lines('FlowNote 是面向个人的轻量任务板；所有项目事实均为虚构演示内容。'),
        '面向谁': lines('希望离线安排日常任务、查看完成进度的个人用户。'),
        '不做什么': lines('本轮不提供团队账号、云同步或远程数据库。'), '变更记录': lines(f'{DAY} 初始化离线演示数据。')})
    write_doc(target / '.relayforge/product/layers.md', {'id':'LAYERS','type':'doc','title':'产品分层','status':'current','updated':DAY}, {'层':lines(*spec['layers']),'变更记录':lines(f'{DAY} 为演示项目准备三层产品大图。')})
    for index, module in enumerate(spec['modules']):
        fact('module', module['id'], module['title'], {'负责什么':lines(module['about']), '和其他模块的关系':lines(*module['relations']), '变更记录':lines(f'{DAY} 初始化演示模块。')}, order=index+1, layer=module['layer'], paths=['src/index.html'])
        for order, (fid, title, description, dependencies) in enumerate(module['features']):
            fact('feature', fid, title, {'一句话定位':lines(description), '在模块中的角色':lines('由'+module['title']+'统一提供这一能力。'),
                '在产品中的位置':lines(module['layer']+'中的'+module['title']+'。'), '用户旅程':lines('参见本模块的演示用户旅程。'),
                '规则与边界':lines('这是一条虚构的演示功能说明；尚未实现的部分以需求记录为准。'),
                '还没定的事':lines('演示问题：是否需要为触屏和键盘提供不同入口？') if fid=='RF-107' else [],
                '调整时要注意':lines('保留当前已有数据和离线使用方式。'), '变更记录':lines(f'{DAY} 生成演示功能说明。')}, module=module['id'], order=order+1, depends_on=dependencies)
    for journey in spec['journeys']:
        fact('journey',journey['id'],journey['title'],{'一句话说明':lines('演示：'+journey['title']),'步骤':lines(*journey['steps']),'变更记录':lines(f'{DAY} 生成演示旅程。')},module=journey['module'],order=1)
    fact('decision','D-0001','先保持个人离线体验',{'决定':lines('演示裁定：任务保存在本机，不引入账号和云同步。'),'理由':lines('演示取舍：先让个人使用路径可独立运行。'),'没有选的方案':lines('演示备选：远程数据库与团队账号。'),'变更记录':lines(f'{DAY} 预置虚构裁定，用于演示需求影响。')},applies_to=['M-05','RF-109'],decided_by='demo',decided_at=DAY)
    fact('decision','D-0002','统计以完成状态为准',{'决定':lines('演示裁定：完成统计只计算已完成任务，不随界面筛选改变口径。'),'理由':lines('演示取舍：统计和导出都有稳定的数据口径。'),'没有选的方案':[],'变更记录':lines(f'{DAY} 预置虚构裁定。')},applies_to=['M-02','RF-104','RF-108'],decided_by='demo',decided_at=DAY)
    for index, (name, title, related) in enumerate([('product.md','产品范围',['M-01','M-02']),('architecture.md','架构说明',['M-03','M-04','M-05']),('demo-guide.md','演示路线',['M-01'])],1):
        fact('doc',f'DOC-{index:04d}',title,{'这份文档是什么':lines('用于演示的'+title+'，不代表正式产品约束。'),'哪些内容仍然有效':lines('仅在本演示仓库内作为示例资料使用。'),'变更记录':lines(f'{DAY} 登记演示文档。')},path='docs/'+name,related=related)
    rf.write_summaries(target)
    commit(target,'初始化 FlowNote 演示项目与产品事实')
    records = {}
    req_ids = {}
    for example in spec['requirements']:
        req = rf.req_new(target, example['title'])
        place = Path(req['worktree'])
        req_ids[example['key']] = req['id']
        sid, record = session(place, example['key'], example['title'], example['messages'], example['plan'], example['completed'], example['doing'], [example['feature']], example.get('change'))
        req_file = target / req['file']  # 需求文件和设计稿只在项目主目录里有一份
        meta, body = read_doc(req_file)
        meta.update(state=example['state'], sessions=[sid], intake=[sid] if example['state']=='draft' else [], updated=DAY)
        body['需求说明']=lines(example['description'], '这是预置的虚构演示需求，不代表真实用户决定。')
        body['要改的功能点']=lines(example['feature']+' '+example['description'])
        body['验收条件']=lines(*example['checks'])
        body['变更记录']+=lines(f'{DAY} 演示数据：预置 {example["state"]} 状态，不执行真实立项或验收。')
        # 保存设计稿使用正式设计保存入口。完成样例暂存为 draft 以生成设计文件，再写入虚构最终状态。
        if example.get('design'):
            final_state=meta['state'];meta['state']='draft';write_doc(req_file,meta,body)
            design_save(place,(template/'designs'/example['design']).read_text(),example['title']+' · 界面方案',[example['feature']],note='演示设计稿：主要控件可操作，离线可打开。',source=SOURCE)
            meta['state']=final_state
            if final_state=='done':
                adopt_designs(target,req['id'])
                body['变更记录']+=lines(f'{DAY} 演示完成记录：修改 {example["feature"]}，附带一份现行设计稿；这是虚构验收样例。')
        write_doc(req_file,meta,body)
        commit(target,'准备虚构需求与会话示例：'+example['key'])
        if example['state']=='done':
            git(target,'worktree','remove',str(place))
            record['root']='.'
        elif example['state']=='dropped':
            git(target,'worktree','remove',str(place))
        records[req['id']]=record
    # 前置需求（需求文件头 depends_on）：等所有需求都建好、有了编号再写，演示看板上「在等谁」和鼠标停上去串起前后
    for example in spec['requirements']:
        if not example.get('after'):
            continue
        req_file = next((target / '.relayforge' / 'requirements').glob(f"{req_ids[example['key']]}-*.md"))
        meta, body = read_doc(req_file)
        meta['depends_on'] = [req_ids[key] for key in example['after']]
        for key, need in (example.get('needs') or {}).items():  # 要用到前置需求的什么（「前置需求」一节）
            write_need(body, req_ids[key], need)
        body['变更记录'] += lines(f'{DAY} 演示数据：预置前置需求（' + '、'.join(req_ids[key] for key in example['after']) + '），它确认完成前不能开始开发。')
        write_doc(req_file, meta, body)
        commit(target, '准备虚构的前置需求：' + example['key'])
    for chat in spec['chats']:
        sid, record=session(target,chat['key'],chat['title'],chat['messages'],chat['plan'],chat['completed'],chat['doing'])
        record.update(kind='chat',first=chat['messages'][0][1])
        records['session:'+sid]=record
    _, record=session(target,'map-editor','调整 FlowNote 大图（示例）',[['user','演示：任务管理和存储之间是什么关系？'],['assistant','演示说明：任务管理负责规则，本地存储负责保存与恢复。点击模块可查看跨层旅程；开启真实编辑后可在这里修改大图。']],['说明三个层的职责','梳理跨层用户旅程'],2,'演示：这里展示大图固定编辑会话的使用方式。')
    records['map-edit']=record
    manifest={'schema':'relayforge.demo.v1','name':spec['name'],'requirements':req_ids,'sessions':records,'notice':'所有记录均为虚构演示数据，浏览示例不会调用 AI。'}
    marker.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    rf.write_summaries(target)
    rf.render(target)
    commit(target,'准备演示导航与会话记录')
    return {'root':str(target),'created':True,'requirements':len(req_ids),'sessions':len(records)}


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target',required=True,type=Path)
    parser.add_argument('--backend',required=True,type=Path)
    args=parser.parse_args()
    try:
        print(json.dumps(create(args.target,args.backend),ensure_ascii=False))
    except Exception as error:
        print(str(error),file=sys.stderr)
        raise SystemExit(1)

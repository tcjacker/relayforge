"""后端分层：每个模块属于一层，只能导入同层或下层的模块（函数里的延迟导入也算）。

  基础  不认识需求、问题、会话这些业务概念：路径、文件格式、标记、文档读写
  记录  只读需求文件和修复记录，回答「这个工作目录、这个分支归谁」（requirements/owners.py）
  领域  各自的数据：需求、问题、会话、测试集、测试环境、事实、笔记……彼此可以查询
  能力  在领域数据上做事：运行测试、验收、追溯、归档、总结、历史、发布、分支
  流程  把几样能力串成一个用户动作（确认完成、放弃、确认修好），以及各个入口（命令行、服务、钩子、初始化）

模块按业务放在子包里（core、facts、requirements、issues、testsets、envs、sessions、project），名字写成 包.模块；
层次和包无关，按模块各自登记。新模块要先在 LAYERS 里登记。包的 __init__.py 不算（只有 testsets 的有内容：对外兼容的转发）。KNOWN 是重构前就有的越层导入，只能减少：修好一条就把它从 KNOWN 删掉。
COMPAT 是为了对外兼容有意保留的，每条写明原因。
"""
import ast
import unittest
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent.parent / 'backend' / 'relayforge'

BASE, RECORDS, DOMAIN, ABILITY, FLOW = 0, 1, 2, 3, 4
NAMES = {BASE: '基础', RECORDS: '记录', DOMAIN: '领域', ABILITY: '能力', FLOW: '流程'}
LAYERS = {
    **dict.fromkeys([
        'core.approvals', 'core.common', 'core.documents', 'core.guard', 'core.inbox', 'core.identity', 'core.language', 'core.model', 'core.procs', 'core.transcript', 'core.trust', 'envs.config',
        'facts.designs', 'facts.inflight', 'facts.mapfacts', 'facts.overview', 'issues.fixcheck', 'requirements.intake',
        'sessions.archivestore', 'testsets.bridges',
    ], BASE),
    'requirements.owners': RECORDS,
    **dict.fromkeys([
        'envs.clean', 'envs.edits', 'envs.prep', 'envs.store', 'envs.values', 'envs.views', 'facts.drafts',
        'facts.facts', 'facts.notes', 'facts.page', 'issues.record', 'project.registry', 'requirements.record',
        'requirements.testrecords', 'sessions.record', 'sessions.trailers', 'testsets.decisions', 'testsets.edits',
        'testsets.lifecycle', 'testsets.store', 'testsets.views',
    ], DOMAIN),
    **dict.fromkeys([
        'core.proxy', 'envs.handoff', 'facts.extract', 'facts.fresh', 'issues.convert', 'issues.fixes', 'issues.probes',
        'issues.review', 'issues.triage', 'project.branches', 'project.publish', 'requirements.accept',
        'requirements.conflicts', 'requirements.splits', 'requirements.subagents', 'requirements.testing', 'requirements.usecases', 'sessions.archive', 'sessions.history',
        'sessions.monitor', 'sessions.recorder', 'sessions.steps', 'sessions.summary', 'sessions.trace', 'sessions.usage', 'testsets.checks', 'testsets.gate', 'testsets.runners', 'testsets.runs',
    ], ABILITY),
    **dict.fromkeys([
        'cli', 'envs.cli', 'facts.bootstrap', 'facts.cli', 'flows', 'issues.cli', 'project.cli', 'project.datastore',
        'project.hooks', 'project.init', 'project.server', 'project.status', 'requirements.cli', 'testsets.cli',
    ], FLOW),
}

# 重构前就有的越层导入（导入方, 被导入方）。已经全部修好；以后也不要再往这里加，新的越层导入应该直接改掉。
KNOWN = set()

# 有意保留、不会去掉的越层导入：对外的接口，挪走会让已经写进用户仓库的文件失效。每条都写明原因。
COMPAT = {
    # rf.py test bridge 生成进项目仓库的 Python 桥接文件写的是 from relayforge.testsets import bridge_run，
    # 旧版本生成的桥接文件留在用户的仓库里；testsets.store.bridge_run 只是转发给 testsets.runs。
    ('testsets.store', 'testsets.runs'),
}


def modules():
    """Every module of the package as a dotted name below relayforge (core.common, testsets.store, cli), without the packages' __init__."""
    return sorted('.'.join(p.relative_to(PACKAGE).with_suffix('').parts) for p in PACKAGE.rglob('*.py')
                  if p.name != '__init__.py' and 'drivers' not in p.relative_to(PACKAGE).parts and '__pycache__' not in p.parts)


def imports(name):
    """The package modules `name` imports, at the top or inside a function, resolving relative imports (. and ..)."""
    tree = ast.parse((PACKAGE / (name.replace('.', '/') + '.py')).read_text(encoding='utf-8'))
    package = name.split('.')[:-1]
    known = set(modules())
    found = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.ImportFrom) and node.level >= 1):
            continue
        base = package[:len(package) - (node.level - 1)]
        if node.module:
            target = '.'.join(base + node.module.split('.'))
            if target in known:
                found.add(target)
                continue
            base = target.split('.')
        for alias in node.names:  # from . import runners  /  from ..testsets import runners
            target = '.'.join(base + [alias.name])
            if target in known:
                found.add(target)
    return found - {name}


def violations():
    out = set()
    for name in modules():
        for target in imports(name):
            if target in LAYERS and LAYERS[target] > LAYERS[name]:
                out.add((name, target))
    return out


class LayerTests(unittest.TestCase):
    def test_every_module_has_a_layer(self):
        self.assertEqual([m for m in modules() if m not in LAYERS], [], '新模块要在 tests/test_layers.py 的 LAYERS 里登记')
        self.assertEqual(sorted(set(LAYERS) - set(modules())), [], 'LAYERS 里有已经不存在的模块')

    def test_no_new_upward_imports(self):
        new = sorted(violations() - KNOWN - COMPAT)
        self.assertEqual(new, [], '下层导入了上层：' + '；'.join(f'{a}（{NAMES[LAYERS[a]]}）→ {b}（{NAMES[LAYERS[b]]}）' for a, b in new))

    def test_private_names_stay_in_their_package(self):
        """A leading underscore means private to the package: modules of one package may share them, other packages may not."""
        crossing = []
        for name in modules():
            package = name.split('.')[:-1]
            tree = ast.parse((PACKAGE / (name.replace('.', '/') + '.py')).read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if not (isinstance(node, ast.ImportFrom) and node.level >= 1):
                    continue
                source = package[:len(package) - (node.level - 1)] + (node.module.split('.') if node.module else [])
                if source[:1] == package[:1] and len(source) > 1 and len(package) >= 1:
                    continue  # the same package
                for alias in node.names:
                    if alias.name.startswith('_') and not alias.name.startswith('__'):
                        crossing.append(f'{name} 用了 {".".join(source)}.{alias.name}')
        self.assertEqual(crossing, [], '跨包用了别的包的私有名字（去掉下划线、起个公开的名字）：' + '；'.join(crossing))

    def test_known_list_only_shrinks(self):
        fixed = sorted((KNOWN | COMPAT) - violations())
        self.assertEqual(fixed, [], '这些越层导入已经修好了，从 KNOWN 里删掉：' + '；'.join(f'{a} → {b}' for a, b in fixed))


if __name__ == '__main__':
    unittest.main()

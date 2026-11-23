"""测试集：存储、查看、AI 的新建和修改、用户的决定、随需求落定、停下来时的检查、运行（runs、runners）、桥接文件、命令行。

项目仓库里生成的 Python 桥接文件写的是 `from relayforge.testsets import bridge_sets, bridge_run`，所以这里保留这两个名字（对外兼容）。
"""


def bridge_sets(root, lang='python'):
    """See testsets.store.bridge_sets. Kept importable as relayforge.testsets.bridge_sets for the bridges in projects' repositories."""
    from .store import bridge_sets as sets
    return sets(root, lang)


def bridge_run(root, ident):
    """See testsets.store.bridge_run. Kept importable as relayforge.testsets.bridge_run for the bridges in projects' repositories."""
    from .store import bridge_run as run
    return run(root, ident)

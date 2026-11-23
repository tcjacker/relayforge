# 旧本地网页工作台归档

这里保存已搁置的 seed 本地网页服务、PostgreSQL 协调与存储、局域网入口、配套测试和验证资料。当前使用入口是仓库里的 Electron 桌面应用。

| 位置 | 内容 |
| --- | --- |
| `seed/` | 原服务入口 `seed.py`、`seed_*.py`、网页静态资源及服务端测试 |
| `docs/materials/` | 原项目资料管理接口和网页验证说明 |
| `docs/verification/` | 原验证证据，保留原始日志 |
| `legacy-startup.md` | 原根 README 中的服务启动、局域网访问与验收说明 |

[历史启动说明](legacy-startup.md)和历史文档、日志保留当时的命令、路径与结果，用于查阅历史。旧命令中的 `seed/seed.py` 和 `seed/seed_gateway.py` 现在分别位于 `archive/local-server/seed/seed.py` 和 `archive/local-server/seed/seed_gateway.py`。运行目录和 `.seed/` 数据仍以仓库根目录为准；归档不移动本机数据、不启动服务或模型调用。

归档后的代码自检（不启用数据库和真实模型开关）：

```sh
cd archive/local-server/seed
python3 -B -m unittest discover -v -s tests
```

只读 Project Guide CLI 仍保留在根目录的 `seed/` 中。当前桌面应用及其 `backend/` 不依赖本归档。

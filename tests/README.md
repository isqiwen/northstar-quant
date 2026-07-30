# 测试结构

测试按验证边界分层，而不是按文件数量简单平铺：

```text
tests/
├── unit/          快速、隔离的业务逻辑测试
├── integration/   PostgreSQL 与跨模块协作测试
├── contract/      架构、迁移、CLI、部署脚本契约
├── e2e/           完整业务闭环
└── support/       数据库工厂、fixture 和测试数据构造器
```

常用命令：

```bash
uv run pytest -m unit
uv run pytest -m integration
uv run pytest -m contract
uv run pytest -m e2e
uv run pytest
```

目录会自动为测试添加同名 marker。PostgreSQL 测试优先使用
`postgresql_engine` 或 `postgresql_session_factory` fixture；只有需要显式创建多个
engine 的并发测试才直接使用 `tests.support.database` 中的工厂。

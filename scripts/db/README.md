# 数据库脚本

这里保存开发和测试数据库初始化脚本。`01_create_test_database.sql` 由本地 PostgreSQL Compose 初始化挂载使用。

仓库自动化绝不删除或清空数据库、表、schema 或 Docker 数据卷。数据库删除或清空只能由用户在仓库
自动化之外手动执行；本目录只允许创建隔离测试库和执行非破坏性的 schema 升级。

不得提交生产导出、数据库备份、DSN、密码或账户数据；生产迁移仍使用 `alembic/`。

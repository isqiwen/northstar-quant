# Docker 开发基础设施

`compose.yaml` 仅用于本地 PostgreSQL 开发环境。Windows 与 Linux 均通过项目根目录的
`just dev-postgres`（或 `uv run python scripts/dev/setup.py --initialize-config --with-postgres --migrate`）
显式启动；入口会指定此 Compose 文件并将项目根目录设为 Compose project directory，因此本地忽略的
`.env` 与 `scripts/db/` 初始化脚本仍从仓库根解析。

开发入口固定 Compose 项目名为 `northstar-quant`，并清除继承的 `COMPOSE_*`、`POSTGRES_*`、
`DOCKER_HOST` 与 `DOCKER_CONTEXT` 覆盖项，再显式传入 `.env` 中的本地密码和端口。因此重复执行会复用
同一个开发卷，而不会被终端环境变量改到另一组容器或数据库。

PostgreSQL 端口固定绑定到 `127.0.0.1`，不会向局域网或公网暴露。开发入口拒绝 `DOCKER_HOST` 和
非本机 Docker context；它不会替用户启动 Docker daemon。Docker 卷、数据库内容、下载数据和凭据不得提交。
若卷已存在而活动 `.env` 缺少 `POSTGRES_PASSWORD`，初始化会失败关闭，绝不生成新密码覆盖旧数据库访问设置，
也不会删除卷。生产 PostgreSQL 不使用此 Compose 文件。

仓库自动化绝不删除或清空数据库、表、schema 或 Docker 数据卷。数据库删除或清空只能由用户在仓库
自动化之外手动执行；Compose 入口只会创建或复用开发服务，不提供 reset、clear 或 volume remove 操作。

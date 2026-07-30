# Scripts

脚本按职责组织，日常只需要记住两个入口。

| 脚本 | 是否直接运行 | 用途 |
| --- | --- | --- |
| `setup_dev.sh` | 是 | 在 macOS/Linux 初始化 Docker PostgreSQL 开发环境。 |
| `deploy.sh` | 是 | 检查、构建、上传并部署到 Linux 服务器。 |

开发环境内部模块：

| 路径 | 用途 |
| --- | --- |
| `dev/common.sh` | 日志、错误处理与系统检查。 |
| `dev/docker.sh` | Docker 与 Compose 检查。 |
| `dev/env.sh` | 本地 `.env` 和数据库密码管理。 |
| `dev/postgres.sh` | 开发 PostgreSQL 启动和初始化。 |

Linux 部署内部模块：

| 脚本 | 用途 |
| --- | --- |
| `deploy/build-artifact.sh` | 构建不含密钥、数据和虚拟环境的源码制品。 |
| `deploy/provision.sh` | 远程部署总控。 |
| `deploy/install-runtime.sh` | 安装 Ubuntu/Debian 运行时、uv、Python 和服务用户。 |
| `deploy/install-release.sh` | 安装锁定依赖、迁移、健康检查、原子切换和失败回退。 |
| `deploy/lib/*.sh` | 公共函数、SSH 连接复用与非敏感部署配置读取。 |
| `deploy/systemd/*.service.in` | health 和 scheduler 的 systemd 安全模板。 |

首次部署：

```bash
cp deploy.env.example deploy.env
cp .env.production.example .env.production
# 编辑两个本地文件后执行
UPLOAD_ENV=1 SETUP_SERVER=1 scripts/deploy.sh
```

后续版本发布：

```bash
scripts/deploy.sh
```

完整说明见 `docs/14_Linux一键部署.md`。

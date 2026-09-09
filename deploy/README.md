# 独立应用部署

每个应用的 Compose 管理自己的多个容器，没有同时启动全部应用的总配置。

| 配置 | 容器 | 默认项目名 |
|---|---|---|
| `data_hub/compose.yaml` | Data API、数据 worker、Next.js 前端 | `northstar-data-hub` |
| `research/compose.yaml` | Research API、Next.js 前端 | `northstar-research` |
| `live/compose.yaml` | Live API、交易内核、Next.js 前端、数据库与初始化 | `northstar-live` |
| `storage/compose.yaml` | Data Hub/Research 共用 PostgreSQL、初始化与来源卷 | `northstar-storage` |

在仓库根目录执行 `make up-data`、`make up-research` 或 `make up-live`，构建并等待所属服务就绪。
对应 `make down-data`、`make down-research`、`make down-live` 停止应用并保留卷；
`make ps-data`、`make ps-research`、`make ps-live` 查看状态。

Data Hub、Research 启动前自动准备存储，不会启动彼此。停止任一应用不会停止共用存储；
需要时单独执行 `make down-storage`。当前 Research 通过数据管理模块读取共用持久数据，
不是跨主机数据交付；不要直接给两者换成独立数据库后期待数据自动同步。
Live 的网络、认证与数据库独立，启动不连接柜台或授予执行权限。

## 配置

可用 `ENV_FILE=/absolute/private/deploy.env` 向 Make 命令传入私有 Compose 环境文件。
同一部署后续启动、查看和停止应使用同一文件。Data Hub/Research/存储的配置必须一致：

- `NORTHSTAR_STORAGE_PROJECT`：共用存储项目名，默认 `northstar-storage`；决定外部网络和来源卷的名称。
- `NORTHSTAR_STORAGE_DATABASE_PASSWORD`：数据库口令，默认本机开发值 `northstar_local`；使用 URL-safe 字符。
- `NORTHSTAR_STORAGE_PORT`：PostgreSQL 本机端口，默认 `15432`。
- `NORTHSTAR_DATA_WEB_PORT` / `NORTHSTAR_DATA_API_PORT`：默认 `18082` / `19082`。
- `NORTHSTAR_RESEARCH_WEB_PORT` / `NORTHSTAR_RESEARCH_API_PORT`：默认 `18084` / `19084`。
- `NORTHSTAR_BACKEND_IMAGE`、`NORTHSTAR_DATA_FRONTEND_IMAGE`、`NORTHSTAR_RESEARCH_FRONTEND_IMAGE`：覆盖镜像引用。

Live 的独立变量见 [Live 部署说明](live/README.md)。默认仅开放本机端口；真实部署须设置私有口令与实测资源预算。
修改环境变量不会轮换已有数据库的口令，也不会迁移数据。不要输出含口令的完整 Compose 渲染结果。

手动操作时，先启动存储，再启动所需应用（Live 不需要此存储）：

```sh
docker compose -f deploy/storage/compose.yaml up -d --wait postgres
docker compose -f deploy/storage/compose.yaml run --rm initialize
docker compose -f deploy/data_hub/compose.yaml up -d --wait
# 或单独启动 Research
docker compose -f deploy/research/compose.yaml up -d --wait
```

部署已验证镜像时加 `--no-build`，设置对应镜像引用；从源码构建由 Make 注入 Git 身份。
只重启容器不会构建新代码。启动命令不删除、迁移或接管已存在的个人容器与持久卷；
已有环境使用相同端口时，应先确认其数据与停机安排，再切换。禁止用 `down -v` 作为日常停止命令。

## 验收

`scripts/check_application_deployment.py` 创建独立临时存储和两个应用项目，验证 Research 单独启动、
API/前端停止后的持久加工、Data Hub 停止后的固定数据回测，以及停止任一应用不影响另一应用与数据保留。
`scripts/check_live_deployment.py` 验证 Live 独立内核、管理端重启和数据库故障。
两者都只清理自己创建的临时容器、网络和卷，不使用柜台凭据。完整调用见 CI。

# systemd 模板

这里保存 health、scheduler 和默认关闭的私网 Dashboard 服务模板。它们只由 Linux 生产目标运行；
Windows/Linux 开发机通过 `scripts/deploy/deploy.py` 和 `just` 远程编排，不在本机模拟 systemd。
Linux 发布流程会将本目录与 `scripts/deploy/` 一起临时上传到远端，再在通过生产环境、preflight 和
实盘确认门禁后渲染模板。

worker 是未来 Linux 运行能力：在仓库存在可审计的 worker CLI、队列语义、幂等和停机门禁之前，不创建
空壳 service 单元，也不允许部署流程将研究回测伪装为长期 worker。

不要手工降低 `health`、`scheduler`、kill switch 或 Dashboard loopback 约束；模板不应包含密码、令牌或真实账户信息。

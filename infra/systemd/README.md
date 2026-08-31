# systemd 模板

这里保存 health、scheduler 和默认关闭的私网 Dashboard 服务模板。它们只由 Linux x86_64 生产目标运行；
Linux x86_64 工作站通过 `scripts/deploy/deploy.py` 和 `just` 远程编排，不在本机模拟 systemd。

模板随不可变发布制品进入目标主机，而不是作为可变共享文件使用。每个通过 preflight 的 release 会先冻结为
root 管理状态，再由 root 从 release 内的模板渲染
`/opt/northstar/releases/<release>/.northstar/systemd/<unit>.service`。渲染单元记录 release ID 与制品
SHA-256；root 将该 release 快照的副本安装到 `/etc/systemd/system/`。回退只能恢复上一 release 的同一
快照，若活动单元与受管快照不一致或首次部署发现未知同名单元，流程会失败关闭而不覆盖它。

生产布局固定为 `/opt/northstar`（代码与 `current`）、`/etc/northstar`（环境快照与规范指针）、
`/var/lib/northstar`、`/var/cache/northstar` 和 `/var/log/northstar`。规范
`/etc/northstar/northstar-quant.env` 是 root 管理的符号链接，且必须精确指向
`/opt/northstar/current/.env`；它不是可替换的全局秘密文件。每个 release 的 `.env` 是 root 创建的链接，
指向同名 `/etc/northstar/releases/<release>.env` 快照，快照本身必须为 `root:northstar 0640` 普通文件。
`/etc/northstar/` 与其 `releases/` 子目录均为 `root:northstar 0750`，使服务账户只能读取获准的快照而不能
替换指针或版本内容。
模板故意使用 `EnvironmentFile=@CURRENT_LINK@/.env`，使原子切换或回退 `current` 自动选择与代码和 systemd
快照一致的环境快照；没有全局 active-config 提升、备份或独立配置回退。

`northstar` 服务账户只可写明确列出的状态、缓存和日志叶子目录；每一个 `ReadWritePaths` 目标必须是
受 root 控制父目录的一层直接孩子，不能把 `downloads`、Dashboard HOME 或其他可写目标嵌套到已有服务
可写叶子下。模板必须保持 `ProtectSystem=strict` 和最小 `ReadWritePaths` 白名单，不能重新引入对整棵
release、`/etc` 或共享目录的写权限。旧 `/srv/northstar/northstar-quant/shared` 布局没有兼容分支。

systemd 模板只描述已发布 release 的运行时隔离，不能单独构成发布信任边界。发布前，固定的
`/usr/local/libexec/northstar-quant/release-gate` 会验证 root 管理的签名 authority、canonical manifest 和
runtime/control bundle 索引，把控制 bundle 解包到 `root:root 0700` 的持久事务目录，再运行固定入口；root
不会从部署 SSH 身份可写的临时目录执行 `provision.sh` 或安装辅助脚本。gate 本身必须由服务器管理员带外
bootstrap，常规部署只可调用其受限 `identity` 与 `submit` 动作。迁移开始后的事务失败进入人工恢复，不能借由
systemd 快照自动切回旧 release 并重启服务。

worker 是未来 Linux 运行能力：在仓库存在可审计的 worker CLI、队列语义、幂等和停机门禁之前，不创建
空壳 service 单元，也不允许部署流程将研究回测伪装为长期 worker。

不要手工降低 `health`、`scheduler`、kill switch、服务隔离或 Dashboard loopback 约束；模板不应包含密码、
令牌或真实账户信息。

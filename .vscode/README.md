# VS Code 开发

在仓库根目录打开 VS Code，安装推荐的 Python/Debugpy 扩展，并准备 uv、Node.js/npm 和 Docker。

- **运行任务**：安装依赖、运行前端、检查代码/协议、执行测试；远程应用管理默认选择 `status`。
- **运行和调试（F5）**：选择单个 API、worker、Live 内核或前端；“全栈”组合启动该应用的前端和后端进程。
- **浏览器调试**：应用启动后选择对应“浏览器”配置，调试 React 页面。

前端使用 18082（Data Hub）、18084（Research）、18080（Live）；API 使用 19082、19084、19080，Live 内核使用 18081。
这些端口需要空闲，不能与同机已部署的应用同时占用。Research 通过本地 Data Hub API 读取发布清单，需要查询数据时先启动 Data Hub。

调试前自动准备依赖和存储：专用 PostgreSQL 位于 `127.0.0.1:15432`，Data Hub、Live 和测试各用独立逻辑库；Research 使用本地 SQLite。
文件、日志和认证保存在 `.northstar/vscode/`，不加载 `deploy/live/.env` 或个人柜台凭据。
`.vscode/compose.yaml` 只管理开发 PostgreSQL，不部署业务应用；停止数据库任务保留开发数据。测试会重置专用的 `northstar_quant_test`。

停止一个调试进程不会自动停止组合中的其他进程；可在调试工具栏分别选择并停止。整套远程 `restart`/`stop` 会影响所选应用的内核或 worker。

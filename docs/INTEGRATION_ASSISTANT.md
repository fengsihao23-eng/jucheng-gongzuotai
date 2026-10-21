# 小聚 · 自主接入 Agent

工作台右上角点击「小聚」，直接描述目标。公司员工均通过 Pi 核心执行任务，企业接入由管理员统一授权一次；员工直接复用公司配置，并按自己的权限使用页面和个人 WorkBuddy 授权。

示例：直接帮我接好 WorkBuddy 并验证连接 / 自动连接企业微信，先读取已有配置 / 柠檬云也接上 / 检查所有连接状态，不修改配置。

## 执行核心

官方 `@earendil-works/pi-agent-core@1.3.0` 和 `@earendil-works/pi-ai@1.3.0` 负责多轮工具调用与状态管理。Python FastAPI 负责账号权限、主机工具、凭证保管和任务持久化。模型使用 DeepSeek V4.1 Flash（`deepseek-flash`）。没有委托到用户全局 Pi CLI 的其他模型账号。

API Key 只经独立进程 stdin 传入，不放在命令行、任务文件或 GitHub。npm lockfile 固定依赖；Docker 构建自动安装 Node 和 Pi 核心。本地开发安装一次：

```bash
cd backend/agent_runtime
npm ci --ignore-scripts
```

服务器 `backend/.env` 示例（实际文件不提交）：

```dotenv
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-flash
LLM_API_KEY=<实际密钥>
LLM_THINKING_MODE=disabled
ASSISTANT_HOST_ACCESS=true
```

公司统一部署默认关闭主机能力，不依赖某位员工的个人电脑。只有管理员明确配置当前主机、且执行环境与 WorkBuddy 所在电脑一致时才启用主机能力。云端容器不能通过网页访问用户电脑文件，未连接本机执行环境时会明确报告未完成，不会把配置写到服务器后声称已连接用户客户端。

## 自动接入

| 系统 | 小聚实际执行 | 尚需真实外部权限的部分 |
|---|---|---|
| 管理员本机 WorkBuddy | 生成或复用个人令牌；合并 `~/.workbuddy/mcp.json`；备份旧文件；实际请求公开端点进行 MCP 握手与工具发现 | 配置已写入并验证可达，与客户端已加载不同；必要时刷新 WorkBuddy 的 MCP 面板 |
| 企业微信 | 自动发现凭证；生成回调密钥；验证企业身份与 AgentID；保存并启用真实 API | 管理后台的回调 URL 验证、可信 IP/域名与业务权限必须得到企业授权 |
| 柠檬云 | 自动发现凭证；验证财税开放平台基础授权并保存 | 账套授权及现有桥接适配器的官方接口联调尚需完成；不把应用授权当作真实财务同步 |

可直接调用已安装接入 CLI 的 `--help` 和 `--version`，配置由文件和 API 工具执行。不会把未验证的 `pi status` 等字符串作为命令，以免 CLI 将其解析成另一场模型对话。

重复接入 WorkBuddy 时，匹配本地令牌与当前账号哈希后复用；发生握手、文件或审计错误时恢复原文件及令牌。「只检查」任务在执行器内禁止配置写入。

员工个人 WorkBuddy 接入由小聚自动生成或复用个人授权、实际自检并提供已填好的配置下载。令牌按账号在服务器 AES-GCM 加密保存，不进入模型或任务日志；下载只对本人有效且一小时过期。首次客户端导入仍需在客户端完成，不能把网站服务器当成员工电脑。

员工连接企业微信或柠檬云时，直接复用公司的统一配置，不读取或索取企业密钥；公司尚未授权则显示「等待公司管理员统一授权」。

## 自动读取与最少人工介入

读取已知来源：当前环境和生效配置、用户导入的接入文件、项目根的 `.env` / `.env.local` / `integration-credentials.env` / `.wecom.env` / `.lemoncloud.env`、后端接入文件以及 WorkBuddy MCP 的 env 字段。

- Pi 只收到来源标识、字段名称和缺失项；凭证值留在 Python。
- 缺企业授权时，只列出具体缺项，提供官方入口及「读取已有接入文件」。
- 导入 `.env` 或 JSON 后自动继续验证，不必逐项抄写所有字段。
- 多份不同账号的凭证需要选择来源，避免混用不同企业。
- 回调密钥可由小聚生成，但必须与企业后台保持一致。
- 没有真实授权时，不会发送消息、同步财务或显示已接通。

备用配置表单在任务下方展开，不是自动接入的必经步骤。

## 执行记录和权限

任务显示实际工具步骤，可停止；关闭面板后继续运行，重新打开可以恢复进行中的任务。默认最长 180 秒、24 次工具调用，工具逐项执行。

- 任务：`STORAGE_DIR` 父目录的 `xiaoju/tasks/`，按账号隔离；不保存聊天指令原文或密钥。
- 导入凭证：`xiaoju/credentials/<user-id>/`；企业生效配置：`integrations.json`；文件均为 `0600`。
- 每次工具调用重新检查账号状态与角色；公司配置、主机读写和 CLI 仅管理员可执行。员工可使用共享连接、个人授权及有权限的页面，不能读取或停止他人任务。
- 接入结果由工具事实生成，不能用模型文字或无关 CLI 错误代替实际检测。
- 聊天疑似密钥在发送到模型前拦截；第三方响应不原样回显凭证。

Docker 的 `/data` 已有持久化卷，迁移时需安全保管上述文件，它们不属于 PostgreSQL 数据库备份。

## 验证

```bash
cd backend
TEST_DB=jucheng_test_xiaoju .venv/bin/python -m pytest tests -q
cd ../frontend
VITE_OUT_DIR=/tmp/jc_build_xiaoju npm run build
```

浏览器测试需连接独立测试库及临时 `ASSISTANT_HOST_HOME` 的 API：

```bash
cd acceptance
API=http://127.0.0.1:18100 node xiaoju.mjs
API=http://127.0.0.1:18100 node assistant.mjs
```

主流程测试使用真实 Pi 核心、DeepSeek、CLI 和 MCP 握手；备用流程测试覆盖表单、下载、普通员工权限和手机布局。

当前验证：230 项后端测试、18 项自主执行与员工流程检查、13 项备用流程检查通过；前端与 Docker 构建通过。企业微信和柠檬云尚未配置公司的真实授权，柠檬云财务同步仍需按账套的官方接口联调，不能据此声称公司系统已经全部接通。

## 官方资料

- [Pi 官方仓库与 MIT 许可](https://github.com/earendil-works/pi)
- [Pi SDK](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/sdk.md)
- [DeepSeek V4.1 Flash](https://deepseek.com/en/news/deepseek-v4-1-flash/)
- [WorkBuddy MCP](https://www.workbuddy.ai/docs/zh/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/MCP-Guide)
- [企业微信开发文档](https://developer.work.weixin.qq.com/document/path/91039)
- [柠檬云财税开放平台](https://open.ningmengyun.com/)

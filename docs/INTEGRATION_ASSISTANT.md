# 站内接入助手

从任意工作台页面顶部点击「接入助手」，或在「WorkBuddy 接入」页面点击「让接入助手帮我配置」。

可以直接说：

- 帮我接入 WorkBuddy
- 帮我接入企业微信
- 柠檬云怎么授权
- 检查所有连接
- 报销在哪 / 流水怎么核对

## 当前能力

| 系统 | 助手能执行 | 仍需完成 |
|---|---|---|
| WorkBuddy | 生成个人令牌与 MCP JSON、执行本服务的 initialize / tools/list 自检、复制或下载配置 | 在 WorkBuddy 的 MCP 配置中合并 JSON 并查看客户端连接状态；已有令牌时需确认重置 |
| 企业微信自建应用 | 检查企业身份、Secret 与 AgentID；生成回调密钥；验证通过后保存凭证并启用真实接口 | 企业微信后台的回调 URL 验证、可信 IP / 可信域名、应用可见范围、审批与通讯录权限 |
| 柠檬云财税开放平台 | 调用官方基础授权接口验证 App ID / App Secret、保存凭证 | 用户与账套授权；现有财务同步适配器的真实接口与字段联调。此步骤保留演示模式，不触发真实财务同步 |

柠檬云当前财务适配器 `app/integration/lemoncloud/live.py` 使用待联调的桥接协议，与财税开放平台和财税能力平台的官方协议不同。助手不会因为基础授权成功就启用该适配器。`nmy.cn` 财税能力平台也不是本次配置表单所连接的平台。

「演示模式」「真实模式 · 待检测」「待客户端导入」描述不同状态，不能把保存、鉴权、自检和业务接通视作同一件事。当前不自动判断外部客户端已导入或企业微信后台已验证回调。

## 对话与执行

聊天接口只识别意图并给出接入卡片或有权限的页面链接，不执行配置写入。表单的「只检测」不改变配置；「验证并启用 / 验证并保存」验证成功后才写入。

未配置模型也可识别常用接入指令和操作导航。如果服务器已有 `LLM_BASE_URL / LLM_API_KEY / LLM_MODEL`，无法由内置指令识别的请求可交给模型分类，最长等待 12 秒。模型只能返回受限的系统标识或页面路径，写操作仍由后端独立鉴权。聊天不具备通用业务问答或任意执行能力。

### DeepSeek V4.1 Flash

在服务器的 `backend/.env` 中配置，实际 API Key 不提交到 GitHub：

```dotenv
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-flash
LLM_API_KEY=<服务器实际密钥>
LLM_THINKING_MODE=disabled
```

官方的 V4.1 Flash API 模型标识为 `deepseek-flash`。接入助手使用 JSON 输出，并显式关闭 DeepSeek 默认开启的思考模式，避免短分类请求的输出预算被思考过程耗尽。其他模型服务可将 `LLM_THINKING_MODE` 留空。修改环境配置后重启 API 进程。

参考：[DeepSeek 模型说明](https://deepseek.com/en/news/deepseek-v4-1-flash/)、[思考模式](https://api-docs.deepseek.com/guides/thinking_mode/)、[JSON 输出](https://api-docs.deepseek.com/guides/json_mode/)。

## 凭证与持久化

- 企业级接入配置和连接检测仅允许 `ADMIN-01` 平台管理员。其他员工可生成自己账号的 WorkBuddy 配置。
- 企业凭证使用独立表单；不进入模型上下文、聊天历史或数据库。审计仅记录字段名和结果，不记录值。
- 服务器配置保存到 `STORAGE_DIR` 的父目录下的 `integrations.json`，权限为 `0600`；采用文件锁和原子替换。启动时恢复，请求和数据源初始化时检查更新，支持部署中的多个 API worker。
- Docker Compose 的默认 `STORAGE_DIR=/data/files` 对应 `/data/integrations.json`，位于已有持久化卷中。迁移或备份环境时需安全保存此文件；它不包含在 PostgreSQL 备份中。
- 已保存配置优先于部署环境中相同的接入配置。企业微信仅覆盖自身的数据源模式，其他来源不受影响。
- GET 鉴权接口的 URL 含凭证，禁用 httpx 的 INFO 请求日志。平台错误不原样回显。
- WorkBuddy 令牌仍沿用现有机制：数据库只存哈希，明文仅在当前面板显示；配置卡片中的临时内容在关闭时清除。

## 官方协议依据

- [WorkBuddy MCP 配置](https://www.workbuddy.ai/docs/zh/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/MCP-Guide)
- [企业微信开发文档](https://developer.work.weixin.qq.com/document/path/91039)
- [柠檬云财税开放平台](https://open.ningmengyun.com/)：`GET /api/OAuth2/AccessToken?appId=...&appSecret=...`
- [柠檬云财税能力平台认证](https://opendocs.nmy.cn/doc-8806534)：这是不同的平台，不适用于上述财税开放平台表单。

## 验证

```bash
cd backend
TEST_DB=jucheng_test_assistant .venv/bin/python -m pytest tests/test_assistant.py tests/test_platform.py tests/test_mcp.py -q

cd ../frontend
VITE_OUT_DIR=/tmp/jc_build_assistant npm run build
```

外部鉴权用 `httpx.MockTransport` 测试成功、错误与无令牌的返回；MCP 使用真实应用的 ASGI 握手。正式接入仍需管理员提供实际企业凭证并完成外部平台配置。

浏览器流程：启动指向独立测试库的 API 后，运行 `cd acceptance && API=http://localhost:18100 WEB=http://localhost:5180 node assistant.mjs`。覆盖配置生成、自检、JSON 下载、凭证清除、企业管理员权限、页面导航与手机宽度布局。不要让此测试连接实际业务库，它会重置测试账号的 MCP 令牌。

本次验证：全量后端测试 204 项通过，浏览器流程检查 13 项通过，前端生产构建通过。DeepSeek V4.1 Flash 已使用实际 API 验证模型可用性和意图分类；企业微信、柠檬云的实际企业凭证尚未提供，未将这些企业接入标记为已完成。

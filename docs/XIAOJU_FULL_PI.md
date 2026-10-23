# 小聚完整 Pi 运行说明

## 使用

右上角打开「小聚」，可以新建、切换或复制对话，选择已启用模型和思考设置。对话自动保存在当前账号；刷新后可继续。较长上下文由 Pi 自动压缩，也可使用「整理上下文」。重要业务操作仍显示具体确认单，确认后继续原会话。

执行时文字与工具进度实时显示，输入框可补充要求；「停止执行」会终止当前模型任务及正在执行的工作区命令。对话中的「查看执行记录」可重新查看此前的业务回执和待确认单。

上传业务附件后，小聚既可调用原模块办理业务，也可在独立工作区运行 Python、处理 Excel/Word、生成文件。工作文件由本人鉴权下载；复制对话会复制已有工作文件。支持的具体格式仍取决于原业务接口、已安装库和所选模型，接入 Pi 不代表自动获得所有外部系统权限。

升级前未保存的原始聊天无法补回；原任务、执行单和业务回执继续保留。升级后的会话保存完整 Pi 消息与工具历史。

## 运行结构

- `pi-coding-agent@1.3.0` 的 `createAgentSession`、`SessionManager`、`DefaultResourceLoader` 管理会话与资源。
- 显式启用官方 Codemode、工具搜索与 MCP 扩展；公司 Skills、提示模板和扩展位于 `backend/agent_runtime/resources/`。这些资源由管理员随版本部署，不从脚本工作区加载可执行扩展。
- 原生 `read / write / edit / bash / grep / find / ls` 使用同版本 SDK 工具，委托独立 Docker 容器实际执行；可信 Skills 允许只读加载。
- Python 业务执行器继续承担账号授权、数据范围、重要操作确认、幂等和回执。每次模型请求与工具调用重新核验会话权限。
- SSE 连接在鉴权后释放数据库连接，断开后前端可恢复或轮询。不同 FastAPI worker 共享文件状态与锁；同一会话只允许一个任务执行。
- 进程文件锁用于识别服务重启后中断的排队和执行任务，避免旧任务重放；继续时使用原回执核对剩余工作。

## 工作区边界

生产 API 通过受控 Docker CLI 创建工具容器。容器仅挂载当前账号、当前对话的工作目录；不挂载 API 代码、企业密钥、数据库连接、Docker socket 或其他账号目录。

执行容器禁用网络、只读根文件系统、删除 capabilities、启用 no-new-privileges，以 UID 10001 运行；限制 1 CPU、384MB 内存、64 个进程，临时目录 64MB。每个工具默认最多 90 秒，单文件最多 50MB；同时检测会话工作文件总量 200MB、10000 个目录项及目录深度。目录总量为运行期间检测，不等同于文件系统项目配额。

公司业务通过原授权工具调用。新增外部 MCP 服务器需由管理员配置并适配其业务权限、写入确认与网页交互；本版本不自动配置未知外部服务，也不保证第三方终端界面扩展可以直接在网页显示。

## 配置

模型密钥继续使用 `LLM_API_KEY`，不进入命令行、会话文件或 Git。可配置项：

| 配置 | 默认 / 部署值 |
| --- | --- |
| `ASSISTANT_MODELS` | 留空只启用 `LLM_MODEL`；生产配置 `deepseek-flash,deepseek-v4-pro` |
| `ASSISTANT_THINKING_LEVEL` | `medium`，Pi 根据模型支持的等级调整 |
| `ASSISTANT_TASK_TIMEOUT_SECONDS` | `900` |
| `ASSISTANT_MAX_TURNS` | `80` |
| `ASSISTANT_MAX_TOOL_CALLS` | `256` |
| `ASSISTANT_MAX_OUTPUT_TOKENS` | `8192` |
| `ASSISTANT_SANDBOX_ENABLED` | 本地默认 false，Compose 设置 true |
| `ASSISTANT_SANDBOX_IMAGE` | `jucheng-xiaoju-workspace:1.3.0` |
| `ASSISTANT_WORKSPACE_ROOT` | Compose 中 `/workspaces` |
| `ASSISTANT_WORKSPACE_VOLUME` | Compose 中 `jucheng_xiaoju_workspaces`，使用 volume-subpath |
| `ASSISTANT_SANDBOX_TIMEOUT_SECONDS` | `90` |

服务器要求 Docker 26 或更高版本，以支持只挂载卷内指定子目录。API 镜像带有 Docker CLI；socket 仅挂载给可信 API，不传给执行容器。容器内安装 Python、openpyxl、python-docx、Pillow、Git 和 ripgrep。

部署脚本先构建执行镜像，再更新 API 和前端，并检查和重载 Nginx。`APP_REVISION` 注入 Git 提交号，公开 `/health` 的 `revision` 可用于核对上线版本。`APT_MIRROR`、`WORKSPACE_APT_MIRROR`、`PIP_INDEX_URL`、`NPM_REGISTRY` 均在不入库的 `deploy/.env` 配置。

## 存储与备份

- `/data/xiaoju/tasks/`：任务、步骤和运行锁。
- `/data/xiaoju/sessions/<user-id>/<session-id>/`：对话界面文本、会话配置与官方 Pi JSONL。
- `/data/xiaoju/business/`：原业务附件、确认单与回执。
- `jucheng_xiaoju_workspaces` 卷：工作文件，按账号和会话隔离。

会话文件及元数据使用 0600 权限。权限、密码、数据源或个人连接变化会使旧上下文失效；用户主动授权并完成的公司连接变更，会在核对账号权限后刷新当前会话范围。

数据库、`/data`、工作区卷和生产配置需要共同纳入备份。业务与模型凭证仍按原方式保管，备份不可提交 Git 或放到静态站点目录。

## 验证命令

后端使用独立测试库：

```sh
cd backend
TEST_DB=jucheng_test_full_pi .venv/bin/python -m pytest tests -q
cd agent_runtime
npm ci --ignore-scripts
npm run check
```

浏览器流程必须使用独立数据库、临时存储和 18100 API，并将 `PUBLIC_BASE_URL` 设置为测试 API 地址。它们会在测试库登记工单、合同和生成文件：

```sh
API=http://127.0.0.1:18100 WEB=http://127.0.0.1:15180 node acceptance/xiaoju-business.mjs
API=http://127.0.0.1:18100 WEB=http://127.0.0.1:15180 node acceptance/full-pi.mjs
```

完整 SDK 测试覆盖原生工具委托、资源范围、跨进程恢复、压缩摘要持久化、确认后停止与任务预算；业务和会话测试覆盖跨账号访问、权限变化失效、文件软链接与路径穿越、SSE、补充消息和服务中断恢复。

2026-10-23 验证：全量后端 **299 项通过**；真实 Pi + DeepSeek 业务浏览器验收 **18 项通过**，完整会话、文件和手机流程 **12 项通过**；Flash 和 Pro 均完成真实模型请求。生成的 Excel 经 openpyxl 独立读取核对，工作表、表头、两条数据与数值类型符合要求。前端生产构建通过。

生产服务器 Docker 26.1.3 及 Node 22.23.4 已实测：完整 SDK 可启动会话，volume-subpath 可用；工作容器中没有 API 凭证、Docker socket 或后端代码，UID 隔离、禁网及只读根文件系统检查全部通过。

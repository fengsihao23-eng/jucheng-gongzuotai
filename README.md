# 聚成数智 · 统一工作台

广东聚成数智科技有限公司内部的统一工作台 + MCP Server：企业微信审批、银行流水、柠檬云账套与各部门台账在这里归集，报表与经营看板自动生成，并通过 MCP 把能力提供给腾讯 WorkBuddy。

需求来源：《聚成科技 Workbuddy 场景内容需求 20261012》→ `docs/PRD.md`。验收结果：`docs/ACCEPTANCE.md`。

## 技术栈

| 层 | 选型 |
|---|---|
| 前端 | Vue 3 + Element Plus + ECharts（Vite） |
| 后端 | Python 3.13 · FastAPI · SQLAlchemy 2 · Alembic |
| 数据库 | PostgreSQL 17 |
| 定时任务 | APScheduler（进程内）+ Postgres advisory lock 防重复执行 |
| MCP | 官方 `mcp` SDK（FastMCP，Streamable HTTP） |
| 部署 | Docker Compose：Nginx + API + PostgreSQL + 每日备份 |

相对 PRD 2.2 的调整：MySQL → PostgreSQL、Celery/Redis → APScheduler（单机百人规模足够，少两个组件）。

## 本地运行

```bash
# 后端
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
createdb jucheng
cp .env.example .env            # 本地开发把 DATA_SOURCE_MODE 改为 mock、DATABASE_URL 指向本机库
.venv/bin/python -m scripts.seed  # 写入演示数据
.venv/bin/uvicorn app.main:app --port 8100

# 前端
cd frontend && npm install && npm run dev   # http://localhost:5180
```

演示账号统一口令 `Jc@2026`（登录页可按部门选择）：董事长 `zhangzhenhua`、财务经理 `chenjing`、出纳 `zhaoli`、市场经理 `wujun`、营销管控 `songjia`、项目经理 `xuhao`、人事 `caofang`、售前 `xiekai`、客服 `yangyue`、管理员 `admin`。

## 模块

| 部门 | 页面 | 主要能力 |
|---|---|---|
| 总经办 | 经营看板 / 大屏 / 汇报提交 | 六板块实时看板、晨报推送、对比分析、未提交催交 |
| 财务部 | 报销归集 / 流水核对 / 出纳日记账 / 项目利润 / 财务数据 / 报表中心 | 审批即归集、流水解析与智能核对、每日支出/周利润/公司周报月报、柠檬云与税务 |
| 市场部 | 重点项目跟进 / 竞品情报 / 项目复盘 | 周报月报自动成稿、进展反馈、复盘初稿 |
| 营销管控部 | 合同台账 / 商机信息 / 业绩与运营 / 数据清洗 | 审批自动登记、合同归档读回款、业绩统计、Excel 清洗导入 |
| 项目部 | 项目流程 | 标准控制流程、节点增删排序、延期标红、周进度 |
| 人事部 | 人事中心 | 考勤核对、到期预警、文书与风险提示、人力分析 PPT、招聘 |
| 产品中心 | 标书助手 | 多轮合规检查、带出处的方案生成 |
| 客服部 | 客服工单 | 一句话登记、相似工单、排查建议、归因报告 |
| 公共 | 知识库 / WorkBuddy 接入 / 系统管理 | 乐享检索、MCP 令牌与工具注册表、角色字典任务审计 |

## 小聚业务 Agent 与 WorkBuddy 接入

任意页面顶部的「小聚」使用 Pi 核心与 DeepSeek，可按当前账号权限执行现有工作台的 241 个业务动作（227 个业务 API、13 个 MCP 工具和附件预览），覆盖财务、合同、项目、人事、客服、标书等 21 个模块。支持上传业务文件、连续对话、实际写入、报表下载及操作回执；记账、删除、权限调整、对外通知会生成具体执行单，点击确认后提交。业务范围及验证见 [小聚业务能力说明](docs/XIAOJU_BUSINESS.md)。

小聚也能自动读取接入配置、调用 CLI、写入 WorkBuddy MCP 配置并验证连接，自动发现和验证企业微信、柠檬云凭证。接入范围见 [接入说明](docs/INTEGRATION_ASSISTANT.md)。本地运行前在 `backend/agent_runtime` 执行一次 `npm ci --ignore-scripts`；Docker 自动安装。

企业微信还支持按账号绑定官方 CLI 个人连接，可调用文档、表格、待办、日程等实际获授权的业务，并在缺少功能权限时提供官方授权入口。使用与账号保护说明见 [企业微信 CLI](docs/WECOM_CLI.md)。

面向公司员工：企业系统由管理员统一授权一次，员工通过小聚按各自权限使用，无需各自提供企业密钥。员工个人 WorkBuddy 授权由小聚自动生成、自检并提供完整配置下载；不能把网站服务器当成员工的电脑，首次客户端导入仍需在客户端完成。

登录工作台 →「WorkBuddy 接入」→ 生成个人令牌 → 复制配置片段到 WorkBuddy。端点 `https://<域名>/mcp`，鉴权 `Authorization: Bearer <令牌>`。13 个工具见该页「工具注册表」，管理员可在线启停与授权。

## 测试与验收

```bash
cd backend && TEST_DB=jucheng_test .venv/bin/python -m pytest tests -q   # 257 项
cd acceptance && npm install && node e2e.mjs                            # 端到端 68 项，需服务在运行
```

小聚真实模型业务验收：在独立测试库启动端口 18100 的 API，运行 `cd acceptance && node xiaoju-business.mjs`。不能连接实际业务库；流程会创建演示工单和合同。

## 生产部署

目标环境：阿里云 ECS（4C8G，华南1 深圳）· Ubuntu/Debian · Docker Compose · 域名 `https://juchengcc.com`。

```bash
cd frontend && npm run build          # 产出 frontend/dist
SERVER=root@<ECS公网IP> ./deploy/deploy.sh --seed   # 首次部署（--seed 写入演示数据；更新部署去掉该参数）
```

`deploy/` 下的部署物料：

| 文件 | 用途 |
|---|---|
| `deploy.sh` | rsync 代码与 dist → `/opt/jucheng` → 安装环境变量 → `docker compose up -d --build` → 健康检查 |
| `app.env` | 生产环境变量（含 SECRET_KEY / 大模型密钥，`600`，不入库），脚本安装为服务器 `backend/.env` |
| `.env` | `DB_PASSWORD`，供 compose 注入 PostgreSQL |
| `certs/fullchain.pem` `certs/privkey.pem` | `juchengcc.com` + `www.juchengcc.com` 证书（有效期至 2027-05-06，到期前替换） |
| `nginx.conf` | 80→443 跳转、www→主域名跳转、SPA、`/api`、`/mcp` 反代 |

服务器前置条件：安全组放行 22/80/443；域名 `juchengcc.com` 与 `www` 的 A 记录指向 ECS 公网 IP（备案需已通过，否则阿里云阻断 80/443）。

日常运维：

```bash
cd /opt/jucheng/deploy
docker compose ps                 # 状态
docker compose logs -f api        # API/定时任务/MCP 日志
docker compose restart api        # 重启
docker compose exec -T api python -m scripts.seed   # 重建演示数据（清空业务表）
```

数据库每日 02:30 全量备份到 `deploy/backups/`，保留 7 天。

上线前：配置企业微信 / 乐享凭证并按数据源启用真实模式；企业微信也可由小聚自动读取凭证并验证启用。柠檬云适配器须先按实际账套接口联调，通过前保持 `lemoncloud:mock`。企业微信后台回调地址配置为 `https://juchengcc.com/api/v1/wecom/callback`。生产 `DATA_SOURCE_MODE=mock` + `PASSWORD_LOGIN_ENABLED=true` 期间按演示环境运行，切换真实数据源后建议关闭口令登录。

## 开发约定

见 `docs/DEV_GUIDE.md`。

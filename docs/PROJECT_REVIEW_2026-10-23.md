# 项目评审 · 聚成数智统一工作台 v2.0

评审日期：2026-10-23　评审方式：源码通读 + 本地实跑（后端测试、前端构建）+ 文档与代码交叉核对
代码规模：后端 25,929 行（app/）· 测试 5,071 行 · 前端 10,164 行 · 文档 10 篇（PRD 77KB）

---

## 一、项目定位

面向广东聚成数智（约百人规模）的内部统一工作台：把企业微信审批、银行流水、柠檬云账套、各部门台账归集到一处，自动出报表与经营看板，并通过 MCP 把能力开放给腾讯 WorkBuddy；另有一个贯通全站的业务 Agent「小聚」。

一句话概括技术形态：**单体 FastAPI + Vue 3 SPA + PostgreSQL，进程内调度，MCP 作为第二出口，Agent 作为第三入口**。

## 二、实测规模

| 维度 | 数字 | 出处 |
|---|---|---|
| HTTP 接口 | 276 个 | `app/api/*.py` 的 `@router.*` |
| 数据表 | 52 张 | `app/models/` 四个模块 |
| 服务层模块 | 44 个 | `app/services/*.py` |
| 定时任务 | 29 个 | `app/jobs/` 的 `@job(...)` |
| MCP 工具 | 13 个 | `mcp_server/server.py` TOOL_META |
| 前端页面 | 30 个视图 + 15 个组件 | `src/views/`、`src/components/` |
| 权限角色 | 15 个（部门 × 角色） | `core/permissions.py` ROLE_DEFS |
| 后端测试 | **299 项，实跑全绿**（51s） | `pytest tests -q` |
| 端到端验收 | 68 项，`results.json` 全部 pass | `acceptance/evidence/` |

## 三、完整链路

### 3.1 同步请求链路

```
浏览器 (Vue3 SPA)
  └ src/api/client.js  axios 拦截器：注入 Bearer、解包 {code,message,data}、401 跳登录、X-Refresh-Token 滑动续期
      └ Nginx  TLS / SPA / /api 与 /mcp 反代
          └ FastAPI  /api/v1/*
              ├ trace_and_renew 中间件：trace_id + 剩余 <30min 下发新 JWT
              ├ get_principal：JWT → sys_user → build_principal
              │    = ROLE_DEFS（代码基线） ⊕ sys_role.permissions（库内可调）→ menus / buttons / data_scope
              ├ require_menu("xxx") / p.can("xxx:yyy")  ← 菜单与按钮双层
              ├ services/<domain>_service  ← 业务全在这里，API 层保持薄
              └ SQLAlchemy → PostgreSQL
      统一出口：core/response.py ok()/BizError；core/audit.py log_op 留痕；core/storage.py 文件 + 1h 签名 URL
```

关键点：**权限是"代码基线 + 库内覆盖"的双层模型**，管理员能在线改角色而不改代码；数据范围 `self/dept/all` 在 service 层收口（如项目经理只能看 `owner_id == 自己`）。

### 3.2 数据入口 → 台账（项目的核心价值所在）

| 入口 | 链路 | 落点 |
|---|---|---|
| 企微审批 | `/wecom/callback` → `wecom/crypto` AES 验签解密 → `dispatch_service`（`sys_dict.approval_template` 字段映射）→ 以 `sp_no` 幂等 | 合同台账 / 报销归集 / 商机 |
| 审批失败兜底 | 重试 3 次 → `dispatch_status=2` 人工队列 + 告警；`jobs/exec` 定时对账补登 | 同上 |
| 银行流水 | 上传 xlsx → `bankflow/parser` 模板识别 → `fin_bank_flow` → `reconcile_service.suggest`（金额+对手方打分）→ 人工确认 | 出纳日记账 + 回款冲抵 |
| 报销 | 审批通过 → 带项目则归集、缺项目进"待归集池" → 项目成本 | 项目利润 |
| 柠檬云 | `integration/lemoncloud/{mock,live}` 适配器 | 凭证 / 科目余额 / 出纳流水 |
| 乐享知识库 | `integration/lexiang` → `kb_document`/`kb_chunk` → `kb_service.search`（带出处） | 知识库检索 |
| Excel 清洗 | `clean_service` 预览映射 → 导入 | 商机 / 台账 |

设计上最值得肯定的一点：**审批 → 台账 → 报表 → 看板是一条账，不是四套数**。`profit_service.project_profit` 被周利润表、看板毛利、MCP `fin.project_profit` 共同调用，这是三个地方数字能对上的原因。

### 3.3 报表链路

`report_service`（1199 行）统一为「类型目录 + 取数 + 渲染 + 快照」四段式：
`catalog()` 报表清单与口径说明 → `collect()` 返回 Decimal metrics → `render()` 输出 xlsx/docx/pptx → `rpt_report` 版本 +1 并存明细快照 → `sys_file` 归档到 `report/{type}/{yyyy-MM}/` → 通知推送。xlsx 强制带「口径说明」sheet。

### 3.4 MCP 链路

```
WorkBuddy → POST /mcp (Streamable HTTP, FastMCP stateless)
  └ McpAuthApp：Bearer jcmcp_* → sha256 比对 sys_user.access_token（库内不存明文）
      └ _run() 统一包装
          ├ mcp_tool 注册表热读 enabled / allowed_roles（角色码或部门码）
          ├ 限流 60 次/分 → 审计 log_mcp_audit（参数脱敏后入库）
          ├ 调 t_* 实现 → 复用 services，与页面同源
          └ 返回 {ok, summary, data, files[1h 签名链接]}
```

DNS rebinding 防护、`/mcp` 精确路由（避免 307）、无令牌 401 都写进了审计。

### 3.5 小聚 Agent 链路（全项目最复杂的一块）

```
IntegrationAssistant.vue (680 行, 挂在 MainLayout)
  └ POST /assistant/tasks + SSE /tasks/{id}/stream
      └ xiaoju_service：线程池 + 每会话文件锁 + worker lease（服务重启后识别中断任务，不重放）
          └ 独立 node 进程 agent_runtime/run.mjs（Pi SDK 1.3.0），stdin/stdout JSON 帧双向通信
              ├ 模型：DeepSeek（流式）；每次请求前回调宿主 check_session_scope 复核权限
              ├ 原生工具 read/write/bash/grep → 委托 Docker 容器（禁网 / 只读根 / UID 10001 / 1C·384M·64proc / 90s）
              └ 业务工具 → xiaoju_business
                   ├ 能力目录由 FastAPI 路由 introspect 生成（继承菜单/按钮/数据范围）
                   ├ 用 2 分钟 JWT 经 ASGITransport 调真实 HTTP 接口 → 权限 100% 复用，不另写一套
                   └ 写操作 → 确认单 action（属主 + 权限签名 + 过期 + 幂等）→ 用户点击 → 执行 → 回执
```

这套设计的核心判断是对的：**Agent 不直连数据库，而是以"一个受限用户"的身份走自己的 API**。确认单机制把"模型说完成了"和"系统真做了"分开，这是同类项目里少见的克制。

### 3.6 定时任务

APScheduler 进程内 → `pg_try_advisory_lock(crc32(任务名))` 防多 worker 重复 → `idempotency_key` 幂等 → `sync_job_log` 留痕 → 失败告警管理员。29 个任务覆盖晨报/日报/周报/月报/对账/考勤/到期预警/知识库同步。

### 3.7 部署

`deploy.sh` rsync → `/opt/jucheng` → `docker compose`（Nginx + API + PG + 备份）→ 容器内 `alembic upgrade head` → uvicorn 2 workers。`APP_REVISION` 注入 commit 号，`/health` 可核对线上版本；每日 02:30 全量备份留 7 天。

## 四、功能矩阵

| 部门 | 页面 | 能力 |
|---|---|---|
| 总经办 | 经营看板 / 大屏 / 汇报提交 | 六板块看板、晨报、对比分析、未提交催交 |
| 财务部 | 报销归集 / 流水核对 / 出纳日记账 / 项目利润 / 财务数据 / 报表中心 | 审批即归集、流水解析与智能核对、日/周/月报、税务 |
| 市场部 | 重点项目跟进 / 竞品情报 / 项目复盘 | 周月报自动成稿、进展反馈、复盘初稿 |
| 营销管控 | 合同台账 / 商机 / 业绩运营 / 数据清洗 | 审批自动登记、归档读回款、业绩统计、Excel 清洗 |
| 项目部 | 项目流程 | 标准控制流程、节点增删排序、延期标红、周进度 |
| 人事部 | 人事中心 | 考勤核对、到期预警、文书与风险提示、人力分析、招聘 |
| 产品中心 | 标书助手 | 多轮合规检查、带出处方案生成 |
| 客服部 | 客服工单 | 一句话登记、相似工单、排查建议、归因报告 |
| 公共 | 知识库 / WorkBuddy 接入 / 系统管理 | 乐享检索、MCP 令牌与工具注册、角色字典与审计 |

## 五、质量评价

### 5.1 做得好的（有证据）

1. **分层与一致性**：api 薄、service 厚、models 纯声明、integration 走适配器、jobs 走注册表。44 个 service 风格统一，靠的是 `docs/DEV_GUIDE.md` 把约定写死。这种一致性在多人（或多 Agent）协作的项目里很少见。
2. **单一事实源**：利润、报表、看板、MCP 三处共用同一取数函数；口径写进 `sys_dict` 和报表 sheet 而不是散在代码里。
3. **测试是真的**：我在本地独立库实跑 299 项全绿；用例覆盖权限矩阵、审批幂等（重复推送 3 次只登记 1 条）、回调验签、路径穿越、确认单过期与越权、跨账号会话隔离。端到端 68 项有逐条证据 JSON，不是口头宣称。
4. **安全边界想得深**：PBKDF2 260k、令牌只存 sha256、文件 1h 签名链接、MCP DNS rebinding 防护、审计脱敏（手机/身份证/密钥）、Agent 工具容器禁网 + 只读根 + 非 root + 资源限额、写操作强制人确认。
5. **可运维性**：alembic 迁移、`/health` 带 revision、任务幂等与失败告警、每日备份、降级路径（LLM 未配置走规则引擎、mock/live 可按源切换）。
6. **文档不虚**：抽查 README、DEV_GUIDE、XIAOJU_FULL_PI 与代码，描述与实现基本对得上——这在"文档即宣传"的项目里是加分项。

### 5.2 主要问题

**高（上线前必须处理）**

| # | 问题 | 位置 | 说明 |
|---|---|---|---|
| H1 | `SECRET_KEY` 有硬编码默认值，生产无启动校验 | `core/config.py:23` | 漏配环境变量时会静默用 `dev-only-secret-change-me`：JWT 可伪造、文件签名可自造、小聚个人凭证 AES 密钥可推导 → 全站失守。应在 `environment=prod` 时缺失即启动失败。 |
| H2 | 口令登录无限流/锁定，演示口令是统一值 | `api/auth.py:77` | 用户名可用姓名/手机号，配合统一口令 `Jc@2026` 可被爆破；`login-config` 在「prod + 开启演示账号」组合下会返回明文 `demo_password_hint`（`auth.py:73`）。README 也承认生产当前是 `mock + 口令登录开启`。 |
| H3 | 真实数据源未验证 | `integration/*/live.py` | 柠檬云 live 的接口路径是"按文档假设、联调时再改"（文件头自述）。mock 全绿 ≠ 能接真账套，这是上线最大不确定性。 |

**中**

- **M1 无 CI / 无 lint / 无类型检查**：没有 `.github`，没有 ruff/eslint/tsconfig。299 个测试靠人手动跑，回归全靠自觉。
- **M2 历史不可审查**：首个提交 261 文件、43,997 行，之后每个功能一大坨。无法 bisect，也无法做有意义的 code review。
- **M3 单进程假设散落**：MCP 限流是进程内 `deque`（`server.py:83`），`--workers 2` 实际限流翻倍；APScheduler 每个 worker 都起一份，靠 advisory lock 兜底但会互相刷 `skipped` 日志。
- **M4 大文件**：`report_service` 1199 行、`tender_service` 1007、`clean_service` 939；`portal.py` 用 30 个 `def _(c: Ctx)` 匿名函数注册卡片，能跑但难读。
- **M5 业务规则写进代码**：小聚的 60 行 system prompt 是 `xiaoju_service.py` 里的字符串常量，改口径要改代码发版。

**低**

- **L1 前端无测试、无 TS**；`element-plus` 与 `echarts` 全量引入，两个 chunk 各约 1MB（gzip 后合计约 700KB），未做按需加载。
- **L2 视图偏大**：30 个视图普遍 300–600 行单文件组件，逻辑与模板混在一起。
- **L3 文档轻微漂移**：`ACCEPTANCE.md` 仍写 176 项通过，实际已 299 项；`README` 与 `XIAOJU_FULL_PI` 已更新，ACCEPTANCE 未回填。
- **L4** `xiaoju_business.invoke()` 用 `asyncio.run()` 每次起事件循环（`xiaoju_business.py:201`），靠线程规避，若将来在协程栈里调用会炸。

### 5.3 结论

**评级：B+ / A-**（以"百人规模企业内部系统"为标尺）

这是一份**交付级**代码，不是演示玩具：分层克制（砍掉 Redis/Celery 换取组件数减少）、权限与数据范围收口清楚、Agent 走"受限用户 + 真 API + 人确认"的正确路线、测试和验收证据可复核。在同体量的企业内部系统里，安全边界与文档质量明显高于平均。

但当前状态准确说是「**演示环境闭环 + 真实系统未接通**」。从"能演示"到"能生产"，差的不是功能，是三道门禁：

1. **硬门禁**：缺失 `SECRET_KEY` 拒启动；口令登录加限流或直接关闭；`demo_password_hint` 在生产永不返回。
2. **真联调**：柠檬云 / 乐享 / 企微 live 三条链路各跑一遍真实数据，把 `live.py` 里的假设路径改成实测路径。
3. **工程门禁**：加一个最小 CI（pytest + vite build + ruff），把"299 项通过"从人的记忆变成流水线的红绿灯。

做完这三件，这套系统可以直接承接真实业务。

---

## 六、企业微信打通与同步核查（2026-10-23 生产实测）

> 本节为上线后补充的线上核查，结论以生产库与容器日志为准，替换第五章 H3 中"企微未验证"的笼统判断。

线上 `/health` 返回：`mode=mock`，`overrides=wecom:live`，`revision=acc21d3`。
即：**整体是演示模式，但企业微信被单独覆盖为真实模式**，柠檬云、乐享仍为 mock。

### 6.1 逐条结论

| 能力 | 状态 | 判定依据 |
|---|---|---|
| 应用凭证与 access_token | **真通** | 线上 `login-config` 返回真实 CorpID `wwf51f9c3db96dadf8`、AgentID `1000027`，非 mock 值 |
| 通讯录读取（department/list + user/list） | **真通、真同步** | 每日 06:00 任务成功：10-22 更新 43 人、10-23 更新 78 人（数量变化说明是每次实时拉取，非快照重放） |
| 应用消息推送（message/send） | **真通** | 30 条发送成功：晨报 10、报销日报 6、报表 5、HR 预警 9，最新 10-23 |
| 扫码/企微内免密登录（auth/getuserinfo） | **真通** | `log_operation` 中 `auth.login method=wecom_oauth` 15 次，最新 10-23 |
| 审批读取（oa/getapprovalinfo + getapprovaldetail） | **未打通** | 对账任务每 10 分钟失败：`企微接口错误 301055: no approval auth` |
| 审批事件回调 | **未打通** | Nginx 近 72 小时 `/api/v1/wecom/callback` 命中 **0 次** |
| 个人 CLI 绑定（员工自助扫码） | **通了，但无数据** | 2 个账号绑定成功（`scope: personal`，服务清单为官方 CLI 返回）；每 10 分钟同步，待办 0 条、日程 0 条 |

### 6.2 审批链路的准确定性

- 库里 539 张审批单全部创建于 10-21、仅 3 个模板、`dispatch_status` 全为 1，`apply_time` 最新 2026-10-21 09:00 —— 是部署时的种子数据，**此后没有任何真实审批单进来**。
- 20 份审批来源合同同样来自这批种子；真实业务中"审批通过 → 自动登记合同"这条主链路**一次都没有跑过**。
- 根因在企微后台权限，不在代码：应用 1000027 未被授予「审批」接口权限。代码侧 `live.py` 对 `getapprovalinfo` / `getapprovaldetail` 的实现和字段归一化（Text/Number/Money/Date/Selector/Contact/Table）是完整的。
- 附带代价：该任务每 10 分钟失败一次并告警，已产生 328 条 `job_failed` 通知，其中 164 条因收件人是已归档的演示账号而发送失败。

### 6.3 修复动作（均在企微管理后台，无需改代码）

1. **审批接口授权**：审批应用 → API → 「可调用接口的应用」中加入 AgentID `1000027`，并授权需要读取的审批模板；建议同时把模板 ID 写入 `sys_dict.approval_template`。
2. **回调配置**：自建应用 → 接收消息 → 设置 API 接收，URL 填 `https://juchengcc.com/api/v1/wecom/callback`，Token / EncodingAESKey 与 `app.env` 中 `WECOM_CALLBACK_TOKEN` / `WECOM_CALLBACK_AES_KEY` 完全一致；保存时企微会发 GET 校验，通过后才会推送事件。
3. **验证**：发起一张测试审批 → 观察 `sync_job_log` 中 `approval_reconcile` 转为 success、`sync_wecom_approval` 出现当日新单、合同台账自动多一条记录。
4. **降噪**：授权完成前可先把 `approval_reconcile` 的 cron 改为小时级，或临时停用，避免每 10 分钟一条失败告警。

### 6.4 小聚对企业微信个人数据的读写边界（实测）

小聚走的是**个人 CLI 授权**通道，与公司自建应用（通讯录/审批）是两套独立授权，互不替代。

| 问题 | 结论 | 依据 |
|---|---|---|
| 能读"每个人"的企微数据吗 | **不能。谁绑定读谁，管理员也读不到他人** | `wecom_cli_service.home(user_id)` 按 user_id 隔离配置目录；`_owned(user_id)` 只认调用者本人；`xiaoju_business_catalog.MODULES` 无 wecom_personal 条目，他人数据接口不进 Agent 目录 |
| 线上铺开了吗 | **没有。79 个在职账号中仅 1 个真实员工 + 1 个 admin 演示账号绑定** | `/data/xiaoju/wecom-cli/{42,120}/binding.json`；42=平台管理员(演示)，120=冯思皓(真实) |
| 绑定后能读什么 | **14 个服务、约 150 个方法** | 实测：calendar 7、sheet 66~71、smartsheet 23、meeting 9、doc 8、smartpage 7、slide 7、todo 6、disk 5、message 4、mail 3、chat 2、contact 1、identity 1 |
| 能操作（写）吗 | **能，且写操作强制出确认单** | `effect = query if 末段∈{get,list,search,whoami} else confirm`；`mail.send`/`message.send`/`doc.contents.overwrite`/`doc.members.update`/`todo.delete` 均为 confirm |
| 能搬文件吗 | **不能** | `upload/download/import/export` 在 `eligible_methods` 中被整体过滤；微盘只剩 list/get/search/rename/create-folder，只能看元数据 |
| 同步到工作台吗 | **只同步待办与日程**，每 10 分钟一次，落 `sys_wecom_item` | `wecom_sync_service.PULLERS = {todo, calendar}`；线上每次运行"同步 2 个已绑定账号，共 0 条数据" |
| 同步后能看到吗 | **看不到——工作台没有展示入口** | 前端从未调用 `/wecom-personal/sync/items`；门户"我的待办"卡片读的是 `SyncWecomApproval`（审批单），不是个人待办 |

因此准确表述是：**小聚具备"被授权者本人的"企微读写能力，能力面很完整（约 150 个方法、含发邮件/发消息/改文档/建日程），但当前只有 1 个真实员工在用，且同步回工作台的只有待办和日程两样，还没有界面承接。**

要变成"全公司可用"，缺的不是代码能力，是两件产品化的事：

1. **绑定覆盖率**：推动员工在「小聚 → 企业微信个人连接」各自扫码（企微对个人数据没有服务端推送，无法代绑）。
2. **数据承接**：在个人中心或门户加待办/日程卡片，消费 `sys_wecom_item`；否则同步了也没人看得见。

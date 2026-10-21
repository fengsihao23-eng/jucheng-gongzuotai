# 开发约定（各领域模块必读）

项目根：`/Users/hao/公司数据站`。需求：`docs/PRD.md`（从旧 demo 拷贝，章节 3~8 为口径/表结构/功能/场景）。
业务线已按公司真实业务改为：`XY 数字校园 / AF 综合智慧安防 / KP 素质教育考评 / YW 运维服务`（PRD 中 ZY/XT/RJ 一律替换）。

## 后端（backend/，Python 3.13 venv：`backend/.venv/bin/python`）
- FastAPI + SQLAlchemy 2（`Mapped[]`）+ PostgreSQL。模型全部在 `app/models/`（**只读，不要改**；如确需新字段/表，在 `app/models/ext_<domain>.py` 新建并在 `app/models/__init__.py` 末尾追加一行 import，回报给主控）。
- 路由：`app/api/<module>.py` 里已有 `router = APIRouter(prefix=...)`，直接往里加接口。已在 `app/api/__init__.py` 注册。
- 统一响应：`from app.core.response import ok, page_data, BizError, not_found, bad_request, conflict, forbidden`；返回 `ok(data)`；错误 `raise BizError(40901, "…")`。金额 Decimal 自动序列化为字符串。
- 鉴权：`from app.core.deps import Principal, get_principal, require_menu, require_button, client_ip`。
  - 页面接口用 `p: Principal = Depends(require_menu("expenses"))`；写操作再校验 `if not p.can("expense:allocate"): raise forbidden()` 或 `Depends(require_button(...))`。
  - 数据范围 `p.data_scope`（self/dept/all）；项目经理 self 只能看 `owner_id == p.id` 的项目。
  - 菜单/按钮/角色定义：`app/core/permissions.py`（ROLE_DEFS）。
- DB：`from app.core.db import get_db, get_or_404`；写完 `db.commit()`。
- 审计：人工改台账/配置/下载 → `from app.core.audit import log_op, snapshot`。
- 文件：`from app.core.storage import save_bytes, file_brief, signed_url, read_bytes, file_path`；报表 key 用 `report/{type}/{yyyy-MM}/{filename}`；返回给前端用 `file_brief(f)`（含签名 url）。
- 通知：`from app.services import notify_service`；`notify_service.send(db, users, msg_type, title, content, link="/xxx")`；`users_by_roles(db, ["CW-01"])`。企微 Mock 推送写入 outbox，可复核。
- 字典口径：`from app.services import dict_service`（`get/items/put`）。已有 dict_type：business_line、payment_target（按月份 key，值 {业务线: 元}）、opportunity_stage、approval_template、expense_category、caliber(profit/payment)、report_rule、notify_rule(morning_report/daily_expense)、alert_rule(hr)、milestone_template(standard/software)。新增口径用新 dict_type，并在自己的 seed 里写入。
- 时间：`from app.core.timeutil import today, now, week_no, week_range, last_week, month_range, prev_month, day_bounds`。**不要用 date.today()**（测试固定 FAKE_TODAY=2026-10-20）。
- 编码：`from app.services import codes`（project_code/contract_no/opp_no/ticket_no）。
- 定时任务：`app/jobs/<domain>.py` 里用 `@job(name, title, cron_kwargs, schedule_text, idempotency=lambda p: ...)` 注册，函数签名 `(db, params) -> {"rows": n, "message": str}`。PRD 6.5 的任务名必须一致。
- Excel/Word/PPT：openpyxl、python-docx、python-pptx 已安装。报表 xlsx 必须含「口径说明」sheet。
- 不要新装依赖；如必须，回报主控。
- 种子：领域演示数据写在 `scripts/seed_<domain>.py` 的 `seed(db, ctx)`（ctx.users[userid] / ctx.projects[code] / ctx.contracts / ctx.T=今天）。核心种子 `scripts/seed_core.py` 只读。
- 测试：`tests/test_<domain>.py`，用 conftest 夹具 `client, login, db` 与 `data(resp)`。**运行时必须设独立库**：
  `cd backend && TEST_DB=jucheng_test_<domain> .venv/bin/python -m pytest tests/test_<domain>.py -q`
  测试共享一次 seed，断言写成“相对增量”或针对本测试新建的数据，避免依赖执行顺序。每条 PRD 验收标准尽量有对应用例。

## 前端（frontend/，Vue 3 + Element Plus + ECharts）
- 页面在 `src/views/<Name>.vue`（占位已建好、路由已配好，见 `src/router/index.js`）。不要改 router/menu/layout/theme，除非任务明确要求。
- API：`import api from '@/api/client'`（`api.get/post/put/del/upload(url,file,fields)/download(url,params)`），返回已解包的 data；错误自动 toast。
- 权限：`import { useAuth } from '@/store/auth'`；`auth.can('flows:import')` 控制按钮显隐。
- 格式化：`@/utils/format`（money、wan、wanNum、percent、date、datetime、BUSINESS_LINES、各类状态字典）。
- 全局组件（无需 import）：`<PageHeader eyebrow title desc>`（右侧插槽放按钮）、`<Panel title sub :padding="false" :loading>`（#extra 插槽）、`<Kpi label value unit hint :delta :progress to>`、`<Chart :option height :empty @click>`、`<StatusPill text tone="up|down|warn|info|brand" dot>`、`<FileLink :file>`、`<UploadBox accept @change>`。
- 布局类：`.jc-page`（页面根）、`.jc-grid.c2/c3/c4/c5`、`.jc-row(.between)`、`.jc-stack`、`.mt-16`、`.jc-muted`、`.jc-num`（数字）、`.jc-serif`（标题）、`.jc-small`。
- 图表：`import { PALETTE, baseGrid, wanAxis } from '@/utils/chart'`，主题 'jc' 已在 Chart 组件内注册。
- **视觉要求**：暖白纸面、墨色、品牌藏青 #1B3275 为唯一强调色；禁止蓝紫渐变/霓虹/发光/玻璃拟态/emoji。信息密度适中，留白、细线分隔。表格延期/风险行用 `row-class-name` 返回 `is-alert`。
- 自检：`cd frontend && VITE_OUT_DIR=/tmp/jc_build_<domain> npx vite build` 必须通过。不要启动 dev server（端口由主控统一管理）。

## 交付要求
完成后回报：新增/修改的文件、实现的 PRD 条款、测试用例数与结果、已知限制。不要 git commit。

## 已提供的共享服务（直接调用，不要重写）
- `app.services.payment_service`：`register_payment(db, contract, amount, date, flow=?, source_type=1|2|3, user_id=?)` 冲抵回款计划、标记流水、写日记账；`refresh_plan_status`、`plan_rows`、`contract_received`。
- `app.services.profit_service.project_profit(db, start, end, project_codes=None)`：项目收入/成本/毛利（累计至期末 + 本期发生）+ 公共费用，口径来自 sys_dict caliber.profit。周利润表、看板毛利、MCP fin.project_profit 必须共用它，保证三处数字一致。
- `app.services.dispatch_service`：企微审批 → 台账分发（已完成，勿改）。
- `app.services.notify_service` / `dict_service` / `codes` / `user_service`。
- 知识库检索接口（由 knowledge 模块实现，其他模块可调用）：`app.services.kb_service.search(db, query, category=None, limit=5) -> list[dict]`，每项 `{doc_id, title, category, url, heading, snippet, score}`；url 为乐享原文链接（出处）。调用方请 `try/except ImportError` 兜底。
- 大模型（由 knowledge 模块实现）：`app.integration.llm.complete(system, user) -> str | None`，未配置 LLM_API_KEY 时返回 None，调用方必须有规则引擎兜底。
- 样例文件（银行流水、考勤、脏 Excel、招标文件等）放 `backend/samples/`，用于测试与验收演示。

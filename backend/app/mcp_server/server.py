"""MCP Server（FR-10）：Streamable HTTP（/mcp）+ 个人 Access Token 鉴权 + 角色校验 + 热配置 + 审计 + 限流。

- 鉴权：Authorization: Bearer <jcmcp_...>，库内只存 sha256；无/错/停用账号令牌 → HTTP 401 并审计（unauthorized）
- 工具注册表 mcp_tool：enabled / allowed_roles（角色码或部门码，["*"] 全员）每次调用实时读取，热生效
- 越权 → 结构化错误 + 审计（denied）；单用户 60 次/分钟 → 友好错误（rate_limited）
- 产出文件以 1h 签名链接返回
- 每个工具的实现复用工作台服务层，保证与页面/报表数字一致
"""
import json
import logging
import time
from collections import defaultdict, deque
from datetime import date
from typing import Any

import anyio
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.server import StreamableHTTPASGIApp
from sqlalchemy import select

from app.core.audit import mask
from app.core.config import settings
from app.core.db import SessionLocal
from app.core.deps import build_principal
from app.core.response import BizError, encode
from app.core.security import hash_token
from app.core.timeutil import utcnow
from app.models import LogMcpAudit, McpTool, SysUser

log = logging.getLogger("mcp")

INSTRUCTIONS = (
    "你正在使用「聚成数智统一工作台」。工具返回结构化 JSON：ok=true 时 data 为结果、files 为报表下载链接（1 小时有效），"
    "summary 为可直接转述给用户的中文摘要；ok=false 时 message 说明原因（如权限不足、参数缺失），请据此向用户澄清。"
    "金额单位为元；日期格式 YYYY-MM-DD；周格式 2026-W42；月份格式 2026-10。"
)

def _transport_security():
    """DNS 重绑定防护：允许的 Host 来自 PUBLIC_BASE_URL + 本机；MCP_ALLOWED_HOSTS 可追加（逗号分隔，支持 host:*）。"""
    from urllib.parse import urlparse

    from mcp.server.transport_security import TransportSecuritySettings
    host = urlparse(settings.public_base_url).hostname or "localhost"
    hosts = {host, f"{host}:*", "localhost", "localhost:*", "127.0.0.1", "127.0.0.1:*", "testserver", "selftest",
             *filter(None, (h.strip() for h in settings.mcp_allowed_hosts.split(",")))}
    return TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=sorted(hosts),
                                     allowed_origins=[settings.public_base_url.rstrip("/")])


mcp_server = FastMCP(name=settings.mcp_server_name, instructions=INSTRUCTIONS, stateless_http=True,
                     json_response=True, streamable_http_path="/", transport_security=_transport_security())

# ---- 工具元数据（种子写入 mcp_tool；注册表可在线调整） ----
TOOL_META: dict[str, dict] = {
    "ops.task_create": {"name": "运营月度任务安排", "roles": ["*"],
                        "desc": "按当前账号权限布置公司任务或提交本人计划，自动归入负责人月度表；本人计划必须经过部门和公司审核。"},
    "ops.task_review": {"name": "运营任务审核", "roles": ["*"],
                        "desc": "仅指定审核人可按部门、公司顺序审核最新任务版本，保留审核意见，禁止自审。"},
    "ops.project_status": {"name": "运营项目进展", "roles": ["*"],
                           "desc": "查询本人有权访问的运营项目、当前待办、负责人和节点反馈。"},
    "ops.task_feedback": {"name": "运营任务反馈", "roles": ["*"],
                          "desc": "对本人负责的运营任务提交简短进展，沿用项目成员权限并保留记录。"},
    "fin.daily_expense_report": {"name": "每日支出报表", "roles": ["CW", "ZB", "ADMIN"],
                                 "desc": "查询某日（默认昨日）或某项目的支出明细，生成每日支出报表文件并返回摘要。"},
    "fin.project_profit": {"name": "项目利润表", "roles": ["CW", "ZB", "ADMIN"],
                           "desc": "生成某周（默认上周）各项目利润表：收入、归集成本、毛利、毛利率，返回文件与摘要。"},
    "fin.bank_flow_sync": {"name": "银行流水同步核对", "roles": ["CW", "ADMIN"],
                           "desc": "解析最新银行流水文件（收件目录）并更新出纳日记账，返回数据核对报告（新增/重复/待核对与匹配建议）。"},
    "oa.pending_approvals": {"name": "我的审批", "roles": ["*"],
                             "desc": "查询我提交的企微审批单（submitted）或待处理事项（todo：审批中/待归集/自动登记失败），返回单号、事项、状态、金额。"},
    "crm.contract_register": {"name": "合同自动登记", "roles": ["YX", "ZB", "ADMIN"],
                              "desc": "读取审批通过的销售合同登记到合同台账（可指定审批单号，默认自动发现未登记的），返回登记结果与当月/年度业绩。"},
    "board.executive_daily": {"name": "经营晨报", "roles": ["ZB-01", "ZB-02", "ADMIN"],
                              "desc": "汇聚经营看板六板块指标生成经营晨报（文字 + 看板链接）。"},
    "board.compare": {"name": "经营对比分析", "roles": ["ZB-01", "ZB-02", "ADMIN"],
                      "desc": "对比两个月份的经营指标（回款完成率/回款/签约/商机），返回两期数值、变化与结论，用于『本月与上月回款完成率对比』。"},
    "kb.search": {"name": "知识库检索", "roles": ["*"],
                  "desc": "检索腾讯乐享企业知识库（制度/售前/方案模板/案例/资质/客服手册），返回片段与出处链接。"},
    "kb.bid_check": {"name": "标书合规检查", "roles": ["CP", "ADMIN"],
                     "desc": "对招标需求书与投标文件做合规性排查（资质/技术参数/商务/格式/★实质性条款），返回逐项整改清单与依据出处。"},
    "hr.attendance_summary": {"name": "考勤汇总", "roles": ["RS", "ADMIN"],
                              "desc": "读取某月考勤数据核对异常（迟到/早退/缺卡/旷工/请假/加班），返回考勤汇总表与人力分析摘要。"},
    "proj.milestone_update": {"name": "项目节点", "roles": ["XM", "ZB", "SC", "ADMIN"],
                              "desc": "查询或更新项目标准控制流程节点（深化设计/进度/采购/到货验收/终验），如『更新 XX 项目深化设计为已完成』。"},
    "cs.ticket_register": {"name": "客服工单登记", "roles": ["KF", "ADMIN"],
                           "desc": "一句话登记客户问题，自动分类、去重、关联相似历史工单并给出排查建议（含知识库出处）。"},
    "market.weekly_report": {"name": "重点项目周报", "roles": ["SC", "ZB", "ADMIN"],
                             "desc": "汇总本周（或指定周/月）重点项目周报：项目进度、合同与回款动态、风险，返回汇总稿与 docx 文件。"},
}

_calls: dict[int, deque] = defaultdict(deque)


class ToolDenied(Exception):
    def __init__(self, outcome: str, message: str):
        super().__init__(message)
        self.outcome = outcome


def _rate_ok(user_id: int) -> bool:
    q, now_ = _calls[user_id], time.monotonic()
    while q and now_ - q[0] > 60:
        q.popleft()
    if len(q) >= settings.mcp_rate_limit_per_min:
        return False
    q.append(now_)
    return True


def _audit(db, user_id, tool, params, outcome, summary, cost_ms, ip):
    db.add(LogMcpAudit(user_id=user_id, tool_code=tool, params=mask(encode(params)), outcome=outcome,
                       result_summary=(summary or "")[:2000], cost_ms=cost_ms, client_ip=ip, called_at=utcnow()))
    db.commit()


_tool_db = __import__("contextvars").ContextVar("mcp_tool_db", default=None)


def _files(*files) -> list[dict]:
    from app.core.storage import file_brief
    from app.models import SysFile
    out = []
    db = _tool_db.get()
    own = db is None
    db = db or SessionLocal()
    try:
        for f in files:
            if isinstance(f, int):
                f = db.get(SysFile, f)
            if f is not None:
                b = file_brief(f, absolute=True)
                out.append({"filename": b["filename"], "url": b["url"], "expires_in": settings.file_link_ttl_seconds})
    finally:
        if own:
            db.close()
    return out


def _run(tool: str, ctx: Context, params: dict, impl) -> str:
    """统一包装：鉴权上下文 → 注册表/角色/限流校验 → 执行 → 审计 → JSON 结果。"""
    t0 = time.perf_counter()
    req = ctx.request_context.request
    uid = getattr(req.state, "mcp_user_id", None) if req is not None else None
    ip = req.client.host if req is not None and req.client else None
    db = SessionLocal()
    try:
        user = db.get(SysUser, uid) if uid else None
        if not user:
            _audit(db, None, tool, params, "unauthorized", "无有效令牌", 0, ip)
            return json.dumps({"ok": False, "message": "未认证：请在工作台「WorkBuddy 接入」页获取个人 Access Token"}, ensure_ascii=False)
        p = build_principal(user)
        reg = db.scalar(select(McpTool).where(McpTool.tool_code == tool))
        if reg is None or not reg.enabled:
            raise ToolDenied("denied", f"工具 {tool} 已停用，请联系平台管理员")
        allowed = set(reg.allowed_roles or [])
        if "*" not in allowed and not p.is_admin and not (allowed & set(p.role_codes)) and p.dept_code not in allowed:
            raise ToolDenied("denied", f"你的角色（{'/'.join(r.name for r in user.roles)}）无权调用「{reg.name}」")
        if not _rate_ok(user.id):
            raise ToolDenied("rate_limited", f"调用过于频繁（上限 {settings.mcp_rate_limit_per_min} 次/分钟），请稍后再试")
        token = _tool_db.set(db)
        try:
            result = impl(db, p, **params)
        finally:
            _tool_db.reset(token)
        db.commit()
        result.setdefault("ok", True)
        _audit(db, user.id, tool, params, "ok", result.get("summary"), round((time.perf_counter() - t0) * 1000), ip)
        return json.dumps(encode(result), ensure_ascii=False)
    except ToolDenied as e:
        db.rollback()
        _audit(db, uid, tool, params, e.outcome, str(e), round((time.perf_counter() - t0) * 1000), ip)
        return json.dumps({"ok": False, "message": str(e)}, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        db.rollback()
        log.exception("mcp tool %s failed", tool)
        msg = getattr(e, "message", None) or str(e)
        _audit(db, uid, tool, params, "error", msg, round((time.perf_counter() - t0) * 1000), ip)
        return json.dumps({"ok": False, "message": f"执行失败：{msg}"}, ensure_ascii=False)
    finally:
        db.close()


# =========================== 工具实现 ===========================
def _d(s: str | None, default: date | None = None) -> date | None:
    return date.fromisoformat(s) if s else default


def t_daily_expense(db, p, date: str | None = None, project_code: str | None = None):
    from datetime import timedelta

    from app.core.timeutil import today
    from app.services import report_service
    d = _d(date, today() - timedelta(days=1))
    rpt = report_service.generate(db, "daily_expense", d.isoformat(), trigger="mcp", user_id=p.id)
    m = rpt.metrics or {}
    count = m.get("expense_count", 0) + m.get("flow_count", 0)
    data = {"date": d, "total": m.get("total"), "count": count, "expense_total": m.get("expense_total"),
            "flow_total": m.get("flow_total"), "by_dept": m.get("by_dept"),
            "by_project": m.get("by_project")}
    if project_code:
        data["project"] = next((x for x in (m.get("by_project") or []) if x.get("project_code") == project_code), None)
        data["items"] = [x for x in (m.get("items") or []) if x.get("project_code") == project_code]
    summary = (f"{d} 支出合计 ¥{float(m.get('total') or 0):,.2f}（报销 {m.get('expense_count', 0)} 笔 "
               f"¥{float(m.get('expense_total') or 0):,.2f}，公户及项目成本 {m.get('flow_count', 0)} 笔 ¥{float(m.get('flow_total') or 0):,.2f}）")
    if project_code:
        pj = data.get("project") or {}
        summary += f"；其中项目 {project_code} ¥{float(pj.get('amount') or 0):,.2f}"
    return {"summary": summary, "data": data, "files": _files(*(rpt.file_ids or []))}


def t_project_profit(db, p, week: str | None = None):
    from app.core.timeutil import last_week
    from app.services import report_service
    wk = week or last_week()
    rpt = report_service.generate(db, "weekly_profit", wk, trigger="mcp", user_id=p.id)
    m = rpt.metrics or {}
    t = m.get("totals") or {}
    gm = t.get("gross_margin")
    summary = (f"{wk} 项目利润表：{len(m.get('rows') or [])} 个项目，累计收入 ¥{float(t.get('income') or 0):,.0f}，"
               f"成本 ¥{float(t.get('cost') or 0):,.0f}，毛利 ¥{float(t.get('gross_profit') or 0):,.0f}"
               f"（毛利率 {float(gm) * 100:.1f}%）" if gm is not None else f"{wk} 项目利润表已生成")
    return {"summary": summary, "data": m, "files": _files(*(rpt.file_ids or []))}


def t_bank_flow_sync(db, p, file_date: str | None = None):
    from sqlalchemy import func

    from app.integration.bankflow import parser
    from app.models import FinBankFlow
    res = parser.auto_parse_dir(db) or {}
    db.commit()
    imports = [x for x in (res.get("reports") or []) if hasattr(x, "filename")] if isinstance(res, dict) else list(res)
    q = select(FinBankFlow).where(FinBankFlow.match_status == 0)
    if file_date:
        q = q.where(FinBankFlow.trade_date == _d(file_date))
    pending = db.scalars(q.order_by(FinBankFlow.trade_date.desc())).all()
    suggestions = []
    try:
        from app.services import reconcile_service
        for f in pending[:20]:
            s = reconcile_service.suggest(db, f)
            if s:
                suggestions.append({"flow_id": f.id, "amount": f.amount, "counterparty": f.counterparty, "best": s[0]})
    except (ImportError, AttributeError):
        pass
    rep = [{"filename": i.filename, "total": i.total_rows, "inserted": i.inserted_rows, "duplicate": i.duplicate_rows,
            "errors": i.error_rows} for i in imports]
    if isinstance(res, dict):
        for r in res.get("reports") or []:
            if isinstance(r, dict):
                rep.append({"filename": r.get("file"), "total": r.get("inserted", 0) + r.get("duplicate", 0) + r.get("errors", 0),
                            "inserted": r.get("inserted", 0), "duplicate": r.get("duplicate", 0), "errors": r.get("errors", 0)})
    total_in = sum(f.amount for f in pending if f.direction == 1)
    total_out = sum(f.amount for f in pending if f.direction == 2)
    journal_today = db.scalar(select(func.count()).select_from(FinBankFlow).where(FinBankFlow.match_status != 0,
                                                                                    FinBankFlow.matched_at.is_not(None)))
    summary = (f"本次解析文件 {len(rep)} 个（新增 {sum(r['inserted'] for r in rep)} 条、重复 {sum(r['duplicate'] for r in rep)} 条）；"
               f"当前待核对流水 {len(pending)} 条（收 ¥{total_in:,.2f} / 支 ¥{total_out:,.2f}），"
               f"其中 {len(suggestions)} 条有匹配建议，请在工作台「流水核对」确认后自动记入日记账。")
    return {"summary": summary, "data": {"imports": rep, "pending": [
        {"id": f.id, "date": f.trade_date, "direction": "收" if f.direction == 1 else "支", "amount": f.amount,
         "counterparty": f.counterparty, "summary": f.summary} for f in pending[:50]], "suggestions": suggestions,
        "matched_total": journal_today, "workbench": settings.public_base_url + "/flows"}}


def t_pending_approvals(db, p, type: str = "submitted", template: str | None = None):
    from app.models import BizExpenseReport, SyncWecomApproval
    q = select(SyncWecomApproval).where(SyncWecomApproval.applicant_userid == p.user.wecom_userid)
    if template:
        q = q.where(SyncWecomApproval.template_key == template)
    if type == "todo":
        q = q.where((SyncWecomApproval.status == 1) | (SyncWecomApproval.dispatch_status == 2))
    rows = db.scalars(q.order_by(SyncWecomApproval.apply_time.desc()).limit(30)).all()
    st = {1: "审批中", 2: "已通过", 3: "已驳回", 4: "已撤销"}
    ds = {0: "待处理", 1: "已登记", 2: "登记失败待人工", 3: "无需登记"}
    items = []
    for r in rows:
        f = (r.detail or {}).get("fields", {})
        amount = f.get("合同金额") or f.get("报销金额") or f.get("预计金额")
        items.append({"sp_no": r.sp_no, "type": r.sp_name, "status": st.get(r.status), "register": ds.get(r.dispatch_status),
                      "target": r.dispatch_target, "amount": amount, "apply_time": r.apply_time,
                      "subject": f.get("合同名称") or f.get("报销事由") or f.get("商机名称")})
    pending_alloc = []
    if type == "todo":
        pending_alloc = [{"approval_no": e.approval_no, "amount": e.amount, "category": e.category,
                          "note": "所属项目无法匹配，待财务补充归集"}
                         for e in db.scalars(select(BizExpenseReport).where(BizExpenseReport.applicant_id == p.id,
                                                                            BizExpenseReport.status == 2))]
    summary = f"共 {len(items)} 张审批单" + (f"，{len(pending_alloc)} 张报销待归集" if pending_alloc else "")
    return {"summary": summary, "data": {"items": items, "pending_allocation": pending_alloc}}


def t_contract_register(db, p, approval_no: str | None = None):
    from app.core.timeutil import today
    from app.models import BizContract, SyncWecomApproval
    from app.services import dispatch_service
    before = set(db.scalars(select(BizContract.contract_no)).all())
    if approval_no:
        rec = dispatch_service.manual_retry(db, approval_no)
        results = [{"sp_no": rec.sp_no, "status": rec.dispatch_status, "contract_no": rec.dispatch_target, "error": rec.error_msg}]
    else:
        dispatch_service.reconcile(db, hours=72)
        db.commit()
        results = [{"sp_no": r.sp_no, "status": r.dispatch_status, "contract_no": r.dispatch_target, "error": r.error_msg}
                   for r in db.scalars(select(SyncWecomApproval).where(SyncWecomApproval.template_key == "contract",
                                                                       SyncWecomApproval.dispatch_status == 2))]
    new = [c for c in db.scalars(select(BizContract).where(BizContract.contract_no.not_in(before)))] if before else []
    T = today()
    from sqlalchemy import extract, func
    month = db.scalar(select(func.coalesce(func.sum(BizContract.amount), 0)).where(
        BizContract.is_deleted.is_(False), extract("year", BizContract.sign_date) == T.year,
        extract("month", BizContract.sign_date) == T.month))
    year = db.scalar(select(func.coalesce(func.sum(BizContract.amount), 0)).where(
        BizContract.is_deleted.is_(False), extract("year", BizContract.sign_date) == T.year))
    summary = (f"新登记合同 {len(new)} 份" + ("：" + "、".join(f"{c.contract_no}（{c.customer_name} ¥{c.amount:,.0f}）" for c in new) if new else "")
               + f"；当月签约 ¥{month:,.0f}，年度累计 ¥{year:,.0f}")
    fails = [r for r in results if r["status"] == 2]
    if fails:
        summary += f"；{len(fails)} 张登记失败待人工处理：" + "；".join(f"{r['sp_no']} {r['error']}" for r in fails[:3])
    return {"summary": summary, "data": {"registered": [{"contract_no": c.contract_no, "customer": c.customer_name,
                                                         "amount": c.amount, "sign_date": c.sign_date,
                                                         "approval_no": c.source_approval_no} for c in new],
                                         "results": results, "month_sign": month, "year_sign": year}}


def t_executive_daily(db, p, date: str | None = None):
    from app.core.timeutil import today
    from app.services import board_service
    d = _d(date, today())
    r = board_service.build_morning_report(db, d)
    return {"summary": r.get("text_markdown"), "data": r.get("summary"), "board_url": settings.public_base_url + "/board",
            "files": _files(r["image_file"]) if r.get("image_file") else []}


def t_compare(db, p, metric: str = "payment_rate", a: str | None = None, b: str | None = None):
    from app.core.timeutil import month_of, prev_month, today
    from app.services import board_service
    a = a or month_of(today())
    b = b or prev_month(a)
    r = board_service.compare(db, metric, a, b)
    return {"summary": r.get("conclusion"), "data": r}


def t_kb_search(db, p, query: str, category: str | None = None):
    from app.services import kb_service
    hits = kb_service.search(db, query, category=category, limit=6, viewer=p)
    summary = f"找到 {len(hits)} 条相关内容" + ("：" + "；".join(f"《{h['title']}》" for h in hits[:3]) if hits else "，可换个关键词再试")
    return {"summary": summary, "data": [{**h, "snippet": h["snippet"].replace("<mark>", "").replace("</mark>", "")} for h in hits]}


def t_bid_check(db, p, file_url: str | None = None, requirement_url: str | None = None,
                bid_doc_id: int | None = None, tender_doc_id: int | None = None):
    """file_url / requirement_url 可传工作台文档 id、标题关键字或文件链接。"""
    from app.models import ProdTenderDoc
    from app.services import tender_service

    def resolve(ref, kind):
        if ref is None:
            return None
        s = str(ref)
        if s.isdigit():
            return int(s)
        if "files/" in s:
            fid = int(s.split("files/")[1].split("/")[0])
            doc = db.scalar(select(ProdTenderDoc).where(ProdTenderDoc.file_id == fid))
            return doc.id if doc else None
        doc = db.scalar(select(ProdTenderDoc).where(ProdTenderDoc.doc_type == kind, ProdTenderDoc.title.contains(s))
                        .order_by(ProdTenderDoc.id.desc()))
        return doc.id if doc else None

    tid = tender_doc_id or resolve(requirement_url, "tender")
    bid = bid_doc_id or resolve(file_url, "bid")
    if not tid or not bid:
        docs = db.scalars(select(ProdTenderDoc).order_by(ProdTenderDoc.id.desc()).limit(10)).all()
        return {"ok": False, "message": "请指定需求书与投标文件（可用文档 id 或标题关键字）。最近上传的文档：" +
                "；".join(f"[{d.id}]{'需求书' if d.doc_type == 'tender' else '投标文件' if d.doc_type == 'bid' else '资料'}《{d.title}》" for d in docs)}
    r = tender_service.bid_check(db, tid, bid)
    findings = r.get("findings", [])
    high = [f for f in findings if f.get("severity") == "高"]
    summary = f"共发现 {len(findings)} 项偏离/风险，其中高风险 {len(high)} 项" + (
        "：" + "；".join(f"{f.get('clause_no', '')} {f.get('deviation', '')}" for f in high[:5]) if high else "")
    files = _files(r["report_file_id"]) if r.get("report_file_id") else []
    return {"summary": summary, "data": r, "files": files}


def t_attendance(db, p, period: str | None = None):
    from app.core.timeutil import month_of, prev_month, today
    from app.services import hr_service
    period = period or prev_month(month_of(today()))
    r = hr_service.attendance_summary(db, period)
    f = r.get("file")
    return {"summary": r.get("analysis"), "data": {k: v for k, v in r.items() if k not in ("file",)},
            "files": _files(f["id"] if isinstance(f, dict) else f) if f else []}


def t_milestone(db, p, project_code: str, node_name: str | None = None, action: str = "query",
                status: str | int | None = None, finish_date: str | None = None, note: str | None = None):
    from app.services import project_service
    r = project_service.update_milestone(db, project_code=project_code, node_name=node_name, action=action,
                                         status=status, finish_date=_d(finish_date) if finish_date else None, note=note,
                                         operator=p, source="mcp")
    db.commit()
    return {"summary": r.get("summary") or r.get("message") or "已完成", "data": r}


def t_ticket(db, p, text: str | None = None, customer_name: str | None = None, problem: str | None = None,
             channel: str | None = None, device: str | None = None):
    from app.services import cs_service
    payload = text or {"customer_name": customer_name, "problem_desc": problem, "channel": channel or "企微", "device": device}
    r = cs_service.register_ticket(db, payload, p.user)
    db.commit()
    return {"summary": r.get("summary") or f"已登记工单 {r.get('ticket_no')}", "data": r}


def t_market_weekly(db, p, period_type: str = "weekly", period: str | None = None):
    from app.core.timeutil import month_of, today, week_no
    from app.services import market_service
    period = period or (week_no(today()) if period_type == "weekly" else month_of(today()))
    r = market_service.build_market_report(db, period_type, period)
    f = r.get("file") or r.get("file_id")
    return {"summary": r.get("text") or r.get("summary"), "data": {k: v for k, v in r.items() if k not in ("file",)},
            "files": _files(f["id"] if isinstance(f, dict) else f) if f else []}


# ---- 注册（显式参数签名，供 WorkBuddy 生成调用参数） ----
def t_ops_status(db, p, project_id: int | None = None, task_id: int | None = None,
                 month: str | None = None, owner_id: int | None = None):
    from app.services import operations_service as ops
    if month:
        from app.services.operations_plans import monthly
        result = monthly(db, p, month, owner_id, project_id)
        stats = result["stats"]
        return {"data": result, "summary": f"{month} 月度任务：已生效 {stats['total']} 项，已完成 {stats['done']} 项（按时 {stats['on_time']}，逾期完成 {stats['late_done']}），未完成 {stats['pending']} 项。"}
    if task_id:
        result = ops.task_detail(db, p, task_id)
        return {"data": result, "summary": f"{result['title']}：{result['owner_name']}，{ops.STATUS_LABELS[result['status']]} {result['progress']}%"}
    if project_id:
        result = ops.brief(db, p, project_id)
        return {"data": result, "summary": result["text"]}
    result = ops.list_projects(db, p)
    return {"data": result, "summary": f"可访问 {len(result['items'])} 个运营项目，当前待办 {len(result['todos'])} 项。"}


def t_ops_feedback(db, p, task_id: int, content: str, status: str, progress: int | None = None,
                   next_step: str = "", completed_node_ids: list[int] | None = None, incomplete_reason: str = ""):
    from app.schemas.operations import FeedbackIn
    from app.services import operations_service as ops
    body = FeedbackIn(content=content, status=status, progress=progress, next_step=next_step,
                      completed_node_ids=completed_node_ids or [], incomplete_reason=incomplete_reason)
    ops.feedback(db, p, task_id, body)
    db.commit()
    return {"data": ops.task_detail(db, p, task_id), "summary": "进展反馈已保存。"}


def t_ops_create(db, p, project_id: int, title: str, owner_id: int, due_date: str,
                 source: str = "company", plan_month: str | None = None, description: str = "",
                 cadence: str = "daily", department_reviewer_id: int | None = None,
                 company_reviewer_id: int | None = None, nodes: list[dict] | None = None):
    from app.schemas.operations import TaskIn
    from app.services import operations_service as ops
    body = TaskIn(title=title, owner_id=owner_id, due_date=due_date, source=source, plan_month=plan_month,
                  description=description, cadence=cadence, department_reviewer_id=department_reviewer_id,
                  company_reviewer_id=company_reviewer_id, nodes=nodes or [])
    task = ops.create_task(db, p, project_id, body)
    db.commit()
    return {"data": ops.task_detail(db, p, task.id), "summary": "公司任务已发布并进入月度表。" if source == "company" else "本人任务已提交，等待部门审核。"}


def t_ops_review(db, p, task_id: int, decision: str, comment: str, expected_revision: int):
    from app.schemas.operations import ReviewIn
    from app.services import operations_plans as plans, operations_service as ops
    task = plans.review_task(db, p, task_id, ReviewIn(decision=decision, comment=comment, expected_revision=expected_revision))
    db.commit()
    return {"data": ops.task_detail(db, p, task.id), "summary": "本级审核结果已保存，请查看当前审核状态。"}


def _register():
    async def ops_task_create(ctx: Context, project_id: int, title: str, owner_id: int, due_date: str,
                              source: str = "company", plan_month: str | None = None, description: str = "",
                              cadence: str = "daily", department_reviewer_id: int | None = None,
                              company_reviewer_id: int | None = None, nodes: list[dict] | None = None) -> str:
        """用户明确要求安排任务后执行。先查询项目、团队与真实ID。source=company|self，due_date=YYYY-MM-DD，plan_month=YYYY-MM，cadence=daily|node；self需两位不同审核人。节点格式{name,due_date}。"""
        params = {"project_id": project_id, "title": title, "owner_id": owner_id, "due_date": due_date, "source": source,
                  "plan_month": plan_month, "description": description, "cadence": cadence,
                  "department_reviewer_id": department_reviewer_id, "company_reviewer_id": company_reviewer_id, "nodes": nodes}
        return await anyio.to_thread.run_sync(lambda: _run("ops.task_create", ctx, params, t_ops_create))

    async def ops_task_review(ctx: Context, task_id: int, decision: str, comment: str, expected_revision: int) -> str:
        """仅在当前用户明确要求审核时执行。先读task确认内容和revision。decision=approve|reject；必须提供审核意见及expected_revision，不能跳过审批或替别人批准。"""
        return await anyio.to_thread.run_sync(lambda: _run("ops.task_review", ctx, {"task_id": task_id, "decision": decision,
            "comment": comment, "expected_revision": expected_revision}, t_ops_review))
    async def ops_project_status(ctx: Context, project_id: int | None = None, task_id: int | None = None,
                                 month: str | None = None, owner_id: int | None = None) -> str:
        """不传 ID 时列出项目与待办；project_id 汇总项目；task_id 读取任务记录；month=YYYY-MM 读取月度任务，可按owner_id筛选。先查询取得 ID，不能猜测。"""
        return await anyio.to_thread.run_sync(lambda: _run("ops.project_status", ctx, {"project_id": project_id, "task_id": task_id, "month": month, "owner_id": owner_id}, t_ops_status))

    async def ops_task_feedback(ctx: Context, task_id: int, content: str, status: str, progress: int | None = None,
                                next_step: str = "", completed_node_ids: list[int] | None = None, incomplete_reason: str = "") -> str:
        """仅在用户要求汇报时提交。先查询任务确认 ID、审核状态和节点；status: todo/doing/blocked/done。progress 可省略，按已完成节点更新。受阻或逾期未完成须提供 incomplete_reason。不得推断完成节点。"""
        return await anyio.to_thread.run_sync(lambda: _run("ops.task_feedback", ctx, {"task_id": task_id, "content": content,
            "status": status, "progress": progress, "next_step": next_step, "completed_node_ids": completed_node_ids, "incomplete_reason": incomplete_reason}, t_ops_feedback))

    async def fin_daily_expense_report(ctx: Context, date: str | None = None, project_code: str | None = None) -> str:
        """每日支出报表。date: YYYY-MM-DD（默认昨日）；project_code: 项目编码（可选，如 JC-XY-2026-001）"""
        return await anyio.to_thread.run_sync(lambda: _run("fin.daily_expense_report", ctx, {"date": date, "project_code": project_code}, t_daily_expense))

    async def fin_project_profit(ctx: Context, week: str | None = None) -> str:
        """每周项目利润表。week: ISO 周如 2026-W42（默认上周）"""
        return await anyio.to_thread.run_sync(lambda: _run("fin.project_profit", ctx, {"week": week}, t_project_profit))

    async def fin_bank_flow_sync(ctx: Context, file_date: str | None = None) -> str:
        """解析收件目录中的最新银行流水文件并返回核对报告。file_date: 只看某日的待核对流水（可选）"""
        return await anyio.to_thread.run_sync(lambda: _run("fin.bank_flow_sync", ctx, {"file_date": file_date}, t_bank_flow_sync))

    async def oa_pending_approvals(ctx: Context, type: str = "submitted", template: str | None = None) -> str:
        """我的审批。type: submitted（我提交的）| todo（待处理）；template: contract|expense|opportunity（可选）"""
        return await anyio.to_thread.run_sync(lambda: _run("oa.pending_approvals", ctx, {"type": type, "template": template}, t_pending_approvals))

    async def crm_contract_register(ctx: Context, approval_no: str | None = None) -> str:
        """合同登记。approval_no: 企微审批单号（可选，默认自动发现审批通过但未登记的合同）"""
        return await anyio.to_thread.run_sync(lambda: _run("crm.contract_register", ctx, {"approval_no": approval_no}, t_contract_register))

    async def board_executive_daily(ctx: Context, date: str | None = None) -> str:
        """经营晨报。date: YYYY-MM-DD（默认今日）"""
        return await anyio.to_thread.run_sync(lambda: _run("board.executive_daily", ctx, {"date": date}, t_executive_daily))

    async def board_compare(ctx: Context, metric: str = "payment_rate", a: str | None = None, b: str | None = None) -> str:
        """经营对比。metric: payment_rate（回款完成率）|payment|sign|opportunity；a/b: 月份 YYYY-MM（默认本月 vs 上月）"""
        return await anyio.to_thread.run_sync(lambda: _run("board.compare", ctx, {"metric": metric, "a": a, "b": b}, t_compare))

    async def kb_search(ctx: Context, query: str, category: str | None = None) -> str:
        """知识库检索。query: 关键词或问题；category: 制度|售前|方案模板|案例|资质|客服（可选）"""
        return await anyio.to_thread.run_sync(lambda: _run("kb.search", ctx, {"query": query, "category": category}, t_kb_search))

    async def kb_bid_check(ctx: Context, file_url: str | None = None, requirement_url: str | None = None,
                           bid_doc_id: int | None = None, tender_doc_id: int | None = None) -> str:
        """标书合规检查。file_url: 投标文件（文档 id/标题关键字/文件链接）；requirement_url: 招标需求书（同上）"""
        return await anyio.to_thread.run_sync(lambda: _run("kb.bid_check", ctx, {"file_url": file_url, "requirement_url": requirement_url,
                                                                                  "bid_doc_id": bid_doc_id, "tender_doc_id": tender_doc_id}, t_bid_check))

    async def hr_attendance_summary(ctx: Context, period: str | None = None) -> str:
        """考勤汇总。period: 月份 YYYY-MM（默认上月）"""
        return await anyio.to_thread.run_sync(lambda: _run("hr.attendance_summary", ctx, {"period": period}, t_attendance))

    async def proj_milestone_update(ctx: Context, project_code: str, node_name: str | None = None, action: str = "query",
                                    status: str | None = None, finish_date: str | None = None, note: str | None = None) -> str:
        """项目节点。project_code: 项目编码或项目名称关键字；action: query|update；status: 未开始|进行中|已完成|延期；finish_date: YYYY-MM-DD；note: 进展说明"""
        return await anyio.to_thread.run_sync(lambda: _run("proj.milestone_update", ctx, {"project_code": project_code, "node_name": node_name, "action": action,
                                                                                           "status": status, "finish_date": finish_date, "note": note}, t_milestone))

    async def cs_ticket_register(ctx: Context, text: str | None = None, customer_name: str | None = None,
                                 problem: str | None = None, channel: str | None = None, device: str | None = None) -> str:
        """客服工单登记。text: 一句话描述（如『登记客户 XX 学校反馈：教室大屏无信号』）；或分别传 customer_name/problem/channel/device"""
        return await anyio.to_thread.run_sync(lambda: _run("cs.ticket_register", ctx, {"text": text, "customer_name": customer_name, "problem": problem,
                                                                                        "channel": channel, "device": device}, t_ticket))

    async def market_weekly_report(ctx: Context, period_type: str = "weekly", period: str | None = None) -> str:
        """重点项目周报/月报。period_type: weekly|monthly；period: 2026-W43 或 2026-10（默认本周/本月）"""
        return await anyio.to_thread.run_sync(lambda: _run("market.weekly_report", ctx, {"period_type": period_type, "period": period}, t_market_weekly))

    for code, fn in [("ops.task_create", ops_task_create), ("ops.task_review", ops_task_review),
                     ("ops.project_status", ops_project_status), ("ops.task_feedback", ops_task_feedback),
                     ("fin.daily_expense_report", fin_daily_expense_report), ("fin.project_profit", fin_project_profit),
                     ("fin.bank_flow_sync", fin_bank_flow_sync), ("oa.pending_approvals", oa_pending_approvals),
                     ("crm.contract_register", crm_contract_register), ("board.executive_daily", board_executive_daily),
                     ("board.compare", board_compare), ("kb.search", kb_search), ("kb.bid_check", kb_bid_check),
                     ("hr.attendance_summary", hr_attendance_summary), ("proj.milestone_update", proj_milestone_update),
                     ("cs.ticket_register", cs_ticket_register), ("market.weekly_report", market_weekly_report)]:
        mcp_server.tool(name=code, description=f"{TOOL_META[code]['name']}：{TOOL_META[code]['desc']}\n\n{fn.__doc__}")(fn)


_register()


# =========================== HTTP 接入（鉴权包装） ===========================
def _lookup_user(token: str) -> int | None:
    db = SessionLocal()
    try:
        u = db.scalar(select(SysUser).where(SysUser.access_token == hash_token(token)))
        from app.core.accounts import resolve_account
        u = resolve_account(db, u)
        return u.id if u and u.status == 1 and not u.is_deleted else None
    except BizError:
        return None
    finally:
        db.close()


def _audit_unauth(ip: str | None, reason: str) -> None:
    db = SessionLocal()
    try:
        _audit(db, None, "-", {}, "unauthorized", reason, 0, ip)
    finally:
        db.close()


class McpAuthApp:
    """Bearer Token 校验后把 user_id 放入 scope.state，再交给 Streamable HTTP 处理。"""

    def __init__(self, inner: Any):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.inner(scope, receive, send)
        headers = dict(scope.get("headers") or [])
        auth = headers.get(b"authorization", b"").decode()
        ip = (scope.get("client") or [None])[0]
        uid = None
        if auth.lower().startswith("bearer "):
            uid = await anyio.to_thread.run_sync(_lookup_user, auth[7:].strip())
        if not uid:
            await anyio.to_thread.run_sync(_audit_unauth, ip, "缺少令牌" if not auth else "令牌无效或已吊销")
            body = json.dumps({"jsonrpc": "2.0", "id": None, "error": {
                "code": -32001, "message": "未认证：请在工作台「WorkBuddy 接入」页获取个人 Access Token"}}, ensure_ascii=False).encode()
            await send({"type": "http.response.start", "status": 401, "headers": [
                (b"content-type", b"application/json"), (b"www-authenticate", b'Bearer realm="jucheng-portal"')]})
            await send({"type": "http.response.body", "body": body})
            return
        scope.setdefault("state", {})["mcp_user_id"] = uid
        scope["path"] = "/"
        scope["raw_path"] = b"/"
        return await self.inner(scope, receive, send)


def build_asgi():
    mcp_server.streamable_http_app()  # 初始化 session manager
    return McpAuthApp(StreamableHTTPASGIApp(mcp_server.session_manager))

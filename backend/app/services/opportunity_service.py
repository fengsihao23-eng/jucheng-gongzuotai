"""商机信息表（FR-05-02）：漏斗聚合、列表/合计、人工新增编辑、阶段推进与历史、赢单转合同草稿、导出。

- 阶段口径来自 sys_dict(opportunity_stage.stages)：线索 → 初步接触 → 方案报价 → 商务谈判 → 赢单/丢单
- 赢单率 = 赢单数 ÷ (赢单数 + 丢单数)
- 来源 source_type：1 企微商机工单自动登记（dispatch_service）2 人工录入 3 Excel 清洗导入
- 阶段变更历史写 log_operation(action=opportunity.stage, target_type=opportunity, target_id=商机 id)
"""
import io
from datetime import date, datetime, timedelta
from decimal import Decimal

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.audit import log_op, snapshot
from app.core.deps import owner_scope
from app.core.response import bad_request, conflict
from app.core.timeutil import TZ, day_bounds, month_range, today, week_range
from app.models import BizOpportunity, LogOperation, SysUser
from app.services import codes, dict_service

ZERO = Decimal("0")
SOURCE_NAMES = {1: "企微商机工单自动登记", 2: "人工录入", 3: "Excel 清洗导入"}
DEFAULT_STAGES = [{"key": "lead", "name": "线索"}, {"key": "contact", "name": "初步接触"},
                  {"key": "quote", "name": "方案报价"}, {"key": "negotiation", "name": "商务谈判"},
                  {"key": "won", "name": "赢单"}, {"key": "lost", "name": "丢单"}]
EDIT_FIELDS = ["name", "customer_name", "business_line", "stage", "amount", "expected_sign_date", "owner_id",
               "project_code", "remark", "status"]


def stages(db: Session) -> list[dict]:
    return dict_service.get(db, "opportunity_stage", "stages") or DEFAULT_STAGES


def stage_names(db: Session) -> dict[str, str]:
    return {s["key"]: s["name"] for s in stages(db)}


def status_of(stage: str) -> str:
    return {"won": "won", "lost": "lost"}.get(stage, "open")


# ---------------- 查询 ----------------
def _filtered(p, *, stage: str = "", business_line: str = "", owner_id: int | None = None, customer: str = "",
              start: date | None = None, end: date | None = None, source: int | None = None, keyword: str = "",
              status: str = ""):
    q = select(BizOpportunity).where(BizOpportunity.is_deleted.is_(False))
    q = q.where(owner_scope(BizOpportunity.owner_id, p))
    if stage:
        q = q.where(BizOpportunity.stage.in_(stage.split(",")))
    if business_line:
        q = q.where(BizOpportunity.business_line.in_(business_line.split(",")))
    if owner_id:
        q = q.where(BizOpportunity.owner_id == owner_id)
    if customer:
        q = q.where(BizOpportunity.customer_name.ilike(f"%{customer}%"))
    if keyword:
        q = q.where(or_(BizOpportunity.name.ilike(f"%{keyword}%"), BizOpportunity.opp_no.ilike(f"%{keyword}%"),
                        BizOpportunity.customer_name.ilike(f"%{keyword}%"),
                        BizOpportunity.source_approval_no.ilike(f"%{keyword}%")))
    if start:
        q = q.where(BizOpportunity.created_at >= day_bounds(start)[0])
    if end:
        q = q.where(BizOpportunity.created_at < day_bounds(end)[1])
    if source:
        q = q.where(BizOpportunity.source_type == source)
    if status:
        q = q.where(BizOpportunity.status == status)
    return q


def row(db: Session, o: BizOpportunity, names: dict | None = None) -> dict:
    names = names or stage_names(db)
    return {"id": o.id, "opp_no": o.opp_no, "name": o.name, "customer_name": o.customer_name,
            "business_line": o.business_line, "stage": o.stage, "stage_name": names.get(o.stage, o.stage),
            "amount": o.amount, "expected_sign_date": o.expected_sign_date, "owner_id": o.owner_id,
            "owner_name": o.owner.name if o.owner else None, "source_type": o.source_type,
            "source_name": SOURCE_NAMES.get(o.source_type, "其他"), "source_approval_no": o.source_approval_no,
            "status": o.status, "project_code": o.project_code, "contract_no": o.contract_no,
            "remark": o.remark,
            "created_at": o.created_at, "updated_at": o.updated_at}


def list_page(db: Session, p, page: int = 1, page_size: int = 20, sort: str = "-created_at", **filters) -> dict:
    q = _filtered(p, **filters)
    sub = q.subquery()
    total, amount = db.execute(select(func.count(), func.coalesce(func.sum(sub.c.amount), 0)).select_from(sub)).one()
    desc = sort.startswith("-")
    col = getattr(BizOpportunity, sort.lstrip("-"), BizOpportunity.created_at)
    q = q.order_by(col.desc() if desc else col.asc(), BizOpportunity.id.desc())
    items = db.scalars(q.offset((page - 1) * page_size).limit(page_size)).unique().all()
    names = stage_names(db)
    return {"items": [row(db, o, names) for o in items], "total": total, "page": page, "page_size": page_size,
            "totals": {"count": total, "amount": Decimal(amount)}}


def funnel(db: Session, p, **filters) -> dict:
    """按阶段聚合数量/金额（与列表同一筛选条件，筛选中的 stage 不参与聚合）。"""
    filters = {k: v for k, v in filters.items() if k != "stage"}
    sub = _filtered(p, **filters).subquery()
    agg = {s: (n, Decimal(a)) for s, n, a in db.execute(
        select(sub.c.stage, func.count(), func.coalesce(func.sum(sub.c.amount), 0)).group_by(sub.c.stage)).all()}
    out = []
    for s in stages(db):
        n, a = agg.get(s["key"], (0, ZERO))
        out.append({"stage": s["key"], "name": s["name"], "count": n, "amount": a})
    won, lost = agg.get("won", (0, ZERO)), agg.get("lost", (0, ZERO))
    open_rows = [x for x in out if x["stage"] not in ("won", "lost")]
    T = today()
    ws, _ = week_range(ref=T)
    ms, _ = month_range(ref=T)
    base = _filtered(p, **filters)

    def _count_since(d: date) -> tuple[int, Decimal]:
        s2 = base.where(BizOpportunity.created_at >= day_bounds(d)[0]).subquery()
        n, a = db.execute(select(func.count(), func.coalesce(func.sum(s2.c.amount), 0)).select_from(s2)).one()
        return n, Decimal(a)

    wn, wa = _count_since(ws)
    mn, ma = _count_since(ms)
    decided = won[0] + lost[0]
    return {
        "stages": out,
        "total": {"count": sum(x["count"] for x in out), "amount": sum((x["amount"] for x in out), ZERO)},
        "open": {"count": sum(x["count"] for x in open_rows), "amount": sum((x["amount"] for x in open_rows), ZERO)},
        "won": {"count": won[0], "amount": won[1]}, "lost": {"count": lost[0], "amount": lost[1]},
        "win_rate": round(won[0] / decided, 4) if decided else None,
        "win_rate_amount": round(float(won[1] / (won[1] + lost[1])), 4) if (won[1] + lost[1]) else None,
        "new_week": {"count": wn, "amount": wa, "since": ws}, "new_month": {"count": mn, "amount": ma, "since": ms},
    }


def options(db: Session) -> dict:
    owners = db.scalars(select(SysUser).where(SysUser.is_deleted.is_(False), SysUser.status == 1)
                        .order_by(SysUser.id)).unique().all()
    from app.core.permissions import BUSINESS_LINES
    return {"stages": stages(db), "business_lines": [{"key": k, "name": v} for k, v in BUSINESS_LINES.items()],
            "owners": [{"id": u.id, "name": u.name, "dept_code": u.dept_code} for u in owners
                       if u.dept_code in ("SC", "YX", "ZB")],
            "sources": [{"key": k, "name": v} for k, v in SOURCE_NAMES.items()]}


# ---------------- 写入 ----------------
def _apply(db: Session, o: BizOpportunity, body: dict) -> None:
    names = stage_names(db)
    if "stage" in body and body["stage"] and body["stage"] not in names:
        raise bad_request(f"未知阶段：{body['stage']}")
    for k in ("name", "customer_name", "business_line", "stage", "expected_sign_date", "owner_id", "project_code",
              "remark"):
        if k in body:
            setattr(o, k, body[k])
    if "amount" in body and body["amount"] is not None:
        amt = Decimal(str(body["amount"]))
        if amt <= 0:
            raise bad_request("预计金额须大于 0")
        o.amount = amt
    if not o.name or not o.customer_name:
        raise bad_request("商机名称与客户名称必填")
    if o.business_line:
        o.business_line = o.business_line[:2].upper()
    o.status = status_of(o.stage)


def create(db: Session, p, body: dict, ip: str | None = None, source_type: int = 2) -> BizOpportunity:
    body = {**body, "stage": body.get("stage") or "lead"}
    o = BizOpportunity(opp_no=codes.opp_no(db), source_type=source_type, amount=ZERO, name="", customer_name="",
                       business_line="XY", stage="lead")
    _apply(db, o, body)
    if o.owner_id is None and p is not None:
        o.owner_id = p.id
    db.add(o)
    db.flush()
    log_op(db, p.id if p else None, "opportunity.create", "opportunity", o.id, after=snapshot(o, EDIT_FIELDS), ip=ip)
    log_op(db, p.id if p else None, "opportunity.stage", "opportunity", o.id, before=None,
           after={"stage": o.stage, "note": "人工新增" if source_type == 2 else "Excel 导入"}, ip=ip)
    return o


def update(db: Session, p, o: BizOpportunity, body: dict, ip: str | None = None) -> BizOpportunity:
    before = snapshot(o, EDIT_FIELDS)
    old_stage = o.stage
    _apply(db, o, body)
    db.flush()
    log_op(db, p.id, "opportunity.update", "opportunity", o.id, before=before, after=snapshot(o, EDIT_FIELDS), ip=ip)
    if o.stage != old_stage:
        log_op(db, p.id, "opportunity.stage", "opportunity", o.id, before={"stage": old_stage},
               after={"stage": o.stage, "note": "编辑时修改阶段"}, ip=ip)
    return o


def advance(db: Session, p, o: BizOpportunity, to_stage: str | None, note: str | None = None,
            lost_reason: str | None = None, ip: str | None = None) -> BizOpportunity:
    """阶段推进：不传 to_stage 时推进到下一阶段（商务谈判之后需明确赢单/丢单）。"""
    keys = [s["key"] for s in stages(db)]
    if not to_stage:
        if o.stage in ("won", "lost"):
            raise conflict("商机已关闭，不能继续推进")
        i = keys.index(o.stage) if o.stage in keys else 0
        to_stage = keys[i + 1] if i + 1 < len(keys) else None
        if to_stage in (None, "won", "lost") and o.stage == "negotiation":
            raise bad_request("商务谈判阶段请选择赢单或丢单")
    if to_stage not in keys:
        raise bad_request(f"未知阶段：{to_stage}")
    if to_stage == o.stage:
        raise conflict("商机已处于该阶段")
    if to_stage == "lost" and not (lost_reason or "").strip():
        raise bad_request("丢单需填写丢单原因")
    old = o.stage
    o.stage, o.status = to_stage, status_of(to_stage)
    if to_stage == "lost":
        o.remark = f"丢单原因：{lost_reason.strip()}" + (f"\n{o.remark}" if o.remark and not o.remark.startswith("丢单原因") else "")
    db.flush()
    log_op(db, p.id, "opportunity.stage", "opportunity", o.id, before={"stage": old},
           after={"stage": to_stage, "note": note, "lost_reason": lost_reason}, ip=ip)
    return o


def history(db: Session, o: BizOpportunity) -> list[dict]:
    names = stage_names(db)
    logs = db.execute(select(LogOperation, SysUser.name).outerjoin(SysUser, SysUser.id == LogOperation.user_id)
                      .where(LogOperation.target_type == "opportunity", LogOperation.target_id == str(o.id),
                             LogOperation.action.like("opportunity.%"))
                      .order_by(LogOperation.created_at, LogOperation.id)).all()
    out = []
    if o.source_type == 1:
        out.append({"at": o.created_at, "action": "source", "title": "企微商机工单自动登记",
                    "stage": None, "stage_name": None, "operator": "系统",
                    "note": f"来源审批单 {o.source_approval_no or '—'}"})
    has_stage = any(lg.action == "opportunity.stage" for lg, _ in logs)
    if not has_stage:  # 无变更记录：以当前阶段作为初始状态
        out.append({"at": o.created_at, "action": "stage", "title": f"登记为「{names.get(o.stage, o.stage)}」",
                    "stage": o.stage, "stage_name": names.get(o.stage), "from_stage": None, "operator": None, "note": None})
    for lg, uname in logs:
        if lg.action != "opportunity.stage":
            if lg.action == "opportunity.update":
                out.append({"at": lg.created_at, "action": "update", "title": "编辑商机信息", "operator": uname,
                            "note": _diff_text(lg.before or {}, lg.after or {})})
            continue
        frm = (lg.before or {}).get("stage")
        to = (lg.after or {}).get("stage")
        title = f"{names.get(frm, frm)} → {names.get(to, to)}" if frm else f"登记为「{names.get(to, to)}」"
        out.append({"at": lg.created_at, "action": "stage", "title": title, "stage": to, "stage_name": names.get(to),
                    "from_stage": frm, "operator": uname or ("系统" if lg.user_id is None else None),
                    "note": (lg.after or {}).get("note"), "lost_reason": (lg.after or {}).get("lost_reason")})
    return out


def _diff_text(before: dict, after: dict) -> str:
    labels = {"name": "名称", "customer_name": "客户", "business_line": "业务线", "stage": "阶段", "amount": "金额",
              "expected_sign_date": "预计签约", "owner_id": "负责人", "project_code": "项目", "remark": "备注"}
    parts = [f"{labels[k]}：{before.get(k) or '空'} → {after.get(k) or '空'}" for k in labels
             if k != "stage" and before.get(k) != after.get(k)]
    return "；".join(parts) or "无字段变化"


def detail(db: Session, o: BizOpportunity) -> dict:
    d = row(db, o)
    d["history"] = history(db, o)
    lost = next((h for h in reversed(d["history"]) if h.get("stage") == "lost" and h.get("lost_reason")), None)
    d["lost_reason"] = lost["lost_reason"] if lost else None
    return d


def contract_draft(db: Session, o: BizOpportunity) -> dict:
    """赢单 → 合同草稿预填数据（由前端带入合同新增表单，最终以合同审批/人工登记落台账）。"""
    if o.stage != "won":
        raise conflict("仅赢单商机可转合同")
    return {"name": f"{o.name}合同", "customer_name": o.customer_name, "amount": o.amount,
            "business_line": o.business_line, "project_code": o.project_code, "owner_id": o.owner_id,
            "owner_name": o.owner.name if o.owner else None, "sign_date": today(),
            "source_opp_no": o.opp_no, "source_opp_id": o.id,
            "remark": f"由商机 {o.opp_no}（{o.name}）赢单转入"}


# ---------------- 导出 ----------------
HEAD_FILL = PatternFill("solid", fgColor="1B3275")


def _sheet_header(ws, headers):
    ws.append(headers)
    for c in ws[1]:
        c.font, c.fill = Font(bold=True, color="FFFFFF"), HEAD_FILL
        c.alignment = Alignment(vertical="center")


def export_xlsx(db: Session, p, **filters) -> bytes:
    q = _filtered(p, **filters).order_by(BizOpportunity.created_at.desc(), BizOpportunity.id.desc())
    items = db.scalars(q).unique().all()
    names = stage_names(db)
    wb = Workbook()
    ws = wb.active
    ws.title = "商机信息表"
    _sheet_header(ws, ["商机编号", "商机名称", "客户名称", "业务线", "阶段", "预计金额(元)", "预计签约日", "负责人",
                       "来源", "来源审批单号", "状态", "关联项目", "登记时间", "备注"])
    from app.core.permissions import BUSINESS_LINES
    total = ZERO
    for o in items:
        total += o.amount
        ws.append([o.opp_no, o.name, o.customer_name, BUSINESS_LINES.get(o.business_line, o.business_line),
                   names.get(o.stage, o.stage), float(o.amount), o.expected_sign_date,
                   o.owner.name if o.owner else None, SOURCE_NAMES.get(o.source_type), o.source_approval_no,
                   {"open": "进行中", "won": "赢单", "lost": "丢单"}.get(o.status, o.status), o.project_code,
                   o.created_at.astimezone(TZ).strftime("%Y-%m-%d %H:%M") if o.created_at else None, o.remark])
    ws.append(["合计", f"{len(items)} 条", None, None, None, float(total)])
    for col, w in zip("ABCDEFGHIJKLMN", [14, 28, 24, 12, 10, 14, 12, 10, 18, 18, 8, 16, 16, 30]):
        ws.column_dimensions[col].width = w
    for r in ws.iter_rows(min_row=2, min_col=6, max_col=6):
        r[0].number_format = "#,##0.00"
    cal = wb.create_sheet("口径说明")
    for line in [["口径项", "说明"], ["数据范围", "与页面筛选条件一致（阶段/业务线/负责人/客户/登记期间/来源）"],
                 ["商机阶段", "线索 → 初步接触 → 方案报价 → 商务谈判 → 赢单/丢单（sys_dict opportunity_stage）"],
                 ["赢单率", "赢单数 ÷ (赢单数 + 丢单数)"],
                 ["来源", "企微商机工单审批通过后自动登记 / 人工录入 / Excel 清洗导入"],
                 ["导出时间", datetime.now(TZ).strftime("%Y-%m-%d %H:%M")]]:
        cal.append(line)
    cal.column_dimensions["A"].width, cal.column_dimensions["B"].width = 14, 80
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def recent_created(db: Session, days: int) -> int:
    since = day_bounds(today() - timedelta(days=days - 1))[0]
    return db.scalar(select(func.count()).select_from(BizOpportunity).where(
        BizOpportunity.is_deleted.is_(False), BizOpportunity.created_at >= since)) or 0

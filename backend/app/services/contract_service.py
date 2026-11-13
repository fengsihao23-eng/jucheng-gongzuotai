"""合同台账（FR-05-01）与合同归档回款读取（FR-05-06）。

- 列表/导出共用同一过滤器，保证「导出 xlsx 与页面筛选结果一致」
- 人工录入 source_type=2；改动写 log_operation（前后值）
- 归档：上传原件（版本控制 key=contract/{合同号}）→ 抽取全文 → 规则识别付款条款 → 生成/更新回款计划
  → 在银行流水中找该客户未核对收入、金额等于某阶段应收额 → payment_service.register_payment(source_type=3)
  → 通知财务与市场负责人；合同状态 → 已归档
"""
from __future__ import annotations

import io
import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session

from app.core.audit import log_op, snapshot
from app.core.permissions import BUSINESS_LINES
from app.core.deps import owner_scope
from app.core.response import BizError, bad_request, not_found
from app.core.storage import file_brief, read_bytes, save_bytes
from app.core.timeutil import month_range, today, utcnow, week_range
from app.models import (CONTRACT_STATUS, BizContract, BizPayment, BizPaymentPlan, BizProject, FinBankFlow, SysFile,
                        SyncWecomApproval, SysUser)
from app.services import codes, notify_service, payment_service

ZERO = Decimal("0")
SOURCE_TYPES = {1: "企微审批", 2: "人工录入", 3: "Excel 导入", 4: "企微表格同步"}
PAY_SOURCE = {1: "流水核对", 2: "人工登记", 3: "归档自动关联"}
EDIT_FIELDS = ["contract_no", "name", "project_code", "customer_name", "amount", "sign_date", "business_line",
               "region", "owner_id", "status", "remark"]
LINES = BUSINESS_LINES


# ------------------------------------------------------------------ 查询
def _received_sq():
    return select(BizPayment.contract_id.label("cid"), func.sum(BizPayment.amount).label("received")) \
        .group_by(BizPayment.contract_id).subquery()


def filtered(p, *, business_line: str = "", customer: str = "", status: int | None = None, sign_month: str = "",
             sign_from: date | None = None, sign_to: date | None = None, source_type: int | None = None,
             keyword: str = "", project_code: str = "", paid_month: str = "", owner_id: int | None = None,
             region: str = "", sign_period: str = ""):
    q = select(BizContract).where(BizContract.is_deleted.is_(False))
    q = q.where(owner_scope(BizContract.owner_id, p))
    if business_line:
        q = q.where(BizContract.business_line == business_line)
    if region:
        q = q.where(BizContract.region == region)
    if customer:
        q = q.where(BizContract.customer_name.contains(customer))
    if status:
        q = q.where(BizContract.status == status)
    if sign_period:
        bounds = signing_periods().get(sign_period)
        if bounds is None:
            raise bad_request("无效的签约统计期间")
        start, end = bounds
        q = q.where(BizContract.status.in_((1, 2)), BizContract.sign_date <= end)
        if start:
            q = q.where(BizContract.sign_date >= start)
        sign_month, sign_from, sign_to = "", None, None
    if sign_month:
        s, e = month_range(sign_month)
        q = q.where(BizContract.sign_date >= s, BizContract.sign_date <= e)
    if sign_from:
        q = q.where(BizContract.sign_date >= sign_from)
    if sign_to:
        q = q.where(BizContract.sign_date <= sign_to)
    if source_type:
        q = q.where(BizContract.source_type == source_type)
    if project_code:
        q = q.where(BizContract.project_code == project_code)
    if owner_id:
        q = q.where(BizContract.owner_id == owner_id)
    if paid_month:
        s, e = month_range(paid_month)
        q = q.where(exists().where(BizPayment.contract_id == BizContract.id, BizPayment.payment_date >= s,
                                   BizPayment.payment_date <= e))
    if keyword:
        kw = f"%{keyword}%"
        q = q.where(or_(BizContract.contract_no.ilike(kw), BizContract.name.ilike(kw),
                        BizContract.customer_name.ilike(kw), BizContract.project_code.ilike(kw),
                        BizContract.source_approval_no.ilike(kw)))
    return q


def signing_periods():
    day = today()
    return {"all": (None, day), "month": (month_range(ref=day)[0], day),
            "week": (week_range(ref=day)[0], day), "today": (day, day)}


def signing_board(db, p, **filters):
    base = filtered(p, **{k: v for k, v in filters.items() if k not in {
        "sign_month", "sign_from", "sign_to", "sign_period"}}).where(BizContract.status.in_((1, 2))).subquery()
    result = []
    for key, (start, end) in signing_periods().items():
        q = select(func.count(base.c.id), func.coalesce(func.sum(base.c.amount), 0)).where(base.c.sign_date <= end)
        if start:
            q = q.where(base.c.sign_date >= start)
        count, amount = db.execute(q).one()
        result.append({"key": key, "start": start, "end": end, "count": count, "amount": amount})
    return result


def _period_paid(db: Session, ids: list[int], paid_month: str) -> dict[int, Decimal]:
    if not paid_month or not ids:
        return {}
    s, e = month_range(paid_month)
    return {k: Decimal(v) for k, v in db.execute(
        select(BizPayment.contract_id, func.sum(BizPayment.amount))
        .where(BizPayment.contract_id.in_(ids), BizPayment.payment_date >= s, BizPayment.payment_date <= e)
        .group_by(BizPayment.contract_id)).all()}


def row(c: BizContract, received: Decimal, owners: dict, projects: dict, period_paid: Decimal | None = None) -> dict:
    received = Decimal(received or 0)
    return {"id": c.id, "contract_no": c.contract_no, "name": c.name, "project_code": c.project_code,
            "project_name": projects.get(c.project_code), "customer_name": c.customer_name, "amount": c.amount,
            "sign_date": c.sign_date, "business_line": c.business_line, "region": c.region,
            "business_line_name": LINES.get(c.business_line, c.business_line), "owner_id": c.owner_id,
            "owner_name": owners.get(c.owner_id), "source_type": c.source_type,
            "source_name": SOURCE_TYPES.get(c.source_type), "source_approval_no": c.source_approval_no,
            "status": c.status, "status_name": CONTRACT_STATUS.get(c.status), "archived": bool(c.archive_file_id),
            "archived_at": c.archived_at, "received": received, "receivable": c.amount - received,
            "received_rate": float((received / c.amount).quantize(Decimal("0.0001"))) if c.amount else None,
            "period_paid": period_paid, "remark": c.remark}


def _lookups(db: Session, rows: list[BizContract]) -> tuple[dict, dict]:
    owners = {u.id: u.name for u in db.scalars(select(SysUser).where(SysUser.id.in_({c.owner_id for c in rows if c.owner_id})))} \
        if rows else {}
    pcs = {c.project_code for c in rows if c.project_code}
    projects = {p.project_code: p.name for p in db.scalars(select(BizProject).where(BizProject.project_code.in_(pcs)))} \
        if pcs else {}
    return owners, projects


def list_page(db: Session, p, page: int = 1, page_size: int = 20, **f) -> dict:
    q = filtered(p, **f)
    sq = _received_sq()
    base = q.subquery()
    total, amount, received = db.execute(
        select(func.count(base.c.id), func.coalesce(func.sum(base.c.amount), 0), func.coalesce(func.sum(sq.c.received), 0))
        .select_from(base).outerjoin(sq, sq.c.cid == base.c.id)).one()
    page_size = max(1, min(page_size, 500))
    rows = db.execute(q.add_columns(func.coalesce(sq.c.received, 0)).outerjoin(sq, sq.c.cid == BizContract.id)
                      .order_by(BizContract.sign_date.desc(), BizContract.id.desc())
                      .offset((page - 1) * page_size).limit(page_size)).all()
    contracts = [r[0] for r in rows]
    owners, projects = _lookups(db, contracts)
    pp = _period_paid(db, [c.id for c in contracts], f.get("paid_month", ""))
    items = [row(c, rec, owners, projects, pp.get(c.id) if f.get("paid_month") else None) for c, rec in rows]
    sums = {"count": int(total), "amount": Decimal(amount), "received": Decimal(received),
            "receivable": Decimal(amount) - Decimal(received)}
    if f.get("paid_month"):
        s, e = month_range(f["paid_month"])
        ids_q = select(base.c.id)
        sums["period_paid"] = Decimal(db.scalar(select(func.coalesce(func.sum(BizPayment.amount), 0))
                                                .where(BizPayment.contract_id.in_(ids_q), BizPayment.payment_date >= s,
                                                       BizPayment.payment_date <= e)))
    return {"items": items, "total": int(total), "page": page, "page_size": page_size, "sums": sums}


def export_xlsx(db: Session, p, **f) -> tuple[bytes, int]:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    q = filtered(p, **f)
    sq = _received_sq()
    rows = db.execute(q.add_columns(func.coalesce(sq.c.received, 0)).outerjoin(sq, sq.c.cid == BizContract.id)
                      .order_by(BizContract.sign_date.desc(), BizContract.id.desc())).all()
    contracts = [r[0] for r in rows]
    owners, projects = _lookups(db, contracts)
    pp = _period_paid(db, [c.id for c in contracts], f.get("paid_month", ""))
    wb = Workbook()
    ws = wb.active
    ws.title = "合同台账"
    head = ["合同编号", "合同名称", "客户名称", "业务线", "区域", "所属项目", "合同金额(元)", "签订日期", "销售负责人", "状态",
            "已回款(元)", "未回款(元)", "回款率", "来源", "来源审批单号", "已归档", "备注"]
    if f.get("paid_month"):
        head.insert(11, f"{f['paid_month']} 回款(元)")
    ws.append(head)
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1B3275")
        cell.alignment = Alignment(horizontal="center")
    for c, rec in rows:
        r = row(c, rec, owners, projects)
        line = [r["contract_no"], r["name"], r["customer_name"], r["business_line_name"], r["region"] or "",
                f"{r['project_code'] or ''} {r['project_name'] or ''}".strip(), float(r["amount"]), r["sign_date"],
                r["owner_name"], r["status_name"], float(r["received"]), float(r["receivable"]),
                float(r["received_rate"] or 0), r["source_name"], r["source_approval_no"], "是" if r["archived"] else "否",
                r["remark"]]
        if f.get("paid_month"):
            line.insert(11, float(pp.get(c.id, ZERO)))
        ws.append(line)
    for i, w in enumerate([14, 36, 26, 12, 34, 14, 12, 10, 8, 14, 14, 8, 10, 20, 8, 20], start=1):
        ws.column_dimensions[ws.cell(1, i).column_letter].width = w
    for r_ in ws.iter_rows(min_row=2):
        for cell in r_:
            if isinstance(cell.value, float):
                cell.number_format = "0.00%" if ws.cell(1, cell.column).value == "回款率" else "#,##0.00"
            elif isinstance(cell.value, date):
                cell.number_format = "yyyy-mm-dd"
    if rows:
        n = len(rows) + 1
        ws.append([])
        ws.append(["合计", f"{len(rows)} 份"] + [None] * 3 + [f"=SUM(F2:F{n})"])
    ws.freeze_panes = "A2"
    cal = wb.create_sheet("口径说明")
    cond = {k: str(v) for k, v in f.items() if v not in (None, "", 0)}
    for line in [["项目", "说明"],
                 ["数据范围", "合同台账（biz_contract）中未删除的合同；与页面列表使用同一筛选条件"],
                 ["筛选条件", "；".join(f"{k}={v}" for k, v in cond.items()) or "无（全部）"],
                 ["合同金额", "合同签订金额（元，含税）"],
                 ["签订日期", "企微合同审批通过日 / 人工录入日期（PRD 3.4 当月合同签订额口径）"],
                 ["已回款", "biz_payment 合计；回款日期以银行流水到账日为准（PRD 3.4）"],
                 ["回款率", "已回款 ÷ 合同金额"],
                 ["来源", "企微审批=审批通过自动登记；人工录入；Excel 导入=数据清洗导入"],
                 ["导出时间", f"{utcnow():%Y-%m-%d %H:%M} UTC"], ["导出行数", str(len(rows))]]:
        cal.append(line)
    cal.column_dimensions["A"].width = 14
    cal.column_dimensions["B"].width = 90
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue(), len(rows)


def get_contract(db: Session, cid: int, p=None) -> BizContract:
    c = db.get(BizContract, cid)
    if not c or c.is_deleted:
        raise not_found("合同")
    if p is not None and not db.scalar(select(BizContract.id).where(BizContract.id == cid,
                                                                    owner_scope(BizContract.owner_id, p))):
        raise BizError(40301, "无权查看该合同")
    return c


def versions(db: Session, c: BizContract) -> list[dict]:
    files = db.scalars(select(SysFile).where(SysFile.storage_key == archive_key(c)).order_by(SysFile.version.desc())).all()
    out = [{**file_brief(f), "current": f.id == c.archive_file_id} for f in files]
    if c.archive_file_id and not any(x["current"] for x in out):
        f = db.get(SysFile, c.archive_file_id)
        if f:
            out.insert(0, {**file_brief(f), "current": True})
    return out


def detail(db: Session, c: BizContract) -> dict:
    owners, projects = _lookups(db, [c])
    received = payment_service.contract_received(c)
    base = row(c, received, owners, projects)
    approval = None
    if c.source_approval_no:
        a = db.scalar(select(SyncWecomApproval).where(SyncWecomApproval.sp_no == c.source_approval_no))
        if a:
            applicant = db.scalar(select(SysUser).where(SysUser.wecom_userid == a.applicant_userid))
            approval = {"sp_no": a.sp_no, "sp_name": a.sp_name, "template_key": a.template_key,
                        "applicant_userid": a.applicant_userid, "applicant_name": applicant.name if applicant else None,
                        "status": a.status, "status_name": {1: "审批中", 2: "已通过", 3: "已驳回", 4: "已撤销"}.get(a.status),
                        "apply_time": a.apply_time, "received_via": a.received_via, "dispatch_status": a.dispatch_status,
                        "dispatched_at": a.dispatched_at, "fields": (a.detail or {}).get("fields", {}),
                        "approved_time": (a.detail or {}).get("approved_time")}
    project = None
    if c.project_code:
        pj = db.scalar(select(BizProject).where(BizProject.project_code == c.project_code))
        if pj:
            from app.models import PROJECT_STATUS
            project = {"project_code": pj.project_code, "name": pj.name, "status": pj.status,
                       "status_text": PROJECT_STATUS.get(pj.status), "owner_name": pj.owner.name if pj.owner else None,
                       "contract_amount": pj.contract_amount}
    regs = {u.id: u.name for u in db.scalars(select(SysUser).where(SysUser.id.in_({x.register_by for x in c.payments if x.register_by})))} \
        if c.payments else {}
    flows = {f.id: f for f in db.scalars(select(FinBankFlow).where(FinBankFlow.id.in_({x.bank_flow_id for x in c.payments if x.bank_flow_id})))} \
        if c.payments else {}
    stage = {pl.id: pl.stage_name for pl in c.plans}
    payments = [{"id": x.id, "amount": x.amount, "payment_date": x.payment_date, "source_type": x.source_type,
                 "source_name": PAY_SOURCE.get(x.source_type), "plan_stage": stage.get(x.plan_id),
                 "bank_flow_id": x.bank_flow_id, "counterparty": flows[x.bank_flow_id].counterparty if x.bank_flow_id in flows else None,
                 "summary": flows[x.bank_flow_id].summary if x.bank_flow_id in flows else None,
                 "register_by": regs.get(x.register_by), "remark": x.remark}
                for x in sorted(c.payments, key=lambda x: (x.payment_date, x.id), reverse=True)]
    return {**base, "approval": approval, "project": project, "plans": plan_rows(c),
            "payments": payments, "files": versions(db, c),
            "archive_file": file_brief(db.get(SysFile, c.archive_file_id)) if c.archive_file_id else None}


def plan_rows(c: BizContract) -> list[dict]:
    """payment_service.plan_rows + 比率保留 4 位（float）与已回款进度。"""
    rows = payment_service.plan_rows(c)
    for r in rows:
        r["ratio"] = float(r["ratio"]) if r["ratio"] is not None else None
        r["progress"] = float((r["received_amount"] / r["amount"]).quantize(Decimal("0.0001"))) if r["amount"] else None
    return rows


# ------------------------------------------------------------------ 新增 / 编辑
def _clean(db: Session, body: dict, creating: bool) -> dict:
    out = {}
    for k in EDIT_FIELDS:
        if k not in body:
            continue
        v = body[k]
        if k == "amount":
            try:
                v = Decimal(str(v).replace(",", ""))
            except (InvalidOperation, TypeError):
                raise bad_request("合同金额无效")
            if v <= 0:
                raise bad_request("合同金额须大于 0")
        elif k == "sign_date":
            v = v if isinstance(v, date) else date.fromisoformat(str(v)[:10]) if v else None
            if not v:
                raise bad_request("签订日期必填")
        elif k == "business_line":
            v = (v or "").upper()
            if v not in LINES:
                raise bad_request("业务线无效")
        elif k == "status":
            v = int(v)
            if v not in CONTRACT_STATUS:
                raise bad_request("状态无效")
        elif k == "project_code":
            v = (v or "").strip() or None
            if v and not db.scalar(select(BizProject.id).where(BizProject.project_code == v)):
                raise bad_request(f"项目 {v} 不存在")
        elif k in ("customer_name", "name", "remark", "contract_no", "region"):
            v = (v or "").strip() or None
        out[k] = v
    if creating:
        for req in ("customer_name", "amount", "sign_date", "business_line"):
            if not out.get(req):
                raise bad_request(f"缺少必填字段 {req}")
    elif "customer_name" in out and not out["customer_name"]:
        raise bad_request("客户名称不能为空")
    return out


def create(db: Session, p, body: dict, ip: str | None = None, source_type: int = 2, *, commit=True) -> BizContract:
    data = _clean(db, body, True)
    if data.get("project_code") and not data.get("business_line"):
        data["business_line"] = data["project_code"].split("-")[1]
    no = data.pop("contract_no", None) or codes.contract_no(db, data["sign_date"].year)
    if db.scalar(select(BizContract.id).where(BizContract.contract_no == no)):
        raise BizError(40901, f"合同编号 {no} 已存在")
    owner = data.pop("owner_id", None)
    if owner is None and source_type != 4:
        owner = p.id  # 人工录入默认归属于录入人；企微表格同步不臆造负责人
    c = BizContract(contract_no=no, source_type=source_type, status=data.pop("status", 1),
                    owner_id=owner, **data)
    db.add(c)
    db.flush()
    log_op(db, p.id, "contract.create", "contract", c.id, None, snapshot(c, EDIT_FIELDS + ["source_type"]), ip)
    if commit:
        db.commit()
        _invalidate()
    return c


def update(db: Session, p, c: BizContract, body: dict, ip: str | None = None, *, commit=True,
           reprice_plans=True) -> BizContract:
    data = _clean(db, body, False)
    if "contract_no" in data:
        if not data["contract_no"]:
            data.pop("contract_no")
        elif data["contract_no"] != c.contract_no and db.scalar(
                select(BizContract.id).where(BizContract.contract_no == data["contract_no"])):
            raise BizError(40901, f"合同编号 {data['contract_no']} 已存在")
    changed = [k for k, v in data.items() if getattr(c, k) != v]
    if not changed:
        return c
    before = snapshot(c, changed)
    for k in changed:
        setattr(c, k, data[k])
    if reprice_plans and "amount" in changed and c.plans:
        for pl in c.plans:
            if pl.ratio is not None:
                pl.amount = (c.amount * pl.ratio).quantize(Decimal("1"))
        payment_service.refresh_plan_status(c)
    log_op(db, p.id, "contract.update", "contract", c.id, before, snapshot(c, changed), ip)
    if commit:
        db.commit()
        _invalidate()
    return c


def manual_payment(db: Session, p, c: BizContract, amount, pay_date, remark: str | None, ip: str | None = None):
    try:
        amount = Decimal(str(amount))
    except (InvalidOperation, TypeError):
        raise bad_request("回款金额无效")
    if amount <= 0:
        raise bad_request("回款金额须大于 0")
    pay_date = pay_date if isinstance(pay_date, date) else date.fromisoformat(str(pay_date)[:10])
    if pay_date > today():
        raise bad_request("回款日期不能晚于今天")
    pay = payment_service.register_payment(db, c, amount, pay_date, source_type=2, user_id=p.id,
                                           remark=remark or "人工登记（无流水）")
    log_op(db, p.id, "contract.payment", "contract", c.id, None,
           {"amount": amount, "payment_date": pay_date, "payment_id": pay.id, "remark": remark}, ip)
    db.commit()
    _invalidate()
    return pay


def _invalidate():
    try:
        from app.services import board_service
        board_service.invalidate()
    except ImportError:
        pass


# ------------------------------------------------------------------ 归档 + 付款条款解析
def archive_key(c: BizContract) -> str:
    return f"contract/{c.contract_no}"


def extract_text(filename: str, data: bytes) -> tuple[str | None, str | None]:
    """返回 (text, warning)。支持 docx / txt；pdf 无解析库时提示转换。"""
    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if ext == "docx":
        from docx import Document
        try:
            doc = Document(io.BytesIO(data))
        except Exception as exc:  # noqa: BLE001
            raise BizError(42201, f"docx 解析失败：{exc}")
        parts = [para.text for para in doc.paragraphs]
        for t in doc.tables:
            for r in t.rows:
                parts.append("　".join(cell.text for cell in r.cells))
        return "\n".join(x for x in parts if x.strip()), None
    if ext in ("txt", "md"):
        for enc in ("utf-8", "gb18030"):
            try:
                return data.decode(enc), None
            except UnicodeDecodeError:
                continue
        raise BizError(42201, "文本编码无法识别（请使用 UTF-8 或 GBK）")
    if ext == "pdf":
        return None, "当前环境未安装 PDF 解析库，原件已归档；如需自动读取付款条款，请另存为 Word(docx) 后重新上传"
    if ext == "doc":
        return None, "旧版 .doc 无法解析，原件已归档；请另存为 docx 后重新上传以读取付款条款"
    raise BizError(42201, "仅支持 docx / txt / pdf 合同原件")


_CN_NUM = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
           "百": 100}


def _cn_to_int(s: str) -> int | None:
    if s.isdigit():
        return int(s)
    total, cur = 0, 0
    for ch in s:
        if ch not in _CN_NUM:
            return None
        v = _CN_NUM[ch]
        if v >= 10:
            total += (cur or 1) * v
            cur = 0
        else:
            cur = v
    return total + cur


STAGE_RULES = [  # 顺序重要：先质保再验收（“验收合格满一年后支付质保金”）
    ("质保金", ("质保", "保修金", "质量保证金")),
    ("预付款", ("预付", "首付", "定金", "签订后", "签订之日", "合同生效")),
    ("到货款", ("到货", "进场", "发货")),
    ("进度款", ("进度", "中期", "安装完成", "初验", "上线")),
    ("验收款", ("验收", "终验", "竣工")),
    ("尾款", ("尾款", "余款", "结算")),
]
PAY_WORDS = ("支付", "付款", "付清", "预付", "首付", "拨付", "结算", "质保金", "质量保证金", "保修金", "尾款", "余款")
EXCLUDE_WORDS = ("违约", "滞纳", "罚", "税率", "增值税", "赔偿", "利息")
PCT_RE = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*[%％]|百分之([零一二两三四五六七八九十百\d]+)")
DAYS_RE = re.compile(r"([零一二两三四五六七八九十百\d]+)\s*个?(工作日|日|天|个月|月|年)")


def _stage_of(sentence: str) -> str:
    for name, kws in STAGE_RULES:
        if any(k in sentence for k in kws):
            return name
    return "付款"


def _due(sentence: str, stage: str, sign: date, plan_end: date | None) -> tuple[date | None, str]:
    m = DAYS_RE.search(sentence)
    n = _cn_to_int(m.group(1)) if m else None
    unit = m.group(2) if m else None
    if n is not None and unit:
        delta = timedelta(days=int(n * 1.4)) if unit == "工作日" else timedelta(days=n) if unit in ("日", "天") \
            else timedelta(days=30 * n) if unit in ("个月", "月") else timedelta(days=365 * n)
    else:
        delta = timedelta(days=10 if stage == "预付款" else 30)
    if stage == "预付款" or "签订" in sentence or "生效" in sentence:
        base, basis = sign, "签订日"
    elif stage == "质保金":
        base = plan_end or sign + timedelta(days=240)
        basis = "验收日（估）"
        if n is None or unit not in ("年", "个月", "月"):
            delta = timedelta(days=365)
    elif stage == "到货款":
        base, basis = sign + timedelta(days=60), "到货（估：签订后 60 天）"
    elif stage == "进度款":
        base, basis = sign + timedelta(days=120), "进度节点（估：签订后 120 天）"
    else:
        base, basis = plan_end or sign + timedelta(days=240), "验收日（估）"
    return base + delta, f"{basis} + {delta.days} 天"


def parse_payment_terms(text: str, amount: Decimal, sign_date: date, plan_end: date | None = None) -> dict:
    """规则抽取付款条款 → 回款计划草案。返回 {stages:[...], ratio_total, warnings, clauses_found}。"""
    sentences = [s.strip() for s in re.split(r"[。；;\n]", text or "") if s.strip()]
    stages, warnings, seen = [], [], set()
    for s in sentences:
        if not any(w in s for w in PAY_WORDS) or any(w in s for w in EXCLUDE_WORDS):
            continue
        m = PCT_RE.search(s)
        if not m:
            continue
        ratio = Decimal(m.group(1)) if m.group(1) else Decimal(_cn_to_int(m.group(2)) or 0)
        if ratio <= 0 or ratio > 100:
            continue
        ratio = (ratio / 100).quantize(Decimal("0.0001"))
        key = (s, ratio)
        if key in seen:
            continue
        seen.add(key)
        stage = _stage_of(s)
        due, basis = _due(s, stage, sign_date, plan_end)
        stages.append({"stage_name": stage, "ratio": float(ratio), "amount": (amount * ratio).quantize(Decimal("1")),
                       "due_date": due, "due_basis": basis, "condition_text": s[:500]})
    # 同名阶段编号区分（如两笔进度款）
    names: dict[str, int] = {}
    for st in stages:
        names[st["stage_name"]] = names.get(st["stage_name"], 0) + 1
    counter: dict[str, int] = {}
    for i, st in enumerate(stages, start=1):
        st["seq"] = i
        if names[st["stage_name"]] > 1:
            counter[st["stage_name"]] = counter.get(st["stage_name"], 0) + 1
            st["stage_name"] = f"{st['stage_name']}{counter[st['stage_name']]}"
    total = sum((Decimal(str(s["ratio"])) for s in stages), ZERO)
    if stages and total == 1:
        stages[-1]["amount"] = amount - sum((s["amount"] for s in stages[:-1]), ZERO)  # 尾差并入最后一期
    if not stages:
        warnings.append("未识别到付款条款（需包含“支付/付款”与百分比），已保留原回款计划，请人工维护")
    elif total != 1:
        warnings.append(f"识别到的付款比例合计 {total * 100:.1f}%，不等于 100%，请核对后确认")
    return {"stages": stages, "ratio_total": float(total), "warnings": warnings, "clauses_found": len(stages)}


def apply_plans(db: Session, c: BizContract, stages: list[dict]) -> None:
    """按阶段生成/更新回款计划；保留已回款并按新计划重新冲抵。"""
    received = Decimal(db.scalar(select(func.coalesce(func.sum(BizPayment.amount), 0))
                                 .where(BizPayment.contract_id == c.id)))
    existing = sorted(c.plans, key=lambda x: x.seq)
    removed = [pl.id for pl in existing[len(stages):] if pl.id]
    if removed:
        db.execute(BizPayment.__table__.update().where(BizPayment.plan_id.in_(removed)).values(plan_id=None))
    for i, st in enumerate(stages):
        pl = existing[i] if i < len(existing) else None
        if pl is None:
            pl = BizPaymentPlan(seq=i + 1, stage_name=st["stage_name"], amount=ZERO)
            c.plans.append(pl)
        pl.seq = i + 1
        pl.stage_name = st["stage_name"][:64]
        pl.ratio = Decimal(str(st["ratio"])) if st.get("ratio") not in (None, "") else None
        amt = st.get("amount")
        pl.amount = Decimal(str(amt)) if amt not in (None, "") else (c.amount * (pl.ratio or 0)).quantize(Decimal("1"))
        due = st.get("due_date")
        pl.due_date = due if isinstance(due, date) or due is None else date.fromisoformat(str(due)[:10]) if due else None
        pl.condition_text = st.get("condition_text")
        pl.received_amount = ZERO
    for pl in existing[len(stages):]:
        c.plans.remove(pl)
    db.flush()
    if received > 0:
        payment_service.apply_to_plans(c, received)
    payment_service.refresh_plan_status(c)
    db.flush()


def _norm(s: str | None) -> str:
    return re.sub(r"[\s（）()·\-]", "", s or "")


def auto_link_flows(db: Session, c: BizContract, user_id: int | None = None, notify: bool = True) -> list[dict]:
    """在银行流水中查找该客户未核对的收入流水，金额等于某阶段应收额 → 自动登记回款（source_type=3）。"""
    if not c.plans:
        return []
    db.execute(select(BizContract.id).where(BizContract.id == c.id).with_for_update())
    cands = db.scalars(select(FinBankFlow).where(FinBankFlow.direction == 1, FinBankFlow.match_status == 0,
                                                 FinBankFlow.trade_date >= c.sign_date)
                       .order_by(FinBankFlow.trade_date, FinBankFlow.id)).all()
    cust = _norm(c.customer_name)
    linked = []
    for f in cands:
        if not cust or not (cust in _norm(f.counterparty) or _norm(f.counterparty) and _norm(f.counterparty) in cust):
            continue
        if db.scalar(select(BizPayment.id).where(BizPayment.bank_flow_id == f.id)):
            continue
        if payment_service.has_confirmed_receipt(db, c.id, f.amount, f.trade_date, c.customer_name, f.counterparty):
            continue
        open_plans = [pl for pl in sorted(c.plans, key=lambda x: x.seq) if pl.amount - pl.received_amount > 0]
        hit = next((pl for pl in open_plans if pl.amount - pl.received_amount == f.amount), None)
        if not hit:
            continue
        pay = payment_service.register_payment(db, c, f.amount, f.trade_date, flow=f, source_type=3, user_id=user_id,
                                               remark=f"合同归档自动关联：{hit.stage_name}")
        linked.append({"payment_id": pay.id, "bank_flow_id": f.id, "amount": f.amount, "trade_date": f.trade_date,
                       "counterparty": f.counterparty, "stage_name": hit.stage_name})
    if linked and notify:
        users = notify_service.users_by_roles(db, ["CW-01"])
        if c.owner_id:
            owner = db.get(SysUser, c.owner_id)
            if owner and owner not in users:
                users.append(owner)
        content = "\n".join(f"{x['trade_date']} {x['counterparty']} ¥{x['amount']:,.2f} → {x['stage_name']}" for x in linked)
        notify_service.send(db, users, "approval", f"合同 {c.contract_no} 已自动关联到账回款 {len(linked)} 笔",
                            f"客户：{c.customer_name}\n{content}", link=f"/contracts/{c.id}", ref_type="contract",
                            ref_id=str(c.id))
    return linked


def archive(db: Session, p, c: BizContract, filename: str, data: bytes, ip: str | None = None) -> dict:
    if not data:
        raise bad_request("文件为空")
    text, warning = extract_text(filename, data)
    f = save_bytes(db, data=data, filename=filename, category="contract", key=archive_key(c), uploader_id=p.id)
    before = {"status": c.status, "archive_file_id": c.archive_file_id, "plans": plan_rows(c)}
    c.archive_file_id, c.archived_at, c.status = f.id, utcnow(), 2
    parsed = {"stages": [], "ratio_total": 0.0, "warnings": [warning] if warning else [], "clauses_found": 0}
    linked: list[dict] = []
    if text is not None:
        plan_end = None
        if c.project_code:
            pj = db.scalar(select(BizProject).where(BizProject.project_code == c.project_code))
            plan_end = (pj.plan_end_date or (pj.milestones[-1].plan_date if pj and pj.milestones else None)) if pj else None
        parsed = parse_payment_terms(text, c.amount, c.sign_date, plan_end)
        if parsed["stages"]:
            apply_plans(db, c, parsed["stages"])
            linked = auto_link_flows(db, c, user_id=p.id)
    log_op(db, p.id, "contract.archive", "contract", c.id, before,
           {"status": 2, "archive_file_id": f.id, "version": f.version, "parsed_stages": len(parsed["stages"]),
            "linked": [x["bank_flow_id"] for x in linked]}, ip)
    db.commit()
    db.refresh(c)
    _invalidate()
    return {"file": file_brief(f), "parsed": parsed, "linked": linked, "plans": plan_rows(c),
            "text_excerpt": (text or "")[:1500]}


def save_plans(db: Session, p, c: BizContract, stages: list[dict], ip: str | None = None) -> dict:
    if not stages:
        raise bad_request("回款计划不能为空")
    total = sum((Decimal(str(s.get("amount") or 0)) for s in stages), ZERO)
    before = plan_rows(c)
    apply_plans(db, c, stages)
    linked = auto_link_flows(db, c, user_id=p.id)
    log_op(db, p.id, "contract.plans", "contract", c.id, {"plans": before}, {"plans": plan_rows(c)}, ip)
    db.commit()
    db.refresh(c)
    _invalidate()
    warn = [] if total == c.amount else [f"计划金额合计 ¥{total:,.2f} 与合同金额 ¥{c.amount:,.2f} 不一致"]
    return {"plans": plan_rows(c), "linked": linked, "warnings": warn}


def refresh_archived(db: Session) -> dict:
    """每周任务：已归档合同 → 自动关联新到账流水；刷新回款计划状态（逾期/待回款）。"""
    contracts = db.scalars(select(BizContract).where(BizContract.is_deleted.is_(False), BizContract.status == 2)).all()
    linked_total = []
    for c in contracts:
        before = [(pl.id, pl.status) for pl in c.plans]
        linked = auto_link_flows(db, c, user_id=None)
        payment_service.refresh_plan_status(c)
        linked_total += [{**x, "contract_no": c.contract_no} for x in linked]
        _ = before
    db.commit()
    _invalidate()
    return {"rows": len(linked_total), "contracts": len(contracts), "linked": linked_total,
            "message": f"扫描已归档合同 {len(contracts)} 份，自动关联到账回款 {len(linked_total)} 笔"
                       + (f"（¥{sum((x['amount'] for x in linked_total), ZERO):,.2f}）" if linked_total else "")}

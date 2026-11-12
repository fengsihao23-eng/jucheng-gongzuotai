"""企微审批事件分发（FR-05-04 / FR-05-05 / FR-07-02 / FR-07-03）。

审批通过 → 按 sys_dict(approval_template) 模板映射 → 合同台账 / 报销归集 / 商机信息表。
- 以 sp_no 幂等：同一审批单重复推送只登记一次
- 处理失败重试 3 次后进入人工待处理队列（dispatch_status=2）并告警
- 报销缺「所属项目」或项目不存在 → 进入待归集池（status=2 已通过，未归集）；选“公共费用” → 归集为公共费用
"""
import logging
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import log_op
from app.core.permissions import BUSINESS_LINES
from app.core.response import BizError
from app.core.timeutil import TZ, now, today, utcnow
from app.integration.wecom import get_wecom
from app.integration.wecom.base import ApprovalDto
from app.models import (BizContract, BizExpenseItem, BizExpenseReport, BizOpportunity, BizProject,
                        SyncWecomApproval, SysUser)
from app.services import codes, dict_service, notify_service, receivables_fields, receivables_service

log = logging.getLogger("dispatch")
MAX_RETRY = 3
PUBLIC_EXPENSE_LABELS = {"公共费用", "公共", "PUBLIC", "无"}
STAGE_LABELS = {"线索": "lead", "初步接触": "contact", "方案报价": "quote", "商务谈判": "negotiation",
                "赢单": "won", "丢单": "lost"}


class DispatchError(Exception):
    pass


def _templates(db: Session) -> dict[str, dict]:
    """template_id → {key, fields{企微控件标题: 标准字段}}"""
    return dict_service.items(db, "approval_template")


def _money(v: str | None, field: str) -> Decimal:
    try:
        d = Decimal(str(v).replace(",", "").replace("¥", "").replace("元", "").strip())
    except (InvalidOperation, AttributeError):
        raise DispatchError(f"字段「{field}」金额无效：{v!r}")
    if not d.is_finite() or d <= 0 or d >= 10**12 or d != d.quantize(Decimal("0.01")):
        raise DispatchError(f"字段「{field}」须为有效金额且最多两位小数")
    return d


def _date(v: str | None) -> date | None:
    if not v:
        return None
    try:
        return date.fromisoformat(v[:10])
    except ValueError:
        raise DispatchError(f"日期格式无效：{v!r}")


def _map_fields(tpl: dict, fields: dict[str, str]) -> dict[str, str]:
    mapping = tpl.get("fields", {})
    return {std: fields.get(title, "") for title, std in mapping.items()}


def _approved_date(a: ApprovalDto) -> date:
    t = a.approved_time or a.apply_time
    return t.astimezone(TZ).date()


def _user(db: Session, userid: str) -> SysUser | None:
    return db.scalar(select(SysUser).where(SysUser.wecom_userid == userid))


# ---------------- 各模板登记动作 ----------------
def _register_contract(db: Session, a: ApprovalDto, f: dict) -> str:
    if not (f.get("customer_name") or "").strip():
        raise DispatchError("缺少字段「客户名称」")
    amount = _money(f.get("amount"), "合同金额")
    number = (f.get("contract_no") or "").strip()
    sign_date = _date(f.get("sign_date"))
    if sign_date and sign_date > today():
        raise DispatchError("签订日期不能晚于今天")
    if number:
        existing = db.scalar(select(BizContract).where(BizContract.contract_no == number).with_for_update())
        if existing:
            if (existing.is_deleted or existing.status == 3 or existing.amount != amount
                    or existing.customer_name.strip() != f["customer_name"].strip()
                    or (sign_date and existing.sign_date != sign_date)
                    or existing.source_approval_no not in (None, a.sp_no)):
                raise DispatchError("合同编号已有记录且内容或来源不一致，请人工核对")
            existing.source_approval_no = a.sp_no
            log_op(db, None, "contract.link_approval", "contract", existing.id, after={"sp_no": a.sp_no})
            return existing.contract_no
    project = db.scalar(select(BizProject).where(BizProject.project_code == f.get("project_code"))) \
        if f.get("project_code") else None
    line = f.get("business_line") or (project.business_line if project else None)
    if not line:
        raise DispatchError("缺少业务线且未关联项目，无法统计业绩")
    line = line.strip()
    known_lines = {**BUSINESS_LINES, **dict_service.items(db, "business_line")}
    label_codes = {(label if isinstance(label, str) else label.get("name", code)): code
                   for code, label in known_lines.items() if isinstance(label, (str, dict))}
    line_code = label_codes.get(line, line[:2].upper())
    if line_code not in known_lines:
        raise DispatchError("业务线不在当前字典中，请核对审批选项")
    applicant = _user(db, a.applicant_userid)
    c = BizContract(contract_no=number or codes.contract_no(db, (sign_date or _approved_date(a)).year),
                    name=f.get("name") or a.sp_name, project_code=project.project_code if project else None,
                    customer_name=f["customer_name"].strip(), amount=amount, sign_date=sign_date or _approved_date(a),
                    business_line=line_code, region=(f.get("region") or "").strip() or None,
                    owner_id=applicant.id if applicant else None,
                    source_approval_no=a.sp_no, source_type=1, status=1, remark=f.get("remark"))
    db.add(c)
    if project and project.contract_amount is None:
        project.contract_amount = amount
    if project and project.status == 1:
        project.status = 2  # 中标签约 → 在建
    db.flush()
    # 业绩通知：营销管控 + 总办领导
    notify_service.send(db, notify_service.users_by_roles(db, ["YX-01", "ZB-01"]), "approval",
                        f"新合同已自动登记：{c.contract_no}",
                        f"客户：{c.customer_name}\n金额：¥{amount:,.2f}\n签订日：{c.sign_date}\n来源审批：{a.sp_no}",
                        link=f"/contracts/{c.id}", ref_type="contract", ref_id=str(c.id))
    return c.contract_no


def _register_expense(db: Session, a: ApprovalDto, f: dict) -> str:
    amount = _money(f.get("amount"), "报销金额")
    applicant = _user(db, a.applicant_userid)
    raw_code = (f.get("project_code") or "").strip()
    code = raw_code.split(" ")[0] if raw_code else ""  # 选项文本可能是「JC-XY-2026-001 XX学校」
    status, project_code = 2, None  # 默认：已通过、待归集
    if code in PUBLIC_EXPENSE_LABELS or raw_code in PUBLIC_EXPENSE_LABELS:
        status = 4  # 归集为公共费用
    elif code and db.scalar(select(BizProject.id).where(BizProject.project_code == code, BizProject.is_deleted.is_(False))):
        status, project_code = 4, code
    r = BizExpenseReport(approval_no=a.sp_no, applicant_id=applicant.id if applicant else None,
                         dept_code=applicant.dept_code if applicant else None, project_code=project_code,
                         amount=amount, expense_date=_date(f.get("expense_date")) or _approved_date(a),
                         approved_at=a.approved_time or a.apply_time, category=f.get("category") or "其他",
                         reason=f.get("reason"), status=status, detail={"fields": a.fields, "raw_project": raw_code})
    r.items = [BizExpenseItem(item_name=f.get("category") or "费用", amount=amount,
                              invoice_count=int(f.get("invoice_count") or 1))]
    db.add(r)
    db.flush()
    if status == 2:
        notify_service.send(db, notify_service.users_by_roles(db, ["CW-01", "CW-03"]), "alert",
                            f"报销单待归集：{a.sp_no}",
                            f"申请人 {applicant.name if applicant else a.applicant_userid}，金额 ¥{amount:,.2f}，"
                            f"所属项目「{raw_code or '未填写'}」无法匹配，请在报销归集页补充。",
                            link="/expenses?status=pending", ref_type="expense", ref_id=a.sp_no, wecom=False)
    return a.sp_no


def _register_procurement(db: Session, a: ApprovalDto, f: dict) -> str:
    # Finance must explicitly map the approved procurement template. The same approval can be counted only once.
    return _register_expense(db, a, {**f, "category": "采购"})


def _register_opportunity(db: Session, a: ApprovalDto, f: dict) -> str:
    if not f.get("customer_name"):
        raise DispatchError("缺少字段「客户名称」")
    stage = STAGE_LABELS.get(f.get("stage", ""), f.get("stage") or "lead")
    applicant = _user(db, a.applicant_userid)
    o = BizOpportunity(opp_no=codes.opp_no(db, _approved_date(a).year), name=f.get("name") or a.sp_name,
                       customer_name=f["customer_name"], business_line=(f.get("business_line") or "XY")[:2].upper(),
                       stage=stage, amount=_money(f.get("amount"), "预计金额"),
                       expected_sign_date=_date(f.get("expected_sign_date")),
                       owner_id=applicant.id if applicant else None, source_approval_no=a.sp_no, source_type=1,
                       status={"won": "won", "lost": "lost"}.get(stage, "open"), remark=f.get("remark"))
    db.add(o)
    db.flush()
    return o.opp_no


def _request_invoice(db, a, values):
    data = receivables_fields.parse(receivables_fields.InvoiceRequest, values)
    row = receivables_service.request_invoice(db, **data.model_dump(), approval_no=a.sp_no)
    return str(row.id)


def _confirm_receipt(db, a, values):
    data = receivables_fields.parse(receivables_fields.Receipt, values)
    row = receivables_service.receipt(db, **data.model_dump(), approval_no=a.sp_no)
    return str(row.id)


HANDLERS = {"contract": _register_contract, "expense": _register_expense, "opportunity": _register_opportunity,
            "invoice": _request_invoice, "receipt": _confirm_receipt, "procurement": _register_procurement}
LEDGER_KEYS = {"contract", "invoice", "receipt", "procurement"}


# ---------------- 主流程 ----------------
def ingest(db: Session, sp_no: str, via: str = "callback", detail: ApprovalDto | None = None) -> SyncWecomApproval:
    """拉取审批详情 → 落 sync_wecom_approval → 审批通过则分发登记。可重复调用（幂等）。"""
    a = detail or get_wecom().get_approval_detail(sp_no)
    tpl = _templates(db).get(a.template_id) or {}
    key = tpl.get("key", "unknown")
    if key in {"invoice", "receipt"}:
        receivables_service.lock(db)
    rec = db.scalar(select(SyncWecomApproval).where(SyncWecomApproval.sp_no == sp_no).with_for_update())
    if not rec:
        rec = SyncWecomApproval(sp_no=sp_no, template_key=key, received_via=via, status=a.sp_status)
        db.add(rec)
        try:
            db.flush()
        except IntegrityError:  # 并发回调：另一请求已插入
            db.rollback()
            if key in {"invoice", "receipt"}:
                receivables_service.lock(db)
            rec = db.scalar(select(SyncWecomApproval).where(SyncWecomApproval.sp_no == sp_no).with_for_update())
    old_fields = (rec.detail or {}).get("fields") or {}
    changed = rec.dispatched_at and key in LEDGER_KEYS and (
        rec.status != a.sp_status or _map_fields(tpl, old_fields) != _map_fields(tpl, a.fields))
    rec.template_id, rec.template_key, rec.sp_name = a.template_id, key, a.sp_name
    rec.applicant_userid, rec.status, rec.apply_time = a.applicant_userid, a.sp_status, a.apply_time
    rec.detail = {"fields": a.fields, "approved_time": a.approved_time.isoformat() if a.approved_time else None}
    if via == "reconcile" and not rec.dispatched_at:
        rec.updated_at = utcnow()  # 轮转长期在途单，避免被相同的前 100 单占满。

    if changed:
        rec.dispatch_status, rec.error_msg = 2, "已入账审批的状态或业务字段已变化，请核对台账并人工更正"
        log_op(db, None, "approval.ledger_changed", "approval", sp_no,
               after={"status": a.sp_status, "target": rec.dispatch_target})
    if rec.dispatched_at or (rec.dispatch_status == 3 and key not in HANDLERS):
        db.commit()
        return rec  # 已处理：幂等
    if key not in HANDLERS or a.sp_status != 2:
        rec.dispatch_status = 3 if key not in HANDLERS else 0  # 非映射模板无需处理；未通过的等待后续状态变化
        db.commit()
        return rec
    try:
        with db.begin_nested():
            mapped = _map_fields(tpl, a.fields)
            if key == "contract" and tpl.get("require_contract_no") and not mapped.get("contract_no", "").strip():
                raise DispatchError("该合同模板必须提供正式合同编号")
            rec.dispatch_target = HANDLERS[key](db, a, mapped)
        rec.dispatch_status, rec.dispatched_at, rec.error_msg = 1, utcnow(), None
    except IntegrityError as exc:
        rec.dispatch_status, rec.error_msg = 2, f"登记冲突（重复单号？）：{exc.orig}"[:500]
        rec.retry_count = MAX_RETRY
    except Exception as exc:  # noqa: BLE001
        rec.retry_count += 1
        rec.error_msg = str(exc)[:500]
        rec.dispatch_status = 2 if rec.retry_count >= MAX_RETRY or isinstance(exc, (BizError, DispatchError)) else 0
        log.warning("dispatch %s failed (%s): %s", sp_no, rec.retry_count, exc)
    if rec.dispatch_status == 2:
        notify_service.send(db, notify_service.users_by_roles(db, ["ADMIN-01", "CW-01"] if key in {"expense", "invoice", "receipt"} else ["ADMIN-01", "YX-01"]),
                            "alert", f"审批自动登记失败，已转人工：{sp_no}", f"{a.sp_name}：{rec.error_msg}",
                            link="/admin?tab=approvals", ref_type="approval", ref_id=sp_no)
    db.commit()
    if rec.dispatch_status == 1 and key in LEDGER_KEYS:
        receivables_service.invalidate()
    return rec


def reconcile(db: Session, hours: int = 24) -> dict:
    """审批兜底对账：拉取近 N 小时审批单，补齐回调丢失与待重试项。"""
    end = now()
    recent = get_wecom().get_approval_list(end - timedelta(hours=hours), end)
    pending = db.scalars(select(SyncWecomApproval.sp_no).where(SyncWecomApproval.dispatch_status == 0,
                         SyncWecomApproval.template_key.in_(LEDGER_KEYS))
                         .order_by(SyncWecomApproval.updated_at, SyncWecomApproval.id).limit(100))
    sp_nos = list(dict.fromkeys([*recent, *pending]))
    known = {r.sp_no: r for r in db.scalars(select(SyncWecomApproval).where(SyncWecomApproval.sp_no.in_(sp_nos)))}
    added = retried = revisited = 0
    for sp in sp_nos:
        r = known.get(sp)
        if r and (r.dispatch_status in (2, 3) or
                  (r.dispatch_status == 1 and r.template_key not in LEDGER_KEYS)):
            continue
        added += r is None
        retried += r is not None and r.dispatch_status != 1
        revisited += r is not None and r.dispatch_status == 1
        ingest(db, sp, via="reconcile")
    return {"rows": added + retried, "message": f"拉取 {len(sp_nos)} 单，补登 {added}，重试 {retried}，复核 {revisited}"}


def manual_retry(db: Session, sp_no: str) -> SyncWecomApproval:
    rec = db.scalar(select(SyncWecomApproval).where(SyncWecomApproval.sp_no == sp_no))
    if rec and rec.dispatch_status == 2 and not rec.dispatched_at:
        rec.dispatch_status, rec.retry_count = 0, 0
        db.flush()
    return ingest(db, sp_no, via="manual")

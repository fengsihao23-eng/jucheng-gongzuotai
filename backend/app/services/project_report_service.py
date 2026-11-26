"""历史项目报表清洗、可重复导入与财务范围查询。"""
import hashlib
import json
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select, text

from app.core.audit import log_op
from app.core.response import bad_request, conflict, forbidden, not_found
from app.core.storage import file_brief, save_bytes
from app.models import (BizContract, BizPayment, BizProject, BizProjectCost, FinProjectReport,
    FinProjectReportContract, FinProjectReportEntry, FinReceipt, SysFile)
from app.services import project_service, receivables_service
from app.services.project_report_parser import COST_KINDS, ZERO, decimal, money, parse_report, read_archive


def finance_viewer(p):
    return p.is_admin or p.has_menu("profit") or p.has_menu("board")


def contract_rows(db, report):
    return db.scalars(select(BizContract).join(FinProjectReportContract,
        FinProjectReportContract.contract_id == BizContract.id).where(
        FinProjectReportContract.report_id == report.id).order_by(BizContract.id)).all()


def can_view(db, p, report):
    if not finance_viewer(p):
        return False
    if p.can_view_all_projects:
        return True
    codes = {c.project_code for c in contract_rows(db, report)}
    projects = db.scalars(select(BizProject).where(BizProject.project_code.in_(codes), BizProject.is_deleted.is_(False))).unique().all()
    return bool(codes) and len(projects) == len(codes) and all(project_service.can_view(db, p, project) for project in projects)


def get_report(db, p, ident):
    row = db.get(FinProjectReport, ident)
    if row is None or not can_view(db, p, row):
        raise not_found("项目来源报表")
    return row


def _payment_total(db, ids, cutoff):
    return db.scalar(select(func.coalesce(func.sum(BizPayment.amount), 0)).where(
        BizPayment.contract_id.in_(ids), BizPayment.payment_date <= cutoff))


def prepare_archive(db, p, data, mapping):
    if not p.is_admin or p.data_scope != "all":
        raise forbidden("历史项目报表批量导入须由平台管理员执行")
    if mapping.get("schema_version") != 1 or mapping.get("archive_sha256") != hashlib.sha256(data).hexdigest():
        raise bad_request("核对清单版本或 ZIP 校验和不符")
    files = read_archive(data)
    mapped = mapping.get("reports", [])
    if len(mapped) != len(files) or len({m.get("filename") for m in mapped}) != len(mapped):
        raise bad_request("核对清单文件数或名称重复")
    by_name = {m["filename"]: m for m in mapped}
    if set(by_name) != {name for name, _ in files}:
        raise bad_request("核对清单与 ZIP 报表文件不一致")
    plans, seen_contracts = [], set()
    for name, raw in files:
        item = parse_report(raw, name)
        spec = by_name[name]
        if item["sha256"] != spec.get("sha256"):
            raise bad_request(f"报表校验和不符：{name}")
        ids = spec.get("contract_ids", [])
        if not ids or len(ids) != len(set(ids)) or spec.get("primary_contract_id") not in ids:
            raise bad_request("合同关联清单无效")
        contracts = db.scalars(select(BizContract).where(BizContract.id.in_(ids)).with_for_update()).all()
        if len(contracts) != len(ids) or any(c.is_deleted or c.status == 3 for c in contracts):
            raise bad_request("关联合同不存在或已停用")
        if sum((c.amount for c in contracts), ZERO) != money(item["summary"]["totals"]["contract"]):
            raise conflict(f"报表合同额与关联合同合计不符：{name}")
        # The reviewed identity prevents matching a different equal-value contract.
        actual = {str(c.id): {"contract_no": c.contract_no, "name": c.name, "amount": str(money(c.amount)),
                              "project_code": c.project_code} for c in contracts}
        if actual != spec.get("contracts"):
            raise conflict(f"关联合同在核对后发生变化：{name}")
        for c in contracts:
            if (c.id, item["period"]) in seen_contracts:
                raise conflict("同一合同同一月份不能累计两份报表")
            seen_contracts.add((c.id, item["period"]))
            project = db.scalar(select(BizProject).where(BizProject.project_code == c.project_code))
            if not project or project.is_deleted:
                raise conflict("合同关联项目不存在或已删除")
        existing = db.scalar(select(FinProjectReport).where(FinProjectReport.source_sha256 == item["sha256"]))
        if existing:
            if {c.id for c in contract_rows(db, existing)} != set(ids):
                raise conflict("相同来源文件已挂接其他合同")
        other = db.scalar(select(FinProjectReport.id).join(FinProjectReportContract,
            FinProjectReportContract.report_id == FinProjectReport.id).where(
            FinProjectReportContract.contract_id.in_(ids), FinProjectReport.period == item["period"],
            FinProjectReport.source_sha256 != item["sha256"]))
        if other:
            raise conflict("同一合同同一月份已有其他版本；须先核对差异，不能直接重复累计")
        primary = next(c for c in contracts if c.id == spec["primary_contract_id"])
        total = _payment_total(db, ids, date.fromisoformat(item["summary"]["cutoff"]))
        source_total = money(item["summary"]["totals"]["received"])
        item["summary"]["ledger_received"] = str(total)
        if total != source_total:
            item["issues"].append({"code": "received_difference", "message": "源表累计已收与现有回款台账不一致，保留原台账",
                                   "reported": str(source_total), "calculated": str(total), "difference": str(source_total - total)})
        if len(contracts) > 1:
            item["issues"].append({"code": "combined_contracts", "message": "本表合并多份合同；统一利润按完整项目组合并核算，未向单份合同分摊",
                                   "primary_project_code": primary.project_code})
        item.update({"raw": raw, "contracts": contracts, "primary": primary, "existing": existing})
        plans.append(item)
    return plans


def preview(plans):
    return [{"filename": item["filename"], "already_imported": item["existing"] is not None,
             "project_code": item["primary"].project_code, "contract_ids": [c.id for c in item["contracts"]],
             "rows": len(item["entries"]), "summary": item["summary"], "issues": item["issues"]} for item in plans]


def apply_archive(db, p, data, mapping, *, persist_files=True):
    # Serialize version and row-level deduplication across all imports.
    db.execute(text("select pg_advisory_xact_lock(621107260906)"))
    plans = prepare_archive(db, p, data, mapping)
    result = []
    for item in plans:
        if item["existing"]:
            result.append({"id": item["existing"].id, "already_imported": True})
            continue
        if not persist_files:
            raise bad_request("正式导入必须保存原件")
        file = save_bytes(db, data=item["raw"], filename=item["filename"], category="project_finance_source",
                          key=f"project_finance/{item['period']}/{item['sha256']}", uploader_id=p.id)
        report = FinProjectReport(source_sha256=item["sha256"], source_name=item["filename"],
            archive_sha256=mapping["archive_sha256"], period=item["period"], primary_project_code=item["primary"].project_code,
            file_id=file.id, operator_id=p.id, summary=item["summary"], issues=item["issues"], source_cells=item["sheets"])
        db.add(report)
        db.flush()
        db.add_all(FinProjectReportContract(report_id=report.id, contract_id=c.id) for c in item["contracts"])
        db.flush()
        for values in item["entries"]:
            entry = FinProjectReportEntry(report_id=report.id, sheet=values["sheet"], source_row=values["source_row"],
                kind=values["kind"], fingerprint=values["fingerprint"], data=dict(values))
            db.add(entry)
            if entry.kind in COST_KINDS and values["posting_state"] == "ready":
                # A reused voucher is one cost even when a later cumulative report repeats it.
                previous = db.scalar(select(FinProjectReportEntry).join(FinProjectReport,
                    FinProjectReport.id == FinProjectReportEntry.report_id).where(
                    FinProjectReport.primary_project_code == report.primary_project_code,
                    FinProjectReportEntry.fingerprint == entry.fingerprint,
                    FinProjectReportEntry.cost_id.is_not(None)).order_by(FinProjectReportEntry.id))
                summary = f"{values['voucher']} {values['summary']}"[:255]
                cost = db.get(BizProjectCost, previous.cost_id) if previous else db.scalar(select(BizProjectCost).where(
                    BizProjectCost.project_code == report.primary_project_code,
                    BizProjectCost.cost_date == date.fromisoformat(values["date"]),
                    BizProjectCost.amount == money(values["amount"]), BizProjectCost.summary == summary,
                    BizProjectCost.cost_type == values["cost_type"]))
                reused = cost is not None
                if cost is None:
                    cost = BizProjectCost(project_code=report.primary_project_code, cost_date=date.fromisoformat(values["date"]),
                        cost_type=values["cost_type"], amount=money(values["amount"]), summary=summary, created_by=p.id)
                    db.add(cost)
                    db.flush()
                    log_op(db, p.id, "project_finance.cost.import", "project_cost", cost.id,
                           after={"report_id": report.id, "sheet": entry.sheet, "row": entry.source_row, "amount": cost.amount})
                if (cost.project_code, cost.amount, cost.cost_date, cost.cost_type) != (report.primary_project_code,
                    money(values["amount"]), date.fromisoformat(values["date"]), values["cost_type"]):
                    raise conflict("已挂接历史凭证的正式成本发生变化，停止导入")
                entry.cost_id = cost.id
                entry.data = {**entry.data, "posting_state": "reused" if reused else "posted"}
            elif entry.kind == "receipt":
                _link_receipt(db, p, report, item["contracts"], entry)
            db.flush()
        check = verify_report(db, report)
        if check["errors"]:
            raise conflict("导入来源链核验失败", check)
        log_op(db, p.id, "project_finance.report.import", "project_report", report.id,
               after={"source_sha256": report.source_sha256, "rows": len(item["entries"]),
                      "contract_ids": [c.id for c in item["contracts"]], "verification": check})
        result.append({"id": report.id, "already_imported": False, **check})
    return result


def _link_receipt(db, p, report, contracts, entry):
    values = entry.data
    dt, amount = date.fromisoformat(values["date"]), money(values["amount"])
    if amount <= 0:
        entry.data = {**values, "posting_state": "reference", "link_note": "非正数回款须核对原到账凭证"}
        return
    # Source dates are voucher booking dates. A unique existing payment in the same
    # month supplies the actual bank receipt date; this importer never creates a payment.
    candidates = db.scalars(select(BizPayment).where(BizPayment.contract_id.in_([c.id for c in contracts]),
        BizPayment.amount == amount, func.date_trunc("month", BizPayment.payment_date) == dt.replace(day=1))).all()
    if len(candidates) != 1:
        entry.data = {**values, "posting_state": "reference", "link_note": "未唯一匹配现有回款，未新增到账"}
        return
    pay = candidates[0]
    contract = next(c for c in contracts if c.id == pay.contract_id)
    existing = db.scalar(select(FinReceipt).where(FinReceipt.payment_id == pay.id))
    if existing and (existing.state != "confirmed" or existing.amount != amount or existing.contract_id != pay.contract_id):
        raise conflict("现有到账记录与回款不一致")
    receipt = existing or receivables_service.receipt(db,
        reference=f"PROJECT-{contract.id}-{entry.fingerprint[:32]}", received_on=pay.payment_date, amount=amount,
        payer=values.get("supplier") or contract.customer_name, account=values["summary"].split("-")[0][:64],
        kind="contract", contract_no=contract.contract_no, payment_id=pay.id,
        note=f"项目报表 {report.id} {entry.sheet}!{entry.source_row}；源凭证记账日 {dt}；保留原回款真实到账日。", actor=p.id)
    entry.payment_id, entry.receipt_id = pay.id, receipt.id
    entry.data = {**values, "posting_state": "linked", "received_on": pay.payment_date.isoformat(),
                  "link_note": "挂接已有回款；源日期为凭证记账日"}


def verify_report(db, report):
    rows = db.scalars(select(FinProjectReportEntry).where(FinProjectReportEntry.report_id == report.id)).all()
    errors, cost_ids, amount, held = [], set(), ZERO, 0
    for row in rows:
        if row.kind in COST_KINDS:
            if row.data["posting_state"] == "held":
                held += 1
                if row.cost_id:
                    errors.append(f"待核对行不应有正式成本：{row.id}")
            else:
                cost = db.get(BizProjectCost, row.cost_id) if row.cost_id else None
                if not cost or (cost.amount, cost.cost_date, cost.project_code, cost.cost_type) != (
                    money(row.data["amount"]), date.fromisoformat(row.data["date"]), report.primary_project_code, row.data["cost_type"]):
                    errors.append(f"成本来源行不一致：{row.id}")
                elif cost.id not in cost_ids:
                    amount += cost.amount
                    cost_ids.add(cost.id)
        if row.payment_id:
            pay, receipt = db.get(BizPayment, row.payment_id), db.get(FinReceipt, row.receipt_id)
            if not pay or not receipt or receipt.payment_id != pay.id or pay.amount != money(row.data["amount"]) or receipt.amount != pay.amount:
                errors.append(f"回款挂接不一致：{row.id}")
    return {"rows": len(rows), "cost_rows": len(cost_ids), "posted_cost": str(amount), "held_rows": held,
            "payable_rows": sum(r.kind == "payable" for r in rows),
            "linked_payments": sum(r.payment_id is not None for r in rows), "errors": errors}


def report_view(db, report, *, detail=False):
    contracts = contract_rows(db, report)
    verification = verify_report(db, report)
    cutoff = date.fromisoformat(report.summary["cutoff"])
    out = {"id": report.id, "source_name": report.source_name, "period": report.period,
        "primary_project_code": report.primary_project_code, "source_sha256": report.source_sha256,
        "contracts": [{"id": c.id, "contract_no": c.contract_no, "name": c.name, "amount": c.amount,
                       "project_code": c.project_code} for c in contracts], "summary": report.summary,
        "issue_count": len(report.issues), "verification": verification,
        "current_ledger_received": _payment_total(db, [c.id for c in contracts], cutoff)}
    if detail:
        out.update({"issues": report.issues, "file": file_brief(db.get(SysFile, report.file_id)),
            "entries": [{"id": r.id, **r.data, "cost_id": r.cost_id, "payment_id": r.payment_id, "receipt_id": r.receipt_id}
                        for r in db.scalars(select(FinProjectReportEntry).where(
                        FinProjectReportEntry.report_id == report.id).order_by(FinProjectReportEntry.id))],
            "source_cells": report.source_cells})
    return out


def list_reports(db, p, *, project_code=None, contract_id=None, period=None, page=1, size=30):
    if not finance_viewer(p):
        raise forbidden()
    q = select(FinProjectReport).order_by(FinProjectReport.period.desc(), FinProjectReport.id)
    if project_code or contract_id:
        sub = select(FinProjectReportContract.report_id).join(BizContract, BizContract.id == FinProjectReportContract.contract_id)
        if project_code:
            sub = sub.where(BizContract.project_code == project_code)
        if contract_id:
            sub = sub.where(BizContract.id == contract_id)
        q = q.where(FinProjectReport.id.in_(sub))
    if period:
        q = q.where(FinProjectReport.period == period)
    visible = [r for r in db.scalars(q) if can_view(db, p, r)]
    return {"items": [report_view(db, r) for r in visible[(page - 1) * size:page * size]],
            "total": len(visible), "page": page, "page_size": size}


def refresh_report_snapshots(db, actor, source_ids):
    """为受历史凭证影响的已存报表生成新版本；旧快照保留，不对外发送。"""
    from app.core.timeutil import today, week_no
    from app.models import RptReport
    from app.services import report_service
    kinds = ["weekly_profit", "project_operation", "company_monthly"]
    latest = {}
    for row in db.scalars(select(RptReport).where(RptReport.report_type.in_(kinds)).order_by(RptReport.version)):
        latest[(row.report_type, row.period)] = (row.metrics or {}).get("_meta", {}).get("project_codes")
    latest.setdefault(("weekly_profit", week_no(today())), None)
    out = []
    for (kind, period), codes in latest.items():
        report = report_service.generate(db, kind, period, trigger="import", user_id=actor, project_codes=codes)
        report.metrics = {**report.metrics, "_source_refresh": {"kind": "project_finance", "source_ids": source_ids}}
        out.append({"id": report.id, "report_type": kind, "period": period, "version": report.version})
    db.flush()
    return out

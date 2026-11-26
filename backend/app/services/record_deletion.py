"""Record-level authorization and removal rules for the shared deletion menu."""
from dataclasses import dataclass

from sqlalchemy import select

from app import models as m
from app.core.audit import log_op
from app.core.deps import owner_scope
from app.core.response import conflict, forbidden, not_found
from app.services import (
    business_workbook, collection_service, key_projects, operations_competitors,
    operations_policy, operations_scope, operations_service, payment_service, project_service,
    record_followup_targets as records, record_removals, receivables_service,
)


@dataclass(frozen=True)
class VisibleRecord:
    model: type
    permissions: tuple[str, ...]


# These resources reuse the same record and module scopes as their detail and feedback pages.
RECORDS = {
    "expense": VisibleRecord(m.BizExpenseReport, ("expense:allocate",)),
    "bank-flow": VisibleRecord(m.FinBankFlow, ("flows:import", "flows:match")),
    "bank-import": VisibleRecord(m.FinBankImport, ("flows:import",)),
    "finance-batch": VisibleRecord(m.SyncLemonBatch, ("finance:sync",)),
    "tax": VisibleRecord(m.SyncTaxFiling, ("tax:upload",)),
    "invoice": VisibleRecord(m.FinSalesInvoice, ("receivables:correct",)),
    "receivables-source": VisibleRecord(m.FinReceivablesSourceRow, ("receivables:source",)),
    "review": VisibleRecord(m.BizProjectReview, ("review:write",)),
    "ticket": VisibleRecord(m.CsTicket, ("ticket:write",)),
    "service-report": VisibleRecord(m.RptReport, ("ticket:write",)),
    "tender-check": VisibleRecord(m.ProdBidCheck, ("tender:write",)),
    "tender-generation": VisibleRecord(m.ProdGeneration, ("tender:write",)),
    "hr-candidate": VisibleRecord(m.HrCandidate, ("hr:write",)),
    "hr-alert": VisibleRecord(m.HrContractAlert, ("hr:write",)),
    "hr-doc": VisibleRecord(m.HrDocument, ("hr:write",)),
    "report": VisibleRecord(m.RptReport, ("report:generate",)),
    "market-report": VisibleRecord(m.RptReport, ("report:generate",)),
    "performance-report": VisibleRecord(m.RptReport, ("report:generate",)),
    "approval": VisibleRecord(m.SyncWecomApproval, ("contracts:write",)),
    "clean": VisibleRecord(m.CleanJob, ("clean:import",)),
}


# Some school records inherit their project through the school rather than a project column.
PROJECT_RECORDS = {
    "operation-competitor": (m.OpsCompetitor, "competitors", "name"),
    "operation-user": (m.OpsProjectUser, "user_list", "name"),
    "operation-product": (m.OpsProduct, "products", "name"),
    "operation-goal": (m.OpsOutreachGoal, "outreach", "year"),
    "operation-progress": (m.OpsProjectProgress, "progress", "progress"),
    "maintenance": (m.OpsMaintenanceRecord, None, "title"),
    "asset-movement": (m.OpsAssetMovement, None, "action"),
    "operation-equipment": (m.OpsEquipment, "procurement", "note"),
    "operation-income": (m.OpsIncome, "income", "income_date"),
    "operation-project-income": (m.OpsProjectIncome, "income", "income_date"),
    "operation-visit": (m.OpsSchoolVisit, "outreach", "occurred_on"),
    "operation-import": (m.OpsImportBatch, None, "source_name"),
}


WORKBOOK_PERMISSIONS = {
    "workbook-contracts": ("contracts:write",),
    "workbook-collections": ("contracts:write", "collections:request", "receivables:correct"),
    "workbook-opportunities": ("opp:write",),
    "workbook-tender": ("project:key", "milestone:write"),
}


def _need(p, *permissions):
    if not p.can_delete(*permissions):
        raise forbidden("无权删除此记录")


def _row(db, model, condition, label):
    row = db.scalar(select(model).where(condition).with_for_update(of=model).execution_options(populate_existing=True))
    if row is None:
        raise not_found(label)
    return row


def _by_id(db, model, ident, label):
    return _row(db, model, model.id == records.number(ident), label)


def _exists(db, model, *conditions):
    return db.scalar(select(model.id).where(*conditions).limit(1)) is not None


def _mark(db, p, row, name, ip):
    record_removals.mark(db, p, row.__tablename__, row.id, name=str(name), ip=ip)


def _check_dependencies(db, row):
    if isinstance(row, m.BizExpenseReport) and (row.pay_status or row.bank_flow_id):
        raise conflict("该报销单已核对付款，请先撤销关联的流水核对")
    if isinstance(row, m.FinBankFlow) and (row.match_status or row.biz_ref):
        raise conflict("请先撤销此流水的核对，再删除流水")
    if isinstance(row, m.FinSalesInvoice) and row.state == "issued" and row.issued_amount != row.reduced_amount:
        raise conflict("已开具的发票请先在开票台账做红冲或更正")
    if isinstance(row, m.CleanJob) and row.status in {"running", "importing"}:
        raise conflict("请在清洗任务结束后删除")
    if isinstance(row, m.OpsProduct) and (
            _exists(db, m.OpsEquipment, m.OpsEquipment.product_id == row.id) or
            _exists(db, m.OpsAssetMovement, m.OpsAssetMovement.product_id == row.id)):
        raise conflict("该产品仍关联设备或出入库记录，请先处理相关记录")
    if isinstance(row, m.OpsEquipment) and _exists(db, m.OpsAssetMovement, m.OpsAssetMovement.equipment_id == row.id):
        raise conflict("该登记已转成出入库记录，请先处理关联的流转记录")
    if isinstance(row, m.OpsAssetMovement) and _exists(
            db, m.OpsAssetMovement, m.OpsAssetMovement.project_id == row.project_id,
            m.OpsAssetMovement.product_id == row.product_id, m.OpsAssetMovement.id > row.id):
        raise conflict("该设备还有后续出入库记录，请先处理后续记录")


def _project_record(db, p, kind, ident, ip):
    model, section, label = PROJECT_RECORDS[kind]
    records.menu(p, "operations")
    row = db.get(model, records.number(ident))
    if not row:
        raise not_found("运营记录")
    project_id = getattr(row, "project_id", None)
    if project_id is None:
        school = db.get(m.OpsSchool, row.school_id)
        if not school:
            raise not_found("学校")
        project_id = school.project_id
    project = operations_service.get_project(db, p, project_id, lock=True)
    row = _by_id(db, model, ident, "运营记录")
    if kind == "maintenance":
        section = row.kind
    elif kind == "asset-movement":
        section = "scrap" if row.action.startswith("scrap_") else "warehouse"
    elif kind == "operation-import":
        section = "income" if row.kind == "income" else "procurement"
    operations_scope.require(db, p, project, section)
    if not p.has_delete_grant or kind == "operation-goal":
        if kind == "operation-competitor":
            if not operations_competitors._can_edit(db, p, project):
                raise forbidden("仅本项目有编辑权限的团队成员可删除对手信息")
        elif kind == "maintenance" and row.owner_id == p.id and operations_service.role(db, p, project) in operations_service.EXECUTION_ROLES:
            operations_service.active(project)
        else:
            operations_service.manage(db, p, project)
    _check_dependencies(db, row)
    if kind == "operation-goal":
        current = db.get(m.OpsTaskPolicy, row.task_id)
        if not current or not current.archived_at:
            operations_policy.archive(db, p, row.task_id, "人工确认删除学校开拓目标")
    _mark(db, p, row, getattr(row, label) or row.id, ip)


def _delete_book_row(db, p, kind, reference, ip):
    _need(p, *WORKBOOK_PERMISSIONS[kind])
    if not reference["revision"]:
        raise conflict("请刷新列表后重新选择要删除的原表记录")
    item = records.resolve(db, p, kind, reference)
    source = _row(db, m.ContractWorkbook, m.ContractWorkbook.id == item.workbook, "企微来源")
    if source.revision != item.revision:
        raise conflict("原表已更新，请重新打开列表后再操作")
    sheet = records.workbook_sheet(source, kind.removeprefix("workbook-"), item.sheet)
    if not sheet or sum(business_workbook.workbook_identity(row) == item.identity
                        for row in sheet.rows.values() if not row.get("parent")) != 1:
        raise conflict("原表中存在无法区分的重复记录，请先核对原表")
    record_removals.mark(db, p, "workbook-row", business_workbook.workbook_deletion_key(
        item.workbook, item.sheet, item.identity), name=item.title, ip=ip)


def _delete_book_receipt(db, p, reference, ip):
    from app.services import workbook_metrics

    _need(p, "contracts:write", "receivables:correct")
    wb = business_workbook.active_source(db, p, "collections")
    if not wb:
        raise not_found("企微回款来源")
    wb = _row(db, m.ContractWorkbook, m.ContractWorkbook.id == wb.id, "企微回款来源")
    if reference["revision"] != wb.revision:
        raise conflict("原表已更新，请重新打开列表后再操作")
    business_workbook._load_workbook_deletions(db, wb)
    receipt = next((row for period in workbook_metrics.collection_periods(wb).values() if period["available"]
                    for row in period["rows"] if row["id"] == reference["id"] and row["sheet_id"] == reference["sheet"]), None)
    if not receipt:
        raise not_found("原表回款记录")
    if not receipt["deletion_key"]:
        raise conflict("此笔回款没有可稳定识别的原表记录，请先核对原表")
    record_removals.mark(db, p, "workbook-receipt", receipt["deletion_key"], name=receipt["name"], ip=ip)


def _delete_project(db, p, ident, ip):
    records.menu(p, "projects", "market")
    project = project_service.get_project(db, ident, p)
    if project.is_key:
        key_projects.delete_project(db, p, project.project_code)
        return
    row = _row(db, m.BizProject, m.BizProject.id == project.id, "项目")
    if p.has_delete_grant:
        if not project_service.can_view(db, p, row):
            raise forbidden("无权查看该项目")
    else:
        project_service.ensure_edit(db, p, row)
    if row.is_deleted:
        raise not_found("项目")
    row.is_deleted = True
    log_op(db, p.id, "project.delete", "biz_project", row.project_code,
           before={"name": row.name}, after={"is_deleted": True}, ip=ip)


def _delete_key_task(db, p, ident, ip):
    records.menu(p, "market", "projects")
    item = db.get(m.BizProjectMilestone, records.number(ident))
    if not item:
        raise not_found("重点项目任务")
    project = key_projects.project(db, p, item.project_id, lock=True)
    if not (key_projects.can_manage(db, p, project) or (p.has_delete_grant and key_projects.standard_access(db, p, project))):
        raise forbidden("无权删除该项目的任务")
    row = _row(db, m.BizProjectMilestone, m.BizProjectMilestone.id == item.id, "重点项目任务")
    _mark(db, p, row, row.node_name, ip)


def _delete_contract(db, p, ident, ip):
    records.menu(p, "contracts")
    _need(p, "contracts:write")
    ident = records.number(ident)
    row = _by_id(db, m.BizContract, str(ident), "合同")
    if row.is_deleted:
        raise not_found("合同")
    if not db.scalar(select(m.BizContract.id).where(m.BizContract.id == ident, owner_scope(m.BizContract.owner_id, p))):
        raise forbidden("无权维护该合同")
    if _exists(db, m.BizPayment, m.BizPayment.contract_id == ident) or _exists(
            db, m.FinSalesInvoice, m.FinSalesInvoice.contract_id == ident, m.FinSalesInvoice.state == "issued",
            m.FinSalesInvoice.issued_amount != m.FinSalesInvoice.reduced_amount):
        raise conflict("该合同还关联有效到账或开票，请先处理相关账目")
    row.is_deleted = True
    log_op(db, p.id, "contract.delete", "biz_contract", row.id, before={"name": row.name}, after={"is_deleted": True}, ip=ip)


def _delete_opportunity(db, p, ident, ip):
    records.menu(p, "opportunities")
    _need(p, "opp:write")
    row = _row(db, m.BizOpportunity, (m.BizOpportunity.id == records.number(ident)) &
               (~m.BizOpportunity.is_deleted) & owner_scope(m.BizOpportunity.owner_id, p), "商机")
    row.is_deleted = True
    log_op(db, p.id, "opportunity.delete", "biz_opportunity", row.id, before={"name": row.name},
           after={"is_deleted": True}, ip=ip)


def _delete_payment(db, p, ident, ip):
    records.menu(p, "contracts", "receivables")
    _need(p, "contracts:write", "receivables:correct")
    receivables_service.lock(db)
    item = db.get(m.BizPayment, records.number(ident))
    if not item:
        raise not_found("合同回款")
    contract = _row(db, m.BizContract, (m.BizContract.id == item.contract_id) &
                    (~m.BizContract.is_deleted) & owner_scope(m.BizContract.owner_id, p), "合同")
    payment = _by_id(db, m.BizPayment, ident, "合同回款")
    if payment.bank_flow_id:
        raise conflict("这笔回款来自银行流水，请先在流水核对中撤销其核对")
    if _exists(db, m.FinProjectReportEntry, m.FinProjectReportEntry.payment_id == payment.id):
        raise conflict("这笔回款由历史项目报表引用，请先处理关联财务凭证")
    receipt = db.scalar(select(m.FinReceipt).where(m.FinReceipt.payment_id == payment.id).with_for_update())
    if receipt:
        _need(p, "receivables:correct")
        owned = receipt.owns_payment
        receivables_service.void_receipt(db, receipt, "人工确认删除合同回款", p.id)
        if owned:
            return
    _mark(db, p, payment, contract.name or contract.contract_no, ip)
    payment.plan_id = None
    db.flush()
    payment_service.rebuild_plan_receipts(db, contract)


def _delete_other(db, p, kind, ident, ip):
    if kind == "receipt":
        records.menu(p, "receivables")
        _need(p, "receivables:correct")
        records.resolve(db, p, kind, {"id": ident})
        receivables_service.lock(db)
        row = _by_id(db, m.FinReceipt, ident, "到账")
        receivables_service.void_receipt(db, row, "人工确认删除到账", p.id)
        return
    if kind == "sporadic-collection":
        from app.services import sporadic_collections

        records.menu(p, "contracts", "receivables")
        if not (p.has_delete_grant or collection_service.can_request(p)):
            raise forbidden("无权删除零星回款")
        row = sporadic_collections.get_project(db, p, records.number(ident), lock=True)
        _mark(db, p, row, row.name, ip)
        return
    if kind == "collection-report":
        workbook = business_workbook.active_source(db, p, "collections")
        row = _by_id(db, m.WorkbookCollectionReport, ident, "回款报告")
        if not workbook or row.workbook_id != workbook.id:
            raise not_found("回款报告")
        if row.author_id != p.id and not (p.has_delete_grant or collection_service.can_request(p)):
            raise forbidden("无权删除此回款报告")
        _mark(db, p, row, row.title, ip)
        return
    if kind == "collection-thread":
        from app.services import collection_followups

        records.menu(p, "contracts", "receivables")
        row = collection_followups._get(db, p, records.number(ident), lock=True)
        if row.author_id != p.id and not (p.has_delete_grant or collection_service.can_request(p)):
            raise forbidden("只有跟进发起人或获授权的管理人员可删除")
        _mark(db, p, row, row.contract_name, ip)
        return
    if kind == "ops-digest":
        item = records.resolve(db, p, kind, {"id": ident})
        row = _by_id(db, m.OpsDigestSnapshot, ident, "运营报告")
        if not (p.has_delete_grant or item.manager or row.generated_by == p.id):
            raise forbidden("仅报告作者或项目管理员可删除")
        _mark(db, p, row, item.title, ip)
        return
    if kind == "import":
        from app.services import import_service

        item = import_service._get(db, p, records.number(ident), manage=True)
        row = _by_id(db, m.ImportBatch, ident, "导入记录")
        if item.status in {"processing", "queued", "analyzing"}:
            raise conflict("请在导入任务结束后删除")
        _mark(db, p, row, row.filename, ip)
        return
    if kind == "employee":
        records.menu(p, "hr")
        _need(p, "hr:write")
        row = _row(db, m.HrEmployee, m.HrEmployee.user_id == records.number(ident), "人事档案")
        _mark(db, p, row, row.employee_no, ip)
        return
    if kind == "submission":
        records.menu(p, "submissions")
        _need(p, "submissions:remind")
        row = _row(db, m.OaSubmission, (m.OaSubmission.id == records.number(ident)) &
                   owner_scope(m.OaSubmission.user_id, p), "汇报")
        _mark(db, p, row, row.period, ip)
        return
    if kind == "notification":
        row = _row(db, m.SysNotify, (m.SysNotify.id == records.number(ident)) &
                   (m.SysNotify.user_id == p.id) & (m.SysNotify.channel == "inapp"), "消息")
        _mark(db, p, row, row.title, ip)
        return
    raise not_found("删除入口")


def delete(db, p, kind, reference, ip=None):
    ident = reference["id"]
    if kind == "approval" and p.is_system_manager:
        row = _row(db, m.SyncWecomApproval, m.SyncWecomApproval.sp_no == ident, "审批记录")
        _mark(db, p, row, row.sp_name or row.sp_no, ip)
    elif kind in RECORDS:
        policy = RECORDS[kind]
        _need(p, *policy.permissions)
        item = records.resolve(db, p, kind, reference)
        row = (_row(db, policy.model, policy.model.sp_no == item.ident, item.label) if kind == "approval"
               else _by_id(db, policy.model, item.ident, item.label))
        _check_dependencies(db, row)
        _mark(db, p, row, item.title, ip)
    elif kind in PROJECT_RECORDS:
        _project_record(db, p, kind, ident, ip)
    elif kind in WORKBOOK_PERMISSIONS:
        _delete_book_row(db, p, kind, reference, ip)
    elif kind == "workbook-receipt":
        _delete_book_receipt(db, p, reference, ip)
    elif kind in {"project", "key-project"}:
        _delete_project(db, p, ident, ip)
    elif kind == "key-task":
        _delete_key_task(db, p, ident, ip)
    elif kind == "contract":
        _delete_contract(db, p, ident, ip)
    elif kind == "opportunity":
        _delete_opportunity(db, p, ident, ip)
    elif kind == "contract-payment":
        _delete_payment(db, p, ident, ip)
    elif kind == "operation-task":
        operations_policy.archive(db, p, records.number(ident), "人工确认删除任务")
    else:
        _delete_other(db, p, kind, ident, ip)

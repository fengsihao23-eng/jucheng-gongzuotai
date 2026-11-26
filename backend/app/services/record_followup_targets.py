"""Allowlisted business records and their existing record-level visibility rules."""
import re
from dataclasses import dataclass, field
from hashlib import sha256
from urllib.parse import quote, urlencode

from sqlalchemy import or_, select

from app import models as m
from app.core.db import get_or_404
from app.core.response import bad_request, conflict, forbidden, not_found
from app.services import (business_workbook, collection_followups, collection_service, contract_service,
                          import_service, kb_service, key_projects, operations_scope, operations_service,
                          project_service, report_service, sporadic_collections)
from app.services.workbook_project_feedback import tracking_key


@dataclass(frozen=True)
class Target:
    menu: str
    label: str
    model: type
    title: str
    owners: tuple[str, ...] = ()
    write: tuple[str, ...] = ()
    facts: tuple[tuple[str, str], ...] = ()


# Records whose original detail is protected by the module menu. More restrictive
# record scopes are resolved below with the corresponding domain service.
TARGETS = {
    "expense": Target("expenses", "报销单详情", m.BizExpenseReport, "approval_no", ("applicant_id",), ("expense:allocate",),
                      (("报销金额", "amount"), ("费用类别", "category"), ("报销日期", "expense_date"))),
    "bank-flow": Target("flows", "流水详情", m.FinBankFlow, "counterparty", ("matched_by",), ("flows:match",),
                         (("发生额", "amount"), ("交易日期", "trade_date"), ("摘要", "summary"))),
    "bank-import": Target("flows", "银行流水导入详情", m.FinBankImport, "filename", ("uploader_id",), ("flows:import",)),
    "journal": Target("journal", "日记账详情", m.FinCashJournal, "summary", ("register_by",), ("journal:write",),
                      (("金额", "amount"), ("记账日期", "journal_date"), ("费用类别", "category"))),
    "finance-batch": Target("finance_data", "同步批次详情", m.SyncLemonBatch, "batch_no", (), ("finance:sync",),
                            (("账期", "period"), ("同步渠道", "channel"))),
    "project-cost": Target("finance_data", "项目成本详情", m.BizProjectCost, "summary", ("created_by",), ("cost:write",),
                            (("项目编号", "project_code"), ("成本金额", "amount"), ("发生日期", "cost_date"))),
    "tax": Target("finance_data", "税务资料详情", m.SyncTaxFiling, "tax_type", ("uploader_id",), ("tax:upload",),
                  (("账期", "period"), ("实缴金额", "paid_amount"), ("申报日期", "filing_date"))),
    "invoice": Target("receivables", "开票详情", m.FinSalesInvoice, "customer", ("confirmed_by",), ("receivables:invoice",),
                      (("发票号码", "invoice_no"), ("开票金额", "issued_amount"), ("开票日期", "issued_on"))),
    "receipt": Target("receivables", "到账详情", m.FinReceipt, "payer", ("confirmed_by",), ("receivables:receipt",),
                      (("到账金额", "amount"), ("到账日期", "received_on"), ("流水参考号", "reference"))),
    "receivables-source": Target("receivables", "来源明细", m.FinReceivablesSourceRow, "record_key", (), ("receivables:source",),
                                 (("来源", "source_key"), ("登记状态", "state"))),
    "intel": Target("intel", "情报详情", m.MktIntel, "title", ("author_id",), ("intel:write",), (("来源", "source_name"),)),
    "review": Target("reviews", "复盘详情", m.BizProjectReview, "title", ("author_id",), ("review:write",)),
    "ticket": Target("service", "客服工单详情", m.CsTicket, "customer_name", ("handler_id",), ("ticket:write",),
                     (("工单编号", "ticket_no"), ("问题类型", "category"), ("来单渠道", "channel"))),
    "tender-doc": Target("tender", "标书资料详情", m.ProdTenderDoc, "title", ("uploader_id",), ("tender:write",),
                         (("项目编号", "project_code"),)),
    "tender-check": Target("tender", "合规检查详情", m.ProdBidCheck, "title", ("created_by",), ("tender:write",)),
    "tender-generation": Target("tender", "方案详情", m.ProdGeneration, "title", ("created_by",), ("tender:write",)),
    "hr-candidate": Target("hr", "候选人详情", m.HrCandidate, "name", (), ("hr:write",),
                           (("应聘岗位", "position"), ("简历来源", "channel"), ("筛选得分", "screen_score"))),
    "hr-alert": Target("hr", "到期预警详情", m.HrContractAlert, "title", (), ("hr:write",),
                       (("到期日", "due_date"), ("处理说明", "handle_note"))),
    "hr-doc": Target("hr", "人事文书详情", m.HrDocument, "title", ("created_by",), ("hr:write",)),
    "report": Target("reports", "报表详情", m.RptReport, "title", ("created_by",), ("report:generate",),
                     (("统计期间", "period"), ("版本", "version"))),
    "market-report": Target("market", "市场报告详情", m.RptReport, "title", ("created_by",), ("report:generate",),
                            (("统计期间", "period"), ("版本", "version"))),
    "general-report": Target("projects", "一般项目报告详情", m.RptReport, "title", ("created_by",), ("report:generate",),
                             (("统计期间", "period"), ("版本", "version"))),
    "service-report": Target("service", "工单归因报告", m.RptReport, "title", ("created_by",), ("ticket:write",),
                             (("统计期间", "period"), ("版本", "version"))),
    "performance-report": Target("performance", "业绩快照详情", m.RptReport, "title", ("created_by",), ("report:generate",),
                                 (("统计期间", "period"), ("版本", "version"))),
    "sync-job": Target("admin", "执行日志详情", m.SyncJobLog, "job_name", (), (),
                       (("运行时间", "run_at"), ("处理数量", "rows_affected"))),
    "admin-user": Target("admin", "员工账号详情", m.SysUser, "name", (), (), (("职务", "position"),)),
}


@dataclass
class Record:
    kind: str
    ident: str
    label: str
    title: str
    menu: str
    owner_ids: tuple[int, ...] = ()
    writer: bool = False
    manager: bool = False
    facts: list[dict] = field(default_factory=list)
    context: str = ""
    path: str | None = None
    sheet: str = ""
    revision: str = ""
    workbook: int | None = None
    identity: str = ""
    legacy: tuple[str, int | str] | None = None
    archive_key: str | None = None
    operation_project_id: int | None = None
    biz_project_id: int | None = None

    @property
    def key(self):
        value = f"{self.workbook}\n{self.sheet}\n{self.identity}" if self.workbook else self.ident
        return record_key(value)

    @property
    def reference(self):
        result = {"id": self.ident}
        if self.workbook:
            result.update(sheet=self.sheet, workbook=self.workbook, identity=self.identity)
        return result


def menu(p, *values):
    if not any(p.has_menu(value) for value in values):
        raise forbidden()


def record_key(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def number(value):
    if not value.isascii() or not value.isdigit() or len(value) > 18 or int(value) < 1:
        raise not_found("业务记录")
    return int(value)


def facts(row, fields):
    return [{"label": label, "value": value} for label, attr in fields if (value := getattr(row, attr, None)) not in (None, "")]


def owners(*values):
    return tuple(dict.fromkeys(value for value in values if value))


def workbook_sheet(wb, view, sheet_id):
    sheets = [business_workbook.opportunity_sheet(wb)] if view == "opportunities" else business_workbook.module_sheets(wb, view)
    return next((sheet for sheet in sheets if sheet and sheet.id == sheet_id and not sheet.warning), None)


def workbook_identity(row):
    return business_workbook.workbook_identity(row)


def workbook_record(db, p, kind, ref):
    view = kind.removeprefix("workbook-")
    wb = business_workbook.active_source(db, p, view)
    if not wb or (ref.get("workbook") and wb.id != ref["workbook"]):
        raise not_found("业务来源")
    if ref.get("revision") and wb.revision != ref["revision"]:
        raise conflict("原表已更新，请重新打开详情")
    sheet = workbook_sheet(wb, view, ref.get("sheet", ""))
    if sheet is None:
        raise not_found("来源子表")
    identity = ref.get("identity")
    if identity:
        matches = [entry for entry in sheet.rows.values() if not entry.get("parent") and workbook_identity(entry) == identity]
        row = matches[0] if len(matches) == 1 else None
    else:
        row = sheet.rows.get(number(ref["id"]))
        # Tender lots share one timeline. Contract subrows belong to the contract.
        if row and row.get("parent"):
            row = sheet.rows.get(row["parent"])
        identity = workbook_identity(row)
    if row is None or row["row"] in sheet.removed_rows or not row.get("record"):
        raise not_found("业务记录")
    if not identity:
        raise conflict("原表中有同名记录，请核对后再跟进")
    item = sheet.project(row)
    label, section, buttons = {
        "contracts": ("合同详情", "经营业绩", ("contracts:write",)),
        "collections": ("回款详情", "回款情况", ("collections:request", "receivables:receipt")),
        "opportunities": ("商机详情", "商机信息", ("opp:write",)),
        "tender": ("项目详情", "招标与在建", ("project:key", "feedback:write", "milestone:write")),
    }[view]
    ident = str(row["row"])
    path = (f"/{view}/items/{quote(sheet.id, safe='')}/{ident}?" + urlencode({"revision": wb.revision})
            if view in ("contracts", "collections") else
            ("/tender-projects" if view == "tender" else "/opportunities") + "?" + urlencode({"sheet": sheet.id, "row": ident}))
    owner = item.get("owner")
    member_ids = tuple(db.scalars(select(m.SysUser.id).where(or_(m.SysUser.name == owner, m.SysUser.wecom_userid == owner)))) if owner else ()
    values = []
    for name, key in (("责任人", "owner"), ("客户 / 对接单位", "customer"), ("业务进度", "progress"),
                      ("开标时间" if view == "tender" else "业务日期", "date"),
                      ("待回款金额" if view == "collections" else "金额", "outstanding" if view == "collections" else "amount")):
        if item.get(key):
            values.append({"label": name, "value": item[key]})
    legacy = ("workbook", identity) if view in {"opportunities", "tender"} else None
    return Record(kind, ident, label, item["name"], section, member_ids, any(p.can(b) for b in buttons),
                  facts=values, context=sheet.name, path=path, sheet=sheet.id, revision=wb.revision,
                  workbook=wb.id, identity=identity, legacy=legacy, archive_key=tracking_key(view, row) if legacy else None)


def resolve(db, p, kind, ref, *, lock=False):
    if not p.workbench_enabled:
        raise forbidden()
    ident = ref["id"]
    if kind in {"workbook-contracts", "workbook-collections", "workbook-opportunities", "workbook-tender"}:
        return workbook_record(db, p, kind, ref)
    if kind in {"project", "key-project", "project-profit"}:
        if kind == "project-profit":
            from app.api.reports import _profit_principal, _scope_codes
            menu(p, "profit", "reports")
            _profit_principal(p)
            scope = _scope_codes(p, db)
            if scope is not None and ident not in scope:
                raise forbidden()
            row = db.scalar(select(m.BizProject).where(m.BizProject.project_code == ident, m.BizProject.is_deleted.is_(False)))
            if not row:
                raise not_found("项目")
            return Record(kind, row.project_code, "项目利润详情", row.name, "项目利润", owners(row.owner_id),
                          p.can("cost:write"), facts=facts(row, (("项目编号", "project_code"),)), path="/profit", biz_project_id=row.id)
        if kind == "key-project":
            row = key_projects.project(db, p, ident, lock=lock)
            standard = key_projects.standard_access(db, p, row)
        else:
            row = project_service.get_project(db, ident, lock=lock)
            standard = p.has_menu("projects") and project_service.can_view(db, p, row)
            if not standard and (row.is_key or not key_projects.is_member(db, p, row)):
                raise forbidden()
        writers = standard and (project_service.can_edit(p, row) or p.can("feedback:write") or p.can("project:key"))
        return Record(kind, row.project_code, "重点项目详情" if kind == "key-project" else "项目详情", row.name,
                      "重点项目" if kind == "key-project" else "项目台账", owners(row.owner_id, row.market_owner_id), writers,
                      facts=facts(row, (("项目编号", "project_code"), ("客户", "customer_name"), ("计划完成", "plan_end_date"))),
                      context=row.remark or "", path=key_projects.project_link(row) if kind == "key-project" or not row.is_key else
                      f"/projects/{row.project_code}",
                      legacy=("project", row.id), biz_project_id=row.id)
    if kind == "key-task":
        item = get_or_404(db, m.BizProjectMilestone, number(ident), "项目任务")
        project = key_projects.project(db, p, item.project_id, lock=lock, is_key=None)
        return Record(kind, str(item.id), "任务详情", item.node_name, "重点项目" if project.is_key else "一般项目",
                      owners(item.owner_id, project.owner_id), key_projects.can_feedback_task(db, p, project, item),
                      facts=facts(item, (("截止日期", "plan_date"),)), context=project.name,
                      path=key_projects.task_link(project, item.id), legacy=("key-task", item.id), biz_project_id=project.id)
    if kind == "operation":
        row = operations_service.get_project(db, p, number(ident))
        operations_scope.require(db, p, row, "progress")
        role = operations_service.role(db, p, row)
        return Record(kind, str(row.id), "运营项目详情", row.name, "运营项目", owners(row.owner_id),
                      role in {"manager", "member", "reviewer"}, role == "manager",
                      facts(row, (("项目分类", "category"), ("目标日期", "target_date"))), row.objective,
                      f"/operations/{row.id}", operation_project_id=row.id)
    if kind in {"school", "maintenance", "operation-product", "asset-movement"}:
        models = {"school": m.OpsSchool, "maintenance": m.OpsMaintenanceRecord,
                  "operation-product": m.OpsProduct, "asset-movement": m.OpsAssetMovement}
        row = get_or_404(db, models[kind], number(ident))
        project = operations_service.get_project(db, p, row.project_id)
        if kind == "maintenance":
            from app.services.operations_assets import record_history
            record_history(db, p, project.id, row.id)
        elif kind == "asset-movement":
            if not operations_scope.can(db, p, project, "warehouse"):
                operations_scope.require(db, p, project, "scrap" if row.action.startswith("scrap_") else "warehouse")
        elif kind == "school":
            if not any(operations_scope.can(db, p, project, key) for key in ("outreach", "procurement", "income")):
                raise forbidden()
        else:
            operations_scope.require(db, p, project, "products")
        role = operations_service.role(db, p, project)
        label = {"school": "学校资料", "maintenance": "维护记录", "operation-product": "产品详情", "asset-movement": "物料出入库详情"}[kind]
        title = getattr(row, "name", None) or getattr(row, "title", None) or label
        return Record(kind, str(row.id), label, title, "运营项目", owners(getattr(row, "owner_id", None), project.owner_id),
                      role in {"manager", "member", "reviewer"}, role == "manager", context=project.name,
                      path=f"/operations/{project.id}", operation_project_id=project.id)
    if kind == "ops-digest":
        from app.services import operations_reports
        row = get_or_404(db, m.OpsDigestSnapshot, number(ident), "运营报告")
        if not operations_reports.can_read(db, p, row):
            raise forbidden()
        project = operations_service.get_project(db, p, row.project_id)
        label = "运营月报" if row.mode == "month" else "运营周报"
        return Record(kind, str(row.id), "运营报告详情", row.facts.get("person", project.name) + " · " + label, "运营项目",
                      owners(row.owner_id), manager=operations_service.role(db, p, project) == "manager",
                      facts=facts(row, (("统计期间", "period"), ("版本", "version"))),
                      path=f"/operations/{project.id}?digest={row.id}", operation_project_id=project.id)
    if kind == "project-source-report":
        from app.services import project_report_service
        menu(p, "profit", "board")
        row = project_report_service.get_report(db, p, number(ident))
        return Record(kind, str(row.id), "项目报表来源与核对", row.source_name, "项目利润",
                      writer=p.can("cost:write"), facts=facts(row, (("截止月份", "period"), ("项目编号", "primary_project_code"))),
                      path="/profit")
    if kind == "contract":
        menu(p, "contracts")
        row = contract_service.get_contract(db, number(ident), p)
        return Record(kind, str(row.id), "合同详情", row.name or row.contract_no, "合同台账", owners(row.owner_id),
                      p.can("contracts:write") or p.can("contracts:archive"),
                      facts=facts(row, (("合同编号", "contract_no"), ("合同金额", "amount"), ("签订日期", "sign_date"))),
                      context=row.customer_name, path=f"/contracts/{row.id}")
    if kind == "opportunity":
        from app.api.opportunities import _get
        menu(p, "opportunities")
        row = _get(db, p, number(ident))
        return Record(kind, str(row.id), "商机详情", row.name, "商机信息", owners(row.owner_id), p.can("opp:write"),
                      facts=facts(row, (("客户", "customer_name"), ("预计签约", "expected_sign_date"), ("预计金额", "amount"))),
                      path="/opportunities?" + urlencode({"id": row.id}))
    if kind == "knowledge":
        if not p.has_menu("kb"):
            raise forbidden()
        row = kb_service._get(db, ident)
        if not kb_service.can_view(db, p, row):
            raise not_found("知识库文档")
        return Record(kind, str(row.id), "知识文档详情", row.title, "知识库", owners(row.uploader_id),
                      kb_service.can_review(db, p, row.category), facts=facts(row, (("分类", "category"), ("版本", "version"))),
                      path="/kb?" + urlencode({"doc": row.id}))
    if kind == "import":
        row = db.scalar(select(m.ImportBatch).where(m.ImportBatch.id == number(ident), import_service.batch_filter(p)))
        if row is None:
            raise not_found("导入记录")
        return Record(kind, str(row.id), "导入记录详情", row.filename, "资料导入", owners(row.uploader_id, row.handler_id),
                      import_service.can_handle_department(p, row.dept_code), path="/imports?" + urlencode({"id": row.id}))
    if kind == "clean":
        from app.api.clean import _job
        menu(p, "clean")
        row = _job(db, p, number(ident))
        return Record(kind, str(row.id), "清洗任务详情", row.filename, "数据清洗", owners(row.created_by), p.can("clean:import"), path="/clean")
    if kind == "approval":
        menu(p, "contracts")
        row = db.scalar(select(m.SyncWecomApproval).where(m.SyncWecomApproval.sp_no == ident))
        if not row:
            raise not_found("审批单")
        owner = db.scalar(select(m.SysUser.id).where(m.SysUser.wecom_userid == row.applicant_userid)) if row.applicant_userid else None
        return Record(kind, row.sp_no, "审批详情", row.sp_name or row.sp_no, "审批台账", owners(owner), p.can("contracts:write"),
                      facts=facts(row, (("审批单号", "sp_no"), ("提交时间", "apply_time"))), path="/approvals")
    if kind == "audit":
        from app.api.operation_records import records_viewer, service
        records_viewer(p)
        row = service.detail(db, ident, p)
        return Record(kind, row["key"], "操作详情", row.get("action") or "操作记录", "操作记录",
                      facts=[{"label": "操作人", "value": row["actor"]}], path="/operation-records")
    if kind == "collection-thread":
        row = collection_followups._get(db, p, number(ident))
        return Record(kind, str(row.id), "回款跟进详情", row.contract_name, "回款情况", owners(row.author_id, row.recipient_id),
                      collection_service.can_request(p), path=f"/collections/followups/{row.id}")
    if kind == sporadic_collections.KIND:
        menu(p, "contracts", "receivables")
        row = sporadic_collections.get_project(db, p, number(ident), lock=lock)
        fields = facts(row, (("截止日期", "due_on"), ("预计回款（元）", "expected_amount"), ("客户", "customer")))
        return Record(kind, str(row.id), "零星回款任务详情", row.name, "零星回款", owners(row.owner_id),
                      writer=row.owner_id == p.id, manager=collection_service.can_request(p), facts=fields,
                      context=row.requirements, path=f"/collections/sporadic?project={row.id}")
    if kind == "legacy-collection":
        menu(p, "contracts", "receivables")
        contract_id, sep, plan = ident.partition(":")
        if not sep or not plan.isascii() or not plan.isdigit() or len(plan) > 18:
            raise bad_request("缺少回款计划编号")
        contract = contract_service.get_contract(db, number(contract_id), p)
        collection_service.detail(db, p, contract.id, int(plan))
        return Record(kind, f"{contract.id}:{int(plan)}", "回款跟进详情", contract.name or contract.contract_no, "回款情况",
                      owners(contract.owner_id), collection_service.can_request(p), path="/collections")
    if kind == "submission":
        menu(p, "submissions")
        row = get_or_404(db, m.OaSubmission, number(ident), "汇报")
        return Record(kind, str(row.id), "汇报详情", "工作汇报 " + row.period, "汇报提交", owners(row.user_id),
                      p.can("submissions:remind"), facts=facts(row, (("提交时间", "submit_time"),)), path="/submissions")
    if kind == "submission-period":
        from app.services import submission_service
        menu(p, "submissions")
        report_type, _, suffix = ident.partition(":")
        period, _, user_id = suffix.partition(":")
        formats = {"weekly": r"20\d{2}-W\d{2}", "monthly": r"20\d{2}-\d{2}", "daily": r"20\d{2}-\d{2}-\d{2}"}
        if report_type not in formats or not re.fullmatch(formats[report_type], period):
            raise bad_request("请选择有效的汇报期间")
        try:
            due = submission_service.deadline_of(report_type, period).date()
        except ValueError:
            raise bad_request("请选择有效的汇报期间") from None
        employee_id = number(user_id)
        employee = next((u for u in submission_service.required_users(db, report_type) if u.id == employee_id), None)
        if employee is None:
            raise not_found("汇报人员")
        values = [{"label": "统计期间", "value": period}, {"label": "截止日期", "value": due}]
        return Record(kind, f"{report_type}:{period}:{employee.id}", "汇报跟进", employee.name, "汇报提交",
                      owners(employee.id), p.can("submissions:remind"), facts=values,
                      path="/submissions?" + urlencode({"type": report_type, "period": period}))
    if kind == "hr-attendance":
        menu(p, "hr")
        period, _, user_id = ident.partition(":")
        if not re.fullmatch(r"20\d{2}-(0[1-9]|1[0-2])", period):
            raise bad_request("请选择有效的考勤月份")
        row = db.scalar(select(m.HrAttendanceSummary).where(m.HrAttendanceSummary.period == period,
                                                             m.HrAttendanceSummary.user_id == number(user_id)))
        if not row:
            raise not_found("考勤汇总")
        employee = get_or_404(db, m.SysUser, row.user_id, "员工")
        return Record(kind, f"{period}:{row.user_id}", "月度考勤详情", employee.name, "人事中心", (),
                      p.can("hr:attendance"), facts=[{"label": "考勤月份", "value": period}], path="/hr?tab=attendance")
    target = TARGETS.get(kind)
    if target is None:
        raise not_found("业务模块")
    menu(p, target.menu)
    if target.menu == "receivables" and p.data_scope != "all":
        raise forbidden()
    if kind == "receivables-source" and not p.can("receivables:source"):
        raise forbidden()
    row = get_or_404(db, target.model, number(ident), target.label.removesuffix("详情"))
    if kind == "review":
        project_service.get_project(db, row.project_id, p)
    if kind == "report" and not report_service.allowed(p, row.report_type):
        raise forbidden("无权查看该类报表")
    if kind == "report":
        from app.services.market_service import TEAM_SCOPED_REPORT_TYPES, can_read_project_report
        if row.report_type in TEAM_SCOPED_REPORT_TYPES and not can_read_project_report(db, p, row):
            raise forbidden("报表包含未加入团队的项目")
    if kind == "market-report" and row.report_type not in {"market_weekly", "market_monthly", "intel_brief"}:
        raise not_found("市场报告")
    if kind == "market-report" and row.report_type != "intel_brief":
        from app.services.market_service import can_read_project_report
        if not can_read_project_report(db, p, row):
            raise not_found("市场报告")
    if kind == "general-report":
        from app.services.market_service import can_read_general_report
        if not can_read_general_report(db, p, row):
            raise not_found("一般项目报告")
    if kind == "service-report" and row.report_type != "cs_attribution":
        raise not_found("归因报告")
    if kind == "performance-report" and row.report_type not in {"performance_monthly", "operation_board"}:
        raise not_found("业绩快照")
    from app.core.permissions import MENUS
    title = getattr(row, target.title, None) or f"{target.label.removesuffix('详情')} #{row.id}"
    if kind == "hr-alert":
        employee = db.get(m.SysUser, row.user_id)
        if employee:
            title = f"{employee.name} · {target.label.removesuffix('详情')}"
    path = "/general-projects?tab=reports" if kind == "general-report" else "/" + {
        "finance_data": "finance-data", "operation_records": "operation-records"}.get(target.menu, target.menu)
    return Record(kind, str(row.id), target.label, title, MENUS[target.menu],
                  owners(*(getattr(row, key, None) for key in target.owners)), any(p.can(key) for key in target.write),
                  facts=facts(row, target.facts), path=path, biz_project_id=row.project_id if kind == "review" else None)

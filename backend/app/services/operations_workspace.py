"""Daily work entry and school-level annual commitments, on existing task records."""
from datetime import date
from decimal import Decimal
from sqlalchemy import select, func

from app.core.audit import log_op
from app.core.deps import owner_scope
from app.core.response import bad_request, conflict, forbidden
from app.core.timeutil import today, month_range, week_range
from app.models import (OpsOutreachGoal, OpsSchoolRegion, OpsSchool, OpsIncome, OpsProjectIncome, OpsProject,
                        OpsTask, BizExpenseReport, BizProject, OpsIncomeIngressBatch)
from app.schemas.operations import TaskIn, TaskPatch
from app.services import operations_service as ops, operations_scope as scope


def workbench(db, p):
    if not p.has_menu("operations"):
        return {"projects": [], "todos": [], "recent": [], "stats": {}, "can_manage": False}
    projects = list(db.scalars(ops.visible_projects(p)))
    rows = ops.tasks(db, p)
    active = {r.id for r in projects if r.status == "active"}
    todos = [r for r in rows if r["project_id"] in active and (
        (r["owner_id"] == p.id and r["status"] != "done" and r["approval_status"] == "approved")
        or r["pending_replies"] or r["can_review"] or r["can_review_delay"])]
    visible_projects = [{**ops.project_dict(db, p, project),
        "stats": ops.task_stats([r for r in rows if r["project_id"] == project.id]),
        "mine_pending": sum(r["project_id"] == project.id for r in todos)} for project in projects]
    recent = sorted([r for r in rows if r["last_feedback_at"]], key=lambda r: r["last_feedback_at"], reverse=True)[:8]
    return {"projects": visible_projects, "todos": todos, "recent": recent, "can_manage": ops.can_create(p),
            "stats": {"todos": len(todos), "overdue": sum(r["overdue"] for r in todos),
                      "needs_feedback": sum(r["needs_update"] and r["owner_id"] == p.id for r in todos),
                      "replies": sum(r["pending_replies"] for r in todos),
                      "reviews": sum(r["can_review"] or r["can_review_delay"] for r in todos)}, "today": today()}


def outreach(db, p, project_id, year=None, region="", owner_id=None):
    project = ops.get_project(db, p, project_id)
    scope.require(db, p, project, "outreach")
    year = year or today().year
    rows = {r["id"]: r for r in ops.tasks(db, p, project_id)}
    schools = {s.id: s for s in db.scalars(select(OpsSchool).where(OpsSchool.project_id == project_id))}
    regions = dict(db.execute(select(OpsSchoolRegion.school_id, OpsSchoolRegion.region).where(OpsSchoolRegion.school_id.in_(schools))).all())
    items = []
    for goal in db.scalars(select(OpsOutreachGoal).where(OpsOutreachGoal.project_id == project_id, OpsOutreachGoal.year == year)):
        task = rows.get(goal.task_id)
        if not task or (owner_id and task["owner_id"] != owner_id) or (region and regions.get(goal.school_id, "待定") != region):
            continue
        school = schools[goal.school_id]
        items.append({**task, "goal_id": goal.id, "school_id": school.id, "school_name": school.name,
                      "stage": school.stage, "year": year, "region": regions.get(school.id, "待定"),
                      "expected_annual_income": goal.expected_annual_income if scope.can(db, p, project, "income") else None})
    years = sorted({today().year, *db.scalars(select(OpsOutreachGoal.year).where(OpsOutreachGoal.task_id.in_(rows)).distinct())}, reverse=True)
    return {"year": year, "years": years, "items": items, "stats": {"targets": len(items), "done": sum(r["status"] == "done" for r in items),
        "overdue": sum(r["overdue"] for r in items), "unreported": sum(not r["last_feedback_at"] for r in items),
        "expected_annual_income": sum((r["expected_annual_income"] or Decimal(0) for r in items), Decimal(0)) if scope.can(db, p, project, "income") else None,
        "unestimated": sum(r["expected_annual_income"] is None for r in items)},
        "note": "年度目标复用同一开拓任务；预计年收入是计划值，不计入实际营收。任务完成不自动代表学校已签约或已收款。"}


def save_goal(db, p, project_id, body):
    project = ops.get_project(db, p, project_id, lock=True)
    ops.manage(db, p, project)
    ops.active(project)
    school = ops.school(db, project_id, body.school_id)
    goal = db.scalar(select(OpsOutreachGoal).where(OpsOutreachGoal.project_id == project_id,
        OpsOutreachGoal.school_id == body.school_id, OpsOutreachGoal.year == body.year))
    if goal:
        if body.expected_revision is None:
            raise conflict("该校本年度目标已存在，请打开原目标调整，避免重复派单")
        task = ops.edit_task(db, p, goal.task_id, TaskPatch(owner_id=body.owner_id, due_date=body.target_date,
            description=body.objective, cadence=body.cadence, expected_revision=body.expected_revision))
        goal.expected_annual_income = body.expected_annual_income
    else:
        if body.expected_revision is not None:
            raise conflict("目标不存在，请重新读取")
        task = ops.create_task(db, p, project_id, TaskIn(title=f"{body.year}年 · {school.name}开拓",
            owner_id=body.owner_id, due_date=body.target_date, description=body.objective,
            cadence=body.cadence, school_id=school.id, kind="outreach"))
        goal = OpsOutreachGoal(project_id=project_id, school_id=school.id, year=body.year,
                               task_id=task.id, expected_annual_income=body.expected_annual_income)
        db.add(goal)
    db.flush()
    log_op(db, p.id, "operations.outreach.goal", "ops_outreach_goal", goal.id,
           after={"year": body.year, "task_id": task.id, "school_id": school.id})
    return {"id": goal.id, "task_id": task.id}


def region_update(db, p, project_id, school_id, region):
    project = ops.get_project(db, p, project_id, lock=True)
    ops.manage(db, p, project)
    ops.school(db, project_id, school_id)
    row = db.get(OpsSchoolRegion, school_id) or OpsSchoolRegion(school_id=school_id)
    before = row.region
    row.region = region
    db.add(row)
    log_op(db, p.id, "operations.school.region", "ops_school", school_id, before={"region": before}, after={"region": region})


def income_board(db, p, project_id, year=None, region="", period="all"):
    project = ops.get_project(db, p, project_id)
    scope.require(db, p, project, "income")
    year = year or today().year
    if not 2000 <= year <= 2100:
        raise bad_request("请选择有效年份")
    day = today()
    bounds = {"all": (date(year, 1, 1), min(day, date(year, 12, 31)))}
    if year == day.year:
        bounds |= {"month": (month_range(ref=day)[0], day), "week": (max(date(year, 1, 1), week_range(ref=day)[0]), day), "today": (day, day)}
    if period not in bounds:
        raise bad_request("历史年份请选择年度，不能把当前月周日套入历史年份")
    school_map = {s.id: s.name for s in db.scalars(select(OpsSchool).where(OpsSchool.project_id == project_id))}
    regions = dict(db.execute(select(OpsSchoolRegion.school_id, OpsSchoolRegion.region).where(OpsSchoolRegion.school_id.in_(school_map))).all())
    income = list(db.scalars(select(OpsIncome).where(OpsIncome.school_id.in_(school_map))))
    common = list(db.scalars(select(OpsProjectIncome).where(OpsProjectIncome.project_id == project_id))) if not region else []
    all_rows = [{"id": r.id, "school_id": r.school_id, "school": school_map[r.school_id], "region": regions.get(r.school_id, "待定"),
        "income_date": r.income_date, "amount": r.amount, "source": r.source, "note": r.note} for r in income
        if not region or regions.get(r.school_id, "待定") == region]
    all_rows += [{"id": r.id, "school_id": None, "school": "项目公共收入", "region": "未分摊", "income_date": r.income_date,
                  "amount": r.amount, "source": r.source, "note": r.note} for r in common]
    cards = [{"key": key, "start": start, "end": end,
              "amount": sum((r["amount"] for r in all_rows if start <= r["income_date"] <= end), Decimal(0)),
              "rows": sum(start <= r["income_date"] <= end for r in all_rows)} for key, (start, end) in bounds.items()]
    start, end = bounds[period]
    entries = sorted([r for r in all_rows if start <= r["income_date"] <= end], key=lambda r: (r["income_date"], r["id"]), reverse=True)
    batches = db.scalars(select(OpsIncomeIngressBatch).where(OpsIncomeIngressBatch.project_id == project_id)
                         .order_by(OpsIncomeIngressBatch.id.desc()).limit(10)).all()
    return {"year": year, "years": sorted({day.year, *[r.income_date.year for r in income], *[r.income_date.year for r in common]}, reverse=True),
        "period": period, "cards": cards, "items": entries, "last_income_date": max((r["income_date"] for r in all_rows), default=None),
        "connection_state": "已接收接口批次" if batches else "未接入固定数据源",
        "batches": [{"source": b.source_key, "batch": b.batch_key, "at": b.created_at, "result": b.result} for b in batches],
        "note": "按已登记实际收款日汇总。未登记不代表零收入；区域筛选不分摊公共收入。接口已预留，未经接入不表示自动日更。"}


def funds(db, p, project_id):
    from app.services import project_service
    project = ops.get_project(db, p, project_id)
    scope.require(db, p, project, "funds")
    code = scope.controls(db, project_id)["business_project_code"]
    if not code:
        return {"linked": False, "items": [], "note": "由项目管理者关联已有业务项目后，可按原权限查看审批归集的经费记录。"}
    if not p.has_menu("expenses") or not p.has_menu("projects"):
        raise forbidden("查看经费与审批还需原有项目台账和报销归集权限")
    project_service.get_project(db, code, p)
    rows = list(db.scalars(select(BizExpenseReport).where(BizExpenseReport.project_code == code,
        owner_scope(BizExpenseReport.applicant_id, p)).order_by(BizExpenseReport.expense_date.desc(), BizExpenseReport.id.desc())))
    return {"linked": True, "project_code": code, "items": [{"id": r.id, "approval_no": r.approval_no,
        "applicant": r.applicant.name if r.applicant else "待核对", "amount": r.amount, "date": r.expense_date,
        "reason": r.reason, "status": r.status, "pay_status": r.pay_status} for r in rows],
        "approved_amount": sum((r.amount for r in rows if r.status in (2, 4)), Decimal(0)),
        "paid_amount": sum((r.amount for r in rows if r.pay_status == 1), Decimal(0)),
        "note": "只读已有审批归集和付款状态；审批通过不等于已经付款，工作台不替代企业微信审批。"}

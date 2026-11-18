"""运营项目服务。所有入口共享成员权限、金额口径及任务反馈规则。"""
from collections import defaultdict
from decimal import Decimal

from sqlalchemy import delete, or_, select, update

from app.core.audit import log_op, snapshot
from app.core.response import bad_request, conflict, forbidden, not_found
from app.core.timeutil import TZ, now, today
from app.models import (OpsEquipment, OpsImportBatch, OpsIncome, OpsMember, OpsOutreachGoal, OpsProduct, OpsProject, OpsProjectIncome,
                        OpsProjectProgress, OpsSchool, OpsSchoolRegion, OpsSchoolVisit,
                        OpsTask, OpsTaskAudience, OpsTaskNode, OpsTaskReview, OpsTaskUpdate, SysUser)
from app.services import notify_service, operations_deadlines as deadlines, operations_policy as policy
from app.services import operations_followup as followups
from app.services import operations_scope as content_scope
from app.services.operations_notifications import PROJECT_ASSIGNMENT

PROJECT_FIELDS = ["name", "category", "owner_id", "objective", "target_date", "status", "is_key"]
PROJECT_PROGRESS_FIELDS = ["progress", "problems", "next_plan", "next_due_date", "task_id"]
SCHOOL_FIELDS = ["name", "stage", "potential", "next_action", "next_contact_date", "labor_cost", "other_cost", "note"]
PRODUCT_FIELDS = ["name", "brand", "model", "unit", "reference_price", "note"]
TASK_FIELDS = ["title", "description", "owner_id", "school_id", "due_date", "priority", "cadence", "status", "progress", "next_step"]
TASK_FIELDS += ["plan_month", "source", "approval_status", "department_reviewer_id", "company_reviewer_id", "revision", "completed_at", "incomplete_reason", "original_due_date"]
STATUS_LABELS = {"todo": "待开始", "doing": "进行中", "blocked": "遇到阻碍", "done": "已完成"}
SOURCE_LABELS = {"project_plan": "负责人计划", "company": "公司安排", "self": "自行安排"}
ZERO = Decimal("0.00")
EXECUTION_ROLES = {"manager", "co_owner", "member", "reviewer"}
POLICY_FIELDS = {"visibility", "viewer_ids", "kind"}


def can_create(p):
    return p.is_admin or p.can("operations:manage")


# 删除运营项目是点名授权（2026-11-16 需求）：只给这三个人，不走角色。
PROJECT_DELETE_USERIDS = {"ken", "FengSiHao", "SZGLY"}
PROJECT_DELETE_NAMES = {"梁道灿", "冯思皓", "聚成数智管理员"}


def can_delete_project(p) -> bool:
    return p.user.wecom_userid in PROJECT_DELETE_USERIDS or p.user.name in PROJECT_DELETE_NAMES


def delete_project(db, p, project):
    """软删除运营项目：状态置 deleted，数据保留可追溯，列表与详情不再出现。"""
    if not can_delete_project(p):
        raise forbidden("只有梁道灿、冯思皓、聚成数智管理员可以删除运营项目")
    if project.status == "deleted":
        raise conflict("该项目已删除")
    before = snapshot(project, PROJECT_FIELDS)
    project.status = "deleted"
    log_op(db, p.id, "operations.project_delete", "ops_project", project.id, before,
           {**before, "status": "deleted"}, None)
    db.commit()
    return {"deleted": True, "id": project.id, "name": project.name}


def can_view_all(p):
    return can_create(p) or p.can("operations:view_all")


def visible_projects(p, *, members_only=False):
    q = select(OpsProject).where(OpsProject.status != "deleted")
    if not can_view_all(p):
        conditions = [policy.member_clause(p)]
        if not members_only and p.can("operations:approve"):
            conditions.append(OpsProject.id.in_(select(OpsTask.project_id).where(
                OpsTask.company_reviewer_id == p.id, ~policy.archived())))
        q = q.where(or_(*conditions))
    return q


def has_project_content(db, p, project):
    return can_view_all(p) or role(db, p, project) is not None


def get_project(db, p, project_id, *, lock=False):
    q = visible_projects(p).where(OpsProject.id == project_id)
    if lock:
        q = q.with_for_update(of=OpsProject)
    project = db.scalar(q)
    if not project:
        raise not_found("运营项目")
    return project


def role(db, p, project):
    if can_create(p) or project.owner_id == p.id:
        return "manager"
    raw = db.scalar(select(OpsMember.role).where(OpsMember.project_id == project.id, OpsMember.user_id == p.id))
    return "manager" if raw == "co_owner" else raw  # 联合负责人与项目管理员同权


def manage(db, p, project):
    if role(db, p, project) != "manager":
        raise forbidden("仅项目负责人或项目管理员可维护")


def active(project):
    if project.status != "active":
        raise conflict("项目已暂停或完成，请先恢复进行中再安排或反馈任务")


def user(db, user_id):
    u = db.get(SysUser, user_id)
    if not u or u.is_deleted or u.status != 1:
        raise bad_request("请选择有效的在职人员")
    return u


def assignee(db, project, user_id):
    u = user(db, user_id)
    member_role = db.scalar(select(OpsMember.role).where(OpsMember.project_id == project.id, OpsMember.user_id == user_id))
    if project.owner_id != user_id and member_role not in EXECUTION_ROLES:
        raise bad_request("任务负责人须为本项目可参与执行的成员，请先在团队中添加")
    return u


def project_dict(db, p, project):
    my_role = role(db, p, project)
    fields = snapshot(project, PROJECT_FIELDS)
    if not (can_view_all(p) or my_role):
        fields.update(objective="", target_date=None)
    return {"id": project.id, **fields, "owner_name": project.owner.name,
            "my_role": my_role, "can_manage": my_role == "manager"}


def _write(db, p, obj, values, fields):
    before = snapshot(obj, fields) if obj.id else None
    for key, value in values.items():
        setattr(obj, key, value)
    db.add(obj)
    db.flush()
    if "owner_id" in values and hasattr(type(obj), "owner"):
        db.expire(obj, ["owner"])
    log_op(db, p.id, "operations.update" if before else "operations.create", obj.__tablename__, obj.id,
           before=before, after=snapshot(obj, fields))
    return obj


def save_project(db, p, body, project_id=None):
    from app.core.deps import build_principal

    if project_id:
        project = get_project(db, p, project_id, lock=True)
        manage(db, p, project)
        if project.owner_id != body.owner_id and not db.scalar(select(OpsMember.id).where(
                OpsMember.project_id == project.id, OpsMember.user_id == project.owner_id)):
            # 保留原负责人的任务访问权，可在团队中另行移除。
            db.add(OpsMember(project_id=project.id, user_id=project.owner_id, role="member"))
    else:
        if not can_create(p):
            raise forbidden("仅公司负责人、市场负责人或管理员可新建运营项目")
        project = OpsProject(created_at=now())
    previous_owner_id = project.owner_id
    legacy_campus_income = bool(project_id and project.name == "智慧校园")
    owner = user(db, body.owner_id)
    project = _write(db, p, project, body.model_dump(include=set(PROJECT_FIELDS)), PROJECT_FIELDS)
    if body.enabled_sections is not None:
        content_scope.save_features(db, p, project, body.enabled_sections, body.expected_features_revision,
                                    creating=not project_id, legacy_campus_income=legacy_campus_income)
    if previous_owner_id != owner.id and build_principal(owner).has_menu("operations"):
        title = "运营项目已交接给你" if project_id else "你负责的运营项目已创建"
        notify_service.send(db, [owner], PROJECT_ASSIGNMENT, title, f"你是「{project.name}」的项目负责人。",
            link=f"/operations/{project.id}", ref_type="ops_project", ref_id=str(project.id),
            wecom=content_scope.controls(db, project.id)["wecom_reminders"], defer=True,
            wecom_content="请打开工作台查看项目安排。")
    return project


def progress_dict(row):
    return {"id": row.id, "project_id": row.project_id, "author_name": row.author.name, "created_at": row.created_at,
            **snapshot(row, PROJECT_PROGRESS_FIELDS)}


def project_progress(db, p, project_id, before_id=None):
    project = get_project(db, p, project_id)
    content_scope.require(db, p, project, "progress")
    if not has_project_content(db, p, project):
        raise forbidden("仅项目成员可查看项目进度")
    query = select(OpsProjectProgress).where(OpsProjectProgress.project_id == project_id,
        or_(OpsProjectProgress.task_id.is_(None),
            OpsProjectProgress.task_id.in_(select(OpsTask.id).where(OpsTask.project_id == project_id, policy.task_access(p)))))
    if before_id is not None:
        query = query.where(OpsProjectProgress.id < before_id)
    rows = db.scalars(query.order_by(OpsProjectProgress.id.desc()).limit(26)).all()
    return {"items": [progress_dict(row) for row in rows[:25]], "next_before_id": rows[24].id if len(rows) > 25 else None}


def record_project_progress(db, p, project_id, body):
    project = get_project(db, p, project_id, lock=True)
    manage(db, p, project)
    content_scope.require(db, p, project, "progress")
    return _save_project_progress(db, p, project, body)


def _save_project_progress(db, p, project, body, task_id=None):
    row = OpsProjectProgress(project_id=project.id, author_id=p.id, created_at=now())
    return progress_dict(_write(db, p, row, {**body.model_dump(), "task_id": task_id}, PROJECT_PROGRESS_FIELDS))


def record_project_progress_entries(db, p, project_id, entries, expected_owner_id=None):
    """Record the report and its dated work items once, in the same transaction."""
    project = get_project(db, p, project_id, lock=True)
    manage(db, p, project)
    content_scope.require(db, p, project, "progress")
    if any(entry.next_due_date for entry in entries):
        active(project)
        if expected_owner_id is not None and project.owner_id != expected_owner_id:
            raise conflict("项目负责人已变更，请核对后重新填写")
        user(db, project.owner_id)
    saved, created = [], []
    for entry in entries:
        plan = _create_project_plan(db, p, project, entry) if entry.next_due_date else None
        saved.append(_save_project_progress(db, p, project, entry, plan.id if plan else None))
        if plan:
            created.append(plan.id)
    return {"project_id": project.id, "items": saved, "created_task_ids": created}


def members(db, project):
    rows = db.scalars(select(OpsMember).where(OpsMember.project_id == project.id).order_by(OpsMember.id)).all()
    result = [{"user_id": project.owner_id, "name": project.owner.name, "dept": project.owner.dept_code,
               "role": "manager", "is_owner": True, "active": project.owner.status == 1 and not project.owner.is_deleted}]
    result += [{"user_id": r.user_id, "name": r.user.name, "dept": r.user.dept_code, "role": r.role, "is_owner": False,
                "active": r.user.status == 1 and not r.user.is_deleted} for r in rows if r.user_id != project.owner_id]
    return result


def set_member(db, p, project_id, body):
    project = get_project(db, p, project_id, lock=True)
    manage(db, p, project)
    user(db, body.user_id)
    if body.user_id == project.owner_id:
        raise bad_request("项目负责人的权限随负责人设置维护")
    row = db.scalar(select(OpsMember).where(OpsMember.project_id == project.id, OpsMember.user_id == body.user_id))
    if body.role == "viewer" and db.scalar(select(OpsTask.id).where(
            OpsTask.project_id == project.id, OpsTask.owner_id == body.user_id, OpsTask.status != "done", ~policy.archived()).limit(1)):
        raise conflict("该成员仍有未完成任务，请先转交任务再改为只读")
    if body.role not in {"manager", "co_owner", "reviewer"} and db.scalar(select(OpsTask.id).where(
            OpsTask.project_id == project.id, OpsTask.department_reviewer_id == body.user_id,
            OpsTask.approval_status.in_(["department_pending", "company_pending"]), ~policy.archived()).limit(1)):
        raise conflict("该成员仍是待审核任务的部门审核人，请先调整任务审核人")
    return _write(db, p, row or OpsMember(project_id=project.id), body.model_dump(), ["user_id", "role"])


def delete_task(db, p, task_id: int) -> None:
    """兼容旧客户端：作废保留节点、反馈、审核和延期记录。"""
    policy.archive(db, p, task_id, "从旧版任务菜单作废")


def remove_member(db, p, project_id, user_id, transfer: bool = False):
    project = get_project(db, p, project_id, lock=True)
    manage(db, p, project)
    if user_id == project.owner_id:
        raise bad_request("请先更换项目负责人")
    row = db.scalar(select(OpsMember).where(OpsMember.project_id == project.id, OpsMember.user_id == user_id))
    if not row:
        raise not_found("团队成员")
    unfinished = db.scalars(select(OpsTask).where(OpsTask.project_id == project.id, OpsTask.owner_id == user_id,
                                                  OpsTask.status != "done", ~policy.archived())).all()
    if unfinished and not transfer:
        raise conflict(f"该成员仍有 {len(unfinished)} 项未完成任务，可转交给项目负责人后移除",
                       {"unfinished": len(unfinished), "can_transfer": True})
    reviewing = db.scalars(select(OpsTask).where(OpsTask.project_id == project.id, OpsTask.department_reviewer_id == user_id,
                                                OpsTask.approval_status.in_(["department_pending", "company_pending"]), ~policy.archived())).all()
    if reviewing and not transfer:
        raise conflict(f"该成员仍有 {len(reviewing)} 项待审核任务，可转交给项目负责人后移除",
                       {"reviewing": len(reviewing), "can_transfer": True})
    if unfinished:
        db.execute(update(OpsTask).where(OpsTask.id.in_([t.id for t in unfinished])).values(owner_id=project.owner_id))
    if reviewing:
        db.execute(update(OpsTask).where(OpsTask.id.in_([t.id for t in reviewing])).values(department_reviewer_id=project.owner_id))
    log_op(db, p.id, "operations.member.remove", "ops_member", row.id, before=snapshot(row, ["user_id", "role"]),
           after={"transferred_tasks": len(unfinished), "transferred_reviews": len(reviewing), "to": project.owner_id})
    db.execute(delete(OpsTaskAudience).where(OpsTaskAudience.user_id == user_id,
        OpsTaskAudience.task_id.in_(select(OpsTask.id).where(OpsTask.project_id == project.id))))
    db.delete(row)


def school(db, project_id, school_id):
    row = db.get(OpsSchool, school_id)
    if not row or row.project_id != project_id:
        raise not_found("本项目学校")
    return row


def save_school(db, p, project_id, body, school_id=None):
    project = get_project(db, p, project_id, lock=True)
    manage(db, p, project)
    content_scope.require_school_features(db, p, project)
    row = school(db, project.id, school_id) if school_id else OpsSchool(project_id=project.id)
    if db.scalar(select(OpsSchool.id).where(OpsSchool.project_id == project.id, OpsSchool.name == body.name,
                                          OpsSchool.id != (school_id or 0))):
        raise conflict("本项目已存在同名学校")
    enabled = content_scope.enabled_features(db, project)
    ignored = set()
    if "outreach" not in enabled:
        ignored |= {"stage", "potential", "next_action", "next_contact_date"}
    if "procurement" not in enabled:
        ignored |= {"labor_cost", "other_cost"}
    return _write(db, p, row, body.model_dump(exclude=ignored), SCHOOL_FIELDS)


def delete_school(db, p, project_id, school_id):
    """删除学校及其设备清单、每日收入、跟进与年度目标，并解除任务关联。

    学校资料在采购、开拓、收入各视图共用，删除后各视图都不再出现。
    """
    project = get_project(db, p, project_id, lock=True)
    manage(db, p, project)
    content_scope.require_school_features(db, p, project)
    row = school(db, project.id, school_id)
    before = snapshot(row, SCHOOL_FIELDS)
    tasks = db.execute(update(OpsTask).where(OpsTask.school_id == row.id).values(school_id=None)).rowcount or 0
    equipment = db.execute(delete(OpsEquipment).where(OpsEquipment.school_id == row.id)).rowcount or 0
    incomes = db.execute(delete(OpsIncome).where(OpsIncome.school_id == row.id)).rowcount or 0
    visits = db.execute(delete(OpsSchoolVisit).where(OpsSchoolVisit.school_id == row.id)).rowcount or 0
    goals = db.execute(delete(OpsOutreachGoal).where(OpsOutreachGoal.school_id == row.id)).rowcount or 0
    db.execute(delete(OpsSchoolRegion).where(OpsSchoolRegion.school_id == row.id))
    db.delete(row)
    db.flush()
    log_op(db, p.id, "operations.school_delete", "ops_school", school_id, before=before,
           after={"deleted": True, "equipment": equipment, "incomes": incomes, "visits": visits,
                  "goals": goals, "tasks_detached": tasks})
    return {"id": school_id, "equipment": equipment, "incomes": incomes, "visits": visits,
            "goals": goals, "tasks_detached": tasks}


def save_product(db, p, project_id, body, product_id=None):
    project = get_project(db, p, project_id, lock=True)
    manage(db, p, project)
    content_scope.require(db, p, project, "products")
    row = db.get(OpsProduct, product_id) if product_id else OpsProduct(project_id=project.id)
    if not row or row.project_id != project.id:
        raise not_found("本项目产品")
    return _write(db, p, row, body.model_dump(), PRODUCT_FIELDS)


def product_dict(row):
    return {"id": row.id, "project_id": row.project_id, **snapshot(row, PRODUCT_FIELDS)}


def save_equipment(db, p, project_id, school_id, body, equipment_id=None):
    project = get_project(db, p, project_id, lock=True)
    manage(db, p, project)
    content_scope.require(db, p, project, "procurement")
    school(db, project.id, school_id)
    # 产品可以复用本人有权查看的其他运营项目，但不能借 ID 读取未授权产品。
    product = db.scalar(select(OpsProduct).where(OpsProduct.id == body.product_id,
        OpsProduct.project_id.in_(content_scope.content_project_ids(db, p, "products"))))
    if not product:
        raise not_found("可用产品")
    if body.unit_price is not None and body.unit_price * body.quantity >= Decimal("1000000000000"):
        raise bad_request("设备总金额超出支持范围")
    row = db.get(OpsEquipment, equipment_id) if equipment_id else OpsEquipment(school_id=school_id)
    if not row or row.school_id != school_id:
        raise not_found("本学校设备")
    return _write(db, p, row, body.model_dump(), ["product_id", "quantity", "unit_price", "recorded_amount", "review_note", "note"])


def save_income(db, p, project_id, school_id, body, income_id=None):
    project = get_project(db, p, project_id, lock=True)
    manage(db, p, project)
    content_scope.require(db, p, project, "income")
    school(db, project.id, school_id)
    if body.income_date > today():
        raise bad_request("收入日期不能晚于今天")
    row = db.get(OpsIncome, income_id) if income_id else OpsIncome(school_id=school_id)
    if not row or row.school_id != school_id:
        raise not_found("本学校收入")
    if db.scalar(select(OpsIncome.id).where(OpsIncome.school_id == school_id, OpsIncome.income_date == body.income_date,
                                          OpsIncome.id != (income_id or 0))):
        raise conflict("该学校当日收入已登记，请编辑原记录，避免重复累计")
    row.operator_id = p.id
    return _write(db, p, row, body.model_dump(), ["income_date", "amount", "source", "note", "operator_id"])


def school_data(db, project):
    from app.models import OpsSchoolVisit, OpsSchoolRegion
    rows = db.scalars(select(OpsSchool).where(OpsSchool.project_id == project.id).order_by(OpsSchool.id)).all()
    ids = [r.id for r in rows]
    regions = dict(db.execute(select(OpsSchoolRegion.school_id, OpsSchoolRegion.region).where(OpsSchoolRegion.school_id.in_(ids))).all())
    visits = {}
    for visit, name in db.execute(select(OpsSchoolVisit, SysUser.name).join(SysUser, SysUser.id == OpsSchoolVisit.author_id)
            .where(OpsSchoolVisit.school_id.in_(ids)).order_by(OpsSchoolVisit.school_id, OpsSchoolVisit.occurred_on.desc(), OpsSchoolVisit.id.desc())):
        visits.setdefault(visit.school_id, {"occurred_on": visit.occurred_on, "author": name, "content": visit.content})
    equipment, incomes = defaultdict(list), defaultdict(list)
    for e in db.scalars(select(OpsEquipment).where(OpsEquipment.school_id.in_(ids)).order_by(OpsEquipment.id)):
        calculated = e.unit_price * e.quantity if e.unit_price is not None else None
        amount = e.recorded_amount if e.recorded_amount is not None else calculated
        equipment[e.school_id].append({"id": e.id, "product_id": e.product_id, "product_name": e.product.name,
            "brand": e.product.brand, "model": e.product.model, "unit": e.product.unit, "quantity": e.quantity,
            "unit_price": e.unit_price, "amount": amount, "calculated_amount": calculated,
            "recorded_amount": e.recorded_amount, "review_note": e.review_note, "note": e.note})
    for r in db.scalars(select(OpsIncome).where(OpsIncome.school_id.in_(ids)).order_by(OpsIncome.income_date.desc(), OpsIncome.id.desc())):
        incomes[r.school_id].append({"id": r.id, **snapshot(r, ["income_date", "amount", "source", "note"])})
    result = []
    for s in rows:
        cost = sum((e["amount"] for e in equipment[s.id] if e["amount"] is not None), ZERO)
        income = sum((Decimal(i["amount"]) for i in incomes[s.id]), ZERO)
        result.append({"id": s.id, **snapshot(s, SCHOOL_FIELDS), "equipment": equipment[s.id], "incomes": incomes[s.id],
                       "region": regions.get(s.id, "待定"),
                       "latest_visit": visits.get(s.id),
                       "equipment_cost": cost, "total_investment": cost + s.labor_cost + s.other_cost, "total_income": income,
                       "unpriced_count": sum(e["amount"] is None for e in equipment[s.id]),
                       "review_count": sum(bool(e["review_note"]) for e in equipment[s.id])})
    return result


def get_task(db, p, task_id, *, lock=False, archived=False):
    q = select(OpsTask).where(OpsTask.id == task_id, policy.task_access(p, archive=archived))
    if lock:
        q = q.with_for_update(of=OpsTask)
    task = db.scalar(q)
    if not task:
        raise not_found("运营任务")
    return task


def can_feedback(db, p, project, task):
    r = role(db, p, project)
    return task.approval_status == "approved" and (r == "manager" or (r in {"member", "reviewer"} and task.owner_id == p.id))


def _event(db, p, task, kind, content, next_step=""):
    event = OpsTaskUpdate(task_id=task.id, author_id=p.id, kind=kind, content=content, next_step=next_step,
                         status=task.status, progress=task.progress, created_at=now())
    db.add(event)
    return event


def _notify(db, recipients, project, task, title, content, *, assignment=False):
    from app.core.deps import build_principal

    db.flush()
    users = [u for u in db.scalars(select(SysUser).where(SysUser.id.in_(set(recipients))))
             if db.scalar(select(OpsTask.id).where(OpsTask.id == task.id, policy.task_access(build_principal(u))))]
    notify_service.send(db, users, "operations_assignment" if assignment else "operations_task", title, content,
                        link=f"/operations/{project.id}?task={task.id}", ref_type="ops_task", ref_id=str(task.id),
                        wecom=assignment and content_scope.controls(db, project.id)["wecom_reminders"], defer=assignment,
                        wecom_content="你有一项新的运营任务安排，请进入工作台查看。")


def _create_project_plan(db, p, project, entry):
    title = entry.next_plan.splitlines()[0][:160].rstrip()
    task = OpsTask(project_id=project.id, creator_id=p.id, owner_id=project.owner_id, created_at=now(),
                   title=title, description=entry.next_plan if title != entry.next_plan else "", due_date=entry.next_due_date,
                   original_due_date=entry.next_due_date, plan_month=entry.next_due_date.strftime("%Y-%m"),
                   source="project_plan", approval_status="approved", priority="normal", cadence="node")
    db.add(task)
    db.flush()
    _event(db, p, task, "planned", f"登记负责人计划，计划完成日期 {task.due_date}。")
    log_op(db, p.id, "operations.task.create", "ops_task", task.id, after=snapshot(task, TASK_FIELDS))
    if project.owner_id != p.id:
        _notify(db, [project.owner_id], project, task, "收到项目计划事项", f"{project.name}：{task.title}，截止 {task.due_date}。", assignment=True)
    return task


def create_task(db, p, project_id, body):
    from app.services import operations_plans as plans
    project = get_project(db, p, project_id, lock=True)
    active(project)
    if body.kind == "outreach":
        content_scope.require(db, p, project, "outreach")
    if body.source == "company":
        manage(db, p, project)
    elif role(db, p, project) not in {"manager", "member", "reviewer"} or body.owner_id != p.id:
        raise forbidden("自行安排只能由项目执行成员为本人提交")
    assigned = assignee(db, project, body.owner_id)
    if body.school_id:
        school(db, project.id, body.school_id)
    values = body.model_dump(exclude={"nodes", *POLICY_FIELDS})
    values["plan_month"] = body.plan_month or body.due_date.strftime("%Y-%m")
    values["approval_status"] = "approved" if body.source == "company" else "department_pending"
    if body.source == "self":
        plans.validate_reviewers(db, project, p.id, body.department_reviewer_id, body.company_reviewer_id)
    else:
        values["department_reviewer_id"] = values["company_reviewer_id"] = None
        values["original_due_date"] = body.due_date
    task = OpsTask(project_id=project.id, creator_id=p.id, created_at=now(), **values)
    db.add(task)
    db.flush()
    task.nodes = [OpsTaskNode(task_id=task.id, seq=i, **node.model_dump()) for i, node in enumerate(body.nodes, 1)]
    policy.update_policy(db, p, project, task, {k: v for k, v in body.model_dump(exclude_unset=True).items()
                        if k in POLICY_FIELDS and (body.source == "company" or k == "kind")})
    if body.source == "self" and (body.visibility != "team" or body.viewer_ids):
        raise bad_request("自行安排任务由项目团队查看")
    _event(db, p, task, "assigned" if body.source == "company" else "submitted",
           f"{'公司安排' if body.source == 'company' else '自行安排，提交部门审核'}：{assigned.name}，计划月份 {task.plan_month}，要求 {task.due_date} 前完成。")
    log_op(db, p.id, "operations.task.create", "ops_task", task.id, after=snapshot(task, TASK_FIELDS))
    target = assigned.id if body.source == "company" else task.department_reviewer_id
    _notify(db, [target], project, task, "收到运营项目任务" if body.source == "company" else "任务等待部门审核",
            f"{project.name}：{task.title}，计划月份 {task.plan_month}，截止 {task.due_date}。", assignment=body.source == "company")
    db.flush()
    return task


def edit_task(db, p, task_id, body):
    from app.services import operations_plans as plans
    task = get_task(db, p, task_id, lock=True)
    project = get_project(db, p, task.project_id, lock=True)
    if not (task.source == "self" and task.creator_id == p.id and role(db, p, project) in {"manager", "member", "reviewer"}):
        manage(db, p, project)
    active(project)
    if task.status == "done":
        raise conflict("已完成任务保留历史安排；新工作请另建任务")
    values = body.model_dump(exclude_unset=True)
    expected_revision = values.pop("expected_revision", None)
    if expected_revision is not None and expected_revision != task.revision:
        raise conflict("任务安排已更新，请刷新后核对再保存")
    if not values or any(v is None for v in values.values()):
        raise bad_request("请提供有效的任务修改内容")
    policy_values = {k: values.pop(k) for k in tuple(values) if k in POLICY_FIELDS}
    if task.source == "self" and policy_values.keys() & {"visibility", "viewer_ids"}:
        raise bad_request("自行安排任务由项目团队查看")
    if "school_id" in values:
        school(db, project.id, values["school_id"])
    if "owner_id" in values:
        assignee(db, project, values["owner_id"])
    if "due_date" in values and any(n.due_date > values["due_date"] for n in task.nodes):
        raise bad_request("任务截止日期不能早于已有节点日期")
    previous = task.owner_id
    before = snapshot(task, TASK_FIELDS)
    if task.source == "self":
        if values.get("owner_id", task.owner_id) != task.creator_id:
            raise bad_request("自行安排任务归属于提交人，转交其他人请由公司另行安排")
        plans.validate_reviewers(db, project, task.owner_id, values.get("department_reviewer_id", task.department_reviewer_id),
                                 values.get("company_reviewer_id", task.company_reviewer_id))
    elif "department_reviewer_id" in values or "company_reviewer_id" in values:
        raise bad_request("此任务无需指定两级审核人")
    if "due_date" in values and task.approval_status == "approved" and task.original_due_date is None:
        task.original_due_date = task.due_date
    _write(db, p, task, values, TASK_FIELDS)
    policy.update_policy(db, p, project, task, policy_values)
    labels = {"school_id": "学校", "title": "任务", "description": "要求", "owner_id": "负责人", "due_date": "截止日期", "priority": "重点程度", "cadence": "反馈频率", "plan_month": "计划月份", "department_reviewer_id": "部门审核人", "company_reviewer_id": "公司审核人"}
    changed = "；".join(f"{labels[k]}：{before[k]} → {snapshot(task, TASK_FIELDS)[k]}" for k in values if before[k] != snapshot(task, TASK_FIELDS)[k])
    _event(db, p, task, "changed", changed or "核对任务安排。")
    task.revision += 1
    if task.source == "self":
        task.approval_status = "department_pending"
        _event(db, p, task, "submitted", "任务内容已调整，重新提交部门及公司两级审核。")
        _notify(db, [task.department_reviewer_id], project, task, "任务重新提交审核", f"{task.title}已修改，请核对最新安排。")
    log_op(db, p.id, "operations.task.revised", "ops_task", task.id, before=before, after=snapshot(task, TASK_FIELDS))
    if previous != task.owner_id:
        _notify(db, [task.owner_id], project, task, "运营任务已转交给你", f"{project.name}：{task.title}，截止 {task.due_date}。", assignment=True)
    return task


def effective_task_progress(task, reported=None):
    if task.status == "done":
        return 100
    total = len(task.nodes)
    completed = sum(node.completed_at is not None for node in task.nodes)
    node_progress = (100 * completed + total // 2) // total if total else 0
    # Node completions raise the floor; 100% remains reserved for confirmed completion.
    return min(99, max(task.progress if reported is None else reported, node_progress))


def feedback(db, p, task_id, body):
    task = get_task(db, p, task_id, lock=True)
    project = get_project(db, p, task.project_id, lock=True)
    active(project)
    if not can_feedback(db, p, project, task):
        raise forbidden("仅任务负责人或项目管理员可反馈")
    if body.expected_revision is not None and task.revision != body.expected_revision:
        raise conflict("任务在反馈期间已更新，请核对最新进展后再提交")
    if task.status == "done":
        raise conflict("任务已完成，历史反馈予以保留；新的工作请另建任务")
    followups.validate_node(task, body.node_id)
    if body.status != "done" and (body.status == "blocked" or task.due_date < today() or
                                  any(not n.completed_at and n.due_date < today() and n.id not in body.completed_node_ids for n in task.nodes)) and not body.incomplete_reason:
        raise bad_request("受阻或逾期任务请填写未完成原因")
    requested = set(body.completed_node_ids)
    nodes = {n.id: n for n in task.nodes}
    if not requested.issubset(nodes):
        raise bad_request("完成节点须属于当前任务")
    if body.status == "done" and any(not n.completed_at and n.id not in requested for n in task.nodes):
        raise bad_request("请先确认所有阶段节点完成，再提交任务完成结果")
    if body.status != "done" and body.progress == 100:
        raise bad_request("进度 100% 时请选择已完成，并确认所有节点")
    before = snapshot(task, TASK_FIELDS)
    completed = []
    for node_id in requested:
        if not nodes[node_id].completed_at:
            nodes[node_id].completed_at = now()
            completed.append(nodes[node_id].name)
    task.status = body.status
    task.progress = effective_task_progress(task, body.progress)
    task.next_step = body.next_step
    task.last_feedback_at = now()
    task.completed_at = now() if body.status == "done" else None
    task.incomplete_reason = "" if body.status == "done" else body.incomplete_reason
    deadlines.capture_feedback_reason(db, p, task, body.incomplete_reason, today())
    task.revision += 1
    content = body.content + ("\n完成节点：" + "、".join(completed) if completed else "")
    if task.incomplete_reason:
        content += "\n未完成原因：" + task.incomplete_reason
    event = _event(db, p, task, "feedback", content, body.next_step)
    if body.node_id:
        from app.models import OpsTaskNodeUpdate
        db.flush()
        db.add(OpsTaskNodeUpdate(update_id=event.id, node_id=body.node_id))
    log_op(db, p.id, "operations.task.feedback", "ops_task", task.id, before=before, after=snapshot(task, TASK_FIELDS))
    recipients = {project.owner_id, task.creator_id}
    # 撤销的项目成员不再收到新的反馈内容。
    allowed = {m["user_id"] for m in members(db, project)}
    for uid in list(recipients):
        u = db.get(SysUser, uid)
        from app.core.deps import build_principal
        if uid not in allowed and (not u or not can_create(build_principal(u))):
            recipients.discard(uid)
    recipients.discard(p.id)
    _notify(db, recipients, project, task, "运营任务有新进展", f"{task.title} · {STATUS_LABELS[task.status]}\n{content[:500]}")
    db.flush()
    return task


def task_dict(db, p, project, task, updates=None, viewer_role=None, delay=None, access=None):
    from app.services import operations_plans as plans
    access = access if access is not None else policy.details(db, task.id)
    if not access:
        access = {"kind": "general", "visibility": "team", "viewer_ids": [], "archived_at": None, "archive_reason": ""}
    archived = bool(access["archived_at"])
    finished = archived or task.status == "done" or task.approval_status != "approved" or project.status != "active"
    day = today()
    overdue = not finished and (task.due_date < day or any(not n.completed_at and n.due_date < day for n in task.nodes))
    cadence = followups.cadence_view(task)
    stale = not finished and task.cadence != "node" and cadence["next_feedback_on"] <= day
    my_role = viewer_role or role(db, p, project)
    stage = plans.review_stage(db, p, project, task)
    return {"id": task.id, "project_id": project.id, "project_name": project.name, **snapshot(task, TASK_FIELDS),
            **deadlines.view(p, task, my_role, delay, day),
            **access, "viewer_ids": access["viewer_ids"] if my_role == "manager" else [],
            "can_archive": (p.is_admin or p.can("operations:archive")) and my_role == "manager",
            "progress": effective_task_progress(task),
            "owner_name": task.owner.name, "school_name": task.school.name if task.school else "",
            "last_feedback_at": task.last_feedback_at, "overdue": overdue, "needs_update": stale, **cadence,
            "can_feedback": project.status == "active" and not finished and (my_role == "manager" or (my_role in {"member", "reviewer"} and task.owner_id == p.id)),
            "can_edit": not archived and project.status == "active" and task.status != "done" and (my_role == "manager" or (task.source == "self" and task.creator_id == p.id and my_role in {"member", "reviewer"})),
            "can_review": not archived and project.status == "active" and stage is not None, "review_stage": stage,
            "department_reviewer_name": task.department_reviewer.name if task.department_reviewer else None,
            "company_reviewer_name": task.company_reviewer.name if task.company_reviewer else None,
            "nodes": [{"id": n.id, "name": n.name, "due_date": n.due_date, "completed_at": n.completed_at,
                       "overdue": not n.completed_at and n.due_date < day} for n in task.nodes],
            **({"updates": updates} if updates is not None else {})}


def task_detail(db, p, task_id, *, archived=False):
    from app.services.operations_notifications import delivery_status
    task = get_task(db, p, task_id, archived=archived)
    project = get_project(db, p, task.project_id)
    if archived and not policy.can_archive(db, p, project):
        raise forbidden("仅获作废管理权限的人员可查看作废任务")
    updates = [{"id": u.id, "kind": u.kind, "author_name": u.author.name, "created_at": u.created_at,
                **snapshot(u, ["content", "next_step", "status", "progress"])} for u in db.scalars(
                    select(OpsTaskUpdate).where(OpsTaskUpdate.task_id == task.id).order_by(OpsTaskUpdate.id.desc()))]
    links = followups.node_links(db, [u["id"] for u in updates])
    for u in updates:
        u["node_id"] = links.get(u["id"])
    reviews = [{"id": r.id, "reviewer_name": r.reviewer.name, "created_at": r.created_at,
                **snapshot(r, ["stage", "decision", "comment", "task_revision"])} for r in db.scalars(
                    select(OpsTaskReview).where(OpsTaskReview.task_id == task_id).order_by(OpsTaskReview.id))]
    delay_records = deadlines.history(db, task_id)
    plan = db.scalar(select(OpsProjectProgress).where(OpsProjectProgress.task_id == task.id)) if task.source == "project_plan" else None
    return {**task_dict(db, p, project, task, updates, delay=delay_records[0] if delay_records else None),
            "reviews": reviews, "delay_history": [deadlines.reason_dict(row) for row in delay_records],
            "plan_context": progress_dict(plan) if plan else None, "today": today(),
            "discussions": followups.discussions(db, p, task),
            "notification_deliveries": delivery_status(db, p, project, task),
            "can_discuss": not archived and project.status == "active" and role(db, p, project) in {"manager", "member", "reviewer"},
            "can_request": not archived and project.status == "active" and task.status != "done" and task.approval_status == "approved" and role(db, p, project) == "manager",
            "participants": followups.participants(db, p, project, task) if not archived else []}


def tasks(db, p, project_id=None, mine=False, *, archived=False):
    projects = db.scalars(visible_projects(p).order_by(OpsProject.id)).all()
    if archived:
        projects = [project for project in projects if policy.can_archive(db, p, project)]
    by_id = {r.id: r for r in projects if project_id is None or r.id == project_id}
    q = select(OpsTask).where(OpsTask.project_id.in_(by_id), policy.task_access(p, archive=archived)).order_by(OpsTask.due_date, OpsTask.id)
    if mine:
        q = q.where(OpsTask.owner_id == p.id)
    task_rows = db.scalars(q).all()
    task_ids = [t.id for t in task_rows]
    pending_replies = followups.pending_by_task(db, p, task_ids)
    delays = deadlines.latest_by_task(db, task_ids)
    plan_ids = [t.id for t in task_rows if t.source == "project_plan"]
    contexts = {row.task_id: progress_dict(row) for row in db.scalars(
        select(OpsProjectProgress).where(OpsProjectProgress.task_id.in_(plan_ids)))} if plan_ids else {}
    latest = {}
    for update in db.scalars(select(OpsTaskUpdate).where(OpsTaskUpdate.task_id.in_(task_ids),
                            OpsTaskUpdate.kind == "feedback").order_by(OpsTaskUpdate.task_id, OpsTaskUpdate.id.desc()).distinct(OpsTaskUpdate.task_id)):
        latest[update.task_id] = update.content
    access = policy.batch_details(db, task_ids)
    roles = {id: role(db, p, project) for id, project in by_id.items()}
    rows = [{**task_dict(db, p, by_id[t.project_id], t, viewer_role=roles[t.project_id], delay=delays.get(t.id), access=access.get(t.id, {})),
             "latest_feedback": latest.get(t.id, ""), "plan_context": contexts.get(t.id),
             "pending_replies": pending_replies.get(t.id, 0)} for t in task_rows]
    return sorted(rows, key=lambda t: (t["status"] == "done", not t["overdue"], t["status"] != "blocked", t["priority"] != "key", t["due_date"], t["id"]))


def task_stats(rows):
    awaiting = sum(t["approval_status"] in {"department_pending", "company_pending"} for t in rows)
    rejected = sum(t["approval_status"] == "rejected" for t in rows)
    rows = [t for t in rows if t["approval_status"] == "approved"]
    pending = [t for t in rows if t["status"] != "done"]
    return {"total": len(rows), "pending": len(pending), "done": len(rows) - len(pending),
            "overdue": sum(t["overdue"] for t in pending), "blocked": sum(t["status"] == "blocked" for t in pending),
            "needs_update": sum(t["needs_update"] for t in pending), "awaiting_approval": awaiting, "rejected": rejected,
            "pending_replies": sum(t.get("pending_replies", 0) for t in pending),
            "on_time": sum(t["deadline_status"] == "on_time" for t in rows),
            "late_done": sum(t["deadline_status"] == "late_done" for t in rows),
            "unknown_done": sum(t["deadline_status"] == "unknown_done" for t in rows),
            "baseline_overdue": sum(t["deadline_status"] == "overdue" for t in pending),
            "delay_pending": sum(t["delay_status"] == "pending" for t in rows)}


def list_projects(db, p, keyword="", category="", key_only=False):
    q = visible_projects(p)
    if keyword:
        q = q.where(or_(OpsProject.name.ilike(f"%{keyword}%"), OpsProject.objective.ilike(f"%{keyword}%")))
    if category:
        q = q.where(OpsProject.category == category)
    if key_only:
        q = q.where(OpsProject.is_key.is_(True))
    all_tasks = tasks(db, p)
    items = [{**project_dict(db, p, r), "stats": task_stats([t for t in all_tasks if t["project_id"] == r.id])}
             for r in db.scalars(q.order_by(OpsProject.is_key.desc(), OpsProject.id.desc()))]
    active_ids = {r.id for r in db.scalars(visible_projects(p).where(OpsProject.status == "active"))}
    active_tasks = [t for t in all_tasks if t["project_id"] in active_ids]
    return {"items": items, "can_create": can_create(p), "can_delete": can_delete_project(p),
            "stats": task_stats(active_tasks),
            "todos": [t for t in active_tasks if (t["status"] != "done" and t["approval_status"] == "approved") or t.get("pending_replies")],
            "delay_review_inbox": [t for t in all_tasks if t["can_review_delay"]], "today": today()}


def project_detail(db, p, project_id):
    project = get_project(db, p, project_id)
    rows = tasks(db, p, project_id)
    shared = has_project_content(db, p, project)
    access = content_scope.view(db, p, project)
    sections = set(access["sections"])
    can_income, can_procurement = "income" in sections, "procurement" in sections
    progress = db.scalar(select(OpsProjectProgress).where(OpsProjectProgress.project_id == project.id,
        or_(OpsProjectProgress.task_id.is_(None), OpsProjectProgress.task_id.in_([t["id"] for t in rows])))
        .order_by(OpsProjectProgress.id.desc()).limit(1)) if shared and "progress" in sections else None
    schools = school_data(db, project) if shared and sections & {"outreach", "income", "procurement"} else []
    products = db.scalars(select(OpsProduct).where(OpsProduct.project_id == project.id).order_by(OpsProduct.id)).all() if shared and "products" in sections else []
    daily = defaultdict(lambda: ZERO)
    for s in schools:
        for i in s["incomes"] if can_income else []:
            daily[i["income_date"]] += Decimal(i["amount"])
    unassigned = [{"id": r.id, **snapshot(r, ["income_date", "amount", "source", "note"])} for r in db.scalars(
        select(OpsProjectIncome).where(OpsProjectIncome.project_id == project.id).order_by(OpsProjectIncome.income_date.desc()))] if shared and can_income else []
    unassigned_total = sum((Decimal(r["amount"]) for r in unassigned), ZERO)
    for r in unassigned:
        daily[r["income_date"]] += Decimal(r["amount"])
    batches = [{"id": b.id, "source_name": b.source_name, "created_at": b.created_at, "result": {k: v for k, v in b.result.items() if k != "created_ids"}} for b in db.scalars(
        select(OpsImportBatch).where(OpsImportBatch.project_id == project.id, OpsImportBatch.kind.in_(
            (["income"] if can_income else []) + (["equipment"] if can_procurement else []))).order_by(OpsImportBatch.id.desc()))] if shared else []
    return {**project_dict(db, p, project), "latest_progress": progress_dict(progress) if progress else None,
            "can_propose": role(db, p, project) in {"manager", "member", "reviewer"}, "members": members(db, project) if shared and "team" in sections else [],
            "can_record_visits": "outreach" in sections and project.status == "active" and role(db, p, project) in {"manager", "member", "reviewer"},
            "visible_sections": sorted(sections), "task_scope": access["task_scope"],
            "feature_settings": content_scope.feature_settings(db, project),
            "can_archive_tasks": policy.can_archive(db, p, project), "can_view_project_content": shared, "tasks": rows, "stats": task_stats(rows),
            "schools": content_scope.redact_schools(schools, sections), "products": [product_dict(r) for r in products], "today": today(),
            "unassigned_incomes": unassigned, "import_batches": batches,
            "financials": {"total_investment": sum((s["total_investment"] for s in schools), ZERO) if can_procurement else None,
                           "total_income": sum((s["total_income"] for s in schools), ZERO) + unassigned_total if can_income else None,
                           "unassigned_income": unassigned_total if can_income else None,
                           "last_income_date": max(daily, default=None),
                           "income_note": "按已登记的实际收款日汇总；未登记不代表零收入，最近收款日不代表已完整对账。",
                           "unpriced_count": sum(s["unpriced_count"] for s in schools) if can_procurement else None,
                           "review_count": sum(s["review_count"] for s in schools) if can_procurement else None,
                           "daily": [{"date": k, "amount": v} for k, v in sorted(daily.items())],
                           "note": "已登记投入按清单金额（有填写时）或数量 × 单价汇总，加人工及其他投入；未标价不计入已知金额。利旧、拆回、调拨及折扣请结合备注核对。收入按实际收款日期登记，不等同于财务利润。"}}


def brief(db, p, project_id):
    data = project_detail(db, p, project_id)
    pending = [t for t in data["tasks"] if t["status"] != "done" and t["approval_status"] == "approved"]
    lines = [f"{data['name']}｜{today()} 进展汇总", f"目标：{data['objective'] or '尚未填写'}"]
    progress = data["latest_progress"]
    if progress and not progress["task_id"]:
        lines += [f"项目进度：{progress['progress']}", f"遇到的问题：{progress['problems'] or '未填写'}",
                  f"下一步计划：{progress['next_plan']}（计划完成：{progress['next_due_date']}）" if progress['next_plan'] else "下一步计划：未填写"]
    elif not progress:
        lines.append("项目进度尚未填写。")
    lines.append(f"任务 {data['stats']['total']} 项，已完成 {data['stats']['done']} 项，逾期 {data['stats']['overdue']} 项，受阻 {data['stats']['blocked']} 项。")
    lines.append(f"已完成中按时 {data['stats']['on_time']} 项，逾期完成 {data['stats']['late_done']} 项，完成日期待核对 {data['stats']['unknown_done']} 项。延期原因待复核 {data['stats']['delay_pending']} 项。")
    lines.append(f"另有 {data['stats']['awaiting_approval']} 项等待审核，{data['stats']['rejected']} 项已退回，尚未进入执行。")
    for source, label in SOURCE_LABELS.items():
        opened = [t for t in pending if t["source"] == source]
        completed = [t for t in data["tasks"] if t["source"] == source and t["status"] == "done"]
        if not (opened or completed):
            continue
        lines.append(f"{label}：")
        for t in opened:
            flags = "、".join(x for x, yes in [("逾期", t["overdue"]), ("今日待反馈", t["needs_update"])] if yes)
            context = t["plan_context"] or {}
            current = t["latest_feedback"] or context.get("progress") or "尚无进展反馈"
            problems = t["incomplete_reason"] if t["last_feedback_at"] else context.get("problems")
            lines.append(f"- {t['title']}：{t['owner_name']}，{STATUS_LABELS[t['status']]} {t['progress']}%，截止 {t['due_date']}" + (f"（{flags}）" if flags else "") + f"；进展：{current}" + (f"；问题：{problems}" if problems else "") + (f"；下一步：{t['next_step']}" if t['next_step'] else ""))
        for t in completed:
            lines.append(f"- {deadlines.DEADLINE_LABELS.get(t['deadline_status'], '已完成')}：{t['title']}（{t['owner_name']}）；结果：{t['latest_feedback'] or '尚无文字反馈'}")
    return {"text": "\n".join(lines), "project": data, "source": "已登记的任务、节点和反馈", "generated_at": now()}

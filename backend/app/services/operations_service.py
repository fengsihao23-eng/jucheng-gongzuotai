"""运营项目服务。所有入口共享成员权限、金额口径及任务反馈规则。"""
from collections import defaultdict
from decimal import Decimal

from sqlalchemy import or_, select

from app.core.audit import log_op, snapshot
from app.core.response import bad_request, conflict, forbidden, not_found
from app.core.timeutil import TZ, now, today
from app.models import (OpsEquipment, OpsImportBatch, OpsIncome, OpsMember, OpsProduct, OpsProject, OpsProjectIncome, OpsProjectProgress, OpsSchool,
                        OpsTask, OpsTaskNode, OpsTaskReview, OpsTaskUpdate, SysUser)
from app.services import notify_service

PROJECT_FIELDS = ["name", "category", "owner_id", "objective", "target_date", "status", "is_key"]
PROJECT_PROGRESS_FIELDS = ["progress", "problems", "next_plan", "next_due_date"]
SCHOOL_FIELDS = ["name", "stage", "potential", "next_action", "next_contact_date", "labor_cost", "other_cost", "note"]
PRODUCT_FIELDS = ["name", "brand", "model", "unit", "reference_price", "note"]
TASK_FIELDS = ["title", "description", "owner_id", "school_id", "due_date", "priority", "cadence", "status", "progress", "next_step"]
TASK_FIELDS += ["plan_month", "source", "approval_status", "department_reviewer_id", "company_reviewer_id", "revision", "completed_at", "incomplete_reason"]
STATUS_LABELS = {"todo": "待开始", "doing": "进行中", "blocked": "遇到阻碍", "done": "已完成"}
ZERO = Decimal("0.00")


def can_create(p):
    return p.is_admin or p.can("operations:manage")


def visible_projects(p):
    q = select(OpsProject)
    if not can_create(p):
        conditions = [OpsProject.owner_id == p.id, OpsProject.id.in_(select(OpsMember.project_id).where(OpsMember.user_id == p.id))]
        if p.can("operations:approve"):
            conditions.append(OpsProject.id.in_(select(OpsTask.project_id).where(OpsTask.company_reviewer_id == p.id)))
        q = q.where(or_(*conditions))
    return q


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
    return db.scalar(select(OpsMember.role).where(OpsMember.project_id == project.id, OpsMember.user_id == p.id))


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
    if project.owner_id != user_id and member_role not in {"manager", "member", "reviewer"}:
        raise bad_request("任务负责人须为本项目可参与执行的成员，请先在团队中添加")
    return u


def project_dict(db, p, project):
    my_role = role(db, p, project)
    return {"id": project.id, **snapshot(project, PROJECT_FIELDS), "owner_name": project.owner.name,
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
    user(db, body.owner_id)
    return _write(db, p, project, body.model_dump(), PROJECT_FIELDS)


def progress_dict(row):
    return {"id": row.id, "project_id": row.project_id, "author_name": row.author.name, "created_at": row.created_at,
            **snapshot(row, PROJECT_PROGRESS_FIELDS)}


def project_progress(db, p, project_id, before_id=None):
    get_project(db, p, project_id)
    query = select(OpsProjectProgress).where(OpsProjectProgress.project_id == project_id)
    if before_id is not None:
        query = query.where(OpsProjectProgress.id < before_id)
    rows = db.scalars(query.order_by(OpsProjectProgress.id.desc()).limit(26)).all()
    return {"items": [progress_dict(row) for row in rows[:25]], "next_before_id": rows[24].id if len(rows) > 25 else None}


def record_project_progress(db, p, project_id, body):
    project = get_project(db, p, project_id, lock=True)
    manage(db, p, project)
    row = OpsProjectProgress(project_id=project.id, author_id=p.id, created_at=now())
    return progress_dict(_write(db, p, row, body.model_dump(), PROJECT_PROGRESS_FIELDS))


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
            OpsTask.project_id == project.id, OpsTask.owner_id == body.user_id, OpsTask.status != "done").limit(1)):
        raise conflict("该成员仍有未完成任务，请先转交任务再改为只读")
    if body.role not in {"manager", "reviewer"} and db.scalar(select(OpsTask.id).where(
            OpsTask.project_id == project.id, OpsTask.department_reviewer_id == body.user_id,
            OpsTask.approval_status.in_(["department_pending", "company_pending"])).limit(1)):
        raise conflict("该成员仍是待审核任务的部门审核人，请先调整任务审核人")
    return _write(db, p, row or OpsMember(project_id=project.id), body.model_dump(), ["user_id", "role"])


def remove_member(db, p, project_id, user_id):
    project = get_project(db, p, project_id, lock=True)
    manage(db, p, project)
    if user_id == project.owner_id:
        raise bad_request("请先更换项目负责人")
    row = db.scalar(select(OpsMember).where(OpsMember.project_id == project.id, OpsMember.user_id == user_id))
    if not row:
        raise not_found("团队成员")
    if db.scalar(select(OpsTask.id).where(OpsTask.project_id == project.id, OpsTask.owner_id == user_id,
                                        OpsTask.status != "done").limit(1)):
        raise conflict("该成员仍有未完成任务，请先转交任务")
    if db.scalar(select(OpsTask.id).where(OpsTask.project_id == project.id, OpsTask.department_reviewer_id == user_id,
                                        OpsTask.approval_status.in_(["department_pending", "company_pending"])).limit(1)):
        raise conflict("该成员仍有待审核任务，请先调整审核人")
    log_op(db, p.id, "operations.member.remove", "ops_member", row.id, before=snapshot(row, ["user_id", "role"]))
    db.delete(row)


def school(db, project_id, school_id):
    row = db.get(OpsSchool, school_id)
    if not row or row.project_id != project_id:
        raise not_found("本项目学校")
    return row


def save_school(db, p, project_id, body, school_id=None):
    project = get_project(db, p, project_id, lock=True)
    manage(db, p, project)
    row = school(db, project.id, school_id) if school_id else OpsSchool(project_id=project.id)
    if db.scalar(select(OpsSchool.id).where(OpsSchool.project_id == project.id, OpsSchool.name == body.name,
                                          OpsSchool.id != (school_id or 0))):
        raise conflict("本项目已存在同名学校")
    return _write(db, p, row, body.model_dump(), SCHOOL_FIELDS)


def save_product(db, p, project_id, body, product_id=None):
    project = get_project(db, p, project_id, lock=True)
    manage(db, p, project)
    row = db.get(OpsProduct, product_id) if product_id else OpsProduct(project_id=project.id)
    if not row or row.project_id != project.id:
        raise not_found("本项目产品")
    return _write(db, p, row, body.model_dump(), PRODUCT_FIELDS)


def product_dict(row):
    return {"id": row.id, "project_id": row.project_id, **snapshot(row, PRODUCT_FIELDS)}


def save_equipment(db, p, project_id, school_id, body, equipment_id=None):
    project = get_project(db, p, project_id, lock=True)
    manage(db, p, project)
    school(db, project.id, school_id)
    # 产品可以复用本人有权查看的其他运营项目，但不能借 ID 读取未授权产品。
    product = db.scalar(select(OpsProduct).where(OpsProduct.id == body.product_id,
        OpsProduct.project_id.in_(visible_projects(p).with_only_columns(OpsProject.id))))
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
    rows = db.scalars(select(OpsSchool).where(OpsSchool.project_id == project.id).order_by(OpsSchool.id)).all()
    ids = [r.id for r in rows]
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
                       "equipment_cost": cost, "total_investment": cost + s.labor_cost + s.other_cost, "total_income": income,
                       "unpriced_count": sum(e["amount"] is None for e in equipment[s.id]),
                       "review_count": sum(bool(e["review_note"]) for e in equipment[s.id])})
    return result


def get_task(db, p, task_id, *, lock=False):
    q = select(OpsTask).where(OpsTask.id == task_id,
        OpsTask.project_id.in_(visible_projects(p).with_only_columns(OpsProject.id)))
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
    db.add(OpsTaskUpdate(task_id=task.id, author_id=p.id, kind=kind, content=content, next_step=next_step,
                         status=task.status, progress=task.progress, created_at=now()))


def _notify(db, recipients, project, task, title, content):
    users = db.scalars(select(SysUser).where(SysUser.id.in_(set(recipients)))).all()
    notify_service.send(db, users, "operations_task", title, content,
                        link=f"/operations/{project.id}?task={task.id}", ref_type="ops_task", ref_id=str(task.id), wecom=False)


def create_task(db, p, project_id, body):
    from app.services import operations_plans as plans
    project = get_project(db, p, project_id, lock=True)
    active(project)
    if body.source == "company":
        manage(db, p, project)
    elif role(db, p, project) not in {"manager", "member", "reviewer"} or body.owner_id != p.id:
        raise forbidden("自行安排只能由项目执行成员为本人提交")
    assigned = assignee(db, project, body.owner_id)
    if body.school_id:
        school(db, project.id, body.school_id)
    values = body.model_dump(exclude={"nodes"})
    values["plan_month"] = body.plan_month or body.due_date.strftime("%Y-%m")
    values["approval_status"] = "approved" if body.source == "company" else "department_pending"
    if body.source == "self":
        plans.validate_reviewers(db, project, p.id, body.department_reviewer_id, body.company_reviewer_id)
    else:
        values["department_reviewer_id"] = values["company_reviewer_id"] = None
    task = OpsTask(project_id=project.id, creator_id=p.id, created_at=now(), **values)
    db.add(task)
    db.flush()
    task.nodes = [OpsTaskNode(task_id=task.id, seq=i, **node.model_dump()) for i, node in enumerate(body.nodes, 1)]
    _event(db, p, task, "assigned" if body.source == "company" else "submitted",
           f"{'公司安排' if body.source == 'company' else '自行安排，提交部门审核'}：{assigned.name}，计划月份 {task.plan_month}，要求 {task.due_date} 前完成。")
    log_op(db, p.id, "operations.task.create", "ops_task", task.id, after=snapshot(task, TASK_FIELDS))
    target = assigned.id if body.source == "company" else task.department_reviewer_id
    _notify(db, [target], project, task, "收到运营项目任务" if body.source == "company" else "任务等待部门审核",
            f"{project.name}：{task.title}，计划月份 {task.plan_month}，截止 {task.due_date}。")
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
    if not values or any(v is None for v in values.values()):
        raise bad_request("请提供有效的任务修改内容")
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
        raise bad_request("公司安排任务无需指定两级审核人")
    _write(db, p, task, values, TASK_FIELDS)
    labels = {"title": "任务", "description": "要求", "owner_id": "负责人", "due_date": "截止日期", "priority": "重点程度", "cadence": "反馈频率", "plan_month": "计划月份", "department_reviewer_id": "部门审核人", "company_reviewer_id": "公司审核人"}
    changed = "；".join(f"{labels[k]}：{before[k]} → {snapshot(task, TASK_FIELDS)[k]}" for k in values if before[k] != snapshot(task, TASK_FIELDS)[k])
    _event(db, p, task, "changed", changed or "核对任务安排。")
    task.revision += 1
    if task.source == "self":
        task.approval_status = "department_pending"
        _event(db, p, task, "submitted", "任务内容已调整，重新提交部门及公司两级审核。")
        _notify(db, [task.department_reviewer_id], project, task, "任务重新提交审核", f"{task.title}已修改，请核对最新安排。")
    log_op(db, p.id, "operations.task.revised", "ops_task", task.id, before=before, after=snapshot(task, TASK_FIELDS))
    if previous != task.owner_id:
        _notify(db, [task.owner_id], project, task, "运营任务已转交给你", f"{project.name}：{task.title}，截止 {task.due_date}。")
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
    task.revision += 1
    content = body.content + ("\n完成节点：" + "、".join(completed) if completed else "")
    if task.incomplete_reason:
        content += "\n未完成原因：" + task.incomplete_reason
    _event(db, p, task, "feedback", content, body.next_step)
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


def task_dict(db, p, project, task, updates=None, viewer_role=None):
    from app.services import operations_plans as plans
    finished = task.status == "done" or task.approval_status != "approved"
    overdue = not finished and (task.due_date < today() or any(not n.completed_at and n.due_date < today() for n in task.nodes))
    stale = not finished and task.cadence == "daily" and (not task.last_feedback_at or task.last_feedback_at.astimezone(TZ).date() < today())
    my_role = viewer_role or role(db, p, project)
    stage = plans.review_stage(db, p, project, task)
    return {"id": task.id, "project_id": project.id, "project_name": project.name, **snapshot(task, TASK_FIELDS),
            "progress": effective_task_progress(task),
            "owner_name": task.owner.name, "school_name": task.school.name if task.school else "",
            "last_feedback_at": task.last_feedback_at, "overdue": overdue, "needs_update": stale,
            "can_feedback": project.status == "active" and not finished and (my_role == "manager" or (my_role in {"member", "reviewer"} and task.owner_id == p.id)),
            "can_edit": project.status == "active" and task.status != "done" and (my_role == "manager" or (task.source == "self" and task.creator_id == p.id and my_role in {"member", "reviewer"})),
            "can_review": project.status == "active" and stage is not None, "review_stage": stage,
            "department_reviewer_name": task.department_reviewer.name if task.department_reviewer else None,
            "company_reviewer_name": task.company_reviewer.name if task.company_reviewer else None,
            "nodes": [{"id": n.id, "name": n.name, "due_date": n.due_date, "completed_at": n.completed_at,
                       "overdue": not n.completed_at and n.due_date < today()} for n in task.nodes],
            **({"updates": updates} if updates is not None else {})}


def task_detail(db, p, task_id):
    task = get_task(db, p, task_id)
    project = get_project(db, p, task.project_id)
    updates = [{"id": u.id, "kind": u.kind, "author_name": u.author.name, "created_at": u.created_at,
                **snapshot(u, ["content", "next_step", "status", "progress"])} for u in db.scalars(
                    select(OpsTaskUpdate).where(OpsTaskUpdate.task_id == task.id).order_by(OpsTaskUpdate.id.desc()))]
    reviews = [{"id": r.id, "reviewer_name": r.reviewer.name, "created_at": r.created_at,
                **snapshot(r, ["stage", "decision", "comment", "task_revision"])} for r in db.scalars(
                    select(OpsTaskReview).where(OpsTaskReview.task_id == task_id).order_by(OpsTaskReview.id))]
    return {**task_dict(db, p, project, task, updates), "reviews": reviews, "today": today()}


def tasks(db, p, project_id=None, mine=False):
    projects = db.scalars(visible_projects(p).order_by(OpsProject.id)).all()
    by_id = {r.id: r for r in projects if project_id is None or r.id == project_id}
    q = select(OpsTask).where(OpsTask.project_id.in_(by_id)).order_by(OpsTask.due_date, OpsTask.id)
    if mine:
        q = q.where(OpsTask.owner_id == p.id)
    task_rows = db.scalars(q).all()
    latest = {}
    for update in db.scalars(select(OpsTaskUpdate).where(OpsTaskUpdate.task_id.in_([t.id for t in task_rows]),
                            OpsTaskUpdate.kind == "feedback").order_by(OpsTaskUpdate.task_id, OpsTaskUpdate.id.desc()).distinct(OpsTaskUpdate.task_id)):
        latest[update.task_id] = update.content
    roles = {id: role(db, p, project) for id, project in by_id.items()}
    rows = [{**task_dict(db, p, by_id[t.project_id], t, viewer_role=roles[t.project_id]), "latest_feedback": latest.get(t.id, "")} for t in task_rows]
    return sorted(rows, key=lambda t: (t["status"] == "done", not t["overdue"], t["status"] != "blocked", t["priority"] != "key", t["due_date"], t["id"]))


def task_stats(rows):
    awaiting = sum(t["approval_status"] in {"department_pending", "company_pending"} for t in rows)
    rejected = sum(t["approval_status"] == "rejected" for t in rows)
    rows = [t for t in rows if t["approval_status"] == "approved"]
    pending = [t for t in rows if t["status"] != "done"]
    return {"total": len(rows), "pending": len(pending), "done": len(rows) - len(pending),
            "overdue": sum(t["overdue"] for t in pending), "blocked": sum(t["status"] == "blocked" for t in pending),
            "needs_update": sum(t["needs_update"] for t in pending), "awaiting_approval": awaiting, "rejected": rejected}


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
    return {"items": items, "can_create": can_create(p), "stats": task_stats(active_tasks),
            "todos": [t for t in active_tasks if t["status"] != "done" and t["approval_status"] == "approved"], "today": today()}


def project_detail(db, p, project_id):
    project = get_project(db, p, project_id)
    progress = db.scalar(select(OpsProjectProgress).where(OpsProjectProgress.project_id == project.id).order_by(OpsProjectProgress.id.desc()).limit(1))
    rows = tasks(db, p, project_id)
    schools = school_data(db, project)
    products = db.scalars(select(OpsProduct).where(OpsProduct.project_id == project.id).order_by(OpsProduct.id)).all()
    daily = defaultdict(lambda: ZERO)
    for s in schools:
        for i in s["incomes"]:
            daily[i["income_date"]] += Decimal(i["amount"])
    unassigned = [{"id": r.id, **snapshot(r, ["income_date", "amount", "source", "note"])} for r in db.scalars(
        select(OpsProjectIncome).where(OpsProjectIncome.project_id == project.id).order_by(OpsProjectIncome.income_date.desc()))]
    unassigned_total = sum((Decimal(r["amount"]) for r in unassigned), ZERO)
    for r in unassigned:
        daily[r["income_date"]] += Decimal(r["amount"])
    batches = [{"id": b.id, "source_name": b.source_name, "created_at": b.created_at, "result": {k: v for k, v in b.result.items() if k != "created_ids"}} for b in db.scalars(
        select(OpsImportBatch).where(OpsImportBatch.project_id == project.id).order_by(OpsImportBatch.id.desc()))]
    return {**project_dict(db, p, project), "latest_progress": progress_dict(progress) if progress else None,
            "can_propose": role(db, p, project) in {"manager", "member", "reviewer"}, "members": members(db, project), "tasks": rows, "stats": task_stats(rows),
            "schools": schools, "products": [product_dict(r) for r in products], "today": today(),
            "unassigned_incomes": unassigned, "import_batches": batches,
            "financials": {"total_investment": sum((s["total_investment"] for s in schools), ZERO),
                           "total_income": sum((s["total_income"] for s in schools), ZERO) + unassigned_total,
                           "unassigned_income": unassigned_total,
                           "unpriced_count": sum(s["unpriced_count"] for s in schools),
                           "review_count": sum(s["review_count"] for s in schools),
                           "daily": [{"date": k, "amount": v} for k, v in sorted(daily.items())],
                           "note": "已登记投入按清单金额（有填写时）或数量 × 单价汇总，加人工及其他投入；未标价不计入已知金额。利旧、拆回、调拨及折扣请结合备注核对。收入按实际收款日期登记，不等同于财务利润。"}}


def brief(db, p, project_id):
    data = project_detail(db, p, project_id)
    pending = [t for t in data["tasks"] if t["status"] != "done" and t["approval_status"] == "approved"]
    lines = [f"{data['name']}｜{today()} 进展汇总", f"目标：{data['objective'] or '尚未填写'}"]
    progress = data["latest_progress"]
    if progress:
        lines += [f"项目进度：{progress['progress']}", f"遇到的问题：{progress['problems'] or '未填写'}",
                  f"下一步计划：{progress['next_plan']}（计划完成：{progress['next_due_date']}）" if progress['next_plan'] else "下一步计划：未填写"]
    else:
        lines.append("项目进度尚未填写。")
    lines.append(f"任务 {data['stats']['total']} 项，已完成 {data['stats']['done']} 项，逾期 {data['stats']['overdue']} 项，受阻 {data['stats']['blocked']} 项。")
    lines.append(f"另有 {data['stats']['awaiting_approval']} 项等待审核，{data['stats']['rejected']} 项已退回，尚未进入执行。")
    for t in pending:
        flags = "、".join(x for x, yes in [("逾期", t["overdue"]), ("今日待反馈", t["needs_update"])] if yes)
        lines.append(f"- {t['title']}：{t['owner_name']}，{STATUS_LABELS[t['status']]} {t['progress']}%，截止 {t['due_date']}" + (f"（{flags}）" if flags else "") + (f"；最新反馈：{t['latest_feedback']}" if t['latest_feedback'] else "；尚无进展反馈") + (f"；下一步：{t['next_step']}" if t['next_step'] else ""))
    for t in data["tasks"]:
        if t["status"] == "done":
            lines.append(f"- 已完成：{t['title']}（{t['owner_name']}）；结果：{t['latest_feedback'] or '尚无文字反馈'}")
    return {"text": "\n".join(lines), "project": data, "source": "已登记的任务、节点和反馈", "generated_at": now()}

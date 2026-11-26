"""Project task planning, feedback and the key project workspace on existing milestone records."""
import hashlib
import json
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import false, func, or_, select, true

from app.core.audit import log_op, snapshot
from app.core.deps import build_principal
from app.core.response import bad_request, conflict, encode, forbidden, not_found
from app.core.timeutil import TZ, day_bounds, month_range, now, today
from app.models import (BizContract, BizPayment, BizProject, BizProjectFeedback, BizProjectFollowSetting, BizProjectMilestone,
                        FinSalesInvoice, KeyProjectMember, KeyProjectTaskNode as Node, KeyProjectTaskPlan as Plan,
                        KeyProjectTaskUpdate as Update, SysNotify, SysUser)
from app.services import codes, market_service as market, notify_service, project_service as ps, task_feedback_deadlines

PROJECT_FIELDS = ["name", "remark", "customer_name", "business_line", "owner_id", "start_date", "plan_end_date", "status", "is_key"]
NOTICE = "key_project_feedback_due"
ASSIGNMENT_NOTICE = "key_project_assignment"
GENERAL_ASSIGNMENT_NOTICE = "general_project_assignment"
TASK_ASSIGNMENT_NOTICE = "key_task_assignment"
TEAM_NOTICE = "key_project_team"
TEAM_REF = "key_project_team"
REF = "key_project_task"
GENERAL_REF = "project_task"
GENERAL_NOTICE = "project_task_due"
GENERAL_TASK_ASSIGNMENT_NOTICE = "project_task_assignment"
PAUSE_USERIDS = {"ken", "SZGLY"}
ZERO = Decimal(0)


def can_create(p, *, is_key=True):
    return p.is_admin or p.can("project:key") or (not is_key and p.can("milestone:write"))


def can_delete(p):
    return p.has_delete_grant or can_create(p)


def has_project_menu(p):
    return p.has_menu("market") or p.has_menu("projects")


def standard_scope(p, *, overview=False, is_key=True):
    if p is None:
        return true()
    menu = p.has_menu("projects") if not is_key else p.has_menu("market") if overview else has_project_menu(p)
    if not menu:
        return false()
    scope = ps.visible_filter(p)
    return scope if scope is not None else true()


def membership_scope(p, *, is_key=True):
    if p is None or not p.workbench_enabled:
        return false()
    membership = ps.visible_filter(p)
    return membership if is_key is None else membership & BizProject.is_key.is_(is_key)


def visible_projects(p, *, overview=False, is_key=True):
    return select(BizProject).where(BizProject.is_deleted.is_(False),
        or_(standard_scope(p, overview=overview, is_key=is_key), membership_scope(p, is_key=is_key)))


def has_team(db, p, *, is_key=True):
    return db.scalar(select(BizProject.id).where(BizProject.is_deleted.is_(False), membership_scope(p, is_key=is_key)).limit(1)) is not None


def is_member(db, p, row):
    return ps.can_view(db, p, row)


def standard_access(db, p, row):
    menu = has_project_menu(p) if row.is_key else p.has_menu("projects")
    return menu and ps.can_view(db, p, row)


def can_manage(db, p, row, *, standard=None):
    return (ps.can_edit(p, row) or (row.is_key and can_create(p))) and (standard_access(db, p, row) if standard is None else standard)


def can_remove_project(p, row):
    return can_delete(p) if row.is_key else (p.has_delete_grant or ps.can_edit(p, row))


def require_team_manager(db, p, row):
    if not can_manage(db, p, row):
        raise forbidden("仅项目负责人或项目管理者可维护项目团队")


def can_feedback_task(db, p, row, item, *, manageable=None):
    if manageable is None:
        manageable = can_manage(db, p, row)
    return manageable or (item.owner_id == p.id and (p.can("feedback:write") or is_member(db, p, row)))


def require_overview(db, p, *, is_key=True):
    if not p.has_menu("market" if is_key else "projects") and not has_team(db, p, is_key=is_key):
        raise forbidden()


def can_pause(p):
    return p.user.wecom_userid in PAUSE_USERIDS and any(p.has_menu(m) for m in ("market", "projects"))


def user(db, user_id, *, is_key=True):
    u = db.get(SysUser, user_id)
    if not u or u.status != 1 or u.is_deleted:
        raise bad_request("请选择在职的项目负责人")
    owner = build_principal(u)
    if not (has_project_menu(owner) if is_key else owner.has_menu("projects")):
        raise bad_request("负责人须有项目访问权限")
    return u


def project(db, p, code, *, lock=False, is_key=True):
    row = ps.get_project(db, code, lock=lock)
    if is_key is not None and row.is_key != is_key:
        raise not_found("重点项目" if is_key else "一般项目")
    if not standard_access(db, p, row) and not is_member(db, p, row):
        raise forbidden("无权查看该重点项目" if row.is_key else "无权查看该一般项目")
    return row


def is_paused(db, row):
    setting = db.get(BizProjectFollowSetting, row.id)
    return bool(setting and setting.paused)


def active(db, row):
    if row.status == 5 or is_paused(db, row):
        raise conflict("项目已结项或暂停跟进，请恢复后再安排任务或填写进展")


def digest(value):
    return hashlib.sha256(json.dumps(encode(value), ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def project_revision(row):
    return digest(snapshot(row, PROJECT_FIELDS))


def available_people(db):
    return db.scalars(select(SysUser).where(SysUser.status == 1, SysUser.is_deleted.is_(False),
        SysUser.workbench_enabled.is_(True)).order_by(SysUser.name, SysUser.id))


def person_info(person):
    return {"id": person.id, "name": person.name, "dept": person.dept_code,
            "department": person.department.name if person.department else None}


def can_assign_to(db, row, assignee):
    return is_member(db, assignee, row)


def general_task_options(db, p, row):
    if not can_manage(db, p, row):
        raise forbidden("仅项目负责人或项目管理者可安排任务")
    return {"users": [person_info(u) for u in available_people(db) if can_assign_to(db, row, build_principal(u))]}


def options(db, p, code=None, include_team=False, *, is_key=True):
    row = project(db, p, code, is_key=is_key) if code else None
    # Keep the employee directory scoped to people managing this project or opening the create form.
    if row and not can_manage(db, p, row):
        raise forbidden()
    create = p.has_menu("market" if is_key else "projects") and can_create(p, is_key=is_key)
    if not row and ((include_team or not is_key) and not create):
        raise forbidden()
    people = list(available_people(db))
    team_ids = set(db.scalars(select(KeyProjectMember.user_id).where(KeyProjectMember.project_id == row.id))) if row else set()
    if row:
        users = [person_info(u) for u in people if u.id in team_ids or u.id == row.owner_id]
    elif is_key:
        users = [person_info(u) for u in people if has_project_menu(build_principal(u))]
    else:
        users = [person_info(u) for u in people if build_principal(u).has_menu("projects") and within_owner_scope(p, u)]
    result = {"users": users, "can_create": can_create(p, is_key=is_key), "today": today()}
    if include_team:
        result["team_users"] = [person_info(u) for u in people]
    return result


def team(db, row):
    joined = select(KeyProjectMember.user_id).where(KeyProjectMember.project_id == row.id)
    people = db.scalars(select(SysUser).where(or_(SysUser.id.in_(joined), SysUser.id == row.owner_id))
        .order_by((SysUser.id == row.owner_id).desc(), SysUser.name, SysUser.id))
    return [{**person_info(u), "is_owner": u.id == row.owner_id,
             "active": u.status == 1 and not u.is_deleted and u.workbench_enabled} for u in people]


def project_label(row):
    return "重点项目" if row.is_key else "一般项目"


def project_link(row):
    return f"{'/market/projects' if row.is_key else '/general-projects'}/{row.project_code}"


def queue_team_notice(db, row, title, *, exclude=(), item=None, recipients=None):
    if row.is_deleted:
        return []
    if recipients is None:
        recipients = db.scalars(select(SysUser).join(KeyProjectMember, KeyProjectMember.user_id == SysUser.id)
            .where(KeyProjectMember.project_id == row.id, SysUser.id.not_in(exclude)).order_by(SysUser.id))
    link = task_link(row, item.id) if item else project_link(row)
    label = project_label(row)
    ref = REF if row.is_key else GENERAL_REF
    subject = f"{row.name} · {item.node_name}" if item else row.name
    notices = notify_service.send(db, recipients, TEAM_NOTICE, title, f"{subject}。请打开{label}查看详细信息。",
        link=link, ref_type=ref if item else TEAM_REF, ref_id=str(item.id if item else row.id), defer=True,
        wecom_content=f"你参与的{label}有新消息，请打开工作台查看。")
    return [notice.id for notice in notices if notice.channel == "wecom_app"]


def add_team(db, p, row, user_ids, *, creating=False):
    if not creating:
        require_team_manager(db, p, row)
    joined = set(db.scalars(select(KeyProjectMember.user_id).where(KeyProjectMember.project_id == row.id)))
    additions = set(user_ids) - joined - {row.owner_id}
    if not additions:
        return 0, []
    people = list(db.scalars(select(SysUser).where(SysUser.id.in_(additions), SysUser.status == 1,
        SysUser.is_deleted.is_(False), SysUser.workbench_enabled.is_(True)).order_by(SysUser.id)))
    if len(people) != len(additions):
        raise bad_request("请选择在职且已获工作台使用权限的员工")
    for person in people:
        db.add(KeyProjectMember(project_id=row.id, user_id=person.id))
    db.flush()
    log_op(db, p.id, "market.key_project.team.add" if row.is_key else "project.team.add", "biz_project", row.project_code,
           after={"user_ids": sorted(additions)})
    return len(additions), queue_team_notice(db, row, f"你已加入{project_label(row)}团队",
        recipients=[person for person in people if person.id != p.id])


def remove_team_member(db, p, row, user_id):
    require_team_manager(db, p, row)
    if row.owner_id == user_id:
        raise bad_request("请先变更项目负责人")
    member = db.get(KeyProjectMember, (row.id, user_id))
    if member is None:
        return
    if db.scalar(
        select(BizProjectMilestone.id).where(BizProjectMilestone.project_id == row.id, BizProjectMilestone.owner_id == user_id,
            BizProjectMilestone.status != 3).limit(1)) is not None:
        raise conflict("请先转交该成员负责的未完成任务，再移除")
    db.delete(member)
    log_op(db, p.id, "market.key_project.team.remove" if row.is_key else "project.team.remove", "biz_project", row.project_code,
           before={"user_id": user_id})


def eligible_team_notice(db, notice):
    if notice.ref_type not in {REF, GENERAL_REF, TEAM_REF} or not (notice.ref_id or "").isdigit():
        return False
    ident = int(notice.ref_id)
    if notice.ref_type in {REF, GENERAL_REF}:
        item = db.get(BizProjectMilestone, ident)
        ident = item.project_id if item else None
    row = db.get(BizProject, ident) if ident else None
    if not row or row.is_deleted or notice.link != (task_link(row, item.id) if notice.ref_type in {REF, GENERAL_REF} else project_link(row)):
        return False
    return row.owner_id == notice.user_id or db.get(KeyProjectMember, (row.id, notice.user_id)) is not None


def within_owner_scope(p, owner):
    return p.data_scope == "all" or owner.id == p.id or (p.data_scope == "dept" and owner.department_id == p.user.department_id)


def save_project(db, p, body, code=None, *, is_key=True):
    row = project(db, p, code, lock=True, is_key=is_key) if code else None
    if not (can_manage(db, p, row) if row else can_create(p, is_key=is_key)):
        raise forbidden("无管理项目权限")
    if row and body.expected_revision != project_revision(row):
        raise conflict("项目资料已更新，请重新打开后保存")
    owner = user(db, body.owner_id, is_key=is_key)
    if not within_owner_scope(p, owner):
        raise forbidden("只能在本人项目数据范围内设置项目负责人")
    if row and any(m.plan_date and not body.preparation_on <= m.plan_date <= body.due_on for m in row.milestones):
        raise bad_request("项目日期需覆盖已有任务的完成节点，请先核对任务日期")
    if row and db.scalar(select(Plan.milestone_id).join(BizProjectMilestone, Plan.milestone_id == BizProjectMilestone.id).where(
            BizProjectMilestone.project_id == row.id, Plan.preparation_on < body.preparation_on).limit(1)):
        raise bad_request("项目准备日期不能晚于已有任务的准备日期")
    before = snapshot(row, PROJECT_FIELDS) if row else None
    if not row:
        row = BizProject(project_code=codes.project_code(db, body.business_line), is_key=is_key, milestone_template="none")
        db.add(row)
    row.name, row.remark, row.customer_name = body.name, body.content, body.customer_name
    row.business_line, row.owner_id, row.status = body.business_line, owner.id, body.status
    row.start_date, row.plan_end_date = body.preparation_on, body.due_on
    db.flush()
    db.expire(row, ["owner"])
    prefix = "market.key_project" if is_key else "project"
    log_op(db, p.id, f"{prefix}.{'update' if code else 'create'}", "biz_project", row.project_code,
           before=before, after=snapshot(row, PROJECT_FIELDS))
    return row


def delete_project(db, p, code, *, is_key=True):
    row = project(db, p, code, lock=True, is_key=is_key)
    if not can_remove_project(p, row) or not standard_access(db, p, row):
        raise forbidden(f"无删除{project_label(row)}权限")
    fields = [*PROJECT_FIELDS, "is_deleted"]
    before = snapshot(row, fields)
    row.is_deleted = True
    log_op(db, p.id, "market.key_project.delete" if is_key else "project.delete", "biz_project", row.project_code,
           before=before, after=snapshot(row, fields))
    return {"deleted": True, "project_code": row.project_code, "name": row.name}


def set_paused(db, p, code, paused, *, is_key=True):
    if not can_pause(p):
        raise forbidden("只有梁道灿和聚成数智管理员可以暂停或恢复项目跟进")
    row = project(db, p, code, lock=True, is_key=is_key)
    if not standard_access(db, p, row):
        raise forbidden("无权暂停或恢复该项目")
    setting = db.get(BizProjectFollowSetting, row.id)
    before = bool(setting and setting.paused)
    if before != paused:
        setting = setting or BizProjectFollowSetting(project_id=row.id)
        setting.paused = paused
        db.add(setting)
        log_op(db, p.id, "market.follow_pause" if is_key else "project.follow_pause", "biz_project", row.project_code,
               before={"paused": before}, after={"paused": paused})
    return {"paused": paused}


def assignment_recipient(db, row, user_id):
    if not row or row.is_deleted or row.status == 5 or row.owner_id != user_id:
        return None
    paused = db.get(BizProjectFollowSetting, row.id)
    owner = db.get(SysUser, user_id)
    if (paused and paused.paused) or not owner or owner.is_deleted or owner.status != 1:
        return None
    p = build_principal(owner)
    return owner if standard_access(db, p, row) else None


def queue_assignment(db, row):
    owner = assignment_recipient(db, row, row.owner_id) if row.owner_id else None
    if not owner:
        return []
    label = project_label(row)
    notices = notify_service.send(db, [owner], ASSIGNMENT_NOTICE if row.is_key else GENERAL_ASSIGNMENT_NOTICE, f"你有新的{label}",
        f"你是「{row.name}」的项目负责人。", link=project_link(row),
        ref_type="biz_project", ref_id=str(row.id), defer=True,
        wecom_content=f"你已被指定为一个新建{label}的负责人，请打开工作台查看项目安排。")
    return [notice.id for notice in notices if notice.channel == "wecom_app"]


def eligible_assignment(db, notice):
    if notice.ref_type != "biz_project" or not (notice.ref_id or "").isdigit():
        return False
    row = db.get(BizProject, int(notice.ref_id))
    return bool(row and row.is_key == (notice.msg_type != GENERAL_ASSIGNMENT_NOTICE) and assignment_recipient(db, row, notice.user_id))


def eligible_workflow_notice(db, notice):
    if not (notice.ref_id or "").isdigit():
        return False
    if notice.ref_type == "milestone":
        item = db.get(BizProjectMilestone, int(notice.ref_id))
        if not item or item.status == 3:
            return False
        project_id = item.project_id
    elif notice.ref_type == "project_weekly":
        project_id = int(notice.ref_id)
    else:
        return False
    user = db.get(SysUser, notice.user_id)
    row = db.get(BizProject, project_id)
    if not user or not row or row.status == 5 or not is_member(db, build_principal(user), row):
        return False
    return notice.ref_type == "project_weekly" or (not is_paused(db, row) and notice.user_id in {item.owner_id, row.owner_id})


def task(db, row, ident):
    item = db.scalar(select(BizProjectMilestone).where(BizProjectMilestone.id == ident, BizProjectMilestone.project_id == row.id)
                     .execution_options(populate_existing=True))
    if not item:
        raise not_found("本项目任务")
    return item


def task_link(row, task_id):
    return f"{project_link(row)}?task={task_id}"


def task_data(db, p, row, item, *, history=False, manageable=None):
    plan = db.get(Plan, item.id)
    nodes = list(db.scalars(select(Node).where(Node.milestone_id == item.id).order_by(Node.seq, Node.id)))
    updates = list(db.execute(select(Update, BizProjectFeedback).join(BizProjectFeedback, Update.feedback_id == BizProjectFeedback.id)
                    .where(Update.milestone_id == item.id).order_by(Update.id.desc()).limit(50 if history else 1)))
    last = updates[0][1] if updates else None
    preparation = plan.preparation_on if plan else row.start_date
    cadence = plan.cadence if plan else "node"
    due = None
    if plan and cadence != "node":
        base = last.feedback_date if last else plan.created_at.astimezone(TZ).date()
        due = max(preparation or base, base) + timedelta(days=1 if cadence == "daily" else 7)
    schedule = task_feedback_deadlines.view(plan, updates[0][0].created_at if updates else None, due,
        active=item.status != 3 and row.status != 5 and not is_paused(db, row))
    if manageable is None:
        manageable = can_manage(db, p, row)
    result = {**ps.ms_dict(item), "title": item.node_name, "requirements": plan.requirements if plan else (item.duty or ""),
              "preparation_on": preparation, "cadence": cadence, "problems": plan.problems if plan else "",
              "next_step": plan.next_step if plan else "", **schedule,
              "nodes": [{"id": n.id, "name": n.name, "due_on": n.due_on, "completed_on": n.completed_on,
                          "overdue": not n.completed_on and n.due_on < today()} for n in nodes],
              "latest_feedback": ps.feedback_dict(last) if last else None, "has_plan": plan is not None,
              "can_manage": manageable, "can_feedback": can_feedback_task(db, p, row, item, manageable=manageable)}
    result["alert"] = result["alert"] or any(n["overdue"] for n in result["nodes"]) or bool(result["problems"]) or result["needs_feedback"]
    result["revision"] = digest({"task": ps._snap(item), "plan": snapshot(plan, ["requirements", "preparation_on", "cadence", "feedback_due_at", "problems", "next_step", "revision"]) if plan else None,
                                 "nodes": result["nodes"], "latest_feedback_id": last.id if last else None})
    if history:
        result["history"] = [{"id": u.id, "feedback": ps.feedback_dict(f), "values": u.values} for u, f in updates]
        result["notification_deliveries"] = notify_service.delivery_status(db,
            SysNotify.ref_type.in_((REF, GENERAL_REF)) & (func.split_part(SysNotify.ref_id, ":", 1) == str(item.id)),
            user_id=None if result["can_manage"] else p.id)
    return result


def save_task(db, p, code, body, task_id=None, *, notice_ids=None, is_key=True):
    row = project(db, p, code, lock=True, is_key=is_key)
    item = _save_task(db, p, row, body, task_id, notice_ids=notice_ids)
    return task_data(db, p, row, item, history=True)


def _save_task(db, p, row, body, task_id=None, *, notice_ids=None, announce_team=True, creating=False):
    active(db, row)
    if not creating and not can_manage(db, p, row):
        raise forbidden("仅项目负责人或项目管理者可安排任务")
    person = db.get(SysUser, body.owner_id)
    if not person or person.status != 1 or person.is_deleted or not person.workbench_enabled:
        raise bad_request("任务负责人须为在职且已获工作台使用权限的员工")
    assignee = build_principal(person)
    if not can_assign_to(db, row, assignee):
        raise bad_request("任务负责人需有进展反馈权限，请选择可执行人员")
    if row.start_date and body.preparation_on < row.start_date or row.plan_end_date and body.due_on > row.plan_end_date:
        raise bad_request("任务准备和完成日期须在项目计划日期内")
    item = task(db, row, task_id) if task_id is not None else None
    if item and body.expected_revision != task_data(db, p, row, item)["revision"]:
        raise conflict("任务已变化，请重新打开后保存")
    if item and item.status == 3:
        raise conflict("任务已完成，保留原计划和完成记录")
    previous_owner = item.owner_id if item else None
    if item:
        ps.edit_milestone(db, row, item, {"node_name": body.title, "owner_id": body.owner_id, "plan_date": body.due_on}, p.id, from_task=True)
    else:
        item = ps.add_milestone(db, row, {"node_name": body.title, "owner_id": body.owner_id, "plan_date": body.due_on}, p.id)
    if previous_owner != item.owner_id:
        db.expire(item, ["owner"])
    plan = db.get(Plan, item.id) or Plan(milestone_id=item.id, revision=0, created_at=now())
    plan.requirements, plan.preparation_on, plan.cadence = body.requirements, body.preparation_on, body.cadence
    task_feedback_deadlines.set_deadline(plan,
        body.feedback_due_at if "feedback_due_at" in body.model_fields_set else plan.feedback_due_at,
        reassigned=previous_owner != item.owner_id)
    plan.revision += 1
    db.add(plan)
    existing = {n.id: n for n in db.scalars(select(Node).where(Node.milestone_id == item.id))}
    incoming = {n.id for n in body.nodes if n.id}
    if not incoming <= existing.keys():
        raise bad_request("阶段节点不属于本任务")
    for old_id, old in existing.items():
        given = next((n for n in body.nodes if n.id == old_id), None)
        if old.completed_on and (not given or (old.name, old.due_on) != (given.name, given.due_on)):
            raise conflict("已完成节点需保留原名称和计划日期")
        if old_id not in incoming:
            db.delete(old)
    for seq, n in enumerate(body.nodes, 1):
        node = existing.get(n.id) or Node(milestone_id=item.id)
        node.name, node.due_on, node.seq = n.name, n.due_on, seq
        db.add(node)
    db.flush()
    log_op(db, p.id, "market.key_task.plan" if row.is_key else "project.task.plan", "biz_project_milestone", item.id,
           after=body.model_dump(mode="json", exclude={"expected_revision"}))
    if previous_owner != item.owner_id:
        label = "重点项目" if row.is_key else "一般项目"
        notices = notify_service.send(db, [assignee.user], TASK_ASSIGNMENT_NOTICE if row.is_key else GENERAL_TASK_ASSIGNMENT_NOTICE,
            f"收到{label}任务" if not previous_owner else f"{label}任务已转交给你",
            f"{row.name} · {item.node_name}，任务完成节点 {item.plan_date}。",
            link=task_link(row, item.id), ref_type=REF if row.is_key else GENERAL_REF, ref_id=str(item.id), defer=True,
            wecom_content=f"你有一项新的{label}任务安排，请打开工作台查看任务要求和反馈时间。")
        if notice_ids is not None:
            notice_ids.extend(notice.id for notice in notices if notice.channel == "wecom_app")
    if announce_team and task_id is None:
        team_notices = queue_team_notice(db, row, f"{project_label(row)}新增任务", exclude={p.id, item.owner_id}, item=item)
        if notice_ids is not None:
            notice_ids.extend(team_notices)
    return item


def create_project(db, p, body, *, is_key=True):
    row = save_project(db, p, body, is_key=is_key)
    notices = queue_assignment(db, row)
    team_ids = {*body.team_member_ids, p.id} - {row.owner_id}
    if team_ids:
        _, team_notices = add_team(db, p, row, team_ids, creating=True)
        notices.extend(team_notices)
    for fields in body.tasks:
        _save_task(db, p, row, fields, notice_ids=notices, announce_team=False, creating=True)
    return row, notices


def feedback(db, p, code, task_id, body, *, notice_ids=None, is_key=True):
    row = project(db, p, code, lock=True, is_key=is_key)
    active(db, row)
    item = task(db, row, task_id)
    current = task_data(db, p, row, item)
    if not current["can_feedback"]:
        raise forbidden("仅任务负责人或项目管理者可反馈本任务")
    if current["revision"] != body.expected_revision:
        raise conflict("任务已有新进展，请刷新后再填写")
    if item.status == 3:
        raise conflict("任务已完成，请查看历史反馈")
    if body.completed_on and (body.status != 3 or body.completed_on > today() or (current["preparation_on"] and body.completed_on < current["preparation_on"])):
        raise bad_request("实际完成日期须在准备日期与今天之间，且任务状态为已完成")
    nodes = {n.id: n for n in db.scalars(select(Node).where(Node.milestone_id == item.id))}
    selected = set(body.completed_node_ids)
    if not selected <= nodes.keys():
        raise bad_request("完成节点须来自当前任务")
    completed_on = body.completed_on or today()
    for ident in selected:
        if not nodes[ident].completed_on:
            nodes[ident].completed_on = completed_on
    if body.status == 3 and any(not n.completed_on for n in nodes.values()):
        raise bad_request("请先确认所有阶段节点完成，再提交任务完成")
    if body.status == 3 and any(n.completed_on and n.completed_on > completed_on for n in nodes.values()):
        raise bad_request("任务完成日期不能早于已完成的阶段节点日期")
    ps.edit_milestone(db, row, item, {"status": body.status, "finish_date": completed_on if body.status == 3 else None,
                                     "progress_note": body.content}, p.id, from_task=True)
    plan = db.get(Plan, item.id) or Plan(milestone_id=item.id, preparation_on=row.start_date, cadence="node", revision=0, created_at=now())
    plan.problems, plan.next_step, plan.revision = body.problems, body.next_step, plan.revision + 1
    db.add(plan)
    text_body = f"任务「{item.node_name}」：{body.content}" + (f"\n完成阶段：{'、'.join(nodes[i].name for i in sorted(selected))}" if selected else "") + (f"\n存在问题：{body.problems}" if body.problems else "") + (f"\n下一步：{body.next_step}" if body.next_step else "")
    fb = market.add_feedback(db, row, {"content": text_body, "kind": "risk" if body.problems else "progress"}, p.id,
                             item=item, notice_ids=notice_ids)
    db.flush()
    db.add(Update(milestone_id=item.id, feedback_id=fb.id, values=body.model_dump(mode="json", exclude={"expected_revision"}), created_at=now()))
    db.flush()
    log_op(db, p.id, "market.key_task.feedback" if row.is_key else "project.task.feedback", "biz_project_milestone", item.id,
           after={"feedback_id": fb.id, "status": body.status})
    return task_data(db, p, row, item, history=True)


def financials(db, row, period=""):
    period = period or f"{today():%Y-%m}"
    try:
        start, end = month_range(period)
        if len(period) != 7 or not 2000 <= start.year <= 2100:
            raise ValueError()
    except (ValueError, TypeError):
        raise bad_request("请选择有效的开票回款月份") from None
    contracts = list(db.scalars(select(BizContract).where(BizContract.project_code == row.project_code,
        BizContract.is_deleted.is_(False), BizContract.status != 3)))
    ids = [c.id for c in contracts]
    contract_map = {c.id: c.contract_no for c in contracts}
    invoices = list(db.scalars(select(FinSalesInvoice).where(FinSalesInvoice.contract_id.in_(ids),
        FinSalesInvoice.state == "issued", FinSalesInvoice.issued_on.between(start, min(end, today()))).order_by(FinSalesInvoice.issued_on.desc(), FinSalesInvoice.id.desc())))
    payments = list(db.scalars(select(BizPayment).where(BizPayment.contract_id.in_(ids),
        BizPayment.payment_date.between(start, min(end, today()))).order_by(BizPayment.payment_date.desc(), BizPayment.id.desc())))
    return {"period": period, "invoiced": sum((i.issued_amount - i.reduced_amount for i in invoices), ZERO),
            "received": sum((p.amount for p in payments), ZERO),
            "invoices": [{"id": i.id, "invoice_no": i.invoice_no, "customer": i.customer, "contract_no": contract_map[i.contract_id],
                          "issued_on": i.issued_on, "amount": i.issued_amount - i.reduced_amount, "note": i.note} for i in invoices],
            "payments": [{"id": p.id, "contract_no": contract_map[p.contract_id], "payment_date": p.payment_date, "amount": p.amount} for p in payments],
            "note": "按本项目关联合同读取已确认开票和实际到账；金额以财务台账为准。"}


def task_workspace(db, p, row, *, standard=None):
    if standard is None:
        standard = standard_access(db, p, row)
    manageable = can_manage(db, p, row, standard=standard)
    tasks = [task_data(db, p, row, m, manageable=manageable) for m in row.milestones]
    return {"project_code": row.project_code, "name": row.name, "owner_id": row.owner_id, "is_key": row.is_key,
            "status": row.status, "start_date": row.start_date, "plan_end_date": row.plan_end_date,
            "today": today(), "paused": is_paused(db, row), "can_manage": manageable,
            "can_delete_tasks": manageable or (standard and p.has_delete_grant),
            "tasks": tasks, "done_nodes": sum(item["status"] == 3 for item in tasks)}


def detail(db, p, code, period="", *, is_key=True):
    row = project(db, p, code, is_key=is_key)
    standard = standard_access(db, p, row)
    fbs = db.scalars(select(BizProjectFeedback).where(BizProjectFeedback.project_id == row.id).order_by(BizProjectFeedback.feedback_date.desc(), BizProjectFeedback.id.desc()).limit(50))
    return {**brief(db, row, standard), **task_workspace(db, p, row, standard=standard),
            "content": row.remark or "", "revision": project_revision(row), "can_delete": standard and can_remove_project(p, row),
            "can_pause": standard and can_pause(p), "team": team(db, row),
            "feedbacks": [ps.feedback_dict(f) for f in fbs], "money": ps.contract_detail(db, row.project_code) if standard else None,
            "financials": financials(db, row, period) if standard else None}


def brief(db, row, standard, *, day=None, money=None):
    result = ps.project_brief(db, row, day, money if standard else None)
    if not standard:
        result.pop("budget_amount", None)
        result.pop("contract_amount", None)
    return result


def reminder_reasons(data):
    day = today()
    if data["status"] == 3:
        return []
    reasons = []
    if data["plan_date"] and data["plan_date"] <= day + timedelta(days=3):
        reasons.append(f"任务完成节点 {data['plan_date']}")
    for node in data["nodes"]:
        if not node["completed_on"] and node["due_on"] <= day + timedelta(days=3):
            reasons.append(f"阶段「{node['name']}」应于 {node['due_on']} 完成")
    if data["needs_feedback"] and not data["feedback_deadline_pending"]:
        reasons.append(f"应于 {data['feedback_due_on']} 填写进展")
    return reasons


def _notice_task(db, notice):
    try:
        mid = int(str(notice.ref_id).split(":")[0])
    except (TypeError, ValueError):
        return None
    item, u = db.get(BizProjectMilestone, mid), db.get(SysUser, notice.user_id)
    if not item or not u or u.is_deleted or u.status != 1:
        return None
    row = db.get(BizProject, item.project_id)
    if not row or row.is_deleted or row.status == 5 or item.status == 3:
        return None
    if row.is_key != (notice.ref_type != GENERAL_REF):
        return None
    p = build_principal(u)
    if is_paused(db, row) or not (standard_access(db, p, row) or is_member(db, p, row)):
        return None
    return row, item, p


def eligible_notice(db, notice):
    target = _notice_task(db, notice)
    if not target:
        return False
    row, item, p = target
    data = task_data(db, p, row, item)
    return notice.user_id in {item.owner_id, row.owner_id} and bool(reminder_reasons(data))


def eligible_task_assignment(db, notice):
    target = _notice_task(db, notice)
    if not target:
        return False
    row, item, p = target
    return item.owner_id == p.id and can_feedback_task(db, p, row, item)


def eligible_feedback_deadline(db, notice):
    target = _notice_task(db, notice)
    if not target:
        return False
    row, item, p = target
    if item.owner_id != p.id or not can_feedback_task(db, p, row, item):
        return False
    plan = db.get(Plan, item.id)
    latest = db.scalar(select(func.max(Update.created_at)).where(Update.milestone_id == item.id))
    return task_feedback_deadlines.overdue(plan, latest) and notice.ref_id == task_feedback_deadlines.reference(item.id, plan)


def scan_feedback_deadlines(db, at):
    has_feedback = select(Update.id).where(Update.milestone_id == Plan.milestone_id,
        Update.created_at >= Plan.feedback_requested_at).exists()
    tasks = db.execute(select(Plan, BizProjectMilestone, BizProject).join(BizProjectMilestone, Plan.milestone_id == BizProjectMilestone.id)
        .join(BizProject, BizProject.id == BizProjectMilestone.project_id).outerjoin(BizProjectFollowSetting, BizProjectFollowSetting.project_id == BizProject.id)
        .where(BizProject.is_deleted.is_(False), BizProject.status != 5,
            BizProjectFollowSetting.paused.is_not(True),
            BizProjectMilestone.status != 3, BizProjectMilestone.owner_id.is_not(None),
            Plan.feedback_due_at < at, Plan.feedback_requested_at.is_not(None), ~has_feedback))
    notices = []
    for plan, item, row in tasks:
        owner = db.get(SysUser, item.owner_id)
        if not owner or owner.is_deleted or owner.status != 1:
            continue
        principal = build_principal(owner)
        if not ((standard_access(db, principal, row) or is_member(db, principal, row)) and can_feedback_task(db, principal, row, item)):
            continue
        kind = task_feedback_deadlines.KEY_OVERDUE if row.is_key else task_feedback_deadlines.PROJECT_OVERDUE
        notices.extend(task_feedback_deadlines.queue_overdue(db, owner, kind=kind, ref_type=REF if row.is_key else GENERAL_REF,
            task_id=item.id, row=plan, title="重点项目任务逾期未反馈" if row.is_key else "一般项目任务逾期未反馈",
            task_title=f"{row.name} · {item.node_name}", link=task_link(row, item.id)))
    return notices


def scan_reminders(db):
    start, end = day_bounds(today())
    sent = 0
    for item, row in db.execute(select(BizProjectMilestone, BizProject).join(BizProject, BizProjectMilestone.project_id == BizProject.id)
            .outerjoin(Plan, Plan.milestone_id == BizProjectMilestone.id)
            .where(or_(BizProject.is_key.is_(True), Plan.milestone_id.is_not(None)), BizProject.is_deleted.is_(False),
                BizProject.status != 5, BizProjectMilestone.status != 3)):
        kind = NOTICE if row.is_key else GENERAL_NOTICE
        ref_type = REF if row.is_key else GENERAL_REF
        label = "重点项目" if row.is_key else "一般项目"
        for uid in {u for u in (item.owner_id, row.owner_id) if u}:
            probe = SysNotify(user_id=uid, ref_type=ref_type, ref_id=str(item.id))
            if not eligible_notice(db, probe) or db.scalar(select(SysNotify.id).where(SysNotify.user_id == uid,
                SysNotify.msg_type == kind, SysNotify.ref_id == str(item.id), SysNotify.created_at >= start, SysNotify.created_at < end).limit(1)):
                continue
            u = db.get(SysUser, uid)
            p = build_principal(u)
            reasons = reminder_reasons(task_data(db, p, row, item))
            notices = notify_service.send(db, [u], kind, f"{label}节点待处理", f"{row.name} / {item.node_name}：" + "；".join(reasons),
                link=task_link(row, item.id), ref_type=ref_type, ref_id=str(item.id), defer=True,
                wecom_content=f"{label}有节点待处理，请打开工作台查看并填写进展。")
            for notice in notices:
                notice.created_at = now()
            sent += 1
    return {"rows": sent, "message": f"已为任务负责人及项目负责人生成 {sent} 组节点提醒"}

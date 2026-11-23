"""A simple key project workspace on the original project, milestone and financial records."""
import hashlib
import json
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select, text

from app.core.audit import log_op, snapshot
from app.core.deps import build_principal
from app.core.response import bad_request, conflict, encode, forbidden, not_found
from app.core.timeutil import TZ, day_bounds, month_range, now, today
from app.models import (BizContract, BizPayment, BizProject, BizProjectFeedback, BizProjectFollowSetting, BizProjectMilestone,
                        FinSalesInvoice, KeyProjectTaskNode as Node, KeyProjectTaskPlan as Plan,
                        KeyProjectTaskUpdate as Update, SysNotify, SysUser)
from app.services import codes, market_service as market, notify_service, project_service as ps

PROJECT_FIELDS = ["name", "remark", "customer_name", "business_line", "owner_id", "start_date", "plan_end_date", "status", "is_key"]
NOTICE = "key_project_feedback_due"
REF = "key_project_task"
ZERO = Decimal(0)


def can_create(p):
    return p.is_admin or p.can("project:key")


def can_manage(p, project):
    return can_create(p) or ps.can_edit(p, project)


def user(db, user_id):
    u = db.get(SysUser, user_id)
    if not u or u.status != 1 or u.is_deleted or not any(build_principal(u).has_menu(m) for m in ("market", "projects")):
        raise bad_request("负责人须为在职且有重点项目或项目台账访问权限的人员")
    return u


def project(db, p, code, *, lock=False):
    row = ps.get_project(db, code, p)
    if not row.is_key:
        raise not_found("重点项目")
    if lock:
        row = db.scalar(select(BizProject).where(BizProject.id == row.id).with_for_update(of=BizProject).execution_options(populate_existing=True))
        if not row or row.is_deleted or not row.is_key or not ps.can_view(db, p, row):
            raise forbidden("项目可见范围已变更，请重新打开")
        db.expire(row, ["milestones"])
    return row


def active(db, row):
    paused = db.get(BizProjectFollowSetting, row.id)
    if row.status == 5 or (paused and paused.paused):
        raise conflict("项目已结项或暂停跟进，请恢复后再安排任务或填写进展")


def digest(value):
    return hashlib.sha256(json.dumps(encode(value), ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def project_revision(row):
    return digest(snapshot(row, PROJECT_FIELDS))


def options(db, p):
    users = [u for u in db.scalars(select(SysUser).where(SysUser.status == 1, SysUser.is_deleted.is_(False)).order_by(SysUser.name))
             if any(build_principal(u).has_menu(m) for m in ("market", "projects"))]
    return {"users": [{"id": u.id, "name": u.name, "dept": u.dept_code} for u in users], "can_create": can_create(p), "today": today()}


def save_project(db, p, body, code=None):
    row = project(db, p, code, lock=True) if code else None
    if not (can_manage(p, row) if row else can_create(p)):
        raise forbidden("无管理重点项目权限")
    if row and body.expected_revision != project_revision(row):
        raise conflict("项目资料已更新，请重新打开后保存")
    owner = user(db, body.owner_id)
    if p.data_scope != "all" and owner.id != p.id and not (p.data_scope == "dept" and owner.department_id == p.user.department_id):
        raise forbidden("只能在本人项目数据范围内设置项目负责人")
    if row and any(m.plan_date and not body.preparation_on <= m.plan_date <= body.due_on for m in row.milestones):
        raise bad_request("项目日期需覆盖已有任务的完成节点，请先核对任务日期")
    if row and db.scalar(select(Plan.milestone_id).join(BizProjectMilestone, Plan.milestone_id == BizProjectMilestone.id).where(
            BizProjectMilestone.project_id == row.id, Plan.preparation_on < body.preparation_on).limit(1)):
        raise bad_request("项目准备日期不能晚于已有任务的准备日期")
    before = snapshot(row, PROJECT_FIELDS) if row else None
    if not row:
        # The code allocator is shared with normal projects; serialize allocation for the chosen business line.
        if db.bind.dialect.name == "postgresql":
            db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": f"key-project:{body.business_line}:{today().year}"})
        row = BizProject(project_code=codes.project_code(db, body.business_line), is_key=True, milestone_template="none")
        db.add(row)
    row.name, row.remark, row.customer_name = body.name, body.content, body.customer_name
    row.business_line, row.owner_id, row.status = body.business_line, owner.id, body.status
    row.start_date, row.plan_end_date = body.preparation_on, body.due_on
    db.flush()
    db.expire(row, ["owner"])
    log_op(db, p.id, "market.key_project.update" if code else "market.key_project.create", "biz_project", row.project_code,
           before=before, after=snapshot(row, PROJECT_FIELDS))
    return row


def delete_project(db, p, code):
    if not can_create(p):
        raise forbidden("无删除重点项目权限")
    row = project(db, p, code, lock=True)
    fields = [*PROJECT_FIELDS, "is_deleted"]
    before = snapshot(row, fields)
    row.is_deleted = True
    log_op(db, p.id, "market.key_project.delete", "biz_project", row.project_code,
           before=before, after=snapshot(row, fields))
    return {"deleted": True, "project_code": row.project_code, "name": row.name}


def task(db, row, ident):
    item = db.scalar(select(BizProjectMilestone).where(BizProjectMilestone.id == ident, BizProjectMilestone.project_id == row.id)
                     .execution_options(populate_existing=True))
    if not item:
        raise not_found("本项目任务")
    return item


def task_data(db, p, row, item, *, history=False):
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
    result = {**ps.ms_dict(item), "title": item.node_name, "requirements": plan.requirements if plan else (item.duty or ""),
              "preparation_on": preparation, "cadence": cadence, "problems": plan.problems if plan else "",
              "next_step": plan.next_step if plan else "", "feedback_due_on": due, "needs_feedback": bool(due and today() >= due and item.status != 3),
              "nodes": [{"id": n.id, "name": n.name, "due_on": n.due_on, "completed_on": n.completed_on,
                          "overdue": not n.completed_on and n.due_on < today()} for n in nodes],
              "latest_feedback": ps.feedback_dict(last) if last else None,
              "can_manage": can_manage(p, row), "can_feedback": can_manage(p, row) or (p.can("feedback:write") and item.owner_id == p.id)}
    result["alert"] = result["alert"] or any(n["overdue"] for n in result["nodes"]) or bool(result["problems"]) or result["needs_feedback"]
    result["revision"] = digest({"task": ps._snap(item), "plan": snapshot(plan, ["requirements", "preparation_on", "cadence", "problems", "next_step", "revision"]) if plan else None,
                                 "nodes": result["nodes"], "latest_feedback_id": last.id if last else None})
    if history:
        result["history"] = [{"id": u.id, "feedback": ps.feedback_dict(f), "values": u.values} for u, f in updates]
    return result


def save_task(db, p, code, body, task_id=None):
    row = project(db, p, code, lock=True)
    active(db, row)
    if not can_manage(p, row):
        raise forbidden("仅项目负责人或项目管理者可安排任务")
    assignee = build_principal(user(db, body.owner_id))
    if not (can_manage(assignee, row) or assignee.can("feedback:write")):
        raise bad_request("任务负责人需有进展反馈权限，请选择可执行人员")
    if row.start_date and body.preparation_on < row.start_date or row.plan_end_date and body.due_on > row.plan_end_date:
        raise bad_request("任务准备和完成日期须在项目计划日期内")
    item = task(db, row, task_id) if task_id else None
    if item and body.expected_revision != task_data(db, p, row, item)["revision"]:
        raise conflict("任务已变化，请重新打开后保存")
    if item and item.status == 3:
        raise conflict("任务已完成，保留原计划和完成记录")
    if item:
        ps.edit_milestone(db, row, item, {"node_name": body.title, "owner_id": body.owner_id, "plan_date": body.due_on}, p.id)
    else:
        item = ps.add_milestone(db, row, {"node_name": body.title, "owner_id": body.owner_id, "plan_date": body.due_on}, p.id)
    plan = db.get(Plan, item.id) or Plan(milestone_id=item.id, revision=0, created_at=now())
    plan.requirements, plan.preparation_on, plan.cadence = body.requirements, body.preparation_on, body.cadence
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
    log_op(db, p.id, "market.key_task.plan", "biz_project_milestone", item.id,
           after=body.model_dump(mode="json", exclude={"expected_revision"}))
    return task_data(db, p, row, item, history=True)


def feedback(db, p, code, task_id, body):
    row = project(db, p, code, lock=True)
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
                                     "progress_note": body.content}, p.id)
    plan = db.get(Plan, item.id) or Plan(milestone_id=item.id, preparation_on=row.start_date, cadence="node", revision=0, created_at=now())
    plan.problems, plan.next_step, plan.revision = body.problems, body.next_step, plan.revision + 1
    db.add(plan)
    text_body = f"任务「{item.node_name}」：{body.content}" + (f"\n完成阶段：{'、'.join(nodes[i].name for i in sorted(selected))}" if selected else "") + (f"\n存在问题：{body.problems}" if body.problems else "") + (f"\n下一步：{body.next_step}" if body.next_step else "")
    fb = market.add_feedback(db, row, {"content": text_body, "kind": "risk" if body.problems else "progress"}, p.id)
    db.flush()
    db.add(Update(milestone_id=item.id, feedback_id=fb.id, values=body.model_dump(mode="json", exclude={"expected_revision"}), created_at=now()))
    db.flush()
    log_op(db, p.id, "market.key_task.feedback", "biz_project_milestone", item.id, after={"feedback_id": fb.id, "status": body.status})
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


def detail(db, p, code, period=""):
    row = project(db, p, code)
    paused = db.get(BizProjectFollowSetting, row.id)
    tasks = [task_data(db, p, row, m) for m in row.milestones]
    fbs = db.scalars(select(BizProjectFeedback).where(BizProjectFeedback.project_id == row.id).order_by(BizProjectFeedback.feedback_date.desc(), BizProjectFeedback.id.desc()).limit(50))
    return {**ps.project_brief(db, row), "content": row.remark or "", "revision": project_revision(row), "today": today(),
            "paused": bool(paused and paused.paused), "can_manage": can_manage(p, row), "tasks": tasks,
            "feedbacks": [ps.feedback_dict(f) for f in fbs], "money": ps.contract_detail(db, row.project_code),
            "financials": financials(db, row, period)}


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
    if data["needs_feedback"]:
        reasons.append(f"应于 {data['feedback_due_on']} 填写进展")
    return reasons


def eligible_notice(db, notice):
    try:
        mid = int(str(notice.ref_id).split(":")[0])
    except (TypeError, ValueError):
        return False
    item, u = db.get(BizProjectMilestone, mid), db.get(SysUser, notice.user_id)
    if not item or not u or u.is_deleted or u.status != 1:
        return False
    row = db.get(BizProject, item.project_id)
    if not row or row.is_deleted or not row.is_key or row.status == 5 or item.status == 3:
        return False
    paused = db.get(BizProjectFollowSetting, row.id)
    p = build_principal(u)
    if (paused and paused.paused) or not any(p.has_menu(m) for m in ("market", "projects")) or not ps.can_view(db, p, row):
        return False
    data = task_data(db, p, row, item)
    return notice.user_id in {item.owner_id, row.owner_id} and bool(reminder_reasons(data))


def scan_reminders(db):
    start, end = day_bounds(today())
    sent = 0
    for item, row in db.execute(select(BizProjectMilestone, BizProject).join(BizProject, BizProjectMilestone.project_id == BizProject.id)
            .where(BizProject.is_key.is_(True), BizProject.is_deleted.is_(False), BizProject.status != 5, BizProjectMilestone.status != 3)):
        for uid in {u for u in (item.owner_id, row.owner_id) if u}:
            probe = SysNotify(user_id=uid, ref_id=str(item.id))
            if not eligible_notice(db, probe) or db.scalar(select(SysNotify.id).where(SysNotify.user_id == uid,
                SysNotify.msg_type == NOTICE, SysNotify.ref_id == str(item.id), SysNotify.created_at >= start, SysNotify.created_at < end).limit(1)):
                continue
            u = db.get(SysUser, uid)
            p = build_principal(u)
            reasons = reminder_reasons(task_data(db, p, row, item))
            link = f"/market/projects/{row.project_code}?task={item.id}"
            notices = notify_service.send(db, [u], NOTICE, "重点项目节点待处理", f"{row.name} / {item.node_name}：" + "；".join(reasons),
                link=link, ref_type=REF, ref_id=str(item.id), defer=True, wecom_content="重点项目有节点待处理，请打开工作台查看并填写进展。")
            for notice in notices:
                notice.created_at = now()
            sent += 1
    return {"rows": sent, "message": f"已为任务负责人及项目负责人生成 {sent} 组节点提醒"}

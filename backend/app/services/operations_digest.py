"""Period summaries generated from visible task records and timestamped feedback."""
from datetime import date, datetime
import re

from sqlalchemy import select

from app.core.response import bad_request
from app.core.timeutil import TZ, day_bounds, last_week, month_range, now, prev_month, today, week_range
from app.models import OpsTaskUpdate, OpsSchoolVisit, OpsSchool, SysUser
from app.services import operations_service as ops
from app.services import operations_followup as followups
from app.services import operations_scope as scope


def period_summary(db, p, mode, period, project_id=None, mine=False, owner_id=None):
    if not period:
        period = last_week() if mode == "week" else prev_month(today().strftime("%Y-%m"))
    pattern = r"20\d{2}-W\d{2}" if mode == "week" else r"20\d{2}-(0[1-9]|1[0-2])"
    if not re.fullmatch(pattern, period):
        raise bad_request("请选择有效周次或月份")
    try:
        start, end = week_range(period) if mode == "week" else month_range(period)
    except ValueError as exc:
        raise bad_request("请选择有效周次或月份") from exc
    if start > today():
        raise bad_request("不能汇总未来期间")
    end = min(end, today())
    if project_id is not None:
        scope.require(db, p, ops.get_project(db, p, project_id), "reports")
    permitted_report_projects = scope.content_project_ids(db, p, "reports")
    if owner_id and owner_id != p.id:
        if project_id is None:
            raise bad_request("查看指定人员汇报时请先选择项目")
        ops.manage(db, p, ops.get_project(db, p, project_id))
    rows = [r for r in ops.tasks(db, p, project_id, mine) if r["approval_status"] == "approved"
            and r["project_id"] in permitted_report_projects and (not owner_id or r["owner_id"] == owner_id)]
    end_exclusive = day_bounds(end)[1]
    start_at = day_bounds(start)[0]
    events = {}
    for event in db.scalars(select(OpsTaskUpdate).where(
        OpsTaskUpdate.task_id.in_([r["id"] for r in rows]), OpsTaskUpdate.created_at < end_exclusive,
        OpsTaskUpdate.kind.in_(("assigned", "feedback", "planned", "submitted", "review")))
        .order_by(OpsTaskUpdate.created_at, OpsTaskUpdate.id)):
        entry = events.setdefault(event.task_id, {})
        entry["first"] = entry.get("first", event.created_at)
        if event.kind == "feedback":
            entry["last"] = event
            if event.created_at >= start_at:
                entry["in_period"] = event
                entry.setdefault("all_feedback", []).append(event)
    result = []
    for row in rows:
        event = events.get(row["id"], {})
        completed_at = row["completed_at"]
        completed_on = None
        if completed_at:
            raw = datetime.fromisoformat(completed_at) if isinstance(completed_at, str) else completed_at
            completed_on = raw.astimezone(TZ).date()
        if completed_on and completed_on < start:
            continue
        feedback = event.get("in_period")
        first_on = event["first"].astimezone(TZ).date() if "first" in event else None
        done = bool(completed_on and start <= completed_on <= end)
        if not (done or feedback or (first_on and start <= first_on <= end) or
                (date.fromisoformat(row["due_date"]) <= end and (first_on is None or first_on <= end))):
            continue
        last = event.get("last")
        status = "done" if done else (last.status if last and last.status != "done" else "todo")
        if not done and row["due_date"] < str(end) and status != "blocked":
            label = "逾期未完成"
        else:
            label = ops.STATUS_LABELS[status]
        deadline = row.get("deadline_basis") or row.get("original_due_date") or row["due_date"]
        late_done = bool(done and completed_on and deadline and str(completed_on) > str(deadline))
        if late_done:
            label = "延期完成"
        result.append({k: row[k] for k in ("id", "project_id", "project_name", "title", "owner_id", "owner_name", "due_date")} |
                      {"status": status, "status_label": label, "done": done, "feedback": feedback.content if feedback else "",
                       "feedback_at": feedback.created_at if feedback else None, "next_step": last.next_step if last else "",
                       "completed_on": completed_on if done else None,
                       "late_done": late_done, "original_due_date": deadline,
                       "incomplete_reason": row.get("incomplete_reason", ""),
                       "feedback_events": [{"id": e.id, "author_id": e.author_id, "author": e.author.name,
                                            "at": e.created_at, "content": e.content, "next_step": e.next_step}
                                           for e in event.get("all_feedback", [])],
                       "link": f"/operations/{row['project_id']}?task={row['id']}"})
    links = followups.node_links(db, [e["id"] for row in result for e in row["feedback_events"]])
    for row in result:
        for event in row["feedback_events"]:
            event["node_id"] = links.get(event["id"])
    permitted_projects = [i for i in scope.content_project_ids(db, p, "outreach") if i in permitted_report_projects]
    project_ids = [i for i in permitted_projects if project_id is None or i == project_id]
    visits_q = select(OpsSchoolVisit, OpsSchool.name, OpsSchool.project_id, SysUser.name).join(
        OpsSchool, OpsSchool.id == OpsSchoolVisit.school_id).join(SysUser, SysUser.id == OpsSchoolVisit.author_id).where(
        OpsSchool.project_id.in_(project_ids), OpsSchoolVisit.occurred_on >= start, OpsSchoolVisit.occurred_on <= end)
    if mine:
        visits_q = visits_q.where(OpsSchoolVisit.author_id == p.id)
    elif owner_id:
        visits_q = visits_q.where(OpsSchoolVisit.author_id == owner_id)
    visits = [{"id": v.id, "school_id": v.school_id, "school_name": name, "project_id": pid, "author": author,
               "author_id": v.author_id, "occurred_on": v.occurred_on, "content": v.content,
               "next_action": v.next_action, "next_contact_date": v.next_contact_date}
              for v, name, pid, author in db.execute(visits_q.order_by(OpsSchoolVisit.occurred_on, OpsSchoolVisit.id))]
    stats = {"total": len(result), "done": sum(r["done"] for r in result),
             "late_done": sum(r["late_done"] for r in result),
             "blocked": sum(r["status"] == "blocked" for r in result),
             "unreported": sum(not r["feedback"] for r in result)}
    report = {"period": period, "mode": mode, "start": start, "end": end, "items": result, "stats": stats, "visits": visits,
              "generated_at": now(), "person": p.user.name if mine else ops.user(db, owner_id).name if owner_id else "项目团队",
              "note": "预览按当前权限和归属展示，反馈按所选期间登记记录统计；保存后保留当时的报告版本。未反馈不推测进展。"}
    report["text"] = render_text(report)
    return report


def render_text(report):
    stats = report["stats"]
    lines = [f"{report.get('person', '项目团队')} · {report['period']} {'周报' if report.get('mode') == 'week' else '月报'}",
             f"统计期间：{report['start']} 至 {report['end']}",
             f"本期纳入 {stats['total']} 项任务，完成 {stats['done']} 项（其中延期完成 {stats.get('late_done', 0)} 项），受阻 {stats['blocked']} 项，未反馈 {stats['unreported']} 项。"]
    for title, predicate in (("一、完成事项", lambda r: r["done"]), ("二、进行中与未完成", lambda r: not r["done"])):
        lines.extend(["", title])
        selected = [r for r in report["items"] if predicate(r)]
        if not selected:
            lines.append("本期暂无已登记事项。")
        for row in selected:
            lines.append(f"• {row['owner_name']}｜{row['project_name']} / {row['title']}：{row['status_label']}，截止 {row['due_date']}。" +
                         (f"实际完成 {row['completed_on']}；原定 {row['original_due_date']}。" if row.get("completed_on") else ""))
            for event in row.get("feedback_events", []):
                at = str(event["at"])[:16].replace("T", " ")
                node = f"，节点 {event['node_id']}" if event.get("node_id") else ""
                lines.append(f"  {at} {event['author']}{node}：{event['content']}")
            if not row.get("feedback_events"):
                lines.append("  本期未反馈，不推测实际进展。")
            if row.get("next_step"):
                lines.append("  下一步：" + row["next_step"])
            lines.append(f"  来源：任务 #{row['id']} {row['link']}")
    lines.extend(["", "三、学校开拓与跟进"])
    for visit in report.get("visits", []):
        lines.append(f"• {visit['occurred_on']} {visit['author']}跟进{visit['school_name']}：{visit['content']}" +
                     (f"；下一步：{visit['next_action']}" if visit['next_action'] else "") + f"（跟进记录 #{visit['id']}）")
    if not report.get("visits"):
        lines.append("本期暂无已登记的学校跟进记录。")
    lines.extend(["", "四、需协调与下一步"])
    pending = [r for r in report["items"] if not r["done"] and (r["status"] == "blocked" or r["status_label"] == "逾期未完成")]
    for row in pending:
        lines.append(f"• {row['owner_name']}：{row['title']}，{row['status_label']}；请结合上述原反馈核对原因与下一步。")
    if not pending:
        lines.append("已登记任务中暂无受阻或逾期未完成事项；其他协调需求请补充到原任务。")
    lines.extend(["", report["note"]])
    return "\n".join(lines)

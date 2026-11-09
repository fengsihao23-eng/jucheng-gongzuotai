from datetime import datetime
import uuid

import pytest
from sqlalchemy import select

from app.core.deps import build_principal
from app.core.response import BizError
from app.models import OpsTask, OpsTaskReview, SysNotify, SysUser
from tests.conftest import data
from tests.test_operations import BASE, project, member, report, task, uid


def propose(client, login, db, project, **kw):
    member(client, login, db, project)
    return client.post(f"{BASE}/{project['id']}/tasks", headers=login("xiekai"), json={
        "title": "自行安排现场需求确认", "owner_id": uid(db,"xiekai"), "due_date":"2026-10-25", "source":"self",
        "plan_month":"2026-10", "department_reviewer_id":uid(db,"wujun"), "company_reviewer_id":uid(db,"zhangzhenhua"), **kw})


def review(client, login, t, actor, decision="approve", **kw):
    return client.post(f"{BASE}/tasks/{t['id']}/review", headers=login(actor), json={
        "decision":decision,"comment":"同意按计划推进" if decision=="approve" else "请补充具体完成要求",
        "expected_revision":t["revision"], **kw})


def monthly(client, login, project, actor="admin", **query):
    return data(client.get(f"{BASE}/monthly", params={"project_id":project["id"],"month":"2026-10",**query},headers=login(actor)))


def test_company_task_automatically_in_month_and_group(client, login, db, project):
    t=task(client,login,db,project)
    assert t["plan_month"]=="2026-10" and t["source"]=="company" and t["approval_status"]=="approved"
    m=monthly(client,login,project,"xiekai")
    assert m["source_counts"]=={"self":0,"company":1}
    assert m["groups"][0]["company_tasks"][0]["id"]==t["id"]
    assert m["groups"][0]["self_tasks"]==[]


def test_two_stage_review_then_feedback_is_single_task(client, login, db, project):
    t=data(propose(client,login,db,project))
    assert t["approval_status"]=="department_pending" and not t["can_feedback"]
    assert t["original_due_date"] is None and t["deadline_status"] is None
    assert report(client,login,t).status_code==403
    assert review(client,login,t,"zhangzhenhua").status_code==403
    assert review(client,login,t,"xiekai").status_code==403
    assert review(client,login,t,"admin").status_code==403  # 管理员也不能代替指定审核人
    m=monthly(client,login,project,"wujun")
    assert m["stats"]["total"]==0 and m["stats"]["awaiting_approval"]==1 and len(m["review_inbox"])==1
    first=data(review(client,login,t,"wujun"))
    assert first["approval_status"]=="company_pending"
    assert review(client,login,t,"wujun").status_code==409
    final=data(review(client,login,first,"zhangzhenhua"))
    assert final["approval_status"]=="approved" and len(final["reviews"])==2
    assert final["original_due_date"]=="2026-10-25"
    d=data(report(client,login,t,status="done",progress=100,content="现场需求清单已确认"))
    assert d["completed_at"] and d["id"]==t["id"]
    m=monthly(client,login,project)
    assert m["stats"]["done"]==1 and m["source_counts"]["self"]==1
    assert m["groups"][0]["self_tasks"][0]["latest_feedback"]=="现场需求清单已确认"
    notices=db.scalars(select(SysNotify).where(SysNotify.ref_type=="ops_task",SysNotify.ref_id==str(t["id"]))).all()
    assert {uid(db,"wujun"),uid(db,"zhangzhenhua"),uid(db,"xiekai")} <= {n.user_id for n in notices}
    assert all(n.channel=="inapp" for n in notices)


def test_rejection_edit_resubmission_and_stale_approval(client,login,db,project):
    t=data(propose(client,login,db,project))
    rejected=data(review(client,login,t,"wujun",decision="reject"))
    assert rejected["approval_status"]=="rejected"
    assert monthly(client,login,project)["stats"]["rejected"]==1
    changed=data(client.put(f"{BASE}/tasks/{t['id']}",headers=login("xiekai"),json={"description":"完成校方需求清单并获得签字确认"}))
    assert changed["approval_status"]=="department_pending" and changed["revision"]>rejected["revision"]
    assert review(client,login,t,"wujun").status_code==409
    first=data(review(client,login,changed,"wujun"))
    changed=data(client.put(f"{BASE}/tasks/{t['id']}",headers=login("xiekai"),json={"due_date":"2026-11-05","plan_month":"2026-11"}))
    assert changed["plan_month"]=="2026-11" and changed["approval_status"]=="department_pending"
    assert review(client,login,first,"zhangzhenhua").status_code==409
    assert len(monthly(client,login,project,month="2026-11")["items"])==1


def test_review_requires_distinct_authorized_people(client,login,db,project):
    assert propose(client,login,db,project,department_reviewer_id=uid(db,"xiekai")).status_code==400
    assert propose(client,login,db,project,company_reviewer_id=uid(db,"wujun")).status_code==400
    assert propose(client,login,db,project,company_reviewer_id=uid(db,"caofang")).status_code==400
    assert propose(client,login,db,project,department_reviewer_id=None).status_code==400
    assert propose(client,login,db,project,owner_id=uid(db,"xuhao")).status_code==403
    assert propose(client,login,db,project,source="company").status_code==403


def test_explicit_team_reviewer_and_revocation(client,login,db,project):
    member(client,login,db,project,"yangyue","reviewer")
    t=data(propose(client,login,db,project,department_reviewer_id=uid(db,"yangyue")))
    assert client.delete(f"{BASE}/{project['id']}/members/{uid(db,'yangyue')}",headers=login("admin")).status_code==409
    assert client.post(f"{BASE}/{project['id']}/members",headers=login("admin"),json={"user_id":uid(db,"yangyue"),"role":"member"}).status_code==409
    assert data(review(client,login,t,"yangyue"))["approval_status"]=="company_pending"


def test_unfinished_reason_and_cross_month_visibility(client,login,db,project):
    t=task(client,login,db,project,due_date="2026-09-30")
    assert report(client,login,t,incomplete_reason="").status_code==400
    blocked=data(report(client,login,t,status="blocked",incomplete_reason="等待校方签字"))
    assert blocked["delay_status"]=="pending"
    m=monthly(client,login,project)
    assert m["carryover_count"]==1 and m["items"][0]["reason"]=="等待校方签字"
    finished=data(report(client,login,t,status="done",progress=100))
    assert finished["deadline_status"]=="late_done"
    assert [entry["reason"] for entry in finished["delay_history"]]==["等待校方签字"]
    assert len(monthly(client,login,project)["items"])==1
    assert monthly(client,login,project,month="2026-11")["items"]==[]


def test_extension_keeps_original_plan_month(client,login,db,project):
    t=task(client,login,db,project,due_date="2026-10-25")
    updated=data(client.put(f"{BASE}/tasks/{t['id']}",headers=login("wujun"),json={"due_date":"2026-11-10"}))
    assert updated["plan_month"]=="2026-10"
    assert updated["original_due_date"]=="2026-10-25"
    assert len(monthly(client,login,project)["items"])==1
    assert monthly(client,login,project,month="2026-11")["carryover_count"]==1


def test_filter_scope_month_validation_and_pending_reminders(client,login,db,project):
    from app.jobs.operations import operations_task_reminder
    t=data(propose(client,login,db,project,due_date="2026-10-19"))
    operations_task_reminder(db,{})
    assert not db.scalar(select(SysNotify.id).where(SysNotify.ref_type=="ops_task_reminder",SysNotify.ref_id==f"{t['id']}:2026-10-20"))
    assert client.get(f"{BASE}/monthly",params={"project_id":project["id"]},headers=login("caofang")).status_code==404
    assert client.get(f"{BASE}/monthly",params={"month":"2026-13"},headers=login("admin")).status_code==400
    assert monthly(client,login,project,owner_id=uid(db,"xuhao"))["items"]==[]
    assert len(monthly(client,login,project,"xiekai",mine=True)["items"])==1


def test_assistant_confirms_business_action_without_skipping_approvals(client,login,db,project,tmp_path,monkeypatch):
    from app.core.config import settings
    from app.services import xiaoju_business as business
    monkeypatch.setattr(settings,"storage_dir",tmp_path/"var/files")
    member(client,login,db,project)
    runner=business.BusinessExecutor(uid(db,"xiekai"),intent="提交本人月度任务")
    action=runner.execute("operations.create_operation_task",{"path":{"project_id":project["id"]},"body":{
        "title":"对话提交月度计划","owner_id":uid(db,"xiekai"),"due_date":"2026-10-25","source":"self",
        "department_reviewer_id":uid(db,"wujun"),"company_reviewer_id":uid(db,"zhangzhenhua")}})["action"]
    result=business.confirm(action["id"],runner.user_id)["result"]["data"]
    assert result["approval_status"]=="department_pending"
    reviewer=business.BusinessExecutor(uid(db,"wujun"),intent="审核任务")
    action=reviewer.execute("operations.review_operation_task",{"path":{"task_id":result["id"]},"body":{
        "decision":"approve","comment":"同意","expected_revision":result["revision"]}})["action"]
    result=business.confirm(action["id"],reviewer.user_id)["result"]["data"]
    assert result["approval_status"]=="company_pending"


def test_workbuddy_create_review_feedback_monthly(client,login,db,project):
    from tests.test_mcp import call
    member(client,login,db,project)
    tokens={name:data(client.post('/api/v1/auth/mcp-token/reset',headers=login(name)))["token"] for name in ('xiekai','wujun','zhangzhenhua')}
    denied=call(client,tokens['xiekai'],'ops.task_create',project_id=project['id'],title='无权公司派单',owner_id=uid(db,'xiekai'),due_date='2026-10-25')
    assert not denied['ok']
    result=call(client,tokens['xiekai'],'ops.task_create',project_id=project['id'],title='WorkBuddy自拟计划',owner_id=uid(db,'xiekai'),due_date='2026-10-25',source='self',department_reviewer_id=uid(db,'wujun'),company_reviewer_id=uid(db,'zhangzhenhua'))
    assert result['ok'] and result['data']['approval_status']=='department_pending'
    task=result['data']
    for actor,status in [('wujun','company_pending'),('zhangzhenhua','approved')]:
        result=call(client,tokens[actor],'ops.task_review',task_id=task['id'],decision='approve',comment='同意',expected_revision=task['revision'])
        assert result['ok'] and result['data']['approval_status']==status
        task=result['data']
    result=call(client,tokens['xiekai'],'ops.task_feedback',task_id=task['id'],content='已完成核对',status='done',progress=100)
    assert result['ok'] and result['data']['status']=='done'
    result=call(client,tokens['zhangzhenhua'],'ops.project_status',project_id=project['id'],month='2026-10')
    assert result['ok'] and result['data']['stats']['done']==1

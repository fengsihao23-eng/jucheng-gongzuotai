"""Acceptance for the user's refined meeting transcript."""
from datetime import date, timedelta
from decimal import Decimal
import json
import uuid

from sqlalchemy import select, func
from app.core.timeutil import now, today
from app.models import (OpsTask, OpsIncome, OpsMemberView, OpsDigestSnapshot, OpsProject, OpsSchool, SysNotify,
                        SysUser, BizProject, BizExpenseReport, OpsOutreachGoal)
from tests.test_meeting_iteration import setup, uid
from conftest import data

BASE = "/api/v1/operations"


def set_view(client, login, db, project, sections, task_scope="self", revision=0):
    return client.put(f"{BASE}/{project['id']}/member-visibility/{uid(db, 'xiekai')}", headers=login("wujun"), json={
        "sections": sections, "task_scope": task_scope, "expected_revision": revision})


def test_home_contains_own_tasks_replies_and_only_authorized_projects(client, login, db, setup):
    project, school, task = setup
    result = data(client.get("/api/v1/portal/work", headers=login("xiekai")))
    assert task["id"] in {r["id"] for r in result["todos"]}
    assert project["id"] in {r["id"] for r in result["projects"]}
    other = data(client.post(BASE, headers=login("admin"), json={"name": "无权项目", "owner_id": uid(db, "admin")}))
    result = data(client.get("/api/v1/portal/work", headers=login("xiekai")))
    assert other["id"] not in {r["id"] for r in result["projects"]}
    assert project["id"] not in {r["id"] for r in data(client.get("/api/v1/portal/work", headers=login("chenjing")))["projects"]}
    data(client.post(f"{BASE}/tasks/{task['id']}/discussions", headers=login("wujun"), json={"kind": "request", "recipient_id": uid(db, "xuhao"), "content": "请协助核对"}))
    result = data(client.get("/api/v1/portal/work", headers=login("xuhao")))
    assert task["id"] in {r["id"] for r in result["todos"]} and result["stats"]["replies"] >= 1


def test_content_scope_redacts_detail_history_reports_products_and_direct_routes(client, login, db, setup):
    project, school, task = setup
    base = f"{BASE}/{project['id']}"
    data(client.put(f"{base}/schools/{school['id']}", headers=login("wujun"), json={"name": "试点目标学校", "labor_cost": "725.19", "other_cost": "128.31"}))
    data(client.post(f"{base}/schools/{school['id']}/income", headers=login("wujun"), json={"income_date": "2026-10-19", "amount": "123.45"}))
    data(client.post(base + "/products", headers=login("wujun"), json={"name": "保密产品", "reference_price": "888.21"}))
    data(set_view(client, login, db, project, ["outreach", "reports"]))
    result = data(client.get(base, headers=login("xiekai")))
    assert result["products"] == [] and result["financials"]["total_income"] is None
    assert result["members"] == [] and result["unassigned_incomes"] == []
    assert "incomes" not in result["schools"][0] and "equipment" not in result["schools"][0]
    assert "labor_cost" not in result["schools"][0]
    assert client.get(base + "/income-board", headers=login("xiekai")).status_code == 403
    assert client.get(base + "/funds", headers=login("xiekai")).status_code == 403
    assert client.get(base + "/progress", headers=login("xiekai")).status_code == 403
    assert project["id"] not in {r["project_id"] for r in data(client.get(BASE + "/products", headers=login("xiekai")))["items"]}
    history = data(client.get(f"{base}/schools/{school['id']}/history", headers=login("xiekai")))
    assert "725.19" not in json.dumps(history)
    brief = data(client.get(base + "/brief", headers=login("xiekai")))
    assert "123.45" not in json.dumps(brief) and "888.21" not in json.dumps(brief)
    assert set_view(client, login, db, project, ["income"]).status_code == 409
    data(set_view(client, login, db, project, ["tasks"], revision=1))
    assert client.get(BASE + "/digest", headers=login("xiekai"), params={"project_id": project["id"], "period": "2026-W43"}).status_code == 403
    assert client.get(f"{base}/schools/{school['id']}/visits", headers=login("xiekai")).status_code == 403


def test_self_task_scope_and_notifications_hide_removed_access(client, login, db, setup):
    project, school, task = setup
    second = data(client.post(f"{BASE}/{project['id']}/tasks", headers=login("admin"), json={"title": "其他人的事", "owner_id": uid(db,"xuhao"), "due_date": "2026-10-25"}))
    data(set_view(client, login, db, project, ["tasks", "reports"]))
    assert client.get(f"{BASE}/tasks/{second['id']}", headers=login("xiekai")).status_code == 404
    assert client.get(f"{BASE}/tasks/{task['id']}", headers=login("xiekai")).status_code == 200
    data(client.post(f"{BASE}/tasks/{task['id']}/discussions", headers=login("wujun"), json={"kind":"request", "content":"只给原负责人看的要求", "recipient_id":uid(db,"xiekai")}))
    before = data(client.get("/api/v1/notifications", headers=login("xiekai"), params={"page_size":1000}))
    assert any("只给原负责人" in r["content"] for r in before["items"])
    data(client.put(f"{BASE}/tasks/{task['id']}", headers=login("wujun"), json={"owner_id":uid(db,"xuhao"),"visibility":"selected","viewer_ids":[]}))
    after = data(client.get("/api/v1/notifications", headers=login("xiekai"), params={"page_size":1000}))
    assert not any("只给原负责人" in r["content"] for r in after["items"])


def test_yearly_goal_is_one_task_with_region_and_private_forecast(client, login, db, setup):
    project, school, task = setup
    base=f"{BASE}/{project['id']}"
    payload={"school_id":school["id"],"year":2026,"owner_id":uid(db,"xiekai"),"target_date":"2026-12-20","objective":"完成试点上线","expected_annual_income":"50000"}
    assert client.post(base + "/outreach-goals", headers=login("xiekai"), json=payload).status_code == 403
    goal=data(client.post(base + "/outreach-goals", headers=login("wujun"), json=payload))
    assert client.post(base + "/outreach-goals", headers=login("wujun"), json=payload).status_code == 409
    row=data(client.get(f"{BASE}/tasks/{goal['task_id']}",headers=login("xiekai")))
    data(client.post(base + "/outreach-goals",headers=login("wujun"),json=payload|{"expected_revision":row["revision"],"objective":"更新目标要求"}))
    assert db.scalar(select(func.count()).select_from(OpsOutreachGoal).where(OpsOutreachGoal.project_id==project["id"])) == 1
    data(client.put(f"{base}/schools/{school['id']}/region",headers=login("wujun"),json={"region":"阳东"}))
    assert data(client.get(base + "/outreach-goals",headers=login("wujun"),params={"year":2026,"region":"阳东"}))["stats"]["targets"] == 1
    assert not data(client.get(base + "/outreach-goals",headers=login("wujun"),params={"year":2025}))["items"]
    data(set_view(client,login,db,project,["outreach"]))
    result=data(client.get(base + "/outreach-goals",headers=login("xiekai")))
    assert result["stats"]["expected_annual_income"] is None and result["items"][0]["expected_annual_income"] is None


def test_reserved_income_preview_confirm_dedupe_and_conflict(client,login,db,setup):
    project,school,task=setup
    base=f"{BASE}/{project['id']}"
    body={"source_key":"future-payments","batch_key":"day-20261019","rows":[{"school_id":school["id"],"income_date":"2026-10-19","amount":"21.50"}]}
    assert client.post(base+'/income-feed/preview',headers=login("xiekai"),json=body).status_code==403
    assert client.post(base+'/income-feed/confirm',headers=login("wujun"),json=body).status_code==409
    preview=data(client.post(base+'/income-feed/preview',headers=login("wujun"),json=body))
    assert preview["new_rows"]==1 and not db.scalar(select(OpsIncome.id).where(OpsIncome.school_id==school['id']))
    confirmed=body|{"expected_sha256":preview["sha256"]}
    result=data(client.post(base+'/income-feed/confirm',headers=login("wujun"),json=confirmed))
    assert not result['already_imported']
    assert data(client.post(base+'/income-feed/confirm',headers=login("wujun"),json=confirmed))['already_imported']
    changed=body|{"rows":[{**body['rows'][0],"amount":"99.00"}]}
    assert client.post(base+'/income-feed/preview',headers=login("wujun"),json=changed).status_code==409
    changed['batch_key']='day-20261019-v2'
    preview=data(client.post(base+'/income-feed/preview',headers=login("wujun"),json=changed))
    assert preview['error_count']==1
    assert client.post(base+'/income-feed/confirm',headers=login("wujun"),json=changed|{"expected_sha256":preview['sha256']}).status_code==409
    assert db.scalar(select(OpsIncome.amount).where(OpsIncome.school_id==school['id'])) == Decimal('21.50')


def test_income_year_region_cards_match_entries_and_no_fake_feed(client,login,db,setup):
    project,school,task=setup
    base=f"{BASE}/{project['id']}"
    assert data(client.get(base+'/income-board',headers=login('wujun')))['connection_state']=='未接入固定数据源'
    data(client.put(f"{base}/schools/{school['id']}/region",headers=login('wujun'),json={'region':'阳东'}))
    for when,amount in [('2025-12-31','100'),('2026-09-15','200'),('2026-10-19','300'),('2026-10-20','400')]:
        data(client.post(f"{base}/schools/{school['id']}/income",headers=login('wujun'),json={'income_date':when,'amount':amount}))
    for period,amount in [('all',900),('month',700),('week',700),('today',400)]:
        report=data(client.get(base+'/income-board',headers=login('wujun'),params={'year':2026,'period':period,'region':'阳东'}))
        card=next(c for c in report['cards'] if c['key']==period)
        assert Decimal(card['amount'])==amount==sum(Decimal(i['amount']) for i in report['items'])
    assert data(client.get(base+'/income-board',headers=login('wujun'),params={'year':2025}))['cards'][0]['amount']=='100.00'
    assert not data(client.get(base+'/income-board',headers=login('wujun'),params={'region':'阳西'}))['items']
    assert client.get(base+'/income-board',headers=login('wujun'),params={'year':2025,'period':'month'}).status_code==400


def test_selected_report_members_and_manager_can_generate_person_report(client,login,db,setup,monkeypatch):
    from app.jobs.operations import generate_period_reports
    project,school,task=setup
    base=f"{BASE}/{project['id']}"
    control={'wecom_reminders':True,'report_audience':'selected','report_user_ids':[uid(db,'xiekai')],'expected_revision':0}
    assert client.put(base+'/controls',headers=login('xiekai'),json=control).status_code==403
    data(client.put(base+'/controls',headers=login('wujun'),json=control))
    assert client.put(base+'/controls',headers=login('wujun'),json=control).status_code==409
    monkeypatch.setenv('FAKE_TODAY','2026-10-26')
    generate_period_reports(db,'week')
    reports=list(db.scalars(select(OpsDigestSnapshot).where(OpsDigestSnapshot.project_id==project['id'])))
    assert {r.owner_id for r in reports if r.audience=='personal'}=={uid(db,'xiekai')}
    body={'project_id':project['id'],'owner_id':uid(db,'xuhao'),'mode':'week','period':'2026-W43'}
    report=data(client.post(BASE+'/digest/reports',headers=login('wujun'),json=body))
    assert report['owner_id']==uid(db,'xuhao') and report['audience']=='personal'
    assert client.post(BASE+'/digest/reports',headers=login('xiekai'),json=body).status_code==403


def test_wecom_discussion_and_reminder_cancel_after_reply_or_completion(client,login,db,setup,monkeypatch):
    from app.services import notify_service
    from app.integration.wecom.base import SendResult
    from app.jobs.operations import operations_task_reminder
    project,school,task=setup
    with monkeypatch.context() as hold:
        hold.setattr(notify_service, 'deliver_committed', lambda *_: None)
        result=data(client.post(f"{BASE}/tasks/{task['id']}/discussions",headers=login('wujun'),json={'kind':'request','content':'请回复','recipient_id':uid(db,'xiekai')}))
    request=result['discussions'][0]
    notice=db.scalar(select(SysNotify).where(SysNotify.ref_type=='ops_discussion',SysNotify.ref_id==str(request['id']),SysNotify.channel=='wecom_app'))
    assert notice and notice.send_status==0
    sent=[]
    class Provider:
        def send_app_message(self,ids,content):
            sent.append(ids)
            return SendResult(True,'test')
    monkeypatch.setattr(notify_service,'get_wecom',lambda:Provider())
    data(client.post(f"{BASE}/tasks/{task['id']}/discussions",headers=login('xiekai'),json={'kind':'reply','content':'已知悉','reply_to_id':request['id']}))
    notify_service._deliver(db,[notice])
    assert sent == [['wujun']] and notice.retry_count == 3
    sent.clear()
    row=db.get(OpsTask,task['id']);row.last_feedback_at=now()-timedelta(days=4);db.commit()
    with monkeypatch.context() as hold:
        hold.setattr(notify_service,'deliver_pending',lambda *_: 0)
        operations_task_reminder(db,{})
    reminder=db.scalar(select(SysNotify).where(SysNotify.ref_type=='ops_task_reminder',SysNotify.ref_id.startswith(f"{task['id']}:{today()}:"),SysNotify.user_id==uid(db,'xiekai'),SysNotify.channel=='wecom_app'))
    assert reminder
    data(client.post(f"{BASE}/tasks/{task['id']}/feedback",headers=login('xiekai'),json={
        'content':'已完成任务及节点','status':'done','completed_node_ids':[n['id'] for n in task['nodes']]}))
    db.expire(row)
    notify_service._deliver(db,[reminder])
    assert not sent and reminder.send_status==2


def test_task_notes_notify_default_or_selected_people_immediately(client, login, db, setup, monkeypatch):
    from app.integration.wecom.base import SendResult
    from app.services import notify_service

    deliveries = []

    class Provider:
        def send_app_message(self, users, message):
            deliveries.append((users, message))
            return SendResult(True, "discussion-test")

    monkeypatch.setattr(notify_service, "get_wecom", Provider)
    project, _, task = setup
    endpoint = f"{BASE}/tasks/{task['id']}"
    payload = {"kind": "note", "content": "企业微信卡片不包含的讨论正文", "node_id": task["nodes"][0]["id"]}
    result = data(client.post(endpoint + "/discussions", headers=login("xiekai"), json=payload))
    assert {u["id"] for u in result["default_note_recipients"]} == {uid(db, "wujun"), uid(db, "admin")}
    assert len(deliveries) == 1 and set(deliveries[0][0]) == {"wujun", "admin"}
    assert deliveries[0][1].msg_type == "textcard"
    assert deliveries[0][1].url.endswith(f"/operations/{project['id']}?task={task['id']}")
    assert payload["content"] not in deliveries[0][1].content
    assert result["discussions"][0]["node_id"] == payload["node_id"]
    data(client.post(endpoint + "/discussions", headers=login("admin"), json={**payload, "recipient_id": uid(db, "xuhao")}))
    assert len(deliveries) == 2 and deliveries[-1][0] == ["xuhao"]
    assert client.post(endpoint + "/discussions", headers=login("admin"),
        json={**payload, "recipient_id": uid(db, "chenjing")}).status_code == 403
    data(client.put(f"{BASE}/{project['id']}/controls", headers=login("wujun"),
                    json={"wecom_reminders": False, "expected_revision": 0}))
    result = data(client.post(endpoint + "/discussions", headers=login("xiekai"), json=payload))
    assert result["discussion_wecom"] is False and len(deliveries) == 2
    recent_id = result["discussions"][-1]["id"]
    assert set(db.scalars(select(SysNotify.channel).where(
        SysNotify.ref_type == "ops_discussion", SysNotify.ref_id == str(recent_id)))) == {"inapp"}
    project_note = data(client.post(f"/api/v1/record-followups/operation/{project['id']}", headers=login("admin"), json={
        "client_id": str(uuid.uuid4()), "action": "note", "content": "项目级互动留言"}))
    assert not project_note["discussion_wecom"] and len(deliveries) == 2
    assert set(db.scalars(select(SysNotify.channel).where(
        SysNotify.ref_type == "record_followup", SysNotify.ref_id == str(project_note["items"][0]["id"])))) == {"inapp"}


def test_linked_funds_keep_original_permission_and_approval_payment_distinction(client,login,db,setup):
    from tests.conftest import add_project_test_members
    project,school,task=setup
    code='JC-FUND-'+uuid.uuid4().hex[:8]
    business=BizProject(project_code=code,name='关联经费样例',customer_name='样例学校',business_line='XY',owner_id=uid(db,'wujun'),status=2)
    db.add(business);db.flush()
    db.add(BizExpenseReport(approval_no='FUND-'+uuid.uuid4().hex,applicant_id=uid(db,'chenjing'),project_code=code,amount=Decimal('123.45'),expense_date=today(),category='采购',status=4,pay_status=0,reason='已批未付款'))
    db.commit()
    add_project_test_members(db, [code], "admin")
    base=f"{BASE}/{project['id']}"
    data(client.put(base+'/controls',headers=login('admin'),json={'expected_revision':0,'business_project_code':code}))
    result=data(client.get(base+'/funds',headers=login('admin')))
    assert result['approved_amount']=='123.45' and result['paid_amount']=='0.00'
    assert client.get(base+'/funds',headers=login('xiekai')).status_code==403


def test_deleted_project_closes_task_and_old_notice_paths(client,login,db,setup):
    project,school,task=setup
    db.get(OpsProject,project['id']).status='deleted';db.commit()
    assert client.get(f"{BASE}/tasks/{task['id']}",headers=login('admin')).status_code==404
    notices=data(client.get('/api/v1/notifications',headers=login('xiekai'),params={'page_size':1000}))
    assert not any(r['link']==f"/operations/{project['id']}?task={task['id']}" for r in notices['items'])


def test_due_today_still_sends_when_weekly_feedback_not_due(client,login,db,setup,monkeypatch):
    from app.services import notify_service
    from app.integration.wecom.base import SendResult
    from app.jobs.operations import operations_task_reminder
    project,school,task=setup
    row=db.get(OpsTask,task['id']);row.due_date=today();row.cadence='weekly';row.last_feedback_at=now();db.commit()
    with monkeypatch.context() as hold:
        hold.setattr(notify_service,'deliver_pending',lambda *_: 0)
        operations_task_reminder(db,{})
    notice=db.scalar(select(SysNotify).where(SysNotify.ref_type=='ops_task_reminder',SysNotify.ref_id.startswith(f"{task['id']}:{today()}:"),SysNotify.channel=='wecom_app',SysNotify.user_id==row.owner_id))
    assert notice
    sent=[]
    class Provider:
        def send_app_message(self,ids,content):
            sent.append(ids);return SendResult(True,'due-today')
    monkeypatch.setattr(notify_service,'get_wecom',lambda:Provider())
    notify_service._deliver(db,[notice])
    assert sent==[['xiekai']] and notice.send_status==1

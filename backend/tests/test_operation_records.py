"""Audit actor isolation, committed changes, rollback, redaction and administrator search."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json
import uuid

from fastapi import Depends, Request
import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import get_principal, Principal
from app.core.response import bad_request, ok
from app.models import AuditChange, AuditEvent, LogOperation, SysUser
from tests.conftest import data

BASE="/api/v1/operation-records"


@pytest.fixture
def probe(client,db):
    from app.main import app
    db.execute(text("CREATE TABLE operation_audit_probe(id bigint PRIMARY KEY, value text, password_hash text, payload jsonb)"))
    db.execute(text("SELECT jc_audit_install()"))
    db.commit()
    def endpoint(request:Request,mode:str="",ident:int=1,p:Principal=Depends(get_principal),session:Session=Depends(get_db)):
        if mode=="insert":
            session.execute(text("INSERT INTO operation_audit_probe(id,value,password_hash,payload) VALUES(:id,:value,:secret,CAST(:payload AS jsonb))"),
                {"id":ident,"value":"原内容","secret":"probe-secret-should-never-be-stored",
                 "payload":json.dumps({"wecom_secret":"do-not-store","api_token":"private-value","phone":"13812345678",
                                      "idcard":"440106199001011234","business_code":"1794680670709",
                                      "url":"https://example.test?token=protected-url&x=1"})})
        elif mode=="update":
            session.execute(text("UPDATE operation_audit_probe SET value=:value WHERE id=:id"),{"value":"已修改","id":ident})
        elif mode=="delete":
            session.execute(text("DELETE FROM operation_audit_probe WHERE id=:id"),{"id":ident})
        elif mode in ("rollback","partial"):
            session.execute(text("INSERT INTO operation_audit_probe(id,value) VALUES(:id,'未提交')"),{"id":ident})
            if mode=="partial":
                session.commit()
            else:
                session.rollback()
            raise bad_request("验证失败结果")
        session.commit()
        return ok({"id":ident})
    path="/api/v1/audit-probe-"+uuid.uuid4().hex
    app.add_api_route(path,endpoint,methods=["POST"])
    try:
        yield path
    finally:
        app.router.routes[:]=[r for r in app.router.routes if getattr(r,"path",None)!=path]
        db.rollback()
        db.execute(text("DROP TABLE operation_audit_probe"))
        db.commit()


def find(db,path):
    return db.scalar(select(AuditEvent).where(AuditEvent.path==path).order_by(AuditEvent.id.desc()))


def test_authenticated_sql_changes_and_private_fields(client,login,db,probe):
    headers=login("admin")
    assert client.post(probe,params={"mode":"insert","ident":101},
                       headers={**headers,"X-Forwarded-For":"192.0.2.99, 198.51.100.25"}).status_code==200
    event=find(db,probe)
    assert event.actor_id and event.actor_name=="平台管理员" and event.account=="admin"
    assert event.outcome=="success" and event.status_code==200
    assert event.client_ip=="198.51.100.25"
    row=db.scalar(select(AuditChange).where(AuditChange.event_key==event.event_key))
    assert row.table_name=="operation_audit_probe" and row.operation=="insert"
    assert row.after["password_hash"]=="已隐藏"
    assert row.after["payload"]["wecom_secret"]==row.after["payload"]["api_token"]=="已隐藏"
    assert row.after["payload"]["phone"]=="138****5678"
    assert row.after["payload"]["idcard"]=="440106********1234"
    assert row.after["payload"]["business_code"]=="1794680670709"
    assert "protected-url" not in json.dumps(row.after)
    assert "probe-secret" not in json.dumps(row.after)
    detail=data(client.get(BASE+f"/event:{event.id}",headers=headers))
    assert detail["actor"]=="平台管理员" and detail["changes"][0]["fields"]
    assert client.post(probe,params={"mode":"update","ident":101},headers=headers).status_code==200
    updated=find(db,probe)
    change=db.scalar(select(AuditChange).where(AuditChange.event_key==updated.event_key))
    assert change.before["value"]=="原内容" and change.after["value"]=="已修改"
    assert change.fields==["value"]
    client.post(probe,params={"mode":"delete","ident":101},headers=headers)
    removed=find(db,probe)
    assert db.scalar(select(AuditChange).where(AuditChange.event_key==removed.event_key)).operation=="delete"


def test_failed_and_partially_committed_requests_remain_distinct(client,login,db,probe):
    for mode,ident,count in (("rollback",201,0),("partial",202,1)):
        assert client.post(probe,params={"mode":mode,"ident":ident},headers=login("admin")).status_code==400
        event=find(db,probe)
        assert event.outcome=="failed"
        assert len(list(db.scalars(select(AuditChange).where(AuditChange.event_key==event.event_key))))==count


def test_account_context_does_not_leak_between_parallel_requests(client,login,db,probe):
    staff=[("admin",301),("xiekai",302),("wangting",303)]
    headers={who:login(who) for who,_ in staff}
    with ThreadPoolExecutor(max_workers=3) as pool:
        replies=list(pool.map(lambda pair:client.post(probe,params={"mode":"insert","ident":pair[1]},headers=headers[pair[0]]),staff))
    assert all(r.status_code==200 for r in replies)
    for who,ident in staff:
        change=db.scalar(select(AuditChange).where(AuditChange.table_name=="operation_audit_probe",AuditChange.record_key==json.dumps({"id":ident})))
        event=db.scalar(select(AuditEvent).where(AuditEvent.event_key==change.event_key))
        assert event.account==who
    # A new database transaction has no left-over account context.
    db.execute(text("UPDATE operation_audit_probe SET value='后台变更' WHERE id=301"))
    db.commit()
    anonymous=db.scalar(select(AuditChange).where(AuditChange.table_name=="operation_audit_probe").order_by(AuditChange.id.desc()))
    event=db.scalar(select(AuditEvent).where(AuditEvent.event_key==anonymous.event_key))
    assert event.source=="database" and event.actor_id is None


def test_permissions_historical_evidence_filters_and_export(client,login,db,probe):
    admin,worker=login("admin"),login("xiekai")
    assert client.get(BASE,headers=worker).status_code==403
    assert client.get(BASE+"/options",headers=worker).status_code==403
    assert client.get(BASE+"/export",headers=worker).status_code==403
    assert client.post(BASE+"/ui",json={"kind":"visit","path":"/operations","title":"运营项目"},headers=worker).status_code==200
    assert client.post(BASE+"/ui",json={"kind":"visit","path":"/operations","title":"运营项目","actor_id":1},headers=worker).status_code==400
    attempted=find(db,BASE+"/ui")
    assert attempted.account=="xiekai" and attempted.outcome=="failed"
    options=data(client.get(BASE+"/options",headers=admin))
    assert options["coverage_tables"]>20
    actor=db.scalar(select(SysUser).where(SysUser.wecom_userid=="admin"))
    old=LogOperation(user_id=actor.id,action="legacy.audit.test",target_type="contract",target_id="100",
                     before={"name":"旧名称"},after={"name":"新名称"},created_at=__import__("datetime").datetime.fromisoformat(options["since"])-timedelta(days=1))
    db.add(old);db.commit()
    result=data(client.get(BASE,params={"keyword":"legacy.audit.test","actor":"admin"},headers=admin))
    assert result["total"]==1 and result["items"][0]["key"]==f"legacy:{old.id}"
    details=data(client.get(BASE+f"/legacy:{old.id}",headers=admin))
    assert details["note"] and details["changes"][0]["before"]=={"name":"旧名称"}
    downloaded=client.get(BASE+"/export",params={"keyword":"legacy.audit.test"},headers=admin)
    assert downloaded.status_code==200 and downloaded.content.startswith(b"\xef\xbb\xbf")
    assert "旧名称" not in downloaded.text  # Index export stays compact; details preserve the field changes.
    assert client.get(BASE,params={"start":"not-a-date"},headers=admin).status_code==400
    assert client.get(BASE+f"/legacy:{old.id}",headers=worker).status_code==403


def test_temporary_log_write_failure_recovers_from_private_journal(db,monkeypatch,tmp_path):
    from app.core import operation_audit as audit
    from app.core.config import settings
    original=audit._save
    monkeypatch.setattr(settings,"storage_dir",tmp_path/"files")
    def unavailable(values):
        raise RuntimeError("temporary audit database failure")
    monkeypatch.setattr(audit,"_save",unavailable)
    ctx=audit.Context(module="操作记录",action="补录验证")
    audit.finish(ctx,detail={"access_token":"must-not-appear"})
    file=tmp_path/"audit-spool"/(ctx.key+".json")
    assert file.exists() and file.stat().st_mode & 0o077==0
    assert "must-not-appear" not in file.read_text()
    monkeypatch.setattr(audit,"_save",original)
    assert audit.recover()==1 and not file.exists()
    assert db.scalar(select(AuditEvent).where(AuditEvent.event_key==ctx.key)).action=="补录验证"


def test_record_permission_can_be_granted_and_revoked_independently(client,login,db):
    from copy import deepcopy
    from app.models import SysUserPermission
    admin, worker=login("admin"),login("xiekai")
    user=db.scalar(select(SysUser).where(SysUser.wecom_userid=="xiekai"))
    path=f"/api/v1/admin/users/{user.id}"
    before=data(client.get(path+"/effective-permissions",headers=admin))
    catalog=data(client.get("/api/v1/admin/roles",headers=admin))
    assert catalog["menus"]["operation_records"]=="操作记录"
    assert client.get(BASE,headers=worker).status_code==403
    try:
        grant={"menus":["operation_records"],"buttons":[],"data_scope":"self"}
        changed=data(client.put(path+"/permissions",headers=admin,json={
            "role_codes":before["assigned_roles"],"permissions":grant,"expected_revision":before["permission_revision"]}))
        profile=data(client.get("/api/v1/auth/me",headers=worker))
        assert "operation_records" in profile["menus"] and "admin" not in profile["menus"]
        assert client.get("/api/v1/admin/roles",headers=worker).status_code==403
        result=data(client.get(BASE,headers=worker))
        assert result["items"]
        assert client.get(BASE+"/"+result["items"][0]["key"],headers=worker).status_code==200
        assert client.get(BASE+"/export",params={"actor":"admin"},headers=worker).status_code==200
        revoked=data(client.put(path+"/permissions",headers=admin,json={
            "role_codes":before["assigned_roles"],"permissions":{**grant,"menus":[]},
            "expected_revision":changed["permission_revision"]}))
        assert client.get(BASE,headers=worker).status_code==403
        assert client.get(BASE+"/export",headers=worker).status_code==403
    finally:
        now=data(client.get(path+"/effective-permissions",headers=admin))
        client.put(path+"/permissions",headers=admin,json={"role_codes":before["assigned_roles"],
                   "permissions":before["individual_permissions"],"expected_revision":now["permission_revision"]})

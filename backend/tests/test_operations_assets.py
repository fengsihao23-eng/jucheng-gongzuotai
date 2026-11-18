"""Inventory reconciliation, approved withdrawals and maintenance audience boundaries."""
from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import select, update

from app.core.timeutil import now
from app.integration.wecom.base import ApprovalDto
from app.models import OpsAssetMovement, OpsMaintenanceUpdate, SysUser
from app.services import dict_service, dispatch_service
from tests.conftest import data

BASE = "/api/v1/operations"


@pytest.fixture
def stock_project(client, login, db, monkeypatch):
    monkeypatch.setenv("FAKE_TODAY", "2026-11-18")
    admin = login("admin")
    owner = db.scalar(select(SysUser.id).where(SysUser.wecom_userid == "admin"))
    worker = db.scalar(select(SysUser.id).where(SysUser.wecom_userid == "xiekai"))
    project = data(client.post(BASE, headers=admin, json={"name": "资产测试-" + uuid4().hex[:8], "owner_id": owner,
        "enabled_sections": ["procurement", "warehouse", "scrap", "reports", "device_maintenance", "arrears", "collection_maintenance", "customers"]}))
    path = f"{BASE}/{project['id']}"
    data(client.post(path + "/members", headers=admin, json={"user_id": worker, "role": "member"}))
    product = data(client.post(path + "/products", headers=admin, json={"name": "验收路由器"}))
    school = data(client.post(path + "/schools", headers=admin, json={"name": "验收学校"}))
    equipment = data(client.post(path + f"/schools/{school['id']}/equipment", headers=admin,
        json={"product_id": product["id"], "quantity": 10, "unit_price": "100.00"}))
    return path, project["id"], product["id"], school["id"], equipment["id"], worker


def movement(product, action, quantity, **extra):
    return {"product_id": product, "action": action, "quantity": quantity, "occurred_on": "2026-11-18",
            "note": "合成验收依据", "request_key": uuid4().hex, **extra}


def test_purchase_outbound_scrap_and_duplicate_request_reconcile(client, login, stock_project):
    path, _, product, school, *_ = stock_project
    head = login("admin")
    get = lambda: data(client.get(path + "/warehouse", headers=head))["items"][0]
    assert {k: get()[k] for k in ("total", "warehouse", "operating", "scrapped")} == dict(total=10, warehouse=10, operating=0, scrapped=0)
    body = movement(product, "outbound", 2, school_id=school)
    first = data(client.post(path + "/warehouse/movements", headers=head, json=body))
    assert data(client.post(path + "/warehouse/movements", headers=head, json=body)) == first
    assert get()["warehouse"] == 8 and get()["operating"] == 2
    assert client.post(path + "/warehouse/movements", headers=head, json={**body, "quantity": 1}).status_code == 409
    data(client.post(path + "/warehouse/movements", headers=head, json=movement(product, "scrap_operating", 1)))
    assert get()["warehouse"] == 8 and get()["operating"] == 1 and get()["scrapped"] == 1
    data(client.post(path + "/warehouse/movements", headers=head, json=movement(product, "return", 1)))
    assert get()["warehouse"] == 9 and get()["total"] == get()["warehouse"] + get()["operating"] + get()["scrapped"]


def test_prevent_overdraw_procurement_reduction_and_source_deletion(client, login, stock_project):
    path, _, product, school, equipment, _ = stock_project
    head = login("admin")
    assert client.post(path + "/warehouse/movements", headers=head, json=movement(product, "outbound", 11)).status_code == 409
    assert client.post(path + "/warehouse/movements", headers=head, json=movement(product, "scrap_operating", 1)).status_code == 409
    data(client.post(path + "/warehouse/movements", headers=head, json=movement(product, "outbound", 4)))
    source = path + f"/schools/{school}/equipment/{equipment}"
    assert client.put(source, headers=head, json={"product_id": product, "quantity": 3}).status_code == 409
    data(client.put(source, headers=head, json={"product_id": product, "quantity": 5}))
    row = data(client.get(path + "/warehouse", headers=head))["items"][0]
    assert row["total"] == 5 and row["warehouse"] == 1 and row["operating"] == 4
    data(client.post(path + "/warehouse/movements", headers=head, json=movement(product, "scrap_operating", 1)))
    row = data(client.get(path + "/warehouse", headers=head))["items"][0]
    assert [row[k] for k in ("total", "warehouse", "operating", "scrapped")] == [5, 1, 3, 1]
    assert client.delete(path + f"/schools/{school}", headers=head).status_code == 409
    assert client.post(path + "/warehouse/movements", headers=head, json=movement(product, "inbound", 1, occurred_on="2026-11-19")).status_code == 400


def test_approved_wecom_withdrawal_is_idempotent_and_changed_approval_needs_review(client, login, db, stock_project):
    path, project, product, school, *_ = stock_project
    template, number = "stock-template-" + uuid4().hex, "stock-approval-" + uuid4().hex
    dict_service.put(db, "approval_template", template, {"key": "stock_out", "fields": {
        "运营项目编号": "project_id", "产品编号": "product_id", "数量": "quantity", "学校编号": "school_id"}})
    db.commit()
    approval = ApprovalDto(number, template, "合成出库审批", 1, now(), "xiekai", fields={
        "运营项目编号": str(project), "产品编号": str(product), "数量": "2", "学校编号": str(school)})
    dispatch_service.ingest(db, number, detail=approval)
    assert not db.scalar(select(OpsAssetMovement.id).where(OpsAssetMovement.source_key == number))
    approval.sp_status, approval.approved_time = 2, now()
    assert dispatch_service.ingest(db, number, detail=approval).dispatch_status == 1
    assert dispatch_service.ingest(db, number, detail=approval).dispatch_status == 1
    row = data(client.get(path + "/warehouse", headers=login("admin")))["items"][0]
    assert row["warehouse"] == 8 and row["operating"] == 2
    approval.fields["数量"] = "3"
    assert dispatch_service.ingest(db, number, detail=approval).dispatch_status == 2
    assert data(client.get(path + "/warehouse", headers=login("admin")))["items"][0]["warehouse"] == 8
    assert data(client.get(path + "/warehouse", headers=login("admin")))["approval_connected"]


def test_member_inventory_and_maintenance_permissions_are_enforced(client, login, stock_project):
    path, _, product, _, _, worker = stock_project
    admin, employee = login("admin"), login("xiekai")
    assert client.post(path + "/warehouse/movements", headers=employee, json=movement(product, "outbound", 1)).status_code == 403
    data(client.put(path + f"/member-visibility/{worker}", headers=admin, json={"sections": ["tasks", "reports"], "expected_revision": 0}))
    assert client.get(path + "/warehouse", headers=employee).status_code == 403
    assert client.get(path + "/warehouse", params={"scrap": True}, headers=employee).status_code == 403
    assert client.get(path + "/maintenance", params={"kind": "customers"}, headers=employee).status_code == 403


@pytest.mark.parametrize("kind", ["device_maintenance", "arrears", "collection_maintenance", "customers"])
def test_maintenance_updates_preserve_history_and_detect_stale_edits(client, login, db, stock_project, kind):
    path, _, _, _, _, worker = stock_project
    admin, employee = login("admin"), login("xiekai")
    body = {"kind": kind, "title": "维护验收-" + kind, "owner_id": worker, "customer_name": "合成客户",
            "content": "已完成客户联系", "problems": "待安排", "due_on": "2026-11-19"}
    record = data(client.post(path + "/maintenance", headers=admin, json=body))
    changed = {**body, "status": "processing", "content": "已反馈具体执行情况", "expected_revision": record["revision"]}
    assert client.put(path + f"/maintenance/{record['id']}", headers=employee, json=changed).status_code == 200
    assert client.put(path + f"/maintenance/{record['id']}", headers=employee, json=changed).status_code == 409
    history = data(client.get(path + f"/maintenance/{record['id']}/history", headers=employee))["items"]
    assert len(history) == 2 and history[0]["content"] == "已反馈具体执行情况" and history[1]["problems"] == "待安排"
    assert client.post(path + "/maintenance", headers=employee, json=body).status_code == 403
    db.execute(update(OpsMaintenanceUpdate).where(OpsMaintenanceUpdate.record_id == record["id"]).values(created_at=now()))
    db.commit()
    report = data(client.get(BASE + "/digest", headers=employee, params={"project_id": int(path.rsplit('/', 1)[1]), "period": "2026-W47", "mine": True}))
    assert body["title"] in report["text"] and "已反馈具体执行情况" in report["text"]
    saved = data(client.post(BASE + "/digest/reports", headers=employee, json={"project_id": int(path.rsplit('/', 1)[1]), "period": "2026-W47", "mine": True}))
    data(client.put(path + f"/member-visibility/{worker}", headers=admin, json={"sections": ["tasks", "reports"], "expected_revision": 0}))
    assert client.get(BASE + f"/digest/reports/{saved['id']}", headers=employee).status_code == 403

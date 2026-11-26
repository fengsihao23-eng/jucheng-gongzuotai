"""Deletion password, role isolation, and persisted list and detail behavior."""
from copy import deepcopy
from datetime import date
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import delete, select

from app.core.permissions import RECORD_DELETE_BUTTON, SYSTEM_MANAGEMENT_ROLE
from app.core.security import create_jwt
from app.models import (BizContract, BizPaymentPlan, BusinessRecordDeletion, LogOperation,
                        SysRole, SysUser, SysUserPermission, SysUserRole, SystemDeletePassword)
from app.services import finance_ledger_fields, finance_ledgers
from tests.conftest import data, deletion_body
from tests.test_collection_workbook import collection_source, source_only  # noqa: F401

ADMIN = "/api/v1/admin"
REMOVE = "/api/v1/record-deletions"
COLLECTION = "/api/v1/business-workbook/collections"


def test_individual_grant_enables_deletion_without_creating_write_permissions(client, login, db):
    admin = login("admin")
    owner = data(client.get("/api/v1/auth/me", headers=admin))["id"]
    actor = SysUser(wecom_userid=f"delete-grant-{uuid4().hex[:10]}", name="删除专项验收", workbench_enabled=True)
    db.add(actor)
    db.flush()
    grant = SysUserPermission(user_id=actor.id, menus=["market", "projects", "receivables", "service"],
                              buttons=[], data_scope="all")
    db.add(grant)
    db.commit()
    headers = {"Authorization": f"Bearer {create_jwt(actor.id)[0]}"}
    project_body = {"name": f"专项删除验收-{uuid4().hex[:6]}", "owner_id": owner, "business_line": "TD",
                    "preparation_on": "2026-10-01", "due_on": "2026-10-30", "team_member_ids": [actor.id]}
    project = data(client.post("/api/v1/market/projects", json=project_body, headers=admin))
    path = f"/api/v1/market/projects/{project['project_code']}"

    def can_delete_key_project():
        cards = data(client.get("/api/v1/market/board", headers=headers))["projects"]
        return next(card["can_delete"] for card in cards if card["project_code"] == project["project_code"])

    assert not can_delete_key_project()
    assert client.request("DELETE", path, headers=headers, json=deletion_body()).status_code == 403
    grant.buttons = [RECORD_DELETE_BUTTON]
    db.commit()
    assert can_delete_key_project()
    detail = data(client.get(path, headers=headers))
    assert detail["can_delete"] and not detail["can_manage"]
    assert client.post("/api/v1/market/projects", json=project_body, headers=headers).status_code == 403
    assert client.get(ADMIN + "/deletion-password", headers=headers).status_code == 403
    assert client.request("DELETE", path, headers=headers, json={"password": "wrong"}).status_code == 400

    sporadic = data(client.post("/api/v1/collections/sporadic", headers=admin, json={"name": "回款专项验收", "owner_id": owner}))
    with_grant = data(client.get("/api/v1/collections/sporadic", headers=headers))
    assert with_grant["can_delete"] and not with_grant["can_manage"]
    payment_path = f"{REMOVE}/sporadic-collection/{sporadic['id']}"
    grant.data_scope = "self"
    db.commit()
    assert client.request("DELETE", payment_path, headers=headers, json=deletion_body()).status_code == 404
    grant.data_scope = "all"
    db.commit()
    assert data(client.request("DELETE", path, headers=headers, json=deletion_body()))["deleted"]
    assert data(client.request("DELETE", payment_path, headers=headers, json=deletion_body()))["deleted"]

    from app.core.deps import build_principal
    db.expire_all()
    principal = build_principal(actor)
    assert finance_ledgers.can_remove(principal, "invoice", owner)
    assert not finance_ledger_fields.can_import(principal, "invoice")
    assert not finance_ledgers.can_remove(principal, "cash_journal", owner)


def test_password_rotation_requires_the_configurable_system_management_role(client, login, db):
    admin, finance = login("admin"), login("chenjing")
    endpoint = ADMIN + "/deletion-password"
    initial = deletion_body()["password"]
    actor = SysUser(wecom_userid=f"test-system-manager-{uuid4().hex[:10]}", name="临时系统管理测试", workbench_enabled=True)
    db.add(actor)
    db.commit()
    p = {"Authorization": f"Bearer {create_jwt(actor.id)[0]}"}
    roles_path = f"{ADMIN}/users/{actor.id}/roles"
    restore = db.get(SystemDeletePassword, 1)
    try:
        assert "admin" not in data(client.get("/api/v1/auth/me", headers=p))["menus"]
        assert client.get(endpoint, params={"reveal": True}, headers=finance).status_code == 403
        assert client.put(endpoint, json={"password": "should-not-work", "expected_revision": 0}, headers=finance).status_code == 403

        # The business administrator role alone conveys no system management access.
        data(client.put(roles_path, headers=admin, json={"role_codes": ["ADMIN-01"]}))
        profile = data(client.get("/api/v1/auth/me", headers=p))
        assert profile["is_admin"] and not profile["is_system_manager"] and "admin" not in profile["menus"]
        assert client.get(ADMIN + "/users", headers=p).status_code == 403

        data(client.put(roles_path, headers=admin, json={"role_codes": [SYSTEM_MANAGEMENT_ROLE]}))
        profile = data(client.get("/api/v1/auth/me", headers=p))
        assert profile["is_system_manager"] and "admin" in profile["menus"] and not profile["is_admin"]
        assert client.get(ADMIN + "/users", headers=p).status_code == 200
        assert "password" not in data(client.get(endpoint, headers=p))
        revealed = client.get(endpoint, params={"reveal": True}, headers=p)
        assert revealed.headers["Cache-Control"] == "no-store"
        assert data(revealed)["password"] == initial

        row = data(client.post("/api/v1/collections/sporadic", headers=admin,
                               json={"name": "改密回归测试", "owner_id": data(client.get("/api/v1/auth/me", headers=admin))["id"]}))
        item = f"{REMOVE}/sporadic-collection/{row['id']}"
        changed = data(client.put(endpoint, headers=p, json={"password": "本次测试口令-v2", "expected_revision": 0}))
        assert changed["revision"] == 1 and "password" not in changed
        assert client.put(endpoint, headers=admin, json={"password": "试图覆盖", "expected_revision": 0}).status_code == 409
        assert client.request("DELETE", item, headers=admin, json=deletion_body()).status_code == 400
        assert client.request("DELETE", item, headers=login("xiekai"), json={"password": "本次测试口令-v2"}).status_code in (403, 404)
        assert data(client.request("DELETE", item, headers=admin, json={"password": "本次测试口令-v2"}))["deleted"]
        assert data(client.get(endpoint, headers=admin, params={"reveal": True}))["password"] == "本次测试口令-v2"
        db.expire_all()
        stored = db.get(SystemDeletePassword, 1)
        assert "本次测试口令-v2".encode() not in stored.password_ciphertext
        logs = db.scalars(select(LogOperation).where(LogOperation.action.in_({"deletion.password_update", "deletion.password_reveal"})))
        assert all("本次测试口令" not in str((log.before, log.after)) for log in logs)

        data(client.put(roles_path, headers=admin, json={"role_codes": []}))
        assert client.get(endpoint, headers=p, params={"reveal": True}).status_code == 403
    finally:
        if restore is None:
            db.execute(delete(SystemDeletePassword))
        role_id = db.scalar(select(SysRole.id).where(SysRole.role_code == SYSTEM_MANAGEMENT_ROLE))
        db.execute(delete(SysUserRole).where(SysUserRole.user_id == actor.id, SysUserRole.role_id == role_id))
        db.commit()


def test_delete_hides_ticket_from_list_totals_and_direct_detail_but_keeps_audit(client, login, db):
    admin = login("admin")
    customer = f"删除测试客户-{uuid4().hex[:8]}"
    ticket = data(client.post("/api/v1/service/tickets", headers=admin,
                              json={"customer_name": customer, "problem_desc": "验收用：抽屉显示的记录待删除"}))
    ticket_id = ticket["ticket"]["id"]
    endpoint = f"{REMOVE}/ticket/{ticket_id}"
    assert client.request("DELETE", endpoint, headers=admin).status_code == 400
    assert client.request("DELETE", endpoint, headers=login("chenjing"), json=deletion_body()).status_code == 403
    assert data(client.get("/api/v1/service/tickets", headers=admin, params={"keyword": customer}))["total"] == 1
    assert data(client.request("DELETE", endpoint, headers=admin, json=deletion_body()))["deleted"]
    result = data(client.get("/api/v1/service/tickets", headers=admin, params={"keyword": customer}))
    assert result["items"] == [] and result["total"] == 0
    assert client.get(f"/api/v1/service/tickets/{ticket_id}", headers=admin).status_code == 404
    assert client.request("DELETE", endpoint, headers=admin, json=deletion_body()).status_code == 404
    assert db.get(BusinessRecordDeletion, ("cs_ticket", str(ticket_id))) is not None
    assert db.scalar(select(LogOperation).where(LogOperation.action == "record.delete",
                                                LogOperation.target_type == "cs_ticket",
                                                LogOperation.target_id == str(ticket_id))).after == {"removed": True}


def test_workbook_removals_survive_row_movement_and_keep_source_totals(client, login, db, collection_source):
    wb, _ = collection_source
    admin = login("admin")
    query = {"sheet": "sales-sheet", "revision": wb.revision}
    contract = f"{REMOVE}/workbook-collections/3"
    assert client.request("DELETE", contract, headers=admin, params={**query, "revision": ""},
                          json=deletion_body()).status_code == 409
    current = data(client.get(COLLECTION, headers=admin, params={"basis": "current"}))
    receipt = next(row for row in current["items"] if row["name"] == "本周项目")
    result = client.request("DELETE", f"{REMOVE}/workbook-receipt/{receipt['id']}", headers=admin, params=query, json=deletion_body())
    assert data(result)["deleted"]
    current = data(client.get(COLLECTION, headers=admin, params={"basis": "current"}))
    assert current["total"] == 1 and current["summary"]["received"]["value"] == "70.00"
    assert any(row["name"] == "本周项目" for row in data(client.get(COLLECTION, headers=admin))["items"])

    assert data(client.request("DELETE", contract, headers=admin, params=query, json=deletion_body()))["deleted"]
    assert client.get(COLLECTION + "/items/sales-sheet/3", headers=admin, params={"revision": wb.revision}).status_code == 404
    revised = deepcopy(wb.sheets)
    revised[0]["rows"][2]["row"], revised[0]["rows"][3]["row"] = 4, 3
    revised[0]["rows"].sort(key=lambda row: row["row"])
    wb.revision, wb.sheets = "moved-after-removal", revised
    db.commit()
    result = data(client.get(COLLECTION, headers=admin))
    assert "本周项目" not in {row["name"] for row in result["items"]}
    assert "月内前一周项目" in {row["name"] for row in result["items"]}


def test_removing_manual_payment_rebuilds_contract_plans(client, login, db):
    admin = login("admin")
    profile = data(client.get("/api/v1/auth/me", headers=admin))
    contract = BizContract(contract_no=f"DEL-{uuid4().hex[:12]}", name="删除回款验收", customer_name="验收客户",
                           owner_id=profile["id"], amount=Decimal(1000), business_line="XY", sign_date=date(2026, 10, 1),
                           plans=[BizPaymentPlan(seq=1, stage_name="首期", amount=Decimal(1000))])
    db.add(contract)
    db.commit()
    endpoint = f"/api/v1/contracts/{contract.id}"
    first, second = (data(client.post(endpoint + "/payments", headers=admin, json={"amount": amount, "payment_date": "2026-10-15"}))
                     for amount in ("300.00", "200.00"))
    assert second["detail"]["plans"][0]["received_amount"] == "500.00"
    deleted = client.request("DELETE", f"{REMOVE}/contract-payment/{first['payment_id']}", headers=admin, json=deletion_body())
    assert data(deleted)["deleted"]
    current = data(client.get(endpoint, headers=admin))
    assert current["plans"][0]["received_amount"] == "200.00"
    assert [payment["id"] for payment in current["payments"]] == [second["payment_id"]]


def test_deleting_own_conversation_preserves_its_internal_archive(client, login):
    from app.services import xiaoju_sessions

    user = login("xiekai")
    owner = data(client.get("/api/v1/auth/me", headers=user))["id"]
    endpoint = "/api/v1/assistant/sessions"
    session = data(client.post(endpoint, headers=user, json={}))
    direct = endpoint + "/" + session["id"]
    assert client.request("DELETE", direct, headers=user).status_code == 400
    assert client.request("DELETE", direct, headers=login("admin"), json=deletion_body()).status_code == 404
    assert data(client.request("DELETE", direct, headers=user, json=deletion_body()))["deleted"]
    assert client.get(direct, headers=user).status_code == 404
    assert session["id"] not in {item["id"] for item in data(client.get(endpoint, headers=user))}
    assert (xiaoju_sessions.folder(owner, session["id"]) / "meta.json").is_file()

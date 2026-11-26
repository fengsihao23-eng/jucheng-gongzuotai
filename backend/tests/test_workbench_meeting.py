from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.models import BizContract, BizPayment, BizPaymentPlan, OpsIncome, SysNotify, SysUser
from conftest import data

API = "/api/v1"


def uid(db, name):
    return db.scalar(select(SysUser.id).where(SysUser.wecom_userid == name))


def test_role_named_members_and_effective_permissions(client, login, db):
    admin, employee = login("admin"), login("yangyue")
    roles = data(client.get(API + "/admin/roles", headers=admin))["roles"]
    match = next(row for row in roles if row["code"] == "ADMIN-01")
    url = API + "/admin/roles/ADMIN-01/members"
    members = data(client.get(url, headers=admin))
    assert members["total"] == match["users"]
    assert any(x["userid"] == "admin" for x in members["items"])
    assert client.get(url, headers=employee).status_code == 403
    grants = data(client.get(API + f"/admin/users/{uid(db, 'admin')}/effective-permissions", headers=admin))
    assert grants["operations_all"]
    assert grants["operations_archive"] and grants["last_login"]["at"]


def test_private_task_access_summary_archive_and_assignment(client, login, db):
    admin, assignee, outsider = login("admin"), login("xuhao"), login("yangyue")
    p = data(client.post(API + "/operations", json={"name": "可见范围验收", "owner_id": uid(db, "admin")}, headers=admin))
    base = API + f"/operations/{p['id']}"
    owner_id, outsider_id = uid(db, "xuhao"), uid(db, "yangyue")
    data(client.post(base + "/members", json={"user_id": owner_id, "role": "co_owner"}, headers=admin))
    batch = data(client.post(base + "/members/batch", json={"user_ids": [owner_id, outsider_id], "role": "viewer"}, headers=admin))
    assert batch["added"] == batch["already_member"] == 1
    t = data(client.post(base + "/tasks", headers=admin, json={"title": "指定成员事项",
        "due_date": "2026-10-20", "owner_id": owner_id, "visibility": "selected", "viewer_ids": []}))
    path = API + f"/operations/tasks/{t['id']}"
    assert client.get(path, headers=outsider).status_code == 404
    assert client.get(path, headers=assignee).status_code == 200
    assert data(client.get(base, headers=outsider))["tasks"] == []
    assert "指定成员事项" not in data(client.get(base + "/brief", headers=outsider))["text"]
    digest = API + "/operations/digest"
    assert not data(client.get(digest, params={"project_id": p["id"], "period": "2026-W43"}, headers=outsider))["items"]
    assert data(client.get(digest, params={"project_id": p["id"], "period": "2026-W43"}, headers=assignee))["items"][0]["id"] == t["id"]
    notice = db.scalar(select(SysNotify).where(SysNotify.ref_type == "ops_task", SysNotify.ref_id == str(t["id"]),
                                            SysNotify.channel == "wecom_app"))
    assert notice.user_id == owner_id and notice.send_status == 1 and t["title"] not in notice.content
    assert client.post(path + "/archive", json={"reason": "试用数据"}, headers=assignee).status_code == 403
    data(client.post(path + "/archive", json={"reason": "试用数据"}, headers=admin))
    assert client.get(path, headers=admin).status_code == 404
    assert data(client.get(path, params={"archived": True}, headers=admin))["updates"]
    data(client.post(path + "/restore", headers=admin))
    assert data(client.get(path, headers=assignee))["id"] == t["id"]
    data(client.post(path + "/feedback", headers=assignee, json={"content": "已核对需求", "status": "done"}))
    row = data(client.get(digest, params={"project_id": p["id"], "period": "2026-W43"}, headers=assignee))["items"][0]
    assert row["done"] and row["feedback"] == "已核对需求"



def test_income_file_preview_confirm_duplicate_and_conflict(client, login, db):
    admin = login("admin")
    p = data(client.post(API + "/operations", headers=admin, json={"name": "收入样例", "owner_id": uid(db, "admin")}))
    base = API + f"/operations/{p['id']}"
    school = data(client.post(base + "/schools", headers=admin, json={"name": "甲校"}))
    raw = "学校,实际收款日期,收入金额,备注\n甲校,2026-10-19,18.25,试点\n".encode()
    files = {"file": ("学校实收.csv", raw, "text/csv")}
    r = data(client.post(base + "/income-file/preview", headers=admin, files=files))
    assert r["new_rows"] == 1 and r["new_amount"] == "18.25"
    assert not db.scalar(select(OpsIncome.id).where(OpsIncome.school_id == school["id"]))
    fields = {"expected_sha": r["sha256"], "expected_rows": r["new_rows"], "expected_amount": r["new_amount"]}
    assert data(client.post(base + "/income-file/confirm", headers=admin, files=files, data=fields))["school_days_created"] == 1
    assert data(client.post(base + "/income-file/confirm", headers=admin, files=files, data=fields))["already_imported"]
    changed = {"file": ("学校实收.csv", raw.replace(b"18.25", b"20.00"), "text/csv")}
    assert data(client.post(base + "/income-file/preview", headers=admin, files=changed))["error_count"] == 1


def test_signing_drilldown_and_year_split_followup(client, login, db):
    admin, owner = login("admin"), login("songjia")
    c = BizContract(contract_no="TEST-WORKBENCH", name="回款试点", customer_name="样例单位", sign_date=date(2026, 10, 20),
                    amount=Decimal("100"), business_line="XY", owner_id=uid(db, "songjia"), status=1)
    db.add(c)
    db.flush()
    old = BizPaymentPlan(contract_id=c.id, seq=1, stage_name="第一期", due_date=date(2025, 12, 30), amount=Decimal("50"))
    recent = BizPaymentPlan(contract_id=c.id, seq=2, stage_name="第二期", due_date=date(2026, 11, 10), amount=Decimal("50"))
    db.add_all([old, recent, BizPayment(contract_id=c.id, amount=Decimal("20"), payment_date=date(2026, 10, 19))])
    db.commit()
    args = {"keyword": "TEST-WORKBENCH"}
    for group in data(client.get(API + "/contracts/signing", params=args, headers=owner))["items"]:
        listing = data(client.get(API + "/contracts", params={**args, "sign_period": group["key"]}, headers=owner))
        assert group["amount"] == listing["sums"]["amount"] and listing["total"] == group["count"] == 1
    rollup = data(client.get(API + "/collections", params=args, headers=admin))
    totals = {b["key"]: Decimal(b["amount"]) for b in rollup["totals"]}
    assert totals["all"] == 80 and totals["before"] == 30 and totals["current"] == 50
    route = API + f"/collections/{c.id}/{recent.id}"
    data(client.post(route, headers=admin, json={"kind": "request", "recipient_id": c.owner_id, "content": "请反馈预计日期"}))
    result = data(client.post(route, headers=owner, json={"kind": "reply", "content": "已经联系客户", "expected_on": "2026-11-07"}))
    assert result["replied_this_week"] and not result["pending_reply"] and result["expected_on"] == "2026-11-07"
    assert Decimal(data(client.get(API + "/collections", params=args, headers=admin))["totals"][0]["amount"]) == 80

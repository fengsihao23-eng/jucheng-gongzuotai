"""Project feature selections complement existing member permissions."""
from uuid import uuid4

from sqlalchemy import select

from app.core.permission_catalog import OPERATION_SECTIONS
from app.models import LogOperation, OpsProduct, OpsProjectFeatures, SysUser
from tests.conftest import data, deletion_body

BASE = "/api/v1/operations"


def user(db, account):
    return db.scalar(select(SysUser.id).where(SysUser.wecom_userid == account))


def project_body(db, **extra):
    return {"name": "标准功能核验-" + uuid4().hex[:10], "category": "心理服务",
            "owner_id": user(db, "admin"), **extra}


def test_new_projects_have_separate_features_and_disabled_endpoints(client, login, db):
    head = login("admin")
    catalog = data(client.get(BASE + "/options", headers=head))
    sections = [r["value"] for r in catalog["features"]]
    assert sections == list(OPERATION_SECTIONS)
    assert all(not r["required"] and not r["depends_on"] for r in catalog["features"])
    permissions = data(client.get("/api/v1/admin/roles", headers=head))["permission_groups"]
    assert next(r["sections"] for r in permissions if r["key"] == "operations") == OPERATION_SECTIONS
    full = data(client.post(BASE, headers=head, json=project_body(db, enabled_sections=sections)))
    assert full["feature_settings"]["sections"] == sections
    simple = data(client.post(BASE, headers=head, json=project_body(db, enabled_sections=["progress", "reports"])))
    purchase = data(client.post(BASE, headers=head, json=project_body(db, enabled_sections=["procurement"])))
    assert simple["feature_settings"]["configured"]
    assert simple["feature_settings"]["revision"] == 1
    assert set(simple["feature_settings"]["sections"]) == {"progress", "reports"}
    assert purchase["feature_settings"]["sections"] == ["procurement"]
    for row, progress_status in ((simple, 200), (purchase, 403)):
        path = f"{BASE}/{row['id']}"
        info = data(client.get(path, headers=head))
        assert set(info["visible_sections"]) == set(row["feature_settings"]["sections"])
        assert client.get(path + "/progress", headers=head).status_code == progress_status
        assert client.get(path + "/income-board", headers=head).status_code == 403
        assert client.get(path + "/controls", headers=head).status_code == 403
        assert client.post(path + "/progress", headers=head, json={"progress": "录入进度"}).status_code == progress_status
    assert client.post(f"{BASE}/{purchase['id']}/schools", headers=head, json={"name": "采购学校"}).status_code == 200
    assert client.post(f"{BASE}/{simple['id']}/schools", headers=head, json={"name": "未开通"}).status_code == 403


def test_toggle_hides_existing_data_and_preserves_member_restrictions(client, login, db):
    admin, worker = login("admin"), login("xiekai")
    body = project_body(db, enabled_sections=["tasks", "team", "products", "reports", "settings"])
    project = data(client.post(BASE, headers=admin, json=body))
    proj = project["id"]
    path = f"{BASE}/{proj}"
    employee = user(db, "xiekai")
    assert client.post(path + "/members", headers=admin, json={"user_id": employee, "role": "member"}).status_code == 200
    assert client.put(path + f"/member-visibility/{employee}", headers=admin,
                      json={"sections": ["tasks", "reports"], "expected_revision": 0}).status_code == 200
    product = data(client.post(path + "/products", headers=admin, json={"name": "留存设备"}))
    task = data(client.post(path + "/tasks", headers=admin, json={
        "title": "留存任务", "owner_id": body["owner_id"], "due_date": "2026-10-30"}))
    assert client.get(path + "/controls", headers=admin).status_code == 200
    assert client.put(path, headers=worker, json={**body, "expected_features_revision": 1}).status_code == 403
    disabled = data(client.put(path, headers=admin, json={**body, "enabled_sections": ["team", "reports"], "expected_features_revision": 1}))
    assert disabled["feature_settings"]["revision"] == 2
    assert not disabled["products"] and db.get(OpsProduct, product["id"]).name == "留存设备"
    assert not disabled["tasks"] and disabled["can_configure"]
    assert client.get(f"{BASE}/tasks/{task['id']}", headers=admin).status_code == 404
    assert client.get(path + "/controls", headers=admin).status_code == 403
    assert client.put(path + "/controls", headers=admin, json={"expected_revision": 0}).status_code == 403
    assert client.post(path + "/products", headers=admin, json={"name": "不能登记"}).status_code == 403
    view = data(client.get(path + "/member-visibility", headers=admin))
    assert "products" not in view["sections"]
    assert client.put(path, headers=admin, json={**body, "expected_features_revision": 1}).status_code == 409
    restored = data(client.put(path, headers=admin, json={**body, "expected_features_revision": 2}))
    assert restored["products"][0]["name"] == "留存设备"
    assert restored["tasks"][0]["title"] == "留存任务"
    assert "products" not in data(client.get(path, headers=worker))["visible_sections"]
    log = db.scalar(select(LogOperation).where(LogOperation.action == "operations.project.features",
        LogOperation.target_id == str(proj)).order_by(LogOperation.id.desc()).limit(1))
    assert "products" not in log.before["sections"] and "products" in log.after["sections"]


def test_legacy_and_ordinary_edits_do_not_overwrite_features(client, login, db):
    head = login("admin")
    legacy_body = project_body(db, name="智慧校园", category="智慧校园")
    legacy = data(client.post(BASE, headers=head, json=legacy_body))
    path = f"{BASE}/{legacy['id']}"
    assert not legacy["feature_settings"]["configured"] and legacy["feature_settings"]["legacy_campus_income"]
    edited = data(client.put(path, headers=head, json={**legacy_body, "objective": "仅改目标"}))
    assert not edited["feature_settings"]["configured"]
    assert db.get(OpsProjectFeatures, legacy["id"]) is None
    selected = data(client.put(path, headers=head, json={**legacy_body, "enabled_sections": ["income"], "expected_features_revision": 0}))
    assert selected["feature_settings"]["legacy_campus_income"]
    assert selected["visible_sections"] == ["income"]
    assert data(client.put(path, headers=head, json=legacy_body))["feature_settings"] == selected["feature_settings"]
    new = data(client.post(BASE, headers=head, json={**legacy_body, "enabled_sections": ["income"]}))
    assert new["feature_settings"]["configured"] and not new["feature_settings"]["legacy_campus_income"]
    assert client.post(BASE, headers=head, json={**legacy_body, "enabled_sections": ["unknown"]}).status_code == 400


def test_competitors_are_project_local_and_writable_by_execution_members(client, login, db):
    admin, member, viewer, outsider = (login(account) for account in ("admin", "xiekai", "yangyue", "wujun"))
    project = data(client.post(BASE, headers=admin, json=project_body(db, enabled_sections=["progress", "team", "competitors"])))
    path = f"{BASE}/{project['id']}"
    member_id = user(db, "xiekai")
    for ident, role in [(member_id, "member"), (user(db, "yangyue"), "viewer")]:
        data(client.post(path + "/members", headers=admin, json={"user_id": ident, "role": role}))
    assert "competitors" in data(client.get(path, headers=member))["visible_sections"]
    url = path + "/competitors"
    payload = {"name": "验收对手", "details": "对方计划提交实施方案", "response": "跟进业主的评审安排"}
    row = data(client.post(url, headers=member, json=payload))
    assert row["revision"] == 1 and row["updated_by"] == member_id
    assert data(client.get(url, headers=viewer)) == {"items": [row], "can_edit": False}
    assert client.post(url, headers=viewer, json=payload).status_code == 403
    assert client.get(url, headers=outsider).status_code == 404
    result = data(client.put(f"{url}/{row['id']}", headers=admin, json={
        **payload, "response": "本周准备对比材料", "expected_revision": 1}))
    assert result["revision"] == 2 and result["response"] == "本周准备对比材料"
    assert client.put(f"{url}/{row['id']}", headers=member, json={**payload, "expected_revision": 1}).status_code == 409

    second = data(client.post(BASE, headers=admin, json=project_body(db)))
    second_url = f"{BASE}/{second['id']}/competitors"
    assert data(client.get(second_url, headers=admin))["items"] == []
    assert client.put(f"{second_url}/{row['id']}", headers=admin, json={**payload, "expected_revision": 2}).status_code == 404
    data(client.put(path + f"/member-visibility/{member_id}", headers=admin,
                    json={"sections": ["tasks"], "expected_revision": 0}))
    assert client.get(url, headers=member).status_code == 403


def test_user_list_categories_project_and_personal_access(client, login, db):
    admin, member, outsider = (login(name) for name in ("admin", "xiekai", "wujun"))
    member_id = user(db, "xiekai")
    body = project_body(db, enabled_sections=["team", "user_list"])
    project = data(client.post(BASE, headers=admin, json=body))
    path = f"{BASE}/{project['id']}"
    url = path + "/user-list"
    data(client.post(path + "/members", headers=admin, json={"user_id": member_id, "role": "member"}))
    education = {"category": "education", "name": "验收教育单位", "contact": "示例联系人"}
    other = {"category": "other", "name": "验收其他单位", "note": "示例备注"}
    rows = [data(client.post(url, headers=admin, json=record)) for record in (education, other)]
    for row in rows:
        assert data(client.get(url, headers=member, params={"category": row["category"]})) == {"items": [row], "can_edit": False}
    assert client.post(url, headers=member, json=education).status_code == 403
    assert client.get(url, headers=outsider, params={"category": "other"}).status_code == 404
    assert client.post(url, headers=admin, json={**other, "category": "unknown"}).status_code == 400
    second = data(client.post(BASE, headers=admin, json=project_body(db, enabled_sections=["user_list"])))
    second_url = f"{BASE}/{second['id']}/user-list"
    edit = {**education, "category": "other", "expected_revision": 1}
    row_url = f"{url}/{rows[0]['id']}"
    assert client.put(f"{second_url}/{rows[0]['id']}", headers=admin, json=edit).status_code == 404
    assert data(client.put(row_url, headers=admin, json=edit))["revision"] == 2
    assert client.put(row_url, headers=admin, json=edit).status_code == 409
    assert data(client.get(url, headers=admin, params={"category": "education"}))["items"] == []

    profile = f"/api/v1/admin/users/{member_id}"
    old = data(client.get(profile + "/effective-permissions", headers=admin))
    permissions = {"menus": ["operations"], "buttons": [], "data_scope": "self"}
    try:
        settings = {"role_codes": old["assigned_roles"], "workbench_enabled": True}
        saved = data(client.put(profile + "/permissions", headers=admin, json={**settings,
            "permissions": {**permissions, "sections": {"operations": []}}, "expected_revision": old["permission_revision"]}))
        assert client.get(url, headers=member, params={"category": "other"}).status_code == 403
        data(client.put(profile + "/permissions", headers=admin, json={**settings,
            "permissions": {**permissions, "sections": {"operations": ["user_list"]}}, "expected_revision": saved["permission_revision"]}))
        assert len(data(client.get(url, headers=member, params={"category": "other"}))["items"]) == 2
    finally:
        current = data(client.get(profile + "/effective-permissions", headers=admin))
        data(client.put(profile + "/permissions", headers=admin, json={
            "role_codes": old["assigned_roles"], "workbench_enabled": old["workbench_enabled"],
            "permissions": old["individual_permissions"], "expected_revision": current["permission_revision"]}))

    data(client.put(path, headers=admin, json={**body, "enabled_sections": [], "expected_features_revision": 1}))
    assert client.get(url, headers=admin, params={"category": "other"}).status_code == 403
    data(client.put(path, headers=admin, json={**body, "expected_features_revision": 2}))
    assert len(data(client.get(url, headers=admin, params={"category": "other"}))["items"]) == 2
    data(client.request("DELETE", f"/api/v1/record-deletions/operation-user/{rows[0]['id']}", headers=admin, json=deletion_body()))
    assert data(client.get(url, headers=admin, params={"category": "other"}))["items"] == [rows[1]]

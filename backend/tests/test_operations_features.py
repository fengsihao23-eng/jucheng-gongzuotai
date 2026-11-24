"""Project feature selections complement existing member permissions."""
from uuid import uuid4

from sqlalchemy import select

from app.models import LogOperation, OpsProduct, OpsProjectFeatures, SysUser
from tests.conftest import data

BASE = "/api/v1/operations"


def user(db, account):
    return db.scalar(select(SysUser.id).where(SysUser.wecom_userid == account))


def project_body(db, **extra):
    return {"name": "标准功能核验-" + uuid4().hex[:10], "category": "心理服务",
            "owner_id": user(db, "admin"), **extra}


def test_new_projects_have_separate_features_and_disabled_endpoints(client, login, db):
    head = login("admin")
    catalog = data(client.get(BASE + "/options", headers=head))
    assert {r["value"] for r in catalog["features"] if r["required"]} == {"tasks", "team", "competitors"}
    simple = data(client.post(BASE, headers=head, json=project_body(db, enabled_sections=["progress", "reports"])))
    purchase = data(client.post(BASE, headers=head, json=project_body(db, enabled_sections=["procurement"])))
    assert simple["feature_settings"]["configured"]
    assert simple["feature_settings"]["revision"] == 1
    assert set(simple["feature_settings"]["sections"]) == {"tasks", "team", "competitors", "progress", "reports"}
    assert set(purchase["feature_settings"]["sections"]) == {"tasks", "team", "competitors", "procurement", "products"}
    for row, key, progress_status in ((simple, "income", 200), (purchase, "income", 403)):
        path = f"{BASE}/{row['id']}"
        info = data(client.get(path, headers=head))
        assert set(info["visible_sections"]) == set(row["feature_settings"]["sections"])
        assert client.get(path + "/progress", headers=head).status_code == progress_status
        assert client.get(path + "/income-board", headers=head).status_code == 403
        assert client.post(path + "/progress", headers=head, json={"progress": "录入进度"}).status_code == progress_status
    assert client.post(f"{BASE}/{purchase['id']}/schools", headers=head, json={"name": "采购学校"}).status_code == 200
    assert client.post(f"{BASE}/{simple['id']}/schools", headers=head, json={"name": "未开通"}).status_code == 403


def test_toggle_hides_existing_data_and_preserves_member_restrictions(client, login, db):
    admin, worker = login("admin"), login("xiekai")
    body = project_body(db, enabled_sections=["products", "reports"])
    project = data(client.post(BASE, headers=admin, json=body))
    proj = project["id"]
    path = f"{BASE}/{proj}"
    employee = user(db, "xiekai")
    assert client.post(path + "/members", headers=admin, json={"user_id": employee, "role": "member"}).status_code == 200
    assert client.put(path + f"/member-visibility/{employee}", headers=admin,
                      json={"sections": ["tasks", "reports"], "expected_revision": 0}).status_code == 200
    product = data(client.post(path + "/products", headers=admin, json={"name": "留存设备"}))
    assert client.put(path, headers=worker, json={**body, "expected_features_revision": 1}).status_code == 403
    disabled = data(client.put(path, headers=admin, json={**body, "enabled_sections": ["reports"], "expected_features_revision": 1}))
    assert disabled["feature_settings"]["revision"] == 2
    assert not disabled["products"] and db.get(OpsProduct, product["id"]).name == "留存设备"
    assert client.post(path + "/products", headers=admin, json={"name": "不能登记"}).status_code == 403
    view = data(client.get(path + "/member-visibility", headers=admin))
    assert "products" not in view["sections"]
    assert client.put(path, headers=admin, json={**body, "expected_features_revision": 1}).status_code == 409
    restored = data(client.put(path, headers=admin, json={**body, "expected_features_revision": 2}))
    assert restored["products"][0]["name"] == "留存设备"
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
    assert set(selected["visible_sections"]) == {"tasks", "team", "competitors", "income"}
    assert data(client.put(path, headers=head, json=legacy_body))["feature_settings"] == selected["feature_settings"]
    new = data(client.post(BASE, headers=head, json={**legacy_body, "enabled_sections": ["income"]}))
    assert new["feature_settings"]["configured"] and not new["feature_settings"]["legacy_campus_income"]
    assert client.post(BASE, headers=head, json={**legacy_body, "enabled_sections": ["unknown"]}).status_code == 400


def test_competitors_are_project_local_and_writable_by_execution_members(client, login, db):
    admin, member, viewer, outsider = (login(account) for account in ("admin", "xiekai", "yangyue", "wujun"))
    project = data(client.post(BASE, headers=admin, json=project_body(db, enabled_sections=["progress"])))
    path = f"{BASE}/{project['id']}"
    features = db.get(OpsProjectFeatures, project["id"])
    features.sections = [section for section in features.sections if section != "competitors"]
    db.commit()
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

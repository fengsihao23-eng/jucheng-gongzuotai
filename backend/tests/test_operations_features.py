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
    assert {r["value"] for r in catalog["features"] if r["required"]} == {"tasks", "team"}
    simple = data(client.post(BASE, headers=head, json=project_body(db, enabled_sections=["progress", "reports"])))
    purchase = data(client.post(BASE, headers=head, json=project_body(db, enabled_sections=["procurement"])))
    assert simple["feature_settings"]["configured"]
    assert simple["feature_settings"]["revision"] == 1
    assert set(simple["feature_settings"]["sections"]) == {"tasks", "team", "progress", "reports"}
    assert set(purchase["feature_settings"]["sections"]) == {"tasks", "team", "procurement", "products"}
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
    assert set(selected["visible_sections"]) == {"tasks", "team", "income"}
    assert data(client.put(path, headers=head, json=legacy_body))["feature_settings"] == selected["feature_settings"]
    new = data(client.post(BASE, headers=head, json={**legacy_body, "enabled_sections": ["income"]}))
    assert new["feature_settings"]["configured"] and not new["feature_settings"]["legacy_campus_income"]
    assert client.post(BASE, headers=head, json={**legacy_body, "enabled_sections": ["unknown"]}).status_code == 400

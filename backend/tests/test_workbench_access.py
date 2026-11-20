"""点名重置、旧会话拦截、真实子项权限及项目隔离的回归。"""
import uuid
from datetime import date

import pytest
from sqlalchemy import func, select

from app.core.deps import build_principal
from app.core.response import BizError
from app.core.security import hash_token
from app.models import OpsMember, OpsProject, OpsTask, SysUser
from app.services import operations_service as ops, workbench_access
from tests.conftest import data

BASE = "/api/v1/operations"


def user(db, userid):
    return db.scalar(select(SysUser).where(SysUser.wecom_userid == userid))


@pytest.fixture
def own_project(client, login, db):
    return data(client.post(BASE, headers=login("admin"), json={
        "name": "权限隔离验收-" + uuid.uuid4().hex[:8], "owner_id": user(db, "wujun").id,
        "enabled_sections": ["tasks", "team", "progress", "reports", "products", "income", "outreach",
            "procurement", "warehouse", "device_maintenance", "arrears", "collection_maintenance", "customers", "scrap", "funds"]}))


def test_revoked_existing_session_and_mcp_token_cannot_use_any_module(client, login, db):
    from app.mcp_server.server import _lookup_user
    headers = login("xiekai")
    target = user(db, "xiekai")
    before = target.workbench_enabled, target.access_token, target.access_token_hint
    raw = "jcmcp_local-access-test"
    try:
        target.workbench_enabled = False
        target.access_token = hash_token(raw)
        db.commit()
        profile = data(client.get("/api/v1/auth/me", headers=headers))
        assert profile["workbench_enabled"] is False and profile["menus"] == [] and profile["buttons"] == []
        assert _lookup_user(raw) is None
        from app.services.wecom_inbox_service import _bound_user
        from app.services.xiaoju_business import principal as business_principal
        from app.services.xiaoju_tools import XiaoJuTools
        assert _bound_user(db, target.wecom_userid) is None
        with pytest.raises(BizError):
            business_principal(target.id)
        assert not XiaoJuTools(target.id).execute("inspect_environment", {})["ok"]
        for endpoint in [BASE, "/api/v1/portal/work", "/api/v1/assistant/sessions",
                         "/api/v1/notifications", "/api/v1/kb/categories"]:
            assert client.get(endpoint, headers=headers).status_code == 403, endpoint
        authenticated = data(client.post("/api/v1/auth/login", json={"username": target.wecom_userid, "password": "Jc@2026"}))
        assert authenticated["profile"]["workbench_enabled"] is False
        db.expire_all()
        assert user(db, "xiekai").workbench_enabled is False
    finally:
        target.workbench_enabled, target.access_token, target.access_token_hint = before
        db.commit()


def test_production_new_accounts_default_to_no_workbench_access(db, monkeypatch):
    from app.core.config import settings
    from app.services.user_service import ensure_default_role
    monkeypatch.setattr(settings, "environment", "prod")
    target = SysUser(wecom_userid="new-disabled-" + uuid.uuid4().hex[:8], name="新入职等待授权")
    db.add(target)
    db.flush()
    assert not target.workbench_enabled
    ensure_default_role(db, target)
    assert not target.roles
    principal = build_principal(target)
    assert not principal.menus and not principal.buttons and not principal.is_admin
    db.rollback()


def test_personal_sections_restrict_even_project_manager_and_refresh_current_session(client, login, db, own_project):
    admin, manager = login("admin"), login("wujun")
    path = f"/api/v1/admin/users/{user(db, 'wujun').id}"
    original = data(client.get(path + "/effective-permissions", headers=admin))
    project_path = BASE + "/" + str(own_project["id"])
    try:
        saved = data(client.put(path + "/permissions", headers=admin, json={
            "role_codes": original["assigned_roles"], "workbench_enabled": True,
            "permissions": {"menus": ["portal", "operations"], "buttons": ["operations:manage"],
                            "data_scope": "all", "sections": {"operations": ["tasks"]}},
            "expected_revision": original["permission_revision"]}))
        profile = data(client.get("/api/v1/auth/me", headers=manager))
        assert profile["sections"] == saved["sections"] == {"operations": ["tasks"]}
        detail = data(client.get(project_path, headers=manager))
        assert detail["visible_sections"] == ["tasks"] and not detail["can_configure"]
        assert detail["members"] == detail["products"] == detail["schools"] == []
        assert detail["financials"]["total_income"] is None
        denied = ["/progress", "/income-board", "/warehouse", "/warehouse?scrap=true",
                  "/maintenance?kind=customers", "/member-visibility", "/controls", "/funds", "/digest-settings"]
        for suffix in denied:
            assert client.get(project_path + suffix, headers=manager).status_code == 403, suffix
        task = data(client.post(project_path + "/tasks", headers=manager, json={
            "title": "仍可执行的已授权小项", "owner_id": user(db, "wujun").id, "due_date": "2026-10-30"}))
        data(client.put(path + "/permissions", headers=admin, json={
            "role_codes": original["assigned_roles"], "permissions": {"menus": ["portal", "operations"],
                "buttons": [], "data_scope": "self", "sections": {"operations": ["products"]}},
            "expected_revision": saved["permission_revision"]}))
        assert not data(client.get(project_path, headers=manager))["tasks"]
        assert client.get(f"{BASE}/tasks/{task['id']}", headers=manager).status_code == 404
        assert client.post(project_path + "/tasks", headers=manager, json={
            "title": "禁止绕过小项创建", "owner_id": user(db, "wujun").id, "due_date": "2026-10-30"}).status_code == 403
    finally:
        current = data(client.get(path + "/effective-permissions", headers=admin))
        data(client.put(path + "/permissions", headers=admin, json={
            "role_codes": original["assigned_roles"], "permissions": original["individual_permissions"],
            "workbench_enabled": original["workbench_enabled"], "expected_revision": current["permission_revision"]}))


def test_global_roles_buttons_scope_and_reviewer_assignment_do_not_bypass_membership(db, own_project):
    outsider = user(db, "zhangzhenhua")
    principal = build_principal(outsider)
    principal.buttons.update({"operations:manage", "operations:view_all", "operations:approve"})
    assert principal.data_scope == "all"
    assert not ops.can_view_all(principal)
    project = db.get(OpsProject, own_project["id"])
    assert ops.role(db, principal, project) is None
    task = OpsTask(project_id=project.id, owner_id=project.owner_id, creator_id=project.owner_id,
                   company_reviewer_id=outsider.id, title="仅指定审核人也不能越过团队",
                   due_date=date(2026, 10, 30), plan_month="2026-10")
    db.add(task)
    db.flush()
    assert project.id not in set(db.scalars(ops.visible_projects(principal).with_only_columns(OpsProject.id)))
    with pytest.raises(BizError):
        ops.get_project(db, principal, project.id)
    with pytest.raises(BizError):
        ops.get_task(db, principal, task.id)
    member = OpsMember(project_id=project.id, user_id=outsider.id, role="member")
    db.add(member)
    db.flush()
    assert ops.get_task(db, principal, task.id).id == task.id
    db.delete(member)
    db.flush()
    with pytest.raises(BizError):
        ops.get_task(db, principal, task.id)
    db.rollback()


@pytest.mark.parametrize("userid,name", [("ken", "梁道灿"), ("FengSiHao", "冯思皓"), ("SZGLY", "聚成数智管理员")])
def test_only_verified_superusers_cross_projects(db, own_project, userid, name):
    target = user(db, userid)
    if target is None:
        target = SysUser(wecom_userid=userid, name=name, workbench_enabled=True)
        db.add(target)
        db.flush()
    principal = build_principal(target)
    assert ops.can_view_all(principal)
    assert ops.get_project(db, principal, own_project["id"]).id == own_project["id"]
    # 改成相同显示名不会获得三位最高权限人员身份。
    other = user(db, "chenjing")
    other.name = name
    assert not ops.can_view_all(build_principal(other))
    db.rollback()


def test_task_delete_requires_password_and_does_not_delete_history(client, login, db, own_project):
    task = data(client.post(f"{BASE}/{own_project['id']}/tasks", headers=login("admin"), json={
        "title": "删除密码校验", "owner_id": own_project["owner_id"], "due_date": "2026-10-30"}))
    endpoint = f"{BASE}/tasks/{task['id']}"
    assert client.delete(endpoint, headers=login("admin")).status_code == 400
    for password in ["wrong", ""]:
        assert client.request("DELETE", endpoint, headers=login("admin"), json={"password": password}).status_code == 400
        assert client.get(endpoint, headers=login("admin")).status_code == 200
    assert client.request("DELETE", endpoint, headers=login("chenjing"), json={"password": "admin123"}).status_code in {403, 404}
    data(client.request("DELETE", endpoint, headers=login("admin"), json={"password": "admin123"}))
    assert client.get(endpoint, headers=login("admin")).status_code == 404
    assert data(client.get(endpoint, headers=login("admin"), params={"archived": True}))["archived_at"]
    assert db.get(OpsTask, task["id"]) is not None


def test_reset_clears_all_grants_preserves_retained_and_cannot_repeat(db, monkeypatch):
    retained = user(db, "wujun")
    actor = user(db, "SZGLY") or SysUser(wecom_userid="SZGLY", name="聚成数智管理员")
    actor.status, actor.is_deleted, actor.workbench_enabled = 1, False, True
    db.add(actor)
    db.flush()
    monkeypatch.setattr(workbench_access, "RETAINED_ACCOUNTS", {"SZGLY": {actor.name}, "wujun": {retained.name}})
    before = sorted(role.role_code for role in retained.roles), retained.workbench_enabled, retained.access_token
    project_count = db.scalar(select(func.count()).select_from(OpsProject))
    plan = workbench_access.preview(db)
    workbench_access.apply(db, plan["revision"])
    db.expire_all()
    retained = db.get(SysUser, retained.id)
    assert (sorted(role.role_code for role in retained.roles), retained.workbench_enabled, retained.access_token) == before
    revoked = user(db, "chenjing")
    assert revoked.workbench_enabled is False and not revoked.roles and not revoked.access_token
    assert revoked.permission_override.menus == revoked.permission_override.buttons == []
    assert not build_principal(revoked).menus
    assert db.scalar(select(func.count()).select_from(OpsProject)) == project_count
    with pytest.raises(BizError, match="已经执行"):
        workbench_access.apply(db, plan["revision"])
    db.rollback()


def test_invalid_child_permissions_are_rejected(client, login, db):
    path = f"/api/v1/admin/users/{user(db, 'xiekai').id}"
    state = data(client.get(path + "/effective-permissions", headers=login("admin")))
    base = {"role_codes": state["assigned_roles"], "expected_revision": state["permission_revision"]}
    for sections in [{"operations": ["unknown"]}, {"hr": ["unknown"]}, {"operations": ["income"]}]:
        result = client.put(path + "/permissions", headers=login("admin"), json={**base, "permissions": {
            "menus": ["portal"], "buttons": [], "data_scope": "self", "sections": sections}})
        assert result.status_code == 400


def test_non_superuser_audit_and_export_do_not_expose_other_projects(client, login, db, own_project):
    import json
    from app.models import AuditEvent
    admin, worker = login("admin"), login("xiekai")
    path = f"/api/v1/admin/users/{user(db, 'xiekai').id}"
    original = data(client.get(path + "/effective-permissions", headers=admin))
    endpoint = f"{BASE}/{own_project['id']}"
    data(client.get(endpoint, headers=admin))
    event = db.scalar(select(AuditEvent).where(AuditEvent.path == endpoint).order_by(AuditEvent.id.desc()))
    try:
        data(client.put(path + "/permissions", headers=admin, json={
            "role_codes": original["assigned_roles"], "permissions": {
                "menus": ["portal", "operations", "operation_records", "admin"], "buttons": [], "data_scope": "all"},
            "expected_revision": original["permission_revision"]}))
        assert client.get(f"/api/v1/operation-records/event:{event.id}", headers=worker).status_code == 404
        records = data(client.get("/api/v1/operation-records", headers=worker, params={"module": "运营项目"}))
        assert records["items"] == [] and records["total"] == 0
        export = client.get("/api/v1/operation-records/export", headers=worker, params={"module": "运营项目"})
        assert own_project["name"] not in export.text
        details = data(client.get(f"/api/v1/admin/users/{own_project['owner_id']}/effective-permissions", headers=worker))
        assert own_project["id"] not in {row["id"] for row in details["projects"]}
        legacy = data(client.get("/api/v1/admin/audit", headers=worker, params={"action": "operations."}))
        assert not legacy["items"]
    finally:
        current = data(client.get(path + "/effective-permissions", headers=admin))
        data(client.put(path + "/permissions", headers=admin, json={
            "role_codes": original["assigned_roles"], "permissions": original["individual_permissions"],
            "expected_revision": current["permission_revision"]}))


def test_project_delete_password_is_checked_on_server(client, login, db, own_project):
    from app.core.security import create_jwt
    target = user(db, "ken") or SysUser(wecom_userid="ken", name="梁道灿")
    target.status, target.is_deleted, target.workbench_enabled = 1, False, True
    db.add(target)
    db.commit()
    headers = {"Authorization": "Bearer " + create_jwt(target.id)[0]}
    endpoint = f"{BASE}/{own_project['id']}"
    assert client.delete(endpoint, headers=headers).status_code == 400
    assert client.request("DELETE", endpoint, headers=headers, json={"password": "wrong"}).status_code == 400
    assert client.get(endpoint, headers=headers).status_code == 200
    data(client.request("DELETE", endpoint, headers=headers, json={"password": "admin123"}))
    assert client.get(endpoint, headers=headers).status_code == 404
    assert db.get(OpsProject, own_project["id"]).status == "deleted"


def test_assistant_history_scope_changes_when_membership_or_sections_are_revoked(client, login, db, own_project):
    from app.services.xiaoju_business import actor_signature
    from app.models import SysUserPermission
    target = user(db, "xiekai")
    before = actor_signature(target.id)
    member = OpsMember(project_id=own_project["id"], user_id=target.id, role="member")
    db.add(member)
    db.commit()
    joined = actor_signature(target.id)
    assert joined != before
    grant = SysUserPermission(user_id=target.id, menus=["portal", "operations"], buttons=[],
                              data_scope="self", sections={"operations": ["tasks"]})
    db.add(grant)
    db.commit()
    limited = actor_signature(target.id)
    assert limited != joined
    db.delete(member)
    db.commit()
    assert actor_signature(target.id) != limited
    db.delete(grant)
    db.commit()

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import engine, get_db
from app.main import app

from app.models import SysDepartment, SysUser, SysUserRole
from app.services.user_service import role_by_code
from conftest import data


@pytest.fixture
def isolated_db(client, login):
    login("admin")
    login("chenjing")
    with engine.connect() as connection, connection.begin() as transaction:
        with Session(bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False, autoflush=False) as session:
            app.dependency_overrides[get_db] = lambda: session
            try:
                yield session
            finally:
                app.dependency_overrides.pop(get_db, None)
                transaction.rollback()


def test_department_members_and_individual_role_changes(client, login, isolated_db):
    db = isolated_db
    admin = login("admin")
    outsider = login("chenjing")
    department = SysDepartment(dept_code="TSTFIN", name="通讯录财务组", wecom_dept_id=987654)
    db.add(department)
    db.flush()
    marker = {"company_directory": {"corp_id": "test-corp"}}
    dual = SysUser(wecom_userid="dept_roles_dual", name="财务组兼任测试", department_id=department.id,
                   position="会计", status=1, preferences=marker)
    second = SysUser(wecom_userid="dept_roles_peer", name="财务组同事测试", department_id=department.id,
                     status=2, preferences=marker)
    local = SysUser(wecom_userid="dept_roles_local", name="财务组本地测试", department_id=department.id,
                    status=1, preferences={"company_directory": {}})
    unassigned = SysUser(wecom_userid="dept_roles_unassigned", name="无部门测试", status=1, preferences=marker)
    db.add_all([dual, second, local, unassigned])
    db.flush()
    accountant = role_by_code(db, "CW-03")
    kb_admin = role_by_code(db, "KB-01")
    db.add_all([SysUserRole(user_id=dual.id, role_id=accountant.id),
                SysUserRole(user_id=dual.id, role_id=kb_admin.id),
                SysUserRole(user_id=second.id, role_id=accountant.id),
                SysUserRole(user_id=local.id, role_id=kb_admin.id)])
    db.commit()

    path = "/api/v1/admin"
    assert client.get(path + "/roles", headers=outsider).status_code == 403
    matrix = data(client.get(path + "/roles", headers=admin, params={"source": "directory"}))
    dept = next(row for row in matrix["departments"] if row["code"] == department.dept_code)
    assert dept["total"] == 2 and dept["active"] == 1
    assert dept["role_counts"] == {"CW-03": 2, "KB-01": 1}
    query = {"dept": department.dept_code, "source": "directory", "role": "KB-01", "keyword": "DEPT_ROLES", "compact": True}
    people = data(client.get(path + "/users", headers=admin, params=query))
    assert people["total"] == 1 and "mobile" not in people["items"][0]
    assert people["items"][0]["id"] == dual.id and people["items"][0]["roles"] == ["CW-03", "KB-01"]
    without_dept = data(client.get(path + "/users", headers=admin, params={"dept": "__unassigned__", "keyword": unassigned.wecom_userid}))
    assert [u["id"] for u in without_dept["items"]] == [unassigned.id]

    user_path = path + f"/users/{dual.id}"
    granted = data(client.get(user_path + "/effective-permissions", headers=admin))
    assert granted["assigned_roles"] == ["CW-03", "KB-01"] and granted["department"] == department.name
    assert granted["position"] == "会计" and "kb:manage" in granted["buttons"]
    roles = {"role_codes": ["CW-03"], "expected_role_codes": granted["assigned_roles"]}
    assert client.put(user_path + "/roles", headers=outsider, json=roles).status_code == 403
    data(client.put(user_path + "/roles", headers=admin, json=roles))
    assert client.put(user_path + "/roles", headers=admin, json=roles).status_code == 409
    changed = data(client.get(user_path + "/effective-permissions", headers=admin))
    assert changed["assigned_roles"] == ["CW-03"] and "kb:manage" not in changed["buttons"]
    assert db.scalar(select(SysUserRole).where(SysUserRole.user_id == local.id, SysUserRole.role_id == kb_admin.id))
    assert data(client.get(path + "/users", headers=admin, params=query))["total"] == 0


def test_personal_menus_actions_and_scope_are_enforced_for_one_user(client, login, isolated_db):
    from app.models import LogOperation
    from app.services.xiaoju_business_catalog import available
    from app.core.deps import build_principal
    db = isolated_db
    admin, target_session = login("admin"), login("chenjing")
    user = db.scalar(select(SysUser).where(SysUser.wecom_userid == "chenjing"))
    peer = db.scalar(select(SysUser).where(SysUser.wecom_userid == "zhaoli"))
    path = f"/api/v1/admin/users/{user.id}"
    before = data(client.get(path + "/effective-permissions", headers=admin))
    peer_before = data(client.get(f"/api/v1/admin/users/{peer.id}/effective-permissions", headers=admin))
    roles_before = {r.role_code: dict(r.permissions) for r in user.roles}
    permissions = {"menus": ["contracts"], "buttons": ["contracts:export"], "data_scope": "self"}
    body = {"role_codes": before["assigned_roles"], "permissions": permissions,
            "expected_revision": before["permission_revision"]}
    assert client.put(path + "/permissions", headers=target_session, json=body).status_code == 403
    saved = data(client.put(path + "/permissions", headers=admin, json=body))
    assert saved["individual_permissions"] == {**permissions, "menus": ["contracts", "portal"]}
    assert client.put(path + "/permissions", headers=admin, json=body).status_code == 409
    profile = data(client.get("/api/v1/auth/me", headers=target_session))
    assert set(profile["menus"]) == {"contracts", "portal"} and profile["buttons"] == ["contracts:export"]
    assert profile["data_scope"] == "self"
    assert client.get("/api/v1/flows", headers=target_session).status_code == 403
    assert client.post("/api/v1/contracts", headers=target_session, json={}).status_code == 403
    assert client.get("/api/v1/contracts", headers=target_session).status_code == 200
    db.refresh(user, attribute_names=["roles", "permission_override"])
    assert not any(op.module == "flows" for op in available(build_principal(user), db).values())
    assert {r.role_code: r.permissions for r in user.roles} == roles_before
    peer_after = data(client.get(f"/api/v1/admin/users/{peer.id}/effective-permissions", headers=admin))
    assert peer_after["permission_revision"] == peer_before["permission_revision"]

    # Portal preferences are a separate store; a member cannot overwrite their grants through it.
    client.put("/api/v1/auth/preferences", headers=target_session, json={"hidden_cards": [], "permissions": {"buttons": ["*"]}})
    assert data(client.get("/api/v1/auth/me", headers=target_session))["buttons"] == ["contracts:export"]

    restored = data(client.put(path + "/permissions", headers=admin, json={
        "role_codes": before["assigned_roles"], "permissions": None, "expected_revision": saved["permission_revision"]}))
    assert restored["individual_permissions"] is None
    assert set(data(client.get("/api/v1/auth/me", headers=target_session))["menus"]) >= {"flows", "expenses"}
    log = db.scalar(select(LogOperation).where(LogOperation.action == "user.permissions", LogOperation.target_id == str(user.id))
                    .order_by(LogOperation.id.desc()).limit(1))
    assert log.before["permissions"]["data_scope"] == "self" and log.after["permissions"] is None


def test_personal_permissions_validate_admin_and_stale_role_defaults(client, login, isolated_db):
    from app.models import SysRole, SysUserPermission
    db = isolated_db
    admin = login("admin")
    user = db.scalar(select(SysUser).where(SysUser.wecom_userid == "chenjing"))
    admin_user = db.scalar(select(SysUser).where(SysUser.wecom_userid == "admin"))
    path = f"/api/v1/admin/users/{user.id}"
    before = data(client.get(path + "/effective-permissions", headers=admin))
    base = {"role_codes": before["assigned_roles"], "permissions": {"menus": ["portal"], "buttons": [], "data_scope": "self"},
            "expected_revision": before["permission_revision"]}
    for invalid in ({"menus": ["does_not_exist"], "buttons": [], "data_scope": "self"},
                    {"menus": ["portal"], "buttons": ["*"], "data_scope": "self"},
                    {"menus": ["portal"], "buttons": [], "data_scope": "unknown"}):
        response = client.put(path + "/permissions", headers=admin, json={**base, "permissions": invalid})
        assert response.status_code == 400, response.text
    assert db.get(SysUserPermission, user.id) is None
    role = db.scalar(select(SysRole).where(SysRole.role_code == "CW-01"))
    role.permissions = {**role.permissions, "buttons": ["contracts:export"]}
    db.flush()
    db.expire(user, ["roles"])
    assert client.put(path + "/permissions", headers=admin, json=base).status_code == 409
    mine = f"/api/v1/admin/users/{admin_user.id}"
    state = data(client.get(mine + "/effective-permissions", headers=admin))
    denied = client.put(mine + "/permissions", headers=admin, json={
        "role_codes": state["assigned_roles"], "permissions": base["permissions"], "expected_revision": state["permission_revision"]})
    assert denied.status_code == 400 and db.get(SysUserPermission, admin_user.id) is None
    denied = client.put(mine + "/permissions", headers=admin, json={
        "role_codes": ["EMP"], "permissions": None, "expected_revision": state["permission_revision"]})
    assert denied.status_code == 409



def test_personal_department_scope_filters_contracts_and_opportunities(client, login, isolated_db):
    from datetime import date
    from app.models import BizContract, BizOpportunity
    db = isolated_db
    admin, session = login("admin"), login("chenjing")
    target = db.scalar(select(SysUser).where(SysUser.wecom_userid == "chenjing"))
    peer = db.scalar(select(SysUser).where(SysUser.wecom_userid == "zhaoli"))
    outside = db.scalar(select(SysUser).where(SysUser.wecom_userid == "songjia"))
    for number, owner in enumerate((target, peer, outside), 1):
        db.add(BizContract(contract_no=f"PRIV-DEPT-{number}", name="个人权限范围", customer_name="范围测试",
                           amount=100, sign_date=date(2026, 10, 20), business_line="XY", owner_id=owner.id))
        db.add(BizOpportunity(opp_no=f"PRIV-DEPT-{number}", name="个人权限范围", customer_name="范围测试",
                              amount=100, stage="lead", business_line="XY", owner_id=owner.id))
    db.commit()
    path = f"/api/v1/admin/users/{target.id}"
    before = data(client.get(path + "/effective-permissions", headers=admin))
    state = data(client.put(path + "/permissions", headers=admin, json={
        "role_codes": before["assigned_roles"], "permissions": {"menus": ["contracts", "opportunities"], "buttons": [], "data_scope": "dept"},
        "expected_revision": before["permission_revision"]}))
    contracts = data(client.get("/api/v1/contracts", headers=session, params={"keyword": "PRIV-DEPT"}))
    assert {r["contract_no"] for r in contracts["items"]} == {"PRIV-DEPT-1", "PRIV-DEPT-2"}
    opportunities = data(client.get("/api/v1/opportunities", headers=session, params={"keyword": "PRIV-DEPT"}))
    assert {r["opp_no"] for r in opportunities["items"]} == {"PRIV-DEPT-1", "PRIV-DEPT-2"}
    contract = db.scalar(select(BizContract).where(BizContract.contract_no == "PRIV-DEPT-3"))
    opportunity = db.scalar(select(BizOpportunity).where(BizOpportunity.opp_no == "PRIV-DEPT-3"))
    assert client.get(f"/api/v1/contracts/{contract.id}", headers=session).status_code == 403
    assert client.get(f"/api/v1/opportunities/{opportunity.id}", headers=session).status_code == 403
    data(client.put(path + "/permissions", headers=admin, json={
        "role_codes": before["assigned_roles"], "permissions": {"menus": ["contracts"], "buttons": [], "data_scope": "self"},
        "expected_revision": state["permission_revision"]}))
    assert data(client.get("/api/v1/contracts", headers=session, params={"keyword": "PRIV-DEPT"}))["total"] == 1

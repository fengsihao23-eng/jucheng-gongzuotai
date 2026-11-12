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

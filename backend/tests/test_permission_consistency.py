"""Saved grants and runtime authorization must agree across an existing session."""
from copy import deepcopy
from sqlalchemy import select
from app.models import SysUser
from tests.conftest import data
from tests.test_business_workbook import source_only  # noqa: F401
from tests.test_contract_workbook import tender


def test_current_session_updates_grants_and_runtime_revision(client,login,db):
    admin,worker=login("admin"),login("xiekai")
    u=db.scalar(select(SysUser).where(SysUser.wecom_userid=="xiekai"))
    path=f"/api/v1/admin/users/{u.id}"
    before=data(client.get(path+"/effective-permissions",headers=admin))
    old=data(client.get("/api/v1/auth/me",headers=worker))
    try:
        body={"role_codes":before["assigned_roles"],"permissions":{"menus":["operations","kb","operation_records"],
             "buttons":["kb:manage"],"data_scope":"self"},"expected_revision":before["permission_revision"]}
        saved=data(client.put(path+"/permissions",json=body,headers=admin))
        actual=data(client.get("/api/v1/auth/me",headers=worker))
        assert actual["menus"]==saved["menu_keys"]
        assert actual["permission_revision"]==saved["login_permission_revision"]!=old["permission_revision"]
        assert actual["buttons"]==["kb:manage"]
        assert client.get("/api/v1/operations",headers=worker).status_code==200
        assert client.get("/api/v1/operation-records",headers=worker).status_code==200
        assert client.get("/api/v1/admin/roles",headers=worker).status_code==403
        current=data(client.get(path+"/effective-permissions",headers=admin))
        data(client.put(path+"/permissions",headers=admin,json={**body,"permissions":{"menus":["kb"],"buttons":[],"data_scope":"self"},
                    "expected_revision":current["permission_revision"]}))
        revoked=data(client.get("/api/v1/auth/me",headers=worker))
        assert "operations" not in revoked["menus"] and "operation_records" not in revoked["menus"]
        assert revoked["permission_revision"]!=actual["permission_revision"]
        assert client.get("/api/v1/operation-records",headers=worker).status_code==403
    finally:
        current=data(client.get(path+"/effective-permissions",headers=admin))
        client.put(path+"/permissions",headers=admin,json={"role_codes":before["assigned_roles"],
                   "permissions":before["individual_permissions"],"expected_revision":current["permission_revision"]})


def test_explicit_company_module_grant_matches_source_view_but_not_other_modules(client,login,db,source_only):
    wb,admin_principal=source_only
    wb.sheets=[tender()]
    db.commit()
    admin,worker=login("admin"),login("xiekai")
    u=db.scalar(select(SysUser).where(SysUser.wecom_userid=="xiekai"))
    path=f"/api/v1/admin/users/{u.id}"
    before=data(client.get(path+"/effective-permissions",headers=admin))
    base="/api/v1/business-workbook"
    body={"role_codes":before["assigned_roles"],"permissions":{"menus":["projects"],"buttons":[],"data_scope":"all"}}
    try:
        saved=data(client.put(path+"/permissions",headers=admin,json={**body,"expected_revision":before["permission_revision"]}))
        result=data(client.get(base+"/tender",headers=worker))
        assert result["items"] and not result["source"]["can_sync"]
        row=result["items"][0]
        assert client.get(base+"/tender/rows/"+row["sheet_id"]+"/"+str(row["row"]),
                          params={"revision":wb.revision},headers=worker).status_code==200
        assert client.get(base+"/contracts",headers=worker).status_code==403
        assert client.get(base+"/opportunities",headers=worker).status_code==403
        assert client.post(base+"/sync?view=tender",headers=worker).status_code==403
        restricted=data(client.put(path+"/permissions",headers=admin,json={**body,
            "permissions":{**body["permissions"],"data_scope":"self"},"expected_revision":saved["permission_revision"]}))
        assert restricted["access_notes"]
        assert client.get(base+"/tender",headers=worker).status_code==403
    finally:
        current=data(client.get(path+"/effective-permissions",headers=admin))
        client.put(path+"/permissions",headers=admin,json={"role_codes":before["assigned_roles"],
                   "permissions":before["individual_permissions"],"expected_revision":current["permission_revision"]})

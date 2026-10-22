"""公司通讯录真实模式的建档与身份隔离；使用独立测试库和事务，不访问企业数据。"""
import json
import stat
import time
from types import SimpleNamespace

import jwt
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import engine
from app.core.deps import build_principal
from app.core.response import BizError
from app.core.security import hash_password, password_version
from app.integration.wecom.base import DeptDto, UserDto
from app.models import SysDepartment, SysUser
from app.services import company_directory_service as directory


@pytest.fixture
def company(seeded, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "storage_dir", tmp_path / "files")
    monkeypatch.setattr(settings, "environment", "test")
    monkeypatch.setattr(settings, "data_source_overrides", "wecom:live")
    monkeypatch.setattr(settings, "wecom_corp_id", "ww-test-directory")
    monkeypatch.setattr(settings, "wecom_secret", "private-company-test-secret")
    monkeypatch.setattr(settings, "wecom_agent_id", 1000027)
    provider = SimpleNamespace(depts=[DeptDto(1, "测试企业", None), DeptDto(3, "真实测试部门", 1)],
                               users=[UserDto("real_employee", "测试员工", [3], "工程师", "13900000000", "test@example.invalid")])
    provider.get_addressbook = lambda: (provider.depts, provider.users)
    monkeypatch.setattr(directory, "get_wecom", lambda: provider)
    with engine.connect() as conn:
        transaction = conn.begin()
        session = Session(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False, autoflush=False)
        admin = session.scalar(select(SysUser).where(SysUser.wecom_userid == "admin"))
        yield SimpleNamespace(db=session, actor=build_principal(admin), provider=provider)
        session.close()
        transaction.rollback()


def imported(company):
    preview = directory.preview(company.db, company.actor)
    result = directory.confirm(company.db, company.actor, preview["preview_id"])
    company.db.expire_all()
    return preview, result


def member(company, userid="real_employee"):
    return company.db.scalar(select(SysUser).where(SysUser.wecom_userid == userid))


def test_mock_and_missing_authorization_cannot_import(company, monkeypatch):
    monkeypatch.setattr(settings, "data_source_overrides", "")
    assert not directory.status(company.db)["ready"]
    with pytest.raises(BizError, match="尚未授权"):
        directory.preview(company.db, company.actor)
    monkeypatch.setattr(settings, "data_source_overrides", "wecom:live")
    monkeypatch.setattr(settings, "wecom_secret", "")
    assert not directory.status(company.db)["ready"]


def test_preview_is_read_only_private_and_contains_actual_scope(company):
    before = (company.db.scalar(select(func.count()).select_from(SysUser)), company.db.scalar(select(func.count()).select_from(SysDepartment)))
    preview = directory.preview(company.db, company.actor)
    assert preview["state"] == "pending" and preview["counts"]["create"] == 1
    assert preview["members"][0]["department_name"] == "真实测试部门"
    assert before == (company.db.scalar(select(func.count()).select_from(SysUser)), company.db.scalar(select(func.count()).select_from(SysDepartment)))
    target = directory._path(preview["preview_id"])
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert settings.wecom_secret not in target.read_text()


def test_confirm_creates_independent_real_departments_and_sso_employee(company):
    old_dept = company.db.scalar(select(SysDepartment).where(SysDepartment.dept_code == "CW"))
    preview, result = imported(company)
    user = member(company)
    assert result["rows"] == 1 and result["create"] == 1
    assert user.name == "测试员工" and user.department_id != old_dept.id
    assert user.department.name == "真实测试部门" and user.department.parent_id
    assert user.password_hash is None and [r.role_code for r in user.roles] == ["EMP"]
    assert user.preferences["company_directory"]["corp_id"] == settings.wecom_corp_id
    assert company.db.get(SysDepartment, old_dept.id).name == old_dept.name
    assert directory.status(company.db)["company_bound"]
    assert directory.confirm(company.db, company.actor, preview["preview_id"])["reused"]


def test_demo_collision_archives_old_identity_without_inheriting_privileged_role(company):
    demo = member(company, "chenjing")
    old_id, old_roles = demo.id, [r.role_code for r in demo.roles]
    company.provider.users = [UserDto("chenjing", "真实陈员工", [3])]
    preview, _ = imported(company)
    real = member(company, "chenjing")
    archived = company.db.get(SysUser, old_id)
    assert real.id != old_id and real.name == "真实陈员工"
    assert [r.role_code for r in real.roles] == ["EMP"]
    assert archived.wecom_userid.startswith("demo_") and archived.status == 2
    assert archived.password_hash is None and archived.access_token is None
    assert [r.role_code for r in archived.roles] == old_roles
    assert any(u["id"] == old_id for u in preview["retire_demo_users"])
    assert company.db.get(SysUser, company.actor.id).wecom_userid == "admin"
    assert company.db.get(SysUser, company.actor.id).status == 1


def test_protected_and_unbound_local_identity_conflicts_are_skipped(company):
    protected = member(company, "admin")
    demo = member(company, "chenjing")
    demo.password_hash = hash_password("unique-local-test-password")
    company.db.commit()
    company.provider.users = [UserDto("admin", "真实同名账号", [3]), UserDto("chenjing", "另一个员工", [3])]
    preview, result = imported(company)
    assert result["conflict"] == 2 and result["rows"] == 0
    assert all(m["action"] == "conflict" for m in preview["members"])
    assert member(company, "admin").id == protected.id and member(company, "admin").name == protected.name
    assert member(company, "chenjing").id == demo.id and member(company, "chenjing").status == 1


def test_incremental_sync_preserves_roles_optional_details_and_absent_members(company):
    imported(company)
    user = member(company)
    original_id, department_id = user.id, user.department_id
    original_role = user.roles[0]
    user.password_hash = hash_password("unique-real-user-test-password")
    password = user.password_hash
    company.db.commit()
    company.provider.users = [UserDto("real_employee", "更新员工", [], None, None, None), UserDto("second_real", "第二员工", [3])]
    result = directory.sync_confirmed_company(company.db)
    company.db.expire_all()
    user = member(company)
    assert result["update"] == 1 and result["create"] == 1
    assert user.id == original_id and user.name == "更新员工"
    assert user.department_id == department_id and user.mobile == "13900000000"
    assert user.password_hash == password and user.roles[0].id == original_role.id
    company.provider.users = [UserDto("second_real", "第二员工", [3])]
    directory.sync_confirmed_company(company.db)
    company.db.expire_all()
    assert member(company).status == 1


@pytest.mark.parametrize("status", [2, 5])
def test_explicit_inactive_revokes_mcp(company, status):
    imported(company)
    user = member(company)
    user.access_token, user.access_token_hint = "directory-test-token", "test-token"
    company.db.commit()
    company.provider.users = [UserDto("real_employee", "测试员工", [3], status=status)]
    directory.sync_confirmed_company(company.db)
    company.db.expire_all()
    assert member(company).status == 2 and member(company).access_token is None and member(company).access_token_hint is None


def test_preview_owner_expiry_and_stale_changes(company):
    p = directory.preview(company.db, company.actor)
    with pytest.raises(BizError, match="其他账号"):
        directory.inspect_preview(p["preview_id"], company.actor.id + 1)
    member(company, "admin").position = "changed-after-preview"
    company.db.commit()
    with pytest.raises(BizError, match="预览后发生变化"):
        directory.confirm(company.db, company.actor, p["preview_id"])
    p = directory.preview(company.db, company.actor)
    record = directory._read(p["preview_id"], company.actor.id)
    record["expires_at"] = time.time() - 1
    directory.private_write(directory._path(p["preview_id"]), record)
    with pytest.raises(BizError, match="失效"):
        directory.confirm(company.db, company.actor, p["preview_id"])


def test_changed_company_cannot_reuse_preview_or_mix_directory(company, monkeypatch):
    p = directory.preview(company.db, company.actor)
    monkeypatch.setattr(settings, "wecom_agent_id", 1000999)
    with pytest.raises(BizError, match="授权或账号权限已变化"):
        directory.confirm(company.db, company.actor, p["preview_id"])
    imported(company)
    monkeypatch.setattr(settings, "wecom_corp_id", "other-test-company")
    with pytest.raises(BizError, match="另一企业"):
        directory.preview(company.db, company.actor)


def test_first_background_import_requires_confirmed_company(company):
    with pytest.raises(BizError, match="先预览并确认"):
        directory.sync_confirmed_company(company.db)


def test_empty_or_failed_source_does_not_retire_anyone(company):
    company.provider.users = []
    with pytest.raises(BizError, match="未返回可用"):
        directory.preview(company.db, company.actor)
    def fail():
        raise RuntimeError("credential=must-not-be-shown")
    company.provider.get_addressbook = fail
    with pytest.raises(BizError) as error:
        directory.preview(company.db, company.actor)
    assert "must-not-be-shown" not in error.value.message
    assert member(company, "chenjing").status == 1


def test_mock_jobs_never_overwrite_imported_company(company, monkeypatch):
    from app.services.user_service import sync_addressbook
    imported(company)
    uid = member(company).id
    monkeypatch.setattr(settings, "data_source_overrides", "")
    monkeypatch.setattr(settings, "environment", "prod")
    assert sync_addressbook(company.db)["rows"] == 0
    assert member(company).id == uid and member(company).department.name == "真实测试部门"


def test_production_mock_oauth_and_old_session_are_rejected(client, login, db, monkeypatch):
    monkeypatch.setattr(settings, "environment", "prod")
    monkeypatch.setattr(settings, "data_source_overrides", "")
    assert client.post("/api/v1/auth/wecom/oauth", json={"code": "mock-admin"}).status_code == 403
    user = db.scalar(select(SysUser).where(SysUser.wecom_userid == "admin"))
    legacy = jwt.encode({"sub": str(user.id), "exp": int(time.time()) + 300, "typ": "session", "pwdv": password_version(user.password_hash)}, settings.secret_key, algorithm="HS256")
    assert client.get("/api/v1/auth/me", headers={"Authorization": "Bearer " + legacy}).status_code == 401
    assert client.get("/api/v1/auth/me", headers=login("admin")).status_code == 200


def test_live_oauth_cannot_inherit_local_admin_identity(client, monkeypatch, tmp_path):
    from app.api import auth
    monkeypatch.setattr(settings, "storage_dir", tmp_path / "files")
    monkeypatch.setattr(settings, "environment", "prod")
    monkeypatch.setattr(settings, "data_source_overrides", "wecom:live")
    monkeypatch.setattr(auth, "get_wecom", lambda: SimpleNamespace(exchange_user_info=lambda code: "admin"))
    response = client.post("/api/v1/auth/wecom/oauth", json={"code": "real-test-code"})
    assert response.status_code == 403 and "冲突" in response.json()["message"]


def test_live_notifications_cannot_deliver_to_unbound_demo_id(company, monkeypatch):
    from app.services import notify_service
    monkeypatch.setattr(notify_service, "get_wecom", lambda: pytest.fail("demo recipient reached enterprise provider"))
    rows = notify_service.send(company.db, [member(company, "admin")], "test", "测试通知", "测试内容")
    delivery = next(r for r in rows if r.channel == "wecom_app")
    assert delivery.send_status == 2 and delivery.retry_count == notify_service.MAX_RETRY


def test_three_basic_fields_generate_callback_keys_and_sanitize_ip_error(monkeypatch):
    from app.services import assistant_service
    monkeypatch.setattr(settings, "wecom_callback_token", "")
    monkeypatch.setattr(settings, "wecom_callback_aes_key", "")
    values = assistant_service.candidate("wecom", {"wecom_corp_id": "ww-test-directory", "wecom_agent_id": "1000027", "wecom_secret": "private-company-test-secret"})
    assert len(values["wecom_callback_aes_key"]) == 43 and len(values["wecom_callback_token"]) == 32
    result = assistant_service._wecom_error(60020, "not allowed from ip: 192.0.2.7; secret=must-not-show")
    assert result["error_code"] == 60020 and result["server_ip"] == "192.0.2.7"
    assert "must-not-show" not in json.dumps(result)


def test_admin_api_preview_confirmation_and_employee_permission(company, client, login):
    from app.core.db import get_db
    from app.main import app
    def session_override():
        yield company.db
    app.dependency_overrides[get_db] = session_override
    try:
        assert client.get('/api/v1/admin/addressbook/preview', headers=login('zengli')).status_code == 403
        preview = client.get('/api/v1/admin/addressbook/preview', headers=login('admin')).json()['data']
        assert member(company) is None
        result = client.post('/api/v1/admin/addressbook/sync', headers=login('admin'), json={'preview_id': preview['preview_id']})
        assert result.status_code == 200 and result.json()['data']['create'] == 1
        listing = client.get('/api/v1/admin/users', headers=login('admin'), params={'source': 'directory'}).json()['data']
        assert listing['total'] == 1 and listing['items'][0]['source'] == '公司通讯录'
        assert listing['items'][0]['mobile'] == '13900000000'
    finally:
        app.dependency_overrides.pop(get_db, None)

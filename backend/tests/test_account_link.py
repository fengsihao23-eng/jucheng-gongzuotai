"""同一所有者的本地管理员登录归并；所有修改封闭在独立事务和临时文件中。"""
import json
import uuid
from copy import deepcopy

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import db as dbcore
from app.core.accounts import LINK_KEY, resolve_account
from app.core.config import settings
from app.core.db import engine
from app.core.deps import build_principal
from app.core.response import BizError
from app.core.security import create_jwt, hash_password, hash_token
from app.models import BizContract, BizProject, LogOperation, SysNotify, SysUser, SysUserRole
from app.services import account_link_service as service, user_service, xiaoju_business, xiaoju_sessions
from tests.conftest import data

PASSWORD = 'Account-link-test-only-83!'


@pytest.fixture
def linked_setup(monkeypatch, tmp_path, seeded):
    conn = engine.connect()
    transaction = conn.begin()
    factory = lambda: Session(bind=conn, join_transaction_mode='create_savepoint', expire_on_commit=False, autoflush=False)
    monkeypatch.setattr(dbcore, 'SessionLocal', factory)
    monkeypatch.setattr(xiaoju_business, 'SessionLocal', factory)
    from app.mcp_server import server
    monkeypatch.setattr(server, 'SessionLocal', factory)
    monkeypatch.setattr(settings, 'wecom_corp_id', 'ww-account-link-test')
    monkeypatch.setattr(settings, 'storage_dir', tmp_path / 'files')
    monkeypatch.setattr(settings, 'assistant_workspace_root', tmp_path / 'workspaces')
    db = factory()
    source = db.scalar(select(SysUser).where(SysUser.wecom_userid == 'admin'))
    source.password_hash = hash_password(PASSWORD)
    source.access_token, source.access_token_hint = hash_token('account-link-test-token'), 'test-token'
    target = SysUser(wecom_userid='LinkTarget' + uuid.uuid4().hex[:10], name='目标企业管理员', status=1,
        preferences={'company_directory': {'corp_id': settings.wecom_corp_id}})
    db.add(target)
    db.flush()
    db.add(SysUserRole(user_id=target.id, role_id=user_service.role_by_code(db, 'EMP').id))
    db.commit()
    protected = db.scalar(select(SysUser).where(SysUser.wecom_userid == 'chenjing'))
    original_protected = {c.name: deepcopy(getattr(protected, c.name)) for c in SysUser.__table__.columns}
    protected_roles = [r.role_code for r in protected.roles]
    yield db, source, target, tmp_path, protected.id, original_protected, protected_roles
    db.close()
    transaction.rollback()
    conn.close()


def test_explicit_alias_preserves_admin_login_old_jwt_and_mcp(client, linked_setup):
    db, source, target, root, protected_id, protected, roles = linked_setup
    old_token = create_jwt(source.id)[0]
    result, files = service.apply(db, source.id, target.id, target.wecom_userid, root / 'backup', source.id)
    db.commit()
    assert not result['already_linked']
    assert source.status == 2 and target.status == 1
    assert build_principal(target).is_admin and build_principal(target).data_scope == 'all'
    assert resolve_account(db, source).id == target.id
    login = client.post('/api/v1/auth/login', json={'username': 'admin', 'password': PASSWORD})
    assert data(login)['profile']['id'] == target.id
    assert data(client.get('/api/v1/auth/me', headers={'Authorization': 'Bearer ' + old_token}))['id'] == target.id
    from app.mcp_server.server import _lookup_user
    assert _lookup_user('account-link-test-token') == target.id
    db.expire_all()
    untouched = db.get(SysUser, protected_id)
    assert {c.name: getattr(untouched, c.name) for c in SysUser.__table__.columns} == protected
    assert [r.role_code for r in untouched.roles] == roles
    again, changes = service.apply(db, source.id, target.id, target.wecom_userid, root / 'not-created', target.id)
    assert again['already_linked'] and changes is None and not (root / 'not-created').exists()


def test_ownership_and_conversations_move_audit_and_other_accounts_stay(linked_setup):
    db, source, target, root, *_ = linked_setup
    project = BizProject(project_code='LINK-' + uuid.uuid4().hex[:12], name='账号归属测试', customer_name='测试客户', business_line='XY', owner_id=source.id)
    db.add(project)
    note = SysNotify(user_id=source.id, channel='wecom_app', msg_type='reminder', title='历史通知', content='不应补发', send_status=0)
    target_note = SysNotify(user_id=target.id, channel='wecom_app', msg_type='reminder', title='目标原通知', content='保留原状态', send_status=0)
    audit = LogOperation(user_id=source.id, action='link.original', created_at=__import__('datetime').datetime.now(__import__('datetime').timezone.utc))
    db.add_all([note, target_note, audit])
    db.commit()
    old = xiaoju_sessions.create(source.id, '历史管理员会话')
    action_id = uuid.uuid4().hex
    path = settings.storage_dir.parent / 'xiaoju' / 'business' / 'actions' / f'{action_id}.json'
    from app.services.xiaoju_tools import private_write
    private_write(path, {'id': action_id, 'user_id': source.id, 'state': 'pending', 'actor_signature': 'old'})
    result, files = service.apply(db, source.id, target.id, target.wecom_userid, root / 'backup', source.id)
    db.commit()
    db.refresh(project); db.refresh(note); db.refresh(audit)
    assert project.owner_id == target.id
    assert note.user_id == target.id and note.retry_count == 3 and note.send_status == 2
    db.refresh(target_note)
    assert target_note.send_status == 0 and target_note.retry_count == 0
    assert audit.user_id == source.id
    assert xiaoju_sessions.load(target.id, old['id'])['messages'] == old['messages']
    action = json.loads(path.read_text())
    assert action['user_id'] == target.id and action['state'] == 'cancelled'
    assert (xiaoju_sessions.folder(source.id, old['id']) / 'meta.json').exists()


def test_wrong_identity_demo_password_and_target_password_fail_closed(linked_setup):
    db, source, target, root, *_ = linked_setup
    with pytest.raises(BizError):
        service.preview(db, source.id, target.id, 'wrong-userid')
    source.password_hash = hash_password('Jc@2026')
    with pytest.raises(BizError, match='演示密码'):
        service.preview(db, source.id, target.id, target.wecom_userid)
    source.password_hash = hash_password(PASSWORD)
    target.password_hash = hash_password('Independent-target-password-14!')
    with pytest.raises(BizError, match='独立密码'):
        service.preview(db, source.id, target.id, target.wecom_userid)
    assert not (root / 'backup').exists() and not build_principal(target).is_admin


def test_invalid_alias_never_grants_access(linked_setup):
    db, source, target, root, *_ = linked_setup
    target_role = user_service.role_by_code(db, 'ADMIN-01')
    db.add(SysUserRole(user_id=target.id, role_id=target_role.id)); db.flush(); db.refresh(target, ['roles'])
    source.preferences = {LINK_KEY: {'target_id': target.id, 'target_userid': 'wrong', 'corp_id': settings.wecom_corp_id}}
    with pytest.raises(BizError): resolve_account(db, source)
    source.preferences[LINK_KEY]['target_userid'] = target.wecom_userid
    target.status = 2
    with pytest.raises(BizError): resolve_account(db, source)


def test_file_failure_rolls_back_database_and_original_file(linked_setup):
    db, source, target, root, *_ = linked_setup
    session = xiaoju_sessions.create(source.id, '冲突测试')
    destination = xiaoju_sessions.folder(target.id, session['id'])
    destination.mkdir(parents=True)
    with pytest.raises(BizError, match='同 ID'):
        service.apply(db, source.id, target.id, target.wecom_userid, root / 'backup', source.id)
    db.rollback()
    db.refresh(source); db.refresh(target)
    assert source.status == 1 and LINK_KEY not in (source.preferences or {})
    assert not build_principal(target).is_admin

"""FR-01 统一认证：口令登录 / 企微 OAuth2（网页授权 + 扫码）/ Mock 身份选择 / MCP 个人令牌。"""
import time
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.audit import log_op
from app.core.accounts import resolve_account
from app.core.config import settings
from app.core.db import get_db
from app.core.deps import Principal, build_principal, client_ip, get_principal
from app.core.permissions import MENUS
from app.core.response import BizError, ok
from app.core.security import create_jwt, hash_password, hash_token, new_access_token, verify_password
from app.core.timeutil import utcnow
from app.integration.wecom import get_wecom
from app.models import SysUser
from app.services import user_service

router = APIRouter(prefix="/auth", tags=["认证"])


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class OAuthIn(BaseModel):
    code: str


class PasswordIn(BaseModel):
    old_password: str
    new_password: str = Field(min_length=8, max_length=64)


class VerifyPasswordIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str = Field(min_length=1, max_length=128)


class PrefsIn(BaseModel):
    card_order: list[str] | None = None
    hidden_cards: list[str] | None = None


def _session(db: Session, user: SysUser, request: Request, method: str) -> dict:
    from app.core.operation_audit import bind_actor
    bind_actor(db, user)
    user_service.ensure_default_role(db, user)
    user.last_login_at = utcnow()
    log_op(db, user.id, "auth.login", "user", user.id, after={"method": method}, ip=client_ip(request))
    db.commit()
    token, exp = create_jwt(user.id, verified_at=int(user.last_login_at.timestamp()))
    return {"token": token, "expires_at": exp, "profile": profile(build_principal(user), db)}


def visible_menus(db: Session, p: Principal) -> list[str]:
    """Explicit grants show the module; data inside it remains member-scoped."""
    menus = set(p.menus)
    if "operations" in menus:
        from app.models import OpsMember, OpsProject
        from app.services import operations_service as ops
        from app.core.permissions import ROLE_DEFS
        if p.user.permission_override is not None and not p.is_admin:
            explicit = "operations" in p.user.permission_override.menus
        else:
            explicit = any("operations" in {**ROLE_DEFS.get(role.role_code, {}), **(role.permissions or {})}.get("menus", [])
                           for role in p.user.roles)
        joined = explicit or ops.can_view_all(p) or db.scalar(
            select(OpsProject.id).where(OpsProject.owner_id == p.id, OpsProject.status != "deleted").limit(1)) is not None or db.scalar(
            select(OpsMember.id).join(OpsProject, OpsProject.id == OpsMember.project_id)
            .where(OpsMember.user_id == p.id, OpsProject.status != "deleted").limit(1)) is not None
        if not joined:
            menus.discard("operations")
    return sorted(menus, key=list(MENUS).index)


def profile(p: Principal, db: Session | None = None) -> dict:
    import hashlib, json
    from app.services.key_projects import has_team
    from app.services.personal_permission_service import view
    u = p.user
    menus = visible_menus(db, p) if db is not None else sorted(p.menus, key=list(MENUS).index)
    key_project_team = db is not None and p.workbench_enabled and has_team(db, p)
    permission_revision = hashlib.sha256(json.dumps({"configuration": view(u)["permission_revision"], "menus": menus,
        "workbench_enabled": p.workbench_enabled, "sections": p.sections, "key_project_team": key_project_team},
        sort_keys=True).encode()).hexdigest()
    return {"id": u.id, "userid": u.wecom_userid, "name": u.name, "position": u.position,
            "dept_code": u.dept_code, "dept_name": u.department.name if u.department else None,
            "roles": [{"code": r.role_code, "name": r.name} for r in u.roles] or [{"code": "EMP", "name": "普通员工"}],
            "menus": menus, "buttons": sorted(p.buttons), "data_scope": p.data_scope,
            "portal": p.portal, "is_admin": p.is_admin, "is_system_manager": p.is_system_manager, "preferences": u.preferences or {},
            "workbench_enabled": p.workbench_enabled, "sections": p.sections, "key_project_team": key_project_team,
            "mcp_token_hint": u.access_token_hint, "permission_revision": permission_revision}


@router.get("/login-config")
def login_config(db: Session = Depends(get_db)):
    data = {"password_login": settings.password_login_enabled, "wecom_mode": settings.source_mode("wecom"),
            "corp_id": settings.wecom_corp_id, "agent_id": settings.wecom_agent_id, "demo_accounts": []}
    if settings.source_mode("wecom") == "mock" and (settings.environment != "prod" or settings.demo_accounts_enabled):
        users = db.scalars(select(SysUser).where(SysUser.status == 1, SysUser.is_deleted.is_(False))
                           .order_by(SysUser.department_id, SysUser.id)).all()
        data["demo_accounts"] = [{"userid": u.wecom_userid, "name": u.name, "position": u.position,
                                  "dept": u.department.name if u.department else ""} for u in users
                                 if not (u.preferences or {}).get("company_directory")
                                 and not (u.preferences or {}).get("archived_demo_userid")]
        data["demo_password_hint"] = settings.demo_password
    return ok(data)


@router.post("/login")
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)):
    if not settings.password_login_enabled:
        raise BizError(40301, "已关闭口令登录，请使用企业微信扫码登录")
    u = db.scalar(select(SysUser).where(or_(SysUser.wecom_userid == body.username, SysUser.mobile == body.username,
                                            SysUser.name == body.username), SysUser.is_deleted.is_(False)))
    u = resolve_account(db, u)
    if not u or not verify_password(body.password, u.password_hash):
        log_op(db, u.id if u else None, "auth.login_failed", "user", body.username, ip=client_ip(request))
        db.commit()
        raise BizError(40101, "账号或密码错误")
    if u.status != 1:
        raise BizError(40301, "账号已停用（离职或被禁用）")
    return ok(_session(db, u, request, "password"))


@router.get("/wecom/authorize-url")
def wecom_authorize_url(redirect: str = "/", qr: bool = False):
    """企微内网页授权（snsapi_base）或浏览器扫码登录跳转地址。"""
    cb = quote(f"{settings.public_base_url.rstrip('/')}/login/callback?redirect={quote(redirect)}", safe="")
    if settings.source_mode("wecom") == "mock":
        return ok({"url": None, "mock": True})
    if qr:
        url = (f"https://login.work.weixin.qq.com/wwlogin/sso/login?login_type=CorpApp&appid={settings.wecom_corp_id}"
               f"&agentid={settings.wecom_agent_id}&redirect_uri={cb}&state=jc")
    else:
        url = (f"https://open.weixin.qq.com/connect/oauth2/authorize?appid={settings.wecom_corp_id}&redirect_uri={cb}"
               f"&response_type=code&scope=snsapi_base&agentid={settings.wecom_agent_id}&state=jc#wechat_redirect")
    return ok({"url": url, "mock": False})


@router.post("/wecom/oauth")
def wecom_oauth(body: OAuthIn, request: Request, db: Session = Depends(get_db)):
    if settings.environment == "prod" and settings.source_mode("wecom") != "live":
        raise BizError(40301, "公司企业微信登录尚未启用，请使用已设置的账号密码登录")
    try:
        userid = get_wecom().exchange_user_info(body.code)
    except Exception:  # Third-party errors can echo credentials.
        raise BizError(40101, "企业微信授权失败，请重新扫码并检查公司应用配置") from None
    u = db.scalar(select(SysUser).where(SysUser.wecom_userid == userid))
    if not u:  # 通讯录尚未同步该成员：即时同步一次
        user_service.sync_addressbook(db)
        u = db.scalar(select(SysUser).where(SysUser.wecom_userid == userid))
    if not u or u.status != 1:
        raise BizError(40301, "该企业微信账号未开通工作台或已离职")
    directory = (u.preferences or {}).get("company_directory")
    if settings.source_mode("wecom") == "live" and (
        not isinstance(directory, dict) or directory.get("corp_id") != settings.wecom_corp_id or u.is_deleted
    ):
        raise BizError(40301, "该企业账号尚未完成公司通讯录建档或与本地账号冲突，请联系平台管理员核对")
    return ok(_session(db, u, request, "wecom_oauth"))


@router.get("/me")
def me(p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    return ok(profile(p, db))


@router.post("/verify-password")
def verify_identity(body: VerifyPasswordIn, request: Request, p: Principal = Depends(get_principal),
                    db: Session = Depends(get_db)):
    if not settings.password_login_enabled or not p.user.password_hash:
        raise BizError(40301, "此账号请通过企业微信重新登录核验身份")
    if not verify_password(body.password, p.user.password_hash):
        log_op(db, p.id, "auth.verify_failed", "user", p.id, ip=client_ip(request))
        db.commit()
        raise BizError(40001, "密码不正确，请重新输入")
    log_op(db, p.id, "auth.verify", "user", p.id, after={"method": "password"}, ip=client_ip(request))
    db.commit()
    token, _ = create_jwt(p.id, verified_at=int(time.time()))
    return ok(headers={"X-Refresh-Token": token})


@router.post("/logout")
def logout(request: Request, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    log_op(db, p.id, "auth.logout", "user", p.id, ip=client_ip(request))
    db.commit()
    return ok()


@router.post("/password")
def change_password(body: PasswordIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    u = db.merge(p.user)
    if not verify_password(body.old_password, u.password_hash):
        raise BizError(40001, "原密码不正确")
    u.password_hash = hash_password(body.new_password)
    log_op(db, p.id, "auth.password_change", "user", p.id)
    db.commit()
    token, _ = create_jwt(u.id)
    return ok(headers={"X-Refresh-Token": token})


@router.put("/preferences")
def save_preferences(body: PrefsIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    u = db.merge(p.user)
    u.preferences = {**(u.preferences or {}), **body.model_dump(exclude_none=True)}
    db.commit()
    return ok(u.preferences)


@router.post("/mcp-token/reset")
def reset_mcp_token(request: Request, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """生成/重置个人 MCP Access Token。明文仅本次返回，旧令牌立即失效。"""
    u = db.merge(p.user)
    token = new_access_token()
    u.access_token, u.access_token_hint = hash_token(token), token[-6:]
    log_op(db, p.id, "mcp.token_reset", "user", p.id, ip=client_ip(request))
    db.commit()
    return ok({"token": token, "hint": u.access_token_hint})

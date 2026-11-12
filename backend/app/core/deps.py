"""依赖注入：当前用户、RBAC（菜单/按钮/数据范围）。"""
from dataclasses import dataclass, field

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.permissions import MENUS, PUBLIC_MENUS, ROLE_DEFS
from app.core.response import BizError, forbidden
from app.core.security import decode_jwt, password_version
from app.models import SysUser

_SCOPE_ORDER = {"self": 0, "dept": 1, "all": 2}


@dataclass
class Principal:
    user: SysUser
    role_codes: list[str]
    menus: set[str] = field(default_factory=set)
    buttons: set[str] = field(default_factory=set)
    data_scope: str = "self"
    portal: str = "employee"

    @property
    def id(self) -> int:
        return self.user.id

    @property
    def dept_code(self) -> str | None:
        return self.user.dept_code

    @property
    def is_admin(self) -> bool:
        return "ADMIN-01" in self.role_codes

    def can(self, button: str) -> bool:
        return "*" in self.buttons or button in self.buttons

    def has_menu(self, menu: str) -> bool:
        return menu in self.menus


def build_principal(user: SysUser, *, inherit_only: bool = False) -> Principal:
    codes = [r.role_code for r in user.roles] or ["EMP"]
    menus, buttons, scope, portal = set(PUBLIC_MENUS), set(), "self", "employee"
    best_portal_rank = -1
    for code in codes:
        perm = ROLE_DEFS.get(code)
        role = next((r for r in user.roles if r.role_code == code), None)
        if role and role.permissions:  # 库内配置优先（管理员可在线调整）
            perm = {**(perm or {}), **role.permissions}
        if not perm:
            continue
        menus.update(perm.get("menus", []))
        buttons.update(perm.get("buttons", []))
        if _SCOPE_ORDER[perm.get("data_scope", "self")] > _SCOPE_ORDER[scope]:
            scope = perm["data_scope"]
        rank = list(ROLE_DEFS).index(code) if code in ROLE_DEFS else 99
        if best_portal_rank == -1 or rank < best_portal_rank:
            best_portal_rank, portal = rank, perm.get("portal", portal)
    individual = user.permission_override if not inherit_only and "ADMIN-01" not in codes else None
    if individual is not None:
        menus = set(individual.menus) & set(MENUS)
        menus.add("portal")
        buttons = set(individual.buttons) - {"*"}
        scope = individual.data_scope
    return Principal(user=user, role_codes=codes, menus=menus, buttons=buttons, data_scope=scope, portal=portal)


def owner_scope(column, p):
    """Shared owner-based scope for contract/opportunity lists and direct links."""
    if p is None or p.data_scope == "all":
        return True
    if p.data_scope == "dept" and p.user.department_id:
        return column.in_(select(SysUser.id).where(SysUser.department_id == p.user.department_id))
    return column == p.id


def get_principal(request: Request, db: Session = Depends(get_db)) -> Principal:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise BizError(40101, "未登录")
    try:
        payload = decode_jwt(auth[7:])
    except Exception as exc:  # noqa: BLE001
        code = 40102 if exc.__class__.__name__ == "ExpiredSignatureError" else 40101
        raise BizError(code, "登录已过期，请重新登录" if code == 40102 else "登录无效") from exc
    user = db.get(SysUser, int(payload["sub"]))
    from app.core.accounts import resolve_account
    user = resolve_account(db, user)
    if not user or user.is_deleted or user.status != 1:
        raise BizError(40101, "账号不可用")
    from app.core.config import settings
    if settings.environment == "prod" and payload.get("authv") != 2:
        raise BizError(40102, "登录保护已更新，请重新登录")
    if payload.get("pwdv") is not None:
        if payload["pwdv"] != password_version(user.password_hash): raise BizError(40102, "账号密码已更新，请重新登录")
    else:
        # Legacy demo sessions cannot access a newly connected personal account.
        from app.services.wecom_cli_service import binding
        if binding(user.id) or (user.preferences or {}).get("company_directory"):
            raise BizError(40102, "账号连接已更新，请重新登录")
    p = build_principal(user)
    request.state.principal = p
    request.state.jwt_exp = payload["exp"]
    request.state.identity_verified_at = payload.get("verified_at")
    return p


def require_menu(*menus: str):
    def dep(p: Principal = Depends(get_principal)) -> Principal:
        if not any(p.has_menu(m) for m in menus):
            raise forbidden()
        return p
    return dep


def require_button(button: str):
    def dep(p: Principal = Depends(get_principal)) -> Principal:
        if not p.can(button):
            raise forbidden("无此操作权限")
        return p
    return dep


def require_recent_login(request: Request) -> None:
    import time
    from app.core.config import settings

    at = getattr(request.state, "identity_verified_at", None)
    if settings.environment == "prod" and (not isinstance(at, int) or not 0 <= time.time() - at <= 900):
        principal = getattr(request.state, "principal", None)
        raise BizError(40301, "请先核验本人身份，再保存权限", data={
            "requires_reauthentication": True,
            "password_available": bool(settings.password_login_enabled and principal and principal.user.password_hash),
        })


def client_ip(request: Request) -> str | None:
    fwd = request.headers.get("X-Forwarded-For")
    return fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else None)

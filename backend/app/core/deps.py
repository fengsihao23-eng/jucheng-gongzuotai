"""依赖注入：当前用户、RBAC（菜单/按钮/数据范围）。"""
from dataclasses import dataclass, field

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.permissions import PUBLIC_MENUS, ROLE_DEFS
from app.core.response import BizError, forbidden
from app.core.security import decode_jwt
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


def build_principal(user: SysUser) -> Principal:
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
    return Principal(user=user, role_codes=codes, menus=menus, buttons=buttons, data_scope=scope, portal=portal)


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
    if not user or user.is_deleted or user.status != 1:
        raise BizError(40101, "账号不可用")
    p = build_principal(user)
    request.state.principal = p
    request.state.jwt_exp = payload["exp"]
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


def client_ip(request: Request) -> str | None:
    fwd = request.headers.get("X-Forwarded-For")
    return fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else None)

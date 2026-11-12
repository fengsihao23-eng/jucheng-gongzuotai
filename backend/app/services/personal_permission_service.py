"""Atomic personal grants using the same Principal for web, assistant and MCP."""
import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select

from app.core.audit import log_op
from app.core.deps import build_principal
from app.core.permissions import BUTTONS, MENUS, ROLE_DEFS
from app.core.response import bad_request, conflict
from app.models import SysRole, SysUserPermission, SysUserRole
from app.services import user_service


class PersonalPermissionsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    menus: list[str] = Field(max_length=100)
    buttons: list[str] = Field(max_length=200)
    data_scope: Literal["self", "dept", "all"]


class PersonalGrantIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role_codes: list[str] = Field(max_length=50)
    permissions: PersonalPermissionsIn | None = None
    expected_revision: str = Field(pattern=r"^[a-f0-9]{64}$")


def options(db):
    result = dict(BUTTONS)
    for role in db.scalars(select(SysRole)):
        for button in (role.permissions or {}).get("buttons", []):
            if button != "*":
                result.setdefault(button, button)
    return result


def configured(principal):
    return {"menus": sorted(principal.menus), "buttons": sorted(principal.buttons), "data_scope": principal.data_scope}


def individual(user):
    setting = user.permission_override
    return {"menus": sorted(setting.menus), "buttons": sorted(setting.buttons),
            "data_scope": setting.data_scope} if setting else None


def view(user):
    inherited = configured(build_principal(user, inherit_only=True))
    saved = individual(user)
    raw = {"user_id": user.id, "active": user.status, "roles": sorted(r.role_code for r in user.roles),
           "inherited": inherited, "individual": saved}
    revision = hashlib.sha256(json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"individual_permissions": saved, "inherited_permissions": inherited,
            "permission_revision": revision}


def update(db, actor, user, body, keep_admin, ip):
    db.refresh(user, attribute_names=["status", "roles", "permission_override"])
    before = {"role_codes": sorted(r.role_code for r in user.roles), "permissions": individual(user)}
    if view(user)["permission_revision"] != body.expected_revision:
        raise conflict("此人的权限或角色规则已更新，请重新查看后再保存")
    codes = sorted(set(body.role_codes))
    unknown = set(codes) - ROLE_DEFS.keys()
    if unknown:
        raise bad_request(f"未知角色 {min(unknown)}")
    saved = None
    if body.permissions is not None:
        if "ADMIN-01" in codes:
            raise bad_request("平台管理员保留完整权限，请先调整角色再单独配置")
        allowed_buttons = options(db)
        unknown_menus = set(body.permissions.menus) - MENUS.keys()
        unknown_buttons = set(body.permissions.buttons) - allowed_buttons.keys()
        if unknown_menus or unknown_buttons:
            raise bad_request("菜单或操作权限无效，请重新读取可选权限")
        saved = {"menus": sorted(set(body.permissions.menus) | {"portal"}),
                 "buttons": sorted(set(body.permissions.buttons)), "data_scope": body.permissions.data_scope}

    keep_admin(db, user, "ADMIN-01" not in codes)
    after = {"role_codes": codes, "permissions": saved}
    if before == after:
        return
    if before["role_codes"] != codes:
        db.execute(delete(SysUserRole).where(SysUserRole.user_id == user.id))
        for code in codes:
            db.add(SysUserRole(user_id=user.id, role_id=user_service.role_by_code(db, code).id))
    record = db.get(SysUserPermission, user.id)
    if saved is None:
        if record:
            db.delete(record)
    else:
        record = record or SysUserPermission(user_id=user.id)
        record.menus, record.buttons, record.data_scope = saved["menus"], saved["buttons"], saved["data_scope"]
        record.updated_by = actor.id
        db.add(record)
    db.flush()
    db.expire(user, ["roles", "permission_override"])
    log_op(db, actor.id, "user.permissions", "user", user.id, before, after, ip)

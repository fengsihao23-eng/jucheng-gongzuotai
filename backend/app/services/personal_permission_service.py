"""Atomic personal grants using the same Principal for web, assistant and MCP."""
import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select

from app.core.audit import log_op
from app.core.deps import build_principal
from app.core.permissions import BUTTONS, MENUS, ROLE_DEFS
from app.core.permission_catalog import MENU_SECTIONS
from app.core.response import bad_request, conflict
from app.models import SysRole, SysUserPermission, SysUserRole
from app.services import user_service


class PersonalPermissionsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    menus: list[str] = Field(max_length=100)
    buttons: list[str] = Field(max_length=200)
    data_scope: Literal["self", "dept", "all"]
    sections: dict[str, list[str]] | None = None


class PersonalGrantIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role_codes: list[str] = Field(max_length=50)
    permissions: PersonalPermissionsIn | None = None
    expected_revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    workbench_enabled: bool | None = None
    all_project_access: bool | None = None


def options(db):
    result = dict(BUTTONS)
    for role in db.scalars(select(SysRole)):
        for button in (role.permissions or {}).get("buttons", []):
            if button != "*":
                result.setdefault(button, button)
    return result


def configured(principal):
    return {"menus": sorted(principal.menus), "buttons": sorted(principal.buttons), "data_scope": principal.data_scope,
            "sections": principal.sections}


def individual(user):
    setting = user.permission_override
    return {"menus": sorted(setting.menus), "buttons": sorted(setting.buttons),
            "data_scope": setting.data_scope,
            **({"sections": setting.sections} if setting.sections is not None else {})} if setting else None


def view(user):
    inherited = configured(build_principal(user, inherit_only=True))
    saved = individual(user)
    raw = {"user_id": user.id, "active": user.status, "workbench_enabled": user.workbench_enabled,
           "all_project_access": user.all_project_access,
           "roles": sorted(r.role_code for r in user.roles),
           "inherited": inherited, "individual": saved}
    revision = hashlib.sha256(json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"individual_permissions": saved, "inherited_permissions": inherited, "all_project_access": user.all_project_access,
            "permission_revision": revision}


def update(db, actor, user, body, keep_admin, ip):
    db.refresh(user, attribute_names=["status", "workbench_enabled", "all_project_access", "roles", "permission_override"])
    before = {"role_codes": sorted(r.role_code for r in user.roles), "permissions": individual(user),
              "workbench_enabled": user.workbench_enabled, "all_project_access": user.all_project_access}
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
        sections = body.permissions.sections
        if sections is not None:
            for menu, selected in sections.items():
                if menu not in MENU_SECTIONS or set(selected) - MENU_SECTIONS[menu].keys():
                    raise bad_request("功能小项无效，请重新读取可选权限")
                if selected and menu not in body.permissions.menus:
                    raise bad_request("请先勾选功能小项所属的菜单")
            sections = {menu: sorted(set(selected)) for menu, selected in sections.items()}
        saved = {"menus": sorted(set(body.permissions.menus) | {"portal"}),
                 "buttons": sorted(set(body.permissions.buttons)), "data_scope": body.permissions.data_scope,
                 **({"sections": sections} if sections is not None else {})}

    enabled = user.workbench_enabled if body.workbench_enabled is None else body.workbench_enabled
    keep_admin(db, user, codes, enabled=enabled)
    project_access = user.all_project_access if body.all_project_access is None else body.all_project_access
    after = {"role_codes": codes, "permissions": saved, "workbench_enabled": enabled, "all_project_access": project_access}
    if before == after:
        return
    user.workbench_enabled = enabled
    user.all_project_access = project_access
    if not enabled:
        user.access_token = user.access_token_hint = None
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
        record.sections = saved.get("sections")
        record.updated_by = actor.id
        db.add(record)
    db.flush()
    db.expire(user, ["roles", "permission_override"])
    log_op(db, actor.id, "user.permissions", "user", user.id, before, after, ip)

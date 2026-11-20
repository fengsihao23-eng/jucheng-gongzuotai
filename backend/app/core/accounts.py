"""显式核准的本地管理员登录别名；不按姓名或企微管理员身份自动合并。"""
from app.core.config import settings
from app.core.response import BizError
from app.models import SysUser

LINK_KEY = "verified_account_link"
RESET_ALIAS_KEY = "workbench_access_reset"
RESET_ALIAS_POLICY = "20261120_explicit_grants"


def resolve_account(db, user):
    if user is None:
        return None
    link = (user.preferences or {}).get(LINK_KEY)
    if link is None:
        return user
    reset_alias = (isinstance(link, dict) and link.get(RESET_ALIAS_KEY) == RESET_ALIAS_POLICY
                   and user.workbench_enabled is False and not user.roles
                   and user.permission_override is not None
                   and not user.permission_override.menus and not user.permission_override.buttons)
    if (not isinstance(link, dict) or user.is_deleted or user.wecom_userid != "admin"
            or not (any(r.role_code == "ADMIN-01" for r in user.roles) or reset_alias)):
        raise BizError(40102, "账号关联无效，请联系平台管理员")
    target_id = link.get("target_id")
    target = db.get(SysUser, target_id) if isinstance(target_id, int) and not isinstance(target_id, bool) else None
    directory = (target.preferences or {}).get("company_directory", {}) if target else {}
    if (not target or target.id == user.id or target.status != 1 or target.is_deleted
            or LINK_KEY in (target.preferences or {})
            or target.wecom_userid != link.get("target_userid")
            or not settings.wecom_corp_id or link.get("corp_id") != settings.wecom_corp_id
            or not isinstance(directory, dict) or directory.get("corp_id") != settings.wecom_corp_id
            or not any(r.role_code == "ADMIN-01" for r in target.roles)):
        raise BizError(40102, "关联的企业管理员账号已变化，请重新核对")
    return target

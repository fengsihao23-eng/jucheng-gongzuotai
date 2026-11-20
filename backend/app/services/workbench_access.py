"""2026-11-20 点名保留名单的一次性权限重置，保留项目业务资料。"""
import hashlib
import json

from sqlalchemy import delete, select

from app.core.audit import log_op
from app.core.accounts import LINK_KEY, RESET_ALIAS_KEY, RESET_ALIAS_POLICY, resolve_account
from app.core.response import conflict
from app.models import SysDict, SysUser, SysUserPermission, SysUserRole
from app.services.personal_permission_service import individual

RETAINED_ACCOUNTS = {
    "SZGLY": {"聚成数智管理员"}, "ken": {"梁道灿"}, "ZhangYuQi": {"张宇琦"}, "LiHua": {"李画"},
    "ZhuDanDan": {"朱丹丹"}, "mr.liang": {"梁华健"}, "LiuYingJun": {"刘映君"},
    "WangLiQian": {"王丽倩"}, "HePeiYing": {"何培英", "何佩英"}, "LinYiFu": {"林奕富"},
    "HuangHuanXing": {"黄换星"}, "XieBinKui": {"谢斌揆"}, "FengSiHao": {"冯思皓"},
}
POLICY_KEY = "20261120_explicit_grants"


def _marker(db):
    return db.scalar(select(SysDict).where(SysDict.dict_type == "workbench_access", SysDict.dict_key == POLICY_KEY))


def preview(db, *, lock=False):
    query = select(SysUser).order_by(SysUser.id)
    users = list(db.scalars(query.with_for_update(of=SysUser) if lock else query))
    retained, revoked, snapshot, aliases = [], [], [], []
    for user in users:
        row = {"id": user.id, "userid": user.wecom_userid, "name": user.name}
        if user.wecom_userid in RETAINED_ACCOUNTS:
            if user.name not in RETAINED_ACCOUNTS[user.wecom_userid] or user.is_deleted or user.status != 1:
                raise conflict("保留账号身份或在职状态异常：" + user.wecom_userid)
            retained.append(row)
        else:
            revoked.append(row)
            if (user.preferences or {}).get(LINK_KEY):
                target = resolve_account(db, user)
                if target.wecom_userid in RETAINED_ACCOUNTS:
                    aliases.append({**row, "target_id": target.id})
        snapshot.append({**row, "active": user.status, "deleted": user.is_deleted,
            "enabled": user.workbench_enabled, "roles": sorted(role.role_code for role in user.roles),
            "individual": individual(user), "has_token": bool(user.access_token)})
    missing = set(RETAINED_ACCOUNTS) - {row["userid"] for row in retained}
    if missing:
        raise conflict("保留名单有账号未找到：" + "、".join(sorted(missing)))
    revision = hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return {"already_applied": bool(_marker(db)), "retained": retained, "login_aliases": aliases, "revoke_count": len(revoked),
            "revoked": revoked, "revision": revision}


def apply(db, expected_revision):
    plan = preview(db, lock=True)
    if plan["already_applied"]:
        raise conflict("名单重置已经执行，不会再次覆盖后来单独配置的权限")
    if plan["revision"] != expected_revision:
        raise conflict("人员或权限已变化，请重新核对重置名单")
    actor = db.scalar(select(SysUser).where(SysUser.wecom_userid == "SZGLY"))
    retained_ids = {row["id"] for row in plan["retained"]}
    alias_ids = {row["id"] for row in plan["login_aliases"]}
    for row in plan["revoked"]:
        user = db.get(SysUser, row["id"])
        before = {"workbench_enabled": user.workbench_enabled,
                  "roles": sorted(role.role_code for role in user.roles), "permissions": individual(user)}
        user.workbench_enabled = False
        user.access_token = user.access_token_hint = None
        if user.id in alias_ids:
            user.preferences = {**user.preferences, LINK_KEY: {
                **user.preferences[LINK_KEY], RESET_ALIAS_KEY: RESET_ALIAS_POLICY}}
        db.execute(delete(SysUserRole).where(SysUserRole.user_id == user.id))
        grant = db.get(SysUserPermission, user.id) or SysUserPermission(user_id=user.id)
        grant.menus, grant.buttons, grant.sections, grant.data_scope, grant.updated_by = [], [], {}, "self", actor.id
        db.add(grant)
        log_op(db, actor.id, "user.access_reset", "user", user.id, before,
               {"workbench_enabled": False, "roles": [], "permissions": {"menus": [], "buttons": [], "sections": {}}})
    # 不写入名单内账号的角色、授权、状态、令牌和项目成员关系。
    db.add(SysDict(dict_type="workbench_access", dict_key=POLICY_KEY,
        dict_value={"retained_ids": sorted(retained_ids), "revoked_count": plan["revoke_count"], "revision": expected_revision},
        remark="2026-11-20 按点名名单保留原权限，其余账号须单独授权"))
    db.flush()
    return plan

"""企业微信客户联系：读取成员可服务的客户，供市场部登记商机。

只做读取。客户数据属于业务敏感信息，接口仅对市场/商机菜单开放，且不落库。
"""
from sqlalchemy.orm import Session

from app.core.response import BizError
from app.integration.wecom.credentials import Gateway, WeComApiError


def _guard(error: WeComApiError):
    if error.errcode in (60011, 48002, 301055, 41048):
        raise BizError(50201, f"企业微信客户联系接口未授权（{error.errcode}）：{error.errmsg}。"
                              "请在企微后台开通「客户联系」并配置客户联系 Secret",
                       {"errcode": error.errcode}) from None
    raise BizError(50201, f"读取客户数据失败（{error.errcode}）：{error.errmsg}") from None


def follow_users() -> list[str]:
    try:
        data = Gateway.call("external", "GET", "/cgi-bin/externalcontact/get_follow_user_list")
    except WeComApiError as error:
        _guard(error)
    return data.get("follow_user") or []


def customers(userid: str) -> list[dict]:
    if not userid:
        raise BizError(40001, "请指定成员")
    out, cursor = [], ""
    for _ in range(10):  # 单次最多 1000 个客户，避免无限翻页
        body = {"userid_list": [userid], "limit": 100}
        if cursor:
            body["cursor"] = cursor
        try:
            data = Gateway.call("external", "POST", "/cgi-bin/externalcontact/batch/get", json=body)
        except WeComApiError as error:
            _guard(error)
        for row in data.get("external_contact_list") or []:
            contact = row.get("external_contact") or {}
            follow = row.get("follow_info") or {}
            out.append({
                "external_userid": contact.get("external_userid") or "",
                "name": contact.get("name") or "未命名客户",
                "type": contact.get("type"),
                "corp_name": contact.get("corp_name") or "",
                "corp_full_name": contact.get("corp_full_name") or "",
                "position": contact.get("position") or "",
                "avatar": contact.get("avatar") or "",
                "remark": follow.get("remark") or "",
                "description": follow.get("description") or "",
                "createtime": follow.get("createtime"),
                "tags": [tag.get("tag_name") for tag in (follow.get("tags") or []) if tag.get("tag_name")],
            })
        cursor = data.get("next_cursor") or ""
        if not cursor:
            break
    return out


def summary(db: Session, user) -> dict:
    """当前登录人能看到的客户概况；未配置凭证时返回可执行提示而不是 500。"""
    try:
        members = follow_users()
    except BizError as error:
        return {"configured": False, "message": error.message, "members": [], "total": 0}
    mine = user.wecom_userid if user.wecom_userid in members else (members[0] if members else "")
    return {"configured": True, "members": members, "default": mine, "total": len(members)}

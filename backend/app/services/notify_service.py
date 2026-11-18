"""消息通知中心（FR-12）：企微应用消息 + 站内通知，发送记录入 sys_notify，失败重试 3 次。"""
import logging
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.timeutil import utcnow
from app.integration.wecom import get_wecom
from app.integration.wecom.base import MessageDto
from app.models import SysNotify, SysRole, SysUser, SysUserRole

log = logging.getLogger("notify")
MAX_RETRY = 3


def _abs(link: str | None) -> str | None:
    if link and link.startswith("/"):
        return settings.public_base_url.rstrip("/") + link
    return link


def send(db: Session, users: Iterable[SysUser], msg_type: str, title: str, content: str, *, link: str | None = None,
         ref_type: str | None = None, ref_id: str | None = None, wecom: bool = True, md: bool = True,
         defer: bool = False, wecom_content: str | None = None) -> list[SysNotify]:
    """对每个用户写一条站内通知；wecom=True 时同时推企微（每人一条 wecom_app 记录，便于逐条追踪状态）。"""
    users = [u for u in users if u and u.status == 1 and not u.is_deleted]
    rows: list[SysNotify] = []
    for u in users:
        rows.append(SysNotify(user_id=u.id, channel="inapp", msg_type=msg_type, title=title, content=content,
                              link=link, ref_type=ref_type, ref_id=ref_id, send_status=1, sent_at=utcnow()))
        if wecom:
            rows.append(SysNotify(user_id=u.id, channel="wecom_app", msg_type=msg_type, title=title, content=wecom_content if wecom_content is not None else content,
                                  link=link, ref_type=ref_type, ref_id=ref_id, send_status=0))
    db.add_all(rows)
    db.flush()
    pending = [r for r in rows if r.channel == "wecom_app"]
    if pending and not defer:
        _deliver(db, pending, "markdown" if md else "textcard")
    return rows


def _deliver(db: Session, rows: list[SysNotify], msg_type: str = "markdown") -> None:
    by_user = {u.id: u for u in db.scalars(select(SysUser).where(SysUser.id.in_({r.user_id for r in rows})))}
    if settings.source_mode("wecom") == "live":
        allowed = []
        for r in rows:
            user = by_user.get(r.user_id)
            marker = (user.preferences or {}).get("company_directory", {}) if user else {}
            if user and user.status == 1 and not user.is_deleted and isinstance(marker, dict) and marker.get("corp_id") == settings.wecom_corp_id:
                allowed.append(r)
            else:
                r.send_status, r.error_msg, r.retry_count = 2, "未绑定公司通讯录的账号不发送企业微信消息", MAX_RETRY
        rows = allowed
        if not rows:
            db.flush()
            return
    from app.services.operations_notifications import TYPES as OPS_TYPES, eligible as ops_eligible
    from app.services.collection_followups import NOTICE_TYPES as COLLECTION_TYPES, eligible_notice as collection_eligible
    if any(r.msg_type in OPS_TYPES for r in rows):
        from app.core.deps import build_principal

        allowed = []
        for r in rows:
            if r.msg_type in OPS_TYPES:
                u = by_user.get(r.user_id)
                can_receive = u and u.status == 1 and not u.is_deleted and ops_eligible(db, build_principal(u), r)
                if not can_receive:
                    r.send_status, r.error_msg, r.retry_count = 2, "事项状态、负责人、权限或项目提醒设置已变更", MAX_RETRY
                    continue
            allowed.append(r)
        rows = allowed
        if not rows:
            db.flush()
            return
    if any(r.msg_type in COLLECTION_TYPES for r in rows):
        allowed = []
        for r in rows:
            if r.msg_type in COLLECTION_TYPES and not collection_eligible(db, r):
                r.send_status, r.error_msg, r.retry_count = 2, '回款跟进已处理或接收人不匹配', MAX_RETRY
                continue
            allowed.append(r)
        rows = allowed
        if not rows:
            db.flush()
            return
    # 同一内容批量发送（企微 touser 支持 | 分隔，最多 1000 人）
    groups: dict[tuple, list[SysNotify]] = {}
    for r in rows:
        # 运营通知统一发为带入口的文本卡片，定时投递和重试使用同一格式。
        delivery_type = "textcard" if r.msg_type in (OPS_TYPES | COLLECTION_TYPES) and r.link else msg_type
        groups.setdefault((delivery_type, r.title, r.content, r.link), []).append(r)
    try:
        provider = get_wecom()
    except Exception as exc:  # noqa: BLE001  凭证未配置等
        for r in rows:
            r.send_status, r.error_msg, r.retry_count = 2, str(exc)[:500], r.retry_count + 1
        return
    for (delivery_type, title, content, link), grp in groups.items():
        ids = [by_user[r.user_id].wecom_userid for r in grp]
        err = ""
        try:
            res = provider.send_app_message(ids, MessageDto(delivery_type, title, content, _abs(link)))
        except Exception as exc:  # noqa: BLE001
            res = None
            err = str(exc)
        for r in grp:
            delivered = bool(res and res.ok and by_user[r.user_id].wecom_userid not in res.invalid_users)
            r.retry_count += 0 if delivered else 1
            if delivered:
                r.send_status, r.sent_at, r.error_msg = 1, utcnow(), None
            else:
                r.send_status = 2
                r.error_msg = (res.error if res else err) or "invalid user"
    db.flush()


def _deliver_queue(db: Session, *conditions) -> int:
    rows = db.scalars(select(SysNotify).where(SysNotify.channel == "wecom_app", SysNotify.retry_count < MAX_RETRY,
        *conditions).order_by(SysNotify.id).limit(500).with_for_update(skip_locked=True)).all()
    if rows:
        _deliver(db, list(rows))
    return len(rows)


def deliver_pending(db: Session, ids: list[int]) -> int:
    """Try newly committed notifications without racing the periodic retry worker."""
    return _deliver_queue(db, SysNotify.id.in_(ids), SysNotify.send_status == 0) if ids else 0


def retry_failed(db: Session) -> int:
    return _deliver_queue(db, SysNotify.send_status.in_([0, 2]))


def users_by_roles(db: Session, role_codes: Iterable[str]) -> list[SysUser]:
    return list(db.scalars(select(SysUser).join(SysUserRole, SysUserRole.user_id == SysUser.id)
                           .join(SysRole, SysRole.id == SysUserRole.role_id)
                           .where(SysRole.role_code.in_(list(role_codes)), SysUser.status == 1).distinct()))


def users_by_userids(db: Session, userids: Iterable[str]) -> list[SysUser]:
    return list(db.scalars(select(SysUser).where(SysUser.wecom_userid.in_(list(userids)))))


def alert_admins(db: Session, title: str, content: str) -> None:
    send(db, users_by_roles(db, ["ADMIN-01"]), "job_failed", title, content, link="/admin?tab=jobs")
    db.commit()

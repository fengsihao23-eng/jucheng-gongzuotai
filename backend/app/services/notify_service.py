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
         ref_type: str | None = None, ref_id: str | None = None, wecom: bool = True, md: bool = True) -> list[SysNotify]:
    """对每个用户写一条站内通知；wecom=True 时同时推企微（每人一条 wecom_app 记录，便于逐条追踪状态）。"""
    users = [u for u in users if u and u.status == 1 and not u.is_deleted]
    rows: list[SysNotify] = []
    for u in users:
        rows.append(SysNotify(user_id=u.id, channel="inapp", msg_type=msg_type, title=title, content=content,
                              link=link, ref_type=ref_type, ref_id=ref_id, send_status=1, sent_at=utcnow()))
        if wecom:
            rows.append(SysNotify(user_id=u.id, channel="wecom_app", msg_type=msg_type, title=title, content=content,
                                  link=link, ref_type=ref_type, ref_id=ref_id, send_status=0))
    db.add_all(rows)
    db.flush()
    pending = [r for r in rows if r.channel == "wecom_app"]
    if pending:
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
    # 同一内容批量发送（企微 touser 支持 | 分隔，最多 1000 人）
    groups: dict[tuple, list[SysNotify]] = {}
    for r in rows:
        groups.setdefault((r.title, r.content, r.link), []).append(r)
    try:
        provider = get_wecom()
    except Exception as exc:  # noqa: BLE001  凭证未配置等
        for r in rows:
            r.send_status, r.error_msg, r.retry_count = 2, str(exc)[:500], r.retry_count + 1
        return
    for (title, content, link), grp in groups.items():
        ids = [by_user[r.user_id].wecom_userid for r in grp]
        err = ""
        try:
            res = provider.send_app_message(ids, MessageDto(msg_type, title, content, _abs(link)))
        except Exception as exc:  # noqa: BLE001
            res = None
            err = str(exc)
        for r in grp:
            r.retry_count += 0 if res and res.ok else 1
            if res and res.ok and by_user[r.user_id].wecom_userid not in res.invalid_users:
                r.send_status, r.sent_at, r.error_msg = 1, utcnow(), None
            else:
                r.send_status = 2
                r.error_msg = (res.error if res else err) or "invalid user"
    db.flush()


def retry_failed(db: Session) -> int:
    rows = db.scalars(select(SysNotify).where(SysNotify.channel == "wecom_app", SysNotify.send_status.in_([0, 2]),
                                              SysNotify.retry_count < MAX_RETRY)).all()
    if rows:
        _deliver(db, list(rows))
    return len(rows)


def users_by_roles(db: Session, role_codes: Iterable[str]) -> list[SysUser]:
    return list(db.scalars(select(SysUser).join(SysUserRole, SysUserRole.user_id == SysUser.id)
                           .join(SysRole, SysRole.id == SysUserRole.role_id)
                           .where(SysRole.role_code.in_(list(role_codes)), SysUser.status == 1).distinct()))


def users_by_userids(db: Session, userids: Iterable[str]) -> list[SysUser]:
    return list(db.scalars(select(SysUser).where(SysUser.wecom_userid.in_(list(userids)))))


def alert_admins(db: Session, title: str, content: str) -> None:
    send(db, users_by_roles(db, ["ADMIN-01"]), "job_failed", title, content, link="/admin?tab=jobs")
    db.commit()

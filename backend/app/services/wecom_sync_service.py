"""企业微信个人数据同步：把已绑定账号的待办、日程拉取成本地快照。

企业微信对个人数据没有服务端推送，只能由本人授权后由服务端主动拉取，
因此这里的"实时"是分钟级增量：绑定后立即同步一次，之后每 10 分钟刷新，
用户打开页面时也可以点「立即同步」。数据按账号隔离，只有本人能读。
"""
import json
import time
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.response import BizError
from app.core.timeutil import TZ, now
from app.models import SysUser, SysWecomItem
from app.services import wecom_cli_service as cli
from app.services.xiaoju_tools import private_write

SERVICES = {"todo": "待办", "calendar": "日程"}
PULLERS: dict[str, object] = {}


def _stamp_path(user_id: int):
    return settings.storage_dir.parent / "wecom" / "sync" / f"{user_id}.json"


def _read_stamp(user_id: int) -> dict:
    try:
        data = json.loads(_stamp_path(user_id).read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _write_stamp(user_id: int, result: dict) -> None:
    private_write(_stamp_path(user_id), {"at": time.time(), "result": result})


def puller(name):
    def deco(fn):
        PULLERS[name] = fn
        return fn
    return deco


def _parse(value) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=TZ)
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=TZ)


def _moment(value) -> datetime | None:
    """兼容企业微信的多种时间表示：字符串、{"time": ...}、秒/毫秒时间戳。"""
    if isinstance(value, (int, float)):
        stamp = float(value)
        return datetime.fromtimestamp(stamp / 1000 if stamp > 1e11 else stamp, TZ)
    if isinstance(value, str):
        return _parse(value)
    if isinstance(value, dict):
        for key in ("time", "datetime", "date", "timestamp", "value", "s_timestamp"):
            if key in value:
                parsed = _moment(value[key])
                if parsed:
                    return parsed
    return None


@puller("todo")
def _todos(user_id: int) -> list[dict]:
    body, items = {}, []
    for _ in range(6):
        data = cli._run(user_id, ["todo", "list", "--json", json.dumps(body, ensure_ascii=False)])
        for item in data.get("items") or []:
            items.append({"external_id": str(item.get("todo_id") or ""), "title": item.get("title") or "未命名待办",
                          "summary": item.get("description"), "status": item.get("status") or item.get("user_status"),
                          "starts_at": None, "ends_at": _moment(item.get("deadline")), "link": None, "payload": item})
        cursor = data.get("next_cursor")
        if not data.get("has_more") or not cursor:
            break
        body["cursor"] = cursor
    return items


@puller("calendar")
def _schedules(user_id: int) -> list[dict]:
    begin = (now() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    end = (now() + timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
    data = cli._run(user_id, ["calendar", "schedules", "list", "--json",
                              json.dumps({"begin_time": begin, "end_time": end}, ensure_ascii=False)])
    items = []
    for item in data.get("schedule_list") or []:
        meeting = item.get("meeting") if isinstance(item.get("meeting"), dict) else {}
        items.append({"external_id": str(item.get("schedule_id") or ""), "title": item.get("subject") or "未命名日程",
                      "summary": item.get("description") or item.get("location"), "status": "scheduled",
                      "starts_at": _parse(item.get("begin_time")), "ends_at": _parse(item.get("end_time")),
                      "link": meeting.get("join_url") or meeting.get("url"), "payload": item})
    return items


def sync_user(db: Session, user_id: int) -> dict:
    if not cli.binding(user_id):
        return {"skipped": True, "message": "该账号尚未绑定企业微信个人连接"}
    result: dict = {}
    for service, pull in PULLERS.items():
        try:
            items = pull(user_id)
        except BizError as error:
            result[service] = {"error": error.message}
            continue
        rows = {row.external_id: row for row in db.scalars(
            select(SysWecomItem).where(SysWecomItem.user_id == user_id, SysWecomItem.service == service))}
        created = updated = 0
        seen = set()
        for item in items:
            if not item["external_id"]:
                continue
            seen.add(item["external_id"])
            row = rows.get(item["external_id"])
            if row is None:
                row = SysWecomItem(user_id=user_id, service=service, external_id=item["external_id"])
                db.add(row)
                created += 1
            else:
                updated += 1
            row.title, row.summary, row.status = item["title"][:200], (item["summary"] or None), item["status"]
            row.starts_at, row.ends_at, row.link = item["starts_at"], item["ends_at"], item["link"]
            row.payload, row.synced_at = item["payload"], now()
        if service == "todo":
            # 待办返回的是全量列表：本次没有返回的，标记为已移除，不做物理删除。
            for ident, row in rows.items():
                if ident not in seen and row.status != "removed":
                    row.status, row.synced_at = "removed", now()
        result[service] = {"items": len(items), "created": created, "updated": updated}
    db.commit()
    _write_stamp(user_id, result)
    return result


def sync_all(db: Session) -> dict:
    users = db.scalars(select(SysUser.id).where(SysUser.status == 1, SysUser.is_deleted.is_(False))).all()
    synced = rows = 0
    errors = []
    for user_id in users:
        if not cli.binding(user_id):
            continue
        try:
            result = sync_user(db, user_id)
        except Exception as error:  # noqa: BLE001 单个账号失败不影响其他人
            db.rollback()
            errors.append({"user_id": user_id, "error": str(error)[:120]})
            continue
        synced += 1
        rows += sum(item.get("items", 0) for item in result.values() if isinstance(item, dict))
    return {"rows": rows, "message": f"同步 {synced} 个已绑定账号，共 {rows} 条数据"
            + (f"；{len(errors)} 个账号失败" if errors else ""), "users": synced, "errors": errors[:5]}


def status(db: Session, user_id: int) -> dict:
    counts = dict(db.execute(select(SysWecomItem.service, func.count()).where(
        SysWecomItem.user_id == user_id).group_by(SysWecomItem.service)).all())
    last = db.scalar(select(func.max(SysWecomItem.synced_at)).where(SysWecomItem.user_id == user_id))
    stamp = _read_stamp(user_id)
    stamped = datetime.fromtimestamp(stamp["at"], TZ) if isinstance(stamp.get("at"), (int, float)) else None
    last_sync = max([value for value in (last, stamped) if value], default=None)
    return {"bound": bool(cli.binding(user_id)), "last_sync_at": last_sync,
            "counts": [{"service": key, "name": SERVICES[key], "items": counts.get(key, 0)} for key in SERVICES],
            "note": "企业微信个人数据不支持服务端推送，绑定后立即同步，此后每 10 分钟增量刷新"}


def items(db: Session, user_id: int, service: str = "", limit: int = 50) -> list[dict]:
    query = select(SysWecomItem).where(SysWecomItem.user_id == user_id,
                                       SysWecomItem.status != "removed")
    if service:
        query = query.where(SysWecomItem.service == service)
    rows = db.scalars(query.order_by(SysWecomItem.starts_at.desc().nullslast(),
                                     SysWecomItem.synced_at.desc()).limit(min(limit, 100))).all()
    return [{"id": row.id, "service": row.service, "title": row.title, "summary": row.summary,
             "status": row.status, "starts_at": row.starts_at, "ends_at": row.ends_at,
             "link": row.link, "synced_at": row.synced_at} for row in rows]

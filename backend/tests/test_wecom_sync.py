"""企业微信个人数据同步：落库、幂等、移除标记与账号隔离。"""
import uuid

import pytest
from sqlalchemy import delete, select

from app.core.config import settings
from app.integration.wecom.crypto import parse_contact_event
from app.models import SysUser, SysWecomItem
from app.services import wecom_cli_service as cli, wecom_sync_service as sync
from app.services.xiaoju_tools import private_bytes, private_write

TODO = {"items": [{"todo_id": "todo-1", "title": "提交项目周报", "description": "本周进度与风险",
                   "status": "pending", "deadline": {"time": "2026-10-25 18:00:00"}}], "has_more": False}
SCHEDULE = {"schedule_list": [{"schedule_id": "sch-1", "subject": "项目周会", "begin_time": "2026-10-24 10:00:00",
                               "end_time": "2026-10-24 11:00:00", "description": "会议室 A"}]}


def _bind(uid: int) -> None:
    private_write(cli.home(uid) / "binding.json",
                  {"user_id": uid, "binding_id": uuid.uuid4().hex, "services": []})
    private_bytes(cli.home(uid) / "config/credentials.enc", b"enc")
    private_bytes(cli.home(uid) / "config/.encryption_key", b"key")


@pytest.fixture
def synced(db, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "storage_dir", tmp_path / "var/files")
    uid = db.scalar(select(SysUser.id).where(SysUser.wecom_userid == "chenjing"))
    other = db.scalar(select(SysUser.id).where(SysUser.wecom_userid == "zhaoli"))
    db.execute(delete(SysWecomItem).where(SysWecomItem.user_id.in_([uid, other])))
    db.commit()
    _bind(uid)
    calls = []

    def fake_run(user_id, args):
        calls.append(args)
        return TODO if args[0] == "todo" else SCHEDULE

    monkeypatch.setattr(cli, "_run", fake_run)
    return uid, other, calls


def test_sync_writes_then_updates_without_duplicates(synced, db):
    uid, _, calls = synced
    first = sync.sync_user(db, uid)
    assert first["todo"] == {"items": 1, "created": 1, "updated": 0}
    assert first["calendar"] == {"items": 1, "created": 1, "updated": 0}

    second = sync.sync_user(db, uid)
    assert second["todo"] == {"items": 1, "created": 0, "updated": 1}
    rows = db.scalars(select(SysWecomItem).where(SysWecomItem.user_id == uid)).all()
    assert len(rows) == 2
    todo = next(row for row in rows if row.service == "todo")
    assert todo.title == "提交项目周报" and todo.ends_at is not None

    status = sync.status(db, uid)
    assert status["bound"] is True and status["last_sync_at"] is not None
    assert {item["service"]: item["items"] for item in status["counts"]} == {"todo": 1, "calendar": 1}
    assert calls and calls[0][0] == "todo"  # 确实通过 CLI 拉取过


def test_removed_todo_is_marked_not_deleted(synced, db, monkeypatch):
    uid, _, _ = synced
    sync.sync_user(db, uid)
    monkeypatch.setattr(cli, "_run", lambda user_id, args: {"items": [], "has_more": False}
                        if args[0] == "todo" else SCHEDULE)
    sync.sync_user(db, uid)
    todo = db.scalar(select(SysWecomItem).where(SysWecomItem.user_id == uid, SysWecomItem.service == "todo"))
    assert todo.status == "removed"
    assert all(item["service"] != "todo" for item in sync.items(db, uid))


def test_items_are_isolated_per_account(synced, db):
    uid, other, _ = synced
    sync.sync_user(db, uid)
    assert len(sync.items(db, uid)) == 2
    assert sync.items(db, other) == []


def test_status_records_sync_time_even_without_items(synced, db, monkeypatch):
    uid, _, _ = synced
    monkeypatch.setattr(cli, "_run", lambda user_id, args: {"items": [], "has_more": False}
                        if args[0] == "todo" else {"schedule_list": []})
    assert sync.sync_user(db, uid)["todo"]["items"] == 0
    status = sync.status(db, uid)
    assert status["last_sync_at"] is not None
    assert all(item["items"] == 0 for item in status["counts"])


def test_contact_event_parsed():
    xml = ("<xml><ToUserName>ww1</ToUserName><Event>change_contact</Event>"
           "<ChangeType>update_user</ChangeType><UserID>zhangsan</UserID></xml>")
    event = parse_contact_event(xml)
    assert event == {"change_type": "update_user", "user_id": "zhangsan", "department_id": "", "name": ""}
    assert parse_contact_event("<xml><Event>sys_approval_change</Event></xml>") is None

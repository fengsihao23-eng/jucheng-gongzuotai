from datetime import datetime

import pytest
from sqlalchemy import delete, select

from app.core.timeutil import TZ
from app.models import SysNotify, SysUser, WorkbookCollectionProgress, WorkbookCollectionReminder
from app.services import collection_reminders as reminders
from tests.conftest import data
from tests.test_business_workbook import source_only  # noqa: F401
from tests.test_workbook_reports_and_execution import rich_workbook  # noqa: F401

BASE = "/api/v1/business-workbook/collections"


@pytest.fixture
def reminder_source(db, rich_workbook):
    yield rich_workbook
    db.execute(delete(SysNotify).where(SysNotify.ref_type == reminders.REF))
    db.execute(delete(WorkbookCollectionReminder).where(WorkbookCollectionReminder.workbook_id == rich_workbook[0].id))
    db.commit()


def settings(client, login, db, reminder_source, cadence="daily"):
    wb, worker = reminder_source
    admin = db.scalar(select(SysUser.id).where(SysUser.wecom_userid == "admin"))
    path = BASE + "/items/sales-sheet/3"
    body = {"revision": wb.revision, "recipient_id": worker.id, "cadence": cadence, "due_on": "2026-11-17",
            "manager_ids": [admin], "expected_revision": 0}
    result = data(client.put(path + "/reminder", headers=login("admin"), json=body))
    return path, body, result["rule"]


@pytest.mark.parametrize("cadence,expected", [("daily", "2026-11-19"), ("weekly", "2026-11-25")])
def test_overdue_nodes_notify_once_and_feedback_rolls_deadline(client, login, db, reminder_source, cadence, expected):
    path, body, rule = settings(client, login, db, reminder_source, cadence)
    assert rule["overdue"]
    listed = data(client.get(BASE + "/reminders/overdue", headers=login("admin")))["items"]
    assert len(listed) == 1 and listed[0]["overdue_days"] == 1
    assert reminders.scan(db)["rows"] == 2
    db.commit()
    assert reminders.scan(db)["rows"] == 0
    notices = list(db.scalars(select(SysNotify).where(SysNotify.msg_type == reminders.NOTICE)))
    assert len(notices) == 4 and all(reminders.eligible(db, n) for n in notices)
    rule_row = db.get(WorkbookCollectionReminder, rule["id"])
    rule_row.updated_at = datetime(2026, 11, 17, 8, tzinfo=TZ)
    db.add(WorkbookCollectionProgress(request_id=rule["request_id"], author_id=body["recipient_id"], stage="awaiting",
           content="负责人已反馈进展与节点", expected_on=None, created_at=datetime(2026, 11, 18, 10, tzinfo=TZ)))
    db.commit()
    view = data(client.get(path + "/execution", headers=login("admin")))["reminder"]["rule"]
    assert view["next_due_on"] == expected and not view["overdue"]
    assert not reminders.scan(db)["rows"]
    assert all(not reminders.eligible(db, n) for n in notices)


def test_reminder_revision_permissions_and_paid_source_stop_delivery(client, login, db, reminder_source):
    path, body, rule = settings(client, login, db, reminder_source)
    assert client.put(path + "/reminder", headers=login("admin"), json=body).status_code == 409
    assert client.put(path + "/reminder", headers=login("xiekai"), json={**body, "expected_revision": 1}).status_code == 403
    reminders.scan(db)
    db.commit()
    wb, _ = reminder_source
    changed = [dict(s) for s in wb.sheets]
    changed[0] = {**changed[0], "rows": [{**r, "cells": [*r["cells"]]} for r in changed[0]["rows"]]}
    row = next(r for r in changed[0]["rows"] if r["row"] == 3)
    row["cells"][9] = 0
    wb.sheets = changed
    db.commit()
    assert data(client.get(BASE + "/reminders/overdue", headers=login("admin")))["items"] == []
    notice = db.scalar(select(SysNotify).where(SysNotify.msg_type == reminders.NOTICE).limit(1))
    assert not reminders.eligible(db, notice)

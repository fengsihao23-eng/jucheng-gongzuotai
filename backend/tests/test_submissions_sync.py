"""企微汇报同步：未授权时的提示、成功入库、定时任务日志。"""
from datetime import datetime

from app.integration.wecom.base import JournalDto
from app.integration.wecom.live import WeComApiError
from app.services import submission_service as ss


def test_sync_endpoint_turns_forbidden_into_actionable_message(client, login, monkeypatch):
    def boom(db, start, end):
        raise WeComApiError(48002, "api forbidden")

    monkeypatch.setattr(ss, "sync_journals", boom)
    resp = client.post("/api/v1/submissions/sync", json={"hours": 24}, headers=login("admin"))
    assert resp.status_code == 400
    body = resp.json()
    assert "48002" in body["message"] and "汇报" in body["message"]


def test_sync_endpoint_writes_submissions_and_is_idempotent(client, login, db, monkeypatch):
    class Fake:
        def get_journal_records(self, start, end):
            return [JournalDto("uuid-1", "zhangzhenhua", "周报", datetime.fromisoformat("2026-11-09T10:00:00+08:00"),
                               {"本周完成": "接通汇报"})]

    monkeypatch.setattr("app.integration.wecom.get_wecom", lambda: Fake())
    first = client.post("/api/v1/submissions/sync", json={"hours": 48}, headers=login("admin"))
    assert first.status_code == 200, first.text
    assert first.json()["data"]["inserted"] == 1
    again = client.post("/api/v1/submissions/sync", json={"hours": 48}, headers=login("admin"))
    assert again.json()["data"]["inserted"] == 0


def test_job_records_unavailable_permission_without_failing(db, monkeypatch):
    from app.jobs.registry import run_job

    def boom(db_, start, end):
        raise WeComApiError(48002, "api forbidden")

    monkeypatch.setattr(ss, "sync_journals", boom)
    entry = run_job("wecom_submission_sync", {"days": 3}, db=db)
    assert entry.status == "success" and entry.message.startswith("未同步")
    assert "48002" in entry.message

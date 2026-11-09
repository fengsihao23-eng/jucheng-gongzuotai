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


def test_journal_query_is_split_into_month_windows(monkeypatch):
    """企微汇报单次查询不能超过一个月，62 天要切成 3 段。"""
    from datetime import timedelta

    from app.integration.wecom.live import LiveWeCom

    calls = []

    def fake_call(self, method, path, **kwargs):
        if path.endswith("get_record_list"):
            calls.append((kwargs["json"]["starttime"], kwargs["json"]["endtime"]))
            return {"journaluuid_list": [], "endflag": 1}
        raise AssertionError(path)

    monkeypatch.setattr(LiveWeCom, "_call", fake_call)
    end = datetime.fromisoformat("2026-11-09T12:00:00+08:00")
    client = object.__new__(LiveWeCom)  # 跳过凭证校验，只验证切窗逻辑
    assert client.get_journal_records(end - timedelta(days=62), end) == []
    assert len(calls) == 3
    assert calls[0][0] < calls[0][1] <= calls[1][0]
    assert max(e - s for s, e in calls) <= 30 * 86400
    assert calls[-1][1] == int(end.timestamp())

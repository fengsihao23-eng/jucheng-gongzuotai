from datetime import datetime, timezone

import pytest

from app.core.response import BizError
from app.models import OpsTask
from tests.conftest import data
from tests.test_operations import BASE, project, report, task, uid
from tests.test_operations_monthly import monthly, propose, review


def test_deadline_outcomes_and_owner_rollup_use_original_deadline(client, login, db, project):
    late = task(client, login, db, project, due_date="2026-10-19")
    route = f"{BASE}/tasks/{late['id']}"
    moved = data(client.put(route, headers=login("wujun"), json={"due_date": "2026-10-26"}))
    assert (moved["original_due_date"], moved["due_date"], moved["deadline_status"], moved["overdue"]) == (
        "2026-10-19", "2026-10-26", "overdue", False)
    assert data(report(client, login, moved, status="done", incomplete_reason=""))["deadline_status"] == "late_done"
    on_time = task(client, login, db, project, due_date="2026-10-20")
    assert data(report(client, login, on_time, status="done"))["deadline_status"] == "on_time"
    row = db.get(OpsTask, on_time["id"])
    # UTC Oct 20 16:00 is already Oct 21 in Shanghai.
    row.completed_at = datetime(2026, 10, 20, 16, tzinfo=timezone.utc)
    db.commit()
    actual = data(client.get(f"{BASE}/tasks/{row.id}", headers=login("admin")))
    assert (actual["completed_on"], actual["deadline_status"]) == ("2026-10-21", "late_done")
    row.completed_at = datetime(2026, 10, 20, 15, 59, tzinfo=timezone.utc)
    db.commit()
    task(client, login, db, project, name="yangyue")
    propose(client, login, db, project)
    result = monthly(client, login, project)
    fields = ("total", "done", "on_time", "late_done", "pending")
    assert [result["stats"][key] for key in fields] == [3, 2, 1, 1, 1]
    by_owner = {owner["owner_id"]: [owner[key] for key in fields] for owner in result["owner_stats"]}
    assert by_owner == {uid(db, "xiekai"): [2, 2, 1, 1, 0], uid(db, "yangyue"): [1, 0, 0, 0, 1]}
    assert monthly(client, login, project, owner_id=uid(db, "yangyue"))["stats"]["on_time"] == 0


def test_delay_review_is_versioned_and_cannot_change_completion(client, login, db, project):
    t = task(client, login, db, project, due_date="2026-10-19")
    t = data(report(client, login, t, status="done", incomplete_reason=""))
    assert t["delay_status"] == "needs_reason"
    path = f"{BASE}/tasks/{t['id']}"

    def post(action, actor, **body):
        return client.post(path + "/" + action, headers=login(actor), json=body)

    assert post("delay-reason", "caofang", reason="不应写入", expected_revision=t["revision"]).status_code == 404
    first = data(post("delay-reason", "xiekai", reason="学校签字比预期晚", expected_revision=t["revision"]))
    first_id = first["delay_history"][0]["id"]
    request = {"reason_id": first_id, "decision": "rejected", "comment": "请补充实际签字时间", "expected_revision": first["revision"]}
    assert post("delay-review", "wujun", **request).status_code == 403
    assert post("delay-review", "xiekai", **request).status_code == 403
    assert len(monthly(client, login, project, "zhangzhenhua", delay_review_mine=True)["items"]) == 1
    assert monthly(client, login, project, "zhangzhenhua", month="2026-11", delay_review_mine=True)["items"] == []
    overview = data(client.get(BASE, headers=login("zhangzhenhua")))
    assert any(row["id"] == t["id"] for row in overview["delay_review_inbox"])
    rejected = data(post("delay-review", "zhangzhenhua", **request))
    assert rejected["delay_status"] == "rejected"
    assert post("delay-review", "zhangzhenhua", **request).status_code == 409
    revised = data(post("delay-reason", "xiekai", reason="学校在 10 月 20 日完成签字", expected_revision=rejected["revision"]))
    assert revised["delay_status"] == "pending" and revised["delay_history"][1]["decision"] == "rejected"
    current_request = {**request, "expected_revision": revised["revision"], "decision": "accepted", "comment": "已核对校方记录"}
    assert post("delay-review", "zhangzhenhua", **current_request).status_code == 409
    current_request["reason_id"] = revised["delay_history"][0]["id"]
    accepted = data(post("delay-review", "zhangzhenhua", **current_request))
    assert accepted["delay_status"] == "accepted" and accepted["delay_history"][0]["reviewer_name"]
    assert (accepted["status"], accepted["approval_status"], accepted["deadline_status"], accepted["completed_at"], accepted["original_due_date"]) == (
        "done", "approved", "late_done", t["completed_at"], "2026-10-19")
    assert monthly(client, login, project, "zhangzhenhua", delay_review_mine=True)["items"] == []


def test_self_plan_deadline_starts_at_first_company_approval(client, login, db, project):
    draft = data(propose(client, login, db, project))
    path = f"{BASE}/tasks/{draft['id']}"
    revised = data(client.put(path, headers=login("xiekai"), json={"due_date": "2026-10-27"}))
    assert revised["original_due_date"] is None
    dept = data(review(client, login, revised, "wujun"))
    accepted = data(review(client, login, dept, "zhangzhenhua"))
    assert accepted["original_due_date"] == "2026-10-27"
    revised = data(client.put(path, headers=login("xiekai"), json={"due_date": "2026-11-05"}))
    dept = data(review(client, login, revised, "wujun"))
    accepted = data(review(client, login, dept, "zhangzhenhua"))
    assert accepted["original_due_date"] == "2026-10-27" and accepted["due_date"] == "2026-11-05"


def test_xiaoju_delay_review_requires_permission_and_confirmation(client, login, db, project, tmp_path, monkeypatch):
    from app.core.config import settings
    from app.services import xiaoju_business as business

    monkeypatch.setattr(settings, "storage_dir", tmp_path / "var/files")
    t = task(client, login, db, project, due_date="2026-10-19")
    t = data(report(client, login, t, status="doing", incomplete_reason="等待校方复核"))
    args = {"path": {"task_id": t["id"]}, "body": {"reason_id": t["delay_history"][0]["id"], "decision": "accepted",
                                                "expected_revision": t["revision"]}}
    with pytest.raises(BizError, match="不能执行"):
        business.BusinessExecutor(uid(db, "xiekai")).execute("operations.review_operation_delay", args)
    reviewer = business.BusinessExecutor(uid(db, "zhangzhenhua"), intent="认可延期原因")
    pending = reviewer.execute("operations.review_operation_delay", args)
    assert pending["requires_confirmation"] and pending["action"]["targets"]["延期原因"] == "等待校方复核"
    route = f"{BASE}/tasks/{t['id']}"
    assert data(client.get(route, headers=login("admin")))["delay_status"] == "pending"
    result = business.confirm(pending["action"]["id"], reviewer.user_id)["result"]["data"]
    assert result["delay_status"] == "accepted" and result["deadline_status"] == "overdue"

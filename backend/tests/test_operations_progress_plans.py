import pytest

from app.core.response import BizError
from tests.conftest import data
from tests.test_operations import BASE, member, project, report, uid
from tests.test_operations_monthly import monthly


def test_project_progress_becomes_work_for_the_owner_and_joins_monthly_outcomes(client, login, db, project):
    base = f"{BASE}/{project['id']}"
    owner = login("wujun")
    old = data(client.post(base + "/progress", headers=owner, json={
        "progress": "原有周报", "next_plan": "历史计划", "next_due_date": "2026-10-15"}))
    company = data(client.post(base + "/tasks", headers=login("zhangzhenhua"), json={
        "title": "交付学校方案", "due_date": "2026-10-20", "owner_id": project["owner_id"]}))
    member(client, login, db, project)
    entries = [
        {"progress": "等校方确认", "problems": "场地数据未齐", "next_plan": "复核场地信息", "next_due_date": "2026-10-19"},
        {"progress": "方案初稿完成", "next_plan": "安排学校评审", "next_due_date": "2026-10-25"},
        {"progress": "本周培训已结束"},
    ]
    path = base + "/progress/entries"
    assert client.post(path, headers=login("xiekai"), json={"entries": entries}).status_code == 403
    assert client.post(path, headers=owner, json={"entries": entries + [{"progress": "缺日期", "next_plan": "先不保存"}]}).status_code == 400
    assert client.post(path, headers=owner, json={"entries": entries, "expected_owner_id": uid(db, "xiekai")}).status_code == 409
    assert [x["id"] for x in data(client.get(base + "/progress", headers=owner))["items"]] == [old["id"]]

    saved = data(client.post(path, headers=owner, json={"entries": entries}))
    late, pending = saved["created_task_ids"]
    assert [row["task_id"] for row in saved["items"]] == [late, pending, None]
    live = {t["id"]: t for t in data(client.get(base, headers=owner))["tasks"]}
    assert set(live) == {late, pending, company["id"]}
    assert (live[late]["plan_context"]["problems"], live[late]["owner_id"], live[late]["source"],
            live[late]["deadline_status"], live[late]["delay_status"]) == (
        "场地数据未齐", project["owner_id"], "project_plan", "overdue", "needs_reason")
    assert not live[pending]["last_feedback_at"]
    assert report(client, login, live[late], "xiekai").status_code == 403
    late_task = data(report(client, login, live[late], "wujun", status="done", content="场地信息复核完成",
                            incomplete_reason="校方晚一天提供数据", next_step=""))
    assert (late_task["deadline_status"], late_task["delay_status"]) == ("late_done", "pending")
    assert data(report(client, login, company, "wujun", status="done", next_step=""))["deadline_status"] == "on_time"
    accepted = data(client.post(f"{BASE}/tasks/{late}/delay-review", headers=login("zhangzhenhua"), json={
        "reason_id": late_task["delay_history"][0]["id"], "decision": "accepted", "expected_revision": late_task["revision"]}))
    assert accepted["deadline_status"] == "late_done"
    assert accepted["delay_status"] == "accepted"
    assert accepted["plan_context"]["progress"] == "等校方确认"
    brief = data(client.get(base + "/brief", headers=owner))["text"]
    assert all(label in brief for label in ("负责人计划：", "公司安排：", "场地信息复核完成", "本周培训已结束"))

    result = monthly(client, login, project)
    assert result["source_counts"] == {"project_plan": 2, "company": 1, "self": 0}
    rollup = result["owner_stats"][0]
    assert (rollup["owner_id"], rollup["total"], rollup["done"], rollup["on_time"], rollup["late_done"], rollup["pending"]) == (
        project["owner_id"], 3, 2, 1, 1, 1)
    assert {row["id"] for row in result["groups"][0]["project_plan_tasks"]} == {late, pending}
    filtered = monthly(client, login, project, source="project_plan")
    assert {row["id"] for row in filtered["items"]} == {late, pending}
    assert len(data(client.get(base + "/progress", headers=owner))["items"]) == 4


def test_xiaoju_confirms_new_progress_entries_using_project_permissions(client, login, db, project, tmp_path, monkeypatch):
    from app.core.config import settings
    from app.services import xiaoju_business as business

    monkeypatch.setattr(settings, "storage_dir", tmp_path / "var/files")
    member(client, login, db, project)
    operation = "operations.record_project_progress_entries"
    args = {"path": {"project_id": project["id"]}, "body": {"entries": [
        {"progress": "第一轮需求核对结束", "next_plan": "核查遗漏", "next_due_date": "2026-10-24"},
        {"progress": "已给校方发周报"},
    ]}}
    with pytest.raises(BizError, match="项目负责人"):
        business.BusinessExecutor(uid(db, "xiekai"), intent="填写项目进度").execute(operation, args)
    executor = business.BusinessExecutor(uid(db, "admin"), intent="填写项目进度和计划")
    preview = executor.execute(operation, args)
    assert preview["requires_confirmation"]
    assert preview["action"]["targets"]["新增负责人计划"] == 1
    assert preview["action"]["targets"]["项目负责人"] == project["owner_name"]
    assert not data(client.get(f"{BASE}/{project['id']}/progress", headers=login("wujun")))["items"]
    result = business.confirm(preview["action"]["id"], executor.user_id)["result"]
    assert result["ok"] and result["page"] == f"/operations/{project['id']}"
    task_id, = result["data"]["created_task_ids"]
    item = data(client.get(f"{BASE}/tasks/{task_id}", headers=login("wujun")))
    assert item["owner_id"] == project["owner_id"] and item["can_feedback"]

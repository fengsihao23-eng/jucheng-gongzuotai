import json
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.core.response import bad_request
from app.core.timeutil import today
from app.models import FeedbackAttachment, SysFile
from app.services import feedback_attachments
from tests.conftest import data, deletion_body

API = "/api/v1"
FILES = [
    ("files", ("现场记录", b"\x00\x91field notes\r\n", "application/octet-stream")),
    ("files", ("设计源文件.自定义", b"\x00\x08\x9dsource", "application/x-design")),
]


def submit(client, path, headers, body, files=FILES):
    return client.post(path, headers=headers, data={"payload": json.dumps(body)}, files=files)


def verify_download(client, headers, file, expected):
    name, content, _ = expected
    assert file["filename"] == name and file["size"] == len(content)
    response = client.get(file["url"], headers=headers)
    assert response.status_code == 200
    assert response.content == content
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["content-disposition"].startswith("attachment")
    assert response.headers["x-content-type-options"] == "nosniff"


def user_id(client, headers):
    return data(client.get(API + "/auth/me", headers=headers))["id"]


def project_task(client, leader, teammate_id, scope):
    owner = user_id(client, leader)
    due = (today() + timedelta(days=12)).isoformat()
    if scope == "operations":
        project = data(client.post(API + "/operations", headers=leader, json={"name": "任务附件测试", "owner_id": owner}))
        root = f"{API}/operations/{project['id']}"
        data(client.post(root + "/members", headers=leader, json={"user_id": teammate_id, "role": "viewer"}))
        task = data(client.post(root + "/tasks", headers=leader, json={"title": "附件任务", "owner_id": owner, "due_date": due}))
        return f"{API}/operations/tasks/{task['id']}", root + f"/members/{teammate_id}", None
    plan = {"owner_id": owner, "preparation_on": today().isoformat(), "due_on": due}
    project = data(client.post(f"{API}/market/projects?scope={scope}", headers=leader, json={**plan,
        "name": "任务附件测试", "team_member_ids": [teammate_id], "tasks": [{**plan, "title": "附件任务"}]}))
    market = f"{API}/market/projects/{project['project_code']}"
    ident = data(client.get(market, headers=leader, params={"scope": scope}))["tasks"][0]["id"]
    base = market if scope == "key" else f"{API}/projects/{project['project_code']}"
    return f"{base}/tasks/{ident}", market + f"/team/{teammate_id}?scope={scope}", ident


@pytest.mark.parametrize("scope", ["operations", "key", "general"])
def test_task_feedback_files_follow_the_current_team_permissions(client, login, scope):
    owner, teammate, outsider = (login(account) for account in ("wujun", "xuhao", "zhengyu"))
    task, remove_member, milestone = project_task(client, owner, user_id(client, teammate), scope)
    before = data(client.get(task, headers=owner))
    body = {"content": "现场已完成设备清点", "expected_revision": before["revision"],
            "status": "doing" if scope == "operations" else 2}
    assert submit(client, task + "/feedback", outsider, body).status_code in {403, 404}
    data(submit(client, task + "/feedback", owner, body))
    current = data(client.get(task, headers=teammate))
    if milestone is None:
        files = next(row["attachments"] for row in current["updates"] if row["kind"] == "feedback")
    else:
        files = current["history"][0]["feedback"]["attachments"]
        history = data(client.get(f"{API}/record-followups/key-task/{milestone}", headers=teammate))
        assert [file["id"] for file in history["legacy"][0]["attachments"]] == [file["id"] for file in files]
    assert len(files) == 2
    for file, (_, expected) in zip(files, FILES, strict=True):
        verify_download(client, teammate, file, expected)
        assert client.get(file["url"], headers=outsider).status_code == 404
        assert client.get(file["url"]).status_code == 401
        assert client.get(f"{API}/files/{file['id']}", headers=outsider).status_code == 404

    removal = client.request("DELETE", remove_member, headers=owner, json=deletion_body())
    assert removal.status_code == 200, removal.text
    assert client.get(files[0]["url"], headers=teammate).status_code == 404


@pytest.fixture
def operations_project(client, login):
    owner = login("wujun")
    project = data(client.post(API + "/operations", headers=owner, json={"name": "反馈附件验收项目", "owner_id": user_id(client, owner),
        "enabled_sections": ["tasks", "team", "progress", "outreach"]}))
    return owner, project["id"]


def test_shared_timeline_persists_attachments_once_and_keeps_text_only_entries(client, operations_project):
    owner, project_id = operations_project
    path = f"{API}/record-followups/operation/{project_id}"
    note = {"client_id": str(uuid4()), "action": "note", "content": "早前的纯文字过程记录"}
    assert data(client.post(path, headers=owner, json=note))["items"][0]["attachments"] == []
    body = {"client_id": str(uuid4()), "action": "progress", "content": "提交现场材料", "status": "doing", "expected_progress": "/"}
    first = data(submit(client, path, owner, body))
    retry = data(submit(client, path, owner, body))
    assert [row["id"] for row in first["items"]] == [row["id"] for row in retry["items"]]
    files = retry["items"][0]["attachments"]
    assert [f["id"] for f in first["items"][0]["attachments"]] == [f["id"] for f in files]
    assert retry["items"][1]["attachments"] == [] and len(files) == 2
    verify_download(client, owner, files[1], FILES[1][1])


def test_grouped_project_progress_and_school_visits_attach_files_to_each_record(client, operations_project, login):
    owner, project_id = operations_project
    path = f"{API}/operations/{project_id}"
    result = data(submit(client, path + "/progress/entries", owner, {"entries": [{"progress": "核对设备"}, {"progress": "整理现场照片"}]},
                         files=[FILES[0], ("files-1", FILES[1][1])]))
    history = {row["id"]: row for row in data(client.get(path + "/progress", headers=owner))["items"]}
    for index, row in enumerate(result["items"]):
        files = history[row["id"]]["attachments"]
        assert len(files) == 1
        verify_download(client, owner, files[0], FILES[index][1])
        assert client.get(files[0]["url"], headers=login("zhengyu")).status_code == 404

    school = data(client.post(path + "/schools", headers=owner, json={"name": "附件测试学校"}))
    visits = path + f"/schools/{school['id']}/visits"
    body = {"content": "提交学校沟通纪要", "occurred_on": today().isoformat(), "status": "doing", "client_id": str(uuid4())}
    created = data(submit(client, visits, owner, body))
    assert data(submit(client, visits, owner, body))["id"] == created["id"]
    [visit] = data(client.get(visits, headers=owner))["items"]
    assert visit["id"] == created["id"] and len(visit["attachments"]) == 2
    verify_download(client, owner, visit["attachments"][0], FILES[0][1])


def test_upload_failure_rolls_back_the_feedback_and_cleans_new_files(client, db, operations_project, monkeypatch):
    owner, project_id = operations_project
    path = f"{API}/record-followups/operation/{project_id}"
    files_dir = Path(settings.storage_dir) / feedback_attachments.CATEGORY
    before = set(files_dir.glob("*"))
    file_count = db.scalar(select(func.count(SysFile.id)).where(SysFile.category == feedback_attachments.CATEGORY))
    link_count = db.scalar(select(func.count(FeedbackAttachment.file_id)))
    save = feedback_attachments.save_bytes

    def fail_on_second(db, **kwargs):
        if kwargs["filename"] == FILES[1][1][0]:
            raise bad_request("模拟附件保存失败")
        return save(db, **kwargs)

    monkeypatch.setattr(feedback_attachments, "save_bytes", fail_on_second)
    body = {"client_id": str(uuid4()), "action": "progress", "content": "此次反馈不能部分入库", "status": "doing", "expected_progress": "/"}
    assert submit(client, path, owner, body).status_code == 400
    assert data(client.get(path, headers=owner))["items"] == []
    assert db.scalar(select(func.count(SysFile.id)).where(SysFile.category == feedback_attachments.CATEGORY)) == file_count
    assert db.scalar(select(func.count(FeedbackAttachment.file_id))) == link_count
    assert set(files_dir.glob("*")) == before

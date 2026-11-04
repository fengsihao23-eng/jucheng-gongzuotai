"""企业微信审批提交：模板控件解析、字段映射与提交。"""
import pytest

from app.integration.wecom.credentials import Gateway

TEMPLATE = {
    "template_list": [{
        "template_id": "T_EXP",
        "template_name": "费用报销",
        "template_content": {"controls": [
            {"id": "Text-1", "control": "Text",
             "property": {"title": "报销事由", "require": 1}, "value": {}},
            {"id": "Money-2", "control": "Money",
             "property": {"title": "报销金额", "require": 1}, "value": {}},
            {"id": "Selector-3", "control": "Selector",
             "property": {"title": "费用类型", "selector": {"type": "single", "options": [
                 {"key": "k1", "value": [{"text": "差旅费"}]},
                 {"key": "k2", "value": [{"text": "办公费"}]}]}}, "value": {}},
            {"id": "Date-4", "control": "Date",
             "property": {"title": "费用日期"}, "value": {}},
        ]},
    }],
}


@pytest.fixture
def approval(monkeypatch):
    calls = []

    def fake(cls, credential, method, path, **kwargs):
        calls.append((path, kwargs.get("json")))
        if path == "/cgi-bin/oa/gettemplateinfo":
            return TEMPLATE
        if path == "/cgi-bin/oa/applyevent":
            return {"errcode": 0, "sp_no": "202611040001"}
        raise AssertionError(path)

    monkeypatch.setattr(Gateway, "call", classmethod(fake))
    return calls


def _configure(client, login):
    response = client.put("/api/v1/wecom-approval/config", headers=login("admin"),
                          json={"expense": {"name": "费用报销", "template_id": "T_EXP"}})
    assert response.status_code == 200, response.text


def test_config_and_template_controls(client, login, approval):
    _configure(client, login)
    kinds = client.get("/api/v1/wecom-approval/kinds", headers=login("chenjing")).json()["data"]
    expense = next(row for row in kinds if row["kind"] == "expense")
    assert expense["configured"] is True and expense["can_submit"] is True

    detail = client.get("/api/v1/wecom-approval/template/expense", headers=login("chenjing")).json()["data"]
    assert [c["title"] for c in detail["controls"]] == ["报销事由", "报销金额", "费用类型", "费用日期"]


def test_submit_maps_values_and_audits(client, login, approval, db):
    _configure(client, login)
    response = client.post("/api/v1/wecom-approval/submit", headers=login("chenjing"), json={
        "kind": "expense",
        "values": {"报销事由": "南沙项目现场差旅", "报销金额": "1280.5",
                   "费用类型": "差旅费", "费用日期": "2026-10-28"},
    })
    body = response.json()["data"]
    assert body["sp_no"] == "202611040001"

    apply_body = next(data for path, data in approval if path == "/cgi-bin/oa/applyevent")
    contents = {row["id"]: row for row in apply_body["apply_data"]["contents"]}
    assert contents["Money-2"]["value"] == {"new_money": "1280.50"}
    assert contents["Selector-3"]["value"]["selector"]["options"][0]["value"][0]["text"] == "差旅费"
    assert contents["Date-4"]["value"]["date"]["type"] == "day"
    assert apply_body["creator_userid"]

    from app.models import LogOperation
    log = db.query(LogOperation).filter(LogOperation.action == "wecom.approval.submit") \
        .order_by(LogOperation.id.desc()).first()
    assert log and log.target_id == "202611040001"


def test_submit_without_template_explains(client, login, approval):
    # 清空配置：接口应给出可执行的提示，而不是空指针
    client.put("/api/v1/wecom-approval/config", headers=login("admin"),
               json={"expense": {"template_id": ""}})
    response = client.post("/api/v1/wecom-approval/submit", headers=login("chenjing"),
                           json={"kind": "expense", "values": {"报销事由": "x"}})
    assert response.json()["code"] == 40010
    assert "模板" in response.json()["message"]
    _configure(client, login)


def test_required_field_missing_is_rejected(client, login, approval):
    _configure(client, login)
    response = client.post("/api/v1/wecom-approval/submit", headers=login("chenjing"),
                           json={"kind": "expense", "values": {"报销事由": "只有事由"}})
    assert response.json()["code"] == 40001
    assert "报销金额" in response.json()["message"]


def test_user_without_module_cannot_submit(client, login, approval):
    _configure(client, login)
    response = client.get("/api/v1/wecom-approval/template/expense", headers=login("xuhao"))
    assert response.status_code == 403

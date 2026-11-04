"""企业微信接口中心：全量目录、凭证隔离、写操作确认与调用审计。"""
from app.integration.wecom import catalog
from app.integration.wecom.credentials import CREDENTIALS, Gateway


def _item(path: str) -> dict:
    return next(row for row in catalog.items() if row["path"] == path)


def test_catalog_covers_server_api_sections():
    stats = catalog.stats()
    assert stats["total"] >= 300
    sections = {node["section"] for node in catalog.tree()}
    assert {"通讯录管理", "客户联系", "微信客服", "打卡", "审批", "会议", "文档", "微盘"} <= sections
    # 方法与路径必须成对出现，GET 只读接口不能被标成写入
    assert _item("/cgi-bin/user/list")["method"] == "GET"
    assert _item("/cgi-bin/user/create")["method"] == "POST"
    assert _item("/cgi-bin/user/create")["credential"] == "contacts"


def test_catalog_items_have_unique_ids_and_paths():
    ids = [row["id"] for row in catalog.items()]
    assert len(ids) == len(set(ids))
    for row in catalog.items():
        assert row["path"].startswith("/cgi-bin/")
        assert row["doc_url"].startswith("https://")


def test_overview_requires_admin(client, login):
    assert client.get("/api/v1/wecom-console/overview", headers=login("admin")).status_code == 200
    assert client.get("/api/v1/wecom-console/overview", headers=login("chenjing")).status_code == 403


def test_catalog_endpoint_and_search(client, login):
    data = client.get("/api/v1/wecom-console/catalog", params={"keyword": "创建成员"},
                      headers=login("admin")).json()["data"]
    names = [row["name"] for node in data["tree"] for group in node["groups"] for row in group["items"]]
    assert any("创建成员" in name for name in names)
    assert data["stats"]["total"] >= 300


def test_call_records_audit_and_masks_request(client, login, monkeypatch, db):
    item = _item("/cgi-bin/user/list")
    monkeypatch.setattr(Gateway, "call", classmethod(lambda cls, *a, **k: {"errcode": 0, "userlist": [{"userid": "x"}]}))
    response = client.post("/api/v1/wecom-console/call", headers=login("admin"),
                           json={"id": item["id"], "params": {"department_id": "1"}, "confirm": True})
    body = response.json()["data"]
    assert body["ok"] is True and body["data"]["userlist"][0]["userid"] == "x"

    from app.models import SysWecomApiCall
    row = db.query(SysWecomApiCall).order_by(SysWecomApiCall.id.desc()).first()
    assert row.doc_path == item["id"] and row.ok is True
    assert row.request_summary == {"params": ["department_id"], "body": []}


def test_write_call_requires_confirmation(client, login, monkeypatch):
    item = _item("/cgi-bin/user/create")
    called = []
    monkeypatch.setattr(Gateway, "call", classmethod(lambda cls, *a, **k: called.append(1) or {"errcode": 0}))
    response = client.post("/api/v1/wecom-console/call", headers=login("admin"),
                           json={"id": item["id"], "payload": {"userid": "demo"}})
    assert response.status_code == 400 and response.json()["code"] == 40012
    assert not called


def test_unknown_endpoint_rejected(client, login):
    response = client.post("/api/v1/wecom-console/call", headers=login("admin"),
                           json={"id": "999999", "confirm": True})
    assert response.json()["code"] == 40404


def test_credential_status_never_returns_secret(client, login):
    data = client.get("/api/v1/wecom-console/credentials", headers=login("admin")).json()["data"]
    assert {row["name"] for row in data} == set(CREDENTIALS)
    assert all("secret" not in row for row in data)
    assert all(len(row["hint"]) <= 16 for row in data)


def test_unknown_credential_rejected(client, login):
    response = client.put("/api/v1/wecom-console/credentials/unknown", headers=login("admin"), json={"secret": "x"})
    assert response.json()["code"] == 40004


def test_call_with_unconfigured_credential_explains(client, login):
    item = _item("/cgi-bin/externalcontact/list")
    response = client.post("/api/v1/wecom-console/call", headers=login("admin"),
                           json={"id": item["id"], "params": {"userid": "x"}})
    # 未配置客户联系凭证时，网关在发请求前就给出可执行的提示
    assert response.json()["code"] == 40010
    assert "客户联系" in response.json()["message"]

"""企业微信客户联系：成员列表与客户读取（只读）。"""
from app.integration.wecom.credentials import Gateway


def test_overview_lists_members(client, login, monkeypatch):
    monkeypatch.setattr(Gateway, "call",
                        classmethod(lambda cls, *a, **k: {"follow_user": ["chenjing", "wujun"]}))
    data = client.get("/api/v1/wecom-external/overview", headers=login("wujun")).json()["data"]
    assert data["configured"] is True
    assert data["members"] == ["chenjing", "wujun"]


def test_customers_returns_business_fields(client, login, monkeypatch):
    def fake(cls, credential, method, path, **kwargs):
        assert credential == "external"
        assert kwargs["json"]["userid_list"] == ["wujun"]
        return {"external_contact_list": [{
            "external_contact": {"external_userid": "wmXXXX", "name": "南沙实验学校",
                                 "type": 2, "corp_name": "南沙实验学校"},
            "follow_info": {"remark": "教务处", "createtime": 1759999999,
                            "tags": [{"tag_name": "重点客户"}]},
        }]}

    monkeypatch.setattr(Gateway, "call", classmethod(fake))
    data = client.get("/api/v1/wecom-external/customers", params={"userid": "wujun"},
                      headers=login("wujun")).json()["data"]
    row = data["items"][0]
    assert row["external_userid"] == "wmXXXX"
    assert row["name"] == "南沙实验学校"
    assert row["tags"] == ["重点客户"]


def test_non_sales_cannot_read_customers(client, login):
    response = client.get("/api/v1/wecom-external/customers", params={"userid": "wujun"},
                          headers=login("chenjing"))
    assert response.status_code == 403

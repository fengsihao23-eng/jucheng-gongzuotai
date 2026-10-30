"""企微接入协议与完整入库衔接。远端回包用与官方 schema 对应的合成样例，不访问真实业务资料。"""
import io
import json
import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.core.response import BizError, forbidden
from app.models import ImportBatch, SysUser
from app.services import wecom_cli_service as cli, wecom_import_service as svc, wecom_import_formats as formats
from tests.conftest import data


@pytest.fixture
def remote(db, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "storage_dir", tmp_path / "files")
    uid = db.scalar(select(SysUser.id).where(SysUser.wecom_userid == "songjia"))
    manifest = {"binding_id": uuid.uuid4().hex, "user_id": uid}
    monkeypatch.setattr(cli, "_owned", lambda user_id, binding_id=None: manifest if user_id == uid and binding_id in (None, manifest["binding_id"]) else (_ for _ in ()).throw(forbidden("连接已变化")))
    calls = []
    responses = {}
    def fetch(user_id, method, payload, binding_id, workspace_id):
        assert user_id == uid and binding_id == manifest["binding_id"]
        calls.append((method, payload))
        reply = responses[method]
        return reply(payload, workspace_id) if callable(reply) else reply
    monkeypatch.setattr(cli, "import_read", fetch)
    return uid, manifest, calls, responses


def preview(client, headers, url, **extra):
    initial = data(client.post("/api/v1/imports/wecom/preview", headers=headers, json={"url": url, **extra}))
    assert initial["state"] == "queued"
    return data(client.get(f"/api/v1/imports/wecom/sources/{initial['id']}", headers=headers))


@pytest.mark.parametrize("url", ["http://doc.weixin.qq.com/doc/a", "https://doc.weixin.qq.com.evil.test/doc/a",
    "https://user:pass@doc.weixin.qq.com/doc/a", "https://doc.weixin.qq.com:8080/doc/a",
    "https://127.0.0.1/s?k=a", "file:///etc/passwd", "https://doc.weixin.qq.com/doc/a%2F..",
    "https://drive.weixin.qq.com/s", "https://page.weixin.qq.com/sheet/a"])
def test_only_specific_official_resource_links_are_accepted(url):
    with pytest.raises(BizError): svc.parse_link(url)


def test_document_body_can_enter_knowledge_with_source_and_private_default(remote, client, login):
    uid, _, calls, responses = remote
    responses["doc.contents.get"] = {"name": "运维巡检手册", "content": "交换机每周检查温度、端口与网络连通情况。", "version": 2}
    headers = login("songjia")
    source = preview(client, headers, "https://doc.weixin.qq.com/doc/d3_example")
    assert source["state"] == "ready" and "交换机" in source["preview_text"]
    imported = data(client.post("/api/v1/imports/wecom/confirm", headers=headers, json={"source_id": source["id"]}))
    batch = data(client.get(f"/api/v1/imports/{imported['id']}", headers=headers))
    assert batch["route"] == "kb" and batch["default_visibility"]["visibility"] == "private"
    assert batch["suggestion"]["external_source"]["url"].endswith("d3_example")
    assert batch["source_snapshot"] and client.get(batch["source_snapshot"]["url"], headers=headers).status_code == 200
    assert client.get(batch["source_snapshot"]["url"]).status_code == 401
    doc = data(client.post(f"/api/v1/imports/{batch['id']}/confirm-kb", headers=headers, json={"category": "客服"}))
    assert doc["kb_doc"]["visibility"] == "private" and not doc["ready"]
    repeated = data(client.post("/api/v1/imports/wecom/confirm", headers=headers, json={"source_id": source["id"]}))
    assert repeated["id"] == batch["id"] and repeated["reused"]


def test_smartpage_fetches_each_page_and_keeps_subpage_text(remote, client, login):
    _, _, calls, responses = remote
    def pages(payload, workspace_id):
        if not payload.get("page_id"):
            return {"doc_title": "设备巡检说明", "rev": 3, "pages": [
                {"page_id": "root", "page_title": "总则"}, {"page_id": "child", "page_title": "详细步骤", "parent_id": "root"}]}
        return {"rev": 3, "pages": [{"page_id": payload["page_id"], "content_file_inner": f"{payload['page_id']}：设备检查与排查的具体步骤。"}]}
    responses["smartpage.pages.get"] = pages
    result = preview(client, login("songjia"), "https://doc.weixin.qq.com/smartpage/a1_example")
    assert result["state"] == "ready" and result["page_count"] == 2
    assert "child" in result["preview_text"] and "详细步骤" in result["preview_text"]
    assert "内嵌数据表" in result["warnings"][0]
    assert len(calls) == 3


def test_smartpage_revision_change_fails_without_creating_batch(remote, client, login, db):
    _, _, _, responses = remote
    responses["smartpage.pages.get"] = lambda p, w: {"doc_title": "更新中", "rev": 2 if p.get("page_id") else 1,
        "pages": [{"page_id": "root", "content_file_inner": "更新中的数据不能被当成完整快照。"}]}
    before = db.scalar(select(func.count(ImportBatch.id)))
    result = preview(client, login("songjia"), "https://page.weixin.qq.com/smartpage/b1_example")
    assert result["state"] == "failed" and "更新" in result["message"]
    db.expire_all()
    assert db.scalar(select(func.count(ImportBatch.id))) == before


def test_disk_download_stays_inside_user_request_workspace(remote, client, login):
    uid, _, _, responses = remote
    responses["disk.files.get"] = {"file": {"id": "f-1", "type": "file", "file_name": "巡检资料.txt", "file_size": 50}}
    def download(payload, source_id):
        target = cli.home(uid) / "work/imports" / source_id / "download.txt"
        target.parent.mkdir(parents=True)
        target.write_bytes("仅供测试的运维巡检记录和异常处理说明。".encode())
        return {"file_path": str(target)}
    responses["disk.files.download"] = download
    result = preview(client, login("songjia"), "https://drive.weixin.qq.com/s?k=test_file")
    assert result["state"] == "ready" and result["byte_size"] > 0
    assert "file_path" not in result


def test_untrusted_cli_local_path_is_never_read(remote, client, login):
    _, _, _, responses = remote
    responses["disk.files.get"] = {"file": {"id": "f-1", "type": "file", "file_name": "资料.txt"}}
    responses["disk.files.download"] = {"file_path": "/etc/passwd"}
    result = preview(client, login("songjia"), "https://drive.weixin.qq.com/s?k=test_file")
    assert result["state"] == "failed" and "本人读取目录" in result["message"]


def test_large_table_json_response_is_materialized_only_from_owned_workspace(remote):
    uid, manifest, _, responses = remote
    source = svc._new(svc._principal(uid), kind="smartsheet", docid="s3_example")
    def large(payload, source_id):
        target = cli.home(uid) / "work/imports" / source_id / "records.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({"records": [{"record_id": "r1", "values": {"数量": 0}}], "has_more": False}))
        return {"file_path": str(target)}
    responses["smartsheet.records.list"] = large
    result = svc._call(source, "smartsheet.records.list", {"docid": "s3_example"})
    assert result["records"][0]["values"]["数量"] == 0


def test_online_document_in_drive_is_read_as_document_not_binary(remote, client, login):
    _, _, calls, responses = remote
    responses["disk.files.get"] = {"file": {"id": "f-online", "type": "word", "file_name": "巡检文档", "docid": "d3_online", "doc_url": "https://doc.weixin.qq.com/doc/d3_online"}}
    responses["doc.contents.get"] = {"name": "巡检文档", "content": "运维巡检文档包含设备排查流程和维护记录。"}
    result = preview(client, login("songjia"), "https://drive.weixin.qq.com/s?k=online_doc")
    assert result["state"] == "ready" and result["kind"] == "word"
    assert [name for name, _ in calls] == ["disk.files.get", "doc.contents.get"]


def smart_responses(responses, *, complex_field=False):
    fields = [{"field_id": f"f{i}", "field_title": title, "field_type": kind} for i, (title, kind) in enumerate(
        [("客户", "text"), ("合同金额", "currency"), ("签约日期", "date_time"), ("业务线", "single_select"), ("备注", "attachment" if complex_field else "text")])]
    responses["smartsheet.sheets.list"] = {"name": "合同台账", "sheets": [{"sheet_id": "business", "title": "销售合同", "type": "smartsheet", "record_count": 2}, {"sheet_id": "dashboard", "title": "看板", "type": "dashboard"}]}
    responses["smartsheet.fields.list"] = {"fields": fields, "has_more": False}
    tag = uuid.uuid4().hex[:8]
    def records(payload, sid):
        second = bool(payload.get("cursor"))
        values = {"客户": "企微合成客户" + tag + ("B" if second else "A"), "合同金额": 100 if second else 200,
                  "签约日期": "2026-10-20 00:00:00", "业务线": [{"id": "xy", "text": "XY"}],
                  "备注": [{"title": "合同附件.pdf", "fileUrl": "https://example.invalid/file"}] if complex_field else "合成样例"}
        return {"records": [{"record_id": "r2" if second else "r1", "values": values}], "total": 2,
                "has_more": not second, "next_cursor": "second" if not second else ""}
    responses["smartsheet.records.list"] = records


def select_table(client, headers, source):
    initial = data(client.post("/api/v1/imports/wecom/preview", headers=headers,
                              json={"source_id": source["id"], "table_id": "business"}))
    return data(client.get(f"/api/v1/imports/wecom/sources/{initial['id']}", headers=headers))


def test_smart_table_requires_real_subtable_and_reads_all_pages(remote, client, login):
    _, _, calls, responses = remote
    smart_responses(responses)
    headers = login("songjia")
    catalog = preview(client, headers, "https://doc.weixin.qq.com/smartsheet/s3_example")
    assert catalog["state"] == "choose_table" and len(catalog["tables"]) == 1
    result = select_table(client, headers, catalog)
    assert result["state"] == "ready" and result["row_count"] == 2
    assert len([1 for name, _ in calls if name == "smartsheet.records.list"]) == 2
    imported = data(client.post("/api/v1/imports/wecom/confirm", headers=headers, json={"source_id": result["id"]}))
    batch = data(client.get(f"/api/v1/imports/{imported['id']}", headers=headers))
    assert batch["clean_job"] and batch["suggestion"]["external_source"]["row_count"] == 2
    job = data(client.post(f"/api/v1/clean/jobs/{batch['clean_job']['id']}/import", headers=headers))
    assert job["stats"]["imported"] == 2


def test_complex_columns_are_kept_raw_and_block_unsafe_ledger_mapping(remote, client, login):
    _, _, _, responses = remote
    smart_responses(responses, complex_field=True)
    headers = login("songjia")
    source = select_table(client, headers, preview(client, headers, "https://doc.weixin.qq.com/smartsheet/s3_example"))
    assert source["warnings"]
    batch = data(client.post("/api/v1/imports/wecom/confirm", headers=headers, json={"source_id": source["id"]}))
    batch = data(client.get(f"/api/v1/imports/{batch['id']}", headers=headers))
    raw = client.get(batch["source_snapshot"]["url"], headers=headers).json()
    assert raw["records"][0]["values"]["备注"][0]["title"] == "合同附件.pdf"
    job_id = batch["clean_job"]["id"]
    refused = client.post(f"/api/v1/clean/jobs/{job_id}/import", headers=headers)
    assert refused.status_code == 400 and "复杂列" in refused.json()["message"]
    data(client.post(f"/api/v1/clean/jobs/{job_id}/remap", headers=headers, json={"mapping": {"4": None}}))
    assert data(client.post(f"/api/v1/clean/jobs/{job_id}/import", headers=headers))["stats"]["imported"] == 2


def test_incomplete_or_repeated_pagination_is_rejected():
    with pytest.raises(BizError, match="游标"):
        formats.paginate(lambda *a: {"records": [{}], "has_more": True, "next_cursor": "same"}, "x", {}, "records")
    with pytest.raises(BizError, match="总数"):
        formats.paginate(lambda *a: {"records": [], "total": 5, "has_more": False}, "x", {}, "records")


def test_online_table_reads_explicit_ranges_and_preserves_false_zero(remote, client, login):
    _, _, _, responses = remote
    responses["sheet.workbook.get"] = {"sheets": [{"sheet_id": "online", "name": "原始数据", "range": "A1:B3"}]}
    responses["sheet.cells.get"] = {"actual_range": "A1:B3", "truncated": False,
        "cells": [[{"value": "名称"}, {"value": "数量"}], [{"value": "零值"}, {"value": 0}], [{"value": "布尔"}, {"value": False}]]}
    headers = login("songjia")
    source = preview(client, headers, "https://doc.weixin.qq.com/sheet/e3_example")
    next_ = data(client.post("/api/v1/imports/wecom/preview", headers=headers, json={"source_id": source["id"], "table_id": "online"}))
    loaded = data(client.get(f"/api/v1/imports/wecom/sources/{next_['id']}", headers=headers))
    assert loaded["state"] == "ready" and loaded["sample_rows"][0][1] == 0 and loaded["sample_rows"][1][1] is False


def test_online_table_does_not_treat_hidden_or_truncated_cells_as_empty(remote, client, login):
    _, _, _, responses = remote
    responses["sheet.workbook.get"] = {"sheets": [{"sheet_id": "online", "name": "受限数据", "range": "A1:B3"}]}
    responses["sheet.cells.get"] = {"truncated": False, "not_viewable_cells": ["B2"], "cells": []}
    headers = login("songjia")
    source = preview(client, headers, "https://doc.weixin.qq.com/sheet/e3_example")
    next_ = data(client.post("/api/v1/imports/wecom/preview", headers=headers, json={"source_id": source["id"], "table_id": "online"}))
    loaded = data(client.get(f"/api/v1/imports/wecom/sources/{next_['id']}", headers=headers))
    assert loaded["state"] == "failed" and "无权查看" in loaded["message"]


def test_source_permission_is_rechecked_before_capture(remote, client, login):
    _, _, _, responses = remote
    responses["doc.contents.get"] = {"name": "已撤权资料", "content": "只允许原授权期间读取的资料内容。", "version": 1}
    headers = login("songjia")
    source = preview(client, headers, "https://doc.weixin.qq.com/doc/d3_example")
    def revoked(*args): raise forbidden("企微已撤销此资料权限")
    responses["doc.contents.get"] = revoked
    assert client.post("/api/v1/imports/wecom/confirm", headers=headers, json={"source_id": source["id"]}).status_code == 403


def test_sources_and_confirm_cannot_cross_accounts_or_changed_binding(remote, client, login):
    _, manifest, _, responses = remote
    responses["doc.contents.get"] = {"name": "本人文档", "content": "本人工作资料及需要审核的具体内容。"}
    owner = login("songjia")
    source = preview(client, owner, "https://doc.weixin.qq.com/doc/d3_example")
    assert client.get(f"/api/v1/imports/wecom/sources/{source['id']}", headers=login("admin")).status_code == 404
    assert client.post("/api/v1/imports/wecom/confirm", headers=login("admin"), json={"source_id": source["id"]}).status_code == 404
    manifest["binding_id"] = uuid.uuid4().hex
    assert client.post("/api/v1/imports/wecom/confirm", headers=owner, json={"source_id": source["id"]}).status_code == 403


def test_missing_feature_authorization_is_visible_but_no_import_is_created(remote, client, login):
    _, _, _, responses = remote
    def denied(*args): raise BizError(40301, "需要开通文档内容权限", data={"authorization_url": "https://work.weixin.qq.com/auth"})
    responses["doc.contents.get"] = denied
    source = preview(client, login("songjia"), "https://doc.weixin.qq.com/doc/d3_example")
    assert source["state"] == "failed" and source["authorization_url"].startswith("https://work.weixin.qq.com/")
    assert client.post("/api/v1/imports/wecom/confirm", headers=login("songjia"), json={"source_id": source["id"]}).status_code == 409


def test_same_remote_table_snapshot_is_reused_across_fresh_previews(remote, client, login):
    _, _, _, responses = remote
    smart_responses(responses)
    headers = login("songjia")
    first = select_table(client, headers, preview(client, headers, "https://doc.weixin.qq.com/smartsheet/s3_example"))
    one = data(client.post("/api/v1/imports/wecom/confirm", headers=headers, json={"source_id": first["id"]}))
    second = select_table(client, headers, preview(client, headers, "https://doc.weixin.qq.com/smartsheet/s3_example"))
    two = data(client.post("/api/v1/imports/wecom/confirm", headers=headers, json={"source_id": second["id"]}))
    assert two["reused"] and two["id"] == one["id"]

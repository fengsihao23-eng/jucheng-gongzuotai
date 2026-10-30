"""经理资料接入：真实状态、原件隔离、后台识别、多表核对和知识发布闭环。"""
import io
import uuid
import zipfile
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.security import create_jwt
from app.core.timeutil import utcnow
from app.models import ImportBatch, SysDepartment, SysRole, SysUser
from tests.conftest import data
from tests.test_kb_imports import _docx, _import, _upload, _xlsx


@pytest.fixture
def account(db):
    def create(dept="SC", *, handler=False, scope="self"):
        tag = uuid.uuid4().hex[:8]
        role = SysRole(role_code=f"T-{tag}", name="资料测试角色", dept_code=dept, permissions={
            "menus": ["clean"], "buttons": ["clean:import", *(["imports:handle"] if handler else [])],
            "data_scope": scope})
        user = SysUser(wecom_userid=f"intake-{tag}", name=f"测试经理{tag}", status=1,
                       department_id=db.scalar(select(SysDepartment.id).where(SysDepartment.dept_code == dept)),
                       roles=[role])
        db.add(user)
        db.commit()
        token, _ = create_jwt(user.id)
        return user, {"Authorization": f"Bearer {token}"}
    return create


def test_raw_import_file_is_not_accessible_by_id_or_shared_signature(client, account):
    _, owner = account()
    _, stranger = account()
    batch = data(_import(client, owner, _docx("项目验收资料仅由本人及授权整理人处理。" + uuid.uuid4().hex), "验收资料.docx"))
    file = batch["file"]
    assert client.get(f"/api/v1/files/{file['id']}", headers=stranger).status_code == 404
    assert client.get(file["url"], headers=stranger).status_code == 404
    assert client.get(file["url"]).status_code == 401
    assert client.get(file["url"], headers=owner).status_code == 200


def test_department_handler_cannot_read_other_departments_pool(client, account):
    _, owner = account("SC")
    _, handler = account("RS", handler=True, scope="dept")
    _, same_department = account("RS")
    foreign = data(_import(client, owner, uuid.uuid4().bytes, "外部门扫描件.png"))
    local = data(_import(client, same_department, uuid.uuid4().bytes, "本部门扫描件.png"))
    listing = data(client.get("/api/v1/imports?scope=pool", headers=handler))
    assert local["id"] in [row["id"] for row in listing["items"]]
    assert foreign["id"] not in [row["id"] for row in listing["items"]]
    assert client.get(f"/api/v1/imports/{foreign['id']}", headers=handler).status_code == 404
    assert client.get(foreign["file"]["url"], headers=handler).status_code == 404
    assert client.post(f"/api/v1/imports/{foreign['id']}/dismiss", json={"note": "处理"}, headers=handler).status_code == 404


def test_clean_jobs_and_reports_are_isolated_even_with_import_permission(client, account):
    _, owner = account()
    _, stranger = account(scope="all")
    content = _xlsx([["客户", "合同金额", "签约日期", "业务线"], [uuid.uuid4().hex, 100, "2026-10-20", "XY"]])
    batch = data(_import(client, owner, content, "合同台账.xlsx"))
    job_id = batch["clean_job"]["id"]
    assert client.get(f"/api/v1/clean/jobs/{job_id}", headers=stranger).status_code == 404
    assert job_id not in [j["id"] for j in data(client.get("/api/v1/clean/jobs", headers=stranger))["items"]]
    for action in ("remap", "import", "cancel", "report"):
        assert client.post(f"/api/v1/clean/jobs/{job_id}/{action}", json={}, headers=stranger).status_code == 404
    report = data(client.post(f"/api/v1/clean/jobs/{job_id}/report", headers=owner))["report_file"]
    assert client.get(report["url"], headers=owner).status_code == 200
    assert client.get(report["url"], headers=stranger).status_code == 404
    assert client.get(report["url"]).status_code == 401


def test_handler_cannot_bypass_unassigned_import_via_clean_copy(client, account):
    _, owner = account()
    _, handler = account(handler=True, scope="all")
    content = _xlsx([["客户", "合同金额", "签约日期", "业务线"], [uuid.uuid4().hex, 200, "2026-10-20", "XY"]])
    batch = data(_import(client, owner, content, "合同台账.xlsx"))
    job_id = batch["clean_job"]["id"]
    own_job = data(client.get(f"/api/v1/clean/jobs/{job_id}", headers=owner))
    assert client.get(f"/api/v1/clean/jobs/{job_id}", headers=handler).status_code == 404
    assert client.get(own_job["file"]["url"], headers=handler).status_code == 404
    data(client.post(f"/api/v1/imports/{batch['id']}/to-pool", headers=owner, json={"note": "请协助整理"}))
    assert client.get(f"/api/v1/clean/jobs/{job_id}", headers=handler).status_code == 200


def test_knowledge_defaults_to_department_and_new_link_has_real_id(client, login):
    doc = data(_upload(client, login("dengxin"), _docx("人事内部资料" + uuid.uuid4().hex), "内部制度.docx", category="制度"))
    assert doc["visibility"] == "dept" and doc["visible_depts"] == ["RS"]
    assert doc["url"].endswith(f"doc={doc['id']}")
    assert client.get(doc["file"]["url"], headers=login("wujun")).status_code == 404


def test_old_signed_link_and_versions_follow_current_visibility(client, login):
    owner, reader = login("dengxin"), login("wujun")
    doc = data(_upload(client, owner, _docx("跨部门共享资料" + uuid.uuid4().hex), "共享制度.docx",
                       category="制度", visibility="dept", visible_depts="SC"))
    original = doc["file"]["url"]
    assert client.get(original, headers=reader).status_code == 200
    version = data(client.post(f"/api/v1/kb/docs/{doc['id']}/version", headers=owner,
                              files={"file": ("共享制度.docx", _docx("共享资料修订版" + uuid.uuid4().hex))}))
    assert len(version["versions"]) == 2
    data(client.put(f"/api/v1/kb/docs/{doc['id']}", headers=owner, json={"visibility": "private"}))
    assert client.get(original, headers=reader).status_code == 404
    assert client.get(version["file"]["url"], headers=reader).status_code == 404
    assert client.get(original, headers=owner).status_code == 200


def test_invisible_duplicate_does_not_disclose_private_title_or_id(client, login):
    content = _docx("独立保存的机密资料" + uuid.uuid4().hex)
    first = data(_upload(client, login("dengxin"), content, "保密薪资.docx", category="制度", visibility="private"))
    second = data(_upload(client, login("wujun"), content, "我的副本.docx", category="制度", visibility="private"))
    assert first["id"] != second["id"] and second["title"] == "我的副本"


def test_handler_submission_keeps_original_owner_and_reports_review_stage(client, login):
    owner, handler = login("zengli"), login("songjia")
    batch = data(_import(client, owner, _docx("设备日常巡检要求及记录方法" + uuid.uuid4().hex), "巡检规范.docx"))
    # 整理人不能浏览经理尚未转交的原始资料；明确转交后才可代办。
    assert client.get(f"/api/v1/imports/{batch['id']}", headers=handler).status_code == 404
    assert client.get(batch["file"]["url"], headers=handler).status_code == 404
    data(client.post(f"/api/v1/imports/{batch['id']}/to-pool", headers=owner, json={"note": "请协助归类"}))
    data(client.post(f"/api/v1/imports/{batch['id']}/to-kb", headers=handler))
    submitted = data(client.post(f"/api/v1/imports/{batch['id']}/confirm-kb", headers=handler, json={"category": "客服"}))
    assert submitted["stage"] == "review" and not submitted["ready"]
    doc_id = submitted["kb_doc"]["id"]
    doc = data(client.get(f"/api/v1/kb/docs/{doc_id}", headers=owner))
    assert doc["uploader_id"] == batch["uploader_id"]
    assert doc["visible_depts"] == batch["default_visibility"]["visible_depts"]
    data(client.post(f"/api/v1/kb/docs/{doc_id}/review", headers=login("huangqi"), json={"approve": True}))
    published = data(client.get(f"/api/v1/imports/{batch['id']}", headers=owner))
    assert published["ready"] and published["stage"] == "ready"
    data(client.post(f"/api/v1/imports/{batch['id']}/withdraw", headers=owner))


def test_rejected_knowledge_shows_current_reason_not_stale_submitted_note(client, login):
    owner = login("zengli")
    batch = data(_import(client, owner, _docx("客户设备操作指南" + uuid.uuid4().hex), "设备操作指南.docx"))
    result = data(client.post(f"/api/v1/imports/{batch['id']}/confirm-kb", headers=owner, json={"category": "客服"}))
    data(client.post(f"/api/v1/kb/docs/{result['kb_doc']['id']}/review", headers=login("huangqi"),
                     json={"approve": False, "note": "请补充设备型号"}))
    result = data(client.get(f"/api/v1/imports/{batch['id']}", headers=owner))
    assert result["stage"] == "rejected" and "设备型号" in result["next_step"] and not result["ready"]


def test_background_upload_returns_saved_state_then_becomes_confirmable(client, login):
    headers = login("zengli")
    result = data(client.post("/api/v1/imports", headers=headers, data={"background": "true"},
                              files={"file": ("巡检说明.txt", ("运维巡检规范" + uuid.uuid4().hex).encode())}))
    assert result["stage"] == "processing"
    final = data(client.get(f"/api/v1/imports/{result['id']}", headers=headers))
    assert final["stage"] == "confirmation" and final["suggestion"]["summary"]
    assert not final["ready"]


def test_background_failure_preserves_file_and_can_retry(client, login, monkeypatch):
    from app.services import import_service
    original = import_service._classify_batch
    def fail(*args):
        raise RuntimeError("simulated parser failure")
    monkeypatch.setattr(import_service, "_classify_batch", fail)
    headers = login("zengli")
    batch = data(client.post("/api/v1/imports", headers=headers, data={"background": "true"},
                             files={"file": ("资料.txt", ("制度资料正文" + uuid.uuid4().hex).encode())}))
    failed = data(client.get(f"/api/v1/imports/{batch['id']}", headers=headers))
    assert failed["stage"] == "needs_help" and "retry" in failed["actions"]
    assert client.get(failed["file"]["url"], headers=headers).status_code == 200
    monkeypatch.setattr(import_service, "_classify_batch", original)
    data(client.post(f"/api/v1/imports/{batch['id']}/retry", headers=headers))
    assert data(client.get(f"/api/v1/imports/{batch['id']}", headers=headers))["stage"] == "confirmation"


def test_interrupted_queue_can_retry_but_never_revives_withdrawn_batch(client, login, monkeypatch, db):
    from app.services import import_service
    worker = import_service.process_many
    monkeypatch.setattr(import_service, "process_many", lambda *args: None)
    headers = login("zengli")
    batch = data(client.post("/api/v1/imports", headers=headers, data={"background": "true"},
                             files={"file": ("资料.txt", ("待识别文档正文" + uuid.uuid4().hex).encode())}))
    assert client.post(f"/api/v1/imports/{batch['id']}/retry", headers=headers).status_code == 409
    record = db.get(ImportBatch, batch["id"])
    record.updated_at = utcnow() - timedelta(minutes=6)
    db.commit()
    data(client.post(f"/api/v1/imports/{batch['id']}/retry", headers=headers))
    data(client.post(f"/api/v1/imports/{batch['id']}/withdraw", headers=headers))
    worker(batch["uploader_id"], [batch["id"]])
    assert data(client.get(f"/api/v1/imports/{batch['id']}", headers=headers))["status"] == "withdrawn"


def test_zip_preserves_each_file_and_reports_skipped_duplicates(client, login):
    output = io.BytesIO()
    content = ("巡检注意事项和安全检查" + uuid.uuid4().hex).encode()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("部门/资料.txt", content)
        archive.writestr("副本.txt", content)
        archive.writestr("空文件.txt", b"")
    batch = data(client.post("/api/v1/imports", headers=login("zengli"), data={"background": "true"},
                             files={"file": ("部门资料.zip", output.getvalue())}))
    assert len(batch["batches"]) == 1 and len(batch["skipped"]) == 2
    assert batch["batches"][0]["archive_name"] == "部门资料.zip"


def test_multi_sheet_requires_explicit_selection_and_imports_only_that_sheet(client, login):
    from openpyxl import Workbook
    wb = Workbook()
    wb.active.title = "本月合同"
    second = wb.create_sheet("下月合同")
    for sheet in (wb.active, second):
        sheet.append(["客户", "合同金额", "签约日期", "业务线"])
        sheet.append([sheet.title + uuid.uuid4().hex, 300, "2026-10-20", "XY"])
    buffer = io.BytesIO()
    wb.save(buffer)
    headers = login("songjia")
    batch = data(_import(client, headers, buffer.getvalue(), "合同台账.xlsx"))
    job_id = batch["clean_job"]["id"]
    assert client.post(f"/api/v1/clean/jobs/{job_id}/import", headers=headers).status_code == 400
    preview = data(client.post(f"/api/v1/clean/jobs/{job_id}/remap", headers=headers, json={"sheet_name": "下月合同"}))
    assert preview["stats"]["sheet_confirmed"] and preview["stats"]["selected_sheet"] == "下月合同"
    imported = data(client.post(f"/api/v1/clean/jobs/{job_id}/import", headers=headers))
    assert imported["stats"]["imported"] == 1
    assert imported["rows"][0]["customer_name"].startswith("下月合同")


def test_zero_valid_rows_cannot_be_reported_as_success(client, login):
    headers = login("songjia")
    batch = data(_import(client, headers, _xlsx([["客户", "合同金额", "签约日期", "业务线"],
        ["无效数据" + uuid.uuid4().hex, "金额不详", "2026-10-20", "XY"]]), "合同台账.xlsx"))
    assert client.post(f"/api/v1/clean/jobs/{batch['clean_job']['id']}/import", headers=headers).status_code == 400
    assert not data(client.get(f"/api/v1/imports/{batch['id']}", headers=headers))["ready"]


def test_partial_import_has_partial_readiness(client, login):
    headers = login("songjia")
    batch = data(_import(client, headers, _xlsx([["客户", "合同金额", "签约日期", "业务线"],
        ["有效" + uuid.uuid4().hex, 500, "2026-10-20", "XY"],
        ["无效" + uuid.uuid4().hex, "未知", "2026-10-20", "XY"]]), "合同台账.xlsx"))
    data(client.post(f"/api/v1/clean/jobs/{batch['clean_job']['id']}/import", headers=headers))
    result = data(client.get(f"/api/v1/imports/{batch['id']}", headers=headers))
    assert result["stage"] == "partial" and not result["ready"]
    assert result["clean_job"]["stats"]["imported"] == 1


def test_two_managers_cannot_import_stale_duplicate_previews(client, account):
    _, first = account()
    _, second = account()
    content = _xlsx([["客户", "合同金额", "签约日期", "业务线"],
                     ["同一客户" + uuid.uuid4().hex, 900, "2026-10-20", "XY"]])
    one = data(_import(client, first, content, "合同台账.xlsx"))
    two = data(_import(client, second, content, "合同台账.xlsx"))
    data(client.post(f"/api/v1/clean/jobs/{one['clean_job']['id']}/import", headers=first))
    response = client.post(f"/api/v1/clean/jobs/{two['clean_job']['id']}/import", headers=second)
    assert response.status_code == 409 and "预览后" in response.json()["message"]


def test_private_knowledge_counts_do_not_leak_to_other_departments(client, login):
    outsider = login("wujun")
    before = data(client.get("/api/v1/kb/meta", headers=outsider))
    data(_upload(client, login("dengxin"), _docx("私有资料统计隔离" + uuid.uuid4().hex), "保密文件.docx",
                 category="制度", visibility="private"))
    after = data(client.get("/api/v1/kb/meta", headers=outsider))
    assert (after["total_docs"], after["total_chunks"]) == (before["total_docs"], before["total_chunks"])


def test_deleted_pending_document_is_not_reported_as_ready(client, login):
    owner = login("zengli")
    batch = data(_import(client, owner, _docx("待处理知识正文" + uuid.uuid4().hex), "运维规范.docx"))
    result = data(client.post(f"/api/v1/imports/{batch['id']}/confirm-kb", headers=owner, json={"category": "客服"}))
    data(client.delete(f"/api/v1/kb/docs/{result['kb_doc']['id']}", headers=owner))
    result = data(client.get(f"/api/v1/imports/{batch['id']}", headers=owner))
    assert not result["ready"] and result["stage"] == "removed"


def test_single_column_csv_and_oversize_extraction_are_explicit():
    from app.services.clean_service import read_table
    from app.services.kb_extract import extract_text
    from app.core.response import BizError
    assert len(read_table("姓名\n张三\n李四".encode(), "名单.csv")) == 3
    with pytest.raises(BizError, match="拆分"):
        extract_text(_xlsx([["字段"]] * 20001), "过长资料.xlsx")


def test_xiaoju_can_discover_import_progress_but_writes_require_confirmation():
    from app.services.xiaoju_business_catalog import operations
    ops = operations()
    assert ops["imports.batches"].effect == "query"
    assert ops["imports.confirm_kb"].effect == "confirm"
    assert ops["imports.withdraw"].effect == "confirm"


def test_keyword_fallback_prefers_specific_inspection_category():
    from app.services.kb_service import suggest_category
    suggestion = suggest_category("巡检规范A", "机房运维巡检规范，每周检查交换机和 UPS，记录排查结果。")
    assert suggestion["category"] == "客服" and suggestion["subcategory"] == "运维规范"


def test_agent_receipt_does_not_label_uploaded_materials_mock_or_hide_truncation():
    from app.services.xiaoju_business import operation_data_mode, result_view
    from app.services.xiaoju_business_catalog import operations
    assert operation_data_mode(operations()["imports.batches"]) == "local"
    record = {"ok": True, "status": "succeeded", "id": "test", "operation_id": "imports.batches",
              "title": "资料进度", "message": "已查询", "page": "/imports", "data_mode": "local",
              "data": {"total": 20, "items": list(range(20))}}
    result = result_view(record)
    assert len(result["data"]["items"]) == 12 and result["data_truncated"] and "分页" in result["data_notice"]


def test_long_document_pages_are_complete_and_follow_visibility(client, login):
    owner, other = login("dengxin"), login("wujun")
    marker = uuid.uuid4().hex
    body = "\n".join(f"## 第{i}节\n" + f"{marker}这是第{i}节的详细流程说明。" * 30 for i in range(8))
    doc = data(_upload(client, owner, body.encode(), "长制度.md", category="制度", visibility="private"))
    chunks, offset = [], 0
    while True:
        page = data(client.get(f"/api/v1/kb/docs/{doc['id']}/chunks", params={"offset": offset, "limit": 2}, headers=owner))
        chunks.extend(page["chunks"])
        if not page["has_more"]:
            break
        assert page["next_offset"] > offset
        offset = page["next_offset"]
    assert len(chunks) == len(doc["chunks"]) == page["total_chunks"]
    assert [c["text"] for c in chunks] == [c["text"] for c in doc["chunks"]]
    assert client.get(f"/api/v1/kb/docs/{doc['id']}/chunks", headers=other).status_code == 404

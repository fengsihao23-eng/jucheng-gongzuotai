"""仅限独立本机测试库的企微合成接口，供浏览器验收页面与业务链路。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from sqlalchemy import select
from app.core.config import settings
from app.core.db import SessionLocal
from app.core.response import BizError, forbidden
from app.models import SysUser
from app.services import wecom_cli_service as cli

assert settings.environment == "test" and settings.database_url.rsplit("/", 1)[-1].startswith("jucheng_test_wecom_ui_")
assert settings.source_mode("wecom") == "mock" and not settings.scheduler_enabled
with SessionLocal() as db:
    owner = db.scalar(select(SysUser.id).where(SysUser.wecom_userid == "songjia"))
manifest = {"binding_id": "a" * 32, "user_id": owner}
def owned(uid, binding_id=None):
    if uid != owner or binding_id not in (None, manifest["binding_id"]): raise forbidden("本合成测试账号没有企微连接")
    return manifest
cli._owned = owned
cli.status = lambda uid: {"available": uid == owner, "configured": uid == owner, "scope": "personal", "requires_password_change": False,
                          "message": "企微合成测试连接" if uid == owner else "尚未绑定本人企业微信"}


def read(uid, method, payload, binding_id, workspace_id):
    owned(uid, binding_id)
    if "denied" in str(payload):
        raise BizError(40301, "需要开通搜索与获取文档内容", {"authorization_url": "https://work.weixin.qq.com/test-authorization"})
    if method in {"doc.search", "disk.files.search"}:
        item = {"docid": "d3_search", "doc_name": "合成搜索巡检手册", "doc_type": "doc", "url": "https://doc.weixin.qq.com/doc/d3_search"}
        return {"docs": [item], "has_more": False} if method == "doc.search" else {"files": [
            {"id": "file-search", "type": "file", "file_name": "合成微盘巡检.txt"}], "has_more": False}
    if method == "doc.contents.get": return {"name": "合成巡检手册", "version": 1,
        "content": "设备运维巡检规范，检查交换机端口、温度及网络连通状态。来源编号：" + payload["docid"]}
    if method == "smartpage.pages.get":
        if not payload.get("page_id"): return {"doc_title": "合成智能文档", "rev": 1, "pages": [
            {"page_id": "root", "page_title": "总则"}, {"page_id": "child", "page_title": "排查步骤", "parent_id": "root"}]}
        return {"rev": 1, "pages": [{"page_id": payload["page_id"], "content_file_inner": payload["page_id"] + "：巡检时检查设备、记录异常并核对结果。"}]}
    if method == "disk.files.get": return {"file": {"id": "file-search", "type": "file", "file_name": "合成微盘巡检.txt", "update_time": "2026-10-30 12:00:00"}}
    if method == "disk.files.download":
        target = cli.home(uid) / "work/imports" / workspace_id / "download.txt"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("合成微盘运维资料，文件下载后按既有分类审核链路归档。", encoding="utf-8")
        return {"file_path": str(target)}
    if method == "smartsheet.sheets.list": return {"name": "合成合同台账", "sheets": [
        {"sheet_id": "business", "title": "客户合同", "type": "smartsheet", "record_count": 2},
        {"sheet_id": "dashboard", "title": "数据看板", "type": "dashboard"}]}
    if method == "smartsheet.fields.list": return {"has_more": False, "fields": [
        {"field_title": "客户", "field_type": "text"}, {"field_title": "合同金额", "field_type": "currency"},
        {"field_title": "签约日期", "field_type": "date_time"}, {"field_title": "业务线", "field_type": "single_select"}]}
    if method == "smartsheet.records.list":
        suffix = payload["docid"][-10:]
        records = [{"record_id": "r" + str(i), "values": {"客户": f"企微浏览器合成{i}-{suffix}", "合同金额": 1000 + i,
                    "签约日期": "2026-10-20 00:00:00", "业务线": [{"text": "XY"}]}} for i in (1, 2)]
        return {"records": records[:payload.get("limit", 100)], "total": 2, "has_more": False}
    raise RuntimeError("Unexpected synthetic method: " + method)
cli.import_read = read

from app.main import app
def fixture_identity(): return {"fixture": "wecom_import_synthetic"}
from fastapi.routing import APIRoute
app.router.routes.insert(0, APIRoute("/intake-test-fixture", fixture_identity, methods=["GET"]))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=18110, log_level="warning", access_log=False)

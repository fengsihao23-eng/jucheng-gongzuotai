"""本人企微资料 → 私有读取预览 → 明确确认 → 原有资料导入审核/清洗链路。"""
import base64
import fcntl
import hashlib
import json
import os
import re
import shutil
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from sqlalchemy import select, text

from app.core.config import settings
from app.core.deps import build_principal
from app.core.response import BizError, bad_request, conflict, forbidden, not_found
from app.core.security import password_version
from app.core.storage import save_bytes
from app.models import ImportBatch, SysUser
from app.services import import_service, wecom_cli_service as cli, wecom_import_formats as fmt
from app.services.xiaoju_tools import private_bytes, private_write

TTL = 3600
SUPPORTED = {"file", "doc", "word", "smartpage", "smartsheet", "sheet"}
KINDS = {"file": "微盘文件", "doc": "在线文档", "word": "在线文档", "smartpage": "智能文档", "smartsheet": "智能表格", "sheet": "在线表格"}


def _folder(user_id):
    return settings.storage_dir.parent / "wecom-import" / str(int(user_id))


def _id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{32}", value):
        raise not_found("企微读取记录")
    return value


def _path(user_id, source_id):
    return _folder(user_id) / (_id(source_id) + ".json")


def _load(user_id, source_id):
    try:
        record = json.loads(_path(user_id, source_id).read_text())
    except (OSError, ValueError):
        raise not_found("企微读取记录") from None
    if record.get("user_id") != user_id:
        raise not_found("企微读取记录")
    if time.time() > record["expires_at"] and record["state"] != "imported":
        raise conflict("预览已过期，请重新读取企微原件后确认")
    return record


@contextmanager
def _locked(user_id, source_id):
    target = _path(user_id, source_id).with_suffix(".lock")
    if not target.parent.is_dir():
        raise not_found("企微读取记录")
    with target.open("a") as stream:
        os.chmod(target, 0o600)
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


def _save(record):
    record["updated_at"] = time.time()
    private_write(_path(record["user_id"], record["id"]), record)


def _principal(user_id):
    from app.core.db import SessionLocal
    with SessionLocal() as db:
        user = db.get(SysUser, user_id)
        if not user or user.status != 1 or user.is_deleted:
            raise forbidden("账号已停用，不能读取企业微信资料")
        return build_principal(user)


def _scope(p):
    return hashlib.sha256(json.dumps({"password": password_version(p.user.password_hash),
        "menus": sorted(p.menus), "buttons": sorted(p.buttons), "dept": p.dept_code,
        "scope": p.data_scope}, sort_keys=True).encode()).hexdigest()


def _check(record):
    p = _principal(record["user_id"])
    cli._owned(p.id, record["binding_id"])
    if _scope(p) != record["scope"]:
        raise forbidden("账号权限或密码已变化，请重新读取资料")
    return p


def _new(p, **values):
    document = cli._owned(p.id)
    root = _folder(p.id)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    # 每人一小时最多保留 40 个读取预览。过期副本删除；已入库的原件另有持久化归档。
    active = 0
    for target in root.glob("*.json"):
        try:
            old = json.loads(target.read_text())
            if old.get("expires_at", 0) < time.time() and old.get("state") != "imported":
                if old.get("state") not in {"reading", "queued"}:
                    target.with_suffix(".payload").unlink(missing_ok=True)
                    target.with_suffix(".raw").unlink(missing_ok=True)
                    target.unlink()
            elif old.get("state") not in {"imported", "selection"}:
                active += 1
        except (OSError, ValueError):
            continue
    if active >= 40:
        raise conflict("本人的读取预览较多，请先确认已有资料或等待预览过期")
    stored_bytes = sum(f.stat().st_size for suffix in ("*.payload", "*.raw") for f in root.glob(suffix) if f.is_file())
    if stored_bytes > 200 * 1024 * 1024:
        raise conflict("本人的临时读取资料已超过 200MB，请先处理已有预览")
    record = {"id": uuid.uuid4().hex, "user_id": p.id, "binding_id": document["binding_id"], "scope": _scope(p),
              "state": "queued", "created_at": time.time(), "expires_at": time.time() + TTL,
              "title": "企业微信资料", "warnings": [], **values}
    _save(record)
    return record


def parse_link(raw):
    try:
        url = urlsplit(raw.strip())
        if url.scheme != "https" or url.username or url.password or url.port not in (None, 443):
            raise ValueError()
    except ValueError:
        raise bad_request("请粘贴企业微信官方 HTTPS 资料链接") from None
    if url.hostname == "drive.weixin.qq.com" and url.path.rstrip("/") == "/s":
        ticket = parse_qs(url.query).get("k", [])
        if len(ticket) != 1 or not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", ticket[0]):
            raise bad_request("微盘分享链接缺少有效的文件标识")
        return {"kind": "file", "url": f"https://drive.weixin.qq.com/s?k={ticket[0]}", "disk": True}
    if url.hostname in {"doc.weixin.qq.com", "page.weixin.qq.com"}:
        match = re.fullmatch(r"/(doc|smartpage|smartsheet|sheet)/([A-Za-z0-9_-]{1,200})/?", url.path)
        if match and (url.hostname != "page.weixin.qq.com" or match[1] == "smartpage"):
            return {"kind": match[1], "docid": match[2], "url": f"https://{url.hostname}/{match[1]}/{match[2]}"}
    raise bad_request("暂支持企微微盘文件、在线文档、智能文档、在线表格及智能表格链接；请提供具体资料链接")


def status(p):
    info = cli.status(p.id)
    return {**info, "snapshot_notice": "确认导入的是本次读取快照，源文档后续修改不会自动覆盖台账。默认仅本人可见，发布前可再次核对范围。"}


def search(p, kind, keywords, cursor="", space=""):
    if kind not in {"disk", "doc"}:
        raise bad_request("请选择微盘或在线文档")
    if not keywords.strip():
        raise bad_request("请输入要查找的资料名称或关键词")
    record = _new(p, state="selection", kind="selection")
    method = "disk.files.search" if kind == "disk" else "doc.search"
    payload = {"keywords": [keywords.strip()[:120]], "limit": 20}
    if cursor:
        payload["cursor"] = cursor
    if kind == "disk":
        payload["search_type"] = "file"
        if space.strip(): payload["space_keywords"] = [space.strip()[:100]]
    result = cli.import_read(p.id, method, payload, record["binding_id"], record["id"])
    _check(record)
    items = result.get("files" if kind == "disk" else "docs")
    if not isinstance(items, list):
        raise bad_request("企微未返回资料列表，未将异常响应当作空列表")
    output = []
    for item in items[:20]:
        resource_kind = item.get("type") or item.get("doc_type")
        if resource_kind not in SUPPORTED:
            continue
        source = _new(p, state="selection", kind=resource_kind, title=str(item.get("file_name") or item.get("doc_name") or "企业微信资料")[:200],
                      file_id=item.get("id") if kind == "disk" else None, docid=item.get("docid"), disk=kind == "disk",
                      url=item.get("doc_url") or item.get("url") or "", source_updated_at=item.get("update_time"))
        output.append(view(source))
    return {"items": output, "has_more": bool(result.get("has_more")), "next_cursor": result.get("next_cursor") or ""}


def start(p, *, url="", source_id="", table_id=""):
    if bool(url.strip()) == bool(source_id):
        raise bad_request("请选择一个搜索结果或粘贴一个资料链接")
    if source_id:
        old = _load(p.id, source_id)
        _check(old)
        source = {k: old[k] for k in ("kind", "title", "file_id", "docid", "disk", "url", "source_updated_at") if k in old}
    else:
        source = parse_link(url)
    return _new(p, **source, requested_table=table_id)


def _call(record, method, payload):
    if time.time() - record["created_at"] > 900:
        raise bad_request("本次读取已超过 15 分钟，请缩小源资料后重新预览")
    _check(record)
    result = cli.import_read(record["user_id"], method, payload, record["binding_id"], record["id"])
    if method in {"smartsheet.sheets.list", "smartsheet.fields.list", "smartsheet.records.list", "sheet.workbook.get", "sheet.cells.get"} and isinstance(result, dict) and result.get("file_path"):
        try:
            result = json.loads(fmt.read_output(record["user_id"], record["id"], result["file_path"]))
        except (ValueError, UnicodeDecodeError):
            raise bad_request("企微大表临时回包格式异常，尚未完整读取") from None
    if not isinstance(result, dict):
        raise bad_request("企微资料接口返回结构异常")
    if result.get("error") or result.get("errcode"):
        raise bad_request("企微资料读取返回业务错误，请检查该资料的授权")
    return result


def _document(record):
    uid, sid = record["user_id"], record["id"]
    if record["kind"] in {"doc", "word"}:
        result = _call(record, "doc.contents.get", {"docid": record["docid"], "content_type": "markdown"})
        record.update(title=result.get("name") or record["title"], version=result.get("version"))
        record["warnings"].append("本次读取在线正文；文内图片、嵌入附件仍保留原链接，未作为独立原件下载。")
        return fmt.text_content(result, uid, sid), result
    tree = _call(record, "smartpage.pages.get", {"docid": record["docid"], "content_type": "markdown"})
    pages = tree.get("pages")
    if not isinstance(pages, list) or not pages or len(pages) > fmt.MAX_PAGES:
        raise bad_request("智能文档页面清单为空或超过 100 页，请按文档拆分接入")
    record.update(title=tree.get("doc_title") or record["title"], version=tree.get("rev"))
    content, raw_pages, seen = [], [], set()
    for page in pages:
        page_id = page.get("page_id")
        if not page_id or page_id in seen:
            raise bad_request("智能文档页面标识不完整或重复，请重新读取")
        seen.add(page_id)
        result = _call(record, "smartpage.pages.get", {"docid": record["docid"], "page_id": page_id, "content_type": "markdown"})
        if tree.get("rev") is not None and result.get("rev") not in (None, tree["rev"]):
            raise conflict("智能文档在读取过程中发生更新，请重新预览")
        details = result.get("pages")
        detail = next((x for x in details or [] if x.get("page_id") == page_id), None)
        if detail is None:
            raise bad_request("智能文档有页面未能读取，尚未形成完整正文快照")
        value = fmt.text_content(detail, uid, sid, "content_file_inner")
        content.append(f"## {page.get('page_title') or detail.get('page_title') or '页面'}\n{value}")
        raw_pages.append(result)
    record["page_count"] = len(pages)
    record["warnings"].append("本次读取全部页面正文；内嵌数据表、图片和附件不作为正文导入。数据表请用其智能表格链接单独接入。")
    return "\n\n".join(content), {"tree": tree, "pages": raw_pages}


def _cleanup_cli(record):
    # 只删除本次随机请求的临时输出目录；原件副本已存入本人预览或归档。
    target = cli.home(record["user_id"]) / "work" / "imports" / _id(record["id"])
    if not target.is_symlink():
        shutil.rmtree(target, ignore_errors=True)


def _raw_value(value):
    if isinstance(value, list): return [_raw_value(x) for x in value]
    if isinstance(value, dict): return {k: _raw_value(v) for k, v in value.items() if k != "file_path"}
    return value


def prepare(user_id, source_id):
    with _locked(user_id, source_id):
        record = _load(user_id, source_id)
        if record["state"] != "queued": return
        record["state"] = "reading"
        _save(record)
        try:
            _check(record)
            if record.get("disk"):
                params = {"file_id": record["file_id"]} if record.get("file_id") else {"url": record["url"]}
                result = _call(record, "disk.files.get", params)
                info = result.get("file")
                if not isinstance(info, dict) or not info.get("id") or info.get("type") not in SUPPORTED:
                    raise bad_request("该链接不是可导入的具体文件或文档")
                record.update(kind=info["type"], file_id=info["id"], title=info.get("file_name") or record["title"],
                              docid=info.get("docid"), source_updated_at=info.get("update_time"))
                if info.get("doc_url"):
                    record["url"] = info["doc_url"]
                if int(info.get("file_size") or 0) > fmt.MAX_BYTES:
                    raise bad_request("微盘文件超过 50MB，请拆分后接入")
            if record["kind"] == "file":
                result = _call(record, "disk.files.download", {"file_id": record["file_id"]})
                if result.get("file_path"):
                    payload = fmt.read_output(user_id, source_id, result["file_path"])
                else:
                    try: payload = base64.b64decode(result["file_content"], validate=True)
                    except (KeyError, ValueError, TypeError):
                        raise bad_request("微盘下载没有返回有效原件，尚未完成下载") from None
                if not payload or len(payload) > fmt.MAX_BYTES:
                    raise bad_request("微盘原件为空或超过 50MB，未保存不完整原件")
                record["filename"] = Path(str(record["title"]).replace("\\", "/")).name[:180]
                raw = {"file_id": record["file_id"], "source_updated_at": record.get("source_updated_at")}
            elif record["kind"] in {"doc", "word", "smartpage"}:
                content, raw = _document(record)
                if not content.strip(): raise bad_request("在线文档没有可导入的正文")
                payload = (f"# {record['title']}\n\n来源：{record.get('url', '')}\n\n" + content).encode()
                record["filename"] = _filename(record["title"], ".md")
                record["preview_text"] = content[:800]
            else:
                fetch = lambda method, params: _call(record, method, params)
                read_table = fmt.smart_table if record["kind"] == "smartsheet" else fmt.online_table
                result = read_table(fetch, record, record.get("requested_table"))
                if result is None:
                    record["state"] = "choose_table"
                    _save(record)
                    return
                payload, raw = result
                record["filename"] = _filename(record["title"] + "-" + record["table_name"], ".xlsx")
                if record.get("complex_columns"):
                    record["warnings"].append("附件、关联、公式等复杂列保留原始类型快照，未下载附件或展开关联；这些列必须在台账映射中忽略。")
            if len(payload) > fmt.MAX_BYTES:
                raise bad_request("读取结果超过 50MB，请拆分资料")
            _check(record)
            private_bytes(_path(user_id, source_id).with_suffix(".payload"), payload)
            raw_bytes = json.dumps(_raw_value(raw), ensure_ascii=False, sort_keys=True).encode()
            if len(raw_bytes) > fmt.MAX_BYTES:
                raise bad_request("原始类型快照过大，请拆分源表")
            private_bytes(_path(user_id, source_id).with_suffix(".raw"), raw_bytes)
            fingerprint = raw_bytes if record["kind"] in {"sheet", "smartsheet"} else payload
            record.update(state="ready", byte_size=len(payload), payload_hash=hashlib.sha256(payload).hexdigest(),
                          snapshot_hash=hashlib.sha256(fingerprint).hexdigest(), captured_at=time.time())
            _save(record)
        except Exception as error:
            record.update(state="failed", message=error.message if isinstance(error, BizError) else "企微资料读取未完成，请重新预览或核对授权。尚未写入业务台账或知识库。")
            if isinstance(error, BizError) and isinstance(error.data, dict) and error.data.get("authorization_url"):
                record["authorization_url"] = error.data["authorization_url"]
            _save(record)
        finally:
            _cleanup_cli(record)


def _filename(title, extension):
    return (re.sub(r'[\\/:*?"<>|\r\n]', "_", str(title)).strip(". ")[:150] or "企业微信资料") + extension


def view(record):
    result = {key: record.get(key) for key in ("id", "state", "kind", "title", "tables", "columns", "sample_rows", "row_count",
        "table_name", "page_count", "warnings", "preview_text", "message", "expires_at", "created_at", "captured_at", "byte_size", "batch_id", "authorization_url")}
    result["kind_name"] = KINDS.get(record.get("kind"), "企业微信资料")
    try: result["url"] = parse_link(record.get("url", ""))["url"]
    except BizError: result["url"] = ""
    result["default_visibility"] = "private"
    return result


def get(p, source_id):
    record = _load(p.id, source_id)
    _check(record)
    return view(record)


def list_sources(p):
    cli._owned(p.id)
    result = []
    for target in sorted(_folder(p.id).glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True):
        try:
            record = _load(p.id, target.stem)
            if record["state"] == "selection": continue
            _check(record)
            result.append(view(record))
        except (BizError, OSError, ValueError):
            continue
        if len(result) >= 20: break
    return result


def confirm(db, p, source_id, purpose=""):
    with _locked(p.id, source_id):
        record = _load(p.id, source_id)
        _check(record)
        if record["state"] == "imported":
            batch = import_service._get(db, p, record["batch_id"])
            return batch, True
        if record["state"] != "ready":
            raise conflict("资料尚未完整读取，请核对预览后再导入")
        # 保存前再次通过原接口核验读取权限，不能只相信一小时前的已授权预览。
        try:
            if record.get("disk"):
                latest = _call(record, "disk.files.get", {"file_id": record["file_id"]}).get("file", {})
                if record.get("source_updated_at") and latest.get("update_time") != record["source_updated_at"]:
                    raise conflict("源文件在预览后更新，请重新读取后确认")
            elif record["kind"] in {"doc", "word"}:
                latest = _call(record, "doc.contents.get", {"docid": record["docid"], "content_type": "markdown"})
                if record.get("version") is not None and latest.get("version") != record["version"]:
                    raise conflict("源文档在预览后更新，请重新读取后确认")
            elif record["kind"] == "smartpage":
                latest = _call(record, "smartpage.pages.get", {"docid": record["docid"], "content_type": "markdown"})
                if record.get("version") is not None and latest.get("rev") != record["version"]:
                    raise conflict("源智能文档在预览后更新，请重新读取后确认")
            elif record["kind"] == "smartsheet":
                _call(record, "smartsheet.records.list", {"docid": record["docid"], "sheet_id": record["table_id"], "limit": 1, "key_type": "field_title"})
            else:
                _call(record, "sheet.workbook.get", {"docid": record["docid"]})
        finally:
            _cleanup_cli(record)
        payload = _path(p.id, source_id).with_suffix(".payload").read_bytes()
        if hashlib.sha256(payload).hexdigest() != record["payload_hash"]:
            raise conflict("读取快照已变化，请重新预览")
        resource_key = f"{record['kind']}:{record.get('docid') or record.get('file_id')}:{record.get('table_name', '')}"
        if db.bind.dialect.name == "postgresql":
            key = int(hashlib.sha256(f"wecom-import:{p.id}:{resource_key}".encode()).hexdigest()[:15], 16)
            db.execute(text("select pg_advisory_xact_lock(:key)"), {"key": key})
        duplicate = db.scalar(select(ImportBatch).where(ImportBatch.uploader_id == p.id,
            ImportBatch.status.notin_(["withdrawn", "dismissed"]),
            ImportBatch.suggestion["external_source"]["key"].as_string() == resource_key,
            ImportBatch.suggestion["external_source"]["snapshot_hash"].as_string() == record["snapshot_hash"]))
        if duplicate:
            record.update(state="imported", batch_id=duplicate.id)
            _save(record)
            return duplicate, True
        try:
            batch = import_service.create_many(db, p, payload, record["filename"], purpose or None, channel="wecom", deferred=True)["batches"][0]
        except BizError as error:
            if error.code != 40901 or not isinstance(error.data, dict) or not error.data.get("batch_id"): raise
            batch = import_service._get(db, p, error.data["batch_id"])
            return batch, True
        raw = _path(p.id, source_id).with_suffix(".raw").read_bytes()
        original = save_bytes(db, data=raw, filename=_filename(record["title"], ".json"), category="import",
            key=f"import/wecom/{p.id}/{source_id}/typed-source", uploader_id=p.id)
        batch.suggestion = {"external_source": {"key": resource_key, "title": record["title"], "kind": record["kind"],
            "url": view(record)["url"], "captured_at": record["captured_at"], "source_updated_at": record.get("source_updated_at"),
            "version": record.get("version"), "snapshot_hash": record["snapshot_hash"], "raw_file_id": original.id,
            "complex_columns": record.get("complex_columns", []), "warnings": record["warnings"],
            "table_name": record.get("table_name"), "row_count": record.get("row_count")}}
        db.flush()
        return batch, False


def completed(p, source_id, batch_id):
    with _locked(p.id, source_id):
        record = _load(p.id, source_id)
        record.update(state="imported", batch_id=batch_id)
        _save(record)
        _path(p.id, source_id).with_suffix(".payload").unlink(missing_ok=True)
        _path(p.id, source_id).with_suffix(".raw").unlink(missing_ok=True)

"""小聚业务执行器：走原业务接口，读写均使用当前账号，重要变更生成确认单。"""
import asyncio
import hashlib
import json
import re
import time
import uuid
import copy
from collections import Counter
from types import SimpleNamespace
from urllib.parse import quote, unquote, urlsplit

import httpx
from jsonschema import Draft202012Validator, FormatChecker

from app.core.audit import log_op
from app.core.config import settings
from app.core.db import SessionLocal
from app.core.deps import build_principal
from app.core.response import BizError, bad_request, conflict, encode, forbidden
from app.core.security import create_jwt
from app.core.storage import file_brief, save_bytes
from app.models import SysFile, SysUser
from app.services import xiaoju_business_catalog as catalog
from app.services import xiaoju_business_state as state
from app.services.xiaoju_tools import redact, safe_value

WRITE_TERMS = ("登记", "新增", "创建", "修改", "更新", "提交", "归档", "录入", "导入", "处理", "推进", "标记", "维护", "分配", "归集",
               "重算", "发送", "通知", "删除", "撤销", "取消", "运行", "执行", "改成", "调整", "安排", "派单", "布置", "发布", "反馈进展",
               "记一下", "开一张", "确认开票", "开具发票", "核销", "红冲", "更正", "入账", "同步", "拉取", "解除", "通过审核", "退回", "催交",
               "设为", "改为", "标为", "完成任务", "完成节点", "保存", "回复")


def write_requested(text):
    return any(word in text for word in WRITE_TERMS)


def principal(user_id):
    with SessionLocal() as db:
        user = db.get(SysUser, user_id)
        if not user or user.is_deleted or user.status != 1:
            raise forbidden("账号已停用，不能执行业务操作")
        return build_principal(user)


def current_operation(operation_id, user_id):
    p = principal(user_id)
    with SessionLocal() as db:
        return catalog.get(operation_id, p, db)


def digest(value):
    return hashlib.sha256(json.dumps(encode(value), sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def actor_signature(user_id):
    p = principal(user_id)
    return digest({"menus": sorted(p.menus), "buttons": sorted(p.buttons), "scope": p.data_scope, "department": p.user.department_id})


def source_signature():
    return digest({"database": settings.database_url, "mode": settings.data_source_mode,
                   "sources": {key: settings.source_mode(key) for key in ("wecom", "lemoncloud")}})


def _wecom_identity():
    return digest((settings.wecom_corp_id, settings.wecom_agent_id, settings.wecom_api_base))


def compact(value, depth=0):
    if depth > 5: return "明细请在业务结果中查看"
    if isinstance(value, dict):
        return {key: compact(item, depth + 1) for key, item in list(value.items())[:45]}
    if isinstance(value, list): return [compact(item, depth + 1) for item in value[:12]]
    if isinstance(value, str): return value[:1600]
    return value


def files_in(value):
    found = {}
    def walk(item):
        if isinstance(item, dict):
            url = item.get("url")
            if item.get("filename") and isinstance(url, str) and (
                url.startswith("/api/v1/files/") or url.startswith(settings.public_base_url.rstrip("/") + "/api/v1/files/")):
                found[url] = item
            for child in item.values(): walk(child)
        elif isinstance(item, list):
            for child in item: walk(child)
    walk(value)
    return list(found.values())[:30]


def _validate_schema(value, schema, field):
    errors = sorted(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(value), key=lambda e: str(e.path))
    if errors:
        error = errors[0]
        name = ".".join([field, *(str(part) for part in error.path)])
        if error.validator == "required":
            raise bad_request(f"{name} 缺少必填字段，请按业务参数说明补充")
        raise bad_request(f"{name} 参数格式不正确，请按业务参数说明填写")


def defaults(value, schema):
    if not isinstance(value, dict): return value
    value = copy.deepcopy(value)
    for key, definition in schema.get("properties", {}).items():
        if key not in value and "default" in definition: value[key] = copy.deepcopy(definition["default"])
        if key in value and isinstance(value[key], dict): value[key] = defaults(value[key], definition)
    return value


_PRIVATE_FIELDS = {"secret", "corpsecret", "appsecret", "accesssecret", "accesstoken", "refreshtoken", "token", "authorization",
                   "encodingaeskey", "aeskey", "webhookkey", "password", "apikey", "jsapiticket", "permanentcode", "sessionkey"}
_PRIVATE_WECOM_METHODS = {"gettoken", "get_access_token", "get_provider_token", "get_suite_token", "get_corp_token", "get_permanent_code",
                         "get_jsapi_ticket", "get_agent_ticket", "ticket/get", "getuserinfo", "getuserdetail", "getuseridentity", "authsucc"}


def _private_field(key):
    return re.sub(r"[^a-z]", "", str(key).lower()) in _PRIVATE_FIELDS


def _scrub_private_fields(value):
    if isinstance(value, dict):
        return {key: _scrub_private_fields(item) for key, item in value.items() if not _private_field(key)}
    if isinstance(value, list):
        return [_scrub_private_fields(item) for item in value]
    return value


def _wecom_console_item(body):
    from app.integration.wecom import catalog as official
    item = official.get(body["id"])
    if item is None: raise bad_request("请先在企业微信官方接口目录中选定具体接口")
    path = item["path"].lower().rstrip("/")
    if any(path.endswith("/" + name) for name in _PRIVATE_WECOM_METHODS):
        raise forbidden("令牌或个人授权接口不能通过对话调用")
    params, payload = body.get("params", {}), body.get("payload")
    if "key" in params or _scrub_private_fields(params) != params or _scrub_private_fields(payload) != payload:
        raise bad_request("使用接口中心已配置的授权，不要在对话中传递密钥或令牌")
    return item


def validate(op, request, user_id):
    if not isinstance(request, dict) or not request.keys() <= {"path", "query", "body", "files"}:
        raise bad_request("业务参数只能包含 path、query、body 和 files")
    request = {"path": request.get("path") or {}, "query": request.get("query") or {},
               "body": request.get("body"), "files": request.get("files") or {}}
    request["query"] = defaults(request["query"], op.schema["query"])
    if request["body"] is None and op.schema["body"].get("type") == "object": request["body"] = {}
    # Omitted PATCH/PUT fields must stay omitted: many original routes use
    # exclude_unset=True. Adding schema defaults here would clear existing data.
    _validate_schema(request["path"], op.schema["path"], "path")
    _validate_schema(request["query"], op.schema["query"], "query")
    if not isinstance(request["files"], dict): raise bad_request("files 必须按文件字段名指定附件")
    if op.schema["content_type"] == "multipart/form-data":
        body = request["body"] or {}
        if not isinstance(body, dict): raise bad_request("上传业务的 body 必须为对象")
        merged = dict(body)
        properties = op.schema["body"].get("properties", {})
        for field, values in request["files"].items():
            if field not in properties: raise bad_request("该业务不支持指定的文件字段")
            ids = values if isinstance(values, list) else [values]
            if not ids or len(ids) > 20: raise bad_request("一次最多处理 20 个附件")
            names = [state.attachment(record_id, user_id)[0]["filename"] for record_id in ids]
            schema = properties[field]
            merged[field] = names if schema.get("type") == "array" else names[0] if len(names) == 1 else None
        _validate_schema(merged, op.schema["body"], "body")
    else:
        if request["files"]: raise bad_request("该业务功能不接受文件上传")
        if request["body"] is not None or op.schema.get("body_required"):
            _validate_schema(request["body"], op.schema["body"], "body")
    if op.source == "wecom_cli":
        from app.services.wecom_cli_service import method_schema
        _validate_schema(request["body"], method_schema(user_id, op.cli_method, op.connection_id), "body")
    if op.id == "wecom_console.call":
        _wecom_console_item(request["body"])
    return request


async def _http(op, request, user_id, *, confirmed=False):
    from app.main import app
    url = op.path
    for key, value in request["path"].items():
        url = url.replace("{" + key + "}", quote(str(value), safe=""))
    if "{" in url: raise bad_request("缺少业务记录编号")
    token, _ = create_jwt(user_id, minutes=2)
    kwargs = {"headers": {"Authorization": f"Bearer {token}", "X-Xiaoju-Business": op.id},
              "params": {key: value for key, value in request["query"].items() if value is not None}}
    if op.schema["content_type"] == "multipart/form-data":
        form = {key: json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else
                str(value).lower() if isinstance(value, bool) else str(value)
                for key, value in (request["body"] or {}).items() if value is not None}
        files = []
        for field, values in request["files"].items():
            for record_id in values if isinstance(values, list) else [values]:
                meta, file_path = state.attachment(record_id, user_id)
                files.append((field, (meta["filename"], file_path.read_bytes(), meta["content_type"])))
        kwargs.update(data=form, files=files)
    elif request["body"] is not None:
        kwargs["json"] = request["body"]
    if op.id == "wecom_console.call":
        _wecom_console_item(request["body"])
        if not confirmed: raise forbidden("请在执行单上确认这次企微接口调用")
        kwargs["json"] = {**request["body"], "confirm": confirmed}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://xiaoju-internal", timeout=90) as client:
        response = await client.request(op.method, url, **kwargs)
    if response.status_code >= 400:
        try:
            error = response.json()
            message, code = redact(str(error.get("message", "业务执行失败"))), error.get("code", 40001)
        except (ValueError, TypeError):
            message, code = "业务接口未返回有效结果，请检查参数后重试", 50201
        raise BizError(code if isinstance(code, int) else 40001, message)
    if "application/json" in response.headers.get("content-type", "") and "content-disposition" not in response.headers:
        body = response.json()
        if isinstance(body, dict) and "code" in body:
            if body["code"] != 0: raise BizError(body["code"], redact(body.get("message", "执行失败")))
            data, message = body.get("data"), body.get("message")
            if op.module == "wecom_console": data = _scrub_private_fields(data)
            if op.id == "imports.decision" and isinstance(data, dict):
                # ASGI waits for the background job; read its actual final state rather than the initial submission response.
                target = current_operation("imports.batch", user_id)
                data, _ = await _http(target, {"path": request["path"], "query": {}, "files": {}, "body": None}, user_id)
                message = data.get("note") or data.get("readiness_label") or "已处理导入决定，请核对实际入库状态"
            if not message or message == "ok":
                message = (data.get("summary") if isinstance(data, dict) and isinstance(data.get("summary"), str) else None) or (
                    f"查询完成，共 {data['total']} 条记录" if isinstance(data, dict) and isinstance(data.get("total"), int) else
                    "查询已完成，详情见业务回执" if op.effect == "query" else "业务操作已执行，详情见业务回执")
            return data, message
        return body, "操作完成"
    content_disposition = response.headers.get("content-disposition", "")
    match = re.search(r"filename\*=UTF-8''([^;]+)|filename=\"?([^\";]+)", content_disposition, re.I)
    filename = unquote((match.group(1) or match.group(2)) if match else "业务导出文件.bin").replace("\\", "/").split("/")[-1]
    with SessionLocal() as db:
        file = save_bytes(db, data=response.content, filename=filename, category="xiaoju", uploader_id=user_id,
                          key=f"xiaoju/{user_id}/{uuid.uuid4().hex}/{filename}", content_type=response.headers.get("content-type"))
        result = {"files": [file_brief(file)], "filename": filename, "size": len(response.content)}
        db.commit()
        return encode(result), "导出文件已生成"


def invoke(op, request, user_id, *, confirmed=False):
    current_operation(op.id, user_id)
    if op.source == "wecom_cli":
        from app.services.wecom_cli_service import execute
        return execute(op, request, user_id)
    if op.source == "attachment": return preview_file(request["body"]["file_id"], user_id), "已读取本人上传文件的预览"
    if op.source == "mcp":
        from app.mcp_server import server
        args = op.params_model.model_validate(request["body"] or {}).model_dump()
        context = SimpleNamespace(request_context=SimpleNamespace(request=SimpleNamespace(
            state=SimpleNamespace(mcp_user_id=user_id), client=SimpleNamespace(host="xiaoju-internal"))))
        body = json.loads(server._run(op.mcp_code, context, args, op.impl))
        if not body.get("ok"): raise bad_request(redact(body.get("message", "业务工具未完成")))
        return body, body.get("summary") or "业务工具执行完成"
    return asyncio.run(_http(op, request, user_id, confirmed=confirmed))


def preview_file(file_id, user_id):
    import csv
    from io import BytesIO, StringIO
    from pathlib import Path
    meta, target = state.attachment(file_id, user_id)
    extension = Path(meta["filename"]).suffix.lower()
    data = target.read_bytes()
    if extension == ".xlsx":
        from openpyxl import load_workbook
        book = load_workbook(BytesIO(data), read_only=True, data_only=True)
        try:
            sheets = []
            for sheet in book.worksheets[:6]:
                rows = []
                for index, row in enumerate(sheet.iter_rows(values_only=True)):
                    if index >= 20: break
                    rows.append({f"列{i+1}": encode(value) for i, value in enumerate(row[:20])})
                sheets.append({"name": sheet.title, "total_rows": sheet.max_row, "rows": rows})
            return {"filename": meta["filename"], "sheets": sheets, "note": "这里只预览前 20 行；导入与业务计算请调用对应业务功能处理完整文件"}
        finally: book.close()
    if extension == ".docx":
        from docx import Document
        document = Document(BytesIO(data))
        text = "\n".join(paragraph.text for paragraph in document.paragraphs if paragraph.text.strip())
        tables = [[{f"列{i+1}": cell.text for i, cell in enumerate(row.cells[:12])} for row in table.rows[:15]] for table in document.tables[:4]]
        return {"filename": meta["filename"], "text": text[:18000], "tables": tables, "truncated": len(text) > 18000}
    if extension in {".txt", ".md", ".csv", ".tsv"}:
        try: text = data.decode("utf-8-sig")
        except UnicodeError: text = data.decode("gb18030", errors="replace")
        if extension in {".csv", ".tsv"}:
            reader = csv.DictReader(StringIO(text), delimiter="\t" if extension == ".tsv" else ",")
            rows = []
            for index, row in enumerate(reader):
                if index >= 20: break
                rows.append(row)
            return {"filename": meta["filename"], "rows": rows, "note": "前 20 行预览，完整导入走业务功能"}
        return {"filename": meta["filename"], "text": text[:18000], "truncated": len(text) > 18000}
    raise bad_request("当前格式不支持直接预览，请提供 docx、xlsx、CSV 或文本；不能假称已解析 PDF、图片或旧版 xls")


def _snapshot(op, request, user_id):
    if not request["path"] or (op.method not in {"PUT", "PATCH", "DELETE"} and not (
        op.method == "POST" and (op.module in {"receivables", "kb"} or op.id.startswith("operations.") and "task_id" in request["path"])
    )):
        return None
    candidates = [item for item in catalog.operations().values() if item.source == "api" and item.method == "GET"
                  and (item.path == op.path or op.path.startswith(item.path + "/"))
                  and set(request["path"]) >= set(item.schema["path"]["properties"])
                  and "{" in item.path]
    if not candidates: return None
    target = max(candidates, key=lambda item: len(item.path))
    values = {key: request["path"][key] for key in target.schema["path"]["properties"]}
    try:
        data, _ = invoke(target, {"path": values, "query": {}, "body": None, "files": {}}, user_id)
    except BizError as exc:
        if exc.http_status in {403, 404}: raise
        return None
    return {"operation_id": target.id, "path": values, "data": compact(data)}


def _concrete_targets(op, request, user_id):
    """Freeze outbound recipients so later joiners are never added after approval."""
    if op.id in {"operations.record_project_progress", "operations.record_project_progress_entries"}:
        from app.services import operations_service as ops
        p = principal(user_id)
        with SessionLocal() as db:
            project = ops.get_project(db, p, request["path"]["project_id"])
            ops.manage(db, p, project)
            if op.id == "operations.record_project_progress_entries":
                entries = request["body"]["entries"]
                count = sum(bool(entry.get("next_due_date")) for entry in entries)
                target = {"运营项目": project.name, "新增进度记录": len(entries), "新增负责人计划": count}
                if count:
                    ops.active(project)
                    if request["body"].get("expected_owner_id") not in {None, project.owner_id}:
                        raise conflict("项目负责人已变更，请核对后重新填写")
                    request["body"]["expected_owner_id"] = project.owner_id
                    target["项目负责人"] = ops.user(db, project.owner_id).name
                return request, target
            return request, {"运营项目": project.name, "记录类型": "新增一条项目进度"}
    task_actions = {"operations.operation_feedback": "can_feedback", "operations.review_operation_task": "can_review",
                    "operations.submit_operation_delay_reason": "can_explain_delay", "operations.review_operation_delay": "can_review_delay"}
    if op.id in task_actions:
        data, _ = invoke(current_operation("operations.operation_task", user_id),
                         {"path": request["path"], "query": {}, "body": None, "files": {}}, user_id)
        flag = task_actions[op.id]
        if not data.get(flag): raise forbidden("当前账号不能执行这项任务的该操作")
        body = request["body"]
        if body.get("expected_revision") is None: body["expected_revision"] = data["revision"]
        if body["expected_revision"] != data["revision"]: raise conflict("任务已变化，请读取最新记录再办理")
        target = {"项目": data["project_name"], "任务": data["title"], "负责人": data["owner_name"], "当前进度": data["progress"]}
        if flag == "can_review_delay":
            reason = (data.get("delay_history") or [{}])[0]
            if body["reason_id"] != reason.get("id"): raise conflict("延期原因已变化，请读取最新记录再办理")
            target.update({"延期原因": reason["reason"], "复核结果": "认可" if body["decision"] == "accepted" else "不认可"})
        return request, target
    if op.id == "wecom_console.call":
        item = _wecom_console_item(request["body"])
        return request, {"接口": item["name"], "方法": item["method"], "接口地址": item["path"],
                         "凭证类别": request["body"].get("credential") or item["credential"]}
    if op.id == "imports.decision":
        data, _ = invoke(current_operation("imports.batch", user_id),
                         {"path": request["path"], "query": {}, "body": None, "files": {}}, user_id)
        plan = data.get("agent_plan") or {}
        if plan.get("version") != request["body"]["version"] or plan.get("state") not in {"draft", "attention"}:
            raise conflict("导入清单已变化，请读取最新清单后再办理")
        return request, {"文件": data["filename"], "决定": "按清单导入" if request["body"]["approve"] else "拒绝剩余导入",
                         "摘要": plan.get("summary"), "统计": plan.get("totals"), "去向": [step["title"] for step in plan.get("steps", [])]}
    if op.id == "admin.addressbook_sync" and (request.get("body") or {}).get("preview_id"):
        from app.services.company_directory_service import inspect_preview
        preview = inspect_preview(request["body"]["preview_id"], user_id)
        return request, {"人员变更统计": preview["counts"], "归档演示账号": preview.get("retire_demo_users", []),
                         "员工预览": preview["members"], "说明": preview["warnings"]}
    if op.id != "submissions.remind": return request, None
    body = request["body"] or {}
    summary_op = current_operation("submissions.summary", user_id)
    report_type = body.get("type", "weekly")
    data, _ = invoke(summary_op, {"path": {}, "query": {"type": report_type, "period": body.get("period")}, "body": None, "files": {}}, user_id)
    selected = body.get("user_ids")
    targets = [item for item in data.get("missing", []) if selected is None or item["user_id"] in selected]
    request["body"] = {"type": report_type, "period": data["period"], "user_ids": [item["user_id"] for item in targets]}
    return request, {"period": data["period"], "count": len(targets), "recipients": [{"user_id": item["user_id"], "name": item["name"]} for item in targets]}


def _stable(value):
    if isinstance(value, list): return [_stable(item) for item in value]
    if not isinstance(value, dict): return value
    volatile = {"url", "expires_in", "trace_id", "now", "today", "next_run", "remaining_days", "overdue_days", "overdue_hours"}
    return {key: _stable(item) for key, item in value.items() if key not in volatile}


def result_view(record):
    data = record["data"]
    preview = compact(data)
    return {"ok": record["ok"], "status": record["status"], "result_id": record["id"], "operation_id": record["operation_id"],
            "title": record["title"], "message": record["message"], "page": record["page"], "data": preview,
            "data_truncated": preview != data,
            "data_notice": "当前为摘要，不能据此当作全部记录。请分页查询原接口或使用完整导出/汇总接口核验。" if preview != data else "",
            "files": files_in(data), "links": _wecom_links(data) if record.get("data_source") == "wecom_cli" else [],
            "data_mode": record["data_mode"], "data_source": record.get("data_source"), "reused": record.get("reused", False),
            "changed": record.get("changed", False)}


def _wecom_links(data):
    links = {}
    def walk(item):
        if isinstance(item, list):
            for child in item: walk(child)
        if not isinstance(item, dict): return
        url = item.get("url") or item.get("doc_url")
        label = item.get("doc_name") or item.get("title") or item.get("filename")
        if isinstance(url, str) and isinstance(label, str):
            try: target = urlsplit(url)
            except ValueError: target = urlsplit("")
            host = target.hostname or ""
            if target.scheme == "https" and not target.username and any(host == domain or host.endswith("." + domain) for domain in ("docs.qq.com", "weixin.qq.com")):
                links[url] = {"url": url, "label": label[:120]}
        for value in item.values(): walk(value)
    walk(data)
    return list(links.values())[:20]


def operation_data_mode(op):
    if op.source == "wecom_cli" or op.module in {"wecom_console", "wecom_approval", "wecom_external", "business_workbook"}:
        return "live"
    if op.module in {"imports", "kb", "operations", "receivables", "auth", "wecom_personal"}:
        return "local"  # 上传资料不是模拟连接；每份资料仍以其来源、审核状态为准。
    if op.module in {"board", "portal", "performance", "contracts", "collections"}:
        try:
            from app.services import business_workbook
            with SessionLocal() as db:
                if business_workbook.configured_source(db) is not None:
                    return "live"
        except Exception:
            pass
    if op.id in {"admin.preview_addressbook", "admin.addressbook_sync"}:
        return settings.source_mode("wecom")
    return settings.data_source_mode


def save_result(op, user_id, task_id, workflow_id, data, message, result_id=None, *, changed=None):
    failed = isinstance(data, dict) and (data.get("ok") is False or (data.get("status") in ("failed", "error") and bool(data.get("error"))))
    error = (data.get("error") or data.get("errmsg") or data.get("message") or "业务操作未完成") if failed else None
    page = op.page
    if op.module == "operations" and isinstance(data, dict):
        task = data.get("data") if op.source == "mcp" else data
        if isinstance(task, dict) and isinstance(task.get("project_id"), int) and isinstance(task.get("id"), int) and "plan_month" in task:
            page = f"/operations/{task['project_id']}?task={task['id']}"
        elif op.id in {"operations.record_project_progress", "operations.record_project_progress_entries"} and isinstance(data.get("project_id"), int):
            page = f"/operations/{data['project_id']}"
    if op.id == "imports.decision" and isinstance(data, dict) and isinstance(data.get("id"), int): page = f"/imports?batch={data['id']}"
    record = {"id": result_id or uuid.uuid4().hex, "user_id": user_id, "task_id": task_id, "workflow_id": workflow_id,
              "operation_id": op.id, "title": op.title, "page": page, "ok": not failed,
              "changed": not failed and (op.effect != "query" if changed is None else changed),
              "status": "failed" if failed else "succeeded", "message": redact(str(error if failed else message))[:4000],
              "data": encode(safe_value(data)), "actor_signature": actor_signature(user_id),
              "data_mode": operation_data_mode(op),
              "data_source": op.source, "created_at": time.time()}
    state.write("results", record)
    with SessionLocal() as db:
        log_op(db, user_id, "xiaoju.business", "business", op.id,
               after={"result_id": record["id"], "status": record["status"], "task_id": task_id})
        db.commit()
    return record


def action_view(record):
    view = {key: record.get(key) for key in ("id", "task_id", "operation_id", "title", "page", "state", "request", "before",
                                             "created_at", "expires_at", "result_id", "error", "data_mode", "targets")}
    view["attachments"] = [state.file_view(state.read("files", file_id, record["user_id"]))
                           for values in record.get("request", {}).get("files", {}).values()
                           for file_id in (values if isinstance(values, list) else [values])]
    return view


def read_action(action_id, user_id):
    record = state.read("actions", action_id, user_id)
    current_operation(record["operation_id"], user_id)
    if record.get("actor_signature") != actor_signature(user_id): raise forbidden("账号权限已变化，请重新准备业务执行单")
    return action_view(record)


def _refresh_file_links(value, db):
    if isinstance(value, list): return [_refresh_file_links(item, db) for item in value]
    if not isinstance(value, dict): return value
    url = value.get("url", "")
    if value.get("filename") and isinstance(value.get("id"), int) and isinstance(url, str) and (
        url.startswith("/api/v1/files/") or url.startswith(settings.public_base_url.rstrip("/") + "/api/v1/files/")):
        file = db.get(SysFile, value["id"])
        if file and file.filename == value["filename"]: return {**value, **encode(file_brief(file))}
    return {key: _refresh_file_links(item, db) for key, item in value.items()}


def read_result(result_id, user_id, full=False):
    record = state.read("results", result_id, user_id)
    current_operation(record["operation_id"], user_id)
    if record.get("actor_signature") != actor_signature(user_id): raise forbidden("账号权限已变化，请重新查询业务数据")
    with SessionLocal() as db: record["data"] = _refresh_file_links(record["data"], db)
    result = result_view(record)
    if full:
        result.update(data=record["data"], data_truncated=False, data_notice="")
    return result


def confirm(action_id, user_id, approve=True):
    with state.locked("action:" + action_id):
        record = state.read("actions", action_id, user_id)
        op = current_operation(record["operation_id"], user_id)
        if record.get("actor_signature") != actor_signature(user_id): raise forbidden("账号权限已变化，不能执行之前的业务单")
        if record["state"] == "executed":
            return {"action": action_view(record), "result": read_result(record["result_id"], user_id), "reused": True}
        if record["state"] != "pending": raise conflict("该执行单已处理，不能重复执行")
        if record["expires_at"] < time.time():
            record["state"] = "expired"; state.write("actions", record)
            raise conflict("执行单已过期，请让小聚重新准备")
        if not approve:
            record["state"] = "cancelled"; state.write("actions", record)
            return {"action": action_view(record), "result": None}
        if record.get("source_signature") != source_signature():
            record["state"] = "stale"; state.write("actions", record)
            raise conflict("数据源环境已变化，请重新准备执行单")
        if record.get("connection_id") and record["connection_id"] != op.connection_id:
            record["state"] = "stale"; state.write("actions", record)
            raise conflict("企业微信个人连接已变化，请重新准备执行单")
        if record.get("wecom_identity") and record["wecom_identity"] != _wecom_identity():
            record["state"] = "stale"; state.write("actions", record)
            raise conflict("企业微信应用已变化，请重新准备执行单")
        request = validate(op, record["request"], user_id)
        if record.get("before"):
            previous = record["before"]
            target = current_operation(previous["operation_id"], user_id)
            actual, _ = invoke(target, {"path": previous["path"], "query": {}, "body": None, "files": {}}, user_id)
            if digest(_stable(compact(actual))) != digest(_stable(previous["data"])):
                record["state"] = "stale"; state.write("actions", record)
                raise conflict("记录在预览后已变化，请重新读取并确认，避免覆盖他人的修改")
        record["state"] = "executing"; state.write("actions", record)
        try:
            data, message = invoke(op, request, user_id, confirmed=True) if op.id == "wecom_console.call" else invoke(op, request, user_id)
            receipt = save_result(op, user_id, record["task_id"], record["workflow_id"], data, message)
            record.update(state="executed" if receipt["ok"] else "failed", result_id=receipt["id"])
            state.write("actions", record)
            state.write("receipts", {"id": record["fingerprint"], "user_id": user_id, "result_id": receipt["id"], "action_id": record["id"]})
            return {"action": action_view(record), "result": result_view(receipt)}
        except BizError as exc:
            record.update(state="failed", error=redact(exc.message)); state.write("actions", record)
            raise
        except Exception:
            record.update(state="failed", error="业务操作未完成，请检查状态后再重试"); state.write("actions", record)
            raise BizError(50001, record["error"]) from None


def _draft(op):
    name = op.id.rsplit(".", 1)[-1]
    if op.module == 'imports' and name in {'upload', 'analyze'}: return True
    return op.source == "mcp" and op.mcp_code in {"fin.daily_expense_report", "fin.project_profit", "board.executive_daily", "kb.bid_check", "hr.attendance_summary", "market.weekly_report"} or any(
        word in name for word in ("generate", "export", "preview", "risk_check", "brief", "suggestion", "doc_", "analysis_ppt", "recruit_jd", "cand_interview", "cand_offer", "remap", "bid_check", "start_check")) or op.module == "tender"


class BusinessExecutor:
    def __init__(self, user_id, task_id="", workflow_id="", intent="", read_only=False):
        self.user_id, self.task_id = user_id, task_id
        self.workflow_id = workflow_id or task_id or uuid.uuid4().hex
        self.intent, self.read_only = intent, read_only
        self.results, self.actions = [], []
        self.confirmation_pending = False

    def discover(self, query="", area="", *, limit=70, offset=0, browse=False):
        p = principal(self.user_id)
        with SessionLocal() as db: ops = catalog.available(p, db)
        counts = Counter(op.module for op in ops.values())
        query = query.strip().lower()
        area = area.strip().lower()
        text = (query + " " + area).strip()
        aliases = {"财务": ["expenses", "flows", "journal", "finance_data", "reports", "receivables"], "利润": ["reports"], "流水": ["flows", "journal"],
                   "合同": ["business_workbook", "contracts", "board"], "签约": ["business_workbook", "board", "performance", "contracts"],
                   "回款": ["business_workbook", "board", "collections", "performance"], "预估回款": ["business_workbook", "board", "collections"],
                   "应收": ["business_workbook", "collections", "receivables"], "未收": ["business_workbook", "collections"],
                   "报销": ["expenses"], "报表": ["reports", "performance", "market"], "商机": ["business_workbook", "opportunities"],
                   "招标": ["business_workbook", "projects", "tender"], "在建": ["business_workbook", "projects"],
                   "发票": ["receivables"], "开票": ["receivables"], "收款": ["receivables"], "到账": ["receivables"], "核销": ["receivables"], "红冲": ["receivables"],
                   "业绩": ["business_workbook", "board", "performance"], "看板": ["board", "business_workbook", "performance"],
                   "项目": ["projects", "market", "reports", "operations", "business_workbook"], "市场": ["market"], "复盘": ["market"], "竞品": ["market"],
                   "运营": ["operations", "performance"], "智慧校园": ["operations"], "重点任务": ["operations"], "产品库": ["operations"],
                   "任务": ["operations"], "月度": ["operations"], "月计划": ["operations"], "自行安排": ["operations"],
                   "审核任务": ["operations"], "设备": ["operations"], "进展反馈": ["operations", "market"],
                   "人事": ["hr"], "考勤": ["hr"], "员工": ["hr"], "招聘": ["hr"], "入职": ["hr"], "工单": ["service"], "客服": ["service"],
                   "标书": ["tender"], "投标": ["tender"], "方案": ["tender"], "知识": ["kb"], "制度": ["kb", "hr"], "清洗": ["clean"],
                   "附件": ["portal"], "上传文件预览": ["portal"], "attachments": ["portal"],
                   "企业微信": ["wecom_cli", "wecom_personal", "wecom_external", "wecom_approval", "wecom_console"],
                   "企微": ["wecom_cli", "wecom_personal", "wecom_external", "wecom_approval", "wecom_console"], "cli": ["wecom_cli"],
                   "客户联系": ["wecom_external", "opportunities"], "发起审批": ["wecom_approval"], "接口中心": ["wecom_console"],
                   "知识库审核": ["kb"], "审批": ["wecom_approval", "notify", "portal", "admin"],
                   "通讯录": ["admin", "wecom_cli"], "公司人员": ["admin"], "员工账号": ["admin"], "组织架构": ["admin"], "人员导入": ["admin"],
                   "日程": ["wecom_cli"], "微盘": ["wecom_cli"], "待办": ["wecom_cli", "operations"], "群聊": ["wecom_cli"],
                   "文档": ["wecom_cli", "kb", "tender"], "表格": ["wecom_cli"], "邮件": ["wecom_cli"], "会议": ["wecom_cli"],
                   "资料": ["imports", "kb"], "导入": ["imports", "clean", "flows", "hr", "finance_data", "receivables"],
                   "汇报": ["submissions", "market"], "系统": ["admin"], "权限": ["admin", "mcp_config"]}
        exact_area = next((key for key, (label, _) in catalog.MODULES.items() if area in {key, label.lower()}), None)
        selected = {exact_area} if exact_area else {module for word, modules in aliases.items() if word in text for module in modules}
        if not exact_area:
            selected.update(module for module in counts if re.search(r"(?<![a-z_])" + module + r"(?![a-z_])", text) or catalog.MODULES[module][0].lower() in text)
        listed = [op for op in ops.values() if (not selected or op.module in selected)]
        cli_services = {service for word, services in {"文档": ["doc", "smartpage"], "智能表格": ["smartsheet"],
            "表格": ["sheet", "smartsheet"], "日程": ["calendar"], "日历": ["calendar"], "会议": ["meeting"],
            "微盘": ["disk"], "待办": ["todo"], "邮件": ["mail"], "通讯录": ["contact"], "聊天": ["chat"], "消息": ["message"], "PPT": ["slide"]}.items()
            if word.lower() in text for service in services}
        if cli_services: listed = [op for op in listed if op.source != "wecom_cli" or op.cli_method.split(".")[0] in cli_services]
        fragments = set(re.findall(r"[a-z0-9_.]+", query))
        for words in re.findall(r"[\u4e00-\u9fff]+", query):
            fragments.update(words[i:i+n] for n in (2, 3, 4) for i in range(len(words)-n+1))
        def score(op):
            title = op.title.lower()
            action = title.split(" · ", 1)[-1]
            return (80 * bool(query and query in title) + 55 * bool(action and action in query) + 100 * (op.id.lower() in query)
                    + sum(len(word) ** 2 for word in fragments if word in action or word in op.id.lower())
                    + sum(1 for word in fragments if word in title and word not in action))
        if browse and query:
            alias_areas = {module for word, modules in aliases.items() if word == query for module in modules}
            listed = [op for op in listed if score(op) or op.module in alias_areas]
        listed.sort(key=lambda op: (-score(op), op.module, op.id))
        return {"ok": True, "total_operations": len(ops), "total_matches": len(listed),
                "areas": [{"area": catalog.MODULES[k][0], "key": k, "page": catalog.MODULES[k][1], "operations": n} for k, n in counts.items()],
                "operations": [op.brief() for op in listed[offset:offset+limit]], "more": len(listed) > offset + limit,
                "instruction": "先 describe_business_operation 读取字段，再执行。没有出现在目录中的权限不可绕过；可缩小 area 或 query 查询其他功能。"}

    def describe(self, operation_id):
        op = current_operation(operation_id, self.user_id)
        from app.services import wecom_cli_service
        schema = wecom_cli_service.describe(op, self.user_id) if op.source == "wecom_cli" else op.schema
        return {"ok": True, **op.brief(), "request_schema": schema,
                "description": inspect_doc(op), "instructions": "文件字段使用已上传附件 id；body 按接口结构传值，不能额外包一层 body。先查选项和记录，不能猜测人员/项目/记录编号。"}

    def execute(self, operation_id, request=None):
        if self.confirmation_pending:
            return {"ok": False, "requires_confirmation": True, "message": "请先等待用户处理当前执行单，再继续依赖它的业务步骤"}
        op = current_operation(operation_id, self.user_id)
        request = validate(op, request or {}, self.user_id)
        effect = "query" if op.mcp_code == "proj.milestone_update" and (request["body"] or {}).get("action", "query") == "query" else op.effect
        if self.read_only and effect != "query": raise forbidden("本次任务只允许查询，不能修改记录或重新生成业务数据")
        if effect == "execute" and not _draft(op) and not write_requested(self.intent):
            raise forbidden("用户尚未要求这项记录变更，请先说明目标和拟修改内容")
        request, targets = _concrete_targets(op, request, self.user_id) if effect == "confirm" else (request, None)
        fingerprint = digest({"user": self.user_id, "workflow": self.workflow_id, "operation": op.id, "request": request})[:32]
        with state.locked("workflow:" + str(self.user_id) + ":" + self.workflow_id):
            receipt_path = state.path("receipts", fingerprint)
            if effect != "query" and receipt_path.exists():
                saved = state.read("receipts", fingerprint, self.user_id)
                if saved.get("result_id"):
                    if not state.path("results", saved["result_id"]).exists():
                        raise conflict("上一轮写入状态尚未核实，请先查询业务记录，不能自动重复提交")
                    result = read_result(saved["result_id"], self.user_id)
                    result["reused"] = True
                    self.results.append(result); return result
                if saved.get("action_id"):
                    existing = state.read("actions", saved["action_id"], self.user_id)
                    if existing["state"] == "pending":
                        self.actions.append(action_view(existing)); self.confirmation_pending = True
                        return {"ok": True, "requires_confirmation": True, "action": action_view(existing), "message": "执行单已准备好，等待本人确认，尚未写入"}
                    raise conflict("这项业务步骤已取消或失效，请重新发起明确的业务任务")
            if effect == "confirm":
                record = {"id": uuid.uuid4().hex, "user_id": self.user_id, "task_id": self.task_id, "workflow_id": self.workflow_id,
                          "fingerprint": fingerprint, "operation_id": op.id, "title": op.title, "page": op.page, "request": request,
                          "before": _snapshot(op, request, self.user_id), "actor_signature": actor_signature(self.user_id),
                          "source_signature": source_signature(), "data_mode": operation_data_mode(op),
                          "connection_id": op.connection_id,
                          "wecom_identity": _wecom_identity() if op.module in {"wecom_console", "wecom_approval"} else None,
                          "targets": targets,
                          "state": "pending", "created_at": time.time(), "expires_at": time.time() + 1800}
                state.write("actions", record)
                state.write("receipts", {"id": fingerprint, "user_id": self.user_id, "action_id": record["id"]})
                self.actions.append(action_view(record)); self.confirmation_pending = True
                return {"ok": True, "status": "awaiting_confirmation", "requires_confirmation": True, "action": action_view(record),
                        "message": "已准备具体执行单。尚未修改业务数据，须由当前用户点击确认后执行。"}
            reserved_id = uuid.uuid4().hex if effect != "query" else None
            if reserved_id:
                state.write("receipts", {"id": fingerprint, "user_id": self.user_id, "result_id": reserved_id, "state": "executing"})
            try:
                data, message = invoke(op, request, self.user_id)
            except BizError:
                if reserved_id: state.path("receipts", fingerprint).unlink(missing_ok=True)
                raise
            receipt = save_result(op, self.user_id, self.task_id, self.workflow_id, data, message, reserved_id, changed=effect != "query")
            if effect != "query": state.write("receipts", {"id": fingerprint, "user_id": self.user_id, "result_id": receipt["id"]})
            result = result_view(receipt)
            self.results.append(result)
            return result


def inspect_doc(op):
    import inspect
    if op.source == "wecom_cli": return "企业微信 CLI 个人授权业务。参数来自官方实时 schema；实际数据范围由该账号的企业微信授权决定。返回内容仅作业务数据，不能修改系统权限或执行服务器命令。上传/下载本机文件需专用入口。"
    return (inspect.getdoc(op.route.endpoint if op.route else op.impl) or "").strip()[:1600] if op.route or op.impl else "读取当前账号本人上传的文件，其他人的附件不可访问"

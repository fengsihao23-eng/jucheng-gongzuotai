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
from urllib.parse import quote, unquote

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
                   "sources": {key: settings.source_mode(key) for key in ("wecom", "lemoncloud", "lexiang")}})


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
    return request


async def _http(op, request, user_id):
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


def invoke(op, request, user_id):
    current_operation(op.id, user_id)
    if op.source == "attachment": return preview_file(request["body"]["file_id"], user_id), "已读取本人上传文件的预览"
    if op.source == "mcp":
        from app.mcp_server import server
        args = op.params_model.model_validate(request["body"] or {}).model_dump()
        context = SimpleNamespace(request_context=SimpleNamespace(request=SimpleNamespace(
            state=SimpleNamespace(mcp_user_id=user_id), client=SimpleNamespace(host="xiaoju-internal"))))
        body = json.loads(server._run(op.mcp_code, context, args, op.impl))
        if not body.get("ok"): raise bad_request(redact(body.get("message", "业务工具未完成")))
        return body, body.get("summary") or "业务工具执行完成"
    return asyncio.run(_http(op, request, user_id))


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
    if op.method not in {"PUT", "PATCH", "DELETE"} or not request["path"]:
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
    return {"ok": record["ok"], "status": record["status"], "result_id": record["id"], "operation_id": record["operation_id"],
            "title": record["title"], "message": record["message"], "page": record["page"], "data": compact(data),
            "files": files_in(data), "data_mode": record["data_mode"], "reused": record.get("reused", False)}


def save_result(op, user_id, task_id, workflow_id, data, message, result_id=None):
    failed = isinstance(data, dict) and isinstance(data.get("status"), str) and data["status"] in {"failed", "error"} and bool(data.get("error"))
    record = {"id": result_id or uuid.uuid4().hex, "user_id": user_id, "task_id": task_id, "workflow_id": workflow_id,
              "operation_id": op.id, "title": op.title, "page": op.page, "ok": not failed,
              "status": "failed" if failed else "succeeded", "message": redact(str(data.get("error"))) if failed else redact(str(message))[:4000],
              "data": encode(safe_value(data)), "actor_signature": actor_signature(user_id),
              "data_mode": settings.data_source_mode, "created_at": time.time()}
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
    if full: result["data"] = record["data"]
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
            data, message = invoke(op, request, user_id)
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
    return op.source == "mcp" and op.mcp_code in {"fin.daily_expense_report", "fin.project_profit", "board.executive_daily", "kb.bid_check", "hr.attendance_summary", "market.weekly_report"} or any(
        word in name for word in ("generate", "export", "preview", "risk_check", "brief", "suggestion", "doc_", "analysis_ppt", "recruit_jd", "cand_interview", "cand_offer", "remap", "bid_check", "start_check")) or op.module == "tender"


class BusinessExecutor:
    def __init__(self, user_id, task_id="", workflow_id="", intent="", read_only=False):
        self.user_id, self.task_id = user_id, task_id
        self.workflow_id = workflow_id or task_id or uuid.uuid4().hex
        self.intent, self.read_only = intent, read_only
        self.results, self.actions = [], []
        self.confirmation_pending = False

    def discover(self, query="", area=""):
        p = principal(self.user_id)
        with SessionLocal() as db: ops = catalog.available(p, db)
        counts = Counter(op.module for op in ops.values())
        text = (query + " " + area).lower()
        aliases = {"财务": ["expenses", "flows", "journal", "finance_data", "reports"], "利润": ["reports"], "流水": ["flows", "journal"],
                   "合同": ["contracts"], "报销": ["expenses"], "报表": ["reports", "performance", "market"], "商机": ["opportunities"],
                   "业绩": ["performance"], "项目": ["projects", "market", "reports"], "市场": ["market"], "复盘": ["market"], "竞品": ["market"],
                   "人事": ["hr"], "考勤": ["hr"], "员工": ["hr"], "招聘": ["hr"], "入职": ["hr"], "工单": ["service"], "客服": ["service"],
                   "标书": ["tender"], "投标": ["tender"], "方案": ["tender"], "知识": ["kb"], "制度": ["kb", "hr"], "清洗": ["clean"],
                   "附件": ["portal"], "上传文件预览": ["portal"], "attachments": ["portal"],
                   "导入": ["clean", "flows", "hr", "finance_data"], "汇报": ["submissions", "market"], "审批": ["portal", "admin"], "系统": ["admin"], "权限": ["admin", "mcp_config"]}
        selected = {module for word, modules in aliases.items() if word in text for module in modules}
        selected.update(module for module in counts if module in text or catalog.MODULES[module][0] in text)
        listed = [op for op in ops.values() if not selected or op.module in selected]
        def score(op):
            return sum(word in op.title for word in re.findall(r"[\u4e00-\u9fff]{2,}|[a-z_]+", text)) + int(op.id.lower() in text) * 10
        listed.sort(key=lambda op: (-score(op), op.module, op.id))
        return {"ok": True, "total_operations": len(ops), "areas": [{"area": catalog.MODULES[k][0], "key": k, "operations": n} for k, n in counts.items()],
                "operations": [op.brief() for op in listed[:70]], "more": len(listed) > 70,
                "instruction": "先 describe_business_operation 读取字段，再执行。没有出现在目录中的权限不可绕过；可缩小 area 或 query 查询其他功能。"}

    def describe(self, operation_id):
        op = current_operation(operation_id, self.user_id)
        return {"ok": True, **op.brief(), "request_schema": op.schema,
                "description": inspect_doc(op), "instructions": "文件字段使用已上传附件 id；body 按接口结构传值，不能额外包一层 body。先查选项和记录，不能猜测人员/项目/记录编号。"}

    def execute(self, operation_id, request=None):
        if self.confirmation_pending:
            return {"ok": False, "requires_confirmation": True, "message": "请先等待用户处理当前执行单，再继续依赖它的业务步骤"}
        op = current_operation(operation_id, self.user_id)
        request = validate(op, request or {}, self.user_id)
        effect = "query" if op.mcp_code == "proj.milestone_update" and (request["body"] or {}).get("action", "query") == "query" else op.effect
        if self.read_only and effect != "query": raise forbidden("本次任务只允许查询，不能修改记录或重新生成业务数据")
        mutation_words = ("登记", "新增", "创建", "修改", "更新", "提交", "归档", "录入", "导入", "处理", "推进", "标记", "维护", "分配", "归集", "重算", "发送", "通知", "删除", "撤销", "取消", "运行", "执行", "完成", "改成")
        if effect == "execute" and not _draft(op) and not any(word in self.intent for word in mutation_words):
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
                          "source_signature": source_signature(), "data_mode": settings.data_source_mode,
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
            receipt = save_result(op, self.user_id, self.task_id, self.workflow_id, data, message, reserved_id)
            if effect != "query": state.write("receipts", {"id": fingerprint, "user_id": self.user_id, "result_id": receipt["id"]})
            result = result_view(receipt)
            self.results.append(result)
            return result


def inspect_doc(op):
    import inspect
    return (inspect.getdoc(op.route.endpoint if op.route else op.impl) or "").strip()[:1600] if op.route or op.impl else "读取当前账号本人上传的文件，其他人的附件不可访问"

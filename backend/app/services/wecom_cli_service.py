"""按账号隔离的企业微信官方 CLI。只执行 discovery 中的业务方法，凭证不进入模型。"""
import json
import os
import re
import subprocess
import threading
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator

from app.core.config import BASE_DIR, settings
from app.core.response import BizError, bad_request, forbidden

_lock = threading.RLock()
SERVICES = {"calendar": "日程", "chat": "聊天记录", "contact": "通讯录", "disk": "微盘", "doc": "文档",
            "mail": "邮件", "message": "消息", "meeting": "会议", "sheet": "在线表格", "smartpage": "智能文档",
            "smartsheet": "智能表格", "todo": "待办", "identity": "当前身份", "slide": "演示文稿"}
READ_METHODS = {"get", "list", "search", "whoami"}
LOCAL_FILE_METHODS = {"upload", "download", "import", "export"}


def home(user_id):
    return settings.storage_dir.parent / "xiaoju" / "wecom-cli" / str(int(user_id))


def launcher():
    return Path(settings.wecom_cli_bin) if settings.wecom_cli_bin else BASE_DIR / "agent_runtime/node_modules/.bin/wecom-cli"


def binding(user_id):
    folder = home(user_id)
    try:
        document = json.loads((folder / "binding.json").read_text())
        if document.get("user_id") != user_id or not re.fullmatch(r"[a-f0-9]{32}", document.get("binding_id", "")):
            return None
        if not all((folder / "config" / name).is_file() for name in ("credentials.enc", ".encryption_key")):
            return None
        return document
    except (OSError, ValueError, TypeError):
        return None


def eligible_methods(document):
    result = {}
    for service in document.get("services", []):
        if service.get("name") not in SERVICES: continue
        for method in service.get("methods", []):
            name = method.get("name", "")
            parts = name.split(".")
            if not re.fullmatch(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+", name) or parts[0] != service["name"]: continue
            # Local file paths must use a future ownership-aware upload/download
            # adapter; the model cannot pass arbitrary server files to the CLI.
            if any(part in LOCAL_FILE_METHODS for part in parts): continue
            result[name] = method
    return result


def status(user_id):
    document = binding(user_id)
    needs_password = bool(document and requires_password_change(user_id))
    return {"available": bool(document and launcher().is_file() and not needs_password), "configured": bool(document),
            "requires_password_change": needs_password, "scope": "personal", "provider": "wecom-cli",
            "services": len(document.get("services", [])) if document else 0,
            "operations": len(eligible_methods(document)) if document else 0,
            "message": "请先在个人中心更换演示密码，再使用真实企业微信个人连接" if needs_password else
                       "企业微信 CLI 个人连接已配置，实际数据范围由企业微信授权决定" if document else "当前账号尚未绑定企业微信 CLI"}


def requires_password_change(user_id):
    if settings.environment != "prod" or not settings.password_login_enabled: return False
    from app.core.db import SessionLocal
    from app.models import SysUser
    with SessionLocal() as db:
        user = db.get(SysUser, user_id)
        return bool(user and _demo_password_hash(user.password_hash, settings.demo_password))


@lru_cache(maxsize=128)
def _demo_password_hash(encoded, configured_demo_password):
    from app.core.security import verify_password
    return any(verify_password(password, encoded) for password in {configured_demo_password, "Jc@2026"} if password)


def _owned(user_id, binding_id=None):
    document = binding(user_id)
    if not document or not launcher().is_file(): raise forbidden("当前账号没有可用的企业微信 CLI 个人连接")
    if requires_password_change(user_id): raise forbidden("请先在个人中心更换演示密码，再使用真实企业微信个人连接")
    if binding_id and document["binding_id"] != binding_id: raise forbidden("企业微信连接已变化，请重新准备业务操作")
    return document


def _run(user_id, args, *, workspace_id=None, binding_id=None):
    _owned(user_id, binding_id)
    folder = home(user_id)
    work = folder / "work"
    if workspace_id is not None:
        if not re.fullmatch(r"[a-f0-9]{32}", workspace_id):
            raise bad_request("企业微信导入工作区无效")
        work = work / "imports" / workspace_id
    work.mkdir(parents=True, exist_ok=True, mode=0o700)
    env = {key: value for key, value in os.environ.items() if not key.startswith("WECOM_CLI_")}
    env["WECOM_CLI_CONFIG_DIR"] = str(folder / "config")
    try:
        process = subprocess.run([str(launcher()), *args, *(["--output-dir", str(work)] if workspace_id else [])], capture_output=True, text=True, timeout=70,
                                 cwd=work, env=env)
    except subprocess.TimeoutExpired:
        raise bad_request("企业微信调用超时，写入结果需要先核实，不能自动重复提交") from None
    except OSError:
        raise bad_request("企业微信 CLI 运行环境不可用，请检查安装") from None
    if len(process.stdout) > 4 * 1024 * 1024: raise bad_request("企业微信结果过大，请缩小查询范围或分页")
    try: result = json.loads(process.stdout)
    except (ValueError, TypeError): raise bad_request("企业微信 CLI 未返回有效结果，请检查连接") from None
    error = result.get("error") if isinstance(result, dict) else None
    remote_code = result.get("errcode", 0) if isinstance(result, dict) else 0
    if process.returncode or error or remote_code:
        code = error.get("code", "unknown") if isinstance(error, dict) else remote_code or "unknown"
        if code == 851008:
            help_text = str(result.get("help_message") or "")
            for url in re.findall(r'https?://[^\s)"<>]+', help_text):
                try: target = urlsplit(url)
                except ValueError: continue
                if target.scheme == "https" and target.hostname in {"work.weixin.qq.com", "open.work.weixin.qq.com"} and not target.username:
                    scope = re.search(r"未被授权[「『](.*?)[」』]", help_text)
                    label = scope.group(1)[:100] if scope else "当前业务能力"
                    raise BizError(40301, f"企业微信个人连接已绑定，尚需开通「{label}」；机器人创建者可通过官方入口授权，其他情况需机器人管理员处理",
                                   data={"authorization_url": url, "authorization_label": "开通" + label})
        # Third party errors can echo headers or credentials. Return the code only.
        raise bad_request(f"企业微信调用未完成（错误码 {code}），请检查该账号的授权与业务参数")
    _owned(user_id, binding_id)
    return result


# 专用资料入口可下载微盘原件，但通用模型工具仍不能自由传入文件路径。
IMPORT_READ_METHODS = frozenset({"disk.files.search", "disk.files.get", "disk.files.download", "doc.search",
    "doc.contents.get", "smartpage.pages.get", "smartpage.databases.get", "smartsheet.sheets.list",
    "smartsheet.fields.list", "smartsheet.records.list", "sheet.workbook.get", "sheet.cells.get"})


def import_read(user_id, method, payload, binding_id, workspace_id):
    document = _owned(user_id, binding_id)
    declared = {m.get("name") for s in document.get("services", []) for m in s.get("methods", [])}
    if method not in IMPORT_READ_METHODS or method not in declared:
        raise forbidden("当前企业微信连接未提供这项资料读取功能")
    schema = _run(user_id, ["schema", "get", method], binding_id=binding_id)
    if schema.get("method") != method:
        raise bad_request("企业微信接口定义不匹配，请重新连接后再试")
    request = resolve(schema.get("request", {}), schema.get("schemas", {}))
    # CLI 的部分数组参数把元素枚举写在数组本层（如 include），按其接口语义规范化。
    def normalize(node):
        if isinstance(node, list): return [normalize(x) for x in node]
        if not isinstance(node, dict): return node
        value = {k: normalize(v) for k, v in node.items()}
        if value.get("type") == "array" and value.get("enum") and not any(isinstance(x, list) for x in value["enum"]):
            value["items"] = {**value.get("items", {}), "enum": value.pop("enum")}
        return value
    request = normalize(request)
    request["additionalProperties"] = False
    if list(Draft202012Validator(request).iter_errors(payload)):
        raise bad_request("当前企业微信接口参数已变化，请联系管理员核对读取适配")
    return _run(user_id, [*method.split("."), "--json", json.dumps(payload, ensure_ascii=False)],
                binding_id=binding_id, workspace_id=workspace_id)


def resolve(schema, definitions, trail=()):
    if isinstance(schema, list): return [resolve(item, definitions, trail) for item in schema]
    if not isinstance(schema, dict): return schema
    if "$ref" in schema:
        name = schema["$ref"].rsplit("/", 1)[-1]
        if name in trail: return {"type": "object"}
        target = definitions.get(name)
        if not isinstance(target, dict): raise bad_request("企业微信参数定义不完整，请刷新能力目录")
        return resolve({**target, **{key: value for key, value in schema.items() if key != "$ref"}}, definitions, (*trail, name))
    return {key: resolve(value, definitions, trail) for key, value in schema.items()}


def method_schema(user_id, method, binding_id=None):
    document = _owned(user_id, binding_id)
    if method not in eligible_methods(document): raise forbidden("该企业微信方法未开放或需要专用的文件处理入口")
    folder = home(user_id) / "schemas"
    target = folder / (method + ".json")
    with _lock:
        if target.exists():
            try: record = json.loads(target.read_text())
            except (ValueError, OSError): record = None
            if record and record.get("binding_id") == document["binding_id"]: return record["schema"]
        remote = _run(user_id, ["schema", "get", method])
        if remote.get("method") != method: raise bad_request("企业微信返回的方法定义不匹配")
        schema = resolve(remote.get("request", {}), remote.get("schemas", {}))
        def has_local_file(node):
            if isinstance(node, list): return any(has_local_file(item) for item in node)
            if not isinstance(node, dict): return False
            unsafe = {"file_path", "local_path", "output_path", "input_path", "source_path", "source_file", "input_file", "local_file", "local_file_path", "filepath"}
            if unsafe & set(node.get("properties", {})): return True
            return any(has_local_file(item) for item in node.values())
        if has_local_file(schema): raise forbidden("该企业微信方法包含本机文件参数，需要专用的本人附件处理入口")
        if schema.get("type") == "object": schema["additionalProperties"] = False
        Draft202012Validator.check_schema(schema)
        from app.services.xiaoju_tools import private_write
        private_write(target, {"binding_id": document["binding_id"], "schema": schema})
        return schema


def operations(user_id):
    document = binding(user_id)
    if not document or not launcher().is_file() or requires_password_change(user_id): return {}
    from app.services.xiaoju_business_catalog import Operation
    result = {}
    for name, metadata in eligible_methods(document).items():
        parts = name.split(".")
        operation_id = "wecom_cli." + name
        effect = "query" if parts[-1] in READ_METHODS else "confirm"
        result[operation_id] = Operation(operation_id, "wecom_cli",
            f"企业微信 · {SERVICES[parts[0]]} · {metadata.get('description', name)}"[:120], "wecom_cli", effect, page="/workbuddy",
            schema={"path": {"type": "object", "additionalProperties": False}, "query": {"type": "object", "additionalProperties": False},
                    "body": {"type": "object"}, "body_required": True, "content_type": "application/json"},
            cli_method=name, connection_id=document["binding_id"])
    return result


def describe(op, user_id):
    return {**op.schema, "body": method_schema(user_id, op.cli_method, op.connection_id)}


def execute(op, request, user_id):
    _owned(user_id, op.connection_id)
    schema = method_schema(user_id, op.cli_method, op.connection_id)
    payload = request.get("body") or {}
    errors = list(Draft202012Validator(schema).iter_errors(payload))
    if errors: raise bad_request("企业微信业务参数不符合实际接口定义，请先读取功能参数")
    serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(serialized.encode()) > 48000: raise bad_request("企业微信参数过长，请分批处理")
    result = _run(user_id, [*op.cli_method.split("."), "--json", serialized])
    return result, "已从企业微信读取真实数据" if op.effect == "query" else "企业微信业务操作已执行"

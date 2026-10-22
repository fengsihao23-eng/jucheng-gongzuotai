"""小聚的执行工具。凭证留在 Python；Pi 仅收到来源、状态及执行结果。"""
import base64
import fcntl
import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
from pathlib import Path

import httpx
from dotenv import dotenv_values
from sqlalchemy import select

from app.core.audit import log_op
from app.core.config import BASE_DIR, settings
from app.core.db import SessionLocal
from app.core.deps import build_principal
from app.core.security import hash_token, new_access_token, password_version
from app.core.response import BizError
from app.models import SysUser
from app.services import assistant_service as connectors

PROVIDERS = ["workbuddy", "wecom", "lemoncloud"]
BASIC = {"wecom": ["wecom_corp_id", "wecom_agent_id", "wecom_secret"],
         "lemoncloud": ["lemoncloud_app_id", "lemoncloud_app_secret"]}
LOGIN_URLS = {"wecom": "https://work.weixin.qq.com/", "lemoncloud": "https://www.ningmengyun.com/"}
CLI_NAMES = ("pi", "workbuddy", "codebuddy", "wecom-cli", "wecom", "lemoncloud", "nmy")


def spec(name, description, properties=None, required=None):
    return {"name": name, "description": description, "parameters": {
        "type": "object", "properties": properties or {}, "required": required or [], "additionalProperties": False}}


TOOL_SPECS = [
    spec("list_business_capabilities", "查找当前账号能真实执行的全部业务功能。财务、合同商机、项目、市场、人事、客服、标书、知识库、系统管理均可；按 query/area 缩小范围。", {
        "query": {"type": "string", "default": ""}, "area": {"type": "string", "default": ""}}),
    spec("describe_business_operation", "读取业务功能的完整参数、文件字段、默认值和是否需要确认。执行前先读取，不能猜测字段或记录编号。", {
        "operation_id": {"type": "string"}}, ["operation_id"]),
    spec("execute_business_operation", "真实调用当前工作台业务服务。查询、报表和可撤回登记直接执行；重要操作先产生具体执行单，尚未写入，必须等待用户点击确认。", {
        "operation_id": {"type": "string"}, "request": {"type": "object", "properties": {
            "path": {"type": "object", "additionalProperties": True}, "query": {"type": "object", "additionalProperties": True},
            "body": {"anyOf": [{"type": "object", "additionalProperties": True}, {"type": "array", "items": {}}, {"type": "string"}, {"type": "number"}, {"type": "boolean"}, {"type": "null"}]},
            "files": {"type": "object", "additionalProperties": True}}, "additionalProperties": False}}, ["operation_id"]),
    spec("read_business_action", "核对本人执行单的当前状态与实际回执。不能通过此工具批准、伪造确认或再次提交已执行操作。", {
        "action_id": {"type": "string"}}, ["action_id"]),
    spec("read_business_result", "读取本人业务执行回执与文件，不重复提交业务操作。", {"result_id": {"type": "string"}}, ["result_id"]),
    spec("inspect_environment", "检查当前接入状态、本机 WorkBuddy 配置和可用 CLI。接入任务首先调用此工具。"),
    spec("discover_credentials", "自动读取现有环境、接入文件和 MCP 中的企业凭证。只返回来源与缺失项，不返回密钥。", {
        "provider": {"type": "string", "enum": ["wecom", "lemoncloud"]}}, ["provider"]),
    spec("install_workbuddy", "自动生成或复用个人令牌，合并到本机 WorkBuddy 的 MCP 配置，备份旧文件并验证实际 MCP 连接。用户要求接入时直接执行，不要求复制配置。"),
    spec("prepare_personal_workbuddy", "为当前员工自动生成或复用个人 WorkBuddy 授权，执行真实 MCP 自检并准备可下载的完整配置。不读取员工电脑，不要求企业密钥。"),
    spec("connect_enterprise", "使用自动发现的凭证进行真实鉴权，通过后保存接入配置。自动生成企业微信回调密钥；缺授权时仅报告需要补充的内容。", {
        "provider": {"type": "string", "enum": ["wecom", "lemoncloud"]}, "source_id": {"type": "string", "default": "auto"}}, ["provider"]),
    spec("run_cli", "直接调用本机接入 CLI 获取 --help 或 --version。传入独立参数数组，无需用户手动运行命令；连接操作使用 API 和配置工具。", {
        "program": {"type": "string", "enum": list(CLI_NAMES)}, "args": {"type": "array", "items": {"type": "string"}, "maxItems": 5}}, ["program", "args"]),
    spec("find_page", "查找用户有权访问的工作台页面，返回可点击的操作入口。", {"query": {"type": "string", "maxLength": 200}}, ["query"]),
]


def private_write(path: Path, data: dict) -> None:
    private_bytes(path, (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode())


def private_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".xiaoju-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(content); out.flush(); os.fsync(out.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def redact(text: str) -> str:
    for name in ("llm_api_key", "wecom_secret", "wecom_callback_token", "wecom_callback_aes_key", "lemoncloud_app_secret"):
        value = str(getattr(settings, name, "") or "")
        if len(value) >= 6:
            text = text.replace(value, "[已隐藏]")
    text = re.sub(r"(?:sk-|jcmcp_)[A-Za-z0-9_-]+", "[已隐藏]", text)
    return re.sub(r'(?i)((?:secret|token|password|authorization|api[_-]?key)[\w-]*[\"\s]*[:=][\"\s]*)([^\s,\"}]+)', r'\1[已隐藏]', text)


def safe_value(value):
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, list):
        return [safe_value(item) for item in value]
    if isinstance(value, dict):
        return {key: safe_value(item) for key, item in value.items()}
    return value


def import_values(provider: str, content: bytes) -> dict:
    """只接收目标系统的已知字段，不读取导入文件里的其他服务密钥。"""
    if provider not in BASIC or len(content) > 65536:
        raise ValueError("接入文件过大或系统标识不正确")
    text = content.decode("utf-8-sig")
    try:
        document = json.loads(text)
    except ValueError:
        from io import StringIO
        document = dict(dotenv_values(stream=StringIO(text), interpolate=False))
    allowed = {field[0] for field in connectors.FIELDS[provider]}
    values = {}

    def walk(item, prefix="", depth=0):
        if not isinstance(item, dict) or depth > 5:
            return
        for key, value in item.items():
            key = re.sub(r"(?<!^)(?=[A-Z][a-z])", "_", str(key)).lower().replace("-", "_")
            full = key if key.startswith(provider + "_") else f"{prefix}_{key}".strip("_")
            if full in allowed and isinstance(value, (str, int)) and str(value).strip():
                values[full] = str(value).strip()
            if isinstance(value, dict):
                walk(value, provider if key == provider else "", depth + 1)
    walk(document)
    return values


class XiaoJuTools:
    def __init__(self, user_id: int, allow_writes=True, task_id="", workflow_id="", intent=""):
        self.user_id = user_id
        self.allow_writes = allow_writes
        self.outcomes = []
        self.links = []
        self.task_id, self.workflow_id, self.intent = task_id, workflow_id, intent
        self._business = None
        self.business_errors = []
        with SessionLocal() as db:
            user = db.get(SysUser, user_id)
            self._credential_version = password_version(user.password_hash if user else None)

    @property
    def business(self):
        if self._business is None:
            from app.services.xiaoju_business import BusinessExecutor
            self._business = BusinessExecutor(self.user_id, self.task_id, self.workflow_id, self.intent, not self.allow_writes)
        return self._business

    def list_business_capabilities(self, query="", area=""):
        return self.business.discover(query, area)

    def describe_business_operation(self, operation_id):
        return self.business.describe(operation_id)

    def execute_business_operation(self, operation_id, request=None):
        result = self.business.execute(operation_id, request)
        self.business_errors = [item for item in self.business_errors if item.get("operation_id") != operation_id]
        return result

    def read_business_action(self, action_id):
        from app.services.xiaoju_business import read_action
        action = read_action(action_id, self.user_id)
        result = self.read_business_result(action["result_id"]) if action.get("result_id") else None
        if action["state"] == "pending":
            if not any(item["id"] == action_id for item in self.business.actions): self.business.actions.append(action)
            self.business.confirmation_pending = True
        return {"ok": True, "action": action, "result": result, "requires_confirmation": action["state"] == "pending"}

    def read_business_result(self, result_id):
        from app.services.xiaoju_business import read_result
        result = read_result(result_id, self.user_id)
        if not any(item["result_id"] == result_id for item in self.business.results): self.business.results.append(result)
        return result

    def _record(self, value):
        self.outcomes = [old for old in self.outcomes if old["provider"] != value["provider"]]
        self.outcomes.append(value)
        return value

    def execute(self, name: str, args: dict) -> dict:
        allowed = {item["name"] for item in TOOL_SPECS}
        if name not in allowed or not isinstance(args, dict):
            return {"ok": False, "message": "工具不存在或参数不正确"}
        try:
            if not self.allow_writes and name in {"install_workbuddy", "connect_enterprise", "prepare_personal_workbuddy"}:
                return {"ok": False, "message": "本次是状态检查，未授权修改接入配置"}
            with SessionLocal() as db:
                user = db.get(SysUser, self.user_id)
                if not user or user.status != 1 or user.is_deleted:
                    return {"ok": False, "message": "账号已停用"}
                if self._credential_version != password_version(user.password_hash):
                    return {"ok": False, "message": "账号密码已更新，请重新发起任务"}
                if not build_principal(user).is_admin and name not in {"inspect_environment", "connect_enterprise", "find_page", "prepare_personal_workbuddy", "list_business_capabilities", "describe_business_operation", "execute_business_operation", "read_business_action", "read_business_result"}:
                    return {"ok": False, "message": "此操作需要公司管理员权限"}
            result = getattr(self, name)(**args)
            # 即使工具返回意外字段，也不允许凭证穿过进程边界。
            return safe_value(result)
        except BizError as exc:
            result = {"ok": False, "code": exc.code, "message": redact(exc.message), "operation_id": args.get("operation_id"), "status": "failed"}
            if "business" in name:
                ui_result = dict(result)
                if isinstance(exc.data, dict) and exc.data.get("authorization_url"):
                    ui_result.update(authorization_url=exc.data["authorization_url"], authorization_label=exc.data["authorization_label"])
                    result["requires_authorization"] = True
                self.business_errors = [item for item in self.business_errors if item.get("operation_id") != ui_result.get("operation_id")]
                self.business_errors.append(ui_result)
            return result
        except (ValueError, TypeError):
            result = {"ok": False, "message": "工具参数或接入文件格式不正确，原有配置已保留"}
        except Exception:
            result = {"ok": False, "message": "执行工具暂未完成，配置未被标记为接通；不能据此推断外部账号或模型出错"}
        if "business" in name:
            result.update(status="failed", operation_id=args.get("operation_id"), message="业务操作未完成，请核对当前记录与参数后继续；尚未核实的写入不会自动重复提交")
            self.business_errors.append(result)
        if name in {"install_workbuddy", "connect_enterprise", "prepare_personal_workbuddy"}:
            result.update(provider="wecom" if args.get("provider") == "wecom" else "lemoncloud" if args.get("provider") == "lemoncloud" else "workbuddy", status="failed")
            self._record(result)
        return result

    def inspect_environment(self):
        with SessionLocal() as db:
            principal = build_principal(db.get(SysUser, self.user_id))
            info = connectors.catalog(db, principal)
        config = settings.assistant_host_home / ".workbuddy" / "mcp.json"
        return {"ok": True, "company_scope": True, "is_admin": principal.is_admin, "host_access": settings.assistant_host_access and principal.is_admin,
                "connectors": [{"provider": c["key"], "state": c["state_label"]} for c in info["connectors"]],
                "cli": [name for name in CLI_NAMES if shutil.which(name)] if principal.is_admin else [],
                "workbuddy_config_exists": config.exists() if settings.assistant_host_access and principal.is_admin else None,
                "instruction": "直接调用对应接入工具完成用户明确要求的接入。检查状态时不修改配置。"}

    def _sources(self, provider):
        if provider not in BASIC:
            raise ValueError()
        fields = {field[0] for field in connectors.FIELDS[provider]}
        sources = [{"id": "current", "label": "当前服务器环境", "values": {
            key: str(getattr(settings, key)) for key in fields if getattr(settings, key)}}]
        saved = settings.storage_dir.parent / "xiaoju" / "credentials" / str(self.user_id) / f"{provider}.json"
        paths = [saved]
        if settings.assistant_host_access:
            paths += [BASE_DIR.parent / name for name in (".env", ".env.local", "integration-credentials.env", f".{provider}.env")]
            paths += [BASE_DIR / "integration-credentials.env", BASE_DIR / f".{provider}.env"]
            paths += [settings.assistant_host_home / ".workbuddy" / "mcp.json"]
        for index, path in enumerate(paths):
            if not path.is_file() or path.stat().st_size > 65536:
                continue
            try:
                values = import_values(provider, path.read_bytes())
                if values:
                    sources.append({"id": f"file-{index}", "label": "已导入的接入文件" if index == 0 else path.name, "values": values})
            except (OSError, ValueError, UnicodeError):
                continue
        # MCP 的 env 字段通常以环境变量键存放；逐项读取，避免把不相关凭证交给模型。
        return sources

    def discover_credentials(self, provider):
        sources = self._sources(provider)
        ready = any(all(s["values"].get(k) for k in BASIC[provider]) for s in sources)
        if not ready:
            best = max(sources, key=lambda source: len(source["values"]))
            missing = [label for key, label, _, _ in connectors.FIELDS[provider] if key in BASIC[provider] and not best["values"].get(key)]
            self._record({"provider": provider, "status": "waiting_input", "ok": False,
                          "message": "已自动检查现有配置，需要完成企业授权或导入已有接入文件", "missing": missing, "login_url": LOGIN_URLS[provider]})
        return {"ok": True, "provider": provider, "sources": [{"id": s["id"], "label": s["label"],
                "fields_found": sorted(s["values"]), "missing": [x for x in BASIC[provider] if not s["values"].get(x)]} for s in sources],
                "ready": ready}

    def connect_enterprise(self, provider, source_id="auto"):
        if provider not in BASIC:
            raise ValueError()
        with SessionLocal() as db:
            principal = build_principal(db.get(SysUser, self.user_id))
            if not principal.is_admin:
                configured = settings.source_mode(provider) == "live" and all(getattr(settings, key) for key in BASIC[provider])
                return self._record({"provider": provider, "ok": configured,
                    "status": "succeeded" if configured else "waiting_admin", "needs_personal_credentials": False,
                    "message": "公司已统一配置此系统，你可按工作台权限使用，无需个人填写企业密钥" if configured else "公司尚未完成此系统的统一授权，需要公司管理员一次性接入；员工无需提供企业密钥"})
        sources = self._sources(provider)
        ready = [s for s in sources if all(s["values"].get(k) for k in BASIC[provider])]
        selected = next((s for s in sources if s["id"] == source_id), None) if source_id != "auto" else next((s for s in ready if s["id"] == "current"), None)
        if selected is None and source_id == "auto" and ready:
            fingerprints = {tuple(s["values"].get(k) for k in BASIC[provider]) for s in ready}
            if len(fingerprints) == 1:
                selected = ready[0]
        if selected is None or not all(selected["values"].get(key) for key in BASIC[provider]):
            best = selected or max(sources, key=lambda s: len(s["values"]))
            missing = [label for key, label, _, _ in connectors.FIELDS[provider] if key in BASIC[provider] and not best["values"].get(key)]
            outcome = {"provider": provider, "status": "waiting_input", "ok": False,
                       "message": "发现多份不同账号的凭证，需要指定使用哪个来源" if selected is None and len(ready) > 1 else "已自动检查现有配置，需要完成企业授权或导入已有接入文件",
                       "missing": missing, "login_url": LOGIN_URLS[provider], "sources": [{"id": s["id"], "label": s["label"]} for s in ready]}
            return self._record(outcome)
        values = dict(selected["values"])
        if provider == "wecom":
            values.setdefault("wecom_callback_token", secrets.token_hex(16))
            values.setdefault("wecom_callback_aes_key", base64.b64encode(secrets.token_bytes(32)).decode().rstrip("="))
        config = connectors.candidate(provider, values)
        result = connectors.probe(provider, config)
        if result["ok"]:
            from app.core.integration_config import save
            save(config, provider, "live" if provider == "wecom" else "mock")
        outcome = {"provider": provider, **result, "status": "partial" if result["ok"] else "failed",
                   "saved": result["ok"], "activated": result["ok"] and provider == "wecom", "login_url": LOGIN_URLS[provider]}
        if result["ok"]:
            outcome["message"] = ("企业微信公司应用已自动验证保存，可以读取实际授权范围内的员工通讯录预览；网页登录、消息回调按需配置" if provider == "wecom" else "柠檬云应用授权已自动验证保存；账套和财务接口尚待联调，保留演示模式")
        with SessionLocal() as db:
            log_op(db, self.user_id, "xiaoju.connect", "integration", provider,
                   after={"ok": result["ok"], "source": selected["id"], "activated": outcome["activated"]})
            db.commit()
        return self._record(outcome)

    def prepare_personal_workbuddy(self):
        from app.services.xiaoju_personal import prepare
        return self._record(prepare(self.user_id))

    def install_workbuddy(self):
        if not settings.assistant_host_access:
            result = {"provider": "workbuddy", "ok": False, "status": "waiting_input", "message": "当前服务未连接你的本机执行环境，无法写入电脑上的 WorkBuddy 配置"}
            return self._record(result)
        root = settings.assistant_host_home / ".workbuddy"
        root.mkdir(parents=True, exist_ok=True)
        target = root / "mcp.json"
        if target.is_symlink():
            raise ValueError("MCP 配置文件不能是符号链接")
        with (root / ".jucheng.lock").open("a") as lock:
            os.chmod(lock.name, 0o600)
            fcntl.flock(lock, fcntl.LOCK_EX)
            before_bytes = target.read_bytes() if target.exists() else None
            config = json.loads(before_bytes) if before_bytes else {}
            if not isinstance(config, dict) or not isinstance(config.get("mcpServers", {}), dict):
                raise ValueError("现有 WorkBuddy MCP 配置格式不正确，保留原文件")
            servers = config.setdefault("mcpServers", {})
            endpoint = settings.public_base_url.rstrip("/") + "/mcp"
            previous = servers.get(settings.mcp_server_name, {})
            if not isinstance(previous, dict):
                raise ValueError("现有服务配置格式不正确，保留原文件")
            with SessionLocal() as db:
                user = db.scalar(select(SysUser).where(SysUser.id == self.user_id).with_for_update(of=SysUser))
                if not user or not build_principal(user).is_admin:
                    raise ValueError("平台管理员权限已失效")
                token = str(previous.get("headers", {}).get("Authorization", ""))[7:] if isinstance(previous, dict) else ""
                reuse = previous.get("url") == endpoint and token and hash_token(token) == user.access_token
                old_hash, old_hint = user.access_token, user.access_token_hint
                if not reuse:
                    token = new_access_token()
                    user.access_token, user.access_token_hint = hash_token(token), token[-6:]
                    db.commit()
                file_written = False
                try:
                    probe = check_mcp(endpoint, token)
                    if not probe["ok"]:
                        raise RuntimeError("MCP 连接验证未通过")
                    entry = {"type": "streamableHttp", "url": endpoint, "headers": {"Authorization": f"Bearer {token}"}}
                    changed = previous != entry
                    backup = None
                    if changed:
                        if before_bytes:
                            backup = root / f"mcp.json.jucheng-{secrets.token_hex(6)}.bak"
                            fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                            with os.fdopen(fd, "wb") as out:
                                out.write(before_bytes)
                        servers[settings.mcp_server_name] = entry
                        private_write(target, config)
                        file_written = True
                    outcome = {"provider": "workbuddy", "ok": True, "status": "succeeded", "installed": True,
                               "message": "已自动写入 WorkBuddy 配置并通过真实 MCP 连接验证" if changed else "WorkBuddy 配置已存在且连接验证通过，无需重复修改",
                               "config_path": str(target), "backup_path": str(backup) if backup else None,
                               "endpoint": endpoint, "tools_count": probe["tools_count"], "changed": changed,
                               "pending": ["配置已准备好；客户端加载状态以 WorkBuddy 显示为准，必要时重新打开 MCP 面板"]}
                    log_op(db, user.id, "xiaoju.workbuddy_install", "integration", "workbuddy",
                           after={"path": str(target), "tools_count": probe["tools_count"], "changed": changed, "token_reused": bool(reuse)})
                    db.commit()
                except Exception:
                    db.rollback()
                    if file_written:
                        if before_bytes is not None:
                            private_bytes(target, before_bytes)
                        else:
                            target.unlink(missing_ok=True)
                    if not reuse:
                        db.refresh(user)
                        if user.access_token == hash_token(token):
                            user.access_token, user.access_token_hint = old_hash, old_hint
                            db.commit()
                    outcome = {"provider": "workbuddy", "ok": False, "status": "failed",
                               "message": "未完成自动接入：请检查 MCP 服务地址、网络或现有配置格式；个人令牌已恢复", "installed": False}
        return self._record(outcome)

    def run_cli(self, program, args):
        # 不猜测 CLI 子命令：一些 Agent 会把未知的 status/auth 解析成提示词并启动另一模型。
        allowed = {("--version",), ("--help",)}
        if program == "wecom-cli":
            allowed.add(("auth", "show", "--status"))
        if program not in CLI_NAMES or not isinstance(args, list) or tuple(args) not in allowed:
            return {"ok": False, "message": "该 CLI 参数不属于接入检查操作；配置写入请使用对应的连接工具"}
        binary = shutil.which(program)
        if not binary:
            return {"ok": False, "message": f"本机未安装 {program} CLI，可继续使用对应的文件或 API 接入工具"}
        if not settings.assistant_host_access:
            return {"ok": False, "message": "本机执行环境未启用"}
        try:
            actual_args = ["--no-extensions", "--no-mcp", "--no-skills", "--no-context-files", *args] if program == "pi" else args
            result = subprocess.run([binary, *actual_args], capture_output=True, text=True, timeout=8, cwd=BASE_DIR.parent)
            return {"ok": result.returncode == 0, "program": program, "exit_code": result.returncode,
                    "output": redact((result.stdout + result.stderr)[:6000])}
        except subprocess.TimeoutExpired:
            return {"ok": False, "message": "CLI 执行超时，已停止本次命令"}

    def find_page(self, query):
        with SessionLocal() as db:
            p = build_principal(db.get(SysUser, self.user_id))
            found = [{"label": title, "path": path} for menu, title, path, aliases in connectors.PAGES
                     if p.has_menu(menu) and (title in query or any(alias in query for alias in aliases))]
        self.links.extend(link for link in found if link not in self.links)
        return {"ok": bool(found), "links": found, "message": "仅返回当前账号可访问的页面"}


def check_mcp(endpoint: str, token: str) -> dict:
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json", "Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-06-18"}
    with httpx.Client(timeout=10, follow_redirects=False) as client:
        first = client.post(endpoint, headers=headers, json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "xiaoju-pi", "version": "1"}}})
        first.raise_for_status()
        if not isinstance(first.json().get("result", {}).get("serverInfo"), dict):
            return {"ok": False, "tools_count": 0}
        listing = client.post(endpoint, headers=headers, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        listing.raise_for_status()
        tools = listing.json().get("result", {}).get("tools")
        return {"ok": isinstance(tools, list), "tools_count": len(tools) if isinstance(tools, list) else 0}

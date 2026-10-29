"""服务端 Pi 会话。会话与工作文件分开保存，权限改变后禁止重用旧上下文。"""
import fcntl
import json
import os
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from app.core.config import settings
from app.core.response import BizError, conflict, forbidden, not_found, bad_request
from app.core.security import password_version
from app.services.xiaoju_tools import private_write, redact


def root():
    return settings.storage_dir.parent / "xiaoju" / "sessions"


def identifier(value):
    if not isinstance(value, str) or len(value) != 32 or any(c not in "0123456789abcdef" for c in value):
        raise not_found("对话")
    return value


def folder(user_id, session_id):
    return root() / str(int(user_id)) / identifier(session_id)


def scope(user_id):
    from app.services.xiaoju_business import principal, actor_signature, source_signature
    from app.services.wecom_cli_service import status, binding
    p = principal(user_id)
    cli = status(user_id)
    return {"actor": actor_signature(user_id), "password": password_version(p.user.password_hash),
            "source": source_signature(), "connection": (binding(user_id) or {}).get("binding_id"), "cli": cli.get("available", False)}


@contextmanager
def locked(user_id, session_id):
    target = folder(user_id, session_id)
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (target / ".lock").open("a") as handle:
        os.chmod(handle.name, 0o600)
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def load(user_id, session_id, check=True):
    try:
        value = json.loads((folder(user_id, session_id) / "meta.json").read_text())
    except (OSError, ValueError):
        raise not_found("对话") from None
    if value["user_id"] != user_id:
        raise forbidden("该对话属于其他账号")
    if check and value["scope"] != scope(user_id):
        raise forbidden("账号权限或连接已变化，请新建对话；旧对话内容不会继续传给模型")
    if value.get("active_task_id"):
        from app.services.xiaoju_service import _load, task_root, TERMINAL
        try:
            if _load(task_root(), value["active_task_id"])["state"] in TERMINAL:
                value["active_task_id"] = None
        except BizError:
            value["active_task_id"] = None
    return value


def save(value):
    value["updated_at"] = time.time()
    private_write(folder(value["user_id"], value["id"]) / "meta.json", value)


def refresh_connection_scope(user_id, session_id):
    with locked(user_id, session_id):
        value = load(user_id, session_id, check=False)
        current = scope(user_id)
        if any(value["scope"][key] != current[key] for key in ("actor", "password")):
            raise forbidden("账号权限已变化，请新建对话")
        value["scope"] = current
        save(value)


def models():
    return list(dict.fromkeys(x.strip() for x in (settings.assistant_models or settings.llm_model or "deepseek-v4.1-flash-1017").split(",") if x.strip()))


def validate_options(model=None, thinking=None):
    model = model or settings.llm_model or models()[0]
    thinking = thinking or settings.assistant_thinking_level
    if model not in models():
        raise bad_request("该模型尚未由管理员启用")
    if thinking not in {"off", "minimal", "low", "medium", "high"}:
        raise bad_request("不支持的思考等级")
    return model, thinking


def create(user_id, title="新对话", model=None, thinking=None):
    model, thinking = validate_options(model, thinking)
    value = {"id": uuid.uuid4().hex, "user_id": user_id, "title": redact(title)[:80], "model": model,
             "thinking": thinking, "scope": scope(user_id), "messages": [], "active_task_id": None,
             "last_task_id": None, "created_at": time.time(), "updated_at": time.time()}
    save(value)
    return value


def view(value, full=False):
    keys = ("id", "title", "model", "thinking", "active_task_id", "last_task_id", "created_at", "updated_at")
    result = {key: value.get(key) for key in keys}
    if full:
        result["messages"] = value.get("messages", [])[-200:]
    return result


def list_for_user(user_id):
    path = root() / str(user_id)
    if not path.exists():
        return []
    current = scope(user_id)
    values = []
    for target in path.glob("*/meta.json"):
        try:
            value = json.loads(target.read_text())
            if value["scope"] == current and not value.get("archived"):
                values.append(value)
        except (OSError, ValueError, KeyError):
            continue
    return [view(value) for value in sorted(values, key=lambda s: s["updated_at"], reverse=True)[:100]]


def reserve(user_id, session_id, task_id, message, model=None, thinking=None):
    with locked(user_id, session_id):
        value = load(user_id, session_id)
        active = value.get("active_task_id")
        if active:
            from app.services.xiaoju_service import read, TERMINAL
            try:
                previous = read(active, user_id)
            except Exception:
                previous = {"state": "failed"}
            if previous["state"] not in TERMINAL:
                raise conflict("当前对话还在执行，请等待完成或先停止")
        value["model"], value["thinking"] = validate_options(model or value["model"], thinking or value["thinking"])
        if not value["messages"]:
            value["title"] = redact(message)[:60]
        value["messages"].append({"role": "user", "text": redact(message), "task_id": task_id})
        value["active_task_id"] = task_id
        value["last_task_id"] = task_id
        save(value)
        return value


def finish(user_id, session_id, task_id, message):
    if not session_id:
        return
    with locked(user_id, session_id):
        value = load(user_id, session_id, check=False)
        if not any(m.get("task_id") == task_id and m["role"] == "assistant" for m in value["messages"]):
            value["messages"].append({"role": "assistant", "text": redact(message), "task_id": task_id})
        if value.get("active_task_id") == task_id:
            value["active_task_id"] = None
        save(value)


def configure(user_id, session_id, model, thinking):
    with locked(user_id, session_id):
        value = load(user_id, session_id)
        if value.get("active_task_id"):
            raise conflict("执行完成后再调整对话设置")
        value["model"], value["thinking"] = validate_options(model, thinking)
        save(value)
        return view(value, True)


def fork(user_id, session_id):
    import subprocess
    from app.core.config import BASE_DIR
    with locked(user_id, session_id):
        source = load(user_id, session_id)
        if source.get("active_task_id"):
            raise conflict("执行完成后再复制对话")
        target = create(user_id, source["title"] + " · 副本", source["model"], source["thinking"])
        if target["scope"] != source["scope"]:
            (folder(user_id, target["id"]) / "meta.json").unlink(missing_ok=True)
            raise forbidden("账号权限或连接已变化，请新建对话")
        source_file = folder(user_id, session_id) / "session.jsonl"
        if source_file.exists():
            result = subprocess.run([settings.assistant_node_bin, str(BASE_DIR / "agent_runtime/run.mjs")],
                                    input=json.dumps({"action": "fork", "sourceFile": str(source_file),
                                                      "sessionDir": str(folder(user_id, target["id"]))}),
                                    text=True, capture_output=True, timeout=30,
                                    env={"PATH": os.environ.get("PATH", ""), "PI_OFFLINE": "1"})
            if result.returncode:
                raise bad_request("未能复制对话，请稍后重试")
        target["messages"] = source["messages"].copy()
        from app.services import xiaoju_workspace as workspace
        for item in workspace.list_files(user_id, session_id):
            content, _ = workspace.read_file(user_id, session_id, item["path"])
            dest = workspace.prepare(user_id, target["id"]) / item["path"]
            from app.services.xiaoju_tools import private_bytes
            private_bytes(dest, content)
            if os.geteuid() == 0:
                os.chown(dest, 10001, 10001)
                parent = dest.parent
                base = workspace.folder(user_id, target["id"])
                while parent != base:
                    os.chown(parent, 10001, 10001)
                    parent = parent.parent
        save(target)
        return view(target, True)

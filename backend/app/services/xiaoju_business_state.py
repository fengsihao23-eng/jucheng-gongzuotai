"""业务附件、执行单与回执。按账号隔离，不允许模型构造服务器文件路径。"""
import fcntl
import hashlib
import json
import mimetypes
import os
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from app.core.config import settings
from app.core.response import bad_request, forbidden, not_found
from app.services.xiaoju_tools import private_bytes, private_write

MAX_FILE = 50 * 1024 * 1024
FILE_TYPES = {".xlsx", ".xls", ".csv", ".tsv", ".docx", ".pptx", ".pdf", ".txt", ".md", ".png", ".jpg", ".jpeg"}


def root():
    return settings.storage_dir.parent / "xiaoju" / "business"


def identifier(value):
    if not isinstance(value, str) or len(value) != 32 or any(c not in "0123456789abcdef" for c in value):
        raise not_found("业务记录")
    return value


def path(kind, record_id):
    if kind not in {"actions", "results", "files", "receipts"}:
        raise ValueError("invalid record kind")
    return root() / kind / f"{identifier(record_id)}.json"


def write(kind, record):
    private_write(path(kind, record["id"]), record)


def read(kind, record_id, user_id):
    try:
        record = json.loads(path(kind, record_id).read_text())
    except (OSError, ValueError):
        raise not_found("业务记录") from None
    if record["user_id"] != user_id:
        raise forbidden("该业务记录属于其他账号")
    return record


@contextmanager
def locked(key):
    folder = root() / "locks"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / (hashlib.sha256(key.encode()).hexdigest() + ".lock")
    with target.open("a") as handle:
        os.chmod(target, 0o600)
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def upload(user_id, filename, content, content_type=None):
    filename = Path(filename.replace("\\", "/")).name.replace("\x00", "").strip()
    if not filename or Path(filename).suffix.lower() not in FILE_TYPES:
        raise bad_request("请上传 Excel、CSV、Word、PDF 等业务文件；接入密钥请使用管理员的凭证导入入口")
    if not content or len(content) > MAX_FILE:
        raise bad_request("业务文件不能为空且不能超过 50MB")
    record_id = uuid.uuid4().hex
    private_bytes(root() / "files" / f"{record_id}.bin", content)
    record = {"id": record_id, "user_id": user_id, "filename": filename, "size": len(content),
              "content_type": content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream",
              "sha256": hashlib.sha256(content).hexdigest(), "created_at": time.time()}
    write("files", record)
    return file_view(record)


def file_view(record):
    return {key: record[key] for key in ("id", "filename", "size", "content_type")}


def attachment(record_id, user_id):
    record = read("files", record_id, user_id)
    target = root() / "files" / f"{identifier(record_id)}.bin"
    if not target.is_file():
        raise not_found("上传文件")
    return record, target


def list_actions(user_id):
    folder = root() / "actions"
    if not folder.exists(): return []
    result = []
    for target in sorted(folder.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:300]:
        record = json.loads(target.read_text())
        if record["user_id"] == user_id and record["state"] == "pending": result.append(record)
        if len(result) >= 20: break
    return result

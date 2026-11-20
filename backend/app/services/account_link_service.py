"""显式核实同一所有者后，将本地 admin 归并到已建档企微账号。仅限受控运维命令。"""
import json
import shutil
import time
from pathlib import Path

from sqlalchemy import func, select, text, update

from app.core.accounts import LINK_KEY
from app.core.audit import log_op
from app.core.config import settings
from app.core.db import Base
from app.core.deps import build_principal
from app.core.response import bad_request, conflict, forbidden
from app.core.security import password_version
from app.models import SysNotify, SysUser, SysUserRole
from app.services import user_service, wecom_cli_service as cli, xiaoju_business, xiaoju_sessions, xiaoju_workspace
from app.services.xiaoju_tools import private_bytes, private_write

# Ownership is moved. Financial registering/confirming actors and immutable audit
# logs stay under the original historical ID, which the verified alias identifies.
OWNERS = {
    "biz_project": ("owner_id", "market_owner_id"), "biz_contract": ("owner_id",),
    "biz_opportunity": ("owner_id",), "biz_project_review": ("author_id",),
    "biz_project_milestone": ("owner_id",), "ops_project": ("owner_id",),
    "clean_job": ("created_by",), "sys_file": ("uploader_id",), "kb_document": ("uploader_id",),
    "prod_tender_doc": ("uploader_id",), "prod_bid_check": ("created_by",),
    "prod_generation": ("created_by",), "rpt_report": ("created_by",),
    "import_batch": ("uploader_id", "handler_id"), "hr_document": ("created_by",),
    "sys_notify": ("user_id",),
}


def _users(db, source_id, target_id, expected_userid):
    users = db.scalars(select(SysUser).where(SysUser.id.in_([source_id, target_id])).with_for_update(of=SysUser)).all()
    source = next((u for u in users if u.id == source_id), None)
    target = next((u for u in users if u.id == target_id), None)
    if not source or not target or source_id == target_id or source.is_deleted or target.is_deleted:
        raise bad_request("来源或目标账号无效")
    marker = (target.preferences or {}).get("company_directory", {})
    if (source.wecom_userid != "admin" or not build_principal(source).is_admin
            or target.wecom_userid != expected_userid or target.status != 1
            or not settings.wecom_corp_id or not isinstance(marker, dict)
            or marker.get("corp_id") != settings.wecom_corp_id):
        raise conflict("须逐项核实本地 admin 与当前企业的目标 UserID，不能按姓名自动合并")
    existing = (source.preferences or {}).get(LINK_KEY)
    if existing:
        if existing.get("target_id") != target.id or existing.get("target_userid") != target.wecom_userid:
            raise conflict("来源账号已经关联其他身份")
        return source, target, True
    if target.preferences and LINK_KEY in target.preferences:
        raise conflict("目标不能是另一个账号别名")
    if (not source.password_hash or cli._demo_password_hash(source.password_hash, settings.demo_password)):
        raise conflict("来源管理员仍使用公开演示密码，不能用于真实企微管理员登录")
    if target.password_hash and target.password_hash != source.password_hash:
        raise conflict("目标已经设置独立密码，停止自动覆盖；须先核对登录凭证")
    if target.access_token and source.access_token and target.access_token != source.access_token:
        raise conflict("两边各有独立 MCP 令牌，须先核对连接，不能覆盖")
    return source, target, False


def _owned_files(source_id):
    root = settings.storage_dir.parent / "xiaoju"
    records = []
    for group in ("tasks", "business"):
        folder = root / group
        for path in folder.rglob("*.json") if folder.exists() else []:
            if path.is_symlink():
                raise conflict("账号文件中包含符号链接，停止迁移")
            try:
                value = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            if isinstance(value, dict) and value.get("user_id") == source_id:
                if group == "tasks" and value.get("state") not in {"succeeded", "failed", "cancelled", "partial", "waiting_input", "awaiting_confirmation"}:
                    raise conflict("来源账号仍有执行中的小聚任务，请完成后再迁移")
                records.append((path, value))
    return records


def preview(db, source_id, target_id, expected_userid):
    source, target, existing = _users(db, source_id, target_id, expected_userid)
    moves = []
    for table_name, names in OWNERS.items():
        table = Base.metadata.tables.get(table_name)
        if table is None:
            continue
        for name in names:
            if name not in table.c:
                continue
            n = db.scalar(select(func.count()).select_from(table).where(table.c[name] == source.id))
            if n:
                moves.append({"table": table_name, "column": name, "rows": n})
    sessions = list((xiaoju_sessions.root() / str(source.id)).glob("*/meta.json"))
    records = _owned_files(source.id)
    from app.services import wecom_import_service as imports
    source_previews = list(imports._folder(source.id).glob("*.json"))
    return {"source_id": source.id, "target_id": target.id, "target_userid": target.wecom_userid,
            "already_linked": existing, "ownership": moves, "sessions": len(sessions), "private_records": len(records),
            "wecom_sources": len(source_previews),
            "source_cli_copied": False, "historical_audit_rewritten": False,
            "target_cli": cli.status(target.id)}


class FileChanges:
    def __init__(self, backup):
        self.backup = Path(backup)
        self.backup.mkdir(parents=True, exist_ok=False, mode=0o700)
        self.changed, self.created = [], []

    def write(self, path, value):
        path = Path(path)
        saved = self.backup / "files" / path.as_posix().lstrip("/")
        if path.exists():
            private_bytes(saved, path.read_bytes())
            self.changed.append((path, saved))
        else:
            self.created.append(path)
        private_write(path, value)

    def copy_tree(self, source, target):
        if not Path(source).exists():
            return
        if Path(target).exists():
            raise conflict("目标已有同 ID 会话或工作目录，停止自动覆盖")
        if any(p.is_symlink() for p in Path(source).rglob("*")):
            raise conflict("工作目录含符号链接，请先核对")
        Path(target).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copytree(source, target)
        self.created.append(Path(target))

    def copy_file(self, source, target):
        if Path(target).exists():
            raise conflict("目标已有同 ID 企微来源文件，停止自动覆盖")
        private_bytes(target, Path(source).read_bytes())
        self.created.append(Path(target))

    def rollback(self):
        for path, backup in reversed(self.changed):
            private_bytes(path, backup.read_bytes())
        for path in reversed(self.created):
            if path.is_dir(): shutil.rmtree(path)
            else: path.unlink(missing_ok=True)


def _scope(db, target):
    p = build_principal(target)
    actor = xiaoju_business.principal_signature(db, p)
    binding = cli.binding(target.id)
    return {"actor": actor, "password": password_version(target.password_hash),
            "source": xiaoju_business.source_signature(), "connection": binding.get("binding_id") if binding else None,
            "cli": bool(binding and cli.launcher().is_file())}


def apply(db, source_id, target_id, expected_userid, backup, operator_id):
    db.execute(text("select pg_advisory_xact_lock(621107260912)"))
    source, target, existing = _users(db, source_id, target_id, expected_userid)
    if existing:
        return {"already_linked": True, "source_id": source.id, "target_id": target.id}, None
    operator = db.get(SysUser, operator_id)
    if not operator or not build_principal(operator).is_admin:
        raise forbidden()
    plan = preview(db, source_id, target_id, expected_userid)
    files = FileChanges(backup)
    try:
        role = user_service.role_by_code(db, "ADMIN-01")
        if not db.get(SysUserRole, (target.id, role.id)):
            db.add(SysUserRole(user_id=target.id, role_id=role.id))
        target.password_hash = source.password_hash
        old_token, old_hint = source.access_token, source.access_token_hint
        if old_token and not target.access_token:
            source.access_token = source.access_token_hint = None
            db.flush()
            target.access_token, target.access_token_hint = old_token, old_hint
        db.flush()
        db.refresh(target, ["roles"])
        old_message_ids = list(db.scalars(select(SysNotify.id).where(SysNotify.user_id == source.id,
            SysNotify.channel == "wecom_app", SysNotify.send_status.in_([0, 2]))))
        for move in plan["ownership"]:
            table = Base.metadata.tables[move["table"]]
            column = table.c[move["column"]]
            db.execute(update(table).where(column == source.id).values({move["column"]: target.id}))
        # No historical message is sent as a side effect of ownership transfer.
        db.execute(update(SysNotify).where(SysNotify.id.in_(old_message_ids)).values(send_status=2, retry_count=3,
            error_msg="账号关联后的历史消息仅保留记录，不自动补发"))
        scope = _scope(db, target)
        migrated = {"source_id": source.id, "at": time.time()}
        for path, record in _owned_files(source.id):
            value = {**record, "user_id": target.id, "account_migration": migrated}
            if "actor_signature" in value:
                value["actor_signature"] = scope["actor"]
            if path.parent.name == "actions" and value.get("state") in {"pending", "executing"}:
                value.update(state="cancelled", message="账号已关联，请重新准备并确认业务操作")
            if path.parent.name == "tasks" and value.get("state") in {"waiting_input", "awaiting_confirmation"}:
                value.update(state="cancelled", message="账号已关联，历史任务保留，请新建对话继续")
            files.write(path, value)
        for original in (xiaoju_sessions.root() / str(source.id)).glob("*/meta.json"):
            record = json.loads(original.read_text())
            destination = xiaoju_sessions.folder(target.id, record["id"])
            files.copy_tree(original.parent, destination)
            value = {**record, "user_id": target.id, "scope": scope, "active_task_id": None, "account_migration": migrated}
            private_write(destination / "meta.json", value)
            files.copy_tree(xiaoju_workspace.folder(source.id, record["id"]), xiaoju_workspace.folder(target.id, record["id"]))
        from app.services import wecom_import_service as imports
        new_binding = cli.binding(target.id)
        for original in imports._folder(source.id).glob("*.json"):
            record = json.loads(original.read_text())
            if record.get("user_id") != source.id:
                continue
            destination = imports._path(target.id, record["id"])
            if destination.exists():
                raise conflict("目标已有同 ID 企微来源记录，停止自动覆盖")
            value = {**record, "user_id": target.id, "account_migration": migrated}
            if new_binding:
                value.update(binding_id=new_binding["binding_id"], scope=imports._scope(build_principal(target)))
            if value.get("state") != "imported":
                value["expires_at"] = 0
            files.write(destination, value)
            for suffix in (".payload", ".raw"):
                data_file = original.with_suffix(suffix)
                if data_file.exists():
                    files.copy_file(data_file, destination.with_suffix(suffix))
        # Re-encrypt a saved personal WorkBuddy token with the target account AAD.
        from app.services import xiaoju_personal
        stored, token = xiaoju_personal._read(source.id)
        if stored and token and not xiaoju_personal._path(target.id).exists():
            cipher = xiaoju_personal._cipher(target.id)
            encrypted, tag = cipher.encrypt_and_digest(token.encode())
            files.write(xiaoju_personal._path(target.id), {**stored, "nonce": cipher.nonce.hex(),
                "ciphertext": encrypted.hex(), "tag": tag.hex()})
        source.preferences = {**(source.preferences or {}), LINK_KEY: {"target_id": target.id,
            "target_userid": target.wecom_userid, "corp_id": settings.wecom_corp_id, "verified_at": time.time()}}
        source.status = 2  # Archived identity; the admin login alias resolves before the status check.
        db.flush()
        log_op(db, operator_id, "account.link", "user", target.id,
            before={"source_id": source.id, "target_id": target.id},
            after={**plan, "login_alias": source.wecom_userid, "target_roles": [r.role_code for r in target.roles]})
        private_write(files.backup / "manifest.json", plan)
        return {**plan, "already_linked": False, "login_alias": source.wecom_userid}, files
    except BaseException:
        files.rollback()
        raise

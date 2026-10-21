"""管理员配置写入持久化服务器文件；不将凭证放入数据库、审计或对话。"""
import fcntl
import json
import os
import tempfile
import threading

from app.core.config import INTEGRATION_KEYS, load_integration_settings, settings

_lock = threading.RLock()
_revision = None


def refresh() -> None:
    global _revision
    path = settings.storage_dir.parent / "integrations.json"
    with _lock:
        revision = (str(path), path.stat().st_mtime_ns) if path.exists() else None
        if revision is not None and revision != _revision:
            before = (settings.wecom_corp_id, settings.wecom_secret, settings.lemoncloud_app_id, settings.lemoncloud_app_secret)
            load_integration_settings(settings)
            after = (settings.wecom_corp_id, settings.wecom_secret, settings.lemoncloud_app_id, settings.lemoncloud_app_secret)
            if before[:2] != after[:2]:
                from app.integration.wecom.live import LiveWeCom
                with LiveWeCom._lock:
                    LiveWeCom._token, LiveWeCom._expire_at = None, 0
            if before[2:] != after[2:]:
                from app.integration.lemoncloud.live import LiveLemonCloud
                with LiveLemonCloud._lock:
                    LiveLemonCloud._token, LiveLemonCloud._exp = None, 0
            _revision = revision


def save(values: dict, source: str, mode: str) -> None:
    if not values.keys() <= INTEGRATION_KEYS or source not in {"wecom", "lemoncloud"} or mode not in {"mock", "live"}:
        raise ValueError("unsupported integration setting")
    root = settings.storage_dir.parent
    root.mkdir(parents=True, exist_ok=True)
    path = root / "integrations.json"
    with _lock, (root / ".integrations.lock").open("a") as guard:
        os.chmod(guard.name, 0o600)
        fcntl.flock(guard, fcntl.LOCK_EX)
        saved = json.loads(path.read_text()) if path.exists() else {}
        overrides = dict(pair.strip().split(":", 1) for pair in
                         saved.get("data_source_overrides", settings.data_source_overrides).split(",") if ":" in pair)
        overrides[source] = mode
        saved.update(values)
        saved["data_source_overrides"] = ",".join(f"{k}:{v}" for k, v in overrides.items())
        # 先校验，再以 0600 临时文件原子替换，避免中断时留下半份凭证。
        from app.core.config import Settings
        Settings.model_validate({**settings.model_dump(), **saved})
        fd, temporary = tempfile.mkstemp(prefix=".integrations-", dir=root)
        try:
            with os.fdopen(fd, "w") as output:
                json.dump(saved, output, ensure_ascii=False)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        refresh()

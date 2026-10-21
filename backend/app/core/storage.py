"""文件存储服务：本地目录实现（接口与 COS 一致，生产换 COS 只替换 _Backend）。

- key 规范：{category}/{yyyy-MM}/{filename}，报表为 report/{type}/{yyyy-MM}/
- 同 key 再次写入版本号 +1（版本控制）
- 下载链接带 HMAC 签名与过期时间（默认 1h），见 /api/v1/files/{id}/download
"""
import hashlib
import mimetypes
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import sign_file
from app.models import SysFile


def _root() -> Path:
    root = Path(settings.storage_dir)
    root.mkdir(parents=True, exist_ok=True)
    return root


def save_bytes(db: Session, *, data: bytes, filename: str, category: str, key: str | None = None,
               uploader_id: int | None = None, content_type: str | None = None) -> SysFile:
    from app.core.timeutil import today
    key = key or f"{category}/{today():%Y-%m}/{filename}"
    version = (db.scalar(select(func.max(SysFile.version)).where(SysFile.storage_key == key)) or 0) + 1
    path = _root() / key / f"v{version}" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    f = SysFile(storage_key=key, version=version, filename=filename, size=len(data),
                content_type=content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream",
                sha256=hashlib.sha256(data).hexdigest(), category=category, uploader_id=uploader_id)
    db.add(f)
    db.flush()
    return f


def file_path(f: SysFile) -> Path:
    return _root() / f.storage_key / f"v{f.version}" / f.filename


def read_bytes(f: SysFile) -> bytes:
    return file_path(f).read_bytes()


def signed_url(f: SysFile | int, ttl: int | None = None, absolute: bool = False) -> str:
    fid = f if isinstance(f, int) else f.id
    exp, sig = sign_file(fid, ttl)
    rel = f"/api/v1/files/{fid}/download?exp={exp}&sig={sig}"
    return settings.public_base_url.rstrip("/") + rel if absolute else rel


def file_brief(f: SysFile | None, absolute: bool = False) -> dict | None:
    if not f:
        return None
    return {"id": f.id, "filename": f.filename, "size": f.size, "version": f.version, "sha256": f.sha256,
            "category": f.category, "created_at": f.created_at, "url": signed_url(f, absolute=absolute)}

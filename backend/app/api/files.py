"""文件服务：签名临时链接下载（1h 有效）+ 登录用户按 id 取新链接。下载全量审计。"""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.audit import log_op
from app.core.db import get_db
from app.core.deps import Principal, client_ip, get_principal
from app.core.response import BizError, not_found, ok
from app.core.security import verify_file_sig
from app.core.storage import file_brief, file_path
from app.models import SysFile
from app.services.intake_access import PROTECTED_FILE_CATEGORIES, require_file_access

router = APIRouter(prefix="/files", tags=["文件"])


@router.get("/{file_id}")
def file_info(file_id: int, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    f = db.get(SysFile, file_id)
    if not f:
        raise not_found("文件")
    require_file_access(db, p, f)
    return ok(file_brief(f))


@router.get("/{file_id}/download")
def download(file_id: int, exp: int, sig: str, request: Request, db: Session = Depends(get_db)):
    if not verify_file_sig(file_id, exp, sig):
        raise BizError(40301, "下载链接无效或已过期，请重新获取")
    f = db.get(SysFile, file_id)
    if not f or not file_path(f).exists():
        raise not_found("文件")
    actor_id = None
    if f.category in PROTECTED_FILE_CATEGORIES:
        p = get_principal(request, db)
        require_file_access(db, p, f)
        actor_id = p.id
    log_op(db, actor_id, "file.download", f.category, f.id, after={"filename": f.filename, "version": f.version},
           ip=client_ip(request))
    db.commit()
    return FileResponse(file_path(f), media_type=f.content_type, filename=f.filename)

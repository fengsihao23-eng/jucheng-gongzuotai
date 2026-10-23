"""企业微信个人连接与个人数据同步接口。

与公司级通讯录分开：这里的每个接口都只作用于当前登录账号，
个人数据按账号隔离，管理员也无法通过接口读取他人内容。
"""
from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.audit import log_op
from app.core.db import get_db
from app.core.deps import Principal, client_ip, get_principal
from app.core.response import BizError, ok

router = APIRouter(prefix="/wecom-personal", tags=["企业微信个人数据"])


@router.post("/bind")
def bind_start(request: Request, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """为当前账号起一次官方 CLI 扫码会话，返回二维码有效期与状态查询标识。"""
    from app.services import wecom_cli_bind as bind
    from app.services.wecom_cli_service import binding
    if binding(p.id):
        raise BizError(40901, "当前账号已绑定企业微信个人连接；如需更换，请先解除绑定")
    result = bind.start(p.id)
    log_op(db, p.id, "wecom_cli.bind_start", "integration", "wecom-cli", after={"session": result["session_id"]},
           ip=client_ip(request))
    db.commit()
    return ok(result)


@router.get("/bind/{session_id}")
def bind_status(session_id: str, p: Principal = Depends(get_principal)):
    from app.services import wecom_cli_bind as bind
    return ok(bind.status(p.id, session_id))


@router.get("/bind/{session_id}/qr")
def bind_qr(session_id: str, p: Principal = Depends(get_principal)):
    from fastapi.responses import FileResponse
    from app.services import wecom_cli_bind as bind
    path = bind.qr_path(p.id, session_id)
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-store"})


@router.delete("/bind/{session_id}")
def bind_cancel(session_id: str, p: Principal = Depends(get_principal)):
    from app.services import wecom_cli_bind as bind
    return ok(bind.cancel(p.id, session_id))


@router.delete("/binding")
def unbind(request: Request, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """解除本人绑定，只清理该账号自己的凭证目录。"""
    from app.services import wecom_cli_bind as bind
    result = bind.unbind(p.id)
    log_op(db, p.id, "wecom_cli.unbind", "integration", "wecom-cli", ip=client_ip(request))
    db.commit()
    return ok(result)


@router.get("/sync")
def sync_status(p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    from app.services import wecom_sync_service
    return ok(wecom_sync_service.status(db, p.id))


@router.post("/sync/run")
def sync_run(request: Request, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """立即拉取一次本人数据；绑定后由前端触发，之后由每 10 分钟的任务增量刷新。"""
    from app.services import wecom_sync_service
    result = wecom_sync_service.sync_user(db, p.id)
    log_op(db, p.id, "wecom.sync", "integration", "wecom-cli",
           after={key: value for key, value in result.items() if isinstance(value, dict)}, ip=client_ip(request))
    db.commit()
    return ok(result)


@router.get("/sync/items")
def sync_items(service: str = "", limit: int = 50, p: Principal = Depends(get_principal),
               db: Session = Depends(get_db)):
    """只返回当前账号自己的数据；管理员也不能通过此接口读取他人内容。"""
    from app.services import wecom_sync_service
    return ok({"items": wecom_sync_service.items(db, p.id, service, limit),
               "status": wecom_sync_service.status(db, p.id)})

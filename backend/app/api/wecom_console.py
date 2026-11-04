"""企业微信接口中心：官方接口目录、凭证配置、在线调用与审计。

仅平台管理员可见。所有调用都走统一网关（凭证选择 → token 缓存 → 重试），
写操作需要显式确认，调用记录落库但不保存凭证与报文原文。
"""
from fastapi import APIRouter, Body, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import Principal, client_ip, require_menu
from app.core.response import BizError, ok, page_data
from app.integration.wecom import catalog
from app.integration.wecom.credentials import CREDENTIALS, Gateway, WeComApiError, save_secret
from app.services import wecom_api_service

router = APIRouter(prefix="/wecom-console", tags=["企业微信接口"])
Admin = Depends(require_menu("admin"))


class CallBody(BaseModel):
    id: str = Field(min_length=1, max_length=16)
    params: dict = Field(default_factory=dict)
    payload: dict | None = None
    credential: str = ""
    confirm: bool = False


@router.get("/overview")
def overview(p: Principal = Admin, db: Session = Depends(get_db)):
    recent, _ = wecom_api_service.recent(db, 1, 5)
    return ok({"stats": catalog.stats(), "credentials": wecom_api_service.credentials_status(),
               "recent": recent, "can_call": bool(Gateway.configured("app") or Gateway.configured("contacts"))})


@router.get("/catalog")
def get_catalog(keyword: str = "", section: str = "", p: Principal = Admin):
    tree = catalog.tree()
    if section:
        tree = [node for node in tree if node["section"] == section]
    if keyword:
        needle = keyword.strip().lower()
        filtered = []
        for node in tree:
            groups = []
            for group in node["groups"]:
                items = [row for row in group["items"]
                         if needle in row["name"].lower() or needle in row["path"].lower()]
                if items:
                    groups.append({"name": group["name"], "items": items})
            if groups:
                filtered.append({"section": node["section"], "count": sum(len(g["items"]) for g in groups),
                                 "groups": groups})
        tree = filtered
    return ok({"tree": tree, "stats": catalog.stats()})


@router.get("/catalog/{doc_path}")
def get_item(doc_path: str, p: Principal = Admin):
    item = catalog.get(doc_path)
    if item is None:
        raise BizError(40404, "接口不存在")
    return ok({**item, "configured": Gateway.configured(item["credential"])})


@router.get("/credentials")
def credentials(p: Principal = Admin):
    return ok(wecom_api_service.credentials_status())


@router.put("/credentials/{name}")
def put_credential(name: str, secret: str = Body(embed=True), request: Request = None,
                   p: Principal = Admin, db: Session = Depends(get_db)):
    if name not in CREDENTIALS:
        raise BizError(40004, "未知的凭证类型")
    from app.core.audit import log_op
    save_secret(name, secret)
    log_op(db, p.id, "wecom.credential", "wecom", name, None, {"configured": bool(secret)}, client_ip(request))
    db.commit()
    return ok({"name": name, "configured": bool(secret)})


@router.post("/credentials/{name}/test")
def test_credential(name: str, p: Principal = Admin):
    if name not in CREDENTIALS:
        raise BizError(40004, "未知的凭证类型")
    try:
        result = Gateway.probe(name)
    except WeComApiError as error:
        return ok({"ok": False, "errcode": error.errcode, "errmsg": error.errmsg})
    return ok(result)


@router.post("/call")
def call(body: CallBody, p: Principal = Admin, db: Session = Depends(get_db)):
    result = wecom_api_service.invoke(db, p.id, body.id, params=body.params, payload=body.payload,
                                      credential=body.credential, confirm=body.confirm)
    return ok(result)


@router.get("/calls")
def calls(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), only_failed: bool = False,
          p: Principal = Admin, db: Session = Depends(get_db)):
    rows, total = wecom_api_service.recent(db, page, page_size, only_failed)
    return ok(page_data(rows, total, page, page_size))

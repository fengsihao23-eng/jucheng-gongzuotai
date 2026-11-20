"""Granted users can read operation records; UI reports always use the current identity."""
from typing import Literal
from urllib.parse import quote
from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import get_principal, Principal
from app.core.response import forbidden, ok
from app.core.timeutil import today
from app.services import operation_records as service

router=APIRouter(prefix="/operation-records",tags=["操作记录"])
HEADERS={"Cache-Control":"no-store"}


def records_viewer(p:Principal=Depends(get_principal)):
    if not (p.is_admin or p.has_menu("operation_records")):
        raise forbidden("没有操作记录查看权限，请联系管理员授权")
    return p


def filters(actor:str=Query("",max_length=100),keyword:str=Query("",max_length=100),
            module:str=Query("",max_length=64),source:str=Query("",max_length=24),
            outcome:str=Query("",max_length=24),start:str="",end:str=""):
    return dict(actor=actor,keyword=keyword,module=module,source=source,outcome=outcome,start=start,end=end)


class UiEvent(BaseModel):
    model_config=ConfigDict(str_strip_whitespace=True,extra="forbid")
    kind:Literal["visit","click"]
    path:str=Field(max_length=300)
    title:str=Field(min_length=1,max_length=64)
    control:str=Field(default="",max_length=100)

    @field_validator("path")
    @classmethod
    def path_only(cls,value):
        if not value.startswith("/") or value.startswith("//") or any(c in value for c in "?#\r\n"):
            raise ValueError("仅记录站内页面路径")
        return value


@router.post("/ui")
def ui_event(body:UiEvent,request:Request,p:Principal=Depends(get_principal)):
    request.state.audit_ui=body.model_dump()
    return ok(None,headers=HEADERS)


@router.get("/options")
def options(p=Depends(records_viewer),db:Session=Depends(get_db)):
    return ok(service.options(db,p),headers=HEADERS)


@router.get("/export")
def export(f:dict=Depends(filters),p=Depends(records_viewer),db:Session=Depends(get_db)):
    payload=service.export_csv(db,p=p,**f)
    return Response(payload,media_type="text/csv; charset=utf-8",
                    headers={**HEADERS,"Content-Disposition":f"attachment; filename*=UTF-8''{quote(f'操作记录_{today():%Y%m%d}.csv')}"})


@router.get("")
def listing(f:dict=Depends(filters),page:int=Query(1,ge=1),size:int=Query(30,ge=1,le=100),
            p=Depends(records_viewer),db:Session=Depends(get_db)):
    return ok(service.listing(db,page=page,size=size,p=p,**f),headers=HEADERS)


@router.get("/{key}")
def detail(key:str,p=Depends(records_viewer),db:Session=Depends(get_db)):
    return ok(service.detail(db,key,p),headers=HEADERS)

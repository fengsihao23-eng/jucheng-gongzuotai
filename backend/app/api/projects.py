"""项目流程（项目部 P1 · PRD 7.6）：项目台账、标准控制流程节点引擎、变更留痕、周进度导出。"""
from datetime import date
from urllib.parse import quote
from typing import Literal

from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import log_op
from app.core.db import get_db
from app.core.deps import Principal, client_ip, require_menu
from app.core.response import bad_request, conflict, forbidden, not_found, ok
from app.core.storage import file_brief, save_bytes
from app.models import PROJECT_STATUS, BizOpportunity, BizProject, BizProjectMilestone, SysUser
from app.services import project_service as ps

router = APIRouter(prefix="/projects", tags=["projects"])
Menu = Depends(require_menu("projects"))
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _xlsx(data: bytes, filename: str) -> Response:
    return Response(data, media_type=XLSX,
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"})


@router.get("")
def list_projects(status: int | None = None, business_line: str | None = None, is_key: bool | None = None,
                  owner_id: int | None = None, keyword: str | None = None, mine: bool = False,
                  scope: Literal["all", "pipeline", "general", "history"] = "all",
                  p: Principal = Menu, db: Session = Depends(get_db)):
    items = ps.list_projects(db, p, status=status, business_line=business_line, is_key=is_key, owner_id=owner_id,
                             keyword=keyword, mine=mine, scope=scope)
    return ok({"items": items, "total": len(items), "data_scope": p.data_scope,
               "can_create": p.is_admin or p.can("milestone:write") or p.can("project:key")})


@router.get("/options")
def options(p: Principal = Menu, db: Session = Depends(get_db)):
    users = db.scalars(select(SysUser).where(SysUser.status == 1, SysUser.is_deleted.is_(False))
                       .order_by(SysUser.department_id, SysUser.id)).all()
    tpls = ps.templates(db)
    return ok({"users": [{"id": u.id, "name": u.name, "dept_code": u.dept_code, "position": u.position} for u in users],
               "templates": [{"key": k, "nodes": v} for k, v in tpls.items()]})


class ProjectIn(BaseModel):
    name: str
    business_line: str
    customer_name: str
    owner_id: int | None = None
    market_owner_id: int | None = None
    status: int = 2
    is_key: bool = False
    budget_amount: float | None = None
    start_date: date | None = None
    plan_end_date: date | None = None
    milestone_template: str | None = None
    bid_deadline: date | None = None
    opportunity_id: int | None = None
    remark: str | None = None


@router.post("")
def create_project(body: ProjectIn, request: Request, p: Principal = Menu, db: Session = Depends(get_db)):
    if not (p.is_admin or p.can("milestone:write") or p.can("project:key")):
        raise forbidden("无新建项目权限")
    data = body.model_dump()
    if body.status not in PROJECT_STATUS:
        raise bad_request("项目状态无效")
    source_id = data.pop("opportunity_id")
    source = None
    if source_id:
        from app.services import opportunity_service as opp
        if not p.has_menu("opportunities"):
            raise forbidden("无商机访问权限")
        source = db.scalar(opp._filtered(p).where(BizOpportunity.id == source_id).with_for_update(of=BizOpportunity))
        if not source:
            raise not_found("商机")
        if source.project_code:
            raise conflict("该商机已关联项目，请从商机详情打开原项目")
        if body.business_line != source.business_line or body.customer_name.strip() != source.customer_name.strip():
            raise bad_request("新项目的客户和业务线必须与关联商机一致")
    if data["is_key"] and not p.can("project:key"):
        data["is_key"] = False
    if not data.get("owner_id") and p.dept_code == "XM":
        data["owner_id"] = p.id
    proj = ps.create_project(db, data, p.id)
    if source:
        source.project_code = proj.project_code
        log_op(db, p.id, "project.link_opportunity", "biz_project", proj.project_code,
               after={"opportunity_id": source.id}, ip=client_ip(request))
    log_op(db, p.id, "project.create", "project", proj.project_code, after={"name": proj.name}, ip=client_ip(request))
    db.commit()
    return ok({"project_code": proj.project_code, "id": proj.id})


@router.get("/weekly/export")
def export_weekly(codes: str | None = None, week: str | None = None, request: Request = None, p: Principal = Menu,
                  db: Session = Depends(get_db)):
    """导出项目周进度 xlsx：指定项目（逗号分隔编码）或「我负责的项目」（data_scope=all 时为全部在建项目）。"""
    q = ps.visible_query(p)
    if codes:
        q = q.where(BizProject.project_code.in_([c.strip() for c in codes.split(",") if c.strip()]))
    else:
        q = q.where(BizProject.status.in_([2, 3, 4]))
        if p.data_scope != "all":
            q = q.where(BizProject.owner_id == p.id)
    projects = db.scalars(q.order_by(BizProject.project_code)).unique().all()
    if not projects:
        raise not_found("可导出的项目")
    content, fname, _ = ps.export_weekly_xlsx(db, projects, week)
    log_op(db, p.id, "report.download", "project_weekly", fname, ip=client_ip(request))
    db.commit()
    return _xlsx(content, fname)


@router.post("/weekly/generate")
def generate_weekly(codes: str | None = Body(None, embed=True), week: str | None = Body(None, embed=True),
                    p: Principal = Menu, db: Session = Depends(get_db)):
    """生成周进度并归档（返回签名链接与汇总文本，供前端/MCP）。"""
    q = ps.visible_query(p).where(BizProject.status.in_([2, 3, 4]))
    if codes:
        q = q.where(BizProject.project_code.in_([c.strip() for c in codes.split(",")]))
    elif p.data_scope != "all":
        q = q.where(BizProject.owner_id == p.id)
    projects = db.scalars(q.order_by(BizProject.project_code)).unique().all()
    if not projects:
        raise not_found("可导出的项目")
    content, fname, data = ps.export_weekly_xlsx(db, projects, week)
    f = save_bytes(db, data=content, filename=fname, category="report",
                   key=f"report/project_weekly/{data['end']:%Y-%m}/{fname}", uploader_id=p.id)
    db.commit()
    return ok({"file": file_brief(f), "text": ps.weekly_text(data), "week": data["week"]})


@router.get("/{code}")
def detail(code: str, p: Principal = Menu, db: Session = Depends(get_db)):
    proj = ps.get_project(db, code, p)
    return ok(ps.project_detail(db, proj, p))


class ProjectPatch(BaseModel):
    name: str | None = None
    customer_name: str | None = None
    owner_id: int | None = None
    market_owner_id: int | None = None
    status: int | None = None
    budget_amount: float | None = None
    start_date: date | None = None
    plan_end_date: date | None = None
    remark: str | None = None


@router.put("/{code}")
def update(code: str, body: ProjectPatch, request: Request, p: Principal = Menu, db: Session = Depends(get_db)):
    proj = ps.get_project(db, code, p)
    if not (ps.can_edit(p, proj) or p.can("project:key")):
        raise forbidden("仅项目负责人或具备节点维护权限者可修改项目")
    before, after = ps.update_project(db, proj, body.model_dump(exclude_unset=True), p)
    log_op(db, p.id, "project.update", "project", proj.project_code, before, after, client_ip(request))
    db.commit()
    return ok(ps.project_brief(db, proj))


@router.put("/{code}/key")
def set_key(code: str, is_key: bool = Body(embed=True), request: Request = None, p: Principal = Menu,
            db: Session = Depends(get_db)):
    if not p.can("project:key"):
        raise forbidden("无重点项目标记权限")
    proj = ps.get_project(db, code, p)
    log_op(db, p.id, "project.key", "project", proj.project_code, {"is_key": proj.is_key}, {"is_key": is_key},
           client_ip(request))
    proj.is_key = is_key
    db.commit()
    return ok({"is_key": proj.is_key})


# ---------------------------------------------------------------- 节点
class MilestoneIn(BaseModel):
    node_name: str | None = None
    owner_id: int | None = None
    duty: str | None = None
    plan_date: date | None = None
    finish_date: date | None = None
    status: int | str | None = None
    progress_note: str | None = None
    seq: int | None = None


def _editable(db, code, p) -> BizProject:
    proj = ps.get_project(db, code, p)
    ps.ensure_edit(db, p, proj)
    return proj


def _ms(db, proj, mid) -> BizProjectMilestone:
    m = db.get(BizProjectMilestone, mid)
    if not m or m.project_id != proj.id:
        raise not_found("节点")
    return m


@router.get("/{code}/milestones")
def milestones(code: str, p: Principal = Menu, db: Session = Depends(get_db)):
    proj = ps.get_project(db, code, p)
    return ok([ps.ms_dict(m) for m in sorted(proj.milestones, key=lambda x: x.seq)])


@router.post("/{code}/milestones")
def add_ms(code: str, body: MilestoneIn, p: Principal = Menu, db: Session = Depends(get_db)):
    proj = _editable(db, code, p)
    m = ps.add_milestone(db, proj, body.model_dump(), p.id)
    db.commit()
    return ok(ps.ms_dict(m))


@router.put("/{code}/milestones/{mid}")
def edit_ms(code: str, mid: int, body: MilestoneIn, p: Principal = Menu, db: Session = Depends(get_db)):
    proj = _editable(db, code, p)
    m = ps.edit_milestone(db, proj, _ms(db, proj, mid), body.model_dump(exclude_unset=True), p.id)
    db.commit()
    return ok(ps.ms_dict(m))


@router.delete("/{code}/milestones/{mid}")
def del_ms(code: str, mid: int, p: Principal = Menu, db: Session = Depends(get_db)):
    proj = _editable(db, code, p)
    ps.delete_milestone(db, proj, _ms(db, proj, mid), p.id)
    db.commit()
    return ok()


@router.put("/{code}/milestones-order")
def reorder(code: str, ids: list[int] = Body(embed=True), p: Principal = Menu, db: Session = Depends(get_db)):
    proj = _editable(db, code, p)
    ps.reorder_milestones(db, proj, ids, p.id)
    db.commit()
    return ok([ps.ms_dict(m) for m in sorted(proj.milestones, key=lambda x: x.seq)])


@router.post("/{code}/milestones/{mid}/move")
def move(code: str, mid: int, direction: str = Body(embed=True), p: Principal = Menu, db: Session = Depends(get_db)):
    if direction not in ("up", "down"):
        raise bad_request("direction 仅支持 up/down")
    proj = _editable(db, code, p)
    ps.move_milestone(db, proj, _ms(db, proj, mid), direction, p.id)
    db.commit()
    return ok([ps.ms_dict(m) for m in sorted(proj.milestones, key=lambda x: x.seq)])


@router.get("/{code}/logs")
def logs(code: str, p: Principal = Menu, db: Session = Depends(get_db)):
    proj = ps.get_project(db, code, p)
    return ok(ps.milestone_logs(db, proj, limit=500))


@router.get("/{code}/weekly")
def project_weekly(code: str, week: str | None = None, p: Principal = Menu, db: Session = Depends(get_db)):
    proj = ps.get_project(db, code, p)
    content, fname, _ = ps.export_weekly_xlsx(db, [proj], week)
    return _xlsx(content, fname)


class MilestoneAction(BaseModel):
    node_name: str | None = None
    action: str = "query"
    status: int | str | None = None
    finish_date: date | None = None
    note: str | None = None


@router.post("/{code}/milestone-action")
def milestone_action(code: str, body: MilestoneAction, p: Principal = Menu, db: Session = Depends(get_db)):
    """与 MCP proj.milestone_update 同一入口（便于联调）。"""
    res = ps.update_milestone(db, code, body.node_name, body.action, body.status, body.finish_date, body.note,
                              operator=p, source="web")
    db.commit()
    return ok(res)

"""市场部（P0 · PRD 7.3）：重点项目跟进看板、进展反馈、市场周报/月报自动生成、竞品情报、项目复盘经验库。

菜单：market（跟进看板/报告）、intel（竞品情报）、reviews（项目复盘）分别控制。
"""
from collections import Counter
from datetime import date, timedelta
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Body, Depends, Request
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.audit import log_op, snapshot
from app.core.db import get_db, get_or_404
from app.core.deletion import DeletionIn, verify_deletion
from app.core.deps import Principal, client_ip, get_principal, require_menu
from app.core.response import bad_request, forbidden, not_found, ok, page_data
from app.core.storage import file_brief, read_bytes, save_bytes
from app.core.timeutil import today
from app.models import BizProject, BizProjectReview, MktIntel, RptReport, SysFile, SysUser
from app.services import market_service as ms
from app.services import project_service as ps
from app.services import key_projects as keys
from app.services import notify_service
from app.schemas.key_projects import KeyProjectCreateIn, KeyProjectEditIn, KeyProjectTeamIn, KeyTaskIn, KeyTaskFeedbackIn

router = APIRouter(prefix="/market", tags=["market"])
Market = Depends(require_menu("market"))
Intel = Depends(require_menu("intel"))
Reviews = Depends(require_menu("reviews"))
MarketOrProject = Depends(require_menu("market", "projects"))
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _docx(data: bytes, filename: str) -> Response:
    return Response(data, media_type=DOCX,
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"})


# ================================================================ 跟进看板
@router.get("/board")
def board(include_all: bool = False, include_paused: bool = False, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    keys.require_overview(db, p)
    d = ms.key_board(db, p, include_all, include_paused)
    d["events"] = ms.key_events(db, p, days=60)[:30] if p.has_menu("market") else []
    return ok(d)


@router.get("/key-options")
def key_project_options(project_code: str | None = None, include_team: bool = False,
                        p: Principal = MarketOrProject, db: Session = Depends(get_db)):
    return ok(keys.options(db, p, project_code, include_team))


@router.post("/projects")
def create_key_project(body: KeyProjectCreateIn, background: BackgroundTasks, p: Principal = Market, db: Session = Depends(get_db)):
    row, notices = keys.create_project(db, p, body)
    db.commit()
    if notices:
        background.add_task(notify_service.deliver_committed, notices)
    return ok({"project_code": row.project_code, "id": row.id})


@router.get("/projects/{project_code}")
def key_project_detail(project_code: str, period: str = "", p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    return ok(keys.detail(db, p, project_code, period))


@router.put("/projects/{project_code}")
def edit_key_project(project_code: str, body: KeyProjectEditIn, p: Principal = MarketOrProject, db: Session = Depends(get_db)):
    row = keys.save_project(db, p, body, project_code)
    db.commit()
    return ok({"project_code": row.project_code})


@router.delete("/projects/{project_code}")
def delete_key_project(project_code: str, body: DeletionIn, p: Principal = MarketOrProject, db: Session = Depends(get_db)):
    verify_deletion(db, body)
    result = keys.delete_project(db, p, project_code)
    db.commit()
    return ok(result)


@router.post("/projects/{project_code}/team")
def add_key_project_team(project_code: str, body: KeyProjectTeamIn, background: BackgroundTasks,
                         p: Principal = MarketOrProject, db: Session = Depends(get_db)):
    row = keys.project(db, p, project_code, lock=True)
    added, notices = keys.add_team(db, p, row, body.user_ids)
    db.commit()
    if notices:
        background.add_task(notify_service.deliver_committed, notices)
    return ok({"added": added})


@router.delete("/projects/{project_code}/team/{user_id}")
def remove_key_project_member(project_code: str, user_id: int, body: DeletionIn,
                              p: Principal = MarketOrProject, db: Session = Depends(get_db)):
    verify_deletion(db, body)
    row = keys.project(db, p, project_code, lock=True)
    keys.remove_team_member(db, p, row, user_id)
    db.commit()
    return ok()


@router.post("/projects/{project_code}/tasks")
def create_key_task(project_code: str, body: KeyTaskIn, background: BackgroundTasks, p: Principal = MarketOrProject, db: Session = Depends(get_db)):
    notices = []
    result = keys.save_task(db, p, project_code, body, notice_ids=notices)
    db.commit()
    if notices:
        background.add_task(notify_service.deliver_committed, notices)
    return ok(result)


@router.get("/projects/{project_code}/tasks/{task_id}")
def key_task_detail(project_code: str, task_id: int, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    row = keys.project(db, p, project_code)
    return ok(keys.task_data(db, p, row, keys.task(db, row, task_id), history=True))


@router.put("/projects/{project_code}/tasks/{task_id}")
def edit_key_task(project_code: str, task_id: int, body: KeyTaskIn, background: BackgroundTasks, p: Principal = MarketOrProject, db: Session = Depends(get_db)):
    notices = []
    result = keys.save_task(db, p, project_code, body, task_id, notice_ids=notices)
    db.commit()
    if notices:
        background.add_task(notify_service.deliver_committed, notices)
    return ok(result)


@router.post("/projects/{project_code}/tasks/{task_id}/feedback")
def key_task_feedback(project_code: str, task_id: int, body: KeyTaskFeedbackIn, background: BackgroundTasks,
                      p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    notices = []
    result = keys.feedback(db, p, project_code, task_id, body, notice_ids=notices)
    db.commit()
    if notices:
        background.add_task(notify_service.deliver_committed, notices)
    return ok(result)


class PauseIn(BaseModel):
    paused: bool


@router.put("/projects/{project_code}/pause")
def pause_follow(project_code: str, body: PauseIn, p: Principal = MarketOrProject, db: Session = Depends(get_db)):
    result = keys.set_paused(db, p, project_code, body.paused)
    db.commit()
    return ok(result)


@router.get("/events")
def events(days: int = 60, kind: str | None = None, project_code: str | None = None, p: Principal = MarketOrProject,
           db: Session = Depends(get_db)):
    kinds = [kind] if kind else ["event", "risk"]
    pid = ps.get_project(db, project_code, p).id if project_code else None
    return ok(ms.key_events(db, p, days=days, kinds=kinds, project_id=pid))


class FeedbackIn(BaseModel):
    project_code: str
    content: str
    kind: str = "progress"
    risk_level: int | None = None
    feedback_date: date | None = None
    problems: str = ""
    next_step: str = ""
    next_due_on: date | None = None


@router.post("/feedback")
def add_feedback(body: FeedbackIn, background: BackgroundTasks, p: Principal = MarketOrProject, db: Session = Depends(get_db)):
    """进展反馈：feedback:write 按钮；项目经理/工程师只能写自己可见（负责）的项目。"""
    if not p.can("feedback:write"):
        raise forbidden("无进展反馈权限")
    proj = ps.get_project(db, body.project_code, p)
    notices = []
    f = ms.add_feedback(db, proj, body.model_dump(), p.id, notice_ids=notices)
    db.commit()
    if notices:
        background.add_task(notify_service.deliver_committed, notices)
    db.refresh(f)
    return ok(ps.feedback_dict(f))


@router.get("/feedback")
def list_feedback(project_code: str, p: Principal = MarketOrProject, db: Session = Depends(get_db)):
    proj = ps.get_project(db, project_code, p)
    return ok(ps.project_detail(db, proj, p)["feedbacks"])


# ================================================================ 市场周报 / 月报
class ReportIn(BaseModel):
    period_type: str = "weekly"
    period: str | None = None


def _report_out(db: Session, r: RptReport, with_text: bool = False) -> dict:
    files = [file_brief(db.get(SysFile, fid)) for fid in (r.file_ids or [])]
    m = dict(r.metrics or {})
    text = m.pop("text", None)
    m.pop("projects", None)
    d = {"id": r.id, "report_type": r.report_type, "period": r.period, "title": r.title, "version": r.version,
         "metrics": m, "files": [f for f in files if f], "trigger": r.trigger, "created_at": r.created_at,
         "created_by": r.created_by}
    if with_text:
        d["text"] = text
        d["projects"] = (r.metrics or {}).get("projects", [])
    return d


@router.post("/reports/generate")
def generate_report(body: ReportIn, request: Request, p: Principal = Market, db: Session = Depends(get_db)):
    res = ms.build_market_report(db, body.period_type, body.period, user_scope=p, trigger="manual", created_by=p.id)
    log_op(db, p.id, "report.generate", res["report"].report_type, res["report"].period, ip=client_ip(request))
    db.commit()
    out = _report_out(db, res["report"])
    out.update(text=res["text"], data=res["data"])
    return ok(out)


@router.get("/reports")
def list_reports(report_type: str | None = None, page: int = 1, page_size: int = 20, p: Principal = Market,
                 db: Session = Depends(get_db)):
    types = [report_type] if report_type else ["market_weekly", "market_monthly", "intel_brief"]
    q = select(RptReport).where(RptReport.report_type.in_(types))
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    rows = db.scalars(q.order_by(RptReport.created_at.desc(), RptReport.id.desc())
                      .offset((page - 1) * page_size).limit(page_size)).all()
    return ok(page_data([_report_out(db, r) for r in rows], total, page, page_size))


@router.get("/reports/summary")
def summary(period_type: str = "weekly", period: str | None = None, p: Principal = Market,
            db: Session = Depends(get_db)):
    """「汇总本周重点项目周报」：返回汇总稿文本（默认本周，不落库）。"""
    if period_type == "weekly":
        from app.core.timeutil import week_no
        res = ms.build_market_report(db, "weekly", period or week_no(today()), p, save=False)
    else:
        res = ms.build_market_report(db, "monthly", period or f"{today():%Y-%m}", p, save=False)
    return ok({"text": res["text"], "data": res["data"]})


@router.get("/reports/{rid}")
def report_detail(rid: int, p: Principal = Market, db: Session = Depends(get_db)):
    r = get_or_404(db, RptReport, rid, "报告")
    if not r.report_type.startswith(("market_", "intel_")):
        raise not_found("报告")
    return ok(_report_out(db, r, with_text=True))


@router.get("/reports/{rid}/download")
def report_download(rid: int, request: Request, p: Principal = Market, db: Session = Depends(get_db)):
    r = get_or_404(db, RptReport, rid, "报告")
    if not r.report_type.startswith(("market_", "intel_")) or not r.file_ids:
        raise not_found("报告文件")
    f = db.get(SysFile, r.file_ids[0])
    log_op(db, p.id, "report.download", r.report_type, r.id, after={"filename": f.filename}, ip=client_ip(request))
    db.commit()
    return _docx(read_bytes(f), f.filename)


# ================================================================ 竞品情报
INTEL_FIELDS = ["title", "kind", "competitor", "summary", "source_url", "source_name", "tags", "importance", "published_on"]


class IntelIn(BaseModel):
    title: str
    kind: str = "competitor"
    competitor: str | None = None
    summary: str
    source_url: str | None = None
    source_name: str | None = None
    tags: list[str] = []
    importance: int = 1
    published_on: date | None = None


def _intel_dict(i: MktIntel, names: dict) -> dict:
    return {"id": i.id, **{k: getattr(i, k) for k in INTEL_FIELDS}, "tags": i.tags or [],
            "kind_text": ms.KIND_TEXT.get(i.kind), "importance_text": ms.IMPORTANCE.get(i.importance),
            "author_id": i.author_id, "author_name": names.get(i.author_id), "created_at": i.created_at,
            "updated_at": i.updated_at}


def _validate_intel(body: IntelIn):
    if body.kind not in ms.KIND_TEXT:
        raise bad_request("情报类型无效（competitor/industry/policy/bid）")
    if body.importance not in ms.IMPORTANCE:
        raise bad_request("重要度无效（1/2/3）")
    if not body.title.strip() or not body.summary.strip():
        raise bad_request("标题与摘要必填")


@router.get("/intel")
def list_intel(keyword: str | None = None, kind: str | None = None, competitor: str | None = None,
               tag: str | None = None, importance: int | None = None, start: date | None = None,
               end: date | None = None, page: int = 1, page_size: int = 50, p: Principal = Intel,
               db: Session = Depends(get_db)):
    q = select(MktIntel)
    if keyword:
        kw = f"%{keyword}%"
        q = q.where(or_(MktIntel.title.ilike(kw), MktIntel.summary.ilike(kw), MktIntel.competitor.ilike(kw),
                        MktIntel.source_name.ilike(kw)))
    if kind:
        q = q.where(MktIntel.kind == kind)
    if competitor:
        q = q.where(MktIntel.competitor == competitor)
    if importance:
        q = q.where(MktIntel.importance >= importance)
    if start:
        q = q.where(MktIntel.published_on >= start)
    if end:
        q = q.where(MktIntel.published_on <= end)
    rows = db.scalars(q.order_by(MktIntel.published_on.desc().nulls_last(), MktIntel.id.desc())).all()
    if tag:
        rows = [r for r in rows if tag in (r.tags or [])]
    allrows = db.scalars(select(MktIntel)).all()
    facets = {"competitors": sorted({r.competitor for r in allrows if r.competitor}),
              "tags": [t for t, _ in Counter(t for r in allrows for t in (r.tags or [])).most_common(40)],
              "kinds": ms.KIND_TEXT}
    names = dict(db.execute(select(SysUser.id, SysUser.name)).all())
    total = len(rows)
    page_rows = rows[(page - 1) * page_size: page * page_size]
    return ok(page_data([_intel_dict(r, names) for r in page_rows], total, page, page_size, facets=facets))


@router.post("/intel")
def create_intel(body: IntelIn, request: Request, p: Principal = Intel, db: Session = Depends(get_db)):
    if not p.can("intel:write"):
        raise forbidden("无情报维护权限")
    _validate_intel(body)
    i = MktIntel(**body.model_dump(), author_id=p.id)
    db.add(i)
    db.flush()
    log_op(db, p.id, "intel.create", "intel", i.id, after={"title": i.title}, ip=client_ip(request))
    db.commit()
    return ok(_intel_dict(i, {p.id: p.user.name}))


@router.get("/intel/{iid}")
def get_intel(iid: int, p: Principal = Intel, db: Session = Depends(get_db)):
    i = get_or_404(db, MktIntel, iid, "情报")
    return ok(_intel_dict(i, dict(db.execute(select(SysUser.id, SysUser.name)).all())))


@router.put("/intel/{iid}")
def update_intel(iid: int, body: IntelIn, request: Request, p: Principal = Intel, db: Session = Depends(get_db)):
    if not p.can("intel:write"):
        raise forbidden("无情报维护权限")
    _validate_intel(body)
    i = get_or_404(db, MktIntel, iid, "情报")
    before = snapshot(i, INTEL_FIELDS)
    for k, v in body.model_dump().items():
        setattr(i, k, v)
    log_op(db, p.id, "intel.update", "intel", i.id, before, snapshot(i, INTEL_FIELDS), client_ip(request))
    db.commit()
    return ok(_intel_dict(i, dict(db.execute(select(SysUser.id, SysUser.name)).all())))


@router.delete("/intel/{iid}")
def delete_intel(iid: int, body: DeletionIn, request: Request, p: Principal = Intel, db: Session = Depends(get_db)):
    if not p.can_delete("intel:write"):
        raise forbidden("无权删除竞品情报")
    verify_deletion(db, body)
    i = get_or_404(db, MktIntel, iid, "情报")
    log_op(db, p.id, "intel.delete", "intel", i.id, snapshot(i, INTEL_FIELDS), None, client_ip(request))
    db.delete(i)
    db.commit()
    return ok()


class BriefIn(BaseModel):
    start: date | None = None
    end: date | None = None
    competitors: list[str] | None = None
    to_kb: bool = False


@router.post("/intel/brief")
def intel_brief(body: BriefIn, p: Principal = Intel, db: Session = Depends(get_db)):
    """整理竞品简报：按竞品分组（竞品动态 / 对我方影响 / 应对建议），生成 docx，可选沉淀知识库。"""
    end = body.end or today()
    start = body.start or end - timedelta(days=30)
    if start > end:
        raise bad_request("开始日期不能晚于结束日期")
    res = ms.intel_brief(db, start, end, body.competitors, author_id=p.id, to_kb=body.to_kb)
    db.commit()
    res["file"] = file_brief(res["file"]) if res["file"] else None
    res.pop("report", None)
    return ok(res)


# ================================================================ 项目复盘
class ReviewIn(BaseModel):
    title: str | None = None
    goal: str | None = None
    result: str | None = None
    lessons: str | None = None
    tags: list[str] | None = None
    status: int | None = None


@router.get("/reviews")
def list_reviews(keyword: str | None = None, tag: str | None = None, status: int | None = None,
                 business_line: str | None = None, p: Principal = Reviews, db: Session = Depends(get_db)):
    q = select(BizProjectReview).join(BizProject, BizProject.id == BizProjectReview.project_id)
    if keyword:
        kw = f"%{keyword}%"
        q = q.where(or_(BizProjectReview.title.ilike(kw), BizProjectReview.lessons.ilike(kw), BizProject.name.ilike(kw)))
    if status:
        q = q.where(BizProjectReview.status == status)
    if business_line:
        q = q.where(BizProject.business_line == business_line)
    rows = db.scalars(q.order_by(BizProjectReview.updated_at.desc())).all()
    if tag:
        rows = [r for r in rows if tag in (r.tags or [])]
    alltags = Counter(t for r in db.scalars(select(BizProjectReview)).all() for t in (r.tags or []))
    candidates = db.scalars(select(BizProject).where(BizProject.status.in_([3, 5]), BizProject.is_deleted.is_(False))
                            .order_by(BizProject.project_code)).all()
    reviewed = set(db.scalars(select(BizProjectReview.project_id)).all())
    return ok({"items": [ms.review_dict(db, r) for r in rows], "total": len(rows),
               "tags": [{"tag": t, "count": n} for t, n in alltags.most_common()],
               "candidates": [{"project_code": c.project_code, "name": c.name, "status": c.status}
                              for c in candidates if c.id not in reviewed],
               "can_write": p.can("review:write")})


@router.post("/reviews/generate")
def generate_review(project_code: str = Body(embed=True), force: bool = Body(False, embed=True),
                    p: Principal = Reviews, db: Session = Depends(get_db)):
    if not p.can("review:write"):
        raise forbidden("无复盘编辑权限")
    proj = ps.get_project(db, project_code)
    r = ms.generate_review(db, proj, author_id=p.id, force=force)
    db.commit()
    return ok(ms.review_dict(db, r))


@router.get("/reviews/{rid}")
def get_review(rid: int, p: Principal = Reviews, db: Session = Depends(get_db)):
    return ok(ms.review_dict(db, get_or_404(db, BizProjectReview, rid, "复盘")))


@router.put("/reviews/{rid}")
def update_review(rid: int, body: ReviewIn, request: Request, p: Principal = Reviews, db: Session = Depends(get_db)):
    if not p.can("review:write"):
        raise forbidden("无复盘编辑权限")
    r = get_or_404(db, BizProjectReview, rid, "复盘")
    fields = ["title", "goal", "result", "lessons", "tags", "status"]
    before = snapshot(r, fields)
    for k, v in body.model_dump(exclude_unset=True).items():
        if v is not None:
            if k == "status" and v not in (1, 2):
                raise bad_request("状态无效（1 初稿 2 已定稿）")
            setattr(r, k, v)
    r.author_id = r.author_id or p.id
    log_op(db, p.id, "review.update", "review", r.id, before, snapshot(r, fields), client_ip(request))
    db.commit()
    return ok(ms.review_dict(db, r))


@router.get("/reviews/{rid}/export")
def export_review(rid: int, request: Request, p: Principal = Reviews, db: Session = Depends(get_db)):
    r = get_or_404(db, BizProjectReview, rid, "复盘")
    data = ms.review_docx(db, r)
    fname = f"{r.title}.docx".replace("/", "_")
    f = save_bytes(db, data=data, filename=fname, category="report",
                   key=f"report/project_review/{today():%Y-%m}/{fname}", uploader_id=p.id)
    r.file_id = f.id
    log_op(db, p.id, "report.download", "review", r.id, after={"filename": fname}, ip=client_ip(request))
    db.commit()
    return _docx(data, fname)

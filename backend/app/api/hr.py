"""人事中心（PRD 7.5）：考勤核对、人力分析/PPT、到期预警、文书生成+风险提示、招聘、员工自助答疑。

菜单 hr；写操作按钮 hr:write，考勤导入/规则 hr:attendance。/hr/qa 对全体登录员工开放。
"""
from fastapi import APIRouter, Body, Depends, File, Form, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import log_op
from app.core.db import get_db, get_or_404
from app.core.deps import Principal, client_ip, get_principal, require_button, require_menu
from app.core.response import BizError, ok
from app.core.storage import file_brief
from app.core.timeutil import prev_month, today
from app.models import HrAttendanceImport, HrCandidate, HrContractAlert, HrDocument, HrEmployee, SysFile
from app.services import attendance_service as att
from app.services import dict_service, hr_doc_service, hr_service, recruit_service

router = APIRouter(prefix="/hr", tags=["人事中心"])
HR = Depends(require_menu("hr"))


def _period(period: str | None) -> str:
    return period or prev_month(f"{today():%Y-%m}")


def _need(p: Principal, button: str) -> None:
    if not p.can(button):
        raise BizError(40301, "无此操作权限")


# ================= 概览 / 花名册 =================
@router.get("/overview")
def overview(p: Principal = HR, db: Session = Depends(get_db)):
    return ok(hr_service.overview(db))


@router.get("/employees")
def employees(keyword: str = "", p: Principal = HR, db: Session = Depends(get_db)):
    rows = []
    for e in db.scalars(select(HrEmployee).order_by(HrEmployee.employee_no)).all():
        u = e.user
        if not u or u.is_deleted or (keyword and keyword not in u.name and keyword not in u.wecom_userid):
            continue
        rows.append({"user_id": u.id, "userid": u.wecom_userid, "name": u.name, "employee_no": e.employee_no,
                     "dept_code": u.dept_code, "dept_name": u.department.name if u.department else "",
                     "position": u.position, "gender": e.gender, "education": e.education, "employ_type": e.employ_type,
                     "hire_date": e.hire_date, "probation_end_date": e.probation_end_date,
                     "contract_start_date": e.contract_start_date, "contract_end_date": e.contract_end_date,
                     "leave_date": e.leave_date, "status": u.status, "annual_leave_days": e.annual_leave_days})
    return ok(rows)


# ================= 考勤 =================
@router.get("/attendance/rule")
def get_rule(p: Principal = HR, db: Session = Depends(get_db)):
    rule = att.get_rule(db)
    return ok({"rule": rule, "labels": att.RULE_LABELS, "text": att.rule_text(rule), "template": att.get_template(db)})


@router.put("/attendance/rule")
def put_rule(body: dict = Body(...), request: Request = None, p: Principal = Depends(require_button("hr:attendance")),
             db: Session = Depends(get_db)):
    before = att.get_rule(db)
    rule = att.put_rule(db, body.get("rule", body))
    log_op(db, p.id, "hr.attendance_rule", "sys_dict", "attendance_rule.default", before, rule, client_ip(request))
    db.commit()
    return ok({"rule": rule, "text": att.rule_text(rule)})


@router.put("/attendance/template")
def put_template(body: dict = Body(...), p: Principal = Depends(require_button("hr:attendance")),
                 db: Session = Depends(get_db)):
    before = att.get_template(db)
    cols = body.get("columns") or {}
    if not isinstance(cols, dict) or any(not isinstance(v, list) for v in cols.values()):
        raise BizError(40001, "columns 应为 {字段: [列名...]}")
    tpl = {**before, **body, "columns": {**before["columns"], **cols}}
    dict_service.put(db, "attendance_template", "default", tpl, "企微考勤导出列映射")
    log_op(db, p.id, "hr.attendance_template", "sys_dict", "attendance_template.default", before, tpl)
    db.commit()
    return ok(tpl)


@router.post("/attendance/import")
async def import_attendance(file: UploadFile = File(...), period: str | None = Form(None), request: Request = None,
                            p: Principal = Depends(require_button("hr:attendance")), db: Session = Depends(get_db)):
    content = await file.read()
    if not content:
        raise BizError(40001, "文件为空")
    imp = att.import_excel(db, content, file.filename or "attendance.xlsx", p.id, period or None)
    log_op(db, p.id, "hr.attendance_import", "hr_attendance_import", imp.id,
           after={"period": imp.period, "filename": imp.filename, "rows": imp.total_rows}, ip=client_ip(request))
    db.commit()
    return ok(att.attendance_summary(db, imp.period))


@router.post("/attendance/pull-wecom")
def pull_wecom_attendance(period: str = Body(embed=True), request: Request = None,
                          p: Principal = Depends(require_button("hr:attendance")),
                          db: Session = Depends(get_db)):
    """从企业微信打卡拉取本期原始记录，替代手工导出 Excel。"""
    from app.services import wecom_checkin_service
    result = wecom_checkin_service.pull(db, period, p.id)
    log_op(db, p.id, "hr.attendance_pull_wecom", "hr_attendance_import", result["import_id"],
           after={"period": period, "records": result["records"], "exception_rows": result["exception_rows"]},
           ip=client_ip(request))
    db.commit()
    return ok({**att.attendance_summary(db, period), "pull": result})


@router.post("/attendance/recheck")
def recheck(period: str = Body(embed=True), p: Principal = Depends(require_button("hr:attendance")),
            db: Session = Depends(get_db)):
    r = att.recheck(db, period)
    log_op(db, p.id, "hr.attendance_recheck", "hr_attendance", period, after={"exception_rows": r["exception_rows"]})
    db.commit()
    return ok(r)


@router.get("/attendance/periods")
def periods(p: Principal = HR, db: Session = Depends(get_db)):
    rows = db.scalars(select(HrAttendanceImport).order_by(HrAttendanceImport.id.desc())).all()
    seen, out = set(), []
    for r in rows:
        if r.period in seen:
            continue
        seen.add(r.period)
        f = db.get(SysFile, r.file_id) if r.file_id else None
        out.append({"period": r.period, "import_id": r.id, "filename": r.filename, "total_rows": r.total_rows,
                    "exception_rows": r.exception_rows, "created_at": r.created_at, "source": file_brief(f)})
    return ok(out)


@router.get("/attendance/summary")
def summary(period: str | None = None, p: Principal = HR, db: Session = Depends(get_db)):
    return ok(att.attendance_summary(db, _period(period)))


@router.get("/attendance/exceptions")
def exceptions(period: str | None = None, type: str = "", include_info: bool = False, p: Principal = HR,
               db: Session = Depends(get_db)):
    types = [t for t in type.split(",") if t]
    return ok(att.exception_rows(db, _period(period), types=types or None, include_info=include_info))


@router.get("/attendance/crosscheck")
def crosscheck(period: str | None = None, p: Principal = HR, db: Session = Depends(get_db)):
    period = _period(period)
    imp = att.latest_import(db, period)
    stored = [{"name": x.get("name"), "account": x.get("account"), "row": r}
              for x in (imp.errors if imp else []) if x.get("kind") == "not_in_roster"
              for r in (x.get("rows") or [x.get("row")])]
    live = att.crosscheck(db, period, unknown=stored)
    return ok({"period": period, "items": live, "labels": att.CROSS_LABELS,
               "row_errors": [x for x in (imp.errors if imp else []) if x.get("kind") == "row_error"]})


@router.get("/attendance/export")
def export(period: str | None = None, request: Request = None, p: Principal = HR, db: Session = Depends(get_db)):
    period = _period(period)
    if not att.latest_import(db, period):
        raise BizError(40401, f"{period} 尚未导入考勤")
    f = att.export_summary(db, period, p.id)
    log_op(db, p.id, "report.download", "attendance", period, after={"file_id": f.id}, ip=client_ip(request))
    db.commit()
    return ok(file_brief(f))


# ================= 人力分析 =================
@router.get("/analysis")
def analysis(period: str | None = None, p: Principal = HR, db: Session = Depends(get_db)):
    return ok(hr_service.analysis(db, period or f"{today():%Y-%m}"))


@router.post("/analysis/ppt")
def analysis_ppt(period: str | None = Body(None, embed=True), request: Request = None,
                 p: Principal = Depends(require_button("hr:write")), db: Session = Depends(get_db)):
    period = _period(period)
    f = hr_service.build_ppt(db, period, p.id)
    log_op(db, p.id, "hr.analysis_ppt", "report", f.id, after={"period": period}, ip=client_ip(request))
    db.commit()
    return ok({"period": period, "file": file_brief(f)})


# ================= 到期预警 =================
@router.get("/alerts")
def alerts(status: str = "pending", p: Principal = HR, db: Session = Depends(get_db)):
    return ok({"rule": hr_service.alert_rule(db), "items": hr_service.alert_rows(db, status)})


@router.post("/alerts/scan")
def alerts_scan(notify: bool = Body(True, embed=True), p: Principal = Depends(require_button("hr:write")),
                db: Session = Depends(get_db)):
    r = hr_service.scan_alerts(db, notify=notify)
    db.commit()
    return ok(r)


@router.put("/alerts/rule")
def alerts_rule(body: dict = Body(...), request: Request = None, p: Principal = Depends(require_button("hr:write")),
                db: Session = Depends(get_db)):
    before = hr_service.alert_rule(db)
    rule = dict(before)
    for k in ("contract_lead_days", "probation_lead_days"):
        if k in body:
            v = int(body[k])
            if not 1 <= v <= 365:
                raise BizError(40001, "提前量须在 1~365 天之间")
            rule[k] = v
    if isinstance(body.get("leader_keywords"), list):
        rule["leader_keywords"] = [str(x) for x in body["leader_keywords"] if str(x).strip()]
    dict_service.put(db, "alert_rule", "hr", rule)
    log_op(db, p.id, "hr.alert_rule", "sys_dict", "alert_rule.hr", before, rule, client_ip(request))
    db.commit()
    return ok(rule)


@router.put("/alerts/{alert_id}")
def alert_handle(alert_id: int, handled: bool = Body(True, embed=True), note: str | None = Body(None, embed=True),
                 p: Principal = Depends(require_button("hr:write")), db: Session = Depends(get_db)):
    a = get_or_404(db, HrContractAlert, alert_id, "预警")
    before = {"handled": a.handled, "handle_note": a.handle_note}
    a.handled, a.handle_note = handled, (note or a.handle_note)
    log_op(db, p.id, "hr.alert_handle", "hr_contract_alert", a.id, before, {"handled": handled, "handle_note": a.handle_note})
    db.commit()
    return ok({"id": a.id, "handled": a.handled, "handle_note": a.handle_note})


# ================= 文书 =================
class DocIn(BaseModel):
    doc_type: str
    user_id: int | None = None
    params: dict = Field(default_factory=dict)


@router.get("/docs/types")
def doc_types(p: Principal = HR):
    return ok({"types": hr_doc_service.catalog(), "onboarding_pack": hr_doc_service.ONBOARD_PACK,
               "disclaimer": hr_doc_service.DISCLAIMER})


@router.post("/docs/risk-check")
def risk_check(body: DocIn, p: Principal = HR, db: Session = Depends(get_db)):
    prm = hr_doc_service.resolve_params(db, body.doc_type, body.params, body.user_id)
    return ok({"risks": hr_doc_service.check_risks(body.doc_type, prm), "disclaimer": hr_doc_service.DISCLAIMER})


@router.post("/docs/generate")
def doc_generate(body: DocIn, p: Principal = Depends(require_button("hr:write")), db: Session = Depends(get_db)):
    r = hr_doc_service.generate(db, body.doc_type, body.params, body.user_id, p.id)
    log_op(db, p.id, "hr.doc_generate", "hr_document", r["document"]["id"], after={"doc_type": body.doc_type})
    db.commit()
    return ok(r)


@router.post("/docs/onboarding-pack")
def doc_pack(body: dict = Body(...), p: Principal = Depends(require_button("hr:write")), db: Session = Depends(get_db)):
    r = hr_doc_service.onboarding_pack(db, body.get("params") or {}, body.get("user_id"), p.id)
    log_op(db, p.id, "hr.onboarding_pack", "hr_document", r["package"]["id"])
    db.commit()
    return ok(r)


@router.post("/docs/minutes")
def doc_minutes(body: dict = Body(...), p: Principal = Depends(require_button("hr:write")), db: Session = Depends(get_db)):
    if not (body.get("raw_text") or "").strip():
        raise BizError(40001, "请粘贴会议纪要原文")
    r = hr_doc_service.generate(db, "minutes", body, None, p.id)
    db.commit()
    return ok(r)


@router.get("/docs")
def docs(doc_type: str = "", p: Principal = HR, db: Session = Depends(get_db)):
    q = select(HrDocument).order_by(HrDocument.id.desc()).limit(200)
    if doc_type:
        q = q.where(HrDocument.doc_type == doc_type)
    return ok([hr_doc_service.doc_row(db, d) for d in db.scalars(q).all()])


# ================= 招聘 =================
@router.post("/recruit/jd")
def recruit_jd(body: dict = Body(...), p: Principal = Depends(require_button("hr:write")), db: Session = Depends(get_db)):
    if not body.get("position"):
        raise BizError(40001, "请填写岗位名称")
    r = recruit_service.generate_jd(body)
    if body.get("save", True):
        g = hr_doc_service.generate(db, "jd", body, None, p.id)
        r["document"] = g["document"]
        db.commit()
    return ok(r)


@router.post("/recruit/screen")
async def recruit_screen(files: list[UploadFile] = File(...), position: str = Form(...), keywords: str = Form(""),
                         min_education: str = Form("本科"), min_years: int = Form(0), pass_score: int = Form(60),
                         bonus: str = Form(""), p: Principal = Depends(require_button("hr:write")),
                         db: Session = Depends(get_db)):
    split = lambda s: [x.strip() for x in s.replace("，", ",").replace("、", ",").split(",") if x.strip()]  # noqa: E731
    payload = [(f.filename or "resume.txt", await f.read()) for f in files]
    r = recruit_service.screen(db, payload, {"position": position, "keywords": split(keywords),
                                             "min_education": min_education, "min_years": min_years,
                                             "pass_score": pass_score, "bonus": split(bonus)})
    db.commit()
    return ok(r)


@router.get("/recruit/candidates")
def candidates(position: str = "", stage: str = "", p: Principal = HR, db: Session = Depends(get_db)):
    q = select(HrCandidate).order_by(HrCandidate.screen_score.desc().nulls_last(), HrCandidate.id)
    if position:
        q = q.where(HrCandidate.position == position)
    if stage:
        q = q.where(HrCandidate.stage == stage)
    return ok({"items": [recruit_service.cand_row(c) for c in db.scalars(q).all()], "stages": recruit_service.STAGES,
               "eval_dims": [{"key": k, "label": lb, "weight": w} for k, lb, w in recruit_service.EVAL_DIMS]})


@router.put("/recruit/candidates/{cid}/stage")
def cand_stage(cid: int, stage: str = Body(embed=True), p: Principal = Depends(require_button("hr:write")),
               db: Session = Depends(get_db)):
    c = get_or_404(db, HrCandidate, cid, "候选人")
    before = c.stage
    recruit_service.move_stage(c, stage)
    log_op(db, p.id, "hr.candidate_stage", "hr_candidate", c.id, {"stage": before}, {"stage": stage})
    db.commit()
    return ok(recruit_service.cand_row(c))


@router.post("/recruit/candidates/{cid}/interview")
def cand_interview(cid: int, body: dict = Body(...), p: Principal = Depends(require_button("hr:write")),
                   db: Session = Depends(get_db)):
    c = get_or_404(db, HrCandidate, cid, "候选人")
    ev = recruit_service.evaluate(body.get("scores") or {}, body.get("interviewer") or p.user.name, body.get("comment"))
    ev["date"] = today().isoformat()
    c.interview_eval = ev
    if c.stage in ("resume", "screen_pass"):
        c.stage = "interview"
    db.commit()
    return ok(recruit_service.cand_row(c))


@router.post("/recruit/candidates/{cid}/offer")
def cand_offer(cid: int, body: dict = Body(...), p: Principal = Depends(require_button("hr:write")),
               db: Session = Depends(get_db)):
    c = get_or_404(db, HrCandidate, cid, "候选人")
    if c.stage not in ("interview", "offer"):
        raise BizError(40901, "候选人须完成面试后才能发放 Offer")
    r = hr_doc_service.generate(db, "offer", {"name": c.name, "position": c.position, **body}, None, p.id)
    c.stage = "offer"
    db.commit()
    return ok({**r, "candidate": recruit_service.cand_row(c)})


@router.get("/recruit/funnel")
def recruit_funnel(position: str = "", p: Principal = HR, db: Session = Depends(get_db)):
    return ok(recruit_service.funnel(db, position or None))


# ================= 员工自助答疑（全员） =================
@router.post("/qa")
def qa(question: str = Body(embed=True), p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    return ok(hr_service.answer(db, p.user, question))


@router.get("/qa/suggestions")
def qa_suggestions(p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    return ok({"questions": ["我还有几天年假？", "我这个月迟到几次？", "病假怎么请？需要什么证明？", "社保公积金什么时候缴？",
                             "加班可以调休吗？", "离职需要提前多久申请？"],
               "faq": [{"title": f["title"], "url": f["url"]} for f in hr_service.FAQ]})


@router.get("/me")
def my_profile(p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """员工个人人事信息（年假余额、本人最近考勤），供门户/个人中心使用。"""
    e = db.scalar(select(HrEmployee).where(HrEmployee.user_id == p.id))
    cur = f"{today():%Y-%m}"
    last = att.summary_rows(db, cur, p.id) or att.summary_rows(db, prev_month(cur), p.id)
    return ok({"annual_leave": hr_service.annual_leave(db, p.id),
               "contract_end_date": e.contract_end_date if e else None,
               "probation_end_date": e.probation_end_date if e and e.employ_type == "试用" else None,
               "attendance": last[0] if last else None})


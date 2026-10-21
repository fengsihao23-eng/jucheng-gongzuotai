"""人事中心（PRD 7.5）：考勤判定与规则配置化、汇总表、跨表核对、到期预警提前量、文书与风险提示、自助答疑、PPT、招聘、RBAC。"""
import io
import zipfile
from datetime import date, datetime, timedelta
from decimal import Decimal

from openpyxl import Workbook, load_workbook
from sqlalchemy import select

from tests.conftest import data


def _uid(db, userid):
    from app.models import SysUser
    return db.scalar(select(SysUser.id).where(SysUser.wecom_userid == userid))


def _dt(d, hm):
    h, m = map(int, hm.split(":"))
    return datetime(d.year, d.month, d.day, h, m)


# ---------------- 判定规则（纯函数边界） ----------------
def test_evaluate_day_boundaries():
    from app.services.attendance_service import DEFAULT_RULE, evaluate_day
    r = dict(DEFAULT_RULE)
    d = date(2026, 3, 2)  # 周一
    ev = lambda ci, co, lt=None, lh=0: evaluate_day(r, d, ci and _dt(d, ci), co and _dt(d, co), lt, lh)  # noqa: E731
    assert ev("09:10", "18:00")["types"] == []                       # 宽限 10 分钟内不算迟到
    x = ev("09:11", "18:00")
    assert x["types"] == ["迟到"] and x["late_minutes"] == 11
    assert ev("08:50", None)["types"] == ["缺卡"] and ev("08:50", None)["missing"] == 1
    x = ev("11:01", "18:00")                                         # 迟到 121 分钟 > 120 → 旷工半天
    assert x["types"] == ["旷工"] and x["absent_days"] == Decimal("0.5")
    assert ev("11:00", "18:00")["types"] == ["迟到"]                 # 恰好 120 分钟仍为迟到
    assert ev(None, None)["types"] == ["旷工"] and ev(None, None)["absent_days"] == 1
    x = ev("11:05", "18:00", "事假", Decimal("2"))                   # 上午请假 2 小时抵消迟到
    assert x["types"] == ["请假"] and x["late_minutes"] == 0
    x = ev("08:55", "16:00", "病假/下午", Decimal("2"))              # 下午请假 2 小时抵消早退
    assert x["types"] == ["请假"]
    assert ev("08:55", "17:30")["types"] == ["早退"]
    x = ev("08:55", "21:40")                                         # 19:00 起算 160 分钟 → 按 30 分钟取整 2.5h
    assert x["types"] == ["加班"] and x["overtime_hours"] == Decimal("2.50")
    assert ev("08:55", "19:20")["overtime_hours"] == 0                # 不足 30 分钟不计
    sat = date(2026, 3, 7)
    x = evaluate_day(r, sat, _dt(sat, "10:00"), _dt(sat, "15:00"))
    assert x["types"] == ["加班"] and x["overtime_hours"] == Decimal("5.00") and not x["workday"]
    assert evaluate_day(r, sat, None, None)["types"] == []           # 休息日无打卡非异常
    # 法定节假日/调休上班日
    assert not evaluate_day(r, date(2026, 10, 1), None, None)["workday"]
    assert evaluate_day(r, date(2026, 10, 10), None, None)["types"] == ["旷工"]


def test_rule_change_changes_judgement():
    from app.services.attendance_service import DEFAULT_RULE, evaluate_day
    d = date(2026, 3, 2)
    r = {**DEFAULT_RULE, "late_grace_minutes": 0, "work_start": "08:30"}
    assert evaluate_day(r, d, _dt(d, "08:35"), _dt(d, "18:00"))["types"] == ["迟到"]
    r = {**DEFAULT_RULE, "absent_threshold_minutes": 30}
    assert evaluate_day(r, d, _dt(d, "09:45"), _dt(d, "18:00"))["types"] == ["旷工"]


# ---------------- 导入 → 明细 → 规则修改重判 → 汇总表 ----------------
MARCH = [  # (日期, 上班, 下班, 审批单)
    ("2026-03-02", "09:10", "18:01", ""),
    ("2026-03-03", "09:11", "18:05", ""),
    ("2026-03-04", "08:50", "--", ""),
    ("2026-03-05", "11:30", "18:10", ""),
    ("2026-03-06", "11:05", "18:02", "请假-事假 2小时（上午）"),
    ("2026-03-09", "08:55", "21:40", "加班"),
]


def _march_excel():
    wb = Workbook()
    ws = wb.active
    ws.append(["2026年03月 考勤报表"])
    ws.append(["姓名", "账号", "部门", "日期", "上班打卡", "下班打卡", "审批单(请假/加班)"])
    for d, ci, co, ap in MARCH:
        ws.append(["丁锐", "dingrui", "项目部", f"{d} 星期一", ci, co, ap])
    ws.append(["路人甲", "nobody", "项目部", "2026-03-02", "09:00", "18:00", ""])   # 不在花名册
    ws.append(["丁锐", "dingrui", "项目部", "2026-03-32", "09:00", "18:00", ""])    # 行级错误
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _import_march(client, login):
    r = client.post("/api/v1/hr/attendance/import", headers=login("caofang"),
                    files={"file": ("考勤_2026-03.xlsx", _march_excel(),
                                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    return data(r)


def test_import_records_and_summary(client, login, db):
    from app.models import HrAttendanceRecord
    res = _import_march(client, login)
    assert res["period"] == "2026-03"
    uid = _uid(db, "dingrui")
    recs = {r.work_date.isoformat(): r for r in db.scalars(select(HrAttendanceRecord).where(HrAttendanceRecord.user_id == uid,
                                                                                           HrAttendanceRecord.work_date.between(date(2026, 3, 1), date(2026, 3, 31))))}
    assert recs["2026-03-02"].exception_type is None
    assert recs["2026-03-03"].exception_type == "迟到" and recs["2026-03-03"].late_minutes == 11
    assert recs["2026-03-04"].exception_type == "缺卡"
    assert recs["2026-03-05"].exception_type == "旷工"
    assert recs["2026-03-06"].exception_type == "请假" and recs["2026-03-06"].leave_hours == Decimal("2.00")
    assert recs["2026-03-09"].exception_type == "加班" and recs["2026-03-09"].overtime_hours == Decimal("2.50")
    row = next(x for x in res["summary_rows"] if x["userid"] == "dingrui")
    assert (row["late_times"], row["missing_times"], row["absent_times"]) == (1, 1, 1)
    assert Decimal(row["overtime_hours"]) == Decimal("2.5") and Decimal(row["leave_days"]) == Decimal("0.25")
    # 行级错误与跨表核对
    assert any("2026-03-32" in e["message"] for e in res["import"]["row_errors"])
    kinds = {x["kind"] for x in res["crosscheck"]}
    assert {"not_in_roster", "no_punch"} <= kinds
    assert any(x["name"] == "路人甲" for x in res["crosscheck"] if x["kind"] == "not_in_roster")
    ex = data(client.get("/api/v1/hr/attendance/exceptions", params={"period": "2026-03"}, headers=login("caofang")))
    assert {tuple(e["types"]) for e in ex if e["name"] == "丁锐"} == {("迟到",), ("缺卡",), ("旷工",)}
    cc = data(client.get("/api/v1/hr/attendance/crosscheck", params={"period": "2026-03"}, headers=login("caofang")))
    assert any(x["kind"] == "not_in_roster" for x in cc["items"])

    # 汇总表 xlsx：汇总 sheet + 异常明细 + 规则说明 + 口径说明
    f = res["file"]
    wb = load_workbook(io.BytesIO(client.get(f["url"]).content))
    assert {"考勤汇总", "异常明细", "规则说明", "口径说明", "跨表核对"} <= set(wb.sheetnames)
    hdr = [c.value for c in wb["考勤汇总"][1]]
    line = next(r for r in wb["考勤汇总"].iter_rows(min_row=2, values_only=True) if r[1] == "丁锐")
    assert line[hdr.index("迟到(次)")] == 1 and line[hdr.index("旷工(次)")] == 1 and line[hdr.index("加班(小时)")] == 2.5
    detail = [r for r in wb["异常明细"].iter_rows(min_row=2, values_only=True) if r[3] == "丁锐"]
    assert len(detail) == 5  # 迟到/缺卡/旷工/请假/加班
    rules = {r[0]: r[1] for r in wb["规则说明"].iter_rows(min_row=2, values_only=True) if r[0]}
    assert rules["迟到宽限（分钟）"] == "10"


def test_rule_update_and_recheck(client, login, db):
    from app.models import HrAttendanceRecord
    _import_march(client, login)
    h = login("caofang")
    old = data(client.get("/api/v1/hr/attendance/rule", headers=h))["rule"]
    uid = _uid(db, "dingrui")
    try:
        data(client.put("/api/v1/hr/attendance/rule", json={"rule": {"late_grace_minutes": 5}}, headers=h))
        data(client.post("/api/v1/hr/attendance/recheck", json={"period": "2026-03"}, headers=h))
        db.expire_all()
        rec = db.scalar(select(HrAttendanceRecord).where(HrAttendanceRecord.user_id == uid,
                                                         HrAttendanceRecord.work_date == date(2026, 3, 2)))
        assert rec.exception_type == "迟到" and rec.late_minutes == 10     # 09:10 在宽限 5 分钟下判为迟到
        s = data(client.get("/api/v1/hr/attendance/summary", params={"period": "2026-03"}, headers=h))
        assert next(x for x in s["summary_rows"] if x["userid"] == "dingrui")["late_times"] == 2
        # 非法规则拒绝
        assert client.put("/api/v1/hr/attendance/rule", json={"rule": {"work_start": "25:00"}}, headers=h).json()["code"] == 40001
    finally:
        data(client.put("/api/v1/hr/attendance/rule", json={"rule": old}, headers=h))
        data(client.post("/api/v1/hr/attendance/recheck", json={"period": "2026-03"}, headers=h))
    db.expire_all()
    rec = db.scalar(select(HrAttendanceRecord).where(HrAttendanceRecord.user_id == uid,
                                                     HrAttendanceRecord.work_date == date(2026, 3, 2)))
    assert rec.exception_type is None


def test_seeded_month_and_mcp_reuse(client, login, db):
    """种子已导入上月考勤；MCP hr.attendance_summary 复用 attendance_summary()。"""
    from app.services.attendance_service import attendance_summary
    r = attendance_summary(db, "2026-09")
    assert r["file"]["filename"] == "考勤汇总_2026-09.xlsx"
    assert r["totals"]["late_times"] > 0 and r["totals"]["absent_times"] > 0
    assert "人力摘要" in r["analysis"] and "考勤核对" in r["analysis"]
    assert any(x["kind"] == "no_punch" and x["name"] == "潘慧" for x in r["crosscheck"])
    types = {t for e in r["exceptions"] for t in e["types"]}
    assert {"迟到", "早退", "缺卡", "旷工"} <= types
    import pytest
    from app.core.response import BizError
    with pytest.raises(BizError):
        attendance_summary(db, "2025-01")


# ---------------- 到期预警 ----------------
def test_alert_lead_days_configurable(client, login, db):
    from app.core.timeutil import today
    from app.models import HrEmployee
    h = login("caofang")
    uid = _uid(db, "liuchang")
    e = db.scalar(select(HrEmployee).where(HrEmployee.user_id == uid))
    old_end = e.contract_end_date
    e.contract_end_date = today() + timedelta(days=40)
    db.commit()
    try:
        data(client.put("/api/v1/hr/alerts/rule", json={"contract_lead_days": 30}, headers=h))
        data(client.post("/api/v1/hr/alerts/scan", json={"notify": False}, headers=h))
        n30 = data(client.get("/api/v1/hr/alerts", headers=h))["items"]
        assert all(x["user_id"] != uid for x in n30)
        data(client.put("/api/v1/hr/alerts/rule", json={"contract_lead_days": 45}, headers=h))
        r = data(client.post("/api/v1/hr/alerts/scan", json={"notify": True}, headers=h))
        n45 = data(client.get("/api/v1/hr/alerts", headers=h))["items"]
        assert len(n45) > len(n30) and any(x["user_id"] == uid and x["days_left"] == 40 for x in n45)
        assert r["notified_users"] >= 1
        a = next(x for x in n45 if x["user_id"] == uid)
        assert "梁伟" in a["leaders"]  # 部门主管
        data(client.put(f"/api/v1/hr/alerts/{a['id']}", json={"handled": True, "note": "已沟通续签"}, headers=h))
        assert all(x["id"] != a["id"] for x in data(client.get("/api/v1/hr/alerts", headers=h))["items"])
    finally:
        data(client.put("/api/v1/hr/alerts/rule", json={"contract_lead_days": 30}, headers=h))
        e = db.scalar(select(HrEmployee).where(HrEmployee.user_id == uid))
        e.contract_end_date = old_end
        db.commit()


def test_alert_notifies_hr_and_dept_leader(db):
    """种子到期扫描：HR(RS-01) 与部门负责人收到通知。"""
    from app.models import SysNotify
    rows = db.scalars(select(SysNotify).where(SysNotify.msg_type == "hr_alert", SysNotify.channel == "inapp")).all()
    users = {r.user_id for r in rows}
    assert _uid(db, "caofang") in users and _uid(db, "yangyue") in users  # 客服主管（黄琪/姚瑶所在部门）


def test_contract_alert_job_idempotent(db):
    from app.jobs import registry
    if not registry.JOBS:
        from app import jobs
        jobs.load_all()
    first = registry.run_job("hr_contract_alert", {"notify": False}, trigger="manual", db=db)
    second = registry.run_job("hr_contract_alert", {"notify": False}, trigger="manual", db=db)
    assert first.status == "success" and first.idempotency_key == "hr_alert:2026-10-20"
    assert second.status == "skipped"


# ---------------- 文书 + 风险提示 ----------------
def test_onboarding_pack_and_risks(client, login, db):
    h = login("caofang")
    r = data(client.post("/api/v1/hr/docs/onboarding-pack", headers=h, json={
        "user_id": _uid(db, "jinbo"), "params": {"probation_months": 3, "contract_years": 1, "salary": 8000,
                                                 "probation_salary": 6000, "work_location": "广州市天河区"}}))
    assert [d["doc_type"] for d in r["documents"]] == ["labor_contract", "nda", "onboard_notice", "handover"]
    rules = {x["rule"] for x in r["risks"]}
    assert "probation_over_limit" in rules            # 1 年合同试用期上限 2 个月
    assert "probation_salary_low" in rules            # 6000 < 8000*80%
    assert "仅作提示" in r["disclaimer"] and "不替代法务" in r["disclaimer"]
    z = zipfile.ZipFile(io.BytesIO(client.get(r["package"]["file"]["url"]).content))
    assert len([n for n in z.namelist() if n.endswith(".docx")]) == 4
    from docx import Document
    txt = "\n".join(p.text for p in Document(io.BytesIO(z.read(next(n for n in z.namelist() if n.startswith("劳动合同"))))).paragraphs)
    assert "金波" in txt and "广州市天河区" in txt
    # 单份可下载
    assert client.get(r["documents"][0]["file"]["url"]).status_code == 200


def test_doc_risk_rules(client, login, db):
    h = login("caofang")
    chk = lambda t, p, uid=None: {x["rule"] for x in data(client.post("/api/v1/hr/docs/risk-check", headers=h,  # noqa: E731
                                                                       json={"doc_type": t, "params": p, "user_id": uid}))["risks"]}
    assert "probation_over_limit" not in chk("labor_contract", {"probation_months": 2, "contract_years": 1,
                                                                 "work_location": "广州", "position": "工程师"})
    assert "probation_over_limit" in chk("labor_contract", {"probation_months": 1, "contract_years": 0.2})
    assert "missing_location" in chk("labor_contract", {"position": "工程师"})
    assert "non_compete_no_comp" in chk("nda", {"non_compete": "是", "position": "售前工程师"})
    assert "termination_no_basis" in chk("termination", {"termination_type": "过失性辞退"})
    assert "leave_cert_negative" in chk("leave_cert", {"leave_reason": "严重违纪被开除"}, _uid(db, "tianye"))
    assert "salary_cut_unilateral" in chk("salary_adjust", {"old_salary": 9000, "new_salary": 8000})
    assert "salary_cut_unilateral" not in chk("salary_adjust", {"old_salary": 9000, "new_salary": 8000, "agreed": "是"})
    # 生成各类文书均可用
    for t in ("leave_cert", "termination", "offer", "salary_adjust", "holiday_notice", "care_notice", "event_notice",
              "pip_notice", "perf_plan", "training_plan", "course_outline", "training_survey", "training_summary", "policy"):
        g = data(client.post("/api/v1/hr/docs/generate", headers=h,
                             json={"doc_type": t, "user_id": _uid(db, "weichen"), "params": {"topic": "测试"}}))
        assert g["document"]["file"]["filename"].endswith(".docx")
    assert len(data(client.get("/api/v1/hr/docs", headers=h))) >= 14


def test_minutes_action_items(client, login):
    raw = ("1. 回顾 Q3 招聘进展，项目部缺编 2 人。\n2. 由朱怡负责发布项目经理 JD，10月25日前完成初筛。\n"
           "3. 邓欣跟进 9 月考勤异常人员面谈，本周五前反馈。\n4. 年会方案曹芳牵头，月底前提交总经办。")
    r = data(client.post("/api/v1/hr/docs/minutes", headers=login("caofang"),
                         json={"topic": "人事部周例会", "date": "2026-10-20", "attendees": "曹芳、邓欣、朱怡", "raw_text": raw}))
    acts = {a["owner"]: a["due"] for a in r["actions"]}
    assert acts["朱怡"] == "2026-10-25" and acts["邓欣"] == "2026-10-23" and acts["曹芳"] == "2026-10-31"


# ---------------- 员工自助答疑（全员可用） ----------------
def test_qa_personal_annual_leave_and_rbac(client, login, db):
    from app.services.hr_service import annual_leave, fmt
    h = login("zengli")  # 普通员工
    assert client.get("/api/v1/hr/attendance/summary", headers=h).json()["code"] == 40301
    assert client.get("/api/v1/hr/overview", headers=h).json()["code"] == 40301
    assert client.post("/api/v1/hr/docs/generate", headers=login("xuhao"),
                       json={"doc_type": "offer", "params": {}}).json()["code"] == 40301
    al = annual_leave(db, _uid(db, "zengli"))
    r = data(client.post("/api/v1/hr/qa", headers=h, json={"question": "我还有几天年假？"}))
    assert r["personal"] and f"剩余 {fmt(al['remaining'])} 天" in r["answer"]
    assert r["sources"] and r["sources"][0]["url"].startswith("http")
    r = data(client.post("/api/v1/hr/qa", headers=h, json={"question": "我这个月迟到几次"}))
    assert r["personal"] and "迟到" in r["answer"]
    r = data(client.post("/api/v1/hr/qa", headers=h, json={"question": "病假怎么请"}))
    assert "医院证明" in r["answer"] and r["sources"]
    assert data(client.get("/api/v1/hr/me", headers=h))["annual_leave"]["total"]


def test_annual_leave_deducts_attendance(db):
    """年假余额 = 档案年假天数 − 当年考勤中的年假。"""
    from app.models import HrAttendanceRecord, HrEmployee
    from app.services.hr_service import annual_leave
    uid = _uid(db, "yangyue")  # 种子中 9 月有 1 天年假
    e = db.scalar(select(HrEmployee).where(HrEmployee.user_id == uid))
    used_h = sum((r.leave_hours for r in db.scalars(select(HrAttendanceRecord).where(
        HrAttendanceRecord.user_id == uid, HrAttendanceRecord.leave_type.like("年假%")))), Decimal(0))
    al = annual_leave(db, uid)
    assert used_h >= 8 and al["remaining"] == Decimal(e.annual_leave_days) - used_h / 8


# ---------------- 人力分析 / PPT ----------------
def test_analysis_and_ppt(client, login):
    h = login("caofang")
    a = data(client.get("/api/v1/hr/analysis", headers=h))
    assert a["headcount"]["planned"] == 50 and a["headcount"]["actual"] > 0
    assert len(a["turnover"]["rows"]) == 12 and a["turnover"]["year"]["leaves"] >= 1
    assert {x["name"] for x in a["structure"]["tenure"]} and a["structure"]["total"] == a["headcount"]["actual"]
    assert Decimal(a["efficiency"]["per_capita_payment"]) > 0
    r = data(client.post("/api/v1/hr/analysis/ppt", json={"period": "2026-09"}, headers=h))
    from pptx import Presentation
    prs = Presentation(io.BytesIO(client.get(r["file"]["url"]).content))
    assert len(prs.slides) >= 6
    assert any(sh.has_chart for s in prs.slides for sh in s.shapes)
    ov = data(client.get("/api/v1/hr/overview", headers=h))
    assert ov["kpi"]["active"] == a["headcount"]["actual"] and ov["kpi"]["alerts"] >= 1


def test_monthly_job(db):
    from app.jobs import registry
    if not registry.JOBS:
        from app import jobs
        jobs.load_all()
    log = registry.run_job("hr_monthly_analysis", {"period": "2026-09"}, trigger="manual", force=True, db=db)
    assert log.status == "success", log.error_msg


# ---------------- 招聘 ----------------
RESUMES = {
    "张三_简历.txt": "姓名：张三\n学历：本科\n8年工作经验，负责智慧校园弱电系统集成项目管理，持一建证书、PMP。",
    "李四_简历.txt": "姓名：李四\n学历：大专\n3年相关经验，熟悉施工管理、CAD。",
    "王五_简历.txt": "姓名：王五\n学历：硕士\n5年项目经验，项目管理、系统集成。",
}


def test_resume_screen_rank(client, login):
    h = login("zhuyi")
    files = [("files", (n, t.encode("utf-8"), "text/plain")) for n, t in RESUMES.items()]
    r = data(client.post("/api/v1/hr/recruit/screen", headers=h, files=files,
                         data={"position": "项目经理（测试）", "keywords": "项目管理,一建,PMP,弱电,系统集成",
                               "min_education": "本科", "min_years": 5, "pass_score": 60}))
    names = [c["name"] for c in r["candidates"]]
    assert names == ["张三", "王五", "李四"]
    scores = [c["screen_score"] for c in r["candidates"]]
    assert scores == sorted(scores, reverse=True) and scores[0] == 100
    li = r["candidates"][-1]
    assert li["stage"] == "rejected" and "学历" in li["screen_reason"] and "不达标" in li["screen_reason"]
    assert r["candidates"][0]["stage"] == "screen_pass"


def test_candidate_flow_and_funnel(client, login):
    h = login("zhuyi")
    jd = data(client.post("/api/v1/hr/recruit/jd", headers=h, json={"position": "售前工程师", "dept": "产品中心",
                                                                  "skills": "标书,方案,海康", "education": "本科", "years": 3}))
    assert "【岗位职责】" in jd["text"] and "标书" in jd["text"] and jd["document"]["file"]
    c = next(x for x in data(client.get("/api/v1/hr/recruit/candidates", params={"position": "项目经理（测试）"}, headers=h))["items"]
             if x["name"] == "张三")
    assert client.put(f"/api/v1/hr/recruit/candidates/{c['id']}/stage", json={"stage": "hired"}, headers=h).json()["code"] == 40901
    ev = data(client.post(f"/api/v1/hr/recruit/candidates/{c['id']}/interview", headers=h, json={
        "scores": {"professional": 5, "communication": 4, "teamwork": 4, "learning": 5, "culture": 4}}))
    assert ev["stage"] == "interview" and ev["interview_eval"]["conclusion"] in ("强烈推荐", "推荐录用")
    off = data(client.post(f"/api/v1/hr/recruit/candidates/{c['id']}/offer", headers=h,
                           json={"salary": 15000, "probation_months": 3, "contract_years": 3}))
    assert off["candidate"]["stage"] == "offer" and any(x["rule"] == "offer_validity" for x in off["risks"])
    fn = data(client.get("/api/v1/hr/recruit/funnel", headers=h))
    counts = [s["count"] for s in fn["steps"]]
    assert counts == sorted(counts, reverse=True) and fn["total"] >= 15


def test_read_endpoints_smoke(client, login):
    h = login("dengxin")
    for url, params in [("/api/v1/hr/employees", None), ("/api/v1/hr/docs/types", None), ("/api/v1/hr/attendance/periods", None),
                        ("/api/v1/hr/attendance/rule", None), ("/api/v1/hr/attendance/export", {"period": "2026-09"}),
                        ("/api/v1/hr/alerts", {"status": "all"}), ("/api/v1/hr/recruit/candidates", None),
                        ("/api/v1/hr/qa/suggestions", None), ("/api/v1/hr/me", None)]:
        data(client.get(url, params=params, headers=h))
    assert len(data(client.get("/api/v1/hr/docs/types", headers=h))["types"]) >= 20
    assert client.get("/api/v1/hr/attendance/periods", headers=login("zengli")).json()["code"] == 40301

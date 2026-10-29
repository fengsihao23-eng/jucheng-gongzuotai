"""人事中心：人力分析（编制/入离职/结构/人效）、月度人力分析 PPT、合同/试用期到期预警、员工自助答疑、概览。

考勤核对见 attendance_service；文书生成见 hr_doc_service；招聘见 recruit_service。
"""
import io
import re
from collections import Counter, defaultdict
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.storage import file_brief, save_bytes
from app.core.timeutil import month_range, prev_month, today
from app.models import (BizContract, BizPayment, HrAttendanceRecord, HrAttendanceSummary, HrCandidate,
                        HrContractAlert, HrEmployee, HrHeadcountPlan, SysDepartment, SysFile, SysUser)
from app.services import attendance_service as att
from app.services import dict_service, notify_service

D0 = Decimal("0")
DEFAULT_ALERT = {"contract_lead_days": 30, "probation_lead_days": 15, "leader_keywords": ["经理", "主管", "总监"]}


# =============== 人力分析 ===============
def _employees(db: Session) -> list[HrEmployee]:
    return [e for e in db.scalars(select(HrEmployee)).all() if e.user and not e.user.is_deleted]


def _active_on(e: HrEmployee, d: date) -> bool:
    return e.hire_date <= d and (e.leave_date is None or e.leave_date > d)


def _depts(db: Session) -> dict[str, str]:
    return {d.dept_code: d.name for d in db.scalars(select(SysDepartment).order_by(SysDepartment.sort)).all()}


def _months_back(end_period: str, n: int) -> list[str]:
    out = [end_period]
    for _ in range(n - 1):
        out.append(prev_month(out[-1]))
    return out[::-1]


def headcount(db: Session, year: int | None = None, on: date | None = None) -> dict:
    on = on or today()
    year = year or on.year
    emps = _employees(db)
    plans = {p.dept_code: p.planned for p in db.scalars(select(HrHeadcountPlan).where(HrHeadcountPlan.year == year))}
    names = _depts(db)
    actual = Counter(e.user.dept_code for e in emps if _active_on(e, on))
    rows = []
    for code in sorted(set(plans) | set(actual), key=lambda c: list(names).index(c) if c in names else 99):
        planned, act = plans.get(code, 0), actual.get(code, 0)
        rows.append({"dept_code": code, "dept_name": names.get(code, code), "planned": planned, "actual": act,
                     "gap": planned - act, "rate": round(act / planned, 4) if planned else None})
    tp, ta = sum(plans.values()), sum(actual.values())
    return {"year": year, "as_of": on, "rows": rows, "planned": tp, "actual": ta, "gap": tp - ta,
            "rate": round(ta / tp, 4) if tp else None}


def turnover(db: Session, end_period: str | None = None, months: int = 12) -> dict:
    end_period = end_period or f"{today():%Y-%m}"
    emps = _employees(db)
    t = today()
    rows = []
    for p in _months_back(end_period, months):
        start, end = month_range(p)
        end_eff = min(end, t)
        begin_n = sum(_active_on(e, start - timedelta(days=1)) for e in emps)
        end_n = sum(_active_on(e, end_eff) for e in emps)
        hires = sum(start <= e.hire_date <= end_eff for e in emps)
        leaves = [e for e in emps if e.leave_date and start <= e.leave_date <= end_eff]
        avg = (begin_n + end_n) / 2 or 1
        rows.append({"period": p, "begin": begin_n, "end": end_n, "hires": hires, "leaves": len(leaves),
                     "hire_rate": round(hires / avg, 4), "leave_rate": round(len(leaves) / avg, 4),
                     "leavers": [{"name": e.user.name, "reason": e.leave_reason, "date": e.leave_date} for e in leaves]})
    ys = date(int(end_period[:4]), 1, 1)
    ye = min(month_range(end_period)[1], t)
    y_hires = sum(ys <= e.hire_date <= ye for e in emps)
    y_leaves = sum(bool(e.leave_date and ys <= e.leave_date <= ye) for e in emps)
    avg_y = _avg_headcount(emps, ys, ye) or 1
    return {"rows": rows, "year": {"year": ys.year, "hires": y_hires, "leaves": y_leaves, "avg_headcount": round(avg_y, 1),
                                   "hire_rate": round(y_hires / avg_y, 4), "leave_rate": round(y_leaves / avg_y, 4)}}


def _avg_headcount(emps: list[HrEmployee], start: date, end: date) -> float:
    """按月末在职人数平均（含期初）。"""
    points, d = [start], date(start.year, start.month, 1)
    while True:
        me = month_range(f"{d:%Y-%m}")[1]
        if me >= end:
            break
        points.append(me)
        d = me + timedelta(days=1)
    points.append(end)
    return sum(sum(_active_on(e, p) for e in emps) for p in points) / len(points)


def _tenure_bucket(days: int) -> str:
    y = days / 365
    return "1 年以下" if y < 1 else "1-3 年" if y < 3 else "3-5 年" if y < 5 else "5 年以上"


def structure(db: Session, on: date | None = None) -> dict:
    on = on or today()
    act = [e for e in _employees(db) if _active_on(e, on)]
    names = _depts(db)

    def cnt(fn, order=None):
        c = Counter(fn(e) or "未填" for e in act)
        keys = [k for k in (order or []) if k in c] + sorted(k for k in c if k not in (order or []))
        return [{"name": k, "value": c[k]} for k in keys]
    return {"total": len(act),
            "dept": cnt(lambda e: names.get(e.user.dept_code, e.user.dept_code), list(names.values())),
            "education": cnt(lambda e: e.education, ["博士", "硕士", "本科", "大专", "高中及以下"]),
            "tenure": cnt(lambda e: _tenure_bucket((on - e.hire_date).days), ["1 年以下", "1-3 年", "3-5 年", "5 年以上"]),
            "gender": cnt(lambda e: e.gender, ["男", "女"]),
            "employ_type": cnt(lambda e: e.employ_type, ["正式", "试用", "实习", "外包"])}


def efficiency(db: Session, year: int | None = None) -> dict:
    t = today()
    year = year or t.year
    ys, ye = date(year, 1, 1), min(date(year, 12, 31), t)
    pay = db.scalar(select(func.coalesce(func.sum(BizPayment.amount), 0))
                    .where(BizPayment.payment_date.between(ys, ye))) or D0
    sign = db.scalar(select(func.coalesce(func.sum(BizContract.amount), 0))
                     .where(BizContract.sign_date.between(ys, ye), BizContract.is_deleted.is_(False))) or D0
    avg = _avg_headcount(_employees(db), ys, ye) or 1
    avg_d = Decimal(str(round(avg, 2)))
    return {"year": year, "as_of": ye, "payment_total": Decimal(pay), "sign_total": Decimal(sign),
            "avg_headcount": round(avg, 1),
            "per_capita_payment": (Decimal(pay) / avg_d).quantize(Decimal("0.01")),
            "per_capita_sign": (Decimal(sign) / avg_d).quantize(Decimal("0.01")),
            "caliber": "人均回款 = 当年回款合计（银行到账日口径）/ 平均在职人数（月末在职人数平均）；人均签约额同理"}


def analysis(db: Session, period: str | None = None) -> dict:
    period = period or f"{today():%Y-%m}"
    start, end = month_range(period)
    on = min(end, today())
    return {"period": period, "headcount": headcount(db, int(period[:4]), on), "turnover": turnover(db, period),
            "structure": structure(db, on), "efficiency": efficiency(db, int(period[:4]))}


def analysis_brief(db: Session, period: str) -> str:
    a = analysis(db, period)
    hc, tm, ef = a["headcount"], a["turnover"]["rows"][-1], a["efficiency"]
    short = [r for r in hc["rows"] if r["gap"] > 0]
    s = (f"人力摘要：在职 {hc['actual']} 人 / 编制 {hc['planned']} 人（满编率 {hc['rate'] * 100:.1f}%），"
         f"{period} 入职 {tm['hires']} 人、离职 {tm['leaves']} 人（离职率 {tm['leave_rate'] * 100:.1f}%）；"
         f"{ef['year']} 年人均回款 {ef['per_capita_payment'] / 10000:.1f} 万元。")
    if short:
        s += "缺编：" + "、".join(f"{r['dept_name']} {r['gap']} 人" for r in short) + "。"
    return s


# =============== 月度人力分析 PPT ===============
def build_ppt(db: Session, period: str, user_id: int | None = None) -> SysFile:
    from pptx import Presentation
    from pptx.chart.data import CategoryChartData
    from pptx.dml.color import RGBColor
    from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.util import Emu, Inches, Pt

    PAPER, INK, INK3, BRAND, GOLD, LINE = (RGBColor(0xFA, 0xF7, 0xF1), RGBColor(0x1D, 0x1F, 0x24), RGBColor(0x7B, 0x7E, 0x86),
                                           RGBColor(0x1B, 0x32, 0x75), RGBColor(0xB0, 0x89, 0x4F), RGBColor(0xE6, 0xE2, 0xD9))
    SERIES = [BRAND, GOLD, RGBColor(0x5D, 0x7F, 0x73), RGBColor(0x9A, 0x5B, 0x4A), RGBColor(0x7D, 0x8A, 0xA6),
              RGBColor(0xC9, 0xB4, 0x8A)]
    a = analysis(db, period)
    hc, to, st, ef = a["headcount"], a["turnover"], a["structure"], a["efficiency"]
    summ = att.summary_rows(db, period)
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    blank = prs.slide_layouts[6]

    def base(title: str, sub: str | None = None):
        s = prs.slides.add_slide(blank)
        s.background.fill.solid()
        s.background.fill.fore_color.rgb = PAPER
        bar = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0.6), Inches(0.55), Inches(0.08), Inches(0.5))
        bar.fill.solid()
        bar.fill.fore_color.rgb = BRAND
        bar.line.fill.background()
        txt(s, title, 0.8, 0.45, 11, 0.7, 26, INK, bold=True)
        if sub:
            txt(s, sub, 0.8, 1.05, 11.5, 0.4, 13, INK3)
        ln = s.shapes.add_connector(1, Inches(0.6), Inches(1.5), Inches(12.73), Inches(1.5))
        ln.line.color.rgb = LINE
        txt(s, f"聚成数智 · 人事部 · {period} 月度人力分析", 0.6, 7.0, 8, 0.3, 10, INK3)
        return s

    def txt(s, text, x, y, w, h, size=14, color=INK, bold=False):
        tb = s.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
        tf = tb.text_frame
        tf.word_wrap = True
        for i, line in enumerate(str(text).split("\n")):
            p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            p.text = line
            p.font.size, p.font.color.rgb, p.font.bold = Pt(size), color, bold
            p.font.name = "Microsoft YaHei"
        return tb

    def kpi(s, x, y, label, value, unit=""):
        box = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(x), Inches(y), Inches(2.85), Inches(1.4))
        box.fill.solid()
        box.fill.fore_color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
        box.line.color.rgb = LINE
        txt(s, label, x + 0.2, y + 0.15, 2.5, 0.35, 12, INK3)
        txt(s, f"{value}{unit}", x + 0.2, y + 0.55, 2.5, 0.7, 28, BRAND, bold=True)

    def chart(s, kind, cats, series: dict, x, y, w, h, legend=True, labels=False):
        cd = CategoryChartData()
        cd.categories = cats
        for name, vals in series.items():
            cd.add_series(name, vals)
        gf = s.shapes.add_chart(kind, Inches(x), Inches(y), Inches(w), Inches(h), cd)
        ch = gf.chart
        ch.font.size, ch.font.name = Pt(11), "Microsoft YaHei"
        ch.font.color.rgb = INK3
        ch.has_legend = legend
        if legend:
            ch.legend.position, ch.legend.include_in_layout = XL_LEGEND_POSITION.BOTTOM, False
        plot = ch.plots[0]
        if kind == XL_CHART_TYPE.DOUGHNUT or kind == XL_CHART_TYPE.PIE:
            for i, pt in enumerate(plot.series[0].points):
                pt.format.fill.solid()
                pt.format.fill.fore_color.rgb = SERIES[i % len(SERIES)]
        else:
            for i, ser in enumerate(plot.series):
                if kind == XL_CHART_TYPE.LINE_MARKERS:
                    ser.format.line.color.rgb = SERIES[i % len(SERIES)]
                    ser.smooth = False
                else:
                    ser.format.fill.solid()
                    ser.format.fill.fore_color.rgb = SERIES[i % len(SERIES)]
            try:
                ch.value_axis.major_gridlines.format.line.color.rgb = LINE
                ch.value_axis.format.line.fill.background()
            except Exception:  # noqa: BLE001
                pass
        if labels:
            plot.has_data_labels = True
            plot.data_labels.font.size = Pt(10)
        return ch

    # 1 封面
    s = prs.slides.add_slide(blank)
    s.background.fill.solid()
    s.background.fill.fore_color.rgb = PAPER
    band = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, Inches(2.5), prs.slide_width, Emu(Inches(0.06)))
    band.fill.solid()
    band.fill.fore_color.rgb = BRAND
    band.line.fill.background()
    txt(s, "广东聚成数智科技有限公司", 0.9, 1.4, 11, 0.6, 18, INK3)
    txt(s, f"{period} 月度人力分析报告", 0.9, 2.8, 11, 1.0, 40, INK, bold=True)
    txt(s, "人员概况 · 编制 · 入离职 · 考勤 · 人效 · 建议", 0.9, 3.9, 11, 0.5, 16, BRAND)
    txt(s, f"人事部 · 系统自动生成于 {today():%Y-%m-%d}（初稿，请人事复核）", 0.9, 6.4, 11, 0.4, 12, INK3)

    # 2 人员概况
    tm = to["rows"][-1]
    s = base("人员概况", f"截至 {hc['as_of']}，在职 {hc['actual']} 人")
    kpi(s, 0.6, 1.8, "在职人数", hc["actual"], " 人")
    kpi(s, 3.65, 1.8, "满编率", f"{(hc['rate'] or 0) * 100:.1f}", "%")
    kpi(s, 6.7, 1.8, "本月入职 / 离职", f"{tm['hires']} / {tm['leaves']}", " 人")
    kpi(s, 9.75, 1.8, "试用期人数", next((x["value"] for x in st["employ_type"] if x["name"] == "试用"), 0), " 人")
    chart(s, XL_CHART_TYPE.DOUGHNUT, [x["name"] for x in st["education"]], {"学历": [x["value"] for x in st["education"]]},
          0.6, 3.5, 4.0, 3.4, labels=True)
    chart(s, XL_CHART_TYPE.DOUGHNUT, [x["name"] for x in st["tenure"]], {"司龄": [x["value"] for x in st["tenure"]]},
          4.7, 3.5, 4.0, 3.4, labels=True)
    chart(s, XL_CHART_TYPE.PIE, [x["name"] for x in st["gender"]], {"性别": [x["value"] for x in st["gender"]]},
          8.8, 3.5, 4.0, 3.4, labels=True)

    # 3 编制
    s = base("编制与满编情况", f"{hc['year']} 年编制 {hc['planned']} 人，实际 {hc['actual']} 人，缺编 {max(hc['gap'], 0)} 人")
    rows = hc["rows"]
    chart(s, XL_CHART_TYPE.COLUMN_CLUSTERED, [r["dept_name"] for r in rows],
          {"编制": [r["planned"] for r in rows], "实际": [r["actual"] for r in rows]}, 0.6, 1.7, 8.2, 5.1, labels=True)
    gaps = [r for r in rows if r["gap"] != 0]
    txt(s, "差异部门\n" + ("\n".join(f"· {r['dept_name']}：{'缺编' if r['gap'] > 0 else '超编'} {abs(r['gap'])} 人" for r in gaps)
                       or "· 各部门均满编"), 9.1, 1.9, 3.8, 4.5, 14, INK)

    # 4 入离职
    s = base("入职与离职趋势", f"近 12 个月；{to['year']['year']} 年累计入职 {to['year']['hires']} 人、离职 {to['year']['leaves']} 人，"
                          f"年度离职率 {to['year']['leave_rate'] * 100:.1f}%")
    chart(s, XL_CHART_TYPE.COLUMN_CLUSTERED, [r["period"][2:] for r in to["rows"]],
          {"入职": [r["hires"] for r in to["rows"]], "离职": [r["leaves"] for r in to["rows"]]}, 0.6, 1.7, 7.6, 5.1)
    chart(s, XL_CHART_TYPE.LINE_MARKERS, [r["period"][2:] for r in to["rows"]],
          {"在职人数": [r["end"] for r in to["rows"]]}, 8.4, 1.7, 4.5, 2.7)
    leavers = [x for r in to["rows"][-3:] for x in r["leavers"]]
    txt(s, "近 3 月离职\n" + ("\n".join(f"· {x['name']}（{x['date']}，{x['reason'] or '未填原因'}）" for x in leavers) or "· 无"),
        8.5, 4.6, 4.4, 2.2, 12, INK)

    # 5 考勤
    s = base("考勤异常", f"{period} 考勤核对结果" + ("" if summ else "（本期尚未导入考勤数据）"))
    tot = {k: sum(r[k] for r in summ) for k in ("late_times", "early_times", "missing_times", "absent_times")}
    chart(s, XL_CHART_TYPE.BAR_CLUSTERED, ["迟到", "早退", "缺卡", "旷工"],
          {"次数": [tot["late_times"], tot["early_times"], tot["missing_times"], tot["absent_times"]]},
          0.6, 1.7, 5.4, 5.1, legend=False, labels=True)
    top = [r for r in sorted(summ, key=lambda r: -r["abnormal"]) if r["abnormal"]][:8]
    if top:
        tbl = s.shapes.add_table(len(top) + 1, 5, Inches(6.3), Inches(1.8), Inches(6.5), Inches(0.4 * (len(top) + 1))).table
        for j, h in enumerate(["姓名", "部门", "迟到", "缺卡", "旷工"]):
            tbl.cell(0, j).text = h
        for i, r in enumerate(top, start=1):
            for j, v in enumerate([r["name"], r["dept_name"], r["late_times"], r["missing_times"], r["absent_times"]]):
                tbl.cell(i, j).text = str(v)
        for i in range(len(top) + 1):
            for j in range(5):
                c = tbl.cell(i, j)
                c.fill.solid()
                c.fill.fore_color.rgb = BRAND if i == 0 else RGBColor(0xFF, 0xFF, 0xFF)
                for p in c.text_frame.paragraphs:
                    p.font.size = Pt(12)
                    p.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF) if i == 0 else INK
    else:
        txt(s, "本期无考勤异常人员。", 6.4, 2.0, 6, 0.5, 14, INK3)

    # 6 人效
    s = base("人效分析", ef["caliber"])
    kpi(s, 0.6, 1.8, f"{ef['year']} 年回款合计", f"{ef['payment_total'] / 10000:.0f}", " 万")
    kpi(s, 3.65, 1.8, "平均在职人数", ef["avg_headcount"], " 人")
    kpi(s, 6.7, 1.8, "人均回款", f"{ef['per_capita_payment'] / 10000:.1f}", " 万")
    kpi(s, 9.75, 1.8, "人均签约额", f"{ef['per_capita_sign'] / 10000:.1f}", " 万")
    chart(s, XL_CHART_TYPE.COLUMN_CLUSTERED, [x["name"] for x in st["dept"]], {"在职人数": [x["value"] for x in st["dept"]]},
          0.6, 3.5, 12.1, 3.3, legend=False, labels=True)

    # 7 建议
    s = base("问题与建议", "以下为规则生成的初稿建议，请结合业务实际调整")
    tips = suggestions(db, a, summ)
    txt(s, "\n".join(f"{i}. {t}" for i, t in enumerate(tips, start=1)), 0.8, 1.8, 11.8, 5.0, 16, INK)

    buf = io.BytesIO()
    prs.save(buf)
    fname = f"人力分析_{period}.pptx"
    return save_bytes(db, data=buf.getvalue(), filename=fname, category="report",
                      key=f"report/hr_analysis/{period}/{fname}", uploader_id=user_id,
                      content_type="application/vnd.openxmlformats-officedocument.presentationml.presentation")


def suggestions(db: Session, a: dict, summ: list[dict]) -> list[str]:
    hc, to = a["headcount"], a["turnover"]
    tips = []
    short = [r for r in hc["rows"] if r["gap"] > 0]
    if short:
        tips.append("缺编部门：" + "、".join(f"{r['dept_name']}缺 {r['gap']} 人" for r in short)
                    + "，建议同步招聘计划，优先保障项目交付岗位。")
    if to["year"]["leave_rate"] > 0.1:
        tips.append(f"年度离职率 {to['year']['leave_rate'] * 100:.1f}%，建议开展离职面谈复盘与关键岗位保留计划。")
    heavy = [r for r in summ if r["absent_times"] or r["late_times"] >= 3]
    if heavy:
        tips.append("考勤异常较多人员 " + "、".join(r["name"] for r in heavy[:6]) + "，建议部门负责人沟通并跟进。")
    ot = sorted(summ, key=lambda r: -r["overtime_hours"])[:3]
    if ot and ot[0]["overtime_hours"] > 10:
        tips.append("加班集中：" + "、".join(f"{r['name']} {r['overtime_hours']}h" for r in ot)
                    + "，关注工作负荷与调休安排，加班需按制度审批。")
    pending = db.scalar(select(func.count()).select_from(HrContractAlert).where(HrContractAlert.handled.is_(False))) or 0
    if pending:
        tips.append(f"合同/试用期到期预警 {pending} 条待处理，请在到期前完成续签或转正评估。")
    tips.append("持续完善员工档案（学历、合同期限等），保证人力报表口径准确。")
    return tips


# =============== 到期预警 ===============
def alert_rule(db: Session) -> dict:
    return {**DEFAULT_ALERT, **(dict_service.get(db, "alert_rule", "hr") or {})}


def dept_leaders(db: Session, dept_code: str | None, exclude_user_id: int | None = None) -> list[SysUser]:
    """部门负责人：部门内职位含“经理/主管/总监”（可配 alert_rule.hr.leader_keywords）。"""
    if not dept_code:
        return []
    kws = alert_rule(db)["leader_keywords"]
    users = db.scalars(select(SysUser).join(SysDepartment, SysDepartment.id == SysUser.department_id)
                       .where(SysDepartment.dept_code == dept_code, SysUser.status == 1,
                              SysUser.is_deleted.is_(False))).all()
    return [u for u in users if u.id != exclude_user_id and any(k in (u.position or "") for k in kws)]


def scan_alerts(db: Session, notify: bool = True) -> dict:
    rule = alert_rule(db)
    t = today()
    c_lead, p_lead = int(rule["contract_lead_days"]), int(rule["probation_lead_days"])
    created, hits = [], []
    for e in _employees(db):
        if e.user.status != 1 or (e.leave_date and e.leave_date <= t):
            continue
        for kind, due, lead in (("contract", e.contract_end_date, c_lead), ("probation", e.probation_end_date, p_lead)):
            if not due or not (t <= due <= t + timedelta(days=lead)):
                continue
            row = db.scalar(select(HrContractAlert).where(HrContractAlert.user_id == e.user_id,
                                                          HrContractAlert.alert_type == kind,
                                                          HrContractAlert.due_date == due))
            if not row:
                row = HrContractAlert(user_id=e.user_id, alert_type=kind, due_date=due, days_left=(due - t).days)
                db.add(row)
                created.append(row)
            row.days_left = (due - t).days
            hits.append((row, e))
    db.flush()
    sent = 0
    unsent = [(r, e) for r, e in hits if not r.alert_sent and not r.handled]
    if notify and unsent:
        hr_users = notify_service.users_by_roles(db, ["RS-01"])
        lines = [_alert_line(r, e) for r, e in unsent]
        notify_service.send(db, hr_users, "hr_alert", f"合同/试用期到期预警（{len(unsent)} 人）",
                            "\n".join(f"- {x}" for x in lines), link="/hr?tab=alerts", ref_type="hr_alert",
                            ref_id=f"{t}")
        sent += len(hr_users)
        by_dept = defaultdict(list)
        for r, e in unsent:
            by_dept[e.user.dept_code].append((r, e))
        hr_ids = {u.id for u in hr_users}
        for dept, items in by_dept.items():
            leaders = [u for u in dept_leaders(db, dept) if u.id not in hr_ids
                       and all(u.id != e.user_id for _, e in items)]
            if leaders:
                notify_service.send(db, leaders, "hr_alert", f"本部门员工到期提醒（{len(items)} 人）",
                                    "\n".join(f"- {_alert_line(r, e)}" for r, e in items) +
                                    "\n\n请与人事部沟通续签/转正评估意见。", link="/portal", ref_type="hr_alert",
                                    ref_id=f"{t}:{dept}")
                sent += len(leaders)
        for r, _ in unsent:
            r.alert_sent = True
    db.flush()
    return {"rule": rule, "hits": len(hits), "created": len(created), "notified_users": sent,
            "pending": len([1 for r, _ in hits if not r.handled])}


def _alert_line(r: HrContractAlert, e: HrEmployee) -> str:
    kind = "劳动合同到期" if r.alert_type == "contract" else "试用期届满"
    dept = e.user.department.name if e.user.department else ""
    return f"{dept} {e.user.name}：{kind} {r.due_date}（剩 {(r.due_date - today()).days} 天）"


def alert_rows(db: Session, status: str = "pending") -> list[dict]:
    rule = alert_rule(db)
    t = today()
    q = select(HrContractAlert)
    if status == "pending":
        q = q.where(HrContractAlert.handled.is_(False), HrContractAlert.due_date >= t)
    elif status == "handled":
        q = q.where(HrContractAlert.handled.is_(True))
    rows = db.scalars(q.order_by(HrContractAlert.due_date)).all()
    emps = {e.user_id: e for e in db.scalars(select(HrEmployee).where(HrEmployee.user_id.in_([r.user_id for r in rows])))}
    out = []
    for r in rows:
        e = emps.get(r.user_id)
        if status == "pending":
            lead = rule["contract_lead_days"] if r.alert_type == "contract" else rule["probation_lead_days"]
            if (r.due_date - t).days > int(lead):
                continue
        u = e.user if e else None
        out.append({"id": r.id, "user_id": r.user_id, "name": u.name if u else "", "employee_no": e.employee_no if e else "",
                    "dept_name": u.department.name if u and u.department else "", "position": u.position if u else "",
                    "alert_type": r.alert_type, "alert_type_name": "劳动合同到期" if r.alert_type == "contract" else "试用期届满",
                    "due_date": r.due_date, "days_left": (r.due_date - t).days, "alert_sent": r.alert_sent,
                    "handled": r.handled, "handle_note": r.handle_note, "hire_date": e.hire_date if e else None,
                    "contract_start_date": e.contract_start_date if e else None,
                    "leaders": [x.name for x in dept_leaders(db, u.dept_code if u else None, r.user_id)]})
    return out


# =============== 员工自助答疑 ===============
FAQ = [
    {"key": "attendance", "kw": ["迟到", "打卡", "考勤", "早退", "缺卡", "旷工", "上班时间", "下班时间"],
     "title": "《考勤管理制度》", "url": "/kb?q=考勤",
     "answer": None},  # 动态读取 attendance_rule
    {"key": "annual", "kw": ["年假", "年休假", "带薪假"], "title": "《休假管理制度》第三章 年休假",
     "url": "/kb?q=年休假",
     "answer": "年休假按《职工带薪年休假条例》执行：累计工作满 1 年不满 10 年 5 天，满 10 年不满 20 年 10 天，满 20 年 15 天；"
               "公司在法定基础上按司龄每满 1 年增加 1 天（上限 15 天）。年假需提前 3 个工作日在企微提交请假审批，当年有效。"},
    {"key": "leave", "kw": ["请假", "事假", "病假", "婚假", "产假", "陪产", "丧假", "调休"],
     "title": "《休假管理制度》第二章 请假流程", "url": "/kb?q=请假",
     "answer": "请假须提前通过企微「请假」审批提交：1 天以内由部门负责人审批，1~3 天加签人事，3 天以上需总经理审批；"
               "病假需附二级以上医院证明；婚假 3 天、产假 98 天（广东另加 80 天）、陪产假 15 天、丧假 3 天；"
               "调休以已审批加班时长为限，按小时折算。"},
    {"key": "social", "kw": ["社保", "公积金", "五险", "医保", "养老", "生育险"], "title": "《薪酬福利制度》第五章 社会保险",
     "url": "/kb?q=社会保险",
     "answer": "公司自入职当月起为员工缴纳养老、医疗（含生育）、失业、工伤保险及住房公积金，缴费基数按上年度月平均工资"
               "并在当地上下限内核定；每月 15 日前增减员，社保卡与公积金账户问题可联系人事部邓欣。"},
    {"key": "overtime", "kw": ["加班", "加班费"], "title": "《考勤管理制度》第四章 加班",
     "url": "/kb?q=加班",
     "answer": "加班需事先在企微提交加班审批；工作日加班优先安排调休，休息日加班可调休或按 200% 计发，法定节假日加班按 300% 计发。"},
    {"key": "salary", "kw": ["工资", "发薪", "薪资", "工资条"], "title": "《薪酬福利制度》第二章 薪资发放",
     "url": "/kb?q=工资",
     "answer": "工资每月 10 日发放上月工资，遇节假日提前；工资条通过企微推送，对薪资有疑问可在 5 个工作日内向人事部反馈。"},
    {"key": "resign", "kw": ["离职", "辞职", "交接", "离职证明"], "title": "《员工入离职管理办法》",
     "url": "/kb?q=离职",
     "answer": "正式员工离职需提前 30 日书面提出（试用期提前 3 日），完成工作交接清单签字后办理离职手续，离职当日开具离职证明。"},
]


def _kb_search(db: Session, q: str) -> list[dict]:
    try:
        from app.services import kb_service  # type: ignore
    except ImportError:
        return []
    try:
        return kb_service.search(db, q, category="制度", limit=3) or []
    except Exception:  # noqa: BLE001
        return []


def fmt(x) -> str:
    d = Decimal(str(x or 0)).normalize()
    return f"{d:f}"


def annual_leave(db: Session, user_id: int, year: int | None = None) -> dict | None:
    e = db.scalar(select(HrEmployee).where(HrEmployee.user_id == user_id))
    if not e:
        return None
    year = year or today().year
    rule = att.get_rule(db)
    hours = db.scalar(select(func.coalesce(func.sum(HrAttendanceRecord.leave_hours), 0))
                      .where(HrAttendanceRecord.user_id == user_id, HrAttendanceRecord.leave_type.like("年假%"),
                             HrAttendanceRecord.work_date.between(date(year, 1, 1), date(year, 12, 31)))) or 0
    used = (Decimal(hours) / Decimal(str(rule["leave_hours_per_day"]))).quantize(Decimal("0.01"))
    total = Decimal(e.annual_leave_days)
    return {"year": year, "total": total, "used": used, "remaining": max(D0, total - used)}


def _my_attendance(db: Session, user_id: int, period: str) -> dict | None:
    rows = att.summary_rows(db, period, user_id)
    return rows[0] if rows else None


def answer(db: Session, user: SysUser, question: str) -> dict:
    q = (question or "").strip()
    if not q:
        return {"answer": "请输入问题，例如「我还有几天年假」「这个月迟到几次」「病假怎么请」。", "sources": [], "personal": False}
    mine = bool(re.search(r"我|本人|自己|我的", q))
    sources: list[dict] = []
    parts: list[str] = []
    personal = False
    t = today()
    cur = f"{t:%Y-%m}"
    # 个性化：年假
    if mine and re.search(r"年假|年休假", q):
        al = annual_leave(db, user.id)
        if al:
            personal = True
            parts.append(f"{user.name}，你 {al['year']} 年共有年假 {fmt(al['total'])} 天，已休 {fmt(al['used'])} 天，"
                         f"剩余 {fmt(al['remaining'])} 天（已休天数按考勤记录中的年假统计）。")
    # 个性化：迟到/考勤
    if mine and re.search(r"迟到|早退|缺卡|旷工|考勤|加班", q):
        want = prev_month(cur) if re.search(r"上个?月", q) else cur
        s = _my_attendance(db, user.id, want)
        if not s and want == cur:
            s, want = _my_attendance(db, user.id, prev_month(cur)), prev_month(cur)
            if s:
                parts.append(f"本月考勤尚未核对，以下为最近一期（{want}）结果：")
        if s:
            personal = True
            parts.append(f"{want} 你迟到 {s['late_times']} 次、早退 {s['early_times']} 次、缺卡 {s['missing_times']} 次、"
                         f"旷工 {s['absent_times']} 次，请假 {fmt(s['leave_days'])} 天，加班 {fmt(s['overtime_hours'])} 小时。")
            excs = att.exception_rows(db, want, user.id)
            if excs:
                parts.append("异常日期：" + "；".join(f"{e['work_date']:%m-%d} {'、'.join(e['types'])}" for e in excs[:6]) +
                             "。如有异议请在企微补卡审批或联系人事。")
        else:
            parts.append("暂未查到你的考勤核对记录。")
    if mine and re.search(r"合同|试用期|转正", q):
        e = db.scalar(select(HrEmployee).where(HrEmployee.user_id == user.id))
        if e:
            personal = True
            parts.append(f"你的劳动合同期限 {e.contract_start_date} 至 {e.contract_end_date}" +
                         (f"，试用期至 {e.probation_end_date}" if e.employ_type == "试用" else "，已转正") + "。")
    # 制度出处：知识库优先，内置 FAQ 兜底
    kb = _kb_search(db, q)
    for k in kb[:3]:
        sources.append({"title": k.get("title"), "url": k.get("url"), "heading": k.get("heading"),
                        "snippet": k.get("snippet"), "from": "kb"})
    faq_hits = [f for f in FAQ if any(k in q for k in f["kw"])]
    if not kb:
        for f in faq_hits[:2]:
            if f["key"] == "attendance":
                rule = att.get_rule(db)
                text = "考勤判定口径：" + "".join(att.rule_text(rule)[:4])
            else:
                text = f["answer"]
            if not personal or f["key"] not in ("annual", "attendance"):
                parts.append(text)
            sources.append({"title": f["title"], "url": f["url"], "snippet": text[:120], "from": "faq"})
    elif not personal:
        parts.append("根据公司制度：" + (kb[0].get("snippet") or ""))
    if not parts:
        parts.append("暂未找到相关制度条款，已为你记录问题，人事部会尽快回复；也可直接联系人事部曹芳/邓欣。")
    return {"answer": "\n".join(parts), "sources": sources, "personal": personal,
            "engine": "kb" if kb else "faq"}


# =============== 概览 ===============
def overview(db: Session) -> dict:
    t = today()
    cur = f"{t:%Y-%m}"
    hc = headcount(db)
    to = turnover(db, cur, 12)
    st = structure(db)
    last = att.latest_import(db, cur) and cur or prev_month(cur)
    exc_cnt = db.scalar(select(func.coalesce(func.sum(HrAttendanceSummary.late_times + HrAttendanceSummary.early_times +
                                                      HrAttendanceSummary.missing_times + HrAttendanceSummary.absent_times), 0))
                        .where(HrAttendanceSummary.period == last)) or 0
    alerts = alert_rows(db)
    funnel = Counter(c.stage for c in db.scalars(select(HrCandidate)).all())
    return {"as_of": t, "kpi": {"active": hc["actual"], "planned": hc["planned"], "fill_rate": hc["rate"],
                                "month_hires": to["rows"][-1]["hires"], "month_leaves": to["rows"][-1]["leaves"],
                                "attendance_period": last, "attendance_exceptions": int(exc_cnt),
                                "alerts": len(alerts), "candidates": sum(funnel.values()),
                                "year_leave_rate": to["year"]["leave_rate"]},
            "structure": st, "turnover": to["rows"], "headcount": hc["rows"], "alerts": alerts[:6]}


def attendance_summary(db: Session, period: str | None = None) -> dict:
    """MCP hr.attendance_summary 入口（转发 attendance_service.attendance_summary）。"""
    return att.attendance_summary(db, period)

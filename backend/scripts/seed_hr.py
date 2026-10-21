"""人事演示数据：考勤规则/列映射字典、上月与本月考勤 Excel 样例（含各类异常）并导入上月、候选人 15 名、示例文书、到期扫描。"""
import io
import random
from datetime import date, timedelta
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font
from sqlalchemy import select

from app.core.timeutil import month_range, prev_month
from app.models import HrCandidate, HrEmployee
from app.services import attendance_service as att
from app.services import dict_service, hr_doc_service, hr_service, recruit_service

SAMPLES = Path(__file__).resolve().parents[1] / "samples"
NO_PUNCH = {"panhui"}          # 花名册在职但考勤表无记录（跨表核对样本）
SKIP = {"admin"}
OT_PEOPLE = {"linna", "dingrui", "xuhao", "tanglei", "weichen", "guojian"}

# 固定边界样本（上月）：(userid, 第 n 个工作日, 上班, 下班, 审批单)
FIXED = [
    ("dingrui", 0, "09:10", "18:05", None),          # 宽限内，不算迟到
    ("dingrui", 1, "09:11", "18:02", None),          # 迟到
    ("weichen", 2, "08:52", "--", None),             # 缺下班卡
    ("shenkang", 3, "11:30", "18:10", None),         # 迟到 150 分钟 → 旷工半天
    ("huangqi", 4, "11:05", "18:03", "请假-事假 2小时（上午）"),  # 请假抵消迟到
    ("linna", 5, "08:55", "21:40", "加班 2.5小时"),   # 加班
    ("pengbo", 6, "--", "--", None),                 # 全天无打卡 → 旷工 1 天
    ("songjia", 7, "09:02", "16:20", None),          # 早退
    ("yangyue", 8, "--", "--", "年假 1天"),           # 全天年假
    ("zhoumin", 9, "08:50", "18:01", "病假 4小时（下午）"),  # 下午病假，未早退判定
]


def _write_excel(db, period: str, end: date, rnd: random.Random, with_fixed: bool = True) -> bytes:
    rule = att.get_rule(db)
    start, _ = month_range(period)
    emps = [e for e in db.scalars(select(HrEmployee)).all() if e.user and e.user.wecom_userid not in SKIP | NO_PUNCH]
    emps.sort(key=lambda e: e.employee_no)
    wds = att.workdays_in(rule, start, end)
    fixed = {(u, wds[i]): (ci, co, ap) for u, i, ci, co, ap in FIXED if i < len(wds)} if with_fixed else {}
    wb = Workbook()
    ws = wb.active
    ws.title = "打卡汇总"
    ws.append([f"{period[:4]}年{period[5:]}月 考勤报表（企业微信导出）"])
    ws["A1"].font = Font(bold=True, size=13)
    ws.append(["姓名", "账号", "部门", "职务", "日期", "上班打卡", "下班打卡", "审批单(请假/加班)"])
    d = start
    while d <= end:
        wd = att.is_workday(rule, d)
        for e in emps:
            u = e.user
            if e.hire_date > d or (e.leave_date and e.leave_date < d):
                continue
            ci, co, ap = _gen_day(rnd, u.wecom_userid, wd)
            if (u.wecom_userid, d) in fixed:
                ci, co, ap = fixed[(u.wecom_userid, d)]
            if not wd and ci == "--" and co == "--":
                continue  # 休息日无打卡不导出
            ws.append([u.name, u.wecom_userid, u.department.name if u.department else "", u.position,
                       f"{d:%Y-%m-%d} 星期{'一二三四五六日'[d.weekday()]}", ci, co, ap or ""])
        d += timedelta(days=1)
    # 打卡人不在花名册（外包临时人员）
    for wd in wds[:3]:
        ws.append(["王强", "wangqiang_tmp", "项目部", "外包施工", f"{wd:%Y-%m-%d}", "08:40", "18:20", ""])
    # 行级错误样本
    ws.append(["何洋", "heyang", "市场部", "市场专员", "2026-13-45", "09:00", "18:00", ""])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _t(h: int, m: int) -> str:
    return f"{h:02d}:{m:02d}"


def _gen_day(rnd: random.Random, uid: str, workday: bool) -> tuple[str, str, str | None]:
    if not workday:
        if uid in OT_PEOPLE and rnd.random() < 0.12:
            return _t(9, rnd.randint(30, 59)), _t(rnd.randint(14, 17), rnd.randint(0, 59)), "加班 休息日"
        return "--", "--", None
    x = rnd.random()
    ci = _t(8, rnd.randint(25, 59)) if rnd.random() < 0.8 else _t(9, rnd.randint(0, 8))
    co = _t(18, rnd.randint(0, 40))
    ap = None
    if x < 0.035:
        ci = _t(9, rnd.randint(12, 45))                              # 迟到
    elif x < 0.045:
        ci = _t(rnd.choice([11, 12]), rnd.randint(0, 59))             # 严重迟到 → 旷工半天
    elif x < 0.06:
        ci, co = (ci, "--") if rnd.random() < 0.5 else ("--", co)    # 缺卡
    elif x < 0.07:
        co = _t(17, rnd.randint(5, 50))                              # 早退
    elif x < 0.085:
        ap = rnd.choice(["请假-事假 4小时（上午）", "请假-病假 1天", "年假 1天", "调休 4小时（下午）"])
        if "1天" in ap:
            ci, co = "--", "--"
        elif "上午" in ap:
            ci = _t(13, rnd.randint(20, 35))
        else:
            co = _t(13, rnd.randint(0, 20))
    elif x < 0.088:
        ci, co = "--", "--"                                          # 旷工
    if uid in OT_PEOPLE and rnd.random() < 0.3 and co != "--":
        co = _t(rnd.randint(19, 21), rnd.randint(0, 59))
        ap = ap or "加班"
    return ci, co, ap


CANDIDATES = [
    # 姓名, 岗位, 渠道, 学历, 年限, 技能, 阶段
    ("赵一鸣", "项目经理", "BOSS直聘", "本科", 8, ["项目管理", "一建", "弱电", "系统集成"], "offer"),
    ("钱可欣", "项目经理", "猎聘", "硕士", 6, ["PMP", "项目管理", "智慧校园"], "interview"),
    ("孙立伟", "项目经理", "内推", "本科", 4, ["二建", "施工管理", "安防"], "interview"),
    ("李嘉豪", "项目经理", "智联招聘", "大专", 3, ["施工管理"], "rejected"),
    ("周晓彤", "项目经理", "BOSS直聘", "本科", 5, ["项目管理", "CAD"], "screen_pass"),
    ("吴思远", "售前工程师", "猎聘", "本科", 5, ["售前", "方案", "标书", "智慧校园", "PPT"], "hired"),
    ("郑雨桐", "售前工程师", "BOSS直聘", "硕士", 3, ["方案", "Visio", "数字校园"], "interview"),
    ("王子涵", "售前工程师", "校园招聘", "本科", 1, ["PPT", "Excel"], "rejected"),
    ("冯佳琪", "售前工程师", "内推", "本科", 4, ["售前", "安防", "海康", "标书"], "screen_pass"),
    ("陈浩然", "售前工程师", "智联招聘", "本科", 2, ["方案"], "resume"),
    ("褚明轩", "项目工程师", "BOSS直聘", "大专", 4, ["网络", "交换机", "视频监控", "门禁"], "screen_pass"),
    ("卫子豪", "项目工程师", "58同城", "大专", 2, ["综合布线", "弱电"], "resume"),
    ("蒋文博", "项目工程师", "内推", "本科", 3, ["网络", "Linux", "服务器", "海康"], "offer"),
    ("沈若曦", "项目工程师", "校园招聘", "本科", 0, ["网络"], "resume"),
    ("韩卓然", "项目工程师", "BOSS直聘", "高中", 6, ["弱电", "综合布线", "门禁"], "rejected"),
]
REQS = {"项目经理": {"keywords": ["项目管理", "一建", "二建", "PMP", "弱电", "系统集成"], "min_education": "本科", "min_years": 5},
        "售前工程师": {"keywords": ["售前", "方案", "标书", "智慧校园", "PPT"], "min_education": "本科", "min_years": 3},
        "项目工程师": {"keywords": ["网络", "交换机", "视频监控", "门禁", "弱电"], "min_education": "大专", "min_years": 2}}


def seed(db, ctx):
    T = ctx.T
    U = ctx.users
    dict_service.put(db, "attendance_rule", "default", dict(att.DEFAULT_RULE), "考勤判定规则（与《考勤管理制度》一致，可在线修改）")
    dict_service.put(db, "attendance_template", "default", dict(att.DEFAULT_TEMPLATE), "企微考勤导出列映射（列名别名）")
    rule = dict_service.get(db, "alert_rule", "hr") or {}
    dict_service.put(db, "alert_rule", "hr", {**hr_service.DEFAULT_ALERT, **rule})
    db.flush()

    # ---- 考勤 Excel 样例：上月（全月）+ 本月（至昨日）；导入上月 ----
    SAMPLES.mkdir(parents=True, exist_ok=True)
    rnd = random.Random(20261020)
    cur = f"{T:%Y-%m}"
    prev = prev_month(cur)
    prev_bytes = _write_excel(db, prev, month_range(prev)[1], rnd)
    (SAMPLES / f"attendance_{prev}.xlsx").write_bytes(prev_bytes)
    if T.day > 1:
        cur_bytes = _write_excel(db, cur, T - timedelta(days=1), rnd, with_fixed=False)
        (SAMPLES / f"attendance_{cur}.xlsx").write_bytes(cur_bytes)
    att.import_excel(db, prev_bytes, f"attendance_{prev}.xlsx", U["caofang"].id)

    # ---- 候选人 ----
    for name, pos, ch, edu, yrs, skills, stage in CANDIDATES:
        req = REQS[pos]
        sc, reason = recruit_service.score({"education": edu, "years": yrs, "skills": skills, "text": " ".join(skills)}, req)
        ev = None
        if stage in ("interview", "offer", "hired") or (stage == "rejected" and yrs >= 5):
            base = 3 + (sc >= 75) + (stage in ("offer", "hired"))
            ev = recruit_service.evaluate({"professional": min(5, base), "communication": min(5, base),
                                           "teamwork": 4, "learning": min(5, base), "culture": 4},
                                          "曹芳", "沟通顺畅，项目经历与岗位匹配" if base >= 4 else "专业深度有待考察")
            ev["date"] = (T - timedelta(days=rnd.randint(2, 20))).isoformat()
        db.add(HrCandidate(name=name, position=pos, channel=ch, education=edu, years=yrs, skills=skills, stage=stage,
                           screen_score=sc, screen_reason=reason, interview_eval=ev))
    db.flush()

    # ---- 示例文书 ----
    hr_doc_service.onboarding_pack(db, {"work_location": "广州市天河区（公司总部）", "probation_months": 3, "contract_years": 3,
                                        "salary": 9000, "probation_salary": 7200, "report_date": (T - timedelta(days=81)).isoformat(), "non_compete": "否",
                                        "overtime_clause": "加班须经企微审批，优先安排调休"},
                                   U["zhuyi"].id, U["caofang"].id)
    hr_doc_service.generate(db, "leave_cert", {"remark": ""}, U["tianye"].id, U["dengxin"].id)
    hr_doc_service.generate(db, "holiday_notice", {"holiday": "国庆节、中秋节", "start_date": "2026-10-01", "end_date": "2026-10-07",
                                                   "makeup_days": "10 月 10 日（星期六）"}, None, U["dengxin"].id)
    hr_doc_service.generate(db, "offer", {"name": "赵一鸣", "dept": "项目部", "position": "项目经理", "salary": 16000,
                                          "probation_months": 3, "contract_years": 3, "report_date": (T + timedelta(days=14)).isoformat(),
                                          "work_location": "广州市天河区", "valid_until": (T + timedelta(days=5)).isoformat()},
                            None, U["zhuyi"].id)

    # ---- 到期扫描 ----
    hr_service.scan_alerts(db, notify=True)
    db.flush()

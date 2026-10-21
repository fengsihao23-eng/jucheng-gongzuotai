"""考勤自动核对（PRD 7.5 / 4.2.7）：企微考勤导出 Excel 解析 → 逐日判定 → 月度汇总 → 汇总表导出 → 跨表核对。

判定严格按 sys_dict(attendance_rule.default) 配置；列映射读 sys_dict(attendance_template.default)。
HrAttendanceRecord 保存原始打卡与请假信息，规则修改后可 recheck 重新判定，结果随规则变化。
"""
import io
import re
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.response import BizError
from app.core.storage import file_brief, save_bytes
from app.core.timeutil import month_range, today
from app.models import (HrAttendanceImport, HrAttendanceRecord, HrAttendanceSummary, HrEmployee, SyncWecomApproval,
                        SysFile, SysUser)
from app.services import dict_service

D0 = Decimal("0")

DEFAULT_RULE = {
    "work_start": "09:00",
    "work_end": "18:00",
    "late_grace_minutes": 10,         # 晚于上班时间 ≤ 宽限 不算迟到
    "early_grace_minutes": 0,         # 早于下班时间 ≤ 宽限 不算早退
    "absent_threshold_minutes": 120,  # 迟到/早退超过该分钟数记旷工半天
    "no_punch_absent_full_day": True,  # 工作日全天无打卡且无请假 → 旷工 1 天
    "overtime_start": "19:00",        # 下班打卡晚于该时间起算加班
    "overtime_min_minutes": 30,       # 不足该分钟数不计加班
    "overtime_unit_minutes": 30,      # 加班按该粒度向下取整
    "weekend_overtime": True,         # 休息日打卡全程计加班
    "leave_hours_per_day": 8,
    "workdays": [1, 2, 3, 4, 5],      # ISO 星期
    "holidays": ["2026-09-25", "2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04", "2026-10-05",
                 "2026-10-06", "2026-10-07"],
    "makeup_workdays": ["2026-09-20", "2026-10-10"],
}

DEFAULT_TEMPLATE = {
    "header_scan_rows": 6,
    "columns": {
        "name": ["姓名", "名字"],
        "account": ["账号", "UserId", "userid", "工号", "企微账号"],
        "dept": ["部门", "所属部门"],
        "date": ["日期", "考勤日期", "打卡日期"],
        "check_in": ["上班打卡", "上班1打卡时间", "签到时间", "上班时间"],
        "check_out": ["下班打卡", "下班1打卡时间", "签退时间", "下班时间"],
        "approval": ["审批单", "假勤申请", "关联审批单", "审批单(请假/加班)", "备注"],
    },
}

RULE_LABELS = {
    "work_start": "上班时间", "work_end": "下班时间", "late_grace_minutes": "迟到宽限（分钟）",
    "early_grace_minutes": "早退宽限（分钟）", "absent_threshold_minutes": "迟到/早退超过该分钟记旷工半天",
    "no_punch_absent_full_day": "工作日全天无打卡且无请假记旷工 1 天", "overtime_start": "加班起算时间",
    "overtime_min_minutes": "加班最小计量（分钟）", "overtime_unit_minutes": "加班计量粒度（分钟）",
    "weekend_overtime": "休息日打卡计加班", "leave_hours_per_day": "请假每天折算小时",
    "workdays": "工作日（ISO 星期）", "holidays": "法定节假日", "makeup_workdays": "调休上班日",
}

LEAVE_TYPES = ["陪产假", "育儿假", "年假", "事假", "病假", "调休", "婚假", "产假", "丧假"]
EXC_ORDER = ["迟到", "早退", "缺卡", "旷工", "请假", "加班"]


# ---------------- 配置 ----------------
def get_rule(db: Session) -> dict:
    return {**DEFAULT_RULE, **(dict_service.get(db, "attendance_rule", "default") or {})}


def put_rule(db: Session, patch: dict) -> dict:
    rule = get_rule(db)
    for k, v in patch.items():
        if k not in DEFAULT_RULE:
            raise BizError(40001, f"未知规则项 {k}")
        if k in ("work_start", "work_end", "overtime_start"):
            _hm(v)
        rule[k] = v
    if _hm(rule["work_end"]) <= _hm(rule["work_start"]):
        raise BizError(40001, "下班时间必须晚于上班时间")
    dict_service.put(db, "attendance_rule", "default", rule, "考勤判定规则（人事制度）")
    return rule


def get_template(db: Session) -> dict:
    t = dict_service.get(db, "attendance_template", "default") or {}
    cols = {**DEFAULT_TEMPLATE["columns"], **(t.get("columns") or {})}
    return {**DEFAULT_TEMPLATE, **t, "columns": cols}


def rule_text(rule: dict) -> list[str]:
    return [
        f"上班 {rule['work_start']}，下班 {rule['work_end']}；工作日为 ISO 星期 {rule['workdays']}，法定节假日与调休按配置。",
        f"迟到：上班打卡晚于 {rule['work_start']} 超过 {rule['late_grace_minutes']} 分钟（{rule['late_grace_minutes']} 分钟内不算）。",
        f"早退：下班打卡早于 {rule['work_end']} 超过 {rule['early_grace_minutes']} 分钟。",
        f"旷工：迟到或早退超过 {rule['absent_threshold_minutes']} 分钟记旷工半天（不再计迟到/早退）；"
        + ("工作日全天无打卡且无请假记旷工 1 天。" if rule["no_punch_absent_full_day"] else "全天无打卡记缺卡 2 次。"),
        "缺卡：工作日缺少上班或下班打卡（一次缺卡计 1 次）。",
        f"请假：以审批单为准，按 {rule['leave_hours_per_day']} 小时/天折算；上午请假顺延上班判定时间，下午请假提前下班判定时间。",
        f"加班：下班打卡晚于 {rule['overtime_start']} 起算，不足 {rule['overtime_min_minutes']} 分钟不计，"
        f"按 {rule['overtime_unit_minutes']} 分钟向下取整；" + ("休息日打卡全程计加班。" if rule["weekend_overtime"] else "休息日不计加班。"),
    ]


# ---------------- 判定 ----------------
def _hm(s: str) -> int:
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", str(s).strip())
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise BizError(40001, f"时间格式应为 HH:MM：{s}")
    return int(m.group(1)) * 60 + int(m.group(2))


def _mins(dt: datetime | None, base: date) -> int | None:
    if dt is None:
        return None
    return (dt.date() - base).days * 1440 + dt.hour * 60 + dt.minute


def is_workday(rule: dict, d: date) -> bool:
    ds = d.isoformat()
    if ds in rule.get("makeup_workdays", []):
        return True
    if ds in rule.get("holidays", []):
        return False
    return d.isoweekday() in rule.get("workdays", [1, 2, 3, 4, 5])


def workdays_in(rule: dict, start: date, end: date) -> list[date]:
    out, d = [], start
    while d <= end:
        if is_workday(rule, d):
            out.append(d)
        d += timedelta(days=1)
    return out


def evaluate_day(rule: dict, work_date: date, check_in: datetime | None, check_out: datetime | None,
                 leave_type: str | None = None, leave_hours: Decimal | float = 0) -> dict:
    """单日判定。返回 {types, late_minutes, early_minutes, overtime_hours, absent_days, missing, workday}。"""
    leave_hours = Decimal(str(leave_hours or 0))
    workday = is_workday(rule, work_date)
    types: list[str] = []
    late = early = missing = 0
    absent = D0
    start, end, ot_start = _hm(rule["work_start"]), _hm(rule["work_end"]), _hm(rule["overtime_start"])
    day_hours = Decimal(str(rule.get("leave_hours_per_day", 8)))
    in_m, out_m = _mins(check_in, work_date), _mins(check_out, work_date)
    if leave_hours > 0:
        types.append("请假")

    if workday:
        full_leave = leave_hours >= day_hours
        if not full_leave:
            shift = int(leave_hours * 60)
            pm = bool(leave_type and "下午" in leave_type)
            eff_start = start + (0 if pm else shift)
            eff_end = end - (shift if pm else 0)
            if in_m is None and out_m is None:
                if rule.get("no_punch_absent_full_day", True):
                    absent = Decimal("0.5") if leave_hours > 0 else Decimal("1")
                    types.append("旷工")
                else:
                    missing = 2
                    types.append("缺卡")
            else:
                if in_m is None or out_m is None:
                    missing = 1
                    types.append("缺卡")
                if in_m is not None:
                    diff = in_m - eff_start
                    if diff > rule["absent_threshold_minutes"]:
                        absent += Decimal("0.5")
                        late = diff
                    elif diff > rule["late_grace_minutes"]:
                        late = diff
                        types.append("迟到")
                if out_m is not None:
                    diff = eff_end - out_m
                    if diff > rule["absent_threshold_minutes"]:
                        absent += Decimal("0.5")
                        early = diff
                    elif diff > rule["early_grace_minutes"]:
                        early = diff
                        types.append("早退")
                if absent > 0:
                    types.append("旷工")
        ot_minutes = (out_m - ot_start) if out_m is not None and out_m > ot_start else 0
    else:
        ot_minutes = (out_m - in_m) if rule.get("weekend_overtime", True) and in_m is not None and out_m is not None else 0
    unit = max(1, int(rule.get("overtime_unit_minutes", 30)))
    if ot_minutes < int(rule.get("overtime_min_minutes", 30)):
        ot_minutes = 0
    ot_hours = (Decimal(ot_minutes // unit * unit) / Decimal(60)).quantize(Decimal("0.01"), ROUND_HALF_UP)
    if ot_hours > 0:
        types.append("加班")
    types = [t for t in EXC_ORDER if t in types]
    return {"types": types, "late_minutes": late, "early_minutes": early, "overtime_hours": ot_hours,
            "absent_days": min(absent, Decimal("1")), "missing": missing, "workday": workday}


def apply_eval(rule: dict, rec: HrAttendanceRecord) -> dict:
    r = evaluate_day(rule, rec.work_date, rec.check_in, rec.check_out, rec.leave_type, rec.leave_hours)
    rec.exception_type = ",".join(t for t in r["types"]) or None
    rec.late_minutes, rec.early_minutes, rec.overtime_hours = r["late_minutes"], r["early_minutes"], r["overtime_hours"]
    return r


# ---------------- 解析 ----------------
_DATE_RE = re.compile(r"(\d{4})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})")
_TIME_RE = re.compile(r"(次日)?\s*(\d{1,2}):(\d{2})")


def _parse_date(v) -> date | None:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    m = _DATE_RE.search(str(v or ""))
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    return None


def _parse_time(v, d: date) -> tuple[datetime | None, bool]:
    """返回 (时间, 是否可解析)。空/--/未打卡 → (None, True)。"""
    if v is None:
        return None, True
    if isinstance(v, datetime):
        return v.replace(tzinfo=None, second=0, microsecond=0), True
    if isinstance(v, time):
        return datetime.combine(d, v.replace(second=0, microsecond=0)), True
    s = str(v).strip()
    if s in ("", "-", "--", "未打卡", "缺卡", "无"):
        return None, True
    m = _TIME_RE.search(s)
    if not m:
        return None, False
    h, mi = int(m.group(2)), int(m.group(3))
    if h > 23 or mi > 59:
        return None, False
    base = d + timedelta(days=1) if m.group(1) else d
    return datetime.combine(base, time(h, mi)), True


def parse_approval(text: str | None, rule: dict) -> tuple[str | None, Decimal]:
    """审批单文本 → (请假类型, 请假小时)。如“请假-事假 2小时(上午)”、“年假 1天”。"""
    if not text:
        return None, D0
    s = str(text)
    if "假" not in s and "调休" not in s:
        return None, D0
    ltype = next((t for t in LEAVE_TYPES if t in s), "事假")
    m = re.search(r"(\d+(?:\.\d+)?)\s*(小时|h|H|天|日|d)", s)
    if m:
        n = Decimal(m.group(1))
        hours = n * Decimal(str(rule.get("leave_hours_per_day", 8))) if m.group(2) in ("天", "日", "d") else n
    else:
        hours = Decimal(str(rule.get("leave_hours_per_day", 8)))
    if "下午" in s:
        ltype += "/下午"
    return ltype, hours


def _find_header(ws, tpl: dict) -> tuple[int, dict[str, int]]:
    cols = tpl["columns"]
    for r_idx, row in enumerate(ws.iter_rows(min_row=1, max_row=int(tpl.get("header_scan_rows", 6)), values_only=True),
                                start=1):
        cells = [str(c).strip() if c is not None else "" for c in row]
        mapping = {}
        for key, aliases in cols.items():
            for i, c in enumerate(cells):
                if c and any(c == a or c.lower() == a.lower() for a in aliases):
                    mapping[key] = i
                    break
        if "date" in mapping and ("name" in mapping or "account" in mapping) and \
                ("check_in" in mapping or "check_out" in mapping):
            return r_idx, mapping
    raise BizError(42201, "未识别到表头：需包含 姓名/账号、日期、上班打卡/下班打卡 列（列名可在 attendance_template 配置）")


def parse_excel(db: Session, content: bytes) -> dict:
    """返回 {rows:[{row, user, date, check_in, check_out, approval}], errors, unknown:[{row,name,account}]}。"""
    try:
        wb = load_workbook(io.BytesIO(content), data_only=True, read_only=True)
    except Exception as exc:  # noqa: BLE001
        raise BizError(42201, f"文件无法解析为 Excel：{exc}") from exc
    ws = wb.worksheets[0]
    tpl = get_template(db)
    hdr, mp = _find_header(ws, tpl)
    users = db.scalars(select(SysUser).where(SysUser.is_deleted.is_(False))).all()
    by_acct = {u.wecom_userid.lower(): u for u in users}
    by_name: dict[str, list[SysUser]] = defaultdict(list)
    for u in users:
        by_name[u.name].append(u)
    out, errors, unknown, seen = [], [], [], {}

    def cell(row, key):
        i = mp.get(key)
        return row[i] if i is not None and i < len(row) else None

    for r_idx, row in enumerate(ws.iter_rows(min_row=hdr + 1, values_only=True), start=hdr + 1):
        if not row or all(c in (None, "") for c in row):
            continue
        name = str(cell(row, "name") or "").strip()
        acct = str(cell(row, "account") or "").strip()
        d = _parse_date(cell(row, "date"))
        if not name and not acct:
            errors.append({"row": r_idx, "kind": "row_error", "message": "缺少姓名与账号"})
            continue
        if not d:
            errors.append({"row": r_idx, "kind": "row_error", "name": name, "message": f"日期无法识别：{cell(row, 'date')}"})
            continue
        u = by_acct.get(acct.lower()) if acct else None
        if not u and name:
            cands = by_name.get(name, [])
            u = cands[0] if len(cands) == 1 else None
        if not u:
            unknown.append({"row": r_idx, "name": name, "account": acct, "date": d.isoformat()})
            continue
        ci, ok1 = _parse_time(cell(row, "check_in"), d)
        co, ok2 = _parse_time(cell(row, "check_out"), d)
        if not (ok1 and ok2):
            errors.append({"row": r_idx, "kind": "row_error", "name": u.name, "date": d.isoformat(),
                           "message": f"打卡时间无法识别：{cell(row, 'check_in')} / {cell(row, 'check_out')}"})
            continue
        if ci and co and co < ci:
            ci, co = co, ci
        key = (u.id, d)
        if key in seen:
            errors.append({"row": r_idx, "kind": "row_error", "name": u.name, "date": d.isoformat(),
                           "message": f"与第 {seen[key]} 行重复（同一人同一天），已忽略"})
            continue
        seen[key] = r_idx
        out.append({"row": r_idx, "user": u, "date": d, "check_in": ci, "check_out": co,
                    "approval": str(cell(row, "approval") or "").strip() or None})
    wb.close()
    return {"rows": out, "errors": errors, "unknown": unknown}


# ---------------- 导入 / 重算 ----------------
def import_excel(db: Session, content: bytes, filename: str, uploader_id: int | None = None,
                 period: str | None = None) -> HrAttendanceImport:
    parsed = parse_excel(db, content)
    rows = parsed["rows"]
    if not rows:
        raise BizError(42201, "文件中没有可识别的考勤行", {"errors": parsed["errors"][:50]})
    period = period or Counter(f"{r['date']:%Y-%m}" for r in rows).most_common(1)[0][0]
    errors = list(parsed["errors"])
    in_period = []
    for r in rows:
        if f"{r['date']:%Y-%m}" != period:
            errors.append({"row": r["row"], "kind": "row_error", "name": r["user"].name, "date": r["date"].isoformat(),
                           "message": f"日期不属于考勤期间 {period}，已忽略"})
        else:
            in_period.append(r)
    rule = get_rule(db)
    f = save_bytes(db, data=content, filename=filename, category="attendance",
                   key=f"attendance/{period}/{filename}", uploader_id=uploader_id)
    start, end = month_range(period)
    db.execute(delete(HrAttendanceRecord).where(HrAttendanceRecord.work_date.between(start, end)))
    imp = HrAttendanceImport(period=period, file_id=f.id, filename=filename, total_rows=len(in_period),
                             uploader_id=uploader_id, errors=[])
    db.add(imp)
    db.flush()
    exc = 0
    for r in in_period:
        ltype, lhours = parse_approval(r["approval"], rule)
        rec = HrAttendanceRecord(user_id=r["user"].id, work_date=r["date"], check_in=r["check_in"],
                                 check_out=r["check_out"], leave_type=ltype, leave_hours=lhours, import_id=imp.id,
                                 source_file=filename, source_row=r["row"])
        res = apply_eval(rule, rec)
        if set(res["types"]) - {"请假", "加班"}:
            exc += 1
        db.add(rec)
    db.flush()
    cross = crosscheck(db, period, unknown=parsed["unknown"], approvals={(r["user"].id, r["date"]): r["approval"]
                                                                          for r in in_period})
    imp.exception_rows = exc
    imp.errors = errors + cross
    summarize(db, period)
    export_summary(db, period, uploader_id)
    db.flush()
    return imp


def recheck(db: Session, period: str) -> dict:
    """规则修改后按当前规则重新判定本期全部明细并重建汇总/汇总表。"""
    rule = get_rule(db)
    start, end = month_range(period)
    recs = db.scalars(select(HrAttendanceRecord).where(HrAttendanceRecord.work_date.between(start, end))).all()
    if not recs:
        raise BizError(40401, f"{period} 尚无考勤明细")
    exc = 0
    for rec in recs:
        if set(apply_eval(rule, rec)["types"]) - {"请假", "加班"}:
            exc += 1
    imp = latest_import(db, period)
    if imp:
        imp.exception_rows = exc
    db.flush()
    summarize(db, period)
    f = export_summary(db, period)
    return {"period": period, "records": len(recs), "exception_rows": exc, "file": file_brief(f)}


def latest_import(db: Session, period: str) -> HrAttendanceImport | None:
    return db.scalar(select(HrAttendanceImport).where(HrAttendanceImport.period == period)
                     .order_by(HrAttendanceImport.id.desc()).limit(1))


# ---------------- 汇总 ----------------
def _period_end(period: str) -> date:
    start, end = month_range(period)
    t = today()
    return min(end, t - timedelta(days=1)) if start <= t <= end else end


def summarize(db: Session, period: str) -> list[HrAttendanceSummary]:
    rule = get_rule(db)
    start, end = month_range(period)
    sched = len(workdays_in(rule, start, _period_end(period)))
    recs = db.scalars(select(HrAttendanceRecord).where(HrAttendanceRecord.work_date.between(start, end))).all()
    agg: dict[int, dict] = defaultdict(lambda: {"late": 0, "early": 0, "missing": 0, "absent": 0, "ot": D0,
                                                "leave_h": D0})
    for rec in recs:
        r = evaluate_day(rule, rec.work_date, rec.check_in, rec.check_out, rec.leave_type, rec.leave_hours)
        a = agg[rec.user_id]
        a["late"] += "迟到" in r["types"]
        a["early"] += "早退" in r["types"]
        a["missing"] += r["missing"]
        a["absent"] += "旷工" in r["types"]
        a["ot"] += r["overtime_hours"]
        a["leave_h"] += Decimal(str(rec.leave_hours or 0))
    db.execute(delete(HrAttendanceSummary).where(HrAttendanceSummary.period == period))
    day_h = Decimal(str(rule.get("leave_hours_per_day", 8)))
    rows = []
    for uid, a in agg.items():
        s = HrAttendanceSummary(user_id=uid, period=period, work_days=sched, late_times=a["late"],
                                early_times=a["early"], missing_times=a["missing"], absent_times=a["absent"],
                                overtime_hours=a["ot"], leave_days=(a["leave_h"] / day_h).quantize(Decimal("0.01")))
        db.add(s)
        rows.append(s)
    db.flush()
    return rows


def summary_rows(db: Session, period: str, user_id: int | None = None) -> list[dict]:
    rule = get_rule(db)
    start, end = month_range(period)
    q = select(HrAttendanceSummary).where(HrAttendanceSummary.period == period)
    if user_id:
        q = q.where(HrAttendanceSummary.user_id == user_id)
    sums = db.scalars(q).all()
    users = {u.id: u for u in db.scalars(select(SysUser).where(SysUser.id.in_([s.user_id for s in sums])))}
    recs = db.scalars(select(HrAttendanceRecord).where(HrAttendanceRecord.work_date.between(start, end))).all()
    actual, absent_days = Counter(), defaultdict(lambda: D0)
    for r in recs:
        if r.check_in or r.check_out:
            actual[r.user_id] += 1
        if r.exception_type and "旷工" in r.exception_type:
            absent_days[r.user_id] += evaluate_day(rule, r.work_date, r.check_in, r.check_out, r.leave_type,
                                                   r.leave_hours)["absent_days"]
    out = []
    for s in sums:
        u = users.get(s.user_id)
        out.append({"user_id": s.user_id, "name": u.name if u else "", "userid": u.wecom_userid if u else "",
                    "dept_code": u.dept_code if u else None, "dept_name": u.department.name if u and u.department else "",
                    "period": s.period, "work_days": s.work_days, "actual_days": actual[s.user_id],
                    "late_times": s.late_times, "early_times": s.early_times, "missing_times": s.missing_times,
                    "absent_times": s.absent_times, "absent_days": absent_days[s.user_id],
                    "overtime_hours": s.overtime_hours, "leave_days": s.leave_days,
                    "abnormal": s.late_times + s.early_times + s.missing_times + s.absent_times})
    out.sort(key=lambda x: (x["dept_code"] or "", -x["abnormal"], x["name"]))
    return out


def exception_rows(db: Session, period: str, user_id: int | None = None, types: list[str] | None = None,
                   include_info: bool = False) -> list[dict]:
    """异常清单（默认只含迟到/早退/缺卡/旷工；include_info=True 时含请假/加班）。"""
    start, end = month_range(period)
    q = select(HrAttendanceRecord).where(HrAttendanceRecord.work_date.between(start, end),
                                         HrAttendanceRecord.exception_type.is_not(None))
    if user_id:
        q = q.where(HrAttendanceRecord.user_id == user_id)
    recs = db.scalars(q.order_by(HrAttendanceRecord.work_date, HrAttendanceRecord.user_id)).all()
    users = {u.id: u for u in db.scalars(select(SysUser).where(SysUser.id.in_({r.user_id for r in recs})))}
    out = []
    for r in recs:
        ts = r.exception_type.split(",")
        real = [t for t in ts if t not in ("请假", "加班")]
        if not include_info and not real:
            continue
        if types and not set(types) & set(ts):
            continue
        u = users.get(r.user_id)
        out.append({"id": r.id, "user_id": r.user_id, "name": u.name if u else "",
                    "dept_name": u.department.name if u and u.department else "", "work_date": r.work_date,
                    "weekday": "一二三四五六日"[r.work_date.weekday()],
                    "check_in": r.check_in.strftime("%H:%M") if r.check_in else None,
                    "check_out": r.check_out.strftime("%H:%M") if r.check_out else None,
                    "types": ts, "late_minutes": r.late_minutes, "early_minutes": r.early_minutes,
                    "overtime_hours": r.overtime_hours, "leave_type": r.leave_type, "leave_hours": r.leave_hours,
                    "source_row": r.source_row})
    return out


# ---------------- 跨表核对 ----------------
def active_employees(db: Session, on: date, *, since: date | None = None) -> list[HrEmployee]:
    """on 当日在职（或 since~on 期间任意一天在职）的员工。"""
    since = since or on
    emps = db.scalars(select(HrEmployee)).all()
    return [e for e in emps if e.hire_date <= on and (e.leave_date is None or e.leave_date >= since)
            and e.user and not e.user.is_deleted]


def crosscheck(db: Session, period: str, unknown: list[dict] | None = None,
               approvals: dict | None = None) -> list[dict]:
    """考勤 × 花名册 × 请假审批 比对。返回问题项 [{kind, name, message, ...}]。"""
    rule = get_rule(db)
    start, end = month_range(period)
    pend = _period_end(period)
    recs = db.scalars(select(HrAttendanceRecord).where(HrAttendanceRecord.work_date.between(start, end))).all()
    by_user = defaultdict(list)
    for r in recs:
        by_user[r.user_id].append(r)
    out: list[dict] = []
    emps = active_employees(db, pend, since=start)
    emp_by_uid = {e.user_id: e for e in emps}
    for e in emps:
        u = e.user
        if u.dept_code == "ADMIN":
            continue
        need = [d for d in workdays_in(rule, max(start, e.hire_date), min(pend, e.leave_date or pend))]
        if need and not by_user.get(e.user_id):
            out.append({"kind": "no_punch", "user_id": u.id, "name": u.name, "dept_name": u.department.name if u.department else "",
                        "message": f"{u.name}：花名册在职，但本期 {len(need)} 个工作日均无考勤记录"})
    grouped: dict[tuple, list] = defaultdict(list)
    for x in unknown or []:
        grouped[(x.get("name") or "", x.get("account") or "")].append(x)
    for (name, acct), xs in grouped.items():
        rows_ = [x.get("row") for x in xs]
        out.append({"kind": "not_in_roster", "name": name, "account": acct, "row": rows_[0], "rows": rows_,
                    "message": f"打卡人「{name or acct}」不在员工花名册（{len(xs)} 条打卡，第 {'、'.join(map(str, rows_[:5]))} 行）"})
    # 离职后仍有打卡
    all_emps = {e.user_id: e for e in db.scalars(select(HrEmployee)).all()}
    for uid, rs in by_user.items():
        e = all_emps.get(uid)
        if e and e.leave_date:
            late = [r for r in rs if r.work_date > e.leave_date and (r.check_in or r.check_out)]
            if late:
                out.append({"kind": "after_leave", "user_id": uid, "name": e.user.name,
                            "message": f"已于 {e.leave_date} 离职，之后仍有 {len(late)} 天打卡记录"})
        if e is None:
            out.append({"kind": "no_profile", "user_id": uid, "message": "有打卡但无人事档案"})
    # 请假审批 × 打卡：全天请假却全天正常打卡
    for uid, rs in by_user.items():
        for r in rs:
            if r.leave_hours and r.leave_hours >= Decimal(str(rule["leave_hours_per_day"])) and r.check_in and r.check_out:
                u = emp_by_uid.get(uid) or all_emps.get(uid)
                out.append({"kind": "leave_conflict", "user_id": uid, "name": u.user.name if u else "",
                            "date": r.work_date.isoformat(),
                            "message": f"{r.work_date} 全天{r.leave_type}，但上下班均有打卡，请核实审批单"})
    # 企微请假审批（若已接入 leave 模板）与考勤表审批列比对
    sp = db.scalars(select(SyncWecomApproval).where(SyncWecomApproval.template_key == "leave",
                                                    SyncWecomApproval.status == 2)).all()
    for a in sp:
        det = a.detail or {}
        d = _parse_date(det.get("leave_date") or det.get("开始时间"))
        if not d or not (start <= d <= end):
            continue
        has = any(r.work_date == d and r.leave_hours for r in by_user.get(a.applicant_id, []))
        if not has:
            out.append({"kind": "approval_missing", "user_id": a.applicant_id, "date": d.isoformat(),
                        "message": f"企微请假审批 {a.sp_no} 已通过，但考勤表当日无请假记录"})
    return out


# ---------------- 导出 ----------------
_HDR_FILL = PatternFill("solid", fgColor="1B3275")
_ALERT_FILL = PatternFill("solid", fgColor="F6E7E3")


def _sheet(ws, header: list[str], rows: list[list], widths: list[int] | None = None):
    ws.append(header)
    for c in ws[1]:
        c.font, c.fill, c.alignment = Font(bold=True, color="FFFFFF"), _HDR_FILL, Alignment(horizontal="center")
    for r in rows:
        ws.append(r)
    for i, w in enumerate(widths or [], start=1):
        ws.column_dimensions[ws.cell(1, i).column_letter].width = w
    ws.freeze_panes = "A2"


def export_summary(db: Session, period: str, user_id: int | None = None) -> SysFile:
    rule = get_rule(db)
    rows = summary_rows(db, period)
    excs = exception_rows(db, period, include_info=True)
    imp = latest_import(db, period)
    wb = Workbook()
    ws = wb.active
    ws.title = "考勤汇总"
    _sheet(ws, ["部门", "姓名", "账号", "应出勤(天)", "实际打卡(天)", "迟到(次)", "早退(次)", "缺卡(次)", "旷工(次)",
                "旷工(天)", "请假(天)", "加班(小时)", "异常合计"],
           [[r["dept_name"], r["name"], r["userid"], r["work_days"], r["actual_days"], r["late_times"], r["early_times"],
             r["missing_times"], r["absent_times"], float(r["absent_days"]), float(r["leave_days"]),
             float(r["overtime_hours"]), r["abnormal"]] for r in rows],
           [12, 10, 14, 11, 12, 9, 9, 9, 9, 9, 9, 11, 10])
    for i, r in enumerate(rows, start=2):
        if r["absent_times"] or r["abnormal"] >= 4:
            for c in ws[i]:
                c.fill = _ALERT_FILL
    ws2 = wb.create_sheet("异常明细")
    _sheet(ws2, ["日期", "星期", "部门", "姓名", "上班打卡", "下班打卡", "异常类型", "迟到(分)", "早退(分)", "加班(时)",
                 "请假类型", "请假(时)", "源文件行号"],
           [[e["work_date"].isoformat(), e["weekday"], e["dept_name"], e["name"], e["check_in"] or "—",
             e["check_out"] or "—", "、".join(e["types"]), e["late_minutes"], e["early_minutes"],
             float(e["overtime_hours"]), e["leave_type"] or "", float(e["leave_hours"] or 0), e["source_row"]]
            for e in excs], [12, 6, 12, 10, 10, 10, 16, 9, 9, 9, 10, 9, 10])
    ws3 = wb.create_sheet("跨表核对")
    cross = [x for x in (imp.errors if imp else []) if x.get("kind") != "row_error"]
    _sheet(ws3, ["类型", "姓名/账号", "说明"],
           [[CROSS_LABELS.get(x["kind"], x["kind"]), x.get("name") or x.get("account") or "", x["message"]] for x in cross],
           [16, 14, 70])
    ws4 = wb.create_sheet("规则说明")
    _sheet(ws4, ["规则项", "配置值"], [[RULE_LABELS.get(k, k), str(v)] for k, v in rule.items()], [36, 60])
    ws4.append([])
    for t in rule_text(rule):
        ws4.append(["判定口径", t])
    ws5 = wb.create_sheet("口径说明")
    for line in [
        f"考勤期间：{period}；数据来源：{imp.filename if imp else '—'}（企微考勤导出）",
        "应出勤天数 = 期间内工作日（扣除法定节假日、计入调休上班日）；当月未结束时统计至昨日。",
        "异常合计 = 迟到 + 早退 + 缺卡 + 旷工 次数；请假、加班单列，不计为异常。",
        "判定规则读取 sys_dict(attendance_rule)，人事制度调整只改配置，可在人事中心在线修改并重新核对。",
        "跨表核对：花名册在职但无打卡、打卡人不在花名册、离职后打卡、全天请假仍打卡、企微请假审批未体现。",
        "本表由系统自动生成，供人事核对使用；工资核算以人事复核结果为准。",
    ]:
        ws5.append([line])
    ws5.column_dimensions["A"].width = 110
    buf = io.BytesIO()
    wb.save(buf)
    fname = f"考勤汇总_{period}.xlsx"
    f = save_bytes(db, data=buf.getvalue(), filename=fname, category="report",
                   key=f"report/attendance/{period}/{fname}", uploader_id=user_id)
    for s in db.scalars(select(HrAttendanceSummary).where(HrAttendanceSummary.period == period)).all():
        s.report_file_id = f.id
    db.flush()
    return f


CROSS_LABELS = {"no_punch": "在职无打卡", "not_in_roster": "打卡人不在花名册", "after_leave": "离职后打卡",
                "no_profile": "无人事档案", "leave_conflict": "请假与打卡冲突", "approval_missing": "审批未体现",
                "row_error": "行级错误"}


# ---------------- MCP 复用 ----------------
def attendance_summary(db: Session, period: str | None = None) -> dict:
    """MCP hr.attendance_summary：考勤汇总表 + 异常清单 + 人力分析摘要。"""
    from app.core.timeutil import prev_month
    period = period or prev_month(f"{today():%Y-%m}")
    if not re.fullmatch(r"\d{4}-\d{2}", period or ""):
        raise BizError(40001, "period 格式应为 YYYY-MM")
    imp = latest_import(db, period)
    if not imp:
        raise BizError(40401, f"{period} 尚未导入考勤 Excel，请先在人事中心「考勤核对」上传企微考勤导出文件")
    rows = summary_rows(db, period)
    excs = exception_rows(db, period)
    first = db.scalar(select(HrAttendanceSummary).where(HrAttendanceSummary.period == period).limit(1))
    f = db.get(SysFile, first.report_file_id) if first and first.report_file_id else None
    if f is None:
        f = export_summary(db, period)
    tot = {k: sum(r[k] for r in rows) for k in ("late_times", "early_times", "missing_times", "absent_times")}
    tot["overtime_hours"] = sum((r["overtime_hours"] for r in rows), D0)
    tot["leave_days"] = sum((r["leave_days"] for r in rows), D0)
    top = sorted(rows, key=lambda r: -r["abnormal"])[:5]
    cross = [x for x in imp.errors if x.get("kind") != "row_error"]
    lines = [f"{period} 考勤核对：{len(rows)} 人，迟到 {tot['late_times']} 次、早退 {tot['early_times']} 次、"
             f"缺卡 {tot['missing_times']} 次、旷工 {tot['absent_times']} 次；请假 {tot['leave_days']} 天，加班 {tot['overtime_hours']} 小时。"]
    if top and top[0]["abnormal"]:
        lines.append("异常较多：" + "、".join(f"{r['name']}（{r['abnormal']} 次）" for r in top if r["abnormal"]) + "。")
    if cross:
        lines.append(f"跨表核对发现 {len(cross)} 项待核实（如{cross[0]['message']}）。")
    try:
        from app.services import hr_service
        lines.append(hr_service.analysis_brief(db, period))
    except Exception:  # noqa: BLE001
        pass
    return {"period": period, "summary_rows": rows, "exceptions": excs, "crosscheck": cross, "totals": tot,
            "analysis": "\n".join(lines), "file": file_brief(f),
            "import": {"id": imp.id, "filename": imp.filename, "total_rows": imp.total_rows,
                       "exception_rows": imp.exception_rows,
                       "row_errors": [x for x in imp.errors if x.get("kind") == "row_error"]}}

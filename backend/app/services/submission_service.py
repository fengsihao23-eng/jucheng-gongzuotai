"""周报/月报/日报提交统计、未提交识别、催交、企微汇报同步（FR-07-05 / PRD 3.5 / 7.2）。

- 应提交名单：sys_dict(report_rule, submit) —— include_depts / exclude_depts / exclude_roles；仅在职成员
- 提交记录：oa_submission（企微汇报 API 同步，按 template_name 判定日报/周报/月报）
- 期间口径：周报 2026-W43（ISO 周）；月报 2026-10；日报 2026-10-20
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.timeutil import TZ, month_of, month_range, now, prev_month, today, week_no, week_range
from app.models import OaSubmission, SysDepartment, SysNotify, SysUser
from app.services import dict_service, notify_service

TYPES = {"weekly": "周报", "monthly": "月报", "daily": "日报"}

WECOM_PERMISSION_HINT = ("企业微信拒绝了汇报接口（错误码 {code}）：当前自建应用还没有「汇报」接口权限。"
                        "请企业微信管理员在「管理后台 → 应用管理 → 自建应用 → 接口权限」勾选「汇报」，"
                        "并在汇报模板设置里把该应用加入可调用范围，保存后再点「同步企微汇报」。")


def wecom_error_hint(exc) -> str:
    code = getattr(exc, "errcode", None)
    if code == 48002:
        return WECOM_PERMISSION_HINT.format(code=code)
    return f"企业微信接口返回错误 {code}：{exc}"
TEMPLATE_TYPES = [("周报", "weekly"), ("月报", "monthly"), ("日报", "daily"), ("weekly", "weekly"),
                  ("monthly", "monthly"), ("daily", "daily")]
DEFAULT_RULE = {"weekly": {"exclude_depts": ["ADMIN"], "exclude_roles": ["EMP"]},
                "monthly": {"exclude_depts": ["ADMIN"], "exclude_roles": ["EMP"]},
                "daily": {"include_depts": ["XM", "SC"], "exclude_roles": ["EMP"]},
                "weekly_deadline": "周五 18:00", "monthly_deadline": "次月 3 日 18:00"}


def rule(db: Session) -> dict:
    return {**DEFAULT_RULE, **(dict_service.get(db, "report_rule", "submit") or {})}


def _check_type(report_type: str) -> None:
    if report_type not in TYPES:
        from app.core.response import bad_request
        raise bad_request(f"report_type 须为 {'/'.join(TYPES)}")


def required_users(db: Session, report_type: str) -> list[SysUser]:
    _check_type(report_type)
    r = rule(db).get(report_type, {})
    inc, exc_d, exc_r = set(r.get("include_depts") or []), set(r.get("exclude_depts") or []), \
        set(r.get("exclude_roles") or [])
    out = []
    for u in db.scalars(select(SysUser).where(SysUser.status == 1, SysUser.is_deleted.is_(False))
                        .order_by(SysUser.department_id, SysUser.id)):
        dc = u.dept_code
        if inc and dc not in inc:
            continue
        if dc in exc_d:
            continue
        codes = {x.role_code for x in u.roles} or {"EMP"}
        if exc_r and codes <= exc_r:
            continue
        out.append(u)
    return out


def current_period(report_type: str, ref: date | None = None) -> str:
    T = ref or today()
    if report_type == "weekly":
        return week_no(T)
    if report_type == "monthly":
        # 月末最后 3 天起统计本月月报；其余时间统计最近一个已结束月份（截止次月 3 日）
        _, me = month_range(ref=T)
        return month_of(T) if T >= me - timedelta(days=2) else prev_month(month_of(T))
    return (T if T.weekday() < 5 else T - timedelta(days=T.weekday() - 4)).isoformat()


def prev_period(report_type: str, period: str) -> str:
    if report_type == "weekly":
        s, _ = week_range(period)
        return week_no(s - timedelta(days=7))
    if report_type == "monthly":
        return prev_month(period)
    d = date.fromisoformat(period) - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.isoformat()


def recent_periods(report_type: str, period: str, n: int) -> list[str]:
    out = [period]
    while len(out) < n:
        out.append(prev_period(report_type, out[-1]))
    return list(reversed(out))


def period_of(report_type: str, t: datetime) -> str:
    """一条提交记录归属的期间（同步用）。"""
    d = t.astimezone(TZ).date() if t.tzinfo else t.date()
    if report_type == "weekly":
        return week_no(d)
    if report_type == "monthly":
        _, me = month_range(ref=d)
        return month_of(d) if d >= me - timedelta(days=6) else prev_month(month_of(d))
    return d.isoformat()


def deadline_of(report_type: str, period: str) -> datetime:
    if report_type == "weekly":
        s, _ = week_range(period)
        d = s + timedelta(days=4)
    elif report_type == "monthly":
        _, e = month_range(period)
        d = e + timedelta(days=3)
    else:
        d = date.fromisoformat(period)
    return datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=TZ)


def _submitted(db: Session, report_type: str, periods: list[str]) -> dict[str, dict[int, OaSubmission]]:
    out: dict[str, dict[int, OaSubmission]] = defaultdict(dict)
    for s in db.scalars(select(OaSubmission).where(OaSubmission.report_type == report_type,
                                                   OaSubmission.period.in_(periods))):
        out[s.period][s.user_id] = s
    return out


def _q(v) -> float | None:
    return None if v is None else float(Decimal(v).quantize(Decimal("0.0001")))


def stats(db: Session, report_type: str, period: str | None = None, with_trend: bool = False) -> dict:
    """按部门/按人提交率、未提交名单。"""
    _check_type(report_type)
    period = period or current_period(report_type)
    users = required_users(db, report_type)
    prev = prev_period(report_type, period)
    trend_n = {"weekly": 8, "monthly": 6, "daily": 10}[report_type]
    periods = recent_periods(report_type, period, trend_n) if with_trend else [prev, period]
    sub = _submitted(db, report_type, list(set(periods + [prev])))
    cur = sub.get(period, {})
    depts = {d.dept_code: d.name for d in db.scalars(select(SysDepartment))}
    by_dept: dict[str, dict] = {}
    people, missing = [], []
    for u in users:
        dc = u.dept_code or "-"
        row = by_dept.setdefault(dc, {"dept_code": dc, "dept_name": depts.get(dc, dc), "required": 0, "submitted": 0,
                                      "late": 0, "missing_names": []})
        row["required"] += 1
        s = cur.get(u.id)
        if s:
            row["submitted"] += 1
            row["late"] += s.stat_status == 2
        else:
            row["missing_names"].append(u.name)
            missing.append({"user_id": u.id, "userid": u.wecom_userid, "name": u.name, "dept_code": dc,
                            "dept_name": depts.get(dc, dc), "position": u.position,
                            "consecutive": u.id not in sub.get(prev, {})})
        people.append({"user_id": u.id, "name": u.name, "dept_code": dc, "dept_name": depts.get(dc, dc),
                       "position": u.position, "submitted": bool(s), "submission_id": s.id if s else None,
                       "submit_time": s.submit_time if s else None,
                       "late": bool(s and s.stat_status == 2), "prev_submitted": u.id in sub.get(prev, {})})
    for row in by_dept.values():
        row["rate"] = _q(Decimal(row["submitted"]) / row["required"]) if row["required"] else None
    n_req, n_sub = len(users), len(users) - len(missing)
    prev_sub = sum(1 for u in users if u.id in sub.get(prev, {}))
    res = {"report_type": report_type, "type_name": TYPES[report_type], "period": period,
           "deadline": deadline_of(report_type, period), "required": n_req, "submitted": n_sub,
           "late": sum(1 for p in people if p["late"]),
           "rate": _q(Decimal(n_sub) / n_req) if n_req else None,
           "prev_period": prev, "prev_rate": _q(Decimal(prev_sub) / n_req) if n_req else None,
           "by_dept": sorted(by_dept.values(), key=lambda r: (r["rate"] if r["rate"] is not None else 1, r["dept_code"])),
           "people": people, "missing": missing}
    if with_trend:
        res["trend"] = [{"period": p, "submitted": sum(1 for u in users if u.id in sub.get(p, {})),
                         "required": n_req,
                         "rate": _q(Decimal(sum(1 for u in users if u.id in sub.get(p, {}))) / n_req) if n_req else None}
                        for p in periods]
    res["summary"] = summary_text(res)
    return res


def summary_text(s: dict) -> str:
    """规则生成：提交率、较上期变化、提交率最低部门、连续未交人员。"""
    if not s["required"]:
        return f"{s['period']} 无应提交{s['type_name']}人员。"
    rate = s["rate"] or 0.0
    parts = [f"{s['period']} {s['type_name']}应提交 {s['required']} 人，已提交 {s['submitted']} 人"
             f"（含迟交 {s['late']} 人），提交率 {rate * 100:.1f}%"]
    if s.get("prev_rate") is not None:
        d = (rate - s["prev_rate"]) * 100
        parts.append(f"较上期（{s['prev_period']}）{'上升' if d > 0 else '下降' if d < 0 else '持平'}"
                     + (f" {abs(d):.1f} 个百分点" if d else ""))
    text = "，".join(parts) + "。"
    lows = [d for d in s["by_dept"] if d["rate"] is not None and d["rate"] < 1]
    if lows:
        low = lows[0]
        text += f"提交率最低的是{low['dept_name']}（{low['rate'] * 100:.0f}%，未交：{'、'.join(low['missing_names'][:5])}）。"
    else:
        text += "各部门均已全部提交。"
    cons = [m["name"] for m in s["missing"] if m["consecutive"]]
    if cons:
        text += f"连续两期未提交：{'、'.join(cons[:8])}{' 等' if len(cons) > 8 else ''}，建议重点提醒。"
    if now() < s["deadline"]:
        text += f"截止时间 {s['deadline']:%m-%d %H:%M} 前仍可提交。"
    return text


def remind(db: Session, report_type: str, period: str | None = None, user_ids: list[int] | None = None,
           operator_id: int | None = None, summary_to_exec: bool = False) -> dict:
    """逐人发企微催交提醒；可选给总经办发提交情况汇总。返回 {reminded:[names], summary}。"""
    s = stats(db, report_type, period)
    targets = s["missing"] if user_ids is None else [m for m in s["missing"] if m["user_id"] in set(user_ids)]
    users = {u.id: u for u in db.scalars(select(SysUser).where(SysUser.id.in_([m["user_id"] for m in targets])))}
    name = s["type_name"]
    deadline = f"{s['deadline']:%m-%d %H:%M}"
    for m in targets:
        u = users.get(m["user_id"])
        if not u:
            continue
        notify_service.send(db, [u], "reminder", f"{name}提交提醒（{s['period']}）",
                            f"{u.name}，您 {s['period']} 的{name}尚未提交，请于 {deadline} 前在企业微信「汇报」中提交。",
                            link="/submissions", ref_type="submission", ref_id=f"{report_type}:{s['period']}")
    if summary_to_exec:
        roles = (dict_service.get(db, "notify_rule", "morning_report") or {}).get("roles") or ["ZB-01", "ZB-02"]
        notify_service.send(db, notify_service.users_by_roles(db, roles), "reminder",
                            f"{name}提交情况（{s['period']}）",
                            s["summary"] + (f"\n已提醒：{'、'.join(m['name'] for m in targets)}" if targets else ""),
                            link=f"/submissions?type={report_type}&period={s['period']}", ref_type="submission",
                            ref_id=f"{report_type}:{s['period']}")
    from app.core.audit import log_op
    log_op(db, operator_id, "submission.remind", "submission", f"{report_type}:{s['period']}",
           after={"users": [m["name"] for m in targets]})
    db.commit()
    return {"period": s["period"], "report_type": report_type, "reminded": [m["name"] for m in targets],
            "count": len(targets), "summary": s["summary"]}


def remind_history(db: Session, report_type: str, period: str) -> list[dict]:
    rows = db.scalars(select(SysNotify).where(SysNotify.msg_type == "reminder", SysNotify.channel == "inapp",
                                              SysNotify.ref_id == f"{report_type}:{period}")
                      .order_by(SysNotify.id.desc()).limit(200)).all()
    names = {u.id: u.name for u in db.scalars(select(SysUser).where(SysUser.id.in_({r.user_id for r in rows})))} \
        if rows else {}
    return [{"user": names.get(r.user_id), "title": r.title, "sent_at": r.created_at} for r in rows]


# ------------------------------------------------------------------ 企微汇报同步
def template_type(name: str | None) -> str | None:
    n = (name or "").lower()
    for kw, t in TEMPLATE_TYPES:
        if kw in n:
            return t
    return None


def sync_journals(db: Session, start: datetime, end: datetime) -> dict:
    """从企微汇报 API 同步提交记录 → oa_submission（按 user+type+period 幂等 upsert）。"""
    from app.integration.wecom import get_wecom
    records = get_wecom().get_journal_records(start, end)
    users = {u.wecom_userid: u for u in db.scalars(select(SysUser))}
    inserted = updated = skipped = failed = 0
    for j in records:
        try:
            with db.begin_nested():  # 单条异常（脏数据、超长字段）不拖垮整批
                state = _upsert_journal(db, j, users)
        except Exception:  # noqa: BLE001
            failed += 1
            continue
        if state == "inserted":
            inserted += 1
        elif state == "updated":
            updated += 1
        else:
            skipped += 1
    db.commit()
    message = f"拉取 {len(records)} 条，新增 {inserted}，更新 {updated}，跳过 {skipped}"
    if failed:
        message += f"，失败 {failed}"
    return {"rows": inserted + updated, "inserted": inserted, "updated": updated, "skipped": skipped,
            "failed": failed, "fetched": len(records), "message": message}


def _upsert_journal(db: Session, j, users: dict) -> str:
    t = template_type(j.template_name)
    u = users.get(j.userid)
    if not t or not u:
        return "skipped"
    period = period_of(t, j.report_time)
    late = 2 if j.report_time > deadline_of(t, period) else 1
    row = db.scalar(select(OaSubmission).where(OaSubmission.user_id == u.id, OaSubmission.report_type == t,
                                               OaSubmission.period == period).execution_options(include_removed_records=True))
    if row:
        if row.wecom_report_id != j.report_id or row.submit_time is None:
            if row.submit_time is None or j.report_time < row.submit_time:
                row.submit_time, row.stat_status = j.report_time, late
            row.wecom_report_id = row.wecom_report_id or j.report_id
            return "updated"
        return "skipped"
    db.add(OaSubmission(user_id=u.id, report_type=t, period=period, submit_time=j.report_time,
                        wecom_report_id=j.report_id, content=j.content, stat_status=late))
    db.flush()
    return "inserted"

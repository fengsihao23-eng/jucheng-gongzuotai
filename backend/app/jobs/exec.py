"""总经办领域定时任务（PRD 6.5）：看板晨报推送、周报/月报催交、企微汇报同步、合同归档回款刷新。"""
from datetime import date, timedelta

from app.core.timeutil import month_range, now, today, week_no
from app.jobs.registry import job
from app.services import board_service, contract_service, dict_service, notify_service
from app.services import submission_service as ss


def _date(params: dict) -> date:
    return date.fromisoformat(params["date"]) if params.get("date") else today()


@job("morning_board_push", "看板晨报快照+企微推送", {"hour": 8, "minute": 0}, "每日 08:00",
     idempotency=lambda p: f"morning:{p.get('date') or today()}")
def morning_board_push(db, params):
    d = _date(params)
    r = board_service.build_morning_report(db, d)
    rule = dict_service.get(db, "notify_rule", "morning_report") or {}
    users = notify_service.users_by_roles(db, rule.get("roles") or ["ZB-01", "ZB-02"])
    from app.core.storage import signed_url
    img = signed_url(r["image_file"], ttl=86400, absolute=True)
    from app.core.config import settings
    board_url = settings.public_base_url.rstrip("/") + "/board"
    content = r["text_markdown"] + f"\n\n[查看晨报卡片]({img})　[打开实时看板]({board_url})"
    notify_service.send(db, users, "morning_report", f"经营晨报 {d:%m-%d}", content, link="/board",
                        ref_type="morning_report", ref_id=str(d))
    try:  # 晨报快照入报表库（可追溯）
        from app.models import RptReport
        db.add(RptReport(report_type="morning_report", period=d.isoformat(), title=f"经营晨报 {d}",
                         metrics=__import__("app.core.response", fromlist=["encode"]).encode(r["summary"]),
                         file_ids=[r["image_file"].id], trigger=params.get("trigger", "schedule")))
    except Exception:  # noqa: BLE001
        pass
    db.commit()
    return {"rows": len(users), "message": f"晨报已推送 {len(users)} 人：{'、'.join(u.name for u in users)}"}


def _reminder_kinds(d: date) -> list[str]:
    kinds = []
    if d.weekday() == 4:
        kinds.append("weekly")
    _, me = month_range(ref=d)
    if d.day <= 3 or d >= me - timedelta(days=2):  # 月末 3 天 / 月初 3 日
        kinds.append("monthly")
    return kinds


@job("journal_reminder", "周报/月报未提交催交", {"hour": 17, "minute": 0}, "每周五 17:00（周报）；月末/月初 3 日 17:00（月报）",
     idempotency=lambda p: f"journal_reminder:{p.get('kind') or 'auto'}:{p.get('period') or p.get('date') or today()}")
def journal_reminder(db, params):
    d = _date(params)
    kinds = [params["kind"]] if params.get("kind") else _reminder_kinds(d)
    if not kinds:
        return {"rows": 0, "message": f"{d} 非周报/月报催交日，跳过"}
    end = now()
    sync = ss.sync_journals(db, end - timedelta(days=10), end)
    out, total = [], 0
    for k in kinds:
        period = params.get("period") or (week_no(d) if k == "weekly" else ss.current_period("monthly", d))
        r = ss.remind(db, k, period, summary_to_exec=True)
        total += r["count"]
        out.append(f"{ss.TYPES[k]} {period} 提醒 {r['count']} 人")
    return {"rows": total, "message": f"同步汇报：{sync['message']}；" + "；".join(out)}


@job("oa_journal_sync", "企微汇报记录同步", {"minute": 5}, "每小时")
def oa_journal_sync(db, params):
    end = now()
    hours = int(params.get("hours", 3))
    r = ss.sync_journals(db, end - timedelta(hours=hours), end)
    board_service.invalidate()
    return r


@job("contract_payment_refresh", "合同归档回款关联刷新", {"day_of_week": "mon", "hour": 7, "minute": 30}, "每周一 07:30",
     idempotency=lambda p: f"contract_payment_refresh:{p.get('date') or week_no(today())}")
def contract_payment_refresh(db, params):
    return contract_service.refresh_archived(db)

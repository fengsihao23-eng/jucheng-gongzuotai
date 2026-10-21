"""营销管控部定时任务：月度业绩快照、每周项目运营看板。"""
from app.core.timeutil import prev_month, today, week_no
from app.jobs.registry import job
from app.services import performance_service


def _last_month(params: dict) -> str:
    return params.get("period") or prev_month(f"{today():%Y-%m}")


@job("performance_snapshot", "月度业绩统计快照", {"day": 1, "hour": 8, "minute": 30}, "每月 1 日 08:30",
     idempotency=lambda p: f"performance_snapshot:{_last_month(p)}")
def performance_snapshot(db, params):
    period = _last_month(params)
    rpt, f = performance_service.generate_report(db, "performance_monthly", period, trigger="schedule")
    db.commit()
    m = rpt.metrics
    return {"rows": 1, "message": f"{period} 业绩快照 v{rpt.version}：签约 {m.get('month_sign')}，回款 {m.get('month_pay')}，"
                                  f"文件 {f.filename}"}


@job("operation_board_weekly", "项目运营看板周报", {"day_of_week": "mon", "hour": 9, "minute": 15}, "每周一 09:15",
     idempotency=lambda p: f"operation_board_weekly:{p.get('period') or week_no(today())}")
def operation_board_weekly(db, params):
    period = params.get("period") or week_no(today())
    rpt, f = performance_service.generate_report(db, "operation_board", period, trigger="schedule")
    db.commit()
    m = rpt.metrics
    return {"rows": int(m.get("projects") or 0),
            "message": f"{period} 运营看板 v{rpt.version}：{m.get('projects')} 个项目，逾期应收 {m.get('overdue')}"}

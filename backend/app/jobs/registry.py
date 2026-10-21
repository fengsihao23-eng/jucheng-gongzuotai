"""定时任务引擎（PRD FR-11）。APScheduler 进程内调度 + Postgres advisory lock 防多进程重复执行。

- 各领域在 app/jobs/<domain>.py 中用 @job(...) 注册任务
- 任务幂等：idempotency_key（如 daily_expense_report:2026-10-19）已成功则跳过，force=True 可强制重跑
- 运行日志入 sync_job_log；失败经通知中心告警平台管理员
- admin 后台可手动触发：POST /api/v1/admin/jobs/{name}/run
"""
import logging
import traceback
import zlib
from dataclasses import dataclass
from typing import Callable

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.db import SessionLocal, engine
from app.core.timeutil import utcnow
from app.models import SyncJobLog

log = logging.getLogger("jobs")


@dataclass
class JobDef:
    name: str
    title: str
    cron: dict  # APScheduler cron kwargs, e.g. {"hour": 8, "minute": 0}
    schedule_text: str
    func: Callable[[Session, dict], dict]  # 返回 {"rows": int, "message": str, "idempotency_key": str?}
    idempotency: Callable[[dict], str | None] | None = None


JOBS: dict[str, JobDef] = {}


def job(name: str, title: str, cron: dict, schedule_text: str, idempotency: Callable[[dict], str | None] | None = None):
    def deco(fn):
        JOBS[name] = JobDef(name, title, cron, schedule_text, fn, idempotency)
        return fn
    return deco


def run_job(name: str, params: dict | None = None, trigger: str = "schedule", force: bool = False,
            db: Session | None = None) -> SyncJobLog:
    jd = JOBS[name]
    params = params or {}
    own = db is None
    db = db or SessionLocal()
    lock_id = zlib.crc32(name.encode())
    lock_conn = None  # 会话级锁必须在同一条独立连接上加锁/解锁（Session 提交后可能换连接）
    try:
        if db.bind.dialect.name == "postgresql":
            lock_conn = engine.connect()
            if not lock_conn.execute(text("select pg_try_advisory_lock(:k)"), {"k": lock_id}).scalar():
                lock_conn.close()
                lock_conn = None
                return _write(db, name, trigger, "skipped", message="另一进程正在执行")
        key = jd.idempotency(params) if jd.idempotency else None
        if key and not force:
            done = db.scalar(select(SyncJobLog).where(SyncJobLog.idempotency_key == key, SyncJobLog.status == "success"))
            if done:
                return _write(db, name, trigger, "skipped", key=key, message=f"已于 {done.run_at:%m-%d %H:%M} 成功执行，幂等跳过")
        entry = _write(db, name, trigger, "running", key=key)
        try:
            result = jd.func(db, params) or {}
            entry.status = "success"
            entry.rows_affected = int(result.get("rows", 0))
            entry.message = result.get("message")
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            entry = db.merge(entry)
            entry.status = "failed"
            entry.error_msg = f"{exc}\n{traceback.format_exc()[-1500:]}"
            log.exception("job %s failed", name)
            try:
                from app.services.notify_service import alert_admins
                alert_admins(db, f"定时任务失败：{jd.title}", f"{name} 执行失败：{exc}")
            except Exception:  # noqa: BLE001
                log.exception("alert admins failed")
        entry.finished_at = utcnow()
        db.commit()
        return entry
    finally:
        if lock_conn is not None:
            lock_conn.execute(text("select pg_advisory_unlock(:k)"), {"k": lock_id})
            lock_conn.close()
        if own:
            db.close()


def _write(db: Session, name: str, trigger: str, status: str, key: str | None = None, message: str | None = None):
    entry = SyncJobLog(job_name=name, trigger=trigger, run_at=utcnow(), status=status, idempotency_key=key,
                       message=message, finished_at=utcnow() if status != "running" else None)
    db.add(entry)
    db.commit()
    return entry


_scheduler = None


def start_scheduler():
    global _scheduler
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger

    from app.core.config import settings
    _scheduler = BackgroundScheduler(timezone=settings.timezone)
    for jd in JOBS.values():
        _scheduler.add_job(run_job, CronTrigger(timezone=settings.timezone, **jd.cron), args=[jd.name],
                           id=jd.name, misfire_grace_time=300, coalesce=True, max_instances=1)
    _scheduler.start()
    log.info("scheduler started with %d jobs", len(JOBS))
    return _scheduler


def stop_scheduler():
    if _scheduler:
        _scheduler.shutdown(wait=False)


def next_runs() -> dict[str, str | None]:
    if not _scheduler:
        return {}
    return {j.id: j.next_run_time.isoformat() if j.next_run_time else None for j in _scheduler.get_jobs()}

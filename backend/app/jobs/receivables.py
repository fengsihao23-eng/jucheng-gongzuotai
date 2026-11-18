import logging

from sqlalchemy import select

from app.core.response import BizError
from app.jobs.registry import job
from app.models import FinReceivablesSourceRow
from app.services import dict_service, receivables_service as ledger, receivables_sync as sync

log = logging.getLogger(__name__)


@job("collection_feedback_reminders", "回款节点超时自动提醒", {"hour": 9, "minute": 0}, "每天 09:00（北京时间）检查日/周反馈节点")
def collection_feedback_reminders(db, params):
    from app.services.collection_reminders import scan
    result = scan(db)
    db.commit()
    return result


@job("receivables_source_sync", "开票与收款固定来源同步", {"minute": "*/15"}, "每 15 分钟")
def sync_sources(db, params):
    configs = dict_service.items(db, "receivables_source")
    changed = posted = 0
    failures = []
    for key, config in configs.items():
        if not isinstance(config, dict) or config.get("enabled") is not True:
            continue
        source = f"wecom:{key}"
        try:
            kind = config.get("kind")
            if kind not in sync.MODELS or len(source) > 80:
                raise ValueError("invalid source definition")
            records = sync.fetch_source(db, config)
            changed += sync.stage(db, source, kind, records, snapshot=True)
            if config.get("auto_apply") is True:
                pending = db.scalars(select(FinReceivablesSourceRow).where(
                    FinReceivablesSourceRow.source_key == source, FinReceivablesSourceRow.state == "pending")
                    .order_by(FinReceivablesSourceRow.id)).all()
                for row in pending:
                    result = sync.apply_row(db, row, row.version, config["reader_id"])
                    posted += result.state == "applied"
            db.commit()
        except Exception as exc:
            db.rollback()
            if not isinstance(exc, BizError):
                log.warning("固定财务来源同步异常：%s (%s)", key, type(exc).__name__)
            failures.append(f"{key}：{exc.message if isinstance(exc, BizError) else '配置或源表格式无效'}")
    if posted:
        ledger.invalidate()
    if failures:
        raise RuntimeError(f"已更新 {changed} 行、入账 {posted} 行；失败：" + "；".join(failures)[:1200])
    return {"rows": changed, "message": f"更新待核对记录 {changed} 行，自动入账 {posted} 行"}

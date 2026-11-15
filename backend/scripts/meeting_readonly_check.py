"""Read-only release baseline; prints counts/fingerprints and source discrepancy counts only."""
import hashlib
import json

from sqlalchemy import func, select, text

from app.core.db import SessionLocal
from app.core.deps import build_principal
from app.models import (OpsProject, OpsTask, OpsTaskUpdate, OpsTaskNode, OpsMember, SysUserPermission,
                        BizContract, BizPayment, OpsSchool, OpsIncome, ContractWorkbook, SysUser, SysNotify)


def run(db):
    db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
    baseline = {}
    for model in (OpsProject, OpsTask, OpsTaskUpdate, OpsTaskNode, OpsMember, SysUserPermission, BizContract, BizPayment, OpsSchool, OpsIncome):
        table = model.__table__
        columns = list(table.columns)
        rows = [dict(r) for r in db.execute(select(*columns).order_by(*table.primary_key.columns)).mappings()]
        raw = json.dumps(rows, default=str, sort_keys=True, ensure_ascii=False).encode()
        baseline[table.name] = {"count": len(rows), "sha256": hashlib.sha256(raw).hexdigest()}
    sources = []
    try:
        from app.services.contract_reconciliation import reconcile
    except ImportError:
        reconcile = None  # A pre-release host can still provide the unchanged-data baseline.
    for workbook in db.scalars(select(ContractWorkbook)):
        user = db.get(SysUser, workbook.config.get("reader_id"))
        result = reconcile(db, build_principal(user)) if reconcile and user and user.status == 1 else None
        sources.append({"key": workbook.source_key, "revision": workbook.revision, "checked_at": workbook.checked_at,
                        "synced_at": workbook.synced_at, "has_error": bool(workbook.last_error),
                        "reconciliation": result["summary"] if result else workbook.summary,
                        "comparison": "current ledger" if result else "saved synchronization summary"})
    notifications = [{"channel": channel, "status": status, "count": count} for channel, status, count in db.execute(
        select(SysNotify.channel, SysNotify.send_status, func.count()).where(SysNotify.msg_type == "operations_assignment")
        .group_by(SysNotify.channel, SysNotify.send_status))]
    return {"tables": baseline, "sources": sources, "assignment_notifications": notifications}


if __name__ == "__main__":
    with SessionLocal() as db:
        print(json.dumps(run(db), default=str, ensure_ascii=False, indent=2))

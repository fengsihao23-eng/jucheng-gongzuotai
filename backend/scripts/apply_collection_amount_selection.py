"""Apply an owner-reviewed contract-total policy; never edits source cells or balances."""
import argparse
from decimal import Decimal
import hashlib
import json
from pathlib import Path

from sqlalchemy import select

from app.core.audit import log_op
from app.core.db import SessionLocal
from app.core.deps import build_principal
from app.core.operation_audit import Context, current, finish, sql_context
from app.core.response import BizError, conflict, forbidden
from app.core.timeutil import utcnow
from app.models import ContractWorkbook, SysUser
from app.services import business_workbook as source, collection_amounts as selection, contract_workbook_sync as sync, collection_plans


def run(review, source_key, *, commit):
    ctx = Context(source="maintenance", module="回款情况", action="应用已确认的合同金额口径" if commit else "核对合同金额口径")
    handle = current.set(ctx)
    outcome = "failed"
    try:
        with SessionLocal() as db:
            sync.lock(db, source_key)
            wb = db.scalar(select(ContractWorkbook).where(ContractWorkbook.source_key == source_key).with_for_update())
            if not wb:
                raise conflict("固定来源不存在")
            actor = db.get(SysUser, wb.config["reader_id"])
            principal = build_principal(actor)
            if not principal.is_admin or actor.is_deleted or actor.status != 1:
                raise forbidden("固定来源管理员无效")
            ctx.actor_id, ctx.actor_name, ctx.account = actor.id, actor.name, actor.wecom_userid
            sql_context(db.connection(), ctx)
            source_hash = hashlib.sha256(json.dumps(wb.sheets, sort_keys=True, default=str).encode()).hexdigest()
            if source_hash != review["source_hash"]:
                raise conflict("原表内容与已确认清单不一致，尚未清洗")
            before = source.module_rows(wb, "collections")
            plans = collection_plans.cards(wb)
            before_unpaid = [r.get("outstanding") for r in before]
            total = lambda rows: sum((Decimal(r["amount"]) for r in rows if r.get("amount") is not None), Decimal(0))
            policy = selection.build_policy(wb, review)
            prior = wb.config.get(selection.CONFIG)
            if prior and prior.get("review_sha256") != policy["review_sha256"]:
                raise conflict("此来源已有另一份核对口径，请先检查")
            already = bool(prior)
            if not already:
                if total(before) != Decimal(review["current_collection_contract_total"]):
                    raise conflict("清洗前合计与已确认清单不一致")
                policy.update(approved_at=utcnow().isoformat(), approved_by=actor.id,
                              approval="用户于2026-11-18确认2026-11-17的12笔红字核对清单")
                wb.config = {**wb.config, selection.CONFIG: policy}
            after = source.module_rows(wb, "collections")
            if total(after) != Decimal(review["proposed_collection_contract_total"]):
                raise conflict("清洗后的合同总金额未通过核对，尚未保存")
            if before_unpaid != [r.get("outstanding") for r in after] or plans != collection_plans.cards(wb):
                raise conflict("未收或月度计划发生变化，尚未保存")
            if source_hash != hashlib.sha256(json.dumps(wb.sheets, sort_keys=True, default=str).encode()).hexdigest():
                raise conflict("原表数据发生变化，尚未保存")
            result = {"applied": commit and not already, "already_applied": already, "source": source_key,
                      "revision": wb.revision, "source_hash": source_hash, "review_sha256": policy["review_sha256"],
                      "before": str(total(before)), "after": str(total(after)), "retained_red_amount": review["retained_total"],
                      "excluded_black_amount": review["excluded_total"], "balances_and_plans_unchanged": True,
                      "excluded_rows": sum(r.get("collection_amount_state") == "excluded" for r in after)}
            if commit and not already:
                log_op(db, actor.id, "collections.amount.selection", "contract_workbook", wb.id,
                       before={"amount": result["before"]}, after={**result, "confirmed": True})
                db.commit()
            else:
                db.rollback()
            outcome = "success"
            return result
    finally:
        try:
            finish(ctx, outcome=outcome, detail={"review_only": not commit})
        finally:
            current.reset(handle)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("key")
    parser.add_argument("--review-file", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(run(json.loads(Path(args.review_file).read_text()), args.key, commit=args.apply), ensure_ascii=False))
    except BizError as exc:
        raise SystemExit(exc.message) from None


if __name__ == "__main__":
    main()

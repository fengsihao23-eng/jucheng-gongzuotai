"""Activate the reviewed source-only pages after a verified database backup.

Retain historical financial ledgers; only the five business page read models
are replaced. Pin both source identity and reviewed revision, audit the switch,
and stop the old enrichment/receipt posting path for this workbook.
"""
import argparse
import json

from sqlalchemy import select

from app.core.audit import log_op
from app.core.db import SessionLocal
from app.core.response import bad_request
from app.models import ContractWorkbook
from app.services import business_workbook as svc, contract_workbook_sync as sync, dict_service


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_key")
    parser.add_argument("--docid", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    with SessionLocal() as db:
        sync.lock(db, args.source_key)
        wb = db.scalar(select(ContractWorkbook).where(ContractWorkbook.source_key == args.source_key).with_for_update())
        if not wb or wb.config.get("docid") != args.docid or wb.revision != args.revision:
            raise bad_request("来源或版本与已核验内容不一致，请重新核验")
        sheets = svc.validate_snapshot(wb.sheets, wb.config)
        controls = [c for s in sheets for c in s.controls()]
        if any(not c["matches"] for c in controls):
            raise bad_request("原表合计与明细存在差异，暂不切换")
        before = {"workbook_config": wb.config, "primary": dict_service.get(db, svc.CONFIG, "primary")}
        if args.apply:
            wb.config = {**wb.config, "presentation_only": True, "auto_apply": False, "auto_receipts": False, "enabled": True}
            config = {"enabled": True, "source_key": wb.source_key, "docid": args.docid,
                      "activation_revision": wb.revision, "legacy_display": "archived"}
            setting = dict_service.put(db, svc.CONFIG, "primary", config, "五块经营页面仅展示固定工作簿原值；历史财务记录保留")
            setting.status = 1
            log_op(db, wb.config["reader_id"], "business.workbook.activate", "contract_workbook", wb.source_key,
                   before=before, after=config)
            db.commit()
        print(json.dumps({"applied": args.apply, "source": wb.source_key, "revision": wb.revision,
                          "source_total_checks": len(controls), "sheets": [s.brief() for s in sheets]}, ensure_ascii=False))


if __name__ == "__main__":
    main()

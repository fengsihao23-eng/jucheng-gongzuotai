"""Manage a fixed WeCom workbook from the server; never prints business values."""
import argparse
import json

from sqlalchemy import select

from app.core.db import SessionLocal
from app.core.response import BizError, not_found
from app.models import ContractWorkbook
from app.services import contract_workbook_sync as sync


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    bind = sub.add_parser("bind")
    bind.add_argument("key")
    bind.add_argument("--title", required=True)
    bind.add_argument("--url", required=True)
    bind.add_argument("--reader-id", type=int, required=True)
    bind.add_argument("--department", type=int, action="append", default=[])
    bind.add_argument("--viewer", type=int, action="append", default=[])
    bind.add_argument("--profile", action="append", default=[], metavar="SHEET_ID=PROFILE")
    bind.add_argument("--auto-apply", action="store_true")
    bind.add_argument("--auto-receipts", action="store_true")
    bind.add_argument("--paused", action="store_true")
    preview = sub.add_parser("preview")
    preview.add_argument("key")
    run = sub.add_parser("run")
    run.add_argument("key")
    run.add_argument("--force", action="store_true")
    run.add_argument("--link-existing-only", action="store_true")
    run.add_argument("--expect-revision")
    for command in ("status", "pause", "resume"):
        sub.add_parser(command).add_argument("key")
    args = parser.parse_args()
    with SessionLocal() as db:
        if args.command == "bind":
            row = sync.configure(db, args.key, title=args.title, url=args.url, reader_id=args.reader_id,
                                 view_department_ids=args.department, view_user_ids=args.viewer,
                                 profiles=dict(s.split("=", 1) for s in args.profile), enabled=not args.paused,
                                 auto_apply=args.auto_apply, auto_receipts=args.auto_receipts)
            db.commit()
            report = {"key": row.source_key, "enabled": row.config["enabled"], "auto_apply": row.config["auto_apply"]}
        elif args.command in {"preview", "run"}:
            report = sync.sync(db, args.key, force=getattr(args, "force", False), preview=args.command == "preview",
                               allow_create=not getattr(args, "link_existing_only", False),
                               expected_revision=getattr(args, "expect_revision", None))
            if "sheets" in report:
                report["sheets"] = sync.sheet_list_from(report["sheets"])
        else:
            sync.lock(db, args.key)
            row = db.scalar(select(ContractWorkbook).where(ContractWorkbook.source_key == args.key).with_for_update())
            if not row:
                raise not_found("固定来源")
            if args.command != "status":
                row.config = {**row.config, "enabled": args.command == "resume"}
                db.commit()
            report = {"key": row.source_key, "enabled": row.config["enabled"], "auto_apply": row.config["auto_apply"],
                      "summary": row.summary, "revision": row.revision, "checked_at": row.checked_at,
                      "last_error": row.last_error}
        print(json.dumps(report, ensure_ascii=False, default=str))


if __name__ == "__main__":
    try:
        main()
    except BizError as exc:
        raise SystemExit(exc.message) from None

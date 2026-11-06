"""Explicitly reviewed admin → corporate identity linking; default is read-only preview."""
import argparse
import json

from app.core.db import SessionLocal
from app.core.response import encode
from app.services import account_link_service as service


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source-id", required=True, type=int)
    p.add_argument("--target-id", required=True, type=int)
    p.add_argument("--expected-userid", required=True)
    p.add_argument("--operator-id", required=True, type=int)
    p.add_argument("--backup-dir")
    p.add_argument("--apply", action="store_true")
    args = p.parse_args()
    if args.apply and not args.backup_dir:
        p.error("--apply requires --backup-dir")
    files = None
    with SessionLocal() as db:
        try:
            if args.apply:
                result, files = service.apply(db, args.source_id, args.target_id, args.expected_userid,
                    args.backup_dir, args.operator_id)
                db.commit()
            else:
                result = service.preview(db, args.source_id, args.target_id, args.expected_userid)
                db.rollback()
            print(json.dumps(encode({"committed": args.apply, **result}), ensure_ascii=False))
        except BaseException:
            db.rollback()
            if files:
                files.rollback()
            raise


if __name__ == "__main__":
    main()

"""先预览，核对摘要后用 --apply --expected-revision 执行；一次性原子重置。"""
import argparse
import json

from sqlalchemy import text

from app.core.db import SessionLocal
from app.services import workbench_access


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-revision")
    args = parser.parse_args()
    if args.apply and not args.expected_revision:
        parser.error("--apply 需要 --expected-revision")
    with SessionLocal() as db:
        db.execute(text("SELECT pg_advisory_xact_lock(190670114)"))
        plan = workbench_access.apply(db, args.expected_revision) if args.apply else workbench_access.preview(db)
        if args.apply:
            db.commit()
        print(json.dumps({"applied": args.apply, **plan}, ensure_ascii=False))


if __name__ == "__main__":
    main()

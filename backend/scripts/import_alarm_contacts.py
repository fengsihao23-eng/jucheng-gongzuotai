"""Preview or apply an alarm project school contact workbook."""
import argparse
import json
from pathlib import Path

from app.core.db import SessionLocal
from app.core.deps import build_principal
from app.core.response import BizError
from app.services.alarm_contacts_import import import_workbook
from app.services.operations_service import user


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workbook", type=Path)
    parser.add_argument("--project-id", type=int, required=True)
    parser.add_argument("--operator-id", type=int, required=True)
    parser.add_argument("--apply", action="store_true", help="提交导入，默认预览并回滚")
    args = parser.parse_args()
    payload = args.workbook.read_bytes()
    with SessionLocal() as db:
        try:
            principal = build_principal(user(db, args.operator_id))
            result = import_workbook(db, principal, args.project_id, args.workbook.name, payload)
            if args.apply:
                db.commit()
            else:
                db.rollback()
        except BizError as exc:
            raise SystemExit(exc.message) from None
    print(json.dumps({"committed": args.apply, "project_id": args.project_id, **result}, ensure_ascii=False))


if __name__ == "__main__":
    main()

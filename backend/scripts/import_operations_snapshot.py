"""Import a reviewed JSON manifest. Defaults to rollback preview; --apply commits one transaction."""
import argparse
import json
from pathlib import Path

from app.core.db import SessionLocal
from app.core.deps import build_principal
from app.core.response import encode
from app.models import SysUser
from app.services.operations_import import Snapshot, apply_snapshot


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest")
    parser.add_argument("--project-id", type=int, required=True)
    parser.add_argument("--operator-id", type=int, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    payload = json.loads(Path(args.manifest).read_text())
    snapshots = [Snapshot.model_validate(item) for item in payload["snapshots"]]
    with SessionLocal() as db:
        try:
            user = db.get(SysUser, args.operator_id)
            if not user or user.is_deleted or user.status != 1:
                raise ValueError("导入操作账号无效")
            principal = build_principal(user)
            results = [apply_snapshot(db, principal, args.project_id, item) for item in snapshots]
            if args.apply:
                db.commit()
            else:
                db.rollback()
            print(json.dumps(encode({"committed": args.apply, "project_id": args.project_id,
                                    "results": [{k: v for k, v in result.items() if k != "created_ids"} for result in results]}), ensure_ascii=False))
        except BaseException:
            db.rollback()
            raise


if __name__ == "__main__":
    main()

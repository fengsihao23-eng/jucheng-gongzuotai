"""历史项目报表导入：默认只读预览，--apply 在一个事务内提交整个 ZIP。"""
import argparse
import json
from pathlib import Path

from app.core.db import SessionLocal
from app.core.deps import build_principal
from app.core.response import encode
from app.models import SysUser
from app.services import project_report_service as service


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive")
    parser.add_argument("mapping")
    parser.add_argument("--operator-id", type=int, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--refresh-reports", action="store_true", help="随首次导入刷新利润、项目经营及公司月报快照")
    args = parser.parse_args()
    raw = Path(args.archive).read_bytes()
    mapping = json.loads(Path(args.mapping).read_text())
    with SessionLocal() as db:
        try:
            user = db.get(SysUser, args.operator_id)
            if not user or user.is_deleted or user.status != 1:
                raise ValueError("导入操作账号无效")
            principal = build_principal(user)
            result = service.apply_archive(db, principal, raw, mapping) if args.apply else service.preview(
                service.prepare_archive(db, principal, raw, mapping))
            if args.apply:
                new_ids = [item["id"] for item in result if not item["already_imported"]]
                refreshed = service.refresh_report_snapshots(db, principal.id, new_ids) if args.refresh_reports and new_ids else []
                db.commit()
                from app.services import board_service, receivables_service
                board_service.invalidate()
                receivables_service.invalidate()
            else:
                db.rollback()
                refreshed = []
            print(json.dumps(encode({"committed": args.apply, "reports": result, "refreshed_reports": refreshed}), ensure_ascii=False))
        except BaseException:
            db.rollback()
            raise


if __name__ == "__main__":
    main()

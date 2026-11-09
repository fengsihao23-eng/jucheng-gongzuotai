"""为缺少流程节点的项目补标准节点（默认只读预览，--apply 写入）。"""
import argparse
import json

from sqlalchemy import select

from app.core.db import SessionLocal
from app.core.response import encode
from app.models import BizProject
from app.services import project_service


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    with SessionLocal() as db:
        try:
            rows = []
            for p in db.scalars(select(BizProject).where(BizProject.is_deleted.is_(False)).order_by(BizProject.id)).all():
                if list(p.milestones):
                    continue
                nodes = project_service.ensure_nodes(db, p)
                rows.append({"project_code": p.project_code, "name": p.name, "status": p.status,
                             "nodes": len(nodes), "current": nodes[0].node_name if nodes else None,
                             "plan_end": str(nodes[-1].plan_date) if nodes and nodes[-1].plan_date else None})
            if args.apply:
                db.commit()
            else:
                db.rollback()
            print(json.dumps(encode({"committed": args.apply, "projects": len(rows),
                                     "nodes": sum(r["nodes"] for r in rows), "rows": rows}), ensure_ascii=False))
        except BaseException:
            db.rollback()
            raise


if __name__ == "__main__":
    main()

"""清除知识库里的演示语料（原乐享 Mock 镜像，source=lexiang），并写入默认分类树。

默认只预览将删除的数量；确认后加 --apply 执行。同事上传的文档（upload）和系统沉淀的文档（local）不受影响。

    python -m scripts.purge_demo_kb            # 预览
    python -m scripts.purge_demo_kb --apply    # 执行
生产：cd /opt/jucheng/deploy && docker compose exec -T api python -m scripts.purge_demo_kb --apply
"""
import sys

from sqlalchemy import func, select

from app.core.db import SessionLocal
from app.models import KbDocument
from app.services import kb_service


def main(apply: bool) -> None:
    db = SessionLocal()
    try:
        by_source = dict(db.execute(select(KbDocument.source, func.count(KbDocument.id)).group_by(KbDocument.source)).all())
        print(f"知识库现有文档：{by_source or '无'}")
        demo = by_source.get("lexiang", 0)
        if not apply:
            print(f"将删除演示语料 {demo} 篇（source=lexiang）。确认后加 --apply 执行。")
            return
        kb_service.ensure_categories(db)
        r = kb_service.purge_demo_corpus(db)
        db.commit()
        print(f"已删除演示语料 {r['docs']} 篇、{r['chunks']} 段；已确保默认分类树存在。")
    finally:
        db.close()


if __name__ == "__main__":
    main("--apply" in sys.argv)

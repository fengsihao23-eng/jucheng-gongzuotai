"""重建数据库并写入演示数据：python -m scripts.seed

核心种子（seed_core）之后依次调用各领域 scripts/seed_<domain>.py 的 seed(db, ctx)（存在即调用）。
"""
import importlib
import shutil
import sys
import time
from pathlib import Path

from app.core.config import settings
from app.core.db import Base, SessionLocal, engine
import app.models  # noqa: F401
from scripts import seed_core

DOMAINS = ["finance_ops", "reports", "exec", "market", "hr", "knowledge", "marketing", "mcp"]


def main(reset_files: bool = True) -> None:
    t0 = time.time()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    if reset_files:
        root = Path(settings.storage_dir).parent
        for sub in ("files", "mock"):
            shutil.rmtree(root / sub, ignore_errors=True)
    db = SessionLocal()
    try:
        ctx = seed_core.seed(db)
        db.commit()
        for d in DOMAINS:
            try:
                mod = importlib.import_module(f"scripts.seed_{d}")
            except ModuleNotFoundError as e:
                if e.name == f"scripts.seed_{d}":
                    continue
                raise
            mod.seed(db, ctx)
            db.commit()
            print(f"  seed_{d} ok")
    finally:
        db.close()
    print(f"seed done in {time.time() - t0:.1f}s -> {settings.database_url}")


if __name__ == "__main__":
    main("--keep-files" not in sys.argv)

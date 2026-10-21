from collections.abc import Iterator
from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, DateTime, Numeric, create_engine, func
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from app.core.config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

Money = Numeric(14, 2)


class Base(DeclarativeBase):
    type_annotation_map = {Decimal: Money}


class TimestampMixin:
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class SoftDeleteMixin:
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_or_404(db: Session, model, ident, what: str = "记录"):
    obj = db.get(model, ident)
    if obj is None or getattr(obj, "is_deleted", False):
        from app.core.response import not_found
        raise not_found(what)
    return obj


# JSON 列：Postgres 下使用 JSONB（可比较/可索引），其他方言回退通用 JSON
from sqlalchemy import JSON as _JSON  # noqa: E402
from sqlalchemy.dialects.postgresql import JSONB  # noqa: E402

JSON = _JSON().with_variant(JSONB(), "postgresql")

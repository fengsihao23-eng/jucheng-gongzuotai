"""口径字典读写（sys_dict）。评审变更只改配置不改代码。"""
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import SysDict


def get(db: Session, dict_type: str, key: str, default: Any = None) -> Any:
    row = db.scalar(select(SysDict).where(SysDict.dict_type == dict_type, SysDict.dict_key == key, SysDict.status == 1))
    return row.dict_value if row else default


def items(db: Session, dict_type: str) -> dict[str, Any]:
    rows = db.scalars(select(SysDict).where(SysDict.dict_type == dict_type, SysDict.status == 1)
                      .order_by(SysDict.dict_key)).all()
    return {r.dict_key: r.dict_value for r in rows}


def put(db: Session, dict_type: str, key: str, value: Any, remark: str | None = None) -> SysDict:
    row = db.scalar(select(SysDict).where(SysDict.dict_type == dict_type, SysDict.dict_key == key))
    if row:
        row.dict_value = value
        if remark is not None:
            row.remark = remark
    else:
        row = SysDict(dict_type=dict_type, dict_key=key, dict_value=value, remark=remark)
        db.add(row)
    db.flush()
    return row

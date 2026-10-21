"""业务编码生成：项目 JC-<业务线>-<年>-<三位流水>、合同 HT-<年>-<四位>、商机 SJ-、工单 KF-。"""
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.timeutil import today


def _next(db: Session, column, prefix: str, width: int) -> str:
    last = db.scalar(select(func.max(column)).where(column.like(f"{prefix}%")))
    seq = int(last[len(prefix):]) + 1 if last and last[len(prefix):].isdigit() else 1
    return f"{prefix}{seq:0{width}d}"


def project_code(db: Session, business_line: str, year: int | None = None) -> str:
    from app.models import BizProject
    return _next(db, BizProject.project_code, f"JC-{business_line}-{year or today().year}-", 3)


def contract_no(db: Session, year: int | None = None) -> str:
    from app.models import BizContract
    return _next(db, BizContract.contract_no, f"HT-{year or today().year}-", 4)


def opp_no(db: Session, year: int | None = None) -> str:
    from app.models import BizOpportunity
    return _next(db, BizOpportunity.opp_no, f"SJ-{year or today().year}-", 4)


def ticket_no(db: Session) -> str:
    from app.models import CsTicket
    return _next(db, CsTicket.ticket_no, f"KF-{today():%Y%m}-", 4)

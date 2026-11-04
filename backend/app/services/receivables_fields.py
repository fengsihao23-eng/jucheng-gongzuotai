from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, field_validator

from app.core.response import bad_request

Money = Annotated[Decimal, Field(gt=0, max_digits=14, decimal_places=2, allow_inf_nan=False)]
NonNegativeMoney = Annotated[Decimal, Field(ge=0, max_digits=14, decimal_places=2, allow_inf_nan=False)]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
Ref = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
InvoiceNo = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
ContractNo = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=32)]
Short = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
Note = Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
ID = Annotated[int, Field(gt=0, le=9_223_372_036_854_775_807)]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Issue(Strict):
    invoice_no: InvoiceNo
    issued_on: date
    amount: Money


class InvoiceRecord(Issue):
    customer: Name
    contract_no: ContractNo | None = None
    approval_no: Short | None = None
    note: Note | None = None


class InvoiceRequest(Strict):
    customer: Name
    amount: Money
    contract_no: ContractNo | None = None
    note: Note | None = None


class Receipt(Strict):
    reference: Ref
    received_on: date
    amount: Money
    payer: Name
    account: Short
    kind: Literal["contract", "unmatched", "other"]
    contract_no: ContractNo | None = None
    payment_id: ID | None = None
    note: Note | None = None

    @field_validator("kind", mode="before")
    @classmethod
    def normalize_kind(cls, value):
        if isinstance(value, str):
            value = value.strip()
            return {"合同回款": "contract", "待匹配收款": "unmatched", "其他收款": "other"}.get(value, value)
        return value


class Assign(Strict):
    contract_no: ContractNo


class Void(Strict):
    reason: Reason


class Correction(Void):
    reduced_total: NonNegativeMoney


class Allocation(Strict):
    invoice_id: ID
    amount: Money


class Allocations(Strict):
    items: list[Allocation] = Field(max_length=100)


def parse(model, fields):
    try:
        return model.model_validate({k: v for k, v in fields.items() if v not in (None, "")})
    except ValidationError as exc:
        first = exc.errors(include_input=False)[0]
        raise bad_request(f"字段 {'.'.join(map(str, first['loc']))} 无效或缺失（{first['msg']}）") from None

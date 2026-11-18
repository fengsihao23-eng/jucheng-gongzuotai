from datetime import date
from typing import Annotated, Literal

from pydantic import Field, StringConstraints

from app.schemas.operations import Id, Input, Money, Note

MaintenanceKind = Literal["device_maintenance", "arrears", "collection_maintenance", "customers"]


class AssetMovementIn(Input):
    product_id: Id
    action: Literal["inbound", "outbound", "return", "scrap_warehouse", "scrap_operating"]
    quantity: int = Field(gt=0, le=1000000)
    occurred_on: date
    school_id: Id | None = None
    note: Annotated[str, StringConstraints(min_length=1, max_length=2000)]
    request_key: Annotated[str, StringConstraints(pattern=r"^[a-zA-Z0-9_-]{8,80}$")]


class MaintenanceIn(Input):
    kind: MaintenanceKind
    title: Annotated[str, StringConstraints(min_length=1, max_length=160)]
    customer_name: Annotated[str, StringConstraints(max_length=128)] = ""
    contact_name: Annotated[str, StringConstraints(max_length=64)] = ""
    phone: Annotated[str, StringConstraints(max_length=64)] = ""
    address: Annotated[str, StringConstraints(max_length=500)] = ""
    device_name: Annotated[str, StringConstraints(max_length=128)] = ""
    amount: Money | None = None
    due_on: date | None = None
    owner_id: Id
    status: Literal["open", "processing", "done"] = "open"
    content: Note = ""
    problems: Note = ""
    next_step: Note = ""
    expected_revision: int | None = Field(default=None, ge=1)

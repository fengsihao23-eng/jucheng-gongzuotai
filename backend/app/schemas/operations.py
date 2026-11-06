"""页面、小聚和 MCP 共用的运营项目输入校验。"""
from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
Note = Annotated[str, StringConstraints(strip_whitespace=True, max_length=6000)]
Money = Annotated[Decimal, Field(ge=0, max_digits=14, decimal_places=2, allow_inf_nan=False)]
Id = Annotated[int, Field(gt=0)]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ProjectIn(Input):
    name: Name
    category: Annotated[str, StringConstraints(min_length=1, max_length=64)] = "智慧校园"
    owner_id: Id
    objective: Note = ""
    target_date: date | None = None
    status: Literal["active", "paused", "completed"] = "active"
    is_key: bool = True


class MemberIn(Input):
    user_id: Id
    role: Literal["manager", "member", "viewer"] = "member"


class SchoolIn(Input):
    name: Name
    stage: Literal["contact", "proposal", "negotiation", "won", "operating", "lost"] = "contact"
    potential: Literal["high", "medium", "low"] = "medium"
    next_action: Note = ""
    next_contact_date: date | None = None
    labor_cost: Money = Decimal("0")
    other_cost: Money = Decimal("0")
    note: Note = ""


class ProductIn(Input):
    name: Name
    brand: Annotated[str, StringConstraints(max_length=64)] = ""
    model: Annotated[str, StringConstraints(max_length=128)] = ""
    unit: Annotated[str, StringConstraints(min_length=1, max_length=16)] = "台"
    reference_price: Money = Decimal("0")
    note: Note = ""


class EquipmentIn(Input):
    product_id: Id
    quantity: Annotated[int, Field(gt=0, le=1000000)] = 1
    unit_price: Money
    note: Note = ""


class IncomeIn(Input):
    income_date: date
    amount: Money
    source: Annotated[str, StringConstraints(min_length=1, max_length=128)] = "财务收款表"
    note: Note = ""


class NodeIn(Input):
    name: Name
    due_date: date


class TaskIn(Input):
    title: Annotated[str, StringConstraints(min_length=1, max_length=160)]
    description: Note = ""
    owner_id: Id
    school_id: Id | None = None
    due_date: date
    priority: Literal["key", "normal"] = "key"
    cadence: Literal["daily", "node"] = "daily"
    nodes: list[NodeIn] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def node_dates(self):
        if any(n.due_date > self.due_date for n in self.nodes):
            raise ValueError("阶段节点日期不能晚于任务截止日期")
        return self


class TaskPatch(Input):
    title: Annotated[str, StringConstraints(min_length=1, max_length=160)] | None = None
    description: Note | None = None
    owner_id: Id | None = None
    due_date: date | None = None
    priority: Literal["key", "normal"] | None = None
    cadence: Literal["daily", "node"] | None = None


class FeedbackIn(Input):
    content: Annotated[str, StringConstraints(min_length=1, max_length=6000)]
    next_step: Note = ""
    status: Literal["todo", "doing", "blocked", "done"]
    progress: Annotated[int, Field(ge=0, le=100)]
    completed_node_ids: list[Id] = Field(default_factory=list, max_length=30)


class DraftIn(Input):
    text: Annotated[str, StringConstraints(min_length=1, max_length=6000)]

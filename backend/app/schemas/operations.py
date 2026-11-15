"""页面、小聚和 MCP 共用的运营项目输入校验。"""
from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
Note = Annotated[str, StringConstraints(strip_whitespace=True, max_length=6000)]
Money = Annotated[Decimal, Field(ge=0, max_digits=14, decimal_places=2, allow_inf_nan=False)]
UnitPrice = Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=6, allow_inf_nan=False)]
Id = Annotated[int, Field(gt=0)]
Month = Annotated[str, StringConstraints(pattern=r"^20\d{2}-(0[1-9]|1[0-2])$")]
TaskSource = Literal["company", "self", "project_plan"]
Visibility = Literal["team", "selected"]
TaskKind = Literal["general", "outreach"]
Cadence = Literal["daily", "every_2_days", "every_3_days", "weekly", "node"]


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


class ProjectProgressIn(Input):
    progress: Annotated[str, StringConstraints(min_length=1, max_length=6000)] = Field(description="填写项目实际进度情况")
    problems: Note = Field(default="", description="遇到的问题；没有问题时可留空")
    next_plan: Note = Field(default="", description="下一步计划；填写时需同时提供计划完成日期")
    next_due_date: date | None = Field(default=None, description="下一步计划完成日期 YYYY-MM-DD，不是项目整体目标日期")

    @model_validator(mode="after")
    def plan_and_date(self):
        if bool(self.next_plan) != bool(self.next_due_date):
            raise ValueError("下一步计划和计划完成日期须一起填写")
        return self


class ProjectProgressEntriesIn(Input):
    entries: list[ProjectProgressIn] = Field(min_length=1, max_length=20, description="项目负责人一次填报的事项。带下一步和日期的条目自动生成可跟踪的项目计划。")
    expected_owner_id: Id | None = Field(default=None, description="预览时的项目负责人编号；未核对时留空")


class MemberIn(Input):
    user_id: Id
    role: Literal["manager", "co_owner", "member", "reviewer", "viewer"] = "member"


class BatchMembersIn(Input):
    user_ids: list[Id] = Field(min_length=1, max_length=200)
    role: Literal["member", "viewer"] = "member"


class ArchiveIn(Input):
    reason: Annotated[str, StringConstraints(min_length=1, max_length=500)]


class SchoolIn(Input):
    name: Name
    stage: Literal["unknown", "contact", "proposal", "negotiation", "won", "operating", "lost"] = "contact"
    potential: Literal["unknown", "high", "medium", "low"] = "medium"
    next_action: Note = ""
    next_contact_date: date | None = None
    labor_cost: Money = Decimal("0")
    other_cost: Money = Decimal("0")
    note: Note = ""


class ProductIn(Input):
    name: Name
    brand: Annotated[str, StringConstraints(max_length=64)] = ""
    model: Note = ""
    unit: Annotated[str, StringConstraints(min_length=1, max_length=16)] = "台"
    reference_price: UnitPrice | None = None
    note: Note = ""


class EquipmentIn(Input):
    product_id: Id
    quantity: Annotated[int, Field(gt=0, le=1000000)] = 1
    unit_price: UnitPrice | None = None
    recorded_amount: Money | None = None
    review_note: Note = ""
    note: Note = ""

    @model_validator(mode="after")
    def explicit_amount_reason(self):
        if self.recorded_amount is not None and self.unit_price is not None and self.recorded_amount != self.quantity * self.unit_price and not self.review_note:
            raise ValueError("清单金额与数量乘单价不一致，请填写核对说明")
        return self


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
    cadence: Cadence = "daily"
    plan_month: Month | None = None
    source: Literal["company", "self"] = "company"
    department_reviewer_id: Id | None = None
    company_reviewer_id: Id | None = None
    nodes: list[NodeIn] = Field(default_factory=list, max_length=30)
    kind: TaskKind = "general"
    visibility: Visibility = "team"
    viewer_ids: list[Id] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def node_dates(self):
        if any(n.due_date > self.due_date for n in self.nodes):
            raise ValueError("阶段节点日期不能晚于任务截止日期")
        return self


class TaskPatch(Input):
    expected_revision: Annotated[int, Field(ge=1)] | None = None
    description: Note | None = None
    title: Annotated[str, StringConstraints(min_length=1, max_length=160)] | None = None
    description: Note | None = None
    owner_id: Id | None = None
    due_date: date | None = None
    priority: Literal["key", "normal"] | None = None
    cadence: Cadence | None = None
    plan_month: Month | None = None
    department_reviewer_id: Id | None = None
    company_reviewer_id: Id | None = None


    school_id: Id | None = None
    kind: TaskKind | None = None
    visibility: Visibility | None = None
    viewer_ids: list[Id] | None = Field(default=None, max_length=200)


class FeedbackIn(Input):
    node_id: Id | None = None
    content: Annotated[str, StringConstraints(min_length=1, max_length=6000)]
    next_step: Note = ""
    status: Literal["todo", "doing", "blocked", "done"]
    progress: Annotated[int, Field(ge=0, le=100)] | None = Field(
        default=None, description="可省略，系统按已完成节点更新进度。仅在用户明确提供时填写百分比。"
    )
    completed_node_ids: list[Id] = Field(default_factory=list, max_length=30)
    incomplete_reason: Note = ""
    expected_revision: Annotated[int, Field(ge=1)] | None = None


class ReviewIn(Input):
    decision: Literal["approve", "reject"]
    comment: Annotated[str, StringConstraints(min_length=1, max_length=6000)]
    expected_revision: Annotated[int, Field(ge=1)]


class DelayReasonIn(Input):
    reason: Annotated[str, StringConstraints(min_length=1, max_length=6000)]
    expected_revision: Annotated[int, Field(ge=1)]


class DelayReviewIn(Input):
    reason_id: Id
    decision: Literal["accepted", "rejected"]
    comment: Note = ""
    expected_revision: Annotated[int, Field(ge=1)]

    @model_validator(mode="after")
    def rejection_comment(self):
        if self.decision == "rejected" and not self.comment:
            raise ValueError("不认可时请填写意见")
        return self


class DraftIn(Input):
    text: Annotated[str, StringConstraints(min_length=1, max_length=6000)]


class DiscussionIn(Input):
    kind: Literal["note", "request", "reply"] = "note"
    content: Annotated[str, StringConstraints(min_length=1, max_length=6000)]
    node_id: Id | None = None
    recipient_id: Id | None = None
    reply_to_id: Id | None = None


class SchoolVisitIn(Input):
    occurred_on: date
    content: Annotated[str, StringConstraints(min_length=1, max_length=6000)]
    next_action: Note = ""
    next_contact_date: date | None = None


class SchoolTransitionIn(Input):
    stage: Literal["won", "operating"]
    expected_stage: str
    reason: Annotated[str, StringConstraints(min_length=1, max_length=1000)]


class DigestGenerateIn(Input):
    owner_id: Id | None = None
    project_id: Id
    mode: Literal["week", "month"] = "week"
    period: str = ""
    mine: bool = False
    new_version: bool = False


class DigestPreferenceIn(Input):
    weekly: bool = True
    monthly: bool = True

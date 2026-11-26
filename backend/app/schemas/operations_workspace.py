from datetime import date
from typing import Annotated, Literal
from pydantic import Field, StringConstraints, model_validator
from app.core.permission_catalog import OPERATION_SECTIONS
from app.schemas.operations import Id, Input, Money, Name, Note, Cadence, OperationSection


class MemberViewIn(Input):
    sections: list[OperationSection] = Field(max_length=len(OPERATION_SECTIONS))
    task_scope: Literal["team", "self"] = "team"
    expected_revision: int = Field(ge=0)


class ProjectControlIn(Input):
    wecom_reminders: bool = True
    business_project_code: str | None = Field(default=None, max_length=32)
    report_audience: Literal["all", "selected"] = "all"
    report_user_ids: list[Id] = Field(default_factory=list, max_length=200)
    expected_revision: int = Field(ge=0)


ProjectUserCategory = Literal["education", "other"]


class ProjectUserIn(Input):
    category: ProjectUserCategory
    name: Name
    contact: Annotated[str, StringConstraints(max_length=80)] = ""
    contact_details: Annotated[str, StringConstraints(max_length=128)] = ""
    note: Annotated[str, StringConstraints(max_length=1000)] = ""


class ProjectUserEditIn(ProjectUserIn):
    expected_revision: int = Field(ge=1)


class OutreachGoalIn(Input):
    school_id: Id
    year: int = Field(ge=2000, le=2100)
    owner_id: Id
    target_date: date
    objective: Annotated[str, StringConstraints(min_length=1, max_length=6000)]
    expected_annual_income: Money | None = None
    cadence: Cadence = "weekly"
    expected_revision: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def year_matches(self):
        if self.target_date.year != self.year:
            raise ValueError("目标完成日期须在所选年度内")
        return self


class SchoolRegionIn(Input):
    region: Literal["市直", "江城", "阳春", "阳东", "阳西", "海陵湾", "其他", "待定"]


class IncomeFeedRow(Input):
    school_id: Id | None = None
    income_date: date
    amount: Money
    note: Note = ""


class IncomeFeedIn(Input):
    source_key: Annotated[str, StringConstraints(pattern=r"^[a-zA-Z0-9_-]{1,64}$")]
    batch_key: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    rows: list[IncomeFeedRow] = Field(min_length=1, max_length=1500)
    expected_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

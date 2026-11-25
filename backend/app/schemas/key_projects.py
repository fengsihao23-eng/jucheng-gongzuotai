from datetime import date
from typing import Annotated, Literal

from pydantic import AwareDatetime, Field, StringConstraints, model_validator

from app.schemas.operations import Id, Input, Note

Revision = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]


class KeyProjectIn(Input):
    name: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    content: Note = ""
    customer_name: Annotated[str, StringConstraints(max_length=128)] = ""
    business_line: Literal["XY", "AF", "KP", "YW", "TD"] = "TD"
    owner_id: Id
    preparation_on: date
    due_on: date
    status: Literal[1, 2, 3, 4, 5] = 2

    @model_validator(mode="after")
    def dates(self):
        if self.preparation_on > self.due_on:
            raise ValueError("准备日期不能晚于计划完成日期")
        return self


class KeyProjectEditIn(KeyProjectIn):
    expected_revision: Revision


class KeyTaskNodeIn(Input):
    id: Id | None = None
    name: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    due_on: date


class KeyTaskIn(Input):
    title: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    requirements: Note = ""
    owner_id: Id
    preparation_on: date
    due_on: date
    cadence: Literal["daily", "weekly", "node"] = "weekly"
    feedback_due_at: AwareDatetime | None = None
    nodes: list[KeyTaskNodeIn] = Field(default_factory=list, max_length=30)
    expected_revision: Revision | None = None

    @model_validator(mode="after")
    def dates(self):
        if self.preparation_on > self.due_on or any(not self.preparation_on <= n.due_on <= self.due_on for n in self.nodes):
            raise ValueError("任务准备日期不能晚于完成日期，阶段节点须在任务日期内")
        ids = [n.id for n in self.nodes if n.id]
        if len(ids) != len(set(ids)):
            raise ValueError("阶段节点编号不能重复")
        return self


class KeyProjectCreateIn(KeyProjectIn):
    tasks: list[KeyTaskIn] = Field(default_factory=list)


class KeyTaskFeedbackIn(Input):
    content: Annotated[str, StringConstraints(min_length=1, max_length=6000)]
    problems: Note = ""
    next_step: Note = ""
    status: Literal[1, 2, 3] = 2
    completed_node_ids: list[Id] = Field(default_factory=list, max_length=30)
    completed_on: date | None = None
    expected_revision: Revision

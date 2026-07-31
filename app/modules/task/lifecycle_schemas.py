from typing import Annotated, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    field_validator,
    model_validator,
)

from app.modules.task.models import TaskLifecycleStatus

PositiveStrictInt = Annotated[StrictInt, Field(gt=0)]

_ALLOWED_TRANSITIONS = {
    TaskLifecycleStatus.TODO: {
        TaskLifecycleStatus.TODO,
        TaskLifecycleStatus.IN_PROGRESS,
        TaskLifecycleStatus.CANCELLED,
    },
    TaskLifecycleStatus.IN_PROGRESS: {
        TaskLifecycleStatus.IN_PROGRESS,
        TaskLifecycleStatus.DONE,
        TaskLifecycleStatus.CANCELLED,
    },
    TaskLifecycleStatus.DONE: {TaskLifecycleStatus.DONE},
    TaskLifecycleStatus.CANCELLED: {TaskLifecycleStatus.CANCELLED},
}


class TaskLifecycleResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: PositiveStrictInt
    company_id: PositiveStrictInt
    previous_status: TaskLifecycleStatus
    current_status: TaskLifecycleStatus
    changed: StrictBool

    @field_validator("previous_status", "current_status", mode="before")
    @classmethod
    def require_lifecycle_status(cls, value: object) -> TaskLifecycleStatus:
        if type(value) is not TaskLifecycleStatus:
            raise ValueError("Task lifecycle status is invalid.")
        return value

    @model_validator(mode="after")
    def validate_transition(self) -> Self:
        if self.current_status not in _ALLOWED_TRANSITIONS[
            self.previous_status
        ] or self.changed != (self.previous_status is not self.current_status):
            raise ValueError("Task lifecycle result is invalid.")
        return self

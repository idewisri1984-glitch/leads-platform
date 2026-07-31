from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database.base import Base
from app.modules.company.models import Company
from app.modules.lead.models import Lead
from app.modules.project.models import Project
from app.modules.task import TaskWorkQueueBucket, TaskWorkQueueService
from app.modules.task.models import Task
from app.modules.task.repository import TaskRepository

AS_OF = datetime(2026, 7, 31, 9)


class CountingSession(Session):
    execute_calls = 0

    def execute(self, *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        self.execute_calls += 1
        return super().execute(*args, **kwargs)


@pytest.fixture
def session() -> CountingSession:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection: object, _record: object) -> None:
        dbapi_connection.execute("PRAGMA foreign_keys=ON")  # type: ignore[attr-defined]

    Base.metadata.create_all(engine)
    selected = CountingSession(engine)
    try:
        yield selected
    finally:
        selected.close()
        engine.dispose()


def seed(session: Session) -> tuple[int, int]:
    project = Project(name="Queue project")
    first = Company(project=project, name="First company")
    second = Company(project=project, name="Second company")
    lead = Lead(company=first, status="NEW")
    other_lead = Lead(company=second, status="NEW")
    session.add_all([project, first, second, lead, other_lead])
    session.flush()
    rows = [
        Task(lead=lead, title="overdue", status="TODO", due_at=AS_OF - timedelta(1)),
        Task(lead=lead, title="boundary", status="IN_PROGRESS", due_at=AS_OF),
        Task(
            lead=lead,
            title="horizon",
            status="TODO",
            due_at=AS_OF + timedelta(days=7),
        ),
        Task(lead=lead, title="unscheduled", status="IN_PROGRESS", due_at=None),
        Task(
            lead=lead,
            title="outside",
            status="TODO",
            due_at=AS_OF + timedelta(days=8),
        ),
        Task(lead=lead, title="done", status="DONE", due_at=None),
        Task(lead=lead, title="custom", status="WAITING_CUSTOMER", due_at=None),
        Task(lead=other_lead, title="cross company", status="TODO", due_at=None),
    ]
    session.add_all(rows)
    session.commit()
    return first.id, second.id


def test_real_repository_is_company_scoped_one_query_and_read_only(
    session: CountingSession,
) -> None:
    company_id, other_company_id = seed(session)
    before = session.scalar(select(func.count()).select_from(Task))
    session.execute_calls = 0
    selected = TaskWorkQueueService(TaskRepository(session)).get_queue(company_id, AS_OF)
    assert session.execute_calls == 1
    assert [x.title for x in selected.items] == [
        "overdue",
        "boundary",
        "horizon",
        "unscheduled",
    ]
    assert [x.bucket for x in selected.items] == [
        TaskWorkQueueBucket.OVERDUE,
        TaskWorkQueueBucket.UPCOMING,
        TaskWorkQueueBucket.UPCOMING,
        TaskWorkQueueBucket.UNSCHEDULED,
    ]
    assert session.scalar(select(func.count()).select_from(Task)) == before
    session.execute_calls = 0
    other = TaskWorkQueueService(TaskRepository(session)).get_queue(other_company_id, AS_OF)
    assert [x.title for x in other.items] == ["cross company"]
    assert session.execute_calls == 1


def test_missing_and_existing_empty_company_are_identical(
    session: CountingSession,
) -> None:
    _, _ = seed(session)
    missing = TaskWorkQueueService(TaskRepository(session)).get_queue(9999, AS_OF)
    project = Project(name="Empty project")
    company = Company(project=project, name="Empty company")
    session.add(project)
    session.commit()
    empty = TaskWorkQueueService(TaskRepository(session)).get_queue(company.id, AS_OF)
    assert missing.items == empty.items == ()
    assert (missing.overdue_count, missing.upcoming_count, missing.unscheduled_count) == (
        0,
        0,
        0,
    )


@pytest.mark.parametrize(
    ("company_id", "as_of", "until"),
    [(True, AS_OF, AS_OF + timedelta(1)), (1, AS_OF, AS_OF), (0, AS_OF, AS_OF)],
)
def test_direct_repository_validation_precedes_query(
    session: CountingSession,
    company_id: object,
    as_of: datetime,
    until: datetime,
) -> None:
    session.execute_calls = 0
    with pytest.raises(ValueError, match="^Task work queue data is invalid\\.$"):
        TaskRepository(session).list_work_queue_for_company(  # type: ignore[arg-type]
            company_id, as_of, until
        )
    assert session.execute_calls == 0

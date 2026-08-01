from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy import inspect as sqlalchemy_inspect
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database.base import Base
from app.modules.company.models import Company
from app.modules.contact.models import Contact
from app.modules.lead.models import Lead
from app.modules.project.models import Project
from app.modules.task import TaskWorkQueueBucket, TaskWorkQueueService
from app.modules.task.models import Task
from app.modules.task.repository import TaskRepository

AS_OF = datetime(2026, 7, 31, 9)


def mapped_scalar_snapshot(instance: object) -> tuple[tuple[str, object], ...]:
    mapper = sqlalchemy_inspect(type(instance))
    keys = tuple(attribute.key for attribute in mapper.column_attrs)
    return tuple((key, getattr(instance, key)) for key in keys)


def domain_snapshot(session: Session) -> dict[str, tuple[tuple[tuple[str, object], ...], ...]]:
    return {
        "projects": tuple(
            mapped_scalar_snapshot(value) for value in session.query(Project).order_by(Project.id)
        ),
        "companies": tuple(
            mapped_scalar_snapshot(value) for value in session.query(Company).order_by(Company.id)
        ),
        "contacts": tuple(
            mapped_scalar_snapshot(value) for value in session.query(Contact).order_by(Contact.id)
        ),
        "leads": tuple(
            mapped_scalar_snapshot(value) for value in session.query(Lead).order_by(Lead.id)
        ),
        "tasks": tuple(
            mapped_scalar_snapshot(value) for value in session.query(Task).order_by(Task.id)
        ),
    }


class CountingSession(Session):
    execute_calls = 0
    commit_calls = 0
    rollback_calls = 0
    add_calls = 0
    flush_calls = 0

    def execute(self, *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        self.execute_calls += 1
        return super().execute(*args, **kwargs)

    def commit(self) -> None:
        self.commit_calls += 1
        super().commit()

    def rollback(self) -> None:
        self.rollback_calls += 1
        super().rollback()

    def add(self, instance: object, *, _warn: bool = True) -> None:
        self.add_calls += 1
        super().add(instance, _warn=_warn)

    def flush(self, objects: object = None) -> None:
        if self.new or self.dirty or self.deleted:
            self.flush_calls += 1
        super().flush(objects)  # type: ignore[arg-type]

    def reset_queue_tracking(self) -> None:
        self.execute_calls = 0
        self.commit_calls = 0
        self.rollback_calls = 0
        self.add_calls = 0
        self.flush_calls = 0


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


def test_complete_status_exclusion_and_window_matrix(session: CountingSession) -> None:
    project = Project(name="Matrix project")
    company = Company(project=project, name="Matrix company")
    lead = Lead(company=company, status="NEW")
    session.add(project)
    session.flush()
    eligible = [
        Task(
            lead=lead,
            title="overdue todo",
            status="TODO",
            due_at=AS_OF - timedelta(microseconds=1),
        ),
        Task(
            lead=lead,
            title="overdue progress",
            status="IN_PROGRESS",
            due_at=AS_OF - timedelta(days=1),
        ),
        Task(lead=lead, title="as of", status="TODO", due_at=AS_OF),
        Task(lead=lead, title="inside", status="IN_PROGRESS", due_at=AS_OF + timedelta(days=1)),
        Task(lead=lead, title="horizon", status="TODO", due_at=AS_OF + timedelta(days=7)),
        Task(lead=lead, title="unscheduled todo", status="TODO", due_at=None),
        Task(lead=lead, title="unscheduled progress", status="IN_PROGRESS", due_at=None),
    ]
    excluded = [
        Task(lead=lead, title=status, status=status, due_at=None)
        for status in (
            "DONE",
            "CANCELLED",
            "todo",
            "in_progress",
            " TODO",
            "IN_PROGRESS ",
            "",
            "WAITING_CUSTOMER",
        )
    ]
    excluded.append(
        Task(
            lead=lead,
            title="after horizon",
            status="TODO",
            due_at=AS_OF + timedelta(days=7, microseconds=1),
        )
    )
    session.add_all([*eligible, *excluded])
    session.commit()
    expected_ids = {task.id for task in eligible}
    excluded_ids = {task.id for task in excluded}
    company_id = company.id
    session.reset_queue_tracking()
    selected = TaskWorkQueueService(TaskRepository(session)).get_queue(company_id, AS_OF)
    assert {item.task_id for item in selected.items} == expected_ids
    assert not ({item.task_id for item in selected.items} & excluded_ids)
    assert [item.title for item in selected.items] == [
        "overdue progress",
        "overdue todo",
        "as of",
        "inside",
        "horizon",
        "unscheduled progress",
        "unscheduled todo",
    ]
    assert (session.execute_calls, session.commit_calls, session.rollback_calls) == (1, 0, 0)
    assert (session.add_calls, session.flush_calls) == (0, 0)


def test_equal_due_and_unscheduled_ordering_tiebreakers(session: CountingSession) -> None:
    project = Project(name="Order project")
    company = Company(project=project, name="Order company")
    lead = Lead(company=company, status="NEW")
    session.add(project)
    session.flush()
    due = AS_OF + timedelta(hours=1)
    first_todo = Task(lead=lead, title="todo low", status="TODO", due_at=due)
    second_todo = Task(lead=lead, title="todo high", status="TODO", due_at=due)
    first_progress = Task(lead=lead, title="progress low", status="IN_PROGRESS", due_at=due)
    second_progress = Task(lead=lead, title="progress high", status="IN_PROGRESS", due_at=due)
    unscheduled_todo = Task(lead=lead, title="u todo", status="TODO", due_at=None)
    unscheduled_progress = Task(lead=lead, title="u progress", status="IN_PROGRESS", due_at=None)
    session.add_all(
        [
            first_todo,
            second_todo,
            first_progress,
            second_progress,
            unscheduled_todo,
            unscheduled_progress,
        ]
    )
    session.commit()
    selected = TaskWorkQueueService(TaskRepository(session)).get_queue(company.id, AS_OF)
    assert [item.task_id for item in selected.items] == [
        first_progress.id,
        second_progress.id,
        first_todo.id,
        second_todo.id,
        unscheduled_progress.id,
        unscheduled_todo.id,
    ]


def test_full_domain_immutability_and_read_only_sql(session: CountingSession) -> None:
    project = Project(name="Immutable project")
    company = Company(
        project=project,
        name="Immutable company",
        website="https://example.test",
        country="ID",
        city="Denpasar",
        industry="Travel",
        status="ACTIVE",
        notes="company notes",
    )
    contact = Contact(
        company=company,
        first_name="Queue",
        last_name="Contact",
        job_title="Queue reviewer",
        email="queue@example.test",
        phone="+62123456789",
        linkedin_url="https://www.linkedin.com/in/queue-contact",
        country="ID",
        city="Denpasar",
        source="manual-review",
        external_id="queue-contact-1",
        status="ACTIVE",
        notes="contact notes",
    )
    lead = Lead(
        company=company,
        contact=contact,
        status="QUALIFIED",
        source="review",
        notes="lead notes",
    )
    task = Task(
        lead=lead,
        title="Immutable task",
        description="description",
        status="TODO",
        due_at=AS_OF,
    )
    session.add(project)
    session.commit()
    before = domain_snapshot(session)
    before_counts = {name: len(rows) for name, rows in before.items()}
    task_id = task.id
    statements: list[str] = []
    engine = session.get_bind()

    def capture_sql(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        statements.append(statement)

    company_id = company.id
    session.reset_queue_tracking()
    event.listen(engine, "before_cursor_execute", capture_sql)
    try:
        result = TaskWorkQueueService(TaskRepository(session)).get_queue(company_id, AS_OF)
    finally:
        event.remove(engine, "before_cursor_execute", capture_sql)
    assert [item.task_id for item in result.items] == [task_id]
    with Session(engine) as verification_session:
        after = domain_snapshot(verification_session)
    assert after == before
    assert {name: len(rows) for name, rows in after.items()} == before_counts
    assert len(statements) == 1
    sql = statements[0].upper()
    assert sql.lstrip().startswith("SELECT")
    assert " JOIN LEADS " in sql
    assert " JOIN COMPANIES " not in sql
    assert " JOIN CONTACTS " not in sql
    assert " JOIN PROJECTS " not in sql
    assert "FOR UPDATE" not in sql
    for verb in ("INSERT", "UPDATE", "DELETE"):
        assert not sql.lstrip().startswith(verb)
    assert (session.execute_calls, session.commit_calls, session.rollback_calls) == (1, 0, 0)
    assert (session.add_calls, session.flush_calls) == (0, 0)

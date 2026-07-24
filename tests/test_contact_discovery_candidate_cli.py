from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from app.cli import contact_discovery_candidates as candidate_cli
from app.cli.main import app
from app.modules.contact_discovery import (
    ContactDiscoveryCandidateReviewResult,
    ContactDiscoveryCandidateStatus,
    ContactDiscoverySourceType,
)
from app.modules.contact_discovery.schemas import ContactDiscoveryCandidateRead


def candidate(status: str = "DISCOVERED", name: str = "Person") -> ContactDiscoveryCandidateRead:
    now = datetime.now(UTC)
    return ContactDiscoveryCandidateRead(
        id=7,
        company_id=3,
        name=name,
        title="Director",
        email="person@example.com",
        normalized_email="person@example.com",
        phone="+1 555 0100",
        source_url="https://secret.example/team",
        source_type=ContactDiscoverySourceType.TEAM_PAGE,
        confidence=80,
        discovery_status=ContactDiscoveryCandidateStatus(status),
        deduplication_key="secret-key",
        notes="secret-note",
        last_error="secret-error",
        created_at=now,
        updated_at=now,
    )


class FakeSession:
    def __init__(self, *, fail: str | None = None) -> None:
        self.fail = fail
        self.commit_calls = 0
        self.rollback_calls = 0
        self.close_calls = 0

    def commit(self) -> None:
        self.commit_calls += 1
        if self.fail == "commit":
            raise RuntimeError("database path secret")

    def rollback(self) -> None:
        self.rollback_calls += 1
        if self.fail == "rollback":
            raise RuntimeError("rollback secret")

    def close(self) -> None:
        self.close_calls += 1
        if self.fail == "close":
            raise RuntimeError("close secret")


class FakeCompanyRepository:
    def __init__(self, session: object) -> None:
        pass

    def get(self, company_id: int) -> object | None:
        return SimpleNamespace(id=company_id) if company_id == 3 else None


class FakeRepository:
    def __init__(self, session: object) -> None:
        pass


class FakeService:
    def __init__(self, repository: object) -> None:
        pass

    def list_candidates(
        self, company_id: int, limit: int, offset: int, candidate_status: object
    ) -> list[ContactDiscoveryCandidateRead]:
        return [candidate()][:limit]

    def get_candidate(self, company_id: int, candidate_id: int) -> ContactDiscoveryCandidateRead:
        return candidate()

    def mark_reviewed(
        self, company_id: int, candidate_id: int
    ) -> ContactDiscoveryCandidateReviewResult:
        item = candidate("REVIEWED")
        return ContactDiscoveryCandidateReviewResult(
            candidate=item,
            previous_status=ContactDiscoveryCandidateStatus.DISCOVERED,
            current_status=ContactDiscoveryCandidateStatus.REVIEWED,
            changed=True,
        )

    def reject(self, company_id: int, candidate_id: int) -> ContactDiscoveryCandidateReviewResult:
        item = candidate("REJECTED")
        return ContactDiscoveryCandidateReviewResult(
            candidate=item,
            previous_status=ContactDiscoveryCandidateStatus.DISCOVERED,
            current_status=ContactDiscoveryCandidateStatus.REJECTED,
            changed=True,
        )


def dependencies(session: FakeSession) -> dict[str, object]:
    return {
        "session_factory": lambda: session,
        "company_repository_factory": FakeCompanyRepository,
        "repository_factory": FakeRepository,
        "service_factory": FakeService,
    }


def test_candidate_commands_are_registered_without_changing_run() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["contact-discovery", "--help"])
    assert result.exit_code == 0
    assert "candidate" in result.output
    assert "run" in result.output
    candidate_help = runner.invoke(app, ["contact-discovery", "candidate", "--help"])
    assert all(command in candidate_help.output for command in ("list", "show", "review", "reject"))
    assert "promote" not in candidate_help.output


@pytest.mark.parametrize(
    ("arguments", "error"),
    [
        ({"company_id": True, "status": None, "limit": 50}, "Invalid company ID."),
        ({"company_id": 3, "status": "bad", "limit": 50}, "Invalid candidate status."),
        ({"company_id": 3, "status": None, "limit": 0}, "Invalid list limit."),
        ({"company_id": 3, "status": None, "limit": 101}, "Invalid list limit."),
        ({"company_id": 3, "status": None, "limit": 50, "offset": -1}, "Invalid list offset."),
    ],
)
def test_invalid_list_inputs_fail_before_session(arguments: dict[str, object], error: str) -> None:
    outcome = candidate_cli.execute_list_candidates(
        **arguments,  # type: ignore[arg-type]
        session_factory=lambda: pytest.fail("session constructed"),
    )
    assert outcome.error_message == error


@pytest.mark.parametrize("transition", ["review", "reject"])
def test_confirmation_fails_before_all_dependencies(transition: str) -> None:
    outcome = candidate_cli.execute_status_change(
        company_id=3,
        candidate_id=7,
        yes=False,
        transition=transition,  # type: ignore[arg-type]
        session_factory=lambda: pytest.fail("session"),
        company_repository_factory=lambda session: pytest.fail("company repository"),
        repository_factory=lambda session: pytest.fail("repository"),
        service_factory=lambda repository: pytest.fail("service"),
    )
    assert outcome.error_message == "Candidate status change requires --yes."


def test_list_and_show_are_read_only_and_scoped() -> None:
    session = FakeSession()
    listed = candidate_cli.execute_list_candidates(
        company_id=3, status="DISCOVERED", limit=50, **dependencies(session)
    )
    shown = candidate_cli.execute_show_candidate(
        company_id=3, candidate_id=7, **dependencies(session)
    )
    assert listed.exit_code == shown.exit_code == 0
    assert session.commit_calls == session.rollback_calls == 0
    assert session.close_calls == 2


@pytest.mark.parametrize("transition", ["review", "reject"])
def test_mutations_commit_exactly_once(transition: str) -> None:
    session = FakeSession()
    outcome = candidate_cli.execute_status_change(
        company_id=3,
        candidate_id=7,
        yes=True,
        transition=transition,  # type: ignore[arg-type]
        **dependencies(session),
    )
    assert outcome.exit_code == 0
    assert session.commit_calls == 1
    assert session.rollback_calls == 0
    assert session.close_calls == 1


@pytest.mark.parametrize("failure", ["commit", "rollback", "close"])
def test_session_failures_are_sanitized(failure: str) -> None:
    session = FakeSession(fail=failure)

    class FailingService(FakeService):
        def mark_reviewed(self, company_id: int, candidate_id: int) -> object:
            if failure == "rollback":
                raise RuntimeError("SQL SELECT secret.db")
            return super().mark_reviewed(company_id, candidate_id)

    deps = dependencies(session)
    deps["service_factory"] = FailingService
    outcome = candidate_cli.execute_status_change(
        company_id=3, candidate_id=7, yes=True, transition="review", **deps
    )
    assert outcome.error_message == "Candidate status update failed."
    assert "secret" not in str(outcome)


def test_output_omits_forbidden_fields_normalizes_and_bounds(
    capsys: pytest.CaptureFixture[str],
) -> None:
    item = candidate(name="A\n" + "x" * 300)
    candidate_cli._print_candidate(item)
    output = capsys.readouterr().out
    assert "source_url" not in output
    assert "secret.example" not in output
    assert "normalized_email" not in output
    assert "secret-key" not in output
    assert "secret-note" not in output
    assert "\nA\n" not in output
    assert "x" * 161 not in output


def test_mutation_output_contains_no_candidate_pii(capsys: pytest.CaptureFixture[str]) -> None:
    result = FakeService(FakeRepository(object())).mark_reviewed(3, 7)
    candidate_cli._print_review_result(result)
    output = capsys.readouterr().out
    assert "person@example.com" not in output
    assert "+1 555" not in output
    assert "https://" not in output


def test_keyboard_interrupt_is_not_swallowed() -> None:
    class InterruptingService(FakeService):
        def mark_reviewed(self, company_id: int, candidate_id: int) -> object:
            raise KeyboardInterrupt

    deps = dependencies(FakeSession())
    deps["service_factory"] = InterruptingService
    with pytest.raises(KeyboardInterrupt):
        candidate_cli.execute_status_change(
            company_id=3, candidate_id=7, yes=True, transition="review", **deps
        )

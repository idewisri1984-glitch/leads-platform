import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ROOT_EXPORTS = [
    "Company",
    "CompanyCreate",
    "CompanyRead",
    "CompanyRepository",
    "CompanyService",
    "CompanyEnrichment",
    "CompanyDiscoveryCandidate",
    "CompanyDiscoveryRun",
    "DiscoveryProvider",
    "DiscoveryProviderAuthenticationError",
    "DiscoveryProviderConfigurationError",
    "DiscoveryProviderError",
    "DiscoveryProviderQuotaExceededError",
    "DiscoveryProviderRateLimitError",
    "DiscoveryProviderRequestError",
    "DiscoveryProviderResponse",
    "DiscoveryProviderResponseError",
    "DiscoveryProviderResponseTooLargeError",
    "DiscoveryProviderResult",
    "DiscoveryResultAdapterError",
    "SearchProfileDiscoveryAdapterError",
    "SearchProfileDiscoveryDryRunResult",
    "SearchProfileDiscoveryExecutionError",
    "SearchProfileDiscoveryPersistenceError",
    "SearchProfileDiscoveryPersistenceService",
    "SearchProfileDiscoveryPersistResult",
    "SearchProfileDiscoveryProviderError",
    "SearchProfileDiscoveryQueryResult",
    "SearchProfileDiscoveryService",
    "Contact",
    "ContactCreate",
    "ContactRead",
    "ContactRepository",
    "ContactService",
    "CompanyContactDiscoveryState",
    "ContactDiscoveryCandidate",
    "Lead",
    "LeadCreate",
    "LeadRead",
    "LeadRepository",
    "LeadService",
    "Project",
    "ProjectCreate",
    "ProjectRead",
    "ProjectRepository",
    "ProjectService",
    "provider_result_to_ingestion_item",
    "SearchProfile",
    "SearchProfileCreate",
    "SearchProfileQueryGenerationError",
    "SearchProfileQueryGenerator",
    "SearchProfileRead",
    "SearchProfileRepository",
    "SearchProfileRunOptions",
    "SearchProfileService",
    "SearchProfileUpdate",
    "SearchQuery",
    "SearchQueryPreview",
    "SerpApiDiscoveryProvider",
    "Task",
    "TaskCreate",
    "TaskRead",
    "TaskRepository",
    "TaskService",
]
AGENT_EXPORTS = [
    "AgentCompanyApplyConflictError",
    "AgentCompanyApplyConfirmationRequiredError",
    "AgentCompanyApplyConsistencyError",
    "AgentCompanyApplyError",
    "AgentCompanyApplyInput",
    "AgentCompanyApplyInternalError",
    "AgentCompanyApplyInvalidDataError",
    "AgentCompanyApplyNotEligibleError",
    "AgentCompanyApplyNotFoundError",
    "AgentCompanyApplyPersistenceError",
    "AgentCompanyApplyResult",
    "AgentCompanyApplyService",
    "AgentCompanyApplyStaleHandoffError",
    "AgentCompanyPlanBindingError",
    "AgentCompanyPlanDecisionError",
    "AgentCompanyPlanDiscoveryDataError",
    "AgentCompanyPlanError",
    "AgentCompanyPlanInput",
    "AgentCompanyPlanInternalError",
    "AgentCompanyPlanInvalidDataError",
    "AgentCompanyPlanPersistenceError",
    "AgentCompanyPlanProjectNotFoundError",
    "AgentCompanyPlanResult",
    "AgentCompanyPlanSearchProfileNotFoundError",
    "AgentCompanyPlanSearchProfileNotReadyError",
    "AgentCompanyPlanSearchProviderError",
    "AgentCompanyPlanSelectionError",
    "AgentCompanyPlanService",
    "AgentCompanySelectionBinding",
    "AgentCompanySelectionConsistencyError",
    "AgentCompanySelectionError",
    "AgentCompanySelectionInput",
    "AgentCompanySelectionInvalidDataError",
    "AgentCompanySelectionNoCandidatesError",
    "AgentCompanySelectionRunNotFoundError",
    "AgentCompanySelectionRunNotReadyError",
    "AgentCompanySelectionService",
    "AgentContactApplyConflictError",
    "AgentContactApplyConfirmationRequiredError",
    "AgentContactApplyConsistencyError",
    "AgentContactApplyError",
    "AgentContactApplyInput",
    "AgentContactApplyInternalError",
    "AgentContactApplyInvalidDataError",
    "AgentContactApplyNotEligibleError",
    "AgentContactApplyNotFoundError",
    "AgentContactApplyPersistenceError",
    "AgentContactApplyResult",
    "AgentContactApplyService",
    "AgentContactApplyStaleHandoffError",
]
ACTIONS = (
    "import-agent",
    "import-main",
    "root-all",
    "agent-all",
    "database-base",
    "apply-symbol",
    "help",
    "parser-failure",
    "missing-yes",
    "stale-handoff",
    "foreign-scope",
    "success",
    "repeated",
    "service-failure",
    "commit-failure",
)


@pytest.mark.parametrize("action", ACTIONS)
def test_contact_apply_true_fresh_process_import_boundary(action: str) -> None:
    script = r"""
import importlib.abc
import json
import socket
import sys

import sqlalchemy.orm

action = sys.argv[1]
expected_root = json.loads(sys.argv[2])
expected_agent = json.loads(sys.argv[3])
forbidden_prefixes = (
    "app.providers.openai_decision",
    "app.providers.serpapi",
    "app.modules.company_discovery.serpapi_provider",
    "app.modules.contact_discovery.website_provider",
)
blocked_configuration = "app.core.config.settings"
counts = {"settings": 0, "sessions": 0, "network": 0}
original_sessionmaker = sqlalchemy.orm.sessionmaker


def counted_sessionmaker(*args, **kwargs):
    counts["sessions"] += 1
    return original_sessionmaker(*args, **kwargs)


sqlalchemy.orm.sessionmaker = counted_sessionmaker


class BoundaryGuard(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == blocked_configuration or fullname.startswith(
            blocked_configuration + "."
        ):
            counts["settings"] += 1
            raise AssertionError(f"eager settings import: {fullname}")
        if any(
            fullname == prefix or fullname.startswith(prefix + ".")
            for prefix in forbidden_prefixes
        ):
            raise AssertionError(f"forbidden provider import: {fullname}")
        return None


original_connect = socket.socket.connect


def forbidden_connect(self, address):
    counts["network"] += 1
    raise AssertionError(f"network call: {address}")


socket.socket.connect = forbidden_connect
sys.meta_path.insert(0, BoundaryGuard())

if action == "root-all":
    import app.modules as package
    assert package.__all__ == expected_root
elif action == "agent-all":
    import app.modules.agent as package
    assert package.__all__ == expected_agent
elif action == "database-base":
    from app.core.database import Base
    from app.core.database.base import Base as original_base
    assert Base is original_base
    assert "app.core.database.engine" not in sys.modules
    assert "app.core.database.session" not in sys.modules
elif action == "apply-symbol":
    from app.modules.agent import AgentContactApplyService
    assert AgentContactApplyService.__name__ == "AgentContactApplyService"
else:
    import app.cli.agent as agent_cli
    if action == "import-main":
        import app.cli.main
    elif action != "import-agent":
        from typer.testing import CliRunner

        valid = [
            "--project-id", "1",
            "--company-id", "2",
            "--candidate-id", "3",
            "--goal", "Create a qualified outreach lead",
            "--handoff-token", "a" * 64,
        ]
        if action in {"help", "parser-failure", "missing-yes"}:
            def forbidden_session():
                counts["sessions"] += 1
                raise AssertionError("Session was created")

            agent_cli.SessionLocal = forbidden_session
            arguments = ["contact-select", "apply"]
            expected = 0
            if action == "help":
                arguments += ["--help"]
            elif action == "parser-failure":
                arguments += [*valid, "--yes", "--unknown"]
                expected = 2
            else:
                arguments += valid
                expected = 3
            result = CliRunner().invoke(agent_cli.app, arguments)
            assert result.exit_code == expected, (
                result.exit_code,
                result.stdout,
                result.stderr,
                result.exception,
            )
        else:
            from sqlalchemy import create_engine
            from sqlalchemy.orm import Session

            from app.modules.agent.contact_apply import (
                AgentContactApplyConflictError,
                AgentContactApplyInput,
                AgentContactApplyInternalError,
                AgentContactApplyNotFoundError,
                AgentContactApplyPersistenceError,
                AgentContactApplyResult,
                AgentContactApplyStaleHandoffError,
            )
            from app.modules.contact_discovery.models import (
                ContactDiscoveryCandidateStatus,
            )

            def result(*, repeated=False):
                return AgentContactApplyResult(
                    project_id=1,
                    company_id=2,
                    candidate_id=3,
                    contact_id=4,
                    lead_id=5,
                    task_id=6,
                    candidate_status_before=(
                        ContactDiscoveryCandidateStatus.PROMOTED
                        if repeated
                        else ContactDiscoveryCandidateStatus.DISCOVERED
                    ),
                    candidate_status_after=ContactDiscoveryCandidateStatus.PROMOTED,
                    candidate_reviewed=not repeated,
                    candidate_promoted=not repeated,
                    contact_created=not repeated,
                    contact_reused=repeated,
                    lead_created=not repeated,
                    lead_reused=repeated,
                    task_created=not repeated,
                    task_reused=repeated,
                    staging_mutated=not repeated,
                    crm_mutated=not repeated,
                    network_call_count=0,
                    contact_mutation_count=0 if repeated else 1,
                    lead_mutation_count=0 if repeated else 1,
                    task_mutation_count=0 if repeated else 1,
                    handoff_verified=True,
                    human_confirmation_required=True,
                    human_confirmation_received=True,
                )

            class Service:
                calls = 0

                def __init__(self, **kwargs):
                    pass

                def apply(self, data):
                    type(self).calls += 1
                    if action == "stale-handoff":
                        raise AgentContactApplyStaleHandoffError("stale")
                    if action == "foreign-scope":
                        raise AgentContactApplyConflictError("foreign")
                    if action == "service-failure":
                        raise AgentContactApplyInternalError("failed")
                    return result(repeated=action == "repeated" and self.calls > 1)

            agent_cli.AgentContactApplyService = Service
            engine = create_engine("sqlite://")
            sessions = []

            def session_factory():
                counts["sessions"] += 1
                session = Session(engine)
                sessions.append(session)
                if action == "commit-failure":
                    def fail_commit():
                        raise RuntimeError("commit failed")
                    session.commit = fail_commit
                return session

            data = AgentContactApplyInput(
                project_id=1,
                company_id=2,
                candidate_id=3,
                goal="Create a qualified outreach lead",
                handoff_token="a" * 64,
                confirmed=True,
            )
            expected_error = {
                "stale-handoff": AgentContactApplyStaleHandoffError,
                "foreign-scope": AgentContactApplyConflictError,
                "service-failure": AgentContactApplyInternalError,
                "commit-failure": AgentContactApplyPersistenceError,
            }.get(action)
            calls = 2 if action == "repeated" else 1
            for _ in range(calls):
                try:
                    rendered = agent_cli._execute_agent_contact_apply(
                        data, "json", session_factory=session_factory
                    )
                except BaseException as error:
                    if expected_error is None or not isinstance(error, expected_error):
                        raise
                else:
                    if expected_error is not None:
                        raise AssertionError("expected failure")
                    payload = json.loads(rendered)
                    assert payload["network_call_count"] == 0
                    assert payload["contact_id"] == 4
                    assert payload["lead_id"] == 5
                    assert payload["task_id"] == 6
            assert counts["sessions"] == calls

assert counts["settings"] == 0
assert counts["network"] == 0
for loaded in sys.modules:
    assert not any(
        loaded == prefix or loaded.startswith(prefix + ".")
        for prefix in forbidden_prefixes
    ), loaded
"""
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            action,
            __import__("json").dumps(ROOT_EXPORTS),
            __import__("json").dumps(AGENT_EXPORTS),
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr

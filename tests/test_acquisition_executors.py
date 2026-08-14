import ast
from importlib import import_module
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError
from typer.testing import CliRunner


def test_executor_modules_import_without_cli_reverse_dependency() -> None:
    modules = (
        import_module("app.modules.agent.execution"),
        import_module("app.modules.email_draft.execution"),
        import_module("app.modules.crm.export_execution"),
    )

    for module in modules:
        path = Path(module.__file__ or "")
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(not alias.name.startswith("app.cli") for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("app.cli")


def test_agent_cli_uses_application_executor_functions() -> None:
    agent = import_module("app.cli.agent")

    assert agent.execute_company_plan.__module__ == "app.modules.agent.execution"
    assert agent.execute_company_apply.__module__ == "app.modules.agent.execution"
    assert agent.execute_contact_plan.__module__ == "app.modules.agent.execution"
    assert agent.execute_contact_apply.__module__ == "app.modules.agent.execution"


def test_email_and_crm_executors_are_application_level() -> None:
    drafts = import_module("app.modules.email_draft.execution")
    crm = import_module("app.modules.crm.export_execution")

    assert drafts.execute_email_draft_generation.__module__ == drafts.__name__
    assert crm.execute_crm_excel_export.__module__ == crm.__name__


def test_company_plan_cli_delegates_exactly_once(monkeypatch: pytest.MonkeyPatch) -> None:
    agent = import_module("app.cli.agent")
    sentinel = object()
    calls: list[tuple[object, dict[str, object]]] = []

    def executor(data: object, **kwargs: object) -> object:
        calls.append((data, kwargs))
        return sentinel

    monkeypatch.setattr(agent, "execute_company_plan", executor)
    data = object()

    result = agent.execute_agent_company_plan(
        data,
        session_factory=lambda: object(),
        decision_factory_factory=lambda: object(),
        serpapi_client_factory=lambda **kwargs: object(),
    )

    assert result is sentinel
    assert len(calls) == 1
    assert calls[0][0] is data


def test_company_apply_cli_delegates_and_renders_once(monkeypatch: pytest.MonkeyPatch) -> None:
    agent = import_module("app.cli.agent")
    sentinel = object()
    calls: list[object] = []
    renders: list[tuple[object, str]] = []

    def executor(data: object, **kwargs: object) -> object:
        calls.append(data)
        kwargs["before_commit"](sentinel)
        return sentinel

    def render(result: object, output: str) -> str:
        renders.append((result, output))
        return "company-apply"

    monkeypatch.setattr(agent, "execute_company_apply", executor)
    monkeypatch.setattr(agent, "render_agent_company_apply", render)
    data = object()

    result = agent._execute_agent_company_apply(data, "json", session_factory=lambda: object())

    assert result == "company-apply"
    assert calls == [data]
    assert renders == [(sentinel, "json")]


def test_contact_plan_cli_delegates_exactly_once(monkeypatch: pytest.MonkeyPatch) -> None:
    agent = import_module("app.cli.agent")
    sentinel = object()
    calls: list[object] = []

    def executor(data: object, **kwargs: object) -> object:
        calls.append(data)
        return sentinel

    monkeypatch.setattr(agent, "execute_contact_plan", executor)
    data = object()

    result = agent.execute_agent_contact_plan(
        data,
        session_factory=lambda: object(),
        provider_factory=lambda: object(),
    )

    assert result is sentinel
    assert calls == [data]


def test_contact_apply_cli_delegates_and_renders_once(monkeypatch: pytest.MonkeyPatch) -> None:
    agent = import_module("app.cli.agent")
    sentinel = object()
    calls: list[object] = []
    renders: list[tuple[object, str]] = []

    def executor(data: object, **kwargs: object) -> object:
        calls.append(data)
        kwargs["before_commit"](sentinel)
        return sentinel

    def render(result: object, output: str) -> str:
        renders.append((result, output))
        return "contact-apply"

    monkeypatch.setattr(agent, "execute_contact_apply", executor)
    monkeypatch.setattr(agent, "render_agent_contact_apply", render)
    data = object()

    result = agent._execute_agent_contact_apply(data, "json", session_factory=lambda: object())

    assert result == "contact-apply"
    assert calls == [data]
    assert renders == [(sentinel, "json")]


def test_email_draft_cli_delegates_and_uses_prepared_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cli = import_module("app.cli.email_draft")
    execution = import_module("app.modules.email_draft.execution")
    sentinel = object()
    calls: list[object] = []

    def executor(data: object, **kwargs: object) -> object:
        calls.append(data)
        kwargs["before_commit"](sentinel)
        return sentinel

    monkeypatch.setattr(execution, "execute_email_draft_generation", executor)
    monkeypatch.setattr(cli, "render_email_draft", lambda result, output: "draft-output")
    data = object()

    result = cli.execute_generate(data, "json", session_factory=lambda: object())

    assert result == "draft-output"
    assert calls == [data]


def test_crm_cli_delegates_exactly_once(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cli = import_module("app.cli.crm")
    execution = import_module("app.modules.crm.export_execution")
    sentinel = object()
    calls: list[dict[str, object]] = []

    def executor(**kwargs: object) -> object:
        calls.append(kwargs)
        return sentinel

    monkeypatch.setattr(execution, "execute_crm_excel_export", executor)
    output_file = tmp_path / "crm.xlsx"

    result = cli._export_excel(3, 5, output_file, overwrite=True)

    assert result is sentinel
    assert len(calls) == 1
    assert calls[0]["project_id"] == 3
    assert calls[0]["company_id"] == 5
    assert calls[0]["output_file"] == output_file
    assert calls[0]["overwrite"] is True


def test_apply_confirmation_remains_cli_owned(monkeypatch: pytest.MonkeyPatch) -> None:
    agent = import_module("app.cli.agent")
    calls: list[str] = []
    monkeypatch.setattr(
        agent, "execute_company_apply", lambda *args, **kwargs: calls.append("company")
    )
    monkeypatch.setattr(
        agent, "execute_contact_apply", lambda *args, **kwargs: calls.append("contact")
    )
    runner = CliRunner()

    company = runner.invoke(
        agent.app,
        [
            "company-select",
            "apply",
            "--project-id",
            "1",
            "--discovery-run-id",
            "2",
            "--candidate-id",
            "3",
        ],
    )
    contact = runner.invoke(
        agent.app,
        [
            "contact-select",
            "apply",
            "--project-id",
            "1",
            "--company-id",
            "2",
            "--candidate-id",
            "3",
            "--goal",
            "goal",
            "--handoff-token",
            "token",
        ],
    )

    assert company.exit_code == contact.exit_code == 3
    assert calls == []


def test_email_draft_preparation_failure_rolls_back_before_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cli = import_module("app.cli.email_draft")
    service_module = import_module("app.modules.email_draft.service")

    class Session:
        commits = 0
        rollbacks = 0
        closes = 0
        adds = 0

        def commit(self) -> None:
            self.commits += 1

        def rollback(self) -> None:
            self.rollbacks += 1

        def close(self) -> None:
            self.closes += 1

        def add(self, value: object) -> None:
            self.adds += 1

    class Generator:
        closes = 0

        def close(self) -> None:
            self.closes += 1

    class HostileService:
        def __init__(self, **kwargs: object) -> None:
            pass

        def generate(self, data: object) -> object:
            return object()

    session = Session()
    generator = Generator()
    monkeypatch.setattr(service_module, "EmailDraftService", HostileService)

    with pytest.raises(service_module.EmailDraftInternalError):
        cli.execute_generate(
            object(),
            "json",
            session_factory=lambda: session,
            generator_factory=lambda: generator,
        )

    assert (session.commits, session.rollbacks, session.closes, session.adds) == (0, 1, 1, 0)
    assert generator.closes == 1


def test_company_plan_provider_error_has_no_sensitive_context() -> None:
    execution = import_module("app.modules.agent.execution")
    company_plan = import_module("app.modules.agent.company_plan")

    def fail() -> object:
        raise RuntimeError("sensitive provider configuration")

    with pytest.raises(company_plan.AgentCompanyPlanSearchProviderError) as caught:
        execution._provider_construction(fail)

    error = caught.value
    assert str(error) == "Company search provider failed."
    assert error.__cause__ is error.__context__ is None
    assert "sensitive" not in str(error).lower()
    assert "sensitive" not in repr(error).lower()
    assert all("sensitive" not in str(value).lower() for value in error.args)


def test_company_apply_conflict_has_no_sensitive_context() -> None:
    execution = import_module("app.modules.agent.execution")
    company_apply = import_module("app.modules.agent.company_apply")

    class Session:
        rollbacks = 0
        closes = 0

        def commit(self) -> None:
            raise IntegrityError("sensitive statement", {}, RuntimeError("sensitive db"))

        def rollback(self) -> None:
            self.rollbacks += 1

        def close(self) -> None:
            self.closes += 1

    class Service:
        def __init__(self, **kwargs: object) -> None:
            pass

        def apply(self, data: object) -> object:
            return object()

    session = Session()
    components = execution.CompanyApplyComponents(
        staging_repository=lambda current: object(),
        company_repository=lambda current: object(),
        review_service=lambda repository: object(),
        promotion_service=lambda staging, companies: object(),
        apply_service=Service,
    )

    with pytest.raises(company_apply.AgentCompanyApplyConflictError) as caught:
        execution.execute_company_apply(
            object(), session_factory=lambda: session, components=components
        )

    error = caught.value
    assert str(error) == "Agent company apply persistence conflict."
    assert error.__cause__ is error.__context__ is None
    assert "sensitive" not in str(error).lower()
    assert "sensitive" not in repr(error).lower()
    assert all("sensitive" not in str(value).lower() for value in error.args)
    assert (session.rollbacks, session.closes) == (1, 1)

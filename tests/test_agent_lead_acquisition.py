from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.modules.agent.lead_acquisition import (
    CompanyApplyOutcome,
    CompanyCompletionOutcome,
    CompanyPlanOutcome,
    ContactApplyOutcome,
    ContactPlanOutcome,
    DraftOutcome,
    ExportOutcome,
    LeadAcquisitionCompanyUnavailableError,
    LeadAcquisitionContactUnavailableError,
    LeadAcquisitionDependencies,
    LeadAcquisitionDraftFailure,
    LeadAcquisitionInput,
    LeadAcquisitionProviderStopError,
    LeadAcquisitionService,
    LeadAcquisitionStatus,
)


@dataclass
class Scenario:
    modes: list[str]
    calls: list[tuple[str, int]] = field(default_factory=list)
    completed: dict[int, DraftOutcome] = field(default_factory=dict)
    draft_failures: set[int] = field(default_factory=set)
    company_emails: dict[int, str | None] = field(default_factory=dict)
    recoverable_company_emails: dict[int, str] = field(default_factory=dict)
    index: int = 0

    def dependencies(self) -> LeadAcquisitionDependencies:
        return LeadAcquisitionDependencies(
            company_plan=self.company_plan,
            company_apply=self.company_apply,
            contact_plan=self.contact_plan,
            contact_apply=self.contact_apply,
            company_email=self.company_email,
            company_complete=self.company_complete,
            draft_generate=self.draft_generate,
            export_crm=self.export_crm,
            company_enrich=self.company_enrich,
        )

    @property
    def mode(self) -> str:
        return self.modes[min(self.index - 1, len(self.modes) - 1)]

    def company_plan(self, _project: int, _profile: int, _goal: str) -> CompanyPlanOutcome:
        self.index += 1
        self.calls.append(("company_plan", self.index))
        mode = self.mode
        if mode == "provider_stop":
            raise LeadAcquisitionProviderStopError("stop")
        selected = None if mode == "no_selection" else self.index
        return CompanyPlanOutcome(self.index, 5, selected, 1, int(selected is not None))

    def company_apply(self, _project: int, _run: int, candidate: int) -> CompanyApplyOutcome:
        self.calls.append(("company_apply", candidate))
        if self.mode == "company_unavailable":
            raise LeadAcquisitionCompanyUnavailableError("unavailable")
        created = self.mode not in {"duplicate", "person_reused", "company_reused"}
        return CompanyApplyOutcome(candidate, created, not created)

    def contact_plan(self, _project: int, company: int, _goal: str) -> ContactPlanOutcome:
        self.calls.append(("contact_plan", company))
        if self.mode == "contact_unavailable":
            raise LeadAcquisitionContactUnavailableError("unavailable")
        if self.mode in {
            "company",
            "company_reused",
            "company_conflict",
            "no_email",
            "contact_no_email",
            "recover_company_email",
            "draft_failure",
        }:
            email = "broken" if self.mode == "contact_no_email" else None
            return ContactPlanOutcome(company, email, "a" * 64, 1, 1)
        return ContactPlanOutcome(company, f"person{company}@studio.test", "a" * 64, 1, 1)

    def contact_apply(
        self, _project: int, company: int, _candidate: int, _goal: str, _token: str
    ) -> ContactApplyOutcome:
        self.calls.append(("contact_apply", company))
        created = self.mode != "person_reused"
        return ContactApplyOutcome(
            company + 100,
            company + 200,
            company + 300,
            created,
            not created,
            created,
            not created,
            created,
            not created,
        )

    def company_email(self, company: int) -> str | None:
        self.calls.append(("company_email", company))
        if company in self.company_emails:
            return self.company_emails[company]
        return (
            None
            if self.mode in {"no_email", "recover_company_email"}
            else f"office{company}@studio.test"
        )

    def company_enrich(self, company: int) -> None:
        self.calls.append(("company_enrich", company))
        recovered = self.recoverable_company_emails.get(company)
        if recovered is not None:
            self.company_emails[company] = recovered

    def company_complete(
        self, _project: int, company: int, _email: str
    ) -> CompanyCompletionOutcome:
        self.calls.append(("company_complete", company))
        if self.mode == "company_conflict":
            raise RuntimeError("typed company-scoped conflict")
        created = self.mode != "company_reused"
        return CompanyCompletionOutcome(
            company + 200,
            company + 300,
            created,
            not created,
            created,
            not created,
        )

    def draft_generate(
        self,
        _project: int,
        company: int,
        contact: int | None,
        lead: int,
        _task: int,
        _goal: str,
    ) -> DraftOutcome:
        self.calls.append(("draft_generate", company))
        if self.mode == "draft_failure" or company in self.draft_failures:
            raise LeadAcquisitionDraftFailure("failed")
        email = (
            f"person{company}@studio.test"
            if contact
            else self.company_emails.get(company, f"office{company}@studio.test")
        )
        created = self.mode not in {"duplicate", "person_reused", "company_reused"}
        created = created and company not in self.completed
        outcome = DraftOutcome(
            company + 400,
            contact,
            lead,
            email,
            "DRAFT",
            created,
            not created,
        )
        self.completed[company] = outcome
        return outcome

    def export_crm(self, _project: int, output: Path, overwrite: bool) -> ExportOutcome:
        self.calls.append(("export", int(overwrite)))
        return ExportOutcome(output)


def acquire(
    scenario: Scenario,
    limit: int,
    *,
    export: Path | None = None,
    overwrite_export: bool = False,
):
    return LeadAcquisitionService(scenario.dependencies()).acquire(
        LeadAcquisitionInput(
            project_id=1,
            search_profile_id=3,
            limit=limit,
            goal="Find relevant design firms",
            export_file=export,
            overwrite_export=overwrite_export,
        )
    )


@pytest.mark.parametrize("limit", [1, 3, 10])
def test_exact_limits_never_over_complete(limit: int) -> None:
    scenario = Scenario(["person"] * limit)
    result = acquire(scenario, limit)
    assert result.completed_count == limit
    assert result.attempt_count == limit
    assert result.status is LeadAcquisitionStatus.COMPLETE
    assert len(result.completed_draft_ids) == limit
    assert scenario.index == limit


def test_budget_exhaustion_stops_at_exact_formula() -> None:
    scenario = Scenario(["no_selection"])
    result = acquire(scenario, 10)
    assert (result.attempt_count, result.attempt_budget) == (50, 50)
    assert result.completed_count == 0
    assert result.no_selection_count == 50
    assert result.budget_exhausted is True
    assert result.status is LeadAcquisitionStatus.PARTIAL_BUDGET_EXHAUSTED


def test_mixed_batch_has_exact_counters_and_recipient_paths() -> None:
    scenario = Scenario(
        [
            "no_selection",
            "duplicate",
            "person",
            "company",
            "no_email",
            "contact_no_email",
            "draft_failure",
            "person",
        ]
    )
    result = acquire(scenario, 4)
    assert result.completed_count == 4
    assert (result.person_scoped_count, result.company_scoped_count) == (2, 2)
    assert result.duplicates_skipped == 1
    assert result.no_selection_count == 1
    assert result.no_contact_count == 0
    assert result.no_email_count == 1
    assert result.draft_failure_count == 1
    assert result.attempt_count == 8


def test_contact_email_wins_and_contact_apply_runs_once() -> None:
    scenario = Scenario(["person"])
    scenario.company_emails[1] = "company@studio.test"
    result = acquire(scenario, 1)
    assert result.person_scoped_count == 1
    assert result.company_scoped_count == 0
    assert result.completed_contact_ids == (101,)
    assert [name for name, _ in scenario.calls].count("contact_apply") == 1
    assert "company_email" not in [name for name, _ in scenario.calls]


@pytest.mark.parametrize("mode", ["company", "contact_no_email"])
def test_company_fallback_persists_selected_contact_without_email(mode: str) -> None:
    scenario = Scenario([mode])
    result = acquire(scenario, 1)
    assert result.company_scoped_count == 1
    assert result.completed_contact_ids == ()
    assert [name for name, _ in scenario.calls].count("contact_apply") == 1
    assert result.contacts_created == 1
    assert [name for name, _ in scenario.calls].count("company_complete") == 1


def test_no_email_never_generates_draft() -> None:
    scenario = Scenario(["no_email"])
    result = acquire(scenario, 1)
    assert result.completed_count == 0
    assert result.no_email_count == result.attempt_budget
    assert "draft_generate" not in [name for name, _ in scenario.calls]
    assert result.contacts_created == result.attempt_budget
    assert result.no_contact_count == 0


def test_company_enrichment_runs_once_before_final_no_email() -> None:
    scenario = Scenario(["no_email"])

    result = acquire(scenario, 1)

    assert result.completed_count == 0
    enrich_calls = [value for name, value in scenario.calls if name == "company_enrich"]
    assert enrich_calls == list(range(1, result.attempt_budget + 1))


def test_company_enrichment_recovers_trusted_email_before_company_completion() -> None:
    scenario = Scenario(["recover_company_email"])
    scenario.recoverable_company_emails[1] = "hello@studio.test"

    result = acquire(scenario, 1)

    assert result.completed_count == 1
    assert result.company_scoped_count == 1
    assert [name for name, _ in scenario.calls].count("company_enrich") == 1
    assert [name for name, _ in scenario.calls].count("company_complete") == 1


def test_restart_skips_complete_target_and_continues_to_new_target() -> None:
    scenario = Scenario(["duplicate", "person"])
    result = acquire(scenario, 1)
    assert result.completed_count == 1
    assert result.duplicates_skipped == 1
    assert result.completed_company_ids == (2,)
    assert result.attempt_count == 2


def test_draft_failure_is_recoverable_and_not_complete() -> None:
    scenario = Scenario(["draft_failure", "person"])
    result = acquire(scenario, 1)
    assert result.draft_failure_count == 1
    assert result.completed_company_ids == (2,)
    assert result.attempt_count == 2


def test_provider_stop_preserves_partial_success() -> None:
    scenario = Scenario(["person", "provider_stop"])
    result = acquire(scenario, 2)
    assert result.completed_count == 1
    assert result.status is LeadAcquisitionStatus.PARTIAL_PROVIDER_STOP
    assert result.budget_exhausted is False


def test_unavailable_apply_paths_are_not_forced() -> None:
    company = Scenario(["company_unavailable"])
    company_result = acquire(company, 1)
    assert company_result.no_selection_count == company_result.attempt_budget
    assert "contact_plan" not in [name for name, _ in company.calls]

    contact = Scenario(["contact_unavailable"])
    contact_result = acquire(contact, 1)
    assert contact_result.company_scoped_count == 1
    assert "contact_apply" not in [name for name, _ in contact.calls]


def test_company_scoped_conflict_is_fatal_without_draft() -> None:
    scenario = Scenario(["company_conflict"])
    with pytest.raises(RuntimeError, match="typed company-scoped conflict"):
        acquire(scenario, 1)
    assert "draft_generate" not in [name for name, _ in scenario.calls]


def test_export_runs_after_loop_and_does_not_affect_completion(tmp_path: Path) -> None:
    scenario = Scenario(["person"])
    output = tmp_path / "crm.xlsx"
    result = acquire(scenario, 1, export=output)
    assert result.export_file == str(output)
    assert result.export_status.value == "SUCCEEDED"
    assert scenario.calls[-1] == ("export", 0)


def test_no_send_dependencies_exist_and_target_context_is_isolated() -> None:
    scenario = Scenario(["person", "person"])
    result = acquire(scenario, 2)
    assert result.completed_company_ids == (1, 2)
    draft_calls = [identifier for name, identifier in scenario.calls if name == "draft_generate"]
    assert draft_calls == [1, 2]
    assert not any("smtp" in name or "send" in name for name, _ in scenario.calls)


def test_import_is_settings_provider_session_and_cli_safe() -> None:
    script = """
import json
import sys
import app.modules.agent.lead_acquisition
import app.modules.lead_acquisition_execution
forbidden = sorted(name for name in sys.modules if name.startswith((
    'app.cli', 'app.core.config.settings', 'app.providers.openai',
    'app.providers.serpapi', 'app.providers.smtp', 'app.core.database.engine',
    'app.core.database.session',
)))
print(json.dumps(forbidden))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script], check=True, capture_output=True, text=True
    )
    assert json.loads(completed.stdout) == []


def test_stale_draft_failure_calls_canonical_boundary_and_never_completes() -> None:
    scenario = Scenario(["draft_failure"])

    result = acquire(scenario, 1)

    assert result.completed_count == 0
    assert result.drafts_created == 0
    assert result.drafts_reused == 0
    assert result.draft_failure_count == result.attempt_budget
    assert "draft_generate" in [name for name, _ in scenario.calls]


def test_current_canonical_draft_is_reused_without_counting_as_new() -> None:
    scenario = Scenario(["duplicate"])
    dependencies = replace(
        scenario.dependencies(),
        company_plan=lambda _project, _profile, _goal: CompanyPlanOutcome(1, 1, 1, 0, 0),
    )

    result = LeadAcquisitionService(dependencies).acquire(
        LeadAcquisitionInput(
            project_id=1,
            search_profile_id=3,
            limit=1,
            goal="Find relevant design firms",
        )
    )

    assert result.completed_count == 0
    assert result.drafts_created == 0
    assert result.drafts_reused == result.attempt_budget
    assert result.duplicates_skipped == result.attempt_budget
    assert scenario.completed[1].draft_id == 401


def test_authoritative_provider_counts_are_summed_without_guessing() -> None:
    scenario = Scenario(["person"])
    dependencies = replace(
        scenario.dependencies(),
        company_plan=lambda _project, _profile, _goal: CompanyPlanOutcome(1, 2, 1, 3, 2),
        contact_plan=lambda _project, _company, _goal: ContactPlanOutcome(
            1, "person1@studio.test", "a" * 64, 4, 1
        ),
    )

    result = LeadAcquisitionService(dependencies).acquire(
        LeadAcquisitionInput(
            project_id=1,
            search_profile_id=3,
            limit=1,
            goal="Find relevant design firms",
        )
    )

    assert result.company_discovery_call_count == 3
    assert result.company_decision_call_count == 2
    assert result.contact_discovery_call_count == 4
    assert result.contact_decision_call_count == 1


def test_provider_failure_before_invocation_reports_zero_calls() -> None:
    scenario = Scenario(["provider_stop"])

    result = acquire(scenario, 1)

    assert result.company_discovery_call_count == 0
    assert result.company_decision_call_count == 0


def test_mixed_created_and_reused_entity_counters_are_exact() -> None:
    scenario = Scenario(["person", "person_reused", "company"])

    result = acquire(scenario, 2)

    assert (result.companies_created, result.companies_reused) == (2, 1)
    assert (result.contacts_created, result.contacts_reused) == (2, 1)
    assert (result.leads_created, result.leads_reused) == (3, 1)
    assert (result.tasks_created, result.tasks_reused) == (3, 1)
    assert (result.drafts_created, result.drafts_reused) == (2, 1)
    assert len(result.completed_task_ids) == 2


def test_export_overwrite_is_explicit_and_failure_preserves_acquisition(tmp_path: Path) -> None:
    allowed = Scenario(["person"])
    output = tmp_path / "allowed.xlsx"
    successful = acquire(allowed, 1, export=output, overwrite_export=True)
    assert successful.completed_count == 1
    assert successful.export_status.value == "SUCCEEDED"
    assert allowed.calls[-1] == ("export", 1)

    denied = Scenario(["person"])

    def fail_existing(_project: int, _output: Path, overwrite: bool) -> ExportOutcome:
        assert overwrite is False
        raise FileExistsError("exists")

    failed = LeadAcquisitionService(
        replace(denied.dependencies(), export_crm=fail_existing)
    ).acquire(
        LeadAcquisitionInput(
            project_id=1,
            search_profile_id=3,
            limit=1,
            goal="Find relevant design firms",
            export_file=tmp_path / "existing.xlsx",
        )
    )
    assert failed.completed_count == 1
    assert failed.export_status.value == "FAILED"


def test_result_rejects_duplicate_completed_identifiers() -> None:
    valid = acquire(Scenario(["person", "person"]), 2)
    payload = valid.model_dump()
    payload["completed_task_ids"] = (valid.completed_task_ids[0],) * 2

    with pytest.raises(ValidationError, match="Completed identifiers are invalid"):
        type(valid)(**payload)

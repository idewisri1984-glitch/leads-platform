from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.modules.agent.company_selection import AgentCompanySelectionService
from app.modules.agent.lead_acquisition import (
    CompanyApplyOutcome,
    CompanyCompletionOutcome,
    CompanyPlanOutcome,
    ContactApplyOutcome,
    ContactPlanOutcome,
    DraftOutcome,
    ExportOutcome,
    LeadAcquisitionDependencies,
    LeadAcquisitionInput,
    LeadAcquisitionService,
)
from app.modules.company_discovery.models import (
    CompanyDiscoveryCandidateStatus,
    CompanyDiscoveryRunStatus,
)
from app.modules.company_discovery.staging_service_schemas import (
    CompanyDiscoveryStagingCandidatePreview,
)
from app.modules.search_profile.query_generation import SearchProfileQueryGenerator
from app.modules.search_profile.schemas import SearchProfileRead, SearchProfileRunOptions
from app.providers.openai_decision import (
    OpenAICompanyFit,
    OpenAIDecisionKind,
    OpenAIDecisionResult,
)


@dataclass
class _Run:
    id: int = 7
    project_id: int = 3
    run_status: CompanyDiscoveryRunStatus = CompanyDiscoveryRunStatus.SUCCEEDED


@dataclass
class _Candidate:
    id: int
    candidate_status: CompanyDiscoveryCandidateStatus
    promoted_company_id: int | None
    project_id: int = 3
    last_seen_run_id: int = 7
    name: str = "Goodrich"
    website: str = "https://goodrich.nyc"
    country_code: str = "US"
    identity_key: str = "website:goodrich.nyc"
    best_position: int = 1


class _Repository:
    def __init__(self, candidates: tuple[_Candidate, ...]) -> None:
        self.candidates = candidates

    def get_run(self, run_id: int) -> _Run | None:
        return _Run() if run_id == 7 else None

    def list_candidates_for_run(
        self,
        project_id: int,
        run_id: int,
        limit: int,
        candidate_status: CompanyDiscoveryCandidateStatus | None = None,
    ) -> tuple[_Candidate, ...]:
        assert (project_id, run_id) == (3, 7)
        return tuple(
            candidate
            for candidate in self.candidates
            if candidate_status is None or candidate.candidate_status is candidate_status
        )[:limit]

    def get_candidate_for_project(
        self,
        project_id: int,
        candidate_id: int,
    ) -> _Candidate | None:
        return next(
            (
                candidate
                for candidate in self.candidates
                if candidate.project_id == project_id and candidate.id == candidate_id
            ),
            None,
        )


def test_query_template_offset_rotates_existing_profile_templates() -> None:
    profile = SearchProfileRead(
        id=3,
        project_id=1,
        name="Eight templates",
        description=None,
        product_or_service="Custom interiors",
        target_customer_types=["interior design studios"],
        target_industries=[],
        positive_keywords=[],
        negative_keywords=[],
        countries=["United States"],
        cities=[],
        languages=[],
        query_templates=[f"template-{index} {{target_customer_type}}" for index in range(1, 9)],
        result_limit=5,
        max_queries_per_run=10,
        total_result_ceiling=50,
        enabled=True,
    )
    generator = SearchProfileQueryGenerator()

    queries = [
        generator.generate_preview(
            profile,
            SearchProfileRunOptions(
                max_queries=1,
                result_limit_per_query=5,
                total_result_ceiling=5,
                query_template_offset=offset,
            ),
        ).queries[0]
        for offset in range(8)
    ]

    assert [query.source_template for query in queries] == profile.query_templates
    wrapped = generator.generate_preview(
        profile,
        SearchProfileRunOptions(max_queries=1, query_template_offset=8),
    )
    assert wrapped.queries[0].source_template == profile.query_templates[0]


def test_recovery_selection_preserves_promoted_company_and_safe_evidence() -> None:
    promoted = _Candidate(
        id=16,
        candidate_status=CompanyDiscoveryCandidateStatus.PROMOTED,
        promoted_company_id=77,
    )
    evidence = CompanyDiscoveryStagingCandidatePreview(
        name="Goodrich",
        website="https://goodrich.nyc",
        website_identity="goodrich.nyc",
        country_code="US",
        best_position=1,
        identity_key="website:goodrich.nyc",
        city="New York",
        industry="Interior Design",
        snippet="Luxury residential and hospitality interior design studio.",
        website_summary="Portfolio includes residential and hotel interiors.",
    )

    selection = AgentCompanySelectionService(_Repository((promoted,))).prepare_for_recovery(
        project_id=3,
        run_id=7,
        goal="Find relevant design firms",
        candidate_evidence=(evidence,),
    )

    assert selection.bindings[0].candidate_id == 16
    decision = OpenAIDecisionResult(
        decision=OpenAIDecisionKind.SELECT,
        selected_candidate_index=1,
        confidence=0.8,
        company_fit=OpenAICompanyFit.HIGH,
        rationale="Relevant design studio",
        next_action_title="Review company",
        next_action_description="Review the company before outreach.",
        human_review_required=True,
    )
    assert (
        AgentCompanySelectionService(_Repository((promoted,))).resolve_selected_existing_company_id(
            selection,
            decision,
        )
        == 77
    )
    request_candidate = selection.request.candidates[0]
    assert request_candidate.city == "New York"
    assert request_candidate.industry == "Interior Design"
    assert request_candidate.snippet == evidence.snippet
    assert request_candidate.website_summary == evidence.website_summary


def test_recovery_selection_mixes_promoted_and_discovered_deterministically() -> None:
    promoted = _Candidate(
        id=16,
        candidate_status=CompanyDiscoveryCandidateStatus.PROMOTED,
        promoted_company_id=77,
        best_position=2,
    )
    discovered = _Candidate(
        id=17,
        candidate_status=CompanyDiscoveryCandidateStatus.DISCOVERED,
        promoted_company_id=None,
        name="New Studio",
        website="https://new.example",
        identity_key="website:new.example",
        best_position=1,
    )

    selection = AgentCompanySelectionService(
        _Repository((promoted, discovered))
    ).prepare_for_recovery(
        project_id=3,
        run_id=7,
        goal="Find relevant design firms",
    )

    assert tuple(binding.candidate_id for binding in selection.bindings) == (17, 16)
    assert tuple(candidate.index for candidate in selection.request.candidates) == (1, 2)


def test_stage6a_advances_after_repeat_and_recovers_existing_company() -> None:
    offsets: list[int] = []
    applied: list[int] = []
    contact_plans: list[int] = []

    def plan_attempt(
        _project_id: int,
        _profile_id: int,
        _goal: str,
        offset: int,
        excluded: tuple[str, ...],
    ) -> CompanyPlanOutcome:
        offsets.append(offset)
        if offset == 0:
            assert excluded == ()
            return CompanyPlanOutcome(
                10,
                1,
                None,
                1,
                1,
                "a" * 64,
                False,
                None,
                (16,),
                ("https://repeat.example",),
            )
        assert excluded == ("a" * 64,)
        return CompanyPlanOutcome(
            11,
            1,
            17,
            1,
            1,
            "b" * 64,
            False,
            77,
            (17,),
            ("https://recovery.example",),
        )

    def company_apply(_project: int, _run: int, candidate: int) -> CompanyApplyOutcome:
        applied.append(candidate)
        raise AssertionError("Existing promoted company must not be applied again.")

    def contact_plan(_project: int, company: int, _goal: str) -> ContactPlanOutcome:
        contact_plans.append(company)
        return ContactPlanOutcome(None, None, None, 0, 0)

    dependencies = LeadAcquisitionDependencies(
        company_plan=lambda _project, _profile, _goal: CompanyPlanOutcome(1, 0, None, 1, 0),
        company_apply=company_apply,
        contact_plan=contact_plan,
        contact_apply=lambda *_args: ContactApplyOutcome(
            1, 1, 1, True, False, True, False, True, False
        ),
        company_email=lambda company: "office@recovery.example" if company == 77 else None,
        company_complete=lambda _project, _company, _email: CompanyCompletionOutcome(
            201, 301, True, False, True, False
        ),
        draft_generate=lambda *_args: DraftOutcome(
            401,
            None,
            201,
            "office@recovery.example",
            "DRAFT",
            True,
            False,
        ),
        export_crm=lambda _project, output, _overwrite: ExportOutcome(Path(output)),
        company_plan_attempt=plan_attempt,
        company_already_complete=lambda _project, _company: False,
    )

    result = LeadAcquisitionService(dependencies).acquire(
        LeadAcquisitionInput(
            project_id=1,
            search_profile_id=3,
            limit=1,
            goal="Find relevant design firms",
        )
    )

    assert offsets == [0, 1]
    assert applied == []
    assert contact_plans == [77]
    assert result.completed_count == 1
    assert result.companies_created == 0
    assert result.companies_reused == 1
    assert result.company_decision_call_count == 2


def test_suppressed_identical_request_saves_decision_call_and_downstream_work() -> None:
    attempts: list[tuple[int, tuple[str, ...]]] = []

    def plan_attempt(
        _project: int,
        _profile: int,
        _goal: str,
        offset: int,
        excluded: tuple[str, ...],
    ) -> CompanyPlanOutcome:
        attempts.append((offset, excluded))
        return CompanyPlanOutcome(
            offset + 1,
            1,
            None,
            1,
            1 if offset == 0 else 0,
            "c" * 64,
            offset > 0,
            None,
            (19,),
            ("https://same.example",),
        )

    dependencies = LeadAcquisitionDependencies(
        company_plan=lambda _project, _profile, _goal: CompanyPlanOutcome(1, 0, None, 1, 0),
        company_apply=lambda *_args: CompanyApplyOutcome(1, True, False),
        contact_plan=lambda *_args: ContactPlanOutcome(None, None, None, 0, 0),
        contact_apply=lambda *_args: ContactApplyOutcome(
            1, 1, 1, True, False, True, False, True, False
        ),
        company_email=lambda _company: None,
        company_complete=lambda *_args: CompanyCompletionOutcome(1, 1, True, False, True, False),
        draft_generate=lambda *_args: DraftOutcome(1, None, 1, "a@b.test", "DRAFT", True, False),
        export_crm=lambda _project, output, _overwrite: ExportOutcome(Path(output)),
        company_plan_attempt=plan_attempt,
    )

    result = LeadAcquisitionService(dependencies).acquire(
        LeadAcquisitionInput(
            project_id=1,
            search_profile_id=3,
            limit=1,
            goal="Find relevant design firms",
        )
    )

    assert len(attempts) == result.attempt_budget == 10
    assert result.company_decision_call_count == 1
    assert result.duplicates_skipped == 9
    assert attempts[1][1] == ("c" * 64,)


def test_already_complete_promoted_company_skips_contact_pipeline() -> None:
    contact_calls: list[int] = []

    def contact_plan(_project: int, company: int, _goal: str) -> ContactPlanOutcome:
        contact_calls.append(company)
        return ContactPlanOutcome(None, None, None, 0, 0)

    dependencies = LeadAcquisitionDependencies(
        company_plan=lambda _project, _profile, _goal: CompanyPlanOutcome(1, 0, None, 1, 0),
        company_apply=lambda *_args: CompanyApplyOutcome(1, True, False),
        contact_plan=contact_plan,
        contact_apply=lambda *_args: ContactApplyOutcome(
            1, 1, 1, True, False, True, False, True, False
        ),
        company_email=lambda _company: None,
        company_complete=lambda *_args: CompanyCompletionOutcome(1, 1, True, False, True, False),
        draft_generate=lambda *_args: DraftOutcome(1, None, 1, "a@b.test", "DRAFT", True, False),
        export_crm=lambda _project, output, _overwrite: ExportOutcome(Path(output)),
        company_plan_attempt=lambda *_args: CompanyPlanOutcome(
            1,
            1,
            20,
            1,
            1,
            "d" * 64,
            False,
            88,
            (20,),
            ("https://complete.example",),
        ),
        company_already_complete=lambda project, company: (project, company) == (1, 88),
    )

    result = LeadAcquisitionService(dependencies).acquire(
        LeadAcquisitionInput(
            project_id=1,
            search_profile_id=3,
            limit=1,
            goal="Find relevant design firms",
        )
    )

    assert contact_calls == []
    assert result.completed_count == 0
    assert result.companies_created == 0
    assert result.drafts_reused == result.attempt_budget
    assert result.duplicates_skipped == result.attempt_budget

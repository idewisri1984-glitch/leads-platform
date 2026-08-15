from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from app.modules.agent.lead_acquisition import (
    DraftOutcome,
    LeadAcquisitionInput,
    LeadAcquisitionResult,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.modules.agent.company_plan import DecisionBoundary


class SessionFactory(Protocol):
    def __call__(self) -> Session: ...


class _DecisionFactory:
    def __init__(self) -> None:
        self._client: Any | None = None

    def __call__(self) -> DecisionBoundary:
        if self._client is None:
            from app.core.config.settings import settings
            from app.providers.openai_decision import OpenAIDecisionClient

            self._client = OpenAIDecisionClient(
                api_key=settings.openai_api_key,
                model=settings.openai_model,
                timeout_seconds=settings.openai_timeout_seconds,
                max_output_tokens=settings.openai_max_output_tokens,
            )
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()


def execute_lead_acquisition(
    data: LeadAcquisitionInput,
    *,
    session_factory: SessionFactory,
    decision_factory_factory: Callable[[], Any] = _DecisionFactory,
    serpapi_client_factory: Callable[..., Any] | None = None,
    contact_provider_factory: Callable[[], Any] | None = None,
    email_generator_factory: Callable[[], Any] | None = None,
) -> LeadAcquisitionResult:
    from sqlalchemy import select

    from app.modules.agent.company_apply import (
        AgentCompanyApplyNotEligibleError,
        AgentCompanyApplyNotFoundError,
        AgentCompanyApplyService,
        AgentCompanyApplyStaleHandoffError,
    )
    from app.modules.agent.company_apply_schemas import AgentCompanyApplyInput
    from app.modules.agent.company_plan import (
        AgentCompanyPlanDecisionError,
        AgentCompanyPlanSearchProviderError,
        AgentCompanyPlanService,
    )
    from app.modules.agent.company_plan_schemas import AgentCompanyPlanInput
    from app.modules.agent.company_selection import AgentCompanySelectionService
    from app.modules.agent.contact_apply import (
        AgentContactApplyNotEligibleError,
        AgentContactApplyNotFoundError,
        AgentContactApplyService,
        AgentContactApplyStaleHandoffError,
    )
    from app.modules.agent.contact_apply_schemas import AgentContactApplyInput
    from app.modules.agent.contact_plan import (
        AgentContactPlanProviderError,
        AgentContactPlanService,
        AgentContactPlanWebsiteMissingError,
    )
    from app.modules.agent.contact_plan_schemas import AgentContactPlanInput
    from app.modules.agent.execution import (
        CompanyApplyComponents,
        CompanyPlanComponents,
        ContactApplyComponents,
        ContactPlanComponents,
        execute_company_apply,
        execute_company_plan,
        execute_contact_apply,
        execute_contact_plan,
    )
    from app.modules.agent.lead_acquisition import (
        CompanyApplyOutcome,
        CompanyCompletionOutcome,
        CompanyPlanOutcome,
        ContactApplyOutcome,
        ContactPlanOutcome,
        ExportOutcome,
        LeadAcquisitionCompanyUnavailableError,
        LeadAcquisitionContactUnavailableError,
        LeadAcquisitionDependencies,
        LeadAcquisitionDraftFailure,
        LeadAcquisitionProviderStopError,
        LeadAcquisitionService,
    )
    from app.modules.company.outreach_completion import (
        CompanyScopedOutreachCompletionInput,
        CompanyScopedOutreachCompletionService,
    )
    from app.modules.company.repository import CompanyRepository
    from app.modules.company_discovery.candidate_promotion import (
        CompanyDiscoveryCandidatePromotionService,
    )
    from app.modules.company_discovery.candidate_review import (
        CompanyDiscoveryCandidateReviewService,
    )
    from app.modules.company_discovery.serpapi_provider import SerpApiDiscoveryProvider
    from app.modules.company_discovery.staging_orchestration import CompanyDiscoveryStagingService
    from app.modules.company_discovery.staging_repository import CompanyDiscoveryStagingRepository
    from app.modules.company_enrichment.repository import CompanyEnrichmentRepository
    from app.modules.contact.repository import ContactRepository
    from app.modules.contact_discovery.candidate_promotion import (
        ContactDiscoveryCandidatePromotionService,
    )
    from app.modules.contact_discovery.candidate_review import (
        ContactDiscoveryCandidateReviewService,
    )
    from app.modules.contact_discovery.repository import ContactDiscoveryRepository
    from app.modules.crm.export_execution import execute_crm_excel_export
    from app.modules.email_draft.context import EMAIL_DRAFT_PROMPT_VERSION
    from app.modules.email_draft.execution import execute_email_draft_generation
    from app.modules.email_draft.models import EmailDraft
    from app.modules.email_draft.schemas import (
        EmailDraftGenerationInput,
        EmailLanguage,
        EmailTone,
    )
    from app.modules.email_draft.service import (
        EmailDraftConflictError,
        EmailDraftGenerationError,
        EmailDraftMissingEmailError,
        EmailDraftNotEligibleError,
        EmailDraftStaleContextError,
    )
    from app.modules.lead.repository import LeadRepository
    from app.modules.project.repository import ProjectRepository
    from app.modules.search_profile import (
        SearchProfileQueryGenerator,
        SearchProfileRepository,
        SearchProfileService,
    )
    from app.modules.task.repository import TaskRepository
    from app.providers.serpapi import SerpApiClient

    if serpapi_client_factory is None:
        serpapi_client_factory = SerpApiClient
    if contact_provider_factory is None:
        from app.modules.contact_discovery.website_provider import WebsiteContactDiscoveryProvider

        contact_provider_factory = WebsiteContactDiscoveryProvider

    def company_plan(project_id: int, profile_id: int, goal: str) -> CompanyPlanOutcome:
        try:
            result = execute_company_plan(
                AgentCompanyPlanInput(
                    project_id=project_id,
                    search_profile_id=profile_id,
                    goal=goal,
                ),
                session_factory=session_factory,
                decision_factory_factory=decision_factory_factory,
                serpapi_client_factory=serpapi_client_factory,
                components=CompanyPlanComponents(
                    staging_repository=CompanyDiscoveryStagingRepository,
                    query_generator=SearchProfileQueryGenerator,
                    project_repository=ProjectRepository,
                    profile_repository=SearchProfileRepository,
                    profile_service=SearchProfileService,
                    staging_service=CompanyDiscoveryStagingService,
                    selection_service=AgentCompanySelectionService,
                    plan_service=AgentCompanyPlanService,
                    discovery_provider=SerpApiDiscoveryProvider,
                ),
            )
        except AgentCompanyPlanSearchProviderError as exc:
            raise LeadAcquisitionProviderStopError(
                "Lead acquisition provider stopped.",
                discovery_call_count=exc.discovery_call_count,
                decision_call_count=exc.decision_call_count,
                discovery_run_count=exc.discovery_run_count,
                candidate_count=exc.candidate_count,
                diagnostic=exc.diagnostic,
            ) from None
        except AgentCompanyPlanDecisionError:
            raise LeadAcquisitionProviderStopError("Lead acquisition provider stopped.") from None
        return CompanyPlanOutcome(
            discovery_run_id=result.discovery_run_id,
            candidate_count=result.staged_candidate_count,
            selected_candidate_id=result.selected_candidate_id,
            discovery_call_count=result.serpapi_call_count,
            decision_call_count=result.openai_call_count,
        )

    def company_apply(project_id: int, run_id: int, candidate_id: int) -> CompanyApplyOutcome:
        try:
            result = execute_company_apply(
                AgentCompanyApplyInput(
                    project_id=project_id,
                    discovery_run_id=run_id,
                    candidate_id=candidate_id,
                    confirmed=True,
                ),
                session_factory=session_factory,
                components=CompanyApplyComponents(
                    staging_repository=CompanyDiscoveryStagingRepository,
                    company_repository=CompanyRepository,
                    review_service=CompanyDiscoveryCandidateReviewService,
                    promotion_service=CompanyDiscoveryCandidatePromotionService,
                    apply_service=AgentCompanyApplyService,
                ),
            )
        except (
            AgentCompanyApplyNotEligibleError,
            AgentCompanyApplyNotFoundError,
            AgentCompanyApplyStaleHandoffError,
        ):
            raise LeadAcquisitionCompanyUnavailableError("Company is unavailable.") from None
        return CompanyApplyOutcome(result.company_id, result.company_created, result.company_reused)

    def contact_plan(project_id: int, company_id: int, goal: str) -> ContactPlanOutcome:
        try:
            result = execute_contact_plan(
                AgentContactPlanInput(project_id=project_id, company_id=company_id, goal=goal),
                session_factory=session_factory,
                provider_factory=contact_provider_factory,
                components=ContactPlanComponents(
                    project_repository=ProjectRepository,
                    company_repository=CompanyRepository,
                    discovery_repository=ContactDiscoveryRepository,
                    plan_service=AgentContactPlanService,
                ),
            )
        except (AgentContactPlanProviderError, AgentContactPlanWebsiteMissingError):
            raise LeadAcquisitionContactUnavailableError("Contact is unavailable.") from None
        selected = result.selected_candidate_id
        return ContactPlanOutcome(
            selected_candidate_id=selected,
            selected_email=result.selected_contact_email,
            handoff_token=result.handoff_token,
            discovery_call_count=result.provider_call_count,
            decision_call_count=int(
                selected is not None or result.decision.value == "NO_SELECTION"
            ),
        )

    def contact_apply(
        project_id: int, company_id: int, candidate_id: int, goal: str, token: str
    ) -> ContactApplyOutcome:
        try:
            result = execute_contact_apply(
                AgentContactApplyInput(
                    project_id=project_id,
                    company_id=company_id,
                    candidate_id=candidate_id,
                    goal=goal,
                    handoff_token=token,
                    confirmed=True,
                ),
                session_factory=session_factory,
                components=ContactApplyComponents(
                    company_repository=CompanyRepository,
                    contact_repository=ContactRepository,
                    discovery_repository=ContactDiscoveryRepository,
                    review_service=ContactDiscoveryCandidateReviewService,
                    promotion_service=ContactDiscoveryCandidatePromotionService,
                    lead_repository=LeadRepository,
                    task_repository=TaskRepository,
                    apply_service=AgentContactApplyService,
                ),
            )
        except (
            AgentContactApplyNotEligibleError,
            AgentContactApplyNotFoundError,
            AgentContactApplyStaleHandoffError,
        ):
            raise LeadAcquisitionContactUnavailableError("Contact is unavailable.") from None
        return ContactApplyOutcome(
            result.contact_id,
            result.lead_id,
            result.task_id,
            result.contact_created,
            result.contact_reused,
            result.lead_created,
            result.lead_reused,
            result.task_created,
            result.task_reused,
        )

    def company_email(company_id: int) -> str | None:
        with session_factory() as session:
            enrichment = CompanyEnrichmentRepository(session).get_by_company_id(company_id)
            return None if enrichment is None else enrichment.email

    def company_complete(project_id: int, company_id: int, email: str) -> CompanyCompletionOutcome:
        result = CompanyScopedOutreachCompletionService(session_factory=session_factory).complete(
            CompanyScopedOutreachCompletionInput(
                project_id=project_id,
                company_id=company_id,
                trusted_recipient_email=email,
            )
        )
        return CompanyCompletionOutcome(
            result.lead_id,
            result.task_id,
            result.lead_created,
            result.lead_reused,
            result.task_created,
            result.task_reused,
        )

    def draft_generate(
        project_id: int,
        company_id: int,
        contact_id: int | None,
        lead_id: int,
        task_id: int,
        goal: str,
    ) -> DraftOutcome:
        with session_factory() as session:
            preexisting_ids = set(
                session.scalars(
                    select(EmailDraft.id).where(
                        EmailDraft.project_id == project_id,
                        EmailDraft.company_id == company_id,
                        EmailDraft.contact_id == contact_id,
                        EmailDraft.lead_id == lead_id,
                        EmailDraft.task_id == task_id,
                    )
                )
            )
        try:
            result = execute_email_draft_generation(
                EmailDraftGenerationInput(
                    project_id=project_id,
                    company_id=company_id,
                    contact_id=contact_id,
                    lead_id=lead_id,
                    task_id=task_id,
                    sender_name="Bohemia Bali",
                    sender_company="Bohemia Bali",
                    language=EmailLanguage.EN,
                    tone=EmailTone.WARM,
                    purpose="Explore a relevant design collaboration with Bohemia Bali.",
                    value_proposition=(
                        "Custom handcrafted furniture and tailored lighting, stone, or decorative "
                        "interior elements selected for the firm's project profile."
                    ),
                    prompt_version=EMAIL_DRAFT_PROMPT_VERSION,
                ),
                session_factory=session_factory,
                generator_factory=email_generator_factory,
            )
        except (
            EmailDraftConflictError,
            EmailDraftGenerationError,
            EmailDraftMissingEmailError,
            EmailDraftNotEligibleError,
            EmailDraftStaleContextError,
        ):
            raise LeadAcquisitionDraftFailure("Email draft generation failed.") from None
        created = result.id not in preexisting_ids
        return _draft_outcome(result, created, not created)

    def export_crm(project_id: int, output_file: Path, overwrite: bool) -> ExportOutcome:
        result = execute_crm_excel_export(
            project_id=project_id,
            company_id=None,
            output_file=output_file,
            overwrite=overwrite,
            session_factory=session_factory,
        )
        return ExportOutcome(result.output_file)

    dependencies = LeadAcquisitionDependencies(
        company_plan=company_plan,
        company_apply=company_apply,
        contact_plan=contact_plan,
        contact_apply=contact_apply,
        company_email=company_email,
        company_complete=company_complete,
        draft_generate=draft_generate,
        export_crm=export_crm,
    )
    return LeadAcquisitionService(dependencies).acquire(data)


def _draft_outcome(result: Any, created: bool, reused: bool) -> DraftOutcome:
    return DraftOutcome(
        draft_id=result.id,
        contact_id=result.contact_id,
        lead_id=result.lead_id,
        recipient_email=result.recipient_email,
        status=result.status,
        created=created,
        reused=reused,
    )


__all__ = ["execute_lead_acquisition"]

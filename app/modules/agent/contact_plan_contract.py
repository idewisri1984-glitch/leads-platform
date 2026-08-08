from dataclasses import dataclass


def normalize_contact_plan_text(value: object, *, required: bool = False) -> str | None:
    if value is None:
        if required:
            raise ValueError("Required contact-plan text is missing.")
        return None
    if type(value) is not str:
        raise ValueError("Contact-plan text is invalid.")
    value.encode("utf-8")
    normalized = " ".join(value.split())
    if required and not normalized:
        raise ValueError("Required contact-plan text is missing.")
    return normalized or None


def bounded_contact_plan_text(value: str, maximum: int) -> str:
    return value if len(value) <= maximum else value[:maximum].rstrip()


@dataclass(frozen=True, slots=True)
class ContactPlanProposals:
    lead_title: str
    task_title: str
    task_description: str


def build_legacy_contact_plan_task_description(
    *,
    company_name: object,
    candidate_name: object,
    candidate_title: object,
    goal: object,
) -> str:
    """Build the exact pre-Stage-4E Task description for bounded legacy reuse."""
    company = normalize_contact_plan_text(company_name, required=True)
    name = normalize_contact_plan_text(candidate_name, required=True)
    title = normalize_contact_plan_text(candidate_title)
    normalized_goal = normalize_contact_plan_text(goal, required=True)
    if company is None or name is None or normalized_goal is None:
        raise ValueError("Contact-plan proposal data is invalid.")
    title_detail = f" with title {title}" if title else ""
    fixed = (
        "A human must verify this contact before any action. No outreach has been sent, "
        "and no Lead or Task has been created. "
        f"Selected person: {name}{title_detail}. Company: {company}. "
        "Prepare a personalized Bohemia Bali partnership message. Goal: "
    )
    return bounded_contact_plan_text(fixed + normalized_goal, 4000)


def build_contact_plan_proposals(
    *,
    company_name: object,
    candidate_name: object,
    candidate_title: object,
    goal: object,
) -> ContactPlanProposals:
    company = normalize_contact_plan_text(company_name, required=True)
    name = normalize_contact_plan_text(candidate_name, required=True)
    title = normalize_contact_plan_text(candidate_title)
    normalized_goal = normalize_contact_plan_text(goal, required=True)
    if company is None or name is None or normalized_goal is None:
        raise ValueError("Contact-plan proposal data is invalid.")
    lead_title = bounded_contact_plan_text(f"Bohemia Bali partnership — {company}", 255)
    task_title = bounded_contact_plan_text(f"Review and prepare outreach to {name}", 255)
    title_detail = f" with title {title}" if title else ""
    fixed = (
        f"Next operator action: review and prepare personalized outreach to {name}{title_detail} "
        f"at {company}. The resulting Task is the actionable follow-up for the resulting Lead. "
        "Use the selected Contact, Company, and Agent goal as authoritative context. Goal: "
    )
    return ContactPlanProposals(
        lead_title=lead_title,
        task_title=task_title,
        task_description=bounded_contact_plan_text(fixed + normalized_goal, 4000),
    )


__all__ = [
    "ContactPlanProposals",
    "bounded_contact_plan_text",
    "build_legacy_contact_plan_task_description",
    "build_contact_plan_proposals",
    "normalize_contact_plan_text",
]

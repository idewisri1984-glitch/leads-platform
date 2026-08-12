from datetime import UTC, datetime, timedelta

import pytest

from app.modules.crm import (
    CRMCompanyRecord,
    CRMContactRecord,
    CRMDeliveryAttemptRecord,
    CRMDraftRecord,
    CRMLeadRecord,
    CRMManualSendRecord,
    CRMOverviewService,
    CRMOverviewSnapshot,
    CRMTaskRecord,
    derive_outreach_status,
)

NOW = datetime(2026, 8, 11, tzinfo=UTC)


def snapshot(
    *,
    contacts: tuple[CRMContactRecord, ...] = (),
    leads: tuple[CRMLeadRecord, ...] = (),
    tasks: tuple[CRMTaskRecord, ...] = (),
    drafts: tuple[CRMDraftRecord, ...] = (),
    manual_sends: tuple[CRMManualSendRecord, ...] = (),
    attempts: tuple[CRMDeliveryAttemptRecord, ...] = (),
) -> CRMOverviewSnapshot:
    return CRMOverviewSnapshot(
        companies=(CRMCompanyRecord(1, 1, "Acme"),),
        contacts=contacts,
        leads=leads,
        tasks=tasks,
        drafts=drafts,
        manual_sends=manual_sends,
        delivery_attempts=attempts,
    )


def contact() -> CRMContactRecord:
    return CRMContactRecord(2, 1, "Ada", "Lovelace", "Founder", "ada@example.com")


def lead() -> CRMLeadRecord:
    return CRMLeadRecord(3, 1, 2, "NEW")


def draft(status: str = "DRAFT", *, task_id: int = 4) -> CRMDraftRecord:
    return CRMDraftRecord(5, 3, task_id, status, NOW, NOW)


def test_keeps_company_contact_and_lead_rows_when_downstream_entities_are_absent() -> None:
    service = CRMOverviewService()
    company_only = service.build(snapshot())[0]
    contact_only = service.build(snapshot(contacts=(contact(),)))[0]
    lead_without_task = service.build(snapshot(contacts=(contact(),), leads=(lead(),)))[0]
    assert company_only.contact_id is None
    assert contact_only.contact_id == 2 and contact_only.lead_id is None
    assert lead_without_task.lead_id == 3 and lead_without_task.task_id is None
    assert lead_without_task.outreach_status == "NO_DRAFT"


@pytest.mark.parametrize(
    ("tasks", "expected_id"),
    [
        (
            (
                CRMTaskRecord(4, 3, "todo", "TODO", None),
                CRMTaskRecord(5, 3, "progress", "IN_PROGRESS", None),
            ),
            5,
        ),
        (
            (
                CRMTaskRecord(4, 3, "later", "IN_PROGRESS", NOW + timedelta(days=2)),
                CRMTaskRecord(5, 3, "earlier", "IN_PROGRESS", NOW + timedelta(days=1)),
            ),
            5,
        ),
        (
            (
                CRMTaskRecord(4, 3, "unscheduled", "TODO", None),
                CRMTaskRecord(5, 3, "scheduled", "TODO", NOW),
            ),
            5,
        ),
        (
            (
                CRMTaskRecord(5, 3, "higher", "TODO", NOW),
                CRMTaskRecord(4, 3, "lower", "TODO", NOW),
            ),
            4,
        ),
        (
            (
                CRMTaskRecord(4, 3, "cancelled", "CANCELLED", None),
                CRMTaskRecord(5, 3, "done", "DONE", None),
            ),
            5,
        ),
        (
            (
                CRMTaskRecord(4, 3, "done old", "DONE", None),
                CRMTaskRecord(7, 3, "done new", "DONE", None),
            ),
            7,
        ),
        (
            (
                CRMTaskRecord(4, 3, "cancelled old", "CANCELLED", None),
                CRMTaskRecord(7, 3, "cancelled new", "CANCELLED", None),
            ),
            7,
        ),
    ],
    ids=[
        "in-progress-before-todo",
        "earliest-naive-due-at",
        "scheduled-before-null",
        "id-tie-break",
        "done-before-cancelled",
        "latest-done-fallback",
        "latest-cancelled-fallback",
    ],
)
def test_current_task_selection_matrix(tasks: tuple[CRMTaskRecord, ...], expected_id: int) -> None:
    row = CRMOverviewService().build(snapshot(contacts=(contact(),), leads=(lead(),), tasks=tasks))[
        0
    ]
    assert row.task_id == expected_id


def test_prefers_selected_task_draft_and_exposes_fallback_draft_task() -> None:
    tasks = (CRMTaskRecord(8, 3, "current", "TODO", None),)
    old = draft(task_id=4)
    current = CRMDraftRecord(9, 3, 8, "DRAFT", NOW, NOW)
    selected = CRMOverviewService().build(
        snapshot(contacts=(contact(),), leads=(lead(),), tasks=tasks, drafts=(old, current))
    )[0]
    assert (selected.draft_id, selected.draft_task_id) == (9, 8)
    fallback = CRMOverviewService().build(
        snapshot(contacts=(contact(),), leads=(lead(),), tasks=tasks, drafts=(old,))
    )[0]
    assert (fallback.task_id, fallback.draft_id, fallback.draft_task_id) == (8, 5, 4)


def test_outreach_status_precedence_and_automatic_states_are_truthful() -> None:
    selected = draft("APPROVED")
    manual = CRMManualSendRecord(5, NOW)
    accepted = CRMDeliveryAttemptRecord(6, 5, "ACCEPTED", NOW, NOW)
    unknown = CRMDeliveryAttemptRecord(7, 5, "UNKNOWN", NOW, None)
    assert derive_outreach_status(None, None, None) == ("NO_DRAFT", None)
    assert derive_outreach_status(draft(), None, None) == ("DRAFT", None)
    assert derive_outreach_status(selected, None, None) == ("APPROVED", None)
    assert derive_outreach_status(selected, manual, accepted) == ("MANUALLY_SENT", NOW)
    assert derive_outreach_status(selected, None, accepted) == ("AUTOMATIC_ACCEPTED", NOW)
    assert derive_outreach_status(selected, None, unknown) == ("AUTOMATIC_UNKNOWN", None)


def test_multiple_contacts_and_unassigned_leads_are_not_dropped() -> None:
    second = CRMContactRecord(8, 1, "Grace", "Hopper", None, None)
    unassigned = CRMLeadRecord(9, 1, None, "NEW")
    rows = CRMOverviewService().build(
        snapshot(contacts=(contact(), second), leads=(lead(), unassigned))
    )
    assert {(row.contact_id, row.lead_id) for row in rows} == {(2, 3), (8, None), (None, 9)}

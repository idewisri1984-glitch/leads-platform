from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.modules.crm.excel_export import CRMExcelExportResult


class SessionFactory(Protocol):
    def __call__(self) -> Session: ...


def execute_crm_excel_export(
    *,
    project_id: int | None,
    company_id: int | None,
    output_file: Path,
    overwrite: bool,
    session_factory: SessionFactory,
) -> CRMExcelExportResult:
    from app.modules.crm.excel_export import CRMExcelExportService

    with session_factory() as session:
        return CRMExcelExportService().export(
            session,
            project_id=project_id,
            company_id=company_id,
            output_file=output_file,
            overwrite=overwrite,
        )


__all__ = ["execute_crm_excel_export"]

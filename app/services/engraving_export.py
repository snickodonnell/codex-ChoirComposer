from __future__ import annotations

from dataclasses import dataclass

from app.models import CanonicalScore


@dataclass(frozen=True)
class PDFExportResult:
    pdf_bytes: bytes
    page_count: int
    pipeline: str


class PDFExportDependencyError(RuntimeError):
    pass


class EngravingExportService:
    def export_pdf(self, score: CanonicalScore) -> PDFExportResult:
        raise NotImplementedError(
            "PDF export is now generated in the browser. Please update the client."
        )


export_service = EngravingExportService()

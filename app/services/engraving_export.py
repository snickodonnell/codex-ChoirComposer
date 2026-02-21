from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from app.logging_utils import log_event
from app.models import CanonicalScore
from app.services.engraving_preview import EngravingOptions, preview_service

logger = logging.getLogger(__name__)


class EngravingExportService:
    def export_pdf(self, score: CanonicalScore, options: EngravingOptions | None = None) -> bytes:
        engraving_options = options or EngravingOptions(include_all_pages=True)
        musicxml = preview_service.build_musicxml(score)
        toolkit = preview_service.build_toolkit(musicxml, engraving_options)

        page_count = max(1, int(toolkit.getPageCount()))
        log_event(logger, "pdf_export_render_started", measure_count=len(score.measures), page_count=page_count)

        pdf_bytes = self._render_pdf(toolkit)
        log_event(logger, "pdf_export_render_completed", output_size_bytes=len(pdf_bytes), page_count=page_count)
        return pdf_bytes

    def _render_pdf(self, toolkit) -> bytes:
        render_to_pdf = getattr(toolkit, "renderToPDF", None)
        if callable(render_to_pdf):
            pdf = render_to_pdf()
            if isinstance(pdf, bytes):
                return pdf
            if isinstance(pdf, str):
                return pdf.encode("latin-1")

        render_to_pdf_file = getattr(toolkit, "renderToPDFFile", None)
        if callable(render_to_pdf_file):
            with tempfile.TemporaryDirectory(prefix="engraving-pdf-") as tmp_dir:
                output_path = Path(tmp_dir) / "score.pdf"
                render_to_pdf_file(str(output_path))
                return output_path.read_bytes()

        raise RuntimeError("Verovio PDF backend is unavailable in this runtime.")


export_service = EngravingExportService()

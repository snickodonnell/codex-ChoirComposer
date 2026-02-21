from __future__ import annotations

import io
import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path

from app.logging_utils import current_request_id, log_event
from app.models import CanonicalScore
from app.services.engraving_preview import EngravingOptions, preview_service

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PDFExportResult:
    pdf_bytes: bytes
    page_count: int
    pipeline: str


class EngravingExportService:
    def export_pdf(self, score: CanonicalScore, options: EngravingOptions | None = None) -> PDFExportResult:
        engraving_options = options or EngravingOptions(include_all_pages=True)
        log_event(
            logger,
            "pdf_export_pipeline_entry",
            request_id=current_request_id(),
            stage=score.meta.stage,
            measure_count=len(score.measures),
        )
        musicxml = preview_service.build_musicxml(score)
        toolkit = preview_service.build_toolkit(musicxml, engraving_options)

        page_count = max(1, int(toolkit.getPageCount()))
        log_event(logger, "pdf_export_render_started", measure_count=len(score.measures), page_count=page_count)

        pipeline = "native_pdf"
        try:
            log_event(
                logger,
                "pdf_export_attempt_native",
                spacingSystem=max(0, min(100, engraving_options.layout.system_spacing)),
            )
            pdf_bytes = self._render_pdf_native(toolkit)
        except Exception as exc:
            log_event(
                logger,
                "pdf_export_falling_back_to_svg_pipeline",
                level=logging.WARNING,
                exception_type=type(exc).__name__,
                error_message=str(exc),
            )
            svg_pages = self._render_svg_pages(toolkit, page_count)
            log_event(logger, "pdf_export_using_cairosvg_fallback", svg_page_count=len(svg_pages))
            pdf_bytes = self._render_pdf_from_svg(svg_pages)
            pipeline = "svg_to_pdf"

        log_event(
            logger,
            "pdf_export_render_completed",
            output_size_bytes=len(pdf_bytes),
            page_count=page_count,
            pipeline=pipeline,
        )
        return PDFExportResult(pdf_bytes=pdf_bytes, page_count=page_count, pipeline=pipeline)

    def _render_pdf_native(self, toolkit) -> bytes:
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

    def _render_svg_pages(self, toolkit, page_count: int) -> list[str]:
        svg_pages: list[str] = []
        for page in range(1, page_count + 1):
            try:
                svg_pages.append(toolkit.renderToSVG(page))
            except TypeError:
                svg_pages.append(toolkit.renderToSVG(page, {}))
        return svg_pages

    def _render_pdf_from_svg(self, svg_pages: list[str]) -> bytes:
        try:
            import cairosvg  # type: ignore
        except ImportError as exc:
            raise RuntimeError("CairoSVG is required for SVG-to-PDF export fallback.") from exc

        try:
            from pypdf import PdfReader, PdfWriter
        except ImportError as exc:
            raise RuntimeError("pypdf is required for SVG-to-PDF export fallback.") from exc

        writer = PdfWriter()
        for svg in svg_pages:
            page_pdf = cairosvg.svg2pdf(bytestring=svg.encode("utf-8"))
            reader = PdfReader(io.BytesIO(page_pdf))
            for page in reader.pages:
                writer.add_page(page)

        output = io.BytesIO()
        writer.write(output)
        return output.getvalue()


export_service = EngravingExportService()

import io

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models import ArrangementItem, CompositionPreferences, CompositionRequest, LyricSection
from app.services.composer import generate_melody_score, harmonize_score
from app.services.engraving_export import EngravingExportService
from app.services.score_validation import ValidationDiagnostics


client = TestClient(app)


def _satb_score_with_verses():
    req = CompositionRequest(
        sections=[
            LyricSection(id="verse-1", label="Verse 1", is_verse=True, text="Morning light renews us"),
            LyricSection(id="verse-2", label="Verse 2", is_verse=True, text="Evening song restores us"),
        ],
        arrangement=[
            ArrangementItem(section_id="verse-1", is_verse=True),
            ArrangementItem(section_id="verse-2", is_verse=True),
        ],
        preferences=CompositionPreferences(key="D", time_signature="4/4", tempo_bpm=90),
    )
    return harmonize_score(generate_melody_score(req))


def test_pdf_export_succeeds_when_preview_pipeline_succeeds(monkeypatch):
    satb = _satb_score_with_verses()

    class StubExportResult:
        pdf_bytes = b"%PDF-stub"
        page_count = 1
        pipeline = "svg_to_pdf"

    class StubExportService:
        def export_pdf(self, score, options=None):
            return StubExportResult()

    monkeypatch.setattr("app.main.export_service", StubExportService())

    pdf_res = client.post("/api/export-pdf", json={"score": satb.model_dump()})

    assert pdf_res.status_code == 200
    assert pdf_res.headers["content-type"] == "application/pdf"
    assert pdf_res.headers["x-composer-warnings-count"] == "0"
    assert pdf_res.content == b"%PDF-stub"


def test_pdf_export_blocks_only_on_fatal_diagnostics(monkeypatch):
    satb = _satb_score_with_verses()

    class StubExportResult:
        pdf_bytes = b"%PDF-warning-ok"
        page_count = 1
        pipeline = "svg_to_pdf"

    class StubExportService:
        def export_pdf(self, score, options=None):
            return StubExportResult()

    monkeypatch.setattr("app.main.export_service", StubExportService())

    monkeypatch.setattr(
        "app.main.validate_score_diagnostics",
        lambda score: ValidationDiagnostics(fatal=[], warnings=["voice crossing warning"]),
    )
    warning_res = client.post("/api/export-pdf", json={"score": satb.model_dump()})
    assert warning_res.status_code == 200
    assert warning_res.headers["x-export-warnings"]
    assert warning_res.headers["x-composer-warnings-count"] == "1"

    monkeypatch.setattr(
        "app.main.validate_score_diagnostics",
        lambda score: ValidationDiagnostics(fatal=["duration mismatch"], warnings=["voice crossing warning"]),
    )
    fatal_res = client.post("/api/export-pdf", json={"score": satb.model_dump()})
    assert fatal_res.status_code == 422


def test_pdf_export_svg_fallback_non_empty_and_page_count_matches_svg(monkeypatch):
    pytest.importorskip("cairosvg")
    PdfReader = pytest.importorskip("pypdf").PdfReader
    satb = _satb_score_with_verses()
    service = EngravingExportService()

    class StubToolkit:
        def getPageCount(self):
            return 2

        def renderToPDF(self):
            raise RuntimeError("native unavailable")

        def renderToSVG(self, page):
            return (
                '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100">'
                f'<text x="20" y="50">Page {page}</text></svg>'
            )

    captured_musicxml = {"value": ""}

    def _capture_toolkit(musicxml, options):
        captured_musicxml["value"] = musicxml
        return StubToolkit()

    monkeypatch.setattr("app.services.engraving_export.preview_service.build_toolkit", _capture_toolkit)

    result = service.export_pdf(satb)

    assert result.pdf_bytes.startswith(b"%PDF")
    assert len(result.pdf_bytes) > 100
    assert result.page_count == 2
    assert result.pipeline == "svg_to_pdf"
    assert len(PdfReader(io.BytesIO(result.pdf_bytes)).pages) == result.page_count
    assert 'lyric number="2"' in captured_musicxml["value"]

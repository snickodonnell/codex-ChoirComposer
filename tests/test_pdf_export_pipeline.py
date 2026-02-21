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


def test_pdf_export_succeeds_when_preview_succeeds(monkeypatch):
    satb = _satb_score_with_verses()

    class StubPreviewService:
        def render_preview(self, score, options):
            return [], False

    class StubExportService:
        def export_pdf(self, score, options=None):
            return b"%PDF-stub"

    monkeypatch.setattr("app.main.preview_service", StubPreviewService())
    monkeypatch.setattr("app.main.export_service", StubExportService())

    preview_res = client.post(
        "/api/engrave/preview",
        json={"score": satb.model_dump(), "preview_mode": "satb", "include_all_pages": False, "scale": 42},
    )
    pdf_res = client.post("/api/export-pdf", json={"score": satb.model_dump()})

    assert preview_res.status_code == 200
    assert pdf_res.status_code == 200
    assert pdf_res.headers["content-type"] == "application/pdf"
    assert pdf_res.content == b"%PDF-stub"


def test_pdf_export_blocks_only_on_fatal_diagnostics(monkeypatch):
    satb = _satb_score_with_verses()

    class StubExportService:
        def export_pdf(self, score, options=None):
            return b"%PDF-warning-ok"

    monkeypatch.setattr("app.main.export_service", StubExportService())

    monkeypatch.setattr(
        "app.main.validate_score_diagnostics",
        lambda score: ValidationDiagnostics(fatal=[], warnings=["voice crossing warning"]),
    )
    warning_res = client.post("/api/export-pdf", json={"score": satb.model_dump()})
    assert warning_res.status_code == 200
    assert warning_res.headers["x-export-warnings"]

    monkeypatch.setattr(
        "app.main.validate_score_diagnostics",
        lambda score: ValidationDiagnostics(fatal=["duration mismatch"], warnings=["voice crossing warning"]),
    )
    fatal_res = client.post("/api/export-pdf", json={"score": satb.model_dump()})
    assert fatal_res.status_code == 422


def test_pdf_export_uses_musicxml_with_multiverse_stacking(monkeypatch):
    satb = _satb_score_with_verses()
    service = EngravingExportService()
    captured_musicxml = {"value": ""}

    class StubToolkit:
        def getPageCount(self):
            return 1

        def renderToPDF(self):
            return b"%PDF-musicxml"

    def _capture_toolkit(musicxml, options):
        captured_musicxml["value"] = musicxml
        return StubToolkit()

    monkeypatch.setattr("app.services.engraving_export.preview_service.build_toolkit", _capture_toolkit)

    pdf_content = service.export_pdf(satb)

    assert pdf_content.startswith(b"%PDF")
    assert 'lyric number="2"' in captured_musicxml["value"]


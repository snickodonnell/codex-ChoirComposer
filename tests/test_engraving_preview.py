from fastapi.testclient import TestClient

from app.main import app
from app.models import ArrangementItem, CompositionPreferences, CompositionRequest, LyricSection
from app.services import engraving_preview
from app.services.composer import generate_melody_score, harmonize_score


client = TestClient(app)


def _melody_score():
    req = CompositionRequest(
        sections=[LyricSection(label="verse", text="Morning light renews us")],
        preferences=CompositionPreferences(key="D", time_signature="4/4", tempo_bpm=90),
    )
    return generate_melody_score(req)


def test_preview_endpoint_rejects_wrong_stage():
    melody = _melody_score()

    res = client.post(
        "/api/engrave/preview",
        json={"score": melody.model_dump(), "preview_mode": "satb", "include_all_pages": False, "scale": 42},
    )

    assert res.status_code == 422
    assert "Engraving preview failed" in res.json()["detail"]["message"]


def test_preview_endpoint_returns_svg_artifacts_and_cache_flag(monkeypatch):
    satb = harmonize_score(_melody_score())

    class StubService:
        def __init__(self):
            self.called = 0

        def render_preview(self, score, options):
            self.called += 1
            return ([engraving_preview.PreviewArtifact(page=1, svg="<svg><text>ok</text></svg>")], self.called > 1)

    stub = StubService()
    monkeypatch.setattr("app.main.preview_service", stub)

    payload = {"score": satb.model_dump(), "preview_mode": "satb", "include_all_pages": True, "scale": 40}
    first = client.post("/api/engrave/preview", json=payload)
    second = client.post("/api/engrave/preview", json=payload)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["cache_hit"] is False
    assert second.json()["cache_hit"] is True
    assert first.json()["artifacts"][0]["svg"].startswith("<svg")


def test_pages_endpoint_matches_preview_page_count_for_same_input(monkeypatch):
    satb = harmonize_score(_melody_score())

    class StubService:
        def render_preview(self, score, options):
            if options.include_all_pages:
                return (
                    [
                        engraving_preview.PreviewArtifact(page=1, svg="<svg><text>page 1</text></svg>"),
                        engraving_preview.PreviewArtifact(page=2, svg="<svg><text>page 2</text></svg>"),
                    ],
                    False,
                )
            return ([engraving_preview.PreviewArtifact(page=1, svg="<svg><text>page 1</text></svg>")], False)

    monkeypatch.setattr("app.main.preview_service", StubService())
    preview_payload = {"score": satb.model_dump(), "preview_mode": "satb", "include_all_pages": True, "scale": 42}
    pages_payload = {"score": satb.model_dump(), "stage": "satb", "include_all_pages": True, "scale": 42}

    preview_response = client.post("/api/engrave/preview", json=preview_payload)
    pages_response = client.post("/api/engrave/pages", json=pages_payload)

    assert preview_response.status_code == 200
    assert pages_response.status_code == 200
    assert pages_response.json()["page_count"] == len(preview_response.json()["artifacts"])
    assert pages_response.json()["page_count"] == len(pages_response.json()["pages"])


def test_pages_endpoint_returns_svg_payload(monkeypatch):
    satb = harmonize_score(_melody_score())

    class StubService:
        def render_preview(self, score, options):
            return [engraving_preview.PreviewArtifact(page=1, svg="<svg><text>ok</text></svg>")], False

    monkeypatch.setattr("app.main.preview_service", StubService())
    payload = {"score": satb.model_dump(), "stage": "satb", "include_all_pages": True, "scale": 42}

    response = client.post("/api/engrave/pages", json=payload)

    assert response.status_code == 200
    assert response.json()["pages"]
    assert any("<svg" in page["svg"] for page in response.json()["pages"])


def test_preview_endpoint_accepts_pickup_enabled_score(monkeypatch):
    req = CompositionRequest(
        sections=[LyricSection(id="verse-1", label="verse", text="glory forever rising now")],
        arrangement=[ArrangementItem(section_id="verse-1", anacrusis_mode="manual", anacrusis_beats=1)],
        preferences=CompositionPreferences(key="D", time_signature="4/4", tempo_bpm=90),
    )
    satb = harmonize_score(generate_melody_score(req))

    class StubService:
        def render_preview(self, score, options):
            return [engraving_preview.PreviewArtifact(page=1, svg="<svg><text>pickup</text></svg>")], False

    monkeypatch.setattr("app.main.preview_service", StubService())
    payload = {"score": satb.model_dump(), "preview_mode": "satb", "include_all_pages": False, "scale": 42}
    res = client.post("/api/engrave/preview", json=payload)
    assert res.status_code == 200
    assert res.json()["artifacts"][0]["svg"].startswith("<svg")


def test_preview_endpoint_succeeds_with_warning_only_diagnostics(monkeypatch):
    satb = harmonize_score(_melody_score())

    class StubService:
        def render_preview(self, score, options):
            return [engraving_preview.PreviewArtifact(page=1, svg="<svg><text>warn</text></svg>")], False

    monkeypatch.setattr("app.main.preview_service", StubService())

    from app.services.score_validation import ValidationDiagnostics

    monkeypatch.setattr(
        "app.main.validate_score_diagnostics",
        lambda score: ValidationDiagnostics(fatal=[], warnings=["Soprano strong-beat note 0 (F#4) conflicts with chord in measure 1."]),
    )

    payload = {"score": satb.model_dump(), "preview_mode": "satb", "include_all_pages": False, "scale": 42}
    res = client.post("/api/engrave/preview", json=payload)

    assert res.status_code == 200
    assert res.json()["artifacts"][0]["svg"].startswith("<svg")
    assert res.json()["warnings"]


def test_preview_endpoint_fails_with_fatal_diagnostics(monkeypatch):
    satb = harmonize_score(_melody_score())
    from app.services.score_validation import ValidationDiagnostics

    monkeypatch.setattr(
        "app.main.validate_score_diagnostics",
        lambda score: ValidationDiagnostics(fatal=["Measure 1 voice soprano has 3 beats; expected 4."], warnings=[]),
    )

    payload = {"score": satb.model_dump(), "preview_mode": "satb", "include_all_pages": False, "scale": 42}
    res = client.post("/api/engrave/preview", json=payload)

    assert res.status_code == 422


def test_build_toolkit_applies_hymn_layout_options(monkeypatch):
    captured = {}

    class StubToolkit:
        def setOptions(self, options):
            captured["options"] = options

        def loadData(self, musicxml):
            captured["musicxml"] = musicxml

    class StubVerovio:
        @staticmethod
        def toolkit():
            return StubToolkit()

    import sys

    monkeypatch.setitem(sys.modules, "verovio", StubVerovio)
    service = engraving_preview.EngravingPreviewService()
    toolkit = service.build_toolkit("<score-partwise/>", engraving_preview.EngravingOptions())

    assert toolkit is not None
    assert captured["options"]["breaks"] == "auto"
    assert captured["options"]["spacingSystem"] == 100
    assert captured["options"]["spacingStaff"] == engraving_preview.DEFAULT_LAYOUT.staff_spacing
    assert captured["options"]["condense"] == "none"
    assert captured["options"]["pageMarginTop"] == engraving_preview.DEFAULT_LAYOUT.margin_top


def test_build_toolkit_clamps_system_spacing_to_verovio_bounds(monkeypatch):
    captured = {}

    class StubToolkit:
        def setOptions(self, options):
            captured["options"] = options

        def loadData(self, musicxml):
            captured["musicxml"] = musicxml

    class StubVerovio:
        @staticmethod
        def toolkit():
            return StubToolkit()

    import sys

    monkeypatch.setitem(sys.modules, "verovio", StubVerovio)
    service = engraving_preview.EngravingPreviewService()
    options = engraving_preview.EngravingOptions(
        layout=engraving_preview.EngravingLayoutConfig(system_spacing=1000)
    )

    service.build_toolkit("<score-partwise/>", options)

    assert 0 <= captured["options"]["spacingSystem"] <= 100

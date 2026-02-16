import logging

from fastapi.testclient import TestClient

from app import main as main_module
from app.main import app
from app.models import CompositionPreferences, CompositionRequest, LyricSection
from app.services.composer import generate_melody_score, harmonize_score


client = TestClient(app)


def _sample_request() -> CompositionRequest:
    return CompositionRequest(
        sections=[LyricSection(label="verse", text="Morning light renews us")],
        preferences=CompositionPreferences(key="D", time_signature="4/4", tempo_bpm=90),
    )


def test_generate_satb_rejects_satb_input_stage():
    melody = generate_melody_score(_sample_request())
    satb = harmonize_score(melody)

    res = client.post("/api/generate-satb", json={"score": satb.model_dump()})

    assert res.status_code == 422
    assert "SATB generation failed" in res.json()["detail"]["message"]
    assert res.json()["detail"]["request_id"]


def test_export_pdf_requires_satb_stage():
    melody = generate_melody_score(_sample_request())

    res = client.post("/api/export-pdf", json={"score": melody.model_dump()})

    assert res.status_code == 422
    assert "PDF export failed" in res.json()["detail"]["message"]
    assert res.json()["detail"]["request_id"]


def test_compose_end_score_endpoint_runs_full_workflow():
    req = _sample_request()

    res = client.post("/api/compose-end-score", json=req.model_dump())

    assert res.status_code == 200
    payload = res.json()
    assert payload["melody"]["meta"]["stage"] == "melody"
    assert payload["satb"]["meta"]["stage"] == "satb"
    assert "input" in payload["composition_notes"]


def test_refine_endpoint_accepts_cluster_regenerate_payload():
    melody = generate_melody_score(_sample_request())

    res = client.post(
        "/api/refine-melody",
        json={
            "score": melody.model_dump(),
            "instruction": "fresh melodic idea",
            "regenerate": True,
            "selected_clusters": ["verse"],
            "section_clusters": {"sec-1": "verse"},
        },
    )

    assert res.status_code == 200
    assert res.json()["score"]["meta"]["stage"] == "melody"


def test_refine_satb_endpoint_accepts_cluster_regenerate_payload():
    melody = generate_melody_score(_sample_request())
    satb = harmonize_score(melody)

    res = client.post(
        "/api/refine-satb",
        json={
            "score": satb.model_dump(),
            "instruction": "fresh harmonic voicing",
            "regenerate": True,
            "selected_clusters": ["verse"],
            "section_clusters": {"sec-1": "verse"},
        },
    )

    assert res.status_code == 200
    assert res.json()["score"]["meta"]["stage"] == "satb"


def test_refine_satb_rejects_melody_input_stage():
    melody = generate_melody_score(_sample_request())

    res = client.post(
        "/api/refine-satb",
        json={"score": melody.model_dump(), "instruction": "smooth inner voices", "regenerate": False},
    )

    assert res.status_code == 422
    assert "SATB refinement failed" in res.json()["detail"]["message"]
    assert res.json()["detail"]["request_id"]


def test_request_id_header_present_on_response():
    req = _sample_request()

    res = client.post("/api/compose-end-score", json=req.model_dump())

    assert res.status_code == 200
    assert res.headers.get("X-Request-ID")


def test_validation_failure_logs_event(caplog, monkeypatch):
    melody = generate_melody_score(_sample_request())

    monkeypatch.setattr(main_module, "validate_score", lambda *_args, **_kwargs: ["diagnostic: strong beat conflict"] )

    with caplog.at_level(logging.ERROR):
        res = client.post("/api/generate-satb", json={"score": melody.model_dump()})

    assert res.status_code == 422
    assert any(getattr(record, "event", "") == "validation_failed" for record in caplog.records)

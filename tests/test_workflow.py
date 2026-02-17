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




def test_validate_score_endpoint_returns_valid_true_for_generated_score():
    melody = generate_melody_score(_sample_request())

    res = client.post("/api/validate-score", json={"score": melody.model_dump()})

    assert res.status_code == 200
    assert res.json() == {"valid": True, "errors": []}


def test_validate_score_endpoint_reports_errors_and_request_id(monkeypatch):
    melody = generate_melody_score(_sample_request())

    monkeypatch.setattr(main_module, "validate_score", lambda *_args, **_kwargs: ["diagnostic: mismatch"])

    res = client.post("/api/validate-score", json={"score": melody.model_dump()})

    assert res.status_code == 200
    payload = res.json()
    assert payload["valid"] is False
    assert "failed validation" in payload["message"]
    assert payload["request_id"]

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
            "selected_units": ["verse"],
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
            "selected_units": ["verse"],
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


def test_client_log_endpoint_records_playback_events(caplog):
    with caplog.at_level(logging.INFO):
        res = client.post(
            "/api/client-log",
            json={
                "ts": "2026-01-01T00:00:00.000Z",
                "event": "playback_started",
                "type": "satb",
                "id": "satb:demo",
                "events": 12,
                "totalSeconds": 9.5,
            },
        )

    assert res.status_code == 200
    assert res.json()["ok"] is True
    assert any(getattr(record, "event", "") == "client_playback_event" for record in caplog.records)


def test_generate_melody_allows_verse_projection_overflow_with_slot_expansion():
    payload = {
        "sections": [
            {
                "id": "v1",
                "label": "Verse",
                "is_verse": True,
                "text": "Amazing grace how sweet the sound\nThat saved a wretch like me\nI once was lost but now am found\nWas blind but now I see",
            },
            {
                "id": "v2",
                "label": "Verse",
                "is_verse": True,
                "text": "Twas grace that taught my heart to fear\nAnd grace my fears relieved\nHow precious did that grace appear\nThe hour I first believed",
            },
            {
                "id": "v3",
                "label": "Verse",
                "is_verse": True,
                "text": "Through many dangers toils and snares\nI have already come\nTis grace hath brought me safe thus far\nAnd grace will lead me home",
            },
        ],
        "arrangement": [
            {"section_id": "v1", "is_verse": True},
            {"section_id": "v2", "is_verse": True},
            {"section_id": "v3", "is_verse": True},
        ],
        "preferences": {"key": "G", "time_signature": "4/4", "tempo_bpm": 92, "lyric_rhythm_preset": "mixed"},
    }

    res = client.post("/api/generate-melody", json=payload)

    assert res.status_code == 200
    assert any(section.get("verse_number") == 3 for section in res.json()["score"]["sections"])

def test_generate_melody_amazing_grace_respects_16_bars_per_verse_and_stacks_to_verse_3():
    payload = {
        "sections": [
            {"id": "v1", "label": "Verse", "is_verse": True, "text": "Amazing grace how sweet the sound\nThat saved a wretch like me"},
            {"id": "c", "label": "Chorus", "is_verse": False, "text": "I once was lost but now am found"},
            {"id": "v2", "label": "Verse", "is_verse": True, "text": "Twas grace that taught my heart to fear\nAnd grace my fears relieved"},
            {"id": "v3", "label": "Verse", "is_verse": True, "text": "Through many dangers toils and snares\nI have already come"},
        ],
        "arrangement": [
            {"section_id": "v1", "is_verse": True},
            {"section_id": "c", "is_verse": False},
            {"section_id": "v2", "is_verse": True},
            {"section_id": "c", "is_verse": False},
            {"section_id": "v3", "is_verse": True},
        ],
        "preferences": {"key": "C", "time_signature": "4/4", "tempo_bpm": 90, "bars_per_verse": 16},
    }

    res = client.post("/api/generate-melody", json=payload)

    assert res.status_code == 200
    score = res.json()["score"]
    assert score["meta"]["verse_music_unit_form"]["bars_per_verse"] == 16

    def full_measures(section_id: str) -> int:
        total_beats = 0.0
        for measure in score["measures"]:
            total_beats += sum(
                note["beats"]
                for note in measure["voices"]["soprano"]
                if note["section_id"] == section_id and not note["is_rest"]
            )
        return int(total_beats // 4)

    assert full_measures("sec-1") == 16
    assert full_measures("sec-3") == 16
    assert full_measures("sec-5") == 16


def test_generate_melody_34_manual_pickup_enforces_exact_verse_measure_structure():
    payload = {
        "sections": [
            {
                "id": "v1",
                "label": "Verse",
                "is_verse": True,
                "text": "Amazing grace how sweet the sound\nThat saved a wretch like me",
            }
        ],
        "arrangement": [
            {"section_id": "v1", "is_verse": True, "anacrusis_mode": "manual", "anacrusis_beats": 2}
        ],
        "preferences": {"key": "C", "time_signature": "3/4", "tempo_bpm": 90, "bars_per_verse": 16},
    }

    res = client.post("/api/generate-melody", json=payload)

    assert res.status_code == 200
    score = res.json()["score"]
    assert len(score["measures"]) == 16

    first_measure = score["measures"][0]["voices"]["soprano"]
    first_measure_non_rest = sum(note["beats"] for note in first_measure if note["section_id"] == "sec-1" and not note["is_rest"])
    assert first_measure_non_rest == 1

    for measure in score["measures"]:
        total_beats = sum(note["beats"] for note in measure["voices"]["soprano"])
        assert total_beats == 3

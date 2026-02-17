import logging

from fastapi.testclient import TestClient

from app import main as main_module
from app.main import app
from app.models import CompositionPreferences, CompositionRequest, LyricSection
from app.services.composer import MelodyGenerationFailedError, generate_melody_score, harmonize_score
from app.services.score_validation import ValidationDiagnostics


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
    assert res.json() == {"valid": True, "errors": [], "warnings": []}


def test_validate_score_endpoint_reports_errors_and_request_id(monkeypatch):
    melody = generate_melody_score(_sample_request())

    monkeypatch.setattr(main_module, "validate_score_diagnostics", lambda *_args, **_kwargs: ValidationDiagnostics(fatal=["diagnostic: mismatch"], warnings=[]))

    res = client.post("/api/validate-score", json={"score": melody.model_dump()})

    assert res.status_code == 200
    payload = res.json()
    assert payload["valid"] is False
    assert "failed validation" in payload["message"]
    assert payload["request_id"]



def test_generate_melody_returns_warning_payload_without_failing(monkeypatch):
    warning = "Soprano strong-beat note 0 (F#4) conflicts with chord in measure 1."
    monkeypatch.setattr(main_module, "validate_score_diagnostics", lambda *_args, **_kwargs: ValidationDiagnostics(fatal=[], warnings=[warning]))

    res = client.post("/api/generate-melody", json=_sample_request().model_dump())

    assert res.status_code == 200
    assert warning in res.json()["warnings"]


def test_generate_melody_34_manual_pickup_bars_per_verse_16_phrase_barline_warning_does_not_422(monkeypatch):
    phrase_warning = "Lyric phrase ending at syllable sec-1-syl-8 ends at beat 7, not on a barline."

    def _warning_only_diagnostics(*_args, **_kwargs):
        return ValidationDiagnostics(fatal=[], warnings=[phrase_warning])

    monkeypatch.setattr(main_module, "validate_score_diagnostics", _warning_only_diagnostics)

    payload = {
        "sections": [
            {
                "id": "v1",
                "label": "Verse",
                "is_verse": True,
                "text": "Amazing grace how sweet the sound\nThat saved a wretch like me",
            },
            {
                "id": "v2",
                "label": "Verse",
                "is_verse": True,
                "text": "Twas grace that taught my heart to fear\nAnd grace my fears relieved",
            },
            {
                "id": "v3",
                "label": "Verse",
                "is_verse": True,
                "text": "Through many dangers toils and snares\nI have already come",
            },
        ],
        "arrangement": [
            {"section_id": "v1", "is_verse": True, "anacrusis_mode": "manual", "anacrusis_beats": 2},
            {"section_id": "v2", "is_verse": True, "anacrusis_mode": "manual", "anacrusis_beats": 2},
            {"section_id": "v3", "is_verse": True, "anacrusis_mode": "manual", "anacrusis_beats": 2},
        ],
        "preferences": {"key": "C", "time_signature": "3/4", "tempo_bpm": 90, "bars_per_verse": 16},
    }

    res = client.post("/api/generate-melody", json=payload)

    assert res.status_code == 200
    assert phrase_warning in res.json()["warnings"]

def test_compose_end_score_endpoint_runs_full_workflow():
    req = _sample_request()

    res = client.post("/api/compose-end-score", json=req.model_dump())

    assert res.status_code == 200
    payload = res.json()
    assert payload["melody"]["meta"]["stage"] == "melody"
    assert payload["satb"]["meta"]["stage"] == "satb"
    assert "input" in payload["composition_notes"]


def test_regenerate_endpoint_accepts_cluster_regenerate_payload():
    melody = generate_melody_score(_sample_request())

    res = client.post(
        "/api/regenerate-melody",
        json={
            "score": melody.model_dump(),
            "selected_units": ["verse"],
            "section_clusters": {"sec-1": "verse"},
        },
    )

    assert res.status_code == 200
    assert res.json()["score"]["meta"]["stage"] == "melody"


def test_regenerate_satb_endpoint_accepts_cluster_regenerate_payload():
    melody = generate_melody_score(_sample_request())
    satb = harmonize_score(melody)

    res = client.post(
        "/api/regenerate-satb",
        json={
            "score": satb.model_dump(),
            "selected_units": ["verse"],
            "section_clusters": {"sec-1": "verse"},
        },
    )

    assert res.status_code == 200
    assert res.json()["score"]["meta"]["stage"] == "satb"




def test_refine_endpoints_removed():
    melody = generate_melody_score(_sample_request())
    satb = harmonize_score(melody)

    melody_res = client.post("/api/refine-melody", json={"score": melody.model_dump(), "instruction": "legacy"})
    satb_res = client.post("/api/refine-satb", json={"score": satb.model_dump(), "instruction": "legacy"})

    assert melody_res.status_code == 404
    assert satb_res.status_code == 404


def test_regenerate_satb_rejects_melody_input_stage():
    melody = generate_melody_score(_sample_request())

    res = client.post(
        "/api/regenerate-satb",
        json={"score": melody.model_dump()},
    )

    assert res.status_code == 422
    assert "SATB regeneration failed" in res.json()["detail"]["message"]
    assert res.json()["detail"]["request_id"]




def test_regenerate_melody_manual_pickups_preserves_full_harmony_coverage():
    payload = {
        "sections": [
            {
                "id": "v1",
                "label": "Verse",
                "is_verse": True,
                "text": "Amazing grace how sweet the sound\nThat saved a wretch like me",
            },
            {
                "id": "v2",
                "label": "Verse",
                "is_verse": True,
                "text": "Twas grace that taught my heart to fear\nAnd grace my fears relieved",
            },
            {
                "id": "v3",
                "label": "Verse",
                "is_verse": True,
                "text": "Through many dangers toils and snares\nI have already come",
            },
        ],
        "arrangement": [
            {"section_id": "v1", "is_verse": True, "anacrusis_mode": "manual", "anacrusis_beats": 2},
            {"section_id": "v2", "is_verse": True, "anacrusis_mode": "manual", "anacrusis_beats": 2},
            {"section_id": "v3", "is_verse": True, "anacrusis_mode": "manual", "anacrusis_beats": 2},
        ],
        "preferences": {"key": "C", "time_signature": "3/4", "tempo_bpm": 90, "bars_per_verse": 16},
    }

    generate_res = client.post("/api/generate-melody", json=payload)

    assert generate_res.status_code == 200
    melody = generate_res.json()["score"]
    original_degrees = [ch["degree"] for ch in melody["chord_progression"]]

    regenerate_res = client.post(
        "/api/regenerate-melody",
        json={
            "score": melody,
            "selected_units": ["verse"],
            "section_clusters": {"sec-1": "verse", "sec-2": "verse", "sec-3": "verse"},
        },
    )

    assert regenerate_res.status_code == 200
    regenerated_score = regenerate_res.json()["score"]
    regenerated_degrees = [ch["degree"] for ch in regenerated_score["chord_progression"]]
    measure_numbers = {measure["number"] for measure in regenerated_score["measures"]}
    chord_measures = {chord["measure_number"] for chord in regenerated_score["chord_progression"]}

    assert regenerated_degrees != original_degrees
    assert measure_numbers == chord_measures
    assert len(regenerated_score["chord_progression"]) == len(regenerated_score["measures"]) == 48

def test_request_id_header_present_on_response():
    req = _sample_request()

    res = client.post("/api/compose-end-score", json=req.model_dump())

    assert res.status_code == 200
    assert res.headers.get("X-Request-ID")




def test_generate_melody_logs_final_failure_event(caplog, monkeypatch):
    diagnostics = [f"diagnostic-{idx}" for idx in range(12)]

    def _raise_failure(_payload):
        raise MelodyGenerationFailedError(
            "friendly failure",
            attempt_count=5,
            final_exception_type="ScoreValidationError",
            final_diagnostics=diagnostics,
        )

    monkeypatch.setattr(main_module, "generate_melody_score", _raise_failure)

    with caplog.at_level(logging.ERROR):
        res = client.post("/api/generate-melody", json=_sample_request().model_dump())

    assert res.status_code == 422
    final_failure_events = [record for record in caplog.records if getattr(record, "event", "") == "melody_generation_final_failure"]
    assert len(final_failure_events) == 1
    event = final_failure_events[0]
    assert event.attempt_count == 5
    assert event.final_exception_type == "ScoreValidationError"
    assert event.final_diagnostics == diagnostics[:10]

def test_validation_failure_logs_event(caplog, monkeypatch):
    melody = generate_melody_score(_sample_request())

    monkeypatch.setattr(main_module, "validate_score_diagnostics", lambda *_args, **_kwargs: ValidationDiagnostics(fatal=["diagnostic: timing mismatch"], warnings=[]))

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
        return sum(
            1
            for measure in score["measures"]
            if any(note["section_id"] == section_id for note in measure["voices"]["soprano"])
        )

    assert full_measures("sec-1") == 16
    assert full_measures("sec-3") == 16
    assert full_measures("sec-5") == 16




def test_generate_melody_34_pickup_bars_per_verse_16_stretches_lyric_bearing_durations_without_lyricless_padding():
    base_payload = {
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
        "preferences": {"key": "C", "time_signature": "3/4", "tempo_bpm": 90},
    }

    baseline_payload = {**base_payload, "preferences": {**base_payload["preferences"], "bars_per_verse": 8}}
    target_payload = {**base_payload, "preferences": {**base_payload["preferences"], "bars_per_verse": 16}}

    baseline_res = client.post("/api/generate-melody", json=baseline_payload)
    target_res = client.post("/api/generate-melody", json=target_payload)

    assert baseline_res.status_code == 200
    assert target_res.status_code == 200

    target_score = target_res.json()["score"]

    def measures_for(score: dict, section_id: str) -> int:
        return sum(
            1
            for measure in score["measures"]
            if any(note["section_id"] == section_id for note in measure["voices"]["soprano"])
        )

    assert measures_for(target_score, "sec-1") == 16

    first_measure = target_score["measures"][0]["voices"]["soprano"]
    first_measure_nonpickup = sum(
        note["beats"]
        for note in first_measure
        if note["section_id"] == "sec-1" and not note["is_rest"]
    )
    assert first_measure_nonpickup == 1

    verse_lyricless = [
        idx
        for idx, note in enumerate(
            n
            for measure in target_score["measures"]
            for n in measure["voices"]["soprano"]
            if n["section_id"] == "sec-1"
        )
        if (not note["is_rest"]) and note["lyric_syllable_id"] is None
    ]
    assert verse_lyricless == []

    def avg_syllable_duration(score: dict, section_id: str) -> float:
        beats_by_syllable: dict[str, float] = {}
        for measure in score["measures"]:
            for note in measure["voices"]["soprano"]:
                if note["section_id"] != section_id or note["is_rest"] or note["lyric_syllable_id"] is None:
                    continue
                beats_by_syllable[note["lyric_syllable_id"]] = beats_by_syllable.get(note["lyric_syllable_id"], 0.0) + note["beats"]
        return sum(beats_by_syllable.values()) / max(1, len(beats_by_syllable))

    baseline_avg = avg_syllable_duration(baseline_res.json()["score"], "sec-1")
    target_avg = avg_syllable_duration(target_score, "sec-1")
    assert target_avg > baseline_avg


def test_generate_melody_34_manual_pickup_amazing_grace_three_verses_stays_at_16_measures_each():
    payload = {
        "sections": [
            {
                "id": "v1",
                "label": "Verse",
                "is_verse": True,
                "text": "Amazing grace how sweet the sound\nThat saved a wretch like me",
            },
            {
                "id": "v2",
                "label": "Verse",
                "is_verse": True,
                "text": "Twas grace that taught my heart to fear\nAnd grace my fears relieved",
            },
            {
                "id": "v3",
                "label": "Verse",
                "is_verse": True,
                "text": "Through many dangers toils and snares\nI have already come",
            },
        ],
        "arrangement": [
            {"section_id": "v1", "is_verse": True, "anacrusis_mode": "manual", "anacrusis_beats": 2},
            {"section_id": "v2", "is_verse": True, "anacrusis_mode": "manual", "anacrusis_beats": 2},
            {"section_id": "v3", "is_verse": True, "anacrusis_mode": "manual", "anacrusis_beats": 2},
        ],
        "preferences": {"key": "C", "time_signature": "3/4", "tempo_bpm": 90, "bars_per_verse": 16},
    }

    res = client.post("/api/generate-melody", json=payload)

    assert res.status_code == 200
    score = res.json()["score"]

    def measures_for(section_id: str) -> int:
        return sum(
            1
            for measure in score["measures"]
            if any(note["section_id"] == section_id for note in measure["voices"]["soprano"])
        )

    assert measures_for("sec-1") == 16
    assert measures_for("sec-2") == 16
    assert measures_for("sec-3") == 16

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

from fastapi.testclient import TestClient

from app.main import app
from app.models import ArrangementItem, CompositionPreferences, CompositionRequest, LyricSection
from app.services.composer import generate_melody_score, harmonize_score


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


def test_pdf_export_returns_501_with_friendly_detail_and_request_id_header():
    satb = _satb_score_with_verses()

    pdf_res = client.post("/api/export-pdf", json={"score": satb.model_dump()})

    assert pdf_res.status_code == 501
    assert pdf_res.headers["x-request-id"]
    assert pdf_res.json() == {
        "detail": {
            "message": "PDF export is now generated in the browser. Please update the client.",
            "request_id": pdf_res.headers["x-request-id"],
        }
    }


def test_pdf_export_returns_501_for_melody_stage_too():
    melody = generate_melody_score(
        CompositionRequest(
            sections=[LyricSection(id="verse-1", label="Verse 1", is_verse=True, text="Morning light renews us")],
            arrangement=[ArrangementItem(section_id="verse-1", is_verse=True)],
            preferences=CompositionPreferences(key="D", time_signature="4/4", tempo_bpm=90),
        )
    )

    pdf_res = client.post("/api/export-pdf", json={"score": melody.model_dump()})

    assert pdf_res.status_code == 501
    assert pdf_res.json()["detail"]["request_id"] == pdf_res.headers["x-request-id"]

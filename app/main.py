from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from app.models import (
    CompositionRequest,
    EndScoreResponse,
    HarmonizeRequest,
    MelodyResponse,
    PDFExportRequest,
    RefineRequest,
    SATBResponse,
)
from app.services.composer import generate_melody_score, harmonize_score, refine_score
from app.services.musicxml_export import export_musicxml
from app.services.pdf_export import build_score_pdf
from app.services.score_normalization import normalize_score_for_rendering
from app.services.score_validation import validate_score

app = FastAPI(title="Choir Composer")
app.mount("/static", StaticFiles(directory="app/static"), name="static")


def _require_score_stage(score, expected_stage: str, action: str) -> None:
    if score.meta.stage != expected_stage:
        raise ValueError(f"{action} requires a {expected_stage} score, but received stage '{score.meta.stage}'.")


def _require_valid_score(score, action: str) -> None:
    errors = validate_score(normalize_score_for_rendering(score))
    if errors:
        raise ValueError(f"{action} requires a valid input score. Resolve validation errors before continuing.")


def _extract_melody_from_satb(score):
    melody = score.model_copy(deep=True)
    melody.meta = melody.meta.model_copy(update={"stage": "melody"})
    for measure in melody.measures:
        soprano_voice = [note.model_copy(deep=True) for note in measure.voices["soprano"]]
        measure.voices["alto"] = [note.model_copy(update={"pitch": "REST", "is_rest": True}) for note in soprano_voice]
        measure.voices["tenor"] = [note.model_copy(update={"pitch": "REST", "is_rest": True}) for note in soprano_voice]
        measure.voices["bass"] = [note.model_copy(update={"pitch": "REST", "is_rest": True}) for note in soprano_voice]
    return melody


@app.get("/")
def index() -> FileResponse:
    return FileResponse("app/static/index.html")


@app.post("/api/generate-melody", response_model=MelodyResponse)
def generate_melody_endpoint(payload: CompositionRequest):
    try:
        return MelodyResponse(score=normalize_score_for_rendering(generate_melody_score(payload)))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/refine-melody", response_model=MelodyResponse)
def refine_melody_endpoint(payload: RefineRequest):
    try:
        _require_score_stage(payload.score, "melody", "Melody refinement")
        _require_valid_score(payload.score, "Melody refinement")
        return MelodyResponse(
            score=normalize_score_for_rendering(refine_score(
                payload.score,
                payload.instruction,
                payload.regenerate,
                payload.selected_clusters,
                payload.section_clusters,
            ))
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/generate-satb", response_model=SATBResponse)
def generate_satb_endpoint(payload: HarmonizeRequest):
    try:
        _require_score_stage(payload.score, "melody", "SATB generation")
        _require_valid_score(payload.score, "SATB generation")
        score = normalize_score_for_rendering(harmonize_score(payload.score))
        return SATBResponse(score=score, harmonization_notes="Chord-led SATB voicing with diatonic progression integrity checks.")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/refine-satb", response_model=SATBResponse)
def refine_satb_endpoint(payload: RefineRequest):
    try:
        _require_score_stage(payload.score, "satb", "SATB refinement")
        _require_valid_score(payload.score, "SATB refinement")
        melody_projection = _extract_melody_from_satb(payload.score)
        refined_melody = refine_score(
            melody_projection,
            payload.instruction,
            payload.regenerate,
            payload.selected_clusters,
            payload.section_clusters,
        )
        score = normalize_score_for_rendering(harmonize_score(refined_melody))
        return SATBResponse(score=score, harmonization_notes="Refined SATB while preserving progression authority.")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/compose-end-score", response_model=EndScoreResponse)
def compose_end_score_endpoint(payload: CompositionRequest):
    try:
        melody = normalize_score_for_rendering(generate_melody_score(payload))
        satb = normalize_score_for_rendering(harmonize_score(melody))
        return EndScoreResponse(
            melody=melody,
            satb=satb,
            composition_notes="Composed through the required workflow: input → melody → SATB end score.",
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/validate-score")
def validate_score_endpoint(payload: HarmonizeRequest):
    errors = validate_score(normalize_score_for_rendering(payload.score))
    return {"valid": len(errors) == 0, "errors": errors}


@app.post("/api/export-pdf")
def export_pdf_endpoint(payload: PDFExportRequest):
    try:
        _require_score_stage(payload.score, "satb", "PDF export")
        _require_valid_score(payload.score, "PDF export")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    content = build_score_pdf(normalize_score_for_rendering(payload.score))
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=choir-score.pdf"},
    )


@app.post("/api/export-musicxml")
def export_musicxml_endpoint(payload: PDFExportRequest):
    try:
        _require_score_stage(payload.score, "satb", "MusicXML export")
        _require_valid_score(payload.score, "MusicXML export")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    content = export_musicxml(normalize_score_for_rendering(payload.score))
    return Response(
        content=content,
        media_type="application/vnd.recordare.musicxml+xml",
        headers={"Content-Disposition": "attachment; filename=choir-score.musicxml"},
    )

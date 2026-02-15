from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from app.models import (
    CompositionRequest,
    HarmonizeRequest,
    MelodyResponse,
    PDFExportRequest,
    RefineRequest,
    SATBResponse,
)
from app.services.composer import generate_melody_score, harmonize_score, refine_score
from app.services.musicxml_export import export_musicxml
from app.services.pdf_export import build_score_pdf
from app.services.score_validation import validate_score

app = FastAPI(title="Choir Composer")
app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse("app/static/index.html")


@app.post("/api/generate-melody", response_model=MelodyResponse)
def generate_melody_endpoint(payload: CompositionRequest):
    try:
        return MelodyResponse(score=generate_melody_score(payload))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/refine-melody", response_model=MelodyResponse)
def refine_melody_endpoint(payload: RefineRequest):
    try:
        return MelodyResponse(score=refine_score(payload.score, payload.instruction, payload.regenerate))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/generate-satb", response_model=SATBResponse)
def generate_satb_endpoint(payload: HarmonizeRequest):
    try:
        score = harmonize_score(payload.score)
        return SATBResponse(score=score, harmonization_notes="Chord-led SATB voicing with diatonic progression integrity checks.")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/validate-score")
def validate_score_endpoint(payload: HarmonizeRequest):
    errors = validate_score(payload.score)
    return {"valid": len(errors) == 0, "errors": errors}


@app.post("/api/export-pdf")
def export_pdf_endpoint(payload: PDFExportRequest):
    content = build_score_pdf(payload.score)
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=choir-score.pdf"},
    )


@app.post("/api/export-musicxml")
def export_musicxml_endpoint(payload: PDFExportRequest):
    content = export_musicxml(payload.score)
    return Response(
        content=content,
        media_type="application/vnd.recordare.musicxml+xml",
        headers={"Content-Disposition": "attachment; filename=choir-score.musicxml"},
    )

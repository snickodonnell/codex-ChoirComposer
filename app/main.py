from __future__ import annotations

import logging
import time
import json

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from app.logging_utils import (
    clear_request_context,
    configure_logging,
    current_request_id,
    log_event,
    new_request_id,
    request_elapsed_ms,
    set_request_context,
)
from app.models import (
    ClientLogEvent,
    CompositionRequest,
    EngravingPreviewArtifact,
    EngravingPreviewRequest,
    EngravingPreviewResponse,
    EndScoreResponse,
    HarmonizeRequest,
    MelodyResponse,
    PDFExportRequest,
    RegenerateRequest,
    SATBResponse,
)
from app.services.composer import MelodyGenerationFailedError, generate_melody_score, harmonize_score, regenerate_score
from app.services.musicxml_export import export_musicxml
from app.services.engraving_preview import DEFAULT_LAYOUT, EngravingLayoutConfig, EngravingOptions, preview_service
from app.services.engraving_export import export_service
from app.services.score_normalization import normalize_score_for_rendering
from app.services.score_validation import validate_score, validate_score_diagnostics

configure_logging()
logger = logging.getLogger(__name__)

app = FastAPI(title="Choir Composer")
app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or new_request_id()
    set_request_context(request_id=request_id, route=request.url.path, method=request.method)
    started = time.perf_counter()
    log_event(logger, "request_started")
    try:
        response = await call_next(request)
    except Exception:
        elapsed_ms = request_elapsed_ms(started)
        log_event(logger, "request_completed", status_code=500, duration_ms=elapsed_ms)
        raise

    elapsed_ms = request_elapsed_ms(started)
    log_event(logger, "request_completed", status_code=response.status_code, duration_ms=elapsed_ms)
    response.headers["X-Request-ID"] = request_id
    clear_request_context()
    return response


@app.exception_handler(Exception)
async def global_exception_handler(_request: Request, exc: Exception):
    request_id = current_request_id()
    logger.exception(
        "unhandled_exception",
        extra={"event": "unhandled_exception", "request_id": request_id},
    )
    response = JSONResponse(
        status_code=500,
        content={
            "detail": "Something went wrong while processing your request. Please try again.",
            "request_id": request_id,
        },
        headers={"X-Request-ID": request_id},
    )
    clear_request_context()
    return response


def _friendly_validation_error(action: str, diagnostics: list[str], level: int = logging.ERROR) -> ValueError:
    log_event(logger, "validation_failed", level=level, action=action, diagnostics=diagnostics)
    return ValueError(f"{action} could not proceed due to invalid score data.")


def _require_score_stage(score, expected_stage: str, action: str) -> None:
    if score.meta.stage != expected_stage:
        raise ValueError(f"{action} requires a {expected_stage} score, but received stage '{score.meta.stage}'.")


def _require_valid_score(score, action: str) -> list[str]:
    report = validate_score_diagnostics(normalize_score_for_rendering(score))
    if report.fatal:
        raise _friendly_validation_error(action, report.fatal, level=logging.ERROR)
    if report.warnings:
        log_event(logger, "validation_failed", level=logging.WARNING, action=action, diagnostics=report.warnings)
    return report.warnings


def _evaluate_melody_validation_gate(score) -> list[str]:
    report = validate_score_diagnostics(normalize_score_for_rendering(score))
    preview_diagnostics = [*report.fatal, *report.warnings][:3]
    will_return_status = 422 if report.fatal else 200
    log_event(
        logger,
        "melody_validation_gate_decision",
        fatal_count=len(report.fatal),
        warning_count=len(report.warnings),
        will_return_status=will_return_status,
        diagnostics_preview=preview_diagnostics,
    )
    if report.fatal:
        raise _friendly_validation_error("Melody generation", report.fatal, level=logging.ERROR)
    if report.warnings:
        log_event(logger, "validation_failed", level=logging.WARNING, action="Melody generation", diagnostics=report.warnings)
    return report.warnings


def _extract_melody_from_satb(score):
    melody = score.model_copy(deep=True)
    melody.meta = melody.meta.model_copy(update={"stage": "melody"})
    for measure in melody.measures:
        soprano_voice = [note.model_copy(deep=True) for note in measure.voices["soprano"]]
        measure.voices["alto"] = [note.model_copy(update={"pitch": "REST", "is_rest": True}) for note in soprano_voice]
        measure.voices["tenor"] = [note.model_copy(update={"pitch": "REST", "is_rest": True}) for note in soprano_voice]
        measure.voices["bass"] = [note.model_copy(update={"pitch": "REST", "is_rest": True}) for note in soprano_voice]
    return melody


def _handle_user_error(action: str, exc: ValueError) -> HTTPException:
    log_event(logger, "request_failed", level=logging.WARNING, action=action, reason=str(exc))
    return HTTPException(
        status_code=422,
        detail={
            "message": f"{action} failed. Please adjust inputs and try again.",
            "request_id": current_request_id(),
        },
    )


@app.get("/")
def index() -> FileResponse:
    return FileResponse("app/static/index.html")


@app.post("/api/generate-melody", response_model=MelodyResponse)
def generate_melody_endpoint(payload: CompositionRequest):
    arrangement_labels = [item.section_id for item in payload.arrangement]
    section_by_id = { (section.id or f"section-{idx}"): section for idx, section in enumerate(payload.sections, start=1) }
    selected_units = [
        ("verse" if section_by_id.get(item.section_id) and section_by_id[item.section_id].is_verse else (section_by_id.get(item.section_id).label if section_by_id.get(item.section_id) else ""))
        for item in payload.arrangement
    ]
    log_event(
        logger,
        "arrangement_inputs_received",
        key=payload.preferences.key,
        mode=payload.preferences.primary_mode,
        time_signature=payload.preferences.time_signature,
        tempo_bpm=payload.preferences.tempo_bpm,
        bars_per_verse=payload.preferences.bars_per_verse,
        section_labels=[section.label for section in payload.sections],
        arrangement_order=arrangement_labels,
        music_units_selected=[u for u in selected_units if u],
    )
    try:
        score = normalize_score_for_rendering(generate_melody_score(payload))
        warnings = _evaluate_melody_validation_gate(score)
        return MelodyResponse(score=score, warnings=warnings)
    except MelodyGenerationFailedError as exc:
        log_event(
            logger,
            "melody_generation_final_failure",
            level=logging.ERROR,
            request_id=current_request_id(),
            attempt_count=exc.attempt_count,
            final_exception_type=exc.final_exception_type,
            final_diagnostics=exc.final_diagnostics[:10],
        )
        raise _handle_user_error("Melody generation", exc) from exc
    except ValueError as exc:
        raise _handle_user_error("Melody generation", exc) from exc


@app.post("/api/regenerate-melody", response_model=MelodyResponse)
def regenerate_melody_endpoint(payload: RegenerateRequest):
    try:
        _require_score_stage(payload.score, "melody", "Melody regeneration")
        _require_valid_score(payload.score, "Melody regeneration")
        log_event(
            logger,
            "draft_version_operation",
            operation="update",
            target="melody",
            regenerate=True,
            selected_units=payload.selected_units or payload.selected_clusters,
        )
        return MelodyResponse(
            score=normalize_score_for_rendering(regenerate_score(
                payload.score,
                payload.selected_units,
                payload.selected_clusters,
                payload.section_clusters,
            ))
        )
    except ValueError as exc:
        raise _handle_user_error("Melody regeneration", exc) from exc


@app.post("/api/generate-satb", response_model=SATBResponse)
def generate_satb_endpoint(payload: HarmonizeRequest):
    try:
        _require_score_stage(payload.score, "melody", "SATB generation")
        melody_warnings = _require_valid_score(payload.score, "SATB generation")
        score = normalize_score_for_rendering(harmonize_score(payload.score))
        satb_warnings = _require_valid_score(score, "SATB generation")
        warnings = list(dict.fromkeys([*melody_warnings, *satb_warnings]))
        log_event(logger, "draft_version_operation", operation="create", target="satb")
        return SATBResponse(
            score=score,
            harmonization_notes="Chord-led SATB voicing with diatonic progression integrity checks.",
            warnings=warnings,
        )
    except ValueError as exc:
        raise _handle_user_error("SATB generation", exc) from exc


@app.post("/api/regenerate-satb", response_model=SATBResponse)
def regenerate_satb_endpoint(payload: RegenerateRequest):
    try:
        _require_score_stage(payload.score, "satb", "SATB regeneration")
        _require_valid_score(payload.score, "SATB regeneration")
        log_event(logger, "draft_version_operation", operation="update", target="satb", regenerate=True)
        melody_projection = _extract_melody_from_satb(payload.score)
        regenerated_melody = regenerate_score(
            melody_projection,
            payload.selected_units,
            payload.selected_clusters,
            payload.section_clusters,
        )
        score = normalize_score_for_rendering(harmonize_score(regenerated_melody))
        return SATBResponse(score=score, harmonization_notes="Regenerated SATB while preserving progression authority.")
    except ValueError as exc:
        raise _handle_user_error("SATB regeneration", exc) from exc



@app.post("/api/client-log")
def client_log_endpoint(payload: ClientLogEvent):
    log_event(
        logger,
        "client_playback_event",
        client_ts=payload.ts,
        client_event=payload.event,
        playback_type=payload.type,
        playback_id=payload.id,
        reason=payload.reason,
        offset_seconds=payload.offsetSeconds,
        total_seconds=payload.totalSeconds,
        event_count=payload.events,
        progress_seconds=payload.progressSeconds,
    )
    return {"ok": True}

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
        raise _handle_user_error("End-score composition", exc) from exc


@app.post("/api/validate-score")
def validate_score_endpoint(payload: HarmonizeRequest):
    report = validate_score_diagnostics(normalize_score_for_rendering(payload.score))
    if report.fatal:
        log_event(logger, "validation_failed", level=logging.ERROR, action="Score validation", diagnostics=report.fatal)
        return {
            "valid": False,
            "message": "The score failed validation. Please adjust your draft and try again.",
            "request_id": current_request_id(),
            "errors": report.fatal,
            "warnings": report.warnings,
        }
    if report.warnings:
        log_event(logger, "validation_failed", level=logging.WARNING, action="Score validation", diagnostics=report.warnings)
    else:
        log_event(logger, "validation_passed", action="Score validation")
    return {"valid": True, "errors": [], "warnings": report.warnings}


@app.post("/api/export-pdf")
def export_pdf_endpoint(payload: PDFExportRequest):
    action = "PDF export"
    try:
        _require_score_stage(payload.score, "satb", action)
    except ValueError as exc:
        raise _handle_user_error(action, exc) from exc

    normalized_score = normalize_score_for_rendering(payload.score)
    diagnostics = validate_score_diagnostics(normalized_score)
    if diagnostics.fatal:
        raise _handle_user_error(action, _friendly_validation_error(action, diagnostics.fatal))
    if diagnostics.warnings:
        log_event(logger, "validation_failed", level=logging.WARNING, action=action, diagnostics=diagnostics.warnings)

    log_event(logger, "export_started", format="pdf")
    try:
        content = export_service.export_pdf(normalized_score)
    except RuntimeError as exc:
        log_event(logger, "export_failed", format="pdf", level=logging.ERROR, reason=str(exc))
        raise HTTPException(status_code=500, detail={"message": str(exc), "request_id": current_request_id()}) from exc

    log_event(logger, "export_completed", format="pdf", output_size_bytes=len(content))
    response_headers = {
        "Content-Disposition": "attachment; filename=choir-score.pdf",
        "X-Request-ID": current_request_id(),
    }
    if diagnostics.warnings:
        response_headers["X-Export-Warnings"] = json.dumps(diagnostics.warnings)

    return Response(
        content=content,
        media_type="application/pdf",
        headers=response_headers,
    )


@app.post("/api/export-musicxml")
def export_musicxml_endpoint(payload: PDFExportRequest):
    try:
        _require_score_stage(payload.score, "satb", "MusicXML export")
        _require_valid_score(payload.score, "MusicXML export")
    except ValueError as exc:
        raise _handle_user_error("MusicXML export", exc) from exc

    log_event(logger, "export_started", format="musicxml")
    content = export_musicxml(normalize_score_for_rendering(payload.score))
    log_event(logger, "export_completed", format="musicxml", output_size_bytes=len(content.encode("utf-8")))
    return Response(
        content=content,
        media_type="application/vnd.recordare.musicxml+xml",
        headers={"Content-Disposition": "attachment; filename=choir-score.musicxml"},
    )


@app.post("/api/engrave/preview", response_model=EngravingPreviewResponse)
def engrave_preview_endpoint(payload: EngravingPreviewRequest):
    action = "Engraving preview"
    try:
        _require_score_stage(payload.score, payload.preview_mode, action)
        warnings = _require_valid_score(payload.score, action)
    except ValueError as exc:
        raise _handle_user_error(action, exc) from exc

    options = EngravingOptions(
        include_all_pages=payload.include_all_pages,
        layout=EngravingLayoutConfig(
            page_width=DEFAULT_LAYOUT.page_width,
            page_height=DEFAULT_LAYOUT.page_height,
            scale=payload.scale,
            system_spacing=DEFAULT_LAYOUT.system_spacing,
            staff_spacing=DEFAULT_LAYOUT.staff_spacing,
            margin_top=DEFAULT_LAYOUT.margin_top,
            margin_bottom=DEFAULT_LAYOUT.margin_bottom,
            margin_left=DEFAULT_LAYOUT.margin_left,
            margin_right=DEFAULT_LAYOUT.margin_right,
        ),
    )
    log_event(
        logger,
        "engraving_preview_started",
        preview_mode=payload.preview_mode,
        include_all_pages=payload.include_all_pages,
        scale=payload.scale,
    )

    try:
        artifacts, cache_hit = preview_service.render_preview(normalize_score_for_rendering(payload.score), options)
    except RuntimeError as exc:
        log_event(logger, "engraving_preview_failed", level=logging.ERROR, reason=str(exc))
        raise HTTPException(status_code=500, detail={"message": str(exc), "request_id": current_request_id()}) from exc

    response = EngravingPreviewResponse(
        preview_mode=payload.preview_mode,
        cache_hit=cache_hit,
        artifacts=[EngravingPreviewArtifact(page=item.page, svg=item.svg) for item in artifacts],
        warnings=warnings,
    )
    log_event(logger, "engraving_preview_completed", preview_mode=payload.preview_mode, pages=len(response.artifacts), cache_hit=cache_hit)
    return response

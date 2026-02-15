from __future__ import annotations

from io import BytesIO

from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas

from app.models import CanonicalScore


def build_score_pdf(score: CanonicalScore) -> bytes:
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    _, height = letter

    def draw_header(title: str) -> float:
        c.setFont("Helvetica-Bold", 16)
        c.drawString(0.75 * inch, height - 0.8 * inch, title)
        c.setFont("Helvetica", 10)
        c.drawString(
            0.75 * inch,
            height - 1.1 * inch,
            f"Key: {score.meta.key}   Time: {score.meta.time_signature}   Tempo: {score.meta.tempo_bpm} BPM",
        )
        return height - 1.4 * inch

    y = draw_header("Choir Composition")
    c.setFont("Courier", 8)
    voices = ["soprano", "alto", "tenor", "bass"]

    chords = {c.measure_number: c.symbol for c in score.chord_progression}

    section_pause_map = {section.id: section.pause_beats for section in score.sections}
    previous_measure_section: str | None = None

    for measure in score.measures:
        if y < 1.2 * inch:
            c.showPage()
            y = draw_header("Choir Composition (cont.)")
            c.setFont("Courier", 8)
        first_section_note = next((n for n in measure.voices['soprano'] if n.section_id != 'padding'), None)
        current_measure_section = first_section_note.section_id if first_section_note else previous_measure_section
        if previous_measure_section and current_measure_section and previous_measure_section != current_measure_section and section_pause_map.get(previous_measure_section, 0) > 0:
            c.setFont("Helvetica-Bold", 9)
            c.drawString(0.75 * inch, y, "‖ Section boundary")
            y -= 0.14 * inch

        c.setFont("Helvetica-Bold", 10)
        chord_symbol = chords.get(measure.number, "—")
        c.drawString(0.75 * inch, y, f"Measure {measure.number}   Chord: {chord_symbol}")
        y -= 0.18 * inch
        c.setFont("Courier", 8)
        for voice in voices:
            notes = measure.voices[voice]
            tokens = [
                ("REST" if n.is_rest else n.pitch) + f":{n.beats:g}" + (f"[{n.lyric}]" if n.lyric else "")
                for n in notes
            ]
            c.drawString(0.9 * inch, y, f"{voice[0].upper()}: {' | '.join(tokens)}")
            y -= 0.16 * inch
        y -= 0.06 * inch
        if current_measure_section:
            previous_measure_section = current_measure_section

    c.showPage()
    c.save()
    buffer.seek(0)
    return buffer.read()

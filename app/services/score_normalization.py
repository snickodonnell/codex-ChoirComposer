from __future__ import annotations

from app.models import CanonicalScore, ScoreChord, ScoreMeasure, ScoreNote, VoiceName
from app.services.music_theory import chord_symbol, parse_key, triad_pitch_classes
from app.services.score_validation import beats_per_measure


VOICE_NAMES: tuple[VoiceName, ...] = ("soprano", "alto", "tenor", "bass")


def normalize_score_for_rendering(score: CanonicalScore) -> CanonicalScore:
    beat_cap = beats_per_measure(score.meta.time_signature)
    per_voice_measures = {
        voice: _normalize_voice_stream(_flatten_voice(score, voice), beat_cap)
        for voice in VOICE_NAMES
    }
    measure_count = max((len(measures) for measures in per_voice_measures.values()), default=0)

    for voice, measures in per_voice_measures.items():
        while len(measures) < measure_count:
            measures.append([_rest(beat_cap)])

    normalized_measures: list[ScoreMeasure] = []
    for idx in range(measure_count):
        normalized_measures.append(
            ScoreMeasure(
                number=idx + 1,
                voices={voice: per_voice_measures[voice][idx] for voice in VOICE_NAMES},
            )
        )

    return score.model_copy(
        update={
            "measures": normalized_measures,
            "chord_progression": _normalize_harmony_coverage(score, normalized_measures),
        }
    )


def _flatten_voice(score: CanonicalScore, voice: VoiceName) -> list[ScoreNote]:
    notes: list[ScoreNote] = []
    for measure in score.measures:
        notes.extend(measure.voices.get(voice, []))
    return notes


def _normalize_voice_stream(notes: list[ScoreNote], beat_cap: float) -> list[list[ScoreNote]]:
    if not notes:
        return []

    measures: list[list[ScoreNote]] = []
    current: list[ScoreNote] = []
    used = 0.0

    for note in notes:
        remaining = note.beats
        first_chunk = True
        while remaining > 1e-9:
            room = beat_cap - used
            if room <= 1e-9:
                measures.append(current)
                current = []
                used = 0.0
                room = beat_cap

            chunk = min(remaining, room)
            current.append(_copy_note_chunk(note, chunk, first_chunk))
            used += chunk
            remaining -= chunk
            first_chunk = False

            if used >= beat_cap - 1e-9:
                measures.append(current)
                current = []
                used = 0.0

    if current:
        if used < beat_cap - 1e-9:
            current.append(_rest(beat_cap - used))
        measures.append(current)

    return measures


def _copy_note_chunk(note: ScoreNote, beats: float, first_chunk: bool) -> ScoreNote:
    if note.is_rest:
        return note.model_copy(update={"beats": beats})
    if first_chunk:
        return note.model_copy(update={"beats": beats})
    return note.model_copy(
        update={
            "beats": beats,
            "lyric": None,
            "lyric_mode": "tie_continue",
        }
    )


def _rest(beats: float) -> ScoreNote:
    return ScoreNote(pitch="REST", beats=beats, is_rest=True, section_id="padding")


def _normalize_harmony_coverage(score: CanonicalScore, measures: list[ScoreMeasure]) -> list[ScoreChord]:
    measure_count = len(measures)
    scale = parse_key(score.meta.key, score.meta.primary_mode)
    existing: dict[int, ScoreChord] = {}
    for chord in sorted(score.chord_progression, key=lambda ch: ch.measure_number):
        if 1 <= chord.measure_number <= measure_count and chord.measure_number not in existing:
            existing[chord.measure_number] = chord

    repaired: list[ScoreChord] = []
    for measure_number in range(1, measure_count + 1):
        chord = existing.get(measure_number)
        if chord:
            repaired.append(chord)
            continue
        section_id = _first_section_id(measures[measure_number - 1])
        repaired.append(
            ScoreChord(
                measure_number=measure_number,
                section_id=section_id,
                degree=1,
                symbol=chord_symbol(scale, 1),
                pitch_classes=triad_pitch_classes(scale, 1),
            )
        )

    return repaired


def _first_section_id(measure: ScoreMeasure) -> str:
    for note in measure.voices["soprano"]:
        if note.section_id != "padding":
            return note.section_id
    return "padding"


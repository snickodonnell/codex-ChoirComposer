from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from app.models import CanonicalScore, VoiceName
from app.services.music_theory import NOTE_TO_SEMITONE, VOICE_RANGES, VOICE_TESSITURA, normalize_note_name, parse_key, pitch_to_midi, triad_pitch_classes

MAX_MELODIC_LEAP = 7


@dataclass
class ValidationDiagnostics:
    fatal: list[str]
    warnings: list[str]


def _is_warning_diagnostic(message: str) -> bool:
    warning_prefixes = (
        "Soprano strong-beat note",
        "Chord ",
        "Potential parallel",
        "Wide spacing",
    )
    warning_fragments = (
        "Lyric phrase ending",
        "is outside chord tones",
        "leap too large",
        "in extreme tessitura",
        "out of range",
    )
    return message.startswith(warning_prefixes) or any(fragment in message for fragment in warning_fragments)


def beats_per_measure(time_signature: str) -> float:
    top, bottom = time_signature.split("/")
    return int(top) * (4 / int(bottom))


def validate_score(score: CanonicalScore, primary_mode: str | None = None) -> list[str]:
    report = validate_score_diagnostics(score, primary_mode)
    return [*report.fatal, *report.warnings]


def validate_score_diagnostics(score: CanonicalScore, primary_mode: str | None = None) -> ValidationDiagnostics:
    errors: list[str] = []
    effective_mode = primary_mode if primary_mode is not None else score.meta.primary_mode
    target = beats_per_measure(score.meta.time_signature)

    for measure in score.measures:
        for voice, notes in measure.voices.items():
            total = sum(n.beats for n in notes)
            if abs(total - target) > 1e-6:
                errors.append(f"Measure {measure.number} voice {voice} has {total:g} beats; expected {target:g}.")

    errors.extend(_validate_chord_progression(score, effective_mode))
    errors.extend(_validate_lyric_mapping(score))
    errors.extend(_validate_phrase_barline_alignment(score))
    errors.extend(_validate_pickup_measure_capacities(score))
    errors.extend(_validate_ranges_and_motion(score))
    errors.extend(_validate_harmonic_integrity(score))

    if score.meta.stage == "satb":
        errors.extend(_validate_voice_separation(score))

    fatal = [error for error in errors if not _is_warning_diagnostic(error)]
    warnings = [error for error in errors if _is_warning_diagnostic(error)]
    return ValidationDiagnostics(fatal=fatal, warnings=warnings)


def _flatten_voice(score: CanonicalScore, voice: VoiceName):
    out = []
    for m in score.measures:
        out.extend(m.voices.get(voice, []))
    return out


def _is_strong_beat(position: float, time_signature: str) -> bool:
    top, bottom = [int(p) for p in time_signature.split("/")]
    quarter_position = position * (bottom / 4)
    if top == 4:
        return abs(quarter_position % 2) < 1e-9
    if top == 6 and bottom == 8:
        return abs(position) < 1e-9 or abs(position - 1.5) < 1e-9
    return abs(quarter_position % 1) < 1e-9




def _chord_symbol_pitch_classes(symbol: str) -> set[int] | None:
    cleaned = symbol.strip()
    if not cleaned:
        return None

    root = cleaned[0]
    rest = cleaned[1:]
    if rest and rest[0] in {"#", "b"}:
        root += rest[0]
        rest = rest[1:]

    normalized_root = normalize_note_name(root)
    root_pc = NOTE_TO_SEMITONE[normalized_root]

    quality = rest.lower()
    if quality.startswith("maj"):
        quality = ""
    intervals = [0, 3, 7] if quality.startswith("m") and not quality.startswith("maj") else [0, 4, 7]
    if "dim" in quality:
        intervals = [0, 3, 6]
    return {(root_pc + iv) % 12 for iv in intervals}


def _normalized_pitch_classes(pitch_classes: list[int]) -> frozenset[int]:
    return frozenset(pc % 12 for pc in pitch_classes)

def _validate_chord_progression(score: CanonicalScore, primary_mode: str | None = None) -> list[str]:
    errors: list[str] = []
    if not score.chord_progression:
        return ["Score must include an explicit chord progression."]

    expected_measures = {m.number for m in score.measures}
    mapped_measures = {c.measure_number for c in score.chord_progression}
    missing = sorted(expected_measures - mapped_measures)
    if missing:
        errors.append(f"Missing chord symbols for measures: {missing}.")

    scale = parse_key(score.meta.key, primary_mode)
    valid_triads = {_normalized_pitch_classes(triad_pitch_classes(scale, degree)) for degree in range(1, 8)}
    for chord in score.chord_progression:
        chord_pcs = _normalized_pitch_classes(chord.pitch_classes)
        symbol_pcs = _chord_symbol_pitch_classes(chord.symbol)
        if chord_pcs in valid_triads:
            continue
        if symbol_pcs is not None and symbol_pcs in valid_triads:
            continue
        errors.append(f"Chord {chord.symbol} at measure {chord.measure_number} is not diatonic in {score.meta.key} ({primary_mode or 'default mode'}).")

    return errors


def _validate_lyric_mapping(score: CanonicalScore) -> list[str]:
    errors: list[str] = []
    expected_ids: dict[str, set[str]] = {s.id: {sy.id for sy in s.syllables} for s in score.sections}
    mapped_ids: dict[str, set[str]] = defaultdict(set)

    lyricless_indices: list[int] = []
    for note_idx, note in enumerate(_flatten_voice(score, "soprano")):
        if note.is_rest:
            continue

        if note.section_id not in expected_ids and note.section_id not in {"padding", "interlude"}:
            errors.append(f"Lyric note references unknown section_id {note.section_id}.")
            continue

        is_interlude = note.section_id == "interlude"
        if not is_interlude and note.lyric_syllable_id is None:
            lyricless_indices.append(note_idx)
            continue

        if note.lyric_syllable_id:
            if note.section_id in expected_ids and note.lyric_syllable_id not in expected_ids[note.section_id]:
                errors.append(f"Unknown syllable id {note.lyric_syllable_id} for section {note.section_id}.")
            mapped_ids[note.section_id].add(note.lyric_syllable_id)

        if note.lyric_mode in {"melisma_continue", "tie_continue"} and note.lyric_syllable_id is None:
            errors.append(f"Note {note_idx} has continuation mode without syllable id.")
        if note.lyric_mode in {"melisma_continue", "tie_continue"} and note.lyric:
            errors.append(f"Note {note_idx} repeats lyric text on a continuation mode.")

    if lyricless_indices:
        errors.append(f"Verse contains lyricless notes at indices {lyricless_indices} (outside interlude).")

    for section in score.sections:
        missing = expected_ids[section.id] - mapped_ids[section.id]
        if missing:
            errors.append(f"Section {section.id} has unmapped syllables: {sorted(missing)}")

    return errors


def _validate_phrase_barline_alignment(score: CanonicalScore) -> list[str]:
    errors: list[str] = []
    bpb = beats_per_measure(score.meta.time_signature)
    phrase_end_ids = {
        syllable.id
        for section in score.sections
        for syllable in section.syllables
        if syllable.phrase_end_after and syllable.must_end_at_barline
    }
    if not phrase_end_ids:
        return errors

    last_end_by_syllable: dict[str, float] = {}
    cursor = 0.0
    for note in _flatten_voice(score, "soprano"):
        end_cursor = cursor + note.beats
        if not note.is_rest and note.lyric_syllable_id in phrase_end_ids:
            last_end_by_syllable[note.lyric_syllable_id] = end_cursor
        cursor = end_cursor

    for syllable_id, end_pos in sorted(last_end_by_syllable.items(), key=lambda item: item[1]):
        if abs(end_pos % bpb) > 1e-6:
            errors.append(
                f"Lyric phrase ending at syllable {syllable_id} ends at beat {end_pos:g}, not on a barline."
            )

    return errors


def _validate_pickup_measure_capacities(score: CanonicalScore) -> list[str]:
    errors: list[str] = []
    beat_cap = beats_per_measure(score.meta.time_signature)
    section_pickups = {section.id: section.anacrusis_beats for section in score.sections if section.anacrusis_beats > 0}
    if not section_pickups:
        return errors

    voices: list[VoiceName] = ["soprano", "alto", "tenor", "bass"]
    for section_id, pickup_beats in section_pickups.items():
        expected_first_capacity = max(0.0, beat_cap - pickup_beats)
        for voice in voices:
            first_measure_number = None
            first_measure_nonpickup = 0.0
            subsequent_nonpickup_by_measure: dict[int, float] = {}
            for measure in score.measures:
                nonpickup = sum(
                    note.beats
                    for note in measure.voices.get(voice, [])
                    if note.section_id == section_id and not note.is_rest
                )
                if nonpickup <= 1e-9:
                    continue
                if first_measure_number is None:
                    first_measure_number = measure.number
                    first_measure_nonpickup = nonpickup
                    continue
                subsequent_nonpickup_by_measure[measure.number] = nonpickup

            if first_measure_number is None:
                continue

            if abs(first_measure_nonpickup - expected_first_capacity) > 1e-6:
                errors.append(
                    f"Section {section_id} voice {voice} first measure non-pickup beats {first_measure_nonpickup:g} != expected {expected_first_capacity:g}."
                )

            for measure_number, nonpickup in sorted(subsequent_nonpickup_by_measure.items()):
                if abs(nonpickup - beat_cap) > 1e-6:
                    errors.append(
                        f"Section {section_id} voice {voice} measure {measure_number} non-pickup beats {nonpickup:g} != expected full {beat_cap:g}."
                    )

    return errors


def _validate_ranges_and_motion(score: CanonicalScore) -> list[str]:
    errors: list[str] = []
    voice_names: list[VoiceName] = ["soprano", "alto", "tenor", "bass"]

    for voice in voice_names:
        lo, hi = VOICE_RANGES[voice]
        t_lo, t_hi = VOICE_TESSITURA[voice]
        prev = None
        for idx, note in enumerate(_flatten_voice(score, voice)):
            if note.is_rest:
                continue
            midi = pitch_to_midi(note.pitch)
            if midi < lo or midi > hi:
                errors.append(f"{voice} note {idx} out of range ({note.pitch}).")
            if prev is not None and abs(midi - prev) > MAX_MELODIC_LEAP:
                errors.append(f"{voice} note {idx} leap too large ({abs(midi-prev)} semitones).")
            if midi < t_lo - 1 or midi > t_hi + 1:
                errors.append(f"{voice} note {idx} in extreme tessitura ({note.pitch}).")
            prev = midi

    return errors


def _validate_harmonic_integrity(score: CanonicalScore) -> list[str]:
    errors: list[str] = []
    progression = {c.measure_number: set(c.pitch_classes) for c in score.chord_progression}
    bpb = beats_per_measure(score.meta.time_signature)

    for voice in ["soprano", "alto", "tenor", "bass"]:
        cursor = 0.0
        for idx, note in enumerate(_flatten_voice(score, voice)):
            if note.is_rest:
                cursor += note.beats
                continue
            measure_number = int(cursor // bpb) + 1
            chord_tones = progression.get(measure_number)
            if not chord_tones:
                cursor += note.beats
                continue
            pc = pitch_to_midi(note.pitch) % 12
            if voice == "soprano":
                if note.lyric_mode in {"tie_continue", "melisma_continue"}:
                    cursor += note.beats
                    continue
                if _is_strong_beat(cursor % bpb, score.meta.time_signature) and pc not in chord_tones:
                    errors.append(f"Soprano strong-beat note {idx} ({note.pitch}) conflicts with chord in measure {measure_number}.")
            elif score.meta.stage == "satb" and pc not in chord_tones:
                errors.append(f"{voice} note {idx} ({note.pitch}) is outside chord tones in measure {measure_number}.")
            cursor += note.beats

    return errors


def _validate_voice_separation(score: CanonicalScore) -> list[str]:
    errors: list[str] = []

    sop = [n for n in _flatten_voice(score, "soprano") if not n.is_rest]
    alto = [n for n in _flatten_voice(score, "alto") if not n.is_rest]
    tenor = [n for n in _flatten_voice(score, "tenor") if not n.is_rest]
    bass = [n for n in _flatten_voice(score, "bass") if not n.is_rest]

    if not (len(sop) == len(alto) == len(tenor) == len(bass)):
        errors.append("SATB voices are not rhythmically aligned by note count.")
        return errors

    for idx, (s, a, t, b) in enumerate(zip(sop, alto, tenor, bass)):
        s_m, a_m, t_m, b_m = map(lambda n: pitch_to_midi(n.pitch), (s, a, t, b))
        if not (s_m >= a_m >= t_m >= b_m):
            errors.append(f"Voice crossing at note {idx}: S/A/T/B not ordered.")
        if s_m - a_m > 12:
            errors.append(f"Wide spacing at note {idx}: soprano-alto exceeds octave.")
        if a_m - t_m > 12:
            errors.append(f"Wide spacing at note {idx}: alto-tenor exceeds octave.")
        if t_m - b_m > 16:
            errors.append(f"Wide spacing at note {idx}: tenor-bass exceeds 10th.")

    return errors


def _validate_parallel_intervals(score: CanonicalScore) -> list[str]:
    errors: list[str] = []
    voices = {
        "soprano": [n for n in _flatten_voice(score, "soprano") if not n.is_rest],
        "alto": [n for n in _flatten_voice(score, "alto") if not n.is_rest],
        "tenor": [n for n in _flatten_voice(score, "tenor") if not n.is_rest],
        "bass": [n for n in _flatten_voice(score, "bass") if not n.is_rest],
    }
    names = ["soprano", "alto", "tenor", "bass"]
    length = min(len(v) for v in voices.values()) if voices else 0

    for i in range(1, length):
        for a in range(len(names)):
            for b in range(a + 1, len(names)):
                x0 = pitch_to_midi(voices[names[a]][i - 1].pitch)
                y0 = pitch_to_midi(voices[names[b]][i - 1].pitch)
                x1 = pitch_to_midi(voices[names[a]][i].pitch)
                y1 = pitch_to_midi(voices[names[b]][i].pitch)
                int0 = abs(x0 - y0) % 12
                int1 = abs(x1 - y1) % 12
                same_dir = (x1 - x0 > 0 and y1 - y0 > 0) or (x1 - x0 < 0 and y1 - y0 < 0)
                if same_dir and int0 in {0, 7} and int1 == int0:
                    errors.append(
                        f"Potential parallel {'8ve' if int0 == 0 else '5th'} between {names[a]} and {names[b]} at note {i}."
                    )

    return errors

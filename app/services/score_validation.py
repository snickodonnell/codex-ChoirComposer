from __future__ import annotations

from collections import defaultdict

from app.models import CanonicalScore, VoiceName
from app.services.music_theory import VOICE_RANGES, VOICE_TESSITURA, parse_key, pitch_to_midi, triad_pitch_classes

MAX_MELODIC_LEAP = 7


def beats_per_measure(time_signature: str) -> float:
    top, bottom = time_signature.split("/")
    return int(top) * (4 / int(bottom))


def validate_score(score: CanonicalScore, primary_mode: str | None = None) -> list[str]:
    errors: list[str] = []
    target = beats_per_measure(score.meta.time_signature)

    for measure in score.measures:
        for voice, notes in measure.voices.items():
            total = sum(n.beats for n in notes)
            if abs(total - target) > 1e-6:
                errors.append(f"Measure {measure.number} voice {voice} has {total:g} beats; expected {target:g}.")

    errors.extend(_validate_chord_progression(score, primary_mode))
    errors.extend(_validate_lyric_mapping(score))
    errors.extend(_validate_ranges_and_motion(score))
    errors.extend(_validate_harmonic_integrity(score))

    if score.meta.stage == "satb":
        errors.extend(_validate_voice_separation(score))

    return errors


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
    valid_triads = {tuple(triad_pitch_classes(scale, degree)) for degree in range(1, 8)}
    for chord in score.chord_progression:
        if tuple(chord.pitch_classes) not in valid_triads:
            errors.append(f"Chord {chord.symbol} at measure {chord.measure_number} is not diatonic in {score.meta.key}.")

    return errors


def _validate_lyric_mapping(score: CanonicalScore) -> list[str]:
    errors: list[str] = []
    expected_ids: dict[str, set[str]] = {s.id: {sy.id for sy in s.syllables} for s in score.sections}
    mapped_ids: dict[str, set[str]] = defaultdict(set)

    for note_idx, note in enumerate(_flatten_voice(score, "soprano")):
        if note.is_rest:
            continue

        if note.section_id not in expected_ids and note.section_id != "padding":
            errors.append(f"Lyric note references unknown section_id {note.section_id}.")
            continue

        is_interlude = note.section_id == "interlude"
        if not is_interlude and note.lyric_syllable_id is None:
            errors.append(f"Orphan melodic note at index {note_idx} without lyric association.")
            continue

        if note.lyric_syllable_id:
            if note.section_id in expected_ids and note.lyric_syllable_id not in expected_ids[note.section_id]:
                errors.append(f"Unknown syllable id {note.lyric_syllable_id} for section {note.section_id}.")
            mapped_ids[note.section_id].add(note.lyric_syllable_id)

        if note.lyric_mode in {"melisma_continue", "tie_continue"} and note.lyric_syllable_id is None:
            errors.append(f"Note {note_idx} has continuation mode without syllable id.")

    for section in score.sections:
        missing = expected_ids[section.id] - mapped_ids[section.id]
        if missing:
            errors.append(f"Section {section.id} has unmapped syllables: {sorted(missing)}")

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

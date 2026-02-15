from __future__ import annotations

import logging
import math
import random

from app.models import (
    CanonicalScore,
    CompositionRequest,
    ScoreChord,
    ScoreMeasure,
    ScoreMeta,
    ScoreNote,
    ScoreSection,
)
from app.services.lyric_mapping import (
    section_archetype,
    config_for_preset,
    plan_syllable_rhythm,
    tokenize_section_lyrics,
)
from app.services.music_theory import (
    VOICE_RANGES,
    VOICE_TESSITURA,
    chord_symbol,
    choose_defaults,
    midi_to_pitch,
    nearest_in_range,
    parse_key,
    pitch_to_midi,
    triad_pitch_classes,
)
from app.services.score_validation import beats_per_measure, validate_score

MAX_MELODIC_LEAP = 7
MAX_GENERATION_ATTEMPTS = 5

logger = logging.getLogger(__name__)


def _append_pause_rests(notes: list[ScoreNote], pause_beats: float, beat_cap: float) -> None:
    remaining = max(0.0, pause_beats)
    while remaining > 1e-9:
        dur = min(remaining, beat_cap)
        notes.append(ScoreNote(pitch="REST", beats=dur, is_rest=True, section_id="interlude"))
        remaining -= dur


def _pack_measures(voice_notes: dict[str, list[ScoreNote]], time_signature: str) -> list[ScoreMeasure]:
    beat_cap = beats_per_measure(time_signature)
    cursors = {voice: 0 for voice in voice_notes}
    measures: list[ScoreMeasure] = []
    number = 1

    while any(cursors[v] < len(voice_notes[v]) for v in voice_notes):
        m_voices: dict[str, list[ScoreNote]] = {}
        for voice in voice_notes:
            used = 0.0
            m_voices[voice] = []
            while cursors[voice] < len(voice_notes[voice]) and used + voice_notes[voice][cursors[voice]].beats <= beat_cap + 1e-9:
                note = voice_notes[voice][cursors[voice]]
                m_voices[voice].append(note)
                used += note.beats
                cursors[voice] += 1

            if used < beat_cap:
                m_voices[voice].append(ScoreNote(pitch="REST", beats=beat_cap - used, is_rest=True, section_id="padding"))

        measures.append(ScoreMeasure(number=number, voices=m_voices))
        number += 1

    return measures


def _constrain_melodic_candidate(candidate: int, previous: int, voice: str, scale_semitones: set[int]) -> int:
    lo, hi = VOICE_RANGES[voice]
    t_lo, t_hi = VOICE_TESSITURA[voice]
    candidate = nearest_in_range(candidate, lo, hi)
    while abs(candidate - previous) > MAX_MELODIC_LEAP:
        candidate += -1 if candidate > previous else 1

    if candidate < t_lo:
        candidate += 1
    elif candidate > t_hi:
        candidate -= 1

    if candidate % 12 not in scale_semitones:
        up = candidate
        down = candidate
        while up % 12 not in scale_semitones and up <= hi:
            up += 1
        while down % 12 not in scale_semitones and down >= lo:
            down -= 1
        if lo <= down <= hi and abs(down - previous) <= abs(up - previous):
            candidate = down
        elif lo <= up <= hi:
            candidate = up

    return nearest_in_range(candidate, lo, hi)


def _flatten_voice(score: CanonicalScore, voice: str) -> list[ScoreNote]:
    return [n for m in score.measures for n in m.voices[voice]]


def _creates_parallel(prev_s: int, curr_s: int, prev_v: int, curr_v: int) -> bool:
    int0 = abs(prev_s - prev_v) % 12
    int1 = abs(curr_s - curr_v) % 12
    same_dir = (curr_s - prev_s > 0 and curr_v - prev_v > 0) or (curr_s - prev_s < 0 and curr_v - prev_v < 0)
    return same_dir and int0 in {0, 7} and int1 == int0


def _break_parallel_with_soprano(curr_s: int, prev_s: int, prev_v: int, candidate: int, voice: str, scale_set: set[int]) -> int:
    if not _creates_parallel(prev_s, curr_s, prev_v, candidate):
        return candidate
    for delta in (-2, -1, 1, 2, -3, 3):
        cand = _constrain_melodic_candidate(candidate + delta, prev_v, voice, scale_set)
        if not _creates_parallel(prev_s, curr_s, prev_v, cand):
            return cand
    return candidate


def _choose_chord_tone(
    voice: str,
    previous: int,
    target: int,
    chord_tones: set[int],
    *,
    lower_bound: int | None = None,
    upper_bound: int | None = None,
) -> int:
    base_lo, base_hi = VOICE_RANGES[voice]
    lo, hi = base_lo, base_hi
    if lower_bound is not None:
        lo = max(lo, lower_bound)
    if upper_bound is not None:
        hi = min(hi, upper_bound)

    t_lo, t_hi = VOICE_TESSITURA[voice]
    if lo > hi:
        lo = max(base_lo, t_lo - 1)
        hi = min(base_hi, t_hi + 1)
        if lo > hi:
            lo, hi = base_lo, base_hi

    candidates = [m for m in range(lo, hi + 1) if m % 12 in chord_tones]
    if not candidates:
        return nearest_in_range(target, lo, hi)

    def pick(pool: list[int]) -> int:
        return min(pool, key=lambda midi: (abs(midi - target), abs(midi - previous)))

    pools = [
        [m for m in candidates if abs(m - previous) <= MAX_MELODIC_LEAP and t_lo - 1 <= m <= t_hi + 1],
        [m for m in candidates if abs(m - previous) <= MAX_MELODIC_LEAP],
        [m for m in candidates if t_lo - 1 <= m <= t_hi + 1],
        candidates,
    ]
    for pool in pools:
        if pool:
            return pick(pool)

    return pick(candidates)


def _is_strong_beat(position: float, time_signature: str) -> bool:
    top, bottom = [int(p) for p in time_signature.split("/")]
    quarter_position = position * (bottom / 4)
    if top == 4:
        return abs(quarter_position % 2) < 1e-9
    if top == 6 and bottom == 8:
        return abs(position) < 1e-9 or abs(position - 1.5) < 1e-9
    return abs(quarter_position % 1) < 1e-9


def _nearest_pitch_class(target: int, pitch_classes: set[int], lo: int, hi: int) -> int:
    candidates = [m for m in range(lo, hi + 1) if m % 12 in pitch_classes]
    return min(candidates, key=lambda m: (abs(m - target), m)) if candidates else nearest_in_range(target, lo, hi)


def _nearest_pitch_class_with_leap(target: int, previous: int, pitch_classes: set[int], voice: str) -> int:
    lo, hi = VOICE_RANGES[voice]
    candidates = [m for m in range(lo, hi + 1) if m % 12 in pitch_classes and abs(m - previous) <= MAX_MELODIC_LEAP]
    if not candidates:
        return _nearest_pitch_class(target, pitch_classes, lo, hi)
    return min(candidates, key=lambda m: (abs(m - target), abs(m - previous), m))


def _cluster_progression_cycle(_scale, cluster_label: str) -> list[int]:
    templates = {
        "verse": [1, 4, 5, 6],
        "chorus": [1, 5, 6, 4],
        "bridge": [6, 4, 1, 5],
        "pre-chorus": [2, 4, 5, 1],
        "intro": [1, 5, 6, 4],
        "outro": [1, 4, 1, 5],
        "custom": [1, 6, 4, 5],
    }
    archetype = section_archetype(cluster_label)
    return templates.get(archetype, templates["custom"])


def _build_section_progression(scale, section_id: str, start_measure: int, measure_count: int, cluster_cycle: list[int]) -> list[ScoreChord]:
    progression: list[ScoreChord] = []

    for i in range(measure_count):
        degree = cluster_cycle[i % len(cluster_cycle)]

        progression.append(
            ScoreChord(
                measure_number=start_measure + i,
                section_id=section_id,
                degree=degree,
                symbol=chord_symbol(scale, degree),
                pitch_classes=triad_pitch_classes(scale, degree),
            )
        )

    return progression






def _repair_harmony_progression(chords: list[ScoreChord], measure_count: int, key: str, primary_mode: str | None) -> list[ScoreChord]:
    scale = parse_key(key, primary_mode)
    repaired: list[ScoreChord] = []

    for chord in sorted(chords, key=lambda c: c.measure_number):
        degree = chord.degree if 1 <= chord.degree <= 7 else 1
        repaired.append(
            ScoreChord(
                measure_number=chord.measure_number,
                section_id=chord.section_id,
                degree=degree,
                symbol=chord_symbol(scale, degree),
                pitch_classes=triad_pitch_classes(scale, degree),
            )
        )

    mapped_measures = {ch.measure_number for ch in repaired}
    fallback_section_id = repaired[0].section_id if repaired else "padding"
    for measure_number in range(1, measure_count + 1):
        if measure_number in mapped_measures:
            continue
        repaired.append(
            ScoreChord(
                measure_number=measure_number,
                section_id=fallback_section_id,
                degree=1,
                symbol=chord_symbol(scale, 1),
                pitch_classes=triad_pitch_classes(scale, 1),
            )
        )

    return sorted(repaired, key=lambda c: c.measure_number)

def _expand_arrangement(req: CompositionRequest) -> list[tuple[str, str, str, float]]:
    section_defs = {}
    for idx, section in enumerate(req.sections, start=1):
        section_key = section.id or f"section-{idx}"
        section_defs[section_key] = section

    if not req.arrangement:
        return [
            (
                (section.id or f"section-{idx}"),
                section.label,
                section.progression_cluster or section.label,
                section.pause_beats,
            )
            for idx, section in enumerate(req.sections, start=1)
        ]

    expanded: list[tuple[str, str, str, float]] = []
    for item in req.arrangement:
        section = section_defs.get(item.section_id)
        if section is None:
            raise ValueError(f"Arrangement references unknown section_id: {item.section_id}")
        expanded.append((item.section_id, section.label, section.progression_cluster or section.label, item.pause_beats))

    return expanded

def _repair_missing_chords(score: CanonicalScore, primary_mode: str | None) -> None:
    if not score.measures:
        return
    scale = parse_key(score.meta.key, primary_mode)
    chord_by_measure = {ch.measure_number: ch for ch in score.chord_progression}
    first_section_by_measure: dict[int, str] = {}
    for measure in score.measures:
        first_note = next((n for n in measure.voices["soprano"] if not n.is_rest), None)
        first_section_by_measure[measure.number] = first_note.section_id if first_note else "padding"

    for measure in score.measures:
        if measure.number in chord_by_measure:
            continue
        section_id = first_section_by_measure.get(measure.number, "padding")
        score.chord_progression.append(
            ScoreChord(
                measure_number=measure.number,
                section_id=section_id,
                degree=1,
                symbol=chord_symbol(scale, 1),
                pitch_classes=triad_pitch_classes(scale, 1),
            )
        )


def _repair_key_mode_mismatch(score: CanonicalScore, primary_mode: str | None) -> None:
    scale = parse_key(score.meta.key, primary_mode)
    for chord in score.chord_progression:
        degree = chord.degree if 1 <= chord.degree <= 7 else 1
        chord.degree = degree
        chord.pitch_classes = triad_pitch_classes(scale, degree)
        chord.symbol = chord_symbol(scale, degree)


def _repair_soprano_strong_beats(score: CanonicalScore, primary_mode: str | None) -> None:
    scale_set = set(parse_key(score.meta.key, primary_mode).semitones)
    chord_by_measure = {ch.measure_number: ch for ch in score.chord_progression}
    bpb = beats_per_measure(score.meta.time_signature)
    prev = None
    cursor = 0.0

    for note in _flatten_voice(score, "soprano"):
        if note.is_rest:
            cursor += note.beats
            continue
        midi = pitch_to_midi(note.pitch)
        basis = midi if prev is None else prev
        if note.lyric_mode == "tie_continue":
            midi = basis
        elif note.lyric_mode != "melisma_continue" and _is_strong_beat(cursor % bpb, score.meta.time_signature):
            chord = chord_by_measure.get(int(cursor // bpb) + 1)
            if chord and midi % 12 not in set(chord.pitch_classes):
                midi = _nearest_pitch_class_with_leap(midi, basis, set(chord.pitch_classes), "soprano")
                midi = _constrain_melodic_candidate(midi, basis, "soprano", scale_set)
                midi = _nearest_pitch_class_with_leap(midi, basis, set(chord.pitch_classes), "soprano")
        note.pitch = midi_to_pitch(midi)
        prev = midi
        cursor += note.beats


def _auto_repair_melody_score(score: CanonicalScore, primary_mode: str | None) -> CanonicalScore:
    _repair_missing_chords(score, primary_mode)
    _repair_key_mode_mismatch(score, primary_mode)
    _repair_soprano_strong_beats(score, primary_mode)
    score.chord_progression.sort(key=lambda chord: chord.measure_number)
    return score


def _compose_melody_once(req: CompositionRequest, attempt_number: int) -> CanonicalScore:
    key, ts, tempo = choose_defaults(req.preferences.style, req.preferences.mood)
    if req.preferences.key:
        key = req.preferences.key
    if req.preferences.time_signature:
        ts = req.preferences.time_signature
    if req.preferences.tempo_bpm:
        tempo = req.preferences.tempo_bpm

    scale = parse_key(key, req.preferences.primary_mode)
    scale_set = set(scale.semitones)
    base_seed = f"{key}-{ts}-{tempo}-{req.preferences.style}"
    random.seed(base_seed if attempt_number == 0 else f"{base_seed}-attempt-{attempt_number}")

    sections: list[ScoreSection] = []
    section_plans: list[tuple[str, str, str, list[dict], float]] = []
    beat_cap = beats_per_measure(ts)

    section_defs = {section.id or f"section-{idx}": section for idx, section in enumerate(req.sections, start=1)}
    arranged_instances = _expand_arrangement(req)

    for idx, (arranged_section_id, section_label, progression_cluster, arranged_pause_beats) in enumerate(arranged_instances, start=1):
        section = section_defs[arranged_section_id]
        section_id = f"sec-{idx}"
        syllables = tokenize_section_lyrics(section_id, section.text)
        sections.append(
            ScoreSection(
                id=section_id,
                label=section_label,
                pause_beats=arranged_pause_beats,
                lyrics=section.text,
                syllables=syllables,
            )
        )

        rhythm_config = config_for_preset(req.preferences.lyric_rhythm_preset, section_label)
        rhythm_seed = (
            f"{key}|{ts}|{tempo}|{req.preferences.style}|{section_label}|"
            f"{section_archetype(section_label)}|{section_id}|{req.preferences.lyric_rhythm_preset}"
        )
        if attempt_number > 0:
            rhythm_seed = f"{rhythm_seed}|attempt-{attempt_number}"
        rhythm_plan = plan_syllable_rhythm(syllables, beat_cap, rhythm_config, rhythm_seed)
        pause_after = arranged_pause_beats if idx < len(arranged_instances) else 0
        section_plans.append((section_id, section_label, progression_cluster, rhythm_plan, pause_after))

    chord_progression: list[ScoreChord] = []
    cluster_cycles: dict[str, list[int]] = {}
    beat_cursor = 0.0
    for section_id, _label, progression_cluster, rhythm_plan, pause_after in section_plans:
        total_beats = sum(sum(item["durations"]) for item in rhythm_plan) + pause_after
        start_measure = int(beat_cursor // beat_cap) + 1
        end_measure = int(max(beat_cursor + total_beats - 1e-9, beat_cursor) // beat_cap) + 1
        section_measures = max(1, end_measure - start_measure + 1)
        cluster_cycle = cluster_cycles.setdefault(progression_cluster, _cluster_progression_cycle(scale, progression_cluster))
        chord_progression.extend(_build_section_progression(scale, section_id, start_measure, section_measures, cluster_cycle))
        beat_cursor += total_beats

    total_measures = max(1, int(max(beat_cursor - 1e-9, 0.0) // beat_cap) + 1)
    chord_progression = _repair_harmony_progression(chord_progression, total_measures, key, req.preferences.primary_mode)
    chord_by_measure = {ch.measure_number: ch for ch in chord_progression}

    soprano_notes: list[ScoreNote] = []
    cursor = 0.0

    for section_id, label, _progression_cluster, rhythm_plan, pause_after in section_plans:
        center = 64 if label in {"verse", "bridge"} else 67
        previous_sung = next((n for n in reversed(soprano_notes) if not n.is_rest), None)
        prev = center if previous_sung is None else pitch_to_midi(previous_sung.pitch)

        for item in rhythm_plan:
            step_base = random.choice([-2, -1, 0, 1, 2, 3])
            stressed_bonus = 1 if item["stressed"] else 0
            for ni, duration in enumerate(item["durations"]):
                measure_number = int(cursor // beat_cap) + 1
                measure_beat = cursor % beat_cap
                chord = chord_by_measure.get(measure_number)
                chord_tones = set(chord.pitch_classes if chord else scale.semitones)

                step = step_base + (1 if (item["stressed"] and ni == 0 and step_base < 2) else 0) - stressed_bonus
                mode = item["modes"][ni]
                candidate = _constrain_melodic_candidate(prev + step, prev, "soprano", scale_set)

                if mode == "tie_continue":
                    candidate = prev
                elif mode == "melisma_continue":
                    candidate = _constrain_melodic_candidate(prev + random.choice([-1, 0, 1]), prev, "soprano", scale_set)
                elif _is_strong_beat(measure_beat, ts):
                    candidate = _nearest_pitch_class_with_leap(candidate, prev, chord_tones, "soprano")
                    candidate = _constrain_melodic_candidate(candidate, prev, "soprano", scale_set)
                    candidate = _nearest_pitch_class_with_leap(candidate, prev, chord_tones, "soprano")

                soprano_notes.append(
                    ScoreNote(
                        pitch=midi_to_pitch(candidate),
                        beats=duration,
                        lyric=item["syllable_text"],
                        lyric_syllable_id=item["syllable_id"],
                        lyric_mode=mode,
                        section_id=item["section_id"],
                        lyric_index=item["lyric_index"],
                    )
                )
                prev = candidate
                cursor += duration

        if pause_after > 0:
            _append_pause_rests(soprano_notes, pause_after, beat_cap)
            cursor += pause_after

    measures = _pack_measures({"soprano": soprano_notes, "alto": [], "tenor": [], "bass": []}, ts)
    score = CanonicalScore(
        meta=ScoreMeta(
            key=key,
            primary_mode=req.preferences.primary_mode,
            time_signature=ts,
            tempo_bpm=tempo,
            style=req.preferences.style,
            stage="melody",
            rationale="Deterministic lyric-to-rhythm mapping with section-wise diatonic chord progression as harmonic authority.",
        ),
        sections=sections,
        measures=measures,
        chord_progression=chord_progression,
    )
    return score


def generate_melody_score(req: CompositionRequest) -> CanonicalScore:
    primary_mode = req.preferences.primary_mode
    error_history: list[str] = []

    for attempt_idx in range(MAX_GENERATION_ATTEMPTS):
        attempt = attempt_idx + 1
        score = _compose_melody_once(req, attempt_idx)
        harmony_issues = [
            err
            for err in validate_score(score, primary_mode)
            if err.startswith("Score must include an explicit chord progression")
            or err.startswith("Missing chord symbols")
            or err.startswith("Chord ")
        ]
        if harmony_issues:
            logger.warning("Harmony validation failed on attempt %s before melody repair: %s", attempt, harmony_issues)
            score.chord_progression = _repair_harmony_progression(
                score.chord_progression,
                len(score.measures),
                score.meta.key,
                primary_mode,
            )

        errs = validate_score(score, primary_mode)
        if not errs:
            return score

        logger.warning("Melody validation failed on attempt %s before repair: %s", attempt, errs)
        repaired = _auto_repair_melody_score(score, primary_mode)
        repaired_errs = validate_score(repaired, primary_mode)
        if not repaired_errs:
            logger.info("Melody auto-repair succeeded on attempt %s.", attempt)
            return repaired

        logger.warning("Melody validation failed on attempt %s after repair: %s", attempt, repaired_errs)
        error_history.append(f"attempt {attempt}: {'; '.join(repaired_errs)}")

    logger.error("Melody generation exhausted retries. Validation errors: %s", error_history)
    raise ValueError(
        "Couldn’t generate a valid melody with the current constraints—try relaxing key/mode/time/tempo or click Regenerate"
    )


def refine_score(score: CanonicalScore, instruction: str, regenerate: bool) -> CanonicalScore:
    random.seed(instruction)
    scale_set = set(parse_key(score.meta.key, score.meta.primary_mode).semitones)
    progression = {c.measure_number: c for c in score.chord_progression}
    bpb = beats_per_measure(score.meta.time_signature)
    prev = None
    cursor = 0.0

    for note in _flatten_voice(score, "soprano"):
        if note.is_rest:
            cursor += note.beats
            continue
        midi = pitch_to_midi(note.pitch)
        basis = midi if prev is None else prev
        if regenerate:
            midi += random.choice([-3, -2, -1, 1, 2, 3])
        elif "higher" in instruction.lower() and note.lyric_mode in {"single", "melisma_start"}:
            midi += 2
        elif "lower" in instruction.lower() and note.lyric_mode in {"single", "melisma_start"}:
            midi -= 2
        midi = _constrain_melodic_candidate(midi, basis, "soprano", scale_set)
        if note.lyric_mode == "tie_continue":
            midi = basis
        elif _is_strong_beat(cursor % bpb, score.meta.time_signature):
            chord = progression.get(int(cursor // bpb) + 1)
            if chord:
                midi = _nearest_pitch_class_with_leap(midi, basis, set(chord.pitch_classes), "soprano")
                midi = _constrain_melodic_candidate(midi, basis, "soprano", scale_set)
                midi = _nearest_pitch_class_with_leap(midi, basis, set(chord.pitch_classes), "soprano")
        note.pitch = midi_to_pitch(midi)
        prev = midi
        cursor += note.beats

    score.meta.rationale = f"Refined while preserving progression authority: {instruction}"
    errs = validate_score(score)
    if errs:
        raise ValueError(f"Refined score failed validation: {'; '.join(errs)}")
    return score


def harmonize_score(score: CanonicalScore) -> CanonicalScore:
    if not score.chord_progression:
        raise ValueError("Cannot harmonize without chord progression.")

    scale_set = set(parse_key(score.meta.key, score.meta.primary_mode).semitones)
    chord_by_measure = {c.measure_number: c for c in score.chord_progression}
    bpb = beats_per_measure(score.meta.time_signature)

    soprano = _flatten_voice(score, "soprano")
    alto: list[ScoreNote] = []
    tenor: list[ScoreNote] = []
    bass: list[ScoreNote] = []

    prev_a, prev_t, prev_b = 62, 55, 48
    prev_s = 64
    cursor = 0.0

    for s in soprano:
        measure_number = int(cursor // bpb) + 1
        chord = chord_by_measure.get(measure_number)
        chord_tones = set(chord.pitch_classes if chord else parse_key(score.meta.key, score.meta.primary_mode).semitones)

        if s.is_rest:
            for voice in (alto, tenor, bass):
                voice.append(ScoreNote(pitch="REST", beats=s.beats, is_rest=True, section_id=s.section_id))
            cursor += s.beats
            continue

        sm = pitch_to_midi(s.pitch)
        bass_tones = chord_tones

        am = _choose_chord_tone("alto", prev_a, min(sm - 3, prev_a), chord_tones, upper_bound=sm - 1)
        tm = _choose_chord_tone("tenor", prev_t, min(sm - 7, prev_t), chord_tones, upper_bound=am - 1)
        bm = _choose_chord_tone("bass", prev_b, prev_b, bass_tones, lower_bound=VOICE_TESSITURA["bass"][0] - 1, upper_bound=tm - 1)

        if sm - am > 12:
            am = _choose_chord_tone("alto", prev_a, sm - 12, chord_tones, lower_bound=sm - 12, upper_bound=sm - 1)
        if am - tm > 12:
            tm = _choose_chord_tone("tenor", prev_t, am - 12, chord_tones, lower_bound=am - 12, upper_bound=am - 1)
        if tm - bm > 16:
            bm = _choose_chord_tone("bass", prev_b, tm - 12, bass_tones, lower_bound=max(tm - 16, VOICE_TESSITURA["bass"][0] - 1), upper_bound=tm - 1)

        am = _break_parallel_with_soprano(sm, prev_s, prev_a, am, "alto", scale_set)
        am = _choose_chord_tone("alto", prev_a, am, chord_tones, upper_bound=sm - 1)
        tm = _break_parallel_with_soprano(sm, prev_s, prev_t, tm, "tenor", scale_set)
        tm = _choose_chord_tone("tenor", prev_t, tm, chord_tones, upper_bound=am - 1)
        bm = _break_parallel_with_soprano(sm, prev_s, prev_b, bm, "bass", scale_set)
        bm = _choose_chord_tone("bass", prev_b, bm, bass_tones, lower_bound=VOICE_TESSITURA["bass"][0] - 1, upper_bound=tm - 1)

        if sm - am > 12:
            am = _choose_chord_tone("alto", prev_a, sm - 12, chord_tones, lower_bound=sm - 12, upper_bound=sm - 1)
        if am - tm > 12:
            tm = _choose_chord_tone("tenor", prev_t, am - 12, chord_tones, lower_bound=am - 12, upper_bound=am - 1)
        if tm - bm > 16:
            bm = _choose_chord_tone("bass", prev_b, tm - 12, bass_tones, lower_bound=max(tm - 16, VOICE_TESSITURA["bass"][0] - 1), upper_bound=tm - 1)

        tenor_floor = VOICE_TESSITURA["tenor"][0] - 1
        if tm < tenor_floor:
            tm = _nearest_pitch_class(tm + 12, chord_tones, *VOICE_RANGES["tenor"])

        alto.append(ScoreNote(pitch=midi_to_pitch(am), beats=s.beats, section_id=s.section_id, lyric=s.lyric, lyric_syllable_id=s.lyric_syllable_id, lyric_mode=s.lyric_mode, lyric_index=s.lyric_index))
        tenor.append(ScoreNote(pitch=midi_to_pitch(tm), beats=s.beats, section_id=s.section_id, lyric=s.lyric, lyric_syllable_id=s.lyric_syllable_id, lyric_mode=s.lyric_mode, lyric_index=s.lyric_index))
        bass.append(ScoreNote(pitch=midi_to_pitch(bm), beats=s.beats, section_id=s.section_id, lyric=s.lyric, lyric_syllable_id=s.lyric_syllable_id, lyric_mode=s.lyric_mode, lyric_index=s.lyric_index))

        prev_a, prev_t, prev_b = am, tm, bm
        prev_s = sm
        cursor += s.beats

    satb = CanonicalScore(
        meta=score.meta.model_copy(update={"stage": "satb", "rationale": "SATB voiced directly from explicit section chord progression."}),
        sections=score.sections,
        measures=_pack_measures({"soprano": [n.model_copy() for n in soprano], "alto": alto, "tenor": tenor, "bass": bass}, score.meta.time_signature),
        chord_progression=score.chord_progression,
    )
    errs = validate_score(satb)
    if errs:
        raise ValueError(f"SATB score failed validation: {'; '.join(errs)}")
    return satb

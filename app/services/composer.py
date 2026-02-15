from __future__ import annotations

import random

from app.models import (
    CanonicalScore,
    CompositionRequest,
    ScoreMeasure,
    ScoreMeta,
    ScoreNote,
    ScoreSection,
)
from app.services.lyric_mapping import (
    config_for_preset,
    plan_syllable_rhythm,
    tokenize_section_lyrics,
)
from app.services.music_theory import (
    VOICE_RANGES,
    VOICE_TESSITURA,
    choose_defaults,
    midi_to_pitch,
    nearest_in_range,
    parse_key,
    pitch_to_midi,
)
from app.services.score_validation import beats_per_measure, validate_score

MAX_MELODIC_LEAP = 7


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


def generate_melody_score(req: CompositionRequest) -> CanonicalScore:
    key, ts, tempo = choose_defaults(req.preferences.style, req.preferences.mood)
    if req.preferences.key:
        key = req.preferences.key
    if req.preferences.time_signature:
        ts = req.preferences.time_signature
    if req.preferences.tempo_bpm:
        tempo = req.preferences.tempo_bpm

    scale = parse_key(key)
    scale_set = set(scale.semitones)
    random.seed(f"{key}-{ts}-{tempo}-{req.preferences.style}")

    sections: list[ScoreSection] = []
    soprano_notes: list[ScoreNote] = []
    beat_cap = beats_per_measure(ts)

    for idx, section in enumerate(req.sections, start=1):
        section_id = f"sec-{idx}"
        syllables = tokenize_section_lyrics(section_id, section.text)
        sections.append(ScoreSection(id=section_id, label=section.label, title=section.title, lyrics=section.text, syllables=syllables))

        rhythm_config = config_for_preset(req.preferences.lyric_rhythm_preset, section.label)
        rhythm_seed = f"{key}|{ts}|{tempo}|{req.preferences.style}|{section.label}|{section_id}|{req.preferences.lyric_rhythm_preset}"
        rhythm_plan = plan_syllable_rhythm(syllables, beat_cap, rhythm_config, rhythm_seed)
        center = 64 if section.label in {"verse", "bridge"} else 67
        prev = center

        for item in rhythm_plan:
            step_base = random.choice([-2, -1, 0, 1, 2, 3])
            stressed_bonus = 1 if item["stressed"] else 0
            note_count = len(item["durations"])
            for ni in range(note_count):
                step = step_base + (1 if (item["stressed"] and ni == 0 and step_base < 2) else 0) - stressed_bonus
                candidate = _constrain_melodic_candidate(prev + step, prev, "soprano", scale_set)

                mode = item["modes"][ni]
                if mode == "tie_continue":
                    candidate = prev
                elif mode == "melisma_continue":
                    candidate = _constrain_melodic_candidate(prev + random.choice([-1, 0, 1]), prev, "soprano", scale_set)

                soprano_notes.append(
                    ScoreNote(
                        pitch=midi_to_pitch(candidate),
                        beats=item["durations"][ni],
                        lyric=item["syllable_text"],
                        lyric_syllable_id=item["syllable_id"],
                        lyric_mode=mode,
                        section_id=item["section_id"],
                        lyric_index=item["lyric_index"],
                    )
                )
                prev = candidate

    measures = _pack_measures({"soprano": soprano_notes, "alto": [], "tenor": [], "bass": []}, ts)
    score = CanonicalScore(
        meta=ScoreMeta(
            key=key,
            time_signature=ts,
            tempo_bpm=tempo,
            style=req.preferences.style,
            stage="melody",
            rationale="Deterministic lyric-to-rhythm mapping aligns syllables, prosody, and phrase endings before SATB.",
        ),
        sections=sections,
        measures=measures,
    )
    errs = validate_score(score)
    if errs:
        raise ValueError(f"Generated melody score failed validation: {'; '.join(errs)}")
    return score


def refine_score(score: CanonicalScore, instruction: str, regenerate: bool) -> CanonicalScore:
    random.seed(instruction)
    scale_set = set(parse_key(score.meta.key).semitones)
    prev = None
    for note in _flatten_voice(score, "soprano"):
        if note.is_rest:
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
        note.pitch = midi_to_pitch(midi)
        prev = midi

    score.meta.rationale = f"Refined based on instruction: {instruction}"
    errs = validate_score(score)
    if errs:
        raise ValueError(f"Refined score failed validation: {'; '.join(errs)}")
    return score


def harmonize_score(score: CanonicalScore) -> CanonicalScore:
    scale_set = set(parse_key(score.meta.key).semitones)
    soprano = _flatten_voice(score, "soprano")
    alto: list[ScoreNote] = []
    tenor: list[ScoreNote] = []
    bass: list[ScoreNote] = []

    prev_a, prev_t, prev_b = 62, 55, 48
    prev_s = 64

    for s in soprano:
        if s.is_rest:
            for voice in (alto, tenor, bass):
                voice.append(ScoreNote(pitch="REST", beats=s.beats, is_rest=True, section_id=s.section_id))
            continue

        sm = pitch_to_midi(s.pitch)
        am = _constrain_melodic_candidate(min(sm - 3, sm), prev_a, "alto", scale_set)
        tm = _constrain_melodic_candidate(min(am - 3, sm - 7), prev_t, "tenor", scale_set)
        bm = _constrain_melodic_candidate(min(tm - 4, sm - 12), prev_b, "bass", scale_set)

        if sm - am > 12:
            am = sm - 12
        if am - tm > 12:
            tm = am - 12

        am = nearest_in_range(am, *VOICE_RANGES["alto"])
        tm = nearest_in_range(tm, *VOICE_RANGES["tenor"])
        bm = nearest_in_range(bm, *VOICE_RANGES["bass"])

        am = _break_parallel_with_soprano(sm, prev_s, prev_a, am, "alto", scale_set)
        tm = _break_parallel_with_soprano(sm, prev_s, prev_t, tm, "tenor", scale_set)
        bm = _break_parallel_with_soprano(sm, prev_s, prev_b, bm, "bass", scale_set)

        alto.append(ScoreNote(pitch=midi_to_pitch(am), beats=s.beats, section_id=s.section_id, lyric=s.lyric, lyric_syllable_id=s.lyric_syllable_id, lyric_mode=s.lyric_mode, lyric_index=s.lyric_index))
        tenor.append(ScoreNote(pitch=midi_to_pitch(tm), beats=s.beats, section_id=s.section_id, lyric=s.lyric, lyric_syllable_id=s.lyric_syllable_id, lyric_mode=s.lyric_mode, lyric_index=s.lyric_index))
        bass.append(ScoreNote(pitch=midi_to_pitch(bm), beats=s.beats, section_id=s.section_id, lyric=s.lyric, lyric_syllable_id=s.lyric_syllable_id, lyric_mode=s.lyric_mode, lyric_index=s.lyric_index))

        prev_a, prev_t, prev_b = am, tm, bm
        prev_s = sm

    satb = CanonicalScore(
        meta=score.meta.model_copy(update={"stage": "satb", "rationale": "SATB with deterministic prosody-aware lyric rhythm alignment and singability constraints."}),
        sections=score.sections,
        measures=_pack_measures({"soprano": [n.model_copy() for n in soprano], "alto": alto, "tenor": tenor, "bass": bass}, score.meta.time_signature),
    )
    errs = validate_score(satb)
    if errs:
        raise ValueError(f"SATB score failed validation: {'; '.join(errs)}")
    return satb

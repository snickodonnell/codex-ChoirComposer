from __future__ import annotations

import logging
import math
import random
import hashlib
import re
from dataclasses import dataclass

from app.logging_utils import log_event

from app.models import (
    ArrangementMusicUnit,
    CanonicalScore,
    CompositionRequest,
    PhraseBlock,
    ScoreChord,
    ScoreMeasure,
    ScoreMeta,
    ScoreNote,
    ScoreSection,
    ScoreSyllable,
    VerseMusicUnitForm,
)
from app.services.lyric_mapping import (
    section_archetype,
    config_for_preset,
    plan_syllable_rhythm,
    tokenize_phrase_blocks,
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
from app.services.score_normalization import normalize_score_for_rendering
from app.services.score_validation import beats_per_measure, validate_score

MAX_MELODIC_LEAP = 7
MAX_GENERATION_ATTEMPTS = 5
MAX_IDENTICAL_CONSECUTIVE_PITCHES = 3

logger = logging.getLogger(__name__)


class VerseFormConstraintError(ValueError):
    pass


@dataclass
class PlannedVerseForm:
    pickup_beats: float
    bars_per_verse: int
    target_measure_count: int
    phrase_end_syllable_indices: list[int]
    phrase_bar_targets: list[int]
    rhythmic_skeleton: list[list[float]]

    def to_music_unit_form(self, music_unit_id: str) -> VerseMusicUnitForm:
        return VerseMusicUnitForm(
            music_unit_id=music_unit_id,
            pickup_beats=self.pickup_beats,
            bars_per_verse=self.bars_per_verse,
            total_measure_count=self.target_measure_count,
            phrase_end_syllable_indices=list(self.phrase_end_syllable_indices),
            phrase_bar_targets=list(self.phrase_bar_targets),
            rhythmic_skeleton=[list(slot) for slot in self.rhythmic_skeleton],
        )


class VerseFormPlanner:
    def __init__(self, *, time_signature: str, bars_per_verse_target: int | None):
        self._time_signature = time_signature
        self._bars_per_verse_target = bars_per_verse_target
        self._beat_cap = beats_per_measure(time_signature)

    def resolve_pickup_beats(
        self,
        *,
        mode: str,
        configured_beats: float,
        syllable_count: int,
        seed: str,
    ) -> float:
        return _resolve_anacrusis_beats(mode, configured_beats, syllable_count, self._beat_cap, seed=seed)

    def phrase_end_indices(self, syllables: list[ScoreSyllable], phrase_blocks: list[PhraseBlock]) -> list[int]:
        return _phrase_end_indices_from_phrase_blocks(syllables, phrase_blocks)

    def allocate_phrase_bar_targets(
        self,
        *,
        syllables: list[ScoreSyllable],
        phrase_end_syllable_indices: list[int],
    ) -> list[int]:
        if not phrase_end_syllable_indices:
            return []

        bars_target = self._bars_per_verse_target if self._bars_per_verse_target is not None else max(1, len(phrase_end_syllable_indices))
        phrase_count = len(phrase_end_syllable_indices)
        if bars_target < phrase_count:
            raise VerseFormConstraintError(
                f"Bars per Verse ({bars_target}) is too short for this verse. It needs at least {phrase_count} bars so each phrase can end at a barline."
            )

        phrase_lengths: list[int] = []
        start = 0
        for end in phrase_end_syllable_indices:
            phrase_lengths.append(max(1, end - start + 1))
            start = end + 1

        remaining = bars_target - phrase_count
        weighted = [remaining * length / max(1, sum(phrase_lengths)) for length in phrase_lengths]
        per_phrase = [1 + int(math.floor(value)) for value in weighted]
        assigned = sum(per_phrase)
        if assigned < bars_target:
            remainders = sorted(
                range(phrase_count),
                key=lambda idx: (weighted[idx] - math.floor(weighted[idx]), -phrase_lengths[idx], -idx),
                reverse=True,
            )
            for idx in remainders[: bars_target - assigned]:
                per_phrase[idx] += 1

        cumulative: list[int] = []
        running = 0
        for bars in per_phrase:
            running += bars
            cumulative.append(running)
        return cumulative

    def enforce_phrase_bar_targets(
        self,
        *,
        rhythm_plan: list[dict],
        phrase_end_syllable_indices: list[int],
        phrase_bar_targets: list[int],
        pickup_beats: float,
        syllable_count: int,
    ) -> list[dict]:
        if not phrase_end_syllable_indices:
            return rhythm_plan

        bars_target = phrase_bar_targets[-1] if phrase_bar_targets else (self._bars_per_verse_target or 1)
        available_beats = bars_target * self._beat_cap
        min_beats_needed = 0.5 * syllable_count
        if min_beats_needed - available_beats > 1e-9:
            raise VerseFormConstraintError(
                f"Bars per Verse ({bars_target}) is too short for this verse. Try a longer verse length."
            )

        adjusted = [
            {
                **item,
                "durations": list(item["durations"]),
                "modes": list(item["modes"]),
            }
            for item in rhythm_plan
        ]

        phrase_start = 0
        previous_target_beat = pickup_beats
        for phrase_idx, phrase_end in enumerate(phrase_end_syllable_indices):
            if not (0 <= phrase_end < len(adjusted)):
                continue
            phrase = adjusted[phrase_start : phrase_end + 1]
            target_end_beat = phrase_bar_targets[phrase_idx] * self._beat_cap
            desired_phrase_beats = target_end_beat - previous_target_beat
            minimum_phrase_beats = 0.5 * len(phrase)
            if desired_phrase_beats + 1e-9 < minimum_phrase_beats:
                raise VerseFormConstraintError(
                    f"Bars per Verse ({bars_target}) is too short to fit phrase {phrase_idx + 1} with current constraints."
                )

            current_phrase_beats = sum(sum(slot["durations"]) for slot in phrase)
            delta = desired_phrase_beats - current_phrase_beats

            if delta > 1e-9:
                phrase[-1]["durations"][-1] += delta
            elif delta < -1e-9:
                remaining = -delta
                for slot in reversed(phrase):
                    for duration_idx in range(len(slot["durations"]) - 1, -1, -1):
                        reducible = max(0.0, slot["durations"][duration_idx] - 0.5)
                        if reducible <= 1e-9:
                            continue
                        reduction = min(reducible, remaining)
                        slot["durations"][duration_idx] -= reduction
                        remaining -= reduction
                        if remaining <= 1e-9:
                            break
                    if remaining <= 1e-9:
                        break
                if remaining > 1e-9:
                    raise VerseFormConstraintError(
                        f"Bars per Verse ({bars_target}) is too short to fit phrase {phrase_idx + 1} with current constraints."
                    )

            previous_target_beat = target_end_beat
            phrase_start = phrase_end + 1

        for slot in adjusted:
            normalized_durations: list[float] = []
            normalized_modes: list[str] = []
            for duration_idx, duration in enumerate(slot["durations"]):
                mode = slot["modes"][duration_idx] if duration_idx < len(slot["modes"]) else "single"
                remaining = duration
                first = True
                while remaining > self._beat_cap + 1e-9:
                    normalized_durations.append(self._beat_cap)
                    normalized_modes.append(mode if first else "tie_continue")
                    remaining -= self._beat_cap
                    first = False
                normalized_durations.append(remaining)
                normalized_modes.append(mode if first else "tie_continue")
            slot["durations"] = normalized_durations
            slot["modes"] = normalized_modes

        return adjusted


    def phrase_bar_targets_from_rhythm_plan(
        self,
        *,
        pickup_beats: float,
        phrase_end_syllable_indices: list[int],
        rhythm_plan: list[dict],
    ) -> list[int]:
        targets: list[int] = []
        for phrase_end_index in phrase_end_syllable_indices:
            if 0 <= phrase_end_index < len(rhythm_plan):
                running_beats = pickup_beats + sum(sum(item["durations"]) for item in rhythm_plan[: phrase_end_index + 1])
                targets.append(int(max(running_beats - 1e-9, 0.0) // self._beat_cap) + 1)
        return targets

    def plan(
        self,
        *,
        pickup_beats: float,
        bars_per_verse: int,
        phrase_end_syllable_indices: list[int],
        phrase_bar_targets: list[int],
        rhythm_template: list[dict],
    ) -> PlannedVerseForm:
        return PlannedVerseForm(
            pickup_beats=pickup_beats,
            bars_per_verse=bars_per_verse,
            target_measure_count=bars_per_verse,
            phrase_end_syllable_indices=list(phrase_end_syllable_indices),
            phrase_bar_targets=list(phrase_bar_targets),
            rhythmic_skeleton=[list(item["durations"]) for item in rhythm_template],
        )


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

            if used < beat_cap and cursors[voice] < len(voice_notes[voice]) and voice_notes[voice][cursors[voice]].beats > beat_cap + 1e-9:
                overflowing = voice_notes[voice][cursors[voice]]
                chunk = overflowing.model_copy(deep=True)
                chunk.beats = beat_cap - used
                m_voices[voice].append(chunk)
                overflowing.beats -= chunk.beats
                overflowing.lyric = None
                overflowing.lyric_syllable_id = None
                overflowing.lyric_mode = "tie_continue"
                used += chunk.beats

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
    return min(candidates, key=lambda m: (abs(m - target), _melodic_leap_penalty(abs(m - previous)), m))


def _melodic_leap_penalty(leap_size: int) -> float:
    if leap_size <= 2:
        return float(leap_size)
    if leap_size <= 4:
        return leap_size - 1.0
    return float(leap_size) + 1.5


def _phrase_note_totals(rhythm_plan: list[dict], phrase_end_ids: set[str]) -> list[int]:
    totals: list[int] = []
    running = 0
    for item in rhythm_plan:
        running += len(item["durations"])
        if item["syllable_id"] in phrase_end_ids:
            totals.append(running)
            running = 0
    if running > 0:
        totals.append(running)
    return totals or [1]


def _phrase_end_indices_from_phrase_blocks(syllables: list[ScoreSyllable], phrase_blocks: list[PhraseBlock]) -> list[int]:
    word_re = r"[A-Za-z']+(?:-[A-Za-z']+)*"
    word_boundaries: list[int] = []
    running_words = 0
    for block in phrase_blocks:
        words = len(re.findall(word_re, block.text))
        running_words += words
        if words <= 0 or block.merge_with_next_phrase:
            continue
        word_boundaries.append(running_words - 1)

    index_by_word: dict[int, int] = {}
    for idx, syllable in enumerate(syllables):
        index_by_word[syllable.word_index] = idx
    return [index_by_word[word_index] for word_index in word_boundaries if word_index in index_by_word]


def _apply_phrase_end_indices(syllables: list[ScoreSyllable], phrase_end_indices: list[int], phrase_blocks: list[PhraseBlock]) -> None:
    for syllable in syllables:
        syllable.phrase_end_after = False
        syllable.breath_after_phrase = False

    phrase_end_set = set(phrase_end_indices)
    for idx in phrase_end_set:
        if 0 <= idx < len(syllables):
            syllables[idx].phrase_end_after = True

    # Preserve explicit breath markers at phrase-block boundaries.
    block_boundaries = _phrase_end_indices_from_phrase_blocks(syllables, phrase_blocks)
    breath_boundaries = {
        boundary for block, boundary in zip(phrase_blocks, block_boundaries, strict=False) if block.breath_after_phrase
    }
    for idx in breath_boundaries:
        if 0 <= idx < len(syllables):
            syllables[idx].phrase_end_after = True
            syllables[idx].breath_after_phrase = True




def _initial_identical_pitch_run(notes: list[ScoreNote]) -> tuple[int, int | None]:
    sung = [n for n in notes if not n.is_rest]
    if not sung:
        return 0, None

    last_pitch = pitch_to_midi(sung[-1].pitch)
    run = 0
    for note in reversed(sung):
        midi = pitch_to_midi(note.pitch)
        if midi != last_pitch:
            break
        run += 1
    return run, last_pitch

def _apply_repetition_guardrail(
    candidate: int,
    previous_pitch: int,
    identical_count: int,
    scale_set: set[int],
    contour_direction: int,
) -> int:
    if identical_count < MAX_IDENTICAL_CONSECUTIVE_PITCHES:
        return candidate

    direction = 1 if contour_direction >= 0 else -1
    for delta in (direction, -direction, direction * 2, -direction * 2):
        moved = _constrain_melodic_candidate(previous_pitch + delta, previous_pitch, "soprano", scale_set)
        if moved != previous_pitch:
            return moved

    return candidate


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


def _apply_phrase_cadential_bias(
    chords: list[ScoreChord],
    sections: list[ScoreSection],
    section_plans: list[tuple[str, str, bool, str, float, list[dict]]],
    beat_cap: float,
    key: str,
    primary_mode: str | None,
) -> list[ScoreChord]:
    if not chords:
        return chords

    scale = parse_key(key, primary_mode)
    section_by_id = {section.id: section for section in sections}
    chords_by_measure = {ch.measure_number: ch for ch in chords}
    section_measures: dict[str, set[int]] = {}
    for chord in chords:
        section_measures.setdefault(chord.section_id, set()).add(chord.measure_number)

    phrase_end_measures: dict[str, list[int]] = {}
    cursor = 0.0
    for section_id, _label, _is_verse, _music_unit_id, pickup_beats, rhythm_plan in section_plans:
        section = section_by_id.get(section_id)
        if section is None:
            continue
        phrase_end_ids = {syllable.id for syllable in section.syllables if syllable.phrase_end_after}
        cursor += pickup_beats
        for item in rhythm_plan:
            cursor += sum(item["durations"])
            if item["syllable_id"] not in phrase_end_ids:
                continue
            ending_measure = int(max(cursor - 1e-9, 0.0) // beat_cap) + 1
            phrase_end_measures.setdefault(section_id, []).append(ending_measure)

    for section_id, endings in phrase_end_measures.items():
        available_measures = section_measures.get(section_id, set())
        if not available_measures:
            continue
        phrase_ends = sorted(set(endings))
        section_start = min(available_measures)
        prev_end = section_start - 1
        for phrase_end in phrase_ends:
            if phrase_end not in available_measures:
                prev_end = phrase_end
                continue

            span_measures = phrase_end - prev_end
            cadence_targets: list[tuple[int, int]] = [(phrase_end, 1)]
            if phrase_end - 1 in available_measures:
                cadence_targets.append((phrase_end - 1, 5))
            if span_measures >= 3 and phrase_end - 2 in available_measures:
                cadence_targets.append((phrase_end - 2, 2))

            for measure_number, degree in cadence_targets:
                chord = chords_by_measure.get(measure_number)
                if chord is None:
                    continue
                chord.degree = degree
                chord.symbol = chord_symbol(scale, degree)
                chord.pitch_classes = triad_pitch_classes(scale, degree)

            prev_end = phrase_end

    return sorted(chords_by_measure.values(), key=lambda chord: chord.measure_number)






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

def _expand_arrangement(req: CompositionRequest) -> list[tuple[str, str, bool, str, str, float, list[PhraseBlock]]]:
    section_defs = {}
    for idx, section in enumerate(req.sections, start=1):
        section_key = section.id or f"section-{idx}"
        section_defs[section_key] = section

    if not req.arrangement:
        return [
            (
                (section.id or f"section-{idx}"),
                section.label,
                section.is_verse,
                ("verse" if section.is_verse else section.label),
                "off",
                0.0,
                [PhraseBlock(text=line.strip(), must_end_at_barline=True, breath_after_phrase=False, merge_with_next_phrase=False) for line in section.text.splitlines() if line.strip()]
                or [PhraseBlock(text=section.text, must_end_at_barline=True, breath_after_phrase=False, merge_with_next_phrase=False)],
            )
            for idx, section in enumerate(req.sections, start=1)
        ]

    expanded: list[tuple[str, str, bool, str, str, float, list[PhraseBlock]]] = []
    for item in req.arrangement:
        section = section_defs.get(item.section_id)
        if section is None:
            raise ValueError(f"Arrangement references unknown section_id: {item.section_id}")
        phrase_blocks = item.phrase_blocks or [
            PhraseBlock(text=line.strip(), must_end_at_barline=True, breath_after_phrase=False, merge_with_next_phrase=False)
            for line in section.text.splitlines()
            if line.strip()
        ]
        if not phrase_blocks:
            phrase_blocks = [PhraseBlock(text=section.text, must_end_at_barline=True, breath_after_phrase=False, merge_with_next_phrase=False)]
        expanded.append((
            item.section_id,
            section.label,
            section.is_verse,
            ("verse" if section.is_verse else (item.progression_cluster or section.label)),
            item.anacrusis_mode,
            item.anacrusis_beats,
            phrase_blocks,
        ))

    return expanded


def _build_arrangement_music_units(arranged_instances: list[tuple[str, str, bool, str, str, float, list[PhraseBlock]]]) -> list[ArrangementMusicUnit]:
    verse_index = 0
    music_units: list[ArrangementMusicUnit] = []
    for arrangement_index, (_section_id, section_label, is_verse, music_unit_id, _mode, _beats, _phrases) in enumerate(arranged_instances):
        current_verse_index = 1
        if is_verse:
            verse_index += 1
            current_verse_index = verse_index
        music_units.append(
            ArrangementMusicUnit(
                arrangement_index=arrangement_index,
                music_unit_id=music_unit_id,
                verse_index=current_verse_index,
            )
        )
    return music_units


def _recommend_anacrusis_beats(syllable_count: int, beat_cap: float) -> float:
    if syllable_count < 7 or beat_cap <= 0:
        return 0.0
    remainder = syllable_count % int(beat_cap)
    if remainder == 1:
        return 1.0
    if beat_cap >= 4 and remainder == 3 and syllable_count >= 11:
        return 1.0
    return 0.0


def _stable_pickup_seed(*, section_id: str, section_label: str, rhythm_seed: str, music_unit_id: str, mode: str) -> str:
    raw = f"{section_id}|{section_label}|{music_unit_id}|{mode}|{rhythm_seed}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _resolve_anacrusis_beats(mode: str, configured_beats: float, syllable_count: int, beat_cap: float, *, seed: str) -> float:
    if mode == "manual":
        return max(0.0, min(configured_beats, max(0.0, beat_cap - 0.5)))
    if mode == "auto":
        _ = seed
        return _recommend_anacrusis_beats(syllable_count, beat_cap)
    return 0.0

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


def _repair_phrase_end_stability(score: CanonicalScore, primary_mode: str | None) -> None:
    if score.meta.stage != "melody":
        return

    bpb = beats_per_measure(score.meta.time_signature)
    scale_set = set(parse_key(score.meta.key, primary_mode).semitones)
    chord_by_measure = {ch.measure_number: ch for ch in score.chord_progression}
    tonic_tones = set(triad_pitch_classes(parse_key(score.meta.key, primary_mode), 1))
    phrase_end_ids = {
        syllable.id
        for section in score.sections
        for syllable in section.syllables
        if syllable.phrase_end_after
    }
    if not phrase_end_ids:
        return

    soprano = _flatten_voice(score, "soprano")
    last_index_by_syllable: dict[str, int] = {}
    for idx, note in enumerate(soprano):
        if note.is_rest or note.lyric_syllable_id is None:
            continue
        last_index_by_syllable[note.lyric_syllable_id] = idx

    prev = None
    cursor = 0.0
    for idx, note in enumerate(soprano):
        if note.is_rest:
            cursor += note.beats
            continue
        midi = pitch_to_midi(note.pitch)
        basis = midi if prev is None else prev
        end_pos = cursor + note.beats
        syllable_id = note.lyric_syllable_id
        if syllable_id in phrase_end_ids and last_index_by_syllable.get(syllable_id) == idx:
            end_measure = int(max(end_pos - 1e-9, 0.0) // bpb) + 1
            chord = chord_by_measure.get(end_measure)
            tones = set(chord.pitch_classes) if chord else scale_set
            stable_tones = tonic_tones if chord and chord.degree == 1 else tones
            midi = _nearest_pitch_class_with_leap(midi, basis, stable_tones, "soprano")
            midi = _constrain_melodic_candidate(midi, basis, "soprano", scale_set)
            midi = _nearest_pitch_class_with_leap(midi, basis, stable_tones, "soprano")
            note.pitch = midi_to_pitch(midi)

        prev = pitch_to_midi(note.pitch)
        cursor = end_pos


def _auto_repair_melody_score(score: CanonicalScore, primary_mode: str | None) -> CanonicalScore:
    _repair_missing_chords(score, primary_mode)
    _repair_key_mode_mismatch(score, primary_mode)
    _repair_soprano_strong_beats(score, primary_mode)
    _repair_phrase_end_stability(score, primary_mode)
    _repair_phrase_end_barlines(score)
    score.chord_progression.sort(key=lambda chord: chord.measure_number)
    return normalize_score_for_rendering(score)


def _repair_phrase_end_barlines(score: CanonicalScore) -> bool:
    if score.meta.stage != "melody":
        return False

    beat_cap = beats_per_measure(score.meta.time_signature)
    phrase_end_ids = {
        syllable.id
        for section in score.sections
        for syllable in section.syllables
        if syllable.phrase_end_after and syllable.must_end_at_barline
    }
    if not phrase_end_ids:
        return False

    soprano_notes = _flatten_voice(score, "soprano")
    last_index_by_syllable: dict[str, int] = {}
    for idx, note in enumerate(soprano_notes):
        if note.is_rest or note.lyric_syllable_id is None:
            continue
        last_index_by_syllable[note.lyric_syllable_id] = idx

    cursor = 0.0
    repairs: list[dict[str, float | str]] = []
    for idx, note in enumerate(soprano_notes):
        end_pos = cursor + note.beats
        syllable_id = note.lyric_syllable_id
        is_phrase_end = (
            not note.is_rest
            and syllable_id in phrase_end_ids
            and last_index_by_syllable.get(syllable_id) == idx
        )
        if is_phrase_end:
            end_mod = end_pos % beat_cap
            if abs(end_mod) > 1e-6:
                extension = beat_cap - end_mod
                note.beats += extension
                end_pos += extension
                repairs.append(
                    {
                        "syllable_id": syllable_id,
                        "extended_beats": round(extension, 6),
                        "repaired_end_beat": round(end_pos, 6),
                    }
                )
        cursor = end_pos

    if not repairs:
        return False

    score.measures = _pack_measures({"soprano": soprano_notes, "alto": [], "tenor": [], "bass": []}, score.meta.time_signature)
    score.chord_progression = _repair_harmony_progression(
        score.chord_progression,
        len(score.measures),
        score.meta.key,
        score.meta.primary_mode,
    )
    log_event(
        logger,
        "phrase_barline_repair_applied",
        level=logging.WARNING,
        repaired_phrase_count=len(repairs),
        diagnostics=repairs,
    )
    return True


def _measure_count_by_section(score: CanonicalScore) -> dict[str, int]:
    counts: dict[str, int] = {}
    for chord in score.chord_progression:
        counts[chord.section_id] = counts.get(chord.section_id, 0) + 1
    return counts


def _regenerate_progression_for_units(
    score: CanonicalScore,
    selected_units: list[str],
    rng: random.Random,
) -> list[ScoreChord]:
    if not score.chord_progression:
        return []

    scale = parse_key(score.meta.key, score.meta.primary_mode)
    requested = {unit.strip() for unit in selected_units if unit and unit.strip()}
    available = {("verse" if section.is_verse else section.label) for section in score.sections}
    target_units = requested or available

    measure_counts = _measure_count_by_section(score)
    regenerated_cycles: dict[str, list[int]] = {}
    regenerated: list[ScoreChord] = []

    for section in score.sections:
        section_id = section.id
        unit_id = "verse" if section.is_verse else section.label
        section_chords = [c for c in score.chord_progression if c.section_id == section_id]
        if not section_chords:
            continue
        section_chords = sorted(section_chords, key=lambda chord: chord.measure_number)
        measure_count = measure_counts.get(section_id, len(section_chords))

        if unit_id in target_units:
            log_event(logger, "music_unit_progression_generation_started", section_id=section_id, music_unit_id=unit_id, measure_count=measure_count)
            cycle = regenerated_cycles.get(unit_id)
            if cycle is None:
                base_cycle = _cluster_progression_cycle(scale, unit_id)
                rotation = rng.randrange(len(base_cycle))
                cycle = base_cycle[rotation:] + base_cycle[:rotation]
                if rng.random() > 0.5:
                    cycle = list(reversed(cycle))
                regenerated_cycles[unit_id] = cycle
            start_measure = section_chords[0].measure_number
            regenerated_section = _build_section_progression(scale, section_id, start_measure, measure_count, cycle)
            previous_degrees = [chord.degree for chord in section_chords]
            regenerated_degrees = [chord.degree for chord in regenerated_section]
            if regenerated_degrees == previous_degrees and len(cycle) > 1:
                shifted_cycle = cycle[1:] + cycle[:1]
                regenerated_cycles[unit_id] = shifted_cycle
                regenerated_section = _build_section_progression(scale, section_id, start_measure, measure_count, shifted_cycle)
            regenerated.extend(regenerated_section)
            log_event(logger, "music_unit_progression_generation_completed", section_id=section_id, music_unit_id=unit_id, measure_count=measure_count)
            continue

        regenerated.extend(section_chords)

    return sorted(regenerated, key=lambda chord: chord.measure_number)


def _project_verse_rhythm_to_template(section_id: str, syllables: list["ScoreSyllable"], template_plan: list[dict]) -> list[dict]:
    projected: list[dict] = []
    shortage_extension = bool(syllables) and len(syllables) < len(template_plan)
    extension_anchor = len(syllables) - 1 if shortage_extension else None

    for idx, template_item in enumerate(template_plan):
        if idx < len(syllables):
            syllable = syllables[idx]
            lyric_text = syllable.text
            lyric_mode_override = None
        elif extension_anchor is not None and extension_anchor >= 0:
            syllable = None
            lyric_text = None
            lyric_mode_override = ["melisma_continue" for _ in template_item["modes"]]
        else:
            syllable = None
            lyric_text = None
            lyric_mode_override = None

        modes = list(lyric_mode_override or template_item["modes"])
        if shortage_extension and extension_anchor is not None and idx == extension_anchor and modes:
            if modes[-1] not in {"melisma_continue", "tie_continue", "melisma_start", "tie_start"}:
                modes[-1] = "melisma_start"

        projected.append(
            {
                "syllable_id": syllable.id if syllable else f"{section_id}-template-slot-{idx}",
                "syllable_text": lyric_text,
                "section_id": section_id,
                "lyric_index": idx,
                "durations": list(template_item["durations"]),
                "modes": modes,
                "stressed": syllable.stressed if syllable else bool(template_item.get("stressed", False)),
            }
        )
    return projected


def _split_duration_for_slot_expansion(duration: float) -> list[float] | None:
    if abs(duration - 2.0) < 1e-9:
        return [1.0, 1.0]
    if abs(duration - 1.5) < 1e-9:
        return [1.0, 0.5]
    if abs(duration - 1.0) < 1e-9:
        return [0.5, 0.5]
    return None


def _expand_verse_template_slots(
    template_plan: list[dict],
    target_slot_count: int,
    beat_cap: float,
    time_signature: str,
    phrase_end_indices: list[int],
) -> tuple[list[dict], list[int], int]:
    expanded = [
        {
            "durations": list(item["durations"]),
            "modes": list(item["modes"]),
            "stressed": bool(item.get("stressed", False)),
        }
        for item in template_plan
    ]
    adjusted_phrase_end_indices = list(phrase_end_indices)
    splits_count = 0

    while len(expanded) < target_slot_count:
        cursor = 0.0
        candidates: list[tuple[float, int, int, int, int, list[float]]] = []
        cadence_indices = set(adjusted_phrase_end_indices)
        for slot_idx, item in enumerate(expanded):
            slot_start = cursor
            slot_is_cadence = slot_idx in cadence_indices
            weak_beat_priority = 0 if not _is_strong_beat(slot_start % beat_cap, time_signature) else 1
            cadence_priority = 1 if slot_is_cadence else 0
            best_duration_idx: int | None = None
            best_split: list[float] | None = None
            best_duration_value = -1.0
            for duration_idx, duration in enumerate(item["durations"]):
                split = _split_duration_for_slot_expansion(duration)
                if not split:
                    continue
                if min(split) < 0.5 - 1e-9:
                    continue
                if duration > best_duration_value:
                    best_duration_idx = duration_idx
                    best_split = split
                    best_duration_value = duration

            if best_duration_idx is not None and best_split is not None:
                priority = (-best_duration_value, weak_beat_priority, cadence_priority, slot_idx)
                candidates.append((priority[0], priority[1], priority[2], priority[3], best_duration_idx, best_split))

            cursor += sum(item["durations"])

        if not candidates:
            break

        candidates.sort()
        _, _, _, slot_idx, duration_idx, split_durations = candidates[0]
        slot = expanded[slot_idx]
        original_mode = slot["modes"][duration_idx] if duration_idx < len(slot["modes"]) else "single"

        first_slot = {
            "durations": [split_durations[0]],
            "modes": [original_mode if original_mode != "melisma_continue" else "single"],
            "stressed": slot.get("stressed", False),
        }
        second_slot = {
            "durations": [split_durations[1]],
            "modes": ["single"],
            "stressed": False,
        }

        expanded[slot_idx : slot_idx + 1] = [first_slot, second_slot]
        adjusted_phrase_end_indices = [index + 1 if index >= slot_idx else index for index in adjusted_phrase_end_indices]
        splits_count += 1

    return expanded, adjusted_phrase_end_indices, splits_count


def _align_verse_syllables_to_template(section_id: str, syllables: list[ScoreSyllable], template_count: int) -> list[ScoreSyllable]:
    if template_count <= 0:
        return []
    if len(syllables) == template_count:
        return syllables
    if len(syllables) > template_count:
        if len(syllables) <= template_count + 2:
            aligned = [s.model_copy(deep=True) for s in syllables[:template_count]]
            overflow = syllables[template_count - 1 :]
            aligned[-1].text = " ".join(s.text for s in overflow if s.text).strip() or aligned[-1].text
            aligned[-1].phrase_end_after = overflow[-1].phrase_end_after
            aligned[-1].breath_after_phrase = overflow[-1].breath_after_phrase
            aligned[-1].must_end_at_barline = overflow[-1].must_end_at_barline
            return aligned
        raise VerseFormConstraintError(
            f"Verse form overflow for {section_id}: {len(syllables)} syllables cannot fit canonical {template_count}-slot skeleton"
        )

    aligned = [s.model_copy(deep=True) for s in syllables]
    if aligned:
        aligned[-1].phrase_end_after = False
        aligned[-1].breath_after_phrase = False
    for idx in range(len(aligned), template_count):
        aligned.append(
            ScoreSyllable(
                id=f"{section_id}-fill-{idx}",
                text="",
                section_id=section_id,
                word_index=idx,
                syllable_index_in_word=0,
                word_text="",
                hyphenated=False,
                stressed=False,
                phrase_end_after=(idx == template_count - 1),
                must_end_at_barline=False,
                breath_after_phrase=False,
            )
        )
    return aligned




def _count_full_measures_for_section(score: CanonicalScore, section_id: str, beat_cap: float) -> int:
    total_beats = 0.0
    for measure in score.measures:
        total_beats += sum(
            note.beats
            for note in measure.voices["soprano"]
            if note.section_id == section_id and not note.is_rest
        )
    return int(total_beats // beat_cap)

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
    section_plans: list[tuple[str, str, bool, str, float, list[dict]]] = []
    beat_cap = beats_per_measure(ts)

    section_defs = {section.id or f"section-{idx}": section for idx, section in enumerate(req.sections, start=1)}
    arranged_instances = _expand_arrangement(req)
    arrangement_music_units = _build_arrangement_music_units(arranged_instances)
    verse_form_planner = VerseFormPlanner(
        time_signature=ts,
        bars_per_verse_target=req.preferences.bars_per_verse,
    )

    verse_syllable_counts = [
        len(tokenize_phrase_blocks(f"verse-precompute-{idx}", phrase_blocks))
        for idx, (_sid, _label, is_verse, _unit, _mode, _beats, phrase_blocks) in enumerate(arranged_instances, start=1)
        if is_verse
    ]
    max_verse_syllable_count = max(verse_syllable_counts) if verse_syllable_counts else 0

    verse_rhythm_template: list[dict] | None = None
    verse_template_anacrusis_beats: float | None = None
    verse_template_phrase_end_indices: list[int] = []
    verse_phrase_bar_targets: list[int] = []
    verse_music_unit_form: VerseMusicUnitForm | None = None

    for idx, (
        arranged_section_id,
        section_label,
        is_verse,
        music_unit_id,
        anacrusis_mode,
        configured_anacrusis_beats,
        phrase_blocks,
    ) in enumerate(arranged_instances, start=1):
        section = section_defs[arranged_section_id]
        section_id = f"sec-{idx}"
        syllables = tokenize_phrase_blocks(section_id, phrase_blocks)
        phrase_end_indices_from_blocks = verse_form_planner.phrase_end_indices(syllables, phrase_blocks)
        if is_verse and phrase_end_indices_from_blocks:
            _apply_phrase_end_indices(syllables, phrase_end_indices_from_blocks, phrase_blocks)
        rhythm_config = config_for_preset(req.preferences.lyric_rhythm_preset, section_label)
        rhythm_seed = (
            f"{key}|{ts}|{tempo}|{req.preferences.style}|{section_label}|"
            f"{section_archetype(section_label)}|{section_id}|{req.preferences.lyric_rhythm_preset}"
        )
        if attempt_number > 0:
            rhythm_seed = f"{rhythm_seed}|attempt-{attempt_number}"
        pickup_seed = _stable_pickup_seed(
            section_id=section_id,
            section_label=section_label,
            rhythm_seed=rhythm_seed,
            music_unit_id=music_unit_id,
            mode=anacrusis_mode,
        )
        anacrusis_beats = verse_form_planner.resolve_pickup_beats(
            mode=anacrusis_mode,
            configured_beats=configured_anacrusis_beats,
            syllable_count=len(syllables),
            seed=pickup_seed,
        )
        if is_verse and req.preferences.bars_per_verse is not None and anacrusis_mode == "auto":
            anacrusis_beats = 0.0
        rhythm_plan = plan_syllable_rhythm(
            syllables,
            beat_cap,
            rhythm_config,
            rhythm_seed,
            initial_offset_beats=anacrusis_beats,
        )

        if is_verse:
            if verse_rhythm_template is None:
                verse_template_anacrusis_beats = anacrusis_beats
                verse_template_phrase_end_indices = phrase_end_indices_from_blocks
                if req.preferences.bars_per_verse is not None:
                    verse_phrase_bar_targets = verse_form_planner.allocate_phrase_bar_targets(
                        syllables=syllables,
                        phrase_end_syllable_indices=verse_template_phrase_end_indices,
                    )
                    rhythm_plan = verse_form_planner.enforce_phrase_bar_targets(
                        rhythm_plan=rhythm_plan,
                        phrase_end_syllable_indices=verse_template_phrase_end_indices,
                        phrase_bar_targets=verse_phrase_bar_targets,
                        pickup_beats=anacrusis_beats,
                        syllable_count=len(syllables),
                    )
                else:
                    verse_phrase_bar_targets = verse_form_planner.phrase_bar_targets_from_rhythm_plan(
                        pickup_beats=anacrusis_beats,
                        phrase_end_syllable_indices=verse_template_phrase_end_indices,
                        rhythm_plan=rhythm_plan,
                    )
                verse_rhythm_template = [
                    {
                        "durations": list(item["durations"]),
                        "modes": list(item["modes"]),
                        "stressed": item["stressed"],
                    }
                    for item in rhythm_plan
                ]
                if max_verse_syllable_count > len(verse_rhythm_template):
                    expanded_template, expanded_phrase_end_indices, splits_count = _expand_verse_template_slots(
                        verse_rhythm_template,
                        max_verse_syllable_count,
                        beat_cap,
                        ts,
                        verse_template_phrase_end_indices,
                    )
                    if len(expanded_template) < max_verse_syllable_count:
                        log_event(
                            logger,
                            "verse_template_alignment_failed",
                            level=logging.WARNING,
                            section_id=section_id,
                            syllable_count=max_verse_syllable_count,
                            template_syllable_count=len(verse_rhythm_template),
                            expanded_slot_count=len(expanded_template),
                            reason="insufficient_splittable_durations_in_canonical_verse_skeleton",
                        )
                        raise VerseFormConstraintError(
                            f"Verse form overflow for {section_id}: {max_verse_syllable_count} syllables cannot fit canonical "
                            f"{len(verse_rhythm_template)}-slot skeleton"
                        )
                    log_event(
                        logger,
                        "verse_slot_expansion_applied",
                        section_id=section_id,
                        before_slots=len(verse_rhythm_template),
                        after_slots=len(expanded_template),
                        splits_count=splits_count,
                    )
                    verse_rhythm_template = expanded_template
                    verse_template_phrase_end_indices = expanded_phrase_end_indices

                syllables = _align_verse_syllables_to_template(section_id, syllables, len(verse_rhythm_template))
                _apply_phrase_end_indices(syllables, verse_template_phrase_end_indices, phrase_blocks)
                rhythm_plan = _project_verse_rhythm_to_template(section_id, syllables, verse_rhythm_template)
            elif verse_rhythm_template:
                projected_template = verse_rhythm_template
                projected_phrase_end_indices = list(verse_template_phrase_end_indices)
                if len(syllables) > len(verse_rhythm_template):
                    projected_template, projected_phrase_end_indices, splits_count = _expand_verse_template_slots(
                        verse_rhythm_template,
                        len(syllables),
                        beat_cap,
                        ts,
                        verse_template_phrase_end_indices,
                    )
                    if len(projected_template) < len(syllables):
                        log_event(
                            logger,
                            "verse_template_alignment_failed",
                            level=logging.WARNING,
                            section_id=section_id,
                            syllable_count=len(syllables),
                            template_syllable_count=len(verse_rhythm_template),
                            expanded_slot_count=len(projected_template),
                            reason="insufficient_splittable_durations_in_canonical_verse_skeleton",
                        )
                        raise VerseFormConstraintError(
                            f"Verse form overflow for {section_id}: {len(syllables)} syllables cannot fit canonical "
                            f"{len(verse_rhythm_template)}-slot skeleton"
                        )
                    log_event(
                        logger,
                        "verse_slot_expansion_applied",
                        section_id=section_id,
                        before_slots=len(verse_rhythm_template),
                        after_slots=len(projected_template),
                        splits_count=splits_count,
                    )
                    verse_rhythm_template = projected_template
                    verse_template_phrase_end_indices = projected_phrase_end_indices

                syllables = _align_verse_syllables_to_template(section_id, syllables, len(verse_rhythm_template))
                _apply_phrase_end_indices(syllables, verse_template_phrase_end_indices, phrase_blocks)
                anacrusis_beats = verse_template_anacrusis_beats or 0.0
                rhythm_plan = _project_verse_rhythm_to_template(section_id, syllables, verse_rhythm_template)

            if verse_rhythm_template:
                verse_music_unit_form = verse_form_planner.plan(
                    pickup_beats=verse_template_anacrusis_beats or 0.0,
                    bars_per_verse=max(1, req.preferences.bars_per_verse or (verse_phrase_bar_targets[-1] if verse_phrase_bar_targets else 1)),
                    phrase_end_syllable_indices=verse_template_phrase_end_indices,
                    phrase_bar_targets=verse_phrase_bar_targets,
                    rhythm_template=verse_rhythm_template,
                ).to_music_unit_form(music_unit_id)

        sections.append(
            ScoreSection(
                id=section_id,
                label=section_label,
                is_verse=is_verse,
                verse_number=arrangement_music_units[idx - 1].verse_index if idx - 1 < len(arrangement_music_units) else None,
                anacrusis_beats=anacrusis_beats,
                lyrics=section.text,
                syllables=syllables,
            )
        )
        log_event(
            logger,
            "pickup_resolution",
            section_id=section_id,
            pickup_mode=anacrusis_mode,
            pickup_requested_beats=configured_anacrusis_beats,
            pickup_resolved_beats=anacrusis_beats,
            effective_first_measure_capacity=max(0.0, beat_cap - anacrusis_beats),
            pickup_seed=pickup_seed,
        )
        section_plans.append((section_id, section_label, is_verse, music_unit_id, anacrusis_beats, rhythm_plan))

    chord_progression: list[ScoreChord] = []
    music_unit_cycles: dict[str, list[int]] = {}
    beat_cursor = 0.0
    for section_id, _label, _is_verse, music_unit_id, anacrusis_beats, rhythm_plan in section_plans:
        total_beats = anacrusis_beats + sum(sum(item["durations"]) for item in rhythm_plan)
        start_measure = int(beat_cursor // beat_cap) + 1
        end_measure = int(max(beat_cursor + total_beats - 1e-9, beat_cursor) // beat_cap) + 1
        section_measures = max(1, end_measure - start_measure + 1)
        cluster_cycle = music_unit_cycles.setdefault(music_unit_id, _cluster_progression_cycle(scale, music_unit_id))
        chord_progression.extend(_build_section_progression(scale, section_id, start_measure, section_measures, cluster_cycle))
        beat_cursor += total_beats

    total_measures = max(1, int(max(beat_cursor - 1e-9, 0.0) // beat_cap) + 1)
    chord_progression = _repair_harmony_progression(chord_progression, total_measures, key, req.preferences.primary_mode)
    chord_progression = _apply_phrase_cadential_bias(
        chord_progression,
        sections,
        section_plans,
        beat_cap,
        key,
        req.preferences.primary_mode,
    )
    chord_by_measure = {ch.measure_number: ch for ch in chord_progression}
    log_event(logger, "pickup_compensation_strategy", strategy="full_bar_padding", section_count=len(section_plans))

    soprano_notes: list[ScoreNote] = []
    section_note_indices: dict[str, list[int]] = {}
    cursor = 0.0
    phrase_end_ids_by_section = {
        section.id: {syllable.id for syllable in section.syllables if syllable.phrase_end_after}
        for section in sections
    }
    tonic_stable_tones = set(triad_pitch_classes(scale, 1))

    verse_template_notes: list[ScoreNote] | None = None
    for section_id, label, is_verse, music_unit_id, anacrusis_beats, rhythm_plan in section_plans:
        center = 64 if label in {"verse", "bridge"} else 67
        repeated_pitch_count, previous_pitch = _initial_identical_pitch_run(soprano_notes)
        if previous_pitch is None:
            prev = center
            repeated_pitch_count = 0
        else:
            prev = previous_pitch
        phrase_note_totals = _phrase_note_totals(rhythm_plan, phrase_end_ids_by_section.get(section_id, set()))
        phrase_idx = 0
        phrase_progress = 0

        verse_note_cursor = 0
        if anacrusis_beats > 0:
            pickup_measure = int(cursor // beat_cap) + 1
            cycle = music_unit_cycles.get(music_unit_id, [])
            if cycle:
                pickup_degree = 5 if cycle[0] == 1 else cycle[0]
                pickup_chord = chord_by_measure.get(pickup_measure)
                if pickup_chord and pickup_chord.section_id == section_id:
                    pickup_chord.degree = pickup_degree
                    pickup_chord.symbol = chord_symbol(scale, pickup_degree)
                    pickup_chord.pitch_classes = triad_pitch_classes(scale, pickup_degree)
            pickup_note_index = len(soprano_notes)
            pickup_note = ScoreNote(pitch="REST", beats=anacrusis_beats, is_rest=True, section_id=section_id)
            if is_verse and verse_template_notes is not None and verse_note_cursor < len(verse_template_notes):
                template_note = verse_template_notes[verse_note_cursor]
                pickup_note.pitch = template_note.pitch
                pickup_note.is_rest = template_note.is_rest
            soprano_notes.append(pickup_note)
            section_note_indices.setdefault(section_id, []).append(pickup_note_index)
            verse_note_cursor += 1
            cursor += anacrusis_beats

        for item in rhythm_plan:
            step_base = random.choice([-2, -1, 0, 1, 2, 3])
            stressed_bonus = 1 if item["stressed"] else 0
            for ni, duration in enumerate(item["durations"]):
                phrase_total = phrase_note_totals[min(phrase_idx, len(phrase_note_totals) - 1)]
                phrase_halfway = max(1, math.ceil(phrase_total / 2))
                contour_bias = 1 if phrase_progress < phrase_halfway else -1

                measure_number = int(cursor // beat_cap) + 1
                measure_beat = cursor % beat_cap
                chord = chord_by_measure.get(measure_number)
                chord_tones = set(chord.pitch_classes if chord else scale.semitones)

                step = (
                    step_base
                    + (1 if (item["stressed"] and ni == 0 and step_base < 2) else 0)
                    - stressed_bonus
                    + contour_bias
                )
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

                is_phrase_end_note = (
                    item["syllable_id"] in phrase_end_ids_by_section.get(item["section_id"], set())
                    and ni == len(item["durations"]) - 1
                )
                if mode != "tie_continue":
                    candidate = _apply_repetition_guardrail(
                        candidate,
                        prev,
                        repeated_pitch_count,
                        scale_set,
                        contour_bias,
                    )

                if is_phrase_end_note:
                    cadence_measure = int(max(cursor + duration - 1e-9, 0.0) // beat_cap) + 1
                    cadence_chord = chord_by_measure.get(cadence_measure, chord)
                    cadence_tones = set(cadence_chord.pitch_classes if cadence_chord else chord_tones)
                    stable_tones = tonic_stable_tones if cadence_chord and cadence_chord.degree == 1 else cadence_tones
                    candidate = _nearest_pitch_class_with_leap(candidate, prev, stable_tones, "soprano")
                    candidate = _constrain_melodic_candidate(candidate, prev, "soprano", scale_set)
                    candidate = _nearest_pitch_class_with_leap(candidate, prev, stable_tones, "soprano")

                lyric_text = item["syllable_text"] if mode not in {"melisma_continue", "tie_continue"} else None

                note_index = len(soprano_notes)
                note = ScoreNote(
                    pitch=midi_to_pitch(candidate),
                    beats=duration,
                    lyric=lyric_text,
                    lyric_syllable_id=item["syllable_id"],
                    lyric_mode=mode,
                    section_id=item["section_id"],
                    lyric_index=item["lyric_index"],
                )
                if is_verse and verse_template_notes is not None and verse_note_cursor < len(verse_template_notes):
                    template_note = verse_template_notes[verse_note_cursor]
                    note.pitch = template_note.pitch
                    note.is_rest = template_note.is_rest
                soprano_notes.append(note)
                section_note_indices.setdefault(section_id, []).append(note_index)
                note_midi = prev
                if not note.is_rest:
                    note_midi = pitch_to_midi(note.pitch)
                repeated_pitch_count = repeated_pitch_count + 1 if note_midi == prev else 1
                prev = note_midi
                phrase_progress += 1
                if is_phrase_end_note:
                    phrase_idx += 1
                    phrase_progress = 0
                cursor += duration
                verse_note_cursor += 1

        if is_verse and verse_template_notes is None:
            note_indices = section_note_indices.get(section_id, [])
            if note_indices:
                verse_template_notes = [soprano_notes[idx].model_copy(deep=True) for idx in note_indices]


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
            arrangement_music_units=arrangement_music_units,
            verse_music_unit_form=verse_music_unit_form,
        ),
        sections=sections,
        measures=measures,
        chord_progression=chord_progression,
    )
    pickup_repair = _repair_phrase_end_barlines(score)
    if pickup_repair:
        log_event(
            logger,
            "pickup_phrase_boundary_enforcement",
            repaired=pickup_repair,
            reason="phrase_end_alignment_with_pickup",
        )

    if score.meta.verse_music_unit_form is not None:
        verse_sections = [section.id for section in score.sections if section.is_verse]
        expected_count = score.meta.verse_music_unit_form.total_measure_count
        for section_id in verse_sections[1:]:
            actual = _count_full_measures_for_section(score, section_id, beat_cap)
            if actual != expected_count:
                log_event(
                    logger,
                    "verse_form_measure_mismatch",
                    level=logging.ERROR,
                    section_id=section_id,
                    expected_measures=expected_count,
                    actual_measures=actual,
                )
                raise VerseFormConstraintError(
                    f"Verse {section_id} could not fit canonical verse form ({actual} measures vs {expected_count})"
                )
    return score


def generate_melody_score(req: CompositionRequest) -> CanonicalScore:
    primary_mode = req.preferences.primary_mode
    error_history: list[str] = []

    log_event(logger, "melody_generation_started", section_count=len(req.sections))
    for attempt_idx in range(MAX_GENERATION_ATTEMPTS):
        attempt = attempt_idx + 1
        try:
            score = _compose_melody_once(req, attempt_idx)
        except VerseFormConstraintError as exc:
            log_event(
                logger,
                "verse_form_constraint_failed",
                level=logging.ERROR,
                attempt=attempt,
                reason="canonical_verse_form_projection_failed",
                diagnostics=str(exc),
            )
            raise ValueError(
                "We couldn’t fit a later verse into Verse 1’s form. Please simplify the text for that verse (or adjust Bars per Verse when that option is available)."
            ) from exc
        harmony_issues = [
            err
            for err in validate_score(score, primary_mode)
            if err.startswith("Score must include an explicit chord progression")
            or err.startswith("Missing chord symbols")
            or err.startswith("Chord ")
        ]
        if harmony_issues:
            log_event(logger, "validation_failed", level=logging.WARNING, stage="melody_generation", attempt=attempt, reason="harmony_validation", diagnostics=harmony_issues)
            score.chord_progression = _repair_harmony_progression(
                score.chord_progression,
                len(score.measures),
                score.meta.key,
                primary_mode,
            )

        score = normalize_score_for_rendering(score)
        errs = validate_score(score, primary_mode)
        if req.preferences.bars_per_verse is not None:
            non_blocking_prefixes = (
                "Lyric phrase ending",
                "Orphan melodic note",
                "Soprano strong-beat note",
            )
            if errs and all(err.startswith(non_blocking_prefixes) for err in errs):
                log_event(logger, "validation_soft_pass", stage="melody_generation", attempt=attempt, diagnostics=errs)
                log_event(logger, "melody_generation_completed", attempt=attempt, soft_validated=True)
                return score
        if not errs:
            log_event(logger, "validation_passed", stage="melody_generation", attempt=attempt)
            log_event(logger, "melody_generation_completed", attempt=attempt)
            return score

        log_event(logger, "validation_failed", level=logging.WARNING, stage="melody_generation", attempt=attempt, reason="pre_repair", diagnostics=errs)
        repaired = normalize_score_for_rendering(_auto_repair_melody_score(score, primary_mode))
        repaired_errs = validate_score(repaired, primary_mode)
        if not repaired_errs:
            log_event(logger, "repair_retry_attempt", attempt=attempt, reason="validation_failure")
            log_event(logger, "validation_passed", stage="melody_generation", attempt=attempt, repaired=True)
            log_event(logger, "melody_generation_completed", attempt=attempt, repaired=True)
            return repaired

        log_event(logger, "validation_failed", level=logging.WARNING, stage="melody_generation", attempt=attempt, reason="post_repair", diagnostics=repaired_errs)
        log_event(logger, "repair_retry_attempt", level=logging.WARNING, attempt=attempt, reason="post_repair_validation_failure")
        error_history.append(f"attempt {attempt}: {'; '.join(repaired_errs)}")

    log_event(logger, "melody_generation_exhausted", level=logging.ERROR, diagnostics=error_history)
    raise ValueError(
        "Couldn’t generate a valid melody with the current constraints—try relaxing key/mode/time/tempo or click Regenerate"
    )


def refine_score(
    score: CanonicalScore,
    instruction: str,
    regenerate: bool,
    selected_units: list[str] | None = None,
    selected_clusters: list[str] | None = None,
    section_clusters: dict[str, str] | None = None,
) -> CanonicalScore:
    score = normalize_score_for_rendering(score)
    rng = random.Random() if regenerate else random.Random(instruction)
    scale_set = set(parse_key(score.meta.key, score.meta.primary_mode).semitones)
    if regenerate:
        requested_units = selected_units if selected_units is not None else selected_clusters or []
        score.chord_progression = _regenerate_progression_for_units(
            score,
            requested_units,
            rng,
        )
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
            midi += rng.choice([-3, -2, -1, 1, 2, 3])
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
        log_event(logger, "validation_failed", level=logging.ERROR, stage="refine_score", diagnostics=errs)
        raise ValueError("Refined score failed validation.")
    log_event(logger, "validation_passed", stage="refine_score")
    return normalize_score_for_rendering(score)


def harmonize_score(score: CanonicalScore) -> CanonicalScore:
    log_event(logger, "rendering_started", target="satb")
    score = normalize_score_for_rendering(score)
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
    satb = normalize_score_for_rendering(satb)
    errs = validate_score(satb)
    if errs:
        log_event(logger, "validation_failed", level=logging.ERROR, stage="harmonize_score", diagnostics=errs)
        raise ValueError("SATB score failed validation.")
    log_event(logger, "validation_passed", stage="harmonize_score")
    log_event(logger, "rendering_completed", target="satb")
    return satb

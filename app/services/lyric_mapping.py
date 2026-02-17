from __future__ import annotations

import random
import re
import math
from dataclasses import dataclass

from app.models import LyricRhythmPreset, PhraseBlock, ScoreSyllable, SectionLabel


def section_archetype(section_label: SectionLabel) -> str:
    normalized = section_label.strip().lower()
    if normalized in {"verse", "chorus", "bridge", "pre-chorus", "intro", "outro"}:
        return normalized
    if "pre" in normalized and "chorus" in normalized:
        return "pre-chorus"
    for archetype in ("chorus", "verse", "bridge", "intro", "outro"):
        if archetype in normalized:
            return archetype
    return "custom"


@dataclass
class RhythmPolicyConfig:
    melismaRate: float
    subdivisionRate: float
    phraseEndHoldBeats: float
    preferStrongBeatForStress: bool


def config_for_preset(preset: LyricRhythmPreset, section_label: SectionLabel) -> RhythmPolicyConfig:
    archetype = section_archetype(section_label)
    base = {
        "syllabic": RhythmPolicyConfig(0.08, 0.08, 1.5, True),
        "mixed": RhythmPolicyConfig(0.22, 0.18, 1.5, True),
        "melismatic": RhythmPolicyConfig(0.42, 0.22, 2.0, True),
    }[preset]

    # Chorus can tolerate more extension, verse a bit less.
    if archetype == "chorus":
        return RhythmPolicyConfig(
            melismaRate=min(1.0, base.melismaRate + 0.08),
            subdivisionRate=base.subdivisionRate,
            phraseEndHoldBeats=min(2.0, base.phraseEndHoldBeats + 0.25),
            preferStrongBeatForStress=base.preferStrongBeatForStress,
        )
    if archetype in {"verse", "bridge"}:
        return RhythmPolicyConfig(
            melismaRate=max(0.0, base.melismaRate - 0.05),
            subdivisionRate=base.subdivisionRate,
            phraseEndHoldBeats=base.phraseEndHoldBeats,
            preferStrongBeatForStress=base.preferStrongBeatForStress,
        )
    return base


def _scale_rhythm_config(config: RhythmPolicyConfig, length_scale: float) -> RhythmPolicyConfig:
    scale = max(0.6, min(1.8, length_scale))
    slower_bias = max(0.0, scale - 1.0)
    faster_bias = max(0.0, 1.0 - scale)
    return RhythmPolicyConfig(
        melismaRate=min(0.75, max(0.02, config.melismaRate + (0.18 * slower_bias) - (0.08 * faster_bias))),
        subdivisionRate=min(0.5, max(0.02, config.subdivisionRate + (0.16 * faster_bias) - (0.06 * slower_bias))),
        phraseEndHoldBeats=max(1.0, min(3.0, config.phraseEndHoldBeats * scale)),
        preferStrongBeatForStress=config.preferStrongBeatForStress,
    )


def split_word_into_syllables(word: str) -> list[str]:
    w = word.lower()
    if len(w) <= 3:
        return [word]
    chunks = re.findall(r"[^aeiouy]*[aeiouy]+(?:[^aeiouy]|$)", w)
    if not chunks:
        return [word]
    rebuilt: list[str] = []
    cursor = 0
    for c in chunks:
        length = len(c)
        rebuilt.append(word[cursor : cursor + length])
        cursor += length
    if cursor < len(word):
        rebuilt[-1] += word[cursor:]
    return [s for s in rebuilt if s]


def tokenize_section_lyrics(section_id: str, text: str) -> list[ScoreSyllable]:
    phrase_blocks = [
        PhraseBlock(text=line.strip(), must_end_at_barline=True, breath_after_phrase=False, merge_with_next_phrase=False)
        for line in text.splitlines()
        if line.strip()
    ]
    if not phrase_blocks:
        phrase_blocks = [PhraseBlock(text=text, must_end_at_barline=True, breath_after_phrase=False, merge_with_next_phrase=False)]
    return tokenize_phrase_blocks(section_id, phrase_blocks)


def tokenize_phrase_blocks(section_id: str, phrase_blocks: list[PhraseBlock]) -> list[ScoreSyllable]:
    normalized_blocks = [block for block in phrase_blocks if block.text.strip()]
    if not normalized_blocks:
        return []

    syllables = _tokenize_phrase_blocks_internal(section_id, normalized_blocks)
    if syllables:
        syllables[-1].phrase_end_after = True
    return syllables


def _tokenize_phrase_blocks_internal(section_id: str, phrase_blocks: list[PhraseBlock]) -> list[ScoreSyllable]:
    token_re = re.compile(r"[A-Za-z']+(?:-[A-Za-z']+)*|[\n]|[.,;?!]")

    out: list[ScoreSyllable] = []
    syllable_counter = 0
    word_index = -1
    total_blocks = len(phrase_blocks)

    for block_index, block in enumerate(phrase_blocks):
        tokens = token_re.findall(block.text)
        last_syllable_index_in_block: int | None = None

        for i, tok in enumerate(tokens):
            if tok in {"\n", ".", ",", ";", "?", "!"}:
                continue

            word_index += 1
            parts = tok.split("-")
            for part_idx, part in enumerate(parts):
                sylls = split_word_into_syllables(part)
                stressed_index = _primary_stress_index(part, sylls)
                for si, syl in enumerate(sylls):
                    out.append(
                        ScoreSyllable(
                            id=f"{section_id}-syl-{syllable_counter}",
                            text=syl,
                            section_id=section_id,
                            word_index=word_index,
                            syllable_index_in_word=si,
                            word_text=tok,
                            hyphenated=(len(parts) > 1 and part_idx < len(parts) - 1),
                            stressed=_is_stressed(syl, si, len(sylls), stressed_index),
                            phrase_end_after=False,
                            must_end_at_barline=block.must_end_at_barline,
                            breath_after_phrase=False,
                        )
                    )
                    last_syllable_index_in_block = len(out) - 1
                    syllable_counter += 1

            if i + 1 < len(tokens) and tokens[i + 1] in {".", ",", ";", "?", "!"} and out:
                out[-1].phrase_end_after = True

        if last_syllable_index_in_block is not None and block_index < total_blocks - 1 and not block.merge_with_next_phrase:
            out[last_syllable_index_in_block].phrase_end_after = True
        if last_syllable_index_in_block is not None and block.breath_after_phrase:
            out[last_syllable_index_in_block].phrase_end_after = True
            out[last_syllable_index_in_block].breath_after_phrase = True

    return out


def _primary_stress_index(word: str, syllables: list[str]) -> int:
    if not syllables:
        return 0
    count = len(syllables)
    if count == 1:
        return 0

    normalized = re.sub(r"[^a-z]", "", word.lower())
    if normalized.endswith(("tion", "sion", "ture", "cian", "cial", "ic")):
        return 1 if count == 2 else min(count - 1, max(0, count - 2))
    if count >= 3 and normalized.endswith(("ity", "graphy", "logy", "metry", "ative", "ify")):
        return min(count - 1, max(0, count - 3))
    if count == 2 and normalized.endswith(("al", "er", "or", "ing", "ed")):
        return 0
    return 0


def _is_stressed(syllable: str, syllable_index: int, syllable_count: int, primary_stress_index: int) -> bool:
    if syllable_count == 1:
        return True
    if syllable_index == primary_stress_index:
        return True
    return len(syllable) >= 4


def _align_to_strong_beat(plans: list[dict], beat_pos: float) -> float:
    if plans and abs(beat_pos % 1.0) > 1e-9:
        plans[-1]["durations"].append(0.5)
        plans[-1]["modes"].append("melisma_continue")
        return beat_pos + 0.5
    return beat_pos


def _strong_beat_positions(beats_per_bar: float) -> set[float]:
    if abs(beats_per_bar - 4.0) < 1e-9:
        return {0.0, 2.0}
    if abs(beats_per_bar - 3.0) < 1e-9:
        return {0.0}
    return {0.0, beats_per_bar / 2.0}


def _is_strong_beat(beat_pos: float, beats_per_bar: float) -> bool:
    pos = beat_pos % beats_per_bar
    return any(abs(pos - strong) < 1e-9 for strong in _strong_beat_positions(beats_per_bar))


def _phrase_target_total_beats(
    phrase: list[ScoreSyllable],
    beats_per_bar: float,
    start_offset: float = 0.0,
    target_scale: float = 1.0,
) -> float:
    min_beats_needed = 0.5 * len(phrase)
    target = max(min_beats_needed, min_beats_needed * max(0.6, min(1.8, target_scale)))
    if beats_per_bar <= 0:
        return max(1.0, math.ceil(target))

    end_pos = start_offset + target
    remainder = end_pos % beats_per_bar
    if abs(remainder) > 1e-9:
        target += beats_per_bar - remainder
    return max(target, beats_per_bar if min_beats_needed > beats_per_bar else target)


def _base_syllable_options(
    is_phrase_end: bool,
    config: RhythmPolicyConfig,
    rng: random.Random,
) -> list[tuple[list[float], list[str]]]:
    options: list[tuple[list[float], list[str]]] = [([1.0], ["single"]), ([0.5], ["subdivision"])]
    if not is_phrase_end and (config.melismaRate > 0 or rng.random() < max(0.05, config.melismaRate)):
        options.append(([0.5, 0.5], ["melisma_start", "melisma_continue"]))
    if is_phrase_end:
        hold = max(1.0, config.phraseEndHoldBeats)
        if hold > 1.0:
            options.append(([1.0, hold - 1.0], ["tie_start", "tie_continue"]))
        else:
            options.append(([hold], ["single"]))
    return options


def _score_phrase_template(
    phrase: list[ScoreSyllable],
    template: list[tuple[list[float], list[str]]],
    beats_per_bar: float,
    config: RhythmPolicyConfig,
    rng: random.Random,
) -> tuple[float, float]:
    beat_pos = 0.0
    score = 0.0
    cadence_idx = max((idx for idx, syllable in enumerate(phrase) if syllable.stressed), default=len(phrase) - 1)
    durations_for_leap = [sum(durations) for durations, _ in template]
    continuation_count = sum(
        1
        for _durations, modes in template
        for mode in modes
        if mode in {"melisma_continue", "tie_continue"}
    )

    for idx, syllable in enumerate(phrase):
        durations, _ = template[idx]
        syllable_total = sum(durations)
        is_strong_beat = _is_strong_beat(beat_pos, beats_per_bar)

        if syllable.stressed and is_strong_beat:
            score += 2.75
        elif syllable.stressed:
            score -= 1.25

        if syllable.phrase_end_after and is_strong_beat:
            score += 1.75

        if idx == cadence_idx:
            if is_strong_beat:
                score += 2.5
            else:
                score -= 1.25
            score += min(2.5, max(0.0, syllable_total - 1.0) * 1.8)

        short_notes = sum(1 for duration in durations if duration <= 0.5 + 1e-9)
        score -= 0.5 * short_notes
        beat_pos += syllable_total

    for i in range(1, len(durations_for_leap)):
        gap = abs(durations_for_leap[i] - durations_for_leap[i - 1])
        if gap > 1.0:
            score -= 0.75 * gap

    score += continuation_count * config.melismaRate * 1.25

    # Keep deterministic tie-breaking but still seed-sensitive.
    tie_break = rng.random() * 0.001
    return score, tie_break


def _search_phrase_template(
    phrase: list[ScoreSyllable],
    beats_per_bar: float,
    config: RhythmPolicyConfig,
    rng: random.Random,
    start_offset: float = 0.0,
    target_scale: float = 1.0,
) -> list[tuple[list[float], list[str]]]:
    target_total = _phrase_target_total_beats(phrase, beats_per_bar, start_offset, target_scale)
    candidates: list[list[tuple[list[float], list[str]]]] = []
    search_budget = 48

    def rec(idx: int, running_total: float, partial: list[tuple[list[float], list[str]]]) -> None:
        if len(candidates) >= search_budget:
            return
        if idx == len(phrase):
            if abs(running_total - target_total) < 1e-9:
                candidates.append([(d[:], m[:]) for d, m in partial])
            return

        remaining = len(phrase) - idx
        min_remaining = 0.5 * (remaining - 1)
        max_remaining = max(3.0, config.phraseEndHoldBeats + 2.0) + (remaining - 1)

        is_phrase_end = idx == len(phrase) - 1
        options = _base_syllable_options(is_phrase_end, config, rng)
        rng.shuffle(options)

        for durations, modes in options:
            total = running_total + sum(durations)
            if total + min_remaining > target_total + 1e-9:
                continue
            if total + max_remaining < target_total - 1e-9:
                continue
            partial.append((durations, modes))
            rec(idx + 1, total, partial)
            partial.pop()

    rec(0, 0.0, [])

    if not candidates:
        fallback: list[tuple[list[float], list[str]]] = [([1.0], ["single"]) for _ in phrase]
        total = sum(sum(durations) for durations, _ in fallback)
        extension = target_total - total
        if extension > 0:
            fallback[-1] = ([1.0, extension], ["tie_start", "tie_continue"])
        return fallback

    best = max(candidates, key=lambda c: _score_phrase_template(phrase, c, beats_per_bar, config, rng))

    has_continuation = any(
        mode in {"melisma_continue", "tie_continue"}
        for _durations, modes in best
        for mode in modes
    )
    if not has_continuation and config.melismaRate >= 0.3:
        for idx, (durations, modes) in enumerate(best[:-1]):
            if len(durations) == 1 and abs(durations[0] - 1.0) < 1e-9:
                best[idx] = ([0.5, 0.5], ["melisma_start", "melisma_continue"])
                break

    return best


def plan_syllable_rhythm(
    syllables: list[ScoreSyllable],
    beats_per_bar: float,
    config: RhythmPolicyConfig,
    seed: str,
    initial_offset_beats: float = 0.0,
    length_scale: float = 1.0,
) -> list[dict]:
    """Deterministic prosody-aware rhythm planning that preserves lyric phrase boundaries at barlines."""
    rng = random.Random(seed)
    plans: list[dict] = []

    phrases: list[list[ScoreSyllable]] = []
    current_phrase: list[ScoreSyllable] = []
    for syl in syllables:
        current_phrase.append(syl)
        if syl.phrase_end_after:
            phrases.append(current_phrase)
            current_phrase = []
    if current_phrase:
        phrases.append(current_phrase)

    running_offset = initial_offset_beats
    scaled_config = _scale_rhythm_config(config, length_scale)
    for phrase in phrases:
        phrase_plan: list[dict] = []
        phrase_template = _search_phrase_template(
            phrase,
            beats_per_bar,
            scaled_config,
            rng,
            running_offset,
            length_scale,
        )

        for idx, syl in enumerate(phrase):
            durations, modes = phrase_template[idx]
            phrase_plan.append(
                {
                    "syllable_id": syl.id,
                    "syllable_text": syl.text,
                    "section_id": syl.section_id,
                    "lyric_index": len(plans) + len(phrase_plan),
                    "durations": durations,
                    "modes": modes,
                    "stressed": syl.stressed,
                }
            )

        plans.extend(phrase_plan)
        running_offset += sum(sum(item["durations"]) for item in phrase_plan)

    return plans

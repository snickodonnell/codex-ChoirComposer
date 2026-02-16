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
        PhraseBlock(text=line.strip(), must_end_at_barline=True, breath_after_phrase=False)
        for line in text.splitlines()
        if line.strip()
    ]
    if not phrase_blocks:
        phrase_blocks = [PhraseBlock(text=text, must_end_at_barline=True, breath_after_phrase=False)]
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
                            stressed=_is_stressed(syl, si, len(sylls)),
                            phrase_end_after=False,
                            must_end_at_barline=block.must_end_at_barline,
                            breath_after_phrase=False,
                        )
                    )
                    last_syllable_index_in_block = len(out) - 1
                    syllable_counter += 1

            if i + 1 < len(tokens) and tokens[i + 1] in {".", ",", ";", "?", "!"} and out:
                out[-1].phrase_end_after = True

        if last_syllable_index_in_block is not None and block_index < total_blocks - 1:
            out[last_syllable_index_in_block].phrase_end_after = True
        if last_syllable_index_in_block is not None and block.breath_after_phrase:
            out[last_syllable_index_in_block].phrase_end_after = True
            out[last_syllable_index_in_block].breath_after_phrase = True

    return out


def _is_stressed(syllable: str, syllable_index: int, syllable_count: int) -> bool:
    if syllable_count == 1:
        return True
    if syllable_index == 0:
        return True
    return len(syllable) >= 4


def _align_to_strong_beat(plans: list[dict], beat_pos: float) -> float:
    if plans and abs(beat_pos % 1.0) > 1e-9:
        plans[-1]["durations"].append(0.5)
        plans[-1]["modes"].append("melisma_continue")
        return beat_pos + 0.5
    return beat_pos


def plan_syllable_rhythm(
    syllables: list[ScoreSyllable],
    beats_per_bar: float,
    config: RhythmPolicyConfig,
    seed: str,
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

    for phrase in phrases:
        phrase_plan: list[dict] = []
        phrase_beat_pos = 0.0
        must_end_at_barline = phrase[-1].must_end_at_barline if phrase else True
        for idx, syl in enumerate(phrase):
            if config.preferStrongBeatForStress and syl.stressed:
                phrase_beat_pos = _align_to_strong_beat(phrase_plan, phrase_beat_pos)

            is_phrase_end = idx == len(phrase) - 1
            use_melisma = rng.random() < config.melismaRate
            use_subdivision = (not use_melisma) and (rng.random() < config.subdivisionRate)

            if is_phrase_end:
                hold = config.phraseEndHoldBeats
                if hold <= 1.0:
                    durations = [hold]
                    modes = ["single"]
                else:
                    durations = [1.0, hold - 1.0]
                    modes = ["tie_start", "tie_continue"]
            elif use_melisma:
                durations = [0.5, 0.5]
                modes = ["melisma_start", "melisma_continue"]
            elif use_subdivision:
                durations = [0.5]
                modes = ["subdivision"]
            else:
                durations = [1.0]
                modes = ["single"]

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
            phrase_beat_pos += sum(durations)

        # Determine the smallest bar-aligned phrase length that can contain the line,
        # then reshape durations inside the phrase to fit it.
        min_beats_needed = 0.5 * len(phrase)

        def phrase_total() -> float:
            return sum(sum(item["durations"]) for item in phrase_plan)

        if must_end_at_barline:
            total = phrase_total()
            target_bars = max(math.ceil(min_beats_needed / beats_per_bar), math.ceil(total / beats_per_bar))
            target_total = target_bars * beats_per_bar
            if total > target_total + 1e-9:
                for item in phrase_plan[:-1]:
                    if total <= target_total + 1e-9:
                        break
                    if sum(item["durations"]) > 0.5 + 1e-9:
                        item["durations"] = [0.5]
                        item["modes"] = ["subdivision"]
                        total = phrase_total()

            while total > target_total + 1e-9:
                target_bars += 1
                target_total = target_bars * beats_per_bar

            if total < target_total - 1e-9:
                extension = target_total - total
                tail = phrase_plan[-1]
                if tail["modes"] and tail["modes"][-1] in {"tie_continue", "tie_start"}:
                    tail["durations"][-1] += extension
                    if len(tail["modes"]) == 1:
                        tail["modes"] = ["tie_start"]
                elif abs(extension - 0.5) < 1e-9:
                    tail["durations"].append(0.5)
                    tail["modes"].append("tie_continue")
                    tail["modes"][0] = "tie_start"
                else:
                    tail["durations"].append(extension)
                    tail["modes"].append("tie_continue")
                    tail["modes"][0] = "tie_start"

        plans.extend(phrase_plan)

    return plans

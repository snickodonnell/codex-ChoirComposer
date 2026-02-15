from __future__ import annotations

import random
import re
from dataclasses import dataclass

from app.models import LyricRhythmPreset, ScoreSyllable, SectionLabel


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
    token_re = re.compile(r"[A-Za-z']+(?:-[A-Za-z']+)*|[\n]|[.,;?!]")
    tokens = token_re.findall(text)

    out: list[ScoreSyllable] = []
    syllable_counter = 0
    word_index = -1

    for i, tok in enumerate(tokens):
        if tok in {"\n", ".", ",", ";", "?", "!"}:
            if out:
                out[-1].phrase_end_after = True
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
                    )
                )
                syllable_counter += 1

        # Also mark phrase ends if punctuation follows immediately.
        if i + 1 < len(tokens) and tokens[i + 1] in {"\n", ".", ",", ";", "?", "!"}:
            out[-1].phrase_end_after = True

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
    """Deterministic prosody-aware rhythm planning without index-pattern rules."""
    rng = random.Random(seed)
    plans: list[dict] = []
    beat_pos = 0.0

    for syl in syllables:
        if config.preferStrongBeatForStress and syl.stressed:
            beat_pos = _align_to_strong_beat(plans, beat_pos)

        is_phrase_end = syl.phrase_end_after
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

        remaining = beats_per_bar - (beat_pos % beats_per_bar)
        if sum(durations) > remaining + 1e-9:
            durations = [remaining]
            modes = ["single"]

        plans.append(
            {
                "syllable_id": syl.id,
                "syllable_text": syl.text,
                "section_id": syl.section_id,
                "lyric_index": len(plans),
                "durations": durations,
                "modes": modes,
                "stressed": syl.stressed,
            }
        )
        beat_pos += sum(durations)

    return plans

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


VoiceName = Literal["soprano", "alto", "tenor", "bass"]
SectionLabel = str
LyricMode = Literal["none", "single", "melisma_start", "melisma_continue", "tie_start", "tie_continue", "subdivision"]
LyricRhythmPreset = Literal["syllabic", "mixed", "melismatic"]
PrimaryMode = Literal["ionian", "dorian", "phrygian", "lydian", "mixolydian", "aeolian", "locrian"]
MoodName = Literal["Uplifting", "Prayerful", "Joyful", "Reflective", "Triumphant", "Peaceful", "Lament"]

VALID_TONICS = {"C", "C#", "Db", "D", "D#", "Eb", "E", "F", "F#", "Gb", "G", "G#", "Ab", "A", "A#", "Bb", "B"}
MODE_FAMILIES = {
    "ionian": "major",
    "lydian": "major",
    "mixolydian": "major",
    "dorian": "minor",
    "phrygian": "minor",
    "aeolian": "minor",
    "locrian": "minor",
}


class LyricSection(BaseModel):
    id: str | None = Field(default=None, min_length=1, max_length=120)
    label: SectionLabel = Field(min_length=1, max_length=80)
    pause_beats: float = Field(default=0, ge=0, le=4)
    text: str = Field(min_length=1)




class ArrangementItem(BaseModel):
    section_id: str = Field(min_length=1, max_length=120)
    pause_beats: float = Field(default=0, ge=0, le=4)
    progression_cluster: str | None = Field(default=None, min_length=1, max_length=80)
    phrase_blocks: list["PhraseBlock"] = Field(default_factory=list)


class PhraseBlock(BaseModel):
    text: str = Field(min_length=1)
    must_end_at_barline: bool = True
    breath_after_phrase: bool = False
    merge_with_next_phrase: bool = False


class CompositionPreferences(BaseModel):
    key: str | None = None
    primary_mode: PrimaryMode | None = None
    time_signature: str | None = None
    tempo_bpm: int | None = Field(default=None, ge=30, le=300)
    style: str = Field(default="Contemporary Worship", min_length=2, max_length=120)
    mood: MoodName = "Uplifting"
    lyric_rhythm_preset: LyricRhythmPreset = "mixed"

    @field_validator("key")
    @classmethod
    def validate_key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            return None
        m = re.fullmatch(r"([A-Ga-g])([#b]?)(m?)", cleaned)
        if not m:
            raise ValueError("Invalid key. Use pitch-class keys like C, F#, Bb, Am.")
        tonic = f"{m.group(1).upper()}{m.group(2)}"
        if tonic not in VALID_TONICS:
            raise ValueError("Invalid key tonic. Allowed tonics are A–G with optional #/b.")
        suffix = "m" if m.group(3) else ""
        return f"{tonic}{suffix}"

    @field_validator("primary_mode", mode="before")
    @classmethod
    def normalize_mode(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = str(value).strip().lower()
        aliases = {"major": "ionian", "minor": "aeolian", "natural minor": "aeolian"}
        return aliases.get(cleaned, cleaned)

    @field_validator("time_signature")
    @classmethod
    def validate_time_signature(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            return None
        m = re.fullmatch(r"(\d{1,2})\s*/\s*(\d{1,2})", cleaned)
        if not m:
            raise ValueError("Invalid time signature. Use forms like 4/4, 3/4, 6/8.")
        top = int(m.group(1))
        bottom = int(m.group(2))
        if top < 1 or top > 16:
            raise ValueError("Time-signature numerator must be between 1 and 16.")
        if bottom not in {1, 2, 4, 8, 16, 32}:
            raise ValueError("Time-signature denominator must be a note value (1,2,4,8,16,32).")
        return f"{top}/{bottom}"

    @model_validator(mode="after")
    def validate_mode_key_consistency(self):
        if not self.primary_mode or not self.key:
            return self
        if self.key.endswith("m"):
            raise ValueError("When Primary Mode is set, provide key tonic only (e.g. A + aeolian, not Am).")
        return self


class CompositionRequest(BaseModel):
    sections: list[LyricSection]
    arrangement: list[ArrangementItem] = Field(default_factory=list)
    preferences: CompositionPreferences = Field(default_factory=CompositionPreferences)


class ScoreSyllable(BaseModel):
    id: str
    text: str
    section_id: str
    word_index: int
    syllable_index_in_word: int
    word_text: str
    hyphenated: bool = False
    stressed: bool = False
    phrase_end_after: bool = False
    must_end_at_barline: bool = True
    breath_after_phrase: bool = False


class ScoreSection(BaseModel):
    id: str
    label: SectionLabel = Field(min_length=1, max_length=80)
    pause_beats: float = Field(default=0, ge=0, le=4)
    lyrics: str
    syllables: list[ScoreSyllable]


class ScoreNote(BaseModel):
    pitch: str = Field(description="Pitch like C4, F#3, or REST")
    beats: float = Field(gt=0)
    is_rest: bool = False
    lyric: str | None = None
    lyric_syllable_id: str | None = None
    lyric_mode: LyricMode = "none"
    section_id: str
    lyric_index: int | None = Field(default=None, ge=0)


class ScoreMeasure(BaseModel):
    number: int = Field(ge=1)
    voices: dict[VoiceName, list[ScoreNote]]


class ScoreChord(BaseModel):
    measure_number: int = Field(ge=1)
    section_id: str
    symbol: str
    degree: int = Field(ge=1, le=7)
    pitch_classes: list[int] = Field(min_length=3, max_length=3)


class ScoreMeta(BaseModel):
    key: str
    primary_mode: PrimaryMode | None = None
    time_signature: str
    tempo_bpm: int
    style: str
    stage: Literal["melody", "satb"]
    rationale: str


class CanonicalScore(BaseModel):
    meta: ScoreMeta
    sections: list[ScoreSection]
    measures: list[ScoreMeasure]
    chord_progression: list[ScoreChord] = Field(default_factory=list)


class MelodyResponse(BaseModel):
    score: CanonicalScore


class RefineRequest(BaseModel):
    score: CanonicalScore
    instruction: str = Field(min_length=3, max_length=300)
    regenerate: bool = False
    selected_clusters: list[str] = Field(default_factory=list)
    section_clusters: dict[str, str] = Field(default_factory=dict)


class SATBResponse(BaseModel):
    score: CanonicalScore
    harmonization_notes: str


class EndScoreResponse(BaseModel):
    melody: CanonicalScore
    satb: CanonicalScore
    composition_notes: str


class HarmonizeRequest(BaseModel):
    score: CanonicalScore


class PDFExportRequest(BaseModel):
    score: CanonicalScore


class EngravingPreviewRequest(BaseModel):
    score: CanonicalScore
    preview_mode: Literal["melody", "satb"]
    include_all_pages: bool = False
    scale: int = Field(default=42, ge=20, le=90)


class EngravingPreviewArtifact(BaseModel):
    page: int = Field(ge=1)
    svg: str = Field(min_length=1)


class EngravingPreviewResponse(BaseModel):
    preview_mode: Literal["melody", "satb"]
    cache_hit: bool
    artifacts: list[EngravingPreviewArtifact]


class ClientLogEvent(BaseModel):
    ts: str
    event: str = Field(min_length=1, max_length=120)
    type: str | None = Field(default=None, min_length=1, max_length=40)
    id: str | None = Field(default=None, min_length=1, max_length=240)
    reason: str | None = Field(default=None, min_length=1, max_length=120)
    offsetSeconds: float | None = None
    totalSeconds: float | None = None
    events: int | None = None
    progressSeconds: float | None = None

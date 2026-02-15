from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


VoiceName = Literal["soprano", "alto", "tenor", "bass"]
SectionLabel = Literal["verse", "chorus", "bridge", "pre-chorus", "intro", "outro", "custom"]
LyricMode = Literal["none", "single", "melisma_start", "melisma_continue", "tie_start", "tie_continue", "subdivision"]
LyricRhythmPreset = Literal["syllabic", "mixed", "melismatic"]


class LyricSection(BaseModel):
    label: SectionLabel
    title: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1)


class CompositionPreferences(BaseModel):
    key: str | None = None
    time_signature: str | None = None
    tempo_bpm: int | None = Field(default=None, ge=50, le=220)
    style: str = Field(default="Contemporary Worship", min_length=2, max_length=120)
    mood: str = Field(default="Uplifting", min_length=2, max_length=120)
    lyric_rhythm_preset: LyricRhythmPreset = "mixed"


class CompositionRequest(BaseModel):
    sections: list[LyricSection]
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


class ScoreSection(BaseModel):
    id: str
    label: SectionLabel
    title: str
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


class SATBResponse(BaseModel):
    score: CanonicalScore
    harmonization_notes: str


class HarmonizeRequest(BaseModel):
    score: CanonicalScore


class PDFExportRequest(BaseModel):
    score: CanonicalScore

from __future__ import annotations

import random
import re
from dataclasses import dataclass

NOTE_TO_SEMITONE = {
    "C": 0,
    "C#": 1,
    "Db": 1,
    "D": 2,
    "D#": 3,
    "Eb": 3,
    "E": 4,
    "F": 5,
    "F#": 6,
    "Gb": 6,
    "G": 7,
    "G#": 8,
    "Ab": 8,
    "A": 9,
    "A#": 10,
    "Bb": 10,
    "B": 11,
}
SEMITONE_TO_NOTE = {v: k for k, v in NOTE_TO_SEMITONE.items() if len(k) == 1 or "#" in k}

MAJOR_PATTERN = [0, 2, 4, 5, 7, 9, 11]
MINOR_PATTERN = [0, 2, 3, 5, 7, 8, 10]
MAJOR_TRIAD_QUALITIES = ["", "m", "m", "", "", "m", "dim"]
MINOR_TRIAD_QUALITIES = ["m", "dim", "", "m", "m", "", ""]

DEFAULT_KEYS = ["C", "G", "D", "F", "Bb", "A"]
DEFAULT_TIME_SIGNATURES = ["4/4", "3/4", "6/8"]

# Required default SATB ranges from product request.
VOICE_RANGES = {
    "soprano": (60, 81),  # C4 - A5
    "alto": (55, 74),  # G3 - D5
    "tenor": (48, 67),  # C3 - G4
    "bass": (40, 60),  # E2 - C4
}

VOICE_TESSITURA = {
    "soprano": (62, 79),
    "alto": (57, 72),
    "tenor": (50, 65),
    "bass": (43, 58),
}


@dataclass
class Scale:
    tonic: str
    is_minor: bool

    @property
    def semitones(self) -> list[int]:
        base = NOTE_TO_SEMITONE[self.tonic]
        pattern = MINOR_PATTERN if self.is_minor else MAJOR_PATTERN
        return [(base + p) % 12 for p in pattern]


def triad_pitch_classes(scale: Scale, degree: int) -> list[int]:
    idx = (degree - 1) % 7
    semis = scale.semitones
    return [semis[idx], semis[(idx + 2) % 7], semis[(idx + 4) % 7]]


def chord_symbol(scale: Scale, degree: int) -> str:
    idx = (degree - 1) % 7
    root_pc = scale.semitones[idx]
    root = SEMITONE_TO_NOTE[root_pc]
    quality = (MINOR_TRIAD_QUALITIES if scale.is_minor else MAJOR_TRIAD_QUALITIES)[idx]
    return f"{root}{quality}"


def tokenize_lyrics(text: str) -> list[str]:
    words = re.findall(r"[A-Za-z']+", text)
    syllables: list[str] = []
    for word in words:
        syllables.extend(split_into_syllables(word))
    return syllables or ["la"]


def split_into_syllables(word: str) -> list[str]:
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


def choose_defaults(style: str, mood: str) -> tuple[str, str, int]:
    random.seed(f"{style}-{mood}")
    key = random.choice(DEFAULT_KEYS)
    time_sig = random.choice(DEFAULT_TIME_SIGNATURES)
    tempo = random.randint(68, 116)
    return key, time_sig, tempo


def parse_key(key: str, primary_mode: str | None = None) -> Scale:
    cleaned = key.strip()
    key_marks_minor = cleaned.lower().endswith("m")
    tonic = cleaned[:-1] if key_marks_minor else cleaned
    tonic = tonic.strip().capitalize()

    mode_minor = {"dorian", "phrygian", "aeolian", "locrian"}
    mode = (primary_mode or "").strip().lower()
    is_minor = key_marks_minor or mode in mode_minor

    if tonic not in NOTE_TO_SEMITONE:
        tonic = "C"
    return Scale(tonic=tonic, is_minor=is_minor)


def midi_to_pitch(midi: int) -> str:
    octave = (midi // 12) - 1
    return f"{SEMITONE_TO_NOTE[midi % 12]}{octave}"


def pitch_to_midi(pitch: str) -> int:
    name = pitch[:-1]
    octave = int(pitch[-1])
    return NOTE_TO_SEMITONE[name] + (octave + 1) * 12


def nearest_in_range(candidate: int, lower: int, upper: int) -> int:
    while candidate < lower:
        candidate += 12
    while candidate > upper:
        candidate -= 12
    return max(lower, min(candidate, upper))

from __future__ import annotations

import logging
from fractions import Fraction

from app.logging_utils import log_event
from app.models import CanonicalScore

logger = logging.getLogger(__name__)

_DURATION_TYPES: list[tuple[Fraction, str, bool]] = [
    (Fraction(4, 1), "whole", False),
    (Fraction(3, 1), "half", True),
    (Fraction(2, 1), "half", False),
    (Fraction(3, 2), "quarter", True),
    (Fraction(1, 1), "quarter", False),
    (Fraction(3, 4), "eighth", True),
    (Fraction(1, 2), "eighth", False),
    (Fraction(3, 8), "16th", True),
    (Fraction(1, 4), "16th", False),
]


CLEFS_BY_STAFF = {
    1: ("G", "2"),
    2: ("F", "4"),
}


def export_musicxml(score: CanonicalScore) -> str:
    log_event(logger, "musicxml_render_started", measure_count=len(score.measures), stage=score.meta.stage)

    beats_i, beat_type_i = _parse_time_signature(score.meta.time_signature)
    divisions = _resolve_divisions(score)
    fifths, mode = _key_signature(score.meta.key)
    measure_duration = beats_i * divisions
    chords = {c.measure_number: c for c in score.chord_progression}

    lines: list[str] = [
        '<?xml version="1.0" encoding="UTF-8" standalone="no"?>',
        '<!DOCTYPE score-partwise PUBLIC "-//Recordare//DTD MusicXML 3.1 Partwise//EN"',
        '  "http://www.musicxml.org/dtds/partwise.dtd">',
        '<score-partwise version="3.1">',
        "  <part-list>",
        '    <score-part id="P1">',
        "      <part-name>Choir</part-name>",
        "    </score-part>",
        "  </part-list>",
        '  <part id="P1">',
    ]

    for measure in score.measures:
        lines.append(f'    <measure number="{measure.number}">')
        if measure.number == 1:
            lines.extend(
                [
                    "      <attributes>",
                    f"        <divisions>{divisions}</divisions>",
                    f"        <key><fifths>{fifths}</fifths><mode>{mode}</mode></key>",
                    f"        <time><beats>{beats_i}</beats><beat-type>{beat_type_i}</beat-type></time>",
                    "        <staves>2</staves>",
                    f"        <clef number=\"1\"><sign>{CLEFS_BY_STAFF[1][0]}</sign><line>{CLEFS_BY_STAFF[1][1]}</line></clef>",
                    f"        <clef number=\"2\"><sign>{CLEFS_BY_STAFF[2][0]}</sign><line>{CLEFS_BY_STAFF[2][1]}</line></clef>",
                    "      </attributes>",
                    (
                        "      <direction placement=\"above\"><direction-type><metronome><beat-unit>quarter</beat-unit>"
                        f"<per-minute>{score.meta.tempo_bpm}</per-minute></metronome></direction-type><sound tempo=\"{score.meta.tempo_bpm}\"/></direction>"
                    ),
                ]
            )

        if measure.number in chords:
            lines.extend(_harmony_xml(chords[measure.number]))

        lines.extend(_voice_measure_xml(score, measure.number, "soprano", 1, 1, divisions))
        lines.extend(_backup_xml(measure_duration))
        lines.extend(_voice_measure_xml(score, measure.number, "alto", 2, 1, divisions))
        lines.extend(_backup_xml(measure_duration))
        lines.extend(_voice_measure_xml(score, measure.number, "tenor", 3, 2, divisions))
        lines.extend(_backup_xml(measure_duration))
        lines.extend(_voice_measure_xml(score, measure.number, "bass", 4, 2, divisions))

        lines.append("    </measure>")

    lines.extend(["  </part>", "</score-partwise>"])

    content = "\n".join(lines)
    log_event(
        logger,
        "musicxml_render_completed",
        output_size_bytes=len(content.encode("utf-8")),
        measure_count=len(score.measures),
        stage=score.meta.stage,
    )
    return content


def _voice_measure_xml(score: CanonicalScore, measure_number: int, voice_name: str, voice_number: int, staff_number: int, divisions: int) -> list[str]:
    measure = next((m for m in score.measures if m.number == measure_number), None)
    if not measure:
        return []

    lines: list[str] = []
    for note in measure.voices[voice_name]:
        duration = max(1, int(round(note.beats * divisions)))
        note_type, dotted = _note_type_from_duration(note.beats)

        lines.append("      <note>")
        if note.is_rest:
            lines.append("        <rest/>")
        else:
            step, alter, octave = _pitch_components(note.pitch)
            lines.append("        <pitch>")
            lines.append(f"          <step>{step}</step>")
            if alter != 0:
                lines.append(f"          <alter>{alter}</alter>")
            lines.append(f"          <octave>{octave}</octave>")
            lines.append("        </pitch>")

        lines.append(f"        <duration>{duration}</duration>")
        lines.append(f"        <voice>{voice_number}</voice>")
        lines.append(f"        <type>{note_type}</type>")
        if dotted:
            lines.append("        <dot/>")
        lines.append(f"        <staff>{staff_number}</staff>")

        if note.lyric and voice_name == "soprano":
            syllabic = _lyric_syllabic(note.lyric_mode)
            lines.append("        <lyric number=\"1\">")
            if syllabic:
                lines.append(f"          <syllabic>{syllabic}</syllabic>")
            lines.append(f"          <text>{_escape_xml(note.lyric)}</text>")
            lines.append("        </lyric>")

        lines.append("      </note>")
    return lines


def _harmony_xml(chord) -> list[str]:
    symbol = chord.symbol or "C"
    root = symbol[0].upper()
    alter = 1 if "#" in symbol else -1 if "b" in symbol else 0
    kind = "minor" if "m" in symbol.lower() else "major"

    lines = [
        "      <harmony>",
        "        <root>",
        f"          <root-step>{root}</root-step>",
    ]
    if alter != 0:
        lines.append(f"          <root-alter>{alter}</root-alter>")
    lines.extend(
        [
            "        </root>",
            f"        <kind>{kind}</kind>",
            f"        <degree><degree-value>{chord.degree}</degree-value></degree>",
            "      </harmony>",
        ]
    )
    return lines


def _backup_xml(measure_duration: int) -> list[str]:
    return ["      <backup>", f"        <duration>{measure_duration}</duration>", "      </backup>"]


def _parse_time_signature(time_signature: str) -> tuple[int, int]:
    beats, beat_type = time_signature.split("/")
    return int(beats), int(beat_type)


def _resolve_divisions(score: CanonicalScore) -> int:
    durations: set[Fraction] = set()
    for measure in score.measures:
        for voice in ("soprano", "alto", "tenor", "bass"):
            for note in measure.voices[voice]:
                durations.add(Fraction(note.beats).limit_denominator(16))

    denom_lcm = 1
    for duration in durations or {Fraction(1, 1)}:
        denom_lcm = _lcm(denom_lcm, duration.denominator)
    return max(1, denom_lcm)


def _note_type_from_duration(beats: float) -> tuple[str, bool]:
    value = Fraction(beats).limit_denominator(16)
    for candidate, note_type, dotted in _DURATION_TYPES:
        if value == candidate:
            return note_type, dotted
    if value >= Fraction(2, 1):
        return "half", False
    if value >= Fraction(1, 1):
        return "quarter", False
    if value >= Fraction(1, 2):
        return "eighth", False
    return "16th", False


def _pitch_components(pitch: str) -> tuple[str, int, int]:
    step = pitch[0].upper()
    accidental = pitch[1:-1]
    octave = int(pitch[-1])
    if accidental == "#":
        alter = 1
    elif accidental == "b":
        alter = -1
    else:
        alter = 0
    return step, alter, octave


def _key_signature(key: str) -> tuple[int, str]:
    key_clean = key.strip()
    minor = key_clean.endswith("m")
    tonic = key_clean[:-1] if minor else key_clean
    mode = "minor" if minor else "major"

    fifths_map_major = {
        "C": 0,
        "G": 1,
        "D": 2,
        "A": 3,
        "E": 4,
        "B": 5,
        "F#": 6,
        "C#": 7,
        "F": -1,
        "Bb": -2,
        "Eb": -3,
        "Ab": -4,
        "Db": -5,
        "Gb": -6,
        "Cb": -7,
    }
    if minor:
        relative_major = {
            "A": "C",
            "E": "G",
            "B": "D",
            "F#": "A",
            "C#": "E",
            "G#": "B",
            "D#": "F#",
            "A#": "C#",
            "D": "F",
            "G": "Bb",
            "C": "Eb",
            "F": "Ab",
            "Bb": "Db",
            "Eb": "Gb",
            "Ab": "Cb",
        }
        tonic = relative_major.get(tonic, "C")

    return fifths_map_major.get(tonic, 0), mode


def _lyric_syllabic(lyric_mode: str) -> str | None:
    mapping = {
        "single": "single",
        "melisma_start": "begin",
        "melisma_continue": "middle",
        "tie_start": "begin",
        "tie_continue": "middle",
        "subdivision": "middle",
    }
    return mapping.get(lyric_mode)


def _lcm(a: int, b: int) -> int:
    return abs(a * b) // _gcd(a, b)


def _gcd(a: int, b: int) -> int:
    while b:
        a, b = b, a % b
    return abs(a)


def _escape_xml(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )

from __future__ import annotations

import logging
from dataclasses import dataclass
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


@dataclass
class _MusicUnitExportPlan:
    exported_measures: list[int]
    stacked_lyrics: dict[tuple[int, int], list[tuple[int, "ScoreNote"]]]
    headers_by_measure: dict[int, str]
    new_system_measures: set[int]


def export_musicxml(score: CanonicalScore) -> str:
    log_event(logger, "musicxml_render_started", measure_count=len(score.measures), stage=score.meta.stage)

    beats_i, beat_type_i = _parse_time_signature(score.meta.time_signature)
    divisions = _resolve_divisions(score)
    fifths, mode = _key_signature(score.meta.key)
    measure_duration = beats_i * divisions
    chords = {c.measure_number: c for c in score.chord_progression}
    breath_mark_positions = _collect_breath_mark_positions(score)

    arrangement_music_unit_lines = _arrangement_music_unit_comments(score)
    music_unit_plan = _build_music_unit_export_plan(score)

    lines: list[str] = [
        '<?xml version="1.0" encoding="UTF-8" standalone="no"?>',
        '<!DOCTYPE score-partwise PUBLIC "-//Recordare//DTD MusicXML 3.1 Partwise//EN"',
        '  "http://www.musicxml.org/dtds/partwise.dtd">',
        '<score-partwise version="3.1">',
        *arrangement_music_unit_lines,
        "  <part-list>",
        '    <score-part id="P1">',
        "      <part-name>Choir</part-name>",
        "    </score-part>",
        "  </part-list>",
        '  <part id="P1">',
    ]

    exported_measure_numbers = set(music_unit_plan.exported_measures)
    for measure in score.measures:
        if measure.number not in exported_measure_numbers:
            continue
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

        if measure.number in music_unit_plan.new_system_measures:
            lines.append('      <print new-system="yes"/>')

        if measure.number in music_unit_plan.headers_by_measure:
            header = music_unit_plan.headers_by_measure[measure.number]
            lines.append(
                "      <direction placement=\"above\"><direction-type>"
                f"<words>{_escape_xml(header)}</words>"
                "</direction-type></direction>"
            )

        if measure.number in chords:
            lines.extend(_harmony_xml(chords[measure.number]))

        lines.extend(
            _voice_measure_xml(
                score,
                measure.number,
                "soprano",
                1,
                1,
                divisions,
                breath_mark_positions,
                music_unit_plan.stacked_lyrics,
            )
        )
        lines.extend(_backup_xml(measure_duration))
        lines.extend(_voice_measure_xml(score, measure.number, "alto", 2, 1, divisions, set()))
        lines.extend(_backup_xml(measure_duration))
        lines.extend(_voice_measure_xml(score, measure.number, "tenor", 3, 2, divisions, set()))
        lines.extend(_backup_xml(measure_duration))
        lines.extend(_voice_measure_xml(score, measure.number, "bass", 4, 2, divisions, set()))

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


def _arrangement_music_unit_comments(score: CanonicalScore) -> list[str]:
    if not score.meta.arrangement_music_units:
        return []
    lines = ["  <!-- arrangement-music-units -->"]
    for music_unit in score.meta.arrangement_music_units:
        lines.append(
            "  <!-- arrangement_index="
            f"{music_unit.arrangement_index},music_unit_id={_escape_xml(music_unit.music_unit_id)},verse_index={music_unit.verse_index}"
            " -->"
        )
    return lines


def _voice_measure_xml(
    score: CanonicalScore,
    measure_number: int,
    voice_name: str,
    voice_number: int,
    staff_number: int,
    divisions: int,
    breath_mark_positions: set[tuple[int, int]],
    stacked_lyrics: dict[tuple[int, int], list[tuple[int, "ScoreNote"]]] | None = None,
) -> list[str]:
    measure = next((m for m in score.measures if m.number == measure_number), None)
    if not measure:
        return []

    lines: list[str] = []
    for note_index, note in enumerate(measure.voices[voice_name]):
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

        if voice_name == "soprano":
            lyric_entries = (stacked_lyrics or {}).get((measure_number, note_index))
            if lyric_entries:
                for verse_index, verse_note in lyric_entries:
                    lines.extend(_lyric_xml(verse_note, verse_index))
            else:
                lines.extend(_lyric_xml(note, 1))

        if (measure_number, note_index) in breath_mark_positions:
            lines.extend(
                [
                    "        <notations>",
                    "          <articulations>",
                    "            <breath-mark/>",
                    "          </articulations>",
                    "        </notations>",
                ]
            )

        lines.append("      </note>")
    return lines



def _collect_breath_mark_positions(score: CanonicalScore) -> set[tuple[int, int]]:
    breath_end_syllable_ids = {
        syllable.id
        for section in score.sections
        for syllable in section.syllables
        if syllable.breath_after_phrase
    }
    if not breath_end_syllable_ids:
        return set()

    last_note_position_by_syllable: dict[str, tuple[int, int]] = {}
    for measure in score.measures:
        for note_index, note in enumerate(measure.voices["soprano"]):
            if note.lyric_syllable_id in breath_end_syllable_ids:
                last_note_position_by_syllable[note.lyric_syllable_id] = (measure.number, note_index)

    return set(last_note_position_by_syllable.values())


def _build_music_unit_export_plan(score: CanonicalScore) -> _MusicUnitExportPlan:
    measure_numbers = [m.number for m in score.measures]
    if not score.meta.arrangement_music_units:
        return _MusicUnitExportPlan(
            exported_measures=measure_numbers,
            stacked_lyrics={},
            headers_by_measure={},
            new_system_measures={1} if measure_numbers else set(),
        )

    section_order = [section.id for section in score.sections]
    section_by_id = {section.id: section for section in score.sections}
    unit_by_section = {
        f"sec-{unit.arrangement_index + 1}": unit for unit in score.meta.arrangement_music_units if unit.arrangement_index >= 0
    }
    spans = _section_measure_spans(score)
    soprano_positions = _section_note_positions(score, "soprano")
    signatures = _section_structure_signatures(score)

    exported_sections: set[str] = set()
    music_unit_anchor: dict[str, str] = {}
    music_unit_verses: dict[str, list[tuple[int, str]]] = {}
    section_headers: dict[str, str] = {}

    for section_id in section_order:
        unit = unit_by_section.get(section_id)
        if unit is None:
            exported_sections.add(section_id)
            section_headers[section_id] = section_by_id.get(section_id).label if section_id in section_by_id else section_id
            continue

        anchor_section = music_unit_anchor.get(unit.music_unit_id)
        if anchor_section is None:
            music_unit_anchor[unit.music_unit_id] = section_id
            music_unit_verses[unit.music_unit_id] = [(unit.verse_index, section_id)]
            exported_sections.add(section_id)
            continue

        if signatures.get(anchor_section) == signatures.get(section_id):
            music_unit_verses[unit.music_unit_id].append((unit.verse_index, section_id))
            continue

        log_event(
            logger,
            "musicxml_verse_stacking_fallback",
            level=logging.WARNING,
            music_unit_id=unit.music_unit_id,
            anchor_section_id=anchor_section,
            fallback_section_id=section_id,
            reason="structure_mismatch_note_counts_or_syllable_mapping",
        )
        exported_sections.add(section_id)
        section_headers[section_id] = section_by_id.get(section_id).label if section_id in section_by_id else f"{unit.music_unit_id} Verse {unit.verse_index}"

    exported_measures = sorted({n for sid in exported_sections for n in spans.get(sid, [])})
    if not exported_measures:
        exported_measures = measure_numbers

    stacked_lyrics: dict[tuple[int, int], list[tuple[int, "ScoreNote"]]] = {}
    headers_by_measure: dict[int, str] = {}
    new_system_measures: set[int] = set()
    for music_unit_id, verses in music_unit_verses.items():
        ordered_verses = sorted(verses, key=lambda pair: pair[0])
        if not ordered_verses:
            continue
        anchor_section = ordered_verses[0][1]
        anchor_positions = soprano_positions.get(anchor_section, [])
        if not anchor_positions:
            continue

        first_measure = anchor_positions[0][0]
        new_system_measures.add(first_measure)
        if len(ordered_verses) > 1:
            verse_numbers = ", ".join(f"Verse {verse_index}" for verse_index, _ in ordered_verses)
            headers_by_measure[first_measure] = f"{music_unit_id} ({verse_numbers})"
        else:
            headers_by_measure[first_measure] = music_unit_id

        for note_slot, (measure_number, note_index, _anchor_note) in enumerate(anchor_positions):
            entries: list[tuple[int, "ScoreNote"]] = []
            for verse_index, section_id in ordered_verses:
                section_positions = soprano_positions.get(section_id, [])
                if note_slot >= len(section_positions):
                    continue
                entries.append((verse_index, section_positions[note_slot][2]))
            if entries:
                stacked_lyrics[(measure_number, note_index)] = entries

    for section_id, header in section_headers.items():
        section_measures = sorted(spans.get(section_id, []))
        if not section_measures:
            continue
        first_measure = section_measures[0]
        new_system_measures.add(first_measure)
        headers_by_measure.setdefault(first_measure, header)

    return _MusicUnitExportPlan(
        exported_measures=exported_measures,
        stacked_lyrics=stacked_lyrics,
        headers_by_measure=headers_by_measure,
        new_system_measures=new_system_measures,
    )


def _section_measure_spans(score: CanonicalScore) -> dict[str, set[int]]:
    spans: dict[str, set[int]] = {}
    for measure in score.measures:
        for note in measure.voices["soprano"]:
            spans.setdefault(note.section_id, set()).add(measure.number)
    return spans


def _section_note_positions(score: CanonicalScore, voice_name: str) -> dict[str, list[tuple[int, int, "ScoreNote"]]]:
    positions: dict[str, list[tuple[int, int, "ScoreNote"]]] = {}
    for measure in score.measures:
        for note_index, note in enumerate(measure.voices[voice_name]):
            positions.setdefault(note.section_id, []).append((measure.number, note_index, note))
    return positions


def _section_structure_signatures(score: CanonicalScore) -> dict[str, tuple]:
    signatures: dict[str, list[tuple]] = {}
    for measure in score.measures:
        for voice_name in ("soprano", "alto", "tenor", "bass"):
            for note in measure.voices[voice_name]:
                signatures.setdefault(note.section_id, []).append(
                    (voice_name, round(note.beats, 6), note.is_rest, note.lyric_mode)
                )
    return {section_id: tuple(signature) for section_id, signature in signatures.items()}


def _lyric_xml(note, verse_index: int) -> list[str]:
    should_emit = bool(note.lyric) or note.lyric_mode in {"melisma_start", "melisma_continue", "tie_start", "tie_continue"}
    if not should_emit:
        return []

    lines = [f"        <lyric number=\"{verse_index}\">"]
    syllabic = _lyric_syllabic(note.lyric_mode)
    if syllabic:
        lines.append(f"          <syllabic>{syllabic}</syllabic>")
    if note.lyric:
        lines.append(f"          <text>{_escape_xml(note.lyric)}</text>")
    if note.lyric_mode in {"melisma_start", "melisma_continue", "tie_start", "tie_continue"}:
        lines.append("          <extend/>")
    lines.append("        </lyric>")
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

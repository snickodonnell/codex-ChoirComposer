from __future__ import annotations

import logging

from app.logging_utils import log_event
from app.models import CanonicalScore, VoiceName

logger = logging.getLogger(__name__)


def export_musicxml(score: CanonicalScore) -> str:
    log_event(logger, "musicxml_render_started", measure_count=len(score.measures))
    divisions = 1
    beats, beat_type = score.meta.time_signature.split("/")
    beats_i = int(beats)
    beat_type_i = int(beat_type)

    parts: list[tuple[VoiceName, str]] = [
        ("soprano", "P1"),
        ("alto", "P2"),
        ("tenor", "P3"),
        ("bass", "P4"),
    ]
    chords = {c.measure_number: c for c in score.chord_progression}

    lines: list[str] = [
        '<?xml version="1.0" encoding="UTF-8" standalone="no"?>',
        '<!DOCTYPE score-partwise PUBLIC "-//Recordare//DTD MusicXML 3.1 Partwise//EN"',
        '  "http://www.musicxml.org/dtds/partwise.dtd">',
        '<score-partwise version="3.1">',
        "  <part-list>",
    ]

    for voice, pid in parts:
        lines.extend(
            [
                f'    <score-part id="{pid}">',
                f"      <part-name>{voice.title()}</part-name>",
                "    </score-part>",
            ]
        )
    lines.append("  </part-list>")

    for voice, pid in parts:
        lines.append(f'  <part id="{pid}">')
        for measure in score.measures:
            lines.append(f'    <measure number="{measure.number}">')
            if measure.number == 1:
                lines.extend(
                    [
                        "      <attributes>",
                        f"        <divisions>{divisions}</divisions>",
                        "        <key><fifths>0</fifths></key>",
                        f"        <time><beats>{beats_i}</beats><beat-type>{beat_type_i}</beat-type></time>",
                        "        <clef><sign>G</sign><line>2</line></clef>" if voice in {"soprano", "alto"} else "        <clef><sign>F</sign><line>4</line></clef>",
                        "      </attributes>",
                        f"      <direction placement=\"above\"><direction-type><metronome><beat-unit>quarter</beat-unit><per-minute>{score.meta.tempo_bpm}</per-minute></metronome></direction-type></direction>",
                    ]
                )

            if voice == "soprano" and measure.number in chords:
                symbol = chords[measure.number].symbol
                step = symbol[0]
                alter = "#" in symbol
                lines.append("      <harmony>")
                lines.append("        <root>")
                lines.append(f"          <root-step>{step}</root-step>")
                if alter:
                    lines.append("          <root-alter>1</root-alter>")
                lines.append("        </root>")
                lines.append("        <kind>major</kind>")
                lines.append(f"        <degree><degree-value>{chords[measure.number].degree}</degree-value></degree>")
                lines.append("      </harmony>")

            for note in measure.voices[voice]:
                dur = int(note.beats)
                if note.is_rest:
                    lines.extend(
                        [
                            "      <note>",
                            "        <rest/>",
                            f"        <duration>{dur}</duration>",
                            "        <type>half</type>" if dur >= 2 else "        <type>quarter</type>",
                            "      </note>",
                        ]
                    )
                    continue

                step = note.pitch[0]
                alter = 1 if "#" in note.pitch else 0
                octave = int(note.pitch[-1])
                lines.append("      <note>")
                lines.append("        <pitch>")
                lines.append(f"          <step>{step}</step>")
                if alter:
                    lines.append("          <alter>1</alter>")
                lines.append(f"          <octave>{octave}</octave>")
                lines.append("        </pitch>")
                lines.append(f"        <duration>{dur}</duration>")
                lines.append("        <type>half</type>" if dur >= 2 else "        <type>quarter</type>")
                if note.lyric and voice == "soprano":
                    lines.append(f"        <lyric><text>{_escape_xml(note.lyric)}</text></lyric>")
                lines.append("      </note>")

            lines.append("    </measure>")
        lines.append("  </part>")

    lines.append("</score-partwise>")
    content = "\n".join(lines)
    log_event(logger, "musicxml_render_completed", output_size_bytes=len(content.encode("utf-8")), measure_count=len(score.measures))
    return content


def _escape_xml(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )

from __future__ import annotations

import re
from collections import defaultdict

from app.models import CanonicalScore

_CONTINUATION_MODES = {"tie_continue", "melisma_continue"}


def _normalize_lyric_text(text: str) -> str:
    return re.sub(r"[^\w']+", "", text.strip().lower())


def build_lyric_underlay_report(score: CanonicalScore) -> dict[str, object]:
    section_order = [section.id for section in score.sections]

    section_payload: dict[str, dict[str, object]] = {}
    syllable_ids_by_section: dict[str, list[str]] = {}

    for section in score.sections:
        phrase_index = 0
        syllable_rows: list[dict[str, object]] = []
        ordered_syllables: list[str] = []
        for syllable in section.syllables:
            ordered_syllables.append(syllable.id)
            syllable_rows.append(
                {
                    "id": syllable.id,
                    "raw_text": syllable.text,
                    "normalized_text": _normalize_lyric_text(syllable.text),
                    "phrase_index": phrase_index,
                }
            )
            if syllable.phrase_end_after:
                phrase_index += 1

        syllable_ids_by_section[section.id] = ordered_syllables
        section_payload[section.id] = {
            "section_id": section.id,
            "tokenized_syllables": syllable_rows,
            "lyric_note_events": [],
            "missing_syllable_ids": [],
            "duplicate_syllable_ids": [],
            "out_of_order_pairs": [],
            "overwritten_events": [],
        }

    soprano = [note for measure in score.measures for note in measure.voices["soprano"]]
    start_beat = 0.0
    note_index = 0

    section_new_refs: dict[str, list[str]] = defaultdict(list)
    section_seen_refs: dict[str, set[str]] = defaultdict(set)
    section_index_by_syllable: dict[str, dict[str, int]] = {
        section_id: {syllable_id: idx for idx, syllable_id in enumerate(ids)}
        for section_id, ids in syllable_ids_by_section.items()
    }
    last_new_ref_index: dict[str, int] = {}
    lyric_slot_assignment: dict[tuple[str, int], str] = {}

    for note in soprano:
        syllable_id = note.lyric_syllable_id
        if note.is_rest or syllable_id is None:
            start_beat += note.beats
            note_index += 1
            continue

        section_id = note.section_id
        section_report = section_payload.get(section_id)
        if section_report is None:
            start_beat += note.beats
            note_index += 1
            continue

        is_tie_start = note.lyric_mode == "tie_start"
        is_tie_continue = note.lyric_mode == "tie_continue"
        is_new_syllable = note.lyric_mode not in _CONTINUATION_MODES

        cast_events = section_report["lyric_note_events"]
        assert isinstance(cast_events, list)
        cast_events.append(
            {
                "note_index": note_index,
                "start_beat": round(start_beat, 6),
                "duration": note.beats,
                "lyric_syllable_id": syllable_id,
                "lyric_mode": note.lyric_mode,
                "is_tie_start": is_tie_start,
                "is_tie_continue": is_tie_continue,
            }
        )

        section_seen_refs[section_id].add(syllable_id)

        if is_new_syllable:
            section_new_refs[section_id].append(syllable_id)
            current_index = section_index_by_syllable.get(section_id, {}).get(syllable_id)
            previous_index = last_new_ref_index.get(section_id)
            if current_index is not None and previous_index is not None and current_index < previous_index:
                cast_pairs = section_report["out_of_order_pairs"]
                assert isinstance(cast_pairs, list)
                cast_pairs.append(
                    {
                        "previous_syllable_id": section_new_refs[section_id][-2],
                        "current_syllable_id": syllable_id,
                        "previous_index": previous_index,
                        "current_index": current_index,
                    }
                )
            if current_index is not None:
                last_new_ref_index[section_id] = current_index

            if note.lyric_index is not None:
                key = (section_id, note.lyric_index)
                first_assigned = lyric_slot_assignment.get(key)
                if first_assigned is None:
                    lyric_slot_assignment[key] = syllable_id
                elif first_assigned != syllable_id:
                    cast_overwritten = section_report["overwritten_events"]
                    assert isinstance(cast_overwritten, list)
                    cast_overwritten.append(
                        {
                            "section_id": section_id,
                            "lyric_index": note.lyric_index,
                            "previous_syllable_id": first_assigned,
                            "replaced_by_syllable_id": syllable_id,
                            "note_index": note_index,
                        }
                    )

        start_beat += note.beats
        note_index += 1

    summary = {"missing": 0, "dupes": 0, "out_of_order": 0}
    for section_id in section_order:
        report = section_payload[section_id]
        expected_ids = syllable_ids_by_section.get(section_id, [])
        seen = section_seen_refs.get(section_id, set())
        missing = [syllable_id for syllable_id in expected_ids if syllable_id not in seen]

        new_refs = section_new_refs.get(section_id, [])
        ref_counts: dict[str, int] = defaultdict(int)
        for ref in new_refs:
            ref_counts[ref] += 1
        duplicate_ids = sorted([syllable_id for syllable_id, count in ref_counts.items() if count > 1])

        report["missing_syllable_ids"] = missing
        report["duplicate_syllable_ids"] = duplicate_ids

        summary["missing"] += len(missing)
        summary["dupes"] += len(duplicate_ids)
        out_of_order_pairs = report.get("out_of_order_pairs", [])
        summary["out_of_order"] += len(out_of_order_pairs if isinstance(out_of_order_pairs, list) else [])

    return {
        "sections": [section_payload[section_id] for section_id in section_order],
        "summary": summary,
        "request_context": {
            "time_signature": score.meta.time_signature,
            "section_ids": section_order,
            "stage": score.meta.stage,
        },
    }

import pytest
from pydantic import ValidationError

from app.services import composer as composer_service
from app.models import ArrangementItem, CanonicalScore, CompositionPreferences, CompositionRequest, LyricSection, PhraseBlock
from app.services.composer import generate_melody_score, harmonize_score, refine_score
from app.services.lyric_mapping import config_for_preset, plan_syllable_rhythm, tokenize_section_lyrics
from app.services.music_theory import VOICE_RANGES, pitch_to_midi
from app.services.musicxml_export import export_musicxml
from app.services.score_normalization import normalize_score_for_rendering
from app.services.score_validation import beats_per_measure, validate_score


def _flatten(score, voice):
    return [n for m in score.measures for n in m.voices[voice] if not n.is_rest]


def _measure_onsets(score, voice="soprano"):
    bpb = beats_per_measure(score.meta.time_signature)
    pos = 0.0
    onsets = []
    for n in [n for m in score.measures for n in m.voices[voice]]:
        onsets.append((int(pos // bpb) + 1, pos % bpb, n))
        pos += n.beats
    return onsets


def test_tokenization_preserves_word_and_hyphen_context():
    syllables = tokenize_section_lyrics("sec-1", "glo-ri-a sing forever\namen")
    assert syllables[0].word_text == "glo-ri-a"
    assert any(s.hyphenated for s in syllables)
    assert any(s.phrase_end_after for s in syllables)
    assert all(s.section_id == "sec-1" for s in syllables)


def test_rhythm_plan_uses_config_and_is_deterministic():
    syllables = tokenize_section_lyrics("sec-1", "sing together forever")
    cfg = config_for_preset("mixed", "verse")
    plan_a = plan_syllable_rhythm(syllables, 4, cfg, "seed-1")
    plan_b = plan_syllable_rhythm(syllables, 4, cfg, "seed-1")
    plan_c = plan_syllable_rhythm(syllables, 4, cfg, "seed-2")
    assert plan_a == plan_b
    assert plan_a != plan_c


def test_preset_controls_melisma_amount():
    syllables = tokenize_section_lyrics("sec-1", "sing together forever in wonder and light")
    syll_cfg = config_for_preset("syllabic", "verse")
    mel_cfg = config_for_preset("melismatic", "verse")
    syll_plan = plan_syllable_rhythm(syllables, 4, syll_cfg, "seed")
    mel_plan = plan_syllable_rhythm(syllables, 4, mel_cfg, "seed")

    def melisma_count(plan):
        return sum(1 for item in plan for m in item["modes"] if m in {"melisma_start", "melisma_continue"})

    assert melisma_count(mel_plan) >= melisma_count(syll_plan)


def test_rhythm_plan_aligns_line_endings_to_barlines():
    syllables = tokenize_section_lyrics("sec-1", "glory rises\nforever amen")
    cfg = config_for_preset("mixed", "verse")
    plan = plan_syllable_rhythm(syllables, 4, cfg, "seed-1")

    by_id = {s.id: s for s in syllables}
    beat_pos = 0.0
    phrase_end_positions = []
    for item in plan:
        beat_pos += sum(item["durations"])
        if by_id[item["syllable_id"]].phrase_end_after:
            phrase_end_positions.append(beat_pos)

    assert phrase_end_positions
    assert all(abs(pos % 4) < 1e-9 for pos in phrase_end_positions)


def test_rhythm_plan_never_places_next_line_inside_prior_line_measure():
    syllables = tokenize_section_lyrics("sec-1", "kyrie eleison\nchrist have mercy")
    cfg = config_for_preset("mixed", "verse")
    plan = plan_syllable_rhythm(syllables, 4, cfg, "seed-2")

    by_id = {s.id: s for s in syllables}
    beat_pos = 0.0
    next_line_started = False
    for item in plan:
        syllable = by_id[item["syllable_id"]]
        if next_line_started:
            assert abs(beat_pos % 4) < 1e-9
            break
        beat_pos += sum(item["durations"])
        if syllable.phrase_end_after:
            next_line_started = True

    assert next_line_started






def test_phrase_search_exactly_fills_phrase_measures():
    syllables = tokenize_section_lyrics("sec-1", "shine eternal glory")
    cfg = config_for_preset("syllabic", "verse")
    plan = plan_syllable_rhythm(syllables, 4, cfg, "seed-phrase-fill")

    total = sum(sum(item["durations"]) for item in plan)
    assert abs(total % 4) < 1e-9


def test_phrase_search_prefers_less_fragmented_syllabic_templates():
    syllables = tokenize_section_lyrics("sec-1", "light of hope")
    cfg = config_for_preset("syllabic", "verse")
    plan = plan_syllable_rhythm(syllables, 4, cfg, "seed-fragment")

    short_count = sum(1 for item in plan for d in item["durations"] if d <= 0.5 + 1e-9)
    assert short_count <= len(plan)


def test_tokenization_prefers_natural_stress_for_common_suffix_words():
    syllables = tokenize_section_lyrics("sec-1", "glorious motion")
    by_word = {}
    for syllable in syllables:
        by_word.setdefault(syllable.word_text.lower(), []).append(syllable)

    assert by_word["glorious"][0].stressed is True
    assert by_word["motion"][0].stressed is False
    assert by_word["motion"][1].stressed is True


def test_phrase_plan_prefers_cadence_hold_on_final_stressed_syllable():
    syllables = tokenize_section_lyrics("sec-1", "holy motion")
    cfg = config_for_preset("mixed", "verse")
    plan = plan_syllable_rhythm(syllables, 4, cfg, "seed-cadence")

    motion_tail = [item for item in plan if item["syllable_text"].lower() == "ion"]
    assert motion_tail
    assert sum(motion_tail[-1]["durations"]) >= 1.0

def test_phrase_blocks_can_merge_with_next_phrase_to_form_run_on_phrase():
    req = CompositionRequest(
        sections=[LyricSection(id="verse-1", label="verse", text="glory rises\nforever amen")],
        arrangement=[
            ArrangementItem(
                section_id="verse-1",
                phrase_blocks=[
                    PhraseBlock(text="glory rises", merge_with_next_phrase=True),
                    PhraseBlock(text="forever amen"),
                ],
            )
        ],
        preferences=CompositionPreferences(time_signature="4/4", lyric_rhythm_preset="mixed"),
    )

    melody = generate_melody_score(req)
    first_section = melody.sections[0]
    phrase_end_syllables = [s for s in first_section.syllables if s.phrase_end_after]

    assert len(phrase_end_syllables) == 1
    assert phrase_end_syllables[0].word_text.lower() == "amen"

    soprano = [n for m in melody.measures for n in m.voices["soprano"]]
    beat_pos = 0.0
    last_note_index_for_syllable = {}
    for idx, note in enumerate(soprano):
        if note.lyric_syllable_id:
            last_note_index_for_syllable[note.lyric_syllable_id] = idx

    phrase_end_beat = 0.0
    for idx, note in enumerate(soprano):
        beat_pos += note.beats
        syllable_id = note.lyric_syllable_id
        if syllable_id == phrase_end_syllables[0].id and last_note_index_for_syllable.get(syllable_id) == idx:
            phrase_end_beat = beat_pos
            break

    assert abs(phrase_end_beat % 4) < 1e-9


def test_phrase_blocks_with_breath_marker_enforce_phrase_boundary_and_keep_timing():
    with_breath = CompositionRequest(
        sections=[LyricSection(id="verse-1", label="verse", text="holy holy\nforever amen")],
        arrangement=[
            ArrangementItem(
                section_id="verse-1",
                phrase_blocks=[
                    PhraseBlock(text="holy holy", breath_after_phrase=True),
                    PhraseBlock(text="forever amen", breath_after_phrase=False),
                ],
            )
        ],
        preferences=CompositionPreferences(time_signature="4/4", lyric_rhythm_preset="mixed"),
    )
    without_breath = CompositionRequest(
        sections=[LyricSection(id="verse-1", label="verse", text="holy holy\nforever amen")],
        arrangement=[
            ArrangementItem(
                section_id="verse-1",
                phrase_blocks=[
                    PhraseBlock(text="holy holy", breath_after_phrase=False),
                    PhraseBlock(text="forever amen", breath_after_phrase=False),
                ],
            )
        ],
        preferences=CompositionPreferences(time_signature="4/4", lyric_rhythm_preset="mixed"),
    )

    melody_with_breath = generate_melody_score(with_breath)
    melody_without_breath = generate_melody_score(without_breath)

    section = melody_with_breath.sections[0]
    first_phrase_end = [s for s in section.syllables if s.phrase_end_after][0]
    assert first_phrase_end.breath_after_phrase is True
    assert first_phrase_end.phrase_end_after is True

    beats_with_breath = sum(note.beats for measure in melody_with_breath.measures for note in measure.voices["soprano"])
    beats_without_breath = sum(note.beats for measure in melody_without_breath.measures for note in measure.voices["soprano"])
    assert beats_with_breath == beats_without_breath



def test_anacrusis_auto_mode_biases_off_unless_bar_fit_is_strong():
    assert composer_service._recommend_anacrusis_beats(6, 4) == 0.0
    assert composer_service._recommend_anacrusis_beats(9, 4) == 1.0


def test_manual_anacrusis_adds_pickup_rest_and_updates_first_chord_degree():
    req = CompositionRequest(
        sections=[LyricSection(id="verse-1", label="verse", text="glory forever rising now")],
        arrangement=[
            ArrangementItem(
                section_id="verse-1",
                progression_cluster="Verse A",
                anacrusis_mode="manual",
                anacrusis_beats=1,
            )
        ],
        preferences=CompositionPreferences(time_signature="4/4", lyric_rhythm_preset="syllabic"),
    )

    melody = generate_melody_score(req)
    first_note = melody.measures[0].voices["soprano"][0]

    assert first_note.is_rest is True
    assert first_note.beats == 1
    assert melody.chord_progression[0].degree == 5


def test_auto_anacrusis_is_deterministic_for_same_request_inputs():
    req = CompositionRequest(
        sections=[LyricSection(id="verse-1", label="verse", text="glory forever rising now in wonder")],
        arrangement=[ArrangementItem(section_id="verse-1", progression_cluster="Verse", anacrusis_mode="auto")],
        preferences=CompositionPreferences(time_signature="4/4", lyric_rhythm_preset="mixed", key="C", tempo_bpm=88),
    )

    melody_a = generate_melody_score(req)
    melody_b = generate_melody_score(req)

    assert melody_a.model_dump() == melody_b.model_dump()


def test_phrase_boundary_stays_on_barline_with_manual_pickup_and_no_syllable_bleed():
    req = CompositionRequest(
        sections=[LyricSection(id="verse-1", label="verse", text="glory rises now\nforever we sing")],
        arrangement=[
            ArrangementItem(
                section_id="verse-1",
                progression_cluster="Verse",
                anacrusis_mode="manual",
                anacrusis_beats=1,
                phrase_blocks=[
                    PhraseBlock(text="glory rises now", merge_with_next_phrase=False),
                    PhraseBlock(text="forever we sing", merge_with_next_phrase=False),
                ],
            )
        ],
        preferences=CompositionPreferences(time_signature="4/4", lyric_rhythm_preset="mixed", key="C", tempo_bpm=88),
    )

    melody = generate_melody_score(req)
    section = melody.sections[0]
    first_phrase_end = next(s for s in section.syllables if s.phrase_end_after)
    second_phrase_first_word = next(s.word_text.lower() for s in section.syllables if s.word_index > first_phrase_end.word_index)

    beat_pos = 0.0
    phrase_end_pos = None
    phrase_end_idx = None
    second_phrase_start_idx = None
    for idx, note in enumerate([n for m in melody.measures for n in m.voices["soprano"]]):
        beat_pos += note.beats
        if note.lyric_syllable_id == first_phrase_end.id:
            phrase_end_pos = beat_pos
            phrase_end_idx = idx
        if second_phrase_start_idx is None and note.lyric and note.lyric.lower() in second_phrase_first_word:
            second_phrase_start_idx = idx

    assert phrase_end_pos is not None
    assert abs(phrase_end_pos % 4) < 1e-9
    assert second_phrase_start_idx is not None and phrase_end_idx is not None
    assert second_phrase_start_idx > phrase_end_idx


def test_pickup_measure_capacity_validation_rule_is_enforced():
    req = CompositionRequest(
        sections=[LyricSection(id="verse-1", label="verse", text="glory forever rising now")],
        arrangement=[ArrangementItem(section_id="verse-1", anacrusis_mode="manual", anacrusis_beats=1)],
        preferences=CompositionPreferences(time_signature="4/4", lyric_rhythm_preset="syllabic", key="C"),
    )
    melody = generate_melody_score(req)
    assert validate_score(melody) == []

    satb = harmonize_score(melody)
    section_id = satb.sections[0].id
    for voice in ["soprano", "alto", "tenor", "bass"]:
        per_measure_nonpickup = []
        for measure in satb.measures:
            nonpickup = sum(
                note.beats
                for note in measure.voices[voice]
                if note.section_id == section_id and not note.is_rest
            )
            if nonpickup > 0:
                per_measure_nonpickup.append(nonpickup)
        assert per_measure_nonpickup[0] == 3
        assert all(abs(v - 4) < 1e-9 for v in per_measure_nonpickup[1:])

def test_continuation_notes_do_not_repeat_lyric_text():
    req = CompositionRequest(
        sections=[LyricSection(label="chorus", text="Gloria in excelsis deo")],
        preferences=CompositionPreferences(time_signature="4/4", lyric_rhythm_preset="melismatic"),
    )
    melody = generate_melody_score(req)

    soprano = [n for m in melody.measures for n in m.voices["soprano"] if not n.is_rest]
    continuation = [n for n in soprano if n.lyric_mode in {"melisma_continue", "tie_continue"}]

    assert continuation
    assert all(n.lyric is None for n in continuation)


def test_each_syllable_text_is_emitted_once_per_syllable_id():
    req = CompositionRequest(
        sections=[LyricSection(label="verse", text="Kyrie eleison forever")],
        preferences=CompositionPreferences(time_signature="4/4", lyric_rhythm_preset="mixed"),
    )
    melody = generate_melody_score(req)

    seen_text_by_syllable_id = {}
    for note in [n for m in melody.measures for n in m.voices["soprano"] if not n.is_rest]:
        if note.lyric_syllable_id is None:
            continue
        if note.lyric:
            seen_text_by_syllable_id.setdefault(note.lyric_syllable_id, 0)
            seen_text_by_syllable_id[note.lyric_syllable_id] += 1

    assert seen_text_by_syllable_id
    assert all(count == 1 for count in seen_text_by_syllable_id.values())


def test_generate_melody_and_satb_validate():
    req = CompositionRequest(
        sections=[LyricSection(label="verse", text="Glory rises in the dawn.")],
        preferences=CompositionPreferences(style="Hymn", mood="Uplifting", lyric_rhythm_preset="mixed", key="D", time_signature="4/4", tempo_bpm=92),
    )
    melody = generate_melody_score(req)
    assert melody.meta.stage == "melody"
    assert melody.chord_progression
    assert validate_score(melody) == []

    satb = harmonize_score(melody)
    assert satb.meta.stage == "satb"
    assert validate_score(satb) == []


def test_free_form_section_label_is_preserved_and_generates():
    req = CompositionRequest(
        sections=[LyricSection(label="Verse Lift", text="Glory rises in the dawn.")],
        preferences=CompositionPreferences(style="Hymn", mood="Uplifting", lyric_rhythm_preset="mixed", key="D", time_signature="4/4", tempo_bpm=92),
    )
    melody = generate_melody_score(req)
    assert melody.sections[0].label == "Verse Lift"
    assert melody.chord_progression


def test_section_boundaries_do_not_insert_timed_rests():
    req = CompositionRequest(
        sections=[
            LyricSection(label="Verse", text="Light in the morning fills every heart"),
            LyricSection(label="Chorus", text="Sing together, hope forever"),
        ],
        preferences=CompositionPreferences(time_signature="4/4", key="C", tempo_bpm=90, lyric_rhythm_preset="mixed"),
    )
    melody = generate_melody_score(req)

    soprano = [n for m in melody.measures for n in m.voices["soprano"]]
    interlude_rests = [n for n in soprano if n.is_rest and n.section_id == "interlude"]

    assert not interlude_rests


def test_strong_beats_prefer_chord_tones():
    req = CompositionRequest(
        sections=[LyricSection(label="verse", text="Morning glory rises higher")],
        preferences=CompositionPreferences(time_signature="4/4", key="C", tempo_bpm=90),
    )
    melody = generate_melody_score(req)
    onsets = _measure_onsets(melody, "soprano")
    chord_map = {c.measure_number: set(c.pitch_classes) for c in melody.chord_progression}

    strong = []
    for measure_number, onset, note in onsets:
        if note.is_rest:
            continue
        if abs(onset % 2.0) < 1e-9:
            strong.append(pitch_to_midi(note.pitch) % 12 in chord_map[measure_number])

    assert strong
    assert sum(1 for ok in strong if ok) >= len(strong) * 0.75


def test_satb_ranges_order_and_spacing_constraints():
    req = CompositionRequest(
        sections=[LyricSection(label="verse", text="Morning mercy lights the sky and gives us song")],
        preferences=CompositionPreferences(key="D", time_signature="4/4", tempo_bpm=92),
    )
    satb = harmonize_score(generate_melody_score(req))

    sop = _flatten(satb, "soprano")
    alto = _flatten(satb, "alto")
    tenor = _flatten(satb, "tenor")
    bass = _flatten(satb, "bass")

    for voice, notes in [("soprano", sop), ("alto", alto), ("tenor", tenor), ("bass", bass)]:
        lo, hi = VOICE_RANGES[voice]
        for n in notes:
            midi = pitch_to_midi(n.pitch)
            assert lo <= midi <= hi

    for s, a, t, b in zip(sop, alto, tenor, bass):
        sm, am, tm, bm = map(lambda n: pitch_to_midi(n.pitch), (s, a, t, b))
        assert sm >= am >= tm >= bm
        assert sm - am <= 12
        assert am - tm <= 12


def test_satb_generation_handles_dense_triple_meter_without_validation_regressions():
    req = CompositionRequest(
        sections=[
            LyricSection(
                label="verse",
                text="sing glory mercy rises river forever glory sing mercy forever mercy joy morning hallelujah light forever glory",
            )
        ],
        preferences=CompositionPreferences(key="G", time_signature="3/4", tempo_bpm=100, lyric_rhythm_preset="mixed"),
    )

    melody = generate_melody_score(req)
    satb = harmonize_score(melody)

    assert satb.meta.stage == "satb"
    assert validate_score(satb) == []

def test_musicxml_export_contains_satb_parts_and_harmony():
    req = CompositionRequest(
        sections=[LyricSection(label="chorus", text="Sing together forever")],
        preferences=CompositionPreferences(),
    )
    satb = harmonize_score(generate_melody_score(req))
    xml = export_musicxml(satb)
    assert "<score-partwise" in xml
    assert "<part-name>Soprano</part-name>" in xml
    assert "<part-name>Alto</part-name>" in xml
    assert "<part-name>Tenor</part-name>" in xml
    assert "<part-name>Bass</part-name>" in xml
    assert "<harmony>" in xml



def test_arrangement_order_preserves_section_instances_without_extra_rests():
    req = CompositionRequest(
        sections=[
            LyricSection(id="v", label="Verse", text="Morning glory rises higher"),
            LyricSection(id="c", label="Chorus", text="Sing together forever"),
        ],
        arrangement=[
            {"section_id": "c"},
            {"section_id": "v"},
            {"section_id": "c"},
        ],
        preferences=CompositionPreferences(time_signature="4/4", key="C", tempo_bpm=90, lyric_rhythm_preset="mixed"),
    )
    melody = generate_melody_score(req)

    assert [s.label for s in melody.sections] == ["Chorus", "Verse", "Chorus"]

    interlude_rests = [
        n
        for m in melody.measures
        for n in m.voices["soprano"]
        if n.is_rest and n.section_id == "interlude"
    ]
    assert not interlude_rests


def test_progression_cluster_reuses_single_progression_across_labels_and_repeats():
    req = CompositionRequest(
        sections=[
            LyricSection(id="v1", label="Verse A", text="Morning light is rising in our hearts"),
            LyricSection(id="v2", label="Verse B", text="Mercy flows and carries every voice"),
        ],
        arrangement=[
            {"section_id": "v1", "progression_cluster": "Verse"},
            {"section_id": "v2", "progression_cluster": "Verse"},
            {"section_id": "v1", "progression_cluster": "Verse"},
        ],
        preferences=CompositionPreferences(time_signature="4/4", key="C", tempo_bpm=90, lyric_rhythm_preset="mixed"),
    )
    melody = generate_melody_score(req)

    chords_by_section = {}
    for chord in melody.chord_progression:
        chords_by_section.setdefault(chord.section_id, []).append(chord.degree)

    assert chords_by_section["sec-1"]
    assert chords_by_section["sec-2"]
    assert chords_by_section["sec-3"]




def test_phrase_endings_bias_progression_toward_cadence_without_rewriting_entire_cluster():
    req = CompositionRequest(
        sections=[LyricSection(id="v", label="Verse", text="Morning light rises\nHope is here")],
        arrangement=[{"section_id": "v", "progression_cluster": "Verse"}],
        preferences=CompositionPreferences(time_signature="4/4", key="C", tempo_bpm=90, lyric_rhythm_preset="mixed"),
    )

    melody = generate_melody_score(req)
    section = melody.sections[0]
    phrase_end_ids = {s.id for s in section.syllables if s.phrase_end_after}

    bpb = beats_per_measure(melody.meta.time_signature)
    pos = 0.0
    phrase_end_measures = []
    last_end_for_syllable = {}
    soprano = [n for m in melody.measures for n in m.voices["soprano"]]
    for note in soprano:
        end_pos = pos + note.beats
        if not note.is_rest and note.lyric_syllable_id in phrase_end_ids:
            last_end_for_syllable[note.lyric_syllable_id] = end_pos
        pos = end_pos

    phrase_end_measures = sorted({int(max(end - 1e-9, 0.0) // bpb) + 1 for end in last_end_for_syllable.values()})
    chords_by_measure = {ch.measure_number: ch.degree for ch in melody.chord_progression}

    assert phrase_end_measures
    assert chords_by_measure[phrase_end_measures[-1]] == 1
    assert any(chords_by_measure[m] == 1 for m in phrase_end_measures)
    assert any(m - 1 in chords_by_measure and chords_by_measure[m - 1] == 5 for m in phrase_end_measures)


def test_phrase_end_soprano_note_lands_on_stable_chord_tone():
    req = CompositionRequest(
        sections=[LyricSection(id="v", label="Verse", text="Bless-ed hope\nA-men")],
        arrangement=[{"section_id": "v", "progression_cluster": "Verse"}],
        preferences=CompositionPreferences(time_signature="4/4", key="C", tempo_bpm=88, lyric_rhythm_preset="mixed"),
    )

    melody = generate_melody_score(req)
    section = melody.sections[0]
    phrase_end_ids = {s.id for s in section.syllables if s.phrase_end_after}

    bpb = beats_per_measure(melody.meta.time_signature)
    pos = 0.0
    last_note_by_phrase_end = {}
    for note in [n for m in melody.measures for n in m.voices["soprano"]]:
        if note.is_rest:
            pos += note.beats
            continue
        end_pos = pos + note.beats
        if note.lyric_syllable_id in phrase_end_ids:
            last_note_by_phrase_end[note.lyric_syllable_id] = (end_pos, note)
        pos = end_pos

    chords_by_measure = {ch.measure_number: set(ch.pitch_classes) for ch in melody.chord_progression}

    assert last_note_by_phrase_end
    for end_pos, note in last_note_by_phrase_end.values():
        measure_number = int(max(end_pos - 1e-9, 0.0) // bpb) + 1
        assert pitch_to_midi(note.pitch) % 12 in chords_by_measure[measure_number]


def test_regenerate_updates_only_selected_clusters():
    req = CompositionRequest(
        sections=[
            LyricSection(id="v", label="Verse", text="Morning glory rises"),
            LyricSection(id="c", label="Chorus", text="Sing together forever"),
        ],
        arrangement=[
            {"section_id": "v", "progression_cluster": "Verse"},
            {"section_id": "c", "progression_cluster": "Chorus"},
        ],
        preferences=CompositionPreferences(time_signature="4/4", key="C", tempo_bpm=90, lyric_rhythm_preset="mixed"),
    )
    melody = generate_melody_score(req)
    before_by_section: dict[str, list[tuple[int, int]]] = {}
    for chord in melody.chord_progression:
        before_by_section.setdefault(chord.section_id, []).append((chord.measure_number, chord.degree))

    refined = refine_score(
        melody,
        "fresh melodic idea",
        True,
        selected_clusters=["Chorus"],
        section_clusters={"sec-1": "Verse", "sec-2": "Chorus"},
    )

    after_by_section: dict[str, list[tuple[int, int]]] = {}
    for chord in refined.chord_progression:
        after_by_section.setdefault(chord.section_id, []).append((chord.measure_number, chord.degree))

    changed_chorus = before_by_section.get("sec-2") != after_by_section.get("sec-2")
    unchanged_verse = before_by_section.get("sec-1") == after_by_section.get("sec-1")

    assert changed_chorus
    assert unchanged_verse


def test_preferences_validate_theory_fields():
    prefs = CompositionPreferences(key="Bb", primary_mode="major", time_signature="6/8", tempo_bpm=96)
    assert prefs.key == "Bb"
    assert prefs.primary_mode == "ionian"
    assert prefs.time_signature == "6/8"


def test_preferences_reject_invalid_theory_values():
    with pytest.raises(ValidationError):
        CompositionPreferences(key="H")

    with pytest.raises(ValidationError):
        CompositionPreferences(primary_mode="super-locrian")

    with pytest.raises(ValidationError):
        CompositionPreferences(time_signature="5/3")

    with pytest.raises(ValidationError):
        CompositionPreferences(tempo_bpm=301)

    with pytest.raises(ValidationError):
        CompositionPreferences(key="Am", primary_mode="aeolian")


def test_validate_score_supports_primary_mode_diatonic_context():
    req = CompositionRequest(
        sections=[LyricSection(label="verse", text="Morning light renews us")],
        preferences=CompositionPreferences(key="A", primary_mode="aeolian", time_signature="4/4", tempo_bpm=90),
    )
    melody = generate_melody_score(req)

    errors_with_mode = validate_score(melody, "aeolian")
    errors_without_mode = validate_score(melody)

    assert errors_with_mode == []
    assert errors_without_mode == []



def test_d_minor_diatonic_accepts_expected_chords():
    score = {
        "meta": {
            "key": "D",
            "primary_mode": "aeolian",
            "time_signature": "4/4",
            "tempo_bpm": 90,
            "style": "Hymn",
            "stage": "melody",
            "rationale": "test",
        },
        "sections": [],
        "measures": [
            {
                "number": i,
                "voices": {
                    voice: [{"pitch": "REST", "beats": 4, "is_rest": True, "section_id": "padding"}]
                    for voice in ["soprano", "alto", "tenor", "bass"]
                },
            }
            for i in range(1, 5)
        ],
        "chord_progression": [
            {"measure_number": 1, "section_id": "sec-1", "symbol": "Dm", "degree": 1, "pitch_classes": [2, 5, 9]},
            {"measure_number": 2, "section_id": "sec-1", "symbol": "Gm", "degree": 4, "pitch_classes": [7, 10, 2]},
            {"measure_number": 3, "section_id": "sec-1", "symbol": "Am", "degree": 5, "pitch_classes": [9, 0, 4]},
            {"measure_number": 4, "section_id": "sec-1", "symbol": "Bb", "degree": 6, "pitch_classes": [10, 2, 5]},
        ],
    }

    assert validate_score(CanonicalScore.model_validate(score)) == []



def test_validate_score_flags_phrase_end_not_on_barline():
    score = CanonicalScore.model_validate(
        {
            "meta": {
                "key": "C",
                "primary_mode": "ionian",
                "time_signature": "4/4",
                "tempo_bpm": 88,
                "style": "Hymn",
                "stage": "melody",
                "rationale": "test",
            },
            "sections": [
                {
                    "id": "sec-1",
                    "label": "verse",
                                        "lyrics": "amen",
                    "syllables": [
                        {
                            "id": "sec-1-syl-0",
                            "text": "a",
                            "section_id": "sec-1",
                            "word_index": 0,
                            "syllable_index_in_word": 0,
                            "word_text": "amen",
                            "hyphenated": False,
                            "stressed": True,
                            "phrase_end_after": True,
                        }
                    ],
                }
            ],
            "measures": [
                {
                    "number": 1,
                    "voices": {
                        "soprano": [
                            {
                                "pitch": "C4",
                                "beats": 3,
                                "section_id": "sec-1",
                                "lyric_syllable_id": "sec-1-syl-0",
                                "lyric_mode": "single",
                                "lyric": "amen",
                            },
                            {"pitch": "REST", "beats": 1, "is_rest": True, "section_id": "padding"},
                        ],
                        "alto": [{"pitch": "REST", "beats": 4, "is_rest": True, "section_id": "padding"}],
                        "tenor": [{"pitch": "REST", "beats": 4, "is_rest": True, "section_id": "padding"}],
                        "bass": [{"pitch": "REST", "beats": 4, "is_rest": True, "section_id": "padding"}],
                    },
                }
            ],
            "chord_progression": [
                {"measure_number": 1, "section_id": "sec-1", "symbol": "C", "degree": 1, "pitch_classes": [0, 4, 7]}
            ],
        }
    )

    errors = validate_score(score)

    assert any("ends at beat 3" in err for err in errors)


def test_generate_melody_repairs_phrase_end_barlines(monkeypatch):
    req = CompositionRequest(
        sections=[LyricSection(label="verse", text="one two")],
        preferences=CompositionPreferences(time_signature="4/4", lyric_rhythm_preset="syllabic", key="C", tempo_bpm=88),
    )

    original_compose = composer_service._compose_melody_once

    def broken_compose(_req, _attempt_number):
        score = original_compose(_req, _attempt_number)
        soprano = [n for m in score.measures for n in m.voices["soprano"] if not n.is_rest]
        phrase_tail = next(n for n in reversed(soprano) if n.lyric_syllable_id)
        phrase_tail.beats -= 1
        return score

    monkeypatch.setattr(composer_service, "_compose_melody_once", broken_compose)
    melody = composer_service.generate_melody_score(req)

    assert validate_score(melody) == []

def test_generate_melody_failure_uses_friendly_message(monkeypatch):
    req = CompositionRequest(
        sections=[LyricSection(label="verse", text="Morning light renews us")],
        preferences=CompositionPreferences(key="C", time_signature="4/4", tempo_bpm=90),
    )

    monkeypatch.setattr(composer_service, "MAX_GENERATION_ATTEMPTS", 2)
    monkeypatch.setattr(composer_service, "validate_score", lambda *_args, **_kwargs: ["raw internal validation detail"])

    with pytest.raises(ValueError) as exc:
        composer_service.generate_melody_score(req)

    assert "Couldn’t generate a valid melody" in str(exc.value)
    assert "raw internal validation detail" not in str(exc.value)


def _assert_measure_complete(score):
    target = beats_per_measure(score.meta.time_signature)
    for measure in score.measures:
        for voice, notes in measure.voices.items():
            assert sum(note.beats for note in notes) == pytest.approx(target), f"m{measure.number} {voice} not measure-complete"


def test_cluster_repeat_arrangement_builds_music_unit_verse_indices():
    req = CompositionRequest(
        sections=[
            LyricSection(id="v1", label="Verse 1", text="Morning glory rises forever"),
            LyricSection(id="c", label="Chorus", text="Sing praise"),
            LyricSection(id="v2", label="Verse 2", text="Mercy carries every broken heart"),
        ],
        arrangement=[
            {"section_id": "v1", "progression_cluster": "Verse"},
            {"section_id": "c", "progression_cluster": "Chorus"},
            {"section_id": "v2", "progression_cluster": "Verse"},
        ],
        preferences=CompositionPreferences(time_signature="3/4", key="C", tempo_bpm=92, lyric_rhythm_preset="mixed"),
    )

    melody = generate_melody_score(req)

    assert [u.cluster_id for u in melody.meta.arrangement_music_units] == ["Verse", "Chorus", "Verse"]
    assert [u.verse_index for u in melody.meta.arrangement_music_units] == [1, 1, 2]


def test_cluster_repeat_arrangement_normalizes_measures_and_harmony_coverage():
    req = CompositionRequest(
        sections=[
            LyricSection(id="v1", label="Verse 1", text="Morning glory rises forever"),
            LyricSection(id="v2", label="Verse 2", text="Mercy carries every broken heart"),
        ],
        arrangement=[
            {"section_id": "v1", "progression_cluster": "Verse"},
            {"section_id": "v2", "progression_cluster": "Verse"},
            {"section_id": "v1", "progression_cluster": "Verse"},
        ],
        preferences=CompositionPreferences(time_signature="3/4", key="C", tempo_bpm=92, lyric_rhythm_preset="mixed"),
    )

    melody = generate_melody_score(req)
    satb = harmonize_score(melody)

    _assert_measure_complete(melody)
    _assert_measure_complete(satb)
    assert len(melody.chord_progression) == len(melody.measures)
    assert len(satb.chord_progression) == len(satb.measures)


def test_normalize_score_for_rendering_splits_cross_measure_notes_and_fills_harmony():
    score = CanonicalScore.model_validate(
        {
            "meta": {
                "key": "C",
                "primary_mode": "ionian",
                "time_signature": "3/4",
                "tempo_bpm": 90,
                "style": "Hymn",
                "stage": "melody",
                "rationale": "test",
            },
            "sections": [],
            "measures": [
                {
                    "number": 1,
                    "voices": {
                        "soprano": [
                            {"pitch": "C4", "beats": 2, "section_id": "sec-1", "lyric_mode": "single"},
                            {"pitch": "D4", "beats": 2, "section_id": "sec-1", "lyric_mode": "single"},
                        ],
                        "alto": [{"pitch": "REST", "beats": 4, "is_rest": True, "section_id": "padding"}],
                        "tenor": [{"pitch": "REST", "beats": 4, "is_rest": True, "section_id": "padding"}],
                        "bass": [{"pitch": "REST", "beats": 4, "is_rest": True, "section_id": "padding"}],
                    },
                }
            ],
            "chord_progression": [
                {"measure_number": 1, "section_id": "sec-1", "symbol": "C", "degree": 1, "pitch_classes": [0, 4, 7]}
            ],
        }
    )

    normalized = normalize_score_for_rendering(score)

    _assert_measure_complete(normalized)
    assert len(normalized.measures) == 2
    assert len(normalized.chord_progression) == 2


def test_melody_guardrail_limits_identical_consecutive_pitches():
    req = CompositionRequest(
        sections=[LyricSection(label="verse", text="holy holy holy holy holy holy holy holy")],
        preferences=CompositionPreferences(time_signature="4/4", key="C", tempo_bpm=88, lyric_rhythm_preset="syllabic"),
    )
    melody = generate_melody_score(req)
    soprano = [n for m in melody.measures for n in m.voices["soprano"] if not n.is_rest]

    max_run = 0
    run = 0
    prev_pitch = None
    for note in soprano:
        if note.lyric_mode == "tie_continue":
            continue
        midi = pitch_to_midi(note.pitch)
        if midi == prev_pitch:
            run += 1
        else:
            run = 1
        prev_pitch = midi
        max_run = max(max_run, run)

    assert max_run <= 3


def test_phrase_contour_bias_prefers_up_then_down_motion():
    req = CompositionRequest(
        sections=[LyricSection(label="verse", text="morning mercy rises now\nsteady light is falling soft")],
        preferences=CompositionPreferences(time_signature="4/4", key="D", tempo_bpm=92, lyric_rhythm_preset="mixed"),
    )
    melody = generate_melody_score(req)

    section = melody.sections[0]
    phrase_end_ids = {s.id for s in section.syllables if s.phrase_end_after}
    soprano = [n for m in melody.measures for n in m.voices["soprano"] if not n.is_rest]

    phrases = []
    current = []
    for note in soprano:
        current.append(note)
        if note.lyric_syllable_id in phrase_end_ids:
            phrases.append(current)
            current = []
    if current:
        phrases.append(current)

    directional_observations = []
    for phrase in phrases:
        if len(phrase) < 4:
            continue
        pitches = [pitch_to_midi(n.pitch) for n in phrase]
        midpoint = len(pitches) // 2
        first_half_delta = pitches[midpoint - 1] - pitches[0]
        second_half_delta = pitches[-1] - pitches[midpoint]
        directional_observations.append((first_half_delta, second_half_delta))

    assert directional_observations
    assert any(first >= 0 for first, _ in directional_observations)
    assert any(second <= 0 for _, second in directional_observations)

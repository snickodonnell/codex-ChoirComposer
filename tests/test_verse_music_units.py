from app.models import (
    ArrangementItem,
    ArrangementMusicUnit,
    CanonicalScore,
    CompositionPreferences,
    CompositionRequest,
    LyricSection,
    ScoreMeasure,
    ScoreMeta,
    ScoreNote,
    ScoreSection,
)
from app.services.composer import generate_melody_score
from app.services.musicxml_export import export_musicxml


def _req() -> CompositionRequest:
    return CompositionRequest(
        sections=[
            LyricSection(id="v1", label="Verse", is_verse=True, text="First verse line"),
            LyricSection(id="v2", label="Verse", is_verse=True, text="Second verse line"),
            LyricSection(id="c", label="Chorus", is_verse=False, text="Lift your voice"),
        ],
        arrangement=[ArrangementItem(section_id="v1"), ArrangementItem(section_id="c"), ArrangementItem(section_id="v2")],
        preferences=CompositionPreferences(key="C", time_signature="4/4", tempo_bpm=90),
    )


def test_verse_numbering_follows_arrangement_order():
    melody = generate_melody_score(_req())
    verse_sections = [section for section in melody.sections if section.is_verse]
    assert [section.verse_number for section in verse_sections] == [1, 2]


def test_verse_music_unit_is_shared_for_all_verse_instances():
    melody = generate_melody_score(_req())
    verse_units = [u for u in melody.meta.arrangement_music_units if u.music_unit_id == "verse"]
    assert len(verse_units) == 2
    assert [u.verse_index for u in verse_units] == [1, 2]


def test_musicxml_stacks_multiple_verse_lyrics_under_shared_notes():
    score = CanonicalScore(
        meta=ScoreMeta(
            key="C",
            time_signature="4/4",
            tempo_bpm=90,
            style="Hymn",
            stage="melody",
            rationale="test",
            arrangement_music_units=[
                ArrangementMusicUnit(arrangement_index=0, music_unit_id="verse", verse_index=1),
                ArrangementMusicUnit(arrangement_index=1, music_unit_id="verse", verse_index=2),
            ],
        ),
        sections=[
            ScoreSection(id="sec-1", label="Verse", is_verse=True, verse_number=1, lyrics="Amazing", syllables=[]),
            ScoreSection(id="sec-2", label="Verse", is_verse=True, verse_number=2, lyrics="Graceful", syllables=[]),
        ],
        measures=[
            ScoreMeasure(number=1, voices={
                "soprano": [ScoreNote(pitch="C4", beats=4, section_id="sec-1", lyric="Amazing", lyric_mode="single")],
                "alto": [ScoreNote(pitch="A3", beats=4, section_id="sec-1")],
                "tenor": [ScoreNote(pitch="E3", beats=4, section_id="sec-1")],
                "bass": [ScoreNote(pitch="C3", beats=4, section_id="sec-1")],
            }),
            ScoreMeasure(number=2, voices={
                "soprano": [ScoreNote(pitch="C4", beats=4, section_id="sec-2", lyric="Graceful", lyric_mode="single")],
                "alto": [ScoreNote(pitch="A3", beats=4, section_id="sec-2")],
                "tenor": [ScoreNote(pitch="E3", beats=4, section_id="sec-2")],
                "bass": [ScoreNote(pitch="C3", beats=4, section_id="sec-2")],
            }),
        ],
        chord_progression=[],
    )
    xml = export_musicxml(score)
    assert '<lyric number="1">' in xml
    assert '<lyric number="2">' in xml


def test_verse_instances_reuse_the_exact_same_soprano_structure():
    melody = generate_melody_score(_req())
    soprano_notes = [note for measure in melody.measures for note in measure.voices["soprano"]]
    first_verse = [note for note in soprano_notes if note.section_id == "sec-1"]
    second_verse = [note for note in soprano_notes if note.section_id == "sec-3"]

    assert first_verse
    assert second_verse
    assert len(first_verse) == len(second_verse)

    first_signature = [(note.pitch, note.beats, note.is_rest, note.lyric_mode) for note in first_verse]
    second_signature = [(note.pitch, note.beats, note.is_rest, note.lyric_mode) for note in second_verse]
    assert first_signature == second_signature


def test_playback_timing_uses_one_second_pause_only_between_real_sections():
    melody = generate_melody_score(_req())
    notes = [note for measure in melody.measures for note in measure.voices["soprano"]]
    seconds_per_beat = 60 / melody.meta.tempo_bpm

    total_seconds = 0.0
    previous_section_id = None
    transition_count = 0
    for note in notes:
        current_section_id = note.section_id if note.section_id != "padding" else previous_section_id
        if previous_section_id and current_section_id and current_section_id != previous_section_id:
            transition_count += 1
            total_seconds += 1.0
        total_seconds += note.beats * seconds_per_beat
        previous_section_id = current_section_id or previous_section_id

    music_seconds = sum(note.beats * seconds_per_beat for note in notes)
    assert transition_count == 2
    assert total_seconds == music_seconds + 2.0


def test_musicxml_uses_canonical_verse_notes_with_melisma_for_shorter_third_verse():
    req = CompositionRequest(
        sections=[
            LyricSection(id="v1", label="Verse", is_verse=True, text="When peace like a river attendeth my way"),
            LyricSection(id="c", label="Chorus", is_verse=False, text="It is well with my soul"),
            LyricSection(id="v2", label="Verse", is_verse=True, text="When sorrows like sea billows roll"),
            LyricSection(id="v3", label="Verse", is_verse=True, text="When toils"),
        ],
        arrangement=[
            ArrangementItem(section_id="v1"),
            ArrangementItem(section_id="c"),
            ArrangementItem(section_id="v2"),
            ArrangementItem(section_id="c"),
            ArrangementItem(section_id="v3"),
        ],
        preferences=CompositionPreferences(key="C", time_signature="4/4", tempo_bpm=90),
    )

    melody = generate_melody_score(req)
    soprano_notes = [note for measure in melody.measures for note in measure.voices["soprano"]]
    verse_1 = [note for note in soprano_notes if note.section_id == "sec-1"]
    verse_3 = [note for note in soprano_notes if note.section_id == "sec-5"]

    assert len(verse_1) == len(verse_3)
    assert [(note.pitch, note.beats, note.is_rest) for note in verse_1] == [
        (note.pitch, note.beats, note.is_rest) for note in verse_3
    ]

    xml = export_musicxml(melody)
    assert '<lyric number="3">' in xml
    assert "<text>toils</text>" in xml
    assert xml.count('<lyric number="3">') >= 2
    assert '<lyric number="3">' in xml and '<extend/>' in xml


def test_third_verse_measure_count_matches_first_verse_for_shared_music_unit():
    req = CompositionRequest(
        sections=[
            LyricSection(id="v1", label="Verse", is_verse=True, text="Amazing grace how sweet the sound"),
            LyricSection(id="c", label="Chorus", is_verse=False, text="I once was lost but now am found"),
            LyricSection(id="v2", label="Verse", is_verse=True, text="Twas grace that taught my heart to fear"),
            LyricSection(id="v3", label="Verse", is_verse=True, text="And grace my fears relieved"),
        ],
        arrangement=[
            ArrangementItem(section_id="v1"),
            ArrangementItem(section_id="c"),
            ArrangementItem(section_id="v2"),
            ArrangementItem(section_id="c"),
            ArrangementItem(section_id="v3"),
        ],
        preferences=CompositionPreferences(key="C", time_signature="4/4", tempo_bpm=90),
    )

    melody = generate_melody_score(req)
    spans: dict[str, set[int]] = {}
    for measure in melody.measures:
        for note in measure.voices["soprano"]:
            if note.section_id in {"sec-1", "sec-5"}:
                spans.setdefault(note.section_id, set()).add(measure.number)

    assert len(spans["sec-1"]) == len(spans["sec-5"])
    assert melody.meta.verse_music_unit_form is not None
    assert len(spans["sec-1"]) == melody.meta.verse_music_unit_form.total_measure_count


def test_verse_music_unit_form_fields_match_canonical_first_verse_projection():
    melody = generate_melody_score(_req())
    form = melody.meta.verse_music_unit_form

    assert form is not None

    beat_cap = 4
    total_beats = form.pickup_beats + sum(sum(slot) for slot in form.rhythmic_skeleton)
    expected_measure_count = max(1, int(max(total_beats - 1e-9, 0.0) // beat_cap) + 1)
    assert form.total_measure_count == expected_measure_count

    recomputed_phrase_targets: list[int] = []
    running = form.pickup_beats
    for idx in form.phrase_end_syllable_indices:
        if 0 <= idx < len(form.rhythmic_skeleton):
            running = form.pickup_beats + sum(sum(slot) for slot in form.rhythmic_skeleton[: idx + 1])
            recomputed_phrase_targets.append(int(max(running - 1e-9, 0.0) // beat_cap) + 1)

    assert form.phrase_bar_targets == recomputed_phrase_targets



def test_commas_in_third_verse_do_not_add_phrase_breaks_or_measures():
    req = CompositionRequest(
        sections=[
            LyricSection(id="v1", label="Verse", is_verse=True, text="Amazing grace\nHow sweet the sound"),
            LyricSection(id="c", label="Chorus", is_verse=False, text="I once was lost but now am found"),
            LyricSection(id="v2", label="Verse", is_verse=True, text="Grace has kept me through the years"),
            LyricSection(id="v3", label="Verse", is_verse=True, text="Amazing grace, how sweet, the sound"),
        ],
        arrangement=[
            ArrangementItem(section_id="v1"),
            ArrangementItem(section_id="c"),
            ArrangementItem(section_id="v2"),
            ArrangementItem(section_id="c"),
            ArrangementItem(section_id="v3"),
        ],
        preferences=CompositionPreferences(key="C", time_signature="4/4", tempo_bpm=90),
    )

    melody = generate_melody_score(req)
    verse1_phrase_breaks = [s.id for s in melody.sections[0].syllables if s.phrase_end_after]
    verse3_phrase_breaks = [s.id for s in melody.sections[4].syllables if s.phrase_end_after]

    assert len(verse3_phrase_breaks) == len(verse1_phrase_breaks)

    spans: dict[str, set[int]] = {}
    for measure in melody.measures:
        for note in measure.voices["soprano"]:
            if note.section_id in {"sec-1", "sec-5"}:
                spans.setdefault(note.section_id, set()).add(measure.number)
    assert len(spans["sec-1"]) == len(spans["sec-5"])

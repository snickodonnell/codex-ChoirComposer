from app.models import ArrangementItem, CompositionPreferences, CompositionRequest, LyricSection, PhraseBlock
from app.services.composer import generate_melody_score, harmonize_score
from app.services.musicxml_export import export_musicxml


def test_export_musicxml_contains_satb_staves_lyrics_and_harmony():
    req = CompositionRequest(
        sections=[LyricSection(label="verse", text="Amazing grace how sweet")],
        preferences=CompositionPreferences(key="G", time_signature="4/4", tempo_bpm=88),
    )
    satb = harmonize_score(generate_melody_score(req))
    xml = export_musicxml(satb)

    assert "<staves>2</staves>" in xml
    assert "<clef number=\"1\"><sign>G</sign><line>2</line></clef>" in xml
    assert "<clef number=\"2\"><sign>F</sign><line>4</line></clef>" in xml
    assert "<voice>1</voice>" in xml
    assert "<voice>4</voice>" in xml
    assert "<lyric number=\"1\">" in xml
    assert "<harmony>" in xml


def test_export_musicxml_includes_breath_mark_without_extra_duration():
    req = CompositionRequest(
        sections=[LyricSection(id="verse-1", label="verse", text="holy holy\nforever")],
        arrangement=[
            ArrangementItem(
                section_id="verse-1",
                phrase_blocks=[
                    PhraseBlock(text="holy holy", must_end_at_barline=False, breath_after_phrase=True),
                    PhraseBlock(text="forever", must_end_at_barline=False, breath_after_phrase=False),
                ],
            )
        ],
        preferences=CompositionPreferences(key="G", time_signature="4/4", tempo_bpm=88),
    )

    satb = harmonize_score(generate_melody_score(req))
    xml = export_musicxml(satb)

    assert xml.count("<breath-mark/>") == 1
    assert "<bar-style>" not in xml


def test_export_musicxml_includes_arrangement_music_unit_mapping_comments():
    req = CompositionRequest(
        sections=[
            LyricSection(id="v1", label="Verse 1", text="Amazing grace how sweet"),
            LyricSection(id="v2", label="Verse 2", text="That saved a soul like me"),
        ],
        arrangement=[
            ArrangementItem(section_id="v1", progression_cluster="Verse"),
            ArrangementItem(section_id="v2", progression_cluster="Verse"),
        ],
        preferences=CompositionPreferences(key="G", time_signature="4/4", tempo_bpm=88),
    )

    satb = harmonize_score(generate_melody_score(req))
    xml = export_musicxml(satb)

    assert "<!-- arrangement-music-units -->" in xml
    assert "arrangement_index=0,cluster_id=Verse,verse_index=1" in xml
    assert "arrangement_index=1,cluster_id=Verse,verse_index=2" in xml

from app.models import ArrangementMusicUnit, CanonicalScore, ScoreMeasure, ScoreMeta, ScoreNote, ScoreSection


def _build_note(section_id: str, lyric: str, beats: float = 4.0) -> ScoreNote:
    return ScoreNote(
        pitch="C4",
        beats=beats,
        is_rest=False,
        lyric=lyric,
        lyric_syllable_id=f"{section_id}-{lyric}",
        lyric_mode="single",
        section_id=section_id,
        lyric_index=0,
    )


def _build_satb_measure(number: int, section_id: str, lyric: str, beats: float = 4.0) -> ScoreMeasure:
    soprano_note = _build_note(section_id, lyric, beats=beats)
    return ScoreMeasure(
        number=number,
        voices={
            "soprano": [soprano_note],
            "alto": [ScoreNote(pitch="A3", beats=beats, is_rest=False, section_id=section_id)],
            "tenor": [ScoreNote(pitch="E3", beats=beats, is_rest=False, section_id=section_id)],
            "bass": [ScoreNote(pitch="C3", beats=beats, is_rest=False, section_id=section_id)],
        },
    )


def test_export_musicxml_stacks_cluster_verses_on_single_notation_block():
    score = CanonicalScore(
        meta=ScoreMeta(
            key="C",
            time_signature="4/4",
            tempo_bpm=90,
            style="Hymn",
            stage="satb",
            rationale="test",
            arrangement_music_units=[
                ArrangementMusicUnit(arrangement_index=0, cluster_id="Verse", verse_index=1),
                ArrangementMusicUnit(arrangement_index=1, cluster_id="Verse", verse_index=2),
            ],
        ),
        sections=[
            ScoreSection(id="sec-1", label="Verse 1", lyrics="Amazing", syllables=[]),
            ScoreSection(id="sec-2", label="Verse 2", lyrics="Graceful", syllables=[]),
        ],
        measures=[
            _build_satb_measure(1, "sec-1", "Amazing", beats=4.0),
            _build_satb_measure(2, "sec-2", "Graceful", beats=4.0),
        ],
        chord_progression=[],
    )

    xml = export_musicxml(score)

    assert xml.count("<measure number=") == 1
    assert '<lyric number="1">' in xml
    assert '<lyric number="2">' in xml
    assert "<text>Amazing</text>" in xml
    assert "<text>Graceful</text>" in xml
    assert "<words>Verse (Verse 1, Verse 2)</words>" in xml
    assert "<print new-system=\"yes\"/>" in xml


def test_export_musicxml_starts_new_system_for_each_cluster_music_unit():
    score = CanonicalScore(
        meta=ScoreMeta(
            key="C",
            time_signature="4/4",
            tempo_bpm=90,
            style="Hymn",
            stage="satb",
            rationale="test",
            arrangement_music_units=[
                ArrangementMusicUnit(arrangement_index=0, cluster_id="Verse", verse_index=1),
                ArrangementMusicUnit(arrangement_index=1, cluster_id="Verse", verse_index=2),
                ArrangementMusicUnit(arrangement_index=2, cluster_id="Chorus", verse_index=1),
            ],
        ),
        sections=[
            ScoreSection(id="sec-1", label="Verse 1", lyrics="Amazing", syllables=[]),
            ScoreSection(id="sec-2", label="Verse 2", lyrics="Graceful", syllables=[]),
            ScoreSection(id="sec-3", label="Chorus", lyrics="Hallelujah", syllables=[]),
        ],
        measures=[
            _build_satb_measure(1, "sec-1", "Amazing", beats=4.0),
            _build_satb_measure(2, "sec-2", "Graceful", beats=4.0),
            _build_satb_measure(3, "sec-3", "Hallelujah", beats=4.0),
        ],
        chord_progression=[],
    )

    xml = export_musicxml(score)

    assert xml.count("<measure number=") == 2
    assert xml.count("<print new-system=\"yes\"/>") == 2
    assert "<words>Verse (Verse 1, Verse 2)</words>" in xml
    assert "<words>Chorus</words>" in xml


def test_export_musicxml_falls_back_to_duplicate_notation_when_cluster_structure_differs(caplog):
    score = CanonicalScore(
        meta=ScoreMeta(
            key="C",
            time_signature="4/4",
            tempo_bpm=90,
            style="Hymn",
            stage="satb",
            rationale="test",
            arrangement_music_units=[
                ArrangementMusicUnit(arrangement_index=0, cluster_id="Verse", verse_index=1),
                ArrangementMusicUnit(arrangement_index=1, cluster_id="Verse", verse_index=2),
            ],
        ),
        sections=[
            ScoreSection(id="sec-1", label="Verse 1", lyrics="Amazing", syllables=[]),
            ScoreSection(id="sec-2", label="Verse 2", lyrics="Graceful", syllables=[]),
        ],
        measures=[
            _build_satb_measure(1, "sec-1", "Amazing", beats=4.0),
            _build_satb_measure(2, "sec-2", "Graceful", beats=2.0),
        ],
        chord_progression=[],
    )

    xml = export_musicxml(score)

    assert xml.count("<measure number=") == 2
    assert "musicxml_verse_stacking_fallback" in caplog.text

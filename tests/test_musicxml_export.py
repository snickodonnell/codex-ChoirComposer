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

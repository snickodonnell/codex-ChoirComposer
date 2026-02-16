from app.models import CompositionPreferences, CompositionRequest, LyricSection
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

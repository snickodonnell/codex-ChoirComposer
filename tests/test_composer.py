import pytest
from pydantic import ValidationError

from app.models import CompositionPreferences, CompositionRequest, LyricSection
from app.services.composer import generate_melody_score, harmonize_score
from app.services.lyric_mapping import config_for_preset, plan_syllable_rhythm, tokenize_section_lyrics
from app.services.music_theory import VOICE_RANGES, pitch_to_midi
from app.services.musicxml_export import export_musicxml
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


def test_generate_melody_and_satb_validate():
    req = CompositionRequest(
        sections=[LyricSection(label="verse", title="Verse 1", text="Glory rises in the dawn.")],
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
        sections=[LyricSection(label="Verse Lift", title="Verse Lift", text="Glory rises in the dawn.")],
        preferences=CompositionPreferences(style="Hymn", mood="Uplifting", lyric_rhythm_preset="mixed", key="D", time_signature="4/4", tempo_bpm=92),
    )
    melody = generate_melody_score(req)
    assert melody.sections[0].label == "Verse Lift"
    assert melody.chord_progression


def test_pause_after_section_inserts_interlude_rest_between_sections():
    req = CompositionRequest(
        sections=[
            LyricSection(label="Verse", title="Verse", text="Light in the morning fills every heart", pause_beats=2),
            LyricSection(label="Chorus", title="Chorus", text="Sing together, hope forever"),
        ],
        preferences=CompositionPreferences(time_signature="4/4", key="C", tempo_bpm=90, lyric_rhythm_preset="mixed"),
    )
    melody = generate_melody_score(req)

    soprano = [n for m in melody.measures for n in m.voices["soprano"]]
    interlude_rests = [n for n in soprano if n.is_rest and n.section_id == "interlude"]

    assert interlude_rests
    assert abs(sum(n.beats for n in interlude_rests) - 2.0) < 1e-9


def test_strong_beats_prefer_chord_tones():
    req = CompositionRequest(
        sections=[LyricSection(label="verse", title="Verse", text="Morning glory rises higher")],
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
        sections=[LyricSection(label="verse", title="Verse", text="Morning mercy lights the sky and gives us song")],
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


def test_musicxml_export_contains_satb_parts_and_harmony():
    req = CompositionRequest(
        sections=[LyricSection(label="chorus", title="Chorus", text="Sing together forever")],
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
        CompositionPreferences(tempo_bpm=300)

    with pytest.raises(ValidationError):
        CompositionPreferences(key="Am", primary_mode="aeolian")

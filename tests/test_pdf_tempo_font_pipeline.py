from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from app.models import CompositionPreferences, CompositionRequest, LyricSection
from app.services.composer import generate_melody_score
from app.services.engraving_preview import EngravingOptions, preview_service


def test_tempo_mark_is_text_with_leipzig_font_family():
    pytest.importorskip('verovio')

    req = CompositionRequest(
        sections=[LyricSection(label='Verse', text='Morning light renews us')],
        preferences=CompositionPreferences(key='D', time_signature='4/4', tempo_bpm=90),
    )
    score = generate_melody_score(req)
    pages, _ = preview_service.engrave_score(score, EngravingOptions(include_all_pages=False))
    svg = pages[0].svg

    root = ET.fromstring(svg)
    ns = {'svg': 'http://www.w3.org/2000/svg'}
    tempo_text_nodes = []
    for text_node in root.findall('.//svg:text', ns):
        joined = ''.join(text_node.itertext())
        if '=' in joined:
            tempo_text_nodes.append(text_node)

    assert tempo_text_nodes, 'Expected tempo mark to be rendered in a <text> node containing =.'

    tempo_text = tempo_text_nodes[0]
    tempo_spans = tempo_text.findall('.//svg:tspan', ns)
    leipzig_spans = [span for span in tempo_spans if span.attrib.get('font-family') == 'Leipzig']
    assert leipzig_spans

    contains_pua = False
    for span in leipzig_spans:
        for ch in ''.join(span.itertext()):
            if 0xE000 <= ord(ch) <= 0xF8FF:
                contains_pua = True
                break
        if contains_pua:
            break
    assert contains_pua, 'Expected SMuFL PUA glyph in tempo text.'


def test_pdf_pipeline_registers_and_maps_only_leipzig_tempo_font():
    app_js = Path('app/static/app.js').read_text(encoding='utf-8')

    assert 'ensureTempoFontRegistered' in app_js
    assert "import('/static/vendor/fonts/leipzig.ttf.base64.js')" in app_js
    assert "normalizeFontFamilyName(requestedFontFamily) === 'leipzig'" in app_js
    assert 'fontCallback' in app_js

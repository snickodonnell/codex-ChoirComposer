from pathlib import Path


def test_canvg_is_not_loaded_from_cdn_and_uses_local_bundle():
    index_html = Path('app/static/index.html').read_text(encoding='utf-8')
    assert 'cdn.jsdelivr.net/npm/canvg' not in index_html
    assert '/static/app.js?v=20260301-svg2pdf-vector' in index_html


def test_app_uses_dynamic_canvg_import_with_actionable_errors():
    app_js = Path('app/static/app.js').read_text(encoding='utf-8')
    assert "import('/static/vendor/canvg.browser.js')" in app_js
    assert 'Canvg.fromString' in app_js
    assert 'SVG renderer is unavailable:' in app_js


def test_bundled_canvg_asset_exists():
    bundled = Path('app/static/vendor/canvg.browser.js')
    assert bundled.exists()
    assert bundled.stat().st_size > 0


def test_svg2pdf_vector_renderer_is_primary_path():
    app_js = Path('app/static/app.js').read_text(encoding='utf-8')
    assert "import('/static/vendor/pdf-vector.browser.js')" in app_js
    assert 'await svg2pdf(svgEl, doc, { x: 0, y: 0, width, height });' in app_js
    assert "renderer: 'svg2pdf-primary'" in app_js


def test_bundled_vector_pdf_asset_exists():
    bundled = Path('app/static/vendor/pdf-vector.browser.js')
    assert bundled.exists()
    assert bundled.stat().st_size > 0

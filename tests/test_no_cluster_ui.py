from pathlib import Path


def test_no_legacy_cluster_ui_copy_present():
    index_html = Path("app/static/index.html").read_text().lower()
    app_js = Path("app/static/app.js").read_text().lower()

    assert "progression cluster" not in index_html
    assert "arrangement-progression-cluster" not in app_js

import types

from app.main import app
from app.services import pdf_deps
from fastapi.testclient import TestClient


client = TestClient(app)


def _module_with_svg2pdf(fn):
    return types.SimpleNamespace(svg2pdf=fn)


def test_check_pdf_export_capabilities_missing_python_deps(monkeypatch):
    pdf_deps.check_pdf_export_capabilities.cache_clear()

    def fake_import(name):
        if name == "verovio":
            return types.SimpleNamespace(toolkit=lambda: types.SimpleNamespace(renderToPDF=lambda: b"pdf"))
        raise ImportError(name)

    monkeypatch.setattr(pdf_deps.importlib, "import_module", fake_import)

    capabilities = pdf_deps.check_pdf_export_capabilities()

    assert capabilities["cairosvg_available"] is False
    assert capabilities["pypdf_available"] is False
    assert "cairosvg" in capabilities["missing"]
    assert "pypdf" in capabilities["missing"]


def test_check_pdf_export_capabilities_cairo_native_missing(monkeypatch):
    pdf_deps.check_pdf_export_capabilities.cache_clear()

    def fake_import(name):
        if name == "verovio":
            return types.SimpleNamespace(toolkit=lambda: types.SimpleNamespace(renderToPDF=lambda: b"pdf"))
        if name == "cairosvg":
            return _module_with_svg2pdf(lambda **_kwargs: (_ for _ in ()).throw(OSError("cannot load cairo")))
        if name == "pypdf":
            return types.SimpleNamespace()
        raise ImportError(name)

    monkeypatch.setattr(pdf_deps.importlib, "import_module", fake_import)

    capabilities = pdf_deps.check_pdf_export_capabilities()

    assert capabilities["cairosvg_available"] is True
    assert capabilities["pypdf_available"] is True
    assert capabilities["cairo_native_available"] is False
    assert capabilities["fallback_svg_to_pdf_available"] is False
    assert "cairo_native" in capabilities["missing"]


def test_export_pdf_capabilities_endpoint_has_expected_keys(monkeypatch):
    fake_caps = {
        "verovio_pdf_available": False,
        "cairosvg_available": True,
        "pypdf_available": True,
        "cairo_native_available": False,
        "fallback_svg_to_pdf_available": False,
        "missing": ["cairo_native"],
        "help": ["Install cairo"],
    }
    monkeypatch.setattr("app.main.check_pdf_export_capabilities", lambda: fake_caps)

    res = client.get("/api/export-pdf/capabilities")

    assert res.status_code == 200
    assert set(fake_caps.keys()) == set(res.json().keys())

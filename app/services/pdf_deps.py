from __future__ import annotations

import importlib
from functools import lru_cache


_CAIRO_HELP_TEXT = "Install Cairo system library (macOS: brew install cairo, Ubuntu/Debian: sudo apt-get install libcairo2, Windows: use Docker or MSYS2/Chocolatey Cairo package)."


@lru_cache(maxsize=1)
def check_pdf_export_capabilities() -> dict[str, object]:
    missing: list[str] = []
    help_messages: list[str] = []

    verovio_pdf_available = False
    try:
        verovio = importlib.import_module("verovio")
        toolkit = verovio.toolkit()
        verovio_pdf_available = callable(getattr(toolkit, "renderToPDF", None)) or callable(
            getattr(toolkit, "renderToPDFFile", None)
        )
    except Exception:
        verovio_pdf_available = False

    cairosvg_available = False
    cairo_native_available = False
    cairosvg_module = None
    try:
        cairosvg_module = importlib.import_module("cairosvg")
        cairosvg_available = True
    except ImportError:
        missing.append("cairosvg")
        help_messages.append("Install Python dependencies with: pip install .[pdf]")

    pypdf_available = False
    try:
        importlib.import_module("pypdf")
        pypdf_available = True
    except ImportError:
        missing.append("pypdf")
        help_messages.append("Install Python dependencies with: pip install .[pdf]")

    if cairosvg_available and cairosvg_module is not None:
        try:
            cairosvg_module.svg2pdf(
                bytestring=(
                    b'<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1">'
                    b'<rect width="1" height="1"/></svg>'
                )
            )
            cairo_native_available = True
        except OSError as exc:
            if "cairo" in str(exc).lower():
                missing.append("cairo_native")
                help_messages.append(_CAIRO_HELP_TEXT)
            else:
                missing.append("cairo_native")
                help_messages.append(_CAIRO_HELP_TEXT)

    fallback_svg_to_pdf_available = cairosvg_available and pypdf_available and cairo_native_available
    if not verovio_pdf_available:
        missing.append("verovio_pdf")

    deduped_help = list(dict.fromkeys(help_messages))
    deduped_missing = list(dict.fromkeys(missing))
    return {
        "verovio_pdf_available": verovio_pdf_available,
        "cairosvg_available": cairosvg_available,
        "pypdf_available": pypdf_available,
        "cairo_native_available": cairo_native_available,
        "fallback_svg_to_pdf_available": fallback_svg_to_pdf_available,
        "missing": deduped_missing,
        "help": deduped_help,
    }

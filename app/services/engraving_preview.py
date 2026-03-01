from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from threading import Lock
from typing import Any

from app.logging_utils import log_event
from app.models import CanonicalScore
from app.services.musicxml_export import export_musicxml

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PreviewArtifact:
    page: int
    svg: str


@dataclass(frozen=True)
class EngravedPageArtifact:
    page: int
    svg: str
    svg_meta: dict[str, str]
    svg_hash: str


@dataclass(frozen=True)
class EngravingLayoutConfig:
    page_width: int = 2100
    page_height: int = 2970
    scale: int = 42
    system_spacing: int = 120
    staff_spacing: int = 10
    margin_top: int = 80
    margin_bottom: int = 80
    margin_left: int = 80
    margin_right: int = 80


DEFAULT_LAYOUT = EngravingLayoutConfig()


def _clamp_system_spacing(value: int) -> int:
    # Verovio expects spacingSystem in the inclusive range [0, 100].
    return max(0, min(100, value))


@dataclass(frozen=True)
class EngravingOptions:
    include_all_pages: bool = False
    layout: EngravingLayoutConfig = DEFAULT_LAYOUT


def build_verovio_options(layout: EngravingLayoutConfig) -> dict[str, Any]:
    return {
        "scale": layout.scale,
        "pageWidth": layout.page_width,
        "pageHeight": layout.page_height,
        "adjustPageHeight": True,
        "breaks": "auto",
        "spacingSystem": _clamp_system_spacing(layout.system_spacing),
        "spacingStaff": layout.staff_spacing,
        "spacingLinear": 0.3,
        "justifyVertically": True,
        "systemMaxPerPage": 0,
        "pageMarginTop": layout.margin_top,
        "pageMarginBottom": layout.margin_bottom,
        "pageMarginLeft": layout.margin_left,
        "pageMarginRight": layout.margin_right,
        "mnumInterval": 1,
        "condense": "none",
        "footer": "none",
        "header": "none",
        "svgViewBox": True,
    }


def extract_svg_meta(svg: str) -> dict[str, str]:
    match = re.search(r"<svg\b[^>]*>", svg, flags=re.IGNORECASE)
    if not match:
        return {"first_tag_snippet": ""}

    first_tag = match.group(0)
    compact_first_tag = " ".join(first_tag.split())
    first_tag_snippet = compact_first_tag[:300]
    if len(compact_first_tag) > 300:
        first_tag_snippet += "..."

    attrs: dict[str, str] = {"first_tag_snippet": first_tag_snippet}
    for key, raw in re.findall(r"([:\w-]+)\s*=\s*([\"'][^\"']*[\"'])", first_tag):
        attrs[key] = raw[1:-1]

    extracted: dict[str, str] = {"first_tag_snippet": first_tag_snippet}
    for key in ("width", "height", "viewBox", "preserveAspectRatio", "xmlns", "xmlns:xlink", "version"):
        if key in attrs:
            extracted[key] = attrs[key]
    return extracted


def hash_svg(svg: str) -> str:
    return hashlib.sha256(svg.encode("utf-8")).hexdigest()


class EngravingPreviewService:
    def __init__(self):
        self._cache: dict[str, list[EngravedPageArtifact]] = {}
        self._cache_lock = Lock()

    def render_preview(self, score: CanonicalScore, options: EngravingOptions) -> tuple[list[PreviewArtifact], bool]:
        pages, cache_hit = self.engrave_score(score, options)
        return [PreviewArtifact(page=item.page, svg=item.svg) for item in pages], cache_hit

    def engrave_score(self, score: CanonicalScore, options: EngravingOptions) -> tuple[list[EngravedPageArtifact], bool]:
        cache_key = self._cache_key(score, options)
        with self._cache_lock:
            cached = self._cache.get(cache_key)
        if cached is not None:
            log_event(logger, "engraving_preview_cache_hit", cache_key=cache_key, pages=len(cached))
            return cached, True

        musicxml = self.build_musicxml(score)
        artifacts = self.engrave_to_svg_pages(musicxml, options)
        with self._cache_lock:
            self._cache[cache_key] = artifacts
        log_event(logger, "engraving_preview_cache_store", cache_key=cache_key, pages=len(artifacts))
        return artifacts, False

    def _cache_key(self, score: CanonicalScore, options: EngravingOptions) -> str:
        canonical_payload = {
            "score": score.model_dump(mode="json"),
            "options": {
                "include_all_pages": options.include_all_pages,
                "layout": {
                    "scale": options.layout.scale,
                    "page_width": options.layout.page_width,
                    "page_height": options.layout.page_height,
                    "system_spacing": options.layout.system_spacing,
                    "staff_spacing": options.layout.staff_spacing,
                    "margin_top": options.layout.margin_top,
                    "margin_bottom": options.layout.margin_bottom,
                    "margin_left": options.layout.margin_left,
                    "margin_right": options.layout.margin_right,
                },
            },
        }
        digest = hashlib.sha256(json.dumps(canonical_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        return f"engraving:v1:{digest}"

    def build_musicxml(self, score: CanonicalScore) -> str:
        return export_musicxml(score)

    def build_toolkit(self, musicxml: str, options: EngravingOptions):
        try:
            import verovio  # type: ignore
        except ImportError as exc:  # pragma: no cover - depends on deployment image
            raise RuntimeError("Verovio is required for server-side preview rendering.") from exc

        toolkit = verovio.toolkit()
        toolkit.setOptions(build_verovio_options(options.layout))
        toolkit.loadData(musicxml)
        return toolkit

    def engrave_to_svg_pages(self, musicxml: str, options: EngravingOptions) -> list[EngravedPageArtifact]:
        toolkit = self.build_toolkit(musicxml, options)

        page_count = max(1, int(toolkit.getPageCount()))
        final_page_count = page_count if options.include_all_pages else 1

        artifacts: list[EngravedPageArtifact] = []
        for page in range(1, final_page_count + 1):
            svg = _render_svg_page(toolkit, page)
            artifacts.append(EngravedPageArtifact(page=page, svg=svg, svg_meta=extract_svg_meta(svg), svg_hash=hash_svg(svg)))

        log_event(logger, "engraving_preview_rendered", pages=len(artifacts), total_pages=page_count)
        return artifacts



def _render_svg_page(toolkit, page: int) -> str:
    try:
        return toolkit.renderToSVG(page)
    except TypeError:
        return toolkit.renderToSVG(page, {})


preview_service = EngravingPreviewService()

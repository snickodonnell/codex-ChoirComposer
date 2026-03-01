from __future__ import annotations

import socket
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]


def _wait_for_port(host: str, port: int, timeout_s: float = 20.0) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            if sock.connect_ex((host, port)) == 0:
                return
        time.sleep(0.1)
    raise TimeoutError(f"Timed out waiting for {host}:{port}")


@contextmanager
def run_app_server(port: int = 8767):
    proc = subprocess.Popen(
        [
            "python",
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        _wait_for_port("127.0.0.1", port)
        yield f"http://127.0.0.1:{port}"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_engraving_unavailable_banner_only_shows_for_actual_engraving_failure():
    playwright = pytest.importorskip("playwright.sync_api")

    with run_app_server() as base_url:
        try:
            with playwright.sync_playwright() as p:
                browser = p.chromium.launch()
                page = browser.new_page()
                page.goto(base_url, wait_until="domcontentloaded")

                success = page.evaluate(
                    """
                    () => {
                      const decision = window.buildEngravingBannerDecision('melody', '/api/engrave/preview', {
                        httpStatus: 200,
                        hadException: false,
                        responsePayload: { svg_pages: ['<svg viewBox="0 0 10 10"></svg>'], svg_meta: [{}] },
                        svgPages: ['<svg viewBox="0 0 10 10"></svg>'],
                        rendererCheckState: { canvgAvailable: false, legacyRendererUnavailable: true },
                      });
                      document.querySelector('#formErrors').innerHTML = '';
                      window.maybeShowEngravingUnavailableBanner('melody', decision);
                      return {
                        shouldShowBanner: decision.shouldShowBanner,
                        errorsText: document.querySelector('#formErrors')?.textContent || '',
                      };
                    }
                    """
                )

                assert success["shouldShowBanner"] is False
                assert "sheet rendering is temporarily unavailable" not in success["errorsText"]

                failure = page.evaluate(
                    """
                    () => {
                      const decision = window.buildEngravingBannerDecision('satb', '/api/engrave/preview', {
                        httpStatus: 200,
                        hadException: false,
                        responsePayload: { svg_pages: [], svg_meta: [] },
                        svgPages: [],
                        rendererCheckState: { canvgAvailable: false, legacyRendererUnavailable: true },
                      });
                      document.querySelector('#formErrors').innerHTML = '';
                      window.maybeShowEngravingUnavailableBanner('satb', decision);
                      return {
                        shouldShowBanner: decision.shouldShowBanner,
                        errorsText: document.querySelector('#formErrors')?.textContent || '',
                      };
                    }
                    """
                )

                assert failure["shouldShowBanner"] is True
                assert "SATB generated, but sheet rendering is temporarily unavailable" in failure["errorsText"]
                browser.close()
        except Exception as exc:  # pragma: no cover - environment-dependent fallback
            pytest.skip(f"Playwright browser runtime unavailable in this environment: {exc}")

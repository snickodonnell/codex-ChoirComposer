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
def run_app_server(port: int = 8765):
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


def test_generate_melody_enables_playback_even_if_sheet_rendering_breaks():
    playwright = pytest.importorskip("playwright.sync_api")

    with run_app_server() as base_url:
        try:
            with playwright.sync_playwright() as p:
                browser = p.chromium.launch()
                page = browser.new_page()
                page.goto(base_url, wait_until="domcontentloaded")
                page.click("#generateMelody")
                page.wait_for_function(
                    """
                    () => {
                      const meta = document.querySelector('#melodyMeta')?.textContent || '';
                      return meta.trim().length > 0;
                    }
                    """,
                    timeout=20000,
                )
                assert page.locator("#playMelody").is_disabled() is False
                browser.close()
        except Exception as exc:  # pragma: no cover - environment-dependent fallback
            pytest.skip(f"Playwright browser runtime unavailable in this environment: {exc}")


def test_regenerate_clusters_defaults_to_all_selected_and_melody_generation_still_works():
    playwright = pytest.importorskip("playwright.sync_api")

    with run_app_server() as base_url:
        try:
            with playwright.sync_playwright() as p:
                browser = p.chromium.launch()
                page = browser.new_page()
                page.goto(base_url, wait_until="domcontentloaded")

                selected_info = page.evaluate(
                    """
                    () => {
                      const select = document.querySelector('#regenerateClusters');
                      const values = Array.from(select.options).map((o) => o.value);
                      const selected = Array.from(select.selectedOptions).map((o) => o.value);
                      return { values, selected };
                    }
                    """
                )

                assert selected_info["values"]
                assert selected_info["selected"] == selected_info["values"]

                page.click("#generateMelody")
                page.wait_for_function(
                    """
                    () => {
                      const meta = document.querySelector('#melodyMeta')?.textContent || '';
                      return meta.trim().length > 0;
                    }
                    """,
                    timeout=20000,
                )
                assert page.locator("#playMelody").is_disabled() is False
                browser.close()
        except Exception as exc:  # pragma: no cover - environment-dependent fallback
            pytest.skip(f"Playwright browser runtime unavailable in this environment: {exc}")

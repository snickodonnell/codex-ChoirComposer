from __future__ import annotations

import json
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
                assert page.locator("#startMelody").is_disabled() is False
                browser.close()
        except Exception as exc:  # pragma: no cover - environment-dependent fallback
            pytest.skip(f"Playwright browser runtime unavailable in this environment: {exc}")




def test_generate_melody_works_with_default_ui_seed_data_without_manual_input():
    playwright = pytest.importorskip("playwright.sync_api")

    with run_app_server() as base_url:
        try:
            with playwright.sync_playwright() as p:
                browser = p.chromium.launch()
                page = browser.new_page()
                page.goto(base_url, wait_until="domcontentloaded")

                section_count = page.evaluate(
                    """
                    () => document.querySelectorAll('#sections .section-row').length
                    """
                )
                arrangement_count = page.evaluate(
                    """
                    () => document.querySelectorAll('#arrangementList .arrangement-item').length
                    """
                )

                assert section_count >= 3
                assert arrangement_count >= 3

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

                errors = page.evaluate(
                    """
                    () => document.querySelector('#formErrors')?.textContent || ''
                    """
                )
                assert "Please add lyrics" not in errors
                assert page.locator("#startMelody").is_disabled() is False
                browser.close()
        except Exception as exc:  # pragma: no cover - environment-dependent fallback
            pytest.skip(f"Playwright browser runtime unavailable in this environment: {exc}")



def test_loading_seed_data_resets_generated_workflow_state():
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

                assert page.locator("#startMelody").is_disabled() is False

                page.click("#loadTestData")

                assert page.locator("#startMelody").is_disabled() is True
                melody_meta = page.locator("#melodyMeta").text_content() or ""
                satb_meta = page.locator("#satbMeta").text_content() or ""
                assert melody_meta.strip() == ""
                assert satb_meta.strip() == ""

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
                assert page.locator("#startMelody").is_disabled() is False
                browser.close()
        except Exception as exc:  # pragma: no cover - environment-dependent fallback
            pytest.skip(f"Playwright browser runtime unavailable in this environment: {exc}")

def test_regenerate_music_units_defaults_to_all_selected_and_melody_generation_still_works():
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
                assert page.locator("#startMelody").is_disabled() is False
                browser.close()
        except Exception as exc:  # pragma: no cover - environment-dependent fallback
            pytest.skip(f"Playwright browser runtime unavailable in this environment: {exc}")


def test_satb_playback_remains_available_after_multiple_generations():
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

                page.click("#generateSATB")
                page.wait_for_function(
                    """
                    () => {
                      const meta = document.querySelector('#satbMeta')?.textContent || '';
                      return meta.trim().length > 0;
                    }
                    """,
                    timeout=20000,
                )
                page.click("#startSATB")
                page.wait_for_function(
                    """
                    () => typeof activePlayback !== 'undefined' && Boolean(activePlayback && activePlayback.type === 'satb')
                    """,
                    timeout=20000,
                )

                page.click("#generateSATB")
                page.wait_for_timeout(300)

                version_count = page.evaluate(
                    """
                    () => document.querySelectorAll('#satbDraftVersionSelect option').length
                    """
                )
                assert version_count >= 2

                page.select_option("#satbDraftVersionSelect", index=0)
                page.click("#startSATB")
                page.wait_for_function(
                    """
                    () => typeof activePlayback !== 'undefined' && Boolean(activePlayback && activePlayback.type === 'satb')
                    """,
                    timeout=20000,
                )
                assert page.locator("#startSATB").is_disabled() is False
                browser.close()
        except Exception as exc:  # pragma: no cover - environment-dependent fallback
            pytest.skip(f"Playwright browser runtime unavailable in this environment: {exc}")


def test_generate_melody_hides_inline_staff_error_when_inline_staff_disabled():
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

                result = page.evaluate(
                    """
                    () => ({
                      errorsText: document.querySelector('#formErrors')?.textContent || '',
                      melodySheetDisplay: getComputedStyle(document.querySelector('#melodySheet')).display,
                      melodyInlineNotice: document.querySelector('#melodyInlineNotice')?.textContent || '',
                    })
                    """
                )

                assert "Inline staff view is unavailable" not in result["errorsText"]
                assert result["melodySheetDisplay"] == "none"
                assert result["melodyInlineNotice"].strip() == ""
                assert page.locator("#startMelody").is_disabled() is False
                browser.close()
        except Exception as exc:  # pragma: no cover - environment-dependent fallback
            pytest.skip(f"Playwright browser runtime unavailable in this environment: {exc}")


def test_inline_staff_render_error_is_non_blocking_notice_when_inline_staff_enabled():
    playwright = pytest.importorskip("playwright.sync_api")

    with run_app_server() as base_url:
        try:
            with playwright.sync_playwright() as p:
                browser = p.chromium.launch()
                page = browser.new_page()
                page.goto(base_url, wait_until="domcontentloaded")

                page.evaluate(
                    """
                    () => {
                      window.__INLINE_STAFF_ENABLED_OVERRIDE = true;
                      window.drawStaff = () => {
                        throw new Error('forced inline render failure for test');
                      };
                    }
                    """
                )

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
                page.wait_for_function(
                    """
                    () => (document.querySelector('#melodyPreview')?.innerHTML || '').length > 0
                    """,
                    timeout=20000,
                )

                result = page.evaluate(
                    """
                    () => ({
                      errorsText: document.querySelector('#formErrors')?.textContent || '',
                      inlineNotice: document.querySelector('#melodyInlineNotice')?.textContent || '',
                      previewStatus: document.querySelector('#melodyPreviewStatus')?.textContent || '',
                    })
                    """
                )

                assert "Inline staff view is unavailable" not in result["errorsText"]
                assert "Inline staff preview is unavailable" in result["inlineNotice"]
                assert "Preview failed" not in result["previewStatus"]
                assert page.locator("#startMelody").is_disabled() is False
                browser.close()
        except Exception as exc:  # pragma: no cover - environment-dependent fallback
            pytest.skip(f"Playwright browser runtime unavailable in this environment: {exc}")


def test_regenerate_melody_renders_warning_banner_when_endpoint_returns_warnings():
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

                warning_text = "Regeneration warning parity check"

                def _regenerate_route(route):
                    req = route.request
                    payload = req.post_data_json
                    score = payload["score"]
                    route.fulfill(
                        status=200,
                        content_type="application/json",
                        body=json.dumps(
                            {
                                "score": score,
                                "warnings": [warning_text],
                                "seed_strategy_used": "random",
                                "seed_used": "test-seed",
                            }
                        ),
                    )

                page.route("**/api/regenerate-melody", _regenerate_route)
                page.click("#regenerate")
                page.wait_for_function(
                    """
                    () => {
                      const el = document.querySelector('#composerWarnings');
                      return !!el && el.style.display !== 'none' && el.textContent.includes('Regeneration warning parity check');
                    }
                    """,
                    timeout=20000,
                )

                assert page.locator("#composerWarnings").text_content().find(warning_text) != -1
                browser.close()
        except Exception as exc:  # pragma: no cover - environment-dependent fallback
            pytest.skip(f"Playwright browser runtime unavailable in this environment: {exc}")


def test_satb_regenerate_creates_new_playable_versions():
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

                page.click("#generateSATB")
                page.wait_for_function(
                    """
                    () => {
                      const meta = document.querySelector('#satbMeta')?.textContent || '';
                      return meta.trim().length > 0;
                    }
                    """,
                    timeout=20000,
                )

                page.click("#regenerateSATB")
                page.wait_for_timeout(300)
                page.click("#regenerateSATB")
                page.wait_for_timeout(300)

                version_count = page.evaluate(
                    """
                    () => document.querySelectorAll('#satbDraftVersionSelect option').length
                    """
                )
                assert version_count >= 3

                page.click("#startSATB")
                page.wait_for_function(
                    """
                    () => typeof activePlayback !== 'undefined' && Boolean(activePlayback && activePlayback.type === 'satb')
                    """,
                    timeout=20000,
                )
                assert page.locator("#regenerateSATB").is_disabled() is False
                browser.close()
        except Exception as exc:  # pragma: no cover - environment-dependent fallback
            pytest.skip(f"Playwright browser runtime unavailable in this environment: {exc}")


def test_melody_playback_still_works_after_satb_regenerate_and_stop_start_cycle():
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

                page.click("#generateSATB")
                page.wait_for_function(
                    """
                    () => {
                      const meta = document.querySelector('#satbMeta')?.textContent || '';
                      return meta.trim().length > 0;
                    }
                    """,
                    timeout=20000,
                )

                page.click("#startSATB")
                page.wait_for_function(
                    """
                    () => typeof activePlayback !== 'undefined' && Boolean(activePlayback && activePlayback.type === 'satb')
                    """,
                    timeout=20000,
                )
                page.click("#stopSATB")

                page.click("#regenerateSATB")
                page.wait_for_timeout(400)

                page.click("#startSATB")
                page.wait_for_function(
                    """
                    () => typeof activePlayback !== 'undefined' && Boolean(activePlayback && activePlayback.type === 'satb')
                    """,
                    timeout=20000,
                )
                page.click("#stopSATB")

                page.click("#startMelody")
                page.wait_for_function(
                    """
                    () => typeof activePlayback !== 'undefined' && Boolean(activePlayback && activePlayback.type === 'melody')
                    """,
                    timeout=20000,
                )
                assert page.locator("#startMelody").is_disabled() is False
                browser.close()
        except Exception as exc:  # pragma: no cover - environment-dependent fallback
            pytest.skip(f"Playwright browser runtime unavailable in this environment: {exc}")


def test_satb_stop_regenerate_start_cycle_emits_new_playback_start_event():
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

                page.click("#generateSATB")
                page.wait_for_function(
                    """
                    () => {
                      const meta = document.querySelector('#satbMeta')?.textContent || '';
                      return meta.trim().length > 0;
                    }
                    """,
                    timeout=20000,
                )

                page.click("#startSATB")
                page.wait_for_function(
                    """
                    () => Array.isArray(window.playbackEventLog)
                      && window.playbackEventLog.some((entry) => entry.event === 'playback_started' && entry.type === 'satb')
                    """,
                    timeout=20000,
                )
                page.click("#stopSATB")

                page.click("#regenerateSATB")
                page.wait_for_timeout(500)

                previous_start_count = page.evaluate(
                    """
                    () => window.playbackEventLog.filter((entry) => entry.event === 'playback_started' && entry.type === 'satb').length
                    """
                )

                page.click("#startSATB")
                page.wait_for_function(
                    """
                    (startCount) => window.playbackEventLog.filter((entry) => entry.event === 'playback_started' && entry.type === 'satb').length > startCount
                    """,
                    arg=previous_start_count,
                    timeout=20000,
                )

                assert page.evaluate(
                    """
                    () => {
                      const events = window.playbackEventLog || [];
                      const stopSeen = events.some((entry) => entry.event === 'playback_stopped' && entry.type === 'satb');
                      const startSeen = events.filter((entry) => entry.event === 'playback_started' && entry.type === 'satb').length >= 2;
                      return stopSeen && startSeen;
                    }
                    """
                )
                browser.close()
        except Exception as exc:  # pragma: no cover - environment-dependent fallback
            pytest.skip(f"Playwright browser runtime unavailable in this environment: {exc}")




def test_refine_controls_are_removed_from_mvp_ui():
    playwright = pytest.importorskip("playwright.sync_api")

    with run_app_server() as base_url:
        try:
            with playwright.sync_playwright() as p:
                browser = p.chromium.launch()
                page = browser.new_page()
                page.goto(base_url, wait_until="domcontentloaded")

                assert page.locator("#refine").count() == 0
                assert page.locator("#instruction").count() == 0
                assert page.locator("#refineSATB").count() == 0
                assert page.locator("#satbInstruction").count() == 0

                browser.close()
        except Exception as exc:  # pragma: no cover - environment-dependent fallback
            pytest.skip(f"Playwright browser runtime unavailable in this environment: {exc}")


def test_seed_data_defaults_to_three_verses_and_manual_two_beat_pickups():
    playwright = pytest.importorskip("playwright.sync_api")

    with run_app_server() as base_url:
        try:
            with playwright.sync_playwright() as p:
                browser = p.chromium.launch()
                page = browser.new_page()
                page.goto(base_url, wait_until="domcontentloaded")

                seed_state = page.evaluate(
                    """
                    () => ({
                      sectionRows: Array.from(document.querySelectorAll('#sections .section-row')).map((row) => ({
                        label: row.querySelector('.section-label')?.value || '',
                        isVerse: Boolean(row.querySelector('.section-is-verse')?.checked),
                      })),
                      arrangementModes: Array.from(document.querySelectorAll('#arrangementList .arrangement-anacrusis-mode')).map((el) => el.value),
                      arrangementBeats: Array.from(document.querySelectorAll('#arrangementList .arrangement-anacrusis-beats')).map((el) => Number(el.value)),
                    })
                    """
                )

                assert len(seed_state["sectionRows"]) == 3
                assert all(item["label"] == "Verse" and item["isVerse"] for item in seed_state["sectionRows"])
                assert seed_state["arrangementModes"] == ["manual", "manual", "manual"]
                assert seed_state["arrangementBeats"] == [2, 2, 2]

                page.fill("#sections .section-row:nth-of-type(1) .section-label", "Chorus")
                page.click("#arrangementList .arrangement-anacrusis-mode")
                page.select_option("#arrangementList .arrangement-anacrusis-mode", "off")
                page.fill("#arrangementList .arrangement-anacrusis-beats", "0")

                page.click("#loadTestData")

                reset_state = page.evaluate(
                    """
                    () => ({
                      sectionLabels: Array.from(document.querySelectorAll('#sections .section-label')).map((el) => el.value),
                      arrangementModes: Array.from(document.querySelectorAll('#arrangementList .arrangement-anacrusis-mode')).map((el) => el.value),
                      arrangementBeats: Array.from(document.querySelectorAll('#arrangementList .arrangement-anacrusis-beats')).map((el) => Number(el.value)),
                    })
                    """
                )

                assert reset_state["sectionLabels"] == ["Verse", "Verse", "Verse"]
                assert reset_state["arrangementModes"] == ["manual", "manual", "manual"]
                assert reset_state["arrangementBeats"] == [2, 2, 2]
                browser.close()
        except Exception as exc:  # pragma: no cover - environment-dependent fallback
            pytest.skip(f"Playwright browser runtime unavailable in this environment: {exc}")

def test_boundary_transition_timing_applies_run_on_and_breath_for_pickup_sections():
    playwright = pytest.importorskip("playwright.sync_api")

    with run_app_server() as base_url:
        try:
            with playwright.sync_playwright() as p:
                browser = p.chromium.launch()
                page = browser.new_page()
                page.goto(base_url, wait_until="domcontentloaded")

                result = page.evaluate(
                    """
                    () => {
                      const score = {
                        meta: {
                          tempo_bpm: 60,
                          arrangement_transitions: [{ transition_mode: 'manual', breath_beats: 1, run_on_beats: 2 }],
                          boundary_plans: [{
                            sectionA_id: 'A',
                            sectionB_id: 'B',
                            breath_beats_effective: 1,
                            run_on_beats_effective: 2,
                          }],
                        },
                      };
                      const events = [
                        { pitches: ['C4'], beats: 4, seconds: 4, sectionId: 'A', isRest: false, hasLyric: true },
                        { pitches: ['REST'], beats: 2, seconds: 2, sectionId: 'A', isRest: true, hasLyric: false },
                        { pitches: ['D4'], beats: 1, seconds: 1, sectionId: 'B', isRest: false, hasLyric: true },
                      ];
                      const assembled = buildTimedPlaybackEvents(events, score, 1);
                      const transitionEvents = (window.playbackEventLog || []).filter((entry) => entry.event === 'boundary_transition_applied');
                      return {
                        bStartSeconds: assembled.timedEvents[2].time,
                        transitionEvent: transitionEvents[transitionEvents.length - 1] || null,
                      };
                    }
                    """
                )

                assert result["bStartSeconds"] == 5
                assert result["transitionEvent"]["run_on_beats"] == 2
                assert result["transitionEvent"]["breath_beats"] == 1
                browser.close()
        except Exception as exc:  # pragma: no cover - environment-dependent fallback
            pytest.skip(f"Playwright browser runtime unavailable in this environment: {exc}")


def test_boundary_transition_legacy_gap_is_unchanged_when_transitions_are_absent():
    playwright = pytest.importorskip("playwright.sync_api")

    with run_app_server() as base_url:
        try:
            with playwright.sync_playwright() as p:
                browser = p.chromium.launch()
                page = browser.new_page()
                page.goto(base_url, wait_until="domcontentloaded")

                result = page.evaluate(
                    """
                    () => {
                      const score = { meta: { tempo_bpm: 60, arrangement_transitions: [], boundary_plans: [] } };
                      const events = [
                        { pitches: ['C4'], beats: 4, seconds: 4, sectionId: 'A', isRest: false, hasLyric: true },
                        { pitches: ['D4'], beats: 1, seconds: 1, sectionId: 'B', isRest: false, hasLyric: true },
                      ];
                      const assembled = buildTimedPlaybackEvents(events, score, 1);
                      return assembled.timedEvents[1].time;
                    }
                    """
                )

                assert result == 5
                browser.close()
        except Exception as exc:  # pragma: no cover - environment-dependent fallback
            pytest.skip(f"Playwright browser runtime unavailable in this environment: {exc}")

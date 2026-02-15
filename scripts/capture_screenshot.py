from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from playwright.sync_api import Error, TimeoutError, sync_playwright


def _browser_launch_kwargs(browser_name: str) -> dict:
    if browser_name != "chromium":
        return {"headless": True}

    args = [
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--no-zygote",
        "--single-process",
    ]
    return {"headless": True, "args": args}


def capture_screenshot(url: str, output_path: Path, timeout_ms: int = 30_000) -> tuple[str, list[dict]]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    errors: list[dict] = []

    with sync_playwright() as p:
        for browser_name in ("chromium", "firefox", "webkit"):
            browser_type = getattr(p, browser_name)
            try:
                browser = browser_type.launch(**_browser_launch_kwargs(browser_name))
                page = browser.new_page(viewport={"width": 1440, "height": 1080})
                try:
                    page.goto(url, wait_until="networkidle", timeout=timeout_ms)
                except TimeoutError:
                    page.goto(url, wait_until="load", timeout=timeout_ms)
                page.screenshot(path=str(output_path), full_page=True)
                browser.close()
                return browser_name, errors
            except Error as exc:
                errors.append({"browser": browser_name, "error": str(exc)})
            except Exception as exc:  # pragma: no cover
                errors.append({"browser": browser_name, "error": repr(exc)})

    raise RuntimeError(json.dumps({"message": "All Playwright browser engines failed to capture screenshot.", "attempts": errors}))


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture UI screenshot with container-safe Playwright fallback.")
    parser.add_argument("--url", required=True, help="URL to capture")
    parser.add_argument("--output", required=True, help="Output PNG path")
    parser.add_argument("--log", default="artifacts/screenshot-capture-log.json", help="Path to capture log JSON")
    parser.add_argument("--timeout-ms", type=int, default=30_000)
    args = parser.parse_args()

    output = Path(args.output)
    log_path = Path(args.log)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        browser, failures = capture_screenshot(args.url, output, args.timeout_ms)
        payload = {
            "status": "success",
            "url": args.url,
            "output": str(output),
            "browser": browser,
            "failures_before_success": failures,
            "ci": bool(os.getenv("CI")),
            "codespaces": bool(os.getenv("CODESPACES")),
        }
        log_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(json.dumps(payload, indent=2))
        return 0
    except Exception as exc:
        details = str(exc)
        attempts = []
        try:
            parsed = json.loads(details)
            details = parsed.get("message", details)
            attempts = parsed.get("attempts", [])
        except json.JSONDecodeError:
            pass
        payload = {
            "status": "failure",
            "url": args.url,
            "output": str(output),
            "error": details,
            "attempts": attempts,
            "ci": bool(os.getenv("CI")),
            "codespaces": bool(os.getenv("CODESPACES")),
        }
        log_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(json.dumps(payload, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

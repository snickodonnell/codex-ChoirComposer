import json
import subprocess
from pathlib import Path


APP_JS_PATH = Path(__file__).resolve().parents[1] / "app" / "static" / "app.js"


def _extract_named_function(source: str, function_name: str) -> str:
    marker = f"function {function_name}("
    start = source.find(marker)
    if start == -1:
        raise AssertionError(f"Could not find function {function_name!r} in app.js")

    brace_start = source.find("{", start)
    if brace_start == -1:
        raise AssertionError(f"Could not parse function {function_name!r} body")

    depth = 0
    for idx in range(brace_start, len(source)):
        ch = source[idx]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return source[start : idx + 1]

    raise AssertionError(f"Unclosed function body for {function_name!r}")


def test_format_api_error_message_handles_fastapi_detail_object():
    source = APP_JS_PATH.read_text(encoding="utf-8")
    get_request_id_fn = _extract_named_function(source, "getRequestIdFromHeaders")
    formatter_fn = _extract_named_function(source, "formatApiErrorMessage")

    payload = {
        "response": {
            "headers": {"x-request-id": "req-123"},
            "data": {
                "detail": {
                    "message": "Melody generation failed. Please adjust inputs and try again.",
                    "request_id": "req-123",
                    "debug": {"code": "UPSTREAM_TIMEOUT"},
                }
            },
        }
    }

    script = f"""
{get_request_id_fn}
{formatter_fn}
const output = formatApiErrorMessage({json.dumps(payload)});
console.log(output);
"""
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)

    assert (
        result.stdout.strip()
        == "Melody generation failed. Please adjust inputs and try again. (request_id: req-123)"
    )

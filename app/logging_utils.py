from __future__ import annotations

import contextvars
import json
import logging
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Any

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
route_var: contextvars.ContextVar[str] = contextvars.ContextVar("route", default="-")
method_var: contextvars.ContextVar[str] = contextvars.ContextVar("method", default="-")


class RequestContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            record.request_id = request_id_var.get()
        if not hasattr(record, "route"):
            record.route = route_var.get()
        if not hasattr(record, "method"):
            record.method = method_var.get()
        if not hasattr(record, "event"):
            record.event = record.msg if isinstance(record.msg, str) else "log"
        if not hasattr(record, "status_code"):
            record.status_code = None
        return True


class StructuredFormatter(logging.Formatter):
    def __init__(self, json_output: bool) -> None:
        super().__init__()
        self.json_output = json_output

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "event": getattr(record, "event", "log"),
            "request_id": getattr(record, "request_id", "-"),
            "route": getattr(record, "route", "-"),
            "method": getattr(record, "method", "-"),
        }
        status_code = getattr(record, "status_code", None)
        if status_code is not None:
            payload["status_code"] = status_code
        message = record.getMessage()
        if message and message != payload["event"]:
            payload["message"] = message

        for key, value in record.__dict__.items():
            if key.startswith("_") or key in {
                "name",
                "msg",
                "args",
                "levelname",
                "levelno",
                "pathname",
                "filename",
                "module",
                "exc_info",
                "exc_text",
                "stack_info",
                "lineno",
                "funcName",
                "created",
                "msecs",
                "relativeCreated",
                "thread",
                "threadName",
                "processName",
                "process",
                "request_id",
                "route",
                "method",
                "event",
                "status_code",
                "message",
            }:
                continue
            payload[key] = value

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        if self.json_output:
            return json.dumps(payload, default=str)

        ordered = [
            f"timestamp={payload.pop('timestamp')}",
            f"level={payload.pop('level')}",
            f"event={payload.pop('event')}",
            f"request_id={payload.pop('request_id')}",
            f"method={payload.pop('method')}",
            f"route={payload.pop('route')}",
        ]
        if "status_code" in payload:
            ordered.append(f"status_code={payload.pop('status_code')}")
        ordered.extend(f"{k}={v}" for k, v in payload.items())
        return " ".join(ordered)


def configure_logging() -> None:
    root = logging.getLogger()
    if getattr(root, "_choir_logging_configured", False):
        return

    level = os.getenv("LOG_LEVEL", "INFO").upper()
    json_output = os.getenv("LOG_FORMAT", "text").lower() == "json"

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(StructuredFormatter(json_output=json_output))
    context_filter = RequestContextFilter()
    handler.addFilter(context_filter)

    root.handlers.clear()
    root.addHandler(handler)
    root.addFilter(context_filter)
    root.setLevel(level)
    root._choir_logging_configured = True  # type: ignore[attr-defined]


def set_request_context(*, request_id: str, route: str, method: str) -> None:
    request_id_var.set(request_id)
    route_var.set(route)
    method_var.set(method)


def clear_request_context() -> None:
    request_id_var.set("-")
    route_var.set("-")
    method_var.set("-")


def current_request_id() -> str:
    return request_id_var.get()


def new_request_id() -> str:
    return str(uuid.uuid4())


def log_event(logger: logging.Logger, event: str, level: int = logging.INFO, **fields: Any) -> None:
    logger.log(level, event, extra={"event": event, **fields})


def request_elapsed_ms(start_time: float) -> int:
    return int((time.perf_counter() - start_time) * 1000)

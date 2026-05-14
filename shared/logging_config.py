"""TubeIntel logging setup.

Dual-output: human-readable to stdout (captured by Docker), ECS-style JSON to
a rotating file for future Filebeat/ELK ingest.

Both handlers use `structlog.stdlib.ProcessorFormatter` so structlog's event
dict is rendered to the correct format per-handler instead of being repr'd
into the message string. structlog's `contextvars` integration is what makes
`video_id` (and any other field bound via `bind_contextvars`) propagate
automatically through nested async calls — no need to thread the ID through
every signature.
"""

import json
import logging
import logging.handlers
import os
import sys
from datetime import datetime, timezone

import structlog
from structlog.contextvars import merge_contextvars


_LOGGING_CONFIGURED = False


def _get_service_name() -> str:
    return os.environ.get("SERVICE_NAME", "tube-intel")


def _supports_color() -> bool:
    # Disable ANSI color when stdout isn't a tty (e.g., Docker JSON file driver,
    # CI logs) to avoid escape codes cluttering the captured stream.
    return sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def _ecs_renderer(_logger, _name, event_dict: dict) -> str:
    """Render structlog event_dict to a single-line JSON string using ECS-style keys."""
    # The standard fields are renamed; everything else passes through.
    timestamp = event_dict.pop("timestamp", None) or datetime.now(timezone.utc).isoformat()
    level = event_dict.pop("level", None)
    logger_name = event_dict.pop("logger", None)
    event = event_dict.pop("event", None)
    out = {
        "@timestamp": timestamp,
        "log.level": level,
        "service.name": _get_service_name(),
        "log.logger": logger_name,
        "message": event,
    }
    # exc_info/stack_info come pre-formatted from format_exc_info / StackInfoRenderer
    if "exception" in event_dict:
        out["error.stack_trace"] = event_dict.pop("exception")
    if "stack" in event_dict:
        out["error.stack_info"] = event_dict.pop("stack")
    out.update(event_dict)
    return json.dumps(out, default=str)


def _console_renderer(_logger, _name, event_dict: dict) -> str:
    """Single-line human-readable: `<ts> <LEVEL> [<service>] <logger>: <event>  k=v k=v`."""
    ts = event_dict.pop("timestamp", None)
    if ts and "T" in ts:
        # Trim ISO timestamp to seconds for readability: 2026-05-13T22:10:17.802 → 22:10:17
        ts = ts.split("T", 1)[1].split(".", 1)[0]
    else:
        ts = datetime.now().strftime("%H:%M:%S")
    level = (event_dict.pop("level", "info") or "info").upper()
    logger_name = event_dict.pop("logger", "")
    event = event_dict.pop("event", "")

    color = ""
    reset = ""
    if _supports_color():
        colors = {"DEBUG": "\033[36m", "INFO": "\033[32m", "WARNING": "\033[33m",
                  "ERROR": "\033[31m", "CRITICAL": "\033[35m"}
        color = colors.get(level, "")
        reset = "\033[0m" if color else ""

    exc = event_dict.pop("exception", None)
    stack = event_dict.pop("stack", None)

    extras = " ".join(f"{k}={v}" for k, v in event_dict.items())
    line = f"{ts} {color}{level:<7}{reset} [{_get_service_name()}] {logger_name}: {event}"
    if extras:
        line += f"  {extras}"
    if exc:
        line += f"\n{exc}"
    if stack:
        line += f"\n{stack}"
    return line


def setup_logging(service_name: str | None = None) -> None:
    """Configure root logger with console + rotating JSON file handlers.

    Safe to call multiple times — second call is a no-op so tests that
    instantiate app factories don't accumulate handlers.
    """
    global _LOGGING_CONFIGURED
    if _LOGGING_CONFIGURED:
        return

    if service_name:
        os.environ["SERVICE_NAME"] = service_name

    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    log_file = os.environ.get("LOG_FILE", "/logs/tube-intel.log")

    # Processors that run for every event from a structlog logger AND for foreign
    # (stdlib logging) records routed through ProcessorFormatter. Kept here so
    # both handlers' foreign_pre_chain can reuse the exact same chain.
    shared_processors = [
        merge_contextvars,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    structlog.configure(
        processors=shared_processors + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    console_formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            _console_renderer,
        ],
    )

    json_formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            _ecs_renderer,
        ],
    )

    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):
        root.removeHandler(h)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(console_formatter)
    root.addHandler(console)

    log_dir = os.path.dirname(log_file)
    if log_dir:
        try:
            os.makedirs(log_dir, exist_ok=True)
            file_handler = logging.handlers.RotatingFileHandler(
                log_file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
            )
            file_handler.setFormatter(json_formatter)
            root.addHandler(file_handler)
        except OSError:
            # Volume not mounted or permission denied — stdout-only is fine.
            # Lets tests and local-dev runs work without /logs.
            pass

    # Silence the noisier libraries unless explicitly asked for DEBUG.
    if level > logging.DEBUG:
        for noisy in ("httpx", "httpcore", "discord", "discord.client",
                      "discord.gateway", "apscheduler", "werkzeug"):
            logging.getLogger(noisy).setLevel(logging.WARNING)

    _LOGGING_CONFIGURED = True


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)


def log_keys_present(**keys: str) -> dict:
    """Return a {name: bool} dict for startup banners. Never logs the values."""
    return {f"{name}_present": bool(value) for name, value in keys.items()}

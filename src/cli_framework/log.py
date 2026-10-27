"""One logging system, two streams, redacted at the handler.

Nothing in an app built on cli_framework calls `print` (ruff rule T20 enforces it):

- **diagnostics** go to stderr under `--log-level`, through `get_logger(...)`;
- **primary output** (a report, a table, a value a script pipes on) goes to stdout through
  `emit(...)`, a logger the level never suppresses.

So `app report > out.txt` keeps the report at any log level, and `app show | jq` works with
`--log-level DEBUG`.

The redaction filter sits on the **handlers**. A logger's own filter does not see records
propagated from its child loggers, so a filter on the root logger silently misses every
`app.module` logger.

`configure` returns a `LogSetup`; the app keeps it (`App.logging`) and uses it to add the
run's own log file once the run directory exists. No module state is mutated after import.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

from .redact import REDACTOR, Redactor

ROOT = "cli_framework"
OUTPUT = "cli_framework.output"


def get_logger(name: str) -> logging.Logger:
    """A diagnostics logger. A dotted name ("mytool.cases") is used as is; a bare name
    ("publish") goes under cli_framework's tree. `configure` gives both trees the same handlers."""
    return logging.getLogger(name if "." in name or name == ROOT else f"{ROOT}.{name}")


def emit(message: str = "") -> None:
    """One line of primary output on stdout, never suppressed by --log-level."""
    logging.getLogger(OUTPUT).info("%s", message)


def use_color(stream: TextIO, no_color: bool = False) -> bool:
    """Colour only for a terminal, and never with NO_COLOR or --no-color."""
    if no_color or os.environ.get("NO_COLOR"):
        return False
    return bool(getattr(stream, "isatty", lambda: False)())


class RedactFilter(logging.Filter):
    """Scrub a record's message and traceback before any formatter sees them."""

    def __init__(self, redactor: Redactor = REDACTOR):
        super().__init__()
        self.redactor = redactor

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 -- a formatting error must not lose the record
            message = str(record.msg)
        record.msg, record.args = self.redactor.scrub(message), ()
        if record.exc_info:
            text = logging.Formatter().formatException(record.exc_info)
            record.exc_text, record.exc_info = self.redactor.scrub(text), None
        return True


class ConsoleFormatter(logging.Formatter):
    def __init__(self, color: bool):
        super().__init__()
        self.color = color

    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        if record.exc_text:
            message = f"{message}\n{record.exc_text}"
        if record.levelno < logging.WARNING:
            return message
        label = record.levelname.lower()
        if self.color:
            label = f"\033[{'31' if record.levelno >= logging.ERROR else '33'}m{label}\033[0m"
        return f"{label}: {message}"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        body = {
            "ts": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_text:
            body["exc"] = record.exc_text
        return json.dumps(body)


class FileFormatter(logging.Formatter):
    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)s %(name)s: %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        return f"{text}\n{record.exc_text}" if record.exc_text else text


@dataclass
class LogSetup:
    """The handlers `configure` installed, and the trees they serve."""

    trees: tuple[str, ...]
    redactor: Redactor
    handlers: list[logging.Handler] = field(default_factory=list)

    def add_file(self, path: Path, level: int = logging.DEBUG) -> logging.Handler:
        """A redacted file handler on every tree, e.g. the run's own log once the run
        directory exists."""
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(path, encoding="utf-8")
        handler.setLevel(level)
        handler.setFormatter(FileFormatter())
        handler.addFilter(RedactFilter(self.redactor))
        self._attach(handler)
        return handler

    def remove(self, handler: logging.Handler) -> None:
        for name in self.trees:
            logging.getLogger(name).removeHandler(handler)
        handler.close()
        if handler in self.handlers:
            self.handlers.remove(handler)

    def adopt(self, *names: str) -> None:
        """Route third-party loggers (e.g. "httpx", "botocore") through the same redacted
        handlers, so their DEBUG output cannot leak a header."""
        self.trees = tuple(dict.fromkeys((*self.trees, *names)))
        for name in names:
            logger = logging.getLogger(name)
            logger.handlers = list(self.handlers)
            logger.setLevel(logging.DEBUG)
            logger.propagate = False

    def _attach(self, handler: logging.Handler) -> None:
        self.handlers.append(handler)
        for name in self.trees:
            logging.getLogger(name).addHandler(handler)


def configure(
    *,
    app: str = ROOT,
    level: str = "INFO",
    fmt: str = "text",
    log_file: Path | None = None,
    no_color: bool = False,
    redactor: Redactor = REDACTOR,
    stderr: TextIO | None = None,
    stdout: TextIO | None = None,
) -> LogSetup:
    """Install both streams. Call once, from the CLI, before anything logs. `app` is the
    app's own logger tree; cli_framework's tree gets the same handlers."""
    setup = LogSetup(tuple(dict.fromkeys((ROOT, app))), redactor)
    for name in setup.trees:
        tree = logging.getLogger(name)
        for old in tree.handlers:
            old.close()
        tree.handlers.clear()
        tree.setLevel(logging.DEBUG)
        tree.propagate = False

    err = stderr or sys.stderr
    console = logging.StreamHandler(err)
    console.setLevel(getattr(logging, level.upper(), logging.INFO))
    console.setFormatter(
        JsonFormatter() if fmt == "json" else ConsoleFormatter(use_color(err, no_color))
    )
    console.addFilter(RedactFilter(redactor))
    setup._attach(console)
    if log_file is not None:
        setup.add_file(log_file)

    output = logging.getLogger(OUTPUT)
    output.handlers.clear()
    output.setLevel(logging.INFO)
    output.propagate = False
    primary = logging.StreamHandler(stdout or sys.stdout)
    primary.setFormatter(logging.Formatter("%(message)s"))
    primary.addFilter(RedactFilter(redactor))
    output.addHandler(primary)
    return setup

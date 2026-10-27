"""Logging configuration with two output streams and redaction at the handlers.

Applications built on va do not call `print`; ruff rule T20 enforces this. Output is split
into two streams:

- Diagnostics are written to stderr, filtered by `--log-level`, through loggers obtained
  from `get_logger`.
- Primary output, such as a report, a table or a value that a script consumes, is written to
  stdout through `emit`, which uses a logger that the log level does not suppress.

As a result, `app report > out.txt` captures the report at any log level, and `app show | jq`
works with `--log-level DEBUG`.

The redaction filter is attached to the handlers rather than to loggers. A logger's own
filter does not see records propagated from its child loggers, so a filter on the root logger
would not apply to `app.module` loggers.

`configure` returns a `LogSetup`. The application keeps it (as `App.logging`) and uses it to
add the run's log file once the run directory exists. Module state is not modified after
import.
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

ROOT = "va"
OUTPUT = "va.output"


def get_logger(name: str) -> logging.Logger:
    """Return a diagnostics logger.

    A dotted name such as "mytool.cases" is used unchanged. A bare name such as "publish" is
    placed under va's logger tree. `configure` gives both trees the same handlers.

    Args:
        name: The logger name.

    Returns:
        The logger.
    """
    return logging.getLogger(name if "." in name or name == ROOT else f"{ROOT}.{name}")


def emit(message: str = "") -> None:
    """Write one line of primary output to stdout.

    The output logger is not affected by `--log-level`.

    Args:
        message: The line to write.
    """
    logging.getLogger(OUTPUT).info("%s", message)


def use_color(stream: TextIO, no_color: bool = False) -> bool:
    """Return whether to colour output written to a stream.

    Colour is used only when the stream is a terminal and neither the NO_COLOR environment
    variable nor `--no-color` is set.

    Args:
        stream: The stream output will be written to.
        no_color: Whether colour was disabled on the command line.

    Returns:
        True if output should be coloured.
    """
    if no_color or os.environ.get("NO_COLOR"):
        return False
    return bool(getattr(stream, "isatty", lambda: False)())


class RedactFilter(logging.Filter):
    """A logging filter that redacts a record's message and traceback before formatting.

    Args:
        redactor: The redactor to apply.
    """

    def __init__(self, redactor: Redactor = REDACTOR):
        super().__init__()
        self.redactor = redactor

    def filter(self, record: logging.LogRecord) -> bool:
        """Redact the record in place and allow it through.

        The message is formatted with its arguments before redaction, and any exception
        information is rendered to text, redacted and stored in `exc_text`.

        Args:
            record: The log record.

        Returns:
            True, so that the record is always emitted.
        """
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001
            # Emit the unformatted message if its arguments fail to format.
            message = str(record.msg)
        record.msg, record.args = self.redactor.scrub(message), ()
        if record.exc_info:
            text = logging.Formatter().formatException(record.exc_info)
            record.exc_text, record.exc_info = self.redactor.scrub(text), None
        return True


class ConsoleFormatter(logging.Formatter):
    """A formatter for diagnostics on the console.

    Records below WARNING are shown as the bare message. Warnings and errors are prefixed with
    the lower-case level name, coloured yellow or red when colour is enabled.

    Args:
        color: Whether to colour the level prefix.
    """

    def __init__(self, color: bool):
        super().__init__()
        self.color = color

    def format(self, record: logging.LogRecord) -> str:
        """Format a record for the console.

        Args:
            record: The log record.

        Returns:
            The formatted message, followed by the traceback text if there is one.
        """
        message = record.getMessage()
        if record.exc_text:
            message = f"{message}\n{record.exc_text}"
        if record.levelno < logging.WARNING:
            return message
        label = record.levelname.lower()
        if self.color:
            label = f"\033[{'31' if record.levelno >= logging.ERROR else '33'}m{label}\033[0m"
        return f"{label}: {message}"


class PrimaryOutputHandler(logging.StreamHandler):
    """A stream handler for primary output on stdout.

    When the reading end of a pipe closes, as in `app show | head -1`, the remaining output is
    discarded silently, as is conventional for Unix tools, instead of printing a traceback for
    each line.
    """

    def handleError(self, record: logging.LogRecord) -> None:
        """Handle an emit error, discarding further output after a broken pipe.

        Args:
            record: The record that could not be emitted.
        """
        if isinstance(sys.exc_info()[1], BrokenPipeError):
            self._discard_rest()
            return
        super().handleError(record)

    def _discard_rest(self) -> None:
        """Redirect the stream's file descriptor to the null device.

        After the redirect, the current write, later writes and the interpreter's final flush
        all succeed without output. Streams without a real file descriptor, such as StringIO,
        are left unchanged.
        """
        try:
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, self.stream.fileno())
            os.close(devnull)
        except (AttributeError, OSError, ValueError):  # No real descriptor, e.g. StringIO.
            pass


class JsonFormatter(logging.Formatter):
    """A formatter that renders each record as a single JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        """Format a record as JSON.

        Args:
            record: The log record.

        Returns:
            A JSON object with the timestamp, level, logger name, message and, when present,
            the traceback text.
        """
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
    """A formatter for log files, with timestamp, level and logger name on each line."""

    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)s %(name)s: %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        """Format a record for a log file.

        Args:
            record: The log record.

        Returns:
            The formatted line, followed by the traceback text if there is one.
        """
        text = super().format(record)
        return f"{text}\n{record.exc_text}" if record.exc_text else text


@dataclass
class LogSetup:
    """The handlers installed by `configure` and the logger trees they serve.

    Attributes:
        trees: The names of the logger trees that receive the handlers.
        redactor: The redactor used by the handlers' filters.
        handlers: The diagnostics handlers attached to every tree.
    """

    trees: tuple[str, ...]
    redactor: Redactor
    handlers: list[logging.Handler] = field(default_factory=list)

    def add_file(self, path: Path, level: int = logging.DEBUG) -> logging.Handler:
        """Add a redacted file handler to every tree.

        This is used, for example, to add the run's own log once the run directory exists.

        Args:
            path: The log file path. Parent directories are created.
            level: The minimum level written to the file.

        Returns:
            The new handler, which can later be passed to `remove`.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(path, encoding="utf-8")
        handler.setLevel(level)
        handler.setFormatter(FileFormatter())
        handler.addFilter(RedactFilter(self.redactor))
        self._attach(handler)
        return handler

    def remove(self, handler: logging.Handler) -> None:
        """Detach a handler from every tree and close it.

        Args:
            handler: The handler to remove.
        """
        for name in self.trees:
            logging.getLogger(name).removeHandler(handler)
        handler.close()
        if handler in self.handlers:
            self.handlers.remove(handler)

    def adopt(self, *names: str) -> None:
        """Route third-party loggers through the same redacted handlers.

        This applies redaction to libraries such as httpx and botocore, whose DEBUG output can
        include request headers.

        Args:
            *names: The names of the loggers to adopt.
        """
        self.trees = tuple(dict.fromkeys((*self.trees, *names)))
        for name in names:
            logger = logging.getLogger(name)
            logger.handlers = list(self.handlers)
            logger.setLevel(logging.DEBUG)
            logger.propagate = False

    def _attach(self, handler: logging.Handler) -> None:
        """Record a handler and add it to every tree.

        Args:
            handler: The handler to attach.
        """
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
    """Install the diagnostics and primary output streams.

    This is called once, from the CLI, before any logging takes place. Existing handlers on the
    configured trees are closed and replaced.

    Args:
        app: The application's own logger tree. va's tree receives the same handlers.
        level: The diagnostics log level name.
        fmt: The console format, "text" or "json".
        log_file: An optional file to receive all diagnostics at DEBUG level.
        no_color: Whether colour was disabled on the command line.
        redactor: The redactor used by every handler.
        stderr: The diagnostics stream. Defaults to `sys.stderr`.
        stdout: The primary output stream. Defaults to `sys.stdout`.

    Returns:
        The installed setup, used later to add or remove file handlers.
    """
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
    primary = PrimaryOutputHandler(stdout or sys.stdout)
    primary.setFormatter(logging.Formatter("%(message)s"))
    primary.addFilter(RedactFilter(redactor))
    output.addHandler(primary)
    return setup

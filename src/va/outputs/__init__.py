"""Opt-in run outputs, each a `RunHooks` that writes into the run directory before it is
published (and optionally to a path of your choosing):

- `HtmlReport`: report.html, one self-contained page (no scripts, no external assets);
- `JUnitReport`: junit.xml, for CI systems that render test results;
- `MetricsFile`: a Prometheus text-format file, for node_exporter's textfile collector or any
  scraper, so run health lands on the same dashboards as the infrastructure.

They are ordinary hooks: pass them in `Session(hooks=...)`, or let `RunOptions.hooks()` build
them from `--html`, `--junit` and `--metrics-file`.
"""

from .html import HtmlReport, render_html
from .junit import JUnitReport, render_junit
from .metrics import MetricsFile, render_metrics

__all__ = [
    "HtmlReport",
    "JUnitReport",
    "MetricsFile",
    "render_html",
    "render_junit",
    "render_metrics",
]

"""Optional run outputs, each a `RunHooks` that writes a file into the run directory.

Each output is written before the run directory is published, and can also be copied to a
chosen path:

- `HtmlReport`: report.html, a self-contained page with no scripts or external assets.
- `JUnitReport`: junit.xml, for CI systems that render test results.
- `MetricsFile`: a Prometheus text-format file, for node_exporter's textfile collector or
  another scraper, so that run health appears on the same dashboards as the infrastructure.

They are ordinary hooks. Pass them to `Session(hooks=...)`, or let `RunOptions.hooks()` build
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

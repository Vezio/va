"""Rendering of report.html, a self-contained page that summarises a run.

The page is readable offline and safe to publish. Its content comes only from the run's own
records, and every value is escaped and redacted. The page contains no scripts and loads no
external resources. Expandable sections use `<details>` elements.
"""

from __future__ import annotations

import json
from functools import cache
from html import escape
from importlib import resources
from pathlib import Path

from ..compare import Comparison
from ..hooks import RunHooks
from ..layout import RunFile
from ..model import ResultRecord, RunOutcome, Verdict
from ..report import RunReport
from ..rundir import RunDir


@cache
def _stylesheet() -> str:
    """Return the page stylesheet, read once from the package data file outputs/report.css."""
    return resources.files(__package__).joinpath("report.css").read_text(encoding="utf-8")


def _cell(value: object) -> str:
    """Return a value as escaped HTML text, serialising non-strings as JSON.

    Args:
        value: The value to render.

    Returns:
        The escaped text.
    """
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return escape(text)


def _checks(record: ResultRecord) -> str:
    """Render a record's checks as a collapsible table.

    Args:
        record: The result record whose checks are rendered.

    Returns:
        An HTML fragment, or a muted note when the record has no checks.
    """
    if not record.checks:
        return '<span class="muted">no checks recorded</span>'
    rows = "".join(
        f"<tr><td class=\"v {'pass' if c.ok else 'fail'}\">{'ok' if c.ok else 'not ok'}</td>"
        f"<td><code>{escape(c.name)}</code></td><td><pre>{_cell(c.observed)}</pre></td>"
        f"<td><pre>{_cell(c.expected)}</pre></td></tr>"
        for c in record.checks
    )
    return (
        "<details><summary>"
        f"{len(record.checks)} check(s)</summary><table><tr><th></th><th>check</th>"
        f"<th>observed</th><th>expected</th></tr>{rows}</table></details>"
    )


def render_html(
    outcome: RunOutcome, records: list[ResultRecord], comparison: Comparison | None = None
) -> str:
    """Render the HTML report page for a run.

    The page shows the title, counts, skip classes, preflight problems, the comparison with
    baseline runs when given, every case ordered with failures first, and links.

    Args:
        outcome: The run outcome.
        records: The run's result records.
        comparison: The comparison with baseline runs, if one was made.

    Returns:
        The complete HTML document.
    """
    report = RunReport.build(outcome, comparison)
    c = outcome.counts
    cards = "".join(
        f'<div class="card"><b class="{cls}">{n}</b>{label}</div>'
        for n, label, cls in (
            (c.passed, "passed", "pass"),
            (c.failed, "failed", "fail"),
            (c.errored, "errors", "error"),
            (c.skipped, "skipped", "skip"),
        )
    )
    meta = " · ".join(
        escape(x)
        for x in (
            f"run {outcome.run_id}",
            f"{outcome.tool} {outcome.tool_version}",
            *(f"{k}={v}" for k, v in outcome.labels.items()),
        )
    )
    parts = [
        f"<h1>{escape(report.title())}</h1><div class=muted>{meta}</div>",
        f"<p>{escape(report.meaning)}.</p><div class=cards>{cards}</div>",
    ]
    if outcome.skips:
        skips = ", ".join(f"{escape(k)} {n}" for k, n in outcome.skips.items())
        parts.append(f"<p class=muted>Skips by class: {skips}</p>")
    if outcome.preflight:
        items = "".join(
            f"<li>{escape(p.severity)} {escape(p.kind)}: {escape(p.message)}</li>"
            for p in outcome.preflight
        )
        parts.append(f"<h2>Preflight</h2><ul>{items}</ul>")
    if comparison:
        rows = "".join(
            f"<tr><td>{escape(ch.kind)}</td><td><code>{escape(str(ch.key))}</code></td>"
            f"<td>{escape('/'.join(ch.before) or '-')} → {escape(ch.after)}</td></tr>"
            for ch in comparison.changes
        )
        base = escape(", ".join(comparison.baseline_runs) or "nothing")
        body = f"<table>{rows}</table>" if rows else "<p class=muted>No change.</p>"
        parts.append(f"<h2>Compared with {base}</h2><div class=wrap>{body}</div>")
    order = {Verdict.FAIL: 0, Verdict.ERROR: 1, Verdict.PASS: 2, Verdict.SKIP: 3}
    rows = "".join(
        f'<tr><td class="v {r.verdict}">{r.verdict}</td><td>{escape(r.target)}</td>'
        f"<td><code>{escape(r.case_id)}</code>"
        + (f" <span class=muted>[{escape(r.variant)}]</span>" if r.variant else "")
        + f"</td><td>{escape(r.reason or r.skip_class or '')}{_checks(r)}</td>"
        f"<td>{r.duration_s:.2f}s</td></tr>"
        for r in sorted(records, key=lambda r: (order[r.verdict], r.target, r.case_id, r.variant))
    )
    parts.append(
        "<h2>Cases</h2><div class=wrap><table><tr><th>verdict</th><th>target</th><th>case</th>"
        f"<th>detail</th><th>time</th></tr>{rows}</table></div>"
    )
    if report.links:
        links = "".join(
            f'<li>{escape(n)}: <a href="{escape(u)}">{escape(u)}</a></li>'
            for n, u in report.links.items()
        )
        parts.append(f"<h2>Links</h2><ul>{links}</ul>")
    page = (
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        '<meta name=viewport content="width=device-width,initial-scale=1">'
        f"<title>{escape(outcome.tool)} run report</title><style>{_stylesheet()}</style></head>"
        f"<body><main>{''.join(parts)}</main></body></html>\n"
    )
    return page


class HtmlReport(RunHooks):
    """A run hook that writes report.html into the run directory and optionally copies it.

    The file in the run directory is published with the run.

    Args:
        copy_to: An optional additional path to write a redacted copy of the page to.
    """

    def __init__(self, copy_to: Path | None = None):
        self.copy_to = copy_to

    def on_finish(self, run: RunDir, outcome: RunOutcome) -> None:
        """Render the page from the run's results and comparison, and write it.

        Args:
            run: The finished run directory.
            outcome: The run outcome.
        """
        body = run.read_json(RunFile.COMPARISON)
        comparison = Comparison.from_dict(body) if body else None
        page = render_html(outcome, run.results(), comparison)
        run.write_text(RunFile.HTML, page)
        if self.copy_to:
            self.copy_to.parent.mkdir(parents=True, exist_ok=True)
            self.copy_to.write_text(run.redactor.scrub(page), encoding="utf-8")

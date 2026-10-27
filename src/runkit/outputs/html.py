"""report.html: one self-contained page for a run, readable offline and safe to publish.

Everything shown comes from the run's own records; every value is escaped and redacted. The
page has no scripts and loads nothing: `<details>` does the expanding.
"""

from __future__ import annotations

import json
from html import escape
from pathlib import Path

from ..compare import Comparison
from ..hooks import RunHooks
from ..model import ResultRecord, RunOutcome, Verdict
from ..report import RunReport
from ..rundir import COMPARISON, RunDir

FILENAME = "report.html"

_CSS = """
:root{--bg:#fff;--fg:#1d1d1f;--muted:#6e6e73;--line:#e5e5ea;--pass:#1a7f37;--fail:#cf222e;
--error:#9a6700;--skip:#6e6e73;--card:#f6f8fa}
@media (prefers-color-scheme:dark){:root{--bg:#0d1117;--fg:#e6edf3;--muted:#8b949e;
--line:#30363d;--pass:#3fb950;--fail:#f85149;--error:#d29922;--skip:#8b949e;--card:#161b22}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:14px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}main{max-width:1100px;margin:auto;
padding:24px 16px}h1{font-size:22px;margin:0 0 4px}h2{font-size:16px;margin:28px 0 8px}
.muted{color:var(--muted)}.cards{display:flex;flex-wrap:wrap;gap:8px;margin:16px 0}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:8px 14px;
min-width:90px}.card b{display:block;font-size:20px}table{width:100%;border-collapse:collapse}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-size:12px;color:var(--muted);font-weight:600}.v{font-weight:600;text-transform:uppercase;
font-size:12px}.pass{color:var(--pass)}.fail{color:var(--fail)}.error{color:var(--error)}
.skip{color:var(--skip)}code,pre{font:12px/1.4 ui-monospace,SFMono-Regular,Menlo,monospace}
pre{white-space:pre-wrap;word-break:break-word;margin:4px 0}details summary{cursor:pointer}
.wrap{overflow-x:auto}
"""


def _cell(value: object) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return escape(text)


def _checks(record: ResultRecord) -> str:
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
        f"<title>{escape(outcome.tool)} run report</title><style>{_CSS}</style></head>"
        f"<body><main>{''.join(parts)}</main></body></html>\n"
    )
    return page


class HtmlReport(RunHooks):
    """Writes report.html into the run directory (published with it), and to `copy_to`."""

    def __init__(self, copy_to: Path | None = None):
        self.copy_to = copy_to

    def on_finish(self, run: RunDir, outcome: RunOutcome) -> None:
        body = run.read_json(COMPARISON)
        comparison = Comparison.from_dict(body) if body else None
        page = render_html(outcome, run.results(), comparison)
        run.write_text(FILENAME, page)
        if self.copy_to:
            self.copy_to.parent.mkdir(parents=True, exist_ok=True)
            self.copy_to.write_text(run.redactor.scrub(page), encoding="utf-8")

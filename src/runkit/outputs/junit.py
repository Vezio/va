"""junit.xml: the run in the format most CI systems render as test results.

One `<testsuite>` per target, one `<testcase>` per case and variant. `fail` is a
`<failure>`, `error` an `<error>`, `skip` a `<skipped>` whose message names its skip class, and
each case's recorded checks go to `<system-out>`, so the evidence travels with the verdict.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

from ..hooks import RunHooks
from ..model import ResultRecord, RunOutcome, Verdict
from ..rundir import RunDir

FILENAME = "junit.xml"


def _checks_text(record: ResultRecord) -> str:
    return "\n".join(
        f"{'ok    ' if c.ok else 'NOT OK'} {c.name}: observed {json.dumps(c.observed, default=str)}"
        f", expected {json.dumps(c.expected, default=str)}"
        for c in record.checks
    )


def render_junit(outcome: RunOutcome, records: list[ResultRecord]) -> str:
    by_target: dict[str, list[ResultRecord]] = defaultdict(list)
    for record in records:
        by_target[record.target].append(record)
    root = ET.Element("testsuites", name=f"{outcome.tool} {outcome.run_id}")
    for target, items in sorted(by_target.items()):
        suite = ET.SubElement(
            root,
            "testsuite",
            name=target or "default",
            tests=str(len(items)),
            failures=str(sum(r.verdict is Verdict.FAIL for r in items)),
            errors=str(sum(r.verdict is Verdict.ERROR for r in items)),
            skipped=str(sum(r.verdict is Verdict.SKIP for r in items)),
            time=f"{sum(r.duration_s for r in items):.3f}",
        )
        for r in items:
            name = r.case_id + (f"[{r.variant}]" if r.variant else "")
            case = ET.SubElement(
                suite,
                "testcase",
                classname=target or "default",
                name=name,
                time=f"{r.duration_s:.3f}",
            )
            if r.verdict is Verdict.FAIL:
                check = r.failed_check
                ET.SubElement(
                    case, "failure", message=r.reason, type=check.name if check else "assertion"
                )
            elif r.verdict is Verdict.ERROR:
                ET.SubElement(case, "error", message=r.reason, type="error")
            elif r.verdict is Verdict.SKIP:
                ET.SubElement(case, "skipped", message=f"{r.skip_class}: {r.reason}")
            if r.checks:
                ET.SubElement(case, "system-out").text = _checks_text(r)
    ET.indent(root)
    return ET.tostring(root, encoding="unicode", xml_declaration=True) + "\n"


class JUnitReport(RunHooks):
    """Writes junit.xml into the run directory (published with it), and to `copy_to`."""

    def __init__(self, copy_to: Path | None = None):
        self.copy_to = copy_to

    def on_finish(self, run: RunDir, outcome: RunOutcome) -> None:
        text = render_junit(outcome, run.results())
        run.write_text(FILENAME, text)
        if self.copy_to:
            self.copy_to.parent.mkdir(parents=True, exist_ok=True)
            self.copy_to.write_text(run.redactor.scrub(text), encoding="utf-8")

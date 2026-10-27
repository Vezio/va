"""Rendering of junit.xml, the test result format that most CI systems display.

The document has one `<testsuite>` per target and one `<testcase>` per case and variant. A
failed case has a `<failure>` element, an errored case an `<error>` element, and a skipped case
a `<skipped>` element whose message names the skip class. Each case's recorded checks are
written to `<system-out>`, so the evidence for a verdict is included alongside it.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

from ..hooks import RunHooks
from ..layout import RunFile
from ..model import ResultRecord, RunOutcome, Verdict
from ..rundir import RunDir


def _checks_text(record: ResultRecord) -> str:
    """Return a record's checks as plain text, one line per check.

    Args:
        record: The result record whose checks are rendered.

    Returns:
        Lines giving each check's status, name, observed value and expected value.
    """
    return "\n".join(
        f"{'ok    ' if c.ok else 'NOT OK'} {c.name}: observed {json.dumps(c.observed, default=str)}"
        f", expected {json.dumps(c.expected, default=str)}"
        for c in record.checks
    )


def render_junit(outcome: RunOutcome, records: list[ResultRecord]) -> str:
    """Render a run's results as a JUnit XML document.

    Args:
        outcome: The run outcome, used to name the top-level `<testsuites>` element.
        records: The run's result records.

    Returns:
        The XML document, including its declaration.
    """
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
    """A run hook that writes junit.xml into the run directory and optionally copies it.

    The file in the run directory is published with the run.

    Args:
        copy_to: An optional additional path to write a redacted copy of the document to.
    """

    def __init__(self, copy_to: Path | None = None):
        self.copy_to = copy_to

    def on_finish(self, run: RunDir, outcome: RunOutcome) -> None:
        """Render the document from the run's results and write it.

        Args:
            run: The finished run directory.
            outcome: The run outcome.
        """
        text = render_junit(outcome, run.results())
        run.write_text(RunFile.JUNIT, text)
        if self.copy_to:
            self.copy_to.parent.mkdir(parents=True, exist_ok=True)
            self.copy_to.write_text(run.redactor.scrub(text), encoding="utf-8")

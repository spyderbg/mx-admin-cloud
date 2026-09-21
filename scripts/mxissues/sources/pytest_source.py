"""Maps a pytest JUnit XML report (--junit-xml) to Findings.

Fingerprint key is the node id (implementation_plan.md §11: "canonical and stable"),
reconstructed as `classname.replace(".", "/") + ".py::" + name` — verified against this
repo's own real --junit-xml output (junit_family=xunit2, the pytest 6+ default): each
<testcase> carries `classname` (dotted module path) and `name` (test function), but NOT
a `file` attribute, so this reconstruction is the only way to recover the
`src/.../test_x.py::test_y` shape pytest's own short summary line uses. Simplification:
this assumes function-style tests (`def test_x`), not class-based ones
(`TestX::test_y`, where the class name would wrongly become a path segment) — matches
every test in this repo today; revisit if that changes.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from ..models import Finding

# Named pytest_source.py, not pytest.py: this package's directory is never added to
# sys.path directly (only scripts/ itself is), so a same-named submodule wouldn't
# actually shadow the real pytest package today — kept distinct anyway as a defensive
# convention, not a fix for a live bug.


def _node_id(classname: str, name: str) -> str:
    return classname.replace(".", "/") + ".py::" + name


def _strip(text: str | None, limit: int = 1200) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit] + "\n... (truncated)"


def parse(xml_path: Path) -> list[Finding]:
    tree = ET.parse(xml_path)
    findings: list[Finding] = []

    for testcase in tree.getroot().iter("testcase"):
        failure = testcase.find("failure")
        error = testcase.find("error")
        node = failure if failure is not None else error
        if node is None:
            continue  # passed or skipped

        classname = testcase.attrib.get("classname", "")
        name = testcase.attrib.get("name", "")
        node_id = _node_id(classname, name)
        message = node.attrib.get("message", "").strip()
        kind = "failure" if failure is not None else "error"

        body = (
            f"**pytest {kind}:** `{node_id}`\n\n"
            f"**Message:** {message}\n\n"
            "**Traceback:**\n```\n" + _strip(node.text) + "\n```"
        )

        findings.append(Finding(
            source="pytest",
            title=f"pytest {kind}: {node_id}",
            body=body,
            fingerprint_key=node_id,
            severity="high" if kind == "error" else "medium",
            affected_files=(node_id.split("::", 1)[0],),
        ))

    return findings

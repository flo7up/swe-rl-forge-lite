"""Per-test result capture and scoping for pytest-based tasks.

The verifier runs the configured test command before and after the gold patch.
With these helpers it can additionally collect a per-test JUnit report from each
run and derive the SWE-bench-style sets:

- ``fail_to_pass``: tests that did not pass before the patch but pass after it —
  the tests the fix is responsible for.
- ``pass_to_pass``: tests that pass both before and after — regression guards.

A candidate fix is then scored against exactly those tests instead of the whole
suite's exit code, so unrelated failing/flaky tests in the repo don't mask the
signal. Everything here is pure and stdlib-only so the same logic can be inlined
into the standalone ``reward.py`` template.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

# A pytest run is wrapped so it writes a JUnit report and echoes it to stdout
# between these sentinels, preserving the original exit code. stdout is already
# captured by the Docker runner, so no volume mount or `docker cp` is needed.
REPORT_PATH = "/tmp/forge-report.xml"
REPORT_START = "<<<FORGE_JUNIT_START>>>"
REPORT_END = "<<<FORGE_JUNIT_END>>>"

_PYTEST_RE = re.compile(r"(^|\s)(pytest|py\.test)(\s|$)|python[0-9.]*\s+-m\s+pytest")


def is_pytest_command(test_command: str) -> bool:
    return bool(_PYTEST_RE.search(test_command or ""))


def wrap_pytest_command(test_command: str) -> str | None:
    """Wrap a pytest command to emit a JUnit report between sentinels on stdout.

    Returns None for non-pytest commands (the caller should run them unwrapped and
    fall back to whole-suite scoring). The original exit code is preserved so the
    existing fail-before/pass-after signals keep working.
    """

    if not is_pytest_command(test_command):
        return None
    inner = f"{test_command} --junitxml={REPORT_PATH}"
    return (
        f"{inner}; __forge_code=$?; "
        f"echo '{REPORT_START}'; cat {REPORT_PATH} 2>/dev/null; echo '{REPORT_END}'; "
        f"exit $__forge_code"
    )


def extract_report(stdout: str) -> str | None:
    """Pull the JUnit XML back out from between the sentinels in captured stdout."""

    if not stdout or REPORT_START not in stdout or REPORT_END not in stdout:
        return None
    start = stdout.index(REPORT_START) + len(REPORT_START)
    end = stdout.index(REPORT_END, start)
    xml = stdout[start:end].strip()
    return xml or None


def parse_junit_xml(xml_text: str | None) -> dict[str, str]:
    """Map ``classname::name`` -> status in {passed, failed, error, skipped}.

    The key is a stable identifier consistent across runs (we control the report
    format), not necessarily a runnable pytest node id.
    """

    results: dict[str, str] = {}
    if not xml_text:
        return results
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return results
    for case in root.iter("testcase"):
        classname = case.get("classname") or ""
        name = case.get("name") or ""
        nodeid = f"{classname}::{name}" if classname else name
        status = "passed"
        for child in case:
            tag = child.tag.lower()
            if tag in ("failure", "error", "skipped"):
                status = "failed" if tag == "failure" else tag
                break
        results[nodeid] = status
    return results


def passed_tests(results: dict[str, str]) -> set[str]:
    return {nodeid for nodeid, status in results.items() if status == "passed"}


def derive_test_deltas(before: dict[str, str], after: dict[str, str]) -> tuple[list[str], list[str]]:
    """Return (fail_to_pass, pass_to_pass) from before/after per-test result maps."""

    before_pass = passed_tests(before)
    after_pass = passed_tests(after)
    fail_to_pass = sorted(after_pass - before_pass)
    pass_to_pass = sorted(after_pass & before_pass)
    return fail_to_pass, pass_to_pass


def score_results(results: dict[str, str], fail_to_pass: list[str], pass_to_pass: list[str]) -> bool:
    """True iff every targeted test (fail_to_pass + pass_to_pass) passed."""

    ok = passed_tests(results)
    return all(t in ok for t in fail_to_pass) and all(t in ok for t in pass_to_pass)

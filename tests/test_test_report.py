from __future__ import annotations

from forge.test_report import (
    REPORT_END,
    REPORT_PATH,
    REPORT_START,
    derive_test_deltas,
    extract_report,
    is_pytest_command,
    parse_junit_xml,
    score_results,
    wrap_pytest_command,
)

SAMPLE_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" tests="4">
  <testcase classname="tests.test_m" name="test_pass"></testcase>
  <testcase classname="tests.test_m" name="test_fail"><failure message="boom">trace</failure></testcase>
  <testcase classname="tests.test_m" name="test_err"><error message="x">trace</error></testcase>
  <testcase classname="tests.test_m" name="test_skip"><skipped/></testcase>
</testsuite></testsuites>"""


def test_is_pytest_command() -> None:
    assert is_pytest_command("pytest")
    assert is_pytest_command("pytest -q tests/")
    assert is_pytest_command("python -m pytest")
    assert is_pytest_command("python3 -m pytest -k foo")
    assert not is_pytest_command("tox")
    assert not is_pytest_command("make test")
    assert not is_pytest_command("")


def test_wrap_pytest_command_roundtrips_through_extract() -> None:
    wrapped = wrap_pytest_command("pytest -q")
    assert wrapped is not None
    assert REPORT_PATH in wrapped
    assert "--junitxml" in wrapped
    assert "exit $__forge_code" in wrapped

    # Simulate the container stdout: test output, then the sentinel-wrapped report.
    stdout = f"collected 4 items\n.F\n{REPORT_START}\n{SAMPLE_XML}\n{REPORT_END}\n"
    assert extract_report(stdout).strip().startswith("<?xml")


def test_wrap_returns_none_for_non_pytest() -> None:
    assert wrap_pytest_command("make test") is None


def test_extract_report_handles_missing_sentinels() -> None:
    assert extract_report("no markers here") is None
    assert extract_report("") is None


def test_parse_junit_xml_classifies_each_status() -> None:
    results = parse_junit_xml(SAMPLE_XML)

    assert results == {
        "tests.test_m::test_pass": "passed",
        "tests.test_m::test_fail": "failed",
        "tests.test_m::test_err": "error",
        "tests.test_m::test_skip": "skipped",
    }


def test_parse_junit_xml_is_lenient_on_garbage() -> None:
    assert parse_junit_xml("not xml") == {}
    assert parse_junit_xml(None) == {}


def test_derive_test_deltas() -> None:
    before = {"a": "failed", "b": "passed", "c": "failed"}
    after = {"a": "passed", "b": "passed", "c": "failed", "d": "passed"}

    fail_to_pass, pass_to_pass = derive_test_deltas(before, after)

    assert fail_to_pass == ["a", "d"]  # a: fail->pass, d: new and passing
    assert pass_to_pass == ["b"]       # passed both runs
    assert "c" not in fail_to_pass and "c" not in pass_to_pass  # still failing


def test_score_results() -> None:
    fail_to_pass = ["a", "d"]
    pass_to_pass = ["b"]

    assert score_results({"a": "passed", "b": "passed", "d": "passed"}, fail_to_pass, pass_to_pass)
    # one fail_to_pass still failing -> not scored
    assert not score_results({"a": "passed", "b": "passed", "d": "failed"}, fail_to_pass, pass_to_pass)
    # a regression in pass_to_pass -> not scored
    assert not score_results({"a": "passed", "b": "failed", "d": "passed"}, fail_to_pass, pass_to_pass)


def test_sentinels_are_distinct() -> None:
    assert REPORT_START != REPORT_END

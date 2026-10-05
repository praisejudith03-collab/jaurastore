#!/usr/bin/env python3
"""Summarise a pytest JUnit report and audit what did NOT run.

A suite that skips is not a suite that passed. The owner's rule is that a
green build must mean "every test executed and every test passed", so this
prints the counts and, with --fail-on-skips, exits non-zero the moment any
test was skipped (listing the node ids and reasons).

CI runs it with --fail-on-skips: chromium, node and every declared test
dependency are installed there, so a skip in CI can only mean a broken
environment or a test that quietly opted out of running - both of which must
be red, not green.

    python tools/ci_report.py pytest-results.xml
    python tools/ci_report.py pytest-results.xml --fail-on-skips
"""
import sys
import xml.etree.ElementTree as ET


def summarise(path):
    """(counts, skipped) for a pytest JUnit report."""
    root = ET.parse(path).getroot()
    counts = {"tests": 0, "passed": 0, "failed": 0, "errors": 0, "skipped": 0}
    skipped = []
    for case in root.iter("testcase"):
        counts["tests"] += 1
        outcome = "passed"
        if case.find("failure") is not None:
            outcome = "failed"
        elif case.find("error") is not None:
            outcome = "errors"
        elif case.find("skipped") is not None:
            outcome = "skipped"
        counts[outcome] += 1
        if outcome == "skipped":
            node = case.find("skipped")
            reason = (node.get("message") or node.text or "").strip().splitlines()
            skipped.append((
                f"{case.get('classname', '')}::{case.get('name', '')}",
                reason[0] if reason else "no reason given",
            ))
    return counts, skipped


def main(argv):
    args = [a for a in argv[1:] if not a.startswith("--")]
    fail_on_skips = "--fail-on-skips" in argv[1:]
    if len(args) != 1:
        print("usage: ci_report.py REPORT.xml [--fail-on-skips]", file=sys.stderr)
        return 2

    try:
        counts, skipped = summarise(args[0])
    except (OSError, ET.ParseError) as exc:
        print(f"ci_report.py: cannot read {args[0]}: {exc}", file=sys.stderr)
        return 2

    print(
        f"ci_report: {counts['tests']} tests | {counts['passed']} passed | "
        f"{counts['failed']} failed | {counts['errors']} errors | "
        f"{counts['skipped']} skipped"
    )

    if skipped:
        print("\nci_report: tests that did NOT run:")
        for node, reason in skipped:
            print(f"  - {node}: {reason}")

    if counts["failed"] or counts["errors"]:
        return 1
    if fail_on_skips and counts["skipped"]:
        print(
            f"\nci_report: FAIL - {counts['skipped']} test(s) skipped in an environment "
            "that installs every dependency, so the run proves less than it claims. "
            "Install what the test needs or delete the test; do not let it skip.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

"""Run one mutation probe's pytest suite and record outcomes and failure sites."""
from __future__ import annotations

import json
import linecache
from pathlib import Path
import sys

import pytest


class FailureSites:
    def __init__(self, suite: str):
        self.suite = Path(suite).as_posix()
        self.failures: dict[str, list[str]] = {}
        self.errors: list[str] = []
        self.outcomes: dict[str, str] = {}
        self.completed_ids: set[str] = set()
        self.executed = 0
        self.deselected = 0

    def _label(self, nodeid: str) -> str:
        suite, _, test = nodeid.partition("::")
        if suite == self.suite and test:
            return test
        return nodeid or self.suite

    def pytest_deselected(self, items):
        self.deselected += len(items)

    def pytest_collection_finish(self, session):
        self.outcomes = {
            self._label(item.nodeid): "collected" for item in session.items
            if item.nodeid.partition("::")[0] == self.suite
        }

    def pytest_collectreport(self, report):
        if report.failed:
            self.errors.append(f"collect: {self._label(report.nodeid)}")

    def pytest_internalerror(self, excrepr, excinfo):
        reason = f"{excinfo.typename}: {excinfo.value}".partition("\n")[0]
        self.errors.append(f"internal: {reason}")

    def pytest_exception_interact(self, node, call, report):
        if report.when != "call" or not report.failed:
            return
        suite, _, test = report.nodeid.partition("::")
        if suite != self.suite:
            return

        exc = call.excinfo
        entry = exc.traceback[-1]
        # A missing pytest.raises exception originates in pytest's hidden frames.
        # For ordinary exceptions the innermost frame must be the test itself.
        if exc.type is pytest.fail.Exception and str(exc.value).startswith("DID NOT RAISE "):
            entry = next((frame for frame in reversed(exc.traceback)
                          if not frame.ishidden(exc)), None)
        statement = ""
        if entry is not None and entry.frame.code.raw is getattr(node.obj, "__code__", None):
            statement = linecache.getline(str(entry.path), entry.lineno + 1).strip()
        reason = f"{exc.typename}: {exc.value}".partition("\n")[0]
        self.failures[test] = [statement, exc.typename, reason]

    def pytest_runtest_logreport(self, report):
        suite, _, test = report.nodeid.partition("::")
        in_suite = suite == self.suite
        if report.when == "call":
            if in_suite:
                self.executed += 1
                self.outcomes[test] = report.outcome
                if report.failed and test not in self.failures:
                    first = report.longreprtext.strip().splitlines()
                    reason = first[0] if first else "failed"
                    self.failures[test] = ["", "Failed", reason]
            elif report.failed:
                self.errors.append(f"call: {report.nodeid}")
        elif report.failed:
            self.errors.append(f"{report.when}: {self._label(report.nodeid)}")

        if in_suite and report.skipped:
            self.outcomes[test] = "skipped"
        # Teardown marks the end of the protocol, even for a skipped test.
        if report.when == "teardown" and in_suite and test in self.outcomes:
            self.completed_ids.add(test)

    def pytest_sessionfinish(self, session, exitstatus):
        if session.testsfailed and not self.failures and not self.errors:
            self.errors.append(f"session: {session.testsfailed} failure(s)")

    def report(self) -> dict:
        return {
            "failures": self.failures,
            "errors": self.errors,
            "outcomes": self.outcomes,
            "executed": self.executed,
            "completed": len(self.completed_ids),
            "deselected": self.deselected,
        }


if __name__ == "__main__":
    suite, destination, *targets = sys.argv[1:]
    # Preserve the import path of the probes' usual `python -m pytest` call.
    sys.path.insert(0, str(Path.cwd()))
    sites = FailureSites(suite)
    # Explicit node IDs collect exactly the focused set and deselect nothing;
    # `suite` is kept as the identity the labels are taken from.
    rc = pytest.main([*(targets or [suite]), "-q", "--rootdir=.", "--no-header", "--tb=no",
                      "-p", "no:cacheprovider"], plugins=[sites])
    Path(destination).write_text(json.dumps(sites.report()))
    sys.exit(rc)

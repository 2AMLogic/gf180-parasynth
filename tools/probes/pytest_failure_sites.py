"""Run one mutation probe's pytest suite and record where each test failed."""
from __future__ import annotations

import json
import linecache
from pathlib import Path
import sys

import pytest


class FailureSites:
    def __init__(self, suite: str):
        self.suite = suite
        self.failures = {}

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


if __name__ == "__main__":
    suite, destination = sys.argv[1:]
    # Preserve the import path of the probes' usual `python -m pytest` call.
    sys.path.insert(0, str(Path.cwd()))
    sites = FailureSites(suite)
    rc = pytest.main([suite, "-q", "--rootdir=.", "--no-header", "--tb=no",
                      "-p", "no:cacheprovider"], plugins=[sites])
    Path(destination).write_text(json.dumps(sites.failures))
    sys.exit(rc)

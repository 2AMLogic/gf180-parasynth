"""Pytest child for crash_rate.py. The launcher reads progress even after a signal."""
import inspect
import os
import sys

import pytest


class TargetCalls:
    def __init__(self, target, progress_fd):
        self.target = target
        self.progress_fd = progress_fd
        self.started = False
        self.completed = False

    def pytest_sessionstart(self, session):
        # Check pytest's parsed selection, including config and PYTEST_ADDOPTS.
        if session.config.args != [self.target]:
            raise pytest.UsageError(f"additional pytest targets in {session.config.args!r}")

    @pytest.hookimpl(tryfirst=True)
    def pytest_runtest_call(self, item):
        # Credit the run only when execution reaches the requested callable.
        # unittest items run setUp inside the call phase, and an async def with
        # no async plugin fails (or is absorbed by xfail) before its body, so
        # entering the call phase is not evidence. Item types whose body entry
        # cannot be observed are left unwrapped and refuse (no tests exercised).
        if type(item) is not pytest.Function or inspect.iscoroutinefunction(item.obj):
            return
        original = item.obj

        def entered(*args, **kwargs):
            if not self.started:
                os.write(self.progress_fd, b"started\n")
                self.started = True
            return original(*args, **kwargs)

        item.obj = entered

    def pytest_runtest_logreport(self, report):
        # An executed xfail is a skipped call with wasxfail. A skip mark or
        # xfail(run=False) stops in setup without reaching the call.
        if report.when == "call" and self.started and (not report.skipped or hasattr(report, "wasxfail")):
            self.completed = True


def main(target, progress_fd):
    os.set_inheritable(progress_fd, False)
    if not sys.flags.safe_path:
        # Match python -m pytest's import path for the target.
        sys.path[0] = os.getcwd()
    calls = TargetCalls(target, progress_fd)
    status = pytest.main([target, "-q", "-p", "no:cacheprovider", "--no-header"], plugins=[calls])
    if status in (pytest.ExitCode.OK, pytest.ExitCode.TESTS_FAILED):
        if not (calls.started and calls.completed):
            return pytest.ExitCode.NO_TESTS_COLLECTED
        os.write(progress_fd, b"completed\n")
    return status


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], int(sys.argv[2])))

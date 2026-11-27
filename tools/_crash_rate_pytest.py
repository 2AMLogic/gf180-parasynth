"""Pytest child for crash_rate.py. The launcher reads progress even after a signal."""
import functools
import os
import sys
import types

import pytest


def _target_code(obj: object) -> types.CodeType | None:
    seen: set[int] = set()
    while True:
        if id(obj) in seen:
            return None
        seen.add(id(obj))
        if hasattr(obj, "__wrapped__"):
            obj = getattr(obj, "__wrapped__")
        elif isinstance(obj, functools.partial):
            obj = obj.func
        elif isinstance(obj, types.MethodType):
            obj = obj.__func__
        else:
            return obj.__code__ if isinstance(obj, types.FunctionType) else None


def _free_monitoring_tool() -> int | None:
    mon = getattr(sys, "monitoring", None)
    if mon is None:
        return None
    for tool_id in (3, 4, mon.PROFILER_ID, mon.COVERAGE_ID, mon.DEBUGGER_ID, mon.OPTIMIZER_ID):
        if mon.get_tool(tool_id) is None:
            return tool_id
    return None


class TargetCalls:
    def __init__(self, target, progress_fd):
        self.target = target
        self.progress_fd = progress_fd
        self.started_items: set[str] = set()
        self.completed_items: set[str] = set()
        self.unentered_outcome = False
        self._wrote_started = False

    def pytest_sessionstart(self, session):
        # Check pytest's parsed selection, including config and PYTEST_ADDOPTS.
        if session.config.args != [self.target]:
            raise pytest.UsageError(f"additional pytest targets in {session.config.args!r}")

    def pytest_collectreport(self, report):
        if report.failed:
            self.unentered_outcome = True

    @pytest.hookimpl(hookwrapper=True, tryfirst=True)
    def pytest_runtest_call(self, item):
        # Observe PY_START on the unwrapped target's code object rather than
        # wrapping item.obj. CPython emits PY_START at RESUME 0: after fixture
        # setup, unittest setUp, @wraps decorators and argument binding succeed,
        # and for generators/coroutines/async-generators only if stepped past
        # RETURN_GENERATOR into the body. Non-Python callables have no code
        # object and remain unarmed (refused). A decorator that omits
        # __wrapped__ rebinds the symbol to its wrapper's code object.
        code = _target_code(item.obj) if isinstance(item, pytest.Function) else None
        tool_id = _free_monitoring_tool() if code is not None else None
        if tool_id is None:
            yield
            return
        mon = sys.monitoring
        mon.use_tool_id(tool_id, "crash_rate")

        def on_start(entered_code, _offset):
            if entered_code is code:
                if not self._wrote_started:
                    os.write(self.progress_fd, b"started\n")
                    self._wrote_started = True
                self.started_items.add(item.nodeid)
                mon.set_local_events(tool_id, code, mon.events.NO_EVENTS)

        mon.register_callback(tool_id, mon.events.PY_START, on_start)
        mon.set_local_events(tool_id, code, mon.events.PY_START)
        try:
            yield
        finally:
            mon.set_local_events(tool_id, code, mon.events.NO_EVENTS)
            mon.register_callback(tool_id, mon.events.PY_START, None)
            mon.free_tool_id(tool_id)

    def pytest_runtest_logreport(self, report):
        entered = report.nodeid in self.started_items
        if report.failed and not entered:
            self.unentered_outcome = True
            return
        if report.when != "call":
            return
        # An executed xfail is a skipped call with wasxfail. A skip mark or
        # xfail(run=False) stops without wasxfail.
        if not report.skipped or hasattr(report, "wasxfail"):
            if entered:
                self.completed_items.add(report.nodeid)
            else:
                self.unentered_outcome = True


def main(target, progress_fd):
    os.set_inheritable(progress_fd, False)
    if not sys.flags.safe_path:
        # Match python -m pytest's import path for the target.
        sys.path[0] = os.getcwd()
    calls = TargetCalls(target, progress_fd)
    status = pytest.main([target, "-q", "-p", "no:cacheprovider", "--no-header"], plugins=[calls])
    if status in (pytest.ExitCode.OK, pytest.ExitCode.TESTS_FAILED):
        if not calls.completed_items or calls.unentered_outcome:
            return pytest.ExitCode.NO_TESTS_COLLECTED
        os.write(progress_fd, b"completed\n")
    return status


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], int(sys.argv[2])))

"""Pin the host load for every fpga/ test (#584).

`midi_session.main(["--port", "sim", ...])` REFUSES on an overloaded host, so any
test that reaches it would otherwise depend on the real load of whatever machine
runs it: on a 4-CPU CI runner at load 5.5 six existing tests returned rc 2 before
reaching the code they test.  A test that needs a loaded host overrides this with
its own monkeypatch (function-scoped fixtures run before the test body).
"""
import os

import pytest


@pytest.fixture(autouse=True)
def _quiet_host(monkeypatch):
    monkeypatch.setattr(os, "getloadavg", lambda: (0.0, 0.0, 0.0))
    monkeypatch.setattr(os, "cpu_count", lambda: 8)

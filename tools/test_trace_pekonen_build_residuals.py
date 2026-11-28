"""Controls for tools/trace_pekonen_build_residuals.py (issue #2055, Doctor fix on PR #2116).

The tool's contract is that a seam which did not take is REFUSED (exit 2), never
a traceback (exit 1, which `exit_contract` reads as a RESULT). The seam-failure
row once called `_line` with a string where it wants a process, so exactly the
failure the guard exists for crashed the guard.
"""
from __future__ import annotations

import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
import exit_contract                                      # noqa: E402
import trace_pekonen_build_residuals as tr                # noqa: E402


def _never_ok(p, n):
    return True, "unreachable"


def test_a_seam_that_did_not_take_is_refused_not_a_traceback(monkeypatch, capsys):
    case = ("X", "tools/none.py", ("raise AssertionError('SEAM FAILED: the seam did not take')\n",),
            "", "REFUSED", _never_ok)
    monkeypatch.setattr(tr, "CASES", (case,))
    monkeypatch.setattr(tr, "handlers", lambda: dict(tr.HANDLERS))
    rc = tr.main([])
    out = capsys.readouterr().out
    assert rc == tr.REFUSED == exit_contract.REFUSED
    assert "SEAM FAILED: the seam did not take" in out      # the row carries the message, not ''


def test_moved_handlers_are_refused(monkeypatch, capsys):
    monkeypatch.setattr(tr, "handlers", lambda: {"controls": [(999, "Refused")], "main": tr.HANDLERS["main"]})
    assert tr.main([]) == tr.REFUSED

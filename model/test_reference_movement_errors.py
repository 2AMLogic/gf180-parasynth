"""#610: stage_plugins must keep exception types and tell a missing plugin
from a defect in building the rig."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import reference_movement as rm


def _run(monkeypatch, exc, capsys):
    def boom(name, block=rm.PLUGIN_BLOCK):
        raise exc
    monkeypatch.setattr(rm, "_build", boom)
    rows = rm.stage_plugins(["miniv3"], cache=None)
    return rows, capsys.readouterr().out


def test_a_missing_plugin_is_unavailable_with_its_type(monkeypatch, capsys):
    rows, out = _run(monkeypatch, FileNotFoundError("Mini V3.vst3"), capsys)
    assert rows[0]["status"] == "unavailable" and rows[0]["error_type"] == "FileNotFoundError"
    assert "NOT AVAILABLE" in out and "FileNotFoundError" in out


def test_a_build_bug_is_not_reported_as_unavailable(monkeypatch, capsys):
    """start-red: a KeyError/TypeError used to print 'NOT AVAILABLE'."""
    rows, out = _run(monkeypatch, KeyError("param 265"), capsys)
    assert rows[0]["status"] == "error" and rows[0]["error_type"] == "KeyError"
    assert "NOT AVAILABLE" not in out and "KeyError" in out


def test_an_import_error_from_our_own_code_is_still_unavailable_known_gap(monkeypatch, capsys):
    """Rule 8: the input that defeats `_UNAVAILABLE`, pinned as an accepted limit.

    An ImportError from a refactor in OUR code is still reported as
    unavailable / "NOT AVAILABLE", the same status as a plugin that is not
    installed. The mitigation is that `error_type` and `why` carry the type
    and message, in the row and in the printed line. If this starts failing
    because the classification was narrowed, update it and the comment on
    `_UNAVAILABLE`."""
    rows, out = _run(monkeypatch,
                     ImportError("cannot import name 'renamed_name' from 'am'"), capsys)
    assert rows[0]["status"] == "unavailable"                 # the known gap
    assert rows[0]["error_type"] == "ImportError"             # the mitigation
    assert "ImportError: cannot import name 'renamed_name'" in rows[0]["why"]
    assert "ImportError: cannot import name 'renamed_name'" in out

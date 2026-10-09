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

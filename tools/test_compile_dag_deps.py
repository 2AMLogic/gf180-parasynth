"""Dependency-status propagation in the DAG compiler (issue #140).

`classify()` judged each node on its OWN evidence only, so a node could render
GREEN directly above a RED or BLOCKED prerequisite (the real F1 -> M1 -> M2 ->
M3 -> M4 chain). A green claim resting on a failing prerequisite is the shape
docs/failure-modes.md warns about: locally true, globally misleading.

Rule pinned here: a GREEN/STAMPED node with a RED or BLOCKED dependency is
BLOCKED. TODO/STALE dependencies do NOT propagate (not known-failing; making
them block would turn most of the graph red on a stale stamp). A node's own
RED/BLOCKED/TODO/STALE verdict is never overwritten. Cycles and unknown deps
are refused (BLOCKED with a stated reason), never guessed past.
"""
from __future__ import annotations
import json, pathlib, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import compile_dag as cd                                            # noqa: E402


def _passing(tmp_path, name="e.json"):
    p = tmp_path / name
    p.write_text(json.dumps({"passed": True}))
    return str(p)


def _own(monkeypatch, own):
    """Stub each node's OWN verdict so only propagation is under test."""
    monkeypatch.setattr(cd, "classify", lambda nid, n: own[nid])


def test_green_above_red_dep_becomes_blocked(monkeypatch):
    own = {"A": ("RED", "failed"), "B": ("GREEN", "passed")}
    _own(monkeypatch, own)
    st = cd.classify_all({"A": {}, "B": {"deps": ["A"]}})
    assert st["A"][0] == "RED"
    assert st["B"][0] == "BLOCKED" and "A" in st["B"][1]


def test_green_above_blocked_dep_becomes_blocked_transitively(monkeypatch):
    own = {"A": ("BLOCKED", "waiting"), "B": ("STAMPED", "tag"), "C": ("GREEN", "ok")}
    _own(monkeypatch, own)
    st = cd.classify_all({"A": {}, "B": {"deps": ["A"]}, "C": {"deps": ["B"]}})
    assert st["B"][0] == "BLOCKED" and st["C"][0] == "BLOCKED"


def test_order_in_the_file_does_not_matter(monkeypatch):
    own = {"A": ("RED", "x"), "B": ("GREEN", "y"), "C": ("GREEN", "z")}
    _own(monkeypatch, own)
    st = cd.classify_all({"C": {"deps": ["B"]}, "B": {"deps": ["A"]}, "A": {}})
    assert st["B"][0] == "BLOCKED" and st["C"][0] == "BLOCKED"


def test_own_failure_is_not_overwritten(monkeypatch):
    own = {"A": ("BLOCKED", "w"), "B": ("RED", "own failure")}
    _own(monkeypatch, own)
    st = cd.classify_all({"A": {}, "B": {"deps": ["A"]}})
    assert st["B"] == ("RED", "own failure")


def test_todo_and_stale_deps_do_not_propagate(monkeypatch):
    own = {"A": ("TODO", "t"), "S": ("STALE", "s"),
           "B": ("GREEN", "ok"), "C": ("STAMPED", "tag")}
    _own(monkeypatch, own)
    st = cd.classify_all({"A": {}, "S": {}, "B": {"deps": ["A"]}, "C": {"deps": ["S"]}})
    assert st["B"][0] == "GREEN" and st["C"][0] == "STAMPED"


def test_green_chain_stays_green(monkeypatch):
    own = {"A": ("STAMPED", "t"), "B": ("GREEN", "ok")}
    _own(monkeypatch, own)
    st = cd.classify_all({"A": {}, "B": {"deps": ["A"]}})
    assert st["A"][0] == "STAMPED" and st["B"][0] == "GREEN"


def test_cycle_is_refused_not_guessed(monkeypatch):
    own = {"A": ("GREEN", "ok"), "B": ("GREEN", "ok")}
    _own(monkeypatch, own)
    st = cd.classify_all({"A": {"deps": ["B"]}, "B": {"deps": ["A"]}})
    assert st["A"][0] == "BLOCKED" and "cycle" in st["A"][1]
    assert st["B"][0] == "BLOCKED" and "cycle" in st["B"][1]


def test_unknown_dep_is_refused(monkeypatch):
    own = {"B": ("GREEN", "ok")}
    _own(monkeypatch, own)
    st = cd.classify_all({"B": {"deps": ["GHOST"]}})
    assert st["B"][0] == "BLOCKED" and "GHOST" in st["B"][1]


def test_end_to_end_with_real_own_evidence(tmp_path):
    """No stubbing: a node whose own evidence passes, above a declared blocker."""
    nodes = {"A": {"blocked": "waiting on a recording"},
             "B": {"evidence_file": _passing(tmp_path), "deps": ["A"]}}
    # B alone is not GREEN without a recorded result, so assert via the stub-free
    # path that A stays BLOCKED and B is not rendered GREEN/STAMPED.
    st = cd.classify_all(nodes)
    assert st["A"][0] == "BLOCKED"
    assert st["B"][0] not in ("GREEN", "STAMPED")

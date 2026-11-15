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


# ---------------------------------------------------------------------------
# #788: an issue number is not evidence
# ---------------------------------------------------------------------------
# M5 ("Noise, osc-3 modulation, full waveform set") cited #48 and D4 ("Complete
# 808 -- all 16 sounds") cited #22. Both issues are CLOSED -- #48 as completed,
# #22 as not planned -- so both nodes rendered `TODO  issue #N`: a status
# pointing at a decision already taken, in a graph whose whole claim is that
# status is derived from evidence rather than asserted. Both capabilities were
# in fact already covered (M5 by M1's `waveforms`/`noise`/`modulation`
# scenarios and M2's suite, D4 by D1/D2 and all sixteen abbreviations), so both
# nodes were retired and the reason recorded in docs/capability-dag.md.
#
# The guard is deliberately LOCAL: it reads the committed docs/dag.json and
# nothing else. The rejected alternative -- a committed list of issue states --
# decays silently in exactly the way that produced this defect (the list
# records #48 as open, #48 closes, the guard still passes) and would need
# network access or manual upkeep in CI.
#
# THE INPUTS THAT DEFEAT THIS GUARD (docs/verification-rules.md rule 8 -- a
# guard ships with them, so the next reader knows its boundary):
#
#   1. Adding ANY recognised evidence key satisfies "not issue-only" while
#      leaving the node unevidenced. Specifically an `evidence_file` with
#      neither a verdict key (passed/ok/success/green) nor `evidence_requires`
#      lands on run_evidence's "EXISTS ONLY -- no verdict declared" branch,
#      which returns PASS, so the node renders GREEN on file existence alone.
#      That hole is real and is NOT closed here: M3, S1 and S3 already render
#      GREEN through that branch today, so a guard forbidding verdict-less
#      `evidence_file` nodes would be unsatisfiable the moment it landed --
#      worse than no guard, per CLAUDE.md. Tracked as #791.
#   2. Deleting the `issue` key instead of resolving the node. That renders
#      `TODO  no evidence declared`, which this guard permits. It is a weaker
#      defeat than it looks: the node then states plainly that it has no
#      evidence, where `issue #48` implied a live pointer. No node in
#      docs/dag.json has that shape today.
EVIDENCE_KEYS = ("suite", "verifier", "tool", "evidence_file")


def _committed_nodes() -> dict:
    """The DAG as committed -- the artefact the README is rendered from."""
    return json.loads(cd.DAG.read_text())["nodes"]


def _is_issue_only(n: dict) -> bool:
    """The forbidden shape: an issue number standing in for evidence.

    A `blocked` reason is the intended escape. A node that says what it is
    waiting for renders BLOCKED with that reason, which is a statement about
    the world rather than a stale pointer, so it may carry an `issue` too.
    """
    return ("issue" in n
            and not any(k in n for k in EVIDENCE_KEYS)
            and not n.get("blocked"))


def test_no_committed_node_cites_an_issue_as_its_only_source():
    """The #788 guard, run against the artefact that ships."""
    offenders = sorted(i for i, n in _committed_nodes().items() if _is_issue_only(n))
    assert not offenders, (
        f"{offenders} declare an `issue` with no evidence key and no `blocked` "
        f"reason, so tools/compile_dag.py renders them `TODO  issue #N` -- a "
        f"status that is asserted, not derived, and that survives the issue "
        f"being closed. Fix it one of three ways: name evidence "
        f"({'/'.join(EVIDENCE_KEYS)}), declare what the node is blocked on, or "
        f"retire the node and record in docs/capability-dag.md which node now "
        f"carries the capability.")


def test_the_guard_turns_red_on_an_injected_issue_only_node():
    """The injected-defect control, in code rather than by hand.

    A guard that has never failed is not evidence (docs/verification-rules.md).
    This pins all three dispositions the failure message offers, including the
    `blocked` escape -- and the defeating input from note 1 above, so the
    boundary is asserted rather than merely described.
    """
    assert _is_issue_only({"issue": 99999})
    # the escape hatch is intended and must stay green
    assert not _is_issue_only({"issue": 99999, "blocked": "waiting on silicon"})
    # ... and so is real evidence
    assert not _is_issue_only({"issue": 99999, "suite": "model/test_moog_acceptance.py"})
    # the defeating input: an evidence key with no verdict behind it passes
    # this guard. #791.
    assert not _is_issue_only({"issue": 99999, "evidence_file": "docs/nothing.json"})


def test_an_issue_only_node_renders_todo_against_that_issue():
    """Why the shape is forbidden -- the mechanism, not just the data.

    If classify() ever stopped rendering the bare issue number, the guard above
    would be forbidding a shape that no longer misleads anyone. Pin the link.
    """
    assert cd.classify("X", {"issue": 99999}) == ("TODO", "issue #99999")
    assert cd.classify("X", {}) == ("TODO", "no evidence declared")
    assert cd.classify("X", {"issue": 99999, "blocked": "a recording"})[0] == "BLOCKED"

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
#      leaving the node unevidenced. This WAS a real defeat: an `evidence_file`
#      with neither a verdict key (passed/ok/success/green) nor
#      `evidence_requires` landed on run_evidence's no-verdict branch, which
#      returned PASS, so the node rendered GREEN on file existence alone --
#      M3, S1 and S3 all did (#791). #810 closed it: the branch now returns
#      False and classify() renders REFUSED, so the shape this guard permits
#      is an honest decline, not a false pass -- and a stale issue pointer
#      behind a REFUSED asserts nothing, because REFUSED never mentions the
#      issue. The guard #791 deferred as unsatisfiable -- forbidding the
#      verdict-less `evidence_file` declaration itself -- is therefore now
#      satisfiable (S1 reads a derived verdict, S3 declares
#      `evidence_requires`, M3 states in its note that REFUSED is deliberate)
#      and lives in this file as
#      test_no_committed_node_declares_a_verdict_less_evidence_file, with
#      its controls in tools/control_dag_verdictless_evidence.py.
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
    # an evidence key still satisfies "not issue-only" -- including a
    # verdict-less `evidence_file`. Since #810 that shape renders REFUSED
    # rather than the GREEN it used to, so what this guard permits is an
    # honest decline (note 1 above); the declaration itself is policed by
    # test_no_committed_node_declares_a_verdict_less_evidence_file below.
    assert not _is_issue_only({"issue": 99999, "evidence_file": "docs/nothing.json"})


def test_an_issue_only_node_renders_todo_against_that_issue():
    """Why the shape is forbidden -- the mechanism, not just the data.

    If classify() ever stopped rendering the bare issue number, the guard above
    would be forbidding a shape that no longer misleads anyone. Pin the link.
    """
    assert cd.classify("X", {"issue": 99999}) == ("TODO", "issue #99999")
    assert cd.classify("X", {}) == ("TODO", "no evidence declared")
    assert cd.classify("X", {"issue": 99999, "blocked": "a recording"})[0] == "BLOCKED"


def test_no_committed_node_declares_a_verdict_less_evidence_file():
    """A committed `evidence_file` must declare a verdict, or own its REFUSED.

    #791's hole -- GREEN on file existence alone -- is closed in
    compile_dag.py (#810): a node whose `evidence_file` carries no verdict key
    and no `evidence_requires` now renders REFUSED, and REFUSED cannot lie.
    What this guard adds is the declaration half: landing in that state by
    ACCIDENT is a node the roadmap cannot answer, so a node that commits it
    must say so in its own `note` -- the M3 pattern, whose note states that
    REFUSED is deliberate and why no substring guard can replace the missing
    verdict. #791 deferred this guard as unsatisfiable while M3, S1 and S3 all
    rendered GREEN through the branch; #810 repaired S1 (a derived
    verdict.json) and S3 (`evidence_requires`) and left M3 REFUSED with that
    note, so this guard is satisfiable against the tree as committed -- run
    against docs/dag.json before committing, per CLAUDE.md's
    unsatisfiable-gate rule.

    Rule 8, the inputs that defeat it, committed as controls in
    tools/control_dag_verdictless_evidence.py:
      * a verdict-less `evidence_file` with NO note -> this guard fires
        (red control SILENT_VERDICTLESS_EVIDENCE_FILE_RETURNS);
      * the same node WITH a REFUSED note is the permitted shape (green
        control DECLARED_REFUSED_VERDICTLESS_STAYS_GREEN). The note is
        checked as a substring, so it asserts acknowledgement, not truth --
        the same trust model as the `blocked` reason the #788 guard accepts;
        the damage a lying note can do is bounded, because the node renders
        REFUSED in the roadmap regardless of what its note claims;
      * `evidence_requires` is the other escape (green control
        EVIDENCE_REQUIRES_ESCAPE_STAYS_GREEN).
    Stated blind spots, not fixed here: a MISSING evidence file is unjudged()'s
    None -- missing is a failure rendered TODO, so no false pass is possible;
    and a hand-written `{"ok": true}` file satisfies unjudged() without any
    run -- that hole belongs to the `covers`/staleness guards (#863), not
    this one.
    """
    silent = sorted(
        i for i, n in _committed_nodes().items()
        if cd.unjudged(n) is not None and "REFUSED" not in n.get("note", ""))
    assert not silent, (
        f"{silent} commit an `evidence_file` carrying no verdict this tool can "
        f"read (no passed/ok/success/green key, no `evidence_requires`), so "
        f"tools/compile_dag.py renders them REFUSED -- an honest decline, but "
        f"one the node must own. State it in the node's `note` (name REFUSED "
        f"and why it is the true state -- M3 is the example), declare "
        f"`evidence_requires`, or make the file carry a verdict key.")

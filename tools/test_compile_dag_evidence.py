"""The DAG's evidence check, controlled.

`run_evidence`'s own docstring said an earlier version called a node GREEN when
its test FILE EXISTED, "which is precisely the failure this tool was written to
prevent" -- and the fix had been applied to the `suite` branch and NOT to the
`evidence_file` branch. A JSON containing `"passed": false` rendered the node
green because the file was on disk.

Fixing the instance and leaving the class is the repeating failure here, so
these cases pin the class.
"""
from __future__ import annotations
import json, pathlib, sys
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import compile_dag as cd                                            # noqa: E402


@pytest.mark.parametrize("doc,expected", [
    ({"passed": False}, False),
    ({"passed": True},  True),
    ({"ok": False},     False),
    ({"success": False}, False),
])
def test_a_json_verdict_is_honoured_not_just_its_existence(tmp_path, doc, expected):
    p = tmp_path / "e.json"
    p.write_text(json.dumps(doc))
    ok, detail = cd.run_evidence("CTL", {"evidence_file": str(p)})
    assert ok is expected, detail
    assert "passed" in detail or "ok" in detail or "success" in detail


def test_a_file_with_no_verdict_is_not_a_pass(tmp_path):
    """RECONCILED by #791. This test used to assert `ok is True` with an
    "EXISTS ONLY" note -- it pinned the exact bug: the detail string said the
    file was unjudged and the boolean said it had passed, and `classify()` reads
    the boolean, so the node rendered GREEN. Announcing it is necessary and was
    never sufficient. Now neither half claims a pass."""
    p = tmp_path / "e.json"
    p.write_text(json.dumps({"corner_hz": 117.9}))
    node = {"evidence_file": str(p)}
    ok, detail = cd.run_evidence("CTL", node)
    assert ok is False
    assert "no verdict" in detail, "an unjudged file must announce that it is unjudged"
    assert cd.unjudged(node), "and the same predicate must say so to classify()"


# ---------------------------------------------------------------------------
# #791: an evidence file that declares no verdict renders REFUSED, not GREEN.
#
# The class, not the instance: the `evidence_file` branch has now been repaired
# three times -- file existence (for `suite`), the falsy verdict (#140), and the
# ABSENT verdict (#791). Each previous fix left the next one intact, so these
# cases pin the predicate itself rather than any one of its callers.
# ---------------------------------------------------------------------------
def _classify(tmp_path, monkeypatch, node, rec=None):
    """classify() one node with a controlled results record and no git."""
    monkeypatch.setattr(cd, "ROOT", tmp_path)
    monkeypatch.setattr(cd, "load_results", lambda: ({"CTL": rec} if rec else {}))
    monkeypatch.setattr(cd, "tag_is_ancestor", lambda t: False)
    monkeypatch.setattr(cd, "stale_against", lambda n, sha: None)
    return cd.classify("CTL", node)


def test_an_unjudged_json_is_refused_not_green(tmp_path, monkeypatch):
    """THE BUG. A recorded `passed: true` must not survive the discovery that
    nothing was ever judged -- which is why classify() asks the DECLARATION
    before it reads the record."""
    (tmp_path / "e.json").write_text(json.dumps({"corner_hz": 117.9}))
    st, note = _classify(tmp_path, monkeypatch, {"evidence_file": "e.json"},
                         rec={"passed": True, "sha": "abc",
                              "detail": "e.json EXISTS ONLY -- no verdict declared"})
    assert st == "REFUSED", f"got {st}: {note}"
    assert "no verdict" in note


def test_an_unjudged_file_is_refused_even_with_a_stamp(tmp_path, monkeypatch):
    """A tag is evidence of a run, not of a verdict. The STAMPED path bypassed
    the record entirely, so it needed the same guard."""
    (tmp_path / "e.json").write_text(json.dumps({"corner_hz": 117.9}))
    monkeypatch.setattr(cd, "ROOT", tmp_path)
    monkeypatch.setattr(cd, "load_results", lambda: {})
    monkeypatch.setattr(cd, "tag_is_ancestor", lambda t: True)
    monkeypatch.setattr(cd, "git", lambda *a: "deadbeef")
    monkeypatch.setattr(cd, "stale_against", lambda n, sha: None)
    st, _ = cd.classify("CTL", {"evidence_file": "e.json", "tag": "node/CTL"})
    assert st == "REFUSED"


def test_a_non_json_file_without_evidence_requires_is_refused(tmp_path, monkeypatch):
    """S3's shape before #791: a .txt, so the JSON verdict loop never runs and
    there is nothing at all to judge."""
    (tmp_path / "e.txt").write_text("ROUTED 0 DRC violations")
    st, note = _classify(tmp_path, monkeypatch, {"evidence_file": "e.txt"},
                         rec={"passed": True, "sha": "abc", "detail": "x"})
    assert st == "REFUSED" and "not JSON" in note


def test_declaring_evidence_requires_makes_a_text_node_judgeable(tmp_path, monkeypatch):
    """S3's repair: the same file, now with a declared pass condition."""
    (tmp_path / "e.txt").write_text("ROUTED 0 DRC violations")
    node = {"evidence_file": "e.txt", "evidence_requires": "0 DRC"}
    monkeypatch.setattr(cd, "ROOT", tmp_path)
    assert cd.unjudged(node) is None, "a declared pass condition IS a verdict"
    st, _ = _classify(tmp_path, monkeypatch, node,
                      rec={"passed": True, "sha": "abc", "detail": "contains '0 DRC'"})
    assert st == "GREEN"


def test_a_json_verdict_key_makes_a_node_judgeable(tmp_path, monkeypatch):
    """S1's repair: a derived verdict file carrying a top-level `passed`."""
    (tmp_path / "v.json").write_text(json.dumps({"passed": True, "detail": "routed"}))
    st, _ = _classify(tmp_path, monkeypatch, {"evidence_file": "v.json"},
                      rec={"passed": True, "sha": "abc", "detail": "v.json passed=True"})
    assert st == "GREEN"


def test_a_falsy_verdict_is_still_red_not_refused(tmp_path, monkeypatch):
    """#140 must not regress. A declared failure is a FAILURE; REFUSED is for
    the absence of a verdict, and widening it to cover a false one would undo
    the previous fix while looking like this one."""
    (tmp_path / "v.json").write_text(json.dumps({"passed": False}))
    node = {"evidence_file": "v.json"}
    monkeypatch.setattr(cd, "ROOT", tmp_path)
    assert cd.unjudged(node) is None
    ok, _ = cd.run_evidence("CTL", node)
    assert ok is False
    st, _ = _classify(tmp_path, monkeypatch, node,
                      rec={"passed": False, "sha": "abc", "detail": "v.json passed=False"})
    assert st == "RED"


def test_malformed_json_is_red_not_refused(tmp_path, monkeypatch):
    """The input that defeats an over-eager REFUSED: a file that cannot be
    parsed is a broken artefact, not an abstention. Reading it as REFUSED would
    hide it from --strict forever."""
    (tmp_path / "v.json").write_text("{truncated")
    node = {"evidence_file": "v.json"}
    monkeypatch.setattr(cd, "ROOT", tmp_path)
    assert cd.unjudged(node) is None
    st, _ = _classify(tmp_path, monkeypatch, node,
                      rec={"passed": False, "sha": "abc", "detail": "not valid JSON"})
    assert st == "RED"


def test_a_missing_file_is_todo_not_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(cd, "ROOT", tmp_path)
    assert cd.unjudged({"evidence_file": "gone.json"}) is None
    st, _ = _classify(tmp_path, monkeypatch, {"evidence_file": "gone.json"})
    assert st == "TODO"


def test_a_json_list_cannot_carry_a_verdict_and_is_refused(tmp_path, monkeypatch):
    """Valid JSON of the wrong shape -- the #600 defeating input, here."""
    (tmp_path / "e.json").write_text("[1, 2, 3]")
    monkeypatch.setattr(cd, "ROOT", tmp_path)
    why = cd.unjudged({"evidence_file": "e.json"})
    assert why and "list" in why


def test_refused_is_registered_in_both_mermaid_dicts():
    """`mark` is a bare index: an unregistered status raises KeyError rather
    than rendering wrong. Run the gate against the current state."""
    nodes = {"CTL": {"name": "ctl", "group": "foundation"}}
    for st in ("STAMPED", "GREEN", "STALE", "BLOCKED", "RED", "TODO", "REFUSED"):
        out = cd.mermaid(nodes, {"CTL": (st, "note")})
        assert "style CTL fill:" in out


def test_refused_does_not_propagate_to_dependents():
    """Stated decision, not an accident: REFUSED means "we do not know", so it
    joins TODO rather than RED/BLOCKED. A dependent whose own evidence passed is
    not made false by an unjudged prerequisite."""
    nodes = {"A": {"name": "a"}, "B": {"name": "b", "deps": ["A"]}}
    import unittest.mock as m
    with m.patch.object(cd, "classify",
                        side_effect=lambda i, n: {"A": ("REFUSED", "no verdict"),
                                                  "B": ("GREEN", "passed")}[i]):
        out = cd.classify_all(nodes)
    assert out["A"][0] == "REFUSED"
    assert out["B"][0] == "GREEN", "REFUSED must not propagate (RED/BLOCKED do)"


def test_the_minimoog_fidelity_warning_is_restored_on_the_real_dag():
    """#45, end to end on the COMMITTED graph -- the outcome #791 is for.

    M3 is the only minimoog fidelity node that was green (M4 is BLOCKED), so the
    audit's "UNVALIDATED against anything external" warning was being held off
    by a file that was never judged. With M3 REFUSED the warning must appear.
    Asserted against `--print`'s real output, not against a hand-built graph,
    because the thing that must change is the document a reader quotes.
    """
    import subprocess
    r = subprocess.run([sys.executable, str(pathlib.Path(cd.__file__)), "--print"],
                       capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stderr
    assert "Minimoog voice: no green fidelity evidence" in r.stdout, r.stdout[-2000:]
    # ...and no row may claim GREEN while saying it was never judged.
    for line in r.stdout.splitlines():
        if "**GREEN**" in line or "**STAMPED**" in line:
            assert "no verdict" not in line and "EXISTS ONLY" not in line, line


def test_a_missing_file_is_a_failure(tmp_path):
    ok, detail = cd.run_evidence("CTL", {"evidence_file": str(tmp_path / "nope.json")})
    assert ok is False and "missing" in detail


def test_malformed_json_is_a_failure_not_a_pass(tmp_path):
    p = tmp_path / "e.json"
    p.write_text("{not json")
    ok, detail = cd.run_evidence("CTL", {"evidence_file": str(p)})
    assert ok is False and "not valid JSON" in detail


@pytest.mark.parametrize("requires,expected", [("0 DRC", True), ("SIGNOFF", False)])
def test_text_evidence_can_declare_what_makes_it_a_pass(tmp_path, requires, expected):
    """Not everything is JSON -- an ORFS or bitstream report is text."""
    p = tmp_path / "e.txt"
    p.write_text("ROUTED 0 DRC violations")
    ok, _ = cd.run_evidence("CTL", {"evidence_file": str(p), "evidence_requires": requires})
    assert ok is expected


# ---------------------------------------------------------------------------
# #600: a corrupt results file is not "no evidence yet"
# ---------------------------------------------------------------------------
def test_a_corrupt_results_file_refuses_rather_than_reading_as_empty(tmp_path, monkeypatch):
    """`load_results` used to return {} on any exception, so a truncated
    dag-results.json made every node look unrun -- and `--run` then wrote the
    partial results over it, deleting the slow nodes' stamps."""
    p = tmp_path / "dag-results.json"
    p.write_text('{"M1": {"passed": true, "sha": "abc"')          # truncated
    monkeypatch.setattr(cd, "RESULTS", p)
    with pytest.raises(SystemExit) as e:
        cd.load_results()
    assert "dag-results.json" in str(e.value)


def test_a_results_file_that_is_not_an_object_refuses(tmp_path, monkeypatch):
    """The input that defeats a parse-only check: valid JSON of the wrong
    shape. `.get(nid)` on a list would raise far from the cause."""
    p = tmp_path / "dag-results.json"
    p.write_text("[]")
    monkeypatch.setattr(cd, "RESULTS", p)
    with pytest.raises(SystemExit):
        cd.load_results()


def test_an_absent_results_file_is_still_no_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(cd, "RESULTS", tmp_path / "absent.json")
    assert cd.load_results() == {}


def test_the_committed_results_file_loads():
    """Run the gate against the current state before committing it."""
    assert isinstance(cd.load_results(), dict)

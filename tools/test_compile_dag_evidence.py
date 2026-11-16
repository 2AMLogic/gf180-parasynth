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
import json, pathlib, subprocess, sys
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


def test_a_file_with_no_verdict_says_so_rather_than_implying_it_was_read(tmp_path):
    """Existence alone may still stand -- but it must not LOOK like a verdict."""
    p = tmp_path / "e.json"
    p.write_text(json.dumps({"corner_hz": 117.9}))
    ok, detail = cd.run_evidence("CTL", {"evidence_file": str(p)})
    assert ok is True
    assert "EXISTS ONLY" in detail, "an unjudged file must announce that it is unjudged"


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


# ---------------------------------------------------------------------------
# #824: an unresolvable record sha is not "nothing changed"
# ---------------------------------------------------------------------------
# `stale_against()` measures staleness with `git log <sha>..HEAD -- <file>`
# through `git()`, which returns stdout only. A failed range query -- a sha
# from a squash-merged or force-pushed-away branch, or a clone too shallow to
# contain it -- writes the error to discarded stderr and "" to stdout, which
# reads as "no commit touched this file": the node renders GREEN with no
# measurement taken at all. The tag path is already guarded
# (`tag_is_ancestor`, reading a returncode); the record path at classify()'s
# `stale_against(n, rec.get("sha", ""))` was not. "Cannot tell" is its own
# verdict here (REFUSED), never GREEN.
#
# The input that defeats the reachability guard (rule 8): a passing record on
# a node whose `covers` is empty. The loop measures nothing and reports "not
# stale" whatever the sha. Closed in the same change -- evidence with no
# covered files to measure is REFUSED too. Pinned by
# test_issue_824_a_passing_record_with_no_covers_is_refused.
def _mini_repo(tmp_path, monkeypatch):
    """A throwaway two-commit git repo, so ancestry questions get real
    answers without touching this checkout's history.

    classify() and everything it calls run with cwd=ROOT, so pointing ROOT
    here redirects the whole apparatus. `v1` touched covered.v; `v2` is HEAD.
    """
    def g(*a: str) -> str:
        r = subprocess.run(["git", *a], cwd=tmp_path, capture_output=True,
                           text=True)
        assert r.returncode == 0, f"git {' '.join(a)} failed: {r.stderr}"
        return r.stdout.strip()

    g("init", "-q", "-b", "main")
    g("config", "user.email", "control@example.com")
    g("config", "user.name", "control")
    g("config", "commit.gpgsign", "false")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "dag.json").write_text("{}\n")
    (tmp_path / "covered.v").write_text("v1\n")
    g("add", "-A")
    g("commit", "-q", "--no-verify", "-m", "v1")
    v1 = g("rev-parse", "HEAD")
    (tmp_path / "covered.v").write_text("v2\n")
    g("add", "-A")
    g("commit", "-q", "--no-verify", "-m", "v2")
    monkeypatch.setattr(cd, "ROOT", tmp_path)
    return v1, g("rev-parse", "HEAD")


def test_issue_824_an_unresolvable_record_sha_is_not_reported_current(monkeypatch):
    """THE CONTROL (rule 5: through classify(), where the verdict is issued).

    The node declares an existing evidence source and a non-empty `covers`,
    both required to get past the TODO/missing guards and reach the record
    branch. Pre-fix this rendered ("GREEN", ...) -- that failure is the one
    this change exists to fix."""
    monkeypatch.setattr(cd, "load_results",
                        lambda: {"N": {"passed": True, "sha": "deadbeef" * 5}})
    st, note = cd.classify("N", {"evidence_file": "docs/dag.json",
                                 "covers": ["README.md"]})
    assert st not in ("GREEN", "STAMPED"), \
        f"an unresolvable sha rendered the node current: {st} -- {note}"
    assert st == "REFUSED"
    assert "deadbee" in note, "the refusal must name the offending sha"


def test_issue_824_a_sha_outside_the_history_is_refused_even_though_it_resolves(tmp_path, monkeypatch):
    """The sharper case: the sha still resolves in the object store but no
    commit in this history reaches it (force-pushed away before gc). The
    range query would silently answer about a divergent history."""
    v1, v2 = _mini_repo(tmp_path, monkeypatch)
    orphan = subprocess.run(
        ["git", "commit-tree", "HEAD^{tree}", "-m", "orphan"],
        cwd=tmp_path, capture_output=True, text=True, check=True).stdout.strip()
    t = subprocess.run(["git", "cat-file", "-t", orphan], cwd=tmp_path,
                       capture_output=True, text=True, check=True).stdout.strip()
    assert t == "commit", "control construction: the object must exist"
    monkeypatch.setattr(cd, "load_results",
                        lambda: {"N": {"passed": True, "sha": orphan}})
    st, note = cd.classify("N", {"evidence_file": "docs/dag.json",
                                 "covers": ["covered.v"]})
    assert st == "REFUSED" and orphan[:8] in note


def test_issue_824_a_record_without_a_sha_is_refused_not_green(monkeypatch):
    """A record with no commit stamp cannot have staleness measured either --
    'never stamped a sha' is a different note from 'stamped a sha that is
    gone', but neither may render as current."""
    for rec in ({"passed": True}, {"passed": True, "sha": ""}):
        monkeypatch.setattr(cd, "load_results", lambda r=rec: {"N": r})
        st, _ = cd.classify("N", {"evidence_file": "docs/dag.json",
                                  "covers": ["README.md"]})
        assert st == "REFUSED", f"record {rec} rendered {st}"


def test_issue_824_a_passing_record_with_no_covers_is_refused(monkeypatch):
    """Rule 8: the input that defeats the sha guard -- an empty `covers`
    measures nothing and reports 'not stale' whatever the sha. Staleness
    needs something to go stale."""
    head = cd.git("rev-parse", "HEAD")
    monkeypatch.setattr(cd, "load_results",
                        lambda: {"N": {"passed": True, "sha": head}})
    st, note = cd.classify("N", {"evidence_file": "docs/dag.json"})
    assert st == "REFUSED" and "covered" in note


def test_issue_824_a_reachable_sha_with_a_changed_file_is_still_stale(tmp_path, monkeypatch):
    """The guard must not overfire: a sha in this history whose covered file
    genuinely changed keeps the honest STALE verdict."""
    v1, _ = _mini_repo(tmp_path, monkeypatch)
    monkeypatch.setattr(cd, "load_results",
                        lambda: {"N": {"passed": True, "sha": v1}})
    st, note = cd.classify("N", {"evidence_file": "docs/dag.json",
                                 "covers": ["covered.v"]})
    assert st == "STALE" and "covered.v" in note


def test_issue_824_a_current_sha_still_renders_green(tmp_path, monkeypatch):
    """Rule 5, condition 1: the clean run passes. A record stamped at HEAD
    with nothing changed since is GREEN -- the control above must stay red
    on exactly the difference between this node and itself."""
    _, v2 = _mini_repo(tmp_path, monkeypatch)
    monkeypatch.setattr(cd, "load_results",
                        lambda: {"N": {"passed": True, "sha": v2}})
    st, _ = cd.classify("N", {"evidence_file": "docs/dag.json",
                              "covers": ["covered.v"]})
    assert st == "GREEN"


def test_issue_824_a_refused_dependency_does_not_propagate(monkeypatch):
    """classify_all's limit is unchanged: only RED/BLOCKED deps propagate.
    One unresolvable sha must not black out the node's subtree."""
    own = {"A": ("REFUSED", "sha gone"), "B": ("GREEN", "ok")}
    monkeypatch.setattr(cd, "classify", lambda nid, n: own[nid])
    st = cd.classify_all({"A": {}, "B": {"deps": ["A"]}})
    assert st["A"] == ("REFUSED", "sha gone")
    assert st["B"][0] == "GREEN"


def test_issue_824_mermaid_renders_the_refused_status_without_a_keyerror():
    """mermaid()'s mark and fill lookups are defaultless dicts keyed on the
    status; an unwired status took the whole render down with a KeyError."""
    nodes = {"N": {"name": "n", "group": "foundation"}}
    out = cd.mermaid(nodes, {"N": ("REFUSED", "sha deadbee not in history")})
    assert 'N["? n"]' in out, "the refusal mark must render on the node"
    assert "  style N fill:" in out, "the refusal fill must render on the node"


def test_issue_824_the_gate_fails_and_names_the_node_on_stderr(monkeypatch, capsys):
    """--check --strict must fail on a REFUSED node DISTINGUISHABLY from
    today's four (F1/M1/D1 STALE, D3 RED): the stderr bad lines must name
    the node with the new status, not merely exit 1, which the gate already
    does on a clean tree. --print keeps the run read-only (it never touches
    README.md); the injected record bypasses run_evidence, so nothing here
    executes a suite."""
    real = dict(cd.load_results())
    real["F3"] = {"passed": True, "sha": "deadbeef" * 5}
    monkeypatch.setattr(cd, "load_results", lambda: real)
    monkeypatch.setattr(sys, "argv",
                        ["compile_dag.py", "--print", "--check", "--strict"])
    rc = cd.main()
    err = capsys.readouterr().err
    assert rc == 1
    assert "REFUSED: F3 -- recorded sha deadbee" in err, \
        f"the gate must name the refused node on stderr; got:\n{err}"

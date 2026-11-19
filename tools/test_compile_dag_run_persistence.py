"""`--run` survives a raising node, and its records survive the run (issue #823).

The `--run` loop accumulated each node's verdict in memory and wrote
docs/dag-results.json ONCE, after the loop, with no handler over the loop. All
three subprocess branches of run_evidence can raise -- `suite` (timeout=1800),
`verifier` (timeout=7200), `tool` (timeout=3600) -- and a TimeoutExpired on node
k escaped the loop, the write never executed, and the run's verdicts were
discarded. The previous run's records stayed on disk, so the harm was silent
staleness rather than an empty file: a node re-verified green at the new sha
kept its old sha and read STALE.

Rules pinned here, class not instance:

  * a raise from ANY evidence branch is that node's RED verdict carrying the
    exception type in `detail` -- never omitted, never left reading as the
    previous run's verdict, and never ending the run;
  * records reach the disk AFTER EACH NODE, before the next one is called: a
    job-level kill (the nightly's timeout-minutes, a runner eviction, SIGKILL)
    cannot be handled, only out-saved;
  * records already on disk from earlier runs survive a partial run;
  * load_results()'s REFUSED on a corrupt file stays loud (SystemExit is not
    swallowed); and a clean run writes exactly what the old single write wrote.

The controls drive the real `--run` path through main() with run_evidence
stubbed at its call site, so no real evidence executes and the tracked
docs/dag-results.json and README.md are never touched -- DAG and RESULTS point
into tmp_path, and `--print` keeps the render off README.md.
"""
from __future__ import annotations
import json, pathlib, subprocess, sys
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import compile_dag as cd                                            # noqa: E402


NODES = {
    "A": {"name": "alpha", "suite": "suite_a/test_x.py"},
    "B": {"name": "beta", "verifier": "verify_b.py"},
    "C": {"name": "gamma", "tool": "tools/tool_c.py"},
    "D": {"name": "delta", "evidence_file": "docs/evidence_d.json"},
}

SHA = "0" * 40


def _drive_run(monkeypatch, tmp_path, nodes=NODES, raise_at=None, exc=None,
               seed=None, seed_text=None, extra_args=()):
    """Run the real `--run` path against a fake DAG and a stubbed run_evidence.

    Returns (seen, results, rc). `seen[nid]` is what the results file held the
    moment nid's evidence was CALLED -- which pins the after-each-node write
    without ever having to kill a process.
    """
    dag = tmp_path / "dag.json"
    dag.write_text(json.dumps({"nodes": nodes}))
    results = tmp_path / "dag-results.json"
    if seed is not None:
        results.write_text(json.dumps(seed, indent=2, sort_keys=True) + "\n")
    if seed_text is not None:
        results.write_text(seed_text)
    seen: dict[str, dict] = {}

    def fake_run_evidence(nid, n):
        seen[nid] = json.loads(results.read_text()) if results.exists() else {}
        if nid == raise_at:
            raise exc
        return True, "ok"

    monkeypatch.setattr(cd, "DAG", dag)
    monkeypatch.setattr(cd, "RESULTS", results)
    monkeypatch.setattr(cd, "run_evidence", fake_run_evidence)
    monkeypatch.setattr(cd, "git", lambda *a: SHA)
    monkeypatch.setattr(cd.sc, "checkout_staleness", lambda: "")
    monkeypatch.setattr(sys, "argv",
                        ["compile_dag.py", "--run", "--print", *extra_args])
    rc = cd.main()
    return seen, results, rc


# The class, not the instance: the exception each of run_evidence's three
# subprocess branches raises (subprocess.TimeoutExpired, with that branch's own
# timeout), plus an unexpected raise from the fourth, non-subprocess branch.
RAISES = [
    ("A", subprocess.TimeoutExpired(cmd="pytest", timeout=1800)),
    ("B", subprocess.TimeoutExpired(cmd="verify_b.py", timeout=7200)),
    ("C", subprocess.TimeoutExpired(cmd="tools/tool_c.py", timeout=3600)),
    ("D", OSError("disk full while reading evidence")),
]


@pytest.mark.parametrize("raise_at,exc", RAISES,
                         ids=["suite-1800s", "verifier-7200s",
                              "tool-3600s", "evidence_file"])
def test_a_raise_from_any_branch_is_red_and_does_not_end_the_run(
        monkeypatch, tmp_path, raise_at, exc):
    seen, results, rc = _drive_run(monkeypatch, tmp_path, raise_at=raise_at, exc=exc)
    assert rc == 0                       # the run completed; RED is a result
    disk = json.loads(results.read_text())
    order = list(NODES)
    for nid in order:
        assert nid in disk, f"{nid}'s verdict was discarded by {raise_at}'s raise"
    assert disk[raise_at]["passed"] is False
    assert type(exc).__name__ in disk[raise_at]["detail"]
    assert disk[raise_at]["sha"] == SHA
    # verdicts already produced were on disk BEFORE the next node was called
    for nid in order[order.index(raise_at) + 1:]:
        for earlier in order[:order.index(nid)]:
            assert earlier in seen[nid], (
                f"{earlier}'s record was not on disk when {nid} started")


def test_earlier_records_survive_and_a_stale_verdict_is_overwritten(
        monkeypatch, tmp_path):
    old = {"passed": True, "sha": "old", "detail": "previous run"}
    seen, results, _ = _drive_run(
        monkeypatch, tmp_path, raise_at="B",
        exc=subprocess.TimeoutExpired(cmd="verify_b.py", timeout=7200),
        seed={"Z": dict(old), "B": dict(old)})
    disk = json.loads(results.read_text())
    assert disk["Z"] == old, "a node this run never reached was clobbered"
    assert seen["C"]["Z"] == old, "Z's record was lost mid-run"
    assert disk["B"]["passed"] is False
    assert "TimeoutExpired" in disk["B"]["detail"]
    assert disk["B"]["sha"] == SHA, "B still reads as its previous run"


def test_a_clean_run_writes_what_the_old_single_write_wrote(monkeypatch, tmp_path):
    seen, results, rc = _drive_run(monkeypatch, tmp_path)
    expected = {nid: {"passed": True, "sha": SHA, "detail": "ok"}
                for nid in NODES}
    assert rc == 0
    assert results.read_text() == json.dumps(expected, indent=2, sort_keys=True) + "\n"


def test_a_corrupt_results_file_still_refuses_through_the_run(monkeypatch, tmp_path):
    corrupt = '{"A": {"passed": true'
    with pytest.raises(SystemExit):
        _drive_run(monkeypatch, tmp_path, seed_text=corrupt)
    assert (tmp_path / "dag-results.json").read_text() == corrupt, \
        "the REFUSED file must not be overwritten"


def test_slow_nodes_are_still_skipped_unless_slow_is_passed(monkeypatch, tmp_path):
    nodes = dict(NODES)
    nodes["S"] = {"name": "slow node", "verifier": "slow_verifier.py",
                  "slow": True}
    _, results, _ = _drive_run(monkeypatch, tmp_path, nodes=nodes)
    assert "S" not in json.loads(results.read_text())
    _, results, _ = _drive_run(monkeypatch, tmp_path, nodes=nodes,
                               extra_args=["--slow"])
    assert "S" in json.loads(results.read_text())

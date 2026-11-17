"""The roadmap gate judges the README AS COMMITTED -- not its own output (#906).

rungs.yml's "the roadmap matches the evidence" step ran

    python tools/compile_dag.py --run || true
    python tools/compile_dag.py --check

`--run` regenerates README.md, so the `--check` after it compared the file
against a render of the records that same command had just written: the gate
could not fail for the only thing non-strict --check asserts -- README
staleness. Measured on PR #818's head (30f1b41e): the committed tree fails
`--check` outright (the rendered block disagrees with the committed README),
while the workflow step passed. This is CLAUDE.md's "a guard ships with the
input that defeats it", with the defeating input on the line immediately
above.

The fixed step checks FIRST, against the committed README, then regenerates.
Rules pinned here, class not instance:

  * on a stale committed README, the fixed step's check exits 1 (RED) where
    the pre-fix ordering -- regen, then check -- exits 0 on the SAME input
    (the injected defect; verification-rules rule 8);
  * the pre-fix ordering is green on that input because --run rewrote the
    README: the gate read its own output, never the committed tree;
  * on a regenerated README -- the author ran `python tools/compile_dag.py`
    and committed the result -- the fixed step is GREEN on both commands:
    the gate is satisfiable on honest PRs;
  * `--check` that fails does not rewrite the README: the divergence stays
    in the tree for the author to see;
  * why the alternative (regen, then `git diff --exit-code -- README.md`)
    was rejected: suite details render VERBATIM into the table and carry
    wall-clock timings ("105 passed in 472.54s"), so every fresh --run
    rewrites the README and the diff churns red on every CI run. Pinned by
    test_detail_text_renders_verbatim_into_the_table.

The controls drive the real main() with run_evidence stubbed at its call
site and DAG/RESULTS/README pointed into tmp_path, so no real evidence
executes and the tracked files are never touched -- the same harness as
test_compile_dag_run_persistence.py (#823).
"""
from __future__ import annotations
import json, pathlib, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import compile_dag as cd                                            # noqa: E402

# The suite and cover paths must EXIST relative to the real ROOT -- classify()
# refuses to judge a node whose evidence file is missing, and staleness is a
# real `git diff` against this repository's HEAD. run_evidence itself is
# stubbed, so no suite actually executes.
NODES = {
    "A": {"name": "alpha", "group": "foundation",
          "suite": "model/test_audio_measure.py",
          "covers": ["model/audio_measure.py"]},
}
DETAIL = "153 passed in 3.70s"          # F3's detail on main: timing included
SHELL = "# roadmap\n\n<!-- DAG:BEGIN -->\nstale block\n<!-- DAG:END -->\n"


def _seed() -> dict:
    sha = cd.git("rev-parse", "HEAD")
    assert sha, "staleness is measured against HEAD; git answered nothing"
    return {"A": {"passed": True, "sha": sha, "detail": DETAIL}}


def _stage(monkeypatch, tmp_path):
    dag = tmp_path / "dag.json"
    dag.write_text(json.dumps({"nodes": NODES}))
    results = tmp_path / "dag-results.json"
    results.write_text(json.dumps(_seed(), indent=2, sort_keys=True) + "\n")
    readme = tmp_path / "README.md"
    readme.write_text(SHELL)
    monkeypatch.setattr(cd, "DAG", dag)
    monkeypatch.setattr(cd, "RESULTS", results)
    monkeypatch.setattr(cd, "README", readme)
    monkeypatch.setattr(cd.sc, "checkout_staleness", lambda: "")
    monkeypatch.setattr(cd, "run_evidence", lambda nid, n: (True, DETAIL))
    return readme


def _compile(monkeypatch, *flags: str) -> int:
    monkeypatch.setattr(sys, "argv", ["compile_dag.py", *flags])
    return cd.main()


def _regenerated(monkeypatch, tmp_path):
    """The honest tree: evidence committed, README regenerated from it."""
    readme = _stage(monkeypatch, tmp_path)
    assert _compile(monkeypatch) == 0           # a bare regen rewrites README
    assert readme.read_text().count("**GREEN**") == 1
    return readme


def _stale(readme) -> None:
    """Commit a claim the evidence does not make: GREEN rewritten STAMPED."""
    text = readme.read_text()
    assert text.count("**GREEN**") == 1
    readme.write_text(text.replace("**GREEN**", "**STAMPED**"))


def test_prefix_ordering_is_green_on_a_stale_committed_readme(
        monkeypatch, tmp_path):
    # The defect, reproduced: the pre-fix step order on the input that must
    # fail it. --run rewrites the README first; the check then reads what the
    # line above wrote and passes.
    readme = _regenerated(monkeypatch, tmp_path)
    _stale(readme)
    assert _compile(monkeypatch, "--run") == 0
    assert _compile(monkeypatch, "--check") == 0, (
        "pre-fix ordering was green on a stale committed README -- "
        "if this now fails, the defeat is gone and this control is stale")


def test_postfix_ordering_is_red_on_the_same_stale_input(
        monkeypatch, tmp_path):
    readme = _regenerated(monkeypatch, tmp_path)
    _stale(readme)
    assert _compile(monkeypatch, "--check") == 1, (
        "the gate answered GREEN on a README the evidence does not support")
    # A failing --check must not launder the divergence off the disk (#600's
    # REFUSED shape: refuse, don't "fix").
    assert "**STAMPED**" in readme.read_text()


def test_postfix_ordering_is_green_on_a_regenerated_readme(
        monkeypatch, tmp_path):
    # The satisfiability control: a PR that carries the regenerated README
    # passes both commands of the fixed step.
    readme = _regenerated(monkeypatch, tmp_path)
    assert _compile(monkeypatch, "--check") == 0
    assert _compile(monkeypatch, "--run") == 0
    assert "**GREEN**" in readme.read_text()


def test_detail_text_renders_verbatim_into_the_table(
        monkeypatch, tmp_path, capsys):
    # Why regen-then-`git diff -- README.md` is not the fix: the rendered
    # block embeds each record's detail string, and suite details carry
    # wall-clock timings, so every fresh --run changes the README and the
    # diff churns red on every CI run (main's committed records hold
    # "105 passed in 472.54s (0:07:52)" for D2, measured 2026-11-17).
    _stage(monkeypatch, tmp_path)
    assert _compile(monkeypatch, "--print") == 0
    assert DETAIL in capsys.readouterr().out

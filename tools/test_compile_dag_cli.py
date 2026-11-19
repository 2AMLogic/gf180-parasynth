"""The `--only` CLI contract, driven through `main()` (issue #826).

`--only ID[,ID...]` exists so an operator can re-record ONE node without
re-running every fast node. Until #826 it had two holes, both measured on the
pre-fix tree:

  * a KNOWN id the loop then declined to run (slow / blocked / no evidence
    key) produced no output and exit 0 -- silence shaped exactly like a pass;
  * the degenerate values `''` and `','` bypassed the unknown-id REFUSAL
    entirely -- `''` fell through to a FULL bare `--run`, re-recording every
    fast node and rewriting their shas, the precise harm the flag exists to
    prevent.

No test anywhere drove `main()` or set `sys.argv` before this file: the CLI
seam is new. Every case below monkeypatches `cd.DAG` / `cd.RESULTS` /
`cd.git` against a fixture DAG (whose node ORDER matters -- the slow node
sits before the runnable one so a `return 2` placed inside the loop would
abandon the runnable node and be caught), then invokes `cd.main()`.

Controls carried (docs/verification-rules.md): each refusal case asserts a
behaviour the pre-fix tree got wrong -- exit code, the printed line, and
records-unchanged -- not just "something was printed".
"""
from __future__ import annotations
import copy, json, pathlib, sys
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import compile_dag as cd                                            # noqa: E402
import scorecard as sc                                              # noqa: E402

SHA = "cafe1234cafe1234cafe1234cafe1234cafe1234"

# Node order is part of the fixture: SF sits BEFORE RN so an in-loop early
# `return 2` at SF's refusal would skip RN entirely -- the shape test
# test_only_mixed_runs_the_runnable_node exists to catch. BL is blocked AND
# declares no evidence key, so it hits one `continue` for two reasons: the
# line must name one reason deterministically (blocked first, classify()'s
# own order). The blocked prose is over 70 chars to pin the truncation.
BLOCKED_PROSE = ("waiting on the drum brake circuit rework, the clap envelope "
                 "retrigger fix, and the hat bandpass retune")
NODES = {
    "SF":  {"name": "slow before runnable", "slow": True,
            "evidence_file": "sf.json", "covers": ["sf.json"]},
    "RN":  {"name": "runnable", "evidence_file": "rn.json",
            "covers": ["rn.json"]},
    "BL":  {"name": "blocked", "blocked": BLOCKED_PROSE},
    "NE":  {"name": "no evidence key declared"},
    "OK":  {"name": "untouched bystander", "evidence_file": "ok.json",
            "covers": ["ok.json"]},
    "SF2": {"name": "second slow", "slow": True, "evidence_file": "sf2.json",
            "covers": ["sf2.json"]},
    "SF3": {"name": "third slow", "slow": True, "evidence_file": "sf3.json",
            "covers": ["sf3.json"]},
}


def _invoke(monkeypatch, tmp_path, capsys, extra, seed=None):
    """Drive cd.main() against a fixture DAG; return (rc, stderr, records-path).

    Neutralises every tree/git reader (staleness banner, ancestor queries,
    content diff) so the only variable is the --only contract. `seed` is the
    pre-existing results dict; the SEED record below belongs to a node that is
    NOT in the fixture DAG, so it can only reach the output file by being
    preserved verbatim.
    """
    nodes = copy.deepcopy(NODES)
    for n in nodes.values():
        if "evidence_file" in n:
            n["evidence_file"] = str(tmp_path / n["evidence_file"])
    dag = tmp_path / "dag.json"
    dag.write_text(json.dumps({"nodes": nodes}))
    for name in ("sf.json", "rn.json", "ok.json", "sf2.json", "sf3.json"):
        (tmp_path / name).write_text(json.dumps({"passed": True}))
    results = tmp_path / "dag-results.json"
    seed_text = json.dumps(seed if seed is not None else
                           {"ZZ": {"passed": True, "sha": SHA,
                                   "detail": "pre-existing record"}}, indent=2)
    results.write_text(seed_text)
    monkeypatch.setattr(cd, "DAG", dag)
    monkeypatch.setattr(cd, "RESULTS", results)
    monkeypatch.setattr(cd, "git", lambda *a: SHA)
    monkeypatch.setattr(sc, "checkout_staleness", lambda: "")
    monkeypatch.setattr(cd, "sha_is_ancestor", lambda sha: bool(sha))
    monkeypatch.setattr(cd, "stale_against", lambda n, at: ("fresh", ""))
    monkeypatch.setattr(sys, "argv", ["compile_dag.py", "--run", "--print", *extra])
    rc = cd.main()
    captured = capsys.readouterr()
    return rc, captured.err, captured.out, results


def _refuse_lines(err: str, nid: str) -> list[str]:
    return [l for l in err.splitlines()
            if l.split()[:2] == ["REFUSE", nid]]


# ---------------------------------------------------------------------------
# A KNOWN id the loop declines to run: one REFUSE line naming node and why,
# non-zero exit, records untouched.
# ---------------------------------------------------------------------------
def test_only_slow_refuses_and_names_the_remedy(monkeypatch, tmp_path, capsys):
    rc, err, out, results = _invoke(monkeypatch, tmp_path, capsys, ["--only", "SF"])
    lines = _refuse_lines(err, "SF")
    assert len(lines) == 1, f"expected exactly one refusal line, got {lines}"
    assert "--slow" in lines[0], "the line must name the remedy"
    assert rc == 2, f"a refused --only must exit 2, got {rc}"
    assert json.loads(results.read_text()) == {
        "ZZ": {"passed": True, "sha": SHA, "detail": "pre-existing record"}}


def test_only_blocked_refuses_with_short_reason(monkeypatch, tmp_path, capsys):
    rc, err, _, _ = _invoke(monkeypatch, tmp_path, capsys, ["--only", "BL"])
    lines = _refuse_lines(err, "BL")
    assert len(lines) == 1
    assert "blocked" in lines[0]
    # BL is blocked AND evidence-less: one reason, deterministically (blocked,
    # classify()'s own order), not both and not neither.
    assert "no evidence key" not in lines[0]
    # The real prose is >70 chars; classify() renders it in full on the board,
    # so the per-node line must carry the truncation, not the dump.
    assert BLOCKED_PROSE not in lines[0]
    assert rc == 2


def test_only_no_evidence_key_refuses(monkeypatch, tmp_path, capsys):
    rc, err, _, _ = _invoke(monkeypatch, tmp_path, capsys, ["--only", "NE"])
    lines = _refuse_lines(err, "NE")
    assert len(lines) == 1
    assert "no evidence key" in lines[0]
    assert rc == 2


def test_only_all_slow_gets_one_line_each(monkeypatch, tmp_path, capsys):
    rc, err, _, _ = _invoke(monkeypatch, tmp_path, capsys,
                         ["--only", "SF,SF2,SF3"])
    for nid in ("SF", "SF2", "SF3"):
        assert len(_refuse_lines(err, nid)) == 1, f"{nid} needs its own line"
    assert rc == 2


def test_only_repeated_id_gets_one_line(monkeypatch, tmp_path, capsys):
    rc, err, _, _ = _invoke(monkeypatch, tmp_path, capsys, ["--only", "SF,SF"])
    assert len(_refuse_lines(err, "SF")) == 1
    assert rc == 2


# ---------------------------------------------------------------------------
# The refusal must not abandon what DID run, nor skip the classification and
# render after it (#600's shape: an early return discarding completed work).
# ---------------------------------------------------------------------------
def test_only_mixed_runs_the_runnable_node(monkeypatch, tmp_path, capsys):
    # SF is slow and sits FIRST in the fixture DAG: a `return 2` placed inside
    # the loop would refuse SF and never reach RN. RN must still be executed,
    # recorded, and rendered.
    rc, err, out, results = _invoke(monkeypatch, tmp_path, capsys,
                               ["--only", "SF,RN"])
    assert len(_refuse_lines(err, "SF")) == 1
    assert any(l.split()[:2] == ["PASS", "RN"] for l in err.splitlines()), err
    rec = json.loads(results.read_text())
    assert rec.get("RN", {}).get("passed") is True, "RN must still be recorded"
    assert rec["RN"]["sha"] == SHA
    assert "ZZ" in rec, "the seed record must survive"
    assert "`RN`" in out, "the render after the loop must still have run"
    assert rc == 2, "exit is non-zero even though RN ran clean"


def test_only_whitespace_and_comma_parse_like_ids(monkeypatch, tmp_path, capsys):
    rc, err, out, results = _invoke(monkeypatch, tmp_path, capsys,
                               ["--only", "SF, RN"])
    assert len(_refuse_lines(err, "SF")) == 1
    assert json.loads(results.read_text()).get("RN", {}).get("passed") is True
    assert rc == 2


# ---------------------------------------------------------------------------
# The degenerate values -- the guard ships with the inputs that defeat it.
# ---------------------------------------------------------------------------
def test_only_empty_string_refuses_not_full_run(monkeypatch, tmp_path, capsys):
    seed = {"ZZ": {"passed": True, "sha": SHA, "detail": "pre-existing record"}}
    rc, err, out, results = _invoke(monkeypatch, tmp_path, capsys, ["--only", ""],
                               seed=seed)
    # Assert no node was executed, not merely the exit code: the pre-fix
    # behaviour was a FULL bare --run, so an exit-code-only assertion would
    # pass against a fix that still ran everything.
    assert rc == 2
    assert "REFUSED" in err
    assert json.loads(results.read_text()) == seed, \
        "an empty --only must not degrade into a full --run"
    assert not [l for l in err.splitlines()
                if l.split()[:1] in (["PASS"], ["FAIL"])]


def test_only_comma_only_refuses(monkeypatch, tmp_path, capsys):
    rc, err, out, results = _invoke(monkeypatch, tmp_path, capsys, ["--only", ","])
    assert rc == 2
    assert "REFUSED" in err
    assert json.loads(results.read_text()) == {
        "ZZ": {"passed": True, "sha": SHA, "detail": "pre-existing record"}}


def test_only_unknown_id_keeps_existing_refusal(monkeypatch, tmp_path, capsys):
    rc, err, _, _ = _invoke(monkeypatch, tmp_path, capsys, ["--only", "NOPE"])
    assert rc == 2
    assert "REFUSED: --only names no such node: NOPE" in err


# ---------------------------------------------------------------------------
# Bare --run is unchanged: the skips are silent BY DESIGN when nothing was
# named, and the exit stays 0.
# ---------------------------------------------------------------------------
def test_bare_run_stays_silent_about_its_skips(monkeypatch, tmp_path, capsys):
    rc, err, out, results = _invoke(monkeypatch, tmp_path, capsys, [])
    assert rc == 0, err
    assert "REFUSE" not in err, "bare --run must not gain refusal noise"
    rec = json.loads(results.read_text())
    assert rec["RN"]["passed"] is True
    assert rec["OK"]["passed"] is True
    assert "SF" not in rec and "SF2" not in rec and "SF3" not in rec
    assert "BL" not in rec and "NE" not in rec
    assert "ZZ" in rec


# ---------------------------------------------------------------------------
# The documented remedy works: --slow runs the named slow node, exit 0.
# ---------------------------------------------------------------------------
def test_only_slow_with_slow_flag_runs_it(monkeypatch, tmp_path, capsys):
    rc, err, out, results = _invoke(monkeypatch, tmp_path, capsys,
                               ["--only", "SF", "--slow"])
    assert any(l.split()[:2] == ["PASS", "SF"] for l in err.splitlines()), err
    assert json.loads(results.read_text())["SF"]["passed"] is True
    assert rc == 0, err

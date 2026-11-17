"""A re-run that shows nothing new leaves the old record's bytes alone (#967).

dag.yml regenerates docs/dag-results.json and README.md on EVERY push to main,
and the writer used to rewrite every record's `sha` on every regeneration --
measured in issue #967: all seven records, including nodes nothing observed
changed. A PR whose deliverable IS a change to one of those records therefore
conflicts with every merge to main while its own CI runs (~41 min against a
median regeneration gap of about the same), which re-resolved PR #810 seven
times without ever landing it.

The rule pinned here, in the writer (--run), is three-legged, and each leg has
a test that goes red if the leg is dropped:

  * same verdict + same detail modulo the wall-clock tail -> the old record is
    kept byte-for-byte, so the bot's next commit stops touching those lines;
  * THE FRESHNESS CONDITION: the recorded `sha` must still be fresh over
    `covers` (stale_against), or the record is re-anchored to the new run's
    sha even when verdict and detail repeat. `sha` is the staleness anchor
    classify() measures against; keeping a stale one would let a node read
    GREEN forever off evidence taken before the change. The injected-bug
    control (tools/control_dag_churn.py) drops exactly this leg and requires
    the covers-changed test to go red;
  * unmeasurable is not fresh: no sha, a sha outside this history, or a git
    failure rewrites -- the pre-#967 behaviour -- rather than vouch for
    evidence that cannot be anchored (#824's discipline, in the writer).

Timing normalisation cannot hide a count change: the counts sit before
` in ` and the strip is end-anchored, so `105 passed in 3s` vs `104 passed
in 3s` is a rewrite.

The controls drive the real --run path through main() against a REAL scratch
git repository (cd.ROOT repointed at it; two commits with content this test
chooses), with run_evidence stubbed at its call site -- so the freshness
question is answered by git diffing actual commits, not by a stub calibrated
on the rule it is testing. The tracked docs/dag-results.json and README.md
are never touched: DAG and RESULTS point into tmp_path, and --print keeps the
render off README.md.
"""
from __future__ import annotations
import json, pathlib, subprocess, sys
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import compile_dag as cd                                            # noqa: E402

NODE = {"name": "alpha", "suite": "suite_a/test_x.py",
        "covers": ["model/voice_fx.py"]}
COVERS = "model/voice_fx.py"


def _git(repo, *a):
    r = subprocess.run(["git", *a], cwd=str(repo), capture_output=True, text=True)
    assert r.returncode == 0, f"git {' '.join(a)} failed: {r.stderr}"
    return r.stdout.strip()


def _advance(repo, change_covers: bool) -> str:
    """Commit B on top of A, touching either the covered file or an unrelated
    one. Returns B's sha."""
    f = (repo / "model" / "voice_fx.py") if change_covers \
        else (repo / "docs" / "notes.md")
    f.write_text(("v2 -- covered change\n") if change_covers
                 else "n2 -- unrelated change\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "B")
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path):
    """A scratch history at commit A, plus a drive() that runs the REAL --run
    path at the current HEAD with run_evidence stubbed and RESULTS seeded."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "suite_a").mkdir()
    (tmp_path / "suite_a" / "test_x.py").write_text("def test_x(): pass\n")
    (tmp_path / "model").mkdir()
    (tmp_path / "model" / "voice_fx.py").write_text("v1\n")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "notes.md").write_text("n1\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "A")
    sha_a = _git(tmp_path, "rev-parse", "HEAD")

    def drive(monkeypatch, seed=None,
              verdict=(True, "105 passed in 3.45s")):
        results = tmp_path / "dag-results.json"
        seed_text = None
        if seed is not None:
            seed_text = json.dumps(seed, indent=2, sort_keys=True) + "\n"
            results.write_text(seed_text)
        dag = tmp_path / "dag.json"
        dag.write_text(json.dumps({"nodes": {"A1": dict(NODE)}}))
        calls: list[str] = []

        def fake_run_evidence(nid, n):
            calls.append(nid)
            return verdict

        monkeypatch.setattr(cd, "ROOT", tmp_path)
        monkeypatch.setattr(cd, "DAG", dag)
        monkeypatch.setattr(cd, "RESULTS", results)
        monkeypatch.setattr(cd, "run_evidence", fake_run_evidence)
        monkeypatch.setattr(cd.sc, "checkout_staleness", lambda: "")
        monkeypatch.setattr(sys, "argv", ["compile_dag.py", "--run", "--print"])
        rc = cd.main()
        after = results.read_text() if results.exists() else None
        return rc, calls, seed_text, after

    return tmp_path, sha_a, drive


def test_run_twice_here_then_once_at_a_covers_untouched_head_keeps_bytes(
        monkeypatch, repo):
    """AC1: the full sequence -- a run at one HEAD, a second run at the same
    HEAD, then a run at a HEAD that changed no covered file -- all keep the
    seeded record's bytes, while the evidence still runs every time."""
    tmp_path, sha_a, drive = repo
    seed = {"A1": {"passed": True, "sha": sha_a,
                   "detail": "105 passed in 3.20s"}}

    rc, calls, seed_text, after = drive(monkeypatch, seed=seed)
    assert rc == 0
    assert after == seed_text, "run 1 at A rewrote a record nothing changed"
    assert calls == ["A1"], "suppression must suppress the WRITE, not the run"

    rc, calls, _, after = drive(monkeypatch, seed=seed)
    assert calls == ["A1"] and after == seed_text, "second run at A moved bytes"

    sha_b = _advance(tmp_path, change_covers=False)
    assert sha_b != sha_a
    for run_no in (1, 2):
        rc, calls, _, after = drive(monkeypatch, seed=seed)
        assert rc == 0 and calls == ["A1"]
        rec = json.loads(after)["A1"]
        assert rec["sha"] == sha_a and rec["detail"] == "105 passed in 3.20s"
        assert after == seed_text, f"run {run_no} at B (covers untouched) moved bytes"


def test_a_covered_change_reanchors_the_record_even_when_the_verdict_matches(
        monkeypatch, repo):
    """AC2, and the target of tools/control_dag_churn.py's injected bugs: a
    covered file changed since the recorded sha, so the record MUST be
    rewritten (new sha) even though the re-run's verdict and detail repeat.
    Drop the freshness leg of record_unchanged and this goes red -- the node
    would read GREEN forever off pre-change evidence."""
    tmp_path, sha_a, drive = repo
    seed = {"A1": {"passed": True, "sha": sha_a,
                   "detail": "105 passed in 3.20s"}}
    sha_b = _advance(tmp_path, change_covers=True)

    rc, calls, seed_text, after = drive(monkeypatch, seed=seed)
    assert rc == 0 and calls == ["A1"]
    rec = json.loads(after)["A1"]
    assert rec["sha"] == sha_b, "a covered change left the record at the old sha"
    assert rec["detail"] == "105 passed in 3.45s", \
        "the re-anchored record must carry the run's own evidence line"
    assert after != seed_text


def test_a_verdict_flip_or_a_change_beyond_timing_rewrites(monkeypatch, repo):
    """AC3: neither leg of 'nothing new' may be fooled -- a verdict flip and a
    detail that differs outside its wall-clock tail both rewrite, covers
    untouched, so this isolates the verdict/detail legs."""
    tmp_path, sha_a, drive = repo
    seed = {"A1": {"passed": True, "sha": sha_a,
                   "detail": "105 passed in 3.20s"}}
    sha_b = _advance(tmp_path, change_covers=False)

    rc, _, _, after = drive(
        monkeypatch, seed=seed,
        verdict=(False, "1 failed, 104 passed in 3.45s"))
    rec = json.loads(after)["A1"]
    assert rec["passed"] is False and rec["sha"] == sha_b, \
        "a verdict flip was carried forward on the old record"

    rc, _, _, after = drive(
        monkeypatch, seed=seed,
        verdict=(True, "105 passed, 1 xfailed in 3.45s"))
    rec = json.loads(after)["A1"]
    assert rec["detail"] == "105 passed, 1 xfailed in 3.45s", \
        "a detail change beyond its timing tail was hidden by normalisation"


def test_normalisation_cannot_hide_a_count_change(monkeypatch, repo):
    """AC4: same duration, different count -> a rewrite. The counts sit
    before ` in `; the strip is end-anchored, so they are never touched."""
    tmp_path, sha_a, drive = repo
    seed = {"A1": {"passed": True, "sha": sha_a,
                   "detail": "105 passed in 3.00s"}}
    sha_b = _advance(tmp_path, change_covers=False)

    rc, _, _, after = drive(monkeypatch, seed=seed,
                            verdict=(True, "104 passed in 3.00s"))
    rec = json.loads(after)["A1"]
    assert rec["detail"] == "104 passed in 3.00s" and rec["sha"] == sha_b, \
        "a count change dressed in an identical duration was suppressed"

    # and the pure property, independent of the run path
    assert cd.normalise_detail("105 passed in 3.45s") \
        == cd.normalise_detail("105 passed in 9.99s")
    assert cd.normalise_detail("105 passed in 3.45s") \
        != cd.normalise_detail("104 passed in 3.45s")


def test_an_unanchored_old_sha_is_not_fresh_and_rewrites(monkeypatch, repo):
    """Unmeasurable is not fresh (#824's discipline in the writer): an old
    record whose sha is outside this history -- or absent -- cannot be vouched
    for, so the rewrite path runs even with verdict and detail repeating."""
    tmp_path, sha_a, drive = repo
    sha_b = _advance(tmp_path, change_covers=False)
    for bad_sha in ("f" * 40, ""):
        seed = {"A1": {"passed": True, "sha": bad_sha,
                       "detail": "105 passed in 3.20s"}}
        rc, calls, _, after = drive(monkeypatch, seed=seed,
                                    verdict=(True, "105 passed in 3.45s"))
        rec = json.loads(after)["A1"]
        assert rec["sha"] == sha_b, \
            f"sha {bad_sha[:8]!r} was treated as fresh over covers"
        assert rec["detail"] == "105 passed in 3.45s"


def test_an_identically_failing_record_is_kept_until_covers_move(
        monkeypatch, repo):
    """D3's real shape: RED on every push, detail identical, sha churned by
    every regeneration. Same verdict, same detail, covers fresh -> kept. The
    moment the covered file moves, re-anchor even though it still fails."""
    tmp_path, sha_a, drive = repo
    seed = {"A1": {"passed": False, "sha": sha_a,
                   "detail": "model/sound_report.py exit 1"}}

    rc, _, seed_text, after = drive(
        monkeypatch, seed=seed,
        verdict=(False, "model/sound_report.py exit 1"))
    assert after == seed_text, "an identical RED's record was rewritten"

    sha_b = _advance(tmp_path, change_covers=True)
    rc, _, _, after = drive(
        monkeypatch, seed=seed,
        verdict=(False, "model/sound_report.py exit 1"))
    rec = json.loads(after)["A1"]
    assert rec["sha"] == sha_b and rec["passed"] is False


def test_normalise_detail_strips_only_a_trailing_wall_clock():
    """The strip's boundary, pinned: pytest's two summary shapes strip; details
    with no tail, a tail cut mid-unit, a tail cut inside the parenthesised
    clock, or a timing-shaped fragment mid-string all stay UNCHANGED -- each
    unmatched case keeps the old rewrite behaviour, never a false freshness."""
    strip = {
        "105 passed in 3.81s": "105 passed",
        "105 passed in 472.19s (0:07:52)": "105 passed",
        "5 passed, 1 warning in 0.02s": "5 passed, 1 warning",
        "1 failed, 104 passed in 1.23s": "1 failed, 104 passed",
        "105 passed in 12ms": "105 passed",
    }
    for raw, want in strip.items():
        assert cd.normalise_detail(raw) == want, raw

    keep = [
        "105 passed in 3.4",                 # truncated mid-unit by run_evidence
        "105 passed in 472.19s (0:07:",      # truncated inside the clock
        "model/sound_report.py exit 0",      # no timing tail at all
        "docs/x.json lacks 'passed in 3s'",  # timing-shaped, not at the end
        "105 passed",                        # already bare
    ]
    for raw in keep:
        assert cd.normalise_detail(raw) == raw, raw

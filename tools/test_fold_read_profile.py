"""Validation cases for tools/fold_read_profile.py (#2112).

The instrument answers one question -- what share of a run's wall time
`discrimination_features._read_at` accounts for -- and a wrong answer there
looks exactly like a right one: a share of 0 % reads as "negligible, close the
issue" whether the kernel is cheap or whether the wrapper was never the
function the fold called. So every control here is one of the two ways this
instrument can lie, fed to it on purpose:

  * it attributes time wrongly -- a deliberately slowed `_read_at` must come
    back DOMINANT, and the seconds it was slowed by must be seen where the
    wrapper says they are;
  * it answers about a run that is not the one asked about -- a suite that
    failed or collected nothing, a module whose `_read_at` was rebound under
    the wrapper, repeats that did not read the same things, a host too loaded
    to time on, a candidate table that no longer names every caller.

The two edge cases the issue names are here too: `taps <= 0` (the index-only
path, which reads but evaluates no kernel) and `taps = -1` (the legacy fold,
which never reaches `_read_at`) must each be counted for what they are and
refused as a stand-in for the kernel.

The whole-suite timings are NOT measured here: this file runs the instrument
on workloads of about a second, on whatever host runs the suite. The
measurement #2112 is closed on is recorded in docs/fold-read-cost-2026-11-28/.
"""
from __future__ import annotations

import json
import os
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import fold_read_profile as P                                    # noqa: E402

FAMILY_FILE = "model/discrimination_features.py"
FOLD_TEST = P.Candidate("fold-test", "pytest",
                        ("model/discrimination_features.py", "-q", "-p", "no:cacheprovider",
                         "-k", "reduces_to_indexing"), FAMILY_FILE, True)


def _work(taps: int, repeats: int = 2):
    """A workload of the shipped `extra_features` at one tap width: `mpd`'s
    folds, `jitter`'s fold, and the CQT and multiscale columns beside them, so
    `_read_at` is a FRACTION of the run rather than the whole of it."""
    def run(reader):
        D = P.module()
        x = D._square(220.0, 44100)
        for _ in range(repeats):
            D.extra_features(x, 44100, taps)
    return run


def _call(cid: str, run) -> P.Candidate:
    return P.Candidate(cid, "call", run, FAMILY_FILE, True)


def _codes(problems: list) -> set[str]:
    return {code for code, _ in problems}


def _cleanest(cand: P.Candidate, tries: int = 3) -> dict:
    """The clean run with the least wall time of `tries`. Preemption only ever
    adds time, so the least is the closest to the run an idle host makes, and
    a control's arithmetic is about what the SLOWING added, not about what a
    busy host added to the clean run."""
    return min((P.profile(cand) for _ in range(tries)), key=lambda r: r["wall_s"])


# ---- the control: a slowed `_read_at` is reported as dominant ---------------
def test_a_slowed_read_at_is_reported_as_dominant():
    """The shipped `extra_features`, timed clean and timed with `_read_at`
    made to do its work CONTROL_REPEATS times over. The slowed read must be
    the DOMINANT cost of its run, and the time the repeats added must be
    inside `_read_at`'s seconds, where the wrapper books it -- not scattered
    into the wall where no function owns it."""
    cand = _call("extra-features", _work(P.module().FOLD_SINC_TAPS))
    clean, slow = _cleanest(cand), P.profile(cand, control=P.CONTROL_REPEATS)
    assert clean["kernel_pos"] > 0, clean
    assert slow["share"] > P.DOMINANT, (clean["share"], slow["share"])
    assert slow["share"] > clean["share"], (clean["share"], slow["share"])
    # the repeats were executed, inside `_read_at`, and nowhere else
    assert slow["read_at_s"] >= (P.CONTROL_REPEATS / 2) * clean["read_at_s"], (clean, slow)
    grew = slow["wall_s"] - clean["wall_s"]
    assert grew >= 0.8 * (slow["read_at_s"] - clean["read_at_s"]), (grew, clean, slow)
    # ...and it was the same run: the slowed read read the same positions
    assert (slow["calls"], slow["kernel_pos"], slow["index_pos"]) == (
        clean["calls"], clean["kernel_pos"], clean["index_pos"])


def test_the_control_sees_a_slowed_read_inside_a_pytest_run_too():
    """The same control through the pytest plugin -- the path every
    collected suite takes -- on the module's own whole-period fold test."""
    clean, slow = _cleanest(FOLD_TEST), P.profile(FOLD_TEST, control=P.CONTROL_REPEATS)
    assert clean["tests"] == {"collected": 1, "passed": 1, "failed": 0, "errors": 0,
                               "skipped": 0}, clean["tests"]
    assert slow["read_at_s"] >= (P.CONTROL_REPEATS / 2) * clean["read_at_s"], (clean, slow)
    assert slow["wall_s"] - clean["wall_s"] >= 0.8 * (slow["read_at_s"] - clean["read_at_s"])
    assert (slow["calls"], slow["kernel_pos"]) == (clean["calls"], clean["kernel_pos"])
    assert clean["unwrapped"] == [] and slow["unwrapped"] == []


# ---- refusal: a run that is not the one being asked about -------------------
def _record(cid: str = "a", **over) -> dict:
    """A child record with every field an honest, complete run has."""
    rec = {"id": cid, "file": FAMILY_FILE, "kind": "call", "control": 0, "bare": False,
           "rc": 0, "wall_s": 20.0, "read_at_s": 2.0, "share": 0.1,
           "calls": 40, "kernel_pos": 400_000, "index_pos": 0,
           "by_caller": {"_fold_at<-mpd_features@16": {"calls": 40, "positions": 400_000,
                                                        "s": 2.0}},
           "rebinds": 0, "unwrapped": [], "modules": ["discrimination_features"],
           "overhead_s": 1e-4, "tests": None, "pytest_s": None}
    rec.update(over)
    return rec


def _bare(cid: str = "a", wall: float = 20.0) -> dict:
    """A bare twin carries a wall time and nothing else: no wrapper ran."""
    return _record(cid, bare=True, rc=0, wall_s=wall, read_at_s=0.0, share=0.0,
                   calls=0, kernel_pos=0, index_pos=0, by_caller={}, overhead_s=0.0,
                   unwrapped=["never looked"])


def _tally(**over) -> dict:
    """A pytest tally: five tests collected, every one of them passed."""
    return {"collected": 5, "passed": 5, "failed": 0, "errors": 0, "skipped": 0, **over}


def _cands(*ids: str) -> list[P.Candidate]:
    return [_call(i, None) for i in ids]


def test_a_complete_run_verifies_clean():
    recs = [_record(wall_s=w, read_at_s=r, share=r / w) for w, r in
            ((20.0, 2.0), (21.0, 2.1), (19.5, 1.95))]
    cands, bare = _cands("a"), {"a": _bare()}
    assert P.verify({"a": recs}, bare, cands, headline="a", smoke=False) == []
    # the same call is green because the run is whole, not because it cannot see
    assert P.verify({"a": recs[:2] + [_record(wall_s=20.0, rc=1)]}, bare, cands, headline="a",
                    smoke=False), "a verify that never refuses passes over anything"


def test_the_cli_control_must_see_the_slowdown_and_call_it_dominant():
    """`--control`'s three conditions on the headline's slowed repeat: the
    read comes back dominant, the repeats were executed, and the time they
    added is where the wrapper books it."""
    clean = P.aggregate([_record(wall_s=10.0, read_at_s=2.0, share=0.2) for _ in range(3)],
                        _bare(wall=10.0))
    good = _record(control=20, wall_s=48.0, read_at_s=40.0, share=40.0 / 48.0)
    assert P.control_problems(clean, good) == []
    weak = dict(good, read_at_s=8.0, wall_s=16.0, share=0.6)
    assert {c for c, _ in P.control_problems(clean, weak)} == {"control_seen"}, weak
    scattered = dict(good, wall_s=30.0)
    assert {c for c, _ in P.control_problems(clean, scattered)} == {"control_wall"}
    assert {c for c, _ in P.control_problems(clean, dict(good, share=0.3))} == {"control_dominant"}


@pytest.mark.parametrize("over, code", [
    (dict(rc=1), "exit"),
    (dict(rc=2), "exit"),
    (dict(unwrapped=["model.discrimination_features"]), "unwrapped"),
    (dict(overhead_s=0.5), "overhead"),
    (dict(tests=_tally(passed=4, failed=1)), "incomplete"),
    (dict(tests=_tally(passed=4, errors=1)), "incomplete"),
    (dict(tests=_tally(passed=3, skipped=1)), "incomplete"),
    (dict(tests=_tally(collected=0)), "incomplete"),
    (dict(kernel_pos=0, index_pos=400_000), "index_only"),
    (dict(calls=0, kernel_pos=0, index_pos=0, read_at_s=0.0, share=0.0), "no_reads"),
])
def test_each_way_a_run_is_not_the_one_asked_about_is_refused(over, code):
    recs = [_record(**over) for _ in range(3)]
    problems = P.verify({"a": recs}, {"a": _bare()}, _cands("a"), headline="a", smoke=False)
    assert code in _codes(problems), (code, problems)


def test_repeats_that_did_not_read_the_same_things_are_refused():
    recs = [_record(), _record(kernel_pos=399_000, calls=39)]
    problems = P.verify({"a": recs}, {"a": _bare()}, _cands("a"), headline="a", smoke=False)
    assert "counts_differ" in _codes(problems), problems


def test_a_candidate_that_was_never_run_is_refused():
    problems = P.verify({"a": [_record()]}, {"a": _bare()}, _cands("a", "b"), headline="a",
                        smoke=False)
    assert "missing" in _codes(problems), problems
    problems = P.verify({"a": [_record()]}, None, _cands("a"), headline="a", smoke=False)
    assert "missing" in _codes(problems), problems


def test_a_share_the_repeats_disagree_on_is_refused_outside_smoke():
    recs = [_record(wall_s=20.0, read_at_s=1.0, share=0.05),
            _record(wall_s=20.0, read_at_s=3.0, share=0.15),
            _record(wall_s=20.0, read_at_s=2.0, share=0.10)]
    problems = P.verify({"a": recs}, {"a": _bare()}, _cands("a"), headline="a", smoke=False)
    assert "spread" in _codes(problems), problems
    assert P.verify({"a": recs}, None, _cands("a"), headline="a", smoke=True) == []


def test_a_run_the_wrapper_changed_is_refused_against_its_bare_twin():
    recs = [_record() for _ in range(3)]
    slow = P.verify({"a": recs}, {"a": _bare(wall=5.0)}, _cands("a"), headline="a", smoke=False)
    fast = P.verify({"a": recs}, {"a": _bare(wall=60.0)}, _cands("a"), headline="a", smoke=False)
    assert "bare" in _codes(slow) and "bare" in _codes(fast), (slow, fast)
    # ...and the bare twin is not held to the instrument's own completeness
    assert P.verify({"a": recs}, {"a": _bare(wall=25.0)}, _cands("a"), headline="a",
                    smoke=False) == []


def test_a_bare_twin_that_fails_is_refused():
    """The twin is the run as CI makes it. If it cannot pass with no wrapper
    at all, it is not the run being asked about either."""
    recs = [_record() for _ in range(3)]
    problems = P.verify({"a": recs}, {"a": dict(_bare(), rc=1)}, _cands("a"), headline="a",
                        smoke=False)
    assert "exit" in _codes(problems), problems


def test_a_saturated_host_is_refused_outside_smoke():
    recs = [_record() for _ in range(3)]
    assert "load" in _codes(P.verify({"a": recs}, {"a": _bare()}, _cands("a"), headline="a",
                                      smoke=False, load=(64.0, 2.0, 1.0), cpus=8))
    assert P.verify({"a": recs}, {"a": _bare()}, _cands("a"), headline="a", smoke=True,
                    load=(64.0, 2.0, 1.0), cpus=8) == []
    assert P.verify({"a": recs}, {"a": _bare()}, _cands("a"), headline="a", smoke=False,
                    load=(8.0, 2.0, 1.0), cpus=8) == []


def test_a_stale_candidate_table_is_refused():
    problems = P.verify({"a": [_record() for _ in range(3)]}, {"a": _bare()}, _cands("a"),
                        headline="a", smoke=False, stale=["tools/new_probe.py"])
    assert "stale_table" in _codes(problems), problems


def test_the_real_tree_names_every_direct_caller_of_the_fold_family():
    """The gate against the CURRENT tree, or it is not a gate: the candidate
    table covers every file that calls the family, and the one exclusion
    still names a file that calls it."""
    assert P.table_problems(P.ROOT) == []
    assert P.exclusion_problems(P.ROOT) == []


def test_an_exclusion_that_no_longer_calls_the_family_is_stale(tmp_path):
    (tmp_path / "tools").mkdir()
    gone, quiet = tmp_path / "tools" / "gone.py", tmp_path / "tools" / "quiet.py"
    quiet.write_text("x = 1\n")
    with pytest.MonkeyPatch.context() as m:
        m.setattr(P, "EXCLUDED", {"tools/gone.py": "why", "tools/quiet.py": "why"})
        assert P.exclusion_problems(tmp_path) == [
            "tools/gone.py is excluded and is not in the tree",
            "tools/quiet.py is excluded and no longer calls the family by name"]
        gone.write_text("import discrimination_features as D\nD.extra_features(None, 44100)\n")
        assert P.exclusion_problems(tmp_path) == [
            "tools/quiet.py is excluded and no longer calls the family by name"]
        m.setattr(P, "EXCLUDED", {"tools/gone.py": "why"})
        assert P.exclusion_problems(tmp_path) == []


def test_a_new_direct_caller_turns_the_table_red_and_a_mention_does_not(tmp_path):
    (tmp_path / "model").mkdir()
    (tmp_path / "model" / "discrimination_features.py").write_text(
        "def extra_features(x, sr, taps=None):\n    return x, sr, taps\n")
    (tmp_path / "model" / "quiet.py").write_text('"""Says extra_features(x) in prose."""\n')
    assert P.table_problems(tmp_path) == []
    (tmp_path / "model" / "probe.py").write_text(
        "import discrimination_features as D\n"
        "def go(x, sr):\n"
        "    D.jitter_features(x, sr)\n"
        "    D.dphase_row_state(x, sr)\n")
    assert P.table_problems(tmp_path) == ["model/probe.py"]


# ---- the edge cases the issue names ------------------------------------------
def test_the_index_only_path_is_counted_for_what_it_is():
    """`taps <= 0` is still `_read_at` -- it reads, by `rint` and a clip --
    but it evaluates no kernel. It must be booked as index positions, never
    as kernel positions, and must be REFUSED as the headline: a run that only
    ever indexed says nothing about the sinc."""
    rec = P.profile(_call("taps0", _work(0, repeats=20)))
    assert rec["calls"] > 0, rec
    assert rec["kernel_pos"] == 0 and rec["index_pos"] > 0, rec
    assert rec["by_caller"], rec["by_caller"]
    problems = P.verify({"taps0": [rec]}, None, _cands("taps0"), headline="taps0", smoke=True)
    assert _codes(problems) == {"index_only"}, problems


def test_the_legacy_arm_never_reaches_the_read():
    """`taps = -1` is `_fold_at`'s integer-lag return: `_fold`, not
    `_read_at`. A run of it must show no reads at all, and no reads is a
    REFUSAL as the headline -- the same reading a wrapper that was never
    called would produce, which is why both are refused and neither is
    ever reported as 0 %."""
    rec = P.profile(_call("legacy", _work(-1)))
    assert (rec["calls"], rec["kernel_pos"], rec["index_pos"], rec["read_at_s"]) == (
        0, 0, 0, 0.0), rec
    problems = P.verify({"legacy": [rec]}, None, _cands("legacy"), headline="legacy", smoke=True)
    assert _codes(problems) == {"no_reads"}, problems


def test_the_arm_comparison_books_both_arms_separately():
    """What `extra_features`'s `for taps in (-1, FOLD_SINC_TAPS)` arm
    comparison looks like: the legacy arm adds nothing to the read, and the
    kernel arm's positions are the whole of it."""
    legacy, kernel = P.profile(_call("legacy", _work(-1))), P.profile(_call("both", _work(16)))
    assert legacy["calls"] == 0
    assert kernel["kernel_pos"] > 0 and kernel["index_pos"] == 0
    assert set(kernel["by_caller"]) == {"_fold_at<-mpd_features@16", "_fold_at<-jitter_features@16"}


# ---- a run that rebinds the read under the wrapper ---------------------------
def test_a_run_that_rebinds_the_read_is_refused_rather_than_timed():
    """THE GUARD SHIPS WITH THE INPUT THAT DEFEATS IT (verification-rules rule
    8). pytest's assertion rewriting re-executes a collected module into the
    module object it finds; anything that does that to
    `discrimination_features` would put the un-timed `_read_at` back, and the
    run would report a smaller share that looks exactly like a cheaper
    kernel. This is that, done on purpose, mid-run."""
    D = P.module()
    original = D._read_at

    def run(reader):
        x = D._square(220.0, 44100)
        D.extra_features(x, 44100)
        D._read_at = original                 # the rebind, after one honest call
        for _ in range(3):
            D.extra_features(x, 44100)

    try:
        rec = P.profile(_call("rebound", run))
        assert rec["unwrapped"] == ["model.discrimination_features"], rec["unwrapped"]
        problems = P.verify({"rebound": [rec]}, None, _cands("rebound"), headline="rebound",
                            smoke=True)
        assert "unwrapped" in _codes(problems), problems
    finally:
        D._read_at = original


def test_a_run_that_crashes_is_refused_with_its_traceback():
    """A candidate that raises is a run that exited non-zero, not a lost
    record: refused, with the reason where a reader can see it."""

    def boom(reader):
        raise RuntimeError("the probe itself broke")

    rec = P.profile(_call("boom", boom))
    assert rec["rc"] == 1, rec
    assert any("RuntimeError: the probe itself broke" in ln for ln in rec["tail"]), rec["tail"]
    problems = P.verify({"boom": [rec]}, None, _cands("boom"), headline="boom", smoke=True)
    assert "exit" in _codes(problems) and "the probe itself broke" in problems[0][1], problems


def test_the_wrapper_is_what_the_shipped_fold_calls_while_it_is_installed():
    """Not by elimination: with the reader installed, the module's `_read_at`
    IS the wrapper and `_fold_at`'s globals ARE the module it is installed
    in, under both names the tree imports it by."""
    seen = []

    def run(reader):
        for name in ("discrimination_features", "model.discrimination_features"):
            seen.append((name, reader.holds(sys.modules[name]), reader.reaches(sys.modules[name])))
    P.profile(_call("names", run))
    names = ("discrimination_features", "model.discrimination_features")
    assert seen == [(n, True, True) for n in names]


# ---- the decision rule, fixed before the measurement -------------------------
@pytest.mark.parametrize("wall, read, material", [
    (100.0, 15.0, True),       # 15 % and 15 s: both bounds met
    (100.0, 5.0, False),       # 5 %: under the share bound however long
    (400.0, 30.0, False),      # 7.5 %: thirty seconds, but not a tenth of the run
    (50.0, 7.0, False),        # 14 %: a tenth of the run, but under ten seconds
])
def test_material_needs_both_a_tenth_of_the_run_and_ten_seconds(wall, read, material):
    agg = P.aggregate([_record(wall_s=wall, read_at_s=read, share=read / wall) for _ in range(3)],
                      _bare(wall=wall))
    assert agg["material"] is material, agg
    assert agg["share"] == pytest.approx(read / wall)
    assert agg["read_at_s"] == pytest.approx(read)


def test_the_headline_is_the_candidate_that_read_the_most():
    small = P.aggregate([_record("small", kernel_pos=1_000)], None)
    large = P.aggregate([_record("large", kernel_pos=9_000)], None)
    assert P.headline_of({"small": small, "large": large}) == "large"
    with pytest.raises(P.Refused):
        P.headline_of({})


# ---- the CLI, end to end, on the toy table -----------------------------------
def test_smoke_runs_the_toy_table_through_the_same_children_and_is_not_a_measurement(
        tmp_path, monkeypatch):
    """Smoke keeps the structural refusals and drops the timing-quality ones,
    so it runs on any host -- here with a load average that would refuse a
    full run -- and says so on every page."""
    monkeypatch.setattr(os, "getloadavg", lambda: (1e6, 1e6, 1e6))
    out = tmp_path / "smoke"
    assert P.main(["--smoke", "--out", str(out)]) == 0
    report = json.loads((out / "report.json").read_text())
    assert report["verdict"] == "SMOKE" and report["smoke"] is True
    assert report["headline"] == "smoke-kernel", report["headline"]
    assert report["candidates"]["smoke-kernel"]["kernel_pos"] > 0
    assert report["candidates"]["smoke-legacy"]["calls"] == 0
    text = (out / "summary.txt").read_text()
    assert "SMOKE" in text and "NOT A MEASUREMENT" in text


def test_a_saturated_host_refuses_the_full_run_before_any_child_is_launched(tmp_path, monkeypatch):
    out = tmp_path / "loaded"
    monkeypatch.setattr(os, "getloadavg", lambda: (1e6, 1.0, 1.0))
    assert P.main(["--out", str(out)]) == P.REFUSED
    report = json.loads((out / "report.json").read_text())
    assert report["verdict"] == "REFUSED"
    assert "load" in {code for code, _ in report["problems"]}
    assert not (out / "children").exists() or list((out / "children").iterdir()) == []


def test_the_cli_refuses_a_candidate_that_reads_nothing(tmp_path):
    out = tmp_path / "only"
    assert P.main(["--smoke", "--out", str(out), "--only", "smoke-legacy"]) == P.REFUSED
    report = json.loads((out / "report.json").read_text())
    assert report["verdict"] == "REFUSED"
    assert "no_reads" in {code for code, _ in report["problems"]}

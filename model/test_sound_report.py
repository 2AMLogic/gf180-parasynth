#!/usr/bin/env python3
"""The injection machinery of `model/sound_report.py`, checked cheaply.

    .venv/bin/python -m pytest model/test_sound_report.py -q

**Why this file exists separately from `make controls`.** A control counts as
caught only if all three of these hold (docs/verification-rules.md 5):

  1. the clean run passes;
  2. the mutant **builds, activates and actually executes**;
  3. the intended assertion is the one that fails.

`make controls` runs the two historical-bug injections end to end and so
covers 1 and 3 -- but each of those is about ninety seconds of rendering, and
neither of them can distinguish "the patch was applied and the defect is
invisible" from "the patch silently did nothing." Condition 2 is the one this
repository has actually got wrong: it once shipped a negative control that
mutated a function signature into invalid Python and passed, proving only that
Python rejects syntax errors.

So this file asserts condition 2 directly and in under a second: that each
injection's `make` really does change the function the report will call, that
the changed function returns the historically broken answer rather than merely
being a different object, and that `run()`'s restore actually restores -- a
patch that leaked would make every LATER property in the same process wrong
while the report still printed clean numbers.
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import audio_measure as am                                          # noqa: E402
import drum_verify as dv                                            # noqa: E402
import sound_report as sr                                           # noqa: E402

SR = sr.SR


# ===========================================================================
# the registry: the historical-bug injections exist and declare real voices
# ===========================================================================
HISTORICAL = ("bd-ma-envelope", "sd-centroid-amp-weighted")


def test_the_historical_bug_injections_are_registered():
    """docs/verification-rules.md 5 -- a bug is not closed until it is an
    injection. These two are the measurement-layer counterparts of
    verify_ctl.py's SPI_ADDR7 / SPI_DATA24, which replay the exact broken SPI
    frame that shipped."""
    for name in HISTORICAL:
        assert name in sr.INJECTIONS, f"{name} is not registered; make controls would not run it"
        desc, voices, make = sr.INJECTIONS[name]
        assert desc.strip(), f"{name} has no description"
        assert voices, f"{name} declares no voice, so cmd_inject has nothing to check"
        assert callable(make)


def test_every_injection_declares_voices_the_report_actually_measures():
    """A defect declared against a voice no property covers reports `NO
    PROPERTY MOVED` forever -- an unsatisfiable control, which is worse than
    no control. Checked over the whole registry, not just the new pair."""
    measured = {p.voice for p in sr.build_properties()}
    for name, (_, voices, _) in sr.INJECTIONS.items():
        missing = [v for v in voices if v not in measured]
        assert not missing, f"{name} touches {missing}, which no property in build_properties() measures"


# ===========================================================================
# condition 2: the mutant activates, executes, and is undone
# ===========================================================================
def test_the_moving_average_injection_really_replaces_drum_verify_envelope():
    """`bd-ma-envelope` reinstates the 5 ms moving average that shipped as an
    envelope estimator. audio_measure.py rule 1: a 5 ms window spans 0.28 of a
    cycle at 56 Hz, so it ripples at the carrier rather than tracking the
    decay.

    This asserts the patched `dv.envelope` returns the *historically broken*
    answer -- numerically equal to `moving_average_envelope` and numerically
    different from the tuned per-voice RMS window -- not merely that some
    attribute was rebound. A patch installed as a pass-through would satisfy
    'the attribute changed' and change nothing the report can see."""
    _, _, make = sr.INJECTIONS["bd-ma-envelope"]
    ctx = {"_patches": []}
    original = dv.envelope

    t = np.arange(int(0.4 * SR)) / SR
    x = np.exp(-t / 0.10) * np.sin(2 * np.pi * 56.0 * t)
    clean = original(x, SR)

    make(ctx)
    try:
        assert dv.envelope is not original, "the injection did not rebind drum_verify.envelope at all"
        got = dv.envelope(x, SR)
        want = np.abs(am.moving_average_envelope(x, 5.0, SR))
        assert np.allclose(got, want), \
            "the patched envelope is not the 5 ms moving average this injection claims to reinstate"
        # And it is a real change, not a re-spelling of the correct estimator.
        rel = np.max(np.abs(got - clean)) / max(np.max(clean), 1e-12)
        assert rel > 0.05, \
            f"the patched envelope is within {rel:.3%} of the correct one; this mutant does not mutate"
    finally:
        for mod, attr, orig in ctx["_patches"]:
            setattr(mod, attr, orig)

    assert dv.envelope is original, "the injection leaked: drum_verify.envelope was not restored"


def test_the_moving_average_injection_ripples_at_the_carrier_it_cannot_resolve():
    """The mechanism, not just the identity: 5 ms holds 0.28 of a cycle at
    56 Hz, so the estimate swings with the waveform instead of tracking the
    decay. BD's tuned window is 12 ms (`drum_verify.ENV_WIN_MS`), 0.67 of a
    cycle, and the RMS of a squared sine over that is far flatter.

    **A wrong-then-right record, kept because it is the point of the file.**
    The first version of this test compared against `dv.envelope(x, SR)` --
    the signature DEFAULT, 4 ms -- and failed, correctly: 4 ms is *shorter*
    than the injected 5 ms and ripples 7.50 dB against the moving average's
    6.55 dB, so the injection measured as an improvement. The default window
    is not the window the acceptance path uses; `dv.measure` selects a
    per-voice one, and comparing against anything else measures a function
    nobody calls. Same shape as every failure in docs/failure-modes.md: a
    correct instrument in a wrong state.

    Measured on this tree at 56 Hz: tuned 12 ms 1.85 dB, injected 5 ms
    moving average 6.55 dB."""
    _, voices, make = sr.INJECTIONS["bd-ma-envelope"]
    assert voices == ["BD"]
    win = dv.ENV_WIN_MS["BD"]
    ctx = {"_patches": []}
    original = dv.envelope

    t = np.arange(int(0.5 * SR)) / SR
    x = np.sin(2 * np.pi * 56.0 * t)                      # no decay at all

    def ripple_db(e):
        seg = e[int(0.1 * SR):int(0.4 * SR)]
        return 20.0 * np.log10(np.max(seg) / max(np.min(seg), 1e-12))

    clean_db = ripple_db(original(x, SR, win_ms=win))
    make(ctx)
    try:
        broken_db = ripple_db(dv.envelope(x, SR, win_ms=win))
    finally:
        for mod, attr, orig in ctx["_patches"]:
            setattr(mod, attr, orig)

    assert clean_db < 3.0, (
        f"the tuned {win:g} ms window itself rippled {clean_db:.2f} dB on a steady 56 Hz carrier; "
        "this test's apparatus is the comparison, so a noisy baseline invalidates it")
    assert broken_db > clean_db + 3.0, (
        f"the 5 ms moving average rippled {broken_db:.2f} dB against the tuned window's "
        f"{clean_db:.2f} dB on a steady 56 Hz carrier; it should be markedly worse, not comparable")


def test_the_moving_average_injection_shortens_the_decay_it_is_asked_to_measure():
    """And the consequence the report actually prints: a rippling envelope
    crosses -20 dB early, so T20 reads short. On a closed-form 100 ms
    exponential at 56 Hz, measured on this tree, the tuned 12 ms window
    recovers tau 100.4 ms / T20 209.6 ms at R^2 0.993; the injected 5 ms
    moving average gives T20 156.0 ms at R^2 0.882.

    The truth is known here (tau is 100 ms by construction), which is what
    separates this from the end-to-end `make controls` run: that one shows
    BD T20 moving 308 -> 207 ms on a real render, but the render has no
    independently known answer, so it can only show the number CHANGED. This
    shows it changed in the wrong DIRECTION, against a signal whose decay was
    chosen before it was measured."""
    _, _, make = sr.INJECTIONS["bd-ma-envelope"]
    win = dv.ENV_WIN_MS["BD"]
    ctx = {"_patches": []}
    original = dv.envelope

    tau_s = 0.10
    t = np.arange(int(0.5 * SR)) / SR
    x = np.exp(-t / tau_s) * np.sin(2 * np.pi * 56.0 * t)

    clean = dv.decay_fit(original(x, SR, win_ms=win), SR)
    make(ctx)
    try:
        broken = dv.decay_fit(dv.envelope(x, SR, win_ms=win), SR)
    finally:
        for mod, attr, orig in ctx["_patches"]:
            setattr(mod, attr, orig)

    assert abs(clean["tau_ms"] - tau_s * 1e3) < 5.0, (
        f"the tuned window recovered tau {clean['tau_ms']:.2f} ms from a signal built with "
        f"{tau_s * 1e3:.0f} ms; the baseline of this comparison is not trustworthy")
    assert broken["t20_ms"] < 0.8 * clean["t20_ms"], (
        f"the injected moving average read T20 {broken['t20_ms']:.1f} ms against the tuned window's "
        f"{clean['t20_ms']:.1f} ms; the historical defect makes decays read SHORT and this does not")
    assert broken["r2"] < clean["r2"] - 0.05, (
        f"the injected fit's R^2 is {broken['r2']:.4f} against {clean['r2']:.4f}; a rippling envelope "
        "should fit an exponential visibly worse, and if it does not, the ripple is not reaching the fit")


def test_the_centroid_injection_switches_which_field_the_brightness_property_reads():
    """`sd-centroid-amp-weighted` does not patch a function -- it sets
    `ctx["centroid_weight"]`, and `m_sd_brightness` reads the other field of
    the measurement `drum_verify.measure` already computes. So the thing to
    assert is that the property's OUTPUT changes, on a real snare render,
    rather than that a dict key was set.

    One render is shared: `_meas` caches on the ctx, so flipping the key and
    re-reading isolates the weighting from every other source of variation."""
    _, voices, make = sr.INJECTIONS["sd-centroid-amp-weighted"]
    assert voices == ["SD"]
    ctx = {"_patches": []}
    power = sr.m_sd_brightness(ctx)
    assert power is not None, "the snare rendered silent; this test has no apparatus"

    make(ctx)
    amplitude = sr.m_sd_brightness(ctx)
    assert amplitude is not None
    assert not ctx["_patches"], "this injection should not need to patch a module attribute"

    assert amplitude > 2.0 * power, (
        f"the amplitude-weighted centroid read {amplitude:.0f} Hz against the power-weighted "
        f"{power:.0f} Hz; docs/drum-verification.md 3 withdrew the amplitude weighting because it "
        "reads a wide quiet noise floor as brightness, so on the snare it must read far higher")


def test_the_brightness_property_is_locked_and_the_lock_is_the_power_weighted_one():
    """A property with no lock reports `no lock recorded yet` and passes
    whatever it measures -- which would make the centroid injection
    unsatisfiable however far it moved the number."""
    key = ("SD", "brightness (power centroid)")
    assert key in sr.LOCKS, "the brightness property has no lock, so nothing can be out of tolerance"
    props = {(p.voice, p.name): p for p in sr.build_properties()}
    assert key in props, "the lock names a property build_properties() does not create"
    p = props[key]
    assert p.kind == "lock"
    assert sr.LOCKS[key] < 3000.0, (
        f"the locked brightness is {sr.LOCKS[key]:.0f} Hz -- high enough to be the amplitude-weighted "
        "value, which would mean the lock was taken with the defect in place")


# ===========================================================================
# the lock changelog (issue #68)
# ===========================================================================
# `LOCKS` used to be an overwrite: a commit that moved a locked value left the
# new number and nothing else, so "did this go green because the model improved
# or because the bound moved?" was answerable only by reading git log and
# guessing which of a commit's changes was the point. These are the gate on the
# record that closes it.
def test_the_lock_changelog_agrees_with_the_live_table():
    """Run the gate against the current state. An unsatisfiable gate is worse
    than no gate -- it trains everyone to ignore gates, including the working
    ones."""
    problems = sr.check_lock_changelog()
    assert problems == [], problems


def test_every_lock_has_a_history_entry_with_a_reason():
    for key in sr.LOCKS:
        hist = sr.lock_history(*key)
        assert hist, f"{key} has no recorded change"
        assert hist[-1]["reason"].strip(), f"{key}'s last change records no reason"
        assert hist[-1]["recorded_by"], f"{key}'s last change names no commit"


def test_a_lock_moved_without_an_entry_is_flagged(monkeypatch):
    """The injected control. Without this the changelog is a comment: it would
    agree with the table on the day it was written and quietly stop agreeing on
    the first re-lock."""
    key = ("BD", "T20")
    monkeypatch.setitem(sr.LOCKS, key, 207.0)
    problems = sr.check_lock_changelog()
    assert any("BD" in p and "T20" in p and "no reason recorded" in p
               for p in problems), problems


def test_a_lock_added_without_an_entry_is_flagged(monkeypatch):
    monkeypatch.setitem(sr.LOCKS, ("BD", "a new property"), 1.0)
    assert any("a new property" in p for p in sr.check_lock_changelog())


def test_a_changelog_entry_for_a_property_that_is_not_a_lock_is_flagged(monkeypatch):
    """A stale entry is the other direction of the same failure: a record that
    describes a bound nobody applies reads as coverage."""
    monkeypatch.setattr(sr, "LOCK_CHANGELOG", sr.LOCK_CHANGELOG + [
        {"at": "x", "recorded_by": "y", "issue": 0, "reason": "r",
         "locks": {("BD", "gone"): (None, 1.0)}}])
    assert any("gone" in p for p in sr.check_lock_changelog())


@pytest.fixture(params=[False, True], ids=["committed", "additional-CP-move"])
def lock_changelog(monkeypatch, request):
    if request.param:
        key = ("CP", "attack")
        old = sr.LOCKS[key]
        event = _ev(key, old, old + 1, recorded_by="74c90449",
                    reason="Fixture moving the clap attack one millisecond later.")
        monkeypatch.setattr(sr, "LOCK_CHANGELOG", [*sr.LOCK_CHANGELOG, event])
        monkeypatch.setitem(sr.LOCKS, key, old + 1)
    return sr.LOCK_CHANGELOG


def test_the_changelog_records_the_commits_that_actually_moved_the_locks(lock_changelog):
    """Recovered with `git log -L` on the LOCKS block, not from memory: 28dfd55
    introduced the table (#51), bf13fdd added the power-centroid lock (#251),
    and 50d7aaf / 80b3756 are the two re-locks #242 traced with drift_probe.py."""
    by = {ev["recorded_by"]: ev for ev in lock_changelog}
    assert {"28dfd55", "bf13fdd", "50d7aaf", "80b3756", "b572b7fc"} <= by.keys()
    assert list(by["bf13fdd"]["locks"]) == [("SD", "brightness (power centroid)")]
    assert list(by["50d7aaf"]["locks"]) == [("LADDER", "corner ratio drift")]
    assert list(by["b572b7fc"]["locks"]) == [("LADDER", "corner ratio drift")]
    assert list(by["80b3756"]["locks"]) == [("LT", "attack")]
    assert all(was is None for was, _ in by["28dfd55"]["locks"].values()), (
        "nothing was pinned before 28dfd55, so every entry there is a first lock")


def test_check_locks_exits_nonzero_when_the_record_disagrees(monkeypatch):
    assert sr.main(["--check-locks"]) == 0
    monkeypatch.setitem(sr.LOCKS, ("SD", "T20"), 1.0)
    assert sr.main(["--check-locks"]) == 1


# ===========================================================================
# the lock table's own provenance, and the gate that reads it (issue #242)
#
# Two locks drifted past tolerance and nobody noticed for nights, because the
# nightly step that was supposed to catch them grepped the plain report for
# "MOVED"/"REGRESS" -- words only the `--inject` report prints. Diagnosing it
# then cost re-measuring 17 historical trees over a 44-commit range, because
# `LOCK` named `ce400a6`, an object that does not exist in this repository. Both are mechanism failures, so both get
# a mechanism here rather than a note.
# ===========================================================================
def test_every_changelog_entry_names_a_real_property_and_real_commits(lock_changelog):
    """A lock move records WHICH commit changed the sound. If the key matches no
    property, or an `at` / `recorded_by` sha does not resolve, it is decoration.
    One table now (#1303): `LOCK_CHANGELOG` feeds the report and --check-locks."""
    props = {(p.voice, p.name) for p in sr.build_properties()}
    for ev in lock_changelog:
        for key in ev["locks"]:
            assert key in props, f"LOCK_CHANGELOG names {key}, which build_properties() does not create"
            assert key in sr.LOCKS, f"LOCK_CHANGELOG names {key}, which has no entry in LOCKS"
        assert len(ev["reason"]) > 40, f"{ev['recorded_by']} gives no reason"
    assert sr.check_lock_changelog() == []


def test_a_relocked_propertys_provenance_reaches_the_line_the_report_prints(lock_changelog):
    """The provenance has to be where the number is read. `Result.sentence()`
    is the only text a reader sees when a lock goes OUT, so the latest move,
    derived from LOCK_CHANGELOG, is appended to `source`."""
    props = {(p.voice, p.name): p for p in sr.build_properties()}
    # Take the expected move from the raw events so a dropped property in
    # lock_history() cannot disappear from both the report and its test.
    latest = {}
    for event in lock_changelog:
        for key, (was, _) in event["locks"].items():
            if was is not None:
                latest[key] = (event, was)
    assert {("LADDER", "corner ratio drift"), ("LT", "attack")} <= latest.keys()
    for key, (last, was) in latest.items():
        p = props[key]
        p.value = sr.LOCKS[key]
        s = sr.Result(p, p.value + 10 * p.tol, False, "", 10 * p.tol).sentence()
        assert f"RE-LOCKED at {last['recorded_by']} (was {was:g})" in s, key
        assert last["reason"] in s, key


def _with_changelog(extra, locks=None):
    """Run check_lock_changelog against a modified copy, then restore."""
    import copy
    orig_cl, orig_locks = copy.deepcopy(sr.LOCK_CHANGELOG), dict(sr.LOCKS)
    try:
        sr.LOCK_CHANGELOG = orig_cl + extra
        if locks:
            sr.LOCKS.update(locks)
        return sr.check_lock_changelog()
    finally:
        sr.LOCK_CHANGELOG[:] = orig_cl
        sr.LOCKS.clear()
        sr.LOCKS.update(orig_locks)


def _ev(key, was, now, **kw):
    e = {"at": "28dfd55", "recorded_by": "50d7aaf", "issue": 1,
         "reason": "a real and sufficiently long reason", "locks": {key: (was, now)}}
    e.update(kw)
    return e


@pytest.mark.parametrize("field", ["at", "recorded_by", "LOCK"])
def test_check_locks_rejects_a_moving_commit_reference(monkeypatch, capsys, field):
    key = ("CP", "attack")
    old = sr.LOCKS[key]
    move = _ev(key, old, old + 1, reason="Fixture moving the clap attack one millisecond later.")
    if field == "LOCK":
        monkeypatch.setattr(sr, "LOCK", "HEAD")
    else:
        move[field] = "HEAD"
    monkeypatch.setattr(sr, "LOCK_CHANGELOG", [*sr.LOCK_CHANGELOG, move])
    monkeypatch.setitem(sr.LOCKS, key, old + 1)

    assert sr.main(["--check-locks"]) == 1
    assert any(field in line and "HEAD" in line and "commit" in line
               for line in capsys.readouterr().out.splitlines())


@pytest.fixture
def measurement_repo(tmp_path):
    def git(*args):
        return subprocess.check_output(
            ["git", "-C", str(tmp_path), "-c", "user.name=Test", "-c", "user.email=test@example.org",
             *args], text=True).strip()

    git("init", "-q")
    git("commit", "-q", "--allow-empty", "-m", "measurement")
    return git


def test_lock_commit_must_resolve_to_its_own_object(tmp_path, monkeypatch, measurement_repo):
    git = measurement_repo
    full = git("rev-parse", "HEAD")
    short = git("rev-parse", "--short", "HEAD")
    tree = git("rev-parse", "HEAD^{tree}")
    monkeypatch.setattr(sr, "HERE", str(tmp_path))
    assert sr._lock_commit_problems([("at", full), ("recorded_by", short)]) == []
    assert sr._lock_commit_problems([("at", tree)]) == [
        f"at={tree!r} does not identify a unique commit in this repository"]

    # A hex branch can shadow a genuine object prefix. Git accepts the text
    # as a commit even though the branch now points at a different commit.
    git("commit", "-q", "--allow-empty", "-m", "later measurement")
    git("branch", short, "HEAD")
    assert git("rev-parse", "--verify", short) != full
    assert sr._lock_commit_problems([("at", short)]) == [
        f"at={short!r} does not identify a unique commit in this repository"]


def test_check_locks_refuses_a_shallow_checkout(tmp_path, monkeypatch, capsys, measurement_repo):
    measurement_repo("commit", "-q", "--allow-empty", "-m", "later measurement")
    shallow = tmp_path / "shallow"
    measurement_repo("clone", "-q", "--depth=1", tmp_path.as_uri(), str(shallow))
    monkeypatch.setattr(sr, "HERE", str(shallow))
    assert sr.main(["--check-locks"]) == 2
    assert "REFUSED: checking lock commits requires a full Git checkout" in capsys.readouterr().out


@pytest.mark.parametrize("field", ["LOCKS", "from", "to"])
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf")])
def test_check_locks_names_nonfinite_values(monkeypatch, capsys, field, bad):
    key = ("LT", "attack")
    if field == "LOCKS":
        monkeypatch.setitem(sr.LOCKS, key, bad)
    else:
        moves = next(ev["locks"] for ev in sr.LOCK_CHANGELOG if ev["recorded_by"] == "80b3756")
        was, now = moves[key]
        monkeypatch.setitem(moves, key, (bad, now) if field == "from" else (was, bad))
    assert sr.main(["--check-locks"]) == 1
    assert any("LT 'attack'" in line and field in line and "finite" in line
               for line in capsys.readouterr().out.splitlines())


@pytest.fixture
def relock_results(monkeypatch):
    # Freeze the starting locks for the reviewer's round-trip example. Future
    # changes to the real instrument can move either attack independently.
    lt, cp = ("LT", "attack"), ("CP", "attack")
    starting = {lt: 6.27083, cp: 3.0}
    event = _ev(lt, None, starting[lt])
    event["locks"][cp] = (None, starting[cp])
    monkeypatch.setattr(sr, "LOCK_CHANGELOG", [event])
    monkeypatch.setattr(sr, "LOCKS", starting)
    props = {(p.voice, p.name): p for p in sr.build_properties()}
    results = [sr.Result(props[lt], 301 * 1000 / 48000, True),
               sr.Result(props[cp], 4.0, True)]
    monkeypatch.setattr(sr, "run", lambda: (results, []))
    return results


@pytest.fixture
def pasted_relock(relock_results, capsys, monkeypatch):
    assert sr.main(["--relock"]) == 0
    table, draft = capsys.readouterr().out.split("\n\n", 1)
    locks = ast.literal_eval(ast.parse(table).body[0].value)
    event, = ast.literal_eval("[" + draft + "]")
    event.update(at="28dfd55", recorded_by="74c90449")
    monkeypatch.setattr(sr, "LOCKS", locks)
    monkeypatch.setattr(sr, "LOCK_CHANGELOG", [*sr.LOCK_CHANGELOG, event])
    return event


def test_relock_draft_round_trips_through_check_locks(pasted_relock):
    assert sr.LOCKS == {("LT", "attack"): 6.27083, ("CP", "attack"): 4}
    assert pasted_relock["locks"] == {("CP", "attack"): (3.0, 4)}
    pasted_relock["reason"] = "Fixture moving the clap attack one millisecond later."
    assert sr.main(["--check-locks"]) == 0


def test_the_pasted_relock_requires_its_reason_to_be_edited(pasted_relock, capsys):
    assert sr.main(["--check-locks"]) == 1
    output = capsys.readouterr().out
    assert "`reason`" in output and "placeholder" in output and "1 problem(s)" in output


@pytest.mark.parametrize("field", ["measured", "LOCKS"])
def test_relock_refuses_a_nonfinite_draft_before_printing_it(relock_results, field, capsys,
                                                             monkeypatch):
    if field == "measured":
        relock_results[1].measured = float("nan")
    else:
        monkeypatch.setitem(sr.LOCKS, ("CP", "attack"), float("inf"))
    assert sr.main(["--relock"]) == 1
    output = capsys.readouterr().out
    assert "CP" in output and "attack" in output and "finite" in output
    assert "LOCKS = {" not in output


def test_the_clean_changelog_has_no_problems():
    assert sr.check_lock_changelog() == []


def test_a_broken_from_to_chain_is_flagged():
    # LT attack is held at 6.27083; this entry claims it moved from 999 -> 8.
    probs = _with_changelog([_ev(("LT", "attack"), 999.0, 8.0)],
                            {("LT", "attack"): 8.0})
    assert any("does not continue" in p for p in probs), probs


def test_a_no_op_move_is_flagged():
    v = sr.LOCKS[("BD", "T20")]
    probs = _with_changelog([_ev(("BD", "T20"), v, v)])
    assert any("from == to" in p for p in probs), probs


def test_an_unedited_relock_placeholder_is_flagged():
    v = sr.LOCKS[("BD", "T20")]
    probs = _with_changelog([_ev(("BD", "T20"), v, 250.0,
                                 at="<the commit these were measured at>",
                                 recorded_by="<the commit that re-locks>",
                                 reason="")],
                            {("BD", "T20"): 250.0})
    assert sum("placeholder" in p for p in probs) == 3, probs


def test_a_new_changelog_move_reaches_the_printed_line_without_a_second_table():
    import copy
    orig = copy.deepcopy(sr.LOCK_CHANGELOG)
    try:
        v = sr.LOCKS[("BD", "T20")]
        sr.LOCK_CHANGELOG.append(_ev(("BD", "T20"), v, 250.0, recorded_by="abc1234"))
        props = {(p.voice, p.name): p for p in sr.build_properties()}
        assert "RE-LOCKED at abc1234" in props[("BD", "T20")].source
    finally:
        sr.LOCK_CHANGELOG[:] = orig


def test_the_plain_report_never_prints_the_words_the_nightly_gate_used_to_grep_for():
    """The root cause, asserted directly. `print_report` marks a failure `OUT`;
    `MOVED` and `REGRESS` belong to `cmd_inject` and to nothing else. Any gate
    keyed to those words over the plain report is an unconditional pass."""
    import io
    import contextlib
    p = sr.Prop("X", "a property", "ms", "lock", 1.0, 0.1, "made up", lambda ctx: None)
    bad = sr.Result(p, 99.0, False, "", 98.0)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        n = sr.print_report([bad], "test")
    text = buf.getvalue()
    assert n == 1, "print_report must count the failure, so main() can exit 1"
    assert "OUT" in text, "the plain report marks a failing row OUT"
    assert "MOVED" not in text.upper().replace("REMOVED", ""), (
        "the plain report printed MOVED -- if that is intentional the nightly "
        "gate's history in #242 needs re-reading first")
    assert "REGRESS" not in text.upper()


def test_the_nightly_gate_decides_on_the_reports_exit_status_not_on_its_text():
    """#242's mechanism fix. The step may still grep the report to SHOW which
    rows are out; what it may not do is decide by grep, which is how it stayed
    green through two out-of-tolerance locks."""
    import pathlib
    wf = pathlib.Path(HERE).parent / ".github" / "workflows" / "nightly.yml"
    if not wf.exists():
        import pytest
        pytest.skip("no nightly.yml in this checkout")
    text = wf.read_text()
    marker = "no locked property may have moved"
    assert marker in text, "the nightly gate step has been renamed or removed"
    # the step's body: from its `- name:` line to the next step at that indent
    start = text.index("- name: " + marker)
    rest = text[start + 1:]
    end = rest.find("\n      - ")
    step = rest[:end if end != -1 else len(rest)]
    assert "steps.report.outputs.rc" in step, (
        "the gate does not read sound_report.py's exit status; grepping the "
        "report text is what failed silently in #242")
    for decide_by_grep in ("grep -q", "grep -iq", "if grep", "if ! grep"):
        assert decide_by_grep not in step, (
            f"the gate decides with `{decide_by_grep}` over the report text")


# ===========================================================================
# #600: a bug in a measurement must not print as "the estimator refused"
# ===========================================================================
REFUSED = "the estimator refused"


def _noise_share_only(monkeypatch, signal):
    """Run the real harness (`sr.run`) over ONLY the SD noise-share property,
    with the drum render replaced by `signal` so this costs milliseconds.
    Returns that property's Result."""
    props = [p for p in sr.build_properties()
             if p.voice == "SD" and p.name == "noise share"]
    assert len(props) == 1, "the SD noise-share property was renamed or removed"
    monkeypatch.setattr(sr, "build_properties", lambda: props)
    monkeypatch.setattr(sr, "_solo", lambda ctx, voice: signal)
    out, _ = sr.run()
    assert len(out) == 1
    return out[0]


def _damped_hit(seconds=0.4, seed=0):
    t = np.arange(int(seconds * SR)) / SR
    rng = np.random.default_rng(seed)
    body = np.exp(-t / 0.05) * (np.sin(2 * np.pi * 175 * t) + 0.5 * np.sin(2 * np.pi * 340 * t))
    return 0.5 * (body + 0.3 * np.exp(-t / 0.03) * rng.standard_normal(len(t)))


def test_a_crashing_noise_share_reports_the_exception_not_a_refusal(monkeypatch):
    """The control #600 asked for. `m_noise_share` used to wrap the call in
    `except Exception: return None`, which pre-empted the harness's own
    `type(e).__name__: e` branch -- so an ImportError, a changed signature or a
    bad `bands` argument all printed `NOT MEASURABLE -- the estimator refused`,
    indistinguishable from a real refusal on a degenerate hit."""
    import drum_fit

    def broken(*a, **k):
        raise TypeError("injected: noise_share() got an unexpected argument")

    monkeypatch.setattr(drum_fit, "noise_share", broken)
    r = _noise_share_only(monkeypatch, _damped_hit())
    assert r.measured is None and not r.ok
    assert "TypeError" in r.why, f"the exception type was swallowed: why={r.why!r}"
    assert r.why != REFUSED, "a crash in the measurement printed as a refusal"
    assert "NOT MEASURABLE -- TypeError" in r.sentence()


def test_noise_share_measures_a_real_hit(monkeypatch):
    """The positive leg: the property, through the same harness and the same
    stub render, yields a finite percentage -- so the test above is reaching
    the code that ships, not a path that always fails."""
    r = _noise_share_only(monkeypatch, _damped_hit())
    assert r.measured is not None, f"no value on a clean hit: {r.why!r}"
    assert np.isfinite(r.measured) and 0.0 <= r.measured <= 100.0
    assert r.why != REFUSED


def test_a_silent_hit_is_a_refusal_not_a_nan_value(monkeypatch):
    """The legitimate refusal. `drum_fit.noise_share` signals a window with no
    energy by returning share=NaN; that is the estimator declining, and must
    print as such rather than reaching the lock comparison as a NaN that fails
    every tolerance with no reason given."""
    r = _noise_share_only(monkeypatch, np.zeros(int(0.4 * SR)))
    assert r.measured is None, f"a silent hit was reported as a value: {r.measured!r}"
    assert r.why == REFUSED

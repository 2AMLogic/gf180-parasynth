"""Tests for `tools/manifest.py` -- the render/analyse/accept manifest scheme
(issue #68).

Two things this suite exists to prove, beyond "the code runs":

1. **Incomplete records are refused, not silently written** -- a measurement
   missing a required field, an acceptance bound with no rationale, and a
   retained WAV whose content no longer matches its recorded hash all raise
   `manifest.Refused` rather than producing a plausible-looking result.
2. **The title question is actually answerable.**
   `test_retroactive_reanalysis_separates_the_sound_fix_from_the_measurement_fix`
   is a synthetic stand-in for this project's own snare 8.8 -> 7.6 -> 3.4
   incident (issue #68's own acceptance criterion 4 names this as an
   acceptable substitute for the real case): it renders once, re-analyses
   the SAME retained WAV with a corrected estimator (an injected-bug control
   reinstating the historical "fixed short window, extrapolated linearly"
   T20 law -- docs/verification-rules.md rule 5), then renders again with the
   underlying model actually fixed, and asserts `diagnose()` correctly labels
   the first step "measurement changed" and the second "sound changed".
"""
from __future__ import annotations

import json
import math
import os
import pathlib
import subprocess
import sys

import numpy as np
import pytest
import yaml

import check_workflows as cw
import manifest as mf
from makefile_recipe import makefile_recipe

ROOT = pathlib.Path(__file__).resolve().parents[1]


# =============================================================================
# The gate on this suite's own wiring
# =============================================================================
def test_this_suite_is_enumerated_in_a_target_a_ci_job_actually_runs():
    """"Check that the thing you are testing is the thing that ships"
    (CLAUDE.md), applied to this suite itself.

    As first written, `tools/test_manifest.py` was reachable only through the
    broad `pytest ... tools/ ...` in `make verify` / `make verify-full`, and no
    workflow invokes either target -- so these tests and the injected-bug T20
    control below shipped without ever executing in CI. `rungs.yml`'s own
    comment states the rule: a gate nothing invokes fails exactly the same way
    as one that passes. This test fails if that wiring is ever removed.
    """
    recipe = makefile_recipe("verify-fast")
    assert "tools/test_manifest.py" in recipe, (
        "tools/test_manifest.py must be enumerated in `make verify-fast`, which "
        "is the target rungs.yml's m5a-fast job runs; the broad `pytest tools/` "
        "in `make verify` is invoked by no workflow")
    assert "tools/inject_manifest_defects.py" in recipe, (
        "the injected-defect controls have the same problem as the suite did if "
        "they live only in `make controls`, which no workflow invokes either")
    workflow = yaml.safe_load((ROOT / ".github/workflows/rungs.yml").read_text())
    blocks, reason = cw.step_would_block_pull_request(workflow, "m5a-fast", "verify-fast")
    assert blocks, (
        "rungs.yml's m5a-fast job no longer runs `make verify-fast` in a way "
        f"that can block a pull request -- this suite's only CI path is "
        f"gone or neutered: {reason}")


def test_makefile_recipe_returns_only_live_lines(tmp_path):
    """The shared parser drops comment lines, not just collects them. A
    control disabled the ordinary debugging way -- `#` after the leading tab,
    invocation text intact -- never runs. The failure mode depends on the
    recipe's shape, not position alone: a *final* recipe line is dropped
    silently (rc 0, one entry gone), while a *mid-recipe* line ends the
    command at the `#` and what happens next depends on that shape. In a
    sequence of shell commands -- this fixture's shape -- `echo C` then runs
    as its own command (rc 0 here) and `echo B` is the only entry dropped;
    in a recipe that is one continued command taking quoted arguments, as
    `controls:`/`verify-fast:` are, the orphaned argument string becomes a
    bogus command and make fails loudly (`Error 127`), dropping every later
    entry (issue #855). Either way the entry does not run, which is the only
    property the assertion needs. The substring assertions above are
    defeater-resistant only because this filter sits in the parser both
    suites share; in raw recipe text the disabled invocation would still
    match."""
    (tmp_path / "Makefile").write_text(
        "t:\n"
        "\t@echo A \\\n"
        "\t#  echo B \\\n"
        "\techo C\n"
    )
    recipe = makefile_recipe("t", root=tmp_path)
    assert "echo A" in recipe and "echo C" in recipe
    assert "echo B" not in recipe, (
        "echo B sits behind a `#`: make never runs it, so the parser must "
        "not return it as recipe text")


def test_every_permanent_injected_defect_still_has_something_to_inject_into():
    """`tools/inject_manifest_defects.py` reintroduces each defect this module
    shipped and checks the test that catches it goes red. Those injections are
    exact source strings, so a refactor can silently make them un-appliable --
    at which point the control REFUSES (exit 2) rather than reporting green, but
    only when `make controls` is next run. This asserts it in the fast suite
    instead, where the refactor happens."""
    import inject_manifest_defects as inj

    for label, rel, old, _new, _tests in inj.INJECTIONS:
        assert old in (ROOT / rel).read_text(), (
            f"injected-defect control {label!r} can no longer be applied to "
            f"{rel} -- update tools/inject_manifest_defects.py")


def test_the_injected_defect_control_refuses_rather_than_reporting_green(monkeypatch, capsys):
    """The control's own only failure mode that matters: an injection that did
    not apply must be REFUSED (exit 2), never counted as a fired control.

    The bogus control names a real test on purpose, and the refusal is
    asserted by its wording. Since the baseline (#1125) runs before the loop,
    a control naming NO test is refused without reaching its injection -- and
    the first version of that baseline refused for a third reason instead, an
    unselected pytest, so this test passed with its anchor check never run.
    """
    import inject_manifest_defects as inj

    monkeypatch.setattr(inj, "INJECTIONS",
                        [("bogus", "tools/manifest.py", "NOT IN THE SOURCE", "x",
                          [f"{inj.TESTS}::test_hash_is_reproducible_across_calls_for_the_same_config"])])
    assert inj.main([]) == 2
    assert "no longer present" in capsys.readouterr().out


def test_the_injection_harness_refuses_a_control_that_names_no_test(monkeypatch, capsys):
    """A control with no test named has no selection: its pytest would collect
    whatever the staged tree holds and its red would be nobody's. REFUSED
    before any pytest runs."""
    import inject_manifest_defects as inj

    ran = []
    monkeypatch.setattr(inj.subprocess, "run", lambda *a, **k: ran.append(a))
    monkeypatch.setattr(inj, "INJECTIONS",
                        [("bogus", "tools/manifest.py", "NOT IN THE SOURCE", "x", [])])
    assert inj.main([]) == 2
    assert "REFUSED" in capsys.readouterr().out
    assert not ran, "pytest ran with nothing to prove green"


def test_the_injection_harness_refuses_when_the_interpreter_loaded_stale_bytecode(tmp_path):
    """The permanent control for the defect the harness itself shipped (PR #260,
    round 2): it checked that the injected text was on DISK and never that the
    interpreter had COMPILED it.

    Four injections add exactly the ten characters `"False and "`, so their
    `tools/manifest.py` is byte-for-byte the same SIZE; CPython's pyc
    invalidation key is `(mtime-to-the-second, size)`; the runs are sequential
    in one staged tree. Two same-size injections in the same wall-clock second
    therefore ran the PREVIOUS injection's bytecode with the guard under test
    still live, and the harness printed `GREEN (MISSED)` -- 7/8 in CI (~0.2 s
    per pytest, no second boundary crossed), 8/8 on a laptop (~1.2 s, boundary
    crossed). A control whose verdict depends on machine speed.

    This reproduces the collision adversarially rather than waiting for a fast
    machine to find it again: one real injection, then a *benign twin* padded
    to exactly the same source length, both with the pinned mtime and with
    `no_bytecode_cache` / `purge_pycache` switched off -- i.e. the as-shipped
    harness. The twin's test would pass (its guard is intact), so the
    as-shipped harness would have reported `GREEN (MISSED)`. What must happen
    instead is `ControlRefused`: nothing was measured.

    The third run is the falsifiability half -- with the mitigation ON, the same
    twin reports GREEN and does *not* refuse, so the detector is discriminating
    rather than refusing unconditionally.
    """
    import inject_manifest_defects as inj

    real = next(i for i in inj.INJECTIONS if i[0].startswith("render() hard-clips"))
    label, rel, old, new, tests = real
    pad = "  #" + "p" * (len(new) - len(old) - 3)      # a comment: no behaviour change
    assert len(old + pad) == len(new), "the twin must be the same source length"
    twin = (label + " (benign twin, same source length)", rel, old, old + pad, tests)

    tree = tmp_path / "tree"
    inj._stage(tree)

    red, _ = inj.run_one(tree, real, no_bytecode_cache=False, purge_pycache=False)
    assert red, "the real injection must still turn its test red"
    with pytest.raises(inj.ControlRefused, match="interpreter LOADED"):
        inj.run_one(tree, twin, no_bytecode_cache=False, purge_pycache=False)

    twin_red, _ = inj.run_one(tree, twin)      # defaults: the shipped mitigation
    assert not twin_red, (
        "the benign twin changes no behaviour, so with the mitigation on its "
        "test must pass -- if this is red the twin is not benign and the "
        "refusal above proved nothing")
    assert not list(tree.rglob("__pycache__")), (
        "the shipped run must leave no bytecode cache in the staged tree at all "
        "-- that is what makes the next injection's compile unconditional")


def test_the_injection_harness_refuses_when_a_declared_test_is_red_before_any_injection(
        tmp_path, monkeypatch, capsys):
    """#1125, rule 8. `main()` read `pytest exited non-zero` as RED (caught)
    without ever running the named tests on the UNMUTATED tree, so a declared
    test that was red for a reason of its own -- drifted, or broken by the
    same edit that moved its injection -- satisfied every control naming it:
    `9/9 controls fired`, exit 0, with the injections contributing nothing.

    The input that must defeat that is planted here: a declared test replaced
    by `assert 0`, before any injection is applied. `main()` must REFUSE
    (exit 2) and must not run the loop. The second half is the falsifiability
    check: on an untouched staged tree the same baseline is green, so the
    gate discriminates rather than refusing unconditionally.
    """
    import inject_manifest_defects as inj

    declared = {t for injection in inj.INJECTIONS for t in injection[4]}
    red = "test_wav_quantisation_rounds_rather_than_truncating"
    assert f"{inj.TESTS}::{red}" in declared
    real_stage = inj._stage

    def stage_with_that_test_red(dest):
        real_stage(dest)
        suite = dest / inj.TESTS
        # A later definition shadows the earlier one in the module namespace,
        # so this is the declared test pytest collects.
        suite.write_text(suite.read_text() + f"\n\ndef {red}():\n    assert 0\n")

    monkeypatch.setattr(inj, "_stage", stage_with_that_test_red)
    assert inj.main([]) == 2, (
        "a declared test red before any injection is not evidence of an "
        "injection")
    out = capsys.readouterr().out
    assert "REFUSED" in out and red in out
    assert "controls fired" not in out, (
        "the loop ran, and counted a red the injections did not cause")

    monkeypatch.setattr(inj, "_stage", real_stage)
    tree = tmp_path / "tree"
    real_stage(tree)
    rc, detail = inj.baseline(tree)
    assert rc == 0, detail


def test_the_bound_ledger_defaults_to_a_tracked_path():
    """The mechanism `accept()` replaces (`model/sound_report.py`'s
    `LOCK`/`LOCKS`) lives in committed source. A ledger defaulting into the
    gitignored `jobs/` tree -- as this module first did -- detects a bound
    change only inside one long-lived worktree: on a fresh checkout or any CI
    run the history is empty, so nothing is ever detected and the gate silently
    passes everything."""
    assert mf.JOBS_DIR not in mf.BOUNDS_HISTORY.parents
    assert mf.RUNS_DIR not in mf.BOUNDS_HISTORY.parents
    r = subprocess.run(["git", "-C", str(ROOT), "check-ignore", str(mf.BOUNDS_HISTORY)],
                       capture_output=True, text=True)
    if r.returncode not in (0, 1):
        pytest.skip(f"git check-ignore unavailable here (rc={r.returncode})")
    assert r.returncode == 1, (
        f"{mf.BOUNDS_HISTORY} is gitignored, so the previous bound does not "
        f"survive a fresh checkout and no bound change can ever be detected")


# =============================================================================
# _hash_json: the identity every render_id and job_id rests on
# =============================================================================
def test_hash_does_not_collide_on_arrays_that_differ_only_in_the_middle():
    """The collision the first implementation shipped: `json.dumps(...,
    default=str)` hashed `str(array)`, which ELIDES the middle of a large
    array, so two genuinely different configs shared one `render_id` -- and so
    one `runs/<render_id>/` directory."""
    a = np.arange(10000)
    b = a.copy()
    b[5000] = -1
    assert str(a) == str(b), ("the premise of this control: str() elides the "
                              "middle, so the two are indistinguishable by repr")
    assert mf._hash_json({"grid": a}) != mf._hash_json({"grid": b})


def test_hash_is_reproducible_across_calls_for_the_same_config():
    a = np.linspace(0.0, 1.0, 257)
    assert mf._hash_json({"grid": a, "n": 3}) == mf._hash_json({"grid": a.copy(), "n": 3})


def test_hash_refuses_a_config_value_with_no_reproducible_identity():
    """The other direction of the same defect: for an object whose repr carries
    its memory address, `default=str` produced a NEW hash on every call, so the
    same source and config never re-derived the same `render_id` and retroactive
    reanalysis was impossible. Refusing is the honest outcome."""
    class Cfg:
        pass

    with pytest.raises(mf.Refused, match="no reproducible JSON identity"):
        mf._hash_json({"o": Cfg()})


def test_render_refuses_a_config_it_cannot_identify(tmp_path):
    class Cfg:
        pass

    with pytest.raises(mf.Refused, match="no reproducible JSON identity"):
        mf.render("T1", {"o": Cfg()}, lambda: _tone(), runs_dir=tmp_path / "runs")


# =============================================================================
# render / load_render
# =============================================================================
def _tone(seconds=0.05, sr=48000, hz=440.0):
    n = int(sr * seconds)
    t = np.arange(n) / sr
    return (0.5 * np.sin(2 * math.pi * hz * t)).astype(np.float64), sr


def test_render_writes_a_manifest_and_a_content_hashed_wav(tmp_path):
    runs = tmp_path / "runs"
    rec = mf.render("T1", {"hz": 440.0}, lambda: _tone(), runs_dir=runs)
    assert rec["stage"] == "render" and rec["case_id"] == "T1"
    wav_path = runs / rec["render_id"] / rec["wav_path"]
    assert wav_path.exists()
    assert rec["wav_sha256"] == mf.provenance.file_sha(wav_path)
    manifest_path = runs / rec["render_id"] / "manifest.json"
    assert json.loads(manifest_path.read_text())["render_id"] == rec["render_id"]


def test_two_renders_with_different_config_never_collide(tmp_path):
    runs = tmp_path / "runs"
    a = mf.render("T1", {"hz": 440.0}, lambda: _tone(hz=440.0), runs_dir=runs)
    b = mf.render("T1", {"hz": 220.0}, lambda: _tone(hz=220.0), runs_dir=runs)
    assert a["render_id"] != b["render_id"]


def test_load_render_refuses_a_wav_that_no_longer_matches_its_recorded_hash(tmp_path):
    """The precondition `analyse()` depends on, asserted at the point of use
    (CLAUDE.md: "assert your apparatus's preconditions ... and REFUSE rather
    than report when they fail")."""
    runs = tmp_path / "runs"
    rec = mf.render("T1", {"hz": 440.0}, lambda: _tone(), runs_dir=runs)
    wav_path = runs / rec["render_id"] / rec["wav_path"]
    # Corrupt the retained WAV after render() wrote it.
    wav_path.write_bytes(wav_path.read_bytes() + b"\x00\x00")
    with pytest.raises(mf.Refused, match="does not match the hash"):
        mf.load_render(rec["render_id"], runs_dir=runs)


def test_load_render_refuses_a_render_id_with_no_manifest(tmp_path):
    with pytest.raises(mf.Refused, match="no render manifest"):
        mf.load_render("does-not-exist", runs_dir=tmp_path / "runs")


def test_render_refuses_to_overwrite_a_retained_wav_with_different_audio(tmp_path):
    """The write-side half of "a retained WAV is what `render()` produced".

    `load_render()` enforces it on READ, which catches the loss after it has
    happened; this catches it before. Two renders reaching the same
    `render_id` with different audio means something the audio depends on is
    not restated in `config` -- and any `analysis.json` already referencing
    that WAV by `render_wav_sha256` would otherwise come to point at audio
    nobody analysed."""
    runs = tmp_path / "runs"
    first = mf.render("T1", {"hz": 440.0}, lambda: _tone(hz=440.0), runs_dir=runs)
    wav_path = runs / first["render_id"] / first["wav_path"]
    with pytest.raises(mf.Refused, match="already records a DIFFERENT"):
        # Same case id, same config, DIFFERENT audio: exactly the render_fn
        # whose behaviour depends on something `config` does not state.
        mf.render("T1", {"hz": 440.0}, lambda: _tone(hz=550.0), runs_dir=runs)
    assert mf.provenance.file_sha(wav_path) == first["wav_sha256"], (
        "the refusal must leave the already-retained WAV untouched")
    # Re-rendering the SAME audio under the same id is fine -- idempotent, not
    # an error: this is the ordinary "re-run the case" path.
    again = mf.render("T1", {"hz": 440.0}, lambda: _tone(hz=440.0), runs_dir=runs)
    assert again["wav_sha256"] == first["wav_sha256"]


# =============================================================================
# render preconditions: an instrument in a wrong state is not a result
# =============================================================================
def test_render_refuses_non_finite_samples(tmp_path):
    with pytest.raises(mf.Refused, match="non-finite"):
        mf.render("T1", {}, lambda: (np.full(480, np.nan), 48000),
                  runs_dir=tmp_path / "runs")


def test_render_refuses_exact_silence_unless_the_call_says_it_is_intended(tmp_path):
    """CLAUDE.md's own example of preconditions assumed rather than asserted is
    "a Model D rendering exact silence". Silence is a legitimate thing to test
    for, and an illegitimate thing to accept by default."""
    runs = tmp_path / "runs"
    with pytest.raises(mf.Refused, match="exact silence"):
        mf.render("T1", {}, lambda: (np.zeros(4800), 48000), runs_dir=runs)
    rec = mf.render("T1", {"silent": True}, lambda: (np.zeros(4800), 48000),
                     runs_dir=runs, allow_silence=True)
    assert rec["requested_peak"] == 0.0 and rec["allow_silence"] is True


def test_render_refuses_a_clipped_render_and_records_the_peak_when_allowed(tmp_path):
    """A peak-3.0 render used to be hard-clipped to 1.0 with nothing recorded:
    invisible afterwards, because every over-scale sample reads back as exactly
    full scale. Refused by default; recorded either way."""
    runs = tmp_path / "runs"
    loud = lambda: (3.0 * _tone()[0], 48000)                      # peak 1.5
    with pytest.raises(mf.Refused, match="hard-clipped"):
        mf.render("T1", {"gain": 3.0}, loud, runs_dir=runs)
    rec = mf.render("T1", {"gain": 3.0}, loud, runs_dir=runs, allow_clipping=True)
    assert rec["requested_peak"] == pytest.approx(1.5, abs=1e-3)
    assert rec["clipped_samples"] > 0
    on_disk = json.loads((runs / rec["render_id"] / "manifest.json").read_text())
    assert on_disk["clipped_samples"] == rec["clipped_samples"]


def test_a_render_normalised_to_exactly_full_scale_is_not_reported_as_clipped(tmp_path):
    """"Normalise to full scale" is a common convention, and the scale here is
    32768 (the exact inverse of `_read_wav16`), so `+1.0` lands one code above
    int16's positive limit. Thresholding the guard on the int16 range therefore
    reported `clipped_samples: 1` for a 1-LSB truncation at the single positive
    endpoint and REFUSED the render. The guard is on `|x| > 1.0` instead:
    everything actually beyond full scale is still caught.
    """
    runs = tmp_path / "runs"
    x, sr = _tone()
    full = x / np.max(np.abs(x))
    rec = mf.render("T1", {"norm": "peak"}, lambda: (full, sr), runs_dir=runs)
    assert rec["requested_peak"] == pytest.approx(1.0, abs=1e-12)
    assert rec["clipped_samples"] == 0, "full scale is not a wrong instrument state"

    with pytest.raises(mf.Refused, match="hard-clipped"):
        mf.render("T1", {"norm": "over"},
                  lambda: (full * (1.0 + 4.0 / 32768.0), sr), runs_dir=runs)

    # Both endpoints directly: -1.0 is exactly representable, +1.0 costs 1 LSB.
    stats = mf._write_wav16(tmp_path / "fs.wav", np.array([1.0, -1.0, 0.5]), sr)
    assert stats["clipped_samples"] == 0
    back, _ = mf._read_wav16(tmp_path / "fs.wav")
    assert [int(round(v * 32768.0)) for v in back] == [32767, -32768, 16384]


def test_an_unclipped_render_records_its_peak_and_zero_clipped_samples(tmp_path):
    rec = mf.render("T1", {"hz": 440.0}, lambda: _tone(), runs_dir=tmp_path / "runs")
    assert rec["requested_peak"] == pytest.approx(0.5, abs=1e-3)
    assert rec["clipped_samples"] == 0


def test_render_refuses_a_case_id_that_is_not_one_path_component(tmp_path):
    """`render_id` is `f"{case_id}-..."` and is used directly as a directory
    name, so `render("SD/01", ...)` silently created
    `runs/SD/01-<commit>-.../raw.wav` -- a record under a name nobody asked
    for, the same class as the analyser-name nit one call away, and with `..`
    it escapes `runs_dir` entirely.

    Checked before `render_fn()` is called: a render that cannot be honestly
    named should not cost a render first.
    """
    runs = tmp_path / "runs"
    called = []

    def render_fn():
        called.append(1)
        return _tone()

    with pytest.raises(mf.Refused, match="single path component"):
        mf.render("SD/01", {}, render_fn, runs_dir=runs)
    assert not called, "case_id must be checked before render_fn() runs"
    assert not runs.exists(), "nothing may be written under a refused case_id"
    for bad in ("..", ".", "a\\b", "x\ny", ""):
        with pytest.raises(mf.Refused):
            mf.render(bad, {}, render_fn, runs_dir=runs)
    assert not called


def test_wav_quantisation_rounds_rather_than_truncating(tmp_path):
    """`astype("<i2")` alone truncates toward zero -- a half-LSB BIASED
    quantisation applied to the one artefact every measurement re-derives
    from."""
    path = tmp_path / "q.wav"
    stats = mf._write_wav16(path, np.array([1.7, -1.7]) / 32768.0, 48000)
    back, _ = mf._read_wav16(path)
    codes = [int(round(v * 32768.0)) for v in back]
    assert codes == [2, -2], "truncation would have given [1, -1]"
    assert stats["clipped_samples"] == 0


# =============================================================================
# measurement / validate_measurement
# =============================================================================
def _good_measurement(**over):
    kw = dict(name="decay", units="ms", value=12.3, analyser="demo",
              analyser_version="v1", method="dB-slope fit",
              interval_s=(0.0, 0.1), channel="mono", resample_hz=None,
              filter="none", normalisation="none", fft={"used": False},
              fit_quality={"r2": 0.98}, reference_identity="synthetic")
    kw.update(over)
    return mf.measurement(**kw)


def test_a_measurement_missing_a_required_field_is_refused_not_silently_written():
    rec = _good_measurement()
    del rec["interval_s"]           # simulate a hand-built / older-schema record
    with pytest.raises(mf.Refused, match="interval_s"):
        mf.validate_measurement(rec)


def test_analyse_writes_nothing_when_one_measurement_is_incomplete(tmp_path):
    runs, jobs = tmp_path / "runs", tmp_path / "jobs"
    render_rec = mf.render("T1", {"hz": 440.0}, lambda: _tone(), runs_dir=runs)
    bad = _good_measurement(name="broken")
    del bad["fit_quality"]
    with pytest.raises(mf.Refused):
        mf.analyse(render_rec, [_good_measurement(), bad], analyser="demo",
                   analyser_version="v1", config={}, jobs_dir=jobs)
    assert not any(jobs.glob("**/analysis.json"))


def test_analyse_round_trips_through_json(tmp_path):
    runs, jobs = tmp_path / "runs", tmp_path / "jobs"
    render_rec = mf.render("T1", {"hz": 440.0}, lambda: _tone(), runs_dir=runs)
    rec = mf.analyse(render_rec, [_good_measurement()], analyser="demo",
                      analyser_version="v1", config={"window_ms": 4}, jobs_dir=jobs)
    on_disk = json.loads((jobs / rec["job_id"] / "analysis.json").read_text())
    assert on_disk["measurements"]["decay"]["value"] == pytest.approx(12.3)
    assert on_disk["render_id"] == render_rec["render_id"]


def test_analyse_carries_the_case_id_it_analysed(tmp_path):
    """The field a human reads first -- and the one `accept()`'s ledger needs
    to key by (case_id, metric) instead of by metric alone."""
    runs, jobs = tmp_path / "runs", tmp_path / "jobs"
    render_rec = mf.render("SD-01", {"hz": 440.0}, lambda: _tone(), runs_dir=runs)
    rec = mf.analyse(render_rec, [_good_measurement()], analyser="demo",
                      analyser_version="v1", config={}, jobs_dir=jobs)
    assert rec["case_id"] == "SD-01"
    assert json.loads((jobs / rec["job_id"] / "analysis.json").read_text())["case_id"] == "SD-01"


def test_analysis_id_refuses_an_analyser_name_that_is_not_one_path_component(tmp_path):
    runs, jobs = tmp_path / "runs", tmp_path / "jobs"
    render_rec = mf.render("T1", {"hz": 440.0}, lambda: _tone(), runs_dir=runs)
    with pytest.raises(mf.Refused, match="single path component"):
        mf.analyse(render_rec, [_good_measurement()], analyser="t20/v2",
                   analyser_version="v1", config={}, jobs_dir=jobs)


# =============================================================================
# accept
# =============================================================================
def _analysis_with(value, jobs_dir, render_rec, analyser="demo", version="v1"):
    return mf.analyse(render_rec, [_good_measurement(value=value)], analyser=analyser,
                       analyser_version=version, config={}, jobs_dir=jobs_dir)


def test_accept_refuses_a_bound_with_no_rationale(tmp_path):
    runs, jobs = tmp_path / "runs", tmp_path / "jobs"
    render_rec = mf.render("T1", {"hz": 440.0}, lambda: _tone(), runs_dir=runs)
    analysis = _analysis_with(12.3, jobs, render_rec)
    with pytest.raises(mf.Refused, match="no rationale"):
        mf.accept(analysis, {"decay": {"lo": 0.0, "hi": 20.0}}, jobs_dir=jobs)


def test_accept_pass_and_fail(tmp_path):
    runs, jobs = tmp_path / "runs", tmp_path / "jobs"
    render_rec = mf.render("T1", {"hz": 440.0}, lambda: _tone(), runs_dir=runs)
    analysis = _analysis_with(12.3, jobs, render_rec)
    ok = mf.accept(analysis, {"decay": {"lo": 0.0, "hi": 20.0, "rationale": "spec"}},
                    jobs_dir=jobs, history_path=jobs / "_h1.json")
    assert ok["verdicts"]["decay"]["state"] == "pass"
    bad = mf.accept(analysis, {"decay": {"lo": 20.0, "hi": 30.0, "rationale": "spec"}},
                     jobs_dir=jobs, history_path=jobs / "_h2.json")
    assert bad["verdicts"]["decay"]["state"] == "fail"


def test_accept_records_why_a_bound_changed(tmp_path):
    """The gap this issue names in `model/sound_report.py`'s `LOCK`/`LOCKS`:
    today a lock value is just overwritten. Here, changing a bound without a
    reason is impossible (rationale is mandatory on every call), and the
    change itself -- old bound, new bound, both reasons -- is written to the
    record when it happens."""
    runs, jobs = tmp_path / "runs", tmp_path / "jobs"
    render_rec = mf.render("T1", {"hz": 440.0}, lambda: _tone(), runs_dir=runs)
    analysis = _analysis_with(12.3, jobs, render_rec)
    hist = jobs / "_bounds_history.json"
    first = mf.accept(analysis, {"decay": {"lo": 0.0, "hi": 20.0, "rationale": "initial spec"}},
                       jobs_dir=jobs, history_path=hist)
    assert first["bound_changes"] == []
    second = mf.accept(analysis, {"decay": {"lo": 5.0, "hi": 25.0,
                                             "rationale": "re-measured reference, +5ms"}},
                        jobs_dir=jobs, history_path=hist)
    assert len(second["bound_changes"]) == 1
    change = second["bound_changes"][0]
    assert {"lo": change["previous"]["lo"], "hi": change["previous"]["hi"],
            "rationale": change["previous"]["rationale"]} == {
        "lo": 0.0, "hi": 20.0, "rationale": "initial spec"}
    assert change["new"]["rationale"] == "re-measured reference, +5ms"
    # An unchanged bound on a THIRD call logs no change.
    third = mf.accept(analysis, {"decay": {"lo": 5.0, "hi": 25.0,
                                            "rationale": "re-measured reference, +5ms"}},
                       jobs_dir=jobs, history_path=hist)
    assert third["bound_changes"] == []
    # Which run set the bound that moved is the first thing a reader wants.
    assert change["previous"]["job_id"] == analysis["job_id"]


def test_the_bound_ledger_is_keyed_by_case_and_metric_not_by_metric_alone(tmp_path):
    """Keyed by metric name alone, accepting `T20` for one case and then for
    another fabricated a `bound_changes` entry claiming the bound had moved
    between two unrelated cases' values. On a scorecard with sixteen drum
    voices each carrying a `T20`, every accept reported a change that had not
    happened -- and a ledger that cries wolf trains everyone to ignore gates,
    including the ones that work (CLAUDE.md)."""
    runs, jobs = tmp_path / "runs", tmp_path / "jobs"
    hist = jobs / "_bounds_history.json"
    render_a = mf.render("CASE-A", {"hz": 440.0}, lambda: _tone(hz=440.0), runs_dir=runs)
    render_b = mf.render("CASE-B", {"hz": 220.0}, lambda: _tone(hz=220.0), runs_dir=runs)
    analysis_a = mf.analyse(render_a, [_good_measurement(name="T20", value=10.0)],
                             analyser="demo", analyser_version="v1", config={},
                             jobs_dir=jobs)
    analysis_b = mf.analyse(render_b, [_good_measurement(name="T20", value=90.0)],
                             analyser="demo", analyser_version="v1", config={},
                             jobs_dir=jobs)
    first = mf.accept(analysis_a, {"T20": {"lo": 5.0, "hi": 15.0,
                                            "rationale": "case A spec"}},
                       jobs_dir=jobs, history_path=hist)
    second = mf.accept(analysis_b, {"T20": {"lo": 85.0, "hi": 95.0,
                                             "rationale": "case B spec"}},
                        jobs_dir=jobs, history_path=hist)
    assert first["bound_changes"] == []
    assert second["bound_changes"] == [], (
        "nothing moved: these are two different cases' bounds for a metric that "
        "happens to share a name")
    assert first["verdicts"]["T20"]["state"] == "pass"
    assert second["verdicts"]["T20"]["state"] == "pass"

    # A REAL change, to the same case's own bound, is still caught.
    moved = mf.accept(analysis_a, {"T20": {"lo": 6.0, "hi": 16.0,
                                           "rationale": "re-measured reference"}},
                       jobs_dir=jobs, history_path=hist)
    assert len(moved["bound_changes"]) == 1
    assert moved["bound_changes"][0]["case_id"] == "CASE-A"
    assert moved["bound_changes"][0]["previous"]["rationale"] == "case A spec"


def test_accept_refuses_an_analysis_that_does_not_say_which_case_it_is_of(tmp_path):
    runs, jobs = tmp_path / "runs", tmp_path / "jobs"
    render_rec = mf.render("T1", {"hz": 440.0}, lambda: _tone(), runs_dir=runs)
    analysis = _analysis_with(12.3, jobs, render_rec)
    del analysis["case_id"]
    with pytest.raises(mf.Refused, match="no case_id"):
        mf.accept(analysis, {"decay": {"lo": 0.0, "hi": 20.0, "rationale": "spec"}},
                  jobs_dir=jobs, history_path=jobs / "_h.json")


def test_accept_does_not_score_a_boolean_as_a_measured_value(tmp_path):
    """`bool` subclasses `int`, so `True` would otherwise be compared against
    the bounds as 1.0 and reported as a value that was measured."""
    runs, jobs = tmp_path / "runs", tmp_path / "jobs"
    render_rec = mf.render("T1", {"hz": 440.0}, lambda: _tone(), runs_dir=runs)
    analysis = _analysis_with(True, jobs, render_rec)
    out = mf.accept(analysis, {"decay": {"lo": 0.0, "hi": 20.0, "rationale": "spec"}},
                     jobs_dir=jobs, history_path=jobs / "_h.json")
    assert out["verdicts"]["decay"]["state"] == "no verdict"


# =============================================================================
# diagnose: "did the sound change, or only the measurement?"
# =============================================================================
def test_diagnose_same_render_different_analyser_is_measurement_changed(tmp_path):
    runs, jobs = tmp_path / "runs", tmp_path / "jobs"
    render_rec = mf.render("T1", {"hz": 440.0}, lambda: _tone(), runs_dir=runs)
    a = _analysis_with(10.0, jobs, render_rec, version="v1")
    b = _analysis_with(8.0, jobs, render_rec, version="v2")
    d = mf.diagnose(a, b, "decay")
    assert d["verdict"] == "measurement changed"
    assert d["delta"] == pytest.approx(-2.0)


def test_diagnose_different_render_same_analyser_is_sound_changed(tmp_path):
    runs, jobs = tmp_path / "runs", tmp_path / "jobs"
    a_render = mf.render("T1", {"hz": 440.0}, lambda: _tone(hz=440.0), runs_dir=runs)
    b_render = mf.render("T1", {"hz": 220.0}, lambda: _tone(hz=220.0), runs_dir=runs)
    a = _analysis_with(10.0, jobs, a_render)
    b = _analysis_with(7.0, jobs, b_render)
    d = mf.diagnose(a, b, "decay")
    assert d["verdict"] == "sound changed"


def test_diagnose_refuses_when_both_render_and_analyser_differ(tmp_path):
    """The comparison this function cannot honestly answer -- it must say so,
    not guess."""
    runs, jobs = tmp_path / "runs", tmp_path / "jobs"
    a_render = mf.render("T1", {"hz": 440.0}, lambda: _tone(hz=440.0), runs_dir=runs)
    b_render = mf.render("T1", {"hz": 220.0}, lambda: _tone(hz=220.0), runs_dir=runs)
    a = _analysis_with(10.0, jobs, a_render, version="v1")
    b = _analysis_with(7.0, jobs, b_render, version="v2")
    with pytest.raises(mf.Refused, match="cannot separate"):
        mf.diagnose(a, b, "decay")


# =============================================================================
# End-to-end: a synthetic stand-in for the snare 8.8 -> 7.6 -> 3.4 case
# =============================================================================
TRUE_TAU_MS = 40.0          # the spec's decay time constant
TARGET_T20_MS = TRUE_TAU_MS * math.log(10)   # ~92.1 ms: T20 for that tau


def _synthetic_decay(tau_ms, sr=48000, dur_s=0.25, seed=0, peak=0.5):
    """A stand-in drum hit: white noise under an exponential envelope with a
    known, exact time constant -- so the "true" T20 is knowable independently
    of any estimator, the way an external reference recording would be.

    Scaled to `peak` because the retained artefact is a 16-bit WAV and
    `render()` refuses a render that would hard-clip into it. As first written
    this returned raw `standard_normal` samples, whose tails reach ~4 sigma, so
    the loudest part of every one of these "hits" -- the attack, where the
    decay fit starts -- was silently clipped to full scale in the WAV the
    measurement then read back. A constant gain cannot change a decay *time*
    (both laws below work in dB relative to the signal's own peak), so the
    scaling costs this control nothing; the clipping was flattening the first
    milliseconds of the envelope it was fitting.
    """
    n = int(sr * dur_s)
    t = np.arange(n) / sr
    rng = np.random.default_rng(seed)
    x = (rng.standard_normal(n) * np.exp(-t / (tau_ms / 1000.0))).astype(np.float64)
    return x * (peak / np.max(np.abs(x))), sr


def _rms_envelope(x, sr, win_ms=2.0):
    win = max(1, int(sr * win_ms / 1000.0))
    n_frames = len(x) // win
    frames = x[: n_frames * win].reshape(n_frames, win)
    rms = np.sqrt(np.mean(frames ** 2, axis=1))
    t_ms = (np.arange(n_frames) * win + win / 2) / sr * 1000.0
    return rms, t_ms


def _t20_law_buggy(x, sr):
    """The historical bug this control reinstates (docs/verification-rules.md
    rule 5: "a bug is not closed until it is an injection"): measure the
    envelope's fall over a FIXED, short absolute window (the first 20 ms)
    and extrapolate that rate LINEARLY out to -20 dB. Over a window this
    short relative to the actual decay this systematically overestimates --
    the same shape of error as `bd-ma-envelope`'s wrong-law family, and (per
    this suite's docstring) a synthetic stand-in for the wrong TONE law
    behind the real snare 8.8 -> 7.6 -> 3.4 incident."""
    rms, _ = _rms_envelope(x, sr)
    win_frames = 10                       # 10 * 2 ms = 20 ms
    if len(rms) < 2 * win_frames:
        return None, None
    early, late = rms[:win_frames].mean(), rms[win_frames:2 * win_frames].mean()
    if late <= 0 or early <= late:
        return None, None
    db_per_ms = 20.0 * math.log10(early / late) / 20.0
    if db_per_ms <= 0:
        return None, None
    return 20.0 / db_per_ms, None       # no fit-quality diagnostics at all


def _t20_law_corrected(x, sr):
    """The corrected law: fit the dB envelope against time over the whole
    decay down to -20 dB (not a fixed 20 ms prefix) and read T20 off the
    fitted slope, reporting the fit's own R^2 as its fit quality."""
    rms, t_ms = _rms_envelope(x, sr)
    peak_i = int(np.argmax(rms))
    peak = rms[peak_i]
    if peak <= 0:
        return None, None
    seg, seg_t = rms[peak_i:], t_ms[peak_i:] - t_ms[peak_i]
    db = 20.0 * np.log10(np.maximum(seg, 1e-9) / peak)
    mask = db > -20.0
    if mask.sum() < 8:
        return None, None
    slope, intercept = np.polyfit(seg_t[mask], db[mask], 1)
    if slope >= 0:
        return None, None
    resid = db[mask] - (slope * seg_t[mask] + intercept)
    ss_res, ss_tot = float(np.sum(resid ** 2)), float(np.sum((db[mask] - db[mask].mean()) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else None
    return -20.0 / slope, r2


def _make_measurement(t20_ms, fit_quality, law_name, law_version):
    why = None if t20_ms is not None else "the estimator did not converge"
    return mf.measurement(
        "T20", "ms", t20_ms, analyser=law_name, analyser_version=law_version,
        method="RMS envelope, dB-domain slope fit" if fit_quality else
               "RMS envelope, fixed 20ms window extrapolated linearly",
        interval_s=(0.0, 0.25), channel="mono", resample_hz=None, filter="none",
        normalisation="none", fft={"used": False},
        fit_quality=({"r2": fit_quality} if fit_quality is not None
                      else {"r2": None, "note": "no fit was performed; a two-point "
                                                 "extrapolation has no residual to report"}),
        reference_identity=f"synthetic ground truth: exponential decay, "
                            f"tau={TRUE_TAU_MS}ms, seed=0", why=why)


def test_retroactive_reanalysis_separates_the_sound_fix_from_the_measurement_fix(tmp_path):
    """A synthetic stand-in for the snare 8.8 -> 7.6 -> 3.4 case (issue #68
    acceptance criterion 4 names this substitute as acceptable).

    Story: the model's decay was actually wrong (55ms instead of the 40ms
    spec) AND the T20 estimator in use at the time had the historical
    fixed-window-extrapolation bug. Two separate fixes landed:

      1. `analyse()` the SAME retained WAV again with the corrected law.
         Nothing was re-rendered -- `diagnose()` must report
         "measurement changed", and the reported distance shrinks
         (some of the original number WAS the wrong law).
      2. Actually fix the model (tau 55ms -> 40ms) and render again, then
         re-analyse with the (now-trusted) corrected law. `diagnose()` must
         report "sound changed", and `accept()`'s verdict flips from FAIL to
         PASS -- the law fix alone was not enough; the real fix was required.
    """
    runs, jobs = tmp_path / "runs", tmp_path / "jobs"
    hist = jobs / "_bounds_history.json"
    criteria = {"T20": {"lo": TARGET_T20_MS - 10.0, "hi": TARGET_T20_MS + 10.0,
                         "rationale": "synthetic spec: tau=40ms exponential decay"}}

    # -- render once, with the model's decay WRONG (55ms instead of 40ms) ---
    render_a = mf.render("SD-SYNTH", {"tau_ms": 55.0, "seed": 0},
                          lambda: _synthetic_decay(55.0), runs_dir=runs)

    # -- leg 1: re-analyse the SAME retained WAV, two different T20 laws ----
    a_manifest, a_x, a_sr = mf.load_render(render_a["render_id"], runs_dir=runs)
    buggy_t20, _ = _t20_law_buggy(a_x, a_sr)
    fixed_t20_a, r2_a = _t20_law_corrected(a_x, a_sr)
    assert buggy_t20 is not None and fixed_t20_a is not None

    analysis_a_buggy = mf.analyse(
        a_manifest, [_make_measurement(buggy_t20, None, "t20-fixed-window", "v1")],
        analyser="t20-fixed-window", analyser_version="v1", config={}, jobs_dir=jobs)
    analysis_a_fixed = mf.analyse(
        a_manifest, [_make_measurement(fixed_t20_a, r2_a, "t20-db-slope", "v2")],
        analyser="t20-db-slope", analyser_version="v2", config={}, jobs_dir=jobs)

    diag_measurement_only = mf.diagnose(analysis_a_buggy, analysis_a_fixed, "T20")
    assert diag_measurement_only["verdict"] == "measurement changed"

    distance_buggy = abs(buggy_t20 - TARGET_T20_MS)
    distance_law_fixed = abs(fixed_t20_a - TARGET_T20_MS)
    # The law fix alone shrinks the reported distance -- some of the
    # original number really was window leakage from the wrong law.
    assert distance_law_fixed < distance_buggy

    verdict_before_sound_fix = mf.accept(analysis_a_fixed, criteria, jobs_dir=jobs,
                                          history_path=hist)
    assert verdict_before_sound_fix["verdicts"]["T20"]["state"] == "fail", (
        "the law fix alone must NOT be enough to pass -- the model's decay is "
        "still genuinely wrong at this point in the story")

    # -- leg 2: the model itself is fixed (55ms -> 40ms); render again ------
    render_b = mf.render("SD-SYNTH", {"tau_ms": 40.0, "seed": 0},
                          lambda: _synthetic_decay(40.0), runs_dir=runs)
    assert render_b["render_id"] != render_a["render_id"]
    b_manifest, b_x, b_sr = mf.load_render(render_b["render_id"], runs_dir=runs)
    fixed_t20_b, r2_b = _t20_law_corrected(b_x, b_sr)
    assert fixed_t20_b is not None

    analysis_b_fixed = mf.analyse(
        b_manifest, [_make_measurement(fixed_t20_b, r2_b, "t20-db-slope", "v2")],
        analyser="t20-db-slope", analyser_version="v2", config={}, jobs_dir=jobs)

    diag_sound_change = mf.diagnose(analysis_a_fixed, analysis_b_fixed, "T20")
    assert diag_sound_change["verdict"] == "sound changed"

    verdict_after_sound_fix = mf.accept(analysis_b_fixed, criteria, jobs_dir=jobs,
                                         history_path=hist)
    assert verdict_after_sound_fix["verdicts"]["T20"]["state"] == "pass"

    # The raw WAVs and numerical traces are retained -- issue #68 acceptance
    # criterion 5 -- so this whole reanalysis was possible from disk alone.
    assert (runs / render_a["render_id"] / "raw.wav").exists()
    assert (runs / render_b["render_id"] / "raw.wav").exists()


# =============================================================================
# #881: the bound ledger is written atomically and read with a declared refusal
# =============================================================================
# `history_path.write_text(_dumps(history))` is the #842 shape on a *tracked*
# file: truncate-then-write, so a kill landing inside it (a session reaped, a
# laptop dying) leaves `spec/acceptance-bounds-history.json` short, and every
# later `accept()` dies on a bare JSONDecodeError until a human restores the
# file. The same injected-kill control PR #862 established for the DAG records.

def test_a_kill_during_the_ledger_write_leaves_the_previous_ledger_intact(tmp_path, monkeypatch):
    """THE CONTROL (start red): this failed against the bare `write_text`.

    Both hooks simulate the same physical event -- a kill inside the write --
    on whichever writer is live: truncate-then-half-write-then-die for
    `write_text`, die-after-tmp-before-replace for `write_json_atomic`.
    Pre-fix the first hook fired and the ledger was left truncated; post-fix
    the original ledger survives both."""
    runs, jobs = tmp_path / "runs", tmp_path / "jobs"
    render_rec = mf.render("T1", {"hz": 440.0}, lambda: _tone(), runs_dir=runs)
    analysis = _analysis_with(12.3, jobs, render_rec)
    hist = jobs / "_bounds_history.json"
    mf.accept(analysis, {"decay": {"lo": 0.0, "hi": 20.0, "rationale": "spec"}},
              jobs_dir=jobs, history_path=hist)
    original = hist.read_bytes()

    def kill_mid_truncate_write(self, data, *a, **k):
        with self.open("w") as fh:
            fh.write(data[: len(data) // 2])
        raise RuntimeError("injected: killed mid truncate-then-write")

    def kill_before_replace(src, dst):
        raise RuntimeError("injected: killed after tmp write, before replace")

    monkeypatch.setattr(pathlib.Path, "write_text", kill_mid_truncate_write)
    monkeypatch.setattr(os, "replace", kill_before_replace)

    with pytest.raises(RuntimeError):
        mf.accept(analysis, {"decay": {"lo": 2.0, "hi": 22.0, "rationale": "moved"}},
                  jobs_dir=jobs, history_path=hist)

    assert json.loads(hist.read_bytes()) == json.loads(original), (
        "a kill during the ledger write corrupted the previous complete ledger")
    assert list(jobs.glob("*tmp*")) == [], (
        f"a tmp sibling survived the failed write: {list(jobs.glob('*tmp*'))}")


def test_a_truncated_bound_ledger_is_refused_with_its_path_not_a_traceback(tmp_path):
    """The read half of #842: a ledger left short by a killed write must make
    the next accept() REFUSE -- naming the file and how to restore it -- not
    die with a bare JSONDecodeError that reads as a bug in the reader rather
    than a corrupted input."""
    runs, jobs = tmp_path / "runs", tmp_path / "jobs"
    render_rec = mf.render("T1", {"hz": 440.0}, lambda: _tone(), runs_dir=runs)
    analysis = _analysis_with(12.3, jobs, render_rec)
    hist = jobs / "_bounds_history.json"
    hist.write_text('{"T1": {"decay": {"lo": 0.0')  # a kill's leftover

    with pytest.raises(mf.Refused, match="_bounds_history\\.json"):
        mf.accept(analysis, {"decay": {"lo": 0.0, "hi": 20.0, "rationale": "spec"}},
                  jobs_dir=jobs, history_path=hist)


def test_the_ledger_write_keeps_its_format_and_ignores_a_stale_tmp_sibling(tmp_path):
    """The ledger's bytes stay the serialisation it already shipped
    (`json.dumps(..., indent=2, sort_keys=True)` plus the atomic writer's
    trailing newline), and a `.tmp-<pid>` leftover from an earlier killed run
    is neither read as history nor picked up by the next write."""
    runs, jobs = tmp_path / "runs", tmp_path / "jobs"
    render_rec = mf.render("T1", {"hz": 440.0}, lambda: _tone(), runs_dir=runs)
    analysis = _analysis_with(12.3, jobs, render_rec)
    hist = jobs / "_bounds_history.json"
    stale = jobs / "_bounds_history.json.tmp-999999"
    jobs.mkdir(parents=True, exist_ok=True)
    stale.write_text('{"garbage": true')  # a previous kill's leftover

    rec = mf.accept(analysis, {"decay": {"lo": 0.0, "hi": 20.0, "rationale": "spec"}},
                    jobs_dir=jobs, history_path=hist)

    loaded = json.loads(hist.read_text())
    assert hist.read_bytes() == (json.dumps(loaded, indent=2, sort_keys=True) + "\n").encode(), (
        "the atomic write changed the ledger's serialisation")
    assert list(loaded) == [rec["case_id"]] and stale.read_bytes() == b'{"garbage": true', (
        "a stale tmp sibling was consumed as history or clobbered by the new write")


# =============================================================================
# load_json_refused: a record truncated mid-UTF-8-character is REFUSED, not a
# bare traceback (#909)
# =============================================================================
@pytest.mark.parametrize("glyph", ["é", "—", "\U0001D11E"])  # 2-, 3- and 4-byte sequences
def test_load_json_refused_refuses_a_record_truncated_mid_multibyte_utf8(tmp_path, glyph):
    """A ledger truncated inside a multi-byte UTF-8 character dies in
    `Path.read_text()` with `UnicodeDecodeError` -- not `JSONDecodeError` --
    so the #883 helper's single except clause let it escape as a bare
    traceback instead of a `Refused` naming the path and the restore step
    (#909). Truncation is exercised at the 2-, 3- and 4-byte sequence
    boundaries; every one refuses with the same shape as a torn-JSON record."""
    p = tmp_path / "ledger.json"
    good = json.dumps({"case_id": "SD/01", "note": f"kept {glyph} intact"},
                      ensure_ascii=False).encode()
    i = good.index(glyph.encode())
    p.write_bytes(good[: i + 1])  # cut mid-character: the #842 kill's leftover shape

    with pytest.raises(mf.Refused,
                       match=r"ledger\.json.*not valid UTF-8.*unexpected end of data"
                             r".*git checkout"):
        mf.load_json_refused(p, "bound ledger")


def test_load_json_refused_refuses_invalid_utf8_that_is_not_a_truncation(tmp_path):
    """Invalid UTF-8 that is not a truncation -- a lone continuation byte
    after an ASCII character -- dies the same way and gets the same refusal,
    so the branch cannot be written to only answer the truncated-tail case."""
    p = tmp_path / "record.json"
    good = json.dumps({"case_id": "SD/01"}).encode()
    p.write_bytes(good.replace(b"SD", b"S\x80"))

    with pytest.raises(mf.Refused,
                       match=r"record\.json.*not valid UTF-8.*invalid start byte"
                             r".*git checkout"):
        mf.load_json_refused(p, "analysis record")


_LOCALE_ENVS = {
    # The decoder `read_text()` would pick is the host locale's. C with coercion
    # and UTF-8 mode off is ASCII; the Latin-1 locale is the judge's (#1849).
    "c-ascii": {"LC_ALL": "C", "PYTHONUTF8": "0", "PYTHONCOERCECLOCALE": "0"},
}


def _load_in_fresh_interpreter(path, env_extra):
    code = ("import sys, pathlib; sys.path.insert(0, %r); import manifest as mf; "
            "print(sorted(mf.load_json_refused(pathlib.Path(sys.argv[1]), 'record')))"
            % str(pathlib.Path(__file__).parent))
    return subprocess.run([sys.executable, "-c", code, str(path)],
                          env={**os.environ, **env_extra}, capture_output=True, text=True)


def test_load_json_refused_reads_valid_utf8_whatever_the_host_locale(tmp_path):
    """POSITIVE control that runs on CI's UTF-8 hosts: a valid UTF-8 record
    holding a non-ASCII character must load under the C locale. A bare
    `read_text()` decodes with the locale (ASCII here) and refuses it; this
    fails on the unpinned helper and passes with `encoding="utf-8"` (#1849)."""
    p = tmp_path / "record.json"
    p.write_bytes(json.dumps({"note": "caf\u00e9"}, ensure_ascii=False).encode("utf-8"))
    r = _load_in_fresh_interpreter(p, _LOCALE_ENVS["c-ascii"])
    assert r.returncode == 0, r.stderr
    assert "note" in r.stdout


def test_load_json_refused_refuses_latin1_bytes_whatever_the_host_locale(tmp_path):
    """A Latin-1 record (0xe9 inside a JSON string, otherwise valid JSON) is
    refused as non-UTF-8 and names the path, not accepted by a Latin-1 locale."""
    p = tmp_path / "record.json"
    p.write_bytes(json.dumps({"note": "caf\u00e9"}, ensure_ascii=False).encode("latin-1"))
    with pytest.raises(mf.Refused, match=r"record\.json.*not valid UTF-8"):
        mf.load_json_refused(p, "record")


# =============================================================================
# require_keys: a record that PARSES but cannot be READ is REFUSED, in the
# caller's own Refused class (#1604)
# =============================================================================
class _CallerRefused(mf.refusal.Refused):
    """Stands in for a reader module's own `Refused`: a sibling of
    `mf.Refused`, not a subclass of it, so a test can tell which was raised."""


def test_require_keys_accepts_a_record_that_carries_every_key():
    assert mf.require_keys({"state": "CAPTURED", "takes": {}}, ("state", "takes"),
                           "the capture record", refused=_CallerRefused) is None


def test_require_keys_raises_the_callers_declared_refusal():
    # `record["takes"]` on this record is the KeyError that exited 1 by
    # traceback before #1604; the refusal names the record and the key.
    with pytest.raises(_CallerRefused, match=r"the capture record.*'takes'") as raised:
        mf.require_keys({"state": "CAPTURED"}, "takes", "the capture record", refused=_CallerRefused)
    # the caller's main() catches its own class, not this module's
    assert not isinstance(raised.value, mf.Refused)


def test_require_keys_without_the_callers_class_is_a_type_error():
    # A default of mf.Refused would raise a class the caller's main() does not
    # catch: the traceback this helper exists to replace, reintroduced quietly.
    with pytest.raises(TypeError):
        mf.require_keys({"state": "CAPTURED"}, ("takes",), "the capture record")


def test_require_keys_names_every_offending_key_not_only_the_first():
    with pytest.raises(_CallerRefused) as raised:
        mf.require_keys({"diagnostics": None, "audio": "x.wav"}, ("diagnostics", "audio", "metrics"),
                        "the M1A board record", refused=_CallerRefused)
    msg = str(raised.value)
    assert "is missing required key(s) 'metrics'" in msg
    assert "carries null for required key(s) 'diagnostics'" in msg
    assert "'audio'" not in msg.partition(" -- ")[0]      # present and non-null: not named


@pytest.mark.parametrize("record, kind", [
    (None, "null"), ([], "a JSON list"), (5, "a JSON int"), ("x", "a JSON str"), (True, "a JSON bool")])
def test_require_keys_refuses_a_record_that_is_not_a_json_object(record, kind):
    # `"takes" not in record` cannot answer this: on a list it is merely true,
    # which would read as a missing key; on a number it is a TypeError.
    with pytest.raises(_CallerRefused, match=f"the capture record is {kind}, not a JSON object"):
        mf.require_keys(record, "takes", "the capture record", refused=_CallerRefused)


def test_require_keys_refuses_a_null_and_reads_a_bare_string_as_one_key():
    with pytest.raises(_CallerRefused, match=r"carries null for required key\(s\) 'takes'"):
        mf.require_keys({"takes": None}, "takes", "the capture record", refused=_CallerRefused)
    with pytest.raises(_CallerRefused) as raised:
        mf.require_keys({}, "takes", "the capture record", refused=_CallerRefused)
    assert "'takes'" in str(raised.value) and "'t'" not in str(raised.value)


# =============================================================================
# require_keys: a key that is PRESENT, is not null, and is of a type the
# reader cannot dereference is REFUSED too (#1679). `kinds` is optional and
# per key.
# =============================================================================
def test_require_keys_refuses_a_key_of_the_wrong_type_in_the_callers_class():
    # the issue's own input: present and not null, so #1619's guard passes it,
    # and `record["takes"].items()` one line later is an AttributeError -- the
    # traceback #1604 removed, reached by a one-character edit to the record
    with pytest.raises(_CallerRefused) as raised:
        mf.require_keys({"state": "CAPTURED", "takes": 5}, "takes", "the capture record",
                        refused=_CallerRefused, kinds={"takes": dict})
    msg = str(raised.value)
    assert "'takes'" in msg and "dict" in msg and "int" in msg
    assert not isinstance(raised.value, mf.Refused)


def test_require_keys_without_kinds_leaves_behaviour_unchanged():
    # omitting `kinds` is exactly the helper #1619 merged: it does not refuse a type
    assert mf.require_keys({"state": "CAPTURED", "takes": 5}, "takes", "the capture record",
                           refused=_CallerRefused) is None


def test_require_keys_accepts_a_key_of_the_declared_type():
    assert mf.require_keys({"state": "CAPTURED", "takes": {"A": {}}}, ("state", "takes"),
                           "the capture record", refused=_CallerRefused,
                           kinds={"takes": dict}) is None


@pytest.mark.parametrize("value, named, kind", [
    (5, "a JSON int", dict), ("x", "a JSON str", dict), ([], "a JSON list", dict),
    ({}, "a JSON dict", list), (True, "a JSON bool", dict), (1.5, "a JSON float", dict)])
def test_require_keys_names_the_actual_and_the_expected_type(value, named, kind):
    with pytest.raises(_CallerRefused, match="the capture record carries the wrong type") as raised:
        mf.require_keys({"grid": value}, "grid", "the capture record",
                        refused=_CallerRefused, kinds={"grid": kind})
    assert f"'grid' ({named}, read as {kind.__name__})" in str(raised.value)


def test_require_keys_states_a_wrong_type_with_every_other_offence_in_one_refusal():
    # every offending key in ONE refusal, as for missing and null: a record is
    # not regenerated once per key
    with pytest.raises(_CallerRefused) as raised:
        mf.require_keys({"takes": 5, "grid": "x", "identity": None},
                        ("takes", "grid", "rows", "identity"), "the plan",
                        refused=_CallerRefused, kinds={"takes": dict, "grid": list})
    msg = str(raised.value)
    assert "is missing required key(s) 'rows'" in msg
    assert "carries null for required key(s) 'identity'" in msg
    assert ("carries the wrong type for required key(s) 'takes' (a JSON int, read as dict), "
            "'grid' (a JSON str, read as list)") in msg


def test_require_keys_a_key_named_in_kinds_is_required_even_if_not_listed():
    # the input that defeats a looser design: if `kinds` checked only the keys
    # that are PRESENT, a caller who names a kind and forgets `required` would
    # pass an empty record here and meet the KeyError #1604 removed
    with pytest.raises(_CallerRefused, match=r"is missing required key\(s\) 'takes'"):
        mf.require_keys({}, (), "the capture record", refused=_CallerRefused, kinds={"takes": dict})
    with pytest.raises(_CallerRefused, match=r"carries null for required key\(s\) 'takes'"):
        mf.require_keys({"takes": None}, (), "the capture record",
                        refused=_CallerRefused, kinds={"takes": dict})


def test_require_keys_a_key_listed_in_both_required_and_kinds_is_named_once():
    with pytest.raises(_CallerRefused) as raised:
        mf.require_keys({"takes": 5}, "takes", "the capture record",
                        refused=_CallerRefused, kinds={"takes": dict})
    msg = str(raised.value)
    assert msg.count("'takes'") == 1 and "missing" not in msg


@pytest.mark.parametrize("value", [True, 1.0, "1", []])
def test_require_keys_a_bool_is_not_an_int_even_though_isinstance_says_so(value):
    # `isinstance(True, int)` is True in Python; in JSON `true` is not a number,
    # and a rate of `true` is not a rate
    with pytest.raises(_CallerRefused, match="carries the wrong type"):
        mf.require_keys({"sample_rate_hz": value}, "sample_rate_hz", "the manifest",
                        refused=_CallerRefused, kinds={"sample_rate_hz": int})
    assert mf.require_keys({"sample_rate_hz": 48000}, "sample_rate_hz", "the manifest",
                           refused=_CallerRefused, kinds={"sample_rate_hz": int}) is None


def test_require_keys_accepts_a_tuple_of_types_and_still_excludes_bool():
    kw = dict(refused=_CallerRefused, kinds={"gain_db": (int, float)})
    assert mf.require_keys({"gain_db": 1}, "gain_db", "the record", **kw) is None
    assert mf.require_keys({"gain_db": -4.5}, "gain_db", "the record", **kw) is None
    with pytest.raises(_CallerRefused,
                       match=r"'gain_db' \(a JSON bool, read as int or float\)"):
        mf.require_keys({"gain_db": True}, "gain_db", "the record", **kw)

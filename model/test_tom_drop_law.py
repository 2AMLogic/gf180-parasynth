#!/usr/bin/env python3
"""The pitch-drop law's own bench: the fit, the comparator, and the constants
drums_fx actually ships, each with the refusals demonstrated red.

`model/tom_drop_fit.py` and `model/tom_drop_compare.py` produced the numbers in
`docs/tom-pitch-drop-correction.md`. A tool that produced a number is a
deliverable, so it carries its validation cases here -- including the three
preconditions that must REFUSE rather than answer.
"""
from __future__ import annotations
import json, math, os, pathlib, sys
import numpy as np
import pytest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "audition"))
import drums_fx as dx
import tom_drop_fit as F
import tom_drop_compare as C
import tom_drop_docs as D

REPO = HERE.parent
LAW = json.loads((REPO / "docs" / "tom-pitch-drop-law.json").read_text(encoding="utf-8"))


# --------------------------------------------------------------- the fit ----

def test_the_fit_reproduces_the_committed_law():
    """Re-running the fit on the merged measurement must give back the constants
    the correction was published with. If `docs/tom-pitch-drop-results.json`
    ever changes, this is what notices."""
    r = F.run()
    assert r["tom"]["K"] == pytest.approx(LAW["tom"]["K"], rel=1e-9)
    assert r["tom"]["A0"] == pytest.approx(LAW["tom"]["A0"], rel=1e-9)
    assert r["G"]["tom"] == pytest.approx(LAW["G"]["tom"], rel=1e-9)
    assert r["conga"]["A0"] == pytest.approx(LAW["conga"]["A0"], rel=1e-9)


def test_what_drums_fx_SHIPS_is_what_the_fit_measured():
    """The constants in the block and the constants in the fit are the same
    numbers. Rounding to three or four places is allowed; drifting is not.

    This is the join that matters: a law derived in one file and a coefficient
    typed into another is exactly the shape of defect this repository keeps
    finding."""
    K = LAW["tom"]["K"]
    A0 = LAW["tom"]["A0"]
    assert dx.TOM_DROP_ACCENT_0 == pytest.approx(A0, abs=5e-4)
    assert dx.TOM_DROP_ACCENT_0_CONGA == pytest.approx(LAW["conga"]["A0"], abs=5e-4)
    assert dx.TOM_DROP_TUNING_G == pytest.approx(LAW["G"]["tom"], abs=5e-3)
    assert dx.TOM_DROP_TUNING_G_CONGA == pytest.approx(LAW["G"]["conga"], abs=5e-3)
    # TOM_DROP_RATIO is the law evaluated at the reference setting.
    assert dx.TOM_DROP_RATIO - 1.0 == pytest.approx(K * (1.0 - A0), abs=5e-4)
    # and the block's own function agrees with the fit's, away from the anchor
    for accent in (0.0, 0.5, 1.0, 1.4, 2.0):
        for f0 in (0.93 * 90.0, 90.0, 1.07 * 90.0):
            want = F.predict(accent, f0 / 90.0 - 1.0, K, A0, LAW["G"]["tom"])
            got = dx.tom_drop_excess(dx.M_LT, f0, accent)
            assert got == pytest.approx(want, abs=2e-3), (accent, f0, got, want)


def test_the_fit_refuses_when_the_measurement_is_missing():
    with pytest.raises(F.Refused):
        F.load_rows(REPO / "docs" / "no-such-measurement.json")


def test_the_fit_refuses_an_accent_law_that_shrinks_with_accent():
    """An unsatisfiable law is worse than none. If the levels ever come back
    ordered the other way, the fit must REFUSE rather than ship a negative
    slope that would make a hard hit sweep less than a soft one."""
    centre = {"A": {"mean": 0.24}, "B": {"mean": 0.14}, "C": {"mean": 0.05}}
    with pytest.raises(F.Refused):
        F.fit_accent(centre, F.ACCENT_MAP)


def test_the_conga_threshold_needs_the_level_it_is_fitted_to():
    with pytest.raises(F.Refused):
        F.fit_conga_threshold({"A": {"mean": 0.004}}, 0.18, F.ACCENT_MAP)


# -------------------------------------------------------- the comparator ----

def test_the_comparator_refuses_audio_the_index_does_not_vouch_for():
    """Three preconditions, each demonstrated red: a member that is not in the
    committed index, a member the index knows but the cache does not have, and
    a file whose size is not the size the index states."""
    sizes = C.indexed_sizes()
    assert len(sizes) > 100, "the committed index should list the whole pack"
    with pytest.raises(C.Refused):
        C.reference_wav("LT", "A", 99, sizes)                 # not indexed
    with pytest.raises(C.Refused):
        C.reference_wav("LT", "A", 6, dict(sizes, **{C.member("LT", "A", 6): 1}))  # wrong size
    real = C.member("LT", "A", 6)
    if not (C.CACHE / real).exists():
        with pytest.raises(C.Refused):
            C.reference_wav("LT", "A", 6, sizes)              # indexed, absent


def _repoint_the_index(tmp_path, monkeypatch) -> pathlib.Path:
    """Point the comparator at an index under `tmp_path`; nothing is written yet."""
    index = tmp_path / "index" / C.INDEX.name
    index.parent.mkdir()
    monkeypatch.setattr(C, "INDEX", index)
    return index


@pytest.mark.parametrize(("row", "field"), [
    ("folder/take.wav", "bytes"),
    ("folder/take.wav\t", "bytes"),
    ("\t12", "path"),
])
def test_the_comparator_refuses_an_incomplete_index_row(tmp_path, monkeypatch, capsys, row, field):
    """An index row with no `path` or no `bytes` vouches for nothing. The
    comparator used to carry its own reader of the index: it read the empty
    path as `{'': 12}`, and the other two rows escaped `main()`'s
    `except Refused` as a `ValueError` / `TypeError` traceback (#1551). The
    sizes are now read by `tools/refaudio_local.py`'s one reader, as
    `tom_drop_measure` reads them, and the row is a named REFUSED before
    anything is measured."""
    index = _repoint_the_index(tmp_path, monkeypatch)
    index.write_text(f"path\tbytes\n{row}\n", encoding="utf-8")
    with pytest.raises(C.Refused, match=f"missing {field}"):
        C.indexed_sizes()
    out = tmp_path / "result.json"
    previous = '{"sentinel": "the previous run"}'
    out.write_text(previous, encoding="utf-8")
    assert C.main(["--json", str(out)]) == 2
    err = capsys.readouterr().err
    assert "REFUSED" in err and str(index) in err and f"missing {field}" in err
    assert out.read_text(encoding="utf-8") == previous


def test_the_comparator_refuses_a_missing_index(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "INDEX", tmp_path / "no-index" / C.INDEX.name)
    with pytest.raises(C.Refused, match="is missing"):
        C.indexed_sizes()


def _a_directory_at_the_index(index: pathlib.Path) -> None:
    index.mkdir()


def _a_file_nobody_can_read(index: pathlib.Path) -> None:
    index.write_text("path\tbytes\nfolder/take.wav\t12\n", encoding="utf-8")
    index.chmod(0)


@pytest.mark.parametrize(("make_it", "why"), [
    (_a_directory_at_the_index, "IsADirectoryError"),
    (_a_file_nobody_can_read, "PermissionError"),
])
def test_the_comparator_refuses_an_index_it_cannot_read(tmp_path, monkeypatch, capsys, make_it, why):
    """An index that is there and cannot be read is an unavailable one: a named
    REFUSED and exit 2 that leaves the previous JSON alone, not an `OSError`
    traceback out of `main()` (#1551). Two ways a committed file is unreadable
    are put for real, not by patching the reader: something else sits at the
    path, and the permission is taken away."""
    index = _repoint_the_index(tmp_path, monkeypatch)
    make_it(index)
    try:
        if why == "PermissionError" and os.access(index, os.R_OK):
            pytest.skip("requires a user subject to file read permissions")
        with pytest.raises(C.Refused, match=why):
            C.indexed_sizes()
        out = tmp_path / "result.json"
        previous = '{"sentinel": "the previous run"}'
        out.write_text(previous, encoding="utf-8")
        assert C.main(["--json", str(out)]) == 2
        err = capsys.readouterr().err
        assert "REFUSED" in err and str(index) in err and why in err
        assert out.read_text(encoding="utf-8") == previous
    finally:
        if index.is_file():
            index.chmod(0o600)


def test_the_resample_control_refuses_a_conversion_that_filters_the_onset(monkeypatch):
    """`validate_resample` is the comparator's own injected-bug control and it
    runs on every comparison. The defect it exists to catch is a conversion that
    damages the SWEEP, so that is what is injected: a band-pass around the
    fundamental, which #110's own validation measured as costing 40 % of the
    excess to `filtfilt` pre-ringing landing on the first retained period.

    Note what does NOT work as an injection, because it says something about the
    estimator: a symmetric moving average is zero-phase and does not move the
    zero crossings of a sinusoid at all, so it changes the amplitude and not the
    measurement. The probe reads times, not levels."""
    from scipy.signal import resample_poly, butter, sosfiltfilt

    def filtered(x):
        y = resample_poly(np.asarray(x, dtype=np.float64), 147, 160)
        sos = butter(4, [0.75 * 90.0 / 22050.0, 2.05 * 90.0 / 22050.0],
                     btype="band", output="sos")
        return sosfiltfilt(sos, y)

    monkeypatch.setattr(C, "to_44100", filtered)
    with pytest.raises(C.Refused):
        C.validate_resample()


def test_a_uniform_rate_error_is_invisible_to_this_control_and_that_is_correct(monkeypatch):
    """Recorded because it is a real blind spot, not because it is a pass.

    Converting to the WRONG rate scales every frequency in the file by the same
    factor, and the measurement is a RATIO of two frequencies from that file, so
    the error cancels exactly. `validate_resample` therefore cannot see it, and
    no tolerance on the ratio ever could. What rules it out instead is that the
    conversion is a fixed 147/160 written once, and that `tom_pitch_probe`
    REFUSES any file whose sample rate is not 44100 -- a precondition, not a
    tolerance."""
    from scipy.signal import resample_poly
    monkeypatch.setattr(C, "to_44100", lambda x: resample_poly(np.asarray(x, float), 140, 160))
    r = C.validate_resample()                       # does NOT refuse
    assert max(c["rel_error_of_excess"] for c in r["cases"]) < 0.01
    assert C.P.SR_EXPECTED == 44100
    bad = P_measure_at_wrong_rate()
    assert bad["verdict"] == "REFUSED" and "sample rate" in bad["why"]


def P_measure_at_wrong_rate():
    import tom_pitch_probe as P
    y = P.synth_tom(90.0, 25.0, P.tau_from_q(90.0, 25.0), ratio=1.24, sr=44100, trim=False)
    return P.measure(y, 48000, label="wrong rate")


def test_the_resample_control_passes_the_conversion_actually_used():
    r = C.validate_resample()
    assert len(r["cases"]) == 3
    for c in r["cases"]:
        assert c["rel_error_of_excess"] < 0.01, c


def test_the_dead_tail_trim_touches_nothing_inside_the_ring():
    x = np.array([0.0, 0.3, -0.2, 0.1, 0.0, 0.0, 0.0])
    y = C.trim_dead_tail(x)
    assert len(y) == 4 and np.array_equal(y, x[:4])
    assert np.array_equal(C.trim_dead_tail(np.zeros(5)), np.zeros(5))


# ------------------------------------------------- the law's own extremes ----

def test_the_tuning_term_is_clamped_to_the_pot_it_was_measured_over():
    """Reference 1.7 puts the TUNING pot at +-10 %, which is also the span the
    99 files cover. A host may write any f0; the law must not extrapolate past
    the audio it came from."""
    inside = dx.tom_drop_excess(dx.M_LT, 90.0 * 1.10, 2.0)
    outside = dx.tom_drop_excess(dx.M_LT, 90.0 * 1.25, 2.0)
    assert outside == pytest.approx(inside, rel=1e-12)
    lo_in = dx.tom_drop_excess(dx.M_LT, 90.0 * 0.90, 2.0)
    assert dx.tom_drop_excess(dx.M_LT, 90.0 * 0.80, 2.0) == pytest.approx(lo_in, rel=1e-12)


def test_the_position_boundary_sits_between_the_two_sounds_not_inside_either():
    """The circuit's position is read off the tuning, so the boundary matters.
    It is the geometric mean of the pair -- 129 Hz for LT/LC -- and both sounds'
    whole pot ranges clear it: a tom detuned +25 % is 112 Hz and a conga
    detuned -25 % is 139 Hz. A host that writes something between them gets the
    nearer position, which is the only answer available."""
    for name, mode in (("LT", dx.M_LT), ("MT", dx.M_MT), ("HT", dx.M_HT)):
        conga = {"LT": "LC", "MT": "MC", "HT": "HC"}[name]
        f_tom, f_conga = dx.TOM_PRESET[name][0], dx.TOM_PRESET[conga][0]
        assert f_conga > 1.7 * f_tom, (name, f_tom, f_conga)
        for k in (0.75, 0.9, 1.0, 1.1, 1.25):
            assert dx.tom_position(mode, f_tom * k) == name, (name, k)
            assert dx.tom_position(mode, f_conga * k) == conga, (conga, k)


def test_a_mode_that_is_not_a_tom_circuit_gets_no_sweep():
    assert dx.tom_position(dx.M_BD, 49.4) is None
    assert dx.tom_drop_excess(dx.M_BD, 49.4, 2.0) == 0.0


def test_each_step_holds_the_intervals_mean_of_the_law():
    """The written coefficient sequence must be the interval-mean staircase,
    not the left-edge one. Checked against the closed form directly, because
    this is the defect that made the delivered sweep 22-38 % larger than the
    law it was delivering."""
    f0, q, amp = 90.0, 25.0, 0.2
    w = dx.tom_pitch_drop_writes(0, dx.M_LT, f0, q, amp, 2.0)
    s = dx.TOM_DROP_STEPS
    e = dx.tom_drop_excess(dx.M_LT, f0, 2.0)
    assert len(w) == 2 * (s + 1)
    for i in range(s + 1):
        a1, a2 = w[2 * i][2], w[2 * i + 1][2]
        got, _ = dx.poles_from_regs(a1, a2)
        shape = ((s / 3.0) * (math.exp(-3.0 * i / s) - math.exp(-3.0 * (i + 1) / s))
                 if i < s else math.exp(-3.0))
        assert got == pytest.approx(f0 * (1.0 + e * shape), rel=2e-3), i
    assert w[0][0] == 0
    assert w[-1][0] == int(round(dx.TOM_DROP_MS * 1e-3 * dx.SR))


# ------------------------------------------ the prose against the tree (#95) ----

def test_the_reference_documents_carry_the_measured_magnitude():
    """`tr808-reference.md` 4 and `drum-verification.md` 12 must state the
    magnitude, the accent threshold and the tuning slope that `drums_fx`
    actually ships -- and the per-accent table must be #110's own medians.

    THIS IS ISSUE #95's residual. #110 measured the drop and #154 shipped the
    correction, and for a week afterwards both reference documents still said
    x1.7: `tr808-reference.md` in the paragraph that ORIGINATED the inference,
    `drum-verification.md` 8.4 in a table that had upgraded it from *magnitude
    inferred* to *verified in a source*. Nothing caught that, because nothing
    was checking. This is the check."""
    ref = D.REFERENCE.read_text(encoding="utf-8")
    ver = D.VERIFICATION.read_text(encoding="utf-8")
    assert D.prose_disagreements(ref, ver) == []
    # and the refuted figure is still named as refuted, not quietly deleted:
    # a reader who arrives with x1.7 in hand has to be able to find out why.
    assert "REFUTED" in ver
    assert "x1.7" in ver.replace("×1.7", "x1.7")


def test_the_prose_check_refuses_a_document_with_no_law_in_it(monkeypatch):
    """The pre-#154 documents, verbatim. A document that does not state the law
    cannot agree with it, and REFUSED is the only honest verdict -- reporting
    OK for an absent claim is how a claim outlives its evidence.

    Run against the real files as they stood on `origin/main`, not a mock, so
    this test also demonstrates that the gate was RED before the change."""
    import subprocess
    try:
        old = subprocess.run(["git", "-C", str(REPO), "show",
                              "origin/main:docs/tr808-reference.md"],
                             capture_output=True, text=True, check=True).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        pytest.skip("no origin/main in this checkout")
    if "1.063" in old:
        pytest.skip("origin/main already carries the correction")
    with pytest.raises(D.Refused):
        D.stated_law(old)


def test_the_prose_check_goes_STALE_if_the_inferred_magnitude_is_restored():
    """The injected defect: put x1.7 back in the reference document's table and
    leave everything else alone. STALE, not REFUSED -- the evidence was found
    and it contradicts the prose."""
    ref = D.REFERENCE.read_text(encoding="utf-8")
    ver = D.VERIFICATION.read_text(encoding="utf-8")
    hurt = ref.replace("**×1.063**", "**×1.700**")
    assert hurt != ref, "the amendment table moved; this control no longer injects anything"
    bad = D.prose_disagreements(hurt, ver)
    assert any("x1.7" in b for b in bad), bad


def test_the_prose_check_goes_STALE_if_the_shipped_constant_moves(monkeypatch):
    """The other direction, which is the one that will actually happen: the code
    changes and the prose does not. A future refit must turn this red."""
    monkeypatch.setattr(dx, "TOM_DROP_RATIO", 1.200)
    monkeypatch.setattr(dx, "TOM_DROP_ACCENT_0", 0.400)
    bad = D.prose_disagreements(D.REFERENCE.read_text(encoding="utf-8"),
                                D.VERIFICATION.read_text(encoding="utf-8"))
    assert any("TOM_DROP_RATIO" in b for b in bad), bad
    assert any("A0 (tom)" in b for b in bad), bad


# --------------------------------------------- the congas re-examined (#95) ----

def test_the_congas_carry_neither_the_clamp_nor_the_missing_tuning_term():
    """#95's last question: the congas share the toms' circuit at about half the
    drop, so do they share the two defects the toms had?

    They do not. Against #110's 63 conga files, each placed on the MODEL's pot
    (chart f0 scaled by that row's own u -- centre to centre, see
    `tom_drop_docs`), the shipped law is within 0.0055 of excess on every cell's
    median and 0.0226 at worst. 0.005 of excess is under 1 Hz at LC and under
    2 Hz at HC, which is the board's own `pitch_drop_hz` floor.

    The *No Accent* cells read shipped 0 against a measured 0.003-0.005 -- the
    conga threshold A0 = 1.064 sits above an unaccented hit, and the machine's
    unaccented congas sit at the measurement floor. That agreement is the
    threshold working, and it is exactly what the old clamp destroyed."""
    r = D.conga_verdict()
    assert r["verdict"] == "PASS", r["failing_cells"]
    assert set(r["cells"]) == {"LC A", "LC B", "MC A", "MC B", "HC A", "HC B"}
    assert r["max_abs_median"] < D.CONGA_MEDIAN_MAX
    assert r["worst"] < D.CONGA_WORST_MAX


def test_the_conga_check_fires_when_the_accent_clamp_is_restored():
    """Injected defect 1: the pre-#154 clamp, at the CORRECTED magnitude, so
    this isolates the clamp from the constant. An unaccented conga then gets
    the full 0.060 where the machine does 0.004, and the No Accent cells' median
    error goes to +0.055 -- five times the bound."""
    def clamped(mode, f0_hz, accent):
        if dx.tom_position(mode, f0_hz) is None:
            return 0.0
        return (dx.TOM_DROP_RATIO - 1.0) * min(max(accent, 0.0), 1.0)

    r = D.conga_verdict(clamped)
    assert r["verdict"] == "FAIL"
    assert r["max_abs_median"] > 5 * D.CONGA_MEDIAN_MAX, r["max_abs_median"]
    assert {"LC A", "MC A", "HC A"} <= set(r["failing_cells"]), r["failing_cells"]


def test_the_conga_check_fires_when_the_conga_is_given_the_toms_tuning_slope():
    """Injected defect 2: G pooled across both positions, i.e. the conga run on
    the tom's 3.58 instead of its own 7.46.

    NOTE WHICH BOUND CATCHES IT, because it is a design point and not an
    accident. At the pot CENTRE the two slopes agree exactly -- exp(G*0) = 1 for
    any G -- so a tuning fault is invisible to a centre-only comparison and
    invisible to the median. It shows only at the pot's ends, which is why this
    check reads every file at its own u and bounds the WORST row as well as the
    median. A centre-only version of this test would have been a false green."""
    def pooled_g(mode, f0_hz, accent):
        name = dx.tom_position(mode, f0_hz)
        if name is None:
            return 0.0
        conga = name in D.CONGAS
        a0 = dx.TOM_DROP_ACCENT_0_CONGA if conga else dx.TOM_DROP_ACCENT_0
        drive = max(0.0, accent - a0) / (1.0 - dx.TOM_DROP_ACCENT_0)
        if drive <= 0.0:
            return 0.0
        u = f0_hz / dx.TOM_PRESET[name][0] - 1.0
        u = min(max(u, -dx.TOM_DROP_TUNING_SPAN), dx.TOM_DROP_TUNING_SPAN)
        return (dx.TOM_DROP_RATIO - 1.0) * drive * math.exp(dx.TOM_DROP_TUNING_G * u)

    clean = D.conga_verdict()
    hurt = D.conga_verdict(pooled_g)
    assert hurt["verdict"] == "FAIL", hurt
    assert hurt["worst"] > 2 * clean["worst"], (clean["worst"], hurt["worst"])
    # the medians barely move: that is the blind spot this control documents
    assert hurt["max_abs_median"] == pytest.approx(clean["max_abs_median"], abs=2e-3)


def test_the_conga_re_examination_refuses_a_voice_it_has_no_files_for():
    with pytest.raises(D.Refused):
        D.conga_cell_errors(voices=("XX",))


# --------------------------------------------- the --json destination (#2097) ----
#
# THE DEFECT, red-first on main @ 8eb04e5b (2026-11-28): the fit, the prose
# check and the comparator each wrote their `--json` after the work was done,
# with `write_text`, and nothing made the directory. Under a missing directory
# that is a `FileNotFoundError` out of `main`: exit 1 and a traceback -- a
# reader takes it for a failed check -- with the record lost.
#
# WHAT RUNS FOR REAL: `tom_drop_fit.main` and `tom_drop_docs.main` over the
# committed measurement and documents; both are cheap and need nothing fetched.
# `tom_drop_compare` reads the licensed 808 From Mars cache, so its `run` is
# replaced by a result of its own shape and `main` is what is tested.
#
# The input that defeats a `mkdir` guard is carried on purpose: a symlink at
# the destination whose target directory is gone. Its parent exists and it is
# not a directory, so the pre-flight passes and only the write fails -- for
# every user, root included, where a read-only directory would not.

BAD_JSON = ["a-directory", "a-file-as-parent"]


def _not_a_file(tmp_path: pathlib.Path, kind: str) -> pathlib.Path:
    """A `--json` that can never be a file: a directory, or a path under one."""
    if kind == "a-directory":
        dest = tmp_path / "result.json"
        dest.mkdir()
    else:
        (tmp_path / "as-parent").write_text("not a directory", encoding="utf-8")
        dest = tmp_path / "as-parent" / "result.json"
    return dest


def _a_dangling_link(dest: pathlib.Path) -> None:
    """The `--json` path itself is a symlink to a directory that does not exist."""
    dest.symlink_to(dest.parent / "gone" / dest.name)


def _nothing_run(*_a, **_k):
    raise AssertionError("the work ran before --json was checked")


# ---------------------------------------------------------------- the fit ----
def test_the_fit_writes_its_json_under_a_directory_that_did_not_exist(tmp_path):
    out = tmp_path / "fits" / "tom-drop" / "law.json"
    assert F.main(["--json", str(out)]) == 0
    law = json.loads(out.read_text(encoding="utf-8"))
    assert law["tom"]["K"] == pytest.approx(LAW["tom"]["K"], rel=1e-9)    # the record is the fit's
    assert set(law["holdouts"]) == {"accent_B_unseen", "tuning_even_unseen",
                                    "voice_MT_unseen", "pot_ends_unseen"}


@pytest.mark.parametrize("kind", BAD_JSON)
def test_the_fit_refuses_a_json_that_cannot_be_a_file_before_the_fit_runs(
        tmp_path, monkeypatch, capsys, kind):
    dest = _not_a_file(tmp_path, kind)
    monkeypatch.setattr(F, "run", _nothing_run)
    assert F.main(["--json", str(dest)]) == 2
    err = capsys.readouterr().err                        # the fit's refusals go to stderr
    assert "REFUSED" in err and str(dest) in err, err


def test_a_fit_json_that_cannot_be_written_after_the_fit_is_refused(tmp_path, capsys):
    dest = tmp_path / "law.json"
    _a_dangling_link(dest)
    assert F.main(["--json", str(dest)]) == 2
    out, err = capsys.readouterr()
    assert "HELD OUT" in out                             # the report was printed first
    assert "REFUSED" in err and str(dest) in err, err
    assert not dest.exists()


# ---------------------------------------------------------- the prose check ----
def test_the_prose_check_writes_its_json_under_a_directory_that_did_not_exist(tmp_path):
    """With and without `--json` the exit is the check's own verdict."""
    bare = D.main([])
    out = tmp_path / "checks" / "prose.json"
    assert D.main(["--json", str(out)]) == bare
    r = json.loads(out.read_text(encoding="utf-8"))
    assert r["congas"]["verdict"] in ("PASS", "FAIL") and "prose_disagreements" in r


@pytest.mark.parametrize("kind", BAD_JSON)
def test_the_prose_check_refuses_a_json_that_cannot_be_a_file_before_the_check_runs(
        tmp_path, monkeypatch, capsys, kind):
    dest = _not_a_file(tmp_path, kind)
    monkeypatch.setattr(D, "run", _nothing_run)
    assert D.main(["--json", str(dest)]) == 2
    out = capsys.readouterr().out                       # this one's refusals go to stdout
    assert "REFUSED" in out and str(dest) in out, out


def test_a_prose_json_that_cannot_be_written_after_the_check_is_refused(tmp_path, capsys):
    dest = tmp_path / "prose.json"
    _a_dangling_link(dest)
    assert D.main(["--json", str(dest)]) == 2
    out = capsys.readouterr().out
    assert "-> PASS" in out or "-> FAIL" in out           # the verdict was printed first
    assert "REFUSED" in out and str(dest) in out, out


def test_a_read_only_parent_is_refused_at_the_write_with_the_verdict_printed(tmp_path, capsys):
    """`parent.mkdir(exist_ok=True)` on a directory that is already there says
    nothing about permission, so a read-only parent passes the pre-flight and
    fails at the write: REFUSED (2) after the check, its verdict still printed.
    Needs a user subject to file permissions; root writes regardless."""
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        if os.access(locked, os.W_OK, effective_ids=True):
            pytest.skip("requires a user subject to file write permissions")
        dest = locked / "prose.json"
        assert D.main(["--json", str(dest)]) == 2
        out = capsys.readouterr().out
        assert "-> PASS" in out or "-> FAIL" in out
        assert "REFUSED" in out and str(dest) in out, out
        assert not dest.exists()
    finally:
        locked.chmod(0o700)


# ------------------------------------------------------------ the comparator ----
def _a_run_of_its_own_shape(stage: str) -> dict:
    """What `run` hands `main`, with no rows: the shape `main` prints and
    writes, without the licensed cache."""
    return {"stage": stage, "provenance": {"drums_fx_sha256": "0" * 16},
            "shipped_constants": {"TOM_DROP_RATIO": dx.TOM_DROP_RATIO},
            "resample_control": {"cases": []}, "rows": []}


def test_the_comparator_writes_its_json_under_a_directory_that_did_not_exist(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "run", _a_run_of_its_own_shape)
    out = tmp_path / "comparisons" / "before" / "rows.json"
    assert C.main(["--stage", "before", "--json", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["stage"] == "before"


@pytest.mark.parametrize("kind", BAD_JSON)
def test_the_comparator_refuses_a_json_that_cannot_be_a_file_before_the_comparison(
        tmp_path, monkeypatch, capsys, kind):
    dest = _not_a_file(tmp_path, kind)
    monkeypatch.setattr(C, "run", _nothing_run)
    assert C.main(["--json", str(dest)]) == 2
    err = capsys.readouterr().err                    # the comparator's refusals go to stderr
    assert "REFUSED" in err and str(dest) in err, err


def test_a_comparator_json_that_cannot_be_written_after_the_comparison_is_refused(
        tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(C, "run", _a_run_of_its_own_shape)
    dest = tmp_path / "rows.json"
    _a_dangling_link(dest)
    assert C.main(["--stage", "before", "--json", str(dest)]) == 2
    out, err = capsys.readouterr()
    assert "stage: before" in out                    # the report was printed first
    assert "REFUSED" in err and str(dest) in err, err
    assert not dest.exists()

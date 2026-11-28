#!/usr/bin/env python3
"""Ground truth for the reference-comparison harness, and its controls.

    .venv/bin/python -m pytest model/test_reference_compare.py -q     # ~2 min

Two jobs, in the order `docs/verification-rules.md` requires them:

  1. **Every estimator is checked against a closed-form signal** whose answer
     is known exactly, BEFORE any number it produces is quoted anywhere. Seven
     measurement claims in this repository were wrong from unvalidated
     methods; the withdrawn 700 Hz Hann-windowed split returned 1.25 % on a
     render whose true answer was exactly 18.55 %.

  2. **The comparison is shown to have power.** Three deliberately-wrong
     ladders -- a dropped pole, the linearised one-tanh structure DR 0001
     rejected, and a cutoff ROM read at the wrong frequency -- must each be
     separated from the real one BY THE SAME MEASUREMENT that is used on the
     references. If a broken model and ours both land inside the reference
     spread, the comparison proves nothing and these tests say so.

The plugin rigs are NOT exercised here: they need the VST3s and take minutes
per render. `model/reference_compare.py --stage validate` is their check, and
it cross-checks the two excitation methods against each other on Surge, where
both are possible.
"""
import math
import os
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "audition"))

import audio_measure as am                                          # noqa: E402
import reference_rigs as rr                                         # noqa: E402
import voice_fx as vf                                               # noqa: E402

SR = rr.SR


# =============================================================================
# 1. the estimators, against signals whose answer is known in closed form
# =============================================================================
def test_slope_recovers_an_exact_minus_24_db_per_octave_line():
    """A response that IS -24 dB/oct must measure -24.000, and the fit's
    residual must say it was a straight line."""
    f = np.geomspace(500.0, 8000.0, 24)
    g = -24.0 * np.log2(f / 500.0)
    e = am.slope_db_oct(f, g, (800.0, 6400.0))
    assert e.ok, e.reason
    assert abs(e.value + 24.0) < 1e-9, e.value
    assert e.detail["residual_db"] < 1e-9


def test_slope_refuses_a_curve_that_is_not_a_straight_line():
    """The resonant skirt is not a slope. A band that straddles the peak has
    to be refused, not averaged -- that is how a 'rolloff' of +7.6 dB/oct got
    reported once (docs/discrimination.md section 8)."""
    f = np.geomspace(100.0, 8000.0, 40)
    g = 20 * np.log10(1.0 / np.abs(1 - (f / 1000.0) ** 2 + 1j * f / (8 * 1000.0)))
    e = am.slope_db_oct(f, g, (300.0, 6000.0))
    assert not e.ok, f"accepted a fit with residual {e.detail.get('residual_db')}"


def test_corner_recovers_the_closed_form_of_four_cascaded_one_poles():
    """Four identical one-poles at f_p are -3 dB at f_p * sqrt(2**(1/4) - 1)
    = 0.434995 * f_p. Exactly, by algebra."""
    fp = 1000.0
    exact = fp * math.sqrt(2.0 ** 0.25 - 1.0)
    f = np.geomspace(20.0, 12000.0, 120)
    g = -4 * 10 * np.log10(1 + (f / fp) ** 2)
    e = am.corner_from_curve(f, g, ref_band=(20.0, 60.0))
    assert e.ok, e.reason
    assert abs(e.value - exact) / exact < 0.005, (e.value, exact)


def test_corner_refuses_a_response_that_never_falls_3_db():
    f = np.geomspace(20.0, 12000.0, 60)
    e = am.corner_from_curve(f, np.zeros_like(f), ref_band=(20.0, 60.0))
    assert not e.ok


def test_peak_recovers_the_closed_form_height_and_q_of_a_known_resonator():
    """For H = 1 / (1 - (f/f0)^2 + j f / (Q f0)) the peak sits at
    f0 * sqrt(1 - 1/(2 Q^2)) and stands 20 log10( Q / sqrt(1 - 1/(4 Q^2)) )
    over the DC plateau. Both are checked, and so is the -3 dB Q."""
    f0, Q = 1000.0, 6.0
    f = np.geomspace(100.0, 10000.0, 400)
    H = 1.0 / (1 - (f / f0) ** 2 + 1j * f / (Q * f0))
    g = 20 * np.log10(np.abs(H))
    exact_f = f0 * math.sqrt(1 - 1 / (2 * Q ** 2))
    # the estimator measures the peak over the PLATEAU it was given, which for
    # this resonator sits 0.17 dB over DC -- so the closed-form comparison is
    # against the closed-form plateau, not against DC.
    exact_plateau = float(np.median(g[(f >= 100.0) & (f <= 200.0)]))
    exact_h = 20 * math.log10(Q / math.sqrt(1 - 1 / (4 * Q ** 2))) - exact_plateau
    e = am.peak_from_curve(f, g, ref_band=(100.0, 200.0))
    assert e.ok, e.reason
    assert abs(e.value - exact_h) < 0.05, (e.value, exact_h)
    assert abs(e.detail["f_peak"] - exact_f) / exact_f < 0.01
    assert abs(e.detail["q"] - Q) / Q < 0.06, (e.detail["q"], Q)


def test_peak_refuses_a_monotonic_response():
    f = np.geomspace(20.0, 12000.0, 80)
    e = am.peak_from_curve(f, -4 * 10 * np.log10(1 + (f / 1000.0) ** 2), ref_band=(20.0, 60.0))
    assert not e.ok, "called the argmax of a monotonic curve a resonance"


@pytest.mark.parametrize("f0", [131.0, 258.0, 997.0])
def test_harmonic_signature_recovers_known_harmonic_levels(f0):
    """A closed-form odd series at -14, -38, -52 dB. This is the estimator
    every structural claim in the write-up rests on, so it is checked at three
    fundamentals and to a tenth of a dB."""
    want = {3: -14.0, 5: -38.0, 7: -52.0}
    n = int(1.0 * SR)
    t = np.arange(n) / SR
    x = np.sin(2 * math.pi * f0 * t)
    for k, dbv in want.items():
        x = x + 10 ** (dbv / 20.0) * np.sin(2 * math.pi * k * f0 * t + 0.3 * k)
    s = am.harmonic_signature(x, SR)
    assert abs(s["f0"] - f0) / f0 < 1e-4, s["f0"]
    for k, dbv in want.items():
        assert s[f"h{k}"] is not None, f"h{k} refused on a signal that has it"
        assert abs(s[f"h{k}"] - dbv) < 0.1, (k, s[f"h{k}"], dbv)
    assert s["h2"] is None, "reported an even harmonic in an odd-symmetric signal"


def test_harmonic_signature_reports_none_rather_than_the_noise_floor():
    """A pure sine under broadband noise has NO third harmonic. The estimator
    must say so, not hand back the noise in that bin as a level -- which is
    exactly the failure that would make two structures look alike."""
    rng = np.random.default_rng(7)
    n = int(1.0 * SR)
    x = np.sin(2 * math.pi * 300.0 * np.arange(n) / SR) + 1e-3 * rng.standard_normal(n)
    s = am.harmonic_signature(x, SR)
    assert s["h3"] is None, s["h3"]
    assert s["floor3"] is not None and s["floor3"] < -50.0, s["floor3"]


def test_harmonic_signature_refuses_harmonics_above_nyquist():
    n = int(0.5 * SR)
    x = np.sin(2 * math.pi * 6000.0 * np.arange(n) / SR)
    s = am.harmonic_signature(x, SR, f_lo=1000.0, f_hi=12000.0)
    assert s["h5"] is None and s["floor5"] is None


def test_the_stepped_tone_rig_agrees_with_the_acceptance_suite_probe():
    """`OurLadder.tone_gain_db` must return what
    `model/test_moog_acceptance.py`'s `probe_gain_db` returns: the harness
    compares the references against OUR filter as the rest of the repository
    already measures it, not against a second opinion about it."""
    from test_moog_acceptance import probe_gain_db
    ours = rr.OurLadder()
    freqs = np.array([100.0, 400.0, 1500.0, 4000.0])
    got = ours.tone_gain_db(freqs, 800.0, 0.5, 30000.0 / rr.FS_Q15)
    want = [probe_gain_db(f, 800, 0.5, 1.0, amp=30000) for f in freqs]
    assert np.allclose(got, want, atol=0.35), list(zip(freqs, got, want))


def test_the_variant_is_our_ladder_when_it_is_not_defective():
    """`_Variant(stages=4, nonlin='every')` must be `LadderFx` bit for bit.
    Every injected-defect control below measures the defect only if this
    holds; if it goes red the controls are measuring their own drift."""
    n = int(0.2 * SR)
    x = np.round(0.9 * rr.FS_Q15
                 * np.sin(2 * math.pi * 220.0 * np.arange(n) / SR)).astype(np.int16)
    real = rr.OurLadder()
    a = real._render(x, 3000, 0.8, drive=2.0)
    lad = rr._REAL_LADDER(**vf.LADDER_CFG)
    g, k, gain, ogain = real._regs(0.8, 3000, 2.0)
    b = lad.process(x, None, 0.8, 2.0, g_q16=np.full(n, g, dtype=np.int64),
                    k=k, gain=gain, ogain=ogain).astype(np.float64)
    assert np.array_equal(a, b)


# =============================================================================
# 2. start red: the comparison must be able to see a broken ladder
# =============================================================================
FREQS = np.geomspace(60.0, 9000.0, 26)


def _curve(rig, cut, res, amp=0.25):
    return rig.tone_gain_db(FREQS, cut, res, amp)


def test_a_dropped_pole_is_visible_in_the_measured_slope():
    """The injected defect for the 24 dB/oct claim. Two poles must measure
    near -12 dB/oct and four near -24, with no overlap: if the slope
    estimator cannot tell those apart it cannot adjudicate anything about the
    references either."""
    band = (700.0, 2400.0)   # 1.8 octaves of real stopband above a 300 Hz corner
    good = am.slope_db_oct(FREQS, _curve(rr.OurLadder(), 300.0, 0.3), band)
    bad = am.slope_db_oct(FREQS, _curve(rr.OurLadder("2-pole", stages=2), 300.0, 0.3), band)
    assert good.ok and bad.ok, (good.reason, bad.reason)
    assert good.value < -20.0, good.value
    assert bad.value > -16.0, bad.value
    assert good.value - bad.value < -6.0, (good.value, bad.value)


def test_a_skewed_cutoff_rom_moves_the_measured_corner_by_the_skew():
    """The injected defect for the cutoff-accuracy claim. A ladder whose g is
    looked up 30 % high must measure a corner about 30 % high -- so a real
    30 % cutoff error in any of the four filters could not hide."""
    ref = am.corner_from_curve(FREQS, _curve(rr.OurLadder(), 800.0, 0.2),
                               ref_band=(60.0, 160.0)).require("ours, corner")
    skew = am.corner_from_curve(FREQS, _curve(rr.OurLadder("skew", cut_skew=1.3), 800.0, 0.2),
                                ref_band=(60.0, 160.0)).require("skewed, corner")
    assert 1.18 < skew / ref < 1.45, (ref, skew, skew / ref)


def test_the_one_tanh_structure_is_separated_by_the_harmonic_signature():
    """The injected defect for the structural claim, and the reason the
    fingerprint is h5 relative to h3 rather than h2: the tanh is odd, so h2 is
    absent in BOTH candidate structures. Four linear poles with a single
    saturating element in the feedback must be far away from a tanh in every
    stage."""
    a = am.harmonic_signature(rr.OurLadder().ring(258.0, 1.3, seconds=1.0), SR)
    b = am.harmonic_signature(
        rr.OurLadder("1-tanh", nonlin="feedback").ring(258.0, 1.3, seconds=1.0), SR)
    assert a["h3"] is not None and b["h3"] is not None, (a, b)
    assert a["h5"] is not None, "ours: h5 under its own floor, no fingerprint to compare"
    sa = a["h5"] - a["h3"]
    sb = (b["h5"] - b["h3"]) if b["h5"] is not None else (b["floor5"] - b["h3"])
    assert sa - sb > 8.0, (sa, sb, a, b)
    # ...and the same probe at the TOP of the resonance range does not separate
    # them, because there the one-tanh control's hard input clip dominates. The
    # fingerprint is only usable at a stated resonance; that is a property of
    # the probe and it is reported as one.
    a2 = am.harmonic_signature(rr.OurLadder().ring(258.0, 2.0, seconds=1.0), SR)
    b2 = am.harmonic_signature(
        rr.OurLadder("1-tanh", nonlin="feedback").ring(258.0, 2.0, seconds=1.0), SR)
    near = abs((a2["h5"] - a2["h3"]) - (b2["h5"] - b2["h3"]))
    assert near < 8.0, ("the probe separates them at res 2.0 too; re-word the "
                        "write-up's caveat", near)


def test_a_silent_stub_is_refused_rather_than_measured():
    """The hardware-shaped failure: a rig that produces nothing must raise, not
    return a plausible number (verification rule 1)."""
    with pytest.raises(am.InsufficientEvidence):
        am.harmonic_signature(np.zeros(SR), SR)


# =============================================================================
# 3. the exit status when nothing answered (issue #1857)
# =============================================================================
# `main` returned 0 unconditionally, and `--report` returned whatever
# `report()` returns, which was 0. `guard` records a stage a rig cannot
# perform -- NotImplementedError -- as `not_answerable` and carries on to the
# next stage and the next device, so a run in which no stage answered wrote
# its files, printed "written to", and exited 0: PASS, a RESULT, having
# measured nothing. Reproduced on origin/main @ 528a228b (2026-11-28) for each
# guarded stage of a rig that answers none, and for two requests that ask
# nothing of anyone: `--devices ""`, and `--stage validate` -- offered by
# argparse and named by this module's docstring, dispatched by no branch of
# `main`.
#
# THE CONTROL is test_the_control_a_sweep_where_one_stage_answered: a sweep in
# which one stage answered is 0, its `not_answerable` records written beside
# it. The boundary of "answered" is the RIG, and it is stated rather than
# hidden: a stage whose rig rendered and whose estimator then refused every row
# (`ok: False`) has answered for this purpose and the run is 0. That is the
# estimator-refusal shape, out of scope on purpose (#1857 draws #1823's line
# here); test_a_rig_that_rendered_counts_as_answered pins it.
#
# Only the host boundary is stood in for (`build`). `main`, `guard`, the
# stages, the row writers and `report` are the real ones, and the answering
# rig renders the closed-form odd series section 1 grounds the estimator on,
# so the control is a real measurement through the real estimator.
#
# Red-first on that tree, 2026-11-28: 11 of the 17 controls here failed --
# ten with rc 0, and the all-not-answerable report through the crash --
# and the six controls and the stated boundary were green.
import json                                                        # noqa: E402

import reference_compare as rcmp                                   # noqa: E402

PASS, FAIL, REFUSED = 0, 1, 2      # tools/exit_contract.py, retyped so the oracle is not read off its subject
GUARDED = ("selfosc", "response", "drive", "peakdrive", "bigdrive")


class _Deaf:
    """A rig that cannot answer: every method a stage calls raises
    NotImplementedError, which `guard` records as `not_answerable`."""

    def __getattr__(self, name):
        def refuse(*a, **k):
            raise NotImplementedError(f"{name}: not answerable")
        return refuse


class _Ringing(_Deaf):
    """Answers `ring` with the closed-form odd series at 258 Hz -- h3, h5, h7
    at -14, -38, -52 dB -- and refuses every other stage."""

    def ring(self, cut, res, seconds=1.2, amp=0.25):
        n = int(seconds * rcmp.SR)
        t = np.arange(n) / rcmp.SR
        y = np.sin(2 * math.pi * 258.0 * t)
        for k, db in ((3, -14.0), (5, -38.0), (7, -52.0)):
            y = y + 10 ** (db / 20.0) * np.sin(2 * math.pi * k * 258.0 * t + 0.3 * k)
        return y


class _SilentRing(_Deaf):
    """Renders silence and refuses every other stage: the rig answered, and
    the estimator refused."""

    def ring(self, cut, res, seconds=1.2, amp=0.25):
        return np.zeros(int(seconds * rcmp.SR))


def _main(monkeypatch, tmp_path, capsys, argv, rigs):
    """(rc, stdout lines) of the real `rcmp.main` into tmp_path with `build`
    replaced: `rigs` is {device: rig class}."""
    monkeypatch.setattr(rcmp, "build", lambda name: rigs[name]())
    rc = rcmp.main([*argv, "--out", str(tmp_path)])
    return rc, capsys.readouterr().out.splitlines()


def _rows(tmp_path, stage, device):
    with open(tmp_path / f"{stage}-{device}.json") as f:
        return json.load(f)


def _verdicts(out):
    """The lines that BEGIN with `REFUSED:`. A stage's own `REFUSED <why>`
    sits mid-row and never begins a line, so it is not the run's verdict."""
    return [ln for ln in out if ln.startswith("REFUSED:")]


@pytest.mark.parametrize("stage", (*GUARDED, "validate"))
def test_a_request_no_stage_answered_exits_refused_not_a_pass(monkeypatch, tmp_path, capsys, stage):
    rc, out = _main(monkeypatch, tmp_path, capsys, ["--stage", stage, "--devices", "ours"],
                    {"ours": _Deaf})
    assert rc == REFUSED, (rc, out)
    assert any(ln.startswith("written to") for ln in out), "the files are written before the verdict"
    verdicts = _verdicts(out)
    assert len(verdicts) == 1 and out[-1] == verdicts[0], out
    if stage == "validate":
        assert sorted(p.name for p in tmp_path.iterdir()) == ["knobs.json"], "validate dispatches no stage"
        assert "0 of 0 requested stage(s) answered" in verdicts[0], verdicts[0]
    else:
        # `--stage response` asks two things of the device: the corner/slope
        # sweep and the resonant-peak sweep
        keys = {"response": ("response", "peak")}.get(stage, (stage,))
        rows = _rows(tmp_path, keys[0], "ours")
        assert len(rows) == 1 and rows[0]["ok"] is False and rows[0]["not_answerable"], \
            "the not_answerable record is still written: " + str(rows)
        assert f"0 of {len(keys)} requested stage(s) answered" in verdicts[0], verdicts[0]
        assert all(f"ours/{key}" in verdicts[0] for key in keys), verdicts[0]


def test_no_device_requested_is_refused(monkeypatch, tmp_path, capsys):
    """`--devices ""` asks nothing of anyone. Exit 0 there would be the same
    defect with no rig to blame."""
    rc, out = _main(monkeypatch, tmp_path, capsys, ["--stage", "selfosc", "--devices", ""], {})
    assert rc == REFUSED, (rc, out)
    assert "0 of 0 requested stage(s) answered" in _verdicts(out)[0], out


def test_every_device_not_answerable_is_refused_and_names_each(monkeypatch, tmp_path, capsys):
    devices = "ours,ours-1tanh,ours-2pole".split(",")
    rc, out = _main(monkeypatch, tmp_path, capsys, ["--stage", "selfosc", "--devices", ",".join(devices)],
                    dict.fromkeys(devices, _Deaf))
    assert rc == REFUSED, (rc, out)
    assert all("not_answerable" in _rows(tmp_path, "selfosc", d)[0] for d in devices)
    verdicts = _verdicts(out)
    assert len(verdicts) == 1 and out[-1] == verdicts[0], out
    assert "0 of 3 requested stage(s) answered" in verdicts[0], verdicts[0]
    assert all(f"{d}/selfosc" in verdicts[0] for d in devices), verdicts[0]


def test_the_control_a_sweep_where_one_stage_answered_still_passes(monkeypatch, tmp_path, capsys):
    """THE CONTROL. `ours` rings a closed-form series the real estimator
    measures; `ours-1tanh` cannot answer. One answered stage among refused
    ones is a result. Widening the rule to "any not_answerable" turns this
    red."""
    rc, out = _main(monkeypatch, tmp_path, capsys, ["--stage", "selfosc", "--devices", "ours,ours-1tanh"],
                    {"ours": _Ringing, "ours-1tanh": _Deaf})
    assert rc == PASS, (rc, out)
    answered = _rows(tmp_path, "selfosc", "ours")
    assert len(answered) == len(rcmp.res_grid("ours")), answered
    assert all(r["ok"] and abs(r["h3"] + 14.0) < 0.1 and abs(r["h5"] + 38.0) < 0.1 for r in answered), answered
    assert "not_answerable" in _rows(tmp_path, "selfosc", "ours-1tanh")[0]
    assert _verdicts(out) == [], out


def test_under_stage_all_one_answered_stage_among_refused_ones_still_passes(monkeypatch, tmp_path, capsys):
    rc, out = _main(monkeypatch, tmp_path, capsys, ["--stage", "all", "--devices", "ours"], {"ours": _Ringing})
    assert rc == PASS, (rc, out)
    written = {p.name[:-len("-ours.json")]: json.load(open(p)) for p in tmp_path.glob("*-ours.json")}
    assert set(written) == {"selfosc", "response", "peak", "tracking", "drive"}, sorted(written)
    assert all(r["ok"] for r in written["selfosc"]), written["selfosc"]
    assert all("not_answerable" in r for r in written["response"] + written["peak"] + written["drive"])
    assert _verdicts(out) == [], out


def test_a_rig_that_rendered_counts_as_answered_even_when_the_estimator_refuses(
        monkeypatch, tmp_path, capsys):
    """The boundary of "answered", and out of scope on purpose (#1857): the
    rig answered every ring, the REAL estimator found each render silent and
    refused. Those rows carry `ok: False` and a reason; they are measurements
    that could not answer, not a rig that was not here."""
    rc, out = _main(monkeypatch, tmp_path, capsys, ["--stage", "selfosc", "--devices", "ours"],
                    {"ours": _SilentRing})
    assert rc == PASS, (rc, out)
    rows = _rows(tmp_path, "selfosc", "ours")
    assert len(rows) == len(rcmp.res_grid("ours")), rows
    assert all(r["ok"] is False and r["why"] for r in rows), rows
    assert _verdicts(out) == [], out


# -- `--report`: the same question over a directory that was written before


def _report(tmp_path, capsys):
    rc = rcmp.main(["--report", "--out", str(tmp_path)])
    return rc, capsys.readouterr().out.splitlines()


@pytest.mark.parametrize("shape", ["absent", "nothing-requested", "all-not-answerable"])
def test_a_report_with_nothing_measured_in_it_is_refused_not_a_pass(tmp_path, capsys, shape):
    """Reproduced on 528a228b for the first two shapes: an empty report and
    exit 0. The third crashed instead -- `max()` over an empty table in
    section 1 -- which is the crash shape of #1783, not a PASS; the verdict
    now comes first. The same crash still takes a MIXED directory (one
    device measured, another's self-oscillation rows all refused) and is
    out of scope here; #1857's PR records it."""
    out_dir = tmp_path / "absent" if shape == "absent" else tmp_path / "run"
    if shape == "nothing-requested":
        out_dir.mkdir()
        (out_dir / "knobs.json").write_text("{}")
    elif shape == "all-not-answerable":
        out_dir.mkdir()
        (out_dir / "selfosc-ours.json").write_text(json.dumps(
            [dict(device="ours", ok=False, not_answerable="ring: not answerable")]))
    rc, out = _report(out_dir, capsys)
    assert rc == REFUSED, (rc, out)
    verdicts = _verdicts(out)
    assert len(verdicts) == 1 and out == verdicts, out


def test_the_control_a_report_over_a_directory_with_a_measurement_still_passes(
        monkeypatch, tmp_path, capsys):
    """THE CONTROL for `--report`: over the files a measured run wrote,
    the comparison prints and the status is 0. Widening the rule to "any
    not_answerable row" would refuse every directory a plugin-less host
    writes, and turn this red."""
    rc, _ = _main(monkeypatch, tmp_path, capsys, ["--stage", "selfosc", "--devices", "ours"],
                  {"ours": _Ringing})
    assert rc == PASS
    rc, out = _report(tmp_path, capsys)
    assert rc == PASS, (rc, out)
    assert _verdicts(out) == [], out


def test_the_control_a_report_passes_on_a_measurement_it_does_not_display(tmp_path, capsys):
    """`report()` prints nothing from the `drive` files. Something was
    measured all the same, so the status keys on what the directory holds,
    not on what the report shows."""
    (tmp_path / "drive-ours.json").write_text(json.dumps(
        [dict(device="ours", level_dbfs=-6.0, ok=True, h3=-20.0)]))
    rc, out = _report(tmp_path, capsys)
    assert rc == PASS, (rc, out)
    assert _verdicts(out) == [], out


# -- review of #1934: the filenames the writer produces, and a refused acquisition

class _NoCurve(_Deaf):
    """The rig is here and cannot acquire a curve: the response call raises
    InsufficientEvidence (what a silent Diva capture does)."""

    def tone_gain_db(self, *a, **k):
        raise rcmp.am.InsufficientEvidence("reference is silent in a requested bin")

    noise_curve = tone_gain_db


@pytest.mark.parametrize("device", sorted(rcmp.KIND))
def test_report_recognises_every_registered_device_filename(tmp_path, capsys, device):
    """Splitting at the last hyphen dropped seven registered devices."""
    (tmp_path / f"selfosc-{device}.json").write_text(json.dumps(
        [dict(device=device, ok=True, res=0.5, rms=0.1)]))
    assert rcmp._stage_files(str(tmp_path)) == [("selfosc", device)]


def test_report_ignores_a_json_file_that_is_not_a_stage_file(tmp_path, capsys):
    """CONTROL (defeats a filter that accepts any prefix): metadata only is REFUSED."""
    (tmp_path / "notes-ours.json").write_text(json.dumps([dict(note="metadata only")]))
    rc, out = _report(tmp_path, capsys)
    assert rc == REFUSED, (rc, out)


@pytest.mark.parametrize("stage", ("peakdrive", "response"))
def test_a_stage_whose_every_acquisition_was_refused_exits_refused(
        monkeypatch, tmp_path, capsys, stage):
    argv = ["--stage", stage, "--devices", "ours"]
    rc, out = _main(monkeypatch, tmp_path, capsys, argv, {"ours": _NoCurve})
    rows = _rows(tmp_path, stage, "ours")
    assert rows and all(r["ok"] is False for r in rows), rows
    assert rc == REFUSED, (rc, out)
    rc, out = _report(tmp_path, capsys)
    assert rc == REFUSED, (rc, out)


def test_a_mixed_run_with_one_acquired_curve_still_passes(monkeypatch, tmp_path, capsys):
    rows = [dict(device="ours", ok=False, why="silent", unacquired=True),
            dict(device="ours", res=0.5, freqs=[1.0], gain_db=[0.0], corner_hz=None)]
    assert rcmp._answered(rows)
    assert not rcmp._answered(rows[:1])

# =============================================================================
# 4. a device that did not answer must not take the run down (#1936)
# =============================================================================
# Three shapes #1857's closing search left in this file, each reproduced by
# RUNNING on that PR's head (d8e97813, 2026-11-28) before a line was changed:
#
#   * `report()` over a MIXED directory -- one device measured, another's rows
#     all refused -- crashed. The issue names section 1 (`max()` over an empty
#     `live`, a ValueError). The same directory crashed in four other printed
#     sections too, each on the row shape `guard` writes: `tracking` (KeyError
#     `want_hz`), `response` (a None cut_hz through a float format),
#     `peakdrive` (KeyError `res`), `bigdrive` (KeyError `level_dbfs`).
#   * `main` guarded five stages against a rig's NotImplementedError and left
#     three things outside: `build()`, the cutoff lookup `cut_setting_for()`
#     makes before any stage, and the whole `tracking` stage. A rig refusing
#     in any of them left as a traceback, after the stages before it had been
#     recorded and written nowhere.
#   * `_stage_files` split at the LAST hyphen, so `selfosc-ours-1tanh.json`
#     read as stage `selfosc-ours` of device `1tanh`. Seven of the ten device
#     names carry a hyphen and none of their files counted: a directory
#     holding nothing but a measured `ours-1tanh` refused, `0 of 0 stage
#     file(s)`. Found here and fixed here; PR #1934's own review then fixed
#     it independently (its `test_report_recognises_every_registered_device_filename`),
#     so on this branch the control below is green before the fix commit. It
#     stays: it is the end-to-end half, a measured directory through `report()`.
#
# WHAT IS OWED. A device that did not answer is NAMED, in the section where
# its row would have stood, with its reason, and the report goes on to the
# others. The status is still 0 when something else answered -- a partly
# refused directory is a result, #1934's boundary, unchanged -- which is
# exactly why the device must show in the text: the status alone cannot say
# it. A rig that cannot be built, cannot state a cutoff or cannot ring for
# `tracking` leaves every stage it was asked for `not_answerable`, recorded
# and written like the guarded stages.
#
# THE CONTROLS: an all-answered directory names no device as unanswered, and
# a sweep in which one stage answered among refused ones is still 0.
NOT_ANSWERABLE = "ring: not answerable"
MEASURED_SELFOSC = [dict(device="ours", res=1.3, ok=True, rms=0.5, f0=258.0,
                         h2=None, h3=-30.0, h5=-50.0, h7=-60.0)]
#: what `--stage all` asks of one device, and so the files a deaf rig leaves
ALL_KEYS = ("selfosc", "response", "peak", "tracking", "drive")


class _Unbuildable:
    """A rig whose construction raises NotImplementedError."""

    def __init__(self):
        raise NotImplementedError("Surge XT is not installed")


def _deaf_row(device="ours-1tanh"):
    """The one row `guard` writes for a stage the rig could not perform."""
    return [dict(device=device, ok=False, not_answerable=NOT_ANSWERABLE)]


def _named(out, device, label):
    """The report lines whose first token is exactly `device` and that carry
    `label`. A prefix would confuse `ours` with `ours-1tanh`."""
    return [ln for ln in out if ln.split()[:1] == [device] and label in ln]


def _assert_not_answerable(tmp_path, device, keys):
    for key in keys:
        rows = _rows(tmp_path, key, device)
        assert len(rows) == 1 and rows[0]["ok"] is False and rows[0]["not_answerable"], (key, rows)


def _ran_to_its_end(out) -> bool:
    """Section 6 is the report's last; a crash in any section before it
    means the line is absent."""
    return any("6. THE INJECTED DEFECTS" in ln for ln in out)


# -- `--report`: the mixed directory


def test_a_device_whose_rows_all_refused_is_named_and_the_report_goes_on(tmp_path, capsys):
    """THE MIXED DIRECTORY the issue names: `ours` measured, `ours-1tanh` all
    `not_answerable`. Reproduced: `ValueError: max() iterable argument is
    empty`, exit 1."""
    (tmp_path / "selfosc-ours.json").write_text(json.dumps(MEASURED_SELFOSC))
    (tmp_path / "selfosc-ours-1tanh.json").write_text(json.dumps(_deaf_row()))
    rc, out = _report(tmp_path, capsys)
    assert rc == PASS, (rc, out)
    named = _named(out, "ours-1tanh", "NOT ANSWERABLE")
    assert len(named) == 1 and NOT_ANSWERABLE in named[0], out
    measured = [ln for ln in out if ln.startswith("ours ") and "-30.0" in ln]
    assert len(measured) == 1, "the measured device's own row still prints: " + "\n".join(out)
    assert _ran_to_its_end(out), out
    assert _verdicts(out) == [], out


def test_a_device_whose_rows_the_estimator_all_refused_is_named_too(monkeypatch, tmp_path, capsys):
    """The other row shape that empties `live`, written by the real `main`:
    the rig rendered silence and the estimator refused every row. Reproduced:
    the same ValueError."""
    rc, _ = _main(monkeypatch, tmp_path, capsys, ["--stage", "selfosc", "--devices", "ours,ours-1tanh"],
                  {"ours": _Ringing, "ours-1tanh": _SilentRing})
    assert rc == PASS
    why = {r["why"] for r in _rows(tmp_path, "selfosc", "ours-1tanh")}
    rc, out = _report(tmp_path, capsys)
    assert rc == PASS, (rc, out)
    named = _named(out, "ours-1tanh", "NO ROW ANSWERED")
    assert len(named) == 1 and all(w in named[0] for w in why), out
    assert _ran_to_its_end(out), out


@pytest.mark.parametrize("stage,crash", [("tracking", "KeyError: 'want_hz'"),
                                         ("response", "TypeError: unsupported format string"),
                                         ("peakdrive", "KeyError: 'res'"),
                                         ("bigdrive", "KeyError: 'level_dbfs'")])
def test_the_same_device_in_the_other_printed_stages_is_named_too(tmp_path, capsys, stage, crash):
    """The same-shape search: the issue names section 1, and each other printed
    section took the same directory down its own way. Reproduced: `crash`."""
    (tmp_path / "selfosc-ours.json").write_text(json.dumps(MEASURED_SELFOSC))
    (tmp_path / f"{stage}-ours-1tanh.json").write_text(json.dumps(_deaf_row()))
    rc, out = _report(tmp_path, capsys)
    assert rc == PASS, (rc, out)
    named = _named(out, "ours-1tanh", "NOT ANSWERABLE")
    assert len(named) == 1 and NOT_ANSWERABLE in named[0], out
    assert _ran_to_its_end(out), out
    assert _verdicts(out) == [], out


def test_a_measured_file_of_a_device_whose_name_carries_a_hyphen_counts(tmp_path, capsys):
    """Red on the #1934 head this was written against (d8e97813: rc 2, `0 of 0
    stage file(s)`). Green since that PR's review fixed `_stage_files`."""
    (tmp_path / "selfosc-ours-1tanh.json").write_text(json.dumps(
        [dict(MEASURED_SELFOSC[0], device="ours-1tanh")]))
    rc, out = _report(tmp_path, capsys)
    assert rc == PASS, (rc, out)
    assert _verdicts(out) == [], out
    assert len(_named(out, "ours-1tanh", "-30.0")) == 1, out


def test_the_control_an_all_answered_directory_names_no_device_as_unanswered(monkeypatch, tmp_path, capsys):
    """THE CONTROL for the report: over a directory in which every row
    answered, no device is named as unanswered. Widening the rule to "name any
    device that has a file" turns this red. (The legend explains both labels,
    so the lines checked are the devices' own.)"""
    rc, _ = _main(monkeypatch, tmp_path, capsys, ["--stage", "selfosc", "--devices", "ours,ours-1tanh"],
                  {"ours": _Ringing, "ours-1tanh": _Ringing})
    assert rc == PASS
    rc, out = _report(tmp_path, capsys)
    assert rc == PASS, (rc, out)
    assert not [ln for d in rcmp.ORDER for ln in out
                if ln.strip().startswith(d) and ("NOT ANSWERABLE" in ln or "NO ROW ANSWERED" in ln)], out
    assert len(_named(out, "ours", "-14.0")) == 1 and len(_named(out, "ours-1tanh", "-14.0")) == 1, out


# -- a run: the three places outside `guard`


def test_under_stage_all_a_rig_that_cannot_ring_leaves_every_requested_stage_not_answerable(
        monkeypatch, tmp_path, capsys):
    """The `tracking` stage sat outside `guard`. Reproduced under `--stage
    all` with a rig that answers nothing: NotImplementedError from `ring`,
    a traceback, after four stages had been recorded and written nowhere."""
    rc, out = _main(monkeypatch, tmp_path, capsys, ["--stage", "all", "--devices", "ours"], {"ours": _Deaf})
    assert rc == REFUSED, (rc, out)
    _assert_not_answerable(tmp_path, "ours", ALL_KEYS)
    verdicts = _verdicts(out)
    assert len(verdicts) == 1 and out[-1] == verdicts[0], out
    assert "0 of 5 requested stage(s) answered" in verdicts[0], verdicts[0]
    assert all(f"ours/{key}" in verdicts[0] for key in ALL_KEYS), verdicts[0]
    assert sum("NOT ANSWERABLE" in ln for ln in out) == len(ALL_KEYS), out


def test_a_rig_that_cannot_state_a_cutoff_leaves_every_requested_stage_not_answerable(
        monkeypatch, tmp_path, capsys):
    """`cut_setting_for` calls `set_point` on a Surge rig before any stage.
    Reproduced under `--devices ours,surge-rk`: a traceback."""
    rc, out = _main(monkeypatch, tmp_path, capsys, ["--stage", "all", "--devices", "ours,surge-rk"],
                    {"ours": _Deaf, "surge-rk": _Deaf})
    assert rc == REFUSED, (rc, out)
    _assert_not_answerable(tmp_path, "ours", ALL_KEYS)
    _assert_not_answerable(tmp_path, "surge-rk", ALL_KEYS)
    verdicts = _verdicts(out)
    assert len(verdicts) == 1 and out[-1] == verdicts[0], out
    assert "0 of 10 requested stage(s) answered" in verdicts[0], verdicts[0]
    assert all(f"{device}/{key}" in verdicts[0] for device in ("ours", "surge-rk")
               for key in ALL_KEYS), verdicts[0]


@pytest.mark.parametrize("deaf", [False, True], ids=["one-device-answered", "nothing-answered"])
def test_a_rig_that_cannot_be_built_is_not_answerable_for_every_requested_stage(
        monkeypatch, tmp_path, capsys, deaf):
    """`build()` sat outside `guard`. Reproduced: a traceback before the device
    had a stage to its name. THE CONTROL is the first parameter: `ours`
    answers two stages beside it, and the run is still 0."""
    rc, out = _main(monkeypatch, tmp_path, capsys, ["--stage", "all", "--devices", "ours,surge-rk"],
                    {"ours": _Deaf if deaf else _Ringing, "surge-rk": _Unbuildable})
    assert rc == (REFUSED if deaf else PASS), (rc, out)
    _assert_not_answerable(tmp_path, "surge-rk", ALL_KEYS)
    assert all("Surge XT is not installed" in _rows(tmp_path, key, "surge-rk")[0]["not_answerable"]
               for key in ALL_KEYS)
    assert sorted(p.name for p in tmp_path.glob("*-surge-rk.json")) == \
        sorted(f"{key}-surge-rk.json" for key in ALL_KEYS)
    assert sum(ln.startswith("  surge-rk:") and "NOT ANSWERABLE" in ln for ln in out) == len(ALL_KEYS), out
    verdicts = _verdicts(out)
    if deaf:
        assert len(verdicts) == 1 and "0 of 10 requested stage(s) answered" in verdicts[0], out
    else:
        assert verdicts == [], out


def test_under_stage_all_a_deaf_rig_leaves_a_directory_the_report_can_read(monkeypatch, tmp_path, capsys):
    """Both repairs end to end, on the shape a host without a plugin writes:
    `ours` answers two stages and refuses three, `ours-1tanh` refuses all
    five. Reproduced: the run itself was the traceback."""
    rc, _ = _main(monkeypatch, tmp_path, capsys, ["--stage", "all", "--devices", "ours,ours-1tanh"],
                  {"ours": _Ringing, "ours-1tanh": _Deaf})
    assert rc == PASS
    rc, out = _report(tmp_path, capsys)
    assert rc == PASS, (rc, out)
    # selfosc, tracking and response are printed and name it; `peak` and
    # `drive` are written and no section prints them
    assert len(_named(out, "ours-1tanh", "NOT ANSWERABLE")) == 3, out
    assert _ran_to_its_end(out), out

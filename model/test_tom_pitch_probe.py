#!/usr/bin/env python3
"""The instrument gate for `tom_pitch_probe`.

An f0 tracker on a decaying, noisy, pitch-swept signal is exactly the kind of
estimator that has produced wrong numbers in this repository. So before the
probe is pointed at a recording whose drop is unknown, it is pointed at
synthetic toms whose drop is **known**, and the error is stated.

Every synthetic here is a real 2-pole resonator kicked by a 1 ms pulse, with a
dither floor, 24-bit quantisation and the same hard onset trim the recordings
have -- so these tests exercise the actual failure modes (the excitation
transient, the floor, a competing line), not a clean sinusoid.

    python model/test_tom_pitch_probe.py        # standalone, prints the table
    pytest model/test_tom_pitch_probe.py
"""
from __future__ import annotations
import math, sys, pathlib
import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import tom_pitch_probe as P
import audio_measure as am

SR = 44100
VOICES = [("LT", 90.0, 25.0), ("MT", 135.0, 24.0), ("HT", 185.0, 25.0)]

# Tolerances this gate asserts. They are the numbers the probe's results must
# be quoted with; nothing here is tuned to make a case pass.
# The worst corner is LT with a 25 ms drop: tau = 8.3 ms is SHORTER THAN ONE
# PERIOD of the 90 Hz carrier, so the relaxation is essentially over before the
# estimator gets its first full-period sample. 8 % is what the probe can do
# there; over the regime the recordings actually occupy (tau ~ 20-25 ms) it is
# better than 2 %. The per-case table is printed, so nothing hides inside the
# bound.
TOL_EXCESS_REL = 0.08     # error on (R - 1), relative
TOL_TAU_REL = 0.12        # error on the relaxation time constant, relative
TOL_NULL = 0.005          # a null must come back inside x1.005


def _synth(nm, f0, q, **kw):
    y = P.synth_tom(f0, q, P.tau_from_q(f0, q), sr=SR, trim=False, **kw)
    i0 = P.onset_index(y, 0.01)
    return y[max(0, i0 - 4):]


def _measure(nm, f0, q, **kw):
    return P.measure(_synth(nm, f0, q, **kw), SR, label=nm, band=kw.pop("band", False))


# ----------------------------------------------------------- recovery -------

def test_recovers_the_contracts_own_drop():
    """x1.7 over 60 ms, exp(-3t/T): the sequence 15.7.1 actually ships."""
    worst = 0.0
    for nm, f0, q in VOICES:
        r = _measure(nm, f0, q, ratio=1.7, drop_ms=60.0, shape="exp", seed=1)
        assert r["verdict"] == "OK", (nm, r.get("why"))
        e = abs((r["ratio_at_onset"] - 1.7) / 0.7)
        worst = max(worst, e)
        assert e < TOL_EXCESS_REL, (nm, r["ratio_at_onset"])
        assert abs(r["fit_tau_ms"] - 20.0) / 20.0 < TOL_TAU_REL, (nm, r["fit_tau_ms"])
    return worst


def test_recovers_small_drops(table: list | None = None):
    """The interesting range if the shipped 1.7 is wrong."""
    worst = worst60 = 0.0
    for nm, f0, q in VOICES:
        for R in (1.05, 1.10, 1.20, 1.40):
            for dms in (25.0, 60.0):
                r = _measure(nm, f0, q, ratio=R, drop_ms=dms, shape="exp", seed=2)
                assert r["verdict"] == "OK", (nm, R, dms, r.get("why"))
                e = abs((r["ratio_at_onset"] - R) / (R - 1.0))
                et = abs(r["fit_tau_ms"] - dms / 3.0) / (dms / 3.0)
                worst = max(worst, e)
                if dms >= 60.0:
                    worst60 = max(worst60, e)
                if table is not None:
                    table.append((nm, R, dms, r["ratio_at_onset"], r["fit_tau_ms"], e, et))
                assert e < TOL_EXCESS_REL, (nm, R, dms, r["ratio_at_onset"])
                assert et < TOL_TAU_REL
    return worst, worst60


def test_invents_no_drop_on_a_null():
    """THE control that matters: a tom with NO pitch drop must not grow one."""
    worst = 0.0
    for nm, f0, q in VOICES:
        for seed in (3, 4, 5):
            r = _measure(nm, f0, q, shape="none", seed=seed)
            assert r["verdict"] in ("NO-DROP-ABOVE-FLOOR", "OK"), (nm, r["verdict"])
            worst = max(worst, abs(r["ratio_at_onset"] - 1.0))
            assert abs(r["ratio_at_onset"] - 1.0) < TOL_NULL, (nm, seed, r["ratio_at_onset"])
            assert r["verdict"] == "NO-DROP-ABOVE-FLOOR", (nm, seed, r["excess_sigma"])
    return worst


def test_distinguishes_exponential_from_linear():
    """The contract ships a SHAPE, so the probe must be able to read one."""
    for nm, f0, q in VOICES:
        for R in (1.2, 1.7):
            e = _measure(nm, f0, q, ratio=R, drop_ms=60.0, shape="exp", seed=6)
            ln = _measure(nm, f0, q, ratio=R, drop_ms=60.0, shape="lin", seed=6)
            assert e["fit_shape"] == "exponential", (nm, R, e["fit_shape"])
            assert ln["fit_shape"] == "linear", (nm, R, ln["fit_shape"])


def test_a_noise_rumble_does_not_fake_a_drop():
    """The toms carry a pink-noise path; in the recordings it sits 30-43 dB
    under the tone. At -20 dB -- worse than anything measured -- it still must
    not manufacture a drop."""
    for nm, f0, q in VOICES:
        y = _synth(nm, f0, q, shape="none", seed=3)
        rng = np.random.default_rng(11)
        n = len(y)
        w = rng.normal(0, 1, n)
        a = math.exp(-2 * math.pi * 400.0 / SR)
        o = np.empty(n); z = 0.0
        for i in range(n):
            z = a * z + (1 - a) * w[i]; o[i] = z
        o *= np.exp(-np.arange(n) / (0.085 * SR))
        y = y + o / np.abs(o).max() * np.abs(y).max() * 10 ** (-20 / 20)
        r = P.measure(y, SR, label=nm)
        if r["verdict"] == "REFUSED":
            continue                      # refusing is an acceptable answer
        assert r["ratio_at_onset"] < 1.05, (nm, r["ratio_at_onset"])


def test_a_coherent_neighbour_is_survived_or_refused():
    """A coherent line near the fundamental is this estimator's worst confound:
    before the extrapolation guard existed, a -25 dB companion at 0.5 x f0
    made a drop-free tom read x1.35. The guard (a fitted onset excess more than
    EXTRAP_MAX times the largest OBSERVED excess, or a tau below one period of
    the carrier, is not a measurement) now refuses those.

    This test does not assert the confound is gone -- it asserts the probe
    either refuses or stays inside the tolerance the results are quoted with,
    and it returns what actually happened so the bound is re-derived rather
    than assumed."""
    outcomes = []
    for nm, f0, q in VOICES:
        for rel, mult in ((-25, 0.5), (-30, 0.5), (-25, 1.35), (-30, 2.7)):
            y = _synth(nm, f0, q, shape="none", seed=3)
            t = np.arange(len(y)) / SR
            m = (np.cos(2 * np.pi * mult * f0 * t + 0.7)
                 * np.exp(-t / (0.6 * P.tau_from_q(f0, q))))
            y = y + m / np.abs(m).max() * np.abs(y).max() * 10 ** (rel / 20)
            r = P.measure(y, SR, label=nm)
            got = r.get("ratio_at_onset") if r["verdict"] == "OK" else None
            outcomes.append((nm, rel, mult, r["verdict"], got, r.get("neighbour_db")))
            assert got is None or abs(got - 1.0) <= 0.05, (nm, rel, mult, got)
    return outcomes


def test_band_limiting_is_worse_and_is_therefore_off():
    """Recorded because it is the opposite of the intuition: band-limiting
    around the fundamental costs a large fraction of the excess (filtfilt
    pre-ringing lands on the first retained period) and biases the null low."""
    bp_err = raw_err = 0.0
    bp_null = raw_null = 0.0
    for nm, f0, q in VOICES:
        y = _synth(nm, f0, q, ratio=1.2, drop_ms=25.0, shape="exp", seed=1)
        a = P.measure(y.copy(), SR, label=nm, band=True)
        b = P.measure(y.copy(), SR, label=nm, band=False)
        bp_err = max(bp_err, abs(a["ratio_at_onset"] - 1.2) / 0.2)
        raw_err = max(raw_err, abs(b["ratio_at_onset"] - 1.2) / 0.2)
        z = _synth(nm, f0, q, shape="none", seed=3)
        bp_null = max(bp_null, abs(P.measure(z.copy(), SR, band=True)["ratio_at_onset"] - 1))
        raw_null = max(raw_null, abs(P.measure(z.copy(), SR, band=False)["ratio_at_onset"] - 1))
    assert raw_err < bp_err and raw_null < bp_null, (bp_err, raw_err, bp_null, raw_null)
    return bp_err, raw_err, bp_null, raw_null


# ----------------------------------------------------------- refusals -------

def test_refuses_what_it_cannot_measure():
    f0, q = 135.0, 24.0
    silent = np.zeros(SR // 2)
    assert P.measure(silent, SR)["verdict"] == "REFUSED"
    y = _synth("MT", f0, q, ratio=1.2, shape="exp")
    assert P.measure(y, 48000)["verdict"] == "REFUSED"          # wrong rate
    clipped = np.clip(y * 6.0, -1.0, 1.0)
    assert P.measure(clipped, SR)["verdict"] == "REFUSED"        # level-crushed
    assert P.measure(y[:200], SR)["verdict"] == "REFUSED"        # too short


def test_pitch_drop_domain_is_inspectable_without_synthesizing_a_signal():
    """#115: the third of the three ad-hoc validation measurements the issue
    names (0.41 %) relocated into `PITCH_DROP_DOMAIN`, as data a test can
    assert against directly rather than a docstring paragraph or a row of
    `docs/tom-pitch-drop-measurement.md`. `measure()`'s own gates
    (`MIN_SETTLED`, `SETTLED_XCHECK`) are the source of truth -- the
    declaration must match them, not restate them independently."""
    snr = P.PITCH_DROP_DOMAIN.axis(am.AXIS_SNR)
    assert snr.lo == P.SETTLED_HEADROOM_DB
    record_length = P.PITCH_DROP_DOMAIN.axis(am.AXIS_RECORD_LENGTH)
    assert record_length.lo == float(P.MIN_SETTLED)
    detuning = P.PITCH_DROP_DOMAIN.axis(am.AXIS_DETUNING)
    assert detuning.hi == P.SETTLED_XCHECK
    assert am.DOMAINS["tom_pitch_probe.measure"] is P.PITCH_DROP_DOMAIN
    assert "0.41" in P.PITCH_DROP_DOMAIN.worst_error


def main() -> int:
    bad = 0
    w1 = test_recovers_the_contracts_own_drop()
    print(f"  recovers the contract's own x1.7 / 60 ms          worst excess error {100*w1:.2f} %")
    tbl: list = []
    w2, w2_60 = test_recovers_small_drops(tbl)
    print(f"  recovers x1.05 .. x1.40 over 25 and 60 ms         worst excess error {100*w2:.2f} %"
          f"  (60 ms drops only: {100*w2_60:.2f} %)")
    print("      voice  trueR  dropms   readR   tau_ms   excess err   tau err")
    for nm, R, dms, rr, tm, e, et in tbl:
        print(f"      {nm:5s}  {R:5.2f}  {dms:6.0f}  {rr:6.4f}  {tm:7.2f}   {100*e:8.2f} %  {100*et:6.2f} %")
    w3 = test_invents_no_drop_on_a_null()
    print(f"  invents no drop on a null (R = 1.000)             worst |R-1| {w3:.5f}")
    test_distinguishes_exponential_from_linear()
    print("  tells an exponential drop from a linear one       OK")
    test_a_noise_rumble_does_not_fake_a_drop()
    print("  a -20 dB pink rumble does not fake a drop         OK")
    oc = test_a_coherent_neighbour_is_survived_or_refused()
    nref = sum(1 for o in oc if o[3] == "REFUSED")
    worst = max((abs(o[4] - 1.0) for o in oc if o[4] is not None), default=0.0)
    print(f"  a coherent neighbour is refused or survived        {nref} of {len(oc)} refused, "
          f"worst surviving |R-1| {worst:.4f}")
    for nm, rel, mult, verd, got, nb in oc:
        print(f"      {nm} {rel:+d} dB at {mult:.2f} f0: {verd:<20s}"
              + (f" x{got:.4f}" if got else "") + f"   settled neighbour {nb:.1f} dB"
              if nb is not None else "")
    bp = test_band_limiting_is_worse_and_is_therefore_off()
    print(f"  band-limiting is worse, so it is off              "
          f"excess err bp {100*bp[0]:.1f} % vs raw {100*bp[1]:.1f} %; "
          f"null bias bp {bp[2]:.4f} vs raw {bp[3]:.4f}")
    test_refuses_what_it_cannot_measure()
    print("  refuses silent / wrong-rate / clipped / too-short OK")
    print(f"\nGATE: excess recovered to better than {100*max(w1,w2):.1f} % of (R-1) across the whole "
          f"grid, {100*max(w1,w2_60):.1f} % for 60 ms drops; null bias below {w3:.4f}.")
    return bad


if __name__ == "__main__":
    sys.exit(main())


def _excess_trajectory():
    t = np.arange(40) * 0.004
    return t, 0.3 * np.exp(-t / 0.02)


def test_a_bug_in_the_fit_raises_rather_than_reading_as_no_fit(monkeypatch):
    """#600: `_fit_exponential` caught `Exception` around both fits, so a bug
    (a changed signature, a TypeError in the model lambda) returned the same
    all-None dict as a fit that legitimately failed to converge."""
    import scipy.optimize as so

    def broken(*a, **k):
        raise TypeError("injected: curve_fit() got an unexpected keyword")

    monkeypatch.setattr(so, "curve_fit", broken)
    t, e = _excess_trajectory()
    try:
        P._fit_exponential(t, e)
    except TypeError:
        return
    raise AssertionError("a TypeError inside the fit was swallowed as 'no fit'")


def test_a_fit_that_does_not_converge_is_still_a_no_fit(monkeypatch):
    """The legitimate refusal: curve_fit raises RuntimeError when it exhausts
    maxfev. That must stay a None result, not a crash."""
    import scipy.optimize as so

    def no_converge(*a, **k):
        raise RuntimeError("Optimal parameters not found: maxfev exceeded")

    monkeypatch.setattr(so, "curve_fit", no_converge)
    t, e = _excess_trajectory()
    out = P._fit_exponential(t, e)
    assert out["tau_ms"] is None and out["shape"] is None

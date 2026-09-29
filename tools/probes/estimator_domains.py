#!/usr/bin/env python3
"""Measure the validated domain of four estimators, against THIS tree, so the
numbers `ValidatedDomain` declares are measured rather than quoted (#115).

    python3 tools/probes/estimator_domains.py

Issue #115 asks each estimator to carry the domain it was validated on and to
REFUSE outside it. Two of the issue's own four cited failures had already been
fixed when it was curated (#108 for `tone_ratio_db`, #92 for
`inharmonic_fraction_db`'s floor), and two of its numbers -- `band_pair_db`
"holds to 1.4 dB over +-10 % detuning" and `decay_tau` "refused on five of
eight references" -- were carried forward UNVERIFIED by the curation pass,
with no committed probe behind either. This script is that probe: every bound
the domain declarations encode is produced here, from closed-form signals whose
answer is known before the measurement is made, and the script exits non-zero
when a bound it measured stops holding.

It is deliberately the same shape as `tools/probes/verify_109_claims.py` --
the same `_damped`/`_two_tone` synthesis, the same `check()` -- because that is
the file #115's own Implementation Guidance names as the template.

WHAT IT CANNOT MEASURE, STATED RATHER THAN SKIPPED
--------------------------------------------------
`decay_tau`'s "refused on five of eight references" is a claim about the
Fischer TR-808 corpus. That corpus is not on this host (`GF180_TR808_REFS`
unset, `/tmp/tr808-ref` absent), so the rate is NOT re-measured here and is NOT
carried forward into `decay_tau`'s declared domain. What is measured instead is
where `decay_tau` refuses on signals whose answer is known -- which is the
domain a declaration can honestly state -- and section 4 prints the corpus's
absence rather than letting a skipped check look like a passed one.
"""
from __future__ import annotations

import math
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "model"))
sys.path.insert(0, str(ROOT / "tools"))

import audio_measure as am                                            # noqa: E402
import run_case as rc                                                 # noqa: E402

SR = 48000
fails: list[str] = []


def check(ok: bool, what: str):
    print(f"      {'OK  ' if ok else 'FAIL'}  {what}")
    if not ok:
        fails.append(what)


def _damped(f, tau, amp, n, sr=SR, phase=0.0):
    t = np.arange(n) / sr
    return amp * np.exp(-t / tau) * np.sin(2 * math.pi * f * t + phase)


def _sine(f, amp, n, sr=SR, phase=0.0):
    t = np.arange(n) / sr
    return amp * np.sin(2 * math.pi * f * t + phase)


def _two_tone(f1, f2, tau1, tau2, a1, a2, seconds, sr=SR, lead_ms=10.0):
    """Two damped partials, with a TRUE PRE-ONSET LEAD by default.

    `band_energy`'s own docstring states the precondition: `sosfiltfilt` pads
    by 27 samples with an odd extension through the first sample, so a segment
    that begins at full amplitude manufactures an edge worth up to 10 dB in a
    sparsely-occupied band. A synthetic struck signal starts at full amplitude
    by construction, so measuring `band_pair_db` on one without a lead measures
    that edge and calls it the estimator. `run_case.prepare` guarantees the
    lead on real records; this guarantees it on synthetic ones."""
    n = int(seconds * sr)
    x = (_damped(f1, tau1, a1, n, sr, 0.3) + _damped(f2, tau2, a2, n, sr, 1.9))
    if lead_ms <= 0:
        return x
    return np.concatenate([np.zeros(int(lead_ms * 1e-3 * sr)), x])


# ===========================================================================
# 1. band_pair_db -- stationary accuracy, the number #115 asks to relocate
# ===========================================================================
#: The rimshot's two bridged-T bands, which is what `band_pair_db` is pointed
#: at in `run_case.RS_BALANCE_OP`'s neighbourhood.
BAND_A = (380.0, 620.0)
BAND_B = (1450.0, 2150.0)


def measure_band_pair_stationary():
    """`docs/conga-body-spectrum-spread.md` section 2 validated the band-split
    family on two steady sines over a 30 dB range, worst error 0.088 dB. That
    check lives in a document about congas; this re-runs it against
    `band_pair_db` itself so the number belongs to the estimator."""
    print("\n" + "=" * 78)
    print("1. band_pair_db on stationary two-sine signals (known answer)")
    print("=" * 78)
    worst = 0.0
    for ratio_db in (0.0, -6.021, -20.0, -30.006, 12.0, 24.0):
        a_b = 10 ** (ratio_db / 20.0)
        n = int(0.5 * SR)
        x = _sine(500.0, 1.0, n) + _sine(1800.0, a_b, n)
        e = rc.band_pair_db(x, SR, BAND_B, BAND_A)
        if not e.ok:
            check(False, f"stationary ratio {ratio_db:+.2f} dB: refused ({e.reason})")
            continue
        err = e.value - ratio_db
        worst = max(worst, abs(err))
        print(f"      truth {ratio_db:+8.3f} dB   measured {e.value:+8.3f} dB   "
              f"error {err:+.4f} dB")
    print(f"      worst |error| over a 54 dB range: {worst:.4f} dB")
    check(worst < 0.2, f"band_pair_db stationary accuracy is better than 0.2 dB "
                       f"(measured {worst:.4f})")
    return worst


def measure_band_pair_decay():
    """The A^2*tau contamination `balance_trajectory_db`'s docstring names, as
    a NUMBER: a fixed-window band ratio carries 10*log10(tau_a/tau_b) of the
    decay on top of the amplitude ratio it is read as."""
    print("\n" + "=" * 78)
    print("2. band_pair_db vs DIFFERENTIAL DECAY (the A^2*tau term)")
    print("=" * 78)
    truth = 0.0                       # equal amplitudes: the balance is 0 dB
    rows = []
    base = None
    for tau_a, tau_b in ((0.006, 0.006), (0.006, 0.0054), (0.006, 0.0045),
                         (0.006, 0.003), (0.006, 0.0015), (0.006, 0.0006)):
        x = _two_tone(500.0, 1800.0, tau_a, tau_b, 1.0, 1.0, 0.25)
        e = rc.band_pair_db(x, SR, BAND_B, BAND_A, min_decay_bias_db=None)
        if not e.ok:
            rows.append((tau_a, tau_b, None, e.reason))
            print(f"      tau_a={tau_a*1e3:5.1f} ms tau_b={tau_b*1e3:5.1f} ms   "
                  f"REFUSED ({e.reason})")
            continue
        err = e.value - truth
        if base is None:
            base = err
        rows.append((tau_a, tau_b, err, ""))
        print(f"      tau_a={tau_a*1e3:5.1f} ms tau_b={tau_b*1e3:5.1f} ms   "
              f"reported {e.value:+7.3f} dB   error {err:+7.3f} dB   "
              f"(10log10(tau_b/tau_a) = {10*math.log10(tau_b/tau_a):+7.3f}, "
              f"bias the record itself implies {e.detail.get('decay_bias_db', float('nan')):+7.3f})")
    print(f"      MATCHED-DECAY error (tau_a == tau_b): {base:+.3f} dB -- this is the")
    print("      irreducible cost of handing a fixed-window band ratio a DECAYING")
    print("      signal at all, with the onset edge already excluded by the lead.")
    check(abs(base) < 0.6, f"a matched-decay pair costs under 0.6 dB (measured {base:+.3f})")
    return rows


def measure_band_pair_edge():
    """The other axis: a partial that has drifted towards a band EDGE is
    attenuated by the band-pass skirt, so the ratio reads low. This measures
    how far inside the band a partial has to sit for that to be negligible."""
    print("\n" + "=" * 78)
    print("3. band_pair_db vs PARTIAL POSITION IN THE BAND (detuning)")
    print("=" * 78)
    n = int(0.5 * SR)
    lo, hi = BAND_B
    centre = math.sqrt(lo * hi)
    out = []
    for frac in (0.5, 0.3, 0.2, 0.1, 0.05, 0.02, 0.0):
        # `frac` = how far from the band edge, as a fraction of the band's
        # half-width in log-frequency. 0.0 sits exactly on the upper edge.
        f = math.exp(math.log(hi) - frac * (math.log(hi) - math.log(centre)))
        x = _sine(500.0, 1.0, n) + _sine(f, 1.0, n)
        e = rc.band_pair_db(x, SR, BAND_B, BAND_A)
        got = e.value if e.ok else None
        out.append((frac, f, got, "" if e.ok else e.reason))
        print(f"      partial at {f:7.1f} Hz ({frac:.2f} of the half-band from the "
              f"edge): " + (f"{got:+7.3f} dB (truth 0)" if e.ok else f"REFUSED ({e.reason})"))
    return out


# ===========================================================================
# 4. tone_ratio_db -- the post-#108 domain, NOT the withdrawn pre-#108 figures
# ===========================================================================
def measure_tone_ratio_domain():
    print("\n" + "=" * 78)
    print("4. tone_ratio_db: detuning, partial separation, record length")
    print("=" * 78)
    f1, f2, a1, a2 = 540.0, 800.0, 1.0, 0.5
    truth = 20.0 * math.log10(a2 / a1)

    print("      (a) DETUNING -- both lines moved off nominal, search is +-10 %")
    worst = 0.0
    for pct in (0.0, 0.01, 0.05, 0.09, 0.099):
        n = int(0.5 * SR)
        x = _sine(f1 * (1 + pct), a1, n, phase=0.3) + _sine(f2 * (1 + pct), a2, n, phase=1.9)
        e = rc.tone_ratio_db(x, SR, f2, f1)
        if not e.ok:
            check(False, f"{pct*100:.1f}% detune inside the search band: refused ({e.reason})")
            continue
        worst = max(worst, abs(e.value - truth))
        print(f"          {pct*100:5.1f} % off nominal: {e.value:+8.4f} dB "
              f"(truth {truth:+.4f}, error {e.value - truth:+.4f})")
    check(worst < 0.05, f"inside +-10 % detuning tone_ratio_db is exact to "
                        f"0.05 dB (worst {worst:.4f})")
    n = int(0.5 * SR)
    x = _sine(f1 * 1.15, a1, n) + _sine(f2 * 1.15, a2, n)
    e = rc.tone_ratio_db(x, SR, f2, f1)
    print(f"          15.0 % off nominal: " + ("REFUSED (" + e.reason + ")" if not e.ok
                                               else f"{e.value:+.4f} dB"))
    check(not e.ok, "outside +-10 % detuning tone_ratio_db REFUSES rather than reporting")

    print("\n      (b) PARTIAL SEPARATION -- `windowed_tone_amplitude`'s docstring")
    print("          claims ratios are exact 'for partials further apart than its")
    print("          8-bin main lobe'. This measures that claim on the PRIMITIVE,")
    print("          at the two known frequencies, so the line search cannot")
    print("          confound it: find_line's own band would be narrower than a")
    print("          bin at these separations and refuses for a different reason.")
    print("          The relative PHASE of the two lines is swept, because at one")
    print("          phase the leakage cancels and a single draw reads exact where")
    print("          the estimator is not: a worst case over phase is the bound.")
    n = int(0.5 * SR)
    bin_hz = SR / n
    sep_rows = []
    for lobes in (2.0, 1.0, 0.75, 0.5, 0.375, 0.25, 0.125):
        sep = lobes * 8.0 * bin_hz
        fa, fb = 800.0, 800.0 - sep
        worst_row = 0.0
        for ph in np.linspace(0.0, 2 * math.pi, 9)[:-1]:
            x = _sine(fb, a1, n, phase=0.0) + _sine(fa, a2, n, phase=ph)
            pa = rc._amplitude_at(x, SR, fa, "numerator")
            pb = rc._amplitude_at(x, SR, fb, "denominator")
            if not (pa.ok and pb.ok and pa.value > 0 and pb.value > 0):
                continue
            worst_row = max(worst_row,
                            abs(20.0 * math.log10(pa.value / pb.value) - truth))
        sep_rows.append((lobes, worst_row))
        print(f"          separation {sep:7.2f} Hz = {lobes:5.3f} main lobes "
              f"({lobes*8:5.1f} bins): worst error over phase {worst_row:7.4f} dB")
    inside = [d for lo, d in sep_rows if lo >= 0.5]
    check(max(inside) < 0.2,
          f"at half a main lobe of separation and wider the ratio is exact to "
          f"0.2 dB over phase (worst {max(inside):.4f})")
    outside = [d for lo, d in sep_rows if lo < 0.5]
    print(f"          worst over phase at or beyond half a lobe: {max(inside):.4f} dB; "
          f"inside half a lobe: {max(outside):.4f} dB")

    print("\n      (c) RECORD LENGTH -- what actually binds is find_line's search")
    print("          band, not windowed_tone_amplitude's 12 periods: a +-10 % band")
    print("          at 540 Hz is 108 Hz wide and has to span enough BINS to hold")
    print("          a peak with its own prominence.")
    for seconds in (0.200, 0.120, 0.100, 0.080, 0.060, 0.050, 0.040, 0.030):
        m = int(seconds * SR)
        x = _sine(f1, a1, m, phase=0.3) + _sine(f2, a2, m, phase=1.9)
        e = rc.tone_ratio_db(x, SR, f2, f1)
        print(f"          {seconds*1e3:6.1f} ms ({seconds*f1:5.1f} periods of the lower "
              f"line, band spans {0.2*f1*seconds:4.1f} bins): "
              + (f"{e.value:+8.4f} dB, error {e.value-truth:+.4f}" if e.ok
                 else f"REFUSED ({e.reason})"))

    print("\n      (d) DIFFERENTIAL DECAY at rimshot speed (#109's own case)")
    for tau1, tau2 in ((0.006, 0.006), (0.006, 0.0015)):
        x = _two_tone(f1, f2, tau1, tau2, a1, a2, 2.2, lead_ms=0.0)
        e = rc.tone_ratio_db(x, SR, f2, f1)
        print(f"          tau {tau1*1e3:.1f}/{tau2*1e3:.1f} ms: " +
              (f"{e.value:+8.4f} dB (truth {truth:+.4f})" if e.ok
               else f"REFUSED ({e.reason})"))
        check(not e.ok or abs(e.value - truth) < 1.0,
              f"tau {tau1*1e3:.1f}/{tau2*1e3:.1f} ms: no silent multi-dB bias")
    return sep_rows


# ===========================================================================
# 5. decay_tau -- its domain re-measured, since the corpus is not on this host
# ===========================================================================
def measure_decay_tau_domain():
    print("\n" + "=" * 78)
    print("5. decay_tau: carrier cycles per tau, decay range, SNR")
    print("=" * 78)
    print("      (a) CYCLES PER TAU -- f*tau. The code's own explicit gate is at")
    print("          0.8 cycles/tau; what this measures is that the gate that")
    print("          ACTUALLY fires first is the single-exponential residual, and")
    print("          it fires up to twice as high, so 0.8 is not the boundary a")
    print("          domain declaration can honestly state.")
    for f, tau in ((56.0, 0.005), (56.0, 0.012), (56.0, 0.0143), (56.0, 0.020),
                   (56.0, 0.025), (56.0, 0.0268), (56.0, 0.029), (90.0, 0.018),
                   (200.0, 0.030), (3450.0, 0.010)):
        x = _damped(f, tau, 1.0, int(max(10 * tau, 0.06) * SR))
        # min_cycles_per_tau=None turns the DECLARED gate off, so what is left
        # is whatever the estimator's own machinery does there -- which is the
        # measurement the declared bound is derived from.
        e = am.decay_tau(x, SR, min_cycles_per_tau=None)
        cyc = f * tau
        if e.ok:
            print(f"          f={f:7.1f} Hz tau={tau*1e3:6.1f} ms ({cyc:5.2f} cycles/tau): "
                  f"{e.value*1e3:7.2f} ms, error {100*(e.value/tau - 1):+6.2f} %")
        else:
            print(f"          f={f:7.1f} Hz tau={tau*1e3:6.1f} ms ({cyc:5.2f} cycles/tau): "
                  f"REFUSED ({e.reason})")

    print("\n      (b) ACCURACY INSIDE THE DOMAIN, over the nine (f, tau) pairs")
    print("          test_audio_measure.py already ground-truths:")
    worst = 0.0
    for f, tau in ((56.0, 0.029), (56.0, 0.127), (56.0, 0.352), (90.0, 0.092),
                   (185.0, 0.044), (173.0, 0.030), (540.0, 0.025),
                   (3450.0, 0.010), (7100.0, 0.003)):
        x = _damped(f, tau, 1.0, int(max(8 * tau, 0.05) * SR))
        e = am.decay_tau(x, SR)
        if not e.ok:
            check(False, f"f={f} tau={tau}: refused inside its own domain ({e.reason})")
            continue
        worst = max(worst, abs(e.value / tau - 1))
    print(f"          worst relative error: {100*worst:.2f} %")
    check(worst <= 0.08, f"decay_tau is accurate to 8 % inside its domain "
                         f"(worst {100*worst:.2f} %)")

    print("\n      (c) DECAY RANGE -- the window has to hold min_range_db of fall")
    for span_db in (40.0, 30.0, 25.0, 22.0, 20.0, 18.0, 15.0, 12.0, 10.0, 6.0):
        tau = 0.030
        seconds = tau * span_db / (20.0 / math.log(10))
        x = _damped(540.0, tau, 1.0, int(seconds * SR))
        e = am.decay_tau(x, SR)
        print(f"          record holds {span_db:5.1f} dB of decay: " +
              (f"{e.value*1e3:6.2f} ms" if e.ok else f"REFUSED ({e.reason})"))

    print("\n      (d) SNR -- additive white noise under a 30 ms / 540 Hz ring.")
    print("          decay_tau has no explicit SNR gate; this measures where its")
    print("          residual gate starts catching the contamination.")
    rng = np.random.default_rng(1)
    tau = 0.030
    n = int(0.25 * SR)
    clean = _damped(540.0, tau, 1.0, n)
    snr_ok = []
    for snr_db in (60.0, 50.0, 45.0, 42.0, 40.0, 38.0, 36.0, 34.0, 30.0, 20.0, 10.0):
        amp = math.sqrt(float(np.mean(clean ** 2))) * 10 ** (-snr_db / 20.0)
        errs = []
        refused = 0
        for seed in (1, 2, 3):
            rng = np.random.default_rng(seed)
            e = am.decay_tau(clean + amp * rng.standard_normal(n), SR)
            if e.ok:
                errs.append(abs(e.value / tau - 1))
            else:
                refused += 1
        worst_e = max(errs) if errs else None
        snr_ok.append((snr_db, refused, worst_e))
        print(f"          SNR {snr_db:5.1f} dB: {3-refused}/3 reported, "
              + (f"worst error {100*worst_e:6.2f} %" if worst_e is not None
                 else "all refused"))
    return snr_ok


# ===========================================================================
# 6. inharmonic_fraction_db -- the headroom threshold, DERIVED not chosen
# ===========================================================================
def _series(f0, n, k, sr=SR):
    t = np.arange(n) / sr
    return sum(np.sin(2 * math.pi * i * f0 * t) / i for i in range(1, k + 1))


def measure_inharmonic_headroom():
    print("\n" + "=" * 78)
    print("6. inharmonic_fraction_db: the floor's additive bias vs headroom")
    print("=" * 78)
    print("      The floor is leakage that ADDS to the inharmonic energy, so a")
    print("      reading h dB above its own measured floor is biased upward by")
    print("      exactly 10*log10(1 + 10^(-h/10)). That is a derivation, not a")
    print("      literal, and it is what fixes the refusal threshold: at the")
    print("      repository's existing 6 dB floor margin the bias is 0.97 dB.")
    n = 1 << 15
    f0 = 500.0
    harm = _series(f0, n, 11)

    def read(share_db):
        share = 10 ** (share_db / 10.0)
        a = math.sqrt(2 * share / (1 - share) * float((harm ** 2).sum()) / n)
        return am.inharmonic_fraction_db(harm + _sine(1.5 * f0, a, n), f0,
                                         min_headroom_db=None)

    worst_model_err = 0.0
    worst_below = 0.0
    rows = []
    for share_db in (-10.0, -20.0, -40.0, -60.0, -70.0, -75.0, -80.0, -82.0,
                     -84.0, -85.0, -88.0):
        e = read(share_db)
        if not e.ok:
            print(f"          planted {share_db:+7.2f} dB: REFUSED ({e.reason})")
            continue
        h = e.detail["headroom_db"]
        err = e.value - share_db
        model = 10.0 * math.log10(1.0 + 10 ** (-h / 10.0))
        rows.append((h, err))
        if h >= 9.0:
            worst_model_err = max(worst_model_err, abs(err - model))
        else:
            worst_below = max(worst_below, abs(err - model))
        print(f"          planted {share_db:+7.2f} dB: read {e.value:+8.3f} dB, "
              f"headroom {h:6.2f} dB, error {err:+6.3f} dB, "
              f"closed form {model:+6.3f} dB")
    check(worst_model_err < 0.1,
          f"above 9 dB of headroom the error IS the closed-form floor bias, to "
          f"0.1 dB (worst departure {worst_model_err:.3f} dB)")
    print(f"      Below 9 dB of headroom the reading departs from the closed form by")
    print(f"      up to {worst_below:.3f} dB -- WORSE than the model, not better: the")
    print("      resynthesised floor is itself contaminated once the planted tone is")
    print("      near it. That is a second, independent reason the gate cannot sit")
    print("      at zero headroom.")
    for h in (0.0, 3.0, 6.0, 10.0):
        print(f"          headroom {h:4.1f} dB => modelled bias "
              f"{10.0*math.log10(1.0 + 10**(-h/10.0)):.3f} dB")
    check(abs(10.0 * math.log10(1.0 + 10 ** (-6.0 / 10.0)) - 0.973) < 0.01,
          "at 6 dB headroom the floor's modelled additive bias is 0.97 dB")
    # The error actually achieved AT the declared boundary, by bisecting for the
    # planted share whose headroom lands on 6 dB.
    lo_db, hi_db = -95.0, -40.0
    for _ in range(40):
        mid = 0.5 * (lo_db + hi_db)
        e = read(mid)
        if not e.ok:
            break
        if e.detail["headroom_db"] < am.INHARMONIC_MIN_HEADROOM_DB:
            lo_db = mid
        else:
            hi_db = mid
    e = read(hi_db)
    print(f"      AT the 6 dB boundary (planted {hi_db:.3f} dB, headroom "
          f"{e.detail['headroom_db']:.3f} dB): error {e.value - hi_db:+.3f} dB")
    check(abs(e.value - hi_db) < 2.0,
          f"at the declared boundary the error is bounded by 2 dB "
          f"(measured {abs(e.value - hi_db):.3f})")
    return worst_model_err


# ===========================================================================
# 7. What this host cannot measure, said out loud
# ===========================================================================
def report_corpus_absence():
    print("\n" + "=" * 78)
    print("7. NOT MEASURED HERE, and why")
    print("=" * 78)
    refs = rc.configured_refs()
    if refs.exists():
        print(f"      The TR-808 corpus IS present at {refs}. #115's 'decay_tau")
        print("      refused on five of eight references' can be re-measured on this")
        print("      host -- it was not, because this probe measures domains on")
        print("      closed-form signals; run the reference-integration suite for the")
        print("      corpus-side rate.")
        return
    print(f"      The Fischer TR-808 corpus is NOT on this host ({refs} absent,")
    print("      GF180_TR808_REFS unset). #115's 'decay_tau refused on five of eight")
    print("      references' is therefore NEITHER reproduced NOR carried forward: it")
    print("      does not appear in decay_tau's declared domain. What the declaration")
    print("      states instead is section 5's measured boundaries, which are")
    print("      reproducible anywhere.")


def main() -> int:
    measure_band_pair_stationary()
    measure_band_pair_decay()
    measure_band_pair_edge()
    measure_tone_ratio_domain()
    measure_decay_tau_domain()
    measure_inharmonic_headroom()
    report_corpus_absence()
    print("\n" + "=" * 78)
    if fails:
        print(f"{len(fails)} check(s) FAILED:")
        for f in fails:
            print(f"  - {f}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

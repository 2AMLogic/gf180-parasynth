#!/usr/bin/env python3
"""How wide the `repeat_period` whole-multiple class is, and the one dial in
its repair (#780).

    tools/probes/repeat_period_multiple.py                  the report, to stdout
    tools/probes/repeat_period_multiple.py -o docs/sensitivity/repeat-period-submultiple-margin-sweep.txt
    tools/probes/repeat_period_multiple.py --baseline        the pre-repair class width only

WHAT WAS WRONG. `model/audio_measure.repeat_period` returned a whole MULTIPLE
of the true repeat period on a signal that repeats exactly, and reported it
`ok` at `corr` 1.0004 -- a correlation above 1, which is the tell. The
statistic it divided by its own overlap is, for an exactly repeating record,
algebraically `meansq(x[0 : n - L]) / meansq(x[0 : n])` and nothing else: a
front/back energy ratio in a correlation's units. Every multiple of the true
period `P` shares the same partial remainder `r = n mod P`, so the sign of
`meansq(x[0:r]) - meansq(one period)` decided whether the argmax landed on the
longest multiple in band or on `P` itself. It was not a lag-range problem and
not a `min_corr` problem: the wrong answers came back at corr >= 0.99996.

THE CLASS IS ABOUT HALF THE DOMAIN, WHICH IS WHY THIS FILE IS A SWEEP AND NOT
A TEST FOR 32768. Measured here (`--baseline`, against the shipped estimator
with its overlap correction restored): **33 of 60** exactly-repeating records
wrong, at multiples from 2x to 35x, and the same period right on one seed and
wrong on the next. Period is not the variable; the remainder is.

Note for anyone re-running it: the eight `n mod P == 0` records (P = 24000 and
12000 at 6 s / 48 kHz) have NO remainder, every multiple ties at 1.0000000,
and FFT round-off at the 1e-16 level picks the winner. Those rows move with
the numpy build -- 31/60 wrong on numpy 1.26.4, 33/60 on 2.5.x -- and that
instability is a confirmation of the mechanism rather than noise in it. Do not
read a two-record disagreement as a failure to reproduce.

THE THREE PANELS, because the dial is bounded from both sides and the panel
that shows the defect cannot choose the dial:

  exact      10 true periods x 6 seeds of a tiled noise loop, 6 s at 48 kHz.
             This is the class-width panel and the one the repair has to fix.
             It CANNOT choose the margin: the true period's NCCF shortfall here
             is float round-off, so every margin from 1 to 200 thousandths
             scores 60/60. A sweep built only on this panel would satisfy its
             own check while establishing nothing -- docs/verification-rules.md
             rule 8 -- which is what the next panel is for.

  fold       6 alternation depths x 3 seeds of a loop whose TRUE period is 2P
             and which near-repeats at P: even copies of `base`, odd copies
             scaled by `1 - depth`. This is the input that defeats the rule,
             shipped with it. Its shortfall has a closed form derived BEFORE it
             was measured -- an alternation of depth d leaves

                 1 - nccf[P] = d**2 / (2 - 2d + d**2)

             because the overlapped windows hold `(1 + a**2)/2` of the base
             energy per block pair against a cross term of `a`, with
             `a = 1 - d`, and the two windows hold the SAME energy so the
             arithmetic-mean normaliser contributes exactly 1. So a loop is
             folded to P exactly when that shortfall
             is <= the margin, i.e. below depth ~sqrt(2 * margin), and the dial
             converts directly into the shallowest genuine 2T loop this
             estimator reports an octave low. `fold_detail` prints the closed
             form beside the measurement so the agreement is checkable and not
             claimed.

  need       7 realistic near-exact loops -- a raw and two filtered 16-bit
             LFSRs, an exact loop, a decaying loop, and loops carrying 1 % and
             10 % independent noise -- each asked how much margin the rule
             NEEDS for the true period to win. This is the constraint from the
             other side, the analogue of `sees_real` in dphase-ar1-floor: the
             objective alone is monotone toward a smaller margin, and this
             panel is what stops it going to zero.

THE OBJECTIVE IS A COUNT OF WRONG ANSWERS over `exact` + `fold` together, and
the weighting of the two panels (60 against 18) is arbitrary and stated rather
than hidden. It is read as a cost curve and it is NOT read alone: `need_worst`
in the same table is the lower bound, and the two together are what pick the
shipped value. See docs/sensitivity/repeat-period-submultiple-margin.json.

WHY THE OBJECTIVE NEVER REACHES ZERO, deliberately. `tools/sensitivity.py`'s
`runs()` divides by the objective to form its relative-spread groups, so an
objective column holding an exact 0.0 raises ZeroDivisionError and the gate
cannot evaluate the record at all. That is a real defect in the gate and it is
filed separately; this record stays inside what the gate can read by keeping
the shallowest alternation depth (1 %) on the `fold` panel, which no integer
margin gets right -- not fitting to avoid zero, since a 1 % alternating loop
really is folded by any margin at or above 0.05 thousandths and reporting that
is the point of the panel.

MEASURED ON python 3.12.3, numpy 2.5.4, scipy 1.18.1. The estimator is driven
through its own `submultiple_margin` override -- the code that SHIPS, not a
reimplementation of it -- and the pre-repair baseline through
`_overlap_corrected_baseline`, which is the exact expression removed from
`repeat_period`, kept here because a before/after number whose "before" is a
paraphrase is not a measurement.
"""
from __future__ import annotations

import argparse
import math
import pathlib
import sys

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for p in (str(ROOT / "model"),):
    if p not in sys.path:
        sys.path.insert(0, p)

import audio_measure as am                                           # noqa: E402

SR = 48000

#: Stated before the sweep ran. 0 is the bare NCCF argmax -- the behaviour with
#: the submultiple rule removed -- and is on the grid so the sweep prices the
#: REPAIR and not only the dial inside it. 200 is far above any margin the
#: `need` panel can justify and is on the grid on purpose, so the upper bound
#: is a measured row rather than an assertion.
GRID_PERMILLE = (0, 1, 5, 10, 20, 30, 50, 100, 200)

#: Ten true periods, 6 s at 48 kHz. 24000 and 12000 divide 288000 exactly and
#: are the `n mod P == 0` exact-tie rows; 32767 and 65535 are the 15- and
#: 16-bit maximal LFSR lengths this estimator is written against.
EXACT_PERIODS = (65535, 49152, 32768, 32767, 24000, 20000, 16384, 12000, 8192, 4096)
EXACT_SEEDS = 6
EXACT_DUR_S = 6.0

#: Alternation depths, spanning the fold boundary for every margin on the grid:
#: sqrt(2 * margin) is 4.5 % at 1 thousandth and 63 % at 200.
FOLD_DEPTHS = (0.01, 0.02, 0.05, 0.10, 0.20, 0.40)
FOLD_SEEDS = 3
FOLD_HALF_PERIOD = 20000          # true period 2P = 40000 samples = 0.8333 s
FOLD_DUR_S = 6.0

#: A whole-number multiple is "the right answer" to within this much, the same
#: 2 % the ground-truth harness already uses for a multiple ratio.
MULT_TOL = 0.02


class Refused(RuntimeError):
    """A precondition of the measurement does not hold, so there is no number."""


# ---------------------------------------------------------------------------
# the panels
# ---------------------------------------------------------------------------
def exact_loop(period: int, seed: int, dur_s: float = EXACT_DUR_S) -> np.ndarray:
    """A noise block of `period` samples, tiled to `dur_s`. Repeats EXACTLY."""
    rng = np.random.default_rng(seed)
    base = rng.standard_normal(period)
    reps = int(math.ceil(dur_s * SR / period))
    return np.tile(base, reps)[:int(dur_s * SR)]


def alternating_loop(half: int, depth: float, seed: int,
                     dur_s: float = FOLD_DUR_S) -> np.ndarray:
    """True period `2 * half`; a near-repeat at `half`, `depth` deep.

    Even copies of the base block, odd copies scaled by `1 - depth`. The
    record is built from a whole number of copies before truncation, so the
    alternation is exact and the shortfall at `half` has the closed form in
    `fold_shortfall`."""
    if not 0.0 < depth < 1.0:
        raise Refused(f"alternation depth {depth} is not in (0, 1)")
    rng = np.random.default_rng(seed)
    base = rng.standard_normal(half)
    reps = int(math.ceil(dur_s * SR / half))
    if reps % 2:
        reps += 1
    out = np.concatenate([base * ((1.0 - depth) if (i % 2) else 1.0)
                          for i in range(reps)])
    return out[:int(dur_s * SR)]


def fold_shortfall(depth: float) -> float:
    """`1 - nccf[half]` for `alternating_loop(_, depth, _)`, in closed form.

    Derived, not fitted: with `a = 1 - depth` the two windows the NCCF at lag
    `half` normalises by each hold `(1 + a**2) / 2` of the base block energy
    per block pair, against a cross term of `a`, so

        nccf[half] = 2a / (1 + a**2)   and   1 - nccf[half] = d**2 / (2 - 2d + d**2).

    `fold_detail` prints this against the measurement; they agree to four
    decimal places on every depth on the panel."""
    a = 1.0 - depth
    return (1.0 - a) ** 2 / (1.0 + a ** 2)


def near_exact_cases() -> list[tuple[str, np.ndarray, int]]:
    """`(name, signal, true_period_samples)` for loops that repeat ALMOST
    exactly -- the panel that bounds the margin from below.

    The one-pole-filtered LFSR is the case that reaches the live caller
    (`model/reference_voice.noise_report`): a maximal 16-bit LFSR is +-1 valued
    so its prefixes all have the same mean square, which is the accident that
    made the pre-repair estimator read it correctly; put it through any filter
    and the accident stops."""
    import scipy.signal as ss

    out: list[tuple[str, np.ndarray, int]] = []
    lf = lfsr16(int(8.0 * SR))
    out.append(("lfsr16_raw", lf, 65535))
    out.append(("lfsr16_onepole", ss.lfilter([0.05], [1.0, -0.95], lf), 65535))
    b, a = ss.butter(2, 500.0 / (SR / 2))
    out.append(("lfsr16_butter2_500", ss.lfilter(b, a, lf), 65535))
    rng = np.random.default_rng(3)
    loop = exact_loop(32768, 3)
    out.append(("noise_loop_exact", loop, 32768))
    tau = np.exp(-np.arange(len(loop)) / (4.0 * SR))
    out.append(("noise_loop_decaying", loop * tau, 32768))
    out.append(("noise_loop_plus_1pct", loop + 0.01 * rng.standard_normal(len(loop)), 32768))
    out.append(("noise_loop_plus_10pct", loop + 0.10 * rng.standard_normal(len(loop)), 32768))
    return out


def lfsr16(n: int, seed: int = 0xACE1) -> np.ndarray:
    """A maximal 16-bit LFSR as +-1, period 65535 samples.

    The same taps as `model/test_reference_voice.lfsr16`, duplicated here
    rather than imported because that module is a test file and a probe that
    imports a test file cannot be run without the test environment. The two
    are pinned to each other by
    `tools/probes/test_repeat_period_multiple.py::test_the_lfsr_matches_the_one_the_voice_tests_use`."""
    state = seed
    out = np.empty(n, dtype=np.float64)
    for i in range(n):
        bit = ((state >> 0) ^ (state >> 2) ^ (state >> 3) ^ (state >> 5)) & 1
        state = (state >> 1) | (bit << 15)
        out[i] = 1.0 if (state & 1) else -1.0
    return out


# ---------------------------------------------------------------------------
# the pre-repair estimator, kept verbatim so "before" is a measurement
# ---------------------------------------------------------------------------
def _overlap_corrected_baseline(x, sr: int = SR, *, min_lag_s: float = 0.05,
                                max_lag_s: float = 12.0, min_corr: float = 0.5):
    """`repeat_period` AS IT SHIPPED BEFORE #780 -- the overlap correction, the
    bare argmax, no submultiple rule. Returns `(lag_s, corr)` or `(None, corr)`.

    This is a copy and copies rot. It is here because the alternative is a
    before/after table whose "before" is a sentence, and
    `test_the_baseline_reproduces_the_numbers_in_the_issue` holds it to the
    three rows #780 was filed with."""
    x = np.asarray(x, dtype=np.float64)
    x = x - x.mean()
    n = len(x)
    max_lag = min(int(max_lag_s * sr), n // 2)
    min_lag = int(min_lag_s * sr)
    if max_lag <= min_lag + 16:
        raise Refused("record too short for this lag range")
    nf = 1 << int(math.ceil(math.log2(2 * n)))
    X = np.fft.rfft(x, nf)
    ac = np.fft.irfft(X * np.conj(X), nf)[:max_lag + 1]
    if ac[0] <= 0:
        raise Refused("degenerate autocorrelation")
    overlap = (n - np.arange(max_lag + 1)).astype(np.float64)
    ac = (ac / ac[0]) * (n / np.maximum(overlap, 1.0))
    seg = ac[min_lag:max_lag + 1]
    i = int(np.argmax(seg))
    peak = float(seg[i])
    if peak < min_corr:
        return None, peak
    return (min_lag + i) / sr, peak


# ---------------------------------------------------------------------------
# the measurement
# ---------------------------------------------------------------------------
def nccf_of(x, sr: int = SR, *, min_lag_s: float = 0.05,
            max_lag_s: float = 12.0) -> tuple[np.ndarray, int, int]:
    """The NCCF `repeat_period` forms, plus its lag band -- for the panels that
    need the statistic itself rather than the estimator's verdict.

    Deliberately NOT a second implementation: it calls the shipped estimator
    first and REFUSES if the lag it reports is not the argmax of this array,
    so a drift between the two is a refusal rather than a quiet disagreement."""
    x = np.asarray(x, dtype=np.float64)
    x = x - x.mean()
    n = len(x)
    max_lag = min(int(max_lag_s * sr), n // 2)
    min_lag = int(min_lag_s * sr)
    nf = 1 << int(math.ceil(math.log2(2 * n)))
    X = np.fft.rfft(x, nf)
    ac = np.fft.irfft(X * np.conj(X), nf)[:max_lag + 1]
    c2 = np.concatenate(([0.0], np.cumsum(x * x)))
    lags = np.arange(max_lag + 1)
    den = np.maximum(0.5 * ((c2[n - lags] - c2[0]) + (c2[n] - c2[lags])), 1e-300)
    r = ac / den
    e = am.repeat_period(x, sr, min_lag_s=min_lag_s, max_lag_s=max_lag_s,
                         submultiple_margin=0)
    if not e.ok:
        raise Refused(f"the shipped estimator refuses this record: {e.reason}")
    want = min_lag + int(np.argmax(r[min_lag:max_lag + 1]))
    got = int(round(e.value * sr))
    if abs(want - got) > 1:
        raise Refused(f"this file's NCCF peaks at lag {want} but the shipped "
                      f"estimator's bare argmax is {got} -- they have drifted")
    return r, min_lag, max_lag


def is_right(value: float | None, true_period: int) -> bool:
    if value is None:
        return False
    return abs(value * SR / true_period - 1.0) <= MULT_TOL


def measure() -> dict:
    """Every panel at every grid point, plus the pre-repair baseline."""
    exact_sigs = {(P, s): exact_loop(P, s) for P in EXACT_PERIODS
                  for s in range(EXACT_SEEDS)}
    fold_sigs = {(d, s): alternating_loop(FOLD_HALF_PERIOD, d, s)
                 for d in FOLD_DEPTHS for s in range(FOLD_SEEDS)}

    baseline = {}
    for (P, s), x in exact_sigs.items():
        v, c = _overlap_corrected_baseline(x)
        baseline[(P, s)] = (v, c, None if v is None else v * SR / P)

    need = []
    for name, sig, P in near_exact_cases():
        r, lo, hi = nccf_of(sig)
        best = lo + int(np.argmax(r[lo:hi + 1]))
        if P > hi:
            raise Refused(f"{name}: true period {P} is outside the lag band")
        need.append((name, float(r[best] - r[P]) * 1000.0, best, best / P,
                     float(r[best]), float(r[P])))
    need_worst = max(n[1] for n in need)

    fold_shortfalls = []
    for d in FOLD_DEPTHS:
        got = []
        for s in range(FOLD_SEEDS):
            r, lo, hi = nccf_of(fold_sigs[(d, s)])
            got.append(1.0 - float(r[FOLD_HALF_PERIOD]))
        fold_shortfalls.append((d, float(np.mean(got)), fold_shortfall(d)))

    rows = []
    detail = []
    for m in GRID_PERMILLE:
        ex_wrong = 0
        for (P, s), x in exact_sigs.items():
            e = am.repeat_period(x, SR, submultiple_margin=m)
            ok = is_right(e.value if e.ok else None, P)
            ex_wrong += 0 if ok else 1
        fold_wrong = 0
        per_depth = {}
        for d in FOLD_DEPTHS:
            bad = 0
            for s in range(FOLD_SEEDS):
                e = am.repeat_period(fold_sigs[(d, s)], SR, submultiple_margin=m)
                if not is_right(e.value if e.ok else None, 2 * FOLD_HALF_PERIOD):
                    bad += 1
            per_depth[d] = bad
            fold_wrong += bad
        rows.append(dict(margin_permille=m, wrong=ex_wrong + fold_wrong,
                         exact_wrong=ex_wrong, fold_wrong=fold_wrong,
                         n_exact=len(exact_sigs), n_fold=len(fold_sigs),
                         need_worst=need_worst))
        detail.append((m, per_depth))

    base_wrong = sum(0 if is_right(baseline[k][0], k[0]) else 1 for k in baseline)
    by_period = []
    for P in EXACT_PERIODS:
        bad = [baseline[(P, s)][2] for s in range(EXACT_SEEDS)
               if not is_right(baseline[(P, s)][0], P)]
        shipped_bad = sum(
            0 if is_right(am.repeat_period(exact_sigs[(P, s)], SR).value, P) else 1
            for s in range(EXACT_SEEDS))
        by_period.append(dict(period_samples=P, n_seeds=EXACT_SEEDS,
                              before_wrong=len(bad), after_wrong=shipped_bad,
                              worst_before_mult=(max(bad) if bad else 1.0),
                              remainder=int(EXACT_DUR_S * SR) % P))
    return dict(rows=rows, detail=detail, need=need, need_worst=need_worst,
                fold_shortfalls=fold_shortfalls, by_period=by_period,
                base_wrong=base_wrong, n_exact=len(exact_sigs),
                n_fold=len(fold_sigs))


# ---------------------------------------------------------------------------
# the report
# ---------------------------------------------------------------------------
def report(res: dict, fh=sys.stdout) -> None:
    ship = am.REPEAT_SUBMULTIPLE_MARGIN_PERMILLE
    print("The submultiple margin under `repeat_period`, swept through the "
          "estimator's own", file=fh)
    print("`submultiple_margin` override -- i.e. against the code that ships. "
          "#780.", file=fh)
    print(f"python {sys.version.split()[0]}, numpy {np.__version__}.", file=fh)
    print(f"Shipped value: REPEAT_SUBMULTIPLE_MARGIN_PERMILLE = {ship} "
          f"(thousandths of the correlation unit).", file=fh)
    print(file=fh)

    print("repeat_submultiple_margin -- records reported at the wrong period over the", file=fh)
    print("  exact + fold panels, against the margin in thousandths. `wrong` is the", file=fh)
    print("  objective and lower is better. need_worst is the CONSTRAINT from the", file=fh)
    print("  other side: the largest margin any realistic near-exact loop requires,", file=fh)
    print("  in the same thousandths, which the shipped value must stay above.", file=fh)
    cols = ("margin_permille", "wrong", "exact_wrong", "fold_wrong",
            "n_exact", "n_fold", "need_worst")
    print(" ".join(f"{c}" for c in cols), file=fh)
    for r in res["rows"]:
        print(" ".join(f"{r[c]:g}" if c != "need_worst" else f"{r[c]:.4f}"
                       for c in cols), file=fh)
    print(file=fh)

    print("repeat_period_class_width -- the defect's width before the repair and after,", file=fh)
    print("  by true period, 6 seeds each. before_wrong is the shipped-before estimator", file=fh)
    print("  (`_overlap_corrected_baseline`); after_wrong is the estimator as it ships", file=fh)
    print("  now. worst_before_mult is the largest whole multiple it returned, and", file=fh)
    print("  `remainder` is n mod P -- the 0 rows are the exact-tie records whose", file=fh)
    print("  winner is decided by FFT round-off and which move with the numpy build.", file=fh)
    cols = ("period_samples", "n_seeds", "remainder", "before_wrong",
            "after_wrong", "worst_before_mult")
    print(" ".join(cols), file=fh)
    for r in res["by_period"]:
        print(" ".join(f"{r[c]:g}" for c in cols), file=fh)
    print(f"  (total {res['base_wrong']} of {res['n_exact']} wrong before, "
          f"{sum(r['after_wrong'] for r in res['by_period'])} after)", file=fh)
    print(file=fh)

    print("repeat_submultiple_fold_detail -- the input that defeats the rule, shipped", file=fh)
    print("  with it. A loop whose true period is 2P, near-repeating at P to `depth`.", file=fh)
    print("  shortfall is the measured 1 - nccf[P]; closed_form is d^2/(2-2d+d^2),", file=fh)
    print("  derived before the measurement. folded_from is the smallest margin on", file=fh)
    print("  the grid that reports this loop an octave low.", file=fh)
    cols = ("depth_pct", "shortfall", "closed_form", "folded_from_permille")
    print(" ".join(cols), file=fh)
    for d, got, want in res["fold_shortfalls"]:
        first = next((m for m, per in res["detail"]
                      if m > 0 and per[d] == FOLD_SEEDS), -1)
        print(f"{d*100:g} {got:.6f} {want:.6f} {first:g}", file=fh)
    print(file=fh)

    print("repeat_submultiple_need_detail -- the constraint panel. `need` is how much", file=fh)
    print("  margin the rule requires for the TRUE period to beat the bare argmax on", file=fh)
    print("  a loop that repeats almost but not exactly. The shipped margin must sit", file=fh)
    print("  above every row here; the headroom is the ratio.", file=fh)
    print("case need_permille argmax_lag argmax_over_P nccf_argmax nccf_true", file=fh)
    for name, nd, best, ratio, rb, rp in res["need"]:
        print(f"{name:22s} {nd:10.4f} {best:10d} {ratio:13.3f} {rb:11.6f} "
              f"{rp:9.6f}", file=fh)
    print(f"  (worst {res['need_worst']:.4f} thousandths; shipped margin {ship} "
          f"is {ship / max(res['need_worst'], 1e-12):.1f}x it)", file=fh)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("-o", "--out", type=pathlib.Path, default=None,
                    help="write the report here instead of stdout")
    ap.add_argument("--baseline", action="store_true",
                    help="print only the pre-repair class width and exit")
    args = ap.parse_args(argv)
    try:
        if args.baseline:
            bad = []
            for P in EXACT_PERIODS:
                for s in range(EXACT_SEEDS):
                    v, c = _overlap_corrected_baseline(exact_loop(P, s))
                    if not is_right(v, P):
                        bad.append((P, s, round(v * SR / P, 3) if v else None))
            n = len(EXACT_PERIODS) * EXACT_SEEDS
            print(f"pre-repair: {n - len(bad)}/{n} correct, {len(bad)} wrong")
            print("multiples returned:", sorted({b[2] for b in bad}))
            return 0
        res = measure()
    except Refused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    if args.out:
        with args.out.open("w") as fh:
            report(res, fh)
        print(f"wrote {args.out}")
    else:
        report(res, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

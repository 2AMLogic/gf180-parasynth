#!/usr/bin/env python3
"""plan081 B: can D12A's "Burst timing" estimator separate STRIKE TIMING from
NOISE INSIDE A SUSTAINED STRIKE?  A bounded apparatus check, not a new detector.

THREE DIFFERENT QUANTITIES (plan082 B), kept apart here:

  1. PROGRAMMED STRIKE TIMES -- the excitation schedule: model/RTL envelope
     state (fire and re-strike frames). Known exactly for our renders, and for
     the synthetic signals below by construction. Unknown for the reference.
  2. PEAKS IN A NOISY ENVELOPE -- what `run_case._burst_span_ms` returns: the
     time between the first and last peaks that `audio_measure.envelope_bursts`
     ACCEPTS in a 4 ms RMS envelope (>= 40 % of peak, >= 6 ms apart, >= 2 dB
     dip between them) over 0-120 ms.
  3. The duration / energy distribution of the audible transient -- not
     measured here.

The declared quantity this check asks the estimator to recover is (1)'s SPAN,
t_last_strike - t_first_strike, and the strike COUNT. If (2) cannot recover
it within the budget below on signals shaped like a clap, "Burst timing" is
UNQUALIFIED for that domain.

THE GENERATOR IS INDEPENDENT OF THE DETECTOR AND OF THE MODEL. Gaussian
noise (numpy PCG64, per-realisation seed) through a float Butterworth band-pass
around the clap band (scipy), times a piecewise envelope built from a declared
strike list: each strike is a_k * exp(-(t - t_k)/tau_k) until the next strike
(re-strike semantics), plus an optional tail b * exp(-t/tau_t) from t = 0.
Truth is the strike list. Nothing is derived by running the peak finder.

ERROR BUDGET, frozen before the first run (from the property's needs, not from
the results): the official tolerance is 50 % of the reference, ~17.9 ms. The
estimator may use at most a fraction of that:
  * |median(span error)|      <= 2 ms
  * 95th pct |span error|     <= 5 ms
  * false EXTRA strikes (count > truth) in <= 1 of 24 realisations
  * a control whose true span moves by >= 8 ms must satisfy the same budget
    against its OWN truth (i.e. the defect is seen at its true size)
in every condition, at both sample rates.

ONE pre-declared correction is evaluated, and only one: `min_dip_db` 2 -> 6 dB.
Justification from the physical signal, not from the results: a strike with
tau <= 5 ms decays >= 17 dB over a >= 10 ms spacing, so a real re-strike dips
far more than 6 dB, while noise fluctuation inside one strike is what 2 dB
admits. If it meets the budget everywhere it is a versioned correction
candidate (the baseline and the reference must then be rescored under it);
otherwise the property is recorded UNQUALIFIED for this domain.
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "model"))

import numpy as np  # noqa: E402
from scipy.signal import butter, sosfilt  # noqa: E402

import audio_measure as am  # noqa: E402
import run_case as rc  # noqa: E402

N_REAL = 24
BUDGET = dict(median_abs_ms=2.0, p95_abs_ms=5.0, max_false_extra=1)
P = 10.646e-3          # 511 frames at 48 kHz: the 4-strike schedule's spacing
TAIL = (0.32, 0.047)   # b relative to the first strike, tau: the current kit's ratio

# name -> (strikes [(t_s, level, tau_s)], tail (b, tau) or None, role)
CONDITIONS = {
    "S3 three short (baseline-like)":
        ([(0.0, 1.0, 4e-3), (0.010, 0.81, 4e-3), (0.020, 0.66, 4e-3)], TAIL, "shape"),
    "S4 four short":
        ([(0.0, 1.0, 4e-3), (P, 0.81, 4e-3), (2 * P, 0.66, 4e-3), (3 * P, 0.54, 4e-3)], TAIL, "shape"),
    "S3+F20 sustained final tau 20 ms":
        ([(0.0, 1.0, 4e-3), (P, 0.81, 4e-3), (2 * P, 0.66, 4e-3), (3 * P, 1.0, 20e-3)], (0.32, 0.080), "shape"),
    "S3+F40 sustained final tau 40 ms":
        ([(0.0, 1.0, 4e-3), (P, 0.81, 4e-3), (2 * P, 0.66, 4e-3), (3 * P, 0.54, 40e-3)], (0.32, 0.080), "shape"),
    "SLOW three short + slow tail, no 4th trigger":
        ([(0.0, 1.0, 4e-3), (P, 0.81, 4e-3), (2 * P, 0.66, 4e-3)], (0.5, 0.080), "shape"),
    "CTRL missing final (vs S3+F20)":
        ([(0.0, 1.0, 4e-3), (P, 0.81, 4e-3), (2 * P, 0.66, 4e-3)], (0.32, 0.080), "control"),
    "CTRL missing 2nd strike (vs S3+F20)":
        ([(0.0, 1.0, 4e-3), (2 * P, 0.66, 4e-3), (3 * P, 1.0, 20e-3)], (0.32, 0.080), "control"),
    "CTRL delayed final +8 ms (vs S3+F20)":
        ([(0.0, 1.0, 4e-3), (P, 0.81, 4e-3), (2 * P, 0.66, 4e-3), (3 * P + 0.008, 1.0, 20e-3)],
         (0.32, 0.080), "control"),
    "CTRL extra strike at 55 ms (vs S3+F20)":
        ([(0.0, 1.0, 4e-3), (P, 0.81, 4e-3), (2 * P, 0.66, 4e-3), (3 * P, 1.0, 12e-3),
          (0.055, 0.9, 4e-3)], (0.32, 0.080), "control"),
}

# name -> (min_dip_db, noisy carrier)
DETECTORS = {"v0 official (min_dip 2 dB)": (2.0, True),
             "v1 pre-declared (min_dip 6 dB)": (6.0, True),
             "v0 on a NOISE-FREE carrier (reference point only)": (2.0, False)}


def synth(strikes, tail, sr, seed, noise=True):
    """noise=False: a sign-alternating carrier (x^2 == env^2 exactly), the
    detector's reference point with no noise at all -- separates "cannot read
    this envelope shape" from "noise breaks it"."""
    rng = np.random.default_rng(seed)
    lead, n = int(0.050 * sr), int(0.400 * sr)
    sos = butter(2, [1071 / 1.6 ** 0.5 / (sr / 2), 1071 * 1.6 ** 0.5 / (sr / 2)], btype="bandpass",
                 output="sos")
    carrier = sosfilt(sos, rng.standard_normal(n + sr // 10))[sr // 10:]   # filter run-in discarded
    carrier /= np.sqrt(np.mean(carrier ** 2))
    if not noise:
        carrier = np.where(np.arange(n) % 2 == 0, 1.0, -1.0)
    env = np.zeros(n)
    t = np.arange(n) / sr
    starts = [lead + int(round(ts * sr)) for ts, _, _ in strikes] + [n]
    for k, (ts, a, tau) in enumerate(strikes):
        s, e = starts[k], starts[k + 1]
        env[s:e] = a * np.exp(-(t[s:e] - t[s]) / tau)
    if tail:
        b, tt = tail
        env[lead:] += b * np.exp(-(t[lead:] - t[lead]) / tt)
    return env * carrier


def detect(x, sr, min_dip):
    y = rc.prepare(x, sr, side="synthetic")
    env = am.rms_envelope(rc.window(y, sr, 0.0, 0.120), rc.ENV_WIN_MS["CP"], sr)
    b = am.envelope_bursts(env, sr, min_sep_s=0.006, min_dip_db=min_dip)
    return (len(b), (b[-1][0] - b[0][0]) * 1e3 if len(b) >= 2 else None)


def run(n_real=N_REAL):
    out = {}
    for dname, (dip, noisy) in DETECTORS.items():
        rows = {}
        for cname, (strikes, tail, role) in CONDITIONS.items():
            truth_span = (strikes[-1][0] - strikes[0][0]) * 1e3
            truth_n = len(strikes)
            for sr in (44100, 48000):
                errs, counts, refused = [], [], 0
                for r in range(n_real):
                    n, span = detect(synth(strikes, tail, sr, seed=1000 * r + sr, noise=noisy), sr, dip)
                    counts.append(n)
                    if span is None:
                        refused += 1
                        continue
                    errs.append(span - truth_span)
                a = np.abs(errs) if errs else np.array([np.inf])
                row = {"truth_span_ms": round(truth_span, 3), "truth_count": truth_n, "role": role,
                       "median_err_ms": round(float(np.median(errs)), 3) if errs else None,
                       "p95_abs_err_ms": round(float(np.percentile(a, 95)), 3),
                       "min_err_ms": round(float(min(errs)), 3) if errs else None,
                       "max_err_ms": round(float(max(errs)), 3) if errs else None,
                       "false_extra": int(sum(c > truth_n for c in counts)),
                       "missed": int(sum(c < truth_n for c in counts)),
                       "refused": refused, "n": n_real}
                row["within_budget"] = bool(
                    refused == 0 and abs(row["median_err_ms"]) <= BUDGET["median_abs_ms"]
                    and row["p95_abs_err_ms"] <= BUDGET["p95_abs_ms"]
                    and row["false_extra"] <= BUDGET["max_false_extra"])
                rows[f"{cname} @ {sr}"] = row
        out[dname] = {"rows": rows, "qualified_everywhere": all(r["within_budget"] for r in rows.values()),
                      "failing": [k for k, r in rows.items() if not r["within_budget"]]}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(ROOT / "docs/scorecard/clap-d12a/burst-timing-qual.json"))
    ap.add_argument("--n", type=int, default=N_REAL)
    a = ap.parse_args(argv)
    res = {"tool": "tools/clap_burst_timing_qual.py", "budget": BUDGET, "n_realisations": a.n,
           "declared_quantity": "programmed strike span t_last - t_first, and strike count",
           "estimator_quantity": "span between first and last ACCEPTED peaks of a 4 ms RMS envelope",
           "detectors": run(a.n)}
    for d, v in res["detectors"].items():
        print(f"== {d}: {'QUALIFIED' if v['qualified_everywhere'] else 'UNQUALIFIED'}")
        for k, r in v["rows"].items():
            print(f"  {'ok  ' if r['within_budget'] else 'FAIL'} {k:52s} truth {r['truth_span_ms']:6.2f} "
                  f"med {r['median_err_ms']!s:>8} p95 {r['p95_abs_err_ms']:8.2f} "
                  f"extra {r['false_extra']:2d} missed {r['missed']:2d} refused {r['refused']}")
    p = pathlib.Path(a.out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(res, indent=1) + "\n")
    v1 = res["detectors"]["v1 pre-declared (min_dip 6 dB)"]["qualified_everywhere"]
    v0 = res["detectors"]["v0 official (min_dip 2 dB)"]["qualified_everywhere"]
    print(f"verdict: official {'QUALIFIED' if v0 else 'UNQUALIFIED'}; "
          f"pre-declared correction {'QUALIFIED' if v1 else 'UNQUALIFIED'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

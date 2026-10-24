#!/usr/bin/env python3
"""Issue #243: would a Pekonen-style post-oscillator one-pole earn its place?

    .venv/bin/python tools/pekonen_coloration_probe.py            # report, exit 0/1/2
    .venv/bin/python tools/pekonen_coloration_probe.py --json out.json
    .venv/bin/python tools/pekonen_coloration_probe.py --controls # every injected case

Pekonen et al. (2011) fit a first-order IIR to a MEASURED Minimoog Voyager
oscillator. We reuse the METHOD only. No Voyager number is used or quoted here,
and nothing below is Model D ground truth: the only external signal is the
committed Mini V3 saw rows in docs/reference-voice-results.json.

WHAT THE REFERENCE IS. Mini V3 is a whole-path observation: its filter cannot be
bypassed (model/reference_voice.py), so the cutoff was at maximum and the
filter's residual response is INSIDE every number. That is stated, not corrected.
A one-pole fitted to (Mini V3 - ours) therefore fits "oscillator + Mini V3's
residual filter + its output stage". It cannot be attributed to the oscillator.

WHAT IS NOT CLAIMED. Fitting p to the same rows it is scored on is a calibration
on our own comparison, not validation. The only out-of-sample figure is
LEAVE-ONE-PITCH-OUT: fit on four pitches, score the fifth.

DECISION RULE, fixed BEFORE the data was looked at (see RULE below).
Exit 0 = report produced and every control behaved; 1 = a control failed;
2 = REFUSED (a precondition of the apparatus failed).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys

import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "model"))
sys.path.insert(0, os.path.join(ROOT, "audition"))

RESULTS = os.path.join(ROOT, "docs", "reference-voice-results.json")
KMAX = 12
SR = 48000
# The documented exclusion of Mini V3's saw at 1760 Hz: "one discontinuity per
# period but not a saw: h6 -19.1 dB, model expects -15.6". Re-derived below, not
# trusted by note number.
NOT_A_SAW_DB = 3.0
STALE_OURS_DB = 0.15      # recorded 'ours' vs ours re-rendered now

# RULE (declared up front). Adopt a coloration stage only if ALL hold:
#   A. held-out (leave-one-pitch-out) RMS error falls by at least 25 % vs no stage;
#   B. the no-stage RMS error exceeds JND_DB (otherwise there is nothing audible to fix);
#   C. the fitted pole is stable across folds (spread <= 0.1);
#   D. the null control (residual shuffled across pitch) does NOT also satisfy A.
# Otherwise: measured, and not taken. JND_DB is a judgement (about 1 dB for a
# single partial's level), stated here as such, not a measured threshold.
RULE = dict(held_out_gain=0.25, jnd_db=1.0, pole_spread=0.1)


class Refused(Exception):
    pass


def ideal_saw_db(k):
    return 20 * math.log10(1.0 / k)


def stage_db(p, k, f0, sr=SR):
    """Level of harmonic k relative to the fundamental through y[n]=(1-p)x[n]+p y[n-1]."""
    def mag(f):
        w = 2 * math.pi * f / sr
        return abs(1 - p) / abs(1 - p * complex(math.cos(w), -math.sin(w)))
    return 20 * math.log10(mag(k * f0) / mag(f0))


def stage_int(p_q15, x):
    """The fixed-point form that would ship if adopted: y += ((x - y) * a) >> 15,
    a = (1-p) in Q15, y a signed 32-bit accumulator. Used only to bound the cost
    of quantising p (see quantisation_error_db); no RTL exists for it."""
    a = 32768 - p_q15
    y = 0
    out = []
    for v in x:
        y += ((int(v) - y) * a) >> 15
        out.append(y)
    return out


def quantisation_error_db(p, k, f0):
    pq = round(p * 32768) / 32768
    return stage_db(pq, k, f0) - stage_db(p, k, f0)


def load_reference(path=RESULTS):
    if not os.path.exists(path):
        raise Refused(f"{path} missing: no reference evidence to compare against")
    rows = json.load(open(path))["osc"]
    mini = {r["note"]: r for r in rows if r["device"] == "miniv3" and r["wave"] == "saw"}
    ours = {r["note"]: r for r in rows if r["device"] == "ours" and r["wave"] == "saw"}
    if len(mini) < 5 or len(ours) < 5:
        raise Refused("fewer than 5 pitches of Mini V3 / ours saw in the record")
    return mini, ours


def hvec(row):
    return {k: row.get(f"h{k}") for k in range(2, KMAX + 1)}


def not_a_saw(row):
    h6 = row.get("h6")
    return h6 is None or abs(h6 - ideal_saw_db(6)) >= NOT_A_SAW_DB


def ours_rerender(note):
    import voice_fx as vf
    import audio_measure as am
    o = vf.OscFx("saw", blep=True)
    y = np.asarray(o.render(int(0.5 * SR), vf.phase_inc(vf.note_hz(note))), float) / 32768.0
    s = am.harmonic_signature(y, SR, f0=vf.note_hz(note), kmax=KMAX)
    return s, vf.note_hz(note)


def build_residuals(mini, ours, rerender=ours_rerender):
    """(note, k, f0, residual_dB = Mini V3 - ours) for every qualifying harmonic."""
    out, excluded = [], []
    for note in sorted(mini):
        if not_a_saw(mini[note]):
            excluded.append(note)
            continue
        s, f0 = rerender(note)
        for k in range(2, KMAX + 1):
            rec, now, ref = ours[note].get(f"h{k}"), s.get(f"h{k}"), mini[note].get(f"h{k}")
            if rec is None or now is None or ref is None:
                continue
            if abs(rec - now) > STALE_OURS_DB:
                raise Refused(f"ours h{k} at note {note}: recorded {rec:.2f} dB but the "
                              f"model now renders {now:.2f} dB -- the reference rows are "
                              "paired with a stale 'ours'; re-run reference_voice.py --stage osc")
            out.append((note, k, f0, ref - now))
    if not out:
        raise Refused("no qualifying rows")
    return out, excluded


def fit_pole(rows, grid=None):
    grid = np.linspace(-0.9, 0.95, 371) if grid is None else grid
    best = None
    for p in grid:
        e = math.sqrt(np.mean([(r - stage_db(p, k, f0)) ** 2 for _, k, f0, r in rows]))
        if best is None or e < best[1]:
            best = (float(p), e)
    return best


def rms(rows, p=None):
    return math.sqrt(np.mean([(r - (0 if p is None else stage_db(p, k, f0))) ** 2
                              for _, k, f0, r in rows]))


def lopo(rows):
    """Leave one pitch out: fit on the others, score the held-out one."""
    notes = sorted({n for n, *_ in rows})
    folds = []
    for n in notes:
        tr = [r for r in rows if r[0] != n]
        te = [r for r in rows if r[0] == n]
        p, _ = fit_pole(tr)
        folds.append(dict(note=n, p=p, none=rms(te), stage=rms(te, p)))
    tot = lambda key: math.sqrt(np.mean([f[key] ** 2 for f in folds]))
    return folds, tot("none"), tot("stage")


def verdict(rows):
    folds, none, stage = lopo(rows)
    ps = [f["p"] for f in folds]
    # null: shuffle the residual across pitch (keeps its distribution, destroys
    # its relation to frequency); a stage that "improves" this is overfitting.
    rng = np.random.default_rng(243)
    gains = []
    for _ in range(20):
        perm = rng.permutation(len(rows))
        sh = [(n, k, f0, rows[j][3]) for (n, k, f0, _), j in zip(rows, perm)]
        _, n0, n1 = lopo(sh)
        gains.append(1 - n1 / n0 if n0 > 1e-9 else 0.0)
    a = (none > 1e-9) and (1 - stage / none) >= RULE["held_out_gain"]
    b = none > RULE["jnd_db"]
    c = (max(ps) - min(ps)) <= RULE["pole_spread"]
    d = not (np.mean(gains) >= RULE["held_out_gain"])
    return dict(held_out_none_rms_db=none, held_out_stage_rms_db=stage,
                held_out_gain=(1 - stage / none) if none > 1e-9 else 0.0, folds=folds,
                insample_none_rms_db=rms(rows), insample_pole=fit_pole(rows),
                null_mean_gain=float(np.mean(gains)),
                A_gain=a, B_audible=b, C_stable=c, D_null_clean=d,
                adopt=bool(a and b and c and d))


def controls():
    """Injected defects. Each must come out the way its label says, or the
    instrument is not trusted (exit 1)."""
    res = {}
    f0s = {n: 440.0 * 2 ** ((n - 69) / 12) for n in (33, 45, 57, 69, 81)}
    # 1. POSITIVE: reference = ours through a known pole. Must recover it, adopt.
    rows = [(n, k, f0s[n], stage_db(0.55, k, f0s[n])) for n in f0s for k in range(2, KMAX + 1)]
    v = verdict(rows)
    res["known_pole_recovered"] = abs(v["insample_pole"][0] - 0.55) < 0.01 and v["A_gain"]
    # 2. NEGATIVE: reference == ours. Nothing to fix; must NOT adopt.
    v = verdict([(n, k, f0s[n], 0.0) for n in f0s for k in range(2, KMAX + 1)])
    res["identical_reference_not_adopted"] = not v["adopt"]
    # 3. WRONG-SHAPE: tilt that depends on harmonic number only (oscillator-
    #    intrinsic, not an LTI stage after it). A one-pole in absolute frequency
    #    must NOT earn adoption from it.
    rows = [(n, k, f0s[n], -0.8 * (k - 1)) for n in f0s for k in range(2, KMAX + 1)]
    v = verdict(rows)
    res["k_only_tilt_not_adopted"] = not v["adopt"]
    # 4. Noise: iid 1 dB noise has no frequency structure; must not adopt.
    rng = np.random.default_rng(7)
    rows = [(n, k, f0s[n], float(rng.normal(0, 1.0))) for n in f0s for k in range(2, KMAX + 1)]
    res["noise_not_adopted"] = not verdict(rows)["adopt"]
    # 5. The refusal guard: a stale 'ours' must be REFUSED, not scored.
    mini, ours = load_reference()
    stale = {n: dict(r, h3=r["h3"] + 1.0) for n, r in ours.items()}
    try:
        build_residuals(mini, stale)
        res["stale_ours_refused"] = False
    except Refused:
        res["stale_ours_refused"] = True
    # 6. Documented Mini V3 1760 Hz exclusion is re-derived from the rows.
    res["miniv3_1760_excluded"] = not_a_saw(mini[93]) and not any(
        not_a_saw(mini[n]) for n in (33, 45, 57, 69, 81))
    # 7. Q15 quantisation of p is far below the JND for any candidate.
    res["q15_error_negligible"] = max(abs(quantisation_error_db(p, k, f))
                                      for p in (-0.5, 0.3, 0.8, 0.95)
                                      for k in range(2, 13) for f in (55, 880)) < 0.05
    # 8. integer stage equals the float recurrence DC gain (unity).
    out = stage_int(round(0.5 * 32768), [32767] * 400)
    res["int_stage_dc_unity"] = abs(out[-1] - 32767) <= 2
    return res


def report(rows, excluded, v):
    L = []
    L.append("Mini V3 saw minus ours (dB, per harmonic). Positive = Mini V3 louder than ours.")
    L.append("Mini V3 is a WHOLE-PATH observation (filter unbypassable); not Model D; not Voyager.")
    L.append(f"Excluded as not-a-saw (re-derived): notes {excluded}")
    notes = sorted({n for n, *_ in rows})
    L.append("note   f0 Hz   " + " ".join(f"h{k:<4}" for k in range(2, KMAX + 1)))
    for n in notes:
        r = {k: x for m, k, f0, x in rows if m == n}
        f0 = [f for m, k, f, x in rows if m == n][0]
        L.append(f"{n:4d} {f0:8.1f}  " + " ".join(
            "  .  " if k not in r else f"{r[k]:+5.2f}" for k in range(2, KMAX + 1)))
    L.append("")
    L.append(f"no stage, RMS over rows: {v['insample_none_rms_db']:.2f} dB")
    p, e = v["insample_pole"]
    L.append(f"in-sample best pole p={p:+.3f} -> RMS {e:.2f} dB   (CALIBRATION, not validation)")
    L.append("leave-one-pitch-out (the only out-of-sample figure):")
    for f in v["folds"]:
        L.append(f"  held out note {f['note']}: p={f['p']:+.3f}  none {f['none']:.2f} dB  stage {f['stage']:.2f} dB")
    L.append(f"  pooled held-out RMS: none {v['held_out_none_rms_db']:.2f} -> stage "
             f"{v['held_out_stage_rms_db']:.2f} dB  (gain {100 * v['held_out_gain']:.0f} %)")
    L.append(f"  null (residual shuffled across pitch) mean held-out gain {100 * v['null_mean_gain']:.0f} %")
    L.append(f"RULE  A gain>={100 * RULE['held_out_gain']:.0f}%: {v['A_gain']}   B audible (>{RULE['jnd_db']} dB): "
             f"{v['B_audible']}   C pole stable: {v['C_stable']}   D null clean: {v['D_null_clean']}")
    L.append("DECISION: " + ("ADOPT-CANDIDATE" if v["adopt"] else "MEASURED, NOT TAKEN"))
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--json")
    ap.add_argument("--controls", action="store_true")
    a = ap.parse_args(argv)
    try:
        c = controls()
        for k, ok in c.items():
            print(f"control {k}: {'ok' if ok else 'FAILED'}")
        if not all(c.values()):
            return 1
        if a.controls:
            return 0
        mini, ours = load_reference()
        rows, excluded = build_residuals(mini, ours)
        v = verdict(rows)
    except Refused as e:
        print(f"REFUSED: {e}")
        return 2
    print(report(rows, excluded, v))
    if a.json:
        json.dump(dict(rows=rows, excluded=excluded, verdict=v, rule=RULE), open(a.json, "w"),
                  indent=1, default=float)
    return 0


if __name__ == "__main__":
    sys.exit(main())

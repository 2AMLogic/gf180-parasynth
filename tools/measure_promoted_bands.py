#!/usr/bin/env python3
"""Measure the two #138 metrics on the Fischer corpus and on our own render.

    python tools/measure_promoted_bands.py validate   # start red, then the controls
    python tools/measure_promoted_bands.py measure    # ceilings + ours-vs-real

`validate` answers "does the estimator read a signal whose answer is known, and
does it go red when it is broken". It runs first and `measure` REFUSES (exit 2)
if it does not pass: a number from an unvalidated estimator is not data.

`measure` reports, per voice, (a) the knob TRAVEL of each metric across every
recorded real setting -- the CEILING `promoted_bands` needs -- and (b) ours
minus the machine at each HELD-OUT setting. Ruler: raw dB / ms of the named
estimator, NOT the knob-equivalent rulers of docs/discrimination.md 3.1.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "model"))
sys.path.insert(0, str(ROOT / "tools"))
import promoted_measures as pm  # noqa: E402
import promoted_bands as pb     # noqa: E402

REFS = pathlib.Path(os.environ.get("GF180_TR808_REFS", pathlib.Path.home() / "dev/refs/sounds-tr808-fischer"))
OUT = ROOT / "docs" / "promoted-bands-results.json"
#: per-voice search band for the dominant partial: 40 Hz to the corpus module's
#: own one-octave-above-range fmax (test_discrimination.TUNING_FMAX).
PERIOD_VOICES = ("LT", "MT", "HT", "LC", "MC", "HC")
SR = 44100


def _tone(f, dur=0.24, tau=0.1, sr=SR):
    t = np.arange(int(dur * sr)) / sr
    return np.sin(2 * np.pi * f * t) * np.exp(-t / tau)


def known_cases() -> list:
    """(label, passed, detail). Every answer is closed-form."""
    out = []
    t = np.arange(int(0.24 * SR)) / SR
    # Two partials, amplitudes 1 at 90 Hz and 0.1 at 1500 Hz. The band-power
    # ratio of two sines under a Hann window is the amplitude-squared ratio
    # (leakage is far below 1e-3 at this separation), so the answer is exact.
    for a_hi in (0.0, 0.1, 0.3, 1.0):
        x = np.sin(2 * np.pi * 90 * t) + a_hi * np.sin(2 * np.pi * 1500 * t)
        want = 10 * np.log10(1.0 / (1.0 + a_hi ** 2))
        e = pm.lowband_level_db(x, SR)
        out.append((f"lowband a_hi={a_hi}", e.ok and abs(e.value - want) < 0.05,
                    f"want {want:.3f} got {e.value if e.ok else e.reason}"))
    for f0 in (60.0, 90.0, 190.0, 410.0):
        for detune in (0.0, 0.02, 0.07):
            f = f0 * (1 + detune)
            e = pm.dominant_period_ms(_tone(f), SR, (40.0, f0 * 2.0))
            want = 1000.0 / f
            out.append((f"period {f:.1f} Hz", e.ok and abs(e.value / want - 1) < 0.005,
                        f"want {want:.4f} got {e.value if e.ok else e.reason}"))
    # Gain cancels exactly.
    x = np.sin(2 * np.pi * 90 * t) + 0.3 * np.sin(2 * np.pi * 1500 * t)
    a, b = pm.lowband_level_db(x, SR), pm.lowband_level_db(0.01 * x, SR)
    out.append(("gain cancels", a.ok and b.ok and abs(a.value - b.value) < 1e-9, ""))
    # Refusals.
    out.append(("silence refuses", not pm.lowband_level_db(np.zeros(len(t)), SR).ok, ""))
    out.append(("short clip refuses", not pm.lowband_level_db(x[:500], SR).ok, ""))
    out.append(("band below floor refuses",
                not pm.lowband_level_db(np.sin(2 * np.pi * 1000 * t), SR).ok, ""))
    noise = np.random.default_rng(0).standard_normal(len(t))
    out.append(("noise has no period", not pm.dominant_period_ms(noise, SR, (40.0, 400.0)).ok, ""))
    return out


def injected_bugs() -> list:
    """Each wrong estimator must be caught by `known_cases`-style checks.
    (label, caught). A control that does not go red proves nothing."""
    t = np.arange(int(0.24 * SR)) / SR
    x = np.sin(2 * np.pi * 90 * t) + 0.3 * np.sin(2 * np.pi * 1500 * t)
    want = 10 * np.log10(1.0 / (1.0 + 0.09))
    res = []
    # (1) band edges taken in the wrong unit (bins as Hz)
    bug1 = pm.lowband_level_db(x, SR, band=(40.0 * 2, 200.0 * 20))
    res.append(("lowband band-edge bug", not (bug1.ok and abs(bug1.value - want) < 0.05)))
    # (2) the rectangular window in place of Hann. Both are run on a tone that
    # does not fall on a bin, where the rectangular window's slow sidelobe
    # decay leaks the 1.5 kHz partial into the band. The control only has power
    # if the wrong window is measurably wrong AND the shipped one is right.
    y = np.sin(2 * np.pi * 90.3 * t) + 0.3 * np.sin(2 * np.pi * 1500.7 * t)
    P = np.abs(np.fft.rfft(y)) ** 2
    f = np.fft.rfftfreq(len(y), 1 / SR)
    rect = 10 * np.log10(P[(f >= 40) & (f < 200)].sum() / P.sum())
    good = pm.lowband_level_db(y, SR)
    ideal = 10 * np.log10(1.0 / 1.09)
    res.append(("lowband rectangular-window bug",
                good.ok and abs(good.value - ideal) < 0.01 and abs(rect - ideal) > 0.02))
    # (3) period reported with sample-rate mistaken by 48000/44100
    e = pm.dominant_period_ms(_tone(90.0), SR, (40.0, 200.0))
    wrong = e.value * 48000 / 44100
    res.append(("period sample-rate bug", abs(wrong / (1000 / 90.0) - 1) > 0.005))
    # (4) a tolerance with an invented floor must not be what band_tolerance returns
    tol, basis = pb.band_tolerance("dominant_period_ms", "LT", 1.0)
    res.append(("tolerance does not invent a floor", np.isnan(tol) and "REFUSED" in basis))
    return res


def cmd_validate() -> int:
    bad = 0
    for label, ok, detail in known_cases():
        print(f"{'PASS' if ok else 'FAIL'}  {label}  {detail}")
        bad += not ok
    for label, caught in injected_bugs():
        print(f"{'CAUGHT' if caught else 'NOT-CAUGHT'}  injected: {label}")
        bad += not caught
    print("validate:", "PASS" if not bad else f"FAIL ({bad})")
    return 1 if bad else 0


def cmd_measure() -> int:
    if cmd_validate() != 0:
        print("REFUSED: estimators did not validate; no number is reported", file=sys.stderr)
        return 2
    if not REFS.is_dir():
        print(f"REFUSED: reference corpus not found at {REFS}", file=sys.stderr)
        return 2
    import test_discrimination as td
    laws = td.fit_laws(str(REFS), True)
    rows, ceilings = [], {}
    for voice in td.ALL_REF:
        clips = [c for c in td.ref_clips(str(REFS), [voice], True)]
        if not clips:
            continue
        per = {}
        for c in clips:
            x, sr = td.read_wav(c.path)
            y = td.condition(x, sr, level_match=False)
            lb = pm.lowband_level_db(y, sr)
            pr = (pm.dominant_period_ms(y, sr, (40.0, td.TUNING_FMAX[voice]))
                  if voice in PERIOD_VOICES else None)
            per[c.knobs] = dict(
                lowband=lb.value if lb.ok else None, lowband_why=None if lb.ok else lb.reason,
                period=pr.value if pr is not None and pr.ok else None,
                period_why=None if pr is None or pr.ok else pr.reason, test=c.is_test,
                # the study's own f0 reader (0.3 s window, argmax bin), as the
                # independent cross-check for the period estimator
                f0_td=(td.measure_f0(y, sr, td.TUNING_FMAX[voice])
                       if voice in PERIOD_VOICES else None))
        for metric, key in (("lowband_level_db", "lowband"), ("dominant_period_ms", "period")):
            v = [d[key] for d in per.values() if d[key] is not None]
            ceilings[f"{metric}/{voice}"] = dict(
                ceiling=(max(v) - min(v)) if len(v) > 1 else None, n=len(v), of=len(per),
                refused=len(per) - len(v))
        # ours at the held-out settings, against the machine at the same setting
        for c in clips:
            if not c.is_test or not td.KNOB_NAMES.get(voice):
                continue
            xo, so = td.render(voice, c.knobs, laws, "ours")
            yo = td.condition(xo, so, level_match=False)
            lo_ = pm.lowband_level_db(yo, so)
            po = (pm.dominant_period_ms(yo, so, (40.0, td.TUNING_FMAX[voice]))
                  if voice in PERIOD_VOICES else None)
            r = per[c.knobs]
            row = dict(voice=voice, knobs=list(c.knobs))
            if lo_.ok and r["lowband"] is not None:
                row["lowband_ours_minus_real_db"] = lo_.value - r["lowband"]
            if po is not None and po.ok and r["period"] is not None:
                row["period_ours_over_real_pct"] = 100.0 * (po.value / r["period"] - 1.0)
                row["freq_pct_sharp"] = 100.0 * (r["period"] / po.value - 1.0)
                row["freq_pct_sharp_study_f0"] = 100.0 * (
                    td.measure_f0(yo, so, td.TUNING_FMAX[voice]) / r["f0_td"] - 1.0)
            rows.append(row)
    out = dict(
        ruler="raw dB / ms of the named estimator on conditioned clips; NOT voice_scale or ours_distance",
        commit=subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                              cwd=ROOT).stdout.strip(),
        dirty=bool(subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"],
                                  capture_output=True, text=True, cwd=ROOT).stdout.strip()),
        machine_floor=pb.MACHINE_FLOOR, ceilings=ceilings, held_out=rows,
        tolerances={k: pb.band_tolerance(k.split("/")[0], k.split("/")[1], v["ceiling"])[1]
                    for k, v in ceilings.items()})
    OUT.write_text(json.dumps(out, indent=1, default=float) + "\n")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("validate", "measure"))
    sys.exit({"validate": cmd_validate, "measure": cmd_measure}[ap.parse_args().cmd]())

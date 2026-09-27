#!/usr/bin/env python3
"""Stage-by-stage unwanted-energy probe for the mono voice (issue #333, plan098 5B).

What it answers: for a held note on the fixed-point voice, how much energy at
each stage is NOT at a harmonic of the programmed pitch, in absolute dBFS and
relative to the intended (harmonic) signal -- and how much of the intended
upper spectrum survives, so a candidate cannot win by getting darker.

Why no oracle is needed in its domain. A held note with drift, noise and
modulation off is periodic at the programmed increment. Every stage the voice
has -- PolyBLEP oscillator, 2x decimator, mixer, rate converter, the tanh
ladder, VCA, volume -- is either linear time-invariant or memoryless within a
held note, so the ideal (infinite-rate) output contains energy ONLY at k * f0.
The exact f0 is computed from the increment register, not estimated. So:

  intended  = power within +-guard bins of k * f0, k * f0 < band edge
  image     = power at the predicted fold of k * f0 above the stage's Nyquist
              (aliasing, including images the tanh generates)
  residual  = everything else except DC: spurs, quantisation, dropouts
  unwanted  = image + residual (the always-valid headline)

Out of domain, and REFUSED rather than answered: silence, self-oscillation,
drift/noise/modulation, glides and transitions (time-varying: they need a
validated oracle, which this tool does not supply), and notes too low for the
analysis window to resolve harmonics.

The window is a 4-term Blackman-Harris (-92 dB sidelobes), so leakage from a
-10 dBFS harmonic stays far below the unwanted levels measured here; the
known-answer suite (tools/test_mono_artifact_probe.py) pins that.

Dimensions are reported separately and never averaged: absolute unwanted dBFS,
unwanted relative to intended, upper wanted power, clipping counts, dropout
depth, activity. Pitch is not measured here (the programmed increment is the
input), so nothing here can pay for aliasing with pitch.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import pathlib
import subprocess
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "model"), str(ROOT / "tools")]
import voice_fx as vf  # noqa: E402

SR = 48_000
PHASE_ONE = 1 << vf.PHASE_BITS
BH4 = (0.35875, 0.48829, 0.14128, 0.01168)
GUARD_BINS = 6            # BH4 main lobe is +-4 bins; +2 for f0 rounding and AM
MIN_BINS_PER_F0 = 16      # below this the harmonic masks swallow the spectrum
UPPER_BAND = (5_000.0, 20_000.0)
SILENT_DBFS = -90.0       # activity floor: well above a 16-bit LSB's -101 dBFS
DROPOUT_BLOCK = 240       # 5 ms blocks
DROPOUT_DB = -12.0        # a block this far under the median block is a dropout
ANALYSIS_VERSION = "mono-artifact-probe-v1"

# ---- controls: each must MOVE the property it names (rule 4 matrix) ---------
INJECTIONS = ("ALIAS_NOBLEP", "DARKEN_LP4K", "SPUR_M70", "CLIP_2XFS",
              "DROPOUT_10MS", "SILENCE")
MOVE_DB = 1.0             # a dB property "moved" when it changed by more than this


class Refused(RuntimeError):
    pass


def bh4(n: int) -> np.ndarray:
    t = 2.0 * np.pi * np.arange(n) / n
    a0, a1, a2, a3 = BH4
    return a0 - a1 * np.cos(t) + a2 * np.cos(2 * t) - a3 * np.cos(3 * t)


def fold(hz: float, sr: float) -> float:
    r = hz % sr
    return sr - r if r > sr / 2 else r


def _dbfs(power: float) -> float:
    return 10.0 * math.log10(max(power, 1e-30))


def split_spectrum(x, sr: float, f0: float, *, band_hz: float | None = None,
                   guard: int = GUARD_BINS, kmax_mult: int = 16) -> dict:
    """Split the mean-square power of `x` (full scale = 1.0) into intended,
    image, residual (and, for an oversampled stage, supra-band) power.

    `band_hz`: the audio band this stage must deliver (default the stage's
    Nyquist). Harmonics between band_hz and sr/2 are `supra`: legitimate at
    this rate, but content the next decimator must remove."""
    x = np.asarray(x, dtype=np.float64)
    n = len(x)
    if n < 1024:
        raise Refused("analysis window shorter than 1024 samples")
    level = _dbfs(float(np.mean(x * x)))
    if not np.isfinite(x).all():
        raise Refused("non-finite samples")
    if level < SILENT_DBFS:
        raise Refused(f"silent: {level:.1f} dBFS < activity floor {SILENT_DBFS} dBFS")
    bin_hz = sr / n
    if f0 / bin_hz < MIN_BINS_PER_F0:
        raise Refused(f"f0 {f0:.2f} Hz is {f0 / bin_hz:.1f} bins; need >= {MIN_BINS_PER_F0}")
    band = sr / 2 if band_hz is None else float(band_hz)
    w = bh4(n)
    X = np.fft.rfft((x - x.mean()) * w)
    p = np.abs(X) ** 2
    one_sided = np.full(len(p), 2.0)
    one_sided[0] = 1.0
    if n % 2 == 0:
        one_sided[-1] = 1.0
    scale = float(n * n * np.mean(w * w))
    pw = p * one_sided / scale                     # mean-square per bin, Parseval-exact
    nb = len(pw)

    def mask_at(hz):
        c = int(round(hz / bin_hz))
        return max(0, c - guard), min(nb, c + guard + 1)

    intended = np.zeros(nb, bool)
    supra = np.zeros(nb, bool)
    harmonics = {}
    k = 1
    while k * f0 < sr / 2 - guard * bin_hz:
        lo, hi = mask_at(k * f0)
        (intended if k * f0 < band else supra)[lo:hi] = True
        if k * f0 < band:
            harmonics[k] = float(pw[lo:hi].sum())
        k += 1
    kn = k - 1
    image = np.zeros(nb, bool)
    collided = used = 0
    for k in range(kn + 1, kn * kmax_mult + 1):
        fa = fold(k * f0, sr)
        if fa < guard * bin_hz or fa > sr / 2 - guard * bin_hz:
            continue
        lo, hi = mask_at(fa)
        if intended[lo:hi].any() or supra[lo:hi].any():
            collided += 1
            continue
        image[lo:hi] = True
        used += 1
    dc = np.zeros(nb, bool)
    dc[:guard + 1] = True
    residual = ~(intended | supra | image | dc)
    p_int = float(pw[intended].sum())
    p_img = float(pw[image].sum())
    p_res = float(pw[residual].sum())
    p_sup = float(pw[supra].sum())
    freqs = np.arange(nb) * bin_hz
    up = intended & (freqs >= UPPER_BAND[0]) & (freqs < min(UPPER_BAND[1], band))
    p_up = float(pw[up].sum())
    hk = np.array(sorted(harmonics))
    hp = np.array([harmonics[k] for k in hk])
    centroid = float((hk * f0 * hp).sum() / hp.sum()) if hp.sum() > 0 else 0.0
    h1 = harmonics.get(1, 0.0)
    return {
        "f0_hz": round(f0, 6), "sr": sr, "band_hz": band, "samples": n,
        "bin_hz": round(bin_hz, 6), "guard_bins": guard,
        "level_dbfs": round(level, 4),
        "intended_dbfs": round(_dbfs(p_int), 4),
        "image_dbfs": round(_dbfs(p_img), 4),
        "residual_dbfs": round(_dbfs(p_res), 4),
        "unwanted_dbfs": round(_dbfs(p_img + p_res), 4),
        "supra_dbfs": round(_dbfs(p_sup), 4) if supra.any() else None,
        "image_rel_db": round(_dbfs(p_img) - _dbfs(p_int), 4),
        "residual_rel_db": round(_dbfs(p_res) - _dbfs(p_int), 4),
        "unwanted_rel_db": round(_dbfs(p_img + p_res) - _dbfs(p_int), 4),
        "upper_wanted_dbfs": round(_dbfs(p_up), 4) if up.any() else None,
        "upper_wanted_rel_db": round(_dbfs(p_up) - _dbfs(p_int), 4) if up.any() else None,
        "harmonic_centroid_hz": round(centroid, 3),
        "harmonics_rel_h1_db": {f"h{k}": round(_dbfs(harmonics[k]) - _dbfs(h1), 3)
                                for k in list(hk)[:16]},
        "harmonics_below_band": int(len(hk)),
        "images_used": used, "images_collided": collided,
        "mask_fraction": round(float((intended | supra | image).mean()), 4),
    }


def dropout_depth_db(x) -> float:
    """Worst 5 ms block RMS relative to the median block, in dB (0 = steady)."""
    x = np.asarray(x, dtype=np.float64)
    m = len(x) // DROPOUT_BLOCK
    if m < 8:
        raise Refused("window too short for dropout blocks")
    blocks = x[:m * DROPOUT_BLOCK].reshape(m, DROPOUT_BLOCK)
    r = np.sqrt(np.mean(blocks * blocks, axis=1))
    med = float(np.median(r))
    if med <= 0:
        raise Refused("silent: median block RMS is zero")
    return round(20.0 * math.log10(max(float(r.min()), 1e-12) / med), 3)


# ---- rendering -----------------------------------------------------------------
ENGINES = {
    # R1 as published: OSC2X=1 FILTER2X=1 PULSE2X=0 (fpga/release/r1-candidate.json)
    "r1": dict(oversample_2x=True, rate_converted_ladder=True, preserve_filter_headroom=True,
               causal_filter=True, pulse479_filter_candidate=True, oversample_pulse_2x=False),
    # the #192/#205 pulse-enabled candidate: OSC2X=1 FILTER2X=1 PULSE2X=1
    "pulse2x": dict(oversample_2x=True, rate_converted_ladder=True, preserve_filter_headroom=True,
                    causal_filter=True, pulse479_filter_candidate=True, oversample_pulse_2x=True),
}


def make_voice(engine: str, *, blep: bool = True) -> vf.VoiceFx:
    if engine not in ENGINES:
        raise Refused(f"unknown engine {engine!r}")
    return vf.VoiceFx(blep=blep, ladder_cfg={**vf.LADDER_CFG, "oversample": 2},
                      **ENGINES[engine])


def osc_f0(voice: vf.VoiceFx, inc: int, shape: str) -> float:
    """Exact programmed pitch. The 2x chain advances 2*floor(inc/2) per frame."""
    two_x = voice.oversample_2x and (shape == "saw" or
                                     (voice.oversample_pulse_2x and shape in vf.TWO_EDGE))
    step = 2 * (inc // 2) if two_x else inc
    return step * SR / PHASE_ONE


def held_patch(**over) -> dict:
    """A steady single-oscillator patch: sustain 1.0 on both envelopes so the
    analysis window is stationary. Everything else is an explicit parameter."""
    p = dict(waves=("saw", "saw", "saw"), detune=(0.0, 0.0, 0.0), mix=(1.0, 0.0, 0.0),
             noise=0.0, cutoff=(20000, 20000), q=0.0, drive=0.75,
             amp=(0.004, 0.05, 1.0, 0.1), fenv=(0.004, 0.05, 1.0, 0.1), track=0.0, vol=0.45,
             mod_mix=0.0, mod_wheel=0.0, osc_mod=False, filt_mod=False)
    p.update(over)
    return p


def render_held(engine: str, note: int, patch: dict, *, blep: bool = True,
                dur: float = 1.4, start: float = 0.5, n_fft: int = 36_000) -> dict:
    """Render one held note and return the stage arrays and exact f0."""
    if patch.get("mod_wheel") or patch.get("noise") or patch.get("drift_cents"):
        raise Refused("modulation, noise and drift are outside the stationary domain")
    if float(patch.get("q", 0.0)) >= 0.97:
        raise Refused("resonance at or above the self-oscillation boundary is out of domain")
    voice = make_voice(engine, blep=blep)
    voice.reset()
    cap = {}
    inner = getattr(voice.ladder, "_ladder", None)
    if inner is not None:                               # rate-converted: capture the 96 kHz stage
        orig = inner.process

        def process(x_hi, *a, **kw):
            y_hi = orig(x_hi, *a, **kw)
            cap.setdefault("x_hi", []).append(np.asarray(x_hi, dtype=np.int64).copy())
            cap.setdefault("y_hi", []).append(np.asarray(y_hi, dtype=np.int64).copy())
            return y_hi
        inner.process = process
    r = voice.note_on(note, dur, gate=dur, **patch)
    out = voice.run(r)
    tr = voice.trace
    if len(tr["osc"][0]) != len(out):
        raise Refused("the voice rendered in several chunks; the trace covers only the last")
    a = int(start * SR)
    b = a + n_fft
    if b > len(out):
        raise Refused("analysis window runs past the render")
    incs = [int(np.asarray(tr["incs"][k])[a]) for k in range(3)]
    shape0 = voice.oscs[0].shape
    f0 = osc_f0(voice, incs[0], shape0)
    for k in (1, 2):
        if voice.weights[k] and osc_f0(voice, incs[k], voice.oscs[k].shape) != f0:
            raise Refused("oscillators at different pitches: multi-f0 patches are out of domain")
    stages = {
        "oscillator": (np.asarray(tr["osc"][0], dtype=np.float64)[a:b] / 32768.0, SR, None),
        "mixer": (np.asarray(tr["mixed"], dtype=np.float64)[a:b] / 32768.0, SR, None),
    }
    if cap:
        xh = np.concatenate(cap["x_hi"]).astype(np.float64) / 32768.0
        yh = np.concatenate(cap["y_hi"]).astype(np.float64) / 32768.0
        stages["ladder_in_96k"] = (xh[2 * a:2 * b], 2 * SR, SR / 2)
        stages["ladder_out_96k"] = (yh[2 * a:2 * b], 2 * SR, SR / 2)
    stages["ladder_out"] = (np.asarray(tr["ladder"], dtype=np.float64)[a:b] / 32768.0, SR, None)
    stages["output"] = (np.asarray(out, dtype=np.float64)[a:b] / 32768.0, SR, None)
    clip = {
        "output_rail_samples": int(np.count_nonzero((out[a:b] >= 32767) | (out[a:b] <= -32768))),
        "oscillator_rail_samples": int(np.count_nonzero(np.abs(np.asarray(tr["osc"][0])[a:b]) >= 32767)),
        "reconstruction_would_clip": (tr.get("filter_reconstruction") or {}).get("would_clip_count"),
        "decimation_would_clip": (tr.get("filter_decimation") or {}).get("would_clip_count"),
    }
    return {"f0": f0, "inc": incs[0], "stages": stages, "clip": clip, "voice": voice}


def _apply_output_injection(name: str, y: np.ndarray, f0: float) -> np.ndarray:
    """Output-side defects. ALIAS_NOBLEP is a model mutation, handled at render."""
    y = y.copy()
    if name == "DARKEN_LP4K":                      # loss of wanted harmonics
        from scipy.signal import butter, sosfilt
        y = sosfilt(butter(2, 4000, fs=SR, output="sos"), y)
    elif name == "SPUR_M70":                       # a quiet steady spurious tone
        hz = 0.5 * f0 + 3131.0
        kn = int(SR / 2 / f0)
        busy = [k * f0 for k in range(1, kn + 2)] + [fold(k * f0, SR) for k in range(kn + 1, 16 * kn + 1)]
        while min(abs(hz - b) for b in busy) < 40:
            hz += 7.0
        y = y + math.sqrt(2) * 10 ** (-70 / 20) * np.sin(2 * np.pi * hz * np.arange(len(y)) / SR)
    elif name == "CLIP_2XFS":                      # clipping outside intentional drive
        # overdrive to twice full scale whatever the preset level, then the rail
        y = np.clip(np.round(y * (2.0 / max(np.max(np.abs(y)), 1e-9)) * 32768.0),
                    -32768, 32767) / 32768.0
    elif name == "DROPOUT_10MS":
        m = len(y) // 2
        y[m:m + 480] = 0.0
    elif name == "SILENCE":
        y = np.zeros_like(y)
    elif name not in ("", "ALIAS_NOBLEP"):
        raise Refused(f"unknown injection {name!r}")
    return y


def measure_point(engine: str, note: int, patch: dict, *, inject: str = "") -> dict:
    """All stages for one held note, plus output-level clipping/dropout/activity.
    A silent output fails ACTIVITY before any artifact number is reported."""
    if inject and inject not in INJECTIONS:
        raise Refused(f"unknown injection {inject!r}")
    r = render_held(engine, note, patch, blep=(inject != "ALIAS_NOBLEP"))
    f0 = r["f0"]
    out_x = _apply_output_injection(inject, r["stages"]["output"][0], f0)
    row = {"engine": engine, "note": note, "inc": r["inc"], "f0_hz": round(f0, 6),
           "patch": {k: patch[k] for k in ("waves", "cutoff", "q", "drive", "vol", "mix")},
           "inject": inject or None, "stages": {}, "clip": r["clip"]}
    level = _dbfs(float(np.mean(out_x * out_x)))
    row["activity"] = {"output_level_dbfs": round(level, 3), "ok": level >= SILENT_DBFS}
    if not row["activity"]["ok"]:
        row["verdict"] = "FAIL-ACTIVITY"
        return row
    row["clip"]["output_rail_samples"] = int(np.count_nonzero(np.abs(out_x) >= 32767 / 32768))
    row["dropout_depth_db"] = dropout_depth_db(out_x)
    for name, (x, sr, band) in r["stages"].items():
        if name == "output":
            x = out_x
        try:
            row["stages"][name] = split_spectrum(x, sr, f0, band_hz=band)
        except Refused as e:
            row["stages"][name] = {"refused": str(e)}
    row["verdict"] = "MEASURED"
    return row


# ---- controls: properties x defects (verification-rules.md rule 4) -------------
PROPERTIES = {
    "unwanted_dbfs": lambda r: r["stages"]["output"].get("unwanted_dbfs"),
    "image_dbfs": lambda r: r["stages"]["output"].get("image_dbfs"),
    "residual_dbfs": lambda r: r["stages"]["output"].get("residual_dbfs"),
    "upper_wanted_rel_db": lambda r: r["stages"]["output"].get("upper_wanted_rel_db"),
    "output_rail_samples": lambda r: r["clip"]["output_rail_samples"],
    "dropout_depth_db": lambda r: r["dropout_depth_db"],
}
EXPECTED = {   # the property each control exists to move
    "ALIAS_NOBLEP": "image_dbfs", "DARKEN_LP4K": "upper_wanted_rel_db",
    "SPUR_M70": "residual_dbfs", "CLIP_2XFS": "output_rail_samples",
    "DROPOUT_10MS": "dropout_depth_db", "SILENCE": "activity",
}


def _moved(prop, clean, dirty) -> bool:
    if clean is None or dirty is None:
        return clean is not dirty
    if prop == "output_rail_samples":
        return dirty != clean
    return abs(dirty - clean) > MOVE_DB


def controls(engine="r1", note=84, wave="saw") -> dict:
    """Run every injection on one canary; exit status says whether each control
    moved its intended property on a clean baseline that itself measured."""
    patch = held_patch(waves=(wave,) * 3)
    clean = measure_point(engine, note, patch)
    if clean["verdict"] != "MEASURED":
        raise Refused("clean control baseline did not measure")
    matrix, caught = {}, {}
    for inj in INJECTIONS:
        dirty = measure_point(engine, note, patch, inject=inj)
        if inj == "SILENCE":
            caught[inj] = dirty["verdict"] == "FAIL-ACTIVITY"
            matrix[inj] = {p: ("REFUSED" if dirty["verdict"] == "FAIL-ACTIVITY" else "MEASURED")
                           for p in PROPERTIES}
            continue
        row = {}
        for p, get in PROPERTIES.items():
            c, d = get(clean), get(dirty)
            row[p] = {"clean": c, "dirty": d, "state": "MOVED" if _moved(p, c, d) else "BLIND"}
        matrix[inj] = row
        caught[inj] = row[EXPECTED[inj]]["state"] == "MOVED"
    return {"canary": {"engine": engine, "note": note, "wave": wave},
            "clean_output": clean["stages"]["output"], "matrix": matrix, "caught": caught,
            "all_caught": all(caught.values())}


# ---- the sweep schedule -----------------------------------------------------------
CANARY_NOTES = (84, 96)
RANGE_NOTES = (24, 36, 48, 60, 72, 84, 96, 108, 120, 127)
WAVES = ("saw", "pulse29", "square", "pulse15", "pulse25", "tri", "sine", "shark", "revsaw")
R1_WAVES = ("saw", "pulse29", "square")   # audible in R1's supported sets


def schedule() -> list:
    """Stage 1: every note x wave x engine at the M5A preset's filter settings
    (drive 0.75, q 0, cutoff 20 kHz). Stage 2: boundary/pairwise over cutoff,
    resonance and drive at the canaries plus the range ends, R1 waves only."""
    pts = []
    for engine in ENGINES:
        for wave in WAVES:
            if engine == "pulse2x" and wave not in vf.TWO_EDGE:
                continue                   # identical to r1 by construction (tested below)
            for note in RANGE_NOTES:
                pts.append(("grid", engine, note, dict(waves=(wave,) * 3)))
    # pairwise over (cutoff, q, drive) x note x wave: every pair of levels appears
    cut = (400, 4000, 20000, 21600)
    qs = (0.0, 0.5, 0.9)
    drv = (0.75, 1.6, 4.0)
    notes = (36, 84, 96, 120)
    rows = []
    for i, c in enumerate(cut):
        for j, q in enumerate(qs):
            for kd, d in enumerate(drv):
                rows.append((c, q, d))
    for engine in ENGINES:
        for wave in R1_WAVES:
            if engine == "pulse2x" and wave == "saw":
                continue
            for idx, (c, q, d) in enumerate(rows):
                note = notes[idx % len(notes)]
                pts.append(("pairwise", engine, note,
                            dict(waves=(wave,) * 3, cutoff=(c, c), q=q, drive=d)))
    return pts


def run_sweep(out: pathlib.Path, part: int = 0, parts: int = 1) -> dict:
    rows = []
    for i, (group, engine, note, over) in enumerate(schedule()):
        if i % parts != part:
            continue
        patch = held_patch(**over)
        try:
            row = measure_point(engine, note, patch)
        except Refused as e:
            row = {"engine": engine, "note": note, "patch": over, "verdict": "REFUSED",
                   "reason": str(e)}
        row["group"] = group
        rows.append(row)
        o = row.get("stages", {}).get("output", {})
        print(f"{group:8s} {engine:7s} {over['waves'][0]:8s} n{note:3d} "
              f"c{over.get('cutoff', (20000,))[0]:5d} q{over.get('q', 0.0):.2f} d{over.get('drive', 0.75):.2f} "
              f"{row['verdict']:13s} unwanted {o.get('unwanted_dbfs')} dBFS "
              f"rel {o.get('unwanted_rel_db')} dB", flush=True)
    return {"rows": rows}


def provenance() -> dict:
    srcs = ["tools/mono_artifact_probe.py", "model/voice_fx.py", "model/filter_rate_chain.py",
            "model/fixed.py"]
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                          text=True).stdout.strip()
    dirty = subprocess.run(["git", "diff", "--quiet", "HEAD", "--", *srcs], cwd=ROOT).returncode
    return {"analysis_version": ANALYSIS_VERSION, "commit": head, "sources_dirty": dirty != 0,
            "sha256": {s: hashlib.sha256((ROOT / s).read_bytes()).hexdigest()[:16] for s in srcs}}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("point")
    s.add_argument("--engine", default="r1", choices=tuple(ENGINES))
    s.add_argument("--note", type=int, required=True)
    s.add_argument("--wave", default="saw")
    s.add_argument("--cutoff", type=int, default=20000)
    s.add_argument("--q", type=float, default=0.0)
    s.add_argument("--drive", type=float, default=0.75)
    s.add_argument("--inject", default="")
    c = sub.add_parser("controls")
    c.add_argument("--engine", default="r1", choices=tuple(ENGINES))
    c.add_argument("--note", type=int, default=84)
    c.add_argument("--wave", default="saw")
    w = sub.add_parser("sweep")
    w.add_argument("--part", type=int, default=0)
    w.add_argument("--parts", type=int, default=1)
    for p in (s, c, w):
        p.add_argument("--out", type=pathlib.Path, required=True)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "point":
            res = measure_point(a.engine, a.note, held_patch(
                waves=(a.wave,) * 3, cutoff=(a.cutoff, a.cutoff), q=a.q, drive=a.drive),
                inject=a.inject)
            rc = 0 if res["verdict"] == "MEASURED" else 1
        elif a.cmd == "controls":
            res = controls(a.engine, a.note, a.wave)
            for inj, ok in res["caught"].items():
                print(f"{inj:14s} {'CAUGHT' if ok else 'MISSED'}  expected {EXPECTED[inj]}")
            rc = 0 if res["all_caught"] else 1
        else:
            res = run_sweep(a.out, a.part, a.parts)
            rc = 0
    except Refused as e:
        print(f"REFUSED: {e}")
        return 2
    res["provenance"] = provenance()
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(res, indent=1, default=float) + "\n")
    print(f"wrote {a.out} (exit {rc})")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())

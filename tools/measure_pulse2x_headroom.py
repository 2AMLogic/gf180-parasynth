#!/usr/bin/env python3
"""Pulse2x decimator headroom: clipped energy at the 2x oscillator's Q1.15 rail (#333).

The 2x chain scales every 96 kHz substep by `_OS2_SUBSTEP_GAIN_Q15` (0.85,
chosen on a SAW MIDI sweep) before the 31-tap decimator, whose output
saturates to Q1.15. A band-limited rectangle rings further than a saw (its
Gibbs peak is ~1.18x the plateau, so 0.85 * 1.18 > 1), so the rectangle
reaches the rail near the top of the range.

What this measures, per waveform x MIDI note x candidate gain, from the
decimator itself (`voice_fx._render_2x`, unchanged):
  peak       largest |unsaturated| decimator output, LSB (rail 32767)
  clipped    samples the saturation changed
  clip_db    energy removed by the saturation relative to the output energy
Candidates are applied either to rectangles only ("rect") or to every 2x
waveform ("all"); "rect" leaves the saw -- and so R1 -- bit-identical.

It also renders the complete M5A/M5B phrases with each candidate through the
selected scorer, because a gain change is a level change and the M5 Gain
property must be read, not assumed. Nothing here changes voice_fx or RTL.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import math
import pathlib
import subprocess
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "model"), str(ROOT / "tools")]
import voice_fx as vf  # noqa: E402

BASE_Q15 = vf._OS2_SUBSTEP_GAIN_Q15
SHAPES = ("pulse479", "square", "pulse29", "pulse25", "pulse15", "saw")
SR = 48_000


def q15(g: float) -> int:
    return int(round(g * 32768))


@contextlib.contextmanager
def candidate(gain_q15: int | None, scope: str):
    """Apply a substep gain inside _render_2x for rectangles ('rect') or every
    2x waveform ('all'); None is the unchanged engine."""
    if gain_q15 is None:
        yield
        return
    if scope not in ("rect", "all"):
        raise ValueError(scope)
    orig = vf._render_2x

    def patched(o, *a, **kw):
        if scope == "all" or o.shape in vf.TWO_EDGE:
            saved = vf._OS2_SUBSTEP_GAIN_Q15
            vf._OS2_SUBSTEP_GAIN_Q15 = gain_q15
            try:
                return orig(o, *a, **kw)
            finally:
                vf._OS2_SUBSTEP_GAIN_Q15 = saved
        return orig(o, *a, **kw)
    vf._render_2x = patched
    try:
        yield
    finally:
        vf._render_2x = orig


def decimator_run(shape: str, note: float, gain_q15: int, n: int = 24_000) -> dict:
    """One held note straight through _render_2x; the unsaturated decimator
    output is recorded from its final sat16 call."""
    seen = []
    orig_sat = vf.sat16

    def rec(v):
        seen.append(np.asarray(v).copy())
        return orig_sat(v)
    o = vf.OscFx(shape)
    inc = vf.phase_inc(vf.note_hz(note))
    saved = vf._OS2_SUBSTEP_GAIN_Q15
    vf._OS2_SUBSTEP_GAIN_Q15 = gain_q15
    vf.sat16 = rec
    try:
        y, _, _ = vf._render_2x(o, n, inc, np.zeros(30, dtype=np.int64), 0)
    finally:
        vf.sat16 = orig_sat
        vf._OS2_SUBSTEP_GAIN_Q15 = saved
    u = np.asarray(seen[-1], dtype=np.float64)[240:]       # drop the FIR fill
    s = np.asarray(y, dtype=np.float64)[240:]
    if not np.array_equal(np.clip(u, -32768, 32767), s):
        raise RuntimeError("recorded pre-saturation samples do not produce the output")
    diff = u - s
    e_sig = float(np.sum(s * s))
    if e_sig <= 0:
        raise RuntimeError(f"{shape} MIDI {note}: silent decimator output")
    e_clip = float(np.sum(diff * diff))
    return {"peak": int(np.max(np.abs(u))), "clipped": int(np.count_nonzero(diff)),
            "clip_db": round(10 * math.log10(e_clip / e_sig), 2) if e_clip else None,
            "rms_dbfs": round(10 * math.log10(e_sig / len(s) / 32768.0 ** 2), 3)}


def decimator_sweep(gains, shapes=SHAPES, notes=range(0, 128)) -> dict:
    out = {}
    for g in gains:
        gq = q15(g)
        for shape in shapes:
            rows = {nt: decimator_run(shape, nt, gq) for nt in notes}
            clipped = [nt for nt, r in rows.items() if r["clipped"]]
            worst = max(rows.items(), key=lambda kv: kv[1]["peak"])
            out[f"{g:.3f}/{shape}"] = {
                "gain": g, "gain_q15": gq, "shape": shape,
                "notes_clipping": clipped, "clipped_samples": sum(r["clipped"] for r in rows.values()),
                "worst_clip_db": max((r["clip_db"] for r in rows.values() if r["clip_db"] is not None),
                                     default=None),
                "worst_peak": worst[1]["peak"], "worst_peak_note": worst[0],
                "headroom_db": round(20 * math.log10(32767 / worst[1]["peak"]), 3),
                "rows": rows}
            s = out[f"{g:.3f}/{shape}"]
            print(f"gain {g:.3f} {shape:8s}: clipping notes {len(clipped):3d} "
                  f"{(clipped[0], clipped[-1]) if clipped else ''} samples {s['clipped_samples']:6d} "
                  f"worst clip {s['worst_clip_db']} dB; peak {s['worst_peak']} @ MIDI {s['worst_peak_note']} "
                  f"({s['headroom_db']:+.2f} dB)", flush=True)
    return out


# Rectangles a FILTER2X=1 PULSE2X=1 image can play: the pulse29 control is
# 47.9 % under VOICE_FILTER_2X's DUTY_WIDE encoding, so a true 29 % pulse is
# not reachable there (it is kept in the coarse sweep as a diagnostic).
REACHABLE = ("square", "pulse479", "pulse25", "pulse15")
MARGIN_DB = 0.1      # frozen before the fine sweep: worst peak must sit this far below the rail


def fine_sweep(gains, lo=90.0, hi=127.0, step=0.125, n=48_000) -> dict:
    """Every reachable rectangle on a 1/8-semitone grid over the top of the
    range, which catches increments between notes (glides) that an integer
    sweep misses. Selection rule, frozen: the largest gain with zero clipped
    samples and worst peak <= rail - MARGIN_DB on every reachable shape."""
    limit = 32767 * 10 ** (-MARGIN_DB / 20)
    notes = list(np.arange(lo, hi + 1e-9, step))
    out = {}
    for g in sorted(gains, reverse=True):
        per = {}
        for shape in REACHABLE:
            rows = [(nt, decimator_run(shape, nt, q15(g), n)) for nt in notes]
            worst = max(rows, key=lambda r: r[1]["peak"])
            per[shape] = {"worst_peak": worst[1]["peak"], "worst_note": round(float(worst[0]), 3),
                          "clipped_samples": sum(r["clipped"] for _, r in rows),
                          "notes_clipping": [round(float(nt), 3) for nt, r in rows if r["clipped"]]}
        ok = all(v["clipped_samples"] == 0 and v["worst_peak"] <= limit for v in per.values())
        out[f"{g:.3f}"] = {"gain": g, "gain_q15": q15(g), "passes_rule": ok, "shapes": per}
        print(f"gain {g:.3f} (Q15 {q15(g)}): {'PASS' if ok else 'fail'} "
              + "; ".join(f"{s} peak {v['worst_peak']}@{v['worst_note']} clip {v['clipped_samples']}"
                          for s, v in per.items()), flush=True)
    chosen = next((v for v in out.values() if v["passes_rule"]), None)
    return {"rule": f"largest gain: zero clipped samples, worst peak <= rail - {MARGIN_DB} dB, "
                    f"reachable rectangles, MIDI {lo}..{hi} step {step}",
            "chosen": chosen and {"gain": chosen["gain"], "gain_q15": chosen["gain_q15"]},
            "candidates": out}


def probe_points(gain: float, notes=(36, 60, 84, 96, 108, 114, 120, 127)) -> dict:
    """Stage/output artifact numbers (mono_artifact_probe) for the reachable
    rectangles on the pulse2x engine, as built (0.85) and with the candidate
    rectangle gain: unwanted energy, upper wanted power and oscillator rail."""
    import mono_artifact_probe as mp
    res = {}
    for shape in ("square", "pulse29", "pulse25", "pulse15"):
        for nt in notes:
            row = {}
            for label, g in (("0.850", None), (f"{gain:.3f}", q15(gain))):
                with candidate(g, "rect"):
                    r = mp.measure_point("pulse2x", nt, mp.held_patch(waves=(shape,) * 3))
                o = r["stages"]["output"]
                row[label] = {"osc_rail": r["clip"]["oscillator_rail_samples"],
                              "unwanted_dbfs": o["unwanted_dbfs"], "unwanted_rel_db": o["unwanted_rel_db"],
                              "intended_dbfs": o["intended_dbfs"], "upper_wanted_rel_db": o["upper_wanted_rel_db"]}
            res[f"{shape}/{nt}"] = row
            a, b = row["0.850"], row[f"{gain:.3f}"]
            print(f"{shape:8s} n{nt:3d} osc rail {a['osc_rail']:5d}->{b['osc_rail']:5d}  unwanted rel "
                  f"{a['unwanted_rel_db']:7.2f}->{b['unwanted_rel_db']:7.2f}  intended {a['intended_dbfs']:6.2f}->"
                  f"{b['intended_dbfs']:6.2f}  upper {a['upper_wanted_rel_db']}->{b['upper_wanted_rel_db']}", flush=True)
    return res


def mix_check(gains, scope="rect", notes=(24, 36, 48, 60, 72, 84, 96, 108, 120, 127)) -> dict:
    """The default preset (saw, saw+7c, square-12) and a three-rectangle mix
    through the pulse2x voice: rail samples at the mixer and the output."""
    patches = {
        "default": dict(),
        "three-rect": dict(waves=("pulse29", "square", "pulse15"), detune=(0.0, 0.07, -12.0),
                           mix=(1.0, 1.0, 1.0), q=0.0, drive=0.75, cutoff=(20000, 20000),
                           amp=(0.004, 0.05, 1.0, 0.1), fenv=(0.004, 0.05, 1.0, 0.1)),
    }
    res = {}
    for g in [None] + list(gains):
        for name, patch in patches.items():
            for nt in notes:
                with candidate(None if g is None else q15(g), scope):
                    v = vf.VoiceFx(oversample_2x=True, rate_converted_ladder=True,
                                   preserve_filter_headroom=True, causal_filter=True,
                                   pulse479_filter_candidate=True, oversample_pulse_2x=True,
                                   ladder_cfg={**vf.LADDER_CFG, "oversample": 2})
                    out = v.note(nt, 1.0, gate=1.0, **patch)
                tr = v.trace
                mixed = np.asarray(tr["mixed"])
                osc_rail = sum(int(np.count_nonzero(np.abs(np.asarray(x)) >= 32767)) for x in tr["osc"])
                res[f"{'base' if g is None else f'{g:.3f}'}/{name}/{nt}"] = {
                    "osc_rail": osc_rail,
                    "mixer_rail": int(np.count_nonzero(np.abs(mixed) >= 32767)),
                    "output_rail": int(np.count_nonzero((out >= 32767) | (out <= -32768))),
                    "output_rms_dbfs": round(20 * math.log10(max(float(np.sqrt(np.mean(
                        (out[12000:].astype(float) / 32768) ** 2))), 1e-12)), 3)}
        tot = {k: sum(r[k] for kk, r in res.items() if kk.startswith(f"{'base' if g is None else f'{g:.3f}'}/"))
               for k in ("osc_rail", "mixer_rail", "output_rail")}
        print(f"mix gain {g}: {tot}", flush=True)
    return res


@contextlib.contextmanager
def pulse_drive(mult):
    """Pulse segments' ladder drive x mult (the #333 level repair for
    rectangle-only presets); saw segments untouched."""
    import mono_m5a_score as score
    if mult is None:
        yield
        return
    orig = score._patch_for_wave

    def pfw(patch, wave, pulse_shape=score.M5A_PULSE_WAVE):
        p = orig(patch, wave, pulse_shape)
        return {**p, "drive": p["drive"] * mult} if wave == "pulse" else p
    score._patch_for_wave = pfw
    try:
        yield
    finally:
        score._patch_for_wave = orig


def phrases(gains, scope="rect", drive_comp=None) -> dict:
    import mono_m5a_score as score
    res = {}
    for case in ("M5A", "M5B"):
        for g in [None] + list(gains):
            with candidate(None if g is None else q15(g), scope), pulse_drive(drive_comp if g is not None else None):
                m = score.measure(case_id=case, voice_factory=lambda: vf.VoiceFx(
                    oversample_2x=True, rate_converted_ladder=True, preserve_filter_headroom=True,
                    causal_filter=True, pulse479_filter_candidate=True, oversample_pulse_2x=True,
                    ladder_cfg={**vf.LADDER_CFG, "oversample": 2}),
                    model_label="pulse2x-headroom", output_path=ROOT / f"build/headroom/{case}-{g}.wav")
            key = f"{case}/{'0.850 (pulse2x as built)' if g is None else f'{g:.3f}'}"
            res[key] = {"errors": {k: v["error"] for k, v in m["metrics"].items()},
                        "pulse_gain_db": {f"{e['wave']} {e['midi']}": e["gain_dbfs"]
                                          for e in m["event_diagnostics"] if e["wave"] == "pulse"},
                        "pulse_foldback_excess_db": {
                            f"{e['wave']} {e['midi']}": e["foldback_db"]["excess_over_reference_db"]
                            for e in m["event_diagnostics"] if e["wave"] == "pulse"}}
            print(key, res[key]["errors"], flush=True)
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("mode", choices=("decimator", "fine", "mix", "phrases", "probe"))
    ap.add_argument("--gains", type=float, nargs="+", default=[0.85, 0.84, 0.83, 0.82, 0.80])
    ap.add_argument("--scope", choices=("rect", "all"), default="rect")
    ap.add_argument("--pulse-drive-comp", type=float, default=None,
                    help="phrases: pulse segments' drive x this for the candidate gains (not the baseline)")
    ap.add_argument("--out", type=pathlib.Path, required=True)
    a = ap.parse_args(argv)
    if a.mode == "decimator":
        res = decimator_sweep(a.gains)
    elif a.mode == "probe":
        res = probe_points(a.gains[0])
    elif a.mode == "fine":
        res = fine_sweep(a.gains)
    elif a.mode == "mix":
        res = mix_check([g for g in a.gains if q15(g) != BASE_Q15], a.scope)
    else:
        res = phrases([g for g in a.gains if q15(g) != BASE_Q15], a.scope, a.pulse_drive_comp)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "diff", "--quiet", "HEAD", "--", "tools/measure_pulse2x_headroom.py",
                            "model/voice_fx.py"], cwd=ROOT).returncode != 0
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps({"mode": a.mode, "scope": a.scope, "base_gain_q15": BASE_Q15,
                                 "commit": head, "sources_dirty": dirty, "results": res},
                                indent=1) + "\n")
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

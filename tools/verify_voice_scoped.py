#!/usr/bin/env python3
"""verify_voice, scoped (#354): is R1's model/RTL divergence inside the qualified domain?

`rtl-sketch/verify_voice.py --set full` plays every scenario on ONE continuing
voice, so a divergence in one scenario's internal state is carried into every
later one. This driver runs the unchanged bench (its generate/simulate/compare)
on a chosen scenario list from reset:

  --extremes-index I   the I-th `extremes` scenario alone, from reset
  --domain             an in-domain stress set: each R1 preset (default,
                       m5a-saw, m5a-pulse) at the live controllers' limits --
                       CC71 resonance 0 and 1.0 through the host's own
                       ladder_regs, CC74 cutoff 40 Hz and 8 kHz, CC7 volume
                       0.9 -- with the playable range's end notes, the
                       preset's glide between them, retrigger and release.
                       Every patch and note is checked by
                       fpga/release/qualified_domain (check_patch, check_note)
                       and the run REFUSES if any is outside it.

It also reports the model's headroom at the ladder's 19-bit word and the
mixer and output rails for the scenarios it ran, so "cannot reach the
saturation that diverges" is a measured margin, not an argument.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "rtl-sketch"), str(ROOT / "model"), str(ROOT / "fpga"),
                str(ROOT / "fpga/release"), str(ROOT / "tools")]
import verify_voice as vv  # noqa: E402
import voice_fx as vf  # noqa: E402
import qualified_domain as qd  # noqa: E402

SR = vf.SR
PRESET_DRIVES = None   # filled lazily: {drive of default, m5a-saw, m5a-pulse}
Y19_RAIL = (1 << 18) - 1


def extremes_only(index: int):
    full = [s for s in vv.scenarios("full", {"extremes"})]
    if not 0 <= index < len(full):
        raise SystemExit(f"REFUSED: extremes has {len(full)} scenarios")
    return [full[index]]


def _preset_regs(name):
    import uart_host as uh
    return dict(uh.preset_regs(None if name == "default" else name))


def domain_scenarios() -> list:
    S = []
    for preset in ("default", "m5a-saw", "m5a-pulse"):
        base = _preset_regs(preset)
        qd.check_patch(base, name=preset)
        lo_note, hi_note = qd.playable_notes(base["detune"])
        for res in (0.0, 1.0):
            for cut in (40, 8000):
                regs = dict(base)
                k, g, og = vf.ladder_regs(res, regs["drive"], regs.get("filter_calibration"))
                regs.update(res=res, k=k, gain=g, ogain=og, cut_lo=cut, vol=int(round(0.9 * 32768)))
                qd.check_patch(regs, name=f"{preset} res {res} cut {cut}")
                notes = [lo_note, hi_note, 36, 84, 96, hi_note]
                for nt in notes:
                    qd.check_note(nt, regs)
                n = int(0.25 * SR)
                w = [(0, "INC", kk, v, True) for kk, v in enumerate(vf.VoiceFx.note_incs(notes[0], regs["detune"]))]
                w += [(0, "TRACK", vf.VoiceFx.note_track(notes[0], regs["track"])), (0, "GATE", 1)]
                step = n // len(notes)
                for i, nt in enumerate(notes[1:], start=1):
                    f = i * step
                    # a legato change: glide at the preset's own rate (non-jump INC)
                    w += [(f, "INC", kk, v, False) for kk, v in enumerate(vf.VoiceFx.note_incs(nt, regs["detune"]))]
                    w += [(f, "TRACK", vf.VoiceFx.note_track(nt, regs["track"]))]
                    if i == 3:
                        w += [(f + 5, "TRIG",)]
                w += [(n - step // 2, "GATE", 0)]
                S.append(("domain", f"{preset}: res {res}, cutoff {cut} Hz, vol 0.9, notes {notes}, glide, "
                                    f"retrigger, release", regs, sorted(w, key=lambda x: x[0]), n))
    return S


def _preset_drives():
    global PRESET_DRIVES
    if PRESET_DRIVES is None:
        PRESET_DRIVES = {round(float(_preset_regs(p)["drive"]), 6) for p in ("default", "m5a-saw", "m5a-pulse")}
    return PRESET_DRIVES


def classify(scns) -> list:
    """Each scenario against R1's qualified domain, as the release host would
    judge it: the patch (check_patch), every programmed increment (check_inc,
    which covers glide targets and jumps alike), and the register words the
    host's own conversions can produce (k, gain, ogain from ladder_regs over
    the CC71 range at the patch's drive; the mixer weights of mix_weights;
    volume up to CC7's 0.9)."""
    _preset_drives()
    out = []
    for key, name, regs, writes, n in scns:
        why = []
        try:
            qd.check_patch(dict(regs), name=name)
        except qd.Rejected as e:
            why.append(f"{e.rule}: {e.args[0][:90]}")
        for w in writes:
            if w[1] == "INC":
                try:
                    qd.check_inc(int(w[3]), osc=w[2])
                except qd.Rejected as e:
                    why.append(f"{e.rule}: inc {w[3]} on osc {w[2]}")
                    break
        # The live host programs k/gain/ogain ONLY through ladder_regs, from a
        # resonance in CC71's 0..1 and the loaded preset's drive (no CC moves
        # drive). So the words must be that conversion of the patch's own res.
        # (The first version demanded res be a CC step r/127, which rejected
        # the default preset's own 0.62 -- wrong, a preset loads its res.)
        res = float(regs.get("res", 0.0))
        if not 0.0 <= res <= 1.0:
            why.append(f"res {res} outside CC71's 0..1")
        if (int(regs["k"]), int(regs["gain"]), int(regs["ogain"])) != \
                tuple(vf.ladder_regs(res, regs.get("drive", 1.0), regs.get("filter_calibration"))):
            why.append(f"k/gain/ogain {regs['k']}/{regs['gain']}/{regs['ogain']} are not the host "
                       f"conversion of res {res} at drive {regs.get('drive')}")
        if round(float(regs.get("drive", 0.0)), 6) not in PRESET_DRIVES:
            why.append(f"drive {regs.get('drive')} is no release preset's ({sorted(PRESET_DRIVES)})")
        if int(regs["vol"]) > int(round(0.9 * 32768)):
            why.append(f"vol {regs['vol']} > CC7's 0.9")
        if max(int(x) for x in regs["weights"]) > (1 << 15):
            why.append(f"mixer weight {max(regs['weights'])} > 1.0")
        out.append({"scenario": name, "in_domain": not why, "excluded_by": why})
    return out


def recon_audit(scns, osc2x, filter2x, pulse2x) -> dict:
    """Frame by frame: does the model's 2x reconstruction leave the 17-bit
    range of the RTL's ladder input port (rate_conv_2x.v clamps x_even/x_odd
    to [-65536, 65535]; the model passes the raw int32)? Returns the first
    such frame per scenario and the worst magnitude."""
    import filter_rate_chain as frc
    v = vf.VoiceFx(oversample_2x=osc2x, oversample_pulse_2x=pulse2x, rate_converted_ladder=filter2x,
                   preserve_filter_headroom=filter2x, causal_filter=filter2x,
                   pulse479_filter_candidate=filter2x)
    v.reset()
    seen = []
    orig = frc.CausalRateConverter.reconstruct

    def rec(self, x):
        hi = orig(self, x)
        seen.append(np.asarray(hi, dtype=np.int64).copy())
        return hi
    frc.CausalRateConverter.reconstruct = rec
    out = []
    try:
        f0 = 0
        for key, name, regs, writes, n in scns:
            seen.clear()
            v.play(regs, writes, n)
            hi = np.concatenate(seen) if seen else np.zeros(0, dtype=np.int64)
            over = np.nonzero((hi > 65535) | (hi < -65536))[0]
            out.append({"scenario": name, "frames": [f0, f0 + n - 1],
                        "max_abs": int(np.max(np.abs(hi))) if len(hi) else 0,
                        "samples_outside_17bit": int(len(over)),
                        "first_frame_outside": None if not len(over) else f0 + int(over[0]) // 2})
            f0 += n
    finally:
        frc.CausalRateConverter.reconstruct = orig
    return {"scenarios": out}


def headroom(scns, osc2x, filter2x, pulse2x) -> dict:
    v = vf.VoiceFx(oversample_2x=osc2x, oversample_pulse_2x=pulse2x, rate_converted_ladder=filter2x,
                   preserve_filter_headroom=filter2x, causal_filter=filter2x,
                   pulse479_filter_candidate=filter2x)
    v.reset()
    worst = {"ladder_y19_abs": 0, "mixed_abs": 0, "out_abs": 0, "vca_abs": 0}
    per = []
    for key, name, regs, writes, n in scns:
        y = v.play(regs, writes, n)
        t = v.trace
        row = {"name": name,
               "ladder_y19_abs": int(np.max(np.abs(t["ladder"]))),
               "mixed_abs": int(np.max(np.abs(t["mixed"]))),
               "out_abs": int(np.max(np.abs(y))),
               "vca_abs": int(np.max(np.abs(t["vca"])))}
        per.append(row)
        for k in worst:
            worst[k] = max(worst[k], row[k])
    worst["ladder_margin_db"] = round(20 * math.log10(Y19_RAIL / max(worst["ladder_y19_abs"], 1)), 2)
    return {"worst": worst, "per_scenario": per}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--extremes-index", type=int)
    g.add_argument("--domain", action="store_true")
    g.add_argument("--recon-audit", choices=("extremes0", "domain", "full"),
                   help="report where the model's reconstruction leaves the RTL's 17-bit port, then stop")
    g.add_argument("--classify-full", action="store_true",
                   help="classify every scenario of the full set against the domain, then stop")
    ap.add_argument("--osc2x", action="store_true")
    ap.add_argument("--filter2x", action="store_true")
    ap.add_argument("--pulse2x", action="store_true")
    ap.add_argument("--rtl", default=None)
    ap.add_argument("--inject", default=None)
    ap.add_argument("--define", action="append", default=[], help="extra Verilog define (a component control)")
    ap.add_argument("--expect-fail", action="store_true")
    ap.add_argument("--headroom-only", action="store_true")
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--json", type=pathlib.Path, default=None)
    a = ap.parse_args(argv)
    if a.recon_audit:
        scns = {"extremes0": lambda: extremes_only(0), "domain": domain_scenarios,
                "full": lambda: list(vv.scenarios("full"))}[a.recon_audit]()
        res = recon_audit(scns, a.osc2x, a.filter2x, a.pulse2x)
        for r in res["scenarios"]:
            if r["samples_outside_17bit"] or a.recon_audit != "full":
                print(f"{r['scenario'][:70]:70s} max |x_hi| {r['max_abs']:7d}  outside 17-bit "
                      f"{r['samples_outside_17bit']:5d}  first frame {r['first_frame_outside']}")
        if a.json:
            a.json.write_text(json.dumps(res, indent=1) + "\n")
        return 0
    if a.classify_full:
        res = classify(list(vv.scenarios("full")))
        for r in res:
            print(("IN     " if r["in_domain"] else "OUT    ") + r["scenario"][:80]
                  + ("" if r["in_domain"] else "  <- " + "; ".join(r["excluded_by"])[:200]))
        if a.json:
            a.json.write_text(json.dumps(res, indent=1) + "\n")
        return 0
    scns = domain_scenarios() if a.domain else extremes_only(a.extremes_index)
    hr = headroom(scns, a.osc2x, a.filter2x, a.pulse2x)
    print("headroom (model):", json.dumps(hr["worst"]))
    if a.headroom_only:
        if a.json:
            a.json.write_text(json.dumps({"headroom": hr}, indent=1) + "\n")
        return 0
    vv.scenarios = lambda which, only=None: scns          # the bench, on this list, from reset
    argv2 = ["--set", "full", "--outdir", a.outdir]
    for flag in ("osc2x", "filter2x", "pulse2x"):
        if getattr(a, flag):
            argv2.append(f"--{flag}")
    if a.rtl:
        argv2 += ["--rtl", a.rtl]
    if a.inject:
        argv2 += ["--inject", a.inject]
    for d in a.define:
        argv2 += ["--define", d]
    if a.expect_fail:
        argv2.append("--expect-fail")
    rc = vv.main(argv2)
    if a.json:
        a.json.write_text(json.dumps({"headroom": hr, "verify_voice_exit": rc,
                                      "scenarios": [s[1] for s in scns]}, indent=1) + "\n")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())

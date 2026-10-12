#!/usr/bin/env python3
"""#656: the cymbal's late T20 after the RATE exponent -- localisation and confirmation.

    python3 tools/probes/cymbal_late_t20.py localise-h --out docs/scorecard/cymbal-369/knob-render/h-late-t20-localise.json
    python3 tools/probes/cymbal_late_t20.py confirm    --out docs/scorecard/cymbal-369/knob-render/confirmation.json

Both measure with the frozen instrument (`tools/cymbal_bands.measure` through
`run_case.prepare`) on `cymbal_candidate`'s shipped-structure knob images,
rendered by the integer model exactly as `cymbal_tone_render.render_shipped_knob`
does (one strike at accent 1.0, both drum buses 0.45, 7 s after the strike).

localise-h
    #656's curation: "attributing every multi-exponential refusal to that floor
    is a hypothesis until a paired intervention demonstrates it". With the floor
    gone, H's late T20 still refuses at TONE 50 (also at the anchor, which the
    floor never touched). This is the paired intervention on the remaining
    cause: the same image with ONE cymbal VCA path switched off (low / short),
    at the anchor and at DECAY 10. If H qualifies with the low path off and
    not with the short path off, H's late segment is the low band's energy
    above 6 kHz on top of E_CYD: a property of the shipped kit's band
    structure, not of the rate register.

confirm
    The DECAY gate (`cymbal_tone_render --knob-render`) selects nothing -- it is
    structural -- but it was READ on the five knob codes. These are conditions it
    never saw: (a) TONE 25 (a confirmation row in the frozen split) at nine
    wiper fractions 0, 0.125, ..., 1.0, four of them between the knob codes;
    (b) TONE 75 at the five codes with the strike 7919 frames later, so the
    free-running LFSR and the six oscillators are at different phases. The
    requirement is the gate's own: L's late T20 qualifies at every point and
    rises strictly. H is reported.

Exit 0 written (whatever the verdict says), 2 REFUSED.
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import subprocess
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "model"), str(ROOT / "tools"), str(ROOT / "audition")]
import drums_fx as dx  # noqa: E402
import cymbal_candidate as cc  # noqa: E402
import cymbal_bands as cb  # noqa: E402
import run_case as rc  # noqa: E402

SECONDS_AFTER = 7.0
PHASE_SHIFT_FRAMES = 7919
WIPERS = tuple(i / 8 for i in range(9))


def render(kit, hit_frame: int = rc.DRUM_SOLO_HIT_FRAME) -> np.ndarray:
    n = hit_frame + int(SECONDS_AFTER * dx.SR)
    dm, bd = dx.DrumsFx().play(dx.hit_writes([(hit_frame, dx.SOUND_STOP["CY"], 1.0)], kit), n)
    g = dx.accent_reg(0.45)
    return np.asarray(dx.output_fx(np.zeros(n), 0, dm, g, bd, g), dtype=np.float64) / 32768.0


def measure(y, label: str) -> dict:
    m = cb.measure(rc.prepare(y, dx.SR, side=label), dx.SR)
    return {b: {"edt10_ms": m[b]["edt10_ms"], "t20_late_ms": m[b]["t20_late_ms"],
                "late_residual_db": m[b].get("late_residual_db"),
                "t20_refused": m[b].get("t20_refused"),
                "energy_share_db": m[b]["energy_share_db"]} for b in ("H", "L")}


def _finite(v) -> bool:
    return isinstance(v, (int, float)) and math.isfinite(v)


def rises(vals: list) -> dict:
    if not all(_finite(v) for v in vals):
        return {"status": "REFUSED", "refused_at": [i for i, v in enumerate(vals) if not _finite(v)]}
    ok = all(b > a for a, b in zip(vals, vals[1:]))
    return {"status": "PASS" if ok else "FAIL"}


def provenance() -> dict:
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "diff", "--quiet", "HEAD", "--", "model", "tools"], cwd=ROOT).returncode != 0
    return {"commit": head, "sources_dirty": dirty, "rate_exp_bits": dx.RATE_EXP_BITS}


def localise_h() -> dict:
    off = dx.path_word(dx.SRC_OFF, dx.ENV_NONE, dest=dx.DEST_MIX)
    out = {}
    for d in ("25", "10"):
        base = dict(cc.shipped_knob_kit(cc.TONE_ANCHOR, d))
        for label, drop in (("all", ()), ("no-low", (cc.P_CYL,)), ("no-short", (cc.P_CYS,))):
            img = dict(base)
            for p in drop:
                img[dx.A_PATH + p] = off
            r = measure(render(sorted(img.items())), f"CY50{d} {label}")
            out[f"D{d}/{label}"] = r
            print(f"DECAY {d} {label:9s} H T20 {r['H']['t20_late_ms']} resid {r['H']['late_residual_db']} | "
                  f"L T20 {r['L']['t20_late_ms']} resid {r['L']['late_residual_db']}", flush=True)
    q = lambda k: _finite(out[k]["H"]["t20_late_ms"])        # noqa: E731
    finding = {d: {"all_qualifies": q(f"D{d}/all"), "no_low_qualifies": q(f"D{d}/no-low"),
                   "no_short_qualifies": q(f"D{d}/no-short")} for d in ("25", "10")}
    localised = all(not f["all_qualifies"] and f["no_low_qualifies"] and not f["no_short_qualifies"]
                    for f in finding.values())
    return {"renders": out, "finding": finding,
            "localised_to_low_band": localised,
            "what": "H's late T20 with one cymbal VCA path off; TONE 50"}


def confirm() -> dict:
    a_rows, a_vals = {}, {"H": [], "L": []}
    for p in WIPERS:
        r = measure(render(cc.shipped_knob_kit_at_wiper("25", p)), f"CY25 wiper {p}")
        a_rows[f"{p:.3f}"] = r
        for b in a_vals:
            a_vals[b].append(r[b]["t20_late_ms"])
        print(f"TONE 25 wiper {p:.3f}: L T20 {r['L']['t20_late_ms']}  H T20 {r['H']['t20_late_ms']}", flush=True)
    b_rows, b_vals = {}, {"H": [], "L": []}
    for d in ("00", "25", "50", "75", "10"):
        r = measure(render(cc.shipped_knob_kit("75", d), rc.DRUM_SOLO_HIT_FRAME + PHASE_SHIFT_FRAMES),
                    f"CY75{d} shifted")
        b_rows[d] = r
        for b in b_vals:
            b_vals[b].append(r[b]["t20_late_ms"])
        print(f"TONE 75 DECAY {d} (+{PHASE_SHIFT_FRAMES} frames): L T20 {r['L']['t20_late_ms']}  "
              f"H T20 {r['H']['t20_late_ms']}", flush=True)
    return {"intermediate_wipers_tone25": {"rows": a_rows, "L": rises(a_vals["L"]), "H": rises(a_vals["H"]),
                                           "wipers": list(WIPERS)},
            "shifted_phase_tone75": {"rows": b_rows, "L": rises(b_vals["L"]), "H": rises(b_vals["H"]),
                                     "hit_frame": rc.DRUM_SOLO_HIT_FRAME + PHASE_SHIFT_FRAMES},
            "requirement": "L late T20 qualifies at every point and rises strictly (the --knob-render gate's "
                           "own rule); H reported, not gated"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("what", choices=("localise-h", "confirm"))
    ap.add_argument("--out", type=pathlib.Path, required=True)
    a = ap.parse_args(argv)
    try:
        res = localise_h() if a.what == "localise-h" else confirm()
    except (cb.Refused, cc.Refused, rc.Refused) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    res.update(provenance())
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(res, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

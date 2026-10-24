"""Reproducible BD pitch-trajectory baseline (#557).

    python tools/bd_pitch_baseline.py --refs $GF180_TR808_REFS \
        --second <another-808-recording.wav> [--second ...] --out build/bd-pitch-baseline.json

Measures, with tools/pitch_trajectory.py (qualified by
tools/test_pitch_trajectory.py), the pitch trajectory of
  - OURS: `perceptual_gate.render_ours("BD")` -- the shipped kit, rendered now;
  - the Fischer BD take (`drum_verify.REF_MAIN["BD"]`);
  - each --second recording (a DIFFERENT 808 unit/session, required).
Every recording goes through `perceptual_gate.condition` (48 kHz, 20 Hz AC
coupling, 20 kHz low-pass, onset at 2 % of peak, -23 LUFS-ish level) so the
onset / bandwidth / level conventions are the gate's, recorded in the output.

REFUSES (exit 2, no number, no JSON) when: the corpus or manifest path is
missing, a --second recording is absent or equals the Fischer take (one
recording is not a spread), the estimator fails its own known-answer
precondition at run time, or any recording's trajectory is refused.  The 230
cents of the issue is HISTORICAL and is not reproduced here by assertion: this
script prints what it measures, and `spread_limits_target` states how far the
recordings disagree with each other, which bounds any exact drop target.

THIS HAS NOT BEEN RUN AGAINST THE REAL CORPUS ON A HOST THAT LACKS IT.  See
docs/bd-pitch-baseline-request.md for exactly what the build box must supply.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import pathlib
import subprocess
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tools"), str(ROOT / "model")]
import pitch_trajectory as pt  # noqa: E402

F_REF = 52.0
QUAL_GLIDE = (58.0, 50.0, 0.020)    # closed form: f0, f_inf, tg


class Refused(RuntimeError):
    pass


def sha(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def qualify_estimator() -> dict:
    """Run-time precondition: the estimator reads a closed-form glide.  If it
    cannot, nothing it says about a recording is data."""
    f0, finf, tg = QUAL_GLIDE
    sr = 48000
    t = np.arange(int(0.6 * sr)) / sr
    ph = 2 * np.pi * (finf * t + (f0 - finf) * tg * (1 - np.exp(-t / tg)))
    x = np.concatenate([np.zeros(960), np.sin(ph) * np.exp(-t / 0.15)])
    tr = pt.trajectory(x, sr, F_REF)
    m = tr.live & (tr.t >= 0.020) & (tr.t <= 0.290)
    truth = finf + (f0 - finf) * np.exp(-tr.t[m] / tg)
    worst = float(np.max(np.abs(1200 * np.log2(tr.f[m] / truth))))
    if not worst < 15.0:
        raise Refused(f"estimator misreads the closed-form glide by {worst:.1f} cents (> 15)")
    return {"closed_form_worst_cents": round(worst, 2), "glide_args": QUAL_GLIDE}


def measure(name: str, x, sr: int, g) -> dict:
    y = g.condition(x, sr, side=name)
    # condition() puts t = 0 at sample LEAD_S*SR
    tr = pt.trajectory(y, g.SR, F_REF, onset_index=int(round(g.LEAD_S * g.SR)))
    return {"name": name, "f0_line_hz": tr.f0, "glide_cents": pt.glide_cents(tr),
            "live_ms": float(tr.live.sum() * pt.HOP_S * 1e3),
            "t_ms": [round(float(v) * 1e3, 1) for v in tr.t[tr.live][:40]],
            "f_hz": [round(float(v), 3) for v in tr.f[tr.live][:40]], "_tr": tr}


def run(refs: pathlib.Path, seconds: list, out: pathlib.Path | None) -> dict:
    import perceptual_gate as g
    import drum_verify as dv
    if not refs.is_dir():
        raise Refused(f"reference corpus {refs} is absent (set GF180_TR808_REFS)")
    rel = dv.REF_MAIN["BD"][0]
    fischer = refs / rel
    if not fischer.is_file():
        raise Refused(f"Fischer BD take {fischer} is absent")
    if not seconds:
        raise Refused("no --second recording: one recording is not a spread")
    for s in seconds:
        if not s.is_file():
            raise Refused(f"second recording {s} is absent")
        if sha(s) == sha(fischer):
            raise Refused(f"second recording {s} is the Fischer take (same bytes)")
    qual = qualify_estimator()
    rows = {}
    x, sr = g.render_ours("BD")
    rows["ours"] = measure("ours", x, sr, g)
    rows["fischer"] = measure("fischer", *g.load_wav(fischer), g)
    for i, s in enumerate(seconds):
        rows[f"second{i}"] = measure(f"second{i}", *g.load_wav(s), g)
    refs_g = [rows[k]["glide_cents"] for k in rows if k != "ours"]
    spread = float(max(refs_g) - min(refs_g))
    trs = {k: rows[k].pop("_tr") for k in rows}
    pair = {}
    for k in rows:
        if k not in ("ours", "fischer"):
            pair[f"fischer_vs_{k}"] = {"offset_cents": pt.offset_cents(trs["fischer"], trs[k]),
                                       "shape_cents": pt.shape_cents(trs["fischer"], trs[k])}
    for k in rows:
        if k != "ours":
            pair[f"{k}_vs_ours"] = {"offset_cents": pt.offset_cents(trs[k], trs["ours"]),
                                    "shape_cents": pt.shape_cents(trs[k], trs["ours"]),
                                    "glide_deficit_cents": rows[k]["glide_cents"] - rows["ours"]["glide_cents"]}
    sh = lambda *c: subprocess.run(c, cwd=ROOT, capture_output=True, text=True).stdout.strip()
    res = {
        "provenance": {
            "commit": sh("git", "rev-parse", "HEAD"),
            "sources_dirty": bool(sh("git", "status", "--porcelain", "--", "tools", "model")),
            "estimator": pt.ESTIMATOR_ID,
            "estimator_sha256": sha(ROOT / "tools" / "pitch_trajectory.py"),
            "corpus": str(refs), "fischer_take": rel,
            "sha256": {"fischer": sha(fischer), **{f"second{i}": sha(s) for i, s in enumerate(seconds)}},
            "second_paths": [str(s) for s in seconds],
            "conventions": {"rate_hz": g.SR, "ac_hp_hz": g.HP_HZ, "lp_hz": g.LP_HZ,
                            "onset_frac": g.ONSET_FRAC, "level": "BS.1770-K -23 (gate condition())",
                            "f_ref_hz": F_REF, "hop_s": pt.HOP_S, "live_db": pt.LIVE_DB,
                            "early_window_s": pt.EARLY_WINDOW_S, "late_window_s": pt.LATE_WINDOW_S},
        },
        "estimator_qualification": qual,
        "rows": rows, "pairs": pair,
        "recording_glide_spread_cents": spread,
        "spread_limits_target": (
            f"recordings (Fischer + {len(seconds)} other) disagree on glide by {spread:.0f} cents; "
            "no drop target tighter than that spread is supported by these recordings"),
        "historical_not_remeasured": "22.2x pitch_shape / ~230 cents (docs/scorecard/gate-379/README.md s4)",
    }
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(res, indent=1, default=float) + "\n")
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--refs", type=pathlib.Path,
                    default=pathlib.Path(os.environ.get("GF180_TR808_REFS") or "/tmp/tr808-ref"))
    ap.add_argument("--second", type=pathlib.Path, action="append", default=[])
    ap.add_argument("--out", type=pathlib.Path)
    a = ap.parse_args(argv)
    try:
        res = run(a.refs, a.second, a.out)
    except (Refused, pt.Refused) as e:
        print(f"REFUSED: {e}")
        return 2
    for k, r in res["rows"].items():
        print(f"{k:9s} glide {r['glide_cents']:+7.1f} cents  live {r['live_ms']:.0f} ms  line {r['f0_line_hz']:.2f} Hz")
    for k, p in res["pairs"].items():
        print(k, {q: round(v, 1) for q, v in p.items()})
    print(res["spread_limits_target"])
    return 0


if __name__ == "__main__":
    sys.exit(main())

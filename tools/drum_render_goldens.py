#!/usr/bin/env python3
"""Render-identity goldens for the drum section, taken from a NAMED commit, so a
later model change can be checked bit for bit against the tree before it.

    tools/drum_render_goldens.py --out docs/scorecard/clap-l2/goldens-pre.json

Records, for the tree it runs on:
  * every one of the sixteen sounds through `run_case.render_drum_solo` at
    accent 1.0 (and 2.0), as sha256 of the float64 render and of the 16-bit PCM,
    and the sha256 of every envelope's per-frame state trace;
  * the frozen L2 experiment render (`tools/clap_final_strike_experiment.py`,
    condition "L2 final 1.00") at accents 0.5 / 1 / 2 and at the DEV and FRESH
    offsets -- the numbers the production implementation must reproduce.

It only reads the model; nothing is written except the JSON.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "model"))

import numpy as np  # noqa: E402


def _sha(a) -> str:
    return hashlib.sha256(np.ascontiguousarray(a).tobytes()).hexdigest()


def solo(args):
    sound, accent = args
    import drums_fx as dx
    import run_case as rc
    stop = dx.SOUND_STOP[sound]
    n = int(rc.SOLO_SECONDS.get(sound, 2.2) * dx.SR)
    d = dx.DrumsFx()
    dm, bd = d.play(dx.hit_writes([(int(0.01 * dx.SR), stop, accent)], dx.kit_with_sounds(sound)), n)
    g = dx.accent_reg(0.45)
    out = np.asarray(dx.output_fx(np.zeros(n), 0, dm, g, bd, g), dtype=np.float64) / 32768.0
    y16 = np.clip(out * 32768.0, -32768, 32767).astype("<i2")
    return (f"{sound}@{accent}", {"float64_sha256": _sha(out), "pcm16_sha256": _sha(y16),
                                  "dmix_sha256": _sha(np.asarray(dm, dtype=np.int64)),
                                  "body_sha256": _sha(np.asarray(bd, dtype=np.int64)),
                                  "env_trace_sha256": [_sha(e) for e in d.trace["env"]],
                                  "peak_fs": round(float(np.max(np.abs(out))), 6)})


def l2(args):
    offset, accent = args
    import clap_final_strike_experiment as ex
    x, sr, strikes, eb = ex.render(ex.CONDITIONS["L2 final 1.00"], accent, offset)
    y16 = np.clip(x * 32768.0, -32768, 32767).astype("<i2")
    return (f"L2@acc{accent}@off{offset}", {"float64_sha256": _sha(x), "pcm16_sha256": _sha(y16),
                                           "burst_env_trace_sha256": _sha(np.asarray(eb, dtype=np.int64)),
                                           "programmed_strikes_ms": strikes})


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--jobs", type=int, default=min(6, os.cpu_count() or 1))
    ap.add_argument("--no-l2", action="store_true", help="skip the experiment renders")
    a = ap.parse_args(argv)
    import drums_fx as dx
    import clap_final_strike_experiment as ex
    jobs = [(s, 1.0) for s in dx.SOUND_NAMES] + [(s, 2.0) for s in dx.SOUND_NAMES]
    l2jobs = [] if a.no_l2 else ([(0, g) for g in (0.5, 1.0, 2.0)]
                                 + [(o, 1.0) for o in ex.DEV + ex.FRESH if o])
    with ProcessPoolExecutor(max_workers=a.jobs) as pool:
        s = dict(pool.map(solo, jobs))
        l = dict(pool.map(l2, l2jobs))
    git = lambda *c: subprocess.run(["git", "-C", str(ROOT), *c], capture_output=True, text=True).stdout.strip()
    out = {"tool": "tools/drum_render_goldens.py", "head": git("rev-parse", "HEAD"),
           "dirty": [x for x in git("status", "--porcelain").splitlines() if x],
           "drums_fx_sha256": hashlib.sha256((ROOT / "model/drums_fx.py").read_bytes()).hexdigest(),
           "solo": s, "l2_experiment": l}
    p = pathlib.Path(a.out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, indent=1) + "\n")
    print(f"wrote {len(s)} solo + {len(l)} L2 goldens at {out['head'][:10]} -> {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Headroom of the NOMINAL demo mix, before the final output clamp (plan087 B1).

    python3 tools/headroom_demo_mix.py --fixture demo --out build/headroom/demo.json
    python3 tools/headroom_demo_mix.py --fixture bar808-full --out ...

Exit: 0 QUALIFIED, 1 FAIL (the nominal mix clips at the final clamp), 2 REFUSED.

WHY THIS EXISTS. #273's clap-phrase fixture ends with every stop at accent 2.0
in one frame, and there the L2 clap gives 327 output-rail samples against 324
for the pre-L2 clap. That is a STRESS result and it stays reported where it is
(docs/scorecard/clap-l2/clap-phrase.json). It says nothing about the mix a
player hears. This asks the other question: at the gains a session actually
uses (fpga/fixtures.py -- `demo` and `bar808-full`, DVOL = BVOL = 0.45, the
reference kit, the fixture's own accents), how far is the master sum from the
final clamp, with L2 and with the pre-L2 clap?

WHAT IS MEASURED, AND WHERE. model/synth_top_model.py step 6 is the master
mix: one exact sum `acc`, one shift, one clamp (sat16). This reads the sum
BEFORE the clamp, so a clip is counted where it happens rather than inferred
from samples that merely equal the rail:

  * pre_clamp_over   frames where acc >> 15 lies outside [-32768, 32767] --
                     the clamp changed the waveform (the verdict's quantity);
  * rail_samples     post-clamp samples at +-32767 / -32768, the same count
                     verify_synth_top.clap_report uses, for comparability;
  * headroom_db      20 log10(32767 / max |acc >> 15|): gain that could be
                     added before the clamp;
  * env_saturated_fires   INTERNAL: strikes whose envelope reached the 24-bit
                     rail (ENV = 32767). Not an output clip; reported separately
                     because plan087 B1 requires the two to stay separate.

The pre-L2 counterfactual is verify_synth_top.pre_l2_clap_writes, the same
function the clap-phrase report uses.

PRECONDITIONS, ASSERTED (REFUSED, exit 2, if any fails):
  * the fixture fires the clap at least once (else it cannot speak to L2);
  * the model output is music (peak >= 1000), not a silence compared to itself;
  * the L2 and pre-L2 renders differ (else the counterfactual did not apply);
  * CONTROL -- the same fixture with every stop's accent forced to 2.0 and
    DVOL = BVOL at full scale must clip before the clamp (pre_clamp_over > 0).
    A counter that cannot see a clip on this material cannot certify its
    absence.

SCOPE. Integer model only, Arty configuration (build_arty.CONFIG), the link
timing of spi_host.BENCH (landing frames move by a few frames between links;
levels do not). No RTL, no image, no board.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for sub in ("fpga", "rtl-sketch", "model", "tools"):
    sys.path.insert(0, os.path.join(ROOT, sub))

import numpy as np                                  # noqa: E402
import spi_host as sh                               # noqa: E402
import drums_fx as dx                               # noqa: E402
import synth_top_model as stm                       # noqa: E402
import fixtures                                     # noqa: E402
import verify_synth_top as vst                      # noqa: E402
import build_arty                                   # noqa: E402
import provenance                                   # noqa: E402

FULL = 65535


def forced_hot(writes: list) -> list:
    """The control: every accent write -> 2.0, DVOL/BVOL -> full scale."""
    out = []
    for f, fl, sec, ad, v in writes:
        if sec == vst.SEC_D and dx.A_ACCENT <= ad < dx.A_ACCENT + dx.N_STOPS:
            v = FULL
        elif sec == vst.SEC_V and ad in (stm.A_DVOL, stm.A_BVOL):
            v = FULL
        out.append((f, fl, sec, ad, v))
    return out


def measure(writes: list, n: int, cfg: dict) -> dict:
    m = stm.SynthTopModel(oversample_2x=bool(cfg["OSC2X"]), filter_2x=bool(cfg["FILTER2X"]),
                          pulse_2x=bool(cfg["PULSE2X"])).run(writes, n)
    route = np.asarray(m["route"])
    acc = (np.asarray(m["v"], dtype=np.int64) * np.asarray(m["vol"], dtype=np.int64)
           + np.where(route != 0, np.asarray(m["d19"], dtype=np.int64) << 15,
                      np.asarray(m["dacc"], dtype=np.int64)))
    pre = acc >> 15
    s = np.asarray(m["sample"], dtype=np.int64)
    # the recomputed sum must BE the model's: same clamp, same samples
    assert np.array_equal(np.clip(pre, -32768, 32767), s), "pre-clamp sum is not the model's mix"
    over = np.where((pre > 32767) | (pre < -32768))[0]
    peak_pre = int(np.abs(pre).max())
    dw = [(f, ad, v) for f, fl, sec, ad, v in writes if sec == vst.SEC_D]
    d = dx.DrumsFx()
    d.play(dw, n)
    fire, env = d.trace["fire"], d.trace["env"]
    cp_fires = int(sum(1 for f in range(n) if (int(fire[f]) >> dx.CP) & 1))
    sat = []
    for e in range(dx.N_ENV):
        for f in np.where(np.asarray(env[e]) == 32767)[0]:
            f = int(f)
            if (f == 0 or env[e][f - 1] != 32767) and int(fire[f]):
                sat.append({"env": e, "frame": f})
    return dict(frames=n, pre_clamp_over=int(len(over)),
                pre_clamp_over_first=[int(x) for x in over[:10]],
                rail_samples=int(np.sum(np.abs(s) == 32767) + np.sum(s == -32768)),
                peak_pre_clamp=peak_pre, peak_sample=int(np.abs(s).max()),
                headroom_db=round(20 * math.log10(32767 / max(peak_pre, 1)), 3),
                rms_dbfs=round(20 * math.log10(max(float(np.sqrt(np.mean(s.astype(np.float64) ** 2))), 1e-9) / 32768), 2),
                env_saturated_fires=len(sat), env_saturated_where=sat[:20],
                cp_fires=cp_fires,
                sample_sha256=__import__("hashlib").sha256(s.astype("<i2").tobytes()).hexdigest())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--fixture", choices=("demo", "bar808-full"), required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    t0 = time.time()
    cfg = dict(build_arty.CONFIG)
    host, n, cover = fixtures.FIXTURES[a.fixture]()
    placed = host.schedule(sh.BENCH)
    writes = sh.model_writes(placed)
    n = max(n, max(w[0] for w in writes) + 1)
    rec = dict(tool="tools/headroom_demo_mix.py", fixture=a.fixture, configuration=cfg,
               link=sh.BENCH.name, writes=len(writes), coverage=cover,
               dvol=[w[4] for w in writes if w[2] == vst.SEC_V and w[3] == stm.A_DVOL],
               bvol=[w[4] for w in writes if w[2] == vst.SEC_V and w[3] == stm.A_BVOL],
               provenance=dict(provenance.worktree_state(), at=provenance.now(),
                               tool_sha=provenance.file_sha(os.path.abspath(__file__))))
    rec["l2"] = measure(writes, n, cfg)
    rec["pre_l2"] = measure(vst.pre_l2_clap_writes(writes), n, cfg)
    rec["control_hot"] = measure(forced_hot(writes), n, cfg)
    refused = []
    if rec["l2"]["cp_fires"] < 1:
        refused.append("the fixture never fires the clap")
    if rec["l2"]["peak_sample"] < 1000:
        refused.append(f"model output peaks at {rec['l2']['peak_sample']}: not music")
    if rec["l2"]["sample_sha256"] == rec["pre_l2"]["sample_sha256"]:
        refused.append("L2 and pre-L2 renders are identical: the counterfactual did not apply")
    if rec["control_hot"]["pre_clamp_over"] == 0:
        refused.append("CONTROL: accents 2.0 + full DVOL/BVOL did not clip, so the counter "
                       "cannot certify the absence of a clip on this material")
    if refused:
        state, code = "REFUSED", 2
    elif rec["l2"]["pre_clamp_over"]:
        state, code = "FAIL", 1
    else:
        state, code = "QUALIFIED", 0
    rec.update(state=state, exit_code=code, refused=refused, seconds=round(time.time() - t0, 1))
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w") as fh:
        json.dump(rec, fh, indent=2)
        fh.write("\n")
    for k in ("l2", "pre_l2", "control_hot"):
        r = rec[k]
        print(f"{a.fixture:12s} {k:12s} pre-clamp over {r['pre_clamp_over']:6d}  rail {r['rail_samples']:6d}  "
              f"peak(pre) {r['peak_pre_clamp']:6d}  headroom {r['headroom_db']:+7.2f} dB  "
              f"env-sat fires {r['env_saturated_fires']:3d}  CP fires {r['cp_fires']}")
    print(f"headroom_demo_mix: {state}" + (" -- " + "; ".join(refused) if refused else ""))
    return code


if __name__ == "__main__":
    sys.exit(main())

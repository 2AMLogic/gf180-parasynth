#!/usr/bin/env python3
"""T1 item 9 cold start: the one command `manifests/inventories/item-9.json` declares.

From a clean clone this bootstraps the pinned environment (spec/trial-environment.json:
Python 3.12 venv, numpy/scipy/pytest/pyyaml, the oss-cad-suite 2026-09-21 iverilog)
and then runs every bench that backs a README status-table claim for rows 3 and 4
(ladder, modal, voice, drums, ctl, i2s_tx, synth_top) plus the injected-defect
controls that must turn them red, through tools/run_all.py.

Exit: 0 every bench and every control behaved; 1 something ran and disagreed or
timed out; 2 REFUSED -- the apparatus could not be set up, which is no evidence
(CLAUDE.md: REFUSED is distinct from pass and fail).

Stdlib only on purpose: klt runs it under whatever `python3` klt's own
environment has, which has no numpy. The children run under the venv built here.

Cost: about four minutes on 2 workers once the toolchain archive is cached. The
full-run claims (verify_voice --set full, 383,460 frames, ~20 min; the 172,063-frame
drum record) are NOT run here -- they exceed klt's 1800 s command cap. They are
listed, with that cost, in the inventory's description.
"""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
VENV = ROOT / "build" / "item9-venv"
PINNED_BIN = ROOT / "build" / "oss-cad-pinned" / "oss-cad-suite" / "bin"
JOBS = 2          # shared-host rule: never above 2 on our own
TIMEOUT = 1700    # inside klt's 1800 s command cap

# Positive benches: each exits 0 only if RTL == model with no tolerance.
BENCHES = [
    "rtl-sketch/verify_ladder.py",
    "rtl-sketch/verify_ladder.py --nch 2 --outdir build/item9/ladder-nch2",  # both filter contexts
    "rtl-sketch/verify_modal.py",
    "rtl-sketch/verify_ctl.py",
    "rtl-sketch/verify_i2s_tx.py",
    "rtl-sketch/verify_synth_top.py",
    "rtl-sketch/verify_voice.py --set quick",
    "rtl-sketch/verify_drums.py",
]

# Injected defects: `--expect-fail` exits 0 only when the bench exits 1 (mismatch),
# never on exit 2 (did not run), so a missing simulator cannot pass as a caught bug.
CONTROLS = (
    [f"rtl-sketch/verify_ladder.py --inject {n} --expect-fail" for n in ("FB", "SAT", "TANH_CLAMP")]
    # CH_BLEED lives in ladder_dp_n.v: it only exists with more than one channel.
    + ["rtl-sketch/verify_ladder.py --nch 2 --inject CH_BLEED --expect-fail --outdir build/item9/ladder-ch-bleed"]
    + [f"rtl-sketch/verify_modal.py --inject {n} --expect-fail"
       for n in ("SAT", "SHIFT", "NUM_HOLD", "PREEXC", "EXC_NOCLEAR")]
    + ["rtl-sketch/verify_voice.py --set quick --only gate --inject ENV_RATE_EXP --expect-fail --outdir build/item9/voice-env-rate-exp",
       "rtl-sketch/verify_voice.py --set quick --only default --inject OSC_SMOOTH_ON --expect-fail --outdir build/item9/voice-smooth-on",
       "rtl-sketch/verify_voice.py --set quick --only drift --inject DRIFT_SHARED --expect-fail --outdir build/item9/voice-drift-shared"]
    + [f"rtl-sketch/verify_drums.py --short --inject {n} --expect-fail --outdir build/item9/drum-{n.lower()}"
       for n in ("DRUM_FINAL_WEAK", "DRUM_FINAL_SHORT", "DRUM_FINAL_SHIFT", "DRUM_FCAP_STALE")]
    + [f"rtl-sketch/verify_ctl.py --inject {n} --expect-fail --outdir build/item9/ctl-{n.lower()}"
       for n in ("SPI_ADDR7", "SPI_DATA24", "SPI_NOSEC", "SPI_ANYLEN", "SPI_DRAIN_LATE")]
    # The ladder stub: right ports, no function. A bench that passes against it
    # cannot detect anything. (verify_ladder resolves --rtl against rtl-sketch/.)
    # The voice stub (rtl-sketch/stubs/voice_dp_stub.v) is deliberately NOT here:
    # on main tb_voice.v probes dut.drift_acc, the stub has none, iverilog aborts and
    # verify_voice exits 2 (did not run), not the red exit 1 the README promises. A
    # control that cannot run is no control; see the inventory description.
    + ["rtl-sketch/verify_ladder.py --rtl stubs/ladder_dp_stub.v --expect-fail --outdir build/item9/stub-ladder"]
    + ["rtl-sketch/verify_i2s_tx.py --inject I2S_SWAP --expect-fail --outdir build/item9/i2s-swap"]
    + [f"rtl-sketch/verify_synth_top.py --inject {n} --expect-fail --outdir build/item9/top-{n.lower()}"
       for n in ("I2S_SHIFT", "I2S_DELAY", "SPI_ADDR7", "SPI_DATA24", "SPI_NOSEC",
                 "SPI_DRAIN_LATE", "MODAL_NUM_HOLD", "DRUM_ENV_FLOOR", "DRUM_BUS_STALE")]
)


def refuse(why: str) -> int:
    print(f"item9_cold_start: REFUSED -- {why}", file=sys.stderr)
    return 2


def main() -> int:
    py = VENV / "bin" / "python"
    if not py.exists():
        # Create the venv first and run bootstrap UNDER it. trial_env compares
        # realpath() of the two interpreters, and a venv's python resolves to the
        # system python, so run from outside it audits the wrong site-packages
        # and refuses a venv it has just populated.
        mk = (["uv", "venv", "--python", "3.12", str(VENV)] if shutil.which("uv")
              else ["python3.12", "-m", "venv", str(VENV)])
        if subprocess.run(mk, cwd=ROOT).returncode != 0 or not py.exists():
            return refuse(f"could not create a Python 3.12 venv ({' '.join(mk)})")
    r = subprocess.run([str(py), str(ROOT / "tools" / "trial_env.py"),
                        "bootstrap", "--venv", str(VENV)], cwd=ROOT)
    if r.returncode != 0:
        return refuse("tools/trial_env.py bootstrap did not satisfy spec/trial-environment.json")
    if not (PINNED_BIN / "iverilog").exists():
        return refuse(f"bootstrap reported success but the pinned iverilog is absent")
    env = dict(os.environ, PATH=f"{PINNED_BIN}{os.pathsep}{os.environ['PATH']}")
    r = subprocess.run([str(py), "tools/trial_env.py", "preflight", "--tools", "iverilog", "vvp"],
                       cwd=ROOT, env=env, stdout=subprocess.DEVNULL)
    if r.returncode != 0:
        return refuse("preflight: the running environment is not the pinned one")
    # --outdir must be ABSOLUTE and must exist: verify_ladder and verify_drums hand
    # it to an iverilog that runs from another directory, so a relative one fails
    # to write its .vvp ("No such file or directory", exit 2 -> NO-VERDICT).
    (ROOT / "build" / "item9").mkdir(parents=True, exist_ok=True)
    cmds = []
    for c in BENCHES + CONTROLS:
        toks = c.split()
        if "--outdir" in toks:
            i = toks.index("--outdir") + 1
            toks[i] = str(ROOT / toks[i])
            pathlib.Path(toks[i]).mkdir(parents=True, exist_ok=True)
        cmds.append(f"{py} {' '.join(toks)}")
    r = subprocess.run([str(py), "tools/run_all.py", "--jobs", str(JOBS), "--timeout", str(TIMEOUT),
                        "--json", "build/item9/run_all.json", *cmds], cwd=ROOT, env=env)
    return 0 if r.returncode == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

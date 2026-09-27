#!/usr/bin/env python3
"""fpga/release/r2_candidate.py -- the R2 candidate record: DRAFT, alongside R1, never replacing it.

    python fpga/release/r2_candidate.py            # BOUND (0) / STALE (1) / REFUSED (2)
    python fpga/release/r2_candidate.py --write    # re-derive r2-candidate.json

WHAT R2 IS (so far). The next image, grouping the confirmed sound repairs
(plan098 section 10):

  * pulse2x: OSC2X=1 FILTER2X=1 PULSE2X=1. The sound owner confirmed it fixes
    M5A/M5B foldback, 10.27 -> 2.21 dB and 8.85 -> 1.91 dB, with the RTL
    bit-exact (#333, PR #343);
  * skip2xwin: an oscillator whose output comes from the 2x bank skips the
    scalar PolyBLEP window loop. This is one S_WIN condition in voice_dp.v
    (docs/deadline/recheck-333, PR #344), and it takes the stress-pulse slack
    from 3 to 20 cycles;
  * PULSE2X admitted by the host's qualified domain, for this image only
    (qualified_domain.PULSE2X_IMAGES).

WHAT IT IS NOT YET. This record is a DRAFT: nothing is frozen and no image is
built. Open before a freeze or a Vivado build:

  * (resolved: the rectangle decimator headroom is 24248/32768, 0.74, saw
    unchanged at 0.85 -- recheck-333 item 5; in polyblep_saw_pair.v and
    voice_fx._render_2x, with the INJECT_BUG_VOICE_PULSE2X_RECT_HEADROOM
    control);
  * the #315 XDC constraint repair, if it is ready and reviewed;
  * the named image selector (#323). The host has no `--image r2` yet, so
    the domain admission cannot be used from the CLI.

The record binds what exists: the configuration, the exact compiled sources
at this tree, how they differ from R1's freeze (voice_dp.v only), and the
domain rule. The evidence is fpga/reports/r2-candidate/summary.json, bound
by digest. BOUND means the committed record equals a fresh derivation.
STALE means something moved. R1's records (r1-candidate.json,
r1-2025.1.json) are neither read for writing nor changed by this module.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for _p in (HERE, ROOT / "fpga", ROOT / "model", ROOT / "rtl-sketch"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

RECORD = HERE / "r2-candidate.json"
EVIDENCE = ROOT / "fpga/reports/r2-candidate/summary.json"
NAME = "R2 candidate"
IMAGE = "r2-candidate"                      # qualified_domain.PULSE2X_IMAGES
CONFIG = {"OSC2X": 1, "FILTER2X": 1, "PULSE2X": 1}
DEFINES = ["VOICE_OSC_2X", "VOICE_FILTER_2X", "VOICE_PULSE_2X"]
STATUS = ("DRAFT -- not frozen, no image built. Open: #354 (verify_voice --set full, "
          "inherited from R1), the #315 XDC repair, the named image selector (#323), "
          "release checks for a second image (#356)")
# the only compiled source R2 may differ in from R1's freeze, and why
EXPECTED_CHANGES = {"rtl-sketch/voice_dp.v": "skip2xwin (#333, docs/deadline/recheck-333)",
                    "rtl-sketch/polyblep_saw_pair.v": "rectangle decimator headroom 24248/32768 "
                                                      "(#333, recheck-333 item 5)"}


class Refused(RuntimeError):
    pass


def sha(p) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def _rel(p) -> str:
    return str(Path(p).resolve().relative_to(ROOT))


def rtl() -> dict:
    import build_arty as ba
    import r1_candidate as r1c
    srcs = {_rel(p): sha(p) for p in ba.sources()}
    roms = {_rel(p): sha(p) for p in ba.roms()}
    changed = []
    for rel, h in {**srcs, **roms}.items():
        r = subprocess.run(["git", "-C", str(ROOT), "show", f"{r1c.RTL_FROZEN_AT}:{rel}"],
                           capture_output=True)
        if r.returncode:
            raise Refused(f"cannot read {rel} at R1's freeze {r1c.RTL_FROZEN_AT[:12]}")
        if hashlib.sha256(r.stdout).hexdigest() != h:
            changed.append(rel)
    unexpected = sorted(set(changed) - set(EXPECTED_CHANGES))
    if unexpected:
        raise Refused(f"compiled sources differ from R1's freeze beyond R2's stated changes: "
                      f"{unexpected}")
    missing = sorted(set(EXPECTED_CHANGES) - set(changed))
    if missing:
        raise Refused(f"R2's stated changes are absent from this tree: {missing}")
    src = (ROOT / "rtl-sketch/voice_dp.v").read_text()
    if src.count("if (!blep || (use_osc2x && shape_osc2x)) state <= S_MIX;") != 1:
        raise Refused("voice_dp.v does not hold the skip2xwin S_WIN condition exactly once")
    pair = (ROOT / "rtl-sketch/polyblep_saw_pair.v").read_text()
    if pair.count("localparam signed [15:0] RECT_GAIN_Q15=16'sd24248;") != 1:
        raise Refused("polyblep_saw_pair.v does not hold the 24248 rectangle headroom exactly once")
    return {"configuration": CONFIG, "defines": DEFINES, "part": ba.PART,
            "sources": srcs, "roms": roms, "constraints": {_rel(ba.XDC): sha(ba.XDC)},
            "differs_from_r1_freeze": {k: EXPECTED_CHANGES[k] for k in sorted(changed)},
            "r1_freeze": r1c.RTL_FROZEN_AT}


def domain() -> dict:
    import qualified_domain as qd
    import voice_fx as vf
    if IMAGE not in qd.PULSE2X_IMAGES:
        raise Refused(f"qualified_domain does not admit PULSE2X on {IMAGE!r}")
    regs = vf.VoiceFx.patch_regs()
    try:
        qd.check_patch(regs, pulse2x=True, image="tree")
    except qd.Rejected:
        pass
    else:
        raise Refused("qualified_domain admits PULSE2X on R1's image")
    qd.check_patch(regs, pulse2x=True, image=IMAGE)
    return {"image": IMAGE, "pulse2x_admitted_on": sorted(qd.PULSE2X_IMAGES),
            "qualified_domain_sha256": sha(HERE / "qualified_domain.py")}


def evidence() -> dict:
    if not EVIDENCE.is_file():
        return {"summary": None, "state": "NONE"}
    rec = json.loads(EVIDENCE.read_text())
    return {"summary": _rel(EVIDENCE), "sha256": sha(EVIDENCE), "state": rec.get("state"),
            "at": rec.get("source_commit")}


def build() -> dict:
    return {"schema": "gf180-parasynth R2 candidate v0", "candidate": NAME, "status": STATUS,
            "rtl": rtl(), "host_domain": domain(), "evidence": evidence(),
            "r1_untouched": ["fpga/release/r1-candidate.json", "fpga/release/r1-2025.1.json"]}


def check(record: Path = RECORD) -> tuple:
    try:
        fresh = json.loads(json.dumps(build()))
    except Refused as exc:
        return "REFUSED", str(exc)
    if not record.exists():
        return "REFUSED", f"no record at {_rel(record)}"
    committed = json.loads(record.read_text())
    if committed != fresh:
        keys = sorted(k for k in set(committed) | set(fresh) if committed.get(k) != fresh.get(k))
        return "STALE", f"differs from a fresh derivation at {keys}"
    return "BOUND", (f"{_rel(record)} equals a fresh derivation (DRAFT; differs from R1's "
                     f"freeze in {list(fresh['rtl']['differs_from_r1_freeze'])}; evidence "
                     f"{fresh['evidence']['state']})")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args(argv)
    if a.write:
        try:
            RECORD.write_text(json.dumps(build(), indent=1) + "\n")
        except Refused as exc:
            print(f"r2_candidate: REFUSED -- {exc}")
            return 2
    v, d = check()
    print(f"r2_candidate: {v} -- {d}")
    return {"BOUND": 0, "STALE": 1}.get(v, 2)


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""The default register images of M1A, M5A and M5B must be BYTE-IDENTICAL to a
baseline tree's (plan074 B7).

    tools/check_default_register_images.py --baseline-root /tmp/wt-main --json OUT

A versioned filter calibration is only a LOCAL, additional operating point if
adding it changed none of the existing images. This computes, in a separate
Python process per tree (so each tree's own `model/` and `tools/` are the ones
imported), for each case:

  * `VoiceFx.patch_regs(**patch)` -- the host conversion's control image, as
    canonical JSON (sorted keys, tuples as lists);
  * the SPI host's `MusicHost(image).load()` voice-section writes, (addr, data)
    in order -- the register image as it leaves on the link;
  * the SPI host's resonance-knob writes at q = 0, 0.5 and 1.0 (the second
    production path through the gain/ogain conversion).

and the default `patch_regs()` image beside them. It compares the sha256 of
each, per item. Exit 0 identical, 1 any difference (listed), 2 refused (a tree
could not produce its images -- no verdict).

The baseline tree is expected to be a checkout of the commit this branch forked
from (`git worktree add <dir> <base>`); the sha is recorded, not assumed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
PY = sys.executable

# Runs INSIDE the tree under test. It may only use names that exist on main.
SNIPPET = r"""
import json, os, sys, pathlib
root = pathlib.Path.cwd()
for p in ("model", "tools", "fpga", "audition", "rtl-sketch"):
    sys.path.insert(0, str(root / p))
import voice_fx as vf
import mono_m1a_score as m1
import mono_m5a_score as m5
import spi_host as sh

def canon(x):
    if isinstance(x, dict):
        return {str(k): canon(v) for k, v in sorted(x.items())}
    if isinstance(x, (list, tuple)):
        return [canon(v) for v in x]
    if hasattr(x, "item"):
        return x.item()
    return x

def link(image):
    h = sh.MusicHost(dict(image)).load(0)
    load = [(w.sec, w.addr, w.data) for w in h.w if w.sec == sh.SEC_VOICE]
    h2 = sh.MusicHost(dict(image))
    for i, q in enumerate((0.0, 0.5, 1.0)):
        h2.knob(10 * i, "resonance", q)
    knob = [(w.frame, w.addr, w.data) for w in h2.w]
    return load, knob

patches = {"default": {}}
patches["M1A"] = m1.patch_for_reference(m1.load_reference()[0])
for cid in ("M5A", "M5B"):
    man = json.loads(m5.MANIFESTS[cid].read_text())
    base = m5._voice_patch(man)
    for wave in ("saw", "pulse"):
        patches[f"{cid}-{wave}"] = m5._patch_for_wave(base, wave)
out = {}
for name, patch in patches.items():
    if os.environ.get("IMAGE_CONTROL") == "CALIBRATE_ALL":
        patch = {**patch, "filter_calibration": "surge-type2-clean-v1"}
    image = vf.VoiceFx.patch_regs(**patch)
    load, knob = link(image)
    out[name] = {"patch_regs": canon(image), "spi_load_voice_writes": canon(load),
                 "spi_resonance_knob_writes": canon(knob)}
print(json.dumps(out, sort_keys=True))
"""


def images(root: pathlib.Path, control: str = "") -> dict:
    env = {**os.environ, "IMAGE_CONTROL": control}
    r = subprocess.run([PY, "-c", SNIPPET], cwd=str(root), capture_output=True, text=True,
                       env=env)
    if r.returncode != 0:
        raise RuntimeError(f"{root}: image extraction failed (exit {r.returncode}):\n"
                           f"{r.stderr[-2000:]}")
    return json.loads(r.stdout)


def sha(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True).encode()).hexdigest()


def head(root: pathlib.Path) -> str:
    r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(root), capture_output=True,
                       text=True)
    return r.stdout.strip() if r.returncode == 0 else "?"


def dirty(root: pathlib.Path) -> bool:
    r = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"],
                       cwd=str(root), capture_output=True, text=True)
    return bool(r.stdout.strip())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--baseline-root", required=True)
    ap.add_argument("--candidate-root", default=str(ROOT))
    ap.add_argument("--json", default=None)
    ap.add_argument("--inject", default="", choices=["", "CALIBRATE_ALL"],
                    help="control: the candidate's images are computed with the "
                         "calibration applied to every patch; must exit 1")
    a = ap.parse_args(argv)
    base_root, cand_root = pathlib.Path(a.baseline_root), pathlib.Path(a.candidate_root)
    try:
        base, cand = images(base_root), images(cand_root, a.inject)
    except RuntimeError as e:
        print(f"REFUSED  {e}")
        return 2
    if set(base) != set(cand):
        print(f"REFUSED  the two trees produced different item sets: {sorted(base)} "
              f"vs {sorted(cand)}")
        return 2
    rows, diffs = {}, []
    for name in sorted(base):
        row = {}
        for part in ("patch_regs", "spi_load_voice_writes", "spi_resonance_knob_writes"):
            b, c = sha(base[name][part]), sha(cand[name][part])
            row[part] = {"baseline_sha256": b, "candidate_sha256": c, "identical": b == c}
            if b != c:
                diffs.append(f"{name}.{part}")
        row["gain_ogain"] = [base[name]["patch_regs"]["gain"],
                             base[name]["patch_regs"]["ogain"]]
        rows[name] = row
        print(f"{name:10s} patch_regs {row['patch_regs']['candidate_sha256'][:16]} "
              f"{'==' if row['patch_regs']['identical'] else '!='}  "
              f"spi load {'==' if row['spi_load_voice_writes']['identical'] else '!='}  "
              f"res knob {'==' if row['spi_resonance_knob_writes']['identical'] else '!='}  "
              f"gain/ogain {row['gain_ogain']}")
    doc = {"what": "default register images, baseline tree vs candidate tree (plan074 B7)",
           "inject": a.inject or None,
           "baseline": {"root": str(base_root), "head": head(base_root),
                        "dirty": dirty(base_root)},
           "candidate": {"root": str(cand_root), "head": head(cand_root),
                         "dirty": dirty(cand_root)},
           "items": rows, "differences": diffs, "identical": not diffs}
    if a.json:
        p = pathlib.Path(a.json)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(doc, indent=1) + "\n")
        print(f"wrote {p}")
    if diffs:
        print(f"DIFFERENT  {len(diffs)} item(s): {diffs}")
        return 1
    print(f"IDENTICAL  {len(rows)} images x 3 parts, baseline {doc['baseline']['head'][:12]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Refusal cases for the DPREG-4 disposition apparatus.

Each case builds a synthetic evidence directory -- a plausible input in a
wrong state -- and runs the analyser (or the simulator's classification)
against it. Every one of these MUST be refused: an analyser that answers
"dismissed" from an empty dump, a truncated dump, a dump missing OPMODE
pins, a hash-mismatched extraction or a sim trace shorter than the stimulus
is answering when it cannot, and its output looks exactly like data
(docs/failure-modes.md: REFUSED is a first-class outcome).

The record of these cases running against the PRE-FIX analyser (which
passed every one of them) is committed next to the evidence as
refusal-cases.pre-fix.json; the post-fix run as refusal-cases.post-fix.json.
That pair is the wrong-then-right accounting for this tool.

Usage:
    dsp_dpreg_refusal_cases.py [--analyser PATH] [--simulator PATH]
                               [--record PATH] [--case NAME]
"""

import hashlib
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent
EVIDENCE = REPO / "fpga/reports/arty/vivado-2025.1/dsp-dpreg-evidence"
REAL_DUMP = EVIDENCE / "dsp_cells_dump.txt"
REAL_DRC = EVIDENCE.parent / "drc.rpt"

ANALYSER = HERE / "dsp_dpreg_analyse.py"
SIMULATOR = HERE / "dsp_dpreg_sim.py"

# child that runs the analyser main() against a patched evidence dir
ANALYSE_CHILD = r"""
import importlib.util, pathlib, sys
spec = importlib.util.spec_from_file_location("dsp_dpreg_analyse", sys.argv[1])
mod = importlib.util.module_from_spec(spec)
sys.modules["dsp_dpreg_analyse"] = mod
spec.loader.exec_module(mod)
mod.EVIDENCE = pathlib.Path(sys.argv[2])
mod.DUMP = mod.EVIDENCE / "dsp_cells_dump.txt"
mod.DRC_RPT = mod.EVIDENCE / "drc.rpt"
sys.exit(mod.main())
"""

# child that runs the simulator classification against a DSIM copy in which
# the clean-run trace died after N cycles and was re-manifested anyway --
# the apparatus state that a clean-pass predicate must refuse.
SIM_CHILD_FMT = r"""
import importlib.util, pathlib, sys, hashlib
spec = importlib.util.spec_from_file_location("dsp_dpreg_sim", sys.argv[1])
mod = importlib.util.module_from_spec(spec)
sys.modules["dsp_dpreg_sim"] = mod
spec.loader.exec_module(mod)
dsim = pathlib.Path(sys.argv[2])
mod.DSIM = dsim

def fake_run_local():
    lines = (dsim / "actual_mode0.txt").read_text().splitlines()[:{keep}]
    (dsim / "actual_mode0.txt").write_text("\n".join(lines) + "\n")
    with open(dsim / "MANIFEST.sha256", "w") as f:
        for p in sorted(dsim.iterdir()):
            if p.is_file() and p.name != "MANIFEST.sha256":
                f.write(hashlib.sha256(p.read_bytes()).hexdigest()
                        + "  " + p.name + "\n")
    return 0

mod.run_local = fake_run_local
sys.exit(mod.main())
"""


def write_manifest(d):
    """Fresh self-consistent manifest over every file in d."""
    lines = []
    for p in sorted(d.iterdir()):
        if p.is_file() and p.name != "MANIFEST.sha256":
            lines.append(hashlib.sha256(p.read_bytes()).hexdigest()
                         + "  " + p.name)
    (d / "MANIFEST.sha256").write_text("\n".join(lines) + "\n")


def cell_blocks(text):
    """Dump text -> ordered list of complete cell blocks."""
    blocks, cur = [], []
    for line in text.splitlines(keepends=True):
        if line.startswith("==== CELL") and cur:
            blocks.append("".join(cur))
            cur = [line]
        else:
            cur.append(line)
    if cur:
        blocks.append("".join(cur))
    return blocks


def block_of(text, name):
    for b in cell_blocks(text):
        if f"==== CELL {name} ====" in b:
            return b
    raise SystemExit(f"cell {name} not in real dump")


TARGET = "u_synth/u_voice/voice_rate_converter/p_1_out__3"

# a complete, dismissible extra cell in the flagged p_1_out family (same-net
# OPMODE[5:4] pairing, so the pre-fix analyser dismisses it): the dump of a
# DIFFERENT build, not this drc.rpt's 13th+1 cell.
EXTRA_CELL = """==== CELL u_synth/u_voice/voice_rate_converter/p_1_out__9 ====
---- properties ----
AREG = 0
BREG = 2
CREG = 0
DREG = 1
MREG = 0
OPMODEREG = 0
PREG = 0
SITE = DSP48_X0Y44
USE_DPORT = 0
---- pins ----
PIN OPMODE[6] dir=IN net=u_synth/u_voice/voice_rate_converter/<const0> driver=u_synth/u_voice/voice_rate_converter/GND/G (GND)
PIN OPMODE[5] dir=IN net=q driver=u_synth/u_voice/voice_rate_converter/lut/O (LUT5)
PIN OPMODE[4] dir=IN net=q driver=u_synth/u_voice/voice_rate_converter/lut/O (LUT5)
PIN OPMODE[3] dir=IN net=u_synth/u_voice/voice_rate_converter/<const0> driver=u_synth/u_voice/voice_rate_converter/GND/G (GND)
PIN OPMODE[2] dir=IN net=u_synth/u_voice/voice_rate_converter/<const1> driver=u_synth/u_voice/voice_rate_converter/VCC/P (VCC)
PIN OPMODE[1] dir=IN net=u_synth/u_voice/voice_rate_converter/<const0> driver=u_synth/u_voice/voice_rate_converter/GND/G (GND)
PIN OPMODE[0] dir=IN net=u_synth/u_voice/voice_rate_converter/<const1> driver=u_synth/u_voice/voice_rate_converter/VCC/P (VCC)
---- P/PCOUT fanout ----
FANOUT P[0] net test/n_0 loads=1
---- control cones (OPMODE/ALUMODE/CARRYINSEL/CARRYIN/CE/RST) ----
CONE x END nets=0
==== END CELL u_synth/u_voice/voice_rate_converter/p_1_out__9 ====
"""


def base_dir(d, drc=True):
    """Fresh evidence dir with the real drc.rpt and its identity files."""
    d.mkdir(parents=True)
    shutil.copy(REAL_DRC, d / "drc.rpt")
    shutil.copy(EVIDENCE / "drc_dpreg_names.txt", d / "drc_dpreg_names.txt")
    shutil.copy(EVIDENCE / "drc_rpt.sha256", d / "drc_rpt.sha256")


def case_empty_dump(d):
    base_dir(d)
    (d / "dsp_cells_dump.txt").write_text("")
    write_manifest(d)


def case_truncated_dump(d):
    base_dir(d)
    text = REAL_DUMP.read_text()
    lines = text.splitlines(keepends=True)
    (d / "dsp_cells_dump.txt").write_text("".join(lines[:int(len(lines) * .7)]))
    write_manifest(d)


def case_missing_opmode_pins(d):
    base_dir(d)
    text = REAL_DUMP.read_text()
    stripped = text.replace(block_of(text, TARGET),
                            "\n".join(l for l in block_of(text, TARGET)
                                      .splitlines()
                                      if not l.startswith("PIN OPMODE"))
                            + "\n")
    (d / "dsp_cells_dump.txt").write_text(stripped)
    write_manifest(d)


def case_wrong_hash(d):
    base_dir(d)
    text = REAL_DUMP.read_text()
    # one bit of evidence changed, manifest NOT regenerated: the recorded
    # hash no longer describes the file being analysed
    (d / "dsp_cells_dump.txt").write_text(text.replace(
        "DSP48_X0Y22", "DSP48_X0Y23", 1))
    shutil.copy(EVIDENCE / "MANIFEST.sha256", d / "MANIFEST.sha256")


def case_missing_manifest(d):
    base_dir(d)
    shutil.copy(REAL_DUMP, d / "dsp_cells_dump.txt")


def case_missing_drc_rpt(d):
    d.mkdir(parents=True)
    shutil.copy(REAL_DUMP, d / "dsp_cells_dump.txt")
    shutil.copy(EVIDENCE / "drc_dpreg_names.txt", d / "drc_dpreg_names.txt")
    shutil.copy(EVIDENCE / "drc_rpt.sha256", d / "drc_rpt.sha256")
    write_manifest(d)


def case_extra_flagged_cell(d):
    base_dir(d)
    shutil.copy(REAL_DUMP, d / "dsp_cells_dump.txt")
    with open(d / "dsp_cells_dump.txt", "a") as f:
        f.write(EXTRA_CELL)
    write_manifest(d)


def case_duplicate_cell(d):
    base_dir(d)
    text = REAL_DUMP.read_text()
    (d / "dsp_cells_dump.txt").write_text(
        text + block_of(text, "u_synth/u_voice/voice_rate_converter/p_1_out"))
    write_manifest(d)


def case_drc_dump_mismatch(d):
    base_dir(d)
    shutil.copy(REAL_DUMP, d / "dsp_cells_dump.txt")
    # the DRC now flags an instance the dump has never seen; the recorded
    # names file still describes the OLD report (stale identity). The
    # instance path also occurs in earlier DRC-rule bodies, so target the
    # DPREG-4#1 body itself.
    text = (d / "drc.rpt").read_text()
    old = "The DSP48E1 cell u_synth/u_voice/osc2_path/p0/pair/dec/prod0__0"
    new = "The DSP48E1 cell u_synth/u_voice/osc2_path/p9/pair/dec/prod0__0"
    i = text.rfind(old)
    (d / "drc.rpt").write_text(text[:i] + new + text[i + len(old):])
    write_manifest(d)


def case_sim_short_trace(d, keep=100):
    """DSIM copy whose clean-run trace stops at `keep` of 148 cycles."""
    d.mkdir(parents=True)
    shutil.copytree(EVIDENCE / "dsim", d / "dsim",
                    ignore=shutil.ignore_patterns("*.vvp"))
    return ("sim", ANALYSE_CHILD and SIM_CHILD_FMT.format(keep=keep),
            str(d / "dsim"))


CASES = [
    ("empty_dump", case_empty_dump, "analyse"),
    ("truncated_dump", case_truncated_dump, "analyse"),
    ("missing_opmode_pins", case_missing_opmode_pins, "analyse"),
    ("wrong_hash", case_wrong_hash, "analyse"),
    ("missing_manifest", case_missing_manifest, "analyse"),
    ("missing_drc_rpt", case_missing_drc_rpt, "analyse"),
    ("extra_flagged_cell", case_extra_flagged_cell, "analyse"),
    ("duplicate_cell", case_duplicate_cell, "analyse"),
    ("drc_dump_mismatch", case_drc_dump_mismatch, "analyse"),
    ("sim_short_trace", case_sim_short_trace, "sim"),
]


def verdict_line(out, kind="analyse"):
    wanted = ("VERDICT:", "NO VERDICT:", "REFUSED:")
    if kind == "sim":
        wanted += ("clean sequence",)  # the clean-pass predicate's own words
    for line in out.splitlines():
        if line.startswith(wanted):
            return line
    return ""


def run_case(name, builder, kind, root):
    d = root / name
    extra = builder(d)
    if kind == "analyse":
        cmd = [sys.executable, "-c", ANALYSE_CHILD, str(ANALYSER), str(d)]
    else:
        _, child, arg = extra
        cmd = [sys.executable, "-c", child, str(SIMULATOR), arg]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    return {"case": name, "exit": r.returncode,
            "verdict": verdict_line(r.stdout, kind)}


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    only = None
    record = None
    args = []
    it = iter(argv)
    for a in it:
        if a == "--case":
            only = next(it)
        elif a == "--record":
            record = pathlib.Path(next(it))
        else:
            args.append(a)
    root = pathlib.Path(tempfile.mkdtemp(prefix="dsp-refusal-"))
    results = []
    for name, builder, kind in CASES:
        if only and name != only:
            continue
        res = run_case(name, builder, kind, root)
        results.append(res)
        print(f"{res['case']:<22} exit={res['exit']:<3} {res['verdict']}")
    if record:
        record.write_text(json.dumps(results, indent=2) + "\n")
        print(f"recorded -> {record}")
    shutil.rmtree(root, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

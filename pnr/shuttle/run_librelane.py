#!/usr/bin/env python3
"""run_librelane.py -- implement synth_top into the wafer.space gf180mcu project
template (half-height 1x0.5 slot) with LibreLane, inside a pinned container.

    ./run_librelane.py floorplan          # synth + floorplan only: MEASURES utilisation
    ./run_librelane.py full               # whole Chip flow, sign-off checkers skipped
    ./run_librelane.py signoff            # whole Chip flow including DRC/LVS/XOR/IR-drop
    ./run_librelane.py check              # preconditions only; runs no tool
    ./run_librelane.py full --run-tag foo # extra args after the mode go to librelane

WHY THIS IS PYTHON AND NOT A SHELL SCRIPT.  It replaces ``run-librelane.sh``
(recovered in PR #176) under CLAUDE.md's "Write Python, not bash" rule.  The
rule is not style: the thing this wrapper must never do is report a result for a
run that did not happen, and the shell version had three separate ways to do
that -- an unquoted array splice, ``$?`` after a pipe, and ``exec`` replacing the
process so no status could be recorded at all.  ``subprocess.run`` cannot.

WHY THERE IS A ``check`` MODE.  CLAUDE.md: *assert your apparatus's preconditions
at the point of use, and REFUSE rather than report when they fail.*  Every
precondition below is one that, left unchecked, yields a run that completes and
produces a plausible number:

  * a PDK root without ``gf180mcu_fd_io`` -- the pad library.  This is not
    hypothetical: the host's default ``ciel`` install of gf180mcu ships the
    standard-cell libraries only, and the ``libs.tech/librelane/config.tcl`` that
    needs the pad library resolves its path with Tcl ``glob``, so the failure
    surfaces tens of minutes in as an opaque ``no files matched glob pattern``
    with nothing naming the missing library.  See ``bootstrap-pdk``.
  * a PDK root whose ``gf180mcu_fd_io/verilog`` predates ``*__blackbox_pp.v``
    (the ``volare``-era build on this host does).  Same Tcl glob, same opaque
    failure, different cause.
  * a missing ``$readmemh`` table.  ``voice_dp.v`` / ``ladder_dp_n.v`` /
    ``drum_dp.v`` read their ROM contents with paths relative to their own source
    file.  A design whose ROMs read as zero still synthesises, still has an area,
    and is worthless -- docs/verification-rules.md rule 3.  This is why
    ``config.yaml`` reads the RTL from ``rtl-sketch/`` in place.
  * an image for the wrong CPU architecture.  The recovered shell script hard-
    coded ``--platform linux/arm64`` and the ``-aarch64`` tag because it was
    written on an Apple machine; on an x86_64 host that silently selects qemu
    emulation, which turns a two-hour route into a fortnight.

REFUSED is a first-class outcome here and exits 3, distinct from a tool failure.

Env / flags (flags win):
  --image      container image            default: pinned tag for this host's arch
  --scratch    working root               default: $LL_SCRATCH or /tmp/gf180-shuttle
  --pdk-root   PDK root                   default: <scratch>/pdk
  --pdk/--scl/--pad                       default: gf180mcuD / 7t 5V / gf180mcu_fd_io
  --density N  PL_TARGET_DENSITY_PCT, an INPUT, written to librelane/density.yaml
"""

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))

# The template drives LibreLane from a Nix shell (`nix-shell; make librelane`).
# There is no Nix on the hosts this repository runs on, so this runs the SAME
# LibreLane version the template's flake.lock pins -- librelane/librelane f18a07a
# == 3.1.0.dev2 -- from its official container, natively for the host's arch.
LIBRELANE_VERSION = "3.1.0.dev2"
ARCH_TAG = {"x86_64": "x86_64", "AMD64": "x86_64", "aarch64": "aarch64", "arm64": "aarch64"}
DOCKER_PLATFORM = {"x86_64": "linux/amd64", "aarch64": "linux/arm64"}

# The PDK version this flow is pinned to.  `bootstrap-pdk` fetches exactly this.
PDK_VERSION = "f6eeac7dad085ffcc829ccfd721f7b4ce39edcf7"

REFUSED = 3

# The sign-off checkers `full` skips.  They are SKIPPED, NOT SILENCED: anything
# `full` reports as "DRC" is the DETAILED ROUTER'S OWN violation count and
# nothing else.  `signoff` runs them.
SIGNOFF_SKIPS = [
    "KLayout.DRC", "Checker.KLayoutDRC",
    "KLayout.Antenna", "Checker.KLayoutAntenna",
    "KLayout.Density", "Checker.KLayoutDensity",
    "Magic.DRC", "Checker.MagicDRC",
    "Netgen.LVS", "Checker.LVS",
    "KLayout.XOR", "Checker.XOR",
]

# Files the RTL $readmemh's, relative to the source file that reads them.  If any
# is missing the ROM reads as zero and the run is worthless but looks fine.
READMEMH_TABLES = [
    "rtl-sketch/tanh16.hex",
    "spec/reference/tables",
]


class Refusal(Exception):
    """A precondition of the apparatus is not met.  Distinct from a tool failure."""


def arch_tag() -> str:
    m = platform.machine()
    if m not in ARCH_TAG:
        raise Refusal(f"unsupported CPU architecture {m!r}; LibreLane ships x86_64 and aarch64")
    return ARCH_TAG[m]


def default_image() -> str:
    return f"ghcr.io/librelane/librelane:{LIBRELANE_VERSION}-{arch_tag()}"


def config_files(here: str, density_yaml: str | None) -> list[str]:
    """The config layering, lowest precedence first.

    slot_1x0p5.yaml is the template's file byte for byte (the fixed DIE_AREA and
    CORE_AREA); macros_5v.yaml the five mandatory wafer.space IP cells;
    config.yaml this project's design config.  density.yaml, if present, carries
    PL_TARGET_DENSITY_PCT, which is an INPUT and is kept in its own file so it can
    never be mistaken for something the flow measured.
    """
    files = [
        os.path.join(here, "librelane", "slots", "slot_1x0p5.yaml"),
        os.path.join(here, "librelane", "macros", "macros_5v.yaml"),
        os.path.join(here, "librelane", "config.yaml"),
    ]
    if density_yaml and os.path.exists(density_yaml):
        files.append(density_yaml)
    return files


def stage_args(mode: str, runs_dir: str) -> list[str]:
    if mode == "floorplan":
        # No --save-views-to: there are no final views at the floorplan, and asking
        # for them would write an empty directory that looks like a finished run.
        return ["--to", "OpenROAD.Floorplan"]
    args: list[str] = []
    if mode == "full":
        for step in SIGNOFF_SKIPS:
            args += ["--skip", step]
        args += ["--skip", "OpenROAD.IRDropReport"]
    args += ["--save-views-to", os.path.join(runs_dir, "final")]
    return args


def verilog_files(here: str) -> list[str]:
    """The RTL config.yaml reads, resolved to absolute host paths.

    Parsed out of config.yaml rather than duplicated, so this check cannot drift
    away from the list the flow actually reads.
    """
    path = os.path.join(here, "librelane", "config.yaml")
    out: list[str] = []
    in_block = False
    for raw in open(path, encoding="utf-8"):
        line = raw.rstrip("\n")
        if line.startswith("VERILOG_FILES:"):
            in_block = True
            continue
        if in_block:
            if line.startswith("- dir::"):
                rel = line[len("- dir::"):].strip()
                out.append(os.path.normpath(os.path.join(here, "librelane", rel)))
                continue
            if line.strip() and not line.startswith(("-", " ", "\t", "#")):
                break
    return out


def check_pdk(pdk_root: str, pdk: str, scl: str, pad: str) -> None:
    root = os.path.join(pdk_root, pdk)
    if not os.path.isdir(root):
        raise Refusal(
            f"no PDK at {root}\n"
            f"  fix: ./run_librelane.py bootstrap-pdk --pdk-root {pdk_root}"
        )
    tech = os.path.join(root, "libs.tech", "librelane", "config.tcl")
    if not os.path.exists(tech):
        raise Refusal(
            f"{root} has no libs.tech/librelane/config.tcl -- this is an OpenLane-1-era\n"
            f"  PDK build; LibreLane 3 needs the librelane variant.\n"
            f"  fix: ./run_librelane.py bootstrap-pdk --pdk-root {pdk_root}"
        )
    for lib in (scl, pad):
        if not os.path.isdir(os.path.join(root, "libs.ref", lib)):
            raise Refusal(
                f"{root}/libs.ref/{lib} is missing.\n"
                f"  ciel's default library set for gf180mcu does NOT include the pad\n"
                f"  library, and libs.tech/librelane/config.tcl resolves it with a Tcl\n"
                f"  glob -- so the run fails tens of minutes in with an opaque\n"
                f"  'no files matched glob pattern' that names no library.\n"
                f"  fix: ./run_librelane.py bootstrap-pdk --pdk-root {pdk_root}"
            )
    # The pad library's config.tcl globs *__blackbox_pp.v.  Older builds (the
    # volare-era gf180mcuD on some hosts) ship only gf180mcu_fd_io.v, and the same
    # Tcl glob fails the same opaque way.
    vdir = os.path.join(root, "libs.ref", pad, "verilog")
    if not any(n.endswith("__blackbox_pp.v") for n in os.listdir(vdir)):
        raise Refusal(
            f"{vdir} has no *__blackbox_pp.v -- this pad library predates the\n"
            f"  blackbox models libs.tech/librelane/config.tcl globs for.\n"
            f"  fix: ./run_librelane.py bootstrap-pdk --pdk-root {pdk_root}"
        )


def check_rtl(here: str, repo: str) -> None:
    missing = [p for p in verilog_files(here) if not os.path.exists(p)]
    if missing:
        raise Refusal(
            "config.yaml lists RTL that does not exist:\n  "
            + "\n  ".join(missing)
        )
    for rel in READMEMH_TABLES:
        if not os.path.exists(os.path.join(repo, rel)):
            raise Refusal(
                f"{rel} is missing. The RTL $readmemh's its ROM contents with paths\n"
                f"  relative to its own source file. A design whose ROMs read as zero\n"
                f"  still synthesises, still has an area, and is worthless\n"
                f"  (docs/verification-rules.md rule 3)."
            )


def check_docker(image: str, pull: bool) -> None:
    if shutil.which("docker") is None:
        raise Refusal("docker is not on PATH; this flow runs LibreLane from its container")
    probe = subprocess.run(["docker", "image", "inspect", image],
                           capture_output=True, text=True)
    if probe.returncode != 0:
        if not pull:
            raise Refusal(f"{image} is not present locally; rerun with --pull")
        print(f"[run_librelane] pulling {image}", flush=True)
        got = subprocess.run(["docker", "pull", image])
        if got.returncode != 0:
            raise Refusal(f"docker pull {image} failed (exit {got.returncode})")


def bootstrap_pdk(pdk_root: str) -> int:
    """Fetch the pinned gf180mcu PDK, WITH ALL LIBRARIES, into pdk_root.

    ``-l all`` is the whole point: the default library set omits gf180mcu_fd_io,
    and a padframe flow cannot run without it.  Writes to pdk_root, never to the
    host's shared ~/.ciel.
    """
    if shutil.which("ciel") is None:
        print("REFUSED: ciel is not on PATH; cannot fetch the PDK", file=sys.stderr)
        return REFUSED
    os.makedirs(pdk_root, exist_ok=True)
    cmd = ["ciel", "enable", "--pdk-root", pdk_root,
           "--pdk-family", "gf180mcu", "-l", "all", PDK_VERSION]
    print("[run_librelane] " + " ".join(cmd), flush=True)
    return subprocess.run(cmd).returncode


def write_density(here: str, density: str | None) -> str:
    path = os.path.join(here, "librelane", "density.yaml")
    if density is not None:
        with open(path, "w", encoding="utf-8") as f:
            f.write("# INPUT, written by run_librelane.py. Global-placement spreading\n"
                    "# target, not a measurement of anything.\n"
                    f"PL_TARGET_DENSITY_PCT: {density}\n")
    return path


def docker_argv(image: str, mounts: list[str], workdir: str, inner: list[str]) -> list[str]:
    """Host paths == container paths, so `dir::` relatives resolve on either side."""
    argv = ["docker", "run", "--rm"]
    plat = DOCKER_PLATFORM.get(arch_tag())
    if plat:
        argv += ["--platform", plat]
    for m in mounts:
        argv += ["-v", f"{m}:{m}"]
    argv += ["-w", workdir, image]
    return argv + inner


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["check", "bootstrap-pdk", "floorplan", "full", "signoff"])
    ap.add_argument("--image", default=os.environ.get("LL_IMAGE") or default_image())
    ap.add_argument("--scratch", default=os.environ.get("LL_SCRATCH", "/tmp/gf180-shuttle"))
    ap.add_argument("--pdk-root", default=os.environ.get("PDK_ROOT"))
    ap.add_argument("--runs-dir", default=os.environ.get("RUNS_DIR"))
    ap.add_argument("--pdk", default=os.environ.get("PDK", "gf180mcuD"))
    ap.add_argument("--scl", default=os.environ.get("SCL", "gf180mcu_fd_sc_mcu7t5v0"))
    ap.add_argument("--pad", default=os.environ.get("PAD", "gf180mcu_fd_io"))
    ap.add_argument("--density", default=os.environ.get("DENSITY") or None,
                    help="PL_TARGET_DENSITY_PCT -- an INPUT, not a measurement")
    ap.add_argument("--pull", action="store_true", help="docker pull the image if absent")
    ap.add_argument("--dry-run", action="store_true", help="print the command, run nothing")
    ap.add_argument("rest", nargs=argparse.REMAINDER, help="extra args passed to librelane")
    a = ap.parse_args(argv)

    pdk_root = a.pdk_root or os.path.join(a.scratch, "pdk")
    runs_dir = a.runs_dir or os.path.join(a.scratch, "runs")

    if a.mode == "bootstrap-pdk":
        return bootstrap_pdk(pdk_root)

    try:
        check_rtl(HERE, REPO)
        check_pdk(pdk_root, a.pdk, a.scl, a.pad)
        if a.mode != "check":
            check_docker(a.image, a.pull)
    except Refusal as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return REFUSED

    if a.mode == "check":
        print(json.dumps({
            "verdict": "preconditions OK",
            "image": a.image,
            "arch": platform.machine(),
            "pdk_root": pdk_root,
            "pdk": a.pdk,
            "scl": a.scl,
            "pad": a.pad,
            "verilog_files": verilog_files(HERE),
        }, indent=2))
        return 0

    os.makedirs(pdk_root, exist_ok=True)
    os.makedirs(runs_dir, exist_ok=True)
    density_yaml = write_density(HERE, a.density)

    inner = ["librelane"] + config_files(HERE, density_yaml) + [
        "--pdk", a.pdk, "--pdk-root", pdk_root, "--manual-pdk",
        "--scl", a.scl, "--pad", a.pad,
    ] + stage_args(a.mode, runs_dir)
    extra = [x for x in a.rest if x != "--"]
    inner += extra

    mounts = sorted({REPO, pdk_root, runs_dir})
    argv_full = docker_argv(a.image, mounts, runs_dir, inner)
    print("[run_librelane] " + " ".join(argv_full), flush=True)
    if a.dry_run:
        return 0
    # subprocess.run, not exec: the wrapper must be able to record a status.
    return subprocess.run(argv_full).returncode


if __name__ == "__main__":
    sys.exit(main())

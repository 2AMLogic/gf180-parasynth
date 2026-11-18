#!/usr/bin/env python3
"""Re-run the ``ladder_dp`` route on the batch fleet, keeping the DEF/ODB (#1006).

Job ``rs-parasynth808-od2-s00`` produced ``ladder_dp.def`` and
``ladder_dp_route.odb`` (``klt-file-listing.txt`` records both) but did not
declare them as outputs, so the 24 residual ``Metal2``/``VSS`` Metal Spacing
sites could not be separated into "the router ignored an obstruction" versus
"the PDN left no legal ``Metal2`` room" -- the question #808 left open and
#1006 owns. The bucket's objects expire (~2026-12-18), so the DEF could not be
fetched afterwards; the only path to one is a re-run. This wrapper is what the
re-run job executes:

1. bootstrap the PDK pin into a run-local store (never the host's shared
   ``~/.ciel`` -- the recipe ``pnr/shuttle/run_librelane.py`` established);
   ``ciel`` itself comes from ``requirements-rerun.txt``, which the batch
   runner installs into a job-local venv at the version the original job
   probed (3.2.0) -- the heavy image does not carry it on PATH, which the
   first submission found as a REFUSED after 20 seconds;
2. run ``run_klt.py`` unchanged -- the committed driver, so the re-run carries
   the same retired-patch audit and the same run.log contract;
3. assert the big artefacts exist and manifest them (path, sha256, size),
   including the route-pass pair whose byte-identity
   ``route_drc_extract.json`` records for the *original* run. Comparing this
   run's report sha256 against that extract's ``be35b9d9…`` is how the re-run
   states whether it reproduced the original route bit-for-bit.

Exit codes: ``0`` ran and every kept artefact is present; ``1`` FAILED -- a
step failed or a declared artefact is missing; ``3`` REFUSED -- a precondition
failed (ciel missing, PDK pin absent after bootstrap) and nothing ran; the
driver's own exit codes (0/1/3/4) propagate as theirs.

Design constraint: the batch runner executes one script and copies declared
output paths out of the source tree afterwards, so every artefact this wrapper
promises must sit at a stable path under ``HERE`` and be declared on the
``repo-script-submit.py`` command line. Nothing here reads or writes outside
the run-local store and ``HERE``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_REFUSED = 3

#: The ``ladder_dp`` lineage's PDK pin (#808 correction 6: deliberately not the
#: shuttle lineage's ``f6eeac7d…``). Re-running at any other pin would make the
#: PDK a second variable, and re-running at a different klt version is out of
#: scope for #1006 -- the pinned heavy AMI carries klt 0.9.0, the version the
#: original job ran, which is what makes a re-run comparable.
PDK_PIN = "54435919abffb937387ec956209f9cf5fd2dfbee"
PDK = "gf180mcuD"
#: ciel's own storage layout (ciel/common.py::get_versions_dir):
#: ``<pdk-root>/ciel/<family>/versions/<pin>`` -- the family level is ciel's,
#: not ours, and the tool-facing root is the version directory itself (what
#: the original job's run.log names).
PDK_FAMILY = "gf180mcu"

#: What must exist after a successful driver run for the DEF question to be
#: answerable, all under HERE. The responses and run.log are re-collected so
#: the manifest binds them next to the artefacts.
KEPT_ARTEFACTS = (
    ".klt/place-and-route/ladder_dp.def",
    ".klt/place-and-route/ladder_dp_route.odb",
    ".klt/place-and-route/ladder_dp_route_drc.rpt",
    ".klt/place-and-route/ladder_dp_route_pass_0_drc.rpt",
    ".klt/place-and-route/ladder_dp_route_metrics.json",
    ".klt/place-and-route/pnr_ladder_dp_route.tcl",
    "synth_response.json",
    "par_response.json",
    "run.log",
)

MANIFEST_NAME = "route_def_manifest.json"
PDK_FETCH_LOG = "pdk-fetch.log"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return f"sha256:{h.hexdigest()}"


def resolve_pdk_root(store: Path, pin: str, pdk: str) -> tuple[Path | None, str | None]:
    """Return ``(tool_pdk_root, None)`` or ``(None, refusal)``.

    ``ciel enable --pdk-root <store>`` installs
    ``<store>/ciel/<family>/versions/<pin>`` (ciel's own layout) and the
    *version directory* is what tools take as ``--pdk-root`` (the original
    job's run.log names exactly that shape). Resolved by glob and asserted to
    hold the cell library, never assumed.
    """
    candidates = sorted(store.glob(f"*/{PDK_FAMILY}/versions/{pin}"))
    if not candidates:
        return None, (
            f"no PDK version dir under {store} for pin {pin}; "
            f"ciel enable did not produce the expected layout"
        )
    root = candidates[0]
    if not (root / pdk / "libs.ref" / "gf180mcu_fd_sc_mcu7t5v0").is_dir():
        return None, f"{root} has no {pdk}/libs.ref/gf180mcu_fd_sc_mcu7t5v0 cell library"
    return root, None


def missing_artefacts(here: Path = HERE) -> list[str]:
    return [name for name in KEPT_ARTEFACTS if not (here / name).is_file()]


def build_manifest(here: Path = HERE) -> dict:
    """Provenance + sha256 of everything kept, written beside the artefacts.

    ``RS_SOURCE_COMMIT`` is exported by the batch job runner for exactly this
    (2am#7367); absent locally, the committed driver's own ``run.log`` still
    carries the version line. The pair verdict is computed here, not asserted
    from memory, so the manifest states this run's own byte-identity fact.
    """
    pair_a = here / ".klt/place-and-route/ladder_dp_route_pass_0_drc.rpt"
    pair_b = here / ".klt/place-and-route/ladder_dp_route_drc.rpt"
    probe = {
        "klt": subprocess.run(["klt", "version"], capture_output=True, text=True).stdout.strip(),
        "yosys": subprocess.run(["yosys", "-V"], capture_output=True, text=True).stdout.strip(),
        "openroad": subprocess.run(["openroad", "-version"], capture_output=True, text=True).stdout.strip(),
        "ciel": subprocess.run(["ciel", "--version"], capture_output=True, text=True).stdout.strip(),
    }
    manifest = {
        "schema_version": 1,
        "what": "Artefacts kept by the #1006 DEF re-run: sha256 bindings for the routed DEF/ODB and the reports the design-vs-router analysis consumed.",
        "source_commit": os.environ.get("RS_SOURCE_COMMIT", "unknown"),
        "pdk_pin": PDK_PIN,
        "pdk": PDK,
        "nproc": multiprocessing.cpu_count(),
        "tools": probe,
        "artefacts": {
            name: {
                "sha256": sha256_file(here / name),
                "bytes": (here / name).stat().st_size,
            }
            for name in KEPT_ARTEFACTS
        },
        "route_pass_pair_byte_identical": pair_a.is_file()
        and pair_b.is_file()
        and pair_a.read_bytes() == pair_b.read_bytes(),
        "original_run_report_sha256": "sha256:be35b9d9bc4ec0ea6cc4525afa5e4ce37abbb4410b2f4e912cae8903ca8e3bbe",
        "reproduced_original_route_report": (
            pair_b.is_file()
            and sha256_file(pair_b)
            == "sha256:be35b9d9bc4ec0ea6cc4525afa5e4ce37abbb4410b2f4e912cae8903ca8e3bbe"
        ),
        "written_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pdk-store", default=str(HERE / ".pdk-store"))
    parser.add_argument(
        "--timeout-s",
        type=float,
        default=6300.0,
        help="per-step cap handed to run_klt.py (the original job's value)",
    )
    args = parser.parse_args(argv)

    store = Path(args.pdk_store).resolve()

    if shutil.which("ciel") is None:
        print("REFUSED: ciel is not on PATH; cannot bootstrap the PDK pin", file=sys.stderr)
        return EXIT_REFUSED

    store.mkdir(parents=True, exist_ok=True)
    print(f"[route-rerun] ciel enable --pdk-root {store} --pdk-family gf180mcu -l all {PDK_PIN}", flush=True)
    with (HERE / PDK_FETCH_LOG).open("w") as fetch_log:
        got = subprocess.run(
            ["ciel", "enable", "--pdk-root", str(store), "--pdk-family", "gf180mcu", "-l", "all", PDK_PIN],
            stdout=fetch_log,
            stderr=subprocess.STDOUT,
        )
    if got.returncode != 0:
        print(f"FAILED: ciel enable exited {got.returncode}; see {PDK_FETCH_LOG}", file=sys.stderr)
        return EXIT_FAILED

    pdk_root, refusal = resolve_pdk_root(store, PDK_PIN, PDK)
    if refusal:
        print(f"REFUSED: {refusal}", file=sys.stderr)
        return EXIT_REFUSED
    print(f"[route-rerun] pdk {PDK} @ {pdk_root}", flush=True)

    driver = subprocess.run(
        [sys.executable, str(HERE / "run_klt.py"), "--pdk-root", str(pdk_root), "--timeout-s", str(args.timeout_s)]
    )
    if driver.returncode != 0:
        print(f"FAILED: run_klt.py exited {driver.returncode}", file=sys.stderr)
        return driver.returncode

    missing = missing_artefacts()
    if missing:
        print(
            "FAILED: driver exited 0 but the kept artefacts are missing: "
            + ", ".join(missing),
            file=sys.stderr,
        )
        return EXIT_FAILED

    manifest = build_manifest()
    manifest_path = HERE / MANIFEST_NAME
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"[route-rerun] kept {len(KEPT_ARTEFACTS)} artefacts; manifest {MANIFEST_NAME}", flush=True)
    for name, rec in manifest["artefacts"].items():
        print(f"  {rec['sha256']}  {name}  ({rec['bytes']} bytes)")
    print(
        f"[route-rerun] route-pass pair byte-identical: "
        f"{manifest['route_pass_pair_byte_identical']}; reproduced the original "
        f"route report: {manifest['reproduced_original_route_report']}",
        flush=True,
    )
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

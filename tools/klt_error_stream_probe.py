#!/usr/bin/env python3
"""Measure which stream `klt <verb> --format json` puts an error envelope on.

This is the instrument behind the numbers in `docs/tool-findings.md`'s
"`par_response.json` is 0 bytes" entry. That entry sat unfilable for weeks
because "stderr was not captured", and the fix for that is not to be more
careful next time -- it is to have a committed thing that captures it.

What it does: hands each verb a request naming a file that does not exist, with
`--format json`, and reports the exit status and the **byte count of each
stream separately**. A verb that answers on stderr and leaves stdout empty
turns `--format json > report.json` into a 0-byte artefact for the caller --
which is deliberate in klt (`cli/output.py:139`), but is the mechanism behind
an 0-byte response file either way.

Preconditions are asserted, not assumed: the probe REFUSES (exit 3) when `klt`
is absent or when a verb exits 0 on a request that should have failed, because
an absent tool and a tool that did not fail are both "no measurement", and
reporting either as a result would be worse than being silent.

Exit codes: 0 measured, 3 REFUSED (no measurement was possible).

    python3 tools/klt_error_stream_probe.py --pdk-root <pdk>
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

EXIT_OK = 0
EXIT_REFUSED = 3

#: A request per verb that must fail on a missing input, and fail *early* --
#: before yosys or OpenROAD is invoked, so the probe costs a second.
REQUESTS: dict[str, dict] = {
    "synthesize": {
        "schema": "klt.synthesize.request/1",
        "engine": "yosys",
        "sources": ["no_such_source.v"],
        "hdl_toplevel": "nope",
        "pdk": {
            "cell_library": "gf180mcu_fd_sc_mcu7t5v0",
            "corner": "tt_025C_5v00",
        },
    },
    "place-and-route": {
        "schema": "klt.place_and_route.request/1",
        "engine": "openroad",
        "netlist": "no_such_netlist.v",
        "hdl_toplevel": "nope",
        "pdk": {
            "cell_library": "gf180mcu_fd_sc_mcu7t5v0",
            "corner": "tt_025C_5v00",
        },
        "floorplan": {
            "method": "utilization",
            "utilization_pct": 50,
            "aspect_ratio": 1.0,
            "core_margin_um": 2.0,
            "site": "GF018hv5v_mcu_sc7",
        },
        "io": {"layer_h": "Metal3", "layer_v": "Metal4"},
        "constraints": {"clock_port": "clk", "clock_period_ns": 10.0},
        "target_stage": "floorplan",
        "seed": 1,
    },
}


def classify(returncode: int, stdout: bytes, stderr: bytes) -> dict:
    """Describe one verb's error behaviour. Pure; this is what is tested.

    ``envelope_stream`` is the stream carrying a parseable klt error envelope,
    or ``None``. ``redirect_artifact_bytes`` is what a caller doing
    ``--format json > report.json`` is left holding.
    """
    def envelope(raw: bytes) -> dict | None:
        try:
            doc = json.loads(raw.decode(errors="replace"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        return doc if isinstance(doc, dict) and "error" in doc else None

    on_stdout, on_stderr = envelope(stdout), envelope(stderr)
    return {
        "exit": returncode,
        "stdout_bytes": len(stdout),
        "stderr_bytes": len(stderr),
        "envelope_stream": (
            "stdout" if on_stdout else "stderr" if on_stderr else None
        ),
        "message": (on_stdout or on_stderr or {}).get("error", {}).get("message"),
        "redirect_artifact_bytes": len(stdout),
    }


def probe(verb: str, pdk: str, pdk_root: str) -> tuple[dict | None, str | None]:
    """Run one verb against a deliberately-missing input. Never raises."""
    request = REQUESTS[verb]
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"{verb}_request.json"
        path.write_text(json.dumps(request, indent=2) + "\n")
        completed = subprocess.run(
            ["klt", verb, "--pdk", pdk, "--pdk-root", pdk_root,
             str(path), "--format", "json"],
            capture_output=True,
            check=False,
        )
    if completed.returncode == 0:
        return None, (
            f"klt {verb} exited 0 on a request naming a file that does not "
            "exist; there is no error to measure the stream of"
        )
    return classify(completed.returncode, completed.stdout, completed.stderr), None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pdk-root", required=True)
    parser.add_argument("--pdk", default="gf180mcuD")
    args = parser.parse_args(argv)

    if shutil.which("klt") is None:
        print("REFUSED: 'klt' is not on $PATH", file=sys.stderr)
        return EXIT_REFUSED
    if not Path(args.pdk_root).is_dir():
        print(f"REFUSED: --pdk-root {args.pdk_root} is not a directory",
              file=sys.stderr)
        return EXIT_REFUSED

    results, refusals = {}, []
    for verb in REQUESTS:
        result, reason = probe(verb, args.pdk, args.pdk_root)
        if reason is not None:
            refusals.append(f"{verb}: {reason}")
        else:
            results[verb] = result
    for reason in refusals:
        print(f"REFUSED: {reason}", file=sys.stderr)
    if not results:
        return EXIT_REFUSED

    version = subprocess.run(["klt", "version"], capture_output=True,
                             text=True, check=False).stdout.strip()
    print(json.dumps({"klt": version, "verbs": results}, indent=2))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""klt synthesize + place-and-route for ``ladder_dp``, with no netlist patches.

This is the Python replacement for the old ``run-klt.sh`` (issue #765). The
shell version carried three hand patches between the two klt calls -- a ``sed``
that stripped ``signed`` port qualifiers, a Python heredoc that swapped bare
constant assigns for 7t tie cells and dropped all-x assigns, and a ``grep -c``
echo -- each working around a defect in ``klt synthesize``'s own output that its
own ``klt place-and-route`` then could not consume (klayout-tools#2085, #2088).
Those defects were fixed upstream (klayout-tools PR 2006, merged 2026-09-17;
PR 2099, merged 2026-09-19), so the patches are gone and ``par_request.json``
points straight at the netlist ``klt synthesize`` emits.

Why Python and not bash: ``CLAUDE.md`` -> "Write Python, not bash". The old
script's ``$(...)`` arithmetic, branching heredoc and ``grep -c ... || true``
are exactly the shapes that have reported success for a failed command in this
repository before. ``subprocess.run`` cannot.

What it records that the shell version did not
----------------------------------------------
``docs/tool-findings.md`` has an unfilable candidate: the committed
``par_response.json`` is 0 bytes, and it cannot be filed upstream because
"stderr was not captured, and the file's mtime ... predates the route
artefacts, so we cannot establish which invocation produced it or why". This
driver captures, per step, the exact argv, the wall time, the **exit status**,
the response size, and stderr (tail, in the committed ``run.log``), so a
repeat of that 0-byte response arrives filable.

Preconditions are asserted, not assumed
---------------------------------------
``CLAUDE.md``: "Assert your apparatus's preconditions at the point of use, and
REFUSE rather than report when they fail. ``REFUSED`` is a first-class outcome."
``--pdk-root`` is required and has no default that happens to exist on one
developer's laptop: the old script defaulted ``PDK_ROOT`` to
``$REPO/../prep/tinytapeout/pdk/ciel/...``, a sibling of the repository that
exists on no CI runner and no dispatch worker, so a clean checkout could not run
it at all.

Exit codes
----------
==== ======================================================================
   0 both steps exited 0 and both responses are non-empty parseable JSON
   1 FAILED -- a step exited non-zero, or wrote an empty/unparseable response
   3 REFUSED -- a precondition failed; nothing was run
   4 NO-VERDICT -- a step hit ``--timeout-s``; the run proves nothing
==== ======================================================================

Usage
-----
::

    python3 pnr/klt/ladder_dp/run_klt.py \
        --pdk-root /path/to/ciel/gf180mcu/versions/<rev>

``klt`` resolves every relative path in a request document against the
*request file's* directory (``place_and_route._resolve_netlist``,
``synthesize``'s counterpart), not against the current directory, so this runs
from anywhere. The old script's "must be run with the repo root as cwd" note
was about the Docker wrapper's bind mounts; ``yosys`` and ``openroad`` are
native binaries on the provisioned hosts and no wrapper is involved.
"""

from __future__ import annotations

import argparse
import hashlib
import json
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
EXIT_NO_VERDICT = 4

#: Binaries every step shells out to, directly or through klt.
REQUIRED_BINARIES = ("klt", "yosys", "openroad")

#: How much of a step's stderr goes into the committed ``run.log``.
STDERR_TAIL_LINES = 200

#: What the committed ``par_request.json`` carries in place of a netlist
#: path. ``klt synthesize`` writes into ``.klt/synthesize/<run_id>/`` and
#: ``run_id`` defaults to a fresh ``uuid4`` per invocation, so there is no
#: static path a committed place-and-route request can name twice (pinning
#: ``run_id`` makes the directory single-use: klt refuses to reuse one).
#: This driver therefore binds the request at run time to the path the
#: synthesize response itself reports -- which is also strictly stronger
#: than a static path, since the netlist routed is by construction the
#: netlist this run produced. Filed upstream; see docs/tool-findings.md.
NETLIST_SENTINEL = "@synth_response.netlist_path"

#: Where the run-time-bound request is written. A sibling of the template,
#: because klt resolves a request's output directory against the request
#: file's own directory.
DERIVED_PAR_REQUEST_NAME = "par_request.resolved.json"


def sha256_file(path: Path) -> str:
    """Return ``sha256:<hex>`` over the raw bytes of ``path``."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return f"sha256:{h.hexdigest()}"


def load_json(path: Path) -> tuple[dict | None, str | None]:
    """Return ``(document, None)`` or ``(None, reason)``. Never raises."""
    try:
        text = path.read_text()
    except OSError as exc:
        return None, f"cannot read {path}: {exc}"
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, f"{path} is not parseable JSON: {exc}"
    if not isinstance(doc, dict):
        return None, f"{path} is not a JSON object"
    return doc, None


def check_preconditions(
    *,
    here: Path,
    pdk_root: Path | None,
    pdk: str,
    which=shutil.which,
) -> list[str]:
    """Return a list of unmet preconditions; empty means safe to run.

    Checked here rather than left to klt because klt's own failure for a
    missing PDK or a missing ``openroad`` arrives as an opaque error several
    minutes into a run, and a missing binary is not a result about the design.
    """
    problems: list[str] = []

    for binary in REQUIRED_BINARIES:
        if which(binary) is None:
            problems.append(f"{binary!r} is not on $PATH")

    synth_request_path = here / "synth_request.json"
    par_request_path = here / "par_request.json"

    synth_request, reason = load_json(synth_request_path)
    if reason is not None:
        problems.append(reason)
    par_request, reason = load_json(par_request_path)
    if reason is not None:
        problems.append(reason)

    cell_library = None
    if synth_request is not None:
        cell_library = (synth_request.get("pdk") or {}).get("cell_library")
        if not cell_library:
            problems.append(
                f"{synth_request_path} has no pdk.cell_library to resolve"
            )
        sources = synth_request.get("sources") or []
        for src in sources:
            src_path = (here / src) if not os.path.isabs(src) else Path(src)
            if not src_path.exists():
                problems.append(f"RTL source {src!r} does not exist at {src_path}")

    if pdk_root is None:
        problems.append(
            "no PDK root: pass --pdk-root (or set $PDK_ROOT). There is "
            "deliberately no default -- the shell version defaulted to a "
            "sibling of the repository that exists on no CI runner."
        )
    else:
        if not pdk_root.is_dir():
            problems.append(f"--pdk-root {pdk_root} is not a directory")
        elif cell_library:
            libs_ref = pdk_root / pdk / "libs.ref" / cell_library
            if not libs_ref.is_dir():
                problems.append(
                    f"PDK root {pdk_root} does not ship {pdk}/libs.ref/"
                    f"{cell_library} (needed by both requests)"
                )

    if par_request is not None:
        netlist = par_request.get("netlist")
        if not netlist:
            problems.append(f"{par_request_path} has no netlist field")
        elif netlist != NETLIST_SENTINEL:
            # The patched-sibling indirection this issue retired: par_request
            # pointed at `ladder_dp_synth_unsigned.v`, a hand-edited copy, so
            # the netlist that was routed was never the netlist klt emitted.
            # Any literal path here is that same shape, because this driver
            # applies no patches and klt's own netlist path is run-scoped.
            problems.append(
                f"{par_request_path} netlist is {netlist!r}, not the "
                f"{NETLIST_SENTINEL!r} sentinel -- a literal path means the "
                "netlist being routed is not necessarily the one this run's "
                "klt synthesize produced"
            )

    pnr_out = here / ".klt" / "place-and-route"
    if pnr_out.is_dir() and any(pnr_out.iterdir()):
        problems.append(
            f"{pnr_out} is already populated; klt place-and-route refuses a "
            "non-empty output directory (remove it, or pass --resume by hand)"
        )

    return problems


class StepResult:
    """One klt invocation: what was run, what it exited with, what it wrote."""

    def __init__(self, name: str, argv: list[str]) -> None:
        self.name = name
        self.argv = argv
        self.returncode: int | None = None
        self.seconds: float = 0.0
        self.stderr: str = ""
        self.response_path: Path | None = None
        self.response_bytes: int = 0
        self.response_parses: bool = False
        self.timed_out: bool = False

    @property
    def ok(self) -> bool:
        return (
            not self.timed_out
            and self.returncode == 0
            and self.response_bytes > 0
            and self.response_parses
        )

    @property
    def verdict(self) -> str:
        if self.timed_out:
            return "NO-VERDICT (timeout)"
        if self.returncode != 0:
            return f"FAILED (exit {self.returncode})"
        if self.response_bytes == 0:
            return "FAILED (exit 0 but empty response)"
        if not self.response_parses:
            return "FAILED (exit 0 but unparseable response)"
        return "ok"


def run_step(
    name: str,
    argv: list[str],
    response_path: Path,
    timeout_s: float,
    *,
    runner=subprocess.run,
) -> StepResult:
    """Run one klt command, stdout -> ``response_path``, stderr captured.

    stdout goes to the file byte for byte, exactly as the shell redirect did,
    so a 0-byte response reproduces rather than being papered over -- but the
    exit status and stderr that make such a response filable are kept.
    """
    result = StepResult(name, argv)
    t0 = time.monotonic()
    try:
        completed = runner(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        result.timed_out = True
        result.seconds = time.monotonic() - t0
        result.stderr = _decode(exc.stderr)
        response_path.write_bytes(_as_bytes(exc.stdout))
    else:
        result.seconds = time.monotonic() - t0
        result.returncode = completed.returncode
        result.stderr = _decode(completed.stderr)
        response_path.write_bytes(_as_bytes(completed.stdout))

    result.response_path = response_path
    result.response_bytes = response_path.stat().st_size
    if result.response_bytes:
        _, reason = load_json(response_path)
        result.response_parses = reason is None
    return result


def _as_bytes(raw) -> bytes:
    if raw is None:
        return b""
    if isinstance(raw, bytes):
        return raw
    return str(raw).encode()


def _decode(raw) -> str:
    if raw is None:
        return ""
    if isinstance(raw, bytes):
        return raw.decode(errors="replace")
    return str(raw)


def format_run_log(
    *,
    klt_version: str,
    pdk_root: Path,
    pdk: str,
    steps: list[StepResult],
    netlist_note: str,
    exit_code: int,
) -> str:
    """Render the committed ``run.log``.

    Deliberately includes the exit status of every step. The shell version
    recorded only timings and two patch counters, which is why the 0-byte
    ``par_response.json`` could not be attributed to an invocation.
    """
    lines = [
        klt_version,
        f"pdk {pdk} @ {pdk_root}",
        "driver pnr/klt/ladder_dp/run_klt.py (no netlist patches)",
        netlist_note,
    ]
    for step in steps:
        lines.append("")
        lines.append(f"[{step.name}] {' '.join(step.argv)}")
        lines.append(
            f"[{step.name}] exit="
            f"{'timeout' if step.timed_out else step.returncode}"
            f" seconds={step.seconds:.0f}"
            f" response_bytes={step.response_bytes}"
            f" response_parses={step.response_parses}"
            f" verdict={step.verdict}"
        )
        stderr_lines = step.stderr.splitlines()
        if not stderr_lines:
            lines.append(f"[{step.name}] stderr: empty")
        else:
            shown = stderr_lines[-STDERR_TAIL_LINES:]
            elided = len(stderr_lines) - len(shown)
            lines.append(
                f"[{step.name}] stderr: {len(stderr_lines)} lines"
                + (f" (first {elided} elided)" if elided else "")
            )
            lines.extend(f"[{step.name}] | {line}" for line in shown)
    lines.append("")
    lines.append(f"exit {exit_code} ({_exit_name(exit_code)})")
    return "\n".join(lines) + "\n"


def _exit_name(code: int) -> str:
    return {
        EXIT_OK: "ok",
        EXIT_FAILED: "FAILED",
        EXIT_REFUSED: "REFUSED",
        EXIT_NO_VERDICT: "NO-VERDICT",
    }.get(code, "unknown")


def verdict_exit_code(steps: list[StepResult]) -> int:
    """Map step outcomes onto this driver's exit code."""
    if any(step.timed_out for step in steps):
        return EXIT_NO_VERDICT
    if all(step.ok for step in steps):
        return EXIT_OK
    return EXIT_FAILED


def resolve_netlist(here: Path, synth_response_path: Path) -> tuple[Path | None, str]:
    """Return the netlist ``klt synthesize`` says it wrote, and a note.

    The response's ``netlist_path`` is repo-scoped, so it is resolved against
    the repository root rather than against this directory.
    """
    response, reason = load_json(synth_response_path)
    if response is None:
        return None, f"netlist: unknown ({reason})"
    netlist_path = response.get("netlist_path") or {}
    rel = netlist_path.get("path")
    if not rel:
        return None, "netlist: synth response has no netlist_path.path"
    repo_root = here.parents[2]
    candidate = Path(rel) if os.path.isabs(rel) else repo_root / rel
    if not candidate.exists():
        return None, f"netlist: {rel} declared by the response does not exist"
    return candidate, f"netlist {rel}"


def build_par_request(
    template_path: Path, netlist: Path, out_path: Path
) -> tuple[Path | None, str | None]:
    """Write a place-and-route request bound to ``netlist``.

    The committed template carries :data:`NETLIST_SENTINEL`; this substitutes
    the **absolute** path ``klt synthesize`` reported, so the derived request
    does not depend on where it is written. Returns ``(path, None)`` or
    ``(None, reason)``.
    """
    template, reason = load_json(template_path)
    if template is None:
        return None, reason
    if template.get("netlist") != NETLIST_SENTINEL:
        return None, (
            f"{template_path} netlist is {template.get('netlist')!r}, not "
            f"{NETLIST_SENTINEL!r}"
        )
    template["netlist"] = str(netlist.resolve())
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(template, indent=2) + "\n")
    return out_path, None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run klt synthesize + place-and-route on ladder_dp with no "
            "netlist patches (issue #765)."
        )
    )
    parser.add_argument(
        "--pdk-root",
        default=os.environ.get("PDK_ROOT"),
        help=(
            "PDK install root containing <pdk>/libs.ref/<cell_library>; "
            "no default (falls back to $PDK_ROOT only)"
        ),
    )
    parser.add_argument("--pdk", default=os.environ.get("PDK", "gf180mcuD"))
    parser.add_argument(
        "--timeout-s",
        type=float,
        default=10800.0,
        help="per-step wall-clock cap; a hit is NO-VERDICT, not a failure",
    )
    parser.add_argument(
        "--here",
        default=str(HERE),
        help="directory holding synth_request.json / par_request.json",
    )
    args = parser.parse_args(argv)

    here = Path(args.here).resolve()
    pdk_root = Path(args.pdk_root).resolve() if args.pdk_root else None

    problems = check_preconditions(here=here, pdk_root=pdk_root, pdk=args.pdk)
    if problems:
        print("REFUSED -- preconditions not met:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return EXIT_REFUSED

    assert pdk_root is not None  # check_preconditions guarantees this
    klt_version = subprocess.run(
        ["klt", "version"], capture_output=True, text=True, check=False
    ).stdout.strip() or "unknown"
    print(klt_version)

    steps: list[StepResult] = []
    synth_response = here / "synth_response.json"
    par_response = here / "par_response.json"

    steps.append(
        run_step(
            "synthesize",
            [
                "klt", "synthesize",
                "--pdk", args.pdk,
                "--pdk-root", str(pdk_root),
                str(here / "synth_request.json"),
                "--format", "json",
            ],
            synth_response,
            args.timeout_s,
        )
    )
    print(f"synthesize: {steps[-1].verdict} in {steps[-1].seconds:.0f} s")

    netlist_note = "netlist: not produced"
    if steps[-1].ok:
        netlist, netlist_note = resolve_netlist(here, synth_response)
        if netlist is None:
            exit_code = EXIT_FAILED
            (here / "run.log").write_text(
                format_run_log(
                    klt_version=klt_version,
                    pdk_root=pdk_root,
                    pdk=args.pdk,
                    steps=steps,
                    netlist_note=netlist_note,
                    exit_code=exit_code,
                )
            )
            print(netlist_note, file=sys.stderr)
            return exit_code

        # "Check that the thing you are testing is the thing that ships."
        # Nothing in this driver edits the netlist; this hash pair is what
        # catches an in-place edit by anything that does, which is the one
        # shape the par_request netlist check in check_preconditions cannot
        # see.
        before = sha256_file(netlist)
        # Deliberately a sibling of the template, not inside `.klt/`: klt
        # derives its output directory from the *request file's* directory, so
        # a request under `.klt/` routes into `.klt/.klt/place-and-route/`.
        # Gitignored (`pnr/klt/*/par_request.resolved.json`).
        derived, reason = build_par_request(
            here / "par_request.json",
            netlist,
            here / DERIVED_PAR_REQUEST_NAME,
        )
        if derived is None:
            netlist_note = f"{netlist_note} {before} (not routed: {reason})"
            (here / "run.log").write_text(
                format_run_log(
                    klt_version=klt_version,
                    pdk_root=pdk_root,
                    pdk=args.pdk,
                    steps=steps,
                    netlist_note=netlist_note,
                    exit_code=EXIT_FAILED,
                )
            )
            print(reason, file=sys.stderr)
            return EXIT_FAILED
        steps.append(
            run_step(
                "place-and-route",
                [
                    "klt", "place-and-route",
                    "--pdk", args.pdk,
                    "--pdk-root", str(pdk_root),
                    str(derived),
                    "--format", "json",
                ],
                par_response,
                args.timeout_s,
            )
        )
        print(f"place-and-route: {steps[-1].verdict} in {steps[-1].seconds:.0f} s")
        after = sha256_file(netlist)
        netlist_note = f"{netlist_note} {before}"
        if before != after:
            netlist_note += f" MUTATED DURING RUN -> {after}"

    exit_code = verdict_exit_code(steps)
    if "MUTATED DURING RUN" in netlist_note:
        exit_code = EXIT_FAILED

    (here / "run.log").write_text(
        format_run_log(
            klt_version=klt_version,
            pdk_root=pdk_root,
            pdk=args.pdk,
            steps=steps,
            netlist_note=netlist_note,
            exit_code=exit_code,
        )
    )
    print(f"exit {exit_code} ({_exit_name(exit_code)}); see {here / 'run.log'}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())

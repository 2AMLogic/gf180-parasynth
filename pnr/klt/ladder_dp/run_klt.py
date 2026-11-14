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

The retirement's premise is re-checked on every run
---------------------------------------------------
Deleting the patches leaves nothing watching for their return. So where the
patches used to run, :func:`audit_retired_patches` counts what each one would
still have to do -- using each patch's **own** matcher -- and the run FAILS,
in a second, if any count is non-zero. Without it a klt regression would
surface where it originally did: hours into OpenSTA or TritonRoute, as an
opaque ``STA-0171``/``DRT-0305`` with nothing pointing back at the netlist.
The counts go into ``run.log``, so each run states the premise as a measured
number instead of inheriting it from this docstring.

Containers outlive the caller
-----------------------------
``openroad`` on the provisioned hosts is a generated ``docker run --rm -i``
wrapper, not a binary. A container is in no process group of ours, so when
klt is killed -- by this driver's own ``--timeout-s``, or by a sweep reaping
its agent -- **the route carries on inside the container and writes its DEF,
ODB, DRC report and metrics into the repository hours later**, with no
response, no stage commit record and no invocation to attribute them to.

That is not a hypothetical: it is where two of this directory's committed
artefacts came from. September 2026 -- response 0 bytes at 01:37, route
artefacts at 15:57, and the shell script's ``place-and-route`` timing line
never printed because it was killed before reaching it. 2026-11-13 -- same
shape, five hours apart, from a run of *this* driver. :func:`run_step` now
runs klt in its own process group, kills the **group** on timeout, and then
kills the OpenROAD containers that are demonstrably this run's
(:func:`own_openroad_containers`) -- never another sweep's.

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
import re
import shutil
import signal
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

#: ``klt place-and-route``'s stages, in order (klt's own ``STAGE_ORDER``).
#: A run that reaches the requested stage is a success by klt's contract, so
#: a bounded run is a real result about the stages it reached and says
#: nothing whatever about the ones it did not.
STAGE_ORDER = ("floorplan", "place", "cts", "route")

#: Seconds between the process group's SIGTERM and its SIGKILL on a timeout.
KILL_GRACE_S = 10.0

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

#: The three patches this issue retired, each as the matcher the patch
#: itself used, so "would this patch still have work to do?" is answered by
#: the patch's own definition of work rather than by a paraphrase of it.
#: Transcribed from ``git show b9930a4a:pnr/klt/ladder_dp/run-klt.sh``:
#: the ``sed`` address, and the two ``re`` patterns in its heredoc.
RETIRED_PATCHES: tuple[tuple[str, str, str], ...] = (
    (
        "patch 1 (sed, STA-0171)",
        r"^[ \t]*(?:input|output|inout|wire|reg)[ \t]+signed[ \t]+",
        "signed port/wire declarations",
    ),
    (
        "patch 2 (tie cells, DRT-0305)",
        r"^\s*assign\s+(\S+)\s*=\s*(1'[hb][01])\s*;",
        "bare 1-bit constant assigns",
    ),
    (
        "patch 3 (all-x assigns, DRT-0305)",
        r"^\s*assign\s+\S+\s*=\s*\d+'[hb]x+\s*;",
        "all-x assigns with no load",
    ),
)

#: Counted and reported, never gated on. ``__tiel``/``__tieh`` instances are
#: how klt now does patch 2's job itself, but a netlist with no constants to
#: tie would legitimately have none, so a ``> 0`` gate here would be red on a
#: correct netlist -- "an unsatisfiable gate is worse than no gate".
TIE_CELL_PATTERN = r"gf180mcu_fd_sc_mcu7t5v0__tie[hl]\b"

#: Patch 2 only ever matched **1-bit** constants (``1'h0``/``1'b1``). A wider
#: constant assign is the same defect class in a shape that patch never
#: handled, so it is reported next to the gated counts and not folded into
#: them -- see this module's "What defeats the audit" note.
WIDE_CONSTANT_PATTERN = r"^\s*assign\s+\S+\s*=\s*(?!1')\d+'[hb][0-9a-fA-F_]+\s*;"

#: ``openroad`` on the provisioned hosts is not a binary: it is a generated
#: ``docker run --rm -i`` wrapper (``klayout-tools``
#: ``scripts/install-openroad-docker.sh``) around
#: ``openroad/orfs@sha256:bb7f3169…``. A timed-out or reaped klt therefore
#: leaks a *container*, not a child process, and no process-group kill can
#: reach it -- see :func:`own_openroad_containers` for how one is identified
#: as this run's, and this module's "Containers outlive the caller" note for
#: what that leak did to this repository's committed evidence.
DOCKER = "docker"


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


def audit_retired_patches(netlist: Path) -> tuple[dict[str, int], list[str]]:
    """Count what each retired patch would still have to do, in ``netlist``.

    Returns ``(counts, complaints)``. ``counts`` always carries one entry per
    :data:`RETIRED_PATCHES` label plus the two reported-only observations;
    ``complaints`` is non-empty exactly when some retired patch still has
    work, which is this driver's whole claim turned into a check.

    Why this exists where the patches used to be: with the patches deleted,
    a klt regression that reintroduced any of these three shapes would not
    fail here -- it would fail hours later inside OpenSTA (``STA-0171``) or
    TritonRoute (``DRT-0305``), as an opaque error with nothing pointing back
    at the netlist, which is the exact pathology klayout-tools#2085 was filed
    for. The counts also go into ``run.log``, so every run states the
    retirement's premise as a measured number rather than inheriting it.

    What defeats this audit, stated rather than left to be discovered
    (``docs/verification-rules.md`` rule 8): it is the three retired patches'
    **own** matchers, so it detects the return of precisely the shapes those
    patches handled and nothing more. A constant assign wider than one bit is
    the same defect class in a shape patch 2 never matched; it is counted and
    reported (:data:`WIDE_CONSTANT_PATTERN`) but deliberately not gated on,
    because it was never evidence about these patches and a run that routes
    with such an assign is not a regression. It also cannot see a patch
    applied to the RTL before synthesize, which is a different claim.
    """
    text = netlist.read_text(errors="replace")
    counts: dict[str, int] = {}
    complaints: list[str] = []
    for label, pattern, what in RETIRED_PATCHES:
        n = len(re.findall(pattern, text, flags=re.M))
        counts[label] = n
        if n:
            complaints.append(
                f"{label} would still have work: {n} {what} in "
                f"{netlist.name}. The patch was retired on the premise that "
                "this count is 0; it is not, so the premise no longer holds."
            )
    counts["klt's own tie-cell instances"] = len(
        re.findall(TIE_CELL_PATTERN, text)
    )
    counts["wider-than-1-bit constant assigns (reported, not gated)"] = len(
        re.findall(WIDE_CONSTANT_PATTERN, text, flags=re.M)
    )
    return counts, complaints


def own_openroad_containers(
    here: Path, *, runner=subprocess.run
) -> list[str]:
    """Return ids of running containers this run's klt started, or ``[]``.

    Ownership test, and it is deliberately the narrow one: a container counts
    as this run's only when one of its **command arguments** is an absolute
    path inside ``here``. klt passes OpenROAD its generated ``.tcl`` script
    and ``-metrics`` path by absolute path, both under
    ``here/.klt/place-and-route/``, so the test is exact.

    It is not the obvious test. The wrapper also bind-mounts ``$PWD``, and
    matching on mounts would match a container started from a *parent* of
    ``here`` -- the main checkout mounts every worktree under it -- so a
    mount-based test could kill another agent's run. Nothing in this
    repository may kill another sweep's container, so the test that could is
    not used. The cost of the narrow test is that a container started from
    outside ``here`` is left alone, which is the right way round.
    """
    listed = runner(
        [DOCKER, "ps", "--quiet", "--no-trunc"],
        capture_output=True,
        text=True,
        check=False,
    )
    if listed.returncode != 0:
        return []
    prefix = str(here.resolve())
    mine: list[str] = []
    for container in listed.stdout.split():
        inspected = runner(
            [DOCKER, "inspect", "--format", "{{json .Args}}", container],
            capture_output=True,
            text=True,
            check=False,
        )
        if inspected.returncode != 0:
            continue
        try:
            argv = json.loads(inspected.stdout or "null") or []
        except (json.JSONDecodeError, TypeError):
            continue
        if any(
            isinstance(arg, str) and arg.startswith(prefix) for arg in argv
        ):
            mine.append(container)
    return mine


def stop_own_openroad_containers(
    here: Path, *, runner=subprocess.run
) -> list[str]:
    """``docker kill`` every container :func:`own_openroad_containers` names."""
    stopped: list[str] = []
    for container in own_openroad_containers(here, runner=runner):
        killed = runner(
            [DOCKER, "kill", container],
            capture_output=True,
            text=True,
            check=False,
        )
        if killed.returncode == 0:
            stopped.append(container)
    return stopped


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
        self.group_killed: bool = False
        self.containers_stopped: list[str] = []

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
    here: Path | None = None,
    popen=subprocess.Popen,
    container_reaper=stop_own_openroad_containers,
    grace_s: float = KILL_GRACE_S,
) -> StepResult:
    """Run one klt command, stdout -> ``response_path``, stderr captured.

    stdout goes to the file byte for byte, exactly as the shell redirect did,
    so a 0-byte response reproduces rather than being papered over -- but the
    exit status and stderr that make such a response filable are kept.

    **Nothing this starts may outlive it.** klt runs in its own process group
    (``start_new_session``) and a timeout kills the *group*, not just klt:
    ``subprocess.run(timeout=…)`` kills the direct child only, so the
    grandchild carries on. Then, because ``openroad`` is a ``docker run``
    wrapper and a container is not in any process group of ours, the
    containers this run owns are killed too (:func:`own_openroad_containers`;
    skipped when ``here`` is ``None``).

    This is not hypothetical and not a style point. It is the mechanism
    behind this directory's own unattributable evidence, twice: a klt killed
    mid-route leaves a 0-byte response and an OpenROAD container that keeps
    routing and writes a DEF, an ODB, a DRC report and a metrics file into
    the repository **hours after the run that is supposed to have produced
    them is gone** -- 14 h in September 2026, 5 h on 2026-11-13. Evidence
    whose invocation cannot be named is not evidence; see
    ``docs/tool-findings.md``.
    """
    result = StepResult(name, argv)
    t0 = time.monotonic()
    process = popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout_s)
        result.returncode = process.returncode
    except subprocess.TimeoutExpired:
        result.timed_out = True
        result.group_killed = _kill_group(process, grace_s=grace_s)
        if here is not None:
            result.containers_stopped = container_reaper(here)
        try:
            stdout, stderr = process.communicate(timeout=grace_s)
        except subprocess.TimeoutExpired:  # pragma: no cover - last resort
            stdout, stderr = b"", b""
    result.seconds = time.monotonic() - t0
    result.stderr = _decode(stderr)
    response_path.write_bytes(_as_bytes(stdout))

    result.response_path = response_path
    result.response_bytes = response_path.stat().st_size
    if result.response_bytes:
        _, reason = load_json(response_path)
        result.response_parses = reason is None
    return result


def _kill_group(process, *, grace_s: float) -> bool:
    """SIGTERM then SIGKILL the whole process group. True if a signal landed.

    Falls back to killing the process alone when it has no group of its own
    (a fake in a test, or a platform without ``killpg``): a narrower kill is
    better than an exception that skips the container reaper below it.
    """
    try:
        pgid = os.getpgid(process.pid)
    except (AttributeError, OSError, ProcessLookupError):
        process.kill()
        return False
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pgid, sig)
        except (OSError, ProcessLookupError):
            return True
        if sig is signal.SIGTERM:
            try:
                process.wait(timeout=grace_s)
                return True
            except subprocess.TimeoutExpired:
                continue
    return True


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
    target_stage: str = "route",
    patch_audit: dict[str, int] | None = None,
) -> str:
    """Render the committed ``run.log``.

    Deliberately includes the exit status of every step. The shell version
    recorded only timings and two patch counters, which is why the 0-byte
    ``par_response.json`` could not be attributed to an invocation.
    """
    reached = (
        f"target_stage {target_stage}"
        if target_stage == STAGE_ORDER[-1]
        else (
            f"target_stage {target_stage} -- BOUNDED RUN: the stages after "
            f"{target_stage} were not attempted and this run says nothing "
            "about them"
        )
    )
    lines = [
        klt_version,
        f"pdk {pdk} @ {pdk_root}",
        "driver pnr/klt/ladder_dp/run_klt.py (no netlist patches)",
        reached,
        netlist_note,
    ]
    if patch_audit is not None:
        lines.append("")
        lines.append(
            "retired-patch audit (each gated count must be 0 -- the "
            "retirement's premise, measured, not inherited):"
        )
        for label, count in patch_audit.items():
            lines.append(f"  {label}: {count}")
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
        if step.timed_out:
            lines.append(
                f"[{step.name}] on timeout: process group killed="
                f"{step.group_killed} openroad containers stopped="
                f"{len(step.containers_stopped)}"
                + (
                    f" {' '.join(c[:12] for c in step.containers_stopped)}"
                    if step.containers_stopped
                    else ""
                )
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
    template_path: Path,
    netlist: Path,
    out_path: Path,
    *,
    target_stage: str | None = None,
) -> tuple[Path | None, str | None]:
    """Write a place-and-route request bound to ``netlist``.

    The committed template carries :data:`NETLIST_SENTINEL`; this substitutes
    the **absolute** path ``klt synthesize`` reported, so the derived request
    does not depend on where it is written. Returns ``(path, None)`` or
    ``(None, reason)``.

    ``target_stage``, when given, overrides the template's own -- the one
    field a caller may bound. It is written into the *derived* request only:
    the committed template keeps saying ``route``, so what this repository
    asks for does not quietly become whatever last fitted in a time budget.
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
    if target_stage is not None:
        template["target_stage"] = target_stage
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
        "--target-stage",
        choices=STAGE_ORDER,
        default=None,
        help=(
            "stop place-and-route after this stage, overriding the committed "
            "request's target_stage. A bounded run is a real result about "
            "the stages it reached and no result at all about the rest; "
            "run.log says so in those words. Exists because a full route of "
            "this design is hours of OpenROAD and a dispatch worker cannot "
            "hold one, and a bounded record beats an unattributable one"
        ),
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
    target_stage = args.target_stage
    patch_audit: dict[str, int] | None = None
    committed_par_request, _ = load_json(here / "par_request.json")
    request_target_stage = (committed_par_request or {}).get(
        "target_stage", STAGE_ORDER[-1]
    )

    def write_log(exit_code: int, netlist_note: str) -> int:
        """Write ``run.log`` and return ``exit_code``.

        Every return path out of this function goes through here, so there is
        no path that runs klt and leaves no record of what it exited with --
        the absence that made the 0-byte ``par_response.json`` unfilable.
        """
        (here / "run.log").write_text(
            format_run_log(
                klt_version=klt_version,
                pdk_root=pdk_root,
                pdk=args.pdk,
                steps=steps,
                netlist_note=netlist_note,
                exit_code=exit_code,
                target_stage=target_stage or request_target_stage,
                patch_audit=patch_audit,
            )
        )
        print(f"exit {exit_code} ({_exit_name(exit_code)}); see {here / 'run.log'}")
        return exit_code

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
            here=here,
        )
    )
    print(f"synthesize: {steps[-1].verdict} in {steps[-1].seconds:.0f} s")

    if not steps[-1].ok:
        return write_log(verdict_exit_code(steps), "netlist: not produced")

    netlist, netlist_note = resolve_netlist(here, synth_response)
    if netlist is None:
        print(netlist_note, file=sys.stderr)
        return write_log(EXIT_FAILED, netlist_note)

    # The retired patches' own matchers, run where the patches used to run.
    # A regression that reintroduced any of the three shapes must turn red
    # HERE, in a second, rather than hours later as an opaque STA-0171 or
    # DRT-0305 with nothing pointing back at the netlist.
    patch_audit, complaints = audit_retired_patches(netlist)
    for label, count in patch_audit.items():
        print(f"audit: {label}: {count}")
    if complaints:
        for complaint in complaints:
            print(f"FAILED -- {complaint}", file=sys.stderr)
        return write_log(
            EXIT_FAILED,
            f"{netlist_note} {sha256_file(netlist)} "
            f"(not routed: retired-patch audit found work remaining)",
        )

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
        target_stage=target_stage,
    )
    if derived is None:
        print(reason, file=sys.stderr)
        return write_log(
            EXIT_FAILED, f"{netlist_note} {before} (not routed: {reason})"
        )

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
            here=here,
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
    return write_log(exit_code, netlist_note)


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""finish_halfslot.py -- carry the in-flight half-slot LibreLane run to a
post-route result without a session having to sit on it.

    ./finish_halfslot.py drc      --run-dir R      # router violations, two sources
    ./finish_halfslot.py wait     [--run-dir R]    # block until the container exits
    ./finish_halfslot.py relocate --from S --to D  # move a run back under the worktree
    ./finish_halfslot.py finish   --stray-run S    # wait, relocate, resume, report

WHY THIS EXISTS AT ALL.  ``docs/pnr-shuttle-halfslot.md`` §7 says the run "was not
finished": it was still in ``OpenROAD.DetailedRouting`` when the page was generated,
and the sweep that started it had already died.  Finishing it is three commands
(``run_librelane.py resume``, ``check_route.py``, ``report_halfslot.py``) separated
by hours of routing, and the last two agents to hold that gap lost it -- one sweep
exited and stranded the run, one page shipped with two rows reading *not measured*.
A detached driver is the fix: the commands are ordered on disk rather than in an
agent's context, so the result survives the session.

WHY ``relocate`` EXISTS, WHICH IS THE UNOBVIOUS PART.  LibreLane writes its run
directory beside the design config -- inside the worktree.  The worktree holding
*this* run was moved aside (to ``/home/ubuntu/loom-run-i33``) and recreated while the
container was still routing.  A bind mount follows the inode, not the path, so the
container kept writing into the moved directory and its state files still name the
ORIGINAL worktree path.  So:

  * the run is intact and its absolute paths are still correct,
  * but a new container started from the recreated worktree would mount a tree with
    no ``librelane/runs/halfslot`` in it at all.

``run_librelane.py resume`` REFUSES in that situation (by design), which is the right
behaviour and not a fix.  The fix is to move the run back under the worktree path its
own state files name, which is a rename on one filesystem, and only ever with the
container gone.

REFUSED is a first-class outcome and exits 3.  Every refusal below guards a failure
that is quiet rather than loud -- a run that continues and reports a plausible number
from the wrong state:

  * relocating while a container still holds the source: the router would be writing
    into a directory that no longer has that name, and the move would look like it
    worked.
  * relocating onto an existing run directory: two runs' step outputs interleaved by
    step number, and ``--from`` resolving against whichever won.
  * resuming when detailed routing never recorded a ``state_out.json``: LibreLane
    resolves ``--from`` against the last state it can find, so post-route STA would
    silently report on the PRE-route layout.
  * a router DRC count taken from only one of the two places that hold it.  The
    metric ``route__drc_errors`` and the router's own log are independent; this script
    requires them to agree and refuses if they do not.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))

REFUSED = 3

LIBRELANE_IMAGE_PREFIX = "ghcr.io/librelane/librelane"

# LibreLane names each executed step `<NN>-<step id lowercased, dots removed>`.
STEP_DIR = re.compile(r"^(\d+)-(.+)$")

# The detailed router prints this once per iteration; the LAST one is the result.
# `Viol/Layer` + one line per layer follows it when the count is non-zero.
DRT_VIOL = re.compile(r"^\[INFO DRT-0199\]\s+Number of violations = (\d+)\.", re.M)
# `Start 46th stubborn tiles iteration.` / `Start 33rd optimization iteration.` --
# the " tiles" is present for two of the three kinds, and requiring it silently
# dropped every `optimization` iteration from the table.
DRT_ITER = re.compile(r"^\[INFO DRT-0195\] Start (\d+)\w* (\w+)(?: tiles?)? iteration",
                      re.M)

# The step whose metrics are the post-route timing.  Named here rather than
# discovered so that a flow which never reached it cannot be mistaken for one that
# did and measured nothing.
POST_ROUTE_STA = "openroad-stapostpnr"
DETAILED_ROUTING = "openroad-detailedrouting"

# WHERE TO RESUME, AND WHY IT IS NOT ``OpenROAD.RCX``.  The Chip flow's steps after
# detailed routing are, in order:
#
#     Odb.RemoveRoutingObstructions      <- resume here
#     OpenROAD.CheckAntennas-1               the POST-ROUTE antenna check
#     Checker.TrDRC                          aborts the flow if ERROR_ON_TR_DRC
#     Odb.ReportDisconnectedPins / Checker.DisconnectedPins
#     Odb.ReportWireLength / Checker.WireLength
#     OpenROAD.FillInsertion                 the fillers
#     Odb.CellFrequencyTables
#     OpenROAD.RCX                           parasitics
#     OpenROAD.STAPostPNR                    per-corner post-route timing
#
# Resuming at ``OpenROAD.RCX`` -- the obvious choice, and the one this script had
# first -- silently skips NINE steps, including the post-route antenna check that
# #33 asks for by name and the filler insertion whose *absence* is the shape of
# klayout-tools#2086.  It would have produced post-route timing for a die with no
# fillers and called the flow finished.  So resume at the FIRST step after the
# route and let everything run.
RESUME_FROM = "Odb.RemoveRoutingObstructions"

# Checker.TrDRC is what ``--from RESUME_FROM`` runs into.  It is made non-fatal FOR
# THIS RUN ONLY, on the command line, rather than by editing the committed config:
# a repository whose config says "do not stop on router DRC" has changed what every
# future run means, and the override belongs where a reader of the command sees it.
# The count itself is NOT suppressed -- it is in `route__drc_errors`, in the router's
# own log, printed by `drc` above, and in the report's own table from both sources.
TR_DRC_OVERRIDE = "ERROR_ON_TR_DRC=false"


class Refusal(Exception):
    """A precondition of the apparatus is not met.  Distinct from a tool failure."""


# ---------------------------------------------------------------- run inspection


def step_dirs(run_dir: str) -> list[str]:
    if not os.path.isdir(run_dir):
        return []
    return sorted(n for n in os.listdir(run_dir) if STEP_DIR.match(n))


def find_step(run_dir: str, suffix: str) -> str | None:
    """The directory of the step whose id ends in `suffix`, or None.

    Matches on the suffix because the numeric prefix depends on how many steps the
    configured flow ran before it, and that moves when a skip list changes.
    """
    for name in step_dirs(run_dir):
        if STEP_DIR.match(name).group(2) == suffix:
            return name
    return None


def step_completed(run_dir: str, suffix: str) -> bool:
    """True iff that step ran AND recorded a state.

    A step directory exists as soon as the step *starts* -- LibreLane writes
    `config.json` and `state_in.json` up front.  `state_out.json` is the only file
    that means it finished, and the difference is exactly the difference between a
    route that completed and one that was killed mid-iteration.
    """
    name = find_step(run_dir, suffix)
    return bool(name) and os.path.exists(os.path.join(run_dir, name, "state_out.json"))


def parse_drt_log(text: str) -> dict:
    """The detailed router's own final violation count, from its log.

    Independent of the metrics payload: different producer, different file.  Returns
    the LAST reported count, the iteration it belongs to, and the per-layer
    breakdown that follows it.
    """
    counts = list(DRT_VIOL.finditer(text))
    iters = list(DRT_ITER.finditer(text))
    if not counts:
        return {"violations": None, "iteration": None, "by_layer": {}, "iterations_seen": 0}
    last = counts[-1]
    by_layer: dict[str, int] = {}
    tail = text[last.end():]
    # `Viol/Layer<ws>Metal2` then `Short<ws>3`; layers are columns, types are rows.
    head = re.search(r"^Viol/Layer\s+(.+)$", tail, re.M)
    if head:
        layers = head.group(1).split()
        seen_row = False
        for line in tail[head.end():].splitlines():
            if not line.strip():
                # The header is followed by a newline before the first row, so a
                # blank line only ends the table once a row has been read.
                if seen_row:
                    break
                continue
            parts = line.split()
            if len(parts) != len(layers) + 1:
                break
            for layer, n in zip(layers, parts[1:]):
                if n.isdigit():
                    by_layer[layer] = by_layer.get(layer, 0) + int(n)
            seen_row = True
    iteration = None
    for m in iters:
        if m.start() < last.start():
            iteration = int(m.group(1))
    return {
        "violations": int(last.group(1)),
        "iteration": iteration,
        "by_layer": by_layer,
        "iterations_seen": len(iters),
    }


DRT_ELAPSED = re.compile(
    r"^\[INFO DRT-0267\] cpu time = (\d+):(\d\d):(\d\d), elapsed time = (\d+):(\d\d):(\d\d)",
    re.M)


def parse_drt_iterations(text: str) -> list[dict]:
    """One record per router iteration: number, kind, elapsed seconds, violations.

    The point of this is not progress reporting -- it is the evidence for §6.5.  The
    router stops improving long before it stops iterating, and only a per-iteration
    table shows that: the count is flat while the elapsed time per iteration grows by
    two orders of magnitude.  A single final number cannot show it.
    """
    events = []
    for m in DRT_ITER.finditer(text):
        events.append(("start", m.start(), int(m.group(1)), m.group(2)))
    for m in DRT_VIOL.finditer(text):
        events.append(("viol", m.start(), int(m.group(1)), None))
    for m in DRT_ELAPSED.finditer(text):
        secs = int(m.group(4)) * 3600 + int(m.group(5)) * 60 + int(m.group(6))
        cpu = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
        events.append(("time", m.start(), secs, cpu))
    events.sort(key=lambda e: e[1])
    out: list[dict] = []
    for kind, _pos, a, b in events:
        if kind == "start":
            out.append({"iteration": a, "kind": b, "violations": None,
                        "elapsed_s": None, "cpu_s": None})
        elif out and kind == "viol" and out[-1]["violations"] is None:
            out[-1]["violations"] = a
        elif out and kind == "time" and out[-1]["elapsed_s"] is None:
            out[-1]["elapsed_s"] = a
            out[-1]["cpu_s"] = b
    return out


def metrics_of(run_dir: str, step_suffix: str) -> dict:
    name = find_step(run_dir, step_suffix)
    if not name:
        return {}
    path = os.path.join(run_dir, name, "state_out.json")
    if not os.path.exists(path):
        return {}
    return json.load(open(path, encoding="utf-8")).get("metrics", {}) or {}


def drc_verdict(run_dir: str) -> dict:
    """Router DRC from BOTH the metric and the router's log, and refuse if they differ.

    `docs/pnr-synth-top.md`'s "0 DRC" caution is about what the number means; this is
    the weaker but orthogonal question of whether the number is the run's.  A count
    read from one place cannot tell a stale payload from a fresh one.
    """
    step = find_step(run_dir, DETAILED_ROUTING)
    if not step:
        raise Refusal(f"{run_dir} has no {DETAILED_ROUTING} step -- nothing routed")
    log = os.path.join(run_dir, step, "openroad-detailedrouting.log")
    if not os.path.exists(log):
        raise Refusal(f"{step} has no openroad-detailedrouting.log")
    from_log = parse_drt_log(open(log, encoding="utf-8", errors="replace").read())
    metrics = metrics_of(run_dir, DETAILED_ROUTING)
    from_metric = metrics.get("route__drc_errors")
    if from_metric is not None and from_log["violations"] is not None:
        if int(from_metric) != int(from_log["violations"]):
            raise Refusal(
                f"router DRC disagrees between its two sources: metric "
                f"route__drc_errors = {from_metric}, the router's own log reports "
                f"{from_log['violations']}. One of them is not this run's."
            )
    return {
        "step": step,
        "completed": step_completed(run_dir, DETAILED_ROUTING),
        "route__drc_errors": from_metric,
        "log_violations": from_log["violations"],
        "log_iteration": from_log["iteration"],
        "log_iterations_seen": from_log["iterations_seen"],
        "by_layer": from_log["by_layer"],
    }


# ---------------------------------------------------------------- the container


def librelane_containers() -> list[str]:
    """Ids of running LibreLane containers, or REFUSE if docker cannot be asked.

    An empty list must mean "none running", never "could not tell" -- the whole
    point of the check is that relocating under a live router is silent damage.
    """
    if shutil.which("docker") is None:
        raise Refusal("docker is not on PATH; cannot tell whether a run is still live")
    p = subprocess.run(["docker", "ps", "--format", "{{.ID}} {{.Image}}"],
                       capture_output=True, text=True)
    if p.returncode != 0:
        raise Refusal(f"`docker ps` failed (exit {p.returncode}): {p.stderr.strip()}")
    out = []
    for line in p.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1].startswith(LIBRELANE_IMAGE_PREFIX):
            out.append(parts[0])
    return out


def wait_for_exit(poll: int = 60, timeout: int | None = None,
                  run_dir: str | None = None) -> dict:
    """Block until no LibreLane container is running.

    Polling happens HERE, in a loop that costs nothing, rather than in an agent that
    re-processes its whole context on every look (CLAUDE.md: "waiting is the
    expensive part").  Progress is printed so the wait is auditable after the fact.
    """
    t0 = time.time()
    last_note = ""
    while True:
        live = librelane_containers()
        if not live:
            return {"waited_s": round(time.time() - t0), "exited": True}
        note = ""
        if run_dir:
            try:
                d = drc_verdict(run_dir)
                note = (f"iteration {d['log_iteration']}, "
                        f"{d['log_violations']} violations")
            except Refusal:
                note = "no router log yet"
        if note != last_note:
            print(f"[finish_halfslot] +{round(time.time() - t0)}s "
                  f"container {live[0]} alive; {note}", flush=True)
            last_note = note
        if timeout is not None and time.time() - t0 > timeout:
            return {"waited_s": round(time.time() - t0), "exited": False}
        time.sleep(poll)


# ---------------------------------------------------------------- relocate


def relocate(src: str, dest: str, allow_live: bool = False) -> None:
    """Move a run directory to `dest`, refusing anything that could damage it."""
    if not os.path.isdir(src):
        raise Refusal(f"no run directory at {src}")
    if os.path.exists(dest):
        raise Refusal(
            f"{dest} already exists.\n"
            f"  Moving onto it would interleave two runs' step outputs by step number,\n"
            f"  and --from would resolve against whichever won."
        )
    if not allow_live:
        live = librelane_containers()
        if live:
            raise Refusal(
                f"a LibreLane container is still running ({', '.join(live)}).\n"
                f"  Moving the run out from under it leaves the router writing into a\n"
                f"  directory with no name, and the move would look like it worked."
            )
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.move(src, dest)
    print(f"[finish_halfslot] moved {src} -> {dest}", flush=True)


# ---------------------------------------------------------------- the sequence


def run(cmd: list[str], cwd: str | None = None) -> int:
    print("[finish_halfslot] $ " + " ".join(cmd), flush=True)
    # subprocess.run, not shell: the driver must be able to record a status.
    return subprocess.run(cmd, cwd=cwd).returncode


def finish(run_tag: str, stray_run: str | None, poll: int, timeout: int | None,
           dry_run: bool) -> int:
    dest = os.path.join(HERE, "librelane", "runs", run_tag)

    if stray_run and not os.path.isdir(dest):
        print(f"[finish_halfslot] waiting for the container before moving {stray_run}",
              flush=True)
        w = wait_for_exit(poll=poll, timeout=timeout, run_dir=stray_run)
        if not w["exited"]:
            print(f"REFUSED: still running after {w['waited_s']}s; nothing moved",
                  file=sys.stderr)
            return REFUSED
        if not dry_run:
            relocate(stray_run, dest)
    else:
        w = wait_for_exit(poll=poll, timeout=timeout, run_dir=dest)
        if not w["exited"]:
            print(f"REFUSED: still running after {w['waited_s']}s", file=sys.stderr)
            return REFUSED

    if not step_completed(dest, DETAILED_ROUTING):
        print(f"REFUSED: {DETAILED_ROUTING} recorded no state_out.json in {dest}.\n"
              f"  The route did not complete, so --from would resolve against the\n"
              f"  PRE-route state and post-route STA would report on the wrong layout.",
              file=sys.stderr)
        return REFUSED

    d = drc_verdict(dest)
    print("[finish_halfslot] router DRC: " + json.dumps(d), flush=True)

    if not step_completed(dest, POST_ROUTE_STA):
        # ERROR_ON_TR_DRC aborts at Checker.TrDRC, BEFORE the steps that produce the
        # post-route antenna check, the fillers, the parasitics and the per-corner
        # timing.  Carrying on past it does not silence the DRC count -- see
        # TR_DRC_OVERRIDE.
        rc = 0 if dry_run else run([
            os.path.join(HERE, "run_librelane.py"), "resume",
            "--run-tag", run_tag, "--from", RESUME_FROM,
            "--override-config", TR_DRC_OVERRIDE,
        ])
        if rc != 0:
            print(f"[finish_halfslot] resume exited {rc}; continuing to report what "
                  f"IS on disk rather than reporting nothing", flush=True)

    steps = [
        [os.path.join(HERE, "check_route.py"), "metrics", dest],
        [os.path.join(HERE, "check_route.py"), "verify", dest],
        [os.path.join(HERE, "report_halfslot.py"), dest],
    ]
    rcs = {}
    for cmd in steps:
        rcs[os.path.basename(cmd[0]) + " " + cmd[1]] = 0 if dry_run else run(cmd)
    print("[finish_halfslot] " + json.dumps({"exit_status": rcs}), flush=True)
    # `verify` is the collapse gate: a non-zero there is a red result, not a driver
    # failure, and must propagate.
    return max(rcs.values()) if rcs else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("drc", help="router violations from the metric AND the log")
    d.add_argument("--run-dir", required=True)

    w = sub.add_parser("wait", help="block until no LibreLane container is running")
    w.add_argument("--run-dir")
    w.add_argument("--poll", type=int, default=60)
    w.add_argument("--timeout", type=int)

    r = sub.add_parser("relocate", help="move a run directory under the worktree")
    r.add_argument("--from", dest="src", required=True)
    r.add_argument("--to", dest="dest", required=True)

    f = sub.add_parser("finish", help="wait, relocate, resume, report")
    f.add_argument("--run-tag", default="halfslot")
    f.add_argument("--stray-run", help="a run directory outside the worktree to move back")
    f.add_argument("--poll", type=int, default=60)
    f.add_argument("--timeout", type=int)
    f.add_argument("--dry-run", action="store_true")

    a = ap.parse_args(argv)
    try:
        if a.cmd == "drc":
            print(json.dumps(drc_verdict(a.run_dir), indent=2))
            return 0
        if a.cmd == "wait":
            print(json.dumps(wait_for_exit(a.poll, a.timeout, a.run_dir), indent=2))
            return 0
        if a.cmd == "relocate":
            relocate(a.src, a.dest)
            return 0
        return finish(a.run_tag, a.stray_run, a.poll, a.timeout, a.dry_run)
    except Refusal as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return REFUSED


if __name__ == "__main__":
    sys.exit(main())

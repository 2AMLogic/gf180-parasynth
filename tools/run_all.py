#!/usr/bin/env python3
"""Run known-independent jobs together in one turn; report each one truthfully.

WHY THIS EXISTS. An audit of one session found **89 agent wake-ups that
produced nothing** but "still running" or "ending my turn"; one agent alone did
24. Each wake reprocesses the agent's context, which is wasteful -- though the
billed cost depends on caching and the provider, so measure usage separately
from transcript turn counts rather than quoting a token figure per wake.

The cause is always serial jobs: fire one, wake, fire the next, wake.

    tools/run_all.py "verify_ladder" "verify_modal" "verify_voice --set quick"

BATCH KNOWN-INDEPENDENT WORK. Not everything: generating vectors before
simulating them, or reading a failure before choosing a diagnostic, is
legitimate sequential work. The objective is fewer *empty* turns, not
unconditional parallelism.

WHAT THIS GUARANTEES, AND WHAT IT DOES NOT

  - It reports **process exit status**, never text. A job that prints "FAIL"
    and exits 0 is reported PASS: that is a defect in the verifier, and the
    runner must not paper over it by scraping output.
  - A timeout is **NO-VERDICT**, distinct from PASS and from FAIL. A timed-out
    job reported as either is how a green run hides a hung one.
  - Each child's own exit status is preserved in the summary and the JSON
    report. Verifiers here distinguish 1 (mismatch) from 2 (did not run) and
    the runner must not erase that.
  - The overall exit code is **bounded**: 0 or 1. A raw failure count is wrong
    -- shell status is eight bits, so 256 failures would exit 0.
  - On timeout the whole **process group** is killed, so a shelled-out job's
    children do not survive. Putting supervision outside the command fixes the
    shell-exit problem specifically; it does not by itself make cleanup correct,
    which is why this is explicit.

It cannot guarantee "one turn": an agent platform may background a long tool
call regardless. It removes the *self-inflicted* serial wakes.
"""
from __future__ import annotations
import argparse, concurrent.futures as cf, json, os, signal, subprocess, sys, time

PASS, FAIL, NO_VERDICT, LAUNCH_ERROR = "PASS", "FAIL", "NO-VERDICT", "LAUNCH-ERROR"

# This repository's verifiers already distinguish "it ran and disagreed" from
# "it did not run", and the runner must not flatten that into one FAIL.
#
#   0  match          the comparison ran and agreed
#   1  mismatch       the comparison ran and disagreed  -- a RESULT
#   2  did not run    simulator missing, compile failed -- NO EVIDENCE
#
# The distinction is load-bearing for negative controls: `--expect-fail` exits
# 0 only on status 1, so a missing simulator can never be mistaken for a
# successfully caught defect. Reporting exit 2 as "FAIL" would hide the
# difference between a control that worked and a control that never ran, which
# is the difference between evidence and its absence.
VERIFIER_NO_EVIDENCE = 2


def run_one(cmd: str, timeout: float | None = None, env: dict | None = None,
            verifier: bool = True) -> dict:
    """Run one command. Never infers status from output.

    `verifier=True` applies this repo's exit-code convention, under which 2
    means "did not run" and is reported NO-VERDICT rather than FAIL. Pass
    False for a command that uses 2 to mean an ordinary error.
    """
    t0 = time.time()
    try:
        p = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True,
                             start_new_session=True, env=env)
    except Exception as exc:                       # could not launch at all
        return {"cmd": cmd, "state": LAUNCH_ERROR, "rc": None,
                "out": f"{type(exc).__name__}: {exc}", "secs": 0.0}
    try:
        out, _ = p.communicate(timeout=timeout)
        rc = p.returncode
        if rc == 0:
            state = PASS
        elif rc == VERIFIER_NO_EVIDENCE and verifier:
            state = NO_VERDICT          # it did not run; that is not a failure
        else:
            state = FAIL
    except subprocess.TimeoutExpired:
        # Kill the GROUP: shell=True means a shell is the direct child, and
        # killing it leaves the real work orphaned and still running.
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            out, _ = p.communicate(timeout=10)
        except Exception:
            out = ""
        rc, state = None, NO_VERDICT
    return {"cmd": cmd, "state": state, "rc": rc, "out": out or "",
            "secs": time.time() - t0}


def run_all(cmds: list[str], *, serial: bool = False, timeout: float | None = None,
            jobs: int | None = None, on_result=None) -> list[dict]:
    """Run commands and call `on_result(index, result)` as each one finishes."""
    results: list[dict | None] = [None] * len(cmds)

    def finished(index: int, result: dict) -> None:
        results[index] = result
        if on_result is not None:
            on_result(index, result)

    if serial or len(cmds) == 1:
        for index, cmd in enumerate(cmds):
            finished(index, run_one(cmd, timeout))
    else:
        workers = jobs or min(len(cmds), (os.cpu_count() or 4))
        with cf.ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(run_one, cmd, timeout): index
                    for index, cmd in enumerate(cmds)}
            for future in cf.as_completed(futs):
                finished(futs[future], future.result())
    if any(result is None for result in results):
        raise RuntimeError("runner finished without recording every job")
    return results


def write_json_atomic(path: str, value) -> None:
    """Replace a JSON report atomically so readers never see a partial file."""
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    temporary = f"{path}.tmp-{os.getpid()}"
    try:
        with open(temporary, "w") as fh:
            json.dump(value, fh, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def summarise(results: list[dict], tail: int = 4) -> str:
    bar = "=" * 78
    lines = [bar]
    for r in results:
        tag = r["state"] if r["state"] != FAIL else f"FAIL({r['rc']})"
        lines.append(f"{tag:<13} {r['secs']:6.1f}s  {r['cmd']}")
        for line in [l for l in r["out"].strip().splitlines() if l.strip()][-tail:]:
            lines.append(f"              {line}")
    ok = sum(1 for r in results if r["state"] == PASS)
    lines.append(bar)
    bad = [f"{r['state']} {r['cmd'][:44]}" for r in results if r["state"] != PASS]
    lines.append(f"{ok}/{len(results)} passed" + (f"  --  {'; '.join(bad)}" if bad else ""))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmds", nargs="+")
    ap.add_argument("--serial", action="store_true",
                    help="run in order; use when jobs contend for a resource")
    ap.add_argument("--jobs", type=int, default=None, help="max concurrent jobs")
    ap.add_argument("--timeout", type=float, default=None,
                    help="seconds per job; a timeout is NO-VERDICT, not FAIL")
    ap.add_argument("--tail", type=int, default=4)
    ap.add_argument("--json", metavar="PATH",
                    help=("atomically record PENDING jobs initially and update each entry "
                          "as its process finishes"))
    a = ap.parse_args(argv)

    partial = ([{"cmd": cmd, "state": "PENDING", "rc": None,
                 "out": "", "secs": 0.0} for cmd in a.cmds]
               if a.json else None)
    if a.json:
        write_json_atomic(a.json, partial)

    def persist(index, result):
        partial[index] = result
        write_json_atomic(a.json, partial)

    results = run_all(a.cmds, serial=a.serial, timeout=a.timeout, jobs=a.jobs,
                      on_result=persist if a.json else None)
    print(summarise(results, a.tail))
    if a.json:
        write_json_atomic(a.json, results)
    # Bounded: 0 or 1. The count is in the summary and the JSON, where it
    # cannot wrap round to zero.
    return 0 if all(r["state"] == PASS for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())

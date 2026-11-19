#!/usr/bin/env python3
"""One retry-wrapped entry point for the Ubuntu CI apt dependency steps.

Why (#1062): `pulse2x-rtl` died red on PR #1059 with no test run at all -- its
dependency step, the one-shot chain `sudo apt-get update && sudo apt-get
install -y ...`, hit an Azure mirror timeout and apt exited 100 (run
44522458312, attempt 1; the successful rerun was attempt 2 on the same head).
Nine call sites across four workflow files carried the same unprotected
sequence, so any one of them could turn a transient mirror outage into a red
PR whose verdict names no test.

A bigger in-apt retry count is not the recovery: the failure log shows apt
already retried the same mirror IP four times within one invocation. What the
rerun evidence suggests is a FRESH invocation -- update the indices again from
whatever mirror the resolver picks next, then install again. That is what one
attempt is here.

Contract:
  * one attempt = `apt-get update`, then -- only if update succeeded --
    `apt-get install -y <packages>`. An install never follows a failed update.
  * a failed phase is retried once by a fresh attempt (default `--attempts 2`).
  * `--deadline` (default 600 s) bounds the whole step, and each invocation is
    clamped to the time the deadline leaves, so a wedged mirror ends as a
    visible dependency-step error inside the tightest job timeout that hosts
    this step (15 min) instead of riding the job timeout.
  * apt's own diagnostics stream through untouched; this wrapper only adds one
    stderr line per phase (`attempt=N phase=... status=...`) and a final line
    when it gives up.
  * the step's exit status is apt's final status; a timeout inside the wrapper
    ends the step with 124 (the conventional timeout status).

Tests: `tools/test_ci_apt_install.py` runs everything against a scripted apt
stand-in -- no mirror contact, no real delays, no sudo.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time

DEFAULT_ATTEMPTS = 2
DEFAULT_DEADLINE_S = 600.0
DEFAULT_DELAY_S = 15.0
DEFAULT_PHASE_TIMEOUT_S = 420.0

TIMEOUT_STATUS = 124  # none: 124 is timeout(1)'s status for the same meaning


def _log(message: str) -> None:
    print(f"ci-apt-install: {message}", file=sys.stderr, flush=True)


def phase_command(sudo: bool, apt: str, phase: str,
                  packages: list[str]) -> list[str]:
    cmd = ["sudo"] if sudo else []
    cmd += [apt]
    if phase == "update":
        return cmd + ["update"]
    return cmd + ["install", "-y"] + list(packages)


def _run_phase(cmd: list[str], timeout: float) -> int | None:
    """One apt invocation. None means the wrapper's clock ended it -- a status
    from apt itself is never confused with a timeout."""
    try:
        return subprocess.run(cmd, timeout=timeout).returncode
    except subprocess.TimeoutExpired:
        return None
    except FileNotFoundError:
        _log(f"apt program not found: {cmd[-1] if cmd else cmd}")
        return 127


def install(packages: list[str], *, sudo: bool = True, apt: str = "apt-get",
            attempts: int = DEFAULT_ATTEMPTS, deadline: float = DEFAULT_DEADLINE_S,
            delay: float = DEFAULT_DELAY_S,
            phase_timeout: float = DEFAULT_PHASE_TIMEOUT_S,
            clock: "time.Callable[[], float]" = time.monotonic,
            sleep: "time.Callable[[float], None]" = time.sleep) -> int:
    """Run update+install with one fresh retry. Returns the step's exit status."""
    if attempts < 1:
        raise ValueError("attempts must be >= 1")
    start = clock()
    last_failure: int | None = None

    def remaining() -> float:
        return deadline - (clock() - start)

    for attempt in range(1, attempts + 1):
        for phase in ("update", "install"):
            left = remaining()
            if left <= 0:
                _log(f"attempt={attempt} phase={phase} deadline exceeded "
                     f"({deadline:.0f}s)"
                     + (f"; last apt status={last_failure}" if last_failure
                        else ""))
                return last_failure if last_failure is not None else TIMEOUT_STATUS
            status = _run_phase(phase_command(sudo, apt, phase, packages),
                                min(phase_timeout, left))
            if status is None:
                _log(f"attempt={attempt} phase={phase} timeout after "
                     f"{min(phase_timeout, left):.0f}s")
                return TIMEOUT_STATUS
            _log(f"attempt={attempt} phase={phase} status={status}")
            if status != 0:
                last_failure = status
                break  # the retry is a fresh attempt: update again first
        else:
            return 0  # update and install both clean, one attempt
        if attempt < attempts and delay > 0:
            sleep(min(delay, max(remaining(), 0.0)))

    _log(f"giving up after {attempts} attempts; last apt status={last_failure}")
    return last_failure if last_failure is not None else TIMEOUT_STATUS


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="retry-wrapped apt-get update+install for CI dependency steps")
    parser.add_argument("packages", nargs="+", metavar="PKG",
                        help="packages for apt-get install -y, in the order the "
                             "step carried them")
    parser.add_argument("--apt", default="apt-get",
                        help="apt-get program to invoke (tests point this at a "
                             "scripted stand-in)")
    parser.add_argument("--no-sudo", action="store_true",
                        help="invoke apt-get without sudo (tests; production "
                             "defaults to the sudo prefix the old chain used)")
    parser.add_argument("--attempts", type=int, default=DEFAULT_ATTEMPTS)
    parser.add_argument("--deadline", type=float, default=DEFAULT_DEADLINE_S,
                        help="bound on the whole step, seconds")
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY_S,
                        help="pause between attempts, seconds")
    parser.add_argument("--phase-timeout", type=float,
                        default=DEFAULT_PHASE_TIMEOUT_S,
                        help="bound on one update/install invocation, seconds "
                             "(clamped to the remaining deadline)")
    args = parser.parse_args(argv)
    if args.attempts < 1:
        parser.error("--attempts must be >= 1")
    return install(args.packages, sudo=not args.no_sudo, apt=args.apt,
                   attempts=args.attempts, deadline=args.deadline,
                   delay=args.delay, phase_timeout=args.phase_timeout)


if __name__ == "__main__":
    sys.exit(main())

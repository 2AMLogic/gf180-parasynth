#!/usr/bin/env python3
"""Run a command, record what the machine did to it, and pass its status through
UNCHANGED.

WHY THIS EXISTS. Issue #422: the `python` job exited 139 -- SIGSEGV -- inside
`pytest model/ spec/ -q`, and the first two questions anybody asked could not be
answered from the log, because the job recorded neither:

    Is it memory or stack?  What was the peak RSS?  What was `ulimit -s`?

An OOM kill is normally 137 and a segfault 139, so those numbers are what
separates "the runner ran out of memory" from "a native extension faulted". A
job that does not record them cannot distinguish the two after the fact, and a
crash you cannot classify gets re-run away.

THE ONE THING THIS MUST NOT DO is turn a crash green. A wrapper that eats a
child's exit status is strictly worse than no wrapper: the gate keeps reporting
success while the suite dies at 30 %. So:

  - the child's stdout and stderr are INHERITED, never captured -- the CI log is
    unchanged and nothing is buffered away if the child dies mid-write;
  - a child killed by signal N exits 128+N here, the shell's own convention, so
    SIGSEGV still surfaces as the 139 that started this;
  - the wrapper's own failure to launch is 127, a code no pytest run produces,
    so "the wrapper broke" can never be read as "the suite passed";
  - `tools/test_run_with_rss.py` asserts each of those against a child that
    really does segfault. A status-propagating wrapper with no injected-crash
    control is the same unexercised instrument this repo keeps finding.

USAGE

    tools/run_with_rss.py -- python -m pytest model/ spec/ -q

Everything after `--` is the command. The report goes to stderr, so a caller
parsing the child's stdout is unaffected.
"""
from __future__ import annotations
import os, pathlib, resource, signal, subprocess, sys, time

LAUNCH_FAILED = 127      # no pytest exit code collides with this


def meminfo_kb() -> dict:
    try:
        want = {"MemTotal", "MemAvailable", "SwapTotal"}
        return {k: int(v.split()[0])
                for k, _, v in (l.partition(":") for l in
                                pathlib.Path("/proc/meminfo").read_text().splitlines())
                if k in want}
    except OSError:
        return {}


def preamble() -> None:
    """The resource envelope, printed BEFORE the command, so it survives a child
    that takes the whole runner down with it."""
    soft, hard = resource.getrlimit(resource.RLIMIT_STACK)
    stack = "unlimited" if soft == resource.RLIM_INFINITY else f"{soft // 1024} kB"
    mem = meminfo_kb()
    try:
        affinity = len(os.sched_getaffinity(0))
    except AttributeError:
        affinity = os.cpu_count()
    thread_vars = ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS",
                   "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS")
    pinned = {k: os.environ[k] for k in thread_vars if k in os.environ}
    print(f"[rss] ulimit -s {stack} (hard "
          f"{'unlimited' if hard == resource.RLIM_INFINITY else str(hard // 1024) + ' kB'})"
          f"  cpus {affinity}/{os.cpu_count()}"
          f"  MemTotal {mem.get('MemTotal', 0) // 1024} MB"
          f"  MemAvailable {mem.get('MemAvailable', 0) // 1024} MB"
          f"  Swap {mem.get('SwapTotal', 0) // 1024} MB", file=sys.stderr, flush=True)
    print(f"[rss] thread limits {pinned or '(none pinned -- BLAS picks its own)'}",
          file=sys.stderr, flush=True)


def main(argv: list[str]) -> int:
    if "--" in argv:
        cmd = argv[argv.index("--") + 1:]
    else:
        cmd = argv
    if not cmd:
        print(__doc__, file=sys.stderr)
        print("[rss] REFUSED: no command given", file=sys.stderr)
        return LAUNCH_FAILED

    preamble()
    t0 = time.monotonic()
    try:
        # stdout/stderr inherited on purpose: see the module docstring.
        p = subprocess.Popen(cmd)
    except OSError as exc:
        print(f"[rss] REFUSED: cannot launch {cmd[0]!r}: {exc}", file=sys.stderr)
        return LAUNCH_FAILED

    _, status, ru = os.wait4(p.pid, 0)
    secs = time.monotonic() - t0
    mem = meminfo_kb()
    print(f"[rss] peak RSS {ru.ru_maxrss // 1024} MB"
          f"  user {ru.ru_utime:.1f}s  sys {ru.ru_stime:.1f}s  wall {secs:.1f}s"
          f"  minflt {ru.ru_minflt}  majflt {ru.ru_majflt}"
          f"  MemAvailable now {mem.get('MemAvailable', 0) // 1024} MB",
          file=sys.stderr, flush=True)

    if os.WIFSIGNALED(status):
        sig = os.WTERMSIG(status)
        try:
            name = signal.Signals(sig).name
        except ValueError:                                    # pragma: no cover
            name = f"signal {sig}"
        print(f"[rss] TERMINATED BY {name} ({sig}) -- exiting {128 + sig}. "
              f"This is a CRASH of the interpreter or a native extension, not a "
              f"test failure; peak RSS above says whether memory was the cause.",
              file=sys.stderr, flush=True)
        return 128 + sig
    code = os.WEXITSTATUS(status)
    print(f"[rss] exit {code}", file=sys.stderr, flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

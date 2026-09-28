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

AND ONE PRECONDITION IT ASSERTS. `--require-thread-pins` refuses to launch at
all unless every one of the five BLAS/OpenMP thread-count variables is set. The
mitigation pinned in `.github/workflows/rungs.yml` is a job-level `env:` block,
and a job-level `env:` block is exactly the kind of thing that stops being in
force silently -- a renamed key, a step-level `env:` that shadows it, a job
split in two. Measured (docs/ci-segfault-2026-09-28.md §2): unpinned, the
failing path takes the process from 1 OS thread to 15; pinned, it stays at 1. So
an un-pinned run is a DIFFERENT numeric environment from the one this
investigation measured, and its log looks identical to a pinned one. Asserting
the precondition at the point of use is the whole of CLAUDE.md's second root
cause; reporting a number from an environment you did not check is the failure
it describes.

USAGE

    tools/run_with_rss.py -- python -m pytest model/ spec/ -q
    tools/run_with_rss.py --require-thread-pins -- python -m pytest model/ -q

Everything after `--` is the command. The report goes to stderr, so a caller
parsing the child's stdout is unaffected.
"""
from __future__ import annotations
import os, pathlib, resource, signal, subprocess, sys, time

LAUNCH_FAILED = 127      # no pytest exit code collides with this
# REFUSED is deliberately NOT 2, this repo's usual refusal code, because this
# tool's exit status IS the wrapped command's: pytest itself exits 2 for
# "interrupted", and a refusal that cannot be told apart from a real pytest
# outcome is not a refusal. 126 is unused by pytest, as 127 is.
REFUSED = 126

THREAD_VARS = ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS",
               "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS")


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
    pinned = {k: os.environ[k] for k in THREAD_VARS if k in os.environ}
    print(f"[rss] ulimit -s {stack} (hard "
          f"{'unlimited' if hard == resource.RLIM_INFINITY else str(hard // 1024) + ' kB'})"
          f"  cpus {affinity}/{os.cpu_count()}"
          f"  MemTotal {mem.get('MemTotal', 0) // 1024} MB"
          f"  MemAvailable {mem.get('MemAvailable', 0) // 1024} MB"
          f"  Swap {mem.get('SwapTotal', 0) // 1024} MB", file=sys.stderr, flush=True)
    print(f"[rss] thread limits {pinned or '(none pinned -- BLAS picks its own)'}",
          file=sys.stderr, flush=True)


def missing_thread_pins() -> list[str]:
    """Which of the five thread-count variables are absent or empty. Empty
    counts as absent: `OMP_NUM_THREADS=` sets nothing and reads as pinned."""
    return [k for k in THREAD_VARS if not os.environ.get(k, "").strip()]


def main(argv: list[str]) -> int:
    require_pins = False
    if "--" in argv:
        flags, cmd = argv[:argv.index("--")], argv[argv.index("--") + 1:]
        for f in flags:
            if f == "--require-thread-pins":
                require_pins = True
            else:
                # A typo'd flag must not be silently ignored: the whole point of
                # the flag is that an un-asserted precondition is invisible.
                print(f"[rss] REFUSED: unknown option {f!r}", file=sys.stderr)
                return REFUSED
    else:
        cmd = argv
    if not cmd:
        print(__doc__, file=sys.stderr)
        print("[rss] REFUSED: no command given", file=sys.stderr)
        return LAUNCH_FAILED

    if require_pins:
        missing = missing_thread_pins()
        if missing:
            print(f"[rss] REFUSED: --require-thread-pins, but these are unset: "
                  f"{', '.join(missing)}. The mitigation for issue #422 is a "
                  f"job-level env: block in .github/workflows/rungs.yml; if it "
                  f"is not in force this run is a different numeric environment "
                  f"(15 OS threads, not 1 -- docs/ci-segfault-2026-09-28.md §2) "
                  f"from the one that was measured. Nothing was run; exiting "
                  f"{REFUSED}.", file=sys.stderr, flush=True)
            return REFUSED

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

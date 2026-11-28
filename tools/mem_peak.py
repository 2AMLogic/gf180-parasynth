#!/usr/bin/env python3
"""Run a command, record the peak resident memory of its WHOLE process tree, and
pass its status through UNCHANGED.

WHY THIS EXISTS. Issue #1947 runs `pytest tools/` on several pytest-xdist
workers and asks, among other things, how much memory that holds at once.
`tools/run_with_rss.py` reports `ru_maxrss`: the largest resident set of any
ONE process in the tree. That is the right number for a single process and it
under-reads N concurrent ones by up to N-1 of them, which is the whole
difference between "fits on the runner" and "does not".

So this reads a SUM: every `--interval` seconds, `VmRSS` of the child and of
every process descended from it, added up, and the peak of that sum is what it
reports. It borrows `run_with_rss.py`'s one rule, because it is sitting in
front of the same step: it must never turn a crash green.

  - the child's stdout and stderr are INHERITED, never captured;
  - a child killed by signal N exits 128+N here, the shell's own convention;
  - the wrapper's own failure to launch, or an option it does not know, is 127,
    a code no pytest run produces, so "the wrapper broke" can never be read as
    "the suite passed";
  - `tools/test_mem_peak.py` asserts each of those against children that
    really do segfault, and asserts the sum against a two-process tree.

WHAT IT IS BLIND TO, stated because nothing here can close it:

  - a peak that lives and dies between two samples is not seen. The record
    carries its sample count next to the number so a reader can tell a 0.5 s
    resolution from a 5 s one;
  - a process that re-parents itself out of the tree (a double fork, a
    daemonised helper) is counted until it leaves and not after;
  - pages shared between processes (the interpreter, BLAS) are resident in each
    process that maps them, so the sum over-reads them by one copy per process.

USAGE

    tools/mem_peak.py --interval 0.5 --out mem.json -- python -m pytest tools/ -q -n 2

Everything after `--` is the command. The report goes to stderr, so a caller
parsing the child's stdout is unaffected. `--out` additionally writes the same
record as JSON.
"""
from __future__ import annotations
import json
import pathlib
import signal
import subprocess
import sys
import time

LAUNCH_FAILED = 127      # no pytest exit code collides with this
PROC = pathlib.Path("/proc")


def rss_kb(pid: int) -> int:
    """Resident set of one process in kB; 0 for one that has already gone."""
    try:
        for line in (PROC / str(pid) / "status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        pass
    return 0


def descendants(root: int) -> list[int]:
    """`root` and every process whose parent chain leads to it."""
    children: dict[int, list[int]] = {}
    for entry in PROC.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            # comm may contain spaces and brackets: everything after the LAST ") "
            with open(entry / "stat") as fh:
                fields = fh.read().rsplit(") ", 1)[1].split()
            children.setdefault(int(fields[1]), []).append(int(entry.name))
        except (OSError, ValueError, IndexError):
            continue                      # it exited between list and read
    out, stack = [], [root]
    while stack:
        pid = stack.pop()
        out.append(pid)
        stack.extend(children.get(pid, ()))
    return out


def tree_rss_mb(root: int) -> tuple[int, int]:
    """(sum of VmRSS over the tree, number of processes seen), in MB."""
    pids = descendants(root)
    return sum(rss_kb(p) for p in pids) // 1024, len(pids)


def mem_total_mb() -> int:
    try:
        for line in (PROC / "meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        pass
    return 0


def parse(argv: list[str]) -> tuple[float, str | None, list[str]] | str:
    """(interval, out, command), or a refusal sentence."""
    if "--" in argv:
        i = argv.index("--")
        opts, cmd = argv[:i], argv[i + 1:]
    else:
        opts, cmd = [], argv
    interval, out = 1.0, None
    it = iter(opts)
    for a in it:
        try:
            if a == "--interval":
                interval = float(next(it))
                if not interval > 0:
                    return f"--interval must be positive, got {interval!r}"
            elif a == "--out":
                out = next(it)
            else:
                return (f"unknown option {a!r} (known: --interval S, --out FILE). "
                        "A typo'd flag must not be silently ignored")
        except StopIteration:
            return f"{a!r} needs a value"
        except ValueError as exc:
            return f"{a!r}: {exc}"
    if not cmd:
        return "no command given"
    return interval, out, cmd


def main(argv: list[str]) -> int:
    parsed = parse(argv)
    if isinstance(parsed, str):
        print(__doc__, file=sys.stderr)
        print(f"[mem] REFUSED: {parsed}. Nothing was run; exiting {LAUNCH_FAILED}.",
              file=sys.stderr)
        return LAUNCH_FAILED
    interval, out, cmd = parsed
    print(f"[mem] sampling every {interval}s  MemTotal {mem_total_mb()} MB",
          file=sys.stderr, flush=True)
    t0 = time.monotonic()
    try:
        p = subprocess.Popen(cmd)             # stdout/stderr inherited: see above
    except OSError as exc:
        print(f"[mem] REFUSED: cannot launch {cmd[0]!r}: {exc}", file=sys.stderr)
        return LAUNCH_FAILED

    samples, peak, peak_at, procs = 0, 0, 0.0, 0
    while True:
        used, n = tree_rss_mb(p.pid)
        samples += 1
        procs = max(procs, n)
        if used > peak:
            peak, peak_at = used, time.monotonic() - t0
        try:
            p.wait(timeout=interval)
            break
        except subprocess.TimeoutExpired:
            pass
    wall = time.monotonic() - t0
    print(f"[mem] peak tree RSS {peak} MB at {peak_at:.1f}s  samples {samples}  "
          f"max processes {procs}  wall {wall:.1f}s", file=sys.stderr, flush=True)

    if p.returncode is not None and p.returncode < 0:
        sig = -p.returncode
        try:
            name = signal.Signals(sig).name
        except ValueError:                                    # pragma: no cover
            name = f"signal {sig}"
        code = 128 + sig
        print(f"[mem] TERMINATED BY {name} ({sig}) -- exiting {code}. This is a "
              "CRASH of the interpreter or a native extension, not a test "
              "failure; the peak above says whether memory was the cause.",
              file=sys.stderr, flush=True)
    else:
        code = p.returncode if p.returncode is not None else LAUNCH_FAILED
        print(f"[mem] exit {code}", file=sys.stderr, flush=True)
    if out:
        pathlib.Path(out).write_text(json.dumps({
            "peak_tree_rss_mb": peak, "peak_at_s": round(peak_at, 1),
            "samples": samples, "interval_s": interval,
            "max_processes": procs, "wall_s": round(wall, 1), "exit": code,
        }, indent=1) + "\n")
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

#!/usr/bin/env python3
"""Run `tools/test_mem_peak.py` beside a CPU hog and report what its controls
OBSERVED. Issue #2139.

WHY THIS EXISTS. Two of that file's bounds were wall-clock counts -- a sample
count for a 0.4 s child, and an overlap held by two `sleep(0.6)` calls -- and
"are they load-sensitive?" was argued rather than measured, on a shared
dispatch worker whose load average is never zero. This runs the file the way
the acceptance criterion names (`-n 2`, the host cap) with and without a hog,
and prints what the two controls saw: sample counts, the ratio of samples to
the sampling periods the record implies, wall and peak.

It reads the tests' own `[obs]` lines, so it cannot report a number the shipped
test did not observe. A run that passed without printing one, a hog that died
mid-run, pytest exiting for a reason other than its tests (3, 4, 5) or with an
error, or a run that timed out is REFUSED (exit 2) rather than reported: a
table that looks like data is this repository's most expensive failure mode.

    .venv/bin/python tools/mem_peak_load_probe.py --hogs 0 1 --runs 10
    .venv/bin/python tools/mem_peak_load_probe.py --hogs 1 --runs 10 --pin 3

`--pin CPU` puts the hog and the whole pytest run on one core with `taskset`,
a load a reader can reproduce on any host whatever else is running there.
Hogs are capped at 2 and expire on their own, so none outlives this probe by
more than HOG_LIFETIME_S even if the probe is killed outright.

Exit 0: every run passed. Exit 1: some test failed (the table is still
printed). Exit 2: REFUSED, and nothing above it is a measurement.
"""
from __future__ import annotations
import argparse
import json
import os
import re
import shutil
import signal
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))
import refusal  # noqa: E402

TEST = "tools/test_mem_peak.py"
#: pytest's `tmp_path` lives here rather than under /tmp: on a shared worker a
#: full root volume turns into "exit 1, no test failed", which is not a verdict.
BASTEMP = ROOT / "build" / "mem_peak_load_probe" / "pytest-tmp"
MAX_HOGS = 2
#: The hog spins for at most this long even if this probe is killed under it.
HOG_LIFETIME_S = 900
#: A pytest section header: `____ test_name ____`, in PASSES and in FAILURES.
HEADER = re.compile(r"^_+ (test_\w+) _+$")
PASSED_FAILED = re.compile(r"(\d+) passed|(\d+) failed|(\d+) errors?")
FAILED_TEST = re.compile(r"^FAILED (\S+)", re.M)
TESTS_VERDICT = (0, 1)                 # pytest exit codes that are about the tests
REFUSED = 2


class Refused(refusal.Refused, Exception):
    """The apparatus is not in a state this probe will report from."""


def hog_argv(lifetime_s: float = HOG_LIFETIME_S) -> list[str]:
    return [sys.executable, "-c", "import time\nt = time.monotonic()\n"
            f"while time.monotonic() - t < {lifetime_s}: pass\n"]


def start_hogs(n: int, pin: int | None) -> list[subprocess.Popen]:
    return [subprocess.Popen(pinned(pin, hog_argv()), start_new_session=True) for _ in range(n)]


def stop_hogs(live: list[subprocess.Popen]) -> None:
    for h in live:
        if h.poll() is None:
            h.kill()
    for h in live:
        h.wait()


def pytest_argv() -> list[str]:
    return [sys.executable, "-m", "pytest", TEST, "-n", "2", "-q", "-rPf",
            "-p", "no:cacheprovider", f"--basetemp={BASTEMP}"]


def pinned(pin: int | None, argv: list[str]) -> list[str]:
    return [*(["taskset", "-c", str(pin)] if pin is not None else []), *argv]


def run(argv: list[str], timeout: float) -> tuple[int | None, str]:
    """(exit status, output) of one pytest run in its own process group, so a
    timeout or an interrupt kills the xdist workers with their controller.
    The status is None when it had to be killed."""
    p = subprocess.Popen(argv, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, start_new_session=True)
    try:
        out, _ = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(p.pid, signal.SIGKILL)
        p.communicate()
        return None, "TIMEOUT"
    except BaseException:                              # interrupt: leave no worker behind
        os.killpg(p.pid, signal.SIGKILL)
        p.communicate()
        raise
    return p.returncode, out


def parse_obs(output: str) -> dict[str, dict]:
    """Every `[obs] scenario=record interval=0.05 samples=8 wall=0.6 peak=159`
    line, keyed by the test whose captured stdout it sits in. The file's
    `test_control_*` tests call the two controls with a defeated input, so
    their lines describe a wrapper that was broken on purpose: left out."""
    seen: dict[str, dict] = {}
    current = None
    for line in output.splitlines():
        m = HEADER.match(line)
        if m:
            current = m.group(1)
        elif line.startswith("[obs] ") and current and not current.startswith("test_control_"):
            fields = dict(kv.split("=", 1) for kv in line[len("[obs] "):].split())
            seen[current] = {k: (v if k == "scenario" else float(v)) for k, v in fields.items()}
    return seen


def tally(output: str) -> tuple[int, int, int]:
    """(passed, failed, errored) as pytest's last summary line states them."""
    passed = failed = errored = 0
    for m in PASSED_FAILED.finditer(output):
        if m.group(1):
            passed = int(m.group(1))
        elif m.group(2):
            failed = int(m.group(2))
        else:
            errored = int(m.group(3))
    return passed, failed, errored


def stats(values) -> tuple[float, float, float]:
    v = sorted(values)
    return v[0], statistics.median(v), v[-1]


def summarize(records: list[dict]) -> dict[str, dict]:
    """Per test: how many runs it printed for, and min/med/max of each column.
    `ratio` is samples over the periods the record implies (wall / interval):
    1.0 is a sampler with no overhead of its own, 0.5 one whose period is
    twice its interval."""
    out = {}
    for name in sorted({t for r in records for t in r["obs"]}):
        rows = [r["obs"][name] for r in records if name in r["obs"]]
        cols = {k: stats(row[k] for row in rows) for k in ("samples", "wall", "peak")}
        cols["ratio"] = stats(row["samples"] / (row["wall"] / row["interval"]) for row in rows)
        out[name] = {"scenario": rows[0]["scenario"], "n": len(rows), **cols}
    return out


def fmt(t: tuple[float, float, float], places: int) -> str:
    return "/".join(f"{x:.{places}f}" for x in t)


def print_table(summary: dict[str, dict]) -> None:
    print(f"  {'scenario':8} {'n':>3}  {'samples min/med/max':19}  {'ratio min/med/max':17}  "
          f"{'wall min/med/max':16}  peak min/med/max")
    for s in summary.values():
        print(f"  {s['scenario']:8} {s['n']:>3}  {fmt(s['samples'], 0):19}  {fmt(s['ratio'], 2):17}  "
              f"{fmt(s['wall'], 1):16}  {fmt(s['peak'], 0)}")


def probe(hogs: int, pin: int | None, runs: int, timeout: float) -> dict:
    load0 = os.getloadavg()
    live = start_hogs(hogs, pin)
    try:
        records = []
        for i in range(runs):
            t0 = time.monotonic()
            rc, out = run(pinned(pin, pytest_argv()), timeout)
            passed, failed, errored = tally(out)
            obs = parse_obs(out)
            records.append({"run": i, "exit": rc, "passed": passed, "failed": failed,
                            "errored": errored, "failed_tests": FAILED_TEST.findall(out),
                            "obs": obs, "seconds": round(time.monotonic() - t0, 1)})
            if rc is None:
                raise Refused(f"run {i} produced no verdict within {timeout:.0f} s. "
                              "NO VERDICT, not a failure.")
            if rc not in TESTS_VERDICT:
                raise Refused(f"run {i}: pytest exited {rc}, which is not a verdict on the "
                              f"tests. Last lines:\n{out[-600:]}")
            if errored or (rc == 1 and not failed):
                raise Refused(f"run {i}: pytest exited {rc} with {errored} errors and "
                              f"{failed} failures. An errored run is not a verdict. "
                              f"Last lines:\n{out[-600:]}")
            if rc == 0 and not obs:
                raise Refused(f"run {i} passed without printing an [obs] line; "
                              "this probe reads those lines.")
            dead = [h.pid for h in live if h.poll() is not None]
            if dead:
                raise Refused(f"hog(s) {dead} died during run {i}: that run was not under load.")
        return {"hogs": hogs, "pin": pin, "load_start": load0, "load_end": os.getloadavg(),
                "runs": records}
    finally:
        stop_hogs(live)


def main(argv: list[str]) -> int:
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(REFUSED))
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--hogs", type=int, nargs="+", default=[1],
                    help="busy-loop processes beside the tests; one table per value")
    ap.add_argument("--runs", type=int, default=10, help="pytest runs per hog count")
    ap.add_argument("--pin", type=int, default=None,
                    help="pin the hogs and the pytest run to this CPU with taskset")
    ap.add_argument("--timeout", type=float, default=600.0, help="seconds per run")
    ap.add_argument("--json", type=Path, default=None, help="write every run's record here")
    a = ap.parse_args(argv)
    if any(h < 0 or h > MAX_HOGS for h in a.hogs) or a.runs < 1:
        ap.error(f"--hogs values must be 0..{MAX_HOGS} (shared host) and --runs at least 1")
    if a.pin is not None and shutil.which("taskset") is None:
        print("REFUSED: --pin needs taskset, which this host lacks.", file=sys.stderr)
        return REFUSED
    BASTEMP.parent.mkdir(parents=True, exist_ok=True)

    print(f"host: {os.cpu_count()} cpus, python {sys.version.split()[0]}")
    report, failed_runs = [], 0
    try:
        for hogs in a.hogs:
            cond = probe(hogs, a.pin, a.runs, a.timeout)
            report.append(cond)
            rs = cond["runs"]
            bad = [r for r in rs if r["exit"] != 0]
            failed_runs += len(bad)
            print(f"\nhogs={hogs} pin={cond['pin']}  runs={len(rs)}  "
                  f"{len(rs) - len(bad)} passed-runs / {len(bad)} failed-runs  "
                  f"{min(r['seconds'] for r in rs)}..{max(r['seconds'] for r in rs)} s per run  "
                  f"load {cond['load_start'][0]:.1f} -> {cond['load_end'][0]:.1f}")
            summary = summarize(rs)
            print_table(summary) if summary else print("  no [obs] line in any run")
            for r in bad:
                print(f"  run {r['run']}: exit {r['exit']}, {r['failed']} failed "
                      f"{r['failed_tests']}; observed {r['obs']}")
    except Refused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return REFUSED
    if a.json:
        a.json.parent.mkdir(parents=True, exist_ok=True)
        a.json.write_text(json.dumps({"cpus": os.cpu_count(), "conditions": report}, indent=1) + "\n")
    return 1 if failed_runs else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

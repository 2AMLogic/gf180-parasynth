#!/usr/bin/env python3
"""Measure the crash rate of a test target, and REFUSE when it cannot.

WHY THIS EXISTS. On 2026-09-27 the `python` job of `.github/workflows/rungs.yml`
exited 139 -- SIGSEGV, core dumped -- inside `python -m pytest model/ spec/ -q`,
with the innermost Python frame at `model/drums_fx.py`'s `Env.out`:

    return self.level >> (ENV_BITS - 15)

A pure-Python shift of two ints cannot fault, so the crash was in the
interpreter or in a native extension and merely *surfaced* at whatever frame was
innermost. Issue #422. The same commit passed the same job on its other trigger,
so the first thing anybody needs is a RATE, not an opinion.

WHAT A RATE COSTS TO GET WRONG. Three ways a loop like this reports a crash rate
that is not one, all of them guarded here:

  - **Counting text.** A run that prints "Segmentation fault" and exits 0 is not
    a crash and a run that segfaults silently is. Only `os.wait4`'s status is
    read; stderr is recorded for the report and never for the verdict. This is
    `tools/run_all.py`'s rule, for the same reason.
  - **Flattening "did not run" into "did not crash."** A target that does not
    exist, a missing numpy, an import error -- all exit non-zero without ever
    reaching the code under test, and averaged into a denominator they look
    exactly like evidence of stability. `ERROR` is a separate bucket, and a
    batch whose runs are all ERROR exits 2 (REFUSED) rather than reporting
    0/N crashes.
  - **Reporting a rate from a state that cannot crash.** A segfault attributed
    to the runner's resource envelope needs that envelope recorded beside the
    number: 0/200 on an 8-core box with 30 GB free says nothing about a
    4-vCPU runner. `environment` in the report carries the stack rlimit, the
    CPU affinity count, MemTotal/MemAvailable and the thread-limit env vars, so
    a later reader can tell whether the measurement applies to their box.

USAGE

    tools/crash_rate.py --runs 100
    tools/crash_rate.py --runs 50 --target model/test_drum_fit.py
    tools/crash_rate.py --runs 50 --env OPENBLAS_NUM_THREADS=1 --env OMP_NUM_THREADS=1
    tools/crash_rate.py --probe                # one run: threads, .so count, RSS

EXIT STATUS

    0   the batch ran and NO run crashed          (a rate of 0/N is a result)
    1   the batch ran and at least one run crashed
    2   REFUSED -- preconditions failed, or every run errored before the
        target executed. Distinct from 0 on purpose: a tool that answers when
        it cannot is worse than one that is absent.
"""
from __future__ import annotations
import argparse, json, os, pathlib, platform, resource, shlex, signal, subprocess, sys, time

ROOT = pathlib.Path(__file__).resolve().parent.parent

# The exact target of the observed crash: the innermost test frame in the
# traceback on run 36377452558. Narrow on purpose -- the whole file is 4x the
# wall clock for the same code path.
DEFAULT_TARGET = ("model/test_drum_fit.py"
                  "::test_separator_matches_the_exact_share_of_our_own_render")

# Signals that mean "the interpreter or a native extension died", as opposed to
# a test that failed or a target that could not be imported.
CRASH_SIGNALS = {signal.SIGSEGV, signal.SIGBUS, signal.SIGFPE, signal.SIGILL,
                 signal.SIGABRT}

PASS, TESTFAIL, CRASH, ERROR, TIMEOUT = "PASS", "TEST-FAIL", "CRASH", "ERROR", "TIMEOUT"


def meminfo() -> dict:
    """MemTotal/MemAvailable in kB, or {} where /proc is not Linux's."""
    try:
        want = {"MemTotal", "MemAvailable", "SwapTotal"}
        out = {}
        for line in pathlib.Path("/proc/meminfo").read_text().splitlines():
            k, _, v = line.partition(":")
            if k in want:
                out[k] = int(v.split()[0])
        return out
    except OSError:
        return {}


def environment(extra_env: dict) -> dict:
    """Everything a later reader needs to decide whether our number applies to
    their machine. A crash rate without this is a number without a unit."""
    soft, hard = resource.getrlimit(resource.RLIMIT_STACK)
    try:
        affinity = len(os.sched_getaffinity(0))
    except AttributeError:
        affinity = os.cpu_count()
    thread_vars = ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS",
                   "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS")
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "rlimit_stack_soft_kb": None if soft == resource.RLIM_INFINITY else soft // 1024,
        "rlimit_stack_hard_kb": None if hard == resource.RLIM_INFINITY else hard // 1024,
        "cpu_count": os.cpu_count(),
        "sched_affinity": affinity,
        "loadavg": os.getloadavg() if hasattr(os, "getloadavg") else None,
        "meminfo_kb": meminfo(),
        "thread_limit_env": {k: os.environ.get(k) for k in thread_vars},
        "extra_env": extra_env,
        "commit": subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                                 capture_output=True, text=True).stdout.strip(),
    }


def preconditions(target: str) -> list[str]:
    """Assert the apparatus at the point of use; return the reasons it cannot
    be trusted. An empty list means the measurement is meaningful."""
    bad = []
    path = target.split("::", 1)[0]
    if not (ROOT / path).exists():
        bad.append(f"target file does not exist: {path}")
    for mod in ("pytest", "numpy", "scipy"):
        r = subprocess.run([sys.executable, "-c", f"import {mod}"],
                           cwd=ROOT, capture_output=True, text=True)
        if r.returncode != 0:
            bad.append(f"cannot import {mod}: {r.stderr.strip().splitlines()[-1:]}")
    return bad


def classify(status: int) -> tuple[str, int | None, int | None]:
    """(verdict, exit code, signal) from a raw wait status. Read the STATUS,
    never the output: a run that prints 'Segmentation fault' and exits 0 did
    not crash, and one that faults silently did."""
    if os.WIFSIGNALED(status):
        sig = os.WTERMSIG(status)
        return (CRASH if sig in CRASH_SIGNALS else ERROR), None, sig
    code = os.WEXITSTATUS(status)
    if code == 0:
        return PASS, code, None
    if code == 1:
        return TESTFAIL, code, None
    # pytest: 2 interrupted, 3 internal error, 4 usage, 5 no tests collected.
    # None of those exercised the code under test, so none is evidence about it.
    return ERROR, code, None


def run_once(target: str, extra_env: dict, timeout: float) -> dict:
    """One child, its own rusage. `os.wait4` gives ru_maxrss for THIS child
    rather than a running maximum over all of them, which is what makes a
    per-run peak-RSS column possible at all."""
    env = dict(os.environ, **extra_env)
    env.setdefault("PYTHONFAULTHANDLER", "1")   # the traceback we would need next time
    cmd = [sys.executable, "-X", "faulthandler", "-m", "pytest", target,
           "-q", "-p", "no:cacheprovider", "--no-header"]
    t0 = time.monotonic()
    p = subprocess.Popen(cmd, cwd=ROOT, env=env, start_new_session=True,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    out, verdict, code, sig = "", None, None, None
    try:
        out = p.stdout.read()
        _, status, ru = os.wait4(p.pid, 0)
        verdict, code, sig = classify(status)
        rss = ru.ru_maxrss
        minflt, majflt = ru.ru_minflt, ru.ru_majflt
    except Exception as exc:                                  # pragma: no cover
        os.killpg(p.pid, signal.SIGKILL)
        verdict, code, sig, rss, minflt, majflt = ERROR, None, None, 0, 0, 0
        out += f"\n[crash_rate] harness error: {exc!r}"
    if time.monotonic() - t0 > timeout and verdict != PASS:
        verdict = TIMEOUT
    tail = "\n".join(out.strip().splitlines()[-12:])
    return {"verdict": verdict, "exit": code, "signal": sig,
            "signal_name": signal.Signals(sig).name if sig else None,
            "seconds": round(time.monotonic() - t0, 2),
            "peak_rss_kb": rss, "minor_faults": minflt, "major_faults": majflt,
            "tail": tail if verdict in (CRASH, ERROR, TESTFAIL, TIMEOUT) else ""}


def probe() -> dict:
    """One render, instrumented: how many OS threads and native extensions the
    crashing path actually brings up. The issue records 99 loaded .so modules
    at the moment of the fault; this is how that number is re-derived."""
    src = r"""
import json, os, sys
sys.path.insert(0, "model"); sys.path.insert(0, "audition")
before = len(os.listdir("/proc/self/task"))
import numpy as np, scipy, drum_fit as df, drums_fx as dx
from dsp import SR
d = dx.DrumsFx(); n = int(0.5 * SR)
dm, bd = d.play(dx.hit_writes([(10, dx.SD, 1.0)], dx.kit_808()), n)
x = dx.output_fx(np.zeros(n), 0, dm, dx.accent_reg(0.45), bd,
                 dx.accent_reg(0.45)).astype(np.float64) / 32768.0
df.noise_share(x, SR, df.VOICE_MODES["SD"])
so = [m.__file__ for m in list(sys.modules.values())
      if getattr(m, "__file__", None) and str(m.__file__).endswith(".so")]
print(json.dumps({"threads_before_import": before,
                  "threads_after_render": len(os.listdir("/proc/self/task")),
                  "native_extensions": len(so),
                  "numpy": np.__version__, "scipy": scipy.__version__}))
"""
    r = subprocess.run([sys.executable, "-c", src], cwd=ROOT,
                       capture_output=True, text=True)
    if r.returncode != 0:
        return {"refused": "probe did not complete", "stderr": r.stderr[-2000:]}
    return json.loads(r.stdout.strip().splitlines()[-1])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target", default=DEFAULT_TARGET, help="pytest node id or file")
    ap.add_argument("--runs", type=int, default=20)
    ap.add_argument("--timeout", type=float, default=600.0, help="seconds per run")
    ap.add_argument("--env", action="append", default=[], metavar="K=V",
                    help="extra environment for every run; repeatable")
    ap.add_argument("--probe", action="store_true",
                    help="one instrumented render (threads, .so count) and exit")
    ap.add_argument("--json", metavar="PATH", help="write the full report here")
    a = ap.parse_args(argv)

    extra_env = {}
    for kv in a.env:
        k, _, v = kv.partition("=")
        if not _:
            print(f"REFUSED: --env {kv!r} is not K=V", file=sys.stderr)
            return 2
        extra_env[k] = v

    env = environment(extra_env)
    if a.probe:
        report = {"environment": env, "probe": probe()}
        print(json.dumps(report, indent=2))
        if a.json:
            pathlib.Path(a.json).write_text(json.dumps(report, indent=2) + "\n")
        return 2 if "refused" in report["probe"] else 0

    bad = preconditions(a.target)
    if bad:
        print("REFUSED: preconditions failed, no crash rate can be reported:",
              file=sys.stderr)
        for b in bad:
            print(f"  - {b}", file=sys.stderr)
        return 2

    runs = []
    print(f"target   {a.target}")
    print(f"runs     {a.runs}   extra env {extra_env or '(none)'}")
    print(f"stack    {env['rlimit_stack_soft_kb']} kB soft   "
          f"affinity {env['sched_affinity']}   "
          f"MemAvailable {env['meminfo_kb'].get('MemAvailable', 0) // 1024} MB")
    for i in range(a.runs):
        r = run_once(a.target, extra_env, a.timeout)
        runs.append(r)
        mark = {PASS: ".", TESTFAIL: "F", CRASH: "!", ERROR: "E", TIMEOUT: "T"}[r["verdict"]]
        print(f"  run {i + 1:>4}/{a.runs}  {mark} {r['verdict']:<9} "
              f"{r['seconds']:>6.2f}s  rss {r['peak_rss_kb'] // 1024:>5} MB"
              + (f"  {r['signal_name']}" if r["signal"] else ""), flush=True)
        if r["verdict"] in (CRASH, ERROR, TIMEOUT) and r["tail"]:
            for line in r["tail"].splitlines():
                print(f"        | {line}")

    counts = {v: sum(1 for r in runs if r["verdict"] == v)
              for v in (PASS, TESTFAIL, CRASH, ERROR, TIMEOUT)}
    exercised = counts[PASS] + counts[TESTFAIL] + counts[CRASH]
    peak = max((r["peak_rss_kb"] for r in runs), default=0)
    summary = {
        "target": a.target, "runs": a.runs, "counts": counts,
        "runs_that_exercised_the_target": exercised,
        "crash_rate": (None if exercised == 0 else counts[CRASH] / exercised),
        "peak_rss_kb": peak,
        "median_seconds": sorted(r["seconds"] for r in runs)[len(runs) // 2],
    }
    report = {"environment": env, "summary": summary, "runs": runs}
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps(report, indent=2) + "\n")

    print("\n" + "-" * 68)
    for k, v in counts.items():
        print(f"  {k:<10} {v}")
    print(f"  peak RSS   {peak // 1024} MB across all runs")
    if exercised == 0:
        print("\nREFUSED: no run reached the code under test "
              "(every run ERRORed or timed out), so there is no crash rate to "
              "report. This is NOT 'zero crashes'.")
        return 2
    print(f"\n  crash rate {counts[CRASH]}/{exercised} "
          f"= {100 * counts[CRASH] / exercised:.1f}% of runs that ran the target")
    return 1 if counts[CRASH] else 0


if __name__ == "__main__":
    sys.exit(main())

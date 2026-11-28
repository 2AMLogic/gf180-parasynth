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
    a crash and a target that segfaults silently is. The signal comes from
    `os.wait4`. A separate progress record shows whether execution entered the
    target callable's body. Stderr is recorded for diagnostics.
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
    2   REFUSED -- preconditions failed, every run errored before the target
        executed, or the --json report could not be written (refused before
        a run where the path says so itself, #2098). Distinct from 0 on
        purpose: a tool that answers when it cannot is worse than one that
        is absent.
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


def classify(status: int, progress: list[str]) -> tuple[str, int | None, int | None]:
    """Interpret the raw wait status using the pytest child's call evidence."""
    if os.WIFSIGNALED(status):
        sig = os.WTERMSIG(status)
        return (CRASH if sig in CRASH_SIGNALS and "started" in progress else ERROR), None, sig
    code = os.WEXITSTATUS(status)
    if "completed" not in progress:
        return ERROR, code, None
    if code == 0:
        return PASS, code, None
    if code == 1:
        return TESTFAIL, code, None
    # pytest: 2 interrupted, 3 internal error, 4 usage, 5 no tests collected.
    # None of those exercised the code under test, so none is evidence about it.
    return ERROR, code, None


# ---- per-run peak RSS: why there is a launcher between us and pytest ----------
#
# `ru_maxrss` is NOT "the most memory this program used". On Linux the kernel
# folds the OUTGOING address space's high-water mark into the process's maxrss
# at execve() (fs/exec.c: exec_mmap -> setmax_mm_hiwater_rss), and a child made
# by fork, vfork or posix_spawn starts life in the parent's address space (a
# copy, or -- vfork/CLONE_VM -- the very same one). So a pytest spawned straight
# from a big parent reports max(parent RSS at spawn, its own peak): under
# `pytest tools/` the harness process is ~230 MB of collected modules, and every
# run -- a two-line test included -- came back as ~230 MB. No choice of spawn
# primitive avoids it, and "subtract a baseline" cannot undo a max().
#
# What does: the address space that is replaced at exec must be SMALL. A
# `python -S` launcher is spawned from us (its own maxrss inherits our size, and
# we never read it), forks, and the fork -- a copy of the launcher's few-MB
# address space -- execs pytest. The launcher wait4()s that grandchild and hands
# back its raw status and rusage on a dedicated pipe. We cannot read them via
# our own wait4 on the launcher: Linux returns RUSAGE_BOTH there, which folds
# the launcher's inherited maxrss straight back in.
#
# Units: ru_maxrss is kilobytes on Linux and BYTES on macOS; normalised to kB.
_LAUNCHER = r"""
import json, os, sys, tempfile
fd, argv = int(sys.argv[1]), sys.argv[2:]
progress = tempfile.TemporaryFile()
pid = os.fork()
if pid == 0:
    os.close(fd)
    try:
        os.set_inheritable(progress.fileno(), True)
        os.execv(argv[0], [*argv, str(progress.fileno())])
    finally:
        os._exit(127)
_, status, ru = os.wait4(pid, 0)
progress.seek(0)
os.write(fd, json.dumps({"status": status, "progress": progress.read().decode().splitlines(),
                         "maxrss": ru.ru_maxrss,
                         "minflt": ru.ru_minflt, "majflt": ru.ru_majflt}).encode())
os.close(fd)
"""
_MAXRSS_TO_KB = 1024 if sys.platform == "darwin" else 1


def run_once(target: str, extra_env: dict, timeout: float) -> dict:
    """One child, its own rusage, measured through a small launcher so the
    peak-RSS column is THIS run's peak and not the harness's own size (see
    `_LAUNCHER` above). The status classified is the pytest process's own raw
    wait status, forwarded unaltered."""
    env = dict(os.environ, **extra_env)
    env.setdefault("PYTHONFAULTHANDLER", "1")   # the traceback we would need next time
    child = pathlib.Path(__file__).resolve().with_name("_crash_rate_pytest.py")
    cmd = [sys.executable, "-X", "faulthandler", str(child), target]
    t0 = time.monotonic()
    rfd, wfd = os.pipe()
    try:
        p = subprocess.Popen([sys.executable, "-S", "-c", _LAUNCHER, str(wfd), *cmd],
                             cwd=ROOT, env=env, start_new_session=True,
                             pass_fds=(wfd,), stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True)
    finally:
        os.close(wfd)
    out, verdict, code, sig = "", None, None, None
    try:
        out = p.stdout.read()
        p.wait()
        with os.fdopen(rfd, "rb") as fh:
            rfd = None
            raw = fh.read()
        if not raw:
            raise RuntimeError(f"launcher exited {p.returncode} without a report")
        rep = json.loads(raw)
        verdict, code, sig = classify(rep["status"], rep["progress"])
        rss = rep["maxrss"] // _MAXRSS_TO_KB
        minflt, majflt = rep["minflt"], rep["majflt"]
    except Exception as exc:                                  # pragma: no cover
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except OSError:
            pass
        verdict, code, sig, rss, minflt, majflt = ERROR, None, None, 0, 0, 0
        out += f"\n[crash_rate] harness error: {exc!r}"
    finally:
        if rfd is not None:
            os.close(rfd)
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


def write_report(json_path, env, summary, runs) -> None:
    """The full report to `json_path`. Raises OSError; the caller decides."""
    report = {"environment": env, "summary": summary, "runs": runs}
    pathlib.Path(json_path).write_text(json.dumps(report, indent=2) + "\n")


def batch_report(target, requested_runs, runs, env, json_path):
    counts = {v: sum(1 for r in runs if r["verdict"] == v)
              for v in (PASS, TESTFAIL, CRASH, ERROR, TIMEOUT)}
    exercised = counts[PASS] + counts[TESTFAIL] + counts[CRASH]
    peak = max((r["peak_rss_kb"] for r in runs), default=0)
    summary = {
        "target": target, "runs": requested_runs, "counts": counts,
        "runs_that_exercised_the_target": exercised,
        "crash_rate": (None if exercised == 0 else counts[CRASH] / exercised),
        "peak_rss_kb": peak,
        "median_seconds": sorted(r["seconds"] for r in runs)[len(runs) // 2] if runs else None,
    }
    if json_path:
        write_report(json_path, env, summary, runs)
    return summary


def _unwritable_record_path(name) -> str | None:
    """Why the `--json` destination cannot hold the report, or None.

    A missing parent directory is created here, as `fpga/selected_preset.py`
    creates `--out`'s. Called before anything runs (#2098), and answered with
    this tool's own "REFUSED:" line and exit 2: a destination that is a
    directory, or whose parent cannot be made, used to fail at the write
    after the batch -- FileNotFoundError, exit 1 with a traceback, which is
    this tool's "at least one run crashed".

    What this cannot see -- a parent that exists but is not writable, a disk
    that fills -- is left to the guards around the writes.
    """
    if not name:
        return None
    out = pathlib.Path(name)
    try:
        is_dir = out.is_dir()
        if not is_dir:
            out.parent.mkdir(parents=True, exist_ok=True)
    except (OSError, ValueError) as exc:
        return f"--json {str(name)!r}: no record can be written there: {exc}"
    if is_dir:
        return f"--json {str(name)!r} names an existing directory; name the record file"
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target", default=DEFAULT_TARGET,
                    help="pytest node id or path; extra selections in pytest options are refused")
    ap.add_argument("--runs", type=int, default=20)
    ap.add_argument("--timeout", type=float, default=600.0, help="seconds per run")
    ap.add_argument("--env", action="append", default=[], metavar="K=V",
                    help="extra environment for every run; repeatable")
    ap.add_argument("--probe", action="store_true",
                    help="one instrumented render (threads, .so count) and exit")
    ap.add_argument("--json", metavar="PATH",
                    help="write the full report here (a missing parent directory is created)")
    a = ap.parse_args(argv)

    extra_env = {}
    for kv in a.env:
        k, _, v = kv.partition("=")
        if not _:
            print(f"REFUSED: --env {kv!r} is not K=V", file=sys.stderr)
            return 2
        extra_env[k] = v

    unwritable = _unwritable_record_path(a.json)
    if unwritable:
        print(f"REFUSED: {unwritable}", file=sys.stderr)
        return 2

    env = environment(extra_env)
    if a.probe:
        report = {"environment": env, "probe": probe()}
        print(json.dumps(report, indent=2))
        if a.json:
            try:
                pathlib.Path(a.json).write_text(json.dumps(report, indent=2) + "\n")
            except OSError as exc:
                # The probe is on stdout above; without its record the report
                # is not kept, so neither status below is true (#2098).
                print(f"REFUSED: probe printed above; --json record not written: {exc}",
                      file=sys.stderr)
                return 2
        return 2 if "refused" in report["probe"] else 0

    if a.runs < 1:
        if a.json:
            try:
                batch_report(a.target, a.runs, [], env, a.json)
            except OSError as exc:
                print(f"REFUSED: --json record not written: {exc}", file=sys.stderr)
        print(f"REFUSED: --runs must be at least 1, got {a.runs}. A batch that "
              "ran nothing has no crash rate to report. This is NOT 'zero "
              "crashes'.", file=sys.stderr)
        return 2

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

    summary = batch_report(a.target, a.runs, runs, env, None)
    counts = summary["counts"]
    exercised = summary["runs_that_exercised_the_target"]
    peak = summary["peak_rss_kb"]
    print("\n" + "-" * 68)
    for k, v in counts.items():
        print(f"  {k:<10} {v}")
    print(f"  peak RSS   {peak // 1024} MB across all runs")
    unwritten = None
    if a.json:
        try:
            write_report(a.json, env, summary, runs)
        except OSError as exc:
            # The counts are on stdout above; the record is the evidence, and
            # without it neither 0 nor 1 is true (#2098). Written before the
            # refusal below, as it always was: a batch that never reached the
            # target still records why each run did not.
            unwritten = exc
    if exercised == 0:
        print("\nREFUSED: no run reached the code under test "
              "(every run ERRORed or timed out), so there is no crash rate to "
              "report. This is NOT 'zero crashes'.")
        if unwritten is not None:
            print(f"REFUSED: --json record not written: {unwritten}", file=sys.stderr)
        return 2
    print(f"\n  crash rate {counts[CRASH]}/{exercised} "
          f"= {100 * counts[CRASH] / exercised:.1f}% of runs that ran the target")
    if unwritten is not None:
        print(f"REFUSED: crash rate printed above; --json record not written: {unwritten}",
              file=sys.stderr)
        return 2
    return 1 if counts[CRASH] else 0


if __name__ == "__main__":
    sys.exit(main())

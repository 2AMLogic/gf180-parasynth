#!/usr/bin/env python3
"""Where the fold's reads go: `_read_at`'s share of a run's wall time (#2112).

    python tools/fold_read_profile.py --out build/fold-read-profile [--repeat 3] [--control]
    python tools/fold_read_profile.py --out DIR --smoke     the instrument's own test

`model/discrimination_features.py` reads every position of a fold through
`_read_at`. Until #2112 that was a Hann-windowed sinc evaluated tap by tap for
every position -- the same shape as the per-sample kernel
`tools/r0_capture.py` tabulated in #1944, with smaller constants -- and
whether it cost anything had never been measured. This is the measurement,
kept because the question comes back whenever the fold family gains a caller.
It times `_read_at` inside each run that calls the family -- every collected
test file that calls it and every probe that does -- and reports, per run,
the seconds and the share of that run's wall time, with the positions split
into kernel reads (`taps > 0`) and index-only reads (`taps <= 0`, which
evaluate no kernel). The headline is the run that read the most kernel
positions. The record of what it found, before and after the kernel was
tabulated, is docs/fold-read-cost-2026-11-28.md.

HOW IT TIMES. Each run is a fresh interpreter (a child of this file's
`--child` mode) so no run inherits another's caches or module state. The
child imports `discrimination_features` under both names the tree imports it
by and replaces `_read_at` in each module object with a wrapper that books
inclusive seconds, calls and positions per caller; a pytest plugin re-checks
the binding after collection and around every test. Each run is timed again
with no wrapper at all (its bare twin): the bare twin is the run as CI makes
it, and an instrumented wall that differs from it shows the wrapper changing
what it came to measure. One bare twin and `--repeat` instrumented runs per
candidate, never two children at once. The bare twin is run once per
instrumented repeat, beside it, and the two medians compared.

THE DECISION RULE IS FIXED HERE, BEFORE THE MEASUREMENT. `_read_at` is
MATERIAL in a run when it is at least MATERIAL_SHARE of that run's wall time
AND at least MATERIAL_SECONDS of one run of it. Both, because each bound
alone is misleading: a third of a two-second file is not worth a kernel that
is no longer bit-identical at fractional positions, and thirty seconds is not
worth it if nothing waits on it. `FOLD_SINC_TAPS` is a registered sensitivity
parameter and `_read_at`'s exactness at whole periods is asserted, so a table
buys its seconds with a change to the evidence those records were measured
with; #1944's table bought 314 s. A share is an UPPER bound on what a table
could recover -- the gather and the normalisation stay -- so a run that is
not material under this rule cannot become material by looking closer.

WHAT MAKES IT REFUSE (exit 2, nothing reported as measured): anything that
would make the number be about some other run. The candidate table no longer
names every file that calls the family; the host is more saturated than one
runnable thread per CPU; a run exited non-zero or did not pass every test it
collected; a module object holding `_read_at` ended the run unwrapped; the
repeats did not read the same counts; the wrapper's own bookkeeping is more
than a tenth of what it booked (which is the most the share can be wrong
by, relative to itself); the headline's share moves by more than SPREAD_MAX
between repeats; the instrumented wall is off its bare twin by more than
BARE_FACTOR. The two readings a broken wrapper and an absent kernel share are
refused rather than printed as 0 %: no reads at all (`no_reads`), and reads
that never evaluated the kernel (`index_only`).

WHAT IT CANNOT SEE. A caller that reaches the fold through another function
without naming the family is profiled only because its FILE is in the table;
the table check reads call syntax, so `f = dfx.extra_features; f(x)` is
invisible to it. `tools/dphase_floor_census.py` is excluded, with its reason
in EXCLUDED: it needs the Fischer corpus and computes its rows in a process
pool, so a wrapper in the parent would time nothing and read exactly like a
free kernel. Its per-row cost is measured instead as one `extra_features`
call on a clip of the study's window (`component` in the report), which is a
measurement of the call and an ESTIMATE for the census. An instrumented run
does not get pytest's assertion rewriting in `discrimination_features`, which
CI's collection of that file does apply: no outcome depends on it.

SMOKE is not a measurement. It runs a toy table through the same launcher
and children, keeps the structural refusals, drops the timing-quality ones
(load, spread, bare twin), and says so on every page.
"""
from __future__ import annotations

import argparse
import ast
import contextlib
import functools
import importlib
import io
import json
import math
import os
import pathlib
import platform
import re
import runpy
import statistics
import subprocess
import sys
import time
import traceback
from collections.abc import Callable

import numpy as np
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "tools"), str(ROOT / "model")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
from exit_contract import FAIL, PASS, REFUSED                     # noqa: E402
import provenance                                            # noqa: E402
import refusal                                               # noqa: E402

SCHEMA = "fold-read-profile/1"

FAMILY_FILE = "model/discrimination_features.py"
FAMILY_PATH = ROOT / FAMILY_FILE
FAMILY_BASENAME = "discrimination_features.py"
FAMILY = ("_read_at", "_fold_at", "mpd_features", "jitter_features", "extra_features",
          "dphase_row_state")
MODULE_NAMES = ("discrimination_features", "model.discrimination_features")
SCAN_DIRS = ("model", "tools", "fpga", "pnr", "rtl-sketch", "spec", "audition")

MATERIAL_SHARE = 0.10
MATERIAL_SECONDS = 10.0
SPREAD_MAX = 0.05
BARE_FACTOR = 1.5
OVERHEAD_MAX = 0.10
DOMINANT = 0.5
CONTROL_REPEATS = 20
CONTROL_TARGET = 1.5
CONTROL_SEEN = 0.5
CONTROL_WALL_SEEN = 0.8
WINDOW_S = 0.24
COMPONENT_CALLS = 5
TAIL_LINES = 12
PASSED_IN = re.compile(r"(\d+) passed\b.*? in ([\d.]+)s")


class Refused(refusal.Refused, Exception):
    """A precondition of the instrument failed: nothing was measured (exit 2)."""


class Candidate:
    """One run the instrument times. `kind` is `pytest` (a collected file, with
    its pytest arguments), `script` (a probe run as `python FILE ARGV...`) or
    `call` (an in-process workload, given the reader). `file` is the file whose
    run this is and what the table check matches; `ci` says whether pytest
    collects that file, i.e. whether CI and `make verify` run it."""

    def __init__(self, cid: str, kind: str, target, file: str, ci: bool):
        self.id, self.kind, self.target, self.file, self.ci = cid, kind, target, file, ci


def _smoke_work(taps: int) -> Callable:
    def run(reader):
        D = module()
        D.extra_features(D._square(220.0, 44100), 44100, taps)
    return run


def _component(reader) -> None:
    """The shipped `extra_features` on a clip of the study's window: what one
    row of the corpus census costs, as far as this instrument can run it."""
    D = module()
    x = D._square(220.0, 44100, dur=WINDOW_S)
    for _ in range(COMPONENT_CALLS):
        D.extra_features(x, 44100)


_QUIET = ("-q", "-p", "no:cacheprovider")
CANDIDATES = (
    Candidate("self-tests", "pytest", ("model/discrimination_features.py", *_QUIET),
              "model/discrimination_features.py", True),
    Candidate("test_discrimination", "pytest", ("model/test_discrimination.py", *_QUIET),
              "model/test_discrimination.py", True),
    Candidate("test_fold_rate_sweep", "pytest", ("tools/probes/test_fold_rate_sweep.py", *_QUIET),
              "tools/probes/test_fold_rate_sweep.py", True),
    Candidate("test_dphase_floor_census", "pytest", ("tools/test_dphase_floor_census.py", *_QUIET),
              "tools/test_dphase_floor_census.py", True),
    Candidate("test_promoted_bands", "pytest", ("tools/test_promoted_bands.py", *_QUIET),
              "tools/test_promoted_bands.py", True),
    Candidate("fold_rate_sweep", "script", ("tools/probes/fold_rate_sweep.py",),
              "tools/probes/fold_rate_sweep.py", False),
    Candidate("rate_tol_sweep", "script", ("tools/probes/rate_tol_sweep.py",),
              "tools/probes/rate_tol_sweep.py", False),
    Candidate("dphase_floor_sweep", "script", ("tools/dphase_floor_sweep.py",),
              "tools/dphase_floor_sweep.py", False),
)
EXCLUDED = {
    "tools/dphase_floor_census.py": (
        "needs the Fischer corpus, GF180_TR808_REFS, and computes its rows in a process pool, "
        "so a wrapper in the parent would time none of its folds and would read exactly like a "
        "free kernel; its per-row cost is the extra_features component instead"),
    "tools/fold_read_profile.py": "this instrument: its own calls are the toy workload",
    "tools/test_fold_read_profile.py": "this instrument's controls, on workloads of about a second",
}
SMOKE = (Candidate("smoke-kernel", "call", _smoke_work(16), FAMILY_FILE, True),
         Candidate("smoke-legacy", "call", _smoke_work(-1), FAMILY_FILE, True))
COMPONENT = Candidate("component", "call", _component, FAMILY_FILE, True)


def module():
    """The family's module, by its package-qualified name."""
    return importlib.import_module("model.discrimination_features")


def _blank(cid: str, file: str, kind: str, control: int, bare: bool) -> dict:
    return {"id": cid, "file": file, "kind": kind, "ci": False, "control": control, "bare": bare,
            "rc": 0, "wall_s": 0.0, "read_at_s": 0.0, "share": 0.0, "calls": 0,
            "kernel_pos": 0, "index_pos": 0, "by_caller": {}, "rebinds": 0, "unwrapped": [],
            "modules": [], "overhead_s": 0.0, "tests": None, "pytest_s": None, "tail": []}


# ---- the reader -------------------------------------------------------------
_OVERHEAD: list[float] = []


def _timeit(fn: Callable) -> float:
    t0 = time.perf_counter()
    fn()
    return time.perf_counter() - t0


def _overhead_per_call() -> float:
    """Seconds one wrapper call spends on its own bookkeeping: the least of
    five tries of two thousand calls on a function that does nothing. A
    minimum, because a preemption during a try would otherwise be booked as
    the wrapper's cost."""
    if not _OVERHEAD:
        wrapped = Reader()._timed(lambda x, pos, taps: None)
        pos = np.zeros(0)
        _OVERHEAD.append(min(_timeit(lambda: [wrapped(None, pos, 16) for _ in range(2000)])
                             for _ in range(5)) / 2000)
    return _OVERHEAD[0]


class Reader:
    """Holds the wrapper and what it booked. `stats[key]` is
    `[calls, kernel positions, index positions, seconds]` for one caller at
    one tap width, the caller being the function that called `_fold_at`."""

    def __init__(self, control: int = 0):
        self.control = int(control)
        self.stats: dict[str, list] = {}
        self.rebinds = 0
        self._installed: list[tuple] = []

    def _timed(self, real: Callable) -> Callable:
        stats, control, perf = self.stats, self.control, time.perf_counter

        @functools.wraps(real)
        def read_at(x, pos, taps, _real=real):
            t0 = perf()
            try:
                out = _real(x, pos, taps)
                for _ in range(control - 1):
                    _real(x, pos, taps)
                return out
            finally:
                dt = perf() - t0
                frame = sys._getframe(1)
                caller = frame.f_code.co_name
                if caller == "_fold_at":
                    back = frame.f_back
                    caller = "_fold_at<-" + (back.f_code.co_name if back is not None else "?")
                row = stats.setdefault(f"{caller}@{int(taps)}", [0, 0, 0, 0.0])
                row[0] += 1
                row[1 if taps > 0 else 2] += int(np.size(pos))
                row[3] += dt
        return read_at

    _family_files: dict[str, bool] = {}

    @classmethod
    def _is_family(cls, m) -> bool:
        """Whether `m` was loaded from the family's file. Answered per module
        from `__file__`, and resolved on disk at most once per distinct path:
        this runs for every module in `sys.modules` on every sweep, and
        resolving each one every time cost about 40 ms a test on a hosted
        runner -- the instrument slowing the runs it was timing."""
        f = getattr(m, "__file__", None)
        if not isinstance(f, str) or not f.endswith(FAMILY_BASENAME):
            return False
        known = cls._family_files.get(f)
        if known is None:
            try:
                known = pathlib.Path(f).resolve() == FAMILY_PATH
            except (OSError, RuntimeError):
                known = False
            cls._family_files[f] = known
        return known

    def holds(self, m) -> bool:
        """`m`'s `_read_at` is this reader's wrapper."""
        return any(m is mod and m.__dict__.get("_read_at") is w for mod, _, w in self._installed)

    def reaches(self, m, caller: str = "_fold_at") -> bool:
        """`m`'s `caller` resolves its globals from the very dict the wrapper is
        installed in, so the call inside it reaches the wrapper."""
        fn = m.__dict__.get(caller)
        return (fn is not None and getattr(fn, "__globals__", None) is m.__dict__
                and self.holds(m))

    def __enter__(self):
        self.install()
        return self

    def __exit__(self, *exc):
        self.remove()

    def _wrap(self, m) -> None:
        real = m.__dict__.get("_read_at")
        if callable(real) and not any(m is mod for mod, _, _ in self._installed):
            w = self._timed(real)
            m._read_at = w
            self._installed.append((m, real, w))

    def install(self) -> None:
        """Import the family under both names and wrap each module object. Both,
        because `model/test_discrimination.py` imports it bare and every probe
        and tools test imports it qualified: two names, two module objects, and
        a wrapper on the wrong one books nothing -- the reading a free kernel
        would give."""
        for name in MODULE_NAMES:
            m = importlib.import_module(name)
            if not self._is_family(m):
                raise Refused(f"{name} is {getattr(m, '__file__', None)!r}, not this tree's "
                              f"{FAMILY_FILE}")
            self._wrap(m)
        for name in MODULE_NAMES:
            m = importlib.import_module(name)
            if not self.reaches(m):
                raise Refused(f"the wrapper on {name} is not what its `_fold_at` calls")

    def sweep(self) -> int:
        """Wrap any module object holding the family that is not wrapped --
        pytest re-executes a collected module into the object it finds, which
        would put the un-timed `_read_at` back. Returns how many it re-wrapped."""
        n = 0
        for m in list(sys.modules.values()):
            if m is not None and self._is_family(m) and not self.holds(m):
                self._wrap(m)
                self.rebinds += 1
                n += 1
        return n

    def unfinished(self) -> list[str]:
        """Module objects holding the family whose `_read_at` is not the
        wrapper at the end of the run."""
        return [getattr(m, "__name__", "?") for m in list(sys.modules.values())
                if m is not None and self._is_family(m) and not self.holds(m)]

    def names(self) -> list[str]:
        return [getattr(m, "__name__", "?") for m, _, _ in self._installed]

    def remove(self) -> None:
        """Put each original `_read_at` back where the wrapper is still in
        place; a binding something else put there is left as it is, for
        `unfinished` to have named."""
        for m, real, w in reversed(self._installed):
            if m.__dict__.get("_read_at") is w:
                m._read_at = real
        self._installed.clear()


class _Plugin:
    """Keeps the wrapper bound through a pytest run and tallies its outcome."""

    def __init__(self, reader: Reader):
        self.reader = reader
        self.tests = {"collected": 0, "passed": 0, "failed": 0, "errors": 0, "skipped": 0}

    def pytest_collection_finish(self, session):
        self.tests["collected"] = len(session.items)
        self.reader.sweep()

    def pytest_runtest_setup(self, item):
        self.reader.sweep()

    def pytest_runtest_teardown(self, item, nextitem):
        self.reader.sweep()

    def pytest_runtest_logreport(self, report):
        t = self.tests
        if report.when == "call":
            if hasattr(report, "wasxfail"):
                t["skipped"] += 1
            elif report.passed:
                t["passed"] += 1
            elif report.failed:
                t["failed"] += 1
        elif report.failed:
            t["errors"] += 1
        elif report.skipped:
            t["skipped"] += 1


def _exit_of(exc: BaseException) -> int:
    code = exc.code if isinstance(exc, SystemExit) else 1
    return code if isinstance(code, int) else (0 if code is None else 1)


def profile(cand: Candidate, *, control: int = 0, bare: bool = False) -> dict:
    """One run of `cand`, in this process: the wrapper installed (unless
    `bare`), the run made from the repository root with its output captured,
    the wrapper's bookkeeping folded into the record and the originals put
    back."""
    reader = None if bare else Reader(control=control)
    rec = _blank(cand.id, cand.file, cand.kind, reader.control if reader else 0, bare)
    rec["ci"] = cand.ci
    out, crashed = io.StringIO(), []
    cwd, argv = os.getcwd(), sys.argv
    try:
        if reader is not None:
            reader.install()
        os.chdir(ROOT)
        sys.argv = [cand.id if cand.kind == "call" else str(cand.target[0])]
        t0 = time.perf_counter()
        try:
            with contextlib.redirect_stdout(out):
                if cand.kind == "pytest":
                    plugin = _Plugin(reader) if reader is not None else None
                    plugins = [plugin] if plugin is not None else []
                    rec["rc"] = int(pytest.main(list(cand.target), plugins=plugins) or 0)
                    if plugin is not None:
                        rec["tests"] = dict(plugin.tests)
                elif cand.kind == "script":
                    script = ROOT / cand.target[0]
                    sys.path.insert(0, str(script.parent))
                    try:
                        runpy.run_path(str(script), run_name="__main__")
                        rec["rc"] = 0
                    except SystemExit as exc:
                        rec["rc"] = _exit_of(exc)
                    finally:
                        sys.path.remove(str(script.parent))
                elif cand.kind == "call":
                    cand.target(reader)
                    rec["rc"] = 0
                else:
                    raise Refused(f"{cand.id}: no such kind {cand.kind!r}")
        except SystemExit as exc:
            rec["rc"] = _exit_of(exc)
        except Exception:                                   # noqa: BLE001 -- recorded, then refused
            rec["rc"] = 1
            crashed = traceback.format_exc().strip().splitlines()[-TAIL_LINES:]
        rec["wall_s"] = time.perf_counter() - t0
    finally:
        sys.argv = argv
        os.chdir(cwd)
        if reader is not None:
            rec["unwrapped"] = reader.unfinished()
            rec["rebinds"] = reader.rebinds
            rec["modules"] = reader.names()
            reader.remove()
    rows = list(reader.stats.items()) if reader else []
    rec["read_at_s"] = sum(v[3] for _, v in rows)
    rec["calls"] = sum(v[0] for _, v in rows)
    rec["kernel_pos"] = sum(v[1] for _, v in rows)
    rec["index_pos"] = sum(v[2] for _, v in rows)
    rec["by_caller"] = {k: {"calls": v[0], "positions": v[1] + v[2], "s": v[3]} for k, v in rows}
    rec["overhead_s"] = rec["calls"] * _overhead_per_call()
    rec["share"] = rec["read_at_s"] / rec["wall_s"] if rec["wall_s"] > 0 else 0.0
    text = out.getvalue().splitlines()[-TAIL_LINES:] + crashed
    rec["tail"] = text[-TAIL_LINES:]
    m = next((hit for hit in (PASSED_IN.search(ln) for ln in reversed(text)) if hit), None)
    rec["pytest_s"] = float(m.group(2)) if m else None
    return rec


# ---- the children -----------------------------------------------------------
def candidate(cid: str) -> Candidate:
    for c in (*CANDIDATES, *SMOKE, COMPONENT):
        if c.id == cid:
            return c
    raise Refused(f"no candidate {cid!r}")


def child(argv: list) -> int:
    """The launcher's other end: one candidate, in a fresh interpreter, its
    record written to `--record`."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--child", required=True)
    ap.add_argument("--bare", action="store_true")
    ap.add_argument("--control", type=int, default=0)
    ap.add_argument("--record", type=pathlib.Path, required=True)
    a = ap.parse_args(argv)
    rec = profile(candidate(a.child), control=a.control, bare=a.bare)
    a.record.write_text(json.dumps(rec) + "\n")
    return 0


def launch(out: pathlib.Path, cid: str, *, bare: bool = False, control: int = 0,
           tag: str = "") -> dict:
    """Run one child and read its record. A child that left no record is a
    record with its exit status and stderr: refused later as a run that is
    not the one asked about."""
    rec_path = out / "children" / f"{cid}{tag}{'-bare' if bare else ''}.json"
    rec_path.parent.mkdir(parents=True, exist_ok=True)
    rec_path.unlink(missing_ok=True)
    argv = [sys.executable, str(pathlib.Path(__file__).resolve()), "--child", cid,
            "--record", str(rec_path)]
    if bare:
        argv.append("--bare")
    if control:
        argv += ["--control", str(control)]
    t0 = time.perf_counter()
    proc = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True)
    if rec_path.is_file():
        rec = json.loads(rec_path.read_text())
    else:
        rec = _blank(cid, "?", "?", control, bare)
    rec["process_s"] = time.perf_counter() - t0
    rec["loadavg_end"] = list(os.getloadavg()) if hasattr(os, "getloadavg") else None
    if proc.returncode != 0 or not rec_path.is_file():
        rec["rc"] = rec["rc"] if rec_path.is_file() else (proc.returncode or 1)
        rec["tail"] = (rec["tail"] or []) + proc.stderr.strip().splitlines()[-TAIL_LINES:]
    return rec


# ---- the candidate table ----------------------------------------------------
def direct_callers(root: pathlib.Path) -> dict[str, str]:
    """Every python file under the scanned directories that CALLS the fold
    family by name, by call syntax, and the ones that could not be read. Read
    from disk on every call: a tree edited under a running process must not
    answer from an earlier read of it."""
    found: dict[str, str] = {}
    for d in SCAN_DIRS:
        for f in sorted((root / d).rglob("*.py")):
            rel = f.relative_to(root).as_posix()
            try:
                tree = ast.parse(f.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError, OSError) as exc:
                found[rel] = f"could not be read ({type(exc).__name__})"
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    fn = node.func
                    name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", None)
                    if name in FAMILY:
                        found[rel] = f"calls {name}"
                        break
    return found


def table_problems(root: pathlib.Path = ROOT) -> list[str]:
    """Files that call the family and are neither profiled nor excluded."""
    named = {c.file for c in CANDIDATES} | set(EXCLUDED)
    return [rel for rel in sorted(direct_callers(root)) if rel not in named]


def exclusion_problems(root: pathlib.Path = ROOT) -> list[str]:
    """Exclusions that no longer earn their place: the file is gone, or it no
    longer calls the family by name."""
    callers = direct_callers(root)
    return [f"{rel} is excluded and is not in the tree" for rel in sorted(EXCLUDED)
            if not (root / rel).is_file()] + [
        f"{rel} is excluded and no longer calls the family by name" for rel in sorted(EXCLUDED)
        if (root / rel).is_file() and rel not in callers]


def usable_cpus() -> int:
    """The CPUs this process may actually run on: the count, narrowed by the
    scheduler affinity and by a cgroup CPU quota where one is set."""
    n = os.cpu_count() or 1
    try:
        n = min(n, len(os.sched_getaffinity(0)))
    except (AttributeError, OSError):
        pass
    try:
        quota, period = pathlib.Path("/sys/fs/cgroup/cpu.max").read_text().split()
        if quota != "max":
            n = min(n, max(1, int(quota) // int(period)))
    except (OSError, ValueError):
        pass
    return n


# ---- what the records say ---------------------------------------------------
def _median(xs: list[float]) -> float:
    return float(statistics.median(xs))


def aggregate(records: list[dict], bare: dict | None) -> dict:
    """One candidate's repeats: the median share and seconds, the least and
    greatest share, the counts (identical across repeats or refused later),
    and the decision rule's verdict."""
    shares = [r["share"] for r in records]
    first, n = records[0], len(records)
    return {"id": first["id"], "file": first["file"], "kind": first["kind"],
            "ci": first.get("ci", False), "repeats": n,
            "wall_s": _median([r["wall_s"] for r in records]),
            "read_at_s": _median([r["read_at_s"] for r in records]),
            "share": _median(shares), "share_min": min(shares), "share_max": max(shares),
            "calls": first["calls"], "kernel_pos": first["kernel_pos"],
            "index_pos": first["index_pos"],
            "bare_wall_s": bare["wall_s"] if bare else None,
            "by_caller": {k: {"calls": v["calls"], "positions": v["positions"],
                              "s": sum(r["by_caller"].get(k, {"s": 0.0})["s"] for r in records) / n}
                          for k, v in sorted(first["by_caller"].items())},
            "material": (_median(shares) >= MATERIAL_SHARE
                         and _median([r["read_at_s"] for r in records]) >= MATERIAL_SECONDS)}


def headline_of(aggs: dict[str, dict]) -> str:
    """The run that read the most kernel positions: the one being asked about."""
    if not aggs:
        raise Refused("no candidate ran, so there is no run that reads the most")
    return max(aggs, key=lambda k: (aggs[k]["kernel_pos"], aggs[k]["index_pos"]))


def verify(runs: dict[str, list[dict]], bare: dict | None, cands: list[Candidate], *,
           headline: str, smoke: bool, load: tuple | None = None, cpus: int | None = None,
           stale: list[str] = ()) -> list[tuple[str, str]]:
    """(code, detail) for every reason the runs are not the ones being asked
    about. Structural reasons apply everywhere; the timing-quality ones --
    load, spread, the bare twin -- are what smoke drops."""
    problems: list[tuple[str, str]] = []

    def add(code: str, detail: str) -> None:
        problems.append((code, detail))

    for item in stale:
        add("stale_table", f"{item}: the ranking would be over less than every caller")
    if not smoke and load is not None:
        n = cpus or usable_cpus()
        if load[0] > n:
            add("load", f"1-min load average {load[0]:.1f} on {n} CPUs: a share timed here is "
                        "a share of whatever else was running")
    for cand in cands:
        recs = runs.get(cand.id) or []
        if not recs:
            add("missing", f"{cand.id} was not run")
            continue
        for r in recs:
            if r["rc"] != 0:
                add("exit", f"{cand.id} exited {r['rc']}: "
                            + " | ".join(r.get("tail") or [])[-300:])
            if r["unwrapped"]:
                add("unwrapped", f"{cand.id}: {r['unwrapped']} ended with an un-timed `_read_at`")
            if r["calls"] and r["overhead_s"] > OVERHEAD_MAX * r["read_at_s"]:
                add("overhead", f"{cand.id}: the wrapper's own bookkeeping is "
                                f"{r['overhead_s']:.3e} s of the {r['read_at_s']:.3e} s it booked")
            t = r.get("tests")
            if t is not None:
                done = t["passed"] + t["failed"] + t["errors"] + t["skipped"]
                if t["collected"] < 1 or t["failed"] or t["errors"] or done != t["collected"]:
                    add("incomplete", f"{cand.id}: {t['passed']} passed, {t['failed']} failed, "
                                      f"{t['errors']} errors, {t['skipped']} skipped, "
                                      f"{t['collected']} collected")
        counts = {(r["calls"], r["kernel_pos"], r["index_pos"]) for r in recs}
        if len(counts) > 1:
            add("counts_differ", f"{cand.id}: its {len(recs)} repeats did not read the same "
                                 f"counts: {sorted(counts)}")
        if not smoke:
            b = (bare or {}).get(cand.id)
            if not b:
                add("missing", f"{cand.id} has no bare twin")
            elif b.get("rc", 0) != 0:
                add("exit", f"{cand.id}: its bare twin exited {b['rc']}")
            else:
                wall = _median([r["wall_s"] for r in recs])
                if not b["wall_s"] / BARE_FACTOR <= wall <= b["wall_s"] * BARE_FACTOR:
                    add("bare", f"{cand.id}: timed at {wall:.2f} s against {b['wall_s']:.2f} s "
                                f"with no wrapper: off by more than {BARE_FACTOR:g}x")
    recs = runs.get(headline) or []
    if recs:
        if recs[0]["calls"] == 0:
            add("no_reads", f"{headline}: nothing went through `_read_at`. Either the run never "
                            "folds, or the wrapper is not what its fold calls; both read 0 %.")
        elif recs[0]["kernel_pos"] == 0:
            add("index_only", f"{headline}: every read took the index-only path (taps <= 0); "
                              "the kernel was never evaluated, so this says nothing about it.")
        if not smoke and len(recs) > 1:
            shares = [r["share"] for r in recs]
            if max(shares) - min(shares) > SPREAD_MAX:
                add("spread", f"{headline}: its share moved from {min(shares):.3f} to "
                              f"{max(shares):.3f} across {len(recs)} repeats, more than "
                              f"{SPREAD_MAX:.0%} of the run")
    return problems


def control_problems(agg: dict, rec: dict) -> list[tuple[str, str]]:
    """`--control`'s own three conditions on the headline's slowed repeat."""
    problems = []
    if not rec["share"] > DOMINANT:
        problems.append(("control_dominant", f"the slowed read is {rec['share']:.1%} of its run, "
                                             f"not above {DOMINANT:.0%}"))
    if rec["read_at_s"] < CONTROL_SEEN * rec["control"] * agg["read_at_s"]:
        problems.append(("control_seen", f"the {rec['control']} repeats left `_read_at` "
                                         f"at {rec['read_at_s']:.2f} s against "
                                         f"{agg['read_at_s']:.2f} s clean: they were not "
                                         "executed where the wrapper books"))
    grew, added = rec["wall_s"] - agg["wall_s"], rec["read_at_s"] - agg["read_at_s"]
    if grew < CONTROL_WALL_SEEN * added:
        problems.append(("control_wall", f"the run grew {grew:.2f} s while `_read_at` "
                                         f"grew {added:.2f} s: the injected time is not "
                                         "where it is booked"))
    return problems


# ---- the report -------------------------------------------------------------
def environment() -> dict:
    import scipy
    info = pathlib.Path("/proc/cpuinfo")
    cpu = platform.processor()
    if info.is_file():
        cpu = next((ln.split(":", 1)[1].strip() for ln in info.read_text().splitlines()
                    if ln.startswith("model name")), cpu)
    return {"python": platform.python_version(), "numpy": np.__version__,
            "scipy": scipy.__version__, "pytest": pytest.__version__,
            "platform": platform.platform(), "cpu": cpu, "cpu_count": os.cpu_count(),
            "usable_cpus": usable_cpus(),
            "loadavg_start": list(os.getloadavg()) if hasattr(os, "getloadavg") else None,
            "thread_env": {k: v for k, v in os.environ.items() if k.endswith("_NUM_THREADS")},
            "source": provenance.worktree_state(ROOT), "started": provenance.now()}


def _pct(x: float) -> str:
    return f"{100 * x:6.2f}%"


def summarise(report: dict, top: int = 8) -> str:
    env, cands = report["environment"], report.get("candidates") or {}
    load = env["loadavg_start"]
    L = [f"fold_read_profile: {report['verdict']}",
         f"  {env['python']}, numpy {env['numpy']}, scipy {env['scipy']}, pytest {env['pytest']}, "
         f"{env['cpu']} x {env['cpu_count']}, "
         + (f"load {'/'.join(f'{x:.2f}' for x in load)} at start, " if load else "")
         + f"source {env['source']['described']}, {report['repeat']} repeat(s)"]
    if report.get("scoped"):
        total = len(SMOKE if report["smoke"] else CANDIDATES)
        L.append(f"  NOT A RANKING -- {len(cands)} of {total} candidates were run "
                 f"({', '.join(cands)}); the headline is the most-reading of THESE")
    for code, detail in report["problems"]:
        L.append(f"  REFUSED -- [{code}] {detail}")
    for code, detail in (report.get("control") or {}).get("problems", []):
        L.append(f"  CONTROL MISSED -- [{code}] {detail}")
    if cands:
        L += ["  candidate             kind    ci   wall s  read_at s    share   min .. max     "
              "calls  kernel pos  index pos  material",
              "  " + "-" * 118]
    for cid, a in cands.items():
        L.append(f"  {cid:<21} {a['kind']:<7} {'yes' if a['ci'] else 'no ':>3} {a['wall_s']:9.2f} "
                 f"{a['read_at_s']:11.3f} {_pct(a['share'])}  "
                 f"{_pct(a['share_min'])} .. {_pct(a['share_max'])} {a['calls']:>8} "
                 f"{a['kernel_pos']:>11} {a['index_pos']:>11}  "
                 f"{'MATERIAL' if a['material'] else '-'}")
        if a["bare_wall_s"] is not None:
            L.append(f"  {'':<21} bare twin, no wrapper, median of {a['repeats']}: "
                     f"{a['bare_wall_s']:.2f} s")
        for name, row in sorted(a["by_caller"].items(), key=lambda kv: -kv[1]["s"])[:top]:
            L.append(f"      {name:<44} {row['calls']:>7} calls {row['positions']:>11} positions "
                     f"{row['s']:9.3f} s")
    if report.get("headline"):
        h = cands[report["headline"]]
        L.append(f"  headline {report['headline']}: read the most kernel positions "
                 f"({h['kernel_pos']}); `_read_at` is {_pct(h['share'])} of its wall, "
                 f"{h['read_at_s']:.2f} s of {h['wall_s']:.2f} s")
    if cands:
        material = [cid for cid, a in cands.items() if a["material"]]
        L.append("  by the rule fixed before the measurement (>= "
                 f"{MATERIAL_SHARE:.0%} of the run AND >= {MATERIAL_SECONDS:g} s in one run): "
                 + (", ".join(material) + " are MATERIAL" if material else "no run is MATERIAL"))
    comp = report.get("component")
    if comp:
        L.append(f"  one extra_features call on a {WINDOW_S:g} s clip: `_read_at` "
                 f"{_pct(comp['share'])} of {comp['wall_s'] * 1e3:.1f} ms, "
                 f"{comp['kernel_pos']} kernel positions, {comp['calls']} calls")
    if report.get("excluded"):
        L.append("  excluded from the ranking: " + "; ".join(
            f"{k} ({v})" for k, v in report["excluded"].items()))
    if report.get("control"):
        c = report["control"]
        L.append(f"  control: the headline's `_read_at` made to run {c['repeat']} times over is "
                 f"{_pct(c['record']['share'])} of its run, {c['record']['read_at_s']:.2f} s of "
                 f"{c['record']['wall_s']:.2f} s")
    if report["smoke"]:
        L.append("  SMOKE -- NOT A MEASUREMENT: a toy table, timing-quality gates off; "
                 "its seconds are not evidence")
    return "\n".join(L)


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv[:1] == ["--child"]:
        return child(argv)
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=pathlib.Path, required=True)
    ap.add_argument("--repeat", type=int, default=None,
                    help="instrumented runs per candidate (default 3, 1 in smoke)")
    ap.add_argument("--only", nargs="+", metavar="ID", help="profile these candidates only")
    ap.add_argument("--control", action="store_true",
                    help="also slow the headline's `_read_at` and require it to come back dominant")
    ap.add_argument("--smoke", action="store_true",
                    help="the toy table, timing-quality gates off: measures nothing")
    ap.add_argument("--top", type=int, default=8, help="callers to list per candidate")
    a = ap.parse_args(argv)
    repeat = a.repeat or (1 if a.smoke else 3)
    table = SMOKE if a.smoke else CANDIDATES
    if a.only:
        unknown = [i for i in a.only if i not in {c.id for c in table}]
        if unknown:
            print(f"fold_read_profile: REFUSED (nothing measured) -- no candidate {unknown}")
            return REFUSED
        table = tuple(c for c in table if c.id in a.only)
    out = a.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    env = environment()
    report: dict = {"schema": SCHEMA, "environment": env, "smoke": a.smoke, "repeat": repeat,
                    "scoped": bool(a.only), "problems": []}
    stale = [] if a.smoke else (
        [f"{rel}: calls the fold family and is neither profiled nor excluded"
         for rel in table_problems()] + exclusion_problems())
    early = verify({}, None, [], headline="", smoke=a.smoke, load=env["loadavg_start"], stale=stale)
    report["problems"] = early
    if not early:
        runs, bare = {}, {}
        for c in table:
            recs, twins = [], []
            for i in range(repeat):
                if not a.smoke:
                    twins.append(launch(out, c.id, bare=True, tag=f"-r{i}"))
                recs.append(launch(out, c.id, tag=f"-r{i}"))
            runs[c.id] = recs
            if twins:
                bare[c.id] = {"wall_s": _median([t["wall_s"] for t in twins]),
                              "walls": [t["wall_s"] for t in twins],
                              "rc": max(t["rc"] for t in twins)}
        aggs = {cid: aggregate(recs, bare.get(cid)) for cid, recs in runs.items()}
        try:
            head = headline_of(aggs)
        except Refused as exc:
            print(f"fold_read_profile: REFUSED (nothing measured) -- {exc}")
            return REFUSED
        report.update(candidates=aggs, headline=head, runs=runs, bare=bare,
                      excluded={} if a.smoke else dict(EXCLUDED))
        report["problems"] = verify(runs, bare, list(table), headline=head, smoke=a.smoke,
                                    load=env["loadavg_start"])
        if not a.smoke:
            recs = [launch(out, COMPONENT.id, tag=f"-c{i}") for i in range(repeat)]
            report["component"] = {
                "wall_s": _median([r["wall_s"] for r in recs]) / COMPONENT_CALLS,
                "read_at_s": _median([r["read_at_s"] for r in recs]) / COMPONENT_CALLS,
                "share": _median([r["share"] for r in recs]),
                "calls": recs[0]["calls"] // COMPONENT_CALLS,
                "kernel_pos": recs[0]["kernel_pos"] // COMPONENT_CALLS}
        if a.control and not a.smoke and not report["problems"]:
            h = aggs[head]
            k = max(2, math.ceil(CONTROL_TARGET * h["wall_s"] / max(h["read_at_s"], 1e-9)))
            rec = launch(out, head, control=k, tag="-control")
            report["control"] = {"repeat": k, "record": {f: rec[f] for f in (
                "wall_s", "read_at_s", "share", "calls", "kernel_pos")},
                "problems": control_problems(h, rec)}
    verdict = ("SMOKE" if a.smoke else "OK") if not report["problems"] else "REFUSED"
    if verdict == "OK" and report.get("control"):
        verdict = "CONTROL" if not report["control"]["problems"] else "CONTROL MISSED"
    report["verdict"] = verdict
    (out / "report.json").write_text(json.dumps(report, indent=1, default=str) + "\n")
    text = summarise(report, a.top)
    (out / "summary.txt").write_text(text + "\n")
    print(text)
    if verdict == "REFUSED":
        return REFUSED
    return FAIL if verdict == "CONTROL MISSED" else PASS


if __name__ == "__main__":
    sys.exit(main())

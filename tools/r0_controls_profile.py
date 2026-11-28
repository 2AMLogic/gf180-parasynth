#!/usr/bin/env python3
"""Where T-PHYSICAL's time goes (#1944): the test's phases, the control's
sessions, the analyser's functions.

    python tools/r0_controls_profile.py --out build/r0-profile [--bench]
    python tools/r0_controls_profile.py --out DIR --smoke     the instrument's own test

Reproduces tools/test_r0_capture.py::test_t_physical_on_a_synthetic_bundle_is_no_verdict_with_its_control_caught
step for step: the synthetic bundle the test makes, the registered trial
through tools/trial.py, the receipt check. The trial's three children are
started through this file's --child mode (a launcher standing in for the
interpreter on the children's command lines), which times every function of
tools/r0_capture.py and tools/r0_reference.py -- inclusive seconds, self
seconds, calls -- and every synthetic session, its synthesis against its
analysis, and writes the timings beside the receipt.

It answers only about a run that IS the test's. REFUSED (exit 2) unless
T-PHYSICAL was NO VERDICT with its required child refused as synthetic, both
controls caught with exit 0, the receipt intact, every defect the control is
registered to catch in its record, and the timed sessions accounting for the
child's own recorded seconds: a number from any other run would look exactly
like data. A control that missed a defect is reported, never refused (exit 1):
that is a finding about the control, not about the instrument.

What it does not see: a function's self seconds are everything inside it that
calls no other timed function, numpy included, so `interp`'s self seconds are
the kernel's cost and nothing finer. --bench splits that one, on one reference
each, and times the interpolation kernel against its direct formula with the
accuracy of each alternative.

--smoke runs a two-defect, two-take trial through the same launcher and the
same children and marks its report SMOKE: those sessions refuse (the
diagnostic set is incomplete), nothing is verified, and its seconds are not
evidence.
"""
from __future__ import annotations

import argparse
import contextlib
import functools
import inspect
import io
import json
import math
import os
import pathlib
import platform
import shutil
import sys
import time

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import provenance                                            # noqa: E402
import r0_capture as rc                                      # noqa: E402
import r0_reference as rr                                    # noqa: E402
import trial                                                 # noqa: E402
import refusal                                               # noqa: E402

SCHEMA = "r0-controls-profile/1"
TEST = ("tools/test_r0_capture.py::"
        "test_t_physical_on_a_synthetic_bundle_is_no_verdict_with_its_control_caught")
TRIAL = "T-PHYSICAL"
SKIP = ("analyse", "stub_analyse", "_kaiser")
# analyse: run_controls hands `_run` this very object and `_run` passes
#          allow_synthetic only when `analyser is analyse`; a wrapper in its
#          place would refuse all thirteen sessions as synthetic, and time that
# _kaiser:  internal to interp, so its cost reads as interp's own
SESSION_FUNCTIONS = ("synth_session", "_run")
SMOKE_DEFECTS = ("gain", "swap")
ACCOUNTED_MIN = 0.8        # the sessions' seconds, as a share of the child's own
LAUNCHER = """#!{python}
# Written by tools/r0_controls_profile.py: the trial starts its children with
# this where their command lines name the interpreter. Everything goes to the
# real one unchanged; a tools/r0_capture.py command line goes through its
# --child mode, which times it.
import os
import sys
os.execv({python!r}, [{python!r}, {tool!r}, "--child", *sys.argv[1:]])
"""


class Refused(refusal.Refused, Exception):
    """A precondition of the instrument failed: nothing was measured (exit 2)."""


# ---- the profiler -----------------------------------------------------------
class Profiler:
    """Times every function of each module by wrapping it in place, for the
    `with` block. `stats[name] = [calls, inclusive s, self s]`; self is
    inclusive minus what the timed functions called inside took. `sessions`
    gets one [function, session, s] row per session synthesised and per
    analysis run, named by the session's directory. Functions in SKIP keep
    their identity."""

    def __init__(self, *modules):
        self.modules = modules
        self.stats, self.sessions, self.stack, self.saved = {}, [], [], []

    def __enter__(self):
        for m in self.modules:
            for name, fn in list(vars(m).items()):
                if inspect.isfunction(fn) and name not in SKIP:
                    self.saved.append((m, name, fn))
                    setattr(m, name, self._timed(name, fn))
        return self

    def __exit__(self, *exc):
        for m, name, fn in reversed(self.saved):
            setattr(m, name, fn)

    def _timed(self, name, fn):
        stack, stats, sessions = self.stack, self.stats, self.sessions

        @functools.wraps(fn)
        def timed(*a, **k):
            t0 = time.perf_counter()
            frame = [0.0]                 # what the calls inside took
            stack.append(frame)
            try:
                return fn(*a, **k)
            finally:
                dt = time.perf_counter() - t0
                stack.pop()
                row = stats.setdefault(name, [0, 0.0, 0.0])
                row[0] += 1
                row[1] += dt
                row[2] += dt - frame[0]
                if stack:
                    stack[-1][0] += dt
                if name in SESSION_FUNCTIONS:
                    which = SESSION_FUNCTIONS.index(name)
                    label = pathlib.Path(a[which]).name if len(a) > which else "?"
                    sessions.append([name, label, dt])
        return timed


def sessions_of(rows: list) -> list:
    """The session rows -> one row per session, synthesis against analysis."""
    out = {}
    for kind, label, secs in rows:
        row = out.setdefault(label, {"name": label, "synth_s": 0.0, "analyse_s": 0.0})
        row["synth_s" if kind == "synth_session" else "analyse_s"] += secs
    return list(out.values())


# ---- the children -----------------------------------------------------------
def child(argv: list) -> int:
    """The launcher's other end: `--child ARGV...` is `python ARGV...`, timed
    when ARGV is this tree's tools/r0_capture.py command line, the real
    interpreter otherwise (the trial's preflight asks its `python -c ...`
    questions here)."""
    python = os.environ.get("R0_PROFILE_PYTHON") or sys.executable
    if not (argv and pathlib.Path(argv[0]).resolve() == pathlib.Path(rc.__file__).resolve()):
        os.execv(python, [python, *argv])
    if os.environ.get("R0_PROFILE_SMOKE") == "1":
        rc.DEFECTS = {k: v for k, v in rc.DEFECTS.items() if k in SMOKE_DEFECTS}
        rc.IMAGE_TAKES = {iid: rc.CROSS_TAKES for iid in rc.IMAGE_TAKES}
    started = time.perf_counter()
    with Profiler(rc, rr) as p:
        code = rc.main(argv[1:])
    out = argv[argv.index("--out") + 1] if "--out" in argv else None
    rec = {"rc": code, "argv": argv[1:], "functions": p.stats, "sessions": p.sessions,
           "main_s": time.perf_counter() - started}
    d = os.environ.get("R0_PROFILE_DIR")
    if d:
        pathlib.Path(d, f"{pathlib.Path(out).name if out else 'no-out'}.json").write_text(
            json.dumps(rec) + "\n")
    return code


def write_launcher(out: pathlib.Path) -> pathlib.Path:
    python = sys.executable
    if any(c.isspace() for c in python):
        raise Refused(f"the interpreter path {python!r} has whitespace in it, and the trial's "
                      "command lines are shell-joined")
    p = out / "work" / "python-profiled.py"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(LAUNCHER.format(python=python, tool=str(pathlib.Path(__file__).resolve())))
    p.chmod(0o755)
    return p


def _env_spec(out: pathlib.Path) -> pathlib.Path:
    """The test's own: this interpreter's version, no packages, no toolchain."""
    spec = out / "work" / "env.json"
    spec.write_text(json.dumps({
        "version": "test-env/1", "python": "%d.%d" % sys.version_info[:2], "packages": {},
        "toolchain": {"installer": "none", "bin": "nobin", "record": "none", "tools": {}}}))
    return spec


def environment() -> dict:
    import scipy
    info = pathlib.Path("/proc/cpuinfo")
    cpu = platform.processor()
    if info.is_file():
        cpu = next((ln.split(":", 1)[1].strip() for ln in info.read_text().splitlines()
                    if ln.startswith("model name")), cpu)
    return {"python": platform.python_version(), "numpy": np.__version__,
            "scipy": scipy.__version__, "platform": platform.platform(), "cpu": cpu,
            "cpu_count": os.cpu_count(),
            "thread_env": {k: v for k, v in os.environ.items() if k.endswith("_NUM_THREADS")},
            "source": provenance.worktree_state(ROOT), "started": provenance.now()}


def phases(out: pathlib.Path, smoke: bool) -> dict:
    """The test, step for step, with the trial's children timed."""
    work = out / "work"
    shutil.rmtree(work, ignore_errors=True)        # an older receipt must not be re-read
    (out / "children").mkdir(parents=True, exist_ok=True)
    launcher = write_launcher(out)
    names = ("R0_CAPTURE_BUNDLE", "R0_PROFILE_DIR", "R0_PROFILE_SMOKE")
    keep = {k: os.environ.get(k) for k in names}
    os.environ.update(R0_PROFILE_DIR=str(out / "children"), R0_PROFILE_SMOKE="1" if smoke else "")
    try:
        t0 = time.perf_counter()
        bundle = rc.synth_session(work / "synthetic-bundle",
                                  takes=rc.CROSS_TAKES if smoke else None)
        t1 = time.perf_counter()
        os.environ["R0_CAPTURE_BUNDLE"] = str(bundle)
        run_dir, rec = trial.run_trial(TRIAL, env_spec=_env_spec(out), out_base=work / "trials",
                                       python=str(launcher))
        t2 = time.perf_counter()
        ok, problems, _ = trial.check_receipt(run_dir / "receipt.json")
        t3 = time.perf_counter()
    finally:
        for k in names:
            os.environ.pop(k, None) if keep[k] is None else os.environ.__setitem__(k, keep[k])
    children = {}
    for c in rec["children"] + rec["controls"]:
        p = out / "children" / f"{c['id']}.json"
        timed = json.loads(p.read_text()) if p.is_file() else {}
        children[c["id"]] = {
            "secs": c["execution"]["secs"], "rc": c["execution"]["rc"],
            "state": c["execution"]["state"], "verdict": c["verdict"], "caught": c.get("caught"),
            # secs is the runner's: interpreter start and imports, then main_s, then exit
            "main_s": timed.get("main_s") or 0.0,
            "functions": timed.get("functions") or {},
            "sessions": sessions_of(timed.get("sessions") or [])}
    cp = run_dir / "synthetic-defects" / "controls.json"
    return {"rec": rec, "ctl": json.loads(cp.read_text()) if cp.is_file() else {},
            "children": children, "bundle": bundle, "receipt_ok": ok, "receipt_problems": problems,
            "phases": {"setup_s": t1 - t0, "trial_s": t2 - t1, "receipt_s": t3 - t2,
                       "total_s": t3 - t0}}


# ---- what the instrument will answer about ----------------------------------
def verify(rec: dict, ctl: dict, children: dict, *, receipt_ok: bool, receipt_problems: list,
           expect_defects=None) -> tuple[list, list]:
    """(apparatus problems, control findings). An apparatus problem means the
    run is not the one the test asserts, so nothing timed in it is evidence
    about the test. A control finding is a defect the control missed: that is
    the control's to answer for, and it is reported."""
    apparatus, control = [], []
    status = (rec.get("execution") or {}).get("status")
    if status != "complete":
        apparatus.append(f"execution {status}: the trial did not complete: "
                         + "; ".join((rec.get("execution") or {}).get("preflight") or [])[:300])
    if rec.get("verdict") != trial.NO_VERDICT:
        apparatus.append(f"T-PHYSICAL {rec.get('verdict')}ed, not NO VERDICT: "
                         + "; ".join(rec.get("verdict_reasons") or [])[:200])
    entry = {c["id"]: c for c in rec.get("children", []) + rec.get("controls", [])}
    req = entry.get("r0-capture") or {}
    if req.get("verdict") != trial.NO_VERDICT:
        apparatus.append(f"r0-capture {req.get('verdict')}, not NO VERDICT: "
                         + "; ".join(req.get("reasons") or ["no entry"])[:160])
    elif "synthetic" not in " ".join(req.get("reasons") or []):
        apparatus.append("r0-capture refused for another reason; the test requires the refusal "
                         "to name the session synthetic: "
                         + "; ".join(req.get("reasons") or [])[:160])
    elif req["execution"]["rc"] != 2:
        apparatus.append(f"r0-capture exit {req['execution']['rc']}, not 2 (REFUSED)")
    defects = ctl.get("defects") or {}
    sd = entry.get("synthetic-defects") or {}
    findings = []
    clean = ctl.get("clean") or {}
    if clean and not clean.get("ok"):
        findings.append(f"clean: {clean.get('verdict')}, known answers "
                        f"{clean.get('known_answers')}")
    for name, d in defects.items():
        if not d.get("caught"):
            findings.append(f"{name}: NOT caught ({d.get('outcome')}; {d.get('verdict')})")
        elif d.get("intended") and d["intended"] not in (d.get("failed") or []):
            findings.append(f"{name}: caught, but not for {d['intended']} "
                            f"(failed: {d.get('failed')})")
    if sd.get("caught"):
        # the trial counted the control caught: its own record must agree
        if sd["execution"]["rc"] != 0:
            apparatus.append(f"synthetic-defects exit {sd['execution']['rc']}, not 0")
        if ctl.get("all_caught") is not True:
            apparatus.append("controls.json says NOT ALL CAUGHT while the trial counted it "
                             "caught")
        control += findings
    elif sd["execution"]["rc"] == 1 and ctl.get("all_caught") is False:
        control += ["the control did not catch everything (exit 1)"] + findings
    else:
        apparatus.append("synthetic-defects not caught: "
                         + "; ".join(sd.get("reasons") or ["no entry"])[:160])
    xi = entry.get("cross-image-refusal") or {}
    if not xi.get("caught"):
        apparatus.append("cross-image-refusal not caught: "
                         + "; ".join(xi.get("reasons") or ["no entry"])[:160])
    elif xi["execution"]["rc"] != 0:
        apparatus.append(f"cross-image-refusal exit {xi['execution']['rc']}, not 0")
    if not receipt_ok:
        apparatus.append("the receipt does not check: " + "; ".join(receipt_problems[:3]))
    for cid in entry:
        if not (children.get(cid) or {}).get("functions"):
            apparatus.append(f"no timings for {cid}: its child did not write them")
    sess = (children.get("synthetic-defects") or {}).get("sessions") or []
    main_s = (children.get("synthetic-defects") or {}).get("main_s") or 0.0
    if sess and main_s:
        timed = sum(s["synth_s"] + s["analyse_s"] for s in sess)
        if not ACCOUNTED_MIN * main_s <= timed <= main_s:
            apparatus.append(f"the {len(sess)} timed sessions account for {timed:.1f} of the "
                             f"{main_s:.1f} s the child spent in its main: not between "
                             f"{ACCOUNTED_MIN:.0%} and all of it")
    for name in expect_defects if expect_defects is not None else []:
        if name not in defects:
            apparatus.append(f"the control's record has no defect {name!r}")
    return apparatus, control


# ---- the kernel and its alternatives ----------------------------------------
def direct(x, pos, half=32):
    """The interpolation kernel as tools/r0_capture.py defines it: sinc x
    Kaiser, evaluated at every sample."""
    x = np.asarray(x, dtype=np.float64)
    pos = np.asarray(pos, dtype=np.float64)
    out = np.zeros(pos.shape)
    pad = np.concatenate([np.zeros(half + 1), x, np.zeros(half + 1)])
    k = np.arange(-half + 1, half + 1)
    for s in range(0, pos.size, 8192):
        p = pos[s:s + 8192]
        i0 = np.floor(p).astype(np.int64)
        frac = p - i0
        idx = i0[:, None] + k[None, :] + half + 1
        valid = (idx >= 0) & (idx < pad.size)
        t = frac[:, None] - k[None, :]
        w = np.sinc(t) * rc._kaiser(t, half)
        vals = np.where(valid, pad[np.clip(idx, 0, pad.size - 1)], 0.0)
        out[s:s + 8192] = np.sum(vals * w, axis=1)
    return out


def lean(x, pos, half=32):
    """`direct`'s arithmetic, tap for tap, with zero padding wide enough that
    no tap of a position with output is clipped or masked, and the positions
    with no output (before the delay, past the end) not evaluated."""
    x = np.asarray(x, dtype=np.float64)
    pos = np.asarray(pos, dtype=np.float64)
    out = np.zeros(pos.shape)
    pad = np.concatenate([np.zeros(2 * half + 1), x, np.zeros(2 * half + 1)])
    k = np.arange(-half + 1, half + 1)
    for s in range(0, pos.size, 8192):
        p = pos[s:s + 8192]
        i0 = np.floor(p).astype(np.int64)
        near = np.flatnonzero((i0 >= -half) & (i0 < x.size + half - 1))
        if not near.size:
            continue
        i0 = i0[near]
        t = (p[near] - i0)[:, None] - k[None, :]
        w = np.sinc(t) * rc._kaiser(t, half)
        out[s + near] = np.sum(pad[i0[:, None] + k[None, :] + 2 * half + 1] * w, axis=1)
    return out


_TABLES = {}


def kernel_table(half: int, n: int, order: int) -> tuple:
    """The kernel at frac = j / n, j = -1 .. n + 2, as the tables its
    interpolation reads. order 3: the cubic through four neighbouring samples
    (Lagrange; exact at the nodes, fourth order between them) as its value and
    three coefficients; order 1: the sample and the slope to the next."""
    key = (half, n, order)
    if key not in _TABLES:
        tt = np.arange(-1, n + 3)[:, None] / n - np.arange(-half + 1, half + 1)[None, :]
        T = np.sinc(tt) * rc._kaiser(tt, half)
        ym, y0, y1, y2 = T[:-3], T[1:-2], T[2:-1], T[3:]
        _TABLES[key] = ((y0, -ym / 3 - y0 / 2 + y1 - y2 / 6, ym / 2 - y0 + y1 / 2,
                         (-ym + 3 * y0 - 3 * y1 + y2) / 6) if order == 3 else (y0, y1 - y0))
    return _TABLES[key]


def table_weights(tab: tuple, frac: np.ndarray, n: int, order: int) -> np.ndarray:
    """(positions, taps): the kernel at each element of `frac` in [0, 1]."""
    u = frac * n
    j = u.astype(np.int64)                           # floor: u is never negative
    a = (u - j)[:, None]
    if order == 3:
        c0, c1, c2, c3 = tab
        return c0[j] + a * (c1[j] + a * (c2[j] + a * c3[j]))
    c0, slope = tab
    return c0[j] + a * slope[j]


def table(x, pos, half=32, n=1024, order=3):
    """`direct`'s skeleton with the weights read off a table instead of
    evaluated: what a table buys, measured against the formula."""
    x = np.asarray(x, dtype=np.float64)
    pos = np.asarray(pos, dtype=np.float64)
    out = np.zeros(pos.shape)
    pad = np.concatenate([np.zeros(half + 1), x, np.zeros(half + 1)])
    k = np.arange(-half + 1, half + 1)
    tab = kernel_table(half, n, order)
    for s in range(0, pos.size, 8192):
        p = pos[s:s + 8192]
        i0 = np.floor(p).astype(np.int64)
        w = table_weights(tab, p - i0, n, order)
        idx = i0[:, None] + k[None, :] + half + 1
        valid = (idx >= 0) & (idx < pad.size)
        vals = np.where(valid, pad[np.clip(idx, 0, pad.size - 1)], 0.0)
        out[s:s + 8192] = np.sum(vals * w, axis=1)
    return out


KERNELS = (("shipped", rc.interp), ("direct", direct), ("lean", lean),
           ("table-cubic-1024", lambda x, p, half=32: table(x, p, half, 1024, 3)),
           ("table-cubic-4096", lambda x, p, half=32: table(x, p, half, 4096, 3)),
           ("table-linear-16384", lambda x, p, half=32: table(x, p, half, 16384, 1)))
TABLES_CHECKED = ((3, 1024), (3, 4096), (1, 16384))


def _best(fn, repeat=3) -> float:
    best = math.inf
    for _ in range(repeat):
        t0 = time.perf_counter()
        fn()
        best = min(best, time.perf_counter() - t0)
    return best


def bench(out: pathlib.Path, bundle: pathlib.Path, functions: dict) -> dict:
    """The analyser's parts on one reference each, and the kernel's
    alternatives. `functions` is the synthetic-defects child's profile, so each
    part shows what the trial's own run says it cost, beside one call here."""
    ref = rc.load_reference(rc.REFERENCES, "demo")
    x = ref["x"]
    rho = 1 + rc.CLEAN["ppm"] * 1e-6
    n = int(math.ceil(rc.CLEAN["delays"][1] + len(x) / rho + rc.SR))
    pos = (np.arange(n) - rc.CLEAN["delays"][1]) * rho
    want = direct(x, pos)
    pts = np.linspace(100.25, 4000.75, 400)
    kern = {}
    for name, fn in KERNELS:
        fn(x, pos[:64])                                  # build any table before timing
        got = fn(x, pos)
        kern[name] = {
            "s_per_million_samples": _best(lambda: fn(x, pos)) / n * 1e6,
            "max_abs_diff_vs_direct": float(np.max(np.abs(got - want))),
            "bit_identical_to_direct": bool(np.array_equal(got, want)),
            "one_position_call_us": _best(lambda: [fn(want[:5000], np.array([q]), half=16)
                                                   for q in pts]) / pts.size * 1e6}
    f = np.linspace(0.0, 1.0, 200_001)
    tt = f[:, None] - np.arange(-31, 33)
    exact = np.sinc(tt) * rc._kaiser(tt, 32)
    for order, samples in TABLES_CHECKED:
        err = float(np.max(np.abs(table_weights(kernel_table(32, samples, order), f, samples, order)
                                  - exact)))
        kern[f"table-{'cubic' if order == 3 else 'linear'}-{samples}"]["kernel_max_abs_error"] = err
    cap_path = bundle / "takes" / "demo-1.wav"
    cap = rc.read_capture(cap_path)[1]
    a = rc.band(cap[:, 2])
    tone = rc.load_reference(rc.REFERENCES, "held-m5a-saw")["x"]
    coarse = int(rc.COARSE_S * rc.SR)
    tmpl = ref["xb"][ref["cal"][0]:ref["cal"][0] + coarse]
    lag, _, corr = rc.ncc_lag(ref["xb"][ref["cal"][0]:ref["cal"][0] + 2048], a, 1000, 2000)
    pred = ref["cal"][0] + lag
    wav = out / "work" / "bench.wav"
    parts = [  # (row, the profiled function it is a call of, the call)
        ("load_reference('demo')", "load_reference", lambda: rc.load_reference(rc.REFERENCES, "demo")),
        ("band(310k)", "band", lambda: rc.band(cap[:, 2])),
        ("envelope(310k)", "envelope", lambda: rc.envelope(ref["xb"])),
        ("read_capture(demo-1)", "read_capture", lambda: rc.read_capture(cap_path)),
        ("sha256_file(demo-1)", "sha256_file", lambda: rc.sha256_file(cap_path)),
        ("write_capture_wav(demo-1)", "write_capture_wav",
         lambda: rc.write_capture_wav(wav, cap)),
        ("ncc_lag, whole capture", "ncc_lag", lambda: rc.ncc_lag(tmpl, a, 0, a.size - coarse)),
        ("ncc_lag, one window", "ncc_lag",
         lambda: rc.ncc_lag(ref["xb"][ref["cal"][0]:ref["cal"][0] + 2048], a,
                            int(round(pred)) - 128, int(round(pred)) + 128, center=pred)),
        ("_refine_peak", "_refine_peak", lambda: rc._refine_peak(corr, int(np.argmax(corr)))),
        ("tone_f0(51k)", "tone_f0", lambda: rc.tone_f0(tone, rc.SR, 30.0, 5000.0)),
        ("rng.normal(310k x 4)", None, lambda: np.random.default_rng(1).normal(0.0, 1e-5, cap.shape))]
    comp = []
    for name, fn_name, call in parts:
        calls = functions.get(fn_name, [0])[0] if fn_name else 0
        ms = _best(call) * 1e3
        comp.append({"call": name, "ms": ms, "calls": calls,
                     # the two ncc_lag rows share one call count, so neither is extrapolated
                     "s_per_session": (ms * calls / 13 / 1e3 if fn_name and fn_name != "ncc_lag"
                                       else None),
                     "profiled_self_s_per_session": (functions.get(fn_name, [0, 0, 0])[2] / 13
                                                     if fn_name else None)})
    wav.unlink(missing_ok=True)
    return {"samples": n, "kernel": kern, "components": comp}


# ---- the report -------------------------------------------------------------
def _matrix(ctl: dict) -> str:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        if ctl:
            rc.print_matrix(ctl)
    return buf.getvalue()


def summarise(report: dict, top: int) -> str:
    env, ph = report["environment"], report["phases"]
    L = [f"r0_controls_profile: {report['verdict']}",
         f"  {env['python']}, numpy {env['numpy']}, scipy {env['scipy']}, "
         f"{env['cpu']} x {env['cpu_count']}, source {env['source']['described']}"]
    for p in report["problems"]["apparatus"]:
        L.append(f"  REFUSED -- {p}")
    for p in report["problems"]["control"]:
        L.append(f"  NOT CAUGHT -- {p}")
    L += ["  T-PHYSICAL, phase by phase",
          f"    setup    synth_session, the test's own bundle   {ph['setup_s']:9.2f} s",
          f"    trial    run_trial, wall                      {ph['trial_s']:9.2f} s"]
    for cid, c in report["children"].items():
        state = "caught" if c["caught"] else str(c["verdict"])
        L.append(f"      {cid:<20} exit {c['rc']}  {state:<12} {c['secs']:9.1f} s   "
                 f"(start and imports {c['secs'] - c['main_s']:.1f}, main {c['main_s']:.1f})")
    L += [f"    receipt  check_receipt                       {ph['receipt_s']:9.2f} s",
          f"    total                                       {ph['total_s']:9.2f} s"]
    sess = (report["children"].get("synthetic-defects") or {}).get("sessions") or []
    if sess:
        d = report["ctl"].get("defects") or {}
        L += ["  synthetic-defects, session by session",
              "    session          synth     analyse      total   verdict   outcome"]
        ts = ta = 0.0
        for s in sess:
            v = d.get(s["name"]) or report["ctl"].get("clean") or {}
            ts, ta = ts + s["synth_s"], ta + s["analyse_s"]
            L.append(f"    {s['name']:<15} {s['synth_s']:8.2f}  {s['analyse_s']:9.2f}  "
                     f"{s['synth_s'] + s['analyse_s']:9.2f}   {str(v.get('verdict', '?')):<7}  "
                     f"{v.get('outcome', v.get('ok'))}")
        L.append(f"    {len(sess)} sessions    {ts:8.2f}  {ta:9.2f}  {ts + ta:9.2f}")
    for cid, c in report["children"].items():
        f = sorted(c["functions"].items(), key=lambda kv: -kv[1][2])[:top]
        if f:
            L.append(f"  {cid}: functions by self seconds (self, inclusive, calls)")
            L += [f"    {name:<26} {own:9.2f}  {incl:9.2f}  {calls:>8}" for name, (calls, incl, own) in f]
    if report.get("matrix"):
        L += ["  " + ln for ln in report["matrix"].rstrip().splitlines()]
    if report.get("bench"):
        b = report["bench"]
        L.append(f"  kernel, demo.wav warped ({b['samples']} positions): s per million samples, "
                 "max abs diff against direct, one-position call, kernel error")
        for name, k in b["kernel"].items():
            L.append(f"    {name:<20} {k['s_per_million_samples']:7.3f}   "
                     f"{k['max_abs_diff_vs_direct']:.1e}   {k['one_position_call_us']:6.1f} us"
                     + (f"   {k['kernel_max_abs_error']:.1e}" if "kernel_max_abs_error" in k else ""))
        L.append("  components: ms per call, calls in the 13 sessions, s per session here, "
                 "profiled self s per session")
        for c in b["components"]:
            L.append(f"    {c['call']:<24} {c['ms']:9.3f}  {c['calls']:>8}  "
                     + (f"{c['s_per_session']:8.3f}" if c["s_per_session"] is not None else " " * 8)
                     + (f"  {c['profiled_self_s_per_session']:8.3f}"
                        if c["profiled_self_s_per_session"] is not None else ""))
    return "\n".join(L)


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv[:1] == ["--child"]:
        return child(argv[1:])
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=pathlib.Path, required=True)
    ap.add_argument("--bench", action="store_true",
                    help="also time the analyser's parts and the kernel's alternatives")
    ap.add_argument("--smoke", action="store_true",
                    help="two defects, two takes: exercises the instrument, measures nothing")
    ap.add_argument("--top", type=int, default=25, help="functions to list per child")
    a = ap.parse_args(argv)
    out = a.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    try:
        env, run = environment(), phases(out, a.smoke)
    except Refused as exc:
        print(f"r0_controls_profile: REFUSED (nothing measured) -- {exc}")
        return 2
    report = {"schema": SCHEMA, "test": TEST, "environment": env, "smoke": a.smoke,
              "phases": run["phases"], "children": run["children"], "ctl": run["ctl"],
              "matrix": _matrix(run["ctl"])}
    apparatus, control = verify(run["rec"], run["ctl"], run["children"],
                                receipt_ok=run["receipt_ok"], receipt_problems=run["receipt_problems"],
                                expect_defects=SMOKE_DEFECTS if a.smoke else list(rc.DEFECTS))
    report["problems"] = {"apparatus": apparatus, "control": control}
    if a.smoke:            # the smoke control is expected to miss; the apparatus is not
        report["verdict"] = "SMOKE" if not apparatus else "REFUSED"
    else:
        report["verdict"] = "REFUSED" if apparatus else "NOT CAUGHT" if control else "OK"
    (out / "report.json").write_text(json.dumps(report, indent=1, default=str) + "\n")
    if a.bench and not apparatus and not a.smoke:
        try:                  # the phases are already on disk: a bench failure loses nothing else
            report["bench"] = bench(out, run["bundle"], run["children"]["synthetic-defects"]["functions"])
        except Exception as exc:                             # noqa: BLE001 -- reported, not raised
            report["bench"] = {"error": f"{type(exc).__name__}: {exc}"}
        (out / "report.json").write_text(json.dumps(report, indent=1, default=str) + "\n")
    print(summarise(report, a.top))
    if a.smoke:
        print("r0_controls_profile: SMOKE -- NOT A MEASUREMENT: two defects, two takes, "
              "nothing verified")
    return {"OK": 0, "SMOKE": 0, "NOT CAUGHT": 1, "REFUSED": 2}[report["verdict"]]


if __name__ == "__main__":
    sys.exit(main())

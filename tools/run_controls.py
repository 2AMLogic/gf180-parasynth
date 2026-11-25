#!/usr/bin/env python3
"""`make controls` runner: a mutant is credited only after ITS clean run passed.

THE DEFECT (#1126). `--expect-fail` is "exit 0 only if the comparison gave 1".
An injected run is one process with no clean run beside it, so a bench that is
red before any injection (one wrong datum in the stimulus, a broken define)
turns every `--inject ... --expect-fail` entry into a PASS: a fixed trace with
one flipped datum and zero wrong addresses printed `CAUGHT` for `SPI_ADDR7`.

THE FIX. Same argument list as tools/run_all.py (`$(RUN)`), plus two prefixes
on a command string:

    "@baseline=ID  <clean command>"   run first; defines group ID
    "@after=ID     <mutant command>"  runs only when baseline ID exited 0

A group is one clean invocation: one stimulus, signal-path configuration and
simulator. Equivalent mutants share it. A command with no prefix is run exactly
as run_all would run it.

WHAT YOU GET, in the report (--json, same entry shape as run_all plus `role`,
`baseline`, `skipped`):
  - baseline not PASS (FAIL, NO-VERDICT, timeout, launch error): every
    dependent is recorded NO-VERDICT with `skipped: true` and the reason, never
    PASS, and the overall exit is 1. The baseline's own state names the cause.
  - baseline PASS: dependents run and are judged by their own exit status, so
    a detected mutant passes and an ineffective one (the clean bench passes
    under `--expect-fail`) still fails.
  - the report is rewritten with every job PENDING before anything runs, so a
    PASS from a previous invocation cannot survive a run that fails or is
    killed.

REFUSED (exit 2, nothing run): a malformed prefix, an `@after` naming no
baseline, a baseline nothing depends on, a duplicate baseline id, a baseline
whose own command carries an injection / expect-failure flag (a baseline that
is itself a mutant proves nothing -- the input that defeats this guard), or a
baseline and dependent that share an `--outdir` (parallel jobs would overwrite
each other's artefacts, and the clean run's evidence with them).
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import re
import shlex
import sys
import threading

import refusal
import run_all
from run_all import FAIL, NO_VERDICT, PASS, run_one, summarise, write_json_atomic

REFUSED = 2

# Flags that make a command a MUTANT or an expected-failure check. A command
# carrying one of these cannot be the clean baseline of anything.
MUTANT_FLAGS = ("--inject", "--inject-clamp", "--expect-fail", "--expect-mismatch",
                "--wrong", "--expect-first", "--expect-deadline-fail",
                "--f1cal-fault", "--rtl", "--compare-only")
_DEFINE_INJECT = re.compile(r"(?:^|\s)(?:--define\s+|-D)INJECT_BUG")
_PREFIX = re.compile(r"^@(baseline|after)=([A-Za-z0-9_.-]+)\s+(.*)$", re.S)


class Refused(refusal.Refused, Exception):
    pass


def parse(raw: list[str]) -> list[dict]:
    """One spec per command, in order: role plain|baseline|mutant, group id."""
    specs = []
    for text in raw:
        stripped = text.strip()
        if stripped.startswith("@"):
            m = _PREFIX.match(stripped)
            if not m:
                raise Refused(f"malformed prefix, want @baseline=ID or @after=ID: {text!r}")
            role = "baseline" if m.group(1) == "baseline" else "mutant"
            specs.append({"role": role, "group": m.group(2), "cmd": m.group(3).strip()})
        else:
            specs.append({"role": "plain", "group": None, "cmd": stripped})
    return specs


def _outdir(cmd: str):
    try:
        toks = shlex.split(cmd)
    except ValueError:
        return None
    for i, tok in enumerate(toks):
        if tok == "--outdir" and i + 1 < len(toks):
            return toks[i + 1]
        if tok.startswith("--outdir="):
            return tok.split("=", 1)[1]
    return None


def validate(specs: list[dict]) -> None:
    baselines: dict[str, dict] = {}
    for s in specs:
        if s["role"] == "baseline":
            if s["group"] in baselines:
                raise Refused(f"duplicate baseline id {s['group']!r}")
            baselines[s["group"]] = s
    used = set()
    for s in specs:
        if s["role"] == "baseline":
            toks = shlex.split(s["cmd"])
            bad = [f for f in MUTANT_FLAGS if f in toks or any(t.startswith(f + "=") for t in toks)]
            if bad or _DEFINE_INJECT.search(s["cmd"]):
                raise Refused(
                    f"baseline {s['group']!r} is itself a mutant or expected-failure "
                    f"check ({bad or 'INJECT_BUG define'}): {s['cmd']!r}")
        elif s["role"] == "mutant":
            base = baselines.get(s["group"])
            if base is None:
                raise Refused(f"@after={s['group']} names no @baseline: {s['cmd']!r}")
            used.add(s["group"])
            if s["cmd"] == base["cmd"]:
                raise Refused(f"mutant is identical to its baseline {s['group']!r}: {s['cmd']!r}")
            out = _outdir(s["cmd"])
            if out is not None and out == _outdir(base["cmd"]):
                raise Refused(f"mutant and baseline {s['group']!r} share --outdir {out}")
    unused = sorted(set(baselines) - used)
    if unused:
        raise Refused(f"baseline(s) nothing depends on: {unused}")


def run_specs(specs: list[dict], *, timeout=None, jobs=None, serial=False,
              on_result=None, runner=None) -> list[dict]:
    """Run with dependencies. `runner(cmd, timeout)` is injectable for tests."""
    runner = runner or run_one      # looked up at call time (tests spy on it)
    n = len(specs)
    results: list[dict | None] = [None] * n
    lock = threading.Lock()
    dependents: dict[str, list[int]] = {}
    for i, s in enumerate(specs):
        if s["role"] == "mutant":
            dependents.setdefault(s["group"], []).append(i)

    def record(i: int, r: dict) -> None:
        r = dict(r)
        r["cmd"] = specs[i]["cmd"]
        r["role"] = specs[i]["role"]
        r["baseline"] = specs[i]["group"]
        r.setdefault("skipped", False)
        with lock:
            results[i] = r
            if on_result is not None:
                on_result(i, r)

    def skip(i: int, base_i: int) -> None:
        b = results[base_i]
        why = (b["state"] if b["state"] != FAIL else f"FAIL({b['rc']})")
        record(i, {"state": NO_VERDICT, "rc": None, "secs": 0.0, "skipped": True,
                   "out": f"SKIPPED: clean baseline {specs[i]['group']!r} was {why}; "
                          f"no verdict for this control. Baseline: {b['cmd']}"})

    base_index = {s["group"]: i for i, s in enumerate(specs) if s["role"] == "baseline"}
    workers = 1 if serial else (jobs or min(n, 8))
    with cf.ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        pending: set[cf.Future] = set()
        fut_index: dict[cf.Future, int] = {}

        def submit(i: int) -> None:
            f = ex.submit(runner, specs[i]["cmd"], timeout)
            fut_index[f] = i
            pending.add(f)

        # baselines and plain commands first, so no mutant waits behind them
        order = [i for i, s in enumerate(specs) if s["role"] != "mutant"]
        for i in order:
            submit(i)
        while pending:
            done, _ = cf.wait(pending, return_when=cf.FIRST_COMPLETED)
            for f in done:
                pending.discard(f)
                i = fut_index[f]
                try:
                    r = f.result()
                except Exception as exc:        # the runner itself broke
                    r = {"cmd": specs[i]["cmd"], "state": run_all.LAUNCH_ERROR, "rc": None,
                         "out": f"{type(exc).__name__}: {exc}", "secs": 0.0}
                record(i, r)
                if specs[i]["role"] == "baseline":
                    for d in dependents.get(specs[i]["group"], []):
                        if results[i]["state"] == PASS:
                            submit(d)
                        else:
                            skip(d, base_index[specs[i]["group"]])
    if any(r is None for r in results):
        raise RuntimeError("runner finished without recording every job")
    return results  # type: ignore[return-value]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmds", nargs="+")
    ap.add_argument("--serial", action="store_true")
    ap.add_argument("--jobs", type=int, default=None)
    ap.add_argument("--timeout", type=float, default=None)
    ap.add_argument("--tail", type=int, default=4)
    ap.add_argument("--json", metavar="PATH")
    a = ap.parse_args(argv)
    try:
        specs = parse(a.cmds)
        validate(specs)
    except Refused as exc:
        print(f"REFUSED: {exc}")
        return REFUSED

    partial = [{"cmd": s["cmd"], "state": "PENDING", "rc": None, "out": "", "secs": 0.0,
                "role": s["role"], "baseline": s["group"], "skipped": False} for s in specs]
    if a.json:
        write_json_atomic(a.json, partial)

    def persist(i, r):
        partial[i] = r
        write_json_atomic(a.json, partial)

    results = run_specs(specs, timeout=a.timeout, jobs=a.jobs, serial=a.serial,
                        on_result=persist if a.json else None)
    print(summarise(results, a.tail))
    if a.json:
        write_json_atomic(a.json, results)
    return 0 if all(r["state"] == PASS for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())

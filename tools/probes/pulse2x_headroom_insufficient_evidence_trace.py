#!/usr/bin/env python3
"""#1908 red-first trace: does any input `measure_pulse2x_headroom.py phrases`
ACCEPTS make `mono_m5a_score.measure` raise `audio_measure.InsufficientEvidence`
(or its subclass `NonFiniteAudio`) past the CLI's `Refused`-only handler?

THE CANDIDATE, from the #1782 census: `phrases()` (`tools/measure_pulse2x_headroom.py`)
calls `mono_m5a_score.measure` under `except refusal.Refused` alone, and `measure`
reaches `audio_measure.harmonic_signature` unguarded at two places
(`tools/mono_m5a_score.py` `_stage_diagnostics` and the per-event comparison).
`InsufficientEvidence` subclasses `AssertionError`, not `refusal.Refused`, so an
estimator refusal there would leave as an exit-1 traceback: a refusal rendered as
FAIL, the inversion `tools/exit_contract.py` names. The census is an AST walk; it
cannot say whether any accepted input reaches the raise. This probe does.

WHAT IT RUNS. Every case is the real file in a real interpreter, so the exit
status is the one a batch runner sees. `accepted` cases are command lines a user
could type (`--gains`, `--scope`, `--pulse-drive-comp`: zero, the smallest
non-zero Q15 gain, negative, large, int64-overflowing, and the non-finite floats
argparse accepts). `injected` cases replace one attribute after the tool is
imported and before the dispatch, because no command line reaches them; they
establish what the handler WOULD do if the estimator refused, and which site is
the live one.

HOW TO READ THE RESULT. The question is answered by the `accepted` rows alone:
the candidate is REACHABLE if any of them exits 1 with an
`audio_measure.InsufficientEvidence` / `NonFiniteAudio` traceback. An accepted
row that exits 2 (`REFUSED  <reason>`) was stopped by an earlier gate, which the
reason names; one that exits 0 measured. An accepted row that exits 1 with some
OTHER exception (ValueError, OverflowError from a non-finite or int64-overflowing
CLI float) is an execution error of a different class: recorded here because it
was observed, not a answer to this question, and not a refusal.

The injected rows are the control: if `inject: InsufficientEvidence at
score.measure` did NOT exit 1, the probe could not see the defect it exists to
find, and every accepted row would be meaningless.

Usage:
    .venv/bin/python tools/probes/pulse2x_headroom_insufficient_evidence_trace.py \
        [--group accepted|injected|all] [--jobs N] [--json PATH] [--timeout S]

About 45 s per accepted row on an 8-vCPU AWS worker (the M5A baseline phrase is
scored before the candidate, ~23 s, and a candidate that measures runs M5B too).
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import pathlib
import re
import subprocess
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "measure_pulse2x_headroom.py"

#: the exception classes whose escape is the defect this probe looks for
IE_CLASSES = ("audio_measure.InsufficientEvidence", "audio_measure.NonFiniteAudio")
#: `Traceback`'s last line: `<module>.<Exception>: <message>`. The class name is
#: taken as written: a first draft matched only names ending in `Error` /
#: `Exception` and so could not read `audio_measure.InsufficientEvidence`, the
#: one class this probe exists to find (wrong, then right, on the first run).
EXC_LINE = re.compile(r"^(?P<cls>[\w.]+)(?:: (?P<msg>.*))?$")
#: a `File "<path>", line <n>` frame of the traceback (MULTILINE: one per line)
FRAME = re.compile(r'^\s*File "(?P<path>[^"]+)", line (?P<line>\d+)', re.M)

#: one (label, extra argv) per accepted command line; every row gets
#: `phrases --out <fresh path>` in front
ACCEPTED = [
    ("gain 0.0",                 ["--gains", "0.0"]),
    ("gain 1e-9 (q15 0)",        ["--gains", "1e-9"]),
    ("gain 2e-5 (q15 1)",        ["--gains", "2e-5"]),
    ("gain 0.001 (q15 33)",      ["--gains", "0.001"]),
    ("gain -0.85",               ["--gains", "-0.85"]),
    ("gain -1.0",                ["--gains", "-1.0"]),
    ("gain 2.0",                 ["--gains", "2.0"]),
    ("gain 100.0",               ["--gains", "100.0"]),
    ("gain 1e15 (q15 > int64)",  ["--gains", "1e15"]),
    ("gain nan",                 ["--gains", "nan"]),
    ("gain inf",                 ["--gains", "inf"]),
    ("gain -inf",                ["--gains", "-inf"]),
    ("gain 0.0, scope all",      ["--gains", "0.0", "--scope", "all"]),
    ("gain -1.0, scope all",     ["--gains", "-1.0", "--scope", "all"]),
    ("drive-comp 0.0",           ["--gains", "0.84", "--pulse-drive-comp", "0.0"]),
    ("drive-comp 1e-9",          ["--gains", "0.84", "--pulse-drive-comp", "1e-9"]),
    ("drive-comp -1.0",          ["--gains", "0.84", "--pulse-drive-comp", "-1.0"]),
    ("drive-comp 1e6",           ["--gains", "0.84", "--pulse-drive-comp", "1e6"]),
    ("drive-comp nan",           ["--gains", "0.84", "--pulse-drive-comp", "nan"]),
    ("drive-comp inf",           ["--gains", "0.84", "--pulse-drive-comp", "inf"]),
    ("drive-comp -inf",          ["--gains", "0.84", "--pulse-drive-comp", "-inf"]),
]

_INJECT_HEAD = ("import pathlib, sys\n"
                f"sys.path[:0] = [{str(ROOT / 'model')!r}, {str(ROOT / 'tools')!r}]\n")

#: one (label, patch source) per injected case; each runs `phrases --gains 0.84`
INJECTED = [
    ("inject: InsufficientEvidence at score.measure",
     "import audio_measure as am\n"
     "import mono_m5a_score as score\n"
     "def _measure(**kw):\n"
     "    raise am.InsufficientEvidence('injected: insufficient evidence at score.measure')\n"
     "score.measure = _measure\n"),
    ("inject: NonFiniteAudio at score.measure",
     "import audio_measure as am\n"
     "import mono_m5a_score as score\n"
     "def _measure(**kw):\n"
     "    raise am.NonFiniteAudio('injected: non-finite audio at score.measure')\n"
     "score.measure = _measure\n"),
    ("inject: plain AssertionError at score.measure",
     "import mono_m5a_score as score\n"
     "def _measure(**kw):\n"
     "    raise AssertionError('injected: plain AssertionError at score.measure')\n"
     "score.measure = _measure\n"),
    ("inject: oscillator stage tap silent, output live",
     "import numpy as np\n"
     "import mono_m5a_score as score\n"
     "_orig = score.vf.render_mono_fx\n"
     "def _render(seq, dur, voice=None, host=None):\n"
     "    pcm = _orig(seq, dur, voice, host)\n"
     "    voice.trace['osc'][0] = np.zeros_like(voice.trace['osc'][0])\n"
     "    return pcm\n"
     "score.vf.render_mono_fx = _render\n"),
]


def _argv(label_extra: list[str], out: pathlib.Path) -> list[str]:
    return ["phrases", *label_extra, "--out", str(out)]


def _driver(argv: list[str], patch: str) -> str:
    return (_INJECT_HEAD
            + f"sys.argv = [{str(TOOL)!r}] + {argv!r}\n"
            "import measure_pulse2x_headroom as mph\n"
            f"{patch}\n"
            "raise SystemExit(mph.main())\n")


def run_case(kind: str, label: str, payload, timeout: float) -> dict:
    """One case in its own interpreter. Reports the process's own exit status,
    the REFUSED lines, the traceback's last line, and whether --out was written."""
    with tempfile.TemporaryDirectory() as tmp:
        out = pathlib.Path(tmp) / "out.json"
        argv = _argv(payload, out) if kind == "accepted" else _argv(["--gains", "0.84"], out)
        cmd = ([sys.executable, str(TOOL), *argv] if kind == "accepted"
               else [sys.executable, "-c", _driver(argv, payload)])
        t0 = time.monotonic()
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=str(ROOT))
            rc, stdout, stderr, timed_out = p.returncode, p.stdout, p.stderr, False
        except subprocess.TimeoutExpired as e:
            rc, stdout, stderr, timed_out = None, e.stdout or "", e.stderr or "", True
        elapsed = time.monotonic() - t0
        refused = [ln for ln in stdout.splitlines() if ln.startswith("REFUSED")]
        has_tb = "Traceback" in stderr
        lines = [ln for ln in stderr.splitlines() if ln.strip()]
        exc_cls, tb_last, site = None, None, []
        if has_tb and lines:
            m = EXC_LINE.match(lines[-1])
            if m:
                exc_cls, tb_last = m.group("cls"), lines[-1]
            site = [f"{pathlib.Path(f['path']).name}:{f['line']}" for f in FRAME.finditer(stderr)][-3:]
        if timed_out:
            outcome = "NO-VERDICT (timeout)"
        elif rc == 2 and refused:
            outcome = "REFUSED (2)"
        elif rc == 2 and "error: argument" in stderr and "usage:" in stderr:
            # argparse's own usage-error status is ALSO 2, the exit contract's
            # REFUSED: `-inf` reads as an unknown flag, so it is not an accepted
            # input at all, and must not be counted as a refusal either
            outcome = "ARGPARSE USAGE ERROR (2): not an accepted input"
        elif rc == 0:
            outcome = "MEASURED (0)"
        elif exc_cls in IE_CLASSES:
            outcome = f"ESCAPED: {exc_cls.split('.')[-1]} (1)"
        elif exc_cls:
            outcome = f"EXIT 1, other exception: {exc_cls}"
        else:
            outcome = f"EXIT {rc}, no traceback parsed"
        return {"kind": kind, "label": label, "argv": argv, "exit": rc, "elapsed_s": round(elapsed, 1),
                "outcome": outcome, "refused": refused, "traceback_last": tb_last, "raising_site": site,
                "traceback": has_tb, "out_written": out.exists(),
                "baseline_scored": "M5A/0.850 (pulse2x as built)" in stdout,
                "stdout_tail": stdout[-300:], "stderr_tail": stderr[-600:]}


def _git(*a: str) -> str:
    return subprocess.run(["git", *a], cwd=str(ROOT), capture_output=True, text=True).stdout.strip()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--group", choices=("accepted", "injected", "all"), default="all")
    ap.add_argument("--filter", default="", help="run only the rows whose label contains this")
    ap.add_argument("--jobs", type=int, default=2)
    ap.add_argument("--json", type=pathlib.Path)
    ap.add_argument("--timeout", type=float, default=900.0)
    a = ap.parse_args(argv)
    cases = []
    if a.group in ("accepted", "all"):
        cases += [("accepted", lb, extra) for lb, extra in ACCEPTED]
    if a.group in ("injected", "all"):
        cases += [("injected", lb, patch) for lb, patch in INJECTED]
    cases = [c for c in cases if a.filter in c[1]]
    rows = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, a.jobs)) as ex:
        futs = [ex.submit(run_case, kind, lb, payload, a.timeout) for kind, lb, payload in cases]
        for f in futs:
            r = f.result()
            rows.append(r)
            print(f"{r['outcome']:48s} {r['elapsed_s']:6.1f}s  {r['label']}", flush=True)

    print()
    print(f"{'case':44s} {'exit':>4s} {'out':>3s} {'base':>4s}  outcome / gate")
    for r in rows:
        detail = r["refused"][0] if r["refused"] else (r["traceback_last"] or "")
        site = f"  @ {' <- '.join(r['raising_site'])}" if r["raising_site"] else ""
        print(f"{r['label'][:44]:44s} {str(r['exit']):>4s} {'y' if r['out_written'] else 'n':>3s} "
              f"{'y' if r['baseline_scored'] else 'n':>4s}  {r['outcome']}  | {detail[:110]}{site}")
    escaped = [r["label"] for r in rows if r["kind"] == "accepted" and r["outcome"].startswith("ESCAPED")]
    rejected = [r for r in rows if r["kind"] == "accepted" and r["outcome"].startswith("ARGPARSE")]
    accepted = [r for r in rows if r["kind"] == "accepted" and not r["outcome"].startswith("ARGPARSE")]
    tally = {k: sum(1 for r in accepted if r["outcome"].startswith(k))
             for k in ("REFUSED", "MEASURED", "ESCAPED", "EXIT 1, other exception")}
    control = next((r for r in rows if r["label"].startswith("inject: InsufficientEvidence")), None)
    print()
    print(f"HEAD {_git('rev-parse', 'HEAD')}  tool_dirty={_git('status', '--porcelain', '--', str(TOOL.relative_to(ROOT))) != ''}")
    print(f"rejected by argparse before the tool ran (not accepted inputs): {len(rejected)} {[r['label'] for r in rejected]}")
    print(f"accepted inputs: {len(accepted)}  -> {tally}")
    print(f"accepted inputs that escaped as InsufficientEvidence/NonFiniteAudio: {len(escaped)} {escaped}")
    if control is not None:
        print(f"injected control at score.measure -> {control['outcome']} "
              f"({'the probe can see the defect' if control['outcome'].startswith('ESCAPED') else 'PROBE IS BLIND: no accepted row means anything'})")
    if a.json:
        a.json.parent.mkdir(parents=True, exist_ok=True)
        a.json.write_text(json.dumps({"head": _git("rev-parse", "HEAD"), "rows": rows}, indent=1) + "\n")
        print(f"wrote {a.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

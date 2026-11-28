#!/usr/bin/env python3
"""Known-answer controls for the #1782 census row `tools/measure_m5_operating_level.py`
(#1898): does any input the tool accepts make `audio_measure.harmonic_signature`
raise `InsufficientEvidence` past the Refused-only dispatch in `main`?

    .venv/bin/python -m pytest tools/test_m5_operating_level_refusal.py -q
    .venv/bin/python tools/test_m5_operating_level_refusal.py --trace   # every accepted input, ~7 min

THE CENSUS ROW is right about the handler and wrong about reachability. The
handler set is exactly `score.Refused` (`_run`) and `refusal.Refused` (`main`);
`InsufficientEvidence` is an `AssertionError`, so it passes both. An injected
one leaves the real CLI at exit 1 with a traceback (the red run, below). But
NO INPUT THE TOOL ACCEPTS REACHES THE RAISE, so by the issue's own rule ("a
candidate found unreachable gets no code change") the handler is unchanged.

WHAT `phrase()` CAN ASK `harmonic_signature` (all three calls pass `f0`):
`measure:464` on the model segment, `measure:465` on the reference segment and
`_stage_diagnostics:219` on oscillator, mixer, ladder and output. With `f0`
given, `harmonic_signature` raises on exactly four conditions, read from
`model/audio_measure.py` and confirmed by `test_..._known_answers` below:

  non-finite      `NonFiniteAudio` from `_as_float`, before anything else
  silent          `peak(x) <= 1e-9`
  f0 outside      `(0, Nyquist)`, through `windowed_tone_amplitude(...).require()`
  too few periods `len(x) * f0 / sr < 12`, through the same `.require()`

`refine_f0` runs first on both segments (`measure:458`) and its ok excludes the
first two on them (it raises `NonFiniteAudio` on a non-finite record and refuses
a silent one, which `measure:460` turns into `Refused`); its f0 is within 50
cents of a MIDI note, excluding the third. It does NOT exclude the fourth: a
480..1100-sample record at MIDI 72 passes `refine_f0` and then raises "too few
periods". What closes that route is the frozen manifests: every event gate is
0.6 s, the window is `gate - 0.2 s`, and the fewest periods on offer is 209
(MIDI 72). The stage traces are integer arrays (`osc`, `mixed` and `ladder`
int64, the output `render_mono_fx` returns int16), so they cannot be
non-finite; whether one can be SILENT in a window is the one question reading
cannot settle, so it is measured.

THE TRACE (every accepted input: 2 engines x the 4 drives of the frozen budget,
`main` driven in-process with `harmonic_signature` observed through
`mono_m5a_score`'s view of `audio_measure`; `origin/main` @ `dd0449e0`,
2026-11-28). "peak" is the smallest `max|x|` over the calls at that site, in
units of full scale, against `is_silent`'s 1e-9:

    engine   drive  exit  calls  raised  min periods   peak seg / osc / mix / ladder / out
    pulse2x  0.75   0     48     0       209.3         .171 / .839 / .839 / .400 / .171
    pulse2x  0.50   0     48     0       209.3         .186 / .839 / .839 / .314 / .186
    pulse2x  0.35   0     48     0       209.3         .190 / .839 / .839 / .241 / .198
    pulse2x  0.25   0      0     -       -             REFUSED at measure:407 (see below)
    r1       0.75   0     48     0       209.3         .171 / .880 / .880 / .400 / .171
    r1       0.50   0     48     0       209.3         .186 / .880 / .880 / .314 / .186
    r1       0.35   0     48     0       209.3         .190 / .880 / .880 / .241 / .198
    r1       0.25   0      0     -       -             REFUSED at measure:407

    control: `InsufficientEvidence` injected at the site, real CLI  exit 1, traceback
    control: plain `AssertionError` at the site, real CLI        exit 1, traceback

1. No input the tool accepts reaches the raise: 288 calls, none raised, every
   one finite, the quietest site 1.7e8 times the silence floor, the shortest
   window 17 times the period floor.
2. Drive 0.25 never asks: `calibrate` returns x2.65 and `measure:407` refuses
   "saw volume correction would leave the supported 0..1 range" before the first
   render; `_run:198` records it as a refused row and the process exits 0. The
   #1763 shape -- a candidate stopped by an earlier gate.
3. The stage traces are not silent in any window (the Curator's open question):
   oscillator and mixer sit at 0.84-0.88 of full scale whatever the drive, and
   the ladder, which the drive does move, no lower than 0.24.

WRONG-THEN-RIGHT (1, in the harness, 0 on the measurement): the child
interpreter's first run exited 2 in 0.2 s because `sys.argv[1:]` still carried
the `-c` arguments into argparse. Caught by the return-code assertion on the
first run, not read; fixed by rewriting `sys.argv` in the child. A second slip
(`EVENTS` flattened one level too many) was caught by re-reading before any
run, which is inspection, not a control.

EACH CONTROL WAS WATCHED FAIL (rule 5), by injecting the defect and reverting:
the handler widened to `am.InsufficientEvidence` -> `test_premise_...` and
`test_injected_...` red, `test_plain_assertion_...` green as it must be;
widened to `AssertionError` -> all three red (the inverse defect); one
manifest gate cut to 0.2 s -> `test_every_event_window_...` red; a silent
render, which refuses at `measure:460` before any call -> the trace test red
on its call COUNT, which is the vacuity guard and nothing else.

WHAT EACH CONTROL IS BLIND TO (rule 8). `drive` records only the calls MADE: a
run refused before a call would pass "no call raised" vacuously, so the trace
test pins the call count (2 segments + 4 stages per event, 8 events) and the
exit status with it. The injected controls are the spy's own evidence that it
sees what it traces. An input that satisfies every check here while reaching an
uncaught `InsufficientEvidence` would have to come through a different
estimator call; `phrase -> measure` also calls `refine_f0`, `rms_envelope`,
`foldback_alias_db` and `rms` unguarded against `NonFiniteAudio`, and those are
left to their own census rows.
"""
from __future__ import annotations

import ast
import collections
import json
import pathlib
import subprocess
import sys
import tempfile

import numpy as np
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "model"))

import audio_measure as am                               # noqa: E402
import exit_contract                                    # noqa: E402
import mono_m5a_score as score                          # noqa: E402
import refusal                                          # noqa: E402
import voice_fx as vf                                   # noqa: E402
import measure_m5_operating_level as tool               # noqa: E402

TOOL = ROOT / "tools" / "measure_m5_operating_level.py"
SR = 48_000
SILENT = 1e-9                                             # audio_measure.is_silent's floor
MIN_PERIODS = 12.0                                        # windowed_tone_amplitude's floor
EVENTS = {"M5A": ((84, 96), (84, 96)), "M5B": ((72, 84), (72, 84))}   # notes per segment, in order
STAGES = ("oscillator", "mixer", "ladder", "output")
MARK = "M5OL_SEAM "
ENGINES = ("pulse2x", "r1")
DRIVES = (tool.BASE_DRIVE,) + tool.CANDIDATE_DRIVES
INJECTED = "harmonic_signature: injected #1898 control"
PLAIN = "plain assertion, not an estimator refusal"


# ---------------------------------------------------------------------------
# the seam: audio_measure as mono_m5a_score sees it, harmonic_signature observed
# ---------------------------------------------------------------------------
class Seam:
    """Forwards everything to the real module except `harmonic_signature`, which
    records the facts that decide its refusal and, given `kind`, raises before
    the real estimator runs. Bound to `mono_m5a_score.am` only: the census row's
    route is `phrase -> score.measure -> am.harmonic_signature`, and nothing
    else on it is touched."""

    def __init__(self, real, *, kind=None, record=None, announce=False):
        self._real, self._kind, self._announce = real, kind, announce
        self.record = [] if record is None else record

    def __getattr__(self, name):
        return getattr(self._real, name)

    def harmonic_signature(self, x, sr=am.SR_DEFAULT, **kw):
        try:
            f = sys._getframe(1)
            where = f"{f.f_code.co_name}/{f.f_locals.get('name', 'segment')}"
        except Exception:                                   # pragma: no cover - CPython only
            where = "?/?"
        x = np.asarray(x)
        f0 = kw.get("f0")
        row = {"where": where, "n": int(len(x)), "f0": None if f0 is None else float(f0),
               "sr": int(sr), "dtype": str(x.dtype), "finite": bool(np.isfinite(x).all()),
               "peak": float(np.max(np.abs(x))) if x.size else 0.0, "raised": None}
        row["periods"] = None if f0 is None else row["n"] * row["f0"] / row["sr"]
        self.record.append(row)
        if self._kind == "ie":
            row["raised"] = "InsufficientEvidence"
        elif self._kind == "plain":
            row["raised"] = "AssertionError"
        if self._announce:
            print(MARK + json.dumps(row), flush=True)
        if self._kind == "ie":
            raise self._real.InsufficientEvidence(INJECTED)
        if self._kind == "plain":
            raise AssertionError(PLAIN)
        try:
            return self._real.harmonic_signature(x, sr, **kw)
        except self._real.InsufficientEvidence as exc:
            row["raised"] = f"{type(exc).__name__}: {exc}"
            raise


def install(kind=None, *, record=None, announce=False) -> Seam:
    seam = Seam(am, kind=kind, record=record, announce=announce)
    score.am = seam
    return seam


def drive(argv, *, kind=None) -> dict:
    """The tool's real `main(argv)` in-process under the seam. An exception that
    leaves `main` is what the process would render as exit 1 + traceback."""
    record = []
    orig, score.am = score.am, Seam(am, kind=kind, record=record)
    try:
        try:
            cell = {"exit": tool.main([str(a) for a in argv]), "raised": None}
        except Exception as exc:                            # the harness records; it does not handle
            cell = {"exit": exit_contract.FAIL, "raised": exc}
    finally:
        score.am = orig
    cell["calls"] = record
    return cell


def _real_cli(kind: str, out: pathlib.Path) -> subprocess.CompletedProcess:
    """`python tools/measure_m5_operating_level.py --engine pulse2x --drives 0.75
    --out <out>` in a child interpreter with the seam installed first, so the
    exit status and the traceback are the process's own."""
    child = (
        "import runpy, sys\n"
        "tools, model, kind = sys.argv[1:4]\n"
        "sys.path[:0] = [tools, model]\n"
        "sys.argv = ['measure_m5_operating_level.py'] + sys.argv[4:]   # argparse must not see ours\n"
        "import test_m5_operating_level_refusal as T\n"
        "T.install(kind=kind, announce=True)\n"
        "runpy.run_path(str(T.TOOL), run_name='__main__')\n")
    cmd = [sys.executable, "-c", child, str(ROOT / "tools"), str(ROOT / "model"), kind,
           "--engine", "pulse2x", "--drives", str(tool.BASE_DRIVE), "--out", str(out)]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=900, cwd=str(ROOT))


def _announced(stdout: str) -> list:
    return [json.loads(line[len(MARK):]) for line in stdout.splitlines() if line.startswith(MARK)]


# ---------------------------------------------------------------------------
# known answers, independent of the tool: what harmonic_signature refuses with
# f0 given, and what refine_f0 does and does not gate
# ---------------------------------------------------------------------------
def _saw(n: int, f0: float) -> np.ndarray:
    """A band-limited saw at exactly `f0`; every partial below Nyquist."""
    t = np.arange(n) / SR
    x = np.zeros(n)
    k = 1
    while k * f0 < SR / 2:
        x += np.sin(2 * np.pi * k * f0 * t) / k
        k += 1
    return 0.3 * x


def test_premise_the_dispatch_names_nothing_an_insufficient_evidence_is_under():
    assert issubclass(am.InsufficientEvidence, AssertionError)
    assert not issubclass(am.InsufficientEvidence, refusal.Refused)
    assert not issubclass(am.InsufficientEvidence, (OSError, ValueError, RuntimeError))
    for klass in (tool.Refused, score.Refused):
        assert issubclass(klass, refusal.Refused), klass
    named = set()
    for node in ast.walk(ast.parse(TOOL.read_text())):
        if isinstance(node, ast.ExceptHandler):
            t = node.type
            named.update(ast.unparse(e) for e in (t.elts if isinstance(t, ast.Tuple) else [t]))
    assert named == {"score.Refused", "refusal.Refused"}, sorted(named)


def test_known_answers_silent_and_non_finite_are_stopped_before_the_site():
    f0 = vf.note_hz(72)
    x = np.zeros(19_200)
    e = am.refine_f0(x, f0, SR)
    assert not e.ok and e.reason == "silent"             # measure:460 -> Refused, before :464
    with pytest.raises(am.InsufficientEvidence, match="silent"):
        am.harmonic_signature(x, SR, f0=f0, kmax=12)
    x = _saw(19_200, f0)
    x[5] = np.nan
    for call in (am.refine_f0, am.harmonic_signature):   # refine_f0 at :458 raises first
        with pytest.raises(am.NonFiniteAudio):
            call(x, f0, SR) if call is am.refine_f0 else call(x, SR, f0=f0, kmax=12)


def test_known_answers_refine_f0_ok_does_not_imply_the_site_will_not_raise():
    """The gap the manifests have to close: at 480..1100 samples `refine_f0`
    returns ok on this tone and `harmonic_signature` raises "too few periods"
    on the same window. Red means `refine_f0` learned to refuse it (the gap
    closed at the gate) or `min_periods` moved: re-read the trace above."""
    f0 = vf.note_hz(72)
    for n in (480, 1100):
        x = _saw(n, f0)
        e = am.refine_f0(x, f0, SR)
        assert e.ok, e
        assert n * e.value / SR < MIN_PERIODS
        with pytest.raises(am.InsufficientEvidence, match="too few periods"):
            am.harmonic_signature(x, SR, f0=e.value, kmax=12)
    x = _saw(19_200, f0)
    e = am.refine_f0(x, f0, SR)
    assert e.ok and am.harmonic_signature(x, SR, f0=e.value, kmax=12)["n_valid"] > 0


def _manifest_windows() -> list:
    rows = []
    for case, per_segment in EVENTS.items():
        manifest = json.loads(score.MANIFESTS[case].read_text())
        for seg, notes in zip(manifest["timeline"]["segments"], per_segment):
            for ev, note in zip(seg["midi_events"], notes):
                assert int(ev["note"]) == note
                gate = float(ev["gate_s"])
                rows.append({"case": case, "wave": seg["wave"], "note": note,
                             "window_s": gate - 0.12 - 0.08,
                             "periods": (gate - 0.12 - 0.08) * vf.note_hz(note)})
    return rows


def test_every_event_window_holds_far_more_than_the_12_periods_the_site_needs():
    """The structural reason, from the frozen manifests, no render. Red on the
    first assertion: a gate shrank below `0.2 s + 12 / f0` and the short-window
    route is live -- add the handler (#1751 shape) and replace this file's
    record. Red on the second: the margin eroded; re-run `--trace`."""
    rows = _manifest_windows()
    assert len(rows) == 8
    for r in rows:
        assert r["periods"] >= MIN_PERIODS, r
    assert min(r["periods"] for r in rows) >= 100          # measured: 209.3 at MIDI 72


# ---------------------------------------------------------------------------
# the red run and its control, on the real CLI
# ---------------------------------------------------------------------------
def test_injected_estimator_refusal_escapes_the_real_cli_uncaught(tmp_path):
    """THE RED RUN (#1898 AC 1). An `InsufficientEvidence` raised at the site
    inside `phrase()` leaves `main` uncaught: exit 1, traceback, no REFUSED,
    no record written. This is the census row made real and the spy's evidence
    that it sees what it traces. If it exits 2, someone widened the handler and
    this file should become that fix's test; the trace below says why it was
    withheld."""
    out = tmp_path / "ie.json"
    p = _real_cli("ie", out)
    assert p.returncode == exit_contract.FAIL, (p.returncode, p.stdout[-300:], p.stderr[-600:])
    assert "Traceback" in p.stderr and "InsufficientEvidence" in p.stderr, p.stderr[-600:]
    assert INJECTED in p.stderr and "REFUSED" not in p.stdout
    for frame in ("measure_m5_operating_level.py", "in phrase", "in measure", "mono_m5a_score.py"):
        assert frame in p.stderr, frame
    seen = _announced(p.stdout)
    assert len(seen) == 1 and seen[0]["where"] == "measure/segment", seen
    assert not out.exists() and list(tmp_path.iterdir()) == []


def test_plain_assertion_error_at_the_same_site_stays_an_execution_error(tmp_path):
    """The issue's edge case: an `AssertionError` that is not an estimator
    refusal is nobody's refusal. Must stay exit 1 whatever happens to the
    handler; red means the dispatch was widened to the base class."""
    out = tmp_path / "plain.json"
    p = _real_cli("plain", out)
    assert p.returncode == exit_contract.FAIL, (p.returncode, p.stdout[-300:], p.stderr[-600:])
    assert "Traceback" in p.stderr and f"AssertionError: {PLAIN}" in p.stderr, p.stderr[-600:]
    assert "REFUSED" not in p.stdout and "InsufficientEvidence" not in p.stderr
    seen = _announced(p.stdout)
    assert len(seen) == 1 and seen[0]["where"] == "measure/segment", seen
    assert not out.exists()


# ---------------------------------------------------------------------------
# the trace, on the default accepted input: the known-good run, observed
# ---------------------------------------------------------------------------
def test_default_input_passes_every_gate_and_never_asks_the_site_to_refuse(tmp_path):
    """`--engine pulse2x --drives 0.75`: exit 0, and every one of the 48 calls
    (8 events x [2 segments + 4 stages]) finite, not silent and over 12
    periods. Red on a `raised`: an accepted input reached the raise and the
    withheld handler is due. Red on the count: a refusal moved ahead of the
    site, which would make "nothing raised" vacuous (rule 8)."""
    cell = drive(["--engine", "pulse2x", "--drives", str(tool.BASE_DRIVE), "--out", tmp_path / "base.json"])
    assert cell["exit"] == exit_contract.PASS and cell["raised"] is None, cell["raised"]
    calls = cell["calls"]
    assert collections.Counter(c["where"] for c in calls) == collections.Counter(
        {"measure/segment": 16, **{f"_stage_diagnostics/{s}": 8 for s in STAGES}})
    assert not any(c["raised"] for c in calls), [c for c in calls if c["raised"]]
    assert all(c["finite"] and c["f0"] is not None for c in calls)
    assert all(c["peak"] > SILENT for c in calls), min(calls, key=lambda c: c["peak"])
    assert all(c["periods"] >= MIN_PERIODS for c in calls), min(calls, key=lambda c: c["periods"])
    assert min(c["periods"] for c in calls) >= 100           # measured: 209.3 at MIDI 72
    assert (tmp_path / "base.json").exists()


# ---------------------------------------------------------------------------
# every accepted input, for the record: `python tools/test_m5_operating_level_refusal.py --trace`
# ---------------------------------------------------------------------------
def trace_all() -> list:
    """One row per accepted input, the rows in the docstring above."""
    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        for engine in ENGINES:
            for d in DRIVES:
                out = pathlib.Path(tmp) / f"{engine}-{d}.json"
                cell = drive(["--engine", engine, "--drives", str(d), "--out", out])
                calls = cell["calls"]
                row = {"engine": engine, "drive": d, "exit": cell["exit"],
                       "escaped": None if cell["raised"] is None else repr(cell["raised"]),
                       "calls": len(calls), "site_raises": sum(1 for c in calls if c["raised"]),
                       "where": dict(sorted(collections.Counter(c["where"] for c in calls).items())),
                       "min_periods": min((c["periods"] for c in calls if c["periods"] is not None), default=None),
                       "min_peak": {s: min((c["peak"] for c in calls if c["where"].endswith(s)), default=None)
                                    for s in ("segment",) + STAGES},
                       "all_finite": all(c["finite"] for c in calls)}
                if cell["exit"] == exit_contract.PASS:
                    res = json.loads(out.read_text())["rows"]
                    row["refused_rows"] = {k: v["refused"] for k, v in res.items() if "refused" in v}
                rows.append(row)
                print(json.dumps(row), flush=True)
    return rows


if __name__ == "__main__":
    trace_all()

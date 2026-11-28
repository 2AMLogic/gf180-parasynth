#!/usr/bin/env python3
"""#1923: can `audio_measure.InsufficientEvidence` leave `tools/score_m5a_i2s.py`?
Traced red-first on main @ 8f3d8ed2 (2026-11-28). Verdict: no input reaches it.

THE ROW (the #1782 census, `estimator_refusal_census.py`). `main` calls
`mono_m5a_score.measure` under `except (m5a.Refused, OSError, ValueError)`
(score_m5a_i2s.py:150); `measure` calls `audio_measure.harmonic_signature`
(mono_m5a_score.py:464-465), which raises `InsufficientEvidence` -- an
`AssertionError`, not a `Refused` -- at three sites. None of the three handler
classes is a superclass of it.

THE HANDLER CLAIM IS TRUE, and was observed rather than argued: with
`m5a.measure` replaced by a function that raises the class at that call site,
the real `__main__` exits 1 with `audio_measure.InsufficientEvidence: ...` in
the traceback and prints nothing; a plain `AssertionError` there does the same.
The census row is a real gap in the handler.

THE RAISE IS NOT REACHABLE from any `--wav` the tool accepts. Each site is
stopped by something the candidate cannot change:

  audio_measure.py:2379  "silent"          `refine_f0` runs `is_silent()` on the
                                          same array first (audio_measure.py:2583)
                                          and `measure` turns not-ok into Refused
                                          (mono_m5a_score.py:459). Rows below.
  audio_measure.py:2389  "no fundamental"  dead on this path: it runs only when
                                          `f0 is None`, and `measure` always passes
                                          `f0=eo.value`, a float. The trace below
                                          pins that every call had a float.
  audio_measure.py:2391  `.require()`      `windowed_tone_amplitude` refuses on
                                          silence (gated as above), a frequency
                                          outside (0, Nyquist), or fewer than
                                          `min_periods` periods of the record.
                                          The window is
                                          int((off - 0.08) * SR) - int((on + 0.12) * SR)
                                          samples (mono_m5a_score.py:455) and the
                                          frequency is within `max_cents` of the
                                          commanded note: both come from the frozen
                                          manifest, so the count is the same for
                                          every candidate. For M5A and M5B it is
                                          19,199 or 19,200 samples at 523-2093 Hz,
                                          at least 203 periods against the 12
                                          required (re-derived below, not trusted).

So the handler is NOT widened: an unreachable candidate gets no code change.
What this file pins is the reasoning, so that a change which makes the raise
reachable (a shorter gate in a manifest, a larger `min_periods`, a window that
moves, a `harmonic_signature` that refuses on something else) turns a row red
and names the handler as the thing to revisit. The exit status of an injected
refusal is deliberately NOT asserted here, so widening the handler later does
not turn this file red (the `holdout_f1d_preconditions` precedent in
tools/test_insufficient_evidence_dispatch.py).

The input under test is the one the tool takes: a decoded I2S WAV and the
bench transcript bound to its digest. The model voice is never rendered on
this path (`measure` renders only when no candidate WAV is given), so the
sibling rows that take that path (#1898-#1908) have a different question --
there `_stage_diagnostics` (mono_m5a_score.py:219) calls the same estimator on
four stage vectors with no pitch gate in front of it.

WRONG-THEN-RIGHT (2, both a prediction from reading that the run corrected):

  1. The issue suggests "a segment with a pitch lock but no dominant
     fundamental" as a route. There is none: the dominance check at :2389 runs
     only when `f0 is None`, and a pitch lock IS `f0` being passed in.
  2. A pure tone at the note was predicted to stop at `harmonic_summary` ("fewer
     than two comparable partials"). It stops at the envelope estimator: a
     partial absent on one side is compared against that side's measured floor
     and counts as measured (mono_m5a_score.py:135-143). The rows pin the words
     the run printed.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import pathlib
import re
import subprocess
import sys
from typing import NamedTuple

import numpy as np
import pytest
from scipy.io import wavfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "model"))
import audio_measure as am                                 # noqa: E402
import exit_contract                                       # noqa: E402
import mono_m5a_score as m5a                               # noqa: E402

SR = m5a.SR
CASE_DIR = ROOT / "docs/scorecard/mono-m5a-miniv3"
COMMITTED_WAV = CASE_DIR / "saw-cutoff-20khz-i2s.wav"
COMMITTED_TRANSCRIPT = CASE_DIR / "saw-cutoff-20khz-i2s.txt"
SCORER = ROOT / "tools" / "score_m5a_i2s.py"

#: reported, never a verdict about the tool, when a seam has not taken
SEAM_FAILED = "SEAM FAILED"
INJECTED = "injected at harmonic_signature"

#: the analysis window `measure` cuts per note event (mono_m5a_score.py:455) and
#: the most it trims either side to (mono_m5a_score.py:444-447). Duplicated here
#: on purpose: the trace asserts the window the tool actually used equals this
#: one, so a change there without a change here is a red row, not a silent gap.
WINDOW_AFTER_ON_S = 0.12
WINDOW_BEFORE_OFF_S = 0.08
MAX_TRIM_SAMPLES = 128

MIN_PERIODS = inspect.signature(am.windowed_tone_amplitude).parameters["min_periods"].default
MAX_CENTS = inspect.signature(am.refine_f0).parameters["max_cents"].default

_CHILD_PATHS = "sys.path[:0] = %r\n" % [str(ROOT / p) for p in ("model", "tools", "audition")]


# ---------------------------------------------------------------------------
# the frozen window: the one input-independent quantity behind :2391
# ---------------------------------------------------------------------------
class Event(NamedTuple):
    wave: str
    segment_samples: int
    a: int                   # first sample of the window, in its segment
    b: int                   # one past the last
    note_hz: float

    @property
    def n(self) -> int:
        return self.b - self.a


def _events(case_id: str) -> list[Event]:
    """Every note event of a frozen manifest with the window `measure` gives
    the estimator, computed the way `measure` computes it."""
    manifest = json.loads(m5a.MANIFESTS[case_id].read_text())
    events = []
    for seg in manifest["timeline"]["segments"]:
        for ev in seg["midi_events"]:
            on, off = float(ev["on_s"]), float(ev["on_s"]) + float(ev["gate_s"])
            events.append(Event(seg["wave"], int(seg["samples"]),
                                int((on + WINDOW_AFTER_ON_S) * SR),
                                int((off - WINDOW_BEFORE_OFF_S) * SR),
                                m5a.vf.note_hz(int(ev["note"]))))
    return events


@pytest.mark.parametrize("case_id", sorted(m5a.MANIFESTS))
def test_every_frozen_window_holds_the_periods_harmonic_signature_requires(case_id):
    """`:2391` refuses a record shorter than `min_periods` periods of a
    frequency that is within `max_cents` of the note. Both numbers come from
    the frozen manifest, so this is the whole candidate space in one check."""
    events = _events(case_id)
    assert len(events) >= 4, events
    for e in events:
        low, high = e.note_hz * 2 ** (-MAX_CENTS / 1200), e.note_hz * 2 ** (MAX_CENTS / 1200)
        assert 0 < low and high < SR / 2, e
        assert e.n * low / SR >= MIN_PERIODS, (e, "periods", e.n * low / SR)
        # `measure` trims both sides to the shorter of candidate and reference,
        # by at most MAX_TRIM_SAMPLES: the reference's window must stay whole,
        # or the count above is the candidate's and not the reference's
        assert e.b + MAX_TRIM_SAMPLES <= e.segment_samples, e


# ---------------------------------------------------------------------------
# the child interpreter: the real __main__, with the estimators traced
# ---------------------------------------------------------------------------
def _tracer(inject: bool = False) -> str:
    """Child-interpreter source: wrap the two estimators `measure` calls per
    event and print what each was asked as one TRACE line at exit. With
    `inject` the estimator refuses at the listed site instead of running."""
    return (
        "import atexit, json\n"
        "import audio_measure as am\n"
        "records, refined = [], []\n"
        "_refine, _signature = am.refine_f0, am.harmonic_signature\n"
        f"INJECT = {inject!r}\n"
        "def refine_f0(x, *a, **k):\n"
        "    e = _refine(x, *a, **k)\n"
        "    refined.append((x, bool(e.ok)))\n"
        "    return e\n"
        "def harmonic_signature(x, sr=am.SR_DEFAULT, **k):\n"
        "    f0 = k.get('f0')\n"
        "    rec = dict(raised=None)\n"
        "    if not INJECT:\n"
        "        rec.update(n=len(x), f0=f0, f0_type=type(f0).__name__,\n"
        "                   silent=bool(am.is_silent(x)),\n"
        "                   periods=(len(x) * f0 / sr) if type(f0) is float else None,\n"
        "                   refined_ok=any(r is x and ok for r, ok in refined))\n"
        "    records.append(rec)\n"
        "    try:\n"
        "        if INJECT:\n"
        f"            raise am.InsufficientEvidence({INJECTED!r})\n"
        "        return _signature(x, sr, **k)\n"
        "    except BaseException as exc:\n"
        "        rec['raised'] = type(exc).__name__\n"
        "        raise\n"
        "am.refine_f0, am.harmonic_signature = refine_f0, harmonic_signature\n"
        "atexit.register(lambda: print('TRACE ' + json.dumps(records)))\n")


def _scorer(wav: pathlib.Path, transcript: pathlib.Path, tmp_path: pathlib.Path,
            seam: str = "") -> subprocess.CompletedProcess:
    """`python tools/score_m5a_i2s.py --wav ... --verification ...` through its
    real `__main__` (`runpy`), with `seam` installed first. The scored audio
    has to stay inside the repository (score_m5a_i2s.py:141 refuses it
    elsewhere), so it goes under the ignored build/ and is removed after."""
    audio = ROOT / "build" / "test_score_m5a_i2s_refusal" / f"{tmp_path.name}.wav"
    src = (f"import sys, runpy\n{_CHILD_PATHS}\n{seam}"
           f"runpy.run_path({str(SCORER)!r}, run_name='__main__')")
    argv = [sys.executable, "-c", src, "--wav", str(wav), "--verification", str(transcript),
            "--audio", str(audio), "--out", str(tmp_path / "M5A.json")]
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=300, cwd=str(ROOT))
    finally:
        audio.unlink(missing_ok=True)


def _trace(p: subprocess.CompletedProcess) -> list[dict]:
    lines = [line for line in p.stdout.splitlines() if line.startswith("TRACE ")]
    assert len(lines) == 1, p.stdout[-600:] + p.stderr[-600:]
    return json.loads(lines[0][len("TRACE "):])


def _could_not_refuse(record: dict) -> None:
    """What the trace must show for a call that reached the estimator: every
    one of its three raise sites was closed before it ran."""
    assert record["raised"] is None, record
    assert record["f0_type"] == "float", "f0=None opens the :2389 branch: " + repr(record)
    assert 0 < record["f0"] < SR / 2, record
    assert record["silent"] is False, record
    assert record["refined_ok"] is True, "no ok refine_f0 on this array: " + repr(record)
    assert record["periods"] >= MIN_PERIODS, record


# ---------------------------------------------------------------------------
# inputs: the candidates tried to reach the refusal
# ---------------------------------------------------------------------------
def _length() -> int:
    manifest = json.loads(m5a.MANIFEST.read_text())
    return int(round(manifest["timeline"]["audio_duration_s"] * SR))


def _time() -> np.ndarray:
    return np.arange(_length()) / SR


def _write(tmp_path: pathlib.Path, name: str, samples: np.ndarray) -> pathlib.Path:
    """A complete mono int16 48 kHz phrase: the only WAV shape `measure` reads."""
    path = tmp_path / f"{name}.wav"
    pcm = np.clip(np.round(np.asarray(samples, dtype=np.float64) * 32768.0), -32768, 32767)
    wavfile.write(str(path), SR, pcm.astype("<i2"))
    return path


def _per_note(make) -> np.ndarray:
    """`make(freq, t)` evaluated for the first saw event's note over the first
    half of the record and for the second event's note over the rest."""
    t = _time()
    return np.where(t < SWITCH_S, make(NOTE[84], t), make(NOTE[96], t))


def _committed_with_second_event_silenced(tmp_path: pathlib.Path, name: str) -> pathlib.Path:
    """The committed passing run with the second saw event's window zeroed:
    the first event passes everything, so the pitch gate that stops this input
    is the gate at the second event, not an earlier refusal."""
    sr, pcm = wavfile.read(str(COMMITTED_WAV))
    assert sr == SR and pcm.dtype == np.int16
    pcm = pcm.copy()
    second = _events("M5A")[1]
    pcm[second.a:second.b] = 0
    assert not np.any(pcm[second.a:second.b]), SEAM_FAILED + ": the event is not silent now"
    assert np.any(pcm), SEAM_FAILED + ": the whole phrase is silent now"
    path = tmp_path / f"{name}.wav"
    wavfile.write(str(path), SR, pcm)
    return path


def _sine(freq: float, t: np.ndarray) -> np.ndarray:
    return np.sin(2 * np.pi * freq * t)


class Row(NamedTuple):
    id: str
    build: object                 # (tmp_path, name) -> the candidate WAV
    stopped_by: tuple             # the gate's own words on the REFUSED line
    reaches_site: bool            # True when the input gets past the pitch gate


#: between the saw segment's two events (0.1-0.7 s and 5.7-6.3 s)
SWITCH_S = 6.5
NOTE = {note: m5a.vf.note_hz(note) for note in (84, 96)}

CANDIDATES = (
    Row("digital_silence",
        lambda d, n: _write(d, n, np.zeros(_length())),
        ("MIDI 84 pitch refused: silent",), False),
    # the weakest and the loudest signals that still lock the pitch: both
    # reach the estimator, which answers
    Row("one_lsb_square_at_the_note",
        lambda d, n: _write(d, n, _per_note(lambda f, t: np.sign(_sine(f, t)) / 32768)),
        ("envelope estimator refused",), True),
    Row("full_scale_square_at_the_note",
        lambda d, n: _write(d, n, _per_note(lambda f, t: 0.999 * np.sign(_sine(f, t)))),
        ("envelope estimator refused",), True),
    Row("dc_at_full_scale",
        lambda d, n: _write(d, n, np.full(_length(), 0.999)),
        ("MIDI 84 pitch refused:",), False),
    Row("an_octave_down",
        lambda d, n: _write(d, n, _per_note(lambda f, t: 0.9 * _sine(f / 2, t))),
        ("MIDI 84 pitch refused:",), False),
    Row("sixty_cents_sharp",
        lambda d, n: _write(d, n, _per_note(lambda f, t: 0.9 * _sine(f * 2 ** (60 / 1200), t))),
        ("MIDI 84 pitch refused:", "within 50 cents"), False),
    Row("white_noise",
        lambda d, n: _write(d, n, np.random.default_rng(1923).uniform(-1, 1, _length())),
        ("MIDI 84 pitch refused:",), False),
    Row("nyquist_rate_square",
        lambda d, n: _write(d, n, np.where(np.arange(_length()) % 2 == 0, 0.999, -0.999)),
        ("MIDI 84 pitch refused:",), False),
    Row("committed_run_second_event_silenced",
        _committed_with_second_event_silenced,
        ("MIDI 96 pitch refused: silent",), True),
)


def _transcript_for(tmp_path: pathlib.Path, wav: pathlib.Path) -> pathlib.Path:
    """The committed bench transcript re-bound to `wav`'s digest: everything
    else in the report is what a real passing run printed."""
    text, n = re.subn(r"decoded I2S WAV sha256 [0-9a-f]{64}",
                      "decoded I2S WAV sha256 " + hashlib.sha256(wav.read_bytes()).hexdigest(),
                      COMMITTED_TRANSCRIPT.read_text())
    assert n == 1, n
    path = tmp_path / "report.txt"
    path.write_text(text)
    return path


@pytest.mark.parametrize("row", CANDIDATES, ids=[r.id for r in CANDIDATES])
def test_no_candidate_reaches_the_estimators_refusal_past_the_gates(row, tmp_path):
    """Each candidate leaves as REFUSED (exit 2, the gate's own words, nothing
    after them, no report), never as the estimator's own refusal. A row that
    says `reaches_site` got past the pitch gate and into `harmonic_signature`;
    there the trace must show every raise site closed. The others never reach
    the estimator at all."""
    wav = row.build(tmp_path, row.id)
    p = _scorer(wav, _transcript_for(tmp_path, wav), tmp_path, _tracer())
    out = p.stdout + p.stderr
    assert SEAM_FAILED not in out, out[-600:]
    assert p.returncode == exit_contract.REFUSED, (p.returncode, out[-600:])
    assert "Traceback" not in out and "InsufficientEvidence" not in out, out[-600:]
    said = [line for line in p.stdout.splitlines() if line.startswith("score_m5a_i2s:")]
    assert len(said) == 1 and said[0].startswith("score_m5a_i2s: REFUSED -- "), p.stdout[-400:]
    for text in row.stopped_by:
        assert text in said[0], said[0]
    assert "score_m5a_i2s: valid" not in p.stdout, "a refused run must not reach a verdict"
    assert not (tmp_path / "M5A.json").exists()
    records = _trace(p)
    if row.reaches_site:
        assert records, "this input was expected to get past the pitch gate"
        for record in records:
            _could_not_refuse(record)
    else:
        assert records == [], "stopped before the estimator: " + repr(records)


def test_the_committed_run_reaches_the_site_and_no_call_there_could_refuse(tmp_path):
    """The control for the rows above: on the committed passing run the
    estimator IS called -- once for the candidate and once for the reference
    per event -- so "never refused" is not "never reached". Every call shows
    all three raise sites closed, and the window is the one this file computed
    from the manifest."""
    p = _scorer(COMMITTED_WAV, COMMITTED_TRANSCRIPT, tmp_path, _tracer())
    out = p.stdout + p.stderr
    assert SEAM_FAILED not in out, out[-600:]
    assert p.returncode == exit_contract.PASS, (p.returncode, out[-600:])
    assert "score_m5a_i2s: valid" in p.stdout and "Traceback" not in p.stderr, out[-600:]
    assert (tmp_path / "M5A.json").is_file()
    records = _trace(p)
    events = _events("M5A")
    assert len(records) >= 2 * len(events), (len(records), len(events))
    assert {r["n"] for r in records} == {e.n for e in events}
    for record in records:
        _could_not_refuse(record)


def test_the_trace_sees_a_refusal_raised_at_the_listed_site(tmp_path):
    """The control against a vacuous trace: with `harmonic_signature` made to
    refuse at its first call, the trace reports it. Observed on 8f3d8ed2: exit
    1, `audio_measure.InsufficientEvidence: injected at harmonic_signature` in
    the traceback, nothing on stdout but the TRACE line -- the census row,
    confirmed. The exit status is not asserted (see the module docstring)."""
    p = _scorer(COMMITTED_WAV, COMMITTED_TRANSCRIPT, tmp_path, _tracer(inject=True))
    out = p.stdout + p.stderr
    assert SEAM_FAILED not in out, out[-600:]
    assert INJECTED in out, out[-600:]
    assert "score_m5a_i2s: valid" not in p.stdout, "a refused run must not reach a verdict"
    assert not (tmp_path / "M5A.json").exists()
    assert [r["raised"] for r in _trace(p)] == ["InsufficientEvidence"]

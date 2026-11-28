#!/usr/bin/env python3
"""#1905 trace: can any audio `tools/measure_mono_attack_context.py` accepts make
`audio_measure` raise `InsufficientEvidence` past its Refused-only handler?

    .venv/bin/python tools/probes/mono_attack_context_refusal_trace.py
    .venv/bin/python tools/probes/mono_attack_context_refusal_trace.py --json

THE ROW (filed by the #1782 census, read off the AST; nothing had been run):

    module   tools/measure_mono_attack_context.py, entry point `__main__`
    site     main -> measure_mono_m5a_reference._event_analysis -> rv.measure
             -> audio_measure.harmonic_signature
    raises   InsufficientEvidence, an AssertionError subclass
    handler  except (ref.Refused, OSError, ValueError) -- names no class that
             InsufficientEvidence is under, so a raise there exits 1 with a
             traceback: the apparatus failing to measure, rendered as FAIL

The census reads handlers; it cannot say whether any input REACHES the raise.
That is what this probe does, at three levels per fixture:

    estimator  rv.measure on exactly the window _event_analysis cuts, every
               raise attributed to the audio_measure chain that raised it
    module     the real `main`, end to end. A playback rig stands in for Mini
               V3: it reports whatever it was asked and plays a fixture, so
               the REAL `_render_segment` output gates run before any estimator
    bypassed   the same, with `_render_segment` replaced by the raw fixture --
               the state the census row describes, where nothing but the
               handler decides

CONTROLS THE PROBE OWES. The committed Mini V3 capture must exit 0 through
the real `main` (known-good: REFUSED, exit 2, if it does not -- then nothing
below is evidence). And `am.harmonic_signature` made to raise on that capture
must exit 1 uncaught (injected: NO-VERDICT, exit 1, if it does not -- a probe
that cannot see the failure mode it traces has nothing to report).

WHAT IS BYPASSED, and it is only apparatus: the two git-clean checks (`main`'s
and `_source_provenance`'s), and the plugin identity and reference-integrity
qualification, which need Mini V3. Every estimator and every gate from
`_render_segment` inward is the shipped code.

THE TRACE (2026-11-28, origin/main 8f3d8ed2, Python 3.12, numpy 2.5.5,
scipy 1.18.1; about a minute). VERDICT: UNREACHABLE BEHIND THE RENDER GATE.
In the last two columns `render:N` is `_render_segment` line N and `event:N`
is `_event_analysis` line N, both in `measure_mono_m5a_reference.py`;
"env." is `rms_envelope`, where the bypassed run's raise comes from.

    fixture          estimator (rv.measure)        real gates               gate bypassed
    --------------------------------------------------------------------------------------------
    frozen_capture   saw / pulse:47.9 %, verified  exit 0                   exit 0
    silence          refine_f0: silent             2 render:212             2 event:290
    tone_peak_1e-10  refine_f0: silent             2 render:212             2 event:290
    tone_peak_1e-6   saw ok; foldback: headroom    2 render:212             2 event:314
    sine_at_pitch    sine, not the asked-for wave  2 event:290              2 event:290
    saw_minus_h3     one edge, h3 absent           2 event:290              2 event:290
    saw_100c_flat    refine_f0: -100 cents         2 event:290              2 event:290
    saw_octave_down  refine_f0: 6 dB at f0/2       2 event:290              2 event:290
    saw_octave_up    refine_f0: no line in band    2 event:290              2 event:290
    white_noise      refine_f0: +669 cents         2 event:290              2 event:290
    dc_only          refine_f0: no line in band    2 event:290              2 event:290
    saw_unit_peak    saw ok; foldback: headroom    2 render:214             2 event:314
    ideal_saw_0p5    saw ok; foldback: headroom    2 event:314              2 event:314
    nan_one_sample   RAISE NonFiniteAudio (refine) 2 render:209             1 NonFiniteAudio, env.
    inf_one_sample   RAISE NonFiniteAudio (refine) 2 render:209             1 NonFiniteAudio, env.
    short_1s         saw / pulse, verified         2 render:207             2 event:281
    empty            refine_f0: silent             2 render:207             2 via the ValueError leg
    tremolo_5hz      does not repeat, 23.6 %       2 event:290              2 event:290
    detuned_pair_2c  does not repeat, 243 %        2 event:290              2 event:290
    naive_saw        does not repeat, 16.6 %       2 event:290              2 event:290
    subharmonic_m30  saw ok at 4.46 %; pulse 6.3   2 event:290              2 event:290
    control          estimator made to raise       1 InsufficientEvidence

  1. NO INPUT THE MODULE ACCEPTS REACHES THE RAISE. Through the shipped gates
     every fixture exits 0 or 2, and every 2 is a named `ref.Refused`. The
     handler at `measure_mono_attack_context.py:111` is never asked about an
     `InsufficientEvidence`, so there is nothing for `am.InsufficientEvidence`
     in that tuple to catch. No code change, by the issue's own rule.

  2. `harmonic_signature` DID NOT RAISE IN ANY OF THE 84 ESTIMATOR WINDOWS,
     and it cannot: with `f0` given it raises only through
     `windowed_tone_amplitude(x, f0).require()`, which refuses on exactly
     three conditions -- a silent record, a frequency outside (0, Nyquist),
     fewer than 12 periods. `refine_f0` returning ok on the same window
     excludes all three for every input, not just these fixtures: it has
     already checked `is_silent`, its f0 is within 50 cents of the commanded
     523.25 or 1046.5 Hz, and `_event_analysis:280` requires 0.2 s of steady
     audio, so the fewest periods on offer is 0.2 s x 508 Hz = 101 (measured:
     209 at MIDI 72, 419 at MIDI 84). The same call inside `waveform_id` is
     the same call. `inharmonic_fraction_db` returns an `Estimate` and raises
     nothing; `_event_analysis:313` turns its refusal into `Refused`.

  3. THE ONE ROUTE TO AN `InsufficientEvidence` IS NON-FINITE AUDIO, and
     `_render_segment:208` refuses it by name before any estimator sees it.
     `NonFiniteAudio` is an `InsufficientEvidence` subclass, raised by
     `_as_float` -- in `rms_envelope` first, in `refine_f0` if the envelope
     were not there. With the render gate bypassed it exits 1 exactly as the
     census row predicts; with the gate in place it is `REFUSED: plugin
     output contains NaN or infinity`. That is the #1763 shape: a candidate
     stopped by an earlier gate.

  4. THE CENSUS IS RIGHT ABOUT THE HANDLER AND WRONG ABOUT REACHABILITY. The
     control shows the exit-1 traceback is real the moment an estimator
     refuses. What keeps it from happening is not the handler but the four
     `Refused` gates in `_render_segment` and the explicit `ok` checks in
     `_event_analysis` and `rv.measure` ahead of every estimator.

  5. OBSERVED, NOT ACTED ON: a closed-form saw carrying a component at f0/3
     30 dB down verifies as a saw (period residual 4.46 % against a 5 %
     limit, under `refine_f0`'s -20 dB `sub_floor`). Both are declared
     tolerances of `audio_measure.py`, which this issue puts out of scope.
     `empty` with the gate bypassed exits 2 only because numpy's
     "a cannot be empty" is a ValueError and the handler names ValueError.

WRONG THEN RIGHT (5, all in this probe's apparatus, caught by the probe's own
controls or by reading its table, none by inspecting the code):
  1. The playback rig keyed its repeat counter by a tuple of dicts:
     TypeError, which `_render_segment` wrapped as "Mini V3 render failed",
     so the INJECTED control exited 2 instead of 1 and the probe returned
     NO-VERDICT rather than a table. Keyed on (note, on, gate).
  2. The capture cache had the same key, same TypeError.
  3. The "saw" fixtures summed cosines. That is a symmetric spike with two
     edges, not a ramp; the classifier named it a rectangle of 8 % duty.
     A saw is the sine series.
  4. Synthesised fixtures at unit peak tripped the clip gate at
     `_render_segment:214` before any estimator ran, so ten module rows said
     nothing about the estimators. Now 0.5 peak; `saw_unit_peak` is at full
     scale on purpose.
  5. The non-finite sample sat at 1.0 s, outside every analysis window, so
     the estimator rows measured clean audio. Now at 0.3 s, inside the note.
The verdict was UNREACHABLE_BEHIND_RENDER_GATE on every complete run, and no
fixture's exit code moved. Fixes 3 and 4 changed attribution: ten module rows
that had stopped at the clip gate now stop in `_event_analysis`, and the saw
rows the classifier had called 8 % rectangles are saws.
"""
from __future__ import annotations

import argparse
import contextlib
import functools
import io
import json
import pathlib
import subprocess
import sys
import tempfile
import traceback
from unittest import mock

import numpy as np
from scipy.io import wavfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "model"))

import measure_mono_attack_context as probe   # noqa: E402
import exit_contract                         # noqa: E402

ref, am, rv = probe.ref, probe.ref.am, probe.ref.rv
SR = ref.SR
SEGMENT = 13.5
CAPTURE = ROOT / "docs/scorecard/mono-attack-context"
WINDOWS = (("isolated84", 0), ("from72_gap3p4", 0))


# ---------------------------------------------------------------------------
# fixtures. Synthesised ones are closed-form Fourier series with a 2 ms
# attack, a held gate and a 4 ms exponential release, so the envelope gates in
# _event_analysis can pass and only the gate under study decides.
# ---------------------------------------------------------------------------
def frozen(wave, events, repeat=0):
    """One committed render of the real Mini V3 capture, float64 as analysed,
    looked up by the condition whose event timeline `events` is."""
    name = next(n for n, e in probe.CONDITIONS.items() if e == tuple(events))
    return _render(wave, name, repeat)


@functools.lru_cache(maxsize=None)
def _render(wave, name, repeat):
    sr, audio = wavfile.read(CAPTURE / f"{wave}-{name}-{repeat}.wav")
    if sr != SR:
        raise RuntimeError(f"capture {wave}-{name}-{repeat} is at {sr} Hz, not {SR}")
    return np.asarray(audio, dtype=np.float64)


def _with_sample(value):
    def make(wave, events, repeat=0):
        audio = np.array(frozen(wave, events, repeat))
        audio[int(0.3 * SR)] = value
        return audio
    return make


def _partials(wave, hz, n, keep=lambda k: True):
    """Band-limited saw or square of unit peak from its Fourier series; `keep`
    selects which partials survive. A saw is the SINE series: the cosine series
    is symmetric about t=0, a spike with two edges, not a ramp."""
    t = np.arange(n) / SR
    y = np.zeros(n)
    for k in range(1, int(0.5 * SR / hz) + 1):
        if keep(k):
            a = 1.0 / k if wave == "saw" else (1.0 if k % 2 else 0.0) / k
            phase = 2 * np.pi * k * hz * t
            y += a * (np.sin(phase) if wave == "saw" else np.cos(phase))
    top = float(np.max(np.abs(y)))
    return y / top if top else y


def _synth(note_fn, gain=0.5):
    """Every event in `events` rendered through `note_fn(wave, hz, n)`, gated,
    and scaled to `gain` times unit peak."""
    def make(wave, events, repeat=0):
        n = int(SEGMENT * SR)
        t = np.arange(n) / SR
        y = np.zeros(n)
        for event in events:
            a, b = int(event["on_s"] * SR), int((event["on_s"] + event["gate_s"]) * SR)
            attack = int(0.002 * SR)
            env = np.zeros(n)
            env[a:a + attack] = np.linspace(0.0, 1.0, attack, endpoint=False)
            env[a + attack:b] = 1.0
            env[b:] = np.exp(-(t[b:] - t[b]) / 0.004)
            y += note_fn(wave, ref.vf.note_hz(event["note"]), n) * env
        top = float(np.max(np.abs(y)))
        return gain * (y / top if top else y)
    return make


def _cents(c):
    return lambda w, hz, n: _partials(w, hz * 2 ** (c / 1200), n)


@functools.lru_cache(maxsize=None)
def _noise(n):
    return np.random.default_rng(1905).standard_normal(n) * 0.3


def _sine_at(k_over, db):
    def tone(w, hz, n):
        sub = 10 ** (db / 20) * np.cos(2 * np.pi * k_over * hz * np.arange(n) / SR)
        return _partials(w, hz, n) + sub
    return tone


FIXTURES = {
    "frozen_capture": {
        "why": "the real Mini V3 renders: the known-good control, must exit 0",
        "make": frozen},
    "silence": {
        "why": "digital silence: is_silent in refine_f0, and the render gate's 1e-5 floor",
        "make": lambda wave, events, repeat=0: np.zeros(int(SEGMENT * SR))},
    "tone_peak_1e-10": {
        "why": "saw at pitch, peak under is_silent's 1e-9 floor",
        "make": _synth(_partials, gain=1e-10)},
    "tone_peak_1e-6": {
        "why": "saw at pitch, above is_silent but under the render gate's 1e-5: "
               "does amplitude reach any raise once the gate is gone?",
        "make": _synth(_partials, gain=1e-6)},
    "sine_at_pitch": {
        "why": "every upper partial dropped: the waveform classifier must refuse",
        "make": _synth(lambda w, hz, n: _partials(w, hz, n, keep=lambda k: k == 1))},
    "saw_minus_h3": {
        "why": "saw at pitch with its third partial dropped: harmonic_signature still "
               "runs, so the stop must come from the spectrum check",
        "make": _synth(lambda w, hz, n: _partials(w, hz, n, keep=lambda k: k != 3))},
    "saw_100c_flat": {
        "why": "wrong pitch, a semitone under the commanded note",
        "make": _synth(_cents(-100))},
    "saw_octave_down": {
        "why": "wrong pitch, an octave under: refine_f0's subharmonic check",
        "make": _synth(_cents(-1200))},
    "saw_octave_up": {
        "why": "wrong pitch, an octave over: nothing in refine_f0's search band",
        "make": _synth(_cents(1200))},
    "white_noise": {
        "why": "no line anywhere: no fundamental to lock onto",
        "make": _synth(lambda w, hz, n: _noise(n))},
    "dc_only": {
        "why": "a constant: not silent, and no line in the band",
        "make": lambda wave, events, repeat=0: np.full(int(SEGMENT * SR), 0.1)},
    "saw_unit_peak": {
        "why": "a perfect saw at full scale: only the render gate's clip check stops it",
        "make": _synth(_partials, gain=1.0)},
    "ideal_saw_0p5": {
        "why": "a closed-form band-limited saw at pitch, 0.5 peak: passes refine_f0, "
               "harmonic_signature and the classifier; the foldback estimator judges it",
        "make": _synth(_partials)},
    "nan_one_sample": {
        "why": "the real capture with one sample NaN, inside the first note: the route to "
               "NonFiniteAudio, an InsufficientEvidence subclass, if anything lets it past",
        "make": _with_sample(np.nan)},
    "inf_one_sample": {
        "why": "the real capture with one sample +Inf, inside the first note",
        "make": _with_sample(np.inf)},
    "short_1s": {
        "why": "the real capture cut to one second: too little audio to hold an event",
        "make": lambda wave, events, repeat=0: frozen(wave, events, repeat)[:SR]},
    "empty": {
        "why": "no audio at all",
        "make": lambda wave, events, repeat=0: np.zeros(0)},
    "tremolo_5hz": {
        "why": "amplitude modulated at 5 Hz: the record does not repeat period to period",
        "make": _synth(lambda w, hz, n: _partials(w, hz, n)
                       * (0.75 + 0.25 * np.cos(2 * np.pi * 5 * np.arange(n) / SR)))},
    "detuned_pair_2c": {
        "why": "two saws 2 cents apart, the unison case cycle_average exists for",
        "make": _synth(lambda w, hz, n: 0.5 * (_partials(w, hz * 2 ** (1 / 1200), n)
                                                + _partials(w, hz * 2 ** (-1 / 1200), n)))},
    "naive_saw": {
        "why": "a non-band-limited ramp: it does not repeat sample to sample",
        "make": _synth(lambda w, hz, n: 2 * ((np.arange(n) * hz / SR) % 1.0) - 1)},
    "subharmonic_m30": {
        "why": "a component at f0/3, 30 dB down, under refine_f0's -20 dB sub_floor: "
               "the hardest input for the period and spectrum checks that still passes",
        "make": _synth(_sine_at(1 / 3, -30))},
}


# ---------------------------------------------------------------------------
# the playback rig: a plugin that reports what it was asked and plays a fixture
# ---------------------------------------------------------------------------
class _PlaybackRig:
    _NAMES = ({index: name for index, name, _ in ref.PATCH.values()}
              | {ref.WAVE_PARAM[0]: ref.WAVE_PARAM[1]})
    _WAVE_TEXT = {value: text for value, text in ref.WAVE_SETTINGS.values()}

    def __init__(self, source, block=ref.BLOCK):
        self.source = source
        self.I = {k: 900 + i for i, k in enumerate(
            ("lvl_o2", "lvl_o3", "lvl_noise", "o2", "o3", "noise_sw", "ext_sw", "lvl_ext"))}
        self.wave_value, self.events, self.text, self.repeats, self.served = None, [], {}, {}, 0
        self.pb = mock.Mock()
        self.eng, self.p = _Engine(self), _Plugin(self)

    def set(self, index, value):
        self.text[index] = (ref.RANGE_READBACK if index == ref.PATCH["osc1_range"][0]
                            else self._WAVE_TEXT.get(value) or repr(float(value)))
        if index == ref.WAVE_PARAM[0]:
            self.wave_value = value

    def check_pins(self):
        return []


class _Plugin:
    def __init__(self, rig):
        self.rig = rig

    def clear_midi(self):
        self.rig.events = []

    def add_midi_note(self, note, velocity, on_s, gate_s):
        self.rig.events.append({"note": note, "on_s": on_s, "gate_s": gate_s})

    def get_latency_samples(self):
        return 0

    def get_parameter_name(self, index):
        return self.rig._NAMES[index]

    def get_parameter_text(self, index):
        return self.rig.text[index]


class _Engine:
    def __init__(self, rig):
        self.rig = rig

    def render(self, seconds):
        self.seconds = seconds

    def get_audio(self):
        wave = next(w for w, (value, _) in ref.WAVE_SETTINGS.items()
                    if value == self.rig.wave_value)
        events = tuple(self.rig.events)
        key = (wave, tuple((e["note"], e["on_s"], e["gate_s"]) for e in events))
        repeat = self.rig.repeats.get(key, 0)
        self.rig.repeats[key] = repeat + 1
        self.rig.served += 1
        return [self.rig.source(wave, events, repeat)]


# ---------------------------------------------------------------------------
# the three levels
# ---------------------------------------------------------------------------
def _describe(error):
    """The audio_measure chain that raised, innermost last, and what the
    exception is under."""
    frames = traceback.extract_tb(error.__traceback__)
    chain = [f"{f.name}:{f.lineno}" for f in frames if f.filename == am.__file__]
    where = "audio_measure." + " > ".join(chain) if chain else "outside audio_measure"
    family = [c.__name__ for c in type(error).__mro__
              if c.__name__ in ("InsufficientEvidence", "AssertionError", "Refused", "ValueError")]
    return (f"RAISE {type(error).__name__}[{' < '.join(family) or 'no refusal family'}] at {where}"
            f" -- {str(error)[:110]}")


def drive_estimator(make, wave, condition, index):
    """`rv.measure` on exactly the window `_event_analysis` cuts for this event."""
    events = probe.CONDITIONS[condition]
    event = events[index]
    audio = make(wave, events, 0)
    a, b = int((event["on_s"] + 0.12) * SR), int((event["on_s"] + event["gate_s"] - 0.08) * SR)
    steady = audio[a:b]
    command = ref.vf.note_hz(event["note"])
    row = {"window_samples": int(len(steady)), "command_hz": command}
    try:
        measured = rv.measure(steady, command, "#1905 trace", "saw" if wave == "saw" else "square")
    except Exception as error:
        row["rv_measure"] = _describe(error)
        return row
    if measured["f0"] is None:
        row["rv_measure"] = f"refine_f0 stop ({measured['wave_reason'][:100]})"
        return row
    row["f0"] = measured["f0"]
    row["fundamental_periods"] = round(len(steady) * measured["f0"] / SR, 1)
    row["fundamental_ok"] = am.windowed_tone_amplitude(steady, measured["f0"], SR).ok
    if not measured["steady"]:
        verdict = f"unqualified ({measured['wave_reason'][:100]})"
    elif measured["verified"]:
        verdict = f"label={measured['wave_label']} verified=True"
    else:
        verdict = (f"steady, label={measured['wave_label']}, not the requested wave "
                   f"({measured['verify_why'][:100]})")
    row["rv_measure"] = f"refine_f0 ok, {verdict}"
    try:
        inharmonic = am.inharmonic_fraction_db(steady, measured["f0"], SR)
    except Exception as error:
        row["inharmonic_fraction_db"] = _describe(error)
    else:
        row["inharmonic_fraction_db"] = ("ok" if inharmonic.ok
                                         else f"refuse ({inharmonic.reason[:60]})")
    return row


def drive_module(source, *, bypass_render_gate=False, inject=None):
    """The real `main` on `source`. `inject` replaces `am.harmonic_signature`."""
    sites = []

    class Traced(ref.Refused):
        def __init__(self, *args, **kwargs):
            frame = sys._getframe(1)
            sites.append(f"{pathlib.Path(frame.f_code.co_filename).name}:{frame.f_lineno} "
                         f"{frame.f_code.co_name} -- {str(args[0]) if args else ''}")
            super().__init__(*args, **kwargs)

    not_read = {"fixture": "not read in the #1905 trace"}
    with tempfile.TemporaryDirectory(prefix="attack-context-trace-") as out, \
            contextlib.ExitStack() as stack:
        stack.enter_context(mock.patch.object(ref.rr, "MiniV3Rig",
                                              lambda block=ref.BLOCK: _PlaybackRig(source)))
        stack.enter_context(mock.patch.object(ref, "_plugin_metadata", lambda: not_read))
        integrity = mock.patch.object(ref, "_qualify_reference_integrity", lambda: not_read)
        stack.enter_context(integrity)
        stack.enter_context(mock.patch.object(subprocess, "check_output", return_value=""))
        stack.enter_context(mock.patch.object(ref, "_source_provenance",
                                              lambda: {"commit": "fixture", "files_sha256": {}}))
        stack.enter_context(mock.patch.object(ref, "Refused", Traced))
        if bypass_render_gate:
            def bypass(wave, decay, events, seconds, block=ref.BLOCK):
                audio = np.asarray(source(wave, events[wave], 0), dtype=np.float64)
                return audio, {"fixture": "render gates bypassed"}
            stack.enter_context(mock.patch.object(ref, "_render_segment", bypass))
        if inject is not None:
            stack.enter_context(mock.patch.object(am, "harmonic_signature", inject))
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            try:
                code, error = probe.main(["--out", out]), None
            except Exception as caught:
                code, error = 1, caught
        refused = pathlib.Path(out) / "refused.json"
        reason = json.loads(refused.read_text())["reason"] if refused.is_file() else None
    return {"exit": code, "refused_at": sites[0] if sites else None, "reason": reason,
            "raised": _describe(error) if error else None,
            "is_insufficient_evidence": bool(error and isinstance(error, am.InsufficientEvidence))}


def _injected():
    def refuse(*args, **kwargs):
        raise am.InsufficientEvidence("harmonic_signature: injected #1905 control")
    return refuse


def _require_capture():
    missing = [f"{wave}-{condition}-{repeat}.wav" for wave in ref.WAVE_SETTINGS
               for condition in probe.CONDITIONS for repeat in range(3)
               if not (CAPTURE / f"{wave}-{condition}-{repeat}.wav").is_file()]
    if missing:
        return f"the frozen capture is incomplete at {CAPTURE}: {len(missing)} renders missing"
    return None


def trace(fixtures=None):
    """Every fixture at every level, plus both controls. `REFUSED` when the
    known-good control does not exit 0; `NO-VERDICT` when the injected one is
    not seen; otherwise REACHABLE, UNREACHABLE_BEHIND_RENDER_GATE or
    UNREACHABLE."""
    refused = _require_capture()
    if refused:
        return {"state": "REFUSED", "reason": refused}
    rows = []
    for name, fixture in (fixtures or FIXTURES).items():
        make = fixture["make"]
        rows.append({
            "fixture": name, "why": fixture["why"],
            "estimator": {f"{wave}/{condition}": drive_estimator(make, wave, condition, index)
                          for condition, index in WINDOWS for wave in ref.WAVE_SETTINGS},
            "module": drive_module(make),
            "module_render_gate_bypassed": drive_module(make, bypass_render_gate=True)})
    good = next((r for r in rows if r["fixture"] == "frozen_capture"), None)
    if good and good["module"]["exit"] != 0:
        return {"state": "REFUSED", "rows": rows,
                "reason": f"the known-good control did not exit 0: {good['module']}"}
    control = drive_module(FIXTURES["frozen_capture"]["make"], inject=_injected())
    if not (control["exit"] == 1 and control["is_insufficient_evidence"]):
        return {"state": "NO-VERDICT", "rows": rows, "control": control,
                "reason": "the injected estimator refusal did not exit 1; this probe cannot "
                          "see the failure mode it traces, so its table is not evidence"}
    escaped = [r["fixture"] for r in rows if r["module"]["exit"] == 1]
    leaked = [r["fixture"] for r in rows if r["module_render_gate_bypassed"]["exit"] == 1]
    if escaped:
        state = "REACHABLE"
    elif leaked:
        state = "UNREACHABLE_BEHIND_RENDER_GATE"
    else:
        state = "UNREACHABLE"
    return {"state": state, "rows": rows, "control": control,
            "escapes_through_shipped_gates": escaped,
            "escapes_only_when_render_gate_bypassed": leaked}


def _short(text, width):
    text = str(text)
    return text if len(text) <= width else text[:width - 3] + "..."


def _module_line(cell):
    if cell["exit"] == 0:
        return "exit 0, report written"
    if cell["exit"] != 1 and not cell["refused_at"]:
        leg = _short(cell["reason"], 90)
        return f"exit 2 through the handler's ValueError/OSError leg, no Refused raised: {leg}"
    return f"exit {cell['exit']}  {_short(cell['refused_at'] or cell['raised'], 150)}"


def format_trace(result):
    lines = []
    for row in result.get("rows", []):
        lines.append(f"[{row['fixture']}]  {row['why']}")
        for key, cell in row["estimator"].items():
            extra = ""
            if "inharmonic_fraction_db" in cell:
                extra = f"; inharmonic_fraction_db {cell['inharmonic_fraction_db']}"
            lines.append(f"    estimator {key:<24s} {cell['rv_measure']}{extra}")
        lines.append(f"    module, real gates            {_module_line(row['module'])}")
        bypassed = row["module_render_gate_bypassed"]
        lines.append(f"    module, render gate bypassed  {_module_line(bypassed)}")
    if "control" in result:
        control = result["control"]
        where = _short(control["refused_at"] or control["raised"], 170)
        lines.append("[control: harmonic_signature made to raise on the real capture]  "
                     f"exit {control['exit']}  {where}")
    lines.append(f"VERDICT: {result['state']}")
    if "reason" in result:
        lines.append(f"    {result['reason']}")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true", help="the full record as JSON")
    ap.add_argument("--only", nargs="*", help="run just these fixtures (both controls still run)")
    args = ap.parse_args(argv)
    chosen = {name: FIXTURES[name] for name in args.only} if args.only else None
    result = trace(chosen)
    if args.json:
        print(json.dumps(result, indent=1, default=str))
    else:
        print(format_trace(result))
        print()
        print("Apparatus: the git-clean and plugin preconditions of main are bypassed; Mini V3")
        print("is a playback rig. Every estimator and gate from _render_segment inward is shipped.")
    return {"REFUSED": exit_contract.REFUSED, "NO-VERDICT": exit_contract.FAIL,
            "REACHABLE": exit_contract.FAIL}.get(result["state"], exit_contract.PASS)


if __name__ == "__main__":
    raise SystemExit(main())

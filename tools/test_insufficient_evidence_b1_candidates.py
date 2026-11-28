#!/usr/bin/env python3
"""#1972: the five #1884-shape candidates that `test_insufficient_evidence_dispatch.py`
could only stop at an EARLIER gate, run past it (red-first on origin/main @
e2d1bae7, 2026-11-28, before any production edit).

THE SHAPE. `audio_measure.InsufficientEvidence` subclasses AssertionError and
every module's `Refused` subclasses `refusal.Refused`; the two share nothing
below Exception. A dispatch handler written `except refusal.Refused` around a
path that reaches an estimator therefore lets the estimator's own refusal out
as a traceback and exit 1: a refusal rendered as FAIL, the inversion
`tools/exit_contract.py` exists to prevent. #1763 ran these five against a
digitally silent model render and each stopped before its `am.*` call. That is
a verdict about ONE input. This file is each candidate run on inputs that get
past that gate.

WHAT THE RUNS FOUND: 3 of 5 reachable, 2 not.

  REACHABLE -- the reference's gate, not the model's render
    measure_m5a_pulse_duty.py, measure_m5a_signal_path.py and
    mono_m5a_score.measure (:464). The analysis window is
    `(on + 0.12 s, off - 0.08 s)` of each manifest event. `refine_f0` guards
    its subharmonic check with `a1.ok and sub.ok`, so on a window of fewer
    than 12 periods it still returns ok -- and `harmonic_signature` then calls
    `.require()` on the same refused estimate and raises. Every gate of
    `load_reference` (suite, hash, format, count, finiteness) passes a record
    whose gates are 0.21 s; the committed record's are 0.6 s, so on the
    committed reference the window is 19,200 samples, 419 periods at MIDI 84,
    and the route is 35x away. No CLI argument of either tool reaches it.

  REACHABLE -- a stage with no signal under a live output
    mono_m5a_score._stage_diagnostics (:219) runs `harmonic_signature` on the
    oscillator, mixer and ladder traces with no `refine_f0` and no silence
    check of its own, at the OUTPUT's f0.

  NOT REACHABLE -- every curve is finite by construction
    f1_selected_path.py and holdout_f1d_preconditions.py. `plateau_db` raises
    only when no FINITE point lies in `rc._ref_band(freqs, cut)`, whose lower
    edge is `freqs[0]` -- a point of every curve. Each of the three curve
    constructors ends in `Estimate.require()` or an `.ok` test and then
    `20*log10(max(a, 1e-12)/...)`, so a silent tone is a `Refused` (caught)
    and never a non-finite point. Pinned below on the frozen reference, with
    the input that would defeat the gate -- a non-finite band -- shown to be
    one no constructor emits. No production change.

WHAT WAS RUN AND DID NOT REACH IT
  --drive 0.0, --cutoff 30, --cutoff 21600 --drive 6.15, --disable-polyblep
      on measure_m5a_signal_path.py: each REFUSED through `m5a.Refused`
      (exit 2) or measured (exit 0)
  non-finite audio: `load_reference` refuses it before any estimator
      (pinned in test_mono_m5a_score.py, row "nan")
  f1_selected_path.py: clean, --level-sweep, both REF_PROFILE injects,
      WRONG_LADDER_PATH: exit 0, 0, 2, 2, 1, no InsufficientEvidence
  holdout_f1d_preconditions.py: --cut 0, 30, 500, 20000 and --open-hz 0, 30:
      exit 2, 1, 0, 1, 2, 0, no InsufficientEvidence. (--cut 1e9 / inf / nan,
      --open-hz inf and --res outside its register crash with IndexError,
      OverflowError, ValueError or voice_fx.CalibrationError instead of
      refusing: a different class, not the audio_measure shape; #2021.)

THE REPAIR, and what the rows below hold it to
  the two CLIs        `am.InsufficientEvidence` named beside `Refused` in
                      `main`'s handler: exit 2, the estimator's reason on the
                      REFUSED line, nothing after it
  the library         the three `harmonic_signature` calls go through
                      `_harmonic_signature`, which translates that one class
                      to `mono_m5a_score.Refused` at the call site. That is
                      the class every caller of `measure` is written against
                      (read: `score_m5a_i2s.py:150`, `run_case.py:4270` and
                      the `main` of each of the other callers catches it or
                      `refusal.Refused`; run: `measure_m5a_saw_cutoff.py`,
                      through its real `__main__`)
  pre-fix replay      the three modules as they stood at `PRE_FIX`, read out
                      of git and run under the SAME seams, still escape: the
                      injected-bug control (docs/verification-rules.md rule
                      5). It and a green row red together mean the seam
                      stopped reaching the defect
  plain AssertionError  raised at the same call, from the same seam: still
                      exit 1 with its traceback and no REFUSED line, in the
                      CLIs, in the library, and in the helper itself
  clean runs          the committed reference still measures (exit 0): the
                      widened handlers refuse nothing that used to pass

RED FIRST (origin/main @ e2d1bae7, the three production modules unmodified,
`git diff --quiet` on them before each run): 6 of the 22 rows fail. The three
CLIs return (1, <traceback from audio_measure.py:2391>); the two library rows
report `OUTCOME raised audio_measure.InsufficientEvidence` -- `{'periods':
10.465}` at `measure`'s call, `harmonic_signature: silent` at
`_stage_diagnostics`'; the helper row finds no helper. The other 16 pass on
that tree, as they must: the replays, the AssertionError controls, the clean
runs and the unreachable pins are claims about behaviour the repair must
leave alone.

WRONG-THEN-RIGHT (6, all in this file's apparatus, each caught because a run
disagreed with what reading had predicted):

  1. `--out` under pytest's tmp_path: both M5A CLIs print their report line
     through `path.relative_to(ROOT)` and died there with ValueError, AFTER a
     measurement that had succeeded -- exit 1 for a reason that was not the
     defect. Outputs now go under `build/` inside the repository (`repo_out`).
  2. The first clean rows reached the tools with NONE of their arguments (the
     call dropped `*argv`). Both exited 0 and printed no REFUSED, so every
     assertion but the last passed, while `measure_m5a_pulse_duty.py` rendered
     all four shapes and overwrote the committed record at its DEFAULT output
     path, `docs/scorecard/mono-m5a-miniv3/pulse-duty-v1.json`, rewriting its
     `source_commit` and `source_hashes` from inside a test. Restored from
     HEAD. `tracked_tree_untouched` now compares `git status --porcelain`
     across every row; it was run against this exact defect before it was
     trusted (the row itself passed, the guard raised).
  3. The dead-ladder seam patched `fixed.LadderFx.process` only. The legacy
     constructor runs `reference_rigs._Variant.process`, its own copy of the
     loop, so it stayed alive and "the legacy curve refuses a silent probe"
     DID NOT RAISE. This is #1763's own wrong-then-right 2, repeated from
     reading a seam without its caveat. Both are patched now.
  4. A plain AssertionError out of the library is reported by the child as
     `builtins.AssertionError` (`type(e).__module__`), not `AssertionError`.
  5. A staticmethod replaced under monkeypatch is called with all four
     positional arguments; the stub took `(clip_id, profile)` and the row
     failed with TypeError before its first assertion.
  6. With the repair in, the caller row asserted that the tool's last line
     STARTS WITH `REFUSED`. That is the two probes' prefix;
     `measure_m5a_saw_cutoff.py` writes `measure_m5a_saw_cutoff: REFUSED --`.
     One tool's convention, generalised -- #1763's own wrong-then-right 1,
     in the neighbouring file. The row now asserts the intent: the last line
     IS the refusal, reason included, whatever prefixes it.

The red-first run was therefore made three times before it was clean: 12 red
(6 the defect, 6 this file), 7 red (6, 1), then 6; and the first green run
came back 21 of 22 on item 6.
"""
from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys

import numpy as np
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "probes"))
sys.path.insert(0, str(ROOT / "model"))

import audio_measure as am                                            # noqa: E402
import exit_contract                                                 # noqa: E402
import refusal                                                       # noqa: E402
import test_insufficient_evidence_dispatch as base                   # noqa: E402 -- the #1763 seams and runner

#: the last revision of main on which none of the three repaired modules
#: names `InsufficientEvidence`: what the replay controls read
PRE_FIX = "e2d1bae7"
#: a gate that leaves 480 samples -- 10.47 periods at MIDI 84 -- in the window
SHORT_GATE_S = 0.21
ESTIMATOR_FAULT = "estimator fault at harmonic_signature"
OUT = "build/test_insufficient_evidence_b1/"

PULSE = "tools/measure_m5a_pulse_duty.py"
SIGNAL = "tools/measure_m5a_signal_path.py"
SCORE = "tools/mono_m5a_score.py"
SAW_CUTOFF = "tools/measure_m5a_saw_cutoff.py"
HOLDOUT = "tools/probes/holdout_f1d_preconditions.py"

#: the estimator's own words when the window is too short for it, and the
#: traceback line as the pre-fix modules leave it
TOO_FEW = ("harmonic_signature: the fundamental: insufficient evidence -- "
           "too few periods for a coherent projection")
ESCAPED = "audio_measure.InsufficientEvidence: harmonic_signature: the fundamental"

LADDER_STAGE_SILENT = (
    "import numpy as np, voice_fx as vf\n"
    "_play = vf.VoiceFx.play\n"
    "def _play_silent_ladder(self, regs, writes, n):\n"
    "    out = _play(self, regs, writes, n)\n"
    '    self.trace["ladder"] = np.zeros_like(np.asarray(self.trace["ladder"]))\n'
    "    return out\n"
    "vf.VoiceFx.play = _play_silent_ladder\n"
    "_v = vf.VoiceFx(oversample_2x=True)\n"
    "_pcm = vf.render_mono_fx([(0.0, 84, 0.3, {})], 0.5, _v)\n"
    f"assert not np.any(_v.trace['ladder']), {base.SEAM_FAILED + ': the ladder stage still has signal'!r}\n"
    f"assert np.any(_pcm), {base.SEAM_FAILED + ': the output is silent too'!r}\n")

#: the control against catching too broadly: a plain AssertionError out of the
#: very call the repair guards
SIGNATURE_FAULT = (
    "import audio_measure as am\n"
    f"_FAULT = {ESTIMATOR_FAULT!r}\n"
    "def _signature(*a, **k):\n"
    "    raise AssertionError(_FAULT)\n"
    "am.harmonic_signature = _signature\n"
    f"assert am.harmonic_signature is _signature, {base.SEAM_FAILED + ': the fault is not installed'!r}\n")


# ---------------------------------------------------------------------------
# the seams
# ---------------------------------------------------------------------------
def _short_gate_record(tmp_path: pathlib.Path) -> pathlib.Path:
    """The committed M5A record with every gate cut to `SHORT_GATE_S`, beside
    the frozen audio (hash-checked as before). Installed through the module's
    own `MANIFEST` override -- the route `test_mono_m5a_score.py` takes to
    exercise other M5A records; no gate of `load_reference` is bypassed."""
    import mono_m5a_score as m5a

    manifest = json.loads(m5a.MANIFEST.read_text())
    assert all(ev["gate_s"] > SHORT_GATE_S for seg in manifest["timeline"]["segments"]
               for ev in seg["midi_events"]), "the committed record already has short gates"
    for seg in manifest["timeline"]["segments"]:
        for ev in seg["midi_events"]:
            ev["gate_s"] = SHORT_GATE_S
    audio = tmp_path / manifest["audio"]["file"]
    if not audio.exists():
        try:
            audio.symlink_to(m5a.MANIFEST.parent / manifest["audio"]["file"])
        except OSError:
            shutil.copyfile(m5a.MANIFEST.parent / manifest["audio"]["file"], audio)
    path = tmp_path / "short-gate-manifest.json"
    path.write_text(json.dumps(manifest))
    return path


def _short_gate_on(module: str, record: pathlib.Path) -> str:
    """Child-interpreter source, for a module already bound to `module`:
    point its `MANIFEST` at `record`, then prove the loader returned it and
    that the tools' own window arithmetic now yields fewer than 12 periods at
    the first event."""
    return (
        "import pathlib\n"
        f"{module}.MANIFEST = pathlib.Path({str(record)!r})\n"
        f"_ref = {module}.load_reference('M5A')\n"
        "_ev = _ref.manifest['timeline']['segments'][0]['midi_events'][0]\n"
        f"assert _ev['gate_s'] == {SHORT_GATE_S!r}, "
        f"{base.SEAM_FAILED + ': the loader did not return the short-gate record'!r}\n"
        "import voice_fx as vf\n"
        f"_n = int((_ev['on_s'] + _ev['gate_s'] - 0.08) * {module}.SR) - int((_ev['on_s'] + 0.12) * {module}.SR)\n"
        f"_periods = _n * vf.note_hz(_ev['note']) / {module}.SR\n"
        f"assert _periods < 12.0, "
        f"{base.SEAM_FAILED + ': the window is still %d samples, %.2f periods'!r} % (_n, _periods)\n")


def _seam(kind: str, tmp_path: pathlib.Path) -> str:
    if kind == "ladder_stage_silent":
        return LADDER_STAGE_SILENT
    assert kind == "short_gate", kind
    return "import mono_m5a_score as m5a\n" + _short_gate_on("m5a", _short_gate_record(tmp_path))


# ---------------------------------------------------------------------------
# the runners: the working tree's module, or a source read back out of git
# under the module's own path so its ROOT resolves
# ---------------------------------------------------------------------------
def _run(argv: tuple, tmp_path: pathlib.Path, text: str = "") -> subprocess.CompletedProcess:
    p = subprocess.run([a.replace("@TMP@", str(tmp_path)) for a in argv], input=text,
                       capture_output=True, text=True, timeout=600, cwd=str(ROOT))
    out = p.stdout + p.stderr
    assert base.SEAM_FAILED not in out, out[-600:]
    return p


def _pre_fix(rel: str) -> str:
    """A repaired module as it stood before the repair. A `git show` that
    cannot run is NO VERDICT, which is red -- never a skip that reads like a
    control that ran."""
    r = subprocess.run(["git", "-C", str(ROOT), "show", f"{PRE_FIX}:{rel}"],
                       capture_output=True, text=True, timeout=60, check=False)
    assert r.returncode == 0, (
        f"NO VERDICT: git show {PRE_FIX}:{rel} failed in this clone "
        f"({r.stderr.strip()[:160]}); the pre-fix module cannot be read back")
    assert "InsufficientEvidence" not in r.stdout, (
        f"{PRE_FIX}:{rel} names InsufficientEvidence: it is not the pre-fix module "
        "this control exists to replay")
    return r.stdout


def _exec_under(path: str, seam: str) -> str:
    return (f"import sys\nsys.argv[0] = {path!r}\n{base._CHILD_PATHS}\n{seam}"
            f"exec(compile(sys.stdin.read(), {path!r}, 'exec'),\n"
            f"     {{'__name__': '__main__', '__file__': {path!r}}})\n")


def _cli(rel: str, seam: str, *argv: str, source: str | None = None) -> tuple:
    if source is None:
        return base._handoff(rel, seam, *argv)
    return sys.executable, "-c", _exec_under(str(ROOT / rel), seam), *argv


_MEASURE = "m5a.measure(keep_audio=False)\nprint('OUTCOME returned')"


def _library(seam: str, source: str | None = None) -> tuple:
    """The library's public surface in a child: the working tree's module, or
    the pre-fix source exec'd into a module bound to the same name, so the
    seam installs the record on the module under test."""
    if source is None:
        return base._call("import mono_m5a_score as m5a\n" + _MEASURE, seam)
    path = str(ROOT / SCORE)
    body = (f"import sys, types\n{base._CHILD_PATHS}\n"
            f"m5a = types.ModuleType('mono_m5a_score')\nm5a.__file__ = {path!r}\n"
            "exec(compile(sys.stdin.read(), m5a.__file__, 'exec'), m5a.__dict__)\n"
            f"{seam}try:\n" + "".join(f"    {line}\n" for line in _MEASURE.splitlines())
            + "except BaseException as e:\n"
            "    print('OUTCOME raised', type(e).__module__ + '.' + type(e).__name__, '|', e)\n")
    return sys.executable, "-c", body


@pytest.fixture
def repo_out():
    """An output directory inside the repository: both M5A CLIs print
    `path.relative_to(ROOT)` after writing, so an output under pytest's
    tmp_path crashes them at the report line of a run that measured."""
    d = ROOT / OUT
    d.mkdir(parents=True, exist_ok=True)
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _tree() -> str:
    return subprocess.run(["git", "--no-optional-locks", "-C", str(ROOT), "status",
                           "--porcelain", "--untracked-files=all"],
                          capture_output=True, text=True, timeout=120, check=True).stdout


@pytest.fixture(autouse=True)
def tracked_tree_untouched():
    """Every row runs a tool that writes a report. None may change a tracked
    file: a row that reaches a tool with the wrong arguments can exit 0, print
    no REFUSED and still overwrite a committed record at the tool's DEFAULT
    output path, which is what the first draft of the clean rows did."""
    before = _tree()
    yield
    after = _tree()
    assert after == before, "a row changed the working tree:\n" + "\n".join(
        sorted(set(after.splitlines()) ^ set(before.splitlines())))


# ---------------------------------------------------------------------------
# REACHABLE, repaired: the estimator's refusal is REFUSED, with its reason
# ---------------------------------------------------------------------------
SHORT_GATE_CLI = (
    ("measure_m5a_pulse_duty", PULSE, ("--out", OUT + "pulse-duty.json"),
     f"REFUSED: {TOO_FEW}"),
    ("measure_m5a_signal_path", SIGNAL, ("--cutoff", "14073", "--drive", "1.0",
                                         "--out", OUT + "signal-path.json"),
     f"REFUSED: {TOO_FEW}"),
    ("measure_m5a_saw_cutoff-caller", SAW_CUTOFF, ("--out", OUT + "saw-cutoff.json"),
     f"measure_m5a_saw_cutoff: REFUSED -- saw MIDI 84 model harmonic_signature refused: {TOO_FEW}"),
)


@pytest.mark.parametrize(("name", "script", "argv", "line"), SHORT_GATE_CLI,
                         ids=[row[0] for row in SHORT_GATE_CLI])
def test_a_short_gate_reference_is_refused_with_the_estimators_reason(name, script, argv, line,
                                                                      tmp_path, repo_out):
    """Every CLI that reaches `harmonic_signature` on a short window -- the two
    that call it directly, and one caller of the library -- exits
    `exit_contract.REFUSED`, ends with the estimator's own reason on its own
    refusal line (each tool prefixes it its own way), and prints nothing
    after it."""
    p = _run(_cli(script, _seam("short_gate", tmp_path), *argv), tmp_path)
    out = p.stdout + p.stderr
    assert p.returncode == exit_contract.REFUSED, (p.returncode, out[-600:])
    assert line in out, out[-600:]
    assert "Traceback" not in out and "audio_measure.InsufficientEvidence" not in out, out[-600:]
    last = p.stdout.splitlines()[-1]
    assert last.startswith(line), "nothing may follow the refusal: " + p.stdout[-400:]


LIBRARY = (
    ("measure-short-gate", "short_gate",
     "OUTCOME raised mono_m5a_score.Refused | saw MIDI 84 model harmonic_signature refused: " + TOO_FEW),
    ("stage-diagnostics-silent-stage", "ladder_stage_silent",
     "OUTCOME raised mono_m5a_score.Refused | ladder stage harmonic_signature refused: "
     "harmonic_signature: silent"),
)


@pytest.mark.parametrize(("name", "kind", "outcome"), LIBRARY, ids=[row[0] for row in LIBRARY])
def test_the_library_hands_its_callers_a_refused_of_its_own(name, kind, outcome, tmp_path):
    """Both raising sites in `mono_m5a_score` -- `measure`'s signature and
    `_stage_diagnostics`' -- leave as `mono_m5a_score.Refused`, which is a
    `refusal.Refused`: the class every caller's handler is written against."""
    import mono_m5a_score as m5a

    assert issubclass(m5a.Refused, refusal.Refused)
    assert not issubclass(m5a.am.InsufficientEvidence, refusal.Refused)
    p = _run(_library(_seam(kind, tmp_path)), tmp_path)
    assert outcome in p.stdout, p.stdout[-600:]
    assert p.returncode == exit_contract.PASS and "OUTCOME returned" not in p.stdout, p.stdout[-600:]


def test_the_translation_helper_names_only_the_estimators_class(monkeypatch):
    """The helper behind the three call sites, with the estimator replaced: its
    class (and `NonFiniteAudio`, a subclass) becomes `Refused` and carries the
    reason; a plain AssertionError is not that class and passes through as
    itself. A helper written `except AssertionError` would fail the second
    half (tools/refusal.py's "inverse defect")."""
    import mono_m5a_score as m5a

    def raising(exc):
        def _signature(*args, **kwargs):
            raise exc
        return _signature

    monkeypatch.setattr(m5a.am, "harmonic_signature", raising(m5a.am.InsufficientEvidence("silent")))
    with pytest.raises(m5a.Refused, match="^saw MIDI 84 model harmonic_signature refused: silent$"):
        m5a._harmonic_signature(np.zeros(4800), 440.0, context="saw MIDI 84 model")
    monkeypatch.setattr(m5a.am, "harmonic_signature",
                        raising(m5a.am.NonFiniteAudio("the signal: non-finite")))
    with pytest.raises(m5a.Refused, match="non-finite"):
        m5a._harmonic_signature(np.zeros(4800), 440.0, context="x")
    fault = AssertionError(ESTIMATOR_FAULT)
    assert type(fault) is AssertionError and not isinstance(fault, m5a.am.InsufficientEvidence)
    monkeypatch.setattr(m5a.am, "harmonic_signature", raising(fault))
    with pytest.raises(AssertionError) as caught:
        m5a._harmonic_signature(np.zeros(4800), 440.0, context="x")
    assert caught.value is fault
    monkeypatch.setattr(m5a.am, "harmonic_signature",
                        lambda x, sr, *, f0, kmax: {"f0": f0, "h1": 0.0, "n_valid": 0})
    assert m5a._harmonic_signature(np.zeros(4800), 440.0, context="x")["f0"] == 440.0


# ---------------------------------------------------------------------------
# the injected-bug control: the pre-fix modules under the same seams
# ---------------------------------------------------------------------------
PRE_FIX_CLI = (
    ("measure_m5a_pulse_duty", PULSE, ("--out", OUT + "pulse-duty.json")),
    ("measure_m5a_signal_path", SIGNAL, ("--cutoff", "14073", "--drive", "1.0",
                                         "--out", OUT + "signal-path.json")),
)


@pytest.mark.parametrize(("name", "script", "argv"), PRE_FIX_CLI, ids=[row[0] for row in PRE_FIX_CLI])
def test_the_pre_fix_cli_under_the_same_seam_still_escapes_exit_1(name, script, argv, tmp_path):
    """With the handler as it stood, the seam the green row runs must still
    produce the defect: exit 1, the estimator's class in the traceback, no
    REFUSED line. Red here and green above is the repair; red in both means
    the seam stopped reaching it."""
    source = _pre_fix(script)
    p = _run(_cli(script, _seam("short_gate", tmp_path), *argv, source=source), tmp_path, text=source)
    assert p.returncode == exit_contract.FAIL, (p.returncode, (p.stdout + p.stderr)[-600:])
    assert "Traceback" in p.stderr and ESCAPED in p.stderr, p.stderr[-600:]
    assert TOO_FEW in p.stderr, p.stderr[-600:]
    assert not any(line.startswith("REFUSED") for line in p.stdout.splitlines()), p.stdout[-400:]


#: as the library's pre-fix `measure` lets it out (the child reports class | message)
ESCAPED_LIBRARY = "OUTCOME raised audio_measure.InsufficientEvidence | harmonic_signature: the fundamental"
PRE_FIX_LIBRARY = (
    ("measure-short-gate", "short_gate", ESCAPED_LIBRARY),
    ("stage-diagnostics-silent-stage", "ladder_stage_silent",
     "OUTCOME raised audio_measure.InsufficientEvidence | harmonic_signature: silent"),
)


@pytest.mark.parametrize(("name", "kind", "escaped"), PRE_FIX_LIBRARY, ids=[row[0] for row in PRE_FIX_LIBRARY])
def test_the_pre_fix_library_under_the_same_seam_still_raises_the_estimators_class(name, kind, escaped,
                                                                                   tmp_path):
    source = _pre_fix(SCORE)
    seam = (LADDER_STAGE_SILENT if kind == "ladder_stage_silent"
            else _short_gate_on("m5a", _short_gate_record(tmp_path)))
    p = _run(_library(seam, source=source), tmp_path, text=source)
    assert escaped in p.stdout, p.stdout[-600:]
    assert "OUTCOME returned" not in p.stdout and "mono_m5a_score.Refused" not in p.stdout, p.stdout[-600:]


# ---------------------------------------------------------------------------
# the control against catching too broadly
# ---------------------------------------------------------------------------
FAULT_CLI = (
    ("measure_m5a_pulse_duty", PULSE, ("--out", OUT + "pulse-duty.json")),
    ("measure_m5a_signal_path", SIGNAL, ("--cutoff", "14073", "--drive", "1.0",
                                         "--out", OUT + "signal-path.json")),
    ("measure_m5a_saw_cutoff-caller", SAW_CUTOFF, ("--out", OUT + "saw-cutoff.json")),
)


@pytest.mark.parametrize(("name", "script", "argv"), FAULT_CLI, ids=[row[0] for row in FAULT_CLI])
def test_a_plain_assertion_error_at_the_same_call_is_still_an_execution_error(name, script, argv,
                                                                              tmp_path):
    """An ordinary AssertionError out of `harmonic_signature` -- the call every
    repair guards, reached on the committed reference -- still exits 1, names
    the injected fault in a traceback that shows that call, and prints no
    REFUSED line."""
    p = _run(_cli(script, SIGNATURE_FAULT, *argv), tmp_path)
    out = p.stdout + p.stderr
    assert p.returncode == exit_contract.FAIL, (p.returncode, out[-600:])
    assert "Traceback" in p.stderr and f"AssertionError: {ESTIMATOR_FAULT}" in p.stderr, p.stderr[-600:]
    assert "harmonic_signature(" in p.stderr, "the fault did not leave through the guarded call: " + p.stderr[-600:]
    assert "REFUSED" not in p.stdout, "an AssertionError must not be relabelled a refusal"


def test_a_plain_assertion_error_leaves_the_library_as_itself(tmp_path):
    p = _run(_library(SIGNATURE_FAULT), tmp_path)
    assert f"OUTCOME raised builtins.AssertionError | {ESTIMATOR_FAULT}" in p.stdout, p.stdout[-600:]
    assert "mono_m5a_score.Refused" not in p.stdout and "OUTCOME returned" not in p.stdout, p.stdout[-600:]


# ---------------------------------------------------------------------------
# the successful path: the committed reference still measures
# ---------------------------------------------------------------------------
CLEAN_CLI = (
    ("measure_m5a_pulse_duty", PULSE, ("--shapes", "square", "--out", OUT + "pulse-duty.json"),
     "pulse-duty.json"),
    ("measure_m5a_signal_path", SIGNAL, ("--cutoff", "21600", "--drive", "6.15",
                                         "--out", OUT + "signal-path.json"), "signal-path.json"),
)


@pytest.mark.parametrize(("name", "script", "argv", "written"), CLEAN_CLI, ids=[row[0] for row in CLEAN_CLI])
def test_the_committed_reference_still_measures_and_exits_0(name, script, argv, written, tmp_path, repo_out):
    """No seam. The widened handlers refuse nothing that used to pass: exit 0,
    a report where `--out` named, no REFUSED line, no traceback."""
    p = _run(_cli(script, "", *argv), tmp_path)
    out = p.stdout + p.stderr
    assert p.returncode == exit_contract.PASS, (p.returncode, out[-600:])
    assert "REFUSED" not in out and "Traceback" not in out, out[-600:]
    assert (repo_out / written).is_file(), (sorted(q.name for q in repo_out.iterdir()), out[:400])


# ---------------------------------------------------------------------------
# NOT REACHABLE: the gate is finiteness, and it holds on real data
# ---------------------------------------------------------------------------
def test_the_reference_band_always_starts_on_a_point_of_the_curve():
    """`plateau_db` raises only when no finite point lies in the band. For
    every cutoff a CLI can name -- including ones no register could carry --
    `run_case._ref_band` returns a band whose lower edge is `freqs[0]` and
    whose top is not below it; and the input that does defeat the gate is a
    non-finite band."""
    import run_case as rc

    freqs = np.geomspace(40.0, 12000.0, 32)
    for cut in (0.0, 30.0, 500.0, 20000.0, 1e9, float("inf"), -5.0, float("nan")):
        lo, top = rc._ref_band(freqs, cut)
        assert lo == freqs[0] and top >= lo, (cut, lo, top)

    gain = np.linspace(0.0, -120.0, 32)
    for top in (100.0, 125.0, 1e9, float("inf")):
        assert isinstance(am.plateau_db(freqs, gain, (freqs[0], top)), float)
    non_finite = gain.copy()
    non_finite[:7] = np.nan
    with pytest.raises(am.InsufficientEvidence, match="no measured points in the reference band"):
        am.plateau_db(freqs, non_finite, (freqs[0], 125.0))


def test_each_curve_constructor_returns_finite_points_or_refuses(monkeypatch):
    """The three places an F1 curve comes from, on closed-form input: a live
    tone gives finite points; no tone gives the estimator's refusal, which the
    `run_case` reader translates to its own `Refused`. None can put a
    non-finite point in a curve."""
    import fixed
    import f1_selected_path as fsp
    import reference_rigs as rr
    import refprofile as rp
    import run_case as rc

    amp = 0.25 * rr.FS_Q15
    x, parts = rr.tone_train([440.0, 1000.0], amp, 0.06, 0.20)
    assert np.isfinite(rr.SurgeRig.tone_project(x, parts, amp, "tone")).all()
    with pytest.raises(am.InsufficientEvidence, match="silent"):
        rr.SurgeRig.tone_project(np.zeros_like(x), parts, amp, "tone")

    freqs, cut, res = [440.0, 1000.0], 1000.0, 0.0
    assert np.isfinite(rc.our_filter_curve(freqs, cut, res, 0.25)).all()
    voice = fsp.build_voice("selected")[1]
    selected, _info = fsp.selected_curve(voice, freqs, cut, fsp.DRIVE, 0.25)
    assert np.isfinite(selected).all()

    def _unprojectable(*args, **kwargs):
        raise am.InsufficientEvidence("tone probe 440 Hz: insufficient evidence -- silent")

    monkeypatch.setattr(rp, "load_clip", lambda clip_id, profile=None: (
        x, 48_000, {"freqs_hz": [440.0], "parts": [[0, 4800, 440.0]], "amp": 0.25}))
    monkeypatch.setattr(rr.SurgeRig, "tone_project", _unprojectable)
    with pytest.raises(rc.Refused, match="could not be projected: tone probe 440 Hz"):
        rc.load_filter_reference("surge-type2/lp-cut250-res0.00")

    # both ladders: `reference_rigs._Variant` carries its own copy of the
    # loop, so a seam on `fixed.LadderFx` alone leaves the legacy one alive
    monkeypatch.setattr(fixed.LadderFx, "process",
                        lambda self, x, *a, **k: np.zeros(len(x), dtype=np.int32))
    monkeypatch.setattr(rr._Variant, "process",
                        lambda self, x, *a, **k: np.zeros(len(x), dtype=np.int16))
    with pytest.raises(rc.Refused, match="stepped-tone probe refused"):
        rc.our_filter_curve(freqs, cut, res, 0.25)
    with pytest.raises(fsp.Refused, match="selected-path probe refused at 440 Hz"):
        fsp.selected_curve(voice, freqs, cut, fsp.DRIVE, rp.PROBE_AMP)


def test_on_the_frozen_reference_every_band_the_f1_probe_reads_holds_a_finite_point(tmp_path, monkeypatch):
    """`f1_selected_path.main` for F1A, in-process, on the frozen Surge clips:
    the six curves it builds are finite at every point, and every call in its
    handler that can raise -- the three `plateau_db`, and `read_curve`'s
    `filt_corner` / `filt_rolloff` / `rolloff_531aa8a`, each of which reaches
    `plateau_db` again inside `corner_from_curve` -- return."""
    import f1_selected_path as fsp
    import refprofile as rp
    import run_case as rc

    base._restore_frozen_cache(tmp_path)
    monkeypatch.setattr(rp, "PROFILE_DIR", tmp_path)
    spec = rc.FILTER_CASES["F1A"]
    cut, amp, res = spec["cut_hz"], rp.PROBE_AMP, spec["res_ours"]
    ref_f, ref_g, _ = rc.load_filter_reference(spec["ref_clip"])
    open_f, open_g, _ = rc.load_filter_reference(spec["ref_open_clip"])
    probe = fsp.build_voice("selected")[1]
    curves = {
        "reference": (ref_f, ref_g),
        "legacy": (ref_f, rc.our_filter_curve(ref_f, cut, res, amp)),
        "selected": (ref_f, fsp.selected_curve(probe, ref_f, cut, res, amp)[0]),
        "open_reference": (open_f, open_g),
        "open_legacy": (open_f, rc.our_filter_curve(open_f, spec["open_hz"], res, amp)),
        "open_selected": (open_f, fsp.selected_curve(probe, open_f, spec["open_hz"], res, amp)[0]),
    }
    assert len(curves) == 6
    for name, (f, g) in curves.items():
        assert len(g) == len(f) == 32 and np.isfinite(g).all(), name
    rb = rc._ref_band(open_f, cut)
    plateaus = {k: am.plateau_db(open_f, curves[f"open_{k}"][1], rb) for k in ("reference", "legacy", "selected")}
    assert all(np.isfinite(v) for v in plateaus.values())
    for name in ("reference", "legacy", "selected"):
        f, g = curves[name]
        reads = fsp.read_curve(f, g, cut, plateaus[name])
        assert set(reads) == {"corner_hz", "lowband_db", "rolloff_db_oct",
                              "rolloff_531aa8a_db_oct", "rolloff_band_hz"}, name
    defeated = curves["open_selected"][1].copy()
    defeated[open_f <= rb[1]] = np.nan
    with pytest.raises(am.InsufficientEvidence, match="no measured points in the reference band"):
        am.plateau_db(open_f, defeated, rb)


def test_the_holdout_probes_own_plateau_input_is_finite_by_construction():
    """`holdout_f1d_preconditions.py:100` in-process: the wide-open curve it
    hands `plateau_db`, and the call itself, on the real selected path."""
    import f1_filter_path as fp
    import refprofile as rp
    import run_case as rc
    import reference_compare as rcmp

    freqs = np.asarray([float(f) for f in rcmp.FREQS], dtype=np.float64)
    ours_open, _info = fp.SelectedFilterPath().curve(freqs, 20000.0, 0.0, rp.PROBE_AMP)
    assert np.isfinite(ours_open).all()
    assert isinstance(am.plateau_db(freqs, ours_open, rc._ref_band(freqs, 500.0)), float)


HOLDOUT_EDGE = (
    ("cut-0-silent-probe", ("--cut", "0"), exit_contract.REFUSED,
     "REFUSED  selected-path probe refused at 40 Hz, cutoff 0 Hz: silent"),
    ("cut-20000-estimator-answer", ("--cut", "20000"), exit_contract.FAIL, "NO REFERENCE WAS READ"),
)


@pytest.mark.parametrize(("name", "argv", "code", "last"), HOLDOUT_EDGE, ids=[row[0] for row in HOLDOUT_EDGE])
def test_the_holdout_probe_at_its_boundary_cutoffs_never_reaches_the_raise(name, argv, code, last,
                                                                          tmp_path):
    """No seam. `--cut 0`: the path's own guard refuses the silent probe and
    the probe exits 2. `--cut 20000`: an estimator refuses, which is the
    probe's ANSWER about the setting (exit 1, a REFUSED estimator row).
    Neither is the estimator's class leaving the module."""
    p = _run(base._handoff(HOLDOUT, "", *argv), tmp_path)
    out = p.stdout + p.stderr
    assert p.returncode == code, (p.returncode, out[-600:])
    lines = p.stdout.splitlines()
    assert lines[-1].startswith(last), out[-600:]
    assert "Traceback" not in out and "InsufficientEvidence" not in out, out[-600:]
    if code == exit_contract.FAIL:
        assert any(line[:15].strip() in ("corner", "lowband", "rolloff") and "REFUSED" in line
                   for line in lines), "exit 1 without a refused estimator row: " + p.stdout[-600:]

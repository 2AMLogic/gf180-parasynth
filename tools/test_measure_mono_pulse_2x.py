"""#1907: where `audio_measure.InsufficientEvidence` goes when it leaves
`mono_m5a_score.measure` under this tool's `Refused`-only dispatch.

The #1782 census listed `measure_mono_pulse_2x.py:129` as a candidate: the
handler named only `refusal.Refused`, and `score.measure` reaches
`am.harmonic_signature` unguarded (`mono_m5a_score.py:464` on the output
slice, `:219` in `_stage_diagnostics` on every stage vector). `InsufficientEvidence`
subclasses AssertionError, not `refusal.Refused`, so that handler could not
see it. A census row is a candidate, not a defect; this file is the row RUN,
red-first on main @ 8f3d8ed2 (2026-11-28), through the tool's real
`__main__` (`runpy`, so its argv is a direct invocation's).

THE TRACE, three routes into the estimator, two stopped by a gate that was
already there:

  --rect-gain-q15 1            the CLI's one free input that could scale a
                               stage vector. REFUSED, exit 2, nothing
                               rendered: `factory` refuses an override on a
                               tree that defines `vf._OS2_RECT_GAIN_Q15`
                               (24248, which is also the default), at :93.
                               The estimator is never reached.
  the whole voice silent       REFUSED, exit 2: `refine_f0` refuses the first
                               note's output slice before any stage is read
                               (`mono_m5a_score.py:459`). This is #1763's
                               finding for `mono_m5a_score.measure`, and it
                               is why a silent RENDER is not the route.
  the oscillator STAGE TAP     ESCAPED. `voice.trace["osc"][0]` zeroed after
  silent, output live          every render while the returned audio is left
                               alone: the pitch gate passes on the live
                               output, then `_stage_diagnostics` hands the
                               estimator a vector nothing has checked.

    $ python tools/measure_mono_pulse_2x.py --case M5A --out <fresh>
    (with the stage tap zeroed)
    Traceback (most recent call last):
      ...
      File "tools/measure_mono_pulse_2x.py", line 129, in measure
        rows[mode] = score.measure(
      File "tools/mono_m5a_score.py", line 491, in measure
        _stage_diagnostics(voice.trace, pcm, a, b, eo.value)),
      File "tools/mono_m5a_score.py", line 219, in _stage_diagnostics
        signature = am.harmonic_signature(x, SR, f0=f0, kmax=12)
      File "model/audio_measure.py", line 2379, in harmonic_signature
        raise InsufficientEvidence("harmonic_signature: silent")
    audio_measure.InsufficientEvidence: harmonic_signature: silent
    exit 1

So the handler gap is real in shape and reachable only by injection on this
tree: the census site is exactly where the traceback lands, and a refusal
left as an exit-1 traceback is rendered FAIL by a batch runner -- the
inversion of tools/exit_contract.py (#804). The handler now names the class
beside `Refused`, as #1751's and #1763's do.

The seam is honest about what it is: a mis-tapped or muted stage trace, not a
fault any CLI input produces today. `_voice_patch` puts the active oscillator
at index 0 for both cases, so a live output with a silent osc[0] is one patch
change away rather than present. The two gate pins below are what make
"unreachable by the CLI" a check instead of an argument.
"""
import copy
import json
import subprocess
import sys

import pytest
import exit_contract
import measure_mono_pulse_2x as experiment
import refusal

#: what a seam raises when it has not taken: reported as that, never as a
#: verdict about the tool
SEAM_FAILED = "SEAM FAILED"
RENDER_FAULT = "unrelated oscillator fault"

#: child-interpreter source: the voice's oscillator STAGE TAP
#: (`voice.trace["osc"][0]`, the vector `_stage_diagnostics` calls
#: "oscillator") is zeroed after every render; the audio `_render` returns is
#: left live, so the pitch gate passes and the estimator is reached
OSC_TAP_SILENT = (
    "import numpy as np, voice_fx as vf\n"
    "_render = vf.VoiceFx._render\n"
    "def _render_silent_tap(self, *a, **kw):\n"
    "    out = _render(self, *a, **kw)\n"
    "    self.trace['osc'][0][:] = 0\n"
    "    return out\n"
    "vf.VoiceFx._render = _render_silent_tap\n"
    "_v = vf.VoiceFx()\n"
    "_pcm = vf.render_mono_fx([(0.0, 69, 0.5, {})], 0.5, _v)\n"
    f"assert np.any(_pcm), {SEAM_FAILED + ': the voice no longer renders'!r}\n"
    f"assert not np.any(_v.trace['osc'][0]), "
    f"{SEAM_FAILED + ': the oscillator tap is not silent'!r}\n"
    f"assert np.any(_v.trace['ladder']), "
    f"{SEAM_FAILED + ': the ladder stage went silent too'!r}\n")

#: the same seam, an ordinary AssertionError after the render instead
OSC_TAP_FAULT = (
    "import voice_fx as vf\n"
    f"RENDER_FAULT = {RENDER_FAULT!r}\n"
    "_render = vf.VoiceFx._render\n"
    "def _render_fault(self, *a, **kw):\n"
    "    out = _render(self, *a, **kw)\n"
    "    raise AssertionError(RENDER_FAULT)\n"
    "vf.VoiceFx._render = _render_fault\n"
    "try:\n"
    "    vf.render_mono_fx([(0.0, 69, 0.5, {})], 0.5)\n"
    "except AssertionError:\n"
    "    pass\n"
    "else:\n"
    f"    raise AssertionError({SEAM_FAILED + ': the seam did not raise'!r})\n")

#: #1763's VOICE_SILENT: the whole voice renders digital silence
VOICE_SILENT = (
    "import numpy as np, voice_fx as vf\n"
    "def _play(self, regs, writes, n):\n"
    "    self.trace = dict(osc=[np.zeros(n, dtype=np.int64)],\n"
    "                     mixed=np.zeros(n, dtype=np.int64),\n"
    "                     ladder=np.zeros(n, dtype=np.int64))\n"
    "    return np.zeros(n, dtype=np.int16)\n"
    "vf.VoiceFx.play = _play\n"
    "_pcm = vf.render_mono_fx([(0.0, 69, 0.5, {})], 0.5)\n"
    f"assert not np.any(_pcm), {SEAM_FAILED + ': the voice still renders'!r}\n")


def _handoff(seam: str, *argv: str) -> list:
    """`python tools/measure_mono_pulse_2x.py <argv...>` with `seam` installed
    first. `run_path` runs the tool's real `__main__`, so its own
    `SystemExit(main())` is what the process exits with."""
    script = experiment.ROOT / "tools" / "measure_mono_pulse_2x.py"
    paths = [str(experiment.ROOT / p) for p in ("model", "tools")]
    src = (f"import sys, runpy\nsys.path[:0] = {paths!r}\n{seam}"
           f"runpy.run_path({str(script)!r}, run_name='__main__')\n")
    return [sys.executable, "-c", src, *argv]


def _run(argv: list, tmp_path) -> subprocess.CompletedProcess:
    p = subprocess.run([a.replace("@TMP@", str(tmp_path)) for a in argv],
                       capture_output=True, text=True, timeout=300,
                       cwd=str(experiment.ROOT))
    assert SEAM_FAILED not in p.stderr, p.stderr[-600:]
    return p


def baseline():
    record = json.loads((experiment.ROOT / "docs/scorecard/results/M5B.json").read_text())
    return {"metrics": record["metrics"], "event_diagnostics": record["diagnostics"]["events"],
            "analysis_version": record["analysis_version"],
            "reference_sha256": record["diagnostics"]["reference_sha256"],
            "manifest_sha256": record["diagnostics"]["reference_manifest_sha256"]}


def test_disabled_candidate_cannot_promote_and_invalid_scores_refuse():
    row = baseline()
    assert not experiment.compare(row, copy.deepcopy(row))["accepts_incremental_improvement"]
    bad = copy.deepcopy(row)
    bad["metrics"]["Pitch"]["valid"] = False
    with pytest.raises(experiment.score.Refused, match="invalid metric"):
        experiment.compare(row, bad)
    bad = copy.deepcopy(row)
    bad["analysis_version"] = "unrelated"
    with pytest.raises(experiment.score.Refused, match="analysis_version"):
        experiment.compare(row, bad)


def test_pulse_improvement_cannot_hide_lost_saw_alias_pass():
    row = baseline()
    bad = copy.deepcopy(row)
    bad["metrics"]["Foldback energy"]["error"] -= 1.0
    bad["event_diagnostics"][0]["foldback_db"]["excess_over_reference_db"] = 3.1
    comparison = experiment.compare(row, bad)
    assert not comparison["accepts_incremental_improvement"]
    assert comparison["lost_per_note_passes"][0]["property"] == "Foldback energy"


def test_gain_control_does_not_select_pulse_oversampling():
    control = experiment.factory("gain_only")
    candidate = experiment.factory("pulse2x")
    assert not control.oversample_pulse_2x
    assert candidate.oversample_pulse_2x
    for obj in (control, candidate):
        assert obj.oversample_2x and obj.rate_converted_ladder
        assert obj.pulse479_filter_candidate and obj.causal_filter


def test_gain_only_control_uses_the_candidates_rectangle_gain():
    """#333: the control must apply the level change the candidate makes. The
    model now builds the rectangle headroom itself (voice_fx._OS2_RECT_GAIN_Q15,
    R2's 0.74), so the tool's gain IS the model's, the gain-only control scales
    rectangles by it (not the saw's 27853), and an override the model does not
    build is refused rather than measured."""
    import numpy as np
    assert experiment.RECT_GAIN_Q15 == experiment.vf._OS2_RECT_GAIN_Q15 == 24248
    ctl = experiment.factory("gain_only").note(96, 0.1, waves=("pulse29",) * 3,
                                               mix=(1.0, 0.0, 0.0))
    base = experiment.factory("baseline").note(96, 0.1, waves=("pulse29",) * 3,
                                               mix=(1.0, 0.0, 0.0))
    assert not np.array_equal(ctl, base)              # the control moved the level
    saw_ctl = experiment.factory("gain_only").note(84, 0.1, waves=("saw",) * 3,
                                                   mix=(1.0, 0.0, 0.0))
    saw_base = experiment.factory("baseline").note(84, 0.1, waves=("saw",) * 3,
                                                   mix=(1.0, 0.0, 0.0))
    assert np.array_equal(saw_ctl, saw_base)          # and left the saw alone
    saved = experiment.RECT_GAIN_Q15
    try:
        experiment.set_rect_gain(experiment.vf._OS2_SUBSTEP_GAIN_Q15)
        with pytest.raises(experiment.score.Refused, match="defines its own rectangle gain"):
            experiment.factory("pulse2x")
    finally:
        experiment.set_rect_gain(saved)


# ---------------------------------------------------------------------------
# #1907: the estimator's own refusal at the dispatch
# ---------------------------------------------------------------------------
def test_estimator_refusal_is_an_assertion_error_the_refused_handler_cannot_see():
    """The class relations the fix rests on: the estimator's refusal is an
    AssertionError (so a handler written against that base would also swallow
    every unrelated assert), it is not a `Refused` (so the old handler could
    not see it), and the class the tool names is the class the scorer raises."""
    klass = experiment.am.InsufficientEvidence
    assert issubclass(klass, AssertionError)
    assert not issubclass(klass, refusal.Refused)
    assert klass is experiment.score.am.InsufficientEvidence


def test_silent_oscillator_stage_refuses_through_the_dispatch_not_a_traceback(tmp_path):
    """The census site, reached: a stage vector the estimator cannot measure
    leaves as `REFUSED: <the estimator's reason>` with the module's REFUSED
    code, no traceback, and no report file."""
    out = tmp_path / "stage-tap"
    p = _run(_handoff(OSC_TAP_SILENT, "--case", "M5A", "--out", str(out)), tmp_path)
    text = p.stdout + p.stderr
    assert p.returncode == experiment.REFUSED, (p.returncode, text[-600:])
    assert "REFUSED: harmonic_signature: silent" in p.stdout, text[-600:]
    assert "Traceback" not in text and "InsufficientEvidence" not in text, text[-600:]
    assert not (out / "M5A.json").exists()
    assert p.stdout.splitlines()[-1].startswith("REFUSED"), "nothing may follow the refusal"


def test_plain_assertion_error_at_the_scoring_boundary_is_still_an_execution_error(tmp_path):
    """The control against catching too broadly: an ordinary AssertionError
    from the same seam, inside `score.measure`, still exits 1 with its
    traceback and prints no REFUSED line. Naming `InsufficientEvidence` must
    not widen the handler to every AssertionError (tools/refusal.py's "inverse
    defect")."""
    p = _run(_handoff(OSC_TAP_FAULT, "--case", "M5A", "--out", str(tmp_path / "fault")), tmp_path)
    assert p.returncode == exit_contract.FAIL, (p.returncode, p.stderr[-600:])
    assert "Traceback" in p.stderr and f"AssertionError: {RENDER_FAULT}" in p.stderr, p.stderr[-600:]
    assert "REFUSED" not in p.stdout, "an AssertionError must not be relabelled a refusal"


# ---------------------------------------------------------------------------
# the two gates that stop every other route before the estimator, pinned by
# their own words: what makes "reachable only by injection" a check
# ---------------------------------------------------------------------------
def test_rectangle_gain_override_is_refused_before_any_stage_is_rendered(tmp_path):
    """The CLI's one free input cannot reach the estimator on a tree that
    defines its own rectangle gain: any override but the model's own value
    is refused in `factory`, before the first render. This is the gate that
    refutes the census's candidate path for `--rect-gain-q15`."""
    out = tmp_path / "gain-1"
    p = _run(_handoff("", "--case", "M5A", "--rect-gain-q15", "1", "--out", str(out)), tmp_path)
    text = p.stdout + p.stderr
    assert p.returncode == experiment.REFUSED, (p.returncode, text[-600:])
    assert "REFUSED: this tree defines its own rectangle gain" in p.stdout, text[-600:]
    assert "Traceback" not in text
    assert not list(out.iterdir()), "a refused run must not render"


def test_a_silent_render_is_stopped_by_the_pitch_gate_before_any_stage_is_read(tmp_path):
    """A voice that renders digital silence is already refused, by `refine_f0`
    on the output slice, before `_stage_diagnostics` reads a stage: #1763's
    finding for `mono_m5a_score.measure`, and the reason a silent RENDER is
    not the route the handler gap is reached through."""
    p = _run(_handoff(VOICE_SILENT, "--case", "M5A", "--out", str(tmp_path / "silent")), tmp_path)
    text = p.stdout + p.stderr
    assert p.returncode == experiment.REFUSED, (p.returncode, text[-600:])
    assert "REFUSED: saw MIDI 84 pitch refused: silent" in p.stdout, text[-600:]
    assert "Traceback" not in text and "InsufficientEvidence" not in text, text[-600:]

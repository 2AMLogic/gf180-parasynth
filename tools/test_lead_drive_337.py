import math
import subprocess
import sys

import pytest

import lead_drive_337 as L
import exit_contract
import refusal

BASE = dict(n_cells=20, max_abs=7.5, rms=4.5, mean_signed=-4.0, alias_max=2.0,
            level_mean=-18.0, pitch_abs=0.10)


def cand(**kw):
    return {**BASE, **kw}


GOOD = cand(max_abs=4.5, rms=3.0, mean_signed=-2.5)


def test_start_red_baseline_against_itself_is_ineligible():
    # the stub "candidate = baseline" must NOT pass (rule 2 and others)
    assert L.rule_failures(BASE, dict(BASE))


def test_good_candidate_passes_and_is_chosen():
    assert L.rule_failures(BASE, GOOD) == []
    assert L.choose(BASE, {0.5: (GOOD, True)}) == 0.5


def test_quieter_candidate_cannot_win():
    f = L.rule_failures(BASE, cand(max_abs=4.5, rms=3.0, mean_signed=-2.5, level_mean=-23.0))
    assert any(x.startswith("5") for x in f)


def test_darker_candidate_cannot_win():
    f = L.rule_failures(BASE, cand(max_abs=4.5, rms=3.0, mean_signed=-4.6))
    assert any(x.startswith("4") for x in f)
    # brighter than baseline but darker than the reference is fine (reference = 0)
    assert not any(x.startswith("4") for x in L.rule_failures(BASE, cand(mean_signed=-3.0)))


def test_darkness_guard_when_baseline_is_bright():
    bright = dict(BASE, mean_signed=+1.0)
    # going from +1 to -0.6 passes only if >= min(+1, 0) - 0.5 = -0.5 -> fails
    assert any(x.startswith("4") for x in L.rule_failures(bright, cand(mean_signed=-0.6, max_abs=4, rms=3)))


def test_alias_pitch_and_unreachable_rules():
    assert any(x.startswith("6") for x in L.rule_failures(BASE, cand(max_abs=4, rms=3, alias_max=3.0)))
    assert any(x.startswith("7") for x in L.rule_failures(BASE, cand(max_abs=4, rms=3, pitch_abs=0.2)))
    assert any(x.startswith("1") for x in L.rule_failures(BASE, GOOD, reachable=False))


def test_rms_regression_blocks_a_max_only_win():
    assert any(x.startswith("3") for x in L.rule_failures(BASE, cand(max_abs=4.5, rms=5.0)))


def test_choose_lowest_among_eligible_and_none():
    better = cand(max_abs=3.0, rms=2.0, mean_signed=-1.5)
    assert L.choose(BASE, {0.5: (GOOD, True), 0.25: (better, True)}) == 0.25
    assert L.choose(BASE, {0.5: (GOOD, True), 0.25: (better, False)}) == 0.5
    assert L.choose(BASE, {0.5: (dict(BASE), True)}) is None


def test_compensation_known_answer():
    assert L.compensation_db([-18, -20], [-24, -26]) == pytest.approx(6.0)


def test_vol_effective_and_reach_boundary():
    assert L.vol_effective("pulse", 0.0) == pytest.approx(0.45)
    assert L.vol_effective("saw", 0.0) == pytest.approx(0.45 * 10 ** (-0.45428 / 20))
    assert L.vol_effective("pulse", 6.9) > 0.99
    assert L.vol_effective("pulse", 7.0) > 1.0


def test_summarise_refuses_nan_and_thin_data():
    ev = [dict(errors={"h2": 1.0, "h3": float("nan"), "h4": 1.0, "h5": 1.0},
               gain_model=-18, alias=0, pitch=0)]
    with pytest.raises(L.Refused):
        L.summarise(ev)
    with pytest.raises(L.Refused):
        L.summarise([dict(errors={"h2": 1.0}, gain_model=-18, alias=0, pitch=0)])


def test_summarise_known_answer():
    ev = [dict(errors={"h2": -3.0, "h3": -4.0, "h4": 4.0, "h5": 3.0},
               gain_model=-10, alias=1.5, pitch=-0.2)]
    s = L.summarise(ev)
    assert s["max_abs"] == 4.0 and s["mean_signed"] == 0.0
    assert s["rms"] == pytest.approx(math.sqrt(12.5)) and s["pitch_abs"] == 0.2


# ---------------------------------------------- apparatus-precondition controls
KEYS = ("Harmonic shape", "Foldback energy", "Gain", "Pitch")
RECS = {c: {k: {"value": v} for k, v in zip(KEYS, (7.0, 2.0, -18.0, 0.1))}
        for c in ("M5A", "M5B")}


def fake(sha, **over):
    m = {k: {"value": RECS["M5A"][k]["value"]} for k in KEYS}
    for k, v in over.items():
        m[k.replace("_", " ")] = {"value": v}
    return {"metrics": m, "_sha": sha}


def pre(a=None, b=None, probe=None):
    return L.establish_preconditions(a or fake("A"), b or fake("B"),
                                     probe or fake("P"), load_record=lambda c: RECS[c])


def test_preconditions_positive_leg_passes():
    r = pre()
    assert r["baseline_matches_records"] and r["drive_changes_audio"]
    assert r["baseline_sha"] == "A" and r["drive0.5_sha"] == "P"
    # a deviation inside RECORD_TOL is accepted (the guard is not trivially strict)
    assert pre(a=fake("A", Gain=-18.0 + L.RECORD_TOL / 2))


@pytest.mark.parametrize("which", ["a", "b"])
@pytest.mark.parametrize("key", ["Harmonic_shape", "Foldback_energy", "Gain", "Pitch"])
def test_baseline_mismatch_is_refused_for_every_metric_and_case(which, key):
    bad = fake("A" if which == "a" else "B", **{key: RECS["M5A"][key.replace("_", " ")]["value"] + 0.1})
    with pytest.raises(L.Refused, match="baseline M5[AB] .* != record"):
        pre(**{which: bad})


def test_baseline_mismatch_just_over_tolerance_is_refused():
    with pytest.raises(L.Refused, match="baseline"):
        pre(a=fake("A", Gain=-18.0 + 2 * L.RECORD_TOL))


def test_baseline_nan_is_refused_not_passed():
    # NaN > tol is False: a naive comparison guard would silently accept it
    with pytest.raises(L.Refused):
        pre(a=fake("A", Gain=float("nan")))


def test_unchanged_candidate_audio_is_refused():
    with pytest.raises(L.Refused, match="did not change the audio"):
        pre(probe=fake("A"))


def test_unchanged_audio_refused_even_when_metrics_differ():
    # same bytes, different metrics dict: the SHA is the evidence, not metrics
    with pytest.raises(L.Refused, match="did not change the audio"):
        pre(probe=fake("A", Gain=-30.0))


# ------------------------------------------- #1897: the estimator's own refusal at the dispatch
#
# `audio_measure.InsufficientEvidence` subclasses AssertionError, not
# `refusal.Refused`, so the dispatch's `except Refused` could not see an
# estimator refusing inside `mono_m5a_score.measure`. Traced red-first on
# main @ e2d1bae7 (2026-11-28) through the real `__main__` (`runpy`, so argv
# is a direct invocation's), each fault put below the CLI and above the
# estimator. The clean CLI ran first: exit 0 in 195 s, saw [0.5, CONFIRMED]
# and pulse NOT RUN, its report equal to the committed one.
#
#   a silent RENDER          REFUSED, exit 2, by the pitch gate: `refine_f0`
#                            refuses the first note's output slice
#                            (mono_m5a_score.py:459) before any stage is read.
#   a dead ladder STAGE TAP  ESCAPED. `voice.trace["ladder"]` zeroed after every
#   (INJECTED; audio live)   render, the returned audio and the other taps left
#                            alone. The stage log names what ran:
#
#                              STAGE oscillator peak 0.92
#                              STAGE mixer      peak 0.92
#                              STAGE ladder     peak 0.0
#
#     File "tools/lead_drive_337.py", line 175, in main
#       base_m5a, base_m5b = render("M5A", BASE_DRIVE), render("M5B", BASE_DRIVE)
#     File "tools/lead_drive_337.py", line 137, in render
#       r = sc.measure(case_id=case, output_path=wav)
#     File "tools/mono_m5a_score.py", line 491, in measure
#       _stage_diagnostics(voice.trace, pcm, a, b, eo.value)),
#     File "tools/mono_m5a_score.py", line 219, in _stage_diagnostics
#       signature = am.harmonic_signature(x, SR, f0=f0, kmax=12)
#     File "model/audio_measure.py", line 2379, in harmonic_signature
#       raise InsufficientEvidence("harmonic_signature: silent")
#   audio_measure.InsufficientEvidence: harmonic_signature: silent
#   exit 1            (the two seam frames, `_measure_once` and
#                      `_signature_logged`, are elided above)
#
# So the gap is real in shape and, on this tree, reached only by injection:
# the supported experiment (drives 0.75, 0.50, 0.25) completes with every
# stage of every event measured. That is a statement about those renders, not
# a proof that no future render has a dead tap, which is why the dispatch now
# names the class: such a stage is REFUSED, never rendered FAIL.
SEAM_FAILED = "SEAM FAILED"
STAGE_FAULT = "unrelated ladder-stage fault"
SECOND_RENDER = SEAM_FAILED + (": the run reached a second render; "
                               "the fault did not take on the first")
REPORT = L.OUT / "report.json"

#: child-interpreter source: the voice's ladder STAGE TAP, dead after every
#: render, while the returned audio and the other taps are left alone
LADDER_TAP_SILENT = (
    "import numpy as np, voice_fx as vf\n"
    "_render = vf.VoiceFx._render\n"
    "def _render_dead_ladder_tap(self, *a, **kw):\n"
    "    out = _render(self, *a, **kw)\n"
    "    self.trace['ladder'][:] = 0\n"
    "    return out\n"
    "vf.VoiceFx._render = _render_dead_ladder_tap\n"
    "_v = vf.VoiceFx()\n"
    "_pcm = vf.render_mono_fx([(0.0, 69, 0.5, {})], 0.5, _v)\n"
    f"assert np.any(_pcm), {SEAM_FAILED + ': the voice no longer renders'!r}\n"
    f"assert not np.any(_v.trace['ladder']), {SEAM_FAILED + ': the ladder tap is not silent'!r}\n"
    f"assert np.any(_v.trace['osc'][0]) and np.any(_v.trace['mixed']), "
    f"{SEAM_FAILED + ': another stage tap went silent too'!r}\n")

#: the same seam, an ordinary AssertionError after the render instead
LADDER_TAP_FAULT = (
    "import voice_fx as vf\n"
    f"STAGE_FAULT = {STAGE_FAULT!r}\n"
    "_render = vf.VoiceFx._render\n"
    "def _render_fault(self, *a, **kw):\n"
    "    _render(self, *a, **kw)\n"
    "    raise AssertionError(STAGE_FAULT)\n"
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

#: the CLI takes no arguments and writes its report at the END of `main`,
#: after every render. Each fault is aimed at the first render, so a run that
#: asks for a second has missed it: stopped there, before any report can exist
ONE_RENDER = (
    "import mono_m5a_score as sc\n"
    "_measure = sc.measure\n"
    f"SECOND_RENDER = {SECOND_RENDER!r}\n"
    "def _measure_once(**kw):\n"
    "    if _measure.calls:\n"
    "        raise RuntimeError(SECOND_RENDER)\n"
    "    _measure.calls += 1\n"
    "    return _measure(**kw)\n"
    "_measure.calls = 0\n"
    "sc.measure = _measure_once\n")

#: names the stage whose window the estimator is handed, read from the
#: scorer's own loop in `_stage_diagnostics`, so the record says which stage ran
STAGE_LOG = (
    "import sys, numpy as np, audio_measure as am\n"
    "_signature = am.harmonic_signature\n"
    "def _signature_logged(x, sr, **kw):\n"
    "    f = sys._getframe(1)\n"
    "    if f.f_code.co_name == '_stage_diagnostics':\n"
    "        print('STAGE', f.f_locals.get('name', '?'), 'peak',\n"
    "              float(np.abs(np.asarray(x)).max()), file=sys.stderr, flush=True)\n"
    "    return _signature(x, sr, **kw)\n"
    "am.harmonic_signature = _signature_logged\n")


def _handoff(*preludes: str) -> list:
    """`python tools/lead_drive_337.py` with `preludes` installed first.
    `run_path` runs the CLI's real `__main__`, so its own `sys.exit` is what
    the process exits with."""
    paths = [str(L.ROOT / p) for p in ("model", "tools")]
    src = (f"import sys, runpy\nsys.path[:0] = {paths!r}\n"
           + "".join(preludes)
           + f"runpy.run_path({str(L.__file__)!r}, run_name='__main__')\n")
    return [sys.executable, "-c", src]


def _run(preludes) -> subprocess.CompletedProcess:
    p = subprocess.run(_handoff(*preludes), capture_output=True, text=True,
                       timeout=300, cwd=str(L.ROOT))
    assert SEAM_FAILED not in p.stderr, p.stderr[-600:]
    return p


def _report_bytes():
    return REPORT.read_bytes() if REPORT.exists() else None


def test_estimator_refusal_is_an_assertion_error_the_refused_handler_cannot_see():
    # the class relations the fix rests on, and that the class the dispatch
    # names is the class the scorer's estimator raises
    klass = L.am.InsufficientEvidence
    assert issubclass(klass, AssertionError)
    assert not issubclass(klass, refusal.Refused)
    assert klass is L.sc.am.InsufficientEvidence


def test_dead_ladder_stage_tap_refuses_through_the_dispatch_not_a_traceback():
    before = _report_bytes()
    p = _run([LADDER_TAP_SILENT, ONE_RENDER, STAGE_LOG])
    text = p.stdout + p.stderr
    assert p.returncode == exit_contract.REFUSED, (p.returncode, text[-1500:])
    assert "REFUSED: harmonic_signature: silent" in p.stderr, text[-600:]
    assert "Traceback" not in text and "InsufficientEvidence" not in text, text[-600:]
    # the pitch gate passed on the live output and the stages before the ladder
    # were measured: the refusal is the ladder stage's, from inside the scorer
    assert "STAGE oscillator peak" in p.stderr and "STAGE mixer peak" in p.stderr, text[-600:]
    assert "STAGE ladder peak 0.0" in p.stderr, text[-600:]
    assert "STAGE output" not in p.stderr, text[-600:]
    # nothing may follow the refusal: no report, no drive verdict
    assert p.stderr.splitlines()[-1].startswith("REFUSED:"), text[-600:]
    assert p.stdout.strip() == "", "a refused run must not print a drive verdict"
    assert _report_bytes() == before, "a refused run must not write a report"


def test_plain_assertion_error_at_the_scoring_boundary_is_still_an_execution_error():
    before = _report_bytes()
    p = _run([LADDER_TAP_FAULT, ONE_RENDER])
    assert p.returncode == exit_contract.FAIL, (p.returncode, p.stdout[-200:], p.stderr[-600:])
    assert "Traceback" in p.stderr and f"AssertionError: {STAGE_FAULT}" in p.stderr, p.stderr[-600:]
    assert "REFUSED" not in p.stdout, "an AssertionError must not be relabelled a refusal"
    assert _report_bytes() == before


def test_a_silent_render_is_stopped_by_the_pitch_gate_before_any_stage_is_read():
    before = _report_bytes()
    p = _run([VOICE_SILENT, ONE_RENDER, STAGE_LOG])
    text = p.stdout + p.stderr
    assert p.returncode == exit_contract.REFUSED, (p.returncode, text[-600:])
    assert "REFUSED: saw MIDI 84 pitch refused: silent" in p.stderr, text[-600:]
    assert "Traceback" not in text and "InsufficientEvidence" not in text, text[-600:]
    assert "STAGE" not in p.stderr, "no stage window may reach the estimator"
    assert _report_bytes() == before


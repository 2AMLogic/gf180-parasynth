"""#1763: where an estimator's own refusal goes, in each of the ten modules the
AST census named after #1751 (red-first on main @ 6aa09d26, 2026-11-28).

`audio_measure.InsufficientEvidence` subclasses AssertionError, not
`refusal.Refused`, so a dispatch handler written `except refusal.Refused`
cannot see an estimator refusing. #1751 repaired one site. The class search
that closed it listed ten more modules that call an estimator able to raise
that class and catch only `Refused`. A census row is a candidate, not a defect,
and this file is each candidate RUN. The fault is #1751's -- the model's own
render is digitally silent, what a dead voice, a muted path or a regression
looks like at the estimator -- put below the tool and above the estimator, the
tool then run through its real `__main__` (`runpy`, so its argv is a direct
invocation's). Two of the ten are libraries without an entry point and are run
through the function that holds the listed call.

WHAT THE RUNS FOUND: 1 + 1 of 10 reachable, 8 not.

  tools/pekonen_coloration_probe.py  ESCAPED. `ours_rerender` hands
      `harmonic_signature` an oscillator render nothing has checked: exit 1
      with `audio_measure.InsufficientEvidence: harmonic_signature: silent`,
      raised at :116 and first reached from control 5 (:220).
  tools/probes/f1_level_scaling.py   ESCAPED, through neither listed
      `plateau_db` site -- both take curves that are finite by construction.
      What defeats `main`'s handler is a site the census cannot see:
      `reference_rigs.tone_project` ends in `Estimate.require()`, a method,
      which raises on a capture whose first tone is silent.

Both dispatch handlers now name the class beside `Refused`, as #1751's does.
The other eight are unchanged, each stopped before its listed call:

  measure_m5a_pulse_duty.py, measure_m5a_signal_path.py, mono_m5a_score.py
                                    `refine_f0` refuses a silent segment first
  f1_selected_path.py               run_case.our_filter_curve translates the
                                    projection's refusal into its own
  ladder_headroom.py                the ring estimator refuses, before :490
  holdout_f1d_preconditions.py      SelectedFilterPath.curve's own guard, at :83
  reference_voice.py                `measure` returns a refused row, no exception
  corner_reference_convention.py    no model render reaches :164, and every
                                    stored curve has a finite point in the band

One of those eight still ends in a traceback, with another class:
holdout_f1d_preconditions.py has no handler at all, so `f1_filter_path.Refused`
leaves as exit 1. That is the older cross-module class (#1162), filed as
#1780; this file pins only that the silent model is stopped at :83 and does
not pin that tool's exit status, so the repair will not turn the row red. The
two other things the runs turned up are filed too: reference_voice.py's entry
point has no refusal path for a missing plugin host (#1783), and the census
that produced this list cannot see `.require()` or a transitive raise origin
(#1782) -- which is how f1_level_scaling.py's escape got listed under two
sites that were never on its path.

WRONG-THEN-RIGHT (4, all in the apparatus, each caught because the run
disagreed with the prediction made from reading):

  1. The first run of f1_level_scaling.py and f1_selected_path.py refused
     with "is not in the cache", not with the gate predicted from reading: a
     fresh checkout has no frozen reference audio until
     `tools/refprofile_restore.py` has run. It is why every row pins the
     gate's own words and not only the exit status.
  2. The first ladder seam replaced `fixed.LadderFx.process` and nothing else.
     `reference_rigs._Variant` overrides `process` with its own copy of the
     loop, so the legacy ladder stayed alive and `ladder_headroom.py --quick`
     exited 0 with a full report. Every seam now renders a probe and asserts it
     is zero before the tool runs.
  3. The first capture seam zeroed `y[a:b]` with (a, b) the first two fields of
     a `parts` row, which are (start, length). It zeroed the wrong slice, its
     own check passed on that same wrong slice, and `f1_level_scaling.py`
     exited 0. The slice is `y[i0:i0 + nw]` and the check asserts both ways.
  4. The first `_restore_frozen_cache` tested `restore(...) != 0`. That
     function returns the clip COUNT (16) and raises on failure, so a
     successful restore skipped the f1_selected_path row as "REFUSED" -- a
     green run with one row measured nothing. It now catches the refusal and
     asserts the count is positive.
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys
from typing import NamedTuple

import numpy as np
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import exit_contract                                                 # noqa: E402

#: what a seam raises when it has not taken: reported as that, never as a
#: verdict about the tool
SEAM_FAILED = "SEAM FAILED"
RENDER_FAULT = "unrelated oscillator fault"
CAPTURE_FAULT = "unrelated capture fault"

_CHILD_PATHS = "sys.path[:0] = %r\n" % [str(ROOT / p) for p in ("model", "tools", "tools/probes")]

#: the oscillator, the voice and both ladders (the selected path's inner
#: `fixed.LadderFx` and the legacy `_Variant`, which carries its own loop)
OSC_SILENT = (
    "import numpy as np, voice_fx as vf\n"
    "def _render(self, n, inc):\n"
    "    return np.zeros(n, dtype=np.int64)\n"
    "vf.OscFx.render = _render\n"
    "_probe = vf.OscFx('saw', blep=True).render(4800, vf.phase_inc(440.0))\n"
    f"assert not np.any(_probe), {SEAM_FAILED + ': the oscillator still renders'!r}\n")

VOICE_SILENT = (
    "import numpy as np, voice_fx as vf\n"
    "def _play(self, regs, writes, n):\n"
    "    self.trace = dict(osc=[np.zeros(n, dtype=np.int64)],\n"
    "                     mixed=np.zeros(n, dtype=np.int64),\n"
    "                     ladder=np.zeros(n, dtype=np.int64))\n"
    "    return np.zeros(n, dtype=np.int16)\n"
    "vf.VoiceFx.play = _play\n"
    "_probe = vf.render_mono_fx([(0.0, 69, 0.5, {})], 0.5)\n"
    f"assert not np.any(_probe), {SEAM_FAILED + ': the voice still renders'!r}\n")

LADDER_SILENT = (
    "import numpy as np, fixed, reference_rigs as rr, voice_fx as vf\n"
    "fixed.LadderFx.process = lambda self, x, *a, **k: np.zeros(len(x), dtype=np.int64)\n"
    "rr._Variant.process = lambda self, x, *a, **k: np.zeros(len(x), dtype=np.int16)\n"
    "_ring = rr.OurLadder('ours').ring(1000.0, 1.05)\n"
    "_note = vf.render_mono_fx([(0.0, 69, 0.5, {})], 0.5)\n"
    f"assert not np.any(_ring) and not np.any(_note), "
    f"{SEAM_FAILED + ': a ladder still renders'!r}\n")

OSC_FAULT = (
    "import voice_fx as vf\n"
    f"RENDER_FAULT = {RENDER_FAULT!r}\n"
    "def _render(self, n, inc):\n"
    "    raise AssertionError(RENDER_FAULT)\n"
    "vf.OscFx.render = _render\n"
    "try:\n"
    "    vf.OscFx('saw', blep=True).render(4800, vf.phase_inc(440.0))\n"
    "except AssertionError:\n"
    "    pass\n"
    "else:\n"
    f"    raise AssertionError({SEAM_FAILED + ': the oscillator still renders'!r})\n")

#: a Surge capture as the level experiment reads it, with its first tone
#: silent: what a host that dropped one tone looks like at the projection
SILENT_FIRST_TONE = (
    "seg[:] = 0.0\n"
    f"    assert not np.any(seg), {SEAM_FAILED + ': the first tone is not silent now'!r}")


def _capture_fault(effect: str) -> str:
    """Child-interpreter source: wrap `f1_level_capture.load_capture` -- the one
    read every Surge curve is projected from, hash-checked -- so the audio it
    returns has `effect` applied to its first tone segment `seg`."""
    return (
        "import numpy as np\n"
        "import f1_level_capture as cap\n"
        "_load = cap.load_capture\n"
        "def _load_capture(cond, amp, take, manifest=None, inject=''):\n"
        "    y, meta = _load(cond, amp, take, manifest, inject)\n"
        "    i0, nw = int(meta['parts'][0][0]), int(meta['parts'][0][1])\n"
        "    seg = y[i0:i0 + nw]\n"
        f"    assert np.any(seg), {SEAM_FAILED + ': the first tone was silent before the fault'!r}\n"
        f"    {effect}\n"
        "    return y, meta\n"
        "cap.load_capture = _load_capture\n")


def _handoff(script: str, seam: str, *argv: str, before: str = "") -> tuple:
    """`python <script> <argv...>` with `seam` installed first. `run_path` puts
    the script's path at argv[0], which is what a direct invocation leaves
    there. `@TMP@` in any argument is the test's temp directory."""
    src = (f"import sys, runpy\n{_CHILD_PATHS}\n{before}{seam}"
           f"runpy.run_path({str(ROOT / script)!r}, run_name='__main__')\n")
    return (sys.executable, "-c", src, *argv)


def _call(body: str, seam: str) -> tuple:
    """A library's public surface in a child: `body` runs under `seam`, and
    whatever leaves it is reported as one OUTCOME line."""
    src = (f"import sys\n{_CHILD_PATHS}\n{seam}try:\n"
           + "".join(f"    {line}\n" for line in body.splitlines())
           + "except BaseException as e:\n"
           "    print('OUTCOME raised', type(e).__module__ + '.' + type(e).__name__, '|', e)\n")
    return (sys.executable, "-c", src)


class Row(NamedTuple):
    """One candidate. `stopped_by` is what the gate says and must appear in the
    output; `absent` must not. `exit_code` None means the exit status is not
    this file's claim."""
    id: str
    argv: tuple
    stopped_by: tuple = ()
    exit_code: int | None = None
    fixed: bool = False
    absent: tuple = ()
    outcome: str = ""
    fault: str = ""


SILENT_RENDER = (
    Row("measure_m5a_pulse_duty",
        _handoff("tools/measure_m5a_pulse_duty.py", VOICE_SILENT, "--out", "@TMP@/pulse-duty.json"),
        ("REFUSED: pulse MIDI 84 pitch refused: silent",), exit_contract.REFUSED),
    Row("measure_m5a_signal_path",
        _handoff("tools/measure_m5a_signal_path.py", VOICE_SILENT,
                 "--cutoff", "14073", "--drive", "1.0", "--out", "@TMP@/signal-path.json"),
        ("REFUSED: oscillator MIDI 84 f0 refused: silent",), exit_contract.REFUSED),
    Row("pekonen_coloration_probe",
        _handoff("tools/pekonen_coloration_probe.py", OSC_SILENT),
        ("REFUSED: harmonic_signature: silent",), exit_contract.REFUSED, fixed=True,
        absent=("control ",)),
    Row("f1_level_scaling",
        _handoff("tools/probes/f1_level_scaling.py", _capture_fault(SILENT_FIRST_TONE), "--workers", "2"),
        ("REFUSED  open20k/", "probe 40 Hz: insufficient evidence -- silent"),
        exit_contract.REFUSED, fixed=True),
    Row("f1_selected_path",
        _handoff("tools/probes/f1_selected_path.py", LADDER_SILENT,
                 before="import pathlib, refprofile as rp\nrp.PROFILE_DIR = pathlib.Path('@TMP@')\n"),
        ("REFUSED  our ladder's stepped-tone probe refused",), exit_contract.REFUSED),
    Row("ladder_headroom",
        _handoff("model/ladder_headroom.py", LADDER_SILENT, "--quick", "--json", "@TMP@/headroom.json"),
        ("REFUSED: the ring at 100 Hz, res 1.05: silent record",), exit_contract.REFUSED),
    Row("holdout_f1d_preconditions",
        _handoff("tools/probes/holdout_f1d_preconditions.py", LADDER_SILENT, "--cut", "500"),
        ("selected-path probe refused at 40 Hz, cutoff 500 Hz: silent",)),
    Row("mono_m5a_score.measure",
        _call("import mono_m5a_score as m5a\n"
              "m5a.measure(keep_audio=False)\n"
              "print('OUTCOME returned')", VOICE_SILENT),
        ("pitch refused: silent",), exit_contract.PASS,
        outcome="OUTCOME raised mono_m5a_score.Refused"),
    Row("reference_voice.measure",
        _call("import numpy as np, reference_voice as rv, voice_fx as vf\n"
              "row = rv.measure(np.zeros(int(0.5 * rv.SR)), vf.note_hz(69), 'silent', 'saw')\n"
              "print('OUTCOME returned', 'valid=%r steady=%r' % (row['valid'], row['steady']))", ""),
        ("valid=False steady=False",), exit_contract.PASS, outcome="OUTCOME returned"),
)

#: the control against catching too broadly: an ordinary AssertionError at the
#: same seam, which is an execution error and must stay one
CONTROLS = (
    Row("pekonen_coloration_probe",
        _handoff("tools/pekonen_coloration_probe.py", OSC_FAULT),
        exit_code=exit_contract.FAIL, fault=RENDER_FAULT),
    Row("f1_level_scaling",
        _handoff("tools/probes/f1_level_scaling.py",
                 _capture_fault(f"raise AssertionError({CAPTURE_FAULT!r})"), "--workers", "2"),
        exit_code=exit_contract.FAIL, fault=CAPTURE_FAULT),
)


def _run(argv: tuple, tmp_path: pathlib.Path) -> subprocess.CompletedProcess:
    p = subprocess.run([a.replace("@TMP@", str(tmp_path)) for a in argv],
                       capture_output=True, text=True, timeout=300, cwd=str(ROOT))
    assert SEAM_FAILED not in p.stderr, p.stderr[-600:]
    return p


def _restore_frozen_cache(tmp_path: pathlib.Path) -> None:
    """`refprofile`'s cache under `tmp_path`, out of the committed archive: what
    `tools/refprofile_restore.py` does for a checkout. CI's `pytest tools/` job
    has not done it, and without the audio `f1_selected_path.py` refuses at the
    frozen-clip read, before any render."""
    import refprofile_restore
    profile = tmp_path / "profile.json"
    profile.write_bytes((ROOT / "refprofile" / "profile.json").read_bytes())
    try:
        # the clip COUNT on success; a refusal is raised, never returned
        count = refprofile_restore.restore(ROOT / "refprofile" / "frozen-cache.zip", profile)
    except refprofile_restore.Refused as exc:
        pytest.skip(f"REFUSED: {exc}")
    assert count > 0, count


@pytest.mark.parametrize("row", SILENT_RENDER, ids=[r.id for r in SILENT_RENDER])
def test_a_silent_model_render_never_leaves_as_the_estimators_own_refusal(row, tmp_path):
    """With the model silent, each candidate either refuses through its dispatch
    (the two repaired rows: exit 2, the estimator's reason on the REFUSED line,
    no traceback) or is stopped before the listed call by a gate that was
    already there (exit 2 with that gate's reason). In no case does
    `audio_measure.InsufficientEvidence` leave the module."""
    if row.id == "f1_selected_path":
        _restore_frozen_cache(tmp_path)
    p = _run(row.argv, tmp_path)
    out = p.stdout + p.stderr
    for text in row.stopped_by:
        assert text in out, out[-600:]
    absent = row.absent + (("Traceback",) if row.fixed else ("InsufficientEvidence",))
    for text in absent:
        assert text not in out, f"{text!r} in: " + out[-600:]
    if row.outcome:
        assert p.returncode == exit_contract.PASS and row.outcome in p.stdout, out[-600:]
    if row.exit_code is not None:
        assert p.returncode == row.exit_code, (p.returncode, out[-600:])
    if row.fixed:
        last = p.stdout.splitlines()[-1]
        assert last.startswith("REFUSED"), "nothing may follow the refusal: " + p.stdout[-400:]


@pytest.mark.parametrize("row", CONTROLS, ids=[r.id for r in CONTROLS])
def test_a_plain_assertion_error_from_the_same_seam_is_still_an_execution_error(row, tmp_path):
    """An ordinary AssertionError from the seam the silent fault uses still
    exits 1, names the injected fault in its traceback and prints no REFUSED
    line: naming `InsufficientEvidence` in the handler must not widen it to
    every AssertionError (`tools/refusal.py`'s "inverse defect")."""
    p = _run(row.argv, tmp_path)
    assert p.returncode == exit_contract.FAIL, (p.returncode, p.stdout[-200:], p.stderr[-600:])
    assert "Traceback" in p.stderr and f"AssertionError: {row.fault}" in p.stderr, p.stderr[-600:]
    assert "REFUSED" not in p.stdout, "an AssertionError must not be relabelled a refusal"


def test_every_stored_curve_has_a_finite_point_in_the_legacy_reference_band():
    """`corner_reference_convention.py:164` reads `plateau_db` over the 56
    measured curves committed in `docs/reference-compare-results.json`.
    `plateau_db` raises only when no finite point falls in the band, and the
    band's lower edge is the curve's first frequency: this is that property,
    read off the data rather than argued."""
    record = json.loads((ROOT / "docs" / "reference-compare-results.json").read_text())
    seen = 0
    for key in record:
        if not key.startswith("response-"):
            continue
        for curve in record[key]:
            if "freqs" not in curve:
                continue
            f = np.asarray(curve["freqs"], dtype=float)
            g = np.asarray(curve["gain_db"], dtype=float)
            top = max(f[0] * 2.5, (curve["cut_hz"] or 400.0) * 0.25)
            in_band = (f >= f[0]) & (f <= top) & np.isfinite(g)
            assert in_band.any(), (key, curve.get("cut_hz"))
            seen += 1
    assert seen >= 56, seen

#!/usr/bin/env python3
"""Known-answer exit-status controls: an estimator refusal raised while the f1
level capture's `--worker` projects a take must reach the process exit as
REFUSED, never as an uncaught exit-1 traceback (#1880 -- the #1763 shape, in
the caller #1763 did not cover).

THE DEFECT, AS MEASURED ON origin/main @ 460284d6 (2026-11-28, red-first run
of this file before the fix; `tools/f1_level_capture.py` is unchanged on
origin/main through 560a1580): `tools/f1_level_capture.py::main` catches only
`(Refused, rp.Refused)` at the CLI dispatch, but `_projected_low_db` reaches
`reference_rigs.SurgeRig.tone_project`, and every tone there goes through
`audio_measure.Estimate.require`. An `audio_measure.InsufficientEvidence` is
neither of the caught classes -- it is an `AssertionError` subclass -- so a
take whose FIRST TONE is silent escaped `--worker` as a traceback and the
process exited 1: a refusal rendered as FAIL, the inversion of the exit
contract (#804, #1162, #1763). Observed on the committed take with its first
tone silenced, before the fix: exit 1 and
`audio_measure.InsufficientEvidence: guard probe 40 Hz: insufficient evidence
-- silent {}` on stderr. After it: exit 2, `REFUSED  <that reason>` on
stdout, no traceback. By reading `capture()` (not run): through `--capture` a
child's non-zero exit was already folded into the parent's own `Refused`; the
exit-1 traceback was the `--worker` contract.

THE KNOWN ANSWERS, independent of anything this repository models: the
coherent projection of an all-zero window is exactly zero (a sum of zeros),
so "silent" is the only honest verdict on a silenced tone whatever the
estimator's arithmetic; and this repository's exit contract is REFUSED (2)
for a stated missing precondition and 1 for an execution error
(tools/exit_contract.py). The signal is the committed
`open20k/a0.2500/take1` capture (sha256 `ad30cebd73b7...`), which
`load_capture` hash-checks against the manifest and the manifest attributes
to Surge XT. The one synthetic act is zeroing its first tone.

PER-SITE REACHABILITY, which decides how much of the census row is real.
MEASURED = executed by this file; READ = traced in the source, not executed
(no fleet host has dawdreamer or Surge XT). Line numbers are those of
460284d6; the fix adds one import line above them.

  worker -> _projected_low_db(render), `:198`
      REACHABLE once the gates above it pass: the only silence gate before
      it (`:195`) tests the whole clip's peak, which a first-tone-silent clip
      satisfies. That gate's defeating input (verification-rules rule 8) is
      this file's stand-in. The projection: MEASURED. The `:195` gate: READ.
  worker -> _projected_low_db(frozen clip), `:179`
      the same call; needs a frozen wide-open clip whose first tone is
      silent. Not observed. READ.
  worker / capture -> rp.load_clip, `:177` and `:267`
      STOPPED INSIDE `load_clip`, which translates `NonFiniteAudio` into its
      own `Refused` (tools/refprofile.py, `except am.NonFiniteAudio ... raise
      Refused`). READ. The issue's enhancement attributed this to sha
      verification; the translation is the reason, and it holds for a
      hash-matching NaN clip as well.
  worker -> rp.write_clip, `:203`
      stopped twice: the `:195` finiteness gate, then `write_clip`'s own
      `NonFiniteAudio` -> `Refused` translation. READ.
  capture_curve, `:362`
      not on this module's `main()` path; its callers are #1763's. READ.

One gate the enhancement did not list, found while tracing and out of scope
here (tracked as #1391): `worker` `:175` demands the frozen grid EQUAL
`reference_compare.FREQS` (`np.geomspace(40, 12000, 32)`). MEASURED on this
host (numpy 2.5.5, Linux): the committed grid differs from it by one ULP at
index 12 (363.8694868585699 vs 363.86948685856987), so with the plugin and
the cache in place this host would refuse THERE first, correctly. DERIVED
from the manifest's existence: the comparison passed on the darwin / numpy
2.5.3 host that built the captures, which is where alone the `:198` site is
reachable today. It changes nothing about the handler: `main()` owes REFUSED
for an estimator refusal whenever one occurs.

WHAT THE CAPTURE OWES, and what these controls assert:

  first tone silent    exit `exit_contract.REFUSED` (2), the estimator's own
                       reason on stdout, no traceback
  NaN sample           the same for `NonFiniteAudio`, which the handler names
                       beside `InsufficientEvidence` although no site inside
                       `main()` reaches it (above)
  no plugin host       the pre-existing local refusal is preserved: exit 2
                       and the `no plugin host` reason, through the REAL
                       `worker`
  plain AssertionError / ValueError from the same call
                       STILL exit 1 with a traceback. `InsufficientEvidence`
                       IS an `AssertionError`, so a handler widened to
                       `AssertionError` or `Exception` would pass every
                       refusal control here and relabel an implementation
                       error as a refusal (tools/refusal.py's "inverse
                       defect"); these two controls are what go red then

Every assertion pins the message, not just the code, because an exit code
alone is satisfied by a crashed-and-caught-anything handler. Pattern:
tools/test_rs_guard_band_refusal.py (#1162).

WRONG-THEN-RIGHT (1, caught by running rather than reading): the first draft
pinned the first clause of the `NonFiniteAudio` message as if it were the
whole line, and the exact-equality assertion failed on the fixed tree. The
estimator's message is three sentences; all three are pinned now.

WHAT THE CONTROLS CAN SEE (handler mutants, run on the fixed tree, 2026-11-28;
the mutant is not committed, the controls that redden under it are): reverting
the handler reddens the three refusal controls and nothing else; widening it
to `AssertionError` reddens exactly the two plain-`AssertionError` controls
while every refusal control stays green, which is why they exist; `Exception`
reddens those two and the `ValueError` control; adding `ValueError` reddens
exactly that one. BLIND to every handler mutant, by construction: the class
relation, the three mechanism controls and the preserved `no plugin host`
refusal, which read the estimator and the worker, not the handler.
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

import numpy as np
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "model"))

import audio_measure as AM                                # noqa: E402
import exit_contract                                      # noqa: E402
import f1_level_capture as cap                            # noqa: E402
import refprofile as rp                                   # noqa: E402
import refusal                                            # noqa: E402

CLIP = ("open20k", 0.25, 1)
WORKER_ARGV = ["--worker", "--amp", "0.25", "--take", "1"]
SILENT = "guard probe 40 Hz: insufficient evidence -- silent {}"
NAN = ("the signal holds 1 non-finite samples of 19200 (1 NaN, 0 Inf; "
       "first at index 5): this is not audio. Every threshold guard downstream "
       "of here would be DEFEATED by it rather than tripped, because NaN "
       "compares False against any bound (#133, #134)")
INJECTED = "injected: a plain {} raised inside the worker"


def _committed() -> tuple[np.ndarray, list, float]:
    """(audio, parts, amp) of the committed take, straight out of the archive."""
    y, meta = cap.load_capture(*CLIP)
    parts = [(int(a), int(b), float(f)) for a, b, f in meta["parts"]]
    return y, parts, float(meta["commanded"]["amp"])


def _first_tone_silenced(y: np.ndarray, parts: list) -> np.ndarray:
    """The take with its FIRST tone -- settle and window -- replaced by digital
    silence; every other sample untouched."""
    z = y.copy()
    z[:parts[1][0]] = 0.0
    return z


def _one_nan_sample(y: np.ndarray, parts: list) -> np.ndarray:
    z = y.copy()
    z[parts[0][0] + 5] = np.nan
    return z


def _projecting(mutate):
    """A `worker` whose render is the committed take with `mutate` applied and
    whose projection is the real `_projected_low_db`: the plugin is the only
    thing stood in for."""
    def worker(amp, take, out):
        y, parts, a = _committed()
        cap._projected_low_db(mutate(y, parts), parts, a)
    return worker


def _raising(klass):
    def worker(amp, take, out):
        raise klass(INJECTED.format(klass.__name__))
    return worker


MODES = {
    "silent-first-tone": lambda p: p.setattr(cap, "worker", _projecting(_first_tone_silenced)),
    "nan-sample": lambda p: p.setattr(cap, "worker", _projecting(_one_nan_sample)),
    "plain-assertion": lambda p: p.setattr(cap, "worker", _raising(AssertionError)),
    "value-error": lambda p: p.setattr(cap, "worker", _raising(ValueError)),
    "no-plugin-host": lambda p: p.setitem(sys.modules, "dawdreamer", None),
}


def _dispatch(mode: str, out: pathlib.Path, patch) -> int:
    """The real `main()` on the real `--worker` argv. `MODES[mode]` decides what
    stands in for the plugin; `no-plugin-host` stands in for nothing -- the real
    `worker` runs, and its first precondition refuses."""
    MODES[mode](patch)
    return cap.main(WORKER_ARGV + ["--out", str(out)])


class _NoRestore:
    """The two `monkeypatch` methods `_dispatch` uses, for the subprocess child
    (which exits, so nothing needs restoring)."""

    def setattr(self, obj, name, value):
        setattr(obj, name, value)

    def setitem(self, mapping, name, value):
        mapping[name] = value


def child(argv: list) -> int:
    """The subprocess child's entry point: `_dispatch` in a fresh interpreter,
    so the exit STATUS and stderr are the real ones."""
    mode, out = argv
    return _dispatch(mode, pathlib.Path(out), _NoRestore())


_CHILD = ("import sys; sys.path.insert(0, {tools!r}); "
          "import test_f1_level_capture_refusal as t; "
          "sys.exit(t.child(sys.argv[1:]))").format(tools=str(ROOT / "tools"))


def _process(mode: str, out: pathlib.Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", _CHILD, mode, str(out)],
                          capture_output=True, text=True, timeout=300, cwd=str(ROOT))


def test_estimator_refusals_are_assertion_errors_and_not_refusals():
    """The class relations the whole fix rests on."""
    assert issubclass(AM.InsufficientEvidence, AssertionError)
    assert issubclass(AM.NonFiniteAudio, AM.InsufficientEvidence)
    for klass in (cap.Refused, rp.Refused, refusal.Refused):
        assert not issubclass(AM.InsufficientEvidence, klass), klass
        assert not issubclass(AM.NonFiniteAudio, klass), klass


def test_committed_take_projects_and_is_blind_to_tones_beyond_the_fourth():
    """The mechanism with no stand-in at all -- the clean run: the real
    estimator on the unmodified committed take, and an exact invariance."""
    y, parts, amp = _committed()
    g = cap._projected_low_db(y, parts, amp)
    assert np.all(np.isfinite(g))
    z = y.copy()
    z[parts[4][0]:parts[4][0] + parts[4][1]] = 0.0
    assert cap._projected_low_db(z, parts, amp) == g


def test_first_tone_silenced_raises_the_estimator_refusal_not_a_refused():
    y, parts, amp = _committed()
    with pytest.raises(AM.InsufficientEvidence) as caught:
        cap._projected_low_db(_first_tone_silenced(y, parts), parts, amp)
    assert str(caught.value) == SILENT
    assert not isinstance(caught.value, (cap.Refused, rp.Refused))


def test_one_nan_sample_raises_nonfinite_audio():
    y, parts, amp = _committed()
    with pytest.raises(AM.NonFiniteAudio) as caught:
        cap._projected_low_db(_one_nan_sample(y, parts), parts, amp)
    assert str(caught.value) == NAN


@pytest.mark.parametrize("mode, reason", [("silent-first-tone", SILENT), ("nan-sample", NAN)])
def test_worker_estimator_refusal_is_refused_2_with_the_reason(tmp_path, monkeypatch, capsys,
                                                                mode, reason):
    """The dispatch: an estimator refusal is REFUSED by reason, not a traceback."""
    rc = _dispatch(mode, tmp_path, monkeypatch)
    assert rc == exit_contract.REFUSED == cap.REFUSED
    assert capsys.readouterr().out == f"REFUSED  {reason}\n"


def test_no_plugin_host_keeps_the_real_workers_own_refusal(tmp_path, monkeypatch, capsys):
    rc = _dispatch("no-plugin-host", tmp_path, monkeypatch)
    assert rc == exit_contract.REFUSED
    assert capsys.readouterr().out.startswith("REFUSED  no plugin host: ")


@pytest.mark.parametrize("mode, klass",
                         [("plain-assertion", AssertionError), ("value-error", ValueError)])
def test_unrelated_errors_from_the_same_call_still_propagate(tmp_path, monkeypatch, capsys,
                                                             mode, klass):
    """Unrelated errors at the same boundary stay execution errors."""
    with pytest.raises(klass) as caught:
        _dispatch(mode, tmp_path, monkeypatch)
    assert type(caught.value) is klass, type(caught.value)
    assert "REFUSED" not in capsys.readouterr().out


def test_process_exits_2_without_a_traceback_for_the_silent_first_tone(tmp_path):
    """The process exit contract, in a real interpreter."""
    p = _process("silent-first-tone", tmp_path)
    assert p.returncode == exit_contract.REFUSED, (p.returncode, p.stdout[-400:], p.stderr[-400:])
    assert p.stdout == f"REFUSED  {SILENT}\n", p.stdout[-400:]
    assert "Traceback" not in p.stderr, p.stderr[-400:]


def test_process_exits_1_with_a_traceback_for_a_plain_assertion_error(tmp_path):
    p = _process("plain-assertion", tmp_path)
    assert p.returncode == exit_contract.FAIL, (p.returncode, p.stdout[-200:], p.stderr[-400:])
    assert "Traceback" in p.stderr, p.stderr[-400:]
    assert f"AssertionError: {INJECTED.format('AssertionError')}" in p.stderr, p.stderr[-400:]
    assert "REFUSED" not in p.stdout, p.stdout[-200:]

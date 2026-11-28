"""`rs_mode_drive`'s harmonic ratios are NOT an instance of #1743, pinned.

`_harm_db` (`:429`, which #1743's `git grep` misses because its `[^)]*`
cannot cross the nested `()` in `p[1:].sum()`) and the three diagnostic ratios
in `cmd_distort` (`:512`, `:528`, `:530`) all divide `max(v, 1e-30)` by
`max(p[0], 1e-30)`: the same-epsilon shape, which reads 0.0 dB for two zeros.
They are fed by exactly one function, `audio_measure.harmonic_powers`, which

  (a) raises `InsufficientEvidence("harmonic_powers: silent")` for
      `peak <= 1e-9`, so an exactly-zero render never reaches any ratio, and
  (b) for every non-silent input tried, returned powers far above the floor
      (measured on origin/main @ 3309dd59, 2026-11-28: >= 9e-23 at amplitude
      1e-7, the Hann leakage of an absent partial), so `p[0]` and `v` are
      never both under it.

So on exactly-zero input the enclosing function RAISES rather than answering
-- but as an `AssertionError` subclass the CLI does not catch: an exit-1
traceback rather than REFUSED. That is #1183's class, not #1743's, and is
tracked as #1751. This file pins (a): if `harmonic_powers`' silence refusal
ever moves, the pin goes red and the classification must be re-read.
"""
from __future__ import annotations

import pathlib
import sys

import numpy as np
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "model"))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "probes"))

import audio_measure as am                         # noqa: E402
import drums_fx as dx                              # noqa: E402
import refusal                                     # noqa: E402
import rs_mode_drive as R                          # noqa: E402


def test_harm_db_raises_on_a_silent_render_and_never_returns_a_number():
    """NOT AN INSTANCE, PINNED (#1743). The ratio would read 0.0 dB for
    `p = zeros`, but a silent render is refused by the callee first."""
    with pytest.raises(am.InsufficientEvidence, match="silent"):
        R._harm_db(np.zeros(4800))


def test_harmonic_powers_is_the_guard_the_classification_rests_on():
    with pytest.raises(am.InsufficientEvidence, match="silent"):
        am.harmonic_powers(np.zeros(4800), dx.RS_LO_HZ, R.HARMS, dx.SR)
    # and why the refusal is not yet a clean one (#1751): the CLI catches
    # refusal.Refused, and this class is not that.
    assert issubclass(am.InsufficientEvidence, AssertionError)
    assert not issubclass(am.InsufficientEvidence, refusal.Refused)


def test_harm_db_known_answer_is_unchanged():
    """sin(f0) + 0.1 sin(2 f0): the harmonics above the fundamental sit 20 dB
    under it, whatever the floors. Measured on the pre-fix tree: -19.9999."""
    n = int((R.HARM_SECONDS + R.PRE_ROLL_S) * dx.SR)
    t = np.arange(n) / dx.SR
    x = np.sin(2 * np.pi * dx.RS_LO_HZ * t) + 0.1 * np.sin(2 * np.pi * 2 * dx.RS_LO_HZ * t)
    assert R._harm_db(x) == pytest.approx(-20.0, abs=0.5)

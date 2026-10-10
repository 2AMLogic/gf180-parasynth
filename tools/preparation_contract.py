"""The preparation contract for a paired ours-vs-reference comparison (#163).

A paired comparison is fair when both sides are in the same STATE when the
shared function runs -- not when they call the same function. #101, #160 F2
and #161 each read `f(ours)` / `f(theirs)` at the call site and were not fair:
the pre-onset lead, a pre-trim and the first sample differed between the
sides, were established upstream in different code paths, and were visible in
neither (`docs/failure-modes.md`, "Root cause: symmetry of code is not
symmetry of treatment").

So the pair asserts the post-`prepare()` APPARATUS state of both sides and
REFUSES, naming every violating field, before any metric reads them:

  field    rule                                         why
  -------  -------------------------------------------  ---------------------------
  sr       a positive integer on each side, and the     window indices and the
           two rates give the same lead IN SAMPLES      zero-phase filter pad are
                                                        sample counts
  lead     samples before t = 0 equal to what           #101 / #132 / #160 F2: the
           `prepare` guarantees at that rate, on each    boundary filter must meet
           side and between the sides, within 1 sample  the same pre-onset on both
  finite   every sample finite, asserted FIRST          #133 / #134: NaN defeats the
                                                        silence test
  silent   neither side silent                          `prepare` returns silence
                                                        unchanged; it does not refuse

**Not compared, deliberately:** first sample, peak, length, onset time into the
original record. Two different sounds legitimately differ in all four, and a
contract that compared them would refuse valid pairs -- which is why
`model/test_discrimination.assert_same_preparation`, which compares duration,
is not used here and is not widened.

**Sample rate: why "equal" is not the rule.** Every real drum pair is a
44.1 kHz Fischer reference against a 48 kHz render, and nothing here resamples
(`run_case` section 3). An equal-rate rule would refuse every #282 drum case,
an unsatisfiable gate. What the rate governs is `required_lead_samples`, which
is 540 samples at both 44.1 and 48 kHz; the contract refuses a rate pair where
it is not (e.g. 44.1 against 96 kHz, 540 against 960).

**What defeats it** (`docs/verification-rules.md` rule 8, pinned as tests in
`tools/test_preparation_contract.py` section 4):

  * a MISDECLARED rate that gives the same sample counts (44 000 for 44 100:
    same 540-sample lead, same 44-sample trim). The contract checks that the
    two sides were windowed alike, not that a declared rate is true.
  * a pedestal under the onset threshold (#161's shape). It is "lead" by the
    threshold's definition, and first samples are deliberately not compared;
    the boundary control in the test file covers this class instead.

The 1-sample lead tolerance is a unit (an onset index on a DC-corrected,
re-normalised copy can move by one sample), not a tuned dial; no sensitivity
dial is registered for it.
"""
from __future__ import annotations

import math
import numbers
from typing import Callable, NamedTuple

import numpy as np

#: The lead may differ from the guarantee by at most this many samples. A unit,
#: not a dial: `prepare` subtracts a DC estimate and re-normalises, which can
#: move the 2 % crossing it is measured at by one sample.
LEAD_TOLERANCE_SAMPLES = 1

#: `audio_measure.is_silent`'s floor. Restated rather than imported so this
#: module has no import-time dependency on `model/`; pinned equal by a test.
SILENCE_FLOOR = 1e-9


class Refused(Exception):
    """A precondition of the apparatus failed. REFUSED is a first-class
    outcome, distinct from pass and from fail: the case is written as a
    no-verdict carrying this reason, and never as a number.

    `violations`, when raised by the contract, is a list of
    `(field, side, detail)` -- every one found, not just the first."""

    def __init__(self, message: str = "", violations=None):
        super().__init__(message)
        self.violations = list(violations or [])


class Side(NamedTuple):
    """One prepared side of a pair: what it is called in a refusal, the
    samples `prepare()` returned, and the rate they are declared at."""
    name: str
    y: object
    sr: object


def _usable_rate(sr) -> bool:
    if isinstance(sr, bool) or not isinstance(sr, numbers.Real):
        return False
    return math.isfinite(float(sr)) and float(sr) > 0 and float(sr) == int(sr)


def _default_convention():
    """`prepare()`'s own convention, read from `run_case` rather than copied:
    a second copy of the lead rule is a second thing to go stale. Imported
    late because `run_case` imports this module."""
    import run_case as rc
    return (rc.required_lead_samples,
            lambda sr: int(round(rc.TRIM_MS * 1e-3 * sr)),
            rc.ONSET_FRAC)


def side_state(side: Side, *, lead_samples: Callable[[int], int],
               trim_samples: Callable[[int], int], onset_frac: float) -> tuple:
    """`(state, violations)` for one prepared side. The state is what was
    measured; each violation is `(field, side.name, detail)`."""
    v = []
    y = np.asarray(side.y)
    st = {"sr": side.sr, "n": int(y.size)}
    rate_ok = _usable_rate(side.sr)
    if not rate_ok:
        v.append(("sr", side.name, f"{side.name} has no usable sample rate ({side.sr!r})"))
    if y.ndim != 1 or y.size == 0:
        v.append(("shape", side.name, f"{side.name} is not a 1-D record (shape {y.shape})"))
        return st, v
    if not np.issubdtype(y.dtype, np.number):
        v.append(("finite", side.name, f"{side.name} is not numeric (dtype {y.dtype})"))
        return st, v
    y = y.astype(np.float64, copy=False)
    bad = ~np.isfinite(y)
    st["finite"] = not bool(bad.any())
    if bad.any():
        # BEFORE the silence test: NaN <= floor is False, so an all-NaN record
        # would otherwise be pronounced "not silent" and measured (#133).
        v.append(("finite", side.name,
                  f"{side.name} has {int(bad.sum())} non-finite sample(s), first at "
                  f"index {int(np.argmax(bad))}"))
        return st, v
    pk = float(np.abs(y).max())
    st["silent"] = pk <= SILENCE_FLOOR
    if st["silent"]:
        v.append(("silent", side.name, f"{side.name} is silent (peak {pk:.3g})"))
        return st, v
    onset = int(np.argmax(np.abs(y) > onset_frac * pk))
    st["onset_index"] = onset
    if not rate_ok:
        return st, v
    sr = int(side.sr)
    want = int(lead_samples(sr))
    st["lead_samples_required"] = want
    if onset == 0:
        st["lead_samples"] = 0
        v.append(("lead", side.name,
                  f"{side.name} begins at or above {onset_frac * 100:.0f} % of its own "
                  f"peak: it was cut into the strike, so it has 0 samples of lead "
                  f"where prepare() guarantees {want}"))
        return st, v
    lead = onset - int(trim_samples(sr))
    st["lead_samples"] = lead
    if abs(lead - want) > LEAD_TOLERANCE_SAMPLES:
        v.append(("lead", side.name,
                  f"{side.name} has {lead} samples ({lead / sr * 1e3:.3f} ms) of lead "
                  f"before t = 0 where prepare() guarantees {want} at {sr} Hz -- it "
                  f"was not prepared by prepare(), or not at the rate it declares"))
    return st, v


def check_prepared_pair(a: Side, b: Side, *, lead_samples=None, trim_samples=None,
                        onset_frac=None) -> dict:
    """Assert that two PREPARED sides are in the same apparatus state, or
    raise `Refused` naming every violating field. Returns the measured state
    of both sides, keyed by side name, when the pair is accepted.

    The convention defaults to `run_case.prepare`'s own; the keywords exist so
    a different preparation path can state its convention rather than borrow
    this one silently."""
    d_lead, d_trim, d_onset = _default_convention() if None in (
        lead_samples, trim_samples, onset_frac) else (None, None, None)
    conv = dict(lead_samples=lead_samples or d_lead,
                trim_samples=trim_samples or d_trim,
                onset_frac=d_onset if onset_frac is None else onset_frac)
    sa, va = side_state(a, **conv)
    sb, vb = side_state(b, **conv)
    v = va + vb
    pair = f"{a.name} / {b.name}"
    if _usable_rate(a.sr) and _usable_rate(b.sr):
        la, lb = conv["lead_samples"](int(a.sr)), conv["lead_samples"](int(b.sr))
        if la != lb:
            v.append(("sr", pair,
                      f"{a.name} at {int(a.sr)} Hz is windowed with a {la}-sample lead and "
                      f"{b.name} at {int(b.sr)} Hz with {lb}: the two sides' zero-phase "
                      f"filters would meet different boundaries"))
    if "lead_samples" in sa and "lead_samples" in sb and \
            abs(sa["lead_samples"] - sb["lead_samples"]) > LEAD_TOLERANCE_SAMPLES:
        v.append(("lead", pair,
                  f"the sides carry {sa['lead_samples']} and {sb['lead_samples']} samples "
                  f"of lead before t = 0"))
    if v:
        fields = sorted({f for f, _, _ in v})
        raise Refused(
            f"preparation contract ({', '.join(fields)}): "
            + "; ".join(f"[{f}] {d}" for f, _, d in v), v)
    return {a.name: sa, b.name: sb}

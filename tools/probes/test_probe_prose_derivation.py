#!/usr/bin/env python3
"""Rule-8 controls for #990's prose-derived-from-measurements repair.

Two probe summaries printed figures as literals beside the same run's live
measurement of the same quantity -- `rs_mode_drive.py distort`'s `reference:`
line and `estimator_defects.py` defect 5's WHY-THE-OTHER-TWO block -- and no
gate read that prose, so it could drift silently (the #988 mechanism). The
repair derives every figure from the values the run measured and guards the
orderings the wording asserts; this file is the committed input that defeats
those guards (`docs/verification-rules.md` rule 8): it perturbs a measured
value and shows the printed prose follow it, breaks each asserted ordering
and shows the guard go red instead of printing true-numbered lies, and feeds
it readings that do not exist (None / nan / a corpus row whose WAV is
absent) and shows it refuse rather than render a plausible number.

Needs no corpus: every figure here is injected. The pin against the literals
the prose used to carry fixes the FORMAT (`.2f` for (b), `.1f` for (c)) at
the precision the old prose printed; the real values behind three of the
five defect-5 figures were re-derived from the probe's seeded fixtures
offline when #990 landed (the other two need the corpus, which is disclosed
in the PR, not claimed here).

    python3 -m pytest tools/probes/test_probe_prose_derivation.py -q
"""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "model"))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "probes"))

import estimator_defects as ed                                      # noqa: E402
import rs_mode_drive as rsd                                         # noqa: E402


# ---------------------------------------------------------------------------
# estimator_defects.py defect 5's WHY-THE-OTHER-TWO block
# ---------------------------------------------------------------------------
def _bc(**overrides) -> dict:
    """A defect-5-shaped (b)/(c) dict carrying the literals the prose used to
    hard-code, at the values the probe's own seeded fixtures measure (verified
    offline, see the module docstring). Keys the helper does not read are
    filled so the dict looks like the run's."""
    bc = {
        ed.PROSE_LABEL_PAD_ZEROS: (0.04, -41.2),
        "PAD  noise -100 dB": (2.02, -28.9),
        ed.PROSE_LABEL_PAD_N60: (3.71, -24.8),
        "REAL tau 40 ms, 0.5 s": (1.38, -6.1),
        "REAL 16-bit quantised": (1.41, -6.3),
        ed.PROSE_LABEL_FLOOR_B: (4.18, -6.2),
        "REAL two-exp, slow late": (1.09, -5.7),
        ed.PROSE_LABEL_BD8: (2.61, -33.6),
        ed.PROSE_LABEL_CL8: (2.24, -25.3),
    }
    bc.update(overrides)
    return bc


def test_derived_prose_matches_the_literals_it_replaced():
    """Printed-precision pin: the derived block renders each figure in the
    format the old prose printed, and the wording's load-bearing claims
    (INVERTED, no threshold) survive the derivation untouched."""
    text = "\n".join(ed._other_two_prose(_bc()))
    assert "pad reads 0.04 and a genuine -80 dBFS noise floor reads 4.18." in text
    assert "the -60 dB pad reads -24.8 dB while" in text
    assert "bass-drum reference -- reads -33.6" in text
    assert "cl8/CL.WAV reads -25.3." in text
    assert "INVERTED" in text and "no threshold" in text


def test_perturbed_measurement_follows_into_the_prose():
    """THE control: perturb a measured value, the prose follows it. The
    figure the prose prints can no longer be a literal -- it is whatever the
    run measured."""
    text = "\n".join(
        ed._other_two_prose(_bc(**{ed.PROSE_LABEL_PAD_N60: (3.71, -20.13)})))
    assert "-20.1" in text and "-24.8" not in text
    text = "\n".join(
        ed._other_two_prose(_bc(**{ed.PROSE_LABEL_PAD_ZEROS: (1.5, -41.2)})))
    assert "pad reads 1.50 and" in text


def test_inverted_claim_goes_red_when_a_reference_reads_above_the_pad():
    """INVERTED means the pad's (c) reads ABOVE both corpus references (the
    pad is the first thing any refuse-below threshold rejects). A reference
    that climbs above the pad falsifies the claim -- the guard must go red,
    not print the word over numbers that say the opposite."""
    with pytest.raises(ed.ProseContradicted):
        ed._other_two_prose(_bc(**{ed.PROSE_LABEL_CL8: (2.24, -20.0)}))


def test_no_threshold_claim_goes_red_when_the_pad_meets_a_reference():
    """'Refuses both references FIRST' is strict: at equality a threshold
    refuses the pad and the reference together, so 'FIRST' would be false."""
    with pytest.raises(ed.ProseContradicted):
        ed._other_two_prose(_bc(**{ed.PROSE_LABEL_CL8: (2.24, -24.8)}))


def test_b_claim_goes_red_when_the_pad_reads_above_the_floor():
    """(b)'s story is that the pad reads as the NON-outlier (the number is a
    property of the sound, not of the cut). A pad above the genuine floor
    would make (b) look like a working detector -- the prose must go red."""
    with pytest.raises(ed.ProseContradicted):
        ed._other_two_prose(_bc(**{ed.PROSE_LABEL_PAD_ZEROS: (5.0, -41.2)}))


def test_absent_corpus_row_is_refused_not_printed():
    """The corpus row's WAV absent (`defect_5` skips the row) leaves the dict
    without the entry; the prose must REFUSE the claim it cannot make."""
    bc = _bc()
    del bc[ed.PROSE_LABEL_BD8]
    with pytest.raises(ed.ProseNeedsRefused):
        ed._other_two_prose(bc)


@pytest.mark.parametrize("label,col,bad", [
    pytest.param(ed.PROSE_LABEL_BD8, 1, float("nan"), id="nan reading"),
    pytest.param(ed.PROSE_LABEL_FLOOR_B, 0, None, id="None reading"),
])
def test_nonfinite_reading_is_refused_not_printed(label, col, bad):
    """An estimator refusal in either encoding must never render as a
    plausible number in prose."""
    entry = _bc()[label]
    entry = (bad, entry[1]) if col == 0 else (entry[0], bad)
    with pytest.raises(ed.ProseNeedsRefused):
        ed._other_two_prose(_bc(**{label: entry}))


# ---------------------------------------------------------------------------
# rs_mode_drive.py distort's `reference:` line
# ---------------------------------------------------------------------------
def test_reference_line_matches_the_literal_it_replaced():
    assert rsd._reference_line(7.24, 3.85) == (
        "   reference: the machine's tail decay is 7.24 ms, bal1 +3.85 dB.")


def test_reference_line_follows_the_measurement():
    line = rsd._reference_line(9.4, -2.0)
    assert "tail decay is 9.40 ms" in line and "bal1 -2.00 dB" in line


@pytest.mark.parametrize("dec,bal", [
    pytest.param(None, 3.85, id="decay refused"),
    pytest.param(7.24, None, id="bal1 refused"),
    pytest.param(float("nan"), 3.85, id="nan decay"),
    pytest.param(7.24, float("nan"), id="nan bal1"),
])
def test_reference_line_refuses_a_reading_that_does_not_exist(dec, bal):
    with pytest.raises(rsd.Refused):
        rsd._reference_line(dec, bal)


def test_reference_fetch_returns_the_measured_pair(monkeypatch):
    """The fetch path, rigged to a measured pair: the prose line's inputs are
    whatever `scored_rows` / `measure` return this run, not a quoted 7.24."""
    monkeypatch.setattr(rsd, "scored_rows",
                        lambda y, sr: {"tail decay": (7.24, "ms")})
    monkeypatch.setattr(rsd, "measure", lambda y, sr: {"balance": 3.85})
    assert rsd._ref_decay_balance(object(), 48000) == (7.24, 3.85)


def test_reference_fetch_returns_none_refusals_untouched(monkeypatch):
    """The None encoding the estimator table produces on a refusal reaches
    the print-site guard untouched -- that guard, not the fetch, is where
    the plausible number would otherwise be born."""
    monkeypatch.setattr(rsd, "scored_rows", lambda y, sr: {})
    monkeypatch.setattr(rsd, "measure", lambda y, sr: {"balance": None})
    assert rsd._ref_decay_balance(object(), 48000) == (None, None)

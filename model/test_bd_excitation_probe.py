#!/usr/bin/env python3
"""What the bass drum's EXCITATION SHAPE can and cannot do (issue #21, 17.20).

These tests need no audio. The reference numbers they check against are
HARDWARE-MEASURED and quoted here as constants, exactly the way
`test_drums_fx.CORPUS` quotes the tom corpus: a test written against what the
kit currently emits can only ever say the kit is itself, which is how an
[inferred] magnitude survived three revisions.

PROVENANCE of `MACHINE`: `model/bd_excitation_probe.reference_range()` over
all 25 bass-drum files of `sounds-tr808-fischer` @ `85fbecf`
(`bd8/BD*.WAV`, 5 TONE x 5 DECAY), measured 2026-09-25. Reproduce with:

    git clone --depth 1 https://github.com/tidalcycles/sounds-tr808-fischer /tmp/tr808-ref
    python3 model/bd_excitation_probe.py --refs /tmp/tr808-ref

The four things these tests hold in place:

  1. THE APPARATUS. The (b) bound's replay harness must stay bit-exact against
     `DrumsFx`, and the band split must stay invariant to the excitation's
     LEVEL -- otherwise an arm that only got louder would read as an arm that
     changed shape.
  2. THE GAP IS STRUCTURAL. Our first 4 ms must still sit outside the machine's
     own 25-setting range on the low band by the margin measured, so nobody
     re-opens this as "a knob position".
  3. (a) CANNOT CLOSE IT. No non-negative exciter envelope brings the 20-80 Hz
     share anywhere near the machine's, because `v = 32767 * (ENV + ENV)` has
     its spectrum maximal at DC by construction.
  4. (a2) CLOSES IT AND BREAKS SOMETHING ELSE. The BD mode's unused numerator
     reaches the machine's range on every band, and the 23.9 dB of resonator
     gain it costs puts the decay inside the truncating biquad's deadband.
     Both halves are asserted, because the first half alone is exactly the
     "moved the number without being better" result issue #21 warns about.
"""
from __future__ import annotations
import math
import os
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "audition"))

import bd_excitation_probe as bx                                   # noqa: E402
import drums_fx as dx                                              # noqa: E402
from modal_fixed import RAW, BP                                    # noqa: E402

# HARDWARE-MEASURED, 25 files, sounds-tr808-fischer @ 85fbecf. Percent of the
# first 4 ms's energy per band of bd_excitation_probe.EDGES.
MACHINE = dict(
    lo=[0.29, 28.93, 43.18, 2.05, 0.05],
    median=[0.76, 41.16, 52.59, 5.60, 0.18],
    hi=[2.30, 52.66, 56.55, 11.19, 1.53],
)
# The published anchor for the single file section 8.3 quotes.
MACHINE_BD5050 = [0.61, 41.16, 52.33, 5.75, 0.15]


@pytest.fixture(scope="module")
def shipped():
    return bx.first4(bx.render(bx.bd_kit()), bx.SR)


# ---------------------------------------------------------- 1. apparatus ----
def test_the_bound_harness_replays_the_block_bit_for_bit():
    """The (b) arm substitutes an excitation into a fresh bank. If the
    unsubstituted replay is not identical to what `DrumsFx` produced, every
    bound it reports is about some other system. `BankReplay` refuses on its
    own, so reaching this assertion at all is most of the test."""
    rp = bx.BankReplay(dur_s=0.25)
    assert np.array_equal(rp.replay(rp.shipped_exc()), rp.body)


def test_the_band_split_cannot_be_moved_by_the_excitations_level_alone():
    """A share is a ratio, so a louder excitation must read identically. This
    is the control that stops "we made the attack louder" being reported as
    "we changed the attack's shape"."""
    rp = bx.BankReplay(dur_s=0.25)
    e = rp.shipped_exc()
    loud, quiet = rp.row(e), rp.row(e // 8)
    assert max(abs(a - b) for a, b in zip(loud, quiet)) < 1.0, (loud, quiet)


def test_meta_the_harness_notices_a_substituted_excitation():
    """The positive control for the two above: if the harness could not see a
    shape change either, they would pass on a dead instrument."""
    rp = bx.BankReplay(dur_s=0.25)
    base = rp.row(rp.shipped_exc())
    moved = rp.row(bx.shape_biphasic(8))
    assert abs(moved[bx.TARGET] - base[bx.TARGET]) > 5.0, (base, moved)


# ------------------------------------------------- 2. the gap is structural --
def test_our_first_4ms_is_outside_the_machines_whole_knob_range(shipped):
    """Not a knob position: the machine puts at most 2.30 % of its first 4 ms
    below 80 Hz at ANY of its 25 settings, and at least 43.18 % in 150-300.
    Ours is 73.4 % and 4.2 %."""
    assert shipped[bx.LOW] > 10 * MACHINE["hi"][bx.LOW], shipped
    assert shipped[2] < MACHINE["lo"][2] / 5.0, shipped
    assert not bx.inside(shipped, dict(lo=MACHINE["lo"], hi=MACHINE["hi"]))


def test_the_published_anchor_still_reproduces_on_our_side(shipped):
    """docs/drum-verification.md section 8.3's "ours, with section 2's attack
    window" row. If this moves, the kit moved and every comparison in issue
    #21's PR is stale."""
    assert shipped[bx.TARGET] == pytest.approx(bx.PUBLISHED_OURS, abs=0.5), shipped


# ------------------------------------------ 3. (a) the exciter envelope ------
@pytest.mark.parametrize("tau_s,hold", [(0.1e-3, 0), (1e-3, 0), (4e-3, 0),
                                        (0.1e-3, 48), (0.1e-3, 192)])
def test_no_non_negative_exciter_envelope_reaches_the_machines_low_band(tau_s, hold):
    """`v = 32767 * (ENV(e1) + ENV(e2)) >> (15 + att)` cannot be negative, so
    its spectrum is maximal at DC and it cannot tilt the excitation upward in
    frequency relative to the near-impulse that already ships.

    The measurable consequence, and the one this asserts: no setting of it
    brings the 20-80 Hz share near the machine's 0.29-2.30 %. tau = 4 ms does
    raise the 80-150 column to 34 %, which is why the LOW band is the
    assertion -- that arm leaves 49 % below 80 Hz, still 21x the machine's
    largest, because what moved was the exciter still being on when the attack
    window closes, not the pulse's spectrum."""
    row = bx.first4(bx.render(bx.bd_kit(tau_s=tau_s, hold=hold)), bx.SR)
    assert row[bx.LOW] > 10 * MACHINE["hi"][bx.LOW], (tau_s, hold, row)


def test_adding_a_second_envelope_segment_costs_an_envelope_and_buys_nothing():
    """Approach (a) as issue #21 proposes it. Two things are asserted: that
    all 18 envelopes are already read by a path (so this is a 19th envelope of
    real area, not a register change), and that spending it does not move the
    measurement."""
    kit = dx.kit_808()
    sp = bx.spare_env(kit)
    assert sp == dx.N_ENV, f"an envelope is spare ({sp}); (a)'s cost has changed"
    kit = bx._set_env(kit, sp, dx.BD, 4e-3, 0.06)
    kit = bx._set_path(kit, dx.P_BDX,
                       dx.path_word(dx.SRC_PULSE, dx.E_BDX, sp, dest=dx.M_BD))
    row = bx.first4(bx.render(kit, envs=sp + 1), bx.SR)
    assert row[bx.LOW] > 10 * MACHINE["hi"][bx.LOW], row


# --------------------------------- 4. (a2) the numerator: both halves --------
def test_a_bipolar_excitation_reaches_the_machines_range_on_every_band():
    """The BD's resonator has an unused numerator register (`M_BD` is mode 8,
    `N_NUMS` is 11). `BP` injects `e[n] - e[n-2]` -- an AC-coupled biphasic
    pulse -- for one register write and no new hardware, and that alone puts
    all five bands inside the range the machine covers over its own 25
    settings."""
    amp = bx.calibrate_amp(BP, 1.0, 0)
    row = bx.first4(bx.render(bx.bd_kit(BP, amp, 1.0)), bx.SR)
    assert bx.inside(row, dict(lo=MACHINE["lo"], hi=MACHINE["hi"])), row


def test_and_it_costs_the_resonator_24_dB_of_state_which_is_why_it_cannot_ship():
    """The other half, and the reason issue #21 closes without a kit change.

    `BP` has a zero at DC, so the 49.4 Hz ring it produces is ~16x weaker for
    the same excitation; the level has to come back through the mode's output
    `amp`, which does not touch the STATE. The state therefore runs 23.9 dB
    smaller inside a biquad that truncates (`acc >> CF`, floor) at a pole of
    r = 0.99993 -- and a truncating biquad settles into a DC deadband of fixed
    STATE size, so its level relative to the signal grows by exactly that
    24 dB.

    Asserted as measured: the gain demand, and a decay that is no longer
    monotone."""
    amp = bx.calibrate_amp(BP, 1.0, 0)
    assert amp / bx.KIT_AMP > 10.0, amp
    assert 20 * math.log10(amp / bx.KIT_AMP) > 20.0, amp
    st = bx.body_stats(bx.render(bx.bd_kit(BP, amp, 1.0), dur_s=bx.TAIL_DUR_S))
    assert st["f0"] == pytest.approx(bx.BD_F0, rel=0.01), st
    assert st["drise"] > 3.0, ("the decay must be visibly non-monotone, which is "
                               "the disqualifier; if this fails the bank's "
                               "arithmetic has been repaired and #21 can reopen", st)


def test_meta_the_shipped_kit_passes_the_decay_check_the_numerator_arm_fails():
    """The positive control for the check above: a decay-monotonicity bound
    that nothing could satisfy would disqualify every arm including the one
    that ships, and would look exactly like evidence."""
    st = bx.body_stats(bx.render(bx.bd_kit(), dur_s=bx.TAIL_DUR_S))
    assert st["drise"] < 1.0, st
    assert st["f0"] == pytest.approx(bx.BD_F0, rel=0.01), st
    assert st["tau_ms"] == pytest.approx(bx.BD_TAU_MS, rel=0.05), st


def test_the_shipped_kit_has_a_dc_pedestal_too_and_the_numerator_arm_is_worse():
    """The blocker, stated as the comparison that decides it. The shipped kit
    already settles into a DC pedestal -- worst -40.2 dB below peak over
    accents 0.5-2.0 -- because the same biquad truncates today. The numerator
    arm's is -19.4 dB. This test is why #21's follow-up is filed against
    `modal_fixed` (#220), not against the kit."""
    ship = max(bx.pedestal_over_accents(bx.bd_kit()))
    amp = bx.calibrate_amp(BP, 1.0, 0)
    bp = max(bx.pedestal_over_accents(bx.bd_kit(BP, amp, 1.0)))
    assert ship < -35.0, ship
    assert bp > ship + 10.0, (ship, bp)


# ----------------------------------------------------- 5. the bound ----------
def test_the_target_is_reachable_by_shape_alone_so_the_mechanism_is_right():
    """The (b) bound. A biphasic pulse a third of a millisecond wide, driven
    at full excitation amplitude rather than paid for out of the mode's gain,
    lands on the machine's median without any of (a2)'s cost. That is what
    says the mechanism is the excitation's shape and the blocker is the
    arithmetic -- and what a shaped SOURCE in 15.4 would be aiming at."""
    rp = bx.BankReplay(dur_s=0.25)
    row = rp.row(bx.shape_biphasic(8))
    assert row[bx.LOW] < MACHINE["hi"][bx.LOW], row
    assert row[bx.TARGET] > 30.0, row
    assert row[2] > 30.0, row

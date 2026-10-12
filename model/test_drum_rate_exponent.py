#!/usr/bin/env python3
"""#656: the drum envelope RATE / FRATE exponent (contract 15.3, revision 17).

The Q0.16 rate register's slowest non-zero decay is rate 1, tau = 2^16 / SR =
1.3653 s, and one step above it (rate 2) halves tau. The cymbal's DECAY knob
asked E_CYL for 1.0155 s and 1.2732 s and got 1.3653 s for both, so two knob
positions shared one envelope. Revision 17 adds an exponent field e above the
16-bit mantissa m:

    RATE = (e << 16) | m        dec = (level * m) >> (16 + e)

e = 0 is the old register bit for bit, so every existing write keeps its
meaning.

Every known answer here is computed from the register code by hand, not from
`drums_fx.rate_code`: the time constant of a code is a property of the
arithmetic, tau = -1 / (SR ln(1 - m / 2^(16 + e))), and the tests measure it
off the simulated level. Codes are written through `DrumsFx.write`, the
production register decode, never by assigning `EnvFx.rate`.
"""
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "audition"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pytest

import drums_fx as dx
from dsp import SR

FULL24 = (1 << 24) - 1
ACC = [32768] * dx.N_STOPS
E = dx.E_CYL                       # any envelope; the cymbal's low band is the one #656 is about


def _env_via_register(rate_code, frate_code=0, ctl=None, peak=FULL24, e=E):
    d = dx.DrumsFx()
    base = dx.A_ENV + e * dx.ENV_STRIDE
    d.write(base, dx.env_ctl(0) if ctl is None else ctl)
    d.write(base + 1, peak)
    d.write(base + 2, rate_code)
    d.write(base + 3, frate_code)
    return d.envs[e]


def _measured_tau(env, frames):
    """Fire, run, and fit tau off the level between -6 dB and -40 dB of the
    peak: ln(level) against frame, least squares. No formula from the model."""
    env.frame(1, ACC)
    pk = env.level
    xs, ys = [], []
    for i in range(1, frames):
        env.frame(0, ACC)
        if env.level <= 0:
            break
        r = env.level / pk
        if 10 ** (-40 / 20) <= r <= 10 ** (-6 / 20):
            xs.append(i)
            ys.append(math.log(env.level))
    assert len(xs) > 20, "the decay never crossed the fit window"
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)
    return -1.0 / (slope * SR)


def _tau_of(e, m):
    return -1.0 / (SR * math.log(1.0 - m / 2.0 ** (16 + e)))


# ---- the defect, as the failing regression it was --------------------------------
@pytest.mark.parametrize("asked", [1.0155, 1.2732])
def test_the_two_slow_decay_positions_get_the_time_constant_they_ask_for(asked):
    """DECAY 7.5 and 10.0's E_CYL asks. Q0.16 gave both 1.3653 s (rate 1).
    Code by hand: e = 7, m = round(alpha * 2^23)."""
    alpha = 1.0 - math.exp(-1.0 / (asked * SR))
    m = round(alpha * 2 ** 23)
    code = (7 << 16) | m
    got = _measured_tau(_env_via_register(code), int(6 * asked * SR))
    assert abs(got / asked - 1.0) < 0.01, (asked, got)


def test_the_slow_positions_are_distinct_envelopes():
    a = _measured_tau(_env_via_register((7 << 16) | round((1 - math.exp(-1 / (1.0155 * SR))) * 2 ** 23)),
                      int(7 * SR))
    b = _measured_tau(_env_via_register((7 << 16) | round((1 - math.exp(-1 / (1.2732 * SR))) * 2 ** 23)),
                      int(8 * SR))
    assert b / a - 1.0 > 0.2, (a, b)          # the 25 % DECAY step survives as a ~25 % tau step


# ---- the contract's corners -----------------------------------------------------
def test_exponent_zero_is_the_q016_register_bit_for_bit():
    """Every legacy code (e = 0) produces the same level sequence the old
    arithmetic does: level -= max(1, (level * rate) >> 16)."""
    for rate in (0, 1, 2, 3, 9, 4466, 53248, 65535):
        env = _env_via_register(rate)
        env.frame(1, ACC)
        lvl = env.level
        for _ in range(3000):
            env.frame(0, ACC)
            dec = (lvl * rate) >> 16
            lvl = max(0, lvl - (dec if dec else 1))
            assert env.level == lvl, rate


@pytest.mark.parametrize("e,m", [(1, 40000), (3, 5000), (7, 65535), (7, 151), (5, 32768)])
def test_measured_time_constant_matches_the_code(e, m):
    want = _tau_of(e, m)
    got = _measured_tau(_env_via_register((e << 16) | m), int(8 * want * SR) + 100)
    assert abs(got / want - 1.0) < 0.01, (e, m, want, got)


def test_largest_and_smallest_codes():
    """Largest: e = 7, m = 65535 -> dec = (L * 65535) >> 23, a factor of about
    1 - 1/128 per frame. Smallest non-zero: e = 7, m = 1 -> the product is
    below one LSB for every level < 2^23, so the floor's one LSB per frame
    governs. Mantissa 0 at any exponent is the same as RATE = 0: one LSB per
    frame, never stuck (the voice's dead-zone rule, 8.3)."""
    env = _env_via_register((7 << 16) | 65535)
    env.frame(1, ACC)
    l0 = env.level
    env.frame(0, ACC)
    assert env.level == l0 - ((l0 * 65535) >> 23)
    for code in ((7 << 16) | 1, (7 << 16) | 0, (3 << 16) | 0, 0):
        env = _env_via_register(code, peak=1 << 20)
        env.frame(1, ACC)
        l0 = env.level
        for k in range(1, 50):
            env.frame(0, ACC)
            assert env.level == l0 - k, hex(code)


def test_register_holds_19_bits_and_ignores_the_rest():
    """RATE and FRATE are 19 bits wide (REG_BITS); bits 31..19 are not stored,
    exactly as bits 31..16 were not stored before revision 17."""
    assert dx.REG_BITS["rate"] == dx.REG_BITS["frate"] == 19
    d = dx.DrumsFx()
    base = dx.A_ENV + E * dx.ENV_STRIDE
    d.write(base + 2, 0xFFF80000 | (5 << 16) | 1234)
    d.write(base + 3, 0x80000 | 7)
    assert d.envs[E].rate == (5 << 16) | 1234
    assert d.envs[E].frate == 7


def test_frate_exponent_runs_the_final_decay_and_any_nonzero_code_enables_it():
    """The clap's final strike (revision 14) with an exponent on FRATE: after
    the last re-strike the decay runs at the FRATE code's time constant. A code
    whose mantissa is 0 but whose exponent is not is still non-zero, so it
    enables the final strike (and then decays one LSB per frame)."""
    ctl = dx.env_ctl(0, bursts=1, period=48)
    slow = (6 << 16) | round((1 - math.exp(-1 / (0.5 * SR))) * 2 ** 22)
    env = _env_via_register(dx.rate_reg(2e-3), frate_code=slow, ctl=ctl)
    env.frame(1, ACC)
    fire = env.level
    for _ in range(48):
        env.frame(0, ACC)
    assert env.level == fire and env.n_final == 1          # the final strike restored the fire level
    lv = [env.level]
    for _ in range(int(0.5 * SR)):
        env.frame(0, ACC)
        lv.append(env.level)
    ratio = lv[-1] / lv[0]
    assert abs(ratio / math.exp(-1.0) - 1.0) < 0.02, ratio    # one tau of FRATE's 0.5 s

    env = _env_via_register(dx.rate_reg(2e-3), frate_code=(3 << 16) | 0, ctl=ctl)
    env.frame(1, ACC)
    for _ in range(48):
        env.frame(0, ACC)
    assert env.n_final == 1                                    # enabled by the exponent alone
    l0 = env.level
    env.frame(0, ACC)
    assert env.level == l0 - 1


def test_retrigger_and_choke_ignore_the_exponent():
    """A re-fire restarts from the accent-scaled peak and a choke zeroes the
    level, whatever the code: the exponent only enters the decay product."""
    ctl = dx.env_ctl(0, choke=1)
    env = _env_via_register((7 << 16) | 200, ctl=ctl)
    env.frame(1, ACC)
    fire = env.level
    for _ in range(1000):
        env.frame(0, ACC)
    assert env.level < fire
    env.frame(1, ACC)
    assert env.level == fire
    env.frame(1 << 1, ACC)
    assert env.level == 0


# ---- the host conversion ----------------------------------------------------------
def test_rate_code_with_no_exponent_is_rate_reg():
    """The encoder at exp_bits = 0 is the historical host conversion exactly,
    so the floor can be reinstated as a mutant by one argument."""
    for tau in (0.0, 1e-4, 2e-3, 0.012, 0.14, 0.5, 1.0, 1.3653, 5.0, 60.0):
        assert dx.rate_code(tau, exp_bits=0) == dx.rate_reg(tau), tau


def test_rate_code_realises_every_cymbal_decay_ask_within_tolerance():
    """Every DECAY ask of E_CYL and E_CYD, at 101 wiper positions, within the
    5 % tolerance the sensitivity plan froze (ISO 3382-1 EDT JND); and the old
    register does not (the floor)."""
    import cymbal_candidate as cc
    worst = {0: 0.0, dx.RATE_EXP_BITS: 0.0}
    for e, tau in cc.SHIPPED_ENV_TAU.items():
        anchor = dx.rate_code_tau(dx.rate_reg(tau))
        for i in range(101):
            r = (cc.DECAY_R93_OHM + i / 100 * cc.DECAY_VR2_OHM) / cc.decay_timing_ohm(cc.DECAY_ANCHOR)
            ask = anchor * r
            for bits in worst:
                got = dx.rate_code_tau(dx.rate_code(ask, exp_bits=bits))
                worst[bits] = max(worst[bits], abs(got / ask - 1.0))
    assert worst[dx.RATE_EXP_BITS] <= 0.05, worst
    assert worst[0] > 0.3, worst                     # the defect, still visible through the mutant


def test_rate_code_tau_matches_the_simulated_envelope():
    """The diagnostic's realised tau is the arithmetic's, not a second model."""
    for tau in (0.07, 0.3, 1.1):
        code = dx.rate_code(tau)
        got = _measured_tau(_env_via_register(code), int(8 * tau * SR) + 100)
        assert abs(got / dx.rate_code_tau(code) - 1.0) < 0.01, (tau, got)

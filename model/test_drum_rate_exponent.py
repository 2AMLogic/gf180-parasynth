#!/usr/bin/env python3
"""#656: the drum envelope RATE / FRATE exponent (contract 15.3, revision 18).

The Q0.16 rate register's slowest non-zero decay is rate 1, tau = 2^16 / SR =
1.3653 s, and one step above it (rate 2) halves tau. The cymbal's DECAY knob
asked E_CYL for 1.0155 s and 1.2732 s and got 1.3653 s for both, so two knob
positions shared one envelope. Revision 18 adds an exponent field e above the
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
EMAX = (1 << dx.RATE_EXP_BITS) - 1   # the largest exponent the field holds (7 at RATE_EXP_BITS = 3)
TOL = 0.05                         # the sensitivity plan's frozen tau tolerance (ISO 3382-1 EDT JND)


def _env_via_register(rate_code, frate_code=0, ctl=None, peak=FULL24, e=E):
    d = dx.DrumsFx()
    base = dx.A_ENV + e * dx.ENV_STRIDE
    d.write(base, dx.env_ctl(0) if ctl is None else ctl)
    d.write(base + 1, peak)
    d.write(base + 2, rate_code)
    d.write(base + 3, frate_code)
    return d.envs[e]


def _measured_tau(env, frames):
    """Fire, run, and fit tau off the level between -3 dB and -20 dB of the
    peak: ln(level) against frame, least squares. No formula from the model.
    Not lower: below about -20 dB the product (level * m) >> (16 + e) is a few
    LSB and its truncation makes the integer decay run several percent slower
    than any code's nominal tau (docs/sensitivity/drum-rate-exp-bits.json,
    tail_slow_pct) -- a property of the envelope since revision 1."""
    env.frame(1, ACC)
    pk = env.level
    xs, ys = [], []
    for i in range(1, frames):
        env.frame(0, ACC)
        if env.level <= 0:
            break
        r = env.level / pk
        if 10 ** (-20 / 20) <= r <= 10 ** (-3 / 20):
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
    Code by hand: the largest exponent, m = round(alpha * 2^(16 + e))."""
    assert EMAX >= 1, "no exponent field: the Q0.16 floor is back"
    alpha = 1.0 - math.exp(-1.0 / (asked * SR))
    m = round(alpha * 2 ** (16 + EMAX))
    code = (EMAX << 16) | m
    got = _measured_tau(_env_via_register(code), int(6 * asked * SR))
    assert abs(got / asked - 1.0) < TOL, (asked, got)


def test_the_slow_positions_are_distinct_envelopes():
    k = 2 ** (16 + EMAX)
    a = _measured_tau(_env_via_register((EMAX << 16) | round((1 - math.exp(-1 / (1.0155 * SR))) * k)),
                      int(7 * SR))
    b = _measured_tau(_env_via_register((EMAX << 16) | round((1 - math.exp(-1 / (1.2732 * SR))) * k)),
                      int(8 * SR))
    assert b / a - 1.0 > 0.15, (a, b)         # the 25 % DECAY step survives as a tau step


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


@pytest.mark.parametrize("e,m", [(1, 2000), (3, 5000), (3, 20000), (3, 10), (2, 300)])
def test_measured_time_constant_matches_the_code(e, m):
    assert e <= EMAX
    want = _tau_of(e, m)
    got = _measured_tau(_env_via_register((e << 16) | m), int(8 * want * SR) + 100)
    assert abs(got / want - 1.0) < 0.01, (e, m, want, got)


def test_largest_and_smallest_codes():
    """Largest: e = EMAX, m = 65535 -> dec = (L * 65535) >> (16 + EMAX).
    Smallest non-zero: e = EMAX, m = 1 -> the product is below one LSB for
    every level < 2^(16 + EMAX), so the floor's one LSB per frame governs.
    Mantissa 0 at any exponent is the same as RATE = 0: one LSB per frame,
    never stuck (the voice's dead-zone rule, 8.3)."""
    env = _env_via_register((EMAX << 16) | 65535)
    env.frame(1, ACC)
    l0 = env.level
    env.frame(0, ACC)
    assert env.level == l0 - ((l0 * 65535) >> (16 + EMAX))
    for code in ((EMAX << 16) | 1, (EMAX << 16) | 0, (1 << 16) | 0, 0):
        env = _env_via_register(code, peak=1 << (15 + EMAX))   # below 2^(16 + EMAX)
        env.frame(1, ACC)
        l0 = env.level
        for k in range(1, 50):
            env.frame(0, ACC)
            assert env.level == l0 - k, hex(code)


def test_register_holds_16_plus_exponent_bits_and_ignores_the_rest():
    """RATE and FRATE are 16 + RATE_EXP_BITS wide (REG_BITS);
    the bits above are not stored, exactly as bits 31..16 were not stored
    before revision 18."""
    w = 16 + dx.RATE_EXP_BITS
    assert dx.REG_BITS["rate"] == dx.REG_BITS["frate"] == w
    d = dx.DrumsFx()
    base = dx.A_ENV + E * dx.ENV_STRIDE
    d.write(base + 2, (0xFFFFFFFF << w) & 0xFFFFFFFF | (EMAX << 16) | 1234)
    d.write(base + 3, (1 << w) | 7)
    assert d.envs[E].rate == (EMAX << 16) | 1234
    assert d.envs[E].frate == 7


def test_frate_exponent_runs_the_final_decay_and_any_nonzero_code_enables_it():
    """The clap's final strike (revision 14) with an exponent on FRATE: after
    the last re-strike the decay runs at the FRATE code's time constant. A code
    whose mantissa is 0 but whose exponent is not is still non-zero, so it
    enables the final strike (and then decays one LSB per frame)."""
    ctl = dx.env_ctl(0, bursts=1, period=48)
    slow = (EMAX << 16) | round((1 - math.exp(-1 / (0.5 * SR))) * 2 ** (16 + EMAX))
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
    want = math.exp(-0.5 / _tau_of(EMAX, slow & 0xFFFF))
    assert abs(ratio / want - 1.0) < 0.02, (ratio, want)      # FRATE's code, not RATE's 2 ms

    env = _env_via_register(dx.rate_reg(2e-3), frate_code=(EMAX << 16) | 0, ctl=ctl)
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
    env = _env_via_register((EMAX << 16) | 200, ctl=ctl)
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


# ---- the RTL carries the same width ------------------------------------------------
def test_rtl_rate_exponent_width_is_the_models():
    """drum_dp / drum_regs / drum_kit / synth_top / tb_drums default RATE_EB to
    the model's RATE_EXP_BITS. A mismatch would be bit-exact on every e = 0
    code and wrong only on the slow DECAY settings, so it is pinned here."""
    import pathlib
    import re
    rtl = pathlib.Path(__file__).resolve().parents[1] / "rtl-sketch"
    for name in ("drum_dp.v", "drum_regs.v", "drum_kit.v", "synth_top.v", "tb_drums.v"):
        hits = re.findall(r"parameter\s+RATE_EB\s*=\s*(\d+)", (rtl / name).read_text())
        assert hits == [str(dx.RATE_EXP_BITS)], (name, hits)

#!/usr/bin/env python3
"""Regression tests for the integer drum section (model/drums_fx.py) and the
modal bank's extension for it (per-mode excitation, numerators). They lock
the numbers the design was sized from and the semantics the contract's
section 15 states, in the spirit of test_voice_fx.py.
"""
import os, sys, math
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "audition"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pytest
import drums_fx as dx
import modal_fixed
from modal_fixed import ModalFx, RAW, BP, HP, pole_regs
import voice_fx as vf
import fixed
from dsp import SR

FULL24 = (1 << 24) - 1


def _solo(stop, seconds=0.3, accent=1.0, kit=None, extra=()):
    d = dx.DrumsFx()
    n = int(seconds * SR)
    w = sorted(dx.hit_writes([(10, stop, accent)], dx.kit_808() if kit is None else kit) + list(extra), key=lambda t: t[0])
    dm, bd = d.play(w, n)
    return d, dm, bd


# ---- the noise source (15.4) -------------------------------------------------
def test_lfsr_polynomial_is_primitive_and_the_leap_equals_the_serial_form():
    """x^31 + x^15 + x^13 + x^11 + 1 (delays 31, 16, 18, 20: state bits 30,
    15, 17, 19) is irreducible over GF(2) -- x^(2^31) = x mod p and no root
    -- and 2^31 - 1 is prime, so it is primitive: the period is 2^31 - 1
    bits. The 16-bits-at-once form the RTL uses is the serial form, step
    for step, and needs only the old state because every tap is at bit 15
    or above."""
    P = (1 << 31) | (1 << 15) | (1 << 13) | (1 << 11) | 1
    assert dx.LFSR_TAPS == (30, 15, 17, 19) and min(dx.LFSR_TAPS) >= dx.NOISE_BITS - 1

    def mulmod(a, b):
        r = 0
        while b:
            if b & 1:
                r ^= a
            b >>= 1
            a <<= 1
            if (a >> 31) & 1:
                a ^= P
        return r
    y = 2
    for _ in range(31):
        y = mulmod(y, y)
    assert y == 2 and P & 1 and bin(P).count("1") % 2 == 1
    s = dx.LFSR_SEED
    for _ in range(4000):
        a, b = dx.lfsr_frame(s), dx.lfsr_frame_leap(s)
        assert a == b
        s = a[0]
    assert dx.lfsr_frame(dx.LFSR_SEED)[1] == 1          # the seed's bit reaches tap 15 on step 16


def test_noise_is_white_and_full_scale():
    """Two seconds of words from reset: mean within 150 of 0 (a uniform
    source's standard error is 61 over 96 000 words; the trinomial the
    strawman used measured +351 here, its sparse-seed recovery), RMS that
    of a uniform Q1.15 variable (32768 / sqrt 3), and no autocorrelation
    above 2 % at lags 1..64 -- what a 16-bit window of an m-sequence per
    frame gives, and what reading the low bits of a once-per-frame LFSR
    (dsp.lfsr_noise) does not."""
    n = 2 * SR
    s, out = dx.LFSR_SEED, np.empty(n)
    for i in range(n):
        s, out[i] = dx.lfsr_frame(s)
    assert abs(out.mean()) < 150
    assert abs(out.std() / (32768 / math.sqrt(3)) - 1.0) < 0.02
    x = out - out.mean()
    for lag in (1, 2, 3, 7, 16, 64):
        assert abs(np.dot(x[:-lag], x[lag:]) / np.dot(x, x)) < 0.02, lag


# ---- stops (15.2) ----------------------------------------------------------------
def test_stops_fire_on_the_edge_between_frames_only():
    """A stop fires when its bit is 1 at a frame's start and was 0 at the
    previous frame's start: a held bit fires once, 1-0-1 fires twice, a
    rewrite of 1 does not fire, and two writes in one frame are one value."""
    d = dx.DrumsFx()
    kit = dx.kit_808()
    w = [(0, a, v) for a, v in kit]
    w += [(5, dx.A_STOPS, 1), (6, dx.A_STOPS, 1), (7, dx.A_STOPS, 1), (20, dx.A_STOPS, 0),
          (30, dx.A_STOPS, 1), (31, dx.A_STOPS, 0), (32, dx.A_STOPS, 1), (33, dx.A_STOPS, 0),
          (40, dx.A_STOPS, 0), (40, dx.A_STOPS, 1), (41, dx.A_STOPS, 0),
          (50, dx.A_STOPS, 1), (50, dx.A_STOPS, 0)]
    d.play(w, 60)
    fires = [f for f in range(60) if d.trace["fire"][f] & 1]
    assert fires == [5, 30, 32, 40]


# ---- envelopes (15.3) --------------------------------------------------------------
@pytest.mark.parametrize("tau", [0.004, 0.02, 0.15, 0.25])
def test_envelope_reaches_exactly_zero_and_the_dead_zone_is_closed(tau):
    """The voice's release rule (8.3) and its dead zone: below level = 2^16 /
    rate the product truncates to zero. Without the max(1, .) the level
    stalls there forever; with it the tail is one LSB per frame and reaches
    exactly 0, and the floor is below -60 dBFS for every tau up to 0.25 s."""
    for floor in (True, False):
        e = dx.EnvFx(floor=floor)
        e.peak, e.rate = FULL24, dx.rate_reg(tau)
        e.set_ctl(dx.env_ctl(0))
        e.frame(1, [32768] * 8)
        levels = []
        for _ in range(int(20 * tau * SR) + 200000):
            e.frame(0, [0] * 8)
            levels.append(e.level)
            if e.level == 0:
                break
        if floor:
            assert e.level == 0
            assert 20 * math.log10(e.floor_level / (1 << 24)) < -60.0
            assert min(l for l in levels if l) == 1                 # the linear tail ends at 1 then 0
        else:
            assert e.level > 0 and e.level < e.floor_level           # stalled inside the dead zone
            assert levels[-1] == levels[-1000]


def test_envelope_hold_bursts_and_choke():
    """hold = 48 keeps the fired level for 48 frames (the 1 ms pulse); bursts
    re-strike at period and 2 x period at 13/16 of the last strike, whatever
    the level has decayed to; a choke zeroes the level at once."""
    acc = [32768] * 8
    e = dx.EnvFx(); e.peak, e.rate = FULL24, 65535; e.set_ctl(dx.env_ctl(0, hold=48))
    e.frame(1, acc)
    lv = [e.level]
    for _ in range(60):
        e.frame(0, acc); lv.append(e.level)
    assert lv[:48] == [FULL24] * 48 and lv[48] == 256 and lv[49] == 1 and lv[50] == 0   # 65535: three updates
    e = dx.EnvFx(); e.peak, e.rate = FULL24, dx.rate_reg(0.004); e.set_ctl(dx.env_ctl(0, bursts=2, period=480))
    e.frame(1, acc)
    lv = [e.level]
    for _ in range(1500):
        e.frame(0, acc); lv.append(e.level)
    assert lv[480] == (FULL24 * 53248) >> 16 and lv[960] == (((FULL24 * 53248) >> 16) * 53248) >> 16
    assert lv[479] < lv[480] // 8 and lv[1440] < lv[960]                    # decayed in between; no fourth
    assert e.n_restrike == 2
    e = dx.EnvFx(); e.peak, e.rate = FULL24, 9; e.set_ctl(dx.env_ctl(5, choke=4))
    e.frame(1 << 5, acc); e.frame(0, acc)
    assert e.level > FULL24 // 2
    e.frame(1 << 4, acc)
    assert e.level == 0 and e.n_choke == 1


def test_accent_scales_the_strike_and_clamps_at_24_bits():
    """level = usat24((peak * accent) >> 15): accent 1.0 is the peak, 0.5
    half, and 2.0 on a full peak clamps to 2^24 - 1."""
    for accent, expect in ((32768, FULL24), (16384, FULL24 >> 1), (65535, FULL24), (0, 0)):
        e = dx.EnvFx(); e.peak = FULL24; e.set_ctl(dx.env_ctl(3))
        e.frame(1 << 3, [0, 0, 0, accent, 0, 0, 0, 0])
        assert e.level == expect, accent
    e = dx.EnvFx(); e.peak = 1000; e.set_ctl(dx.env_ctl(0))
    e.frame(1, [65535] * 8)
    assert e.level == (1000 * 65535) >> 15


# ---- paths, sources, nonlinearities, taps (15.5) --------------------------------------
def test_path_sums_are_exact_and_bounded():
    """Sixteen paths of PULSE x (FULL + FULL) into the mix bus: 16 x 65533,
    exact, no clamp; into one mode: the same into that mode's excitation,
    and the bank's 28-bit state clamp catches it, not a bus."""
    d = dx.DrumsFx()
    w = [(0, dx.A_PATH + p, dx.path_word(dx.SRC_PULSE, dx.ENV_FULL, dx.ENV_FULL, dest=dx.DEST_MIX)) for p in range(16)]
    dm, bd = d.play(w, 3)
    assert dm[0] == 16 * ((32767 * 65534) >> 15) == 16 * 65532 < (1 << 20)
    d = dx.DrumsFx()
    w = [(0, dx.A_PATH + p, dx.path_word(dx.SRC_PULSE, dx.ENV_FULL, dx.ENV_FULL, dest=3)) for p in range(16)]
    dm, bd = d.play(w, 3)
    assert d.trace["exc"][0, 3] == 16 * 65532 and dm[0] == 0
    assert d.bank.y1[3] == 16 * 65532                                         # a1 = a2 = 0: y = x, each frame                                     # a1 = a2 = 0: the sum, three frames


def test_nonlinearities_are_the_ladders_tanh_on_the_swing_and_plain():
    """LIN passes the source; TANH is tanh(x) with 1.0 at tanh(1.0) = 0.76
    (the ladder's table, Appendix C, on x << 5); SWING is x4 on the positive
    half -- 32767 x 4 is one LSB short of the clamp and interpolates to
    32766 -- and /8 on the negative, so the two halves differ by 32x in
    small-signal gain."""
    d = dx.DrumsFx()
    lad = fixed.LadderFx(**vf.LADDER_CFG)
    for x in (0, 1, 100, 4096, 8192, 16384, 32767, -1, -100, -4096, -32768):
        assert d._nonlinear(x, dx.NL_LIN) == x
        assert d._nonlinear(x, dx.NL_TANH) == lad.tanh_fx(x << 5)
        u = (x << 2) if x > 0 else (x >> 3)
        assert d._nonlinear(x, dx.NL_SWING) == lad.tanh_fx(fixed.sat(u << 5, 24))
    assert d._nonlinear(32767, dx.NL_SWING) == 32766 and d._nonlinear(-32768, dx.NL_SWING) == lad.tanh_fx(-4096 << 5)
    assert d._nonlinear(32768 // 2, dx.NL_TANH) == lad.tanh_fx(1 << 19)      # 0.5 -> tanh(0.5) = 0.46
    assert abs(d._nonlinear(1000, dx.NL_SWING)) > 30 * abs(d._nonlinear(-1000, dx.NL_SWING))


def test_tap_is_the_mode_state_over_eight_with_a_rail():
    """TAP m reads sat16(y1[m] >> 3): a mode ringing at 8.0 x full scale
    reads as the rail. Set the state directly and read it through a path
    with the full-scale envelope (index 15 = 32767, so v = tap * 32767 >> 15)."""
    for y1, tap in ((32768, 4096), (-32768 * 8, -32768), (32767 * 8 + 7, 32767), (32767 * 64, 32767),
                    (-(1 << 27), -32768), (-9, -2), (16, 2)):
        d = dx.DrumsFx()
        d.write(dx.A_PATH, dx.path_word(dx.SRC_TAP + 7, dx.ENV_FULL, dest=dx.DEST_MIX))
        d.bank.y1[7] = y1
        dm, _, *_ = d.frame()
        assert dm == (tap * 32767) >> 15, y1


def test_sources_are_what_the_contract_says():
    """PULSE is 32767; SQSUM is the six squares at +-5461 each (a 7-level
    staircase from 0 phases = +32766); SQPAIR is squares 4 and 5 at +-16383;
    SQ i is square i ALONE at +-16383 (src 5..10), so SQ 4 + SQ 5 is SQPAIR
    term for term and the cowbell's two gates cost no level; OFF and every
    unassigned code are 0; envelope index ENV_FULL reads full scale (32767, so
    a path value is the source x 32767 >> 15) and any other index at or above
    N_ENV reads zero."""
    d = dx.DrumsFx()
    F, Z = dx.ENV_FULL, dx.ENV_NONE
    srcs = ((dx.SRC_PULSE, F), (dx.SRC_SQSUM, F), (dx.SRC_SQPAIR, F), (dx.SRC_OFF, F),
            (11, F), (dx.SRC_PULSE, Z), (dx.SRC_PULSE, dx.N_ENV), (dx.SRC_NOISE, F),
            (dx.SRC_SQ + 4, F), (dx.SRC_SQ + 5, F))
    for p, (src, e1) in enumerate(srcs):
        d.write(dx.A_PATH + p, dx.path_word(src, e1, dest=p))
    d.phase[5] = 1 << 23                                                      # square 5 low
    _, _, _, noise, sqsum, exc, vals = d.frame()
    assert vals[:7] == [(v * 32767) >> 15 for v in (32767, 4 * 5461)] + [0] * 5 and sqsum == 4 * 5461
    assert noise == 1 and vals[7] == (noise * 32767) >> 15 == 0             # the seed's first word, x 32767 >> 15
    lone = [(v * 32767) >> 15 for v in (dx.SQPAIR_STEP, -dx.SQPAIR_STEP)]
    assert vals[8:10] == lone, vals[8:10]
    # SQ 4 + SQ 5 is SQPAIR at the SOURCE, so splitting the cowbell into two
    # gated paths costs no level. The path values can differ by one LSB
    # because `>> 15` floors, and floor(a) + floor(b) != floor(a + b) when the
    # two have opposite signs -- which is the only difference the split makes
    # to a LINEAR path, and is why the cowbell's own test measures the
    # difference tone rather than the sample values.
    assert abs(vals[8] + vals[9] - vals[2]) <= 1, (vals[8], vals[9], vals[2])


# ---- the bank's extension (15.6) -----------------------------------------------------
def test_numerators_reject_dc_and_the_resonator_passes_it():
    """BP (1 - z^-2) and HP (1 - z^-1)^2 on a DC excitation settle to 0
    within the floor rounding's residue (the recursion floors toward minus
    infinity, so a few LSB of state, -13 here, persist); RAW settles to
    DC gain 1 / (1 - a1 - a2). Modes at or above `nums` are RAW whatever
    num says."""
    m = ModalFx(modes=4, nums=2, headroom=0)
    coefs = [pole_regs(2000.0, 2.0) + (65535,)] * 4
    y = m.process(np.full(2000, 4096, np.int16), coefs, [BP, HP, HP, RAW])
    y1 = list(m.y1)
    assert abs(y1[0]) <= 16 and abs(y1[1]) <= 16
    a1, a2 = coefs[0][:2]
    dc = 4096 / (1 - (a1 + a2) / (1 << 24))
    assert abs(y1[2] - dc) < 0.01 * dc and abs(y1[3] - dc) < 0.01 * dc
    # a broadcast excitation equals the per-mode form, step for step
    m2 = ModalFx(modes=4, nums=2, headroom=0)
    exc = np.random.default_rng(1).integers(-32768, 32767, 500).astype(np.int16)
    a = ModalFx(modes=4, nums=2, headroom=0).process(exc, coefs, [BP, HP, RAW, RAW])
    b = m2.process(np.repeat(exc[:, None], 4, axis=1), coefs, [BP, HP, RAW, RAW])
    assert np.array_equal(a, b)


def test_pole_regs_reproduce_the_808_reference_table():
    """docs/tr808-reference.md section 14's Q2.24 pairs, from the same
    formula: r = exp(-pi f0 / (Q fs)), w = 2 pi f0 / fs."""
    assert pole_regs(56.0, 22.3) == (33548016, -16771702)      # BD, decay mid
    a1, a2 = pole_regs(56.0, 5.1519)                           # BD, short: the row prints Q as 5.2; its r is Q 5.152
    assert abs(a1 - 33529659) <= 16 and abs(a2 + 16753353) <= 16   # the row's own rounding of f0 and r
    assert pole_regs(173.0, 16.3) == (33522534, -16753924)     # SD low, later units
    assert pole_regs(336.0, 9.9) == (33447602, -16702846)      # SD high
    assert pole_regs(90.0, 25.0) == (33544199, -16769312)      # LT
    assert pole_regs(2500.0, 200.0) == (31747718, -16749787)   # CL


def test_bank_headroom_zero_and_nineteen_bits_hold_the_kits_loudest_hit():
    """All eight stops in one frame at accent 1.4, then 2.0: the body word
    (Q4.15, +-8.0) never saturates and no mode's 28-bit state does -- the
    kit's amps keep the loudest legal combination inside the width, so the
    only clamp on the way to the DAC is the output stage's (12)."""
    class Cov:
        def __init__(self): self.n = 0; self.s = modal_fixed.sat; modal_fixed.sat = self.c
        def c(self, v, b): r = self.s(v, b); self.n += (r != v); return r
        def off(self): modal_fixed.sat = self.s
    for accent in (1.4, 2.0):
        cov = Cov()
        d = dx.DrumsFx()
        dm, bd = d.play(dx.hit_writes([(10, s, accent) for s in range(dx.N_STOPS)], dx.kit_808()), int(0.3 * SR))
        cov.off()
        assert cov.n == 0, accent
        assert np.abs(bd).max() < (1 << 18) and np.abs(dm).max() < 65536


# ---- the output stage (12) ---------------------------------------------------------------
def test_output_stage_is_the_voice_formula_when_the_drums_are_silent():
    v = np.random.default_rng(2).integers(-(1 << 19), 1 << 19, 1000)
    ref = np.clip((v * vf.VOL_REF) >> 15, -32768, 32767).astype(np.int16)
    z = np.zeros(1000, dtype=np.int64)
    assert np.array_equal(dx.output_fx(v, vf.VOL_REF, z, 14746, z, 14746), ref)
    assert np.array_equal(dx.output_fx(v, vf.VOL_REF, v, 0, v, 0), ref)
    # the one rail: full buses at full gains clip, never wrap
    full = np.full(4, 1 << 20)
    assert np.all(dx.output_fx(full, 65535, full, 65535, full, 65535) == 32767)
    assert np.all(dx.output_fx(-full, 65535, -full, 65535, -full, 65535) == -32768)


# ---- the reference kit (Appendix G) ----------------------------------------------------
def test_kit_writes_fit_their_registers():
    B = dx.REG_BITS
    for a, v in dx.kit_808():
        assert 0 <= v < (1 << 32)
        if a == dx.A_STOPS: assert v < (1 << B["stops"])
        elif dx.A_ACCENT <= a < dx.A_ACCENT + dx.N_STOPS: assert v < (1 << B["accent"])
        elif dx.A_OSC <= a < dx.A_OSC + dx.N_OSC: assert v < (1 << B["osc_inc"])
        elif dx.A_ENV <= a < dx.A_ENV + dx.N_ENV * dx.ENV_STRIDE:
            assert v < (1 << (B["env_ctl"], B["peak"], B["rate"], B["frate"])[(a - dx.A_ENV) % 4])
        elif dx.A_PATH <= a < dx.A_PATH + dx.N_PATH: assert v < (1 << B["path"])
        elif dx.A_MODE <= a < dx.A_MODE + dx.N_MODES * dx.MODE_STRIDE:
            assert v < (1 << (B["a1"], B["a2"], B["amp"], B["num"])[(a - dx.A_MODE) % 4])
        else: raise AssertionError(a)


def test_kit_voices_sit_at_the_chart_levels():
    """Each voice alone at accent 1.0 peaks at its target on its bus:
    Roland's chart proportions with the loudest at 0.5 x full scale
    (drums_fx_render.py --balance), within 12 %."""
    for name in dx.SOUND_NAMES:
        d = dx.DrumsFx()
        n = int(1.0 * SR)
        dm, bd = d.play(dx.hit_writes([(10, dx.SOUND_STOP[name], 1.0)], dx.kit_with_sounds(name)), n)
        pk = max(np.abs(dm).max(), np.abs(bd).max()) / 32768
        # 12 % was the tolerance when every voice was a body-bus ring. The
        # noise- and oscillator-driven ones are not repeatable to that: the
        # peak of ONE hit depends on where the LFSR and the six free-running
        # phases happen to be when it lands, which moves MA and CY by +-9 %
        # between hit times on their own. 15 % covers that; it is a level
        # check, not a decay one.
        assert abs(pk / dx.BUS_TARGET[name] - 1.0) < 0.15, (name, pk, dx.BUS_TARGET[name])


def test_snare_has_two_partials_and_a_snap():
    """Spectral peaks at the SD's 173 and 336 Hz modes (later units,
    reference 3), and noise energy above 2 kHz from the snappy path that is
    absent with SNAPPY (the noise envelope's peak) at zero."""
    d, dm, bd = _solo(dx.SD, 0.4)
    x = bd.astype(float)[10:]
    spec = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    f = np.fft.rfftfreq(len(x), 1 / SR)
    for target in (173.0, 336.0):
        band = (f > target * 0.9) & (f < target * 1.1)
        assert f[band][np.argmax(spec[band])] == pytest.approx(target, rel=0.05)
    hi = spec[f > 2000].sum()
    kit = [(a, (0 if a == dx.A_ENV + dx.E_SDN * 4 + 1 else v)) for a, v in dx.kit_808()]
    d2, _, bd2 = _solo(dx.SD, 0.4, kit=kit)
    x2 = bd2.astype(float)[10:]
    spec2 = np.abs(np.fft.rfft(x2 * np.hanning(len(x2))))
    assert spec2[f > 2000].sum() < 0.05 * hi


def test_hats_are_squares_not_noise():
    """The hats are the six square oscillators through the band-pass, the
    swing VCA and a high-pass: with every oscillator increment 0 the closed
    hat is silent (a noise-based hat would not be), and with the kit the
    energy is above 5 kHz."""
    kit = [(a, (0 if dx.A_OSC <= a < dx.A_OSC + 6 else v)) for a, v in dx.kit_808()]
    d = dx.DrumsFx()                        # the hit well after the band-pass's start-up step has rung down
    dm, bd = d.play(dx.hit_writes([(2000, dx.CH, 1.0)], kit), 4000)
    assert np.abs(bd[1000:]).max() == 0 and np.abs(dm).max() == 0
    d, dm, bd = _solo(dx.CH, 0.2)
    x = bd.astype(float)[10:]
    spec = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2
    f = np.fft.rfftfreq(len(x), 1 / SR)
    assert spec[f > 5000].sum() > 5 * spec[f < 5000].sum()


def test_clap_has_four_strikes_the_last_at_the_fire_level_and_a_tail():
    """Envelope 8's trace (contract revision 11, 15.3; plan084 L2): strikes at
    0, 10.6 and 21.3 ms at 1, 13/16 and (13/16)^2 of the first, then the FINAL
    strike at 31.9 ms back at the fire level, decaying at FRATE; envelope 9 the
    tail, which outlasts them. Was three strikes at 10 ms and no final strike
    (revision 10) -- the D12A deficit #253 diagnosed."""
    d, dm, bd = _solo(dx.CP, 0.2)
    burst = d.trace["env"][dx.E_CPBURST]
    tail = d.trace["env"][dx.E_CPTAIL]
    P, T = dx.peak_reg(0.69), dx.CP_PERIOD
    s1 = (P * 53248) >> 16
    s2 = (s1 * 53248) >> 16
    assert burst[10] == P >> 9 and burst[10 + T] == s1 >> 9 and burst[10 + 2 * T] == s2 >> 9
    assert burst[10 + 3 * T] == P >> 9                      # the final strike: the fire level
    assert burst[9 + T] < burst[10 + T] // 8                # the early strikes are short
    assert burst[10 + 3 * T + 480] > (P >> 9) // 3          # the final one is not (tau 20 ms)
    assert burst[-1] < burst[10 + 3 * T] // 64
    assert tail[10] == dx.peak_reg(0.22) >> 9 and tail[-1] > 0


def test_closed_hat_chokes_the_open_hat():
    """OH's envelope is zeroed in the frame CH fires (reference 11)."""
    d = dx.DrumsFx()
    d.play(dx.hit_writes([(10, dx.OH, 1.0), (500, dx.CH, 1.0)], dx.kit_808()), 600)
    oh = d.trace["env"][dx.E_OH]
    assert oh[499] > 30000 and oh[500] == 0 and oh[599] == 0
    assert d.trace["env"][dx.E_CH][500] == 32767


# ---- every legal register value runs (15.1) -------------------------------------------
def test_every_legal_register_value_runs():
    """Whatever a register can hold, the model must process: extremes on
    every address (all-ones words on every register at once, then all
    zeros), random words with a fixed seed, taps of railed modes into
    unstable modes, every source code, hits throughout -- no raise, both
    buses inside their widths, every state inside its width."""
    rng = np.random.default_rng(7)
    d = dx.DrumsFx()
    addrs = ([dx.A_STOPS] + list(range(dx.A_ACCENT, dx.A_ACCENT + dx.N_STOPS))
             + list(range(dx.A_OSC, dx.A_OSC + dx.N_OSC))
             + list(range(dx.A_ENV, dx.A_ENV + dx.N_ENV * dx.ENV_STRIDE))
             + list(range(dx.A_PATH, dx.A_PATH + dx.N_PATH))
             + list(range(dx.A_MODE, dx.A_MODE + dx.N_MODES * dx.MODE_STRIDE)))
    all_stops = (1 << dx.N_STOPS) - 1
    writes = ([(0, a, 0xFFFFFFFF) for a in addrs] + [(50, a, 0) for a in addrs]
              + [(51, dx.A_STOPS, all_stops)])
    for f in range(60, 400, 4):
        writes.append((f, int(rng.choice(addrs)), int(rng.integers(0, 1 << 32))))
        if f % 12 == 0:
            writes += [(f, dx.A_STOPS, 0), (f + 1, dx.A_STOPS, all_stops)]
    writes.append((100, 0x37, 1))                                          # no register there: ignored
    writes.append((200, dx.A_RESET, 0))
    writes += [(201, a, v) for a, v in dx.kit_808()]
    dm, bd = d.play(sorted(writes, key=lambda t: t[0]), 400)
    assert np.abs(dm).max() < (1 << 21) and np.abs(bd).max() < (1 << 18)
    assert all(0 <= e.level <= FULL24 and 0 <= e.t <= 2047 for e in d.envs)
    assert all(-(1 << 27) <= y < (1 << 27) for y in d.bank.y1 + d.bank.y2)
    assert np.abs(d.trace["exc"]).max() < (1 << 20)


def test_signal_path_has_no_transcendentals():
    """After the kit's writes are computed, play() must not call a float
    transcendental, and every traced array is integer."""
    d = dx.DrumsFx()
    w = dx.hit_writes([(5, s, 1.0) for s in range(dx.N_STOPS)], dx.kit_808())
    nframes = max(300, max(f for f, _, _ in w) + 1)   # the coefficient sequences
                                                      # of 15.7 run to 60 ms after a hit

    def boom(*a, **k):
        raise AssertionError("float transcendental in the signal path")
    saved = [(math, "exp"), (math, "tanh"), (math, "sin"), (math, "cos"), (math, "log"),
             (np, "exp"), (np, "tanh"), (np, "sin"), (np, "log")]
    orig = [(o, n, getattr(o, n)) for o, n in saved]
    try:
        for o, n, _ in orig:
            setattr(o, n, boom)
        dm, bd = d.play(w, nframes)
    finally:
        for o, n, f in orig:
            setattr(o, n, f)
    assert dm.dtype == np.int64 and bd.dtype == np.int32
    for k in ("dmix", "body", "fire", "noise", "env", "exc"):
        assert np.issubdtype(d.trace[k].dtype, np.integer), k


# ---- measured against a real TR-808 (docs/drum-verification.md section 8) -------
#
# Every target below is a number measured from the reference recordings by
# model/drum_fit.py, whose separator is itself validated in
# model/test_drum_fit.py. The three statuses the contract distinguishes:
#   verified in a source      docs/tr808-reference.md's tagged claims
#   measured against this recording   s/n 103852, one unit, stated as such
#   our chosen specification  what kit_808() ships, and why
# are kept apart in the names and the docstrings.

SNAPPY_RATIO_AT_5 = 0.6193       # measured: noise/tone amplitude at SNAPPY 5.0
CB_DIFF_TONE_DB = -65.0          # the machine's own 265 Hz line is -67.8 dB


def _sd_noise_ratio(accent=1.0):
    """The snare's noise/tone amplitude ratio, by exact arithmetic rather than
    by the separator: the tonal and noise paths are separate paths into
    separate modes with LIN nonlinearities, so each renders alone and the two
    sum to the whole."""
    import drum_fit as df
    pk = lambda e: dx.A_ENV + e * dx.ENV_STRIDE + 1
    def render(ov):
        kit = [(a, ov.get(a, v)) for a, v in dx.kit_808()]
        d = dx.DrumsFx(); n = int(0.5 * SR)
        dm, bd = d.play(dx.hit_writes([(10, dx.SD, accent)], kit), n)
        return bd.astype(np.float64) / 32768.0
    full, tone, noise = render({}), render({pk(dx.E_SDN): 0}), render({pk(dx.E_SDX): 0})
    i = max(0, int(np.argmax(np.abs(full) > 0.02 * np.abs(full).max())) - int(5e-4 * SR))
    n = int(0.25 * SR)
    t, z = tone[i:i + n], noise[i:i + n]
    return math.sqrt(float(z @ z) / float(t @ t)), noise


def test_snare_noise_sits_where_the_machines_snappy_knob_puts_it():
    """MEASURED: across the reference set's 25 snare files the noise/tone
    amplitude ratio runs 0.212 / 0.219 / 0.619 / 1.153 / 1.652 at SNAPPY
    0 / 2.5 / 5 / 7.5 / 10. kit_808() claims the chart's 12-o'clock setting,
    so it must sit at SNAPPY 5.0 -- within 15 %, which is inside the +-0.125
    spread the five TONE positions show at that setting.

    The target is read off the KNOB'S CURVE, not off one file: a single
    energy share cannot say where on a knob a signal sits, because the curve
    is flat from 0 to 2.5 (the pot's dead zone plus the separator's own 4.7 %
    floor on this material) and then rises about 2 dB per knob unit."""
    got, _ = _sd_noise_ratio()
    assert abs(got / SNAPPY_RATIO_AT_5 - 1.0) < 0.15, (
        f"noise/tone amplitude {got:.4f} against the machine's {SNAPPY_RATIO_AT_5:.4f} "
        f"at SNAPPY 5.0 ({20 * math.log10(got / SNAPPY_RATIO_AT_5):+.2f} dB)")


def test_snare_noise_is_a_hump_at_3_to_5_khz_not_a_rising_high_pass():
    """MEASURED: the machine's snare noise, recovered as the residual after
    subtracting the two body modes, puts 1.6 / 20.5 / 36.1 / 28.5 / 10.4 /
    2.9 percent of its energy in 0.7-1.5 / 1.5-3 / 3-5 / 5-8 / 8-12 /
    12-16 kHz -- it PEAKS at 3-5 kHz and falls above. A 2-pole high-pass
    cannot do that; ours put 30.5 % in 8-12 kHz and 27.9 % above 12 kHz."""
    import drum_fit as df
    _, noise = _sd_noise_ratio()
    edges = [700, 1500, 3000, 5000, 8000, 12000, 16000]
    sh = df.band_shares(df.trim_onset(noise, SR)[:int(0.25 * SR)], SR, edges)
    assert np.argmax(sh) == 2, f"the peak band must be 3-5 kHz, got {sh}"
    assert sh[5] < 8.0, f"{sh[5]:.1f} % above 12 kHz against the machine's 2.9 %"
    assert sh[1] > 12.0, f"{sh[1]:.1f} % in 1.5-3 kHz against the machine's 20.5 %"


def test_snare_noise_filter_is_a_band_pass():
    """The fix is one register: the snappy mode's NUMERATOR. Its pole stays at
    the reference's own 2.75 kHz / Q 0.7 (docs/tr808-reference.md section 3,
    verified in a source); read as a high-pass that pole is flat to Nyquist,
    and read as a band-pass it fits the measured noise spectrum to 1.9 dB
    weighted rms against the high-pass's 5.2."""
    kit = dict(dx.kit_808())
    assert kit[dx.A_MODE + dx.M_SDN * dx.MODE_STRIDE + 3] == BP


def test_cowbell_gates_each_oscillator_separately():
    """VERIFIED IN A SOURCE (docs/tr808-reference.md section 9): 'each
    oscillator has its own transistor gate (Q15, Q14)'. Two paths, one per
    oscillator, each through its own swing VCA, summed into the band-pass --
    and no path may take the pre-summed pair, because nl(a+b) != nl(a)+nl(b)."""
    kit = dict(dx.kit_808())
    paths = [kit[dx.A_PATH + p] for p in range(dx.N_PATH) if dx.A_PATH + p in kit]
    cb = [w for w in paths if ((w >> 20) & 31) == dx.M_CBBP]
    assert len(cb) == 2, f"expected one path per cowbell oscillator, got {len(cb)}"
    srcs = sorted(w & 31 for w in cb)
    assert srcs == [dx.SRC_SQ + dx.SQPAIR[0], dx.SRC_SQ + dx.SQPAIR[1]], srcs
    assert all(((w >> 13) & 3) == dx.NL_SWING for w in cb)
    assert not any((w & 31) == dx.SRC_SQPAIR for w in paths), "SRC_SQPAIR is the defect"


def test_cowbell_has_no_difference_tone():
    """MEASURED: the reference unit's 265 Hz line (824 - 558, the difference
    of its two trimmed oscillators) is at -67.8 dB, which is 18.9 dB above
    that recording's own spectral floor in 230-290 Hz (median -86.7 dB) and
    so is a real line, not the floor. Ours was at -25.6 dB: 42 dB louder than
    the machine's. It must now be at least as far down as the machine's,
    allowing 3 dB."""
    import drum_fit as df
    d, dm, bd = _solo(dx.CB, 0.5)
    x = bd.astype(np.float64) / 32768.0
    f0, f1 = dx.OSC_HZ[dx.SQPAIR[0]], dx.OSC_HZ[dx.SQPAIR[1]]
    diff = abs(f0 - f1)
    got = df.line_db(x, SR, diff, tol=6.0)
    assert got < CB_DIFF_TONE_DB, f"difference tone at {diff:.0f} Hz is {got:.1f} dB"


def test_gating_the_sum_makes_a_difference_tone_and_gating_each_does_not():
    """The mechanism, in isolation: two square waves, one swing VCA, nothing
    else. `nl(a + b)` puts the difference and sum tones about 8 dB below the
    fundamentals; `nl(a) + nl(b)` puts them at the arithmetic floor, 150 dB
    down -- they are not there at all. This is the whole of the cowbell
    defect, and it is why the fix is two paths rather than a filter."""
    d = dx.DrumsFx()
    n = 1 << 16
    fa, fb = dx.OSC_HZ[dx.SQPAIR[1]], dx.OSC_HZ[dx.SQPAIR[0]]
    mask = (1 << 24) - 1
    pa = (np.arange(n) * dx.osc_inc_reg(fa)) & mask
    pb = (np.arange(n) * dx.osc_inc_reg(fb)) & mask
    A = np.where(pa < (1 << 23), dx.SQPAIR_STEP, -dx.SQPAIR_STEP)
    B = np.where(pb < (1 << 23), dx.SQPAIR_STEP, -dx.SQPAIR_STEP)
    nl = lambda v: np.array([d._nonlinear(int(s), dx.NL_SWING) for s in v], dtype=np.float64)
    def line(y, hz):
        S = np.abs(np.fft.rfft(y * np.hanning(len(y))))
        f = np.fft.rfftfreq(len(y), 1.0 / SR)
        m = (f > hz - 6) & (f < hz + 6)
        return 20 * math.log10(S[m].max() / S.max() + 1e-30)
    together, apart = nl(A + B), nl(A) + nl(B)
    assert line(together, abs(fa - fb)) > -15.0
    assert line(together, fa + fb) > -15.0
    assert line(apart, abs(fa - fb)) < -100.0
    assert line(apart, fa + fb) < -100.0


def test_cowbell_tail_and_band_pass_are_the_fitted_ones():
    """MEASURED: the reference cowbell's tail is tau = 98 ms (fit over
    -3..-30 dB), not 30; and a 2-pole band-pass fitted to 16 identified
    partials lands at 1100 Hz Q 2.8, not 900 Hz Q 4."""
    kit = dict(dx.kit_808())
    mask = (1 << 26) - 1
    a1, a2 = pole_regs(1100.0, 2.8)
    assert kit[dx.A_MODE + dx.M_CBBP * dx.MODE_STRIDE] == (a1 & mask)
    assert kit[dx.A_MODE + dx.M_CBBP * dx.MODE_STRIDE + 1] == (a2 & mask)
    assert kit[dx.A_ENV + dx.E_CBB * dx.ENV_STRIDE + 2] == dx.rate_reg(100e-3)


def test_bd_pitch_is_the_reference_circuits_not_roland_s_chart():
    """VERIFIED IN A SOURCE (docs/tr808-reference.md section 2, [W14a section
    5; computed with section 1.2]): the bridged-T's f0 is 49.4 Hz, and Werner
    measures ~49.5. Roland's chart's 56 Hz ("18 ms") is the outlier, and the
    reference marks the chart "typical and variable".

    The proof that 49.4 is the reference's own working number, not a reading
    of it, is section 2's decay table: its Q and tau columns satisfy the
    resonator identity tau = Q / (pi f0) to 1.5 % at 49.4 Hz and only to
    12.8 % at 56. Rev 5 shipped that table's Q = 22.3 with the chart's 56 Hz,
    which is why its tau came out 127 ms where the same table says 144.
    MEASURED, corroborating: 50.70 +- 0.02 Hz on the reference unit at the
    chart's own 12-o'clock condition, 48.8-51.0 Hz on a second sample set."""
    import drum_fit as df
    for f0, bound in ((dx.BD_HZ, 0.016), (dx.BD_HZ_CHART, 0.130)):
        worst = max(abs(q / (math.pi * f0) * 1e3 / tau - 1)
                    for q, tau in ((2.3, 15), (5.2, 33), (22.3, 144), (63.0, 408), (84.0, 544)))
        assert worst < bound, (f0, worst)
        assert (worst < 0.02) == (f0 == dx.BD_HZ), "only one of the two is self-consistent"
        assert (f0 != dx.BD_HZ_CHART) or worst > 0.10, "the chart's f0 must be the inconsistent one"
    d, dm, bd = _solo(dx.BD, 1.0)
    x = bd.astype(np.float64) / 32768.0
    m = df.noise_share(df.trim_onset(x, SR)[int(0.020 * SR):], SR,
                       df.VOICE_MODES["BD"], win_s=0.30)["modes"][0]
    assert m["hz"] == pytest.approx(dx.BD_HZ, rel=0.01), m


def test_bd_decay_control_is_the_references_own_q_table():
    """The DECAY control is NOT a fault and is not retuned here: `bd_decay_q`
    is docs/tr808-reference.md section 2's table, Q against the VR6 position,
    and it must reproduce every tabulated point within 3 %.

    With the pitch fixed, the resulting tau at the 12-o'clock position is the
    reference's own 144 ms -- reported as TAU, the single exponential's time
    constant. Its T20 is 2.303 tau = 331 ms, which is the table's own "2.3
    tau" column (330 ms); Roland's chart's "mid 300 ms" is the T20-like
    figure, not the tau, and reading it as a tau is how this voice acquired
    two false decay findings already."""
    for k, q in dx.BD_DECAY_Q.items():
        assert dx.bd_decay_q(k) == pytest.approx(q, rel=0.03), (k, q)
    tau_ms = dx.bd_decay_q(5.0) / (math.pi * dx.BD_HZ) * 1e3
    assert tau_ms == pytest.approx(144.0, rel=0.03), tau_ms
    assert 2.303 * tau_ms == pytest.approx(330.0, rel=0.05), "T20, not tau"


def test_bd_body_rings_at_the_tabulated_tau():
    """MEASURED on our own render, the quantity the table states: tau of the
    body mode, fitted as a damped sinusoid from 20 ms (past the attack window)
    over 300 ms. It must be section 2's 144 ms.

    Recorded, not hidden: the reference UNIT measures tau = 178.0 +- 29.1 ms
    at its 12-o'clock DECAY, 23 % longer than the circuit table. That is
    inside the +-50 % on Q that section 12 says is normal between units, and
    the rule is that the reference wins over one unit, so the kit takes 144
    and the gap is tracked (contract 17.21) rather than tuned away."""
    import drum_fit as df
    d, dm, bd = _solo(dx.BD, 1.0)
    x = bd.astype(np.float64) / 32768.0
    m = df.noise_share(df.trim_onset(x, SR)[int(0.020 * SR):], SR,
                       df.VOICE_MODES["BD"], win_s=0.30)["modes"][0]
    assert m["tau_ms"] == pytest.approx(144.0, rel=0.05), m


def test_bd_has_the_attack_window_and_without_it_does_not():
    """VERIFIED IN A SOURCE (section 2, [W14a section 8.1; SN p.6]): while Q43
    is on it shorts R165 and f0 rises to ~130 Hz at Q ~ 6 for ~4 ms. It is the
    SAME resonator retuned, so the host writes it (15.7).

    Measured as BAND ENERGY OVER A STATED INTERVAL -- the first 4 ms, filtered
    in the time domain and then integrated -- never as a short-window FFT
    peak: 4 ms at 48 kHz gives 250 Hz bins, and the apparent peak of anything
    then tracks the bin spacing. `coef_seq=False` is the built-in negative
    control: the same measurement on the same render without the sequence.
    Ours puts 22.3 % of the first 4 ms in 80-150 Hz against 2.7 % without it;
    the reference unit puts 41.2 % there, so this closes most of the gap and
    not all of it (contract 17.21)."""
    import drum_fit as df
    edges = [20, 80, 150, 300, 600, 2000]
    def first4(seq):
        d = dx.DrumsFx()
        n = int(0.8 * SR)
        dm, bd = d.play(dx.hit_writes([(10, dx.BD, 1.0)], dx.kit_808(), coef_seq=seq), n)
        x = df.trim_onset(bd.astype(np.float64) / 32768.0, SR)
        return df.band_energy_interval(x, SR, edges, 0.0, 0.004)
    with_, without = first4(True), first4(False)
    assert without[1] < 5.0, f"the control must have no attack: {without}"
    assert with_[1] > 15.0, f"80-150 Hz over the first 4 ms is {with_[1]:.1f} %"
    assert with_[1] > 5 * without[1]


# HARDWARE, not the contract. Every bound below comes from
# docs/tom-pitch-drop-results.json -- 99 clean-digital tom files of a real
# TR-808 measured in #110 -- and NOT from what drums_fx ships. A test written
# against the shipped constant can only ever say the constant is itself, which
# is exactly how x1.7 survived three revisions marked [inferred].
CORPUS = {                     # onset f0 / settled f0, per accent level
    "A": {"median": 1.063, "min": 1.040, "max": 1.094},   # No Accent
    "B": {"median": 1.140, "min": 1.085, "max": 1.272},   # Accent
    "C": {"median": 1.236, "min": 1.169, "max": 1.344},   # More Accent
}
CORPUS_MAX_ANYWHERE = 1.344    # the largest drop in any of the 99 files
CORPUS_ACCENT = {"A": 1.0, "B": 1.4, "C": 2.0}   # model/tom_drop_fit.py's map


def _tom_start_hz(accent, seq=True, stop=None, kit=None):
    """Onset f0 of an LT hit, from half-periods between zero crossings -- no
    window at all. `seq=False` is the negative control: no sequence, no sweep."""
    def halfperiods(x, n=4):
        z = np.nonzero(np.diff(np.signbit(x)))[0]
        return [SR / (2 * (z[i + 1] - z[i])) for i in range(min(n, len(z) - 1))]
    d = dx.DrumsFx()
    dm, bd = d.play(dx.hit_writes([(10, dx.LT if stop is None else stop, accent)],
                                  dx.kit_808() if kit is None else kit, coef_seq=seq),
                    int(0.6 * SR))
    import drum_fit as df
    return max(halfperiods(df.trim_onset(bd.astype(np.float64), SR)))


def test_toms_drop_in_pitch_by_the_MEASURED_ratio_not_the_inferred_one():
    """VERIFIED IN A SOURCE (section 4, SN text) that the drop exists, is
    amplitude-dependent and is gradual. HARDWARE-MEASURED (#110, 99 files)
    for how big it is: x1.063 unaccented, x1.140 accented, x1.236 at more
    accent, and NEVER x1.7 -- the largest drop in the whole corpus is x1.344.

    The assertion is against the corpus's own measured range at each level, so
    this test fails if the inferred x1.7 ever comes back, and it does not care
    what drums_fx currently holds."""
    flat = _tom_start_hz(1.0, seq=False)
    assert flat == pytest.approx(90.0, rel=0.03), f"the control must be flat at 90 Hz, got {flat}"
    for lvl, accent in CORPUS_ACCENT.items():
        got = _tom_start_hz(accent) / 90.0
        lo, hi = CORPUS[lvl]["min"], CORPUS[lvl]["max"]
        assert lo * 0.98 <= got <= hi * 1.02, (
            f"accent {accent} reads x{got:.4f}; the machine's {lvl} level spans "
            f"x{lo:.3f}-x{hi:.3f} over 11 tunings. x1.7 is outside every one of them.")


def test_no_setting_the_register_map_allows_exceeds_the_largest_drop_measured():
    """The loudest hit at either end of the TUNING pot must still land inside
    the 99 files. This is the guard that fails if x1.7 -- or anything like it --
    returns by any route: a constant, the clamp, or the tuning term."""
    worst = 0.0
    for name in ("LT", "MT", "HT", "LC", "MC", "HC"):
        f_nom = dx.TOM_PRESET[name][0]
        mode = {"LT": dx.M_LT, "LC": dx.M_LT, "MT": dx.M_MT,
                "MC": dx.M_MT, "HT": dx.M_HT, "HC": dx.M_HT}[name]
        for f0 in (0.80 * f_nom, f_nom, 1.25 * f_nom):
            for accent in (0.0, 0.5, 1.0, 1.4, 2.0):
                worst = max(worst, 1.0 + dx.tom_drop_excess(mode, f0, accent))
    assert worst <= CORPUS_MAX_ANYWHERE * 1.02, (
        f"the register map allows x{worst:.4f}; the largest drop in 99 files of "
        f"real hardware is x{CORPUS_MAX_ANYWHERE}")
    assert dx.TOM_DROP_RATIO < CORPUS["A"]["max"] * 1.02, (
        f"TOM_DROP_RATIO is the ratio at accent 1.0 with the pot centred, which "
        f"the machine measures at x{CORPUS['A']['median']}; it is x{dx.TOM_DROP_RATIO}")


def test_the_unaccented_hit_is_not_given_the_full_drop():
    """The old law scaled by min(max(accent, 0), 1), so an unaccented hit got
    the WHOLE sweep. The machine gives it x1.06 and its loudest hit x1.24, so
    the excess at accent 1.0 must be a small fraction of the excess at 2.0 --
    measured 0.063 / 0.236, i.e. about a quarter."""
    e1 = dx.tom_drop_excess(dx.M_LT, 90.0, 1.0)
    e2 = dx.tom_drop_excess(dx.M_LT, 90.0, 2.0)
    assert e1 > 0.0 and e2 > e1
    ratio = e1 / e2
    measured = (CORPUS["A"]["median"] - 1.0) / (CORPUS["C"]["median"] - 1.0)
    assert ratio == pytest.approx(measured, rel=0.25), (
        f"excess at accent 1.0 is {100*ratio:.0f} % of the excess at 2.0; "
        f"the machine says {100*measured:.0f} %. A clamp would say 100 %.")


def test_the_drop_has_a_threshold_and_a_soft_hit_does_not_sweep_at_all():
    """Germanium diodes do not conduct below a drive. Below the threshold the
    sequence still runs -- the write count is part of 15.7.1 -- but it writes
    the settled coefficients, so the pitch does not move."""
    assert dx.tom_drop_excess(dx.M_LT, 90.0, dx.TOM_DROP_ACCENT_0) == 0.0
    assert dx.tom_drop_excess(dx.M_LT, 90.0, 0.4) == 0.0
    soft = _tom_start_hz(0.4)
    assert soft == pytest.approx(90.0, rel=0.03), f"a soft hit must not sweep, got {soft}"
    assert len(dx.tom_pitch_drop_writes(0, dx.M_LT, 90.0, 25.0, 0.2, 0.4)) == \
        len(dx.tom_pitch_drop_writes(0, dx.M_LT, 90.0, 25.0, 0.2, 2.0)), \
        "the write count must not depend on what the host played"


def test_the_drop_follows_the_tuning_pot():
    """HARDWARE-MEASURED (#110): LT at More Accent runs x1.169 at 82 Hz and
    x1.325 at 101 Hz -- the excess nearly doubles across the pot. The shipped
    sequence used to be tuning-independent."""
    lo = dx.tom_drop_excess(dx.M_LT, 82.0, 2.0)
    hi = dx.tom_drop_excess(dx.M_LT, 101.0, 2.0)
    assert hi > lo
    measured = (1.325 - 1.0) / (1.169 - 1.0)
    assert hi / lo == pytest.approx(measured, rel=0.25), (
        f"the pot moves the excess by x{hi/lo:.2f}; the machine says x{measured:.2f}")


def test_the_conga_position_drops_far_less_than_the_tom_at_the_same_frequency():
    """HT and LC are BOTH nominally 185 Hz, one bridged-T with a capacitor
    switched (SW8). HARDWARE-MEASURED (#110): unaccented their excesses are
    0.061 and 0.0055 -- eleven times apart at the SAME frequency. So the drop
    cannot be a function of f0, and a law that made it one would be wrong here
    and nowhere else."""
    tom = dx.tom_drop_excess(dx.M_HT, 185.0, 1.0)     # HT position
    conga = dx.tom_drop_excess(dx.M_LT, 185.0, 1.0)   # LC position, same f0
    assert dx.tom_position(dx.M_HT, 185.0) == "HT" and dx.tom_position(dx.M_LT, 185.0) == "LC"
    assert tom > 0.0
    assert conga < tom / 5.0, (
        f"tom {tom:.4f} against conga {conga:.4f} at the same 185 Hz; the machine "
        f"measures 0.061 against 0.0055")


def test_a_write_past_the_end_is_dropped_and_a_negative_frame_is_not():
    """`play(writes, n)` must tolerate a write scheduled at or after frame n:
    the coefficient sequences of 15.7.1 run to 60 ms past a hit, so any caller
    rendering a shorter passage would otherwise have to know about them. Such
    a write cannot affect a sample, so it is dropped and counted. A negative
    frame is still a caller error and must raise."""
    d = dx.DrumsFx()
    hits = [(10, dx.BD, 1.0)]
    w = dx.hit_writes(hits, dx.kit_808())
    assert max(f for f, _, _ in w) > 100, "the BD hit must schedule a later write to drop"
    dm, bd = d.play(w, 100)
    assert d.n_late_writes > 0 and len(dm) == 100
    # and the result is exactly the same as feeding it only the in-range writes
    d2 = dx.DrumsFx()
    dm2, bd2 = d2.play([t for t in w if t[0] < 100], 100)
    assert np.array_equal(dm, dm2) and np.array_equal(bd, bd2)
    assert d2.n_late_writes == 0
    with pytest.raises(AssertionError):
        dx.DrumsFx().play([(-1, dx.A_STOPS, 1)], 10)

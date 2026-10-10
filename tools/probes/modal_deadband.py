#!/usr/bin/env python3
"""Production-path qualification of the modal bank's floor-arithmetic deadband (#350).

    python3 tools/probes/modal_deadband.py --declared             thresholds, grids, mapping
    python3 tools/probes/modal_deadband.py --known-answer         the committed table, reproduced
    python3 tools/probes/modal_deadband.py --controls             MOVED/BLIND: properties x defects
    python3 tools/probes/modal_deadband.py --population           every (sound, mode) cell, 1 accent
    python3 tools/probes/modal_deadband.py --freeze               commit-bound freeze of the sets
    python3 tools/probes/modal_deadband.py --table dev|confirm --out F.json
    python3 tools/probes/modal_deadband.py --ground [--refs DIR]  what the captures can support
    python3 -m pytest tools/probes/test_modal_deadband.py -q

MEASUREMENT + TARGET-SETTING ONLY. No production arithmetic, RTL, register or
image file is touched; the apparatus imports the shipped model and runs it.

WHAT IT MEASURES. `tools/probe_tom_numerator.py --explain deadband` (PR #351)
compares ONE fixed-point mode with a float recursion that uses the same integer
coefficients and shows the floor-rounded recursion leaving its poles at ~6-8k
state LSB. That is an arithmetic effect at the state, not a statement about the
bus. Here the SHIPPED `DrumsFx` is run -- its real kit image, its real
coefficient sequences (BD attack window, tom pitch drop), its real exciters --
with the bank replaced by `TwinBank`, which is `ModalFx.step` line for line
(asserted bit-identical to the stock bank, `bank_is_stock`) plus a float shadow
of every mode's state driven by the SAME post-numerator drive and the SAME
integer coefficients. So, per mode, per hit:

  state residual     fixed - float state, in state LSB (Q15 within a 28-bit word)
  bus residual       the mode's own (y*amp)>>16 term minus its float, in body-bus LSB
                     (the 19-bit Q4.15 word) and dBFS re that word's full scale
  int16 residual     the bus term through `output_fx`'s body gain, in int16 LSB / dBFS
  departure          first 10 ms block, after the mode stops being driven and
                     retuned, where fixed and float block peaks differ > 3.0 dB
  tail shape         stuck value, or exact period of the limit cycle
  voice T20          `run_case._t20_ms(0.005)` of the fixed int16 output vs the
                     float twin's, REFUSED if either estimate is refused

SCOPE LIMITS THAT ARE PART OF THE RESULT.
  * The float twin is driven by the FIXED run's drive. A TAP-fed mode (cymbal,
    hats) therefore sees the fixed upstream state; the comparison is per mode,
    open loop. It is not a closed-loop float rendition of the whole kit.
  * A mode with `amp == 0` reaches the drum bus only through a TAP path and a
    nonlinear VCA. Its STATE residual is measured; its bus residual is
    REFUSED (`tap-only`), not guessed.
  * The float twin is NOT an external fidelity reference. It says how far the
    integer arithmetic is from its own poles. What the Fischer captures can say
    about it is `--ground`'s question and is answered separately.

REFUSED is a first-class outcome. Every row ends as a measurement or as a named
reason; `reconcile` asserts the counts add up to the population.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import pathlib
import subprocess
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "model"), str(ROOT / "tools"), str(ROOT / "tools" / "probes")]
import drums_fx as dx          # noqa: E402
import modal_fixed as mf       # noqa: E402
from modal_fixed import ModalFx, RAW, BP, HP, sat, shl   # noqa: E402

# ---------------------------------------------------------------------------
# Frozen thresholds (declared BEFORE any production row was inspected)
# ---------------------------------------------------------------------------
DEPART_DB = 3.0            # the issue's departure criterion
BLOCK_S = 0.010            # the committed table's block
BLOCK = int(BLOCK_S * dx.SR)
PERSIST_S = 0.100          # "persistent" = peak over the last 100 ms of the render
PERSIST = int(PERSIST_S * dx.SR)
PERIOD_WINDOW = 4800       # exact-period search window (100 ms)
MAX_PERIOD = 2400
MIN_FLOAT_PEAK = 1.0       # float tail peak (state LSB) below which there is no tail
KA_CROSS_TOL_BLOCKS = 1    # AC1: crossing within one 10 ms block
KA_TAIL_TOL_LSB = 1.0      # AC1: fixed tail peak within 1 state LSB
SAT_RAIL = (1 << (28 - 1)) - 1
BODY_FS = float(1 << 18)   # the 19-bit Q4.15 body word, full scale
INT16_FS = 32768.0
GAIN_REG = dx.accent_reg(0.45)      # run_case.render_drum_solo's bus gain, both buses
CAPTURE_MARGIN_DB = 6.0    # a residual must clear the capture's floor by this to be grounded
T20_REL_REFUSE = 0.0       # T20 deltas are reported, never silently gated

PRODUCTION = dict(coef_frac=24, state_bits=28, state_q=15, headroom=0, out_bits=19, nums=11,
                  modes=16, sr=48000)

SOURCE_FILES = ("tools/probes/modal_deadband.py", "model/modal_fixed.py", "model/drums_fx.py",
                "tools/run_case.py")

# ---- the frozen sets (declared before inspecting confirmation) ------------
ACCENTS_DEV = (0.5, 1.0, 2.0)
ACCENTS_CONF = (0.25, 0.75, 1.5)
ACCENTS_ALL = tuple(sorted(set(ACCENTS_DEV + ACCENTS_CONF)))
# Modal drum voices (free-ringing bodies). Development selects on the
# non-conga positions; the congas share the toms' modes at other poles and are
# untouched until the confirmation table.
SOUNDS_DEV = ("BD", "SD", "LT", "MT", "HT")
SOUNDS_CONF_NEW = ("LC", "MC", "HC")
SOUNDS_OTHER = ("CH", "OH", "CP", "MA", "CB", "CY", "RS", "CL")      # population only, accent 1.0
CONDITIONS = {
    "dev": [(s, a) for s in SOUNDS_DEV for a in ACCENTS_DEV],
    "confirm": ([(s, a) for s in SOUNDS_DEV for a in ACCENTS_CONF]
                + [(s, a) for s in SOUNDS_CONF_NEW for a in ACCENTS_ALL]),
}


class Refused(RuntimeError):
    """A precondition of the apparatus failed. Not a pass, not a fail."""


class KnownAnswerMismatch(AssertionError):
    """The apparatus does not reproduce the committed known-answer table."""


# ---------------------------------------------------------------------------
# 1. The twin bank
# ---------------------------------------------------------------------------
class TwinBank(ModalFx):
    """`ModalFx.step` verbatim, plus a float shadow of every mode.

    The shadow is y[n] = x[n] + a1*y[n-1]/2^CF + a2*y[n-2]/2^CF in float64 with
    the SAME integer a1, a2 (read each frame, so a retune is followed), the SAME
    drive x (after the numerator) as the integer path, no floor and no clamp.
    Records are kept per frame so every metric is computed from arrays and none
    from a running summary."""

    def __init__(self, n_frames: int, floor_shadow: bool = False, **kw):
        self._n = int(n_frames)
        self._floor_shadow = floor_shadow
        super().__init__(**kw)
        M = self.M
        self.rec_y = np.zeros((self._n, M), dtype=np.int64)
        self.rec_f = np.zeros((self._n, M), dtype=np.float64)
        self.rec_x = np.zeros((self._n, M), dtype=np.int64)
        self.rec_c = np.zeros((self._n, M), dtype=np.int64)       # (y*amp)>>16
        self.rec_cf = np.zeros((self._n, M), dtype=np.float64)    # y_float*amp/65536
        self.rec_coef = np.zeros((self._n, M, 3), dtype=np.int64)
        self.rec_body = np.zeros(self._n, dtype=np.int64)
        self.rec_body_f = np.zeros(self._n, dtype=np.float64)
        self.t = 0

    def reset(self):
        super().reset()
        self.f1 = [0.0] * self.M
        self.f2 = [0.0] * self.M

    def step(self, exc, coefs, num=None) -> int:
        CF, SB, SQ = self.CF, self.SB, self.SQ
        osh = SQ - 15 + self.HR
        y1, y2, h1, h2 = self.y1, self.y2, self.h1, self.h2
        t = self.t
        mix = 0
        mixf = 0.0
        sc = float(1 << CF)
        for m in range(self.M):
            a1, a2, amp = coefs[m]
            e = shl(int(exc[m]), SQ - 15)
            if m < self.NUMS and num is not None:
                k = int(num[m])
                if k == BP:
                    x = e - h2[m]
                elif k == HP:
                    x = e - 2 * h1[m] + h2[m]
                else:
                    x = e
                h2[m], h1[m] = h1[m], e
            else:
                x = e
            acc = a1 * y1[m] + a2 * y2[m] + self.RND
            y = sat((acc >> CF) + x, SB)
            yf = (a1 * self.f1[m] + a2 * self.f2[m]) / sc + x
            self.f2[m], self.f1[m] = self.f1[m], yf
            y2[m], y1[m] = y1[m], y
            c = (y * amp) >> 16
            mix += c
            mixf += yf * amp / 65536.0
            if t < self._n:
                self.rec_y[t, m], self.rec_f[t, m], self.rec_x[t, m] = y, yf, x
                self.rec_c[t, m], self.rec_cf[t, m] = c, yf * amp / 65536.0
                self.rec_coef[t, m] = (a1, a2, amp)
        out = sat(mix >> osh, self.OB)
        if t < self._n:
            self.rec_body[t] = out
            self.rec_body_f[t] = mixf / float(1 << osh)
        self.t = t + 1
        return out


def assert_production_config(d, bank) -> None:
    """Point-of-use precondition: the thing measured is the thing that ships.
    REFUSED (not a number) when the bank or the block is not the shipped one."""
    got = dict(coef_frac=bank.CF, state_bits=bank.SB, state_q=bank.SQ, headroom=bank.HR,
               out_bits=bank.OB, nums=bank.NUMS, modes=bank.M, sr=dx.SR)
    bad = {k: (got[k], v) for k, v in PRODUCTION.items() if got[k] != v}
    if bad:
        raise Refused(f"bank is not the shipped configuration (got, want): {bad}")
    if bank.RND != 0:
        raise Refused("bank rounds (RND != 0): the shipped recursion floors")
    if (dx.N_MODES, dx.N_NUMS, dx.BODY_HR, dx.BODY_BITS) != (16, 11, 0, 19):
        raise Refused("drums_fx constants are not the shipped bank shape")
    if getattr(d, "couple", dx.COUPLE_OFF) != dx.COUPLE_OFF:
        raise Refused("experimental coupling placement active; production is the register")


# ---------------------------------------------------------------------------
# 2. Production render with the twin
# ---------------------------------------------------------------------------
def kit_for(sound: str) -> list:
    return dx.kit_with_sounds(sound)


def mode_reach(sound: str) -> dict:
    """mode -> how it can reach the drum bus in this sound's kit image, decoded
    from the registers (amp) and the path words (TAP sources), never assumed."""
    img = dict(kit_for(sound))
    out = {}
    taps = {}
    for p in range(dx.N_PATH):
        w = img.get(dx.A_PATH + p, 0)
        src, dest = w & 31, (w >> 20) & 31
        if dx.SRC_TAP <= src < dx.SRC_TAP + dx.N_MODES:
            taps.setdefault(src - dx.SRC_TAP, []).append(dest)
    for m in range(dx.N_MODES):
        amp = img.get(dx.A_MODE + m * dx.MODE_STRIDE + 2, 0)
        routes = []
        if amp > 0:
            routes.append("body-bus")
        for dest in taps.get(m, ()):
            routes.append("tap->mix" if dest == dx.DEST_MIX else f"tap->mode{dest}")
        out[m] = routes
    return out


def render_twin(sound: str, accent: float, seconds: float | None = None, hit_frame: int | None = None):
    """The shipped solo render (run_case.render_drum_solo's exact recipe) with
    the twin bank. Returns a dict of arrays and the int16 output."""
    import run_case as rc
    stop = dx.SOUND_STOP[sound]
    n = int((rc.SOLO_SECONDS.get(sound, 2.2) if seconds is None else seconds) * dx.SR)
    hit = rc.DRUM_SOLO_HIT_FRAME if hit_frame is None else int(hit_frame)
    d = dx.DrumsFx()
    d.bank = TwinBank(n, modes=dx.N_MODES, nums=dx.N_NUMS, headroom=dx.BODY_HR, out_bits=dx.BODY_BITS)
    assert_production_config(d, d.bank)
    kit = kit_for(sound)
    dm, bd = d.play(dx.hit_writes([(hit, stop, accent)], kit), n)
    out = dx.output_fx(np.zeros(n), 0, dm, GAIN_REG, bd, GAIN_REG)
    b = d.bank
    return dict(n=n, hit=hit, dmix=np.asarray(dm, dtype=np.int64), body=np.asarray(bd, dtype=np.int64),
                out=np.asarray(out, dtype=np.int64), bank=b, couple_en=d.couple_en, kit=kit)


def bank_is_stock(sound: str, accent: float, r: dict) -> bool:
    """The twin's int16 output equals the stock `render_drum_solo` sample for sample."""
    import run_case as rc
    x, _ = rc.render_drum_solo(sound, accent, frames=r["n"])
    return bool(np.array_equal(np.asarray(r["out"], dtype=np.float64) / 32768.0, x))


def float_out(r: dict) -> np.ndarray:
    """The output stage applied to the twin's float body: floor((dmix*g + body_f*g)/2^15), clamped."""
    acc = r["dmix"] * GAIN_REG + r["bank"].rec_body_f * GAIN_REG
    return np.clip(np.floor(acc / 32768.0), -32768, 32767)


# ---------------------------------------------------------------------------
# 3. Per-mode metrics
# ---------------------------------------------------------------------------
def block_peaks(x: np.ndarray, start: int) -> np.ndarray:
    nb = (len(x) - start) // BLOCK
    return np.abs(x[start:start + nb * BLOCK]).reshape(nb, BLOCK).max(axis=1) if nb > 0 else np.zeros(0)


def departure(fixed: np.ndarray, flt: np.ndarray, start: int, db: float = DEPART_DB):
    """First block (at/after `start`, aligned to absolute 10 ms blocks) where the
    block peaks differ by more than `db`. Returns (block_index, ms, float_level)
    or None. The 1e-9 floor mirrors the committed probe: a fixed state at exact
    zero against a live float is a departure, not a division by zero."""
    ex, ef = block_peaks(np.asarray(fixed, dtype=np.float64), start), block_peaks(np.asarray(flt, dtype=np.float64), start)
    if len(ex) == 0:
        return None
    r = np.abs(20 * np.log10(np.maximum(ex, 1e-9) / np.maximum(ef, 1e-9)))
    bad = np.nonzero(r > db)[0]
    if len(bad) == 0:
        return None
    i = int(bad[0])
    return i, (start + i * BLOCK) * 1000.0 / dx.SR, float(ef[i])


def tail_shape(y: np.ndarray) -> dict:
    """Exact structure of a fixed state tail: stuck at a value, an exact
    periodic limit cycle (smallest period), or neither."""
    w = np.asarray(y[-PERIOD_WINDOW:], dtype=np.int64)
    if len(w) == 0:
        return dict(kind="empty")
    if np.all(w == w[0]):
        return dict(kind="stuck", value=int(w[0]))
    for p in range(1, MAX_PERIOD + 1):
        if np.array_equal(w[p:], w[:-p]):
            return dict(kind="limit-cycle", period=p, p2p=int(w.max() - w.min()))
    return dict(kind="aperiodic", p2p=int(w.max() - w.min()))


def db_re(v: float, fs: float):
    return None if v <= 0 else round(20 * math.log10(v / fs), 2)


def last_event(r: dict, m: int) -> int:
    """Last frame at which mode m is still driven (post-numerator drive nonzero)
    or retuned (coefficients differ from the previous frame, after the first
    programming)."""
    b = r["bank"]
    n = r["n"]
    drv = np.nonzero(b.rec_x[:n, m] != 0)[0]
    last_drive = int(drv[-1]) if len(drv) else -1
    c = b.rec_coef[:n, m, :]
    ch = np.nonzero(np.any(c[1:] != c[:-1], axis=1))[0]
    last_change = int(ch[-1]) + 1 if len(ch) else -1
    return max(last_drive, last_change)


def mode_row(r: dict, m: int, sound: str, accent: float, reach: list) -> dict:
    """One (sound, accent, mode) cell: a measurement or a named REFUSED reason."""
    b = r["bank"]
    n = r["n"]
    row = dict(sound=sound, accent=accent, mode=m, reach=reach)
    y, f, c, cf = b.rec_y[:n, m], b.rec_f[:n, m], b.rec_c[:n, m], b.rec_cf[:n, m]
    if not (np.all(np.isfinite(f)) and np.all(np.isfinite(cf))):
        return dict(row, status="REFUSED", reason="non-finite float shadow")
    if not np.any(b.rec_x[:n, m] != 0):
        return dict(row, status="NOT-DRIVEN", reason="no drive reaches this mode in this sound")
    if np.any(np.abs(y) >= SAT_RAIL):
        return dict(row, status="REFUSED", reason="fixed state hit the 28-bit rail: the unclamped float is not its twin")
    last = last_event(r, m)
    if last >= n - 2 * BLOCK:
        return dict(row, status="REFUSED",
                    reason=f"driven/retuned until frame {last} of {n}: no free tail inside the render",
                    last_event_frame=last)
    start = ((last + 1) + BLOCK - 1) // BLOCK * BLOCK
    if float(np.max(np.abs(f[start:]))) < MIN_FLOAT_PEAK:
        return dict(row, status="NO-TAIL", reason="float state is below 1 LSB when the drive stops",
                    tail_start_ms=round(start * 1000.0 / dx.SR, 1))
    dep = departure(y, f, start)
    win = slice(n - PERSIST, n)
    res = (y[win] - f[win])
    res_pk = float(np.max(np.abs(res)))
    amp = int(b.rec_coef[n - 1, m, 2])
    bus = cf[win] - c[win]
    bus_pk = float(np.max(np.abs(bus)))
    bus_dc = float(np.mean(c[win] - cf[win]))
    i16_pk = bus_pk * GAIN_REG / 32768.0
    out = dict(row, status="MEASURED", amp=amp,
               tail_start_ms=round(start * 1000.0 / dx.SR, 1),
               float_peak_at_tail_start=round(float(np.max(np.abs(f[start:start + BLOCK]))), 1),
               state_residual_peak_lsb=round(res_pk, 2),
               state_residual_mean_lsb=round(float(np.mean(res)), 2),
               float_end_peak_lsb=round(float(np.max(np.abs(f[win]))), 3),
               fixed_end_peak_lsb=int(np.max(np.abs(y[win]))),
               tail=tail_shape(y),
               departure_ms=None if dep is None else round(dep[1], 1),
               float_level_at_departure_lsb=None if dep is None else round(dep[2], 1))
    if amp == 0:
        out.update(bus="REFUSED: tap-only mode (amp 0); reaches the bus through a nonlinear VCA path",
                   bus_residual_peak_lsb=None, bus_residual_dbfs=None,
                   int16_residual_peak_lsb=None, int16_residual_dbfs=None)
    else:
        out.update(bus_residual_peak_lsb=round(bus_pk, 3), bus_residual_mean_lsb=round(bus_dc, 3),
                   bus_residual_dbfs=db_re(bus_pk, BODY_FS),
                   int16_residual_peak_lsb=round(i16_pk, 4), int16_residual_dbfs=db_re(i16_pk, INT16_FS))
    return out


def voice_t20(r: dict, sound: str) -> dict:
    """T20 of the fixed int16 voice vs the float twin's, by the scorer's own
    estimator (run_case._t20_ms(0.005)); REFUSED if either side is refused."""
    import run_case as rc
    fx = np.asarray(r["out"], dtype=np.float64) / 32768.0
    fl = float_out(r) / 32768.0
    res = {}
    for name, x in (("fixed", fx), ("float", fl)):
        if not np.all(np.isfinite(x)) or float(np.max(np.abs(x))) == 0.0:
            return dict(status="REFUSED", reason=f"{name} render is silent or non-finite")
        try:
            y = rc.prepare(x, dx.SR, side=sound)
            e = rc._t20_ms(0.005)(y, dx.SR)
        except rc.Refused as why:
            return dict(status="REFUSED", reason=f"{name}: {why}")
        if not e.ok:
            return dict(status="REFUSED", reason=f"{name}: {e.reason}")
        res[name] = float(e.value)
    d = res["fixed"] - res["float"]
    return dict(status="MEASURED", t20_fixed_ms=round(res["fixed"], 2), t20_float_ms=round(res["float"], 2),
                delta_ms=round(d, 3), delta_pct=round(100.0 * d / res["float"], 3))


def voice_summary(r: dict, sound: str, accent: float) -> dict:
    n = r["n"]
    out = r["out"]
    flo = float_out(r)
    if not np.all(np.isfinite(flo)):
        raise Refused("non-finite float output")
    if int(np.max(np.abs(out))) == 0:
        raise Refused(f"{sound} accent {accent}: silent render")
    snd = np.nonzero(np.abs(out) >= 1)[0]
    win = slice(n - PERSIST, n)
    d = out[win] - flo[win]
    return dict(sound=sound, accent=accent, peak_int16=int(np.max(np.abs(out))),
                sounding_extent_ms=round((int(snd[-1]) - r["hit"]) * 1000.0 / dx.SR, 1),
                tail_fixed_int16_peak=int(np.max(np.abs(out[win]))),
                tail_fixed_int16_mean=round(float(np.mean(out[win])), 3),
                tail_float_int16_peak=int(np.max(np.abs(flo[win]))),
                output_residual_peak_lsb=round(float(np.max(np.abs(d))), 3),
                couple_en=int(r["couple_en"]), t20=voice_t20(r, sound))


def rows_for(sound: str, accent: float, check_stock: bool = False) -> dict:
    r = render_twin(sound, accent)
    if check_stock and not bank_is_stock(sound, accent, r):
        raise Refused(f"{sound} accent {accent}: twin render is not the stock render")
    reach = mode_reach(sound)
    rows = [mode_row(r, m, sound, accent, reach[m]) for m in range(dx.N_MODES)]
    return dict(voice=voice_summary(r, sound, accent), rows=rows, stock_checked=bool(check_stock))


def reconcile(tables: list[dict]) -> dict:
    """Counts must add up: every (render x mode) cell is exactly one status."""
    cells = sum(len(t["rows"]) for t in tables)
    by = {}
    for t in tables:
        for rw in t["rows"]:
            by[rw["status"]] = by.get(rw["status"], 0) + 1
    expect = len(tables) * dx.N_MODES
    if cells != expect or sum(by.values()) != cells:
        raise AssertionError(f"counts do not reconcile: {cells} cells, {expect} expected, {by}")
    return dict(renders=len(tables), cells=cells, by_status=dict(sorted(by.items())))


# ---------------------------------------------------------------------------
# 4. The known-answer apparatus (AC1)
# ---------------------------------------------------------------------------
KA_PINGS = (100, 1000, 10000, 100000)
KA_N = 96000
COMMITTED = ROOT / "docs" / "scorecard" / "tom-body-334" / "deadband.json"


def fixed_recursion(exc, a1, a2, **kw) -> np.ndarray:
    """The shipped one-mode fixed recursion (28-bit state, Q15, floor)."""
    b = ModalFx(modes=1, nums=1, headroom=0, out_bits=28, **kw)
    return np.asarray(b.process(exc, [(a1, a2, 65535)], num=[RAW]), dtype=np.float64)


def float_recursion(exc, a1, a2) -> np.ndarray:
    from scipy.signal import lfilter
    return lfilter([1.0], [1.0, -a1 / 2 ** 24, -a2 / 2 ** 24], np.asarray(exc, dtype=np.float64)) * 65535 / 65536


def analytic_impulse(a1, a2, n, amp=1.0) -> np.ndarray:
    """Closed form of y[k] = a1 y[k-1] + a2 y[k-2] + amp*delta[k-0]: independent of
    any recursion code. Poles are the roots of z^2 - a1 z - a2."""
    A, B = a1 / 2 ** 24, a2 / 2 ** 24
    disc = A * A + 4 * B
    if disc >= 0:
        raise ValueError("real poles: not a resonator")
    r = math.sqrt(-B)
    w = math.acos(A / (2 * r))
    k = np.arange(n)
    return amp * r ** k * np.sin((k + 1) * w) / math.sin(w)


def deadband_table(f0=90.0, q=25.0, levels=KA_PINGS, n=KA_N, fixed_fn=fixed_recursion, ping_frame=10) -> dict:
    a1, a2 = mf.pole_regs(f0, q)
    out = {}
    for lev in levels:
        exc = np.zeros(n, dtype=np.int64)
        exc[ping_frame] = lev
        y = fixed_fn(exc, a1, a2)
        yf = float_recursion(exc, a1, a2)
        dep = departure(y, yf, 0)
        out[str(lev)] = dict(departs_at_ms=None if dep is None else int(round(dep[1])),
                             float_level_there_lsb=None if dep is None else round(dep[2], 1),
                             ends_at_exact_zero=bool(np.all(y[-BLOCK:] == 0)),
                             fixed_tail_peak_lsb=float(np.max(np.abs(y[-BLOCK:]))),
                             max_abs_diff_lsb=round(float(np.max(np.abs(y - yf))), 2),
                             tail=tail_shape(y.astype(np.int64)))
    return out


def check_known_answer(table: dict, committed: dict | None = None) -> list[str]:
    """AC1: first >3 dB departure within one 10 ms block, fixed tail peak within
    1 state LSB, of the committed deterministic result. Raises
    KnownAnswerMismatch (an AssertionError) on any miss; returns the passes."""
    committed = json.loads(COMMITTED.read_text()) if committed is None else committed
    ok, bad = [], []
    if set(table) != set(committed):
        raise KnownAnswerMismatch(f"pings differ: {sorted(table)} vs committed {sorted(committed)}")
    for k, c in committed.items():
        t = table[k]
        if t["departs_at_ms"] is None or c["departs_at_ms"] is None:
            bad.append(f"ping {k}: departure {t['departs_at_ms']} vs committed {c['departs_at_ms']}")
        elif abs(t["departs_at_ms"] - c["departs_at_ms"]) > KA_CROSS_TOL_BLOCKS * BLOCK_S * 1000:
            bad.append(f"ping {k}: departs {t['departs_at_ms']} ms vs committed {c['departs_at_ms']} ms")
        elif abs(t["fixed_tail_peak_lsb"] - c["fixed_tail_peak_lsb"]) > KA_TAIL_TOL_LSB:
            bad.append(f"ping {k}: tail peak {t['fixed_tail_peak_lsb']} vs committed {c['fixed_tail_peak_lsb']}")
        else:
            ok.append(f"ping {k}")
    if bad:
        raise KnownAnswerMismatch("; ".join(bad))
    return ok


# --- guards with their defeating inputs ------------------------------------
def guard_float_twin_independent(f0=90.0, q=25.0, rel_tol=1e-9) -> None:
    """The float shadow equals the CLOSED FORM, not another run of itself."""
    a1, a2 = mf.pole_regs(f0, q)
    exc = np.zeros(4000)
    exc[0] = 1.0
    got = float_recursion(exc, a1, a2) * 65536 / 65535
    want = analytic_impulse(a1, a2, 4000)
    if not np.allclose(got, want, rtol=0, atol=rel_tol * float(np.max(np.abs(want)))):
        raise Refused("float shadow disagrees with the closed-form impulse response")


def guard_twin_not_fixed(table: dict) -> None:
    """A twin that IS the fixed run (or only its rounding) reports ~zero
    residual everywhere and would 'confirm' no defect. The 100-LSB known-deadband
    ping must differ from the float by more than the 0.5 LSB a rounding of the
    float could account for; if not, the comparison cannot see the effect."""
    if table["100"]["max_abs_diff_lsb"] <= 0.5:
        raise Refused("known-deadband ping differs from the float by <= 0.5 LSB: the twin is not independent "
                      "of the fixed path, so a zero residual would mean nothing")


# ---------------------------------------------------------------------------
# 5. The freeze (confirmation is run only against what was fixed first)
# ---------------------------------------------------------------------------
FREEZE_PATH = ROOT / "docs" / "scorecard" / "modal-deadband-350" / "freeze.json"


def _sha(rel: str) -> str:
    return hashlib.sha256((ROOT / rel).read_bytes()).hexdigest()


def frozen_spec() -> dict:
    thr = dict(DEPART_DB=DEPART_DB, BLOCK_S=BLOCK_S, PERSIST_S=PERSIST_S, MIN_FLOAT_PEAK=MIN_FLOAT_PEAK,
               KA_CROSS_TOL_BLOCKS=KA_CROSS_TOL_BLOCKS, KA_TAIL_TOL_LSB=KA_TAIL_TOL_LSB,
               CAPTURE_MARGIN_DB=CAPTURE_MARGIN_DB, GAIN_REG=GAIN_REG)
    return dict(thresholds=thr, conditions={k: [list(c) for c in v] for k, v in CONDITIONS.items()},
                production=PRODUCTION, source_sha256={f: _sha(f) for f in SOURCE_FILES})


def write_freeze(path=FREEZE_PATH) -> dict:
    spec = frozen_spec()
    spec["digest"] = hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(spec, indent=1, sort_keys=True) + "\n")
    return spec


def check_freeze(path=FREEZE_PATH, committed_check=True) -> dict:
    """REFUSE to produce the confirmation table unless the freeze exists, still
    describes the current thresholds / grids / model sources, and is committed
    (so a freeze written after looking is distinguishable in git history)."""
    if not path.is_file():
        raise Refused(f"no freeze at {path}: confirmation is not run before the sets are frozen")
    spec = json.loads(path.read_text())
    digest = spec.pop("digest", None)
    if digest != hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest():
        raise Refused("freeze file was edited after it was written")
    now = frozen_spec()
    if now != spec:
        diff = [k for k in now if now[k] != spec.get(k)]
        raise Refused(f"thresholds/grids/sources changed since the freeze: {diff}")
    if committed_check:
        try:
            rel = str(path.relative_to(ROOT))
        except ValueError:
            raise Refused(f"freeze {path} is outside the repository: it cannot be committed here") from None
        tracked = subprocess.run(["git", "-C", str(ROOT), "ls-files", "--error-unmatch", rel],
                                 capture_output=True, text=True)
        if tracked.returncode != 0:
            raise Refused("freeze is not committed: commit it before running confirmation")
        dirty = subprocess.run(["git", "-C", str(ROOT), "diff", "--quiet", "HEAD", "--", rel]).returncode != 0
        if dirty:
            raise Refused("freeze has uncommitted changes")
    return dict(spec, digest=digest)


# ---------------------------------------------------------------------------
# 6. Provenance
# ---------------------------------------------------------------------------
def provenance(command: str, extra_inputs: dict | None = None) -> dict:
    import provenance as pv
    st = pv.worktree_state()
    inputs = {f: pv.file_sha(ROOT / f) for f in SOURCE_FILES}
    inputs.update(extra_inputs or {})
    return dict(source_commit=st["commit"], dirty=st["dirty"], uncommitted_sha256=st["uncommitted_sha256"],
                branch=st["branch"], command=command, inputs=inputs, generated=pv.now())


# ---------------------------------------------------------------------------
# 7. Controls: properties x defects
# ---------------------------------------------------------------------------
def _ka_small() -> dict:
    return deadband_table(levels=(100, 10000), n=48000)


def _committed_subset() -> dict:
    c = json.loads(COMMITTED.read_text())
    return {k: c[k] for k in ("100", "10000")}


# Defects are functions of the fixed recursion (or the shadow); each is a plausible
# wrong implementation of exactly the thing under test.
def _mut_rounding(exc, a1, a2):            # the PR #543 hypothesis, activated
    return fixed_recursion(exc, a1, a2, rounding=True)


def _mut_float_stub(exc, a1, a2):          # right-shaped stub: returns the float twin, quantised
    return np.round(float_recursion(exc, a1, a2))


def _mut_a2_lsb(exc, a1, a2):              # wrong recursion: coefficient off by 1 LSB
    return fixed_recursion(exc, a1, a2 + 1)


def _mut_wider_state(exc, a1, a2):         # more fractional bits: deadband moves in LSB terms
    b = ModalFx(modes=1, nums=1, headroom=0, out_bits=32, state_bits=32, state_q=19)
    return np.asarray(b.process(exc * 16, [(a1, a2, 65535)], num=[RAW]), dtype=np.float64) / 16.0


def _mut_zero(exc, a1, a2):                # a recursion that does not execute
    return np.zeros(len(exc))


DEFECTS = {"rounding": _mut_rounding, "float-stub": _mut_float_stub, "a2+1lsb": _mut_a2_lsb,
           "wider-state": _mut_wider_state, "zeros": _mut_zero}


def p_known_answer(fn) -> bool:
    """True = property HOLDS (clean)."""
    try:
        check_known_answer(deadband_table(levels=(100, 10000), n=48000, fixed_fn=fn), _committed_subset())
        return True
    except KnownAnswerMismatch:
        return False


def p_twin_independent(fn) -> bool:
    try:
        guard_twin_not_fixed(deadband_table(levels=(100,), n=48000, fixed_fn=fn))
        return True
    except Refused:
        return False


def p_config_guard(kw) -> bool:
    try:
        bank = TwinBank(4, **kw)
        assert_production_config(dx.DrumsFx(), bank)
        return True
    except Refused:
        return False


PROD_KW = dict(modes=dx.N_MODES, nums=dx.N_NUMS, headroom=dx.BODY_HR, out_bits=dx.BODY_BITS)
CONFIG_DEFECTS = {"headroom=10": dict(PROD_KW, headroom=10), "state_q=17": dict(PROD_KW, state_q=17),
                  "state_bits=24": dict(PROD_KW, state_bits=24), "rounding": dict(PROD_KW, rounding=True),
                  "nums=16": dict(PROD_KW, nums=16)}


def controls_matrix() -> dict:
    """properties x defects. MOVED = the property flips from its clean value."""
    m = {}
    props = {"known-answer-table": p_known_answer, "twin-independent": p_twin_independent}
    clean = {k: f(fixed_recursion) for k, f in props.items()}
    for dn, fn in DEFECTS.items():
        for pn, pf in props.items():
            m[(pn, dn)] = "MOVED" if pf(fn) != clean[pn] else "BLIND"
    clean_cfg = p_config_guard(PROD_KW)
    for dn, kw in CONFIG_DEFECTS.items():
        m[("config-guard", dn)] = "MOVED" if p_config_guard(kw) != clean_cfg else "BLIND"
    return dict(clean=dict(clean, **{"config-guard": clean_cfg}), matrix=m)


# Which (property, defect) pairs MUST move. Anything else may be BLIND by design:
# a property is only obliged to see the defect it exists to catch.
EXPECTED_MOVED = ({("known-answer-table", d) for d in DEFECTS}
                  | {("twin-independent", "float-stub")}
                  | {("config-guard", d) for d in CONFIG_DEFECTS})


def controls_failures(res: dict) -> list:
    return [k for k in sorted(EXPECTED_MOVED) if res["matrix"].get(k) != "MOVED"]


def print_matrix(res: dict) -> None:
    print("clean:", res["clean"])
    pw = max(len(p) for p, _ in res["matrix"])
    for (p, d), v in sorted(res["matrix"].items()):
        print(f"  {p:{pw}s}  {d:14s} {v}")


# ---------------------------------------------------------------------------
# 8. External grounding
# ---------------------------------------------------------------------------
def ground(refs: str | None = None) -> dict:
    """What the Fischer captures can and cannot establish.

    1. DC / pedestal: `residual_dc.reference_gate` (the #152 precondition: a
       corpus manifest with sha256s, sample_rate, and a declared capture
       coupling) decides whether the recording's DC may be read at all.
    2. AC tail floor: the capture's own last-100 ms floor vs our int16 residual,
       valid only if that floor is flat (non-decaying).
    """
    import residual_dc as rd
    import run_case as rc
    from scipy.io import wavfile
    g = rd.reference_gate(refs)
    refdir = pathlib.Path(refs) if refs else rc.configured_refs()
    out = dict(gate=g, refdir=str(refdir), voices={})
    head = subprocess.run(["git", "-C", str(refdir), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    status = subprocess.run(["git", "-C", str(refdir), "status", "--porcelain"], capture_output=True, text=True).stdout
    out["corpus_git_head"] = head or None
    out["corpus_tree_clean"] = (status.strip() == "") if head else None
    out["pin"] = "85fbecf1bec32553395625ea659e2a56dfd7c0e1"
    out["pinned_ok"] = head == out["pin"] and out["corpus_tree_clean"] is True
    for v, (rel, _) in rc.REF_MAIN.items():
        p = refdir / rel
        if not p.is_file():
            out["voices"][v] = dict(status="REFUSED", reason=f"{rel} missing")
            continue
        sr, x = wavfile.read(str(p))
        x = np.asarray(x, dtype=np.float64)
        x = x.mean(axis=1) if x.ndim > 1 else x
        x /= 32768.0
        k = int(0.1 * sr)
        if len(x) < 3 * k:
            out["voices"][v] = dict(status="REFUSED", reason="capture shorter than 300 ms")
            continue
        last, prev = x[-k:], x[-2 * k:-k]
        pk = lambda s: float(np.max(np.abs(s - np.mean(s))))
        flat = pk(last) > 0 and abs(20 * math.log10(max(pk(prev), 1e-12) / pk(last))) <= 3.0
        out["voices"][v] = dict(status="MEASURED" if flat else "REFUSED", sha256=hashlib.sha256(p.read_bytes()).hexdigest()[:16],
                                sr=int(sr), seconds=round(len(x) / sr, 3),
                                tail_ac_peak_dbfs=round(20 * math.log10(max(pk(last), 1e-12)), 2),
                                tail_mean_dbfs=round(20 * math.log10(max(abs(float(np.mean(last))), 1e-12)), 2),
                                reason="" if flat else "last 100 ms is still decaying (not a noise floor)")
    return out


# ---------------------------------------------------------------------------
def _json_default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    raise TypeError(type(o))


def run_table(cond: str, out: pathlib.Path, stock_accent: float = 1.0) -> dict:
    if cond == "confirm":
        check_freeze()
    tabs = []
    for sound, acc in CONDITIONS[cond]:
        t = rows_for(sound, acc, check_stock=(acc == stock_accent))
        tabs.append(t)
        v = t["voice"]
        n_m = sum(1 for r in t["rows"] if r["status"] == "MEASURED")
        print(f"{cond} {sound} acc {acc}: peak {v['peak_int16']} tailpk {v['tail_fixed_int16_peak']} "
              f"measured modes {n_m} T20 {v['t20'].get('delta_pct', v['t20'].get('reason'))}", flush=True)
    rec = dict(condition=cond, reconcile=reconcile(tabs), tables=tabs,
               provenance=provenance(f"python3 tools/probes/modal_deadband.py --table {cond} --out {out}"))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rec, indent=1, default=_json_default) + "\n")
    print("reconcile:", rec["reconcile"])
    return rec


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--declared", action="store_true")
    ap.add_argument("--known-answer", action="store_true")
    ap.add_argument("--controls", action="store_true")
    ap.add_argument("--population", action="store_true")
    ap.add_argument("--freeze", action="store_true")
    ap.add_argument("--ground", action="store_true")
    ap.add_argument("--refs", default=None)
    ap.add_argument("--table", choices=("dev", "confirm"))
    ap.add_argument("--out", type=pathlib.Path)
    a = ap.parse_args(argv)
    if a.declared:
        print(json.dumps(frozen_spec(), indent=1))
    elif a.known_answer:
        t = deadband_table()
        guard_float_twin_independent()
        guard_twin_not_fixed(t)
        print(json.dumps(t, indent=1))
        print("reproduces committed:", check_known_answer(t))
    elif a.controls:
        res = controls_matrix()
        print_matrix(res)
        bad = controls_failures(res)
        print("BLIND where it must MOVE:", bad or "none")
        return 1 if bad else 0
    elif a.freeze:
        print(write_freeze()["digest"])
    elif a.table:
        run_table(a.table, a.out)
    elif a.population:
        tabs = []
        for s in dx.SOUND_NAMES:
            tabs.append(rows_for(s, 1.0, check_stock=True))
            print(s, "ok", flush=True)
        rec = dict(reconcile=reconcile(tabs), tables=tabs,
                   provenance=provenance("python3 tools/probes/modal_deadband.py --population --out " + str(a.out)))
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(rec, indent=1, default=_json_default) + "\n")
        print(rec["reconcile"])
    elif a.ground:
        g = ground(a.refs)
        if a.out:
            a.out.parent.mkdir(parents=True, exist_ok=True)
            a.out.write_text(json.dumps(g, indent=1, default=_json_default) + "\n")
        print(json.dumps(g, indent=1, default=_json_default))
    else:
        ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

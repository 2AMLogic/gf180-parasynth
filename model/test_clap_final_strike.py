"""The clap's final strike (contract revision 11, 15.3; plan084 "L2").

Expectations here are EVENT facts stated from the contract text -- which frames
strike, at what level, at which rate the level falls afterwards -- not a second
copy of `EnvFx.frame`. Each defect plan084 names has a mutant below, and
`test_every_mutant_is_caught_for_its_own_reason` requires the checks to catch
every one for the reason it was written for, while the clean envelope passes
all of them.

Envelope-level checks run on one `EnvFx`; the CP/MA and reset checks run a short
`DrumsFx` render and compare envelope STATE traces, which do not depend on the
noise, so they are exact.
"""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import drums_fx as dx  # noqa: E402

P = dx.CP_PERIOD
LAST = dx.CP_BURSTS * P
RATE = dx.rate_reg(dx.CP_BURST_TAU)
FRATE = dx.rate_reg(dx.CP_FINAL_TAU)
C = dx.BURST_C
ACC1, ACC_LOUD = 32768, 52000
STOP, CHOKE = dx.CP, dx.CB


# ---- mutants: each is one named defect --------------------------------------------
class WeakFinal(dx.EnvFx):
    """The old behaviour: the last re-strike is 13/16 of the previous one."""
    def frame(self, fire, accents):
        fr = self.frate
        t_next = min(self.t + 1, dx.T_MAX)
        if fr and not ((fire >> self.stop) & 1) and t_next == self.bursts * self.period and t_next >= self.hold:
            self.frate = 0
            super().frame(fire, accents)
            self.frate = fr
        else:
            super().frame(fire, accents)


class ShortFinalDecay(dx.EnvFx):
    """The final strike keeps the early strikes' short decay (FRATE ignored for the rate)."""
    def frame(self, fire, accents):
        fr = self.frate
        if fr and min(self.t + 1, dx.T_MAX) > self.bursts * self.period and not ((fire >> self.stop) & 1):
            self.frate = 0
            super().frame(fire, accents)
            self.frate = fr
        else:
            super().frame(fire, accents)


class StaleCapture(dx.EnvFx):
    """A retrigger does not refresh the captured fire level (the previous hit's is reused)."""
    def frame(self, fire, accents):
        old = self.fcap
        had = old != 0
        super().frame(fire, accents)
        if had and (fire >> self.stop) & 1:
            self.fcap = old


class ShiftedFinal(dx.EnvFx):
    """The final strike lands one period early (at 2P) and the 4th strike is omitted."""
    def frame(self, fire, accents):
        b = self.bursts
        if self.frate and b >= 2:
            self.bursts = b - 1
            super().frame(fire, accents)
            self.bursts = b
        else:
            super().frame(fire, accents)


def make(cls=dx.EnvFx, frate=FRATE, choke=15, hold=0):
    e = cls()
    e.set_ctl(dx.env_ctl(STOP, choke, hold, dx.CP_BURSTS, P))
    e.peak = dx.peak_reg(0.69)
    e.rate = RATE
    e.frate = frate
    return e


def acc(a=ACC1):
    v = [0] * dx.N_STOPS
    v[STOP] = a
    return v


def run(e, n, hits=(0,), accents=None, chokes=(), writes=None):
    """Level per frame. `hits`: frames the stop fires; `accents`: {frame: ACCENT
    register value from that frame on}; `writes`: {frame: fn(env)} applied at the
    start of that frame, as a register write is."""
    out, a = [], ACC1
    for f in range(n):
        if writes and f in writes:
            writes[f](e)
        if accents and f in accents:
            a = accents[f]
        fire = (1 << STOP) if f in hits else 0
        if f in chokes:
            fire |= 1 << CHOKE
        e.frame(fire, acc(a))
        out.append(e.level)
    return out


def fire_level(a=ACC1):
    return dx.usat((dx.peak_reg(0.69) * a) >> 15, dx.ENV_BITS)


# ---- the checks, each returning a reason string or "" ------------------------------
def check_schedule_and_levels(cls):
    lv = run(make(cls), LAST + 40)
    s0 = fire_level()
    s1 = (s0 * C) >> 16
    s2 = (s1 * C) >> 16
    rises = [f for f in range(1, len(lv)) if lv[f] > lv[f - 1]]
    if rises != [P, 2 * P, LAST]:
        return f"strike frames {rises} != {[P, 2 * P, LAST]}"
    if (lv[0], lv[P], lv[2 * P]) != (s0, s1, s2):
        return "early strikes are not fire, 13/16, 13/16^2"
    if lv[LAST] != s0:
        return f"final strike {lv[LAST]} != fire level {s0} (weak final strike)"
    return ""


def check_final_decay_rate(cls):
    lv = run(make(cls), LAST + 3)
    want = lv[LAST] - max(1, (lv[LAST] * FRATE) >> 16)
    if lv[LAST + 1] != want:
        return f"first decay after the final strike {lv[LAST + 1]} != FRATE step {want} (short final decay)"
    before = lv[LAST - 2] - max(1, (lv[LAST - 2] * RATE) >> 16)
    if lv[LAST - 1] != before:
        return "the early decay is not at RATE"
    return ""


@pytest.mark.parametrize("dt", [-1, 0, 1])
def test_retrigger_at_the_final_boundary_owns_its_state(dt):
    assert check_retrigger(dx.EnvFx, dt) == ""


def check_retrigger(cls, dt):
    h2 = LAST + dt
    lv = run(make(cls), h2 + LAST + 5, hits=(0, h2), accents={0: ACC1, h2: ACC_LOUD})
    fresh = run(make(dx.EnvFx), LAST + 5, hits=(0,), accents={0: ACC_LOUD})
    if lv[h2:] != fresh:
        return f"retrigger at final{dt:+d}: the second hit is not a fresh hit (stale capture or schedule)"
    return ""


def check_choke_leaves_no_ghost_final(cls):
    ch = 2 * P + 10
    e = make(cls, choke=CHOKE)
    lv = run(e, LAST + 50, hits=(0,), chokes=(ch,))
    if any(lv[ch:]):
        return f"level {max(lv[ch:])} after a choke at {ch}: a ghost final strike"
    return ""


def check_midnote_peak_and_accent_do_not_move_the_final(cls):
    def bump(env):
        env.peak = dx.peak_reg(0.2)
    lv = run(make(cls), LAST + 2, writes={100: bump}, accents={0: ACC1, 200: ACC_LOUD})
    if lv[LAST] != fire_level():
        return "a mid-note PEAK or ACCENT write changed the final strike"
    return ""


def check_midnote_frate_applies_from_next_frame(cls):
    new = dx.rate_reg(5e-3)
    def w(env):
        env.frate = new
    lv = run(make(cls), LAST + 12, writes={LAST + 5: w})
    want = lv[LAST + 4] - max(1, (lv[LAST + 4] * new) >> 16)
    if lv[LAST + 5] != want:
        return "a mid-note FRATE write did not apply from its frame"
    return ""


def check_disabled_is_revision_10(cls):
    lv = run(make(cls, frate=0), LAST + 3)
    s = fire_level()
    for _ in range(3):
        s = (s * C) >> 16
    if lv[LAST] != s:
        return "FRATE = 0 does not give revision 10's 13/16 fourth strike"
    return ""


CHECKS = {
    "schedule": check_schedule_and_levels,
    "final decay": check_final_decay_rate,
    "retrigger -1": lambda c: check_retrigger(c, -1),
    "retrigger 0": lambda c: check_retrigger(c, 0),
    "retrigger +1": lambda c: check_retrigger(c, 1),
    "choke": check_choke_leaves_no_ghost_final,
    "mid-note peak": check_midnote_peak_and_accent_do_not_move_the_final,
    "mid-note frate": check_midnote_frate_applies_from_next_frame,
    "disabled": check_disabled_is_revision_10,
}


@pytest.mark.parametrize("name", list(CHECKS))
def test_clean_envelope_passes(name):
    assert CHECKS[name](dx.EnvFx) == ""


MUTANTS = {
    WeakFinal: ("schedule", "weak final strike"),
    ShortFinalDecay: ("final decay", "short final decay"),
    StaleCapture: ("retrigger -1", "stale capture"),
    ShiftedFinal: ("schedule", "strike frames"),
}


@pytest.mark.parametrize("cls", list(MUTANTS), ids=lambda c: c.__name__)
def test_every_mutant_is_caught_for_its_own_reason(cls):
    check, reason = MUTANTS[cls]
    why = CHECKS[check](cls)
    assert why and reason in why, (cls.__name__, why)


# ---- DrumsFx level: RESET, CP <-> MA switching, and the MA leak control ------------
def trace(writes, n):
    d = dx.DrumsFx()
    d.play(sorted(writes, key=lambda t: t[0]), n)
    return d.trace["env"][[dx.E_CPBURST, dx.E_CPTAIL]], d


def hit(frame, sound, a=1.0):
    return dx.hit_writes([(frame, dx.SOUND_STOP[sound], a)], coef_seq=False)


def test_reset_mid_note_leaves_nothing_behind():
    kit = [(0, a, v) for a, v in dx.kit_with_sounds("CP")]
    tr, d = trace(kit + hit(10, "CP") + [(10 + 2 * P + 5, dx.A_RESET, 0)], LAST + 200)
    assert not tr[:, 10 + 2 * P + 5:].any()
    assert d.envs[dx.E_CPBURST].frate == 0 and d.envs[dx.E_CPBURST].fcap == 0


def _switch(first, second, leak=False):
    """Strike `first`, switch to `second` while it rings, strike `second`.
    Returns (trace from the second hit on, the same hit from a clean kit)."""
    k1 = [(0, a, v) for a, v in dx.kit_with_sounds(first)]
    pw = dx.preset_writes(second)
    if leak:
        pw = [(a, v) for a, v in pw if a != dx.A_ENV + dx.E_CPBURST * dx.ENV_STRIDE + 3]
    sw, h2 = 10 + P + 7, 10 + P + 40
    tr, _ = trace(k1 + hit(10, first) + [(sw, a, v) for a, v in pw] + hit(h2, second), h2 + LAST + 200)
    k2 = [(0, a, v) for a, v in dx.kit_with_sounds(second)]
    ref, _ = trace(k2 + hit(h2, second), h2 + LAST + 200)
    return tr[:, h2:], ref[:, h2:]


@pytest.mark.parametrize("pair", [("CP", "MA"), ("MA", "CP")])
def test_switching_while_active_inherits_nothing(pair):
    got, want = _switch(*pair)
    assert (got == want).all()


def test_control_ma_preset_that_does_not_clear_frate_is_caught():
    got, want = _switch("CP", "MA", leak=True)
    assert not (got == want).all(), "the MA leak control was not caught"


def test_ma_does_not_use_the_final_strike():
    d = dx.DrumsFx()
    d.play([(0, a, v) for a, v in dx.kit_with_sounds("MA")] + hit(10, "MA"), 3000)
    assert d.envs[dx.E_CPBURST].frate == 0 and d.envs[dx.E_CPBURST].n_final == 0

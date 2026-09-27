#!/usr/bin/env python3
"""The cowbell's two partials decay at their OWN rates (issue #107).

The real TR-808 cowbell's low line rings longer than its high line, so its
partial balance FALLS over the note. Until contract revision 15 both of our
partials were driven by one envelope pair (E_CBA/E_CBB) and the balance was
flat: right at one instant, wrong everywhere else.

WHY A TRAJECTORY AND NOT A TAU (#109, absorbed into #107): the older
`test_808_acceptance.test_cowbell_decay_matches_a_real_machine` fits ONE time
constant to the SUMMED signal. It cannot tell two partials at 105/124 ms from
two at 98/98 ms, so it cannot tell the fix from a compromise envelope that
matches the balance at one instant only. Everything here reads each partial
separately, at several instants, through the estimator
`tools/measure_partial_balance.py` validates against known damped partials
(declared error 1.1 dB) -- the same code that measured the machine.

HARDWARE-MEASURED, on cb8/CB.WAV of the Fischer corpus (a real TR-808,
sha256:1468cbd6c75a1f23); the runs are committed under
docs/scorecard/cowbell-107/ (`measure` and `decay`). The numbers are copied
here so this suite does not need the corpus; the tool re-derives them with it.

Every property carries a control that must fail it:
  * the pre-revision-15 wiring (both partials on one pair);
  * the COMPROMISE: the low partial on its own envelopes at the right level
    but the high partial's rate -- a level-only fix, the thing #107 forbids.
"""
import functools
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "audition"))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "tools"))
import drums_fx as dx                        # noqa: E402
import measure_partial_balance as mpb        # noqa: E402

# the machine, `measure_partial_balance.py measure`: balance (dB, 800-ish over
# 540-ish line) at each instant after the strike
REF_BALANCE_DB = {0.030: 15.35, 0.060: 14.86, 0.100: 14.58, 0.200: 14.17,
                  0.300: 12.87, 0.400: 11.33}
# the machine, `measure_partial_balance.py decay`: each line's own tau over 30-400 ms
REF_TAU_MS = {"lo": 121.4, "hi": 106.6}
AT = (0.030, 0.100, 0.400)                   # the issue's three instants
BAL_TOL_DB = 1.5        # the estimator's validated 1.1 dB, plus the record-to-record slack
FALL_TOL_DB = 1.5       # on the 30 -> 400 ms fall, which a flat balance misses by 4 dB
TAU_TOL = 0.10          # each partial's tau, relative: one machine, capacitors +-20 %
RATIO_TOL = 0.06        # tau_lo / tau_hi, absolute: the machine's is 1.139
RENDER_S = 0.45         # the trajectory needs 400 ms plus half a 20 ms window


def _cb_paths(kit):
    """(high, low) path words of the cowbell, by source oscillator."""
    img = dict(kit)
    words = [img[dx.A_PATH + p] for p in range(dx.N_PATH) if dx.A_PATH + p in img]
    by_src = {w & 31: w for w in words if ((w >> 20) & 31) == dx.M_CBBP}
    return by_src[dx.SRC_SQ + dx.SQPAIR[0]], by_src[dx.SRC_SQ + dx.SQPAIR[1]]


def _envs(word):
    return (word >> 5) & 31, (word >> 10) & 31


def _rate_addr(e):
    return dx.A_ENV + e * dx.ENV_STRIDE + 2


def kit_pre_107():
    """CONTROL: the low partial's path back on the high partial's envelopes --
    the wiring every revision before 15 shipped."""
    img = dict(dx.kit_with_sounds("CB"))
    hi, lo = _cb_paths(img.items())
    e1, e2 = _envs(hi)
    img[dx.A_PATH + dx.P_CBB] = (lo & ~((1 << 15) - (1 << 5))) | (e1 << 5) | (e2 << 10)
    return sorted(img.items())


def kit_compromise():
    """CONTROL: the level-only fix. The low partial keeps its own envelopes and
    its own (lower) level, but its tail decays at the HIGH partial's rate, so
    the balance is right at one instant and flat."""
    img = dict(dx.kit_with_sounds("CB"))
    hi, lo = _cb_paths(img.items())
    img[_rate_addr(_envs(lo)[1])] = img[_rate_addr(_envs(hi)[1])]
    return sorted(img.items())


@functools.lru_cache(maxsize=None)
def _render(which):
    kit = {"shipped": None, "pre_107": kit_pre_107(), "compromise": kit_compromise()}[which]
    return mpb.render_ours("CB", seconds=RENDER_S, kit=kit)


@functools.lru_cache(maxsize=None)
def _balance(which):
    x, sr = _render(which)
    return mpb.balance_at(x, sr, "CB", tuple(REF_BALANCE_DB))


def trajectory_failures(bal):
    """Every way `bal` (instant -> dB) misses the machine's trajectory; empty
    when it follows it. A function so the controls are judged by exactly the
    check the kit is."""
    bad = []
    for t in AT:
        err = bal[t] - REF_BALANCE_DB[t]
        if abs(err) > BAL_TOL_DB:
            bad.append(f"at {t*1e3:.0f} ms ours {bal[t]:.2f} dB vs machine "
                       f"{REF_BALANCE_DB[t]:.2f} ({err:+.2f})")
    fall = bal[AT[0]] - bal[AT[-1]]
    ref_fall = REF_BALANCE_DB[AT[0]] - REF_BALANCE_DB[AT[-1]]
    if abs(fall - ref_fall) > FALL_TOL_DB:
        bad.append(f"fall {AT[0]*1e3:.0f}->{AT[-1]*1e3:.0f} ms is {fall:.2f} dB, "
                   f"the machine's {ref_fall:.2f}")
    return bad


def test_the_two_partials_are_on_disjoint_envelopes():
    """Structural: no envelope drives both partials, and the low partial's
    tail rate is not the high partial's. `kit_with_sounds("CB")` is the image
    the host sends."""
    kit = dx.kit_with_sounds("CB")
    hi, lo = _cb_paths(kit)
    assert not set(_envs(hi)) & set(_envs(lo)), (_envs(hi), _envs(lo))
    img = dict(kit)
    assert img[_rate_addr(_envs(lo)[1])] < img[_rate_addr(_envs(hi)[1])], \
        "the low partial's tail must decay SLOWER than the high partial's"


def test_balance_follows_the_machine_over_the_note():
    """[hardware-measured] At 30, 100 and 400 ms -- not one window, not one
    aggregate tau -- the balance is within 1.5 dB of the machine's, and it
    falls from 30 to 400 ms by the machine's 4.0 dB to within 1.5."""
    bal = _balance("shipped")
    bad = trajectory_failures(bal)
    table = "  ".join(f"{t*1e3:.0f}ms {bal[t]:.2f}/{REF_BALANCE_DB[t]:.2f}" for t in REF_BALANCE_DB)
    assert not bad, f"ours/machine: {table}; " + "; ".join(bad)


def test_each_partial_decays_at_the_machines_own_rate():
    """[hardware-measured] Each line's own tau over 30-400 ms within 10 % of
    the machine's, and the low line rings longer by the machine's ratio."""
    x, sr = _render("shipped")
    fit = mpb.partial_decay(x, sr)
    for tag in ("lo", "hi"):
        assert fit[tag]["ok"], f"{tag}: REFUSED {fit[tag]['reason']}"
        got, want = fit[tag]["tau_ms"], REF_TAU_MS[tag]
        assert abs(got / want - 1) <= TAU_TOL, f"{tag} tau {got:.1f} ms, machine {want:.1f}"
    ratio = fit["lo"]["tau_ms"] / fit["hi"]["tau_ms"]
    ref = REF_TAU_MS["lo"] / REF_TAU_MS["hi"]
    assert abs(ratio - ref) <= RATIO_TOL, f"tau lo/hi {ratio:.3f}, machine {ref:.3f}"


def test_control_the_shared_pair_fails_the_trajectory():
    """The wiring #107 replaced must fail the check that passes the kit --
    otherwise the check is not what decided."""
    bad = trajectory_failures(_balance("pre_107"))
    assert any(b.startswith("fall") for b in bad), bad


def test_control_a_compromise_envelope_fails_the_trajectory():
    """The level-only fix must fail on the FALL, however close it sits at some
    instant: #107's 'optimises the integral and matches at no instant'."""
    bad = trajectory_failures(_balance("compromise"))
    assert any(b.startswith("fall") for b in bad), bad


def test_control_a_compromise_envelope_fails_the_tau_ratio():
    x, sr = _render("compromise")
    fit = mpb.partial_decay(x, sr)
    assert fit["lo"]["ok"] and fit["hi"]["ok"]
    ratio = fit["lo"]["tau_ms"] / fit["hi"]["tau_ms"]
    assert abs(ratio - REF_TAU_MS["lo"] / REF_TAU_MS["hi"]) > RATIO_TOL, ratio

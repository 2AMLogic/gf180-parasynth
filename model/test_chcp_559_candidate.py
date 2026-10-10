"""#559's disabled CH candidate (`kit_808_candidate_559`) and the registered
tunables it moves. What these pin:

  * the SHIPPED kit is byte-identical to origin/main's (the tunables were made
    integer constants for tools/sensitivity.py; nothing that ships moved);
  * the candidate differs from it in exactly the CH high-pass's two pole
    registers, and nothing else -- not the hat band-pass (which improved CH and
    OH but regressed CY, docs/scorecard/chcp-559), not the clap;
  * selecting any circuit's sound on the candidate keeps the candidate (a
    preset that rewrote the CH high-pass would silently revert it -- the trap
    the CP tail has, because preset_writes('CP') rewrites E_CPTAIL);
  * the OH -> CH choke still works under the candidate.
"""
import hashlib

import numpy as np
import pytest

import drums_fx as dx

#: dx._kit_sha256(kit_808()) at origin/main cd066543, before #559 touched it.
KIT808_SHIPPED_SHA256 = "3d239bf8453635efcc0750d611c83529367961c1f7613dbb1d19d4047a90e458"
#: every sound's selected image, same tree.
ALL_SOUNDS_SHA256 = "346194ddd938c83b54eb316b76050ce8628c61b89c594c63d79beb0143b27b42"


def test_the_shipped_kit_did_not_move():
    assert dx._kit_sha256(dx.kit_808()) == KIT808_SHIPPED_SHA256
    got = hashlib.sha256(repr([dx.kit_with_sounds(s) for s in dx.SOUND_NAMES]).encode()).hexdigest()
    assert got == ALL_SOUNDS_SHA256
    assert dx.kit_808(tuning=None) == dx.kit_808(tuning={}) == dx.kit_808()


def test_the_integer_tunables_are_the_literals_they_replaced():
    assert dx.CH_HP_Q_X10 / 10 == 2.5 and dx.HAT_BP_Q_X10 / 10 == 6.0
    assert dx.CP_TAIL_TAU == 80e-3 and dx.CP_TAIL_PEAK_LEVEL == 0.22


def test_the_candidate_moves_only_the_ch_high_pass_poles():
    a, b = dict(dx.kit_808()), dict(dx.kit_808_candidate_559())
    assert set(a) == set(b)
    base = dx.A_MODE + dx.M_CHHP * dx.MODE_STRIDE
    assert sorted(k for k in a if a[k] != b[k]) == [base, base + 1]
    assert dx.CANDIDATE_559 == {"CH_HP_Q_X10": 5}
    a1, a2 = dx.pole_regs(11700.0, 0.5)
    assert (b[base], b[base + 1]) == (a1 & ((1 << 26) - 1), a2 & ((1 << 26) - 1))


@pytest.mark.parametrize("sound", dx.SOUND_NAMES)
def test_selecting_any_sound_keeps_the_candidate(sound):
    base = dx.A_MODE + dx.M_CHHP * dx.MODE_STRIDE
    cand = dict(dx.kit_808_candidate_559())
    img = dict(dx.kit_with_sounds(sound, kit=dx.kit_808_candidate_559()))
    assert (img[base], img[base + 1]) == (cand[base], cand[base + 1]), sound


def test_selecting_a_sound_control_the_clap_preset_does_revert_its_own_registers():
    """The control for the test above: it can see a revert. preset_writes('CP')
    rewrites the clap tail, so a kit whose tail was changed comes back shipped."""
    e = dx.A_ENV + dx.E_CPTAIL * dx.ENV_STRIDE
    changed = dict(dx.kit_808())
    changed[e + 2] = dx.rate_reg(0.250)
    back = dict(dx.kit_with_sounds("CP", kit=sorted(changed.items())))
    assert back[e + 2] == dict(dx.kit_808())[e + 2] != changed[e + 2]


def test_an_unknown_tuning_name_refuses():
    with pytest.raises(KeyError, match="not a registered tunable"):
        dx.kit_808(tuning={"CH_HP_Q": 5})


def test_the_oh_ch_choke_survives_the_candidate():
    d = dx.DrumsFx()
    n = 3000
    hits = [(100, dx.OH, 1.0), (1500, dx.CH, 1.0)]
    d.play(dx.hit_writes(hits, dx.kit_808_candidate_559()), n)
    oh = d.trace["env"][dx.E_OH]
    assert oh[1400] > 0, "OH must still be sounding before the CH strike"
    assert np.all(oh[1501:] == 0), "CH must choke OH (reference 11)"
    assert d.trace["env"][dx.E_CH][1501] > 0

"""The contract's tables are the model's, and the hashes the contract states
are the ones the revisions were written with: revision 1's five, unchanged
through revisions 2, 3 and 4 (4 changed no arithmetic); K_ROM32 from DR 0006
(revision 3); NOISE64 from DR 0008 (revision 5), which KIT808 joined in the
same revision and then LEFT in revision 6 -- the kit was fitted to a real
TR-808 (DR 0009, DR 0010) and its hash moved. It moved again in revision 7
(the snare) and in revision 10, which is the first move that is
not a refit: six more SOUNDS, 100 writes -> 147. Revision 11 records the
tom level rebalance after the measured pitch correction. Revision 13 is the
clap's final strike (plan084 "L2"): one new register write and two changed
values, 147 writes -> 148. It is the first pinned table in
this contract's history to change, and the pair below is how that is visible
rather than quiet: REV5 holds what rev 5 stated, REV6 what rev 6 states, and
`test_the_only_hash_that_ever_moved_is_the_kits` asserts exactly which one.

    .venv/bin/python -m pytest spec/reference -q

The literal hashes below are deliberately duplicated from the contract: if
the model's tables change, BOTH this test and `gen_tables.py --check` fail,
and the right response is a revision bump, not an update of the literals.
"""
import math
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gen_tables as gt

REV3 = {
    "NOTE_INC":      "e771e6b7b39d3941c471b772bfb5cdca398b78ee7fa964c3c90388d2cc888ba4",
    "SINE_Q256":     "66cfc2e50e0ea6c326d698bd2aa14cc8b67f8e518530c9bb9c3f8f62d0fd19a0",
    "SINE_FULL1024": "41a30c959df1413245a6817c2d398c9d571f33460b34634b433c0717fb3c52ea",
    "TANH16":        "65a5fa4b38b807735e09eed0eadd49b2a42850151daa47e3abb97a1641542c04",
    "TANH16_ROM":    "3aa73628ec4f1b6eec99e77524a5460813c531dd9703a8fdea6df799dc91efeb",
    "G_ROM128":      "c5ee86efeffbe3cadd040ca3851b5c90806f05f9fab13d5f3cea1cf7730fbe2a",
    "K_ROM32":       "514d0ba224df47ab47e4c6b5454666b88568f3172bacdc2e17baba3c5b6c6e1a",
}
REV5 = {
    "NOISE64":       "41f2adb399b60f0d7f1d77a03bf004d9b2ec28ab220f476cfafe96c36f99a613",
}
# KIT808 as revision 5 stated it, kept so the change is a visible fact and not
# an edited literal. Revision 6 refitted the kit to the reference recording.
KIT808_REV5 = "819ef081eca2aaff17c8f63d9653ee8d62dc69a6f6e48b08082161b8db66b3dc"
# ... and as revision 6 stated it. Revision 7 moved it a second time: the
# snare's partial balance and snappy rate, both measured on the same machine.
KIT808_REV6 = "06f47f307efbd44317e2aa0fcba99cba96f7cf747cdeffdc6c471b94b869914a"
# ... and as revision 7 stated it. Revision 10 moved it a THIRD time, for a
# different kind of reason from the first two: not a refit of a voice that was
# already there, but SIX MORE SOUNDS -- the mid tom/conga, the claves/rimshot
# and the cymbal circuits, and the conga and maracas presets. The kit went from
# 100 writes to 147. Checked before re-pinning: KIT808 is the ONLY hash that
# moved against revision 9's pins, G_ROM128 and K_ROM32 included.
KIT808_REV7 = "7ea9a2e3ae152f3aa7e65ad33b43b154aa8c513105ccde0f2bc6605ee6ae6ec4"
REV7 = {
    "KIT808":        KIT808_REV7,
}
REV10 = {
    "KIT808":        "feb8c6fdeab4c89e506bbfebf6114c82d698d09064933e1a7292998905ba0671",
}
# Revision 11 records #154's tom level rebalance after correcting the sweep.
# Only MODE_AMP[11..13] changes; retain REV10 as the historical pin.
REV11 = {
    "KIT808": "a43fe2a7d596a417ae3c9949fe43f94cc8e64482f7cac6ede5bc271009a5ff19",
}
# Revision 13 (plan084 "L2", the clap's final strike; revision 12 moved no
# table). KIT808 moves a FIFTH time, and only on the clap's two envelopes:
# ENV_CTL[8] 3 -> 4 strikes at period 480 -> 511, the NEW write ENV_FRATE[8]
# (tau 20 ms), and ENV_RATE[9] tau 47 -> 80 ms. 147 writes -> 148.
# `test_revision_13_changes_only_the_clap_final_strike_registers` rebuilds
# REV11 from the live image by undoing exactly those, so updating this literal
# to whatever the generator produced cannot pass on its own.
REV13 = {
    "KIT808": "321a93546cfa5ffab03b3cf91557580ea7655ada933ce380c81cd07597a9b683",
}
# the three clap writes revision 13 changed: address -> (revision 11's value,
# None where the write did not exist; revision 13's value)
REV13_CLAP = {0x60: (125960438, 134152438),   # ENV_CTL[8]: bursts 2 -> 3, period 480 -> 511
              0x63: (None, 68),               # ENV_FRATE[8]: new, tau 20 ms
              0x66: (29, 17)}                 # ENV_RATE[9]: tail tau 47 -> 80 ms
# Revision 9 (DR 0011) moved the cutoff ROM, and with it the resonance-
# compensation ROM derived from it: Huovilainen's `fcr` tuning polynomial and
# one constant scale went into `voice_fx.make_g_rom`. These are the SECOND and
# THIRD pinned tables ever to move, and the first to move for a reason other
# than a fit to a recording.
G_ROM128_REV3 = "c5ee86efeffbe3cadd040ca3851b5c90806f05f9fab13d5f3cea1cf7730fbe2a"
K_ROM32_REV3 = "514d0ba224df47ab47e4c6b5454666b88568f3172bacdc2e17baba3c5b6c6e1a"
REV9 = {
    "G_ROM128":      "7d03fb29bdf97a177c31274f95864cb69111b70b7164dc5eb05c0e04a6f83414",
    "K_ROM32":       "19da75793533fc6d34eed44858cac4e934388d20ab0916fb7e54fea0afe69c28",
}
# Revision 9 also ADDS one table -- the modulation path's 2^x ROM (DR 0012).
# Adding is not moving: no revision ever pinned a different EXP_ROM65.
REV9_NEW = {
    "EXP_ROM65":     "6a1cbbf81f383149c4ececcbd0eef37e979c24e9f700bfd6efc31185f520d557",
}
REV3_STILL = {k: v for k, v in REV3.items() if k not in REV9}


def test_committed_images_and_contract_match_the_model():
    """Every hex image, the appendix block, rtl-sketch/tanh16.hex and every
    stated hash must be exactly what the model generates now."""
    assert gt.main(["--check"]) == 0


def test_rev3_hashes_are_unchanged_and_rev5_adds_two():
    """Revision 5 added NOISE64 and KIT808 and changed no existing table;
    revision 6 changed KIT808 and nothing else."""
    got = {name: gt.sha(vals) for name, vals, _, _, _ in gt.tables()}
    assert {k: got[k] for k in REV3_STILL} == REV3_STILL
    assert {k: got[k] for k in REV5} == REV5
    assert {k: got[k] for k in REV13} == REV13
    assert {k: got[k] for k in REV9} == REV9
    assert {k: got[k] for k in REV9_NEW} == REV9_NEW
    assert set(got) == set(REV3) | set(REV5) | set(REV10) | set(REV9_NEW)


def test_exactly_three_pinned_tables_have_ever_moved():
    """Loudly, because a pinned table moving is the expensive kind of change.
    KIT808 moved five times: revisions 6 and 7 were fits to a real machine,
    revision 10 is six more SOUNDS (the kit went 100 -> 147 writes),
    revision 11 records the tom level rebalance, and revision 13 is the
    clap's final strike (147 -> 148 writes). G_ROM128
    and K_ROM32 moved once, together, in revision 9 (DR 0011's tuning
    polynomial -- K_ROM32 is derived from G_ROM128, so it could not not move).
    Every other table in the contract's history is still what revision 1 or 3
    or 5 pinned -- INCLUDING TANH16_ROM, whose guard word DR 0013 measured and
    deliberately left alone. A fourth entry here means a fourth pinned table
    has moved and needs its own revision and its own paragraph."""
    got = {name: gt.sha(vals) for name, vals, _, _, _ in gt.tables()}
    was = {**REV3, **REV5, "KIT808": KIT808_REV5}   # EXP_ROM65 did not exist then
    moved = sorted(k for k, v in was.items() if got[k] != v)
    assert moved == ["G_ROM128", "KIT808", "K_ROM32"], f"against revision 5's pins: {moved}"
    assert got["KIT808"] == REV13["KIT808"]
    # The check that makes re-pinning honest rather than a rubber stamp: against
    # revision 9's pins -- the ones immediately before this change -- the kit
    # must be the ONLY thing that moved.
    nine = {**REV3_STILL, **REV5, **REV9, **REV9_NEW, "KIT808": KIT808_REV7}
    assert sorted(k for k, v in nine.items() if got[k] != v) == ["KIT808"]
    assert {k: got[k] for k in REV9} == REV9
    assert got["TANH16_ROM"] == REV3["TANH16_ROM"], "DR 0013's guard word is NOT taken"


def test_the_tuning_polynomial_is_the_only_thing_that_moved_the_cutoff_rom():
    """DR 0011, and the guard against the ROM having moved for some OTHER
    reason: the revision-3 image is exactly what `make_g_rom(tune=False)` still
    builds, so the whole difference between the two pins is the polynomial and
    the trim."""
    import voice_fx as vf
    assert gt.sha([int(v) for v in vf.make_g_rom(tune=False)]) == G_ROM128_REV3


def test_spot_values_the_contract_quotes():
    ni = gt.note_inc()
    assert (ni[0], ni[69], ni[127]) == (2858, 153791, 4384395)
    assert max(ni) < (1 << 23)
    sq = gt.sine_q256()
    assert len(sq) == 256 and sq[0] == 101 and sq[255] == 32767
    th = gt.tanh16()
    assert len(th) == 16 and th[0] == 0 and th[1] == 8025 and th[15] == 32731
    assert gt.tanh16_rom()[16] == 32767 != round(math.tanh(4.0) * 32767)   # DR 0013, not taken
    gr = gt.g_rom128()
    assert len(gr) == 129 and gr[0] == 0 and gr[1] == 1116 and gr[128] == 62445
    assert all(b > a for a, b in zip(gr, gr[1:]))          # strictly increasing
    kr = gt.k_rom32()
    assert len(kr) == 33 and kr[0] == 32800 and max(kr) == 39875 and kr[22] == 33837
    er = gt.exp_rom65()
    assert len(er) == 65 and er[0] == 0 and er[64] == 32768 and er[32] == 13573
    assert all(b > a for a, b in zip(er, er[1:]))
    nz = gt.noise64()
    assert len(nz) == 64 and nz[0] == 1 and all(-32768 <= v <= 32767 for v in nz)
    kit = gt.kit808()
    assert len(kit) == 148 and all(0 <= a < 256 and 0 <= v < (1 << 32) for a, v in kit)
    assert kit[0] == (0x20, 71758)                           # OSC_INC[0]: 205.3 Hz
    addrs = [a for a, _ in kit]
    assert len(set(addrs)) == len(addrs), "the kit writes an address twice"
    # The three blocks revision 10 moved, spot-checked at their new bases so a
    # silent move back would fail here and not only in the hash.
    assert max(a for a in addrs if 0x90 <= a < 0xB0) == 0x90 + 23 - 1   # PATH, 23 of them
    assert max(a for a in addrs if 0xB0 <= a) == 0xB0 + 16 * 4 - 1      # MODE, 16 modes
    assert 0xFF not in addrs, "a mode register landed on RESET"


def test_note_inc_is_the_siblings():
    """Same formula, same 128 values as gf180-polysynth's Appendix A."""
    assert gt.sha(gt.note_inc()) == "e771e6b7b39d3941c471b772bfb5cdca398b78ee7fa964c3c90388d2cc888ba4"


def _kit_rev11():
    """Revision 11's image, rebuilt from the live one by undoing exactly
    revision 13's three clap writes (REV13_CLAP), in the live write order."""
    kit = gt.kit808()
    for addr, (_, after) in REV13_CLAP.items():
        assert dict(kit)[addr] == after, hex(addr)
    return [(a, REV13_CLAP[a][0] if a in REV13_CLAP else v) for a, v in kit
            if not (a in REV13_CLAP and REV13_CLAP[a][0] is None)]


def test_revision_13_changes_only_the_clap_final_strike_registers():
    """Reconstruct revision 11's image independently of the new hash pin: undo
    the three documented clap writes and nothing else, and it must hash to
    REV11. A different register, a moved write, or an extra change cannot pass
    by updating REV13 alone. The values are also decoded, so the pin says what
    the contract says (drums_fx's own encoders, not literals)."""
    import drums_fx as dx
    assert gt.sha([(a << 32) | v for a, v in _kit_rev11()]) == REV11["KIT808"]
    burst = dx.A_ENV + dx.E_CPBURST * dx.ENV_STRIDE
    tail = dx.A_ENV + dx.E_CPTAIL * dx.ENV_STRIDE
    assert set(REV13_CLAP) == {burst, burst + 3, tail + 2}
    assert REV13_CLAP[burst] == (dx.env_ctl(dx.CP, 15, 0, 2, 480),
                                 dx.env_ctl(dx.CP, 15, 0, 3, 511))
    assert REV13_CLAP[burst + 3][1] == dx.rate_reg(20e-3)
    assert REV13_CLAP[tail + 2] == (dx.rate_reg(47e-3), dx.rate_reg(80e-3))


def test_revision_11_changes_only_the_three_documented_tom_levels():
    """Reconstruct the previous image independently of the new hash pin.
    A different register, write order, or amplitude change cannot pass merely
    by updating REV11 to whatever the generator produced. Since revision 13 it
    starts from revision 11's image (`_kit_rev11`), not the live one.
    """
    kit = _kit_rev11()
    changes = {0xDE: (0x201, 0x13B), 0xE2: (0x296, 0x196),
               0xE6: (0x42C, 0x28B)}
    for addr, (_, after) in changes.items():
        assert dict(kit)[addr] == after
    old = [(addr << 32) | (changes[addr][0] if addr in changes else value)
           for addr, value in kit]
    assert gt.sha(old) == REV10["KIT808"]

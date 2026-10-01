#!/usr/bin/env python3
"""Unit tests for the host of fpga/spi_host.py.

These are the checks that do not need a simulator. The ones that do -- above
all "does the landing-frame prediction match the CS_N pin" -- are in
fpga/verify_fixture.py, and they are the ones that caught this model's only
real defect so far (k_rst 8 vs 10).
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest                                      # noqa: E402
import spi_host as sh                              # noqa: E402
import drums_fx as dx                              # noqa: E402
import fixtures                                    # noqa: E402
from spi_host import BENCH, CONTRACT, FRAME_PS     # noqa: E402

LINKS = [BENCH, CONTRACT]


# ---- the frame format, contract 5.4 -----------------------------------------
def test_the_transaction_is_48_bits_and_round_trips():
    for flag in (0, 1):
        for sec in (0, 1):
            for addr in (0x00, 0x3F, 0x80, 0xC0, 0xFF):
                for data in (0, 1, 0x7FFFFFFF, 0xFFFFFFFF, 0x0DEFACED):
                    w = sh.encode_tx(flag, sec, addr, data)
                    assert 0 <= w < (1 << 48)
                    assert sh.decode_tx(w) == (flag, sec, addr, data)
                    assert len(sh.tx_bits(flag, sec, addr, data)) == sh.TX_BITS


def test_the_six_reserved_bits_are_zero_and_a_word_that_sets_them_is_rejected():
    assert (sh.encode_tx(1, 1, 0xFF, 0xFFFFFFFF) >> 41) & 0x3F == 0
    with pytest.raises(ValueError):
        sh.decode_tx(sh.encode_tx(0, 0, 0, 0) | (1 << 44))


def test_the_bits_go_out_msb_first():
    b = sh.tx_bits(1, 1, 0xA5, 0x0000_0001)
    assert b[0] == 1                       # F
    assert b[1:7] == [0] * 6               # reserved
    assert b[7] == 1                       # SEC
    assert b[8:16] == [1, 0, 1, 0, 0, 1, 0, 1]     # A = 0xA5
    assert b[-1] == 1 and sum(b[16:-1]) == 0       # D = 1


# ---- the budget --------------------------------------------------------------
@pytest.mark.parametrize("link", LINKS)
def test_a_transaction_is_longer_than_a_frame(link):
    """The whole budget in one assertion: at most one write lands per frame."""
    assert link.tx_period_ps > FRAME_PS
    assert link.writes_per_frame < 1.0
    assert link.sck_hz <= sh.SCK_MAX_HZ


@pytest.mark.parametrize("link", LINKS)
def test_the_cs_n_gap_meets_the_contract(link):
    assert link.cs_gap_ps >= 1e12 * sh.CS_GAP_CYCLES / sh.F_CORE_HZ - 1


@pytest.mark.parametrize("link", LINKS)
def test_land_frame_is_monotonic_in_the_start_time(link):
    t0 = link.t_rst_ps
    prev = -1
    for i in range(0, 4000, 37):
        f = link.land_frame(t0 + i * 10_000)
        assert f >= prev
        prev = f


@pytest.mark.parametrize("link", LINKS)
def test_start_for_land_is_the_latest_start_that_still_lands_there(link):
    for frame in (5, 50, 517, 2048):
        t = link.start_for_land(frame)
        assert link.land_frame(t) <= frame
        assert link.land_frame(t + FRAME_PS) > frame


# ---- the scheduler -----------------------------------------------------------
@pytest.mark.parametrize("link", LINKS)
def test_transactions_never_overlap(link):
    host, _, _ = fixtures.bar_808(short=True)
    sched = host.schedule(link)
    for a, b in zip(sched, sched[1:]):
        assert b.start_ps >= a.start_ps + link.tx_period_ps


@pytest.mark.parametrize("link", LINKS)
def test_nothing_lands_before_the_frame_it_was_asked_for(link):
    host, _, _ = fixtures.bar_808(short=True)
    for p in host.schedule(link):
        assert p.land >= p.w.frame, f"{p.w.tag} landed early: {p.land} < {p.w.frame}"


@pytest.mark.parametrize("link", LINKS)
def test_every_musical_instant_lands_in_its_own_frame(link):
    host, _, _ = fixtures.bar_808(short=True)
    assert sh.check(host.schedule(link))["conflicts"] == []


@pytest.mark.parametrize("link", LINKS)
def test_two_musical_instants_in_one_frame_are_quantised_not_dropped(link):
    """A key down on the beat a drum hit lands on. Both must still happen, in
    order, and the report must say how far the second moved."""
    host = sh.MusicHost()
    host.load(0)
    host.hits([(4000, dx.BD, 1.0)])
    host.keys([(4000, "on", 45)])
    sched = host.schedule(link)
    st = sh.check(sched)
    assert st["conflicts"] == []
    anchors = [p for p in sched if p.w.anchor]
    assert len(anchors) == len({p.land for p in anchors})          # one frame each
    # one transaction is the floor: two instants cannot share a frame
    assert 0 < st["anchor_jitter_us"] <= 2 * link.tx_period_ps / 1e6 + 1
    assert [p.w.tag for p in sched].count("stops-on") == 1
    assert [p.w.tag for p in sched].count("gate") == 1


def test_the_command_file_is_what_tb_top_bx_scans():
    host, _, _ = fixtures.bar_808(short=True)
    for line in sh.cmd_lines(host.schedule(BENCH)):
        parts = line.split()
        assert len(parts) == 5
        wait, flag, sec, addr, data = (int(x) for x in parts)
        assert wait >= 0 and flag in (0, 1) and sec in (0, 1)
        assert 0 <= addr <= 0xFF and 0 <= data <= 0xFFFFFFFF


# ---- the timed coefficient sequences of contract 15.7.1 ----------------------
def test_the_bd_attack_window_is_four_writes_192_frames_wide():
    host = sh.MusicHost()
    host.load(0)
    host.hits([(5000, dx.BD, 1.0)])
    hot = [w for w in host.w if w.tag == "bd-attack-hot"]
    rest = [w for w in host.w if w.tag == "bd-attack-restore"]
    assert len(hot) == 2 and len(rest) == 2
    assert {w.frame for w in hot} == {5000}
    assert {w.frame for w in rest} == {5000 + int(round(dx.BD_ATTACK_MS * 1e-3 * sh.SR))}
    base = dx.A_MODE + dx.M_BD * dx.MODE_STRIDE
    assert sorted(w.addr for w in hot) == [base, base + 1]


SEQ_TAGS = ("bd-attack-hot", "bd-attack-restore", "tom-bend")


def _seq_moves(host, link):
    """Every coefficient-sequence write whose laid-out frame differs from the
    frame the host asked for. `nominal` is set by spread() from the request."""
    laid = sh.lay_out(host.w, link)
    seq = [w for w in laid if w.tag in SEQ_TAGS]
    return seq, [w for w in seq if w.frame != w.nominal]


def _max_pull(moved, tag):
    return max([w.nominal - w.frame for w in moved if w.tag == tag], default=0)


@pytest.mark.parametrize("link", LINKS)
def test_the_reference_fixture_moves_sequence_steps_only_by_a_few_frames(link):
    """MEASURED, and not what the issue first assumed: steps DO move in
    bar_808, but only earlier and only by a handful of frames, because the
    stop-bit anchors need the frames just before them. The hot write moves
    most (it shares the strike's frame, so it backs up in front of the stop
    bit: the intended setup-before-strike order). A restore is pulled 2
    frames of its 192 (42 us of 4 ms), a tom step 4 of 480. Pin those bounds
    so growth is noticed."""
    host, _, _ = fixtures.bar_808(short=True)
    seq, moved = _seq_moves(host, link)
    assert {w.tag for w in seq} == set(SEQ_TAGS), "fixture lost a sequence"
    assert all(w.frame < w.nominal for w in moved), "spread() only moves earlier"
    assert _max_pull(moved, "bd-attack-hot") <= 8
    assert _max_pull(moved, "bd-attack-restore") <= 2
    assert _max_pull(moved, "tom-bend") <= 4


def test_the_sequence_writes_are_not_anchors():
    host = sh.MusicHost(); host.load(0)
    host.hits([(5000, dx.BD, 1.0), (9000, dx.LT, 1.0)])
    assert [w for w in host.w if w.tag in SEQ_TAGS and w.anchor] == []


@pytest.mark.parametrize("link", LINKS)
def test_a_dense_pattern_moves_a_sequence_step_earlier(link):
    """The adversarial case: a second hit lands inside the BD's 192-frame
    window, so its stop bit (an anchor) needs frames that the restore holds.
    The restore moves (here later, stretching the window), and check() says
    nothing. Documented behaviour, not a goal."""
    host = sh.MusicHost(); host.load(0)
    host.hits([(5000, dx.BD, 1.0)] + [(5150 + 4 * k, dx.SD, 1.0) for k in range(20)])
    seq, moved = _seq_moves(host, link)
    restore = [w for w in moved if w.tag == "bd-attack-restore"]
    # worse than anything the reference fixture does to a restore (2 frames
    # earlier). Here the burst of anchors around the restore's frame is dense
    # enough that the forward pass (feasible()) pushes it LATER instead, so
    # the 4 ms window is stretched: a coefficient step is not pinned in either
    # direction. Observed: +20 frames (5192 -> 5212), 192 -> 212 wide.
    assert restore and max(abs(w.frame - w.nominal) for w in restore) > 2
    assert sh.check(host.schedule(link))["conflicts"] == []


def test_the_attack_window_restores_the_IMAGE_not_the_kit_preset():
    """The DECAY knob moves the BD's Q. If the window's restore recomputed the
    preset it would silently undo the knob -- the exact fault drums_fx's own
    record had to be fixed for. The host keeps the image, so it cannot."""
    host = sh.MusicHost()
    host.load(0)
    host.knob(1000, "decay", 9.0)
    host.hits([(2000, dx.BD, 1.0)])
    knob = [w for w in host.w if w.tag == "knob-decay"]
    rest = [w for w in host.w if w.tag == "bd-attack-restore"]
    assert len(knob) == 2 and len(rest) == 2
    assert sorted((w.addr, w.data) for w in knob) == sorted((w.addr, w.data) for w in rest)
    kit = dict(dx.kit_808())
    assert any(w.data != kit[w.addr] for w in rest), "the knob did not move the pole"


def test_the_tom_pitch_drop_is_fourteen_writes_over_sixty_ms():
    host = sh.MusicHost()
    host.load(0)
    host.hits([(5000, dx.LT, 1.0)])
    bend = [w for w in host.w if w.tag == "tom-bend"]
    assert len(bend) == 2 * (dx.TOM_DROP_STEPS + 1)
    span = max(w.frame for w in bend) - min(w.frame for w in bend)
    assert span == int(round(dx.TOM_DROP_MS * 1e-3 * sh.SR))
    assert span * 1e3 / sh.SR == pytest.approx(dx.TOM_DROP_MS, abs=0.05)


def test_the_tom_pitch_drop_is_scaled_by_the_accent():
    """Reference 4: "accent changes the pitch envelope". Two identical hits at
    different accents must not produce the same coefficient sequence."""
    a, b = sh.MusicHost(), sh.MusicHost()
    a.load(0); a.hits([(5000, dx.LT, 1.0)])
    b.load(0); b.hits([(5000, dx.LT, 0.3)])
    wa = [(w.frame, w.addr, w.data) for w in a.w if w.tag == "tom-bend"]
    wb = [(w.frame, w.addr, w.data) for w in b.w if w.tag == "tom-bend"]
    assert len(wa) == len(wb) and wa != wb


def test_the_tom_drop_sweeps_from_wherever_the_image_sits_not_from_ninety_hz():
    """The congas are the same circuit at another f0 (reference 4, SW8). A host
    that hard-coded the tom's tuning would sweep a retuned drum from the wrong
    place."""
    host = sh.MusicHost()
    host.load(0)
    for addr, v in dx.mode_writes(dx.M_LT, 140.0, 25.0, 0.25):
        host.drum(1000, addr, v, tag="retune")
    host.hits([(5000, dx.LT, 1.0)])
    bend = sorted((w.frame, w.addr, w.data) for w in host.w if w.tag == "tom-bend")
    plain = sh.MusicHost(); plain.load(0); plain.hits([(5000, dx.LT, 1.0)])
    assert bend != sorted((w.frame, w.addr, w.data) for w in plain.w if w.tag == "tom-bend")
    # and it is EXACTLY the sequence drums_fx would emit from the image's own
    # pole -- the host reads the registers back, it does not remember a kit
    base = dx.A_MODE + dx.M_LT * dx.MODE_STRIDE
    img = dict((w.addr, w.data) for w in host.w if w.tag == "retune")
    f0, q = dx.poles_from_regs(img[base], img[base + 1])
    want = dx.tom_pitch_drop_writes(5000, dx.M_LT, f0, q, img[base + 2] / 65536.0, 1.0)
    assert bend == sorted((f, a, v) for f, a, v in want)


def test_a_host_without_the_sequences_sends_strictly_fewer_writes():
    """This is what a trigger bridge is: the same kit, the same strikes, and
    54 transactions that never happen."""
    a, _, cov_a = fixtures.bar_808(short=True)
    b = sh.MusicHost(coef_seq=False)
    b.load(0)
    b.hits([(600, dx.BD, 1.4), (1400, dx.LT, 1.0)])
    assert cov_a["bd_hot"] and cov_a["tom_bend"]
    assert not [w for w in b.w if w.tag.startswith(("bd-attack", "tom-bend"))]


# ---- the knobs ---------------------------------------------------------------
@pytest.mark.parametrize("name,n", sorted(sh.knob_cost().items()))
def test_each_knob_costs_the_writes_the_budget_says_it_does(name, n):
    host = sh.MusicHost()
    before = len(host.w)
    host.knob(100, name, {"cutoff": 900.0, "resonance": 0.9, "decay": 7.0,
                          "volume": 0.5}[name])
    assert len(host.w) - before == n


def test_the_cutoff_knob_actually_changes_the_register():
    host = sh.MusicHost()
    host.knob(100, "cutoff", 900)
    host.knob(200, "cutoff", 300)
    w = [x for x in host.w if x.tag == "knob-cutoff"]
    assert [x.data for x in w] == [900, 300]


def test_the_resonance_knob_writes_the_compensation_pair_as_well_as_k():
    host = sh.MusicHost()
    host.knob(100, "resonance", 0.95)
    w = [x for x in host.w if x.tag == "knob-res"]
    assert sorted(x.addr for x in w) == sorted([0x1C, 0x1D, 0x1E])      # K, GAIN, OGAIN


# ---- the fixtures ------------------------------------------------------------
@pytest.mark.parametrize("name", sorted(fixtures.FIXTURES))
def test_every_fixture_schedules_without_a_late_musical_instant(name):
    host, n, cover = fixtures.FIXTURES[name]()
    for link in LINKS:
        st = sh.check(host.schedule(link))
        assert st["conflicts"] == [], f"{name} on {link.name}"
        assert st["anchor_jitter_us"] < 400.0
    assert cover["bd_hot"] >= 2 and cover["tom_bend"] >= 14
    assert n > max(w.frame for w in host.w)


def test_demo_mode_needs_no_keyboard_and_still_plays_notes_and_knobs():
    host, n, cover = fixtures.demo(bars=1)
    assert cover["gates"] >= 8 and cover["knobs"] >= 8 and cover["strikes"] >= 8


# ---- live delivery (#142) --------------------------------------------------
def test_same_timestamped_events_are_independent_of_submission_order():
    """The API call order is not part of a musical event's meaning.

    This is the audit's red control: the old offline host interleaves the BD
    attack and tom bend according to the order in which ``hits`` was called.
    Their coefficient writes then land in different frames.
    """
    a = sh.MusicHost().load(0)
    a.hits([(1000, dx.BD, 1.0), (1000, dx.LT, .7), (1200, dx.CP, 1.0)])
    b = sh.MusicHost().load(0)
    b.hits([(1000, dx.LT, .7), (1000, dx.BD, 1.0), (1200, dx.CP, 1.0)])
    assert sh.model_writes(a.schedule(BENCH)) == sh.model_writes(b.schedule(BENCH))


def test_live_queue_is_causal_and_reports_bounded_latency():
    live = sh.LiveMusicHost().load(0)
    live.submit(1000, "hit", (dx.BD, 1.0))
    live.submit(1000, "key", ("on", 45))
    live.submit(1000, "hit", (dx.LT, .8))
    placed = live.schedule(BENCH)
    report = sh.check(placed)
    assert report["conflicts"] == []
    assert all(p.land >= p.w.frame for p in placed)
    latency = live.latency(placed)
    assert latency["max_frames"] <= live.MAX_LIVE_LATENCY_FRAMES
    assert latency["max_us"] == pytest.approx(latency["max_frames"] * 1e6 / 48000)


def test_live_queue_preserves_repeated_and_simultaneous_events():
    live = sh.LiveMusicHost().load(0)
    for f, op in ((1000, "on"), (1050, "off"),
                  (1100, "on"), (1150, "off")):
        live.submit(f, "key", (op, 45))
    live.submit(1000, "hit", (dx.BD, 1.0))
    live.submit(1000, "hit", (dx.SD, 1.0))
    placed = live.schedule(BENCH)
    stops = [p for p in placed if p.w.tag == "stops-on"]
    assert len(stops) == 1 and stops[0].w.data == (1 << dx.BD) | (1 << dx.SD)
    assert len([p for p in placed if p.w.tag == "gate"]) == 4
    assert all(b.start_ps >= a.start_ps + BENCH.tx_period_ps
               for a, b in zip(placed, placed[1:]))


def test_live_queue_can_accept_more_events_after_a_drain():
    live = sh.LiveMusicHost().load(0)
    live.submit(1000, "key", ("on", 45))
    first = live.schedule(BENCH)
    live.submit(1200, "key", ("off", 45))
    second = live.schedule(BENCH)
    assert len(second) > len(first)
    assert len([p for p in second if p.w.tag == "gate"]) == 2

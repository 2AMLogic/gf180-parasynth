"""The pads demo's ROM gate and timing contract (#449), without a simulator.

The anchors here are external to the pads code: model/drums_fx.hit_writes (what
a hit IS), fpga/release/r1-kit.json and uart_host.R1_KIT_SHA256 (what the kit
IS), uart_host's packet builders (how long a packet IS)."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for _p in ("fpga", "model", "rtl-sketch"):
    sys.path.insert(0, str(ROOT / _p))

import drums_fx as dx          # noqa: E402
import pads_rom as pr          # noqa: E402
import uart_host as uh         # noqa: E402


def test_committed_rom_is_bound():
    assert pr.ROM_V.read_text() == pr.render(), "run python3 fpga/pads_rom.py --write"
    rom = pr.check_rom(pr.ROM_V)
    assert len(rom["boot"]) == 2 + len(uh.r1_kit()) + 2


def test_rom_kit_hashes_to_r1_digest_independently():
    rom = pr.parse_rom(pr.ROM_V.read_text())
    kit = [(a, d) for _f, s, a, d in rom["boot"][2:-2]]
    assert all(s == 1 for _f, s, _a, _d in rom["boot"][2:-2])
    assert dx._kit_sha256(kit) == uh.R1_KIT_SHA256
    assert dx._kit_sha256(kit) == json.loads(pr.R1_KIT.read_text())["sha256"]


@pytest.mark.parametrize("how", ["value", "drop", "swap"])
def test_tampered_rom_is_refused(tmp_path, how):
    text = pr.ROM_V.read_text()
    lines = text.splitlines(keepends=True)
    k = next(i for i, ln in enumerate(lines) if "8'd10: word" in ln)
    if how == "value":
        j = lines[k].index("};") - 1           # the data word's last hex digit
        lines[k] = lines[k][:j] + ("1" if lines[k][j] != "1" else "2") + lines[k][j + 1:]
    elif how == "drop":
        del lines[k]
    else:                                     # two kit writes exchanged in ORDER
        a0, w0 = lines[k].split("word =")
        a1, w1 = lines[k + 1].split("word =")
        lines[k], lines[k + 1] = a0 + "word =" + w1, a1 + "word =" + w0
    bad = tmp_path / "pads_rom.v"
    bad.write_text("".join(lines))
    assert bad.read_text() != text
    with pytest.raises(pr.Refused):
        pr.check_rom(bad)


def test_programs_are_hit_writes_verbatim():
    kit = uh.r1_kit()
    for v, name in enumerate(pr.VOICES):
        w = dx.hit_writes([(0, dx.SOUND_STOP[name], pr.ACCENT)], kit=kit)[len(kit):]
        prog = pr.programs(kit)[v]
        assert [(f, a) for f, a, _ in w] == [(e[0], e[4]) for e in prog]
        for (f, a, d), e in zip(w, prog):
            if a == dx.A_STOPS:
                assert e[1] == (pr.KIND_RISE if d else pr.KIND_DROP)
            else:
                assert e[5] == d


@pytest.mark.parametrize("name", ["SD", "CH", "CP"])
def test_isolated_press_is_hit_writes_at_t0_plus_lat(name):
    v = pr.VOICES.index(name)
    t0 = 1234
    got = pr.expected_hits([(t0, v)])
    ref = dx.hit_writes([(t0 + pr.LAT, dx.SOUND_STOP[name], pr.ACCENT)], kit=[])
    assert got == [(f, 0, 1, a, d) for f, a, d in ref]


def test_isolated_bd_spills_its_trigger_one_frame():
    """Four writes in BD's first frame, two write slots: the accent and the
    trigger land one frame later, the drop one frame after that. The only
    place an isolated hit departs from hit_writes' frames."""
    t0 = 500
    got = pr.expected_hits([(t0, 0)])
    ref = dx.hit_writes([(t0 + pr.LAT, dx.BD, pr.ACCENT)], kit=uh.r1_kit(), coef_seq=True)
    ref = ref[len(uh.r1_kit()):]
    assert [(a, d) for _f, _fl, _s, a, d in got] == [(a, d) for _f, a, d in ref]
    d0 = t0 + pr.LAT
    assert [f for f, *_ in got] == [d0, d0, d0 + 1, d0 + 1, d0 + 2, d0 + 192, d0 + 192]


def test_lat_covers_the_worst_case_and_packet_sizes_are_the_hosts():
    assert len(uh.pkt_event(0, 0, 1, 0, 0)) == pr.EVENT_BYTES
    assert len(uh.pkt_write(0, 1, 0, 0)) == pr.WRITE_BYTES
    assert pr.BYTE_CYCLES == uh.byte_cycles(pr.BAUD)
    assert pr.lat_required() <= pr.LAT
    # 16 outstanding writes is the whole of every program
    assert sum(len(p) for p in pr.programs()) == 16


def test_chord_keeps_every_trigger_edge():
    """All four in one frame: the stops writes carry the shadow, so each stop's
    bit is 0 in some frame and 1 in the next -- the edge drum_regs needs."""
    hits = pr.expected_hits([(100, v) for v in range(4)])
    stops = [(f, d) for f, _fl, _s, a, d in hits if a == dx.A_STOPS]
    last = {}
    for f, d in stops:
        last[f] = d                           # the last write of a frame is what it plays
    reg, val = {}, 0
    for f in range(min(last) - 1, max(last) + 2):
        val = last.get(f, val)
        reg[f] = val
    for name in pr.VOICES:
        bit = 1 << dx.SOUND_STOP[name]
        edges = [f for f in reg if f - 1 in reg and reg[f] & bit and not reg[f - 1] & bit]
        assert len(edges) == 1, (name, stops)
    # and no frame ever carries more than two writes
    per = {}
    for f, *_ in hits:
        per[f] = per.get(f, 0) + 1
    assert max(per.values()) <= uh.WRITE_SLOTS


def test_dues_never_decrease():
    presses = [(10, 0), (10, 3), (11, 1), (40, 2), (300, 1)]
    dues = [w[0] for w in pr.schedule(presses)["writes"]]
    assert dues == sorted(dues)


def test_busy_voice_drops_a_press_until_its_last_due_has_passed():
    last = pr.programs()[1][-1][0]
    sch = pr.schedule([(0, 1), (last + pr.LAT, 1), (last + pr.LAT + 1, 1)])
    assert sch["dropped"] == [(last + pr.LAT, 1)]
    assert sch["accepted"] == [(0, 1), (last + pr.LAT + 1, 1)]


def test_trig_stuck_contract_never_clears_the_bit():
    hits = pr.expected_hits([(0, 1), (2000, 1)], trig_stuck=True)
    assert all(d != 0 for _f, _fl, _s, a, d in hits if a == dx.A_STOPS)


def test_debounce_contract():
    clean = [(1000, 1), (500_000, 0)]
    assert pr.press_cycles(clean) == [1000 + pr.PRESS_LAT_CYC]
    bounce = [(1000, 1), (5000, 0), (10_000, 1), (17_000, 0), (26_000, 1),
              (500_000, 0), (504_000, 1), (509_000, 0), (516_000, 1), (525_000, 0)]
    assert pr.press_cycles(bounce) == [1000 + pr.PRESS_LAT_CYC]
    # without the lockout every rising edge is a press, the release's included
    assert len(pr.press_cycles(bounce, lockout=False)) == 5
    # a change still pending when the lockout ends is taken at the expiry
    late = [(1000, 1), (1000 + 100, 0)]
    assert pr.press_cycles(late) == [1000 + pr.PRESS_LAT_CYC]


def test_cycle_frame_boundary():
    assert pr.cycle_frame(255) == 0 and pr.cycle_frame(256) == 1


def test_apply_frames_spill():
    assert pr.apply_frames([5, 5, 5, 5, 6, 9]) == [5, 5, 6, 6, 7, 9]

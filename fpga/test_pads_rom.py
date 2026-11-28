"""The pads demo's ROM gate and timing contract (#449), without a simulator.

The anchors here are external to the pads code: model/drums_fx.hit_writes (what
a hit IS), fpga/release/r1-kit.json and uart_host.R1_KIT_SHA256 (what the kit
IS), uart_host's packet builders (how long a packet IS)."""
import json
import os
import shutil
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


# ---------------------------------------------------------------------------
# the output path (#2096, a slice of the #2095 census from #2040)
#
# THE DEFECT, red-first on main @ 8eb04e5b (2026-11-28): `--write --rom
# NEWDIR/pads_rom.v` with NEWDIR absent derived the ROM and then died at
# `a.rom.write_text(text)` on a FileNotFoundError -- a traceback and exit 1,
# which is STALE's code, for a derivation that had succeeded.
#
# THE CONTRACT, the #2040 pattern: the destination is checked BEFORE the ROM is
# derived (a directory given as the file, or a parent that cannot be made, is
# REFUSED, exit 2, with nothing derived); a missing parent is created; and an
# OSError at the write itself is REFUSED (2), never a traceback.
#
# WHAT RUNS FOR REAL: `main`, `render`, `check_rom` and the comparison with a
# fresh derivation. `render` is doubled only where the test must prove it never
# ran, and once to remove the destination between the pre-flight and the write.
# ---------------------------------------------------------------------------
def test_write_under_a_missing_directory_creates_it_and_binds(tmp_path, capsys):
    rom = tmp_path / "new" / "dir" / "pads_rom.v"
    assert pr.main(["--write", "--rom", str(rom)]) == 0
    assert rom.read_text() == pr.render()
    assert "pads_rom: BOUND --" in capsys.readouterr().out


def test_write_with_a_bare_filename_writes_into_the_cwd(tmp_path, monkeypatch):
    """Green before and after the fix: it pins the empty-parent case
    (`Path("x.v").parent` is the cwd) that the pre-flight must not break."""
    monkeypatch.chdir(tmp_path)
    assert pr.main(["--write", "--rom", "pads_rom.v"]) == 0
    assert (tmp_path / "pads_rom.v").read_text() == pr.render()


def _never_derive(monkeypatch):
    def render():
        raise AssertionError("the ROM was derived although the destination was refused")
    monkeypatch.setattr(pr, "render", render)


READ_ONLY = pytest.param("read-only", marks=pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root writes into a read-only directory, so nothing refuses"))


def _lose_the_directory(parent, how):
    """Between the pre-flight and the write: the directory is removed, or made
    read-only -- the input that passes `mkdir(exist_ok=True)` (rule 8)."""
    if how == "removed":
        shutil.rmtree(parent, ignore_errors=True)
    else:
        parent.mkdir(parents=True, exist_ok=True)
        parent.chmod(0o500)


def _an_unwritable_destination(tmp_path, how):
    if how == "directory":
        return tmp_path
    if how == "file-in-the-way":
        (tmp_path / "file").write_text("not a directory")
        return tmp_path / "file" / "pads_rom.v"
    (tmp_path / "read-only").mkdir()
    (tmp_path / "read-only").chmod(0o500)
    return tmp_path / "read-only" / "new" / "pads_rom.v"


@pytest.mark.parametrize("how", ["directory", "file-in-the-way", READ_ONLY],
                         ids=["directory-given-as-the-file", "parent-uncreatable", "parent-read-only"])
def test_an_unwritable_rom_is_refused_before_the_rom_is_derived(
        tmp_path, monkeypatch, capsys, how):
    rom = _an_unwritable_destination(tmp_path, how)
    try:
        _never_derive(monkeypatch)
        assert pr.main(["--write", "--rom", str(rom)]) == 2
    finally:
        if how == "read-only":
            (tmp_path / "read-only").chmod(0o700)
    assert "pads_rom: REFUSED --" in capsys.readouterr().out


@pytest.mark.parametrize("how", ["removed", READ_ONLY], ids=["directory-removed", "parent-read-only"])
def test_a_write_failure_after_the_derivation_is_refused_not_a_traceback(
        tmp_path, monkeypatch, capsys, how):
    """The destination is lost between the pre-flight and the write: the real
    `write_text` then raises OSError, which must be REFUSED (2)."""
    rom = tmp_path / "dir" / "pads_rom.v"
    real = pr.render

    def derive_then_lose_the_directory():
        text = real()
        _lose_the_directory(rom.parent, how)
        return text

    monkeypatch.setattr(pr, "render", derive_then_lose_the_directory)
    try:
        assert pr.main(["--write", "--rom", str(rom)]) == 2
        out = capsys.readouterr().out
    finally:
        if how == "read-only":
            rom.parent.chmod(0o700)
    assert "pads_rom: REFUSED --" in out and str(rom) in out, out

"""The live MIDI session sends the kit the IMAGE on the board plays (#273, Judge 2).

`fpga/midi_session.py` is a second host, separate from `uart_host.py run`, and it
built `spi_host.MusicHost` with no kit, so it fell back to `drums_fx.kit_808()`:
after #273 that is revision 14's clap (ENV_CTL[8] burst 3, the new ENV_FRATE[8],
ENV_RATE[9] 80 ms). The published R0 Arty image is revision 11 and does not
decode ENV_FRATE, so a live session on a real board played an unverified clap
and nothing refused or reported it.

The mechanism mirrors `uart_host --image` (fpga/test_image_kit.py):
  * `MidiSession(image=...)`, default `release` (the frozen revision-11 kit);
  * the CLI's `--image`: a serial port defaults to `release`; `--port sim` is the
    tree's device contract and implies `tree`; `--port sim --image release` is
    REFUSED rather than letting a revision-11 kit pass against a revision-14
    simulator;
  * the verification harness drives the tree (sim and arty_a7_top RTL), so it
    names `tree` explicitly for both the session and its oracle.

Every claim carries a control that must turn it red: the tree image's known
state DOES carry ENV_FRATE[8] (the check discriminates), and the frozen kit
REFUSES when the tree's kit moves a non-clap write.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
for _p in ("fpga", "fpga/release", "model", "audition", "spec/reference"):
    if str(ROOT / _p) not in sys.path:
        sys.path.insert(0, str(ROOT / _p))

import drums_fx as dx                 # noqa: E402
import midi_session as ms             # noqa: E402
import uart_device_sim as dev         # noqa: E402
import uart_host as uh                # noqa: E402

BURST = dx.A_ENV + dx.E_CPBURST * dx.ENV_STRIDE          # ENV_CTL[8]
FRATE = BURST + 3                                       # ENV_FRATE[8], revision 14 only
TAIL_RATE = dx.A_ENV + dx.E_CPTAIL * dx.ENV_STRIDE + 2    # ENV_RATE[9]


def _session(**kw):
    clock = dev.SimClock()
    sim = dev.UartDeviceSim(clock=clock)
    return ms.MidiSession(dev.SimSerial(sim), clock=clock, **kw)


def _drums(writes) -> dict:
    """SEC 1 (drum) register writes of a known-state image, addr -> data."""
    return {addr: data for _flag, sec, addr, data in writes if sec == 1}


def test_default_session_sends_the_release_kit():
    """RED before the fix: the session sent kit_808() (revision 14)."""
    s = _session()
    assert s.image == uh.DEFAULT_IMAGE == "release"
    assert s.mh.kit == dx.kit_808_rev11()
    drums = _drums(s.init_writes())
    assert FRATE not in drums
    assert drums[BURST] == dx.env_ctl(dx.CP, 15, 0, 2, 480)
    assert drums[TAIL_RATE] == dx.rate_reg(47e-3)


def test_control_tree_session_sends_revision_14():
    """The check discriminates: the tree image's known state has the write the
    release image cannot decode, and its values are the revision-14 kit's
    (`tree` is the newest BUILT image, R1; see uart_host.IMAGE_REVISION)."""
    s = _session(image="tree")
    assert s.mh.kit == dx.kit_808_rev14()
    drums = _drums(s.init_writes())
    assert FRATE in drums
    tree = dict(dx.kit_808_rev14())
    assert drums[BURST] == tree[BURST] and drums[TAIL_RATE] == tree[TAIL_RATE]
    # one more kit write (ENV_FRATE[8]) and R1's known-state preamble (#279)
    assert len(s.init_writes()) == len(_session().init_writes()) + 1 + len(uh.known_state_preamble())
    assert s.init_writes()[:2] == list(uh.known_state_preamble())


def test_control_release_session_refuses_a_drifted_kit(monkeypatch):
    """A later change to a non-clap kit write must not silently reach the
    published image through the live session either."""
    live = dx.kit_808()
    a, v = live[0]
    monkeypatch.setattr(dx, "kit_808", lambda: [(a, v ^ 1)] + live[1:])
    with pytest.raises(dx.KitRefused):
        _session()


def test_unknown_image_refuses():
    with pytest.raises(ValueError):
        _session(image="r13")


def test_cli_image_resolution():
    assert ms.resolve_image("/dev/ttyUSB1", None) == "release"
    assert ms.resolve_image("/dev/ttyUSB1", "tree") == "tree"
    assert ms.resolve_image("sim", None) == "tree"
    assert ms.resolve_image("sim", "tree") == "tree"
    with pytest.raises(uh.Refused):
        ms.resolve_image("sim", "release")


def test_cli_refuses_sim_with_the_release_image(capsys):
    """REFUSED before any port or MIDI input is opened."""
    rc = ms.main(["--port", "sim", "--midi-in", "/nonexistent", "--image", "release"])
    assert rc == 2
    assert "REFUSED" in capsys.readouterr().err


def test_harness_drives_the_tree_image():
    """verify_live_midi's sim and RTL replay are revision 14: the session
    sends the named R1 release (`r1`, #323) and the oracle holds the frozen R1
    target, so the oracle's known state has ENV_FRATE[8] and matches the
    session's write for write."""
    import verify_live_midi as vlm
    assert vlm.HARNESS_IMAGE == "r1"
    oracle = vlm.Oracle(lambda t: 0)
    assert FRATE in _drums(oracle.static)
    assert oracle.static == _session(image=vlm.HARNESS_IMAGE).init_writes()


def test_doc_and_start_command_log_state_the_measured_counts():
    """docs/live-midi.md and the committed start-command evidence carry the
    known-state sizes the code produces, per image: a kit change that moves
    them turns this red instead of leaving a stale count in the doc."""
    n_tree = len(_session(image="tree").init_writes())
    n_rel = len(_session(image="release").init_writes())
    doc = " ".join((ROOT / "docs/live-midi.md").read_text().split())
    assert (f"That is {n_tree} writes on the tree image (revision 14) and {n_rel} "
            f"on the release image") in doc
    assert f"{n_tree} init writes acknowledged (kit for image tree" in doc
    assert f"static image 0/{n_tree}" in doc
    log = (ROOT / "fpga/reports/live-midi/start-command.log").read_text()
    assert f"known state sent ({n_tree} writes, acknowledged; kit for image tree" in log
    assert f"{n_tree} live;" in log

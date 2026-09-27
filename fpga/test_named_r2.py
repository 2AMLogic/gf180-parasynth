"""fpga/test_named_r2.py -- the named R2 selection `--image r2` (fpga/release/R2.md).

R2 is R1's contract revision with PULSE2X=1 and four RTL changes; it has no
kit or preset change. So `--image r2` sends R1's frozen kit and R1's
known-state start, and emits exactly R1's bytes for every supported command.
What differs is the IMAGE: PULSE2X is admitted on r2 only, and the RTL
replays of r2's bytes run in the PULSE2X=1 configuration.
"""
import contextlib
import io
import os
import sys
import tempfile
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
for p in (HERE, HERE / "release", HERE.parent / "model", HERE.parent / "audition"):
    sys.path.insert(0, str(p))

import drums_fx as dx                                         # noqa: E402
import midi_session as ms                                     # noqa: E402
import qualified_domain as qd                                 # noqa: E402
import r1_candidate as r1c                                    # noqa: E402
import uart_host as uh                                        # noqa: E402
import verify_uart_bridge as vub                              # noqa: E402
import voice_fx as vf                                         # noqa: E402


def _bytes(argv):
    with tempfile.TemporaryDirectory() as d:
        prefix = os.path.join(d, "cap")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            assert uh.main(["--dry-run", *argv, "--capture", prefix]) == 0
        return Path(prefix + ".cmds").read_bytes()


def test_r2_is_named_and_the_defaults_do_not_move():
    assert uh.DEFAULT_IMAGE == "release"
    assert uh.IMAGE_REVISION["r2"] == 14
    assert "r2" in uh.KNOWN_STATE_IMAGES
    assert ms.resolve_image("sim", "r2") == "r2"
    assert ms.resolve_image("/dev/ttyUSB1", "r2") == "r2"


def test_r2_sends_r1s_frozen_kit():
    assert uh.image_kit("r2") == uh.image_kit("r1")
    assert dx._kit_sha256(uh.image_kit("r2")) == r1c.KIT_R14_SHA256


@pytest.mark.parametrize("name,argv", r1c.COMMANDS)
def test_every_supported_command_emits_r1s_bytes_under_r2(name, argv):
    assert _bytes([*argv, "--image", "r2"]) == _bytes([*argv, "--image", "r1"])


def test_control_r2_bytes_differ_from_r0s():
    """The byte check can fail: the R0 sender's demo is not R2's."""
    assert _bytes(["run", "--fixture", "demo", "--image", "r2"]) != \
        _bytes(["run", "--fixture", "demo", "--image", "release"])


def test_r2_starts_from_the_known_state():
    setup, kit = r1c.emitted_setup(["run", "--fixture", "demo", "--image", "r2"])
    assert kit and r1c.check_init(setup, kit_expected=True) == []


def test_pulse2x_is_admitted_on_r2_and_refused_elsewhere():
    regs = vf.VoiceFx.patch_regs()
    qd.check_patch(regs, pulse2x=True, image="r2")
    for other in ("release", "tree", "r1"):
        with pytest.raises(qd.Rejected):
            qd.check_patch(regs, pulse2x=True, image=other)


def test_the_replay_configuration_follows_the_image():
    assert vub.CONFIG["PULSE2X"] == 0
    with vub.image_config("r2"):
        assert vub.CONFIG["PULSE2X"] == 1
        assert "VOICE_PULSE_2X" in vub.config_defines()
    assert vub.CONFIG["PULSE2X"] == 0
    for other in ("release", "tree", "r1"):
        with vub.image_config(other):
            assert "VOICE_PULSE_2X" not in vub.config_defines()


def test_midi_session_r2_known_state_equals_r1s():
    class _Nul:
        timeout = 0
        def write(self, b): return len(b)
        def read(self, n=1): return b""
        def flush(self): pass
    assert ms.MidiSession(_Nul(), image="r2").init_writes() == \
        ms.MidiSession(_Nul(), image="r1").init_writes()


def test_r2_gets_r1s_frozen_sound_positions_for_the_five_pairs():
    """#298: the alternates are exposed on r2, from R1's frozen table (R2's drum
    RTL is R1's), never the tree's drums_fx positions."""
    assert "r2" in uh.ALTERNATE_IMAGES
    assert uh.image_sound_presets("r2") == uh.image_sound_presets("r1")
    assert uh.image_sound_presets("r2")


def test_midi_session_r2_plays_the_alternates_like_r1():
    class _Nul:
        timeout = 0
        def write(self, b): return len(b)
        def read(self, n=1): return b""
        def flush(self): pass
    assert ms.MidiSession(_Nul(), image="r2").presets == \
        ms.MidiSession(_Nul(), image="r1").presets

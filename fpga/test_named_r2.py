"""fpga/test_named_r2.py -- the named R2 selection `--image r2` (fpga/release/R2.md).

R2 is R1's contract revision with PULSE2X=1 and four RTL changes; it has no
kit or preset change. So `--image r2` sends R1's frozen kit and R1's
known-state start, and emits exactly R1's bytes for every supported command.
What differs is the IMAGE: PULSE2X is admitted on r2 only, and the RTL
replays of r2's bytes run in the PULSE2X=1 configuration.
"""
import contextlib
import io
import json
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
import verify_live_midi as vlm                                 # noqa: E402
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


def test_the_live_midi_rtl_replay_of_r2_runs_pulse2x(monkeypatch, tmp_path):
    """This file's contract, end to end through the shipped harness: the RTL
    replays of r2's bytes run PULSE2X=1. The simulator is stubbed (it is not
    the subject); the stubs RECORD the bench defines and the model CONFIG
    they run under, so a replay stage that skips vub.image_config -- the
    duplicated copy #1219 removed -- answers PULSE2X=0 and fails here."""
    seen = {"sim": [], "analyze": [], "trunc": []}

    def fake_simulate_replay(prefix, outdir, **kw):
        seen["sim"].append({"outdir": Path(outdir), "defines": vub.config_defines()})
        Path(outdir).mkdir(parents=True, exist_ok=True)
        (Path(outdir) / "run_identity.json").write_text("{}\n")
        return {"outdir": str(outdir), "reused": False, "reuse": {}, "identity": {}}

    def fake_analyze(rr):
        seen["analyze"].append(dict(vub.CONFIG))
        return True, {"i2s_incomplete": False}, []

    def fake_truncation_control(rr):
        seen["trunc"].append(dict(vub.CONFIG))
        return {"caught": True}

    monkeypatch.setattr(vub, "simulate_replay", fake_simulate_replay)
    monkeypatch.setattr(vub, "analyze", fake_analyze)
    monkeypatch.setattr(vub, "truncation_control", fake_truncation_control)
    # main() reassigns the module's HARNESS_IMAGE; restore it afterwards
    monkeypatch.setattr(vlm, "HARNESS_IMAGE", vlm.HARNESS_IMAGE)

    for image in ("r1", "r2"):
        base = tmp_path / image
        with contextlib.redirect_stdout(io.StringIO()):
            assert vlm.main(["--outdir", str(base), "--image", image,
                             "--rtl", "coverage"]) == 0
        recdir = base if image == "r1" else base / image
        rec = json.loads((recdir / "verification.json").read_text())
        assert rec["image"] == image
        assert rec["rtl"]["coverage"]["state"] == "PASS"

    # the r2 replay ran PULSE2X: the bench's defines carry it, and the model
    # configuration the comparison saw during analyze was PULSE2X=1 (an
    # analyze outside vub.image_config answers 0)
    assert "VOICE_PULSE_2X" not in seen["sim"][0]["defines"]
    assert "VOICE_PULSE_2X" in seen["sim"][1]["defines"]
    assert seen["analyze"][0]["PULSE2X"] == 0
    assert seen["analyze"][1]["PULSE2X"] == 1
    assert seen["trunc"][1]["PULSE2X"] == 1
    # one scratch directory per image (a reuse can never cross images), and
    # the default r1 image keeps the historical live-midi-rtl path
    assert seen["sim"][0]["outdir"].parent.name == "live-midi-rtl"
    assert seen["sim"][1]["outdir"].parent.name == "live-midi-rtl-r2"
    assert seen["sim"][1]["outdir"] != seen["sim"][0]["outdir"]


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

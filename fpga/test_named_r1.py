"""fpga/test_named_r1.py -- the named, frozen R1 selection `--image r1` (#323).

plan092 section 2: R1 is selected by name. `release` and no --image stay R0,
and `tree` stays a development selector. `r1` sends R1's kit frozen BY VALUE,
with R1's known-state start, and today it emits exactly the bytes the R1
evidence replayed with `--image tree`.
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
import r1_candidate as r1c                                    # noqa: E402
import uart_host as uh                                        # noqa: E402


def _bytes(argv):
    with tempfile.TemporaryDirectory() as d:
        prefix = os.path.join(d, "cap")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            assert uh.main(["--dry-run", *argv, "--capture", prefix]) == 0
        return Path(prefix + ".cmds").read_bytes()


def test_the_defaults_do_not_move():
    assert uh.DEFAULT_IMAGE == "release"
    assert uh.IMAGE_REVISION == {"release": 11, "tree": 14, "r1": 14}
    assert ms.resolve_image("/dev/ttyUSB1", None) == "release"
    assert ms.resolve_image("sim", None) == "tree"
    assert ms.resolve_image("sim", "r1") == "r1"


def test_r1_sends_r1s_frozen_kit_by_value():
    kit = uh.image_kit("r1")
    assert dx._kit_sha256(kit) == r1c.KIT_R14_SHA256 == uh.R1_KIT_SHA256
    assert len(kit) == r1c.KIT_R14_WRITES


def test_r1_does_not_follow_a_later_change_to_the_trees_kit(monkeypatch):
    """Sound work may change kit_808(); `tree` follows it, `r1` does not."""
    before = uh.image_kit("r1")
    moved = [(a, v + 1 if i == 0 else v) for i, (a, v) in enumerate(dx.kit_808())]
    monkeypatch.setattr(dx, "kit_808", lambda: moved)
    monkeypatch.setitem(dx.KITS_BY_REVISION, 14, lambda: moved)
    assert uh.image_kit("tree") == moved
    assert uh.image_kit("r1") == before


def test_control_a_tampered_frozen_r1_kit_is_refused(tmp_path, monkeypatch):
    rec = json.loads(uh.R1_KIT.read_text())
    rec["writes"][0][1] += 1
    p = tmp_path / "r1-kit.json"
    p.write_text(json.dumps(rec))
    monkeypatch.setattr(uh, "R1_KIT", p)
    with pytest.raises(dx.KitRefused, match="not R1's frozen kit"):
        uh.image_kit("r1")


@pytest.mark.parametrize("name,argv", r1c.COMMANDS)
def test_every_r1_command_emits_the_bytes_r1s_evidence_replayed(name, argv):
    """Today the frozen kit IS the tree's, so r1 and tree are byte-identical --
    the R1 RTL evidence (recorded with `--image tree`) covers `--image r1`."""
    assert _bytes([*argv, "--image", "r1"]) == _bytes([*argv, "--image", "tree"])


def test_r1_starts_from_the_known_state_and_passes_the_frozen_target():
    setup, kit = r1c.emitted_setup(["run", "--fixture", "demo", "--image", "r1"])
    assert kit and r1c.check_init(setup, kit_expected=True) == []


def test_control_wrong_kit_under_r1_fails_at_the_init_bytes(monkeypatch):
    monkeypatch.setattr(uh, "INJECT_BUGS", set(uh.INJECT_BUGS) | {"WRONG_KIT"})
    setup, kit = r1c.emitted_setup(["run", "--fixture", "demo", "--image", "r1"])
    probs = r1c.check_init(setup, kit_expected=True)
    assert probs and any("kit" in p or "ENV_FRATE" in p for p in probs), probs


def test_control_an_r0_sender_fails_the_r1_target():
    setup, kit = r1c.emitted_setup(["run", "--fixture", "demo", "--image", "release"])
    assert r1c.check_init(setup, kit_expected=True)


def test_midi_session_r1_known_state_is_the_frozen_target():
    class _Nul:
        timeout = 0
        def write(self, b): return len(b)
        def read(self, n=1): return b""
        def flush(self): pass
    s_r1 = ms.MidiSession(_Nul(), image="r1").init_writes()
    s_tree = ms.MidiSession(_Nul(), image="tree").init_writes()
    assert s_r1 == s_tree and r1c.check_init(s_r1, kit_expected=True) == []

"""fpga/test_named_r1.py -- the named, frozen R1 selection `--image r1` (#323).

plan092 section 2: R1 is selected by name. `release` and no --image stay R0,
and `tree` stays a development selector. `r1` sends R1's kit frozen BY VALUE,
with R1's known-state start, and it emits exactly the bytes the R1 evidence
replayed -- which `--image tree` produced AT THE FREEZE, i.e. at contract
revision 14. Since revision 15 (#388, the rimshot's two bridged-T modes) the
tree's kit is NOT R1's any more: `tree` followed the sound work and `r1` did
not, which is the whole reason R1 is selected by name. The tests below
therefore compare `r1` against revision 14 rather than against the live tree,
and assert separately that the live tree differs from it in exactly revision
15's two writes.
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
    # `tree` moved to 15 with #388's rimshot writes; `r1` stays 14, which is the
    # whole point of naming the image rather than assuming the tree's -- and so
    # does `r2`, which carries R1's drum RTL and contract revision.
    assert uh.IMAGE_REVISION == {"release": 11, "tree": 15, "r1": 14, "r2": 14}
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
    monkeypatch.setitem(dx.KITS_BY_REVISION, 15, lambda: moved)
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
def test_every_r1_command_emits_the_bytes_r1s_evidence_replayed(name, argv, monkeypatch):
    """R1's RTL evidence was recorded with `--image tree`, at the freeze, when
    the tree's kit WAS R1's. Contract revision 15 (#388) moved the tree's kit,
    so this is no longer a bare identity with `tree` -- and it must not become
    one, because `tree` following later sound work is exactly what `r1` exists
    to be insulated from. What the evidence actually rests on is revision 14's
    kit, so `tree` is driven at revision 14 here: `--image r1` reads its writes
    from r1-kit.json and never from the tree, so a failure means the FROZEN kit
    moved, which is the thing worth a red test."""
    r1_stream = _bytes([*argv, "--image", "r1"])
    monkeypatch.setitem(uh.IMAGE_REVISION, "tree", 14)
    assert r1_stream == _bytes([*argv, "--image", "tree"])


def test_r1_is_no_longer_the_trees_kit_and_differs_only_where_revision_15_did():
    """The other side of the test above, so "they match" cannot pass by both
    sides being broken the same way. At the LIVE tree revision the two kits must
    DIFFER -- revision 15 (#388) moved the rimshot -- and the difference must be
    exactly revision 15's two register writes, at the same addresses and with no
    write added or dropped."""
    r1_kit, tree_kit = dict(uh.image_kit("r1")), dict(uh.image_kit("tree"))
    assert set(r1_kit) == set(tree_kit), "revision 15 adds and removes no write"
    moved = {a for a in tree_kit if tree_kit[a] != r1_kit[a]}
    assert moved == {dx.A_PATH + dx.P_RS1X,
                     dx.A_ENV + dx.E_RSG * dx.ENV_STRIDE + 1}, sorted(hex(a) for a in moved)
    # and the streams a player actually gets differ too, on the commands that
    # send a kit at all (`--fixture none` holds a note and sends none).
    differ = [n for n, argv in r1c.COMMANDS
              if _bytes([*argv, "--image", "r1"]) != _bytes([*argv, "--image", "tree"])]
    assert differ, "no R1 command sends the kit, so the freeze is untested end to end"


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
    """`r1`'s session still starts from R1's frozen target. It is no longer
    ALSO the tree's: contract revision 15 (#388) moved two rimshot writes, and
    the session is asserted against R1's own `check_init` -- the frozen target
    -- rather than against `tree`, which is free to move and did."""
    class _Nul:
        timeout = 0
        def write(self, b): return len(b)
        def read(self, n=1): return b""
        def flush(self): pass
    s_r1 = ms.MidiSession(_Nul(), image="r1").init_writes()
    assert r1c.check_init(s_r1, kit_expected=True) == []
    s_tree = ms.MidiSession(_Nul(), image="tree").init_writes()
    assert s_r1 != s_tree, "revision 15 moved the tree's rimshot; r1 must not follow"
    moved = {a for (_, _, a, v), (_, _, _, w) in zip(s_r1, s_tree) if v != w}
    assert moved == {dx.A_PATH + dx.P_RS1X,
                     dx.A_ENV + dx.E_RSG * dx.ENV_STRIDE + 1}, sorted(hex(a) for a in moved)


def _tampered_kit(tmp_path, monkeypatch):
    """R1's frozen kit with ONE value changed (the Judge's case on 74ec7d5)."""
    rec = json.loads(uh.R1_KIT.read_text())
    a, v = rec["writes"][0]
    rec["writes"][0] = [a, v ^ 1]
    p = tmp_path / "r1-kit.json"
    p.write_text(json.dumps(rec))
    monkeypatch.setattr(uh, "R1_KIT", p)
    with pytest.raises(dx.KitRefused):
        uh.r1_kit()                               # the host refuses it, as it should


def test_a_tampered_r1_kit_makes_the_candidate_check_refuse_with_the_reason(tmp_path,
                                                                             monkeypatch):
    _tampered_kit(tmp_path, monkeypatch)
    verdict, detail = r1c.check()
    assert verdict == "REFUSED", (verdict, detail)
    assert "r1-kit.json" in detail and "frozen kit" in detail, detail
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert r1c.main(["check"]) == 2
    assert "REFUSED" in out.getvalue() and "r1-kit.json" in out.getvalue()


def test_a_tampered_r1_kit_makes_the_release_check_refuse_with_the_reason(tmp_path,
                                                                           monkeypatch):
    import r1_release as r1r
    _tampered_kit(tmp_path, monkeypatch)
    verdict, detail = r1r.check()
    assert verdict == "REFUSED", (verdict, detail)
    assert "r1-kit.json" in detail and "frozen kit" in detail, detail
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert r1r.main(["check"]) == 2
    assert "REFUSED" in out.getvalue() and "r1-kit.json" in out.getvalue()

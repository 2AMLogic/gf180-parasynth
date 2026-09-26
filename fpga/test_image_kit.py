"""The kit the player CLI sends is the kit the IMAGE on the board plays.

#273 (clap L2, contract revision 13) changed `drums_fx.kit_808()`. The published
R1 image is revision-12 RTL with no ENV_FRATE, and its release manifest binds
the CLI's bytes (fpga/release/baseline-2025.1.json). These tests hold the
mechanism that keeps both true: `uart_host --image release` (the default) sends
the frozen revision-12 kit, `--image tree` sends the tree's.

Every claim carries a control that must turn it red:
  * the frozen kit REFUSES when the tree's kit moves a non-clap write;
  * the tree image's bytes are NOT the release's (the binding discriminates),
    and differ exactly where revision 13 says they should.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
for _p in ("fpga", "model", "audition", "spec/reference"):
    if str(ROOT / _p) not in sys.path:
        sys.path.insert(0, str(ROOT / _p))

import drums_fx as dx          # noqa: E402
import uart_host as uh         # noqa: E402

MANIFEST = ROOT / "fpga/release/baseline-2025.1.json"
BURST = dx.A_ENV + dx.E_CPBURST * dx.ENV_STRIDE        # ENV_CTL[8]
FRATE = BURST + 3                                     # ENV_FRATE[8], revision 13 only
TAIL_RATE = dx.A_ENV + dx.E_CPTAIL * dx.ENV_STRIDE + 2  # ENV_RATE[9]


def _sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _gt_sha(kit):
    import gen_tables as gt
    return gt.sha([(a << 32) | v for a, v in kit])


def test_rev12_kit_is_revision_11_and_12s_pinned_image():
    """Independent of drums_fx's own pin: spec/reference's generator hash and
    test_tables' REV11 literal (revision 12 moved no table)."""
    import test_tables as tt
    kit = dx.kit_808_rev12()
    assert _gt_sha(kit) == tt.REV11["KIT808"] == dx.KIT808_REV12_SHA256
    assert _gt_sha(dx.kit_808()) == tt.REV13["KIT808"]
    assert len(kit) == len(dx.kit_808()) - 1
    assert FRATE not in dict(kit) and FRATE in dict(dx.kit_808())


def test_rev12_kit_differs_from_the_tree_only_on_the_clap():
    old, new = dict(dx.kit_808_rev12()), dict(dx.kit_808())
    moved = sorted(a for a in set(old) | set(new) if old.get(a) != new.get(a))
    assert moved == sorted([BURST, FRATE, TAIL_RATE])
    assert old[BURST] == dx.env_ctl(dx.CP, 15, 0, 2, 480)
    assert old[TAIL_RATE] == dx.rate_reg(47e-3)


def test_control_rev12_kit_refuses_when_the_tree_kit_moves(monkeypatch):
    """A later change to any other kit write moves kit_808() for the tree but
    not the published image; the frozen kit must refuse, not follow it."""
    live = dx.kit_808()
    a, v = live[0]
    monkeypatch.setattr(dx, "kit_808", lambda: [(a, v ^ 1)] + live[1:])
    with pytest.raises(dx.KitRefused):
        dx.kit_808_rev12()
    with pytest.raises(dx.KitRefused):
        uh.image_kit("release")


def test_default_image_is_the_release_and_unknown_images_refuse():
    assert uh.DEFAULT_IMAGE == "release"
    assert uh.IMAGE_REVISION == {"release": 12, "tree": 13}
    assert uh.image_kit() == dx.kit_808_rev12()
    assert uh.image_kit("tree") == dx.kit_808()
    with pytest.raises(ValueError):
        uh.image_kit("r13")


def _capture(tmp_path, *extra):
    prefix = tmp_path / ("cap" + "".join(extra).replace("-", "_"))
    rc = uh.main(["--dry-run", "run", "--fixture", "demo", *extra, "--capture", str(prefix)])
    assert rc == 0
    return Path(str(prefix) + ".cmds")


def _drum_addrs(cmds: Path) -> set:
    """Drum-section (SEC 1) register addresses in an S-line capture:
    `S wait op sec addr d3 d2 d1 d0 ck`."""
    out = set()
    for line in cmds.read_text().splitlines():
        f = line.split()
        if f[0] == "S" and f[2] in ("57", "45") and int(f[3], 16) & 0x3 == 1:
            out.add(int(f[4], 16))
    return out


def test_cli_default_sends_the_bytes_the_release_binds(tmp_path, capsys):
    bound = json.loads(MANIFEST.read_text())["commands"]["demo"]["cmds_sha256"]
    default = _capture(tmp_path)
    explicit = _capture(tmp_path, "--image", "release")
    assert _sha(default) == _sha(explicit) == bound
    assert FRATE not in _drum_addrs(default)


def test_control_tree_image_is_not_the_release(tmp_path, capsys):
    """The binding discriminates: the tree's kit is different bytes, and the
    difference includes the revision-13 register the R1 image does not have."""
    bound = json.loads(MANIFEST.read_text())["commands"]["demo"]["cmds_sha256"]
    tree = _capture(tmp_path, "--image", "tree")
    assert _sha(tree) != bound
    assert FRATE in _drum_addrs(tree)

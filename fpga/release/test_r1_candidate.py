"""The R1 candidate's frozen target and the checks that use it (#279, plan088).

Every claim here carries a control that turns it red:
  * the frozen kit REFUSES when the tree's kit moves;
  * a sender on the release image (revision-11 kit, no preamble) FAILS the
    init-byte check against the R1 target, naming the kit and the final strike;
  * a sender on the tree image with the release kit injected (WRONG_KIT) FAILS
    for the kit alone -- the preamble is present, so the kit is what is seen;
  * the rolling verifier REFUSES an image selector without a stated target;
  * a session that finds queued events from an earlier session REFUSES.
The R0 (release) command bytes are unchanged: the R0 manifest pins them, and
test_release_manifest/T-RELEASE-BOUND keep holding that.
"""
from __future__ import annotations

import contextlib
import io
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for _p in (HERE, ROOT / "fpga", ROOT / "model", ROOT / "audition", ROOT / "tools"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import drums_fx as dx                      # noqa: E402
import r1_candidate as r1c                 # noqa: E402
import uart_device_sim as dev              # noqa: E402
import uart_host as uh                     # noqa: E402
import verify_rolling_playback as vrp      # noqa: E402

FRATE = dx.A_ENV + dx.E_CPBURST * dx.ENV_STRIDE + 3


def _setup(argv):
    ws, kit = r1c.emitted_setup(argv)
    return ws, kit


def test_frozen_kit_is_revision_14_with_a_nonzero_final_strike():
    kit = r1c.frozen_kit()
    assert len(kit) == r1c.KIT_R14_WRITES and kit == [tuple(w) for w in dx.kit_808()]
    addr, val = r1c.clap_final_strike()
    assert addr == FRATE == 0x63 and val != 0
    assert FRATE not in dict(dx.kit_808_rev11())


def test_control_frozen_kit_refuses_a_moved_kit(monkeypatch):
    moved = [(a, v + 1 if a == FRATE else v) for a, v in dx.kit_808()]
    monkeypatch.setattr(dx, "kit_808", lambda: moved)
    with pytest.raises(r1c.Refused, match="new candidate"):
        r1c.frozen_kit()


@pytest.mark.parametrize("fixture", ["demo", "bar808-full"])
def test_r1_fixture_setup_is_the_frozen_target(fixture):
    ws, kit = _setup(["run", "--fixture", fixture, "--image", "tree"])
    assert kit and r1c.check_init(ws, kit_expected=True) == []
    assert tuple(ws[:2]) == r1c.PREAMBLE
    assert [d for f, s, a, d in ws if s == 1 and a == FRATE] == [r1c.clap_final_strike()[1]]


@pytest.mark.parametrize("fixture", ["demo", "bar808-full"])
def test_control_release_sender_fails_the_r1_target(fixture):
    ws, _ = _setup(["run", "--fixture", fixture, "--image", "release"])
    probs = r1c.check_init(ws, kit_expected=True)
    assert any("preamble" in p for p in probs)
    assert any("kit differs" in p for p in probs)
    assert any("ENV_FRATE" in p for p in probs)


def test_control_wrong_kit_under_tree_is_seen_for_the_kit_alone():
    uh.INJECT_BUGS.add("WRONG_KIT")
    try:
        ws, _ = _setup(["run", "--fixture", "demo", "--image", "tree"])
    finally:
        uh.INJECT_BUGS.discard("WRONG_KIT")
    probs = r1c.check_init(ws, kit_expected=True)
    assert probs and not any("preamble" in p for p in probs)
    assert any("kit differs" in p for p in probs) and any("ENV_FRATE" in p for p in probs)


@pytest.mark.parametrize("argv", [["run", "--note", "45", "--fixture", "none"],
                                  ["run", "--preset", "m5a-saw", "--note", "72", "--fixture", "none"],
                                  ["run", "--note", "45", "--fixture", "m5a"]])
def test_r1_voice_commands_start_from_the_known_state(argv):
    ws, kit = _setup([*argv, "--image", "tree"])
    assert not kit and r1c.check_init(ws, kit_expected=False) == []
    rel, _ = _setup(argv)                       # R0 bytes: no preamble, unchanged
    assert r1c.check_init(rel, kit_expected=False) != []
    assert ws[2:] == rel


def test_rolling_verifier_refuses_a_sender_without_a_stated_target(tmp_path):
    with contextlib.redirect_stdout(io.StringIO()) as out:
        rc = vrp.main(["--image", "tree", "--outdir", str(tmp_path)])
    assert rc == 2 and "--expect-image" in out.getvalue()


def test_rolling_r1_run_and_its_wrong_kit_controls(tmp_path):
    with contextlib.redirect_stdout(io.StringIO()):
        rc = vrp.main(["--image", "tree", "--expect-image", "tree", "--outdir", str(tmp_path),
                       "--epochs", "0"])
    rec = json.loads((tmp_path / "verification.json").read_text())
    assert rc == 0 and rec["state"] == "PASS"
    assert rec["image"] == {"sender": "tree", "target": "tree", "target_contract_revision": 14,
                            "target_kit_sha256": r1c.KIT_R14_SHA256}
    for fx in ("demo", "bar808-full"):
        c = rec["controls"][f"WRONG_KIT:{fx}"]
        assert c["caught"] and c["init_check"]


def test_control_rolling_release_sender_against_r1_target_is_caught(tmp_path):
    with contextlib.redirect_stdout(io.StringIO()):
        rc = vrp.main(["--image", "release", "--expect-image", "tree", "--expect-fail",
                       "--outdir", str(tmp_path), "--epochs", "0"])
    rec = json.loads((tmp_path / "verification.json").read_text())
    assert rc == 0 and rec["state"] == "FAIL" and rec["expect_fail"]["caught"]


def test_r0_rolling_check_is_unchanged(tmp_path):
    """The release target still passes with its own (release) bytes."""
    with contextlib.redirect_stdout(io.StringIO()):
        rc = vrp.main(["--outdir", str(tmp_path), "--epochs", "0"])
    rec = json.loads((tmp_path / "verification.json").read_text())
    assert rc == 0 and rec["image"]["sender"] == rec["image"]["target"] == "release"
    assert not any(k.startswith("WRONG_KIT") for k in rec["controls"])


def _harness():
    h = vrp.Harness(0)
    return h


def test_r1_session_refuses_queued_events_from_an_earlier_session():
    h = _harness()
    h.sim.evq.append([30000, 0, 0, 0x21, 0])       # a stale GATE_OFF far in the future
    h.sim.evq_count += 1
    h.sim.last_due = 30000
    err = io.StringIO()
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
        rc = uh.main(["run", "--note", "45", "--fixture", "none", "--port", "sim",
                      "--image", "tree"], bridge_factory=h.factory)
    assert rc == 2 and "queued events" in err.getvalue()
    assert not [w for w in h.sim.writes if w[5] == "live"]     # nothing was sent


def test_control_release_session_does_not_check_the_queue():
    """R0's host behaviour is unchanged: the idle check is R1's."""
    h = _harness()
    h.sim.evq.append([30000, 0, 0, 0x21, 0])
    h.sim.evq_count += 1
    h.sim.last_due = 30000
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        uh.main(["run", "--note", "45", "--fixture", "none", "--port", "sim"],
                bridge_factory=h.factory)
    assert [w for w in h.sim.writes if w[5] == "live"]


def test_drift_is_refused_with_the_r1_reason():
    import qualified_domain as qd
    with pytest.raises(qd.Rejected, match="R1"):
        qd.check_stream([(0, 0, qd.A_DRIFT, 5)], image="tree")
    with pytest.raises(qd.Rejected, match="released image was built"):
        qd.check_stream([(0, 0, qd.A_DRIFT, 5)])


def test_live_midi_known_state_is_the_frozen_target():
    ws = r1c.live_midi_init()
    assert tuple(ws[:2]) == r1c.PREAMBLE and r1c.check_init(ws, kit_expected=True) == []

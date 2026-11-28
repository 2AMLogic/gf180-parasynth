"""The R2 release manifest (fpga/release/r2_release.py): BOUND to the built
image, with R1 BOUND as its rollback; every refusal is for its reason."""
import contextlib
import io
import json
import shutil
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import r2_release as r2r                                   # noqa: E402
import r1_candidate as r1c                                 # noqa: E402
import release_manifest as rm                              # noqa: E402
import uart_host as uh                                     # noqa: E402
import refusal                                             # noqa: E402


def test_the_committed_r2_manifest_is_bound():
    verdict, detail = r2r.check()
    assert verdict == "BOUND", detail


def test_r2_manifest_names_its_configuration_override_and_rollback():
    m = json.loads(r2r.MANIFEST.read_text())
    assert m["configuration"] == {"OSC2X": 1, "FILTER2X": 1, "PULSE2X": 1}
    assert m["image"]["image"] == "r2"
    assert "OVERRIDE" in m["status"] and "NOT ACCEPTED" in m["operator_override"]["rule_verdict"]
    assert len(m["operator_override"]["known_limitations"]) == 6
    assert m["rollback"]["name"] == "R1"
    assert sorted(m["image"]["differs_from_r1_freeze"]) == [
        "fpga/boards/arty-a7-100.xdc", "rtl-sketch/ladder_dp_n.v",
        "rtl-sketch/polyblep_saw_pair.v", "rtl-sketch/voice_dp.v"]
    assert all(c.get("equals_r1_bytes") for n, c in m["host"]["commands"].items()
               if n != "live-midi")


@pytest.mark.parametrize("case", ["image", "host"])
def test_stale_control_is_caught_at_exactly_its_fields(tmp_path, case):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = r2r.stale_control(case, tmp_path)
    assert rc == 1, out.getvalue()
    assert "stale_control: STALE" in out.getvalue()


def test_stale_control_host_reports_refused_when_the_cli_refuses(tmp_path, monkeypatch,
                                                                  capsys):
    """#784's confirmed site, in the R2 copy: release_manifest._capture
    refuses (a uart_host that exits non-zero on `run --fixture demo --image
    release`), and that refusal must reach stale_control's designed REFUSED
    verdict (exit 2), not escape as a traceback past the handler -- a sibling
    RuntimeError subclass does not match `except Refused`. The CLI's own
    reason is asserted too: any OTHER refusal (a missing manifest, a copy
    that is not BOUND) exits 2 with the same verdict line."""
    def refused(argv):
        print("uart_host: unsupported command", file=sys.stderr)
        return 1

    monkeypatch.setattr(uh, "main", refused)
    assert r2r.stale_control("host", tmp_path) == 2
    out = capsys.readouterr().out
    assert "stale_control: REFUSED" in out
    assert "uart_host: unsupported command" in out


def test_a_non_refusal_error_from_the_capture_still_propagates(tmp_path, monkeypatch):
    """The control for the test above: the handler catches refusals by class
    and nothing wider (tools/refusal.py). An implementation error at the same
    call site is an EXECUTION ERROR; it must leave stale_control untouched,
    never become `stale_control: REFUSED`."""
    def broke(argv, outdir):
        raise ValueError("not a refusal: the capture broke")

    monkeypatch.setattr(rm, "_capture", broke)
    with pytest.raises(ValueError, match="the capture broke"):
        r2r.stale_control("host", tmp_path)


# ---- #1671: a sibling refusal through the host, and from rm.presets() ---------

def _sibling():
    """`mono_m5a_score.Refused`, the class `selected_preset.definition` raises
    for an engine profile or waveform name missing from the tables: a
    refusal.Refused that is none of uh.Refused, rm.Refused, r1c.Refused or
    this module's own Refused. Asserted here, at the point of use: if the
    classes are ever re-based onto each other the escape is gone and the
    tests below no longer measure it."""
    import selected_preset
    sibling = selected_preset.score.Refused
    assert issubclass(sibling, refusal.Refused)
    assert not issubclass(sibling, (uh.Refused, rm.Refused, r1c.Refused, r2r.Refused))
    return sibling


def _arm_preset_regs_from_inside_presets(monkeypatch, preset_regs):
    """Swap `uh.preset_regs` for `preset_regs` from INSIDE `rm.presets`, so
    what it raises arrives at host_binding's `presets = rm.presets()` unconverted.
    Armed before build(), the CLI's own boundary (#1671) would first turn it
    into exit 2 and _capture into rm.Refused, which the handler around the
    capture loop already catches: the refusal would end as r2_release.Refused
    without ever reaching rm.presets(), and the test would pass without
    measuring the handler it exists for."""
    real_presets = rm.presets

    def presets():
        monkeypatch.setattr(uh, "preset_regs", preset_regs)
        return real_presets()

    monkeypatch.setattr(rm, "presets", presets)


def test_a_sibling_refusal_through_the_host_ends_as_r2_refused(monkeypatch):
    """#1671 sites 2/3: host_binding's `_capture` and `emitted_setup` call
    `uart_host.main` IN PROCESS. A `mono_m5a_score.Refused` from
    `selected_preset.definition` used to escape `uart_host.main` (nothing
    guarded command assembly), then `_capture`, which reads only a return
    code, then the handler around the capture loop, which names r1c.Refused
    and rm.Refused. With the CLI as its own refusal boundary (#1671) the
    callers convert exit 2 to their own Refused and that handler makes it
    this module's -- so the capture loop's handler is unchanged."""
    import selected_preset
    sibling = _sibling()

    def definition(name):
        raise sibling("injected: engine profile missing from the tables")

    monkeypatch.setattr(selected_preset, "definition", definition)
    with pytest.raises(r2r.Refused, match="host's R2 bytes are not the frozen target") as ei:
        r2r.build()
    assert "engine profile missing from the tables" in str(ei.value)


def test_the_in_process_callers_turn_a_nonzero_cli_exit_into_their_own_refusal(tmp_path,
                                                                             monkeypatch):
    """What the repair above rests on, as a check rather than a reading: both
    callers of the in-process CLI convert ANY non-zero exit into a class the
    capture loop's handler names. Reverting the uart_host.main boundary is
    what turns the test above red; this one stays green either way."""
    monkeypatch.setattr(uh, "main", lambda argv: 2)
    argv = ["run", "--note", "45", "--fixture", "none"]
    with pytest.raises(rm.Refused, match="the CLI refused"):
        rm._capture(argv, tmp_path)
    with pytest.raises(r1c.Refused, match=r"the CLI refused .* \(exit 2\)"):
        r1c.emitted_setup(argv)


def test_a_sibling_refusal_from_presets_is_refused_by_check(monkeypatch):
    """#1671: `rm.presets()` calls `uh.preset_regs` directly, not through
    `uart_host.main`, so the CLI's boundary does not reach it, and it sits
    outside the capture loop's handler. check() caught this module's Refused
    only; a sibling left it as a traceback instead of ("REFUSED", reason)."""
    sibling = _sibling()

    def preset_regs(preset=None):
        raise sibling("injected: engine profile missing from the tables")

    _arm_preset_regs_from_inside_presets(monkeypatch, preset_regs)
    verdict, detail = r2r.check(r2r.MANIFEST)
    assert verdict == "REFUSED", (verdict, detail)
    assert "engine profile missing from the tables" in detail


def test_a_sibling_refusal_from_presets_is_refused_by_main_write(tmp_path, monkeypatch,
                                                                  capsys):
    """The same handler through the CLI's --write path: exit 2 with the REFUSED
    line, and no manifest written."""
    sibling = _sibling()

    def preset_regs(preset=None):
        raise sibling("injected: engine profile missing from the tables")

    _arm_preset_regs_from_inside_presets(monkeypatch, preset_regs)
    manifest = tmp_path / "r2.json"
    rc = r2r.main(["--write", "--manifest", str(manifest)])
    out = capsys.readouterr().out
    assert rc == 2, out
    assert f"{r2r.PREFIX}REFUSED" in out
    assert "engine profile missing from the tables" in out
    assert not manifest.exists()


def test_a_non_refusal_error_from_presets_still_propagates(tmp_path, monkeypatch):
    """The control for both handlers: they catch refusals by class and nothing
    wider (tools/refusal.py). An implementation error at the same site is an
    EXECUTION ERROR; it must leave check() and main() untouched, never become
    a REFUSED verdict or exit 2."""
    def preset_regs(preset=None):
        raise KeyError("engine profile")

    _arm_preset_regs_from_inside_presets(monkeypatch, preset_regs)
    with pytest.raises(KeyError, match="engine profile"):
        r2r.check(r2r.MANIFEST)
    with pytest.raises(KeyError, match="engine profile"):
        r2r.main(["--write", "--manifest", str(tmp_path / "r2.json")])


def test_an_unbound_rollback_refuses(monkeypatch):
    monkeypatch.setattr(r2r.r1r, "check", lambda *a, **k: ("STALE", "moved"))
    verdict, detail = r2r.check()
    assert verdict == "REFUSED" and "rollback R1 is not BOUND" in detail, detail


def test_a_run_without_the_pulse2x_define_is_not_r2_evidence(tmp_path, monkeypatch):
    src = r2r.EVIDENCE_DIR
    dst = tmp_path / "r2"
    shutil.copytree(src, dst)
    ident = next(dst.rglob("*run_identity.json"))
    rec = json.loads(ident.read_text())
    rec["identity"]["defines"] = [d for d in rec["identity"]["defines"]
                                  if d != r2r.PULSE2X_DEFINE]
    ident.write_text(json.dumps(rec))
    monkeypatch.setattr(r2r, "EVIDENCE_DIR", dst)
    with pytest.raises(r2r.Refused, match="lack VOICE_PULSE_2X"):
        r2r.evidence()


def test_r2_does_not_write_r1_or_r0_records():
    src = (HERE / "r2_release.py").read_text()
    for name in ("r1-2025.1.json", "r1-candidate.json", "baseline-2025.1.json"):
        assert f'{name}").write' not in src

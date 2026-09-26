"""tools/check_arty_evidence_binding.py: red first, then the current state.

The red control is the real thing this gate exists to catch: the binding left
pointing at reports/arty/uart-clean, which was captured before voice_dp.v
gained per-oscillator drift (6.11). That state is what produced 23 failures in
fpga/test_build_arty.py, fpga/test_publish_arty.py and
fpga/test_publish_binding.py, 39 minutes into the broad pytest job.
"""
import importlib.util
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location(
    "check_arty_evidence_binding", ROOT / "tools/check_arty_evidence_binding.py")
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)

sys.path.insert(0, str(ROOT / "fpga"))
import publish_arty  # noqa: E402

STALE = ROOT / "fpga/reports/arty/uart-clean/verification.json"


def test_red_first_the_pre_drift_record_is_reported_stale_and_names_the_file(capsys):
    """The control. Bind the pre-drift record and the gate must go red for its
    own reason -- naming rtl-sketch/voice_dp.v, not just a count."""
    assert STALE.is_file(), "the superseded record must stay in the tree"
    saved = dict(publish_arty.VERIFICATION_BY_WRAPPER)
    try:
        publish_arty.VERIFICATION_BY_WRAPPER.clear()
        publish_arty.VERIFICATION_BY_WRAPPER["arty_a7_top"] = STALE
        assert gate.main([]) == 1
        out = capsys.readouterr().out
        assert "STALE" in out
        assert "rtl-sketch/voice_dp.v" in out
        assert "uart-clean" in out
        assert "verify_uart_bridge.py" in out, "a red must say how to refresh it"
    finally:
        publish_arty.VERIFICATION_BY_WRAPPER.clear()
        publish_arty.VERIFICATION_BY_WRAPPER.update(saved)


def test_the_gate_passes_against_the_current_state(capsys):
    """Satisfiability. An unsatisfiable gate is worse than no gate."""
    assert gate.main([]) == 0
    out = capsys.readouterr().out
    assert "BOUND: arty_a7_top" in out
    assert "STALE" not in out


def test_the_bound_record_is_exactly_what_build_arty_accepts():
    """The gate must answer the same question build_arty.validate_verification
    does -- not a re-implementation that could drift from it."""
    import build_arty as build
    for wrapper, path in gate.bound_bindings():
        assert gate.drift(path) == []
        build.validate_verification(path, build.sources() + build.roms())


def test_a_wrapper_with_no_bound_or_missing_record_refuses_rather_than_passes(tmp_path, capsys):
    saved = dict(publish_arty.VERIFICATION_BY_WRAPPER)
    try:
        publish_arty.VERIFICATION_BY_WRAPPER.clear()
        assert gate.main([]) == 2
        assert "REFUSED" in capsys.readouterr().out
        publish_arty.VERIFICATION_BY_WRAPPER["arty_a7_top"] = tmp_path / "absent.json"
        assert gate.main([]) == 2
        assert "REFUSED" in capsys.readouterr().out
    finally:
        publish_arty.VERIFICATION_BY_WRAPPER.clear()
        publish_arty.VERIFICATION_BY_WRAPPER.update(saved)


def test_superseded_records_are_data_and_never_a_failure(capsys):
    """Nineteen-odd committed records name a moved source and must stay that
    way; the gate must not count them, or the one real red is lost in them."""
    records = dict(gate.historical())
    assert "fpga/reports/arty/uart-clean/verification.json" in records
    # EXACT lists, and they grow only when a compiled source changes: the
    # pre-drift proof (the one the published R0 image cites) differs by drift
    # (voice_dp.v) and by the clap's final strike (contract rev 13: the drum
    # RTL and the top that carries ENV_FRATE); the pre-L2 drift proof by
    # exactly the latter.
    L2 = ["rtl-sketch/drum_dp.v", "rtl-sketch/drum_kit.v",
          "rtl-sketch/drum_regs.v", "rtl-sketch/synth_top.v"]
    assert records["fpga/reports/arty/uart-clean/verification.json"] == sorted(
        L2 + ["rtl-sketch/voice_dp.v"])
    assert records["fpga/reports/arty/drift-clean/verification.json"] == L2
    bound = {str(p.relative_to(ROOT)) for _, p in gate.bound_bindings()}
    assert not (bound & set(records)), "the bound record is not history"
    assert gate.main(["--list-historical"]) == 0
    out = capsys.readouterr().out
    assert "expected, not a failure" in out
    assert "uart-clean" in out


R0 = ROOT / "fpga/reports/arty/integrated-baseline-2025.1"


def _sha(path):
    import hashlib
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def test_r0_published_image_is_bound_to_its_historical_source_set():
    """plan087 section 4: R0 (bitstream a66c9349..., checkpoint 6c3c22c5...)
    is bound to the source set it was built from, NOT to the working tree.
    Its proof is reports/arty/uart-clean, by record AND transcript hash; the
    image and that proof name the same bytes for every source both cover; and
    the tree has since moved on exactly the files the historical record says,
    so neither the old image nor its proof can be read as a build of the
    current source. If a later change rewrote uart-clean in place, or rebound
    the published image to a live-tree record, this goes red."""
    import json
    pub = json.loads((R0 / "publication.json").read_text())
    rec = json.loads(STALE.read_text())
    assert pub["bitstream_sha256"].startswith("a66c9349")
    assert pub["verification"]["record_sha256"] == _sha(STALE)
    assert pub["verification"]["transcript_sha256"] == _sha(STALE.with_name("verification.txt"))
    common = set(pub["source_sha256"]) & set(rec["source_sha256"])
    assert len(common) >= 8
    assert {k: pub["source_sha256"][k] for k in common} == {k: rec["source_sha256"][k] for k in common}
    moved = sorted(k for k in pub["source_sha256"]
                   if (ROOT / k).is_file() and _sha(ROOT / k) != pub["source_sha256"][k])
    assert moved == dict(gate.historical())[str(STALE.relative_to(ROOT))]
    assert moved, "R0 is historical: the live tree is not its source set"
    bound = {str(q.relative_to(ROOT)) for _, q in gate.bound_bindings()}
    assert str(STALE.relative_to(ROOT)) not in bound, "the live binding must not be R0's proof"

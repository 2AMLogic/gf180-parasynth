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
    # (voice_dp.v) and by the clap's final strike (contract rev 14: the drum
    # RTL and the top that carries ENV_FRATE); the pre-L2 drift proof by
    # exactly the latter.
    L2 = ["rtl-sketch/drum_dp.v", "rtl-sketch/drum_kit.v",
          "rtl-sketch/drum_regs.v", "rtl-sketch/synth_top.v"]
    assert records["fpga/reports/arty/uart-clean/verification.json"] == sorted(
        L2 + ["rtl-sketch/voice_dp.v"])
    V = ["rtl-sketch/voice_dp.v"]            # polyBLAMP (revision 13) since then
    assert records["fpga/reports/arty/drift-clean/verification.json"] == sorted(L2 + V)
    # the two parents of the revision-14 tree: polyBLAMP without L2, and L2
    # without polyBLAMP (whose drum comments were renumbered 13 -> 14 after it)
    assert records["fpga/reports/arty/shark-blamp-clean/verification.json"] == L2
    assert records["fpga/reports/arty/l2-clean/verification.json"] == [
        "rtl-sketch/drum_dp.v", "rtl-sketch/drum_regs.v", "rtl-sketch/voice_dp.v"]
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


# The exact set of R0-published sources the tree has moved on since 62392bd
# published the image, each with the reason it moved. NAMED, not derived: a
# set derived from the tree is whatever the tree says, which is not a gate.
# Extending it is a claim -- see the failure message below for what to check.
R0_MOVED = {
    # the verification-scope five, the same list
    # test_superseded_records_are_data_and_never_a_failure pins by hand
    "rtl-sketch/drum_dp.v": "clap final strike, contract rev 14",
    "rtl-sketch/drum_kit.v": "clap final strike, contract rev 14",
    "rtl-sketch/drum_regs.v": "clap final strike, contract rev 14",
    "rtl-sketch/synth_top.v": "ENV_FRATE, contract rev 14",
    "rtl-sketch/voice_dp.v": "per-oscillator drift (6.11)",
    # publication scope only (see _check_r0_binding): 383f10b, #315, which
    # landed 2026-09-26, three days after 62392bd published R0
    "fpga/boards/arty-a7-100.xdc": "#315 UART-sync constraints bind g_uart.u_uart",
}


def _check_r0_binding(sha=_sha):
    """plan087 section 4: R0 (bitstream a66c9349..., checkpoint 6c3c22c5...)
    is bound to the source set it was built from, NOT to the working tree.
    Its proof is reports/arty/uart-clean, by record AND transcript hash; the
    image and that proof name the same bytes for every source both cover; and
    the tree has since moved on exactly the files R0_MOVED names, so neither
    the old image nor its proof can be read as a build of the current source.
    If a later change rewrote uart-clean in place, rebound the published image
    to a live-tree record, or reverted one of those sources, this goes red.

    Parameterised on `sha` so a control can present a reverted source and
    prove each assertion below is still reachable. Raises AssertionError;
    never returns a verdict."""
    import json
    import build_arty as build
    pub = json.loads((R0 / "publication.json").read_text())
    rec = json.loads(STALE.read_text())
    assert pub["bitstream_sha256"].startswith("a66c9349")
    assert pub["verification"]["record_sha256"] == _sha(STALE)
    assert pub["verification"]["transcript_sha256"] == _sha(STALE.with_name("verification.txt"))
    common = set(pub["source_sha256"]) & set(rec["source_sha256"])
    assert len(common) >= 8
    assert {k: pub["source_sha256"][k] for k in common} == {k: rec["source_sha256"][k] for k in common}

    # Checked BEFORE historical(), which skips bound records: a rebind must
    # fail here by name, not as a KeyError four lines further down.
    bound = {str(q.relative_to(ROOT)) for _, q in gate.bound_bindings()}
    assert str(STALE.relative_to(ROOT)) not in bound, \
        "the live binding must not be R0's proof"

    # PUBLICATION SCOPE IS WIDER THAN VERIFICATION SCOPE, BY DESIGN, and that
    # is why this cannot simply equal gate.historical()'s list. publish_arty
    # records sources()+roms()+[XDC] because a bitstream depends on its pin
    # constraints; build_arty.validate_verification (and so drift() and
    # historical()) checks sources()+roms() only, because the UART digital
    # bench never drives a physical pin. Name the difference; do not let it be
    # whatever the two records happen to disagree about.
    verification_scope = {str(p.relative_to(build.ROOT))
                          for p in build.sources() + build.roms()}
    xdc = str(build.XDC.relative_to(ROOT))
    assert sorted(set(pub["source_sha256"]) - verification_scope) == [xdc], (
        "publication scope minus verification scope must be exactly the XDC; "
        "if it is not, publish_arty's source_sha256 construction changed and "
        "this test's scope reasoning needs re-deriving, not widening")
    assert sha(ROOT / xdc) != pub["source_sha256"][xdc], (
        f"{xdc} hashes to R0's published bytes again: R0's constraint set is "
        "no longer purely historical (reverted? re-published?). Do not widen "
        "R0_MOVED to match -- work out which happened")

    moved = sorted(k for k in pub["source_sha256"]
                   if (ROOT / k).is_file() and sha(ROOT / k) != pub["source_sha256"][k])
    assert moved == sorted(R0_MOVED), (
        "R0's moved source set is not what R0_MOVED names. If a source moved, "
        "add it WITH the commit that moved it (it must postdate 62392bd, R0's "
        "publication); if one stopped moving it was reverted to R0's bytes and "
        "R0 is no longer purely historical. Either way the fix is evidence, "
        "not bookkeeping.")
    # and the two scopes still agree where they overlap: everything moved
    # except the XDC is exactly what the verification-scope gate reports for
    # R0's own proof, so the publication-side and gate-side computations
    # cannot drift apart unnoticed.
    assert [k for k in moved if k != xdc] == \
        dict(gate.historical())[str(STALE.relative_to(ROOT))]
    assert moved, "R0 is historical: the live tree is not its source set"


def test_r0_published_image_is_bound_to_its_historical_source_set():
    _check_r0_binding()


def _revert_to_r0(rel, monkeypatch):
    """Present the whole world -- this test AND gate.historical(), which
    hashes through build_arty.sha -- with `rel` reverted to the bytes R0
    published. Returns the sha callable to hand _check_r0_binding."""
    import json
    import build_arty as build
    published = json.loads((R0 / "publication.json").read_text())["source_sha256"][rel]
    target = (ROOT / rel).resolve()

    def sha(path):
        return published if Path(path).resolve() == target else _sha(path)

    monkeypatch.setattr(build, "sha", sha)
    return sha


def test_control_a_reverted_xdc_turns_the_r0_binding_red(monkeypatch):
    """Control for the publication-only half of the widened expectation. The
    XDC is out of gate.historical()'s scope, so nothing else in this file
    would notice it going back to R0's bytes -- if the expectation were
    derived from the tree rather than named, this would stay green."""
    sha = _revert_to_r0("fpga/boards/arty-a7-100.xdc", monkeypatch)
    with pytest.raises(AssertionError, match="no longer purely historical"):
        _check_r0_binding(sha)


def test_control_a_reverted_compiled_source_turns_the_r0_binding_red(monkeypatch):
    """Control for the verification-scope half, and the reason R0_MOVED is a
    named constant rather than gate.historical()'s output: revert voice_dp.v
    and BOTH sides of that equality shrink together, so the cross-check stays
    satisfied. Only the named set catches it."""
    sha = _revert_to_r0("rtl-sketch/voice_dp.v", monkeypatch)
    with pytest.raises(AssertionError, match="R0's moved source set"):
        _check_r0_binding(sha)


def test_control_rebinding_the_live_wrapper_to_r0s_proof_turns_it_red():
    """Control: the failure the docstring promises. If someone rebinds the
    published image's pre-drift proof as the wrapper's live evidence, R0 stops
    being history and this must say so by name."""
    saved = dict(publish_arty.VERIFICATION_BY_WRAPPER)
    try:
        publish_arty.VERIFICATION_BY_WRAPPER["arty_a7_top"] = STALE
        with pytest.raises(AssertionError, match="must not be R0's proof"):
            _check_r0_binding()
    finally:
        publish_arty.VERIFICATION_BY_WRAPPER.clear()
        publish_arty.VERIFICATION_BY_WRAPPER.update(saved)

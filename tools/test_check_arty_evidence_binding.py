"""tools/check_arty_evidence_binding.py: red first, then the current state.

The red control is the real thing this gate exists to catch: the binding left
pointing at reports/arty/uart-clean, which was captured before voice_dp.v
gained per-oscillator drift (6.11). That state is what produced 23 failures in
fpga/test_build_arty.py, fpga/test_publish_arty.py and
fpga/test_publish_binding.py, 39 minutes into the broad pytest job.
"""
import importlib.util
import json
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


XDC_REL = "fpga/boards/arty-a7-100.xdc"
R1 = "fpga/reports/arty/r1-player-preview-2025.1/publication.json"


def _bound_record_plus_xdc(tmp_path, xdc_sha):
    """The live bound record with the one key a verification record never
    carries: the constraint file.

    Still synthesised, and still nothing any bench emits -- the UART bench
    hashes sources()+roms() only. Since #436 it has exactly ONE remaining
    user, test_coverage_separates_the_two_states_where_drift_cannot, which is
    a unit test of coverage()'s two states and needs a record that DIFFERS on
    the XDC rather than one that never hashed it. The gate-level arms no
    longer use it: they run against the real committed constraint record."""
    import json
    source = gate.bound_bindings()[0][1]
    record = json.loads(source.read_text())
    record["source_sha256"][XDC_REL] = xdc_sha
    path = tmp_path / "verification.json"
    path.write_text(json.dumps(record, indent=2) + "\n")
    return path


def _bind(path):
    saved = dict(publish_arty.VERIFICATION_BY_WRAPPER)
    publish_arty.VERIFICATION_BY_WRAPPER.clear()
    publish_arty.VERIFICATION_BY_WRAPPER["arty_a7_top"] = path
    return saved


def _restore(saved):
    publish_arty.VERIFICATION_BY_WRAPPER.clear()
    publish_arty.VERIFICATION_BY_WRAPPER.update(saved)


def test_the_digital_record_still_does_not_cover_the_xdc_and_never_will(capsys):
    """#421 point 3, unchanged by #436 and deliberately so. The UART bench
    hashes sources()+roms(); it never opens a constraint file, so its record
    says NOTHING about the XDC -- a different state from "the bytes moved".

    #436 did not widen this record. It bound a SECOND one, from a bench that
    does read the XDC, so the answer now comes from evidence rather than from
    a record claiming coverage of bytes it never read."""
    assert gate.coverage(gate.bound_bindings()[0][1], gate.PUBLICATION_SCOPE) == \
        [(XDC_REL, gate.NOT_COVERED)]
    # ... and in publication scope that record is asked only about the files
    # its own bench read, which is why the gate as a whole can now answer
    import build_arty as build
    digital = [e for e in gate.bound_evidence(gate.PUBLICATION_SCOPE)
               if e.validate is None]
    assert len(digital) == 1
    assert gate.coverage(digital[0].record, gate.PUBLICATION_SCOPE,
                         digital[0].files) == []
    assert build.XDC not in digital[0].files, \
        "the digital bench must never be asked to answer for the constraints"


def test_publication_scope_is_satisfied_against_the_current_tree(capsys):
    """The acceptance shape of #436, and the state #421 could not reach: exit
    0, from a record that covers the XDC's LIVE bytes rather than from a
    widened default that stopped checking."""
    import build_arty as build
    assert gate.main(["--scope", "publication"]) == 0
    out = capsys.readouterr().out
    assert gate.NOT_COVERED not in out and gate.DIFFERS not in out
    assert XDC_REL in out, "a green must name the file it covers"
    assert "xdc-binding" in out, "and the record that covers it"
    # the 0 is a hash comparison against the tree's bytes, not an absence of
    # one: the record's XDC entry IS the live sha256
    constraint = [e for e in gate.bound_evidence(gate.PUBLICATION_SCOPE)
                  if e.validate is not None]
    assert len(constraint) == 1
    recorded = json.loads(Path(constraint[0].record).read_text())["source_sha256"]
    assert recorded[XDC_REL] == build.sha(build.XDC)


def test_the_constraint_record_is_exactly_what_its_own_bench_accepts():
    """The mirror of test_the_bound_record_is_exactly_what_build_arty_accepts:
    the gate must answer the same question fpga/verify_xdc_binding asks, not a
    re-implementation that could drift from it."""
    import verify_xdc_binding as vxb
    for item in gate.bound_evidence(gate.PUBLICATION_SCOPE):
        if item.validate is None:
            continue
        item.validate(item.record)
        assert vxb.validate_record(item.record)["state"] == "PASS"
        assert gate.coverage(item.record, gate.PUBLICATION_SCOPE,
                             item.files) == []


def test_control_a_constraint_record_that_is_not_evidence_refuses(tmp_path, capsys):
    """Controls for the record's own preconditions, driven through the gate's
    exit code. A record carrying the right hashes but produced by an INJECTED
    run, or against other constraint bytes, must REFUSE -- otherwise the one
    thing a consumer cannot re-derive is exactly the thing it trusts."""
    import verify_xdc_binding as vxb
    real = Path(vxb.CONSTRAINT_BY_WRAPPER[vxb.WRAPPER])
    base = json.loads(real.read_text())
    saved = dict(vxb.CONSTRAINT_BY_WRAPPER)
    try:
        for index, (needle, changes) in enumerate((
                ("INJECTED", {"inject": "UART_SLASH_JOIN"}),
                ("not the tree's constraint file",
                 {"xdc_override": "/tmp/pre315.xdc"}),
                ("property with problems",
                 {"properties": dict(base["properties"],
                                     hier_separators=["edited in by hand"])}))):
            directory = tmp_path / f"arm{index}"
            directory.mkdir()
            path = directory / vxb.RECORD
            path.write_text(json.dumps(dict(base, **changes), indent=2) + "\n")
            (directory / vxb.TRANSCRIPT).write_bytes(
                real.with_name(vxb.TRANSCRIPT).read_bytes())
            vxb.CONSTRAINT_BY_WRAPPER[vxb.WRAPPER] = path
            assert gate.main(["--scope", "publication"]) == 2
            out = capsys.readouterr().out
            assert "REFUSED" in out and needle in out
    finally:
        vxb.CONSTRAINT_BY_WRAPPER.clear()
        vxb.CONSTRAINT_BY_WRAPPER.update(saved)


def test_control_a_file_no_record_answers_for_refuses_rather_than_passing(monkeypatch, capsys):
    """The direction a coverage check must never move in. If a bench narrows
    its read set, the files it dropped are not covered -- they are UNASKED,
    and a gate that quietly stops asking gets greener as its evidence gets
    thinner."""
    real = gate.bound_evidence

    def narrowed(scope=gate.VERIFICATION_SCOPE):
        return [item if item.validate is not None else
                item._replace(files=[f for f in item.files if f.suffix != ".hex"])
                for item in real(scope)]

    monkeypatch.setattr(gate, "bound_evidence", narrowed)
    assert gate.main(["--scope", "publication"]) == 2
    out = capsys.readouterr().out
    assert "no bound record answers" in out
    assert ".hex" in out and gate.NOT_COVERED in out


def test_verification_scope_names_the_question_it_does_not_answer(capsys):
    """Until #421 the rung printed "covers every compiled source" and stopped.
    383f10b changed the XDC and nothing said a word. The verdict is unchanged
    (that would be an unsatisfiable rung) but the output must no longer read
    as constraint coverage."""
    assert gate.main([]) == 0
    out = capsys.readouterr().out
    assert "BOUND: arty_a7_top" in out
    assert XDC_REL in out, "the rung must name the file it does not check"
    assert "xdc-binding" in out, \
        "#436: and, now that one exists, the record that DOES answer for it"
    assert "--scope publication" in out
    assert "STALE" not in out


def test_control_a_reverted_xdc_turns_the_publication_gate_red(tmp_path, monkeypatch, capsys):
    """THE control for #421, driven directly: both arms call gate.main and
    assert its exit code, so a widening that failed to fire shows up as the
    GREEN arm's code repeated, not as an unraised exception swallowed by a
    matcher.

    Arm 1 proves the mode is not red by construction; arm 2 reverts the XDC to
    the bytes R0 was published with and requires a red that NAMES the file.

    #436: both arms now run against the REAL committed records. Arm 1 used to
    bind a record this test had just written, which made "the mode is not red
    by construction" a statement about the test rather than the repository."""
    assert gate.main(["--scope", "publication"]) == 0, \
        "the committed constraint record must cover the live XDC"
    assert "BOUND: arty_a7_top" in capsys.readouterr().out

    _revert_to_r0(XDC_REL, monkeypatch)
    code = gate.main(["--scope", "publication"])
    out = capsys.readouterr().out
    assert code == 1, ("the XDC moved and publication scope stayed green: "
                       "the constraint file is not in its comparison set")
    assert "STALE" in out
    assert XDC_REL in out, "a red must name the file, not just a count"
    assert gate.DIFFERS in out


def test_the_same_reverted_xdc_stays_invisible_to_the_default_rung(monkeypatch, capsys):
    """The other half of the decision, pinned so it cannot be widened by
    accident: verification scope still does not look at the constraints, so
    the Makefile rung cannot go red for a question the DIGITAL record cannot
    answer. If this ever fails, the default was widened and
    test_the_bound_record_is_exactly_what_build_arty_accepts is now checking a
    different question from build_arty.validate_verification."""
    _revert_to_r0(XDC_REL, monkeypatch)
    assert gate.main([]) == 0
    out = capsys.readouterr().out
    assert "BOUND: arty_a7_top" in out
    assert gate.DIFFERS in out, \
        "invisible to the VERDICT is not invisible to the READER: the " \
        "constraint line must still report the reverted state"
    assert "xdc-binding" in out, "and say which record saw it"


def test_the_default_rung_consults_one_record_and_never_the_constraint_one():
    """The #436 boundary, as data rather than as prose. Verification scope
    must resolve to exactly the digital record over exactly
    build_arty.validate_verification's file set: a second record appearing
    here would make the Makefile rung depend on evidence build_arty does not
    check, which is a different question wearing the same name."""
    import build_arty as build
    evidence = gate.bound_evidence()
    assert [item.record for item in evidence] == [p for _, p in gate.bound_bindings()]
    assert all(item.validate is None for item in evidence)
    for item in evidence:
        assert item.files == build.sources() + build.roms()
    assert len(gate.bound_evidence(gate.PUBLICATION_SCOPE)) == len(evidence) + 1


def test_control_a_moved_source_is_not_reported_as_constraint_drift(monkeypatch, capsys):
    """False-positive control. Publication scope must go red for voice_dp.v
    and say nothing about the XDC -- otherwise "red when the XDC moves" is
    just "red", and the mode carries no constraint information."""
    _revert_to_r0("rtl-sketch/voice_dp.v", monkeypatch)
    assert gate.main(["--scope", "publication"]) == 1
    out = capsys.readouterr().out
    assert "rtl-sketch/voice_dp.v" in out
    assert XDC_REL not in out


def test_coverage_separates_the_two_states_where_drift_cannot(tmp_path):
    """drift() returns names, so "never hashed" and "hashed and moved" are the
    same string to it. That conflation is what hid the gap; coverage() is the
    call that can tell them apart, and both must still agree on the names."""
    bound = gate.bound_bindings()[0][1]
    stale_xdc = _bound_record_plus_xdc(tmp_path, "0" * 64)
    assert gate.coverage(bound, gate.PUBLICATION_SCOPE) == [(XDC_REL, gate.NOT_COVERED)]
    assert gate.coverage(stale_xdc, gate.PUBLICATION_SCOPE) == [(XDC_REL, gate.DIFFERS)]
    assert gate.drift(bound, gate.PUBLICATION_SCOPE) == [XDC_REL]
    assert gate.drift(stale_xdc, gate.PUBLICATION_SCOPE) == [XDC_REL]
    assert gate.drift(bound) == [] and gate.drift(stale_xdc) == []


def test_an_unknown_scope_refuses_rather_than_silently_narrowing():
    """A typo must not fall back to the narrow scope and answer anyway."""
    for call in (lambda: gate.scope_files("verfication"),
                 lambda: gate.coverage(gate.bound_bindings()[0][1], "pub"),
                 lambda: gate.historical("")):
        try:
            call()
        except ValueError:
            continue
        raise AssertionError("an unknown scope was accepted")


def test_publication_scope_reproduces_the_hand_named_r0_moved_set():
    """External-ish agreement: R0_MOVED is maintained by hand, one entry per
    commit that moved a source, and includes the XDC because publication scope
    is wider. gate.historical(PUBLICATION_SCOPE) derives the same set from the
    records. Two independently maintained things agreeing is worth more than
    either alone -- and before #421 the gate could not produce this set."""
    records = dict(gate.historical(gate.PUBLICATION_SCOPE))
    key = "fpga/reports/arty/integrated-baseline-2025.1/publication.json"
    assert records[key] == sorted(R0_MOVED)
    narrow = dict(gate.historical())
    assert set(records[key]) - set(narrow.get(key, [])) == {XDC_REL}


def test_the_newest_published_image_moved_only_its_constraints():
    """The gap is not hypothetical. R1 (e0dd329, the player-preview image) was
    built on a branch that did not carry 383f10b, so the published bitstream's
    ONLY divergence from this tree is the constraint file -- and verification
    scope reports that image as covering the tree exactly.

    Stated as a delta rather than an exact list so ordinary RTL churn does not
    turn it red for an unrelated reason; the claim is the delta."""
    wide = set(dict(gate.historical(gate.PUBLICATION_SCOPE)).get(R1, []))
    narrow = set(dict(gate.historical()).get(R1, []))
    assert wide - narrow == {XDC_REL}, (
        "publication scope must see exactly one thing verification scope "
        "cannot for R1: its pin constraints")


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

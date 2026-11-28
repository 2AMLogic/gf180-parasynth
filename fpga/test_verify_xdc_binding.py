"""fpga/verify_xdc_binding.py: red first, on a defect that actually shipped.

The red control here is not synthesised. `383f10b^:fpga/boards/arty-a7-100.xdc`
is the constraint file R0 and R1 were routed against, and its two UART-RX
synchroniser constraints joined the generate block with a slash, matched
nothing, and were DROPPED by Vivado from both published bitstreams (#315). The
bench must fail on those bytes, by name, before its PASS on the tree's bytes
means anything.
"""
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "fpga"))

import build_arty as build          # noqa: E402
import verify_xdc_binding as vxb    # noqa: E402

PRE_315 = "383f10b^"
XDC_REL = "fpga/boards/arty-a7-100.xdc"


@pytest.fixture(scope="module")
def world():
    return (build.XDC.read_text(), (ROOT / "fpga/rtl/arty_a7_top.v").read_text(),
            vxb.rtl_model(build.sources()))


def _pre_315_xdc(tmp_path):
    result = subprocess.run(["git", "-C", str(ROOT), "show", f"{PRE_315}:{XDC_REL}"],
                            capture_output=True, text=True)
    if result.returncode != 0:
        pytest.skip(f"git show {PRE_315} unavailable: {result.stderr.strip()[:80]}")
    path = tmp_path / "pre315.xdc"
    path.write_text(result.stdout)
    return path


def test_red_first_the_constraints_r0_and_r1_shipped_fail_by_name(tmp_path, capsys):
    """THE control. Not an imagined defect: these bytes are in this
    repository's history and two published bitstreams were routed against
    them, with both UART constraints silently dropped."""
    assert vxb.main(["--xdc", str(_pre_315_xdc(tmp_path))]) == 1
    out = capsys.readouterr().out
    assert "hier_separators      FAIL" in out
    assert "XDC line 42" in out and "XDC line 46" in out, \
        "the two constraints #315 names must be the ones reported"
    assert "generate block" in out
    # and only that property: the pre-#315 file was correct in every other way,
    # so a bench that went red across the board would not be discriminating
    for name in vxb.PROPERTIES:
        if name != "hier_separators":
            assert f"{name:<20s} ok" in out


def test_the_bench_passes_against_the_current_tree(capsys):
    """Satisfiability. An unsatisfiable gate is worse than no gate."""
    assert vxb.main([]) == 0
    out = capsys.readouterr().out
    assert "PASS: 0 problem(s)" in out
    assert "FAIL" not in out


def test_every_property_is_moved_by_some_injection(world, capsys):
    """docs/verification-rules.md rule 4, and the reason the matrix exists: a
    property no control can move is decoration wearing the costume of
    coverage. Asserted per property, not merely 'the matrix ran'."""
    xdc_text, wrapper_text, model = world
    clean = vxb.check(xdc_text, wrapper_text, model)
    assert not any(clean.values())
    moved_by = {name: [] for name in vxb.PROPERTIES}
    for name, (_why, mutate) in vxb.INJECTIONS.items():
        found = vxb.check(mutate(xdc_text), wrapper_text, model)
        saw = [p for p in vxb.PROPERTIES if len(found[p]) > len(clean[p])]
        assert saw, f"injection {name} moved no property: it is a no-op"
        for prop in saw:
            moved_by[prop].append(name)
    blind = sorted(p for p, names in moved_by.items() if not names)
    assert not blind, (f"{blind} is moved by no injection -- either add a "
                       "control for it or delete the property")


def test_the_matrix_refuses_rather_than_reporting_when_the_clean_run_fails(monkeypatch, capsys):
    """Condition 1 of the three: a control means nothing if the clean run does
    not pass, so the matrix must say so rather than print rows."""
    real = vxb.check
    monkeypatch.setattr(vxb, "check",
                        lambda *a, **k: dict(real(*a, **k), uart_gate=["injected"]))
    assert vxb.main(["--matrix"]) == 1
    assert "REFUSED: the clean run does not pass" in capsys.readouterr().out


def test_an_injection_that_cannot_be_applied_refuses_rather_than_passing(world):
    """Condition 2: a mutant that does not activate must not read as a caught
    defect. `_sub_once` refuses when its target text is absent."""
    with pytest.raises(vxb.Refused, match="cannot be applied"):
        vxb._sub_once("nothing here", "absent text", "x", "MADE_UP")


def _refuses(name):
    return lambda t: vxb._sub_once(t, "ABSENT TEXT", "x", name)


PLANTED_REFUSAL = ("planted: its target text is absent, so _sub_once refuses",
                   _refuses("PLANTED_REFUSAL"))
PLANTED_NOOP = ("planted: the identity, so it is evaluated and moves nothing",
                lambda t: t)


def _plant(monkeypatch, **planted):
    for name in planted:
        assert len(name) <= max(len(n) for n in vxb.INJECTIONS), \
            f"{name} is longer than every real injection, so it would " \
            "change the column width and the real rows could not be " \
            "byte-identical"
    monkeypatch.setattr(vxb, "INJECTIONS", {**vxb.INJECTIONS, **planted})


def test_an_injection_whose_check_refuses_is_refused_not_failed(world, monkeypatch, capsys):
    """A refusal is not a verdict. When `check` declines to answer for one
    injection, whether that defect behaved as named is UNKNOWN, so the row
    stays REFUSED and the matrix answers 2. It must not be booked beside the
    injections that were measured as 'did not behave' (FAIL, 1)."""
    xdc_text, wrapper_text, model = world
    clean_lines = vxb.matrix(xdc_text, wrapper_text, model)[0].splitlines()
    _plant(monkeypatch, PLANTED_REFUSAL=PLANTED_REFUSAL)
    assert vxb.main(["--matrix"]) == 2
    out_lines = capsys.readouterr().out.splitlines()
    row = next(l for l in out_lines if l.startswith("PLANTED_REFUSAL"))
    assert "REFUSED: injection PLANTED_REFUSAL cannot be applied" in row
    assert "'ABSENT TEXT' is not in the constraint file" in row
    assert out_lines[-1].startswith("COULD NOT DECIDE"), out_lines[-1]
    assert "DID NOT BEHAVE" not in "\n".join(out_lines)
    assert [l for l in out_lines[:-1] if not l.startswith("PLANTED_REFUSAL")] \
        == clean_lines[:-1], "the real rows must be byte-identical"
    _, ok, refused = vxb.matrix(xdc_text, wrapper_text, model)
    assert ok is True and refused is True


def test_a_measured_no_op_outranks_a_refusal(world, monkeypatch, capsys):
    """Injected-bug control for the repair, so that it cannot turn every
    failure into a refusal: an injection that IS evaluated and moves nothing
    is a measured FAIL, and beside a refusal it still decides the exit
    (1, never 2)."""
    _plant(monkeypatch, PLANTED_REFUSAL=PLANTED_REFUSAL, PLANTED_NOOP=PLANTED_NOOP)
    assert vxb.main(["--matrix"]) == 1
    out = capsys.readouterr().out
    assert ("DEFECT: PLANTED_NOOP moved NO property -- it is not a control, "
            "it is a no-op") in out
    assert "REFUSED: injection PLANTED_REFUSAL cannot be applied" in out
    assert out.rstrip().endswith("AN INJECTION DID NOT BEHAVE AS NAMED")


def test_a_matrix_in_which_every_injection_refuses_is_refused(world, monkeypatch, capsys):
    """The edge of the same repair: with nothing evaluated, nothing was
    measured to fail, so the answer is the refusal (2), never a clean 0."""
    monkeypatch.setattr(vxb, "INJECTIONS",
                        {n: (why, _refuses(n)) for n, (why, _) in vxb.INJECTIONS.items()})
    assert vxb.main(["--matrix"]) == 2
    out = capsys.readouterr().out
    assert out.count("REFUSED: injection") == len(vxb.INJECTIONS)
    assert out.rstrip().splitlines()[-1].startswith("COULD NOT DECIDE")
    assert "DID NOT BEHAVE" not in out
    assert "every injected defect moved" not in out
    _, ok, refused = vxb.matrix(*world)
    assert ok is True and refused is True


def test_the_clean_matrix_still_decides_zero(capsys):
    """No-op control for the repair: with nothing refused and nothing a
    no-op, the gate's answer is exactly what it was."""
    assert vxb.main(["--matrix"]) == 0
    out = capsys.readouterr().out
    assert out.rstrip().endswith("every injected defect moved at least one property")
    assert "REFUSED" not in out and "DEFECT" not in out
    assert "COULD NOT DECIDE" not in out


def test_the_generate_dot_is_not_a_regex_metacharacter():
    """The parser distinction the whole #315 property rests on: in a -regexp
    query `\\.` is a generate join and a bare `.` is any-character; in a
    literal path the dot is itself. Parsing one as the other reports the
    pre-#315 file as fine."""
    assert vxb.path_atoms(r".*g_uart\.u_uart/rx_q_reg\[[01]\]") == [
        ("", "g_uart"), (".", "u_uart"), ("/", "rx_q_reg")]
    assert vxb.path_atoms(r".*g_uart/u_uart/rx_q_reg\[0\]/D") == [
        ("", "g_uart"), ("/", "u_uart"), ("/", "rx_q_reg"), ("/", "D")]
    assert vxb.path_atoms("hardware_clock.mmcm/CLKOUT0", regexp=False) == [
        ("", "hardware_clock"), (".", "mmcm"), ("/", "CLKOUT0")]


def test_the_wrapper_port_list_is_read_from_the_rtl_not_restated():
    """ports_declared is only a cross-check if the port set comes from the
    RTL. Twelve ports, fifteen bits, and led is the only vector."""
    ports = vxb.module_ports((ROOT / "fpga/rtl/arty_a7_top.v").read_text(),
                             vxb.WRAPPER)
    assert ports == {"clk_100mhz": 1, "btn_reset": 1, "led": 4,
                     "spi_sck": 1, "spi_mosi": 1, "spi_cs_n": 1, "spi_miso": 1,
                     "uart_rxd": 1, "uart_txd": 1,
                     "i2s_bclk": 1, "i2s_lrclk": 1, "i2s_sdata": 1}
    assert len(vxb.all_bits(ports)) == 15


def test_a_record_is_never_written_for_an_injected_run(tmp_path, capsys):
    """An injected run is not evidence. If it could be written, the one thing
    a consumer cannot check -- that the record came from a clean run -- would
    be exactly the thing an attacker or an accident supplies."""
    assert vxb.main(["--inject", "UART_SLASH_JOIN", "--outdir", str(tmp_path)]) == 2
    assert "never written as a record" in capsys.readouterr().out
    assert not list(tmp_path.iterdir())


def test_the_committed_record_is_what_a_fresh_run_produces(tmp_path):
    """The committed record must be the tool's output on this tree, not a file
    someone edited to match. Re-run it and compare the fields that carry the
    claim; a hand-edit shows up here."""
    assert vxb.main(["--outdir", str(tmp_path)]) == 0
    fresh = json.loads((tmp_path / vxb.RECORD).read_text())
    committed = json.loads(Path(vxb.CONSTRAINT_BY_WRAPPER[vxb.WRAPPER]).read_text())
    for key in ("state", "exit_code", "inject", "wrapper", "scope",
                "xdc_override", "properties", "source_sha256",
                "transcript_sha256"):
        assert fresh[key] == committed[key], f"the committed record's {key} is " \
            "not what fpga/verify_xdc_binding.py produces on this tree"


def test_the_record_hashes_what_it_read_and_only_that():
    """A record that covers files its check never opened is the failure this
    whole issue exists to avoid, with the sign flipped."""
    record = json.loads(Path(vxb.CONSTRAINT_BY_WRAPPER[vxb.WRAPPER]).read_text())
    read = {str(p.relative_to(ROOT)) for p in vxb.read_files(build.XDC)}
    assert set(record["source_sha256"]) == read
    assert XDC_REL in read
    roms = {str(p.relative_to(ROOT)) for p in build.roms()}
    assert not (read & roms), "the constraint bench never opens a ROM"
    for key, digest in record["source_sha256"].items():
        assert build.sha(ROOT / key) == digest


def test_validate_record_refuses_every_way_a_record_can_fail_to_be_evidence(tmp_path):
    real = Path(vxb.CONSTRAINT_BY_WRAPPER[vxb.WRAPPER])
    base = json.loads(real.read_text())
    vxb.validate_record(real)                     # the control's green arm
    cases = {
        "not a clean pass": {"state": "FAIL"},
        "INJECTED run": {"inject": "UART_SLASH_JOIN"},
        "not the tree's constraint file": {"xdc_override": "/tmp/other.xdc"},
        "not 'arty_a7_top'": {"wrapper": "something_else"},
        "nine properties": {"properties": {"ports_declared": []}},
        "claims PASS": {"properties": dict(base["properties"],
                                           uart_gate=["hand edited"])},
        "transcript is absent or changed": {"transcript_sha256": "0" * 64},
    }
    for index, (message, changes) in enumerate(cases.items()):
        directory = tmp_path / f"case{index}"
        directory.mkdir()
        path = directory / vxb.RECORD
        path.write_text(json.dumps(dict(base, **changes), indent=2) + "\n")
        (directory / vxb.TRANSCRIPT).write_bytes(
            real.with_name(vxb.TRANSCRIPT).read_bytes())
        with pytest.raises(vxb.Refused, match=message):
            vxb.validate_record(path)

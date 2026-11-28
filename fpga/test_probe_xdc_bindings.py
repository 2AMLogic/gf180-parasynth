"""tools/probe_xdc_bindings.py must refuse, not answer, off the build box.

The probe is #315's assertion on a REAL netlist: it opens the routed R1
checkpoint in Vivado, so it can only run where Vivado and that checkpoint
exist -- the build box, with the digest recorded at
`fpga/reports/arty/r1-player-preview-2025.1/dsp-dpreg-evidence/routed_dcp.sha256`.
Everywhere else the honest outcome is its documented refusal (exit 2, "the
apparatus cannot run"), and that is what a host without the box will ever see
of it -- so the refusal itself is what these tests pin (#1106). A probe that
answered without its netlist, or reported the missing apparatus as a FAIL
(exit 1, evidence of a defect), would look exactly like data.
"""
import importlib.util
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "tools" / "probe_xdc_bindings.py"


def test_refuses_without_the_apparatus(tmp_path):
    r = subprocess.run(
        [sys.executable, str(PROBE),
         "--dcp", str(tmp_path / "absent.dcp"),
         "--dcp-sha256", "0" * 64,
         "--out", str(tmp_path / "out"),
         "--vivado", "vivado-that-resolves-nowhere-1106"],
        capture_output=True, text=True, cwd=str(ROOT))
    assert r.returncode == 2, (r.returncode, r.stdout, r.stderr)
    # the refusal must NAME the precondition, and must not leave a
    # verdict-shaped artefact behind (nothing was measured)
    assert "REFUSED" in r.stdout and "Vivado" in r.stdout, r.stdout
    assert not (tmp_path / "out" / "probe.json").exists()


def _probe_module(monkeypatch, vivado="/nonexistent-vivado"):
    monkeypatch.setattr("shutil.which", lambda name: vivado if name == vivado else None)
    spec = importlib.util.spec_from_file_location("probe_xdc_bindings", PROBE)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_the_digest_guard_refuses_a_present_but_unnamed_checkpoint(tmp_path, monkeypatch):
    """Vivado resolves, a file is there, but it is not the named checkpoint:
    the digest assertion must refuse before Vivado is ever asked to open it.

    Without this leg the guard is defeatable by any .dcp-shaped file -- the
    probe would open whatever it was handed and report on a netlist nobody
    named (verification-rules rule 8).
    """
    m = _probe_module(monkeypatch, vivado="/nonexistent-vivado")
    fake = tmp_path / "not-the-r1-checkpoint.dcp"
    fake.write_bytes(b"not a checkpoint")
    rc = m.main(["--dcp", str(fake), "--dcp-sha256", "1" * 64,
                 "--out", str(tmp_path / "out"), "--vivado", "/nonexistent-vivado"])
    assert rc == 2
    assert not (tmp_path / "out" / "probe.json").exists()


def test_a_check_that_cannot_read_its_xdc_ends_the_probe_refused(tmp_path, monkeypatch, capsys):
    """#1922. The tree's XDC gains an exception kind expected_exceptions() does
    not model: tcl_assertions still emits, Vivado still runs -- three times, on
    the build box -- and check_route refuses on the way back. The probe must say
    REFUSED (exit 2) and name the reason: not fold the refusal into a problems
    list and print FAIL, and not leave a verdict-shaped probe.json behind."""
    sys.path.insert(0, str(ROOT / "fpga"))
    import xdc_bindings as xb
    clean = xb.XDC.read_text()
    m = _probe_module(monkeypatch)
    xdc = tmp_path / "arty-a7-100.xdc"
    xdc.write_text(clean + "\nset_max_delay 5.0 -from [get_ports x] -to [get_ports y]\n")
    monkeypatch.setattr(xb, "XDC", xdc)
    # what a Vivado run on a netlist that bound every query would leave behind
    report = "".join(f"MATCH\t{n}\t{kind}\t{exp}\t{exp}\t{exp if a else '-'}\t{xb.query_id(rx)}\n"
                     for n, kind, rx, exp, a in xb.object_queries(clean)) + "END\t0\n" \
        + "".join(f"CHECK\t{c}\t1\tfixture\n" for c in xb.ROUTE_CHECKS) + "ROUTE_END\t0\n"
    table = "\n".join(["Exceptions Report", "", "--------  ----  -------  --  -----  ----  ------"]
                      + [f"{i}  {f}  *  {t}  false  false"
                         for i, (f, t) in enumerate(xb.expected_exceptions(clean), 2)]) + "\n"
    real_run = subprocess.run

    def vivado(argv, **kwargs):
        if argv[0] == "git":                      # R1's XDC still comes from the repository
            return real_run(argv, **kwargs)
        d = Path(argv[argv.index("-source") + 1]).parent
        (d / xb.REPORT).write_text(report)
        (d / xb.EXCEPTIONS).write_text(table)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", vivado)
    dcp = tmp_path / "r1-routed.dcp"
    dcp.write_bytes(b"the checkpoint, bound by digest")
    rc = m.main(["--dcp", str(dcp), "--dcp-sha256", m.sha(dcp), "--out", str(tmp_path / "out"),
                 "--vivado", "/nonexistent-vivado"])
    out = capsys.readouterr().out
    assert rc == 2, out
    assert "probe_xdc_bindings: REFUSED --" in out and "does not model" in out, out
    assert "FAIL" not in out and "PASS" not in out, out
    assert not (tmp_path / "out" / "probe.json").exists()

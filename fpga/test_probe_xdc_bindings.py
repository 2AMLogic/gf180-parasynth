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

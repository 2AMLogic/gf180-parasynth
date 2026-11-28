"""Offline fixture replays for the disposition table in docs/tier2-empty-records.md.

Run with python -m pytest -qs tools/probes/test_tier2_empty_records.py. These exercise
the consumers of audio, build reports and tool responses without running EDA or
loading a licensed plugin.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from unittest.mock import Mock

import numpy as np
import pytest
from scipy.io import wavfile

ROOT = Path(__file__).resolve().parents[2]
for subdir in ("tools", "model", "fpga", "pnr/klt/ladder_dp"):
    sys.path.insert(0, str(ROOT / subdir))

import build_arty_pads as pads
import build_arty_sd as sd
import measure_mono_m1a_reference as m1a
import refprofile
import r0_capture
import test_r0_capture as capture_fixtures
import test_trial as trial_fixtures
import trial
import run_case
import run_klt
import test_build_arty_pads as pads_fixtures
import test_run_klt as klt_fixtures
from test_sensitivity import root as sensitivity_root
from test_refprofile import profile  # pytest's one-clip cache fixture


def test_capture_requires_takes(tmp_path):
    clean = r0_capture.synth_session(tmp_path / "synthetic")
    bundle = capture_fixtures._as_real(clean, tmp_path)
    out = tmp_path / "out"
    args = ["analyse", "--bundle", str(bundle), "--out", str(out)]
    assert r0_capture.main(args) == 0
    capture_fixtures._edit(bundle, lambda session: session.update(takes=[]))
    assert r0_capture.main(args) == 2
    record = json.loads((out / "analysis.json").read_text())
    assert "session.takes missing or empty" in record["reasons"][0]


def test_sensitivity_requires_records(sensitivity_root):
    path = sensitivity_root / "docs/sensitivity/registry.json"
    registry = json.loads(path.read_text())
    tool_dir = sensitivity_root / "tools"
    tool_dir.mkdir(exist_ok=True)
    # finite_number.py (imported by sensitivity.py since #1994) needs numpy; the
    # subprocess runs under sys.executable, the same interpreter as this test.
    for name in ("sensitivity.py", "refusal.py", "finite_number.py"):
        shutil.copyfile(ROOT / "tools" / name, tool_dir / name)
    cmd = [sys.executable, str(tool_dir / "sensitivity.py"), "check"]
    assert subprocess.run(cmd).returncode == 0
    registry["records"] = []
    path.write_text(json.dumps(registry))
    empty = subprocess.run(cmd, capture_output=True, text=True)
    assert empty.returncode == 2 and "no records listed" in empty.stdout


def test_trial_requires_receipts(tmp_path, monkeypatch):
    repo = trial_fixtures.Repo(tmp_path)
    repo.child(trial_fixtures.ok_deadline())
    repo.child(trial_fixtures.late_control(), role="control")
    path, receipt = repo.run()
    assert receipt["verdict"] == trial.PASS
    # check-all normally resolves relative paths in the shipping repository.
    check = trial.check_receipt
    monkeypatch.setattr(trial, "check_receipt", lambda p: check(p, root=tmp_path))
    args = ["check-all", str(path)]
    assert trial.main(args) == 0
    (path / "receipt.json").unlink()
    assert trial.main(args) == 1


def test_case_selection_before_verdict(tmp_path, monkeypatch):
    # Keep the score and selection live. Replay the one measured filter case.
    record = ROOT / "docs/scorecard/results/F1A.json"
    calls = []

    def replay(case, *args, **kwargs):
        calls.append(case["case_id"])
        return json.loads(record.read_text())

    monkeypatch.setattr(run_case, "run_case", replay)
    opts = ["--dry-run", "--no-audio", "--results", str(tmp_path)]
    assert run_case.main(["F1A", *opts]) == 0
    with pytest.raises(SystemExit) as err:
        run_case.main(["--family", "absent family", *opts])
    assert err.value.code == 2 and calls == ["F1A"]


def test_profile_clip_population(profile):
    record, _ = profile
    assert refprofile.main([]) == 0
    record["clips"] = {}
    refprofile.PROFILE_JSON.write_text(json.dumps(record))
    assert refprofile.main([]) == 2


def test_m1a_render_requires_measurements(monkeypatch):
    directory = ROOT / "docs/scorecard/mono-m1a-miniv3"
    repeat = json.loads((directory / "manifest.json").read_text())["renders"][0]
    rate, audio = wavfile.read(directory / repeat["file"])
    assert rate == m1a.ref.SR
    # Replay the host boundary. All patch writes, name/value readbacks and
    # audio checks in render() remain live, including both pin sweeps.
    rig = object.__new__(m1a.ref.rr.MiniV3Rig)
    rig.p, rig.pb, rig.eng = Mock(), Mock(), Mock()
    names = {i: name for i, _, name, _ in rig.PINS}
    values = {i: want for i, _, _, want in rig.PINS}
    for row in repeat["apparatus"]["before"].values():
        names[row["index"]] = row["name"]
    wave_index, wave_name = m1a.ref.WAVE_PARAM
    names[wave_index] = wave_name
    enumerated = {45: "8'", 46: "4'", 49: "sawtooth",
                  wave_index: m1a.ref.WAVE_SETTINGS["saw"][1]}
    rig.p.set_parameter.side_effect = lambda i, v: values.update({i: str(v)})
    rig.p.get_parameter_name.side_effect = names.__getitem__
    rig.p.get_parameter_text.side_effect = lambda i: enumerated.get(i, values.get(i))
    rig.p.get_latency_samples.return_value = 0
    rig.eng.get_audio.return_value = [audio]
    monkeypatch.setattr(m1a.ref.rr, "MiniV3Rig", lambda **kw: rig)
    _, apparatus = m1a.render()
    print("M1A patch readbacks before/after:", len(apparatus["before"]), len(apparatus["after"]))
    assert apparatus["before"] == apparatus["after"] and apparatus["before"]
    rig.eng.get_audio.return_value = [np.array([])]
    with pytest.raises(m1a.ref.Refused, match="truncated or non-finite"):
        m1a.render()


def test_pads_build_counts_reported_inputs(tmp_path):
    verification = tmp_path / "verification"
    verification.mkdir()
    sources = pads.sources() + pads.roms()
    pads_fixtures._records(verification, sources)
    for path in verification.glob("*/verification.json"):
        rec = json.loads(path.read_text())
        rec["source_sha256"] = {pads._key(p): pads.sha(p) for p in sources}
        path.write_text(json.dumps(rec))

    reports = ROOT / "fpga/reports/arty/pads-demo-2025.1"
    # Replay the archived reports through main's post-Vivado verdict. The
    # two placeholder binary outputs only stand in for Vivado's file writes.
    supplied = tmp_path / "pads_inputs.rpt"
    shutil.copyfile(reports / pads.PADS_REPORT, supplied)
    vivado = tmp_path / "vivado"
    vivado.write_text(f"""#!{sys.executable}
import sys, shutil
from pathlib import Path
if '-version' in sys.argv:
    print('report replay')
    sys.exit(0)
out = Path(sys.argv[sys.argv.index('-log') + 1]).parent
for p in Path({str(reports)!r}).glob('*.rpt'):
    shutil.copyfile(p, out / p.name)
shutil.copyfile({str(supplied)!r}, out / 'pads_inputs.rpt')
for name in ('arty_pads.bit', 'routed.dcp'):
    (out / name).write_bytes(b'fixture')
""")
    vivado.chmod(0o755)
    build = tmp_path / "build"
    args = ["--vivado", str(vivado), "--out", str(build), "--verification", str(verification)]
    assert pads.main(args) == 0
    supplied.write_text("END\t0\n")
    assert pads.main(args) == 1
    rec = json.loads((build / "report.json").read_text())
    assert all(any(f"input {p} is absent" in msg for msg in rec["problems"]) for p in pads.INPUTS)


def test_sd_implementation_requires_pins(tmp_path):
    reports = ROOT / "fpga/reports/arty/sd-demo-2025.1"
    build = tmp_path / "build"
    shutil.copytree(reports, build)
    archived = json.loads((build / "report.json").read_text())["source_sha256"]
    for p in (sd.build.XDC, sd.SD_XDC):
        rel = p.relative_to(ROOT)
        assert sd.build.sha(p) == archived[str(rel)]
        dest = build / "inputs" / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, dest)
    (build / "vivado.log").write_text(
        "WARNING: [Synth 8-3917] design arty_a7_sd_top has port dac_sck driven by constant 0\n")
    assert sd.check_implementation(build)["placed_pins"] == sd.DIRECT_PLUG_PINS
    (build / sd.IO_REPORT).write_text("")
    with pytest.raises(ValueError, match="placed pins"):
        sd.check_implementation(build)


def test_klt_driver_cannot_reach_its_verdict_with_zero_steps(tmp_path, monkeypatch):
    here = klt_fixtures._mirrored_here(tmp_path)
    pdk = klt_fixtures._fake_pdk(tmp_path)
    netlist = tmp_path / "synth.v"
    netlist.write_text(klt_fixtures._CURRENT_NETLIST)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ["PATH"])
    for name in ("yosys", "openroad"):
        (bindir / name).symlink_to(sys.executable)  # presence checks only
    klt = bindir / "klt"
    klt.write_text(f"""#!{sys.executable}
import json, sys
from pathlib import Path
if sys.argv[1] == 'version':
    print('fixture')
elif sys.argv[1] == 'synthesize':
    print(json.dumps({{"netlist_path": {{"path": {str(netlist)!r}}}}}))
else:
    print(Path({str(run_klt.HERE / 'par_response.json')!r}).read_text())
""")
    klt.chmod(0o755)
    args = ["--here", str(here), "--pdk-root", str(pdk), "--pdk", klt_fixtures.PDK]
    assert run_klt.main(args) == 0
    log = (here / "run.log").read_text()
    assert "[synthesize] exit=0" in log and "[place-and-route] exit=0" in log
    (here / "par_request.json").write_text("{}")
    assert run_klt.main(args) == run_klt.EXIT_REFUSED

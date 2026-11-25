"""Process-level controls for refusals at the M5 sound measurement boundaries."""
import importlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCORECARD = Path("docs/scorecard")


@pytest.fixture
def sandbox(tmp_path):
    # The scripts resolve their frozen inputs relative to __file__. Only the
    # manifests are mutable. Read the large reference WAVs from the checkout.
    for directory in ("tools", "model", "audition"):
        shutil.copytree(ROOT / directory, tmp_path / directory,
                        ignore=shutil.ignore_patterns("__pycache__"))
    for case in ("m5a", "m5b"):
        directory = SCORECARD / f"mono-{case}-miniv3"
        (tmp_path / directory).mkdir(parents=True)
        manifest = directory / "manifest.json"
        shutil.copyfile(ROOT / manifest, tmp_path / manifest)
        wav = directory / json.loads((ROOT / manifest).read_text())["audio"]["file"]
        (tmp_path / wav).symlink_to(ROOT / wav)
    return tmp_path


def run_cli(root, module, *args):
    return subprocess.run([sys.executable, str(root / "tools" / f"{module}.py"), *args],
                          cwd=root, capture_output=True, text=True, timeout=300)


def assert_refused(proc, reason):
    assert proc.returncode == 2, (proc.stdout, proc.stderr)
    assert f"REFUSED: {reason}" in proc.stdout.splitlines()
    assert "Traceback" not in proc.stdout + proc.stderr


def corrupt_audio_hash(sandbox, case):
    path = sandbox / SCORECARD / f"mono-{case.lower()}-miniv3/manifest.json"
    manifest = json.loads(path.read_text())
    manifest["audio"]["sha256"] = "0" * 64
    path.write_text(json.dumps(manifest))


@pytest.mark.parametrize(("module", "case", "args"), [
    # Operating level must finish M5A. Its existing per-drive handler already
    # records M5A refusals, so corrupting M5A would miss this escape.
    ("measure_m5_operating_level", "M5B", ("--drives", "0.75")),
    ("measure_m5_pulse_cutoff", "M5A", ()),
    ("measure_m5a_filter_headroom", "M5A", ()),
    ("measure_m5a_filter_oversample", "M5A", ()),
    ("measure_mono_pulse_2x", "M5A", ("--case", "M5A")),
])
def test_frozen_audio_mismatch_exits_refused(sandbox, module, case, args):
    corrupt_audio_hash(sandbox, case)
    proc = run_cli(sandbox, module, *args, "--out", "result")
    assert_refused(proc, f"frozen {case} Mini V3 audio hash mismatch")


@pytest.mark.parametrize("module", ["measure_m5_operating_level", "measure_m5_saw_drive"])
def test_m5a_refusal_is_still_recorded_per_drive(sandbox, module):
    corrupt_audio_hash(sandbox, "M5A")
    proc = run_cli(sandbox, module, "--drives", "0.75", "--out", "result.json")
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    rows = json.loads((sandbox / "result.json").read_text())["rows"]
    assert rows["0.75"]["refused"] == "frozen M5A Mini V3 audio hash mismatch"


@pytest.mark.parametrize("module", ["measure_m5_operating_level", "measure_m5_saw_drive"])
def test_probe_refusal_at_calibration_reaches_cli(module, monkeypatch, tmp_path, capsys):
    import mono_artifact_probe as probe
    import mono_m5a_score as score
    import refusal

    # Both CLIs freeze the note, engine choices, drive budget and stationary
    # patch. No CLI or reference-file input sets probe resonance. Inject the
    # out-of-domain patch at calibration and exercise the real render guard.
    assert issubclass(probe.Refused, refusal.Refused)
    assert not issubclass(probe.Refused, score.Refused)
    held_patch = probe.held_patch
    monkeypatch.setattr(probe, "held_patch", lambda **kw: held_patch(q=1.0, **kw))
    tool = importlib.import_module(module)
    assert tool.main(["--drives", "0.5", "--out", str(tmp_path / "result")]) == 2
    assert ("REFUSED: resonance at or above the self-oscillation boundary is out of domain"
            in capsys.readouterr().out.splitlines())


@pytest.mark.parametrize(("option", "reason"), [
    ("--recompare-report", "recomparison requires an m5a-filter-headroom-v1 report"),
    ("--reuse-offline-report", "reuse report schema, reference, or pulse set does not match"),
])
def test_headroom_refuses_wrong_report_schema(sandbox, option, reason):
    report = sandbox / "report.json"
    report.write_text("{}")
    assert_refused(run_cli(sandbox, "measure_m5a_filter_headroom", option, report), reason)
    assert report.read_text() == "{}"


def test_pulse_experiment_refuses_conflicting_gain(sandbox):
    proc = run_cli(sandbox, "measure_mono_pulse_2x", "--case", "M5B", "--rect-gain-q15", "1")
    assert_refused(proc, "this tree defines its own rectangle gain; an override would "
                         "measure a candidate the model does not build")


@pytest.mark.parametrize(("damage", "reason"), [
    ("hash", "attack-context reference audio hash mismatch"),
    ("timeline", "attack-context reference MIDI timeline differs"),
    ("missing-renders", "missing three independent renders: saw/delayed84_at4p1"),
    ("summary", "attack-context reference summary is inconsistent"),
    ("release", "model envelope incomplete: saw/delayed84_at4p1"),
])
def test_attack_context_refuses_damaged_evidence(sandbox, damage, reason):
    directory = SCORECARD / "mono-attack-context"
    (sandbox / directory).mkdir()
    path = directory / "report.json"
    report = json.loads((ROOT / path).read_text())
    for row in report["renders"]:
        wav = directory / row["wav"]
        (sandbox / wav).symlink_to(ROOT / wav)
    if damage == "hash":
        report["renders"][0]["sha256"] = "0" * 64
    elif damage == "timeline":
        report["renders"][0]["events"] = []
    elif damage == "missing-renders":
        report["renders"] = []
    elif damage == "summary":
        report["summary"] = {}
    elif damage == "release":
        path_m5a = sandbox / SCORECARD / "mono-m5a-miniv3/manifest.json"
        manifest = json.loads(path_m5a.read_text())
        first = manifest["timeline"]["segments"][0]["measurements"][0]
        first["envelope"]["release_t20_ms"] = 100_000
        path_m5a.write_text(json.dumps(manifest))
    (sandbox / path).write_text(json.dumps(report))
    assert_refused(run_cli(sandbox, "compare_mono_attack_context"), reason)


def test_unexpected_json_error_still_exits_one(sandbox):
    path = sandbox / SCORECARD / "mono-m5a-miniv3/manifest.json"
    path.write_text("{")
    proc = run_cli(sandbox, "measure_m5_pulse_cutoff", "--out", "result")
    assert proc.returncode == 1
    assert "Traceback" in proc.stderr and "json.decoder.JSONDecodeError:" in proc.stderr
    assert "REFUSED:" not in proc.stdout + proc.stderr

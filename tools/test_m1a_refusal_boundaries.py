#!/usr/bin/env python3
"""Known-answer exit-status controls for the 6 M1A / phase-cycle CLI tools
(#1265, split from #1202).

THE DEFECT, AS MEASURED ON origin/main @ b4b05ed7 (2026-11-25, red-first run):
`mono_m1a_score.Refused` aliases `mono_m5a_score.Refused`. Four M1A /
phase-cycle CLIs (`diagnose_m1a_harmonic_phase`, `measure_m1a_drive`,
`phase_aware_m1a`, `analyse_m1a_phase_cycle`) define a local subclass of a
parent `Refused`; three of them guard `main()` with `except Refused` (missing
parent and sibling classes), while `diagnose_m1a_harmonic_phase`,
`measure_m1a_volume_mapping` and `regen_m1a_units` have no CLI-boundary
refusal handler at all. Setting `valid` to `false` in the M1A reference
manifest raises `mono_m5a_score.Refused("M1A reference manifest is not
qualified")` inside `mono_m1a_score.load_reference()`, which escaped all 6
CLIs as an uncaught exit-1 traceback instead of `exit_contract.REFUSED` (2).

RULE-8 NARROW-HANDLER CONTROL (docs/verification-rules.md): widening each CLI
boundary to `except refusal.Refused` must never become `except Exception`.
Setting `renders` to `null` in the sandbox's M1A manifest passes the JSON and
suite/qualification checks in `load_reference()` and raises `TypeError` on
`[*manifest["renders"], ...]` -- an execution error inside the same `try`
block, which must continue to exit 1 with a traceback and without printing
`REFUSED` or writing `analysis-refused.json`.
"""
from __future__ import annotations

import copy
import json
import os
import pathlib
import shutil
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))
if str(ROOT / "model") not in sys.path:
    sys.path.insert(0, str(ROOT / "model"))

import exit_contract                                     # noqa: E402
import refusal                                           # noqa: E402

CLI_MODULES = (
    ("diagnose_m1a_harmonic_phase.py",
     "docs/scorecard/mono-m1a-miniv3/harmonic-diagnosis/report.json"),
    ("measure_m1a_drive.py",
     "docs/scorecard/mono-m1a-miniv3/drive-experiment/report.json"),
    ("measure_m1a_volume_mapping.py",
     "docs/scorecard/mono-m1a-miniv3/volume-mapping/report.json"),
    ("phase_aware_m1a.py",
     "docs/scorecard/mono-m1a-miniv3/phase-aware-337/report.json"),
    ("analyse_m1a_phase_cycle.py",
     "docs/scorecard/mono-m1a-miniv3/phase-cycle-capture/report.json"),
    ("regen_m1a_units.py",
     "docs/scorecard/results/M1A.json"),
)


def _sandbox(tmp_path: pathlib.Path) -> pathlib.Path:
    """Private copy of tools/, model/, audition/ and the M1A scorecard inputs.

    Every directory and JSON file is a real copy under `tmp_path` so no write
    or atomic replace can touch `ROOT`; read-only reference/capture WAVs are
    symlinked to keep all 12 subprocess runs fast without mutating repo state.
    """
    box = tmp_path / "sandbox"
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", "test_*.py")
    for tree in ("tools", "model", "audition"):
        shutil.copytree(ROOT / tree, box / tree, ignore=ignore)

    (box / "docs/scorecard/results").mkdir(parents=True)
    shutil.copy2(ROOT / "docs/scorecard/cases.csv",
                 box / "docs/scorecard/cases.csv")
    shutil.copy2(ROOT / "docs/scorecard/results/M1A.json",
                 box / "docs/scorecard/results/M1A.json")

    src_m1a = ROOT / "docs/scorecard/mono-m1a-miniv3"
    dst_m1a = box / "docs/scorecard/mono-m1a-miniv3"
    for dirpath, dirnames, filenames in os.walk(src_m1a):
        rel = pathlib.Path(dirpath).relative_to(src_m1a)
        target_dir = dst_m1a / rel
        target_dir.mkdir(parents=True, exist_ok=True)
        for name in filenames:
            src_file = pathlib.Path(dirpath) / name
            dst_file = target_dir / name
            if name.endswith(".json") or name == "m1a-model.wav":
                shutil.copy2(src_file, dst_file)
            elif name.endswith(".wav") and rel != pathlib.Path("volume-mapping"):
                os.symlink(src_file, dst_file)
    return box


def _run_cli(box: pathlib.Path, script: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(box / "tools" / script)],
        capture_output=True, text=True, timeout=180, cwd=str(box),
    )


def _mutate_manifest(box: pathlib.Path, **updates) -> None:
    manifest_path = box / "docs/scorecard/mono-m1a-miniv3/manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest.update(updates)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")


# ---------------------------------------------------------------------------
# Class relations behind the cross-module escapes
# ---------------------------------------------------------------------------
def test_m1a_refused_classes_are_siblings_or_ancestors_under_refusal_base():
    import analyse_m1a_phase_cycle as ap
    import diagnose_m1a_harmonic_phase as d
    import measure_m1a_drive as dx
    import mono_m1a_score as bass
    import phase_aware_m1a as pa

    classes = (bass.Refused, d.Refused, dx.Refused, pa.Refused, ap.Refused)
    for klass in classes:
        assert issubclass(klass, refusal.Refused), klass

    # Local `except Refused` in each subclass module missed its parent/siblings:
    assert not issubclass(bass.Refused, dx.Refused)
    assert not issubclass(ap.Refused, dx.Refused)
    assert not issubclass(bass.Refused, pa.Refused)
    assert not issubclass(d.Refused, pa.Refused)
    assert not issubclass(bass.Refused, ap.Refused)
    assert not issubclass(d.Refused, ap.Refused)


# ---------------------------------------------------------------------------
# Red-first subprocess controls: cross-module Refused -> exit 2 (REFUSED)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("script,report_rel", CLI_MODULES)
def test_unqualified_reference_manifest_exits_refused_2(tmp_path, script, report_rel):
    box = _sandbox(tmp_path)
    report_path = box / report_rel
    before = report_path.read_bytes()
    _mutate_manifest(box, valid=False)

    p = _run_cli(box, script)
    assert p.returncode == exit_contract.REFUSED, (
        script, p.returncode, p.stdout[-300:], p.stderr[-400:])
    assert "REFUSED:" in p.stdout, (script, p.stdout[-400:])
    assert "M1A reference manifest is not qualified" in p.stdout, (
        script, p.stdout[-400:])
    assert "Traceback" not in p.stderr and "Traceback" not in p.stdout, (
        script, p.stderr[-400:])
    assert report_path.read_bytes() == before, (
        f"{script} modified {report_rel} on a refused run")

    if script == "analyse_m1a_phase_cycle.py":
        refused_json = (
            box / "docs/scorecard/mono-m1a-miniv3/phase-cycle-capture/analysis-refused.json"
        )
        assert refused_json.exists(), "analyse_m1a_phase_cycle must write analysis-refused.json"
        assert json.loads(refused_json.read_text()) == {
            "state": "REFUSED",
            "reason": "M1A reference manifest is not qualified",
        }


# ---------------------------------------------------------------------------
# Rule-8 narrow-handler controls: non-Refused errors still exit 1
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("script,report_rel", CLI_MODULES)
def test_non_refused_exception_is_not_swallowed_by_cli_boundary(
        tmp_path, script, report_rel):
    box = _sandbox(tmp_path)
    report_path = box / report_rel
    before = report_path.read_bytes()
    # `renders: null` passes suite/qualification checks in load_reference() and
    # raises TypeError (`'NoneType' object is not iterable`), which load_reference
    # does NOT wrap into Refused -- exercising the CLI's own except clause.
    _mutate_manifest(box, renders=None)

    p = _run_cli(box, script)
    assert p.returncode == 1, (script, p.returncode, p.stdout[-300:], p.stderr[-400:])
    assert "Traceback" in p.stderr and "TypeError" in p.stderr, (
        script, p.stderr[-400:])
    assert "REFUSED" not in p.stdout, (
        f"{script} relabelled a TypeError as REFUSED: {p.stdout[-300:]}")
    assert report_path.read_bytes() == before

    if script == "analyse_m1a_phase_cycle.py":
        refused_json = (
            box / "docs/scorecard/mono-m1a-miniv3/phase-cycle-capture/analysis-refused.json"
        )
        assert not refused_json.exists(), (
            "analyse_m1a_phase_cycle must not write analysis-refused.json on TypeError")


# ---------------------------------------------------------------------------
# regen_m1a_units.py's existing exit-1 numeric and audio-hash gates stay intact
# ---------------------------------------------------------------------------
def test_regen_m1a_units_preserves_exit_1_numeric_and_hash_gates(
        tmp_path, monkeypatch, capsys):
    import regen_m1a_units as regen

    record_copy = tmp_path / "M1A.json"
    shutil.copy2(ROOT / "docs/scorecard/results/M1A.json", record_copy)
    original_bytes = record_copy.read_bytes()
    old = json.loads(record_copy.read_text())
    monkeypatch.setattr(regen, "RECORD", record_copy)

    # 1. Published metric drift -> exit 1, no write
    moved = copy.deepcopy(old)
    first_metric = next(iter(moved["metrics"].values()))
    first_metric["value"] = float(first_metric["value"]) + 0.25
    monkeypatch.setattr(regen.bass, "run", lambda *a, **k: moved)
    assert regen.main() == 1
    assert "published numbers moved" in capsys.readouterr().err
    assert record_copy.read_bytes() == original_bytes

    # 2. Cached audio hash mismatch -> exit 1, no write
    rehashed = copy.deepcopy(old)
    rehashed["diagnostics"]["model_audio_sha256"] = "0" * 64
    monkeypatch.setattr(regen.bass, "run", lambda *a, **k: rehashed)
    assert regen.main() == 1
    assert "reanalysed audio hash differs" in capsys.readouterr().err
    assert record_copy.read_bytes() == original_bytes


# ---------------------------------------------------------------------------
# #1862: a missing or non-RIFF committed-model WAV exits REFUSED (2), naming it
# ---------------------------------------------------------------------------
MODEL_WAV_REL = "docs/scorecard/mono-m1a-miniv3/m1a-model.wav"
MODEL_WAV_READERS = ("measure_m1a_drive.py", "phase_aware_m1a.py", "analyse_m1a_phase_cycle.py")


@pytest.mark.parametrize("blob", [None, b"not a WAV"], ids=["missing", "non-RIFF"])
@pytest.mark.parametrize("script", MODEL_WAV_READERS)
def test_missing_or_non_riff_model_wav_exits_refused_2(tmp_path, script, blob):
    """Each CLI reads `m1a-model.wav` under `except refusal.Refused` with no
    earlier guard on it (#1848 section B), so a missing or non-RIFF file escaped
    as exit 1 -- the status `run_all` renders as a RESULT."""
    box = _sandbox(tmp_path)
    report_rel = dict(CLI_MODULES)[script]
    report_path = box / report_rel
    before = report_path.read_bytes()
    model_wav = box / MODEL_WAV_REL
    if blob is None:
        model_wav.unlink()
    else:
        model_wav.write_bytes(blob)

    p = _run_cli(box, script)
    assert p.returncode == exit_contract.REFUSED, (
        script, blob, p.returncode, p.stdout[-300:], p.stderr[-400:])
    assert "REFUSED:" in p.stdout and "m1a-model.wav" in p.stdout, (script, p.stdout[-400:])
    assert "Traceback" not in p.stderr and "Traceback" not in p.stdout, (
        script, p.stderr[-400:])
    assert report_path.read_bytes() == before, (
        f"{script} modified {report_rel} on a refused run")

    if script == "analyse_m1a_phase_cycle.py":
        refused_json = (
            box / "docs/scorecard/mono-m1a-miniv3/phase-cycle-capture/analysis-refused.json"
        )
        refused = json.loads(refused_json.read_text())
        assert refused["state"] == "REFUSED" and "m1a-model.wav" in refused["reason"]

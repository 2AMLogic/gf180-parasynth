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
    # #1570: both carried a private copy of the loader that raised RuntimeError.
    ("measure_m5a_pulse_duty", "M5A", ("--shapes", "pulse29")),
    ("measure_m5a_signal_path", "M5A", ("--cutoff", "14073", "--drive", "0.75")),
])
def test_frozen_audio_mismatch_exits_refused(sandbox, module, case, args):
    corrupt_audio_hash(sandbox, case)
    proc = run_cli(sandbox, module, *args, "--out", "result")
    assert_refused(proc, f"frozen {case} Mini V3 audio hash mismatch")


MINI_V4_IDENTITY = {"name": "Mini V4", "version": "4.0.4.6366"}


@pytest.mark.parametrize(("damage", "reason"), [
    ("suite", "reference-suite mix"),
    ("case", "frozen manifest identifies as 'M5B', expected M5A"),
])
@pytest.mark.parametrize(("module", "args", "prefix"), [
    ("measure_m5a_pulse_duty", ("--shapes", "pulse29"), "REFUSED: "),
    ("measure_m5a_signal_path", ("--cutoff", "14073", "--drive", "0.75"), "REFUSED: "),
    ("measure_m5a_attack_bias", (), "measure_m5a_attack_bias: REFUSED -- "),
])
def test_foreign_manifest_exits_refused_and_writes_no_report(
        sandbox, module, args, prefix, damage, reason):
    # #1570: none of the three checked the suite (#1050) or the case id, so a
    # Mini V4 record or an M5B manifest in the M5A slot produced a valid report.
    path = sandbox / SCORECARD / "mono-m5a-miniv3/manifest.json"
    manifest = json.loads(path.read_text())
    if damage == "suite":
        manifest["identity"] = {**manifest["identity"], **MINI_V4_IDENTITY}
    else:
        manifest["case_id"] = "M5B"
    path.write_text(json.dumps(manifest))
    proc = run_cli(sandbox, module, *args, "--out", "result")
    assert proc.returncode == 2, (proc.stdout, proc.stderr)
    assert any(line.startswith(f"{prefix}{reason}") for line in proc.stdout.splitlines()), proc.stdout
    assert "Traceback" not in proc.stdout + proc.stderr
    assert not (sandbox / "result").exists()


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


@pytest.mark.parametrize("q15", [0, -1, 32768])
def test_pulse_experiment_refuses_out_of_range_gain(sandbox, q15):
    proc = run_cli(sandbox, "measure_mono_pulse_2x", "--case", "M5A",
                   "--rect-gain-q15", str(q15), "--out", "result")
    assert_refused(proc, f"rectangle gain {q15} outside Q0.15")
    assert not (sandbox / "result").exists()


@pytest.mark.parametrize("q15", [1, 32767])
def test_pulse_experiment_in_range_gain_clears_the_range_guard(sandbox, q15):
    # The Q0.15 endpoints clear the range guard and reach the model's own-gain
    # refusal, which fires only after set_rect_gain has returned.
    proc = run_cli(sandbox, "measure_mono_pulse_2x", "--case", "M5B",
                   "--rect-gain-q15", str(q15), "--out", "result")
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


# ---- #1692 ---------------------------------------------------------------
# measure_point answers a silent note with verdict FAIL-ACTIVITY and NO stages,
# and a stage it could not split with {"refused": "<text>"}. A reader that
# indexes row["stages"][stage][field] shows a KeyError: an implementation error
# where a stated refusal belongs. No frozen CLI input reaches such a row today,
# so each reader is driven by serving it the row under test.


def _confirm_point():
    import confirm_m5_saw_drive as c
    return c.point(c.BASE, c.SAW_VOL, 78, 20000, 0.0)


def _operating_level_probe():
    import measure_m5_operating_level as ol
    return ol.probe("r1", 0.75, 1.0)


def _pulse_cutoff_probe():
    import measure_m5_pulse_cutoff as pc
    return pc.probe(pc.BASE_CUT)


def _saw_drive_probe():
    import measure_m5_saw_drive as sd
    return sd.probe("r1", 0.75, 1.0)


def _drive_sweep():
    import mono_artifact_probe as probe
    return probe.drive_sweep(notes=(84,), drives=(0.75,))


# (the `where` label, the call, the stages read: stage -> a key the reader reads)
READERS = [
    ("confirm_m5_saw_drive.point", _confirm_point,
     {"output": "unwanted_rel_db", "ladder_out": "harmonics_rel_h1_db"}),
    ("measure_m5_operating_level.probe", _operating_level_probe, {"output": "unwanted_rel_db"}),
    ("measure_m5_pulse_cutoff.probe", _pulse_cutoff_probe, {"output": "unwanted_rel_db"}),
    ("measure_m5_saw_drive.probe", _saw_drive_probe, {"output": "unwanted_rel_db"}),
    ("drive_sweep", _drive_sweep,
     {"output": "unwanted_rel_db", "ladder_out": "harmonics_rel_h1_db",
      "oscillator": "harmonics_rel_h1_db"}),
]
READER_IDS = [r[0] for r in READERS]
READER_STAGES = [(name, call, stage, key) for name, call, reads in READERS for stage, key in reads.items()]
STAGE_IDS = [f"{name}-{stage}" for name, _, stage, _ in READER_STAGES]


@pytest.fixture(scope="module")
def measured_row():
    # A row the real measure_point measured: the shape the readers are written
    # against, from the code that produces it rather than synthesized.
    import mono_artifact_probe as probe
    row = probe.measure_point("r1", 84, probe.held_patch())
    assert row["verdict"] == "MEASURED", row["verdict"]
    assert {"output", "ladder_out", "oscillator"} <= set(row["stages"])
    assert not any("refused" in s for s in row["stages"].values())
    return row


@pytest.fixture(scope="module")
def silent_row():
    # The real silent row (#1692's driver): rectangles at zero gain in the 2x
    # chain render nothing at MIDI 36. Asserted, not assumed -- were the engine
    # to stop rendering silence here, this must refuse rather than serve a row
    # that measured.
    import measure_pulse2x_headroom as hr
    import mono_artifact_probe as probe
    with hr.candidate(0, "rect"):
        row = probe.measure_point("pulse2x", 36, probe.held_patch(waves=("square",) * 3))
    assert row["verdict"] == "FAIL-ACTIVITY" and row["stages"] == {}, row
    return row


def _serve(monkeypatch, row):
    # measure_point returns `row`; the list records that the reader reached it.
    import mono_artifact_probe as probe
    calls = []

    def measure_point(engine, note, patch, *, inject=""):
        calls.append((engine, note))
        return row
    monkeypatch.setattr(probe, "measure_point", measure_point)
    return calls


def _with_stage(row, stage, stage_dict):
    return {**row, "stages": {**row["stages"], stage: stage_dict}}


@pytest.mark.parametrize(("reader", "call", "reads"), READERS, ids=READER_IDS)
def test_reader_completes_on_a_row_that_measured(monkeypatch, measured_row, reader, call, reads):
    # Positive control for the two below: the refusal is the served row's doing.
    calls = _serve(monkeypatch, measured_row)
    call()
    assert calls


@pytest.mark.parametrize(("reader", "call", "reads"), READERS, ids=READER_IDS)
def test_reader_refuses_the_real_silent_row(monkeypatch, silent_row, reader, call, reads):
    import mono_artifact_probe as probe
    import refusal
    calls = _serve(monkeypatch, silent_row)
    with pytest.raises(probe.Refused) as info:
        call()
    assert isinstance(info.value, refusal.Refused)
    assert calls, f"{reader} never reached measure_point"
    text = str(info.value)
    assert text.startswith(reader), text
    assert "FAIL-ACTIVITY" in text and "-300.0" in text, text


@pytest.mark.parametrize(("reader", "call", "stage", "key"), READER_STAGES, ids=STAGE_IDS)
def test_reader_refuses_a_refused_stage_and_keeps_its_text(monkeypatch, measured_row, reader, call,
                                                           stage, key):
    import mono_artifact_probe as probe
    planted = f"planted {stage} refusal text"
    _serve(monkeypatch, _with_stage(measured_row, stage, {"refused": planted}))
    with pytest.raises(probe.Refused) as info:
        call()
    text = str(info.value)
    assert text.startswith(reader) and stage in text, text
    assert planted in text


@pytest.mark.parametrize(("reader", "call", "stage", "key"), READER_STAGES, ids=STAGE_IDS)
def test_reader_names_a_measured_stage_that_lacks_the_field_it_reads(monkeypatch, measured_row, reader,
                                                                     call, stage, key):
    # Rule 8 on the fields a reader lists: with every other key of the real
    # stage present, the one the reader reads must be refused by name. Every
    # other removal must refuse or not matter -- never surface as a KeyError.
    import mono_artifact_probe as probe
    real = measured_row["stages"][stage]
    for gone in real:
        _serve(monkeypatch, _with_stage(measured_row, stage, {k: v for k, v in real.items() if k != gone}))
        try:
            call()
        except probe.Refused as exc:
            if gone == key:
                assert key in str(exc), str(exc)
        else:
            assert gone != key, f"{reader} read {stage} without {key}"


# ---- require_measured: the one guard the readers share ----------------------

def test_require_measured_hands_back_the_stage_of_a_measured_row(measured_row):
    import mono_artifact_probe as probe
    stage = probe.require_measured(measured_row, "output", ("unwanted_rel_db", "intended_dbfs"), "where")
    assert stage is measured_row["stages"]["output"]


def test_require_measured_passes_a_none_that_is_present(measured_row):
    # split_spectrum sets upper_wanted_rel_db to None for a stage with no
    # harmonic in 5-20 kHz: presence is the condition, not a value.
    import mono_artifact_probe as probe
    stage = {**measured_row["stages"]["output"], "upper_wanted_rel_db": None}
    row = _with_stage(measured_row, "output", stage)
    assert probe.require_measured(row, "output", ("upper_wanted_rel_db", "intended_dbfs"), "where") is stage


def test_require_measured_refuses_a_missing_field_with_no_refused_key(measured_row):
    import mono_artifact_probe as probe
    stage = {k: v for k, v in measured_row["stages"]["output"].items() if k != "unwanted_rel_db"}
    with pytest.raises(probe.Refused) as info:
        probe.require_measured(_with_stage(measured_row, "output", stage), "output",
                               ("unwanted_rel_db", "intended_dbfs"), "where: x")
    assert str(info.value) == "where: x: output stage has no 'unwanted_rel_db'"


def test_require_measured_treats_one_string_as_one_field(measured_row):
    # A bare string is one field, never an iterable of characters.
    import mono_artifact_probe as probe
    with pytest.raises(probe.Refused) as info:
        probe.require_measured(measured_row, "output", "no_such_field", "where")
    assert str(info.value) == "where: output stage has no 'no_such_field'"
    assert probe.require_measured(measured_row, "output", "intended_dbfs", "where")


def test_require_measured_refuses_a_refused_stage_verbatim(measured_row):
    import mono_artifact_probe as probe
    row = _with_stage(measured_row, "ladder_out", {"refused": "planted ladder text"})
    with pytest.raises(probe.Refused) as info:
        probe.require_measured(row, "ladder_out", ("harmonics_rel_h1_db",), "where: y")
    assert str(info.value) == "where: y: ladder_out stage refused: planted ladder text"


@pytest.mark.parametrize("verdict", ["FAIL-ACTIVITY", "REFUSED", None])
def test_require_measured_names_the_verdict_that_is_not_measured(measured_row, silent_row, verdict):
    import mono_artifact_probe as probe
    if verdict == "FAIL-ACTIVITY":
        row = silent_row
    else:
        row = {k: v for k, v in measured_row.items() if k != "activity"}
        row["verdict"] = verdict
        row["reason"] = "planted reason"
    with pytest.raises(probe.Refused) as info:
        probe.require_measured(row, "output", ("unwanted_rel_db",), "where: z")
    text = str(info.value)
    assert text.startswith("where: z"), text
    if verdict == "FAIL-ACTIVITY":
        assert "FAIL-ACTIVITY" in text and "-300.0" in text, text
    else:
        assert f"verdict {verdict}" in text and "planted reason" in text, text


@pytest.mark.parametrize("stages", [
    {}, {"ladder_out": None}, {"ladder_out": []}, {"ladder_out": "x"}, {"ladder_out": 0},
    [], None,
])
def test_require_measured_names_a_stage_that_is_not_a_dict(measured_row, stages):
    # The guard-defeat: the verdict says MEASURED, the stage is not there.
    # The key must be the one asked for ("ladder_out"), or every case just
    # takes the missing-key path and the isinstance(got, dict) guard is never
    # exercised; fields=() and the exact message pin the stage refusal itself
    # (a missing-field refusal would otherwise satisfy a loose match).
    import mono_artifact_probe as probe
    row = {**measured_row, "stages": stages}
    with pytest.raises(probe.Refused,
                       match="where: w: measure_point verdict MEASURED has no 'ladder_out' stage"):
        probe.require_measured(row, "ladder_out", (), "where: w")


def test_require_measured_names_a_row_with_no_stages_at_all(measured_row):
    import mono_artifact_probe as probe
    row = {k: v for k, v in measured_row.items() if k != "stages"}
    with pytest.raises(probe.Refused, match="where: v.*ladder_out"):
        probe.require_measured(row, "ladder_out", (), "where: v")


# ---- confirm_m5_saw_drive: the CLI boundary it lacked -----------------------

CONFIRM_OUT = "result/nested.json"


@pytest.mark.parametrize("row_kind", ["silent", "stage-refused"])
def test_confirm_cli_refuses_an_unmeasured_row_and_writes_no_report(
        monkeypatch, tmp_path, capsys, measured_row, silent_row, row_kind):
    import confirm_m5_saw_drive as c
    if row_kind == "silent":
        row, planted = silent_row, "FAIL-ACTIVITY"
    else:
        row, planted = _with_stage(measured_row, "ladder_out", {"refused": "planted ladder text"}), \
            "planted ladder text"
    monkeypatch.setattr(c.ol, "calibrate", lambda engine, drive: 1.9231)
    _serve(monkeypatch, row)
    out = tmp_path / CONFIRM_OUT
    assert c.main(["--out", str(out)]) == 2
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1 and lines[0].startswith("REFUSED: confirm_m5_saw_drive.point: "), lines
    assert planted in lines[0]
    assert not out.exists()


def test_confirm_cli_writes_its_report_when_every_point_measures(monkeypatch, tmp_path, capsys, measured_row):
    import confirm_m5_saw_drive as c
    monkeypatch.setattr(c.ol, "calibrate", lambda engine, drive: 1.9231)
    calls = _serve(monkeypatch, measured_row)
    out = tmp_path / CONFIRM_OUT
    points = len(c.NOTES) * len(c.CUTOFFS) * len(c.RESONANCES)
    assert c.main(["--out", str(out)]) == 0
    assert len(calls) == 2 * points == 56          # a baseline and a candidate per point
    res = json.loads(out.read_text())
    assert res["confirmed"] is True and res["points"] == points == 28


def test_confirm_cli_does_not_relabel_an_execution_error(monkeypatch, tmp_path, capsys):
    import mono_artifact_probe as probe
    import confirm_m5_saw_drive as c
    monkeypatch.setattr(c.ol, "calibrate", lambda engine, drive: 1.9231)

    def broken(engine, note, patch, *, inject=""):
        raise KeyError("planted implementation error")
    monkeypatch.setattr(probe, "measure_point", broken)
    out = tmp_path / CONFIRM_OUT
    with pytest.raises(KeyError, match="planted implementation error"):
        c.main(["--out", str(out)])
    assert "REFUSED:" not in capsys.readouterr().out
    assert not out.exists()


def test_confirm_cli_boundary_carries_a_refusal_from_the_render_guard(monkeypatch, tmp_path, capsys):
    # Not a served row: the real guard in render_held refuses, and the refusal
    # leaves through main() as exit 2 (test_probe_refusal_at_calibration's
    # shape, for the one tool that had no boundary).
    import confirm_m5_saw_drive as c
    import mono_artifact_probe as probe
    held_patch = probe.held_patch
    monkeypatch.setattr(probe, "held_patch", lambda **kw: held_patch(q=1.0, **kw))
    out = tmp_path / CONFIRM_OUT
    assert c.main(["--out", str(out)]) == 2
    assert capsys.readouterr().out.splitlines() == [
        "REFUSED: resonance at or above the self-oscillation boundary is out of domain"]
    assert not out.exists()

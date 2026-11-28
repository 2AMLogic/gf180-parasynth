"""compare_r2_pulse must never accept over missing pairs, and must pair a
repaired row with R1's row for the same scheduled condition."""
import gzip
import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import compare_r2_pulse as c  # noqa: E402
import mono_artifact_probe as probe


def _row(engine, drive, unw=-60.0, rel=-50.0, up=-15.0, nominal_drive=None):
    r = {"engine": engine, "note": 84, "verdict": "MEASURED",
         "patch": {"waves": ["pulse29"] * 3, "cutoff": [20000, 20000], "q": 0.0, "drive": drive},
         "stages": {"output": {"unwanted_dbfs": unw, "unwanted_rel_db": rel, "upper_wanted_rel_db": up,
                               "intended_dbfs": -12.0, "residual_dbfs": -80.0}},
         "clip": {"output_rail_samples": 0}, "dropout_depth_db": -0.1}
    if nominal_drive is not None:
        r["nominal"] = {"waves": ["pulse29"] * 3, "cutoff": [20000, 20000], "q": 0.0, "drive": nominal_drive}
    return r


def test_repaired_row_pairs_by_schedule():
    recs, counts = c.compare([_row("r1", 0.75), _row("pulse2x", 1.0136, unw=-70, nominal_drive=0.75)])
    assert counts == {"measured": 1, "refused": 0, "not_run": 0}


# ---- a sweep record missing the key _load() reads with [] is REFUSED, not a
# traceback (#1604, #1623), through the tool's own main(). The record is the
# archived probe sweep the decision above was made on.
def _archived(tmp_path, part, edit=None):
    archive = probe.ROOT / "docs/scorecard/m5-artifacts-333"
    rec = json.loads(gzip.decompress((archive / f"r2-compare/s074-{part}.json.gz").read_bytes()))
    assert isinstance(rec["rows"], list) and rec["rows"]      # the key the reader needs
    if edit:
        edit(rec)
    path = tmp_path / f"{part}.json"
    path.write_text(json.dumps(rec))
    return str(path)


def test_a_sweep_record_missing_rows_is_refused(tmp_path, capsys):
    paths = [_archived(tmp_path, 0, lambda rec: rec.pop("rows")), _archived(tmp_path, 1)]
    comparison = tmp_path / "comparison.json"
    assert c.main([*paths, "--out", str(comparison)]) == 2
    out = capsys.readouterr().out
    assert "REFUSED" in out and "'rows'" in out
    assert not comparison.exists()                 # no comparison over a partial record


def test_a_sweep_record_with_null_rows_is_refused(tmp_path, capsys):
    paths = [_archived(tmp_path, 0, lambda rec: rec.__setitem__("rows", None)), _archived(tmp_path, 1)]
    comparison = tmp_path / "comparison.json"
    assert c.main([*paths, "--out", str(comparison)]) == 2
    out = capsys.readouterr().out
    assert "REFUSED" in out and "'rows'" in out


@pytest.mark.parametrize("record, why", [({}, "'rows'"), ([], "not a JSON object"), (None, "not a JSON object")])
def test_a_sweep_record_that_is_not_an_object_is_refused(tmp_path, capsys, record, why):
    path = tmp_path / "0.json"
    path.write_text(json.dumps(record))
    comparison = tmp_path / "comparison.json"
    assert c.main([str(path), _archived(tmp_path, 1), "--out", str(comparison)]) == 2
    out = capsys.readouterr().out
    assert "REFUSED" in out and why in out
    assert not comparison.exists()


def test_unpaired_rows_are_not_run_and_block_acceptance():
    recs, counts = c.compare([_row("r1", 0.75), _row("pulse2x", 1.0136)])
    assert counts["not_run"] == 1 and counts["measured"] == 0


def test_1_02_db_worse_fails_a_1_00_db_band():
    recs, _ = c.compare([_row("r1", 0.75, rel=-50.0), _row("pulse2x", 0.75, rel=-48.98)])
    assert recs[0]["verdict"] == "FAIL" and recs[0]["unwanted_rel_db"]["state"] == "regressed beyond band"


def test_darker_is_the_adverse_direction_for_brightness():
    recs, _ = c.compare([_row("r1", 0.75, up=-15.0), _row("pulse2x", 0.75, up=-16.5)])
    assert recs[0]["fails_declared_rule"] == ["upper_wanted_rel_db"]


@pytest.mark.parametrize(
    "prefix, decision, measured, failures, localized_r1, localized_candidates",
    [
        ("r2-compare/s074", "r2-074.json", 110, 6, 90, 40),
        ("pulse-level-repair/fresh", "r2-074-drivecomp-fresh.json", 112, 11, 14, 0),
    ],
)
def test_archived_pulse_reports(
    tmp_path, prefix, decision, measured, failures, localized_r1, localized_candidates
):
    archive = probe.ROOT / "docs/scorecard/m5-artifacts-333"
    paths = []
    for part in (0, 1):
        path = tmp_path / f"{part}.json"
        path.write_bytes(
            gzip.decompress((archive / f"{prefix}-{part}.json.gz").read_bytes())
        )
        paths.append(str(path))

    comparison = tmp_path / "comparison.json"
    assert c.main([*paths, "--out", str(comparison)]) == 1
    report = json.loads(comparison.read_text())
    archived_decision = (archive / prefix).parent / decision
    assert report["points"] == json.loads(archived_decision.read_text())["points"]
    assert report["counts"] == {"measured": measured, "not_run": 0, "refused": 0}
    assert len(report["failing_points"]) == failures

    summary = tmp_path / "summary.json"
    assert probe.main(["summarize", *paths, "--out", str(summary)]) == 0
    result = json.loads(summary.read_text())
    pairs = result["pulse2x_vs_r1"]
    assert len(pairs) == measured
    fields = ("wave", "note", "cutoff", "q", "drive")
    points = {tuple(p[f] for f in fields): p for p in report["points"]}
    for pair in pairs:
        point = points.pop(tuple(pair[f] for f in fields))
        assert pair["dev"] == point["development"]
        for prop, _ in c.PROPS:
            assert pair[f"d_{prop}"] == point[prop].get("delta")
    assert not points
    assert result["confirmation"]["unavailable_pairs"] == []
    assert sum(r["engine"] == "r1" for r in result["localization"]) == localized_r1
    assert (
        sum(r["engine"] == "pulse2x" for r in result["localization"])
        == localized_candidates
    )


def test_summary_identifies_candidate_without_r1(tmp_path):
    # Both candidates rendered at the same drive. Only one scheduled drive has an R1 row.
    rows = [
        _row("r1", 0.75),
        _row("pulse2x", 0.75),
        _row("pulse2x", 0.75, nominal_drive=1.6),
    ]
    record = tmp_path / "mixed.json"
    record.write_text(json.dumps({"rows": rows}))
    summary = probe.summarize([record])
    assert [p["drive"] for p in summary["pulse2x_vs_r1"]] == [0.75]
    assert summary["confirmation"]["unavailable_pairs"] == [
        {
            "wave": "pulse29",
            "note": 84,
            "cutoff": 20000,
            "q": 0.0,
            "drive": 1.6,
            "verdict": "NOT RUN (no R1 row)",
        }
    ]


def test_refused_legacy_r1_is_present_but_unmeasured(tmp_path):
    # Early grid sweeps saved only the patch overrides when a render refused.
    base = {
        "engine": "r1",
        "note": 84,
        "verdict": "REFUSED",
        "reason": "silent",
        "patch": {"waves": ["pulse29"] * 3},
    }
    rows = [base, _row("pulse2x", 0.75)]
    record = tmp_path / "refused.json"
    record.write_text(json.dumps({"rows": rows}))
    summary = probe.summarize([record])
    assert len(summary["refused"]) == 1
    assert summary["pulse2x_vs_r1"] == []
    assert [r["verdict"] for r in summary["confirmation"]["unavailable_pairs"]] == [
        "NO VERDICT (r1 REFUSED, candidate MEASURED)"
    ]
    _, counts = c.compare(rows)
    assert counts == {"measured": 0, "refused": 1, "not_run": 0}


def _reports_with_unavailable(tmp_path, rows):
    record = tmp_path / "sweep.json"
    record.write_text(json.dumps({"rows": rows}))
    comparison = tmp_path / "comparison.json"
    assert c.main([str(record), "--out", str(comparison)]) == 2
    report = json.loads(comparison.read_text())
    assert report["accepted_under_declared_rule"] == "NO VERDICT"
    summary = tmp_path / "summary.json"
    assert probe.main(["summarize", str(record), "--out", str(summary)]) == 0
    return json.loads(summary.read_text()), report


@pytest.mark.parametrize("engine,cutoff,q,drive", [
    ("pulse2x", 8000, 0.5, 1.6),
    ("r1", 20000, 0.0, 0.75),  # also used for preset stage localization
])
def test_refused_output_only_withholds_its_fresh_pair(tmp_path, engine, cutoff, q, drive):
    archive = probe.ROOT / "docs/scorecard/m5-artifacts-333/pulse-level-repair"
    rows, _ = c._load([archive / f"fresh-{part}.json.gz" for part in (0, 1)])
    point = {"wave": "pulse29", "note": 42, "cutoff": cutoff, "q": q, "drive": drive}
    row, = (r for r in rows if r["engine"] == engine and r["note"] == 42
            and r["nominal"] == {"waves": ["pulse29"] * 3, "cutoff": [cutoff] * 2,
                                 "q": q, "drive": drive})
    row["stages"]["output"] = {"refused": "non-finite samples"}

    summary, report = _reports_with_unavailable(tmp_path, rows)
    unavailable, = summary["confirmation"]["unavailable_pairs"]
    reason = unavailable["verdict"]
    assert reason.startswith("NO VERDICT") and "non-finite samples" in reason
    assert unavailable == {**point, "verdict": reason}
    refusal, = summary["refused"]
    assert refusal["engine"] == engine and "non-finite samples" in refusal["reason"]
    assert len(summary["pulse2x_vs_r1"]) == 111
    assert report["counts"] == {"measured": 111, "refused": 1, "not_run": 0}
    original = json.loads((archive / "r2-074-drivecomp-fresh.json").read_text())
    for result, expected in zip(report["points"], original["points"], strict=True):
        if all(expected[k] == v for k, v in point.items()):
            assert result == {**unavailable, "development": False}
        else:
            assert result == expected
    if engine == "r1":
        local, = (r for r in summary["localization"]
                  if (r["engine"], r["wave"], r["note"]) == ("r1", "pulse29", 42))
        assert "non-finite samples" in local["refused"]
        assert local["output_intended_dbfs"] is None
        assert local["unwanted_inband_dbfs"]["oscillator"] == (
            row["stages"]["oscillator"]["unwanted_inband_dbfs"]
        )


@pytest.mark.parametrize("engine, defect", [("r1", "refusal"), ("pulse2x", "level")])
def test_disagreeing_duplicates_refuse_in_both_orders(tmp_path, engine, defect):
    archive = probe.ROOT / "docs/scorecard/m5-artifacts-333/r2-compare"
    rows, _ = c._load([archive / f"s074-{part}.json.gz" for part in (0, 1)])
    repeated = [r for r in rows if r["note"] == 96 and r["patch"]["waves"][0] == "pulse29"
                and r["patch"]["cutoff"][0] == 20000 and r["patch"]["q"] == 0.0
                and r["patch"]["drive"] == 0.75]
    assert len(repeated) == 4
    row, = (r for r in repeated if r["engine"] == engine and r["group"] == "pairwise")
    if defect == "refusal":
        row.update(verdict="REFUSED", reason="silent")
    else:
        row["stages"]["output"]["unwanted_dbfs"] += 10

    results = []
    for ordered in (repeated, repeated[::-1]):
        summary, report = _reports_with_unavailable(tmp_path, ordered)
        assert report["counts"] == {"measured": 0, "refused": 1, "not_run": 0}
        assert summary["pulse2x_vs_r1"] == []
        unavailable, = summary["confirmation"]["unavailable_pairs"]
        assert "conflicting duplicate" in unavailable["verdict"]
        assert report["points"] == [{**unavailable, "development": True}]
        if defect == "level":
            local, = (r for r in summary["localization"] if r["engine"] == engine)
            assert "conflicting duplicate" in local["refused"]
            assert local["output_unwanted_rel_db"] is None
        results.append((summary, report["points"]))
    assert results[0] == results[1]

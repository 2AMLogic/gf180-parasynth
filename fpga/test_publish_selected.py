"""Timing evidence must cover the actual PLL core clock and fit the device."""
import copy
import json
import math
from pathlib import Path
import pytest

import publish_selected as pub
from publish_selected import timing_summary


def valid_report():
    return {"fmax": {"clk_core": {"constraint": 725 / 59, "achieved": 15.0}},
            "utilization": {"MULT18X18D": {"used": 104, "available": 156},
                            "TRELLIS_FF": {"used": 14008, "available": 83640}},
            "critical_paths": [{"from": "clk_core", "to": "clk_core"}]}


def test_valid_timing_report():
    result = timing_summary(valid_report())
    assert result["state"] == "PASS"
    assert result["core_constraint_mhz"] == pytest.approx(725 / 59)


def test_real_pinned_linux_timing_report():
    fixture = Path(__file__).parent / "reports/selected/linux-85f/timing.json"
    report = json.loads(fixture.read_text())
    result = timing_summary(report)
    assert result["state"] == "PASS"
    assert result["clocks"]["$glbnet$clk_core"]["achieved"] == pytest.approx(12.8869304657)


@pytest.mark.parametrize("wrong", [12.288, 12.287, 12.289, 25., float("nan")])
def test_nearby_but_wrong_core_clocks_refuse(wrong):
    report = valid_report()
    report["fmax"]["clk_core"]["constraint"] = wrong
    with pytest.raises(ValueError, match="clock constraint"):
        timing_summary(report)


@pytest.mark.parametrize("mutation", ["absent-clock", "wrong-clock", "missed-timing", "nan", "overfull", "no-paths"])
def test_wrong_or_incomplete_report_refuses(mutation):
    report = copy.deepcopy(valid_report())
    if mutation == "absent-clock": report["fmax"] = {}
    if mutation == "wrong-clock": report["fmax"]["clk_core"]["constraint"] = 25
    if mutation == "missed-timing": report["fmax"]["clk_core"]["achieved"] = 12
    if mutation == "nan": report["fmax"]["clk_core"]["achieved"] = math.nan
    if mutation == "overfull": report["utilization"]["MULT18X18D"]["available"] = 28
    if mutation == "no-paths": report["critical_paths"] = []
    with pytest.raises(ValueError): timing_summary(report)


# ---- a build report or toolchain record that cannot be read is REFUSED (#1621)

TOOLCHAIN_KEYS = ("sha256", "release")
REPORT_KEYS = ("configuration", "device", "source_commit", "source_sha256",
               "simulation_evidence", "stages", "package", "state")
KEYS = [("toolchain", k) for k in TOOLCHAIN_KEYS] + [("report", k) for k in REPORT_KEYS]
URL = "https://example.invalid/workflow"


def _records():
    """Both records with every key publish() reads and no valid value: the guard
    at the first read refuses a missing key before any value is checked."""
    toolchain = {"sha256": pub.toolchain_pin.SHA256, "release": pub.toolchain_pin.VERSION}
    return toolchain, {k: "x" for k in REPORT_KEYS}


def _artifact(tmp_path, toolchain, report):
    art = tmp_path / "artifact"
    art.mkdir()
    (art / "report.json").write_text(json.dumps(report))
    (art / "toolchain.json").write_text(json.dumps(toolchain))
    return art


def _damaged(which, key, how=None):
    toolchain, report = _records()
    record = toolchain if which == "toolchain" else report
    if how == "missing":
        record.pop(key)
    elif how == "null":
        record[key] = None
    return toolchain, report


@pytest.mark.parametrize("which,key", KEYS, ids=[f"{w}:{k}" for w, k in KEYS])
@pytest.mark.parametrize("how", ["missing", "null"])
def test_a_record_lacking_a_key_publish_reads_is_refused_naming_it(tmp_path, which, key, how):
    """`report[...]` and `toolchain[...]` were read with nothing checking either
    record, so a partial one was a `KeyError` traceback (exit 1) where the
    publisher's own refusals are ValueError."""
    toolchain, report = _damaged(which, key, how)
    with pytest.raises(ValueError, match=repr(key)):
        pub.publish(_artifact(tmp_path, toolchain, report), tmp_path / "out", URL)


@pytest.mark.parametrize("which,key", KEYS, ids=[f"{w}:{k}" for w, k in KEYS])
def test_the_entry_point_reports_a_record_lacking_a_key_as_refused(tmp_path, capsys, which, key):
    toolchain, report = _damaged(which, key, "missing")
    art = _artifact(tmp_path, toolchain, report)
    rc = pub.main([str(art), "--workflow-url", URL, "--out", str(tmp_path / "out")])
    out = capsys.readouterr().out
    assert rc == 2 and "REFUSED" in out and repr(key) in out, out
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("which", ["toolchain", "report"])
@pytest.mark.parametrize("whole", [{}, [], 5, None], ids=["empty", "list", "number", "null"])
def test_a_record_that_carries_nothing_readable_is_refused(tmp_path, capsys, which, whole):
    toolchain, report = _damaged(which, None)
    if which == "toolchain":
        toolchain = whole
    else:
        report = whole
    art = _artifact(tmp_path, toolchain, report)
    with pytest.raises(ValueError):
        pub.publish(art, tmp_path / "out", URL)
    rc = pub.main([str(art), "--workflow-url", URL, "--out", str(tmp_path / "out")])
    out = capsys.readouterr().out
    assert rc == 2 and "REFUSED" in out, out
    if whole == {}:
        for key in (TOOLCHAIN_KEYS if which == "toolchain" else REPORT_KEYS):
            assert repr(key) in out, (key, out)
    assert not (tmp_path / "out").exists()


def test_the_committed_publication_records_carry_the_keys_publish_reads():
    # the guards above must be satisfiable against the records that ship
    d = Path(__file__).parent / "reports/selected/linux-85f"
    report = json.loads((d / "report.json").read_text())
    toolchain = json.loads((d / "toolchain.json").read_text())
    assert all(report[k] is not None for k in REPORT_KEYS)
    assert all(toolchain[k] is not None for k in TOOLCHAIN_KEYS)

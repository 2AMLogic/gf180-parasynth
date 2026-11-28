"""Controls for tools/pytest_durations_report.py (issue #1816).

The report turns one hosted run's ``pytest --durations=0`` block into where the
``tools`` job's time goes. Its only failure mode that matters is a false table:
a line silently dropped is time silently reassigned to "everything else", and
the table still looks like data. So the parser REFUSES (exit 2) rather than
reports on every input that could produce one, and each refusal is asserted to
fire on the defect it exists for before the tool is trusted on a real log.

The expected numbers below are hand-computed from BLOCK, not from the tool, and
BLOCK is shaped like real pytest 9.2.0 output (header, 2-decimal rows, the
``N durations < 0.005s hidden`` trailer, then the ``-q`` summary line).
"""
from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as etree
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pytest_durations_report as drep  # noqa: E402

ROWS = (
    "   60.00s call     tools/test_a.py::test_slow\n"
    "    0.50s setup    tools/test_a.py::test_slow\n"
    "    0.10s teardown tools/test_a.py::test_slow\n"
    "    9.00s call     tools/test_a.py::test_quick\n"
    "    5.00s call     tools/test_b.py::test_one[x]\n"
    "    1.00s call     tools/test_c.py::TestK::test_m[y z]\n"
)
HEADER = "=" * 30 + " slowest durations " + "=" * 30 + "\n"
TRAILER = "(15 durations < 0.005s hidden.  Use -vv to show these durations.)\n"
SUMMARY = "1234 passed, 56 skipped in 3612.34s (1:00:12)\n"
BLOCK = HEADER + ROWS + TRAILER + "\n" + SUMMARY

LISTED = 60.0 + 0.5 + 0.1 + 9.0 + 5.0 + 1.0


def by_file(report: dict) -> dict[str, float]:
    return {f["file"]: f["total_s"] for f in report["files"]}


def test_totals_are_known_by_construction():
    report = drep.build_report(BLOCK)
    assert report["listed_total_s"] == pytest.approx(LISTED)
    assert report["phase_totals_s"] == pytest.approx(
        {"call": 75.0, "setup": 0.5, "teardown": 0.1}
    )
    assert report["summary"] == {
        "passed": 1234,
        "skipped": 56,
        "other": {},
        "total_s": 3612.34,
    }
    assert report["hidden"] == 15


def test_top_tests_are_summed_across_phases_and_sorted():
    report = drep.build_report(BLOCK)
    top = [(t["nodeid"], t["total_s"]) for t in report["tests"]]
    assert top == [
        ("tools/test_a.py::test_slow", pytest.approx(60.6)),
        ("tools/test_a.py::test_quick", pytest.approx(9.0)),
        ("tools/test_b.py::test_one[x]", pytest.approx(5.0)),
        ("tools/test_c.py::TestK::test_m[y z]", pytest.approx(1.0)),
    ]
    assert report["tests"][0]["share"] == pytest.approx(60.6 / LISTED)
    assert report["n_tests"] == 4 and report["n_files"] == 3


def test_file_totals_and_share():
    report = drep.build_report(BLOCK)
    files = by_file(report)
    assert files == pytest.approx(
        {"tools/test_a.py": 69.6, "tools/test_b.py": 5.0, "tools/test_c.py": 1.0}
    )
    assert sum(f["share"] for f in report["files"]) == pytest.approx(1.0)
    assert [f["file"] for f in report["files"]] == list(files)


def test_concentration_counts_files_to_reach_a_share():
    report = drep.build_report(BLOCK)
    c = report["concentration"]
    assert c["files_for_50pct"] == 1 and c["files_for_80pct"] == 1
    assert c["files_for_90pct"] == 1
    assert c["tests_for_50pct"] == 1 and c["tests_for_90pct"] == 2
    assert c["top5_files_share"] == pytest.approx(1.0)
    assert c["top25_tests_share"] == pytest.approx(1.0)


def test_evenly_spread_time_needs_every_file():
    text = HEADER + "".join(f"10.00s call     tools/test_{i}.py::test_t\n" for i in range(4))
    c = drep.build_report(text)["concentration"]
    assert c["files_for_50pct"] == 2 and c["files_for_90pct"] == 4
    assert c["top5_files_share"] == pytest.approx(1.0)


def test_duration_buckets_count_tests_and_time_per_bracket():
    b = {x["bucket"]: x for x in drep.build_report(BLOCK)["buckets"]}
    assert [x["bucket"] for x in b.values()] == [">=60", "30-60", "10-30", "1-10", "<1"]
    assert (b[">=60"]["n_tests"], b[">=60"]["total_s"]) == (1, pytest.approx(60.6))
    assert (b["30-60"]["n_tests"], b["10-30"]["n_tests"], b["<1"]["n_tests"]) == (0, 0, 0)
    assert (b["1-10"]["n_tests"], b["1-10"]["total_s"]) == (3, pytest.approx(15.0))
    assert sum(x["total_s"] for x in b.values()) == pytest.approx(LISTED)
    assert sum(x["share"] for x in b.values()) == pytest.approx(1.0)
    line = "tests at 10 s or more: 1 of 4 (25.0%) hold 80.2% of listed time"
    assert line in drep.render(drep.build_report(BLOCK), 25, 40)


def test_log_prefix_is_tolerated():
    prefix = "tools\tevery tools/ test\t2026-11-28T11:30:00.1234567Z "
    prefixed = "".join(prefix + line if line.strip() else line for line in BLOCK.splitlines(keepends=True))
    assert drep.build_report(prefixed)["listed_total_s"] == pytest.approx(LISTED)


def test_summary_is_optional():
    report = drep.build_report(HEADER + ROWS)
    assert report["summary"] is None and report["hidden"] is None
    assert report["listed_total_s"] == pytest.approx(LISTED)


def test_group_totals_and_a_group_file_with_no_durations_is_visible():
    groups = {
        "g1": ["tools/test_a.py"],
        "g2": ["tools/test_a.py", "tools/test_b.py"],
        "g3": ["tools/test_a.py", "tools/test_missing.py"],
    }
    report = drep.build_report(BLOCK, groups=groups)
    g = {x["name"]: x for x in report["groups"]}
    assert g["g1"]["total_s"] == pytest.approx(69.6)
    assert g["g1"]["share"] == pytest.approx(69.6 / LISTED)
    assert g["g2"]["total_s"] == pytest.approx(74.6)
    assert g["g3"]["files_with_no_durations"] == ["tools/test_missing.py"]


def test_a_block_whose_every_duration_rounds_to_zero_is_refused():
    """Added after its guard was disabled and nothing went red (rule 4)."""
    block = HEADER + "    0.00s call     tools/test_a.py::test_z\n" + TRAILER + "\n" + SUMMARY
    with pytest.raises(drep.Refused, match="rounds to 0.00s"):
        drep.build_report(block)


def test_no_block_is_refused():
    with pytest.raises(drep.Refused, match="no --durations block"):
        drep.build_report(".....   [100%]\n" + SUMMARY)


def test_two_blocks_are_refused():
    with pytest.raises(drep.Refused, match="2 --durations blocks"):
        drep.build_report(BLOCK + BLOCK)


def test_empty_block_is_refused():
    with pytest.raises(drep.Refused, match="no duration lines"):
        drep.build_report(HEADER + TRAILER + "\n" + SUMMARY)


def test_malformed_line_inside_the_block_is_refused():
    lines = ROWS.splitlines(keepends=True)
    lines.insert(3, "    0.40s cal     tools/test_a.py::test_typo\n")
    with pytest.raises(drep.Refused, match="malformed line inside the block"):
        drep.build_report(HEADER + "".join(lines) + TRAILER + "\n" + SUMMARY)


def test_truncated_line_at_the_end_of_the_block_is_refused():
    with pytest.raises(drep.Refused, match="malformed line inside the block"):
        drep.build_report(HEADER + ROWS + "    1.00s call")


def test_blank_line_inside_the_block_is_refused():
    lines = ROWS.splitlines(keepends=True)
    lines.insert(3, "\n")
    with pytest.raises(drep.Refused, match="duration line after the block ended"):
        drep.build_report(HEADER + "".join(lines) + TRAILER + "\n" + SUMMARY)


def test_dropped_lines_are_invisible_without_an_oracle_and_the_report_says_so():
    """The rule-8 adversary: a complete-looking block with one test removed.

    Nothing in the text can distinguish this from a run where that test was
    fast, so the tool must not claim completeness it does not have: without
    --junit the report carries no completeness verdict at all.
    """
    report = drep.build_report(HEADER + ROWS.replace("    9.00s call     tools/test_a.py::test_quick\n", "") + TRAILER + "\n" + SUMMARY)
    assert report["completeness"] is None


def test_extract_writes_exactly_the_block_with_log_prefixes_stripped(tmp_path):
    prefix = "tools\tevery tools/ test\t2026-11-28T11:30:00.1234567Z "
    prefixed = "".join(prefix + line if line.strip() else line for line in BLOCK.splitlines(keepends=True))
    src = tmp_path / "run.log"
    src.write_text("pip noise\nmore noise\n" + prefixed + "##[group]Run post step\n")
    out = tmp_path / "block.txt"
    assert drep.main(["--durations", str(src), "--extract", str(out)]) == 0
    assert out.read_text() == BLOCK


def test_a_pipe_in_a_nodeid_is_escaped_in_tables(tmp_path):
    """Real shape: test_check_workflows.py has ids containing `... || true`."""
    block = HEADER + "    5.00s call     tools/test_a.py::test_x[py a.py || true]\n" + TRAILER + "\n"
    src = tmp_path / "d.txt"
    src.write_text(block)
    assert drep.main(["--durations", str(src), "--json", str(tmp_path / "r.json")]) == 0
    row = [l for l in drep.render(drep.build_report(block), 25, 40).splitlines() if "test_x[" in l]
    assert row == ["| 1 | 5.00 | 100.0% | 5.00 | `tools/test_a.py::test_x[py a.py \\|\\| true]` |"]
    assert json.loads((tmp_path / "r.json").read_text())["tests"][0]["nodeid"] == "tools/test_a.py::test_x[py a.py || true]"


def test_cli_report_exits_0_and_writes_json(tmp_path, capsys):
    src = tmp_path / "durations.txt"
    src.write_text(BLOCK)
    out = tmp_path / "report.json"
    assert drep.main(["--durations", str(src), "--json", str(out)]) == 0
    data = json.loads(out.read_text())
    assert data["listed_total_s"] == pytest.approx(LISTED)
    printed = capsys.readouterr().out
    assert "REFUSED" not in printed and "tools/test_a.py" in printed


def test_cli_refusal_exits_2_and_names_the_line(tmp_path, capsys):
    src = tmp_path / "durations.txt"
    src.write_text(HEADER + ROWS + "    1.00s call")
    assert drep.main(["--durations", str(src)]) == 2
    captured = capsys.readouterr()
    assert "REFUSED" in captured.err and "malformed line inside the block" in captured.err
    assert captured.out == ""


def test_group_from_grep_reads_test_files_only_and_anchors_per_line(tmp_path):
    scan = tmp_path / "scan"
    scan.mkdir()
    (scan / "test_a.py").write_text("import subprocess\n")
    (scan / "test_b.py").write_text("print('not an import: import subprocess')\n")
    (scan / "helper.py").write_text("import subprocess\n")
    (scan / "nested").mkdir()
    (scan / "nested" / "test_c.py").write_text("from subprocess import run\n")
    assert drep.group_from_grep(str(scan), "^import subprocess|^from subprocess import") == [
        f"{scan}/nested/test_c.py",
        f"{scan}/test_a.py",
    ]
    assert drep.group_from_grep(str(scan), "^import subprocess") == [f"{scan}/test_a.py"]


def test_cli_group_grep_selects_files_whose_text_matches(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "scan").mkdir()
    (tmp_path / "scan" / "test_a.py").write_text("import subprocess\n")
    (tmp_path / "scan" / "test_b.py").write_text("print('no spawn here')\n")
    (tmp_path / "durations.txt").write_text(BLOCK.replace("tools/", "scan/"))
    report = json.loads(_json_report(tmp_path, ["--group-grep", "spawn=^import subprocess", "--scan-dir", "scan"]))
    g = {x["name"]: x for x in report["groups"]}
    assert g["spawn"]["n_files"] == 1
    assert g["spawn"]["total_s"] == pytest.approx(69.6)
    assert g["spawn"]["files_with_no_durations"] == []


JUNIT = etree.fromstring(
    "<testsuites><testsuite>"
    '<testcase classname="tools.test_a" name="test_slow" time="60.604" />'
    '<testcase classname="tools.test_a" name="test_quick" time="9.003" />'
    '<testcase classname="tools.test_b" name="test_one[x]" time="5.004" />'
    '<testcase classname="tools.test_c.TestK" name="test_m[y z]" time="1.002" />'
    "</testsuite></testsuites>"
)
QUICK_ROW = "    9.00s call     tools/test_a.py::test_quick\n"


def junit_with(cases: str):
    return etree.fromstring(f"<testsuites><testsuite>{cases}</testsuite></testsuites>")


def test_junit_key_follows_pytests_address_mangling():
    assert drep.junit_key("tools/test_a.py::test_slow") == ("tools.test_a", "test_slow")
    assert drep.junit_key("tools/test_c.py::TestK::test_m[y z]") == (
        "tools.test_c.TestK",
        "test_m[y z]",
    )
    assert drep.junit_key("tools/probes/test_p.py::test_x[a.b]") == (
        "tools.probes.test_p",
        "test_x[a.b]",
    )


def test_junit_key_does_not_split_on_a_double_colon_inside_the_brackets():
    """The shape that made the real run REFUSE: a param carrying a pytest nodeid."""
    nodeid = (
        "tools/test_bd_pitch_predeclaration.py::"
        "test_control_missing_probe_is_caught[model/bd_pedestal.py::tail_pedestal_GONE-defines no tail]"
    )
    assert drep.junit_key(nodeid) == (
        "tools.test_bd_pitch_predeclaration",
        "test_control_missing_probe_is_caught[model/bd_pedestal.py::tail_pedestal_GONE-defines no tail]",
    )


def test_a_matching_pair_is_accepted():
    report = drep.build_report(BLOCK, junit=JUNIT)
    c = report["completeness"]
    assert c["n_junit"] == 4 and c["n_block"] == 4 and c["n_only_in_junit"] == 0
    assert c["worst_per_test_gap_s"] <= c["per_test_tolerance_s"] == pytest.approx(0.0155)
    assert c["junit_counts_match_summary"] is False


def test_junit_counts_are_compared_with_the_summary_line():
    summary = "3 passed, 1 skipped in 4.00s\n"
    c = drep.build_report(HEADER + ROWS + TRAILER + "\n" + summary, junit=JUNIT)["completeness"]
    assert c["junit_counts_match_summary"] is True
    wrong = "3 passed, 2 skipped in 4.00s\n"
    c = drep.build_report(HEADER + ROWS + TRAILER + "\n" + wrong, junit=JUNIT)["completeness"]
    assert c["junit_counts_match_summary"] is False


def test_a_slow_test_missing_from_the_block_is_refused_by_name():
    """Rule 8: the text alone cannot see this; only the junit oracle can."""
    block = HEADER + ROWS.replace(QUICK_ROW, "") + TRAILER + "\n" + SUMMARY
    assert drep.build_report(block)["completeness"] is None
    with pytest.raises(drep.Refused, match=r"no duration line \(first: \('tools.test_a', 'test_quick'\) at 9.003s\)"):
        drep.build_report(block, junit=JUNIT)


def test_a_test_whose_every_phase_is_under_the_print_threshold_may_be_junit_only():
    tiny = junit_with('<testcase classname="tools.test_a" name="test_slow" time="60.604" />'
                      '<testcase classname="tools.test_a" name="test_quick" time="9.003" />'
                      '<testcase classname="tools.test_b" name="test_one[x]" time="5.004" />'
                      '<testcase classname="tools.test_c.TestK" name="test_m[y z]" time="1.002" />'
                      '<testcase classname="tools.test_a" name="test_instant" time="0.000" />')
    c = drep.build_report(BLOCK, junit=tiny)["completeness"]
    assert c["n_junit"] == 5 and c["n_only_in_junit"] == 1


def test_a_block_test_absent_from_junit_is_refused():
    without = junit_with(
        '<testcase classname="tools.test_a" name="test_slow" time="60.604" />'
        '<testcase classname="tools.test_a" name="test_quick" time="9.003" />'
        '<testcase classname="tools.test_c.TestK" name="test_m[y z]" time="1.002" />'
    )
    with pytest.raises(drep.Refused, match=r"absent from junit \(first: \('tools.test_b', 'test_one\[x\]'\)"):
        drep.build_report(BLOCK, junit=without)


def test_a_time_that_disagrees_beyond_the_derived_tolerance_is_refused():
    wrong = junit_with(
        '<testcase classname="tools.test_a" name="test_slow" time="30.000" />'
        '<testcase classname="tools.test_a" name="test_quick" time="9.003" />'
        '<testcase classname="tools.test_b" name="test_one[x]" time="5.004" />'
        '<testcase classname="tools.test_c.TestK" name="test_m[y z]" time="1.002" />'
    )
    with pytest.raises(drep.Refused, match="junit and durations disagree on \\('tools.test_a', 'test_slow'\\): 30.000s vs 60.60s"):
        drep.build_report(BLOCK, junit=wrong)


def test_the_tolerance_boundary_is_where_it_is_derived_to_be():
    def gap_case(gap: float):
        cases = "".join(
            f'<testcase classname="{cls}" name="{name}" time="{t + (gap if name == "test_slow" else 0):.4f}" />'
            for cls, name, t in (
                ("tools.test_a", "test_slow", 60.6),
                ("tools.test_a", "test_quick", 9.0),
                ("tools.test_b", "test_one[x]", 5.0),
                ("tools.test_c.TestK", "test_m[y z]", 1.0),
            )
        )
        return drep.build_report(BLOCK, junit=junit_with(cases))["completeness"]

    assert gap_case(0.0100)["worst_per_test_gap_s"] == pytest.approx(0.0100, abs=1e-3)
    with pytest.raises(drep.Refused, match="disagree"):
        gap_case(0.1000)


def test_duplicate_junit_ids_are_refused():
    dup = junit_with(
        '<testcase classname="tools.test_a" name="test_slow" time="60.604" />'
        '<testcase classname="tools.test_a" name="test_slow" time="60.604" />'
        '<testcase classname="tools.test_a" name="test_quick" time="9.003" />'
        '<testcase classname="tools.test_b" name="test_one[x]" time="5.004" />'
        '<testcase classname="tools.test_c.TestK" name="test_m[y z]" time="1.002" />'
    )
    with pytest.raises(drep.Refused, match="two testcases with the same id"):
        drep.build_report(BLOCK, junit=dup)


def test_a_group_that_matches_nothing_is_reported_as_empty_not_as_cheap(tmp_path, monkeypatch):
    """The rule-8 adversary for --group-grep: a regex that matches nothing.

    An empty group sums to 0.00s, which is exactly what a genuinely free group
    sums to, so the report must carry the distinction (n_files == 0).
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "scan").mkdir()
    (tmp_path / "scan" / "test_a.py").write_text("import subprocess\n")
    (tmp_path / "durations.txt").write_text(BLOCK)
    report = json.loads(_json_report(tmp_path, ["--group-grep", "typo=^improt subprocess", "--scan-dir", "scan"]))
    g = {x["name"]: x for x in report["groups"]}
    assert g["typo"]["n_files"] == 0 and g["typo"]["total_s"] == 0.0


def _json_report(tmp_path: Path, extra: list[str]) -> str:
    out = tmp_path / "r.json"
    argv = ["--durations", str(tmp_path / "durations.txt"), "--json", str(out), *extra]
    assert drep.main(argv) == 0
    return out.read_text()


def test_cli_group_file_lists_one_path_per_line(tmp_path):
    (tmp_path / "durations.txt").write_text(BLOCK)
    listing = tmp_path / "group.txt"
    listing.write_text("# a comment line\n\ntools/test_b.py\ntools/test_c.py\n")
    report = json.loads(_json_report(tmp_path, ["--group-file", "bc=" + str(listing)]))
    g = {x["name"]: x for x in report["groups"]}
    assert g["bc"]["total_s"] == pytest.approx(6.0)
    assert g["bc"]["n_files"] == 2

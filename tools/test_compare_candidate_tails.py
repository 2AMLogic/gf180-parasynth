"""The tail every compare_*_candidate.py script shares: provenance block,
output guard, and the fail-closed git contract behind them.

The duplicated tail was untested three times over (issue #1187: no test of
`compare_files`, `main` or the output guard existed in any of the three
`test_compare_*` files) while being the part that carries a delta record's
evidence identity -- which commit, which tree state, which exact inputs. A
refactor of that tail into one helper is only reviewable if the behaviour it
must preserve is pinned first, per script, at the CLI boundary these tests
drive.

The pin that matters most is the REFUSAL, not the happy path: a git that
cannot answer must surface as `CalledProcessError` out of `compare_files`
and REFUSED (exit 2) out of `main` -- never as a record claiming an empty
commit and a clean tree (#1187's trap; `provenance.git()`'s empty-string
contract is the shape this must NOT drift into). `run_at_utc` is pinned to
the isoformat-with-offset shape the committed delta records under
docs/scorecard/ carry, which is not `provenance.now()`'s shape.
"""
import copy
import hashlib
import json
import pathlib
import subprocess
import sys
from datetime import datetime

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import compare_drum_i2s_candidate as drum_mod                  # noqa: E402
import compare_ensemble_candidate as ensemble_mod              # noqa: E402
import compare_m5a_i2s_candidate as m5a_mod                    # noqa: E402
import delta_report                                          # noqa: E402


DRUM_SOURCES = {"tools/compare_drum_i2s_candidate.py", "tools/score_drum_i2s.py",
                "tools/run_case.py", "tools/scorecard.py", "tools/delta_report.py"}
ENSEMBLE_SOURCES = {"tools/compare_ensemble_candidate.py", "tools/score_ensemble_i2s.py",
                    "tools/run_case.py", "tools/scorecard.py", "tools/delta_report.py"}
M5A_SOURCES = {"tools/compare_m5a_i2s_candidate.py",
               "tools/measure_m5a_filter_oversample.py", "tools/mono_m5a_score.py",
               "tools/delta_report.py"}

#: Minimal reports carrying exactly the keys each script's `main` summary
#: print reads -- enough for the tail to run end to end without re-deriving
#: the (already tested) domain comparison above `compare_records`.
DRUM_REPORT = {
    "schema": "drum-integrated-rtl-vs-fixed-model-v1", "case_id": "D02A",
    "case_verdicts_agree": True,
    "baseline": {"scorecard_state": "pass"}, "candidate": {"scorecard_state": "pass"},
    "properties_that_differ": [], "properties_whose_verdict_flips": [],
    "attribution": {"attributable_to": "nothing"}}
ENSEMBLE_REPORT = {
    "schema": "ensemble-integrated-rtl-delta-v1", "comparison_mode": "cross-engine",
    "baseline": {"engine": "fixed-model", "state": "pass"},
    "candidate": {"engine": "integrated-rtl", "state": "pass"},
    "comparison": {"states_agree": True, "case_passes": True, "improved_components": [],
                   "regressed_components": [], "lost_passes": [], "accepts_candidate": True}}
M5A_REPORT = {
    "schema": "m5a-integrated-rtl-delta-v1", "valid": True,
    "comparison": {"accepts_incremental_improvement": True, "case_passes": True,
                   "improved_components": [], "regressed_components": []}}

DRUM_SUMMARY = {"report", "case_id", "case_verdicts_agree", "baseline_state",
                "candidate_state", "properties_that_differ",
                "properties_whose_verdict_flips", "attributable_to"}
ENSEMBLE_SUMMARY = {"report", "comparison_mode", "states_agree", "case_passes",
                    "improved", "regressed", "lost_passes", "accepts_candidate"}
M5A_SUMMARY = {"report", "accepts_incremental_improvement", "case_passes",
               "improved_components", "regressed_components"}

CASES = [
    (drum_mod, "compare_drum_i2s_candidate", DRUM_SOURCES, DRUM_REPORT, DRUM_SUMMARY),
    (ensemble_mod, "compare_ensemble_candidate", ENSEMBLE_SOURCES, ENSEMBLE_REPORT,
     ENSEMBLE_SUMMARY),
    (m5a_mod, "compare_m5a_i2s_candidate", M5A_SOURCES, M5A_REPORT, M5A_SUMMARY),
]


def _sandbox_repo(path: pathlib.Path, files: dict) -> str:
    """A real miniature repository, so the provenance block's git calls answer
    a genuine HEAD and a genuine clean/dirty state -- the same approach as
    `test_provenance_git_failure.py`, because a mocked git cannot exhibit the
    failure mode these tests exist to pin. Returns HEAD's sha."""
    for rel, body in files.items():
        target = path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body)
    env = ("-c", "user.email=t@t", "-c", "user.name=t")
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(path), *env, "commit", "-qm", "sandbox"], check=True)
    return subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"],
                          check=True, capture_output=True, text=True).stdout.strip()


def _sandbox(compare, sources, report, tmp_path, monkeypatch):
    files = {rel: f"# {rel}\n" for rel in sources}
    files["baseline.json"] = '{"engine": "baseline"}\n'
    files["candidate.json"] = '{"engine": "candidate"}\n'
    head = _sandbox_repo(tmp_path, files)
    monkeypatch.setattr(compare, "ROOT", tmp_path)
    monkeypatch.setattr(compare, "compare_records",
                        lambda b, c: copy.deepcopy(report))
    return head, tmp_path / "baseline.json", tmp_path / "candidate.json"


@pytest.mark.parametrize("compare,tool,sources,report,summary_keys", CASES,
                         ids=["drum", "ensemble", "m5a"])
def test_compare_files_pins_the_provenance_block(compare, tool, sources, report,
                                                 summary_keys, tmp_path, monkeypatch):
    head, baseline, candidate = _sandbox(compare, sources, report, tmp_path, monkeypatch)
    provenance = compare.compare_files(baseline, candidate)["provenance"]
    assert set(provenance) == {"source_commit", "source_dirty", "input_sha256",
                               "source_sha256", "run_at_utc"}
    assert provenance["source_commit"] == head
    assert provenance["source_dirty"] is False
    assert provenance["input_sha256"] == {
        "baseline_record": hashlib.sha256(baseline.read_bytes()).hexdigest(),
        "candidate_record": hashlib.sha256(candidate.read_bytes()).hexdigest()}
    assert set(provenance["source_sha256"]) == sources
    for rel in sources:
        assert provenance["source_sha256"][rel] == \
            hashlib.sha256((tmp_path / rel).read_bytes()).hexdigest()
    datetime.fromisoformat(provenance["run_at_utc"])
    assert provenance["run_at_utc"].endswith("+00:00")
    (tmp_path / "uncommitted.txt").write_text("dirt\n")
    assert compare.compare_files(baseline, candidate)["provenance"]["source_dirty"] is True


@pytest.mark.parametrize("compare,tool,sources,report,summary_keys", CASES,
                         ids=["drum", "ensemble", "m5a"])
def test_sources_hash_the_module_that_computes_the_provenance(
        compare, tool, sources, report, summary_keys):
    """Issue #1208: `comparison_provenance()` moved into `tools/delta_report.py`
    (PR #1191), which nothing hashed -- a future edit to it would emit
    differently-shaped records under an identical `source_sha256`. The module
    each comparator actually imports and calls for its provenance must be in
    that comparator's own SOURCES, derived from the import (not a stale
    hardcoded string)."""
    helper = "tools/" + pathlib.Path(compare.delta_report.__file__).name
    assert helper in compare.SOURCES


@pytest.mark.parametrize("compare,tool,sources,report,summary_keys", CASES,
                         ids=["drum", "ensemble", "m5a"])
def test_editing_the_provenance_module_changes_its_hashed_bytes(
        compare, tool, sources, report, summary_keys, tmp_path, monkeypatch):
    """The original #1208 gap, pinned end to end: editing delta_report.py's
    bytes must change the `source_sha256` entry for it -- and only that entry
    -- or the record's evidence identity is blind to the code that builds it."""
    _, baseline, candidate = _sandbox(compare, sources, report, tmp_path, monkeypatch)
    helper = "tools/" + pathlib.Path(compare.delta_report.__file__).name
    before = compare.compare_files(baseline, candidate)["provenance"]["source_sha256"]
    (tmp_path / helper).write_text("# edited provenance module bytes\n")
    after = compare.compare_files(baseline, candidate)["provenance"]["source_sha256"]
    assert after[helper] != before[helper]
    assert {k: v for k, v in after.items() if k != helper} == \
        {k: v for k, v in before.items() if k != helper}


@pytest.mark.parametrize("compare,tool,sources,report,summary_keys", CASES,
                         ids=["drum", "ensemble", "m5a"])
def test_a_git_that_cannot_answer_refuses_rather_than_records(
        compare, tool, sources, report, summary_keys, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(compare, "ROOT", tmp_path)          # never a repository
    monkeypatch.setattr(compare, "compare_records",
                        lambda b, c: copy.deepcopy(report))
    baseline = tmp_path / "baseline.json"
    baseline.write_text('{"engine": "baseline"}\n')
    candidate = tmp_path / "candidate.json"
    candidate.write_text('{"engine": "candidate"}\n')
    with pytest.raises(subprocess.CalledProcessError):
        compare.compare_files(baseline, candidate)
    rc = compare.main([str(baseline), str(candidate), "--out", "out/delta.json"])
    assert rc == 2
    assert f"{tool}: REFUSED" in capsys.readouterr().out
    assert not (tmp_path / "out" / "delta.json").exists()


@pytest.mark.parametrize("compare,tool,sources,report,summary_keys", CASES,
                         ids=["drum", "ensemble", "m5a"])
def test_main_refuses_an_out_path_outside_the_repository(
        compare, tool, sources, report, summary_keys, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(compare, "compare_records",
                        lambda b, c: copy.deepcopy(report))
    baseline = tmp_path / "baseline.json"
    baseline.write_text('{"engine": "baseline"}\n')
    candidate = tmp_path / "candidate.json"
    candidate.write_text('{"engine": "candidate"}\n')
    escape = tmp_path / "escape.json"
    rc = compare.main([str(baseline), str(candidate), "--out", str(escape)])
    assert rc == 2
    assert f"{tool}: REFUSED -- output must remain inside the repository" \
        in capsys.readouterr().out
    assert not escape.exists()


@pytest.mark.parametrize("compare,tool,sources,report,summary_keys", CASES,
                         ids=["drum", "ensemble", "m5a"])
def test_main_writes_the_report_inside_the_repository_and_prints_its_summary(
        compare, tool, sources, report, summary_keys, tmp_path, monkeypatch, capsys):
    head, baseline, candidate = _sandbox(compare, sources, report, tmp_path, monkeypatch)
    rc = compare.main(["baseline.json", "candidate.json", "--out", "out/delta.json"])
    assert rc == 0
    on_disk = json.loads((tmp_path / "out" / "delta.json").read_text())
    assert on_disk["provenance"]["source_commit"] == head
    for key, value in report.items():
        assert on_disk[key] == value
    printed = capsys.readouterr().out
    summary = json.loads(printed[printed.index("{"):])
    assert set(summary) == summary_keys
    assert summary["report"] == "out/delta.json"


# --------------------------------------------------------------------------- #
# a report that is not JSON (issue #1636)
#
# `json.dumps` writes NaN and Infinity as bare tokens by default, and Python
# is the only thing that reads them back. Every guard that stops a non-finite
# number reaching `write_report` is one edit away from not stopping it, so the
# write itself refuses -- and each `main` must render that as REFUSED rather
# than let the ValueError escape as a traceback.
# --------------------------------------------------------------------------- #

def _reject(token: str):
    raise AssertionError(f"a bare {token} token in a written record")


@pytest.mark.parametrize("number", [float("inf"), float("-inf"), float("nan")],
                         ids=["inf", "-inf", "nan"])
def test_write_report_refuses_a_non_finite_number_and_leaves_no_file(tmp_path, number):
    with pytest.raises(ValueError, match="not JSON"):
        delta_report.write_report({"worst": number}, "out/delta.json", "tool", root=tmp_path)
    assert not (tmp_path / "out" / "delta.json").exists()


def test_a_refused_report_leaves_the_record_already_at_the_output_path_as_it_was(
        tmp_path):
    out = tmp_path / "delta.json"
    out.write_text('{"old": true}\n')
    with pytest.raises(ValueError, match="not JSON"):
        delta_report.write_report({"worst": float("inf")}, "delta.json", "tool", root=tmp_path)
    assert out.read_text() == '{"old": true}\n'


def test_a_finite_report_is_still_written_and_is_strict_json(tmp_path):
    """The clean half of the control: the largest float and a long integer are
    numbers, not tokens, and must still be written."""
    report = {"worst": 1.7976931348623157e308, "n": 10**400}
    out = delta_report.write_report(report, "out/delta.json", "tool", root=tmp_path)
    assert json.loads(out.read_text(), parse_constant=_reject) == report


@pytest.mark.parametrize("compare,tool,sources,report,summary_keys", CASES,
                         ids=["drum", "ensemble", "m5a"])
def test_main_refuses_a_report_that_is_not_json_and_writes_nothing(
        compare, tool, sources, report, summary_keys, tmp_path, monkeypatch, capsys):
    """A non-finite number that got past every guard above the write."""
    monkeypatch.setattr(compare, "ROOT", tmp_path)
    unserialisable = {**copy.deepcopy(report), "worst": float("inf")}
    monkeypatch.setattr(compare, "compare_files", lambda b, c: unserialisable)
    baseline = tmp_path / "baseline.json"
    baseline.write_text("{}\n")
    candidate = tmp_path / "candidate.json"
    candidate.write_text("{}\n")
    rc = compare.main([str(baseline), str(candidate), "--out", "out/delta.json"])
    assert rc == 2
    printed = capsys.readouterr().out
    assert f"{tool}: REFUSED" in printed
    # the non-finite refusal, not the out-of-tree one that also prints REFUSED
    assert "not JSON" in printed
    assert not (tmp_path / "out" / "delta.json").exists()

"""Independent controls for causal rate-converter measurement instrumentation,
and for the record gate in front of the two paths that read its saved rows
back from JSON (#1687).

Every recompare case takes the shipped report, changes one value, and runs
the shipped `main` in-process against that copy inside a throwaway
repository, so the real report is never rewritten."""

import copy
import json
import pathlib
import subprocess

import pytest

import measure_m5a_filter_headroom as headroom
import measure_m5a_filter_oversample as experiment
import mono_m5a_score as m5a
from exit_contract import REFUSED

CHECKOUT = pathlib.Path(__file__).resolve().parents[1]
SHIPPED = CHECKOUT / "docs/scorecard/mono-m5a-miniv3/filter-headroom-v1.json"

_UNREADABLE = [pytest.param(10**400, id="beyond-float"),
               pytest.param(None, id="null"),
               pytest.param(True, id="true"),
               pytest.param("1.5", id="numeric-string"),
               pytest.param("nan", id="nan-string"),
               pytest.param(float("nan"), id="nan"),
               pytest.param(float("inf"), id="inf"),
               pytest.param(float("-inf"), id="-inf")]

_ABSENT = object()

_STUB_REPORT = {"source_commit": "stub", "source_dirty": False,
                "comparisons": {}, "causal_converter": {}}


def _output_tree(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (repo / "link").symlink_to(outside)
    monkeypatch.setattr(headroom, "ROOT", repo.resolve())
    monkeypatch.chdir(repo)
    return outside


def _escape(outside, case):
    return {"absolute": str(outside / "report.json"),
            "dotdot": "../report.json",
            "symlink": "link/report.json"}[case]


def _stub(monkeypatch, option):
    calls = []
    name = "run" if option == "--out" else "_recompare_report"
    monkeypatch.setattr(headroom, name,
                        lambda *args, **kwargs: calls.append(args) or _STUB_REPORT)
    return calls


def test_control_latency_diagnostic_requires_and_measures_the_injected_step():
    result = headroom._measure_control_response()
    assert result["step_frame"] == 100
    assert 0 <= result["first_changed_latency_frames"] <= 10
    quantiles = result["squared_response_energy_quantiles_frames_from_start"]
    assert quantiles["0.1"] <= quantiles["0.5"] <= quantiles["0.9"]
    assert quantiles["0.9"] < 512


@pytest.mark.parametrize("case", ["absolute", "dotdot", "symlink"])
@pytest.mark.parametrize("option", ["--out", "--recompare-report"])
def test_output_that_leaves_the_repository_is_refused_before_anything_runs(tmp_path, monkeypatch, capsys, option, case):
    outside = _output_tree(tmp_path, monkeypatch)
    calls = _stub(monkeypatch, option)
    assert headroom.main([option, _escape(outside, case)]) == 2
    assert "REFUSED" in capsys.readouterr().out
    assert calls == []
    assert not list(tmp_path.rglob("report.json"))


@pytest.mark.parametrize("option", ["--out", "--recompare-report"])
def test_in_repo_output_is_written_and_named_relative_to_the_repository(tmp_path, monkeypatch, capsys, option):
    _output_tree(tmp_path, monkeypatch)
    calls = _stub(monkeypatch, option)
    assert headroom.main([option, "build/report.json"]) == 0
    assert len(calls) == 1
    assert json.loads((tmp_path / "repo" / "build" / "report.json").read_text()) == _STUB_REPORT
    assert json.loads(capsys.readouterr().out)["report"] == "build/report.json"


def test_in_repo_out_named_through_a_symlink_from_outside_is_written_in_repo(tmp_path, monkeypatch, capsys):
    _output_tree(tmp_path, monkeypatch)
    _stub(monkeypatch, "--out")
    alias = tmp_path / "alias"
    alias.symlink_to(tmp_path / "repo")
    assert headroom.main(["--out", str(alias / "build" / "report.json")]) == 0
    assert (tmp_path / "repo" / "build" / "report.json").is_file()
    assert json.loads(capsys.readouterr().out)["report"] == "build/report.json"


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A throwaway git repository standing in for the checkout, so the shipped
    `main` rewrites a report inside the tree it describes and reads git state
    there, and the real report is never touched."""
    root = tmp_path / "repo"
    for name in headroom._SOURCE_FILES:
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((CHECKOUT / name).read_bytes())
    for command in (["git", "init", "-q", str(root)],
                    ["git", "-C", str(root), "add", "-A"],
                    ["git", "-C", str(root), "-c", "user.email=t@t", "-c", "user.name=t",
                     "-c", "commit.gpgsign=false", "commit", "-qm", "sandbox"]):
        subprocess.run(command, check=True)
    monkeypatch.setattr(headroom, "ROOT", root)
    return root


@pytest.fixture
def comparisons(monkeypatch):
    """Every `_compare_incremental` call the shipped code makes, so a test can
    tell REFUSED from 'compared, then refused'."""
    calls = []
    compare = experiment._compare_incremental

    def recording(baseline, candidate):
        calls.append((baseline, candidate))
        return compare(baseline, candidate)

    monkeypatch.setattr(experiment, "_compare_incremental", recording)
    return calls


@pytest.fixture
def renderer(monkeypatch):
    """Stand in for the renders `run` performs for the rows a reuse report does
    not supply: the shipped row for that pulse and mode, and a record of which
    were asked for."""
    calls = []

    def measure(*, pulse_shape, model_label, **_):
        calls.append((pulse_shape, model_label))
        mode = model_label.removeprefix("reconstructed_2x_")
        return copy.deepcopy(_shipped()["configurations"][pulse_shape][mode])

    monkeypatch.setattr(m5a, "measure", measure)
    return calls


def _shipped():
    return json.loads(SHIPPED.read_text())


def _saved(root, report, name="report.json"):
    path = root / name
    path.write_text(json.dumps(report))
    return path


def _set_metric(report, pulse, mode, metric, value):
    measured = report["configurations"][pulse][mode]["metrics"][metric]
    if value is _ABSENT:
        del measured["error"]
    else:
        measured["error"] = value


def _set_partial(report, pulse, mode, index, partial, value):
    event = report["configurations"][pulse][mode]["event_diagnostics"][index]
    event["harmonic_error_db_model_minus_reference"][partial] = value


def _recompare(capsys, path):
    rc = headroom.main(["--recompare-report", str(path)])
    return rc, capsys.readouterr().out


def _comparison(path, pulse, name):
    return json.loads(path.read_text())["comparisons"][pulse][name]


def test_the_shipped_report_recompares_to_the_decisions_it_shipped_with(repo, comparisons, capsys):
    """The apparatus's own precondition: an intact report passes the gate, is
    rewritten inside the tree, and the decisions recomputed from its rows are
    the ones it already carries."""
    untouched = SHIPPED.read_bytes()
    path = _saved(repo, _shipped())
    rc, out = _recompare(capsys, path)
    assert rc == 0
    assert len(comparisons) == 6
    assert json.loads(path.read_text())["comparisons"] == _shipped()["comparisons"]
    assert json.loads(out)["report"] == "report.json"
    assert SHIPPED.read_bytes() == untouched


@pytest.mark.parametrize("value", _UNREADABLE + [pytest.param(_ABSENT, id="absent")])
@pytest.mark.parametrize("pulse, mode, metric", [
    ("pulse29", "headroom", "Pitch"), ("pulse479", "causal_headroom", "Gain")])
def test_recompare_refuses_a_metric_error_that_is_not_a_finite_json_number(
        repo, comparisons, capsys, value, pulse, mode, metric):
    """#1687 item 1. `abs(float(...))` raises on `null` and on an integer beyond
    float range -- out of `_error_magnitude`, exit 1, which is FAIL, for a
    record the tool should decline -- and accepts every other value here as a
    number."""
    report = copy.deepcopy(_shipped())
    _set_metric(report, pulse, mode, metric, value)
    path = _saved(repo, report)
    before = path.read_bytes()
    rc, out = _recompare(capsys, path)
    assert rc == REFUSED
    assert out.splitlines()[0].startswith(
        f"REFUSED: {pulse}/{mode} metrics.{metric}.error must be ")
    assert path.read_bytes() == before
    assert comparisons == []


def _headroom_row(report):
    return report["configurations"]["pulse29"]["headroom"]


@pytest.mark.parametrize("damage, refusal", [
    (lambda row, report: report["configurations"]["pulse29"].__setitem__("headroom", 7),
     "pulse29/headroom is not an object"),
    (lambda row, report: row.pop("metrics"),
     "pulse29/headroom metrics is missing or not an object"),
    (lambda row, report: row.__setitem__("metrics", []),
     "pulse29/headroom metrics is missing or not an object"),
    (lambda row, report: row["metrics"].__setitem__("Pitch", -0.14812),
     "pulse29/headroom metrics.Pitch is not an object"),
    (lambda row, report: row.pop("event_diagnostics"),
     "pulse29/headroom event_diagnostics is missing or not a list"),
    (lambda row, report: row.__setitem__("event_diagnostics", dict.fromkeys("0123")),
     "pulse29/headroom event_diagnostics is missing or not a list"),
    (lambda row, report: row["event_diagnostics"].__setitem__(0, "saw"),
     "pulse29/headroom event 0 is not an object"),
    (lambda row, report: row["event_diagnostics"][0].pop("harmonic_error_db_model_minus_reference"),
     "pulse29/headroom event 0 harmonic partial evidence is missing or not an object"),
    (lambda row, report: row["event_diagnostics"][0].__setitem__(
        "harmonic_error_db_model_minus_reference",
        list(row["event_diagnostics"][0]["harmonic_error_db_model_minus_reference"])),
     "pulse29/headroom event 0 harmonic partial evidence is missing or not an object"),
], ids=["row", "metrics-absent", "metrics-list", "metric", "events-absent", "events-object",
        "event", "partials-absent", "partials-list"])
def test_recompare_refuses_a_row_whose_evidence_is_not_shaped_to_be_read(
        repo, comparisons, capsys, damage, refusal):
    """The gate cannot read numbers out of evidence that is not made of
    objects, and the comparator cannot either: five of these shapes were
    tracebacks here, and the other four its refusals in other words."""
    report = copy.deepcopy(_shipped())
    damage(_headroom_row(report), report)
    path = _saved(repo, report)
    before = path.read_bytes()
    rc, out = _recompare(capsys, path)
    assert rc == REFUSED
    assert out.splitlines() == [f"REFUSED: {refusal}"]
    assert path.read_bytes() == before
    assert comparisons == []


@pytest.mark.parametrize("value", _UNREADABLE)
def test_recompare_refuses_a_partial_that_is_not_a_finite_json_number(
        repo, comparisons, capsys, value):
    """#1687 item 2. `float()` raises for two of these and accepts the others:
    `true` was listed as an improvement of 1.56 dB and `"1.5"` as one of
    1.06 dB, and a NaN delta fell in neither list, which reads as no change."""
    report = copy.deepcopy(_shipped())
    _set_partial(report, "pulse29", "headroom", 0, "h2", value)
    path = _saved(repo, report)
    before = path.read_bytes()
    rc, out = _recompare(capsys, path)
    assert rc == REFUSED
    assert out.splitlines()[0].startswith(
        "REFUSED: pulse29/headroom event 0 saw/84 harmonic partial h2 must be ")
    assert path.read_bytes() == before
    assert comparisons == []


@pytest.mark.parametrize("value", [3, -2.5, 0, 1, 10**300],
                         ids=["int", "negative", "zero", "one", "large-in-range"])
def test_a_readable_partial_is_still_compared(repo, capsys, value):
    """The other half of the control above: a gate that refused every partial
    would satisfy it as easily."""
    report = copy.deepcopy(_shipped())
    _set_partial(report, "pulse29", "headroom", 0, "h2", value)
    path = _saved(repo, report)
    assert headroom.main(["--recompare-report", str(path)]) == 0
    components = _comparison(path, "pulse29", "headroom_vs_clamped_offline")["components"]
    assert components["partial:0:saw:84:h2"]["candidate_error"] == abs(float(value))


def test_a_readable_integer_metric_error_is_still_compared(repo, capsys):
    """`1` is compared where `true` is refused: the gate reads JSON numbers,
    which is what `bool` is not."""
    report = copy.deepcopy(_shipped())
    _set_metric(report, "pulse29", "headroom", "Pitch", 1)
    path = _saved(repo, report)
    assert headroom.main(["--recompare-report", str(path)]) == 0
    comparison = _comparison(path, "pulse29", "headroom_vs_clamped_offline")
    assert comparison["components"]["metric:Pitch"]["candidate_error"] == 1.0
    assert "metric:Pitch" in comparison["regressed_components"]


def test_a_report_path_outside_the_repository_is_refused_before_any_comparison(
        tmp_path, comparisons, capsys):
    """#1687 item 3. `main` rewrote the report and only then asked for a
    repository-relative path to print, which raised outside the tree: the
    file replaced, and exit 1."""
    outside = tmp_path / "outside" / "report.json"
    outside.parent.mkdir()
    outside.write_text(json.dumps(_shipped()))
    before = outside.read_bytes()
    rc, out = _recompare(capsys, outside)
    assert rc == REFUSED
    assert out.splitlines()[0].startswith(
        "REFUSED: --recompare-report output must remain inside the repository, not ")
    assert outside.read_bytes() == before
    assert comparisons == []


def test_an_out_path_outside_the_repository_is_refused_before_any_render(repo, renderer, capsys):
    """`--out` resolves against the repository, so an absolute path escapes
    the same way; it is refused first, before the renders it would waste."""
    escape = repo.parent / "escape.json"
    assert headroom.main(["--out", str(escape)]) == REFUSED
    assert capsys.readouterr().out.splitlines()[0].startswith(
        "REFUSED: --out output must remain inside the repository, not ")
    assert renderer == []
    assert not escape.exists()


def test_a_report_the_summary_cannot_read_is_refused_and_left_unwritten(repo, capsys):
    """The second way this command rewrote a report and then exited 1: the
    summary was built after the write, from keys `_recompare_report` never
    requires of the record it loads."""
    report = copy.deepcopy(_shipped())
    del report["causal_converter"]
    path = _saved(repo, report)
    before = path.read_bytes()
    rc, out = _recompare(capsys, path)
    assert rc == REFUSED
    assert out.splitlines() == [
        "REFUSED: report lacks causal_converter, so there is nothing to summarise"]
    assert path.read_bytes() == before


def test_an_intact_shipped_report_is_reused_and_only_the_missing_rows_are_rendered(
        repo, renderer, capsys):
    """The reuse apparatus's precondition: an intact report is accepted, and
    the rows it does not supply are measured for."""
    path = _saved(repo, _shipped(), "reuse.json")
    assert headroom.main(["--reuse-offline-report", str(path), "--out", "build/report.json"]) == 0
    assert sorted(renderer) == [("pulse29", "reconstructed_2x_causal_headroom"),
                                ("pulse479", "reconstructed_2x_causal_headroom")]
    report = json.loads((repo / "build/report.json").read_text())
    assert report["comparisons"] == _shipped()["comparisons"]
    reused = report["reused_measurements"]["clamped+headroom"]
    assert reused["reused_rows"] == ["clamped", "headroom"]


@pytest.mark.parametrize("option, pulse, mode", [
    ("--reuse-offline-report", "pulse29", "clamped"),
    ("--reuse-offline-report", "pulse479", "headroom"),
    ("--reuse-causal-report", "pulse29", "causal_headroom"),
])
@pytest.mark.parametrize("damage, refusal", [
    (lambda report, pulse, mode: _set_metric(report, pulse, mode, "Pitch", None),
     " metrics.Pitch.error must be a JSON number, not None"),
    (lambda report, pulse, mode: _set_partial(report, pulse, mode, 0, "h2", float("nan")),
     " event 0 saw/84 harmonic partial h2 must be finite, not nan"),
], ids=["null-metric", "nan-partial"])
def test_reuse_refuses_an_unreadable_row_before_anything_is_rendered(
        repo, comparisons, renderer, capsys, option, pulse, mode, damage, refusal):
    """#1687, the `load_reuse` path: the reused rows reach the same
    `_compare_incremental` with nothing in front of them, and only after every
    row the report does not supply has been rendered."""
    report = copy.deepcopy(_shipped())
    damage(report, pulse, mode)
    path = _saved(repo, report, "reuse.json")
    before = path.read_bytes()
    rc = headroom.main([option, str(path), "--out", "build/report.json"])
    assert rc == REFUSED
    assert capsys.readouterr().out.splitlines() == [f"REFUSED: reuse {pulse}/{mode}{refusal}"]
    assert renderer == []
    assert comparisons == []
    assert path.read_bytes() == before
    assert not (repo / "build/report.json").exists()


@pytest.mark.parametrize("value", [10**400, None, "not-a-number"],
                         ids=["beyond-float", "null", "non-numeric-string"])
def test_recompare_report_refuses_a_partial_that_float_cannot_read(repo, capsys, value):
    """#1680, the second shipped path: `--recompare-report` hands JSON-loaded
    rows straight to `_compare_incremental`, with no record gate in front, so
    its unguarded `float()` left as a traceback -- exit 1 -- here too. The
    reason is asserted because any refusal would give the exit code."""
    report = _shipped()
    row = report["configurations"]["pulse29"]["headroom"]
    row["event_diagnostics"][0]["harmonic_error_db_model_minus_reference"]["h2"] = value
    path = _saved(repo, report)  # inside the repo: #1687 confinement refuses outside paths first

    assert headroom.main(["--recompare-report", str(path)]) == 2

    # #1687's record gate now refuses these before any comparison runs, naming the
    # row and field; the reason is still asserted, not just the exit code.
    assert ("REFUSED: pulse29/headroom event 0 saw/84 harmonic partial h2 must be"
            in capsys.readouterr().out)
    assert path.read_text() == json.dumps(report)

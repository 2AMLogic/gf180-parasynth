"""Four ways `tools/scorecard.py` misreported a record it could not score,
each kept as the control its repair must stay green against
(`docs/verification-rules.md` rule 8). Items 1-3 are issue #1669; item 4 is
#2089.

1. An `engine` that is not a string. `evaluate` answered `no verdict` and
   carried the value into the row; `main()` then sorted it against string
   engines and joined it. That is exit 1 with a traceback, which
   `tools/exit_contract.py` defines as FAIL -- "the comparison ran and
   disagreed" -- for a record the board had already declined to score.
2. `--markdown` pointed outside the repository. The file was written, and the
   NEXT statement, `out.relative_to(ROOT)`, raised: exit 1 with the file on
   disk.
3. A result file holding `null`. `json.loads` answers `None`, which
   `evaluate` reads as "there is no file" -- so a case that ran and wrote
   `null` was reported as never attempted rather than as a record the board
   cannot score.
4. A nested provenance field that is not an object. `evaluate` tested
   `worktree`, `inputs` and `config` only for presence, so `["x"]` under any
   of them passed -- and the record was scored as a pass on provenance nobody
   could read. `measurement_basis` then raised on `inputs.items()`, and
   `compare` read the `worktree` case as REJECT: the device did not improve.

Every record here is the committed `docs/scorecard/results/D02A.json` changed
in one field, so no test can pass against a fixture shape nothing writes; the
unchanged record is the control (`test_the_committed_record_still_passes`).
The board is driven through `main()` with `ROOT`, `RESULTS`, `load_cases` and
`checkout_staleness` redirected; the CLI has no flag for a results directory.
"""
import json
import pathlib
import re
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import scorecard as sc                                              # noqa: E402

TOOLS = pathlib.Path(__file__).resolve().parent
RESULTS = TOOLS.parent / "docs" / "scorecard" / "results"
CASES = {c["case_id"]: c for c in sc.load_cases()}


class Verbatim(str):
    """A result file's exact text, written as given."""


def _committed(case_id="D02A", **change):
    """A committed record -- D02A is a pass on the integrated RTL -- with
    `change` applied on top."""
    record = json.loads((RESULTS / f"{case_id}.json").read_text())
    record.update(change)
    return record


def _render(monkeypatch, tmp_path, capsys, results, *flags, markdown="docs/scorecard/BOARD.md"):
    """`tools/scorecard.py` over a results directory of the caller's making.

    `ROOT` is a directory of this test's own, so "outside the repository" is
    a property of the test rather than of wherever pytest put `tmp_path`.
    Returns `(rc, stdout)`. An entry of `None` is a case with no result file."""
    repo = tmp_path / "repo"
    (repo / "results").mkdir(parents=True)
    for case_id, record in results.items():
        if record is None:
            continue
        (repo / "results" / f"{case_id}.json").write_text(
            record if isinstance(record, Verbatim) else json.dumps(record))
    monkeypatch.setattr(sc, "ROOT", repo)
    monkeypatch.setattr(sc, "RESULTS", repo / "results")
    monkeypatch.setattr(sc, "load_cases", lambda: [dict(CASES[cid]) for cid in results])
    monkeypatch.setattr(sc, "checkout_staleness", lambda: None)
    argv = ["scorecard.py", *(["--markdown", markdown] if markdown else []), *flags]
    monkeypatch.setattr(sys, "argv", argv)
    rc = sc.main()
    return rc, capsys.readouterr().out


def _line(text, prefix):
    return next(line for line in text.splitlines() if line.startswith(prefix))


def _tally(out):
    """(cases, valid, pass, fail, no verdict, not run) off the TOTAL line."""
    return tuple(int(n) for n in re.findall(r"\d+", _line(out, "TOTAL")))


def _board(tmp_path):
    return (tmp_path / "repo" / "docs" / "scorecard" / "BOARD.md").read_text()


# ---- the control -----------------------------------------------------------

def test_the_committed_record_still_passes(monkeypatch, tmp_path, capsys):
    """The apparatus renders the real D02A as a pass, so a `no verdict` below
    is the one changed field and nothing else."""
    rc, out = _render(monkeypatch, tmp_path, capsys, {"D02A": _committed()}, "--verbose")
    assert rc == 0 and _tally(out) == (1, 1, 1, 0, 0, 0)
    assert "Engines represented: integrated-rtl" in out


# ---- item 1: an engine that is not a string --------------------------------

NOT_STRINGS = [pytest.param(5, id="int"), pytest.param(True, id="bool"),
               pytest.param(["fixed-model"], id="list"),
               pytest.param({"name": "fixed-model"}, id="dict")]


@pytest.mark.parametrize("engine", NOT_STRINGS)
def test_evaluate_carries_a_non_string_engine_as_text(engine):
    """The row's `engine` is text whatever the record held. Every renderer
    sorts, joins and formats it, and none of them can take a JSON value."""
    verdict = sc.evaluate(CASES["D02A"], _committed(engine=engine))
    assert verdict["state"] == sc.NO_VERDICT and verdict["worst"] is None
    assert f"unknown engine {engine!r}" in verdict["why"]
    assert isinstance(verdict["engine"], str) and verdict["engine"]


@pytest.mark.parametrize("engine", NOT_STRINGS)
@pytest.mark.parametrize("flags", [[], ["--verbose"]], ids=["plain", "verbose"])
def test_the_board_renders_a_non_string_engine_as_no_verdict(
        monkeypatch, tmp_path, capsys, engine, flags):
    rc, out = _render(monkeypatch, tmp_path, capsys,
                      {"D02A": _committed(engine=engine)}, *flags)
    assert rc == 0
    assert _tally(out) == (1, 0, 0, 0, 1, 0)
    if flags:
        row = _line(out, "D02A")
        assert "no verdict" in row and f"unknown engine {engine!r}" in row
    assert "no verdict" in _line(_board(tmp_path), "| `D02A` |")


def test_a_non_string_engine_is_stringified_not_dropped_from_the_engines_line(
        monkeypatch, tmp_path, capsys):
    """Stringified rather than excluded: the console's "no case has been
    measured on the integrated RTL" note hangs off the engines set, and a
    board whose only record names a non-string engine has still not been
    measured on the RTL."""
    rc, out = _render(monkeypatch, tmp_path, capsys, {"D02A": _committed(engine=5)})
    assert rc == 0
    assert "Engines represented: 5" in out
    assert "no case has been measured on the integrated RTL" in out


def test_a_non_string_engine_beside_string_ones_sorts_and_joins(monkeypatch, tmp_path, capsys):
    """The real board's shape -- one bad record among string engines -- is
    where `sorted` met the mixed set before the join did."""
    rc, out = _render(monkeypatch, tmp_path, capsys,
                      {"D02A": _committed(engine=True), "D09A": _committed("D09A")})
    assert rc == 0
    assert _tally(out) == (2, 1, 1, 0, 1, 0)
    assert "Engines represented: True, fixed-model" in out
    assert "integrated-rtl" not in _line(out, "Engines represented")


# ---- item 2: --markdown outside the repository -----------------------------

def test_markdown_outside_the_repository_is_written_and_exits_0(monkeypatch, tmp_path, capsys):
    repo, out = tmp_path / "repo", tmp_path / "outside" / "anywhere" / "BOARD.md"
    assert not out.is_relative_to(repo)               # the precondition, asserted
    rc, stdout = _render(monkeypatch, tmp_path, capsys, {"D02A": _committed()},
                         markdown=str(out))
    assert rc == 0
    assert out.read_text().startswith("# The board")
    assert f"wrote {out}" in stdout                   # absolute: nothing inside ROOT to print


@pytest.mark.parametrize("given", ["relative", "absolute"])
def test_markdown_inside_the_repository_is_still_named_relative_to_it(
        monkeypatch, tmp_path, capsys, given):
    """The other half: a repair that printed every path absolutely would pass
    the test above as easily."""
    repo = tmp_path / "repo"
    path = repo / "docs" / "scorecard" / "BOARD.md"
    rc, stdout = _render(monkeypatch, tmp_path, capsys, {"D02A": _committed()},
                         markdown="docs/scorecard/BOARD.md" if given == "relative" else str(path))
    assert rc == 0 and path.is_file()
    assert "wrote docs/scorecard/BOARD.md" in stdout


def test_the_cli_exits_0_after_writing_markdown_outside_the_repository(tmp_path):
    """The shipped command, as the issue wrote it, against the real board."""
    out = tmp_path / "anywhere" / "BOARD.md"
    done = subprocess.run([sys.executable, str(TOOLS / "scorecard.py"), "--markdown", str(out)],
                          cwd=TOOLS.parent, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert "Traceback" not in done.stderr
    assert out.is_file()


# ---- item 3: a result file that is not a JSON object -----------------------

NOT_OBJECTS = [pytest.param("null", id="null"), pytest.param("true", id="bool"),
               pytest.param("5", id="int"), pytest.param('"x"', id="string"),
               pytest.param("[]", id="array"), pytest.param("[1, 2]", id="non-empty array")]


@pytest.mark.parametrize("document", NOT_OBJECTS)
def test_a_result_file_that_is_not_an_object_is_no_verdict_not_not_run(
        monkeypatch, tmp_path, capsys, document):
    rc, out = _render(monkeypatch, tmp_path, capsys,
                      {"D02A": Verbatim(document)}, "--verbose")
    assert rc == 0
    assert _tally(out) == (1, 0, 0, 0, 1, 0)
    assert "no verdict" in _line(out, "D02A")
    assert "no verdict" in _line(_board(tmp_path), "| `D02A` |")


def test_a_case_with_no_result_file_is_still_not_run(monkeypatch, tmp_path, capsys):
    """The other half: "not run" must keep meaning that no file exists. A
    repair that called every unscored record broken would pass the tests
    above as easily."""
    rc, out = _render(monkeypatch, tmp_path, capsys, {"D02A": None}, "--verbose")
    assert rc == 0
    assert _tally(out) == (1, 0, 0, 0, 0, 1)
    assert "not run" in _line(out, "D02A")
    assert "not run" in _line(_board(tmp_path), "| `D02A` |")


def test_load_result_tells_an_absent_file_from_a_null_one(monkeypatch, tmp_path):
    monkeypatch.setattr(sc, "RESULTS", tmp_path)
    assert sc.load_result("ABSENT") is None                     # no file: not run
    (tmp_path / "NULLA.json").write_text("null")
    loaded = sc.load_result("NULLA")
    assert "_broken" in loaded and "null" in loaded["_broken"]
    assert "not an object" in loaded["_broken"]
    assert sc.evaluate(CASES["D02A"], loaded)["state"] == sc.NO_VERDICT
    committed = _committed()
    (tmp_path / "D02A.json").write_text(json.dumps(committed))
    assert sc.load_result("D02A") == committed                  # an object loads as itself


# ---- item 4 (#2089): a nested provenance field that is not an object -------

# `evaluate` reads these three as objects -- `measurement_basis` iterates
# `inputs.items()` and `config.items()` -- and tested them only for presence.
NESTED = ["provenance.worktree", "provenance.inputs", "provenance.config"]

# Truthy non-objects: a presence check is satisfied outright.
TRUTHY = [("list", ["x"]), ("string", "x"), ("number", 5)]
# Falsy non-objects: the input that defeats a repair written as
# `prov.get(key) or {}` and then type-tested. The default has already turned
# the value into a valid empty object by the time the test sees it. For the two
# required fields the presence check already refuses them; `config` is the
# optional one it let through.
FALSY = [("empty-list", []), ("false", False), ("zero", 0), ("empty-string", "")]

OMITTED = object()


def _cells(paths, values):
    """(path, value) pairs as parameters, id'd `<path>-<kind>`."""
    return [pytest.param(path, value, id=f"{path}-{kind}")
            for path in paths for kind, value in values]


MALFORMED = _cells(NESTED, TRUTHY) + _cells(["provenance.config"], FALSY)


def _nested(case_id, path, value):
    """A committed record with the field at the dotted `path` replaced by
    `value`. Nothing else is touched, so the required metrics are intact and
    only `path` can account for the verdict."""
    head, last = path.split(".")
    record = _committed(case_id)
    record[head][last] = value
    return record


def _worst_cell(row):
    """The distance column of a verbose row: `--` when the row has none."""
    return re.search(r"(?:pass|fail|no verdict)\s+(--|[\d.]+)\s{2}", row).group(1)


def test_the_committed_record_keeps_its_score():
    """The control for everything below: D02A's distance under the policy as
    it stands is 1.9536 dB of body/noise balance error against a 3 dB
    tolerance. A repair that refused the record or moved this number would be
    caught here and nowhere else -- `test_the_committed_record_still_passes`
    checks the tally, not the distance."""
    verdict = sc.evaluate(CASES["D02A"], _committed())
    assert verdict["state"] == sc.PASS, verdict
    assert verdict["worst"] == pytest.approx(0.6512, abs=1e-4), verdict


@pytest.mark.parametrize("path, value", MALFORMED)
def test_a_nested_provenance_field_that_is_not_an_object_is_no_verdict(path, value):
    """The metrics are the committed ones, which pass. The only thing wrong
    with the record is `path`, and that alone must withhold the verdict -- and
    name the field, so the reader knows which part of the file to open."""
    verdict = sc.evaluate(CASES["D02A"], _nested("D02A", path, value))
    assert verdict["state"] == sc.NO_VERDICT and verdict["worst"] is None, verdict
    assert path in verdict["why"], verdict


GONE = [pytest.param(OMITTED, id="omitted"), pytest.param(None, id="null"),
        pytest.param({}, id="empty-object")]


@pytest.mark.parametrize("gone", GONE)
@pytest.mark.parametrize("field", ["worktree", "inputs"])
def test_a_missing_required_nested_field_is_still_refused_by_name(field, gone):
    """The presence check, unchanged: absent, null and empty all leave the
    reader without the field, and the reason still names it."""
    record = _committed()
    if gone is OMITTED:
        del record["provenance"][field]
    else:
        record["provenance"][field] = gone
    verdict = sc.evaluate(CASES["D02A"], record)
    assert verdict["state"] == sc.NO_VERDICT and verdict["worst"] is None, verdict
    assert field in verdict["why"], verdict


@pytest.mark.parametrize("config", GONE)
def test_an_optional_config_that_is_omitted_null_or_empty_is_still_valid(config):
    """`config` is optional, so "none" is an answer in every form a writer
    uses for it. JSON `null` is the one non-object that reads as omitted
    rather than as a shape error: it is what an absent field already gives
    through `.get`, and `measurement_basis` has always read it as no config.
    The required fields refuse it as missing (the test above)."""
    record = _committed()
    if config is not OMITTED:
        record["provenance"]["config"] = config
    verdict = sc.evaluate(CASES["D02A"], record)
    assert verdict["state"] == sc.PASS, verdict
    assert verdict["worst"] == pytest.approx(0.6512, abs=1e-4), verdict


@pytest.mark.parametrize("path, value", MALFORMED)
def test_the_board_renders_a_malformed_nested_field_and_keeps_its_neighbour(
        monkeypatch, tmp_path, capsys, path, value):
    """Through `main()` with `--verbose --markdown --check`: the malformed row
    is rendered with its reason and no distance, counted once under `no
    verdict`, and the board exits 0. The well-formed row beside it keeps the
    verdict and distance it has on a board of its own."""
    # `_render` builds its repo under `tmp_path` and will not build it twice,
    # so the control gets a directory of its own.
    control = tmp_path / "control"
    _, alone = _render(monkeypatch, control, capsys,
                       {"D09A": _committed("D09A")}, "--verbose")
    neighbour = _line(_board(control), "| `D09A` |")
    rc, out = _render(monkeypatch, tmp_path, capsys,
                      {"D02A": _nested("D02A", path, value),
                       "D09A": _committed("D09A")}, "--verbose", "--check")
    assert rc == 0, out
    assert _tally(out) == (2, 1, 1, 0, 1, 0), out
    row = _line(out, "D02A")
    assert "no verdict" in row and path in row and _worst_cell(row) == "--", row
    assert _line(out, "D09A") == _line(alone, "D09A")
    malformed = _line(_board(tmp_path), "| `D02A` |")
    assert "| ⚠️ no verdict |  |" in malformed and path in malformed, malformed
    assert _line(_board(tmp_path), "| `D09A` |") == neighbour


@pytest.mark.parametrize("path, value", MALFORMED)
def test_an_evaluated_malformed_row_is_incomparable_with_a_clean_one(path, value):
    """`compare` takes what `evaluate` returned. Before the repair that was a
    PASS row carrying a list where the provenance should be: for `worktree`
    `compare` answered REJECT -- no required property improved -- and for the
    other two it raised. A record that cannot be scored cannot be compared."""
    clean = sc.evaluate(CASES["D02A"], _committed())
    malformed = sc.evaluate(CASES["D02A"], _nested("D02A", path, value))
    assert sc.compare(malformed, clean)["verdict"] == sc.INCOMPARABLE
    assert sc.compare(clean, malformed)["verdict"] == sc.INCOMPARABLE

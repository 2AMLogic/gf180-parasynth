"""Three ways `tools/scorecard.py` misreported a record it could not score
(issue #1669, items 1-3), each kept as the control its repair must stay green
against (`docs/verification-rules.md` rule 8).

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


def _render(monkeypatch, tmp_path, capsys, results, *flags, markdown="docs/scorecard/BOARD.md",
            root=None, asked_of_git=False):
    """`tools/scorecard.py` over a results directory of the caller's making.

    `ROOT` is a directory of this test's own, so "outside the repository" is
    a property of the test rather than of wherever pytest put `tmp_path`.
    That directory is not a git repository, so #2043's question -- is the
    record's commit on origin/main -- could only be answered "no" from it.
    It is answered "yes" for the tests that are not about that question;
    the tests that are give `root` a real clone and pass `asked_of_git`.
    Returns `(rc, stdout)`. An entry of `None` is a case with no result file."""
    repo = tmp_path / "repo"
    (repo / "results").mkdir(parents=True, exist_ok=True)      # a test may render twice
    for stale in (repo / "results").glob("*.json"):
        stale.unlink()
    for case_id, record in results.items():
        if record is None:
            continue
        (repo / "results" / f"{case_id}.json").write_text(
            record if isinstance(record, Verbatim) else json.dumps(record))
    monkeypatch.setattr(sc, "ROOT", root or repo)
    monkeypatch.setattr(sc, "RESULTS", repo / "results")
    monkeypatch.setattr(sc, "load_cases", lambda: [dict(CASES[cid]) for cid in results])
    monkeypatch.setattr(sc, "checkout_staleness", lambda: None)
    if not asked_of_git:
        monkeypatch.setattr(sc, "not_on_origin_main", lambda res: None)
    argv = ["scorecard.py", *(["--markdown", markdown] if markdown else []), *flags]
    monkeypatch.setattr(sys, "argv", argv)
    rc = sc.main()
    return rc, capsys.readouterr().out


def _line(text, prefix):
    return next(line for line in text.splitlines() if line.startswith(prefix))


def _tally(out):
    """(cases, valid, pass, fail, no verdict, not run) off the TOTAL line."""
    return tuple(int(n) for n in re.findall(r"\d+", _line(out, "TOTAL")))


def _board(tmp_path, root=None):
    return ((root or tmp_path / "repo") / "docs" / "scorecard" / "BOARD.md").read_text()


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


# ---- #2043: a record whose commit is not on origin/main ---------------------
#
# The board asks this when it READS a record, not when one is written. Records
# are written on branches; a merge commit is what puts the commit on main. A
# board that trusted a `false` recorded at write time would refuse every record
# any pull request ever produced, for good -- the committed D02A among them,
# written on feature/issue-290. So the refusal is `board_verdict`'s, over the
# real `tools/provenance.py` helper and a real clone; `evaluate` is untouched.

NOBODY_HAS_IT = "0" * 40                       # git's null id: never an object


GIT_ID = ("-c", "user.email=t@t", "-c", "user.name=t", "-c", "commit.gpgsign=false")


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *GIT_ID, *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def _commit(repo, message="c"):
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", message)


def _history(tmp_path):
    """`work`, a clone of `up` whose `main` holds one commit, plus a second
    commit on a branch of `work` that origin has never seen. The tree of
    `work` is clean either way. Returns `(up, work, on_main, off_main)`."""
    up, w = tmp_path / "up", tmp_path / "work"
    subprocess.run(["git", "init", "-q", "-b", "main", str(up)], check=True)
    (up / "f").write_text("x")
    _commit(up)
    subprocess.run(["git", "clone", "-q", str(up), str(w)], check=True)
    on_main = _git(w, "rev-parse", "HEAD")
    _git(w, "checkout", "-q", "-b", "feature")
    (w / "g").write_text("g")
    _commit(w)
    assert _git(w, "status", "--porcelain") == ""
    return up, w, on_main, _git(w, "rev-parse", "HEAD")


def _citing(case_id, commit):
    """The committed record, citing `commit` instead of the one it was
    written at -- the only change, as `_committed` changes one field."""
    record = _committed(case_id)
    record["source_commit"] = commit
    record["provenance"]["worktree"]["commit"] = commit
    return record


def _board_says(monkeypatch, tmp_path, capsys, w, case_id, commit, *flags):
    rc, out = _render(monkeypatch, tmp_path, capsys, {case_id: _citing(case_id, commit)},
                      "--verbose", *flags, root=w, asked_of_git=True)
    return rc, out, _line(out, case_id)


def test_the_real_record_at_a_commit_on_origin_main_is_scored(tmp_path, monkeypatch, capsys):
    """The control for every refusal below: the committed D02A, moved to a
    commit that IS on origin/main, is the pass it is on the real board. A
    `board_verdict` that refused everything would pass each of those."""
    _, w, on_main, _ = _history(tmp_path)
    rc, out, row = _board_says(monkeypatch, tmp_path, capsys, w, "D02A", on_main)
    assert rc == 0 and _tally(out) == (1, 1, 1, 0, 0, 0)
    assert "pass" in row and "REFUSED" not in row
    assert "pass" in _line(_board(tmp_path, w), "| `D02A` |")


def test_a_record_at_a_commit_that_is_only_on_a_branch_is_refused(
        tmp_path, monkeypatch, capsys):
    """Whatever its metrics say. `no verdict` -- neither pass nor fail -- with
    a reason that says it is a refusal and names the commit."""
    _, w, _, off_main = _history(tmp_path)
    rc, out, row = _board_says(monkeypatch, tmp_path, capsys, w, "D02A", off_main)
    assert rc == 0
    assert _tally(out) == (1, 0, 0, 0, 1, 0)
    assert "no verdict" in row and f"REFUSED: {off_main[:12]} is not on origin/main" in row
    assert "pass" not in row.split("no verdict")[1]
    assert "no verdict" in _line(_board(tmp_path, w), "| `D02A` |")
    board_row = _line(_board(tmp_path, w), "| `D02A` |")
    assert f"REFUSED: {off_main[:12]} is not on origin/main" in board_row
    why = sc.board_verdict(CASES["D02A"], _citing("D02A", off_main))["why"]
    assert why == f"REFUSED: {off_main[:12]} is not on origin/main (answer: False)"


def test_the_refusal_lifts_once_a_merge_commit_puts_the_commit_on_origin_main(
        tmp_path, monkeypatch, capsys):
    """Before the fetch it is still refused: the board reads the reader's
    origin/main, which is the only main it can show anybody."""
    up, w, _, off_main = _history(tmp_path)
    assert "REFUSED" in _board_says(monkeypatch, tmp_path, capsys, w, "D02A", off_main)[2]
    _git(w, "push", "-q", "origin", "feature")
    _git(up, "merge", "-q", "--no-ff", "feature")
    assert "REFUSED" in _board_says(monkeypatch, tmp_path, capsys, w, "D02A", off_main)[2]
    _git(w, "fetch", "-q", "origin")
    rc, out, row = _board_says(monkeypatch, tmp_path, capsys, w, "D02A", off_main)
    assert rc == 0 and _tally(out) == (1, 1, 1, 0, 0, 0) and "pass" in row


def test_a_squash_merge_does_not_lift_the_refusal(tmp_path, monkeypatch, capsys):
    """The shape the helper docstring assumes this repository does not use:
    the squash commit is on main, the commit the record cites never is.

    Two commits on the branch, and a message of the squash's own. A branch of
    ONE commit, squashed with the same author in the same second under the
    same message, is the branch commit -- byte for byte, so the same object,
    so reachable. The precondition below is what showed that; the first
    version of this test passed by fast-forwarding in disguise."""
    up, w, _, off_main = _history(tmp_path)
    (w / "h").write_text("h")
    _commit(w, "second")
    _git(w, "push", "-q", "origin", "feature")
    _git(up, "merge", "-q", "--squash", "feature")
    _commit(up, "squashed")
    _git(w, "fetch", "-q", "origin")
    assert _git(w, "rev-parse", "origin/main") != off_main           # the precondition
    rc, out, row = _board_says(monkeypatch, tmp_path, capsys, w, "D02A", off_main)
    assert rc == 0 and _tally(out) == (1, 0, 0, 0, 1, 0) and "REFUSED" in row


def test_a_record_citing_a_commit_no_checkout_has_is_refused(tmp_path, monkeypatch, capsys):
    """Fourteen of the twenty-six committed records were in this state at
    `616b8d63` (2026-11-28): their commit in no object store and under no
    remote ref."""
    _, w, _, _ = _history(tmp_path)
    present = subprocess.run(["git", "-C", str(w), "cat-file", "-e", NOBODY_HAS_IT])
    assert present.returncode != 0                                    # the precondition
    rc, out, row = _board_says(monkeypatch, tmp_path, capsys, w, "D02A", NOBODY_HAS_IT)
    assert rc == 0 and _tally(out) == (1, 0, 0, 0, 1, 0)
    # The console row keeps 44 characters of `why`: the refusal and the id,
    # whole. The answer is in the full reason, and on the markdown board.
    assert f"REFUSED: {NOBODY_HAS_IT[:12]} is not on origin/main" in row
    why = sc.board_verdict(CASES["D02A"], _citing("D02A", NOBODY_HAS_IT))["why"]
    assert why == f"REFUSED: {NOBODY_HAS_IT[:12]} is not on origin/main (answer: None)"


@pytest.mark.parametrize("name", ["HEAD", "origin/main", "main"])
def test_a_record_citing_a_moving_name_as_its_commit_is_refused(
        tmp_path, monkeypatch, capsys, name):
    """The input that defeats this guard (verification-rules rule 8). Each of
    these names IS an ancestor of origin/main -- git says so here, in a real
    clone standing on main -- so asking git about the recorded string would
    pass a record that cites nothing any later reader can check out."""
    _, w, _, _ = _history(tmp_path)
    _git(w, "checkout", "-q", "main")
    asked = subprocess.run(["git", "-C", str(w), "merge-base", "--is-ancestor", name,
                            "origin/main"])
    assert asked.returncode == 0                                      # what makes it an adversary
    rc, out, row = _board_says(monkeypatch, tmp_path, capsys, w, "D02A", name)
    assert rc == 0 and _tally(out) == (1, 0, 0, 0, 1, 0)
    assert f"REFUSED: {name[:12]} is not on origin/main" in row
    assert sc.board_verdict(CASES["D02A"], _citing("D02A", name))["why"].endswith("(answer: None)")


def test_a_record_with_no_commit_at_all_is_refused(tmp_path, monkeypatch, capsys):
    _, w, _, _ = _history(tmp_path)
    rc, out, row = _board_says(monkeypatch, tmp_path, capsys, w, "D02A", "?")
    assert rc == 0 and _tally(out) == (1, 0, 0, 0, 1, 0)
    assert "REFUSED" in row


def test_a_record_the_board_already_declines_keeps_its_own_reason(tmp_path, monkeypatch, capsys):
    """Reachability is asked only of a record the board would otherwise score.
    D04A is `invalid: decay` wherever its commit is, and that is the reason
    its reader can act on."""
    _, w, _, off_main = _history(tmp_path)
    rc, out, row = _board_says(monkeypatch, tmp_path, capsys, w, "D04A", off_main)
    assert rc == 0 and _tally(out) == (1, 0, 0, 0, 1, 0)
    assert "invalid: decay" in row and "REFUSED" not in row


def test_evaluate_does_not_ask_git_so_a_writer_on_a_branch_keeps_its_verdict(
        tmp_path, monkeypatch):
    """`run_case.py` writes `provenance.outcome_code` from `evaluate` as it
    writes a record -- on a branch, before any merge. With the refusal inside
    `evaluate`, every record a pull request produced would carry outcome_code 2
    for good, and read inconsistent on the day its commit reached main."""
    _, w, _, off_main = _history(tmp_path)
    record = _citing("D02A", off_main)
    monkeypatch.setattr(sc, "ROOT", w)
    assert sc.evaluate(CASES["D02A"], record)["state"] == sc.PASS
    assert sc.board_verdict(CASES["D02A"], record)["state"] == sc.NO_VERDICT


def test_check_does_not_call_a_refused_record_inconsistent(tmp_path, monkeypatch, capsys):
    """`--check` compares a SCORED row with its record's outcome_code. D02A
    carries 0; refused, its row is no verdict, and 0 beside no verdict must
    not read as the board contradicting itself."""
    assert _committed()["provenance"]["outcome_code"] == 0            # the precondition
    _, w, _, off_main = _history(tmp_path)
    rc = _render(monkeypatch, tmp_path, capsys, {"D02A": _citing("D02A", off_main)},
                 "--check", markdown=None, root=w, asked_of_git=True)[0]
    assert rc == 0


def test_the_board_says_how_many_records_it_refused(tmp_path, monkeypatch, capsys):
    up, w, _, off_main = _history(tmp_path)
    out = _render(monkeypatch, tmp_path, capsys,
                  {"D02A": _citing("D02A", off_main), "D09A": _citing("D09A", off_main)},
                  root=w, asked_of_git=True)[1]
    line = _line(out, "Refused (2):")
    assert "D02A" in line and "D09A" in line and "origin/main" in line
    _git(w, "push", "-q", "origin", "feature")
    _git(up, "merge", "-q", "--no-ff", "feature")
    _git(w, "fetch", "-q", "origin")
    out = _render(monkeypatch, tmp_path, capsys,
                  {"D02A": _citing("D02A", off_main), "D09A": _citing("D09A", off_main)},
                  root=w, asked_of_git=True)[1]
    assert "Refused (" not in out                                   # and says nothing once none is

"""Controls for `tools/refusal_stream_consumers.py` (issue #1988).

The census answers one question -- does any in-repo consumer capture ONE
stream of a tool whose refusal lines split across streams, and so lose a
reason? -- and a "no" is worth exactly what the instrument that produced it
is worth.  These are its controls, on a small tree built for the purpose:

  * every verdict the census can print is reached by a consumer written to
    earn it, so NO-LOSS is a classification and never a default;
  * the consumer #1988 asked about -- one stream captured, text read, the
    tool's other stream unheard -- is EXPOSED, not NO-LOSS: a census that
    cannot name a loser cannot refute one;
  * each precondition REFUSES rather than answering: a partial tree, a
    runner that stopped merging, a launch it cannot classify;
  * the reading #1988's table was not taken with: a wrapped print counts as
    stdout to the grep recipe and as stderr to the AST.

The real-tree test asserts the SHAPE of an answer, never its contents.
Which files split and which consumers read one stream drift with every
merge, and a pin that reds on other people's work gets trained away
(tools/refusal_exit_census.py records that lesson at length).  What the
census found on the tree it was written against is in the issue, dated.
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import refusal_stream_consumers as census_mod                     # noqa: E402

SPLITTY = "tools/splitty.py"
QUIET = "tools/quiet_err.py"

RUN_ALL = """\
import subprocess
def run_one(cmd):
    return subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
"""

TREE = {
    "tools/run_all.py": RUN_ALL,
    SPLITTY: """\
import sys
REFUSED = 2
def main():
    if "--corpus" in sys.argv:
        print("REFUSED: no corpus", file=sys.stderr)
        return REFUSED
    print("REFUSED: controls did not pass")
    return 1
""",
    QUIET: """\
import sys
def main():
    print("REFUSED: only ever stderr", file=sys.stderr)
    return 2
""",
    "tools/c_merged.py": """\
import subprocess, sys
def go():
    p = subprocess.run([sys.executable, "tools/splitty.py"], stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, text=True)
    print(p.stdout)
""",
    "tools/c_pinned.py": """\
import subprocess, sys
def go():
    r = subprocess.run([sys.executable, "tools/splitty.py", "--corpus"],
                       capture_output=True, text=True)
    assert r.returncode == 2 and "REFUSED" in r.stderr
""",
    "tools/c_exposed.py": """\
import subprocess, sys
def go():
    p = subprocess.run([sys.executable, "tools/splitty.py", "--corpus"],
                       stdout=subprocess.PIPE, text=True)
    print(p.stdout or "(no reason captured)")
""",
    "tools/c_status.py": """\
import subprocess, sys
def go():
    return subprocess.run([sys.executable, "tools/splitty.py", "--corpus"],
                          stdout=subprocess.PIPE).returncode
""",
    "tools/c_via_helper.py": """\
import subprocess, sys
def run(tool):
    p = subprocess.run([sys.executable, tool], capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr
def go():
    return run("tools/splitty.py")
""",
    "tools/c_runner.py": """\
from run_all import run_one
def go():
    return run_one("python tools/splitty.py")
""",
    "tools/c_inproc.py": """\
import splitty as s
def test_reads_stdout_only(capsys):
    assert s.main([]) == 1
    assert "REFUSED" in capsys.readouterr().out
""",
    "tools/c_data.py": """\
import subprocess
def go():
    return subprocess.run(["git", "show", "HEAD:tools/splitty.py"], capture_output=True)
""",
    "tools/c_mention.py": '"""Runs tools/splitty.py by hand; reads nothing here."""\n',
    "tools/c_specs.py": """\
import subprocess
def spec(entry):
    return {"entry": entry}
def go():
    subprocess.run(["git", "status"], capture_output=True)
    return spec("tools/splitty.py")
""",
    "tools/c_quiet_wrong.py": """\
import subprocess, sys
def go():
    p = subprocess.run([sys.executable, "tools/quiet_err.py"], stdout=subprocess.PIPE, text=True)
    print(p.stdout or "(nothing)")
""",
    "tools/c_quiet_right.py": """\
import subprocess, sys
def go():
    r = subprocess.run([sys.executable, "tools/quiet_err.py"], capture_output=True, text=True)
    assert "REFUSED" in r.stderr
""",
    "tools/wrapped.py": """\
import sys
def main():
    print("REFUSED: a wrapped print",
          file=sys.stderr)
    return 2
""",
    "tools/prose.py": """\
import sys
def main():
    print("REFUSED: the real one", file=sys.stderr)
    print("2 rows were REFUSED, see above")
""",
    "Makefile": """\
RUN := python3 tools/run_all.py
PY := python3
check:
\t@$(RUN) --timeout 10 "python3 tools/splitty.py" "$(PY) tools/splitty.py --corpus"
direct:
\t@$(PY) tools/splitty.py
merged:
\tpython3 tools/splitty.py 2>&1 | cat
""",
    ".github/workflows/ci.yml": """\
name: ci
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - run: python tools/splitty.py
      - name: two
        run: |
          echo hi
          python tools/splitty.py > out.txt 2>&1
          make check
""",
}


def build(tmp_path: pathlib.Path, **override) -> tuple[pathlib.Path, list[str]]:
    tree = {**TREE, **override}
    for rel, text in tree.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return tmp_path, sorted(tree)


def rows(res: dict, tool: str, reader: str, kind: str = "") -> list:
    return [r for r in res["rows"] if r.tool == tool and r.file == reader and r.kind.startswith(kind)]


RANK = {"NO-LOSS": 0, "HAND-TRACE": 1, "PINNED": 2, "EXPOSED": 3}


def verdicts(res: dict, tool: str) -> dict[str, str]:
    """Per reader, its most serious verdict: a file can carry a NO-LOSS
    mention beside a HAND-TRACE row, and the mention must not mask it."""
    out: dict[str, str] = {}
    for r in res["rows"]:
        if r.tool == tool and RANK[r.verdict] > RANK.get(out.get(r.file, "NONE"), -1):
            out[r.file] = r.verdict
    return out


@pytest.fixture(scope="module")
def real():
    return census_mod.census()


def test_the_real_tree_gets_an_answer_whose_shape_is_complete(real):
    """Every file #1988 named is answered, every site and row falls in the
    census's vocabulary, and the runner's capture was read, not assumed."""
    assert real["runner_capture"] == "MERGED"
    assert set(census_mod.ISSUE_1988_FILES) <= set(real["sites"])
    for sites in real["sites"].values():
        assert sites, "a file with no refusal line is not a site entry"
        assert all(s.stream in (*census_mod.STREAMS, "unknown") for s in sites)
        assert all(s.kind in ("print", "write", "exit", "call") for s in sites)
    assert set(real["split"]) <= set(real["sites"])
    assert all({s.stream for s in real["sites"][f]} == set(census_mod.STREAMS)
               for f in real["split"])
    for r in real["rows"]:
        assert r.verdict in ("NO-LOSS", "PINNED", "EXPOSED", "HAND-TRACE")
        assert r.kind in ("subprocess", "in-process", "make", "ci", "data", "import",
                          "mention", "hand-trace")
        assert r.capture in ("", "MERGED", "SEPARATE", "STDOUT-ONLY", "STDERR-ONLY",
                             "LOG", "NOT-READ", "DISCARDED")
        assert r.reads <= set(census_mod.STREAMS)


def test_cli_renders_the_census_the_three_ways(real, monkeypatch, capsys):
    """The exact CLI paths, fed a census so the real tree is read once."""
    monkeypatch.setattr(census_mod, "census", lambda: real)
    assert census_mod.main([]) == 0
    assert "rows by verdict" in capsys.readouterr().out
    assert census_mod.main(["--sites"]) == 0
    assert "#1988's TABLE" in capsys.readouterr().out
    assert census_mod.main(["--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["split"] == real["split"] and len(doc["rows"]) == len(real["rows"])


def test_the_grep_recipe_reads_a_wrapped_print_as_stdout_and_the_ast_as_stderr(tmp_path):
    """WRONG BEFORE IT WAS RIGHT: #1988's table counted `file=sys.stderr`
    only when it shared the `print(`'s line, so nine stderr-only files read
    as split.  One wrapped print is the whole defect."""
    root, files = build(tmp_path)
    text = (root / "tools/wrapped.py").read_text()
    assert census_mod._grep_recipe(text) == (1, 0)
    sites = census_mod.refusal_sites(root, files)
    assert [(s.file, s.stream) for s in sites if s.file == "tools/wrapped.py"] == \
        [("tools/wrapped.py", "stderr")]


def test_a_split_tool_and_every_consumer_verdict(tmp_path):
    """One small tree in which each verdict is earned by a consumer written
    for it.  EXPOSED is the shape #1988 asked about."""
    root, files = build(tmp_path)
    res = census_mod.census(root, files)
    assert res["split"] == [SPLITTY]
    got = verdicts(res, SPLITTY)
    assert got == {
        "tools/c_merged.py": "NO-LOSS",
        "tools/c_pinned.py": "PINNED",
        "tools/c_exposed.py": "EXPOSED",
        "tools/c_status.py": "NO-LOSS",
        "tools/c_via_helper.py": "NO-LOSS",
        "tools/c_runner.py": "NO-LOSS",
        "tools/c_inproc.py": "PINNED",
        "tools/c_data.py": "NO-LOSS",
        "tools/c_mention.py": "NO-LOSS",
        "tools/c_specs.py": "HAND-TRACE",
        "Makefile": "NO-LOSS",
        ".github/workflows/ci.yml": "NO-LOSS",
    }
    by_file = {r.file: r for r in res["rows"] if r.tool == SPLITTY and r.kind != "mention"}
    assert by_file["tools/c_merged.py"].capture == "MERGED"
    assert (by_file["tools/c_exposed.py"].capture, by_file["tools/c_exposed.py"].reads) == \
        ("STDOUT-ONLY", frozenset({"stdout"}))
    assert (by_file["tools/c_status.py"].capture, by_file["tools/c_status.py"].reads) == \
        ("STDOUT-ONLY", frozenset())
    assert (by_file["tools/c_pinned.py"].capture, by_file["tools/c_pinned.py"].reads) == \
        ("SEPARATE", frozenset({"stderr"}))
    assert (by_file["tools/c_inproc.py"].capture, by_file["tools/c_inproc.py"].reads) == \
        ("SEPARATE", frozenset({"stdout"}))
    via = by_file["tools/c_via_helper.py"]
    assert via.capture == "SEPARATE" and via.reads == frozenset({"stdout", "stderr"})
    assert "run() call sites" in via.note
    assert by_file["tools/c_runner.py"].note == "run_all.run_one"
    assert by_file["tools/c_data.py"].kind == "data" and "`git`" in by_file["tools/c_data.py"].note
    assert rows(res, SPLITTY, "tools/c_mention.py", "mention")
    assert "launches nothing" in by_file["tools/c_specs.py"].note
    assert rows(res, SPLITTY, "tools/c_specs.py", "mention")
    make = rows(res, SPLITTY, "Makefile")
    assert [(r.capture, "target check" in r.note) for r in make] == \
        [("MERGED", True), ("MERGED", True), ("LOG", False), ("MERGED", False)]
    ci = [r.capture for r in rows(res, SPLITTY, ".github/workflows/ci.yml")]
    assert ci == ["LOG", "MERGED", ""]


def test_the_exposed_row_names_the_stream_the_consumer_cannot_hear(tmp_path, monkeypatch):
    """The control for the one above: with the tool's stderr line removed
    the same stdout-only consumer is NO-LOSS -- the EXPOSED came from the
    stream it cannot hear, not from its capture class alone.  The tool stays
    a target (it is named) so the control is not vacuous."""
    monkeypatch.setattr(census_mod, "ISSUE_1988_FILES", (SPLITTY,))
    one_stream = TREE[SPLITTY].replace('        print("REFUSED: no corpus", file=sys.stderr)\n', "")
    root, files = build(tmp_path, **{SPLITTY: one_stream})
    res = census_mod.census(root, files)
    assert res["split"] == []
    got = verdicts(res, SPLITTY)
    assert got["tools/c_exposed.py"] == "NO-LOSS"
    assert got["tools/c_pinned.py"] == "NO-LOSS"


def test_a_single_stream_tool_is_traced_when_it_is_named(tmp_path, monkeypatch):
    """The nine files of #1988 that turned out not to split are still
    traced, because the census names them; there the exposure is the whole
    refusal, not part of it."""
    monkeypatch.setattr(census_mod, "ISSUE_1988_FILES", (QUIET,))
    root, files = build(tmp_path)
    got = verdicts(census_mod.census(root, files), QUIET)
    assert got == {"tools/c_quiet_wrong.py": "EXPOSED", "tools/c_quiet_right.py": "NO-LOSS"}


def test_a_mention_that_is_not_a_refusal_line_is_counted_not_assumed(tmp_path):
    """The stricter rule has a cost and the census measures it: a file whose
    only stdout mention is mid-line does not split, and is listed as one
    that would under the looser rule."""
    root, files = build(tmp_path)
    res = census_mod.census(root, files)
    assert "tools/prose.py" not in res["split"]
    assert res["loose_only_split"] == ["tools/prose.py"]
    assert res["n_loose_only_sites"] == 1
    assert [s.line for s in res["sites"]["tools/prose.py"]] == [3]


def test_a_command_the_census_cannot_follow_is_listed_not_dropped(tmp_path):
    """A job string built in one function and launched from another is past
    the census's reach.  It must surface as a row to trace by hand -- this is
    how tools/deadline_reanalyse.py was found -- while a path named in prose
    must not."""
    root, files = build(tmp_path, **{
        "tools/c_jobs.py": """\
import subprocess, sys
def job_cmds(py):
    return {"a": f"{py} tools/splitty.py --corpus"}
def go():
    for name, cmd in job_cmds(sys.executable).items():
        subprocess.run([sys.executable, "tools/run_all.py", cmd], capture_output=True)
""",
        "tools/c_prose.py": """\
import subprocess
NOTE = "see tools/splitty.py for the history"
def go():
    subprocess.run(["git", "status"], capture_output=True)
"""})
    res = census_mod.census(root, files)
    traced = [r for r in res["rows"] if r.kind == "hand-trace" and r.tool == SPLITTY
              and r.file != "tools/c_specs.py"]
    assert [r.file for r in traced] == ["tools/c_jobs.py"]
    assert "names splitty.py in code" in traced[0].note
    assert rows(res, SPLITTY, "tools/c_prose.py", "mention")


def test_an_unknown_stream_is_reported_not_guessed(tmp_path):
    root, files = build(tmp_path, **{"tools/unknown.py": """\
import sys
LOG = None
def main():
    print("REFUSED: to a handle", file=LOG)
    return 2
"""})
    sites = {s.file: s.stream for s in census_mod.refusal_sites(root, files)
             if s.file == "tools/unknown.py"}
    assert sites == {"tools/unknown.py": "unknown"}
    res = census_mod.census(root, files)
    assert census_mod._state(res, "tools/unknown.py") == "stream unreadable"


def test_a_partial_tree_is_refused(tmp_path):
    """A census over a few files reads exactly like one over the repository."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools/splitty.py").write_text(TREE[SPLITTY])
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    with pytest.raises(census_mod.Refused, match="under the 300"):
        census_mod.census(tmp_path)


def test_a_runner_that_stopped_merging_is_refused(tmp_path, monkeypatch, capsys):
    """Every runner row's MERGED is read from tools/run_all.py.  Take the
    merge away and the census REFUSES (exit 2, on stderr) rather than
    reporting the stale assumption."""
    root, files = build(tmp_path, **{"tools/run_all.py": RUN_ALL.replace(
        "subprocess.STDOUT", "subprocess.PIPE")})
    with pytest.raises(census_mod.Refused, match="no longer merges"):
        census_mod.census(root, files)
    monkeypatch.setattr(census_mod, "ROOT", root)
    monkeypatch.setattr(census_mod, "tracked", lambda r: files)
    monkeypatch.setattr(census_mod, "MIN_PY_FILES", 1)
    assert census_mod.main([]) == 2
    err = capsys.readouterr()
    assert err.err.startswith("REFUSED: tools/run_all.py") and err.out == ""


def test_a_launch_the_census_cannot_classify_is_refused(tmp_path):
    root, files = build(tmp_path, **{"tools/c_odd.py": """\
import subprocess, sys
def go(mode):
    return subprocess.run([sys.executable, "tools/splitty.py"], stdout=subprocess.PIPE,
                          stderr=mode)
"""})
    with pytest.raises(census_mod.Refused, match="cannot classify"):
        census_mod.census(root, files)


def test_the_cli_on_a_small_tree(tmp_path, monkeypatch, capsys):
    root, files = build(tmp_path)
    monkeypatch.setattr(census_mod, "ROOT", root)
    monkeypatch.setattr(census_mod, "tracked", lambda r: files)
    monkeypatch.setattr(census_mod, "MIN_PY_FILES", 1)
    assert census_mod.main([]) == 0
    out = capsys.readouterr().out
    assert f"{SPLITTY}  [SPLIT]" in out and "EXPOSED" in out and "HAND-TRACE" in out
    assert census_mod.main(["--sites"]) == 0
    assert "1 file(s) would split only under that looser rule" in capsys.readouterr().out

"""Controls for the #1929 and #2014 site searches: what each must find, and what
must turn it red.

`tools/nan_guard_audit.py --sites` is a triage list with one verdict per
comparison site, `--aggregates` the same for every builtin `max()` / `min()` a
NaN can be dropped by, and `--check` beside either is the claim that the list
is complete. A list nobody has seen miss a site, or pass one with no verdict,
is not a control; the misses are here:

  * a site in a `#` comment, a docstring or any other string is NOT a site --
    #1870's own docstring was the filed count's 71st line;
  * a site with no verdict, a verdict whose line was edited, a verdict naming
    a control that does not exist, a verdict word outside the vocabulary: each
    is a gap, and each is exercised;
  * what each search is blind to is asserted as blind, not left to be found.

The two tests that are standing gates are
`test_the_committed_table_is_complete_fresh_and_its_controls_exist` and
`test_the_committed_aggregate_table_is_complete_fresh_and_its_controls_exist`.
They make a new NaN-blind comparison, or a new builtin `max()` / `min()` with a
non-literal argument in a function named for a verdict, in a non-test file
under tools/, model/ or fpga/ (outside tools/probes/) fail `make verify` until
it has a verdict.
"""

from __future__ import annotations

import ast
import math
import pathlib
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import nan_guard_audit as nga  # noqa: E402

#: One site of each shape, and one line of each kind that is NOT a site: a
#: docstring, a string and a `#` comment carry shape A's text and are not sites.
#: The fails-closed form is not a site either; a comparison wrapped across two
#: lines is not found. Line numbers are read off the text below by `_line`,
#: never counted here.
TOOL = '''"""A docstring line such as abs(doc - string) > tol is not a comparison."""
BOUND = 1.0
QUOTED = ("if abs(in - a_string) > tol: raise Refused",)
# if abs(in - a_comment) > tol:
    raise Refused("comment")

def f(a, b, x):
    if abs(a - b) > tol:
        raise Refused("shape A")
    if x > X_BOUND:
        raise Refused("shape B")
    if not (abs(a - b) <= tol):
        raise Refused("the fails-closed form: True for a NaN, so not a site")
    if abs(a -
           b) > tol:
        raise Refused("wrapped across lines: not found, see the blind-spot test")
'''
TEST_FILE = '''def test_x(a, b):
    assert abs(a - b) > tol
'''
PROBE = '''if abs(a - b) > tol:
    raise Refused("a probe")
'''
KEY_A = ("tools/tool.py", "A", "if abs(a - b) > tol:")
KEY_B = ("tools/tool.py", "B", "if x > X_BOUND:")
NOWHERE = pathlib.Path("/nonexistent-root-for-triage-gaps")

def _line(text: str, needle: str) -> int:
    assert text.count(needle) == 1, needle
    return text[:text.index(needle)].count("\n") + 1

L_DOC = _line(TOOL, '"""A docstring line')
L_STR = _line(TOOL, 'QUOTED = ("if abs(in - a_string) > tol:')
L_CMT = _line(TOOL, "# if abs(in - a_comment) > tol:")
L_A = _line(TOOL, "    if abs(a - b) > tol:")
L_B = _line(TOOL, "    if x > X_BOUND:")
L_CLOSED = _line(TOOL, "    if not (abs(a - b) <= tol):")
L_WRAP = _line(TOOL, "    if abs(a -\n")
L_TEST = _line(TEST_FILE, "    assert abs(a - b) > tol")
L_PROBE = _line(PROBE, "if abs(a - b) > tol:")

def _sites(**extra):
    src = {"tools/tool.py": TOOL, "tools/test_tool.py": TEST_FILE,
           "tools/probes/probe.py": PROBE, "model/notes.txt": "abs(a - b) > tol"}
    src.update(extra)
    return nga.comparison_sites(src)[0]

# ---------------------------------------------------------------------------
# What is a site
# ---------------------------------------------------------------------------

def test_the_two_shapes_are_the_ones_filed():
    assert nga.SHAPE_A.pattern == r"abs\(.* - .*\)\s*>\s*[\w.]+"
    assert nga.SHAPE_B.pattern == r">\s*[A-Za-z_][\w.]*BOUND\b"
    assert nga.SHAPE_A.search("    if abs(a - b) > tol:")
    assert nga.SHAPE_B.search("    if x > SOME_BOUND:")
    assert nga.SHAPE_B.search("    if x > C.DEVICE_QUEUE_BOUND:")
    # the filed regex needs at least one character before the suffix
    assert not nga.SHAPE_B.search("    if x > BOUND:")
    assert not nga.SHAPE_B.search("    if x > some_bound:")
    assert not nga.SHAPE_A.search("    if not (abs(a - b) <= tol):")

def test_a_site_is_a_shape_on_a_code_line_and_only_that():
    sites = _sites()
    assert [(s.path, s.lineno, s.shape) for s in sites] == [
        ("tools/tool.py", L_A, "A"), ("tools/tool.py", L_B, "B"),
        ("tools/test_tool.py", L_TEST, "A"), ("tools/probes/probe.py", L_PROBE, "A")]
    assert len({L_DOC, L_STR, L_CMT, L_A, L_B, L_CLOSED, L_WRAP}) == 7
    assert [s.text for s in sites[:2]] == ["if abs(a - b) > tol:", "if x > X_BOUND:"]
    assert [s.in_scope for s in sites] == [True, True, False, False]

def test_the_same_text_in_a_string_comment_or_docstring_is_not_a_site():
    """Each of these three lines carries shape A's text and none is a site."""
    sites = _sites()
    assert not [s for s in sites
                if s.path == "tools/tool.py" and s.lineno in (L_DOC, L_STR, L_CMT)]

def test_test_files_and_probes_are_told_apart_by_path():
    assert nga.is_test_path("tools/test_x.py")
    assert nga.is_test_path("tools/x_test.py")
    assert nga.is_test_path("tools/tests/test_x.py")
    assert nga.is_test_path("tools/conftest.py")
    assert not nga.is_test_path("tools/contest.py")
    assert not nga.is_test_path("tools/probes/x.py")
    site = nga.comparison_sites({"tools/probes/x.py": PROBE})[0][0]
    assert site.in_probes and not site.in_scope

# ---------------------------------------------------------------------------
# What the search is blind to, asserted rather than discovered
# ---------------------------------------------------------------------------

def test_a_comparison_wrapped_across_lines_is_not_found():
    """Stated in the module: the shapes are line regexes. This is the proof."""
    assert not [s for s in _sites()
                if s.path == "tools/tool.py" and s.lineno in (L_WRAP, L_WRAP + 1)]
    assert L_CLOSED not in [s.lineno for s in _sites() if s.path == "tools/tool.py"]

def test_shape_b_cannot_see_a_bare_bound_or_a_lowercase_one():
    """Stated in the module, and measured on origin/main @ dd0449e0: no in-scope
    line compares with a name that is exactly `BOUND`, and the one lowercase
    `..._bound` is under tools/probes/, already out of scope."""
    src = "def f(x):\n    if x > BOUND:\n        pass\n    if x > some_bound:\n        pass\n"
    sites, _ = nga.comparison_sites({"tools/b.py": src})
    assert sites == []

def test_a_file_that_does_not_tokenise_is_still_scanned_and_is_named():
    """Without tokens the scanner keeps the filed method, `#` comments only, and
    says so: in such a file a docstring line counts, as #1870's did."""
    broken = ('"""abs(doc - string) > tol is quoted here"""\n'
              "# if abs(in - a_comment) > tol:\n"
              "def f(:\n"
              "    if abs(a - b) > tol:\n")
    sites, untokenised = nga.comparison_sites({"tools/broken.py": broken})
    assert untokenised == ["tools/broken.py"]
    assert [s.lineno for s in sites] == [1, 4]

# ---------------------------------------------------------------------------
# Where the sources come from
# ---------------------------------------------------------------------------

def _git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                          text=True, check=True).stdout

def test_a_ref_is_read_from_git_and_the_working_tree_from_disk(tmp_path):
    (tmp_path / "tools").mkdir()
    f = tmp_path / "tools" / "x.py"
    f.write_text("COMMITTED = 1\n")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "-c", "user.name=t", "-c", "user.email=t@t", "add", "-A")
    _git(tmp_path, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "c")
    f.write_text("UNCOMMITTED = 1\n")
    (tmp_path / "model").mkdir()
    (tmp_path / "tools" / "notes.txt").write_text("not python")
    assert nga.py_sources(None, tmp_path) == {"tools/x.py": "UNCOMMITTED = 1\n"}
    assert nga.py_sources("HEAD", tmp_path) == {"tools/x.py": "COMMITTED = 1\n"}
    with pytest.raises(SystemExit) as exc:
        nga.py_sources("no-such-ref", tmp_path)
    assert "REFUSED" in str(exc.value)

def test_the_tree_a_count_describes_is_named_with_it():
    label = nga.tree_label(None)
    assert label.startswith("working tree @ ")
    assert len(label.split("@ ")[1][:8]) == 8

# ---------------------------------------------------------------------------
# The gate: `--sites --check`
# ---------------------------------------------------------------------------

def test_the_committed_table_is_complete_fresh_and_its_controls_exist():
    """The standing gate. Every in-scope site on this tree has a verdict, every
    verdict still names a site, every control a verdict cites exists as a test,
    and every verdict is one of the three words."""
    sites, untokenised = nga.comparison_sites(nga.py_sources())
    assert untokenised == []
    assert sites, "the search found nothing; it is not running"
    assert nga.triage_gaps(sites) == []
    assert {v[0] for v in nga.SITE_VERDICTS.values()} == set(nga.VERDICTS)

def test_the_cli_check_exits_zero_on_this_tree():
    script = str(pathlib.Path(nga.__file__))
    p = subprocess.run([sys.executable, script], capture_output=True, text=True)
    q = subprocess.run([sys.executable, script, "--sites", "--check"],
                       capture_output=True, text=True)
    assert (p.returncode, q.returncode) == (0, 0), q.stdout[-400:] + q.stderr[-400:]
    assert "every in-scope site has a verdict" in q.stdout

def test_a_site_with_no_verdict_is_a_gap_that_names_the_site(monkeypatch):
    """CONTROL. A new NaN-blind comparison, added without a verdict, must turn
    the gate red and say which line, and what to paste."""
    monkeypatch.setattr(nga, "SITE_VERDICTS", {})
    gaps = nga.triage_gaps(_sites(), root=NOWHERE)
    assert len(gaps) == 2, gaps                       # the tool file's A and B
    assert gaps[0] == (
        f"UNTRIAGED tools/tool.py:{L_A} [A] if abs(a - b) > tol:\n"
        "    add an entry to tools/nan_guard_audit.py::SITE_VERDICTS keyed\n"
        "    ('tools/tool.py', 'A', 'if abs(a - b) > tol:')")
    text = "\n".join(gaps)
    assert "tools/probes/probe.py" not in text and "test_tool.py" not in text

def test_a_control_that_does_not_exist_is_a_gap(tmp_path, monkeypatch):
    """CONTROL. A REACHABLE verdict that cites an injected-NaN test must cite
    one that is there: a cited test that was renamed or deleted is a gap, and
    a cited test that exists is not."""
    table = {KEY_A: nga._v(nga.REACHABLE, "path", "tools/test_x.py::test_it_is_red"),
             KEY_B: nga._v(nga.UNREACHABLE, "integer")}
    sites = _sites()
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "test_x.py").write_text("def test_it_is_red():\n    assert False\n")
    monkeypatch.setattr(nga, "SITE_VERDICTS", table)
    assert nga.triage_gaps(sites, root=tmp_path) == []
    for missing in ("tools/test_x.py::test_renamed", "tools/gone.py::test_it_is_red"):
        table[KEY_A] = nga._v(nga.REACHABLE, "path", missing)
        gaps = nga.triage_gaps(sites, root=tmp_path)
        assert len(gaps) == 1 and gaps[0].startswith(f"CONTROL-MISSING {missing}")

def test_a_verdict_word_outside_the_vocabulary_is_a_gap(monkeypatch):
    monkeypatch.setattr(nga, "SITE_VERDICTS",
                        {KEY_A: ("PROBABLY-FINE", "a guess", None), KEY_B: ("ok", "", None)})
    gaps = nga.triage_gaps(_sites(), root=NOWHERE)
    assert [g for g in gaps if g.startswith("BAD-VERDICT")] == [
        "BAD-VERDICT 'PROBABLY-FINE' (not one of ('REACHABLE', 'UNREACHABLE', "
        "'UNDETERMINED')): ('tools/tool.py', 'A', 'if abs(a - b) > tol:')",
        "BAD-VERDICT 'ok' (not one of ('REACHABLE', 'UNREACHABLE', 'UNDETERMINED')): "
        "('tools/tool.py', 'B', 'if x > X_BOUND:')"]

def test_a_verdict_cannot_rest_on_a_line_that_no_longer_exists(monkeypatch):
    """The other half of freshness: an entry for a site that is gone is a gap
    even when every live site has a verdict."""
    monkeypatch.setattr(nga, "SITE_VERDICTS", {
        KEY_A: nga._v(nga.UNREACHABLE, "integer"),
        KEY_B: nga._v(nga.UNREACHABLE, "integer"),
        ("tools/gone.py", "A", "if abs(a - b) > tol:"): nga._v(nga.REACHABLE, "path")})
    assert nga.triage_gaps(_sites(), root=NOWHERE) == [
        "STALE verdict names a line that is no longer a site: ('tools/gone.py', 'A', "
        "'if abs(a - b) > tol:')\n"
        "    the comparison was edited, moved or deleted; re-triage or drop the entry"]

def test_an_edited_comparison_loses_its_verdict_and_is_asked_for_again(monkeypatch):
    """CONTROL. A tolerance edit changes the key. The old verdict no longer
    applies and the edited comparison must be looked at again."""
    monkeypatch.setattr(nga, "SITE_VERDICTS", {
        KEY_A: nga._v(nga.UNREACHABLE, "integer"), KEY_B: nga._v(nga.UNREACHABLE, "integer")})
    assert nga.triage_gaps(_sites(), root=NOWHERE) == []
    edited = TOOL.replace("    if abs(a - b) > tol:", "    if abs(a - b) > 0.5:")
    assert edited != TOOL
    gaps = nga.triage_gaps(_sites(**{"tools/tool.py": edited}), root=NOWHERE)
    assert len(gaps) == 2
    assert gaps[0].startswith(f"UNTRIAGED tools/tool.py:{L_A} [A] if abs(a - b) > 0.5:")
    assert gaps[1].startswith("STALE verdict names a line that is no longer a site")

def test_the_report_names_its_tree_and_marks_the_sites_that_have_a_control(capsys):
    assert nga.sites_main(False, None) == 0
    out = capsys.readouterr().out
    assert out.startswith("NaN-blind comparison sites, read from working tree @ ")
    assert "in scope (non-test, outside tools/probes/): " in out
    marked = [l for l in out.splitlines() if " REACHABLE + " in l]
    assert len(marked) == sum(1 for v in nga.SITE_VERDICTS.values() if v[2])
    unmarked = [l for l in out.splitlines() if " REACHABLE  " in l]
    assert len(unmarked) == sum(1 for v in nga.SITE_VERDICTS.values()
                                if v[0] == nga.REACHABLE and not v[2])

# ---------------------------------------------------------------------------
# #2014: builtin max() / min() a NaN can be dropped by
# ---------------------------------------------------------------------------
#
# `--aggregates` is the same kind of list for the third shape. What it must
# find, what it must not, and what it is blind to:

#: One call of each kind that IS a site and one of each kind that is NOT: the
#: builtin only (`np.max`, `builtins.max`), no lambda body, no function named
#: for neither a verdict nor a property. Line numbers are read off the text.
AGG_TOOL = '''"""A docstring line such as max(abs(doc - a_string)) is not a site."""
import numpy as np

FAIL = "FAIL"

def check(rows, worst, floor):
    worst = max(worst, abs(rows[0] - rows[1]))      # an accumulator's other operand
    span = max(rows) - min(rows)                    # a population held in a name
    peak = max(r["peak"] for r in rows)             # the filed walk's own kind
    level = max(rows["level"], 1e-30)               # first position: returned, not dropped
    best = min(rows, key=lambda r: max(r["err"], 0))   # a lambda body is not visited
    count = max(len(rows), 1)                       # an integer is still a site
    wrapped = max(
        abs(r["w"]) for r in rows)                  # whitespace is collapsed
    if worst > floor:
        raise Refused("worst")
    return span, peak, level, best, count, wrapped

def level_report(x):
    return max(float(np.max(x)), 1e-15)

def _helper(rows):
    return max(abs(r) for r in rows)

def outer(rows):
    def verify(cells):
        return max(c["w"] for c in cells)

    return verify(rows), np.max(rows), builtins.max(rows)

def tally(rows):
    return max(len(rows), 1) + min(len(rows), 3)

def quiet_level(x):
    worst = max(float(x[0]), 1e-15)
    if worst > 1.0:
        raise Refused("loud")
    return worst
'''
L_DOC_AGG = _line(AGG_TOOL, '"""A docstring line such as max(')
L_ACC = _line(AGG_TOOL, "worst = max(worst, abs(rows[0] - rows[1]))")
L_SPAN = _line(AGG_TOOL, "span = max(rows) - min(rows)")
L_PEAK = _line(AGG_TOOL, 'peak = max(r["peak"] for r in rows)')
L_LEVEL = _line(AGG_TOOL, 'level = max(rows["level"], 1e-30)')
L_BEST = _line(AGG_TOOL, 'best = min(rows, key=lambda r: max(r["err"], 0))')
L_COUNT = _line(AGG_TOOL, "count = max(len(rows), 1)")
L_WRAP = _line(AGG_TOOL, "wrapped = max(")
L_WRAP_END = _line(AGG_TOOL, 'abs(r["w"]) for r in rows)')
L_MEASURE = _line(AGG_TOOL, "return max(float(np.max(x)), 1e-15)")
L_VERIFY = _line(AGG_TOOL, 'return max(c["w"] for c in cells)')
L_LOUD = _line(AGG_TOOL, "worst = max(float(x[0]), 1e-15)")
KEY_MEASURE = ("tools/agg.py", "max", "level_report", "max(float(np.max(x)), 1e-15)")

def _agg(**extra):
    src = {"tools/agg.py": AGG_TOOL}
    src.update(extra)
    return nga.aggregate_sites(src)

def _measure_sites(src=AGG_TOOL):
    return [s for s in nga.aggregate_sites({"tools/agg.py": src})[0] if s.func == "level_report"]

def _arg0(expr: str):
    call = ast.parse(expr, mode="eval").body
    assert isinstance(call, ast.Call)
    return call.args[0]

def test_an_aggregate_is_a_builtin_max_or_min_with_a_non_literal_argument_in_a_named_function():
    """Every call of the fixture in source order, with the function it sits in
    and whether the filed walk's own test counted it."""
    sites, unparsed = _agg()
    assert unparsed == []
    assert [(s.lineno, s.shape, s.func, s.text, s.filed, s.in_scope) for s in sites] == [
        (L_ACC, "max", "check", "max(worst, abs(rows[0] - rows[1]))", False, True),
        (L_SPAN, "max", "check", "max(rows)", False, True),
        (L_SPAN, "min", "check", "min(rows)", False, True),
        (L_PEAK, "max", "check", 'max(r["peak"] for r in rows)', True, True),
        (L_LEVEL, "max", "check", 'max(rows["level"], 1e-30)', True, True),
        (L_BEST, "min", "check", 'min(rows, key=lambda r: max(r["err"], 0))', False, True),
        (L_COUNT, "max", "check", "max(len(rows), 1)", False, True),
        (L_WRAP, "max", "check", 'max( abs(r["w"]) for r in rows)', True, True),
        (L_MEASURE, "max", "level_report", "max(float(np.max(x)), 1e-15)", True, True),
        (L_VERIFY, "max", "outer.verify", 'max(c["w"] for c in cells)', True, True),
        (L_LOUD, "max", "quiet_level", "max(float(x[0]), 1e-15)", True, False)]

def test_what_is_not_a_site():
    sites, _ = _agg()
    texts = [s.text for s in sites]
    # the builtin only
    assert not [t for t in texts if "np.max(rows)" in t or "builtins" in t]
    # a lambda body is not visited: the inner max of the `best =` line
    assert texts.count('min(rows, key=lambda r: max(r["err"], 0))') == 1
    assert 'max(r["err"], 0)' not in texts
    # a function named for neither a verdict nor a property is not visited
    assert not [s for s in sites if s.func in ("_helper", "tally", "outer")]
    # the docstring
    assert not [s for s in sites if s.lineno == L_DOC_AGG]
    # every argument a literal
    assert nga.aggregate_sites({"tools/lit.py": "def check(x):\n    return max(1, 2)\n"})[0] == []

def test_the_key_is_the_call_as_written_with_its_function_and_whitespace_collapsed():
    sites, _ = _agg()
    wrapped = [s for s in sites if s.lineno == L_WRAP][0]
    assert L_WRAP_END == L_WRAP + 1                     # two source lines ...
    assert wrapped.text == 'max( abs(r["w"]) for r in rows)'   # ... one key, double quotes kept
    assert wrapped.key == ("tools/agg.py", "max", "check", 'max( abs(r["w"]) for r in rows)')
    assert wrapped.label == 'check: max( abs(r["w"]) for r in rows)'
    twin = ("def verdict(rows):\n    return max(rows)\n\n\n"
            "def check(rows):\n    return max(rows)\n")
    keys = [s.key for s in nga.aggregate_sites({"tools/twin.py": twin})[0]]
    assert len(set(keys)) == 2 and [k[2] for k in keys] == ["verdict", "check"]

@pytest.mark.parametrize("expr, filed", [
    ('max(r["peak"] for r in rows)', True),         # a generator
    ('max([r for r in rows], default=0)', True),    # a comprehension
    ('max(v.values())', True), ('max(d.items())', True),
    ('max(list(rows))', True), ('max(abs(a), abs(b))', True),
    ('max(float(x), 1e-300)', True),
    ('max(rows["level"], 1e-30)', True),            # a subscript
    ('max(rows)', False),                           # a bare name
    ('max(worst, abs(a - b))', False),              # ... even with a population after it
    ('max(len(rows), 1)', False), ('max(am.rms(r), 1e-30)', False),
    ('max(8, int(x))', False), ('max(1e-9, rows)', False),
])
def test_the_filed_walks_population_test_is_the_first_argument_and_no_bare_name(expr, filed):
    assert nga._filed_population(_arg0(expr)) is filed

def test_a_registered_function_is_gated_and_a_refusing_one_is_only_counted(monkeypatch):
    """The name filter is the filed walk's. `quiet_level` refuses and is not
    named for it, so it is counted under NOT GATED and not triaged; a function
    registered by hand is."""
    sites, _ = _agg()
    assert [(s.named, s.in_scope) for s in sites if s.func == "quiet_level"] == [
        ("refuses", False)]
    assert not [s for s in sites if s.func == "tally"]
    monkeypatch.setattr(nga, "AGGREGATE_REGISTRY", (("tools/agg.py", "tally", "why"),))
    sites, _ = _agg()
    assert [(s.text, s.named, s.in_scope) for s in sites if s.func == "tally"] == [
        ("max(len(rows), 1)", "registered", True), ("min(len(rows), 3)", "registered", True)]
    assert [s.named for s in sites if s.func == "level_report"] == ["name"]

def test_only_tools_is_walked_and_test_files_and_probes_are_told_apart(tmp_path):
    probe = "def check(rows):\n    return max(rows)\n"
    sites, _ = _agg(**{"model/m.py": probe, "fpga/f.py": probe, "tools/test_agg.py": probe,
                       "tools/probes/p.py": probe, "tools/nested/d/tool.py": probe})
    assert sorted({s.path for s in sites}) == [
        "tools/agg.py", "tools/nested/d/tool.py", "tools/probes/p.py", "tools/test_agg.py"]
    assert [(s.path, s.is_test, s.in_probes, s.in_scope) for s in sites
            if s.path != "tools/agg.py"] == [
        ("tools/nested/d/tool.py", False, False, True),
        ("tools/probes/p.py", False, True, False), ("tools/test_agg.py", True, False, False)]
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "x.py").write_text("A = 1\n")
    (tmp_path / "model").mkdir()
    (tmp_path / "model" / "y.py").write_text("B = 2\n")
    assert nga.py_sources(None, tmp_path, roots=nga.AGGREGATE_ROOTS) == {"tools/x.py": "A = 1\n"}

def test_a_file_that_does_not_parse_is_named_and_the_rest_are_still_walked():
    sites, unparsed = _agg(**{"tools/broken.py": "def check(:\n    return max(rows)\n"})
    assert unparsed == ["tools/broken.py (SyntaxError)"]
    assert len([s for s in sites if s.path == "tools/agg.py"]) == 11

def test_python_drops_a_nan_that_is_not_first_and_returns_one_that_is():
    """The fact the whole shape rests on, asserted rather than recalled."""
    nan = float("nan")
    assert min(1.0, nan, 2.0) == 1.0          # dropped: the others set the anchor
    assert max(1.0, nan, 2.0) == 2.0
    assert math.isnan(min(nan, 1.0))           # first: returned, not dropped
    assert math.isnan(max(x for x in (nan, 1.0)))
    assert max(r["v"] for r in ({"v": 1.0}, {"v": nan})) == 1.0
    assert min((x for x in (1.0, nan)), default=0.0) == 1.0
    assert math.isnan(max([], default=nan))    # default= is what is left when all is
    assert min(1.0, float("inf")) == 1.0       # an infinity compares, and is kept ...
    assert max(1.0, float("inf")) == float("inf")

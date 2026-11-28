"""Controls for the #1929 site search: what it must find, and what must turn it red.

`tools/nan_guard_audit.py --sites` is a triage list with one verdict per
comparison site, and `--sites --check` is the claim that the list is complete.
A list nobody has seen miss a site, or pass one with no verdict, is not a
control; the misses are here:

  * a site in a `#` comment, a docstring or any other string is NOT a site --
    #1870's own docstring was the filed count's 71st line;
  * a site with no verdict, a verdict whose line was edited, a verdict naming
    a control that does not exist, a verdict word outside the vocabulary: each
    is a gap, and each is exercised;
  * what the search is blind to is asserted as blind, not left to be found.

The one test that is a standing gate is
`test_the_committed_table_is_complete_fresh_and_its_controls_exist`. It makes
a new NaN-blind comparison in a non-test file under tools/, model/ or fpga/
(outside tools/probes/) fail `make verify` until it has a verdict.
"""

from __future__ import annotations

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


def _tree_with_one_site(tmp_path):
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "new_tool.py").write_text(
        "def f(a, b, tol):\n    if abs(a - b) > tol:\n        return 1\n    return 0\n")
    return tmp_path


@pytest.mark.parametrize("gate", [False, True], ids=["--sites", "--sites --check"])
def test_sites_main_reports_an_untriaged_site_instead_of_crashing(tmp_path, monkeypatch,
                                                                 capsys, gate):
    """CONTROL (Judge, #2019). Before the fix `sites_main` indexed
    SITE_VERDICTS[s.key] unconditionally and died with KeyError on exactly the
    input the gate exists to catch. `triage_gaps()` was tested alone, so the
    CLI path was never red."""
    monkeypatch.setattr(nga, "SITE_VERDICTS", {})
    rc = nga.sites_main(gate, None, root=_tree_with_one_site(tmp_path))
    out = capsys.readouterr().out
    assert "<no verdict>" in out
    assert rc == (1 if gate else 0)
    if gate:
        assert "UNTRIAGED tools/new_tool.py" in out


def test_sites_main_reports_a_verdict_outside_the_vocabulary(tmp_path, monkeypatch, capsys):
    root = _tree_with_one_site(tmp_path)
    key = ("tools/new_tool.py", "A", "if abs(a - b) > tol:")
    monkeypatch.setattr(nga, "SITE_VERDICTS", {key: ("PROBABLY-FINE", "a guess", None)})
    assert nga.sites_main(True, None, root=root) == 1
    assert "BAD-VERDICT" in capsys.readouterr().out

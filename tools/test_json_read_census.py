"""Controls for tools/json_read_census.py (issue #2055).

The census exists because a scratch version of it reported 0 sites: it tested
the wrong AST node, and nothing noticed until a site known to match
(`tools/manifest.py`, `load_json_refused`) turned up absent. So the controls
here are, in order: a fixture whose every candidate line carries its own known
answer in a trailing marker, asserted exactly; that known-match site in the
real file; two mutants of the finder's own tables, each shown to move the
fixture's count; and the git layer, which must read the commit and never the
working tree.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
import exit_contract                                      # noqa: E402
import json_read_census as jc                             # noqa: E402

#: The fixture. `# S<n> guarded` / `# S<n> bare` mark a site and its answer;
#: `# N<n> not a site` marks a line that must stay out of the count.
A_READER = '''\
import json
from pathlib import Path

P = Path("x.json")


def bare_text():
    return json.loads(P.read_text())                       # S1 bare


def bare_bytes():
    return json.loads(P.read_bytes())                      # S2 bare


def read_with_arguments():
    return json.loads(P.read_text(encoding="utf-8"))       # S3 bare


def not_a_site_read():
    return json.loads(open("x.json").read())               # N1 not a site


def not_a_site_json_load():
    return json.load(open("x.json"))                       # N2 not a site


def not_a_site_stored_read():
    text = P.read_text()
    return json.loads(text)                                # N3 not a site


def not_a_site_alias():
    return j.loads(P.read_text())                          # N4 not a site


def guarded_oserror():
    try:
        return json.loads(P.read_text())                   # S4 guarded
    except OSError:
        return {}


def guarded_tuple():
    try:
        return json.loads(P.read_bytes())                  # S5 guarded
    except (ValueError, OSError):
        return {}


def guarded_exception():
    try:
        return json.loads(P.read_text())                   # S6 guarded
    except Exception:
        return {}


def guarded_baseexception():
    try:
        return json.loads(P.read_text())                   # S7 guarded
    except BaseException:
        return {}


def guarded_bare_except():
    try:
        return json.loads(P.read_text())                   # S8 guarded
    except:
        return {}


def only_valueerror_is_not_a_guard():
    try:
        return json.loads(P.read_text())                   # S9 bare
    except ValueError:
        return {}


def inner_narrow_outer_wide():
    try:
        try:
            return json.loads(P.read_text())               # S10 guarded
        except ValueError:
            pass
    except OSError:
        return {}


def in_a_handler_is_not_guarded():
    try:
        x = 0
    except OSError:
        return json.loads(P.read_text())                   # S11 bare


def in_finally_is_not_guarded():
    try:
        x = 0
    finally:
        return json.loads(P.read_text())                   # S12 bare


def make_reader():
    try:
        def later():
            return json.loads(P.read_text())               # S13 bare
    except OSError:
        pass
    return later


def nested_def_with_its_own_try():
    def later():
        try:
            return json.loads(P.read_text())               # S14 guarded
        except OSError:
            return {}
    return later()
'''

P_READER = '''\
import json
from pathlib import Path

P = Path("x.json")


def a():
    return json.loads(P.read_text())                       # P1 bare


def b():
    try:
        return json.loads(P.read_bytes())                  # P2 guarded
    except OSError:
        return {}
'''

TEST_READER = '''\
import json
from pathlib import Path

P = Path("x.json")


def test_a():
    return json.loads(P.read_text())                       # T1 bare


def test_b():
    return json.loads(P.read_text())                       # T2 bare


def test_c():
    try:
        return json.loads(P.read_bytes())                  # T3 guarded
    except (OSError, ValueError):
        return {}
'''

TEST_UNDER_PROBES = '''\
import json
from pathlib import Path

P = Path("x.json")


def test_d():
    return json.loads(P.read_text())                       # O1 bare


def helper():
    try:
        return json.loads(P.read_bytes())                  # O2 guarded
    except OSError:
        return {}
'''

FIXTURE = {
    "tools/a_reader.py": A_READER,
    "tools/probes/p_reader.py": P_READER,
    "tools/test_reader.py": TEST_READER,
    "tools/probes/test_p_reader.py": TEST_UNDER_PROBES,
    "model/whatever_test.py": 'import json\nP = None\n\n\ndef test_e():\n'
                              '    return json.loads(P.read_text())         # M1 bare\n',
    "model/no_sites.py": "X = 1\n",
}

_MARKER = re.compile(r"#\s+([A-Z]\d+)\s+(guarded|bare|not a site)\s*$")
_ANSWER = {"guarded": True, "bare": False, "not a site": None}


def _marked(src: str) -> dict[str, tuple[int, bool | None]]:
    """marker id -> (line, True guarded | False bare | None must not be a site)."""
    out: dict[str, tuple[int, bool | None]] = {}
    for n, line in enumerate(src.splitlines(), 1):
        m = _MARKER.search(line)
        if m:
            out[m.group(1)] = (n, _ANSWER[m.group(2)])
    return out


def _line(src: str, marker: str) -> int:
    return _marked(src)[marker][0]


def _by_line(path: str) -> dict[int, jc.Site]:
    return {s.line: s for s in jc.find_sites(path, FIXTURE[path])}


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t",
                    "-c", "commit.gpgsign=false", *args], check=True, capture_output=True)


# --- the fixture: every marker line, exactly ---------------------------------

def test_fixture_answers_every_marker_line_exactly():
    marked = _marked(A_READER)
    assert len(marked) == 18, "the fixture lost a marker"
    got = _by_line("tools/a_reader.py")
    assert {n: s.guard is not None for n, s in got.items()} == {
        line: g for line, g in marked.values() if g is not None}
    assert not set(got) & {line for line, g in marked.values() if g is None}
    # the read that made each site, read off the node and not the marker
    assert {got[n].read for n in got} == {"read_text", "read_bytes"}
    assert got[_line(A_READER, "S2")].read == "read_bytes"
    assert got[_line(A_READER, "S3")].read == "read_text"
    assert [got[_line(A_READER, m)].guard for m in ("S4", "S5", "S8", "S10", "S14")] == [
        "OSError", "(ValueError, OSError)", "except:", "OSError", "OSError"]


def test_fixture_counts_are_exactly_known():
    s = jc.summarize(jc.census(FIXTURE))
    assert s.files == 6
    assert (s.sites, s.guarded, s.bare) == (22, 10, 12)
    # the three lines the issue reported, which overlap rather than partition
    assert (s.bare_other, s.bare_under_probes, s.bare_tests) == (7, 2, 4)
    assert s.bare_tests_under_probes == 1
    assert s.bare_other + s.bare_under_probes + s.bare_tests - s.bare_tests_under_probes == s.bare
    # the partition: a file is test, else probe, else other
    assert s.part == {"other": (7, 7), "probe": (1, 1), "test": (2, 4)}
    assert tuple(map(sum, zip(*s.part.values()))) == (s.guarded, s.bare)


def test_category_precedence_is_test_then_probe_then_other():
    assert [jc.category(p) for p in FIXTURE] == ["other", "probe", "test", "test", "test", "other"]
    assert jc.category("tools/probes/data_helper.py") == "probe"
    assert jc.category("fpga/test_x.py") == "test"


# --- mutants of the finder's own tables: each must move the count -------------

def test_read_is_a_site_only_when_the_table_says_so(monkeypatch):
    line = _line(A_READER, "N1")
    assert line not in _by_line("tools/a_reader.py")
    monkeypatch.setattr(jc, "READ_ATTRS", jc.READ_ATTRS + ("read",))
    assert line in _by_line("tools/a_reader.py")


def test_a_handler_naming_only_valueerror_guards_when_the_table_says_so(monkeypatch):
    line = _line(A_READER, "S9")
    assert _by_line("tools/a_reader.py")[line].guard is None
    monkeypatch.setattr(jc, "GUARD_NAMES", jc.GUARD_NAMES | {"ValueError"})
    assert _by_line("tools/a_reader.py")[line].guard == "ValueError"


# --- the known match: the scratch census's 0 was wrong here -------------------

def _manifest_line() -> int:
    hits = [n for n, line in enumerate((TOOLS / "manifest.py").read_text().splitlines(), 1)
            if "json.loads(path.read_text())" in line]
    assert len(hits) == 1, hits
    return hits[0]


def test_the_manifest_load_json_refused_site_is_counted_and_is_bare():
    sites = {s.line: s for s in jc.find_sites("tools/manifest.py", (TOOLS / "manifest.py").read_text())}
    s = sites[_manifest_line()]
    assert (s.path, s.read, s.guard) == ("tools/manifest.py", "read_text", None)


def test_that_control_goes_red_when_the_finder_sees_nothing(monkeypatch):
    monkeypatch.setattr(jc, "_is_site", lambda call: None)
    assert jc.find_sites("tools/manifest.py", (TOOLS / "manifest.py").read_text()) == []


# --- the git layer: the commit, never the working tree -----------------------

def _repo(root: Path, tracked: dict[str, str]) -> None:
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    for path, src in tracked.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(src)
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "fixture")


ONE_SITE = "import json\nfrom pathlib import Path\n\n\ndef f(p: Path):\n    return json.loads(p.read_text())\n"


def test_read_tree_reads_the_commit_and_only_tracked_files(tmp_path):
    _repo(tmp_path, {"tools/t.py": ONE_SITE})
    (tmp_path / "tools" / "t.py").write_text(ONE_SITE + ONE_SITE.replace("def f", "def g"))
    (tmp_path / "tools" / "u.py").write_text(ONE_SITE)      # never added
    (tmp_path / "tools" / "n.txt").write_text(ONE_SITE)     # not a .py file
    sources, sha = jc.read_tree("HEAD", tmp_path)
    assert list(sources) == ["tools/t.py"]
    assert len(jc.find_sites("tools/t.py", sources["tools/t.py"])) == 1
    assert re.fullmatch(r"[0-9a-f]{40,64}", sha)


def test_read_tree_refuses_a_rev_it_cannot_resolve(tmp_path):
    _repo(tmp_path, {"tools/t.py": ONE_SITE})
    with pytest.raises(jc.Refused, match="no-such-rev"):
        jc.read_tree("no-such-rev", tmp_path)


@pytest.mark.parametrize("source, reason", [
    ("def f(:\n", "does not parse"),
    ("X = (1,\n", "does not parse"),
    ("X = 1\n", "no site"),
], ids=["syntax-error", "unclosed-paren", "no-site"])
def test_cli_refuses_rather_than_reporting_a_partial_or_empty_count(tmp_path, source, reason):
    _repo(tmp_path, {"tools/t.py": source})
    p = subprocess.run([sys.executable, str(TOOLS / "json_read_census.py"), "--rev", "HEAD",
                        "--root", str(tmp_path)], capture_output=True, text=True)
    assert p.returncode == exit_contract.REFUSED, p.stdout + p.stderr
    assert p.stdout.startswith("REFUSED: ") and reason in p.stdout and "tools/t.py" in p.stdout


def test_cli_refuses_an_unresolvable_rev(tmp_path):
    _repo(tmp_path, {"tools/t.py": ONE_SITE})
    p = subprocess.run([sys.executable, str(TOOLS / "json_read_census.py"), "--rev", "nope",
                        "--root", str(tmp_path)], capture_output=True, text=True)
    assert p.returncode == exit_contract.REFUSED
    assert p.stdout.startswith("REFUSED: ") and "nope" in p.stdout


def test_cli_lists_every_site_with_its_answer(tmp_path):
    _repo(tmp_path, {"tools/t.py": ONE_SITE})
    p = subprocess.run([sys.executable, str(TOOLS / "json_read_census.py"), "--rev", "HEAD",
                        "--root", str(tmp_path), "--list"], capture_output=True, text=True)
    assert p.returncode == exit_contract.PASS, p.stdout + p.stderr
    assert "tools/t.py:6  read_text  bare  [other]" in p.stdout
    assert "sites: 1; guarded" in p.stdout and "bare: 1" in p.stdout


def test_cli_on_the_real_tree_agrees_with_itself():
    p = subprocess.run([sys.executable, str(TOOLS / "json_read_census.py"), "--rev", "HEAD",
                        "--root", str(TOOLS.parent)], capture_output=True, text=True)
    assert p.returncode == exit_contract.PASS, p.stdout + p.stderr
    m = re.search(r"(\d+) tracked \.py files.*?sites: (\d+); guarded[^:]*: (\d+); bare: (\d+)", p.stdout, re.S)
    assert m, p.stdout
    files, sites, guarded, bare = map(int, m.groups())
    assert guarded + bare == sites
    # a floor, not a pin: the scratch census's failure was a count of 0, and
    # origin/main held 769 files / 750 sites when #2055 was filed
    assert files > 700 and sites > 700

"""Tests for tools/vacuous_guard_audit.py (#620).

The tool is an instrument, so it carries its own ground truth
(docs/verification-rules.md rule 8): a verdict function with an emptied
population that the audit MUST flag, its guarded twin that it MUST NOT, and a
mutant of each registered fixed function with its population assertion deleted
that `--check` MUST turn red -- on the population check specifically, i.e. with
exactly the one GAP line naming that function, not an import or parse error.
"""
from __future__ import annotations

import ast
import pathlib
import shutil
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import vacuous_guard_audit as vga  # noqa: E402


# ---------------------------------------------------------------------------
# the audit: the defeating input
# ---------------------------------------------------------------------------
VACUOUS = '''
def check_children(rec):
    for c in rec["children"]:
        if c["verdict"] != "PASS":
            raise Refused("child failed")
    return True

def check_all(rows):
    ok = all(r["ok"] for r in rows)
    if not ok:
        raise Refused("a row failed")
    return ok

def check_none_bad(rows):
    if any(r["bad"] for r in rows):
        raise Refused("bad")
    if not any(r["bad"] for r in rows):
        return True
'''

GUARDED = '''
def check_children(rec):
    if not rec["children"]:
        raise Refused("no children answered")
    for c in rec["children"]:
        if c["verdict"] != "PASS":
            raise Refused("child failed")
    return True

def check_all(rows):
    if len(rows) == 0:
        raise Refused("no rows")
    return all(r["ok"] for r in rows)
'''


def _hits(src):
    return vga.audit_source(src, "probe.py")


def test_an_emptied_population_is_flagged_for_each_construct():
    hits = _hits(VACUOUS)
    kinds = {(h.func, h.kind) for h in hits}
    assert ("check_children", "for-loop refuses") in kinds
    assert ("check_all", "all()") in kinds
    assert all(not h.mark for h in hits if h.func in ("check_children", "check_all"))


def test_the_same_functions_with_a_population_assertion_are_not_flagged():
    assert _hits(GUARDED) == []


def test_an_assertion_about_a_different_thing_does_not_excuse_the_loop():
    """#582's shape: the function checks `pub["configuration"]` and then loops
    over `pub["published_sha256"]`. Naming `pub` is not asserting the set."""
    src = (
        "def image_identity(pub):\n"
        "    if pub['configuration'] != CONFIG:\n"
        "        raise Refused('config')\n"
        "    for name, digest in pub['published_sha256'].items():\n"
        "        if sha(name) != digest:\n"
        "            raise Refused('hash')\n"
    )
    hits = _hits(src)
    assert [(h.func, h.text) for h in hits] == [
        ("image_identity", "over pub['published_sha256']")]


def test_a_population_assertion_after_the_loop_does_not_count():
    src = (
        "def check_x(items):\n"
        "    for i in items:\n"
        "        if i < 0:\n"
        "            raise Refused('neg')\n"
        "    if not items:\n"
        "        raise Refused('empty')\n"
    )
    assert len(_hits(src)) == 1


def test_constant_and_integer_populations_are_advisory_not_dropped():
    src = (
        "def check_k(x):\n"
        "    for k in ('a', 'b'):\n"
        "        if k not in x:\n"
        "            raise Refused(k)\n"
        "    for n in range(3):\n"
        "        if x[n] < 0:\n"
        "            raise Refused(n)\n"
        "    for k in KEYS:\n"
        "        if k not in x:\n"
        "            raise Refused(k)\n"
    )
    assert sorted(h.mark for h in _hits(src)) == ["const?", "const?", "int?"]


def test_a_loop_that_cannot_refuse_is_not_a_verdict_hole():
    src = (
        "def check_sum(xs):\n"
        "    t = 0\n"
        "    for x in xs:\n"
        "        t += x\n"
        "    if t < 0:\n"
        "        raise Refused('neg')\n"
    )
    assert _hits(src) == []


def test_helpers_that_are_not_verdict_shaped_are_skipped():
    src = "def render(xs):\n    for x in xs:\n        yield x\n"
    assert _hits(src) == []


def test_the_audit_runs_over_the_real_tree_and_finds_something():
    """A scan that found nothing in 100+ files would be a broken scan."""
    hits = vga.audit_tree()
    assert len(hits) > 20
    assert any(h.mark == "const?" for h in hits) and any(not h.mark for h in hits)


# ---------------------------------------------------------------------------
# the gate: green on the real tree, red on each mutant
# ---------------------------------------------------------------------------
def test_check_is_green_on_the_current_tree_as_a_subprocess():
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "vacuous_guard_audit.py"),
                        "--check"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "population assertions all present" in r.stdout


def test_the_register_names_real_functions_and_the_issues_it_claims():
    assert len(vga.BOUNDARIES) >= 3
    issues = {b[3] for b in vga.BOUNDARIES}
    assert {"#118", "#283", "#431"} <= issues
    for rel, func, needles, _issue, why in vga.BOUNDARIES:
        sites = vga.assertion_sites((ROOT / rel).read_text())
        assert func in sites, f"{rel}::{func}"
        assert any(all(n in s for n in needles) for s in sites[func]), f"{rel}::{func}"
        assert len(why.split()) >= 6


class _DeleteAssertion(ast.NodeTransformer):
    """Replace the registered population assertion in one function with `pass`."""

    def __init__(self, func, needles):
        self.func, self.needles, self.done = func, needles, 0
        self._in = False

    def visit_FunctionDef(self, node):
        was, self._in = self._in, node.name == self.func
        self.generic_visit(node)
        self._in = was
        return node

    def _match(self, text):
        return all(n in text for n in self.needles)

    def visit_If(self, node):
        if self._in and self._match(ast.unparse(node.test)):
            self.done += 1
            return ast.Pass()
        return self.generic_visit(node)

    def visit_ExceptHandler(self, node):
        if self._in:
            node.body = [ast.Pass() if isinstance(s, ast.Raise) and self._match(ast.unparse(s))
                         and not setattr(self, "done", self.done + 1) else s
                         for s in node.body]
        return self.generic_visit(node)


def _mutant_root(tmp_path, rel, func, needles):
    for r in {b[0] for b in vga.BOUNDARIES}:
        dst = tmp_path / r
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / r, dst)
    tree = ast.parse((tmp_path / rel).read_text())
    tf = _DeleteAssertion(func, needles)
    tree = tf.visit(tree)
    assert tf.done >= 1, "the mutator found nothing to delete: the control is void"
    ast.fix_missing_locations(tree)
    (tmp_path / rel).write_text(ast.unparse(tree))
    ast.parse((tmp_path / rel).read_text())          # the mutant is valid Python
    return tmp_path


def test_the_unmutated_copy_is_clean(tmp_path):
    for r in {b[0] for b in vga.BOUNDARIES}:
        (tmp_path / r).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / r, tmp_path / r)
    assert vga.boundary_gaps(tmp_path) == []


@pytest.mark.parametrize("rel,func,needles,issue,why", vga.BOUNDARIES,
                         ids=[f"{b[3]}-{b[1]}-{'+'.join(b[2])}" for b in vga.BOUNDARIES])
def test_deleting_a_registered_population_assertion_turns_check_red(
        tmp_path, rel, func, needles, issue, why):
    root = _mutant_root(tmp_path, rel, func, needles)
    gaps = vga.boundary_gaps(root)
    # exactly this entry, and for the population reason -- not a missing file
    # or a parse error, which would also be "red".
    assert len(gaps) == 1, gaps
    assert f"{rel}::{func} ({issue}) has lost its population assertion" in gaps[0]
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "vacuous_guard_audit.py"),
                        "--check", "--root", str(root)], capture_output=True, text=True)
    assert r.returncode == 1
    assert "lost its population assertion" in r.stdout
    assert "Traceback" not in r.stderr


def test_an_unrelated_deletion_does_not_turn_check_red(tmp_path):
    """The converse: the gate is red because of the assertion, not because any
    edit to the file would turn it red."""
    for r in {b[0] for b in vga.BOUNDARIES}:
        (tmp_path / r).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / r, tmp_path / r)
    p = tmp_path / "tools/trial.py"
    p.write_text(p.read_text().replace("def composite(", "X = 1\n\n\ndef composite(", 1))
    assert vga.boundary_gaps(tmp_path) == []


def test_a_renamed_function_is_a_gap_not_a_silent_pass(tmp_path):
    for r in {b[0] for b in vga.BOUNDARIES}:
        (tmp_path / r).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / r, tmp_path / r)
    p = tmp_path / "tools/trial.py"
    p.write_text(p.read_text().replace("def composite(", "def composite_v2(", 1))
    gaps = vga.boundary_gaps(tmp_path)
    assert any("composite" in g and "not a function" in g for g in gaps)

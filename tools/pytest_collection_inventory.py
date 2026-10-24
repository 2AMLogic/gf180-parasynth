#!/usr/bin/env python3
"""Every test this repository defines must be a test pytest actually collects.

Issue #564. Four modules -- ``tools/probes/excitation_energy.py``,
``tools/probes/dc_blocker.py``, ``model/discrimination_features.py`` and
``model/discrimination_trajectory.py`` -- carried 57 ``def test_*`` functions
that no CI job and no ``make`` target had ever run, because their filenames do
not match pytest's default ``test_*.py`` / ``*_test.py`` and the suites are
invoked by DIRECTORY (``pytest model/ spec/ tools/ fpga/ pnr/``). They were the
injected controls behind the F1 pedestal finding and the DC-blocker gates: the
``docs/failure-modes.md`` shape of a control that exists and never turns
anything red. The repair is the root ``pytest.ini``; this module is the guard
that keeps the two sets -- tests that exist and tests that run -- from drifting
apart again.

Two independent oracles, compared:

* **What exists** -- static: every git-tracked ``*.py`` under the guarded
  directories is parsed with ``ast`` (not a regex), and every module-level
  ``def test*`` and every ``def test*`` method of a module-level class is an
  expected test, named the way pytest names it (``path::name`` or
  ``path::Class::name``).
* **What runs** -- observed: ``pytest --collect-only`` over the same
  directories, the way ``make verify`` and the CI ``tools`` / ``model`` jobs
  invoke it, with its node IDs read back. A parametrised test matches by its
  ``name[`` prefix.

Verdicts (``REFUSED`` is first-class, CLAUDE.md):

* ``PASS``    every expected test is collected.
* ``FAIL``    at least one expected test is not collected; every missing node
              is listed by path, so the report names the omission.
* ``REFUSED`` the comparison cannot be made: git cannot list files, a file does
              not parse, pytest reports a collection ERROR (an import failure is
              not an omission and must not read as one), or nothing at all was
              discovered or collected.

Inputs that satisfy this guard while violating its intent (rule 8) -- stated
because none of them can be closed cheaply:

* a test generated at runtime (``globals()["test_x"] = ...``, a factory loop,
  ``exec``) is invisible to ``ast`` and so is never expected;
* a test nested inside a function, or a class nested inside a class, is not
  expected (pytest would not collect it either -- it is still a lost test);
* a test that IS collected but always skipped, or deselected by a ``-k`` /
  ``--deselect`` in a Makefile line, passes this guard: collection is not
  execution, and a collected failing test is pytest's own job to turn red
  (``tools/test_pytest_collection_inventory.py`` shows that it does);
* a test-bearing file outside ``GUARDED_DIRS`` (e.g. ``rtl-sketch/``, whose
  tests are run by name, not by directory) is out of scope;
* an untracked file is invisible to ``git ls-files``. CI sees only commits, so
  this matters locally only.

Run::

    python tools/pytest_collection_inventory.py            # the real tree
    python tools/pytest_collection_inventory.py controls   # injected defects

Exit 0 PASS, 1 FAIL, 2 REFUSED. ``controls`` exits 0 only if every injected
defect produced its expected verdict.
"""
from __future__ import annotations

import argparse
import ast
import os
import subprocess
import sys
import tempfile
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

#: The directories ``make verify`` (Makefile ``verify``/``verify-full``/
#: ``test``) collects by directory. CI collects the same ones in two jobs
#: (``pytest model/ spec/`` and ``pytest tools/``); fpga/ and pnr/ are covered
#: by ``make verify`` only.
GUARDED_DIRS = ("model", "spec", "tools", "fpga", "pnr")

#: pytest's own defaults (``python_functions = test``,
#: ``python_classes = Test``) -- what "a test" means to the reader of a file.
FUNCTION_PREFIX = "test"

PASS, FAIL, REFUSED = "PASS", "FAIL", "REFUSED"


@dataclass
class Report:
    verdict: str
    expected: dict[str, list[str]] = field(default_factory=dict)  # path -> node ids
    collected: set[str] = field(default_factory=set)
    missing: list[str] = field(default_factory=list)
    reason: str = ""

    def render(self) -> str:
        n_exp = sum(len(v) for v in self.expected.values())
        head = (f"{self.verdict}: {n_exp} test definitions in "
                f"{len(self.expected)} modules, {len(self.collected)} items collected")
        lines = [head]
        if self.reason:
            lines.append(f"  reason: {self.reason}")
        if self.missing:
            by_path: dict[str, list[str]] = {}
            for node in self.missing:
                by_path.setdefault(node.split("::", 1)[0], []).append(node)
            lines.append(f"  {len(self.missing)} defined tests are NOT collected, "
                         f"in {len(by_path)} modules:")
            for path in sorted(by_path):
                lines.append(f"    {path}  ({len(by_path[path])} tests)")
                lines.extend(f"      {n}" for n in by_path[path])
        return "\n".join(lines)


class Refusal(Exception):
    pass


def tracked_python_files(root: Path, dirs: tuple[str, ...]) -> list[str]:
    """Repo-relative POSIX paths of git-tracked ``*.py`` under ``dirs``."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z", "--", *dirs],
            capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        raise Refusal(f"git is not available: {exc}") from exc
    if proc.returncode != 0:
        raise Refusal(f"git ls-files exited {proc.returncode}: {proc.stderr.strip()}")
    return sorted(p for p in proc.stdout.split("\0") if p.endswith(".py"))


def expected_nodes(path: str, source: str) -> list[str]:
    """Node IDs pytest would produce for the tests a reader sees in ``source``."""
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as exc:
        raise Refusal(f"{path} does not parse: {exc}") from exc
    nodes: list[str] = []
    funcs = (ast.FunctionDef, ast.AsyncFunctionDef)
    for stmt in tree.body:
        if isinstance(stmt, funcs) and stmt.name.startswith(FUNCTION_PREFIX):
            nodes.append(f"{path}::{stmt.name}")
        elif isinstance(stmt, ast.ClassDef):
            for sub in stmt.body:
                if isinstance(sub, funcs) and sub.name.startswith(FUNCTION_PREFIX):
                    nodes.append(f"{path}::{stmt.name}::{sub.name}")
    return nodes


def discover(root: Path, dirs: tuple[str, ...]) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for path in tracked_python_files(root, dirs):
        nodes = expected_nodes(path, (root / path).read_text(encoding="utf-8"))
        if nodes:
            found[path] = nodes
    return found


def collect(root: Path, dirs: tuple[str, ...]) -> set[str]:
    """Node IDs from ``pytest --collect-only`` run the way the suites run it."""
    env = dict(os.environ)
    # An inherited -k/--deselect would remove items and read as omission.
    env.pop("PYTEST_ADDOPTS", None)
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q",
         "-p", "no:cacheprovider", "--rootdir", str(root), *dirs],
        cwd=root, capture_output=True, text=True, env=env, check=False)
    out = proc.stdout + proc.stderr
    if proc.returncode not in (0, 5) or "ERROR collecting" in out:
        tail = "\n".join(out.strip().splitlines()[-15:])
        raise Refusal(f"pytest collection exited {proc.returncode} "
                      f"(a collection error is not an omission):\n{tail}")
    return {line.strip() for line in proc.stdout.splitlines()
            if "::" in line and not line.startswith(" ")}


def is_collected(node: str, collected: set[str]) -> bool:
    return node in collected or any(c.startswith(node + "[") for c in collected)


def check(root: Path = REPO_ROOT, dirs: tuple[str, ...] = GUARDED_DIRS) -> Report:
    try:
        expected = discover(root, dirs)
        if not expected:
            raise Refusal(f"no test definitions discovered under {dirs}: "
                          "nothing to compare")
        collected = collect(root, dirs)
        if not collected:
            raise Refusal("pytest collected zero items while tests are defined: "
                          "the collection output was not read")
    except Refusal as exc:
        return Report(REFUSED, reason=str(exc))
    missing = [n for nodes in expected.values() for n in nodes
               if not is_collected(n, collected)]
    return Report(FAIL if missing else PASS, expected, collected, missing)


# --------------------------------------------------------------------------
# Injected-defect controls. Each builds a throwaway git project, so the real
# tree is never edited and no control re-collects the whole repository.

_PASSING = "def test_ok():\n    assert True\n"


def make_project(base: Path, files: dict[str, str], ini: str | None) -> Path:
    base.mkdir(parents=True, exist_ok=True)
    for rel, body in files.items():
        p = base / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(body), encoding="utf-8")
    if ini is not None:
        (base / "pytest.ini").write_text(textwrap.dedent(ini), encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(base)], check=True)
    subprocess.run(["git", "-C", str(base), "add", "-A"], check=True)
    return base


@dataclass
class Control:
    name: str
    files: dict[str, str]
    ini: str | None
    expect: str
    expect_missing: tuple[str, ...] = ()


CONTROLS = (
    Control("clean", {"pkg/test_a.py": _PASSING}, "[pytest]\n", PASS),
    # The #564 shape: a nonstandard-named module carrying a FAILING test. The
    # guard must name the omission; pytest alone stays green because it never
    # sees the module.
    Control("uncollected_module",
            {"pkg/test_a.py": _PASSING,
             "pkg/probe.py": "def test_probe_fails():\n    assert False\n"},
            "[pytest]\n", FAIL, ("pkg/probe.py::test_probe_fails",)),
    Control("uncollected_class",
            {"pkg/test_a.py": _PASSING,
             "pkg/helper.py": "class TestHelper:\n"
                              "    def test_method(self):\n        assert False\n"},
            "[pytest]\n", FAIL, ("pkg/helper.py::TestHelper::test_method",)),
    # Inside a collected file, a class pytest skips (no Test prefix).
    Control("uncollected_class_in_test_file",
            {"pkg/test_a.py": _PASSING + "class Checks:\n"
                                         "    def test_lost(self):\n        assert False\n"},
            "[pytest]\n", FAIL, ("pkg/test_a.py::Checks::test_lost",)),
    # The repair, applied to the same input: the module is now collected.
    Control("module_named_in_python_files",
            {"pkg/test_a.py": _PASSING,
             "pkg/probe.py": "def test_probe_fails():\n    assert False\n"},
            "[pytest]\npython_files = test_*.py *_test.py probe.py\n", PASS),
    # An import failure is NO VERDICT, never a caught omission.
    Control("import_error",
            {"pkg/test_a.py": _PASSING,
             "pkg/probe.py": "import no_such_module_564\n"
                             "def test_probe():\n    assert True\n"},
            "[pytest]\npython_files = test_*.py *_test.py probe.py\n", REFUSED),
    Control("nothing_defined", {"pkg/util.py": "X = 1\n"}, "[pytest]\n", REFUSED),
)


def run_control(ctl: Control, base: Path) -> Report:
    root = make_project(base / ctl.name, ctl.files, ctl.ini)
    return check(root, ("pkg",))


def control_ok(ctl: Control, rep: Report) -> bool:
    return rep.verdict == ctl.expect and set(rep.missing) == set(ctl.expect_missing)


def run_controls() -> int:
    bad = 0
    with tempfile.TemporaryDirectory(prefix="i564-controls-") as tmp:
        for ctl in CONTROLS:
            rep = run_control(ctl, Path(tmp))
            ok = control_ok(ctl, rep)
            bad += not ok
            print(f"{'ok ' if ok else 'BAD'} {ctl.name:32s} expected {ctl.expect:8s} "
                  f"got {rep.verdict:8s} missing={rep.missing}")
            if not ok:
                print(textwrap.indent(rep.render(), "    "))
    print(f"{len(CONTROLS) - bad}/{len(CONTROLS)} controls behaved as expected")
    return 0 if bad == 0 else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("mode", nargs="?", choices=("check", "controls"), default="check")
    args = ap.parse_args(argv)
    if args.mode == "controls":
        return run_controls()
    rep = check()
    print(rep.render())
    return {PASS: 0, FAIL: 1, REFUSED: 2}[rep.verdict]


if __name__ == "__main__":
    sys.exit(main())

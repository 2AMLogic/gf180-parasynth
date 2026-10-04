"""The corpus location is decided in ONE place: run_case.configured_refs() (#522).

Four readers once resolved it four ways (a different layout default, a different
variable name, and a direct read of the constant that bypassed the variable), so
an operator who set the documented variable got no error and no effect on some
tools. This test makes the next divergence a red run.

The scan is over the AST, not text: it flags any non-docstring string constant
that IS the variable name, the legacy alias, or begins with the default path, any attribute or
name use of REFS_DEFAULT / REFS_ENV, and any `+`-folded string that spells one of
them (the obvious way to satisfy a grep while violating the intent). Docstrings
and comments may still *talk about* the variable.

Layout (settled by #522): the variable names the Fischer repository ROOT, i.e.
`<refs>/bd8/BD5050.WAV` (model/drum_verify.REF_MAIN, clap_d12a_probe's
manifest), NOT its parent.
"""
from __future__ import annotations

import ast
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "model"))
import run_case as rc  # noqa: E402

OWNER = pathlib.PurePosixPath("tools/run_case.py")
OWNER_TESTS = pathlib.PurePosixPath("tools/test_run_case.py")   # tests the resolver itself
SELF = pathlib.PurePosixPath("tools/test_corpus_path_single_source.py")
EXACT = {"GF180_TR808_REFS", "TR808_REFS", "GF180_TR808_REFS_DEFAULT"}   # env-var keys
DEFAULT_PATH = "/tmp/tr808-ref"                  # the default, as a path (prose may mention it)
BANNED_ATTRS = {"REFS_DEFAULT", "REFS_ENV"}


def _fold(node):
    """Constant-fold string `+` so "TR808_" "REFS" and "TR808_" + "REFS" are seen."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value for v in node.values
                       if isinstance(v, ast.Constant) and isinstance(v.value, str))
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        a, b = _fold(node.left), _fold(node.right)
        if a is not None and b is not None:
            return a + b
    return None


def violations(source: str) -> list[str]:
    tree = ast.parse(source)
    docs = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            b = n.body
            if b and isinstance(b[0], ast.Expr) and isinstance(b[0].value, ast.Constant):
                docs.add(id(b[0].value))
    # REFS_ENV inside an f-string is a message naming the variable, not a read
    fmt = {id(c) for n in ast.walk(tree) if isinstance(n, ast.JoinedStr) for c in ast.walk(n)}
    out = []
    for n in ast.walk(tree):
        if id(n) in fmt and getattr(n, "attr", None) == "REFS_ENV":
            continue
        if isinstance(n, ast.Attribute) and n.attr in BANNED_ATTRS:
            out.append(f"line {n.lineno}: .{n.attr}")
        elif isinstance(n, ast.Name) and n.id in BANNED_ATTRS:
            out.append(f"line {n.lineno}: {n.id}")
        elif isinstance(n, (ast.Constant, ast.JoinedStr, ast.BinOp)) and id(n) not in docs:
            s = _fold(n)
            if s and (s.strip() in EXACT or s.startswith(DEFAULT_PATH)):
                out.append(f"line {n.lineno}: string {s[:60]!r}")
    return sorted(set(out))


def _tracked_py() -> list[str]:
    r = subprocess.run(["git", "-C", str(ROOT), "ls-files", "*.py"],
                       capture_output=True, text=True, check=True)
    return [p for p in r.stdout.splitlines() if (ROOT / p).exists()]


def test_no_module_outside_run_case_reads_the_corpus_variable_or_default():
    files = _tracked_py()
    assert len(files) > 100, f"scan saw only {len(files)} files -- vacuous"
    bad = {}
    for p in files:
        if pathlib.PurePosixPath(p) in (OWNER, OWNER_TESTS, SELF):
            continue
        v = violations((ROOT / p).read_text())
        if v:
            bad[p] = v
    assert not bad, ("these read the corpus location themselves; call "
                     f"run_case.configured_refs() instead: {bad}")


def test_the_scan_is_not_vacuous_it_sees_the_owner():
    # injected-bug control: run_case itself must trip the scan, or the scan is blind
    assert violations((ROOT / OWNER).read_text())


@pytest.mark.parametrize("src", [
    'import os\nx = os.environ.get("GF180_TR808_REFS")\n',
    'import os\nx = os.environ.get("TR808_REFS", "/tmp/tr808-ref")\n',
    'import run_case as rc\nx = rc.REFS_DEFAULT\n',
    'from run_case import REFS_ENV\n_ = REFS_ENV\n',
    'import os\nx = os.environ["TR808_" + "REFS"]\n',
    'import os, run_case as rc\nx = os.environ.get(rc.REFS_ENV)\n',
    'import run_case as rc\nx = f"{rc.REFS_DEFAULT}/bd8"\n',
    'p = "/tmp/tr808" + "-ref"\n',
    'import pathlib\nx = pathlib.Path(f"/tmp/tr808-ref")\n',
])
def test_injected_defects_are_caught(src):
    assert violations(src), src


def test_docstrings_and_the_sanctioned_call_are_allowed():
    ok = '"""Reads $GF180_TR808_REFS via run_case."""\nimport run_case as rc\nx = rc.configured_refs()\n'
    assert violations(ok) == []
    msg = 'import run_case as rc\nm = f"set {rc.REFS_ENV}"\n'
    assert violations(msg) == []


# ---- the resolver itself ---------------------------------------------------

def test_variable_wins_else_alias_else_default(monkeypatch, tmp_path):
    monkeypatch.delenv("GF180_TR808_REFS", raising=False)
    monkeypatch.delenv("TR808_REFS", raising=False)
    assert rc.configured_refs() == pathlib.Path("/tmp/tr808-ref")
    monkeypatch.setenv("TR808_REFS", str(tmp_path / "a"))
    assert rc.configured_refs() == tmp_path / "a"            # documented alias
    monkeypatch.setenv("GF180_TR808_REFS", str(tmp_path / "a"))
    assert rc.configured_refs() == tmp_path / "a"            # agreeing: fine
    monkeypatch.setenv("GF180_TR808_REFS", str(tmp_path / "b"))
    with pytest.raises(RuntimeError, match="disagree"):      # silent pick would be the bug
        rc.configured_refs()
    monkeypatch.delenv("TR808_REFS")
    assert rc.configured_refs() == tmp_path / "b"


MODULES = ["measure_promoted_bands", "measure_partial_balance"]


@pytest.mark.parametrize("mod", MODULES)
def test_readers_follow_the_documented_variable(mod, monkeypatch, tmp_path):
    """Run the reader's own refs resolution in a subprocess with the variable set.
    Each module exposes REFS or REFDIR, evaluated at import."""
    code = (f"import sys; sys.path[:0]=['{ROOT}/tools','{ROOT}/model'];"
            f"import {mod} as m; print(getattr(m,'REFS',None) or m.REFDIR)")
    env = {"PATH": "/usr/bin:/bin", "GF180_TR808_REFS": str(tmp_path)}
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr[-1500:]
    assert r.stdout.strip() == str(tmp_path), r.stdout


def test_go_sh_exports_the_fischer_root_not_its_parent():
    txt = (ROOT / "fpga/reports/r2/settled/go.sh").read_text()
    line = [l for l in txt.splitlines() if l.startswith("export GF180_TR808_REFS=")][0]
    assert line.rstrip().endswith("/sounds-tr808-fischer"), line

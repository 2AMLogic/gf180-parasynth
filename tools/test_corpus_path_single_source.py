"""The corpus location is decided in ONE place: run_case.configured_refs() (#522).

Four readers once resolved it four ways (a different layout default, a different
variable name, and a direct read of the constant that bypassed the variable), so
an operator who set the documented variable got no error and no effect on some
tools. This test makes the next divergence a red run.

The scan is over the AST, not text. Outside docstrings it flags:

- a string that IS the variable name, the alias, or the name of one of
  run_case's corpus constants (so `getattr(rc, "REFS_DEFAULT")` is seen);
- a string that begins with the default path, or a whitespace-free path whose
  components include a corpus directory name (`tr808-ref`,
  `sounds-tr808-fischer`) -- a second hard-coded root that never names the
  variable, e.g. `Path.home() / "dev/refs/sounds-tr808-fischer"`;
- a string containing a shell/expandvars reference `$GF180_TR808_REFS`,
  `${TR808_REFS}`;
- any attribute or name use of REFS_DEFAULT / REFS_ENV / REFS_ALIAS_ENV, EXCEPT
  inside an f-string that has literal text of its own and is not the key of an
  environment read (a message naming the variable, not a read of it).

"Is" is evaluated after constant-folding `+`, implicit concatenation, f-string
literal parts, `/` on paths, `Path(...)`/`PurePath(...)` arguments,
`os.path.join(...)` and `"sep".join([...])` -- the idiomatic ways to satisfy a
grep while violating the intent. Docstrings and comments may still *talk about*
the variable.

Known gaps -- inputs this static scan cannot catch, and why (rule 8):

- Data flow across statements: `k = f"{rc.REFS_ENV} "` on one line and
  `os.environ.get(k.strip())` on another. The f-string has literal text and is
  not itself an environment key, so it looks like a message; telling the two
  apart needs data-flow analysis, not a syntax scan.
- Names computed at run time: `getattr(rc, "REFS_" + suffix)` with a variable
  `suffix`, `chr()`/base64/`bytes.decode` spellings, `eval`/`exec`, reading
  `importlib.import_module("run_case").__dict__` in a loop. No constant to fold.
- Indirect environment reads: iterating `os.environ.items()` and matching the key
  by a computed predicate, or reading the variable in a subprocess spawned with
  an argv list (`["sh", "-c", var]`) where the command text is computed.
- A hard-coded root that names NEITHER the variable NOR a corpus directory (e.g.
  `Path.home() / "dev" / "refs"` with the Fischer subdirectory appended from a
  variable). Out of scope: such a path is indistinguishable from any other
  directory without knowing it is used as the corpus.
- Non-Python readers (shell, Makefile, YAML). The scan is over tracked `*.py`;
  `go.sh` is checked separately below and the Makefile is not checked.

Layout (settled by #522): the variable names the Fischer repository ROOT, i.e.
`<refs>/bd8/BD5050.WAV` (model/drum_verify.REF_MAIN, clap_d12a_probe's
manifest), NOT its parent.
"""
from __future__ import annotations

import ast
import pathlib
import re
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
ENV_NAMES = {"GF180_TR808_REFS", "TR808_REFS", "GF180_TR808_REFS_DEFAULT"}   # env-var keys
DEFAULT_PATH = "/tmp/tr808-ref"                  # the default, as a path (prose may mention it)
BANNED_ATTRS = {"REFS_DEFAULT", "REFS_ENV", "REFS_ALIAS_ENV"}
# Only the variable NAMES may appear in a message; interpolating REFS_DEFAULT
# (a path) is using it, wherever it appears.
MESSAGE_OK_ATTRS = {"REFS_ENV", "REFS_ALIAS_ENV"}
CORPUS_DIRS = {"tr808-ref", "sounds-tr808-fischer"}   # a path component naming the corpus
SHELL_REF = re.compile(r"\$\{?(GF180_TR808_REFS|TR808_REFS)\b")
PATH_CTORS = {"Path", "PurePath", "PurePosixPath", "PosixPath", "PureWindowsPath", "WindowsPath"}
PATH_PASSTHROUGH = {"expanduser", "expandvars", "abspath", "realpath", "normpath", "fspath"}
# The only functions in tools/test_run_case.py allowed to name the variable:
# they test the resolver (and the required-corpus gate) themselves.
OWNER_TESTS_RESOLVER_FUNCS = frozenset({
    "_pytest_one",
    "test_a_required_reference_gate_refuses_a_missing_corpus",
    "test_the_tests_read_the_same_corpus_location_as_the_runner",
})


def _is_str(node) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def _fold(node):
    """Constant-fold the string- and path-building forms a reader would use, so
    "TR808_" + "REFS", Path("/tmp") / "tr808-ref", os.path.join(...) and
    "_".join([...]) are all seen as the string they spell. None if not constant."""
    if _is_str(node):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value for v in node.values if _is_str(v))
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Div)):
        a, b = _fold(node.left), _fold(node.right)
        if a is None or b is None:
            return None
        return a + b if isinstance(node.op, ast.Add) else a.rstrip("/") + "/" + b
    if isinstance(node, ast.Call) and not node.keywords:
        f = node.func
        name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
        if name == "join" and isinstance(f, ast.Attribute) and _is_str(f.value):
            if len(node.args) == 1 and isinstance(node.args[0], (ast.List, ast.Tuple)):
                items = [_fold(e) for e in node.args[0].elts]
                if all(i is not None for i in items):
                    return f.value.value.join(items)
            return None
        parts = [_fold(a) for a in node.args]
        if not parts or any(p is None for p in parts):
            return None
        if name in PATH_CTORS or (name == "join" and isinstance(f, ast.Attribute)
                                  and ast.unparse(f.value).endswith("path")):
            return "/".join([p.rstrip("/") for p in parts[:-1]] + [parts[-1]])
        if name in PATH_PASSTHROUGH and len(parts) == 1:
            return parts[0]
    return None


def _is_env_read(node) -> bool:
    """A call or subscript that reads the process environment by key."""
    if isinstance(node, ast.Call):
        callee = ast.unparse(node.func)
        return "environ" in callee or callee.endswith("getenv")
    if isinstance(node, ast.Subscript):
        return "environ" in ast.unparse(node.value)
    return False


def _subtree_ids(nodes) -> set[int]:
    return {id(c) for n in nodes for c in ast.walk(n)}


def _flag_string(s: str) -> bool:
    t = s.strip()
    if t in ENV_NAMES or t in BANNED_ATTRS or s.startswith(DEFAULT_PATH):
        return True
    if SHELL_REF.search(s):
        return True
    # a whitespace-free, non-URL path naming the corpus directory: a hard-coded root
    if t and not any(c.isspace() for c in t) and "://" not in t:
        return bool(set(t.split("/")) & CORPUS_DIRS)
    return False


def violations(source: str, allow_in: frozenset[str] = frozenset()) -> list[str]:
    """Every place `source` reads the corpus location itself. Nodes inside a
    function whose name is in `allow_in` are skipped (see OWNER_TESTS_RESOLVER_FUNCS)."""
    tree = ast.parse(source)
    docs = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            b = n.body
            if b and isinstance(b[0], ast.Expr) and isinstance(b[0].value, ast.Constant):
                docs.add(id(b[0].value))
    allowed = _subtree_ids(n for n in ast.walk(tree)
                           if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                           and n.name in allow_in)
    # environment keys: the arguments of os.environ.get / os.getenv / environ[...]
    env_keys = set()
    for n in ast.walk(tree):
        if _is_env_read(n):
            env_keys |= _subtree_ids(n.args if isinstance(n, ast.Call) else [n.slice])
    # A banned constant inside an f-string is a MESSAGE naming the variable only if
    # the f-string has literal text of its own and is not itself an environment key.
    # (v1 exempted every f-string, so os.environ.get(f"{rc.REFS_ENV}") passed.)
    message = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.JoinedStr) and id(n) not in env_keys and \
                any(_is_str(v) and v.value.strip() for v in n.values):
            message |= _subtree_ids([n])
    out = []
    for n in ast.walk(tree):
        if id(n) in allowed:
            continue
        if isinstance(n, ast.Attribute) and n.attr in BANNED_ATTRS:
            if id(n) not in message or n.attr not in MESSAGE_OK_ATTRS:
                out.append(f"line {n.lineno}: .{n.attr}")
        elif isinstance(n, ast.Name) and n.id in BANNED_ATTRS:
            if id(n) not in message or n.id not in MESSAGE_OK_ATTRS:
                out.append(f"line {n.lineno}: {n.id}")
        elif isinstance(n, (ast.Constant, ast.JoinedStr, ast.BinOp, ast.Call)) and id(n) not in docs:
            s = _fold(n)
            if s and _flag_string(s):
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
        pp = pathlib.PurePosixPath(p)
        if pp in (OWNER, SELF):
            continue
        allow = OWNER_TESTS_RESOLVER_FUNCS if pp == OWNER_TESTS else frozenset()
        v = violations((ROOT / p).read_text(), allow_in=allow)
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


# Inputs that defeated the first version of the scan (Judge, PR #535). Each one
# reads the variable or spells a corpus default without being flagged. They are
# kept as their own list so the history of what the guard missed stays visible.
DEFEATS_OF_V1 = [
    # the f-string exemption was itself the hole: no literal text, used as a key
    'import os, run_case as rc\nx = os.environ.get(f"{rc.REFS_ENV}")\n',
    'import os, run_case as rc\nx = os.getenv(f"{rc.REFS_ENV}")\n',
    'import os, run_case as rc\nx = os.environ[f"{rc.REFS_ENV}"]\n',
    # literal text present, but still the argument of an environment read
    'import os, run_case as rc\nx = os.environ.get(f"{rc.REFS_ENV} ".strip())\n',
    # idiomatic path construction of the default
    'import pathlib\nx = pathlib.Path("/tmp") / "tr808-ref"\n',
    'from pathlib import Path\nx = Path("/tmp", "tr808-ref")\n',
    'import os\nx = os.path.join("/tmp", "tr808-ref")\n',
    # a NEW hard-coded root that never names the variable (pre-#522 measure_promoted_bands)
    'import pathlib\nx = pathlib.Path.home() / "dev/refs/sounds-tr808-fischer"\n',
    'import pathlib\nx = pathlib.Path.home() / "dev" / "refs" / "sounds-tr808-fischer"\n',
    'import os\nx = os.path.expanduser("~/dev/refs/sounds-tr808-fischer")\n',
    # the name assembled or looked up by string
    'import os\nx = os.environ.get("_".join(["TR808", "REFS"]))\n',
    'import run_case as rc\nx = getattr(rc, "REFS_DEFAULT")\n',
    'import run_case as rc\nx = vars(rc)["REFS_" + "ENV"]\n',
    # the alias constant was not banned at all
    'import os, run_case as rc\nx = os.environ.get(rc.REFS_ALIAS_ENV)\n',
    # shell / expandvars expansion of the variable inside a string
    'import os\nx = os.path.expandvars("$GF180_TR808_REFS/bd8")\n',
    'import subprocess\nsubprocess.run("ls ${TR808_REFS}/bd8", shell=True)\n',
]


@pytest.mark.parametrize("src", DEFEATS_OF_V1)
def test_inputs_that_defeated_the_first_scan_are_caught(src):
    assert violations(src), src


def test_docstrings_and_the_sanctioned_call_are_allowed():
    ok = '"""Reads $GF180_TR808_REFS via run_case."""\nimport run_case as rc\nx = rc.configured_refs()\n'
    assert violations(ok) == []
    msg = 'import run_case as rc\nm = f"set {rc.REFS_ENV}"\n'
    assert violations(msg) == []
    # prose that names the corpus repository or the variable is not a read
    prose = ('import run_case as rc\n'
             'm = f"clone tidalcycles/sounds-tr808-fischer or point GF180_TR808_REFS at it"\n'
             'n = "no corpus; clone sounds-tr808-fischer into /tmp"\n'
             'raise SystemExit(f"REFUSED: unset {rc.REFS_ENV} or fix it")\n')
    assert violations(prose) == []


def test_run_case_tests_are_exempt_only_inside_the_resolver_tests():
    """tools/test_run_case.py tests the resolver, so a few of its functions must
    name the variable. Only THOSE functions are exempt: a reader added anywhere
    else in that file is a second resolver and must be seen."""
    src = (ROOT / OWNER_TESTS).read_text()
    assert violations(src), "test_run_case.py does not trip the scan at all -- exemption is vacuous"
    assert violations(src, allow_in=OWNER_TESTS_RESOLVER_FUNCS) == []
    planted = src + '\n\ndef test_new():\n    x = rc.REFS_DEFAULT\n'
    assert violations(planted, allow_in=OWNER_TESTS_RESOLVER_FUNCS), "planted reader not seen"


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
    with pytest.raises(RuntimeError, match="^REFUSED: .*disagree"):  # silent pick would be the bug
        rc.configured_refs()
    monkeypatch.delenv("TR808_REFS")
    assert rc.configured_refs() == tmp_path / "b"


# Every reader that resolves the location AT IMPORT. The rest resolve it as an
# argparse default or inside a function; the scan covers them structurally.
MODULES = ["measure_promoted_bands", "measure_partial_balance",
           "estimator_defects", "audio_distance_floor"]


@pytest.mark.parametrize("mod", MODULES)
def test_readers_follow_the_documented_variable(mod, monkeypatch, tmp_path):
    """Run the reader's own refs resolution in a subprocess with the variable set.
    Each module exposes REFS or REFDIR, evaluated at import."""
    code = (f"import sys; sys.path[:0]=['{ROOT}/tools','{ROOT}/model','{ROOT}/tools/probes'];"
            f"import {mod} as m; print(getattr(m,'REFS',None) or m.REFDIR)")
    env = {"PATH": "/usr/bin:/bin", "GF180_TR808_REFS": str(tmp_path)}
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr[-1500:]
    assert r.stdout.strip() == str(tmp_path), r.stdout


def test_go_sh_exports_the_fischer_root_not_its_parent():
    txt = (ROOT / "fpga/reports/r2/settled/go.sh").read_text()
    line = [l for l in txt.splitlines() if l.startswith("export GF180_TR808_REFS=")][0]
    assert line.rstrip().endswith("/sounds-tr808-fischer"), line

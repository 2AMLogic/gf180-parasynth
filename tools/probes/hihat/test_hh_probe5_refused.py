"""hh_probe5.Refused must NOT subclass SystemExit (#1640; inverts #815's pin).

Measured on the pre-change tree (`origin/main` @ `a9fe1c53`), where the class was
`class Refused(refusal.Refused, SystemExit)` and its `__init__` called
`super().__init__(f"REFUSED: {why}")`: that resolves to `Exception.__init__`,
which sets `args` and never `code`, because `Exception` precedes `SystemExit`
in the MRO. CPython's top-level SystemExit handling reads `code`, finds None,
and exits 0 without printing. So an uncaught local refusal -- from a one-line
`raise hh_probe5.Refused(...)`, from the importable `hh_probe5.main()`, or from
`python3 tools/probes/hihat/hh_probe5.py --refs /tmp/no-such-refs ...` -- exited
0 with its reason on neither stream: a refusal that reads as PASS.

WRONG-THEN-RIGHT, recorded twice:
  * #815 kept the SystemExit base on the premise that it preserves "bypasses
    `except Exception`". It did not, and could not: refusal.Refused subclasses
    Exception, so every class that resolves to it is an Exception subclass,
    whatever else it also inherits.
  * #815's pin test (the previous version of this file) then recorded the
    surviving property as "an uncaught raise still exits through the SystemExit
    path (message to stderr, no traceback)". The first half was true; the second
    was false -- and that test caught the class in-process, so it never observed
    an exit code or stderr and could not see the difference.

What the base bought in practice was `except SystemExit`; what it cost was a
silent exit 0. The cost turns a refusal into a false PASS, so the base is gone:
`class Refused(refusal.Refused)`, the shape refusal.py's docstring prescribes,
and an uncaught refusal is a normal exception -- traceback on stderr, exit 1.

The exit-code tests run a fresh interpreter and read what the OS read. Each
weaker check ships the control that defeats it (verification-rules rule 8): the
pre-change shape exits 0 silently, and a SystemExit subclass whose `__init__`
does reach `SystemExit.__init__` exits 1 with the message on stderr and no
traceback -- so "non-zero with REFUSED: on stderr" alone cannot tell a normal
exception from the SystemExit path. The traceback line is what does.
"""
import ast
import os
import pathlib
import subprocess
import sys
import textwrap

import pytest

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "tools"))

import refusal                                  # noqa: E402
import hh_probe5                                # noqa: E402

PYTHONPATH = os.pathsep.join([str(HERE), str(ROOT / "tools")])

# The class as it was before #1640, reproduced inline as in the issue.
PRE_CHANGE_SHAPE = """\
class Base(Exception): pass
class R(Base, SystemExit):
    def __init__(self, why): super().__init__(f"REFUSED: {why}")
raise R("local precondition")
"""

# The shape that defeats "non-zero with REFUSED: on stderr": SystemExit.__init__
# runs, so `code` is set, so the interpreter prints it and exits 1 -- no traceback.
SYSTEM_EXIT_WITH_A_CODE = """\
class R(SystemExit):
    def __init__(self, why): super().__init__(f"REFUSED: {why}")
raise R("code is set")
"""


def _exit_of(script: str) -> subprocess.CompletedProcess:
    """Run `script` in a fresh interpreter and return what the OS saw."""
    return subprocess.run([sys.executable, "-c", script], cwd=ROOT, env={
        **os.environ, "PYTHONPATH": PYTHONPATH}, capture_output=True,
        text=True, timeout=120, check=False)


def test_refused_is_not_a_system_exit_subclass():
    assert not issubclass(hh_probe5.Refused, SystemExit)


def test_refused_resolves_to_the_common_base():
    assert issubclass(hh_probe5.Refused, refusal.Refused)


def test_common_boundary_catches_it_and_except_system_exit_does_not():
    with pytest.raises(refusal.Refused,
                       match=r"^REFUSED: the apparatus precondition failed$"):
        raise hh_probe5.Refused("the apparatus precondition failed")
    with pytest.raises(hh_probe5.Refused):
        try:
            raise hh_probe5.Refused("the apparatus precondition failed")
        except SystemExit:  # the precise catch #815 kept; #1640 removes it
            pytest.fail("except SystemExit caught the local refusal")


def test_uncaught_local_refusal_exits_1_with_a_traceback_on_stderr():
    r = _exit_of("import hh_probe5; "
                 "raise hh_probe5.Refused('local precondition')")
    assert r.returncode == 1, (r.returncode, r.stdout, r.stderr)
    assert "Traceback (most recent call last)" in r.stderr, r.stderr
    assert "REFUSED: local precondition" in r.stderr
    assert "REFUSED:" not in r.stdout


def test_uncaught_refusal_from_the_importable_entry_point_exits_1(tmp_path):
    refs = tmp_path / "no-such-refs"
    argv = ["--refs", str(refs), "--out", str(tmp_path / "out"),
            "--quick", "--seconds", "0.05"]
    r = _exit_of(f"import hh_probe5; hh_probe5.main({argv!r})")
    assert r.returncode == 1, (r.returncode, r.stdout, r.stderr)
    assert "Traceback (most recent call last)" in r.stderr, r.stderr
    assert f"REFUSED: reference corpus not at {refs}" in r.stderr
    assert "REFUSED:" not in r.stdout


def test_the_surrogate_sweep_handler_still_records_refused_rows():
    # main() cannot reach its sweep without the reference corpus, so the one
    # `except Refused` block is run here, from the shipped source, against a
    # cy_kit that refuses.
    tree = ast.parse(pathlib.Path(hh_probe5.__file__).read_text())
    blocks = [n for n in ast.walk(tree) if isinstance(n, ast.Try)
              and any(isinstance(h.type, ast.Name) and h.type.id == "Refused"
                      for h in n.handlers)
              and any(isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
                      and c.func.id == "cy_kit" for c in ast.walk(n))]
    assert len(blocks) == 1, len(blocks)
    rows: list = []

    def refuse(*a, **k):
        raise hh_probe5.Refused("the apparatus precondition failed")

    src = "for q in (1.8,):\n" + textwrap.indent(ast.unparse(blocks[0]), "    ")
    exec(src, {**vars(hh_probe5), "rows": rows, "arm": "2pole", "g": 1.0,  # noqa: S102
               "cy_kit": refuse})
    assert rows == [{"q": 1.8, "gain": 1.0,
                     "refused": "REFUSED: the apparatus precondition failed"}]


def test_control_pre_change_shape_exits_0_silently():
    r = _exit_of(PRE_CHANGE_SHAPE)
    assert (r.returncode, r.stderr, r.stdout) == (0, "", ""), (
        r.returncode, r.stdout, r.stderr)


def test_control_system_exit_with_a_code_exits_1_without_a_traceback():
    r = _exit_of(SYSTEM_EXIT_WITH_A_CODE)
    assert r.returncode == 1, (r.returncode, r.stdout, r.stderr)
    assert "REFUSED: code is set" in r.stderr
    assert "Traceback" not in r.stderr

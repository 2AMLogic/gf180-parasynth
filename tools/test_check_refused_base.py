"""tools/check_refused_base.py: the guard bites on a non-conforming
definition, passes a sibling-chained conforming one, ignores alias and
from-import reuse, exempts a bare @dataclass record but not a bare exception
class keyed on its name, REFUSES (exit 3) on a parse failure, and ENFORCES:
a non-conforming definition exits 1 (#815)."""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import check_refused_base as cb                # noqa: E402


def _write(tmp_path, name, src):
    p = tmp_path / name
    p.write_text(src)
    return p


def test_a_nonconforming_definition_is_reported(tmp_path):
    p = _write(tmp_path, "fix_bare.py",
               "class Refused(RuntimeError):\n    pass\n")
    bad, total, exempt = cb.nonconforming([p], cb.repo_root())
    assert len(bad) == 1 and total == 1 and exempt == []
    key, line, name, bases = bad[0]
    assert name == "Refused" and bases == "RuntimeError" and line == 1
    assert "fix_bare.py" in key


def test_a_sibling_chained_conforming_definition_is_not_reported(tmp_path):
    root = _write(tmp_path, "fix_root.py",
                  "import refusal\n\n\nclass Refused(refusal.Refused):\n    pass\n")
    child = _write(tmp_path, "fix_child.py",
                   "import fix_root\n\n\nclass Refused(fix_root.Refused):\n    pass\n")
    grand = _write(tmp_path, "fix_grand.py",
                   "from fix_child import Refused as base\n\n\nclass Refused(base):\n"
                   "    pass\n")
    bad, total, exempt = cb.nonconforming([root, child, grand], cb.repo_root())
    assert bad == [], bad
    assert total == 3 and exempt == []


def test_alias_and_from_import_reuse_is_never_flagged(tmp_path):
    base = _write(tmp_path, "fix_base.py",
                  "import refusal\n\n\nclass Refused(refusal.Refused):\n    pass\n")
    aliaser = _write(tmp_path, "fix_alias.py",
                     "import fix_base\n\nRefused = fix_base.Refused\n")
    reimporter = _write(tmp_path, "fix_reimport.py",
                        "from fix_base import Refused\n")
    bad, total, exempt = cb.nonconforming([base, aliaser, reimporter], cb.repo_root())
    assert bad == []
    assert total == 1                          # only fix_base defines a class
    assert exempt == []


def test_a_chained_definition_still_bites_when_the_root_does_not_conform(tmp_path):
    root = _write(tmp_path, "fix_low.py",
                  "class Refused(Exception):\n    pass\n")
    child = _write(tmp_path, "fix_high.py",
                   "import fix_low\n\n\nclass Refused(fix_low.Refused):\n    pass\n")
    bad, _, _ = cb.nonconforming([root, child], cb.repo_root())
    assert len(bad) == 2                       # both the root and the chain


def test_a_parse_failure_refuses_exit_3(tmp_path, capsys):
    p = _write(tmp_path, "broken.py", "class Refused(RuntimeError\n")
    assert cb.main(["--paths", str(p)]) == 3
    assert "broken.py" in capsys.readouterr().err


def test_a_direct_base_on_the_canonical_module_conforms(tmp_path):
    p = _write(tmp_path, "fix_direct.py",
               "import refusal\n\n\nclass Refused(refusal.Refused):\n"
               "    pass\n\n\nclass Refusal(refusal.Refused):\n    pass\n")
    bad, total, exempt = cb.nonconforming([p], cb.repo_root())
    assert bad == [] and total == 2 and exempt == []


def test_the_canonical_base_itself_is_not_reported():
    bad, total, exempt = cb.nonconforming([cb.repo_root() / "tools/refusal.py"],
                                          cb.repo_root())
    # the file's one definition is the exempt base itself: nothing is reportable
    assert bad == [] and total == 0 and exempt == []


# ---- enforcing mode (#815) ---------------------------------------------------

def test_enforcing_mode_exits_1_on_a_nonconforming_definition(tmp_path, capsys):
    p1 = _write(tmp_path, "fix_one.py",
                "class Refused(RuntimeError):\n    pass\n")
    p2 = _write(tmp_path, "fix_two.py",
                "class Refused(ValueError):\n    pass\n")
    assert cb.main(["--paths", str(p1), str(p2)]) == 1
    out = capsys.readouterr().out
    assert "NON-CONFORMING" in out
    assert "fix_one.py:Refused(RuntimeError)" in out
    assert "fix_two.py:Refused(ValueError)" in out
    assert "2 of 2 definition(s) do not resolve" in out


def test_enforcing_mode_exits_0_when_everything_conforms(tmp_path, capsys):
    p = _write(tmp_path, "fix_ok.py",
               "import refusal\n\n\nclass Refused(refusal.Refused, RuntimeError):\n"
               "    pass\n")
    assert cb.main(["--paths", str(p)]) == 0
    assert "0 of 1 definition(s) do not resolve" in capsys.readouterr().out


# ---- the dataclass-record carve-out (#815; fpga/midi_session.py Refusal) ------

def test_a_bare_dataclass_record_is_exempt(tmp_path):
    p = _write(tmp_path, "fix_record.py",
               "from dataclasses import dataclass\n\n\n@dataclass\n"
               "class Refusal:\n    t: float\n    raw: bytes\n")
    bad, total, exempt = cb.nonconforming([p], cb.repo_root())
    assert bad == [] and total == 1 and len(exempt) == 1
    assert "fix_record.py:Refusal()" in exempt[0]


def test_the_exemption_is_keyed_on_dataclass_not_on_the_name(tmp_path):
    # rule 8: the defeating input for a name-keyed carve-out is an exception
    # class that merely calls itself Refusal. A bare, non-dataclass class is
    # NOT exempt: it is a plain object subclass, and if it were ever raised
    # the raise itself would TypeError -- the guard still flags it.
    p = _write(tmp_path, "fix_named.py", "class Refusal:\n    pass\n")
    bad, total, exempt = cb.nonconforming([p], cb.repo_root())
    assert len(bad) == 1 and exempt == [] and total == 1


def test_a_dataclass_with_bases_is_still_checked(tmp_path):
    # the carve-out is no-bases + dataclass: a decorated class that DOES have
    # a base is an exception hierarchy member and must conform like any other
    p = _write(tmp_path, "fix_dc_base.py",
               "from dataclasses import dataclass\n\n\n@dataclass\n"
               "class Refusal(RuntimeError):\n    code: int\n")
    bad, total, exempt = cb.nonconforming([p], cb.repo_root())
    assert len(bad) == 1 and exempt == [] and total == 1


def test_the_qualified_dataclasses_dataclass_decorator_is_recognised(tmp_path):
    p = _write(tmp_path, "fix_qualified.py",
               "import dataclasses\n\n\n@dataclasses.dataclass\n"
               "class Refused:\n    t: float\n")
    bad, total, exempt = cb.nonconforming([p], cb.repo_root())
    assert bad == [] and total == 1 and len(exempt) == 1


def test_the_midi_session_record_is_exempt_in_the_real_tree():
    bad, total, exempt = cb.nonconforming(
        [cb.repo_root() / "fpga/midi_session.py"], cb.repo_root())
    assert bad == [] and total == 1
    assert exempt == ["fpga/midi_session.py:Refusal()"]


def test_the_committed_tree_is_clean():
    # the #815 acceptance gate, re-run on every pytest pass: after the re-base
    # the whole tracked tree resolves to refusal.Refused (one exempt dataclass
    # record), so enforcing mode exits 0 -- an unsatisfiable gate is worse
    # than no gate.
    assert cb.main([]) == 0

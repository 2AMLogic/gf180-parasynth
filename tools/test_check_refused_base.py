"""tools/check_refused_base.py: the guard bites on a non-conforming
definition, passes a sibling-chained conforming one, ignores alias and
from-import reuse, REFUSES (exit 3) on a parse failure, and in report/baseline
mode stays green while REPORTING a NEW non-conforming definition (#784; #815
flips the guard -- and that exit -- to enforcing)."""
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
    bad, total = cb.nonconforming([p], cb.repo_root())
    assert len(bad) == 1 and total == 1
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
    bad, total = cb.nonconforming([root, child, grand], cb.repo_root())
    assert bad == [], bad
    assert total == 3


def test_alias_and_from_import_reuse_is_never_flagged(tmp_path):
    base = _write(tmp_path, "fix_base.py",
                  "import refusal\n\n\nclass Refused(refusal.Refused):\n    pass\n")
    aliaser = _write(tmp_path, "fix_alias.py",
                     "import fix_base\n\nRefused = fix_base.Refused\n")
    reimporter = _write(tmp_path, "fix_reimport.py",
                        "from fix_base import Refused\n")
    bad, total = cb.nonconforming([base, aliaser, reimporter], cb.repo_root())
    assert bad == []
    assert total == 1                          # only fix_base defines a class


def test_a_chained_definition_still_bites_when_the_root_does_not_conform(tmp_path):
    root = _write(tmp_path, "fix_low.py",
                  "class Refused(Exception):\n    pass\n")
    child = _write(tmp_path, "fix_high.py",
                   "import fix_low\n\n\nclass Refused(fix_low.Refused):\n    pass\n")
    bad, _ = cb.nonconforming([root, child], cb.repo_root())
    assert len(bad) == 2                       # both the root and the chain


def test_a_parse_failure_refuses_exit_3(tmp_path, capsys):
    p = _write(tmp_path, "broken.py", "class Refused(RuntimeError\n")
    assert cb.main(["--paths", str(p)]) == 3
    assert "broken.py" in capsys.readouterr().err


def test_a_direct_base_on_the_canonical_module_conforms(tmp_path):
    p = _write(tmp_path, "fix_direct.py",
               "import refusal\n\n\nclass Refused(refusal.Refused):\n"
               "    pass\n\n\nclass Refusal(refusal.Refused):\n    pass\n")
    bad, total = cb.nonconforming([p], cb.repo_root())
    assert bad == [] and total == 2


def test_the_canonical_base_itself_is_not_reported():
    bad, total = cb.nonconforming([cb.repo_root() / "tools/refusal.py"], cb.repo_root())
    # the file's one definition is the exempt base itself: nothing is reportable
    assert bad == [] and total == 0


def test_report_mode_stays_green_while_reporting_a_new_definition(tmp_path, capsys):
    p1 = _write(tmp_path, "fix_one.py",
                "class Refused(RuntimeError):\n    pass\n")
    p2 = _write(tmp_path, "fix_two.py",
                "class Refused(ValueError):\n    pass\n")
    baseline = tmp_path / "baseline.json"

    assert cb.main(["--paths", str(p1), "--baseline", str(baseline),
                    "--write-baseline"]) == 0
    assert cb.main(["--paths", str(p1), "--baseline", str(baseline)]) == 0

    # a NEW non-conforming definition is reported loudly but stays green: an
    # exit 1 here would red `make verify` on `main` for every unrelated merge
    # that adds a refusal class. The enforcing flip is #815's, together with
    # the whole-set re-base.
    assert cb.main(["--paths", str(p1), str(p2),
                    "--baseline", str(baseline)]) == 0
    out = capsys.readouterr().out
    # fixtures live outside the repo, so their keys are absolute paths; the
    # basename plus the loud NEW/REPORTED markers are the stable part
    assert "NEW: " in out and "fix_two.py:Refused(ValueError)" in out
    assert "REPORTED" in out
    # ... and it is absorbed only by an explicit baseline write
    assert cb.main(["--paths", str(p1), str(p2), "--baseline", str(baseline),
                    "--write-baseline"]) == 0
    # a definition that goes away shrinks the baseline: exit 0, with a note
    result = cb.main(["--paths", str(p1), "--baseline", str(baseline)])
    assert result == 0

    # a missing baseline refuses rather than passing silently
    assert cb.main(["--paths", str(p1), "--baseline",
                    str(tmp_path / "absent.json")]) == 3


def test_baseline_keys_are_repo_relative_and_travel_between_roots(tmp_path):
    # The defect the CI red exposed: keys built from absolute paths can only
    # match the checkout that wrote the baseline. The same tree content under
    # two different roots must yield identical, repo-relative keys.
    for name in ("checkout-a", "checkout-b"):
        root = tmp_path / name
        (root / "tools").mkdir(parents=True)
        (root / "pkg").mkdir()
        (root / "tools" / "refusal.py").write_text(
            "class Refused(Exception):\n    pass\n")
        (root / "pkg" / "fix_bare.py").write_text(
            "class Refused(RuntimeError):\n    pass\n")
    keys = []
    for name in ("checkout-a", "checkout-b"):
        root = tmp_path / name
        bad, total = cb.nonconforming(
            sorted(root.glob("*/*.py")), root)
        assert total == 1
        keys.append([k for k, *_ in bad])
    assert keys[0] == keys[1], keys
    assert keys[0] == ["pkg/fix_bare.py:Refused(RuntimeError)"]
    assert not any(k.startswith("/") for k in keys[0])


def test_the_committed_tree_matches_its_baseline():
    assert cb.main([]) == 0

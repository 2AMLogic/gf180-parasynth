"""Exactly one implementation of the BS.1770 K-weighting coefficients (#828).

tools/ab_808_loud.py and tools/make_cymbal_pack.py each carried their own
typed-out copy of the coefficients and had drifted from the gate's
(tools/perceptual_gate.py) in edge cases: epsilon 1e-20 vs 1e-30, and a clip
shorter than one 50 ms block raised ValueError in the pack copies. Both pack
tools now import perceptual_gate. This guard counts occurrences of the head
coefficient literal 1.53512485958697 across tools/*.py and demands exactly one
file carrying it: tools/perceptual_gate.py.

Injected-copy control: test_a_seeded_copy_makes_the_guard_fire seeds a second
copy into a scratch directory and proves the counter sees it AND the
single-copy assertion fails there -- the guard can fire, not just pass.

What this guard cannot see (docs/verification-rules.md rule 8): a fourth copy
that splits the literal across string pieces, reformats it
(1.535124859586970), truncates trailing digits, or computes the coefficients
analytically satisfies the count while violating the intent. The grep is a
tripwire against copy-paste duplication, not a proof that no other
K-weighting implementation exists; review owns the rest.
"""
from __future__ import annotations

import pathlib

import pytest

TOOLS = pathlib.Path(__file__).resolve().parent
HEAD_COEF = "1.53512485958697"


def files_carrying_the_literal(root):
    hits = []
    for p in sorted(pathlib.Path(root).glob("*.py")):
        if p.name == pathlib.Path(__file__).name:
            continue  # this guard names the literal to test it; it does not count itself
        n = p.read_text(encoding="utf-8").count(HEAD_COEF)
        if n:
            hits.append((p.name, n))
    return hits


def assert_single_implementation(root):
    hits = files_carrying_the_literal(root)
    assert hits == [("perceptual_gate.py", 1)], (
        "the BS.1770 K-weighting coefficients must be typed in exactly one "
        f"file (tools/perceptual_gate.py); import it, do not copy it: {hits}")


def test_exactly_one_implementation():
    assert_single_implementation(TOOLS)


def test_a_seeded_copy_makes_the_guard_fire(tmp_path):
    # the control: the same scan that passes on tools/ must FAIL once a second
    # copy exists, so a guard that can only pass cannot hide here.
    assert files_carrying_the_literal(TOOLS) == [("perceptual_gate.py", 1)]
    (tmp_path / "seeded_fourth_copy.py").write_text(
        "b, a = [1.53512485958697, -2.69169618940638, 1.19839281085285], []\n",
        encoding="utf-8")
    assert files_carrying_the_literal(tmp_path) == [("seeded_fourth_copy.py", 1)]
    with pytest.raises(AssertionError):
        assert_single_implementation(tmp_path)

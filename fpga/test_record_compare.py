"""The shared release staleness comparison (fpga/record_compare.py, #785).

Four hand-copied comparators decided BOUND vs STALE for the committed release
records, and two of them disagreed. This file pins the table that showed the
disagreement, keeps the pre-#785 comparator as a control that MUST STAY WRONG
(docs/verification-rules.md rule 5 -- a bug is not closed until it is an
injection), records the two inputs that defeat the repaired one (rule 8), and
asserts that no copy has drifted back into the five modules that had one.

The controls that drive the SHIPPING verdict rather than this helper are
`test_control_a_key_dropped_from_the_derivation_is_stale_not_bound` in
fpga/release/test_r1_candidate.py and fpga/release/test_release_manifest.py.
Both of those fail on the commit before this module existed, which is this
change's start-red; a unit test on a comparator cannot start red against its
own implementation, so it is not offered as that evidence.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT / "fpga", ROOT / "fpga/release", ROOT / "model", ROOT / "rtl-sketch",
           ROOT / "audition", ROOT / "tools"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import record_compare as rc                      # noqa: E402

#: every module that carried a copy, and what it carried
CONSOLIDATED = ("r1_candidate", "r1_release", "release_manifest", "r2_candidate")
REL_COPIES = (("r1_candidate", "fpga/release"), ("r1_release", "fpga/release"),
              ("release_manifest", "fpga/release"), ("r2_candidate", "fpga/release"),
              ("verify_uart_bridge", "fpga"))


def _pre_785_diff(a, b, path=""):
    """The comparator three of the four modules carried, verbatim.

    It is kept, and must keep being wrong, because it is the defect this change
    repaired: `a.get(k)` / `b.get(k)` makes a key ABSENT on one side the same
    input as a key present with value `null`.
    """
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for k in sorted(set(a) | set(b)):
            out += _pre_785_diff(a.get(k), b.get(k), f"{path}.{k}" if path else k)
        return out
    return [] if a == b else [path]


def _pre_785_r2(committed, fresh):
    """r2_candidate's drifted variant, verbatim -- `(stale, named_keys)`.

    The two halves do not agree with each other, which is sharper than the
    issue's table recorded: the whole-dict `!=` has no `.get()` hole and so
    catches the absent-vs-null case, but the SHALLOW key list it then builds to
    say WHERE has exactly the same hole, and comes back empty. So this variant
    reported `differs from a fresh derivation at []` -- a correct verdict that
    could not name its own reason.
    """
    if committed != fresh:
        return True, sorted(k for k in set(committed) | set(fresh)
                            if committed.get(k) != fresh.get(k))
    return False, []


# ---- the divergence table, control case last ---------------------------------
#: (name, committed, derivation, must the repaired comparator call it STALE?)
TABLE = (
    ("field dropped from derivation vs committed null",
     {"calibration": None}, {}, True),
    ("nested field dropped vs null",
     {"presets": {"default": {"calibration": None}}},
     {"presets": {"default": {}}}, True),
    ("int 1 vs bool true", {"n": 1}, {"n": True}, True),
    ("int 1 vs float 1.0", {"n": 1}, {"n": 1.0}, True),
    # the control: a plain value change every variant always caught. Without
    # it the table cannot be shown to discriminate at all.
    ("real value change (control)", {"n": 1}, {"n": 2}, True),
    # and the other direction, so "STALE for everything" cannot pass the table
    ("identical records (negative control)",
     {"n": 1, "k": None, "l": [1, None]}, {"n": 1, "k": None, "l": [1, None]}, False),
)


@pytest.mark.parametrize("name,committed,fresh,stale", TABLE, ids=[t[0] for t in TABLE])
def test_the_shared_comparator_decides_every_row_of_the_divergence_table(
        name, committed, fresh, stale):
    assert bool(rc.diff(committed, fresh)) is stale, \
        f"{name}: diff said {rc.diff(committed, fresh)!r}"


@pytest.mark.parametrize("name,committed,fresh,stale", TABLE[:2], ids=[t[0] for t in TABLE[:2]])
def test_control_the_pre_785_comparator_still_misses_the_absent_vs_null_rows(
        name, committed, fresh, stale):
    """The injected defect, kept red by construction: the comparator that
    shipped until #785 calls these two BOUND. If this test ever fails, the
    historical bug has been edited away and the control stops being one."""
    assert stale is True                        # the row IS a real difference
    assert _pre_785_diff(committed, fresh) == [], \
        f"{name}: the pre-785 comparator no longer has the hole this controls for"
    assert rc.diff(committed, fresh), f"{name}: the repaired comparator must catch it"


def test_control_the_two_generations_disagreed_on_exactly_the_null_rows():
    """The claim the issue was filed on, as a test: r2_candidate's variant
    caught what the recursive one missed, and they agreed on every other row --
    including both controls, which is what shows the table discriminates."""
    disagreed = [name for name, a, b, _ in TABLE
                 if bool(_pre_785_diff(a, b)) != _pre_785_r2(a, b)[0]]
    assert disagreed == ["field dropped from derivation vs committed null",
                         "nested field dropped vs null"], disagreed


def test_control_the_drifted_variants_verdict_and_its_key_list_disagreed():
    """Both pre-785 comparators shared the `.get()` hole. r2_candidate escaped a
    wrong VERDICT only because a whole-dict `!=` ran first; the shallow key list
    it then built to say WHERE has the hole, so it could not fully name what the
    verdict had caught.

    Measured rather than assumed, and the first version of this test asserted
    the wrong thing: the key list is EMPTY only when the dropped key is at the
    TOP level. For a nested drop the top-level ancestor's subtrees are unequal
    by `!=`, so the list names the ancestor -- never the leaf, which is the
    path an operator needs.
    """
    top_name, top_committed, top_fresh, _ = TABLE[0]
    stale, named = _pre_785_r2(top_committed, top_fresh)
    assert (stale, named) == (True, []), f"{top_name}: {named}"
    assert rc.diff(top_committed, top_fresh) == ["calibration"]

    nested_name, nested_committed, nested_fresh, _ = TABLE[1]
    stale, named = _pre_785_r2(nested_committed, nested_fresh)
    assert (stale, named) == (True, ["presets"]), f"{nested_name}: {named}"
    assert rc.diff(nested_committed, nested_fresh) == ["presets.default.calibration"]


def test_the_drifted_variant_reported_a_coarser_path_than_the_others():
    """Not just a different verdict: where the recursive copies name
    `presets.default.calibration`, r2_candidate's shallow key list named
    `presets`. One format now, for all five gates."""
    committed = {"presets": {"default": {"calibration": 1}}}
    fresh = {"presets": {"default": {"calibration": 2}}}
    assert _pre_785_r2(committed, fresh) == (True, ["presets"])
    assert rc.diff(committed, fresh) == ["presets.default.calibration"]


# ---- type strictness: the decision recorded in record_compare's docstring -----
#: pairs Python's `==` calls EQUAL and `json.dumps` writes differently -- the
#: holes all four copies shared, and the ones the DECISION block is about
CONFLATED = [(1, True), (0, False), (1, 1.0), (0, 0.0), (True, 1.0)]
#: pairs Python already separates: here to show the strict rule did not have to
#: break them to close the ones above
ALREADY_DISTINCT = [(1, "1"), (None, 0), (None, ""), (None, False), ([], {})]


@pytest.mark.parametrize("a,b", CONFLATED)
def test_values_python_equality_conflates_are_different_records(a, b):
    import json
    assert a == b, "this pair is no longer a conflation; move it"
    assert json.dumps(a) != json.dumps(b), "and json writes them differently"
    assert rc.diff({"n": a}, {"n": b}) == ["n"]
    assert rc.diff({"n": b}, {"n": a}) == ["n"]


@pytest.mark.parametrize("a,b", ALREADY_DISTINCT)
def test_values_python_already_separates_stay_separated(a, b):
    assert a != b
    assert rc.diff({"n": a}, {"n": b}) == ["n"]
    assert rc.diff({"n": b}, {"n": a}) == ["n"]


def test_the_coercion_holes_are_closed_inside_lists_too():
    """`diff` compares lists as units, so this is the only place the type
    strictness could have been skipped."""
    assert rc.diff({"l": [1]}, {"l": [True]}) == ["l"]
    assert rc.diff({"l": [{"k": 1}]}, {"l": [{"k": 1.0}]}) == ["l"]
    assert rc.diff({"l": [{"k": None}]}, {"l": [{}]}) == ["l"]
    assert rc.diff({"l": [1, 2]}, {"l": [1, 2]}) == []


def test_a_type_change_at_the_top_of_a_subtree_is_reported_not_descended():
    assert rc.diff({"k": {"a": 1}}, {"k": [1]}) == ["k"]
    assert rc.diff({"k": {"a": 1}}, {"k": None}) == ["k"]


# ---- rule 8: the inputs that defeat this guard, as controls -------------------
def test_defeat_1_a_key_order_change_alone_is_bound_by_design():
    """STATED, not overlooked (record_compare's rule-8 block): the record's
    CONTENT is identical, and this gate asks whether the record equals its
    derivation, not whether the file is byte-identical. Byte identity is caught
    by a stronger mechanism one level up -- r1_release.host_binding() hashes
    r1-candidate.json's actual bytes into r1-2025.1.json."""
    assert rc.diff({"a": 1, "b": 2}, {"b": 2, "a": 1}) == []
    # the backstop itself, asserted rather than described:
    src = (ROOT / "fpga/release/r1_release.py").read_text()
    assert 'sha(CANDIDATE)' in src, \
        "the byte-level backstop this carve-out relies on is gone; re-decide the carve-out"


def test_defeat_2_a_change_inside_a_list_is_reported_at_the_lists_path():
    """Nothing is missed; the path is coarser than a dict's. Stated so that a
    reader chasing `presets.m5a-saw.waves[1]` knows why it reads
    `presets.m5a-saw.waves`."""
    assert rc.diff({"waves": ["saw", "saw"]}, {"waves": ["saw", "square"]}) == ["waves"]


def test_a_nan_makes_a_record_stale_forever_which_is_the_loud_direction():
    """#134's shape: IEEE comparisons with NaN are always False. Recorded as a
    decision -- a gate that cannot go BOUND gets fixed, whereas one that cannot
    go STALE is the hole #785 repaired. No committed record holds a NaN."""
    nan = float("nan")
    assert rc.diff({"x": nan}, {"x": nan}) == ["x"]
    import json
    for rec in sorted((ROOT / "fpga/release").glob("*.json")):
        assert "NaN" not in rec.read_text(), rec
        json.loads(rec.read_text())            # and it is standard JSON


# ---- anti-drift: no module carries a copy anymore -----------------------------
@pytest.mark.parametrize("mod", CONSOLIDATED)
def test_every_consolidated_module_uses_the_shared_comparison_object(mod):
    """Identity, not behaviour: a re-copied `_diff` would be a different
    object even if it happened to agree on today's inputs."""
    m = __import__(mod)
    assert m._diff is rc.diff, f"{mod}._diff is no longer fpga/record_compare.diff"


def test_r2_release_reaches_the_shared_comparison_through_r1_release():
    """r2_release was already consolidated against r1_release (its line 68
    aliases r1_release's helpers) and is the pattern this change extended."""
    import r1_release
    import r2_release
    assert r2_release.r1r is r1_release
    assert r1_release._diff is rc.diff


@pytest.mark.parametrize("mod,where", REL_COPIES, ids=[m for m, _ in REL_COPIES])
def test_every_rel_copy_reports_a_path_outside_the_tree_instead_of_crashing(mod, where):
    """The fifth `_rel` copy (r2_candidate) had no outside-ROOT fallback and
    raised ValueError on a test's substituted record; the other four returned
    the absolute path. All five now agree, via record_compare.rel."""
    m = __import__(mod)
    outside = Path("/tmp/not-in-this-tree/record.json")
    assert m._rel(outside) == str(outside)
    assert m._rel(ROOT / where / f"{mod}.py") == f"{where}/{mod}.py"


def test_no_recursive_get_comparator_has_been_copied_back_in():
    """A smoke test over the two directories that had copies, for the literal
    regression -- a fresh `a.get(k)` recursion. It cannot catch a comparator
    written from scratch under another name in a new file; the identity
    assertions above are the exact check, and they cover only these five
    modules. Both are stated rather than claimed to be complete."""
    # the literal historical shape: a recursive call fed `a.get(k)`/`b.get(k)`.
    # An earlier, looser pattern (any line with two `.get(k)` calls) matched
    # r1_release.py:182 -- a FLAT sha-dict comparison with no recursion -- so
    # the gate was unsatisfiable the first time it was run. Run against the
    # tree before committing, per CLAUDE.md.
    shape = re.compile(r"\(\s*a\.get\(k[,)]\s*,\s*b\.get\(k[,)]")
    offenders = []
    for p in sorted((ROOT / "fpga/release").glob("*.py")) + [ROOT / "fpga/verify_uart_bridge.py"]:
        if p.name.startswith("test_"):
            continue
        for i, line in enumerate(p.read_text().splitlines(), 1):
            if shape.search(line):
                offenders.append(f"{p.relative_to(ROOT)}:{i}: {line.strip()}")
    assert offenders == [], "a recursive .get() comparator is back:\n" + "\n".join(offenders)

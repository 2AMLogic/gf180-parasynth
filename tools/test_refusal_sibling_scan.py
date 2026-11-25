#!/usr/bin/env python3
"""Controls for `tools/refusal_sibling_scan.py` (#1162): the scanner's
classifications are pinned against a synthetic tree where every relation is
known by construction, so a misclassification cannot ship silently.

Each control plants the exact shape it names -- the shapes are the ones the
#1160/#1162 repairs found in the real tree, and the false positives are the
ones the #1160 one-off dotted-call grep actually produced (a local variable
named like a module; a callee with no refusal family; a callee whose FUNCTION
raises nothing even though its module has a family). The #1188 section pins
the three gaps that issue filed (try-handler credit, stem collision, alias
loss) and the #1193 section the borrowed-family gap: each control there
started red against the unfixed scanner, except the shadow pin noted inline.
The #1214 section pins the ambiguous-callee filter (a non-raising collision
is not a site), the repo-relative `candidates` format, and the shadowed-alias
blind spot #1188's test plan named but never pinned.
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import refusal_sibling_scan as rss                     # noqa: E402


MOD_A = '''
import refusal

class Refused(refusal.Refused, Exception):
    pass

def load(path):
    raise Refused(f"unusable: {path}")

def quiet(x):
    return x
'''

MOD_B = '''
import refusal
from mod_a import Refused as _unused          # a family module importing another

class Refused(refusal.Refused, Exception):
    pass

def measure(x):
    raise Refused("band is silent")
'''

PLAIN = '''
def helper(x):
    return x / 0
'''

CALLER_SIBLING_GUARD = '''
import refusal
import mod_a

class Refused(refusal.Refused, Exception):
    pass

def go(p):
    try:
        mod_a.load(p)
    except Refused as e:
        print("REFUSED", e)
'''

CALLER_BASE_GUARD = '''
import refusal
import mod_a

class Refused(refusal.Refused, Exception):
    pass

def go(p):
    try:
        mod_a.load(p)
    except refusal.Refused as e:
        print("REFUSED", e)
'''

CALLER_UNGUARDED = '''
import mod_a

def go(p):
    return mod_a.load(p)
'''

CALLER_ALIAS_EXACT = '''
import refusal
import mod_b as mb                 # alias != stem: resolution must follow it

Refused = mb.Refused

def go(x):
    try:
        mb.measure(x)
    except Refused as e:
        print("REFUSED", e)
'''

CALLER_QUIET_FUNCTION = '''
import mod_a

def go(p):
    return mod_a.quiet(p)          # module has a family; THIS function raises none
'''

CALLER_LOCAL_VAR_FALSE_POSITIVE = '''
def go(ref):
    return ref.regs(3)             # a local VARIABLE named ref: no import, no site
'''

CALLER_BROAD_GUARD = '''
import mod_a

def go(p):
    try:
        mod_a.load(p)
    except Exception:
        return None
'''

CALLER_EXACT_MODULE_GUARD = '''
import refusal
import mod_a

def go(p):
    try:
        mod_a.load(p)
    except mod_a.Refused as e:
        print("REFUSED", e)
'''

CALLER_CLI_BASE_GUARD = '''
import sys
import refusal
import mod_a

def go(p):
    return mod_a.load(p)           # unguarded HERE; caught at the boundary

if __name__ == "__main__":
    try:
        sys.exit(go("x"))
    except refusal.Refused as e:
        print(f"REFUSED  {e}")
        sys.exit(2)
'''

CALLER_EXCEPT_CLAUSE = '''
import refusal
import mod_a
import mod_b

def go(p):
    try:
        return mod_b.measure(p)   # body: this try's handlers DO cover it
    except refusal.Refused:
        return mod_a.load(p)      # except clause: they do NOT
'''

CALLER_ELSE_CLAUSE = '''
import mod_a

def go(p):
    try:
        touch(p)
    except Exception:
        touch(p)
    else:
        return mod_a.load(p)      # else clause: not covered by this try
'''

CALLER_FINALLY_CLAUSE = '''
import refusal
import mod_a

def go(p):
    try:
        touch(p)
    except refusal.Refused:
        touch(p)
    finally:
        return mod_a.load(p)      # finally clause: not covered by this try
'''

CALLER_NESTED_IN_HANDLER = '''
import refusal
import mod_a

def go(p):
    try:
        touch(p)
    except refusal.Refused:
        try:
            return mod_a.load(p)  # inner body: inner try has no handlers,
        finally:                  # and the outer guards only ITS body
            touch(p)
'''

CALLER_FROM_IMPORT_ALIAS = '''
from mod_a import load as l

def go(p):
    return l(p)                   # aliased from-import of a raising function
'''

CALLER_FROM_IMPORT_QUIET_ALIAS = '''
from mod_a import quiet as q

def go(p):
    return q(p)                   # alias of a non-raising function: no site
'''

PLAY_ALPHA = '''
import refusal

class Refused(refusal.Refused, Exception):
    pass

def run(x):
    raise Refused("no records")
'''

PLAY_BETA = '''
import mod_a

def go(p):
    return mod_a.load(p)          # this file's OWN site must be reported
'''

PLAY_GAMMA = '''
import mod_b

def go(x):
    return mod_b.measure(x)       # a third same-stem site
'''

CALLER_AMBIGUOUS_STEM = '''
from play import run

def go(p):
    return run(p)                 # WHICH play? the census must not guess
'''


# --- #1214 shapes: the ambiguous-callee filter and the candidates format ----
# Two same-stem candidates whose callees raise nothing used to be emitted as
# `ambiguous-callee` anyway: the multi-candidate branch had no equivalent of
# the single-callee `no family at all` / `this function raises no refusal`
# filters. `candidates` also carried absolute host paths where `site` was
# repo-relative, so the --json census was host-dependent.

PLAY_FAMILY_RAISES = '''
import refusal

class Refused(refusal.Refused, Exception):
    pass

def run(x):
    raise Refused("no records")

def quiet(x):
    return x
'''

PLAY_PLAIN_HELPER = '''
def helper(x):
    return x / 0

def quiet(x):
    return x
'''

CALLER_AMBIG_FAMILYLESS = '''
from play import helper

def go(p):
    return helper(p)              # every candidate is quiet: not a site at all
'''

CALLER_AMBIG_QUIET = '''
from play import quiet

def go(p):
    return quiet(p)               # family present, but no candidate raises this fname
'''

CALLER_AMBIG_PARTIAL = '''
from play import run

def go(p):
    return run(p)                 # one candidate raises: still ambiguous-callee
'''

CALLER_SHADOWED_ALIAS = '''
from mod_a import load as l

def go(p):
    l = lambda q: q               # local rebinding: untracked (documented blind spot)
    return l(p)                   # still attributed to mod_a.load
'''


def _collision_tree(tmp_path: pathlib.Path, alpha: str, beta: str,
                    caller_name: str, caller_body: str) -> list[pathlib.Path]:
    """The flat tree plus a two-way same-stem collision (alpha/play.py,
    beta/play.py) and a caller importing the stem: the flat tree's stems are
    unique, so `play` is the only collision the caller can hit."""
    files = _tree(tmp_path)
    for sub, body in (("alpha", alpha), ("beta", beta)):
        d = tmp_path / sub
        d.mkdir()
        p = d / "play.py"
        p.write_text(body)
        files.append(p)
    c = tmp_path / f"{caller_name}.py"
    c.write_text(caller_body)
    files.append(c)
    return files


def _sites_for(scan_files, idx, caller_name: str) -> list[dict]:
    return [s for s in rss.scan(scan_files, idx)
            if s["site"].split("/")[-1].startswith(f"{caller_name}.py:")]


def _tree(tmp_path: pathlib.Path) -> list[pathlib.Path]:
    files = {}
    for name, body in (("mod_a", MOD_A), ("mod_b", MOD_B), ("plain", PLAIN),
                       ("caller_sibling", CALLER_SIBLING_GUARD),
                       ("caller_base", CALLER_BASE_GUARD),
                       ("caller_unguarded", CALLER_UNGUARDED),
                       ("caller_alias", CALLER_ALIAS_EXACT),
                       ("caller_quiet", CALLER_QUIET_FUNCTION),
                       ("caller_localvar", CALLER_LOCAL_VAR_FALSE_POSITIVE),
                       ("caller_broad", CALLER_BROAD_GUARD),
                       ("caller_exact", CALLER_EXACT_MODULE_GUARD),
                       ("caller_cli_base", CALLER_CLI_BASE_GUARD),
                       ("caller_except_clause", CALLER_EXCEPT_CLAUSE),
                       ("caller_else_clause", CALLER_ELSE_CLAUSE),
                       ("caller_finally_clause", CALLER_FINALLY_CLAUSE),
                       ("caller_nested_handler", CALLER_NESTED_IN_HANDLER),
                       ("caller_from_alias", CALLER_FROM_IMPORT_ALIAS),
                       ("caller_from_quiet", CALLER_FROM_IMPORT_QUIET_ALIAS)):
        p = tmp_path / f"{name}.py"
        p.write_text(body)
        files[name] = p
    return list(files.values())


def _stem_tree(tmp_path: pathlib.Path) -> list[pathlib.Path]:
    """The flat tree plus a three-way same-stem collision and a caller that
    imports the colliding stem: audition/play.py vs fpga/play.py in the real
    tree, at three files so both drop directions are exercised."""
    files = _tree(tmp_path)
    for sub, body in (("alpha", PLAY_ALPHA), ("beta", PLAY_BETA),
                      ("gamma", PLAY_GAMMA)):
        d = tmp_path / sub
        d.mkdir()
        p = d / "play.py"
        p.write_text(body)
        files.append(p)
    amb = tmp_path / "caller_stem.py"
    amb.write_text(CALLER_AMBIGUOUS_STEM)
    files.append(amb)
    return files


def _scan(tmp_path):
    files = _tree(tmp_path)
    idx = rss.build_index(files)
    return {(s["site"].split("/")[-1].split(":")[0], s["callee"]): s for s in rss.scan(files, idx)}


def test_sibling_guarded_call_is_a_candidate_escape(tmp_path):
    s = _scan(tmp_path)[("caller_sibling.py", "mod_a.load")]
    assert s["classification"] == "candidate-escape"
    assert s["uncovered"] == ["mod_a"], s


def test_base_guard_is_covered(tmp_path):
    s = _scan(tmp_path)[("caller_base.py", "mod_a.load")]
    assert s["classification"].startswith("covered"), s
    assert "base" in s["guard"], s


def test_unguarded_call_is_a_candidate_escape(tmp_path):
    s = _scan(tmp_path)[("caller_unguarded.py", "mod_a.load")]
    assert s["classification"] == "candidate-escape", s
    assert s["guard"] == "none", s


def test_alias_of_the_callee_class_covers_exactly_that_callee(tmp_path):
    """The measure_partial_balance shape: `Refused = pc.Refused` covers
    run_case's refusals because run_case.Refused IS pc.Refused -- and covers
    nothing else, which is why the sibling module stays a candidate."""
    s = _scan(tmp_path)[("caller_alias.py", "mod_b.measure")]
    assert s["classification"].startswith("covered"), s
    # the same caller calling mod_a under that alias stays uncovered:
    files = [p for p in _tree(tmp_path)]
    (tmp_path / "caller_alias.py").write_text(
        "import mod_a as ma\n" + CALLER_ALIAS_EXACT.replace("mb.measure", "ma.load"))
    idx = rss.build_index(files)
    cross = [s for s in rss.scan(files, idx)
             if s["callee"] == "mod_a.load" and "caller_alias" in s["site"]][0]
    assert cross["classification"] == "candidate-escape", cross


def test_family_module_function_that_raises_nothing_is_not_a_site(tmp_path):
    assert not [k for k in _scan(tmp_path) if k[0] == "caller_quiet.py"]


def test_local_variable_named_like_a_module_is_not_a_site(tmp_path):
    """The #1160 one-off listed corpus_lineage and reference_rigs on exactly
    this false positive; the import-binding requirement removes it."""
    assert not [k for k in _scan(tmp_path) if k[0] == "caller_localvar.py"]


def test_broad_handler_is_reported_not_confused_with_base(tmp_path):
    s = _scan(tmp_path)[("caller_broad.py", "mod_a.load")]
    assert s["classification"].startswith("covered"), s
    assert s["guard"] == "broad", s


def test_exact_module_refused_handler_covers(tmp_path):
    s = _scan(tmp_path)[("caller_exact.py", "mod_a.load")]
    assert s["classification"].startswith("covered"), s
    assert "exact" in s["guard"], s


def test_unguarded_call_under_a_base_cli_dispatch_is_cli_base(tmp_path):
    """The rs_guard_band/rs_mode_drive shape: unguarded at the call site,
    `except refusal.Refused` at the `__main__` dispatch -- the census must
    not list the repaired modules as escapes."""
    s = _scan(tmp_path)[("caller_cli_base.py", "mod_a.load")]
    assert s["classification"] == "covered(cli-base)", s
    assert s["guard"] == "none", s


# --- #1188 controls: the three gaps the blind-spot list was missing ----------
# Each starts red against the unfixed scanner; see the module docstring's
# "three gaps" paragraph in tools/refusal_sibling_scan.py for the shapes.

def test_call_in_a_trys_except_clause_is_not_covered_by_it(tmp_path):
    """Gap "try-handler credit": any enclosing try used to credit the call,
    but a handler covers only its try's BODY -- a refusal raised in the
    except clause of a base-guarded try escapes it."""
    got = _scan(tmp_path)
    s = got[("caller_except_clause.py", "mod_a.load")]
    assert s["classification"] == "candidate-escape", s
    assert s["guard"] == "none", s
    body = got[("caller_except_clause.py", "mod_b.measure")]
    assert body["classification"].startswith("covered"), body  # body still is


def test_call_in_a_trys_else_clause_is_not_covered_by_it(tmp_path):
    s = _scan(tmp_path)[("caller_else_clause.py", "mod_a.load")]
    assert s["classification"] == "candidate-escape", s
    assert s["guard"] == "none", s


def test_call_in_a_trys_finally_clause_is_not_covered_by_it(tmp_path):
    s = _scan(tmp_path)[("caller_finally_clause.py", "mod_a.load")]
    assert s["classification"] == "candidate-escape", s
    assert s["guard"] == "none", s


def test_nested_try_inside_a_handler_guards_only_its_own_body(tmp_path):
    """Inner try (finally-only, no handlers) inside an outer handler: the
    call is in the inner body but the outer try guards only its own body."""
    s = _scan(tmp_path)[("caller_nested_handler.py", "mod_a.load")]
    assert s["classification"] == "candidate-escape", s
    assert s["guard"] == "none", s


def test_each_same_stem_file_is_scanned_with_its_own_tree(tmp_path):
    """Gap "stem collision": the index was keyed by stem with setdefault, so
    of two same-stem files (audition/play.py, fpga/play.py in the real tree)
    every file after the first was silently dropped -- its own sites
    unreported and the winner's sites attributed to it."""
    files = _stem_tree(tmp_path)
    idx = rss.build_index(files)
    got = {(s["site"].split("/")[-1].split(":")[0], s["callee"]): s
           for s in rss.scan(files, idx)}
    beta = got[("play.py", "mod_a.load")]
    assert beta["classification"] == "candidate-escape", beta
    gamma = got[("play.py", "mod_b.measure")]
    assert gamma["classification"] == "candidate-escape", gamma


def test_ambiguous_same_stem_callee_is_named_not_guessed(tmp_path):
    """A `from play import run` cannot be attributed to one of three
    same-stem modules from the AST alone: the site is reported as
    ambiguous-callee with the candidates, never classified by guessing --
    except where a base/broad guard covers every family anyway."""
    files = _stem_tree(tmp_path)
    idx = rss.build_index(files)
    got = {(s["site"].split("/")[-1].split(":")[0], s["callee"]): s
           for s in rss.scan(files, idx)}
    s = got[("caller_stem.py", "play.run")]
    assert s["classification"] == "ambiguous-callee", s
    assert len(s["candidates"]) == 3, s
    assert all(c.endswith("play.py") for c in s["candidates"]), s


def test_aliased_from_import_call_resolves_to_the_original_function(tmp_path):
    """Gap "alias loss": `from x import f as g` recorded only the module, so
    `g()` looked for a def named g in x, found none, and the raising call
    vanished from the census."""
    s = _scan(tmp_path)[("caller_from_alias.py", "mod_a.load")]
    assert s["classification"] == "candidate-escape", s
    assert s["origins"] == ["mod_a"], s
    assert s["uncovered"] == ["mod_a"], s


def test_alias_of_a_non_raising_function_is_not_a_site(tmp_path):
    assert not [k for k in _scan(tmp_path) if k[0] == "caller_from_quiet.py"]


# --- #1193 controls: the borrowed-family blind spot ---------------------------
# Every borrow control except the shadow pin started red against the unfixed
# scanner: a from-imported Refused bound no family name, so the borrower's
# own `raise Refused` read as raising no family and its `except Refused`
# handlers read as shape `other` -- both sides of the census silenced.

BORROWING_CALLEE = '''
from mod_a import Refused as R           # the family, BORROWED (aliased)

def fetch(p):
    raise R(f"cannot use {p}")
'''

BORROW_CALLEE_CALLER = '''
import borrow_callee

def go(p):
    return borrow_callee.fetch(p)        # unguarded: the site the census lost
'''

BORROWING_HANDLER = '''
import mod_a
import mod_b
from mod_a import Refused                # the borrower's guard, unaliased

def go(p):
    try:
        mod_a.load(p)                    # covered: the class IS mod_a's
        mod_b.measure(p)                 # NOT covered: mod_b's is a sibling
    except Refused as e:
        print("REFUSED", e)
'''

BORROWING_HANDLER_ALIAS = '''
import mod_a
import mod_b
from mod_a import Refused as R           # the borrower's guard, aliased

def go(p):
    try:
        mod_a.load(p)
        mod_b.measure(p)
    except R as e:
        print("REFUSED", e)
'''

TWO_STEM_BORROWER = '''
import mod_a
import mod_b
from mod_a import Refused as RA          # two different lenders...
from mod_b import Refused as RB

def go(p):
    try:
        mod_a.load(p)
        mod_b.measure(p)
    except RA as e:                      # ...so each name covers only its own
        print("A", e)
    except RB as e:
        print("B", e)
'''

BORROW_SHADOWED_BY_CLASS = '''
import refusal
import mod_a
from mod_a import Refused                # borrowed first...

class Refused(refusal.Refused, Exception):   # ...then rebound to a local def
    pass

def go(p):
    try:
        mod_a.load(p)
    except Refused as e:                 # the LOCAL sibling: does not cover
        print("REFUSED", e)
'''

CLASS_SHADOWED_BY_BORROW = '''
import refusal
import mod_a

class Refused(refusal.Refused, Exception):   # local first...
    pass

from mod_a import Refused                # ...then rebound to the borrow

def go(p):
    try:
        mod_a.load(p)
    except Refused as e:                 # mod_a's class again: covers it
        print("REFUSED", e)
'''


def _borrow_tree(tmp_path: pathlib.Path) -> list[pathlib.Path]:
    files = _tree(tmp_path)
    for name, body in (("borrow_callee", BORROWING_CALLEE),
                       ("borrow_caller", BORROW_CALLEE_CALLER),
                       ("borrow_handler", BORROWING_HANDLER),
                       ("borrow_handler_alias", BORROWING_HANDLER_ALIAS),
                       ("borrow_two_stems", TWO_STEM_BORROWER),
                       ("borrow_shadowed", BORROW_SHADOWED_BY_CLASS),
                       ("class_shadowed", CLASS_SHADOWED_BY_BORROW)):
        p = tmp_path / f"{name}.py"
        p.write_text(body)
        files.append(p)
    return files


def _borrow_scan(tmp_path):
    files = _borrow_tree(tmp_path)
    idx = rss.build_index(files)
    return {(s["site"].split("/")[-1].split(":")[0], s["callee"]): s
            for s in rss.scan(files, idx)}


def test_raise_of_a_borrowed_family_resolves_to_the_lenders_origin(tmp_path):
    """Gap "borrowed family": `from mod_a import Refused as R` bound no family
    name, so `raise R(...)` in the borrower read as raising nothing and this
    caller's site vanished from the census entirely."""
    s = _borrow_scan(tmp_path)[("borrow_caller.py", "borrow_callee.fetch")]
    assert s["classification"] == "candidate-escape", s
    assert s["origins"] == ["mod_a"], s
    assert s["uncovered"] == ["mod_a"], s


def test_borrowed_handler_covers_exactly_the_lender_and_no_sibling(tmp_path):
    """The res_tuning shape: `except Refused` written with a borrowed Refused
    covers the lender's origin (shape `exact` -- the class IS the lender's)
    and nothing else, which is why the sibling module stays a candidate."""
    got = _borrow_scan(tmp_path)
    s = got[("borrow_handler.py", "mod_a.load")]
    assert s["classification"].startswith("covered"), s
    assert s["guard"] == "exact", s
    sib = got[("borrow_handler.py", "mod_b.measure")]
    assert sib["classification"] == "candidate-escape", sib
    assert sib["uncovered"] == ["mod_b"], sib


def test_aliased_borrowed_handler_covers_exactly_the_lender(tmp_path):
    got = _borrow_scan(tmp_path)
    s = got[("borrow_handler_alias.py", "mod_a.load")]
    assert s["classification"].startswith("covered"), s
    assert s["guard"] == "exact", s
    sib = got[("borrow_handler_alias.py", "mod_b.measure")]
    assert sib["classification"] == "candidate-escape", sib


def test_borrow_from_two_stems_covers_each_name_to_its_own_lender(tmp_path):
    """`Refused` borrowed under two names from two lenders: each handler
    covers only its own lender's class, not the other's."""
    got = _borrow_scan(tmp_path)
    a = got[("borrow_two_stems.py", "mod_a.load")]
    assert a["classification"].startswith("covered"), a
    b = got[("borrow_two_stems.py", "mod_b.measure")]
    assert b["classification"].startswith("covered"), b


def test_borrow_shadowed_by_a_local_class_stays_an_escape(tmp_path):
    """Python's last binding wins: a `class Refused` def AFTER the borrow
    rebinds the name to a local sibling, so the handler no longer covers the
    lender. A control, not a red-first case -- it pins the fix against
    crediting every binding the module ever made."""
    s = _borrow_scan(tmp_path)[("borrow_shadowed.py", "mod_a.load")]
    assert s["classification"] == "candidate-escape", s
    assert s["uncovered"] == ["mod_a"], s
    assert s["guard"] == "local", s


def test_local_class_shadowed_by_a_borrow_covers_the_lender(tmp_path):
    """The reverse shadow: the borrow comes after the class def, so the name
    is the lender's class again and the handler covers it (shape `exact`,
    not `local`)."""
    s = _borrow_scan(tmp_path)[("class_shadowed.py", "mod_a.load")]
    assert s["classification"].startswith("covered"), s
    assert s["guard"] == "exact", s


# --- #1214 controls: the ambiguous-callee filter (items 1), repo-relative
# candidates (item 2), and the shadowed-alias blind-spot pin (item 4). The
# filter and format controls started red against the unfixed scanner.

REEXPORTER = '''
from mod_a import Refused as Refused     # PEP 484 explicit re-export

def fetch(p):
    raise Refused(f"reexported: {p}")
'''

REEXPORT_CALLER = '''
import reexporter

def go(p):
    try:
        reexporter.fetch(p)
    except reexporter.Refused as e:      # the lender's class, reached via the re-export
        print("REFUSED", e)
'''


def test_same_name_reexport_keeps_the_lenders_origin(tmp_path):
    """Rule-8 control for the `nm == FAMILY_NAME` guard in `_read_bindings`
    (#1267). `from mod_a import Refused as Refused` binds `reexporter.Refused`
    to mod_a's class, so a dotted `except reexporter.Refused` covers mod_a
    exactly. Red under `if nm == FAMILY_NAME and a.asname is None:` (origin
    becomes None, the site turns into a false candidate-escape); green as
    shipped. The inverted guard is already caught by the borrow tests above.

    Rule 8, other direction (stated reason, not a control): no realistic input
    satisfies the guard while violating its intent. `_read_bindings` walks only
    `tree.body`, so a conditional/guarded import is never seen and leaves the
    origin None -- the pessimistic direction. Only a `del Refused` after the
    import would defeat it, which is contrived and out of scope."""
    files = _tree(tmp_path)
    for name, body in (("reexporter", REEXPORTER), ("reexport_caller", REEXPORT_CALLER)):
        f = tmp_path / f"{name}.py"
        f.write_text(body)
        files.append(f)
    idx = rss.build_index(files)
    reexp = next(m for k, m in idx.items() if k.endswith("reexporter.py"))
    assert reexp.refused_origin == "mod_a"
    sites = _sites_for(files, idx, "reexport_caller")
    assert len(sites) == 1, sites
    s = sites[0]
    assert s["callee"] == "reexporter.fetch", s
    assert s["classification"] == "covered(exact)", s
    assert s["origins"] == ["mod_a"], s
    assert s["uncovered"] == [], s


def test_family_less_stem_collision_is_not_a_site(tmp_path):
    """Item 1: two family-less play.py candidates and a `from play import
    helper` caller used to be reported as ambiguous-callee -- "I cannot tell
    which refusal this is" about a call that raises no refusal. The
    single-callee path already filtered this shape; the multi-candidate
    branch now mirrors it."""
    files = _collision_tree(tmp_path, PLAY_PLAIN_HELPER, PLAY_PLAIN_HELPER,
                            "caller_familyless", CALLER_AMBIG_FAMILYLESS)
    idx = rss.build_index(files)
    assert _sites_for(files, idx, "caller_familyless") == []


def test_collision_where_no_candidate_raises_for_the_callee_is_not_a_site(tmp_path):
    """Item 1, quiet-fname direction: one candidate module HAS a family but
    its `quiet` raises nothing, the other has none -- the call cannot raise a
    refusal under any resolution, so it is not a site (same contract as the
    single-callee `caller_quiet` pin)."""
    files = _collision_tree(tmp_path, PLAY_FAMILY_RAISES, PLAY_PLAIN_HELPER,
                            "caller_quiet_stem", CALLER_AMBIG_QUIET)
    idx = rss.build_index(files)
    assert _sites_for(files, idx, "caller_quiet_stem") == []


def test_partially_raising_collision_is_still_ambiguous_callee(tmp_path):
    """The filter must not over-filter: one raising candidate keeps the site,
    with every candidate listed (a `any`, never `all`, filter)."""
    files = _collision_tree(tmp_path, PLAY_FAMILY_RAISES, PLAY_PLAIN_HELPER,
                            "caller_partial", CALLER_AMBIG_PARTIAL)
    idx = rss.build_index(files)
    got = _sites_for(files, idx, "caller_partial")
    assert len(got) == 1, got
    s = got[0]
    assert s["classification"] == "ambiguous-callee", s
    assert len(s["candidates"]) == 2, s


def test_candidates_are_repo_relative_when_under_root(tmp_path, monkeypatch):
    """Item 2: `candidates` used str(c.path) -- absolute, host-dependent --
    while `site` was ROOT-relative; under a monkeypatched ROOT the json-facing
    field must carry repo-relative paths, so the census is reproducible
    across hosts (#1183 consumes --json)."""
    monkeypatch.setattr(rss, "ROOT", tmp_path)
    files = _collision_tree(tmp_path, PLAY_FAMILY_RAISES, PLAY_PLAIN_HELPER,
                            "caller_partial", CALLER_AMBIG_PARTIAL)
    idx = rss.build_index(files)
    s = _sites_for(files, idx, "caller_partial")[0]
    assert s["candidates"] == ["alpha/play.py", "beta/play.py"], s


def test_candidates_outside_root_fall_back_to_the_absolute_path(tmp_path):
    """Synthetic test trees live outside ROOT: the ValueError fallback keeps
    them scannable, exactly as `site` already does."""
    files = _collision_tree(tmp_path, PLAY_FAMILY_RAISES, PLAY_PLAIN_HELPER,
                            "caller_partial", CALLER_AMBIG_PARTIAL)
    idx = rss.build_index(files)
    s = _sites_for(files, idx, "caller_partial")[0]
    assert all(c.startswith(str(tmp_path)) for c in s["candidates"]), s


def test_shadowed_alias_is_attributed_to_the_imported_def(tmp_path):
    """Item 4, #1188's fourth edge case: `from mod_a import load as l` with a
    LOCAL `l = ...` rebinding before the call. Bindings are module-level, so
    the call is still attributed to mod_a.load -- the documented blind spot
    (`a local name shadowing an import can misattribute`). Pinned so a future
    scope-tracking change moves this test deliberately, not a user's census."""
    files = _tree(tmp_path)
    p = tmp_path / "caller_shadow.py"
    p.write_text(CALLER_SHADOWED_ALIAS)
    files.append(p)
    idx = rss.build_index(files)
    got = _sites_for(files, idx, "caller_shadow")
    assert len(got) == 1, got
    s = got[0]
    assert s["callee"] == "mod_a.load", s
    assert s["classification"] == "candidate-escape", s
    assert s["uncovered"] == ["mod_a"], s


# --- #1234 controls: an ALIASED borrow exposes no `<mod>.Refused` ------------
# `from lender import Refused as R` binds only `R`: a dotted
# `except borrower.Refused` handler is an AttributeError, so it must not be
# credited as covering the lender. Each control started red against the
# scanner that set `refused_origin = stem` for any borrow of the family name.

ALIAS_ONLY_BORROWER = '''
from mod_a import Refused as R           # ALIASED only: no borrower.Refused

def fetch(p):
    raise R(f"cannot use {p}")
'''

ALIAS_DOTTED_CALLER = '''
import alias_only

def go(p):
    try:
        alias_only.fetch(p)
    except alias_only.Refused as e:      # AttributeError at runtime
        print(e)
'''

ALIAS_LENDER_DOTTED_CALLER = '''
import mod_a
import alias_only

def go(p):
    try:
        alias_only.fetch(p)
    except mod_a.Refused as e:           # the real lender class: covers
        print(e)
'''

LOCAL_THEN_ALIAS = '''
import refusal

class Refused(refusal.Refused, Exception):   # local family first...
    pass

from mod_a import Refused as R           # ...then an aliased borrow (valid)

def own(p):
    raise Refused("local")

def lent(p):
    raise R("lender's")
'''

LOCAL_THEN_ALIAS_CALLER = '''
import local_then_alias

def go_own(p):
    try:
        local_then_alias.own(p)
    except local_then_alias.Refused as e:    # exists at runtime, catches own
        print(e)

def go_lent(p):
    try:
        local_then_alias.lent(p)
    except local_then_alias.Refused as e:    # local class: misses lender's
        print(e)
'''


def _alias_tree(tmp_path, extra: dict[str, str]):
    files = _tree(tmp_path)
    for name, body in extra.items():
        p = tmp_path / f"{name}.py"
        p.write_text(body)
        files.append(p)
    return files, rss.build_index(files)


def test_aliased_only_borrow_does_not_expose_the_family_attribute(tmp_path):
    files, idx = _alias_tree(tmp_path, {"alias_only": ALIAS_ONLY_BORROWER,
                                        "alias_dotted_caller": ALIAS_DOTTED_CALLER})
    info = idx[str(tmp_path / "alias_only.py")]
    assert info.refused_origin is None, info.refused_origin
    assert info.refused_names == {"R": "mod_a"}, info.refused_names
    assert rss.origin_of(rss.stem_map(idx), "alias_only") is None
    got = _sites_for(files, idx, "alias_dotted_caller")
    assert len(got) == 1, got
    assert got[0]["classification"] == "candidate-escape", got[0]
    assert got[0]["uncovered"] == ["mod_a"], got[0]


def test_aliased_borrower_own_raise_still_resolves_to_the_lender(tmp_path):
    files, idx = _alias_tree(tmp_path, {"alias_only": ALIAS_ONLY_BORROWER,
                                        "alias_lender_caller": ALIAS_LENDER_DOTTED_CALLER})
    got = _sites_for(files, idx, "alias_lender_caller")
    assert len(got) == 1, got
    assert got[0]["classification"] == "covered(exact)", got[0]
    assert got[0]["guard"] == "exact", got[0]


def test_aliased_handler_still_covers_exactly_the_lender(tmp_path):
    """`except R` (aliased) keeps covering mod_a and only mod_a (#1193)."""
    files = _tree(tmp_path)
    p = tmp_path / "alias_handler.py"
    p.write_text(BORROWING_HANDLER_ALIAS)
    files.append(p)
    idx = rss.build_index(files)
    by_callee = {s["callee"]: s for s in _sites_for(files, idx, "alias_handler")}
    assert by_callee["mod_a.load"]["guard"] == "exact", by_callee
    assert by_callee["mod_a.load"]["classification"] == "covered(exact)", by_callee
    assert by_callee["mod_b.measure"]["classification"] == "candidate-escape", by_callee


def test_local_family_then_aliased_borrow_keeps_the_local_origin(tmp_path):
    files, idx = _alias_tree(tmp_path, {"local_then_alias": LOCAL_THEN_ALIAS,
                                        "lta_caller": LOCAL_THEN_ALIAS_CALLER})
    info = idx[str(tmp_path / "local_then_alias.py")]
    assert info.refused_origin == "local_then_alias", info.refused_origin
    by_callee = {s["callee"]: s for s in _sites_for(files, idx, "lta_caller")}
    own = by_callee["local_then_alias.own"]
    lent = by_callee["local_then_alias.lent"]
    assert own["classification"] == "covered(exact)" and own["guard"] == "exact", own
    assert lent["classification"] == "candidate-escape", lent
    assert lent["uncovered"] == ["mod_a"], lent

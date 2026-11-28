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
blind spot #1188's test plan named but never pinned. The #1573 section pins
function-local imports: an `import x as y` written inside a function bound
nothing, so its call site was ABSENT from the census (#1569's defect shape),
and the scoping that keeps a function's import out of its siblings. The #1577
section pins the blind spots that issue listed: a family class borrowed inside
a function (raise, handler, the `scan` pre-filter, and the shadow that keeps it
out of the rest of the module), the two roots outside `SCAN_ROOTS`, source
order inside a function scope, and the one site the new roots add to the
census, accepted with the runtime evidence for its acceptance. The #1702
section plants each scope boundary a try can be credited across, RUNS the
planted caller against a refusing callee, and holds the census row to the
interpreter's verdict -- a rule about when Python runs a call is answered
by Python, not by the rule's own docstring.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import pathlib
import sys
import textwrap
import types

import pytest

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


# --- #1573 controls: function-local imports ------------------------------------
# `fpga/release/r2_release.py`'s `stale_control` imports `release_manifest as
# rm` inside an `elif` branch and calls `rm._capture` under a `with` there,
# inside a try whose handler is `except Refused` -- an alias of
# r1_release.Refused. `_read_bindings` read `tree.body` only, so `rm` was
# never bound and the site was ABSENT from the census, not misclassified: the
# census could not flag the defect #1569 repairs. Every scope control below
# started red against the module-level-only scanner except two pins: the
# module-level-bindings control, and the function-local family borrow, which
# #1577 flipped into a positive control.

LOCAL_SIBLING_CALLER = '''
import mod_b as mb

Refused = mb.Refused

def go(case):
    try:
        if case == "image":
            touch(case)
        elif case == "host":
            import mod_a as rm
            with open(case) as d:
                cap = rm.load(d)
    except Refused as exc:
        return f"REFUSED {exc}"
'''

LOCAL_FROM_IMPORT_CALLER = '''
def go(p):
    from mod_a import load as l
    return l(p)
'''

LOCAL_DOTTED_IMPORT_CALLER = '''
def go(p):
    import mod_a.pkg as ma
    return ma.load(p)
'''

LOCAL_NESTED_FUNCTION_CALLER = '''
def go(p):
    def inner(q):
        import mod_a as rm
        return rm.load(q)
    return inner(p)
'''

LOCAL_CLOSURE_CALLER = '''
def go(p):
    import mod_a as rm
    def inner(q):
        return rm.load(q)
    return inner(p)
'''

LOCAL_SHADOW_CALLER = '''
import mod_a as rm

def first(p):
    import mod_b as rm
    return rm.measure(p)

def second(p):
    return rm.load(p)
'''

LOCAL_NESTED_SHADOW_CALLER = '''
def go(p):
    import mod_b as rm
    def inner(q):
        import mod_a as rm
        return rm.load(q)
    return rm.measure(p), inner(p)
'''

LOCAL_AND_MODULE_CALLER = '''
import mod_a
import mod_b as mb

def go(p):
    import mod_a as ma
    return mod_a.load(p), ma.load(p), mb.measure(p)
'''

LOCAL_HANDLER_CALLER = '''
import mod_a

def go(p):
    import mod_a as rm
    try:
        return mod_a.load(p)
    except rm.Refused as e:
        print(e)
'''

LOCAL_RAISER = '''
import refusal

class Refused(refusal.Refused, Exception):
    pass

def fetch(p):
    import mod_b as mb
    raise mb.Refused(f"cannot use {p}")
'''

LOCAL_RAISER_CALLER = '''
import local_raiser
import mod_b

def go(p):
    try:
        local_raiser.fetch(p)
    except mod_b.Refused as e:
        print(e)
'''

LOCAL_FAMILY_BORROWER = '''
import refusal

class Refused(refusal.Refused, Exception):
    pass

def own(p):
    raise Refused("the module's own class")

def fetch(p):
    from mod_b import Refused as R
    raise R(f"cannot use {p}")
'''

LOCAL_BORROW_CALLER = '''
import local_borrower

def go(p):
    local_borrower.own(p)
    return local_borrower.fetch(p)
'''


def _local_tree(tmp_path, extra: dict[str, str]):
    files = _tree(tmp_path)
    for name, body in extra.items():
        p = tmp_path / f"{name}.py"
        p.write_text(body)
        files.append(p)
    return files, rss.build_index(files)


def _call_in(mod, head: str, attr: str) -> ast.Call:
    return next(n for n in ast.walk(mod.tree)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and isinstance(n.func.value, ast.Name)
                and n.func.value.id == head and n.func.attr == attr)


def test_function_local_import_under_an_own_class_handler_is_an_escape(tmp_path):
    """The #1569 shape, red first: `import mod_a as rm` sits in an `elif`
    branch, the `rm.load` call in a `with` under it, and the handler is
    `except Refused` with `Refused` an alias of a SIBLING's class. Against
    the module-level-only scanner the site list is empty -- the site was
    absent, so the census could not flag the defect."""
    files, idx = _local_tree(tmp_path, {"caller_local": LOCAL_SIBLING_CALLER})
    got = _sites_for(files, idx, "caller_local")
    assert len(got) == 1, got
    s = got[0]
    assert s["callee"] == "mod_a.load", s
    assert s["site"].endswith("caller_local.py:13"), s
    assert s["classification"] == "candidate-escape", s
    assert s["uncovered"] == ["mod_a"], s
    assert s["guard"] == "exact", s


def test_function_local_from_import_alias_resolves_to_the_original_function(tmp_path):
    """`from x import y as z` inside a function: the `l()` call resolves to
    mod_a.load, exactly as the module-level alias does (#1188 "alias loss")."""
    files, idx = _local_tree(tmp_path, {"caller_local_from": LOCAL_FROM_IMPORT_CALLER})
    got = _sites_for(files, idx, "caller_local_from")
    assert len(got) == 1, got
    assert got[0]["callee"] == "mod_a.load", got[0]
    assert got[0]["classification"] == "candidate-escape", got[0]


def test_function_local_dotted_import_binds_the_stem(tmp_path):
    """`import a.b as c` inside a function: `c` binds the STEM, as at module
    level, so `ma.load` resolves to mod_a.load."""
    files, idx = _local_tree(tmp_path, {"caller_local_dotted": LOCAL_DOTTED_IMPORT_CALLER})
    got = _sites_for(files, idx, "caller_local_dotted")
    assert len(got) == 1, got
    assert got[0]["callee"] == "mod_a.load", got[0]


def test_local_import_in_a_nested_function_resolves_there(tmp_path):
    files, idx = _local_tree(tmp_path, {"caller_local_nested": LOCAL_NESTED_FUNCTION_CALLER})
    got = _sites_for(files, idx, "caller_local_nested")
    assert len(got) == 1, got
    assert got[0]["callee"] == "mod_a.load", got[0]


def test_nested_function_reads_its_enclosing_defs_import(tmp_path):
    """Lexical scoping, the inward direction: a nested def reads the import
    of the def it sits in (a closure), so the call is not lost."""
    files, idx = _local_tree(tmp_path, {"caller_local_closure": LOCAL_CLOSURE_CALLER})
    got = _sites_for(files, idx, "caller_local_closure")
    assert len(got) == 1, got
    assert got[0]["callee"] == "mod_a.load", got[0]


def test_function_local_import_does_not_leak_across_functions(tmp_path):
    """Rule 8's guard-defeating input for the scope walk. `rm` is bound at
    module level to mod_a and, in `first()` only, to mod_b. Hoisting the
    local import into the module's bindings (last binding wins) rebinds `rm`
    for the WHOLE file: `second()`'s call would read as mod_b.load -- a def
    that does not exist -- and the real mod_a.load site would vanish. The
    control asserts both directions: each function sees its own `rm`."""
    files, idx = _local_tree(tmp_path, {"caller_local_shadow": LOCAL_SHADOW_CALLER})
    got = {s["callee"]: s for s in _sites_for(files, idx, "caller_local_shadow")}
    assert sorted(got) == ["mod_a.load", "mod_b.measure"], got
    assert got["mod_a.load"]["classification"] == "candidate-escape", got
    assert got["mod_a.load"]["uncovered"] == ["mod_a"], got
    assert got["mod_b.measure"]["uncovered"] == ["mod_b"], got


def test_nested_functions_import_shadows_its_enclosing_defs(tmp_path):
    """Innermost wins, the inverse of the closure case: the nested def's own
    `rm` must govern the call inside it, and the enclosing def's `rm` must
    survive for its own call. A walk that credits the inner import to the
    OUTER def (last binding wins there) rebinds the outer `rm` as well and
    the mod_b.measure site vanishes. The real tree has this shape:
    r1_release.py binds `rc` to record_compare at module level and to
    r1_candidate inside three functions."""
    files, idx = _local_tree(tmp_path, {"caller_local_nshadow": LOCAL_NESTED_SHADOW_CALLER})
    got = {s["callee"]: s for s in _sites_for(files, idx, "caller_local_nshadow")}
    assert sorted(got) == ["mod_a.load", "mod_b.measure"], got


def test_module_level_bindings_are_unchanged_by_function_local_imports(tmp_path):
    """A control, not a red-first case: the module's own `imports` hold what
    they held before function scopes existed -- the function-local names are
    not in them -- and `resolve_head` without a use site answers from module
    level only, so every module-level caller of the API is unchanged."""
    files, idx = _local_tree(tmp_path, {"caller_local_shadow": LOCAL_SHADOW_CALLER,
                                        "caller_local_both": LOCAL_AND_MODULE_CALLER})
    shadow = idx[str(tmp_path / "caller_local_shadow.py")]
    assert shadow.imports == {"rm": "mod_a"}, shadow.imports
    assert shadow.from_imports == {}, shadow.from_imports
    assert shadow.resolve_head("rm") == "mod_a"
    assert shadow.resolve_head("rm", _call_in(shadow, "rm", "measure")) == "mod_b"
    both = idx[str(tmp_path / "caller_local_both.py")]
    assert both.imports == {"mod_a": "mod_a", "mb": "mod_b"}, both.imports
    assert both.resolve_head("ma") is None
    assert both.resolve_head("ma", _call_in(both, "ma", "load")) == "mod_a"
    got = sorted(s["callee"] for s in _sites_for(files, idx, "caller_local_both"))
    assert got == ["mod_a.load", "mod_a.load", "mod_b.measure"], got


def test_function_local_alias_in_a_handler_covers_its_module(tmp_path):
    """The handler side of the same mechanism, red first: `except rm.Refused`
    with `rm` imported inside the function used to credit an origin spelled
    `rm` -- a stem no module carries -- so a handler that covers the callee
    exactly read as a candidate escape."""
    files, idx = _local_tree(tmp_path, {"caller_local_handler": LOCAL_HANDLER_CALLER})
    got = _sites_for(files, idx, "caller_local_handler")
    assert len(got) == 1, got
    assert got[0]["classification"] == "covered(exact)", got[0]
    assert got[0]["guard"] == "exact", got[0]
    assert got[0]["origins"] == ["mod_a"], got[0]


def test_raise_through_a_function_local_import_names_the_real_module(tmp_path):
    """The raise side, red first: `raise mb.Refused(...)` with `mb` imported
    inside the callee's def read as origin `mb`; a caller's exact
    `except mod_b.Refused` could never cover a stem that is not a module."""
    files, idx = _local_tree(tmp_path, {"local_raiser": LOCAL_RAISER,
                                        "caller_local_raiser": LOCAL_RAISER_CALLER})
    got = _sites_for(files, idx, "caller_local_raiser")
    assert len(got) == 1, got
    assert got[0]["origins"] == ["mod_b"], got[0]
    assert got[0]["classification"] == "covered(exact)", got[0]
    assert got[0]["uncovered"] == [], got[0]


def test_family_borrowed_inside_a_function_resolves_to_the_lender(tmp_path):
    """The #1573 blind-spot pin, flipped by #1577. `from mod_b import Refused
    as R` inside a def used to bind no family name, so `raise R(...)` read as
    raising nothing and this caller's `fetch` site was ABSENT from the census
    while `own` was listed -- the same silencing #1193's module-level borrow
    had. The sibling call to `own` is still asserted, now for the other
    direction: the borrow must not rebind the module's own class."""
    files, idx = _local_tree(tmp_path, {"local_borrower": LOCAL_FAMILY_BORROWER,
                                        "caller_local_borrow": LOCAL_BORROW_CALLER})
    got = {s["callee"]: s for s in _sites_for(files, idx, "caller_local_borrow")}
    assert sorted(got) == ["local_borrower.fetch", "local_borrower.own"], got
    assert got["local_borrower.own"]["origins"] == ["local_borrower"], got
    fetch = got["local_borrower.fetch"]
    assert fetch["origins"] == ["mod_b"], fetch
    assert fetch["classification"] == "candidate-escape", fetch
    assert fetch["uncovered"] == ["mod_b"], fetch


# --- #1577 controls: the remaining import-scope blind spots --------------------
# Three blind spots the scanner's docstring had stated as accepted. Every
# scanner control below started red against the #1573 scanner, each on the
# assertion its docstring names; the two drum-solo controls at the end do not
# involve the scanner at all.
#
# (1) A family class borrowed by a FUNCTION-LOCAL from-import bound no family
#     name: `from mod_b import Refused as R` inside a def made `raise R(...)`
#     read as raising nothing, `except R` read as shape `other`, and -- when
#     that was the module's only family binding -- `scan`'s "no family at all"
#     pre-filter dropped the module's sites before `raise_origins` could read
#     them. Fixing the raise alone leaves that pre-filter skipping the module,
#     so it has its own control, in both the single-callee branch and the
#     same-stem-collision branch, which carries the same test.

LOCAL_ONLY_BORROWER = '''
def fetch(p):
    from mod_b import Refused as R           # the module's ONLY family binding
    raise R(f"cannot use {p}")
'''

LOCAL_ONLY_CALLER = '''
import local_only

def go(p):
    return local_only.fetch(p)               # unguarded
'''

LOCAL_BORROWING_HANDLER = '''
import mod_a
import mod_b

def go(p):
    from mod_b import Refused as R           # the borrower's guard, function-local
    try:
        mod_a.load(p)                        # NOT covered: mod_a's is a sibling
        mod_b.measure(p)                     # covered: the class IS mod_b's
    except R as e:
        print("REFUSED", e)
'''

LOCAL_UNALIASED_BORROWER = '''
import refusal
import mod_a

class Refused(refusal.Refused, Exception):   # the module's own family
    pass

def own(p):
    raise Refused("the module's own class")

def fetch(p):
    from mod_a import Refused                 # the SAME name, the lender's class, here only
    raise Refused(f"cannot use {p}")

def guarded(p):
    from mod_a import Refused                 # the lender's class, here only
    try:
        mod_a.load(p)                         # covered: exact, not local
    except Refused as e:
        print("REFUSED", e)

def sibling_guarded(p):                       # no borrow here: the name is the
    try:                                      # module's own class again
        mod_a.load(p)                         # a sibling: NOT covered
    except Refused as e:
        print("REFUSED", e)
'''

LOCAL_UNALIASED_CALLER = '''
import local_unaliased

def go(p):
    local_unaliased.own(p)
    local_unaliased.fetch(p)
    local_unaliased.guarded(p)
    local_unaliased.sibling_guarded(p)
'''

CALLER_AMBIG_LOCAL_FAMILY = '''
from play import fetch

def go(p):
    return fetch(p)                 # one candidate borrows a family in a def only
'''


def test_callee_whose_only_family_is_function_local_is_not_skipped(tmp_path):
    """The pre-filter, red first: `scan` dropped a callee with no module-level
    family name before reading its raises, so this site was absent even with a
    correct `raise_origins`. The module-level assertions keep the control
    honest about which fix passes it: the name is read from the function
    scope, not hoisted into the module's own family names."""
    files, idx = _local_tree(tmp_path, {"local_only": LOCAL_ONLY_BORROWER,
                                        "caller_local_only": LOCAL_ONLY_CALLER})
    info = idx[str(tmp_path / "local_only.py")]
    assert not info.refused_names and info.refused_origin is None, vars(info)
    got = _sites_for(files, idx, "caller_local_only")
    assert len(got) == 1, got
    assert got[0]["callee"] == "local_only.fetch", got[0]
    assert got[0]["origins"] == ["mod_b"], got[0]
    assert got[0]["classification"] == "candidate-escape", got[0]


def test_function_local_borrowed_handler_covers_exactly_the_lender(tmp_path):
    """The handler side, red first: `except R` with `R` borrowed inside the
    function read as shape `other`, so a guard that covers the callee exactly
    was reported as a candidate escape."""
    files, idx = _local_tree(tmp_path, {"caller_local_borrow_handler": LOCAL_BORROWING_HANDLER})
    got = {s["callee"]: s for s in _sites_for(files, idx, "caller_local_borrow_handler")}
    assert got["mod_b.measure"]["classification"] == "covered(exact)", got
    assert got["mod_b.measure"]["guard"] == "exact", got
    sib = got["mod_a.load"]
    assert sib["classification"] == "candidate-escape", sib
    assert sib["uncovered"] == ["mod_a"], sib


def test_function_local_borrow_shadows_the_module_family_inside_its_def_only(tmp_path):
    """Rule 8's guard-defeating input for the fix, red first on both sides. The
    module defines its own `Refused` and one def borrows another module's class
    under the SAME name: inside that def the name is the lender's, everywhere
    else it is the module's own. Hoisting the function-local borrow into the
    module's family names (last binding wins) rebinds `Refused` for the WHOLE
    file -- `own` would read as raising mod_a's class and `sibling_guarded` as
    covering it. Both directions are asserted, and so is the shape: the borrow
    is `exact` in its def and `local` in the sibling's. The sibling's two
    `mod_a.load` calls are read as the borrower's own sites, at lines 18 and
    24 of the fixture."""
    files, idx = _local_tree(tmp_path, {"local_unaliased": LOCAL_UNALIASED_BORROWER,
                                        "caller_local_unaliased": LOCAL_UNALIASED_CALLER})
    info = idx[str(tmp_path / "local_unaliased.py")]
    assert info.has_local_family and info.refused_origin == "local_unaliased", vars(info)
    assert info.refused_names == {"Refused": "local_unaliased"}, info.refused_names
    got = {s["callee"]: s for s in _sites_for(files, idx, "caller_local_unaliased")}
    assert got["local_unaliased.own"]["origins"] == ["local_unaliased"], got
    assert got["local_unaliased.fetch"]["origins"] == ["mod_a"], got
    internal = {s["site"].rsplit(":", 1)[1]: s
                for s in _sites_for(files, idx, "local_unaliased")}
    assert sorted(internal) == ["18", "24"], internal
    assert internal["18"]["classification"] == "covered(exact)", internal
    assert internal["18"]["guard"] == "exact", internal
    sib = internal["24"]
    assert sib["classification"] == "candidate-escape", sib
    assert sib["guard"] == "local", sib
    assert sib["uncovered"] == ["mod_a"], sib


def test_collision_where_the_raising_candidates_family_is_function_local_is_a_site(tmp_path):
    """The same pre-filter in the same-stem-collision branch, which tests each
    candidate for a family before reading its raises: when the one raising
    candidate's only family binding is a function-local borrow, a
    `from play import fetch` caller was dropped as unable to refuse under any
    resolution."""
    files = _collision_tree(tmp_path, LOCAL_ONLY_BORROWER, PLAIN,
                            "caller_local_amb", CALLER_AMBIG_LOCAL_FAMILY)
    idx = rss.build_index(files)
    got = _sites_for(files, idx, "caller_local_amb")
    assert len(got) == 1, got
    assert got[0]["classification"] == "ambiguous-callee", got[0]
    assert len(got[0]["candidates"]) == 2, got[0]


# (2) `rtl-sketch/` and `audition/` were outside `SCAN_ROOTS`, so the one
#     cross-module refusal site between them was in no census.

ROOT_PROBE = '''
import refusal
import mod_a

class Refused(refusal.Refused, Exception):   # the caller's OWN class: a sibling
    pass

def go(p):
    try:
        mod_a.load(p)               # guarded by a SIBLING: the defect shape
    except Refused as e:
        print("REFUSED", e)
'''

# one fixture per root, written out: the control must name the roots it
# demands, not read them back from the tuple it is checking
SCAN_ROOT_FIXTURES = ("tools", "model", "pnr", "fpga", "rtl-sketch", "audition")


def test_every_scan_root_reaches_the_census_through_main(tmp_path, monkeypatch, capsys):
    """The root filter lives in `main()`, not in `scan()`, so the control
    drives `main()`: one caller under EVERY root, all planting the defect
    shape the census exists for -- a call to another module's raising code
    under `except Refused` with `Refused` the caller's own class. Dropping a
    root from `SCAN_ROOTS` (the #1573 state for `rtl-sketch` and
    `audition`) removes its fixture from the census; the caller under no root
    (`scratch/`) must stay out, so the control also pins that the roots are
    still a filter. A root added without a fixture here fails the tuple
    assertion, so the demand travels with the tuple."""
    monkeypatch.setattr(rss, "ROOT", tmp_path)
    files = [tmp_path / "mod_a.py"]
    files[0].write_text(MOD_A)
    for root in (*SCAN_ROOT_FIXTURES, "scratch"):
        d = tmp_path / root
        d.mkdir()
        p = d / "probe.py"
        p.write_text(ROOT_PROBE)
        files.append(p)
    monkeypatch.setattr(rss, "tracked_files", lambda: files)
    assert rss.main(["--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert list(rss.SCAN_ROOTS) == list(SCAN_ROOT_FIXTURES), rss.SCAN_ROOTS
    assert out["files_scanned"] == len(SCAN_ROOT_FIXTURES), out["files_scanned"]
    assert all(s["classification"] == "candidate-escape" and s["guard"] == "local"
               and s["uncovered"] == ["mod_a"] for s in out["sites"]), out["sites"]
    got = sorted(s["site"] for s in out["sites"])
    assert got == sorted(f"{root}/probe.py:10" for root in SCAN_ROOT_FIXTURES), got


# (3) Inside a function scope the binding that won was `ast.walk`'s breadth-
#     first order, not source order (PR #1579's review): the scope pass was one
#     walk over the whole tree, so a deeper, earlier import was bound AFTER a
#     shallower, later one and won -- the opposite of the module-level rule and
#     of what runs. Only case B was wrong; A and C are carried so the control
#     cannot pass by agreeing with walk order.

ORDER_SHALLOW_THEN_DEEP = '''
def f(c):
    import a as x                 # shallow, FIRST in source
    if c:
        import b as x             # deep, LAST in source: wins
    return x.go()
'''

ORDER_DEEP_THEN_SHALLOW = '''
def f(c):
    if c:
        import a as x             # deep, FIRST in source
    import b as x                 # shallow, LAST in source: wins
    return x.go()
'''

ORDER_SAME_DEPTH = '''
def f(c):
    import a as x
    import b as x                 # same depth, LAST in source: wins
    return x.go()
'''


@pytest.mark.parametrize("label,body", [
    ("A: shallow then deep", ORDER_SHALLOW_THEN_DEEP),
    ("B: deep then shallow", ORDER_DEEP_THEN_SHALLOW),
    ("C: same depth", ORDER_SAME_DEPTH),
])
def test_function_scope_bindings_follow_source_order_not_walk_order(tmp_path, label, body):
    """The last import in SOURCE order wins in a function scope, exactly as at
    module level. Case B was the divergence: the walk bound the shallow `b`
    first and the deep `a` last, so `x` read as `a`."""
    p = tmp_path / "order.py"
    p.write_text(body)
    mod = rss.ModInfo(p)
    assert mod.resolve_head("x", _call_in(mod, "x", "go")) == "b", label


# The one site the two new roots add to the census, and its disposition.
# `rtl-sketch/verify_synth_top.py`'s `drum_solo_report` imports `run_case as rc`
# inside the def, calls `rc.render_drum_solo` inside a nested helper `_render`,
# and guards the two `_render()` calls with `except rc.Refused`. The call is
# lexically outside that try (it sits in the helper's def), so the census
# reports it `guard=none, candidate-escape`: a guard in the def that CALLS a
# local helper is not credited. The site is ACCEPTED, not repaired -- a second
# handler at the call would only restate the one that already catches it. The
# controls below are the evidence for that acceptance, run on the shipped
# function; the stipulated world is the drum model's trace and the callee,
# nothing inside the def under test.

def _run_case_stub(raise_cls=None):
    """A `run_case` module whose `render_drum_solo` always refuses: with
    `raise_cls` when given, else with the module's OWN `Refused` -- its own
    class, as the real run_case's is preparation_contract's."""
    m = types.ModuleType("run_case")
    m.Refused = type("Refused", (Exception,), {})

    def render_drum_solo(sound, accent=1.0, **kw):
        raise (raise_cls or m.Refused)(f"stubbed refusal for {sound}")
    m.render_drum_solo = render_drum_solo
    return m


def _drum_solo_report(monkeypatch, run_case):
    """A callable that runs the REAL `drum_solo_report` as far as
    `rc.render_drum_solo`: one strike of one stop at frame 600 and no other
    circuit, one accent write that matches the spec, no coefficient writes."""
    monkeypatch.syspath_prepend(str(ROOT / "rtl-sketch"))
    import verify_synth_top as top
    stop, strike, n = 3, 600, 2000

    class Drums:
        def play(self, dw, frames):
            self.trace = {"fire": [0] * strike + [1 << stop] + [0] * (frames - strike - 1)}

    class DrumsFxModule:
        N_STOPS, STOP_NAMES = 16, [f"s{i}" for i in range(16)]
        A_STOPS, A_ACCENT, A_RESET = 0x10, 0x20, 0x30
        DrumsFx = Drums

        @staticmethod
        def accent_reg(accent):
            return 7

    monkeypatch.setattr(top, "dx", DrumsFxModule)
    monkeypatch.setattr(sys, "path", list(sys.path))     # the def inserts tools/ into it
    monkeypatch.setitem(sys.modules, "run_case", run_case)
    writes = [(strike, 0, top.SEC_D, DrumsFxModule.A_ACCENT + stop, 7)]
    spec = {"sound": "BD", "stop": stop, "accent": 1.0, "seconds": 0.15,
            "requested_coef_offsets": [], "lead_frames": 480, "n_render": 100}
    return lambda: top.drum_solo_report(writes, n, [0] * 400, spec, 1)


def test_drum_solo_refusal_from_the_callee_is_caught_by_the_callers_handler(monkeypatch):
    """Positive: a refusal raised by `rc.render_drum_solo` at the census site
    comes back inside the report's `refused` list, not as an exception."""
    rep, _ = _drum_solo_report(monkeypatch, _run_case_stub())()
    assert rep["refused"] == ["stubbed refusal for BD"], rep


def test_drum_solo_refusal_from_a_sibling_class_escapes_that_handler(monkeypatch):
    """The control that makes the first test a measurement: the callee raises
    a SIBLING `Refused` and the same handler lets it out of the report as an
    exception -- the defect class the census exists to surface, injected into
    the callee and observed where the verdict is issued."""
    sibling = type("Refused", (Exception,), {})
    with pytest.raises(sibling, match="stubbed refusal for BD"):
        _drum_solo_report(monkeypatch, _run_case_stub(raise_cls=sibling))()


# ---------------------------------------------------------------------------
# #1598 review: three places the census disagreed with Python on the family
# lookup. Each control states the Python behaviour it pins, then the census.
# ---------------------------------------------------------------------------
MIXED_FORMS_BORROWER = '''
def mixed(p):
    import math as R
    from mod_a import Refused as R      # the LAST binding of R: mod_a's class
    raise R("x")
'''

MIXED_FORMS_REVERSED = '''
from mod_a import Refused as R
import math as R                        # the LAST binding of R: the math module

def mixed(p):
    return R.floor(p)
'''

MIXED_FORMS_REVERSED_RAISING = '''
from mod_a import Refused as R
import math as R                        # R is math here, and `R(...)` is not a refusal

def mixed(p):
    raise R("x")
'''

MIXED_CALLER = '''
import {mod}

def go(p):
    {mod}.mixed(p)
'''


def test_mixed_import_forms_last_binding_wins_so_the_refusal_is_not_hidden(tmp_path):
    """Python: `import math as R` then `from mod_a import Refused as R` binds
    R to mod_a's class, so `raise R()` escapes an unguarded caller. The two
    import maps both kept R and the plain import won at lookup, so the census
    omitted the call entirely."""
    files, idx = _local_tree(tmp_path, {
        "borrow_mixed": MIXED_FORMS_BORROWER,
        "caller_mixed": MIXED_CALLER.format(mod="borrow_mixed")})
    got = _sites_for(files, idx, "caller_mixed")
    assert len(got) == 1, got
    assert got[0]["origins"] == ["mod_a"], got[0]
    assert got[0]["classification"] == "candidate-escape", got[0]


def test_mixed_import_forms_reversed_unbinds_the_family_name(tmp_path):
    """The negative control, in the other order: the plain import is the last
    binding, R is `math`, and `raise R()` is no refusal. Without the unbinding
    the stale family name would invent an escape."""
    files, idx = _local_tree(tmp_path, {
        "borrow_rev": MIXED_FORMS_REVERSED_RAISING,
        "caller_rev": MIXED_CALLER.format(mod="borrow_rev")})
    assert idx[str(tmp_path / "borrow_rev.py")].refused_names == {}, "R is math at module level"
    assert _sites_for(files, idx, "caller_rev") == []


RELAY = '''
from mod_a import Refused               # a re-export: the lender's own class
'''

RELAY_RAISER = '''
from relay import Refused as R

def boom(p):
    raise R("x")
'''

RELAY_CALLER = '''
from {lender} import Refused as R
import relay_raiser

def go(p):
    try:
        relay_raiser.boom(p)
    except R:
        return None
'''


def test_reexported_class_resolves_to_one_origin_on_both_sides(tmp_path):
    """Python: `relay.Refused` IS `mod_a.Refused`, so a handler naming it
    catches a raise of it. The raise side kept the re-exporter's stem
    (`relay`) while the handler side resolved to the definer (`mod_a`), so
    the census read one class as two and reported an escape."""
    files, idx = _local_tree(tmp_path, {
        "relay": RELAY, "relay_raiser": RELAY_RAISER,
        "caller_relay": RELAY_CALLER.format(lender="relay")})
    got = _sites_for(files, idx, "caller_relay")
    assert len(got) == 1, got
    assert got[0]["origins"] == ["mod_a"], got[0]
    assert got[0]["classification"] == "covered(exact)", got[0]


def test_reexport_control_a_sibling_handler_still_leaves_the_escape(tmp_path):
    """The negative control for the re-export fix: the handler names mod_b's
    class, a sibling of mod_a's, so the raise still escapes it."""
    files, idx = _local_tree(tmp_path, {
        "relay": RELAY, "relay_raiser": RELAY_RAISER,
        "caller_relay_sib": RELAY_CALLER.format(lender="mod_b")})
    got = _sites_for(files, idx, "caller_relay_sib")
    assert len(got) == 1 and got[0]["classification"] == "candidate-escape", got
    assert got[0]["uncovered"] == ["mod_a"], got[0]


BASE_RAISER = '''
from mod_a import Refused

def boom(p):
    raise Refused("x")
'''

BASE_LOCAL_CALLER = '''
import base_raiser

def go(p):
    from {lender} import Refused as R
    try:
        base_raiser.boom(p)
    except R:
        return None
'''


def test_locally_imported_common_base_covers_every_subclass(tmp_path):
    """Python: `from refusal import Refused as R` inside the def and `except
    R` catches a raise of any subclass, mod_a's included. The census took the
    name for an exact guard on `refusal` and listed mod_a as uncovered."""
    files, idx = _local_tree(tmp_path, {
        "base_raiser": BASE_RAISER,
        "caller_base_local": BASE_LOCAL_CALLER.format(lender="refusal")})
    got = _sites_for(files, idx, "caller_base_local")
    assert len(got) == 1, got
    assert got[0]["classification"] == "covered(base)", got[0]
    assert got[0]["uncovered"] == [], got[0]


def test_locally_imported_sibling_is_not_a_base_guard(tmp_path):
    """The negative control: the same shape naming mod_b's class, a sibling
    of the raised mod_a class, covers nothing it was not named for."""
    files, idx = _local_tree(tmp_path, {
        "base_raiser": BASE_RAISER,
        "caller_base_sib": BASE_LOCAL_CALLER.format(lender="mod_b")})
    got = _sites_for(files, idx, "caller_base_sib")
    assert len(got) == 1 and got[0]["classification"] == "candidate-escape", got
    assert got[0]["uncovered"] == ["mod_a"], got[0]


# ---------------------------------------------------------------------------
# #1702 controls: a try is credited only within the call's own scope
# ---------------------------------------------------------------------------
# The walk up from a call credited EVERY try whose body contains it
# lexically, so a def, a lambda or a generator expression WRITTEN inside a
# try lent that try's handler to a call that runs somewhere else: a false
# COVERED, the optimistic direction. The real tree held 0 such rows at
# 73ff5717, so every control here PLANTS its shape; and because the rule is
# a claim about when Python runs a call, each one RUNS the planted caller
# first -- imported from the file the census then reads -- and holds the
# census row to the interpreter's verdict.

def _python_verdict(monkeypatch, path: pathlib.Path, mod: str = "mod_a",
                    fn: str = "load") -> str:
    """What the INTERPRETER does with a planted caller, imported as a real
    module from the very file the census reads, against a callee that
    always refuses with a class of its own in the family: 'caught' when the
    module runs to completion after the callee raised, 'escaped' when the
    refusal leaves the module, 'not-run' when the callee was never reached
    (the planting does not exercise the body it defers -- a defect in the
    control, not a verdict)."""
    import refusal
    calls: list = []
    m = types.ModuleType(mod)
    m.Refused = type("Refused", (refusal.Refused, Exception), {})

    def refuse(*args, **kw):
        calls.append((args, kw))
        raise m.Refused("stubbed refusal")

    setattr(m, fn, refuse)
    monkeypatch.setitem(sys.modules, mod, m)
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, path.stem, module)
    try:
        spec.loader.exec_module(module)
    except m.Refused:
        return "escaped" if calls else "not-run"
    return "caught" if calls else "not-run"


def _scope_try(body: str, trigger: str = "", imports: str = "mod_a") -> str:
    """A module-level `try` with an exact handler (`except mod_a.Refused`,
    the callee's own class) around `body`, then `trigger` after it: the
    same try, handler and refusing call around every boundary in turn, so
    the boundary is all a control varies. Without a trigger the try is all
    there is, and its own body makes the call -- or does not."""
    indented = textwrap.indent(textwrap.dedent(body).strip("\n"), "    ")
    return (f"import {imports}\n\ntry:\n{indented}\n"
            "except mod_a.Refused:\n    pass\n" + (f"\n{trigger}\n" if trigger else ""))


def _plant(tmp_path, monkeypatch, name: str, src: str, python,
           mod: str = "mod_a") -> dict:
    """The control both ways, on ONE file: the planted caller is written,
    RUNS to the verdict the control names -- so a 'caught' is never the
    callee simply not raising and an 'escaped' never a planting that never
    called -- and the same file is then read by the census, its one row
    returned for the reading."""
    want = (python,) if isinstance(python, str) else tuple(python)
    path = tmp_path / f"{name}.py"
    path.write_text(src)
    assert _python_verdict(monkeypatch, path, mod=mod) in want, src
    files, idx = _local_tree(tmp_path, {name: src})
    got = _sites_for(files, idx, name)
    assert len(got) == 1, got
    return got[0]


def _reads(s: dict, classification: str) -> None:
    """The whole row, not just its class: an escape has no guard credited
    and leaves the callee's one origin uncovered; a cover here is the
    planted exact handler and leaves nothing."""
    row = (s["classification"], s["guard"], s["uncovered"])
    if classification == "candidate-escape":
        assert row == (classification, "none", ["mod_a"]), s
    else:
        assert row == (classification, "exact", []), s


SCOPE_DEF = _scope_try('''
    def later(**kw):
        return mod_a.load(**kw)
''', 'later(path="x")')

SCOPE_LAMBDA = _scope_try('''
    later = lambda **kw: mod_a.load(**kw)
''', 'later(path="x")')

SCOPE_GENEXP = _scope_try('''
    rows = (mod_a.load(path=p) for p in ["x"])
''', "list(rows)")


def test_call_in_a_def_written_inside_the_try_escapes_it(tmp_path, monkeypatch):
    """Python: the `def` statement runs inside the try and only BINDS
    `later`; the call runs at `later(...)`, after the try. The census
    credited the try to the call -- the shape #1702 was filed on."""
    s = _plant(tmp_path, monkeypatch, "scope_def", SCOPE_DEF, "escaped")
    _reads(s, "candidate-escape")


def test_call_in_a_lambda_written_inside_the_try_escapes_it(tmp_path, monkeypatch):
    """Python: the lambda is CREATED inside the try and called after it."""
    s = _plant(tmp_path, monkeypatch, "scope_lambda", SCOPE_LAMBDA, "escaped")
    _reads(s, "candidate-escape")


def test_call_in_a_generator_expression_inside_the_try_escapes_it(tmp_path, monkeypatch):
    """Python: a generator expression is only CREATED where it is written;
    its element runs when something consumes it, here after the try."""
    s = _plant(tmp_path, monkeypatch, "scope_genexp", SCOPE_GENEXP, "escaped")
    _reads(s, "candidate-escape")


@pytest.mark.parametrize("comp", [
    "rows = [mod_a.load(path=p) for p in ['x']]",
    "rows = {mod_a.load(path=p) for p in ['x']}",
    "rows = {p: mod_a.load(path=p) for p in ['x']}",
], ids=["list", "set", "dict"])
def test_call_in_a_comprehension_inside_the_try_stays_covered(tmp_path, monkeypatch, comp):
    """Python: a comprehension runs to completion where it is written -- the
    generator expression is the lazy member of the family -- so the refusal
    is raised inside the try. The boundary is the generator expression, not
    the comprehension node kind it shares a scope with."""
    s = _plant(tmp_path, monkeypatch, "scope_comp", _scope_try(comp), "caught")
    _reads(s, "covered(exact)")


SCOPE_AMBIG_PLAY = '''
import refusal

class Refused(refusal.Refused, Exception):
    pass

def load(x):
    raise Refused("no records")
'''

SCOPE_AMBIG_DEF = '''
import refusal
from play import load

try:
    def later(**kw):
        return load(**kw)
except refusal.Refused:
    pass

later(path="x")
'''

SCOPE_AMBIG_COMP = '''
import refusal
from play import load

try:
    rows = [load(path=p) for p in ["x"]]
except refusal.Refused:
    pass
'''


def _plant_collision(tmp_path, monkeypatch, name: str, src: str, python: str) -> dict:
    """As `_plant`, on a two-way same-stem collision with a BASE handler: the
    branch that reads tries to decide `covered(base)` or `ambiguous-callee`."""
    path = tmp_path / f"{name}.py"
    path.write_text(src)
    assert _python_verdict(monkeypatch, path, mod="play") == python, src
    files = _collision_tree(tmp_path, SCOPE_AMBIG_PLAY, PLAY_BETA, name, src)
    idx = rss.build_index(files)
    got = _sites_for(files, idx, name)
    assert len(got) == 1, got
    return got[0]


def test_a_def_inside_the_try_does_not_settle_a_stem_collision(tmp_path, monkeypatch):
    """The collision branch read tries the same lexical way. A base guard
    reached only through a def's body settles nothing, so the row falls
    back to `ambiguous-callee` exactly as an unguarded collision does."""
    s = _plant_collision(tmp_path, monkeypatch, "scope_amb_def", SCOPE_AMBIG_DEF,
                         "escaped")
    assert (s["classification"], s["guard"]) == ("ambiguous-callee", "none"), s


def test_a_comprehension_inside_the_try_still_settles_a_stem_collision(tmp_path, monkeypatch):
    """The other side of the same decision: a base guard the comprehension
    does run under still settles the collision."""
    s = _plant_collision(tmp_path, monkeypatch, "scope_amb_comp", SCOPE_AMBIG_COMP,
                         "caught")
    assert (s["classification"], s["guard"]) == ("covered(base)", "base"), s


# --- the four shapes the issue asks a decision for, each with its reason ---

SCOPE_DECORATOR = _scope_try('''
    @mod_a.load(path="x")
    def decorated():
        return 1
''')


def test_a_decorator_call_runs_at_def_time_inside_the_try(tmp_path, monkeypatch):
    """Decorator: NOT excluded here, for a reason that differs from the
    body. Python evaluates the decorator expression as the `def` statement
    runs -- inside the try -- and calls it with the function, so the
    refusal is raised under the try. The walk passes THROUGH a def it was
    entered by a decorator, and stops only for the body."""
    s = _plant(tmp_path, monkeypatch, "scope_decorator", SCOPE_DECORATOR, "caught")
    _reads(s, "covered(exact)")


SCOPE_ASYNC_DEF = _scope_try('''
    async def later(**kw):
        return mod_a.load(**kw)
''', 'asyncio.run(later(path="x"))', imports="asyncio\nimport mod_a")


def test_call_in_an_async_def_written_inside_the_try_escapes_it(tmp_path, monkeypatch):
    """`async def`: the same boundary as a def, because its body runs when
    the coroutine is awaited, not where it is defined."""
    s = _plant(tmp_path, monkeypatch, "scope_async", SCOPE_ASYNC_DEF, "escaped")
    _reads(s, "candidate-escape")


SCOPE_YIELD = '''
import mod_a

def rows():
    try:
        yield mod_a.load(path="x")
    except mod_a.Refused:
        yield "caught"

list(rows())
'''


def test_a_call_yielded_under_the_generators_own_try_stays_covered(tmp_path, monkeypatch):
    """`yield`: no exclusion is needed for the site's own scope. Python runs
    the call on `next()`, while the generator's frame is inside its own
    try, so that try's handler takes it. #1600's helper credit keeps a
    yield out because THERE the try belongs to another def; here it is the
    call's own. A walk that made a boundary of any def containing a yield
    would lose this row."""
    s = _plant(tmp_path, monkeypatch, "scope_yield", SCOPE_YIELD, "caught")
    _reads(s, "covered(exact)")


SCOPE_CLASS_BODY = _scope_try('''
    class Holder:
        row = mod_a.load(path="x")
''')

SCOPE_CLASS_METHOD = _scope_try('''
    class Holder:
        def row(self, **kw):
            return mod_a.load(**kw)
''', 'Holder().row(path="x")')


def test_a_call_in_a_class_body_runs_where_the_class_statement_is(tmp_path, monkeypatch):
    """Class body: NOT a boundary, though the issue lists one. Python
    executes a class body where the `class` statement is -- like a
    comprehension, unlike a def -- so a try around the class covers the
    call. Stopping the walk at the class statement would have manufactured
    a false escape; the verdict asserted above is the interpreter's, not a
    reading of the reference manual."""
    s = _plant(tmp_path, monkeypatch, "scope_class_body", SCOPE_CLASS_BODY, "caught")
    _reads(s, "covered(exact)")


def test_a_call_in_a_method_written_inside_the_try_escapes_it(tmp_path, monkeypatch):
    """The control that keeps the class-body decision honest: a method is a
    def, its body runs at the call, and the try around the class statement
    is not on its path."""
    s = _plant(tmp_path, monkeypatch, "scope_class_method", SCOPE_CLASS_METHOD,
               "escaped")
    _reads(s, "candidate-escape")


SCOPE_LAMBDA_IN_COMP = _scope_try(
    "later = [(lambda **kw: mod_a.load(**kw)) for _ in range(1)]", 'later[0](path="x")')


def test_a_lambda_created_by_a_comprehension_still_escapes_the_try(tmp_path, monkeypatch):
    """A comprehension creates its lambda on the spot, inside the try, but
    creating a lambda is not calling it: the call runs at `later[0](...)`.
    A boundary stays crossed above an eager node, or this row is lost."""
    s = _plant(tmp_path, monkeypatch, "scope_lambda_in_comp", SCOPE_LAMBDA_IN_COMP,
               "escaped")
    _reads(s, "candidate-escape")


@pytest.mark.parametrize("body", [
    'row = (lambda **kw: mod_a.load(**kw))(path="x")',
    '@lambda f: mod_a.load(path="x")\ndef decorated():\n    return 1',
], ids=["called-on-the-spot", "as-a-decorator"])
def test_a_lambda_invoked_where_written_runs_inside_the_try(tmp_path, monkeypatch, body):
    """The two places the AST itself says a lambda is invoked on the spot:
    as the callee of a call, and as a decorator (called with the def). Its
    body runs there, under the try, and the walk must not stop at it."""
    s = _plant(tmp_path, monkeypatch, "scope_lambda_invoked", _scope_try(body), "caught")
    _reads(s, "covered(exact)")


SCOPE_DEF_IN_A_DEF = '''
import mod_a

def go(p):
    try:
        def later(**kw):
            return mod_a.load(**kw)
    except mod_a.Refused:
        return None
    return later(path=p)

go("x")
'''

SCOPE_NESTED_OWN_TRY = '''
import mod_a

def go(p):
    try:
        def inner(**kw):
            try:
                return mod_a.load(**kw)
            except mod_a.Refused:
                return None
    except mod_a.Refused:
        return None
    return inner(path=p)

go("x")
'''


def test_a_try_in_an_enclosing_def_is_not_lent_to_a_nested_defs_call(tmp_path, monkeypatch):
    """The issue's shape one level down: the try belongs to `go`, the call
    to `later`, and `go` calling `later` after its try is where the refusal
    escapes. A boundary is a boundary at any depth, not only at module
    level."""
    s = _plant(tmp_path, monkeypatch, "scope_def_in_def", SCOPE_DEF_IN_A_DEF,
               "escaped")
    _reads(s, "candidate-escape")


def test_a_nested_defs_own_try_still_covers_its_call(tmp_path, monkeypatch):
    """The control that bounds the fix: only tries OUTSIDE the def are
    withheld. Against the unfixed scanner this row read
    `covered(exact+exact)`: `go`'s try was credited next to the call's
    own, though the call never runs under it -- the same defect on the
    side where it does no harm. After it, only the try `inner` writes is
    left; a walk that credited nothing once a def was anywhere on the
    path would lose this row."""
    s = _plant(tmp_path, monkeypatch, "scope_own_try", SCOPE_NESTED_OWN_TRY, "caught")
    _reads(s, "covered(exact)")


# --- what defeats a scope rule built from the boundaries the issue names ---

SCOPE_TYPE_ALIAS = _scope_try('type Row = mod_a.load(path="x")', "Row.__value__")


def test_call_in_a_type_alias_statement_inside_the_try_escapes_it(tmp_path, monkeypatch):
    """The input that defeats a rule built from the issue's four boundaries
    (verification-rules rule 8): a `type` alias statement evaluates its
    value on first use, not where it is written (PEP 695), so the refusal
    leaves the try at `Row.__value__`. No def, lambda, class or generator
    expression sits between, and without the alias among the boundaries
    this row reads `covered(exact)` -- the optimistic direction again. The
    tree held no such statement at 73ff5717."""
    s = _plant(tmp_path, monkeypatch, "scope_alias", SCOPE_TYPE_ALIAS, "escaped")
    _reads(s, "candidate-escape")


SCOPE_DEFAULT = _scope_try('''
    def needs(row=mod_a.load(path="x")):
        return row
''')

SCOPE_GENEXP_FIRST_ITERABLE = _scope_try('rows = ("ok" for p in mod_a.load(path="x"))')


def test_a_call_in_a_default_value_runs_at_def_time_inside_the_try(tmp_path, monkeypatch):
    """A default value is evaluated as the `def` statement runs, like a
    decorator, so it runs under the try around the def."""
    s = _plant(tmp_path, monkeypatch, "scope_default", SCOPE_DEFAULT, "caught")
    _reads(s, "covered(exact)")


def test_a_generator_expressions_first_iterable_runs_where_written(tmp_path, monkeypatch):
    """PEP 289 evaluates a generator expression's FIRST iterable on the
    spot and defers the rest; a rule that stopped at the generator
    expression without reading which part the call is in would lose this
    row."""
    s = _plant(tmp_path, monkeypatch, "scope_genexp_iter",
               SCOPE_GENEXP_FIRST_ITERABLE, "caught")
    _reads(s, "covered(exact)")


SCOPE_USED_ON_THE_SPOT = [
    _scope_try("rows = list(mod_a.load(path=p) for p in ['x'])"),
    _scope_try("rows = sorted(['x'], key=lambda p: mod_a.load(path=p))"),
    _scope_try("def needs(row: mod_a.load(path='x')):\n    return row"),
]


@pytest.mark.parametrize("src", SCOPE_USED_ON_THE_SPOT, ids=[
    "generator-expression-drained-by-list", "lambda-passed-as-a-key",
    "call-in-an-annotation"])
def test_a_deferred_call_the_same_try_uses_on_the_spot_reads_candidate_escape(
        tmp_path, monkeypatch, src):
    """The cost of the scope rule, pinned so it is a reading and not a
    rumour. Python catches the first two inside the try and, on 3.12
    without `from __future__ import annotations`, evaluates the third
    there too (with the import it never runs it): none escapes. But WHICH
    calls consume their argument, and whether an annotation is evaluated
    at all, is the callee's and the module's business, not the AST's, so
    the walk takes the pessimistic side. A false escape is a row to
    triage; a false covered is the defect this issue exists to remove."""
    s = _plant(tmp_path, monkeypatch, "scope_on_the_spot", src, ("caught", "not-run"))
    _reads(s, "candidate-escape")

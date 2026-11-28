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
census, accepted with the runtime evidence for its acceptance. The #1656
section pins CLI credit by PATH instead of by presence: the site #1577
accepted is covered by it, and the sibling-helper, try-after-dispatch,
value-reference and dispatch-table shapes are each pinned both ways.
The #1641 section pins the CLI boundary reached through a `_cli()` wrapper the
dispatch calls -- the placement hh_probe5's first repair used, which the
census read as no guard -- and the inputs that must not be credited as one. The
#1718 section plants an `async def` callee, which `funcs` did not register: each
control RUNS the planted coroutine code first and holds the census row to the
interpreter's verdict, and each fix is reverted in memory to show that its
control goes red without it. The #1956 section plants a SUBCLASS of the
callee's own family raised under its name, which `raise_origins` read as no
refusal: each row is held to the interpreter's verdict on the planted files,
the same-named class outside the family is the rule-8 control, and each of
the fix's three parts is reverted in memory to show which control goes red
without it. One blind spot the fix leaves is pinned as such: a dotted base.
"""
from __future__ import annotations

import ast
import asyncio
import importlib.util
import json
import pathlib
import sys
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import refusal                                        # noqa: E402
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
# lexically outside that try (it sits in the helper's def), so at #1577 the
# census reported it `guard=none, candidate-escape` and the site was ACCEPTED
# rather than repaired -- a second handler at the call would only restate the
# one that already catches it. Since #1656 the credit is by path: the call to
# `_render()` is an edge that carries the try, and the row reads
# `covered(cli-exact)` (REAL_TREE_ROWS pins it). The controls below are still
# the evidence that the label is true, run on the shipped function; the
# stipulated world is the drum model's trace and the callee, nothing inside
# the def under test.

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


# --- #1656 controls: CLI credit by PATH, not by presence -----------------------
# `cli_boundary_guards()` credited a handler by PRESENCE: any try directly in
# `main()` or under the `__main__` dispatch covered EVERY uncovered site in the
# module. That is sound only for a frame every CLI path passes through, and
# `main()` is not one: it fans out to sibling helpers, and a sibling's try
# guards its own branch alone. On the real tree (origin/main at a3186508, 388
# rows) 36 rows at 35 sites move, and the by-presence label was wrong in both
# directions: 8 sites guarded on every path were listed as escapes, and 22
# rows at 21 sites with no handler on their path read `covered` or
# `relabelled`; the other 6 are paths this graph cannot see, kept as escapes
# on purpose (the row-by-row table is in the PR). The credit is
# now per path -- a site is `covered(cli-*)` only when every path from the
# module's entry to the site's own def runs through the handler's try body --
# and `via` names the handlers it rests on. Each control that names a defect
# started red against the by-presence scanner; the others pin an input that
# would defeat a looser rule (verification-rules rule 8).

PATH_HEAD = '''
import sys
import refusal
import mod_a
import mod_b

Refused = mod_a.Refused                 # an ALIAS of mod_a's class: shape exact
'''

PATH_ENTER_MAIN = "    sys.exit(main())\n"

PATH_GUARD_TWO_FRAMES_UP = '''
def image_identity(pub):
    return mod_a.load(pub)              # the site: no try anywhere in this def

def build():
    return image_identity("pub")        # ...nor in this one

def check(fresh=None):
    try:
        return build()
    except Refused as exc:              # the alias: catches mod_a's exactly
        return f"REFUSED {exc}"

def main(argv=None):
    return check()
'''

PATH_EVERY_CALLER_GUARDS = '''
def gate(pack):
    return [mod_a.load(pack), mod_b.measure(pack)]   # two origins, one def

def cmd_gate(pack):
    try:
        return gate(pack)
    except refusal.Refused as exc:
        return 2

def cmd_run(pack):
    try:
        gate(pack)
    except refusal.Refused as exc:      # a DIFFERENT try from cmd_gate's
        return 2
    return 0

def main(argv=None):
    return cmd_gate("x") + cmd_run("y")
'''

PATH_BASE_AND_EXACT_CALLERS = '''
def image_identity(pub):
    return mod_a.load(pub)

def build():
    return image_identity("pub")

def check():
    try:
        return build()
    except Refused as exc:              # exact: mod_a's alone
        return str(exc)

def stale_control():
    try:
        return build()
    except refusal.Refused as exc:      # base: every family
        return str(exc)

def main(argv=None):
    return [check(), stale_control()]
'''

PATH_SIBLING_HELPER = '''
def synth(click_db):
    return mod_a.load(click_db)         # the site

def candidate():
    return synth(0.0)

def onset():
    return synth(-3.0)

def main(argv=None):
    out = []
    try:
        out.append(candidate())         # guarded HERE, under an `if` in a loop
    except refusal.Refused as exc:
        out.append(str(exc))
    out.append(onset())                 # ...and not here: a sibling's bare call
    return out
'''

PATH_GUARD_UNDER_A_BRANCH = '''
def candidate(trim_s):
    return mod_a.load(trim_s)

def main(argv=None):
    out = {}
    for trim_s in (1.5, 2.0):
        if trim_s:
            try:
                out[trim_s] = candidate(trim_s)
            except refusal.Refused as exc:
                out[trim_s] = {"refused": str(exc)}
    return out
'''

PATH_TWO_CALLS_IN_MAIN = '''
def helper(p):
    return mod_a.load(p)

def main(argv=None):
    try:
        helper("x")
    except refusal.Refused as exc:
        print(exc)
    return helper("y")                  # the same callee on a path with no handler
'''

PATH_TRY_AFTER_OTHER_SUBCOMMANDS = '''
def load_manifest(path):
    return mod_a.load(path)             # reached from main with no try

def analyse(bundle):
    return {"image": mod_b.measure(bundle)}     # reached ONLY through the broad try

def cross_image(out):
    return load_manifest(out)

def main(argv=None):
    if argv == "cross-image-controls":
        return cross_image("out")      # this branch returns first
    try:
        rec = analyse("bundle")         # the broad try, a direct child of main
    except BaseException as exc:
        rec = {"verdict": "ERROR"}
    return rec
'''

PATH_TRY_BELOW_THE_SITE = '''
def noise_floor(p):
    x = mod_a.load(p)                   # written BEFORE the try...
    try:
        y = mod_b.measure(x)            # ...which was added for THIS call
    except refusal.Refused as e:
        return {"refused": str(e)}
    return x + y

def main(argv=None):
    return noise_floor("p")
'''

PATH_WRAPPER = '''
def go(p):
    return mod_a.load(p)

def _cli(argv=None):
    try:
        return go(argv)
    except refusal.Refused as exc:
        print(exc)
        return 2
'''

PATH_WRAPPER_UNDER_MAIN = PATH_WRAPPER + '''
def main(argv=None):
    return _cli(argv)
'''

PATH_WRAPPER_TWO_FRAMES_IN = '''
def go(p):
    return mod_a.load(p)

def _inner(argv):
    try:
        return go(argv)
    except refusal.Refused as exc:
        print(exc)
        return 2

def _cli(argv=None):
    return _inner(argv)               # the dispatch reaches the try TWO frames in
'''

PATH_ENTER_WRAPPER = "    sys.exit(_cli())\n"

PATH_ENTER_THROUGH_A_DEF_IN_THE_DISPATCH = '''
    def inner():
        return _cli()                 # written in a def of the dispatch's own
    sys.exit(inner())
'''

PATH_VALUE_REFERENCE = '''
def helper(p):
    return mod_a.load(p)

def apply(fn, p):
    return fn(p)                        # a callback parameter: no edge to follow

def main(argv=None):
    try:
        r = helper("x")                 # THIS path is guarded...
    except refusal.Refused as exc:
        r = str(exc)
    return apply(helper, "y")           # ...and this one passes helper as a VALUE
'''

PATH_PARAM_SHADOWS_DEF = '''
def helper(p):
    return mod_a.load(p)              # never called: run_cb's `helper` is its PARAMETER

def run_cb(helper):
    try:
        return helper("x")
    except refusal.Refused:
        return 2

def main(argv=None):
    return run_cb(lambda x: 0)
'''

PATH_PARAM_SHADOWS_TABLE = '''
def cmd_measure():
    return mod_a.load("x")            # never reached: run_cb's COMMANDS is its PARAMETER

COMMANDS = {"a": cmd_measure}

def run_cb(COMMANDS):
    try:
        return COMMANDS["a"]()
    except refusal.Refused:
        return 2

def main(argv=None):
    return run_cb({"a": lambda: 0})
'''

PATH_COMPREHENSION_TARGET_IS_NOT_A_LOCAL = '''
def helper(p):
    return mod_a.load(p)

def apply(fn, p):
    return fn(p)

def main(argv=None):
    _ = [helper for helper in ()]     # the target is the comprehension's own
    try:
        helper("x")
    except refusal.Refused:
        pass
    return apply(helper, "y")         # a value read of the def: an unseen path
'''

PATH_COMPREHENSION_TARGET_ONLY = '''
def helper(p):
    return mod_a.load(p)

def main(argv=None):
    _ = [helper for helper in ()]     # the target is the comprehension's own
    try:
        return helper("x")            # still `def helper`: guarded by this try
    except refusal.Refused:
        return 2
'''

PATH_EXCEPT_AS_IS_A_LOCAL = '''
def verdict(p):
    return mod_a.load(p)

def check():
    try:
        return 0
    except ValueError as verdict:     # binds `verdict` in check() only
        return str(verdict)

def main(argv=None):
    try:
        return verdict("y")
    except refusal.Refused:
        return 2
'''

PATH_LOCAL_VARIABLE_NAMED_LIKE_A_FUNCTION = '''
def site(p):
    return mod_a.load(p)              # the site, below the def that shares a name

def verdict(p):
    return site(p)                    # no try here

def main(argv=None):
    try:
        return verdict("y")           # the guard, around the CALL to verdict()
    except refusal.Refused as exc:
        return str(exc)

def check(p):
    ok, verdict = True, "x"           # a LOCAL that shares the def's name
    return f"{ok} {verdict}"
'''

PATH_CALL_FROM_A_CLAUSE = '''
def helper(p):
    return mod_a.load(p)              # the site, in a def main calls from a clause

def main(argv=None):
    try:
        touch(None)
%(pre)s
    %(clause)s
        return helper("x")
    return 0
'''

# where in the try the call to `helper()` is written: its except clause, its
# else clause, or its finally clause -- never its body
CLAUSE_SHAPES = {
    "except": dict(pre="", clause="except (OSError, refusal.Refused):"),
    "else": dict(pre="    except (OSError, refusal.Refused):\n        touch(None)", clause="else:"),
    "finally": dict(pre="    except (OSError, refusal.Refused):\n        touch(None)",
                    clause="finally:"),
}

PATH_METHOD_CALL = '''
class Loader:
    def load(self, p):
        return mod_a.load(p)            # in a method

def main(argv=None):
    try:
        return Loader().load("x")       # reached through an instance
    except refusal.Refused as exc:
        return 2
'''

PATH_LAMBDA_CALL = '''
def helper(p):
    return mod_a.load(p)

def main(argv=None):
    try:
        return list(map(lambda q: helper(q), ["x"]))   # called from a lambda
    except refusal.Refused as exc:
        return 2
'''

PATH_MIXED_LAMBDA = '''
def helper(p):
    return mod_a.load(p)

def a():
    try:
        return helper("x")
    except refusal.Refused as exc:
        return 2

def b():
    f = lambda: helper("y")             # a real path, no handler on it
    return f()

def main(argv=None):
    a()
    return b()
'''

PATH_MIXED_METHOD = '''
def helper(p):
    return mod_a.load(p)

def a():
    try:
        return helper("x")
    except refusal.Refused as exc:
        return 2

class Driver:
    def go(self):
        return helper("y")              # a real path, no handler on it

def main(argv=None):
    a()
    return Driver().go()
'''

PATH_UNCALLED_FUNCTION = '''
def helper(p):
    return mod_a.load(p)                # nothing at module level calls it

def main(argv=None):
    try:
        return mod_b.measure("x")
    except refusal.Refused as exc:
        return 2
'''

PATH_MODULE_LEVEL_SITE = '''
def main(argv=None):
    try:
        return mod_b.measure("x")
    except refusal.Refused as exc:
        return 2

CFG = mod_a.load("cfg")                 # import time: before the dispatch runs
'''

PATH_INTERSECTION = '''
import dual

def both(p):
    return dual.fetch(p)                # raises mod_a's OR mod_b's

def via_a(p):
    try:
        return both(p)
    except mod_a.Refused:               # catches mod_a's alone
        return None

def via_b(p):
    try:
        return both(p)
    except mod_b.Refused:               # catches mod_b's alone
        return None

def main(argv=None):
    return [via_a("a"), via_b("b")]
'''

DUAL_RAISER = '''
from mod_a import Refused as RA
from mod_b import Refused as RB

def fetch(p):
    if p == "a":
        raise RA("a's")
    raise RB("b's")
'''

PATH_CYCLE = '''
def helper(p):
    return mod_a.load(p)                # every path here came through a()'s try

def a(n):
    early = mod_b.measure(n)            # OUTSIDE the try: a path-union credit takes it
    try:
        return b(n)
    except refusal.Refused as exc:
        return early

def b(n):
    if n:
        return helper(n)
    return a(n + 1)                     # the cycle

def main(argv=None):
    return a(1)
'''

PATH_TABLE_INLINE = '''
def cmd_compare(p):
    return mod_a.load(p)

def cmd_validate(p):
    return mod_b.measure(p)
'''

PATH_TABLE_ENTER_INLINE = '''
    cmd = sys.argv[1] if len(sys.argv) > 1 else "validate"
    try:
        sys.exit({"validate": cmd_validate, "compare": cmd_compare}[cmd]())
    except refusal.Refused as e:
        print(f"REFUSED  {e}")
        sys.exit(2)
'''

PATH_TABLE_BY_NAME = '''
def cmd_measure(p):
    return mod_a.load(p)

COMMANDS = {"measure": cmd_measure}
'''

PATH_TABLE_ENTER_BY_NAME = '''
    cmd = sys.argv[1] if len(sys.argv) > 1 else "measure"
    if cmd not in COMMANDS:                     # membership: reads the keys
        print(" | ".join(COMMANDS))             # a usage message: reads the keys
        sys.exit(64)
    try:
        sys.exit(COMMANDS[cmd]())
    except refusal.Refused as e:
        sys.exit(2)
'''

PATH_TABLE_ESCAPED = '''
def cmd_measure(p):
    return mod_a.load(p)

COMMANDS = {"measure": cmd_measure}
'''

PATH_TABLE_ENTER_ESCAPED = '''
    table = COMMANDS                   # the table itself escapes into a variable
    try:
        sys.exit(COMMANDS[cmd]())      # ...though it is ALSO called under the try
    except refusal.Refused as e:
        sys.exit(2)
'''

PATH_TABLE_STORED = '''
def cmd_measure(p):
    return mod_a.load(p)

COMMANDS = {"measure": cmd_measure}
'''

PATH_TABLE_ENTER_STORED = '''
    handler = COMMANDS[sys.argv[1]]     # stored in a variable: where it goes is unseen
    try:
        sys.exit(handler())
    except refusal.Refused as e:
        sys.exit(2)
'''


def _path_scan(tmp_path, body: str, entry: str, extra: dict[str, str] | None = None):
    """One CLI module `caller_path.py` -- PATH_HEAD, `body`, and the `__main__`
    dispatch `entry` (already indented) -- beside the flat tree, plus any
    `extra` sibling modules. Returns its sites keyed by (the def the site is
    written in, callee) -> the sites by line, and the module's text. The key
    is read off the AST here, not off a scanner field, so these controls fail
    on a classification against any scanner, not on a missing attribute."""
    files = _tree(tmp_path)
    text = PATH_HEAD + body + '\n\nif __name__ == "__main__":\n' + entry
    for name, module in (extra or {}).items():
        f = tmp_path / f"{name}.py"
        f.write_text(module)
        files.append(f)
    p = tmp_path / "caller_path.py"
    p.write_text(text)
    files.append(p)
    idx = rss.build_index(files)
    mod = idx[str(p)]
    got: dict[tuple[str, str], list[dict]] = {}
    for s in _sites_for(files, idx, "caller_path"):
        call = next(n for n in ast.walk(mod.tree)
                    if isinstance(n, ast.Call) and n.lineno == int(s["site"].rsplit(":", 1)[1]))
        cur = mod.parents.get(call)
        while cur is not None and not isinstance(cur, ast.FunctionDef):
            cur = mod.parents.get(cur)
        got.setdefault((cur.name if cur is not None else "<module>", s["callee"]), []).append(s)
    for rows in got.values():
        rows.sort(key=lambda s: int(s["site"].rsplit(":", 1)[1]))
    return got, text


def _one(got: dict, scope: str, callee: str) -> dict:
    rows = got[(scope, callee)]
    assert len(rows) == 1, (scope, callee, rows)
    return rows[0]


def _try_lines(text: str, scope: str, via: list[str]) -> None:
    """Every `via` entry is a real `try:` line, inside the def it names."""
    lines = text.splitlines()
    for v in via:
        where, line = v.rsplit(":", 1)
        assert lines[int(line) - 1].strip() == "try:", (v, lines[int(line) - 1])
        starts = [i for i, l in enumerate(lines, 1) if l.startswith(f"def {where}(")]
        assert starts and min(starts) < int(line), (v, starts)


def test_a_guard_in_every_caller_covers_a_site_two_frames_below(tmp_path):
    """Defect 1, the r2_release.py:111 shape, red first: the site's def and
    its caller have no try at all; the guard is in the caller's caller. By
    presence the site was an escape -- a repaired one that gets re-traced."""
    got, text = _path_scan(tmp_path, PATH_GUARD_TWO_FRAMES_UP, PATH_ENTER_MAIN)
    s = _one(got, "image_identity", "mod_a.load")
    assert s["classification"] == "covered(cli-exact)", s
    assert s["guard"] == "none", s
    assert s["uncovered"] == [], s
    assert len(s["via"]) == 1 and s["via"][0].startswith("check:"), s
    _try_lines(text, "check", s["via"])


def test_what_is_caught_is_intersected_not_which_handler(tmp_path):
    """Defect 1, the capability_pack.py:177 shape, red first: `gate()` has
    two callers and each guards its call with a DIFFERENT try. No single
    handler sits on every path, yet a base handler does -- so intersecting
    handler identity credits nothing, and this row stayed an escape."""
    got, text = _path_scan(tmp_path, PATH_EVERY_CALLER_GUARDS, PATH_ENTER_MAIN)
    for callee in ("mod_a.load", "mod_b.measure"):
        s = _one(got, "gate", callee)
        assert s["classification"] == "covered(cli-base)", s
        assert s["guard"] == "none", s
        assert [v.split(":")[0] for v in s["via"]] == ["cmd_gate", "cmd_run"], s
        _try_lines(text, "cmd_gate", s["via"][:1])
        _try_lines(text, "cmd_run", s["via"][1:])


def test_a_base_path_and_an_exact_path_meet_on_what_both_catch(tmp_path):
    """The r2_release.py shape in full: `build()` is called under an EXACT
    try, under a BASE try and under another exact one. What every path
    catches is the exact origin, not nothing. A lattice that carries a base
    handler as "no origins" meets the two into an empty guarantee and turns
    this row back into an escape -- the first implementation of #1656 did
    exactly that, and no all-base or all-exact control could see it; only
    the real-tree pin below did."""
    got, text = _path_scan(tmp_path, PATH_BASE_AND_EXACT_CALLERS, PATH_ENTER_MAIN)
    s = _one(got, "image_identity", "mod_a.load")
    assert s["classification"] == "covered(cli-exact)", s
    assert s["uncovered"] == [], s
    assert [v.split(":")[0] for v in s["via"]] == ["check", "stale_control"], s


def test_a_try_nested_under_a_branch_in_main_guards_its_own_callee(tmp_path):
    """Defect 1, the cymbal_low_tail.py:638 shape, red first: the base try is
    not a direct child of `main()` -- it sits under an `if` inside a `for` --
    so even a by-presence rule never saw it, and the site stayed an escape
    while every path to it was guarded."""
    got, text = _path_scan(tmp_path, PATH_GUARD_UNDER_A_BRANCH, PATH_ENTER_MAIN)
    s = _one(got, "candidate", "mod_a.load")
    assert s["classification"] == "covered(cli-base)", s
    assert s["guard"] == "none", s
    assert len(s["via"]) == 1 and s["via"][0].startswith("main:"), s
    _try_lines(text, "main", s["via"])


def test_a_sibling_helper_with_an_unguarded_twin_is_not_credited(tmp_path):
    """The withdrawn half of #1641, as a rule-8 control: `main()` guards ONE
    call to `synth()` and makes another with no handler. A scanner that
    credits any try in a `main()` callee by presence -- or that requires
    SOME caller to guard rather than EVERY one -- turns this escape into
    `covered`. Ten real rows moved that way when the rule was tried."""
    got, _ = _path_scan(tmp_path, PATH_SIBLING_HELPER, PATH_ENTER_MAIN)
    s = _one(got, "synth", "mod_a.load")
    assert s["classification"] == "candidate-escape", s
    assert s["uncovered"] == ["mod_a"], s
    assert "via" not in s, s


def test_the_same_callee_guarded_and_bare_in_one_def_is_an_escape(tmp_path):
    """Every call CHAIN, not every call site: one guarded call to `helper()`
    in `main()` does not cover its second, bare call two lines down."""
    got, _ = _path_scan(tmp_path, PATH_TWO_CALLS_IN_MAIN, PATH_ENTER_MAIN)
    s = _one(got, "helper", "mod_a.load")
    assert s["classification"] == "candidate-escape", s
    assert s["uncovered"] == ["mod_a"], s


def test_a_try_in_main_that_does_not_enclose_the_call_relabels_nothing(tmp_path):
    """Defect 2, the r0_capture.py:1631 shape, red first: `main()` has an
    `except BaseException` directly in it, but around ONE subcommand's call;
    the site's def is called from a branch that returned before it. By
    presence the broad try relabelled every such row `relabelled(cli-total)`
    -- a refusal that really exits as a traceback, read as a relabelled
    result."""
    got, _ = _path_scan(tmp_path, PATH_TRY_AFTER_OTHER_SUBCOMMANDS, PATH_ENTER_MAIN)
    s = _one(got, "load_manifest", "mod_a.load")
    assert s["classification"] == "candidate-escape", s
    assert s["uncovered"] == ["mod_a"], s
    assert "via" not in s, s


def test_the_broad_try_still_relabels_the_path_it_is_on(tmp_path):
    """The other half of defect 2: the same broad try does credit the def it
    encloses, so the rule is `on the path`, not `never a broad try`."""
    got, _ = _path_scan(tmp_path, PATH_TRY_AFTER_OTHER_SUBCOMMANDS, PATH_ENTER_MAIN)
    s = _one(got, "analyse", "mod_b.measure")
    assert s["classification"] == "relabelled(cli-total)", s
    assert len(s["via"]) == 1 and s["via"][0].startswith("main:"), s


@pytest.mark.parametrize("clause", sorted(CLAUSE_SHAPES))
def test_a_call_from_a_trys_clause_guards_nothing_on_the_path(tmp_path, clause):
    """The #1188 rule at the PATH, where it had no control: a try covers its
    BODY, so a call written in its except, else or finally clause runs after
    the try has handed over, and its handlers catch nothing of that call. The
    site sits one frame down, in a def `main()` calls from the clause, with a
    base handler on that try -- so a path credit that drops the body test on
    the way to the call reads it covered(cli-base)."""
    body = PATH_CALL_FROM_A_CLAUSE % CLAUSE_SHAPES[clause]
    got, _ = _path_scan(tmp_path, body, PATH_ENTER_MAIN)
    s = _one(got, "helper", "mod_a.load")
    assert s["classification"] == "candidate-escape", (clause, s)
    assert s["uncovered"] == ["mod_a"], s


def test_a_try_below_the_site_guards_only_what_follows_it(tmp_path):
    """The cymbal_low_tail.py:794 shape: the try in the site's own def starts
    two lines BELOW the site (it was added for another call). A handler is
    attached by enclosing the call, never by sharing a def with it."""
    got, _ = _path_scan(tmp_path, PATH_TRY_BELOW_THE_SITE, PATH_ENTER_MAIN)
    before = _one(got, "noise_floor", "mod_a.load")
    assert before["classification"] == "candidate-escape", before
    assert before["guard"] == "none", before
    inside = _one(got, "noise_floor", "mod_b.measure")
    assert inside["classification"] == "covered(base)", inside


def test_a_wrapper_the_dispatch_calls_is_the_first_edge(tmp_path):
    """hh_probe5's first repair (#1611): the dispatch calls `_cli()`, whose
    try guards `go()`. Under a path rule the wrapper is simply the first
    edge and needs no rule of its own (#1641's shape)."""
    got, text = _path_scan(tmp_path, PATH_WRAPPER, "    sys.exit(_cli())\n")
    s = _one(got, "go", "mod_a.load")
    assert s["classification"] == "covered(cli-base)", s
    assert s["guard"] == "none", s
    _try_lines(text, "_cli", s["via"])
    assert len(s["via"]) == 1, s


def test_a_guarded_path_through_main_is_followed_like_any_other(tmp_path):
    """The fixture of #1641's `test_a_wrapper_main_calls_is_not_the_boundary`,
    which asserted an escape: dispatch -> main() -> _cli() -> go(), with the
    try in `_cli()`. That site IS guarded on every path, so under the path
    rule it is covered, and #1641's pin -- written against the by-presence
    rule, whose `main()` half was withdrawn for false-covering ten rows -- is
    superseded by `test_a_sibling_helper_with_an_unguarded_twin_is_not_credited`,
    the sibling shape that actually defeats presence. Pinned so the fixture
    is not "repaired" back into a false escape."""
    got, _ = _path_scan(tmp_path, PATH_WRAPPER_UNDER_MAIN, PATH_ENTER_MAIN)
    s = _one(got, "go", "mod_a.load")
    assert s["classification"] == "covered(cli-base)", s
    assert len(s["via"]) == 1 and s["via"][0].startswith("_cli:"), s


def test_a_guard_two_frames_in_from_the_dispatch_is_followed(tmp_path):
    """dispatch -> _cli() -> _inner() [try] -> go(). A credit by presence
    has to stop somewhere -- #1641 stopped one level in, since at level two
    it could no longer tell a real guard from a sibling's -- so this shape
    read as an escape while the refusal is caught. A credit by path has no
    level to stop at: every path to `go` runs through `_inner`'s try."""
    got, text = _path_scan(tmp_path, PATH_WRAPPER_TWO_FRAMES_IN, PATH_ENTER_WRAPPER)
    s = _one(got, "go", "mod_a.load")
    assert s["classification"] == "covered(cli-base)", s
    assert len(s["via"]) == 1 and s["via"][0].startswith("_inner:"), s
    _try_lines(text, "_inner", s["via"])


def test_a_guard_reached_through_a_def_of_the_dispatch_is_followed(tmp_path):
    """The dispatch defines `inner()` and calls it; `inner()` calls the
    wrapper with the try. By presence a call written in a def of the
    boundary's own is "one frame further in" and reaches nothing (#1641); by
    path it is an edge like any other, and the refusal is caught."""
    got, _ = _path_scan(tmp_path, PATH_WRAPPER, PATH_ENTER_THROUGH_A_DEF_IN_THE_DISPATCH)
    s = _one(got, "go", "mod_a.load")
    assert s["classification"] == "covered(cli-base)", s
    assert len(s["via"]) == 1 and s["via"][0].startswith("_cli:"), s


# #1688 review round 3: first-match handlers, defaults, deferred bodies.
# Each shape below was reproduced against a7865f72 first (red there), then the
# scanner changed. The positive twins pin that the fix did not simply stop
# crediting (rule 8: what satisfies the check while violating its intent).

def _load_sites(got: dict, callee: str = "mod_a.load") -> list[dict]:
    return [r for rows in got.values() for r in rows if r["callee"] == callee]


PATH_VIA_WRONG_HANDLER = '''
def helper(p):
    return mod_a.load(p)

def _run(p):
    try:
        return helper(p)
    except mod_b.Refused as exc:        # a DIFFERENT family: not this refusal
        return 3

def main(argv=None):
    try:
        return _run("x")
    except refusal.Refused as exc:      # the handler that actually catches it
        return 2
'''


def test_via_names_the_handler_that_catches_not_one_that_does_not(tmp_path):
    """The M5 saw-drive shape: the nearest try on the path catches ANOTHER
    family's `Refused` (mod_b's), the base handler two frames out catches
    this one. `via` used to stop at the nearest try that caught anything and
    named it; the label rests on the base handler."""
    got, text = _path_scan(tmp_path, PATH_VIA_WRONG_HANDLER, PATH_ENTER_MAIN)
    (s,) = _load_sites(got)
    assert s["classification"] == "covered(cli-base)", s
    assert [v.split(":")[0] for v in s["via"]] == ["main"], s
    _try_lines(text, "main", s["via"])


PATH_INNER_BROAD_SHADOWS_OUTER_BASE = '''
def helper(p):
    return mod_a.load(p)

def mid(p):
    try:
        return helper(p)
    except Exception as exc:            # catches the refusal FIRST
        print("marker")
        return 1

def main(argv=None):
    try:
        return mid("x")
    except refusal.Refused as exc:      # never sees it
        return 2
'''


def test_an_inner_broad_handler_shadows_the_outer_base_one(tmp_path):
    """Python runs the first handler that matches, walking out. `mid`'s
    `except Exception` swallows the refusal and returns 1, so the CLI never
    exits through the refusal path; reading `main`'s base handler as the
    cover read it as `covered(cli-base)`."""
    got, text = _path_scan(tmp_path, PATH_INNER_BROAD_SHADOWS_OUTER_BASE, PATH_ENTER_MAIN)
    (s,) = _load_sites(got)
    assert s["classification"] == "relabelled(cli-total)", s
    assert [v.split(":")[0] for v in s["via"]] == ["mid"], s
    _try_lines(text, "mid", s["via"])


PATH_NESTED_TRIES_ONE_DEF = '''
def helper(p):
    return mod_a.load(p)

def main(argv=None):
    try:
        try:
            return helper("x")
        except Exception:               # innermost: runs first
            return 1
    except refusal.Refused:
        return 2
'''


def test_nested_tries_in_one_def_the_innermost_match_decides(tmp_path):
    """Both handlers sit on ONE edge (main -> helper); the broad one is the
    inner try. Found by the injected-defect matrix: the loosening "the best
    handler on the edge wins" was seen by no control."""
    got, text = _path_scan(tmp_path, PATH_NESTED_TRIES_ONE_DEF, PATH_ENTER_MAIN)
    (s,) = _load_sites(got)
    assert s["classification"] == "relabelled(cli-total)", s
    assert len(s["via"]) == 1, s
    assert text.splitlines()[int(s["via"][0].split(":")[1]) - 1 + 2].strip().startswith(
        "except Exception"), s


def test_an_inner_handler_for_another_family_does_not_shadow(tmp_path):
    """Rule 8, the other direction: the inner handler is exact for mod_b, so
    the base handler outside still decides (PATH_VIA_WRONG_HANDLER pins this
    too; here the callee is mod_b's own refusal, which the inner one DOES
    catch and so is `covered(cli-exact)`, not base)."""
    got, _ = _path_scan(tmp_path, PATH_VIA_WRONG_HANDLER.replace("mod_a.load", "mod_b.measure"),
                        PATH_ENTER_MAIN)
    (s,) = _load_sites(got, "mod_b.measure")
    assert s["classification"] == "covered(cli-exact)", s


def _default_case(kw: str, tmp_path) -> dict:
    body = f'''
def helper(p):
    return mod_a.load(p)

{kw} use(helper=helper):
    return helper("callback")           # `helper` here is the PARAMETER

def main(argv=None):
    try:
        helper("guarded")
    except refusal.Refused:
        return 2
    return use()                        # outside the try: the default runs helper
'''
    got, _ = _path_scan(tmp_path, body, PATH_ENTER_MAIN)
    (s,) = _load_sites(got)
    return s


def test_a_def_default_reads_the_outer_name(tmp_path):
    """`def use(helper=helper)` evaluates the default where `use` is defined,
    so `helper` is read as a value (a callback path the graph cannot see) and
    its guarded bare-name call in `main` cannot carry it. Ran for real: exits
    1 with `mod_a.Refused: unusable: callback`."""
    assert _default_case("def", tmp_path)["classification"] == "candidate-escape"


def test_an_async_def_default_reads_the_outer_name(tmp_path):
    assert _default_case("async def", tmp_path)["classification"] == "candidate-escape"


def test_a_decorator_parameter_of_the_same_name_is_not_the_def(tmp_path):
    """The decorator of a def runs in the enclosing scope too: the same class
    as a default (found by searching the class, not the cited line)."""
    body = '''
def helper(p):
    return mod_a.load(p)

def use(helper):
    @helper
    def inner():
        pass
    return inner

def main(argv=None):
    try:
        helper("guarded")
    except refusal.Refused:
        return 2
    return 0
'''
    got, _ = _path_scan(tmp_path, body, PATH_ENTER_MAIN)
    (s,) = _load_sites(got)
    assert s["classification"] == "covered(cli-base)", s     # `helper` here is the param


def test_a_default_that_names_a_local_is_still_read_through_the_local(tmp_path):
    """Rule 8: the fix must not make EVERY default a value read. A default
    naming a variable of the enclosing def reads that variable."""
    body = '''
def helper(p):
    return mod_a.load(p)

def main(argv=None):
    helper = len
    def use(f=helper):
        return f("x")
    try:
        return use()
    except refusal.Refused:
        return 2

def other():
    try:
        return helper("guarded")
    except refusal.Refused:
        return 2
'''
    got, _ = _path_scan(tmp_path, body + "\n", PATH_ENTER_MAIN + "    other()\n")
    (s,) = _load_sites(got)
    assert s["classification"] == "covered(cli-base)", s


def _deferred(tmp_path, helper: str, main_body: str) -> dict:
    body = f"import asyncio\n{helper}\ndef main(argv=None):\n{main_body}"
    got, _ = _path_scan(tmp_path, body, PATH_ENTER_MAIN)
    (s,) = _load_sites(got)
    return s


def test_a_try_around_creating_a_generator_guards_nothing(tmp_path):
    """Ran for real: exits 1 with `mod_a.Refused: unusable: next`."""
    s = _deferred(tmp_path, "def helper(p):\n    yield mod_a.load(p)\n", '''
    try:
        g = helper("next")
    except refusal.Refused:
        return 2
    return next(g)                      # the body runs HERE, outside the try
''')
    assert s["classification"] == "candidate-escape", s


def test_a_try_around_creating_a_generator_expression_guards_nothing(tmp_path):
    s = _deferred(tmp_path, "def helper(p):\n    return mod_a.load(p)\n", '''
    try:
        g = (helper(p) for p in ["x"])
    except refusal.Refused:
        return 2
    return next(g)
''')
    assert s["classification"] == "candidate-escape", s


def test_a_try_around_creating_a_coroutine_guards_nothing(tmp_path):
    s = _deferred(tmp_path, "async def helper(p):\n    return mod_a.load(p)\n", '''
    try:
        c = helper("x")
    except refusal.Refused:
        return 2
    return asyncio.run(c)               # awaited outside the try
''')
    assert s["classification"] == "candidate-escape", s


def test_a_coroutine_awaited_inside_the_try_is_guarded(tmp_path):
    """Rule 8: not every async def is deferred past the try -- `await` in it
    runs the body there."""
    body = '''
import asyncio

async def helper(p):
    return mod_a.load(p)

async def amain():
    try:
        return await helper("x")
    except refusal.Refused:
        return 2

def main(argv=None):
    return asyncio.run(amain())
'''
    got, _ = _path_scan(tmp_path, body, PATH_ENTER_MAIN)
    (s,) = _load_sites(got)
    assert s["classification"] == "covered(cli-base)", s


def test_the_first_iterable_of_a_generator_expression_runs_at_once(tmp_path):
    """Rule 8: `(x for x in helper(p))` evaluates `helper(p)` where the
    expression is written, so a try around it does guard that call."""
    s = _deferred(tmp_path, "def helper(p):\n    return [mod_a.load(p)]\n", '''
    try:
        g = (x for x in helper("x"))
    except refusal.Refused:
        return 2
    return list(g)
''')
    assert s["classification"] == "covered(cli-base)", s


def test_a_list_comprehension_in_the_try_is_guarded(tmp_path):
    s = _deferred(tmp_path, "def helper(p):\n    return mod_a.load(p)\n", '''
    try:
        return [helper(p) for p in ["x"]]
    except refusal.Refused:
        return 2
''')
    assert s["classification"] == "covered(cli-base)", s


def test_a_function_passed_as_a_value_has_a_path_no_call_shows(tmp_path):
    """Rule 8: `helper` is called by name under a try AND handed to `apply`
    as an argument. The bare-name edge is guarded; the callback's path is not
    visible, so crediting the first would false-cover the second. A scanner
    without the value rule turns this escape into `covered(cli-base)`."""
    got, _ = _path_scan(tmp_path, PATH_VALUE_REFERENCE, PATH_ENTER_MAIN)
    s = _one(got, "helper", "mod_a.load")
    assert s["classification"] == "candidate-escape", s
    assert s["uncovered"] == ["mod_a"], s


def test_a_local_variable_named_like_a_def_is_not_a_value_of_it(tmp_path):
    """Rule 8, the other direction of the value rule: `check()` holds a LOCAL
    `verdict` from a tuple-unpack while `def verdict` sits at module level
    (cymbal_tone_knob L910 is this exactly), and the site sits BELOW
    `verdict()`, guarded by `main()`'s try around its call. A name-wide rule
    taints the def, and a site guarded on every path becomes a false escape
    -- which the mutant run showed an earlier fixture of this test could not
    see: with the try INSIDE the tainted def the edge below it stayed
    guarded whatever taints the def. The label is the same under the
    by-presence scanner, which credits main()'s try by presence; red against
    a name-wide value rule."""
    got, _ = _path_scan(tmp_path, PATH_LOCAL_VARIABLE_NAMED_LIKE_A_FUNCTION, PATH_ENTER_MAIN)
    s = _one(got, "site", "mod_a.load")
    assert s["classification"] == "covered(cli-base)", s


def test_a_method_reached_through_an_instance_is_not_followed(tmp_path):
    """A stated blind spot, pinned: `Loader().load("x")` is not a bare-name
    call, so the method is never reached and never credited, though main's
    try does enclose it at runtime. The conservative direction -- red against
    the by-presence scanner, which called it covered(cli-base)."""
    got, _ = _path_scan(tmp_path, PATH_METHOD_CALL, PATH_ENTER_MAIN)
    s = _one(got, "load", "mod_a.load")
    assert s["classification"] == "candidate-escape", s


def test_a_call_from_a_lambda_is_not_followed(tmp_path):
    """A stated blind spot, pinned: the lambda is no frame anything reaches."""
    got, _ = _path_scan(tmp_path, PATH_LAMBDA_CALL, PATH_ENTER_MAIN)
    s = _one(got, "helper", "mod_a.load")
    assert s["classification"] == "candidate-escape", s


def test_an_unresolvable_caller_beside_a_guarded_one_is_an_unguarded_path(tmp_path):
    """#1688 review: a lambda or method caller has no edge into it, and
    dropping it let the guarded sibling alone carry the intersection. Mixed
    case, red against a scanner that drops the edge."""
    for body in (PATH_MIXED_LAMBDA, PATH_MIXED_METHOD):
        got, _ = _path_scan(tmp_path, body, PATH_ENTER_MAIN)
        s = _one(got, "helper", "mod_a.load")
        assert s["classification"] == "candidate-escape", (body, s)


def test_a_parameter_named_like_a_def_stops_the_name_lookup(tmp_path):
    """#1688 review: `run_cb(helper)` calls its PARAMETER, so the module-level
    `def helper` is unreached. A lookup that ignores non-def locals fell
    through to the def and read it covered(cli-base) via run_cb's try."""
    got, _ = _path_scan(tmp_path, PATH_PARAM_SHADOWS_DEF, PATH_ENTER_MAIN)
    s = _one(got, "helper", "mod_a.load")
    assert s["classification"] == "candidate-escape", s


def test_a_parameter_named_like_a_sealed_table_does_not_follow_the_table(tmp_path):
    """Same defect on a dispatch table: `COMMANDS` inside run_cb is the
    parameter, so `cmd_measure` is never reached through it."""
    got, _ = _path_scan(tmp_path, PATH_PARAM_SHADOWS_TABLE, PATH_ENTER_MAIN)
    s = _one(got, "cmd_measure", "mod_a.load")
    assert s["classification"] == "candidate-escape", s


def test_a_comprehension_target_is_not_a_local_of_the_enclosing_def(tmp_path):
    """Python 3 scopes a comprehension: `[helper for helper in ()]` binds
    nothing in main(), so `apply(helper, "y")` IS a value read of the def and
    its unseen path withholds the credit its guarded call would give."""
    got, _ = _path_scan(tmp_path, PATH_COMPREHENSION_TARGET_IS_NOT_A_LOCAL, PATH_ENTER_MAIN)
    s = _one(got, "helper", "mod_a.load")
    assert s["classification"] == "candidate-escape", s
    # and with no value read, the guarded call still reaches the def: a
    # target leaked into main()'s locals would resolve it to nothing
    got, _ = _path_scan(tmp_path, PATH_COMPREHENSION_TARGET_ONLY, PATH_ENTER_MAIN)
    s = _one(got, "helper", "mod_a.load")
    assert s["classification"].startswith("covered(cli"), s


def test_an_except_as_name_is_a_local_of_its_def_only(tmp_path):
    """`except ValueError as verdict` in a sibling check() binds `verdict`
    THERE; it is no read of `def verdict`, whose guarded call stays covered.
    Red when `except ... as` is not collected as a binding."""
    got, _ = _path_scan(tmp_path, PATH_EXCEPT_AS_IS_A_LOCAL, PATH_ENTER_MAIN)
    s = _one(got, "verdict", "mod_a.load")
    assert s["classification"].startswith("covered(cli"), s


def test_a_function_the_entry_never_reaches_is_credited_nothing(tmp_path):
    """Rule 8: `helper()` is defined and never called at module level -- dead
    here, or another module's. `main()` has a base try, which by presence
    covered this site too. Red against the by-presence scanner."""
    got, _ = _path_scan(tmp_path, PATH_UNCALLED_FUNCTION, PATH_ENTER_MAIN)
    s = _one(got, "helper", "mod_a.load")
    assert s["classification"] == "candidate-escape", s
    assert s["uncovered"] == ["mod_a"], s
    own = _one(got, "main", "mod_b.measure")
    assert own["classification"] == "covered(base)", own      # the try itself still counts


def test_a_module_level_site_is_not_under_a_later_dispatch_try(tmp_path):
    """`CFG = mod_a.load("cfg")` runs at import, before the dispatch and its
    try exist. The by-presence rule read it covered(cli-base)."""
    got, _ = _path_scan(tmp_path, PATH_MODULE_LEVEL_SITE, PATH_ENTER_MAIN)
    s = _one(got, "<module>", "mod_a.load")
    assert s["classification"] == "candidate-escape", s


def test_guarantees_are_intersected_over_callers_not_unioned(tmp_path):
    """Two callers, each exact for a DIFFERENT origin, of one site whose
    callee raises both: on every path one origin escapes. A union over
    callers reads `covered(cli-exact)`."""
    got, _ = _path_scan(tmp_path, PATH_INTERSECTION, PATH_ENTER_MAIN, {"dual": DUAL_RAISER})
    s = _one(got, "both", "dual.fetch")
    assert s["origins"] == ["mod_a", "mod_b"], s
    assert s["classification"] == "candidate-escape", s
    assert s["uncovered"] == ["mod_a", "mod_b"], s


def test_a_cycle_converges_on_every_path_not_some_path(tmp_path):
    """`a -> b -> a`. Every path to `helper()` enters through a()'s try, so
    its site is covered; `a()`'s own site sits BEFORE that try and is not. A
    least-fixed-point (union, "some path") credit takes b's handler back
    around the cycle and covers a's site too."""
    got, _ = _path_scan(tmp_path, PATH_CYCLE, PATH_ENTER_MAIN)
    s = _one(got, "helper", "mod_a.load")
    assert s["classification"] == "covered(cli-base)", s
    assert len(s["via"]) == 1 and s["via"][0].startswith("a:"), s
    early = _one(got, "a", "mod_b.measure")
    assert early["classification"] == "candidate-escape", early
    assert early["uncovered"] == ["mod_b"], early


def test_an_inline_dispatch_table_under_the_try_is_followed(tmp_path):
    """The #1162/#1183 repairs (rs_guard_band, rs_mode_drive,
    balance_line_shape) dispatch `{"compare": cmd_compare, ...}[cmd]()` under
    the try that is the repair. Read as callbacks, the entries carry an unseen
    path and lose the credit the try gives them; the census would list the
    repaired modules as escapes again."""
    got, _ = _path_scan(tmp_path, PATH_TABLE_INLINE, PATH_TABLE_ENTER_INLINE)
    for scope, callee in (("cmd_compare", "mod_a.load"), ("cmd_validate", "mod_b.measure")):
        s = _one(got, scope, callee)
        assert s["classification"] == "covered(cli-base)", s
        assert s["guard"] == "none", s
        assert len(s["via"]) == 1 and s["via"][0].startswith("<module>:"), s


def test_a_table_read_only_for_its_keys_is_still_sealed(tmp_path):
    """measure_partial_balance's `COMMANDS`: subscripted and called under the
    try, and read twice more for its KEYS -- a membership test and
    `sep.join(COMMANDS)` in a usage message. Neither can reach an entry, so
    the table stays sealed. A seal rule that allows only the call and the
    membership test turns these rows into escapes."""
    got, _ = _path_scan(tmp_path, PATH_TABLE_BY_NAME, PATH_TABLE_ENTER_BY_NAME)
    s = _one(got, "cmd_measure", "mod_a.load")
    assert s["classification"] == "covered(cli-base)", s
    assert len(s["via"]) == 1 and s["via"][0].startswith("<module>:"), s


def test_a_table_that_escapes_is_not_sealed_though_it_is_called(tmp_path):
    """Rule 8 for the seal: the same table is subscripted and called under the
    try AND copied into a variable, through which the graph cannot follow
    it. The guarded call is real, but so is the unseen path, so the entry
    keeps no credit. A seal that looks only for the guarded call -- or that
    ignores reads other than the call -- turns this into covered(cli-base)."""
    got, _ = _path_scan(tmp_path, PATH_TABLE_ESCAPED, PATH_TABLE_ENTER_ESCAPED)
    s = _one(got, "cmd_measure", "mod_a.load")
    assert s["classification"] == "candidate-escape", s
    assert s["uncovered"] == ["mod_a"], s


def test_a_table_entry_stored_in_a_variable_is_not_followed(tmp_path):
    """A stated blind spot, pinned: `handler = COMMANDS[cmd]` parks the entry
    in a variable and the call `handler()` is not a bare-name call to a def.
    Guarded at runtime; the census lists it as an escape."""
    got, _ = _path_scan(tmp_path, PATH_TABLE_STORED, PATH_TABLE_ENTER_STORED)
    s = _one(got, "cmd_measure", "mod_a.load")
    assert s["classification"] == "candidate-escape", s


# The 14 rows of #1656's table on the real tree, keyed by file, the def the
# site is written in and the callee -- never by line, so an unrelated edit to
# one of these files does not red it (the module docstring's "NOT A GATE"). It
# moves only when one of these defs changes its callers or its guards, which
# is exactly when its label should be re-read. `callers` of these defs that
# the graph cannot see are the stated blind spots above.
REAL_TREE_ROWS = {
    "fpga/release/r2_release.py": {
        "image_identity": {"r1_release.image_identity": ["covered(cli-exact)"]},
        "build": {"r1_release.external_io": ["covered(cli-exact)"]}},
    "tools/capability_pack.py": {
        "gate": {"refprofile.require_capability": ["covered(cli-exact)"]}},
    "tools/cymbal_low_tail.py": {
        "candidate": {"run_case.prepare": ["covered(cli-base)"]},
        "synth": {"run_case.prepare": ["candidate-escape"]},
        "shipped": {"run_case.render_drum_solo": ["candidate-escape"],
                    "run_case.prepare": ["candidate-escape"]},
        # the 15th row is not in the table: the try in noise_floor_control
        # still covers ITS OWN prepare call, two lines below the two escapes
        "noise_floor_control": {"run_case.render_drum_solo": ["candidate-escape"],
                                "run_case.prepare": ["candidate-escape", "covered(base)"]}},
    "tools/lead_drive_337.py": {
        "vol_effective": {"mono_m5a_score.engine_configuration": ["candidate-escape"]}},
    "tools/r0_capture.py": {
        "resolve_image": {"r0_reference.load_manifest": ["candidate-escape"]},
        "load_reference": {"r0_reference.read_wav_int16": ["candidate-escape"]},
        "cross_image_controls": {"r0_reference.load_manifest": ["candidate-escape"] * 2}},
    # not in #1656's table: the site #1577 ACCEPTED, which the path rule covers
    "rtl-sketch/verify_synth_top.py": {
        "_render": {"run_case.render_drum_solo": ["covered(cli-exact)"]}},
}


@pytest.fixture(scope="module")
def _real_tree():
    """The census of the tracked tree, keyed (file, the def a site is written
    in, callee) -> its sites. The key is read off the AST here, not off a
    scanner field, so a row is found the same way under any scanner."""
    files = rss.tracked_files()
    idx = rss.build_index(files)
    scope = [f for f in files if any(str(f.relative_to(rss.ROOT)).startswith(r + "/")
                                     for r in rss.SCAN_ROOTS)]
    got: dict[tuple[str, str, str], list[dict]] = {}
    for s in rss.scan(scope, idx):
        path, line = s["site"].rsplit(":", 1)
        mod = idx[str(rss.ROOT / path)]
        call = next(n for n in ast.walk(mod.tree)
                    if isinstance(n, ast.Call) and n.lineno == int(line))
        cur = mod.parents.get(call)
        while cur is not None and not isinstance(cur, ast.FunctionDef):
            cur = mod.parents.get(cur)
        got.setdefault((path, cur.name, s["callee"]), []).append(s)
    return got


def test_the_real_trees_named_rows_read_as_the_tables_say(_real_tree):
    """What ships, not only the synthetic shapes: #1656's acceptance
    criteria 2-4 on the tracked tree -- the four defect-1 rows are covered,
    the four r0_capture rows have left `relabelled(cli-total)` for an escape,
    and the six other escaping sites are not covered under any rule added."""
    assert len(REAL_TREE_ROWS) == 6
    pinned = 0
    for path, defs in REAL_TREE_ROWS.items():
        for def_name, callees in defs.items():
            for callee, want in callees.items():
                have = sorted(s["classification"] for s in _real_tree[(path, def_name, callee)])
                assert have == sorted(want), (path, def_name, callee, have, want)
                pinned += len(want)
    assert pinned == 16, pinned


# The label against what the shipped CLI DOES. For each site below the REAL
# `main()` runs with the census site's callee stubbed to raise its own
# module's `Refused`: an escape row must come out of `main()` as that
# exception -- the traceback the CLI would print -- and a covered row must
# not. The assertion is label <-> behaviour, not a fixed label, so it
# survives a repair: when someone guards an escape row the census reads
# covered and the run reads caught, and the test still passes. At #1656 the
# first three were `covered(cli-base)`, `covered(cli-exact)` and
# `relabelled(cli-total)` by presence while their refusal escapes, and the
# fourth was an escape while it is caught. Nothing here is inferred from the
# scanner; `RUNTIME_STUBS` is the stipulated world, and it stipulates only
# the callees.
def _np_ones(*a, **k):
    import numpy
    return numpy.ones(4800) * 0.1, 48000


RUNTIME_STUBS = {
    ("tools/make_cymbal_pack.py", "main", "run_case.render_drum_solo"): dict(
        module="make_cymbal_pack", argv=lambda out: ["--out", str(out)],
        alongside={"cymbal_bands._load": _np_ones,                 # the corpus: present, not silent
                   "cymbal_candidate_eval.calibrate": (lambda: {"amps": {}}),
                   "cymbal_candidate_eval.kit_with_levels": (lambda amps: {}),
                   "cymbal_candidate.render": (lambda kit, sound: _np_ones())}),
    ("tools/clap_d12a_probe.py", "known_answer", "run_case.prepare"): dict(
        module="clap_d12a_probe",
        argv=lambda out: ["--known-answer-only", "--out", str(out / "p.json")], alongside={}),
    ("tools/r0_capture.py", "cross_image_controls", "r0_reference.load_manifest"): dict(
        module="r0_capture", argv=lambda out: ["cross-image-controls", "--out", str(out)],
        alongside={}),
    ("tools/capability_pack.py", "gate", "refprofile.require_capability"): dict(
        module="capability_pack", argv=lambda out: ["--gate"], alongside={}),
}


def _raising(stem: str):
    """A stand-in for the callee that raises ITS module's `Refused`."""
    cls = importlib.import_module(stem).Refused

    def refuse(*a, **k):
        raise cls(f"stubbed refusal from {stem}, for the census")
    return refuse


def _set(monkeypatch, target: str, value) -> None:
    mod, _, attr = target.rpartition(".")
    monkeypatch.setattr(importlib.import_module(mod), attr, value)


@pytest.mark.parametrize("key", sorted(RUNTIME_STUBS), ids=lambda k: f"{k[0].split('/')[-1]}:{k[1]}")
def test_the_label_and_the_shipped_cli_agree(tmp_path, monkeypatch, _real_tree, key):
    rows = _real_tree[key]
    assert rows, key
    polarities = {s["classification"] == "candidate-escape" for s in rows}
    assert len(polarities) == 1, [(s["site"], s["classification"]) for s in rows]
    for s in rows:                          # each site raises one family: its own callee's
        assert len(s["origins"]) == 1, s
        _set(monkeypatch, key[2], _raising(s["origins"][0]))
    for target, value in RUNTIME_STUBS[key]["alongside"].items():
        _set(monkeypatch, target, value)
    spec = RUNTIME_STUBS[key]
    try:
        importlib.import_module(spec["module"]).main(spec["argv"](tmp_path))
    except refusal.Refused:
        escaped = True
    else:
        escaped = False
    assert escaped == polarities.pop(), [(s["site"], s["classification"]) for s in rows]


# ---------------------------------------------------------------------------
# #1718 controls: an `async def` callee is in the census
# ---------------------------------------------------------------------------
# `ModInfo.funcs` registered `ast.FunctionDef` alone, so `raise_origins` found
# no def for an `async def` callee, read the call as raising nothing, and the
# site was ABSENT from the census -- not misclassified: the optimistic
# direction, the shape #1573 found for function-level imports.
# `cli_boundary_guards.in_boundary` matched `ast.FunctionDef` named `main` the
# same way, so a try in an `async def main()` guarded nothing. The real tree
# held 0 async production defs at 710d3f66, so every control here PLANTS its
# shape. The await rule is a claim about where PYTHON raises a coroutine's
# refusal, so each control runs the planted files first -- imported as real
# modules from the very files the census then reads -- and holds the census
# row to the interpreter's verdict.

ASYNC_CALLEE = '''
import refusal

class Refused(refusal.Refused, Exception):
    pass

CALLS = []                       # written when `load` RUNS: a 'caught' is never a silent callee

async def load(path):
    CALLS.append(path)
    raise Refused(f"unusable: {path}")

def load_sync(path):
    raise Refused(f"unusable: {path}")
'''

# the issue's caller.py, with the sync twin the census listed all along
ASYNC_CALLER = '''
import callee

async def go(p):
    return await callee.load(p)

def go_sync(p):
    return callee.load_sync(p)
'''

ASYNC_AWAITED_BASE_GUARD = '''
import refusal
import callee

async def go(p):
    try:
        return await callee.load(p)      # raised HERE, in the body: covered
    except refusal.Refused as e:
        return f"REFUSED  {e}"
'''

ASYNC_AWAITED_SIBLING_GUARD = '''
import refusal
import callee

class Refused(refusal.Refused, Exception):   # the caller's OWN class: a sibling
    pass

async def go(p):
    try:
        return await callee.load(p)
    except Refused as e:
        return f"REFUSED  {e}"
'''

ASYNC_STORED_THEN_AWAITED_AFTER = '''
import refusal
import callee

async def go(p):
    try:
        coro = callee.load(p)            # CREATED in the body: no callee code ran
    except refusal.Refused as e:
        return f"REFUSED  {e}"
    return await coro                    # RAISED here: past the try
'''

ASYNC_STORED_AND_AWAITED_INSIDE = '''
import refusal
import callee

async def go(p):
    try:
        coro = callee.load(p)
        return await coro                # awaited INSIDE the try: Python catches it
    except refusal.Refused as e:
        return f"REFUSED  {e}"
'''

SYNC_CALLER_STORES_COROUTINE = '''
import asyncio
import refusal
import callee

def go(p):                               # a sync caller can only CREATE it
    try:
        coro = callee.load(p)
    except refusal.Refused as e:
        return f"REFUSED  {e}"
    return asyncio.run(coro)             # and drive it after the try
'''

ASYNC_MAIN_BOUNDARY = '''
import asyncio
import sys
import refusal
import callee

async def go(p):
    return await callee.load(p)          # unguarded HERE; caught at the boundary

async def main(p):
    try:
        return await go(p)
    except refusal.Refused as e:
        return f"REFUSED  {e}"

if __name__ == "__main__":
    sys.exit(asyncio.run(main("x")))
'''

# The same boundary, but the dispatch does NOT call `main` by bare name, so the
# #1641 wrapper branch of `in_boundary` cannot credit it: only the `main`
# branch can. After #1641 landed, ASYNC_MAIN_BOUNDARY alone no longer isolates
# that branch (main is a bare-name dispatch callee there, hence a wrapper too).
ASYNC_MAIN_BOUNDARY_NOT_BARE = ASYNC_MAIN_BOUNDARY.replace(
    'asyncio.run(main("x"))', 'asyncio.run(globals()["main"]("x"))')

ASYNC_MAIN_STORED_THEN_AWAITED_AFTER = '''
import asyncio
import sys
import refusal
import callee

async def main(p):
    try:
        coro = callee.load(p)            # CREATED inside the boundary try...
    except refusal.Refused as e:
        return f"REFUSED  {e}"
    return await coro                    # ...and RAISED after it: past the guard

if __name__ == "__main__":
    sys.exit(asyncio.run(main("x")))
'''

ASYNC_NON_MAIN_GUARD = '''
import refusal
import callee

async def go(p):
    return await callee.load(p)

async def run(p):                        # the same try in any other async def
    try:
        return await go(p)
    except refusal.Refused as e:
        return f"REFUSED  {e}"
'''

PLAY_ASYNC_RAISER = '''
import refusal

class Refused(refusal.Refused, Exception):
    pass

async def fetch(p):
    raise Refused("no records")
'''

CALLER_AMBIG_ASYNC_AWAITED = '''
import refusal
from play import fetch

async def go(p):
    try:
        return await fetch(p)
    except refusal.Refused:
        return None
'''

CALLER_AMBIG_ASYNC_STORED = '''
import refusal
from play import fetch

async def go(p):
    try:
        coro = fetch(p)
    except refusal.Refused:
        return None
    return await coro
'''


def _async_files(tmp_path, extra: dict[str, str]) -> list[pathlib.Path]:
    """The issue's two-file tree plus one caller per entry: `callee.py`,
    whose `load` is an `async def` that refuses, and the given callers."""
    p = tmp_path / "callee.py"
    p.write_text(ASYNC_CALLEE)
    files = [p]
    for name, body in extra.items():
        q = tmp_path / f"{name}.py"
        q.write_text(body)
        files.append(q)
    return files


def _async_tree(tmp_path, extra: dict[str, str]):
    files = _async_files(tmp_path, extra)
    return files, rss.build_index(files)


def _import(monkeypatch, path: pathlib.Path):
    """`path` imported as a real module under its own stem, for this test."""
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    spec = importlib.util.spec_from_file_location(path.stem, path)
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, path.stem, mod)
    spec.loader.exec_module(mod)
    return mod


def _coroutine_verdict(monkeypatch, tmp_path: pathlib.Path, caller: str,
                       fn: str = "go") -> str:
    """What the INTERPRETER does with a planted caller: `fn` is run against
    the callee that always refuses. 'caught' when it returns after the
    callee RAISED, 'escaped' when the refusal leaves it, 'not-run' when the
    callee was never reached -- a defect in the planting, not a verdict."""
    callee = _import(monkeypatch, tmp_path / "callee.py")
    mod = _import(monkeypatch, tmp_path / f"{caller}.py")
    f = getattr(mod, fn)
    try:
        if asyncio.iscoroutinefunction(f):
            asyncio.run(f("x"))
        else:
            f("x")
    except callee.Refused:
        verdict = "escaped"
    else:
        verdict = "caught"
    return verdict if callee.CALLS else "not-run"


def _scanner_with(old: str, new: str):
    """The shipped scanner with ONE fix reverted, compiled in memory from
    its own source. `old` must be there exactly once or the mutant is
    REFUSED: an injection point that moved is drift to surface, never a
    mutant that changed nothing and passed."""
    path = ROOT / "tools" / "refusal_sibling_scan.py"
    src = path.read_text()
    assert src.count(old) == 1, (src.count(old), old)
    m = types.ModuleType("rss_mutant")
    m.__file__ = str(path)
    exec(compile(src.replace(old, new), str(path), "exec"), m.__dict__)  # noqa: S102
    return m


def test_a_call_to_an_async_def_callee_is_a_site(tmp_path, monkeypatch):
    """The issue's two-file tree, red first: `callee.funcs` held the sync
    twin alone, so `callee.load_sync` was reported and `callee.load` -- the
    call Python raises out of `go` -- was in no census. The module-level
    assertions keep the control honest about which fix passes it: the async
    def is REGISTERED, as itself, not read through some other path."""
    files, idx = _async_tree(tmp_path, {"caller_async": ASYNC_CALLER})
    funcs = idx[str(tmp_path / "callee.py")].funcs
    assert "load" in funcs and isinstance(funcs["load"], ast.AsyncFunctionDef), sorted(funcs)
    assert isinstance(funcs["load_sync"], ast.FunctionDef), sorted(funcs)
    assert _coroutine_verdict(monkeypatch, tmp_path, "caller_async") == "escaped"
    got = {s["callee"]: s for s in _sites_for(files, idx, "caller_async")}
    assert sorted(got) == ["callee.load", "callee.load_sync"], got
    for s in got.values():
        assert (s["origins"], s["classification"], s["guard"], s["uncovered"]) == (
            ["callee"], "candidate-escape", "none", ["callee"]), s


def test_a_try_in_an_async_main_is_the_cli_boundary(tmp_path):
    """A module whose `main` is an `async def` had no boundary in the census
    (#1718): a site that module's `main()` catches was reported a candidate
    escape, the repair shape of #1160/#1162 read as still open. Since #1656
    the credit is by path, so the control is the row: the site in `go`, under
    `await go(p)` in main's try, is `covered(cli-base)`. The second assertion
    is the control's other side: the same try in an async def the entry never
    reaches credits nothing, as for a def."""
    files = _async_files(tmp_path, {"caller_async_main": ASYNC_MAIN_BOUNDARY,
                                    "caller_async_run": ASYNC_NON_MAIN_GUARD})
    idx = rss.build_index(files)
    got = _sites_for(files, idx, "caller_async_main")
    assert [(s["classification"], s["guard"]) for s in got] == [("covered(cli-base)", "none")], got
    got = _sites_for(files, idx, "caller_async_run")
    assert [(s["classification"], s["guard"]) for s in got] == [("candidate-escape", "none")], got


@pytest.mark.parametrize("src,fn,python,row", [
    pytest.param(ASYNC_AWAITED_BASE_GUARD, "go", "caught",
                 ("covered(base)", "base", []), id="awaited-under-a-base-guard"),
    pytest.param(ASYNC_AWAITED_SIBLING_GUARD, "go", "escaped",
                 ("candidate-escape", "local", ["callee"]), id="awaited-under-a-sibling"),
    pytest.param(ASYNC_STORED_THEN_AWAITED_AFTER, "go", "escaped",
                 ("candidate-escape", "none", ["callee"]), id="created-then-awaited-after"),
    pytest.param(ASYNC_STORED_AND_AWAITED_INSIDE, "go", "caught",
                 ("candidate-escape", "none", ["callee"]), id="created-and-awaited-inside"),
    pytest.param(SYNC_CALLER_STORES_COROUTINE, "go", "escaped",
                 ("candidate-escape", "none", ["callee"]), id="sync-caller-drives-it-after"),
    pytest.param(ASYNC_MAIN_BOUNDARY, "main", "caught",
                 ("covered(cli-base)", "none", []), id="async-main-boundary"),
    pytest.param(ASYNC_MAIN_STORED_THEN_AWAITED_AFTER, "main", "escaped",
                 ("candidate-escape", "none", ["callee"]), id="async-main-created-then-awaited"),
    pytest.param(ASYNC_NON_MAIN_GUARD, "run", "caught",
                 ("candidate-escape", "none", ["callee"]), id="async-def-not-named-main"),
])
def test_the_census_row_of_an_async_callee_follows_the_interpreter(
        tmp_path, monkeypatch, src, fn, python, row):
    """The await rule, held to what Python does. A coroutine is only
    CREATED by the call and raises where it is AWAITED: the three
    created-then-awaited rows raise past a try whose body holds the Call
    node, so a walk that credits the Call node's own try reports a false
    covered there. The rule credits a call to an `async def` -- by the tries
    around it and by the CLI boundary -- only when it is written
    `await <call>`. One row is the rule's own stated cost: a coroutine
    awaited INSIDE the try that created it is 'caught' by Python and
    'candidate-escape' by the census, because where the coroutine is awaited
    is not in the AST at the Call node. The last row is no cost of the rule:
    Python catches that refusal in an intermediate frame, a blind spot the
    module docstring already lists; it is here as the other side of the
    boundary fix, the same try in an async def not named main."""
    files, idx = _async_tree(tmp_path, {"caller_async": src})
    assert _coroutine_verdict(monkeypatch, tmp_path, "caller_async", fn) == python, src
    got = _sites_for(files, idx, "caller_async")
    assert len(got) == 1, got
    assert (got[0]["classification"], got[0]["guard"], got[0]["uncovered"]) == row, got


@pytest.mark.parametrize("caller,row", [
    pytest.param(CALLER_AMBIG_ASYNC_AWAITED, ("covered(base)", "base"),
                 id="awaited-under-a-base-guard"),
    pytest.param(CALLER_AMBIG_ASYNC_STORED, ("ambiguous-callee", "none"),
                 id="created-then-awaited-after"),
])
def test_the_await_rule_reaches_the_stem_collision_branch(tmp_path, caller, row):
    """The collision branch reads the tries around the call on its own, to
    settle a base or broad guard the same way for every same-stem candidate;
    a bare call to a callee that MAY be an `async def` must settle nothing
    there either. The interpreter's verdict is not re-run here: which module
    the import binds is the undecidable fact the branch exists for."""
    files = _collision_tree(tmp_path, PLAY_ASYNC_RAISER, PLAIN, "caller_amb_async", caller)
    idx = rss.build_index(files)
    got = _sites_for(files, idx, "caller_amb_async")
    assert len(got) == 1, got
    assert (got[0]["classification"], got[0]["guard"]) == row, got
    assert len(got[0]["candidates"]) == 2, got


def test_with_the_registration_reverted_the_async_site_is_absent_again(tmp_path):
    """Mutant 1 of 3: `funcs` back to `ast.FunctionDef` alone. The issue's
    own measurement, re-derived on the shipped source -- `callee.funcs ==
    ['load_sync']`, the sync twin a site, the async site gone -- so the
    first control is red under it and green only through the registration."""
    ind = " " * 16                    # the registration line's own indentation
    mut = _scanner_with(
        f"if isinstance(node, FUNCTION_NODES):\n{ind}self.funcs.setdefault(node.name, node)",
        f"if isinstance(node, ast.FunctionDef):\n{ind}self.funcs.setdefault(node.name, node)")
    files = _async_files(tmp_path, {"caller_async": ASYNC_CALLER})
    idx = mut.build_index(files)
    # the revert is ACTIVE: the async def is unregistered again
    assert sorted(idx[str(tmp_path / "callee.py")].funcs) == ["load_sync"]
    got = sorted(s["callee"] for s in mut.scan(files, idx))
    # the control's claim is false: `callee.load` is gone, the twin remains
    assert got == ["callee.load_sync"], got


def test_with_the_async_resolution_reverted_an_async_main_is_no_boundary(tmp_path):
    """Mutant 2 of 3: name resolution back to `ast.FunctionDef` alone, so
    `asyncio.run(main("x"))` reaches no def. `async def main` is unreached,
    the try in it is on no path, and the site it catches is a candidate
    escape again -- the control above red under this revert."""
    mut = _scanner_with(
        "        return [d for d in own if isinstance(d, FUNCTION_NODES)]",
        "        return [d for d in own if isinstance(d, ast.FunctionDef)]")
    files = _async_files(tmp_path, {"caller_async": ASYNC_MAIN_BOUNDARY})
    idx = mut.build_index(files)
    # the shipped scanner credits it: the revert below is what removes that
    ship = rss.build_index(files)
    shipped = [s for s in rss.scan(files, ship) if "caller_async.py" in s["site"]]
    assert [s["classification"] for s in shipped] == ["covered(cli-base)"], shipped
    got = [s for s in mut.scan(files, idx) if "caller_async.py" in s["site"]]
    assert [(s["classification"], s["guard"]) for s in got] == [("candidate-escape", "none")], got


def test_with_the_await_rule_reverted_a_bare_call_borrows_the_trys_handler(tmp_path):
    """Mutant 3 of 3: `refusal_runs_at_call` answering True for every call,
    the census with the two registrations fixed and no await rule. The Call
    node sits in the try body, so the walk lends it the handler, and a
    refusal Python raises after the try reads `covered(base)` -- the false
    covered the rule exists to withhold."""
    mut = _scanner_with(
        "    return not any(isinstance(d, ast.AsyncFunctionDef) for d in defs)",
        "    return True")
    files = _async_files(tmp_path, {"caller_async": ASYNC_STORED_THEN_AWAITED_AFTER})
    idx = mut.build_index(files)
    got = [s for s in mut.scan(files, idx) if "caller_async.py" in s["site"]]
    assert [(s["classification"], s["guard"]) for s in got] == [("covered(base)", "base")], got


# --- #1641 controls: the CLI boundary reached through a wrapper ---------------
# `hh_probe5`'s first repair (#1611, `513b0213`) put `try/except
# refusal.Refused` in a `_cli()` wrapper that the `__main__` block calls.
# `in_boundary` recognised a boundary try only directly in `main()` or under
# the dispatch, so the census still listed both repaired sites as escapes
# while every exit-status control was green; they cleared (85 -> 83) only once
# the try moved into `__main__`. The boundary now also credits a try directly
# in a module-level function that the DISPATCH calls by bare name, ONE level
# only. The two positive controls started red against the unwidened scanner.
# The negative controls are green before and after the widening: each pins an
# input that would defeat it (verification-rules rule 8), so a scanner that
# credited any module-level try turns them red. One of them is the half of
# the issue's acceptance criterion the tree falsified: a wrapper `main()`
# calls was tried as boundary and flipped ten escaping sites to `covered`
# (the row-by-row table is #1656; the pin is
# `test_a_wrapper_main_calls_is_not_the_boundary`).

WRAPPER_CLI_HEAD = '''
import sys
import refusal
import mod_a

def go(p):
    return mod_a.load(p)           # unguarded HERE; the guard, if any, is below
'''

WRAPPER_BASE_GUARD = '''
def _cli(argv=None):
    try:
        return go(argv)
    except refusal.Refused as e:
        print(f"REFUSED  {e}")
        return 2
'''


def _wrapper_cli(wrapper: str, dispatch: str) -> str:
    """A CLI whose `go()` site is unguarded where it is written: `wrapper`
    is the module-level def(s) between the site and the `__main__` block,
    `dispatch` that block's statements (already indented)."""
    return f'{WRAPPER_CLI_HEAD}\n{wrapper}\n\nif __name__ == "__main__":\n{dispatch}'


def _wrapper_scan(tmp_path, wrapper: str, dispatch: str) -> dict:
    """The one `mod_a.load` site of a wrapper-shaped CLI: a second site there
    would be a fixture mistake, so it is refused rather than indexed."""
    files = _tree(tmp_path)
    p = tmp_path / "caller_wrapped.py"
    p.write_text(_wrapper_cli(wrapper, dispatch))
    files.append(p)
    idx = rss.build_index(files)
    got = [s for s in _sites_for(files, idx, "caller_wrapped") if s["callee"] == "mod_a.load"]
    assert len(got) == 1, got
    return got[0]


def test_base_guard_in_a_wrapper_the_dispatch_calls_is_cli_base(tmp_path):
    """The `hh_probe5` shape: unguarded at the call site, `except
    refusal.Refused` in a `_cli()` the `__main__` block calls through
    `sys.exit` -- red against a scanner that reads `main()` and the dispatch
    only."""
    s = _wrapper_scan(tmp_path, WRAPPER_BASE_GUARD, "    sys.exit(_cli())\n")
    assert s["classification"] == "covered(cli-base)", s
    assert s["guard"] == "none", s


def test_wrapper_called_by_a_bare_statement_is_cli_base(tmp_path):
    s = _wrapper_scan(tmp_path, WRAPPER_BASE_GUARD, "    _cli()\n")
    assert s["classification"] == "covered(cli-base)", s


def test_a_wrapper_main_calls_is_covered_by_path_not_presence(tmp_path):
    """SUPERSEDED by #1656: #1641 refused to credit a wrapper `main()` calls,
    because the by-presence boundary then covered every site in the module
    (ten escapes read `covered`). Credit is by PATH now: `main -> _cli -> go`
    runs through the wrapper's try on every path, so this site IS guarded.
    The sibling-helper negative that motivated the refusal stays pinned by
    the #1656 section."""
    s = _wrapper_scan(tmp_path, WRAPPER_BASE_GUARD + '''
def main(argv=None):
    return _cli(argv)
''', "    sys.exit(main())\n")
    assert s["classification"] == "covered(cli-base)", s


def test_uncalled_wrapper_leaves_the_site_an_escape(tmp_path):
    """Rule 8: a wrapper with a base guard that nothing reaches guards
    nothing. The dispatch calls `go()` directly; `_cli` is defined, never
    called."""
    s = _wrapper_scan(tmp_path, WRAPPER_BASE_GUARD, "    sys.exit(go())\n")
    assert s["classification"] == "candidate-escape", s
    assert s["uncovered"] == ["mod_a"], s


def test_wrapper_with_a_sibling_handler_leaves_the_site_an_escape(tmp_path):
    """Rule 8: the wrapper is reached, but its handler is the caller's OWN
    class -- a sibling of mod_a's, which it cannot catch."""
    s = _wrapper_scan(tmp_path, '''
class Refused(refusal.Refused, Exception):
    pass

def _cli(argv=None):
    try:
        return go(argv)
    except Refused as e:
        print(f"REFUSED  {e}")
        return 2
''', "    sys.exit(_cli())\n")
    assert s["classification"] == "candidate-escape", s
    assert s["uncovered"] == ["mod_a"], s


def test_a_try_one_frame_further_in_is_covered_by_path(tmp_path):
    """SUPERSEDED by #1656 ("one level only" is gone): `_cli` is reached and
    ITS callee has the try, which every path to the site runs through."""
    s = _wrapper_scan(tmp_path, '''
def _inner(argv):
    try:
        return go(argv)
    except refusal.Refused as e:
        print(f"REFUSED  {e}")
        return 2

def _cli(argv=None):
    return _inner(argv)
''', "    sys.exit(_cli())\n")
    assert s["classification"] == "covered(cli-base)", s


def test_a_call_written_in_a_def_nested_in_the_dispatch_is_covered_by_path(tmp_path):
    """SUPERSEDED by #1656: the dispatch defines `inner` and calls it, and
    `inner` calls the guarded wrapper -- every path to the site runs through
    the wrapper's try, so it is covered (see PATH_ENTER_THROUGH_A_DEF...)."""
    s = _wrapper_scan(tmp_path, WRAPPER_BASE_GUARD, '''
    def inner():
        return _cli()
    sys.exit(inner())
''')
    assert s["classification"] == "covered(cli-base)", s


def test_wrapper_called_only_from_a_handler_is_not_entry(tmp_path):
    """Rule 8: the dispatch reaches the wrapper from an `except` clause, so it
    runs after the site's work is over and catches nothing on the way out."""
    s = _wrapper_scan(tmp_path, WRAPPER_BASE_GUARD, '''
    try:
        sys.exit(go())
    except OSError:
        sys.exit(_cli())
''')
    assert s["classification"] == "candidate-escape", s


def test_helper_with_an_unrelated_try_changes_nothing(tmp_path):
    """A dispatch-called helper whose try is not about refusals is on the
    boundary's call list; its `except OSError` is shape `other`, and the
    unguarded site stays what it was."""
    s = _wrapper_scan(tmp_path, '''
def helper():
    try:
        return open("x")
    except OSError:
        return None
''', '''
    helper()
    sys.exit(go())
''')
    assert s["classification"] == "candidate-escape", s
    assert s["uncovered"] == ["mod_a"], s


# --- #1688 review, round 2: a DEFERRED call inherits no handler ----------------
# A call that only CREATES a generator, a coroutine or a generator expression
# runs its body later, under whatever is around the code that drives it. The
# path graph gave such an edge no handlers of its own and then let it inherit
# its CREATOR's outcome, so a try around `factory()` was credited for a
# refusal Python raises after that try exited. Each deferred fixture below was
# run under Python 3.12 against the scanner at 18807d7a: the scanner said
# `covered(cli-base)`, the interpreter exited 1 with an uncaught
# `callee.Refused`. The eager control is the same tree with the call made on
# the spot: Python catches it, and the scanner must keep crediting it.

DEFERRED_CALLEE = '''
import refusal

class Refused(refusal.Refused, Exception):
    pass

CALLS = []

def load():
    CALLS.append(1)
    raise Refused("factory control")
'''

_DEFERRED_MAIN = '''
def main():
    try:
        pending = factory()
    except refusal.Refused:
        print("caught")
        return 2
    return {drive}

if __name__ == "__main__":
    sys.exit(main())
'''

DEFERRED_GENERATOR = '''
import sys, refusal, callee
def helper():
    yield callee.load()

def factory():
    return helper()
''' + _DEFERRED_MAIN.format(drive="next(pending)")

DEFERRED_COROUTINE = '''
import sys, asyncio, refusal, callee
async def helper():
    return callee.load()

def factory():
    return helper()
''' + _DEFERRED_MAIN.format(drive="asyncio.run(pending)")

DEFERRED_GENEXP = '''
import sys, refusal, callee
def helper():
    return callee.load()

def factory():
    return (helper() for _ in (0,))
''' + _DEFERRED_MAIN.format(drive="next(pending)")

EAGER_CONTROL = '''
import sys, refusal, callee
def helper():
    return callee.load()

def factory():
    return helper()
''' + _DEFERRED_MAIN.format(drive="pending")


def _deferred_tree(tmp_path, body: str):
    (tmp_path / "callee.py").write_text(DEFERRED_CALLEE)
    (tmp_path / "caller_d.py").write_text(body)
    files = [tmp_path / "callee.py", tmp_path / "caller_d.py"]
    return files, rss.build_index(files)


def _deferred_python_verdict(monkeypatch, tmp_path) -> str:
    """'escaped' / 'caught' from RUNNING the planted caller; 'not-run' when the
    callee was never reached (a defect in the planting, not a verdict)."""
    callee = _import(monkeypatch, tmp_path / "callee.py")
    mod = _import(monkeypatch, tmp_path / "caller_d.py")
    try:
        mod.main()
    except callee.Refused:
        verdict = "escaped"
    else:
        verdict = "caught"
    return verdict if callee.CALLS else "not-run"


@pytest.mark.parametrize("body,python,classification", [
    pytest.param(DEFERRED_GENERATOR, "escaped", "candidate-escape", id="generator"),
    pytest.param(DEFERRED_COROUTINE, "escaped", "candidate-escape", id="coroutine"),
    pytest.param(DEFERRED_GENEXP, "escaped", "candidate-escape", id="genexp"),
    pytest.param(EAGER_CONTROL, "caught", "covered(cli-base)", id="eager-control"),
])
def test_a_deferred_call_is_not_credited_the_creators_handler(
        tmp_path, monkeypatch, body, python, classification):
    files, idx = _deferred_tree(tmp_path, body)
    assert _deferred_python_verdict(monkeypatch, tmp_path) == python
    got = [s for s in rss.scan(files, idx) if "caller_d.py" in s["site"]]
    assert [s["classification"] for s in got] == [classification], got


@pytest.mark.parametrize("body", [DEFERRED_GENERATOR, DEFERRED_COROUTINE, DEFERRED_GENEXP])
def test_with_the_deferral_reverted_the_creators_handler_is_credited_again(tmp_path, body):
    """Injected defect: a deferred edge inherits its caller's outcome again
    (the empty-edge fallback). Each deferred fixture then reads
    `covered(cli-base)` -- the false covered the three controls above refuse."""
    mut = _scanner_with(
        '                    if deferred:\n'
        '                        new.add(("none", None))\n'
        '                        continue\n', '')
    files, idx = _deferred_tree(tmp_path, body)
    midx = mut.build_index(files)
    got = [s for s in mut.scan(files, midx) if "caller_d.py" in s["site"]]
    assert [s["classification"] for s in got] == ["covered(cli-base)"], got


# --- #1688 review, round 3: deferral and definition time at the FINAL call ----
# Round 2 deferred the edges to LOCAL defs; the cross-module call site itself
# still inherited the factory's handler. Each fixture was run under Python
# 3.12 and the scanner at 56a744b2 read `covered(cli-base)` for every one that
# escapes. A def's defaults, annotations and decorators run in the frame that
# holds the def statement, not in the def.

_R3_MAIN = '''
def make_pending():
    try:
        return factory()
    except refusal.Refused:
        sys.exit(2)

def main():
    return {drive}

if __name__ == "__main__":
    sys.exit(main())
'''

R3_GENEXP_DIRECT = '''
import sys, refusal, callee
def factory():
    return (callee.load() for _ in (0,))
''' + _R3_MAIN.format(drive="next(make_pending())")

R3_GENEXP_FIRST_ITERABLE = '''
import sys, refusal, callee
def factory():
    return (x for x in callee.load())
''' + _R3_MAIN.format(drive="next(make_pending())")

R3_IMPORTED_GENERATOR_ATTR = '''
import sys, refusal, callee
def factory():
    return callee.gen()
''' + _R3_MAIN.format(drive="next(make_pending())")

R3_IMPORTED_GENERATOR_FROM = '''
import sys, refusal
from callee import gen
def factory():
    return gen()
''' + _R3_MAIN.format(drive="next(make_pending())")

R3_EAGER_LISTCOMP = '''
import sys, refusal, callee
def factory():
    return [callee.load() for _ in (0,)]
''' + _R3_MAIN.format(drive="make_pending()")

R3_EAGER_CALL = '''
import sys, refusal, callee
def factory():
    return callee.load()
''' + _R3_MAIN.format(drive="make_pending()")

R3_CALLEE = DEFERRED_CALLEE + '''
def gen():
    CALLS.append(1)
    raise Refused("generator control")
    yield
'''


def _r3_scan_and_run(monkeypatch, tmp_path, body, name="caller_r3"):
    (tmp_path / "callee.py").write_text(R3_CALLEE)
    (tmp_path / f"{name}.py").write_text(body)
    files = [tmp_path / "callee.py", tmp_path / f"{name}.py"]
    callee = _import(monkeypatch, tmp_path / "callee.py")
    try:
        mod = _import(monkeypatch, tmp_path / f"{name}.py")
        mod.main()
        verdict = "caught"
    except SystemExit:
        verdict = "caught"
    except callee.Refused:
        verdict = "escaped"
    if not callee.CALLS:
        verdict = "not-run"
    return files, verdict


def _r3_classes(files, scanner=rss, name="caller_r3"):
    idx = scanner.build_index(files)
    return [s["classification"] for s in scanner.scan(files, idx) if name in s["site"]]


@pytest.mark.parametrize("body,python,classification", [
    pytest.param(R3_GENEXP_DIRECT, "escaped", "candidate-escape", id="genexp-direct"),
    pytest.param(R3_GENEXP_FIRST_ITERABLE, "caught", "covered(cli-base)",
                 id="genexp-first-iterable-is-eager"),
    pytest.param(R3_IMPORTED_GENERATOR_ATTR, "escaped", "candidate-escape",
                 id="imported-generator-attr"),
    pytest.param(R3_IMPORTED_GENERATOR_FROM, "escaped", "candidate-escape",
                 id="imported-generator-from"),
    pytest.param(R3_EAGER_LISTCOMP, "caught", "covered(cli-base)", id="eager-listcomp"),
    pytest.param(R3_EAGER_CALL, "caught", "covered(cli-base)", id="eager-call"),
])
def test_a_deferred_final_call_is_not_credited_the_factorys_handler(
        monkeypatch, tmp_path, body, python, classification):
    files, verdict = _r3_scan_and_run(monkeypatch, tmp_path, body)
    assert verdict == python
    assert _r3_classes(files) == [classification]


R3_DEFAULT = '''
import sys, refusal, callee
def make_pending():
    try:
        def factory(x=callee.load()):
            return x
        return factory
    except refusal.Refused:
        sys.exit(2)

def main():
    return make_pending()
'''

R3_DEF_TIME = '''
import sys, refusal, callee
{decl}
def make_pending():
    try:
        return factory()
    except refusal.Refused:
        sys.exit(2)

def main():
    return make_pending()

if __name__ == "__main__":
    sys.exit(main())
'''


@pytest.mark.parametrize("decl", [
    pytest.param("def factory(x=callee.load()):\n    return x", id="default"),
    pytest.param("def factory(x: callee.load() = 0):\n    return x", id="annotation"),
    pytest.param("def factory() -> callee.load():\n    return 0", id="return-annotation"),
    pytest.param("@callee.load()\ndef factory():\n    return 0", id="decorator"),
])
def test_a_definition_time_call_runs_in_the_frame_that_holds_the_def(
        monkeypatch, tmp_path, decl):
    """Python raises at module initialisation, before `main` starts, so the
    guarded `factory()` call below it guards nothing."""
    (tmp_path / "callee.py").write_text(R3_CALLEE)
    (tmp_path / "caller_r3.py").write_text(R3_DEF_TIME.format(decl=decl))
    files = [tmp_path / "callee.py", tmp_path / "caller_r3.py"]
    callee = _import(monkeypatch, tmp_path / "callee.py")
    with pytest.raises(callee.Refused):
        _import(monkeypatch, tmp_path / "caller_r3.py")
    assert _r3_classes(files) == ["candidate-escape"]


def test_a_definition_time_call_inside_a_guarded_try_is_still_covered(monkeypatch, tmp_path):
    """Control: the def statement itself sits in the try, so Python catches the
    default's refusal there. The frame moves out; the try still counts."""
    files, verdict = _r3_scan_and_run(monkeypatch, tmp_path, R3_DEFAULT)
    assert verdict == "caught"
    assert _r3_classes(files) == ["covered(base)"]


def test_with_the_final_call_deferral_reverted_the_factorys_handler_is_credited(
        monkeypatch, tmp_path):
    mut = _scanner_with(
        "    if _handlers_within(mod, mod.caller_of(call), call)[1]:\n        return False\n"
        "    defs = [c.funcs.get(fname) for c in callees]\n"
        "    if any(isinstance(d, FUNCTION_NODES) and _is_generator(d) for d in defs):\n"
        "        return False\n", "    defs = [c.funcs.get(fname) for c in callees]\n")
    for body in (R3_GENEXP_DIRECT, R3_IMPORTED_GENERATOR_ATTR):
        files, _ = _r3_scan_and_run(monkeypatch, tmp_path, body)
        assert _r3_classes(files, mut) == ["covered(cli-base)"]


def test_with_definition_time_attribution_reverted_the_guarded_caller_is_credited(
        monkeypatch, tmp_path):
    mut = _scanner_with(
        "        cur, prev = self.parents.get(node), node\n"
        "        while cur is not None and not (isinstance(cur, CALLER_NODES)\n"
        "                                       and not self._at_definition(cur, prev)):\n",
        "        cur, prev = self.parents.get(node), node\n"
        "        while cur is not None and not isinstance(cur, CALLER_NODES):\n")
    decl = "def factory(x=callee.load()):\n    return x"
    (tmp_path / "callee.py").write_text(R3_CALLEE)
    (tmp_path / "caller_r3.py").write_text(R3_DEF_TIME.format(decl=decl))
    files = [tmp_path / "callee.py", tmp_path / "caller_r3.py"]
    assert _r3_classes(files, mut) == ["covered(cli-base)"]


# ---------------------------------------------------------------------------
# #1956 controls: a SUBCLASS of the family, raised under its own name
# ---------------------------------------------------------------------------
# `raise_origins` counted a raise only when its expression was a family NAME
# or ended in `.Refused`. A def whose raises were all of a module-local
# subclass of the module's own `Refused` -- `raise Unbindable(...)` after
# `class Unbindable(Refused)` -- read as raising nothing, and its callers left
# the census: the optimistic direction, the shape #1573 found for
# function-level imports. #1843's `f1_level_capture.load_capture` raised only
# such subclasses and its one caller, guarded by `except refusal.Refused` at
# the CLI boundary, vanished from the inventory (397 -> 396 sites). At
# dd0449e0 the real tree holds three such defs
# (`tone_stage_schematic.verify_source` and `._tolerance_from_sens`,
# `cymbal_vca_drive.verify_source`) and no cross-module caller of any of
# them, so every control here plants its shape and RUNS it: each planted file
# is imported as a real module from the very file the census then reads, and
# the census row is held to what the interpreter does with the refusal.
#
# Rule 8's guard-defeating input is the fix's own cheap imitation: a fix that
# counted ANY local class would pass every positive control here. The
# same-name class on a non-family base, raised the same way, must stay out.

SUBCLASS_CALLEE = '''
import refusal

class Refused(refusal.Refused, Exception):
    pass

class Unbindable(Refused):                 # a SUBCLASS of the module's own family
    pass

RAISED = []              # written when a def RUNS: a 'caught' is never a silent callee

def load_capture(p):
    RAISED.append(p)
    raise Unbindable(f"cannot bind {p}")   # the def's ONLY raise: of the subclass

def plain(p):
    RAISED.append(p)
    raise Refused("plain")

def quiet(p):
    return p
'''

NON_FAMILY_CALLEE = '''
import refusal

class Refused(refusal.Refused, Exception):
    pass

class Unbindable(Exception):               # the SAME name, NOT of the family
    pass

RAISED = []

def load_capture(p):
    RAISED.append(p)
    raise Unbindable(f"cannot bind {p}")   # the same raise shape, no refusal in it

def plain(p):
    RAISED.append(p)
    raise Refused("plain")
'''

CHAIN_CALLEE = '''
import refusal

class Refused(refusal.Refused, Exception):
    pass

class A(Refused):                          # one level under the family
    pass

class B(A):                                # two levels: a refusal all the same
    pass

RAISED = []

def load_capture(p):
    RAISED.append(p)
    raise B(f"cannot bind {p}")

def one_level(p):
    RAISED.append(p)
    raise A("one level")
'''

# the family BORROWED by from-import, through the #1214 re-exporter
BORROWED_SUBCLASS_CALLEE = '''
from relay import Refused                   # re-exported: the class IS mod_a's

class Unbindable(Refused):
    pass

RAISED = []

def load_capture(p):
    RAISED.append(p)
    raise Unbindable(f"cannot bind {p}")
'''

ALIASED_BORROWED_SUBCLASS_CALLEE = '''
from relay import Refused as R              # the same borrow, under another name

class Unbindable(R):
    pass

RAISED = []

def load_capture(p):
    RAISED.append(p)
    raise Unbindable(f"cannot bind {p}")
'''

# the subclass's own name, rebound by a LATER module-level import
FOREIGN_CLASS = '''
class Unbindable(Exception):               # the same name, of no family
    pass
'''

SHADOWED_SUBCLASS_CALLEE = '''
import refusal
from foreign import Unbindable               # BEFORE the class: the class wins

class Refused(refusal.Refused, Exception):
    pass

class Unbindable(Refused):
    pass

from foreign import Unbindable               # AFTER the class: the import wins

RAISED = []

def load_capture(p):
    RAISED.append(p)
    raise Unbindable(f"cannot bind {p}")
'''

FAMILY_SHADOWED_SUBCLASS_CALLEE = '''
import refusal
from mod_a import Refused as Unbindable      # BEFORE the class: the class wins

class Refused(refusal.Refused, Exception):
    pass

class Unbindable(Refused):
    pass

from mod_a import Refused as Unbindable      # AFTER the class: mod_a's class is the name now

RAISED = []

def load_capture(p):
    RAISED.append(p)
    raise Unbindable(f"cannot bind {p}")
'''

# the name is the subclass again in every def but the one that rebinds it
LOCAL_SHADOW_CALLEE = '''
import refusal

class Refused(refusal.Refused, Exception):
    pass

class Unbindable(Refused):
    pass

RAISED = []

def lent(p):
    from mod_a import Refused as Unbindable  # the lender's class, in this def only
    RAISED.append(p)
    raise Unbindable(p)

def own(p):
    RAISED.append(p)
    raise Unbindable(p)                      # the local subclass: this module's family

def not_a_class(p):
    from mod_a import quiet as Unbindable    # a function of that name, in this def only
    raise Unbindable(p)
'''

# a family name rebound AFTER the subclass was derived from it
FAMILY_REBOUND_CALLEE = '''
import refusal

class Refused(refusal.Refused, Exception):   # the module's own family, at first
    pass

class Unbindable(Refused):                   # derives from THAT class, for good
    pass

from mod_a import Refused                    # the NAME rebinds later; the base does not

RAISED = []

def load_capture(p):
    RAISED.append(p)
    raise Unbindable(f"cannot bind {p}")
'''

# a DOTTED base: the shape of a family kept under another name (#2011). Not
# resolved by this fix -- pinned here so a change moves it deliberately.
DOTTED_BASE_CALLEE = '''
import refusal

class Unbindable(refusal.Refused):           # no family NAME in this module at all
    pass

RAISED = []

def load_capture(p):
    RAISED.append(p)
    raise Unbindable(f"cannot bind {p}")
'''

PLAY_SUBCLASS_RAISER = '''
import refusal

class Refused(refusal.Refused, Exception):
    pass

class Unbindable(Refused):
    pass

def fetch(p):
    raise Unbindable("cannot bind")
'''

PLAY_NON_FAMILY_RAISER = '''
class Unbindable(Exception):
    pass

def fetch(p):
    raise Unbindable("cannot bind")
'''

CALLER_AMBIG_SUBCLASS = '''
from play import fetch

def go(p):
    return fetch(p)                          # WHICH play? the census must not guess
'''

SUBCLASS_CALLER_UNGUARDED = '''
import subclass_callee

def go(p):
    return subclass_callee.load_capture(p)   # unguarded: the site the census lost
'''

SUBCLASS_CALLER_BASE = '''
import refusal
import subclass_callee

def go(p):
    try:
        return subclass_callee.load_capture(p)
    except refusal.Refused as e:             # the common base: every subclass
        print("REFUSED", e)
'''

# `{lender}` names the module whose `.Refused` the handler catches
SUBCLASS_CALLER_EXACT = '''
import refusal
import mod_a
import mod_b
import subclass_callee

def go(p):
    try:
        return subclass_callee.load_capture(p)
    except {lender}.Refused as e:            # covers exactly {lender}'s class and its subclasses
        print("REFUSED", e)
'''

SUBCLASS_CALLER_SIBLING = '''
import refusal
import subclass_callee

class Refused(refusal.Refused, Exception):   # the caller's OWN class: a sibling of Unbindable
    pass

def go(p):
    try:
        return subclass_callee.load_capture(p)
    except Refused as e:
        print("REFUSED", e)
'''

SUBCLASS_CALLER_CLI_BASE = '''
import sys
import refusal
import subclass_callee

def go(p):
    return subclass_callee.load_capture(p)   # unguarded HERE; the guard is the boundary

if __name__ == "__main__":
    try:
        sys.exit(go("x"))
    except refusal.Refused as e:
        print(f"REFUSED  {e}")
        sys.exit(2)
'''

PLAIN_CALLER = '''
import subclass_callee

def go(p):
    return subclass_callee.plain(p)          # the module's plain family raise: a site all along
'''

QUIET_CALLER = '''
import subclass_callee

def go(p):
    return subclass_callee.quiet(p)          # no raise at all: no site before or after
'''

CHAIN_CALLER = '''
import subclass_callee

def go(p):
    return subclass_callee.load_capture(p)   # raises B, two levels under the family

def one(p):
    return subclass_callee.one_level(p)      # raises A
'''

SUBCLASS_LOCAL_SHADOW_CALLER = '''
import subclass_callee

def go(p):
    return subclass_callee.lent(p)           # mod_a's class, through the def's own borrow

def own(p):
    return subclass_callee.own(p)            # the local subclass

def other(p):
    return subclass_callee.not_a_class(p)    # a function of that name: no refusal
'''


def _subclass_verdict(monkeypatch, tmp_path: pathlib.Path, caller: str,
                      fn: str | None = "go") -> str:
    """What the INTERPRETER does with a planted caller of `subclass_callee`:
    'caught' when the callee RAISED and the caller came back, 'escaped' when
    the refusal left it, 'left-as-<class>' when something that is no refusal
    left it, 'not-run' when the callee never raised -- a defect in the
    planting, not a verdict. `fn=None` runs the module as `__main__`, the CLI
    boundary itself, and reads its exit status: the fixture exits 2 from its
    handler and 0 from a returned value."""
    import refusal                            # noqa: E402  (tools/ is on sys.path above)
    for stem in ("mod_a", "mod_b", "relay", "foreign", "subclass_callee"):
        if (tmp_path / f"{stem}.py").exists():
            _import(monkeypatch, tmp_path / f"{stem}.py")
    callee = sys.modules["subclass_callee"]
    path = tmp_path / f"{caller}.py"
    if fn is None:
        monkeypatch.setattr(sys, "dont_write_bytecode", True)
        spec = importlib.util.spec_from_file_location("__main__", path)
        mod = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, "__main__", mod)
        try:
            spec.loader.exec_module(mod)
        except SystemExit as e:
            verdict = "caught" if e.code == 2 else f"exit-{e.code!r}"
        except refusal.Refused:
            verdict = "escaped"
        else:
            verdict = "no-exit"
    else:
        mod = _import(monkeypatch, path)
        try:
            getattr(mod, fn)("x")
        except refusal.Refused:
            verdict = "escaped"
        except Exception as e:               # noqa: BLE001  reported, never swallowed
            verdict = f"left-as-{type(e).__name__}"
        else:
            verdict = "caught"
    return verdict if callee.RAISED else "not-run"


def test_a_def_raising_only_a_local_subclass_is_a_site(tmp_path):
    """The issue's shape, red first: `raise_origins` keyed a raise on the
    name `Refused`, so `load_capture` -- whose one raise is `Unbindable`, a
    subclass of the callee's own `Refused` -- read as raising nothing and
    this unguarded caller was ABSENT from the census. The module-level
    assertion keeps the control honest about which fix passes it: the class
    is read as the family's subclass, to the module's own origin, the origin
    a plain `raise Refused` in the same module has."""
    files, idx = _local_tree(tmp_path, {"subclass_callee": SUBCLASS_CALLEE,
                                        "caller_sub_unguarded": SUBCLASS_CALLER_UNGUARDED})
    got = _sites_for(files, idx, "caller_sub_unguarded")
    assert len(got) == 1, got
    assert (got[0]["callee"], got[0]["origins"], got[0]["classification"],
            got[0]["guard"], got[0]["uncovered"]) == (
        "subclass_callee.load_capture", ["subclass_callee"], "candidate-escape",
        "none", ["subclass_callee"]), got[0]
    info = idx[str(tmp_path / "subclass_callee.py")]
    assert info.family_subclasses == {"Unbindable": frozenset({"subclass_callee"})}, vars(info)


def test_the_module_of_a_subclass_still_reads_its_plain_raises(tmp_path):
    """The fix changes one reading: a raise of a subclass. The plain family
    raise in the same module keeps its row, and the quiet def stays quiet."""
    files, idx = _local_tree(tmp_path, {
        "subclass_callee": SUBCLASS_CALLEE, "caller_sub_plain": PLAIN_CALLER,
        "caller_sub_quiet": QUIET_CALLER})
    plain = _sites_for(files, idx, "caller_sub_plain")
    assert len(plain) == 1, plain
    assert (plain[0]["origins"], plain[0]["classification"]) == (
        ["subclass_callee"], "candidate-escape"), plain[0]
    assert _sites_for(files, idx, "caller_sub_quiet") == []


@pytest.mark.parametrize("caller,fn,python,row", [
    pytest.param(SUBCLASS_CALLER_UNGUARDED, "go", "escaped",
                 ("candidate-escape", "none", ["subclass_callee"]), id="unguarded"),
    pytest.param(SUBCLASS_CALLER_BASE, "go", "caught",
                 ("covered(base)", "base", []), id="base-guard"),
    pytest.param(SUBCLASS_CALLER_EXACT.format(lender="subclass_callee"), "go", "caught",
                 ("covered(exact)", "exact", []), id="the-familys-own-class"),
    pytest.param(SUBCLASS_CALLER_SIBLING, "go", "escaped",
                 ("candidate-escape", "local", ["subclass_callee"]), id="sibling-guard"),
    pytest.param(SUBCLASS_CALLER_CLI_BASE, None, "caught",
                 ("covered(cli-base)", "none", []), id="cli-base-boundary"),
])
def test_the_census_row_of_a_subclass_raise_follows_the_interpreter(
        tmp_path, monkeypatch, caller, fn, python, row):
    """Resolved to the module's origin, a subclass raise reads exactly as the
    plain family raise beside it: covered by the base and by the family's own
    class (which it derives from), an escape under a sibling's, caught at a
    base CLI boundary. Each row is what the interpreter did with the planted
    files, so a resolution to some other origin (the base module's, say)
    cannot pass: `except subclass_callee.Refused` is `exact` here and would
    leave that origin uncovered."""
    files, idx = _local_tree(tmp_path, {"subclass_callee": SUBCLASS_CALLEE,
                                        "caller_sub": caller})
    assert _subclass_verdict(monkeypatch, tmp_path, "caller_sub", fn) == python, caller
    got = _sites_for(files, idx, "caller_sub")
    assert len(got) == 1, got
    assert (got[0]["origins"], got[0]["classification"], got[0]["guard"],
            got[0]["uncovered"]) == (["subclass_callee"], *row), got[0]


@pytest.mark.parametrize("callee", [SUBCLASS_CALLEE, NON_FAMILY_CALLEE],
                         ids=["of-the-family", "outside-the-family"])
def test_a_local_class_outside_the_family_raised_the_same_way_is_not_counted(
        tmp_path, monkeypatch, callee):
    """Rule 8: the input that would satisfy a fix counting ANY local class.
    `Unbindable(Exception)` beside an intact family, raised by the same
    def under the same name, must leave the census: what leaves the caller
    is not a refusal, whichever guard is written. The family's own plain
    raise is still a site, so the module was not dropped as a whole."""
    files, idx = _local_tree(tmp_path, {
        "subclass_callee": callee, "caller_sub_unguarded": SUBCLASS_CALLER_UNGUARDED,
        "caller_sub_base": SUBCLASS_CALLER_BASE, "caller_sub_plain": PLAIN_CALLER})
    of_the_family = callee is SUBCLASS_CALLEE
    info = idx[str(tmp_path / "subclass_callee.py")]
    assert bool(info.family_subclasses) is of_the_family, vars(info)
    got = _sites_for(files, idx, "caller_sub_unguarded")
    assert bool(got) is of_the_family, got
    assert bool(_sites_for(files, idx, "caller_sub_base")) is of_the_family
    verdict = _subclass_verdict(monkeypatch, tmp_path, "caller_sub_unguarded")
    assert verdict == ("escaped" if of_the_family else "left-as-Unbindable"), verdict
    plain = _sites_for(files, idx, "caller_sub_plain")
    assert len(plain) == 1 and plain[0]["origins"] == ["subclass_callee"], plain


def test_a_subclass_of_a_subclass_is_still_the_family(tmp_path, monkeypatch):
    """`class B(A)` after `class A(Refused)`: two levels down, and a refusal
    of the module's family either way. The interpreter is asked first, so
    the chain is a claim about Python's class relations, not the census's."""
    files, idx = _local_tree(tmp_path, {"subclass_callee": CHAIN_CALLEE,
                                        "caller_sub_chain": CHAIN_CALLER})
    info = idx[str(tmp_path / "subclass_callee.py")]
    assert info.family_subclasses == {"A": frozenset({"subclass_callee"}),
                                      "B": frozenset({"subclass_callee"})}, vars(info)
    assert _subclass_verdict(monkeypatch, tmp_path, "caller_sub_chain") == "escaped"
    callee = sys.modules["subclass_callee"]
    assert issubclass(callee.B, callee.A) and issubclass(callee.B, callee.Refused)
    got = {s["callee"]: s for s in _sites_for(files, idx, "caller_sub_chain")}
    assert sorted(got) == ["subclass_callee.load_capture", "subclass_callee.one_level"], got
    for s in got.values():
        assert (s["origins"], s["classification"], s["uncovered"]) == (
            ["subclass_callee"], "candidate-escape", ["subclass_callee"]), s


@pytest.mark.parametrize("callee", [BORROWED_SUBCLASS_CALLEE, ALIASED_BORROWED_SUBCLASS_CALLEE],
                         ids=["borrowed", "borrowed-under-an-alias"])
def test_a_subclass_of_a_reexported_family_resolves_to_the_definer(
        tmp_path, monkeypatch, callee):
    """The `defining()` resolution, on a subclass: the callee's family is
    relay's re-export of mod_a's class, so the subclass's origin is mod_a --
    the origin a plain `raise Refused` there has. A handler naming mod_a's
    class covers it and one naming mod_b's, a sibling, does not, because
    relay.Refused IS mod_a.Refused and the interpreter says so."""
    files, idx = _local_tree(tmp_path, {
        "relay": RELAY, "subclass_callee": callee,
        "caller_sub_lender": SUBCLASS_CALLER_EXACT.format(lender="mod_a"),
        "caller_sub_sibling": SUBCLASS_CALLER_EXACT.format(lender="mod_b")})
    lender = _sites_for(files, idx, "caller_sub_lender")
    assert len(lender) == 1, lender
    assert (lender[0]["origins"], lender[0]["classification"], lender[0]["guard"]) == (
        ["mod_a"], "covered(exact)", "exact"), lender[0]
    sib = _sites_for(files, idx, "caller_sub_sibling")
    assert len(sib) == 1, sib
    assert (sib[0]["origins"], sib[0]["classification"], sib[0]["uncovered"]) == (
        ["mod_a"], "candidate-escape", ["mod_a"]), sib[0]
    assert _subclass_verdict(monkeypatch, tmp_path, "caller_sub_lender") == "caught"
    assert _subclass_verdict(monkeypatch, tmp_path, "caller_sub_sibling") == "escaped"


@pytest.mark.parametrize("callee,origins,verdict", [
    pytest.param(SHADOWED_SUBCLASS_CALLEE, None, "left-as-Unbindable",
                 id="to-a-class-outside-the-family"),
    pytest.param(FAMILY_SHADOWED_SUBCLASS_CALLEE, ["mod_a"], "escaped",
                 id="to-a-borrowed-family"),
])
def test_a_subclass_name_rebound_by_a_later_import_is_no_longer_the_subclass(
        tmp_path, monkeypatch, callee, origins, verdict):
    """Python's last binding wins for a class name as for any other: an
    import AFTER `class Unbindable(Refused)` rebinds the name, and a later
    `raise Unbindable(...)` raises what the import bound -- never the
    subclass, which no longer has a name. The first row is the input that
    defeats a fix keeping every class it saw; the second, that the family
    NAME a rebind to a borrowed `Refused` gives is read first, as everywhere
    else, to the lender."""
    files, idx = _local_tree(tmp_path, {
        "foreign": FOREIGN_CLASS, "subclass_callee": callee,
        "caller_sub_unguarded": SUBCLASS_CALLER_UNGUARDED,
        "caller_sub_exact": SUBCLASS_CALLER_EXACT.format(lender="mod_a")})
    assert idx[str(tmp_path / "subclass_callee.py")].family_subclasses == {}
    assert _subclass_verdict(monkeypatch, tmp_path, "caller_sub_unguarded") == verdict
    got = _sites_for(files, idx, "caller_sub_unguarded")
    if origins is None:
        assert got == [] and _sites_for(files, idx, "caller_sub_exact") == [], got
        return
    assert len(got) == 1 and (got[0]["origins"], got[0]["classification"]) == (
        origins, "candidate-escape"), got[0]
    exact = _sites_for(files, idx, "caller_sub_exact")
    assert len(exact) == 1 and (exact[0]["origins"], exact[0]["classification"]) == (
        origins, "covered(exact)"), exact[0]
    assert _subclass_verdict(monkeypatch, tmp_path, "caller_sub_exact") == "caught"


def test_a_subclass_name_shadowed_inside_one_def_is_the_subclass_only_elsewhere(tmp_path):
    """The scope rule of #1573/#1577, on a class name: a def's own import of
    the name binds it there and only there, so `lent` raises mod_a's class
    and `own`, in the sibling def, the local subclass -- which the
    module-level assertion shows was not hoisted away by the def's borrow."""
    files, idx = _local_tree(tmp_path, {"subclass_callee": LOCAL_SHADOW_CALLEE,
                                        "caller_sub_local": SUBCLASS_LOCAL_SHADOW_CALLER})
    info = idx[str(tmp_path / "subclass_callee.py")]
    assert info.family_subclasses == {"Unbindable": frozenset({"subclass_callee"})}, vars(info)
    got = {s["callee"]: (s["origins"], s["classification"])
           for s in _sites_for(files, idx, "caller_sub_local")}
    assert got == {"subclass_callee.lent": (["mod_a"], "candidate-escape"),
                   "subclass_callee.own": (["subclass_callee"], "candidate-escape")}, got


def test_a_family_name_rebound_after_the_subclass_does_not_reach_its_base(tmp_path, monkeypatch):
    """Bases are read as of the class statement, as Python reads them.
    `Unbindable(Refused)` derives from the module's FIRST `Refused`; a
    `from mod_a import Refused` written later rebinds the name and leaves the
    base alone. So `<mod>.Refused` at the caller is mod_a's class, which the
    raised subclass does NOT derive from: an exact escape, which the
    interpreter confirms. Reading the base through the module's final binding
    would name mod_a's origin and report this site covered."""
    files, idx = _local_tree(tmp_path, {
        "subclass_callee": FAMILY_REBOUND_CALLEE,
        "caller_sub_final": SUBCLASS_CALLER_EXACT.format(lender="subclass_callee"),
        "caller_sub_mod_a": SUBCLASS_CALLER_EXACT.format(lender="mod_a"),
        "caller_sub_base": SUBCLASS_CALLER_BASE})
    info = idx[str(tmp_path / "subclass_callee.py")]
    assert info.family_subclasses == {"Unbindable": frozenset({"subclass_callee"})}, vars(info)
    assert info.refused_names == {"Refused": "mod_a"}, info.refused_names
    for caller, row in (("caller_sub_final", ("candidate-escape", ["subclass_callee"])),
                        ("caller_sub_mod_a", ("candidate-escape", ["subclass_callee"])),
                        ("caller_sub_base", ("covered(base)", []))):
        got = _sites_for(files, idx, caller)
        assert len(got) == 1 and (got[0]["origins"], got[0]["classification"],
                                  got[0]["uncovered"]) == (["subclass_callee"], *row), (caller, got)
    assert _subclass_verdict(monkeypatch, tmp_path, "caller_sub_final") == "escaped"
    assert _subclass_verdict(monkeypatch, tmp_path, "caller_sub_mod_a") == "escaped"
    assert _subclass_verdict(monkeypatch, tmp_path, "caller_sub_base") == "caught"


@pytest.mark.parametrize("beta", [PLAY_NON_FAMILY_RAISER, PLAIN],
                         ids=["other-candidate-outside-the-family", "other-candidate-quiet"])
def test_a_collision_whose_raising_candidate_raises_a_subclass_is_a_site(tmp_path, beta):
    """The collision branch tests each candidate for a family and for a
    raising def before emitting a row; a subclass raise must count there as
    in the single-callee branch, or the site is dropped as unable to refuse
    under any resolution. Each candidate is read with its own tree."""
    files = _collision_tree(tmp_path, PLAY_SUBCLASS_RAISER, beta,
                            "caller_amb_sub", CALLER_AMBIG_SUBCLASS)
    idx = rss.build_index(files)
    got = _sites_for(files, idx, "caller_amb_sub")
    assert len(got) == 1, got
    assert (got[0]["classification"], got[0]["guard"]) == ("ambiguous-callee", "none"), got[0]
    assert len(got[0]["candidates"]) == 2, got[0]


def test_a_collision_where_no_candidate_raises_a_refusal_is_not_a_site(tmp_path):
    """The multi-candidate mirror of the rule-8 control: two same-stem
    candidates whose `fetch` each raise a same-named class outside the
    family cannot refuse under ANY resolution, so there is no row."""
    files = _collision_tree(tmp_path, PLAY_NON_FAMILY_RAISER, PLAY_NON_FAMILY_RAISER,
                            "caller_amb_other", CALLER_AMBIG_SUBCLASS)
    idx = rss.build_index(files)
    assert _sites_for(files, idx, "caller_amb_other") == []


def test_a_dotted_base_is_not_resolved_and_the_refusal_escapes_unlisted(tmp_path, monkeypatch):
    """A stated blind spot, pinned. `class Unbindable(refusal.Refused)` has
    no family NAME for a base, so the class is no family subclass by the
    rule this fix adds and its raise stays unread: the census holds no row
    while the interpreter lets the refusal out of the caller. On this tree
    that is the shape of a family kept under another name (`class
    Refusal(refusal.Refused, Exception)`), which the census cannot see on
    the raise or the handler side either; it is filed on its own, not
    absorbed here."""
    files, idx = _local_tree(tmp_path, {"subclass_callee": DOTTED_BASE_CALLEE,
                                        "caller_sub_unguarded": SUBCLASS_CALLER_UNGUARDED})
    info = idx[str(tmp_path / "subclass_callee.py")]
    assert info.family_subclasses == {}, vars(info)
    assert _sites_for(files, idx, "caller_sub_unguarded") == []
    assert _subclass_verdict(monkeypatch, tmp_path, "caller_sub_unguarded") == "escaped"


def test_with_the_subclass_lookup_reverted_the_subclass_site_is_absent_again(tmp_path):
    """Mutant 1 of 3: `raise_origins` back to reading a family name alone,
    compiled in memory from the shipped source. The issue's own measurement,
    re-derived -- `load_capture` raises nothing the census knows, and the
    unguarded caller is gone -- so the red-first control is red under it and
    green only through the lookup."""
    mut = _scanner_with("subs = mod.family_subclasses_at(txt, node) if fam is None else ()",
                        "subs = ()")
    files, idx = _local_tree(tmp_path, {"subclass_callee": SUBCLASS_CALLEE,
                                        "caller_sub_unguarded": SUBCLASS_CALLER_UNGUARDED})
    mut_idx = mut.build_index(files)
    # the class IS known to the mutant; only the raise stopped reading it
    assert mut_idx[str(tmp_path / "subclass_callee.py")].family_subclasses == {
        "Unbindable": frozenset({"subclass_callee"})}
    got = [s for s in mut.scan(files, mut_idx) if "caller_sub_unguarded.py" in s["site"]]
    assert got == [], got
    assert len(_sites_for(files, idx, "caller_sub_unguarded")) == 1


def test_with_the_rebinding_reverted_a_shadowed_subclass_name_is_counted_again(tmp_path):
    """Mutant 2 of 3: the import no longer unbinds a local subclass's name.
    `raise Unbindable(...)` after the rebind is no refusal, and the mutant
    reports the caller a candidate escape -- the rule-8 shadow control red
    under it: a site invented for a raise Python cannot catch as a refusal."""
    mut = _scanner_with(
        "                for nm in _bind(node, self.imports, self.from_imports):\n"
        "                    self.family_subclasses.pop(nm, None)",
        "                _bind(node, self.imports, self.from_imports)")
    files = _local_tree(tmp_path, {"foreign": FOREIGN_CLASS,
                                   "subclass_callee": SHADOWED_SUBCLASS_CALLEE,
                                   "caller_sub_unguarded": SUBCLASS_CALLER_UNGUARDED})[0]
    idx = mut.build_index(files)
    got = [s for s in mut.scan(files, idx) if "caller_sub_unguarded.py" in s["site"]]
    assert [(s["classification"], s["uncovered"]) for s in got] == [
        ("candidate-escape", ["subclass_callee"])], got


def test_with_every_local_class_counted_the_rule_8_control_goes_red(tmp_path):
    """Mutant 3 of 3: the fix as its own cheap imitation -- any class defined
    in the module reads as a subclass of the module's family. The
    `Unbindable(Exception)` control is red under it: a def that raises no
    refusal is reported a candidate escape."""
    mut = _scanner_with(
        "                if fams:\n"
        "                    self.family_subclasses[node.name] = frozenset(fams)",
        "                if True:\n"
        "                    self.family_subclasses[node.name] = "
        "frozenset(fams or {self.path.stem})")
    files = _local_tree(tmp_path, {"subclass_callee": NON_FAMILY_CALLEE,
                                   "caller_sub_unguarded": SUBCLASS_CALLER_UNGUARDED})[0]
    idx = mut.build_index(files)
    got = [s for s in mut.scan(files, idx) if "caller_sub_unguarded.py" in s["site"]]
    assert [(s["callee"], s["classification"]) for s in got] == [
        ("subclass_callee.load_capture", "candidate-escape")], got

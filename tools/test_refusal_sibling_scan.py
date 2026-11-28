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
census, accepted with the runtime evidence for its acceptance. The #1641
section pins the CLI boundary reached through a `_cli()` wrapper the
dispatch calls -- the placement hh_probe5's first repair used, which the
census read as no guard -- and the inputs that must not be credited as one. The
#1718 section plants an `async def` callee, which `funcs` did not register: each
control RUNS the planted coroutine code first and holds the census row to the
interpreter's verdict, and each fix is reverted in memory to show that its
control goes red without it. The #1600 section plants a site inside a nested
def whose caller guards the call: each control RUNS the escape route it names
first and holds the census row to the interpreter's verdict, and each refusal
of the credit is reverted in memory to show the control written for it reads a
false covered without it.
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
# lexically outside that try (it sits in the helper's def), so until #1600 the
# census reported it `guard=none, candidate-escape`: a guard in the def that
# CALLS a local helper was not credited. It reads `covered(exact)` now, and
# the site itself is still ACCEPTED, not repaired -- a second handler at the
# call would only restate the one that already catches it. The controls below
# are the evidence for that acceptance, run on the shipped function; the
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


def test_the_real_drum_solo_site_is_covered_exact_in_the_census():
    """The census row of the site those two controls are the evidence for,
    read on the tracked tree: `guard=none, candidate-escape` until #1600,
    now the handler its caller wraps both `_render()` calls in. Found by
    callee and not by line, so an edit elsewhere in the file cannot red it."""
    files = rss.tracked_files()
    scope = [f for f in files if any(str(f.relative_to(rss.ROOT)).startswith(r + "/")
                                     for r in rss.SCAN_ROOTS)]
    idx = rss.build_index(files)
    got = [s for s in rss.scan(scope, idx)
           if s["callee"] == "run_case.render_drum_solo"
           and s["site"].startswith("rtl-sketch/verify_synth_top.py:")]
    assert len(got) == 1, got
    assert (got[0]["origins"], got[0]["classification"], got[0]["guard"],
            got[0]["uncovered"]) == (["preparation_contract"], "covered(exact)",
                                     "exact", []), got[0]


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
    """`in_boundary` matched `ast.FunctionDef` named `main`, so a module
    whose `main` was an `async def` had no boundary in the census: a site
    that module's `main()` catches was reported a candidate escape, the
    repair shape of #1160/#1162 read as still open. The second assertion is
    the control's other side: the same try in any other async def is still
    no boundary, as for a def."""
    files = _async_files(tmp_path, {"caller_async_main": ASYNC_MAIN_BOUNDARY,
                                    "caller_async_run": ASYNC_NON_MAIN_GUARD})
    idx = rss.build_index(files)
    by_stem = rss.stem_map(idx)
    main = idx[str(tmp_path / "caller_async_main.py")]
    run = idx[str(tmp_path / "caller_async_run.py")]
    assert rss.cli_boundary_guards(main, by_stem) == [(set(), "base")]
    assert rss.cli_boundary_guards(run, by_stem) == []


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


def test_with_the_main_match_reverted_an_async_main_is_no_boundary(tmp_path):
    """Mutant 2 of 3: `in_boundary` back to `ast.FunctionDef` alone. The
    try in `async def main` is no boundary again, so the site it catches is
    a candidate escape -- the control above red under this revert."""
    mut = _scanner_with('if isinstance(cur, FUNCTION_NODES) and cur.name == "main":',
                        'if isinstance(cur, ast.FunctionDef) and cur.name == "main":')
    files = _async_files(tmp_path, {"caller_async": ASYNC_MAIN_BOUNDARY_NOT_BARE})
    idx = mut.build_index(files)
    caller = idx[str(tmp_path / "caller_async.py")]
    # the shipped scanner credits it: the revert below is what removes that
    ship = rss.build_index(files)
    assert rss.cli_boundary_guards(ship[str(tmp_path / "caller_async.py")], rss.stem_map(ship)) != []
    assert mut.cli_boundary_guards(caller, mut.stem_map(idx)) == []
    got = [s for s in mut.scan(files, idx) if "caller_async.py" in s["site"]]
    assert [(s["classification"], s["guard"]) for s in got] == [("candidate-escape", "none")], got


def test_with_the_await_rule_reverted_a_bare_call_borrows_the_trys_handler(tmp_path):
    """Mutant 3 of 3: `refusal_runs_at_call` answering True for every call,
    the census with the two registrations fixed and no await rule. The Call
    node sits in the try body, so the walk lends it the handler, and a
    refusal Python raises after the try reads `covered(base)` -- the false
    covered the rule exists to withhold."""
    mut = _scanner_with(
        "    return not any(isinstance(c.funcs.get(fname), ast.AsyncFunctionDef)"
        " for c in callees)",
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


def test_a_wrapper_main_calls_is_not_the_boundary(tmp_path):
    """A wrapper `main()` calls by bare name is NOT credited, though it is
    the same `_cli()` shape. `main()` fans out to sibling helpers and a
    sibling's try guards its own branch alone, so crediting it covers every
    site in the module: tried during #1641, it turned ten escaping sites
    into `covered` (cymbal_low_tail, lead_drive_337, r0_capture -- the
    row-by-row table is #1656). The dispatch's callees are frames every CLI
    path passes through; main's are not. A conservative false escape is the
    harmless direction."""
    s = _wrapper_scan(tmp_path, WRAPPER_BASE_GUARD + '''
def main(argv=None):
    return _cli(argv)
''', "    sys.exit(main())\n")
    assert s["classification"] == "candidate-escape", s
    assert s["uncovered"] == ["mod_a"], s


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


def test_a_try_one_frame_further_in_is_not_the_boundary(tmp_path):
    """One level only: `_cli` is reached, but ITS callee has the try."""
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
    assert s["classification"] == "candidate-escape", s


def test_a_call_written_in_a_def_nested_in_the_dispatch_is_not_its_own(tmp_path):
    """The dispatch defines `inner` and calls it; `inner` calls the wrapper.
    A call written in a def of the boundary's own is that def's, one frame
    further in."""
    s = _wrapper_scan(tmp_path, WRAPPER_BASE_GUARD, '''
    def inner():
        return _cli()
    sys.exit(inner())
''')
    assert s["classification"] == "candidate-escape", s


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


# ---------------------------------------------------------------------------
# #1600: a guard in the def that CALLS a local helper. A site written in a
# nested def is lexically outside its caller's try, so the census read it
# `guard=none, candidate-escape` though the refusal is caught one frame up --
# the shape `verify_synth_top.drum_solo_report` and its `_render` have. The
# tries around the helper's CALLS are credited now, and only when every
# reference to the helper in the module is such a call, in the body of the def
# that defines it. Each input that must keep the row a candidate is planted
# with the escape route it names, RUN under the interpreter first, and held to
# that verdict; each refusal of the credit is then reverted in memory to show
# the control written for it reads a false covered without it.
# ---------------------------------------------------------------------------

HELPER_CALLEE = '''
import refusal

class Refused(refusal.Refused, Exception):
    pass

CALLS = 0                               # how many times a `load` ran, for the verdict

def load(p):
    global CALLS
    CALLS += 1
    raise Refused("planted refusal")
'''

HELPER_GUARDED = '''
import callee

def outer(p):
    def helper(**kw):
        return callee.load(p)            # the census site: lexically outside the try
    try:
        helper()                         # ...caught HERE, by the def that CALLS it
    except callee.Refused as e:
        return f"REFUSED  {e}"

def run():
    return outer("x")
'''

HELPER_TWO_CALLS = '''
import callee

def outer(p):
    def helper(**kw):
        return callee.load(p)
    try:
        at_strike = helper(frames=1)     # the real site's shape: two calls, one try
        scorecard = helper()
    except callee.Refused as exc:
        return f"REFUSED  {exc}"

def run():
    return outer("x")
'''

HELPER_TWO_GUARDS = '''
import refusal
import callee

def outer(p):
    def helper(**kw):
        return callee.load(p)
    try:
        helper()
    except callee.Refused:              # exact at one call...
        pass
    try:
        helper()
    except refusal.Refused:             # ...and the base at the other
        pass

def run():
    return outer("x")
'''

HELPER_LISTCOMP = '''
import callee

def outer(p):
    def helper(**kw):
        return [callee.load(x) for x in [p]]   # a list comprehension runs eagerly, inside helper()
    try:
        helper()
    except callee.Refused:
        return None

def run():
    return outer("x")
'''

HELPER_ASYNC_AWAITED = '''
import asyncio
import callee

async def outer(p):
    async def helper(**kw):
        return callee.load(p)           # raises inside the coroutine...
    try:
        await helper()                  # ...and surfaces HERE, inside the try
    except callee.Refused:
        return None

def run():
    return asyncio.run(outer("x"))
'''

HELPER_MIXED_CALLERS = '''
import callee

def outer(p):
    def helper(**kw):
        return callee.load(p)
    try:
        helper()                         # guarded...
    except callee.Refused:
        pass
    return helper()                      # ...and not: a second caller

def run():
    return outer("x")
'''

HELPER_RETURNED = '''
import callee

def outer(p):
    def helper(**kw):
        return callee.load(p)
    try:
        helper()
    except callee.Refused:
        pass
    return helper                       # ESCAPES its def

def run():
    outer("x")()                        # called where the def never sees it
'''

HELPER_STORED = '''
import callee

REGISTRY = []

def outer(p):
    def helper(**kw):
        return callee.load(p)
    try:
        helper()
    except callee.Refused:
        pass
    REGISTRY.append(helper)             # stored on an object

def run():
    outer("x")
    REGISTRY[0]()
'''

HELPER_CALLBACK = '''
import callee

def each(f, p):
    return f(p)

def outer(p):
    def helper(*a, **kw):
        return callee.load(p)
    try:
        helper()
    except callee.Refused:
        pass
    return each(helper, p)              # passed as a callback: invoked inside each

def run():
    return outer("x")
'''

HELPER_SIBLING_DEF = '''
import callee

def outer(p):
    def helper(**kw):
        return callee.load(p)
    def other(**kw):
        return helper(**kw)              # called from ANOTHER nested def, unguarded
    try:
        helper()
    except callee.Refused:
        pass
    return other()                       # the refusal leaves outer here

def run():
    return outer("x")
'''

HELPER_GLOBAL = '''
import callee

def outer(p):
    global helper                        # the helper becomes a module attribute
    def helper(**kw):
        return callee.load(p)
    try:
        helper()
    except callee.Refused:
        pass
'''

HELPER_GLOBAL_USER = '''
import caller

def run():
    caller.outer("x")
    caller.helper()                      # called from outside the def, by attribute
'''

HELPER_UNCALLED = '''
import callee

def outer(p):
    def helper(**kw):
        return callee.load(p)           # defined, never called: the site cannot run

def run():
    return outer("x")
'''

HELPER_DECORATED = '''
import callee

REGISTRY = []

def register(f):
    REGISTRY.append(f)                  # the decorator holds the helper...
    return f

def outer(p):
    @register
    def helper(**kw):
        return callee.load(p)
    try:
        helper()
    except callee.Refused:
        pass

def run():
    outer("x")
    REGISTRY[0]()                       # ...and hands it on: called outside the def
'''

HELPER_ASYNC_BARE = '''
import asyncio
import callee

async def outer(p):
    async def helper(**kw):
        return callee.load(p)
    try:
        coro = helper()                 # CREATED inside the try: no code runs yet
    except callee.Refused:
        return None
    await coro                          # ...and RAISED after it

def run():
    return asyncio.run(outer("x"))
'''

HELPER_GENERATOR = '''
import callee

def outer(p):
    def helper(**kw):
        yield callee.load(p)            # a generator: the site runs when it is ITERATED
        yield 0
    try:
        g = helper()                    # CREATED inside the try: no code runs yet
    except callee.Refused:
        return None
    return list(g)                      # ...and runs after it

def run():
    return outer("x")
'''

HELPER_IN_LAMBDA = '''
import callee

REGISTRY = []

def top(p):
    def outer(q):
        helper = lambda **kw: callee.load(q)    # a lambda has no def to follow
        REGISTRY.append(helper)
        try:
            helper()
        except callee.Refused:
            pass
    try:
        outer(p)
    except callee.Refused:
        pass

def run():
    top("x")
    REGISTRY[0]()                       # called where no guard reaches
'''

HELPER_IN_GENEXP = '''
import callee

REGISTRY = []

def top(p):
    def outer(q):
        REGISTRY.append(callee.load(x) for x in [q])   # a generator expression: runs when iterated
    try:
        outer(p)
    except callee.Refused:
        pass

def run():
    top("x")
    return list(REGISTRY[0])            # iterated outside every try
'''

HELPER_DEFAULT_ARG_SITE = '''
import callee

def outer(p):
    def helper(x=callee.load(p)):       # a site in the DEFAULT: runs at the def statement
        return x
    try:
        helper()
    except callee.Refused:
        return None

def run():
    return outer("x")
'''

HELPER_SIBLING_HANDLER = '''
import refusal
import callee

class Refused(refusal.Refused, Exception):
    pass

def outer(p):
    def helper(**kw):
        return callee.load(p)
    try:
        helper()
    except Refused:                    # the caller's OWN class: a sibling of callee's
        return None

def run():
    return outer("x")
'''

MODULE_HELPER_GUARDED = '''
import callee

def helper(p):
    return callee.load(p)               # a MODULE-level helper: any frame may call it

def outer(p):
    try:
        helper(p)                       # guarded HERE
    except callee.Refused:
        return None

def run():
    return outer("x")
'''

HELPER_CALLEE_TWO = '''
import callee
import refusal
from callee import Refused as R          # a borrowed family: its origin is `callee`

class Refused(refusal.Refused, Exception):
    pass

def load(p):
    callee.CALLS += 1
    if p == "own":
        raise Refused("planted refusal")
    raise R("planted refusal")
'''

HELPER_PARTIAL = '''
import callee2

def outer(p):
    def helper(**kw):
        return callee2.load(p)
    try:
        helper()
    except callee2.Refused:             # exact for callee2's OWN class alone
        return None

def run():
    return outer("borrowed")            # the origin the handler cannot catch
'''

ESCAPE_ROW = ("candidate-escape", "none", ["callee"])


def _case(src, python, row, *, callee="callee.load", extra=None,
          order=("caller",), run_in="caller"):
    return dict(src=src, python=python, row=row, callee=callee,
                extra=extra or {}, order=("callee", *order), run_in=run_in)


HELPER_CASES = {
    # --- credited: every reference is a guarded call in the enclosing def ---
    "every-call-guarded": _case(HELPER_GUARDED, "caught", ("covered(exact)", "exact", [])),
    "two-calls-one-try": _case(HELPER_TWO_CALLS, "caught", ("covered(exact)", "exact", [])),
    "exact-at-one-call-base-at-the-other": _case(
        HELPER_TWO_GUARDS, "caught", ("covered(base+exact)", "base,exact", [])),
    "site-in-an-eager-list-comprehension": _case(
        HELPER_LISTCOMP, "caught", ("covered(exact)", "exact", [])),
    "async-helper-awaited-in-the-try": _case(
        HELPER_ASYNC_AWAITED, "caught", ("covered(exact)", "exact", [])),
    # --- the issue's three, each with the escape route it names -------------
    "mixed-callers": _case(HELPER_MIXED_CALLERS, "escaped", ESCAPE_ROW),
    "returned": _case(HELPER_RETURNED, "escaped", ESCAPE_ROW),
    "stored-on-an-object": _case(HELPER_STORED, "escaped", ESCAPE_ROW),
    "passed-as-a-callback": _case(HELPER_CALLBACK, "escaped", ESCAPE_ROW),
    "called-from-a-sibling-def": _case(HELPER_SIBLING_DEF, "escaped", ESCAPE_ROW),
    "a-module-attribute-by-global": _case(
        HELPER_GLOBAL, "escaped", ESCAPE_ROW, extra={"helper_global_user": HELPER_GLOBAL_USER},
        order=("caller", "helper_global_user"), run_in="helper_global_user"),
    # --- rule 8: the other inputs that satisfy "every call is guarded" -----
    "never-called": _case(HELPER_UNCALLED, "not-run", ESCAPE_ROW),
    "decorated": _case(HELPER_DECORATED, "escaped", ESCAPE_ROW),
    "async-helper-created-in-the-try": _case(HELPER_ASYNC_BARE, "escaped", ESCAPE_ROW),
    "a-generator": _case(HELPER_GENERATOR, "escaped", ESCAPE_ROW),
    "site-in-a-lambda": _case(HELPER_IN_LAMBDA, "escaped", ESCAPE_ROW),
    "site-in-a-generator-expression": _case(HELPER_IN_GENEXP, "escaped", ESCAPE_ROW),
    "site-in-a-default-argument": _case(HELPER_DEFAULT_ARG_SITE, "escaped", ESCAPE_ROW),
    "the-callers-own-sibling-class": _case(HELPER_SIBLING_HANDLER, "escaped", ESCAPE_ROW),
}


def _helper_files(tmp_path: pathlib.Path, modules: dict[str, str]) -> list[pathlib.Path]:
    files = []
    for name, body in modules.items():
        p = tmp_path / f"{name}.py"
        p.write_text(body)
        files.append(p)
    return files


def _helper_tree(tmp_path: pathlib.Path, c: dict) -> list[pathlib.Path]:
    """`callee.py`, whose `load` always refuses and counts its calls, the
    caller, and any further module the case's escape route needs."""
    return _helper_files(tmp_path, {"callee": HELPER_CALLEE, "caller": c["src"], **c["extra"]})


def _helper_row(scan_mod, files: list[pathlib.Path], callee: str = "callee.load") -> dict:
    """The one `callee` site of a helper tree, read by `scan_mod` (the
    shipped scanner or a mutant of it): a second would be a fixture mistake."""
    idx = scan_mod.build_index(files)
    got = [s for s in scan_mod.scan(files, idx) if s["callee"] == callee]
    assert len(got) == 1, got
    return got[0]


def _helper_verdict(monkeypatch, tmp_path: pathlib.Path, order: tuple[str, ...],
                    run_in: str) -> str:
    """What the INTERPRETER does with a planted escape route: the modules are
    imported for real in `order`, `run()` of the last is called, and
    'caught' / 'escaped' is read where the callee's refusal ends up.
    'not-run' when the callee was never reached: a defect in the planting,
    not a verdict."""
    for name in order:
        _import(monkeypatch, tmp_path / f"{name}.py")
    callee = sys.modules["callee"]
    callee.CALLS = 0
    f = sys.modules[run_in].run
    try:
        asyncio.run(f()) if asyncio.iscoroutinefunction(f) else f()
    except callee.Refused:
        verdict = "escaped"
    else:
        verdict = "caught"
    return verdict if callee.CALLS else "not-run"


@pytest.mark.parametrize("case", list(HELPER_CASES))
def test_the_census_row_of_a_helper_site_follows_the_interpreter(tmp_path, monkeypatch, case):
    """The #1600 rule, held to what Python does. A site inside a nested def
    reads covered ONLY when every reference to the helper in the module is a
    call that the enclosing def guards; one unguarded call, one reference that
    lets the helper out, or one shape that runs the body elsewhere, and the
    refusal leaves the caller -- each planted below, and each read by the
    census as the escape the interpreter reports. The first five are the
    credit itself; they started red against the scanner that read the site's
    own tries alone."""
    c = HELPER_CASES[case]
    files = _helper_tree(tmp_path, c)
    assert _helper_verdict(monkeypatch, tmp_path, c["order"], c["run_in"]) == c["python"], case
    s = _helper_row(rss, files, c["callee"])
    assert (s["classification"], s["guard"], s["uncovered"]) == c["row"], (case, s)


_OUTER_SCOPE_REFUSAL = (
    "        if not (isinstance(ref.ctx, ast.Load) and isinstance(call, ast.Call)\n"
    "                and call.func is ref and scope is outer and entry in outer.body):\n"
    "            return None")
_DECORATED_OR_DEFERRED = "    if h.decorator_list or _is_generator(mod, h) or _is_declared(mod, h):"
_DEFERRED_SCOPES = "DEFERRED_SCOPES = SCOPE_NODES + (ast.Lambda, ast.GeneratorExp)"

HELPER_MUTANTS = [
    # (the shipped line(s), the over-credited variant, the control it turns red)
    pytest.param("        caught &= cov", "        caught |= cov",
                 "mixed-callers", id="the-union-of-the-calls-guards"),
    pytest.param(
        _OUTER_SCOPE_REFUSAL,
        "        if not (isinstance(call, ast.Call) and call.func is ref):\n"
        "            continue\n"
        "        if not (isinstance(ref.ctx, ast.Load)\n"
        "                and scope is outer and entry in outer.body):\n"
        "            return None",
        "returned", id="only-the-call-references-are-read"),
    pytest.param(
        _OUTER_SCOPE_REFUSAL,
        "        if not (isinstance(ref.ctx, ast.Load) and isinstance(call, ast.Call)\n"
        "                and call.func is ref):\n"
        "            return None\n"
        "        if not (scope is outer and entry in outer.body):\n"
        "            continue",
        "called-from-a-sibling-def", id="references-outside-the-enclosing-def-are-not-read"),
    pytest.param("    return (outer, calls) if calls else None", "    return (outer, calls)",
                 "never-called", id="an-uncalled-helper-is-vacuously-covered"),
    pytest.param(
        "        if (isinstance(h, ast.AsyncFunctionDef)\n"
        "                and not isinstance(mod.parents.get(call), ast.Await)):\n"
        "            return None",
        "        pass",
        "async-helper-created-in-the-try", id="a-bare-call-to-an-async-helper-counts"),
    pytest.param(_DECORATED_OR_DEFERRED, "    if _is_generator(mod, h) or _is_declared(mod, h):",
                 "decorated", id="a-decorated-helper-is-credited"),
    pytest.param(_DECORATED_OR_DEFERRED, "    if h.decorator_list or _is_generator(mod, h):",
                 "a-module-attribute-by-global", id="a-global-helper-is-credited"),
    pytest.param(_DECORATED_OR_DEFERRED, "    if h.decorator_list or _is_declared(mod, h):",
                 "a-generator", id="a-generator-helper-is-credited"),
    pytest.param(_DEFERRED_SCOPES, "DEFERRED_SCOPES = SCOPE_NODES",
                 "site-in-a-lambda", id="a-lambda-is-not-a-scope"),
    pytest.param(_DEFERRED_SCOPES, "DEFERRED_SCOPES = SCOPE_NODES",
                 "site-in-a-generator-expression", id="a-generator-expression-is-not-a-scope"),
    pytest.param("    if not isinstance(h, FUNCTION_NODES) or entry not in h.body:",
                 "    if not isinstance(h, FUNCTION_NODES):",
                 "site-in-a-default-argument", id="a-site-in-the-defaults-is-in-the-body"),
]


@pytest.mark.parametrize("old,new,case", HELPER_MUTANTS)
def test_with_a_refusal_reverted_its_control_reads_a_false_covered(tmp_path, old, new, case):
    """The over-credited variant of the fix: ONE refusal reverted in the
    shipped source, and the control written for it reads the site covered
    where the interpreter lets the refusal out. The last statement is that
    control's own assertion, run on the mutant: red, as it must be."""
    c = HELPER_CASES[case]
    mut = _scanner_with(old, new)
    got = _helper_row(mut, _helper_tree(tmp_path, c), c["callee"])
    assert got["classification"].startswith("covered"), (case, got)
    with pytest.raises(AssertionError):
        assert (got["classification"], got["guard"], got["uncovered"]) == c["row"], (case, got)


def test_the_credit_is_per_origin_like_a_sites_own_try(tmp_path, monkeypatch):
    """`helper_call_credit` credits an origin when EVERY call catches it, and
    only that origin: the callee raises two classes and the caller's handler
    is exact for one. The census reports the other as uncovered, as it does
    for a site's own exact try, and Python lets that one out."""
    files = _helper_files(tmp_path, {"callee": HELPER_CALLEE, "callee2": HELPER_CALLEE_TWO,
                                     "caller": HELPER_PARTIAL})
    assert _helper_verdict(monkeypatch, tmp_path, ("callee", "callee2", "caller"),
                           "caller") == "escaped"
    s = _helper_row(rss, files, "callee2.load")
    assert (s["origins"], s["classification"], s["guard"], s["uncovered"]) == (
        ["callee", "callee2"], "candidate-escape", "exact", ["callee"]), s


def test_a_module_level_helper_is_not_followed(tmp_path, monkeypatch):
    """Out of #1600's scope on purpose, and pinned so the boundary is
    visible: Python catches this one and the census still lists it a
    candidate. A module-level helper is callable from every frame in the
    module, so the try around ONE caller's call says nothing about the
    others -- the cross-def call graph, not one level up."""
    files = _helper_files(tmp_path, {"callee": HELPER_CALLEE, "caller": MODULE_HELPER_GUARDED})
    assert _helper_verdict(monkeypatch, tmp_path, ("callee", "caller"), "caller") == "caught"
    s = _helper_row(rss, files)
    assert (s["classification"], s["guard"], s["uncovered"]) == ESCAPE_ROW, s


# The same credit in the stem-collision branch, which reads the tries around a
# site on its own and lets only a base or broad guard settle a callee it
# cannot attribute. The interpreter's verdict is not run here: which module the
# import binds is the undecidable fact the branch exists for.
CALLER_AMBIG_HELPER_BASE = '''
import refusal
from play import run

def outer(p):
    def helper(**kw):
        return run(p)                   # WHICH play? and inside a nested helper
    try:
        helper()
    except refusal.Refused:            # the base: settles it for every candidate
        return None
'''

CALLER_AMBIG_HELPER_EXACT = CALLER_AMBIG_HELPER_BASE.replace(
    "from play import run", "import play").replace(
    "return run(p)", "return play.run(p)").replace(
    "    except refusal.Refused:            # the base: settles it for every candidate\n",
    "    except play.Refused:              # exact for a module it cannot name\n")

CALLER_AMBIG_HELPER_MIXED = CALLER_AMBIG_HELPER_BASE.replace(
    "    except refusal.Refused:            # the base: settles it for every candidate\n"
    "        return None\n",
    "    except refusal.Refused:\n"
    "        pass\n"
    "    return helper()                  # and not at this call\n")


def _amb_helper(tmp_path, caller: str) -> tuple[dict, list[str]]:
    """(the caller's one `play.run` row, the shapes `helper_call_credit`
    returns for it against every origin a candidate raises): what the
    collision branch reads."""
    files = _collision_tree(tmp_path, PLAY_FAMILY_RAISES, PLAIN, "caller_amb_helper", caller)
    idx = rss.build_index(files)
    got = _sites_for(files, idx, "caller_amb_helper")
    assert len(got) == 1, got
    assert len(got[0]["candidates"]) == 2, got[0]
    by_stem = rss.stem_map(idx)
    mod = idx[str(tmp_path / "caller_amb_helper.py")]
    site = next(n for n in ast.walk(mod.tree)
                if isinstance(n, ast.Call) and ast.unparse(n.func) in ("run", "play.run"))
    every = {o for c in by_stem["play"] for o in rss.raise_origins(c, "run", by_stem)}
    return got[0], rss.helper_call_credit(mod, by_stem, site, every)[1]


def test_a_base_guard_at_every_call_of_the_helper_settles_an_ambiguous_callee(tmp_path):
    """Started red: the branch read `ambiguous-callee, guard=none` though the
    caller's `except refusal.Refused` catches whichever `play` is bound."""
    s, credit = _amb_helper(tmp_path, CALLER_AMBIG_HELPER_BASE)
    assert credit == ["base"]
    assert (s["classification"], s["guard"]) == ("covered(base)", "base"), s


def test_an_exact_guard_at_the_helper_calls_settles_no_ambiguous_callee(tmp_path):
    """What settles a collision did not widen: an exact handler names one
    module, and the branch cannot say which candidate that is. The credit
    IS exact here -- asserted, so this is the branch refusing it and not a
    fixture that never reached it."""
    s, credit = _amb_helper(tmp_path, CALLER_AMBIG_HELPER_EXACT)
    assert credit == ["exact"]
    assert (s["classification"], s["guard"]) == ("ambiguous-callee", "none"), s


def test_an_ambiguous_callee_with_one_unguarded_call_stays_ambiguous(tmp_path):
    """The mixed-callers refusal, in this branch too: one call of the helper
    outside the try, and the base guard at the other settles nothing."""
    s, credit = _amb_helper(tmp_path, CALLER_AMBIG_HELPER_MIXED)
    assert credit == []
    assert (s["classification"], s["guard"]) == ("ambiguous-callee", "none"), s


# PR #1803 review, rule 8: the input that defeats the branch's helper credit.
# A base handler at ONE call and a partial exact handler at the other keeps
# `caught` non-empty while leaving the `play` origin uncaught at the second.
ALPHA_PLAY_OWN_ORIGIN = '''
import refusal

class Refused(refusal.Refused, Exception):
    pass

def run(x):
    raise Refused("own class -> origin play")
'''

BETA_PLAY_BORROWED_ORIGIN = '''
import refusal
import mod_a

class Refused(refusal.Refused, Exception):
    pass

def run(x):
    raise mod_a.Refused("borrowed class -> origin mod_a")
'''

CALLER_AMBIG_HELPER_BASE_THEN_PARTIAL = '''
import refusal
import mod_a
import play

def outer(p):
    def helper(**kw):
        return play.run(p)
    try:
        helper()
    except refusal.Refused:             # base: holds for every candidate
        pass
    try:
        helper()
    except mod_a.Refused:               # exact: only the mod_a origin
        pass
'''


def test_base_at_one_helper_call_and_a_partial_guard_at_another_stays_ambiguous(tmp_path):
    """Started red: the row read covered(base) though the second call's
    `except mod_a.Refused` lets the `play` origin out (the interpreter
    escapes it, per the review's probe). The credit is asserted to be the
    defeating one -- non-empty `caught`, short of `every`, a base shape."""
    files = _collision_tree(tmp_path, ALPHA_PLAY_OWN_ORIGIN, BETA_PLAY_BORROWED_ORIGIN,
                            "caller_amb_mixed", CALLER_AMBIG_HELPER_BASE_THEN_PARTIAL)
    idx = rss.build_index(files)
    got = _sites_for(files, idx, "caller_amb_mixed")
    assert len(got) == 1, got
    by_stem = rss.stem_map(idx)
    mod = idx[str(tmp_path / "caller_amb_mixed.py")]
    site = next(n for n in ast.walk(mod.tree)
                if isinstance(n, ast.Call) and ast.unparse(n.func) == "play.run")
    every = {o for c in by_stem["play"] for o in rss.raise_origins(c, "run", by_stem)}
    caught, shapes = rss.helper_call_credit(mod, by_stem, site, every)
    assert caught and caught < every and "base" in shapes, (caught, every, shapes)
    assert (got[0]["classification"], got[0]["guard"]) == ("ambiguous-callee", "none"), got[0]

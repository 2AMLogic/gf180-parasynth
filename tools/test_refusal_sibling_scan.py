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
loss): each control there started red against the unfixed scanner.
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

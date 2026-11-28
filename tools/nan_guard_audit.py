#!/usr/bin/env python3
"""Which refusal guards does a NaN walk straight through, and which boundaries
stop it? (#134)

    python3 tools/nan_guard_audit.py              # the audit, as a table
    python3 tools/nan_guard_audit.py --list       # every fails-open guard
    python3 tools/nan_guard_audit.py --check      # the standing check; exit 1 on a gap
    python3 tools/nan_guard_audit.py --sites       # every NaN-blind comparison site and its
                                                  # verdict (#1929); --ref origin/main to read
                                                  # main, not the checkout
    python3 tools/nan_guard_audit.py --sites --check  # exit 1 on an untriaged or stale site
    python3 tools/nan_guard_audit.py --aggregates  # every builtin max()/min() with a
                                                  # non-literal argument and its verdict
                                                  # (#2014); --ref reads another tree
    python3 tools/nan_guard_audit.py --aggregates --check  # exit 1 on an untriaged or stale site

WHY THIS EXISTS

IEEE comparisons against NaN are always False, so the guard

    if float(np.abs(y).max()) <= 1e-9:
        raise Refused("silent")           # NaN <= 1e-9 is False -> "not silent"

does not merely miss a NaN, it is INVERTED by one: the check written to catch
bad values is the check bad values are most invisible to. #133 is the worked
example -- all-NaN audio passed seven checks in `refprofile.load_clip`
(profile membership, file present, byte count, sha256, sample rate, frame count,
not-silent) and loaded as a reference.

TWO HALVES, AND ONLY ONE OF THEM IS A GATE.

**The audit (default output) is a TRIAGE LIST, not a defect count.** It
classifies each refusal guard by what happens when its ordering comparisons are
forced False, which is what a NaN does to them:

    fails-open      the function CONTINUES -- a NaN reaching here is invisible
    fails-closed    the function REFUSES   -- `if not (x > t)` and friends
    indeterminate   the boolean structure does not decide it either way

A fails-open guard is only a defect if a NaN can actually reach it, and most
cannot: a guard on `len(x)`, `sel.sum()` or an `int(...)` is in the integer
domain where no NaN exists. Those are marked `int?` (a HEURISTIC on the operand
text, advisory only) so a reader can filter them out. Deciding reachability is
the human half of the audit and the tool does not pretend to do it.

**The boundary check (`--check`) is the gate, and it is deliberately narrow.**
Checking finiteness once per audio-entry boundary is tractable; rewriting a
hundred comparison guards is not, and would not help -- a value that reached
them is already a number. So `BOUNDARIES` below names each function where audio
ENTERS this repository from outside its own arithmetic, and `--check` asserts
that each one still contains a finiteness assertion in its own body. That
catches the regression this class of bug is really made of: a guard that is
quietly deleted or refactored away while the suite stays green (#113).

`--check` is asserted by `model/test_nan_invariance.py`, so `make verify` runs
it; the invariance property in that file is the dynamic half that no amount of
AST reading can replace.
"""
from __future__ import annotations

import argparse
import ast
import io
import pathlib
import re
import subprocess
import sys
import tarfile
import tokenize
from typing import NamedTuple

TOOLS = pathlib.Path(__file__).resolve().parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
import vacuous_guard_audit as vga  # noqa: E402  -- the function walk every audit here shares

ROOT = pathlib.Path(__file__).resolve().parent.parent

#: The four files #134 names, in its own order.
AUDIT_FILES = (
    "model/audio_measure.py",
    "tools/run_case.py",
    "tools/refprofile.py",
    "model/reference_rigs.py",
)

#: Every function where audio ENTERS from outside this repository's own
#: arithmetic, and must therefore be proven to be numbers before anything
#: compares it with anything. `(file, qualified name, why it is a boundary)`.
#:
#: A function is on this list because audio arrives in it that nothing here
#: computed -- a WAV off the disk, a plugin host's buffer, a caller's array.
#: It is NOT on this list merely because it touches audio: `prepare()` is here
#: because every metric's input passes through it, `window()` is not, because
#: `prepare()` already vouched for what it slices.
BOUNDARIES = (
    ("model/audio_measure.py", "_as_float",
     "every estimator in the module ingests its signal through this one "
     "function; `finite=False` is the explicit, commented opt-out for the "
     "response-curve callers whose dB axis legitimately holds -inf/NaN"),
    ("tools/refprofile.py", "load_clip",
     "the frozen reference profile's reader -- #133's seven-check miss"),
    ("tools/refprofile.py", "write_clip",
     "the FREEZE side of the same cache: a hash vouches for the bytes, so "
     "non-finite audio must never become the bytes"),
    ("tools/refprofile.py", "render",
     "the decision to accept a plugin's clip INTO the profile, where the "
     "level floor it sits beside is the guard a NaN is invisible to"),
    ("tools/run_case.py", "load_reference",
     "the external reference corpus reader (a WAV this repository did not write)"),
    ("tools/run_case.py", "prepare",
     "both sides of every drum comparison pass through here before any metric "
     "sees them; it is the render path's boundary as well as the reference's"),
    ("model/reference_rigs.py", "render_midi_note",
     "the plugin host's output buffer -- a plugin in a wrong state (unlicensed, "
     "un-set-up, denormal-blowup) is exactly where non-finite audio comes from"),
)

#: What a refusal looks like in this repository.
REFUSAL_CALLS = ("_fail", "Refused", "KitRefused", "InsufficientEvidence",
                 "NonFiniteAudio", "level_refusal")

#: Forced to False by a NaN on either side.
FALSE_ON_NAN = (ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Eq, ast.Is)
#: Forced to True by a NaN on either side.
TRUE_ON_NAN = (ast.NotEq, ast.IsNot)

#: Operand text that means "this cannot be a NaN": a count, a length, an index.
#: A HEURISTIC, for filtering the triage list only. Nothing is decided by it.
INT_DOMAIN_HINTS = ("len(", ".size", ".sum()", "int(", ".shape", "count_nonzero",
                    "n_", "_n", "len ", "nbins", "idx", "index")


class _Verdict:
    OPEN = "fails-open"
    CLOSED = "fails-closed"
    UNKNOWN = "indeterminate"


def _nan_value(node: ast.expr):
    """Three-valued evaluation of `node` with every ordering comparison forced
    to the value a NaN gives it. Returns True, False or None (unknown).

    This is the whole analysis, and it is small on purpose: the question "does
    this guard refuse when its comparisons are NaN-False" is a question about
    boolean structure, and boolean structure is exactly what an AST has."""
    if isinstance(node, ast.Compare):
        if any(isinstance(op, TRUE_ON_NAN) for op in node.ops):
            return True
        if all(isinstance(op, FALSE_ON_NAN) for op in node.ops):
            return False
        return None
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        v = _nan_value(node.operand)
        return None if v is None else (not v)
    if isinstance(node, ast.BoolOp):
        vals = [_nan_value(v) for v in node.values]
        if isinstance(node.op, ast.And):
            if any(v is False for v in vals):
                return False
            return True if all(v is True for v in vals) else None
        if any(v is True for v in vals):
            return True
        return False if all(v is False for v in vals) else None
    return None


def _refuses(body: list[ast.stmt]) -> bool:
    """Does this `if` body refuse, rather than fall through?"""
    for stmt in body:
        if isinstance(stmt, ast.Raise):
            return True
        if isinstance(stmt, ast.Return) and isinstance(stmt.value, ast.Call):
            fn = stmt.value.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            if name in REFUSAL_CALLS:
                return True
        # `return REFUSED, [...]` -- refprofile's verify() shape
        if isinstance(stmt, ast.Return) and isinstance(stmt.value, ast.Tuple):
            first = stmt.value.elts[0] if stmt.value.elts else None
            if isinstance(first, ast.Name) and first.id == "REFUSED":
                return True
    return False


class _Guard:
    def __init__(self, path: str, lineno: int, verdict: str, text: str, func: str):
        self.path, self.lineno, self.verdict, self.text, self.func = (
            path, lineno, verdict, text, func)

    @property
    def int_domain(self) -> bool:
        return any(h in self.text for h in INT_DOMAIN_HINTS)

    def __str__(self) -> str:
        mark = " [int?]" if self.int_domain else ""
        return f"{self.path}:{self.lineno} {self.func}: if {self.text}{mark}"


def _enclosing(tree: ast.Module) -> dict[int, str]:
    """line number -> qualified function name, for every line in a function."""
    out: dict[int, str] = {}

    def walk(node, prefix=""):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = f"{prefix}{child.name}"
                for ln in range(child.lineno, (child.end_lineno or child.lineno) + 1):
                    out.setdefault(ln, name)
                walk(child, prefix=f"{name}.")
            elif isinstance(child, ast.ClassDef):
                walk(child, prefix=f"{prefix}{child.name}.")
            else:
                walk(child, prefix=prefix)

    walk(tree)
    return out


def audit_file(rel: str, root: pathlib.Path = ROOT) -> list[_Guard]:
    """Every refusal guard in one file, classified."""
    src = (root / rel).read_text(encoding="utf-8")
    tree = ast.parse(src)
    where = _enclosing(tree)
    guards: list[_Guard] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.If) or not _refuses(node.body):
            continue
        if not any(isinstance(n, ast.Compare) for n in ast.walk(node.test)):
            continue
        v = _nan_value(node.test)
        verdict = (_Verdict.CLOSED if v is True
                   else _Verdict.OPEN if v is False else _Verdict.UNKNOWN)
        guards.append(_Guard(rel, node.test.lineno, verdict,
                             ast.unparse(node.test), where.get(node.lineno, "<module>")))
    return guards


#: The names `finite_checks` counts, as a per-FILE test as well: does this file
#: assert finiteness ANYWHERE? It says nothing about whether the call sits before a
#: given comparison, which is what the per-site verdicts below are for.
FINITENESS_NAMES = ("isfinite", "require_finite", "nonfinite_report", "isnan", "isinf")


def finite_checks(rel: str, root: pathlib.Path = ROOT) -> dict[str, int]:
    """qualified function name -> how many finiteness assertions its own body
    makes. A call to a helper whose NAME says finite counts, because that is
    how the boundary check is meant to be written."""
    src = (root / rel).read_text(encoding="utf-8")
    tree = ast.parse(src)
    where = _enclosing(tree)
    out: dict[str, int] = {}
    for node in ast.walk(tree):
        text = ""
        if isinstance(node, ast.Call):
            text = ast.unparse(node.func)
        if not text:
            continue
        if any(k in text for k in FINITENESS_NAMES):
            fn = where.get(node.lineno, "<module>")
            out[fn] = out.get(fn, 0) + 1
    return out


def boundary_gaps(root: pathlib.Path = ROOT) -> list[str]:
    """Which registered boundaries have NO finiteness assertion of their own.

    This is `--check`'s entire content, and the reason it is narrow: it does not
    guess where audio enters, it asserts that the places someone WROTE DOWN as
    entry points still check. Adding a reader without registering it here is a
    gap this cannot see -- which is why the dynamic property test exists too."""
    gaps = []
    by_file: dict[str, dict[str, int]] = {}
    for rel, func, _why in BOUNDARIES:
        if rel not in by_file:
            by_file[rel] = finite_checks(rel, root)
        if not by_file[rel].get(func):
            gaps.append(f"{rel}::{func} has no finiteness assertion in its own body")
    return gaps


# ---------------------------------------------------------------------------
# #1929: every comparison a NaN walks through, and what was decided about each
# ---------------------------------------------------------------------------
#
# `audit_file` above classifies REFUSAL guards by their boolean shape. This is
# the other half of the class: the two comparison FORMS a NaN passes through
# wherever they appear, in any file, whether or not they guard a refusal --
# filed as a triage list by #1870's closing search and decided site by site.
#
# The search is mechanical and the verdicts are not. A site is one LINE
# matching one of two shapes (#1929's regexes, verbatim):
#
#   A   abs\(.* - .*\)\s*>\s*[\w.]+      the comparison is False for a NaN
#   B   >\s*[A-Za-z_][\w.]*BOUND\b         the comparison is False for a NaN
#
# A match that begins inside a string literal is not a site: #1870's own
# docstring was the filed count's 71st line, and this file's table quotes every
# line it triages. `#` comment lines are skipped, as in the filed method.
#
# `SITE_VERDICTS` below is the triage, keyed by the line's text, so that
# EDITING a comparison -- not merely moving it -- asks for a fresh verdict.
#
# What `--sites --check` asserts, and what it does not. It asserts that the
# table is COMPLETE (every in-scope site has a verdict), FRESH (every verdict
# still names a site) and that every control a verdict names exists as a test.
# It cannot check that a verdict is TRUE: reading the operands is the human
# half, exactly as for `audit_file`. Each citation is a `file:line` a reader
# can check in seconds.
#
# Scope is #1929's: non-test .py under tools/, model/ and fpga/, excluding
# tools/probes/. Test files and tools/probes/ are counted and listed, not gated.
#
# BLIND SPOTS, stated rather than left to be found. The shapes are line
# regexes: a comparison wrapped across lines is not enumerated, and neither is
# a name that is exactly `BOUND` (shape B needs a character before the suffix)
# or a lowercase `..._bound`. Measured on origin/main @ dd0449e0, that hides
# nothing in scope: no such line outside test files and tools/probes/. The
# fails-closed form `not (abs(a - b) <= tol)` is not a site because it is True
# for a NaN; `max()` / `min()` is #1928's shape. A file that does not tokenise
# is scanned for `#` comments only, and is named in the output when that
# happens.

SHAPE_A = re.compile(r"abs\(.* - .*\)\s*>\s*[\w.]+")
SHAPE_B = re.compile(r">\s*[A-Za-z_][\w.]*BOUND\b")
SHAPES = (("A", SHAPE_A), ("B", SHAPE_B))
SITE_ROOTS = ("tools", "model", "fpga")

REACHABLE = "REACHABLE"
UNREACHABLE = "UNREACHABLE"
UNDETERMINED = "UNDETERMINED"
VERDICTS = (REACHABLE, UNREACHABLE, UNDETERMINED)


def _v(verdict: str, why: str, control: str | None = None) -> tuple:
    return (verdict, why, control)

#: The triage. `{_v}` is (verdict, why, control): `why` cites the path a NaN takes or
#: the guard that stops it, by `file:line` on the tree the verdict was written
#: against; `control` is `file::test`, the injected-NaN test that is red without the
#: assertion now in front of the comparison. A REACHABLE site with no `control` is
#: one still open -- either a repair on another branch or the #1929 follow-up.
#:
#: 71 sites on origin/main @ dd0449e0, and the same 71 at e2d1bae7 (non-test, outside tools/probes/):
#: 18 REACHABLE (6 repaired in #1929, 3 with a repair already
#: on another branch, 9 filed as one follow-up), 24 UNREACHABLE, 29 UNDETERMINED.
#:
#: WRONG BEFORE RIGHT (71 sites): 0 were first judged unreachable and then
#: found reachable -- the direction that hides a defect. Two were first judged
#: REACHABLE and then found unreachable: `tools/werner_fig4.py` L329 and L332, read
#: from `parse_content`'s `float(tok)` before `load_pdf`'s hash pin was read.
#: A path was also narrowed, not flipped: at `tools/cymbal_vca_clip.py` L999-L1008
#: only a NaN in FIRST position reaches the comparison -- `min()` / `max()` drop
#: any other NaN while `n` still counts it, which is #1928's shape and is in the
#: follow-up.
SITE_VERDICTS = {
    # --- REACHABLE, repaired here: an assertion names the field before the comparison (6) ---
    ('model/drift_probe.py', 'A',
     'if prev[n] is not None and v is not None and abs(v - prev[n]) > 1e-6:'):
        _v(REACHABLE,
           ("_run_tree() json.loads a historical tree's own stdout, where the driver prints "
           'json.dumps(float(v)). A NaN probe value arrives as NaN and the comparison is False '
           'on BOTH the finite->NaN and the NaN->finite transition, so neither is marked MOVED '
           '(measured).'),
           control='model/test_drift_probe.py::test_a_non_finite_probe_value_is_n_a_not_a_number'),
    ('tools/cymbal_vca_clip.py', 'A', 'if abs(float(got) - want) > 0.011:'):
        _v(REACHABLE,
           ("targets() reads m-origin.json's gap.bands.M.<key> through float(). Measured: a NaN "
           'in any of the four keys passes the drift guard and the file is recorded under '
           'verified_against.'),
           control='tools/test_cymbal_vca_clip.py::'
                  'test_targets_refuses_a_non_finite_m_origin_figure'),
    ('tools/cymbal_vca_clip.py', 'A',
     'if abs(per[T_RHO_WINDOW]["min"] - T_RHO_808_MIN) > 0.0011:'):
        _v(REACHABLE,
           ("targets() takes min() over low-tail.json's windows.<w>.fischer.*.rho.M.-10. "
           'Measured: a NaN that is the FIRST reading of the compared window leaves min = max '
           '= NaN and both guards pass.'),
           control='tools/test_cymbal_vca_clip.py::test_targets_refuses_a_non_finite_low_tail_rho'),
    ('tools/cymbal_vca_clip.py', 'A',
     'if abs(per[T_RHO_WINDOW][key] - RHO_M_808_RANGE[i]) > 0.0011:'):
        _v(REACHABLE,
           ('the max() half of the site above: the same first-position NaN leaves max = NaN and '
           'passes.'),
           control='tools/test_cymbal_vca_clip.py::test_targets_refuses_a_non_finite_low_tail_rho'),
    ('tools/cymbal_vca_clip.py', 'A', 'if abs(allmin - T_RHO_808_MIN_ANY_WINDOW) > 0.0011:'):
        _v(REACHABLE,
           ('targets() takes min() over the per-window minima. Measured: a NaN first in ANY '
           "window's readings leaves allmin = NaN and this passes."),
           control='tools/test_cymbal_vca_clip.py::'
                  'test_targets_refuses_a_non_finite_rho_in_any_window'),
    ('tools/cymbal_vca_drive.py', 'A',
     'drift = {b: round(got[b] - want[b], 3) for b in want if abs(got[b] - want[b]) > 0.005}'):
        _v(REACHABLE,
           ("balance_targets() reads balance.json's gap_db.<band>.gap_db through float() and "
           'compares it with nothing in between; json.loads admits a NaN literal and float() '
           "the string 'nan'. Measured: a NaN in either band is returned as data; +-inf is "
           'caught by the drift guard.'),
           control='tools/test_cymbal_vca_drive.py::test_balance_targets_refuses_a_non_finite_gap'),
    # --- REACHABLE, repair already on another branch (3) ---
    ('tools/cymbal_band_balance.py', 'A',
     'for k, v in peak_db.items() if abs(v - want_peak[k]) > FILTER_TOL_DB'):
        _v(REACHABLE,
           ('filter_record(): no finiteness call in this function. Repair in flight: #1837 (PR '
           '#1871).')),
    ('tools/cymbal_band_balance.py', 'A',
     'for k, v in pass_db.items() if abs(v - want_pass[k]) > FILTER_TOL_DB'):
        _v(REACHABLE,
           ('filter_record(): no finiteness call in this function. Repair in flight: #1837 (PR '
           '#1871).')),
    ('tools/cymbal_band_balance.py', 'A', 'and abs(entry["hz"] - CENTRE_HZ[band]) > 1e-6:'):
        _v(REACHABLE,
           ('schematic_tone_term(): no finiteness call in this function. Repair in flight: '
           '#1870 (PR #1950), which asserts hz finite a few lines above.')),
    # --- REACHABLE, filed as one follow-up (#1994) (9) ---
    ('tools/check_m1a_selection.py', 'A',
     'if raw[0] is None or abs(raw[0] - m) > 1e-5 or abs(raw[1] - r) > 1e-5:'):
        _v(REACHABLE,
           ("Follow-up: #1994. raw[0] is the record's properties['Envelope attack'].value, "
           'loaded at L191 through manifest.load_json_refused -- a json.loads that refuses '
           "decode errors only; a NaN passes 'record attack reading is not its own worst "
           "event'.")),
    ('tools/diagnose_m1a_harmonic_phase.py', 'A', 'if abs(mine - v) > 1e-6:'):
        _v(REACHABLE,
           ('Follow-up: #1994. v is a scored error out of the M1A record (RECORD, L523); a NaN '
           "passes 'scored error does not reproduce the record'.")),
    ('tools/external_claim.py', 'A', 'if got is None or abs(got - want) > 1e-6:'):
        _v(REACHABLE,
           ('Follow-up: #1994. got is a value the plugin host read back (a caller argument); a '
           "NaN passes the pin readback. The same function's level_dbfs < min_level_dbfs four "
           'lines above (L84) is False for a NaN level and is outside both shapes. No render '
           'harness calls assert_readback yet; only its test does.')),
    ('tools/measure_m1a_drive.py', 'A',
     'if prop.get("valid") and abs(base["properties"][name]["value"] - prop["value"]) > 1e-6:'):
        _v(REACHABLE,
           ("Follow-up: #1994. prop['value'] is read from results/M1A.json (RECORD); a NaN "
           "passes 'baseline ... does not reproduce results/M1A.json'.")),
    ('tools/measure_m1a_drive.py', 'A',
     ('if abs(mp["partials"][h]["range_db"] - '
     'phase["summaries"]["model"]["partials"][h]["range_db"]) > 1e-9:')):
        _v(REACHABLE,
           ("Follow-up: #1994. phase['summaries']['model']['partials'][h]['range_db'] is read "
           "from the phase report JSON; a NaN passes 'does not reproduce #219's model curves'.")),
    ('tools/measure_m5_operating_level.py', 'A',
     'if abs(abs(c["Gain"]) - abs(base["Gain"])) > 0.5:'):
        _v(REACHABLE,
           ("Follow-up: #1994. select() compares rows[str(d)]['M5A']['errors']['Gain'] out of a "
           "JSON rows file (json.loads, L185) with the baseline's; nothing asserts finiteness "
           'before it.')),
    ('tools/measure_m5_saw_drive.py', 'A', 'if abs(abs(c["Gain"]) - abs(be["Gain"])) > 0.5:'):
        _v(REACHABLE,
           ('Follow-up: #1994. the same expression on rows read from JSON at L182 (the L189 '
           'branch measures in process).')),
    ('tools/pekonen_coloration_probe.py', 'A', 'if abs(rec - now) > STALE_OURS_DB:'):
        _v(REACHABLE,
           ("Follow-up: #1994. rec is the recorded 'ours' harmonic level out of the reference "
           'JSON (load_reference, L99); a NaN passes the stale-ours guard and the row is '
           'emitted.')),
    ('tools/sensitivity.py', 'A', 'elif abs(recorded[value] - objective) > 0.05:'):
        _v(REACHABLE,
           ('Follow-up: #1994. recorded[value] is float(points[].objective) out of '
           'docs/sensitivity/*.json (load_records, json.loads); a NaN passes the transcription '
           'check. refuse_unmeasurable_objective checks the MEASURED column only.')),
    # --- UNREACHABLE: the entry cites the guard (24) ---
    ('fpga/ext_io_timing.py', 'A', 'if abs(want - got) > 5e-4:'):
        _v(UNREACHABLE,
           ('token grammar admits no nan/inf spelling: got is float() of a `-?[\\d.]+` capture '
           '(_OUTPUT_DELAY_RE, L257); want is a code constant.')),
    ('fpga/verify_live_midi.py', 'B', 'if sim.evq_peak > C.DEVICE_QUEUE_BOUND:'):
        _v(UNREACHABLE,
           ("integer, and the one shape-B site: evq_peak is uart_device_sim's event-queue "
           'high-water mark, initialised to 0 and raised by max() over a count '
           '(uart_device_sim.py:146, L328).')),
    ('fpga/verify_pads_top.py', 'A', 'elif abs(got_ready - exp["ready"]) > 4:'):
        _v(UNREACHABLE,
           ("integer: got_ready is int(m.group(3)) (L401) and exp['ready'] is "
           "boot_ready_cycle's cycle count.")),
    ('fpga/verify_pads_top.py', 'A', 'if kind == "boot" and abs(g[0] - f) > 1:'):
        _v(UNREACHABLE,
           ("integer: g[0] comes from _ints() (L381) and f from boot_apply_frames' integer "
           'frame arithmetic.')),
    ('fpga/verify_uart_bridge.py', 'A', 'if abs(want[0] - got[0]) > tol:'):
        _v(UNREACHABLE,
           ("integer: got[0] is int(row[0]) (L713) and want[0] is the contract's integer "
           'apply_frame.')),
    ('model/discrimination_features.py', 'A',
     'assert abs(366.4 / p - round(366.4 / p)) > 0.02, (T, p)'):
        _v(UNREACHABLE,
           ('raises first: round() raises ValueError on a NaN before the comparison is '
           'evaluated.')),
    ('model/ladder_headroom.py', 'A', 'if int(np.abs(rom[idx] - want).max()) > 1:'):
        _v(UNREACHABLE,
           ('raises first: int() of the deviation raises ValueError on a NaN before the '
           'comparison is evaluated.')),
    ('model/tom_drop_docs.py', 'A', 'if abs(law["excess_at_reference"] - shipped) > 5e-4:'):
        _v(UNREACHABLE,
           ('token grammar admits no nan/inf spelling: float() of a `[\\d.]+` capture (_EXCESS, '
           'L113); the other operand is a drums_fx constant.')),
    ('model/tom_drop_docs.py', 'A', 'if abs(stated - shipped_value) > 5e-3:'):
        _v(UNREACHABLE,
           ('token grammar admits no nan/inf spelling: float() of a `[\\d.]+` capture (_A0_ROW '
           '/ _G_ROW, L114-115); the other operand is a drums_fx constant.')),
    ('model/tom_drop_docs.py', 'A', 'if abs(v_shipped - dx.TOM_DROP_RATIO) > 5e-4:'):
        _v(UNREACHABLE,
           ('token grammar admits no nan/inf spelling: float() of a `[\\d.]+` capture '
           '(_SHIPPED_ROW, L117); the other operand is a drums_fx constant.')),
    ('model/tom_pitch_probe.py', 'A',
     'if np.abs(x).max() >= 1.0 - 1e-9 or int(np.sum(np.abs(x) > 0.999)) > 2:'):
        _v(UNREACHABLE,
           ('the comparison the shape finds is int(np.sum(...)) > 2, an integer count. '
           "Follow-up: #1994. the same line's clip guard np.abs(x).max() >= 1.0 - 1e-9 and "
           "L318's pk <= 0 are both False for a NaN, and read_wav passes float-sample WAV data "
           'through; whether a NaN-bearing file reaches a reported number was not determined '
           '(a synthetic one was REFUSED later for an unrelated reason).')),
    ('tools/clap_d12a_probe.py', 'A', '"caught": bool(abs(v - want) > KNOWN_ANSWER_TOL_DB)}'):
        _v(UNREACHABLE,
           ('direction: a NaN mutant reads caught = False, and the gate needs every mutant '
           'caught (L370-372): red, not green.')),
    ('tools/compare_ladder_candidates.py', 'A',
     'abs(r["moving_tanh"] - r["held_tanh"]) > 1e-9 for r in good),'):
        _v(UNREACHABLE,
           ('count: moving_tanh and held_tanh are operation counts out of last_cost, not '
           'measurements.')),
    ('tools/measure_m5a_pulse_duty.py', 'A', 'if abs(len(ref_segment) - len(pcm)) > 128:'):
        _v(UNREACHABLE,
           'integer: len() of two arrays.'),
    ('tools/measure_m5a_signal_path.py', 'A', 'if abs(len(ref_segment) - len(pcm)) > 128:'):
        _v(UNREACHABLE,
           'integer: len() of two arrays.'),
    ('tools/measure_promoted_bands.py', 'A',
     'got == got and abs(100.0 * (got / f_bar - 1.0)) > GLIDE_NOT_THE_MEAN_PCT,'):
        _v(UNREACHABLE,
           ('the same expression tests got == got first, which is False for a NaN and '
           'short-circuits the comparison.')),
    ('tools/measure_repeatability.py', 'A',
     'while k > 0 and abs(m[k - 1]) > frac * pk and j - k < 2000:'):
        _v(UNREACHABLE,
           ("the shape's subtraction is the index, not an operand, and the operand is finite by "
           'construction: every recording reaches refine_onset through load(), which refuses a '
           'non-finite peak with `not (0.0 < pk <= 1.0)` (L146); the other callers pass loaded '
           'or synthesised audio.')),
    ('tools/measure_repeatability.py', 'A',
     'if len(gaps) and (np.abs(gaps - 1.0) > BEAT_TOL).any():'):
        _v(UNREACHABLE,
           ('integer: gaps are integer onset-index differences over a finite beat; a non-finite '
           'beat raises in round(len(m) / beat) (L1053) first.')),
    ('tools/mono_m5a_score.py', 'A', 'if abs(len(ref_segment) - len(ours)) > 128:'):
        _v(UNREACHABLE,
           'integer: len() of two arrays.'),
    ('tools/score_m5a_i2s.py', 'A',
     'or abs(float(match.group(3)) - 100.0 * effective_duty) > 0.01):'):
        _v(UNREACHABLE,
           ('token grammar admits no nan/inf spelling: float() of a `(\\d+(?:\\.\\d+)?)` '
           'capture (L78-81); the other operand is DUTY / CYCLE constants.')),
    ('tools/score_m5a_i2s.py', 'A',
     'and abs(controls["saw_volume_correction_db"] - saw_volume_correction_db) > 0.00001):'):
        _v(UNREACHABLE,
           ('token grammar admits no nan/inf spelling: float() of a `[+-]?\\d+(?:\\.\\d+)?` '
           'capture (L79-80); the other operand is a caller constant.')),
    ('tools/werner_fig4.py', 'A', 'if (abs(y0 - y1) < 1e-6 and abs(x1 - x0) > 0.9 * w'):
        _v(UNREACHABLE,
           ('hash pin: the content stream exists only from the PDF load_pdf admits by SHA-256 '
           '(W14B_SHA256, L907-925) before curves() / level_tilt() parse it. WRONG BEFORE '
           "RIGHT: first judged reachable from parse_content's float(tok) alone, which accepts "
           "'nan' and '1e999'.")),
    ('tools/werner_fig4.py', 'A', 'if (abs(x0 - x1) < 1e-6 and abs(y1 - y0) > 0.9 * h'):
        _v(UNREACHABLE,
           ('hash pin: the content stream exists only from the PDF load_pdf admits by SHA-256 '
           '(W14B_SHA256, L907-925) before curves() / level_tilt() parse it. WRONG BEFORE '
           "RIGHT: first judged reachable from parse_content's float(tok) alone, which accepts "
           "'nan' and '1e999'.")),
    ('tools/werner_fig4.py', 'A', 'if abs(db - round(db / db_step) * db_step) > db_tol:'):
        _v(UNREACHABLE,
           ('raises first: round(db / db_step) raises ValueError on a NaN db and OverflowError '
           'on an inf before the comparison is evaluated; db_step and db_tol are finite by '
           'construction (integer tick labels).')),
    # --- UNDETERMINED: the entry says what would settle it (29) ---
    ('fpga/verify_sd_dac.py', 'A',
     'if known_inband is None or abs(known_inband - 70.0) > 0.5:'):
        _v(UNDETERMINED,
           ('ordering not checked: this file contains a finiteness call elsewhere, and whether '
           "it vouches for this comparison's operands was not traced. The file's one call is "
           'not in check_estimator.')),
    ('model/audio_measure.py', 'A', 'if abs(b - top) > MAX_PLATEAU_EXTRAPOLATION_DB:'):
        _v(UNDETERMINED,
           ('ordering not checked: this file contains a finiteness call elsewhere, and whether '
           "it vouches for this comparison's operands was not traced.np.isfinite(g) selects "
           "the curve's points at L2180, in the same function; whether b and top derive only "
           'from that selection was not traced.')),
    ('model/audio_measure.py', 'A', 'return float(np.mean(np.abs(c - mid) > 0.5 * half))'):
        _v(UNDETERMINED,
           ('ordering not checked: this file contains a finiteness call elsewhere, and whether '
           "it vouches for this comparison's operands was not traced.The deviation abs(c - "
           'mid) > 0.5 * half counts a NaN as not deviating.')),
    ('model/audio_measure.py', 'A', 'if min(abs(d - duty), abs((1 - d) - duty)) > duty_tol:'):
        _v(UNDETERMINED,
           ('ordering not checked: this file contains a finiteness call elsewhere, and whether '
           "it vouches for this comparison's operands was not traced. A NaN duty makes the "
           'comparison False, so the guard cannot fire; no finiteness call sits inside '
           'waveform_matches.')),
    ('model/bd_excitation_probe.py', 'A',
     'if abs(ref[TARGET] - PUBLISHED_REF) > PUBLISHED_TOL:'):
        _v(UNDETERMINED,
           ('ordering not checked: this file contains a finiteness call elsewhere, and whether '
           "it vouches for this comparison's operands was not traced. The file's one call is "
           'not in check_preconditions.')),
    ('model/bd_excitation_probe.py', 'A',
     'if abs(ours[TARGET] - PUBLISHED_OURS) > PUBLISHED_TOL:'):
        _v(UNDETERMINED,
           ('ordering not checked: this file contains a finiteness call elsewhere, and whether '
           "it vouches for this comparison's operands was not traced. The file's one call is "
           'not in check_preconditions.')),
    ('model/discrimination_features.py', 'A', 'if abs(x - y) > tol:'):
        _v(UNDETERMINED,
           ('ordering not checked: this file contains a finiteness call elsewhere, and whether '
           "it vouches for this comparison's operands was not traced. _rate_gaps() takes both "
           'operands from its callers; no finiteness call sits inside it.')),
    ('model/discrimination_features.py', 'A',
     'if abs(sa[k] - sb[k]) / max(abs(sa[k]), abs(sb[k]), 1e-6) > 0.12:'):
        _v(UNDETERMINED,
           ('ordering not checked: this file contains a finiteness call elsewhere, and whether '
           "it vouches for this comparison's operands was not traced. A self-check function "
           'inside a non-test file.')),
    ('model/discrimination_features.py', 'A',
     'if abs(x - y) / max(abs(x), abs(y), 1.0) > 1e-6]'):
        _v(UNDETERMINED,
           ('ordering not checked: this file contains a finiteness call elsewhere, and whether '
           "it vouches for this comparison's operands was not traced. A self-check function "
           'inside a non-test file.')),
    ('model/reference_rigs.py', 'A', 'if abs(held - float(val)) > tol:'):
        _v(UNDETERMINED,
           ('ordering not checked: this file contains a finiteness call elsewhere, and whether '
           "it vouches for this comparison's operands was not traced.")),
    ('model/reference_rigs.py', 'A',
     'if abs(landed - best["raw_held"]) > self.p.raw_tolerance(idx):'):
        _v(UNDETERMINED,
           ('ordering not checked: this file contains a finiteness call elsewhere, and whether '
           "it vouches for this comparison's operands was not traced.")),
    ('model/reference_rigs.py', 'A',
     'if abs(landed - chosen["raw_held"]) > self.p.raw_tolerance(idx):'):
        _v(UNDETERMINED,
           ('ordering not checked: this file contains a finiteness call elsewhere, and whether '
           "it vouches for this comparison's operands was not traced.")),
    ('model/rig_qualification.py', 'A', 'if abs(ratio / want - 1.0) > tol:'):
        _v(UNDETERMINED,
           ('ordering not checked: this file contains a finiteness call elsewhere, and whether '
           "it vouches for this comparison's operands was not traced.")),
    ('model/sound_report.py', 'A',
     'if last is None or abs(float(last) - float(value)) > 1e-9:'):
        _v(UNDETERMINED,
           ('ordering not checked: this file contains a finiteness call elsewhere, and whether '
           "it vouches for this comparison's operands was not traced.last and value are "
           "float()s of a lock's changelog entry; a NaN here would pass the lock comparison.")),
    ('tools/analyse_m1a_phase_cycle.py', 'A',
     'if abs(model["drift_hz"] - drift) > DRIFT_TOL * abs(drift):'):
        _v(UNDETERMINED,
           ('computed in process, with no record or file between the computation and the '
           'comparison; whether the estimator can emit a non-finite value was not analysed. '
           "Both drifts come from this file's measure().")),
    ('tools/confirm_m5_saw_drive.py', 'A',
     'if abs(c["intended_dbfs"] - b["intended_dbfs"]) > 1.5:'):
        _v(UNDETERMINED,
           ('computed in process, with no record or file between the computation and the '
           'comparison; whether the estimator can emit a non-finite value was not analysed. '
           'point() goes through require_measured, which checks PRESENCE only '
           '(mono_artifact_probe.py:355).')),
    ('tools/cymbal_tone_render.py', 'A',
     'if (want is None) != (got is None) or (want is not None and abs(want - got) > 1e-6):'):
        _v(UNDETERMINED,
           ('computed in process, with no record or file between the computation and the '
           'comparison; whether the estimator can emit a non-finite value was not analysed. '
           "tn.chosen()'s poles, after tn.check() passes; no path to a NaN was constructed or "
           'excluded.')),
    ('tools/cymbal_tone_render.py', 'A', 'if abs(a - b) > 1e-3:'):
        _v(UNDETERMINED,
           ('computed in process, with no record or file between the computation and the '
           'comparison; whether the estimator can emit a non-finite value was not analysed. '
           'tn.tone_gain_db(), after tn.check() passes; no path to a NaN was constructed or '
           'excluded.')),
    ('tools/diagnose_m1a_harmonic_phase.py', 'A',
     'if abs(wrap(iso["psi_deg"] - psi_ref[i])) > 5.:'):
        _v(UNDETERMINED,
           ('computed in process, with no record or file between the computation and the '
           'comparison; whether the estimator can emit a non-finite value was not analysed. '
           "iso['psi_deg'] is relative_phase() on the model's own render.")),
    ('tools/f1_level_capture.py', 'A', 'if abs(low - frozen_open_low) > OPEN_GUARD_DB:'):
        _v(UNDETERMINED,
           ('ordering not checked: this file contains a finiteness call elsewhere, and whether '
           "it vouches for this comparison's operands was not traced.y is finite-checked at "
           'L195, in the same function; whether low derives only from y was not traced.')),
    ('tools/measure_m5_pulse_cutoff.py', 'A',
     'if abs(abs(ce["Gain"]) - abs(be["Gain"])) > 0.5:'):
        _v(UNDETERMINED,
           ('computed in process, with no record or file between the computation and the '
           'comparison; whether the estimator can emit a non-finite value was not analysed. '
           "rows[cut]['M5A']['errors'] comes from phrase().")),
    ('tools/mono_artifact_probe.py', 'A', 'return abs(dirty - clean) > MOVE_DB'):
        _v(UNDETERMINED,
           ('ordering not checked: this file contains a finiteness call elsewhere, and whether '
           "it vouches for this comparison's operands was not traced.")),
    ('tools/performance_suite.py', 'A', 'entered = i > 0 and abs(d[i - 1]) > knee'):
        _v(UNDETERMINED,
           ('ordering not checked: this file contains a finiteness call elsewhere, and whether '
           "it vouches for this comparison's operands was not traced.")),
    ('tools/phase_aware_m1a.py', 'A', 'if abs(have - want) > 1e-6:'):
        _v(UNDETERMINED,
           ('ordering not checked: this file contains a finiteness call elsewhere, and whether '
           "it vouches for this comparison's operands was not traced.")),
    ('tools/preparation_contract.py', 'A', 'if abs(lead - want) > LEAD_TOLERANCE_SAMPLES:'):
        _v(UNDETERMINED,
           ('ordering not checked: this file contains a finiteness call elsewhere, and whether '
           "it vouches for this comparison's operands was not traced.y is finite-masked at "
           'L127, in the same function; whether lead derives only from y was not traced.')),
    ('tools/preparation_contract.py', 'A',
     'abs(sa["lead_samples"] - sb["lead_samples"]) > LEAD_TOLERANCE_SAMPLES:'):
        _v(UNDETERMINED,
           ('ordering not checked: this file contains a finiteness call elsewhere, and whether '
           "it vouches for this comparison's operands was not traced.")),
    ('tools/stage_case.py', 'A', 'if abs(region["tau_ms"] - float(fit["tau_ms"])) > 1e-6:'):
        _v(UNDETERMINED,
           ('ordering not checked: this file contains a finiteness call elsewhere, and whether '
           "it vouches for this comparison's operands was not traced.The L378 guard asserts "
           "fit['tau_ms'] finite on the line before; whether region['ok'] vouches for "
           "region['tau_ms'] was not traced.")),
    ('tools/tone_stage_schematic.py', 'A',
     'p: ("MOVED" if abs(got[p] - clean[p]) > tol * scale else "BLIND")'):
        _v(UNDETERMINED,
           ('ordering not checked: this file contains a finiteness call elsewhere, and whether '
           "it vouches for this comparison's operands was not traced. The file's one call is "
           'not in sensitivity_control_matrix.')),
    ('tools/tone_stage_schematic.py', 'A', 'if abs(alpha - ALPHA_K1) > 1e-3:'):
        _v(UNDETERMINED,
           ('ordering not checked: this file contains a finiteness call elsewhere, and whether '
           "it vouches for this comparison's operands was not traced. The file's one call is "
           'not in check.')),
}


class Site(NamedTuple):
    path: str
    lineno: int
    shape: str
    text: str
    is_test: bool
    in_probes: bool

    @property
    def in_scope(self) -> bool:
        return not (self.is_test or self.in_probes)

    @property
    def key(self) -> tuple:
        return (self.path, self.shape, self.text)

    @property
    def label(self) -> str:
        """What a gap message quotes for the site."""
        return self.text


def is_test_path(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return (name.startswith("test_") or name.endswith("_test.py")
            or "conftest" in name or "/tests/" in path)


def _string_spans(src: str) -> list | None:
    """(start_row, start_col, end_row, end_col) of every string-literal token,
    docstrings included -- or None when the file does not tokenise."""
    spans = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.STRING or tok.type == FSTRING_MIDDLE:
                spans.append((*tok.start, *tok.end))
    except (tokenize.TokenError, SyntaxError, IndentationError, ValueError):
        return None
    return spans


#: `tokenize.FSTRING_MIDDLE` exists from Python 3.12; the audit must not depend on it.
FSTRING_MIDDLE = getattr(tokenize, "FSTRING_MIDDLE", -1)


def _inside(row: int, col: int, spans) -> bool:
    return any((sr, sc) <= (row, col) < (er, ec) for sr, sc, er, ec in spans)


def py_sources(ref: str | None = None, root: pathlib.Path = ROOT,
               roots: tuple = SITE_ROOTS) -> dict[str, str]:
    """path -> source for every .py under `roots`.

    `ref=None` reads the working tree. A ref is read with `git archive`, never
    from a checkout: a fact about main is read from origin/main (CLAUDE.md)."""
    if ref is None:
        out = {}
        for d in roots:
            for p in sorted((root / d).rglob("*.py")):
                out[p.relative_to(root).as_posix()] = p.read_text(
                    encoding="utf-8", errors="replace")
        return out
    if not _git(root, "rev-parse", "--verify", "--quiet", ref):
        raise SystemExit(f"REFUSED: {ref} is not a revision in {root}")
    # `git archive` refuses the whole call if one pathspec matches nothing, so
    # only the roots this tree has are asked for.
    have = [d for d in roots
            if _git(root, "ls-tree", "-d", "--name-only", ref, "--", d).strip()]
    if not have:
        return {}
    git = subprocess.run(["git", "-C", str(root), "archive", "--format=tar", ref,
                          *have], capture_output=True)
    if git.returncode != 0:
        raise SystemExit(f"REFUSED: git archive {ref} {' '.join(have)}: "
                         f"{git.stderr.decode(errors='replace').strip()[:200]}")
    out = {}
    with tarfile.open(fileobj=io.BytesIO(git.stdout)) as tar:
        for m in tar:
            if m.isfile() and m.name.endswith(".py"):
                out[m.name] = tar.extractfile(m).read().decode("utf-8", errors="replace")
    return out


def comparison_sites(sources: dict[str, str]) -> tuple[list[Site], list[str]]:
    """Every shape A / B line in the `.py` files of `sources`, and the files
    that did not tokenise."""
    sites: list[Site] = []
    untokenised: list[str] = []
    for path, src in sources.items():
        if not path.endswith(".py"):
            continue
        spans = _string_spans(src)
        if spans is None:
            untokenised.append(path)
            spans = []
        for i, line in enumerate(src.splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            for shape, rx in SHAPES:
                m = rx.search(line)
                if m and not _inside(i, m.start(), spans):
                    sites.append(Site(path, i, shape, " ".join(line.split()),
                                      is_test_path(path), path.startswith("tools/probes/")))
    return sites, untokenised


def _key_text(key: tuple) -> str:
    return "(" + ", ".join(repr(part) for part in key) + ")"


def triage_gaps(sites: list, root: pathlib.Path = ROOT, table: dict | None = None,
                table_name: str = "SITE_VERDICTS", what: str = "comparison") -> list[str]:
    """`--sites --check`'s entire content, and `--aggregates --check`'s: what a
    verdict table gets wrong about the tree. An empty list is the all-clear;
    anything else is a gap with its fix."""
    table = SITE_VERDICTS if table is None else table
    gaps = []
    live = {s.key for s in sites if s.in_scope}
    for s in sites:
        if s.in_scope and s.key not in table:
            gaps.append(f"UNTRIAGED {s.path}:{s.lineno} [{s.shape}] {s.label}\n"
                        f"    add an entry to tools/nan_guard_audit.py::{table_name} keyed\n"
                        f"    {_key_text(s.key)}")
    for key in sorted(table):
        if key not in live:
            gaps.append(f"STALE verdict names a line that is no longer a site: {key}\n"
                        f"    the {what} was edited, moved or deleted; re-triage or drop "
                        "the entry")
    for key, (verdict, _why, control) in sorted(table.items()):
        if verdict not in VERDICTS:
            gaps.append(f"BAD-VERDICT {verdict!r} (not one of {VERDICTS}): {key}")
        if control is None:
            continue
        f, _, name = control.partition("::")
        p = root / f
        if not p.is_file() or f"def {name}(" not in p.read_text(encoding="utf-8"):
            gaps.append(f"CONTROL-MISSING {control}, named by {key[0]} {key[1]}: the injected-NaN "
                        "control a REACHABLE verdict cites must exist")
    return gaps


def _git(root: pathlib.Path, *a: str) -> str:
    try:
        r = subprocess.run(["git", "-C", str(root), *a], capture_output=True,
                           text=True, timeout=20)
    except Exception:
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


def tree_label(ref: str | None, root: pathlib.Path = ROOT) -> str:
    """Which tree the numbers below describe, and whether it is main's. A count
    read from a stale checkout is indistinguishable from a current one."""
    if ref:
        return f"{ref} @ {_git(root, 'rev-parse', '--short=8', ref) or '?'}"
    head = _git(root, "rev-parse", "--short=8", "HEAD") or "?"
    dirty = _git(root, "status", "--porcelain", "--", *SITE_ROOTS)
    return f"working tree @ {head}" + (" (dirty)" if dirty else "")


def sites_main(gate: bool, ref: str | None, root: pathlib.Path = ROOT) -> int:
    sources = py_sources(ref, root)
    sites, untokenised = comparison_sites(sources)
    scoped = [s for s in sites if s.in_scope]
    by_verdict = {v: 0 for v in VERDICTS}
    files_with_call = {path for path, src in sources.items()
                       if any(k in src for k in FINITENESS_NAMES)}
    for s in scoped:
        by_verdict[SITE_VERDICTS[s.key][0]] += 1
    a = [s for s in sites if s.shape == "A"]
    print(f"NaN-blind comparison sites, read from {tree_label(ref, root)}")
    behind = _git(root, "rev-list", "--count", "HEAD..origin/main")
    if behind.isdigit() and int(behind) and ref is None:
        print(f"!! THIS TREE IS {behind} COMMITS BEHIND origin/main. The lines below describe "
              "that older tree, not main. Use --ref origin/main.")
    print(f"  shape A: {len(a)} lines ({sum(s.is_test for s in a)} in test files, "
          f"{sum(s.in_probes and not s.is_test for s in a)} in tools/probes/)   "
          f"shape B: {len(sites) - len(a)} lines")
    n_no = sum(1 for s in scoped if s.path not in files_with_call)
    n_yes = len(scoped) - n_no
    print(f"  in scope (non-test, outside tools/probes/): {len(scoped)} sites in "
          f"{len({s.path for s in scoped})} files = {n_yes} in files with a finiteness call "
          f"somewhere + {n_no} in files with none")
    print("  verdicts: " + ", ".join(f"{by_verdict[v]} {v}" for v in VERDICTS)
          + f"   table: {len(SITE_VERDICTS)} entries")
    for s in scoped:
        verdict, why, control = SITE_VERDICTS.get(s.key, ("<no verdict>", "", None))
        mark = " +" if control else ""
        print(f"{s.path}:{s.lineno} [{s.shape}] {verdict}{mark}  {why}")
    out = [s for s in sites if not s.in_scope]
    if out:
        print(f"\nnot in scope, not triaged ({len(out)}): "
              + " ".join(f"{s.path}:{s.lineno}" for s in out))
    if untokenised:
        print(f"\nDID NOT TOKENISE (scanned for # comments only): {', '.join(untokenised)}")
    print("\n'+' marks a REACHABLE site whose injected-NaN control exists. A verdict is a reading "
          "of the operands, not a proof: each cites its guard or its path.")
    if not gate:
        return 0
    gaps = triage_gaps(sites, root)
    for g in gaps:
        print(f"GAP      {g}")
    if gaps:
        print(f"\n{len(gaps)} gap(s) against tools/nan_guard_audit.py::SITE_VERDICTS. A comparison "
              "a NaN walks through was added or changed without a verdict.")
        return 1
    print("OK       every in-scope site has a verdict and every verdict names a site")
    return 0


# ---------------------------------------------------------------------------
# #2014: every builtin max() / min() a NaN can be dropped by, and what was
# decided about each
# ---------------------------------------------------------------------------
#
# The third shape. `max()` / `min()` keep their FIRST argument and replace it
# only when a later one compares greater (or smaller), and a NaN compares with
# nothing: a NaN that is not the first argument is DROPPED and the aggregate
# stays finite, merely smaller (or larger) than it should be. #1928 measured
# one: a non-finite figure-route bound in the decay band turned rule-bind's
# widest tone bound from 21.3 dB into 14.7 dB and the property PASSED against
# the smaller number. A NaN that IS the first argument is returned, which is
# #1929's shape one comparison later.
#
# A site is one call, found by an AST walk over `vacuous_guard_audit`'s
# function walk and keyed by its text, so EDITING a call -- not merely moving
# it -- asks for a fresh verdict:
#
#   * the call is to the BUILTIN `max` / `min`, a bare name: `np.max` and
#     friends propagate a NaN and are not this shape, and neither is a
#     `sorted(...)[0]`;
#   * at least one argument is not a literal, because a literal cannot be a NaN;
#   * it sits in a function under AGGREGATE_ROOTS whose name matches
#     AGGREGATE_NAME -- `vacuous_guard_audit.VERDICT_NAME`, widened as the
#     filing widened it -- or that is registered in AGGREGATE_REGISTRY. A nested
#     def is visited as its own function, never as part of the enclosing one.
#
# THE FILED COUNT, REPRODUCED, AND THE DIFFERENCE. #2014 filed 48 sites in 16
# files, 28 of them in 8 files with no finiteness call anywhere, from a walk
# whose population test -- read back off its own 48 lines -- was "the FIRST
# argument is a comprehension, an `x.values()` / `.items()`, a `list()` /
# `abs()` / `float()` call or a subscript". `_filed_population` is that test
# and the report prints it as a subtotal, so
# `--aggregates --ref origin/feature/issue-1928` reproduces 48 / 16 and 28 / 8
# at 52d756ac -- e2d1bae7 plus #1928's two commits, the tree the filing
# counted, 2026-11-28 -- and at 616b8d63, origin/main the day this was
# written: 48 sites in 16 files, 20 in the 8 files with a finiteness call
# and 28 in the 8 without. That test did not count a bare name, although the
# filing's method lists one, and a bare name is where most of this shape
# lives: a population held in a local (`max(err)`), a running accumulator's
# other operand (`worst = max(worst, abs(db - ref))`), a non-literal argument
# after a first one that is not a population (`max(math.log2(lo / p) ..., ...)`).
# The rule here counts them: with `rebalance` registered, 115 sites in 32
# files on each of those trees -- the filed 48, `rebalance`'s own `min()`
# (a first-argument population the filed walk missed only because nothing in
# the function's name matches), and 66 beyond both.
#
# WHAT THE GATE IS BLIND TO, stated rather than left to be found:
#
#   * THE NAME FILTER. A function that refuses but is named for neither a
#     verdict nor a property (`rebalance`, `measure`, `capture`) is not
#     visited unless registered, and the report counts what that hides on
#     every run under NOT GATED. The filter is the filed walk's; every function
#     is the audit's job (`vacuous_guard_audit.py`), not a per-site table's.
#   * An alias: `builtins.max`, `from builtins import max as biggest`, or a
#     local rebinding is not a bare `max` / `min`.
#   * `model/` and `fpga/`: the filing scanned `tools/` only, so the gate does.
#   * Whether a verdict is TRUE. Reading the operands is the human half, as it
#     is for `audit_file`; each entry cites the guard that stops the NaN or the
#     path it takes, by `file:line` on the tree the verdict was written against.

AGGREGATES = ("max", "min")
AGGREGATE_ROOTS = ("tools",)
AGGREGATE_NAME = re.compile(vga.VERDICT_NAME.pattern[:-1]
                            + r"|propert|score|report|summar|rank|decide)", re.I)
#: The filing's per-file heuristic, which lists `is_finite_number` as well.
AGGREGATE_FINITE_NAMES = FINITENESS_NAMES + ("is_finite_number",)
_POPULATION_VIEWS = ("values", "items")
_POPULATION_CALLS = ("list", "abs", "float")

#: Functions the name filter misses whose max() / min() still decides a number
#: that ships. `(file, qualified name, why it is verdict-shaped)`.
AGGREGATE_REGISTRY = (
    ("tools/cymbal_band_balance.py", "rebalance",
     "its min() is the anchor every band of the ablation is scaled by, and "
     "nothing in the name says so"),
)


def _filed_population(arg: ast.expr) -> bool:
    """The filed walk's population test, on the FIRST argument: a comprehension;
    `x.values()` / `x.items()`; `list()` / `abs()` / `float()`; a subscript.
    Not a bare name, which is the difference this module counts."""
    if isinstance(arg, (ast.GeneratorExp, ast.ListComp, ast.SetComp, ast.DictComp)):
        return True
    if isinstance(arg, ast.Call):
        if isinstance(arg.func, ast.Attribute) and arg.func.attr in _POPULATION_VIEWS:
            return True
        return isinstance(arg.func, ast.Name) and arg.func.id in _POPULATION_CALLS
    return isinstance(arg, ast.Subscript)


def _call_text(src: str, call: ast.Call) -> str:
    """The call as written, whitespace collapsed. Not `ast.unparse`: its quote
    style differs between Python versions, and a version bump would turn every
    key in the table stale."""
    segment = ast.get_source_segment(src, call) or ast.unparse(call)
    return " ".join(segment.split())


class Aggregate(NamedTuple):
    path: str
    lineno: int
    shape: str
    func: str
    text: str
    filed: bool
    named: str
    is_test: bool
    in_probes: bool

    @property
    def in_scope(self) -> bool:
        return self.named != "refuses" and not (self.is_test or self.in_probes)

    @property
    def key(self) -> tuple:
        return (self.path, self.shape, self.func, self.text)

    @property
    def label(self) -> str:
        """What a gap message quotes for the site."""
        return f"{self.func}: {self.text}"


def aggregate_sites(sources: dict[str, str],
                    roots: tuple = AGGREGATE_ROOTS) -> tuple[list[Aggregate], list[str]]:
    """Every builtin max() / min() with a non-literal argument in a function
    under `roots` that is named for a verdict, registered, or refuses -- and
    the files that did not parse. Only the first two kinds are in scope; the
    third is counted so the name filter's blind spot is a number."""
    sites: list[tuple] = []
    unparsed: list[str] = []
    registered = {(path, func) for path, func, _why in AGGREGATE_REGISTRY}
    prefixes = tuple(f"{d}/" for d in roots)
    for path, src in sorted(sources.items()):
        if not path.endswith(".py") or not path.startswith(prefixes):
            continue
        try:
            tree = ast.parse(src)
        except (SyntaxError, ValueError) as exc:
            unparsed.append(f"{path} ({type(exc).__name__})")
            continue
        is_test, in_probes = is_test_path(path), path.startswith("tools/probes/")
        for fn in vga._functions(tree):
            if AGGREGATE_NAME.search(fn.node.name):
                named = "name"
            elif (path, fn.qual) in registered:
                named = "registered"
            elif any(vga._is_refusal_stmt(n) or isinstance(n, ast.Raise)
                     for n in vga._own_nodes(fn.node)):
                named = "refuses"
            else:
                continue
            for n in vga._own_nodes(fn.node):
                if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                        and n.func.id in AGGREGATES):
                    continue
                if not n.args or all(isinstance(a, ast.Constant) for a in n.args):
                    continue
                sites.append((path, n.lineno, n.col_offset, Aggregate(
                    path, n.lineno, n.func.id, fn.qual, _call_text(src, n),
                    _filed_population(n.args[0]), named, is_test, in_probes)))
    # `_own_nodes` empties a stack, so a function's calls come out backwards;
    # the report reads in source order.
    sites.sort(key=lambda t: t[:3])
    return [site for _path, _line, _col, site in sites], unparsed


#: The triage, in `SITE_VERDICTS`' own `(verdict, why, control)` shape, keyed
#: `(path, builtin, qualified function, call text)`.
AGGREGATE_VERDICTS: dict[tuple, tuple] = {}


def aggregate_gaps(sites: list[Aggregate], root: pathlib.Path = ROOT) -> list[str]:
    """`--aggregates --check`'s entire content."""
    return triage_gaps(sites, root, table=AGGREGATE_VERDICTS,
                       table_name="AGGREGATE_VERDICTS", what="max()/min()")


def aggregates_main(gate: bool, ref: str | None, root: pathlib.Path = ROOT) -> int:
    sources = py_sources(ref, root, AGGREGATE_ROOTS)
    sites, unparsed = aggregate_sites(sources)
    scoped = [s for s in sites if s.in_scope]
    by_verdict = {v: 0 for v in VERDICTS}
    with_call = {path for path, src in sources.items()
                 if any(k in src for k in AGGREGATE_FINITE_NAMES)}
    for s in scoped:
        verdict = AGGREGATE_VERDICTS.get(s.key)
        if verdict:
            by_verdict[verdict[0]] += 1
    filed = [s for s in scoped if s.filed]
    beyond = [s for s in scoped if not s.filed]
    no_call = [s for s in filed if s.path not in with_call]
    has_call = [s for s in filed if s.path in with_call]
    print(f"NaN-blind max()/min() sites, read from {tree_label(ref, root)}")
    behind = _git(root, "rev-list", "--count", "HEAD..origin/main")
    if behind.isdigit() and int(behind) and ref is None:
        print(f"!! THIS TREE IS {behind} COMMITS BEHIND origin/main. The lines below describe "
              "that older tree, not main. Use --ref origin/main.")
    print(f"  builtin max()/min() with a non-literal argument, in a function named for a "
          f"verdict or registered: {len(scoped)} sites in "
          f"{len({s.path for s in scoped})} files")
    print(f"    the filed walk's own test (first argument a population): {len(filed)} sites in "
          f"{len({s.path for s in filed})} files = {len(has_call)} sites in "
          f"{len({s.path for s in has_call})} files with a finiteness call somewhere + "
          f"{len(no_call)} sites in {len({s.path for s in no_call})} files with none")
    print(f"    beyond that test: {len(beyond)} sites in {len({s.path for s in beyond})} files")
    print("  verdicts: " + ", ".join(f"{by_verdict[v]} {v}" for v in VERDICTS)
          + f"   table: {len(AGGREGATE_VERDICTS)} entries")
    for s in scoped:
        verdict, why, control = AGGREGATE_VERDICTS.get(s.key, ("<no verdict>", "", None))
        mark = " +" if control else ""
        print(f"{s.path}:{s.lineno} [{s.shape}] {s.func}: {verdict}{mark}  {why}")
    refusing = [s for s in sites if s.named == "refuses"]
    if refusing:
        funcs = sorted({f"{s.path}:{s.func}" for s in refusing})
        print(f"\nNOT GATED ({len(refusing)}): in {len(funcs)} functions under "
              f"{', '.join(AGGREGATE_ROOTS)} that refuse but are named "
              "for neither a verdict nor a property -- the name filter is the filed walk's. "
              "Register a function in AGGREGATE_REGISTRY to gate its sites.")
        print("  " + " ".join(funcs))
    out = [s for s in sites if not s.in_scope and s.named != "refuses"]
    if out:
        print(f"\nnot in scope, not triaged ({len(out)}): "
              + " ".join(f"{s.path}:{s.lineno}" for s in out))
    if unparsed:
        print(f"\nDID NOT PARSE: {', '.join(unparsed)}")
    print("\n'+' marks a REACHABLE site whose injected-NaN control exists. A verdict is a reading "
          "of the arguments, not a proof: each cites its guard or its path.")
    if not gate:
        return 0
    gaps = aggregate_gaps(sites, root)
    for g in gaps:
        print(f"GAP      {g}")
    if gaps:
        print(f"\n{len(gaps)} gap(s) against tools/nan_guard_audit.py::AGGREGATE_VERDICTS. A "
              "max()/min() that drops a NaN was added or changed without a verdict.")
        return 1
    print("OK       every in-scope site has a verdict and every verdict names a site")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true",
                    help="print every fails-open guard, not just the counts")
    ap.add_argument("--check", action="store_true",
                    help="assert every registered boundary still checks finiteness; with "
                         "--sites, assert the per-site verdict table is complete and fresh")
    ap.add_argument("--sites", action="store_true",
                    help="list every shape A/B comparison site with its verdict (#1929)")
    ap.add_argument("--aggregates", action="store_true",
                    help="list every builtin max()/min() with a non-literal argument and its "
                         "verdict (#2014)")
    ap.add_argument("--ref", help="read the sources from this git ref instead of the "
                                   "working tree (e.g. origin/main)")
    args = ap.parse_args(argv)

    if args.sites:
        return sites_main(args.check, args.ref)

    if args.aggregates:
        return aggregates_main(args.check, args.ref)

    if args.check:
        gaps = boundary_gaps()
        for g in gaps:
            print(f"GAP      {g}")
        if gaps:
            print(f"\n{len(gaps)} of {len(BOUNDARIES)} audio-entry boundaries do not "
                  f"assert finiteness. A NaN entering there is invisible to every "
                  f"threshold guard downstream (#134).")
            return 1
        print(f"OK       {len(BOUNDARIES)} audio-entry boundaries all assert finiteness")
        return 0

    rows = []
    total = {"fails-open": 0, "fails-closed": 0, "indeterminate": 0}
    for rel in AUDIT_FILES:
        guards = audit_file(rel)
        counts = {k: 0 for k in total}
        reachable = 0
        for g in guards:
            counts[g.verdict] += 1
            total[g.verdict] += 1
            if g.verdict == _Verdict.OPEN and not g.int_domain:
                reachable += 1
        fin = sum(finite_checks(rel).values())
        rows.append((rel, counts, reachable, fin))

    w = max(len(r[0]) for r in rows)
    print(f"{'file':<{w}}  open  closed  indet  open&float?  finite-checks")
    for rel, counts, reachable, fin in rows:
        print(f"{rel:<{w}}  {counts['fails-open']:>4}  {counts['fails-closed']:>6}  "
              f"{counts['indeterminate']:>5}  {reachable:>11}  {fin:>13}")
    print(f"\n{total['fails-open']} fails-open, {total['fails-closed']} fails-closed, "
          f"{total['indeterminate']} indeterminate refusal guards.")
    print("'open&float?' drops the guards whose operands look like counts or "
          "lengths (heuristic; see --list).")
    print("A fails-open guard is a defect only where a NaN can REACH it, which is "
          "what the boundary checks are for: python3 tools/nan_guard_audit.py --check")

    if args.list:
        print()
        for rel in AUDIT_FILES:
            for g in audit_file(rel):
                if g.verdict == _Verdict.OPEN:
                    print(g)
    return 0


if __name__ == "__main__":
    sys.exit(main())

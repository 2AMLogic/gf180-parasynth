"""The vintage pin for the two corner probes' labels (#792).

`tools/probes/estimator_defects.py` and `tools/probes/filter_corner_recalibration.py`
both hold a copy of the pre-#150 corner estimator, and both printed it under
column headers that read as what currently ships. #792 dates the columns
instead of inverting them -- the `SHIPPED`/`REPAIRED` pair is
`estimator_defects.py`'s declared file-wide convention (its module docstring,
lines 10-13, defines "shipped" as "as it stood before the repair", and seven
sibling `*_shipped` helpers follow it) -- and renames the helper only in
`filter_corner_recalibration.py`, which declares no such convention.

Three gates, each scoped to the corner-estimator sites only. The scoping is
measured, not stylistic (#792, verified correction of 2026-11-15): the
unscoped versions are red on 6 of 9 helpers and 10 of 11 header sites, most
belonging to other defects, so an unscoped gate would be unsatisfiable
without editing sections this issue does not own.

Guard-defeating input, stated per docs/verification-rules.md rule 8: a naive
"the header contains a #" check passes `SHIPPED (#150)` -- the repaired
vintage on the retired column, which is exactly the inversion #792 exists to
prevent. The binding below therefore requires the pre-repair column to carry
`pre-#150` and the repaired column to carry `#150` without `pre-`, and the
injected controls turn that binding red.

The policed population has its own defeating input (#900): a column header
written without a format spec -- a plain string, or an f-string interpolation
carrying none -- is outside the scan, and a newly added inverted header
written that way moves no count. Measured on this tree (origin/main 754bce36,
2026-11-16, standalone ast replication of the FCR half, pin 6): a new
`SHIPPED (#150)` header as a plain string or bare f-string interpolation
leaves the count at 6 and the binding green -- #900's rows (b) and (c),
re-measured here, not copied; written the file's own way -- with a format
spec -- it is policed, count 7, both gates red (row (d)). Stripping the spec
from an existing header also leaves the scan, but drops the count below the
pin, so the count assertion fails (row (a)): the count pin is what keeps the
existing headers from leaving the set. This is recorded as a bound, not
repaired -- policing spec-less strings again would reintroduce #852's false
positives -- and the FCR control below pins row (b) as measured.

Also pins the REFUSED path of `filter_corner_recalibration.py` section 1
against genuinely refusing estimators at a cutoff where `rc.filt_corner`
refuses on the F1 grid (100 Hz; #792's measured table), not a stub.
"""
from __future__ import annotations

import ast
import importlib.util
import pathlib
import re

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
EST = ROOT / "tools" / "probes" / "estimator_defects.py"
FCR = ROOT / "tools" / "probes" / "filter_corner_recalibration.py"

EST_TEXT = EST.read_text()
FCR_TEXT = FCR.read_text()
EST_NAME = str(EST.relative_to(ROOT))
FCR_NAME = str(FCR.relative_to(ROOT))

HELPER_RE = re.compile(r"(.*_shipped|shipped_.*)")
HEADER_RE = re.compile(r"SHIPPED|REPAIRED|(shipped|repaired) (ratio|spread)")


def _func(text, name):
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name!r} not found")


def _section6_range() -> tuple[int, int]:
    """estimator_defects.py section 6 only: `defect_6`'s line span. Defects 1,
    3 and 5 have their own undated (or differently-vintaged) headers that are
    deliberately out of #792's scope."""
    n = _func(EST_TEXT, "defect_6")
    return n.lineno, n.end_lineno


def _header_literals(text: str, lo: int, hi: int) -> list[tuple[int, str]]:
    """(1-based line number, literal) for every column header in the
    inclusive line span. A column header is a string constant that is the
    `.value` of an `ast.FormattedValue` carrying a `format_spec` -- a string
    being column-formatted, literally ``{'SHIPPED (pre-#150)':>19}``. Parsed,
    not grepped, so quoting on the physical line cannot fool this; and
    prose -- docstrings, plain string arguments, bare f-string fragments and
    ``{n}`` interpolations without a format spec -- is not formatted into a
    column, so it stays outside the policed set (#852). The mirror bound
    (#900): a *column header* written without a format spec is equally
    outside this set -- the scan cannot tell it from prose -- so an existing
    header is kept in the set by gate (c)'s count pins, not by this
    predicate; see the module docstring's rule-8 paragraph for the measured
    rows."""
    found = []
    for node in ast.walk(ast.parse(text)):
        if not (isinstance(node, ast.FormattedValue)
                and node.format_spec is not None):
            continue
        value = node.value
        if (isinstance(value, ast.Constant) and isinstance(value.value, str)
                and lo <= value.lineno <= hi and HEADER_RE.search(value.value)):
            found.append((value.lineno, value.value))
    return found


def _vintage_violations(pairs: list[tuple[int, str]],
                        source: str) -> list[str]:
    """Bind the vintage to the column it heads: a pre-repair column must say
    `pre-#150`; a repaired column must say `#150` and must not say `pre-`.
    `SHIPPED (#150)` -- the repaired vintage on the retired column -- fails
    the first rule, which is the inversion this pin exists to catch. `source`
    names the file the pairs came from, so a violation is actionable."""
    bad = []
    for ln, lit in pairs:
        if re.search(r"SHIPPED|shipped", lit) and "pre-#150" not in lit:
            bad.append(f"{source} line {ln}: {lit!r} -- pre-repair column "
                       f"carries no `pre-#150` vintage")
        if re.search(r"REPAIRED|repaired", lit) and ("#150" not in lit or "pre-" in lit):
            bad.append(f"{source} line {ln}: {lit!r} -- repaired column must "
                       f"carry `#150` and not `pre-`")
    return bad


def _load_fcr():
    spec = importlib.util.spec_from_file_location(
        "filter_corner_recalibration_under_test", FCR)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# gate (a): the docstring, scoped to the one helper whose referent moved
# ---------------------------------------------------------------------------

def test_corner_shipped_docstring_names_its_vintage():
    doc = ast.get_docstring(_func(EST_TEXT, "corner_shipped"))
    assert doc is not None, "corner_shipped lost its docstring"
    assert "before #150" in doc, (
        "corner_shipped's docstring states 'exactly as it stood' with no "
        "vintage -- the one omission among eight siblings (#792)")


def test_corner_shipped_keeps_its_name():
    """AC1, scoped: `corner_shipped` keeps its name -- renaming it would break
    estimator_defects.py's declared file-wide convention, not restore it."""
    helpers = sorted(n.name for n in ast.walk(ast.parse(EST_TEXT))
                     if isinstance(n, ast.FunctionDef) and HELPER_RE.fullmatch(n.name))
    assert "corner_shipped" in helpers
    # the declared convention: corner_shipped plus its seven siblings
    assert helpers == sorted([
        "corner_shipped", "inharmonic_shipped", "prepare_shipped",
        "split_db_shipped", "t20_length_guard_shipped", "t20_shipped",
        "tone_ratio_shipped", "window_shipped"]), (
        f"estimator_defects.py's *_shipped set changed: {helpers}")


def test_other_shipped_helpers_stay_out_of_scope():
    """The boundary is real, not decorative: the docstring gate selects
    corner_shipped by name, so the seven siblings -- three of which carry no
    docstring at all -- stay green without being edited."""
    for name in ("window_shipped", "split_db_shipped", "tone_ratio_shipped",
                 "inharmonic_shipped", "t20_shipped"):
        node = _func(EST_TEXT, name)
        assert node.name == name


# ---------------------------------------------------------------------------
# gate (b): filter_corner_recalibration.py names its helper for its vintage
# ---------------------------------------------------------------------------

def test_filter_corner_recalibration_has_no_status_named_helper():
    helpers = [n.name for n in ast.walk(ast.parse(FCR_TEXT))
               if isinstance(n, ast.FunctionDef) and HELPER_RE.fullmatch(n.name)]
    assert helpers == [], (
        "filter_corner_recalibration.py declares no shipped-means-pre-repair "
        f"convention, so a status-named helper rots: {helpers}")
    names = {n.name for n in ast.walk(ast.parse(FCR_TEXT))
             if isinstance(n, ast.FunctionDef)}
    assert "corner_pre150" in names


# ---------------------------------------------------------------------------
# gate (c): the header pairs carry their vintage, bound to their column
# ---------------------------------------------------------------------------

def test_corner_header_pairs_carry_their_vintage():
    est = _header_literals(EST_TEXT, *_section6_range())
    fcr = _header_literals(FCR_TEXT, 1, len(FCR_TEXT.splitlines()))
    # Pin the population, not just its emptiness: a scan that silently
    # narrows to nothing passes `assert est` vacuously (#852).
    assert len(est) == 4, (
        f"estimator_defects section-6 scan changed: {est} -- the policed "
        "population moved; re-pin this count deliberately")
    assert len(fcr) == 6, (
        f"filter_corner_recalibration scan changed: {fcr} -- the policed "
        "population moved; re-pin this count deliberately")
    assert (_vintage_violations(est, EST_NAME)
            + _vintage_violations(fcr, FCR_NAME)) == []


def test_the_gate_scope_is_load_bearing_not_decorative():
    """Defects 1 and 3's undated headers are real and violate the binding --
    the section-6 scope is what keeps them out. Asserting both halves stops
    the next reader from 'fixing' the scope by widening it over sections this
    issue does not own (#792, Out of Scope)."""
    lo, hi = _section6_range()
    full = _header_literals(EST_TEXT, 1, len(EST_TEXT.splitlines()))
    scoped = _header_literals(EST_TEXT, lo, hi)
    defect1 = [(ln, lit) for ln, lit in full if lit in ("SHIPPED err", "REPAIRED err")]
    defect3 = [(ln, lit) for ln, lit in full if lit == "SHIPPED guard"]
    assert defect1 and defect3, "expected the out-of-scope undated headers on main"
    assert all(ln < lo or ln > hi for ln, _ in defect1 + defect3), (
        "defects 1 and 3 leaked into the section-6 scan")
    assert _vintage_violations(defect1 + defect3, EST_NAME), (
        "the out-of-scope headers pass the binding -- the scope claim is stale")
    assert not any(lit in ("SHIPPED err", "REPAIRED err", "SHIPPED guard")
                   for _, lit in scoped)


# ---------------------------------------------------------------------------
# injected controls: the binding must reject the inversion, not just absence
# ---------------------------------------------------------------------------

def _est_section6_with(old: str, new: str) -> str:
    lines = EST_TEXT.splitlines()
    lo, hi = _section6_range()
    hits = [i for i in range(lo - 1, hi) if old in lines[i]]
    assert len(hits) >= 1, f"control target {old!r} not found in section 6"
    for i in hits:
        lines[i] = lines[i].replace(old, new)
    return "\n".join(lines)


def _control_red(text: str) -> list[str]:
    lo, hi = None, None
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "defect_6":
            lo, hi = node.lineno, node.end_lineno
    return _vintage_violations(_header_literals(text, lo, hi), EST_NAME)


def test_control_repaired_vintage_on_the_retired_column_turns_red():
    """`SHIPPED (#150)` satisfies every naive 'contains a vintage' check and
    is exactly the inversion. The binding must turn it red."""
    text = _est_section6_with("SHIPPED (pre-#150)", "SHIPPED (#150)")
    assert _control_red(text)


def test_control_undated_header_turns_red():
    text = _est_section6_with("SHIPPED (pre-#150)", "SHIPPED")
    assert _control_red(text)


def test_control_pre_vintage_on_the_repaired_column_turns_red():
    text = _est_section6_with("REPAIRED (#150)", "REPAIRED (pre-#150)")
    assert _control_red(text)


# ---------------------------------------------------------------------------
# injected controls on the FCR half: prose must stay green, the inversion red
# ---------------------------------------------------------------------------

def _fcr_gate(text: str) -> list[str]:
    """Gate (c) as it applies to the FCR half: whole-file scan + binding."""
    return _vintage_violations(
        _header_literals(text, 1, len(text.splitlines())), FCR_NAME)


def _fcr_docstring_with(prose: str) -> str:
    """FCR with one prose line inserted inside the module docstring."""
    doc = ast.parse(FCR_TEXT).body[0]
    assert isinstance(doc, ast.Expr) and isinstance(doc.value, ast.Constant) \
        and isinstance(doc.value.value, str), "FCR lost its module docstring"
    lines = FCR_TEXT.splitlines()
    lines.insert(doc.end_lineno - 1, prose)
    return "\n".join(lines)


def test_control_docstring_naming_both_columns_stays_green():
    """Correct prose in the module docstring -- naming both columns by their
    header text, as a reader documents #150's rename -- must not turn the
    gate red. #852's false positive: rule 2's `pre-` test is literal-wide,
    so a literal documenting both columns fails it by construction."""
    text = _fcr_docstring_with(
        "The SHIPPED (pre-#150) column is the retired estimator; "
        "REPAIRED (#150) is the calibrated one.")
    assert _fcr_gate(text) == []


def test_control_docstring_naming_one_undated_column_stays_green():
    """Prose that mentions one column without a vintage is still prose, not
    a column header: rule 1 must not reach it."""
    text = _fcr_docstring_with(
        "See the SHIPPED column for the retired estimator.")
    assert _fcr_gate(text) == []


def test_control_prose_fstring_print_stays_green():
    """A prose f-string naming a column is not a column header: its text is
    a bare JoinedStr fragment, not a string being column-formatted."""
    text = FCR_TEXT + ('\nprint(f"   the SHIPPED column above is the retired '
                       'estimator, {13} devices")\n')
    assert _fcr_gate(text) == []


def test_control_real_inversion_in_an_fcr_header_turns_red():
    """The scan must not narrow past the real defect: `SHIPPED (#150)` in an
    actual FCR column header -- the inversion #792 exists to catch -- stays
    red, red before and after any population fix (#852)."""
    assert "SHIPPED (pre-#150)" in FCR_TEXT, "control target moved in FCR"
    text = FCR_TEXT.replace("SHIPPED (pre-#150)", "SHIPPED (#150)", 1)
    assert _fcr_gate(text)


def test_control_new_header_without_format_spec_escapes_the_gate():
    """Records #900's measured bound; it is not a pass to rely on. A NEW
    column header written without a format spec -- row (b), a plain string;
    row (c), a bare f-string interpolation, behaves identically -- is outside
    the policed set and moves no count, so the gate stays green over an
    inverted `SHIPPED (#150)` that arrived spec-less. If this control ever
    fails, the predicate was widened over spec-less strings -- #852's false
    positives are back -- and the module docstring's rule-8 paragraph and
    this control must be updated together."""
    text = FCR_TEXT + '\nprint("      SHIPPED (#150)       ")\n'
    assert len(_header_literals(text, 1, len(text.splitlines()))) == 6, (
        "the policed population moved -- a spec-less header was policed "
        "after all; re-measure #900's rows (a)-(d) and update the rule-8 "
        "statement")
    assert _fcr_gate(text) == []


# ---------------------------------------------------------------------------
# the refusal path: section 1 prints REFUSED, not a TypeError, on real refusals
# ---------------------------------------------------------------------------

def test_section1_refuses_when_filt_corner_refuses():
    """100 Hz on the F1 grid: the shipped estimator genuinely refuses and the
    pre-#150 copy answers (#792's measured table) -- the row must print
    REFUSED with the reason, not raise TypeError on a None `.value`."""
    mod = _load_fcr()
    import reference_compare as rcmp
    import refprofile as rp
    import run_case as rc
    F = np.asarray(rcmp.FREQS, dtype=np.float64)
    g = rc.our_filter_curve(F, 100.0, 0.0, rp.PROBE_AMP)
    o = mod.corner_pre150(F, g, 100.0)
    n = rc.filt_corner(100.0)(F, g)
    assert o.ok and o.value is not None, "the pre-#150 copy stopped answering"
    assert not n.ok and n.value is None, (
        "the premise moved: rc.filt_corner no longer refuses at 100 Hz")
    line = mod._ours_row("F1X", 100.0, o, n)
    assert "REFUSED" in line
    assert n.reason.split(" -- ")[0][:44] in line


def test_section1_refuses_when_the_pre150_copy_refuses():
    """Both operands, per #792: a constant-zero curve never falls 3 dB below
    its passband, which `corner_from_curve` refuses."""
    mod = _load_fcr()
    import reference_compare as rcmp
    import refprofile as rp
    import run_case as rc
    F = np.asarray(rcmp.FREQS, dtype=np.float64)
    g = np.zeros_like(F)
    o = mod.corner_pre150(F, g, 250.0)
    n = rc.filt_corner(250.0)(F, g)
    assert not o.ok and o.value is None, (
        "the premise moved: corner_pre150 answered a curve with no corner")
    line = mod._ours_row("F1X", 250.0, o, n)
    assert "REFUSED" in line
    assert o.reason.split(" -- ")[0][:44] in line

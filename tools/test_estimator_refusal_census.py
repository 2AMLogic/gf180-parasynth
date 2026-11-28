#!/usr/bin/env python3
"""Controls for `tools/estimator_refusal_census.py` (#1782): the #1763 walk,
committed, plus the two rules that walk was blind to -- `Estimate.require()`
and one hop through a function that raises nothing itself.

Every planted shape is RUN before the census is asked about it. The planted
modules are real, importable Python, and each caller is started as a command
with an input that makes its estimator refuse: the interpreter then either
shows `audio_measure.InsufficientEvidence` in an exit-1 traceback (ESCAPED)
or the caller's own `REFUSED` line and exit 2 (HANDLED). The census row is
held to that verdict, not to the rule that produced it -- the hold the #1718
controls put on `refusal_sibling_scan` -- so a rule that misreads a shape
cannot pass by agreeing with itself. Two shapes escape and are NOT
candidates: each is a stated blind spot (a two-hop chain; an Estimate that
arrives as a parameter), and the control is that the census names them where
it says what it cannot see, never silently.

STARTED RED (docs/verification-rules.md rule 1). Run against a stub whose
`Census` reported no origins, no rows and no blind spots, all 53 controls
failed; the stub's run is recorded in the PR. Running the planted code
before the census existed was worth two fixes to the fixtures themselves --
one caller named `refusal.Refused` without importing `refusal`, so its own
handler raised a NameError over the refusal it was written to catch; and
the "guarded inside" shape exits 0, not 2, because the callee absorbs its
own refusal -- both found by the interpreter, neither by reading. The frozen-tree
controls read `origin/main` as it stood when #1782 was filed straight out of
git, so the known escape is shown absent under the old rules and present
under the new ones on the bytes that had it -- the way
`fpga/test_verify_xdc_binding.py` reads `383f10b^` -- and they skip VISIBLY,
naming the commit, in a clone without the object.
"""
from __future__ import annotations

import ast
import json
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import estimator_refusal_census as erc                  # noqa: E402
import refusal_sibling_scan as rss                      # noqa: E402

OK, REFUSED = 0, 2

#: `origin/main` when #1782's edges were verified: `tools/f1_level_capture.py`
#: calls `reference_rigs.SurgeRig.tone_project` under no handler in it
PINNED_ESCAPE_TREE = "0314839944a59674220703ba056341f5a5c6b677"
#: the tree #1763's ten-row table was read from. "#1751 applied" was a
#: commit of its own then, so `tools/probes/rs_mode_drive.py` is an eleventh
#: shape row on this tree and in no table
PINNED_1763_TREE = "e26d7f29e8248da46bdccb68c7defd6dde0b7943"

# ---------------------------------------------------------------------------
# the planted tree: an estimator module that refuses, a rig module that
# reaches it two ways, the common refusal base and a `require` that is not an
# estimator's. Pure Python on purpose -- these are imported and run.
# ---------------------------------------------------------------------------

REFUSAL = '''
class Refused(Exception):
    pass
'''

REQUIRE_RAISING = '''    def require(self, what=""):
        if not self.ok or self.value is None:
            raise InsufficientEvidence(
                f"{what or 'estimate'}: insufficient evidence -- {self.reason}")
        return float(self.value)
'''

REQUIRE_QUIET = '''    def require(self, what=""):
        return float(self.value or 0.0)
'''

AUDIO_MEASURE = '''
class InsufficientEvidence(AssertionError):
    """An estimator was asked for a number it cannot honestly supply."""


class NonFiniteAudio(InsufficientEvidence):
    pass


class Estimate:
    def __init__(self, value=None, ok=True, reason="", detail=None):
        self.value, self.ok, self.reason = value, ok, reason
        self.detail = detail or {}

''' + REQUIRE_RAISING + '''

def _fail(reason, **detail):
    return Estimate(None, False, reason, detail)


def is_silent(x):
    return not x or max(abs(v) for v in x) <= 1e-9


def plateau_db(freqs, gain_db, band) -> float:
    sel = [g for f, g in zip(freqs, gain_db) if band[0] <= f <= band[1]]
    if not sel:
        raise InsufficientEvidence("plateau_db: no measured points in the reference band")
    sel.sort()
    return sel[len(sel) // 2]


def corner_from_curve(freqs, gain_db, *, ref_band=None) -> Estimate:
    ref = plateau_db(freqs, gain_db, ref_band or (freqs[0], freqs[0] * 2.0))
    return Estimate(ref)


def guarded_plateau(freqs, gain_db, band) -> Estimate:
    try:
        return Estimate(plateau_db(freqs, gain_db, band))
    except InsufficientEvidence as e:
        return _fail(str(e))


def cycle_average(x, f0, sr=48000, *, n=1024):
    if is_silent(x):
        raise InsufficientEvidence("cycle_average: silent")
    return x[:n], 0.0


def harmonic_signature(x, sr=48000, *, f0=None) -> dict:
    if len(x) < 8:
        raise InsufficientEvidence("harmonic_signature: too few samples")
    return {"f0": f0}


def waveform_id(x, f0, sr=48000, *, kmax=9):
    det = {"f0": float(f0)}
    try:
        cyc, jit = cycle_average(x, f0, sr)
    except InsufficientEvidence as e:
        return ("refused", str(e), det)
    det["signature"] = harmonic_signature(x, sr, f0=f0)
    return ("named", "saw", det)


def tone_amplitude(x, hz, sr=48000) -> Estimate:
    if is_silent(x):
        return _fail("silent")
    return Estimate(1.0)


def rms(x) -> float:
    return 0.5


NOTED = []


def rethrown_plateau(freqs, gain_db, band) -> float:
    """Notes the exception, then puts it back: the handler catches and is no
    guard at all (the `r0_capture._write_atomic` shape)."""
    try:
        return plateau_db(freqs, gain_db, band)
    except Exception as exc:
        NOTED.append(type(exc).__name__)
        raise
'''

REFERENCE_RIGS = '''
import audio_measure as am


class Rig:
    name = "rig"


class OurLadder(Rig):
    def tone_gain_db(self, freqs, cut, res, amp):
        out = []
        for f in freqs:
            a = am.tone_amplitude(cut, f).require(f"ours probe {f:.0f} Hz")
            out.append(a / amp)
        return out


class SurgeRig(Rig):
    @staticmethod
    def tone_project(y, parts, amp, name="tone"):
        out = []
        for i0, nw, f in parts:
            a = am.tone_amplitude(y[i0: i0 + nw], f).require(f"{name} probe {f:.0f} Hz")
            out.append(a / amp)
        return out

    @staticmethod
    def tone_gain_db(freqs, cut, res, amp):
        return SurgeRig.tone_project(cut, [(0, 4, f) for f in freqs], amp, "surge")

    def curve(self, freqs, cut, res, amp):
        return self.tone_project(cut, [(0, 4, f) for f in freqs], amp, self.name)
'''

RIG_QUALIFICATION = '''
import refusal


class RigRefusal(refusal.Refused):
    pass


class Qualification:
    qualified = False

    def require(self):
        """Return self when qualified; otherwise REFUSE -- a `require` that is
        not an estimator's (the name #1782's collision control plants)."""
        if self.qualified:
            return self
        raise RigRefusal("rig not qualified")
'''

CALLER = '''\
import sys
{imports}

{setup}
def {fn}({args}):
{body}
def main():
    try:
        {call}
    except {handler} as e:
        print(f"REFUSED  {{e}}")
        return 2
    return 0

if __name__ == "__main__":
    sys.exit(main())
'''

SILENT = "[0.0] * 16"
REFUSED_ONLY = "refusal.Refused"
CURVE = "[100.0, 200.0], [0.0, -1.0], ref_band=(1000.0, 2000.0)"
REQUIRE_TONE = f"am.tone_amplitude({SILENT}, 440.0).require('probe 440 Hz')"


def _c(imports="", setup="", fn="measure", args="", body="", call=None, handler=REFUSED_ONLY):
    body = "".join(f"    {ln}\n" for ln in body.strip("\n").split("\n") if ln)
    return CALLER.format(imports=imports, setup=setup, fn=fn, args=args, body=body,
                         call=call or f"{fn}()", handler=handler)


#: name -> source: one planted caller per shape
PLANTED = {
    # the three controls #1782's acceptance criteria name
    "c_require_unguarded": _c("import audio_measure as am", body=REQUIRE_TONE),
    "c_require_guarded": _c("import audio_measure as am\nimport refusal", body=REQUIRE_TONE,
                            handler="(refusal.Refused, am.InsufficientEvidence)"),
    "c_require_other_object": _c("import refusal\nimport rig_qualification as rq",
                                 args="q", body="return q.require()",
                                 call="measure(rq.Qualification())"),
    # Rule 2: one hop through a function that raises nothing itself
    "c_corner_one_hop": _c("import audio_measure as am",
                           body=f"return am.corner_from_curve({CURVE})"),
    "c_waveform_one_hop": _c("import audio_measure as am",
                             body="return am.waveform_id([1.0] * 4, 440.0)"),
    "c_guarded_edge": _c("import audio_measure as am",
                         body="return am.guarded_plateau([100.0, 200.0], [0.0, -1.0], "
                              "(1000.0, 2000.0))"),
    "c_reraised_origin": _c("import audio_measure as am",
                            body="return am.rethrown_plateau([100.0, 200.0], [0.0, -1.0], (1000.0, 2000.0))"),
    # a boundary handler that catches the family and puts it back: the
    # #1763 walk read `except Exception` as covering (`fpga/play_song.py`)
    "c_reraising_handler": f"""import sys
import audio_measure as am
import refusal


def measure():
    return am.plateau_db([100.0, 200.0], [0.0, -1.0], (1000.0, 2000.0))


def main():
    try:
        measure()
    except refusal.Refused as e:
        print(f"REFUSED  {{e}}")
        return 2
    except Exception as exc:
        print(f"noted {{type(exc).__name__}}")
        raise
    return 0


if __name__ == "__main__":
    sys.exit(main())
""",
    "c_raise_of_the_caught_name": f"""import sys
import audio_measure as am


def measure():
    return am.plateau_db([100.0, 200.0], [0.0, -1.0], (1000.0, 2000.0))


def main():
    try:
        measure()
    except am.InsufficientEvidence as e:
        print("noted")
        raise e
    return 0


if __name__ == "__main__":
    sys.exit(main())
""",
    # a handler that raises a NEW exception stops the family: a library, not run
    "c_converting_handler": """import audio_measure as am
import refusal


def level(y, band):
    try:
        return am.plateau_db(y, y, band)
    except am.InsufficientEvidence as e:
        raise refusal.Refused(f"no plateau: {e}") from e
""",
    # the known escape in its own shape: a function-local import and a
    # class's static method, under a Refused-only handler at the boundary
    "c_known_escape": _c(
        "import refusal", fn="capture_curve",
        body="import reference_rigs as rr\n"
             f"return rr.SurgeRig.tone_project({SILENT}, [(0, 4, 40.0)], 0.25, "
             "'open20k/a0.2500/take1')"),
    "c_untyped_receiver": _c(
        "import reference_rigs as rr",
        body="dev = rr.OurLadder()\n"
             "return dev.tone_gain_db([440.0, 880.0], [0.0] * 16, 0.7, 0.1)"),
    # what a guard has to name
    "c_assertion_handler": _c("import audio_measure as am", body=REQUIRE_TONE,
                              handler="AssertionError"),
    "c_exception_handler": _c("import audio_measure as am", body=REQUIRE_TONE, handler="Exception"),
    "c_subclass_only_handler": _c("import audio_measure as am", body=REQUIRE_TONE,
                                  handler="am.NonFiniteAudio"),
    "c_alias_handler": _c("import audio_measure as am",
                          setup="IE = am.InsufficientEvidence",
                          body=REQUIRE_TONE, handler="IE"),
    # the ways an Estimate and an estimator are spelled
    "c_reexport_require": _c(
        "import reference_rigs as rr",
        body=f"return rr.am.tone_amplitude({SILENT}, 440.0).require('ring')"),
    "c_assigned_name_require": _c(
        "import audio_measure as am",
        body=f"e = am.tone_amplitude({SILENT}, 440.0)\nreturn e.require('probe 440 Hz')"),
    "c_from_import_alias": _c("from audio_measure import tone_amplitude as tone",
                              body=f"return tone({SILENT}, 440.0).require('probe 440 Hz')"),
    "c_module_alias": _c("import audio_measure as AM",
                         body=f"return AM.corner_from_curve({CURVE})"),
    # positive typing: a `.require()` after a call into the estimator module
    # is not enough -- `rms` returns a float, so this is an AttributeError
    "c_non_estimate_require": _c("import audio_measure as am",
                                 body="return am.rms([1.0] * 16).require('level')"),
    # a library with the call and no handler of any kind is not the shape:
    # #1763's criterion is a Refused-only boundary, and this has none
    "c_library_no_handler": "import audio_measure as am\n\n\ndef level(y):\n"
                            "    return am.tone_amplitude(y, 440.0).require('level')\n",
    # the two stated blind spots: they escape, and the census must say so
    "c_chain_two_hop": _c("import reference_rigs as rr\nimport refusal",
                          body="return rr.SurgeRig.tone_gain_db([440.0], [0.0] * 16, "
                               "0.7, 0.1)"),
    "c_parameter_require": _c("import audio_measure as am", fn="use", args="est",
                              setup=f"def give():\n    return am.tone_amplitude({SILENT}, 440.0)\n",
                              body="return est.require('passed in')", call="use(give())"),
}

#: what the interpreter does with each planted caller and what the census must
#: say: (escapes, a candidate under the #1763 rules, a candidate under all
#: rules, how the site is resolved, which rule carries it). `True` escapes as
#: an exit-1 traceback; `False` is handled at the boundary (exit 2, REFUSED);
#: `"RETURNED"` never raises, the callee having absorbed its own refusal
#: (exit 0); `"AttributeError"` is not a refusal at all; `None` marks a
#: library that is not run.
SHAPES = {
    "c_require_unguarded": (True, False, True, "estimate", "require"),
    "c_require_guarded": (False, False, False, None, None),
    "c_require_other_object": (False, False, False, None, None),
    "c_corner_one_hop": (True, False, True, "import", "one-hop"),
    "c_waveform_one_hop": (True, False, True, "import", "one-hop"),
    "c_guarded_edge": ("RETURNED", False, False, None, None),
    "c_reraised_origin": (True, False, True, "import", "one-hop"),
    "c_reraising_handler": (True, False, True, "import", "direct"),
    "c_raise_of_the_caught_name": (True, False, True, "import", "direct"),
    "c_converting_handler": (None, False, False, None, None),
    "c_known_escape": (True, False, True, "class", "one-hop"),
    "c_untyped_receiver": (True, False, True, "untyped-receiver", "one-hop"),
    "c_assertion_handler": (False, False, False, None, None),
    "c_exception_handler": (False, False, False, None, None),
    "c_subclass_only_handler": (True, False, True, "estimate", "require"),
    "c_alias_handler": (False, False, False, None, None),
    "c_reexport_require": (True, False, True, "estimate", "require"),
    "c_assigned_name_require": (True, False, True, "estimate", "require"),
    "c_from_import_alias": (True, False, True, "estimate", "require"),
    "c_module_alias": (True, False, True, "import", "one-hop"),
    "c_non_estimate_require": ("AttributeError", False, False, None, None),
    "c_library_no_handler": (None, False, False, None, None),
    "c_chain_two_hop": (True, False, False, None, None),
    "c_parameter_require": (True, False, False, None, None),
}

BASE = {"refusal": REFUSAL, "audio_measure": AUDIO_MEASURE, "reference_rigs": REFERENCE_RIGS,
        "rig_qualification": RIG_QUALIFICATION}


def _plant(tmp_path: pathlib.Path, replace: dict[str, str] | None = None,
           without=()) -> dict[str, pathlib.Path]:
    """Write the planted tree and return its modules by name."""
    src = {**BASE, **PLANTED, **(replace or {})}
    out = {}
    for name, body in src.items():
        if name in without:
            continue
        p = tmp_path / f"{name}.py"
        p.write_text(body)
        out[name] = p
    return out


def _line(src: str, needle: str, exact: bool = False) -> int:
    """The 1-based line `needle` first appears on -- or, with `exact`, equals
    once stripped (a bare `raise` is a substring of every other one)."""
    hits = [i for i, ln in enumerate(src.split("\n"), 1)
            if (ln.strip() == needle if exact else needle in ln)]
    assert hits, needle
    return hits[0]


def _row(census, name):
    rows = [r for r in census.rows if r["path"] == f"{name}.py"]
    assert len(rows) == 1, (name, [r["path"] for r in census.rows])
    return rows[0]


def _site(census, name, **match):
    """The one site of the module's row whose fields equal `match`. A module
    that reaches an estimator through a function of its own has two sites --
    the function's call, and `main`'s call of that function, itself one hop
    from the refusal -- so a control names the one it is about."""
    row = _row(census, name)
    sites = [s for s in row["sites"] if all(s[k] == v for k, v in match.items())]
    assert len(sites) == 1, (name, match, row["sites"])
    return sites[0]


def _listed(census, name):
    return _row(census, name)["candidate"]


def _verdict(tree: pathlib.Path, name: str) -> str:
    """What the interpreter does with a planted caller run as a command."""
    r = subprocess.run([sys.executable, f"{name}.py"], cwd=tree, capture_output=True,
                       text=True, timeout=120)
    err = r.stderr
    if r.returncode == 1 and "Traceback" in err and "audio_measure.InsufficientEvidence" in err:
        return "ESCAPED"
    if r.returncode == OK and not err.strip():
        return "RETURNED"
    if r.returncode == 1 and "AttributeError" in err:
        return "AttributeError"
    if r.returncode == REFUSED and "REFUSED" in r.stdout:
        return "HANDLED"
    return f"OTHER exit={r.returncode} {err.strip()[-160:]}"


def _pair(files, rules=("direct", "transitive")):
    """A tree read under each rule set from ONE parse of it: the real tree
    costs more to index than to read."""
    index = rss.build_index(files)
    return tuple(erc.Census(files, rules=r, index=index) for r in rules)


def _read_at(tree: pathlib.Path, files, rules=("direct", "transitive")):
    """`Census` records each path relative to `erc.ROOT` as it is built; name
    the tree for exactly that long, and no longer -- a fixture that moved
    `ROOT` for its whole life would hand every later test a tree that is not
    the repository's -- so a planted or frozen row's `path` is tree-relative
    exactly as it is repo-relative in a real run."""
    root, erc.ROOT = erc.ROOT, tree
    try:
        return _pair(files, rules)
    finally:
        erc.ROOT = root


@pytest.fixture(scope="module")
def planted(tmp_path_factory):
    tree = tmp_path_factory.mktemp("planted")
    old, new = _read_at(tree, list(_plant(tree).values()))
    return tree, old, new


@pytest.mark.parametrize("name", sorted(SHAPES))
def test_each_planted_shape_runs_and_the_census_row_agrees_with_the_interpreter(planted, name):
    tree, old, new = planted
    escapes, old_listed, new_listed, how, via = SHAPES[name]
    if escapes is True:
        assert _verdict(tree, name) == "ESCAPED", name
    elif escapes is False:
        assert _verdict(tree, name) == "HANDLED", name
    elif escapes == "AttributeError":
        assert _verdict(tree, name) == "AttributeError", name
    elif escapes == "RETURNED":
        assert _verdict(tree, name) == "RETURNED", name
    assert _listed(old, name) is old_listed, (name, "old rules", _row(old, name))
    assert _listed(new, name) is new_listed, (name, "new rules", _row(new, name))
    if new_listed:
        site = _site(new, name, how=how, via=via)
    # what ESCAPES and is not a candidate is never dropped silently: it is
    # named where the census says what it cannot see
    if escapes is True and not new_listed:
        blind = new.to_json()["blind"]
        unattributed = [u for u in blind["unresolved_requires"] if u["path"] == f"{name}.py"]
        chained = [s for s in blind["chain_sites"] if s["path"] == f"{name}.py"]
        assert unattributed or chained, (
            name, "an escape the census neither lists nor declares blind to")


# --- the three controls the acceptance criteria name ------------------------

def test_control_an_unguarded_require_under_a_refused_only_handler_is_listed(planted):
    """AC: "a synthetic module with an unguarded `.require()` under a
    `Refused`-only handler must be listed"."""
    _, old, new = planted
    src = PLANTED["c_require_unguarded"]
    row = _row(new, "c_require_unguarded")
    assert row["candidate"] and row["printed"] and not row["is_test"]
    assert row["refused_handlers"] == [
        f"{_line(src, 'except refusal.Refused')}:refusal.Refused"], row
    assert row["family_handlers"] == []
    site = _site(new, "c_require_unguarded", via="require")
    assert (site["line"], site["fn"], site["guarded"]) == (
        _line(src, ".require("), "measure", False), site
    assert site["callees"] == ["audio_measure.Estimate.require"], site
    # and the #1763 walk never saw it: `.require()` is a method
    assert _row(old, "c_require_unguarded")["sites"] == []


def test_control_the_same_require_under_an_insufficient_evidence_handler_is_not(planted):
    """AC: "the same with `except AM.InsufficientEvidence` must not"."""
    _, _, new = planted
    src = PLANTED["c_require_guarded"]
    row = _row(new, "c_require_guarded")
    assert not row["candidate"]
    assert row["family_handlers"] == [
        f"{_line(src, 'except (')}:(refusal.Refused, am.InsufficientEvidence)"], row
    # the guard is lexical: `main()`'s try wraps its call of `measure()`, and
    # the `.require()` inside `measure` carries no try of its own
    assert _site(new, "c_require_guarded", how="local")["guarded"] is True, row
    assert _site(new, "c_require_guarded", via="require")["guarded"] is False, row


def test_control_a_require_on_an_object_that_is_not_an_estimate_is_not_listed(planted):
    """AC: "a `.require()` on a non-`audio_measure` object must not (name
    collision)". `rig_qualification.Qualification.require` raises a
    `RigRefusal`, which the Refused handler catches (the interpreter says
    HANDLED), so listing the module would be a false candidate."""
    _, _, new = planted
    row = _row(new, "c_require_other_object")
    assert row["sites"] == [] and not row["candidate"], row
    # and the site is not dropped: it is declared unattributed
    un = [u for u in new.unresolved_requires if u["path"] == "c_require_other_object.py"]
    assert [(u["line"], u["fn"], u["receiver"]) for u in un] == [
        (_line(PLANTED["c_require_other_object"], "q.require()"), "measure", "q")], (
        new.unresolved_requires)


# --- Rule 2: one hop, and the per-callee note -------------------------------

def test_one_hop_through_a_function_that_raises_nothing_itself_is_an_origin(planted):
    _, old, new = planted
    assert "audio_measure.corner_from_curve" not in old.direct
    assert "audio_measure.corner_from_curve" not in new.direct
    edges = new.one_hop["audio_measure.corner_from_curve"]
    assert [(e["kind"], e["callee"], e["guarded"], e["line"]) for e in edges] == [
        ("direct", "audio_measure.plateau_db", False,
         _line(AUDIO_MEASURE, "ref = plateau_db("))], edges
    site = _site(new, "c_corner_one_hop", how="import")
    assert (site["via"], site["through"]) == ("one-hop", ["audio_measure.plateau_db"]), site
    # start red: the #1763 walk did not list this module, and the estimator
    # still escapes it (the interpreter's verdict, above)
    assert not _listed(old, "c_corner_one_hop")


def test_a_guarded_edge_is_noted_per_callee_and_does_not_hide_the_unguarded_one(planted):
    """AC: "a per-callee note when the caller guards the edge
    (`waveform_id`/`cycle_average`)". `waveform_id` guards `cycle_average`
    and does not guard `harmonic_signature`; both edges are reported, each
    with its own guard state."""
    _, _, new = planted
    edges = new.one_hop["audio_measure.waveform_id"]
    assert {e["callee"]: e["guarded"] for e in edges} == {
        "audio_measure.cycle_average": True, "audio_measure.harmonic_signature": False}, edges
    assert _site(new, "c_waveform_one_hop", how="import")["through"] == [
        "audio_measure.cycle_average [guarded]", "audio_measure.harmonic_signature"]


def test_a_function_whose_only_raising_edge_is_guarded_is_not_an_origin(planted):
    _, _, new = planted
    assert "audio_measure.guarded_plateau" not in new.one_hop
    assert "audio_measure.guarded_plateau" not in new.direct
    edges = new.guarded_only["audio_measure.guarded_plateau"]
    assert [(e["kind"], e["callee"], e["guarded"]) for e in edges] == [
        ("direct", "audio_measure.plateau_db", True)], edges
    row = _row(new, "c_guarded_edge")
    assert row["sites"] == [] and not row["candidate"], row


def test_a_handler_that_puts_the_exception_back_is_no_guard_so_its_edge_is_unguarded(planted):
    """`rethrown_plateau` catches `Exception` and raises again. Its edge to
    `plateau_db` is therefore not guarded and it is a one-hop origin -- not a
    direct one: a bare `raise` names nothing, and whether it lets the family
    out is a question about the try it sits under. (Counting every bare
    `raise` under a broad handler as an origin made `r0_capture._write_atomic`
    one; its try body cannot raise the family at all.)"""
    _, old, new = planted
    for table in (old.direct, new.direct):
        assert "audio_measure.rethrown_plateau" not in table
    assert "audio_measure.rethrown_plateau" not in new.guarded_only
    assert [(e["kind"], e["callee"], e["guarded"]) for e in new.one_hop["audio_measure.rethrown_plateau"]] == [
        ("direct", "audio_measure.plateau_db", False)]
    site = _site(new, "c_reraised_origin", how="import")
    assert (site["via"], site["through"]) == ("one-hop", ["audio_measure.plateau_db"]), site
    assert not _listed(old, "c_reraised_origin")


def test_a_boundary_handler_that_raises_again_does_not_cover_and_the_walk_never_saw_it(planted):
    """`except Exception as exc: ...; raise` catches the family and puts it
    back: the interpreter shows the refusal leaving `main()`. The #1763 walk
    read a handler by NAME, so under `--rules direct` this module is covered
    and in no table; under the default rules it is a candidate, and the row
    says why."""
    _, old, new = planted
    src = PLANTED["c_reraising_handler"]
    assert not _listed(old, "c_reraising_handler")
    assert _row(old, "c_reraising_handler")["walk"] is True
    row = _row(new, "c_reraising_handler")
    assert row["candidate"] and row["new"] and row["family_handlers"] == [], row
    assert row["reraising_handlers"] == [f"{_line(src, 'except Exception as exc')}:Exception"], row


def test_a_raise_of_the_caught_name_is_a_reraise_and_a_new_exception_is_not(planted):
    """`raise e` for the exception the handler bound puts it back; `raise
    Refused(...) from e` raises something else and stops the family."""
    _, _, new = planted
    row = _row(new, "c_raise_of_the_caught_name")
    src = PLANTED["c_raise_of_the_caught_name"]
    assert row["candidate"] and row["reraising_handlers"] == [
        f"{_line(src, 'except am.InsufficientEvidence as e')}:am.InsufficientEvidence"], row
    converted = _row(new, "c_converting_handler")
    src = PLANTED["c_converting_handler"]
    assert converted["reraising_handlers"] == [] and converted["family_handlers"] == [
        f"{_line(src, 'except am.InsufficientEvidence as e')}:am.InsufficientEvidence"], converted


def test_a_chain_beyond_one_hop_is_reported_not_listed(planted):
    """`SurgeRig.tone_gain_db` calls `SurgeRig.tone_project` and
    `SurgeRig.curve` calls `self.tone_project`, a one-hop origin, which calls
    `Estimate.require`: two hops. Out of #1782's scope, and the interpreter
    shows the caller escaping -- so the census names the chain and every
    module that calls into it rather than leaving them in no list at all."""
    _, _, new = planted
    for chain in ("reference_rigs.SurgeRig.tone_gain_db", "reference_rigs.SurgeRig.curve"):
        assert chain not in new.one_hop and chain not in new.direct, chain
    assert not _listed(new, "c_chain_two_hop")
    blind = new.to_json()["blind"]
    chains = blind["chains"]["reference_rigs.SurgeRig.tone_project"]
    assert sorted((c["caller"], c["path"], c["line"]) for c in chains) == sorted([
        ("reference_rigs.SurgeRig.tone_gain_db", "reference_rigs.py",
         _line(REFERENCE_RIGS, "return SurgeRig.tone_project(")),
        ("reference_rigs.SurgeRig.curve", "reference_rigs.py",
         _line(REFERENCE_RIGS, "return self.tone_project(")),
        # `capture_curve` calls the origin itself: one hop from it, and its
        # own caller in `main` is the two-hop site the section exists for
        ("c_known_escape.capture_curve", "c_known_escape.py",
         _line(PLANTED["c_known_escape"], "rr.SurgeRig.tone_project("))])
    assert [(s["callee"], s["line"], s["fn"]) for s in blind["chain_sites"]
            if s["path"] == "c_chain_two_hop.py"] == [
        ("reference_rigs.SurgeRig.tone_gain_db",
         _line(PLANTED["c_chain_two_hop"], "rr.SurgeRig.tone_gain_db("), "measure")]


# --- the known escape, in its own shape --------------------------------------

def test_the_known_escape_shape_is_invisible_to_the_old_rules_and_listed_by_the_new(planted):
    """`tools/f1_level_capture.py` `capture_curve`, replanted: a
    function-local `import reference_rigs as rr` and
    `rr.SurgeRig.tone_project(...)`, under `except refusal.Refused` in
    `main()`."""
    _, old, new = planted
    src = PLANTED["c_known_escape"]
    assert not _listed(old, "c_known_escape")
    row = _row(new, "c_known_escape")
    assert row["candidate"] and row["entry"] == "__main__"
    site = _site(new, "c_known_escape", how="class")
    assert (site["line"], site["fn"], site["how"], site["via"]) == (
        _line(src, "rr.SurgeRig.tone_project("), "capture_curve", "class", "one-hop"), site
    assert site["callees"] == ["reference_rigs.SurgeRig.tone_project"]
    assert site["through"] == ["audio_measure.Estimate.require"], site


def test_an_instance_call_on_a_local_variable_is_credited_by_method_name_and_flagged(planted):
    _, _, new = planted
    site = _site(new, "c_untyped_receiver", how="untyped-receiver")
    assert site["callees"] == ["reference_rigs.OurLadder.tone_gain_db"], site
    assert (site["via"], site["through"]) == ("one-hop", ["audio_measure.Estimate.require"])


# --- what counts as an Estimate, and what a guard has to name ----------------

def test_a_require_after_a_call_that_returns_no_estimate_is_not_attributed(planted):
    """Positive typing, not "a call into the estimator module followed by
    `.require()`": `rms` returns a float, and `.require` on it is an
    AttributeError (the interpreter's verdict), not a refusal."""
    _, _, new = planted
    row = _row(new, "c_non_estimate_require")
    assert row["sites"] == [] and not row["candidate"], row
    assert [u["receiver"] for u in new.unresolved_requires
            if u["path"] == "c_non_estimate_require.py"] == ["am.rms([1.0] * 16)"]


def test_an_estimate_arriving_as_a_parameter_is_a_declared_blind_spot(planted):
    _, _, new = planted
    un = [u for u in new.unresolved_requires if u["path"] == "c_parameter_require.py"]
    assert [(u["line"], u["fn"], u["receiver"]) for u in un] == [
        (_line(PLANTED["c_parameter_require"], "est.require("), "use", "est")], (
            new.unresolved_requires)


def test_a_subclass_only_handler_does_not_catch_the_family(planted):
    """`except am.NonFiniteAudio` catches that subclass alone; a plain
    `InsufficientEvidence` passes it (the interpreter: ESCAPED). #1763's
    prose listed `NonFiniteAudio` among the handlers that cover, and the
    walk that produced its table counted it the same way. Neither is right."""
    _, _, new = planted
    row = _row(new, "c_subclass_only_handler")
    assert row["candidate"] and row["family_handlers"] == []
    assert row["subclass_only_handlers"] == [
        f"{_line(PLANTED['c_subclass_only_handler'], 'except am.NonFiniteAudio')}"
        ":am.NonFiniteAudio"], row
    require = _site(new, "c_subclass_only_handler", via="require")
    assert require["classes"] == ["InsufficientEvidence"]


def test_an_alias_of_the_family_class_is_a_guard(planted):
    _, _, new = planted
    row = _row(new, "c_alias_handler")
    assert not row["candidate"]
    assert row["family_handlers"] == [
        f"{_line(PLANTED['c_alias_handler'], 'except IE')}:IE"], row


def test_the_established_spelling_of_an_estimate_and_an_estimator_all_resolve(planted):
    _, _, new = planted
    reexport = _site(new, "c_reexport_require", via="require")
    assert (reexport["via"], reexport["callees"]) == ("require", ["audio_measure.Estimate.require"])
    src = PLANTED["c_assigned_name_require"]
    assigned = _site(new, "c_assigned_name_require", via="require")
    assert (assigned["line"], assigned["via"]) == (_line(src, "e.require("), "require")
    assert _site(new, "c_from_import_alias", via="require")["via"] == "require"
    alias = _site(new, "c_module_alias", how="import")
    assert (alias["how"], alias["callees"]) == ("import", ["audio_measure.corner_from_curve"])


def test_a_library_with_the_call_and_no_handler_at_all_is_not_the_shape(planted):
    _, _, new = planted
    row = _row(new, "c_library_no_handler")
    assert row["sites"] and not row["printed"] and not row["candidate"], row


# ---------------------------------------------------------------------------
# the real tree, frozen: the escape and #1763's table, on the bytes that had them
# ---------------------------------------------------------------------------

def _tree_at(rev: str, dest: pathlib.Path) -> pathlib.Path:
    """Every tracked `.py` at `rev`, read out of git into `dest` -- all of
    them, the way the walk's `git ls-files *.py` listed them."""
    ls = subprocess.run(["git", "-C", str(ROOT), "ls-tree", "-r", "--name-only", rev],
                        capture_output=True, text=True)
    if ls.returncode != 0:
        pytest.skip(f"git ls-tree {rev[:8]} is unavailable in this clone: {ls.stderr.strip()[:80]}")
    names = [n for n in ls.stdout.splitlines() if n.endswith(".py")]
    batch = subprocess.run(["git", "-C", str(ROOT), "cat-file", "--batch"],
                           input="".join(f"{rev}:{n}\n" for n in names).encode(),
                           capture_output=True, check=True).stdout
    pos = 0
    for n in names:
        end = batch.index(b"\n", pos)
        size = int(batch[pos:end].split()[2])
        body = batch[end + 1:end + 1 + size]
        pos = end + 1 + size + 1
        p = dest / n
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(body)
    return dest


@pytest.fixture(scope="module")
def escape_pair(tmp_path_factory):
    tree = _tree_at(PINNED_ESCAPE_TREE, tmp_path_factory.mktemp("escape-tree"))
    return _read_at(tree, sorted(tree.rglob("*.py")))


@pytest.fixture(scope="module")
def old_at_table_tree(tmp_path_factory):
    tree = _tree_at(PINNED_1763_TREE, tmp_path_factory.mktemp("table-tree"))
    return _read_at(tree, sorted(tree.rglob("*.py")), rules=("direct",))[0]


def test_red_first_the_old_rules_miss_the_known_escape_and_the_new_rules_list_it(escape_pair):
    """THE control (#1782, "starts red"). On `origin/main` as it stood when
    the issue was filed, `tools/f1_level_capture.py` calls
    `reference_rigs.SurgeRig.tone_project` -- which raises through
    `Estimate.require` -- under handlers that name `Refused` alone. The #1763
    rules do not list the module; a census that does not find the known
    escape is wrong."""
    old, new = escape_pair
    assert "tools/f1_level_capture.py" not in [r["path"] for r in old.candidates]
    row, = [r for r in new.candidates if r["path"] == "tools/f1_level_capture.py"]
    assert row["entry"] == "__main__" and row["family_handlers"] == [], row
    tone_project = (["reference_rigs.SurgeRig.tone_project"], "one-hop", "class",
                    ["audio_measure.Estimate.require"])
    sites = [s for s in row["sites"] if s["callees"] == ["reference_rigs.SurgeRig.tone_project"]]
    got = {(s["line"], s["fn"]): (s["callees"], s["via"], s["how"], s["through"]) for s in sites}
    assert got == {(154, "_projected_low_db"): tone_project,
                   (362, "capture_curve"): tone_project}, got


def test_the_edges_of_the_issue_table_hold_on_the_frozen_tree(escape_pair):
    """#1782's table of edges ("by AST"), each read off the tree by the
    census: what it credits is what the AST holds, by line."""
    _, new = escape_pair
    assert new.direct["audio_measure.plateau_db"] == [2088]
    assert new.direct["audio_measure.Estimate.require"] == [378]
    assert new.direct["audio_measure.harmonic_signature"] == [2379, 2389]
    assert "audio_measure.corner_from_curve" not in new.direct
    assert {e["callee"]: e["guarded"] for e in new.one_hop["audio_measure.corner_from_curve"]} == {
        "audio_measure.plateau_db": False}
    assert {e["callee"]: e["guarded"] for e in new.one_hop["audio_measure.waveform_id"]} == {
        "audio_measure.cycle_average": True, "audio_measure.harmonic_signature": False}
    for origin in ("reference_rigs.SurgeRig.tone_project", "reference_rigs.OurLadder.tone_gain_db",
                   "reference_rigs.MiniV3Rig.tone_gain_db"):
        assert [e["kind"] for e in new.one_hop[origin]] == ["require"], origin
    # the chain #1782 put out of scope, on the bytes that have it
    chains = new.to_json()["blind"]["chains"]
    assert "reference_rigs.SurgeRig.tone_gain_db" in {
        c["caller"] for c in chains["reference_rigs.SurgeRig.tone_project"]}


TABLE_1763 = {
    # module: (estimator calls at their lines, Refused handler lines)
    "tools/measure_m5a_pulse_duty.py": ({"harmonic_signature": [84, 85]}, [140]),
    "tools/measure_m5a_signal_path.py": ({"harmonic_signature": [80, 90]}, [161]),
    "tools/pekonen_coloration_probe.py": ({"harmonic_signature": [116]}, [222, 281]),
    "tools/probes/f1_level_scaling.py": ({"plateau_db": [242, 324]}, [458]),
    "tools/probes/f1_selected_path.py": ({"plateau_db": [216, 344, 345, 346]}, [393]),
    "model/ladder_headroom.py": ({"harmonic_signature": [490]}, [390, 697]),
    "model/reference_voice.py": ({"harmonic_signature": [157]}, []),
    "tools/probes/corner_reference_convention.py": ({"plateau_db": [164]}, []),
    "tools/probes/holdout_f1d_preconditions.py": ({"plateau_db": [86]}, []),
    "tools/mono_m5a_score.py": ({"harmonic_signature": [219, 464, 465]}, [310]),
}
#: the #1751 site: in the walk's rows on this tree, in no table, because
#: #1763's was read with the #1751 repair applied
RS_MODE_DRIVE = "tools/probes/rs_mode_drive.py"


def test_old_rules_reproduce_the_ten_rows_of_1763_on_the_tree_they_were_read_from(
        old_at_table_tree):
    """AC: "a parity test that the old-rules mode reproduces #1763's 10-row
    table" -- every module, every estimator call at its line, every Refused
    handler at its line, and the counts around it (28 callers, 20 printed),
    on `e26d7f29`."""
    old = old_at_table_tree
    got = {r["path"]: r for r in old.candidates}
    assert set(got) == set(TABLE_1763) | {RS_MODE_DRIVE}
    assert len(old.direct) == 10, sorted(old.direct)
    assert "audio_measure.Estimate.require" not in old.direct
    for path, (calls, refused) in TABLE_1763.items():
        row = got[path]
        assert not row["is_test"] and row["family_handlers"] == [], row
        assert row["entry"] == ("none" if path == "tools/mono_m5a_score.py" else "__main__"), row
        lines: dict[str, list[int]] = {}
        for s in row["sites"]:
            assert s["via"] == "direct", s
            for c in s["callees"]:
                lines.setdefault(c.split(".")[-1], []).append(s["line"])
        assert lines == calls, (path, lines, calls)
        assert sorted({int(h.split(":")[0]) for h in row["refused_handlers"]}) == refused, (
            path, row["refused_handlers"])
    assert len([r for r in old.rows if r["n_sites"]]) == 28
    assert len([r for r in old.rows if r["n_sites"] and r["printed"]]) == 20


def test_the_new_rules_add_to_the_ten_and_take_none_away(escape_pair):
    old, new = escape_pair
    a = {r["path"] for r in old.candidates}
    b = {r["path"] for r in new.candidates}
    assert a <= b, sorted(a - b)
    assert "tools/f1_level_capture.py" in b - a


def _scratch_walk(root: pathlib.Path) -> list[tuple]:
    """The walk of #1763's first comment as posted, with two lines changed
    (each marked `changed`): it took its root from `argv` and listed tracked
    files through git. It returns the `rows` it built instead of printing
    them."""
    ROOT = pathlib.Path(root).resolve()                                   # changed
    files = sorted(ROOT.rglob("*.py"))                                    # changed

    def raises_classes(fn):
        out = set()
        for n in ast.walk(fn):
            if isinstance(n, ast.Raise) and isinstance(n.exc, ast.Call):
                e = n.exc.func
                out.add(e.attr if isinstance(e, ast.Attribute) else getattr(e, "id", None))
        return out

    am_tree = ast.parse((ROOT / "model/audio_measure.py").read_text())
    raising = set()
    for n in am_tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
                {"InsufficientEvidence", "NonFiniteAudio"} & raises_classes(n)):
            raising.add(n.name)

    def alias_map(tree):
        am_names, direct = set(), {}
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                for a in n.names:
                    if a.name == "audio_measure":
                        am_names.add(a.asname or "audio_measure")
            if isinstance(n, ast.ImportFrom) and n.module == "audio_measure":
                for a in n.names:
                    direct[a.asname or a.name] = a.name
        return am_names, direct

    def handler_classes(tree):
        named = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.ExceptHandler):
                t = n.type
                elts = t.elts if isinstance(t, ast.Tuple) else [t]
                for e in elts:
                    named.add(ast.unparse(e))
        return named

    def has_entry(tree):
        return any(isinstance(n, ast.If) and "__main__" in ast.unparse(n.test) for n in ast.walk(tree))

    rows = []
    for f in files:
        try:
            src = f.read_text()
            tree = ast.parse(src)
        except Exception:
            continue
        am_names, direct = alias_map(tree)
        if not am_names and not direct:
            continue
        calls = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Call):
                fn = n.func
                if isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name) and fn.value.id in am_names and fn.attr in raising:
                    calls.add(fn.attr)
                if isinstance(fn, ast.Name) and direct.get(fn.id) in raising:
                    calls.add(direct[fn.id])
        if not calls:
            continue
        h = handler_classes(tree)
        refused = sorted(x for x in h if x.endswith("Refused"))
        covers_ie = any(x.split(".")[-1] in ("InsufficientEvidence", "AssertionError", "Exception", "BaseException", "AM.InsufficientEvidence")
                        or x.split(".")[-1] in ("NonFiniteAudio",) for x in h)
        broad = [x for x in h if x.split(".")[-1] in ("Exception", "BaseException")]
        rows.append((str(f.relative_to(ROOT)), sorted(calls), refused, covers_ie, broad, has_entry(tree), "test_" in f.name))
    return rows


@pytest.fixture(scope="module")
def live_pair():
    return _pair(erc.tracked_files())


def test_old_rules_agree_with_the_1763_walk_on_this_tree(live_pair):
    """Differential parity, so it holds on whatever this tree becomes: for
    every module the #1763 walk lists, the committed old-rules mode lists it
    with the same calls, the same Refused handlers and the same entry point,
    and the same ones are candidates. The walk cannot read a bare `except:`
    (`ast.unparse(None)`), found by running it on a planted tree; a tree
    with one in an estimator caller skips here, saying so."""
    try:
        scratch = {r[0]: r for r in _scratch_walk(ROOT)}
    except AttributeError as e:
        pytest.skip(f"the #1763 walk cannot read this tree ({e}); its parity is unverified here")
    old, _ = live_pair
    mine = {r["path"]: r for r in old.rows if r["n_sites"]}
    assert set(mine) == set(scratch), sorted(set(mine) ^ set(scratch))
    for path, (_rel, calls, refused, covers, _broad, entry, is_test) in scratch.items():
        row = mine[path]
        assert sorted({c.split(".")[-1] for s in row["sites"] for c in s["callees"]}) == calls, path
        assert sorted({h.split(":", 1)[1] for h in row["refused_handlers"]}) == refused, path
        assert row["printed"] is bool(refused or entry), path
        assert row["is_test"] is is_test, path
        expected = bool(refused or entry) and not covers and not is_test
        assert row["candidate"] is expected, (path, row)


def test_the_two_rule_sets_are_reported_with_consistent_counts_and_labels(live_pair):
    """Not a pin (NOT A GATE): the numbers a report quotes must add up, and
    a row must carry what tells a new candidate from a #1763 row."""
    old, new = live_pair
    a = {r["path"] for r in old.candidates}
    b = {r["path"] for r in new.candidates}
    assert a <= b, sorted(a - b)
    out = new.to_json()
    assert out["counts"]["candidates_direct"] == len(a)
    assert out["counts"]["candidates_transitive"] == len(b)
    assert out["counts"]["candidates_new"] == len(b - a)
    assert out["counts"]["callers_direct"] == len([r for r in old.rows if r["n_sites"]])
    assert out["counts"]["callers_transitive"] == len([r for r in new.rows if r["n_sites"]])
    assert {r["path"] for r in new.candidates if r["new"]} == b - a
    assert all(r["candidate"] and not r["is_test"] and r["family_handlers"] == []
               for r in new.candidates)
    for r in new.rows:
        if r["n_sites"] and not r["is_test"]:
            assert r["raises"], r


# ---------------------------------------------------------------------------
# preconditions: an instrument in the wrong state refuses
# ---------------------------------------------------------------------------

def test_without_an_estimator_module_it_refuses(tmp_path):
    files = _plant(tmp_path, without=("audio_measure",))
    with pytest.raises(erc.PreconditionRefused, match="audio_measure"):
        erc.Census(list(files.values()))


def test_an_estimator_module_without_the_family_class_refuses(tmp_path):
    files = _plant(tmp_path, replace={"audio_measure": "x = 1\n"})
    with pytest.raises(erc.PreconditionRefused, match="InsufficientEvidence"):
        erc.Census(list(files.values()))


def test_two_modules_named_audio_measure_are_not_guessed(tmp_path):
    files = _plant(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    files["duplicate"] = elsewhere / "audio_measure.py"
    files["duplicate"].write_text(AUDIO_MEASURE)
    with pytest.raises(erc.PreconditionRefused, match="2 modules"):
        erc.Census(list(files.values()))


def test_a_require_that_raises_nothing_refuses_rather_than_censusing_nothing(tmp_path):
    """If `Estimate.require` stopped raising the family -- a rebase onto
    `refusal.Refused` is #1763's recorded design decision -- Rule 1 is void,
    and a census that silently reported no `.require()` origins would look
    exactly like one that found none."""
    assert REQUIRE_RAISING in AUDIO_MEASURE
    quiet = AUDIO_MEASURE.replace(REQUIRE_RAISING, REQUIRE_QUIET)
    files = _plant(tmp_path, replace={"audio_measure": quiet})
    with pytest.raises(erc.PreconditionRefused, match="require"):
        erc.Census(list(files.values()))


def test_a_file_that_does_not_parse_refuses(tmp_path):
    files = _plant(tmp_path, replace={"broken": "def f(:\n    pass\n"})
    with pytest.raises(erc.PreconditionRefused, match="broken"):
        erc.Census(list(files.values()))


def test_an_unknown_rules_value_is_refused(tmp_path):
    with pytest.raises(erc.PreconditionRefused, match="rules"):
        erc.Census(list(_plant(tmp_path).values()), rules="everything")


# ---------------------------------------------------------------------------
# main(): the report, its header, and its exit status
# ---------------------------------------------------------------------------

def _main_tree(tmp_path, monkeypatch, replace=None):
    tree = tmp_path / "tree"
    tree.mkdir()
    files = _plant(tree, replace=replace)
    monkeypatch.setattr(erc, "ROOT", tree)
    monkeypatch.setattr(erc, "tracked_files", lambda: list(files.values()))
    return tree


def _section(out: str, header: str) -> list[str]:
    """The lines under the line that starts with `header`, up to the next
    section."""
    lines = out.splitlines()
    start, = [i for i, ln in enumerate(lines) if ln.startswith(header)]
    end = next((i for i in range(start + 1, len(lines)) if lines[i][:1].isupper()), len(lines))
    return lines[start + 1:end]


def test_main_prints_the_tree_it_read_both_rule_sets_and_the_candidates(
        tmp_path, monkeypatch, capsys):
    _main_tree(tmp_path, monkeypatch)
    assert erc.main([]) == 0
    out = capsys.readouterr().out
    head, = [ln for ln in out.splitlines() if ln.startswith("# estimator_refusal_census")]
    assert "origin/main" in head and "behind" in head, head
    assert any("CANDIDATES, NOT DEFECTS" in ln for ln in out.splitlines()), out
    assert "rules=direct (the #1763 walk): 3 callers, 0 candidates" in out, out
    assert "rules=transitive: 21 callers, 13 candidates (13 new)" in out, out
    # the three the walk called covered are the two that raise again and the
    # one that converts: none is a Refused-only boundary the walk could see
    assert not [ln for ln in _section(out, "CANDIDATES (") if "c_converting_handler" in ln], out
    cand = _section(out, "CANDIDATES (")
    assert any(ln.startswith("  c_known_escape.py") for ln in cand), out
    assert "capture_curve" in "\n".join(cand), out
    assert "reference_rigs.SurgeRig.tone_project" in "\n".join(cand), out
    assert not any(ln.startswith("  c_require_guarded.py") for ln in cand), out
    assert any("c_require_guarded.py" in ln for ln in _section(out, "COVERED")), out
    assert any("c_library_no_handler.py" in ln for ln in _section(out, "NOT THE SHAPE")), out
    unattributed = _section(out, "UNRESOLVED .require()")
    assert any(ln.startswith("  c_parameter_require.py") for ln in unattributed), out
    chains = "\n".join(_section(out, "CHAINS BEYOND ONE HOP"))
    escape = [ln for ln in chains.splitlines()
              if ln.startswith("  reference_rigs.SurgeRig.tone_project")]
    assert escape and "c_chain_two_hop.py" in escape[0] and "c_known_escape.py" in escape[0], out


def test_main_json_is_machine_readable_and_host_independent(tmp_path, monkeypatch, capsys):
    tree = _main_tree(tmp_path, monkeypatch)
    assert erc.main(["--json"]) == 0
    text = capsys.readouterr().out
    assert str(tree) not in text, "the --json census must not carry host paths"
    out = json.loads(text)
    assert out["rules"] == "transitive"
    assert set(out["counts"]) >= {"candidates_direct", "candidates_transitive", "candidates_new",
                                  "callers_direct", "callers_transitive", "origins_direct",
                                  "origins_one_hop", "origins_guarded_only"}
    assert {"rev", "origin_main", "behind_origin_main"} <= set(out)
    rows = {r["path"]: r for r in out["rows"]}
    assert rows["c_known_escape.py"]["candidate"] is True
    assert rows["c_known_escape.py"]["new"] is True
    assert rows["c_library_no_handler.py"]["candidate"] is False
    assert "c_parameter_require.py" in {u["path"] for u in out["blind"]["unresolved_requires"]}


def test_main_rules_direct_is_the_1763_walk(tmp_path, monkeypatch, capsys):
    _main_tree(tmp_path, monkeypatch)
    assert erc.main(["--rules", "direct", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["rules"] == "direct"
    assert not [r["path"] for r in out["rows"] if r["candidate"]]


def test_main_refuses_with_exit_2_when_the_estimator_module_is_absent(
        tmp_path, monkeypatch, capsys):
    _main_tree(tmp_path, monkeypatch, replace={"audio_measure": "x = 1\n"})
    assert erc.main([]) == 2
    err = capsys.readouterr().err
    assert err.startswith("REFUSED") and "InsufficientEvidence" in err, err

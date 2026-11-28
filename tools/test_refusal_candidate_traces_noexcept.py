#!/usr/bin/env python3
"""The #1964 record read back against the tree it names, and the invariants it
rests on re-derived rather than carried.

`tools/refusal_candidate_traces_noexcept.py` traces the seven `candidate-escape`
rows that sit in the five files with no `except` clause, and records every one
of them as unreachable with refusing input: the call at the site executed and
came back in a working run, no input on the entry point's input space reaches
the callee's refusing arm, and no repair follows. A record of non-escapes is
where a bench passes most vacuously -- nothing escaped, so nothing had to be
seen escaping -- so each invariant is checked here against the tree, and each
check ships with the input that defeats it (docs/verification-rules.md rule 8):

  * `clap_burst_timing_qual`: `synth`'s signal cut to its onset, and the same
    signal with one NaN sample, both of which `prepare` REFUSES -- the two arms
    are live, and the test that says no `synth` output reaches them has been
    watched failing on the shapes that do;
  * `f1_filter_path`: constructions whose profile name is read off an argparse
    value, positional or keyword, through an aliased import, and a boolean
    expression where the check credits a conditional -- each of which the AST
    check over the tracked tree must report, by keyword and line.

THE COMMITTED RECORD is checked for what it says about itself -- seven rows,
each in the census it carries, every working run reaching its site with exit 0
and no traceback, no refusing role anywhere, every boundary run raising the
callee's own class -- the discipline `tools/test_refusal_candidate_trace.py`
applies to the #1576 record.

STATED, NOT CARRIED: `tools/run_case.py`'s F1 caller (`:2664`, the whole F1
train) and `tools/score_f1_rtl.py` were not run under the tracer. The first
passes `substitute_profile` only and the second builds the same path through
`RtlFilterChainPath.__init__`; the AST check reads both off the tree. A change
to WHAT `detect` hands `prepare` is seen by re-running the trace, not by the
lead test, which measures `synth`'s outputs. The AST check reads constructions
by the class's own name and its aliased imports; a path built through
`getattr`, `functools.partial` or a re-export under another name is invisible
to it.
"""
from __future__ import annotations

import ast
import json
import pathlib
import sys

import numpy as np
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for _p in ("", "tools", "model", "tools/probes", "fpga"):
    sys.path.insert(0, str(ROOT / _p))

import refusal_candidate_trace as rct  # noqa: E402
import refusal_candidate_traces_noexcept as spec  # noqa: E402
import refusal_sibling_scan as rss  # noqa: E402

import clap_burst_timing_qual as cb  # noqa: E402
import run_case as rc  # noqa: E402
import selected_preset as sp  # noqa: E402
import f1_filter_path as fp  # noqa: E402
import mono_m5a_score as m5  # noqa: E402

RECORD = ROOT / "docs/verification/refusal-candidate-trace-noexcept.json"
NEW = ("tools/clap_burst_timing_qual.py:132", "tools/f1_filter_path.py:95", "tools/f1_filter_path.py:98")
#: per row, the class its boundary script drove out of the callee's refusing arm
ARM = {"fpga/release/glide_boundary.py:242": "uart_host.Refused",
       "fpga/selected_preset.py:26": "mono_m5a_score.Refused",
       "fpga/selected_preset.py:28": "mono_m5a_score.Refused",
       "model/bd_pedestal.py:483": "bd_excitation_probe.Refused",
       "tools/clap_burst_timing_qual.py:132": "preparation_contract.Refused",
       "tools/f1_filter_path.py:95": "mono_m5a_score.Refused",
       "tools/f1_filter_path.py:98": "mono_m5a_score.Refused"}
AT_THE_SITE = {"tools/f1_filter_path.py:95", "tools/f1_filter_path.py:98"}


def _record() -> dict:
    return json.loads(RECORD.read_text())


# ---------------------------------------------------------------------------
# the committed record is internally consistent
# ---------------------------------------------------------------------------
def _check_record(record: dict) -> None:
    """Seven rows on a clean tree, each in the census the record carries; every
    working run reached its own site and the call came back with exit 0; no row
    has a refusing run, because none has a refusing input; every boundary run
    drove the callee's own Refused out of its arm and exited 0."""
    assert not record["tree_dirty"] and record["dirty_paths"] == []
    rows = {(c["site"], c["callee"]) for c in record["scanner"]["candidates"]}
    seen = set()
    for t in record["traces"]:
        assert (t["site"], t["callee"]) in rows, t["id"]
        assert t["id"] not in seen
        seen.add(t["id"])
        assert t["runs"]["refusing"] is None and t["invariant"], t["id"]
        working = t["runs"]["working"]
        assert working, t["id"]
        for rec in working:
            assert rec["verdict"] == rct.RETURNED and rec["site_executed"] == [t["site"]], t["id"]
            assert rec["exit_status"] == 0 and not rec["traceback"] and rec["uncaught"] is None, (t["id"], rec["entry"])
            assert all(c["outcome"] == "returned" for c in rec["calls"]), t["id"]
        [b] = t["runs"]["boundary"]
        assert b["exit_status"] == 0 and not b["traceback"], t["id"]
        assert b["all_raised"] == [ARM[t["id"]]], t["id"]
        assert any("raised " + ARM[t["id"]] in line for line in b["stdout_tail"]), t["id"]
        if t["id"] in AT_THE_SITE:
            # the library rows: the arm fired AT the site, and the clause that
            # took it is the boundary script's own -- never a production frame
            assert b["verdict"] == rct.HANDLED and b["site_executed"] == [t["site"]], t["id"]
            followed = b["follows"][-1]
            path, _, line = t["site"].rpartition(":")
            assert (followed["stack"][0]["file"], followed["stack"][0]["line"]) == (path, int(line))
            assert all(h["file"].endswith("f1-site-arms.py") for h in followed["handlers"]), t["id"]
        else:
            assert b["verdict"] == rct.NOT_REACHED, t["id"]    # the callee, called away from the site
    assert seen == set(spec.ALREADY_TRACED) | set(NEW)


def test_the_committed_record_names_what_it_traced():
    _check_record(_record())


def _first_boundary(record: dict) -> dict:
    return record["traces"][0]["runs"]["boundary"][0]


def _last_boundary(record: dict) -> dict:
    return record["traces"][-1]["runs"]["boundary"][0]


@pytest.mark.parametrize("change", [
    pytest.param(lambda r: r.__setitem__("tree_dirty", True), id="dirty-tree"),
    pytest.param(lambda r: r["traces"].pop(), id="a-row-missing"),
    pytest.param(lambda r: r["traces"][0]["runs"]["working"][0].__setitem__("verdict", rct.ESCAPED),
                 id="working-escaped"),
    pytest.param(lambda r: r["traces"][0]["runs"]["working"][0].__setitem__("traceback", True),
                 id="working-traceback"),
    pytest.param(lambda r: r["traces"][0]["runs"]["working"][0].__setitem__("site_executed", []),
                 id="site-never-reached"),
    pytest.param(lambda r: r["traces"][0]["runs"].__setitem__(
        "refusing", [dict(_first_boundary(r), verdict=rct.ESCAPED)]), id="a-refusing-run-appeared"),
    pytest.param(lambda r: _first_boundary(r).__setitem__("all_raised", []), id="boundary-raised-nothing"),
    pytest.param(lambda r: _last_boundary(r)["follows"][-1]["handlers"][0].__setitem__(
        "file", "tools/f1_rtl_filter_path.py"), id="a-production-frame-credited-as-the-handler"),
])
def test_the_record_check_refuses_a_doctored_record(change):
    """The control for the test above: each doctoring is a way the record could
    say something other than what this issue claims -- an escape, a run that
    never reached its site, a refusing input that appeared, a boundary that
    proved nothing, a production handler mistaken for the script's -- and the
    check must go red on every one, by its own assertion and not by a crash."""
    doctored = _record()
    change(doctored)
    with pytest.raises(AssertionError):
        _check_record(doctored)


def test_the_record_is_this_plan_on_a_clean_tree():
    """The record's revision carries the plan as committed: the four #1576 rows
    are that plan's rows unchanged, and the three new ones are only here."""
    record = _record()
    base = {t["id"]: t for t in spec.base.TRACES}
    for t in spec.TRACES:
        if t["id"] in spec.ALREADY_TRACED:
            assert t == base[t["id"]], t["id"]
    assert len(base) == 37 and len(spec.TRACES) == 7
    assert not set(base) & set(NEW) and set(base) >= set(spec.ALREADY_TRACED)
    assert int(record["scanner"]["classes"]["candidate-escape"]) == len(record["scanner"]["candidates"])
    assert all(c["classification"] == "candidate-escape" for c in record["scanner"]["candidates"])
    assert {t["id"] for t in record["traces"]} <= {c["site"] for c in record["scanner"]["candidates"]}


# ---------------------------------------------------------------------------
# clap_burst_timing_qual.py:132 -- the lead excludes prepare's only arms
# ---------------------------------------------------------------------------
def _exact_lead(x: np.ndarray, sr: int) -> bool:
    """What excludes both of `prepare`'s arms: finite throughout, and the first
    50 ms exactly zero, so the onset cannot be at index 0."""
    return bool(np.isfinite(x).all()) and not np.any(x[:int(0.050 * sr)])


@pytest.mark.parametrize("sr", (44100, 48000))
@pytest.mark.parametrize("noisy", (True, False))
@pytest.mark.parametrize("r", (0, 1, cb.N_REAL - 1))
def test_every_signal_the_cli_hands_prepare_starts_with_exact_silence(sr, noisy, r):
    """`run` seeds each realisation `1000 * r + sr`; every condition, both
    carriers. `prepare` refuses a non-finite record or one whose onset is at
    index 0; each of these is finite and begins with 50 ms of exact zeros, and
    `prepare` returns for it."""
    for cname, (strikes, tail, _role) in cb.CONDITIONS.items():
        x = cb.synth(strikes, tail, sr, seed=1000 * r + sr, noise=noisy)
        assert _exact_lead(x, sr), (cname, sr, noisy, r)
        assert isinstance(rc.prepare(x, sr, side="synthetic"), np.ndarray), (cname, sr, noisy, r)


def test_the_two_arms_are_live_on_the_shapes_that_do_reach_them():
    """The control for the test above: strip the lead and `prepare` refuses the
    record as cut into the strike; put one NaN in it and `prepare` refuses it
    as non-finite -- and `_exact_lead` says False of both, so the guard is not
    satisfied by what defeats it. The onset is the lead's own end: nothing
    precedes it."""
    strikes, tail, _role = cb.CONDITIONS["S4 four short"]
    x = cb.synth(strikes, tail, 44100, seed=1000 + 44100)
    onset = int(np.argmax(np.abs(x) > rc.ONSET_FRAC * np.abs(x).max()))
    assert onset == int(0.05 * 44100)
    assert _exact_lead(x, 44100)
    with pytest.raises(rc.Refused, match="cut into the strike"):
        rc.prepare(x[onset:], 44100, side="synthetic")
    assert not _exact_lead(x[onset:], 44100)
    x[len(x) // 2] = np.nan
    with pytest.raises(rc.Refused, match="non-finite"):
        rc.prepare(x, 44100, side="synthetic")
    assert not _exact_lead(x, 44100)


# ---------------------------------------------------------------------------
# f1_filter_path.py:95, :98 -- no production code names a profile the engine lacks
# ---------------------------------------------------------------------------
PATH_CLASSES = ("SelectedFilterPath", "RtlFilterChainPath")
PROFILE_KEYS = ("profile", "substitute_profile")
LIB, RTL = "tools/f1_filter_path.py", "tools/f1_rtl_filter_path.py"
CALLERS = {RTL, "tools/run_case.py", "tools/score_f1_rtl.py", "tools/probes/holdout_f1d_preconditions.py"}


def _names(tree: ast.AST) -> dict[str, str]:
    """Every name each path class is called under in this file, to its class:
    its own, and any `from <module> import <Class> as <alias>` of it."""
    out = {c: c for c in PATH_CLASSES}
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            out |= {a.asname: a.name for a in n.names if a.name in PATH_CLASSES and a.asname}
    return out


def _constructions(sources: list[tuple[str, str]]) -> list[dict]:
    """Every call that builds one of the two path classes: the file and line,
    the class, the value it passes for each profile keyword (the first
    positional argument is `profile` in both), and whether it is the
    pass-through inside `RtlFilterChainPath.__init__`."""
    found = []
    for name, src in sources:
        tree = ast.parse(src)
        names = _names(tree)
        pass_through: set[int] = set()
        for c in ast.walk(tree):
            if isinstance(c, ast.ClassDef) and c.name == "RtlFilterChainPath" and name == RTL:
                for f in c.body:
                    if isinstance(f, ast.FunctionDef) and f.name == "__init__":
                        pass_through |= {id(n) for n in ast.walk(f)}
        for n in ast.walk(tree):
            if not isinstance(n, ast.Call):
                continue
            f = n.func
            cls = names.get(f.id if isinstance(f, ast.Name) else getattr(f, "attr", None))
            if cls is None:
                continue
            values = {"profile": n.args[0]} if n.args else {}
            values |= {k.arg: k.value for k in n.keywords if k.arg in PROFILE_KEYS}
            found.append(dict(file=name, line=n.lineno, cls=cls, values=values, pass_through=id(n) in pass_through))
    return found


def _literal(node: ast.expr, keys: set[str]) -> bool:
    """`None`, a literal key of `ENGINE_PROFILES`, or a conditional between two of those."""
    return (isinstance(node, ast.Constant) and (node.value is None or node.value in keys)) or (
        isinstance(node, ast.IfExp) and _literal(node.body, keys) and _literal(node.orelse, keys))


def _violations(found: list[dict], keys: set[str]) -> list[str]:
    """`RtlFilterChainPath` takes no profile from its caller at all. For
    `SelectedFilterPath`, a profile is sound when it is absent, `None`, a key,
    or -- for `substitute_profile` -- a conditional between those; the one bare
    Name allowed is `RtlFilterChainPath.__init__` handing its own parameter on,
    whose defaults are checked separately."""
    bad = []
    for c in found:
        if c["cls"] == "RtlFilterChainPath":
            if c["values"]:
                bad.append(f"{c['file']}:{c['line']} RtlFilterChainPath takes no profile from its caller: "
                           + ", ".join(f"{k}={ast.unparse(v)}" for k, v in c["values"].items()))
            continue
        for k, node in c["values"].items():
            if c["pass_through"] and isinstance(node, ast.Name) and node.id == k:
                continue
            if k == "substitute_profile" and _literal(node, keys):
                continue
            bad.append(f"{c['file']}:{c['line']} {k}={ast.unparse(node)}")
    return bad


def _init_default(src: str, cls: str, arg: str) -> str:
    """The source of `cls.__init__`'s default for `arg`, positional or keyword-only."""
    for c in ast.walk(ast.parse(src)):
        if isinstance(c, ast.ClassDef) and c.name == cls:
            for f in c.body:
                if isinstance(f, ast.FunctionDef) and f.name == "__init__":
                    defaults = {a.arg: d for a, d in zip(f.args.args[-len(f.args.defaults):], f.args.defaults)}
                    defaults |= {a.arg: d for a, d in zip(f.args.kwonlyargs, f.args.kw_defaults) if d is not None}
                    return ast.unparse(defaults[arg])
    raise AssertionError(f"no {cls}.__init__ default for {arg}")


def _tracked_sources() -> list[tuple[str, str]]:
    return [(str(p.relative_to(rss.ROOT)), p.read_text()) for p in rss.tracked_files()
            if any(str(p.relative_to(rss.ROOT)).startswith(r_ + "/") for r_ in rss.SCAN_ROOTS)]


def test_no_production_code_names_a_profile_the_engine_lacks():
    """`engine_configuration` refuses a name that is not a key of
    `ENGINE_PROFILES`; nothing that reaches `f1_filter_path.py:95` or `:98`
    can pass one. The construction sites and the two `__init__` defaults are
    read off the tracked tree, not remembered; a new caller turns this red
    until it has been read and added to CALLERS."""
    keys = set(m5.ENGINE_PROFILES)
    found = _constructions(_tracked_sources())
    assert not _violations(found, keys), _violations(found, keys)
    assert {c["file"] for c in found} == CALLERS
    assert fp.ENGINE_PROFILE in keys
    rtl = pathlib.Path(rss.ROOT / RTL).read_text()
    assert _init_default(rtl, "RtlFilterChainPath", "profile") == "fp.ENGINE_PROFILE"
    assert _init_default(rtl, "RtlFilterChainPath", "substitute_profile") == "None"
    lib = pathlib.Path(rss.ROOT / LIB).read_text()
    assert _init_default(lib, "SelectedFilterPath", "profile") == "ENGINE_PROFILE"
    assert _init_default(lib, "SelectedFilterPath", "substitute_profile") == "None"


def test_the_ast_check_reports_the_constructions_that_would_defeat_it():
    """The control for the test above: a profile read off an argparse value,
    positional and keyword; a substitute passed into `RtlFilterChainPath`; a
    boolean expression where the check credits a conditional; and the same
    defect through an aliased import -- each reported by keyword and line. The
    sound shapes -- the defaults, and `run_case.py:2664`'s conditional -- are
    not."""
    keys = set(m5.ENGINE_PROFILES)
    sound = ("import f1_filter_path as fp\n"
             "a = fp.SelectedFilterPath()\n"
             "b = fp.SelectedFilterPath(substitute_profile='legacy' if inject == 'F1_LEGACY_SUBSTITUTE' else None)\n"
             "c = fp.SelectedFilterPath(substitute_profile=None)\n")
    assert _violations(_constructions([("<sound>", sound)]), keys) == []
    mutant = ("import f1_filter_path as fp\n"
              "from f1_filter_path import SelectedFilterPath as SFP\n"
              "from f1_rtl_filter_path import RtlFilterChainPath\n"
              "a = fp.SelectedFilterPath(profile=a.profile)\n"
              "b = fp.SelectedFilterPath(a.positional)\n"
              "c = RtlFilterChainPath(substitute_profile=a.substitute)\n"
              "d = fp.SelectedFilterPath(substitute_profile=a.name or 'legacy')\n"
              "e = SFP(profile=a.other)\n"
              "f = fp.SelectedFilterPath(substitute_profile='no-such-engine')\n")
    assert _violations(_constructions([("<mutant>", mutant)]), keys) == [
        "<mutant>:4 profile=a.profile",
        "<mutant>:5 profile=a.positional",
        "<mutant>:6 RtlFilterChainPath takes no profile from its caller: substitute_profile=a.substitute",
        "<mutant>:7 substitute_profile=a.name or 'legacy'",
        "<mutant>:8 profile=a.other",
        "<mutant>:9 substitute_profile='no-such-engine'",
    ]


def test_the_pass_through_is_credited_only_inside_rtlfilterchainpath_init():
    """A bare Name is sound in exactly one place: `RtlFilterChainPath.__init__`
    handing its own parameter on. The same line anywhere else is a profile read
    off a caller, and is reported."""
    keys = set(m5.ENGINE_PROFILES)
    line = "fp.SelectedFilterPath(profile, calibration, substitute_profile=substitute_profile)\n"
    assert _violations(_constructions([("tools/elsewhere.py", line)]), keys) == [
        "tools/elsewhere.py:1 profile=profile", "tools/elsewhere.py:1 substitute_profile=substitute_profile"]
    in_init = "import f1_filter_path as fp\nclass RtlFilterChainPath:\n    def __init__(self):\n        " + line
    assert _violations(_constructions([(RTL, in_init)]), keys) == []


# ---------------------------------------------------------------------------
# the four #1576 rows, their invariants on this tree
# ---------------------------------------------------------------------------
def test_selected_preset_passes_only_names_the_callee_accepts():
    """`definition` hands `engine_configuration` the literal 'selected' and
    `_patch_for_wave` a wave chosen from NAMES; both return, and the arm is
    live for a name outside each set."""
    keys = set(m5.ENGINE_PROFILES)
    assert m5.engine_configuration("selected") and "selected" in keys
    waves = {"pulse" if n == "m5a-pulse" else "saw" for n in sp.NAMES}
    assert waves <= {"saw", "pulse"}
    patch = m5._voice_patch(json.loads(m5.MANIFEST.read_text()))
    for w in waves:
        assert m5._patch_for_wave(patch, w) == m5._patch_for_wave(patch, w, m5.M5A_PULSE_WAVE)
    with pytest.raises(m5.Refused, match="unsupported Mono engine configuration"):
        m5.engine_configuration("no-such-engine")
    with pytest.raises(m5.Refused, match="unsupported Mono waveform"):
        m5._patch_for_wave(patch, "triangle")


def test_the_bd_calibration_render_is_not_silent():
    """`main` passes the literals `calibrate_amp(mf.BP, 1.0, 0)`; the render
    they produce is not silent, which is the only thing `calibrate_amp`
    refuses on -- and it refuses on a silent one."""
    import modal_fixed as mf
    import bd_excitation_probe as bx
    assert bx.calibrate_amp(mf.BP, 1.0, 0) > 0
    with pytest.raises(bx.Refused, match="calibration render is silent"):
        bx.calibrate_amp(mf.BP, 1.0, 0, probe_amp=0.0)


def test_wrapper_capture_never_plans_a_gate_off_marker():
    """`plan_show`'s refusing arms all need a `gate-off` marker command or a
    `hold_frames`; `wrapper_capture` builds neither. Its call passes the
    command list and nothing else, and the marker word is nowhere in the file."""
    source = (ROOT / "fpga/release/glide_boundary.py").read_text()
    assert "gate-off" not in source
    calls = [n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute) and n.func.attr == "plan_show"]
    assert [(ast.unparse(c.args[0]), [k.arg for k in c.keywords]) for c in calls] == [("cmds", [])]

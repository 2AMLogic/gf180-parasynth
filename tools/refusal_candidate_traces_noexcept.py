#!/usr/bin/env python3
"""The #1964 trace plan: the seven `candidate-escape` rows that sit in the five
tracked files with no `except` clause at all.

#1878's closing class search ran `tools/refusal_sibling_scan.py` and found 68
`candidate-escape` rows in 37 files; five of those files have no `except`
clause anywhere, which is #1878's file-level shape with other callees. The
scan is an inventory: it cannot say whether any input reaches the call with a
refusing argument. It also does not know that #1576 had already traced four of
these files by execution (`tools/refusal_candidate_traces.py`: the rows
`glide_boundary.py:242`, `selected_preset.py:26`, `:28` and
`bd_pedestal.py:483`, recorded under "no refusing input on the entry point's
input space"). So this plan is two kinds of row:

  * the four #1576 rows, taken from that plan by id and re-run UNCHANGED at
    this revision -- the same working input, the same boundary script, the
    same stated invariant;
  * the three rows never traced before: `tools/clap_burst_timing_qual.py:132`
    and `tools/f1_filter_path.py:95`, `:98`.

Every row has a WORKING run in which the call at the site executed and came
back, and NO `refusing` run, because no input on the entry point's input space
reaches the callee's refusing arm. That is a verdict the entry-point run and a
boundary script establish together and neither establishes alone: the
boundary script calls the callee -- or the library call at the site -- with the
argument that DOES reach the arm, to show the arm is live and what it raises;
the entry-point run shows the caller never passes it. `invariant` says why, and
`tools/test_refusal_candidate_traces_noexcept.py` re-derives each invariant
mechanically on the tree, so "unreachable" is checked rather than carried.

`tools/f1_filter_path.py` is a library: its two rows are traced through its
callers' CLIs (`tools/probes/holdout_f1d_preconditions.py`, which builds the
path directly, and `tools/f1_rtl_filter_path.py`, which builds it through
`RtlFilterChainPath`), and their boundary script builds the path with the
refusing name at the site itself.

Prerequisites, each REFUSED as an `unresolved` row rather than skipped: the
restored frozen reference audio for the two F1 caller CLIs
(`python tools/refprofile_restore.py`); iverilog on `PATH` for the glide and
F1 RTL runs. What each run did is in
`docs/verification/refusal-candidate-trace-noexcept.json`.
"""
from __future__ import annotations

import pathlib

import refusal_candidate_traces as base

set_scratch = base.set_scratch

run, script, TOOLS_PATH = base.run, base.script, base.TOOLS_PATH

#: the #1576 rows, by id in that plan; each is re-run as it stands there
ALREADY_TRACED = (
    "fpga/release/glide_boundary.py:242",
    "fpga/selected_preset.py:26",
    "fpga/selected_preset.py:28",
    "model/bd_pedestal.py:483",
)
_by_id = {t["id"]: t for t in base.TRACES}

CLAP = "tools/clap_burst_timing_qual.py"
F1 = "tools/f1_filter_path.py"
HOLDOUT = "tools/probes/holdout_f1d_preconditions.py"
F1_RTL = "tools/f1_rtl_filter_path.py"
M5A = ["mono_m5a_score.engine_configuration", "mono_m5a_score._patch_for_wave"]

CLAP_ARMS = """\
import numpy as np
import run_case as rc
import clap_burst_timing_qual as cb
n = 0
for sr in (44100, 48000):
    for noisy in (True, False):
        for cname, (strikes, tail, role) in cb.CONDITIONS.items():
            x = cb.synth(strikes, tail, sr, seed=1000 + sr, noise=noisy)
            lead = int(0.050 * sr)
            assert not np.any(x[:lead]), (cname, sr, noisy)
            y = rc.prepare(x, sr, side="synthetic")
            n += 1
print("prepare(synth(...), sr, side='synthetic') returned for all %d conditions x sample rates x carriers;"
      " each begins with 50 ms of exact digital silence" % n)
strikes, tail, role = cb.CONDITIONS["S4 four short"]
x = cb.synth(strikes, tail, 44100, seed=144100)
onset = int(np.argmax(np.abs(x) > rc.ONSET_FRAC * np.abs(x).max()))
print("S4 @ 44100: onset index", onset, "-- the lead is", int(0.05 * 44100), "samples")
try:
    rc.prepare(x[onset:], 44100, side="synthetic")
    print("NO RAISE")
except Exception as e:
    print("prepare(synth cut to its onset) raised %s.%s -- %s" % (type(e).__module__, type(e).__name__, str(e)[:90]))
x[len(x) // 2] = np.nan
try:
    rc.prepare(x, 44100, side="synthetic")
    print("NO RAISE")
except Exception as e:
    print("prepare(synth with one NaN sample) raised %s.%s -- %s" % (type(e).__module__, type(e).__name__, str(e)[:90]))
"""

F1_SITE_ARMS = """\
import mono_m5a_score as m5
import f1_filter_path as fp
print("ENGINE_PROFILES keys:", sorted(m5.ENGINE_PROFILES), "-- ENGINE_PROFILE is", repr(fp.ENGINE_PROFILE))
for kw in (dict(), dict(substitute_profile="legacy")):
    try:
        p = fp.SelectedFilterPath(**kw)
        print("SelectedFilterPath(%s) built: profile %r, probe_profile %r"
              % (kw or "defaults", p.profile["name"], p.probe_profile["name"]))
    except Exception as e:
        print("SelectedFilterPath(**%r) raised %s.%s -- %s"
              % (kw, type(e).__module__, type(e).__name__, str(e)[:90]))
for kw in (dict(profile="no-such-engine"), dict(substitute_profile="no-such-engine")):
    try:
        fp.SelectedFilterPath(**kw)
        print("NO RAISE")
    except Exception as e:
        print("SelectedFilterPath(**%r) raised %s.%s -- %s"
              % (kw, type(e).__module__, type(e).__name__, str(e)[:90]))
"""

TRACES: list[dict] = [dict(_by_id[i]) for i in ALREADY_TRACED]

TRACES.append(dict(
    id=f"{CLAP}:132", site=f"{CLAP}:132", callee="run_case.prepare", wrap=["run_case.prepare"],
    working=lambda ws: run(CLAP, ["--out", str(ws / "burst-timing-qual.json")],
                           note="the default --n 24: every condition, detector and sample rate, 1296 calls at :132"),
    refusing=None,
    boundary=[lambda ws: script(ws, "clap-prepare-arms", CLAP_ARMS, TOOLS_PATH)],
    invariant="every signal detect() hands prepare is synth(...)'s, which begins with int(0.050 * sr) samples of "
              "exact digital silence (env is np.zeros(n) until lead) and is finite by construction (unit-RMS "
              "band-passed Gaussian noise, or an alternating +-1 carrier); prepare refuses only a non-finite "
              "record or one whose onset is at index 0, and --n and --out do not touch the signal"))

def f1_callers(ws: pathlib.Path) -> list[dict]:
    """The two caller CLIs that reach f1_filter_path.py:95 and :98, each run
    once per session (a shared name stands for ONE run) and viewed by both rows."""
    return [run(HOLDOUT, [], note="builds fp.SelectedFilterPath() with every default at :93",
                share="f1-holdout"),
            run(F1_RTL, ["--json", str(base.SCRATCH / "f1-rtl-smoke-curve.json")],
                note="the smoke run: RtlFilterChainPath(inject=None, frames=None) at :423 builds "
                     "fp.SelectedFilterPath(fp.ENGINE_PROFILE, fp.CALIBRATION, substitute_profile=None) at :219",
                share="f1-rtl-smoke")]


TRACES += [
    dict(id=f"{F1}:95", site=f"{F1}:95", callee="mono_m5a_score.engine_configuration", wrap=M5A,
         working=f1_callers,
         refusing=None,
         boundary=[lambda ws: script(ws, "f1-site-arms", F1_SITE_ARMS, TOOLS_PATH)],
         invariant="profile is never a CLI argument: SelectedFilterPath.__init__'s default is the literal "
                   "ENGINE_PROFILE = 'selected', and every production construction leaves it there -- "
                   "tools/probes/holdout_f1d_preconditions.py:93 passes nothing; RtlFilterChainPath.__init__ "
                   "(tools/f1_rtl_filter_path.py:207) defaults it to fp.ENGINE_PROFILE and passes it through at "
                   ":219, and its two constructions (tools/f1_rtl_filter_path.py:423, tools/score_f1_rtl.py:204) "
                   "pass neither; tools/run_case.py:2664 passes substitute_profile only. 'selected' is a key of "
                   "mono_m5a_score.ENGINE_PROFILES, the only thing engine_configuration refuses on"),
    dict(id=f"{F1}:98", site=f"{F1}:98", callee="mono_m5a_score.engine_configuration", wrap=M5A,
         working=f1_callers,
         refusing=None,
         boundary=[lambda ws: script(ws, "f1-site-arms", F1_SITE_ARMS, TOOLS_PATH)],
         invariant="substitute_profile or profile is one of three values at every production construction: None "
                   "(the default, at holdout_f1d_preconditions.py:93, through RtlFilterChainPath.__init__ at "
                   "f1_rtl_filter_path.py:219, and at run_case.py:2665 unless inject == 'F1_LEGACY_SUBSTITUTE') "
                   "or the literal 'legacy' (run_case.py:2665) or 'selected' (the profile default). All three "
                   "but None are keys of ENGINE_PROFILES. The 'legacy' substitute is then refused by the "
                   "module's OWN class at f1_filter_path.py:102, which run_case.py:2670 catches -- a designed "
                   "control, not this row"),
]

assert len({t["id"] for t in TRACES}) == len(TRACES) == 7, len(TRACES)   # the seven rows of #1964
assert all(t["refusing"] is None for t in TRACES)                       # no entry point has a refusing input

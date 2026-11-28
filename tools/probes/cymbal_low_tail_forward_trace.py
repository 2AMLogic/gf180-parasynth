#!/usr/bin/env python3
"""cymbal_low_tail_forward_trace -- the trace that chose the repaired
FORWARD_INTEGRAL (#1869), kept because its results are quoted.

`tools/cymbal_low_tail.py`'s `FORWARD_INTEGRAL` control is held to ANSWERING a
wrong time. Three other readings of "integrate forwards" were run before that
one was chosen, and each is a result worth keeping:

  unrepaired  the module before #1869, read straight out of git at BASE:
              normalised by sch[0], the onset's first sample. No measurement
              at all -- Refused where that sample sits below the floor, None
              at every depth where it sits above. check() credited the
              refusal as a catch.
  A           normalised by its own total, still read as the first sample at
              or below the depth: 0.00 ms at every depth on every record, and
              measure() then dies on 0/0 computing rho.
  B           integrated forward and read back-to-front: it crosses late, the
              end-margin guard refuses every depth, and it is red through
              None -- the refusal again.
  shipped     integrated forward, read where the rising curve REACHES the
              depth: the energy-arrival time.

The probe mirrors `schroeder_times` for the readings the tool does not
implement, and first proves the mirror exact: its clean path must reproduce
the tool's own output, dict for dict, on every condition and band, and so must
its copy of the shipped reading; with BASE in the clone, so must its copy of
the unrepaired one against the module git hands back. A mirror that had
drifted would make the table a trace of the probe rather than of the
instrument, so any drift fails the run.

Run:   python3 tools/probes/cymbal_low_tail_forward_trace.py
Exit:  0 when every self-check passed and every reading still behaves as the
       record above says; 1 otherwise.
"""
from __future__ import annotations

import functools
import importlib.util
import math
import pathlib
import subprocess
import sys
import tempfile

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[2]
for _p in (ROOT / "tools", ROOT / "model"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import cymbal_bands as cb  # noqa: E402
import cymbal_low_tail as lt  # noqa: E402

BASE = "528a228b"            # origin/main when #1869 was built: the unrepaired module
# A COPY of lt.TRUNC_MARGIN_DB, deliberately not a live read: a mirror that
# followed the instrument's constants could not report the instrument changing.
MARGIN_DB = 15.0
READINGS = ("unrepaired", "A", "B", "shipped")
CONDS = (
    ("tau 0.35, 2.0 s (the edt-known record)", {"tau_l": 0.35}, 2.0, 3.0),
    ("tau 0.10, 1.5 s", {"tau_l": 0.10}, 1.5, 3.0),
    ("tau 0.60, 3.0 s", {"tau_l": 0.60}, 3.0, 4.0),
    ("tau 0.35, 2.0 s, floor 0.03", {"tau_l": 0.35, "floor": 0.03}, 2.0, 3.0),
    ("tau 0.35, 2.0 s, seed 99", {"tau_l": 0.35, "seed": 99}, 2.0, 3.0),
)
RECORD = {
    "unrepaired": "raises Refused",
    "A": "raises ZeroDivisionError",
    "B": "answers with edt-known (False, None)",
    "shipped": "answers with edt-known red on a number",
}


@functools.cache
def _bands(i: int) -> dict:
    kw, w, dur = CONDS[i][1:]
    y, sr = lt.synth(a_h=0.0, dur=dur, **kw)
    y = lt._trim(y, sr, w)
    return {b: cb._bp(y, sr, *lt.BANDS[b]) for b in lt.BANDS}


def _mirror(reading):
    """A `schroeder_times` for one reading. It differs from the tool's in the
    two lines the issue is about, and copies everything else verbatim."""
    def schroeder_times(x, sr, depths=lt.DEPTHS, *, defect=None):
        x = np.asarray(x, dtype=np.float64)
        p = x * x
        nf = max(4, int(0.1 * sr))
        floor = float(np.mean(p[-nf:]))
        sm = max(1, int(0.05 * sr))
        env = np.convolve(p, np.ones(sm) / sm, mode="same")
        pk = float(np.max(env))
        if pk <= 0:
            raise lt.Refused("band is silent")
        q = np.maximum(p - (0.0 if defect == "NO_FLOOR_SUB" else floor), 0.0)
        if reading == "clean" or defect != "FORWARD_INTEGRAL":
            sch, end, rising = np.cumsum(q[::-1])[::-1], 0, False
        elif reading == "unrepaired":
            sch, end, rising = np.cumsum(q), 0, False
        elif reading == "A":
            sch, end, rising = np.cumsum(q), -1, False
        elif reading == "B":
            sch, end, rising = np.cumsum(q)[::-1], 0, False
        else:
            sch, end, rising = np.cumsum(q), -1, True
        ref = float(sch[end])
        if ref <= 0:
            raise lt.Refused("band holds no energy above its floor")
        c = 10 * np.log10(np.maximum(sch / ref, 1e-30))
        t = np.arange(len(c)) / sr
        lvl_end = 10 * math.log10(max(float(np.mean(env[-sm:])), 1e-30) / pk)
        out = {"floor_db_re_peak": round(10 * math.log10(max(floor, 1e-30) / pk), 2),
               "times_ms": {}, "refused": {}, "margin_db": {}}
        for d in depths:
            key = f"{d:.0f}"
            i = np.nonzero(c >= d)[0] if rising else np.nonzero(c <= d)[0]
            if not len(i):
                out["times_ms"][key] = None
                out["refused"][key] = f"the energy curve never reaches {d:.0f} dB"
                continue
            j = int(i[0])
            lvl = 10 * math.log10(max(env[j], 1e-30) / pk)
            out["margin_db"][key] = round(lvl - lvl_end, 2)
            if defect != "NO_TRUNC_GUARD" and lvl - lvl_end < MARGIN_DB:
                out["times_ms"][key] = None
                out["refused"][key] = (f"analysed window too short: the envelope at its end is "
                                       f"only {lvl - lvl_end:.1f} dB below its level at the "
                                       f"{d:.0f} dB crossing (need {MARGIN_DB})")
                continue
            out["times_ms"][key] = round(1e3 * float(t[j]), 2)
        return out
    return schroeder_times


def _call(f, x, sr, defect):
    try:
        return ("answered", f(x, sr, defect=defect))
    except Exception as e:                      # noqa: BLE001 -- what escapes is the measurement
        return ("raised", type(e).__name__, str(e))


def _under(reading, fn):
    """Run `fn` with the tool's `schroeder_times` swapped for the mirror."""
    tool = lt.schroeder_times
    lt.schroeder_times = _mirror(reading)
    try:
        return fn()
    finally:
        lt.schroeder_times = tool


def _unrepaired_from_git():
    """The module as it was before #1869, loaded from git rather than
    reconstructed -- or None when BASE is not in this clone (a shallow one)."""
    r = subprocess.run(["git", "show", f"{BASE}:tools/cymbal_low_tail.py"], cwd=ROOT,
                       capture_output=True, text=True, check=False)
    if r.returncode != 0 or "else np.cumsum(q)" not in r.stdout:
        return None
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "cymbal_low_tail_unrepaired.py"
        p.write_text(r.stdout)
        spec = importlib.util.spec_from_file_location("cymbal_low_tail_unrepaired", p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    return mod


def _outcome(mod):
    try:
        got = mod.properties(defect="FORWARD_INTEGRAL")
    except Exception as e:                      # noqa: BLE001
        return ("raised", type(e).__name__, str(e))
    return ("answered", got["edt-known"], tuple(k for k in mod.PROPERTIES if got[k][0] is False))


def outcomes() -> dict:
    """What `properties(defect='FORWARD_INTEGRAL')` does under each reading."""
    old = _unrepaired_from_git()
    out = {"unrepaired": _outcome(old) if old else _under("unrepaired", lambda: _outcome(lt)),
           "unrepaired_from": f"git {BASE}" if old else "the mirror (BASE is not in this clone)"}
    for r in ("A", "B"):
        out[r] = _under(r, lambda: _outcome(lt))
    out["shipped"] = _outcome(lt)
    return out


def mirror_is_exact() -> bool:
    """The clean path, the shipped reading and (when git has it) the unrepaired
    one, mirror against the tool or against git's module, dict for dict."""
    old = _unrepaired_from_git()
    against = [("shipped", lt.schroeder_times)]
    if old is not None:
        against.append(("unrepaired", old.schroeder_times))
    for i in range(len(CONDS)):
        # the mirror has no band-dependent branch: one band proves it
        xb, tool = _bands(i)[lt.REF_BAND], lt.schroeder_times
        for defect in (None, "NO_FLOOR_SUB", "NO_TRUNC_GUARD"):
            mine = _call(_mirror("clean"), xb, lt.SR, defect)
            if mine != _call(tool, xb, lt.SR, defect):
                return False
        for reading, ref in against:
            mine = _call(_mirror(reading), xb, lt.SR, "FORWARD_INTEGRAL")
            if mine != _call(ref, xb, lt.SR, "FORWARD_INTEGRAL"):
                return False
    return True


def _matches_record(o: tuple, reading: str) -> bool:
    if o[0] == "raised":
        return o[1] == {"unrepaired": "Refused", "A": "ZeroDivisionError"}.get(reading)
    if reading == "B":
        return o[1] == (False, None)
    return reading == "shipped" and o[1][0] is False and o[1][1] is not None


def _cells(o) -> str:
    if o[0] == "raised":
        return f"raised {o[1]}: {o[2]}"
    cells = []
    for d in (-5.0, -10.0, -20.0, -30.0):
        key = f"{d:.0f}"
        v, why = o[1]["times_ms"][key], o[1]["refused"].get(key, "")
        cells.append("REF" if v is None and "too short" in why else
                     "never" if v is None else f"{v:.2f}")
    return " / ".join(cells)


def main(argv=None) -> int:
    exact = mirror_is_exact()
    got = outcomes()
    print(f"mirror exact against the instrument: {exact}; "
          f"unrepaired read from {got['unrepaired_from']}")
    print("\nLn crossing times (ms) at -5 / -10 / -20 / -30 dB; "
          "REF = refused by the end-margin guard")
    for i, (label, _, _, _) in enumerate(CONDS):
        print(f"  {label}")
        for r in READINGS:
            f = lt.schroeder_times if r == "shipped" else _mirror(r)
            o = _call(f, _bands(i)[lt.REF_BAND], lt.SR, "FORWARD_INTEGRAL")
            print(f"    {r:10s} {_cells(o)}")
    print("\nproperties(defect='FORWARD_INTEGRAL'), one line per reading, "
          "and the record it must still match:")
    for r in READINGS:
        o = got[r]
        head = (f"raised {o[1]}: {o[2]}" if o[0] == "raised"
                else f"answered; edt-known {o[1]}; red {list(o[2])}")
        print(f"  {r:10s} {head}   [record: {RECORD[r]}]")
    bad = [r for r in READINGS if not _matches_record(got[r], r)]
    print(f"\nself-checks: mirror exact = {exact}; readings matching the record = "
          f"{len(READINGS) - len(bad)}/{len(READINGS)}" + (f"; drifted: {bad}" if bad else ""))
    return 0 if exact and not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())

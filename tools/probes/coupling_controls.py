#!/usr/bin/env python3
"""Controls for the shared-bus DC coupling's executable specification (#551).

    python3 tools/probes/coupling_controls.py            # clean run + every mutant + the matrix
    python3 tools/probes/coupling_controls.py --list

`model/test_drums_fx.py::test_coupling_*` is the specification of contract
15.10. A suite nobody has watched fail is not a suite (docs/verification-rules.md
rule 1), so this runs it against the clean model, then against each NAMED
defect injected into the model, each declared against the ONE property that
must catch it. `any property went red` is not the verdict: a defect that
trips something unrelated would pass that test while the property written for
it stayed blind.

THREE OUTCOMES, not two, per docs/verification-rules.md:

    CAUGHT      the declared property failed ON AN ASSERTION under the defect
    BLIND       the defect was active and the declared property stayed green
                (exit 1: the suite cannot see this defect)
    NO VERDICT  the apparatus could not decide: the clean model is not green,
                a property raised something that is not an assertion (a
                traceback is not a verdict), the defect changed nothing at
                all (inactive: no property moved), or a tool is missing
                (exit 2)

The matrix is properties x defects: MOVED where a property went red under a
defect, `.` where it stayed green. A property no defect moves is printed as
BLIND-TO-ALL; it has never been seen to do anything.

Defects are monkeypatches of the model, so the production code carries no
injection hooks that could ship.
"""
from __future__ import annotations

import contextlib
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "model"))
sys.path.insert(0, os.path.join(ROOT, "audition"))

NO_VERDICT = 2
try:
    import numpy as np
    import pytest
    import drums_fx as dx
    import synth_top_model as stm
    import test_drums_fx as T
except Exception as e:              # missing tool: refuse, do not answer
    print(f"NO VERDICT: cannot import the apparatus: {e!r}")
    sys.exit(NO_VERDICT)

PROPS = sorted(n for n in dir(T) if n.startswith("test_coupling_"))
RED = (AssertionError, pytest.fail.Exception)


# ---- the defects -------------------------------------------------------------
@contextlib.contextmanager
def patch(obj, name, new):
    old = obj.__dict__[name] if name in obj.__dict__ else None
    setattr(obj, name, new)
    try:
        yield
    finally:
        if old is None:
            delattr(obj, name)
        else:
            setattr(obj, name, old)


def _wrap_write(pre):
    orig = dx.DrumsFx.write

    def write(self, addr, value):
        if pre(self, int(addr), int(value)):
            return
        return orig(self, addr, value)
    return orig, write


def m_bypass():
    """The old model: the enable register does nothing."""
    return _wrap_write(lambda d, a, v: a == dx.A_COUPLE)


def m_reserved_bits():
    """Any nonzero value enables (reserved bits not ignored)."""
    def pre(d, a, v):
        if a == dx.A_COUPLE:
            d.couple_en = int(v != 0)
            return True
    return _wrap_write(pre)


def m_alias_neighbour():
    """0x31 also enables."""
    def pre(d, a, v):
        if a == dx.A_COUPLE + 1:
            d.couple_en = v & 1
            return True
    return _wrap_write(pre)


def m_constructor_overrides():
    """The register no longer refuses to coexist with an experimental constructor mode."""
    def pre(d, a, v):
        if a == dx.A_COUPLE:
            d.couple_en = v & 1
            return True
    return _wrap_write(pre)


def _clear(d):
    for b in (d.cc_dmix, d.cc_body):
        b.acc = 0


def m_clear_on_hit():
    prev = {}

    def pre(d, a, v):
        if a == dx.A_STOPS:
            if v & ~d.stops & ((1 << dx.N_STOPS) - 1):
                _clear(d)
    return _wrap_write(pre)


def m_clear_on_retune():
    def pre(d, a, v):
        if dx.A_MODE <= a < dx.A_MODE + d.M * dx.MODE_STRIDE:
            _clear(d)
    return _wrap_write(pre)


def m_clear_on_accent():
    def pre(d, a, v):
        if dx.A_ACCENT <= a < dx.A_ACCENT + dx.N_STOPS:
            _clear(d)
    return _wrap_write(pre)


def _patch_reset(fn):
    return ("reset", fn)


def m_reset_keeps_charge():
    orig = dx.DrumsFx.reset

    def reset(self):
        keep = (self.cc_dmix.acc, self.cc_body.acc) if hasattr(self, "cc_dmix") else None
        orig(self)
        if keep:
            self.cc_dmix.acc, self.cc_body.acc = keep
    return orig, reset


def m_reset_keeps_enable():
    orig = dx.DrumsFx.reset

    def reset(self):
        keep = getattr(self, "couple_en", 0)
        orig(self)
        self.couple_en = keep
    return orig, reset


def m_reset_on():
    orig = dx.DrumsFx.reset

    def reset(self):
        orig(self)
        self.couple_en = 1
    return orig, reset


def _wrap_frame(post):
    orig = dx.DrumsFx.frame

    def frame(self):
        snap = (self.cc_dmix.acc, self.cc_body.acc)
        out = orig(self)
        return post(self, out, snap)
    return orig, frame


def m_bus_dmix_only():
    def post(d, out, snap):
        if d.couple_en:
            return (out[0], d.raw_body) + out[2:]
        return out
    return _wrap_frame(post)


def m_bus_body_only():
    def post(d, out, snap):
        if d.couple_en:
            return (d.raw_dmix, out[1]) + out[2:]
        return out
    return _wrap_frame(post)


def m_frozen_while_bypassed():
    def post(d, out, snap):
        if not d.couple_en:
            d.cc_dmix.acc, d.cc_body.acc = snap
        return out
    return _wrap_frame(post)


class _Pair:
    """A defect that needs two patches."""
    def __init__(self, *ps):
        self.ps = ps


def m_post_clamp():
    """The coupling moved to the far side of the output clamp."""
    orig_run = stm.SynthTopModel.run
    orig_frame = dx.DrumsFx.frame

    def frame(self):                       # buses leave the block raw ...
        out = orig_frame(self)
        return (self.raw_dmix, self.raw_body) + out[2:]

    def run(self, writes, n):
        on = [int(w[0]) for w in writes if int(w[2]) == stm.SEC_DRUM and int(w[3]) == dx.A_COUPLE and int(w[4]) & 1]
        out = orig_run(self, writes, n)
        if on:
            f0 = min(on)
            y = dx.dc_block(out["sample"], dx.COUPLE_K)
            s = out["sample"].copy()
            s[f0:] = np.clip(y[f0:], -32768, 32767)
            out = dict(out, sample=s)
            out["i2s"] = np.zeros(n, dtype=np.int64); out["i2s"][1:] = s[:-1]
        return out
    return _Pair((dx.DrumsFx, "frame", orig_frame, frame), (stm.SynthTopModel, "run", orig_run, run))


def m_shift_trunc_toward_zero():
    orig = dx.DcBlockFx.step

    def step(self, x):
        x = int(x)
        d = -((-self.acc) >> self.k) if self.acc < 0 else self.acc >> self.k
        y = x - d
        self.acc += y
        return y
    return orig, step


def m_acc_31_bits():
    orig = dx.DcBlockFx.step

    def step(self, x):
        y = orig(self, x)
        w = (self.acc + (1 << 30)) % (1 << 31) - (1 << 30)   # a 31-bit signed charge
        self.acc = w
        return y
    return orig, step


def m_out_22_bits():
    orig = dx.DcBlockFx.step

    def step(self, x):
        y = orig(self, x)
        return (y + (1 << 21)) % (1 << 22) - (1 << 21)       # output no wider than the input
    return orig, step


def m_k_not_ten():
    return ("const", dx, "COUPLE_K", 11)


# name -> (factory, the property that must catch it, one-line meaning)
MUTANTS = {
    "BYPASS_IGNORES_ENABLE":   (m_bypass, "enabled_path_follows_the_recurrence_on_both_buses", "enable register has no effect (the old model)"),
    "BUS_DMIX_ONLY":           (m_bus_dmix_only, "buses_separately_and_together", "body bus left uncoupled"),
    "BUS_BODY_ONLY":           (m_bus_body_only, "buses_separately_and_together", "mix bus left uncoupled"),
    "POST_CLAMP":              (m_post_clamp, "production_path_places_the_blocker_before_the_clamp", "coupling after the output clamp"),
    "SHIFT_TOWARD_ZERO":       (m_shift_trunc_toward_zero, "known_answers_constants_and_impulses", "signed shift truncates toward zero, not floor"),
    "ACC_31_BITS":             (m_acc_31_bits, "widths_are_the_proven_bounds_and_the_near_limit_hits_them", "charge one bit too narrow"),
    "OUT_22_BITS":             (m_out_22_bits, "widths_are_the_proven_bounds_and_the_near_limit_hits_them", "output no wider than the bus"),
    "K_NOT_TEN":               (m_k_not_ten, "constructor_modes_do_not_override_the_register", "fixed K changed from 10"),
    "CLEAR_ON_HIT":            (m_clear_on_hit, "charge_survives_hits_chokes_and_retunes", "charge cleared when a stop fires"),
    "CLEAR_ON_RETUNE":         (m_clear_on_retune, "charge_survives_hits_chokes_and_retunes", "charge cleared by a mode-coefficient write"),
    "CLEAR_ON_ACCENT":         (m_clear_on_accent, "charge_survives_hits_chokes_and_retunes", "charge cleared by an accent write"),
    "RESET_KEEPS_CHARGE":      (m_reset_keeps_charge, "reset_clears_charge_and_enable_voice_reset_does_not", "A_RESET leaves the charge"),
    "RESET_KEEPS_ENABLE":      (m_reset_keeps_enable, "reset_clears_charge_and_enable_voice_reset_does_not", "A_RESET leaves enable on"),
    "RESET_ON":                (m_reset_on, "reset_off_reproduces_the_previous_stream_exactly", "reset state is enabled"),
    "FROZEN_WHILE_BYPASSED":   (m_frozen_while_bypassed, "enable_transitions_track_while_bypassed", "charge frozen while bypassed"),
    "RESERVED_BITS_ENABLE":    (m_reserved_bits, "reserved_bits_and_neighbouring_addresses", "any nonzero value enables"),
    "ALIAS_0X31":              (m_alias_neighbour, "reserved_bits_and_neighbouring_addresses", "0x31 also enables"),
    "CONSTRUCTOR_OVERRIDES":   (m_constructor_overrides, "constructor_modes_do_not_override_the_register", "register silently coexists with an experimental mode"),
}


@contextlib.contextmanager
def inject(factory):
    r = factory()
    undo = []
    if isinstance(r, _Pair):
        specs = [(o, n, orig, new) for o, n, orig, new in r.ps]
    elif r[0] == "const":
        _, mod, name, val = r
        specs = [(mod, name, getattr(mod, name), val)]
    elif r[0] == "reset":
        specs = [(dx.DrumsFx, "reset", *r[1:])]
    else:
        orig, new = r
        name = {"write": "write", "reset": "reset", "frame": "frame", "step": "step"}[new.__name__]
        owner = dx.DcBlockFx if name == "step" else dx.DrumsFx
        specs = [(owner, name, orig, new)]
    for owner, name, orig, new in specs:
        setattr(owner, name, new)
    try:
        yield
    finally:
        for owner, name, orig, new in specs:
            setattr(owner, name, orig)


def run_props():
    """{prop: 'ok' | 'red' | 'error:<exc>'} -- a traceback is not a verdict."""
    T._PLAY_MEMO.clear()
    out = {}
    for p in PROPS:
        try:
            getattr(T, p)()
            out[p] = "ok"
        except RED:
            out[p] = "red"
        except BaseException as e:           # noqa: BLE001 -- recorded, never swallowed
            out[p] = f"error:{type(e).__name__}: {str(e)[:80]}"
    T._PLAY_MEMO.clear()
    return out


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--list" in argv:
        for k, (_, prop, why) in MUTANTS.items():
            print(f"{k:24s} -> test_coupling_{prop}   ({why})")
        return 0
    missing = [prop for _, prop, _ in MUTANTS.values() if "test_coupling_" + prop not in PROPS]
    if missing:
        print(f"NO VERDICT: declared properties absent from the suite: {missing}")
        return NO_VERDICT
    clean = run_props()
    bad = {p: v for p, v in clean.items() if v != "ok"}
    if bad:
        print("NO VERDICT: the CLEAN model is not green, so no control can mean anything:")
        for p, v in bad.items():
            print(f"  {p}: {v}")
        return NO_VERDICT
    print(f"clean: {len(PROPS)} properties green")
    rows, results = {}, {}
    for name, (factory, prop, why) in MUTANTS.items():
        with inject(factory):
            r = run_props()
        rows[name] = r
        moved = [p for p, v in r.items() if v == "red"]
        errs = {p: v for p, v in r.items() if v.startswith("error")}
        want = "test_coupling_" + prop
        if r[want] == "red":
            results[name] = "CAUGHT"
        elif r[want].startswith("error"):
            results[name] = "NO VERDICT"
        elif not moved and not errs:
            results[name] = "NO VERDICT"      # inactive: nothing moved at all
        else:
            results[name] = "BLIND"
        print(f"{name:24s} {results[name]:10s} declared={prop}  red={len(moved)}"
              + (f" errors={len(errs)}" if errs else ""), flush=True)
    short = {p: p.replace("test_coupling_", "")[:22] for p in PROPS}
    print("\nproperties x defects (X = went red, e = raised a non-assertion, . = stayed green)")
    names = list(MUTANTS)
    print(" " * 24 + " ".join(f"{i:2d}" for i in range(len(names))))
    for p in PROPS:
        cells = []
        for n in names:
            v = rows[n][p]
            cells.append(" X" if v == "red" else (" e" if v.startswith("error") else " ."))
        blind_all = all(rows[n][p] != "red" for n in names)
        print(f"{short[p]:24s}" + " ".join(cells) + ("   BLIND-TO-ALL" if blind_all else ""))
    for i, n in enumerate(names):
        print(f"  {i:2d} {n}")
    errors = {(n, p): v for n, r in rows.items() for p, v in r.items() if v.startswith("error")}
    for (n, p), v in sorted(errors.items()):
        print(f"  note: {n} / {p}: {v}")
    if any(v == "NO VERDICT" for v in results.values()):
        print("\nNO VERDICT (a declared property raised a non-assertion, or a defect was inactive)")
        return NO_VERDICT
    if any(v == "BLIND" for v in results.values()):
        print("\nFAIL: the suite is BLIND to " + ", ".join(n for n, v in results.items() if v == "BLIND"))
        return 1
    print(f"\nPASS: {len(MUTANTS)} defects, each caught by its declared property")
    return 0


if __name__ == "__main__":
    sys.exit(main())

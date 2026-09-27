#!/usr/bin/env python3
"""Task 2.1: count what the eight-stop kit ACTUALLY occupies in the modal bank,
by reading the register image the kit writes -- not by counting instrument names.

Run:  python3 fpga/scripts/mode_census.py
"""
import os, sys, collections
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, 'model'))
import drums_fx as D

kit = D.kit_808()
STRIDE, A_MODE, A_PATH, A_OSC = D.MODE_STRIDE, D.A_MODE, D.A_PATH, D.A_OSC

# --- which modes the kit configures, and with which numerator ---------------
NUMNAME = {D.RAW: 'RAW body', D.BP: 'BP filter', D.HP: 'HP filter'}
mode_regs = collections.defaultdict(dict)
for addr, val in kit:
    if A_MODE <= addr < A_MODE + D.N_MODES * STRIDE:
        m, off = divmod(addr - A_MODE, STRIDE)
        mode_regs[m][off] = val

# the names from drums_fx itself: a literal list here was revision 5's twelve
# and indexed past its end once MODES reached 16 (found under #107)
MNAMES = {getattr(D, n): n for n in dir(D) if n.startswith('M_') and isinstance(getattr(D, n), int)}
MNAMES = [MNAMES.get(m, 'M_SPARE') for m in range(D.N_MODES)]
print("=== modal bank occupancy of the shipped eight-stop kit (MODES=%d provisioned) ===" % D.N_MODES)
active = []
for m in range(D.N_MODES):
    r = mode_regs.get(m, {})
    if not r:
        print(f"  mode {m:2d} {MNAMES[m]:10s}  UNCONFIGURED")
        continue
    poles = (r.get(0, 0), r.get(1, 0))
    amp   = r.get(2, 0)
    num   = r.get(3, 0)
    kind  = NUMNAME.get(num, f'num={num}')
    live  = poles != (0, 0)
    if live: active.append((m, kind))
    print(f"  mode {m:2d} {MNAMES[m]:10s}  {kind:10s} amp={amp:6d} {'ACTIVE' if live else 'idle'}")
filt = [m for m, k in active if 'filter' in k]
body = [m for m, k in active if 'body' in k]
print(f"\n  ACTIVE MODES: {len(active)} of {D.N_MODES}  ({len(filt)} filters, {len(body)} bodies)")
print(f"  spare: {D.N_MODES - len(active)}")

# --- how many named stops, and how modes map onto them ---------------------
paths = [v for a, v in kit if A_PATH <= a < A_PATH + D.N_PATH]
print(f"\n  named stops: {len(D.STOP_NAMES)} {D.STOP_NAMES}")
print(f"  envelopes configured: {len(set((a - D.A_ENV)//D.ENV_STRIDE for a,_ in kit if D.A_ENV <= a < D.A_ENV + D.N_ENV*D.ENV_STRIDE))} of {D.N_ENV}")
print(f"  paths configured: {len(paths)} of {D.N_PATH}")
print(f"  oscillators: {len([a for a,_ in kit if A_OSC <= a < A_OSC + D.N_OSC])} of {D.N_OSC}")

# --- configuration storage, in bits ----------------------------------------
bits_per_mode = 2*(D.COEF_FRAC + 2) + 16 + 2 if hasattr(D, 'COEF_FRAC') else 2*26 + 16 + 2
print(f"\n=== configuration storage the bank needs (register bits) ===")
print(f"  per mode: 2 x (CF+2)=26 coefficient + 16 amp + 2 num = {bits_per_mode} bits")
for m in (11, 12, 14, 15):
    print(f"  MODES={m:2d}: {m*bits_per_mode:5d} bits of mode configuration")
print(f"  (envelopes {D.N_ENV} x (27+24+16) = {D.N_ENV*67} bits, paths {D.N_PATH} x 22 = {D.N_PATH*22} bits,")
print(f"   oscillators {D.N_OSC} x 24 = {D.N_OSC*24} bits, stops/accents {D.N_STOPS + 8*16} bits)")
tot = D.N_MODES*bits_per_mode + D.N_ENV*67 + D.N_PATH*22 + D.N_OSC*24 + D.N_STOPS + 8*16
print(f"  TOTAL drum-section configuration at MODES=12: {tot} bits")
print("\n  NONE OF THIS EXISTS IN RTL: drum_kit.v takes a1_bus/a2_bus/amp_bus/num_bus,")
print("  env_*_bus, path_bus, osc_inc_bus and accent_bus as INPUT PORTS. tb_drums.v")
print("  drives them from a file. No module in rtl-sketch/ holds them.")

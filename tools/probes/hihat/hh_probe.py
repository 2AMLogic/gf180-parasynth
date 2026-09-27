"""Is the 808 hat/cymbal high-band filter network under-built, or mistuned?

Float model only. No RTL, no iverilog. Every number printed says where it came
from: DERIVED (a formula on schematic component values), MEASURED (a render of
the model), RECORDED (a figure already in the repo, reproduced as a
precondition), or FIT (a search -- labelled as such, never presented as a
circuit value).
"""
from __future__ import annotations
_ROOT = __import__("pathlib").Path(__file__).resolve().parents[3]
import hashlib, itertools, json, math, os, subprocess, sys, time

WT = str(_ROOT)
SCRATCH = ("/private/tmp/claude-501/-Users-joseph-dev-2amlogic/"
           "b50207d1-0443-45b1-85ae-6193fbb61d1b/scratchpad")
sys.path.insert(0, os.path.join(WT, "model"))
sys.path.insert(0, os.path.join(WT, "audition"))

import numpy as np
from scipy.signal import lfilter

import audio_measure as am
import drums_fx as dx
import measure_harness as mh
from dsp import SR
from modal_fixed import RAW, BP, HP, pole_regs

OUT: dict = {}
def say(*a): print(*a, flush=True)

# ---------------------------------------------------------------- provenance
def provenance():
    rev = subprocess.run(["git", "-C", WT, "rev-parse", "HEAD"],
                         capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "-C", WT, "status", "--porcelain"],
                           capture_output=True, text=True).stdout.strip()
    files = {f: hashlib.sha256(open(os.path.join(WT, f), "rb").read()).hexdigest()[:16]
             for f in ("model/drums_fx.py", "model/modal_fixed.py",
                       "model/audio_measure.py", "model/test_808_acceptance.py",
                       "docs/tr808-reference.md", "docs/drum-verification.md")}
    import scipy, numpy
    return dict(source_commit=rev, worktree_dirty=bool(dirty), sha256_16=files,
                python=sys.version.split()[0], numpy=numpy.__version__,
                scipy=scipy.__version__, sr=SR,
                cmd="python scratchpad/hh_probe.py")

# ------------------------------------------------ DERIVED: Sallen-Key filters
def sallen_key_hp(c_f, r1, r2):
    """Equal-C unity-gain Sallen-Key high-pass, from schematic values:
        f0 = 1/(2 pi C sqrt(R1 R2)),  Q = 1/2 sqrt(R2/R1).
    The reading docs/tr808-reference.md attributes to Werner for Hh1 and
    reuses for the two hat filters. No audio involved."""
    return 1.0 / (2.0 * math.pi * c_f * math.sqrt(r1 * r2)), 0.5 * math.sqrt(r2 / r1)

SK_ROWS = [  # name,        C,      R1,    R2,     f0 stated, Q stated, source
    ("OH Hh (Q26)",  1.5e-9, 2.7e3, 68e3,  7800.0, 2.50, "reference 11"),
    ("CH Hh (Q31)",  1.0e-9, 2.7e3, 68e3, 11700.0, 2.50, "reference 11"),
    ("CY Hh1 (Q25)", 1.5e-9, 22e3,  82e3,  2500.0, 0.97, "reference 10"),
]

def derive_filters():
    say("\n== DERIVED: the Sallen-Key high-passes, from schematic component values ==")
    say(f"{'filter':<14}{'C nF':>7}{'R1':>8}{'R2':>8}{'f0 der':>9}{'f0 ref':>8}"
        f"{'Q der':>8}{'Q ref':>7}")
    out = {}
    for name, c, r1, r2, f_ref, q_ref, _ in SK_ROWS:
        f0, q = sallen_key_hp(c, r1, r2); out[name] = (f0, q)
        say(f"{name:<14}{c*1e9:>7.3f}{r1:>8.0f}{r2:>8.0f}{f0:>9.0f}{f_ref:>8.0f}"
            f"{q:>8.3f}{q_ref:>7.2f}")
        if name != "CY Hh1 (Q25)" and (abs(f0/f_ref-1) > 0.02 or abs(q/q_ref-1) > 0.03):
            raise SystemExit(f"REFUSED: the formula does not reproduce {name}")
    say("  precondition OK: reproduces BOTH already-stated filters to <1 %, so its")
    say("  answer for Hh1 -- 2498 Hz, Q 0.965 -- is DERIVED from the circuit.")
    return out

# ----------------------------------------------------------- render machinery
PRE = 0.010
def render(kit, seconds, stop=dx.CY, accent=1.0, modes=dx.N_MODES, nums=dx.N_NUMS):
    at = int(PRE * SR)
    n = at + int(seconds * SR)
    d = dx.DrumsFx(modes=modes, nums=nums)
    dm, bd = d.play(dx.hit_writes([(at, stop, accent)], kit), n)
    return d, dm.astype(np.float64) + bd.astype(np.float64)

CY_BANDS = ((20., 2000.), (2000., 5000.), (5000., 9000.), (9000., 13000.), (13000., 19000.))
HAT_BANDS = ((3000., 6000.), (6000., 9000.), (9000., 13000.), (13000., 19000.))
HW_CY = (0.011, 0.103, 0.532, 0.233, 0.060)         # RECORDED cy8/CY5025.WAV
REC_CY = (0.013, 0.063, 0.584, 0.153, 0.067)        # RECORDED drums_fx.CY_FIT['shares']
HW_CH = (0.038, 0.301, 0.361, 0.300)                # RECORDED drum-verification 4.4
HW_OH = (0.087, 0.669, 0.202, 0.042)
REC_CH = (0.011, 0.220, 0.298, 0.470)
REC_OH = (0.033, 0.838, 0.074, 0.055)

_SOS: dict = {}
def fast_band_energy(x, edges):
    """am.band_energy with the Butterworth designs cached. Validated against
    am.band_energy itself below before any sweep uses it."""
    from scipy.signal import butter, sosfiltfilt
    x = np.asarray(x, dtype=np.float64)
    total = float((x**2).sum())
    if total <= 0:
        return np.zeros(len(edges))
    out = []
    for lo, hi in edges:
        k = (lo, hi)
        if k not in _SOS:
            _SOS[k] = butter(4, [lo/(SR/2.), min(hi, SR/2.-1.)/(SR/2.)],
                             btype="band", output="sos")
        out.append(float((sosfiltfilt(_SOS[k], x)**2).sum()) / total)
    return np.array(out)

def fmt(sh): return " / ".join(f"{v*100:5.1f}" for v in sh)
def dfmt(a, b): return " / ".join(f"{(x-y)*100:+5.1f}" for x, y in zip(a, b))
def cost(got, ref): return float(sum(abs(g-r) for g, r in zip(got, ref)) * 100)

# ---------------------------------------------------------------- the biquads
def biquad(f0, q, num):
    a1, a2 = pole_regs(f0, q)
    a1, a2 = a1 / (1 << 24), a2 / (1 << 24)
    b = {RAW: [1., 0., 0.], BP: [1., 0., -1.], HP: [1., -2., 1.]}[num]
    return np.array(b), np.array([1., -a1, -a2])

def response(f0, q, num, f):
    b, a = biquad(f0, q, num)
    z = np.exp(-2j*np.pi*np.asarray(f, dtype=float)/SR)
    return np.abs((b[0]+b[1]*z+b[2]*z**2) / (a[0]+a[1]*z+a[2]*z**2))

def unity_amp(f0, q, num, at=20000.0):
    """The `amp` that makes the mode's passband gain 1.0, as a unity-gain
    Sallen-Key's is. DERIVED from the filter, not tuned against a band."""
    return 1.0 / float(response(f0, q, num, np.array([at]))[0])

# ------------------------------------------------------------- baseline / red
TIGHT = 0.040        # absolute per band: the machine's value +- 4 points
def gate(sh, label, ref=HW_CY, bands=CY_BANDS, tol=TIGHT):
    bad = [f"{lo/1000:g}-{hi/1000:g}k {g*100:.1f} vs {r*100:.1f}"
           for (lo, hi), g, r in zip(bands, sh, ref) if abs(g-r) > tol]
    say(f"  [{'GREEN' if not bad else 'RED  '}] {label}: "
        + ("; ".join(bad) if bad else f"every band within {tol*100:.0f} points"))
    return not bad

# ---------------------------------------------- offline post-filter emulator
M_CAP_S, M_CAP_D, M_CAP_L = 16, 17, 18      # capture modes for the three CY VCAs

def capture_cy():
    """Render CY with each swing-VCA output routed into its own dead capture
    mode (a1 = a2 = amp = 0), so `trace['exc']` holds the three band signals
    EXACTLY as the fixed-point block produces them, before any post-filter."""
    img = dict(dx.kit_with_sounds("CY"))
    for m in (M_CAP_S, M_CAP_D, M_CAP_L):
        for a, v in dx.mode_writes(m, 1000.0, 1.0, 0.0, RAW):
            img[a] = v
        img[dx.A_MODE + m*dx.MODE_STRIDE + 0] = 0     # a1 = 0
        img[dx.A_MODE + m*dx.MODE_STRIDE + 1] = 0     # a2 = 0
    img[dx.A_PATH+dx.P_CYS] = dx.path_word(dx.SRC_TAP+dx.M_HATBP, dx.E_CYS,
                                           nl=dx.NL_SWING, att=dx.CY_ATT, dest=M_CAP_S)
    img[dx.A_PATH+dx.P_CYD] = dx.path_word(dx.SRC_TAP+dx.M_HATBP, dx.E_CYD,
                                           nl=dx.NL_SWING, att=dx.CY_ATT, dest=M_CAP_D)
    img[dx.A_PATH+dx.P_CYL] = dx.path_word(dx.SRC_TAP+dx.M_CYBP, dx.E_CYL,
                                           nl=dx.NL_SWING, att=dx.CY_ATT, dest=M_CAP_L)
    d, _ = render(sorted(img.items()), 2.0, modes=19, nums=11)
    e = d.trace["exc"].astype(np.float64)
    return e[:, M_CAP_S], e[:, M_CAP_D], e[:, M_CAP_L]

def chain(sig, *stages):
    """stages: (f0, q, num, amp) applied in cascade."""
    y = sig
    for f0, q, num, amp in stages:
        b, a = biquad(f0, q, num)
        y = lfilter(b, a, y) * amp
    return y

def main():
    prov = provenance()
    say("== provenance =="); say(json.dumps(prov, indent=2))
    filt = derive_filters()
    f_hh1, q_hh1 = filt["CY Hh1 (Q25)"]

    # ---------- baseline ----------------------------------------------------
    say("\n== MEASURED: baseline CY, kit_808() unmodified, 2.00 s ==")
    t = time.time(); d0, x0 = render(dx.kit_with_sounds("CY"), 2.0)
    base = am.band_energy(x0, CY_BANDS, SR)
    say(f"  bands <2k / 2-5k / 5-9k / 9-13k / >13k      ({time.time()-t:.1f} s)")
    say(f"    machine   {fmt(HW_CY)}")
    say(f"    ours      {fmt(base)}")
    say(f"    RECORDED  {fmt(REC_CY)}   (drums_fx.CY_FIT['shares'])")
    err = mh.assert_precondition(base, REC_CY, 0.003,
                                 what="cannot reproduce CY_FIT['shares']")
    say(f"  precondition OK: reproduces the recorded 2.0 s row to {err*100:.2f} points.")
    say("\n== START RED: the machine's split at +-4 points per band ==")
    gate(base, "baseline kit_808()")

    # ---------- STEP 1: Hh1 restored, DERIVED -------------------------------
    say("\n== STEP 1: reference 10's Hh1 restored on the LOW band ==")
    say(f"  Hh1 = {f_hh1:.0f} Hz Q {q_hh1:.3f}, HP numerator; it sits AFTER the")
    say("  low band's swing VCA (Q18), i.e. on the 3.45 kHz band-pass, which is")
    say("  where reference 10 puts it. Needs a 17th mode.")
    g = response(f_hh1, q_hh1, HP, np.array([1000., 2000., 3453., 7100., 10500., 20000.]))
    say("  |H| at 1k/2k/3.45k/7.1k/10.5k/20k: " + " ".join(f"{v:.3f}" for v in g))
    amp = unity_amp(f_hh1, q_hh1, HP)
    img = dict(dx.kit_with_sounds("CY"))
    for a, v in dx.mode_writes(16, f_hh1, q_hh1, amp, HP):
        img[a] = v
    img[dx.A_PATH+dx.P_CYL] = dx.path_word(dx.SRC_TAP+dx.M_CYBP, dx.E_CYL,
                                           nl=dx.NL_SWING, att=dx.CY_ATT, dest=16)
    _, x1 = render(sorted(img.items()), 2.0, modes=17, nums=12)
    sh1 = am.band_energy(x1, CY_BANDS, SR)
    say(f"    machine   {fmt(HW_CY)}")
    say(f"    baseline  {fmt(base)}    cost {cost(base, HW_CY):5.1f}")
    say(f"    + Hh1     {fmt(sh1)}    cost {cost(sh1, HW_CY):5.1f}")
    say(f"    delta     {dfmt(sh1, base)}")
    gate(sh1, "with Hh1 restored (17 modes)")
    OUT["step1_hh1"] = dict(f0=f_hh1, q=q_hh1, amp=amp, shares=list(sh1),
                            baseline=list(base), cost=cost(sh1, HW_CY),
                            cost_baseline=cost(base, HW_CY))

    # ---------- injected control -------------------------------------------
    say("\n== INJECTED CONTROL ==")
    img = dict(dx.kit_with_sounds("CY"))
    for a, v in dx.mode_writes(dx.M_CYHI, dx.CY_HI_HZ, dx.CY_HI_Q, 0.0, BP):
        img[a] = v
    _, xd = render(sorted(img.items()), 2.0)
    shd = am.band_energy(xd, CY_BANDS, SR)
    say(f"  defect: M_CYHI amp = 0 (the high band's post-filter muted)")
    say(f"    {fmt(shd)}")
    tight_green = gate(shd, "defect vs tightened gate")
    tol_ship = (0.020, 0.060, 0.120, 0.120, 0.035)
    bad_ship = [f"{lo/1000:g}-{hi/1000:g}k" for (lo, hi), gg, r, tt
                in zip(CY_BANDS, shd, HW_CY, tol_ship) if abs(gg-r) > tt]
    say(f"    shipped gate (test_cymbal_band_split...): "
        + ("RED on " + ",".join(bad_ship) if bad_ship else
           "GREEN -- the SHIPPED gate cannot see this defect"))
    OUT["control"] = dict(shares=list(shd), tight_green=tight_green,
                          shipped_red=bool(bad_ship))

    # ---------- capture + validate the offline emulator ---------------------
    say("\n== apparatus: offline post-filter emulator ==")
    vs, vd, vl = capture_cy()
    recon = (chain(vs, (11700., 2.5, HP, 0.69))
             + chain(vd, (dx.CY_HI_HZ, dx.CY_HI_Q, BP, 1.0))
             + vl)
    sh_re = am.band_energy(recon, CY_BANDS, SR)
    say(f"    fixed-point render {fmt(base)}")
    say(f"    offline emulator   {fmt(sh_re)}")
    e2 = mh.assert_precondition(
        sh_re, base, 0.010,
        what="emulator off; the sweep below would not mean anything")
    say(f"  precondition OK: within {e2*100:.2f} points of the fixed-point render,")
    say("  so a sweep of post-filters run offline is trustworthy to ~0.5 points.")
    fb = fast_band_energy(recon, CY_BANDS)
    e3 = mh.assert_precondition(fb, sh_re, 1e-9, what="cached band_energy differs")
    say(f"  precondition OK: the cached band-energy estimator is bit-identical "
        f"to audio_measure.band_energy ({e3:.1e}).")

    # ---------- what the post-filter is being asked to do -------------------
    say("\n== MEASURED: the signal the high band's post-filter receives ==")
    for nm, v in (("short band VCA out (E_CYS)", vs), ("decay band VCA out (E_CYD)", vd),
                  ("low band VCA out (E_CYL)", vl)):
        sh = am.band_energy(v, CY_BANDS, SR)
        say(f"    {nm:<28} {fmt(sh)}   energy {float((v**2).sum()):.3e}")
    say("    the machine's finished cymbal    " + fmt(HW_CY))

    # ---------- STEP 1b: can ONE 2-pole post-filter reach the machine? ------
    say("\n== FIT (labelled): sweep the DECAY band's single post-filter ==")
    say("  Everything else held at kit_808(). This asks whether the shoulder is")
    say("  reachable by RETUNING one filter -- if it is, the network is not short")
    say("  of filters. f0 6-16 kHz, Q 0.7-8, numerator BP or HP, gain unity.")
    f0s = np.arange(6000., 16001., 250.)
    qs = [0.7, 1.0, 1.4, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0, 8.0]
    best1 = None
    short_fixed = chain(vs, (11700., 2.5, HP, 0.69))
    for num in (BP, HP):
        for f0 in f0s:
            for q in qs:
                y = short_fixed + chain(vd, (float(f0), q, num, unity_amp(f0, q, num))) + vl
                sh = fast_band_energy(y, CY_BANDS)
                c = cost(sh, HW_CY)
                if best1 is None or c < best1[0]:
                    best1 = (c, float(f0), q, num, list(sh))
    c, f0, q, num, sh = best1
    say(f"    best single 2-pole: {f0:.0f} Hz Q {q} {'BP' if num==BP else 'HP'}")
    say(f"      machine {fmt(HW_CY)}")
    say(f"      best    {fmt(sh)}   cost {c:.1f}  (baseline {cost(base, HW_CY):.1f})")
    gate(sh, "best single retuned post-filter")
    OUT["sweep_one_filter"] = dict(cost=c, f0=f0, q=q, num=int(num), shares=sh)

    # ---------- STEP 1c: does a SECOND high-band filter beat it? -----------
    say("\n== FIT (labelled): a SECOND post-filter cascaded on the decay band ==")
    say("  i.e. actually adding a filter the model does not have, and asking")
    say("  whether more filtering -- not retuning -- is what the shoulder needs.")
    best2 = None
    for num1 in (BP, HP):
        for f1 in np.arange(7000., 15001., 1000.):
            for q1 in (1.0, 2.5, 5.0):
                s1 = chain(vd, (float(f1), q1, num1, unity_amp(f1, q1, num1)))
                for f2 in np.arange(7000., 15001., 1000.):
                    for q2 in (1.0, 2.5, 5.0):
                        y = short_fixed + chain(s1, (float(f2), q2, BP,
                                                     unity_amp(f2, q2, BP))) + vl
                        sh = fast_band_energy(y, CY_BANDS)
                        c = cost(sh, HW_CY)
                        if best2 is None or c < best2[0]:
                            best2 = (c, float(f1), q1, int(num1), float(f2), q2, list(sh))
    c2, f1, q1, n1, f2, q2, sh2 = best2
    say(f"    best 2-filter cascade: {f1:.0f} Hz Q {q1} {'BP' if n1==BP else 'HP'}"
        f"  ->  {f2:.0f} Hz Q {q2} BP")
    say(f"      machine {fmt(HW_CY)}")
    say(f"      best    {fmt(sh2)}   cost {c2:.1f}")
    gate(sh2, "best two-filter cascade")
    say(f"\n  one filter {best1[0]:.1f}  vs  two filters {c2:.1f}  vs  "
        f"baseline {cost(base, HW_CY):.1f}  (lower is closer to the machine)")
    OUT["sweep_two_filters"] = dict(cost=c2, f0a=f1, qa=q1, numa=n1, f0b=f2, qb=q2,
                                    shares=sh2)

    json.dump(OUT, open(os.path.join(SCRATCH, "hh_cy.json"), "w"), indent=2, default=float)
    json.dump(prov, open(os.path.join(SCRATCH, "hh_prov.json"), "w"), indent=2)
    say("\n(written: hh_cy.json, hh_prov.json)")

if __name__ == "__main__":
    main()

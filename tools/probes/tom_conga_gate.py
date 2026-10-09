"""#558: the six tom/conga sounds against the #379 perceptual gate, by mechanism.

The gate (tools/perceptual_gate.py) ranks MC, LC, LT, MT, HC, HT as failing on
`pitch_shape` (every conga and LT), `impulse` (all six) and, for LT,
`modulation`. This probe does not change the gate, the kit or the RTL. It asks
what each failing feature is reading, and tests one mechanism on conditions
frozen in docs/scorecard/tom-conga-558/prereg.json BEFORE the run.

  diagnose     per sound at the gate's own target (TUNING 5.0): the full
               verdict, the impulse statistic (strike, body) of the target,
               its neighbours and ours, and both gate pitch trajectories.
  knownanswer  float resonators whose answer is known without our model: an
               all-pole mode (sin-phase onset) against the same poles with a
               DC zero (cos-phase onset, the circuit's band-pass), and a
               resonator with and without a stationary noise floor. What does
               the gate's pitch track and impulse statistic read for each?
  run          every pre-registered condition (6 sounds x 5 TUNING takes) for
               the shipped engine and the pre-registered candidates, plus the
               floor-transplant attribution; writes the record that `judge`
               reads.
  judge        applies prereg.json's frozen rules to a `run` record.

Preconditions are asserted at the point of use and the probe REFUSES rather
than answers: every take must match the frozen sha256 table in prereg.json,
be finite and non-silent at 44.1/48 kHz; our renders must be finite and
non-silent; the retune at ratio 1.0 must reproduce `render_drum_solo`
bit-exactly (else the "baseline" is not the engine that ships).
"""
from __future__ import annotations

import argparse
import cmath
import hashlib
import json
import math
import pathlib
import statistics
import subprocess
import sys

import numpy as np
from scipy.signal import butter, lfilter, sosfiltfilt

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "tools"), str(ROOT / "model")]
import perceptual_gate as pg  # noqa: E402

FAMILY = ("LT", "MT", "HT", "LC", "MC", "HC")
PREREG = ROOT / "docs" / "scorecard" / "tom-conga-558" / "prereg.json"
PITCH_DB = pg.PITCH_DB


class Refused(RuntimeError):
    pass


#: Rule 5 controls: defects a judge or fixture of this shape plausibly has,
#: reinstated on demand; each must turn a named known answer red
#: (test_tom_conga_gate.py::test_injection_*). "allpole-stub" is the start-red
#: stub of the H1 fixture: it ignores the numerator it is given.
#: "twin-no-host-writes" is a float twin that drops the host's frame-by-frame
#: writes (the diode-drop staircase), so it no longer tracks the engine.
INJECTIONS = ("no-preserve", "no-peak", "confirm-reads-all", "allpole-stub", "twin-no-host-writes")
INJECT: set = set()


# ---------------------------------------------------------------------------
# corpus
# ---------------------------------------------------------------------------
def take_rel(sound: str, code: str) -> str:
    return f"{pg.ONE_KNOB[sound]}{code}.WAV"


def sha16(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


def load_prereg(path: pathlib.Path = PREREG) -> dict:
    if not path.exists():
        raise Refused(f"pre-registration missing: {path}")
    return json.loads(path.read_text())


def load_checked(refs: pathlib.Path, rel: str, hashes: dict | None = None):
    p = refs / rel
    if not p.exists():
        raise Refused(f"corpus take missing: {p}")
    if hashes is not None:
        want = hashes.get(rel)
        if want is None:
            raise Refused(f"{rel} is not in the frozen corpus table")
        got = sha16(p)
        if got != want:
            raise Refused(f"{rel}: sha256 {got} is not the frozen {want}: a different corpus")
    x, sr = pg.load_wav(p)
    if not np.all(np.isfinite(x)):
        raise Refused(f"{rel}: non-finite samples")
    if float(np.max(np.abs(x))) < 1e-4:
        raise Refused(f"{rel}: silent")
    if sr not in (44100, 48000):
        raise Refused(f"{rel}: unexpected sample rate {sr}")
    return x, sr


# ---------------------------------------------------------------------------
# the bar for ANY take (pg.bar_for is pinned to the gate's one target)
# ---------------------------------------------------------------------------
def bar_for_take(sound: str, rel: str, refs: pathlib.Path, T, hashes=None) -> dict:
    """pg.bar_for's exact rule, for the take `rel` instead of target_rel(sound):
    the nearest adjacent knob setting by `spec`, measured through the candidate
    path, plus the apparatus floor, with the three perceptual floors.
    `test_bar_for_take_is_the_gates_bar` pins it to pg.bar_for at TUNING 5.0."""
    nbs = {}
    for nb in pg.neighbours(sound, rel):
        if (refs / nb).exists():
            nbs[nb] = T.distance(*pg.candidate_path(*load_checked(refs, nb, hashes)), nb)
    x, sr = load_checked(refs, rel, hashes)
    floor = T.distance(*pg.candidate_path(x, sr), "apparatus floor")
    if not nbs:
        raise Refused(f"{rel}: no neighbour take")
    best = min(nbs, key=lambda k: nbs[k]["spec"])
    base = nbs[best]
    bar = {f: (None if base[f] is None else base[f] + (floor[f] or 0.0)) for f in base}
    if bar.get("attack") is not None:
        bar["attack"] = max(bar["attack"], pg.ATTACK_JND_MS)
    if bar.get("pitch") is not None:
        bar["pitch"] = max(bar["pitch"], pg.PITCH_JND_CENTS)
    if bar.get("impulse") is not None:
        bar["impulse"] = max(bar["impulse"], pg.IMPULSE_FLOOR_DB)
    return {"from": best, "bar": bar}


# ---------------------------------------------------------------------------
# our side: the shipped engine, retuned, with an optional numerator candidate
# ---------------------------------------------------------------------------
def _circuit(sound):
    import drums_fx as dx
    return {"LT": dx.M_LT, "LC": dx.M_LT, "MT": dx.M_MT, "MC": dx.M_MT,
            "HT": dx.M_HT, "HC": dx.M_HT}[sound]


def num_gain(kind: str, f0: float, sr: int) -> float:
    z = cmath.exp(-1j * 2 * math.pi * f0 / sr)
    return abs(1 - z * z) if kind == "BP" else abs((1 - z) ** 2)


def render(sound: str, ratio: float = 1.0, kind: str | None = None) -> tuple:
    """render_drum_solo's exact render with the circuit's TUNING moved by
    `ratio` (f0 x ratio, Q x ratio so tau = Q / (pi f0) is held -- the pot
    moves the bridged-T's resistor, which reference 4 treats as an f0 change)
    and an optional numerator on the circuit, amp compensated at f0 so the
    ring's level is unchanged (tools/probe_tom_numerator.py's convention).

    The host's diode-drop writes read f0 back out of the image, so the shipped
    law's TUNING term follows the retune exactly as a host's would.

    Run on a bank with nums = 16. With every numerator RAW that is
    bit-identical to the shipped nums = 11 (`test_retune_unity_is_shipped`);
    with a numerator it equals the remap that puts the toms on numerator-capable
    modes 8-10 (tools/probe_tom_numerator.py)."""
    import drums_fx as dx
    import run_case as rc
    stop = dx.SOUND_STOP[sound]
    n = int(rc.SOLO_SECONDS.get(sound, 2.2) * dx.SR)
    kit = dict(dx.kit_with_sounds(sound))
    m = _circuit(sound)
    base = dx.A_MODE + m * dx.MODE_STRIDE
    f0, q, _ = dx.TOM_PRESET[sound]
    amp = dx.AMP_TOM[sound]
    if ratio != 1.0:
        for a, v in dx.mode_writes(m, f0 * ratio, q * ratio, amp, dx.RAW):
            kit[a] = v
    if kind is not None:
        g = 1.0
        if kind.endswith("+X4"):
            ex = {dx.M_LT: dx.E_LTX, dx.M_MT: dx.E_MTX, dx.M_HT: dx.E_HTX}[m]
            pa = dx.A_ENV + ex * dx.ENV_STRIDE + 1
            new = min(dx.FULL24, kit[pa] * 4)
            g = new / kit[pa]
            kit[pa] = new
        a_ = amp / num_gain(kind.split("+")[0], f0 * ratio, dx.SR) / g
        if a_ >= 1.0:
            raise Refused(f"{sound} {kind}: compensated amp {a_:.3f} does not fit Q0.16")
        kit[base + 2] = dx.amp_reg(a_)
        kit[base + 3] = {"BP": dx.BP, "HP": dx.HP}[kind.split("+")[0]]
    d = dx.DrumsFx(nums=dx.N_MODES)
    dm, bd = d.play(dx.hit_writes([(rc.DRUM_SOLO_HIT_FRAME, stop, 1.0)], sorted(kit.items())), n)
    gg = dx.accent_reg(0.45)
    out = np.asarray(dx.output_fx(np.zeros(n), 0, dm, gg, bd, gg), dtype=np.float64) / 32768.0
    if not np.all(np.isfinite(out)) or float(np.max(np.abs(out))) < 1e-5:
        raise Refused(f"our {sound} x{ratio} {kind}: silent or non-finite render")
    return out, dx.SR


def render_twin(sound: str, ratio: float = 1.0, numer=(1.0,)) -> tuple:
    """POST-HOC DIAGNOSTIC, not a candidate: a FLOAT twin of the tom voice --
    the same host writes (retune, diode-drop staircase, frame by frame), the
    same 0.1 ms exciter, the same poles, but a float recursion with a chosen
    numerator. It separates the MECHANISM (does a DC zero move the gate the
    way the 808 does?) from the fixed-point bank's deadband (#350), which
    #351 found breaks the numerator on the shipped engine. Its RAW form is
    checked against the shipped render on the gate's pitch_shape
    (`test_twin_raw_tracks_the_engine`, which needs the corpus); the
    `twin-no-host-writes` injection, a twin that drops the frame-by-frame host
    writes, must turn that test red.

    It does NOT separate the deadband from the fixed-point BP+X4 candidate's
    other difference, the x4 exciter scaling: the twin has neither."""
    import drums_fx as dx
    import run_case as rc
    n = int(rc.SOLO_SECONDS.get(sound, 2.2) * dx.SR)
    kit = dict(dx.kit_with_sounds(sound))
    m = _circuit(sound)
    f0, q, _ = dx.TOM_PRESET[sound]
    if ratio != 1.0:
        for a, v in dx.mode_writes(m, f0 * ratio, q * ratio, dx.AMP_TOM[sound], dx.RAW):
            kit[a] = v
    hit = rc.DRUM_SOLO_HIT_FRAME
    base = dx.A_MODE + m * dx.MODE_STRIDE
    a1 = np.zeros(n)
    a2 = np.zeros(n)
    cur = {base: kit[base], base + 1: kit[base + 1]}
    ev = sorted((f, a, v) for f, a, v in dx.hit_writes([(hit, dx.SOUND_STOP[sound], 1.0)],
                                                       sorted(kit.items())) if a in cur)
    if "twin-no-host-writes" in INJECT:
        ev = []
    k = 0
    for i in range(n):
        while k < len(ev) and ev[k][0] <= i:
            cur[ev[k][1]] = ev[k][2]
            k += 1
        a1[i] = dx.s26(cur[base]) / (1 << 24)
        a2[i] = dx.s26(cur[base + 1]) / (1 << 24)
    x = np.zeros(n)
    t = np.arange(n - hit)
    x[hit:] = np.exp(-t / (0.1e-3 * dx.SR))
    xn = lfilter(list(numer), [1.0], x)
    y = np.zeros(n)
    y1 = y2 = 0.0
    for i in range(n):
        v = xn[i] + a1[i] * y1 + a2[i] * y2
        y[i] = v
        y2, y1 = y1, v
    return y / np.abs(y).max() * 0.5, dx.SR


# ---------------------------------------------------------------------------
# measurement helpers
# ---------------------------------------------------------------------------
def trajectory(a: dict) -> dict:
    """The gate's own pitch track, reduced: cents about the median of the live
    frames (within PITCH_DB of the line's peak)."""
    p = a["pitch"]
    live = p["amp"] > p["amp"].max() * 10 ** (-PITCH_DB / 20)
    f = np.maximum(p["f"], 1.0)
    med = float(np.median(f[live]))
    return {"f0_line": p["f0"], "median_hz": med, "cents": 1200 * np.log2(f / med), "live": live}


def take_f0(T) -> float:
    return trajectory(T.a)["median_hz"]


def floor_noise(x, sr, n_out: int, gain_db: float = 0.0) -> np.ndarray:
    """The take's OWN stationary floor: its last 100 ms, high-passed at 1 kHz so
    no residue of a <= 450 Hz ring can ride along, tiled to n_out samples."""
    seg = x[-int(0.1 * sr):]
    seg = sosfiltfilt(butter(4, 1000 / (sr / 2), btype="highpass", output="sos"), seg)
    reps = int(math.ceil(n_out / len(seg)))
    return np.tile(seg, reps)[:n_out] * 10 ** (gain_db / 20)


def with_floor(y, ysr, x, sr, gain_db: float = 0.0):
    """Our render at the take's own loudness, with the take's floor added at
    its own level (x gain_db). Returned at the take's rate: the floor is the
    take's own samples, never resampled."""
    yr = np.asarray(y, dtype=np.float64) if ysr == sr else _to(y, ysr, sr)
    yr = yr * 10 ** ((pg.loudness_db(pg.to_rate(x, sr)) - pg.loudness_db(pg.to_rate(yr, sr))) / 20)
    return yr + floor_noise(x, sr, len(yr), gain_db), sr


def _to(y, ysr, sr):
    from scipy.signal import resample_poly
    g = math.gcd(ysr, sr)
    return resample_poly(np.asarray(y, dtype=np.float64), sr // g, ysr // g)


def score(T, bar, y, ysr, label) -> dict:
    v = pg.verdict(T.distance(y, ysr, label), bar)
    return {"verdict": v["verdict"], "worst": v["worst_ratio"], "worst_feature": v["worst_feature"],
            "ratio": {f: v["features"][f].get("ratio") for f in pg.FEATURES},
            "d": {f: v["features"][f].get("d") for f in pg.FEATURES}}


def git(*a) -> str:
    p = subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True)
    if p.returncode != 0:
        raise Refused(f"git {' '.join(a)} failed ({p.returncode}): {p.stderr.strip()}")
    return p.stdout.strip()


#: What a record's numbers depend on: the code (model/, tools/) and the frozen
#: pre-registration this probe reads. The records it WRITES live beside
#: prereg.json and are deliberately not in scope, so regenerating one record
#: does not block the next.
DIRTY_SCOPE = ("model", "tools", str(PREREG.relative_to(ROOT)))


def provenance(refs: pathlib.Path | None) -> dict:
    """Provenance read at the START of a measuring command (#589 review: it was
    read when the run finished, so run.json/twin.json named a commit neither ran
    from). REFUSES on a dirty model/, tools/ or prereg.json: a record has to name
    a commit that reproduces it. The modules the measurement uses are imported
    here, so the code that runs is the code this commit + clean flag describe
    even if the tree is edited mid-run."""
    import drums_fx  # noqa: F401
    import run_case  # noqa: F401
    dirty = git("status", "--porcelain", "--", *DIRTY_SCOPE)
    if dirty:
        raise Refused("uncommitted changes in " + ", ".join(DIRTY_SCOPE)
                      + "; commit first so the record names the code that produced it:\n" + dirty)
    return {"commit": git("rev-parse", "HEAD"),
            "model_tools_dirty": False,
            "read_at": "start",
            "origin_main": git("rev-parse", "origin/main"),
            "prereg_sha256_16": sha16(PREREG) if PREREG.exists() else None,
            "refs": None if refs is None else str(refs)}


def close_provenance(prov: dict) -> dict:
    """Re-read HEAD and the dirty flag when the run ends. It does not replace
    the start record; it says whether the checkout moved under the run."""
    end = git("rev-parse", "HEAD")
    prov["commit_at_end"] = end
    prov["head_moved_during_run"] = end != prov["commit"]
    prov["dirty_at_end"] = bool(git("status", "--porcelain", "--", *DIRTY_SCOPE))
    return prov


# ---------------------------------------------------------------------------
# diagnose: the gate's own target
# ---------------------------------------------------------------------------
def diagnose(refs: pathlib.Path, sounds, hashes=None) -> dict:
    out = {}
    for s in sounds:
        rel = pg.target_rel(s)
        x, sr = load_checked(refs, rel, hashes)
        T = pg.Target(x, sr, s, rel)
        b = pg.bar_for(s, refs, T)
        y, ysr = pg.render_ours(s)
        ao = pg.analyse(pg.condition(np.asarray(y, dtype=np.float64), ysr, side=f"ours {s}"), T.plan)
        v = pg.verdict(pg.distances(T.a, ao, T.plan), b["bar"])
        tt, to = trajectory(T.a), trajectory(ao)
        imp = {"target": T.a["impulse"].tolist(), "ours": ao["impulse"].tolist()}
        for nb in b["neighbours"]:
            nx, nsr = load_checked(refs, nb, hashes)
            imp[nb] = pg.analyse(pg.condition(*pg.candidate_path(nx, nsr), side=nb), T.plan)["impulse"].tolist()
        n = min(40, len(tt["cents"]))
        out[s] = {"target": rel, "bar_from": b["from"], "verdict": v["verdict"],
                  "worst": v["worst_ratio"], "worst_feature": v["worst_feature"],
                  "ratio": {f: v["features"][f].get("ratio") for f in pg.FEATURES},
                  "d": {f: v["features"][f].get("d") for f in pg.FEATURES},
                  "bar": {f: v["features"][f].get("bar") for f in pg.FEATURES},
                  "impulse_db_strike_body": imp,
                  "pitch": {"target_median_hz": tt["median_hz"], "ours_median_hz": to["median_hz"],
                            "target_cents_5ms": [round(float(c), 1) if l else None
                                                 for c, l in zip(tt["cents"][:n], tt["live"][:n])],
                            "ours_cents_5ms": [round(float(c), 1) if l else None
                                               for c, l in zip(to["cents"][:n], to["live"][:n])]}}
        r = out[s]
        print(f"{s} {r['verdict']} worst {r['worst']:.1f} ({r['worst_feature']}) "
              f"pitch_shape {r['ratio']['pitch_shape']:.1f}x impulse {r['ratio']['impulse']:.1f}x", flush=True)
        print("   impulse dB (strike, body):", {k: [round(q, 1) for q in v_] for k, v_ in imp.items()})
        print("   808 cents :", r["pitch"]["target_cents_5ms"][:12])
        print("   ours cents:", r["pitch"]["ours_cents_5ms"][:12])
    return out


# ---------------------------------------------------------------------------
# known answers, independent of our model
# ---------------------------------------------------------------------------
KA_VOICES = {"LT": (90.0, 0.0876), "MT": (135.0, 0.0577), "HT": (185.0, 0.0417),
             "LC": (185.0, 0.0769), "MC": (280.0, 0.0387), "HC": (400.0, 0.0343)}


def resonator(f0: float, tau: float, numer=(1.0,), seconds=1.0, sr=48000, lead=480) -> np.ndarray:
    """A float two-pole resonator's impulse response, with a chosen numerator.
    (1,) is all-pole (sin-phase onset, what the RAW mode is); (1, 0, -1) is the
    bilinear band-pass (cos-phase onset, the bridged-T's s / (s^2 + ...))."""
    n = int(seconds * sr)
    imp = np.zeros(n)
    imp[lead] = 1.0
    w, r = 2 * math.pi * f0 / sr, math.exp(-1.0 / (tau * sr))
    return lfilter(list(numer), [1.0, -2 * r * math.cos(w), r * r], imp)


def ka_pitch_onset(f0: float, tau: float, numer, sound: str = "LC") -> dict:
    """The gate's pitch trajectory and impulse statistic for a known resonator,
    analysed against ITSELF as the target (no corpus: the plan is its own)."""
    y = resonator(f0, tau, (1.0,) if "allpole-stub" in INJECT else numer)
    yc = pg.condition(y, 48000, side="ka")
    plan = pg.plan_from_target(yc, sound)
    a = pg.analyse(yc, plan)
    t = trajectory(a)
    return {"cents_first_15ms": [round(float(c), 1) for c in t["cents"][:3]],
            "impulse_db": [round(float(v), 1) for v in a["impulse"]]}


def ka_floor(f0: float, tau: float, level_db: float | None, seed: int = 1, sound: str = "LC") -> list:
    """Body impulse statistic of an all-pole resonator with a stationary white
    floor at level_db re its peak (None: no floor)."""
    y = resonator(f0, tau)
    y = y / np.abs(y).max() * 0.5
    if level_db is not None:
        y = y + np.random.default_rng(seed).standard_normal(len(y)) * 0.5 * 10 ** (level_db / 20)
    yc = pg.condition(y, 48000, side="ka")
    plan = pg.plan_from_target(yc, sound)
    return [round(float(v), 1) for v in pg.analyse(yc, plan)["impulse"]]


def knownanswer() -> dict:
    out = {"pitch_onset": {}, "floor": {}}
    for s, (f0, tau) in KA_VOICES.items():
        out["pitch_onset"][s] = {"all-pole": ka_pitch_onset(f0, tau, (1.0,), s),
                                 "band-pass (1 - z^-2)": ka_pitch_onset(f0, tau, (1.0, 0.0, -1.0), s),
                                 "AC zero (1 - z^-1)": ka_pitch_onset(f0, tau, (1.0, -1.0), s)}
        out["floor"][s] = {str(lv): ka_floor(f0, tau, lv, sound=s) for lv in (None, -100.0, -85.0, -70.0)}
        print(s, json.dumps(out["pitch_onset"][s]), "| floor ->", json.dumps(out["floor"][s]), flush=True)
    return out


# ---------------------------------------------------------------------------
# run: every pre-registered condition
# ---------------------------------------------------------------------------
def run(refs: pathlib.Path, prereg: dict, sounds=None, codes=None) -> dict:
    hashes = prereg["corpus"]["sha256_16"]
    # precondition: the retune at unity IS the engine that ships
    import run_case as rc
    for s in ("LT", "HC"):
        if not np.array_equal(render(s)[0], rc.render_drum_solo(s)[0]):
            raise Refused(f"render({s}) at ratio 1.0 is not render_drum_solo: not the shipped engine")
    variants = prereg["candidates"]
    rows = {}
    for s in sounds or FAMILY:
        x50, sr50 = load_checked(refs, take_rel(s, "50"), hashes)
        f50 = take_f0(pg.Target(x50, sr50, s, take_rel(s, "50")))
        for c in codes or pg.CODES:
            rel = take_rel(s, c)
            x, sr = load_checked(refs, rel, hashes)
            T = pg.Target(x, sr, s, rel)
            b = bar_for_take(s, rel, refs, T, hashes)
            ratio = 1.0 if c == "50" else take_f0(T) / f50
            row = {"take": rel, "bar_from": b["from"], "tuning_ratio": ratio,
                   "split": prereg["split_of"][f"{s}{c}"], "variants": {}, "peak_fs": {}}
            for name in ["shipped"] + list(variants):
                y, ysr = render(s, ratio, None if name == "shipped" else variants[name])
                row["peak_fs"][name] = float(np.max(np.abs(y)))
                row["variants"][name] = score(T, b["bar"], y, ysr, f"{name} {s}{c}")
                if name == "shipped":
                    for gdb in prereg["attribution"]["floor_gain_db"]:
                        yf, sf = with_floor(y, ysr, x, sr, gdb)
                        row["variants"][f"shipped+take_floor{gdb:+.0f}dB"] = score(T, b["bar"], yf, sf,
                                                                                   f"floor {s}{c}")
            rows[f"{s}{c}"] = row
            print(f"{s}{c} [{row['split']}] x{ratio:.3f} " + " | ".join(
                f"{k}: ps {v['ratio']['pitch_shape']:.2f} imp {v['ratio']['impulse']:.2f} "
                f"worst {v['worst']:.1f}({v['worst_feature']})" for k, v in row["variants"].items()), flush=True)
    return {"rows": rows}


def harmonics(y) -> dict:
    """H2..H4 in dB re H1, from 20 to 300 ms of a conditioned signal."""
    a, b = pg._LEAD + int(0.02 * pg.SR), pg._LEAD + int(0.3 * pg.SR)
    seg = y[a:b]
    S = np.abs(np.fft.rfft(seg * np.hanning(len(seg)), 1 << 18))
    f = np.fft.rfftfreq(1 << 18, 1 / pg.SR)
    f0 = float(f[np.argmax(np.where((f > 40) & (f < 600), S, 0))])
    lv = [20 * math.log10(S[(f > k * f0 * 0.95) & (f < k * f0 * 1.05)].max()) for k in (1, 2, 3, 4)]
    return {"f0": f0, "H2_H4_db_re_H1": [x - lv[0] for x in lv[1:]]}


def h2_response(refs: pathlib.Path, hashes=None) -> dict:
    """POST-HOC DIAGNOSTIC (H4, untested as a candidate): the 808's toms carry
    H2..H4 at -33..-41 dB and ours at < -80; do the gate's failing features move
    when a quadratic term puts H2 at the 808's level? On the TUNING 5.0 targets
    only, which were seen in diagnosis, so this is a pointer, not evidence."""
    out = {}
    for s in FAMILY:
        rel = pg.target_rel(s)
        x, sr = load_checked(refs, rel, hashes)
        T = pg.Target(x, sr, s, rel)
        b = pg.bar_for(s, refs, T)["bar"]
        y, ysr = pg.render_ours(s)
        y = np.asarray(y, dtype=np.float64)
        row = {"harmonics_808": harmonics(T.y), "harmonics_ours": harmonics(pg.condition(y, ysr))}
        if s in ("LT", "MT", "HT"):
            pk = float(np.abs(y).max())
            for h2 in (None, -37.0, -57.0):
                z = y if h2 is None else y + 2 * 10 ** (h2 / 20) / pk * y * y
                row[f"H2 {h2}"] = score(T, b, z, ysr, "h2")["ratio"]
        out[s] = row
        print(s, json.dumps(_json(row))[:400], flush=True)
    return out


def ratepath16(refs: pathlib.Path) -> dict:
    """CLASS SEARCH for the rate-path finding, all sixteen gate sounds at their
    gate targets: `impulse` (strike, body and ratio) for the shipped render at
    its native 48 kHz and the SAME render through the target's own rate. A
    48 kHz target is a no-op round trip, and is reported as such."""
    out = {}
    for s in pg.SOUNDS16:
        rel = pg.target_rel(s)
        x, sr = load_checked(refs, rel)
        T = pg.Target(x, sr, s, rel)
        b = pg.bar_for(s, refs, T)["bar"]
        y, ysr = pg.render_ours(s)
        y = np.asarray(y, dtype=np.float64)
        row = {"target_sr": sr, "target_impulse_db": T.a["impulse"].tolist()}
        for name, (z, zs) in (("native", (y, ysr)), ("via-take-rate", (_to(y, ysr, sr) if sr != ysr else y, sr))):
            a_ = pg.analyse(pg.condition(z, zs, side=name), T.plan)
            row[name] = {"impulse_db": a_["impulse"].tolist(),
                         "impulse_ratio": score(T, b, z, zs, name)["ratio"]["impulse"]}
        out[s] = row
        print(s, sr, "target", np.round(T.a["impulse"], 1), "| native", np.round(row["native"]["impulse_db"], 1),
              f"x{row['native']['impulse_ratio']:.2f}", "| via take rate", np.round(row["via-take-rate"]["impulse_db"], 1),
              f"x{row['via-take-rate']['impulse_ratio']:.2f}", flush=True)
    return out


def twin(refs: pathlib.Path, prereg: dict, sounds=None, codes=None) -> dict:
    """The float twin, RAW and with the band-pass numerator, on every
    condition. Post-hoc: added after `run` showed BP failing on the fixed-point
    engine; it selects nothing and confirms nothing."""
    hashes = prereg["corpus"]["sha256_16"]
    rows = {}
    for s in sounds or FAMILY:
        x50, sr50 = load_checked(refs, take_rel(s, "50"), hashes)
        f50 = take_f0(pg.Target(x50, sr50, s, take_rel(s, "50")))
        for c in codes or pg.CODES:
            rel = take_rel(s, c)
            x, sr = load_checked(refs, rel, hashes)
            T = pg.Target(x, sr, s, rel)
            b = bar_for_take(s, rel, refs, T, hashes)
            ratio = 1.0 if c == "50" else take_f0(T) / f50
            row = {"take": rel, "split": prereg["split_of"][f"{s}{c}"], "tuning_ratio": ratio, "variants": {}}
            for name, numer in (("twin-RAW", (1.0,)), ("twin-BP", (1.0, 0.0, -1.0))):
                row["variants"][name] = score(T, b["bar"], *render_twin(s, ratio, numer), f"{name} {s}{c}")
            # the RATE PATH: the shipped render, unchanged, through the take's own
            # rate (48 k -> 44.1 k; the gate then brings it back to 48 k exactly as
            # it does every Fischer take). Nothing is added to the signal.
            y, ysr = render(s, ratio)
            row["variants"]["shipped-via-take-rate"] = score(T, b["bar"], _to(y, ysr, sr), sr, f"rate {s}{c}")
            row["variants"]["shipped"] = score(T, b["bar"], y, ysr, f"shipped {s}{c}")
            rows[f"{s}{c}"] = row
            print(f"{s}{c} [{row['split']}] " + " | ".join(
                f"{k}: ps {v['ratio']['pitch_shape']:.2f} decay {v['ratio']['decay']:.2f} imp {v['ratio']['impulse']:.2f} "
                f"worst {v['worst']:.1f}({v['worst_feature']})" for k, v in row["variants"].items()), flush=True)
    return {"rows": rows, "post_hoc": True}


# ---------------------------------------------------------------------------
# judge: the frozen rules
# ---------------------------------------------------------------------------
def _violations(base: dict, cand: dict, rule: dict) -> list:
    out = []
    if "no-preserve" in INJECT:
        return out
    for f in pg.FEATURES:
        if f in rule["targets"]:
            continue
        rb, rc_ = base["ratio"].get(f), cand["ratio"].get(f)
        if rb is None or rc_ is None:
            continue
        if rc_ > rb * rule["preserve_rel"] + rule["preserve_abs"]:
            out.append(f"{f} {rb:.2f}->{rc_:.2f}")
    return out


def judge(record: dict, prereg: dict) -> dict:
    rule = prereg["rules"]
    res = {"selection": {}, "confirmation": {}, "attribution": {}}
    rows = record["rows"]
    dev = [k for k, r in rows.items() if r["split"] == "development"]
    unt = [k for k, r in rows.items() if r["split"] == "untouched"
           or ("confirm-reads-all" in INJECT and r["split"] == "development")]

    def summarise(keys, name):
        red, viol, peak = [], {}, {}
        for k in keys:
            b, c = rows[k]["variants"]["shipped"], rows[k]["variants"][name]
            red.append(1 - c["ratio"]["pitch_shape"] / b["ratio"]["pitch_shape"])
            v = _violations(b, c, rule)
            dp = 20 * math.log10(rows[k]["peak_fs"][name] / rows[k]["peak_fs"]["shipped"])
            if abs(dp) > rule["peak_db"] and "no-peak" not in INJECT:
                v.append(f"peak {dp:+.2f} dB")
            if v:
                viol[k] = v
            peak[k] = round(dp, 2)
        return {"pitch_shape_reduction_median": statistics.median(red),
                "pitch_shape_improved_frac": sum(r > 0 for r in red) / len(red),
                "violations": viol, "peak_change_db": peak, "n": len(keys)}

    for name in prereg["candidates"]:
        s = summarise(dev, name)
        s["admissible"] = not s["violations"]
        res["selection"][name] = s
    adm = [n for n, s in res["selection"].items() if s["admissible"]]
    chosen = (min(adm, key=lambda n: -res["selection"][n]["pitch_shape_reduction_median"])
              if adm else None)
    res["chosen"] = chosen
    for name in prereg["candidates"]:
        s = summarise(unt, name)
        s["confirmed"] = (name == chosen and not s["violations"]
                          and s["pitch_shape_improved_frac"] >= rule["confirm_improved_frac"]
                          and s["pitch_shape_reduction_median"] >= rule["confirm_median_reduction"])
        s["role"] = "selected candidate" if name == chosen else "not selected: reported, not confirmable"
        res["confirmation"][name] = s
    att = prereg["attribution"]
    for gdb in att["floor_gain_db"]:
        key = f"shipped+take_floor{gdb:+.0f}dB"
        within = {f: sum(rows[k]["variants"][key]["ratio"][f] <= 1.0 for k in rows)
                  for f in ("impulse", "modulation", "pitch_shape")
                  if all(rows[k]["variants"][key]["ratio"][f] is not None for k in rows)}
        base = {f: sum(rows[k]["variants"]["shipped"]["ratio"][f] <= 1.0 for k in rows)
                for f in within}
        res["attribution"][key] = {"n": len(rows), "within_bar": within, "shipped_within_bar": base}
    return res


def post_hoc(run_rec: dict, twin_rec: dict) -> dict:
    """POST-HOC tables from `twin` (selects and confirms nothing): how often the
    float twin's band-pass, the fixed-point candidates and the rate path move
    each feature, per split and per position (tom / conga)."""
    out = {}
    for split in ("development", "untouched", "seen-in-diagnosis"):
        for pos, names in (("tom", ("LT", "MT", "HT")), ("conga", ("LC", "MC", "HC"))):
            keys = [k for k, r in run_rec["rows"].items() if r["split"] == split and k[:2] in names]
            if not keys:
                continue
            cell = {"n": len(keys)}
            for var, src, base in (("BP", run_rec, "shipped"), ("BP+X4", run_rec, "shipped"),
                                   ("twin-BP", twin_rec, "twin-RAW")):
                rs = [1 - src["rows"][k]["variants"][var]["ratio"]["pitch_shape"]
                      / src["rows"][k]["variants"][base]["ratio"]["pitch_shape"] for k in keys]
                dec = [src["rows"][k]["variants"][var]["ratio"]["decay"]
                       / src["rows"][k]["variants"][base]["ratio"]["decay"] for k in keys]
                cell[var] = {"pitch_shape_improved": sum(r > 0 for r in rs),
                             "pitch_shape_reduction_median": statistics.median(rs),
                             "decay_ratio_x_median": statistics.median(dec),
                             "decay_worse_than_x1.1_plus": sum(
                                 src["rows"][k]["variants"][var]["ratio"]["decay"]
                                 > 1.10 * src["rows"][k]["variants"][base]["ratio"]["decay"] + 0.10
                                 for k in keys)}
            cell["impulse_within_bar"] = {
                v: sum(twin_rec["rows"][k]["variants"][v]["ratio"]["impulse"] <= 1.0 for k in keys)
                for v in ("shipped", "shipped-via-take-rate")}
            out[f"{split}/{pos}"] = cell
    return out


def _json(o):
    if isinstance(o, (bool, np.bool_)):
        return bool(o)
    if isinstance(o, (float, np.floating)):
        o = float(o)
        return round(o, 4) if math.isfinite(o) else str(o)
    if isinstance(o, dict):
        return {k: _json(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_json(v) for v in o]
    if isinstance(o, np.ndarray):
        return _json(o.tolist())
    return o


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("diagnose", "run", "twin"):
        p = sub.add_parser(name)
        p.add_argument("--refs", type=pathlib.Path, required=True)
        p.add_argument("--sounds", nargs="*", default=None)
        p.add_argument("--out", type=pathlib.Path, required=True)
        if name in ("run", "twin"):
            p.add_argument("--codes", nargs="*", default=None)
    h = sub.add_parser("harmonics")
    h.add_argument("--refs", type=pathlib.Path, required=True)
    h.add_argument("--out", type=pathlib.Path, required=True)
    r16 = sub.add_parser("ratepath16")
    r16.add_argument("--refs", type=pathlib.Path, required=True)
    r16.add_argument("--out", type=pathlib.Path, required=True)
    k = sub.add_parser("knownanswer")
    k.add_argument("--out", type=pathlib.Path, required=True)
    j = sub.add_parser("judge")
    j.add_argument("record", type=pathlib.Path)
    j.add_argument("--twin", type=pathlib.Path, default=None, help="add the post-hoc tables from `twin`")
    j.add_argument("--out", type=pathlib.Path, required=True)
    a = ap.parse_args(argv)
    try:
        prov = None if a.cmd == "judge" else provenance(getattr(a, "refs", None))
        if a.cmd == "knownanswer":
            res = knownanswer()
        elif a.cmd == "harmonics":
            res = h2_response(a.refs, load_prereg()["corpus"]["sha256_16"])
        elif a.cmd == "ratepath16":
            res = ratepath16(a.refs)
        elif a.cmd == "judge":
            rec = json.loads(a.record.read_text())
            res = judge(rec, load_prereg())
            if a.twin:
                res["post_hoc"] = post_hoc(rec, json.loads(a.twin.read_text()))
            print(json.dumps(_json(res), indent=1))
        else:
            pr = load_prereg()
            res = (diagnose(a.refs, a.sounds or list(FAMILY), pr["corpus"]["sha256_16"])
                   if a.cmd == "diagnose" else
                   run(a.refs, pr, a.sounds, a.codes) if a.cmd == "run" else
                   twin(a.refs, pr, a.sounds, a.codes))
        if prov is not None:
            res["provenance"] = close_provenance(prov)
    except (Refused, pg.Refused) as e:
        print("REFUSED:", e)
        return 2
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(_json(res), indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

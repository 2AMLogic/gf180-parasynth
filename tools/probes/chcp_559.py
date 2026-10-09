#!/usr/bin/env python3
"""#559: the closed hat's flatness and the clap's decay / attack against the
#379 perceptual gate. Diagnosis, candidate selection on DEV conditions,
confirmation on conditions not used to select.

WHAT THIS IS AND IS NOT. The #379 gate (`tools/perceptual_gate.py`) is used
UNCHANGED: same conditioning, same features, same WEAK bar (CH and CP have one
Fischer take each, so the bar is that take played x1.0628 faster). No bar is
widened and no preprocessing is changed to accept anything. The WEAK bar ranks;
it is not a calibrated between-recording bar (gate-379 README section 8: that
calibration was REFUSED), so every improvement here is COMPARATIVE, and final
fidelity qualification is REFUSED by construction until #379/#560 qualify an
acceptance policy.

WHAT IS INDEPENDENT OF OUR MODEL. The targets are the Fischer TR-808 takes
(CC0, s/n 103852), pinned below by SHA-256; the probe REFUSES on a missing or
different file. "Varying our model's noise phase" does NOT create independent
real recordings: CH and CP have exactly one take each, so DEV and CONFIRM differ
in OUR realisation (strike frame -> noise / oscillator phase, and accent), never
in the reference.

FROZEN BEFORE ANY CANDIDATE WAS MEASURED (the shipped baseline at the default
strike frame had been measured once, to reproduce gate-379 section 4):
  DEV_OFFSETS       strike frame offsets used to select
  CONFIRM_OFFSETS   untouched strike offsets, and CONFIRM_ACCENTS at two of
                    them, used only after selection
Both drawn from numpy PCG64(559) (`_draw_offsets`), so nobody chose them.

THE CP FAST PATH, and why it is exact rather than an emulator. The clap's
output is `(s_nl * (ENV_burst + ENV_tail)) >> 15` on the mix bus, where s_nl is
tanh of the band-passed noise tap. The band-pass's input is noise x ENV_FULL,
so s_nl does not depend on any envelope or any hit: it is recorded once from the
real block, and a variant only re-runs the two `EnvFx` objects (the production
class) and the output stage. `check-fast` asserts bit-identity against full
renders of the real block for the shipped kit AND for a variant that changes
every envelope register the sweep touches. hh_probe4 (tools/probes/hihat) is
the record of an emulator used for a knob it was not validated for; this one is
asserted on the knobs it is used for, and refuses otherwise.

Subcommands (each prints, and writes JSON with --out):
  refs                 check the pinned reference hashes
  baseline             the shipped CH and CP through the gate, per-feature and
                       per-band-group, at the default strike and the DEV set
  check-fast           the CP fast path vs full real-block renders (REFUSE on
                       any differing sample)
  cp-tail              the reference's own tail shape: is the late energy a
                       decaying signal or the recording floor? (SD from the
                       same session as the chain control)
  cp-sweep             CP candidates on DEV only
  cp-confirm           the selected CP candidate on CONFIRM only
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "tools"), str(ROOT / "model"), str(ROOT / "audition")]

import perceptual_gate as pg  # noqa: E402

CACHE = ROOT / "build" / "probes" / "chcp559"
#: One recording covers every DEV and CONFIRM strike plus a 2.2 s render
#: (30167 - 480 + 105600 = 135287 frames). A recorded sequence is generated
#: from reset frame by frame and depends on no hit, so a shorter render's is an
#: exact PREFIX of it; check-fast / check-cpt assert the result bit for bit.
N_REC = 136000

# ---------------------------------------------------------------------------
# the references, pinned
# ---------------------------------------------------------------------------
#: tidalcycles/sounds-tr808-fischer at 85fbecf. CP's hash is the one
#: docs/scorecard/clap-d12a/README.md section 1 records; SD5050 is the session
#: control for "is a slow tail the recording chain?".
PINNED = {
    "ch8/CH.WAV": "c9f30ff2b4d73b03f41960e504e03c54e9a59697af666fe4d155bab9cd1ccae6",
    "cp8/CP.WAV": "376429bb81cb48d1f392a11cd066c32ffb7883d445b2488704ea52e46bb08286",
    "sd8/SD5050.WAV": "6dcbf8acd5cee6d6b7955d26c6b57d8ca413f1ae1c7caafaaec780d83f08d712",
}


class Refused(RuntimeError):
    """A precondition failed; the probe withholds a number."""


def default_refs() -> pathlib.Path:
    return pathlib.Path(os.environ.get("GF180_TR808_REFS")
                        or pathlib.Path.home() / "dev/refs/sounds-tr808-fischer")


def check_refs(refs: pathlib.Path) -> dict:
    out = {}
    for rel, want in PINNED.items():
        p = refs / rel
        if not p.is_file():
            raise Refused(f"reference missing: {p} (set GF180_TR808_REFS to the Fischer corpus)")
        got = hashlib.sha256(p.read_bytes()).hexdigest()
        if got != want:
            raise Refused(f"reference {rel} is {got[:16]}..., pinned {want[:16]}...: a different file")
        out[rel] = got
    return out


# ---------------------------------------------------------------------------
# frozen conditions
# ---------------------------------------------------------------------------
def _draw_offsets(n_dev: int = 4, n_conf: int = 6) -> tuple:
    rng = np.random.Generator(np.random.PCG64(559))
    draw = sorted(set(int(v) for v in rng.integers(1, 30000, size=n_dev + n_conf)))
    rng.shuffle(draw)
    return tuple(sorted(draw[:n_dev])), tuple(sorted(draw[n_dev:]))


DEV_OFFSETS, CONFIRM_OFFSETS = _draw_offsets()
CONFIRM_ACCENTS = (0.5, 2.0)          # at CONFIRM_OFFSETS[0] and [1]
BASE_HIT = 480                        # run_case.DRUM_SOLO_HIT_FRAME


def conditions(which: str) -> list:
    """(strike frame, accent) pairs. DEV: the shipped strike plus DEV_OFFSETS
    at accent 1. CONFIRM: CONFIRM_OFFSETS at accent 1, plus two accents."""
    if which == "dev":
        return [(BASE_HIT, 1.0)] + [(BASE_HIT + o, 1.0) for o in DEV_OFFSETS]
    if which == "confirm":
        c = [(BASE_HIT + o, 1.0) for o in CONFIRM_OFFSETS]
        c += [(BASE_HIT + CONFIRM_OFFSETS[i], a) for i, a in enumerate(CONFIRM_ACCENTS)]
        return c
    raise ValueError(which)


# ---------------------------------------------------------------------------
# the gate, unchanged, plus a per-band-group breakdown
# ---------------------------------------------------------------------------
_TARGETS: dict = {}


def target(sound: str, refs: pathlib.Path):
    if sound not in _TARGETS:
        rel = pg.target_rel(sound)
        x, sr = pg.load_wav(refs / rel)
        T = pg.Target(x, sr, sound, rel)
        _TARGETS[sound] = (T, pg.bar_for(sound, refs, T))
    return _TARGETS[sound]


def gate(sound: str, y, sr, refs: pathlib.Path) -> dict:
    T, b = target(sound, refs)
    if not np.all(np.isfinite(y)):
        raise Refused(f"{sound}: non-finite samples in the candidate")
    yc = pg.condition(y, sr, side=f"candidate {sound}")
    ao = pg.analyse(yc, T.plan)
    d = pg.distances(T.a, ao, T.plan)
    v = pg.verdict(d, b["bar"])
    groups = []
    for g, (lo, hi) in enumerate(pg.GROUPS):
        et, eo = T.a["edc"][g], ao["edc"][g]
        live = et > -pg.DECAY_DB
        dec = float(np.sqrt(np.mean((np.maximum(eo[live], -pg.DECAY_CLIP_DB)
                                     - np.maximum(et[live], -pg.DECAY_CLIP_DB)) ** 2)))
        att = float(np.sqrt(np.mean((ao["attack"][g] - T.a["attack"][g]) ** 2)))
        used = max(T.a["gshare"][g], ao["gshare"][g]) >= pg.GROUP_MIN_SHARE
        groups.append({"band": [lo, hi], "used": bool(used), "decay": dec, "attack": att,
                       "attack_ms_t": T.a["attack"][g].tolist(), "attack_ms_o": ao["attack"][g].tolist(),
                       "gshare_t": float(T.a["gshare"][g]), "gshare_o": float(ao["gshare"][g])})
    ratios = {f: r["ratio"] for f, r in v["features"].items() if "ratio" in r}
    return {"verdict": v["verdict"], "worst": v["worst_ratio"], "worst_feature": v["worst_feature"],
            "ratios": ratios, "d": {f: r.get("d") for f, r in v["features"].items() if "d" in r},
            "groups": groups}


# ---------------------------------------------------------------------------
# renders through the real block
# ---------------------------------------------------------------------------
def _dx():
    import drums_fx as dx
    return dx


def model_sha() -> str:
    return hashlib.sha256((ROOT / "model" / "drums_fx.py").read_bytes()).hexdigest()[:16]


#: Source that a render here executes or builds candidate kits with. kit_808,
#: preset_writes and kit_with_sounds are NOT here by design: their only effect
#: on a render is the register images, which are hashed directly below (a
#: source hash would refuse a behaviour-preserving refactor of them, #559).
ENGINE_NAMES = ("EnvFx", "DrumsFx", "output_fx", "lfsr_frame", "rate_reg", "peak_reg", "accent_reg",
                "amp_reg", "mode_regs", "mode_writes", "env_writes", "env_ctl", "path_word", "hit_writes")


def engine_fingerprint(dx=None) -> str:
    """What a render here depends on: the engine's source (the classes and
    functions above, plus model/fixed.py and model/modal_fixed.py whole) and
    the register images every sound the probe touches is rendered from. A
    file hash refuses on an unrelated edit to drums_fx.py; this refuses on any
    edit that could move a sample here, including a changed kit_808()."""
    import inspect
    dx = dx or _dx()
    h = hashlib.sha256()
    for name in ENGINE_NAMES:
        h.update(inspect.getsource(getattr(dx, name)).encode())
    for f in ("fixed.py", "modal_fixed.py"):
        h.update((ROOT / "model" / f).read_bytes())
    for s in ("CH", "CP", "OH", "CY", "MA", "RS"):
        h.update(repr(dx.kit_with_sounds(s)).encode())
    return h.hexdigest()[:16]


def engine_fingerprint_at(commit: str) -> str:
    """engine_fingerprint() of model/drums_fx.py as it was at `commit`."""
    import importlib.util
    import subprocess
    import tempfile
    src = subprocess.run(["git", "show", f"{commit}:model/drums_fx.py"], cwd=ROOT,
                         capture_output=True, text=True, check=True).stdout
    with tempfile.TemporaryDirectory() as td:
        p = pathlib.Path(td) / "drums_fx_at.py"
        p.write_text(src)
        spec = importlib.util.spec_from_file_location("drums_fx_at", p)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["drums_fx_at"] = mod          # dataclasses resolve their module by name
        try:
            spec.loader.exec_module(mod)
            return engine_fingerprint(mod)
        finally:
            sys.modules.pop("drums_fx_at", None)


def render_block(sound: str, kit: list, hit: int, accent: float = 1.0, seconds: float = 2.2) -> np.ndarray:
    """One hit through the REAL DrumsFx and output stage, exactly as
    run_case.render_drum_solo does it, with `kit` in place of the shipped
    image and the render running `seconds` past the strike's lead."""
    dx = _dx()
    n = hit - BASE_HIT + int(seconds * dx.SR)
    d = dx.DrumsFx()
    dm, bd = d.play(dx.hit_writes([(hit, dx.SOUND_STOP[sound], accent)], kit), n)
    g = dx.accent_reg(0.45)
    out = dx.output_fx(np.zeros(n), 0, dm, g, bd, g)
    return np.asarray(out, dtype=np.float64)[hit - BASE_HIT:] / 32768.0


# ---- the clap's exact fast path ------------------------------------------
def cp_snl(n: int) -> np.ndarray:
    """tanh(band-passed noise tap) per frame, from the real block with the CP
    circuit loaded and no hit. Cached by model hash and length."""
    dx = _dx()
    if n > N_REC:
        raise Refused(f"a {n}-frame render is longer than the {N_REC}-frame recording")
    CACHE.mkdir(parents=True, exist_ok=True)
    p = CACHE / f"cp_snl_{model_sha()}_{N_REC}.npy"
    if p.exists():
        return np.load(p)[:n]
    n_req, n = n, N_REC
    rec = []

    class Rec(dx.DrumsFx):
        def _nonlinear(self, x, nl):
            r = super()._nonlinear(x, nl)
            if nl == dx.NL_TANH:
                rec.append(r)
            return r

    d = Rec()
    d.play([(0, a, v) for a, v in dx.kit_with_sounds("CP")], n)
    if len(rec) != n:
        raise Refused(f"expected exactly one NL_TANH path per frame (the clap's), got {len(rec)} in {n}")
    s = np.asarray(rec, dtype=np.int64)
    np.save(p, s)
    return s[:n_req]


def env_trace(kit: list, e: int, stop: int, hit: int, accent: float, n: int) -> np.ndarray:
    """One production EnvFx programmed from `kit`'s registers, struck at `hit`."""
    dx = _dx()
    img = dict(kit)
    env = dx.EnvFx()
    base = dx.A_ENV + e * dx.ENV_STRIDE
    env.set_ctl(img.get(base, 0))
    env.peak = img.get(base + 1, 0) & dx.FULL24
    env.rate = img.get(base + 2, 0) & 0xFFFF
    env.frate = img.get(base + 3, 0) & 0xFFFF
    acc = [0] * dx.N_STOPS
    out = np.empty(n, dtype=np.int64)
    for f in range(n):
        fire = 0
        if f == hit:
            acc[stop] = dx.accent_reg(accent)
            fire = 1 << stop
        env.frame(fire, acc)
        out[f] = env.out
    return out


def cp_fast(kit: list, hit: int, accent: float = 1.0, seconds: float = 2.2, extra=None) -> np.ndarray:
    """The clap from the recorded s_nl and the two production envelopes.
    `extra` (EXPERIMENT ONLY -- no such slot exists in the block, N_ENV = 18 is
    full) is an optional third envelope trace summed with the two; it is the
    two-slope tail that would need a block change."""
    dx = _dx()
    n = hit - BASE_HIT + int(seconds * dx.SR)
    s = cp_snl(n)
    eb = env_trace(kit, dx.E_CPBURST, dx.CP, hit, accent, n)
    et = env_trace(kit, dx.E_CPTAIL, dx.CP, hit, accent, n)
    e = eb + et + (extra if extra is not None else 0)
    dmix = (s * e) >> 15
    g = dx.accent_reg(0.45)
    out = dx.output_fx(np.zeros(n), 0, dmix, g, np.zeros(n, dtype=np.int64), g)
    return np.asarray(out, dtype=np.float64)[hit - BASE_HIT:] / 32768.0


def cp_kit(tail_tau=None, tail_peak=None, final_tau=None, kit=None) -> list:
    """The shipped CP image with the tail / final-strike registers replaced."""
    dx = _dx()
    img = dict(kit if kit is not None else dx.kit_with_sounds("CP"))
    if tail_tau is not None or tail_peak is not None:
        for a, v in dx.env_writes(dx.E_CPTAIL, dx.CP, tail_tau if tail_tau is not None else dx.CP_TAIL_TAU,
                                  tail_peak if tail_peak is not None else 0.22):
            img[a] = v
    if final_tau is not None:
        for a, v in dx.env_writes(dx.E_CPBURST, dx.CP, dx.CP_BURST_TAU, 0.69, bursts=dx.CP_BURSTS,
                                  period=dx.CP_PERIOD, final_tau=final_tau):
            img[a] = v
    return sorted(img.items())


# ---- CP-T: a separately filtered tail (EXPERIMENT: needs a block change) ----
def cpt_kit(f_hz: float, q: float, exc_att: int, tail_peak: float, tail_tau_s: float) -> list:
    """A CP-SOLO prototype image of the separately filtered tail. It BORROWS
    the RS circuit's idle slots (P_RS1X, P_RS1OUT, M_RS1 -- RAW, index 14 >=
    NUMS), so it is only valid while RS/CL is not struck: the shipped block has
    no spare path or mode (N_PATH 23 and N_MODES 16 are full). The bursts keep
    P_CPOUT alone; the tail is noise -> RAW mode -> LIN x E_CPTAIL -> mix."""
    dx = _dx()
    img = dict(dx.kit_with_sounds("CP"))
    img[dx.A_PATH + dx.P_CPOUT] = dx.path_word(dx.SRC_TAP + dx.M_CPBP, dx.E_CPBURST, nl=dx.NL_TANH,
                                               dest=dx.DEST_MIX)
    for a, v in dx.mode_writes(dx.M_RS1, f_hz, q, 0.0, dx.RAW):
        img[a] = v
    img[dx.A_PATH + dx.P_RS1X] = dx.path_word(dx.SRC_NOISE, dx.ENV_FULL, att=exc_att, dest=dx.M_RS1)
    img[dx.A_PATH + dx.P_RS1OUT] = dx.path_word(dx.SRC_TAP + dx.M_RS1, dx.E_CPTAIL, nl=dx.NL_LIN,
                                                dest=dx.DEST_MIX)
    if not 0 < tail_peak <= 1.0:
        raise Refused(f"CP-T tail peak {tail_peak:.3f} is outside the envelope's full scale")
    for a, v in dx.env_writes(dx.E_CPTAIL, dx.CP, tail_tau_s, tail_peak):
        img[a] = v
    return sorted(img.items())


def cpt_tail_tap(f_hz: float, q: float, exc_att: int, n: int) -> np.ndarray:
    """The CP-T tail path's nonlinearity output per frame (LIN: the RAW mode's
    tap), from the real block with no hit. Independent of every envelope."""
    dx = _dx()
    if n > N_REC:
        raise Refused(f"a {n}-frame render is longer than the {N_REC}-frame recording")
    CACHE.mkdir(parents=True, exist_ok=True)
    p = CACHE / f"cpt_tap_{model_sha()}_{f_hz:.0f}_{q}_{exc_att}_{N_REC}.npy"
    if p.exists():
        return np.load(p)[:n]
    n_req, n = n, N_REC
    rec = []

    class Rec(dx.DrumsFx):
        def frame(self):
            self._calls = []
            r = super().frame()
            if len(self._calls) != dx.N_PATH:
                raise Refused(f"expected one nonlinearity call per path, got {len(self._calls)}")
            rec.append(self._calls[dx.P_RS1OUT])
            return r

        def _nonlinear(self, x, nl):
            y = super()._nonlinear(x, nl)
            self._calls.append(y)
            return y

    Rec().play([(0, a, v) for a, v in cpt_kit(f_hz, q, exc_att, 0.5, 0.08)], n)
    s = np.asarray(rec, dtype=np.int64)
    np.save(p, s)
    return s[:n_req]


def cpt_fast(f_hz, q, exc_att, tail_peak, tail_tau_s, hit, accent=1.0, seconds=2.2) -> np.ndarray:
    dx = _dx()
    n = hit - BASE_HIT + int(seconds * dx.SR)
    kit = cpt_kit(f_hz, q, exc_att, tail_peak, tail_tau_s)
    sb, st = cp_snl(n), cpt_tail_tap(f_hz, q, exc_att, n)
    eb = env_trace(kit, dx.E_CPBURST, dx.CP, hit, accent, n)
    et = env_trace(kit, dx.E_CPTAIL, dx.CP, hit, accent, n)
    dmix = ((sb * eb) >> 15) + ((st * et) >> 15)
    g = dx.accent_reg(0.45)
    out = dx.output_fx(np.zeros(n), 0, dmix, g, np.zeros(n, dtype=np.int64), g)
    return np.asarray(out, dtype=np.float64)[hit - BASE_HIT:] / 32768.0


def cpt_peak_for_db(f_hz, q, exc_att, db, n=N_REC) -> float:
    """The tail envelope peak that puts the CP-T tail's RMS at its fire `db`
    re the SHIPPED tail's (0.22 x rms of the tanh tap)."""
    sb, st = cp_snl(n), cpt_tail_tap(f_hz, q, exc_att, n)
    return 0.22 * float(np.std(sb)) / float(np.std(st)) * 10 ** (db / 20)


def check_cpt(f_hz=1300.0, q=0.7, exc_att=2) -> dict:
    """REFUSE unless the CP-T fast path is the real block on the borrowed-slot image."""
    peak = cpt_peak_for_db(f_hz, q, exc_att, -6.0)
    hit = BASE_HIT + DEV_OFFSETS[1]
    full = render_block("CP", cpt_kit(f_hz, q, exc_att, peak, 0.160), hit, 1.0)
    fast = cpt_fast(f_hz, q, exc_att, peak, 0.160, hit, 1.0)
    nd = int(np.sum(full != fast))
    print(f"check-cpt {f_hz:.0f} Hz Q{q} att{exc_att} peak {peak:.3f}: {nd} of {len(full)} samples differ", flush=True)
    if nd:
        raise Refused(f"CP-T fast path differs from the real block ({nd} samples)")
    return {"f_hz": f_hz, "q": q, "exc_att": exc_att, "peak": peak, "samples": len(full), "differing": nd}


def check_fast() -> dict:
    """REFUSE unless the fast path is the real block, sample for sample, on the
    shipped kit and on a variant moving tail tau, tail peak and final tau."""
    dx = _dx()
    res = {}
    cases = {"shipped": (dx.kit_with_sounds("CP"), BASE_HIT, 1.0),
             "variant tail 160ms/0.30, final 30ms, acc 2, later strike":
                 (cp_kit(tail_tau=0.160, tail_peak=0.30, final_tau=0.030), BASE_HIT + DEV_OFFSETS[0], 2.0)}
    for name, (kit, hit, acc) in cases.items():
        full = render_block("CP", kit, hit, acc)
        fast = cp_fast(kit, hit, acc)
        nd = int(np.sum(full != fast))
        res[name] = {"samples": int(len(full)), "differing": nd,
                     "peak": float(np.abs(full).max())}
        print(f"check-fast {name}: {nd} of {len(full)} samples differ", flush=True)
        if nd:
            raise Refused(f"CP fast path differs from the real block on '{name}' ({nd} samples)")
    # and the shipped kit through the fast path IS what the gate ranked
    import run_case as rc
    y, _ = rc.render_drum_solo("CP")
    nd = int(np.sum(y != cp_fast(dx.kit_with_sounds("CP"), BASE_HIT)))
    res["vs run_case.render_drum_solo"] = nd
    if nd:
        raise Refused(f"fast path differs from render_drum_solo('CP') on {nd} samples")
    return res


# ---------------------------------------------------------------------------
# the reference's own tail
# ---------------------------------------------------------------------------
def tail_shape(refs: pathlib.Path) -> dict:
    """20 ms RMS of each take re its own loudest 20 ms, and a two-segment
    log-linear fit of the CP tail. A recording floor is FLAT in dB; a decaying
    signal is not. SD5050 from the same session shows whether the chain adds a
    slow tail to everything."""
    out = {}
    for rel in ("cp8/CP.WAV", "sd8/SD5050.WAV"):
        x, sr = pg.load_wav(refs / rel)
        y = pg.to_rate(x, sr)
        n = int(0.02 * pg.SR)
        m = len(y) // n
        r = 10 * np.log10(np.mean(y[:m * n].reshape(m, n) ** 2, axis=1) + 1e-20)
        r -= r.max()
        t = (np.arange(m) + 0.5) * 0.02
        last100 = float(np.mean(r[-5:]))
        row = {"len_s": len(y) / pg.SR, "rms_db_by_20ms": [round(float(v), 2) for v in r],
               "floor_db_last_100ms": last100}
        if rel.startswith("cp8"):
            fits = {}
            for lo, hi in ((0.08, 0.20), (0.30, 1.20)):
                sel = (t >= lo) & (t <= hi)
                k, c = np.polyfit(t[sel], r[sel], 1)
                res = r[sel] - (k * t[sel] + c)
                fits[f"{lo}-{hi}s"] = {"slope_db_per_s": float(k), "amp_tau_ms": float(-8.686 / k * 1e3),
                                       "rms_resid_db": float(np.sqrt(np.mean(res ** 2)))}
            row["fits"] = fits
            row["margin_over_floor_at_1.2s_db"] = float(r[int(1.2 / 0.02)] - last100)
        out[rel] = row
    return out


def known_answer_tail() -> dict:
    """The two-segment fit on a synthetic two-slope tail with a known answer
    (taus 90 and 270 ms, noise carrier, 16-bit), so the numbers tail_shape
    reports are those of a validated estimator, not of our model."""
    rng = np.random.Generator(np.random.PCG64(1))
    t = np.arange(int(2.0 * pg.SR)) / pg.SR
    env = np.exp(-t / 0.090) + 10 ** (-20 / 20) * np.exp(-t / 0.270)
    x = rng.standard_normal(len(t)) * env * 0.3
    x = np.round(x * 32767) / 32767
    n = int(0.02 * pg.SR)
    m = len(x) // n
    r = 10 * np.log10(np.mean(x[:m * n].reshape(m, n) ** 2, axis=1) + 1e-20)
    tt = (np.arange(m) + 0.5) * 0.02
    sel = (tt >= 0.6) & (tt <= 1.4)
    k, _ = np.polyfit(tt[sel], r[sel], 1)
    got = -8.686 / k * 1e3
    ok = abs(got - 270) / 270 < 0.10
    return {"true_late_tau_ms": 270.0, "fit_ms": float(got), "within_10pct": bool(ok)}


# ---------------------------------------------------------------------------
def _fmt(r: dict) -> str:
    keys = ("spec", "spec_peak", "centroid", "flatness", "impulse", "attack", "decay", "modulation")
    return " ".join(f"{k}={r['ratios'][k]:.2f}" for k in keys if k in r["ratios"])


def cmd_baseline(refs, out):
    dx = _dx()
    res = {"model_sha16": model_sha(), "dev": {}}
    import run_case as rc
    for s in ("CH", "CP"):
        y, sr = rc.render_drum_solo(s)
        g = gate(s, y, sr, refs)
        res[s] = g
        print(f"{s} default strike: worst {g['worst']:.2f} ({g['worst_feature']}) | {_fmt(g)}", flush=True)
        for gr in g["groups"]:
            print(f"   {gr['band']} used={gr['used']} decay={gr['decay']:.2f} attack={gr['attack']:.2f} "
                  f"att_t={np.round(gr['attack_ms_t'], 1)} att_o={np.round(gr['attack_ms_o'], 1)}", flush=True)
    for hit, acc in conditions("dev")[1:]:
        y = cp_fast(dx.kit_with_sounds("CP"), hit, acc)
        g = gate("CP", y, dx.SR, refs)
        res["dev"][f"CP@{hit}"] = g
        print(f"CP DEV strike {hit}: worst {g['worst']:.2f} ({g['worst_feature']}) | {_fmt(g)}", flush=True)
    return res


#: The best point of each CP family on DEV that the frozen rule did NOT select
#: (each regressed another feature). Run on CONFIRM as DIAGNOSTIC evidence of
#: the trade-off only -- never a selection, never promoted.
CP_FRONTIER = ({"family": "P", "tau_ms": 160, "peak_db": -8.0},
               {"family": "X", "tau_ms": 250, "db": -22.0},
               {"family": "T", "f_hz": 1300.0, "tau_ms": 160, "db": -12.0})


def regen() -> int:
    """Every record in docs/scorecard/chcp-559 from ONE clean, committed tree.
    REFUSES on a dirty tools/ or model/. Three independent streams run as
    parallel processes (each sequential inside); every step's own exit status
    is recorded, and the evidence tables are written only if every sweep
    passed. One process per stream, so at most three cores."""
    import subprocess
    p0 = _provenance()
    if p0["sources_dirty"]:
        raise Refused("regen needs a clean tools/ and model/: commit first, so every record names a real commit")
    D = "docs/scorecard/chcp-559"
    # Shared recordings first, in this one process: two streams writing and
    # reading the same cache file at once could read a half-written array.
    import chcp_559_select as sel
    cp_snl(N_REC)
    for f in sel.CPT_F_HZ:
        cpt_tail_tap(f, sel.CPT_Q, sel.CPT_EXC_ATT, N_REC)
    print("shared recordings cached", flush=True)
    me = [sys.executable, str(pathlib.Path(__file__).resolve())]
    conf = lambda snd, name, cand: [*me, "confirm", "--sound", snd, "--sweep", f"{D}/{snd.lower()}-sweep-dev.json",
                                    "--cand", json.dumps(cand), "--out", f"{D}/{snd.lower()}-confirm-{name}.json"]
    streams = {
        "A": [("cp-tail", [*me, "cp-tail", "--out", f"{D}/cp-tail.json"]),
              ("baseline", [*me, "baseline", "--out", f"{D}/baseline.json"]),
              ("cp-sweep", [*me, "cp-sweep", "--out", f"{D}/cp-sweep-dev.json"])]
             + [(f"cp-frontier-{i}", conf("CP", f"frontier-{c['family']}", c)) for i, c in enumerate(CP_FRONTIER[:2])],
        "B": [("cpt-sweep", [*me, "cpt-sweep", "--out", f"{D}/cpt-sweep-dev.json"]),
              ("cpt-frontier", [*me, "confirm", "--sound", "CP", "--sweep", f"{D}/cpt-sweep-dev.json", "--cand",
                                json.dumps(CP_FRONTIER[2]), "--out", f"{D}/cp-confirm-frontier-T.json"])],
        "C": [("ch-sweep", [*me, "ch-sweep", "--out", f"{D}/ch-sweep-dev.json"]),
              ("ch-confirm-hpq05", conf("CH", "hpq05", {"hpq": 0.5, "bpq": 6.0})),
              ("ch-confirm-hpq05-bpq3", conf("CH", "hpq05-bpq3", {"hpq": 0.5, "bpq": 3.0}))],
    }
    drivers = []
    for name, steps in streams.items():
        script = ("import subprocess, sys, json\nres = {}\n"
                  f"for label, cmd in {steps!r}:\n"
                  f"    with open('{D}/' + label + '.log', 'w') as fh:\n"
                  "        r = subprocess.run(cmd, cwd=" + repr(str(ROOT)) + ", stdout=fh, stderr=subprocess.STDOUT)\n"
                  "    res[label] = r.returncode\n"
                  "    print(label, r.returncode, flush=True)\n"
                  "    if r.returncode != 0:\n"
                  "        break\n"
                  "print('STREAM', json.dumps(res), flush=True)\n"
                  "sys.exit(max(res.values()) if res else 2)\n")
        drivers.append((name, subprocess.Popen([sys.executable, "-c", script], cwd=ROOT, stdout=subprocess.PIPE,
                                               text=True)))
    status = {}
    for name, pr in drivers:
        out, _ = pr.communicate()
        status[name] = pr.returncode
        print(f"stream {name} exit {pr.returncode}\n{out}", flush=True)
    if any(status.values()):
        print(f"REGEN INCOMPLETE: {status}; tables not written", flush=True)
        return 1
    r = subprocess.run([*me, "tables"], cwd=ROOT)
    p1 = _provenance()
    if {k: p0[k] for k in p0 if k != "sources_dirty"} != {k: p1[k] for k in p1 if k != "sources_dirty"}:
        print(f"REGEN: sources moved during the run {p0} -> {p1}", flush=True)
        return 1
    print(f"REGEN OK at {p0['commit'][:12]}: streams {status}, tables exit {r.returncode}", flush=True)
    return r.returncode


def _provenance() -> dict:
    import subprocess
    git = lambda *x: subprocess.run(["git", *x], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    probe = hashlib.sha256(b"".join((ROOT / "tools" / "probes" / f).read_bytes()
                                    for f in ("chcp_559.py", "chcp_559_select.py"))).hexdigest()[:16]
    return {"model_sha16": model_sha(), "engine_fingerprint": engine_fingerprint(), "probe_sha16": probe,
            "commit": git("rev-parse", "HEAD"),
            "sources_dirty": bool(git("status", "--porcelain", "--", "tools", "model"))}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=("refs", "baseline", "check-fast", "check-cpt", "cp-tail", "cp-sweep", "cpt-sweep", "ch-sweep", "confirm", "tables", "regen"))
    ap.add_argument("--refs", type=pathlib.Path, default=None)
    ap.add_argument("--sweep", type=pathlib.Path, default=None, help="confirm: the sweep JSON whose selection to confirm")
    ap.add_argument("--sound", choices=("CH", "CP"), default=None)
    ap.add_argument("--cand", default=None,
                    help="confirm: a candidate from the sweep's grid as JSON, in place of its 'selected' "
                         "(used when a shared voice blocks the selection; must be a swept point)")
    ap.add_argument("--out", type=pathlib.Path, default=None)
    a = ap.parse_args(argv)
    refs = a.refs or default_refs()
    if a.cmd == "regen":
        try:
            check_refs(refs)
            return regen()
        except Refused as e:
            print(f"REFUSED: {e}", flush=True)
            return 2
    # Provenance is taken BEFORE any work and checked again after. The first
    # version took it at the end, so two sweeps recorded the model file as it
    # was when they FINISHED, after mid-run edits, not the one they imported
    # (wrong-then-right, #559). A run whose sources moved under it writes a
    # record that says so instead of a clean one.
    prov0 = _provenance()
    try:
        hashes = check_refs(refs)
        if a.cmd == "refs":
            res = {"ok": True}
        elif a.cmd == "baseline":
            res = cmd_baseline(refs, a.out)
        elif a.cmd == "check-fast":
            res = check_fast()
        elif a.cmd == "check-cpt":
            res = check_cpt()
        elif a.cmd == "tables":
            import chcp_559_select as sel
            ch = json.loads((ROOT / "docs/scorecard/chcp-559/ch-sweep-dev.json").read_text())
            cp = json.loads((ROOT / "docs/scorecard/chcp-559/cp-sweep-dev.json").read_text())
            txt = sel.sensitivity_tables(ch, cp)
            outp = ROOT / "docs/sensitivity/chcp559-sweeps.txt"
            outp.write_text(txt)
            print(txt)
            res = {"written": str(outp.relative_to(ROOT))}
        elif a.cmd == "cp-tail":
            ka = known_answer_tail()
            print("known answer:", ka, flush=True)
            if not ka["within_10pct"]:
                raise Refused(f"the tail fit misses its own known answer: {ka}")
            res = {"known_answer": ka, "tail": tail_shape(refs)}
            for rel, row in res["tail"].items():
                print(rel, "floor(last 100 ms)", round(row["floor_db_last_100ms"], 1), row.get("fits"),
                      "margin@1.2s", row.get("margin_over_floor_at_1.2s_db"), flush=True)
        elif a.cmd == "confirm":
            import chcp_559_select as sel
            if not (a.sweep and a.sound):
                raise Refused("confirm needs --sweep <the DEV sweep JSON> and --sound")
            sw = json.loads(a.sweep.read_text())
            chosen = sw["result"].get("selected")
            if a.cand:
                want = json.loads(a.cand)
                swept = [x["cand"] for k in ("candidates", "stage1", "stage2") for x in sw["result"].get(k, [])]
                if want not in swept:
                    raise Refused(f"--cand {want} is not a point the DEV sweep measured")
                chosen = {"cand": want, "override_of": (sw["result"].get("selected") or {}).get("label")}
            if not chosen:
                raise Refused(f"{a.sweep} selected nothing on DEV: there is nothing to confirm")
            if sw.get("model_sha16") != model_sha():
                now = engine_fingerprint()
                then = engine_fingerprint_at(sw["commit"])
                print(f"model file changed since the sweep ({sw.get('model_sha16')} -> {model_sha()}); "
                      f"engine+images fingerprint at {sw['commit'][:12]} {then}, now {now}", flush=True)
                if then != now:
                    raise Refused(f"{a.sweep} was swept on an engine/image that differs from this tree's")
            res = sel.confirm(a.sound, chosen["cand"], refs)
        else:
            import chcp_559_select as sel
            if a.cmd == "cp-sweep":
                check_fast()                  # the fast path is the block, on THIS model, or no sweep
            if a.cmd == "cpt-sweep":
                check_cpt()
            res = sel.run(a.cmd, refs)
    except Refused as e:
        print(f"REFUSED: {e}", flush=True)
        return 2
    prov1 = _provenance()
    moved = {k: [prov0[k], prov1[k]] for k in prov0 if prov0[k] != prov1[k]}
    if moved:
        print(f"WARNING: sources changed during the run: {moved}; the record says so", flush=True)
    res = {"cmd": a.cmd, **prov0, "sources_moved_during_run": moved,
           "refs_sha256": hashes, "conditions": {"dev": conditions("dev"),
           "confirm": conditions("confirm")}, "result": res}
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(pg._r(res), indent=1, default=float) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

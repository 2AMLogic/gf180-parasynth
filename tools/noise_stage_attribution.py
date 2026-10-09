"""Which voice stage sets the MA / RS brightness and noisiness trajectory (#556).

THE QUESTION.  The gate (`tools/perceptual_gate.py`) fails MA on the Bark
centroid trajectory (35.5x) and RS on centroid/flatness (20.6x).  The issue
says: measure which stage of the 808 circuit sets that trajectory BEFORE
changing the model.  This module is that measurement.  It does not fit
anything and it does not touch the model.

WHAT IT DOES.  Renders the shipped model's MA or RS hit, then re-renders with
ONE stage perturbed at a time (stage list below, frozen in this file before
any result was read) and reports how far each perturbation moves the gate's
own centroid and flatness trajectories (`perceptual_gate.analyse` /
`distances`, same frame weights).  A stage "sets" the trajectory only if its
perturbation moves it by more than NOISE_MARGIN x the repeat floor, where the
floor is the distance between two renders that differ ONLY in which noise
realisation the free-running LFSR delivers (hit frame shifted by a few
samples).  A sensitivity below the floor is not attributed to anything.

INDEPENDENT PREDICTION (written before the first measurement, so that the
result can refute it): MA is white noise -> LTI high-pass -> a gate.  If the
gate were linear, the spectrum of the output would be time-invariant and the
envelope (shape, tau, the documented 18.2 ms rise) could not move the CENTROID
at all, only the level.  So the prediction is that MA's centroid trajectory is
set by the nonlinear (swing) stage and the level driving it, NOT by the
envelope.  `prediction_check` reports whether the measurement agrees.

WHAT QUALIFIES THE INSTRUMENT (`qualify`, run before every attribution; also
`tools/test_noise_stage_attribution.py`):
  * a log-swept sine, whose Bark centroid is known from ZWICKER'S closed-form
    z(f) = 13 atan(0.00076 f) + 3.5 atan((f/7500)^2) -- a formula that is not
    in this repository's analyser, which uses its own 25-band edge table;
  * Gaussian white noise, whose periodogram flatness is exp(-gamma) = -2.507
    dB by theory (geometric / arithmetic mean of an exponential variate);
  * a stationary sine, whose flatness must be far below noise;
  * a noise-free control: perturbing a register that nothing reads must read
    as zero, and an identical re-render must read as zero.
If any fails the tool raises Refused: a number from an unqualified analyser
looks exactly like data.

WHAT IT REFUSES (never a number):
  * the perturbation did not change the register image (a no-op ablation
    would read as "this stage does nothing");
  * the render is silent / non-finite / has fewer than MIN_LIVE_FRAMES frames;
  * no stage exceeds the repeat floor (the apparatus cannot see anything);
  * the renderer is not the kit (the `stub` renderer, used to start red).

WHAT IT CANNOT SAY.  It attributes the trajectory in OUR model to OUR model's
stages.  Whether the Fischer recording's trajectory is set by the same stage,
and which direction a repair must move, needs the recording:
`attribute_vs_reference` REFUSES without the corpus, and
docs/ma-rs-stage-attribution-request.md says what the build box must supply.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import math
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tools"), str(ROOT / "model")]
import perceptual_gate as pg  # noqa: E402

ESTIMATOR_ID = "noise_stage_attribution/v1 on perceptual_gate.analyse"

NOISE_MARGIN = 3.0           # a stage is attributed only above 3x the repeat floor
MIN_LIVE_FRAMES = 5
LIVE_DB = 30.0               # frames within 30 dB of the loudest carry a trajectory
REPEAT_SHIFTS = (3, 7)       # hit-frame offsets (samples) -> other LFSR phase
RENDER_S = 0.6               # enough for MA (tau 12 ms) and RS (22 ms gate); the full 2.2 s
                             # span is the gate's, this is for the attribution only
FACTOR = 2.0                 # every continuous stage is perturbed x2 and /2
GLIDE_TOL_BARK = 0.6         # qualification tolerance vs Zwicker z(f)
WHITE_FLAT_DB = -2.507       # exp(-gamma), theory
WHITE_FLAT_TOL = 0.7
SINE_FLAT_MAX = -20.0


class Refused(RuntimeError):
    """A precondition failed.  First-class outcome; never mapped to zero."""


# ---------------------------------------------------------------------------
# the stage list, frozen before any result was read
# ---------------------------------------------------------------------------
# (stage, what it is in the circuit, constant in model/drums_fx.py, kind)
#   kind "x": perturb x2 and /2;  "nl": swing -> linear;  "step": +-1 integer
STAGES = {
    "MA": (
        ("envelope", "R341/C134 gate envelope tau", "MA_TAU", "x"),
        ("highpass_hz", "Q68 Sallen-Key corner", "MA_HP_HZ", "x"),
        ("highpass_q", "Q68 Sallen-Key Q", "MA_HP_Q", "x"),
        ("gate_level", "level into the gate transistor", "PEAK_MA", "x"),
        ("gate_nonlinearity", "swing VCA (Q68 gate) -> linear", "NL_SWING", "nl"),
    ),
    "RS": (
        ("gate_tau", "Q74 ~22 ms gate", "RS_GATE_TAU", "x"),
        ("lo_mode_q", "455 Hz bridged-T decay", "RS_LO_Q", "x"),
        ("hi_mode_q", "1786 Hz bridged-T decay", "RS_HI_Q", "x"),
        ("mode_balance", "pulse into the 455 Hz network", "RS_LO_X_ATT", "step"),
        ("gate_level", "level into the swing VCA", "PEAK_RSG", "x"),
        ("gate_nonlinearity", "swing VCA (Q62) -> linear", "NL_SWING", "nl"),
    ),
}


def _dx():
    import drums_fx as dx
    return dx


@contextlib.contextmanager
def patched(**consts):
    """Set module constants of drums_fx for the duration of one render."""
    dx = _dx()
    for k in consts:
        if not hasattr(dx, k):
            raise Refused(f"drums_fx has no constant {k}: the stage list is stale")
    old = {k: getattr(dx, k) for k in consts}
    try:
        for k, v in consts.items():
            setattr(dx, k, v)
        yield
    finally:
        for k, v in old.items():
            setattr(dx, k, v)


def kit_image(sound: str) -> dict:
    return dict(_dx().kit_with_sounds(sound))


def render_kit(sound: str, hit_frame: int = 480, seconds: float = RENDER_S):
    """The shipped model's hit, through the register interface (same path as
    run_case.render_drum_solo, with a shorter span)."""
    dx = _dx()
    n = int(seconds * dx.SR)
    d = dx.DrumsFx()
    kit = dx.kit_with_sounds(sound)
    dm, bd = d.play(dx.hit_writes([(hit_frame, dx.SOUND_STOP[sound], 1.0)], kit), n)
    g = dx.accent_reg(0.45)
    out = dx.output_fx(np.zeros(n), 0, dm, g, bd, g)
    return np.asarray(out, dtype=np.float64) / 32768.0, dx.SR


def render_stub(sound: str, hit_frame: int = 480, seconds: float = RENDER_S):
    """START RED: right shape, no 808 -- a decaying 1 kHz sine."""
    sr = 48000
    t = np.arange(int(seconds * sr)) / sr
    y = np.concatenate([np.zeros(hit_frame), np.sin(2 * np.pi * 1000 * t) * np.exp(-t / 0.1)])
    return y[: int(seconds * sr)], sr


# ---------------------------------------------------------------------------
# trajectories
# ---------------------------------------------------------------------------
def analyse_signal(x, sr, sound="MA", plan=None):
    if not np.all(np.isfinite(x)):
        raise Refused("non-finite samples")
    try:
        y = pg.condition(x, sr, side="signal")
    except pg.Refused as e:
        raise Refused(str(e)) from e
    if plan is None:
        plan = pg.plan_from_target(y, sound)
    a = pg.analyse(y, plan)
    live = a["loud"] > a["loud"].max() * 10 ** (-LIVE_DB / 10)
    if int(live.sum()) < MIN_LIVE_FRAMES:
        raise Refused(f"only {int(live.sum())} live frames (< {MIN_LIVE_FRAMES})")
    return a, plan, live


def zwicker_bark(f):
    f = np.asarray(f, dtype=float)
    return 13 * np.arctan(0.00076 * f) + 3.5 * np.arctan((f / 7500.0) ** 2)


def trajectory_summary(a, live) -> dict:
    """Level-free description of one trajectory (live frames only)."""
    c, fl = a["centroid"][live], a["flatness"][live]
    return {"live_frames": int(live.sum()),
            "centroid_mean": float(c.mean()), "centroid_range": float(c.max() - c.min()),
            "centroid_first_last": [float(c[0]), float(c[-1])],
            "flatness_mean": float(fl.mean()), "flatness_range": float(fl.max() - fl.min())}


# ---------------------------------------------------------------------------
# qualification against signals whose answer does not come from our model
# ---------------------------------------------------------------------------
def qualify() -> dict:
    sr = 48000
    t = np.arange(int(1.2 * sr)) / sr
    f = 500.0 * (4000.0 / 500.0) ** (t / 1.2)
    sweep = np.sin(2 * np.pi * np.cumsum(f) / sr) * np.minimum(1.0, t / 0.01)
    a, _, _ = analyse_signal(np.concatenate([np.zeros(480), sweep]), sr)
    ks = np.arange(10, 105, 5)                      # 0.1 .. 1.0 s, clear of the edges
    truth = zwicker_bark(500.0 * 8.0 ** (ks * 0.01 / 1.2))
    glide = float(np.max(np.abs(a["centroid"][ks] - truth)))
    rng = np.random.default_rng(1)
    w = rng.standard_normal(sr) * np.minimum(1.0, np.arange(sr) / 480)
    aw, _, _ = analyse_signal(np.concatenate([np.zeros(480), w]), sr)
    white = float(np.mean(aw["flatness"][5:90]))
    st = np.sin(2 * np.pi * 1500.0 * np.arange(sr) / sr) * np.minimum(1.0, np.arange(sr) / 480)
    asn, _, _ = analyse_signal(np.concatenate([np.zeros(480), st]), sr)
    sine = float(np.mean(asn["flatness"][5:90]))
    out = {"glide_worst_bark": round(glide, 3), "white_flatness_db": round(white, 3),
           "sine_flatness_db": round(sine, 2)}
    bad = []
    if not glide < GLIDE_TOL_BARK:
        bad.append(f"swept sine centroid is {glide:.2f} Bark from Zwicker z(f) (> {GLIDE_TOL_BARK})")
    if not abs(white - WHITE_FLAT_DB) < WHITE_FLAT_TOL:
        bad.append(f"white-noise flatness {white:.2f} dB, theory {WHITE_FLAT_DB} dB")
    if not sine < SINE_FLAT_MAX:
        bad.append(f"a stationary sine reads flatness {sine:.1f} dB (> {SINE_FLAT_MAX})")
    if bad:
        raise Refused("estimator fails its known-answer qualification: " + "; ".join(bad))
    return out


# ---------------------------------------------------------------------------
# attribution
# ---------------------------------------------------------------------------
def _perturbations(sound: str):
    """(stage, label, constants-dict) for every perturbation, frozen order."""
    dx = _dx()
    out = []
    for stage, _what, const, kind in STAGES[sound]:
        base = getattr(dx, const)
        if kind == "x":
            out.append((stage, f"{const} x{FACTOR:g}", {const: base * FACTOR}))
            out.append((stage, f"{const} /{FACTOR:g}", {const: base / FACTOR}))
        elif kind == "step":
            out.append((stage, f"{const} +1", {const: base + 1}))
            out.append((stage, f"{const} -1", {const: base - 1}))
        elif kind == "nl":
            out.append((stage, f"{const} -> NL_LIN", {const: dx.NL_LIN}))
    return out


def _dist(base, other, plan):
    d = pg.distances(base, other, plan)
    return {"centroid": d["centroid"], "flatness": d["flatness"]}


def attribute(sound: str, renderer=render_kit, stages=None, shifts=REPEAT_SHIFTS) -> dict:
    """Per-stage sensitivity of the centroid / flatness trajectories, relative
    to the repeat floor.  Raises Refused rather than report on a failed
    precondition."""
    if sound not in STAGES:
        raise Refused(f"{sound}: no frozen stage list (MA, RS)")
    q = qualify()
    x0, sr = renderer(sound)
    base, plan, live = analyse_signal(x0, sr, sound)
    image0 = kit_image(sound) if renderer is render_kit else None
    # repeat floor: same register image, other noise phase
    floor = {"centroid": 0.0, "flatness": 0.0}
    for s in shifts:
        xs, _ = renderer(sound, hit_frame=480 + s)
        a, _, _ = analyse_signal(xs, sr, sound, plan=plan)
        d = _dist(base, a, plan)
        for k in floor:
            floor[k] = max(floor[k], d[k])
    rows = []
    for stage, label, consts in (stages if stages is not None else _perturbations(sound)):
        if renderer is render_kit:
            with patched(**consts):
                if kit_image(sound) == image0:
                    raise Refused(f"perturbation '{label}' left the register image unchanged: "
                                  f"an ablation that changes nothing reads as 'stage inert'")
                xv, _ = renderer(sound)
        else:
            raise Refused("renderer is not the kit: nothing to perturb (the stub starts red)")
        a, _, lv = analyse_signal(xv, sr, sound, plan=plan)
        d = _dist(base, a, plan)
        rows.append({"stage": stage, "perturbation": label, "centroid": d["centroid"],
                     "flatness": d["flatness"], "summary": trajectory_summary(a, lv)})
    by_stage = {}
    for r in rows:
        s = by_stage.setdefault(r["stage"], {"centroid": 0.0, "flatness": 0.0})
        s["centroid"] = max(s["centroid"], r["centroid"])
        s["flatness"] = max(s["flatness"], r["flatness"])
    for s in by_stage.values():
        for k in ("centroid", "flatness"):
            s[f"{k}_x_floor"] = s[k] / max(floor[k], 1e-3)
            s[f"{k}_attributed"] = bool(s[k] > NOISE_MARGIN * max(floor[k], 1e-3))
    if not any(s["centroid_attributed"] or s["flatness_attributed"] for s in by_stage.values()):
        raise Refused("no stage moved either trajectory above the repeat floor: "
                      "the apparatus cannot see this voice's stages")
    rank = {k: sorted(by_stage, key=lambda n: -by_stage[n][k]) for k in ("centroid", "flatness")}
    return {"estimator": ESTIMATOR_ID, "sound": sound, "qualification": q,
            "render_seconds": RENDER_S, "baseline": trajectory_summary(base, live),
            "repeat_floor": floor, "noise_margin": NOISE_MARGIN, "stages": by_stage,
            "perturbations": rows, "ranking": rank}


def prediction_check(result: dict) -> dict:
    """MA only: the pre-registered prediction -- the nonlinear stage / level
    sets the centroid, the envelope does not."""
    if result["sound"] != "MA":
        return {"applies": False}
    s = result["stages"]
    nl = max(s["gate_nonlinearity"]["centroid"], s["gate_level"]["centroid"])
    env = s["envelope"]["centroid"]
    return {"applies": True, "prediction": "centroid: nonlinear stage / gate level > envelope",
            "nl_or_level": nl, "envelope": env, "holds": bool(nl > env)}


# ---------------------------------------------------------------------------
# reference side: REFUSES without the corpus
# ---------------------------------------------------------------------------
def attribute_vs_reference(sound: str, refs: pathlib.Path | None) -> dict:
    """Would rank stages by how far a perturbation moves OUR trajectory toward
    the Fischer take's.  Needs the recording, so it refuses without it."""
    if refs is None or not pathlib.Path(refs).is_dir():
        raise Refused(f"reference corpus not found ({refs}): set GF180_TR808_REFS / --refs. "
                      f"The model-side attribution does not need it; this does.")
    manifest = pathlib.Path(refs) / "manifest.json"
    if not manifest.exists():
        raise Refused(f"{manifest} missing: refusing to read unmanifested audio")
    import drum_verify as dv
    rel = dv.REF_MAIN[sound][0]
    path = pathlib.Path(refs) / rel
    if not path.exists():
        raise Refused(f"Fischer take {path} missing")
    x, sr = pg.load_wav(path)
    ref, plan, live = analyse_signal(x, sr, sound)
    x0, sr0 = render_kit(sound)
    ours, _, _ = analyse_signal(x0, sr0, sound, plan=plan)
    base = _dist(ref, ours, plan)
    rows = []
    for stage, label, consts in _perturbations(sound):
        with patched(**consts):
            xv, _ = render_kit(sound)
        a, _, _ = analyse_signal(xv, sr0, sound, plan=plan)
        d = _dist(ref, a, plan)
        rows.append({"stage": stage, "perturbation": label,
                     "centroid_vs_ref": d["centroid"], "flatness_vs_ref": d["flatness"],
                     "centroid_gain": base["centroid"] - d["centroid"],
                     "flatness_gain": base["flatness"] - d["flatness"]})
    return {"sound": sound, "baseline_vs_ref": base, "perturbations": rows,
            "reference": str(rel)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("sound", choices=sorted(STAGES))
    ap.add_argument("--refs", type=pathlib.Path, default=None)
    ap.add_argument("--renderer", choices=("kit", "stub"), default="kit")
    ap.add_argument("--out", type=pathlib.Path)
    a = ap.parse_args(argv)
    try:
        res = attribute(a.sound, render_kit if a.renderer == "kit" else render_stub)
        res["prediction"] = prediction_check(res)
        if a.refs is not None:
            res["vs_reference"] = attribute_vs_reference(a.sound, a.refs)
    except Refused as e:
        print(f"REFUSED: {e}")
        return 2
    txt = json.dumps(res, indent=1, default=float)
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(txt)
    print(txt)
    return 0


if __name__ == "__main__":
    sys.exit(main())

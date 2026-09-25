#!/usr/bin/env python3
"""F1A/F1B/F1C cutoff response on the SELECTED voice's filter path, beside the
legacy standalone-component baseline, on the same frozen Surge Type 2 clips.

    tools/probes/f1_selected_path.py --json docs/scorecard/f1-baseline/selected-path.json
    tools/probes/f1_selected_path.py --inject WRONG_LADDER_PATH     # must exit 1
    tools/probes/f1_selected_path.py --inject REF_PROFILE_TAMPERED  # must exit 2

WHAT IS MEASURED, AND WHAT IS NOT
---------------------------------
`run_case.our_filter_curve` measures `reference_rigs.OurLadder`, which runs the
ladder at the base rate with the 2x-oversample loop inside it: every input
sample held for two subframes. The selected Mono engine
(`mono_m5a_score.ENGINE_PROFILES["selected"]`) instead builds
`filter_rate_chain.RateConvertedLadder`: causal Kaiser 2x reconstruction,
int32 headroom preserved, the ladder at 96 kHz, causal Kaiser decimation, a
19-bit output word. That is a different signal path, so the legacy number is
not automatically a measurement of it.

This probe adds NO filter. It builds the selected voice with the scorer's own
constructor (`mono_m5a_score._voice_for_engine`), takes that voice's own
`ladder` object and its own g/k ROMs, and computes the registers with the same
host conversion (`VoiceFx.patch_regs`) and the same per-frame lines
`VoiceFx._render` executes (`g_from_cut`, `kc_from_cut`, `k_effective`). The
stimulus is the stepped tone `OurLadder` uses, rounded to int16 the same way,
at the same probe level, resonance 0 and drive 1.0 -- the F1 settings. Only
the path changes.

Three things are checked before any number is reported, and each REFUSES
rather than reports when it fails:

  1. IDENTITY. The ladder under test is a causal, headroom-preserving 2x
     `RateConvertedLadder` with 2x-rate ROMs. Selecting the wrong path (the
     legacy base-rate `LadderFx`) is refused by name.
  2. EXACT MATCH. A real selected-voice note is played (`VoiceFx.play`), its
     integer mixer output and cutoff trace are fed through THIS probe's render
     function, and the g/k register streams and the ladder output words must
     equal the voice's own trace bit for bit. If they do not, the probe is not
     measuring the selected component and it exits 1 with the mismatch count.
  3. REFERENCE. The Surge side comes only from `run_case.load_filter_reference`,
     which refuses a clip whose bytes do not hash to the committed profile.

It also re-reads the LEGACY curves with the rolloff estimator as it stood at
runner hash 531aa8a3731dfdb8 (before #178 moved its corner onto the
DC-extrapolated plateau) and with the current one, on the same arrays, and
checks the old reading reproduces the committed F1 records. That is the
measurement change, separated from any sound change.

Exit: 0 measured, 1 a check failed (a result), 2 refused (no evidence).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "model"))
sys.path.insert(0, str(ROOT / "tools"))

import audio_measure as am           # noqa: E402
import refprofile as rp              # noqa: E402
import run_case as rc                # noqa: E402
import reference_rigs as rr          # noqa: E402
import voice_fx as vf                # noqa: E402
import mono_m5a_score as m5          # noqa: E402
from filter_rate_chain import RateConvertedLadder   # noqa: E402

CASES = ("F1A", "F1B", "F1C")
DRIVE = 1.0          # OurLadder's drive: the F1 stimulus setting, held fixed
INJECTS = ("", "WRONG_LADDER_PATH", "REF_PROFILE_TAMPERED", "REF_PROFILE_MISSING")

#: The committed F1 board records (`docs/scorecard/results/F1*.json`), which name
#: `run_case@531aa8a3731d`: corner by #164's DC-plateau estimator, rolloff by
#: the pre-#178 one. Read at this revision out of git, never transcribed.
SUPERSEDED_AT = "c993946"     # origin/main this baseline was frozen on
SAME = 5e-4          # the records are written to 4 decimals


class Refused(RuntimeError):
    pass


def _sha16(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# the selected path, built by the scorer's own constructor
# ---------------------------------------------------------------------------
def build_voice(profile_name: str):
    prof = m5.engine_configuration(profile_name)
    return prof, m5._voice_for_engine(prof)


def check_identity(voice) -> dict:
    """Refuse unless `voice.ladder` is the selected rate-converted component."""
    lad = voice.ladder
    ident = {"ladder_class": type(lad).__name__,
             "factor": getattr(lad, "factor", None),
             "causal": getattr(lad, "causal", None),
             "preserve_headroom": getattr(lad, "preserve_headroom", None),
             "latency_frames": getattr(lad, "latency_frames", None),
             "out_bits": getattr(lad, "out_bits", getattr(lad, "OB", None)),
             "ladder_cfg": dict(voice.ladder_cfg),
             "g_rom_bits": voice.GB, "k_rom_bits": voice.KB,
             "g_exact": voice.g_exact, "k_comp": voice.k_comp}
    problems = []
    if not isinstance(lad, RateConvertedLadder):
        problems.append(f"ladder is {type(lad).__name__}, not RateConvertedLadder")
    else:
        if lad.factor != 2:
            problems.append(f"rate factor {lad.factor}, selected is 2")
        if not lad.causal:
            problems.append("non-causal rate converter; selected is causal")
        if not lad.preserve_headroom:
            problems.append("reconstruction clipped to int16; selected preserves headroom")
    if voice.ladder_cfg.get("oversample") != 2:
        problems.append(f"g/k ROMs built for {voice.ladder_cfg.get('oversample')}x, selected is 2x")
    if voice.g_exact or not voice.k_comp:
        problems.append("g_exact or k_comp off; selected uses integer ROMs with compensation")
    # The ROMs the voice holds must be the ones its oversample factor implies.
    if not np.array_equal(voice.g_rom, vf.make_g_rom(voice.GB, 2)):
        problems.append("g ROM is not make_g_rom(GROM_BITS, 2)")
    if not np.array_equal(voice.k_rom, vf.make_k_rom(voice.KB, voice.GB, 2)):
        problems.append("k ROM is not make_k_rom(KROM_BITS, GROM_BITS, 2)")
    ident["problems"] = problems
    return ident


def host_regs(cut_hz: float, res: float, drive: float) -> dict:
    """The host conversion (contract 5.5) for a patch at constant cutoff."""
    r = vf.VoiceFx.patch_regs(cutoff=(cut_hz, cut_hz), q=res, drive=drive)
    return {k: r[k] for k in ("cut_lo", "k", "gain", "ogain", "res", "drive")}


def render_path(voice, x_int16: np.ndarray, cut: np.ndarray, regs: dict) -> tuple:
    """`VoiceFx._render` step 6 for a given integer input and cutoff stream:
    the same four calls on the voice's own ROMs and its own ladder object."""
    cut = np.asarray(cut, dtype=np.int64)
    kc = vf.kc_from_cut(cut, voice.k_rom, voice.KB)
    k_eff = vf.k_effective(regs["k"], kc) if voice.k_comp else \
        np.full(len(cut), regs["k"], dtype=np.int64)
    g = vf.g_from_cut(cut, voice.g_rom, voice.GB)
    voice.ladder.reset()
    y = voice.ladder.process(np.asarray(x_int16).astype(np.int16), None, regs["res"],
                             regs["drive"], g_q16=g, k=regs["k"], gain=regs["gain"],
                             ogain=regs["ogain"], k_q14=k_eff)
    return y.astype(np.int64), g, k_eff


def exact_match(selected_voice, probe_voice) -> dict:
    """Play a real note on the selected voice, then push its own integer mixer
    output and cutoff stream through `render_path` on `probe_voice`. Bitwise."""
    selected_voice.reset()
    note = selected_voice.note_on(45, 0.25, cutoff=(250, 4000), q=0.0, drive=DRIVE,
                                  track=0.35)
    selected_voice.run(note)
    tr = selected_voice.trace
    regs = {k: note["regs"][k] for k in ("k", "gain", "ogain", "res", "drive")}
    y, g, k_eff = render_path(probe_voice, tr["mixed"], tr["cut"], regs)
    want = np.asarray(tr["ladder"], dtype=np.int64)
    n_mis = int(np.count_nonzero(y != want))
    return {"frames": int(len(want)),
            "cutoff_hz_range": [int(np.min(tr["cut"])), int(np.max(tr["cut"]))],
            "g_mismatches": int(np.count_nonzero(g != tr["g"])),
            "k_mismatches": int(np.count_nonzero(k_eff != tr["k_eff"])),
            "ladder_mismatches": n_mis,
            "first_mismatch": (int(np.argmax(y != want)) if n_mis else None),
            "ladder_rms_q15": round(float(np.sqrt(np.mean(want.astype(float) ** 2))), 2),
            "ok": n_mis == 0 and np.array_equal(g, tr["g"]) and np.array_equal(k_eff, tr["k_eff"])}


def selected_curve(voice, freqs, cut_hz: float, res: float, amp: float) -> tuple:
    x, parts = rr.tone_train(list(freqs), amp * rr.FS_Q15, 0.06, 0.20)
    xq = np.clip(np.round(x), -32768, 32767).astype(np.int16)
    regs = host_regs(cut_hz, res, DRIVE)
    cut = np.full(len(xq), regs["cut_lo"], dtype=np.int64)
    y, g, k_eff = render_path(voice, xq, cut, regs)
    recon = dict(voice.ladder.last_reconstruction or {})
    out = []
    for i0, nw, f in parts:
        a = am.tone_amplitude(y[i0:i0 + nw].astype(np.float64), f)
        if not a.ok:
            raise Refused(f"selected-path probe refused at {f:.0f} Hz, cutoff {cut_hz:.0f} Hz: "
                          f"{a.reason}")
        out.append(20 * math.log10(max(a.value, 1e-12) / (amp * rr.FS_Q15)))
    info = {"g_q16": int(g[0]), "k_eff_q14": int(k_eff[0]), "regs": regs,
            "reconstruction_would_clip": recon.get("would_clip_count"),
            "reconstruction_max_abs_q15": recon.get("max_abs_q15"),
            "output_max_abs": int(np.max(np.abs(y)))}
    return np.asarray(out), info


# ---------------------------------------------------------------------------
# estimators: current (shipping) and the rolloff as it stood at 531aa8a
# ---------------------------------------------------------------------------
def rolloff_531aa8a(cut_hz: float):
    """`filt_rolloff` before #178, verbatim except for the one line #178
    changed: the corner that places the fit band came from the moving-median
    plateau (`corner_from_curve` with no `ref_db`)."""
    def f(freqs, g):
        rb = rc._ref_band(freqs, cut_hz)
        c = am.corner_from_curve(freqs, g, ref_band=rb)
        if not c.ok:
            return am.Estimate(None, False, "no corner: " + c.reason, c.detail)
        pl = am.plateau_db(freqs, g, rb)
        live = np.asarray(freqs)[np.asarray(g) > pl + rc.STOPBAND_FLOOR_DB]
        top = float(live.max()) if len(live) else 9000.0
        band = (2.2 * c.value, min(7.0 * c.value, 9000.0, top))
        return am.slope_db_oct(freqs, g, band)
    return f


def _v(e):
    return round(float(e.value), 4) if e.ok else None


def read_curve(freqs, g, cut, open_plateau):
    return {"corner_hz": _v(rc.filt_corner(cut)(freqs, g)),
            "lowband_db": _v(rc.filt_lowband_gain(cut, open_plateau)(freqs, g)),
            "rolloff_db_oct": _v(rc.filt_rolloff(cut)(freqs, g)),
            "rolloff_531aa8a_db_oct": _v(rolloff_531aa8a(cut)(freqs, g)),
            "rolloff_band_hz": (rc.filt_rolloff(cut)(freqs, g).detail or {}).get("band_hz")}


def committed_records() -> dict:
    import subprocess
    out = {}
    for cid in CASES:
        r = subprocess.run(["git", "show", f"{SUPERSEDED_AT}:docs/scorecard/results/{cid}.json"],
                           cwd=str(ROOT), capture_output=True, text=True)
        if r.returncode != 0:
            raise Refused(f"cannot read committed {cid} at {SUPERSEDED_AT}")
        out[cid] = json.loads(r.stdout)
    return out


def score(ours: dict, ref: dict) -> dict:
    rows = {}
    for key, tol_of in (("corner_hz", lambda r: abs(r) * 0.10),
                        ("lowband_db", lambda r: 3.0),
                        ("rolloff_db_oct", lambda r: 1.5)):
        o, r = ours[key], ref[key]
        if o is None or r is None:
            rows[key] = {"value": o, "reference": r, "valid": False}
            continue
        t = tol_of(r)
        rows[key] = {"value": o, "reference": r, "error": round(o - r, 4),
                     "tolerance": round(t, 4), "valid": True,
                     "within": abs(o - r) <= t}
    c = rows["corner_hz"]
    if c["valid"]:
        c["error_pct"] = round(100.0 * (c["value"] / c["reference"] - 1.0), 2)
    return rows


def level_sweep(voice, amps=(0.015625, 0.03125, 0.0625, 0.125, 0.25, 0.5)) -> list:
    """The selected path's usable probe LEVEL range: each F1 cutoff at each
    level, read by the current estimators, beside the legacy component at the
    same level and the frozen reference's corner (whose clip exists only at
    the profile's probe level). A refusal is recorded as one."""
    out = []
    for cid in CASES:
        spec = rc.FILTER_CASES[cid]
        ref_f, ref_g, _ = rc.load_filter_reference(spec["ref_clip"])
        ref_c = _v(rc.filt_corner(spec["cut_hz"])(ref_f, ref_g))
        for amp in amps:
            g, info = selected_curve(voice, ref_f, spec["cut_hz"], spec["res_ours"], amp)
            lc = _v(rc.filt_corner(spec["cut_hz"])(
                ref_f, rc.our_filter_curve(ref_f, spec["cut_hz"], spec["res_ours"], amp)))
            c = rc.filt_corner(spec["cut_hz"])(ref_f, g)
            r = rc.filt_rolloff(spec["cut_hz"])(ref_f, g)
            out.append({"case": cid, "amp": amp,
                        "level_dbfs": round(20 * math.log10(amp), 2),
                        "corner_hz": _v(c), "corner_refused": None if c.ok else c.reason,
                        "legacy_corner_hz": lc, "reference_corner_hz_at_probe_level": ref_c,
                        "corner_error_pct_vs_reference": (round(100 * (_v(c) / ref_c - 1), 2)
                                                          if c.ok and ref_c else None),
                        "rolloff_db_oct": _v(r), "rolloff_refused": None if r.ok else r.reason,
                        "reconstruction_would_clip": info["reconstruction_would_clip"],
                        "output_max_abs": info["output_max_abs"]})
            print(f"level {cid} {out[-1]['level_dbfs']:+6.2f} dBFS  corner {out[-1]['corner_hz']} "
                  f"({out[-1]['corner_error_pct_vs_reference']:+.2f} %)  legacy {lc}  "
                  f"rolloff {out[-1]['rolloff_db_oct']}  {r.reason if not r.ok else ''}")
    return out


# ---------------------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", default=None)
    ap.add_argument("--inject", default="", choices=INJECTS)
    ap.add_argument("--level-sweep", action="store_true",
                    help="also read each case at -24/-18/-12/-6 dBFS on the selected path")
    a = ap.parse_args(argv)

    _, selected = build_voice("selected")
    # The probe's own voice: the selected one, unless the control asks for the
    # wrong path to be measured instead.
    probe_prof, probe = build_voice("legacy" if a.inject == "WRONG_LADDER_PATH" else "selected")
    ident = check_identity(probe)
    match = exact_match(selected, probe)
    print(f"engine profile under test: {probe_prof['name']}")
    print(f"identity: {ident['ladder_class']} factor={ident['factor']} causal={ident['causal']} "
          f"headroom={ident['preserve_headroom']} latency={ident['latency_frames']} "
          f"out_bits={ident['out_bits']}")
    print(f"exact match vs selected voice trace: {match['frames']} frames, "
          f"g {match['g_mismatches']} / k {match['k_mismatches']} / "
          f"ladder {match['ladder_mismatches']} mismatches")
    if ident["problems"]:
        print("REFUSED  wrong ladder path: " + "; ".join(ident["problems"]))
        if not match["ok"]:
            print(f"FAIL     and the probe's output differs from the selected component "
                  f"in {match['ladder_mismatches']} of {match['frames']} frames")
        return 2 if match["ok"] else 1
    if not match["ok"]:
        print("FAIL     probe does not reproduce the selected component bit for bit")
        return 1

    try:
        committed = committed_records()
        rows, curves = [], {}
        for cid in CASES:
            spec = rc.FILTER_CASES[cid]
            cut, amp, res = spec["cut_hz"], rp.PROBE_AMP, spec["res_ours"]
            ref_f, ref_g, meta = rc.load_filter_reference(spec["ref_clip"], a.inject)
            open_f, open_g, open_meta = rc.load_filter_reference(spec["ref_open_clip"])
            leg_g = rc.our_filter_curve(ref_f, cut, res, amp)
            leg_open = rc.our_filter_curve(open_f, spec["open_hz"], res, amp)
            sel_g, sel_info = selected_curve(probe, ref_f, cut, res, amp)
            sel_open, sel_open_info = selected_curve(probe, open_f, spec["open_hz"], res, amp)
            rb = rc._ref_band(open_f, cut)
            plateaus = {"reference": am.plateau_db(open_f, open_g, rb),
                        "legacy": am.plateau_db(open_f, leg_open, rb),
                        "selected": am.plateau_db(open_f, sel_open, rb)}
            reads = {"reference": read_curve(ref_f, ref_g, cut, plateaus["reference"]),
                     "legacy": read_curve(ref_f, leg_g, cut, plateaus["legacy"]),
                     "selected": read_curve(ref_f, sel_g, cut, plateaus["selected"])}
            old = committed[cid]["metrics"]
            repro = {
                "legacy_corner": (reads["legacy"]["corner_hz"], old["Corner frequency"]["value"]),
                "legacy_lowband": (reads["legacy"]["lowband_db"], old["low-band gain"]["value"]),
                "legacy_rolloff_531aa8a": (reads["legacy"]["rolloff_531aa8a_db_oct"],
                                           old["rolloff"]["value"]),
                "reference_corner": (reads["reference"]["corner_hz"],
                                     old["Corner frequency"]["reference"]),
                "reference_rolloff_531aa8a": (reads["reference"]["rolloff_531aa8a_db_oct"],
                                              old["rolloff"]["reference"]),
            }
            repro_ok = all(x is not None and abs(x - y) <= SAME for x, y in repro.values())
            open_dev = sel_open - plateaus["selected"]
            leg_open_dev = leg_open - plateaus["legacy"]
            rows.append({
                "case": cid, "commanded_hz": cut, "res": res, "drive": DRIVE,
                "probe_amp": amp, "probe_level_dbfs": rp.PROBE_LEVEL_DBFS,
                "n_probe_tones": len(ref_f),
                "grid_hz": [round(float(ref_f[0]), 2), round(float(ref_f[-1]), 2)],
                "reference_clip": spec["ref_clip"],
                "reference_sha256": meta["sha256"][:16],
                "reference_open_clip": spec["ref_open_clip"],
                "reference_open_sha256": open_meta["sha256"][:16],
                "wide_open_plateau_db": {k: round(float(v), 4) for k, v in plateaus.items()},
                "reads": reads,
                "legacy_vs_reference": score(reads["legacy"], reads["reference"]),
                "selected_vs_reference": score(reads["selected"], reads["reference"]),
                "selected_minus_legacy": {k: (round(reads["selected"][k] - reads["legacy"][k], 4)
                                              if reads["selected"][k] is not None
                                              and reads["legacy"][k] is not None else None)
                                          for k in ("corner_hz", "lowband_db", "rolloff_db_oct")},
                "committed_531aa8a_reproduced": repro_ok,
                "committed_531aa8a_pairs": {k: list(v) for k, v in repro.items()},
                "selected_registers": sel_info,
                "selected_open_registers": sel_open_info,
                "selected_open_deviation_db_max_abs": round(float(np.max(np.abs(open_dev))), 4),
                "legacy_open_deviation_db_max_abs": round(float(np.max(np.abs(leg_open_dev))), 4),
                "selected_open_deviation_db": [round(float(v), 4) for v in open_dev],
            })
            curves[cid] = {"freqs_hz": [float(f) for f in ref_f],
                           "reference_db": [round(float(v), 4) for v in ref_g],
                           "legacy_db": [round(float(v), 4) for v in leg_g],
                           "selected_db": [round(float(v), 4) for v in sel_g]}
    except (rp.Refused, rc.Refused, Refused) as e:
        print(f"REFUSED  {e}")
        return 2

    hdr = (f"{'case':4s} {'cmd':>5s} | {'ref Hz':>8s} {'leg Hz':>8s} {'leg%':>7s} "
           f"{'sel Hz':>8s} {'sel%':>7s} | {'ref r':>7s} {'leg r':>7s} {'sel r':>7s} "
           f"{'leg r@531':>9s} | {'ref g':>6s} {'leg g':>6s} {'sel g':>6s} | repro")
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        R, L, S = r["reads"]["reference"], r["reads"]["legacy"], r["reads"]["selected"]
        print(f"{r['case']:4s} {r['commanded_hz']:5.0f} | {R['corner_hz']:8.2f} "
              f"{L['corner_hz']:8.2f} {r['legacy_vs_reference']['corner_hz']['error_pct']:+7.2f} "
              f"{S['corner_hz']:8.2f} {r['selected_vs_reference']['corner_hz']['error_pct']:+7.2f} | "
              f"{R['rolloff_db_oct']:7.2f} {L['rolloff_db_oct']:7.2f} {S['rolloff_db_oct']:7.2f} "
              f"{L['rolloff_531aa8a_db_oct']:9.2f} | {R['lowband_db']:6.2f} {L['lowband_db']:6.2f} "
              f"{S['lowband_db']:6.2f} | {r['committed_531aa8a_reproduced']}")

    all_repro = all(r["committed_531aa8a_reproduced"] for r in rows)
    sweep = level_sweep(probe) if a.level_sweep else None
    if a.json:
        doc = {
            "what": "F1A-F1C cutoff response: legacy standalone component and the "
                    "SELECTED Mono filter path, both read by the current estimators, "
                    "against frozen Surge Type 2 clips",
            "source_commit": rc.source_commit(),
            "inputs": {p: "sha256:" + _sha16(ROOT / p) for p in (
                "tools/run_case.py", "tools/probes/f1_selected_path.py",
                "tools/mono_m5a_score.py", "model/audio_measure.py",
                "model/reference_rigs.py", "model/voice_fx.py",
                "model/filter_rate_chain.py", "model/fixed.py",
                "refprofile/profile.json", "refprofile/frozen-cache.zip")},
            "selected_engine_profile": probe_prof,
            "selected_identity": ident,
            "exact_match": match,
            "rom": {"GROM_BITS": vf.GROM_BITS, "KROM_BITS": vf.KROM_BITS,
                    "CUT_TRIM": vf.CUT_TRIM, "coefficient_oversample": 2,
                    "g_rom_sha256": hashlib.sha256(probe.g_rom.tobytes()).hexdigest()[:16],
                    "k_rom_sha256": hashlib.sha256(probe.k_rom.tobytes()).hexdigest()[:16],
                    "legacy_g_rom_sha256": hashlib.sha256(rr.G_ROM.tobytes()).hexdigest()[:16],
                    "legacy_k_rom_sha256": hashlib.sha256(rr.K_ROM.tobytes()).hexdigest()[:16]},
            "sr_hz": rp.SR,
            "committed_records_at": SUPERSEDED_AT,
            "committed_531aa8a_reproduced_all": all_repro,
            "rows": rows,
            "level_sweep": sweep,
            "curves": curves,
        }
        p = pathlib.Path(a.json)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(doc, indent=1) + "\n")
        print(f"wrote {p}")
    if not all_repro:
        print("FAIL     today's legacy curves do not reproduce the committed F1 records "
              "under the 531aa8a estimator: the curves moved, so old-vs-new is confounded")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

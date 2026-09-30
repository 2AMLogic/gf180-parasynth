#!/usr/bin/env python3
"""The F1 filter path measured on the RTL, not on the fixed-point model.

`tools/f1_filter_path.py` is the F1 *model* side: `filter_rate_chain`'s causal
2x `RateConvertedLadder` under the frozen `surge-type2-clean-v1` calibration.
Every F1 record on the board was produced by it, so every F1 number carried the
engine label `fixed-model`. This module is the same measurement with the
arithmetic moved into the synthesizable RTL the chip ships:

    x (Q1.15, 48 kHz)
      -> rate_conv_2x   interpolation      rtl-sketch/rate_conv_2x.v
      -> ladder_dp_n    NCH=1, os2x=0      rtl-sketch/ladder_dp_n.v   (96 kHz)
      -> rate_conv_2x   decimation         -> 19-bit Q4.15 at 48 kHz

composed by `rtl-sketch/tb_f1_chain.v` exactly as `voice_dp.v` composes them
under `VOICE_FILTER_2X`, compiled by iverilog and run by vvp. Every dB in a
curve returned here is projected from words that came out of vvp.

WHY A BENCH AND NOT `synth_top`. `M5A`'s `integrated-rtl` anchor drives SPI
pins and decodes I2S, because a mono note is something the chip can be asked
to play. An F1 case is a transfer function: it needs a stepped tone train to
enter the FILTER, and `synth_top` has no audio input -- its filter is fed by
the oscillator mixer. The RTL under test here is therefore the filter path's
own modules, unmodified, wired as the voice wires them.

PRECONDITIONS, ASSERTED AT THE POINT OF USE (each REFUSES, never reports):

  * iverilog and vvp are on PATH (or under $OSS_CAD_SUITE);
  * the model path passes its own identity/calibration/exact-match checks
    (`f1_filter_path.SelectedFilterPath`) -- a legacy substitute is refused by
    name, so the thing the RTL is compared against is the selected path;
  * the model's ladder configuration is the one this bench instantiates
    (24-bit state, 20 fraction bits, 16-entry interpolated tanh, 19-bit out);
  * the words entering the model's inner ladder are the host image's
    (`VoiceFx.patch_regs`) and are constant over the curve, because the bench
    drives one coefficient set per run and a varying one would be silently
    mis-driven;
  * vvp produced exactly one output frame per input frame, none undefined.

WHAT IS *NOT* A REFUSAL: the RTL disagreeing with the model. That is the
finding this instrument exists to be able to make, so a mismatch is counted,
located and reported, and the metrics are still projected from the RTL words.
`tools/score_f1_rtl.py` puts both readings on the record.

Controls: `--inject` compiles a defect into the RTL and must turn the
model-vs-RTL comparison red. `tools/score_f1_rtl.py --inject ...` refuses to
write a board record, so a control cannot be mistaken for evidence. Which
injections the F1 stimulus reaches was measured rather than assumed, and the
answer is on `INJECTS_NOT_EXERCISED`: at resonance 0 and -12 dBFS the ladder's
feedback, saturation and tanh clamp are all idle, so only the two composition
controls in the bench can fire.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import pathlib
import shutil
import subprocess
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
for _p in (ROOT / "model", ROOT / "tools", ROOT / "tools" / "probes"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import audio_measure as am           # noqa: E402
import reference_rigs as rr          # noqa: E402
import voice_fx as vf                # noqa: E402
import f1_filter_path as fp          # noqa: E402
import f1_selected_path as sp        # noqa: E402

#: Every record this module contributes to names this engine.
ENGINE = "integrated-rtl"
CHAIN_VERSION = "f1-rtl-filter-chain-v1"

RTL_DIR = ROOT / "rtl-sketch"
BENCH = "tb_f1_chain.v"
#: The sources whose bytes decide what the numbers mean; hashed onto the record.
RTL_SOURCES = ("rtl-sketch/tb_f1_chain.v", "rtl-sketch/rate_conv_2x.v",
               "rtl-sketch/ladder_dp_n.v", "rtl-sketch/tanh16.hex",
               "tools/f1_rtl_filter_path.py", "tools/f1_filter_path.py",
               "tools/probes/f1_selected_path.py", "model/filter_rate_chain.py",
               "model/fixed.py")

#: `rate_conv_2x.v` clamps its interpolated word to +/-65535 (17-bit path). The
#: model's causal reconstruction clamps only at int32. Above this the two are
#: allowed to differ, so the margin is measured and recorded rather than assumed.
INTERP_CLAMP = 65535
#: The ladder configuration `tb_f1_chain.v` instantiates. The model must match.
EXPECTED_LADDER_CFG = {"state_bits": 24, "state_q": 20, "tanh_entries": 16,
                       "interp": True, "out_bits": 19}
TANH_ROM = {16: "tanh16.hex", 256: "tanh256.hex"}
TANH_LOG2N = {16: 4, 256: 8}
#: The RTL defects that can be compiled in.
INJECTS = ("LADDER_FB", "LADDER_SAT", "LADDER_TANH_CLAMP", "RATE_CONV_2X_CLAMP",
           "F1_CHAIN_SKIP_INTERP", "F1_CHAIN_DROP_DECIM")
#: Which of those the F1 stimulus actually reaches -- MEASURED (2026-09-26, 30,000
#: frames of F1A's train at cutoff 250 Hz), not assumed, and the answer was a
#: surprise worth carrying on the record: only the two composition controls fire.
#: F1A/F1B/F1C command resonance 0, so `k_eff` is 0 and the ladder's feedback term
#: is multiplied by zero -- `LADDER_FB` (which changes the half-sample average to a
#: unit delay) cannot change a single word. The probe sits at -12 dBFS, so the
#: state never reaches 4.0 (`LADDER_TANH_CLAMP` idle), never saturates
#: (`LADDER_SAT` idle) and the interpolated word never passes +/-32767
#: (`RATE_CONV_2X_CLAMP` idle). That is a statement about the coverage of the
#: Filters family, not about the comparison: the two bench controls below change
#: every frame of every F1 curve, so the comparison demonstrably can turn red.
INJECTS_EXERCISED = ("F1_CHAIN_SKIP_INTERP", "F1_CHAIN_DROP_DECIM")
INJECTS_NOT_EXERCISED = {
    "LADDER_FB": "resonance 0 makes k_eff 0, so the feedback term is multiplied by zero",
    "LADDER_SAT": "the -12 dBFS probe never drives the ladder state to a rail",
    "LADDER_TANH_CLAMP": "|state| stays below 4.0, so the tanh clamp never engages",
    "RATE_CONV_2X_CLAMP": "the interpolated word never passes +/-32767",
}
#: The stepped-tone shape the F1 cases use (`f1_filter_path.SelectedFilterPath.curve`).
SETTLE_S, WINDOW_S = 0.06, 0.20


class Refused(RuntimeError):
    """The apparatus cannot answer. Never a measurement."""


def _tool(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    suite = os.environ.get("OSS_CAD_SUITE")
    if suite and os.path.exists(os.path.join(suite, "bin", name)):
        return os.path.join(suite, "bin", name)
    return None


def _tool_version(binary: str) -> str:
    try:
        out = subprocess.run([binary, "-V"], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:      # pragma: no cover - tool-specific
        return f"unknown ({exc})"
    first = (out.stdout or out.stderr).strip().splitlines()
    return first[0].strip() if first else "unknown"


def _file_sha(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def read_rtl_output(path: pathlib.Path, frames: int, name: str = "tb_f1_chain") -> np.ndarray:
    """One 19-bit word per input frame, or REFUSE. `x` is never a number."""
    try:
        tokens = path.read_text().split()
    except OSError as exc:
        raise Refused(f"{name}: no RTL output at {path}: {exc}") from exc
    if len(tokens) != frames:
        raise Refused(f"{name}: RTL produced {len(tokens)} samples for {frames} input "
                      f"frames -- it did not run to completion")
    out = np.empty(frames, dtype=np.int64)
    for i, token in enumerate(tokens):
        try:
            value = int(token)
        except ValueError as exc:
            raise Refused(f"{name}: RTL sample {i} is undefined ({token!r})") from exc
        if not -(1 << 18) <= value < (1 << 18):
            raise Refused(f"{name}: RTL sample {i} = {value} is outside the 19-bit output word")
        out[i] = value
    return out


def compare_streams(model: np.ndarray, rtl: np.ndarray) -> dict:
    """Sample-for-sample, no tolerance. A disagreement is a result, not a refusal."""
    diff = rtl.astype(np.int64) - model.astype(np.int64)
    nz = np.flatnonzero(diff)
    return {"frames": int(len(model)),
            "mismatches": int(len(nz)),
            "first_mismatch_frame": int(nz[0]) if len(nz) else None,
            "first_mismatch_model": int(model[nz[0]]) if len(nz) else None,
            "first_mismatch_rtl": int(rtl[nz[0]]) if len(nz) else None,
            "max_abs_error_lsb": int(np.max(np.abs(diff))) if len(nz) else 0,
            "bit_exact": bool(len(nz) == 0),
            "model_sha256": hashlib.sha256(model.astype(np.int64).tobytes()).hexdigest()[:16],
            "rtl_sha256": hashlib.sha256(rtl.astype(np.int64).tobytes()).hexdigest()[:16]}


class RtlFilterChainPath:
    """Drop-in for `f1_filter_path.SelectedFilterPath` whose `curve` is measured
    on the RTL. Same constructor checks, same registers, same stimulus, same
    projection; only the arithmetic moves."""

    engine = ENGINE
    path_version = CHAIN_VERSION
    render_label = (f"RTL filter chain {CHAIN_VERSION}: rate_conv_2x.v + ladder_dp_n.v "
                    f"(NCH=1, os2x=0, OW=19) under iverilog, composed by "
                    f"rtl-sketch/tb_f1_chain.v as voice_dp.v composes them")

    def __init__(self, profile: str = fp.ENGINE_PROFILE,
                 calibration: str | None = fp.CALIBRATION, *,
                 substitute_profile: str | None = None,
                 inject: str | None = None,
                 frames: int | None = None,
                 build_dir: pathlib.Path | None = None,
                 timeout_s: float = 14400.0):
        if inject is not None and inject not in INJECTS:
            raise Refused(f"unknown RTL injection {inject!r}; known: {', '.join(INJECTS)}")
        # The model side, with all of its own refusals (identity, calibration,
        # bit-exact reproduction of a real selected-voice note).
        try:
            self.model = fp.SelectedFilterPath(profile, calibration,
                                               substitute_profile=substitute_profile)
        except fp.Refused as exc:
            raise Refused(f"model reference path: {exc}") from exc
        self.calibration = self.model.calibration
        self.profile = self.model.profile
        self.probe_profile = self.model.probe_profile
        self.voice = self.model.voice
        self.words = self.model.words
        self.legacy_words = self.model.legacy_words
        self.match = self.model.match
        self.inject = inject
        self.frames_limit = int(frames) if frames else None
        self.timeout_s = float(timeout_s)
        # One directory per (injection, process). Two runs sharing a vector file
        # is not a hypothetical: the first control batch here ran four injections
        # two at a time, they overwrote each other's vectors, and two of them came
        # back as truncated output -- a refusal that looked like an RTL defect.
        self.build = (pathlib.Path(build_dir) if build_dir else
                      RTL_DIR / "build" / "f1-rtl" / f"{inject or 'clean'}-{os.getpid()}")
        self.build.mkdir(parents=True, exist_ok=True)

        cfg = dict(self.voice.ladder_cfg)
        got = {key: cfg.get(key) for key in EXPECTED_LADDER_CFG}
        if got != EXPECTED_LADDER_CFG:
            raise Refused(f"the model's ladder configuration {got} is not the one "
                          f"tb_f1_chain.v instantiates {EXPECTED_LADDER_CFG}")
        self.tanh_entries = int(cfg["tanh_entries"])
        self.out_bits = int(cfg["out_bits"])

        self.iverilog = _tool("iverilog")
        self.vvp = _tool("vvp")
        if not self.iverilog or not self.vvp:
            raise Refused("iverilog/vvp are not on PATH (or set $OSS_CAD_SUITE): "
                          "no RTL measurement is possible on this host")
        self.tools = {"iverilog": _tool_version(self.iverilog),
                      "vvp": _tool_version(self.vvp)}
        self.defines = [f"INJECT_BUG_{inject}"] if inject else []
        self.vvp_image = None
        self.curves: list[dict] = []

    # -- the RTL ------------------------------------------------------------
    def compile(self) -> pathlib.Path:
        if self.vvp_image is not None:
            return self.vvp_image
        image = self.build / ("tb_f1_chain" + (f"-{self.inject}" if self.inject else "") + ".vvp")
        cmd = [self.iverilog, "-g2012", "-o", str(image),
               f"-Ptb_f1_chain.LOG2N={TANH_LOG2N[self.tanh_entries]}",
               f'-Ptb_f1_chain.ROM_FILE="{TANH_ROM[self.tanh_entries]}"',
               f"-Ptb_f1_chain.OW={self.out_bits}"]
        cmd += [f"-D{d}" for d in self.defines]
        cmd += [BENCH, "rate_conv_2x.v", "ladder_dp_n.v"]
        run = subprocess.run(cmd, cwd=RTL_DIR, capture_output=True, text=True)
        if run.returncode != 0:
            raise Refused("iverilog failed to build tb_f1_chain: "
                          + (run.stdout + run.stderr).strip()[:2000])
        self.vvp_image = image
        self.compile_command = " ".join(cmd)
        return image

    def simulate(self, xq: np.ndarray, regs: dict, tag: str) -> np.ndarray:
        image = self.compile()
        vec = self.build / f"f1-chain-{tag}-in.txt"
        out = self.build / f"f1-chain-{tag}-out.txt"
        vec.write_text("".join(f"{int(v)}\n" for v in xq))
        if out.exists():
            out.unlink()
        cmd = [self.vvp, "-n", str(image), f"+vec={vec}", f"+out={out}",
               f"+g={int(regs['g'])}", f"+k={int(regs['k_eff'])}",
               f"+gain={int(regs['gain'])}", f"+ogain={int(regs['ogain'])}"]
        try:
            run = subprocess.run(cmd, cwd=RTL_DIR, capture_output=True, text=True,
                                 timeout=self.timeout_s)
        except subprocess.TimeoutExpired as exc:
            raise Refused(f"vvp timed out after {self.timeout_s:.0f}s on {tag}") from exc
        report = (run.stdout + run.stderr).strip()
        if run.returncode != 0:
            raise Refused(f"vvp failed on {tag} (exit {run.returncode}): {report[:2000]}")
        if "REFUSED" in report or "TIMEOUT" in report:
            raise Refused(f"tb_f1_chain refused on {tag}: {report[:2000]}")
        self.last_sim_report = report
        self.last_sim_command = " ".join(cmd)
        return read_rtl_output(out, len(xq))

    # -- the measurement ----------------------------------------------------
    def stimulus(self, freqs, amp_q15: float) -> tuple[np.ndarray, list]:
        """The F1 stepped tone, rounded to int16 exactly as the model path does."""
        x, parts = rr.tone_train(list(freqs), amp_q15, SETTLE_S, WINDOW_S)
        xq = np.clip(np.round(x), -32768, 32767).astype(np.int16)
        if self.frames_limit:
            xq = xq[:self.frames_limit]
            parts = [(i0, nw, f) for i0, nw, f in parts if i0 + nw <= len(xq)]
            if not parts:
                raise Refused(f"--frames {self.frames_limit} is shorter than the first "
                              f"analysis window; nothing could be projected")
        return xq, parts

    def curve(self, freqs, cut_hz: float, res: float, amp: float) -> tuple:
        """The F1 curve in dB, projected from RTL words. Same signature as
        `f1_filter_path.SelectedFilterPath.curve`."""
        amp_q15 = amp * rr.FS_Q15
        xq, parts = self.stimulus(freqs, amp_q15)
        regs = fp.host_regs(cut_hz, res, self.calibration)
        if regs["filter_calibration"] != self.calibration:
            raise Refused(f"host image names calibration {regs['filter_calibration']!r}, "
                          f"not {self.calibration!r}")
        cut = np.full(len(xq), regs["cut_lo"], dtype=np.int64)

        # The model twin, on the same words, through the path's own render.
        with fp.words_entering(self.voice) as seen:
            y_model, g, k_eff = sp.render_path(self.voice, xq, cut, regs)
        want = (regs["gain"], regs["ogain"])
        if seen != {want}:
            raise Refused(f"words entering the model ladder {sorted(seen)} are not the "
                          f"host image's {want}")
        if self.calibration is not None and want == self.legacy_words:
            raise Refused(f"{self.calibration}: entering words equal the legacy words")
        g_set, k_set = set(int(v) for v in g), set(int(v) for v in k_eff)
        if len(g_set) != 1 or len(k_set) != 1:
            raise Refused("tb_f1_chain drives one coefficient set per run, but this curve "
                          f"asks for {len(g_set)} g and {len(k_set)} k values")
        words = {"g": g_set.pop(), "k_eff": k_set.pop(),
                 "gain": int(regs["gain"]), "ogain": int(regs["ogain"])}

        recon = dict(self.voice.ladder.last_reconstruction or {})
        recon_max = int(recon.get("max_abs_q15") or 0)
        tag = f"cut{int(round(cut_hz))}-res{res}".replace(".", "p")
        y_rtl = self.simulate(xq, words, tag)
        agree = compare_streams(np.asarray(y_model, dtype=np.int64), y_rtl)

        out = []
        for i0, nw, f in parts:
            a = am.tone_amplitude(y_rtl[i0:i0 + nw].astype(np.float64), f)
            if not a.ok:
                raise Refused(f"RTL filter-chain probe refused at {f:.0f} Hz, cutoff "
                              f"{cut_hz:.0f} Hz: {a.reason}")
            out.append(20 * math.log10(max(a.value, 1e-12) / amp_q15))

        info = {"engine": ENGINE, "chain_version": CHAIN_VERSION,
                "regs": regs, "words_driven": words,
                "words_entering": sorted(seen),
                "g_q16": words["g"], "k_eff_q14": words["k_eff"],
                "frames": int(len(xq)), "n_tones": len(parts),
                "reconstruction_would_clip": recon.get("would_clip_count"),
                "reconstruction_max_abs_q15": recon_max,
                "interp_clamp_q15": INTERP_CLAMP,
                "reconstruction_within_rtl_interp_clamp": bool(recon_max <= INTERP_CLAMP),
                "output_max_abs": int(np.max(np.abs(y_rtl))),
                "output_sha256": agree["rtl_sha256"],
                "model_output_sha256": agree["model_sha256"],
                "rtl_vs_model": agree,
                "rtl_injection": self.inject,
                "simulator": "iverilog",
                "simulator_versions": self.tools,
                "compile_command": self.compile_command,
                "simulate_command": self.last_sim_command,
                "simulator_report": self.last_sim_report}
        self.curves.append({"tag": tag, "cut_hz": cut_hz, "res": res} | info)
        return np.asarray(out, dtype=np.float64), info

    # -- provenance ---------------------------------------------------------
    def record(self) -> dict:
        base = self.model.record()
        base["engine"] = ENGINE
        base["rtl_chain"] = {
            "chain_version": CHAIN_VERSION,
            "bench": "rtl-sketch/tb_f1_chain.v",
            "modules": ["rtl-sketch/rate_conv_2x.v (interpolation and decimation)",
                        "rtl-sketch/ladder_dp_n.v (NCH=1, os2x=0, OW=19)"],
            "wired_as": ("voice_dp.v VOICE_FILTER_2X filter block: interp -> ladder "
                         "subframe 0 -> ladder subframe 1 -> decimate"),
            "tanh_rom": TANH_ROM[self.tanh_entries],
            "ladder_cfg": EXPECTED_LADDER_CFG,
            "simulator": "iverilog",
            "simulator_versions": self.tools,
            "rtl_injection": self.inject,
            "frames_limit": self.frames_limit,
            "source_sha256": {rel: _file_sha(ROOT / rel) for rel in RTL_SOURCES},
            "curves": [{k: v for k, v in c.items() if k != "simulator_report"}
                       for c in self.curves],
            "bit_exact_against_model": all(c["rtl_vs_model"]["bit_exact"] for c in self.curves)
                                       if self.curves else None,
        }
        return base


def main(argv=None) -> int:
    """A standalone smoke/control run: one curve, model versus RTL."""
    ap = argparse.ArgumentParser(description="drive the F1 filter chain in RTL and compare "
                                             "it with the fixed-point model")
    ap.add_argument("--cut-hz", type=float, default=250.0)
    ap.add_argument("--res", type=float, default=0.0)
    ap.add_argument("--frames", type=int, default=20000,
                    help="truncate the stepped-tone stimulus (0 = the whole F1 train)")
    ap.add_argument("--inject", choices=INJECTS, default=None,
                    help="compile a defect into the RTL; the comparison must turn red")
    ap.add_argument("--expect-mismatch", action="store_true",
                    help="exit 0 only when the RTL differs from the model (control mode)")
    ap.add_argument("--json", default=None)
    a = ap.parse_args(argv)
    sys.path.insert(0, str(ROOT / "tools"))
    import refprofile as rp
    try:
        path = RtlFilterChainPath(inject=a.inject, frames=a.frames or None)
        freqs = np.asarray(_profile_freqs(), dtype=np.float64)
        db, info = path.curve(freqs, a.cut_hz, a.res, rp.PROBE_AMP)
    except Refused as exc:
        print(f"f1_rtl_filter_path: REFUSED -- {exc}")
        return 2
    agree = info["rtl_vs_model"]
    print(f"f1_rtl_filter_path: {agree['frames']} frames, {info['n_tones']} tones, "
          f"cut {a.cut_hz:.0f} Hz, words g={info['g_q16']} k={info['k_eff_q14']} "
          f"gain={info['regs']['gain']} ogain={info['regs']['ogain']}")
    print(f"  RTL vs model: {'BIT-EXACT' if agree['bit_exact'] else 'DIFFERS'} "
          f"({agree['mismatches']} of {agree['frames']} frames, worst "
          f"{agree['max_abs_error_lsb']} LSB, first at {agree['first_mismatch_frame']})")
    print("  RTL response dB: " + ", ".join(f"{v:+.2f}" for v in db[:8])
          + (" ..." if len(db) > 8 else ""))
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps(
            {"cut_hz": a.cut_hz, "res": a.res, "inject": a.inject,
             "response_db": [float(v) for v in db],
             "info": {k: v for k, v in info.items() if k != "simulator_report"}},
            indent=2) + "\n")
    if a.expect_mismatch:
        if agree["bit_exact"]:
            print("f1_rtl_filter_path: CONTROL NOT CAUGHT -- the injected RTL defect "
                  "left the comparison bit-exact")
            return 1
        print("f1_rtl_filter_path: control fired (the injected RTL defect was caught)")
        return 0
    return 0 if agree["bit_exact"] else 1


def _profile_freqs() -> list[float]:
    """The frozen probe grid, from the clip metadata the F1 cases use."""
    import run_case as rc
    _, _, meta = rc.load_filter_reference("surge-type2/lp-cut250-res0.00")
    return [float(f) for f in meta["freqs_hz"]]


if __name__ == "__main__":
    raise SystemExit(main())

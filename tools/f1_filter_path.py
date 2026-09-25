#!/usr/bin/env python3
"""The F1 model side, as the official runner measures it (plan074 C).

F1A-F1C request the same stepped tone through two filter paths. Before this
module the runner's side was `reference_rigs.OurLadder`, the legacy base-rate
standalone component with the global gain/ogain words. That is not the filter
the selected Mono engine ships, and it cannot carry a calibration. This module
is the selected path WITH a named calibration, and nothing else:

  * the voice is built by the M5A/M5B scorer's own constructor from the named
    engine profile (`mono_m5a_score.engine_configuration`), and its identity is
    checked (`f1_selected_path.check_identity`): a causal, headroom-preserving
    2x `RateConvertedLadder` on 2x-rate ROMs. A legacy substitute is REFUSED by
    name, however close its numbers;
  * the registers come from the production host conversion
    (`VoiceFx.patch_regs(..., filter_calibration=...)`), never from a probe's
    own arithmetic;
  * the words that actually ENTER the inner ladder's `process` call are
    recorded and must equal the calibration's words (and, for a calibration,
    differ from the legacy words) or the path REFUSES;
  * a real selected-voice note, played with the same calibration, is pushed
    through the same render function and must match the voice's own trace bit
    for bit (g, k_eff and every ladder output word) or the path REFUSES.

Nothing here fits anything. The calibration is frozen in `voice_fx`.
"""
from __future__ import annotations

import contextlib
import hashlib
import math
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
for _p in (ROOT / "model", ROOT / "tools", ROOT / "tools" / "probes"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import audio_measure as am           # noqa: E402
import reference_rigs as rr          # noqa: E402
import voice_fx as vf                # noqa: E402
import mono_m5a_score as m5          # noqa: E402
import f1_selected_path as sp        # noqa: E402

#: What the official F1 cases measure. Changing either is a new result series.
ENGINE_PROFILE = "selected"
CALIBRATION = "surge-type2-clean-v1"
DRIVE = 1.0          # the F1 stimulus setting
PATH_VERSION = "f1-filter-path-v1"


class Refused(RuntimeError):
    pass


@contextlib.contextmanager
def words_entering(voice):
    """Record (gain, ogain) at the INNER ladder's process call. Changes nothing."""
    inner = voice.ladder._ladder if hasattr(voice.ladder, "_ladder") else voice.ladder
    seen = set()
    orig = inner.process

    def proc(*a, **kw):
        seen.add((int(kw["gain"]), int(kw["ogain"])))
        return orig(*a, **kw)

    inner.process = proc
    try:
        yield seen
    finally:
        del inner.process


def host_regs(cut_hz: float, res: float, calibration: str | None) -> dict:
    kw = {} if calibration is None else {"filter_calibration": calibration}
    r = vf.VoiceFx.patch_regs(cutoff=(cut_hz, cut_hz), q=res, drive=DRIVE, **kw)
    return {k: r[k] for k in ("cut_lo", "k", "gain", "ogain", "res", "drive")} | {
        "filter_calibration": r.get("filter_calibration")}


class SelectedFilterPath:
    """Build once per case; `curve` per condition."""

    def __init__(self, profile: str = ENGINE_PROFILE, calibration: str | None = CALIBRATION,
                 *, substitute_profile: str | None = None):
        self.calibration = calibration
        self.profile = m5.engine_configuration(profile)
        # the probe's own voice: the profile named, unless a control asks for a
        # substitute to be measured under the selected label
        self.probe_profile = m5.engine_configuration(substitute_profile or profile)
        self.voice = m5._voice_for_engine(self.probe_profile)
        self.identity = sp.check_identity(self.voice)
        if self.identity["problems"]:
            raise Refused("F1 model path is not the selected filter: "
                          + "; ".join(self.identity["problems"]))
        self.legacy_words = tuple(vf.ladder_regs(0.0, DRIVE)[1:])
        self.words = tuple(vf.ladder_regs(0.0, DRIVE, calibration)[1:])
        if calibration is not None and self.words == self.legacy_words:
            raise Refused(f"{calibration}: words {self.words} equal the legacy words")
        self.match = self._exact_match()

    def _exact_match(self) -> dict:
        """A real selected-voice note under the calibration, through `render`."""
        ref_voice = m5._voice_for_engine(self.profile)
        ref_voice.reset()
        kw = {} if self.calibration is None else {"filter_calibration": self.calibration}
        note = ref_voice.note_on(45, 0.25, cutoff=(250, 4000), q=0.0, drive=DRIVE,
                                 track=0.35, **kw)
        with words_entering(ref_voice) as voice_words:
            ref_voice.run(note)
        tr = ref_voice.trace
        regs = {k: note["regs"][k] for k in ("k", "gain", "ogain", "res", "drive")}
        with words_entering(self.voice) as probe_words:
            y, g, k_eff = sp.render_path(self.voice, tr["mixed"], tr["cut"], regs)
        want = np.asarray(tr["ladder"], dtype=np.int64)
        n_mis = int(np.count_nonzero(y != want))
        out = {"frames": int(len(want)),
               "calibration_in_image": note["regs"].get("filter_calibration"),
               "voice_words_entering": sorted(voice_words),
               "probe_words_entering": sorted(probe_words),
               "g_mismatches": int(np.count_nonzero(g != tr["g"])),
               "k_mismatches": int(np.count_nonzero(k_eff != tr["k_eff"])),
               "ladder_mismatches": n_mis,
               "ladder_sha256": hashlib.sha256(want.tobytes()).hexdigest()[:16]}
        out["ok"] = (n_mis == 0 and out["g_mismatches"] == 0 and out["k_mismatches"] == 0
                     and voice_words == probe_words == {self.words})
        if not out["ok"]:
            raise Refused(f"F1 model path does not reproduce the selected voice under "
                          f"{self.calibration}: {out}")
        return out

    def curve(self, freqs, cut_hz: float, res: float, amp: float) -> tuple:
        x, parts = rr.tone_train(list(freqs), amp * rr.FS_Q15, 0.06, 0.20)
        xq = np.clip(np.round(x), -32768, 32767).astype(np.int16)
        regs = host_regs(cut_hz, res, self.calibration)
        if regs["filter_calibration"] != self.calibration:
            raise Refused(f"host image names calibration {regs['filter_calibration']!r}, "
                          f"not {self.calibration!r}")
        cut = np.full(len(xq), regs["cut_lo"], dtype=np.int64)
        with words_entering(self.voice) as seen:
            y, g, k_eff = sp.render_path(self.voice, xq, cut, regs)
        want = (regs["gain"], regs["ogain"])
        if seen != {want}:
            raise Refused(f"words entering the ladder {sorted(seen)} are not the host "
                          f"image's {want}")
        if self.calibration is not None and want == self.legacy_words:
            raise Refused(f"{self.calibration}: entering words equal the legacy words")
        recon = dict(self.voice.ladder.last_reconstruction or {})
        out = []
        for i0, nw, f in parts:
            a = am.tone_amplitude(y[i0:i0 + nw].astype(np.float64), f)
            if not a.ok:
                raise Refused(f"selected-path probe refused at {f:.0f} Hz, cutoff "
                              f"{cut_hz:.0f} Hz: {a.reason}")
            out.append(20 * math.log10(max(a.value, 1e-12) / (amp * rr.FS_Q15)))
        info = {"regs": regs, "words_entering": sorted(seen), "g_q16": int(g[0]),
                "k_eff_q14": int(k_eff[0]),
                "reconstruction_would_clip": recon.get("would_clip_count"),
                "output_max_abs": int(np.max(np.abs(y))),
                "output_sha256": hashlib.sha256(y.astype(np.int64).tobytes()).hexdigest()[:16]}
        return np.asarray(out, dtype=np.float64), info

    def record(self) -> dict:
        v = self.voice
        return {"path_version": PATH_VERSION,
                "engine_profile": self.probe_profile["name"],
                "filter_calibration": self.calibration,
                "calibration_definition": (vf.FILTER_CALIBRATIONS.get(self.calibration)
                                           if self.calibration else None),
                "gain_ogain_res0_drive1": list(self.words),
                "legacy_gain_ogain_res0_drive1": list(self.legacy_words),
                "identity": {k: self.identity[k] for k in (
                    "ladder_class", "factor", "causal", "preserve_headroom",
                    "latency_frames", "out_bits", "g_rom_bits", "k_rom_bits")},
                "rom": {"CUT_TRIM": vf.CUT_TRIM, "coefficient_oversample": 2,
                        "g_rom_sha256": hashlib.sha256(v.g_rom.tobytes()).hexdigest()[:16],
                        "k_rom_sha256": hashlib.sha256(v.k_rom.tobytes()).hexdigest()[:16]},
                "exact_match": self.match}

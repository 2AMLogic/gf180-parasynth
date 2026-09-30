#!/usr/bin/env python3
"""Headless rigs for the three Minimoog-lineage software references on this
machine, plus our own ladder and its injected-defect controls.

Every rig answers the SAME three questions with the SAME stimulus, so the
numbers can be put side by side:

    tone_gain_db(freqs, cutoff, res, amp)  steady-state gain at each frequency,
                                           from a stepped tone and a coherent
                                           projection -- the measured transfer
                                           function, never a centroid
    ring(cutoff, res)                      the filter kicked once and left to
                                           ring: the self-oscillation tail
    drive_harmonics(...)                   h3/h5 of a steady tone as the input
                                           level rises

Why a stepped tone rather than an impulse response: this filter's response
depends on level by design (DR 0001, and the same is true of all three
references), so an impulse response would presume a linearity none of them
has. `model/test_moog_acceptance.py` already measures our filter this way and
this module uses the same probe, at the same drive, for all five rigs.

Hosting is `dawdreamer` (VST3, headless, programmatic parameters). What each
reference can and cannot be asked:

  Surge XT 1.2.3   OPEN SOURCE. "LP Vintage Ladder" subtype "Type 2" is
                   sst-filters' `VintageLadder::Huov` -- Huovilainen's DAFx-04
                   model, the same paper DR 0001 implements. Cutoff is
                   commanded in Hz and reads back in Hz, so cutoff ACCURACY is
                   answerable here and only here. Excited through the "Audio
                   In" oscillator, so the stimulus enters the filter directly.
  u-he Diva        VCF model "Ladder", 24 dB mode. No audio input (0 input
                   channels), so its own oscillator is the source and the
                   response is measured against a wide-open reference render.
                   Cutoff reads back on u-he's 30..150 scale, not in Hz.
  Arturia Mini V3  Dedicated Model D emulation, 2 audio inputs (the Model D's
                   external-input jack). Every parameter is a bare 0..1 with
                   no units and no readback, so its cutoff knob has to be
                   calibrated by measurement; commanded-cutoff accuracy is NOT
                   answerable against it.

All rigs render at 48 kHz, which is our own SR: nothing is resampled anywhere
in this harness, so no result can be a resampler artefact.
"""
from __future__ import annotations

import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "audition"))

SR = 48000
BLOCK = 512
FS_Q15 = 32768.0

VST3 = "/Library/Audio/Plug-Ins/VST3"
PATH_SURGE = f"{VST3}/Surge XT.vst3"
PATH_DIVA = f"{VST3}/Diva.vst3"
PATH_MINIV3 = f"{VST3}/Mini V3.vst3"


# ===========================================================================
# stimulus, shared by every rig so the comparison is of filters, not stimuli
# ===========================================================================
def tone_train(freqs, amp: float, settle_s: float, window_s: float):
    """One buffer holding a tone at each frequency in turn, each an INTEGER
    number of periods long so the coherent projection sees no leakage.
    Returns (buffer, [(start, n_window, f), ...]) with the analysis window
    starting after `settle_s` of each segment."""
    segs, parts, t = [], [], 0
    for f in freqs:
        per = SR / f
        n_set = int(round(max(settle_s * SR, 8 * per) / per)) * int(round(per)) if per < 1 else \
            int(round(max(settle_s * SR, 8 * per) / per) * round(per))
        n_win = int(round(max(window_s * SR, 16 * per) / per) * round(per))
        n = n_set + n_win
        ph = 2 * math.pi * f * np.arange(n) / SR
        segs.append(amp * np.sin(ph))
        parts.append((t + n_set, n_win, f))
        t += n
    return np.concatenate(segs), parts


def kick_then_silence(cut: float, seconds: float, amp: float, kick_s: float = 0.005):
    """A short sine burst at the cutoff, then silence: the free-ring stimulus
    `model/test_moog_acceptance.py` uses to make the filter sing on its own."""
    n = int(seconds * SR)
    nk = int(kick_s * SR)
    x = np.zeros(n)
    x[:nk] = amp * np.sin(2 * math.pi * cut * np.arange(nk) / SR)
    return x


# ===========================================================================
# our ladder, and the three deliberately-wrong ladders that give it power
# ===========================================================================
import voice_fx as vf                                               # noqa: E402
import audio_measure as am                                          # noqa: E402
import rig_qualification as rq                                      # noqa: E402

G_ROM = vf.make_g_rom()
K_ROM = vf.make_k_rom()
_REAL_LADDER = vf.LadderFx
_K_ROM_CACHE: dict[int, np.ndarray] = {}


def _k_rom_for(g_rom: np.ndarray) -> np.ndarray:
    """DR 0006's compensation ROM built against a SUBSTITUTED cutoff ROM. The
    compensation is derived from the cutoff coefficients, so a device that
    changes one has to change the other or it measures two defects at once.
    Byte for byte the same construction as `_k_rom_for` in
    `model/test_moog_acceptance.py`."""
    key = id(g_rom)
    if key not in _K_ROM_CACHE:
        step = (1 << 15) >> vf.KROM_BITS
        _K_ROM_CACHE[key] = np.array(
            [int(round(vf.k_onset(min(max(vf.CUT_MIN, i * step), vf.CUT_MAX),
                                  g_rom, vf.GROM_BITS, 2)[0] / 4.0 * 32768))
             for i in range((1 << vf.KROM_BITS) + 1)], dtype=np.int64)
    return _K_ROM_CACHE[key]


class _Variant(_REAL_LADDER):
    """The model's inner loop with two knobs, byte for byte the same code as
    `_LadderVariant` in `model/test_moog_acceptance.py` (which pins it to
    `LadderFx` bit-exactly in its non-defective setting).

    `stages`  4 is the model; 2 is the dropped-pole defect
    `nonlin`  'every'    tanh in every stage -- the model, DR 0001
              'feedback' four LINEAR poles and one saturating element in the
                         feedback: the Stilson/Smith shape DR 0001 rejected
              'input'    one tanh at the input, four linear poles
    """

    def __init__(self, *a, stages=4, nonlin="every", **kw):
        super().__init__(*a, **kw)
        self.stages, self.nonlin = stages, nonlin

    def process(self, x_q15, cutoff_hz, res, drive=1.0, *, g_q16=None,
                k=None, gain=None, ogain=None, k_q14=None):
        from fixed import sat, shl
        os_, SQ, SB, OB = self.os, self.SQ, self.SB, self.OB
        n = len(x_q15)
        g_tab, k_tab, gain, ogain = self.coefficients(
            cutoff_hz, res, drive, g_q16=g_q16, n=n, k=k, gain=gain, ogain=ogain, k_q14=k_q14)
        k_per_sample = np.ndim(k_tab) > 0
        k = None if k_per_sample else int(k_tab)
        out = np.empty(n, dtype=np.int16 if OB <= 16 else np.int32)
        y, w = self.y, self.w
        d1, d2 = self.d1, self.d2
        TQ = SQ - 15
        S, nl = self.stages, self.nonlin
        for i in range(n):
            xi = int(x_q15[i])
            g = int(g_tab[i])
            if k_per_sample:
                k = int(k_tab[i])
            for _ in range(os_):
                fb = (d1 + d2) >> 1
                if nl == "feedback":
                    fb = shl(self.tanh_fx(fb), TQ)
                u = sat(shl(xi * gain, TQ - 16) - ((k * fb) >> 14), SB)
                w0 = self.tanh_fx(u) if nl != "feedback" else sat(shl(u, -TQ), 16)
                for s in range(S):
                    prev = w0 if s == 0 else w[s - 1]
                    diff = prev - w[s]
                    y[s] = sat(y[s] + ((g * shl(diff, TQ)) >> 16), SB)
                    w[s] = self.tanh_fx(y[s]) if nl == "every" else sat(shl(y[s], -TQ), 16)
                d2, d1 = d1, y[S - 1]
            out[i] = sat((shl(y[S - 1], -TQ) * ogain) >> 16, OB)
        self.y, self.w, self.d1, self.d2 = y, w, d1, d2
        return out


class OurLadder:
    """Our fixed-point ladder at the host's own operating point: the g ROM and
    (by default) DR 0006's per-frame resonance compensation, exactly as
    `model/test_moog_acceptance.py` drives it.

    `cut_skew` multiplies the cutoff used to look up `g` WITHOUT changing the
    cutoff we claim to have commanded: the injected cutoff defect.
    """
    kind = "ours"
    cutoff_in_hz = True

    def __init__(self, name="ours", stages=4, nonlin="every", compensated=True,
                 cut_skew=1.0, drive=1.0, cfg=None, huov_fcr=False, g_rom=None):
        self.name = name
        self.stages, self.nonlin = stages, nonlin
        self.compensated, self.cut_skew, self.drive = compensated, cut_skew, drive
        self.cfg = dict(vf.LADDER_CFG, **(cfg or {}))
        # `g_rom` substitutes a different cutoff ROM. Default None is the shipped
        # module-level pair, so every device built before this parameter existed
        # is byte-identical to what it was. The resonance compensation is DERIVED
        # from the cutoff coefficients (DR 0006, make_k_rom -> k_onset ->
        # g_from_cut), so substituting one and not the other would measure two
        # defects at once -- the same rule `model/test_moog_acceptance.py`'s
        # `_k_rom_for` follows.
        self.g_rom = G_ROM if g_rom is None else np.asarray(g_rom, dtype=np.int64)
        self.k_rom = K_ROM if g_rom is None else _k_rom_for(self.g_rom)
        # PRECONDITION, asserted at the point of use rather than assumed.
        # `huov_fcr` exists to answer "what would applying Huovilainen's tuning
        # polynomial buy us", and it answers that only while the cutoff ROM does
        # NOT already carry it. Since DR 0011 the shipped ROM does, so against
        # that ROM the flag applies the correction a SECOND time and the
        # `ours-huovtune` device would be a doubly-tuned filter reported as a
        # candidate. A correct instrument in a wrong state is worse than an
        # absent one, so this refuses instead of measuring it; `build()` passes
        # the untuned ROM, which is what that device always meant.
        if huov_fcr and not np.array_equal(self.g_rom, vf.make_g_rom(tune=False)):
            raise AssertionError(
                "huov_fcr=True would apply Huovilainen's tuning polynomial on top of "
                "a cutoff ROM that already carries it (DR 0011). To measure the "
                "pre-DR-0011 candidate, pass g_rom=vf.make_g_rom(tune=False); "
                "to measure the shipped filter, use huov_fcr=False.")
        self.huov_fcr = huov_fcr

    @staticmethod
    def fcr(cut_hz: float, sr: float = SR) -> float:
        """Huovilainen's published tuning polynomial (DAFx-04). `fc` is the
        cutoff normalised to the BASE rate, not the oversampled one, which is
        how both Surge and Csound's original evaluate it.

        **WE APPLY IT TOO, AND HAVE SINCE DR 0011.** The sentence that used to
        stand here -- "which Surge applies and we do not" -- was true when this
        rig was written and stopped being true on 2026-09-18, when DR 0011 baked
        `CUT_TRIM * fcr()` into `voice_fx.make_g_rom()`. `G_ROM` above IS that
        tuned ROM, so `huov_fcr=True` now applies the polynomial a SECOND time;
        `__init__` refuses that combination rather than measuring it. See the
        identical correction in `model/voice_fx.py`'s own comment, and issue
        #46's rung-1 audit (`model/ladder_headroom.py`) for what is left.

        **The quadratic coefficient is 0.4955, and `sst-filters` ships
        0.4995.** Surge's own comment in `VintageLadders.h` reads
        `0.4955 * fc2` while the constant beside it is named `m04955` and
        initialised to `0.4995f` -- and the cited source spells it `0.4955`.
        So the paper's value is 0.4955 and Surge's shipping artefact is a
        typo; this repository follows the paper, and
        `docs/surge-source-notes.md` section 4 records why our value differs
        from the code anyone comparing against Surge will be reading.

        It changes nothing measured: at a 10 kHz cutoff the two differ by
        1.7e-4 in an fcr of 0.9022, which is 0.003 cents."""
        fc = cut_hz / sr
        return 1.8730 * fc ** 3 + 0.4955 * fc ** 2 - 0.6490 * fc + 0.9988

    def _regs(self, res, cut, drive):
        ref = _REAL_LADDER(**self.cfg)
        k, gain, ogain = ref.regs(res, drive)
        skew = self.cut_skew * (self.fcr(cut) if self.huov_fcr else 1.0)
        g = int(vf.g_from_cut(np.array([cut * skew]), self.g_rom)[0])
        if self.compensated:
            kc = int(vf.kc_from_cut(np.array([cut * skew]), self.k_rom)[0])
            k = int(vf.k_effective(k, kc))
        return g, k, gain, ogain

    def _render(self, x, cut, res, drive=None):
        drive = self.drive if drive is None else drive
        g, k, gain, ogain = self._regs(res, cut, drive)
        lad = _Variant(**self.cfg, stages=self.stages, nonlin=self.nonlin)
        xq = np.clip(np.round(x), -32768, 32767).astype(np.int16)
        n = len(xq)
        return lad.process(xq, None, res, drive, g_q16=np.full(n, g, dtype=np.int64),
                           k=k, gain=gain, ogain=ogain).astype(np.float64)

    # -- the three questions -------------------------------------------------
    def tone_gain_db(self, freqs, cut, res, amp):
        x, parts = tone_train(freqs, amp * FS_Q15, 0.06, 0.20)
        y = self._render(x, cut, res)
        out = []
        for i0, nw, f in parts:
            a = am.tone_amplitude(y[i0:i0 + nw], f).require(f"ours probe {f:.0f} Hz")
            out.append(20 * math.log10(max(a, 1e-12) / (amp * FS_Q15)))
        return np.array(out)

    def ring(self, cut, res, seconds=0.6, amp=0.09):
        y = self._render(kick_then_silence(cut, seconds, amp * FS_Q15), cut, res)
        return y[int(0.4 * len(y)):] / FS_Q15

    def drive_tone(self, f, cut, res, amp):
        n = int(0.4 * SR)
        x = amp * FS_Q15 * np.sin(2 * math.pi * f * np.arange(n) / SR)
        return self._render(x, cut, res)[int(0.15 * SR):] / FS_Q15


# ===========================================================================
# the plugin rigs
# ===========================================================================
class _Plugin:
    """One dawdreamer engine holding one plugin, with an optional audio input.
    Parameters are set by index; every index used here was found by name from
    `get_parameters_description()` and is checked on construction."""

    path = None
    note = 48
    have_input = False

    def __init__(self, quiet=True, block=BLOCK):
        """`block` is the host block size. It matters for one measurement and
        only one: parameter AUTOMATION is applied per block, so a 512-sample
        block moves a swept cutoff in 93.75 Hz steps and the resulting ripple
        is the HARNESS's, not the plugin's. The movement study passes a small
        block so that the automation rate sits above its analysis band."""
        import dawdreamer as daw
        self._daw = daw
        self.block = block
        self.eng = daw.RenderEngine(SR, block)
        self.p = self.eng.make_plugin_processor(self.name, self.path)
        self._buf = np.zeros((2, SR), dtype=np.float32)
        if self.have_input:
            self.pb = self.eng.make_playback_processor("src", self._buf)
            self.eng.load_graph([(self.pb, []), (self.p, ["src"])])
        else:
            self.pb = None
            self.eng.load_graph([(self.p, [])])
        self.setup()
        self.qualify()

    def qualify(self):
        """Render once, then hold every pinned setting to its NAME and its
        READBACK. Both halves are needed and neither is decoration:

          * a plugin's parameter text does not update until the processor has
            run, which is how a Diva cutoff appeared stuck at 90 for a session
          * Surge's oscillator 1 parameters 259-267 change MEANING with the
            oscillator type while dawdreamer keeps reporting the Classic
            oscillator's names, so index 265 is 'A Osc 1 Unison Voices' under
            every type and reads '1 voice' for a Classic and '14.28 Hz' for an
            Audio In, where it is the High Cut. Only the readback separates
            them, and pinning 265 to 0 as "1 voice" would have put a high cut
            on every measurement

        A rig that cannot prove its own settings refuses to be built."""
        if self.have_input:
            self.pb.set_data(np.zeros((2, int(0.05 * SR)), dtype=np.float32))
        self.p.clear_midi()
        self.eng.render(0.05)
        bad = self.check_pins()
        if bad:
            raise RuntimeError(f"{self.name}: pinned settings did not hold: {bad}")

    # -- helpers -------------------------------------------------------------
    def set(self, idx, v):
        self.p.set_parameter(int(idx), float(v))

    def text(self, idx):
        return self.p.get_parameter_text(int(idx))

    # (index, normalised value, EXPECTED PARAMETER NAME, expected readback or
    # None) -- every setting that changes the sound and is NOT the thing under
    # test. A comparison against a setting nobody wrote down is not a
    # comparison, and every one of these was previously left at whatever the
    # plugin happened to default to.
    #
    # The expected NAME is not decoration. Surge RENAMES parameters 259-267
    # when oscillator 1's type changes, so index 265 is "Unison Voices" for a
    # Classic oscillator and "High Cut" for an Audio In one -- and pinning it
    # to 0 as "1 voice" sets a 13.75 Hz high cut instead. That is a silent,
    # total loss of signal, and the name check is what caught it.
    PINS = ()

    def apply_pins(self):
        for idx, val, _name, _want in self.PINS:
            if val is not None:
                self.set(idx, val)

    def pinned_report(self) -> dict:
        """What the pinned settings actually read back, AFTER a render -- a
        plugin's parameter text and NAMES do not update until the processor
        has run, which is how a Diva cutoff appeared stuck at 90 for a whole
        session and how the Surge renumbering above stayed hidden."""
        return {f"{i}:{self.p.get_parameter_name(i)}": self.text(i)
                for i, _v, _n, _w in self.PINS}

    def check_pins(self) -> list:
        """Every pinned index must still carry the name it was pinned by, and
        read back what it was set to. Call this AFTER a render."""
        bad = []
        for idx, _val, name, want in self.PINS:
            got_name = self.p.get_parameter_name(idx)
            if name is not None and got_name != name:
                bad.append((idx, "NAME", name, got_name))
            elif want is not None and self.text(idx) != want:
                bad.append((idx, "VALUE", want, self.text(idx)))
        return bad

    def check_names(self, mapping):
        bad = [(i, want, self.p.get_parameter_name(i)) for i, want in mapping.items()
               if self.p.get_parameter_name(i) != want]
        if bad:
            raise RuntimeError(f"{self.name}: parameter indices moved: {bad}")

    def render(self, x, seconds, note_at=0.02, note_len=None):
        """Render `seconds` with `x` (mono, already at SR) in the audio input
        when the plugin has one, a note held throughout, and the plugin's own
        latency removed. Returns mono float."""
        n = int(seconds * SR)
        if self.have_input:
            buf = np.zeros((2, n), dtype=np.float32)
            m = min(n, len(x))
            buf[0, :m] = x[:m]
            buf[1, :m] = x[:m]
            self.pb.set_data(buf)
        self.p.clear_midi()
        self.p.add_midi_note(self.note, 100, note_at, seconds if note_len is None else note_len)
        self.eng.render(seconds)
        a = self.eng.get_audio()
        y = a[0].astype(np.float64)
        lat = self.p.get_latency_samples()
        return y[lat:] if lat else y

    def silence_state(self, seconds=0.4):
        """Render silence with no note so the filter state decays before the
        next measurement."""
        if self.have_input:
            self.pb.set_data(np.zeros((2, int(seconds * SR)), dtype=np.float32))
        self.p.clear_midi()
        self.eng.render(seconds)

    # -- the three questions -------------------------------------------------
    def tone_gain_db(self, freqs, cut, res, amp):
        raise NotImplementedError

    def ring(self, cut, res, seconds=1.2, amp=0.25):
        raise NotImplementedError


class SurgeRig(_Plugin):
    """Surge XT, scene A, oscillator 1 = Audio In, filter 1 = LP Vintage
    Ladder. `subtype` selects the model: 'Type 2' is Huovilainen (the paper we
    implemented), 'Type 1' is the Runge-Kutta/Stilson-lineage model."""
    name = "surge"
    path = PATH_SURGE
    have_input = True

    # indices verified by name on construction
    I = dict(osc1_type=256, osc1_level=292, osc1_mute=293, osc2_mute=297, osc3_mute=301,
             rm12_mute=305, rm23_mute=309, noise_mute=313, fconfig=249, ws_type=252,
             f1_type=317, f1_sub=318, f1_cut=319, f1_res=320, f1_feg=321, f1_kt=322,
             f2_type=323, amp_a=329, amp_d=331, amp_s=333, amp_r=334,
             scene_vol=237, prefilter_gain=316, vca_gain=246, vel_vca=247)
    NAMES = {256: 'A Osc 1 Type', 292: 'A Osc 1 Level', 293: 'A Osc 1 Mute',
             297: 'A Osc 2 Mute', 301: 'A Osc 3 Mute', 305: 'A Ring Modulation 1x2 Mute',
             309: 'A Ring Modulation 2x3 Mute', 313: 'A Noise Mute',
             249: 'A Filter Configuration', 252: 'A Waveshaper Type',
             317: 'A Filter 1 Type', 318: 'A Filter 1 Subtype', 319: 'A Filter 1 Cutoff',
             320: 'A Filter 1 Resonance', 321: 'A Filter 1 FEG Mod Amount',
             322: 'A Filter 1 Keytrack', 323: 'A Filter 2 Type',
             329: 'A Amp EG Attack', 331: 'A Amp EG Decay', 333: 'A Amp EG Sustain',
             334: 'A Amp EG Release', 237: 'A Volume', 316: 'A Pre-Filter Gain',
             246: 'A VCA Gain', 247: 'A Velocity > VCA Gain'}
    SUBTYPE = {"Type 1": 0.015, "Type 1 Compensated": 0.07,
               "Type 2": 0.135, "Type 2 Compensated": 0.20}
    PINS = (
        (227, 0.5, 'Character', 'Neutral'),       # a pre-filter tone control on the
                                                  # oscillators. Was never set.
        (234, 0.0, 'A Osc Drift', '0.00 %'),
        # 259/260/264/265 are the Audio In oscillator's Channel, Gain, Low Cut
        # and High Cut -- but dawdreamer's VST3 view reports them under the
        # CLASSIC oscillator's names whatever the type is loaded, so the names
        # below are the ones the plugin actually hands back and the NAME check
        # cannot see the renumbering at all. The READBACK can: 265 reads
        # '14.28 Hz' here and '1 voice' under a Classic oscillator, and that
        # difference is the whole of the hazard this rig was bitten by.
        # 264/265 read the extremes of their ranges, which is how Surge shows
        # a DEACTIVATED cut; no value is written, because writing 0 to 265
        # would set a high cut and silence the input. `flat_path_db` measures
        # that the path really is flat rather than taking the readback's word.
        (259, 0.5, 'A Osc 1 Shape', '0.00 % (Stereo)'),
        (260, 0.5, 'A Osc 1 Width 1', '0.00 dB'),
        (264, None, 'A Osc 1 Unison Detune', '29.14 Hz'),
        (265, None, 'A Osc 1 Unison Voices', '14.28 Hz'),
        (277, 0.0, 'A Osc 2 Unison Voices', '1 voice'),
        (289, 0.0, 'A Osc 3 Unison Voices', '1 voice'),
        (18, 1.0, 'FX Chain Bypass', 'All FX Off'),
        # Every FX slot, pinned by READBACK. `setup` already wrote these to Off
        # and nothing checked that they stayed there -- and an effect in the
        # path is not always visible in the signal: Surge's Phaser is a chain
        # of allpasses, so at its default mix it moves phase and leaves every
        # harmonic amplitude where it was. A spectral check cannot refuse that
        # one. A pin can.
        (19, 0.0, 'FX A1 FX Type', 'Off'),
        (32, 0.0, 'FX A2 FX Type', 'Off'),
        (45, 0.0, 'FX B1 FX Type', 'Off'),
        (58, 0.0, 'FX B2 FX Type', 'Off'),
        (12, 1.0, 'Global Volume', '0.00 dB'),
        (316, 0.5, 'A Pre-Filter Gain', '0.00 dB'),
        (246, 0.5, 'A VCA Gain', '0.00 dB'),
        (250, 0.5, 'A Filter Balance', '0.00 %'),
        (253, 0.5, 'A Waveshaper Drive', '0.00 dB'),
        (230, 0.0, 'A Portamento', '0.000 s'),
        (235, 0.5, 'A Noise Color', '0.00 %'),
    )
    V_VINTAGE_LADDER = 0.3063
    V_AUDIO_IN = 0.3662
    # Surge's cutoff scale, read straight off its own readback: 13.75 Hz at 0,
    # 25087.71 Hz at 1, exactly 130 semitones across.
    CUT_LO, CUT_SEMIS = 13.75, 130.0
    cutoff_in_hz = True
    kind = "surge"

    def __init__(self, subtype="Type 2", **kw):
        self.subtype = subtype
        self.name = f"surge-{subtype.replace(' ', '').lower()}"
        super().__init__(**kw)

    def setup(self):
        self.check_names(self.NAMES)
        I = self.I
        self.set(I['osc1_type'], self.V_AUDIO_IN)
        self.set(I['osc1_level'], 1.0)
        for m in ('osc1_mute',):
            self.set(I[m], 0.0)
        for m in ('osc2_mute', 'osc3_mute', 'rm12_mute', 'rm23_mute', 'noise_mute'):
            self.set(I[m], 1.0)
        self.set(I['fconfig'], 0.0)          # Serial 1: filter 1 only
        self.set(I['ws_type'], 0.0)          # waveshaper off
        self.set(I['f2_type'], 0.0)          # filter 2 off
        self.set(I['f1_type'], self.V_VINTAGE_LADDER)
        self.set(I['f1_sub'], self.SUBTYPE[self.subtype])
        self.set(I['f1_feg'], 0.5)           # 0 semitones of envelope on cutoff
        self.set(I['f1_kt'], 0.5)            # 0 % keytrack
        self.set(I['amp_a'], 0.0)
        self.set(I['amp_d'], 1.0)
        self.set(I['amp_s'], 1.0)            # flat gate: no amplitude envelope
        self.set(I['amp_r'], 0.5)
        self.set(I['vel_vca'], 1.0)          # 0 dB: no velocity sensitivity
        for i in range(19, 19 + 4 * 13, 13):  # every FX slot type -> Off
            self.set(i, 0.0)
        self.apply_pins()
        assert self.text(I['f1_type']) == 'LP Vintage Ladder', self.text(I['f1_type'])
        assert self.text(I['f1_sub']) == self.subtype, self.text(I['f1_sub'])

    def cut_value(self, hz):
        return math.log2(hz / self.CUT_LO) * 12.0 / self.CUT_SEMIS

    def set_point(self, cut_hz, res):
        self.set(self.I['f1_cut'], self.cut_value(cut_hz))
        self.set(self.I['f1_res'], res)
        return float(self.text(self.I['f1_cut']).split()[0])

    # The stepped-tone RENDER and the projection that reads it are split so
    # that a frozen reference profile can cache the audio itself and re-derive
    # the curve from it later, rather than freezing a curve nobody can
    # re-measure. `tone_gain_db` is byte-for-byte the same measurement it was;
    # `tools/test_refprofile.py::test_tone_gain_db_equals_projection_of_tone_render`
    # pins the two together on a closed-form signal, with no plugin involved.
    TONE_SETTLE_S, TONE_WINDOW_S, TONE_PRE_S = 0.06, 0.20, 0.30

    def tone_render(self, freqs, cut, res, amp):
        """The stepped-tone stimulus through the filter, as audio. Returns
        (y, parts, commanded_cutoff_readback) with `y` already trimmed of the
        lead-in, so `parts` indexes straight into it."""
        read = self.set_point(cut, res)
        x, parts = tone_train(freqs, amp, self.TONE_SETTLE_S, self.TONE_WINDOW_S)
        total = (len(x) / SR) + 0.35
        pre = int(self.TONE_PRE_S * SR)
        y = self.render(np.concatenate([np.zeros(pre), x]), total)
        return y[pre:], parts, read

    @staticmethod
    def tone_project(y, parts, amp, name="tone"):
        """dB gain at each stepped tone, by coherent projection. The only step
        between a cached render and a response curve."""
        out = []
        for i0, nw, f in parts:
            a = am.tone_amplitude(y[i0: i0 + nw], f).require(f"{name} probe {f:.0f} Hz")
            out.append(20 * math.log10(max(a, 1e-12) / amp))
        return np.array(out)

    def tone_gain_db(self, freqs, cut, res, amp):
        y, parts, _ = self.tone_render(freqs, cut, res, amp)
        return self.tone_project(y, parts, amp, self.name)

    def ring(self, cut, res, seconds=1.2, amp=0.25):
        self.set_point(cut, res)
        self.silence_state(0.3)
        x = np.concatenate([np.zeros(int(0.05 * SR)), kick_then_silence(cut, seconds, amp)])
        y = self.render(x, seconds + 0.1)
        return y[int(0.55 * len(y)):]

    def drive_tone(self, f, cut, res, amp):
        self.set_point(cut, res)
        n = int(0.4 * SR)
        x = np.concatenate([np.zeros(int(0.25 * SR)),
                            amp * np.sin(2 * math.pi * f * np.arange(n) / SR)])
        y = self.render(x, 0.70)
        return y[int(0.42 * SR):int(0.65 * SR)]

    # ---- oscillator 1, for the waveform study -----------------------------
    # Surge's Classic oscillator sums TWO saws whose separation is set by
    # Width, and Shape mixes between one saw and the pair. Shape is BIPOLAR.
    # `--stage shape` sweeps it and identifies each result by
    # `audio_measure.waveform_id` -- time domain first, duty measured, nulls
    # checked against that measured duty.
    #
    # THE TABLE BELOW IS A TRANSCRIPTION of that sweep, which is committed as
    # docs/surge-waveform-mapping.txt -- the stage's own output, and the only
    # authority for these cells. Nothing here is re-derived or paraphrased:
    # `tools/check_surge_waveform_comment.py` parses the table back out of this
    # file and REFUSES unless every cell reproduces that file character for
    # character, and the `python` job of .github/workflows/rungs.yml runs it
    # (and its controls) on every pull request -- so the two cannot drift
    # silently again. A comment claiming that guarantee while the checker ran
    # only on the build box would be the same defect one level up. They had:
    # six cells
    # disagreed with the sweep and the citation named a file that has never
    # existed in this repository (issue #271).
    #
    # At A2 = 110 Hz, filter OFF, unison asserted at 1 voice, every FX slot
    # off. UNQUALIFIED is `waveform_id` REFUSING to name a waveform for that
    # run -- an outcome, not a waveform; the reasons are quoted below.
    #
    #   Shape    reads      Width     identified as
    #   0.000  -100.00 %   50.00 %    pulse:50.0%
    #   0.125   -75.00 %   50.00 %    pulse:50.1%
    #   0.250   -50.00 %   50.00 %    UNQUALIFIED
    #   0.375   -25.00 %   50.00 %    UNQUALIFIED
    #   0.500     0.00 %   50.00 %    saw
    #   0.625    25.00 %   50.00 %    UNQUALIFIED
    #   0.750    50.00 %   50.00 %    UNQUALIFIED
    #   0.875    75.00 %   50.00 %    UNQUALIFIED
    #   1.000   100.00 %   50.00 %    UNQUALIFIED
    #   0.000  -100.00 %   25.00 %    pulse:25.0%
    #   0.125   -75.00 %   25.00 %    pulse:25.0%
    #   0.250   -50.00 %   25.00 %    UNQUALIFIED
    #   0.375   -25.00 %   25.00 %    saw
    #   0.500     0.00 %   25.00 %    saw
    #   0.625    25.00 %   25.00 %    saw
    #   0.750    50.00 %   25.00 %    UNQUALIFIED
    #   0.875    75.00 %   25.00 %    UNQUALIFIED
    #   1.000   100.00 %   25.00 %    UNQUALIFIED
    #
    # Why each refusal fired, quoted from that file:
    # (this list is the block's VERBATIM section -- every double-quoted run in
    # it is checked against the sweep. Quotes elsewhere in this comment are
    # labels, not measurements, and are NOT checked, so a measured string put
    # outside this list would go unverified. Put it here.)
    #   0.250        "6 discontinuities in one period -- neither a ramp nor a
    #                rectangle" at Width 50 %, and "3 discontinuities in one
    #                period" at 25 %
    #   0.375, 0.625 "one discontinuity per period but not a saw" -- h2 reads
    #                -10.2 and -1.4 dB where a saw needs -6.0, at Width 50 %
    #                only. At 25 % both of those ARE saws, so Shape 0.500 is
    #                the centre of a WIDE saw region there and of a narrow one
    #                at 50 %; it is the only value that is a saw at both.
    #   0.750        "harmonics above the fundamental at [2]" at Width 50 %,
    #                and the h2 refusal again at 25 %, at -12.8 dB
    #   0.875, 1.000 "4 midpoint crossings in one period -- the record holds 2
    #                cycles of something, not one waveform" -- both widths at
    #                0.875, and at Width 25 % for 1.000 as well
    #   1.000 / 50 % "no component within 50 cents of the commanded 110.00 Hz
    #                (the strongest nearby is 79.98 Hz, -551.7 cents)" -- the
    #                DUAL SAW. Note what the sweep does NOT report here: no
    #                waveform and no valid fundamental. This cell is why an
    #                earlier version of this comment was wrong to call it a saw
    #                at 2*f0 -- the measurement identifies nothing.
    #
    # The mapping that shipped had saw at 0.00 and square at 1.00: it asked for
    # a 50 % PULSE and called it a saw, and for the DUAL SAW -- the run the
    # sweep could not qualify at all -- and called it a square. Every Surge
    # oscillator row of docs/reference-voice-report.txt before this change is of
    # a different waveform from the one it is labelled with. There is no
    # triangle on this oscillator, so Surge has no counterpart for ours -- a
    # finding about the comparison, not an error.
    #
    # The SINE is a separate oscillator type and it needs pinning just as hard:
    # 259 is a wave SELECTOR there (28 shapes) and 260 is Feedback, and the
    # mapping that shipped wrote neither, so Surge's "sine" was whatever the
    # previously measured waveform happened to leave behind. At 260 = 0.0 that
    # is -400 % feedback and h3 sits at -3.4 dB.
    V_CLASSIC, V_SINE = 0.0238, 0.0938
    # (oscillator type, Shape, Width, the readback BOTH must show). The
    # readback is the mapping: a normalised value means nothing on its own,
    # and the same index is Shape/Width for a Classic oscillator and
    # Wave/Feedback for a Sine one.
    WAVES = {
        "saw":     ("Classic", 0.5, 0.5, ('0.00 %', '50.00 %')),
        "square":  ("Classic", 0.0, 0.5, ('-100.00 %', '50.00 %')),
        "pulse25": ("Classic", 0.0, 0.25, ('-100.00 %', '25.00 %')),
        "sine":    ("Sine", 0.0, 0.5, ('Wave 1 (TX 1)', '0.00 %')),
    }

    # Surge's oscillator 1 parameters 259-267 change MEANING with the type and
    # dawdreamer does NOT rename them, so only the readback text can tell the
    # types apart -- and it does not update until the processor has run. 265 is
    # the one that matters: 'Unison Voices' for Classic and Sine, the Audio In
    # HIGH CUT for Audio In. It is asserted, never written; a rig that finds
    # unison switched on refuses rather than measuring three detuned saws.
    OSC_TYPE = {"Classic": V_CLASSIC, "Sine": V_SINE, "Audio In": V_AUDIO_IN}
    OSC_READBACK = {
        "Audio In": {259: '0.00 % (Stereo)', 260: '0.00 dB',
                     264: '29.14 Hz', 265: '14.28 Hz'},
        "Classic": {264: '10.00 cents', 265: '1 voice'},
        "Sine": {264: '10.00 cents', 265: '1 voice'},
    }

    def select_osc(self, kind):
        """Oscillator 1 -> `kind`, filter OFF, and REFUSE unless the plugin
        agrees it is that oscillator with unison off. The render is what makes
        the readbacks current; writing Shape immediately after a type change
        writes it into the previous type's parameter, which is how the first
        run of this probe reported a "square" with h2 at +79.6 dB."""
        self.set(self.I['osc1_type'], self.OSC_TYPE[kind])
        self.set(self.I['f1_type'], 0.0)                 # filter OFF
        # settle with NO note. `render` adds a MIDI note every time it is
        # called, so using it to make the readbacks current stacks note-ons and
        # leaves the previous oscillator's voice decaying into the next
        # measurement -- which is how the sine that follows a 1760 Hz pulse
        # read as a 50.3 % rectangle while the same setting measured on its own
        # read as a sine.
        self.silence_state(0.25)
        got = self.text(self.I['osc1_type'])
        if got != kind:
            raise RuntimeError(f"{self.name}: oscillator 1 reads {got!r}, wanted {kind!r}")
        bad = [(i, w, self.text(i)) for i, w in self.OSC_READBACK[kind].items()
               if self.text(i) != w]
        if bad:
            raise RuntimeError(
                f"{self.name}: oscillator 1 is {kind!r} but its parameters read {bad} "
                f"-- (index, wanted, got)")

    def osc_raw(self, kind, shape, width, note, seconds=0.5, expect=None):
        """One oscillator, FILTER OFF, flat gate, at a COMMANDED Shape and
        Width -- the primitive `--stage shape` sweeps. Returns the audio and
        what the plugin says the two controls read, so the sweep table records
        Surge's own numbers and not ours. `expect`, when given, REFUSES a
        readback that is not the one the mapping was measured at."""
        self.select_osc(kind)
        reads = (None, None)
        if shape is not None:
            self.set(259, shape)
            self.set(260, width)
            self.silence_state(0.10)
            reads = (self.text(259), self.text(260))
            if expect is not None and reads != tuple(expect):
                raise RuntimeError(
                    f"{self.name}: oscillator 1 Shape/Width read {reads}, "
                    f"the mapping was measured at {tuple(expect)}")
        self.note = int(note)
        y = self.render(np.zeros(1), seconds + 0.25)
        return y[int(0.2 * SR):int(0.2 * SR) + int(seconds * SR)], reads

    def osc_tone(self, wave, note, seconds=0.5):
        """One oscillator, FILTER OFF, flat gate: the waveform as the
        instrument makes it, with nothing else in the path."""
        if wave not in self.WAVES:
            raise NotImplementedError(f"Surge's Classic oscillator has no {wave!r}")
        kind, shape, width, expect = self.WAVES[wave]
        return self.osc_raw(kind, shape, width, note, seconds, expect)[0]

    def noise_tone(self, seconds=6.0, colour=0.5):
        """Surge's noise source alone, filter OFF. `colour` is its own
        -100..100 % Noise Color control as a normalised value; 0.5 is 0 %."""
        self.set(self.I['osc1_mute'], 1.0)
        self.set(self.I['noise_mute'], 0.0)
        self.set(312, 1.0)                               # A Noise Level, 0 dB
        self.set(235, colour)
        self.set(self.I['f1_type'], 0.0)                 # filter OFF
        y = self.render(np.zeros(1), seconds + 0.3)
        self.set(self.I['noise_mute'], 1.0)
        self.set(self.I['osc1_mute'], 0.0)
        return y[int(0.25 * SR):int(0.25 * SR) + int(seconds * SR)]

    def osc_level_ref(self, seconds=2.0, note=45):
        """The saw at the SAME mixer setting the noise was measured at, so
        "noise relative to an oscillator" is a number and not an impression."""
        self.set(self.I['osc1_mute'], 0.0)
        self.set(self.I['noise_mute'], 1.0)
        self.set(self.I['osc1_level'], 1.0)
        return self.osc_tone("saw", note, seconds)

    def swept_cutoff(self, carrier, lo, hi, seconds, cache=None, res=0.1, amp=0.25):
        """A steady carrier through the cutoff swept lo -> hi by parameter
        AUTOMATION, which is how a host moves a control and the only way to
        make the plugin do it sample by sample. Surge's cutoff parameter is
        exponential in Hz, so a linear ramp of the normalised value is an
        exponential sweep -- exactly what our own sweep does."""
        self.set(self.I['f1_res'], res)
        pre = int(0.25 * SR)
        n = int(seconds * SR)
        ramp = np.concatenate([np.full(pre, self.cut_value(lo), dtype=np.float32),
                               np.linspace(self.cut_value(lo), self.cut_value(hi),
                                           n).astype(np.float32),
                               np.full(int(0.05 * SR), self.cut_value(hi), dtype=np.float32)])
        self.p.set_automation(self.I['f1_cut'], ramp)
        t = np.arange(len(ramp)) / SR
        x = amp * np.sin(2 * math.pi * carrier * t)
        y = self.render(x, len(ramp) / SR)
        return y[pre:pre + n]


class MiniV3Rig(_Plugin):
    """Arturia Mini V3 through the Model D's external-input jack. Every
    parameter is a bare 0..1 with no readback, so the cutoff knob is a knob,
    not a frequency: this rig answers shape questions, not commanded-cutoff
    accuracy."""
    name = "miniv3"
    path = PATH_MINIV3
    have_input = True
    cutoff_in_hz = False
    kind = "miniv3"
    note = 48

    I = dict(level=0, glide=1, tune=2, lvl_o1=15, lvl_o2=16, lvl_o3=17,
             lvl_noise=21, lvl_ext=22, cutoff=23, emphasis=24, contour=25,
             vcf_a=26, vcf_d=27, vcf_s=28, vca_a=29, vca_d=30, vca_s=31,
             chorus_mix=6, delay_wet=11, vocal_wet=43, o1=72, o2=73, o3=74,
             noise_sw=75, ext_sw=76, fmod=78)
    PINS = (
        (103, 0.0, 'Unison', None),   # Unison -- multiple detuned voices. Was never set.
        (110, 0.0, 'Soft Clipping', 'Off'),  # Soft Clipping -- an output saturation stage. Was never set.
        (3, 0.0, 'Voices/Osc detune', None),     # Voices/Osc detune. Was never set.
        (62, 0.0, 'Chorus', None),    # Chorus enable
        (63, 0.0, 'Chorus Type', 'Chorus Type 1'),
        (4, 0.0, 'Chorus Speed', None), (5, 0.0, 'Chorus Depth', None), (6, 0.0, 'Chorus Dry/Wet', None),      # chorus speed/depth/mix
        (64, 0.0, 'Delay', None), (65, 0.0, 'Delay Sync', None),                    # Delay enable, sync
        (7, 0.0, 'Delay Time Left', None), (8, 0.0, 'Delay FeedBack Left', None), (9, 0.0, 'Delay Time Right', None), (10, 0.0, 'Delay FeedBack Right', None), (11, 0.0, 'Delay Wet', None),
        (106, 0.0, 'Vocal Filter', None),   # Vocal Filter enable
        (41, 0.0, 'Vocal Filter X', None), (42, 0.0, 'Vocal Filter Y', None), (43, 0.0, 'Vocal Filter Dry/wet', None), (44, 0.0, 'Vocal Filter resonance', None),
        (99, 0.0, 'Vocal Filter Lfo Rate', None), (100, 0.0, 'Vocal Filter Lfo', None),                   # vocal filter LFO
        (77, 0.0, 'Pink Noise', None),    # Pink Noise blend
        (2, 0.5, 'Tune', None),     # Tune -- centred
    )
    NAMES = {0: 'General Level', 15: 'Level Osc1', 16: 'Level Osc2', 17: 'Level Osc3',
             21: 'Level Noise', 22: 'Level Ext', 23: 'CutOff', 24: 'Emphasis',
             25: 'Amount', 26: 'VCF Attack', 27: 'VCF Decay', 28: 'VCF Sustain',
             29: 'VCA Attack', 30: 'VCA Decay', 31: 'VCA Sustain',
             6: 'Chorus Dry/Wet', 11: 'Delay Wet', 43: 'Vocal Filter Dry/wet',
             75: 'Noise', 78: 'Filter Modulation'}

    def setup(self):
        self.check_names(self.NAMES)
        I = self.I
        for k in ('lvl_o1', 'lvl_o2', 'lvl_o3', 'lvl_noise', 'o1', 'o2', 'o3', 'noise_sw',
                  'chorus_mix', 'delay_wet', 'vocal_wet', 'glide', 'contour', 'fmod'):
            self.set(I[k], 0.0)
        self.set(I['lvl_ext'], 0.8)
        self.set(I['ext_sw'], 1.0)
        self.set(I['level'], 0.7)
        self.set(I['tune'], 0.5)
        self.set(I['vcf_a'], 0.0); self.set(I['vcf_d'], 0.0); self.set(I['vcf_s'], 1.0)
        self.set(I['vca_a'], 0.0); self.set(I['vca_d'], 1.0); self.set(I['vca_s'], 1.0)
        self.apply_pins()

    def set_point(self, cut, res):
        self.set(self.I['cutoff'], cut)
        self.set(self.I['emphasis'], res)
        return cut

    def tone_gain_db(self, freqs, cut, res, amp):
        self.set_point(cut, res)
        x, parts = tone_train(freqs, amp, 0.06, 0.20)
        total = (len(x) / SR) + 0.40
        y = self.render(np.concatenate([np.zeros(int(0.35 * SR)), x]), total)
        off = int(0.35 * SR)
        out = []
        for i0, nw, f in parts:
            seg = y[off + i0: off + i0 + nw]
            a = am.tone_amplitude(seg, f).require(f"{self.name} probe {f:.0f} Hz")
            out.append(20 * math.log10(max(a, 1e-12) / amp))
        return np.array(out)

    def ring(self, cut, res, seconds=1.2, amp=0.25):
        self.set_point(cut, res)
        self.silence_state(0.3)
        x = np.concatenate([np.zeros(int(0.05 * SR)),
                            kick_then_silence(400.0, seconds, amp, kick_s=0.01)])
        y = self.render(x, seconds + 0.1)
        return y[int(0.55 * len(y)):]

    def drive_tone(self, f, cut, res, amp):
        self.set_point(cut, res)
        n = int(0.4 * SR)
        x = np.concatenate([np.zeros(int(0.30 * SR)),
                            amp * np.sin(2 * math.pi * f * np.arange(n) / SR)])
        y = self.render(x, 0.78)
        return y[int(0.48 * SR):int(0.70 * SR)]

    # The Model D's six waveforms, in panel order, as Mini V3 enumerates them.
    # Ours has four of these and a sine the Model D does not have; the Model D
    # has a shark-tooth we do not. Both gaps are findings.
    WAVES = {"tri": 0.075, "shark": 0.2417, "saw": 0.4083, "square": 0.575,
             "wide_rect": 0.7417, "narrow_rect": 0.9167}

    def osc_tone(self, wave, note, seconds=0.5):
        """One oscillator, filter wide open, flat gate. Mini V3's filter
        cannot be bypassed, so `cutoff` is at maximum and its residual
        response is stated with any result that depends on the top octave."""
        self.set(48, self.WAVES[wave])
        # Range Osc1. Its default is 'Low' -- the Model D's sub-audio setting --
        # so without this the oscillator is an LFO and every harmonic
        # measurement refuses. It is a sound-changing control that was not
        # pinned, which is exactly the class of omission this rig now guards.
        self.set(45, 0.575)                              # "8'"
        self.set(self.I['lvl_ext'], 0.0); self.set(self.I['ext_sw'], 0.0)
        self.set(self.I['lvl_o1'], 0.9); self.set(self.I['o1'], 1.0)
        self.set(self.I['cutoff'], 1.0); self.set(self.I['emphasis'], 0.0)
        self.note = int(note)
        y = self.render(np.zeros(1), seconds + 0.3)
        return y[int(0.25 * SR):int(0.25 * SR) + int(seconds * SR)]

    def noise_tone(self, seconds=6.0, colour=0.0):
        """Mini V3's noise alone, filter wide open. `colour` is its own
        'Pink Noise' control, 0 = white."""
        for k in ('lvl_o1', 'lvl_o2', 'lvl_o3', 'o1', 'o2', 'o3', 'lvl_ext', 'ext_sw'):
            self.set(self.I[k], 0.0)
        self.set(self.I['lvl_noise'], 0.9)
        self.set(self.I['noise_sw'], 1.0)
        self.set(77, colour)                             # Pink Noise
        self.set(self.I['cutoff'], 1.0); self.set(self.I['emphasis'], 0.0)
        y = self.render(np.zeros(1), seconds + 0.35)
        self.set(self.I['noise_sw'], 0.0); self.set(self.I['lvl_noise'], 0.0)
        return y[int(0.3 * SR):int(0.3 * SR) + int(seconds * SR)]

    def osc_level_ref(self, seconds=2.0, note=45):
        self.set(self.I['lvl_noise'], 0.0); self.set(self.I['noise_sw'], 0.0)
        return self.osc_tone("saw", note, seconds)

    def swept_cutoff(self, carrier, lo, hi, seconds, cache=None, res=0.1, amp=0.25):
        """Mini V3's cutoff knob has no units, so the sweep's endpoints come
        from the bisection calibration in `reference_compare.calibrate_knob`,
        cached. The knob's own taper between them is unknown and is NOT
        assumed to be exponential -- the measured range is reported with the
        result rather than a nominal octaves/second."""
        ka, kb = _knob(cache, self, "miniv3", lo), _knob(cache, self, "miniv3", hi)
        self.set(self.I['emphasis'], res)
        pre = int(0.30 * SR)
        n = int(seconds * SR)
        ramp = np.concatenate([np.full(pre, ka, dtype=np.float32),
                               np.linspace(ka, kb, n).astype(np.float32),
                               np.full(int(0.05 * SR), kb, dtype=np.float32)])
        self.p.set_automation(self.I['cutoff'], ramp)
        t = np.arange(len(ramp)) / SR
        x = amp * np.sin(2 * math.pi * carrier * t)
        y = self.render(x, len(ramp) / SR)
        return y[pre:pre + n]


class DivaRig(_Plugin):
    """u-he Diva, VCF model "Ladder" in 24 dB mode. Diva has NO audio input,
    so the source is its own white-noise generator, gated off after the first
    30 ms for the free-ring measurement and held on for the response
    measurement. Its transfer is therefore measured against a wide-open
    reference render, which divides out the source spectrum and the output
    path (`--validate-noise` checks that method against the stepped tone on
    Surge, where both are possible)."""
    name = "diva"
    path = PATH_DIVA
    have_input = False
    cutoff_in_hz = False
    kind = "diva"
    note = 48

    I = dict(fx1=1, fx2=2, multicore=17, accuracy=18, offlineacc=19,
             tuneslop=20, cutoffslop=21, envslop=24,
             osc_model=87, vol1=99, vol2=100, vol3=101, noisevol=124, noisecolor=125,
             hpf_model=147, hpf_freq=148, hpf_res=149,
             vcf_model=155, vcf_freq=156, vcf_res=157,
             vcf_modsrc=158, vcf_moddepth=159, vcf_mod2depth=161, keyfollow=162,
             filterfm=163, laddermode=164, laddercolor=165, feedback=168,
             shapemix=175, pan=179, volume=180, vca=181, vca_moddepth=183)
    NAMES = {1: 'Active #FX1', 2: 'Active #FX2', 17: 'MultiCore', 18: 'Accuracy',
             19: 'OfflineAcc', 20: 'TuneSlop', 21: 'CutoffSlop', 24: 'EnvrateSlop',
             87: 'Model', 99: 'Volume1', 100: 'Volume2', 101: 'Volume3',
             124: 'NoiseVol', 125: 'NoiseColor', 147: 'Model', 148: 'Frequency',
             149: 'Resonance', 155: 'Model', 156: 'Frequency', 157: 'Resonance',
             159: 'FreqModDepth', 161: 'FreqMod2Depth', 162: 'KeyFollow',
             163: 'FilterFM', 164: 'LadderMode', 165: 'LadderColor', 168: 'Feedback',
             175: 'ShapeMix', 179: 'Pan', 180: 'Volume', 181: 'VCA', 183: 'ModDepth'}
    PINS = (
        (150, 0.0, 'Revision', '1.00'),     # HPF Revision -- a filter MODEL revision
        (166, 0.0, 'SlnKyRevision', '1.00'),     # SlnKyRevision -- likewise
        (186, 0.0, 'Mode', 'lin'),      # VCA Mode: lin / simple moog / complex moog. Was never set.
        (15, 0.5, 'FineTuneCents', '0.00'),      # FineTuneCents
        (13, 0.0, 'TuningMode', None),        # TuningMode
        (122, 0.0, 'ShapeModel', 'ideal'),    # ShapeModel: ideal / analog1 / analog2
        (154, 0.0, 'Post-HPF Freq', '-1.00'),    # Post-HPF Freq
        (175, 0.0, 'ShapeMix', '0.00'),     # ShapeMix
    )
    V_LADDER, V_24DB, V_CLEAN, V_ROUGH = 0.0917, 0.2417, 0.2417, 0.75
    V_GATE, V_DIVINE, V_BEST, V_OFF, V_WHITE = 0.2417, 0.875, 0.75, 0.2417, 0.2417
    V_HPF_POST = 0.3667

    def __init__(self, color="rough", **kw):
        self.color = color
        self.name = f"diva-{color}"
        super().__init__(**kw)

    def setup(self):
        self.check_names(self.NAMES)
        I = self.I
        self.set(I['fx1'], 0.0); self.set(I['fx2'], 0.0)
        self.set(I['multicore'], 0.0)
        self.set(I['accuracy'], self.V_DIVINE)
        self.set(I['offlineacc'], self.V_BEST)
        for k in ('tuneslop', 'cutoffslop', 'envslop'):
            self.set(I[k], 0.0)                     # no voice-to-voice analogue drift
        self.set(I['vol1'], 0.0); self.set(I['vol2'], 0.0); self.set(I['vol3'], 0.0)
        self.set(I['noisevol'], 1.0)
        self.set(I['noisecolor'], self.V_WHITE)
        self.set(I['hpf_freq'], 0.0)                # HPF out of the way
        self.set(I['hpf_res'], 0.0)
        self.set(I['vcf_model'], self.V_LADDER)
        self.set(I['laddermode'], self.V_24DB)
        self.set(I['laddercolor'], self.V_ROUGH if self.color == "rough" else self.V_CLEAN)
        self.set(I['vcf_moddepth'], 0.5)            # 0 of 120
        self.set(I['vcf_mod2depth'], 0.5)
        self.set(I['keyfollow'], 0.0)
        self.set(I['filterfm'], 0.5)
        self.set(I['feedback'], 0.0)
        self.set(I['shapemix'], 0.0)
        self.set(I['pan'], 0.5)
        self.set(I['vca'], self.V_GATE)             # flat gate, no amplitude envelope
        self.set(I['vca_moddepth'], 0.5)
        self.set(I['volume'], 0.8)
        self.apply_pins()
        assert self.text(I['vcf_model']) == 'Ladder', self.text(I['vcf_model'])
        assert self.text(I['laddermode']) == '24db', self.text(I['laddermode'])
        assert self.text(I['accuracy']) == 'divine', self.text(I['accuracy'])

    def set_point(self, cut, res):
        self.set(self.I['vcf_freq'], cut)
        self.set(self.I['vcf_res'], res)
        return float(self.text(self.I['vcf_freq']))

    def noise_render(self, cut, res, seconds=1.4, level=1.0):
        """Steady white noise through the ladder, at a stated source level --
        the only drive control Diva offers, since it has no audio input."""
        self.set_point(cut, res)
        self.set(self.I['noisevol'], level)
        self.p.set_automation(self.I['noisevol'],
                              np.full(int(seconds * SR), float(level), dtype=np.float32))
        y = self.render(np.zeros(1), seconds)
        return y[int(0.35 * SR):]

    def tone_gain_db(self, freqs, cut, res, amp):
        raise NotImplementedError("Diva has no audio input; use noise_render")

    def ring(self, cut, res, seconds=1.4, amp=1.0):
        """Noise for the first 30 ms to seed the loop, then the source gated
        off: whatever is left is the filter ringing on its own."""
        self.set_point(cut, res)
        n = int(seconds * SR)
        a = np.zeros(n, dtype=np.float32)
        a[:int(0.03 * SR)] = float(amp)
        self.p.set_automation(self.I['noisevol'], a)
        y = self.render(np.zeros(1), seconds)
        return y[int(0.55 * len(y)):]

    # -- Diva's cutoff scale, and the noise-excitation transfer -------------
    # u-he reads the VCF Frequency back on a 30..150 scale that is linear in
    # the knob (30 + 120*v, checked against the readback) and ONE UNIT PER
    # SEMITONE (checked by measurement: 18 units is 2.82x in frequency, which
    # is 17.95 semitones). Anchoring it at the measured 90 -> 740.0 Hz gives
    #     f = 440 * 2 ** ((value - 81) / 12)
    # This is an inference from Diva's own readback plus a measurement, NOT a
    # figure u-he publishes, so it is checked against the measured -3 dB
    # corner in `--stage validate` and every Diva cutoff number in the
    # write-up carries that caveat.
    FREQ_ANCHOR_VALUE, FREQ_ANCHOR_HZ = 81.0, 440.0

    def freq_value(self, hz):
        value = self.FREQ_ANCHOR_VALUE + 12.0 * math.log2(hz / self.FREQ_ANCHOR_HZ)
        return float(np.clip((value - 30.0) / 120.0, 0.0, 1.0))

    def nominal_hz(self, knob):
        return self.FREQ_ANCHOR_HZ * 2.0 ** ((30.0 + 120.0 * knob
                                              - self.FREQ_ANCHOR_VALUE) / 12.0)

    def noise_curve(self, freqs, cut, res, seconds=4.0, level=1.0):
        """Transfer by noise excitation: the filter's own white noise through
        the ladder at (cut, res), divided by the SAME source through the ladder
        wide open. The ratio removes the source spectrum and the output path;
        what is left is the filter, at a stated drive.

        Diva has no audio input, so this is the only way to measure it. The
        method's agreement with the stepped tone is checked on Surge, where
        both are possible (`--stage validate`); it is not assumed."""
        num = self.noise_render(cut, res, seconds, level)
        den = self._wide_open(seconds, level)
        fn, pn = _welch(num)
        _, pd = _welch(den)
        g = 10 * np.log10(np.maximum(pn, 1e-30) / np.maximum(pd, 1e-30))
        return _smooth_log(fn, g, freqs)

    def _wide_open(self, seconds=4.0, level=1.0):
        key = (round(seconds, 3), round(level, 6))
        if getattr(self, "_wo_key", None) != key:
            self._wo = self.noise_render(1.0, 0.0, seconds, level)
            self._wo_key = key
        return self._wo

    def drive_tone(self, f, cut, res, amp):
        raise NotImplementedError(
            "Diva has 0 audio input channels, so no known signal can be put into its "
            "filter: the drive measurement is not answerable against it")

    V_DIGITAL, V_DIGI_TRI = 0.9, 0.9333

    def swept_cutoff(self, carrier, lo, hi, seconds, cache=None, res=0.05, amp=1.0):
        """Diva has no audio input, so the carrier is its own Digital
        oscillator set to a triangle and tuned to `carrier` by MIDI note --
        near enough to a sine that the envelope is clean, and its own
        harmonics sit above the band limit the ripple measure applies."""
        note = int(round(69 + 12 * math.log2(carrier / 440.0)))
        self.note = note
        self.set(self.I['osc_model'], self.V_DIGITAL)
        self.set(144, self.V_DIGI_TRI)                 # DigitalType1 = Triangle
        self.set(self.I['vol1'], 1.0)
        self.set(self.I['noisevol'], 0.0)
        self.set(self.I['vcf_res'], res)
        pre = int(0.30 * SR)
        n = int(seconds * SR)
        ramp = np.concatenate([np.full(pre, self.freq_value(lo), dtype=np.float32),
                               np.linspace(self.freq_value(lo), self.freq_value(hi),
                                           n).astype(np.float32),
                               np.full(int(0.05 * SR), self.freq_value(hi), dtype=np.float32)])
        self.p.set_automation(self.I['vcf_freq'], ramp)
        self.p.set_automation(self.I['noisevol'],
                              np.zeros(len(ramp), dtype=np.float32))
        y = self.render(np.zeros(1), len(ramp) / SR)
        return y[pre:pre + n]


def _knob(cache, dev, name, hz):
    """The knob position that commands `hz` on a unitless control, bisected
    against the device's own self-oscillation frequency and cached."""
    import reference_compare as rc
    key = f"{name}-{hz:.0f}"
    if cache is None:
        return rc.calibrate_knob(dev, name, hz)
    if key not in cache:
        cache[key] = rc.calibrate_knob(dev, name, hz)
    return cache[key]


def _welch(x, nfft=8192):
    """Power spectrum, Hann windows, 50 % overlap, averaged. `len(x)/nfft*2`
    segments, so the per-bin standard error is 1/sqrt(that) -- about 0.9 dB
    for a 4 s record, taken down under 0.2 dB by the 1/12-octave smoothing
    `_smooth_log` applies after."""
    x = np.asarray(x, dtype=np.float64)
    w = np.hanning(nfft)
    step = nfft // 2
    segs = [np.abs(np.fft.rfft(x[i:i + nfft] * w)) ** 2
            for i in range(0, len(x) - nfft + 1, step)]
    if not segs:
        raise ValueError("record shorter than one FFT window")
    return np.fft.rfftfreq(nfft, 1.0 / SR), np.mean(segs, axis=0)


def _smooth_log(f, g_db, at, frac: float = 1 / 12.0):
    """Average a dB curve over a fractional-octave band at each requested
    frequency. In dB (the quantity being compared), and the band is stated."""
    out = []
    for fc in at:
        lo, hi = fc * 2 ** (-frac / 2), fc * 2 ** (frac / 2)
        sel = (f >= lo) & (f <= hi)
        if sel.sum() < 3:
            i = int(np.argmin(np.abs(f - fc)))
            sel = np.zeros(len(f), bool)
            sel[max(0, i - 1):i + 2] = True
        out.append(float(np.mean(g_db[sel])))
    return np.array(out)


PATH_MODELD = f"{VST3}/Model D.vst3"


class ModelDRig(_Plugin):
    """**Moog's own Minimoog Model D.** Not another third-party interpretation:
    written by the people with the schematics, with the brand's name on it.

    It cannot be read, so it is not the kind of evidence Surge is. But a
    disagreement with it is a different kind of finding from a disagreement
    with Mini V3 or Diva -- it is the closest thing to an authoritative
    statement about what a Model D should sound like that can be driven
    programmatically. **It gets its own verdict and is never averaged into a
    "reference spread".**

    Every parameter is a bare 0..1 whose text is the raw value -- no units, no
    enumerated names -- so its cutoff knob is a knob and is calibrated by
    bisecting against its own self-oscillation, exactly as Mini V3's is.
    Commanded-cutoff accuracy is NOT answerable against it.

    0 audio input channels, so no external signal can be put through its
    filter; its own noise generator is the broadband source.
    """
    name = "modeld"
    path = PATH_MODELD
    have_input = False
    cutoff_in_hz = False
    kind = "modeld"
    note = 48

    I = dict(legato=0, poly=1, contour_shape=2, tune=3, glide_rate=4, mod_mix=5,
             mod_o3_feg=6, mod_noise_lfo=7, osc_mod=8, osc3_ctl=9,
             o1_range=10, o1_wave=11, o2_range=12, o2_tune=13, o2_wave=14,
             o3_range=15, o3_tune=16, o3_wave=17,
             o1_vol=18, o2_vol=19, o3_vol=20, ext_vol=21, noise_vol=22,
             o1_on=23, o2_on=24, o3_on=25, ext_on=26, noise_on=27, noise_color=28,
             filt_mod=29, kbd1=30, kbd2=31, cutoff=32, emphasis=33, contour_amt=34,
             feg_a=35, feg_d=36, feg_s=37, aeg_a=38, aeg_d=39, aeg_s=40,
             master=41, lfo_rate=42, lfo_wave=43, glide_on=44, decay_on=45,
             arp_on=46, key_hold=52, bender_on=53)
    NAMES = {0: 'Legato', 1: 'Polyphonic', 2: 'Contour Shape', 3: 'Tune',
             4: 'Glide Rate', 10: 'Osc 1 Range', 11: 'Osc 1 Wave',
             18: 'Osc 1 Volume', 21: 'Ext. Input Volume', 22: 'Noise Volume',
             23: 'Osc 1 Enabled', 26: 'Ext. Input Enabled', 27: 'Noise Enabled',
             28: 'Noise Color', 32: 'Cutoff Frequency', 33: 'Filter Emphasis',
             34: 'Amount Of Contour', 35: 'Filter Contour Attack',
             38: 'Loudness Contour Attack', 40: 'Loudness Contour Sustain',
             41: 'Master Volume', 44: 'Glide Enabled', 45: 'Decay Enabled',
             46: 'Arp Enabled', 52: 'Key Hold'}
    # Every sound-changing control that is NOT the thing under test, written
    # down rather than defaulted. Model D's text is the raw 0..1 value, so the
    # expected readback is that number.
    PINS = (
        (0, 0.0, 'Legato', '0.00'),
        (1, 0.0, 'Polyphonic', '0.00'),
        (2, 0.0, 'Contour Shape', '0.00'),
        (3, 0.5, 'Tune', '0.50'),
        (5, 0.0, 'Modulation Mix', '0.00'),
        (6, 0.0, 'Mod. Osc 3 Filter EG', '0.00'),
        (7, 0.0, 'Mod. Noise LFO', '0.00'),
        (8, 0.0, 'Osc Modulation', '0.00'),
        (29, 0.0, 'Filter Modulation', '0.00'),
        (30, 0.0, 'Keyboard Control 1', '0.00'),
        (31, 0.0, 'Keyboard Control 2', '0.00'),
        (34, 0.0, 'Amount Of Contour', '0.00'),
        (44, 0.0, 'Glide Enabled', '0.00'),
        (45, 0.0, 'Decay Enabled', '0.00'),
        (46, 0.0, 'Arp Enabled', '0.00'),
        (52, 0.0, 'Key Hold', '0.00'),
        (53, 0.0, 'Bender Enabled', '0.00'),
    )

    def setup(self):
        self.check_names(self.NAMES)
        I = self.I
        for k in ('o1_vol', 'o2_vol', 'o3_vol', 'ext_vol', 'noise_vol',
                  'o1_on', 'o2_on', 'o3_on', 'ext_on', 'noise_on'):
            self.set(I[k], 0.0)
        self.set(I['feg_a'], 0.0); self.set(I['feg_d'], 0.0); self.set(I['feg_s'], 1.0)
        self.set(I['aeg_a'], 0.0); self.set(I['aeg_d'], 1.0); self.set(I['aeg_s'], 1.0)
        self.set(I['master'], 0.8)
        self.set(I['noise_color'], 0.0)          # calibrated by measurement, not assumed
        self.apply_pins()

    def set_point(self, cut, res):
        self.set(self.I['cutoff'], cut)
        self.set(self.I['emphasis'], res)
        return cut

    def ring(self, cut, res, seconds=1.2, amp=0.25):
        """Self-oscillation: every mixer source off, emphasis past threshold,
        one key held. The Model D's own filter singing on its own -- which is
        the one measurement `model/moog_probe.py` was built for."""
        self.set_point(cut, res)
        for k in ('o1_on', 'o2_on', 'o3_on', 'ext_on', 'noise_on'):
            self.set(self.I[k], 0.0)
        self.silence_state(0.3)
        y = self.render(np.zeros(1), seconds + 0.1)
        return y[int(0.55 * len(y)):]

    def noise_render(self, cut, res, seconds=1.4, level=1.0):
        self.set_point(cut, res)
        self.set(self.I['noise_on'], 1.0)
        self.set(self.I['noise_vol'], level)
        y = self.render(np.zeros(1), seconds)
        self.set(self.I['noise_on'], 0.0)
        return y[int(0.35 * SR):]

    def noise_curve(self, freqs, cut, res, seconds=4.0, level=1.0):
        num = self.noise_render(cut, res, seconds, level)
        den = self._wide_open(seconds, level)
        fn, pn = _welch(num)
        _, pd = _welch(den)
        g = 10 * np.log10(np.maximum(pn, 1e-30) / np.maximum(pd, 1e-30))
        return _smooth_log(fn, g, freqs)

    def _wide_open(self, seconds=4.0, level=1.0):
        key = (round(seconds, 3), round(level, 6))
        if getattr(self, "_wo_key", None) != key:
            self._wo = self.noise_render(1.0, 0.0, seconds, level)
            self._wo_key = key
        return self._wo

    def drive_tone(self, f, cut, res, amp):
        raise NotImplementedError(
            "Model D has 0 audio input channels: no known signal can be put through its "
            "filter, so the drive measurement is not answerable against it")

    def swept_cutoff(self, carrier, lo, hi, seconds, cache=None, res=0.05, amp=0.25):
        ka, kb = _knob(cache, self, "modeld", lo), _knob(cache, self, "modeld", hi)
        self.set(self.I['emphasis'], res)
        self.set(self.I['o1_on'], 1.0); self.set(self.I['o1_vol'], 0.9)
        self.set(self.I['o1_wave'], 0.0)                 # calibrated by measurement
        pre, n = int(0.30 * SR), int(seconds * SR)
        ramp = np.concatenate([np.full(pre, ka, dtype=np.float32),
                               np.linspace(ka, kb, n).astype(np.float32),
                               np.full(int(0.05 * SR), kb, dtype=np.float32)])
        self.p.set_automation(self.I['cutoff'], ramp)
        y = self.render(np.zeros(1), len(ramp) / SR)
        return y[pre:pre + n]


# ===========================================================================
# A SECOND HOST: pedalboard
# ===========================================================================
# Issue #124. Which host works differs PER PLUGIN, and that is measured, not
# assumed -- on the same machine, the same binaries and the same note:
#
#                 pedalboard                 dawdreamer 0.9.0
#   Model D       peak 1.000000  SOUND       peak 0.0  SILENT
#   Mini V3       peak 0.000000  SILENT      sounds
#
# So the rig layer has to support both and record which one produced a clip
# (#123). What follows is a SIBLING of `_Plugin`, not a fork of it: it
# subclasses `_Plugin` and overrides exactly the three methods that touch the
# host --
#
#     __init__          loads the plugin
#     render            renders one note
#     silence_state     settles the plugin without a note
#
# -- and inherits `set`, `text`, `apply_pins`, `pinned_report`, `check_pins`
# and `check_names` VERBATIM, so the pin discipline is one implementation and
# not two that have to be kept in step. That inheritance is what the
# host-shaped adapter below exists to buy.
#
# WHAT IS GENUINELY DIFFERENT UNDER THIS HOST, and both differences are
# properties of the reference, not of the code:
#
#   * **state does not carry between renders.** `pedalboard`'s `process` takes
#     `reset=True`, so each render starts from a clean plugin. Under
#     dawdreamer the engine carries filter and voice state forward, which is
#     why `SurgeRig.select_osc` has to settle with NO note -- calling `render`
#     stacks note-ons and leaves the previous voice decaying into the next
#     measurement. Here that cannot happen. Every clip is independent, which
#     is better, and it means a `silence_state` call is belt and braces rather
#     than load-bearing.
#   * **there is no parameter automation.** `pedalboard` sets a parameter and
#     it holds; there is no per-block ramp. So the swept-cutoff measurement is
#     NOT answerable under this host, and `swept_cutoff` refuses rather than
#     faking a ramp out of stitched renders. The 94 Hz artefact that made three
#     plugins appear to step identically was dawdreamer's per-block automation
#     at a 512-sample block; under pedalboard the block size still matters (it
#     is the processing chunk, and it is recorded), but it cannot manufacture
#     that particular artefact because nothing here automates.
class _PedalboardParams:
    """dawdreamer's BY-INDEX parameter interface, over a `pedalboard` plugin.

    `pedalboard` addresses parameters by sanitised Python name
    (`plugin.parameters["cutoff_frequency"]`); `reference_rigs`' whole pin
    discipline is keyed by INDEX, because an index is what a `PINS` table can
    pin and a name is what the hazard moves (Surge renames 259-267 by
    oscillator type). `pedalboard` exposes the index on each parameter object,
    so this adapter presents the same five calls dawdreamer does and the pin
    code above runs unchanged over it.

    Two preconditions are asserted here rather than assumed, because an
    index-keyed pin table means nothing without them:

      * the indices must be UNIQUE -- two parameters claiming index 32 makes
        every pin at 32 a coin toss
      * they must cover 0..n-1 with no gaps -- a host that hides some
        parameters renumbers the rest, and a `PINS` table measured under the
        other host would then be pinning different controls with no error
    """

    def __init__(self, plugin):
        raw = getattr(plugin, "_parameters", None)
        if not raw:
            raw = list(getattr(plugin, "parameters", {}).values())
        if not raw:
            raise RuntimeError("the plugin exposes no parameters, so nothing can be "
                               "pinned and no rig can be qualified against it")
        by_index: dict[int, object] = {}
        for prm in raw:
            i = int(prm.index)
            if i in by_index:
                raise RuntimeError(
                    f"two parameters report index {i} ({by_index[i].name!r} and "
                    f"{prm.name!r}): an index-keyed pin table cannot be trusted here")
            by_index[i] = prm
        n = len(by_index)
        missing = [i for i in range(n) if i not in by_index]
        if missing:
            raise RuntimeError(
                f"the plugin reports {n} parameters whose indices are not 0..{n - 1} "
                f"({len(missing)} missing, first {missing[0]}): a pin table measured "
                f"under another host would be pinning different controls here")
        self._by_index = by_index
        self._plugin = plugin

    # -- the dawdreamer-shaped calls the pin code uses ----------------------
    def parameter(self, i):
        try:
            return self._by_index[int(i)]
        except KeyError:
            raise IndexError(f"no parameter at index {i} (the plugin has "
                             f"{len(self._by_index)})") from None

    def set_parameter(self, i, v):
        self.parameter(i).raw_value = float(v)

    def get_parameter(self, i):
        return float(self.parameter(i).raw_value)

    def get_parameter_text(self, i):
        return str(self.parameter(i).string_value)

    def get_parameter_name(self, i):
        return str(self.parameter(i).name)

    def get_parameters_description(self):
        return [{"index": i, "name": p.name, "text": p.string_value,
                 "num_steps": getattr(p, "num_steps", None),
                 "is_discrete": bool(getattr(p, "is_discrete", False))}
                for i, p in sorted(self._by_index.items())]

    def get_latency_samples(self):
        return int(getattr(self._plugin, "reported_latency_samples", 0) or 0)

    # -- the readback this host can assert and the other one cannot ---------
    def raw_tolerance(self, i) -> float:
        """How far a written raw value may legitimately land from where it was
        put. A DISCRETE parameter snaps to its own steps, so half a step is the
        honest tolerance; a continuous one has none to speak of.

        This is not slack for a plugin that ignores a write: half a step is the
        largest error a correct snap can produce, and anything larger is a
        write that did not take."""
        p = self.parameter(i)
        steps = int(getattr(p, "num_steps", 0) or 0)
        if bool(getattr(p, "is_discrete", False)) and steps > 1:
            return 0.5 / (steps - 1)
        return 1e-6


#: Where a VST3 bundle keeps its binary, per platform. A `.vst3` bundle is a
#: DIRECTORY, so `os.path.exists` is true for one that holds nothing a host can
#: load -- which is why the check below is a layout check and not an existence
#: check.
VST3_BINARY_DIR = {
    "darwin": "Contents/MacOS",
    "linux": "Contents/x86_64-linux",
    "win32": "Contents/x86_64-win",
}


def bundle_diagnosis(path: str) -> str:
    """Why a `.vst3` bundle at `path` could not be loaded, in the terms an
    operator can act on: which platform sub-directory this platform needs, which
    one the bundle actually has, and how big the binary is.

    THIS IS CALLED ONLY AFTER A LOAD HAS ALREADY FAILED. It is a diagnosis, not
    a precondition, and it deliberately does not gate the happy path: a host can
    legitimately load a layout this function does not know about, and a bundle
    check that rejected such a plugin would be an unsatisfiable gate.

    Written because of a bundle found on a Linux dispatch worker at
    `/tmp/mdb/Model D.vst3` on 2026-09-30: correct name, plausible directory
    tree, **one byte** of content (the single character `x`, mode 644), in the
    *macOS* layout on a Linux host. `pathlib.Path(...).exists()` is True for it
    and `pedalboard.load_plugin` answers only `unsupported plugin format or scan
    failure`, which reads like a missing host rather than a bogus file. That
    combination is how a stub gets mistaken for an uninstalled plugin, and the
    reverse.
    """
    want = VST3_BINARY_DIR.get(sys.platform)
    bits = [f"sys.platform is {sys.platform!r}"]
    if want is None:
        bits.append("no VST3 bundle layout is known for this platform, so the "
                    "layout cannot be checked here")
    else:
        d = os.path.join(path, want)
        bits.append(f"a VST3 bundle for it needs {want}/, which is "
                    f"{'present' if os.path.isdir(d) else 'ABSENT'}")
    present = sorted(
        rel for rel in VST3_BINARY_DIR.values()
        if os.path.isdir(os.path.join(path, rel)))
    if present:
        bits.append("the bundle carries " + ", ".join(f"{p}/" for p in present))
        if want is not None and want not in present:
            bits.append("so this bundle was built for ANOTHER PLATFORM and no "
                        "host on this one can load it")
    else:
        bits.append("the bundle carries no platform binary directory at all")
    sizes = []
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                sizes.append(os.path.getsize(os.path.join(root, f)))
            except OSError:
                pass
    if not sizes:
        bits.append("and contains NO FILES")
    else:
        big = max(sizes)
        bits.append(f"its largest of {len(sizes)} file(s) is {big} bytes")
        if big < 4096:
            bits.append("which is far too small to be a plugin binary: this is a "
                        "STUB or a truncated download, not an installed plugin")
    return "; ".join(bits)


class _PedalboardPlugin(_Plugin):
    """One `pedalboard`-hosted instrument plugin, driven by MIDI, with the pin
    discipline of `_Plugin` and the signal-side battery of
    `model/rig_qualification.py` on top.

    `qualify()` here is a SUPERSET of `_Plugin.qualify()`, never a replacement:
    it still refuses a rig whose pinned settings do not hold by name and by
    readback, and it additionally refuses one whose output is silent, clipped,
    an octave out, unidentifiable, or whose commands cause no effect (#137).
    The record is on `self.qualification` and is attached to the refusal, so a
    rig that cannot be qualified still produces evidence rather than a
    traceback.
    """

    host = "pedalboard"

    #: The rig must STATE its licence position rather than leave it implied.
    #: There is no licence probe for any plugin in this repository: the one
    #: licence finding we have (`docs/reference-integrity.md` section 1, Diva
    #: inserting clicks) was found by hearing the clicks, not by asking the
    #: plugin. So the honest default is "unverified", and a rig that means
    #: something else says so.
    licence = {"state": "unverified",
               "how": "no licence probe exists for any plugin here. The only licence "
                      "finding in this repository (docs/reference-integrity.md "
                      "section 1) was an unlicensed Diva inserting clicks, found by "
                      "hearing them. 'unverified' is therefore the measured position "
                      "and not a placeholder"}

    def __init__(self, quiet=True, block=BLOCK, sr=SR):
        """`block` is `pedalboard`'s `buffer_size`: the chunk the plugin is
        processed in. It is pinned to the SAME 512 the dawdreamer rigs use, and
        it is pinned rather than defaulted because `pedalboard`'s own default is
        8192 -- a 5.86 Hz chunk rate. A host chunk rate that lands inside the
        analysis band is how three unrelated plugins once appeared to step
        identically at 94 Hz, which was dawdreamer's 512-sample block and not a
        plugin defect. It is recorded with every clip (#123)."""
        import pedalboard
        self._pb = pedalboard
        self.block, self.sr = int(block), int(sr)
        if not os.path.exists(self.path):
            raise rq.RigRefusal(f"{self.name}: no plugin bundle at {self.path}")
        # PRECONDITION: the bundle must actually LOAD. A `.vst3` bundle is a
        # directory, so the existence check above passes for one that holds a
        # single byte -- and `pedalboard.load_plugin` then raises `ImportError`,
        # which escaped this constructor uncaught until 2026-09-30. Two things
        # were wrong with that: a traceback is not one of this repository's three
        # outcomes, so the caller could not report REFUSED; and the exception
        # TYPE is the same one `import pedalboard` raises when the host is not
        # installed at all, so "your bundle is a stub" and "you have no
        # pedalboard" arrived indistinguishable. Both are refusals, and they are
        # different refusals.
        try:
            self.plugin = pedalboard.load_plugin(self.path)
        except Exception as e:
            raise rq.RigRefusal(
                f"{self.name}: a bundle exists at {self.path} but this host could "
                f"not load it -- {type(e).__name__}: {e}. "
                f"{bundle_diagnosis(self.path)}. Nothing was measured, so this "
                f"says nothing about the plugin") from e
        # PRECONDITION: an INSTRUMENT. An effect plugin loaded here would be
        # handed MIDI, return its own silence, and every check downstream would
        # be measuring the absence of a synthesiser.
        if not bool(getattr(self.plugin, "is_instrument", False)):
            raise rq.RigRefusal(
                f"{self.name}: {self.path} loads as an effect, not an instrument "
                f"(is_instrument is False): MIDI cannot drive it")
        self.p = _PedalboardParams(self.plugin)
        self.qualification = None
        self.setup()
        self.qualify()

    # -- host ---------------------------------------------------------------
    def render(self, x, seconds, note_at=0.02, note_len=None):
        """`seconds` of audio with one note held, the plugin's reported latency
        removed, mono (channel 0).

        `x` exists only to keep the signature identical to `_Plugin.render`, so
        code written against one host runs against the other. A pedalboard
        instrument has no audio input, so a NON-ZERO `x` is refused rather than
        silently dropped -- a measurement that thinks it put a known signal
        through a filter and did not is the exact shape of the bench that drove
        the wrong port."""
        x = np.asarray(x, dtype=np.float64).ravel()
        if x.size and float(np.abs(x).max()) > 0.0:
            raise rq.RigRefusal(
                f"{self.name}: a pedalboard instrument has no audio input, so the "
                f"{x.size}-sample stimulus handed to render() would be discarded. "
                f"Nothing here can put a known signal through this plugin's filter")
        dur = float(seconds)
        held = dur if note_len is None else float(note_len)
        off = min(float(note_at) + held, dur)
        msgs = [([0x90, int(self.note), 100], float(note_at)),
                ([0x80, int(self.note), 0], float(off))]
        a = self.plugin(msgs, duration=dur, sample_rate=float(self.sr),
                        num_channels=2, buffer_size=int(self.block), reset=True)
        y = np.asarray(a, dtype=np.float64)
        if y.ndim > 1:
            y = y[0]
        lat = self.p.get_latency_samples()
        return y[lat:] if lat else y

    def silence_state(self, seconds=0.4):
        """Render with NO note, which under this host also makes the parameter
        readbacks current. `reset=True` already clears state between renders,
        so unlike the dawdreamer path this is not how state is cleared -- it is
        how a plugin whose text readback lags its processor (a Diva cutoff that
        read 90 for a whole session) is made to tell the truth."""
        a = self.plugin([], duration=float(seconds), sample_rate=float(self.sr),
                        num_channels=2, buffer_size=int(self.block), reset=True)
        y = np.asarray(a, dtype=np.float64)
        return y[0] if y.ndim > 1 else y

    def swept_cutoff(self, carrier, lo, hi, seconds, cache=None, res=0.05, amp=0.25):
        raise NotImplementedError(
            "pedalboard has no parameter automation: a swept cutoff cannot be "
            "commanded under this host, and stitching one out of per-block renders "
            "would measure the stitching. Use the dawdreamer rig for the movement "
            "study")

    # -- the readback this host can assert ----------------------------------
    def written_pin_problems(self) -> list:
        """Every pin the rig WROTE, read back from the plugin twice and by two
        routes: the raw value, and the plugin's own text for the value it is
        actually holding.

        This is the check `_Plugin.check_pins` cannot make and it is not a
        duplicate of it. `check_pins` asserts a readback STRING measured under
        dawdreamer, and a string is the host's formatting of the plugin's
        answer -- inheriting one across hosts would be pinning a measurement
        nobody took here. This asserts instead that

          * the write took effect (`raw_value` came back where it was put,
            within `raw_tolerance`: half a step for a discrete control, which
            is the largest error a correct snap can produce)
          * the two readback routes AGREE. `string_value` is what the plugin
            says it is holding now; `get_text_for_raw_value(held)` is what it
            says that value means. A plugin whose text lags its processor
            disagrees between them -- which is precisely the Diva cutoff that
            read 90 for a session -- and a disagreement here is a refusal, not
            a note.

        Returns [(index, kind, expected, got, tolerance), ...]."""
        bad = []
        for idx, val, _name, _want in self.PINS:
            if val is None:
                continue
            prm = self.p.parameter(idx)
            held = float(prm.raw_value)
            tol = self.p.raw_tolerance(idx)
            if abs(held - float(val)) > tol:
                bad.append((int(idx), "RAW", float(val), held, tol))
                continue
            try:
                want_text = str(prm.get_text_for_raw_value(held))
            except Exception as e:                                  # pragma: no cover
                bad.append((int(idx), "TEXT-UNREADABLE", f"{type(e).__name__}: {e}",
                            None, None))
                continue
            got_text = str(prm.string_value)
            if got_text != want_text:
                bad.append((int(idx), "TEXT", want_text, got_text, None))
        return bad

    def written_pins_check(self):
        return rq.check_pins(self.written_pin_problems,
                             n_pins=sum(1 for _i, v, _n, _w in self.PINS if v is not None),
                             name="pins written (raw readback)")

    # -- qualification ------------------------------------------------------
    #: The window of the record a periodic estimator may read: past the attack,
    #: clear of the release. Stated as numbers because "the steady part" is not
    #: a measurement.
    STEADY_FROM_S, STEADY_LEN_S = 0.10, 0.40
    RENDER_S = 0.70
    CUTOFF_KNOBS = (0.3, 0.5, 0.7, 0.9)

    def steady(self, y):
        a = int(self.STEADY_FROM_S * self.sr)
        n = int(self.STEADY_LEN_S * self.sr)
        return np.asarray(y, dtype=np.float64).ravel()[a:a + n]

    def render_note(self, note: int, seconds: float | None = None):
        """One note, at the rig's current patch. `note` is written to
        `self.note` so `render` sends it, and restored after."""
        was, self.note = self.note, int(note)
        try:
            return self.render(np.zeros(1), self.RENDER_S if seconds is None else seconds)
        finally:
            self.note = was

    def render_cutoff(self, knob: float, seconds: float | None = None):
        raise NotImplementedError

    def qualify(self):
        """`_Plugin.qualify()`'s contract, plus the signal-side battery.

        The record is built whatever the outcome and stored on
        `self.qualification` BEFORE anything is raised, so a rig that cannot be
        qualified is a finding with numbers in it. `RigRefusal` carries the same
        record."""
        self.silence_state(0.05)
        checks = [rq.check_pins(self.check_pins, n_pins=len(self.PINS)),
                  self.written_pins_check()]
        checks.extend(self.patch_checks())
        battery = rq.qualify_voice(
            rig=self.name, host=self.host, render_note=self.render_note,
            render_cutoff=self.render_cutoff, note_hz=vf.note_hz, note=self.note,
            sr=self.sr, expect_wave=self.EXPECT_WAVE, steady=self.steady,
            cutoff_knobs=self.CUTOFF_KNOBS)
        checks.extend(battery.checks)
        self.qualification = rq.Qualification(self.name, self.host, tuple(checks))
        self.qualification.require()

    EXPECT_WAVE = None

    def patch_checks(self) -> list:
        """Checks that have to run between `setup()` and the battery -- the
        calibrations a rig needs in order to be in a state worth measuring at
        all. Default: none."""
        return []


class ModelDPedalboardRig(_PedalboardPlugin):
    """**Moog's own Minimoog Model D, hosted through `pedalboard`.**

    The same plugin as `ModelDRig` and a DIFFERENT rig, because the host
    changed the answer: under `dawdreamer` 0.9.0 this bundle renders a buffer
    whose peak is exactly 0.0, and under `pedalboard` it renders peak 1.000
    with 8.57 % of samples at the rail and its strongest partial at 131.00 Hz
    for MIDI 60 -- an octave below the 261.63 Hz that was commanded
    (`refprofile/README.md`). Both of those are measurements on record, and
    both of them are reasons the audio cannot be frozen as it comes.

    So this rig does not accept the plugin's default patch. It tries to CORRECT
    both defects through Model D's own parameters and measures whether it
    worked:

      * `calibrate_range` sweeps Osc 1 Range across its own discrete positions
        and MEASURES the fundamental at each, then selects the position that
        sounds the commanded note. The mapping is not looked up, and it is not
        borrowed from Mini V3 (whose 8' is 0.575): a value that happens to work
        on a different vendor's emulation of the same panel is a guess.
      * `trim_level` steps the master volume down over a stated grid and takes
        the LOUDEST setting that puts zero samples at the rail, so the clip is
        as loud as it can be without having lost anything.

    Either search can fail to find a position, and if it does this rig REFUSES.
    That refusal is issue #124's own sequencing gate and it is a complete
    outcome: it says the Mono cases cannot use Model D as their reference under
    this host either, with the sweep table as the evidence, and it sends the
    re-specification question in #122 back open. It is NOT an error to route
    around by keeping the octave-down clipped default and calling it a
    reference.

    The note is MIDI 60, not the 48 the dawdreamer rig uses, so this rig is
    measured at the note the pedalboard finding was measured at.
    """
    name = "modeld-pedalboard"
    kind = "modeld"
    path = PATH_MODELD
    have_input = False
    cutoff_in_hz = False
    note = 60

    # Taken from the dawdreamer rig rather than retyped: the index map and the
    # parameter NAMES are properties of the plugin and are the same under both
    # hosts. The readback STRINGS are not -- see PINS below.
    I = dict(ModelDRig.I)
    NAMES = dict(ModelDRig.NAMES)

    #: The same indices and the same expected NAMES as `ModelDRig.PINS`, with
    #: every expected readback string dropped to `None`.
    #:
    #: **This is deliberate and it is a gap, stated rather than papered over.**
    #: A readback string is the host's rendering of the plugin's own answer;
    #: `ModelDRig.PINS` pins '0.00' because that is what dawdreamer returned
    #: when somebody measured it. Copying those strings here would assert a
    #: measurement nobody has taken under this host, and if `pedalboard`
    #: returned '0' or 'Off' the rig would refuse for a formatting difference
    #: and call it a parameter drift.
    #:
    #: What replaces it is NOT weaker: `written_pin_problems` asserts, for
    #: every pin, that the raw value came back where it was written and that
    #: the plugin's two readback routes agree with each other. That holds
    #: without anyone having measured a string first. The observed strings are
    #: recorded in the qualification record, so the first qualifying run on an
    #: operator's machine is what fills this column in -- by measurement.
    PINS = tuple((i, v, n, None) for i, v, n, _w in ModelDRig.PINS)

    #: Written down rather than defaulted, and restated here because the
    #: dawdreamer rig's `setup` deliberately leaves every mixer source OFF (it
    #: exists to measure self-oscillation) while this one has to make a note.
    OSC1_LEVEL = 0.9
    MASTER_START = 0.8
    #: The master-volume grid `trim_level` searches, loudest first. Explicit
    #: rather than a bisection: a bisection on a control whose taper is unknown
    #: reports a position nobody can reproduce, and twelve renders is cheap.
    MASTER_GRID = (0.80, 0.70, 0.60, 0.50, 0.42, 0.35, 0.30, 0.25, 0.20, 0.16,
                   0.12, 0.09, 0.06)
    #: Osc 1 Range, swept rather than looked up. The Model D panel has LO, 32',
    #: 16', 8', 4' and 2'; which normalised value selects which is exactly what
    #: is measured, so the grid is a fine scan of the whole control and the
    #: answer comes from the fundamental it produces.
    RANGE_GRID = tuple(round(i / 24.0, 6) for i in range(25))
    #: How close the measured fundamental has to be to the commanded note for a
    #: Range position to count as the right one. 50 cents is `refine_f0`'s own
    #: window: past a quarter tone the rig is playing a different note.
    RANGE_MAX_CENTS = 50.0

    #: No wave-selector mapping for this plugin has been measured on any host,
    #: so the waveform check requires the record to be IDENTIFIABLE and does
    #: not require a particular label. Asserting a label here would be
    #: asserting the mapping -- `SurgeRig` asked for a saw, received a 50 %
    #: pulse and published it for a whole study. The identified label is
    #: recorded, which is how the mapping gets measured.
    EXPECT_WAVE = None

    # `setup` is `ModelDRig`'s, taken by reference and not copied: it writes
    # only through `self.set` / `self.check_names` / `self.apply_pins`, all of
    # which are host-agnostic, so it is literally the same patch under both
    # hosts. The voice patch on top of it is this rig's own, because the
    # dawdreamer rig's whole purpose is a silent mixer.
    _base_setup = ModelDRig.setup

    def setup(self):
        type(self)._base_setup(self)
        self.voice_patch()

    def voice_patch(self, cutoff: float = 1.0, emphasis: float = 0.0):
        """Oscillator 1 alone, filter wide open, flat gate: one note and
        nothing else in the path."""
        I = self.I
        for k in ('o2_on', 'o3_on', 'ext_on', 'noise_on',
                  'o2_vol', 'o3_vol', 'ext_vol', 'noise_vol'):
            self.set(I[k], 0.0)
        self.set(I['o1_on'], 1.0)
        self.set(I['o1_vol'], self.OSC1_LEVEL)
        self.set(I['cutoff'], float(cutoff))
        self.set(I['emphasis'], float(emphasis))
        self.set(I['master'], self.MASTER_START)

    def render_cutoff(self, knob: float, seconds: float | None = None):
        """One note at one cutoff-knob position, emphasis at zero, everything
        else as the patch left it. The cutoff is restored afterwards so a sweep
        cannot leave the rig somewhere it was not measured."""
        was = self.p.get_parameter(self.I['cutoff'])
        self.set(self.I['cutoff'], float(knob))
        self.set(self.I['emphasis'], 0.0)
        try:
            return self.render_note(self.note, seconds)
        finally:
            self.set(self.I['cutoff'], was)

    # -- the two corrections, each measured -------------------------------
    def calibrate_range(self) -> rq.Check:
        """Sweep Osc 1 Range and MEASURE the fundamental at each position;
        select the one that sounds the commanded note.

        Every row of the sweep goes into the record, including the rejected
        ones, because "the octave default cannot be corrected" is only a
        finding if the positions that were tried are visible. `refine_f0` is
        the estimator, so a position whose second harmonic sits on the
        commanded note is rejected rather than accepted -- that is the exact
        failure mode this control has.

        Selection rule, stated before the sweep runs: among the positions
        inside `RANGE_MAX_CENTS`, the one whose measured fundamental is CLOSEST
        to the commanded note. Ties cannot occur (the metric is a float) and a
        rule chosen after seeing the table would not be a rule."""
        cmd = float(vf.note_hz(self.note))
        idx = self.I['o1_range']
        was = self.p.get_parameter(idx)
        rows = []
        try:
            for v in self.RANGE_GRID:
                self.set(idx, float(v))
                # What the plugin HOLDS, not what was written to it. A discrete
                # control snaps, so the two differ, and a table of commanded
                # values would name positions the plugin was never in -- the
                # same reason every readback in this file is read back.
                held = float(self.p.get_parameter(idx))
                y = self.steady(self.render_note(self.note))
                pk = float(np.abs(y).max()) if y.size else 0.0
                row = {"raw": float(v), "raw_held": held, "text": self.text(idx),
                       "peak": pk}
                if y.size and pk > rq.SILENCE_FLOOR:
                    e = am.refine_f0(y, cmd, self.sr, max_cents=self.RANGE_MAX_CENTS)
                    row["f0_hz"] = float(e.value) if e.ok else None
                    row["cents"] = (float(e.detail.get("cents")) if e.ok
                                    else e.detail.get("cents"))
                    row["why"] = None if e.ok else e.reason
                    row["ok"] = bool(e.ok)
                else:
                    row.update(f0_hz=None, cents=None, ok=False,
                               why="the render is silent at this Range position")
                rows.append(row)
        finally:
            self.set(idx, was)
        det = {"parameter": idx, "name": self.p.get_parameter_name(idx),
               "commanded_note": int(self.note), "commanded_hz": cmd,
               "max_cents": self.RANGE_MAX_CENTS, "sweep": rows,
               "default_raw": float(was),
               "default_text": self.text(idx)}
        good = [r for r in rows if r["ok"]]
        if not good:
            return rq.Check(
                "osc range calibration", rq.FAIL,
                f"no Osc 1 Range position in {len(rows)} sounds the commanded "
                f"note {self.note} ({cmd:.2f} Hz) within {self.RANGE_MAX_CENTS:.0f} "
                f"cents. The octave default is NOT correctable through this "
                f"plugin's own parameters, so nothing downstream of it can be a "
                f"reference", det)
        best = min(good, key=lambda r: abs(r["cents"] or 0.0))
        self.set(idx, best["raw"])
        # REFUSE rather than report if the write did not land where the sweep
        # measured. Every number in `best` was measured with the plugin holding
        # `raw_held`; leaving it holding anything else would qualify one state
        # and ship another.
        landed = float(self.p.get_parameter(idx))
        det["selected_raw_held"] = landed
        if abs(landed - best["raw_held"]) > self.p.raw_tolerance(idx):
            det["selected"] = best
            return rq.Check(
                "osc range calibration", rq.FAIL,
                f"the selected Osc 1 Range was measured with the plugin holding "
                f"{best['raw_held']:.6f} and re-writing it left the plugin holding "
                f"{landed:.6f}: the rig cannot be put back into the state it "
                f"qualified in", det)
        det["selected"] = best
        det["default_was_correct"] = bool(
            abs(best["raw_held"] - float(was)) <= self.p.raw_tolerance(idx))
        why = (f"Osc 1 Range {best['raw_held']:.4f} ({best['text']!r}) sounds "
               f"{best['f0_hz']:.2f} Hz for note {self.note} "
               f"({best['cents']:+.1f} cents)")
        why += (" -- which is where the default already was"
                if det["default_was_correct"] else
                f"; the default {det['default_raw']:.4f} "
                f"({det['default_text']!r}) was not it")
        return rq.Check("osc range calibration", rq.PASS, why, det)

    def trim_level(self) -> rq.Check:
        """Step the master volume down and take the LOUDEST setting that puts
        zero samples at the rail and still clears `rq.PEAK_MIN`.

        Loudest-that-is-clean, not quietest-that-is-safe: a reference frozen 20
        dB down carries 20 dB less of whatever it is a reference for, and the
        quantisation floor of the fixed-point side it will be compared against
        is not movable. Every row is recorded, so "the clipping cannot be
        corrected" is a table and not an assertion."""
        idx = self.I['master']
        was = self.p.get_parameter(idx)
        rows, chosen = [], None
        try:
            for v in self.MASTER_GRID:
                self.set(idx, float(v))
                y = self.render_note(self.note)
                lv = rq.check_level(y)
                row = {"raw": float(v), "raw_held": float(self.p.get_parameter(idx)),
                       "text": self.text(idx),
                       "peak": lv.detail.get("peak"),
                       "clipped_fraction": lv.detail.get("clipped_fraction"),
                       "outcome": lv.outcome}
                rows.append(row)
                if lv.ok:
                    chosen = row
                    break
        finally:
            if chosen is None:
                self.set(idx, was)
        det = {"parameter": idx, "name": self.p.get_parameter_name(idx),
               "grid": list(self.MASTER_GRID), "default_raw": float(was),
               "peak_window": [rq.PEAK_MIN, rq.PEAK_MAX],
               "clipped_max": rq.CLIPPED_MAX, "sweep": rows}
        if chosen is None:
            return rq.Check(
                "level trim", rq.FAIL,
                f"no master-volume position in {len(rows)} puts the peak inside "
                f"[{rq.PEAK_MIN:g}, {rq.PEAK_MAX:g}] with zero samples at the rail. "
                f"The clipping is NOT correctable through this plugin's own "
                f"parameters, so no clip from it can be frozen as a reference", det)
        self.set(idx, chosen["raw"])
        landed = float(self.p.get_parameter(idx))
        det["selected"] = chosen
        det["selected_raw_held"] = landed
        if abs(landed - chosen["raw_held"]) > self.p.raw_tolerance(idx):
            return rq.Check(
                "level trim", rq.FAIL,
                f"the selected master volume was measured with the plugin holding "
                f"{chosen['raw_held']:.6f} and re-writing it left the plugin holding "
                f"{landed:.6f}: the rig cannot be put back into the state it "
                f"qualified in", det)
        return rq.Check("level trim", rq.PASS,
                        f"master volume {chosen['raw_held']:.4f} ({chosen['text']!r}) peaks "
                        f"at {chosen['peak']:.6f} with "
                        f"{100 * (chosen['clipped_fraction'] or 0.0):.2f} % at the rail",
                        det)

    def patch_checks(self) -> list:
        """Both corrections, in the order they depend on each other: the Range
        sweep changes the octave and therefore the level, so the level trim
        runs after it and not before."""
        return [self.calibrate_range(), self.trim_level()]

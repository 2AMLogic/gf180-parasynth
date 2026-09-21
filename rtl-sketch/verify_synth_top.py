#!/usr/bin/env python3
"""Bit-exact verification of the WHOLE CHIP, at its pins, against
model/synth_top_model.py.

    .venv/bin/python rtl-sketch/verify_synth_top.py

What it does:

  1. builds a write stream -- the voice's patch image (model/voice_fx.py's own
     conversion), the reference drum kit (model/drums_fx.py's kit_808, contract
     Appendix G), then notes, drum hits, a retune while a body rings, ROUTE
     switched to the drum filter mid-run, and a volume change;
  2. runs tb_top_bx.v, which sends every one of those over the SPI PINS as
     48-bit DR 0007 revision 2 frames, reports which frame each write landed
     in, and decodes the I2S wire the way a DAC does -- from BCLK, LRCLK and
     SDATA only, never from inside the DUT;
  3. runs the model on the writes at the frames PREDICTED FROM THE PIN, after
     checking each one arrived with its payload intact, in order, and in the
     frame the prediction named;
  4. compares THE DECODED I2S WORDS against the MODEL, with no tolerance.

The model is driven by the frames PREDICTED FROM THE CS_N PIN (the acceptance
cycle is the pin edge plus DR 0007 section 5's three synchroniser cycles, and
a write received during frame f applies at the start of f+1), and the chip is
separately required to agree with that prediction. Driving the model with the
frame the CHIP reported would have been self-referential: a link that delayed
every write by a frame would move the model with it and no comparison could
see it. `LAST` records what any failure actually was, so a caller can check
that a negative control failed for the reason it was recorded to fail for and
not for some other one.

Point 4 is the whole point. rtl-sketch/tb_synth_top.v compares the I2S stream
against `dut.sample` -- the DUT's own output -- which is circular and cannot
detect a bit shift, a channel swap or a wrong D. The comparison here is
against a number the chip had no part in producing.

Exit status, as verify_ladder.py: 0 identical, 1 differed, 2 did not run.

WHAT THE STIMULUS COVERS, and why each case is here. These are the joins, not
the blocks; every one of them can be wrong while `verify_voice`, `verify_drums`
and `verify_modal` are all green, because none of those three can see the join.

  1. every sound through its REGISTER INTERFACE -- all eleven circuits struck
     one at a time from the STOPS register, and all five shared circuits
     switched to their second sound with `drums_fx.preset_writes`. Sixteen
     sounds, not eight, and not one of them poked into internal state;
  2. repeated triggers and the exclusive pairs -- a circuit re-struck on
     consecutive frames, and a paired circuit retuned WHILE it rings and struck
     again, which is the case where one circuit cannot sound twice at once;
  3. simultaneous loud hits -- every accent at full scale, both drum gains at
     65535 and every stop in one frame, which is the only thing the master
     clamp exists for. The run REFUSES unless the model actually reached the
     rail, because a headroom case that never clipped is not a headroom case;
  4. reset during activity -- the drum page's RESET (0xFF) with the kit ringing
     and the voice page's RESET (0x23) under a held note, each followed
     immediately by more writes, which must still arrive: the datapath resets
     must not touch the link (DR 0007 section 4);
  5. the handshake -- `drum_done` must name THIS frame's buses. Two negative
     controls in synth_top.v cover it: DRUM_BUS_STALE (the mix takes the
     previous frame's buses) and DRUM_DONE_NOWAIT (the handshake removed). A
     mix that is one frame stale produces entirely plausible audio;
  6. the deadline and the format -- both datapaths must be idle at the tick,
     `overrun` and the link's `overflow` must never set, a sample must be
     strobed in every frame, and every I2S slot must be 32 BCLK. These are
     ASSERTED here from the bench's own report, not merely printed.

Exit status, as verify_ladder.py: 0 identical, 1 differed, 2 did not run --
which includes REFUSED: a stimulus that did not reach a case it claims to cover
is a broken instrument, and is reported as one rather than as a pass.

  --inject NAME   compile with -DINJECT_BUG_<NAME>. Controls that must turn
                  this red: VOICE_MASTER_PRESHIFT (each product floored before
                  the sum instead of contract 12's single shift),
                  VOICE_DRUM_CLAMP16 (the drum buses clipped to 16 bits before
                  their gains), VOICE_OUT_SAT (no rail), I2S_SHIFT (the wire
                  one bit late), I2S_SWAP (channels swapped), I2S_DELAY (the
                  sample sent a period late, D = 2), SPI_ADDR7, SPI_DATA24,
                  SPI_NOSEC, SPI_DRAIN_LATE, VOICE_MIX_SAT, MODAL_NUM_HOLD,
                  DRUM_ENV_FLOOR, DRUM_LFSR_TAP,
                  DRUM_RESET_ALIAS (revision 8's address collision),
                  DRUM_STOPS8 (revision 8's eight-bit stop field: the three
                  circuits revision 10 added go silent), DRUM_BUS_STALE and
                  DRUM_DONE_NOWAIT (the handshake, case 5 above)
  --rtl DIR       take any of this chip's sources that DIR holds from there
                  instead of from rtl-sketch/. This is the START-RED path of
                  docs/verification-rules.md 1: point it at a PRE-INTEGRATION
                  drum section and watch this bench fail. Files DIR does not
                  hold still come from rtl-sketch/, so a directory holding
                  synth_top.v, drum_regs.v, drum_kit.v and drum_dp.v swaps
                  exactly the drum section and nothing else.
  --expect-fail   exit 0 only if the comparison gave 1
  --short         a third of the stimulus
  --frames N      override the run length
"""
from __future__ import annotations
import argparse, json, os, re, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "model"))
sys.path.insert(0, os.path.join(ROOT, "audition"))
import hashlib
import shutil
import numpy as np
from scipy.io import wavfile
import voice_fx as vf
import drums_fx as dx
import synth_top_model as stm
from verify_ladder import tool

SEC_V, SEC_D = 0, 1
A = stm
WAVE_CODE = dict(saw=0, square=1, pulse25=2, tri=3, sine=4,
                 shark=5, revsaw=6, pulse29=7, pulse15=8)
SRCS = ("synth_top.v", "voice_dp.v", "spi_ctl.v", "drum_regs.v", "drum_kit.v",
        "drum_dp.v", "modal_dp.v", "i2s_tx.v", "ladder_dp_n.v", "recip_div.v",
        "osc_2x_saw_bank.v", "osc_2x_saw_path.v", "polyblep_saw_pair.v",
        "osc_substep_pair.v", "decimate_2x_tm_sym.v", "rate_conv_2x.v")


def script(short: bool = False):
    """(wait_frames, flag, sec, addr, data) in send order, the frames to run
    after the last write, and a COVERAGE claim -- what this stimulus says it
    reaches, checked in main() against the model rather than assumed.
    `wait_frames` is how many frame ticks the bench waits before starting that
    transaction; the frame a write LANDS in is the link's business and comes
    back from the bench."""
    S = 0.35 if short else 1.0
    regs = vf.VoiceFx.patch_regs()
    w = []
    def put(wait, flag, sec, addr, data): w.append((wait, flag, sec, addr, data))

    # ---- 1. the voice image, back to back (the MCU's boot-time patch load) ----
    for k, s_ in enumerate(regs["waves"]):  put(0, 0, SEC_V, A.A_WAVE + k, WAVE_CODE[s_])
    for base, key in ((A.A_AMP, "amp"), (A.A_FILT, "fenv")):
        for j, v in enumerate(regs[key]):   put(0, 0, SEC_V, base + j, v)
    for nm, key in (("A_CUT_LO", "cut_lo"), ("A_CUT_HI", "cut_hi"), ("A_K", "k"),
                    ("A_GAIN", "gain"), ("A_OGAIN", "ogain"), ("A_GLIDE", "glide")):
        put(0, 0, SEC_V, getattr(A, nm), regs[key])
    put(0, 0, SEC_V, A.A_VOL,  regs["vol"])
    put(0, 0, SEC_V, A.A_DVOL, dx.accent_reg(0.45))          # DR 0005's reference drum gains
    put(0, 0, SEC_V, A.A_BVOL, dx.accent_reg(0.45))
    put(0, 0, SEC_V, A.A_DCUT, 1800)
    put(0, 0, SEC_V, A.A_DK,     regs["k"])
    put(0, 0, SEC_V, A.A_DGAIN,  regs["gain"])
    put(0, 0, SEC_V, A.A_DOGAIN, regs["ogain"])
    for k, v in enumerate(vf.VoiceFx.note_incs(45, regs["detune"])):
        put(0, 1, SEC_V, A.A_INC + k, v)                     # flag = jump
    put(0, 0, SEC_V, A.A_TRACK, vf.VoiceFx.note_track(45, regs["track"]))
    for k, g in enumerate(regs["weights"]): put(0, 0, SEC_V, A.A_W + k, g)

    # ---- 2. the drum image: the reference kit, plus an accent of 1.0 per stop ----
    for a, v in dx.kit_808():               put(0, 0, SEC_D, a, v)
    for st in range(dx.N_STOPS):            put(0, 0, SEC_D, dx.A_ACCENT + st, dx.accent_reg(1.0))
    ALL_STOPS = (1 << dx.N_STOPS) - 1       # ELEVEN circuits. 0xFF reaches eight of them,
                                            # which is what this bench used to send.
    fired = set()                           # every stop this stimulus actually strikes
    def strike(wait, mask, hold=2):
        put(wait, 0, SEC_D, dx.A_STOPS, mask); put(hold, 0, SEC_D, dx.A_STOPS, 0)
        fired.update(i for i in range(dx.N_STOPS) if mask >> i & 1)

    # ---- 3. play. The voice first alone, then with drums, then through the filter ----
    gap = max(2, int(14 * S))
    put(8, 0, SEC_V, A.A_GATE_ON, 0)                          # a note, drums silent
    strike(gap * 2, 1 << dx.BD)                               # bass drum
    strike(gap, (1 << dx.CH) | (1 << dx.SD))
    strike(gap, 1 << dx.OH)                                   # the OH -> CH choke
    put(gap, 0, SEC_V, A.A_TRIG, 0)                           # re-trigger the voice under the ring
    strike(gap, ALL_STOPS)                                    # every stop in one frame

    # ---- 3b. CASE 1: each of the eleven circuits struck ALONE ------------------
    # Struck one at a time so a wrong one is attributable, and through the STOPS
    # register like a host, never by poking state. MT, CL and CY are stops 8, 9
    # and 10: the mask this bench used to send could not reach them and the run
    # would have been green with all three silent, which is the defect
    # INJECT_BUG_DRUM_STOPS8 reproduces.
    for st in range(dx.N_STOPS):
        strike(max(2, gap // 2), 1 << st)

    # ---- 3c. CASE 1 continued: sixteen sounds on eleven circuits --------------
    # Five circuits carry two sounds each (drums_fx.PAIRS) and `kit_808()` loads
    # the first of each. Selecting the other is a burst of MODE, PATH and ENV
    # writes -- the three register blocks revision 10 moved or grew -- so this is
    # also the register map with audio behind it: leave PATH decoding at 0x80 or
    # MODE at 0xC0 and the swapped sound is silent or unchanged, not merely
    # mis-addressed.
    # ---- CASE 2 with it: the EXCLUSIVE PAIR. The circuit is retuned WHILE it
    # rings from the sound it is already playing, then struck again. One circuit
    # cannot sound twice at once, and the second strike must restart it.
    swapped = []
    for alt in ("LC", "MC", "HC", "CL", "MA"):
        st = dx.SOUND_STOP[alt]
        strike(gap, 1 << st)                                  # the first sound of the pair, ringing
        for a, v in dx.preset_writes(alt):  put(0, 0, SEC_D, a, v)   # retuned mid-ring
        swapped.append(alt)
        strike(max(2, gap // 2), 1 << st)                     # and struck again as the second sound
        strike(2, 1 << st)                                    # CASE 2: re-struck on the next frames

    # retune the bass drum WHILE it rings -- a coefficient write mid-decay, which
    # is the write-atomicity case: a1 lands one frame, a2 the next (DR 0008)
    for a, v in dx.mode_writes(dx.M_BD, dx.BD_HZ_CHART, dx.bd_decay_q(1.0), 0.0)[:2]:
        put(gap // 2, 0, SEC_D, a, v)
    strike(gap, 1 << dx.BD)
    # ---- the drum filter engaged mid-run (ROUTE.DFILT), then a louder master ----
    put(gap, 0, SEC_V, A.A_ROUTE, 1)
    strike(gap, (1 << dx.BD) | (1 << dx.CH))
    put(gap, 0, SEC_V, A.A_DCUT, 400)                         # sweep the drum filter down
    strike(gap, (1 << dx.OH) | (1 << dx.SD))

    # ---- 3d. CASE 3: simultaneous loud hits, which is what the clamp is for ----
    # Every accent at full scale, both drum gains at their top, every circuit in
    # one frame, under a held note at the voice's own volume. main() REFUSES if
    # the model did not actually reach the rail here.
    put(gap, 0, SEC_V, A.A_DVOL, 65535)
    put(0, 0, SEC_V, A.A_BVOL, 65535)
    for st in range(dx.N_STOPS):            put(0, 0, SEC_D, dx.A_ACCENT + st, 65535)
    strike(gap, ALL_STOPS)
    strike(2, ALL_STOPS)                                      # CASE 2: and again immediately
    put(gap, 0, SEC_V, A.A_GATE_OFF, 0)                       # release: the note ends, drums ring on
    put(gap, 0, SEC_V, A.A_ROUTE, 0)                          # back to bypass, mid-ring
    strike(gap, 1 << dx.CB)

    # ---- 3e. CASE 4: a RESET in each page, during activity ---------------------
    # The drum page's RESET while the whole kit rings, then the kit reloaded and
    # struck: if 0xFF had touched the link or the queue those writes would not
    # arrive, and the write-integrity check above counts every one of them. The
    # voice page's RESET under a held note does the same on the other page. This
    # is also the RESET_ALIAS control's target -- under it a MODE coefficient
    # write reaches 0xFF's decode and silences the kit.
    strike(gap, ALL_STOPS)
    put(max(2, gap // 3), 0, SEC_D, dx.A_RESET, 0)            # drums reset mid-ring
    for a, v in dx.kit_808():               put(0, 0, SEC_D, a, v)   # ... and the link still carries
    for st in range(dx.N_STOPS):            put(0, 0, SEC_D, dx.A_ACCENT + st, dx.accent_reg(1.0))
    strike(gap, ALL_STOPS)
    put(gap, 0, SEC_V, A.A_GATE_ON, 0)                        # a note, then reset the voice under it
    put(gap, 0, SEC_V, A.A_RESET, 0)
    for k, v in enumerate(vf.VoiceFx.note_incs(45, regs["detune"])):
        put(0, 1, SEC_V, A.A_INC + k, v)                      # the link carried these too
    put(0, 0, SEC_V, A.A_VOL, regs["vol"])
    put(0, 0, SEC_V, A.A_DVOL, dx.accent_reg(0.45))
    put(0, 0, SEC_V, A.A_BVOL, dx.accent_reg(0.45))
    strike(gap, (1 << dx.SD) | (1 << dx.CY))

    # ---- the envelope DEAD ZONE (15.3 / 8.3), reached on purpose -------------
    # Below 2^16/rate the exponential step is zero; without the max(1, .) the
    # level FREEZES there instead of running out at one LSB per frame. From a
    # full-scale strike the open hat takes ~57 000 frames to descend that far,
    # which no run of this length reaches -- so the rule is exercised by
    # striking it from a peak just above its freeze level instead. Without this
    # write INJECT_BUG_DRUM_ENV_FLOOR goes uncaught here, and it did.
    kit = dict(dx.kit_808())
    e = dx.E_OH
    rate = kit[dx.A_ENV + e * 4 + 2]
    freeze = 65535 // rate                       # largest level whose step is zero
    put(gap, 0, SEC_D, dx.A_ENV + e * 4 + 1, freeze + 64)
    strike(gap, 1 << dx.OH)
    # The tail must outlast the SLOWEST envelope's linear floor, or the dead-zone
    # rule of 15.3 / 8.3 is never exercised and INJECT_BUG_DRUM_ENV_FLOOR goes
    # uncaught: below 2^16/rate the exponential step is zero and the tail runs at
    # one LSB per frame, which is hundreds of frames for the open hat.
    tail = int(1500 * S) if not short else int(260 * S)
    cover = dict(stops=sorted(fired), swapped=swapped,
                 sounds=len(fired) + len(swapped),
                 drum_reset=sum(1 for c in w if c[2] == SEC_D and c[3] == dx.A_RESET),
                 voice_reset=sum(1 for c in w if c[2] == SEC_V and c[3] == A.A_RESET))
    return w, tail, cover


def m5a_script(manifest_path: str, *, smoke: bool = False,
               pulse_shape: str = "pulse29"):
    """Build the frozen two-wave M5A phrase as register writes over SPI."""
    manifest_file = os.path.abspath(manifest_path)
    manifest = json.loads(open(manifest_file).read())
    audio = manifest.get("audio", {})
    if audio.get("sample_rate_hz") != 48000 or audio.get("channels") != 1:
        raise ValueError("M5A reference must be mono at 48 kHz")
    ref_audio = os.path.join(os.path.dirname(manifest_file), audio.get("file", ""))
    if not os.path.isfile(ref_audio):
        raise ValueError(f"M5A reference audio is missing: {ref_audio}")
    if hashlib.sha256(open(ref_audio, "rb").read()).hexdigest() != audio.get("sha256"):
        raise ValueError("M5A reference WAV hash does not match its manifest")
    if not (4.0 <= manifest["timeline"]["phrase_s"] <= 8.0):
        raise ValueError("M5A phrase is outside its declared 4–8 s interval")

    first_measurement = manifest["timeline"]["segments"][0]["measurements"][0]
    env = first_measurement["envelope"]
    cutoff_measurement = manifest["patch"]["cutoff_measurement"]
    if (int(cutoff_measurement["block_size_samples"]) != int(manifest["host"]["block_size_samples"])
            or int(cutoff_measurement["repeat_count"]) < 3
            or float(cutoff_measurement["repeat_range_hz"]) > 10.0):
        raise ValueError("M5A cutoff calibration lacks the pinned block size or three repeatable measurements")
    cutoff_hz = int(round(float(cutoff_measurement["f0_hz"])))
    if not 1000 <= cutoff_hz < 15000:
        raise ValueError(f"measured M5A max cutoff is outside the qualified range: {cutoff_hz} Hz")
    attack_s = float(env["attack_10_90_ms"]) / 1000.0 / 0.8
    release_s = float(env["release_t20_ms"]) / 1000.0 * 4.0 / np.log(10.0)
    regs = vf.VoiceFx.patch_regs(
        waves=("saw", "saw", "saw"), detune=(0.0, 0.0, 0.0), mix=(1.0, 0.0, 0.0),
        noise=0.0, cutoff=(cutoff_hz, cutoff_hz), q=0.0, drive=0.75,
        amp=(attack_s, 0.25, 1.0, release_s),
        fenv=(0.004, 0.30, 1.0, 0.10), track=0.0, vol=0.45,
        mod_mix=0.0, mod_wheel=0.0, osc_mod=False, filt_mod=False)
    if pulse_shape not in vf.WAVE_CODE or pulse_shape not in vf.DUTY:
        raise ValueError(f"M5A pulse shape must be a supported rectangular shape: {pulse_shape}")
    w = []

    def put(wait, flag, addr, data):
        w.append((int(wait), int(flag), SEC_V, int(addr), int(data)))

    for k, wave in enumerate(regs["waves"]):
        put(0, 0, A.A_WAVE + k, WAVE_CODE[wave])
    for k, weight in enumerate(regs["weights"]):
        put(0, 0, A.A_W + k, weight)
    for base, key in ((A.A_AMP, "amp"), (A.A_FILT, "fenv")):
        for k, value in enumerate(regs[key]):
            put(0, 0, base + k, value)
    for name, value in (("A_CUT_LO", "cut_lo"), ("A_CUT_HI", "cut_hi"),
                        ("A_K", "k"), ("A_GAIN", "gain"), ("A_OGAIN", "ogain"),
                        ("A_GLIDE", "glide"), ("A_VOL", "vol"),
                        ("A_ROUTE", "route"), ("A_NSEL", "nsel"),
                        ("A_MROUTE", "mroute"), ("A_MMIX", "mmix"),
                        ("A_MWHEEL", "mwheel"), ("A_MPD", "mpd"), ("A_MFD", "mfd")):
        put(0, 0, getattr(A, name), regs.get(value, 0))
    put(0, 0, A.A_DVOL, 0); put(0, 0, A.A_BVOL, 0)
    put(0, 0, A.A_DCUT, cutoff_hz); put(0, 0, A.A_DK, regs["k"])
    put(0, 0, A.A_DGAIN, regs["gain"]); put(0, 0, A.A_DOGAIN, regs["ogain"])

    if smoke:
        # This compact integration case exercises the same M5A pitches and
        # both oscillator selections, but makes no claim about envelope
        # completeness. The full phrase remains the sound-quality stimulus.
        timeline_segments = [
            {"wave": "saw", "duration_s": 0.045,
             "midi_events": [{"note": 84, "on_s": 0.005, "gate_s": 0.010},
                             {"note": 96, "on_s": 0.020, "gate_s": 0.010}]},
            {"wave": "pulse", "duration_s": 0.045,
             "midi_events": [{"note": 84, "on_s": 0.005, "gate_s": 0.010},
                             {"note": 96, "on_s": 0.020, "gate_s": 0.010}]},
        ]
        segment_silence_s = 0.005
        final_audio_s = 0.100
    else:
        timeline_segments = manifest["timeline"]["segments"]
        segment_silence_s = float(manifest["timeline"]["segment_silence_s"])
        final_audio_s = float(manifest["timeline"]["audio_duration_s"])
    cursor_s = 0.0
    previous_off_s = 0.0
    events = []
    for segment_index, segment in enumerate(timeline_segments):
        wave = segment["wave"]
        if wave not in ("saw", "pulse"):
            raise ValueError(f"unsupported M5A waveform segment {wave!r}")
        absolute_segment_s = cursor_s
        for event_index, event in enumerate(segment["midi_events"]):
            on_s = absolute_segment_s + float(event["on_s"])
            off_s = on_s + float(event["gate_s"])
            # A write serializes for about 1.55 frames. Reserve ten frames for
            # the three increment writes and gate write, then use the pin-side
            # report below as the authoritative event time.
            wait = max(0, round((on_s - previous_off_s) * 48000) - 10)
            if event_index == 0:
                wave_code = 0 if wave == "saw" else vf.WAVE_CODE[pulse_shape]
                put(wait, 0, A.A_WAVE, wave_code)
                wait = 0
            incs = vf.VoiceFx.note_incs(int(event["note"]), regs["detune"])
            put(wait, 1, A.A_INC, incs[0])
            put(0, 1, A.A_INC + 1, incs[1]); put(0, 1, A.A_INC + 2, incs[2])
            put(0, 0, A.A_TRACK, vf.VoiceFx.note_track(int(event["note"]), regs["track"]))
            put(0, 0, A.A_GATE_ON, 0)
            events.append({"wave": wave, "note": int(event["note"]),
                           "on_s": on_s, "off_s": off_s})
            wait_off = max(0, round(float(event["gate_s"]) * 48000) - 3)
            put(wait_off, 0, A.A_GATE_OFF, 0)
            previous_off_s = off_s
        cursor_s += float(segment["duration_s"]) + segment_silence_s
    tail = max(1, round((final_audio_s - previous_off_s) * 48000) - 3)
    return w, tail, {"events": events, "manifest": manifest, "reference_audio": ref_audio,
                    "filter_drive": 0.75, "pulse_shape": pulse_shape,
                     "smoke": smoke, "audio_duration_s": final_audio_s}


def write_cmds(path: str, w) -> None:
    with open(path, "w") as fh:
        for wait, flag, sec, addr, data in w:
            fh.write(f"{wait} {flag} {sec} {addr} {data}\n")


def resolve_sources(rtl_dir: str | None):
    """Which file each source name is actually taken from. A `--rtl DIR` that
    holds some of them swaps exactly those; everything else stays rtl-sketch's.
    Returned so that the run can SAY which top level it built rather than
    leaving it to be inferred -- this project has published area figures for a
    chip whose drum section was a placeholder."""
    names = list(SRCS) + ["tb_top_bx.v"]
    out = []
    for n in names:
        alt = os.path.join(rtl_dir, n) if rtl_dir else None
        out.append((n, alt if alt and os.path.exists(alt) else os.path.join(HERE, n)))
    return out


def provenance(srcs) -> dict:
    """The exact build, recorded: the source commit, every source file's SHA-256
    and where it came from, and the generated ROM images the design reads."""
    def sha(path):
        with open(path, "rb") as fh: return hashlib.sha256(fh.read()).hexdigest()[:12]
    try:
        commit = subprocess.run(["git", "-C", ROOT, "rev-parse", "--short", "HEAD"],
                                capture_output=True, text=True).stdout.strip() or "?"
        dirty = bool(subprocess.run(["git", "-C", ROOT, "status", "--porcelain"],
                                    capture_output=True, text=True).stdout.strip())
    except Exception:
        commit, dirty = "?", False
    gen = {}
    for n in ("tanh16.hex", "tanh256.hex"):                 # generated inputs the RTL $readmemh's
        f = os.path.join(HERE, n)
        if os.path.exists(f): gen[n] = sha(f)
    files = {n: (sha(f), "rtl-sketch" if os.path.dirname(f) == HERE else os.path.dirname(f))
             for n, f in srcs}
    return dict(commit=commit + ("-dirty" if dirty else ""), files=files, generated=gen)


# the bench's own report lines, which are preconditions of the comparison and
# not decoration: a run where a datapath was still busy at the tick, or a frame
# had no sample, is not a run whose audio means anything.
RE_BUSY = re.compile(r"datapath busy at a tick: (\d+).*overrun (\d+); overflow (\d+)")
RE_STRB = re.compile(r"sample strobed in (\d+) frames, MISSING in (\d+), worst strobe cycle (\d+)")


def simulate(defines, outdir, frames, timeout_s=5400.0, rtl_dir=None,
             simulator="iverilog", envtrace_path=None):
    tag = "_".join(d.replace("INJECT_BUG_", "") for d in defines) or "base"
    out = {k: os.path.join(outdir, f"top_{k}_{tag}.txt") for k in ("i2s", "samp", "wrs")}
    if envtrace_path:
        out["envtrace"] = envtrace_path
    for f in out.values():
        if os.path.exists(f): os.remove(f)
    resolved = resolve_sources(rtl_dir)
    srcs = [f for _, f in resolved]
    if simulator == "iverilog":
        iverilog, vvp = tool("iverilog"), tool("vvp")
        if not iverilog or not vvp:
            print("verify_synth_top: iverilog/vvp not on PATH (or set OSS_CAD_SUITE)"); return None
        executable = os.path.join(outdir, f"tb_top_bx_{tag}.vvp")
        compile_cmd = [iverilog, "-g2012", "-o", executable] + [f"-D{d}" for d in defines] + srcs
    elif simulator == "verilator":
        verilator = shutil.which("verilator")
        if not verilator:
            print("verify_synth_top: Verilator not on PATH"); return None
        mdir = os.path.join(outdir, f"obj_top_bx_{tag}")
        os.makedirs(mdir, exist_ok=True)
        executable = os.path.join(mdir, "tb_top_bx")
        compile_cmd = [verilator, "--binary", "--timing", "-Wno-fatal",
                       "--top-module", "tb_top_bx", "--Mdir", mdir, "-o", executable]
        compile_cmd += [f"-D{d}" for d in defines] + srcs
    else:
        raise ValueError(f"unknown simulator {simulator!r}")
    r = subprocess.run(compile_cmd, cwd=HERE, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"verify_synth_top: {simulator} compile failed:\n" + r.stdout + r.stderr); return None
    try:
        run_cmd = ([tool("vvp"), "-n", executable] if simulator == "iverilog" else [executable])
        r = subprocess.run(run_cmd + [f"+cmd={os.path.join(outdir, 'top_bx_cmds.txt')}",
                            f"+i2s={out['i2s']}", f"+samp={out['samp']}", f"+wrs={out['wrs']}",
                            f"+frames={frames}"] +
                           ([f"+env={envtrace_path}"] if envtrace_path else []),
                           cwd=HERE, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        print("verify_synth_top: simulation timed out"); return None
    report = [l for l in r.stdout.splitlines() if l.startswith("tb_top_bx")]
    sys.stdout.write("".join("  sim: " + l + "\n" for l in report))
    if r.returncode != 0 or not os.path.exists(out["i2s"]):
        print("verify_synth_top: vvp failed:\n" + r.stdout + r.stderr); return None
    out["report"] = report
    out["sources"] = resolved
    return out


def rows(path):
    return [ln.split() for ln in open(path).read().splitlines() if ln.strip()]


# What the last run actually found. A negative control must be checked against
# the failure it was RECORDED to cause, not merely against a non-zero status:
# an expectation satisfied by the wrong failure is not evidence.
LAST: dict = {}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--inject", default=None); ap.add_argument("--expect-fail", action="store_true")
    ap.add_argument("--short", action="store_true"); ap.add_argument("--frames", type=int, default=None)
    ap.add_argument("--osc2x", action="store_true",
                    help="select the measured 2x saw chain in RTL and Python model")
    ap.add_argument("--filter2x", action="store_true",
                    help="select causal reconstructed 2x filter and pulse-duty challenger")
    ap.add_argument("--m5a", action="store_true",
                    help="play the frozen Mono M5A reference phrase through SPI and I2S")
    ap.add_argument("--m5a-smoke", action="store_true",
                    help="short SPI-to-I2S M5A pitch/waveform integration check; no envelope claim")
    ap.add_argument("--m5a-pulse-shape", choices=("square", "pulse15", "pulse25", "pulse29"),
                    default="pulse29", help="supported rectangular shape for M5A pulse segments")
    ap.add_argument("--m5a-manifest", default=os.path.join(ROOT, "docs/scorecard/mono-m5a-miniv3/manifest.json"))
    ap.add_argument("--wav-out", default=None,
                    help="write decoded left-channel I2S samples to an int16 WAV")
    ap.add_argument("--simulator", choices=("iverilog", "verilator"), default="iverilog",
                    help="RTL event engine (Verilator is substantially faster for complete phrases)")
    ap.add_argument("--rtl", default=None,
                    help="take sources this directory holds from there (the start-red path)")
    ap.add_argument("--outdir", default=os.path.join(HERE, "build"))
    ap.add_argument("--envtrace", action="store_true",
                    help="write diagnostic voice gate/envelope registers per frame")
    a = ap.parse_args(argv)
    if a.filter2x:
        a.osc2x = True
    a.outdir = os.path.abspath(a.outdir); os.makedirs(a.outdir, exist_ok=True)
    if a.rtl:
        a.rtl = os.path.abspath(a.rtl)
        if not os.path.isdir(a.rtl):
            print(f"verify_synth_top: REFUSED -- --rtl {a.rtl} is not a directory"); return 2

    m5a = None
    if a.m5a or a.m5a_smoke:
        a.m5a = True
        if not a.osc2x:
            print("verify_synth_top: REFUSED -- M5A requires the selected 2x saw candidate")
            return 2
        try:
            cmds, tail, m5a = m5a_script(a.m5a_manifest, smoke=a.m5a_smoke,
                                         pulse_shape=a.m5a_pulse_shape)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            print(f"verify_synth_top: REFUSED -- M5A stimulus: {exc}")
            return 2
        cover = {}
    else:
        cmds, tail, cover = script(a.short)
    # The stimulus must reach the cases this bench claims, or it is not the
    # bench it says it is. Checked BEFORE the simulation, so a stimulus edit
    # that quietly drops a circuit refuses instead of passing.
    if not a.m5a:
        missing = [dx.STOP_NAMES[i] for i in range(dx.N_STOPS) if i not in cover["stops"]]
        pairs = {b for _, b in dx.PAIRS}
        if missing:
            print(f"verify_synth_top: REFUSED -- the stimulus never strikes {', '.join(missing)}: "
                  f"{len(cover['stops'])} of {dx.N_STOPS} circuits"); return 2
        if not pairs.issubset(set(cover["swapped"])):
            print(f"verify_synth_top: REFUSED -- the stimulus never selects "
                  f"{', '.join(sorted(pairs - set(cover['swapped'])))}: "
                  f"{cover['sounds']} of {len(dx.SOUND_NAMES)} sounds"); return 2
        if not (cover["drum_reset"] and cover["voice_reset"]):
            print("verify_synth_top: REFUSED -- the stimulus does not reset both pages during activity")
            return 2
    write_cmds(os.path.join(a.outdir, "top_bx_cmds.txt"), cmds)
    print(f"verify_synth_top: {len(cmds)} writes over the pins "
          f"({sum(1 for c in cmds if c[2] == SEC_V)} voice, {sum(1 for c in cmds if c[2] == SEC_D)} drum), "
          f"{tail} frames after the last")
    if a.m5a:
        detail = ("smoke: envelope completion not claimed" if m5a["smoke"] else
                  f"{m5a['manifest']['timeline']['phrase_s']:.3f} s phrase and complete release")
        print(f"verify_synth_top: M5A stimulus has {len(m5a['events'])} note events, "
              f"{detail}; "
              f"selected filter drive {m5a['filter_drive']:.2f}; "
              f"reference sha256 {m5a['manifest']['audio']['sha256']}")
    else:
        print(f"verify_synth_top: stimulus covers {len(cover['stops'])} of {dx.N_STOPS} circuits and "
              f"{cover['sounds']} of {len(dx.SOUND_NAMES)} sounds "
              f"({', '.join(dx.STOP_NAMES[i] for i in cover['stops'])}; swapped to "
              f"{', '.join(cover['swapped'])}), both pages reset while sounding")
    defines = (["VOICE_OSC_2X"] if a.osc2x else [])
    if a.filter2x:
        defines.append("VOICE_FILTER_2X")
    if a.inject:
        defines.append(f"INJECT_BUG_{a.inject}")
    config = ("2x saw + causal 2x filter candidate" if a.filter2x else
              "2x saw candidate" if a.osc2x else "legacy single-rate waveform")
    print(f"verify_synth_top: selected {config}; compile defines: {', '.join(defines) or '(none)'}")
    print(f"verify_synth_top: simulator backend {a.simulator}")
    # The bench closes I2S at the final sample boundary and drops the three
    # serializer periods still in flight.  Give the M5A transport check three
    # drain frames so its *modelled* phrase length remains unchanged while the
    # wire has time to emit its complete final periods.
    sim_frames = tail + (3 if a.m5a else 0)
    envtrace_path = os.path.join(a.outdir, "top_envtrace.txt") if a.envtrace else None
    out = simulate(defines, a.outdir, sim_frames, rtl_dir=a.rtl, simulator=a.simulator,
                   envtrace_path=envtrace_path)
    if out is None:
        return 2

    # ---- WHICH top level was built, recorded, not inferred -------------------
    pv = provenance(out["sources"])
    swapped_in = sorted(n for n, (_, where) in pv["files"].items() if where != "rtl-sketch")
    print(f"verify_synth_top: built from {pv['commit']}, outdir {a.outdir}; "
          f"synth_top.v {pv['files']['synth_top.v'][0]}, drum_kit.v {pv['files']['drum_kit.v'][0]}, "
          f"drum_regs.v {pv['files']['drum_regs.v'][0]}"
          + (f"; ROMs " + ", ".join(f"{k} {v}" for k, v in sorted(pv["generated"].items())) if pv["generated"] else "")
          + (f"; {len(swapped_in)} source(s) taken from {a.rtl}: {', '.join(swapped_in)}" if swapped_in
             else "; every source from rtl-sketch/"))
    LAST.update(provenance=pv, cover=cover, rtl_dir=a.rtl,
                oscillator_config=config, compile_defines=defines)

    # ---- the deadline, the overrun and the strobe: preconditions, asserted ----
    rep = "\n".join(out["report"])
    mb, ms = RE_BUSY.search(rep), RE_STRB.search(rep)
    if not mb or not ms:
        print("verify_synth_top: REFUSED -- the bench did not report the frame budget; "
              "it cannot be checked and will not be assumed"); return 2
    busy, overrun, overflow = (int(x) for x in mb.groups())
    strobed, missing_s, worst = (int(x) for x in ms.groups())
    LAST.update(busy_at_tick=busy, overrun=overrun, overflow=overflow,
                frames_no_sample=missing_s, worst_strobe_cycle=worst)
    if busy or overrun or overflow or missing_s or worst >= 256:
        print(f"verify_synth_top: FAIL -- the frame budget was not met: a datapath was busy at "
              f"{busy} tick(s), overrun {overrun}, link overflow {overflow}, {missing_s} frame(s) "
              f"with no sample, worst strobe cycle {worst} of 256")
        return 1
    # An apparatus precondition, reported where it is used. The LRCLK transition
    # is cycle 128 (cyc[7]). i2s_tx latches the period's word from `held` at
    # cycle 255 and the right slot re-reads at cycle 127; if the core has not
    # strobed a NEW sample by then, `held` still equals `cur` and a right-channel
    # defect emits a stream bit-identical to the correct one. Revision 10's drum
    # section pushed the strobe from cycle 124 to 156, so that is now the case
    # and INJECT_BUG_I2S_SWAP no longer discriminates here. The audio is correct
    # either way -- what is lost is a negative control, and it is said out loud
    # rather than left in a list of controls that no longer fire.
    if worst >= 128:
        print(f"verify_synth_top: NOTE -- the core strobes its sample as late as cycle {worst} of 256, "
              f"past the LRCLK transition at 128 ({100 * (256 - worst) / 256:.0f} % of the frame still "
              f"spare, no overrun). A right-channel-only defect is INVISIBLE at the pins in this "
              f"configuration: see the I2S_SWAP entry in the Makefile's controls target")

    # ---- what the chip says it received, and when --------------------------
    wr = rows(out["wrs"])
    if len(wr) != len(cmds):
        print(f"verify_synth_top: FAIL -- {len(wr)} writes reached the register port, {len(cmds)} were sent")
        if not wr: return 2
    bad = 0
    for (want, got) in zip(cmds, wr):
        if (int(got[1]), int(got[2]), int(got[3]), int(got[4])) != (want[1], want[2], want[3], want[4] & 0xFFFFFFFF):
            bad += 1
    LAST.update(writes_bad=bad, writes_seen=len(wr), writes_sent=len(cmds))
    if bad:
        print(f"verify_synth_top: FAIL -- {bad} of {len(cmds)} writes arrived corrupted "
              f"(run verify_ctl.py: that is the link, not the datapath)")
        return 1
    # the landing frame the PIN predicts, and whether the chip agreed
    pred_bad = sum(1 for g in wr if len(g) > 5 and int(g[5]) >= 0 and int(g[5]) != int(g[0]))
    no_pred = sum(1 for g in wr if len(g) <= 5 or int(g[5]) < 0)
    LAST.update(frame_pred_bad=pred_bad, frame_no_pred=no_pred)
    if pred_bad or no_pred:
        print(f"verify_synth_top: FAIL -- {pred_bad} write(s) landed in a frame the CS_N pin did not "
              f"predict, {no_pred} with no prediction at all: the link's timing is not DR 0007 section 5's")
        return 1
    # drive the model from the PREDICTION, not from what the chip reported
    model_writes = [(int(g[5]), int(g[1]), int(g[2]), int(g[3]), int(g[4])) for g in wr]
    last = max(f for f, *_ in model_writes)
    n = a.frames or (last + tail + 1)
    print(f"verify_synth_top: writes landed in frames {model_writes[0][0]}..{last}; modelling {n} frames")

    # ---- the model, on those frames ----------------------------------------
    m = stm.SynthTopModel(oversample_2x=a.osc2x, filter_2x=a.filter2x).run(model_writes, n)
    exp_i2s, exp_s = m["i2s"], m["sample"]

    # ---- the comparison: the WIRE against the MODEL -------------------------
    i2s = rows(out["i2s"])
    if not i2s:
        print("verify_synth_top: FAIL -- no I2S periods decoded from the wire"); return 2
    nper = min(len(i2s), n)
    if a.m5a and nper != n:
        print(f"verify_synth_top: REFUSED -- M5A I2S path yielded {nper} periods for {n} frames")
        return 2
    if a.m5a and [int(r[0]) for r in i2s[:n]] != list(range(n)):
        print("verify_synth_top: REFUSED -- M5A I2S periods are not a complete, ordered 0..N-1 timeline")
        return 2
    mism = swap = width = xs = 0
    first = None
    for r in i2s[:nper]:
        p, left, right, nbl, nbr = (int(r[0]), int(r[1]), int(r[2]), int(r[3]), int(r[4]))
        if p >= n: break
        if nbl != 32 or nbr != 32:
            width += 1
        e = int(exp_i2s[p])
        if left != e:
            mism += 1
            if first is None: first = (p, e, left)
        if right != left:
            swap += 1
    if a.wav_out:
        if not a.m5a:
            print("verify_synth_top: REFUSED -- --wav-out requires --m5a")
            return 2
        os.makedirs(os.path.dirname(os.path.abspath(a.wav_out)), exist_ok=True)
        wavfile.write(a.wav_out, 48000,
                      np.asarray([int(r[1]) for r in i2s[:nper]], dtype=np.int16))
        print(f"verify_synth_top: decoded I2S WAV written to {a.wav_out}")
        with open(a.wav_out, "rb") as fh:
            wav_sha256 = hashlib.sha256(fh.read()).hexdigest()
        print(f"verify_synth_top: decoded I2S WAV sha256 {wav_sha256}")
    # the DUT's own stream, as a DIAGNOSTIC only
    sm = rows(out["samp"])
    core_bad = sum(1 for r in sm if int(r[0]) < n and int(r[1]) != int(exp_s[int(r[0])]))
    core_first = next(((int(r[0]), int(exp_s[int(r[0])]), int(r[1])) for r in sm
                       if int(r[0]) < n and int(r[1]) != int(exp_s[int(r[0])])), None)
    peak = int(np.abs(exp_s).max())
    clipped = int(np.sum(np.abs(exp_s) == 32767) + np.sum(exp_s == -32768))
    print(f"verify_synth_top: model peak |sample| {peak} of 32768 ({clipped} frames at the rail); "
          f"drums |dmix| max {int(np.abs(m['dmix']).max())}, |body| max {int(np.abs(m['body']).max())}, "
          f"DFILT engaged in {int(m['route'].sum())} frames")
    # CASE 3's precondition. The simultaneous-hit section exists to drive the
    # master clamp; if the model never reached the rail the clamp was never
    # exercised and a pass here would be silent about the one thing that
    # section is for. The model is the same whatever is injected, so this
    # refuses for a stimulus that stopped covering the case, never for a defect.
    LAST.update(model_peak=peak, model_clipped=clipped)
    if clipped == 0 and not a.m5a:
        print(f"verify_synth_top: REFUSED -- the simultaneous-hit section never reached the rail "
              f"(model peak {peak} of 32768): the master clamp is not exercised by this stimulus")
        return 2
    LAST.update(wire_mismatch=mism, swap=swap, width=width, core_bad=core_bad, periods=nper)
    if mism == 0 and swap == 0 and width == 0 and core_bad == 0:
        print(f"verify_synth_top: PASS -- {nper} I2S periods decoded from the wire, every one identical "
              f"to the model; both channels agree; every slot 32 BCLK; the core's own stream matches too")
        if a.m5a:
            print("verify_synth_top: M5A path verified from SPI pins through the production voice and I2S pins")
        return 0
    print(f"verify_synth_top: FAIL -- of {nper} decoded I2S periods: {mism} differ from the model, "
          f"{swap} have L != R, {width} have a slot that is not 32 BCLK")
    if first:
        p, e, g = first
        print(f"  first wire mismatch at period {p}: model {e}, wire {g}, error {g - e:+d} LSB")
    print(f"  the core's own sample stream: {core_bad} of {len(sm)} frames differ from the model"
          + (f"; first at frame {core_first[0]}: model {core_first[1]}, core {core_first[2]}" if core_first else ""))
    if core_bad == 0 and mism:
        print("  the core is right and the wire is wrong: the defect is in i2s_tx or its timing")
    elif core_bad and mism:
        print("  the core is already wrong: the defect is upstream of the serialiser")
    return 1


if __name__ == "__main__":
    st = main()
    ap_fail = "--expect-fail" in sys.argv
    if ap_fail:
        inj = sys.argv[sys.argv.index("--inject") + 1] if "--inject" in sys.argv else "?"
        if st == 1:
            print(f"verify_synth_top: negative control {inj} CAUGHT (comparison failed as required)"); sys.exit(0)
        print(f"verify_synth_top: NEGATIVE CONTROL NOT CAUGHT (status {st})"); sys.exit(1 if st == 0 else 2)
    sys.exit(st)

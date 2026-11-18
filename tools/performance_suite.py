#!/usr/bin/env python3
"""Full-performance suite: bass, lead, drum-only and mixed phrases (issue #338, plan098 5D).

The top-level playing-behaviour gate. Given a rendered mono phrase and the
EVENT SCHEDULE that was sent to the instrument, it checks, per phrase:

  timing        every note/drum onset is found in the audio within
                [-EARLY_TOL_S, +LATE_TOL_S] of its scheduled time. Onsets are
                found from the audio alone (1 ms RMS envelope, a level floor
                relative to the event's own peak AND a >= 3.5 dB rise within
                3 ms, so a ringing tail cannot masquerade as a new attack).
  presence      every event is audible (peak >= PRESENT_DBFS). A candidate
                cannot pass by going quiet or silent.
  headroom      peak <= HEADROOM_DBFS, zero samples at the rail, and no
                flat-topped runs at the peak (a soft clipper below the rail).
  dropout       inside a held note no 5 ms block is DROPOUT_DB under the
                median block of that note.
  release tail  after gate-off the note is not hard-truncated (level 3-5 ms
                later still within TRUNC_DB of the held level) and has decayed
                by STUCK_DB (or below STUCK_FLOOR_DBFS) `release_s` later --
                else the note is STUCK. Drums are held to the same decay by
                `ring_s`.
  repeated      same-pitch repeats each produce their own onset (the
                `rise` rule above makes a merged/legato repeat read as missing).
  automation    a declared control move (cutoff -> spectral centroid, volume
                -> RMS) must move the measured quantity the declared way, by
                at least `min_ratio`, over the named events.

Three verdicts, never two: PASS, FAIL, and REFUSED. REFUSED means a
precondition of the apparatus failed (non-finite or silent audio, audio shorter
than the schedule, events closer than MIN_IOI_S, a schedule with no event whose
release tail can be judged, an automation declaration that cannot be evaluated
as written -- any event ID out of range or negative, fewer than two distinct
IDs, an unknown kind, a direction other than +/-1, a min_ratio that is not
finite and > 1, a window that overlaps the next onset or is silent) and NOTHING
is reported about the sound. A declaration is never narrowed to its valid part. The CLI
exit codes are 0 / 1 / 2.

Independence from the model under test. The controls here are driven by
`reference_render`, an additive/noise performer written for this file that
shares no code with model/voice_fx.py or model/drums_fx.py, plus defects
injected into ITS schedule or output. The thresholds are stated tolerances, not
fitted to the voice. `--backend synthtop` is the production-path gate: the
phrase is compiled to the chip's register write stream (phrase_writes) and
played through model/synth_top_model.py -- the whole chip, voice and drums
mixed by contract 12's one exact sum -- whose bit-exactness against
synth_top.v at the I2S pins is rtl-sketch/verify_synth_top.py's contract, so
its verdicts are the shipped path's. The full seven-phrase synthtop run is a
standing gate: `test_synthtop_backend_full_suite_passes` re-derives it in
test_performance_suite.py, which runs in the tools/ CI job and `make verify`'s
broad pytest run. `--backend model` is retained for comparison: an UNRUN,
per-note-reset approximation of the instrument (see `model_render`), not the
gate. `--matrix` prints which property each seeded
defect moves and which stay blind; defects seed the REFERENCE performer
(--inject) -- the synthtop backend's controls seed the WRITE STREAM instead
(test_performance_suite.py), because a defect the chip ships is a control
move or an omission, not a magic edit of its output.

What each guard cannot see, so a reader does not over-trust a PASS: timing is
audio-onset based with ~1 ms resolution, on a wideband envelope OR a
first-difference (~6 dB/oct high-pass) one -- the high band exists because the
kit's own bd/sd ring can sit within a few dB of a following hat's peak, where
no wideband rise is possible; an event with no high-band content masked by a
loud high-band neighbour is still invisible to both. The stuck test is only
made where a gap leaves room (>= 25 ms after the allowance) -- each phrase carries gaps and
a final tail, and a phrase with none is REFUSED; automation is tested on the
named events only. A note held past gate-off is always red, but when the next
onset is masked by it the report is often "timing +N ms" on that next event
rather than "stuck": the diagnosis can be the neighbour's, the verdict is not.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import math
import pathlib
import sys

import numpy as np
import refusal

SR = 48_000
FRAME = SR // 1000                    # 1 ms envelope frames
EARLY_TOL_S = 0.005
LATE_TOL_S = 0.010
RISE_RATIO = 1.5                      # onset: env[f] >= RISE_RATIO * env[f-3]
ONSET_FLOOR_REL = 0.1                 # ... and >= 10% of the event's own peak
PRESENT_DBFS = -50.0
HEADROOM_DBFS = -1.0
RAIL = 0.999
FLAT_RUN = 4                          # samples in a plateau at the peak
FLAT_PEAK_MIN = 0.3
FLAT_LEVEL = 0.98                     # plateau sits within 2% of the peak ...
FLAT_STEP_REL = 1e-3                  # ... successive samples differ by <= 0.1% of peak ...
FLAT_KNEE = 2.5                       # ... and entered/left by a step >= 2.5x its largest inner step
DROPOUT_BLOCK = 240                   # 5 ms
DROPOUT_DB = -12.0
HELD_SETTLE_S = 0.05
TRUNC_DB = -20.0
STUCK_DB = -30.0
STUCK_FLOOR_DBFS = -70.0
MIN_IOI_S = 0.02
MIN_GAP_S = 0.025


class Refused(refusal.Refused, RuntimeError):
    """A precondition of the apparatus failed: no verdict about the sound."""


@dataclasses.dataclass(frozen=True)
class Event:
    onset_s: float
    kind: str            # "note" or "drum"
    gate_s: float = 0.0  # notes: gate length
    midi: int = 0        # notes
    drum: str = ""       # drums: "bd" | "sd" | "hh"
    ring_s: float = 0.6  # drums: allowed ring before the stuck test


@dataclasses.dataclass(frozen=True)
class Phrase:
    name: str
    duration_s: float
    events: tuple
    release_s: float = 0.35
    controls: tuple = ()       # (time_s, "cutoff"|"volume", value), sample-and-hold
    automation: tuple = ()     # dicts: kind, events, direction (+1/-1), min_ratio


def _notes(midi, start, ioi, gate, n=None):
    seq = list(midi) if n is None else [midi[i % len(midi)] for i in range(n)]
    return tuple(Event(start + i * ioi, "note", gate, m) for i, m in enumerate(seq))


def phrases() -> dict:
    bass = Phrase("bass", 4.0, _notes([36, 36, 43, 41, 38], 0.2, 0.7, 0.5))
    lead = Phrase("lead", 3.6, _notes([72, 76, 79, 84, 79, 76], 0.2, 0.5, 0.32))
    drums = Phrase("drums", 3.0, tuple(
        Event(0.2 + i * 0.25, "drum", drum=d, ring_s=0.45)
        for i, d in enumerate(["bd", "hh", "sd", "hh", "bd", "bd", "sd", "hh", "bd", "hh"])))
    mix_notes = _notes([40] * 6, 0.3, 0.55, 0.25)
    mix_drums = tuple(Event(0.3 + 0.55 * i + 0.275, "drum", drum=d, ring_s=0.3)
                      for i, d in enumerate(["bd", "sd", "bd", "sd", "bd", "sd"]))
    mixed = Phrase(
        "mixed_automation", 3.9, tuple(sorted(mix_notes + mix_drums, key=lambda e: e.onset_s)),
        release_s=0.3,
        controls=tuple((0.3 + 0.55 * i, "cutoff", 150.0 * 2 ** (i * 1.0)) for i in range(6)),
        automation=({"kind": "centroid", "events": tuple(range(0, 12, 2)), "direction": 1,
                     "min_ratio": 1.5, "of": "note"},))
    repeated = Phrase("repeated_notes", 3.0, _notes([55], 0.2, 0.3, 0.18, n=8), release_s=0.3)
    dense = Phrase("dense", 3.0, _notes([48, 60, 55, 63], 0.2, 0.1, 0.055, n=16), release_s=0.3)
    dense_drums = Phrase("dense_drums", 2.2, tuple(
        Event(0.2 + 0.1 * i, "drum", drum=["bd", "sd", "hh"][i % 3], ring_s=0.5) for i in range(14)))
    # ring_s 0.5, not the reference performer's 0.3: the R1 kit's sd is -22.2 dB
    # down at +0.30 s, -31.3 dB at +0.45 s, -34.3 dB at +0.50 s (synthtop render,
    # unmasked last-sd tail) -- a 0.3 s deadline calls the instrument's own
    # snare ring a stuck note.
    return {p.name: p for p in (bass, lead, drums, mixed, repeated, dense, dense_drums)}


# ------------------------------------------------------------------ reference performer
def _control_at(phrase: Phrase, kind: str, t: float, default: float) -> float:
    v = default
    for (ct, k, val) in sorted(phrase.controls):
        if k == kind and ct <= t + 1e-9:
            v = val
    return v


def reference_render(phrase: Phrase, *, defect: str = "", seed: int = 7) -> np.ndarray:
    """Independent additive/noise performer. `defect` seeds one of DEFECTS."""
    if defect and defect not in DEFECTS:
        raise ValueError(f"unknown defect {defect!r}")
    rng = np.random.default_rng(seed)
    n = int((phrase.duration_s + 0.0) * SR)
    out = np.zeros(n)
    evs = list(phrase.events)
    notes = [i for i, e in enumerate(evs) if e.kind == "note"]
    for i, e in enumerate(evs):
        t0 = e.onset_s
        if defect == "late_one" and i == len(evs) // 2:
            t0 += 0.025
        if defect == "late_all":
            t0 += 0.02
        if defect == "missing" and i == len(evs) // 2:
            continue
        a = int(round(t0 * SR))
        if e.kind == "note":
            gate = e.gate_s
            nxt = evs[notes[notes.index(i) + 1]].onset_s if notes.index(i) + 1 < len(notes) else None
            if defect == "legato" and nxt is not None and nxt - e.onset_s < 0.35:
                gate = nxt - e.onset_s + 0.05
            tau = 0.04
            m = n - a
            t = np.arange(m) / SR
            f0 = 440.0 * 2 ** ((e.midi - 69) / 12)
            fc = _control_at(phrase, "cutoff", e.onset_s, 4000.0)
            if defect == "frozen_automation":
                fc = 4000.0
            sig = np.zeros(m)
            k = 1
            while k * f0 < 20_000 and k <= 40:
                g = 1.0 / k / math.sqrt(1.0 + (k * f0 / fc) ** 4)
                sig += g * np.sin(2 * np.pi * k * f0 * t)
                k += 1
            env = np.minimum(t / 0.003, 1.0)
            go = gate
            rel = np.exp(-np.maximum(t - go, 0.0) / tau)
            if defect == "stuck" and i == notes[1]:
                rel = np.ones(m)                         # never releases
            if defect == "truncate":
                rel = np.where(t > go, 0.0, 1.0)
            vol = _control_at(phrase, "volume", e.onset_s, 1.0)
            out[a:] += 0.35 * vol * sig * env * rel / max(1.0, np.sqrt(k))
        else:
            m = min(n - a, int(2.5 * SR))
            t = np.arange(m) / SR
            if e.drum == "bd":
                ph = 2 * np.pi * (45 * t + 40 * 0.05 * (1 - np.exp(-t / 0.05)))
                sig = np.sin(ph) * np.exp(-t / 0.12)
            elif e.drum == "sd":
                sig = 0.6 * np.sin(2 * np.pi * 180 * t) * np.exp(-t / 0.05) + \
                    0.6 * rng.standard_normal(m) * np.exp(-t / 0.06)
            else:
                sig = np.diff(rng.standard_normal(m + 1)) * np.exp(-t / 0.025) * 0.4
            sig = sig * np.minimum(t / 0.001, 1.0)
            if defect == "stuck" and e.drum == "bd" and not notes:
                sig = np.sin(2 * np.pi * 45 * t) * 0.05         # rings forever, BELOW the headroom guard
            out[a:a + m] += 0.25 * sig[:n - a]
    if defect == "clip":
        out = np.clip(out * 4.0, -1.0, 1.0)
    elif defect == "soft_clip":
        out = np.clip(out * 3.0, -0.6, 0.6)
    elif defect == "dropout":
        for e in evs:
            if e.kind == "note" and e.gate_s >= 0.2:
                a = int((e.onset_s + 0.12) * SR)
                out[a:a + int(0.02 * SR)] = 0.0
                break
    elif defect == "quiet":
        out = out * 0.05
    elif defect == "silent":
        out = out * 0.0
    return out


DEFECTS = ("clip", "soft_clip", "dropout", "stuck", "late_one", "late_all", "missing",
           "legato", "frozen_automation", "truncate", "quiet", "silent")


# ------------------------------------------------------------------ analysis
ENV_WIN = 8 * FRAME                   # 8 ms trailing window: a 1 ms RMS of a 45 Hz
                                     # kick fluctuates wildly and fakes rises


def _env(x: np.ndarray) -> np.ndarray:
    """RMS over the trailing ENV_WIN samples, sampled every 1 ms (frame f ends
    at (f+1) ms). Frames before a full window use what exists."""
    c = np.concatenate([[0.0], np.cumsum(x * x)])
    m = len(x) // FRAME
    end = (np.arange(m) + 1) * FRAME
    start = np.maximum(end - ENV_WIN, 0)
    return np.sqrt((c[end] - c[start]) / (end - start))


def _db(v: float) -> float:
    return 20.0 * math.log10(max(float(v), 1e-12))


def preconditions(x, phrase: Phrase) -> np.ndarray:
    x = np.asarray(x)
    if x.ndim != 1:
        raise Refused("audio must be mono 1-D")
    x = x.astype(np.float64)
    if not np.all(np.isfinite(x)):
        raise Refused("non-finite samples")
    if len(x) < int(phrase.duration_s * SR) - 1:
        raise Refused(f"audio {len(x)/SR:.3f}s shorter than the schedule {phrase.duration_s}s")
    if not phrase.events:
        raise Refused("empty schedule")
    evs = phrase.events
    odd = sorted({e.kind for e in evs} - {"note", "drum"})
    if odd:
        # anything not "note" would otherwise be judged as a drum, silently
        # dropping the note-only dropout and truncation checks
        raise Refused(f"event kinds {odd} are not 'note' or 'drum'")
    if any(b.onset_s - a.onset_s < MIN_IOI_S for a, b in zip(evs, evs[1:])):
        raise Refused(f"events closer than {MIN_IOI_S}s cannot be resolved")
    if any(e.onset_s < 0.05 or e.onset_s + (e.gate_s or 0) > phrase.duration_s for e in evs):
        raise Refused("event outside the phrase window")
    if float(np.max(np.abs(x))) < 10 ** (-60 / 20):
        raise Refused("audio is silent (peak below -60 dBFS): nothing to judge")
    last = evs[-1]
    tail_end = last.onset_s + (last.gate_s + phrase.release_s if last.kind == "note" else last.ring_s)
    if tail_end + 0.02 > phrase.duration_s:
        raise Refused("phrase ends before the last event's release tail can be judged")
    for j, au in enumerate(phrase.automation):
        _check_automation(j, au, phrase)
    return x


AUTO_SKIP_S = 0.03                    # automation window starts 30 ms after the found onset ...
AUTO_WIN_S = 0.08                     # ... and lasts 80 ms
AUTO_KINDS = ("centroid", "rms")
AUTO_KEYS = {"kind", "events", "direction", "min_ratio"}
AUTO_OPTIONAL = {"of"}


def _late_tol(e: Event) -> float:
    """Allowed lateness. An energy onset of a low note cannot resolve better
    than a quarter period of its fundamental (the first quarter cycle carries
    almost no energy), so the note allowance grows by that much."""
    return LATE_TOL_S + (0.25 / (440.0 * 2 ** ((e.midi - 69) / 12)) if e.kind == "note" else 0.0)


def _check_automation(j: int, au, phrase: Phrase) -> None:
    """REFUSE a declaration that cannot be evaluated as written. Never narrow
    it to the part that can: a declaration with one bad ID is a wrong
    declaration, and measuring the rest would report on a move nobody asked
    about (review #606: (), (0,), (100, 101) all PASSED a frozen sweep)."""
    where = f"automation declaration {j}"
    if not isinstance(au, dict):
        raise Refused(f"{where}: not a dict: {au!r}")
    missing, extra = AUTO_KEYS - set(au), set(au) - AUTO_KEYS - AUTO_OPTIONAL
    if missing or extra:
        raise Refused(f"{where}: missing keys {sorted(missing)}, unknown keys {sorted(extra)}")
    if au["kind"] not in AUTO_KINDS:
        raise Refused(f"{where}: kind {au['kind']!r} not one of {AUTO_KINDS}")
    d = au["direction"]
    if isinstance(d, bool) or d not in (1, -1):
        raise Refused(f"{where}: direction {d!r} must be +1 or -1")
    r = au["min_ratio"]
    if isinstance(r, bool) or not isinstance(r, (int, float)) or not math.isfinite(r) or r <= 1.0:
        raise Refused(f"{where}: min_ratio {r!r} must be finite and > 1 (else the ratio test is vacuous)")
    ids = au["events"]
    evs = phrase.events
    if not isinstance(ids, (tuple, list)):
        raise Refused(f"{where}: events {ids!r} is not a sequence of event IDs")
    bad = [k for k in ids if isinstance(k, bool) or not isinstance(k, (int, np.integer))
           or not 0 <= k < len(evs)]
    if bad:
        raise Refused(f"{where}: event IDs {bad!r} are not integer IDs in 0..{len(evs) - 1}; "
                      f"the declaration is refused, not narrowed")
    if len(ids) < 2:
        raise Refused(f"{where}: {len(ids)} event(s); a move needs at least two")
    if any(b <= a for a, b in zip(ids, ids[1:])):
        raise Refused(f"{where}: event IDs {list(ids)} must be distinct and in schedule order")
    of = au.get("of")
    if of is not None:
        if of not in ("note", "drum"):
            raise Refused(f"{where}: of={of!r} must be 'note' or 'drum'")
        wrong = [k for k in ids if evs[k].kind != of]
        if wrong:
            raise Refused(f"{where}: events {wrong} are not {of}s")
    for k in ids:
        e = evs[k]
        end = e.onset_s + _late_tol(e) + AUTO_SKIP_S + AUTO_WIN_S
        nxt = evs[k + 1].onset_s if k + 1 < len(evs) else phrase.duration_s
        if end > nxt:
            raise Refused(f"{where}: event {k}'s measurement window (to {end:.3f}s at the late "
                          f"limit) runs past the next onset/phrase end at {nxt:.3f}s")


def _centroid(seg: np.ndarray) -> float:
    w = np.hanning(len(seg))
    s = np.abs(np.fft.rfft(seg * w)) ** 2
    f = np.fft.rfftfreq(len(seg), 1 / SR)
    return float((f * s).sum() / max(s.sum(), 1e-30))


def flat_top_run(x: np.ndarray, peak: float) -> int:
    """Longest PLATEAU at the peak, 0 if none reaches FLAT_RUN samples.

    A plateau is a run of samples within FLAT_LEVEL of the peak whose successive
    differences are <= FLAT_STEP_REL of the peak AND which is entered or left
    by a step of at least FLAT_KNEE times its largest inner step (and above
    FLAT_STEP_REL * peak). The knee condition is what separates a clipper from
    smooth curvature: a sine's crest also spends many samples within 2% of its
    maximum, but its steps shrink gradually toward the crest, so the step that
    leaves the run is at most ~(k+1)/k times the largest inside it, never
    2.5x. A clipper's inner steps are ~0 and its knee is abrupt, at any
    fundamental. Cannot see: a clip so shallow its knee is under the step
    floor (inaudible; the headroom guard still applies)."""
    d = np.diff(x)
    still = (np.abs(d) <= FLAT_STEP_REL * peak) & (np.abs(x[:-1]) >= FLAT_LEVEL * peak) \
        & (np.abs(x[1:]) >= FLAT_LEVEL * peak)
    floor = FLAT_STEP_REL * peak
    best = i = 0
    n = len(still)
    while i < n:
        if not still[i]:
            i += 1
            continue
        j = i
        while j < n and still[j]:
            j += 1
        knee = max(floor, FLAT_KNEE * float(np.max(np.abs(d[i:j]))))
        entered = i > 0 and abs(d[i - 1]) > knee
        left = j < len(d) and abs(d[j]) > knee
        if (entered or left) and j - i + 1 >= FLAT_RUN:
            best = max(best, j - i + 1)
        i = j
    return best


PROPERTIES = ("timing", "presence", "headroom", "dropout", "release", "automation")


def failed_property(msg: str) -> str:
    """Which property a failure message belongs to. Unclassifiable text is an
    error, not a default: a new check must say which property it decides."""
    if msg.startswith(("headroom:", "clipping:")):
        return "headroom"
    if msg.startswith("timing:"):
        return "timing"
    if msg.startswith("dropout:"):
        return "dropout"
    if msg.startswith(("release:", "stuck:")):
        return "release"
    if msg.startswith("automation:"):
        return "automation"
    if "inaudible" in msg or "no onset" in msg:
        return "presence"
    raise ValueError(f"failure message belongs to no property: {msg!r}")


def evaluate(x, phrase: Phrase) -> dict:
    x = preconditions(x, phrase)
    env = _env(x)
    # ~6 dB/oct high-passed copy: a hat under lingering low tails (the #338
    # finding: the kit's sd/bd ring sits ~3 dB under the next hat's own peak
    # 100 ms in, so NO wideband rise is possible)
    env_hi = _env(np.diff(x))
    fr = lambda t: int(round(t * 1000))          # noqa: E731
    fails: list[str] = []
    info: dict = {"events": []}
    # ---- headroom
    peak = float(np.max(np.abs(x)))
    rail = int(np.count_nonzero(np.abs(x) >= RAIL))
    info["peak_dbfs"] = round(_db(peak), 2)
    info["rail_samples"] = rail
    if _db(peak) > HEADROOM_DBFS:
        fails.append(f"headroom: peak {_db(peak):.2f} dBFS > {HEADROOM_DBFS}")
    if rail:
        fails.append(f"clipping: {rail} samples at the rail")
    if peak > FLAT_PEAK_MIN:
        best = flat_top_run(x, peak)
        if best:
            info["flat_run"] = best
            fails.append(f"clipping: flat-topped run of {best} samples at the peak")
    # ---- per event
    evs = phrase.events
    judged_tails = 0
    onsets = []
    for i, e in enumerate(evs):
        t = e.onset_s
        nxt = evs[i + 1].onset_s if i + 1 < len(evs) else phrase.duration_s
        lo, hi = max(0, fr(t - 0.01)), min(len(env), fr(nxt - 0.005 if i + 1 < len(evs) else nxt))
        pk = float(env[lo:hi].max()) if hi > lo else 0.0
        hi_end = min(hi, len(env_hi))
        rec = {"i": i, "kind": e.kind, "peak_dbfs": round(_db(pk), 1)}
        if _db(pk) < PRESENT_DBFS:
            fails.append(f"event {i} ({e.kind}@{t:.3f}s): inaudible, peak {_db(pk):.1f} dBFS")
            rec["onset_found"] = False
            info["events"].append(rec)
            onsets.append(None)
            continue
        thr = ONSET_FLOOR_REL * pk
        thr_hi = ONSET_FLOOR_REL * pk        # the wideband peak anchors BOTH floors:
        found = None                         # a hi-band claim must be a tenth of the
        for f in range(lo, hi_end):          # event's audible peak, else the release
            rises = f < 3 or env[f] >= RISE_RATIO * env[f - 3]   # tail's own spectral
            if env[f] >= thr and rises:      # wiggle reads as the next drum's attack
                found = f                    # (mixed_automation's bd@2.775 read -10 ms
                break                        # early, #338)
            rises_hi = f < 3 or env_hi[f] >= RISE_RATIO * env_hi[f - 3]
            if env_hi[f] >= thr_hi and rises_hi:
                found = f
                break
        if found is None:
            fails.append(f"event {i} ({e.kind}@{t:.3f}s): no onset found (missing or merged)")
            rec["onset_found"] = False
            onsets.append(None)
            info["events"].append(rec)
            continue
        late = found / 1000.0 - t
        rec["onset_found"] = True
        rec["lateness_ms"] = round(late * 1000, 1)
        onsets.append(found)
        late_tol = _late_tol(e)
        rec["late_allowed_ms"] = round(late_tol * 1000, 1)
        if late > late_tol or late < -EARLY_TOL_S:
            fails.append(f"timing: event {i} ({e.kind}@{t:.3f}s) {late*1000:+.1f} ms "
                         f"(allowed -{EARLY_TOL_S*1000:.0f}..+{late_tol*1000:.1f})")
        # ---- sustain / release for notes
        goff = t + e.gate_s if e.kind == "note" else t + e.ring_s
        allow = phrase.release_s if e.kind == "note" else 0.0
        held = None
        if e.kind == "note":
            a, b = fr(t + HELD_SETTLE_S), fr(t + e.gate_s - 0.01)
            if b - a >= 40:
                seg = x[a * FRAME:b * FRAME]
                m = len(seg) // DROPOUT_BLOCK
                blocks = np.sqrt(np.mean(seg[:m * DROPOUT_BLOCK].reshape(m, DROPOUT_BLOCK) ** 2, axis=1))
                med = float(np.median(blocks))
                held = med
                if med > 0 and _db(blocks.min() / med) < DROPOUT_DB:
                    fails.append(f"dropout: note {i} ({t:.3f}s) block {_db(blocks.min()/med):.1f} dB "
                                 f"under its median")
            ref = held if held else pk
            if held is not None:
                g = int((t + e.gate_s) * SR)
                after = float(np.max(np.abs(x[g + 2 * FRAME:g + 6 * FRAME])))
                before = float(np.max(np.abs(x[int((t + HELD_SETTLE_S) * SR):g])))
                if _db(after / max(before, 1e-12)) < TRUNC_DB:
                    fails.append(f"release: note {i} ({t:.3f}s) hard-truncated at gate-off "
                                 f"({_db(after/before):.1f} dB in 2-6 ms)")
        ref = held if held else pk
        T = goff + allow
        if T + MIN_GAP_S <= nxt - 0.005:
            judged_tails += 1
            tail = env[fr(T):fr(T) + 20]
            lvl = _db(tail.mean() / max(ref, 1e-12))
            rec["tail_db_vs_held"] = round(lvl, 1)
            if lvl > STUCK_DB and _db(tail.mean()) > STUCK_FLOOR_DBFS:
                fails.append(f"stuck: {e.kind} {i} ({t:.3f}s) only {lvl:.1f} dB down "
                             f"{allow if allow else e.ring_s:.2f}s after release")
        info["events"].append(rec)
    if judged_tails == 0 and not fails:
        raise Refused("no event had a gap long enough to judge its release tail")
    # ---- automation
    # Declarations were validated in preconditions(); every ID is in range,
    # there are >= 2 of them, and each nominal window fits before the next onset.
    for j, au in enumerate(phrase.automation):
        ids = list(au["events"])
        vals = []
        missing = [k for k in ids if onsets[k] is None]
        for k in missing:
            fails.append(f"automation: event {k} missing, move cannot be measured")
        if missing:
            continue                    # already red; a partial move is not measured
        for k in ids:
            a = onsets[k] * FRAME + int(AUTO_SKIP_S * SR)
            seg = x[a:a + int(AUTO_WIN_S * SR)]
            if len(seg) < int(AUTO_WIN_S * SR):
                raise Refused(f"automation declaration {j}: event {k}'s window runs off the record")
            v = _centroid(seg) if au["kind"] == "centroid" else float(np.sqrt(np.mean(seg ** 2)))
            if not (math.isfinite(v) and v > 0.0):
                raise Refused(f"automation declaration {j}: event {k}'s window is silent "
                              f"({au['kind']} = {v!r}), nothing to measure")
            vals.append(v)
        d = au["direction"]
        mono = all(d * (b - a) >= -0.05 * abs(a) for a, b in zip(vals, vals[1:]))
        ratio = vals[-1] / vals[0] if d > 0 else vals[0] / vals[-1]
        info.setdefault("automation", []).append([round(v, 1) for v in vals])
        if not mono or ratio < au["min_ratio"]:
            fails.append(f"automation: {au['kind']} {[round(v) for v in vals]} did not move "
                         f"{'up' if d > 0 else 'down'} by >= {au['min_ratio']}x")
    bad = {failed_property(f) for f in fails}
    return {"phrase": phrase.name, "verdict": "FAIL" if fails else "PASS",
            "failures": fails, "info": info,
            "props": {k: ("FAIL" if k in bad else "ok") for k in PROPERTIES}}


def run_suite(render, names=None) -> list[dict]:
    """Run the named phrases (all if `names` is None). An unknown name or an
    empty selection raises Refused: skipping requested work must not read as a pass."""
    known = phrases()
    if names is not None:
        unknown = [n for n in names if n not in known]
        if unknown or not names:
            raise Refused(f"unknown or empty phrase selection {list(names)!r}: "
                          f"{unknown or 'nothing requested'} (known: {sorted(known)})")
    out = []
    for name, ph in known.items():
        if names and name not in names:
            continue
        try:
            out.append(evaluate(render(ph), ph))
        except Refused as r:
            out.append({"phrase": name, "verdict": "REFUSED", "failures": [str(r)], "info": {}})
    return out


def worst(results) -> str:
    v = {r["verdict"] for r in results}
    return "REFUSED" if "REFUSED" in v or not v else "FAIL" if "FAIL" in v else "PASS"


def defect_matrix(names=None) -> dict:
    """Properties x defects. For every seeded defect (and "" = clean), the set of
    properties that went red on ANY phrase. A property absent from a defect's
    row is BLIND to that defect; present is MOVED. Machine-readable so a reader
    sees what each check cannot see, not only what it caught
    (docs/verification-rules.md rule 4)."""
    m = {}
    for d in ("",) + DEFECTS:
        res = run_suite(lambda p, d=d: reference_render(p, defect=d), names)
        if d == "silent":
            m[d] = {"verdict": worst(res), "moved": [], "blind": list(PROPERTIES)}
            continue
        moved = sorted({k for r in res for k, v in r["props"].items() if v == "FAIL"})
        m[d] = {"verdict": worst(res), "moved": moved,
                "blind": [k for k in PROPERTIES if k not in moved]}
    return m


# ------------------------------------------------------------------ model backend (heavy)
def model_render(phrase: Phrase, engine: str = "r1") -> np.ndarray:
    """Render the schedule through the fixed-point voice / drums. HEAVY: a real
    phrase is minutes of simulation; run on the build box.

    NOT the production path, so a PASS here is not the #338 instrument gate:
    each note gets a freshly reset voice, summed at its onset (no continuous
    mono state or retrigger), and the drums are mixed against a zero voice with
    the voice added afterwards in floating point (not the combined production
    mixer). Wiring the continuous schedule through the shipped mixer is the open
    part of #338."""
    root = pathlib.Path(__file__).resolve().parents[1]
    sys.path[:0] = [str(root / "model"), str(root / "tools")]
    import drums_fx as dx
    import mono_artifact_probe as mp
    n = int(phrase.duration_s * SR)
    out = np.zeros(n)
    for e in phrase.events:
        if e.kind != "note":
            continue
        voice = mp.make_voice(engine)
        voice.reset()
        dur = e.gate_s + phrase.release_s
        fc = _control_at(phrase, "cutoff", e.onset_s, 4000.0)
        # (start, end) of the filter envelope; sustain 1.0 holds the END value, so
        # both must be the commanded cutoff or the control is silently ignored
        # (first model run: centroid flat at ~208 Hz with end=20000).
        patch = mp.held_patch(amp=(0.004, 0.05, 1.0, 0.04), cutoff=(fc, fc))
        y = np.asarray(voice.run(voice.note_on(e.midi, dur, gate=e.gate_s, **patch)), float) / 32768.0
        a = int(round(e.onset_s * SR))
        out[a:a + len(y)] += y[:n - a]
    names = {"bd": "BD", "sd": "SD", "hh": "CH"}
    dr = [e for e in phrase.events if e.kind == "drum"]
    if dr:
        pattern = sorted({names[e.drum] for e in dr})
        kit = dx.kit_with_sounds(*pattern)
        hits = [(int(round(e.onset_s * SR)), dx.SOUND_STOP[names[e.drum]], 1.0) for e in dr]
        d = dx.DrumsFx()
        w = sorted(dx.hit_writes(hits, kit), key=lambda t: t[0])
        dm, bd = d.play(w, n)
        mix = dx.output_fx(np.zeros(n), 0, dm, dx.accent_reg(0.45), bd, dx.accent_reg(0.45))
        out += np.asarray(mix, float) / 32768.0
    return out


# ------------------------------------------------------------------ synth_top backend (the shipped path)
SYNTHTOP_PATCH = dict(waves=("saw", "saw", "saw"), detune=(0.0, 0.0, 0.0),
                      mix=(1.0, 0.0, 0.0), noise=0.0, q=0.0, drive=0.75,
                      amp=(0.004, 0.05, 1.0, 0.04), fenv=(0.004, 0.05, 1.0, 0.04),
                      track=0.0, vol=0.45)
SYNTHTOP_CUTOFF_HZ = 4000.0        # commanded cutoff where the phrase declares none
SYNTHTOP_DRUM_GAIN = 0.45          # DVOL/BVOL, verify_synth_top's reference gains
SYNTHTOP_ACCENT = 1.0
DRUM_STOP_NAME = {"bd": "BD", "sd": "SD", "hh": "CH"}

# Which chip build renders. r1 is the published release (fpga/release/r1-candidate.json:
# OSC2X=1 FILTER2X=1 PULSE2X=0, RTL frozen at 6864435a) -- the same mapping
# tools/mono_artifact_probe.ENGINES carries onto synth_top_model's construction
# flags. `baseline` (no 2x paths) is NOT the shipped instrument: on it a saw's
# first half-cycle pins the ladder drive's tanh and prints a 4 ms flat-topped
# start-up plateau the headroom guard reads as clipping.
SYNTHTOP_ENGINES = {
    "r1": dict(oversample_2x=True, filter_2x=True, pulse_2x=False),
    "pulse2x": dict(oversample_2x=True, filter_2x=True, pulse_2x=True),
    "baseline": dict(oversample_2x=False, filter_2x=False, pulse_2x=False),
}
SYNTHTOP_ENGINE = "r1"


def phrase_writes(phrase, *, patch=None, cutoff_hz=SYNTHTOP_CUTOFF_HZ,
                  drum_gain=SYNTHTOP_DRUM_GAIN, accent=SYNTHTOP_ACCENT) -> list:
    """The phrase as the chip's write stream -- (frame, flag, sec, addr, data),
    the only thing a host can actually send. Frame f means round(t*SR); the
    model applies writes at the START of the frame they name (synth_top_model's
    register contract; the link's own latency is verify_ctl/verify_synth_top's
    business and is irrelevant at these tolerances).

    Per note: the three INC registers as JUMPS (flag = 1 -- no glide between
    phrase notes), TRACK, the commanded cutoff as cut_lo AND cut_hi (the
    envelope's sustain holds the END value, so one-sided programming would
    silently ignore the control -- first model run, 6c94bdb2) whenever the
    commanded cutoff changes, then GATE_ON; GATE_OFF at the gate's frame.
    Drums: the kit image at frame 0, then STOPS pulses via drums_fx.hit_writes
    (the bit drops the next frame so a repeat is an edge again).

    The voice is MONO: gates that overlap retrigger, they do not voice two
    notes at once -- that is what the shipped instrument does with the
    schedule, and every frozen phrase keeps gates below inter-onsets.

    REFUSES a drum name outside DRUM_STOP_NAME and a gate-off or drum write
    landing past the record: the chip's run() would assert mid-render, and a
    truncated stream must not look like data."""
    root = pathlib.Path(__file__).resolve().parents[1]
    sys.path[:0] = [str(root / "model"), str(root / "tools")]
    import drums_fx as dx
    import synth_top_model as st
    import voice_fx as vf

    p = dict(SYNTHTOP_PATCH if patch is None else patch)
    regs = vf.VoiceFx.patch_regs(**p)
    fr = lambda t: int(round(t * SR))          # noqa: E731
    w: list = []
    put = lambda f, fl, a, d: w.append((f, fl, st.SEC_VOICE, a, d))   # noqa: E731

    # the boot-time image: the exact mapping rtl-sketch/verify_synth_top._voice_image writes
    for k, s_ in enumerate(regs["waves"]):
        put(0, 0, st.A_WAVE + k, vf.WAVE_CODE[s_])
    for base, key in ((st.A_AMP, "amp"), (st.A_FILT, "fenv")):
        for j, v in enumerate(regs[key]):
            put(0, 0, base + j, v)
    for nm, key in (("A_CUT_LO", "cut_lo"), ("A_CUT_HI", "cut_hi"), ("A_K", "k"),
                    ("A_GAIN", "gain"), ("A_OGAIN", "ogain"), ("A_GLIDE", "glide")):
        put(0, 0, getattr(st, nm), regs[key])
    put(0, 0, st.A_VOL, regs["vol"])
    put(0, 0, st.A_DVOL, dx.accent_reg(drum_gain))
    put(0, 0, st.A_BVOL, dx.accent_reg(drum_gain))
    for k, g in enumerate(regs["weights"]):
        put(0, 0, st.A_W + k, g)

    n = int(phrase.duration_s * SR)
    last_fc = None
    for e in phrase.events:
        if e.kind != "note":
            continue
        f, fg = fr(e.onset_s), fr(e.onset_s + e.gate_s)
        if fg >= n:
            raise Refused(f"note at {e.onset_s:.3f}s: gate-off lands at frame {fg}, "
                          f"past the {n}-frame record")
        fc = _control_at(phrase, "cutoff", e.onset_s, cutoff_hz)
        if fc != last_fc:                      # before the gate: the envelope must start there
            put(f, 0, st.A_CUT_LO, vf.usat(int(round(fc)), vf.CUT_BITS))
            put(f, 0, st.A_CUT_HI, vf.usat(int(round(fc)), vf.CUT_BITS))
            last_fc = fc
        for k, v in enumerate(vf.VoiceFx.note_incs(e.midi, regs["detune"])):
            put(f, 1, st.A_INC + k, v)
        put(f, 0, st.A_TRACK, vf.VoiceFx.note_track(e.midi, p["track"]))
        put(f, 0, st.A_GATE_ON, 0)
        put(fg, 0, st.A_GATE_OFF, 0)

    drums = [e for e in phrase.events if e.kind == "drum"]
    if drums:
        bad = sorted({e.drum for e in drums} - set(DRUM_STOP_NAME))
        if bad:
            raise Refused(f"drum names {bad} are not one of {sorted(DRUM_STOP_NAME)}; "
                          f"the schedule names a circuit the host cannot name")
        kit = dx.kit_with_sounds(*(DRUM_STOP_NAME[e.drum] for e in drums))
        dw = dx.hit_writes([(fr(e.onset_s),
                             dx.SOUND_STOP[DRUM_STOP_NAME[e.drum]],
                             accent) for e in drums], kit)
        late = max(f for f, _, _ in dw)
        if late >= n:
            raise Refused(f"drum write lands at frame {late}, past the "
                          f"{n}-frame record: the chip's run() would assert "
                          f"mid-render, and a truncated stream must not look "
                          f"like data")
        w.extend((f, 0, st.SEC_DRUM, a, v) for f, a, v in dw)
    return w


def render_writes(writes, duration_s: float, *, engine: str = SYNTHTOP_ENGINE) -> np.ndarray:
    """An explicit write stream through the chip model (synth_top_model.run),
    built as the named release configuration (SYNTHTOP_ENGINES; default r1).
    The control surface for seeding a defect in the STREAM: the suite judges
    the audio against the schedule either way, so a control that omits
    gate-offs or floors the volume mid-note is a defect the host shipped, not
    an edit to the judged audio. An unknown engine is REFUSED, not defaulted."""
    if engine not in SYNTHTOP_ENGINES:
        raise Refused(f"engine {engine!r} is not one of {sorted(SYNTHTOP_ENGINES)}")
    root = pathlib.Path(__file__).resolve().parents[1]
    sys.path[:0] = [str(root / "model"), str(root / "tools")]
    import synth_top_model as st

    m = st.SynthTopModel(**SYNTHTOP_ENGINES[engine])
    out = m.run(writes, int(duration_s * SR))
    return np.asarray(out["sample"], dtype=np.float64) / 32768.0


def synthtop_render(phrase, *, engine: str = SYNTHTOP_ENGINE) -> np.ndarray:
    """phrase_writes -> render_writes: the phrase through the shipped chip's
    register interface and contract-12 mixer."""
    return render_writes(phrase_writes(phrase), phrase.duration_s, engine=engine)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--backend", choices=("reference", "model", "synthtop"),
                    default="reference")
    ap.add_argument("--engine", choices=tuple(SYNTHTOP_ENGINES), default=None,
                    help="chip build the synthtop backend renders (default r1)")
    ap.add_argument("--inject", default="", help="seed a defect into the reference render")
    ap.add_argument("--expect-fail", action="store_true")
    ap.add_argument("--phrase", action="append")
    ap.add_argument("--out", type=pathlib.Path)
    ap.add_argument("--matrix", action="store_true",
                    help="print the properties x seeded-defects MOVED/BLIND matrix as JSON")
    a = ap.parse_args(argv)
    if a.matrix:
        print(json.dumps(defect_matrix(), indent=1))
        return 0
    if a.inject and a.backend != "reference":
        print("REFUSED: --inject seeds the reference performer only", file=sys.stderr)
        return 2
    if a.engine is not None and a.backend != "synthtop":
        print("REFUSED: --engine selects the synthtop backend's chip build only",
              file=sys.stderr)
        return 2
    render = {"reference": lambda p: reference_render(p, defect=a.inject),
              "model": model_render,
              "synthtop": lambda p: synthtop_render(p, engine=a.engine or SYNTHTOP_ENGINE)}[a.backend]
    try:
        res = run_suite(render, a.phrase)
    except Refused as r:
        print(f"REFUSED: {r}", file=sys.stderr)
        return 2
    for r in res:
        print(f"{r['verdict']:8s} {r['phrase']}")
        for f in r["failures"]:
            print("    " + f)
    v = worst(res)
    if a.out:
        a.out.write_text(json.dumps({"verdict": v, "results": res}, indent=1) + "\n")
    print(f"SUITE {v}")
    if v == "REFUSED":
        return 2
    return (0 if v == "FAIL" else 1) if a.expect_fail else (0 if v == "PASS" else 1)


if __name__ == "__main__":
    raise SystemExit(main())

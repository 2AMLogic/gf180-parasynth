"""First M1A comparison. Attack now qualified; Model D cross-check remains
unavailable through the qualified host (docs/scorecard/mono-m5a-policy.md).

The mapping is fixed before rendering. Uncalibrated envelope controls are
explicit hypotheses, never inferred from the Mini V3 normalized knob numbers.
"""
from __future__ import annotations
import hashlib
import json
import math
from pathlib import Path
import numpy as np
from scipy.io import wavfile
import mono_m5a_score as lead
import measure_mono_m1a_reference as reference

ROOT, SR = lead.ROOT, lead.SR
MANIFEST = ROOT / "docs/scorecard/mono-m1a-miniv3/manifest.json"
Refused = lead.Refused


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_reference(path=MANIFEST):
    try:
        manifest = json.loads(path.read_text())
        if manifest.get("case_id") != "M1A" or not manifest.get("valid"):
            raise Refused("M1A reference manifest is not qualified")
        if manifest["events"] != list(reference.EVENTS):
            raise Refused("M1A reference timeline differs")
        for item in [*manifest["renders"], *manifest["controls"].values()]:
            if sha(path.parent / item["file"]) != item["sha256"]:
                raise Refused("M1A reference audio hash mismatch")
        if len(manifest["renders"]) != 3 or len({r["sha256"] for r in manifest["renders"]}) != 1:
            raise Refused("M1A reference lacks three identical repeats")
        sr, pcm = wavfile.read(path.parent / manifest["renders"][0]["file"])
        if sr != SR or pcm.dtype != np.float32 or pcm.ndim != 1:
            raise Refused("M1A reference format differs")
        if len(pcm) != round(reference.SECONDS * SR) - manifest["renders"][0]["apparatus"]["latency_removed"]:
            raise Refused("M1A reference length differs")
        if not np.isfinite(pcm).all() or np.max(np.abs(pcm)) <= 1e-5:
            raise Refused("M1A reference silent or non-finite")
        return manifest, pcm.astype(np.float64)
    except (OSError, KeyError, ValueError) as exc:
        raise Refused(f"M1A reference unavailable: {exc}") from exc


# The M1A patch is a NAMED, VERSIONED identity, applied in the instrument
# patch before rendering -- never by normalising an exported WAV. Each version
# names the evidence that selected it. The scorecard record carries the id
# (`diagnostics.configuration.patch_id`), so a board row always says which
# patch it describes.
PATCH_VERSIONS = {
    "m1a-provisional-v1": {
        "output_volume_db": 0.,
        "selected_by": "initial provisional mapping (docs/scorecard/mono-m1a-miniv3/model-comparison.md)",
    },
    "m1a-gain-minus4db-v2": {
        "output_volume_db": -4.,
        "selected_by": "fixed -4 dB output-volume candidate, PR #197 "
                       "(docs/scorecard/mono-m1a-miniv3/volume-mapping/report.json); "
                       "Gain +4.97 -> -1.04 dB, every existing pass preserved",
    },
}
SELECTED_PATCH = "m1a-gain-minus4db-v2"


def patch_for_reference(manifest, patch_id=SELECTED_PATCH):
    if patch_id not in PATCH_VERSIONS:
        raise Refused(f"unknown M1A patch identity: {patch_id}")
    volume_db = PATCH_VERSIONS[patch_id]["output_volume_db"]
    controls = manifest["controls"]
    octave_db = controls["osc2_open"]["rms_dbfs"] - controls["osc1_open"]["rms_dbfs"]
    release_ms = float(np.median([e["envelope"]["release_t20_ms"]
                                  for e in manifest["renders"][0]["events"]]))
    cutoff = round(float(np.median(manifest["filter_rest_ring_hz"])))
    patch = dict(waves=("saw", "saw", "saw"), detune=(0., 12., 0.),
                 mix=(1., 10 ** (octave_db / 20), 0.), noise=0.,
                 cutoff=(cutoff, 2 * cutoff), q=.05, drive=.75,
                 amp=(.010, .25, .75, release_ms / 1000 * 4 / math.log(10)),
                 fenv=(.004, .05, 0., .05), track=0., vol=.45,
                 mod_mix=0., mod_wheel=0., osc_mod=False, filt_mod=False)
    if volume_db:
        # the exact expression the volume-mapping candidate rendered with, so
        # the selected patch is bit-identical to the audio that was accepted
        patch["vol"] = patch["vol"] * 10 ** (volume_db / 20)
    return patch


# attack_fit's KNOWN-SIGNAL domain, from its own qualification (docstring of
# measure_mono_m1a_reference.attack_fit): 10-90% spans from ramps of 0.5-20 ms
# and shapes p in {0.5, 1, 2}. The fit also searches p = 3, 4 and cannot
# return a ramp shorter than 32 samples (0.667 ms); a fit on that minimum is a
# search boundary, which establishes nothing about the true attack. A reading
# outside the domain on EITHER side is not a measurement this estimator has
# been shown to make, so the comparison is UNQUALIFIED -- neither pass nor fail.
ATTACK_QUALIFIED_SHAPES = (0.5, 1.0, 2.0)
ATTACK_QUALIFIED_RAMP_MS = (0.5, 20.0)
ATTACK_FIT_MIN_RAMP_MS = 32 * 1000 / 48000


def attack_fit_qualified(fit):
    """Why this attack fit is outside the estimator's validated domain, or None."""
    if fit["shape_p"] not in ATTACK_QUALIFIED_SHAPES:
        return f"shape p={fit['shape_p']:g} outside qualified {{0.5, 1, 2}}"
    if fit["ramp_ms"] <= ATTACK_FIT_MIN_RAMP_MS + 1e-9:
        return f"ramp {fit['ramp_ms']:.3f} ms on the fit's search minimum"
    lo, hi = ATTACK_QUALIFIED_RAMP_MS
    if not lo <= fit["ramp_ms"] <= hi:
        return f"ramp {fit['ramp_ms']:.3f} ms outside qualified {lo}-{hi} ms"
    return None


def invalid(reason, units="ms"):
    return {"valid": False, "state": "no verdict", "why": reason, "units": units}


def compare_audio(ours, ref, *, attack_domain_rule=True):
    """`attack_domain_rule=False` exists ONLY for the control that shows the
    pre-rule scorer would have graded an out-of-domain attack."""
    ours, ref = np.asarray(ours, dtype=np.float64), np.asarray(ref, dtype=np.float64)
    if ours.shape != ref.shape or ours.ndim != 1 or len(ref) < round(7.4 * SR):
        raise Refused("bass comparison requires complete matching phrases")
    if any(not np.isfinite(x).all() or np.max(np.abs(x)) <= 1e-5 for x in (ours, ref)):
        raise Refused("bass comparison audio is silent or non-finite")
    am = lead.am
    envelopes = [am.rms_envelope(x, ms=reference.ENVELOPE_WINDOW_MS, sr=SR) for x in (ours, ref)]
    values = {name: [] for name in ("Pitch", "Harmonic shape", "Envelope attack",
                                    "Envelope release", "Gain")}
    rows = []
    for event in reference.EVENTS:
        on, off = event["on_s"], event["on_s"] + event["gate_s"]
        start, end = round((on + .3) * SR), round((on + .55) * SR)
        clips = [x[start:end] for x in (ours, ref)]
        hz = lead.vf.note_hz(event["note"])
        pitches = [am.refine_f0(x, hz, SR) for x in clips]
        if not all(p.ok for p in pitches):
            raise Refused("bass pitch estimator refused")
        pitch_error = lead._cents_error(pitches[0].value, pitches[1].value)
        values["Pitch"].append((pitch_error, 0.))
        spectra = [am.harmonic_signature(x, SR, f0=p.value, kmax=12) for x, p in zip(clips, pitches)]
        partials = {f"h{k}": lead._harmonic_error(*spectra, k) for k in range(2, 13)}
        errors = [abs(error) for error, _ in partials.values() if error is not None]
        if len(errors) < 2:
            raise Refused("bass has fewer than two comparable partials")
        values["Harmonic shape"].append((max(errors), 0.))
        levels = [20 * math.log10(max(am.rms(x), 1e-15)) for x in clips]
        values["Gain"].append(tuple(levels))
        timing = [reference.ref.envelope_timing(env, SR, on, off) for env in envelopes]
        if not all(t.get("valid") and t.get("release_complete_40db") for t in timing):
            raise Refused("bass release incomplete or unmeasurable")
        values["Envelope release"].append(tuple(t["release_t20_ms"] for t in timing))
        # Qualified 10-90% attack on BOTH sides by the same waveform-domain
        # fit, each measured with its own steady-state template.
        attacks = [reference.attack_fit(x, SR, p.value, on, off)
                   for x, p in zip((ours, ref), pitches)]
        values["Envelope attack"].append(tuple(a["attack_10_90_ms"] for a in attacks))
        attack_domain = {side: attack_fit_qualified(a) for side, a in zip(("model", "reference"), attacks)}
        rows.append({**event, "pitch_error_cents": pitch_error,
                     "harmonics_db": dict(zip(("model", "reference"), spectra)),
                     "harmonic_error_db_model_minus_reference": {k: v[0] for k, v in partials.items()},
                     "harmonic_comparison": {k: v[1] for k, v in partials.items()},
                     "rms_dbfs": dict(zip(("model", "reference"), levels)),
                     "release_t20_ms": dict(zip(("model", "reference"), values["Envelope release"][-1])),
                     "attack_10_90_ms": dict(zip(("model", "reference"), values["Envelope attack"][-1])),
                     "attack_fit": {side: {k: a[k] for k in ("t0_ms", "ramp_ms", "shape_p",
                                                            "explained_ratio")}
                                    for side, a in zip(("model", "reference"), attacks)},
                     "attack_out_of_domain": attack_domain,
                     "attack_state": ("unqualified" if any(attack_domain.values()) else
                                      "pass" if abs(attacks[0]["attack_10_90_ms"]
                                                    - attacks[1]["attack_10_90_ms"]) <= 5.0
                                      else "fail")})
    properties = {}
    for name, pairs in values.items():
        ours_value, reference_value = max(pairs, key=lambda p: abs(p[0] - p[1]))
        tolerance, basis = lead.TOLERANCES[name]
        # units follow the measurement, not the majority branch: attack and
        # release are 10-90% spans and T20 in MILLISECONDS (the published
        # record once carried "dB" here while its basis and values said ms)
        units = ("cents" if name == "Pitch"
                 else "ms" if name in ("Envelope attack", "Envelope release")
                 else "dB")
        if name == "Envelope release":
            tolerance, basis = 20., "ms; fixed bass screening limit, above known-signal release error (<10 ms)"
        if name == "Envelope attack":
            tolerance, basis = 5.0, ("ms; the M5A screening limit for the same 10-90% "
                                     "measurement; the waveform-fit estimator's known-signal "
                                     "error is under 1 ms (qualification in the manifest)")
        properties[name] = lead._metric(name, ours_value, reference_value, units, tolerance, basis)
    unqualified = [f"MIDI {r['note']} @{r['on_s']:g}s {side}: {why}" for r in rows
                   for side, why in r["attack_out_of_domain"].items() if why]
    if attack_domain_rule and unqualified:
        raw = properties["Envelope attack"]
        properties["Envelope attack"] = {
            **invalid("attack fit outside the estimator's qualified domain: "
                      + "; ".join(unqualified)),
            "unqualified_value": raw["value"], "unqualified_reference": raw["reference"],
            "unqualified_error": raw["error"], "tolerance": raw["tolerance"],
            "tolerance_basis": raw["tolerance_basis"]}
    properties["Filter envelope"] = invalid("output-difference control does not identify cutoff trajectory; mapping provisional")
    clips = [100 * np.count_nonzero(np.abs(x) >= 32767 / 32768) / len(x) for x in (ours, ref)]
    properties["Clipping"] = lead._metric("Clipping", *clips, "%", .01, "% samples at output rail")
    return {"properties": properties, "events": rows}


def required_metrics(measured):
    props = measured["properties"]
    distance = max(abs(props[n]["error"]) / props[n]["tolerance"] for n in ("Pitch", "Harmonic shape"))
    parts = ("Envelope attack", "Envelope release")
    missing = [n for n in parts if not props[n].get("valid")]
    if missing:
        # a required component without a verdict has NO distance, not the
        # distance of whatever components remain (DR 0015)
        envelope_metric = {**invalid("unqualified component: " + ", ".join(
                               f"{n} ({props[n]['why']})" for n in missing), "normalized maximum"),
                           "tolerance": 1.}
    else:
        envelope = max(abs(props[n]["error"]) / props[n]["tolerance"] for n in parts)
        envelope_metric = lead._metric("envelope", envelope, 0., "normalized maximum", 1.,
            "maximum of envelope attack / 5 ms (waveform-fit, qualified) and "
            "envelope release / 20 ms (40 ms RMS window); no averaging")
    return {"Fundamental/harmonics": lead._metric("Fundamental/harmonics", distance, 0., "normalized maximum", 1.,
                "maximum of pitch / 1 cent and harmonic error / 1 dB; no averaging"),
            "envelope": envelope_metric,
            "bass level": props["Gain"]}


def load_model_cache(record, patch, engine):
    """Reanalyse a hash-bound model render only while its DSP tree is unchanged."""
    import subprocess
    pcm_path = ROOT / record["audio"]
    config = record["diagnostics"]["configuration"]
    if json.dumps(config["patch"], sort_keys=True) != json.dumps(patch, sort_keys=True) or config["engine"] != engine:
        raise Refused("cached model patch or engine differs")
    if config.get("inject") or record["engine"] != "fixed-model":
        raise Refused("only clean model audio may be reused")
    if sha(pcm_path) != record["diagnostics"]["model_audio_sha256"]:
        raise Refused("cached model audio hash mismatch")
    if record["provenance"]["inputs"]["frozen:M1A:manifest"] != "sha256:" + sha(MANIFEST):
        raise Refused("cached model reference differs")
    # Older records have shortened model hashes. Check every recorded DSP input
    # as well as the committed tree; retain their original dirty-tree identity.
    for rel, digest in record["provenance"]["inputs"].items():
        if rel.startswith(("model/", "audition/")):
            if digest.removeprefix("sha256:") not in (sha(ROOT / rel), sha(ROOT / rel)[:16]):
                raise Refused(f"cached model input differs: {rel}")
    changed = subprocess.run(["git", "diff", "--exit-code", record["source_commit"], "--", "model", "audition"],
                             cwd=ROOT, capture_output=True, text=True)
    if changed.returncode != 0:
        raise Refused("cached model DSP sources differ or cannot be checked")
    sr, pcm = wavfile.read(pcm_path)
    if sr != SR or pcm.dtype != np.int16 or pcm.ndim != 1:
        raise Refused("cached model audio format differs")
    return pcm


def run(case, inject="", keep_audio=True, cached_record=None):
    import run_case
    source_commit = run_case.source_commit()
    source_tree = run_case.worktree_state()
    if inject == "REF_MISSING":
        load_reference(MANIFEST.parent / "missing-manifest.json")
    if inject not in ("", "MONO_PITCH_UP_25_CENTS"):
        raise Refused(f"unsupported M1A mutation: {inject}")
    manifest, reference_pcm = load_reference()
    qualification = reference.qualify_envelope_basis()
    patch = patch_for_reference(manifest)
    engine = lead.engine_configuration("selected")
    sequence = [(e["on_s"], e["note"] + (.25 if inject else 0), e["gate_s"],
                 {**patch, "gate": e["gate_s"]}) for e in reference.EVENTS]
    if cached_record is not None:
        if inject:
            raise Refused("model mutations cannot reuse cached audio")
        pcm = load_model_cache(cached_record, patch, engine)
    else:
        voice = lead._voice_for_engine(engine)
        pcm = lead.vf.render_mono_fx(sequence, reference.SECONDS, voice)[:len(reference_pcm)]
    measured = compare_audio(pcm.astype(np.float64) / 32768., reference_pcm)
    output = (ROOT / f"build/scorecard/M1A-{inject}-model.wav" if inject else
              MANIFEST.parent / "m1a-model.wav")
    artifacts = {}
    if keep_audio:
        output.parent.mkdir(parents=True, exist_ok=True)
        wavfile.write(output, SR, pcm.astype('<i2'))
        artifacts["ours"] = str(output.relative_to(ROOT))
    config = {"engine": engine, "patch_id": SELECTED_PATCH, "patch": patch, "inject": inject or None,
              "mapping_qualification": {
                  "output_volume": f"{PATCH_VERSIONS[SELECTED_PATCH]['output_volume_db']:+g} dB fixed "
                                   "patch volume, applied in the patch before rendering; no "
                                   "post-render normalization. " + PATCH_VERSIONS[SELECTED_PATCH]["selected_by"],
                  "octave_mix": "from isolated open-filter reference RMS ratio; no gain normalization after rendering",
                  "release": "median reference T20 encoded using existing host exponential law",
                  "rest_cutoff": "command set to reference ring frequency; low-resonance equivalence unqualified",
                  "amp_attack_decay_sustain": "fixed provisional 10 ms / 250 ms / 0.75; not inferred from reference knobs",
                  "filter_envelope": "fixed provisional 4 ms / 50 ms / zero sustain, 2x cutoff peak",
                  "resonance": "fixed low setting 0.05; not calibrated from plugin normalized value"}}
    inputs = run_case.model_input_hashes({
        "frozen:M1A:manifest": "sha256:" + sha(MANIFEST),
        "frozen:M1A:audio": "sha256:" + manifest["renders"][0]["sha256"],
        "tools/mono_m1a_score.py": "sha256:" + sha(Path(__file__)),
        "model/filter_rate_chain.py": "sha256:" + sha(ROOT / "model/filter_rate_chain.py"),
        "tools/measure_mono_m1a_reference.py": "sha256:" + sha(ROOT / "tools/measure_mono_m1a_reference.py")})
    for rel in ("audition/dsp.py", "model/fixed.py"):
        inputs[rel] = "sha256:" + sha(ROOT / rel)
    provenance = run_case.provenance(inputs, artifacts, config)
    provenance["worktree"] = source_tree
    return {"engine": "fixed-model", "case_id": "M1A", "subject": case["subject"],
            "source_commit": source_commit, "analysis_run": run_case.analysis_run(),
            "analysis_version": "m1a-envelope-score-v2",
            "reference_profile": "frozen Mini V3 software reference (screening policy: "
                                 "docs/scorecard/mono-m5a-policy.md); Model D cross-check unavailable",
            "render_run": "7.5 s complete MIDI 36/43/36 phrase; selected oscillator/filter 2x; provisional patch",
            "audio": artifacts.get("ours", ""), "metrics": required_metrics(measured),
            "diagnostics": {**measured, "qualification": qualification, "configuration": config,
                            "render_source_commit": (cached_record["source_commit"] if cached_record else source_commit),
                            "render_worktree": (cached_record["provenance"]["worktree"] if cached_record else source_tree),
                            "audio_reused": cached_record is not None,
                            "model_audio_sha256": sha(output) if keep_audio else None,
                            "missing_evidence": {
                                "Model D cross-check": "renders exact digital silence under the "
                                "qualified host (dawdreamer); tracked upstream (#123). The case's "
                                "required_measurements do not include it; the screening policy "
                                "accepts the frozen Mini V3 software reference alone."},
                            "filter_envelope_scope": "the model's filter-envelope mapping stays "
                                "provisional and the reference cutoff trajectory is not measurable "
                                "from frozen audio; recorded as an unqualified property, not a metric",
                            "wrong_then_right": {"apparatus_corrections": 5,
                                "session_estimator_iterations": 6,
                                "latest": "six ground-truth-caught defects while building the "
                                "waveform-fit attack estimator (objective, template phase twice, "
                                "FFT offset, fold smear, shape refinement); all listed in the "
                                "estimator commit"}},
            "provenance": provenance,
            "note": "envelope attack qualified by waveform-domain fit with closed-form "
                    "ground truth; envelope metric = worst of attack/release normalized errors"}

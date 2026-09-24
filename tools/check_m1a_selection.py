#!/usr/bin/env python3
"""Assert that the promoted M1A board record IS the accepted -4 dB candidate.

Selection is only meaningful if the record the board reads was produced by the
named patch and reproduces the evidence that justified selecting it. So this
checks, against committed files only (no render):

1. identity   -- the record names `mono_m1a_score.SELECTED_PATCH`, and its
                 patch equals that identity's patch and the candidate's patch;
2. audio      -- the record's model WAV hashes to the candidate WAV's hash;
3. vector     -- the record's full property vector and required metrics equal
                 the candidate's exactly; per-event raw diagnostics agree to
                 1e-6 dB (FFT last-bit differences across hosts, see below);
4. policy     -- M1A's declared preservation policy
                 (tools/measure_m1a_volume_mapping.py) holds against the
                 pre-selection baseline: no property pass lost, no per-note
                 harmonic pass lost, every per-note gain inside tolerance.

Exit 0 match, 1 mismatch, 2 refused (an input is missing or unreadable).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import mono_m1a_score as bass                                        # noqa: E402
import measure_m1a_volume_mapping as volume                           # noqa: E402

RECORD = ROOT / "docs/scorecard/results/M1A.json"
CANDIDATE = volume.OUT / "volume-minus4db.json"
BASELINE = volume.OUT / "baseline.json"


def _norm(x):
    """JSON-normalise (tuples -> lists) so a patch compares by value."""
    return json.loads(json.dumps(x))


EVENT_ATOL = 1e-6


def event_deviation(a, b):
    """Largest absolute numeric difference between two diagnostics trees, or
    None when their structure or any non-numeric value differs."""
    if isinstance(a, bool) or isinstance(b, bool) or a is None or b is None:
        return 0. if a == b else None
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b)
    if isinstance(a, dict) and isinstance(b, dict):
        if set(a) != set(b):
            return None
        parts = [event_deviation(a[k], b[k]) for k in a]
    elif isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        if len(a) != len(b):
            return None
        parts = [event_deviation(x, y) for x, y in zip(a, b)]
    else:
        return 0. if a == b else None
    return None if None in parts else max(parts, default=0.)


V2_EVENT_FIELDS = ("attack_out_of_domain", "attack_state")


def candidate_event_deviation(record, candidate):
    """event_deviation, ignoring the fields the v2 scorer adds per event."""
    return event_deviation([{k: v for k, v in e.items() if k not in V2_EVENT_FIELDS}
                            for e in record["diagnostics"]["events"]],
                           candidate["measurements"]["events"])


def _passes(measured):
    return {name for name, p in measured["properties"].items()
            if p.get("valid") and abs(p["error"]) <= p["tolerance"]}


def check(record: dict, candidate: dict, baseline: dict, manifest: dict) -> list[str]:
    """Every reason the record is not the selected candidate; [] when it is."""
    problems = []
    config = record["diagnostics"]["configuration"]
    selected = _norm(bass.patch_for_reference(manifest, bass.SELECTED_PATCH))
    if config.get("patch_id") != bass.SELECTED_PATCH:
        problems.append(f"record patch_id {config.get('patch_id')!r} != {bass.SELECTED_PATCH!r}")
    if _norm(config["patch"]) != selected:
        problems.append("record patch differs from the selected identity")
    if _norm(candidate["patch"]) != selected:
        problems.append("candidate evidence patch differs from the selected identity")
    if config.get("inject"):
        problems.append("record is an injected control, not evidence")
    audio = ROOT / record["audio"]
    if not audio.exists() or bass.sha(audio) != record["diagnostics"]["model_audio_sha256"]:
        problems.append("record audio missing or does not hash to its own record")
    if record["diagnostics"]["model_audio_sha256"] != candidate["sha256"]:
        problems.append(f"record audio {record['diagnostics']['model_audio_sha256'][:12]} != "
                        f"candidate {candidate['sha256'][:12]}")
    measured = candidate["measurements"]
    # The candidate evidence was scored by m1a-envelope-score-v1, before the
    # two-sided attack qualification rule. Every other property must match
    # exactly; the attack must carry the SAME raw reading, and be graded only
    # if both sides' fits are inside the estimator's domain.
    rp, cp = record["diagnostics"]["properties"], measured["properties"]
    differs = [k for k in cp if k != "Envelope attack" and _norm(rp.get(k)) != _norm(cp[k])]
    if differs or set(rp) != set(cp):
        problems.append(f"record properties differ from the candidate's: {', '.join(differs)}")
    ra, ca = rp["Envelope attack"], cp["Envelope attack"]
    raw = ((ra["value"], ra["reference"], ra["error"]) if ra.get("valid") else
           (ra.get("unqualified_value"), ra.get("unqualified_reference"), ra.get("unqualified_error")))
    if raw != (ca["value"], ca["reference"], ca["error"]):
        problems.append("record attack reading differs from the candidate's")
    out_of_domain = any(bass.attack_fit_qualified(e["attack_fit"][side])
                        for e in record["diagnostics"]["events"] for side in ("model", "reference"))
    if bool(ra.get("valid")) == out_of_domain:
        problems.append("record attack graded against the two-sided qualification rule")
    # Per-event diagnostics are raw floats from an FFT. The same audio analysed
    # on two hosts differs in the last bits (observed: 7.5e-11 dB on a -99 dB
    # floor, on the REFERENCE side as well, so it is the library, not the
    # render). Equal structure and non-numeric fields, numbers within 1e-6 dB,
    # which is four orders below the published 5-decimal property vector.
    worst = candidate_event_deviation(record, candidate)
    if worst is None or worst > EVENT_ATOL:
        problems.append(f"record events differ from the candidate's (max deviation {worst})")
    for name in ("Fundamental/harmonics", "bass level"):
        if _norm(record["metrics"][name]) != _norm(candidate["metrics"][name]):
            problems.append(f"record required metric {name} differs from the candidate's")
    if record["metrics"]["envelope"].get("valid") != ra.get("valid"):
        problems.append("record envelope metric validity disagrees with its attack property")
    # the declared preservation policy, against the pre-selection baseline
    base = baseline["measurements"]
    lost = sorted(_passes(base) - _passes(measured))
    if lost:
        problems.append(f"preservation: property pass lost: {', '.join(lost)}")
    lost_partials = sorted(volume.passed_partials(base) - volume.passed_partials(measured))
    if lost_partials:
        problems.append(f"preservation: per-note harmonic pass lost: {lost_partials}")
    tol = measured["properties"]["Gain"]["tolerance"]
    gains = [e["rms_dbfs"]["model"] - e["rms_dbfs"]["reference"] for e in measured["events"]]
    if not all(abs(g) <= tol for g in gains):
        problems.append(f"preservation: per-note gain outside {tol} dB: {gains}")
    return problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--record", type=Path, default=RECORD)
    a = ap.parse_args(argv)
    try:
        record = json.loads(a.record.read_text())
        candidate = json.loads(CANDIDATE.read_text())
        baseline = json.loads(BASELINE.read_text())
        manifest, _ = bass.load_reference()
    except (OSError, ValueError, bass.Refused) as exc:
        print(f"REFUSED: {exc}")
        return 2
    problems = check(record, candidate, baseline, manifest)
    for p in problems:
        print("MISMATCH:", p)
    if problems:
        return 1
    props = record["diagnostics"]["properties"]
    print(f"MATCH: {bass.SELECTED_PATCH}; audio {candidate['sha256']}; per-event max "
          f"deviation {candidate_event_deviation(record, candidate):.3g}")
    for name, p in props.items():
        state = ("unqualified" if not p.get("valid") else
                 "pass" if abs(p["error"]) <= p["tolerance"] else "fail")
        value = (f"{p['error']:+.5f} {p['units']}" if p.get("valid") else
                 f"[unqualified reading {p['unqualified_error']:+.5f} {p['units']}] {p['why']}"
                 if "unqualified_error" in p else p.get("why", ""))
        print(f"  {name:<18} {value:<24} {state}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

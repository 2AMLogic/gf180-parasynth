import json
import numpy as np
import pytest
from scipy.io import wavfile
import mono_m1a_score as bass
import run_case


def signal(cents=0):
    t = np.arange(round(bass.reference.SECONDS * bass.SR)) / bass.SR
    audio = np.zeros_like(t)
    for event in bass.reference.EVENTS:
        dt = t - event["on_s"]
        env = np.clip(dt / .005, 0, 1)   # 10-90 = 4 ms, inside the v3 domain
        off = dt >= event["gate_s"]
        env[off] = np.exp(-(dt[off] - event["gate_s"]) / .05)
        hz = 440 * 2 ** ((event["note"] - 69 + cents / 100) / 12)
        audio += .1 * env * (np.sin(2 * np.pi * hz * t)
                             + .2 * np.sin(4 * np.pi * hz * t)
                             + .1 * np.sin(6 * np.pi * hz * t))
    return audio


def test_known_bass_and_pitch_mutation():
    clean = bass.compare_audio(signal(), signal())
    changed = bass.compare_audio(signal(25), signal())
    assert abs(clean["properties"]["Pitch"]["error"]) < .01
    assert changed["properties"]["Pitch"]["error"] == pytest.approx(25, abs=.5)
    assert clean["properties"]["Envelope release"]["valid"]
    # identical audio on both sides: the qualified attack must read the same
    # 4 ms 10-90 span it was built with, within the estimator's own bound
    assert clean["properties"]["Envelope attack"]["valid"]
    assert abs(clean["properties"]["Envelope attack"]["error"]) < 1.0
    assert abs(changed["properties"]["Envelope attack"]["error"]) < 1.0
    # the attack fit measures 10-90% spans in MILLISECONDS: its values are
    # attack_10_90_ms and its tolerance basis says ms. The published record
    # once labelled this diagnostic "dB" -- arithmetic right, metadata wrong.
    assert clean["properties"]["Envelope attack"]["units"] == "ms", \
        clean["properties"]["Envelope attack"]
    assert clean["properties"]["Envelope release"]["units"] == "ms"
    assert clean["properties"]["Harmonic shape"]["units"] == "dB"
    assert clean["properties"]["Gain"]["units"] == "dB"
    for event in clean["events"]:
        assert event["harmonics_db"]["model"]["h2"] == pytest.approx(20 * np.log10(.2), abs=.05)
        assert event["harmonics_db"]["model"]["h3"] == pytest.approx(-20., abs=.05)
        assert event["release_t20_ms"]["model"] == pytest.approx(50 * np.log(10), abs=10)
        assert event["attack_10_90_ms"]["model"] == pytest.approx(4., abs=1.0)
        assert event["attack_10_90_ms"]["reference"] == pytest.approx(4., abs=1.0)


def test_missing_or_corrupt_reference_refuses(tmp_path):
    manifest = json.loads(bass.MANIFEST.read_text())
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    with pytest.raises(bass.Refused, match="reference"):
        bass.load_reference(path)
    wavfile.write(tmp_path / manifest["renders"][0]["file"], bass.SR,
                  np.zeros(100, dtype=np.float32))
    with pytest.raises(bass.Refused, match="reference"):
        bass.load_reference(path)


def test_silent_and_truncated_candidates_refuse():
    clean = signal()
    with pytest.raises(bass.Refused):
        bass.compare_audio(np.zeros_like(clean), clean)
    with pytest.raises(bass.Refused):
        bass.compare_audio(clean[:100], clean)


def test_runner_executes_bass_with_qualified_envelope():
    assert run_case.plan_for("M1A") == "mono-bass"
    measured = bass.compare_audio(signal(), signal())
    metrics = bass.required_metrics(measured)
    case = next(c for c in run_case.load_cases() if c["case_id"] == "M1A")
    required = {x.strip() for x in case["required_measurements"].split(";")}
    assert required <= metrics.keys()
    assert all(m["valid"] for m in metrics.values()), \
        f"an invalid metric makes the case no-verdict: {[m for m in metrics if not m['valid']]}"
    assert metrics["envelope"]["valid"]
    assert metrics["Fundamental/harmonics"]["valid"]
    assert metrics["bass level"]["valid"]


def test_render_provenance_uses_existing_dsp_sources(monkeypatch):
    # Exercise the complete reporting path without an expensive synth render.
    # This caught the mistaken model/dsp.py path (the module is in audition/).
    pcm = np.asarray(signal() * 32768, dtype=np.int16)
    monkeypatch.setattr(bass.lead.vf, "render_mono_fx", lambda *args: pcm)
    case = next(c for c in run_case.load_cases() if c["case_id"] == "M1A")
    record = bass.run(case, keep_audio=False)
    assert record["provenance"]["inputs"]["audition/dsp.py"] == "sha256:" + bass.sha(bass.ROOT / "audition/dsp.py")
    assert record["provenance"]["inputs"]["model/fixed.py"] == "sha256:" + bass.sha(bass.ROOT / "model/fixed.py")


def test_changed_cached_audio_refuses(tmp_path):
    path = tmp_path / "changed.wav"
    path.write_bytes(b"corrupt")
    record = {"audio": str(path), "engine": "fixed-model",
              "diagnostics": {"configuration": {"patch": {}, "engine": {}, "inject": None},
                              "model_audio_sha256": "original"}}
    with pytest.raises(bass.Refused, match="hash mismatch"):
        bass.load_model_cache(record, {}, {})


# --- attack qualification on BOTH sides ---------------------------------------
# A reading outside attack_fit_v3's known-answer domain (a region of reported
# 10-90 value x explained ratio, off the ramp search boundary) is UNQUALIFIED.

def _patched_fits(monkeypatch, side, **fit):
    real = bass.attack_fit_v3.attack_fit_v3
    calls = {"n": 0}

    def fake(*args, **kwargs):
        out = dict(real(*args, **kwargs))
        which = ("model", "reference")[calls["n"] % 2]     # compare_audio calls model first
        calls["n"] += 1
        if which == side:
            out.update(fit)
        return out
    monkeypatch.setattr(bass.attack_fit_v3, "attack_fit_v3", fake)


@pytest.mark.parametrize("side", ["model", "reference"])
@pytest.mark.parametrize("fit", [dict(shape_p=4.0, ramp_ms=0.6666666666666666,
                                      attack_10_90_ms=0.27444, search_boundary="minimum"),
                                 dict(shape_p=1.0, ramp_ms=10.0, attack_10_90_ms=8.0),
                                 dict(explained_ratio=0.72)])
def test_out_of_domain_attack_on_either_side_is_unqualified(monkeypatch, side, fit):
    _patched_fits(monkeypatch, side, **fit)
    measured = bass.compare_audio(signal(), signal())
    attack = measured["properties"]["Envelope attack"]
    assert attack["valid"] is False and attack["state"] == "no verdict"
    assert side in attack["why"]
    assert all(e["attack_state"] == "unqualified" for e in measured["events"])
    metrics = bass.required_metrics(measured)
    assert metrics["envelope"]["valid"] is False           # no partial maximum
    assert metrics["Fundamental/harmonics"]["valid"] and metrics["bass level"]["valid"]


def test_control_pre_rule_scorer_would_have_graded_it(monkeypatch):
    """The same out-of-domain reference fit, with the rule off, is GRADED --
    which is what the published board did with M1A's p=3 / p=4 reference fits."""
    _patched_fits(monkeypatch, "reference", shape_p=4.0, ramp_ms=0.6666666666666666,
                  attack_10_90_ms=0.27444, search_boundary="minimum")
    old = bass.compare_audio(signal(), signal(), attack_domain_rule=False)
    assert old["properties"]["Envelope attack"]["valid"] is True
    assert bass.required_metrics(old)["envelope"]["valid"] is True


def test_in_domain_fits_still_grade():
    measured = bass.compare_audio(signal(), signal())
    assert measured["properties"]["Envelope attack"]["valid"]
    assert all(e["attack_state"] == "pass" for e in measured["events"])
    ok = {"attack_10_90_ms": 4.0, "explained_ratio": 0.99, "search_boundary": None}
    assert bass.attack_fit_qualified(ok) is None
    assert bass.attack_fit_qualified({**ok, "attack_10_90_ms": 8.0})

import hashlib
import json
from pathlib import Path
import numpy as np
import pytest
from scipy.io import wavfile
import check_m1a_selection as selection
import mono_m1a_score as bass
import run_case
import scorecard


def signal(cents=0, *, low_note_ramp=240, low_note_power=1):
    t = np.arange(round(bass.reference.SECONDS * bass.SR)) / bass.SR
    audio = np.zeros_like(t)
    for event in bass.reference.EVENTS:
        dt = t - event["on_s"]
        # The middle note keeps its 4 ms 10-90 attack as a pass control.
        span, power = (low_note_ramp, low_note_power) if event["note"] == 36 else (240, 1)
        env = np.clip(dt * bass.SR / span, 0, 1) ** power
        off = dt >= event["gate_s"]
        env[off] = np.exp(-(dt[off] - event["gate_s"]) / .05)
        hz = 440 * 2 ** ((event["note"] - 69 + cents / 100) / 12)
        phase = 2 * np.pi * hz * t
        audio += .1 * env * (np.sin(phase) + .2 * np.sin(2 * phase) + .1 * np.sin(3 * phase))
    return audio


@pytest.mark.parametrize(("span", "expected"), [(347, "fail"), (348, "fail"), (346, "pass")])
def test_qualified_attack_grade_survives_score_serialization(span, expected, capsys):
    measured = bass.compare_audio(signal(low_note_ramp=span),
                                  signal(low_note_ramp=75, low_note_power=3))
    metrics = bass.required_metrics(measured)
    measured, metrics = json.loads(json.dumps([measured, metrics]))
    attack = measured["properties"]["Envelope attack"]
    release = measured["properties"]["Envelope release"]
    assert attack["valid"] and release["valid"] and metrics["envelope"]["valid"]
    assert abs(release["error"]) <= release["tolerance"]
    assert [e["attack_state"] for e in measured["events"]] == [expected, "pass", expected]
    timings = [e["attack_10_90_ms"] for e in measured["events"]]
    worst = max((t["model"] - t["reference"] for t in timings), key=abs)
    if span == 347:  # The five-decimal store used to turn this miss into a pass.
        assert worst > 5 and round(worst, 5) == 5
    passed = expected == "pass"
    assert ("Envelope attack" in selection._passes(measured)) == passed
    distance = scorecard.metric_distance(metrics["envelope"])
    assert (distance <= 1) == passed
    assert attack["error"] == worst
    assert distance == max(abs(worst) / 5, abs(release["error"]) / release["tolerance"])
    selection.print_properties(measured)
    line = next(s for s in capsys.readouterr().out.splitlines() if s.strip().startswith("Envelope attack "))
    assert line.split()[-1] == expected


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


def test_exact_negative_five_ms_attack_still_passes(monkeypatch):
    _patched_fits(monkeypatch, "model", attack_10_90_ms=.75)
    _patched_fits(monkeypatch, "reference", attack_10_90_ms=5.75)
    measured = bass.compare_audio(signal(), signal())
    assert [e["attack_state"] for e in measured["events"]] == ["pass"] * 3
    assert measured["properties"]["Envelope attack"]["error"] == -5.0
    assert "Envelope attack" in selection._passes(measured)
    assert scorecard.metric_distance(bass.required_metrics(measured)["envelope"]) == 1.0


# --- the qualification the v3 record embeds -----------------------------------
# The attack is graded inside attack_fit_v3's known-answer domain, yet run()
# also called reference.qualify_envelope_basis(), whose `attack` entry runs
# the RETIRED v2 fit 56 times and stamps its "<1 ms known-signal error" into a
# record whose diagnostics.wrong_then_right.attack_v3 refutes that claim (#1423).

#: Everything but `attack` in reference.qualify_envelope_basis(), as the frozen
#: module published it in docs/scorecard/results/M1A.json at 283b3a43. The
#: scorer carries a port of that release half (its digest-pinned module cannot
#: supply it without the v2 fit) and is held to these rows exactly.
FROZEN_RELEASE = {
    "known_release_ms": 230.25850929940458,
    "spectral_window_s": .25,
    "lead_window": {"window_ms": 5.0,
                    "release_ms": [201.8125, 229.02083333333334, 207.16666666666666],
                    "max_error_ms": 28.44600929940458},
    "bass_window": {"window_ms": 40.0,
                    "release_ms": [233.5625, 231.1875, 232.25],
                    "max_error_ms": 3.3039907005954205},
    "scope": "40 ms RMS window qualified for release ONLY; attack is measured by "
             "the waveform-domain fit, not by any RMS window",
    "initial_failures": "100 ms spectral window refused below 12 periods; 5 ms envelope "
                        "window failed known release; early h4 window could not resolve "
                        "the short filter transient",
}


def _forbid_retired_v2(monkeypatch):
    def retired(*args, **kwargs):
        raise AssertionError("the v3 scorer ran the retired v2 attack qualification")
    monkeypatch.setattr(bass.reference, "qualify_attack_basis", retired)
    monkeypatch.setattr(bass.reference, "attack_fit", retired)


def _assert_v3_attack_summary(attack):
    report = json.loads((bass.QUALIFICATION / "qualification.json").read_text())
    suite = json.loads((bass.QUALIFICATION / "suite-v3.json").read_text())
    assert attack["estimator"] == "attack_fit_v3"
    assert attack["schema"] == report["schema"]
    assert attack["bound_ms"] == report["bound_ms"]
    assert attack["cases"] == len(suite["rows"]) == 1898
    assert attack["search_boundary_hits"] == report["v3"]["search_boundary_hits"]
    assert attack["domain"] == bass.qualified_attack_domain() == report["v3"]["domain"]
    assert "observations" not in attack
    assert attack["qualification_path"] == ("docs/scorecard/mono-m1a-miniv3/"
                                            "attack-qualification/qualification.json")


def test_qualification_is_the_release_basis_and_the_v3_attack_domain(monkeypatch):
    _forbid_retired_v2(monkeypatch)
    qualification = bass.qualify_envelope_basis()
    for key, frozen in FROZEN_RELEASE.items():
        assert qualification[key] == frozen, key
    # the 40 ms window measures the known release; the 5 ms lead window cannot
    assert qualification["bass_window"]["max_error_ms"] < 10
    assert qualification["lead_window"]["max_error_ms"] > 10
    _assert_v3_attack_summary(qualification["attack"])


def test_run_records_the_v3_qualification_without_running_the_retired_v2_fit(monkeypatch):
    _forbid_retired_v2(monkeypatch)
    pcm = np.asarray(signal() * 32768, dtype=np.int16)
    monkeypatch.setattr(bass.lead.vf, "render_mono_fx", lambda *args: pcm)
    case = next(c for c in run_case.load_cases() if c["case_id"] == "M1A")
    record = bass.run(case, keep_audio=False)
    assert record["analysis_version"] == "m1a-envelope-score-v3"
    qualification = record["diagnostics"]["qualification"]
    assert qualification == bass.qualify_envelope_basis()
    _assert_v3_attack_summary(qualification["attack"])


@pytest.mark.parametrize("lying_window",
                         [bass.reference.ENVELOPE_WINDOW_MS, 5.0], ids=["bass", "lead"])
def test_control_release_qualification_refuses_a_window_that_lies(monkeypatch, lying_window):
    """Each half of the gate, shown able to go red. A 40 ms window that reads
    what the 5 ms one does cannot measure release; a 5 ms window that reads
    what the 40 ms one does is a control that no longer fails. The other
    window is left honest, so the refusal can only come from the lying one."""
    real = bass.lead.am.rms_envelope
    other = 5.0 if lying_window == bass.reference.ENVELOPE_WINDOW_MS else bass.reference.ENVELOPE_WINDOW_MS
    asked = []

    def lying(audio, ms, sr):
        asked.append(ms)
        return real(audio, ms=other if ms == lying_window else ms, sr=sr)

    monkeypatch.setattr(bass.lead.am, "rms_envelope", lying)
    with pytest.raises(bass.Refused, match="qualification or wrong-window control failed"):
        bass.qualify_envelope_basis()
    assert asked == [5.0, bass.reference.ENVELOPE_WINDOW_MS]


def test_qualification_refuses_a_stale_attack_suite(monkeypatch):
    real = bass.sha
    monkeypatch.setattr(bass, "sha", lambda p: "0" * 64 if str(p).endswith("attack_fit_v3.py") else real(p))
    with pytest.raises(bass.Refused, match="stale"):
        bass.qualify_envelope_basis()


# --- qualification.json: ONE buffer for the gate, the record and its digest --
# run() opened the report to gate the attack (twice: the release basis and
# compare_audio each re-read it) and once more to hash it for provenance, so
# the published digest named whatever sat at the path LAST, not the bytes whose
# v3.domain gated the metric (#1677; the manifest's shape in #1603).

QUALIFICATION_INPUT = "attack-qualification/qualification.json"


def _replace_on_first_open(monkeypatch, target, replacement, *, requested=None):
    """An open handle keeps A while the next reader of the same path gets B."""
    real_open = Path.open
    requested = target if requested is None else requested

    def open_then_replace(path, *args, **kwargs):
        if path != requested:
            return real_open(path, *args, **kwargs)
        handle = real_open(target, *args, **kwargs)
        if replacement.exists():
            replacement.replace(target)
        return handle

    monkeypatch.setattr(Path, "open", open_then_replace)


def _serve_in_place_of(monkeypatch, requested, source):
    """Every open of `requested` reads `source` instead; nothing is swapped."""
    real_open = Path.open
    monkeypatch.setattr(Path, "open",
                        lambda path, *args, **kwargs: real_open(source if path == requested else path,
                                                                 *args, **kwargs))


def _run_without_render(monkeypatch):
    """run() over the complete reporting path, minus the render and WAV write."""
    pcm = np.asarray(signal() * 32768, dtype=np.int16)
    monkeypatch.setattr(bass.lead.vf, "render_mono_fx", lambda *args: pcm)
    case = next(c for c in run_case.load_cases() if c["case_id"] == "M1A")
    return bass.run(case, keep_audio=False)


def test_scorecard_binds_parsed_qualification_after_replacement(tmp_path, monkeypatch):
    """qualification.json is replaced the moment run() has opened it. The
    published digest must be of the bytes that were PARSED: on the unfixed
    scorer it came from a later open and named the replacement, while the
    record's own qualification summary was built from the first read."""
    raw = (bass.QUALIFICATION / "qualification.json").read_bytes()
    original = json.loads(raw)
    target = tmp_path / "qualification.json"
    target.write_bytes(raw)
    # Still a valid report (same domain, same bound), so the unfixed scorer's
    # later reads do not refuse: the digest is the only thing that can differ.
    replaced = {**original, "v3": {**original["v3"],
                                   "search_boundary_hits": original["v3"]["search_boundary_hits"] + 1}}
    other = json.dumps(replaced).encode()
    replacement = tmp_path / "replacement.json"
    replacement.write_bytes(other)
    assert hashlib.sha256(other).hexdigest() != hashlib.sha256(raw).hexdigest()
    # Redirect only the report; the suite and its hashed sources stay real.
    _replace_on_first_open(monkeypatch, target, replacement,
                           requested=bass.QUALIFICATION / "qualification.json")

    record = _run_without_render(monkeypatch)
    # the swap fired: the path serves B now
    assert not replacement.exists()
    assert target.read_bytes() == other
    # the record was built from A ...
    attack = record["diagnostics"]["qualification"]["attack"]
    assert attack["search_boundary_hits"] == original["v3"]["search_boundary_hits"]
    # ... and names A's bytes, independently hashed here
    assert record["provenance"]["inputs"][QUALIFICATION_INPUT] == "sha256:" + hashlib.sha256(raw).hexdigest()


def test_qualification_accepts_utf8_bom_and_digests_the_bom_bytes(tmp_path, monkeypatch):
    """read_text() + json.loads(str) refused a UTF-8 BOM that json.loads(bytes)
    accepts; the digest is of the bytes as they sit on disk, BOM included."""
    raw = (bass.QUALIFICATION / "qualification.json").read_bytes()
    bom = b"\xef\xbb\xbf" + raw
    served = tmp_path / "qualification.json"
    served.write_bytes(bom)
    _serve_in_place_of(monkeypatch, bass.QUALIFICATION / "qualification.json", served)

    record = _run_without_render(monkeypatch)
    attack = record["diagnostics"]["qualification"]["attack"]
    assert attack["search_boundary_hits"] == json.loads(raw)["v3"]["search_boundary_hits"]
    assert record["provenance"]["inputs"][QUALIFICATION_INPUT] == "sha256:" + hashlib.sha256(bom).hexdigest()


def test_run_opens_the_qualification_report_once_as_bytes(monkeypatch):
    """One buffer serves the release basis, the attack gate and the provenance
    digest: none of them reopens the path, and the one open is a byte read."""
    report = bass.QUALIFICATION / "qualification.json"
    opened = []
    real_open = Path.open

    def counting(path, *args, **kwargs):
        if path == report:
            opened.append(kwargs.get("mode", args[0] if args else "r"))
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", counting)
    _run_without_render(monkeypatch)
    assert opened == ["rb"]

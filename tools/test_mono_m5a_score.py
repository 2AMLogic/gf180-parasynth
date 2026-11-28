import hashlib
import json
import pathlib

import numpy as np
import pytest

import mono_m5a_score as score


def test_corrupted_reference_refuses_before_measurement(tmp_path, monkeypatch):
    audio = tmp_path / "broken.wav"
    audio.write_bytes(b"not the frozen audio")
    manifest = tmp_path / "manifest.json"
    # The identity block is the suite gate's input, so the fixture carries a
    # genuine V3 identity: this test is about the HASH gate firing before any
    # measurement, not about the suite gate (#1050), which has its own tests.
    manifest.write_text(json.dumps({
        "case_id": "M5A",
        "identity": {"name": "Mini V3", "version": "3.12.0.3422",
                     "bundle_id": "com.Arturia.Mini-V3.vst3"},
        "audio": {
            "file": audio.name,
            "sha256": hashlib.sha256(b"different content").hexdigest(),
        }}))
    monkeypatch.setattr(score, "MANIFEST", manifest)
    with pytest.raises(score.Refused, match="hash mismatch"):
        score.measure()


def test_metric_keeps_signed_error_and_declared_units():
    result = score._metric("Pitch", -0.2, 0.1, "cents", 1.0, "frozen")
    assert result["error"] == -0.3
    assert result["tolerance"] == 1.0
    assert result["units"] == "cents"
    assert result["valid"] is True


def test_mono_pitch_mutation_is_explicit_and_bounded():
    assert score._model_note(72) == 72
    assert score._model_note(72, "MONO_PITCH_UP_25_CENTS") == 72.25
    with pytest.raises(score.Refused, match="unsupported Mono model injection"):
        score._model_note(72, "REF_F0_20PCT")


def test_m5b_missing_reference_injection_refuses_with_the_mutation_name():
    with pytest.raises(score.Refused, match="no-such-file.wav"):
        score.measure(case_id="M5B", inject="REF_MISSING")


def test_frozen_manifest_that_cannot_be_read_refuses_naming_it(tmp_path, monkeypatch):
    """#1939 row 3: `read_bytes()` on the frozen manifest was outside every
    try, so an absent one left `measure` as a bare FileNotFoundError -- a
    traceback at lead_drive_337 (exit 1), an execution-error record with a
    `traceback` field at run_case (origin/main @ e2d1bae7). The audio read
    two lines below already refused; the manifest read now does the same."""
    absent = tmp_path / "absent.json"
    monkeypatch.setattr(score, "MANIFEST", absent)
    with pytest.raises(score.Refused, match=r"frozen M5A manifest .*absent\.json"):
        score.measure()


def test_model_wav_destination_that_cannot_be_written_refuses_naming_it(tmp_path):
    """#1939 row 3: the WAV write at the end of `measure` was outside every
    try. A destination that cannot be created must be stated -- after the
    whole render, since that is where the write is -- not left as the bare
    FileExistsError it was on origin/main @ e2d1bae7."""
    blocker = tmp_path / "blocker"
    blocker.write_text("a regular file where a directory is needed")
    with pytest.raises(score.Refused, match=r"blocker[/\\]x\.wav"):
        score.measure(case_id="M5B", output_path=blocker / "x.wav")


def test_pitch_deviation_uses_cents_not_semitone_percent():
    assert score._cents_error(2.0, 1.0) == pytest.approx(1200.0)
    assert score._cents_error(440.0 * 2 ** (1 / 12), 440.0) == pytest.approx(100.0)


def test_decoded_i2s_candidate_requires_full_mono_int16_phrase(tmp_path):
    import numpy as np
    from scipy.io import wavfile

    short = tmp_path / "short.wav"
    wavfile.write(short, score.SR, np.zeros(100, dtype=np.int16))
    with pytest.raises(score.Refused, match="short"):
        score._load_i2s_candidate(short, 101)

    stereo = tmp_path / "stereo.wav"
    wavfile.write(stereo, score.SR, np.zeros((128, 2), dtype=np.int16))
    with pytest.raises(score.Refused, match="mono signed-int16"):
        score._load_i2s_candidate(stereo, 100)

    valid = tmp_path / "complete.wav"
    samples = np.arange(128, dtype=np.int16)
    wavfile.write(valid, score.SR, samples)
    loaded, digest = score._load_i2s_candidate(valid, 100)
    assert loaded.shape == (100,)
    assert loaded[12] == pytest.approx(12 / 32768)
    assert digest == hashlib.sha256(valid.read_bytes()).hexdigest()


def test_cleaner_alias_output_is_not_penalized():
    assert score._excess_alias_db(-62.0, -50.0) == 0.0
    assert score._excess_alias_db(-45.0, -50.0) == pytest.approx(5.0)


def test_missing_harmonic_is_scored_against_its_measured_floor():
    error, status = score._harmonic_error(
        {"h8": None, "floor8": -80.0}, {"h8": -25.9, "floor8": -90.0}, 8)
    assert error == pytest.approx(-54.1)
    assert status == "model_below_floor_bound"


def test_two_unmeasurable_harmonics_are_explicitly_uncompared():
    error, status = score._harmonic_error(
        {"h8": None, "floor8": -80.0}, {"h8": None, "floor8": -82.0}, 8)
    assert error is None
    assert status == "both_below_floor"


def test_pulse_segment_selects_pulse_in_the_model():
    common = {"waves": ("saw", "saw", "saw"), "mix": (1.0, 0.0, 0.0)}
    assert score._patch_for_wave(common, "saw")["waves"][0] == "saw"
    assert score._patch_for_wave(common, "pulse")["waves"][0] == "pulse29"
    assert score._patch_for_wave(common, "pulse", "pulse479")["waves"][0] == "pulse479"
    with pytest.raises(score.Refused, match="unsupported"):
        score._patch_for_wave(common, "triangle")


def test_m5a_candidate_uses_the_measured_filter_drive_intervention():
    manifest = json.loads(score.MANIFEST.read_text())
    assert score._voice_patch(manifest)["drive"] == pytest.approx(0.75)


def test_m5b_model_score_covers_both_waveforms_and_notes(tmp_path):
    measured = score.measure(case_id="M5B", output_path=tmp_path / "m5b-model.wav")
    assert set(measured["metrics"]) == set(score.TOLERANCES)
    assert all(metric["valid"] for metric in measured["metrics"].values())
    assert measured["analysis_version"] == "m5b-score-v1"
    assert measured["reference_sha256"]
    assert measured["manifest_sha256"]
    assert measured["model_configuration"]["engine_profile"] == "selected"
    assert measured["model_configuration"]["filter_rate_converted"] is True
    assert measured["model_configuration"]["filter_preserve_headroom"] is True
    assert measured["model_configuration"]["filter_causal"] is True
    assert measured["model_configuration"]["pulse479_filter_candidate"] is True
    assert measured["model_configuration"]["pulse_effective_waveform"] == "pulse479"
    assert measured["model_configuration"]["pulse_effective_duty_percent"] == pytest.approx(47.9, abs=0.01)
    assert measured["model_configuration"]["saw_cutoff_override_hz"] == 20_000
    assert measured["model_configuration"]["saw_volume_correction_db"] == pytest.approx(-0.45428)
    assert measured["model_configuration"]["envelope_calibration_source"] == "frozen M5B Mini V3 measurements"
    assert measured["metrics"]["Harmonic shape"]["value"] == pytest.approx(5.96219, abs=0.01)
    assert measured["metrics"]["Foldback energy"]["value"] == pytest.approx(8.84954, abs=0.02)
    assert measured["metrics"]["Envelope attack"]["error"] == pytest.approx(6.0625, abs=0.01)
    saw_alias = [event["foldback_db"]["excess_over_reference_db"]
                 for event in measured["event_diagnostics"] if event["wave"] == "saw"]
    pulse_alias = [event["foldback_db"]["excess_over_reference_db"]
                   for event in measured["event_diagnostics"] if event["wave"] == "pulse"]
    assert len(saw_alias) == len(pulse_alias) == 2
    assert max(saw_alias) < 3.0
    assert min(pulse_alias) > 3.0

    legacy_path = score.ROOT / "docs/scorecard/mono-m5b-miniv3/legacy-score-v1.json"
    legacy = json.loads(legacy_path.read_text())
    legacy_audio = score.ROOT / legacy["audio"]
    assert hashlib.sha256(legacy_audio.read_bytes()).hexdigest() == \
           legacy["diagnostics"]["legacy_model_audio_sha256"]
    assert legacy["provenance"]["config"]["pulse_effective_waveform"] == "pulse29"
    assert legacy["provenance"]["config"]["filter_rate_converted"] is False

    import measure_m5a_filter_oversample as filter_gate

    legacy_row = {"metrics": legacy["metrics"],
                  "event_diagnostics": legacy["diagnostics"]["events"]}
    selected_row = {"metrics": measured["metrics"],
                    "event_diagnostics": measured["event_diagnostics"]}
    gate = filter_gate._compare_incremental(legacy_row, selected_row)
    assert gate["accepts_incremental_improvement"] is False
    lost_pulse_partials = []
    for before, after in zip(legacy_row["event_diagnostics"],
                             selected_row["event_diagnostics"], strict=True):
        for partial, old_error in before["harmonic_error_db_model_minus_reference"].items():
            new_error = after["harmonic_error_db_model_minus_reference"][partial]
            if abs(old_error) <= 1.0 < abs(new_error):
                lost_pulse_partials.append((before["wave"], before["midi"], partial))
    assert len(lost_pulse_partials) == 3
    assert all(wave == "pulse" for wave, _note, _partial in lost_pulse_partials)
    assert [(event["wave"], event["midi"]) for event in measured["event_diagnostics"]] == [
        ("saw", 72), ("saw", 84), ("pulse", 72), ("pulse", 84)]

    import numpy as np
    from scipy.io import wavfile

    timeline = json.loads(score.MANIFESTS["M5B"].read_text())["timeline"]
    saw, pulse = timeline["segments"]
    written_sr, written = wavfile.read(tmp_path / "m5b-model.wav")
    assert written_sr == score.SR
    assert written.dtype == np.int16
    assert len(written) == pulse["offset_samples"] + pulse["samples"]
    assert measured["duration_s"] == pytest.approx(len(written) / score.SR)
    assert not np.any(written[saw["samples"]:pulse["offset_samples"]])
    assert np.any(written[:saw["samples"]])
    assert np.any(written[pulse["offset_samples"]:])


def test_shared_mono_engine_profiles_name_effective_wave_and_every_filter_flag():
    selected = score.engine_configuration("selected")
    assert selected == {
        "name": "selected-m5a-reconstructed-filter2x",
        "oscillator_oversample_2x": True,
        "filter_rate_converted": True,
        "filter_preserve_headroom": True,
        "filter_causal": True,
        "pulse479_filter_candidate": True,
        "filter_g_exact": False,
        "filter_k_comp": True,
        "filter_drive": 0.75,
        "pulse_control_label": "pulse29",
        "filter_oversample_factor": 2,
        "ladder_coefficient_oversample": 2,
        "pulse_effective_waveform": "pulse479",
        "pulse_effective_duty_percent": pytest.approx(47.9, abs=0.01),
        "saw_cutoff_hz": 20_000,
        "saw_volume_correction_db": pytest.approx(-0.45428),
    }
    legacy = score.engine_configuration("legacy")
    assert legacy["name"] == "legacy-osc2x-base-rate-filter"
    assert legacy["oscillator_oversample_2x"] is True
    assert legacy["filter_rate_converted"] is False
    assert legacy["filter_preserve_headroom"] is False
    assert legacy["filter_causal"] is False
    assert legacy["pulse479_filter_candidate"] is False
    assert legacy["filter_g_exact"] is False
    assert legacy["filter_k_comp"] is True
    assert legacy["pulse_control_label"] == "pulse29"
    assert legacy["pulse_effective_waveform"] == "pulse29"
    assert legacy["pulse_effective_duty_percent"] == pytest.approx(29.0, abs=0.01)
    assert legacy["filter_oversample_factor"] == 1
    assert legacy["ladder_coefficient_oversample"] == 2
    assert legacy["saw_cutoff_hz"] is None
    assert legacy["saw_volume_correction_db"] == 0.0


def test_saw_cutoff_override_is_scored_on_complete_phrase_and_leaves_pulse_fixed(monkeypatch, tmp_path):
    import score_m5a_i2s
    from scipy.io import wavfile

    scorer_write = wavfile.write

    def write_only_under_tmp_path(path, *args, **kwargs):
        if not pathlib.Path(path).is_relative_to(tmp_path):
            pytest.fail(f"the scorer wrote {path} outside tmp_path")
        return scorer_write(path, *args, **kwargs)

    monkeypatch.setattr(wavfile, "write", write_only_under_tmp_path)
    monkeypatch.setattr(score, "ROOT", tmp_path)
    default_wav = tmp_path / "build/scorecard/M5A-model.wav"
    common = {"pulse_shape": "pulse479",
              "voice_factory": score_m5a_i2s._candidate_factory}
    manifest = json.loads(score.MANIFEST.read_text())
    cutoff_reference = int(round(manifest["patch"]["cutoff_measurement"]["f0_hz"]))
    baseline = score.measure(**common, model_label="cutoff-control-baseline",
                             saw_cutoff_override=cutoff_reference,
                             saw_volume_correction_db=0.0,
                             output_path=tmp_path / "cutoff-control-baseline.wav")
    assert not default_wav.exists(), "the baseline call wrote the default M5A model WAV"
    default_wav.parent.mkdir(parents=True)
    seeded = b"seeded in place of the archived M5A model WAV\x00\xff\x00\xff"
    default_wav.write_bytes(seeded)
    candidate = score.measure(**common, model_label="cutoff-control-20000",
                              saw_cutoff_override=20000,
                              saw_volume_correction_db=-0.45428,
                              output_path=tmp_path / "cutoff-control-20000.wav")
    assert default_wav.read_bytes() == seeded, "the candidate call rewrote the default M5A model WAV"

    assert baseline["reference_sha256"] == candidate["reference_sha256"] == manifest["audio"]["sha256"]
    phrase = [(segment["wave"], int(event["note"])) for segment in manifest["timeline"]["segments"]
              for event in segment["midi_events"]]
    assert [(event["wave"], event["midi"]) for event in baseline["event_diagnostics"]] == phrase
    assert [(event["wave"], event["midi"]) for event in candidate["event_diagnostics"]] == phrase

    assert set(candidate["metrics"]) == set(score.TOLERANCES)
    assert candidate["model_configuration"]["saw_cutoff_override_hz"] == 20000
    assert candidate["model_configuration"]["saw_volume_correction_db"] == pytest.approx(-0.45428)
    assert candidate["metrics"]["Gain"]["error"] == pytest.approx(
        baseline["metrics"]["Gain"]["error"], abs=0.01)
    assert len(baseline["event_diagnostics"]) == len(candidate["event_diagnostics"])
    saw_changed = False
    for before, after in zip(baseline["event_diagnostics"],
                             candidate["event_diagnostics"], strict=True):
        assert (before["wave"], before["midi"]) == (after["wave"], after["midi"])
        if before["wave"] == "pulse":
            assert before["harmonic_error_db_model_minus_reference"] == \
                   after["harmonic_error_db_model_minus_reference"]
            assert before["gain_dbfs"] == after["gain_dbfs"]
        else:
            saw_changed |= before["harmonic_error_db_model_minus_reference"] != \
                           after["harmonic_error_db_model_minus_reference"]
    assert saw_changed, "cutoff override must change the executed saw measurement"


@pytest.mark.parametrize("bad", [0, 30_000, True, 14_073.5])
def test_saw_cutoff_override_refuses_invalid_model_settings(bad):
    with pytest.raises(score.Refused, match="cutoff override"):
        score.measure(saw_cutoff_override=bad)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -13.0, 13.0, True])
def test_saw_volume_correction_refuses_invalid_model_settings(bad):
    with pytest.raises(score.Refused, match="saw volume correction"):
        score.measure(saw_volume_correction_db=bad)


def test_pulse479_is_explicitly_model_only_and_has_no_rtl_encoding():
    import voice_fx as vf

    assert "pulse479" not in vf.WAVE_CODE
    with pytest.raises(score.Refused, match="unsupported M5A model pulse candidate"):
        score._patch_for_wave({"waves": ("saw",) * 3}, "pulse", "pulse50")


def test_stage_vector_reports_absolute_power_and_keeps_stages_distinct():
    import numpy as np

    note, f0, n = 84, score.vf.note_hz(84), 12_000
    tone = score.vf.OscFx("saw").render(n, score.vf.phase_inc(f0)).astype(np.int16)
    trace = {"osc": [tone], "mixed": tone, "ladder": tone.astype(np.int32) * 8}
    stages = score._stage_diagnostics(trace, tone, 0, n, f0)
    assert set(stages) == {"oscillator", "mixer", "ladder", "output"}
    assert stages["ladder"]["total_signal_power_dbfs"] > stages["output"]["total_signal_power_dbfs"] + 15
    for values in stages.values():
        assert np.isfinite(values["alias_band_power_dbfs"])
        assert np.isfinite(values["total_signal_power_dbfs"])


def test_stage_vector_refuses_an_alias_ambiguous_pure_tone():
    import numpy as np

    n = 4_800
    t = np.arange(n) / score.SR
    tone = np.rint(0.1 * 32768 * np.sin(2 * np.pi * 440 * t)).astype(np.int16)
    trace = {"osc": [tone], "mixed": tone,
             "ladder": tone.astype(np.int32) * 8}
    with pytest.raises(score.Refused, match="predicted images collide with real harmonics"):
        score._stage_diagnostics(trace, tone, 0, n, 440.0)


def test_frozen_cutoff_measurement_is_repeatable_at_the_pinned_block_size():
    manifest = json.loads((score.MANIFEST).read_text())
    measurement = manifest["patch"]["cutoff_measurement"]
    assert measurement["block_size_samples"] == manifest["host"]["block_size_samples"] == 16
    assert measurement["repeat_count"] >= 3
    assert measurement["repeat_range_hz"] <= 10.0
    wr = manifest["qualification"]["wrong_then_right"]
    assert wr["overall_wrong_then_right_rate"] == "2/6"
    assert wr["cutoff_calibration"]["discarded"]["host_block_size_samples"] == 512
    assert wr["cutoff_calibration"]["discarded"]["injected_control_caught"] is True


def test_supplied_i2s_audio_is_scored_without_rendering_the_software_voice(monkeypatch, tmp_path):
    import json

    candidate = score.ROOT / "docs/scorecard/mono-m5a-miniv3/saw-cutoff-20khz-i2s.wav"
    expected = json.loads((score.ROOT / "docs/scorecard/results/M5A.json").read_text())

    def unexpected_render(*_args, **_kwargs):
        pytest.fail("supplied I2S audio must not render the software voice")

    monkeypatch.setattr(score.vf, "render_mono_fx", unexpected_render)
    output = tmp_path / "scored.wav"
    measured = score.measure(candidate_wav=candidate, output_path=output)
    assert measured["metrics"] == expected["metrics"]
    assert measured["candidate_i2s_sha256"] == hashlib.sha256(candidate.read_bytes()).hexdigest()
    # The raw I2S capture has 21 extra tail samples outside the frozen phrase.
    # Reproduce the committed scored WAV after the scorer's timeline trim.
    expected_audio = score.ROOT / expected["audio"]
    assert hashlib.sha256(output.read_bytes()).hexdigest() == hashlib.sha256(expected_audio.read_bytes()).hexdigest()


def test_supplied_i2s_listening_wav_keeps_the_captured_gap_between_segments(monkeypatch, tmp_path):
    import numpy as np
    from scipy.io import wavfile

    manifest = json.loads(score.MANIFEST.read_text())
    record = json.loads((score.ROOT / "docs/scorecard/results/M5A.json").read_text())
    capture = score.MANIFEST.parent / "saw-cutoff-20khz-i2s.wav"
    assert hashlib.sha256(capture.read_bytes()).hexdigest() == record["diagnostics"]["decoded_i2s_sha256"]
    sr, clean = wavfile.read(capture)
    phrase = int(manifest["audio"]["samples"])
    first, second = manifest["timeline"]["segments"]
    start = first["offset_samples"] + first["samples"]
    stop = second["offset_samples"]
    assert stop - start == round(sr * manifest["timeline"]["segment_silence_s"]) >= 48
    assert not np.any(clean[start:stop])
    assert len(clean) == phrase + 21

    marked = clean.copy()
    marked[start:start + 48] = 16384
    candidate, output = tmp_path / "candidate.wav", tmp_path / "scored.wav"
    wavfile.write(candidate, sr, marked)

    def unexpected_render(*_args, **_kwargs):
        pytest.fail("supplied I2S audio must not render the software voice")

    monkeypatch.setattr(score.vf, "render_mono_fx", unexpected_render)
    measured = score.measure(candidate_wav=candidate, output_path=output)
    assert measured["candidate_i2s_sha256"] == hashlib.sha256(candidate.read_bytes()).hexdigest()
    scored_sr, scored = wavfile.read(output)
    assert scored_sr == sr
    np.testing.assert_array_equal(scored[start:stop], marked[start:stop])
    np.testing.assert_array_equal(scored, marked[:phrase])


def test_measure_without_audio_writes_no_wav_and_changes_nothing_else(tmp_path):
    """`keep_audio=False` is the writer's switch for `--no-audio` (#1430), and
    until it existed `measure` wrote its WAV to whatever destination it was
    given. With the switch off it overwrites nothing -- the sentinel at the
    destination is read back byte for byte -- and everything it returns but
    `audio` is what the audio-enabled call returns, metrics included. The
    audio-enabled call is unchanged: the default still writes, where it is
    told."""
    sentinel = tmp_path / "M5B-model.wav"
    sentinel.write_bytes(b"sentinel: written by a test, by no render")
    quiet = score.measure(case_id="M5B", output_path=sentinel, keep_audio=False)
    assert quiet["audio"] == "not written (--no-audio)"
    assert sentinel.read_bytes() == b"sentinel: written by a test, by no render"

    loud = score.measure(case_id="M5B", output_path=sentinel)
    assert sentinel.read_bytes() != b"sentinel: written by a test, by no render"
    assert loud["audio"] != "not written (--no-audio)"
    assert {k: v for k, v in quiet.items() if k != "audio"} \
        == {k: v for k, v in loud.items() if k != "audio"}


def test_load_reference_returns_the_frozen_record_with_its_digests():
    reference = score.load_reference("M5A")
    manifest = json.loads(score.MANIFEST.read_text())
    assert reference.manifest == manifest
    assert reference.audio_path == score.MANIFEST.parent / manifest["audio"]["file"]
    assert reference.audio_sha256 == manifest["audio"]["sha256"]
    assert reference.manifest_sha256 == hashlib.sha256(score.MANIFEST.read_bytes()).hexdigest()
    assert reference.pcm.dtype == np.float64
    assert len(reference.pcm) == manifest["audio"]["samples"]


def _damaged_m5a_record(tmp_path, damage):
    """An M5A record that fails exactly one gate of `load_reference`."""
    from scipy.io import wavfile

    manifest = json.loads(score.MANIFEST.read_text())
    audio = tmp_path / "audio.wav"
    if damage in ("nan", "int16"):
        samples = int(manifest["audio"]["samples"])
        pcm = (np.full(samples, np.nan, dtype=np.float32) if damage == "nan"
               else np.zeros(samples, dtype=np.int16))
        wavfile.write(audio, score.SR, pcm)
        manifest["audio"] = {**manifest["audio"], "file": audio.name,
                             "sha256": hashlib.sha256(audio.read_bytes()).hexdigest()}
    elif damage == "hash":
        audio.write_bytes(b"not the frozen audio")
        manifest["audio"] = {**manifest["audio"], "file": audio.name, "sha256": "0" * 64}
    elif damage == "suite":
        manifest["identity"] = {"name": "Mini V4", "version": "4.0.4.6366"}
    else:
        manifest["case_id"] = "M5B"
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    return path


@pytest.mark.parametrize(("damage", "reason"), [
    ("suite", "reference-suite mix"),
    ("case", "identifies as 'M5B', expected M5A"),
    ("hash", "hash mismatch"),
    ("nan", "finite-sample"),
    ("int16", "not mono float32"),
])
def test_load_reference_refuses_each_drifted_gate(tmp_path, damage, reason):
    # #1570: the five gates the three sibling tools had each dropped a part of.
    with pytest.raises(score.Refused, match=reason):
        score.load_reference("M5A", manifest_path=_damaged_m5a_record(tmp_path, damage))


def test_load_reference_honours_the_m5a_manifest_override(tmp_path, monkeypatch):
    monkeypatch.setattr(score, "MANIFEST", _damaged_m5a_record(tmp_path, "case"))
    with pytest.raises(score.Refused, match="identifies as"):
        score.load_reference("M5A")


def test_harmonic_summary_rounds_each_partial_but_not_the_max():
    model = {"h2": -40.0, "h3": -20.0, "floor4": -65.0}
    reference = {"h2": -30.0, "h3": -21.0, "h4": -30.0}
    summary = score.harmonic_summary(model, reference, 1000.0, 1000.0, 48000,
                                     context="saw MIDI 84")
    assert summary.errors["h2"] == -10.0 and summary.errors["h3"] == 1.0
    assert summary.errors["h4"] == -35.0 and summary.statuses["h4"] == "model_below_floor_bound"
    assert summary.errors["h5"] is None and summary.statuses["h5"] == "both_below_floor"
    assert summary.max_abs_error == 35.0
    # The scorecard metric is rounded to 5 dp, so the max must stay unrounded.
    near = score.harmonic_summary({"h2": -30.00004, "h3": -20.00003},
                                 {"h2": -30.0, "h3": -20.0}, 1000.0, 1000.0, 48000)
    assert near.errors["h2"] == 0.0 and near.errors["h3"] == 0.0
    assert near.max_abs_error == pytest.approx(0.00004)


def test_harmonic_summary_refuses_fewer_than_two_comparable_partials():
    signature = {"h2": -40.0, "h3": -30.0, "h4": -50.0}
    for f0 in (9000.0, 12000.0):
        with pytest.raises(score.Refused,
                           match="saw MIDI 96 has fewer than two comparable partials"):
            score.harmonic_summary(signature, signature, f0, f0, 48000, context="saw MIDI 96")
    silent = {"h2": None, "h3": None, "h4": None}
    with pytest.raises(score.Refused, match="fewer than two comparable partials"):
        score.harmonic_summary(silent, silent, 1000.0, 1000.0, 48000)


def test_selected_engine_voice_passes_blep_through_and_defaults_on():
    profile = score.engine_configuration("selected")
    assert score._voice_for_engine(profile).blep is True
    assert score._voice_for_engine(profile, blep=False).blep is False


def _swap_after_first_read(monkeypatch, target, decoy_bytes):
    """Replace `target` with `decoy_bytes` right after the first read of it."""
    real_bytes, real_text = pathlib.Path.read_bytes, pathlib.Path.read_text
    state = {"swapped": False}

    def swap(path):
        if pathlib.Path(path) == target and not state["swapped"]:
            state["swapped"] = True
            target.write_bytes(decoy_bytes)

    def read_bytes(self):
        data = real_bytes(self)
        swap(self)
        return data

    def read_text(self, *args, **kwargs):
        data = real_text(self, *args, **kwargs)
        swap(self)
        return data

    monkeypatch.setattr(pathlib.Path, "read_bytes", read_bytes)
    monkeypatch.setattr(pathlib.Path, "read_text", read_text)
    return state


def _m5a_copy(tmp_path):
    manifest = json.loads(score.MANIFEST.read_text())
    (tmp_path / manifest["audio"]["file"]).write_bytes(
        (score.MANIFEST.parent / manifest["audio"]["file"]).read_bytes())
    path = tmp_path / "manifest.json"
    path.write_bytes(score.MANIFEST.read_bytes())
    return path, manifest


def test_load_reference_manifest_content_and_digest_come_from_one_read(tmp_path, monkeypatch):
    # #1586 review: a file replaced between the parse and the hash must not pair
    # one file's content with another's digest.
    path, manifest = _m5a_copy(tmp_path)
    original = path.read_bytes()
    decoy = json.dumps({**manifest, "case_id": "M5B"}).encode()
    state = _swap_after_first_read(monkeypatch, path, decoy)
    reference = score.load_reference("M5A", manifest_path=path)
    assert state["swapped"]
    assert reference.manifest["case_id"] == "M5A"
    assert reference.manifest_sha256 == hashlib.sha256(original).hexdigest()


def test_load_reference_audio_content_and_digest_come_from_one_read(tmp_path, monkeypatch):
    from scipy.io import wavfile

    path, manifest = _m5a_copy(tmp_path)
    audio = tmp_path / manifest["audio"]["file"]
    silent = tmp_path / "silent.wav"
    wavfile.write(silent, score.SR, np.zeros(int(manifest["audio"]["samples"]), np.float32))
    state = _swap_after_first_read(monkeypatch, audio, silent.read_bytes())
    reference = score.load_reference("M5A", manifest_path=path)
    assert state["swapped"]
    assert np.count_nonzero(reference.pcm) > 0
    assert reference.audio_sha256 == manifest["audio"]["sha256"]


def test_load_reference_accepts_a_manifest_with_a_utf8_bom(tmp_path):
    # Attack-bias parsed bytes, so a BOM was accepted before it used this loader.
    path, manifest = _m5a_copy(tmp_path)
    path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())
    reference = score.load_reference("M5A", manifest_path=path)
    assert reference.manifest == manifest
    assert reference.manifest_sha256 == hashlib.sha256(path.read_bytes()).hexdigest()

"""Exit-status controls for the saw-cutoff CLI's dispatch handler (#1903).

THE DEFECT, AS MEASURED on `origin/main` `dd0449e0` (2026-11-28, red first):
an injected silent oscillator stage reaches the real
`audio_measure.harmonic_signature` through `mono_m5a_score.measure`
(`:491` -> `_stage_diagnostics` `:219`) from `measure` line 40, and `main`
caught only `(OSError, ValueError, Refused)`. The process exited 1 with an
`InsufficientEvidence: harmonic_signature: silent` traceback and no JSON.

THE KNOWN ANSWERS, independent of anything this repository models: an
estimator that cannot honestly supply a number is REFUSED (exit 2,
`tools/exit_contract.py`), and a plain `AssertionError` raised through the
same path is an execution error (exit 1, traceback intact).
`InsufficientEvidence` SUBCLASSES `AssertionError`, which is why the handler
names the subclass: catching the base would relabel every programming error
in scoring as a refusal.
"""
import json
import subprocess
import sys

import pytest

import exit_contract
import measure_m5a_saw_cutoff as candidate

REFUSAL_LINE = "measure_m5a_saw_cutoff: REFUSED -- harmonic_signature: silent"

# The frozen first segment is returned as the output PCM, so the note-level
# checks pass and the scorer reaches the stage it is told is silent.
DRIVER = """\
import pathlib
import sys

import numpy as np

root, mode, out = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, str(pathlib.Path(root) / "tools"))
import measure_m5a_saw_cutoff as cli  # noqa: E402

ref = cli.m5a.load_reference("M5A")
seg = ref.manifest["timeline"]["segments"][0]
start, length = seg["offset_samples"], seg["samples"]
pcm = np.rint(ref.pcm[start:start + length] * 32767).astype(np.int16)


def render(seq, duration, voice):
    if mode == "assert":
        raise AssertionError("injected scoring assertion")
    voice.trace = {"osc": [np.zeros_like(pcm)], "mixed": pcm, "ladder": pcm}
    return pcm


cli.vf.render_mono_fx = render
sys.exit(cli.main(["--cutoff", "20000", "--out", out]))
"""


def _drive(tmp_path, mode):
    driver = tmp_path / "drive.py"
    driver.write_text(DRIVER)
    out = tmp_path / "m5a-saw-cutoff.json"
    done = subprocess.run(
        [sys.executable, str(driver), str(candidate.ROOT), mode, str(out)],
        capture_output=True, text=True, timeout=300, cwd=str(candidate.ROOT))
    return done, out


def test_invalid_cutoff_refuses_before_render(monkeypatch):
    def unexpected_render(*_args, **_kwargs):
        pytest.fail("invalid cutoff must refuse before rendering")

    monkeypatch.setattr(candidate.m5a, "measure", unexpected_render)
    with pytest.raises(candidate.m5a.Refused, match="cutoff"):
        candidate.measure(30_000)


def test_complete_candidate_uses_selected_filter_factory_and_saw_only_override(monkeypatch):
    calls = []
    baseline_cutoff = int(round(json.loads(candidate.m5a.MANIFEST.read_text())
                                ["patch"]["cutoff_measurement"]["f0_hz"]))

    def fake_measure(**kwargs):
        calls.append(kwargs)
        factory = kwargs["voice_factory"]
        voice = factory()
        assert voice.rate_converted_ladder
        assert voice.ladder_cfg["oversample"] == 2
        assert voice.preserve_filter_headroom and voice.causal_filter
        cutoff = kwargs.get("saw_cutoff_override")
        units = {"Pitch": "cents", "Harmonic shape": "dB",
                 "Foldback energy": "dB", "Envelope attack": "ms",
                 "Envelope release": "ms", "Gain": "dB", "Clipping": "%"}
        metrics = {name: {"error": 0.0, "tolerance": tolerance[0],
                          "units": units[name]}
                   for name, tolerance in candidate.m5a.TOLERANCES.items()}
        harmonic = {"h2": -1.0 if cutoff == baseline_cutoff else -0.5}
        return {"metrics": metrics, "event_diagnostics": [
            {"wave": "saw", "midi": 84,
             "harmonic_error_db_model_minus_reference": harmonic}],
            "model_configuration": {"label": kwargs["model_label"],
                                    "pulse_shape": kwargs["pulse_shape"],
                                    "saw_cutoff_override_hz": cutoff},
            "audio": "build/fake.wav", "reference_sha256": "ref",
            "manifest_sha256": "manifest",
            "cutoff_calibration": {"f0_hz": 14_073}}

    monkeypatch.setattr(candidate.m5a, "measure", fake_measure)
    result = candidate.measure(20_000, -0.45428)

    assert len(calls) == 2
    assert calls[0]["saw_cutoff_override"] == baseline_cutoff
    assert calls[1]["saw_cutoff_override"] == 20_000
    assert calls[0]["pulse_shape"] == calls[1]["pulse_shape"] == "pulse479"
    assert calls[0].get("saw_volume_correction_db", 0.0) == 0.0
    assert calls[1]["saw_volume_correction_db"] == pytest.approx(-0.45428)
    assert result["saw_gain_correction_db"]["candidate"] == pytest.approx(-0.45428)
    assert result["comparison"]["case_passes"] is True
    assert result["comparison"]["accepts_incremental_improvement"] is True


def test_estimator_refusal_class_is_the_scorers_and_not_a_plain_assertion():
    am = candidate.am
    assert am.InsufficientEvidence is candidate.m5a.am.InsufficientEvidence
    assert issubclass(am.InsufficientEvidence, AssertionError)
    assert not issubclass(AssertionError, am.InsufficientEvidence)


def test_silent_oscillator_stage_refuses_with_exit_2_and_no_report(tmp_path):
    done, out = _drive(tmp_path, "silent")
    assert done.returncode == exit_contract.REFUSED, done.stderr[-400:]
    assert REFUSAL_LINE in done.stdout, done.stdout[-400:]
    assert "Traceback" not in done.stderr and "Traceback" not in done.stdout, done.stderr[-400:]
    assert not out.exists()


def test_plain_assertion_from_scoring_is_an_execution_error_not_a_refusal(tmp_path):
    done, out = _drive(tmp_path, "assert")
    assert done.returncode == exit_contract.FAIL, (done.returncode, done.stdout[-200:])
    assert "Traceback" in done.stderr, done.stderr[-400:]
    assert "AssertionError: injected scoring assertion" in done.stderr, done.stderr[-400:]
    assert "REFUSED" not in done.stdout, "a programming error must not be relabelled a refusal"
    assert not out.exists()

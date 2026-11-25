"""Tests and injected-bug controls for `model/acceptance_meta.py` (#1278).

Verifies that `assert_ground_truth_gate` recognizes qualified `<module>.test_*`
references independently of the registered module list, reports offending suite
tests and citations when either the module or target test is unknown, preserves
plain-language exemptions and local self-references, and enforces every cited
module in the live 808 and Moog acceptance suites.
"""

from __future__ import annotations

import os
import sys
import types
from unittest.mock import patch

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import acceptance_meta


def _make_module(name: str, funcs: dict[str, str]) -> types.ModuleType:
    mod = types.ModuleType(name)
    for fn_name, doc in funcs.items():

        def _fn():
            pass

        _fn.__name__ = fn_name
        _fn.__doc__ = doc
        setattr(mod, fn_name, _fn)
    return mod


def test_catches_missing_target_in_registered_module():
    gt = _make_module("test_audio_measure", {"test_known": ""})
    suite = _make_module(
        "test_suite",
        {
            "test_case": (
                "[method] Checks estimator.\n\n"
                "Ground truth: test_audio_measure.test_missing\n"
            ),
        },
    )
    with pytest.raises(AssertionError) as exc_info:
        acceptance_meta.assert_ground_truth_gate(suite, gt)
    assert "test_case -> test_audio_measure.test_missing" in str(exc_info.value)


def test_catches_citation_to_unregistered_module():
    gt = _make_module("test_audio_measure", {"test_known": ""})
    suite = _make_module(
        "test_suite",
        {
            "test_case": (
                "[hardware-measured: ref] Checks snare balance.\n\n"
                "Ground truth: test_drum_fit.test_missing_snare_ground_truth\n"
            ),
        },
    )
    with pytest.raises(AssertionError) as exc_info:
        acceptance_meta.assert_ground_truth_gate(suite, gt)
    msg = str(exc_info.value)
    assert "test_case -> test_drum_fit.test_missing_snare_ground_truth" in msg
    assert "unknown module" in msg


def test_catches_misspelled_module_name():
    gt = _make_module("test_audio_measure", {"test_known": ""})
    suite = _make_module(
        "test_suite",
        {
            "test_case": (
                "[method] Checks estimator.\n\n"
                "Ground truth: test_audio_measur.test_known\n"
            ),
        },
    )
    with pytest.raises(AssertionError) as exc_info:
        acceptance_meta.assert_ground_truth_gate(suite, gt)
    msg = str(exc_info.value)
    assert "test_case -> test_audio_measur.test_known" in msg
    assert "unknown module" in msg


def test_catches_citation_with_empty_registry():
    suite = _make_module(
        "test_suite",
        {
            "test_case": (
                "[method] Checks estimator.\n\nGround truth: test_drum_fit.test_known\n"
            ),
        },
    )
    with pytest.raises(AssertionError) as exc_info:
        acceptance_meta.assert_ground_truth_gate(suite, {})
    assert "test_case -> test_drum_fit.test_known" in str(exc_info.value)


def test_catches_dotted_module_path_when_unregistered():
    gt = _make_module("test_drum_fit", {"test_known": ""})
    suite = _make_module(
        "test_suite",
        {
            "test_case": (
                "[method] Checks estimator.\n\n"
                "Ground truth: model.test_drum_fit.test_known\n"
            ),
        },
    )
    with pytest.raises(AssertionError) as exc_info:
        acceptance_meta.assert_ground_truth_gate(suite, gt)
    assert "test_case -> model.test_drum_fit.test_known" in str(exc_info.value)


def test_preserves_plain_language_exemptions_and_non_test_dotted_tokens():
    gt = _make_module("test_audio_measure", {"test_known": ""})
    suite = _make_module(
        "test_suite",
        {
            "test_register_readback": (
                "[method] Arithmetic on register.\n\n"
                "Ground truth: none needed -- register read-back and arithmetic\n"
                "(see drums_fx.py, e.g. dx.BD_HZ at 2.0; rendered counterpart is\n"
                "test_rendered_counterpart).\n"
            ),
            "test_prose_with_valid_citation": (
                "[method] Measures rms.\n\n"
                "Ground truth: test_audio_measure.test_known\n"
                "(validates audio_measure.rms_envelope from audio_measure.py).\n"
            ),
        },
    )
    acceptance_meta.assert_ground_truth_gate(suite, gt)


def test_preserves_local_self_references_and_multiple_modules():
    gt = _make_module("test_audio_measure", {"test_shared": ""})
    suite = _make_module(
        "test_local_suite",
        {
            "test_meta_local_estimator": "[meta] Validates local estimator.",
            "test_uses_both": (
                "[method] Uses shared and local estimators.\n\n"
                "Ground truth: test_audio_measure.test_shared,\n"
                "test_local_suite.test_meta_local_estimator\n"
            ),
        },
    )
    acceptance_meta.assert_ground_truth_gate(
        suite, {gt.__name__: gt, suite.__name__: suite}
    )

    broken = _make_module(
        "test_local_suite",
        {
            "test_uses_missing_local": (
                "[method] Uses missing local estimator.\n\n"
                "Ground truth: test_local_suite.test_meta_missing\n"
            ),
        },
    )
    with pytest.raises(AssertionError) as exc_info:
        acceptance_meta.assert_ground_truth_gate(
            broken, {gt.__name__: gt, broken.__name__: broken}
        )
    assert "test_uses_missing_local -> test_local_suite.test_meta_missing" in str(
        exc_info.value
    )


def test_808_gate_catches_missing_snare_tom_targets_and_misspelled_module():
    import test_808_acceptance as kit

    gate = kit.test_meta_every_test_declares_status_and_ground_truth
    gate()

    snare = kit.test_sd_noise_balance_matches_a_real_machine
    original = "test_drum_fit.test_separator_recovers_a_known_noise_share"
    assert snare.__doc__.count(original) == 1
    for module in ("test_audio_measure", "test_drum_fit", "test_drum_fitt"):
        missing = f"{module}.test_missing_snare_ground_truth"
        with patch.object(snare, "__doc__", snare.__doc__.replace(original, missing)):
            with pytest.raises(AssertionError) as exc_info:
                gate()
            assert f"{snare.__name__} -> {missing}" in str(exc_info.value)

    tom = kit.test_tom_pitch_falls_during_the_ring
    tom_cases = (
        (
            "test_tom_pitch_probe.test_recovers_small_drops",
            "test_tom_pitch_probe.test_missing_tom_probe",
        ),
        (
            "test_tom_drop_law.test_the_resample_control_passes_the_conversion_actually_used",
            "test_tom_drop_law.test_missing_tom_law",
        ),
    )
    for orig, missing in tom_cases:
        assert tom.__doc__.count(orig) == 1
        with patch.object(tom, "__doc__", tom.__doc__.replace(orig, missing)):
            with pytest.raises(AssertionError) as exc_info:
                gate()
            assert f"{tom.__name__} -> {missing}" in str(exc_info.value)


def test_moog_gate_passes_and_catches_unregistered_module():
    import test_moog_acceptance as moog

    gate = moog.test_meta_every_test_declares_status_and_ground_truth
    gate()

    drift = moog.test_drift_moves_the_rendered_pitch_by_the_commanded_cents
    original = "test_osc_drift_probe.test_known_ou_wander_is_recovered"
    assert drift.__doc__.count(original) == 1
    misspelled = "test_osc_drift_prob.test_known_ou_wander_is_recovered"
    with patch.object(drift, "__doc__", drift.__doc__.replace(original, misspelled)):
        with pytest.raises(AssertionError) as exc_info:
            gate()
        assert f"{drift.__name__} -> {misspelled}" in str(exc_info.value)


def test_known_defeat_boundary_unqualified_and_non_test_tokens():
    """docs/verification-rules.md rule 8: record the inputs that defeat this
    guard by construction. Plain-language exemptions may mention bare `test_*`
    names (e.g. `test_bd_rendered_decay_at_each_setting` in the 808 suite) or
    non-test dotted attributes of an UNREGISTERED module
    (`audio_measure.rms_envelope`), so those pass. A qualified citation into a
    REGISTERED module is always checked, whatever the target is called, so a
    misspelled `tst_` target there fails (see the next test).
    """
    gt = _make_module("test_audio_measure", {"test_known": ""})
    suite = _make_module(
        "test_suite",
        {
            "test_unqualified_only": (
                "[method] Defeat control.\n\n"
                "Ground truth: test_missing_without_module\n"
            ),
            "test_prose_attribute": (
                "[method] Defeat control.\n\n"
                "Ground truth: none needed; see audio_measure.rms_envelope\n"
            ),
        },
    )
    acceptance_meta.assert_ground_truth_gate(suite, gt)


def test_catches_misspelled_target_in_registered_module():
    """A `tst_` typo in a registered module's target must be named."""
    gt = _make_module("test_audio_measure", {"test_known": ""})
    suite = _make_module(
        "test_suite",
        {
            "test_non_test_prefix": (
                "[method] Typo control.\n\n"
                "Ground truth: test_audio_measure.tst_missing\n"
            ),
        },
    )
    with pytest.raises(AssertionError) as exc_info:
        acceptance_meta.assert_ground_truth_gate(suite, gt)
    assert "test_non_test_prefix -> test_audio_measure.tst_missing" in str(
        exc_info.value)

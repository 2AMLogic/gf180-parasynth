"""The focused configuration check for tools/probes/f1_selected_path.py:
selecting the wrong ladder path must be refused and must fail the bit-exact
comparison, for that reason and not for an import error, silence or a missing
cache.

Why it is needed: on F1A/F1B the selected and legacy paths read the same corner
to 0.03 % and the same rolloff to 0.01 dB/oct, so the F1 numbers alone cannot
tell which path was measured. Only identity plus an exact comparison can.
"""
from __future__ import annotations

import pathlib
import sys

import numpy as np
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "probes"))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "model"))

import f1_selected_path as fsp        # noqa: E402


@pytest.fixture(scope="module")
def selected():
    return fsp.build_voice("selected")[1]


def test_the_selected_voice_passes_its_identity_check(selected):
    ident = fsp.check_identity(selected)
    assert ident["problems"] == []
    assert ident["ladder_class"] == "RateConvertedLadder"
    assert (ident["factor"], ident["causal"], ident["preserve_headroom"]) == (2, True, True)


def test_the_probe_reproduces_the_selected_component_bit_for_bit(selected):
    probe = fsp.build_voice("selected")[1]
    m = fsp.exact_match(selected, probe)
    assert m["ok"], m
    assert m["frames"] == 12000 and m["ladder_mismatches"] == 0
    # Not a vacuous match: the voice played, and the cutoff moved.
    assert m["ladder_rms_q15"] > 1000
    assert m["cutoff_hz_range"][1] > 2 * m["cutoff_hz_range"][0]


def test_the_legacy_ladder_path_is_refused_by_name(selected):
    legacy = fsp.build_voice("legacy")[1]
    problems = fsp.check_identity(legacy)["problems"]
    assert any("not RateConvertedLadder" in p for p in problems), problems


def test_the_legacy_ladder_path_fails_the_exact_comparison(selected):
    legacy = fsp.build_voice("legacy")[1]
    m = fsp.exact_match(selected, legacy)
    # Same registers (the ROMs are shared), different ladder output: the
    # comparison sees the path, not the coefficient conversion.
    assert m["g_mismatches"] == 0 and m["k_mismatches"] == 0
    assert m["ladder_mismatches"] > 0.9 * m["frames"], m
    assert m["ladder_rms_q15"] > 1000


@pytest.mark.parametrize("flag,problem", [
    ("causal", "non-causal"),
    ("preserve_headroom", "headroom"),
])
def test_a_misconfigured_rate_converted_ladder_is_refused(flag, problem):
    v = fsp.build_voice("selected")[1]
    setattr(v.ladder, flag, False)
    problems = fsp.check_identity(v)["problems"]
    assert any(problem in p for p in problems), problems


def test_the_wrong_path_control_exits_1_with_the_stated_reason(capsys):
    assert fsp.main(["--inject", "WRONG_LADDER_PATH"]) == 1
    out = capsys.readouterr().out
    assert "ladder is LadderFx, not RateConvertedLadder" in out
    assert "differs from the selected component" in out


def test_a_tampered_reference_refuses_for_the_hash_not_for_absence(capsys):
    if not (ROOT / "refprofile" / "cache").is_dir():
        pytest.skip("frozen cache not restored; an absent cache is not a caught defect")
    assert fsp.main(["--inject", "REF_PROFILE_TAMPERED"]) == 2
    out = capsys.readouterr().out
    assert "is not the audio the profile describes" in out


def test_the_531aa8a_rolloff_differs_from_the_current_one_only_through_its_corner():
    """Known answer: on an ideal 4-pole whose passband is flat the two corner
    bases agree, so the two rolloff estimators must agree too."""
    import run_case as rc
    f = np.geomspace(40, 12000, 32)
    fp = 250.0
    g = -40 * np.log10(np.abs(1 + 1j * f / fp))     # |1/(1+jf/fp)|^4 in dB
    cut = 250.0
    new = rc.filt_rolloff(cut)(f, g)
    old = fsp.rolloff_531aa8a(cut)(f, g)
    assert new.ok and old.ok
    assert abs(new.value - old.value) < 1.0

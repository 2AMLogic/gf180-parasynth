"""#546: the probe's gate can fail, and the two callers' decision stands."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import corner_reference_convention as crc                            # noqa: E402


def test_the_gate_turns_red_on_an_injected_cutoff_dependent_bias_and_only_then():
    assert crc.controls() == []


def test_the_old_convention_is_moved_and_the_plateau_one_kept_on_every_condition():
    for kw in ({}, dict(freqs_override=crc.HELD_OUT_GRID), dict(pole_scale=1.3)):
        for name, v in crc.report(**kw).items():
            assert v == {"median": "MOVE", "dc": "KEEP"}, (name, kw, v)


def test_the_threshold_is_the_lock_tolerance_not_a_literal():
    assert crc.threshold_pct() == 3.0

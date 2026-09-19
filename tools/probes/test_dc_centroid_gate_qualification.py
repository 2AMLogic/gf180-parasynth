import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import dc_centroid_gate_qualification as qualification


def test_global_centroid_gate_rejects_a_harmless_sub20_contaminant():
    result = qualification.case()
    assert abs(result["global_shift_pct"]) >= 15.6
    assert abs(result["protected_shift_pct"]) < 0.01


def test_the_qualified_band_excludes_the_contaminant_without_hiding_a_1khz_change():
    result = qualification.case()
    assert abs(result["protected_before_hz"] - 1000.0) < 0.01
    assert abs(result["protected_after_hz"] - 1000.0) < 0.01

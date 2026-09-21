"""Whole-chip register-image tests for the top-level reference model."""
import pytest
import synth_top_model as stm


@pytest.mark.parametrize("address,image_key", [
    (stm.A_AMP + 3, "amp"),
    (stm.A_FILT + 3, "fenv"),
])
def test_voice_release_rate_keeps_the_exponent_field(address, image_key):
    model = stm.SynthTopModel()
    rate = 0x0EA1D6

    assert model._write_voice(0, address, rate) == "image"

    assert model.img[image_key][3] == rate

"""Bit-exact and injected-clamp controls for the causal RTL rate converter."""

import verify_rate_conv_2x as verify


def test_causal_rate_converter_rtl_matches_python_exactly():
    assert verify.run() == 0


def test_reconstruction_clamp_mutation_is_detected():
    assert verify.run(inject_clamp=True) == 1

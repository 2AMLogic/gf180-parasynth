"""The RTL filter-chain harness's refusals and its comparator.

No simulator is needed for any of this: what is tested here is the reading of
vvp's output (where an `x` must never become a number, and a short run must
never become a measurement) and the stream comparison the anchor rests on.
"""
import numpy as np
import pytest

import f1_rtl_filter_path as rtlpath


def test_engine_and_version_are_named_once():
    assert rtlpath.ENGINE == "integrated-rtl"
    assert rtlpath.RtlFilterChainPath.engine == rtlpath.ENGINE
    assert rtlpath.CHAIN_VERSION in rtlpath.RtlFilterChainPath.render_label


def test_a_short_run_is_refused_not_truncated(tmp_path):
    out = tmp_path / "o.txt"
    out.write_text("1\n2\n3\n")
    with pytest.raises(rtlpath.Refused, match="did not run to completion"):
        rtlpath.read_rtl_output(out, 5)


def test_an_undefined_sample_is_refused_rather_than_read_as_zero(tmp_path):
    out = tmp_path / "o.txt"
    out.write_text("1\nx\n3\n")
    with pytest.raises(rtlpath.Refused, match="undefined"):
        rtlpath.read_rtl_output(out, 3)


def test_a_word_outside_the_output_format_is_refused(tmp_path):
    out = tmp_path / "o.txt"
    out.write_text(f"1\n{1 << 18}\n3\n")
    with pytest.raises(rtlpath.Refused, match="outside the 19-bit output word"):
        rtlpath.read_rtl_output(out, 3)


def test_a_missing_output_file_is_refused(tmp_path):
    with pytest.raises(rtlpath.Refused, match="no RTL output"):
        rtlpath.read_rtl_output(tmp_path / "nope.txt", 1)


def test_a_valid_run_reads_back_signed_words(tmp_path):
    out = tmp_path / "o.txt"
    out.write_text("-262144\n0\n262143\n")
    got = rtlpath.read_rtl_output(out, 3)
    assert list(got) == [-262144, 0, 262143]


def test_compare_streams_is_exact_and_locates_the_first_difference():
    model = np.arange(10, dtype=np.int64)
    same = rtlpath.compare_streams(model, model.copy())
    assert same["bit_exact"] and same["mismatches"] == 0
    assert same["first_mismatch_frame"] is None and same["max_abs_error_lsb"] == 0

    rtl = model.copy()
    rtl[4] += 3
    rtl[7] -= 1
    off = rtlpath.compare_streams(model, rtl)
    assert off["bit_exact"] is False and off["mismatches"] == 2
    assert off["first_mismatch_frame"] == 4
    assert off["first_mismatch_model"] == 4 and off["first_mismatch_rtl"] == 7
    assert off["max_abs_error_lsb"] == 3
    assert off["model_sha256"] != off["rtl_sha256"]


def test_an_unknown_injection_is_refused_before_anything_runs():
    with pytest.raises(rtlpath.Refused, match="unknown RTL injection"):
        rtlpath.RtlFilterChainPath(inject="NOT_A_DEFECT")


def test_the_ladder_configuration_this_bench_instantiates_is_stated():
    """The bench hard-codes a 24-bit/20-fraction state and a 16-entry tanh; if the
    model's configuration ever moves, the harness must refuse rather than compare
    two different filters."""
    assert rtlpath.EXPECTED_LADDER_CFG["out_bits"] == 19
    assert rtlpath.TANH_ROM[rtlpath.EXPECTED_LADDER_CFG["tanh_entries"]] == "tanh16.hex"
    assert rtlpath.TANH_LOG2N[16] == 4


def test_the_bench_and_its_modules_exist():
    """An instrument whose sources are missing is an unsatisfiable gate."""
    for rel in ("rtl-sketch/tb_f1_chain.v", "rtl-sketch/rate_conv_2x.v",
                "rtl-sketch/ladder_dp_n.v"):
        assert (rtlpath.ROOT / rel).is_file(), rel


def test_the_bench_composes_the_chain_the_voice_composes():
    """A transcription check, cheap enough to keep: tb_f1_chain must instantiate
    the two production modules and drive the ladder at the high rate (os2x=0),
    which is what makes it the voice's 2x filter path rather than a new one."""
    text = (rtlpath.ROOT / "rtl-sketch/tb_f1_chain.v").read_text()
    assert "rate_conv_2x u_rc" in text
    assert "ladder_dp_n #(.NCH(1)" in text
    assert ".os2x(1'b0)" in text
    assert "INJECT_BUG_F1_CHAIN_SKIP_INTERP" in text
    assert "INJECT_BUG_F1_CHAIN_DROP_DECIM" in text

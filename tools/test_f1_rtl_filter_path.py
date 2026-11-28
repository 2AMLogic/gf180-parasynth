"""The RTL filter-chain harness's refusals and its comparator.

No simulator is needed for any of this: what is tested here is the reading of
vvp's output (where an `x` must never become a number, and a short run must
never become a measurement), the stream comparison the anchor rests on, and
the CLI's own default length -- which must be one that can be projected.
"""
import re

import numpy as np
import pytest

import f1_rtl_filter_path as rtlpath
import exit_contract                     # noqa: E402
import reference_rigs as rr              # noqa: E402
import refprofile as rp                  # noqa: E402
import run_case as rc                    # noqa: E402

_REAL_RENDER = rtlpath.sp.render_path


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


# ---------------------------------------------------------------------------
# the CLI's bare invocation (#1617)
#
# THE DEFECT, red-first on main @ 0a6b0861 (2026-11-28): `--frames` defaulted
# to 20000, but the first analysis window of the frozen F1 grid ends at frame
# 28,800, so `python tools/f1_rtl_filter_path.py` with no arguments always
# printed `REFUSED -- --frames 20000 is shorter than the first analysis
# window` and exited 2. A smoke run that cannot succeed as documented.
#
# THE KNOWN ANSWER, from the arithmetic and not from the module under test: the
# grid's first tone is 40 Hz, 1200 frames a period at 48 kHz, and
# `reference_rigs.tone_train` floors every segment at 8 settle + 16 window
# periods -- 24 * 1200 = 28,800 frames, where SETTLE_S + WINDOW_S is 12,480.
#
# WHAT RUNS FOR REAL: `main`, the `RtlFilterChainPath` constructor (the model
# reference path with its identity and exact-match checks), `stimulus`
# (`tone_train` on the committed grid), `curve` (the fixed-point model render
# and the projection) and `_profile_freqs` on the committed clip metadata.
# Doubled: tool discovery and the simulator, and the cached Surge audio that
# `load_filter_reference` hashes and projects.
# ---------------------------------------------------------------------------
F1A_CLIP = "surge-type2/lp-cut250-res0.00"
FIRST_WINDOW_END = 24 * 1200             # 8 settle + 16 window periods of 40 Hz


class _SmokeBench:
    """The real CLI with only its tools and simulator doubled. The simulator
    returns the model's own words, or differs in the last frame on request."""

    def __init__(self, capsys):
        self.capsys = capsys
        self.model_stream = None
        self.corrupt_last_frame = False

    def render(self, voice, xq, cut, regs):
        y, g, k_eff = _REAL_RENDER(voice, xq, cut, regs)
        self.model_stream = np.asarray(y, dtype=np.int64)
        return y, g, k_eff

    def simulate(self, path, xq, words, tag):
        # the provenance the real compile/simulate leave on the path; `curve`
        # records them, so a double that skips them is not a simulator
        path.compile_command = path.last_sim_command = path.last_sim_report = \
            "test double -- no RTL ran"
        y = self.model_stream.copy()
        if self.corrupt_last_frame:
            y[-1] += 1
        return y

    def run(self, argv):
        code = rtlpath.main(argv)
        return code, self.capsys.readouterr().out


@pytest.fixture
def smoke(monkeypatch, tmp_path, capsys):
    bench = _SmokeBench(capsys)
    profile = rp.load_profile()

    def frozen_clip(clip_id, inject=""):
        return None, None, profile["clips"][clip_id]

    monkeypatch.setattr(rc, "load_filter_reference", frozen_clip)
    monkeypatch.setattr(rtlpath, "RTL_DIR", tmp_path)
    monkeypatch.setattr(rtlpath, "_tool", lambda name: str(tmp_path / name))
    monkeypatch.setattr(rtlpath, "_tool_version", lambda binary: "test double -- no tool ran")
    monkeypatch.setattr(rtlpath.sp, "render_path", bench.render)

    def simulate(path, xq, words, tag):
        return bench.simulate(path, xq, words, tag)

    monkeypatch.setattr(rtlpath.RtlFilterChainPath, "simulate", simulate)
    return bench


def _reported(out):
    """(frames, tones) from the CLI's report line."""
    m = re.search(r"f1_rtl_filter_path: (\d+) frames, (\d+) tones", out)
    assert m, out
    return int(m.group(1)), int(m.group(2))


def test_the_first_analysis_window_of_the_frozen_grid_ends_at_28800():
    freqs = rp.load_profile()["clips"][F1A_CLIP]["freqs_hz"]
    _, parts = rr.tone_train(list(freqs), rp.PROBE_AMP * rr.FS_Q15,
                             rtlpath.SETTLE_S, rtlpath.WINDOW_S)
    assert parts[0] == (9600, 19200, 40.0)
    assert parts[0][0] + parts[0][1] == FIRST_WINDOW_END == 28800


@pytest.mark.parametrize("argv, frames", [([], 30000), (["--frames", "28800"], 28800)],
                         ids=["bare-default", "exactly-the-window"])
def test_a_length_reaching_the_first_window_projects_one_tone(smoke, argv, frames):
    code, out = smoke.run(argv)
    assert code == exit_contract.PASS, out
    assert _reported(out) == (frames, 1), out
    assert "BIT-EXACT" in out, out


def test_the_bare_invocation_fails_when_the_rtl_differs(smoke):
    """Its exit 0 is the comparison's verdict, not a vacuous pass."""
    smoke.corrupt_last_frame = True
    code, out = smoke.run([])
    assert code == exit_contract.FAIL, out
    assert _reported(out) == (30000, 1), out
    assert "DIFFERS" in out and "1 of 30000 frames" in out, out


def test_the_shipped_default_20000_refuses_though_it_exceeds_the_seconds_sum(smoke):
    """The input that defeats a guard written in SETTLE_S + WINDOW_S: that is
    12,480 frames, so 20000 would pass it and have nothing to project."""
    assert (rtlpath.SETTLE_S + rtlpath.WINDOW_S) * rr.SR < 20000 < FIRST_WINDOW_END
    code, out = smoke.run(["--frames", "20000"])
    assert code == exit_contract.REFUSED, out
    assert "REFUSED" in out, out
    assert "--frames 20000 is shorter than the first analysis window" in out, out


def test_one_frame_short_of_the_first_window_is_refused(smoke):
    code, out = smoke.run(["--frames", str(FIRST_WINDOW_END - 1)])
    assert code == exit_contract.REFUSED, out
    assert "REFUSED" in out, out
    assert "--frames 28799 is shorter than the first analysis window" in out, out


def test_frames_zero_still_runs_the_whole_train(smoke):
    code, out = smoke.run(["--frames", "0"])
    assert code == exit_contract.PASS, out
    assert _reported(out) == (442296, 32), out


def test_the_frames_help_names_the_default_and_the_zero_convention(capsys):
    with pytest.raises(SystemExit) as e:
        rtlpath.main(["--help"])
    assert e.value.code == 0
    out = capsys.readouterr().out
    assert "default: 30000" in out, out
    assert "0 = the whole F1 train" in out, out

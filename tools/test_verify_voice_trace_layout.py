"""#1356: the voice comparator reads the trace in the order tb_voice.v writes
it -- exactly the expected frame rows, then ONE complete final-state row, then
end of input -- instead of filtering STATE rows out of a pile and zipping the
common prefix. Pure Python, no simulator: compare() is fed one-frame traces at
the boundary the partitioning read got wrong, where a trace that omitted all
final-state evidence still printed "final state identical."

The status contract under test (verify_voice.py's exit doc): 0 identical,
1 a COMPLETE record with a wrong value or X (an injected defect, the all-X
stub), 2 malformed or incomplete output -- a refusal that cannot satisfy an
injected-defect control, which must require exactly 1.
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "rtl-sketch"))

import verify_voice as v  # noqa: E402

FRAME = [0] * len(v.FIELDS)                     # the model's one frame of taps
STATE = list(range(len(v.STATE_FIELDS)))        # a final state with every field distinct
REPORT = [dict(key="t", n=1, name="trace", f0=0, writes=0, cov="")]


def frame_row(i, values):
    return " ".join(map(str, [i] + list(values) + [100]))


def write(tmp_path, text):
    trace = tmp_path / "trace"
    trace.write_text(text)
    return str(trace)


def compare_trace(tmp_path, text, frames=None, state=None):
    return v.compare(frames or [FRAME], state or STATE, REPORT,
                     write(tmp_path, text), name="t")


def test_complete_trace_passes(tmp_path, capsys):
    assert compare_trace(tmp_path, frame_row(0, FRAME) + "\nSTATE "
                         + " ".join(map(str, STATE)) + "\n") == 0
    assert "final state identical" in capsys.readouterr().out


def test_wrong_value_in_a_complete_record_fails_and_names_the_field(tmp_path, capsys):
    status = compare_trace(tmp_path, frame_row(0, FRAME) + "\nSTATE "
                           + " ".join(map(str, STATE[:-1] + [999])) + "\n")
    assert status == 1
    out = capsys.readouterr().out
    assert "drift_acc2 model 16 RTL 999" in out
    assert v.LAST_COMPARE["state_mismatch"] == [("drift_acc2", 16, "999")]


def test_truncated_final_state_refuses(tmp_path, capsys):
    assert compare_trace(tmp_path, frame_row(0, FRAME) + "\nSTATE "
                         + " ".join(map(str, STATE[:-1])) + "\n") == 2


def test_state_marker_alone_refuses(tmp_path):
    assert compare_trace(tmp_path, frame_row(0, FRAME) + "\nSTATE\n") == 2


def test_extra_frame_row_refuses(tmp_path):
    # one frame expected; the bench's layout declares the STATE row next
    assert compare_trace(tmp_path, frame_row(0, FRAME) + "\n" + frame_row(1, FRAME)
                         + "\nSTATE " + " ".join(map(str, STATE)) + "\n") == 2


def test_second_state_record_refuses(tmp_path):
    assert compare_trace(tmp_path, frame_row(0, FRAME) + "\nSTATE "
                         + " ".join(map(str, STATE)) + "\nSTATE "
                         + " ".join(map(str, STATE)) + "\n") == 2


def test_surplus_value_on_the_state_row_refuses(tmp_path):
    assert compare_trace(tmp_path, frame_row(0, FRAME) + "\nSTATE "
                         + " ".join(map(str, STATE + [17])) + "\n") == 2


def test_state_record_before_the_final_frame_row_refuses(tmp_path):
    # a displaced STATE row promoted the trailing frame row into the comparison
    assert compare_trace(tmp_path, "STATE " + " ".join(map(str, STATE)) + "\n"
                         + frame_row(0, FRAME) + "\n") == 2


def test_frame_row_after_the_state_record_refuses(tmp_path):
    assert compare_trace(tmp_path, frame_row(0, FRAME) + "\nSTATE "
                         + " ".join(map(str, STATE)) + "\n" + frame_row(1, FRAME)
                         + "\n") == 2


def test_short_frame_output_refuses(tmp_path, capsys):
    status = compare_trace(tmp_path, frame_row(0, FRAME) + "\n")
    assert status == 2
    assert "did not run to completion" in capsys.readouterr().out


def test_x_in_a_complete_state_record_fails_not_refuses(tmp_path):
    # the all-X stub's contract: X in a COMPLETE record is a mismatch (1),
    # never a refusal -- a refusal would hide an unimplemented design
    assert compare_trace(tmp_path, frame_row(0, FRAME) + "\nSTATE "
                         + " ".join(["x"] * len(STATE)) + "\n") == 1


def test_x_in_frame_taps_fails_with_x_reported(tmp_path, capsys):
    status = compare_trace(tmp_path, frame_row(0, ["x"] * len(v.FIELDS)) + "\nSTATE "
                           + " ".join(map(str, STATE)) + "\n")
    assert status == 1
    out = capsys.readouterr().out
    assert f"{len(v.FIELDS)} undefined/X" in out
    assert v.LAST_COMPARE["mism"]["sample"] == 1


def test_wrong_frame_tap_still_fails_with_its_diagnostic(tmp_path, capsys):
    bad = list(FRAME)
    bad[1] = 5                                            # osc0
    status = compare_trace(tmp_path, frame_row(0, bad) + "\nSTATE "
                           + " ".join(map(str, STATE)) + "\n")
    assert status == 1
    assert "first osc0 mismatch at frame 0 [t]: model 0, RTL 5" in capsys.readouterr().out


def test_several_matching_frames_pass(tmp_path):
    frames = [FRAME, [1] * len(v.FIELDS), FRAME]
    text = "".join(frame_row(i, f) + "\n" for i, f in enumerate(frames)) \
        + "STATE " + " ".join(map(str, STATE)) + "\n"
    assert v.compare(frames, STATE, [dict(key="t", n=3, name="trace", f0=0, writes=0, cov="")],
                     write(tmp_path, text), name="t") == 0

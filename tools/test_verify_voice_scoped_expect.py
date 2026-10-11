"""#354: `verify_voice_scoped.py --expect-first` catches a control only where it
was recorded to fail. Pure Python, no simulator: the matcher is fed compare()
results shaped like verify_voice.LAST_COMPARE, including the inputs that would
defeat a bare --expect-fail."""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import verify_voice_scoped as vs  # noqa: E402

# R1's recorded signature on the all-maximum image (docs/verification/voice-fullset-354/x0.log)
R1 = {"status": 1, "n": 960,
      "mism": {**{f: 0 for f in vs.PIPELINE}, "sample": 387, "y19": 758, "v": 758, "out_v": 758},
      "first": {"sample": (203, 32767, "-32768", "extremes"), "y19": (62, -262119, "-262144", "extremes"),
                "v": (62, -262112, "-262136", "extremes"), "out_v": (62, -524217, "-524265", "extremes")}}


def test_r1_signature_is_caught():
    assert vs.check_expect_first(R1, "y19@62:387")[0]
    assert vs.check_expect_first(R1, "y19@62")[0]


def test_clean_pass_is_not_caught():
    assert not vs.check_expect_first({"status": 0, "n": 960, "mism": {}, "first": {}}, "y19@62")[0]


def test_no_verdict_is_not_caught():
    assert not vs.check_expect_first({}, "y19@62")[0]


def test_failure_elsewhere_is_not_caught():
    # defeating input for a bare --expect-fail: an unrelated, EARLIER, upstream divergence
    other = {**R1, "first": {**R1["first"], "mixed": (10, 1, "2", "extremes")}}
    caught, msg = vs.check_expect_first(other, "y19@62:387")
    assert not caught and "mixed" in msg


def test_downstream_tap_at_same_frame_does_not_mask_upstream():
    # v and out_v diverge at 62 too; y19 is named because it is upstream of them
    assert vs.first_divergence(R1["first"]) == (62, "y19")
    assert not vs.check_expect_first(R1, "v@62")[0]


def test_wrong_sample_count_is_not_caught():
    assert not vs.check_expect_first(R1, "y19@62:386")[0]


def test_wrong_frame_is_not_caught():
    # right tap, right sample count, wrong frame: only the frame comparison rejects this
    caught, msg = vs.check_expect_first(R1, "y19@63:387")
    assert not caught and "63" in msg


@pytest.mark.parametrize("spec", ["nosuchtap@62", "y19", "y19@x", "y19@62:x", "y19@", ""])
def test_malformed_spec_refuses(spec):
    with pytest.raises(SystemExit) as e:
        vs.parse_expect_first(spec)
    assert str(e.value.code).startswith("REFUSED")

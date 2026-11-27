"""verify_fixture.py must REFUSE (exit 2) a capture that is not of the stream it
sent, rather than pass or satisfy --expect-fail on a prefix (#1482).

The simulator is replaced by files built from the real host schedule, and the
model by a stub with known output, so the only thing under test is the verifier's
own gating. Each refusal case has a clean control that exits 0 through the same
harness: a refusal test with no passing twin proves nothing.
"""
import os
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import verify_fixture as vf  # noqa: E402

# Read from the module that owns the contract, not through verify_fixture: the
# harness must run (and its clean control pass) on a tree that predates #1482.
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "rtl-sketch"))
from verify_deadline import I2S_DRAIN  # noqa: E402

N_FRAMES = 40000  # the stub model's length; bar808's own n is larger, so pass --frames


class _Model:
    def run(self, writes, n):
        z = np.zeros(n, dtype=np.int64)
        i2s = (np.arange(n) % 2000 + 5000).astype(np.int64)
        return dict(i2s=i2s, sample=i2s.copy(), dmix=z, body=z)


def _sent(fixture, wrong, link="bench"):
    host, n_fix, _ = vf.fixtures.FIXTURES[fixture]()
    lk = vf.LINKS[link]
    laid = vf.sh.lay_out(host.w, lk)
    truth = vf.sh.place(laid, lk)
    sw, _ = vf.corrupt(laid, wrong)
    sent = vf.place_burst(sw, lk) if wrong == "burst" else vf.sh.place(sw, lk)
    last = max(p.land for p in truth + sent)
    return sent, max(n_fix, last + 600), last


@pytest.fixture
def harness(monkeypatch, tmp_path):
    monkeypatch.setattr(vf.stm, "SynthTopModel", _Model)

    def go(*, wrong=None, expect_fail=False, wr_edit=None, i2s_edit=None, mismatch_i2s=False,
           busy=0):
        sent, n, last = _sent("bar808", wrong)
        tail = max(1, n - last)
        exp = _Model().run(None, n)["i2s"]

        def simulate(cmds, outdir, tail_):
            assert tail_ == tail
            wr = [[p.land, *p.w.key(), p.land] for p in sent]
            frames = min(n, max(p.land for p in sent) + tail) - I2S_DRAIN
            i2s = [[k, int(exp[k]) + (1 if mismatch_i2s and k == 7 else 0),
                    int(exp[k]) + (1 if mismatch_i2s and k == 7 else 0), 32, 32]
                   for k in range(frames)]
            if wr_edit:
                wr = wr_edit(wr)
            if i2s_edit:
                i2s = i2s_edit(i2s)
            files = {}
            for name, rows in (("wrs", wr), ("i2s", i2s),
                               ("samp", [[k, int(exp[k])] for k in range(len(i2s))])):
                f = tmp_path / name
                f.write_text("\n".join(" ".join(map(str, r)) for r in rows) + "\n")
                files[name] = str(f)
            files["report"] = [f"busy_at_tick {busy} overrun 0 overflow 0",
                               "strobed 1 missing 0 worst 0"]
            return files

        monkeypatch.setattr(vf.vst, "simulate", simulate)
        monkeypatch.setattr(vf.vst, "RE_BUSY", __import__("re").compile(
            r"busy_at_tick (\d+) overrun (\d+) overflow (\d+)"))
        monkeypatch.setattr(vf.vst, "RE_STRB", __import__("re").compile(
            r"strobed (\d+) missing (\d+) worst (\d+)"))
        argv = ["--outdir", str(tmp_path / "o")]
        if wrong:
            argv += ["--wrong", wrong]
        if expect_fail:
            argv += ["--expect-fail"]
        vf.LAST.clear()
        return vf.main(argv)
    return go


def test_clean_capture_passes(harness):
    assert harness() == 0


def test_surplus_write_is_refused(harness):
    assert harness(wr_edit=lambda w: w + [list(w[-1])]) == 2


def test_short_write_is_refused(harness):
    assert harness(wr_edit=lambda w: w[:-1]) == 2


def test_empty_writes_are_refused(harness):
    assert harness(wr_edit=lambda w: []) == 2


@pytest.mark.parametrize("edit", [lambda w: w[:-1], lambda w: w + [list(w[-1])]])
def test_count_mismatch_with_corrupt_prefix_never_counts_as_caught(harness, edit):
    def both(w):
        w = [list(r) for r in w]
        w[0][1] += 1                                   # corrupt a payload field
        return edit(w)
    assert harness(wr_edit=both, expect_fail=True) == 2


def test_equal_length_corrupt_write_fails_and_is_caught(harness):
    def bad(w):
        w = [list(r) for r in w]
        w[0][1] += 1
        return w
    assert harness(wr_edit=bad) == 1
    assert harness(wr_edit=bad, expect_fail=True) == 0


def test_single_silent_period_is_refused(harness):
    assert harness(i2s_edit=lambda r: [[0, 0, 0, 32, 32]]) == 2


def test_gapped_i2s_is_refused(harness):
    assert harness(i2s_edit=lambda r: r[:100] + r[101:]) == 2


def test_repeated_period_zero_is_refused(harness):
    assert harness(i2s_edit=lambda r: [[0, 0, 0, 32, 32] for _ in r]) == 2


@pytest.mark.parametrize("expect_fail", [False, True])
@pytest.mark.parametrize("row", [["x", 0, 0, 32, 32], [0, 5000, 5000, 32],
                                 [0, 5000, 5000, 32, 32, "x"], [0, 5000, 5000, 32, "x"]])
def test_malformed_i2s_row_is_refused(harness, row, expect_fail):
    assert harness(i2s_edit=lambda r: [row] + r[1:], expect_fail=expect_fail) == 2


def _first_write_flipped(w):
    w = [list(r) for r in w]
    w[0][1] += 1
    return w


@pytest.mark.parametrize("wrong", [None, "burst"])
def test_incomplete_i2s_with_real_mismatch_is_never_caught(harness, wrong):
    """The 0-return reproductions: a flipped write (a real mismatch) kept with only
    period 0 decoded must REFUSE, not be 'caught'."""
    assert harness(wrong=wrong, expect_fail=True, wr_edit=_first_write_flipped,
                   i2s_edit=lambda r: r[:1]) == 2


def test_dropped_write_with_busy_tick_is_refused_not_caught(harness):
    """A dropped last write plus a frame-budget failure in the report: the capture is
    incomplete, so this is a refusal, not a comparison verdict."""
    assert harness(wr_edit=lambda w: w[:-1], expect_fail=True, busy=1) == 2
    assert harness(wr_edit=lambda w: w[:-1], busy=1) == 2


def test_busy_tick_alone_still_fails_and_is_caught(harness):
    assert harness(busy=1) == 1
    assert harness(busy=1, expect_fail=True) == 0


def test_pin_timing_mismatch_with_incomplete_i2s_is_refused(harness):
    def late(w):
        w = [list(r) for r in w]
        w[0][5] += 1
        return w
    assert harness(wr_edit=late, i2s_edit=lambda r: r[:1], expect_fail=True) == 2


def test_wire_mismatch_still_fails_and_is_caught(harness):
    assert harness(mismatch_i2s=True) == 1
    assert harness(mismatch_i2s=True, expect_fail=True) == 0


def test_clean_run_is_not_caught_under_expect_fail(harness):
    assert harness(expect_fail=True) == 1


def test_short_wrong_stream_is_not_falsely_refused(harness):
    """Rule 8: the floor follows the simulated span, so a --wrong stream whose last
    write lands well before n (burst) is judged, not refused."""
    assert harness(wrong="burst", mismatch_i2s=True) == 1
    assert harness(wrong="burst", mismatch_i2s=True, expect_fail=True) == 0

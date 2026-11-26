"""The shared comparator's sample-count contract (issue #1366).

`verify_ladder.compare` is the one comparator behind three bit-exact benches
(ladder, drums, modal). Until #1366 it accepted a matching prefix and silently
dropped surplus samples (`got = got[:n]` before the zip), so a trace with extra
output -- for these benches a capture that is not the declared output at all --
printed PASS and returned 0, and `--expect-fail` accepted a surplus trace as a
caught defect. These tests pin the count contract: one RTL value for every
expected value, status 2 for a count mismatch in either direction, the count
checked before any value, and the diagnostic naming the bench, both counts and
which is larger. No simulator is needed: the comparator runs on temporary
trace files, and the bench entry points are driven through --compare-only with
vector generation stubbed and the real comparator.
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "rtl-sketch"))

import verify_drums                                                      # noqa: E402
import verify_ladder                                                     # noqa: E402
import verify_modal                                                      # noqa: E402


def trace(tmp_path, text):
    p = tmp_path / "rtl_out.txt"
    p.write_text(text)
    return str(p)


def test_all_three_benches_share_the_one_comparator():
    assert verify_ladder.compare is verify_drums.compare is verify_modal.compare


def test_surplus_output_is_a_malformed_capture_not_a_pass(tmp_path, capsys):
    # the #1366 reproduction: a matching prefix plus two surplus samples
    assert verify_ladder.compare([1, 2, 3], trace(tmp_path, "1 2 3 4 5\n")) == 2
    out = capsys.readouterr().out
    assert "verify_ladder" in out
    assert "5" in out and "3" in out          # observed and expected counts
    assert "more" in out and "surplus" in out  # which side is larger, and why
    # a trailing X is still surplus, not an X-value mismatch
    assert verify_ladder.compare([1, 2, 3], trace(tmp_path, "1 2 3 4 x\n")) == 2


def test_short_output_is_still_a_refusal_that_names_both_counts(tmp_path, capsys):
    assert verify_ladder.compare([1, 2, 3], trace(tmp_path, "1\n")) == 2
    out = capsys.readouterr().out
    assert "verify_ladder" in out and "1" in out and "3" in out and "fewer" in out


def test_exact_wrong_and_x_traces_keep_their_statuses(tmp_path):
    assert verify_ladder.compare([1, 2, 3], trace(tmp_path, "1 2 3\n")) == 0
    assert verify_ladder.compare([1, 2, 3], trace(tmp_path, "1 2 9\n")) == 1
    assert verify_ladder.compare([1, 2, 3], trace(tmp_path, "1 2 x\n")) == 1


def test_the_count_takes_precedence_over_values(tmp_path):
    # wrong prefix AND surplus: the count must decide, so this is a malformed
    # capture (2), not a caught arithmetic defect (1) -- --expect-fail reads 1
    # as a caught control, so 1 here would resurrect the false pass
    assert verify_ladder.compare([1, 2, 3], trace(tmp_path, "9 2 3 4 5\n")) == 2


# ---- the bench entry points, driven through --compare-only ------------------
# Vector generation is stubbed (the model runs need no simulator and are not
# under test); the comparator each main() reaches is the real one.


def test_ladder_entry_point_refuses_surplus(tmp_path, monkeypatch):
    monkeypatch.setattr(verify_ladder, "generate",
                        lambda tanh_n, outdir, verbose=True, out_bits=19, nch=1: ([1, 2, 3], None))
    surplus = trace(tmp_path, "1 2 3 4 5\n")
    assert verify_ladder.main(["--compare-only", surplus]) == 2
    # --expect-fail must not accept a surplus trace as a caught defect
    assert verify_ladder.main(["--compare-only", surplus, "--expect-fail"]) == 2


def test_drums_entry_point_refuses_a_surplus_mix_body_pair(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(verify_drums, "generate",
                        lambda outdir, short=False, verbose=True: ([5, 6, 7, 8], 2, None))
    surplus = trace(tmp_path, "5 6 7 8 9 10\n")          # one extra mix/body pair
    assert verify_drums.main(["--compare-only", surplus]) == 2
    out = capsys.readouterr().out
    assert "verify_drums" in out and "6" in out and "4" in out and "more" in out
    assert verify_drums.main(["--compare-only", surplus, "--expect-fail"]) == 2


def test_drums_negative_control_contract_survives(tmp_path, monkeypatch):
    monkeypatch.setattr(verify_drums, "generate",
                        lambda outdir, short=False, verbose=True: ([5, 6, 7, 8], 2, None))
    caught = trace(tmp_path, "5 6 7 9\n")                # equal length, wrong value
    assert verify_drums.main(["--compare-only", caught, "--expect-fail"]) == 0


def test_modal_entry_point_refuses_surplus(tmp_path, monkeypatch):
    monkeypatch.setattr(verify_modal, "generate",
                        lambda outdir, verbose=True: ([1, 2, 3], None))
    surplus = trace(tmp_path, "1 2 3 4\n")
    assert verify_modal.main(["--compare-only", surplus]) == 2
    assert verify_modal.main(["--compare-only", surplus, "--expect-fail"]) == 2

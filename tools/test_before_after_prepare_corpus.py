"""False-green cases for the two-SHA driver (#700), in the style of test_run_all."""
from __future__ import annotations
import os, sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import before_after_prepare_corpus as ba                         # noqa: E402
import run_all as ra                                             # noqa: E402


def res(state, rc, out=""):
    return {"state": state, "rc": rc, "out": out, "cmd": "x", "secs": 0.0}


def test_corpus_absent_is_refused_not_skipped(tmp_path):
    with pytest.raises(ba.Refused):
        ba.corpus_manifest(tmp_path / "nope")
    (tmp_path / "empty").mkdir()
    with pytest.raises(ba.Refused):
        ba.corpus_manifest(tmp_path / "empty")


def test_main_exits_2_when_corpus_absent(tmp_path, capsys):
    assert ba.main(["--refs", str(tmp_path / "nope")]) == 2
    assert "REFUSED" in capsys.readouterr().err


def test_manifest_changes_when_a_file_changes(tmp_path):
    (tmp_path / "a.wav").write_bytes(b"1")
    m1 = ba.corpus_manifest(tmp_path)
    (tmp_path / "a.wav").write_bytes(b"2")
    assert ba.corpus_manifest(tmp_path) != m1


def test_exit_2_is_refused_not_failed_and_exit_1_is_failed():
    assert ba.classify(res(ra.NO_VERDICT, 2)) == ba.REFUSED
    assert ba.classify(res(ra.FAIL, 1)) == ba.FAILED
    assert ba.classify(res(ra.NO_VERDICT, None)) == ba.REFUSED   # timeout
    assert ba.classify(res(ra.PASS, 0)) == ba.OK


def test_before_control_requires_headline_from_a_job_that_ran():
    ba.before_control({"j": res(ra.PASS, 0, "spread 6.07 dB")})
    with pytest.raises(ba.Refused):
        ba.before_control({"j": res(ra.PASS, 0, "spread 5.9 dB")})
    # headline printed by a job that did NOT run proves nothing
    with pytest.raises(ba.Refused):
        ba.before_control({"j": res(ra.NO_VERDICT, 2, "6.07")})
    # substring of another number must not satisfy it
    with pytest.raises(ba.Refused):
        ba.before_control({"j": res(ra.PASS, 0, "16.075 dB")})

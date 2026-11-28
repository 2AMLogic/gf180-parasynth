"""Controls for tools/envelope_history_337.py (rule 2, rule 8, start-red)."""
import errno, json, math, os, sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent))
import envelope_history_337 as eh  # noqa: E402

SR = 48000


def tone(on_s, amp_pre, amp_held=0.2, total=1.0, f=1046.5):
    t = np.arange(int(total * SR)) / SR
    x = np.sin(2 * np.pi * f * t)
    a = np.where(t < on_s, amp_pre, amp_held)
    return a * x


def test_known_residual_reads_minus_20_db():
    x = tone(0.5, 0.02)          # pre-onset 20 dB below held
    r, floored = eh.residual_db(x, SR, 0.5, 0.9)
    assert abs(r - (-20.0)) < 0.1 and not floored


def test_digital_zero_is_floor_limited_not_minus_inf():
    r, floored = eh.residual_db(tone(0.5, 0.0), SR, 0.5, 0.9)
    assert r == eh.FLOOR_DB and floored


def test_nan_refuses():
    x = tone(0.5, 0.02)
    x[1000] = np.nan
    with pytest.raises(eh.Refused):
        eh.residual_db(x, SR, 0.5, 0.9)


def test_window_reaching_the_onset_refuses():
    with pytest.raises(eh.Refused):
        eh.residual_db(tone(0.5, 0.02), SR, 0.5, 0.9, end_before_on_s=0.0)


def test_too_little_lead_in_refuses():
    with pytest.raises(eh.Refused):
        eh.residual_db(tone(0.03, 0.0), SR, 0.03, 0.9)


def test_silent_held_level_refuses():
    with pytest.raises(eh.Refused):
        eh.residual_db(np.zeros(SR), SR, 0.5, 0.9)


def test_wrong_onset_moves_the_number_start_red():
    """A stub with the onset index 40 ms late must NOT read the true value."""
    x = tone(0.5, 0.0)
    good, _ = eh.residual_db(x, SR, 0.5, 0.9)
    bad, _ = eh.residual_db(x, SR, 0.54, 0.94)
    assert good == eh.FLOOR_DB and bad > -30.0


def test_gate_rejects_nonfinite_and_short_groups():
    rows = [{"wave": "saw", "condition": c, "r_db": -10.0}
            for c in eh.HISTORY for _ in range(3)]
    with pytest.raises(eh.Refused):
        eh.gate(rows)                      # no-history group missing
    rows += [{"wave": "saw", "condition": c, "r_db": float("nan")}
             for c in eh.NO_HISTORY for _ in range(3)]
    with pytest.raises(eh.Refused):
        eh.gate(rows)


def test_gate_viable_only_when_history_has_level_and_isolated_does_not():
    def rows(h, n):
        return ([{"wave": "saw", "condition": c, "r_db": h} for c in eh.HISTORY for _ in range(3)]
                + [{"wave": "saw", "condition": c, "r_db": n} for c in eh.NO_HISTORY for _ in range(3)])
    assert eh.verdict(eh.gate(rows(-10.0, -80.0))) == "VIABLE"
    assert eh.verdict(eh.gate(rows(-10.0, -15.0))) == "REFUTED"     # no contrast
    assert eh.verdict(eh.gate(rows(-120.0, -120.0))) == "REFUTED"


def test_all_silent_file_is_refused_not_refuted():
    """Rule 8: the defeating input. A silent file satisfies 'residual below
    -30 dB' trivially; it must refuse (no held level), not read as REFUTED."""
    with pytest.raises(eh.Refused):
        eh.residual_db(np.zeros(2 * SR), SR, 1.0, 1.5)


def test_positive_control_on_real_audio_sees_the_previous_release():
    """The instrument can see a residual that exists: in the frozen repeat
    render, a window inside the earlier note's release must read far above the floor."""
    path = eh.SRC / "saw-repeat84_gap3p4-0.wav"
    if not path.exists():
        pytest.skip("frozen audio absent")
    from scipy.io import wavfile
    sr, x = wavfile.read(path)
    x = x.astype(np.float64)
    # pretend a note starts at 1.0 s, inside the earlier note's release: the
    # 40 ms before it holds the louder, earlier part of that release.
    r, floored = eh.residual_db(x, sr, 1.0, 1.5, end_before_on_s=0.005)
    assert not floored and r > -10.0


def test_hash_mismatch_refuses(tmp_path):
    import json, shutil
    for n in ("report.json",):
        shutil.copy(eh.SRC / n, tmp_path / n)
    rep = json.loads((tmp_path / "report.json").read_text())
    for row in rep["renders"]:
        shutil.copy(eh.SRC / row["wav"], tmp_path / row["wav"])
        break
    (tmp_path / rep["renders"][0]["wav"]).write_bytes(b"x" + (tmp_path / rep["renders"][0]["wav"]).read_bytes())
    with pytest.raises(eh.Refused):
        eh.measure(tmp_path)


# ---------------------------------------------------------------------------
# the CLI's --out destination (#2098, a slice of the #2095 class)
#
# THE DEFECT, red-first on origin/main @ 8eb04e5b (2026-11-28): `main` wrote
# `a.out` with `write_text` after the verdict had been computed and printed,
# and nothing made the parent directory. Under a missing one the write raised
# FileNotFoundError out of `main`: exit 1 with a traceback, for a #337
# verdict that had just printed VIABLE, with the record lost.
#
# THE KNOWN ANSWER, from tools/exit_contract.py and not from the module under
# test: a destination that cannot be written is a precondition that was not
# met, so REFUSED (2) whichever way the verdict went; refused before the
# frozen audio is read where the path says so itself.
#
# WHAT RUNS FOR REAL: `main`, `gate`, `verdict` and the summary. Doubled
# (counted): `measure`, which in production reads the hash-bound reference
# renders. The rows it returns are the ones `test_gate_viable_...` builds.
# ---------------------------------------------------------------------------
def _rows(h, n):
    row = lambda c, r_db: {"wave": "saw", "condition": c, "r_db": r_db, "attack_5ms": 1.0,
                           "attack_1ms": 0.5, "zero_run_before_on_s": 0.25,
                           "floor_limited": False}
    return ([row(c, h) for c in eh.HISTORY for _ in range(3)]
            + [row(c, n) for c in eh.NO_HISTORY for _ in range(3)])


@pytest.fixture
def measured(monkeypatch):
    calls = []
    monkeypatch.setattr(eh, "measure", lambda *a, **k: calls.append(1) or _rows(-10.0, -80.0))
    return calls


def _full_disk(target):
    """`Path.write_text` that fails for `target` only."""
    real = Path.write_text

    def write_text(self, data, *args, **kwargs):
        if self == target:
            raise OSError(errno.ENOSPC, "No space left on device")
        return real(self, data, *args, **kwargs)

    return write_text


@pytest.mark.parametrize("target, reason", [
    ("{tmp}", "names an existing directory; name the record file"),
    ("{tmp}/a-file/history-337.json", "no record can be written there: [Errno 17] File exists"),
    ("nul\x00dir/history-337.json", "no record can be written there: embedded null byte"),
], ids=["a-directory", "parent-is-a-file", "parent-has-a-nul-byte"])
def test_an_unwritable_out_destination_is_refused_before_anything_is_read(measured, tmp_path, capsys,
                                                                          target, reason):
    (tmp_path / "a-file").write_text("not a directory\n")
    rc = eh.main(["--out", target.format(tmp=tmp_path)])
    out, _ = capsys.readouterr()
    assert rc == 2, (rc, out)
    assert "REFUSED" in out and reason in out, out
    assert measured == [], "the renders were read for a destination that could not be written"
    assert "VERDICT" not in out, out


def test_an_out_record_under_a_missing_directory_is_created_and_written(measured, tmp_path, capsys):
    target = tmp_path / "no-such-dir" / "history-337.json"
    rc = eh.main(["--out", str(target)])
    out, _ = capsys.readouterr()
    assert rc == 0, out
    assert measured == [1] and "VERDICT: VIABLE" in out, out
    record = json.loads(target.read_text())
    assert record["verdict"] == "VIABLE" and len(record["rows"]) == 18
    assert record["per_wave"]["saw"]["median_history_db"] == -10.0


def test_a_write_failure_after_the_verdict_is_refused_with_it_kept(measured, tmp_path, capsys,
                                                                   monkeypatch):
    target = tmp_path / "history-337.json"
    monkeypatch.setattr(Path, "write_text", _full_disk(target))
    rc = eh.main(["--out", str(target)])
    out, _ = capsys.readouterr()
    assert rc == 2, (rc, out)
    assert "VERDICT: VIABLE" in out, out
    assert "REFUSED" in out and "not written" in out, out
    assert "No space left on device" in out and "Traceback" not in out, out
    assert not target.exists()


def test_a_refused_write_is_not_a_refutation_when_the_verdict_was_refuted(measured, tmp_path, capsys,
                                                                          monkeypatch):
    """The fourth corner. REFUTED is the comparison's own verdict and exits 0
    either way here, because this CLI's 0 means "answered", not "viable"; the
    record is the answer, so with it unwritten the exit is REFUSED."""
    monkeypatch.setattr(eh, "measure", lambda *a, **k: _rows(-120.0, -120.0))
    target = tmp_path / "history-337.json"
    monkeypatch.setattr(Path, "write_text", _full_disk(target))
    rc = eh.main(["--out", str(target)])
    out, _ = capsys.readouterr()
    assert rc == 2, (rc, out)
    assert "VERDICT: REFUTED" in out and "not written" in out, out
    assert not target.exists()


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0,
                    reason="root writes into a read-only directory")
def test_a_read_only_parent_is_refused_and_leaves_no_record(measured, tmp_path, capsys):
    """Rule 8: the input that defeats the pre-flight. The parent exists, so
    `mkdir(exist_ok=True)` accepts it; only the guard around the write can
    refuse it."""
    parent = tmp_path / "read-only"
    parent.mkdir()
    parent.chmod(0o555)
    try:
        rc = eh.main(["--out", str(parent / "history-337.json")])
        written = list(parent.iterdir())
    finally:
        parent.chmod(0o700)
    out, _ = capsys.readouterr()
    assert rc == 2, (rc, out)
    assert "REFUSED" in out and "not written" in out, out
    assert "VERDICT: VIABLE" in out, out
    assert written == []

"""Independent controls for causal rate-converter measurement instrumentation."""

import json
import pathlib
import subprocess
import sys

import numpy as np
import pytest

import measure_m5a_filter_headroom as headroom


def test_control_latency_diagnostic_requires_and_measures_the_injected_step():
    result = headroom._measure_control_response()
    assert result["step_frame"] == 100
    assert 0 <= result["first_changed_latency_frames"] <= 10
    quantiles = result["squared_response_energy_quantiles_frames_from_start"]
    assert quantiles["0.1"] <= quantiles["0.5"] <= quantiles["0.9"]
    assert quantiles["0.9"] < 512


@pytest.mark.parametrize("value", [10**400, None, "not-a-number"],
                         ids=["beyond-float", "null", "non-numeric-string"])
def test_recompare_report_refuses_a_partial_that_float_cannot_read(tmp_path, capsys, value):
    """#1680, the second shipped path: `--recompare-report` hands JSON-loaded
    rows straight to `_compare_incremental`, with no record gate in front, so
    its unguarded `float()` left as a traceback -- exit 1 -- here too. The
    reason is asserted because any refusal would give the exit code."""
    shipped = headroom.ROOT / "docs/scorecard/mono-m5a-miniv3/filter-headroom-v1.json"
    report = json.loads(shipped.read_text())
    row = report["configurations"]["pulse29"]["headroom"]
    row["event_diagnostics"][0]["harmonic_error_db_model_minus_reference"]["h2"] = value
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report))

    assert headroom.main(["--recompare-report", str(path)]) == 2

    assert ("REFUSED: candidate saw/84 harmonic partial h2 cannot be read as a float"
            in capsys.readouterr().out)
    assert path.read_text() == json.dumps(report)


# ---- #1901: an estimator refusal out of run() -> m5a.measure -----------------
#
# `audio_measure.InsufficientEvidence` subclasses AssertionError, not
# `refusal.Refused`, so `main`'s `except refusal.Refused` could not see an
# estimator refusing, and it left as exit 1 -- a refusal rendered as FAIL
# (tools/exit_contract.py).
#
# RED-FIRST on main @ dd0449e0 (2026-11-28), through the real `__main__` with a
# broken stage tap (`VoiceFx.play` wrapped to zero `trace["ladder"]` after the
# real render, the output left a valid note): exit 1 and the traceback
# `audio_measure.InsufficientEvidence: harmonic_signature: silent` raised at
# model/audio_measure.py:2379 via mono_m5a_score._stage_diagnostics:219 <-
# measure:491 <- measure_m5a_filter_headroom.run:156 <- main:245. A wholly
# silent output is NOT this path: `refine_f0` stops it at mono_m5a_score.py:458
# as `mono_m5a_score.Refused`, which the old handler already caught (pinned in
# tools/test_insufficient_evidence_dispatch.py). That run costs 13.2 s for the
# one 13.5 s segment rendered before the first event's estimators, so the
# controls below keep the real `_stage_diagnostics` in a direct check and stub
# the `m5a.measure` seam for the `main` / `__main__` dispatch.

ESTIMATOR_REASON = "harmonic_signature: silent"
PLAIN_FAULT = "unrelated measure fault"


def test_insufficient_evidence_is_an_assertion_error_not_a_refused():
    """The class relation the widened handler rests on: the estimator's own
    refusal is invisible to `except refusal.Refused`, and naming
    `AssertionError` instead would catch every implementation bug."""
    import audio_measure as am
    import refusal

    assert issubclass(am.InsufficientEvidence, AssertionError)
    assert not issubclass(am.InsufficientEvidence, refusal.Refused)
    assert issubclass(headroom.m5a.Refused, refusal.Refused)


def test_broken_stage_tap_raises_insufficient_evidence_out_of_stage_diagnostics():
    """The raise origin is real and unguarded: with a valid note at the output
    and one silent stage trace, the real `_stage_diagnostics` hands
    `harmonic_signature` zeros and it raises the estimator's own class -- not
    `Refused` -- so no `refusal.Refused` handler can see it."""
    import audio_measure as am
    import voice_fx as vf

    f0 = vf.note_hz(84)
    t = np.arange(int(0.4 * vf.SR)) / vf.SR
    tone = (0.2 * 32768 * np.sin(2 * np.pi * f0 * t)).astype(np.int16)
    trace = {"osc": [tone.astype(np.int64)], "mixed": tone.astype(np.int64),
             "ladder": np.zeros(len(tone), dtype=np.int64)}

    with pytest.raises(am.InsufficientEvidence, match=ESTIMATOR_REASON):
        headroom.m5a._stage_diagnostics(trace, tone, 0, len(tone), f0)


def _measure_raising(exc):
    def measure(**kwargs):
        raise exc
    return measure


def test_insufficient_evidence_from_measure_is_refused_2_and_writes_no_report(
        tmp_path, capsys, monkeypatch):
    import audio_measure as am

    monkeypatch.setattr(headroom.m5a, "measure",
                        _measure_raising(am.InsufficientEvidence(ESTIMATOR_REASON)))
    out = tmp_path / "refused.json"

    assert headroom.main(["--out", str(out)]) == 2

    assert f"REFUSED: {ESTIMATOR_REASON}" in capsys.readouterr().out.splitlines()
    assert not out.exists()


def test_plain_assertion_error_from_measure_is_not_a_refusal(tmp_path, capsys, monkeypatch):
    """The inverse defect (tools/refusal.py): an ordinary AssertionError at the
    same seam must not be relabelled REFUSED."""
    monkeypatch.setattr(headroom.m5a, "measure", _measure_raising(AssertionError(PLAIN_FAULT)))
    out = tmp_path / "refused.json"

    with pytest.raises(AssertionError, match=PLAIN_FAULT):
        headroom.main(["--out", str(out)])

    assert "REFUSED" not in capsys.readouterr().out
    assert not out.exists()


def _cli(exc: str, out: pathlib.Path) -> subprocess.CompletedProcess:
    """The real `__main__` with `m5a.measure` raising `exc` at the seam: what a
    batch runner sees, exit status and all."""
    seam = ("import sys\n"
            f"sys.path[:0] = {[str(headroom.ROOT / p) for p in ('model', 'tools')]!r}\n"
            "import mono_m5a_score as m5a, audio_measure as am\n"
            "def _measure(**kwargs):\n"
            f"    raise {exc}\n"
            "m5a.measure = _measure\n")
    src = (f"{seam}import runpy\n"
           f"runpy.run_path({str(headroom.ROOT / 'tools/measure_m5a_filter_headroom.py')!r},"
           " run_name='__main__')\n")
    return subprocess.run([sys.executable, "-c", src, "--out", str(out)],
                          capture_output=True, text=True, timeout=120, cwd=str(headroom.ROOT))


def test_insufficient_evidence_exits_refused_2_through_main_entry(tmp_path):
    out = tmp_path / "refused.json"

    proc = _cli(f"am.InsufficientEvidence({ESTIMATOR_REASON!r})", out)

    assert proc.returncode == 2, (proc.returncode, proc.stdout[-400:], proc.stderr[-600:])
    assert f"REFUSED: {ESTIMATOR_REASON}" in proc.stdout.splitlines(), proc.stdout[-400:]
    assert "Traceback" not in proc.stdout + proc.stderr, proc.stderr[-600:]
    assert not out.exists()


def test_plain_assertion_error_still_exits_1_through_main_entry(tmp_path):
    out = tmp_path / "refused.json"

    proc = _cli(f"AssertionError({PLAIN_FAULT!r})", out)

    assert proc.returncode == 1, (proc.returncode, proc.stdout[-400:], proc.stderr[-600:])
    assert "Traceback" in proc.stderr and f"AssertionError: {PLAIN_FAULT}" in proc.stderr
    assert "REFUSED" not in proc.stdout, "an AssertionError must not be relabelled a refusal"
    assert not out.exists()

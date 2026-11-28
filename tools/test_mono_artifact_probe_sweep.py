"""Sweep exit status for tools/mono_artifact_probe.py: a sweep whose record
holds a row with no output number is no verdict (#1796).

Before the fix `sweep` printed `None` where a low note's numbers belong, wrote
the record and exited 0, so a shard with a refusing row rendered PASS under
tools/run_all.py. Every test here drives the real main(["sweep", ...]) over
the real run_sweep; only the schedule is replaced, in-process, with a handful
of points. The record format is not what moves (#1770 settled that
measure_point keeps stamping MEASURED on a row whose stage refused): the exit
status is.

The refusals are never mocked: note 12 is 16.35 Hz, 12.3 bins of the
36,000-sample window, under MIN_BINS_PER_F0, so every stage of the real voice
refuses; and q = 1.0 is past the self-oscillation boundary render_held refuses.
"""
from __future__ import annotations

import gzip
import json
import pathlib
import sys
import textwrap

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import exit_contract  # noqa: E402
import mono_artifact_probe as p  # noqa: E402
import run_all  # noqa: E402

LOW_NOTE = 12                   # 16.35 Hz: under MIN_BINS_PER_F0, every stage refuses
SAW = dict(waves=("saw",) * 3)


def _line(n, m, named):
    verb = "holds" if n == 1 else "hold"
    return (f"REFUSED: {n} of {m} rows {verb} no output number, "
            f"so this sweep is NO-VERDICT, not a pass: {named}")


def _sweep(tmp_path, capsys, monkeypatch, *points):
    """The real sweep CLI over `points` -- (engine, note, patch overrides) --
    returning (exit status, the record's rows, stdout lines)."""
    monkeypatch.setitem(p.SCHEDULES, "standard", lambda: [
        ("grid", engine, note, {**SAW, **over}) for engine, note, over in points])
    out = tmp_path / "sweep.json"
    rc = p.main(["sweep", "--out", str(out)])
    return rc, json.loads(out.read_text())["rows"], capsys.readouterr().out.splitlines()


def test_sweep_over_a_low_note_exits_refused_and_keeps_the_record(tmp_path, capsys, monkeypatch):
    """The issue's reproduction: one real note beside one the window cannot resolve."""
    rc, rows, lines = _sweep(tmp_path, capsys, monkeypatch, ("r1", 24, {}), ("r1", LOW_NOTE, {}))
    good, low = rows
    assert good["verdict"] == "MEASURED" and not any("refused" in s for s in good["stages"].values())
    assert low["verdict"] == "MEASURED" and all("refused" in s for s in low["stages"].values())
    assert rc == exit_contract.REFUSED, f"sweep exited {rc} on a record that holds a row with no output number"
    assert [line for line in lines if line.startswith("REFUSED:")] == [
        _line(1, 2, f"r1 note {LOW_NOTE}: output REFUSED: {low['stages']['output']['refused']}")]
    assert lines[-1] == f"wrote {tmp_path / 'sweep.json'} (exit {exit_contract.REFUSED})"
    # the row's own line is unchanged: None where its numbers belong, as before
    assert lines[1].endswith("MEASURED      unwanted None dBFS rel None dB")


def test_sweep_over_a_whole_row_refusal_exits_refused(tmp_path, capsys, monkeypatch):
    """The other shape _output_status knows: run_sweep's own handler wrote a row
    with verdict REFUSED and no stages at all (q = 1.0 is outside the domain)."""
    rc, rows, lines = _sweep(tmp_path, capsys, monkeypatch, ("r1", 24, {}), ("r1", 24, dict(q=1.0)))
    bad = rows[1]
    assert bad["verdict"] == "REFUSED" and "stages" not in bad
    assert rc == exit_contract.REFUSED, f"sweep exited {rc} on a record that holds a REFUSED row"
    assert [line for line in lines if line.startswith("REFUSED:")] == [
        _line(1, 2, f"r1 note 24: {bad['reason']}")]
    assert lines[-1] == f"wrote {tmp_path / 'sweep.json'} (exit {exit_contract.REFUSED})"


def test_sweep_over_resolved_notes_exits_zero_and_prints_nothing_more(tmp_path, capsys, monkeypatch):
    """The clean control through the same path (notes >= 24): one line per row
    and the `wrote` line, exactly as before, and no REFUSED line."""
    rc, rows, lines = _sweep(tmp_path, capsys, monkeypatch, ("r1", 24, {}), ("r1", 36, {}))
    assert all(p._output_status(r) == "MEASURED" for r in rows)
    assert not any("refused" in s for r in rows for s in r["stages"].values())
    assert rc == exit_contract.PASS
    assert not [line for line in lines if line.startswith("REFUSED:")]
    assert len(lines) == len(rows) + 1
    assert lines[-1] == f"wrote {tmp_path / 'sweep.json'} (exit {exit_contract.PASS})"


def test_a_refusing_shard_is_no_verdict_under_run_all(tmp_path):
    """The failure as reported: two shards, one holds the low note. run_all
    rendered both PASS before; the refusing shard is NO-VERDICT and the clean
    one still PASS, and each shard's record is still written."""
    driver = tmp_path / "shard.py"
    driver.write_text(textwrap.dedent(f"""
        import pathlib, sys
        root = {str(p.ROOT)!r}
        sys.path[:0] = [root + "/model", root + "/tools"]
        import mono_artifact_probe as p
        p.SCHEDULES["standard"] = lambda: [
            ("grid", "r1", 24, dict(waves=("saw",) * 3)),
            ("grid", "r1", 12, dict(waves=("saw",) * 3)),
        ]
        raise SystemExit(p.main(["sweep", "--part", sys.argv[1], "--parts", "2", "--out", sys.argv[2]]))
        """))
    results = [run_all.run_one(f'"{sys.executable}" "{driver}" {part} "{tmp_path / f"part{part}.json"}"')
               for part in (0, 1)]
    assert [(r["state"], r["rc"]) for r in results] == [
        (run_all.PASS, exit_contract.PASS), (run_all.NO_VERDICT, exit_contract.REFUSED)]
    assert "REFUSED: 1 of 1 rows holds" in results[1]["out"]
    assert json.loads((tmp_path / "part1.json").read_text())["rows"][0]["note"] == LOW_NOTE


def test_refusal_line_counts_every_shape_output_status_refuses():
    """A FAIL-ACTIVITY row holds no number too (a silent voice is not a result),
    and a sweep that refuses everywhere names its first rows, not all of them."""

    def row(engine, note, verdict, **extra):
        return {"engine": engine, "note": note, "verdict": verdict, **extra}

    measured = row("r1", 36, "MEASURED", stages={"output": {"unwanted_dbfs": -80.0}})
    low = row("r1", LOW_NOTE, "MEASURED", stages={"output": {"refused": "12.3 bins"}})
    assert p._sweep_refusal_line([measured]) is None
    assert p._sweep_refusal_line([measured, low]) == _line(1, 2, "r1 note 12: output REFUSED: 12.3 bins")
    rows = [row("r1", 24, "FAIL-ACTIVITY"), measured, low] + [
        row("pulse2x", n, "REFUSED", reason="window runs past the render") for n in range(48, 53)]
    why = "window runs past the render"
    assert p._sweep_refusal_line(rows) == _line(7, 8, (
        f"r1 note 24: FAIL-ACTIVITY; r1 note 12: output REFUSED: 12.3 bins; "
        f"pulse2x note 48: {why}; pulse2x note 49: {why}; pulse2x note 50: {why}; and 2 more"))


# the archived standard and fresh sweeps under docs/scorecard/m5-artifacts-333,
# each in two shards, with the row counts the issue records for them
ARCHIVED_SWEEPS = {"probe/sweep2": 310, "probe-rect074/sweep": 310, "pulse-level-repair/fresh": 224,
                   "pulse-level-repair/sdrv": 310, "r2-compare/s074": 310}


@pytest.mark.parametrize("rel", sorted(ARCHIVED_SWEEPS))
def test_the_real_sweeps_hold_no_row_the_line_refuses(rel):
    """The gate is satisfiable: every row of the archived sweeps measures (their
    schedules use notes >= 24), so the real schedules do not trip the line."""
    archive = p.ROOT / "docs/scorecard/m5-artifacts-333"
    rows = []
    for part in (0, 1):
        record = gzip.decompress((archive / f"{rel}-{part}.json.gz").read_bytes())
        rows += json.loads(record)["rows"]
    assert len(rows) == ARCHIVED_SWEEPS[rel]
    assert all(p._output_status(r) == "MEASURED" for r in rows)
    assert p._sweep_refusal_line(rows) is None

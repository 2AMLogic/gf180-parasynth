"""Known line shapes distinguish D10A/D13A's two reported residuals."""

import re

import balance_line_shape as probe
import numpy as np
import pytest


def line_shape(n_bins, floor_power):
    # 10 ms and -10 dB (power 0.1) are both exact points on the fit grids.
    offsets = np.repeat(np.arange(1, n_bins + 1) / 0.06, 2)
    lorentzian_power = 1 / (1 + (2 * np.pi * 0.01 * offsets) ** 2)
    return offsets, -10 * np.log10(lorentzian_power + floor_power)


def test_candidate_table_identifies_a_known_pedestal(monkeypatch):
    offsets, drops = line_shape(6, floor_power=0.1)
    reference = probe.PT._lorentzian_fit(offsets, drops, pedestal=True)
    assert reference["ok"] and reference["pedestal_db"] == -10
    assert reference["resid_db"] == pytest.approx(0, abs=1e-10)

    monkeypatch.setattr(
        probe.PT, "line_offsets", lambda *args, **kwargs: (1.0, offsets, drops)
    )
    values, tau, shipped = probe.row(None, probe.SR, 460.0, 0.06)
    assert values["floorless dB"] == pytest.approx(1.3566613)
    assert values["fit resid dB"] == pytest.approx(0, abs=1e-10)
    displayed = " ".join(probe.fmt(values, tau, shipped).split())
    assert "floorless dB 1.36" in displayed
    assert "fit resid dB 0.00" in displayed


def test_sweep_reports_both_models_and_ranks_by_floorless_ratio(monkeypatch, capsys):
    # An equal and opposite error on the two sides leaves the best curve
    # unchanged and adds that error in quadrature to its RMS residual.
    record = object()
    op = probe.RC.RS_BALANCE_OP
    monkeypatch.setattr(probe, "OPS", {"RS (D10A)": op})
    monkeypatch.setattr(
        probe,
        "_real_records",
        lambda path: [("RS (D10A)", op, "fixture", record, probe.SR)],
    )
    monkeypatch.setattr(probe.PT, "find_partial", lambda *args, **kwargs: 460.0)

    def offsets_for(x, sr, f, *, seconds, n_bins):
        offsets, drops = line_shape(n_bins, floor_power=0.1 if x is record else 0.0)
        sides = np.tile([-1, 1], n_bins)
        # At wider bins the noise fixture becomes less symmetric. The two
        # models should consequently prefer different widths.
        error = (
            0.5 if x is record else np.repeat(np.where(np.arange(n_bins) < 6, 1, 2), 2)
        )
        return 1.0, offsets, drops + sides * error

    monkeypatch.setattr(probe.PT, "line_offsets", offsets_for)
    assert probe.cmd_sweep(["1"]) == 0
    lines = capsys.readouterr().out.splitlines()
    header = next(line for line in lines if line.lstrip().startswith("n_bins "))
    columns = (
        "rec resid max",
        "noi resid min",
        "gap",
        "ped ratio",
        "rec f-less",
        "noi f-less",
        "f-less ratio",
    )
    rows = {}
    for line in lines:
        if re.match(r"^\s+\d+\s", line):
            width, *values = map(float, line.split())
            rows[int(width)] = dict(zip(columns, values, strict=True))

    # The six-bin row uses the very same pedestal as the candidate table.
    six = rows[6]
    assert six["rec f-less"] == pytest.approx(np.hypot(1.3566613, 0.5), abs=0.005)
    assert six["rec resid max"] == 0.50
    assert six["noi resid min"] == six["noi f-less"] == 1.0
    assert " ".join(header.split()) == " ".join(("n_bins", *columns))

    # Derive the winners from the residuals as well as from the displayed
    # ratios. Swapping the two ratios and ranking on the swapped column fails.
    by_floorless = max(
        rows, key=lambda k: rows[k]["noi f-less"] / rows[k]["rec f-less"]
    )
    by_pedestal = max(
        rows, key=lambda k: rows[k]["noi resid min"] / rows[k]["rec resid max"]
    )
    assert by_floorless != by_pedestal
    assert max(rows, key=lambda k: rows[k]["f-less ratio"]) == by_floorless
    assert max(rows, key=lambda k: rows[k]["ped ratio"]) == by_pedestal
    summary = f"n_bins={by_floorless} (noise min / records max = {rows[by_floorless]['f-less ratio']:.2f}x)"
    reported_winner = next(line for line in lines if "n_bins=" in line)
    assert summary in reported_winner


@pytest.mark.parametrize("column", ["floorless dB", "fit resid dB"])
def test_width_sets_the_minimum_number_of_finite_offsets(column):
    offsets, drops = line_shape(4, floor_power=0.1)
    drops[4:] = np.inf
    rows = [("line", 0.06, offsets, drops)]
    groups = {"records": rows, "noise": rows}
    # Four finite offsets suffice at width 3. Width 4 requires six.
    assert np.isfinite(probe._width_stats(groups, 3, column)[0])
    assert np.isinf(probe._width_stats(groups, 4, column)[0])

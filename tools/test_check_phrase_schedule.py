"""Known-answer logs, refusals and early-landing controls for the M5 phrase
schedule check (#372).

Every expected frame here is written out by hand from the frozen manifests'
own numbers (`offset_samples`, `on_s`, `gate_s`) and the I2S contract's one
period of wire delay -- never from the repaired scheduler -- so the scheduler
cannot pass by agreeing with itself.

The historical defect is reinstated twice: as the exact offsets the published
M5 phrases landed at (`docs/scorecard/m5-artifacts-333/saw-drive/confirm/
schedule-M5A.json`: GATE_ON 0, -11, -16, -27 frames, first event aligned), and
through the scheduler's own `legacy-relative` mode, which must be rejected
when its writes are placed at the frames the bench's link puts them in.
"""
from __future__ import annotations

import copy
import json
import os
import pathlib
import sys

import pytest

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import check_phrase_schedule as cps                                   # noqa: E402

M5A = ROOT / "docs/scorecard/mono-m5a-miniv3/manifest.json"
M5B = ROOT / "docs/scorecard/mono-m5b-miniv3/manifest.json"

# Hand-derived from M5A's manifest: segment offsets 0 and 657556 samples
# (647956 + 9600 of silence), events at 0.1 s and 5.7 s for 0.6 s, minus the
# single period i2s_tx delays every sample (contract 13, D = 1).
M5A_ON = [4_799, 273_599, 662_355, 931_155]
M5A_OFF = [33_599, 302_399, 691_155, 959_955]
# M5B: offsets 0 and 369556, events at 0.1 s and 4.1 s for 0.6 s.
M5B_ON = [4_799, 196_799, 374_355, 566_355]
M5B_OFF = [33_599, 225_599, 403_155, 595_155]

A_ON, A_OFF, A_INC = 0x20, 0x21, 0x30


def _rows(on, off, *, image=40):
    """A bench write log: an image, then per event the three increments, a
    track write and the gate, then the gate-off. Applied == predicted."""
    rows = [[1 + k, 0, 0, 0x01, 0, 1 + k] for k in range(image)]
    for f_on, f_off in zip(on, off):
        for k in range(4):
            f = f_on - 4 + k
            rows.append([f, 1, 0, A_INC, 7, f])
        rows.append([f_on, 0, 0, A_ON, 0, f_on])
        rows.append([f_off, 0, 0, A_OFF, 0, f_off])
    return rows


def _write(tmp_path, rows, name="run"):
    d = tmp_path / name
    d.mkdir()
    (d / "top_wrs_VOICE_OSC_2X.txt").write_text(
        "".join(" ".join(str(x) for x in r) + "\n" for r in rows))
    return d


def _run(tmp_path, rows, manifest=M5A, *extra):
    d = _write(tmp_path, rows)
    return cps.main(["--manifest", str(manifest), f"run={d}", *extra])


# ---- the targets, by hand -------------------------------------------------

def test_targets_come_from_the_scorers_timing_map_not_the_cursor():
    t = cps.targets(json.loads(M5A.read_text()))
    assert [e["on_frame"] for e in t] == M5A_ON
    assert [e["off_frame"] for e in t] == M5A_OFF
    t = cps.targets(json.loads(M5B.read_text()))
    assert [e["on_frame"] for e in t] == M5B_ON
    assert [e["off_frame"] for e in t] == M5B_OFF


def test_rounding_boundary_is_round_to_nearest_frame():
    m = json.loads(M5A.read_text())
    m["timeline"]["segments"][0]["midi_events"][0]["on_s"] = 0.1 + 0.49 / 48_000
    assert cps.targets(m)[0]["on_frame"] == 4_799
    m["timeline"]["segments"][0]["midi_events"][0]["on_s"] = 0.1 + 0.51 / 48_000
    assert cps.targets(m)[0]["on_frame"] == 4_800


@pytest.mark.parametrize("mutate", [
    lambda m: m["timeline"]["segments"][1].__setitem__("offset_samples", 657_600),
    lambda m: m["timeline"]["segments"][0].__setitem__("offset_samples", 5),
    lambda m: m["timeline"]["segments"][0]["midi_events"][1].__setitem__("on_s", 0.5),
    lambda m: m["timeline"]["segments"][0]["midi_events"][1].__setitem__("gate_s", 20.0),
    lambda m: m["timeline"]["segments"][0]["midi_events"][0].__setitem__("on_s", float("nan")),
    lambda m: m["timeline"].__setitem__("segments", []),
])
def test_an_inconsistent_timing_map_is_refused(mutate):
    m = json.loads(M5A.read_text())
    mutate(m)
    with pytest.raises(cps.Refused):
        cps.targets(m)


# ---- the clean known answer ----------------------------------------------

def test_a_log_on_the_manifest_schedule_passes(tmp_path):
    assert _run(tmp_path, _rows(M5A_ON, M5A_OFF)) == 0


def test_m5b_known_answer_passes(tmp_path):
    assert _run(tmp_path, _rows(M5B_ON, M5B_OFF), M5B) == 0


# ---- early landings: the defect this check exists for ---------------------

def test_the_historical_early_landing_is_rejected(tmp_path, capsys):
    """#372's own shape: the first event on time, later ones 11, 16 and 27
    frames early, every applied frame equal to its pin prediction."""
    on = [f + d for f, d in zip(M5A_ON, (0, -11, -16, -27))]
    off = [f + d for f, d in zip(M5A_OFF, (-2, -13, -18, -29))]
    assert _run(tmp_path, _rows(on, off)) == 1
    out = capsys.readouterr().out
    assert "FAIL" in out and "early" in out and "-27" in out


def test_one_frame_early_on_the_LAST_event_only_is_rejected(tmp_path):
    """Defeats a check that only looks at the first event."""
    on = list(M5A_ON); on[-1] -= 1
    assert _run(tmp_path, _rows(on, M5A_OFF)) == 1


def test_a_constant_lead_in_is_not_aligned_away(tmp_path):
    """The previous checker subtracted the first event's offset, so a phrase
    54 frames late everywhere -- what the scorer's absolute windows see --
    printed all zeros."""
    assert _run(tmp_path, _rows([f + 54 for f in M5A_ON], [f + 54 for f in M5A_OFF])) == 1


def test_a_short_gate_is_rejected_even_when_every_gate_on_is_exact(tmp_path):
    assert _run(tmp_path, _rows(M5A_ON, [f - 2 for f in M5A_OFF])) == 1


def test_the_report_separates_lead_in_drift_and_gate_error(tmp_path):
    on = [f + 3 + d for f, d in zip(M5A_ON, (0, -5, -8, -13))]
    off = [f - 2 for f in on]
    off = [o + (M5A_OFF[i] - M5A_ON[i]) for i, o in enumerate(off)]
    d = _write(tmp_path, _rows(on, off))
    res = cps.judge(cps.targets(json.loads(M5A.read_text())), cps.read_log(d))
    assert res["verdict"] == "FAIL"
    assert res["lead_in_frames"] == 3
    assert res["gate_on_drift_frames"] == [0, -5, -8, -13]
    assert res["gate_length_error_frames"] == [-2, -2, -2, -2]
    assert res["worst_early_frames"] == -12


# ---- refusals: no verdict on a log that cannot carry one -------------------

def test_a_missing_gate_is_refused(tmp_path):
    rows = _rows(M5A_ON, M5A_OFF)
    rows = [r for r in rows if not (r[3] == A_OFF and r[0] == M5A_OFF[-1])]
    assert _run(tmp_path, rows) == 2


def test_an_extra_gate_is_refused(tmp_path):
    rows = _rows(M5A_ON, M5A_OFF)
    rows.append([M5A_OFF[-1] + 10, 0, 0, A_ON, 0, M5A_OFF[-1] + 10])
    assert _run(tmp_path, rows) == 2


def test_gate_off_before_gate_on_is_refused(tmp_path):
    rows = _rows(M5A_ON, M5A_OFF)
    i = next(k for k, r in enumerate(rows) if r[3] == A_ON)
    rows[i][3] = A_OFF
    rows[i + 1][3] = A_ON
    assert _run(tmp_path, rows) == 2


def test_an_incomplete_row_is_refused(tmp_path):
    rows = _rows(M5A_ON, M5A_OFF)
    rows[10] = rows[10][:5]
    assert _run(tmp_path, rows) == 2


def test_a_non_integer_row_is_refused(tmp_path):
    rows = _rows(M5A_ON, M5A_OFF)
    rows[10][4] = "x"
    assert _run(tmp_path, rows) == 2


def test_unordered_frames_are_refused(tmp_path):
    rows = _rows(M5A_ON, M5A_OFF)
    rows[5][0] = rows[5][5] = 10_000
    assert _run(tmp_path, rows) == 2


def test_a_frame_that_disagrees_with_its_pin_prediction_is_refused(tmp_path):
    rows = _rows(M5A_ON, M5A_OFF)
    i = next(k for k, r in enumerate(rows) if r[3] == A_ON)
    rows[i][5] += 1
    assert _run(tmp_path, rows) == 2


def test_a_write_with_no_pin_prediction_is_refused(tmp_path):
    rows = _rows(M5A_ON, M5A_OFF)
    rows[3][5] = -1
    assert _run(tmp_path, rows) == 2


def test_no_log_or_two_logs_is_refused(tmp_path):
    d = tmp_path / "empty"
    d.mkdir()
    assert cps.main(["--manifest", str(M5A), f"run={d}"]) == 2
    d = _write(tmp_path, _rows(M5A_ON, M5A_OFF), "two")
    (d / "top_wrs_OTHER.txt").write_text("")
    assert cps.main(["--manifest", str(M5A), f"run={d}"]) == 2


# ---- the scheduler, end to end through the bench's link timing ------------

def _log_from_script(writes):
    sys.path.insert(0, str(ROOT / "rtl-sketch"))
    import tb_top_bx_timing as link
    frames = link.landing_frames([w[0] for w in writes])
    return [[f, w[1], w[2], w[3], w[4], f] for f, w in zip(frames, writes)]


@pytest.mark.parametrize("manifest", [M5A, M5B])
@pytest.mark.parametrize("kwargs", [
    {}, {"saw_drive": 0.35},
    {"saw_cutoff_hz": 20_000, "saw_volume_correction_db": 5.22576},
])
def test_the_scheduled_phrase_lands_on_the_manifest(tmp_path, manifest, kwargs):
    sys.path.insert(0, str(ROOT / "rtl-sketch"))
    import verify_synth_top as top
    writes, _tail, _rep = top.m5a_script(str(manifest), **kwargs)
    assert _run(tmp_path, _log_from_script(writes), manifest) == 0


@pytest.mark.parametrize("kwargs,expected_on", [
    # The three rows the published receipts recorded, first event aligned.
    ({"saw_cutoff_hz": 20_000, "saw_volume_correction_db": 5.22576}, [0, -11, -16, -27]),
    ({"saw_drive": 0.35}, [0, -11, -11, -22]),
    ({"saw_drive": 0.35, "legacy_margin": 22}, [0, -17, -23, -40]),
])
def test_the_legacy_relative_schedule_reproduces_the_receipts_and_is_rejected(
        tmp_path, kwargs, expected_on):
    sys.path.insert(0, str(ROOT / "rtl-sketch"))
    import verify_synth_top as top
    writes, _tail, _rep = top.m5a_script(str(M5A), schedule="legacy-relative", **kwargs)
    rows = _log_from_script(writes)
    # The receipts were taken by the previous checker: first event aligned,
    # against the duration + silence cursor (0.1, 5.7, 13.8 and 19.4 s).
    on = [r[0] for r in rows if r[3] == A_ON]
    cursor = [4_800, 273_600, 662_400, 931_200]
    assert [a - c - (on[0] - cursor[0]) for a, c in zip(on, cursor)] == expected_on
    d = _write(tmp_path, rows)
    res = cps.judge(cps.targets(json.loads(M5A.read_text())), cps.read_log(d))
    assert res["verdict"] == "FAIL"
    assert res["worst_early_frames"] < 0 or res["lead_in_frames"] != 0
    assert cps.main(["--manifest", str(M5A), f"run={d}"]) == 1

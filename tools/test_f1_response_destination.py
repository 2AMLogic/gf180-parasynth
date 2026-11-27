#!/usr/bin/env python3
"""Where an F1 response curve goes, and whose it is once written (#1412).

The board's F1A row names `build/scorecard/F1A-ours-response-rtl.json`: the
curve its clean RTL run measured, which is what the cutoff, low-band gain and
rolloff numbers were qualified against. Until #1412 both callers of
`run_case.run_filter_case` chose a results directory and then handed the
writer a file-name suffix, so the writer put every run's curve at one shared
path. An injected control replaced the clean board curve whenever an operator
believed `--results` had given the control a directory of its own.

WHAT IS THE SHIPPED CODE AND WHAT IS A DOUBLE. Both CLIs run through their own
`main()`; the scoring (`run_filter_case`, its estimators, the board's own
`evaluate`), the verdicts and every file write are the shipped code. Four
things are doubles, because this file is about file ownership and not about a
filter:

  * the reference curve (`load_filter_reference`) is a closed-form 4-pole at
    the clip's commanded cutoff, so no frozen Surge cache is needed;
  * the DUT curve is the same closed form -- the RTL path's double compiles
    the injection in as a doubled cutoff -- so two runs' curves differ in
    their bytes and nothing needs twenty minutes of iverilog;
  * the legacy standalone diagnostic, which renders two model curves and is
    not on the path under test;
  * `base_check`, whose subject is this checkout's relation to origin/main.

The doubles substitute INPUTS. No destination is stubbed, no writer is
replaced, and the test writes no record field: those are the things under test.

WHAT A PASSING RUN HERE DOES NOT ESTABLISH. Any acoustic property of the
filter, or that the real RTL curve equals the model's. `tools/
test_score_f1_rtl.py` covers the scorer's comparison logic and `make verify`
runs the real anchor.
"""
from __future__ import annotations

import json
import pathlib
import shutil
import tempfile

import numpy as np
import pytest

import f1_filter_path as fp
import f1_rtl_filter_path as rtlpath
import run_case as rc
import score_f1_rtl as score

#: The frozen profile's own 32-point stepped-tone grid.
GRID = np.geomspace(40.0, 12000.0, 32)
RTL_CURVE = "F1A-ours-response-rtl.json"
MODEL_CURVE = "F1A-ours-response.json"
NO_AUDIO = "not written (--no-audio)"


def _four_pole_db(freqs, pole_hz):
    f = np.asarray(freqs, dtype=np.float64)
    return -40.0 * np.log10(1.0 + (f / pole_hz) ** 2)


def _reference(clip_id, inject=""):
    """`load_filter_reference` as a closed form: the clip at its commanded
    cutoff, flat for the wide-open clip, and the real loader's halved
    reference axis under `REF_CORNER_2X`."""
    pole_hz = float("inf") if "open20k" in clip_id \
        else float(clip_id.split("-cut")[1].split("-")[0])
    freqs, gain = GRID, _four_pole_db(GRID, pole_hz)
    if inject == "REF_CORNER_2X":
        freqs = freqs / 2.0
    meta = {"freqs_hz": GRID.tolist(),
            "parts": [[i, 1, float(f)] for i, f in enumerate(GRID)],
            "amp": 0.25, "sha256": "0" * 64, "rig": "surge-type2",
            "file": f"cache/{clip_id}.wav", "commanded": {"cut_hz": pole_hz},
            "cutoff_readback_hz": pole_hz}
    return freqs, gain, meta


class _CurveDouble:
    """Enough of a filter path (`SelectedFilterPath`, `RtlFilterChainPath`) for
    the real `run_filter_case` to score a case and reach its writers. The
    curve is the reference's closed form; `cutoff_scale` is a defect compiled
    in, and moves the corner by that factor."""

    render_label = "a closed-form stepped-tone curve (test double)"
    path_version = "test-double-v0"
    tools = {"iverilog": "test double -- no RTL ran"}
    cutoff_scale = 1.0

    def __init__(self, *args, **kwargs):
        self.probe_profile = {"name": "test-double"}
        self.calibration = "test-double-calibration"
        self.match = {"frames": 0}

    def curve(self, freqs, cut_hz, res, amp):
        f = np.asarray(freqs, dtype=np.float64)
        if cut_hz >= 20000.0:                        # the wide-open plateau
            return np.zeros_like(f), {"regs": {"gain": 0, "ogain": 0}}
        return (_four_pole_db(f, cut_hz * self.cutoff_scale),
                {"regs": {"gain": 0, "ogain": 0}})

    def record(self):
        return {"path_version": self.path_version}


def _rtl_double():
    """`RtlFilterChainPath` with the scorer's injection as a doubled cutoff, so
    the control is a measured, failing record rather than a refusal."""

    class _RtlDouble(_CurveDouble):
        def __init__(self, inject=None, timeout_s=None):
            super().__init__()
            self.cutoff_scale = 2.0 if inject else 1.0

    return _RtlDouble


@pytest.fixture
def board(monkeypatch):
    """A board and a shared-curve directory of this test's own, so nothing
    here can reach `docs/scorecard/results` or `build/scorecard`.

    Under the checkout's gitignored `build/`, in a directory nobody else can
    name, and removed afterwards. Under the ROOT rather than in a tmp_path
    because an on-board curve is STATED relative to the checkout, and a
    directory outside it cannot be; what a run states is half of what is
    checked here."""
    scratch_root = rc.ROOT / "build"
    scratch_root.mkdir(exist_ok=True)
    base = pathlib.Path(tempfile.mkdtemp(prefix="f1-response-", dir=scratch_root))
    places = {"base": base, "board": base / "board", "shared": base / "shared"}
    monkeypatch.setattr(rc, "RESULTS", places["board"])
    monkeypatch.setattr(rc, "AUDIO_OUT", places["shared"])
    # each CLI overwrites this module global; the double's value must not
    # outlive the test that asked for it
    monkeypatch.setattr(rc, "BASE_STATE", rc.BASE_STATE)
    monkeypatch.setattr(rc, "base_check",
                        lambda allow_stale=False: {"checked": False, "why": "a test double"})
    monkeypatch.setattr(rc, "load_filter_reference", _reference)
    monkeypatch.setattr(rc, "_legacy_reads",
                        lambda *args: {"engine": "test double -- not rendered"})
    try:
        yield places
    finally:
        shutil.rmtree(base, ignore_errors=True)


def _curve_named_by(record):
    """The bytes of the curve a record names in `provenance.artefacts.ours`,
    resolved the way a reader would: relative to the checkout unless absolute."""
    path = pathlib.Path(record["provenance"]["artefacts"]["ours"])
    return (path if path.is_absolute() else rc.ROOT / path).read_bytes()


def _gains(curve_bytes):
    return json.loads(curve_bytes)["ours_gain_db"]


def test_an_injected_rtl_control_leaves_the_clean_board_curve_and_record_untouched(
        board, monkeypatch, tmp_path):
    """The issue's reproduction, through the CLI: a clean run, then the control
    into a scratch directory of its own, outside the checkout."""
    monkeypatch.setattr(rtlpath, "RtlFilterChainPath", _rtl_double())
    scratch = tmp_path / "scratch"                # absolute, outside the checkout
    assert not scratch.is_relative_to(rc.ROOT)

    assert score.main(["--case", "F1A"]) == 0     # the clean run, on the board
    clean_record = (board["board"] / "F1A.json").read_bytes()
    clean_curve_path = board["shared"] / RTL_CURVE
    clean_curve = clean_curve_path.read_bytes()
    clean = json.loads(clean_record)
    assert _curve_named_by(clean) == clean_curve
    assert clean["provenance"]["artefacts"]["ours"] == str(clean_curve_path.relative_to(rc.ROOT))

    assert score.main(["--case", "F1A", "--inject", "F1_CHAIN_DROP_DECIM",
                       "--results", str(scratch)]) == 1
    # the clean record and the curve it names are byte-for-byte what they were
    assert (board["board"] / "F1A.json").read_bytes() == clean_record
    assert clean_curve_path.read_bytes() == clean_curve
    # ...and the control names a curve of its own, beside its record, by where
    # it actually went
    control = json.loads((scratch / "F1A.json").read_text())
    assert control["INJECTED_CONTROL"] == "RTL:F1_CHAIN_DROP_DECIM"
    stated = control["provenance"]["artefacts"]["ours"]
    assert stated == str(scratch / RTL_CURVE)
    assert control["audio"].endswith(f"ours {stated}")
    assert len(_gains(_curve_named_by(control))) == len(GRID)
    assert _gains(_curve_named_by(control)) != _gains(clean_curve)


def test_a_control_cannot_adopt_the_shared_curve_directory_as_its_results(
        board, monkeypatch, capsys):
    """The input that defeats "an off-board curve is written beside its
    results": `--results build/scorecard` IS that directory. The board guard
    does not see it, and the shared path is exactly where the control's curve
    would then go."""
    monkeypatch.setattr(rtlpath, "RtlFilterChainPath", _rtl_double())
    assert score.main(["--case", "F1A"]) == 0
    clean_curve = (board["shared"] / RTL_CURVE).read_bytes()
    before = sorted(p.name for p in board["shared"].iterdir())
    shared = board["shared"]

    for spelling in (shared, shared / ".." / shared.name / "."):
        capsys.readouterr()
        assert score.main(["--case", "F1A", "--inject", "F1_CHAIN_DROP_DECIM",
                           "--results", str(spelling)]) == 2
        assert "REFUSED" in capsys.readouterr().err
        assert (board["shared"] / RTL_CURVE).read_bytes() == clean_curve
        assert sorted(p.name for p in board["shared"].iterdir()) == before


def test_no_audio_still_writes_no_curve(board, monkeypatch, tmp_path):
    """`--no-audio` is preserved: no curve, anywhere, and the record says so."""
    monkeypatch.setattr(rtlpath, "RtlFilterChainPath", _rtl_double())
    assert score.main(["--case", "F1A"]) == 0
    clean_curve = (board["shared"] / RTL_CURVE).read_bytes()

    scratch = tmp_path / "no-audio"
    assert score.main(["--case", "F1A", "--no-audio",
                       "--results", str(scratch)]) == 0
    record = json.loads((scratch / "F1A.json").read_text())
    assert record["provenance"]["artefacts"]["ours"] == NO_AUDIO
    assert [p.name for p in scratch.iterdir()] == ["F1A.json"]
    # on the board too: a `--no-audio` run replaces neither the curve nor
    # anything beside it
    assert score.main(["--case", "F1A", "--no-audio"]) == 0
    assert (board["shared"] / RTL_CURVE).read_bytes() == clean_curve


def test_the_fixed_model_runner_keeps_an_off_board_curve_with_its_results(
        board, monkeypatch, tmp_path):
    """`tools/run_case.py` reaches the same writer for every F1 case. Its
    control moves the reference, so the record is a reference-side injection;
    the ownership rule is the same one."""
    monkeypatch.setattr(fp, "SelectedFilterPath", _CurveDouble)
    scratch = tmp_path / "scratch"
    assert not scratch.is_relative_to(rc.ROOT)

    assert rc.main(["F1A"]) == 0                  # the clean run, on the board
    clean_record = (board["board"] / "F1A.json").read_bytes()
    clean_curve = (board["shared"] / MODEL_CURVE).read_bytes()
    assert _curve_named_by(json.loads(clean_record)) == clean_curve

    assert rc.main(["F1A", "--inject", "REF_CORNER_2X", "--results", str(scratch)]) == 1
    assert (board["board"] / "F1A.json").read_bytes() == clean_record
    assert (board["shared"] / MODEL_CURVE).read_bytes() == clean_curve
    control = json.loads((scratch / "F1A.json").read_text())
    assert control["INJECTED_CONTROL"] == "REF_CORNER_2X"
    assert control["provenance"]["artefacts"]["ours"] == str(scratch / MODEL_CURVE)
    assert _curve_named_by(control) != clean_curve

    # and the same control cannot adopt the shared directory either
    assert rc.main(["F1A", "--inject", "REF_CORNER_2X",
                    "--results", str(board["shared"])]) == 2
    assert sorted(p.name for p in board["shared"].iterdir()) == [MODEL_CURVE]
    assert (board["shared"] / MODEL_CURVE).read_bytes() == clean_curve


def test_every_published_f1_row_names_the_curve_a_default_run_of_its_engine_writes():
    """The clean board path is not moved by the off-board rule: each published
    F1 row names the file a default run of its own engine writes."""
    for cid in ("F1A", "F1B", "F1C"):
        row = json.loads((rc.RESULTS / f"{cid}.json").read_text())
        tag = "-rtl" if row["engine"] == rtlpath.ENGINE else ""
        assert row["provenance"]["artefacts"]["ours"] == str(
            (rc.AUDIO_OUT / f"{cid}-ours-response{tag}.json").relative_to(rc.ROOT))


def test_the_response_destination_is_the_shared_path_on_the_board_and_beside_the_results_off_it(
        tmp_path):
    """The rule both callers resolve through, pinned apart from any run."""
    board, shared = rc.RESULTS, rc.AUDIO_OUT
    assert rc.response_destination(board, "F1A", "-rtl") == shared / RTL_CURVE
    assert rc.response_destination(board, "F1A") == shared / MODEL_CURVE
    # the board reached by another spelling is still the board
    assert rc.response_destination(board / "." / ".." / board.name, "F1A", "-rtl") \
        == shared / RTL_CURVE
    # off the board: beside the results, whatever directory that is
    inside = rc.ROOT / "build" / "candidate"
    assert rc.response_destination(inside, "F1A", "-rtl") == inside / RTL_CURVE
    assert rc.response_destination(tmp_path, "F1A", "-rtl") == tmp_path / RTL_CURVE
    # ...even when that directory is the shared one. The path is then the clean
    # board curve's own, which is why an injected run is refused that directory.
    assert rc.response_destination(shared, "F1A", "-rtl") == shared / RTL_CURVE


def test_a_stated_path_is_relative_inside_the_checkout_and_absolute_outside(tmp_path):
    assert rc.stated_path(rc.ROOT / "build" / "scorecard" / "x.json") \
        == "build/scorecard/x.json"
    assert rc.stated_path(rc.ROOT / ".." / "beside.json") == "../beside.json"
    outside = tmp_path / "x.json"
    assert not outside.is_relative_to(rc.ROOT)
    assert rc.stated_path(outside) == str(outside)
    # a results directory given relative to some other working directory is
    # stated where it actually went, not relative to a cwd nobody kept
    assert rc.stated_path(pathlib.Path("elsewhere/x.json")) \
        == str(pathlib.Path("elsewhere/x.json").resolve())


#: Each CLI that reaches the F1 curve writer, as (its `main`, the module and
#: class it builds its filter path from, the double for that class, the
#: arguments of a clean F1A, the file name its curve is written under).
_CLIS = {
    "model": (rc, fp, "SelectedFilterPath", _CurveDouble, ["F1A"], MODEL_CURVE),
    "rtl": (score, rtlpath, "RtlFilterChainPath", _rtl_double(), ["--case", "F1A"],
            RTL_CURVE),
}


def _clean_run_into(cli, monkeypatch, results):
    """One clean F1A through the named CLI, its curve written into `results`.
    Returns the CLI's exit status."""
    module, paths, cls, double, argv, _ = _CLIS[cli]
    monkeypatch.setattr(paths, cls, double)
    return module.main([*argv, "--results", str(results)])


@pytest.mark.parametrize("cli, where", [(cli, where) for cli in _CLIS
                                        for where in ("outside", "inside")])
def test_an_absolute_results_directory_records_the_target_of_the_curve_filename_symlink(
        board, monkeypatch, tmp_path, cli, where):
    """#1435: the absolute spelling of #1415's retargeting control, through
    both CLIs that reach the F1 curve writer. A symlink AT the curve filename
    receives the write, and the record states that link by its own name -- so
    once the link is retargeted, the name opens the replacement's `{}` and no
    longer the measured curve. The record must name the file the write landed
    in: relative to the checkout when that file is inside it, absolute when it
    is not.

    An absolute `--results` is the board's own default (`ROOT / "build" /
    "scorecard"`), and the RTL scorer's rule for every spelling, so it is the
    spelling the published rows are written under. Checked on the LITERAL
    strings of both fields that carry the path.

    What a pass does not establish: that the named file's BYTES are pinned. A
    link moved between the write and the resolution, or the target itself
    replaced afterwards, is not guarded against."""
    candidate = (tmp_path if where == "outside" else board["base"]) / "candidate"
    candidate.mkdir()
    # the premise of each spelling -- and that no symlink sits on the way to
    # the directory, so only the filename's own link is under test
    assert candidate.is_absolute()
    assert candidate.is_relative_to(rc.ROOT) == (where == "inside")
    assert candidate.resolve() == candidate
    original, replacement = candidate / "curve-v1.json", candidate / "curve-v2.json"
    original.write_text("{}")
    replacement.write_text("{}")
    link = candidate / _CLIS[cli][5]
    link.symlink_to(original.name)

    assert _clean_run_into(cli, monkeypatch, candidate) == 0
    record = json.loads((candidate / "F1A.json").read_text())
    measured = original.read_bytes()
    assert len(_gains(measured)) == len(GRID)        # the first target received the write
    link.unlink()
    link.symlink_to(replacement.name)
    assert _curve_named_by(record) == measured, "the record follows the retargeted symlink"
    stated = str(original.relative_to(rc.ROOT) if where == "inside" else original)
    assert record["provenance"]["artefacts"]["ours"] == stated
    assert record["audio"].endswith(f"ours {stated}")

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

A CLEAN RUN AND THE PUBLISHED CURVE (#1430). Every test above reaches the
board guard through `--inject`. A clean run carries none, so until #1430

    tools/run_case.py F1B --results build/scorecard

was refused by nothing: off the board a curve goes beside its record, and
with that `--results` "beside the record" IS the published curve. The bench at
the end of this file (`_clean_collision`) points a clean run of each row at
its own published curve six ways and reads four properties off it
(`PROTECTED`). The rows are the ones a published record names: `results/F1B.json`
names the fixed-model curve `run_case.py` writes, `results/F1A.json` the RTL
curve `tools/score_f1_rtl.py` does. The two CLIs read a relative `--results`
differently -- against the working directory, and against the checkout -- so
the bench gives each its own relative spelling rather than one that would
send the RTL scorer into the real `build/scorecard`.

WHAT A PASSING RUN HERE DOES NOT ESTABLISH. Any acoustic property of the
filter, or that the real RTL curve equals the model's. `tools/
test_score_f1_rtl.py` covers the scorer's comparison logic and `make verify`
runs the real anchor.
"""
from __future__ import annotations

import json
import os
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
    places = {"base": base, "board": base / "board", "shared": base / "shared",
              "cwd": base / "cwd", "runs": 0, "base_checks": 0}
    # the other working directory `run_case.main` reads a relative --results
    # from (#1430)
    places["cwd"].mkdir()
    monkeypatch.setattr(rc, "RESULTS", places["board"])
    monkeypatch.setattr(rc, "AUDIO_OUT", places["shared"])
    # each CLI overwrites this module global; the double's value must not
    # outlive the test that asked for it
    monkeypatch.setattr(rc, "BASE_STATE", rc.BASE_STATE)

    def base_check(allow_stale=False):
        places["base_checks"] += 1        # so a test can tell a refusal that
        return {"checked": False, "why": "a test double"}   # came before it

    monkeypatch.setattr(rc, "base_check", base_check)
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
    # ...but never onto the clean board curve's own path, which is exactly what
    # a `--results` of the shared directory spells (#1430). An injected run was
    # already refused that directory; a clean one is refused the FILE.
    with pytest.raises(rc.Refused, match=REFUSAL):
        rc.response_destination(shared, "F1A", "-rtl")
    with pytest.raises(rc.Refused, match=REFUSAL):
        rc.response_destination(shared, "F1B")


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


# ===========================================================================
# A clean run and the published curve (#1430)
# ===========================================================================

#: The two rows a published record names through this writer, each with the
#: curve's own name: `results/F1B.json` names the fixed-model curve
#: `run_case.py` writes, `results/F1A.json` the RTL curve of
#: `tools/score_f1_rtl.py`.
PUBLISHED = [pytest.param("model", "F1B", "F1B-ours-response.json", id="fixed-model"),
             pytest.param("rtl", "F1A", RTL_CURVE, id="integrated-rtl")]

#: What separates this refusal from every other exit 2: an RTL path that cannot
#: be built is REFUSED with exit 2 as well.
REFUSAL = "would put this run's response curve"

#: What the collision bench checks, and what a violation is called.
REFUSED_NAMING = "refused, naming the case, the destination and the published curve"
PUBLISHED_CURVE = "the published curve is byte-identical"
NOTHING_BESIDE = "nothing was written into the published directory"
NOTHING_RAN = "nothing ran: neither base_check nor the filter path"
PROTECTED = (REFUSED_NAMING, PUBLISHED_CURVE, NOTHING_BESIDE, NOTHING_RAN)

#: The six spellings, by what a run given one of them writes beside its curve:
#: a run given the published DIRECTORY (by any name) puts its record there too,
#: so `NOTHING_BESIDE` sees it; a run given a link to the published FILE puts
#: its record beside the link, where that property cannot look.
RELATIVE, DOTDOT = "relative", "with .. in it"
SOFT, HARD_LINK = "a symlink to the published curve", "a hard link to the published curve"
TO_THE_DIRECTORY = (RELATIVE, DOTDOT, "the directory itself", "with . in it")
TO_A_LINK = (SOFT, HARD_LINK)
#: The spellings that reach the published curve through an alias rather than by
#: its directory's own name.
ALIASES = (RELATIVE, DOTDOT, SOFT, HARD_LINK)
#: What a spelling that proceeds violates, by kind.
THROUGH_THE_DIRECTORY = PROTECTED
THROUGH_A_LINK = (REFUSED_NAMING, PUBLISHED_CURVE, NOTHING_RAN)


#: Every spelling, and what each violates when it proceeds unchecked.
ALL = TO_THE_DIRECTORY + TO_A_LINK
UNCHECKED = ({label: THROUGH_THE_DIRECTORY for label in TO_THE_DIRECTORY}
             | {label: THROUGH_A_LINK for label in TO_A_LINK})


def _readings(proceeded: dict[str, tuple[str, ...]]) -> dict[str, tuple[str, ...]]:
    """The bench's reading when the spellings in `proceeded` ran, violating
    what it says of each, and every other spelling was refused."""
    return {label: proceeded.get(label, tuple()) for label in ALL}


def _lexical_catches(engine: str) -> tuple[str, ...]:
    """The spellings a comparison of spellings catches: the directory's own
    name -- and, for the RTL scorer, its relative spelling too, because that
    CLI makes a relative `--results` absolute against the checkout before any
    comparison is made."""
    return ("the directory itself", "with . in it") + (() if engine == "model" else (RELATIVE,))


def _double(board, monkeypatch, engine: str) -> None:
    """The doubled filter path a row's CLI scores through, counting how often
    it is built, so a test can tell a refusal that came before the run."""
    base = _CurveDouble if engine == "model" else _rtl_double()

    class _Counted(base):
        def __init__(self, *args, **kwargs):
            board["runs"] += 1
            super().__init__(*args, **kwargs)

    name, module = (("SelectedFilterPath", fp) if engine == "model"
                    else ("RtlFilterChainPath", rtlpath))
    monkeypatch.setattr(module, name, _Counted)


def _cli(engine: str, *argv) -> int:
    """The row's own CLI: `run_case.main` for the fixed-model curve,
    `score_f1_rtl.main` for the RTL one."""
    if engine == "rtl":
        return score.main(["--case", "F1A", *argv])
    return rc.main(["F1B", *argv])


def _case(engine: str) -> str:
    return "F1A" if engine == "rtl" else "F1B"


def _seed(curve: pathlib.Path) -> bytes:
    """Put a sentinel on a published path -- bytes no run writes -- and return
    them."""
    curve.parent.mkdir(parents=True, exist_ok=True)
    curve.write_bytes(b'{"ours_gain_db": "sentinel: written by a test, by no run"}\n')
    return curve.read_bytes()


def _relative(board, engine: str) -> tuple[str, str, pathlib.Path]:
    """The relative spelling of the published directory, read the way the row's
    own CLI reads one."""
    if engine == "model":
        # `run_case.main`: against the working directory, here through a symlink
        through = board["cwd"] / "build" / "scorecard"
        through.parent.mkdir(parents=True)
        through.symlink_to(board["shared"], target_is_directory=True)
        return ("relative", "build/scorecard", through)
    # `score_f1_rtl.main`: against the checkout
    return ("relative", os.path.relpath(board["shared"], rc.ROOT), board["shared"])


def _spellings(board, engine: str, curve: str) -> list[tuple[str, str, pathlib.Path]]:
    """Spellings of the published directory, or of a link to its curve, each
    with the directory whose file the refusal has to name."""
    shared = board["shared"]
    soft, hard = board["base"] / "soft", board["base"] / "hard"
    soft.mkdir()
    (soft / curve).symlink_to(shared / curve)
    hard.mkdir()
    os.link(shared / curve, hard / curve)
    return [_relative(board, engine),
            ("the directory itself", str(shared), shared),
            ("with . in it", f"{shared}/.", shared),
            (DOTDOT, str(shared / ".." / shared.name), shared / ".." / shared.name),
            (SOFT, str(soft), soft),
            (HARD_LINK, str(hard), hard)]


def _select_without_refusing(results_dir, case_id, tag=""):
    """`response_destination` as #1412 shipped it: the path, and no refusal."""
    return rc.artefact_directory(results_dir) / f"{case_id}-ours-response{tag}.json"


def _clean_collision(board, monkeypatch, capsys, engine: str, curve: str) -> dict[str, list[str]]:
    """A CLEAN run pointed at its own published curve, through the row's CLI,
    once per spelling of `_spellings`. Returns, per spelling, the properties of
    `PROTECTED` it violated -- the bench the controls below must turn red.

    Every spelling starts from the same state -- the sentinel, and nothing
    else in the published directory -- so what an earlier spelling left behind
    is never read as a later one's doing."""
    case_id = _case(engine)
    published = board["shared"] / curve
    _seed(published)                      # the hard link needs the file to exist
    _double(board, monkeypatch, engine)
    monkeypatch.chdir(board["cwd"])
    seen = {}
    for label, spelling, said in _spellings(board, engine, curve):
        before = _seed(published)
        ran = (board["runs"], board["base_checks"])
        capsys.readouterr()
        code = _cli(engine, "--results", spelling)
        err = capsys.readouterr().err
        violated = []
        # exit 2 alone is not the refusal: an RTL path that cannot be built is
        # exit 2 too
        if not (code == 2 and "REFUSED" in err and case_id in err and REFUSAL in err
                and f"at {rc.stated_path(said / curve)}," in err
                and f"published response curve {rc.stated_path(published.resolve())} " in err
                and rc.stated_path(rc.RESULTS / f"{case_id}.json") in err):
            violated.append(REFUSED_NAMING)
        if not published.exists() or published.read_bytes() != before:
            violated.append(PUBLISHED_CURVE)
        if sorted(p.name for p in board["shared"].iterdir()) != [curve]:
            violated.append(NOTHING_BESIDE)
        if (board["runs"], board["base_checks"]) != ran:
            violated.append(NOTHING_RAN)
        seen[label] = violated
        for stray in board["shared"].iterdir():
            if stray.name != curve:
                stray.unlink()
    return seen


@pytest.mark.parametrize("engine,case_id,curve", PUBLISHED)
def test_a_clean_run_cannot_put_its_curve_on_the_published_path(
        board, monkeypatch, capsys, engine, case_id, curve):
    """The input #1412's guard could not have, through each CLI: a CLEAN run
    whose `--results` names its own published curve, six ways. Each is refused
    with exit 2 before `base_check` and before the filter path is built, says
    which case, which destination and which published curve, and leaves the
    sentinel and the directory as they were."""
    seen = _clean_collision(board, monkeypatch, capsys, engine, curve)
    assert {label: violated for label, violated in seen.items() if violated} == {}, seen


@pytest.mark.parametrize("engine,case_id,curve", PUBLISHED)
def test_the_published_curve_is_protected_before_it_exists(
        board, monkeypatch, capsys, engine, case_id, curve):
    """A published curve nobody has measured yet is still the published path: a
    clean run must not be the thing that creates it. The comparison is of
    paths, so it needs neither file to exist -- including through a link left
    dangling by the absent curve."""
    shared = board["shared"]
    shared.mkdir()
    dangling = board["base"] / "dangling"
    dangling.mkdir()
    (dangling / curve).symlink_to(shared / curve)       # a link to what is not there
    _double(board, monkeypatch, engine)
    monkeypatch.chdir(board["cwd"])
    spellings = [_relative(board, engine),
                 ("the directory itself", str(shared), shared),
                 ("with .. in it", str(shared / ".." / shared.name), shared),
                 ("a symlink to the curve, not there yet", str(dangling), dangling)]
    for label, spelling, _said in spellings:
        capsys.readouterr()
        assert _cli(engine, "--results", spelling) == 2, label
        err = capsys.readouterr().err
        assert REFUSAL in err and case_id in err, (label, err)
    assert (board["runs"], board["base_checks"]) == (0, 0)
    assert list(shared.iterdir()) == []
    assert [p.name for p in dangling.iterdir()] == [curve] and (dangling / curve).is_symlink()


@pytest.mark.parametrize("engine,case_id,curve", PUBLISHED)
def test_no_audio_needs_no_refusal_and_leaves_the_published_curve_alone(
        board, monkeypatch, capsys, engine, case_id, curve):
    """The collision check applies only when the invocation will write the
    curve (AC 2). With `--no-audio` the published directory is an ordinary
    off-board one: the run proceeds, its record alone lands there, and the
    record says the curve was not written."""
    _double(board, monkeypatch, engine)
    before = _seed(board["shared"] / curve)
    capsys.readouterr()
    _cli(engine, "--no-audio", "--results", str(board["shared"]))
    assert REFUSAL not in capsys.readouterr().err
    record = json.loads((board["shared"] / f"{case_id}.json").read_text())
    assert record["provenance"]["artefacts"]["ours"] == NO_AUDIO
    assert (board["shared"] / curve).read_bytes() == before
    assert sorted(p.name for p in board["shared"].iterdir()) == sorted([curve, f"{case_id}.json"])


def test_a_path_that_cannot_be_built_is_not_read_as_a_collision_refusal(
        board, monkeypatch, capsys, tmp_path):
    """The other way exit 2 arrives from this CLI: an RTL path that cannot be
    built is REFUSED, on stderr, with the same status. It shares the collision
    refusal's shape and nothing else, which is why the bench reads the words."""
    def cannot_build(*args, **kwargs):
        raise rtlpath.Refused("iverilog: not found (a test double)")

    monkeypatch.setattr(rtlpath, "RtlFilterChainPath", cannot_build)
    capsys.readouterr()
    assert score.main(["--case", "F1A", "--results", str(tmp_path / "scratch")]) == 2
    err = capsys.readouterr().err
    assert "REFUSED" in err and REFUSAL not in err
    assert (board["runs"], board["base_checks"]) == (0, 1)


@pytest.mark.parametrize("engine,case_id,curve", PUBLISHED)
def test_the_unrefused_shared_curve_is_a_control_that_stays_red(
        board, monkeypatch, capsys, engine, case_id, curve):
    """The defect as #1430 found it on these writers, kept where the verdict is
    issued: a destination that selects the path and never refuses it. The bench
    above must go red on it, for every spelling.

        ==========================  =========================================
        property                    under a destination that never refuses
        ==========================  =========================================
        refused, naming ...         MOVED   the run went ahead, every spelling
        the published curve         MOVED   replaced by this run's curve
        nothing written beside      MOVED   for the four directory spellings:
                                            its record lands there too. BLIND
                                            to the two file links: the record
                                            lands beside the link, as it should
        nothing ran                 MOVED   base_check and the path, every time
        ==========================  =========================================
    """
    monkeypatch.setattr(rc, "response_destination", _select_without_refusing)
    seen = _clean_collision(board, monkeypatch, capsys, engine, curve)
    assert {label: tuple(violated) for label, violated in seen.items()} == _readings(UNCHECKED)


@pytest.mark.parametrize("engine,case_id,curve", PUBLISHED)
def test_a_comparison_of_spellings_is_a_control_that_stays_red(
        board, monkeypatch, capsys, engine, case_id, curve):
    """Constructed at write time (rule 8), from no bug this project made: the
    guard compares the two paths as written. Every alias defeats it, exactly
    as the control above is defeated; the directory's own name is all it
    catches -- under two spellings for `run_case.py`, and three for
    `score_f1_rtl.py`, which makes a relative `--results` absolute against
    the checkout (`_lexical_catches`)."""
    monkeypatch.setattr(rc, "_names_one_file", lambda candidate, published: candidate == published)
    seen = _clean_collision(board, monkeypatch, capsys, engine, curve)
    caught = _lexical_catches(engine)
    assert {label: tuple(violated) for label, violated in seen.items()} \
        == _readings({label: v for label, v in UNCHECKED.items() if label not in caught})
    assert seen[DOTDOT] == list(PROTECTED)


@pytest.mark.parametrize("engine,case_id,curve", PUBLISHED)
def test_a_comparison_without_the_inode_leg_is_a_control_that_stays_red(
        board, monkeypatch, capsys, engine, case_id, curve):
    """The other leg, constructed the same way: resolved paths, and no
    same-file test. A hard link is the one spelling that defeats it, because
    a hard link is another name for the inode and no path comparison sees it.
    `NOTHING_BESIDE` is BLIND to that spelling, for the reason the first
    control gives: the record lands beside the link."""
    monkeypatch.setattr(rc, "_names_one_file",
                        lambda candidate, published: candidate.resolve() == published.resolve())
    seen = _clean_collision(board, monkeypatch, capsys, engine, curve)
    assert {label: tuple(violated) for label, violated in seen.items()} \
        == _readings({HARD_LINK: THROUGH_A_LINK})
    assert seen[HARD_LINK] == list(THROUGH_A_LINK)

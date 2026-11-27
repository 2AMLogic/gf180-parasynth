#!/usr/bin/env python3
"""Where a drum or ensemble WAV goes, and whose it is once written (#1414).

`docs/scorecard/results/D12A.json` names `build/scorecard/D12A-ours.wav` in
`provenance.artefacts.ours`: the clap a reader re-derives the burst/tail ratio
from. Until #1414 both WAV writers in `tools/run_case.py` chose that directory
themselves and neither was told the results directory, so

    tools/run_case.py --inject REF_F0_20PCT D12A --results build/x

put its record in `build/x/` and its WAV on the board's own path. The control
replaced the recording the published record names, and the control's own
record named that shared file as its own. The refusals #1412 added to
`run_case.main` did not cover it: these writers ignored `--results`
altogether. #1412 was the same defect in the filter writers, and
`tools/test_f1_response_destination.py` is the pattern this file follows.

WHAT IS THE SHIPPED CODE AND WHAT IS A DOUBLE. Both writers run through
`run_case.main()`. The routing to a family's runner, the directory rule, the
measurement, the verdict, the WAV writer (`write_wav16`), the record writer
(`write_result`) and every field of each record are the shipped code. Three
things are doubled, because this file is about file ownership and not about a
drum:

  * the Fischer reference is a synthetic corpus directory holding int16 PCM
    for the clap and snare. The real `load_reference` reads it, with
    its own guards, and the real `REF_F0_20PCT` stretch is what moves it;
  * our render (`render_drum_solo`, `render_ensemble`) is a signal the test
    tags per invocation;
  * `base_check`, whose subject is this checkout's relation to origin/main.

The doubles substitute INPUTS. No destination is stubbed, no writer is
replaced, and the test writes no record field: those are the things under
test.

WHY THE TWO INVOCATIONS ARE MADE TO SOUND DIFFERENT. In a real run the drum
control moves the REFERENCE and leaves our render alone, and the ensemble
render is the same for an injected run, so two runs of a case write the
same bytes. A shared-path rewrite is then invisible in the board WAV -- it is
replaced with itself -- and comparing the clean WAV alone reports an intact
board. So the second invocation of each case renders a different signal, and
the samples are read back from the file the control's record names.

WHAT A PASSING RUN HERE DOES NOT ESTABLISH. Any acoustic property of the clap
or of the ensemble, or that the synthetic corpus resembles a TR-808. Which
WAV paths the PUBLISHED records name is checked against the real module
constants, apart from any run.
"""
from __future__ import annotations

import json
import math
import pathlib
import shutil
import tempfile

import numpy as np
import pytest
from scipy.io import wavfile

import run_case as rc

SR = 48000
FIRST, SECOND = 220.0, 331.0            # a render's tag, in Hz: one per invocation
NO_AUDIO = "not written (--no-audio)"

#: The two writers: the case that reaches each, its WAV's established name,
#: and the `provenance.artefacts` key its record states the WAV under.
WRITERS = [pytest.param("D12A", "D12A-ours.wav", "ours", id="drum"),
           pytest.param("E1A", "E1A-mix.wav", "mix", id="ensemble")]

#: What this file checks of a control run, and what a violation is called.
CLEAN_RECORD = "the clean board record is byte-identical"
CLEAN_WAV = "the clean board WAV is byte-identical"
OWN_WAV = "the control names a WAV of its own, beside its record"
OWN_PCM = "that WAV holds the control's own samples"
PROPERTIES = (CLEAN_RECORD, CLEAN_WAV, OWN_WAV, OWN_PCM)


def _strike_pcm(freq_hz: float, sr: int = SR, seconds: float = 0.60, tau: float = 0.040,
                lead_ms: float = 10.0) -> np.ndarray:
    """A drum-shaped signal as 16-bit PCM: a click on a decaying body after a
    lead of true silence, long enough that the decay estimator's length guard
    is satisfied (the shape `tools/test_run_case.py` gives the drum plans).
    `freq_hz` is the invocation's tag: two tags differ in nearly every sample."""
    n = int(seconds * sr)
    a = int(lead_ms * 1e-3 * sr)
    t = np.arange(n - a) / sr
    x = np.zeros(n)
    x[a:] = (np.exp(-t / tau) * np.sin(2 * math.pi * freq_hz * t)
             + 0.05 * np.exp(-t / 0.002) * np.sin(2 * math.pi * 3000.0 * t))
    return np.round(x / np.abs(x).max() * 32767.0).astype("<i2")


def _ensemble(freq_hz: float, patch_name: str, sr: int = SR, seconds: float = 6.0,
              hit_s: float = 0.05) -> dict:
    """What `render_ensemble` returns, for a test: a voice stem and a drum stem
    struck together, their sum as the mix, and the drum stem alone as the one
    stop whose onset is timed against the schedule it was written on. Every
    metric of E1A measures it: the mix is the stem sum to the sample, nothing
    reaches the rail, and the onset is found where it was scheduled. `freq_hz`
    is the invocation's tag."""
    n = int(seconds * sr)
    i = int(hit_s * sr)

    def stem(f: float, tau: float, gain: float, length_s: float) -> np.ndarray:
        s = np.zeros(n)
        t = np.arange(int(length_s * sr)) / sr
        s[i:i + t.size] = gain * np.exp(-t / tau) * np.sin(2 * math.pi * f * t)
        return np.round(s * 20000.0)

    voice = stem(freq_hz / 2.0, 0.20, 0.5, 0.4)
    drum = stem(freq_hz * 60.0 / 220.0, 0.25, 0.8, 0.5)
    return dict(sr=sr, mix=voice + drum, stems={"voice": voice, "drum-mix": drum},
                per_stop={"BD": (drum, [hit_s])}, hits_s=[hit_s], patch=patch_name,
                bpm=124.0, n=n, render="a test double -- nothing was rendered")


def _rendered(case_id: str, hz: float) -> np.ndarray:
    """The 16-bit samples the double hands `run_case` for a case at tag `hz`."""
    if case_id in ("D12A", "D02A"):
        return _strike_pcm(hz)
    return _ensemble(hz, "")["mix"].astype("<i2")


class _Renders:
    """Stands in for `render_drum_solo` and `render_ensemble`, returning the
    signal `hz` selects, so a test can make two invocations of one case differ
    in their bytes."""

    def __init__(self) -> None:
        self.hz = FIRST

    def drum(self, sound: str, accent: float = 1.0, inject: str | None = None,
             hit_frame: int | None = None, frames: int | None = None) -> tuple:
        assert sound in ("CP", "SD")
        return _strike_pcm(self.hz) / 32768.0, SR

    def ensemble(self, patch_name: str, dense: bool, seconds: float = 6.0,
                 bpm: float = 124.0) -> dict:
        return _ensemble(self.hz, patch_name)


@pytest.fixture
def board(monkeypatch, tmp_path):
    """A board and a shared-WAV directory of this test's own, plus the input
    doubles, so nothing here can reach `docs/scorecard/results` or
    `build/scorecard`.

    Under the checkout's gitignored `build/`, in a directory nobody else can
    name, and removed afterwards. Under the ROOT rather than in a tmp_path
    because an on-board WAV is STATED relative to the checkout, and a
    directory outside it cannot be; what a run states is half of what is
    checked here."""
    scratch_root = rc.ROOT / "build"
    scratch_root.mkdir(exist_ok=True)
    base = pathlib.Path(tempfile.mkdtemp(prefix="wav-dest-", dir=scratch_root))
    places = {"base": base, "board": base / "board", "shared": base / "shared",
              "corpus": tmp_path / "corpus"}
    monkeypatch.setattr(rc, "RESULTS", places["board"])
    monkeypatch.setattr(rc, "AUDIO_OUT", places["shared"])
    # `main` overwrites this module global; the double's value must not outlive
    # the test that asked for it
    monkeypatch.setattr(rc, "BASE_STATE", rc.BASE_STATE)
    monkeypatch.setattr(rc, "base_check",
                        lambda allow_stale=False: {"checked": False, "why": "a test double"})
    renders = _Renders()
    monkeypatch.setattr(rc, "render_drum_solo", renders.drum)
    monkeypatch.setattr(rc, "render_ensemble", renders.ensemble)
    places["renders"] = renders
    # Give the real loader the same strike as the clean drum render.
    for sound in ("CP", "SD"):
        reference = places["corpus"] / rc.REF_MAIN[sound][0]
        reference.parent.mkdir(parents=True, exist_ok=True)
        wavfile.write(str(reference), SR, _strike_pcm(FIRST))
    # the premise of everything below: a rewrite IS visible in the bytes
    assert not np.array_equal(_rendered("D12A", FIRST), _rendered("D12A", SECOND))
    assert not np.array_equal(_rendered("E1A", FIRST), _rendered("E1A", SECOND))
    try:
        yield places
    finally:
        shutil.rmtree(base, ignore_errors=True)


def _cli(board, case_id: str, *extra) -> int:
    """`run_case.main` for one case, against the isolated board."""
    return rc.main([case_id, "--refs", str(board["corpus"]), *extra])


def _resolved(stated: str) -> pathlib.Path:
    """The file a record names, resolved the way a reader would: relative to
    the checkout unless absolute."""
    path = pathlib.Path(stated)
    return path if path.is_absolute() else rc.ROOT / path


def _pcm(path: pathlib.Path) -> np.ndarray:
    """The samples of a WAV as written, read by scipy rather than by anything
    `run_case` owns."""
    assert path.exists(), f"{path} is named by a record but was not written"
    sr, x = wavfile.read(str(path))
    assert sr == SR and x.dtype == np.int16, (path, sr, x.dtype)
    return x


def _measured(record: dict) -> bool:
    """The run reached its writers: no runner refused before them. A refusal
    writes a record with no artefact, so a test that read one as a pass would
    be green on nothing -- `no verdict` is not ownership."""
    return not record.get("note", "").startswith("REFUSED") and "traceback" not in record


def _files_under(directory: pathlib.Path) -> list[str]:
    """Every file below `directory`, whether or not it exists yet, by path."""
    if not directory.exists():
        return []
    return sorted(p.relative_to(directory).as_posix() for p in directory.rglob("*") if p.is_file())


def _control_beside_its_record(board, scratch, case_id: str, wav: str, key: str,
                               arm=lambda: None) -> dict:
    """The issue's reproduction through the CLI, for one writer: a clean run on
    the board, then `--inject REF_F0_20PCT` into a scratch directory of its own,
    outside the checkout, rendering a different signal the second time. `arm`
    runs between the two, and is where a control installs its defect.

    Returns what was observed: the properties violated, in `PROPERTIES` order,
    and both records. The same run is therefore the bench the controls at the
    end of this file must turn red."""
    assert not scratch.is_relative_to(rc.ROOT)
    renders = board["renders"]
    _cli(board, case_id)                                # the clean run, on the board
    clean_bytes = (board["board"] / f"{case_id}.json").read_bytes()
    clean = json.loads(clean_bytes)
    assert _measured(clean), clean.get("note")
    clean_stated = clean["provenance"]["artefacts"][key]
    # a default run keeps the established name, stated relative to the checkout
    assert clean_stated == str((board["shared"] / wav).relative_to(rc.ROOT))
    board_wav = _resolved(clean_stated)
    board_bytes = board_wav.read_bytes()
    assert np.array_equal(_pcm(board_wav), _rendered(case_id, FIRST))

    renders.hz = SECOND                                 # the second invocation differs
    arm()
    _cli(board, case_id, "--inject", "REF_F0_20PCT", "--results", str(scratch))
    control = json.loads((scratch / f"{case_id}.json").read_bytes())
    assert _measured(control), control.get("note")
    stated = control["provenance"]["artefacts"][key]
    control_wav = _resolved(stated)
    control_pcm = _pcm(control_wav) if control_wav.exists() else None

    violated = []
    if (board["board"] / f"{case_id}.json").read_bytes() != clean_bytes:
        violated.append(CLEAN_RECORD)
    if not board_wav.exists() or board_wav.read_bytes() != board_bytes:
        violated.append(CLEAN_WAV)
    if control_wav != scratch / wav or control_wav == board_wav:
        violated.append(OWN_WAV)
    if control_pcm is None or not np.array_equal(control_pcm, _rendered(case_id, SECOND)):
        violated.append(OWN_PCM)
    return {"violated": violated, "clean": clean, "control": control, "stated": stated}


@pytest.mark.parametrize("case_id,wav,key", WRITERS)
def test_an_injected_control_leaves_the_clean_board_wav_and_record_untouched(
        board, tmp_path, case_id, wav, key):
    """The issue's reproduction, through the CLI: a clean run, then the control
    into a scratch directory of its own, outside the checkout."""
    scratch = tmp_path / "scratch"
    obs = _control_beside_its_record(board, scratch, case_id, wav, key)
    # the clean record and the WAV it names are byte-for-byte what they were
    assert obs["violated"] == []
    # ...and the control names a WAV of its own, beside its record, by where it
    # actually went
    assert obs["stated"] == str(scratch / wav)
    assert obs["stated"] != obs["clean"]["provenance"]["artefacts"][key]
    control, clean = obs["control"], obs["clean"]
    if key == "ours":
        # the control fired on the reference, through the real loader: its
        # decay is the clean reference's stretched by the injection's 1.2
        assert control["INJECTED_CONTROL"] == "REF_F0_20PCT"
        assert control["metrics"]["decay"]["valid"] and clean["metrics"]["decay"]["valid"]
        assert control["metrics"]["decay"]["reference"] == pytest.approx(
            1.2 * clean["metrics"]["decay"]["reference"], rel=1e-3)
        assert control["windowing"]["reference"]["onset_index"] \
            != clean["windowing"]["reference"]["onset_index"]
        assert control["audio"] == f"reference {rc.REF_MAIN['CP'][0]}; ours {obs['stated']}"
    else:
        # the ensemble measurement is identical for an injection; what makes
        # this run a control is the directory it was given
        assert "INJECTED_CONTROL" not in control
        assert control["audio"] == obs["stated"]


@pytest.mark.parametrize("case_id,wav,key", [
    pytest.param("D02A", "D02A-ours.wav", "ours", id="snare"),
    pytest.param("E1A", "E1A-mix.wav", "mix", id="ensemble"),
])
def test_model_wav_follows_its_json_when_an_rtl_anchor_is_present(
        board, tmp_path, case_id, wav, key):
    anchor = (rc.ROOT / "docs" / "scorecard" / "results" / f"{case_id}.json").read_bytes()
    assert json.loads(anchor)["engine"] == "integrated-rtl"
    scratch = tmp_path / "results"
    assert not scratch.is_relative_to(rc.ROOT)

    injected = ("--inject", "REF_F0_20PCT", "--results", str(scratch))
    for dest, wav_dir, hz, args in (
        (board["board"], board["shared"], FIRST, ()),
        (scratch, scratch / "model-twin", SECOND, injected),
    ):
        dest.mkdir()
        anchor_path = dest / f"{case_id}.json"
        anchor_path.write_bytes(anchor)
        board["renders"].hz = hz
        _cli(board, case_id, *args)

        assert anchor_path.read_bytes() == anchor
        twin = dest / "model-twin" / anchor_path.name
        record = json.loads(twin.read_bytes())
        assert _measured(record) and record["engine"] == "fixed-model"
        named = _resolved(record["provenance"]["artefacts"][key])
        assert named == wav_dir / wav
        np.testing.assert_array_equal(_pcm(named), _rendered(case_id, hz))
        assert not (dest / wav).exists()


@pytest.mark.parametrize("case_id,wav,key", WRITERS)
def test_the_shared_path_writer_is_a_control_that_stays_red(
        board, tmp_path, monkeypatch, case_id, wav, key):
    """The defect as it shipped, kept where the verdict is issued (#1414): a
    writer that ignores the results directory. This bench must go red on it.

    What each property sees:

    ==========================  =========================================
    property                    under a shared-path writer
    ==========================  =========================================
    the clean record           BLIND   the record always followed `--results`
    the clean WAV              MOVED   replaced by the control's render
    names its own WAV          MOVED   it names the board's file
    holds its own samples      BLIND   the board's file now holds them
    ==========================  =========================================

    The last row is why the samples are read back at all only once the path
    is known to be the control's own: content alone cannot tell whose file
    it is when the overwrite succeeded."""
    monkeypatch.setattr(rc, "wav_destination",
                        lambda results_dir, case_id, suffix, **kw: rc.AUDIO_OUT / f"{case_id}{suffix}")
    obs = _control_beside_its_record(board, tmp_path / "scratch", case_id, wav, key)
    assert obs["violated"] == [CLEAN_WAV, OWN_WAV]
    assert [p for p in PROPERTIES if p not in obs["violated"]] == [CLEAN_RECORD, OWN_PCM]


@pytest.mark.parametrize("case_id,wav,key", WRITERS)
def test_a_record_naming_a_wav_nobody_wrote_is_a_control_that_stays_red(
        board, tmp_path, monkeypatch, case_id, wav, key):
    """The property the control above leaves BLIND. Constructed at write time
    (rule 8), not taken from a bug this project made: a writer that states a
    path and writes nothing there. The record is then a lie a reader cannot
    see, and only reading the named file can find it. The other three
    properties are BLIND to it, by construction: the board is untouched and
    the stated path is the control's own."""
    def unwritten():
        monkeypatch.setattr(rc, "write_wav16", lambda path, x, sr: None)

    obs = _control_beside_its_record(board, tmp_path / "scratch", case_id, wav, key,
                                     arm=unwritten)
    assert obs["stated"] == str(tmp_path / "scratch" / wav)
    assert obs["violated"] == [OWN_PCM]
    assert [p for p in PROPERTIES if p not in obs["violated"]] == [
        CLEAN_RECORD, CLEAN_WAV, OWN_WAV]


@pytest.mark.parametrize("case_id,wav,key", WRITERS)
def test_a_control_cannot_adopt_the_shared_wav_directory_as_its_results(
        board, capsys, case_id, wav, key):
    """The input that defeats "an off-board WAV is written beside its
    results": `--results build/scorecard` IS that directory. The board guard
    does not see it, and the shared path is exactly where the control's WAV
    would then go -- under the clean board WAV's own name."""
    _cli(board, case_id)
    clean_wav = (board["shared"] / wav).read_bytes()
    before = sorted(p.name for p in board["shared"].iterdir())
    shared = board["shared"]
    board["renders"].hz = SECOND

    for spelling in (shared, shared / ".", shared / ".." / shared.name / "."):
        capsys.readouterr()
        assert _cli(board, case_id, "--inject", "REF_F0_20PCT",
                    "--results", str(spelling)) == 2
        err = capsys.readouterr().err
        assert "--inject refuses to write into" in err and rc.stated_path(shared) in err
        assert (shared / wav).read_bytes() == clean_wav
        assert sorted(p.name for p in shared.iterdir()) == before


@pytest.mark.parametrize("case_id,wav,key", WRITERS)
def test_injected_wav_alias_refused_before_overwriting_the_clean_recording(
        board, capsys, case_id, wav, key):
    trial = board["base"] / "trial"  # same filesystem as the published WAV for hardlink_to()
    trial.mkdir()
    trial_record = trial / f"{case_id}.json"
    board_record = board["board"] / trial_record.name
    if case_id == "E1A":
        anchor = (rc.ROOT / "docs/scorecard/results/E1A.json").read_bytes()
        assert json.loads(anchor)["engine"] == "integrated-rtl"
        board_record.parent.mkdir()
        board_record.write_bytes(anchor)
        trial_record.write_bytes(anchor)
        model_record = board_record.parent / "model-twin" / board_record.name
    else:
        model_record = board_record

    _cli(board, case_id)
    clean_record = model_record.read_bytes()
    clean = json.loads(clean_record)
    assert _measured(clean)
    published = _resolved(clean["provenance"]["artefacts"][key])
    assert published == board["shared"] / wav
    np.testing.assert_array_equal(_pcm(published), _rendered(case_id, FIRST))
    clean_wav = published.read_bytes()

    if case_id == "E1A":
        (trial / "model-twin").symlink_to(board["shared"], target_is_directory=True)
        candidate = trial / "model-twin" / wav
    else:
        candidate = trial / wav
        candidate.hardlink_to(published)
    assert candidate.samefile(published)
    board["renders"].hz = SECOND

    capsys.readouterr()
    status = _cli(board, case_id, "--inject", "REF_F0_20PCT", "--results", str(trial))
    assert published.read_bytes() == clean_wav, "the injected render overwrote the clean WAV"
    assert status == 2
    err = capsys.readouterr().err
    assert "--inject" in err and rc.stated_path(published) in err
    assert rc.stated_path(candidate) in err
    assert model_record.read_bytes() == clean_record
    assert not (candidate.parent / trial_record.name).exists()
    if case_id == "E1A":
        assert board_record.read_bytes() == trial_record.read_bytes() == anchor

    # Imported callers reach the writers without the CLI's batch preflight.
    case = next(c for c in rc.load_cases() if c["case_id"] == case_id)
    measured = rc.run_case(case, board["corpus"], "REF_F0_20PCT", results_dir=str(trial))
    assert measured["note"].startswith(f"REFUSED: {case_id}: --inject")
    assert published.read_bytes() == clean_wav


@pytest.mark.parametrize("case_id,wav,key", WRITERS)
def test_no_audio_still_writes_no_wav(board, tmp_path, case_id, wav, key):
    """`--no-audio` is preserved: no WAV, anywhere, and the record says so."""
    _cli(board, case_id)
    clean_wav = (board["shared"] / wav).read_bytes()

    scratch = tmp_path / "no-audio"
    _cli(board, case_id, "--no-audio", "--results", str(scratch))
    record = json.loads((scratch / f"{case_id}.json").read_text())
    assert record["provenance"]["artefacts"][key] == NO_AUDIO
    assert [p.name for p in scratch.iterdir()] == [f"{case_id}.json"]
    # on the board too: a `--no-audio` run replaces neither the WAV nor
    # anything beside it
    _cli(board, case_id, "--no-audio")
    assert (board["shared"] / wav).read_bytes() == clean_wav
    assert sorted(p.name for p in (board["shared"]).iterdir()) == [wav]


def test_a_caller_that_states_no_results_directory_gets_the_shared_path(board):
    """`tools/clap_d12a_probe.py::reproduce` calls `run_drum_case` positionally,
    for its numbers alone. That call is unchanged and still writes nothing.
    With audio kept and no results directory stated -- a caller that writes no
    record anywhere -- the WAV takes the established shared path, as
    `run_filter_case`'s curve does."""
    case = dict(case_id="D12A", subject="Clap / anchor",
                required_measurements="Burst timing; burst/tail ratio; decay")
    res = rc.run_drum_case(case, board["corpus"], "", keep_audio=False)
    assert res["provenance"]["artefacts"]["ours"] == NO_AUDIO
    assert _files_under(board["base"]) == []

    res = rc.run_drum_case(case, board["corpus"], "", keep_audio=True)
    stated = res["provenance"]["artefacts"]["ours"]
    assert stated == str((board["shared"] / "D12A-ours.wav").relative_to(rc.ROOT))
    assert np.array_equal(_pcm(_resolved(stated)), _rendered("D12A", FIRST))


def _assert_default_wav(path, record, cid, kind):
    assert record["case_id"] == cid and record["engine"] == rc.ENGINE, path
    if kind in rc.MODEL_WAV_KINDS:
        expected = rc.stated_path(rc.model_wav_destination(rc.RESULTS, kind, cid))
        assert record.get("audio") == expected, path
    else:
        expected = rc.stated_path(rc.wav_destination(rc.RESULTS, cid, rc.RENDERED_WAV_SUFFIX[kind]))
    key = "mix" if kind == "ensemble" else "ours"
    assert record["provenance"].get("artefacts", {}).get(key) == expected, path


def _check_live_model_wavs(kinds, required):
    """Discover renders in the live directory. Only a checked live file counts toward required."""
    checked = set()
    for path in sorted(rc.RESULTS.glob("*.json")):
        row = json.loads(path.read_text())
        if row["engine"] != rc.ENGINE:
            continue
        kind = rc.plan_for(row["case_id"])
        if kind not in kinds:
            continue
        key = "mix" if kind == "ensemble" else "ours"
        artefacts = row["provenance"].get("artefacts", {})
        if artefacts.get(key, NO_AUDIO) == NO_AUDIO:
            audio = row.get("audio", "")
            # A drum can still name its reference when the run wrote no model audio.
            if audio in ("", "none", NO_AUDIO) or (
                kind == "drum" and audio.endswith("; ours not written")
            ):
                continue
        _assert_default_wav(path, row, path.stem, kind)
        checked.add(path.stem)
    assert required <= checked, (
        f"Missing live fixed-model WAV records: {sorted(required - checked)}"
    )


def _check_published_rendered_wavs():
    _check_live_model_wavs(rc.RENDERED_WAV_SUFFIX, {"D12A", "E1B", "E2A"})

    twin = rc.RESULTS.parent / "ensemble-e1a/rtl/E1A-fixed-model-twin.json"
    _assert_default_wav(twin, json.loads(twin.read_text()), "E1A", "ensemble")
    toms = rc.RESULTS.parent / "tom-body-334/results"
    for cid in ("D03A", "D04A", "D05A", "D06A", "D07A", "D08A"):
        path = toms / f"{cid}.json"
        _assert_default_wav(path, json.loads(path.read_text()), cid, "drum")


def test_every_published_fixed_model_record_names_the_wav_a_default_run_writes():
    _check_published_rendered_wavs()


def _copy_published_results(tmp_path, monkeypatch, *archives):
    results = shutil.copytree(rc.RESULTS, tmp_path / "results")
    for archive in archives:
        (tmp_path / archive).symlink_to(rc.RESULTS.parent / archive, target_is_directory=True)
    monkeypatch.setattr(rc, "RESULTS", results)
    return results


@pytest.mark.parametrize("cid,key", [("D01A", "ours"), ("E1A", "mix")])
def test_new_live_render_must_name_its_default_wav(tmp_path, monkeypatch, cid, key):
    results = _copy_published_results(tmp_path, monkeypatch, "ensemble-e1a", "tom-body-334")
    live = results / f"{cid}.json"
    if cid == "D01A":
        row = json.loads(live.read_text())
        row["provenance"]["artefacts"][key] = "build/scorecard/D01A-ours.wav"
        row["audio"] = "reference bd8/BD5050.WAV; ours build/scorecard/D01A-ours.wav"
    else:
        twin = tmp_path / "ensemble-e1a/rtl/E1A-fixed-model-twin.json"
        row = json.loads(twin.read_text())
    live.write_text(json.dumps(row))
    _check_published_rendered_wavs()

    row["provenance"]["artefacts"][key] = f"build/scorecard/{cid}-renamed.wav"
    live.write_text(json.dumps(row))
    with pytest.raises(AssertionError, match=live.name):
        _check_published_rendered_wavs()


def test_the_wav_destination_is_the_shared_path_on_the_board_and_beside_the_results_off_it(
        tmp_path):
    """The rule both writers resolve through, pinned apart from any run -- and
    pinned to the F1 curve's: one directory decision, three artefacts."""
    board, shared = rc.RESULTS, rc.AUDIO_OUT
    names = (("D12A", "-ours.wav"), ("E1A", "-mix.wav"))
    for cid, suffix in names:
        assert rc.wav_destination(board, cid, suffix) == shared / f"{cid}{suffix}"
        # the board reached by another spelling is still the board
        assert rc.wav_destination(board / "." / ".." / board.name, cid, suffix) \
            == shared / f"{cid}{suffix}"
        # a caller that states no results directory gets the established path
        assert rc.wav_destination(None, cid, suffix) == shared / f"{cid}{suffix}"
    # off the board: beside the results, whatever directory that is
    inside = rc.ROOT / "build" / "candidate"
    for results_dir in (inside, tmp_path):
        assert not tmp_path.is_relative_to(rc.ROOT)
        for cid, suffix in names:
            assert rc.wav_destination(results_dir, cid, suffix) == results_dir / f"{cid}{suffix}"
            assert rc.wav_destination(results_dir, cid, suffix).parent \
                == rc.response_destination(results_dir, "F1A").parent
    # ...even when that directory is the shared one. The path is then the clean
    # board WAV's own, which is why an injected run is refused that directory.
    assert rc.wav_destination(shared, "D12A", "-ours.wav") == shared / "D12A-ours.wav"

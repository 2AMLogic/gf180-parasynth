#!/usr/bin/env python3
"""Where a mono family's model WAV goes, and whose it is once written (#1422).

`results/M5B.json` names `build/scorecard/M5B-model.wav` and `results/M1A.json`
names `docs/scorecard/mono-m1a-miniv3/m1a-model.wav` in
`provenance.artefacts.ours`: the render each mono row's numbers were taken
from. Until #1422 both mono writers chose that path themselves and neither was
told the results directory, so

    tools/run_case.py M5B --results /tmp/mono-m5b

put its record in `/tmp/mono-m5b/` and its WAV on the published path, and the
board's row then pointed at another run's render. #1414 was the same defect in
the drum and ensemble writers, and `tools/test_wav_destination.py` is the
pattern this file follows.

THE INPUT #1414 COULD NOT HAVE. A clean run carries no `--inject`, so none of
the refusals `run_case.main` gives an injected control applies to it, and
`--results build/scorecard` (M5B) or `--results docs/scorecard/mono-m1a-miniv3`
(M1A) is a legal spelling whose "beside the record" IS the published WAV. Such
a run is refused here: exit 2 before anything is measured, with the published
bytes untouched.

WHAT IS THE SHIPPED CODE AND WHAT IS A DOUBLE. Both writers run through
`run_case.main()`. The routing to a family's runner, the destination rule and
its refusal, M5B's whole measurement (`mono_m5a_score.measure`: its render, its
analysis, its WAV writer), M1A's reference loader, comparison, WAV writer and
provenance, the record writer (`write_result`) and every field of each record
are the shipped code. Five things are doubled, because this file is about file
ownership and not about a synthesizer:

  * M1A's render (`render_mono_fx` as its runner calls it) is a synthetic
    three-note phrase, the shape `tools/test_mono_m1a_score.py` gives the same
    seam. The real one is slow and this file runs M1A repeatedly;
  * M1A's estimator qualification (`mono_m1a_score.qualify_envelope_basis`).
    The fixture substitutes the qualification report for these destination tests;
  * the published places (`RESULTS`, `AUDIO_OUT`, M1A's published WAV) are a
    scratch board under the checkout's gitignored `build/`, and the fixture
    asserts at teardown that the two real published WAVs are what they were;
  * the sentinel WAVs seeded on those published paths;
  * `base_check`, whose subject is this checkout's relation to origin/main.

The doubles substitute INPUTS and PLACES. No destination is stubbed, no writer
is replaced, and the test writes no record field: those are the things under
test.

WHY A SENTINEL AND NOT A COMPARISON OF TWO RENDERS. A clean run renders what
the board's own run rendered, so replacing the published WAV with it changes
nothing visible in the bytes -- the issue could not show M1A rewriting its
tracked WAV for exactly that reason. Each published path is seeded with a
16-sample WAV first, so "the published WAV is byte-identical" can be read
without knowing what the model sounds like.

THE BASELINE AND `--no-audio` (#1430). `main` measures an `--expect` control's
clean baseline with `keep_audio=False` and no results directory, so the M5
writer took its own path -- and wrote to it whatever `keep_audio` said, because
`mono_m5a_score.measure` had no such switch. The `make controls` line for M5B
therefore left `build/scorecard/M5B-model.wav` present when it was absent
before: a clean render of the working tree's model, on the path `results/M5B.json`
names, with no record anywhere saying it was written. The writer now takes the
switch (`keep_audio`), the baseline writes no WAV, and a `--no-audio` run
states on its record that the WAV was not written. For those tests the
writer's own path is moved into the isolated board (`writers_own_path`),
because that is the path the baseline, and the control that reinstates the
old writer, write to.

WHAT A PASSING RUN HERE DOES NOT ESTABLISH. Any acoustic property of either
case: no test here reads a verdict, and `fail` from the model is as good as
`pass` provided the run reached its writers. That a run DID reach them is
asserted (`_measured`), because a refused run writes no WAV and would
otherwise pass an ownership check on nothing. Which WAV paths the PUBLISHED
records name is checked against the real module constants, apart from any run.
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import tempfile

import numpy as np
import pytest
from scipy.io import wavfile

import mono_m1a_score as bass
import mono_m5a_score as m5a
import run_case as rc
from test_wav_destination import (
    _assert_default_wav,
    _check_live_model_wavs,
    _copy_published_results,
)

SR = 48000
INJECT = "MONO_PITCH_UP_25_CENTS"             # the one control both mono families take
REFUSAL = "would put this run's model WAV"     # what separates this refusal from any other
REAL_RENDER = bass.lead.vf.render_mono_fx      # as imported, before any test doubles it
NO_AUDIO = "not written (--no-audio)"          # how a record states an omitted artefact
TAGGED = f"M5B-model-control-{INJECT}.wav"     # an M5B control's WAV, as it has always been named
#: Where the M5 writer's own path is published, as imported: the premise of
#: #1430's second item, which `writers_own_path` asserts before moving it.
REAL_SHARED = rc.AUDIO_OUT
#: `render_mono_fx` renders one phrase segment per call, so this is how many
#: calls the fixture's counter sees in one M5B measurement.
SEGMENTS = len(json.loads(m5a.MANIFESTS["M5B"].read_text())["timeline"]["segments"])

#: The two mono writers as `run_case.main` reaches each: the case, its plan
#: (`plan_for`), where the fixture keeps that family's published directory, the
#: published file name, and the name an injected control's WAV has always taken.
WRITERS = [pytest.param("M5B", "mono", "shared", "M5B-model.wav",
                        f"M5B-model-control-{INJECT}.wav", id="mono"),
           pytest.param("M1A", "mono-bass", "m1a", "m1a-model.wav",
                        f"M1A-{INJECT}-model.wav", id="mono-bass")]

#: What this file checks of an off-board run, and what a violation is called.
PUBLISHED_WAV = "the published WAV is byte-identical"
OWN_WAV = "the record names a WAV of its own, beside its record"
OWN_PCM = "that WAV is there, and is this run's render"
PROPERTIES = (PUBLISHED_WAV, OWN_WAV, OWN_PCM)


def _bass_phrase(cents: float = 0.0) -> np.ndarray:
    """The render double: M1A's three notes as 16-bit PCM, each a 5 ms attack
    into a 50 ms release, so the real analysis accepts them. `cents` is the
    invocation's tag: a control's phrase is 25 cents sharp."""
    t = np.arange(round(bass.reference.SECONDS * bass.SR)) / bass.SR
    audio = np.zeros_like(t)
    for event in bass.reference.EVENTS:
        dt = t - event["on_s"]
        env = np.clip(dt / .005, 0, 1)
        off = dt >= event["gate_s"]
        env[off] = np.exp(-(dt[off] - event["gate_s"]) / .05)
        hz = 440 * 2 ** ((event["note"] - 69 + cents / 100) / 12)
        audio += .1 * env * (np.sin(2 * np.pi * hz * t)
                             + .2 * np.sin(4 * np.pi * hz * t)
                             + .1 * np.sin(6 * np.pi * hz * t))
    return np.asarray(audio * 32768, dtype=np.int16)


def _bass_render(seq, seconds, voice) -> np.ndarray:
    """`render_mono_fx` as M1A's runner calls it: the phrase at the pitch the
    sequence asks for, so a control's WAV differs from a clean run's."""
    cents = round(100 * (seq[0][1] - bass.reference.EVENTS[0]["note"]), 6)
    return _bass_phrase(cents)


@pytest.fixture
def board(monkeypatch, tmp_path):
    """A board, a shared-WAV directory and an M1A directory of this test's
    own, plus the input doubles, so nothing here can reach
    `docs/scorecard/results`, `build/scorecard` or the tracked `m1a-model.wav`.

    Under the checkout's gitignored `build/`, in a directory nobody else can
    name, and removed afterwards. Under the ROOT rather than in a tmp_path
    because an on-board WAV is STATED relative to the checkout, and a directory
    outside it cannot be; what a run states is half of what is checked here.

    That nothing reached the real published paths is asserted at teardown,
    not assumed: a fixture that redirected the wrong name would let a red run
    rewrite the artefact these tests exist to protect, and go on looking like
    a clean test."""
    scratch_root = rc.ROOT / "build"
    scratch_root.mkdir(exist_ok=True)
    base = pathlib.Path(tempfile.mkdtemp(prefix="mono-wav-", dir=scratch_root))
    places = {"base": base, "board": base / "board", "shared": base / "shared",
              "m1a": base / "m1a", "cwd": base / "cwd", "refs": tmp_path / "refs",
              "renders": 0}
    places["cwd"].mkdir()          # the other working directory a relative --results is read from
    kept = {path: (path.read_bytes() if path.exists() else None)
            for path in (rc.AUDIO_OUT / "M5B-model.wav",
                         bass.MANIFEST.parent / "m1a-model.wav")}
    try:
        monkeypatch.setattr(rc, "RESULTS", places["board"])
        monkeypatch.setattr(rc, "AUDIO_OUT", places["shared"])
        # created if absent, so this file run against a tree without the fix
        # reports the behaviour there and not a missing name
        monkeypatch.setattr(bass, "PUBLISHED_MODEL_WAV",
                            places["m1a"] / "m1a-model.wav", raising=False)
        # `main` overwrites this module global; the double's value must not
        # outlive the test that asked for it
        monkeypatch.setattr(rc, "BASE_STATE", rc.BASE_STATE)
        monkeypatch.setattr(rc, "base_check",
                            lambda allow_stale=False: {"checked": False, "why": "a test double"})
        monkeypatch.setattr(bass, "qualify_envelope_basis", lambda report=None: {
            "note": "a test double: the estimator's known-answer suite did not run"})
        yield places
    finally:
        shutil.rmtree(base, ignore_errors=True)
        for path, before in kept.items():
            after = path.read_bytes() if path.exists() else None
            if after != before:
                if before is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_bytes(before)
                raise AssertionError(
                    f"this test rewrote the published WAV {rc.stated_path(path)} "
                    f"(restored): the fixture's redirection did not hold")


@pytest.fixture
def writers_own_path(board, monkeypatch) -> pathlib.Path:
    """The path `mono_m5a_score.measure` takes when it is told none, moved into
    the isolated board. It is `build/scorecard/<case>-model.wav` under the
    writer's own `ROOT` -- the published M5 WAV, asserted before it is moved,
    because that is what makes #1430's second item a published-artefact write.
    The control baseline and a caller that states no destination reach the
    writer this way; relocating it keeps those writes, and the control that
    reinstates the old writer, off the real published path."""
    assert m5a.ROOT / "build" / "scorecard" == REAL_SHARED
    board["shared"].mkdir(exist_ok=True)    # the published directory is there;
    own = board["base"] / "root"            # its WAV is what a test adds or not
    (own / "build").mkdir(parents=True)
    (own / "build" / "scorecard").symlink_to(board["shared"])
    monkeypatch.setattr(m5a, "ROOT", own)
    return board["shared"] / "M5B-model.wav"


@pytest.fixture(scope="session")
def m5b_with_audio(tmp_path_factory) -> dict:
    """One real, audio-enabled M5B run through the CLI into a directory of its
    own: what a run without audio must be shown to compute. Its WAV lands
    beside its record, so no published place is involved."""
    out = tmp_path_factory.mktemp("m5b-with-audio")
    with pytest.MonkeyPatch.context() as patched:
        patched.setattr(rc, "BASE_STATE", rc.BASE_STATE)
        patched.setattr(rc, "base_check",
                        lambda allow_stale=False: {"checked": False, "why": "a test double"})
        rc.main(["M5B", "--results", str(out)])
    record = json.loads((out / "M5B.json").read_text())
    assert _measured(record), record.get("note")
    assert record["provenance"]["artefacts"]["ours"] == str(out / "M5B-model.wav")
    return record


def _cli(board, monkeypatch, case_id: str, *argv) -> int:
    """`run_case.main` for one case, against the isolated board. M1A's render
    is the double and M5B's is the real one; both are counted in
    `board["renders"]`, so a test can tell a refusal that came before any
    measurement from one that came after."""
    def counted(seq, seconds, voice):
        board["renders"] += 1
        return _bass_render(seq, seconds, voice) if case_id == "M1A" \
            else REAL_RENDER(seq, seconds, voice)
    monkeypatch.setattr(bass.lead.vf, "render_mono_fx", counted)
    return rc.main([case_id, "--refs", str(board["refs"]), *argv])


def _seed(published: pathlib.Path) -> bytes:
    """Put a sentinel on a published path -- a valid WAV of 16 samples, which
    no render of a 7.5 s phrase resembles in bytes or in length -- and return
    its bytes."""
    published.parent.mkdir(parents=True, exist_ok=True)
    wavfile.write(str(published), SR, np.arange(16, dtype="<i2"))
    return published.read_bytes()


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
    assert sr == SR and x.dtype == np.int16 and x.ndim == 1, (path, sr, x.dtype)
    return x


def _is_render(path: pathlib.Path, phrase: np.ndarray | None = None) -> bool:
    """Whether `path` holds a phrase rather than a sentinel: at least seven
    seconds of int16, and equal to `phrase` where the test knows the render --
    over the WAV's whole length, because M1A's writer trims its render to the
    reference's length (the frozen recording has its host latency removed)."""
    try:
        x = _pcm(path)
    except (AssertionError, OSError, ValueError):
        return False
    if x.size < 7 * SR:
        return False
    return phrase is None or (x.size <= phrase.size and np.array_equal(x, phrase[:x.size]))


def _measured(record: dict) -> bool:
    """The run reached its writers: no runner refused before them. A refusal
    writes a record with no artefact, so a test that read one as a pass would
    be green on nothing -- `no verdict` is not ownership."""
    return not record.get("note", "").startswith("REFUSED") and "traceback" not in record


def _rendered(case_id: str) -> np.ndarray | None:
    """What a clean run of `case_id` writes, where the test knows it."""
    return _bass_phrase() if case_id == "M1A" else None


def _off_board(board, monkeypatch, trial: pathlib.Path, cid: str, key: str,
               name: str) -> dict:
    """A clean run into a directory of its own, outside the checkout, and what
    was observed of it: the properties violated, in `PROPERTIES` order, and the
    record. The same run is the bench the control at the end of this file must
    turn red."""
    assert not trial.is_relative_to(rc.ROOT)
    published = board[key] / name
    before = published.read_bytes()
    _cli(board, monkeypatch, cid, "--results", str(trial))
    record = json.loads((trial / f"{cid}.json").read_text())
    assert _measured(record), record.get("note")
    stated = record["provenance"]["artefacts"]["ours"]
    named = _resolved(stated)
    violated = []
    if published.read_bytes() != before:
        violated.append(PUBLISHED_WAV)
    if named != trial / name:
        violated.append(OWN_WAV)
    if not _is_render(named, _rendered(cid)):
        violated.append(OWN_PCM)
    return {"violated": violated, "record": record, "stated": stated}


def _spellings(board, key: str, name: str) -> list[tuple[str, str, pathlib.Path]]:
    """Spellings of the published directory or a link to its WAV, with the
    candidate directory that the refusal must identify."""
    published = board[key]
    relative = {"shared": "build/scorecard",
                "m1a": "docs/scorecard/mono-m1a-miniv3"}[key]
    through = board["cwd"] / relative
    through.parent.mkdir(parents=True)
    through.symlink_to(published, target_is_directory=True)
    soft = board["base"] / "soft"
    soft.mkdir()
    (soft / name).symlink_to(published / name)
    hard = board["base"] / "hard"
    hard.mkdir()
    os.link(published / name, hard / name)
    hop = board["cwd"] / "hop"
    board["board"].mkdir()
    hop.symlink_to(board["board"], target_is_directory=True)
    via_parent = pathlib.Path("hop") / ".." / published.name
    assert via_parent.resolve() == published
    assert pathlib.Path(os.path.abspath(via_parent)) != published
    return [(f"relative, through a symlink ({relative}, from another cwd)", relative, through),
            ("the directory itself", str(published), published),
            ("with .. in it", str(published / ".." / published.name), published / ".." / published.name),
            ("a symlink to the published WAV", str(soft), soft),
            ("a hard link to the published WAV", str(hard), hard),
            (".. after a symlink", str(via_parent), board["cwd"] / via_parent)]


@pytest.mark.parametrize("cid,kind,key,name,tagged", WRITERS)
def test_a_clean_run_cannot_put_its_wav_on_the_published_path(
        board, monkeypatch, capsys, cid, kind, key, name, tagged):
    """The input #1414 could not have, through the CLI: a CLEAN run whose
    `--results` names its own published WAV several ways. Each is refused
    before anything is measured, says which case, which published WAV and which
    destination, and leaves the sentinel and the directory as they were."""
    published = board[key] / name
    before = _seed(published)
    monkeypatch.chdir(board["cwd"])
    for label, spelling, said in _spellings(board, key, name):
        capsys.readouterr()
        code = _cli(board, monkeypatch, cid, "--results", spelling)
        err = capsys.readouterr().err
        assert code == 2, (label, err)
        assert "REFUSED" in err and cid in err and REFUSAL in err, (label, err)
        assert f"at {rc.stated_path(said / name)}," in err, (label, err)
        assert f"published WAV {rc.stated_path(published.resolve())} " in err, (label, err)
        # the board row that names the published WAV
        assert rc.stated_path(rc.RESULTS / f"{cid}.json") in err, (label, err)
        assert published.read_bytes() == before, label
        # ...and, refused before the batch began, nothing was measured and no
        # record was written beside it
        assert board["renders"] == 0, label
        assert sorted(p.name for p in published.parent.iterdir()) == [name], label


@pytest.mark.parametrize("cid,kind,key,name,tagged", WRITERS)
def test_a_clean_off_board_run_writes_its_wav_beside_its_record(
        board, monkeypatch, tmp_path, cid, kind, key, name, tagged):
    """The issue's reproduction, through the CLI, repaired: the record and the
    WAV land together in a directory outside the checkout, the record states
    the WAV absolutely because there is no checkout-relative spelling of it,
    and the published WAV is not touched."""
    trial = tmp_path / "trial"
    _seed(board[key] / name)
    obs = _off_board(board, monkeypatch, trial, cid, key, name)
    assert obs["violated"] == []
    assert obs["stated"] == str(trial / name)
    assert obs["record"]["audio"] == obs["stated"]
    assert sorted(p.name for p in trial.iterdir()) == sorted([f"{cid}.json", name])


@pytest.mark.parametrize("cid,kind,key,name,tagged", WRITERS)
def test_off_board_rtl_anchor_keeps_the_model_wav_with_its_twin(
        board, monkeypatch, tmp_path, cid, kind, key, name, tagged):
    published = board[key] / name
    before = _seed(published)
    # M1A's requested directory would collide if the WAV ignored the twin.
    trial = published.parent if cid == "M1A" else tmp_path
    anchor = trial / f"{cid}.json"
    if cid == "M5B":
        shutil.copyfile(rc.ROOT / "docs/scorecard/mono-pulse-2x/rtl/M5B.json", anchor)
    else:
        anchor.write_text(json.dumps({"case_id": cid, "engine": "integrated-rtl"}))
    anchor_bytes = anchor.read_bytes()
    _cli(board, monkeypatch, cid, "--results", str(trial))
    assert anchor.read_bytes() == anchor_bytes
    record_path = trial / "model-twin" / anchor.name
    assert record_path.is_file(), "expected a model JSON beside the preserved anchor"
    record = json.loads(record_path.read_text())
    assert _measured(record), record.get("note")
    wav = record_path.with_name(name)
    assert record["audio"] == record["provenance"]["artefacts"]["ours"]
    assert _resolved(record["audio"]) == wav
    assert _is_render(wav, _rendered(cid))
    assert list(trial.glob("*.wav")) == ([published] if trial == published.parent else [])
    assert published.read_bytes() == before


@pytest.mark.parametrize("link_kind", ["symlink", "hardlink"])
@pytest.mark.parametrize("through_missing_parent", [False, True], ids=["direct", "missing-parent"])
def test_collision_in_model_twin_refuses_before_render(
        board, monkeypatch, tmp_path, capsys, through_missing_parent, link_kind):
    published = board["m1a"] / "m1a-model.wav"
    before = _seed(published)
    # A hard link must share the published WAV's filesystem.
    trial_dir = (board["base"] if link_kind == "hardlink" else tmp_path) / "trial"
    trial_dir.mkdir()
    anchor = trial_dir / "M1A.json"
    anchor_bytes = b'{"case_id":"M1A","engine":"integrated-rtl"}'
    anchor.write_bytes(anchor_bytes)
    twin = trial_dir / "model-twin"
    if link_kind == "symlink":
        twin.symlink_to(published.parent, target_is_directory=True)
    else:
        twin.mkdir()
        (twin / published.name).hardlink_to(published)
    trial = trial_dir / "missing" / ".." if through_missing_parent else trial_dir
    assert _cli(board, monkeypatch, "M1A", "--results", str(trial)) == 2
    err = capsys.readouterr().err
    assert REFUSAL in err and rc.stated_path(trial / twin.name / published.name) in err
    assert board["renders"] == 0
    assert anchor.read_bytes() == anchor_bytes
    assert published.read_bytes() == before
    assert not (twin / anchor.name).exists()
    assert not (trial_dir / "missing").exists()


@pytest.mark.parametrize("cid,kind,key,name,tagged", WRITERS)
def test_a_run_on_the_board_keeps_the_published_wav(
        board, monkeypatch, cid, kind, key, name, tagged):
    """The off-board rule does not move the board's own path: a default run
    still replaces the published WAV, under the name the published record
    states, and its record lands on the board."""
    published = board[key] / name
    _seed(published)
    _cli(board, monkeypatch, cid)
    record = json.loads((board["board"] / f"{cid}.json").read_text())
    assert _measured(record), record.get("note")
    assert record["provenance"]["artefacts"]["ours"] == str(published.relative_to(rc.ROOT))
    assert record["audio"] == record["provenance"]["artefacts"]["ours"]
    assert _is_render(published, _rendered(cid))
    assert sorted(p.name for p in board["board"].iterdir()) == [f"{cid}.json"]
    assert sorted(p.name for p in published.parent.iterdir()) == [name]


@pytest.mark.parametrize("cid,kind,key,name,tagged", WRITERS)
def test_an_injected_control_keeps_its_tagged_wav(
        board, monkeypatch, tmp_path, cid, kind, key, name, tagged):
    """A control's WAV is unchanged by this issue: tagged with its injection
    under the shared directory, where the control's record names it, and never
    the clean published name."""
    published = board[key] / name
    before = _seed(published)
    control = tmp_path / "control"
    _cli(board, monkeypatch, cid, "--inject", INJECT, "--results", str(control))
    record = json.loads((control / f"{cid}.json").read_text())
    assert _measured(record), record.get("note")
    # how a control's record says so is each runner's own and is not changed here
    if cid == "M1A":
        assert record["diagnostics"]["configuration"]["inject"] == INJECT
    else:
        assert record["INJECTED_CONTROL"] == INJECT
    wav = board["shared"] / tagged
    assert record["provenance"]["artefacts"]["ours"] == str(wav.relative_to(rc.ROOT))
    assert _is_render(wav, _bass_phrase(25.0) if cid == "M1A" else None)
    assert published.read_bytes() == before
    assert sorted(p.name for p in control.iterdir()) == [f"{cid}.json"]


def test_m1a_without_audio_writes_no_wav_even_into_its_published_directory(
        board, monkeypatch, capsys):
    """`--no-audio` is preserved for M1A, and it is what makes a colliding
    `--results` harmless: with no WAV to write there is nothing the refusal
    protects, so the run proceeds, and only its record lands there."""
    published = board["m1a"] / "m1a-model.wav"
    before = _seed(published)
    capsys.readouterr()
    _cli(board, monkeypatch, "M1A", "--no-audio", "--results", str(published.parent))
    assert REFUSAL not in capsys.readouterr().err
    record = json.loads((published.parent / "M1A.json").read_text())
    assert _measured(record), record.get("note")
    assert "ours" not in record["provenance"]["artefacts"] and record["audio"] == ""
    assert record["diagnostics"]["model_audio_sha256"] is None
    # the sentinel is still the only WAV there: nothing was written over it or
    # beside it
    assert published.read_bytes() == before
    assert sorted(p.name for p in published.parent.iterdir()) \
        == sorted(["M1A.json", "m1a-model.wav"])


def test_m5_without_audio_writes_no_wav_even_into_its_published_directory(
        board, monkeypatch, capsys, writers_own_path, m5b_with_audio):
    """`--no-audio` now means for M5 what it has meant for every other family
    (#1430, AC 3), and it is what makes a colliding `--results` harmless: with
    no WAV to write there is nothing the refusal protects, so the run proceeds
    and only its record lands there, stating that the WAV was not written.

    The same metrics and the same score, against a real audio-enabled run of
    this tree (`m5b_with_audio`) rather than against itself."""
    published = writers_own_path
    before = _seed(published)
    capsys.readouterr()
    _cli(board, monkeypatch, "M5B", "--no-audio", "--results", str(published.parent))
    assert REFUSAL not in capsys.readouterr().err
    record = json.loads((published.parent / "M5B.json").read_text())
    assert _measured(record), record.get("note")
    assert board["renders"] == SEGMENTS             # one measurement was made
    assert record["provenance"]["artefacts"]["ours"] == NO_AUDIO
    assert record["audio"] == NO_AUDIO
    assert published.read_bytes() == before
    assert sorted(p.name for p in published.parent.iterdir()) \
        == sorted(["M5B.json", "M5B-model.wav"])

    loud = m5b_with_audio
    assert loud["provenance"]["artefacts"]["ours"] != NO_AUDIO
    assert record["metrics"] == loud["metrics"]
    assert record["provenance"]["outcome_code"] == loud["provenance"]["outcome_code"]
    case = next(c for c in rc.load_cases() if c["case_id"] == "M5B")
    assert rc.verdict_of(case, record)[:2] == rc.verdict_of(case, loud)[:2]


def test_the_control_without_audio_writes_no_wav_on_either_leg(
        board, monkeypatch, capsys, tmp_path, writers_own_path):
    """Explicit `--no-audio` with an injected run, as the `make controls` line
    would spell it (AC 4): the baseline and the control both reach the writer,
    the control still fires and is still labelled, and neither leg leaves a
    WAV -- not the published one, and not the injection-tagged one."""
    published = writers_own_path
    assert not published.exists()
    control = tmp_path / "control"
    capsys.readouterr()
    code = _cli(board, monkeypatch, "M5B", "--inject", INJECT, "--no-audio",
                "--results", str(control), "--expect", "changed")
    out = capsys.readouterr().out
    assert code == 0 and "control fired" in out, out
    record = json.loads((control / "M5B.json").read_text())
    assert _measured(record), record.get("note")
    assert record["INJECTED_CONTROL"] == INJECT
    assert record["provenance"]["artefacts"]["ours"] == NO_AUDIO
    assert board["renders"] == 2 * SEGMENTS       # two measurements, no WAV
    assert list(board["shared"].iterdir()) == []
    assert [p.name for p in control.iterdir()] == ["M5B.json"]


@pytest.mark.parametrize("published_wav", ["absent", "a sentinel"])
def test_the_control_baseline_writes_neither_a_record_nor_a_model_wav(
        board, monkeypatch, capsys, tmp_path, writers_own_path, published_wav):
    """The `make controls` line for M5B, exactly as the Makefile spells it
    (#1430, AC 4). Its clean baseline measures the case with no results
    directory and `keep_audio=False`; until #1430 the M5 writer ignored the
    latter and wrote the published WAV. The issue saw it leave that WAV present
    when it was absent before, so both states are checked: absent, and a
    sentinel. The control itself is unchanged: it fires, its result is
    labelled, and with audio enabled its WAV keeps the injection-tagged name."""
    published = writers_own_path
    before = None if published_wav == "absent" else _seed(published)
    assert published.exists() == (before is not None)
    ledger = rc.holdout.LEDGER
    ledger_before = ledger.read_bytes() if ledger.exists() else None
    control = tmp_path / "control"
    tagged = board["shared"] / TAGGED

    capsys.readouterr()
    code = _cli(board, monkeypatch, "M5B", "--inject", INJECT,
                "--results", str(control), "--expect", "changed")
    out = capsys.readouterr().out
    assert code == 0 and "control fired" in out, out
    record = json.loads((control / "M5B.json").read_text())
    assert _measured(record), record.get("note")
    assert record["INJECTED_CONTROL"] == INJECT
    # the baseline still measured the case -- two measurements of a
    # `SEGMENTS`-segment phrase: baseline, then control -- and consumed no
    # holdout reading
    assert board["renders"] == 2 * SEGMENTS
    assert "ledger_read" not in record.get("holdout", {})
    assert (ledger.read_bytes() if ledger.exists() else None) == ledger_before
    # (stated by where it is: this fixture moves the writer's ROOT, so a WAV
    # routed by `run_case` is stated absolutely here; the relative spelling is
    # `test_an_injected_control_keeps_its_tagged_wav`'s to check)
    assert _resolved(record["provenance"]["artefacts"]["ours"]) == tagged
    assert _is_render(tagged)
    assert [p.name for p in control.iterdir()] == ["M5B.json"]
    # absent before, absent after; a sentinel before, the same sentinel after
    assert (published.read_bytes() if published.exists() else None) == before
    assert sorted(p.name for p in published.parent.iterdir()) == sorted(
        [TAGGED] + ([published.name] if before is not None else []))


#: What the no-WAV bench checks, in the order it checks them, and what a
#: violation is called.
QUIET_RUN = "`--no-audio` into the published directory left the published WAV byte-identical"
STATED = "a `--no-audio` record states that its WAV was not written"
BASELINE = "the control's baseline left the published WAV byte-identical"
SILENT = (QUIET_RUN, STATED, BASELINE)


def _no_wav_bench(board, monkeypatch, capsys, tmp_path, published) -> list[str]:
    """Two invocations that write no WAV once the writer honours `keep_audio`:
    `--no-audio` into the published directory, then the `make controls` line,
    whose baseline reaches the writer with no destination. Returns the
    properties of `SILENT` that were violated."""
    before = _seed(published)
    violated = []
    capsys.readouterr()
    _cli(board, monkeypatch, "M5B", "--no-audio", "--results", str(published.parent))
    record_path = published.parent / "M5B.json"
    record = json.loads(record_path.read_text()) if record_path.is_file() else {}
    if published.read_bytes() != before:
        violated.append(QUIET_RUN)
    if record.get("provenance", {}).get("artefacts", {}).get("ours") != NO_AUDIO:
        violated.append(STATED)
    _seed(published)
    capsys.readouterr()
    _cli(board, monkeypatch, "M5B", "--inject", INJECT,
         "--results", str(tmp_path / "control"), "--expect", "changed")
    if published.read_bytes() != before:
        violated.append(BASELINE)
    return violated


def test_the_unconditional_m5_write_is_a_control_that_stays_red(
        board, monkeypatch, capsys, tmp_path, writers_own_path):
    """The defect as #1430 found it in the M5 writer, kept where the verdict is
    issued: `measure` writes its WAV whatever `keep_audio` says. The bench is
    the two green invocations above, put through the same fixture; under this
    writer every property it reads must move.

        =====================================================  =====================
        property                                              unconditional writer
        =====================================================  =====================
        `--no-audio` left the published WAV byte-identical   MOVED   it is replaced
        a `--no-audio` record says the WAV was not written   MOVED   it names the WAV
        the baseline left the published WAV byte-identical   MOVED   it is replaced
        =====================================================  =====================

    No property is blind to this control. The record is read as well as the
    filesystem because the two can fail apart: a writer that skips the WAV and
    still names one moves only the second row, and only the record sees it."""
    published = writers_own_path
    real = m5a.measure

    def unconditional(**kwargs):
        return real(**{**kwargs, "keep_audio": True})

    monkeypatch.setattr(m5a, "measure", unconditional)
    assert _no_wav_bench(board, monkeypatch, capsys, tmp_path, published) == list(SILENT)


@pytest.mark.parametrize("cid,kind,key,name,tagged", WRITERS)
def test_the_shared_path_writer_is_a_control_that_stays_red(
        board, monkeypatch, tmp_path, capsys, cid, kind, key, name, tagged):
    """The defect as it shipped, kept where the verdict is issued (#1422): a
    writer that writes the published WAV whatever `--results` says, and is
    never refused it. Both benches above must go red on it.

    What each property sees:

    ==========================  =========================================
    property                    under a shared-path writer
    ==========================  =========================================
    the published WAV           MOVED   replaced by this run's render
    names a WAV of its own      MOVED   it names the published path, truly
    that WAV is this run's      BLIND   the published file now holds it
    ==========================  =========================================

    The last row is why the content is read back only once the path is known
    to be the run's own: content alone cannot tell whose file it is when the
    overwrite succeeded."""
    monkeypatch.setattr(rc, "model_wav_destination",
                        lambda results_dir, kind, case_id, **_:
                        rc.AUDIO_OUT / f"{case_id}-model.wav" if kind == "mono"
                        else bass.PUBLISHED_MODEL_WAV)
    published = board[key] / name
    _seed(published)
    obs = _off_board(board, monkeypatch, tmp_path / "trial", cid, key, name)
    assert obs["violated"] == [PUBLISHED_WAV, OWN_WAV]
    assert [p for p in PROPERTIES if p not in obs["violated"]] == [OWN_PCM]
    # the record is not lying: it names the file the writer really wrote
    assert obs["record"]["provenance"]["artefacts"]["ours"] == rc.stated_path(published)

    _seed(published)
    capsys.readouterr()
    _cli(board, monkeypatch, cid, "--results", str(published.parent))
    assert REFUSAL not in capsys.readouterr().err
    assert _is_render(published, _rendered(cid))         # the sentinel is gone
    assert (published.parent / f"{cid}.json").exists()  # and the run went ahead


def test_the_model_wav_destination_is_the_published_path_on_the_board_and_beside_the_results_off_it(
        tmp_path):
    """The rule both mono writers resolve through, pinned apart from any run --
    and pinned to the drum and ensemble writers': one decision about what the
    board is."""
    m1a = bass.PUBLISHED_MODEL_WAV
    assert m1a == bass.MANIFEST.parent / "m1a-model.wav"
    for kind, cid, published in (("mono", "M5B", rc.AUDIO_OUT / "M5B-model.wav"),
                                 ("mono-bass", "M1A", m1a)):
        assert rc.published_model_wav(kind, cid) == published
        assert rc.model_wav_destination(rc.RESULTS, kind, cid) == published
        # the board reached by another spelling is still the board
        assert rc.model_wav_destination(rc.RESULTS / "." / ".." / rc.RESULTS.name, kind, cid) \
            == published
        # off the board: beside the results, whatever directory that is
        inside = rc.ROOT / "build" / "candidate"
        assert not tmp_path.is_relative_to(rc.ROOT)
        for results_dir in (inside, tmp_path):
            assert rc.model_wav_destination(results_dir, kind, cid) \
                == results_dir.resolve() / published.name
            assert rc.model_wav_destination(results_dir, kind, cid).parent \
                == rc.wav_destination(results_dir, cid, "-x.wav").parent.resolve()
    # ...but not when that is the case's own published WAV, however reached
    with pytest.raises(rc.Refused, match=REFUSAL):
        rc.model_wav_destination(rc.AUDIO_OUT, "mono", "M5B")
    with pytest.raises(rc.Refused, match=REFUSAL):
        rc.model_wav_destination(m1a.parent, "mono-bass", "M1A")
    # another family's published directory is an ordinary off-board directory
    assert rc.model_wav_destination(rc.AUDIO_OUT, "mono-bass", "M1A") \
        == rc.AUDIO_OUT.resolve() / "m1a-model.wav"
    assert rc.model_wav_destination(m1a.parent, "mono", "M5B") \
        == m1a.parent.resolve() / "M5B-model.wav"
    # an M1A run that writes no WAV has no destination to collide
    assert rc.model_wav_destination(m1a.parent, "mono-bass", "M1A", keep_audio=False) is None
    # ...and since #1430 neither has an M5 run: its writer has the switch too
    assert rc.model_wav_destination(rc.AUDIO_OUT, "mono", "M5B", keep_audio=False) is None


def _check_published_mono_wavs():
    _check_live_model_wavs(rc.MODEL_WAV_KINDS, {"M1A", "M5B"})
    repro = rc.RESULTS.parent / "m5-artifacts-333/repro"
    for cid in ("M5A", "M5B"):
        path = repro / f"model-{cid}.json"
        _assert_default_wav(path, json.loads(path.read_text()), cid, rc.plan_for(cid))


def test_every_published_mono_record_names_the_wav_a_default_run_writes():
    _check_published_mono_wavs()


@pytest.mark.parametrize("damage", ["renamed", "missing"])
def test_archived_m5b_cannot_mask_a_renamed_live_wav(tmp_path, monkeypatch, damage):
    results = _copy_published_results(tmp_path, monkeypatch, "m5-artifacts-333")
    _check_published_mono_wavs()

    live = results / "M5B.json"
    if damage == "missing":
        live.unlink()
    else:
        row = json.loads(live.read_text())
        row["audio"] = row["provenance"]["artefacts"]["ours"] = "build/scorecard/M5B-renamed.wav"
        live.write_text(json.dumps(row))
    with pytest.raises(AssertionError, match="M5B"):
        _check_published_mono_wavs()


@pytest.mark.parametrize("renamed", ["audio", "ours", "both"])
def test_new_live_mono_render_checks_both_paths(tmp_path, monkeypatch, renamed):
    results = _copy_published_results(tmp_path, monkeypatch, "m5-artifacts-333")
    archive = tmp_path / "m5-artifacts-333/repro/model-M5A.json"
    live = results / "M5A.json"
    shutil.copyfile(archive, live)
    _check_published_mono_wavs()

    row = json.loads(live.read_text())
    wrong = "build/scorecard/M5A-renamed"
    if renamed in ("audio", "both"):
        row["audio"] = wrong
    if renamed in ("ours", "both"):
        row["provenance"]["artefacts"]["ours"] = wrong
    live.write_text(json.dumps(row))
    with pytest.raises(AssertionError, match=live.name):
        _check_published_mono_wavs()


def test_a_caller_that_states_no_destination_gets_the_published_path(board, monkeypatch):
    """`tools/regen_m1a_units.py` re-labels the published M1A record through
    `mono_m1a_score.run()` from the cached audio, stating no destination, and
    `run_case.run_case` is called without a results directory by other tests.
    Both calls are unchanged: a caller that states none gets the established
    path."""
    case = next(c for c in rc.load_cases() if c["case_id"] == "M1A")
    monkeypatch.setattr(bass.lead.vf, "render_mono_fx", _bass_render)
    published = board["m1a"] / "m1a-model.wav"
    _seed(published)

    res = bass.run(case, "", True)
    assert res["provenance"]["artefacts"]["ours"] == rc.stated_path(published)
    assert _is_render(published, _bass_phrase())

    _seed(published)
    res = rc.run_case(case, board["refs"], "", True)
    assert res["provenance"]["artefacts"]["ours"] == rc.stated_path(published)
    assert _is_render(published, _bass_phrase())

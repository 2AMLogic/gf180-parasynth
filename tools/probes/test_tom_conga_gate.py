"""Known answers and controls for tools/probes/tom_conga_gate.py (#558).

The model-independent tests (float resonators, the judge's frozen rules) run
anywhere. The tests that need the Fischer corpus read GF180_TR808_FISCHER (the
checkout of tidalcycles/sounds-tr808-fischer at 85fbecf) and are SKIPPED with
that reason when it is absent -- a skip, never a pass.
"""
from __future__ import annotations

import copy
import json
import os
import pathlib
import subprocess
import sys

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import tom_conga_gate as tc  # noqa: E402

pg = tc.pg
REFS = os.environ.get("GF180_TR808_FISCHER")
needs_refs = pytest.mark.skipif(not REFS, reason="GF180_TR808_FISCHER (the Fischer corpus) not set")


# ---- H1: the onset phase of the transfer function moves the gate's pitch track ----
@pytest.mark.parametrize("s", list(tc.KA_VOICES))
def test_all_pole_onset_reads_no_pitch_excess(s):
    f0, tau = tc.KA_VOICES[s]
    assert abs(tc.ka_pitch_onset(f0, tau, (1.0,), s)["cents_first_15ms"][0]) < 8.0


@pytest.mark.parametrize("s", list(tc.KA_VOICES))
def test_dc_zero_onset_reads_a_pitch_excess(s):
    """A pure LTI resonator with NO pitch change at all reads > 30 cents at the
    gate's first frame once its transfer has a DC zero. So an onset excess in
    the gate's track is not, by itself, evidence of a pitch drop."""
    f0, tau = tc.KA_VOICES[s]
    bp = tc.ka_pitch_onset(f0, tau, (1.0, 0.0, -1.0), s)["cents_first_15ms"]
    assert bp[0] > 30.0
    assert bp[0] > bp[2]           # and it relaxes, like a drop would


# ---- H3: the body half of `impulse` is a floor meter ----
def test_body_impulse_rises_with_a_stationary_floor():
    f0, tau = tc.KA_VOICES["LC"]
    levels = [tc.ka_floor(f0, tau, lv)[1] for lv in (-100.0, -85.0, -70.0)]
    assert levels[0] < levels[1] < levels[2]
    assert levels[2] - levels[0] > 20.0        # ~1 dB per dB of floor
    none = tc.ka_floor(f0, tau, None)[1]
    assert none < levels[0]


def test_strike_impulse_is_blind_to_the_floor():
    f0, tau = tc.KA_VOICES["MC"]
    a, b = tc.ka_floor(f0, tau, None)[0], tc.ka_floor(f0, tau, -70.0)[0]
    assert abs(a - b) < 1.0


# ---- the frozen judge ----
def _row(split, ps, decay=1.0, peak=0.5, cand_ps=None, cand_decay=None, cand_peak=None):
    def v(p, d):
        r = {f: 1.0 for f in pg.FEATURES}
        r["pitch_shape"], r["decay"] = p, d
        return {"ratio": r, "worst": max(r.values()), "worst_feature": "x", "verdict": "FAIL", "d": {}}
    variants = {"shipped": v(ps, decay)}
    pf = {"shipped": peak}
    for n in ("BP", "BP+X4"):
        variants[n] = v(cand_ps if cand_ps is not None else ps,
                        cand_decay if cand_decay is not None else decay)
        pf[n] = cand_peak if cand_peak is not None else peak
    for g in (0, -20):
        variants[f"shipped+take_floor{g:+.0f}dB"] = v(ps, decay)
    return {"split": split, "variants": variants, "peak_fs": pf}


PRE = {"candidates": {"BP": "BP", "BP+X4": "BP+X4"},
       "rules": {"targets": ["pitch_shape"], "preserve_rel": 1.10, "preserve_abs": 0.10,
                 "peak_db": 1.0, "confirm_improved_frac": 0.75, "confirm_median_reduction": 0.25},
       "attribution": {"floor_gain_db": [0.0, -20.0]}}


def _rec(**kw):
    return {"rows": {f"D{i}": _row("development", 10.0, **kw) for i in range(3)}
            | {f"U{i}": _row("untouched", 10.0, **kw) for i in range(4)}}


def test_judge_confirms_a_clean_improvement():
    r = tc.judge(_rec(cand_ps=5.0), PRE)
    assert r["chosen"] in PRE["candidates"]
    assert r["confirmation"][r["chosen"]]["confirmed"]


def test_judge_rejects_a_win_bought_with_decay():
    """#351's own outcome: pitch better, decay worse -> inadmissible."""
    r = tc.judge(_rec(cand_ps=5.0, cand_decay=3.0), PRE)
    assert r["chosen"] is None
    assert not any(c["confirmed"] for c in r["confirmation"].values())


def test_judge_rejects_a_win_bought_with_silence():
    r = tc.judge(_rec(cand_ps=5.0, cand_peak=0.05), PRE)
    assert r["chosen"] is None


def test_judge_does_not_confirm_no_change():
    r = tc.judge(_rec(), PRE)
    assert not any(c["confirmed"] for c in r["confirmation"].values())


def test_judge_confirmation_reads_untouched_only():
    # six development rows that improve, two untouched that do not: a judge
    # that pooled the splits would see 6/8 improved and confirm. (With 3 + 4
    # rows it pooled to 3/7 and this test was BLIND to that defect --
    # wrong-then-right, caught by the injection below.)
    rec = {"rows": {f"D{i}": _row("development", 10.0, cand_ps=5.0) for i in range(6)}
           | {f"U{i}": _row("untouched", 10.0, cand_ps=5.0) for i in range(2)}}
    for k, row in rec["rows"].items():
        if row["split"] == "untouched":
            for n in PRE["candidates"]:
                row["variants"][n]["ratio"]["pitch_shape"] = 10.0
    r = tc.judge(rec, PRE)
    assert r["chosen"] is not None
    assert not r["confirmation"][r["chosen"]]["confirmed"]


# ---- the engine and the corpus ----
def test_retune_unity_is_shipped():
    import run_case as rc
    for s in ("MT", "LC"):
        assert np.array_equal(tc.render(s)[0], rc.render_drum_solo(s)[0])


def test_retune_moves_pitch():
    a, sr = tc.render("HC", 1.0)
    b, _ = tc.render("HC", 1.05)
    fa = pg._strongest_line(pg.condition(a, sr), 200, 800)
    fb = pg._strongest_line(pg.condition(b, sr), 200, 800)
    assert 1.03 < fb / fa < 1.07


def test_prereg_is_frozen_and_complete():
    p = tc.load_prereg()
    assert len(p["corpus"]["sha256_16"]) == 30
    splits = list(p["split_of"].values())
    assert splits.count("development") == 6 and splits.count("untouched") == 18
    for s in ("MT", "LC", "HC"):
        assert all(p["split_of"][f"{s}{c}"] != "development" for c in pg.CODES)


@needs_refs
def test_bar_for_take_is_the_gates_bar():
    refs = pathlib.Path(REFS)
    for s in ("LT", "MC"):
        rel = pg.target_rel(s)
        T = pg.Target(*pg.load_wav(refs / rel), s, rel)
        ours, theirs = tc.bar_for_take(s, rel, refs, T), pg.bar_for(s, refs, T)
        assert ours["from"] == theirs["from"]
        for f in pg.FEATURES:
            assert ours["bar"][f] == theirs["bar"][f]


@needs_refs
def test_refuses_a_different_corpus(tmp_path):
    refs = pathlib.Path(REFS)
    hashes = copy.deepcopy(tc.load_prereg()["corpus"]["sha256_16"])
    hashes["lt8/LT50.WAV"] = "0" * 16
    with pytest.raises(tc.Refused):
        tc.load_checked(refs, "lt8/LT50.WAV", hashes)
    (tmp_path / "lt8").mkdir()
    (tmp_path / "lt8" / "LT50.WAV").write_bytes(b"")
    with pytest.raises(Exception):
        tc.load_checked(tmp_path, "lt8/LT50.WAV", None)


#: The float twin's RAW form must give the gate's pitch_shape the shipped engine
#: gives, or the twin-BP vs twin-RAW attribution says nothing about the engine.
#: twin.json's worst case over all 30 conditions is 0.229 (HC25). LT00 carries
#: the diode-drop staircase (the conga modes have no per-frame host writes), so
#: it is the row the `twin-no-host-writes` injection turns red (3.34 -> 6.10).
TWIN_TOL = 0.25
TWIN_ROWS = (("LT", "00"), ("HC", "25"), ("MC", "00"))


@needs_refs
def test_twin_raw_tracks_the_engine():
    refs = pathlib.Path(REFS)
    hashes = tc.load_prereg()["corpus"]["sha256_16"]
    for s, c in TWIN_ROWS:
        rel = tc.take_rel(s, c)
        x50, sr50 = tc.load_checked(refs, tc.take_rel(s, "50"), hashes)
        f50 = tc.take_f0(pg.Target(x50, sr50, s, tc.take_rel(s, "50")))
        x, sr = tc.load_checked(refs, rel, hashes)
        T = pg.Target(x, sr, s, rel)
        bar = tc.bar_for_take(s, rel, refs, T, hashes)["bar"]
        ratio = tc.take_f0(T) / f50
        ship = tc.score(T, bar, *tc.render(s, ratio), "shipped")["ratio"]["pitch_shape"]
        twin = tc.score(T, bar, *tc.render_twin(s, ratio), "twin-RAW")["ratio"]["pitch_shape"]
        assert abs(twin - ship) <= TWIN_TOL, f"{s}{c}: twin-RAW {twin:.3f} vs shipped {ship:.3f}"


# ---- provenance is read at the start, and a dirty tree is REFUSED ----
def _fake_git(state):
    def g(*a):
        if a[:2] == ("rev-parse", "HEAD"):
            return state["head"]
        if a[:1] == ("rev-parse",):
            return "origin-main-sha"
        if a[:1] == ("status",):
            state["status_scope"] = a[3:]
            return state["dirty"]
        raise AssertionError(a)
    return g


def test_dirty_tree_is_refused_before_any_measurement(monkeypatch, tmp_path):
    """The defeating input for a provenance read: a record that ran from
    uncommitted code. It must REFUSE (exit 2), write nothing, and never start
    the measurement."""
    state = {"head": "A", "dirty": " M tools/probes/tom_conga_gate.py"}
    monkeypatch.setattr(tc, "git", _fake_git(state))
    ran = []
    monkeypatch.setattr(tc, "knownanswer", lambda: ran.append(1) or {})
    out = tmp_path / "ka.json"
    assert tc.main(["knownanswer", "--out", str(out)]) == 2
    assert not ran and not out.exists()
    # the scope includes the pre-registration the probe reads, not only code
    assert "model" in state["status_scope"] and "tools" in state["status_scope"]
    assert any(p.endswith("prereg.json") for p in state["status_scope"])


def test_provenance_names_the_commit_the_run_started_from(monkeypatch, tmp_path):
    """The defeating input for an end-of-run read (what run.json and twin.json
    have): HEAD moves while the run is in progress."""
    state = {"head": "A", "dirty": ""}
    monkeypatch.setattr(tc, "git", _fake_git(state))

    def work():
        state["head"] = "B"          # a commit lands mid-run
        state["dirty"] = " M model/drums_fx.py"
        return {}
    monkeypatch.setattr(tc, "knownanswer", work)
    out = tmp_path / "ka.json"
    assert tc.main(["knownanswer", "--out", str(out)]) == 0
    p = json.loads(out.read_text())["provenance"]
    assert p["commit"] == "A" and p["read_at"] == "start" and p["model_tools_dirty"] is False
    assert p["commit_at_end"] == "B" and p["head_moved_during_run"] and p["dirty_at_end"]


def _write_tom_take(refs: pathlib.Path, rel: str, hz: float) -> None:
    from scipy.io import wavfile
    t = np.arange(int(1.0 * 44100)) / 44100
    y = np.sin(2 * np.pi * hz * t) * np.exp(-t / 0.15)
    y = np.concatenate([np.zeros(int(0.02 * 44100)), y])
    p = refs / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    wavfile.write(p, 44100, np.round(16000 * y).astype(np.int16))


@pytest.fixture
def lt_takes(tmp_path):
    for code, hz in (("25", 80.0), ("50", 90.0), ("75", 110.0)):
        _write_tom_take(tmp_path, tc.take_rel("LT", code), hz)
    return (pg.load_wav(tmp_path / tc.take_rel("LT", "50")),
            pg.load_wav(tmp_path / tc.take_rel("LT", "75")))


def test_diagnose_prints_a_perfect_match_without_a_feature(tmp_path, lt_takes, monkeypatch, capsys):
    target, _ = lt_takes
    monkeypatch.setattr(pg, "render_ours", lambda sound: target)
    row = tc.diagnose(tmp_path, ["LT"])["LT"]
    assert row["worst_feature"] is None
    assert capsys.readouterr().out.splitlines()[0] == "LT PASS worst 0.0 pitch_shape 0.0x impulse 0.0x"


@pytest.mark.parametrize("probe, named_variant", [(tc.run, "BP"), (tc.twin, "twin-BP")])
def test_variant_output_shows_only_present_features(probe, named_variant, tmp_path, lt_takes, monkeypatch, capsys):
    import run_case as rc

    target, detuned = lt_takes
    monkeypatch.setattr(rc, "render_drum_solo", lambda sound: target)
    monkeypatch.setattr(tc, "render", lambda sound, ratio=1.0, kind=None: target if kind is None else detuned)
    monkeypatch.setattr(tc, "render_twin", lambda sound, ratio, numer: target if numer == (1.0,) else detuned)
    hashes = {p.relative_to(tmp_path).as_posix(): tc.sha16(p) for p in tmp_path.rglob("*.WAV")}
    prereg = {"corpus": {"sha256_16": hashes}, "split_of": {"LT50": "development"},
              "candidates": {"BP": "BP"}, "attribution": {"floor_gain_db": []}}

    variants = probe(tmp_path, prereg, sounds=["LT"], codes=["50"])["rows"]["LT50"]["variants"]
    assert variants["shipped"]["worst"] == 0.0 and variants["shipped"]["worst_feature"] is None
    named = variants[named_variant]
    assert named["worst"] > 0 and named["worst_feature"] in pg.FEATURES
    output = capsys.readouterr().out
    assert "None" not in output
    fields = output.strip().split(" | ")
    assert any("shipped: " in field and field.endswith("worst 0.0") for field in fields)
    expected = f"worst {named['worst']:.1f}({named['worst_feature']})"
    assert any(f"{named_variant}: " in field and field.endswith(expected) for field in fields)


# ---- #1263: per-take neighbour calibration refuses partial or missing sets ----
@pytest.mark.parametrize("mode", ["nearest", "farthest", "all", "directory"])
def test_bar_for_take_refuses_incomplete_neighbours_before_distances(tmp_path, monkeypatch, mode):
    target = tc.take_rel("LT", "50")
    neighbours = pg.neighbours("LT", target)
    _write_tom_take(tmp_path, target, 90.0)
    _write_tom_take(tmp_path, neighbours[0], 90.0)
    _write_tom_take(tmp_path, neighbours[1], 110.0)

    T = pg.Target(*tc.load_checked(tmp_path, target), "LT", target)
    cand = T.distance(*pg.candidate_path(*tc.load_checked(tmp_path, neighbours[1])))
    complete = tc.bar_for_take("LT", target, tmp_path, T)
    assert complete["from"] == neighbours[0]
    assert pg.verdict(cand, complete["bar"])["verdict"] == "FAIL"

    if mode == "nearest":
        removed = [neighbours[0]]
    elif mode == "farthest":
        removed = [neighbours[1]]
    elif mode == "all":
        removed = list(neighbours)
    else:
        removed = [neighbours[1]]

    for rel in removed:
        (tmp_path / rel).unlink()
    if mode == "directory":
        (tmp_path / removed[0]).mkdir()

    calls = []
    orig_distance = T.distance
    monkeypatch.setattr(T, "distance", lambda *a, **kw: calls.append(1) or orig_distance(*a, **kw))

    with pytest.raises(tc.Refused) as exc_info:
        tc.bar_for_take("LT", target, tmp_path, T)
    msg = str(exc_info.value)
    for rel in removed:
        assert str(tmp_path / rel) in msg
        assert rel in msg
    assert calls == [], "completeness must be checked before computing any distance"


# ---- rule 5: every injection turns its known answer red ----
INJECTED = {"no-preserve": test_judge_rejects_a_win_bought_with_decay,
            "no-peak": test_judge_rejects_a_win_bought_with_silence,
            "confirm-reads-all": test_judge_confirmation_reads_untouched_only,
            "allpole-stub": lambda: test_dc_zero_onset_reads_a_pitch_excess("MC"),
            "twin-no-host-writes": test_twin_raw_tracks_the_engine}


@pytest.mark.parametrize("name", tc.INJECTIONS)
def test_injection_turns_its_known_answer_red(name):
    assert name in INJECTED, f"injection {name} has no known answer to redden"
    if name == "twin-no-host-writes" and not REFS:
        pytest.skip("GF180_TR808_FISCHER (the Fischer corpus) not set")
    tc.INJECT.add(name)
    try:
        with pytest.raises(AssertionError):
            INJECTED[name]()
    finally:
        tc.INJECT.discard(name)
    INJECTED[name]()               # and green again once removed


# ---- #1774: a record the operator names that cannot be READ is REFUSED, never a traceback ----
# `judge <record>` and `judge --twin <file>` read files the OPERATOR names on the command
# line. The reader was `json.loads(path.read_text())`, so one that could not be read -- a
# path that does not exist, truncated JSON, bytes that are not UTF-8 -- exited 1 by
# traceback: FAIL, "the comparison ran and disagreed" (tools/exit_contract.py), where this
# tool's declared refusal is exit 2 and nothing was judged. The refusal tests run the real
# CLI as a subprocess, nothing stubbed: what is asserted is what an operator sees. The
# injected-bug control drives `main()` in process, because a monkeypatch cannot reach a
# subprocess, and the last control calls the loader itself. None of these needs the corpus.
REPO = pathlib.Path(__file__).resolve().parents[2]
ARCHIVED = REPO / "docs" / "scorecard" / "tom-conga-558"
#: Each way a user-supplied file can fail to be read, with the exception the shipped
#: reader died with: #1774's table, plus its test plan's two edge cases (an empty file;
#: a directory where a file was named).
RAW_READ_RAISES = {"missing": FileNotFoundError,
                   "truncated": json.JSONDecodeError,
                   "not-utf8": (UnicodeDecodeError, json.JSONDecodeError),  # locale-dependent: Latin-1 decodes ff fe, then JSON fails
                   "empty": json.JSONDecodeError,
                   "directory": IsADirectoryError}


def _unreadable(tmp_path, kind) -> pathlib.Path:
    """A path that cannot be read, for `kind`: #1774's own bytes for the three it
    names. The path carries the kind, so a failure prints which one it was."""
    p = tmp_path / kind / "record.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    if kind == "truncated":
        p.write_text('{"rows": [1, 2')
    elif kind == "not-utf8":
        p.write_bytes(b"\xff\xfe" + bytes(range(256)) * 4)
    elif kind == "empty":
        p.write_text("")
    elif kind == "directory":
        p.mkdir(exist_ok=True)
    return p


def _judge_cli(*argv) -> subprocess.CompletedProcess:
    """The real CLI as an operator runs it: a fresh interpreter, the file on disk."""
    return subprocess.run([sys.executable, str(REPO / "tools" / "probes" / "tom_conga_gate.py"),
                           "judge", *argv], cwd=REPO, capture_output=True, text=True, errors="replace")


def _assert_refused(r: subprocess.CompletedProcess, named: pathlib.Path, out: pathlib.Path) -> None:
    assert r.returncode == 2, r.stdout + r.stderr
    assert "REFUSED" in r.stdout and str(named) in r.stdout, r.stdout
    assert "Traceback" not in r.stderr, r.stderr
    assert not out.exists()


@pytest.mark.parametrize("kind", list(RAW_READ_RAISES))
def test_judge_refuses_a_record_that_cannot_be_read(tmp_path, kind):
    rec, out = _unreadable(tmp_path, kind), tmp_path / "judge.json"
    _assert_refused(_judge_cli(str(rec), "--out", str(out)), rec, out)


@pytest.mark.parametrize("kind", list(RAW_READ_RAISES))
def test_judge_refuses_a_twin_file_that_cannot_be_read(tmp_path, kind):
    """The main record is the archived `run.json`, which judges on its own (the control
    below): only `--twin` is unreadable, and the refusal names the twin, not the record."""
    twin, out = _unreadable(tmp_path, kind), tmp_path / "judge.json"
    _assert_refused(_judge_cli(str(ARCHIVED / "run.json"), "--twin", str(twin), "--out", str(out)), twin, out)


def test_judge_still_judges_the_archived_run_record(tmp_path):
    """The guard's other side (verification rule 8): a record `run` wrote is not refused.
    Without it the refusals above would pass against a reader that refuses everything."""
    out = tmp_path / "judge.json"
    r = _judge_cli(str(ARCHIVED / "run.json"), "--out", str(out))
    assert r.returncode == 0, r.stdout + r.stderr
    res = json.loads(out.read_text())
    assert res["selection"] and "post_hoc" not in res


def test_judge_still_adds_the_tables_of_the_archived_twin(tmp_path):
    out = tmp_path / "judge.json"
    r = _judge_cli(str(ARCHIVED / "run.json"), "--twin", str(ARCHIVED / "twin.json"), "--out", str(out))
    assert r.returncode == 0, r.stdout + r.stderr
    assert json.loads(out.read_text())["post_hoc"]


@pytest.mark.parametrize("kind", list(RAW_READ_RAISES))
def test_without_the_loader_judge_is_the_shipped_defect(tmp_path, monkeypatch, kind):
    """Injected-bug control (#1774 AC 6), committed rather than described: the fix is
    `load_record`, so reverted to the bare read it replaces, each input above is exit 1
    by traceback again -- through `main()`, with the very exception #1774's table names,
    at both read sites -- and nothing is written."""
    monkeypatch.setattr(tc, "load_record", lambda path, what: json.loads(path.read_text()))
    rec, out = _unreadable(tmp_path, kind), tmp_path / "judge.json"
    with pytest.raises(RAW_READ_RAISES[kind]):
        tc.main(["judge", str(rec), "--out", str(out)])
    with pytest.raises(RAW_READ_RAISES[kind]):
        tc.main(["judge", str(ARCHIVED / "run.json"), "--twin", str(rec), "--out", str(out)])
    assert not out.exists()


def test_the_loader_refuses_only_what_it_names(tmp_path, monkeypatch):
    """#1774 AC 7: the catch is `manifest.Refused` and `OSError`, never `Exception`. An
    exception that is neither -- an implementation error inside the reader -- is an
    execution error: it leaves `load_record` as itself rather than being relabelled a
    refusal, which is the inverse defect. Called on the loader directly, so it fails only
    for that reason; whether the CLI reads THROUGH the loader is the refusal tests' to
    assert."""
    rec = tmp_path / "record.json"
    rec.write_text("{}")

    def boom(path, what):
        raise TypeError("an implementation error inside the reader, not a refusal")
    monkeypatch.setattr(tc.mf, "load_json_refused", boom)
    with pytest.raises(TypeError, match="not a refusal"):
        tc.load_record(rec, "the run record")


# ---- #1860: an unreadable take reaches the gate's reader, and exits 2 --------
LFS_POINTER = ("version https://git-lfs.github.com/spec/v1\n"
               "oid sha256:" + "0" * 64 + "\nsize 12345\n")


@pytest.mark.parametrize("kind", ["git-lfs-pointer", "empty"])
def test_an_unreadable_take_exits_2_through_ratepath16(monkeypatch, tmp_path, capsys, kind):
    """#1860. `ratepath16` reads each gate target through `load_checked(refs,
    rel)` with NO hash table, so nothing refuses a corrupted take before the
    reader: a git-lfs pointer or a 0-byte file is a file, so `is_file()` passes
    and it reaches `pg.load_wav`. `main` already catches `pg.Refused`; what was
    missing is the reader's own guard. START RED on main: scipy's ValueError
    escaped `main` as a traceback (exit 1)."""
    monkeypatch.setattr(tc, "git", _fake_git({"head": "A", "dirty": ""}))
    p = tmp_path / pg.target_rel("BD")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(LFS_POINTER if kind == "git-lfs-pointer" else "")
    out = tmp_path / "ratepath16.json"
    assert tc.main(["ratepath16", "--refs", str(tmp_path), "--out", str(out)]) == 2
    text = capsys.readouterr().out
    assert "REFUSED" in text and str(p) in text, text[-400:]
    assert not out.exists()

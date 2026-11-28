"""The D12A probe's own apparatus: the burst/tail estimator on signals whose
answer is closed form, and the mutants that must be caught. No reference
corpus and no drum render needed, so this runs in the fast suite."""
import copy
import json
import math
import pathlib
import sys

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import clap_d12a_probe as cp  # noqa: E402
import run_case as rc  # noqa: E402


@pytest.mark.parametrize("sr", [44100, 48000])
@pytest.mark.parametrize("kw", [dict(), dict(b=0.05), dict(b=0.5, t_tau=0.090),
                                dict(n_bursts=4, period_s=0.012, tail_start_s=0.050)])
def test_scorer_ratio_equals_closed_form(sr, kw):
    x, q = cp.synthetic_clap(sr, **kw)
    got = cp.ratio_db(rc.prepare(x, sr), sr)
    assert abs(got - cp.synthetic_expected_db(q)) <= cp.KNOWN_ANSWER_TOL_DB


def test_every_mutant_is_killed_and_the_control_is_green():
    ka = cp.known_answer()
    assert ka["ok"], ka
    assert all(ka["mutants_killed"].values()), ka["mutants_killed"]


def test_split_mutant_is_caught_on_the_default_signal():
    x, q = cp.synthetic_clap(48000)
    y = rc.prepare(x, 48000)
    assert abs(cp._mutant_split40(y, 48000) - cp.synthetic_expected_db(q)) > 1.0


def test_tail_doubling_is_seen_at_its_closed_form_size():
    x, q = cp.synthetic_clap(44100)
    x2, q2 = cp.synthetic_clap(44100, b=0.4)
    d = cp.ratio_db(rc.prepare(x2, 44100), 44100) - cp.ratio_db(rc.prepare(x, 44100), 44100)
    want = cp.synthetic_expected_db(q2) - cp.synthetic_expected_db(q)
    assert abs(d - want) <= cp.KNOWN_ANSWER_TOL_DB
    assert want < -5.0


def test_overlapping_synthetic_is_refused():
    with pytest.raises(ValueError):
        cp.synthetic_clap(48000, n_bursts=4, period_s=0.012, tail_start_s=0.031)


@pytest.mark.parametrize("tau", [0.047, 0.090])
def test_tail_fit_recovers_a_known_time_constant(tau):
    x, _ = cp.synthetic_clap(48000, t_tau=tau, b=0.3)
    f = cp.tail_fit(rc.prepare(x, 48000), 48000)
    assert math.isclose(f["amp_tau_ms"], tau * 1e3, rel_tol=0.01)


def test_manifest_mismatch_refuses(tmp_path):
    (tmp_path / "a.wav").write_bytes(b"abc")
    m = tmp_path / "m.sha256"
    m.write_text("0" * 64 + "  ./a.wav\n")
    with pytest.raises(cp.Refused):
        cp.verify_manifest(tmp_path, m)


def test_empty_manifest_refuses_instead_of_verifying_nothing(tmp_path):
    """#1469: a zero-byte manifest gave `rows == []`, so `bad == []` and the
    reference-hash precondition passed without hashing one file."""
    (tmp_path / "cp8").mkdir()
    (tmp_path / "cp8" / "CP.WAV").write_bytes(b"cp")
    m = tmp_path / "m.sha256"
    m.write_bytes(b"")
    with pytest.raises(cp.Refused) as e:
        cp.verify_manifest(tmp_path, m)
    assert "empty" in str(e.value)


def test_populated_manifest_passes_and_still_names_a_mismatch(tmp_path):
    # the good case beside the refusal: cp8/CP.WAV covered by its true digest
    import hashlib
    (tmp_path / "cp8").mkdir()
    (tmp_path / "cp8" / "CP.WAV").write_bytes(b"cp")
    m = tmp_path / "m.sha256"
    m.write_text(hashlib.sha256(b"cp").hexdigest() + "  ./cp8/CP.WAV\n")
    got = cp.verify_manifest(tmp_path, m)
    assert got["files_checked"] == 1 and got["files_mismatched"] == 0


def test_manifest_not_covering_cp_wav_refuses(tmp_path):
    # Rule 8 defeating input for `if not rows:`: a non-empty manifest that
    # hashes only a bystander file leaves cp8/CP.WAV -- the file the probe
    # actually measures -- unverified.
    import hashlib
    (tmp_path / "cp8").mkdir()
    (tmp_path / "cp8" / "CP.WAV").write_bytes(b"unverified-cp")
    (tmp_path / "other.txt").write_bytes(b"abc")
    m = tmp_path / "m.sha256"
    m.write_text(hashlib.sha256(b"abc").hexdigest() + "  ./other.txt\n")
    with pytest.raises(cp.Refused, match="CP.WAV"):
        cp.verify_manifest(tmp_path, m)


def test_malformed_manifest_line_refuses(tmp_path):
    (tmp_path / "cp8").mkdir()
    (tmp_path / "cp8" / "CP.WAV").write_bytes(b"cp")
    m = tmp_path / "m.sha256"
    m.write_text("not-a-digest-and-path-line\n")
    with pytest.raises(cp.Refused, match="malformed"):
        cp.verify_manifest(tmp_path, m)


@pytest.mark.parametrize("payload", [b"", b"null"])
def test_unreadable_d12a_record_refuses_before_rendering(tmp_path, monkeypatch, capsys, payload):
    path = tmp_path / "D12A.json"
    path.write_bytes(payload)
    monkeypatch.setattr(cp, "RECORD", path)
    monkeypatch.setattr(cp, "basis", lambda *_: {})
    monkeypatch.setattr(rc, "run_drum_case", lambda *a, **kw: pytest.fail("record accepted"))
    assert cp.main([]) == 2
    out = capsys.readouterr().out
    assert "REFUSED" in out and str(path) in out


@pytest.fixture
def replay_d12a(tmp_path, monkeypatch):
    """Replay the reporting path with the committed record and fixture audio."""
    committed = json.loads(cp.RECORD.read_text())
    scored = copy.deepcopy(committed)
    record = tmp_path / "D12A.json"
    output = tmp_path / "probe.json"
    monkeypatch.setattr(cp, "RECORD", record)
    monkeypatch.setattr(cp, "basis", lambda *_: {})
    monkeypatch.setattr(cp, "known_answer", lambda: {"ok": True, "cases": []})

    def score(case, *args, **kwargs):
        assert case["required_measurements"].split("; ") == [m[0] for m in rc.DRUM_PLAN["CP"]]
        return scored

    monkeypatch.setattr(rc, "run_drum_case", score)
    audio = (np.array([0.25, -0.25]), 48000)
    monkeypatch.setattr(rc, "render_drum_solo", lambda *_: audio)
    monkeypatch.setattr(cp, "render_cp", lambda *_: audio)
    diagnoses = []

    def diagnose(*args):
        diagnoses.append(args)
        return {"sensitivity": [], "decomposition": {}, "nuisance": [], "accent_headroom": []}

    monkeypatch.setattr(cp, "diagnose", diagnose)

    def run():
        record.write_text(json.dumps(committed))
        return cp.main(["--out", str(output)])

    return committed, scored, run, output, diagnoses


@pytest.mark.parametrize("side", ["committed", "scorer"])
@pytest.mark.parametrize("keep", [(), ("burst/tail ratio",)])
def test_reproduction_refuses_incomplete_metric_inventory(replay_d12a, capsys, side, keep):
    committed, scored, run, output, diagnoses = replay_d12a
    scored["metrics"]["decay"]["error"] = 12345
    record = committed if side == "committed" else scored
    record["metrics"] = {name: record["metrics"][name] for name in keep}
    assert run() == 2
    message = capsys.readouterr().out
    assert "REFUSED" in message and side in message and "decay" in message
    assert not diagnoses and not output.exists()


@pytest.mark.parametrize("name,row", [
    ("decay", {}),
    ("decay", {"valid": True}),
    ("burst/tail ratio", {"valid": False, "why": "no ratio measured"}),
])
def test_reproduction_refuses_matching_incomplete_rows(replay_d12a, capsys, name, row):
    committed, scored, run, output, diagnoses = replay_d12a
    committed["metrics"][name] = row
    scored["metrics"][name] = row
    assert run() == 2
    message = capsys.readouterr().out
    assert "REFUSED" in message and name in message
    assert not diagnoses and not output.exists()


def test_reproduction_counts_metrics_from_the_plan(replay_d12a, monkeypatch):
    committed, scored, run, output, _ = replay_d12a
    name = "additional planned measurement"
    plan = rc.DRUM_PLAN["CP"]
    monkeypatch.setitem(rc.DRUM_PLAN, "CP", [*plan, (name, *plan[0][1:])])
    for record in (committed, scored):
        record["metrics"][name] = record["metrics"]["decay"].copy()
    assert run() == 0
    reproduction = json.loads(output.read_text())["reproduction"]
    assert name in reproduction["metrics"]
    assert "x 4 metrics" in reproduction["fields_compared"]


def test_reproduction_refuses_wrong_metric_with_same_count(replay_d12a, capsys):
    committed, scored, run, output, diagnoses = replay_d12a
    for record in (committed, scored):
        record["metrics"]["other"] = record["metrics"].pop("decay")
    assert run() == 2
    message = capsys.readouterr().out
    assert "decay" in message and "other" in message
    assert not diagnoses and not output.exists()


@pytest.mark.parametrize("plan_defect", ["empty", "duplicate"])
def test_reproduction_refuses_unusable_clap_plan(replay_d12a, monkeypatch, capsys, plan_defect):
    _, _, run, output, diagnoses = replay_d12a
    plan = rc.DRUM_PLAN["CP"]
    monkeypatch.setitem(rc.DRUM_PLAN, "CP", [] if plan_defect == "empty" else [*plan, plan[0]])
    assert run() == 2
    assert "CP measurement plan" in capsys.readouterr().out
    assert not diagnoses and not output.exists()


@pytest.mark.parametrize("mismatch", [False, True])
def test_reproduction_compares_all_planned_metrics(replay_d12a, capsys, mismatch):
    committed, scored, run, output, diagnoses = replay_d12a
    if mismatch:
        scored["metrics"]["decay"]["error"] = 12345
        assert run() == 2
        assert "decay.error" in capsys.readouterr().out
        assert not diagnoses and not output.exists()
    else:
        assert run() == 0
        reproduced = json.loads(output.read_text())["reproduction"]
        assert reproduced["metrics"] == committed["metrics"]
        assert "x 3 metrics" in reproduced["fields_compared"]


PARTIAL_RECORD_FIELDS = ("metrics", "windowing", "diagnostics", "source_commit", "analysis_run", "audio")


def names_missing_field(out, field):
    return "REFUSED" in out and field in out and str(cp.RECORD) in out


def test_every_literal_committed_field_read_is_a_required_field():
    import ast
    import inspect
    reads = {node.slice.value for node in ast.walk(ast.parse(inspect.getsource(cp.reproduce)))
             if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name)
             and node.value.id == "committed" and isinstance(node.slice, ast.Constant)}
    assert {"windowing", "diagnostics", "source_commit", "analysis_run", "audio"} <= reads
    assert reads <= set(PARTIAL_RECORD_FIELDS) <= set(cp.COMMITTED_RECORD_FIELDS)


def test_intact_committed_record_reproduces_and_writes_the_report(replay_d12a):
    committed, _, run, output, diagnoses = replay_d12a
    assert run() == 0
    assert len(diagnoses) == 1
    reproduction = json.loads(output.read_text())["reproduction"]
    assert list(reproduction["metrics"]) == [m[0] for m in rc.DRUM_PLAN["CP"]]
    assert reproduction["metrics"] == committed["metrics"]
    for field in ("source_commit", "analysis_run", "audio"):
        assert reproduction[f"committed_{field}"] == committed[field]


@pytest.mark.parametrize("field", PARTIAL_RECORD_FIELDS)
def test_partial_committed_record_refuses_with_its_missing_field(replay_d12a, capsys, field):
    committed, _, run, output, diagnoses = replay_d12a
    del committed[field]
    assert run() == 2
    assert names_missing_field(capsys.readouterr().out, field)
    assert not diagnoses and not output.exists()


@pytest.mark.parametrize("field", PARTIAL_RECORD_FIELDS)
def test_partial_committed_record_never_reaches_the_scorer(replay_d12a, monkeypatch, field):
    committed, _, run, _, _ = replay_d12a
    del committed[field]
    monkeypatch.setattr(rc, "run_drum_case",
                        lambda *args, **kwargs: pytest.fail("a partial record reached the scorer"))
    assert run() == 2


def test_a_non_reproducing_record_is_not_mistaken_for_a_partial_one(replay_d12a, capsys):
    _, scored, run, output, diagnoses = replay_d12a
    scored["windowing"]["ours"]["onset_index"] += 1
    assert run() == 2
    out = capsys.readouterr().out
    assert "REFUSED" in out and "windowing.ours differs" in out
    assert not names_missing_field(out, "windowing")
    assert not diagnoses and not output.exists()


def test_a_refusal_naming_the_record_but_no_field_is_not_a_missing_field():
    out = f"known answer: PASS\nREFUSED: cannot read D12A record {cp.RECORD}: Expecting value\n"
    assert not names_missing_field(out, "windowing")


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="presence-only guard (#1597): a field present but null still passes")
def test_a_committed_field_present_but_null_refuses(replay_d12a, capsys):
    committed, _, run, output, _ = replay_d12a
    committed["source_commit"] = None
    assert run() == 2
    assert names_missing_field(capsys.readouterr().out, "source_commit")
    assert not output.exists()


@pytest.mark.xfail(strict=True, raises=KeyError,
                   reason="presence-only guard (#1597): a field present but empty still escapes as a traceback")
def test_a_committed_field_present_but_empty_refuses(replay_d12a, capsys):
    committed, _, run, output, _ = replay_d12a
    committed["windowing"] = {}
    assert run() == 2
    assert names_missing_field(capsys.readouterr().out, "windowing")
    assert not output.exists()

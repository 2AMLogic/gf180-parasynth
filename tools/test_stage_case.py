#!/usr/bin/env python3
"""The end-to-end demonstration, as a gate.

Two renders and three analyses cost about a minute, so the staging is a module
fixture rather than a per-test one. What it asserts, in order of how much it
matters:

  1. `analyse` renders NOTHING. Proved by making `render_voice` raise and then
     analysing anyway -- not by inspecting the code, which is how every bench in
     this repository that drove the wrong port passed.
  2. the withdrawn estimator on the RETAINED audio reproduces the historical
     numbers (BD T20 207 ms against a locked 308), and the manifest attributes
     the delta to the measurement.
  3. the same estimator on a render with a real sound defect moves the same
     metric by a similar amount, and the manifest attributes THAT to the sound.
  4. both at once is refused rather than guessed -- and the delta is still
     reported, because on T20 the two defects partly cancel.
  5. every record the real producer writes is complete by the schema's own
     standard. A validator that has only ever seen hand-written fixtures has not
     been shown to accept anything real.
  6. the committed evidence under `docs/provenance/bd-decay-demo/` still agrees
     with what this tree produces.
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "model"))
sys.path.insert(0, str(ROOT / "audition"))
sys.path.insert(0, str(ROOT / "tools"))

import manifest                                                       # noqa: E402
import provenance_retention as pr                                     # noqa: E402
import sound_report as sr                                             # noqa: E402
import stage_case as sc                                               # noqa: E402

V1 = "drum-decay-moving-average"
V2 = "drum-decay-rms"
DEMO = ROOT / "docs" / "provenance" / "bd-decay-demo"


@pytest.fixture(scope="module")
def staged(tmp_path_factory):
    root = tmp_path_factory.mktemp("provenance")
    clean = sc.do_render(root, "BD", None, "reference-fixture")
    broken = sc.do_render(root, "BD", "bd-decay-short", "smoke")
    return {"root": root, "clean": clean, "broken": broken,
            "v1_clean": sc.do_analyse(root, clean, V1),
            "v2_clean": sc.do_analyse(root, clean, V2),
            "v2_broken": sc.do_analyse(root, broken, V2)}


def value(analysis, metric):
    return analysis["measurements"][metric]["value"]


# ---------------------------------------------------------------------------
# 0. this suite runs in CI
# ---------------------------------------------------------------------------
def test_this_suite_and_the_demo_are_run_by_a_workflow():
    """"Check that the thing you are testing is the thing that ships"
    (CLAUDE.md), applied to this suite itself. `tools/test_manifest.py` shipped
    for a while reachable only through a `pytest tools/` that no workflow
    invoked; the same hole is available to this file and to the demo, which are
    the only places the real renderer is exercised at all."""
    wf = yaml.safe_load((ROOT / ".github/workflows/provenance.yml").read_text())
    runs = " ".join(str(s.get("run", ""))
                    for s in wf["jobs"]["stages-and-controls"]["steps"])
    for needed in ("tools/test_stage_case.py", "stage_case.py demo",
                   "stage_case.py controls", "tools/test_provenance_retention.py",
                   "--check-locks"):
        assert needed in runs, (
            f"{needed!r} is in no step of .github/workflows/provenance.yml -- a "
            f"gate nothing invokes fails exactly the same way as one that passes")


# ---------------------------------------------------------------------------
# 1. analyse really does re-read the retained audio
# ---------------------------------------------------------------------------
def test_analyse_does_not_render(staged, monkeypatch):
    """The claim "we re-ran the estimator on last week's audio" is worth exactly
    as much as the proof that no render happened. So: break rendering, then
    analyse."""
    def refuse(*a, **k):
        raise AssertionError("analyse rendered something, so the attribution it "
                             "reports is about audio nobody retained")
    monkeypatch.setattr(sc, "render_voice", refuse)
    rec = sc.do_analyse(staged["root"], staged["clean"], V1)
    assert rec["render_wav_sha256"] == staged["clean"]["wav_sha256"]
    assert rec["render_id"] == staged["clean"]["render_id"]


def test_the_retained_audio_is_on_disk_and_is_not_only_a_summary(staged):
    d = pathlib.Path(staged["root"]) / "runs" / staged["clean"]["render_id"]
    wav = d / staged["clean"]["wav_path"]
    assert wav.exists() and wav.stat().st_size > 100_000, (
        f"{staged['clean']['n_samples']} int16 samples is over 200 kB; a smaller "
        f"file is a summary, and issue #68's 'keep raw artifacts' is exactly the "
        f"requirement that summary statistics are not enough")
    import provenance
    assert provenance.file_sha(wav) == staged["clean"]["wav_sha256"]


def test_the_render_retains_numerical_traces_as_well_as_audio(staged):
    d = pathlib.Path(staged["root"]) / "runs" / staged["clean"]["render_id"]
    assert set(staged["clean"]["trace_paths"]) == {"bus_dm_peak_per_ms",
                                                    "bus_bd_peak_per_ms"}
    for rel in staged["clean"]["trace_paths"].values():
        vals = json.loads((d / rel).read_text())
        assert len(vals) > 1000, "a per-millisecond envelope of 2.2 s is 2200 points"


def test_the_analysis_retains_the_numbers_the_verdict_rests_on(staged):
    """Not a summary of them. The fit REGION is retained rather than the
    full-rate envelope because the envelope is re-derivable from the retained
    WAV by the recorded method, whereas the region depends on the estimator and
    is what the next estimator has to be argued against."""
    d = pathlib.Path(staged["root"]) / "jobs" / staged["v2_clean"]["job_id"]
    paths = staged["v2_clean"]["trace_paths"]
    assert set(paths) == {"fit_region_t_s", "fit_region_db", "envelope_db_per_ms"}
    t = json.loads((d / paths["fit_region_t_s"]).read_text())
    db = json.loads((d / paths["fit_region_db"]).read_text())
    assert len(t) == len(db) > 8
    assert db[0] <= -3.0 and db[-1] > -30.0, (
        "the retained region must be the -3 to -30 dB window decay_fit actually "
        "fits, not the whole tail")


def test_a_tampered_retained_wav_is_refused_rather_than_measured(staged, tmp_path):
    r = sc.do_render(tmp_path, "BD", None, "smoke")
    sc.tamper(tmp_path, r)
    with pytest.raises(manifest.Refused, match="does not match the hash"):
        sc.do_analyse(tmp_path, r, V2)


def test_each_record_is_marked_with_its_retention_class(staged):
    """A record that belongs to no class is uploaded on no clock."""
    root = pathlib.Path(staged["root"])
    assert pr.read_mark(root / "runs" / staged["clean"]["render_id"]) \
        == "reference-fixture"
    assert pr.read_mark(root / "runs" / staged["broken"]["render_id"]) == "smoke"
    assert pr.read_mark(root / "jobs" / staged["v2_clean"]["job_id"]) \
        == "reference-fixture", (
        "an analysis of a reference fixture must not expire before the fixture; "
        "the measurement is the thing the bound was actually derived from")
    assert pr.read_mark(root / "jobs" / staged["v2_broken"]["job_id"]) == "smoke"


def test_the_whole_store_groups_cleanly_for_upload(staged, tmp_path):
    """The producer and the packer are written separately, so this is the test
    that they agree: every record the real run writes must be placeable."""
    s = pr.group(staged["root"], tmp_path / "upload")
    assert "refused" not in s, s
    assert set(s["classes"]) == {"reference-fixture", "smoke"}


# ---------------------------------------------------------------------------
# 2-4. attribution on a real case
# ---------------------------------------------------------------------------
def test_the_withdrawn_estimator_reproduces_the_historical_numbers(staged):
    """The 5 ms moving-average envelope is `sound_report.py --inject
    bd-ma-envelope`, whose own record is "BD T20 308 -> 207 ms, BD attack
    14.56 -> 9.94 ms; BD fundamental and decay tau stay BLIND". Reproduced here
    off a RETAINED WAV rather than off a fresh render, which is the capability
    under test."""
    assert value(staged["v2_clean"], "T20") == pytest.approx(307.979, abs=0.01)
    assert value(staged["v1_clean"], "T20") == pytest.approx(207.04, abs=0.01)
    assert value(staged["v2_clean"], "attack") == pytest.approx(14.5625, abs=0.01)
    assert value(staged["v1_clean"], "attack") == pytest.approx(9.9375, abs=0.01)


def test_the_measurement_changed_and_the_sound_did_not(staged):
    d = sc.attribute(staged["v1_clean"], staged["v2_clean"])
    assert d["attribution"] == "measurement changed"
    assert d["renders"][0] == d["renders"][1] == staged["clean"]["render_id"]
    assert d["metrics"]["T20"]["saw"] == "MOVED"
    assert d["metrics"]["decay tau"]["saw"] == "BLIND", (
        "docs/verification-rules.md 4: a metric that cannot see a defect is worth "
        "printing. tau moves 149 -> 144, inside its own 36 ms tolerance")


def test_the_sound_changed_and_the_measurement_did_not(staged):
    d = sc.attribute(staged["v2_clean"], staged["v2_broken"])
    assert d["attribution"] == "sound changed"
    assert d["analysers"][0] == d["analysers"][1]
    assert d["metrics"]["T20"]["saw"] == "MOVED"
    assert d["metrics"]["attack"]["saw"] == "BLIND"


def test_the_two_defects_together_cannot_be_attributed_and_are_not(staged):
    d = sc.attribute(staged["v1_clean"], staged["v2_broken"])
    assert not d["attributable"]
    assert d["attribution"].startswith("REFUSED")
    assert all(not row["attributable"] for row in d["metrics"].values())


def test_the_two_defects_partly_cancel_on_t20_which_is_why_refusing_matters(staged):
    """-101 ms from the ruler and -122 ms from the sound leave -21 ms, which is
    INSIDE T20's 36 ms tolerance. A comparison that attributed this pair would
    report the metric as unmoved while both halves of it were broken.

    The delta has to still be REPORTED under the refusal for this to be
    visible -- REFUSED is about attribution, not about arithmetic. It was not,
    at first: `attribute()` dropped the values whenever `diagnose()` refused and
    the demo printed `delta nan`, which hid the single most interesting number
    in the whole demonstration."""
    d = sc.attribute(staged["v1_clean"], staged["v2_broken"])
    t20 = d["metrics"]["T20"]
    assert t20["saw"] == "BLIND"
    assert abs(t20["delta"]) < t20["tolerance"]
    assert abs(t20["delta"]) == pytest.approx(20.94, abs=0.1)


def test_the_verdicts_are_pass_fail_fail_for_three_different_reasons(staged):
    root = staged["root"]
    assert sc.outcome_of(sc.do_accept(root, staged["v2_clean"]))[0] == "pass"
    v1 = sc.do_accept(root, staged["v1_clean"])
    assert sc.outcome_of(v1)[0] == "fail"
    assert v1["verdicts"]["T20"]["state"] == "fail"
    vb = sc.do_accept(root, staged["v2_broken"])
    assert sc.outcome_of(vb)[0] == "fail"
    assert vb["verdicts"]["decay tau"]["state"] == "fail"


# ---------------------------------------------------------------------------
# 5. the records the real producer writes are complete
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("which", ["v1_clean", "v2_clean", "v2_broken"])
def test_every_record_the_producer_writes_is_complete(staged, which):
    for m in staged[which]["measurements"].values():
        manifest.validate_measurement(m)


def test_every_measurement_names_its_units_method_interval_and_reference(staged):
    """Issue #68's "what a measurement must carry", field by field, on the
    records the real producer writes rather than on a fixture."""
    for name, m in staged["v2_clean"]["measurements"].items():
        assert m["units"] in ("ms", "Hz"), name
        assert m["analyser_version"] and len(m["method"]) > 40, name
        assert m["interval_s"][1] > m["interval_s"][0], name
        assert m["selection"]["hit_index"] == 0 and "onset_s" in m["selection"], name
        assert m["selection"]["interval_basis"], name
        assert m["channel"] and m["resample_hz"] and m["normalisation"], name
        assert m["filter"], name
        for k in ("transform", "window", "nfft", "hop"):
            assert m["fft"][k], f"{name}: fft.{k} must be STATED, not omitted"
        q = m["fit_quality"]
        assert q["uncertainty"] is not None and q["uncertainty_basis"], name
        assert q["noise_floor_db"] is not None and q["noise_floor_treatment"], name
        assert "sha256:" in m["reference_identity"], (
            f"{name}: the reference must be identified by content, not by name")


def test_the_two_kinds_of_reference_are_distinguished(staged):
    """A document's figure and this model's own pinned measurement are different
    kinds of comparand and must not read alike. `CP decay tau 47 ms` was the
    E_CPTAIL register, not the voice's decay."""
    refs = {n: m["reference_identity"]
            for n, m in staged["v2_clean"]["measurements"].items()}
    assert refs["fundamental"].startswith("document:")
    assert refs["decay tau"].startswith("document:")
    assert refs["T20"].startswith("model-lock:")
    assert refs["attack"].startswith("model-lock:")
    assert "NO RECORDING IS READ" in refs["fundamental"], (
        "a document's figure must not be dressed up as a recording identity")


def test_a_target_and_a_lock_are_not_mixed(staged):
    kinds = {m: sc.bound_for("BD", m)["kind"] for m in sc.METRICS}
    assert kinds == {"fundamental": "target", "decay tau": "target",
                     "T20": "lock", "attack": "lock"}


def test_the_bound_values_come_from_the_tables_that_own_them():
    """Not copied. A second bound table would be a second thing to forget."""
    import drum_verify as dv
    assert sc.bound_for("BD", "decay tau")["value"] == dv.SPEC["BD"]["tau_ms"]
    assert sc.bound_for("BD", "T20")["value"] == sr.LOCKS[("BD", "T20")]
    assert sc.bound_for("BD", "attack")["value"] == sr.LOCKS[("BD", "attack")]


def test_every_bound_carries_the_record_of_when_it_last_moved(staged):
    """Issue #68's acceptance criterion 2, on the rationale a verdict actually
    carries: not just "why this bound", but "when it last moved and why"."""
    v = sc.do_accept(staged["root"], staged["v2_clean"])
    for name, row in v["verdicts"].items():
        assert "last changed at" in row["rationale"], name
        assert " -> " in row["rationale"] and "because" in row["rationale"], name
    assert "7be1490" in v["verdicts"]["decay tau"]["rationale"], (
        "the BD tau target moved 127 -> 144 ms at 7be1490 (DR 0009, #14) and the "
        "verdict should say so")
    assert sr.LOCK in v["verdicts"]["T20"]["rationale"]


def test_a_metric_sound_report_does_not_declare_gets_no_invented_bound():
    with pytest.raises(manifest.Refused, match="declares no"):
        sc.bound_for("BD", "a metric nobody declared")


def test_a_bound_whose_history_is_empty_is_refused(monkeypatch):
    """The other half of the same rule: a bound that exists but has no recorded
    change cannot answer "did this go green because the model improved or
    because the bound moved?", so it gets no verdict rather than a silent one."""
    monkeypatch.setattr(sc, "TARGET_HISTORY", {})
    with pytest.raises(manifest.Refused, match="no recorded change"):
        sc.bound_for("BD", "decay tau")


# ---------------------------------------------------------------------------
# the bound ledger survives a fresh checkout
# ---------------------------------------------------------------------------
def test_the_tracked_bound_ledger_is_committed_and_covers_this_case():
    """A bound-change ledger that lives only in a gitignored build directory
    detects no bound change at all: on a fresh checkout or any CI run the
    history is empty, so the gate silently passes everything."""
    assert sc.BOUNDS_TRACKED.exists(), (
        f"{sc.BOUNDS_TRACKED} is not present -- regenerate it with "
        f"`tools/stage_case.py demo --record-bounds`")
    led = json.loads(sc.BOUNDS_TRACKED.read_text())
    assert set(led["BD-solo"]) == set(sc.METRICS)
    for metric, row in led["BD-solo"].items():
        b = sc.bound_for("BD", metric)
        assert (row["lo"], row["hi"]) == (b["lo"], b["hi"]), (
            f"the committed ledger's {metric} bound is [{row['lo']}, {row['hi']}] "
            f"and the tables now say [{b['lo']}, {b['hi']}]. If the move was "
            f"intended, record it: `tools/stage_case.py demo --record-bounds`, and "
            f"say in the commit message why the bound moved")


def test_a_bound_moved_against_the_committed_ledger_is_recorded(tmp_path, staged):
    """Started from a scratch store SEEDED from the tracked ledger, which is the
    fresh-checkout path. The move must be recorded with both bounds and both
    rationales, and the run's outcome must stop being a plain pass."""
    v = sc.do_accept(tmp_path, staged["v1_clean"], inject="BOUND_SILENT_CHANGE")
    assert v["bound_changes"], (
        "moving T20's bound onto 207.0 ms -- the value the withdrawn estimator "
        "reports -- was not recorded, so a green cell produced by moving a bound "
        "is indistinguishable from one produced by fixing the model")
    ch = next(c for c in v["bound_changes"] if c["metric"] == "T20")
    assert ch["previous"]["lo"] == pytest.approx(271.979, abs=0.01)
    assert ch["new"]["lo"] == 206.0 and ch["new"]["hi"] == 208.0
    assert ch["previous"]["rationale"] and ch["new"]["rationale"]
    assert v["verdicts"]["T20"]["state"] == "pass", (
        "the whitewash has to actually whitewash, or this proves nothing")
    state, code = sc.outcome_of(v)
    assert "BOUND MOVED" in state and code == 1, (
        "a verdict reached while a bound moved is not a verdict that passed")
    # And the rule-4 observation that falls out of it: the whitewash was written
    # for T20 and T20 alone, and `attack` -- the other lock on this voice, which
    # the same envelope defect moves 14.56 -> 9.94 ms -- still fails. Whitewashing
    # one metric does not buy a green run, because more than one property saw the
    # defect (docs/verification-rules.md 4).
    assert v["verdicts"]["attack"]["state"] == "fail"
    assert state == "fail (BOUND MOVED)"


# ---------------------------------------------------------------------------
# the injected controls: every one must be REFUSED by the stage that owns it
# ---------------------------------------------------------------------------
def test_the_controls_command_refuses_every_injection(tmp_path):
    """`tools/stage_case.py controls` as a gate, including its own precondition
    that the clean run passes (docs/verification-rules.md 5, condition 1). Run
    here as well as in CI because a control suite that only ever runs in a
    workflow is one nobody sees go red while they are editing."""
    assert sc.controls(tmp_path) == 0


@pytest.mark.parametrize("inject", sorted(sc.INJECTIONS))
def test_every_injection_names_a_stage_and_a_guard(inject):
    what, stage = sc.INJECTIONS[inject]
    assert stage in ("render", "analyse", "accept")
    assert len(what) > 30


def test_a_measurement_missing_a_required_field_gets_no_verdict(staged):
    """Issue #68's own Test Plan: a record missing units is rejected or flagged
    incomplete, not silently accepted. It is refused at `analyse`, before
    anything is written, so a partial analysis.json cannot be mistaken for a
    complete one -- and it is refused against the SAME retained render the clean
    analysis above measured, so the refusal cannot come from a missing input."""
    with pytest.raises(manifest.Refused, match="missing required field"):
        sc.do_analyse(staged["root"], staged["clean"], V1,
                      inject="MEASUREMENT_NO_UNITS")


def test_a_bound_with_no_rationale_gets_no_verdict(staged, tmp_path):
    with pytest.raises(manifest.Refused, match="no rationale"):
        sc.do_accept(tmp_path, staged["v2_clean"], inject="BOUND_NO_RATIONALE")


def test_a_silent_render_is_refused_as_the_apparatus_in_a_wrong_state(tmp_path):
    with pytest.raises(manifest.Refused, match="exact silence"):
        sc.do_render(tmp_path, "BD", None, "smoke", silent=True)


def test_which_guards_attributes_a_refusal_to_a_named_check():
    """A refusal that fires no named guard cannot be attributed to the defect,
    which is condition 3 of the three: the INTENDED assertion must be the one
    that fails."""
    assert sc.which_guards("... is missing required field(s): units") \
        == ["measurement field"]
    assert sc.which_guards("nothing in particular went wrong") == []


# ---------------------------------------------------------------------------
# 6. the committed evidence
# ---------------------------------------------------------------------------
def _committed(name):
    return json.loads((DEMO / f"{name}.json").read_text())


def test_the_committed_evidence_exists_and_includes_the_transcripts():
    """Committed so a reader can see the before/after without running anything,
    the same way `docs/scorecard/results/*.json` is committed."""
    assert (DEMO / "demo-transcript.txt").exists()
    assert (DEMO / "controls-transcript.txt").exists()
    for n in ("render-clean", "render-bd-decay-short", "analysis-v1-on-clean",
              "analysis-v2-on-clean", "analysis-v2-on-broken",
              "verdict-v1-on-clean", "verdict-v2-on-clean", "verdict-v2-on-broken",
              "delta-measurement-only", "delta-sound-only", "delta-unattributable"):
        assert (DEMO / f"{n}.json").exists(), n


def test_the_committed_evidence_retains_a_playable_wav_not_only_json():
    """"Raw WAVs and numerical traces are retained, not just summary
    statistics." A committed evidence directory of nothing but JSON would be
    summary statistics wearing a manifest."""
    wav = DEMO / "raw-clean.wav"
    assert wav.exists() and wav.stat().st_size > 100_000
    import provenance
    assert provenance.file_sha(wav) == _committed("render-clean")["wav_sha256"], (
        "the committed WAV is not the one the committed render manifest names")


@pytest.mark.parametrize("name,which", [("analysis-v1-on-clean", "v1_clean"),
                                        ("analysis-v2-on-clean", "v2_clean"),
                                        ("analysis-v2-on-broken", "v2_broken")])
def test_the_committed_measurements_match_what_the_tree_produces(staged, name, which):
    """STALE, not FAIL, is the thing being detected here.

    Deliberately compares the measured VALUES rather than the manifest ids. An
    id moves when the source moves at all, including a docstring, so gating on
    the id would go red on exactly the branch that edits the renderer -- and an
    unsatisfiable gate is worse than no gate (CLAUDE.md). The values move only
    when the sound or the measurement really moved, which is when the committed
    evidence genuinely needs refreshing:

        python tools/stage_case.py demo --root build/provenance \\
            --out docs/provenance/bd-decay-demo
    """
    was = {k: v["value"] for k, v in _committed(name)["measurements"].items()}
    now = {k: v["value"] for k, v in staged[which]["measurements"].items()}
    assert set(was) == set(now)
    drift = {k: (was[k], now[k]) for k in was
             if was[k] is None or now[k] is None or abs(was[k] - now[k]) > 1e-3}
    assert not drift, (
        f"STALE: docs/provenance/bd-decay-demo/{name}.json disagrees with this "
        f"tree: {drift}. If the change was intended, regenerate the evidence with "
        f"`tools/stage_case.py demo --out docs/provenance/bd-decay-demo` and say in "
        f"the commit message which of the sound and the measurement moved")


@pytest.mark.parametrize("name,expect", [("verdict-v2-on-clean", "pass"),
                                          ("verdict-v1-on-clean", "fail"),
                                          ("verdict-v2-on-broken", "fail")])
def test_the_committed_verdicts_say_what_the_document_says_they_say(name, expect):
    assert sc.outcome_of(_committed(name)) == (expect, {"pass": 0, "fail": 1}[expect])


@pytest.mark.parametrize("name,attribution", [
    ("delta-measurement-only", "measurement changed"),
    ("delta-sound-only", "sound changed")])
def test_the_committed_attributions_are_the_ones_claimed(name, attribution):
    assert _committed(name)["attribution"] == attribution


def test_the_committed_unattributable_delta_is_refused_and_says_why():
    d = _committed("delta-unattributable")
    assert not d["attributable"]
    assert d["renders"][0] != d["renders"][1]
    assert d["analysers"][0] != d["analysers"][1]
    t20 = d["metrics"]["T20"]
    assert abs(t20["delta"]) < t20["tolerance"], (
        "the point of this record is that the two defects partly cancel on T20")


# ---------------------------------------------------------------------------
# the CLI, because a stage nobody can run from the command line is a library
# ---------------------------------------------------------------------------
def test_the_cli_walks_the_three_stages(tmp_path, capsys):
    def run(*argv):
        """Drain the capture after every call: a readouterr() that spans two
        commands returns the first one's prose glued to the second one's JSON,
        which is a test bug that reads exactly like a CLI bug."""
        rc = sc.main([*argv, "--root", str(tmp_path)])
        return rc, capsys.readouterr().out

    rc, out = run("render", "--voice", "BD")
    assert rc == 0
    rid = json.loads(out)["render_id"]

    rc, out = run("analyse", "--render", rid, "--analyser", V1)
    assert rc == 0
    aid = json.loads(out)["job_id"]
    assert run("accept", "--analysis", aid)[0] == 1, (
        "the withdrawn estimator must exit 1 -- a mismatch is a RESULT, and is a "
        "different exit code from no evidence")

    rc, out = run("analyse", "--render", rid, "--analyser", V2)
    assert rc == 0
    bid = json.loads(out)["job_id"]
    assert run("accept", "--analysis", bid)[0] == 0

    rc, out = run("compare", "--before", aid, "--after", bid)
    assert rc == 0 and "measurement changed" in out


def test_the_cli_refuses_a_tampered_render_with_code_two(tmp_path, capsys):
    """Exit 2 is this repository's "no evidence" (`tools/run_case.py`'s
    OUTCOME_CODE), and is deliberately not 1: a tampered artefact is not a
    mismatch."""
    assert sc.main(["render", "--root", str(tmp_path)]) == 0
    rid = json.loads(capsys.readouterr().out)["render_id"]
    assert sc.main(["analyse", "--root", str(tmp_path), "--render", rid,
                    "--inject", "TAMPER_RETAINED_AUDIO"]) == 2
    assert "REFUSED" in capsys.readouterr().out, (
        "exit 2 with no REFUSED line is a refusal nobody reading the log can act on")


def test_the_cli_lists_the_analysers_variants_injections_and_retention(capsys):
    assert sc.main(["list"]) == 0
    out = capsys.readouterr().out
    assert V1 in out and V2 in out and "bd-decay-short" in out
    assert "reference-fixture" in out and "BOUND_SILENT_CHANGE" in out

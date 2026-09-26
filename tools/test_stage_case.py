#!/usr/bin/env python3
"""The end-to-end demonstration, as a gate.

Three renders and four analyses cost about twenty seconds, so this is a module
fixture rather than a per-test one. What it asserts, in order of how much it
matters:

  1. `analyse` renders NOTHING. Proved by making `render_voice` raise and then
     analysing anyway -- not by inspecting the code, which is how every bench in
     this repository that drove the wrong port passed.
  2. the withdrawn estimator on the RETAINED audio reproduces the historical
     numbers (BD T20 207 ms against 308), and the manifest attributes the delta
     to the measurement.
  3. the same estimator on a render with a real sound defect moves the same
     metric by a similar amount, and the manifest attributes THAT to the sound.
  4. both at once is refused rather than guessed.
  5. every record the real producer writes is complete by the schema's own
     standard -- a validator that only ever sees hand-written fixtures has not
     been shown to accept anything real.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "model"))
sys.path.insert(0, str(ROOT / "audition"))
sys.path.insert(0, str(ROOT / "tools"))

import measurement_manifest as mm                                    # noqa: E402
import sound_report as sr                                            # noqa: E402
import stage_case as sc                                              # noqa: E402

V1 = "decay-v1-moving-average-5ms"
V2 = "decay-v2-rms-per-voice"


@pytest.fixture(scope="module")
def staged(tmp_path_factory):
    root = tmp_path_factory.mktemp("provenance")
    clean = sc.do_render(root, "BD", None, "reference-fixture")
    broken = sc.do_render(root, "BD", "bd-decay-short", "smoke")
    return {"root": root, "clean": clean, "broken": broken,
            "v1_clean": sc.analyse_one(root, clean, V1),
            "v2_clean": sc.analyse_one(root, clean, V2),
            "v2_broken": sc.analyse_one(root, broken, V2)}


def value(analysis, metric):
    return next(m["value"] for m in analysis["measurements"] if m["metric"] == metric)


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
    rec = sc.analyse_one(staged["root"], staged["clean"], V1)
    assert rec["outcome"] == "measured"
    assert rec["read"]["sha256"] == staged["clean"]["artefacts"]["audio"]["sha256"]


def test_the_retained_audio_is_on_disk_and_is_not_only_a_summary(staged):
    d = mm.stage_dir(staged["root"], staged["clean"])
    wav = d / staged["clean"]["artefacts"]["audio"]["path"]
    traces = d / staged["clean"]["artefacts"]["traces"]["path"]
    assert wav.exists() and wav.stat().st_size > 100_000, (
        "105600 float32 samples is 422 kB; a smaller file is a summary")
    assert traces.exists(), "the internal traces were not retained"
    assert mm.sha256_file(wav) == staged["clean"]["artefacts"]["audio"]["sha256"]


def test_the_analysis_retains_the_numerical_trace_the_verdict_rests_on(staged):
    d = mm.stage_dir(staged["root"], staged["v2_clean"])
    p = d / staged["v2_clean"]["artefacts"]["traces"]["path"]
    assert p.exists()
    import numpy as np
    z = np.load(p)
    assert set(("envelope", "envelope_db", "fit_indices", "spectrum_hz",
                "spectrum_mag")) <= set(z.files)
    assert len(z["fit_indices"]) > 8


def test_a_tampered_retained_wav_is_refused_rather_than_measured(staged, tmp_path):
    r = sc.do_render(tmp_path, "BD", None, "smoke")
    sc.tamper(tmp_path, r)
    with pytest.raises(ValueError, match="REFUSED"):
        sc.analyse_one(tmp_path, r, V2)


# ---------------------------------------------------------------------------
# 2-4. attribution on a real case
# ---------------------------------------------------------------------------
def test_the_withdrawn_estimator_reproduces_the_historical_numbers(staged):
    """bf13fdd's own commit message: "BD T20 308 -> 207 ms, BD attack
    14.56 -> 9.94 ms; BD fundamental and decay tau stay BLIND." Reproduced here
    off a retained WAV rather than off a fresh render."""
    assert value(staged["v2_clean"], "T20") == pytest.approx(307.979, abs=0.01)
    assert value(staged["v1_clean"], "T20") == pytest.approx(207.04, abs=0.01)
    assert value(staged["v2_clean"], "attack") == pytest.approx(14.5625, abs=0.01)
    assert value(staged["v1_clean"], "attack") == pytest.approx(9.9375, abs=0.01)


def test_the_measurement_changed_and_the_sound_did_not(staged):
    d = mm.classify_delta(staged["v1_clean"], staged["v2_clean"])
    assert d["attribution"] == mm.MEASUREMENT_ONLY
    assert d["renders"][0] == d["renders"][1] == staged["clean"]["id"]
    assert d["metrics"]["T20"]["saw"] == "MOVED"
    assert d["metrics"]["decay tau"]["saw"] == "BLIND", (
        "docs/verification-rules.md 4: a metric that cannot see a defect is worth "
        "printing, and tau's 149 vs 144 is inside its own 36 ms tolerance")


def test_the_sound_changed_and_the_measurement_did_not(staged):
    d = mm.classify_delta(staged["v2_clean"], staged["v2_broken"])
    assert d["attribution"] == mm.SOUND_ONLY
    assert d["analysers"][0] == d["analysers"][1]
    assert d["metrics"]["T20"]["saw"] == "MOVED"
    assert d["metrics"]["attack"]["saw"] == "BLIND"


def test_the_two_defects_together_cannot_be_attributed_and_are_not(staged):
    d = mm.classify_delta(staged["v1_clean"], staged["v2_broken"])
    assert not d["attributable"] and d["render_changed"] and d["analyser_changed"]


def test_the_two_defects_partly_cancel_on_t20_which_is_why_refusing_matters(staged):
    """-101 ms from the ruler and -122 ms from the sound leave -21 ms, which is
    INSIDE T20's 36 ms tolerance. A comparison that attributed this pair would
    report the metric as unmoved while both halves of it were broken."""
    d = mm.classify_delta(staged["v1_clean"], staged["v2_broken"])
    assert d["metrics"]["T20"]["saw"] == "BLIND"
    assert abs(d["metrics"]["T20"]["delta"]) < 36.0


def test_the_verdicts_are_pass_fail_fail_for_three_different_reasons(staged):
    root = staged["root"]
    assert sc.do_accept(root, staged["v2_clean"])["outcome"] == "pass"
    v1 = sc.do_accept(root, staged["v1_clean"])
    assert v1["outcome"] == "fail" and v1["metrics"]["T20"]["state"] == "fail"
    vb = sc.do_accept(root, staged["v2_broken"])
    assert vb["outcome"] == "fail" and vb["metrics"]["decay tau"]["state"] == "fail"


# ---------------------------------------------------------------------------
# 5. the records the real producer writes are complete
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("which", ["v1_clean", "v2_clean", "v2_broken"])
def test_every_record_the_producer_writes_is_complete(staged, which):
    problems = mm.validate_measurements(staged[which]["measurements"])
    assert problems == [], problems
    assert staged[which]["outcome"] == "measured"


def test_every_measurement_names_its_units_its_method_and_its_interval(staged):
    for m in staged["v2_clean"]["measurements"]:
        assert m["units"] == "ms" or m["units"] == "Hz"
        assert m["analyser"]["method"] and m["analyser"]["version"]
        assert m["selection"]["interval_s"][1] > m["selection"]["interval_s"][0]
        assert m["bound"]["rationale"] and m["bound"]["history"]
        assert m["reference"]["sha256"].startswith("sha256:")


def test_a_target_and_a_lock_are_not_mixed(staged):
    kinds = {m["metric"]: m["bound"]["kind"] for m in staged["v2_clean"]["measurements"]}
    assert kinds == {"fundamental": "target", "decay tau": "target",
                     "T20": "lock", "attack": "lock"}


def test_the_bound_values_come_from_the_tables_that_own_them(staged):
    """Not copied. A second bound table would be a second thing to forget."""
    import drum_verify as dv
    by = {m["metric"]: m["bound"] for m in staged["v2_clean"]["measurements"]}
    assert by["decay tau"]["value"] == dv.SPEC["BD"]["tau_ms"]
    assert by["T20"]["value"] == sr.LOCKS[("BD", "T20")]
    assert by["attack"]["value"] == sr.LOCKS[("BD", "attack")]


def test_a_metric_sound_report_does_not_declare_gets_no_invented_bound():
    with pytest.raises(KeyError):
        sc.bound_for("BD", "a metric nobody declared")


# ---------------------------------------------------------------------------
# the record injections: every one must be REFUSED, never passed and never failed
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("inject", sorted(i for i in sc.INJECTIONS
                                          if i not in sc.REFUSED_AT))
def test_each_record_injection_is_refused_at_accept(staged, inject):
    a = sc.analyse_one(staged["root"], staged["clean"], V2, inject=inject)
    v = sc.do_accept(staged["root"], a)
    assert v["outcome"] == mm.REFUSED, (
        f"{inject} produced {v['outcome']!r}: an injection that is not refused is "
        f"a hole in the schema, and an incomplete record that gets a verdict is "
        f"worse than one that gets none")
    assert v["outcome_code"] == 2
    assert sc.which_checks(v["schema_problems"]), (
        f"{inject} was refused but by no named check, so the refusal cannot be "
        f"attributed to the defect")


def test_the_clean_run_passes_so_the_injections_mean_something(staged):
    """Condition 1 of the three (docs/verification-rules.md 5): a control only
    counts as caught if the clean run passes. Asserted, not assumed."""
    assert sc.do_accept(staged["root"], staged["v2_clean"])["outcome"] == "pass"


def test_the_bound_whitewash_injection_would_have_passed_the_v1_defect(staged):
    """BOUND_SILENT_CHANGE moves T20's bound to 207.0 -- the value the withdrawn
    estimator reports. Under the v1 analysis that is a green cell produced by
    moving a bound, and this asserts the changelog check is the only thing
    stopping it."""
    a = sc.analyse_one(staged["root"], staged["clean"], V1,
                       inject="BOUND_SILENT_CHANGE")
    v = sc.do_accept(staged["root"], a)
    assert v["outcome"] == mm.REFUSED
    t20 = next(m for m in a["measurements"] if m["metric"] == "T20")
    assert t20["bound"]["value"] == 207.0
    assert abs(t20["value"] - 207.0) < t20["bound"]["tolerance"], (
        "the whitewash has to actually whitewash, or this control proves nothing")


# ---------------------------------------------------------------------------
# the CLI, because a stage nobody can run from the command line is a library
# ---------------------------------------------------------------------------
def test_the_cli_walks_the_three_stages(tmp_path, capsys):
    assert sc.main(["render", "--root", str(tmp_path), "--voice", "BD"]) == 0
    rid = [e["id"] for e in _index(tmp_path) if e["stage"] == "render"][0]
    assert sc.main(["analyse", "--root", str(tmp_path), "--render", rid,
                    "--analyser", V1]) == 0
    aid = [e["id"] for e in _index(tmp_path) if e["stage"] == "analyse"][0]
    assert sc.main(["accept", "--root", str(tmp_path), "--analysis", aid]) == 1, (
        "the withdrawn estimator must exit 1 -- a mismatch is a RESULT, and is a "
        "different exit code from no evidence")
    assert sc.main(["analyse", "--root", str(tmp_path), "--render", rid,
                    "--analyser", V2]) == 0
    bid = [e["id"] for e in _index(tmp_path)
           if e["stage"] == "analyse" and e["id"] != aid][0]
    assert sc.main(["accept", "--root", str(tmp_path), "--analysis", bid]) == 0
    assert sc.main(["compare", "--root", str(tmp_path),
                    "--before", aid, "--after", bid]) == 0
    out = capsys.readouterr().out
    assert mm.MEASUREMENT_ONLY in out


def test_the_cli_refuses_a_tampered_render_with_code_two(tmp_path):
    assert sc.main(["render", "--root", str(tmp_path)]) == 0
    rid = [e["id"] for e in _index(tmp_path) if e["stage"] == "render"][0]
    assert sc.main(["analyse", "--root", str(tmp_path), "--render", rid,
                    "--inject", "TAMPER_RETAINED_AUDIO"]) == 2


def _index(root):
    import json
    return json.loads((pathlib.Path(root) / "index.json").read_text())

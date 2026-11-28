import hashlib
import itertools
import json
import subprocess
import numpy as np
import pytest
from scipy.io import wavfile
import check_m1a_selection as selection
import mono_m1a_score as bass
import run_case
import scorecard


def signal(cents=0, *, low_note_ramp=240, low_note_power=1):
    t = np.arange(round(bass.reference.SECONDS * bass.SR)) / bass.SR
    audio = np.zeros_like(t)
    for event in bass.reference.EVENTS:
        dt = t - event["on_s"]
        # The middle note keeps its 4 ms 10-90 attack as a pass control.
        span, power = (low_note_ramp, low_note_power) if event["note"] == 36 else (240, 1)
        env = np.clip(dt * bass.SR / span, 0, 1) ** power
        off = dt >= event["gate_s"]
        env[off] = np.exp(-(dt[off] - event["gate_s"]) / .05)
        hz = 440 * 2 ** ((event["note"] - 69 + cents / 100) / 12)
        phase = 2 * np.pi * hz * t
        audio += .1 * env * (np.sin(phase) + .2 * np.sin(2 * phase) + .1 * np.sin(3 * phase))
    return audio


@pytest.mark.parametrize(("span", "expected"), [(347, "fail"), (348, "fail"), (346, "pass")])
def test_qualified_attack_grade_survives_score_serialization(span, expected, capsys):
    measured = bass.compare_audio(signal(low_note_ramp=span),
                                  signal(low_note_ramp=75, low_note_power=3))
    metrics = bass.required_metrics(measured)
    measured, metrics = json.loads(json.dumps([measured, metrics]))
    attack = measured["properties"]["Envelope attack"]
    release = measured["properties"]["Envelope release"]
    assert attack["valid"] and release["valid"] and metrics["envelope"]["valid"]
    assert abs(release["error"]) <= release["tolerance"]
    assert [e["attack_state"] for e in measured["events"]] == [expected, "pass", expected]
    timings = [e["attack_10_90_ms"] for e in measured["events"]]
    worst = max((t["model"] - t["reference"] for t in timings), key=abs)
    if span == 347:  # The five-decimal store used to turn this miss into a pass.
        assert worst > 5 and round(worst, 5) == 5
    passed = expected == "pass"
    assert ("Envelope attack" in selection._passes(measured)) == passed
    distance = scorecard.metric_distance(metrics["envelope"])
    assert (distance <= 1) == passed
    assert attack["error"] == worst
    assert distance == max(abs(worst) / 5, abs(release["error"]) / release["tolerance"])
    selection.print_properties(measured)
    line = next(s for s in capsys.readouterr().out.splitlines() if s.strip().startswith("Envelope attack "))
    assert line.split()[-1] == expected


def test_known_bass_and_pitch_mutation():
    clean = bass.compare_audio(signal(), signal())
    changed = bass.compare_audio(signal(25), signal())
    assert abs(clean["properties"]["Pitch"]["error"]) < .01
    assert changed["properties"]["Pitch"]["error"] == pytest.approx(25, abs=.5)
    assert clean["properties"]["Envelope release"]["valid"]
    # identical audio on both sides: the qualified attack must read the same
    # 4 ms 10-90 span it was built with, within the estimator's own bound
    assert clean["properties"]["Envelope attack"]["valid"]
    assert abs(clean["properties"]["Envelope attack"]["error"]) < 1.0
    assert abs(changed["properties"]["Envelope attack"]["error"]) < 1.0
    # the attack fit measures 10-90% spans in MILLISECONDS: its values are
    # attack_10_90_ms and its tolerance basis says ms. The published record
    # once labelled this diagnostic "dB" -- arithmetic right, metadata wrong.
    assert clean["properties"]["Envelope attack"]["units"] == "ms", \
        clean["properties"]["Envelope attack"]
    assert clean["properties"]["Envelope release"]["units"] == "ms"
    assert clean["properties"]["Harmonic shape"]["units"] == "dB"
    assert clean["properties"]["Gain"]["units"] == "dB"
    for event in clean["events"]:
        assert event["harmonics_db"]["model"]["h2"] == pytest.approx(20 * np.log10(.2), abs=.05)
        assert event["harmonics_db"]["model"]["h3"] == pytest.approx(-20., abs=.05)
        assert event["release_t20_ms"]["model"] == pytest.approx(50 * np.log(10), abs=10)
        assert event["attack_10_90_ms"]["model"] == pytest.approx(4., abs=1.0)
        assert event["attack_10_90_ms"]["reference"] == pytest.approx(4., abs=1.0)


def test_missing_or_corrupt_reference_refuses(tmp_path):
    manifest = json.loads(bass.MANIFEST.read_text())
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    with pytest.raises(bass.Refused, match="reference"):
        bass.load_reference(path)
    wavfile.write(tmp_path / manifest["renders"][0]["file"], bass.SR,
                  np.zeros(100, dtype=np.float32))
    with pytest.raises(bass.Refused, match="reference"):
        bass.load_reference(path)


def test_silent_and_truncated_candidates_refuse():
    clean = signal()
    with pytest.raises(bass.Refused):
        bass.compare_audio(np.zeros_like(clean), clean)
    with pytest.raises(bass.Refused):
        bass.compare_audio(clean[:100], clean)


def test_runner_executes_bass_with_qualified_envelope():
    assert run_case.plan_for("M1A") == "mono-bass"
    measured = bass.compare_audio(signal(), signal())
    metrics = bass.required_metrics(measured)
    case = next(c for c in run_case.load_cases() if c["case_id"] == "M1A")
    required = {x.strip() for x in case["required_measurements"].split(";")}
    assert required <= metrics.keys()
    assert all(m["valid"] for m in metrics.values()), \
        f"an invalid metric makes the case no-verdict: {[m for m in metrics if not m['valid']]}"
    assert metrics["envelope"]["valid"]
    assert metrics["Fundamental/harmonics"]["valid"]
    assert metrics["bass level"]["valid"]


def test_render_provenance_uses_existing_dsp_sources(monkeypatch):
    # Exercise the complete reporting path without an expensive synth render.
    # This caught the mistaken model/dsp.py path (the module is in audition/).
    pcm = np.asarray(signal() * 32768, dtype=np.int16)
    monkeypatch.setattr(bass.lead.vf, "render_mono_fx", lambda *args: pcm)
    case = next(c for c in run_case.load_cases() if c["case_id"] == "M1A")
    record = bass.run(case, keep_audio=False)
    assert record["provenance"]["inputs"]["audition/dsp.py"] == "sha256:" + bass.sha(bass.ROOT / "audition/dsp.py")
    assert record["provenance"]["inputs"]["model/fixed.py"] == "sha256:" + bass.sha(bass.ROOT / "model/fixed.py")


def test_changed_cached_audio_refuses(tmp_path):
    path = tmp_path / "changed.wav"
    path.write_bytes(b"corrupt")
    record = {"audio": str(path), "engine": "fixed-model",
              "diagnostics": {"configuration": {"patch": {}, "engine": {}, "inject": None},
                              "model_audio_sha256": "original"}}
    with pytest.raises(bass.Refused, match="hash mismatch"):
        bass.load_model_cache(record, {}, {})


# --- attack qualification on BOTH sides ---------------------------------------
# A reading outside attack_fit_v3's known-answer domain (a region of reported
# 10-90 value x explained ratio, off the ramp search boundary) is UNQUALIFIED.

def _patched_fits(monkeypatch, side, **fit):
    real = bass.attack_fit_v3.attack_fit_v3
    calls = {"n": 0}

    def fake(*args, **kwargs):
        out = dict(real(*args, **kwargs))
        which = ("model", "reference")[calls["n"] % 2]     # compare_audio calls model first
        calls["n"] += 1
        if which == side:
            out.update(fit)
        return out
    monkeypatch.setattr(bass.attack_fit_v3, "attack_fit_v3", fake)


@pytest.mark.parametrize("side", ["model", "reference"])
@pytest.mark.parametrize("fit", [dict(shape_p=4.0, ramp_ms=0.6666666666666666,
                                      attack_10_90_ms=0.27444, search_boundary="minimum"),
                                 dict(shape_p=1.0, ramp_ms=10.0, attack_10_90_ms=8.0),
                                 dict(explained_ratio=0.72)])
def test_out_of_domain_attack_on_either_side_is_unqualified(monkeypatch, side, fit):
    _patched_fits(monkeypatch, side, **fit)
    measured = bass.compare_audio(signal(), signal())
    attack = measured["properties"]["Envelope attack"]
    assert attack["valid"] is False and attack["state"] == "no verdict"
    assert side in attack["why"]
    assert all(e["attack_state"] == "unqualified" for e in measured["events"])
    metrics = bass.required_metrics(measured)
    assert metrics["envelope"]["valid"] is False           # no partial maximum
    assert metrics["Fundamental/harmonics"]["valid"] and metrics["bass level"]["valid"]


def test_control_pre_rule_scorer_would_have_graded_it(monkeypatch):
    """The same out-of-domain reference fit, with the rule off, is GRADED --
    which is what the published board did with M1A's p=3 / p=4 reference fits."""
    _patched_fits(monkeypatch, "reference", shape_p=4.0, ramp_ms=0.6666666666666666,
                  attack_10_90_ms=0.27444, search_boundary="minimum")
    old = bass.compare_audio(signal(), signal(), attack_domain_rule=False)
    assert old["properties"]["Envelope attack"]["valid"] is True
    assert bass.required_metrics(old)["envelope"]["valid"] is True


def test_in_domain_fits_still_grade():
    measured = bass.compare_audio(signal(), signal())
    assert measured["properties"]["Envelope attack"]["valid"]
    assert all(e["attack_state"] == "pass" for e in measured["events"])
    ok = {"attack_10_90_ms": 4.0, "explained_ratio": 0.99, "search_boundary": None}
    assert bass.attack_fit_qualified(ok) is None
    assert bass.attack_fit_qualified({**ok, "attack_10_90_ms": 8.0})


def test_exact_negative_five_ms_attack_still_passes(monkeypatch):
    _patched_fits(monkeypatch, "model", attack_10_90_ms=.75)
    _patched_fits(monkeypatch, "reference", attack_10_90_ms=5.75)
    measured = bass.compare_audio(signal(), signal())
    assert [e["attack_state"] for e in measured["events"]] == ["pass"] * 3
    assert measured["properties"]["Envelope attack"]["error"] == -5.0
    assert "Envelope attack" in selection._passes(measured)
    assert scorecard.metric_distance(bass.required_metrics(measured)["envelope"]) == 1.0


# --- the qualification the v3 record embeds -----------------------------------
# The attack is graded inside attack_fit_v3's known-answer domain, yet run()
# also called reference.qualify_envelope_basis(), whose `attack` entry runs
# the RETIRED v2 fit 56 times and stamps its "<1 ms known-signal error" into a
# record whose diagnostics.wrong_then_right.attack_v3 refutes that claim (#1423).

#: Everything but `attack` in reference.qualify_envelope_basis(), as the frozen
#: module published it in docs/scorecard/results/M1A.json at 283b3a43. The
#: scorer carries a port of that release half (its digest-pinned module cannot
#: supply it without the v2 fit) and is held to these rows exactly.
FROZEN_RELEASE = {
    "known_release_ms": 230.25850929940458,
    "spectral_window_s": .25,
    "lead_window": {"window_ms": 5.0,
                    "release_ms": [201.8125, 229.02083333333334, 207.16666666666666],
                    "max_error_ms": 28.44600929940458},
    "bass_window": {"window_ms": 40.0,
                    "release_ms": [233.5625, 231.1875, 232.25],
                    "max_error_ms": 3.3039907005954205},
    "scope": "40 ms RMS window qualified for release ONLY; attack is measured by "
             "the waveform-domain fit, not by any RMS window",
    "initial_failures": "100 ms spectral window refused below 12 periods; 5 ms envelope "
                        "window failed known release; early h4 window could not resolve "
                        "the short filter transient",
}


def _forbid_retired_v2(monkeypatch):
    def retired(*args, **kwargs):
        raise AssertionError("the v3 scorer ran the retired v2 attack qualification")
    monkeypatch.setattr(bass.reference, "qualify_attack_basis", retired)
    monkeypatch.setattr(bass.reference, "attack_fit", retired)


def _assert_v3_attack_summary(attack):
    report = json.loads((bass.QUALIFICATION / "qualification.json").read_text())
    suite = json.loads((bass.QUALIFICATION / "suite-v3.json").read_text())
    assert attack["estimator"] == "attack_fit_v3"
    assert attack["schema"] == report["schema"]
    assert attack["bound_ms"] == report["bound_ms"]
    assert attack["cases"] == len(suite["rows"]) == 1898
    assert attack["search_boundary_hits"] == report["v3"]["search_boundary_hits"]
    assert attack["domain"] == bass.qualified_attack_domain() == report["v3"]["domain"]
    assert "observations" not in attack
    assert attack["qualification_path"] == ("docs/scorecard/mono-m1a-miniv3/"
                                            "attack-qualification/qualification.json")


def test_qualification_is_the_release_basis_and_the_v3_attack_domain(monkeypatch):
    _forbid_retired_v2(monkeypatch)
    qualification = bass.qualify_envelope_basis()
    for key, frozen in FROZEN_RELEASE.items():
        assert qualification[key] == frozen, key
    # the 40 ms window measures the known release; the 5 ms lead window cannot
    assert qualification["bass_window"]["max_error_ms"] < 10
    assert qualification["lead_window"]["max_error_ms"] > 10
    _assert_v3_attack_summary(qualification["attack"])


def test_run_records_the_v3_qualification_without_running_the_retired_v2_fit(monkeypatch):
    _forbid_retired_v2(monkeypatch)
    pcm = np.asarray(signal() * 32768, dtype=np.int16)
    monkeypatch.setattr(bass.lead.vf, "render_mono_fx", lambda *args: pcm)
    case = next(c for c in run_case.load_cases() if c["case_id"] == "M1A")
    record = bass.run(case, keep_audio=False)
    assert record["analysis_version"] == "m1a-envelope-score-v3"
    qualification = record["diagnostics"]["qualification"]
    assert qualification == bass.qualify_envelope_basis()
    _assert_v3_attack_summary(qualification["attack"])


@pytest.mark.parametrize("lying_window",
                         [bass.reference.ENVELOPE_WINDOW_MS, 5.0], ids=["bass", "lead"])
def test_control_release_qualification_refuses_a_window_that_lies(monkeypatch, lying_window):
    """Each half of the gate, shown able to go red. A 40 ms window that reads
    what the 5 ms one does cannot measure release; a 5 ms window that reads
    what the 40 ms one does is a control that no longer fails. The other
    window is left honest, so the refusal can only come from the lying one."""
    real = bass.lead.am.rms_envelope
    other = 5.0 if lying_window == bass.reference.ENVELOPE_WINDOW_MS else bass.reference.ENVELOPE_WINDOW_MS
    asked = []

    def lying(audio, ms, sr):
        asked.append(ms)
        return real(audio, ms=other if ms == lying_window else ms, sr=sr)

    monkeypatch.setattr(bass.lead.am, "rms_envelope", lying)
    with pytest.raises(bass.Refused, match="qualification or wrong-window control failed"):
        bass.qualify_envelope_basis()
    assert asked == [5.0, bass.reference.ENVELOPE_WINDOW_MS]


def test_qualification_refuses_a_stale_attack_suite(monkeypatch):
    real = bass.sha
    monkeypatch.setattr(bass, "sha", lambda p: "0" * 64 if str(p).endswith("attack_fit_v3.py") else real(p))
    with pytest.raises(bass.Refused, match="stale"):
        bass.qualify_envelope_basis()


# --- the cached record's source_commit reaches git in the object position ------
# `git diff --exit-code <source_commit> -- model audition`: a recorded value
# beginning with `-` is git's option, not an object (#2082, #2104). The form is
# checked first, the name is then resolved, and only the resolved ID goes on.

RESOLVED_CEA5525 = "cea552573a80b89f2f7860a7c294d272da692f1a"      # what `cea5525` names
DIFF = ["git", "diff", "--exit-code"]
REV_PARSE = ["git", "rev-parse", "--verify", "-q"]


def _cacheable_record(tmp_path, source_commit):
    """A record that passes every check before the git one: a real int16 WAV
    at `bass.SR`, bound to the reference manifest the checkout ships."""
    pcm_path = tmp_path / "model.wav"
    wavfile.write(pcm_path, bass.SR, np.zeros(480, dtype="<i2"))
    return {"audio": str(pcm_path), "engine": "fixed-model", "source_commit": source_commit,
            "diagnostics": {"configuration": {"patch": {}, "engine": {}, "inject": None},
                            "model_audio_sha256": bass.sha(pcm_path)},
            "provenance": {"inputs": {"frozen:M1A:manifest": "sha256:" + bass.sha(bass.MANIFEST)}}}


def _record_git(monkeypatch, answer):
    """Every `subprocess.run` the module makes is recorded and answered by
    `answer(argv, real, kwargs)`, which may hand a call to real git."""
    real = subprocess.run
    calls = []

    def run(argv, **kwargs):
        calls.append(list(argv))
        return answer(list(argv), real, kwargs)
    monkeypatch.setattr(subprocess, "run", run)
    return calls


def _resolve_then_stub_diff(argv, real, kwargs):
    """Real git resolves the name; the diff that follows is answered "no
    change", so a control does not depend on the DSP tree being unchanged
    since the recorded commit."""
    if argv[:len(REV_PARSE)] == REV_PARSE:
        return real(argv, **kwargs)
    assert argv[:3] == DIFF, argv
    return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")


def _no_git(argv, real, kwargs):
    """Were the unguarded code to reach git, nothing is written: exit 1."""
    return subprocess.CompletedProcess(argv, 1, stdout="", stderr="")


def _scratch_repo(path, *dirs):
    """A committed repository under `path` holding `dirs`."""
    path.mkdir(parents=True, exist_ok=True)
    for d in dirs:
        (path / d).mkdir(parents=True)
        (path / d / "dsp.py").write_text("x = 1\n")
    for args in (["init", "-q"], ["add", "."],
                 ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "init"]):
        subprocess.run(["git", *args], cwd=path, check=True, capture_output=True)
    return path


def test_control_recorded_option_truncates_a_file_and_exits_zero(tmp_path):
    """The input that defeats the unguarded check, on real git. With the
    record's text in the object position, `--output=PATH` is git's own option:
    git writes the diff to PATH -- creating it, truncating it if it exists --
    compares the working tree with the index because no object was named, and
    exits 0 on a clean tree. So `returncode != 0` saw a pass and no commit was
    ever compared. Pinned on a scratch repository so the answer does not
    depend on this checkout's state; if it fails, git changed, and the guard's
    premise changed with it."""
    repo = _scratch_repo(tmp_path / "repo", "model")
    fresh, victim = tmp_path / "fresh", tmp_path / "victim"
    assert not fresh.exists()
    victim.write_bytes(b"keep me\n")
    for target in (fresh, victim):
        diffed = subprocess.run([*DIFF, f"--output={target}", "--", "model"],
                                cwd=repo, capture_output=True, text=True)
        assert diffed.returncode == 0, diffed.stderr
        assert target.read_bytes() == b"", target


@pytest.mark.parametrize("preexisting", [False, True], ids=["created", "truncated"])
def test_recorded_option_in_source_commit_is_refused_before_git(tmp_path, monkeypatch, preexisting):
    """The guard: the same input, refused by its form before any git runs, and
    the named file is neither created nor truncated. Every call is handed to
    real git, so an absent guard truncates the file here too."""
    target = tmp_path / "target"
    if preexisting:
        target.write_bytes(b"keep me\n")
    calls = _record_git(monkeypatch, lambda argv, real, kwargs: real(argv, **kwargs))
    with pytest.raises(bass.Refused, match="source_commit is not a hex object name"):
        bass.load_model_cache(_cacheable_record(tmp_path, f"--output={target}"), {}, {})
    assert calls == []
    if preexisting:
        assert target.read_bytes() == b"keep me\n"
    else:
        assert not target.exists()


@pytest.mark.parametrize("source_commit", [
    "CEA5525", "CEA552573A80B89F2F7860A7C294D272DA692F1A",
    "cea", "cea552573a80b89f2f7860a7c294d272da692f1aa",
    "cea5525\n", " cea5525", "cea5525 ", "cea-5525", "cea5525^", "cea5525~1", "cea5525:",
    "HEAD", "", "-cea5525", "--output=/tmp/x", "--",
    None, 212, 3.5, b"cea5525", ["cea5525"], {"commit": "cea5525"},
], ids=repr)
def test_malformed_source_commit_is_refused_before_git(tmp_path, monkeypatch, source_commit):
    """Uppercase hex, 3 and 41 characters, surrounding whitespace, every
    revision syntax git would otherwise read, and a non-string."""
    calls = _record_git(monkeypatch, _no_git)
    with pytest.raises(bass.Refused, match="source_commit is not a hex object name"):
        bass.load_model_cache(_cacheable_record(tmp_path, source_commit), {}, {})
    assert calls == []


@pytest.mark.parametrize("recorded", ["cea5525", RESOLVED_CEA5525])
def test_shipped_id_resolves_to_the_full_object_id_git_is_given(tmp_path, monkeypatch, recorded):
    """Positive control: the guard does not refuse everything.
    `docs/scorecard/results/M1A.json` records `cea5525`, because provenance
    writes `rev-parse --short`; it passes, real git resolves it, and `git diff`
    is given the commit it names -- the 40-hex ID, never the record's text."""
    shipped = json.loads((bass.ROOT / "docs/scorecard/results/M1A.json").read_text())["source_commit"]
    assert shipped == "cea5525"
    calls = _record_git(monkeypatch, _resolve_then_stub_diff)
    assert len(bass.load_model_cache(_cacheable_record(tmp_path, recorded), {}, {})) == 480
    assert calls == [[*REV_PARSE, f"{recorded}^{{commit}}"],
                     [*DIFF, RESOLVED_CEA5525, "--", "model", "audition"]]


def test_a_resolved_commit_whose_tree_differs_still_refuses(tmp_path, monkeypatch):
    """The check the diff existed for is unchanged: a change is a refusal."""
    def changed(argv, real, kwargs):
        done = _resolve_then_stub_diff(argv, real, kwargs)
        return done if argv[:len(REV_PARSE)] == REV_PARSE else subprocess.CompletedProcess(argv, 1)
    _record_git(monkeypatch, changed)
    with pytest.raises(bass.Refused, match="DSP sources differ"):
        bass.load_model_cache(_cacheable_record(tmp_path, "cea5525"), {}, {})


def test_hex_naming_no_commit_is_refused_before_the_diff(tmp_path, monkeypatch):
    """The form passes and the name does not: real git is asked to resolve it,
    answers that it names nothing, and no diff follows."""
    calls = _record_git(monkeypatch, _resolve_then_stub_diff)
    with pytest.raises(bass.Refused, match="source_commit names no single commit"):
        bass.load_model_cache(_cacheable_record(tmp_path, "f" * 40), {}, {})
    assert [c[:len(REV_PARSE)] for c in calls] == [REV_PARSE]


def test_abbreviation_naming_two_objects_is_refused_before_the_diff(tmp_path, monkeypatch):
    """An abbreviation that names two objects is not a commit. Real git answers
    `rev-parse --verify -q` with exit 1 and nothing else for it -- the same
    silence as a name matching nothing (probed on git 2.43) -- so both are one
    refusal; this one is built, with two blobs sharing a four-character prefix."""
    repo = _scratch_repo(tmp_path / "repo")
    fmt = subprocess.run(["git", "rev-parse", "--show-object-format"], cwd=repo,
                         check=True, capture_output=True, text=True).stdout.strip()
    assert fmt == "sha1", fmt
    seen = {}
    for i in itertools.count():
        body = f"candidate {i}".encode()
        oid = hashlib.sha1(b"blob %d\0" % len(body) + body).hexdigest()
        first = seen.setdefault(oid[:4], (oid, body))
        if first[0] != oid:
            break
    for oid, content in (first, (oid, body)):
        assert subprocess.run(["git", "hash-object", "-w", "--stdin"], cwd=repo, input=content,
                              check=True, capture_output=True).stdout.strip().decode() == oid
    monkeypatch.setattr(bass, "ROOT", repo)
    calls = _record_git(monkeypatch, _resolve_then_stub_diff)
    with pytest.raises(bass.Refused, match="source_commit names no single commit"):
        bass.load_model_cache(_cacheable_record(tmp_path, oid[:4]), {}, {})
    assert [c[:len(REV_PARSE)] for c in calls] == [REV_PARSE]

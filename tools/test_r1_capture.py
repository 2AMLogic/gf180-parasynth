"""R1 capture inputs (#324): R1-bound references and commands for the physical
capture procedure, and the refusal in BOTH directions between R0 and R1.

What these tests are about is one failure shape: a reference set (or a
session, or a transcript) that belongs to the OTHER image being accepted
because the check that was written compared labels. R0's release string is
"arty-a7-100t baseline 2025.1, r1" -- its own revision 1 -- so even a
substring test for "r1" is satisfied by R0. Every guard here is therefore paired
with the input that defeats a label-only check: R0 bytes under R1 labels.

REFUSED is a third outcome, distinct from PASS and FAIL; an R0 reference set
presented to the R1 procedure must be REFUSED, never FAIL (a measured defect)
and never PASS.

No R1 reference set is committed with this change: rendering it replays the
image's RTL for a long time and belongs on the build box (docs/capture-r1.md).
So the tests build an R1-SHAPED world out of R0's measured material, bound to a
test-only manifest, to prove the machinery accepts material that is bound to
ITS image, and refuse everything else against the real R1 manifest.
"""
from __future__ import annotations

import copy
import json
import pathlib
import shutil
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import r0_capture as rc                                           # noqa: E402
import r0_reference as rr                                         # noqa: E402

R1_BITSTREAM = "544499e2c97061957004f999caf21d5caaf520f6b2682f84c6e6773e7d291d21"
R0_BITSTREAM = "a66c9349ef9b5572f3c3453777f38e1b143136755620fe419e730d6f5c84cb95"
R0_REFS = rc.REFERENCES
R1_ONLY_COMMANDS = {"run-m5a"}          # R1's replacement for R0's `play --fixture m5a`
SMALL_TAKES = (("silence-1", "silence"), ("tone-1", "held-m5a-saw"))


# ---- the manifest, normalised ------------------------------------------------
def test_r1_manifest_is_loaded_as_r1_bound_to_the_published_image():
    m = rr.load_manifest(rr.IMAGES["r1"]["manifest"])
    assert m["image_id"] == "r1"
    assert m["image"]["bitstream_sha256"] == R1_BITSTREAM
    assert m["image"]["source_commit"].startswith("6864435")
    assert {"held-default", "held-m5a-saw", "held-m5a-pulse", "run-m5a", "demo",
            "bar808-full"} == set(m["commands"])           # live-midi has no pinned bytes
    for spec in m["commands"].values():
        assert len(spec["cmds_sha256"]) == 64 and spec["command"].endswith("--image r1")
    assert "phrase-m5a" not in m["commands"]       # R0's removed `play --fixture m5a`


def test_r0_manifest_is_still_r0_and_the_two_images_share_no_pinned_bytes():
    a = rr.load_manifest(rr.IMAGES["r0"]["manifest"])
    b = rr.load_manifest(rr.IMAGES["r1"]["manifest"])
    assert a["image_id"] == "r0" and b["image_id"] == "r1"
    assert a["image"]["bitstream_sha256"] == R0_BITSTREAM != b["image"]["bitstream_sha256"]
    for k in set(a["commands"]) & set(b["commands"]):
        assert a["commands"][k]["cmds_sha256"] != b["commands"][k]["cmds_sha256"], k
    assert rr.IMAGES["r0"]["refdir"] != rr.IMAGES["r1"]["refdir"]


def test_an_unknown_manifest_schema_is_refused(tmp_path):
    p = tmp_path / "m.json"
    p.write_text(json.dumps({"schema": "something else", "image": {}, "commands": {}}))
    with pytest.raises(rr.Refused, match="schema"):
        rr.load_manifest(p)


# ---- the shipped host emits the bytes the manifest pins -----------------------
def test_the_hosts_r1_selection_emits_exactly_the_pinned_command_bytes():
    """Checks the thing that ships: the CLI under `--image r1`, not the tree's
    default selection. The bytes are compared with the manifest's pin."""
    m = rr.load_manifest(rr.IMAGES["r1"]["manifest"])
    assert rr.command_bytes_problems(m) == []


def test_the_command_bytes_check_sees_a_changed_pin(tmp_path):
    m = rr.load_manifest(rr.IMAGES["r1"]["manifest"])
    m["commands"]["held-default"]["cmds_sha256"] = "0" * 64
    probs = rr.command_bytes_problems(m, keys=["held-default"])
    assert probs and "held-default" in probs[0]


def test_the_host_default_selection_is_not_r1s_bytes():
    """The control for the check above: the SAME command without `--image r1`
    sends R0's bytes, which the R1 pin refuses. If this did not differ, the
    check could not tell the selections apart."""
    m = rr.load_manifest(rr.IMAGES["r1"]["manifest"])
    spec = dict(m["commands"]["held-default"])
    spec["command"] = spec["command"].replace(" --image r1", "")
    m["commands"] = {"held-default": spec}
    assert rr.command_bytes_problems(m)


# ---- the renderer refuses a tree that is not the image's ----------------------
def test_render_refuses_a_tree_whose_sources_are_not_the_images(tmp_path, capsys):
    """Main has moved past R1's freeze (voice_dp.v etc.); a reference rendered
    from this tree would describe a bitstream nobody built. Exit 2, REFUSED,
    before any simulation. (If this tree's RTL ever equals R1's again, this
    test says so and must be rewritten, not skipped.)"""
    m = rr.load_manifest(rr.IMAGES["r1"]["manifest"])
    moved = rr.image_source_problems(m)
    assert moved, "the tree's sources equal R1's: the staging guard is untested here"
    code = rr.main(["--image", "r1", "render", "--out", str(tmp_path / "o")])
    assert code == 2
    assert "REFUSED" in capsys.readouterr().out


def test_stage_plan_names_exactly_the_sources_that_moved():
    m = rr.load_manifest(rr.IMAGES["r1"]["manifest"])
    plan = rr.stage_plan(m)
    assert set(plan) == {p.split(":")[0] for p in rr.image_source_problems(m)}
    assert plan, "nothing moved"


# ---- the reference bindings: both directions ----------------------------------
def relabel(src: pathlib.Path, dst: pathlib.Path, manifest: dict, *, rename=None,
            bind_bytes=False) -> pathlib.Path:
    """Copy a reference set and rewrite its LABELS to `manifest`'s image: the
    schema, image id, release and image identity. With bind_bytes False the
    pinned command bytes and RTL source hashes stay the source image's -- the
    input that defeats a label-only check."""
    shutil.copytree(src, dst)
    idm = rr.IMAGES[manifest["image_id"]]
    for p in sorted(dst.glob("*.json")):
        if p.name.endswith(".plan.json"):
            continue
        rec = json.loads(p.read_text())
        rec["schema"] = idm["ref_schema"]
        if rec.get("command_id") != "silence":
            rec["release"] = manifest["release"]
            rec["image"] = {k: manifest["image"][k] for k in ("bitstream_sha256",
                                                              "routed_dcp_sha256",
                                                              "source_commit")}
        rec["image_id"] = manifest["image_id"]
        p.write_text(json.dumps(rec, indent=1, sort_keys=True) + "\n")
    return dst


def test_r0_references_are_refused_against_r1s_manifest(tmp_path):
    m1 = rr.load_manifest(rr.IMAGES["r1"]["manifest"])
    for key in ("held-default", "held-m5a-saw", "demo", "bar808-full"):
        why = rr.reference_problems(R0_REFS, key, m1)
        assert why, key
        assert any("bitstream" in w for w in why), why


def test_the_r0_references_still_satisfy_the_r0_manifest():
    """The twin of the refusals: the guard passes what is bound to its own image."""
    m0 = rr.load_manifest(rr.IMAGES["r0"]["manifest"])
    for key in m0["commands"]:
        assert rr.reference_problems(R0_REFS, key, m0) == [], key


def test_r0_bytes_under_r1_labels_are_still_refused(tmp_path):
    """The defeat of a label check: every label says R1, the bytes are R0's."""
    m1 = rr.load_manifest(rr.IMAGES["r1"]["manifest"])
    d = relabel(R0_REFS, tmp_path / "labelled", m1)
    why = rr.reference_problems(d, "held-default", m1)
    assert why
    joined = "; ".join(why)
    assert "bytes differ" in joined and "sources" in joined, why
    assert "bitstream" not in joined          # the labels were fixed; the content was not


def test_r1_labelled_references_are_refused_by_the_r0_procedure(tmp_path):
    m0 = rr.load_manifest(rr.IMAGES["r0"]["manifest"])
    m1 = rr.load_manifest(rr.IMAGES["r1"]["manifest"])
    d = relabel(R0_REFS, tmp_path / "labelled", m1)
    why = rr.reference_problems(d, "held-default", m0)
    assert why and any("schema" in w or "image" in w for w in why), why


def test_the_silence_reference_is_bound_to_its_image_too(tmp_path):
    """silence.json is declared, not rendered, and was never checked at all:
    an R1 procedure given R0's silence must still say whose it is."""
    m0 = rr.load_manifest(rr.IMAGES["r0"]["manifest"])
    m1 = rr.load_manifest(rr.IMAGES["r1"]["manifest"])
    assert rr.reference_problems(R0_REFS, "silence", m0) == []
    assert rr.reference_problems(R0_REFS, "silence", m1)
    d = relabel(R0_REFS, tmp_path / "labelled", m1)
    assert rr.reference_problems(d, "silence", m1) == []       # labelled R1: nothing to compare
    assert rr.reference_problems(d, "silence", m0)             # but not the R0 procedure's


def test_a_missing_r1_reference_set_is_a_named_problem_not_a_pass(tmp_path):
    m1 = rr.load_manifest(rr.IMAGES["r1"]["manifest"])
    why = rr.reference_problems(tmp_path / "none", "held-default", m1)
    assert why and "no reference" in why[0]


# ---- the transcript and the session ------------------------------------------
R1_TRANSCRIPT = (
    "r1_release: BOUND -- fpga/release/r1-2025.1.json equals a fresh derivation\n"
    "exit 0\n"
    f"{R1_BITSTREAM}  fpga/reports/arty/r1-player-preview-2025.1/arty.bit\n"
    "openFPGALoader v1.1.1\nLoad SRAM: [====] 100.00%\nDone\nexit 0\n")


def test_the_r1_transcript_is_accepted_only_by_the_r1_procedure():
    m0 = rr.load_manifest(rr.IMAGES["r0"]["manifest"])
    m1 = rr.load_manifest(rr.IMAGES["r1"]["manifest"])
    assert rc.transcript_problems(R1_TRANSCRIPT, R1_BITSTREAM, manifest=m1) == []
    why = rc.transcript_problems(R1_TRANSCRIPT, R0_BITSTREAM, manifest=m0)
    assert why
    from test_r0_capture import GOOD_TRANSCRIPT
    assert rc.transcript_problems(GOOD_TRANSCRIPT, R0_BITSTREAM, manifest=m0) == []
    r0_under_r1 = rc.transcript_problems(GOOD_TRANSCRIPT, R1_BITSTREAM, manifest=m1)
    assert any("r1_release: BOUND" in w for w in r0_under_r1), r0_under_r1
    assert any("not R1's" in w for w in r0_under_r1), r0_under_r1


def test_an_r1_manifest_check_line_with_the_r0_digest_is_refused():
    """R1's check line, R0's image: the wrong digest alone must refuse."""
    m1 = rr.load_manifest(rr.IMAGES["r1"]["manifest"])
    t = R1_TRANSCRIPT.replace(R1_BITSTREAM, R0_BITSTREAM)
    why = rc.transcript_problems(t, R1_BITSTREAM, manifest=m1)
    assert any("not R1's" in w for w in why), why


def test_new_session_for_r1_is_filled_from_r1s_manifest(tmp_path):
    p = rc.new_session(tmp_path / "b", image="r1")
    s = json.loads(p.read_text())
    assert s["schema"] == rc.SESSION_SCHEMAS["r1"] != rc.SESSION_SCHEMAS["r0"]
    assert s["image_id"] == "r1" and s["image"]["bitstream_sha256"] == R1_BITSTREAM
    assert "arty.bit" in s["image"]["bitstream"] and "r1-player-preview" in s["image"]["bitstream"]
    cmds = {t["command_id"]: t["command"] for t in s["takes"]}
    assert "run-m5a" in cmds and "phrase-m5a" not in cmds
    assert all(c is None or c.endswith("--image r1") for c in cmds.values()), cmds
    assert cmds["silence"] is None


def test_new_session_default_stays_r0(tmp_path):
    s = json.loads(rc.new_session(tmp_path / "b").read_text())
    assert s["image"]["bitstream_sha256"] == R0_BITSTREAM
    assert s.get("image_id", "r0") == "r0"


def _small_session(tmp_path, name, image, refdir=None, manifest_path=None):
    return rc.synth_session(tmp_path / name, image=image, refdir=refdir,
                            manifest_path=manifest_path, takes=SMALL_TAKES)


def test_an_r0_session_presented_to_the_r1_procedure_is_refused(tmp_path):
    d = _small_session(tmp_path, "r0s", "r0")
    rec = rc.analyse(d, R0_REFS, image="r1", allow_synthetic=True)
    assert rec["verdict"] == rc.REFUSED
    assert "R1" in rec["reasons"][0] and "capture metadata" in rec["reasons"][0]
    assert rec["image_id"] == "r1"


def test_an_r1_session_presented_to_the_r0_procedure_is_refused(tmp_path):
    d = _small_session(tmp_path, "r1s", "r1", refdir=R0_REFS)
    rec = rc.analyse(d, R0_REFS, image="r0", allow_synthetic=True)
    assert rec["verdict"] == rc.REFUSED
    assert "capture metadata" in rec["reasons"][0]
    assert rec["image_id"] == "r0"


def test_r0_references_under_the_r1_procedure_are_refused_not_failed(tmp_path):
    """A session that is R1's in every field, scored against R0's references:
    REFUSED naming the reference. Not PASS, and not FAIL (a measured defect)."""
    d = _small_session(tmp_path, "r1s", "r1", refdir=R0_REFS)
    rec = rc.analyse(d, R0_REFS, image="r1", allow_synthetic=True)
    assert rec["verdict"] == rc.REFUSED, rec["reasons"]
    assert "the reference is not the release's" in rec["reasons"][0]
    assert "bitstream" in rec["reasons"][0]
    assert "properties" not in rec or not rec["properties"]    # nothing was measured


def test_r1_labelled_r0_bytes_under_the_r1_procedure_are_refused(tmp_path):
    m1 = rr.load_manifest(rr.IMAGES["r1"]["manifest"])
    labelled = relabel(R0_REFS, tmp_path / "labelled", m1)
    d = _small_session(tmp_path, "r1s", "r1", refdir=R0_REFS)
    rec = rc.analyse(d, labelled, image="r1", allow_synthetic=True)
    assert rec["verdict"] == rc.REFUSED, rec["reasons"]
    assert "bytes differ" in rec["reasons"][0]


def test_image_argument_must_agree_with_the_manifest(tmp_path):
    d = _small_session(tmp_path, "r0s", "r0")
    rec = rc.analyse(d, R0_REFS, rr.IMAGES["r0"]["manifest"], image="r1", allow_synthetic=True)
    assert rec["verdict"] == rc.REFUSED and "image" in rec["reasons"][0]


# ---- the controls: refusal is itself a tested outcome -------------------------
@pytest.mark.parametrize("image", ["r0", "r1"])
def test_cross_image_controls_are_caught_for_each_procedure(tmp_path, image):
    res = rc.cross_image_controls(tmp_path, image=image)
    assert res["image"] == image
    assert res["all_caught"], json.dumps(res["cases"], indent=1)
    names = set(res["cases"])
    assert {"other-references", "other-labels-same-bytes", "other-session"} <= names
    for name, c in res["cases"].items():
        assert c["verdict"] == rc.REFUSED, (name, c)
        assert c["caught"] and c["outcome"] == "REFUSED as declared", (name, c)


def test_the_cross_image_control_can_fail_when_the_guard_is_weakened(tmp_path, monkeypatch):
    """Start red: with the reference binding removed the same inputs must NOT
    be caught. A control that passes against a guard that is not there is the
    failure this repository keeps producing."""
    monkeypatch.setattr(rr, "reference_problems", lambda *a, **k: [])
    res = rc.cross_image_controls(tmp_path, image="r1")
    assert not res["all_caught"]
    assert not res["cases"]["other-references"]["caught"]


def test_the_cross_image_control_is_not_satisfied_by_any_refusal(tmp_path, monkeypatch):
    """A refusal for the WRONG reason (an unreadable reference) is not a catch."""
    real = rr.reference_problems
    monkeypatch.setattr(rr, "reference_problems",
                        lambda *a, **k: ["held-default: wav missing or altered"])
    res = rc.cross_image_controls(tmp_path, image="r1")
    assert not res["cases"]["other-references"]["caught"]
    monkeypatch.setattr(rr, "reference_problems", real)


def test_cross_image_controls_cli_writes_a_record_and_exit_code(tmp_path):
    code = rc.main(["cross-image-controls", "--image", "r1", "--out", str(tmp_path)])
    rec = json.loads((tmp_path / "cross-image.json").read_text())
    assert code == 0 and rec["all_caught"] and rec["image"] == "r1"


# ---- the analysis accepts what IS bound to its image --------------------------
def _r1_shaped_world(tmp_path):
    """A manifest and reference set that are R1's by every label and every
    pinned value, but built from R0's measured material (a test-only world:
    the real R1 references need the image's RTL replayed on the build box)."""
    m0 = rr.load_manifest(rr.IMAGES["r0"]["manifest"])
    raw = json.loads(rr.IMAGES["r1"]["manifest"].read_text())
    raw["image"].update({k: m0["image"][k] for k in ("bitstream_sha256", "routed_dcp_sha256",
                                                     "source_commit", "source_sha256")})
    for k, spec in raw["host"]["commands"].items():
        src = "phrase-m5a" if k == "run-m5a" else k
        if "cmds_sha256" in spec:
            spec["cmds_sha256"] = m0["commands"][src]["cmds_sha256"]
    mp = tmp_path / "r1-shaped.json"
    mp.write_text(json.dumps(raw))
    m = rr.load_manifest(mp)
    refs = tmp_path / "refs"
    relabel(R0_REFS, refs, m)
    for ext in (".json", ".plan.json", ".wav"):                   # R0's phrase-m5a -> R1's run-m5a
        shutil.move(refs / f"phrase-m5a{ext}", refs / f"run-m5a{ext}")
    p = refs / "run-m5a.json"
    rec = json.loads(p.read_text())
    rec["command_id"], rec["wav"] = "run-m5a", "run-m5a.wav"
    rec["command"] = m["commands"]["run-m5a"]["command"]
    p.write_text(json.dumps(rec, indent=1, sort_keys=True) + "\n")
    for k in m["commands"]:
        assert rr.reference_problems(refs, k, m) == [], k
    return mp, refs, m


def test_a_session_bound_to_its_own_image_passes_the_r1_procedure(tmp_path):
    """The positive twin of every refusal above, on the full diagnostic set:
    the same procedure, given material bound to R1's manifest, PASSes with the
    injected delay, gain and clock recovered. Without this, REFUSED could be
    the only thing the R1 path ever says."""
    mp, refs, m = _r1_shaped_world(tmp_path)
    d = rc.synth_session(tmp_path / "s", image="r1", refdir=refs, manifest_path=mp)
    rec = rc.analyse(d, refs, mp, image="r1", allow_synthetic=True)
    assert rec["verdict"] == rc.PASS, rec["reasons"]
    assert rec["image_id"] == "r1"
    assert abs(rec["calibration"]["ppm"] - rc.CLEAN["ppm"]) < 2.0
    assert {t["command_id"] for t in rec["takes"]} >= {"silence", "demo", "run-m5a"}


# ---- command-line entry points ------------------------------------------------
def test_cli_analyse_r1_without_a_bundle_is_operator_blocked(tmp_path, capsys):
    code = rc.main(["analyse", "--image", "r1", "--bundle", str(tmp_path / "none"),
                    "--out", str(tmp_path / "out")])
    rec = json.loads((tmp_path / "out" / "analysis.json").read_text())
    assert code == 2 and rec["verdict"] == rc.REFUSED and rec["image_id"] == "r1"
    assert rec.get("operator_blocked") and "capture-r1" in rec["reasons"][0]


def test_cli_the_r1_bundle_variable_is_not_the_r0_one(tmp_path, monkeypatch):
    """An R0 bundle exported for R0 must not become the R1 bundle by default."""
    monkeypatch.setenv("R0_CAPTURE_BUNDLE", str(tmp_path / "an-r0-bundle"))
    monkeypatch.delenv("R1_CAPTURE_BUNDLE", raising=False)
    assert rc.default_bundle("r1") == rc.ROOT / "captures" / "r1"
    assert rc.default_bundle("r0") == tmp_path / "an-r0-bundle"
    monkeypatch.setenv("R1_CAPTURE_BUNDLE", str(tmp_path / "x"))
    assert rc.default_bundle("r1") == tmp_path / "x"


def test_cli_an_r0_bundle_under_image_r1_is_refused_exit_2(tmp_path):
    d = _small_session(tmp_path, "r0s", "r0")
    code = rc.main(["analyse", "--image", "r1", "--bundle", str(d), "--out", str(tmp_path / "o")])
    rec = json.loads((tmp_path / "o" / "analysis.json").read_text())
    assert code == 2 and rec["verdict"] == rc.REFUSED


# ---- T-PHYSICAL: the R1 variant -----------------------------------------------
def _spec(trial_mod, mode):
    reg = trial_mod.load_registry()
    return reg["trials"]["T-PHYSICAL"]["modes"][mode]


def test_t_physical_registers_an_r1_capture_mode_bound_to_r1():
    import trial
    r0 = _spec(trial, "capture")
    r1 = _spec(trial, "capture-r1")
    assert "fpga/release/r1-2025.1.json" in r1["requires"]
    assert "fpga/release/r1-2025.1.json" not in r0["requires"]
    child = r1["required"][0]
    assert child["args"][:3] == ["analyse", "--image", "r1"]
    assert child["image_id"] == "r1" and r0["required"][0].get("image_id", "r0") == "r0"
    assert "captures/r1" not in json.dumps(r0) and "R1_CAPTURE_BUNDLE" not in json.dumps(r0)
    ctl = {c["id"] for c in r1["controls"]}
    assert "cross-image-refusal" in ctl
    assert "cross-image-refusal" in {c["id"] for c in r0["controls"]}


def _analysis(tmp_path, **kw):
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    rec = {"verdict": "PASS", "reasons": [], "takes": [], "properties": {}, **kw}
    (out / "analysis.json").write_text(json.dumps(rec))
    return out


def test_the_trial_interpreter_refuses_a_record_of_the_other_image(tmp_path):
    """The trial reads the checker's record; a record that names the other
    image (or none) must not become this mode's verdict."""
    import trial
    from trial import interpret_physical_capture_record as interp
    run = {"rc": 0}
    out = _analysis(tmp_path, image_id="r0")
    res = interp({"fixtures": [], "image_id": "r1"}, run, out, "required")
    assert res["verdict"] == trial.NO_VERDICT and "image" in res["reasons"][0]
    out = _analysis(tmp_path)                                  # no image_id at all
    res = interp({"fixtures": [], "image_id": "r1"}, run, out, "required")
    assert res["verdict"] == trial.NO_VERDICT
    out = _analysis(tmp_path, image_id="r1", takes=[{"command_id": "demo"}] * 2)
    res = interp({"fixtures": ["demo"], "image_id": "r1"}, run, out, "required")
    assert res["verdict"] == trial.PASS, res["reasons"]


def _required_only(tmp_path, monkeypatch, mode, env, bundle):
    import trial
    from test_r0_capture import _env_spec
    reg = json.loads((rc.ROOT / "docs/trials.json").read_text())
    reg["trials"]["T-PHYSICAL"]["modes"][mode]["controls"] = []
    rp = tmp_path / "trials-required-only.json"
    rp.write_text(json.dumps(reg))
    monkeypatch.setenv(env, str(bundle))
    _, rec = trial.run_trial("T-PHYSICAL", mode=mode, registry=rp, env_spec=_env_spec(tmp_path),
                             out_base=tmp_path / "trials")
    return rec["children"][0]


def test_t_physical_r1_without_a_capture_is_operator_blocked(tmp_path, monkeypatch):
    import trial
    child = _required_only(tmp_path, monkeypatch, "capture-r1", "R1_CAPTURE_BUNDLE",
                           tmp_path / "no-bundle")
    assert child["verdict"] == trial.NO_VERDICT
    assert "operator-blocked" in child["reasons"][0]


def test_t_physical_r1_given_an_r0_bundle_is_no_verdict(tmp_path, monkeypatch):
    import trial
    d = _small_session(tmp_path, "r0s", "r0")
    child = _required_only(tmp_path, monkeypatch, "capture-r1", "R1_CAPTURE_BUNDLE", d)
    assert child["verdict"] == trial.NO_VERDICT
    assert "R1" in child["reasons"][0]


def test_t_physical_r0_given_an_r1_bundle_is_no_verdict(tmp_path, monkeypatch):
    import trial
    d = _small_session(tmp_path, "r1s", "r1", refdir=R0_REFS)
    child = _required_only(tmp_path, monkeypatch, "capture", "R0_CAPTURE_BUNDLE", d)
    assert child["verdict"] == trial.NO_VERDICT


def test_t_physical_cross_image_control_is_caught_through_the_trial(tmp_path):
    """The registered control, end to end through tools/trial.py, for the R1 mode
    (the required child is stripped: its own entry points are tested above)."""
    import trial
    from test_r0_capture import _env_spec
    reg = json.loads((rc.ROOT / "docs/trials.json").read_text())
    m = reg["trials"]["T-PHYSICAL"]["modes"]["capture-r1"]
    m["controls"] = [c for c in m["controls"] if c["id"] == "cross-image-refusal"]
    rp = tmp_path / "t.json"
    rp.write_text(json.dumps(reg))
    run_dir, rec = trial.run_trial("T-PHYSICAL", mode="capture-r1", registry=rp,
                                   env_spec=_env_spec(tmp_path), out_base=tmp_path / "trials")
    assert rec["execution"]["status"] == "complete", rec["execution"]
    assert rec["controls"][0]["caught"] is True, rec["controls"][0]["reasons"]
    assert rec["verdict"] == trial.NO_VERDICT          # never PASS before a capture exists

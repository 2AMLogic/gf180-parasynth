"""The release manifest is bound to the tree, and each way the selected
artifacts can disagree turns it REFUSED or STALE for its own reason."""
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
for _p in (HERE, HERE.parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import release_manifest as rm            # noqa: E402


def _sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def fresh():
    return json.loads(json.dumps(rm.build()))


def test_committed_manifest_is_bound_to_this_tree():
    verdict, detail = rm.check()
    assert verdict == "BOUND", detail


def test_selected_artifacts_agree(fresh):
    img = fresh["image"]
    assert img["configuration"] == {"OSC2X": 1, "FILTER2X": 1, "PULSE2X": 0}
    assert img["bitstream_sha256"].startswith("a66c9349")
    assert img["routed_dcp_sha256"].startswith("6c3c22c5")
    assert img["source_commit_verified"] is True
    assert img["dsp_feedback_review"]["complete"] is True
    for fx, b in fresh["evidence"]["rolling_playback"].items():
        assert b["cli_bytes_equal_recorded_replay"] and b["replay_rtl_sources_match_image"], fx
    for p, h in fresh["evidence"]["held_note"].items():
        assert h["verdict"] == "PASS" and h["i2s_peak_lsb"] >= 1024, p
        assert h["replay_capture_sha256"] == fresh["commands"][h["command"]]["cmds_sha256"]
    # the glide bench's control is the injected glide-floor bug (a wrapper-level
    # #247 control was tried and NOT caught without the drum route; it is
    # recorded as the probe `wrapper:probe-247-incs`). #247 itself is
    # reproduced by probe_247's `orig` variant (exit 1).
    v = fresh["evidence"]["glide_boundary"]["verdicts"]
    assert "wrapper:control-247" not in v
    assert v["voice:accept-default+GLIDE_FLOOR"] == {"rc": 0, "expected": "CAUGHT", "met": True}
    for k in ("voice:accept-default", "wrapper:accept-default", "wrapper:accept-pulse29",
              "wrapper:accept-247-shape"):
        assert v[k] == {"rc": 0, "expected": "PASS", "met": True}, k
    assert fresh["evidence"]["probe_247"]["rc"]["orig"] == 1


def test_every_supported_command_passes_the_validator(fresh):
    for name, c in fresh["commands"].items():
        assert c["validator"] and "release domain OK" in c["validator"], name


def test_exclusions_and_runtime_evidence_are_explicit(fresh):
    whats = " ".join(e["what"] for e in fresh["exclusions"])
    for needle in ("PULSE2X=1", "#247 as filed", "#247 as measured", "surge-type2-clean-v1"):
        assert needle in whats
    dl = fresh["runtime_qualification"]["deadline"]
    assert dl["status"] == "LANDED" and "not a formal proof" in dl["does_not_establish"]
    assert fresh["evidence"]["probe_247"]["rc"] == {
        "orig": 1, "no-drumfilter": 0, "no-strikes": 0, "jumps": 1, "inrange": 1, "inrange-route0": 0}
    assert fresh["physical_capture"]["status"] == "NONE"


# ---- controls: each disagreement refuses for its own reason ----------------------------
def test_edited_manifest_is_stale(tmp_path):
    m = json.loads(rm.MANIFEST.read_text())
    m["image"]["bitstream_sha256"] = "0" * 64
    p = tmp_path / "m.json"
    p.write_text(json.dumps(m))
    verdict, detail = rm.check(p)
    assert verdict == "STALE" and "image.bitstream_sha256" in detail


def _copy_pub(tmp_path):
    d = tmp_path / "pub"
    shutil.copytree(rm.PUB_DIR, d)
    return d


def test_bitstream_that_is_not_the_published_one_refuses(tmp_path, monkeypatch):
    d = _copy_pub(tmp_path)
    b = bytearray((d / "arty.bit").read_bytes())
    b[-1] ^= 1
    (d / "arty.bit").write_bytes(bytes(b))
    monkeypatch.setattr(rm, "PUB_DIR", d)
    with pytest.raises(rm.Refused, match="arty.bit hashes to"):
        rm.image_identity()


def test_dsp_evidence_bound_to_another_checkpoint_refuses(tmp_path, monkeypatch):
    d = _copy_pub(tmp_path)
    pub = json.loads((d / "publication.json").read_text())
    pub["dsp_disposition"]["routed_dcp_sha256"] = "f" * 64
    (d / "publication.json").write_text(json.dumps(pub))
    monkeypatch.setattr(rm, "PUB_DIR", d)
    with pytest.raises(rm.Refused, match="routed.dcp digest disagrees"):
        rm.image_identity()


def test_image_source_absent_from_the_source_commit_refuses(tmp_path, monkeypatch):
    d = _copy_pub(tmp_path)
    pub = json.loads((d / "publication.json").read_text())
    rep = json.loads((d / "report.json").read_text())
    for r in (pub, rep):
        r["source_sha256"]["rtl-sketch/voice_dp.v"] = "e" * 64
    (d / "report.json").write_text(json.dumps(rep))
    pub["published_sha256"]["report.json"] = _sha(d / "report.json")
    (d / "publication.json").write_text(json.dumps(pub))
    monkeypatch.setattr(rm, "PUB_DIR", d)
    verdict, detail = rm.check()
    assert verdict == "REFUSED" and "voice_dp.v" in detail and "source commit" in detail


# ---- population: the sets are compared with an independent expected list -----
# (docs/verification-rules.md rule 8: each fixture must fail on the population
# message, not on a hash mismatch, so a hash check cannot satisfy it.)
_POP = {
    "published_sha256": "published files",
    "published_evidence_sha256": "DSP evidence files",
    "source_sha256": "image sources",
}


def _write_pub(d, pub, rep=None):
    if rep is not None:
        (d / "report.json").write_text(json.dumps(rep))
        pub["published_sha256"]["report.json"] = _sha(d / "report.json")
    (d / "publication.json").write_text(json.dumps(pub))


@pytest.mark.parametrize("key", sorted(_POP))
def test_emptied_set_refuses_on_population(tmp_path, monkeypatch, key):
    d = _copy_pub(tmp_path)
    pub = json.loads((d / "publication.json").read_text())
    rep = json.loads((d / "report.json").read_text())
    pub[key] = {}
    if key == "source_sha256":
        rep[key] = {}  # both emptied: the report/publication equality still holds
        _write_pub(d, pub, rep)
    else:
        _write_pub(d, pub)
    monkeypatch.setattr(rm, "PUB_DIR", d)
    with pytest.raises(rm.Refused, match=rf"{_POP[key]}.*missing"):
        rm.image_identity()


@pytest.mark.parametrize("key,victim", [
    ("published_sha256", "timing.rpt"),
    ("published_evidence_sha256", "vivado_extract.log"),
    ("source_sha256", "rtl-sketch/voice_dp.v"),
])
def test_entry_removed_with_its_file_refuses_on_population(tmp_path, monkeypatch, key, victim):
    d = _copy_pub(tmp_path)
    pub = json.loads((d / "publication.json").read_text())
    rep = json.loads((d / "report.json").read_text())
    del pub[key][victim]
    if key == "source_sha256":
        del rep[key][victim]
        _write_pub(d, pub, rep)
    else:
        base = d / "dsp-dpreg-evidence" if key == "published_evidence_sha256" else d
        (base / victim).unlink()
        _write_pub(d, pub)
    monkeypatch.setattr(rm, "PUB_DIR", d)
    with pytest.raises(rm.Refused, match=rf"{_POP[key]}.*missing.*{victim}"):
        rm.image_identity()


@pytest.mark.parametrize("key,extra", [
    ("published_sha256", "extra.rpt"),
    ("published_evidence_sha256", "extra.log"),
    ("source_sha256", "rtl-sketch/extra.v"),
])
def test_extra_entry_refuses_on_population(tmp_path, monkeypatch, key, extra):
    d = _copy_pub(tmp_path)
    pub = json.loads((d / "publication.json").read_text())
    rep = json.loads((d / "report.json").read_text())
    if key == "source_sha256":
        pub[key][extra] = rep[key][extra] = "a" * 64
        _write_pub(d, pub, rep)
    else:
        base = d / "dsp-dpreg-evidence" if key == "published_evidence_sha256" else d
        (base / extra).write_text("x")
        pub[key][extra] = _sha(base / extra)
        _write_pub(d, pub)
    monkeypatch.setattr(rm, "PUB_DIR", d)
    with pytest.raises(rm.Refused, match=rf"{_POP[key]}.*extra.*{extra}"):
        rm.image_identity()


def test_working_tree_moving_past_the_image_is_reported_not_bound():
    """#252 changed voice_dp.v on main after the image was built: the release
    still names the image's own sources (at IMAGE_SOURCE_COMMIT) and says the
    tree has moved, instead of silently re-binding to RTL the bitstream lacks."""
    moved = rm.tree_drift()
    verdict, detail = rm.check()
    assert verdict == "BOUND"
    assert ("moved past this image" in detail) == bool(moved)


def test_recorded_replay_that_is_not_the_clis_bytes_refuses(tmp_path, monkeypatch):
    d = tmp_path / "rolling"
    shutil.copytree(rm.ROLLING_DIR, d)
    p = d / "demo" / "rtl-replay" / "demo.cmds"
    lines = p.read_text().splitlines()
    lines[5] = lines[5][:-1] + ("0" if lines[5][-1] != "0" else "1")
    p.write_text("\n".join(lines) + "\n")
    monkeypatch.setattr(rm, "ROLLING_DIR", d)
    assert rm.rolling_binding("demo")["cli_bytes_equal_recorded_replay"] is False


def test_held_note_evidence_of_other_bytes_refuses(tmp_path, monkeypatch):
    d = tmp_path / "evidence"
    shutil.copytree(rm.EVIDENCE_DIR, d)
    cap = d / "held-note" / "default" / "capture.cmds"
    # the pre-fix (silent) bytes' replay is not evidence for the shipped command
    shutil.copy(rm.EVIDENCE_DIR / "held-note" / "default-legacy" / "capture.cmds", cap)
    monkeypatch.setattr(rm, "EVIDENCE_DIR", d)
    verdict, detail = rm.check()
    assert verdict == "REFUSED" and "not what the CLI emits now" in detail


def test_control_a_key_dropped_from_the_derivation_is_stale_not_bound(monkeypatch):
    """The absent-vs-explicit-null hole (#785), through the gate that issues the
    verdict rather than through its comparison helper.

    `presets.default.calibration` ships as an explicit `null` in
    baseline-2025.1.json, so a derivation that stops emitting the key AT ALL
    must read STALE: the committed record no longer equals `build()`, and the
    pre-#785 comparator's `a.get(k)` / `b.get(k)` made absent and `null`
    indistinguishable, so this gate reported BOUND on a record that no longer
    matched its derivation -- docs/verification-rules.md rule 8 exactly.

    The three conditions of rule 5 are asserted, not assumed: the clean run is
    BOUND before the mutant means anything; the substitution is shown to be a
    real change against the committed record (a committed explicit null, a key
    the derivation really does emit); and the verdict must name EXACTLY the
    dropped path, so a STALE arising for some other reason does not count.
    """
    committed = json.loads(rm.MANIFEST.read_text())
    assert committed["presets"]["default"]["calibration"] is None, \
        "the control needs a committed explicit null; it no longer has one"
    assert rm.check()[0] == "BOUND", "clean run must pass before the mutant proves anything"

    emit = rm.presets

    def presets_without_calibration():
        out = emit()
        assert "calibration" in out["default"], "the mutant did not activate"
        del out["default"]["calibration"]
        return out

    monkeypatch.setattr(rm, "presets", presets_without_calibration)
    verdict, detail = rm.check()
    assert verdict == "STALE", \
        f"a derivation that stopped emitting presets.default.calibration read {verdict}: {detail}"
    assert "presets.default.calibration" in detail, detail


# ---- a record that cannot be read is REFUSED, not a traceback (#1620) -----------
# The shape #1618's scan found in this file 45 times: a loaded record read with
# [] and no refusal for its absence, so a partial record died with `KeyError`,
# exit 1 -- the code of "the comparison ran and disagreed". Each guard below is
# proven through the tool's own main(), one control per key, each naming its key
# and the record it came from (the #1604 pattern). The records are the committed
# ones with exactly one key removed, never hand-written, and each directory is
# substituted as a COPY: a test must not write under fpga/reports or evidence/.
_PUB_KEYS = ("configuration", "published_sha256", "published_evidence_sha256", "source_sha256",
             "bitstream_sha256", "original_artifact_sha256", "dsp_disposition", "verification",
             "state", "part", "tool", "timing", "core_period_ns", "internal_timing_pass",
             "external_io_timing_qualified", "output_delay_exceptions", "remaining_review")
# (kind, module directory, the record under it, the keys read with [], how the
#  refusal names the record): one row per json.loads the scan hit
_RECORDS = [
    ("publication", "PUB_DIR", "publication.json", _PUB_KEYS, "the publication at"),
    ("report", "PUB_DIR", "report.json", ("artifact_sha256", "source_sha256"), "the report at"),
    ("rollback-publication", "ROLLBACK_DIR", "publication.json",
     ("bitstream_sha256", "configuration", "state"), "the rollback publication at"),
    ("rolling-record", "ROLLING_DIR", "demo/verification.json", ("state",),
     "the rolling-playback record at"),
    ("rolling-identity", "ROLLING_DIR", "demo/rtl-replay/demo.run_identity.json", ("identity",),
     "the replay run identity at"),
    ("held-identity", "EVIDENCE_DIR", "held-note/default/run_identity.json", ("identity",),
     "the held-note run identity at"),
    ("held-record", "EVIDENCE_DIR", "held-note/default/held_note_audible.json",
     ("verdict", "i2s_peak_lsb"), "the held-note record at"),
    ("glide-summary", "EVIDENCE_DIR", "glide-boundary/summary.json",
     ("wrapper_identities", "all_expectations_met", "verdicts"), "the glide-boundary summary at"),
]
_DROPS = [(kind, attr, rel, what, key) for kind, attr, rel, keys, what in _RECORDS for key in keys]


def _substitute(monkeypatch, tmp_path, attr):
    d = tmp_path / "sub"
    shutil.copytree(getattr(rm, attr), d)
    monkeypatch.setattr(rm, attr, d)
    return d


def _rewrite(path, fn):
    path.write_text(json.dumps(fn(json.loads(path.read_text()))))


def _main(capsys):
    code = rm.main([])
    return code, capsys.readouterr().out


def test_the_committed_records_carry_the_keys_the_manifest_reads(capsys):
    # the guards below must be satisfiable against the records that ship
    for kind, attr, rel, keys, _what in _RECORDS:
        assert set(keys) <= set(json.loads((getattr(rm, attr) / rel).read_text())), kind
    assert _main(capsys)[0] == 0


@pytest.mark.parametrize("attr", ["PUB_DIR", "ROLLBACK_DIR", "ROLLING_DIR", "EVIDENCE_DIR"])
def test_an_intact_substituted_copy_is_compared_not_refused(tmp_path, monkeypatch, capsys, attr):
    # every control below damages a COPY; the copy alone must refuse nothing
    # (rule 8). With every key present the tree is derived in full and
    # compared: STALE on the copy's absolute paths, which is a verdict.
    _substitute(monkeypatch, tmp_path, attr)
    code, out = _main(capsys)
    assert code == 1 and "STALE" in out and "REFUSED" not in out, out


@pytest.mark.parametrize("kind,attr,rel,what,key", _DROPS,
                         ids=[f"{k}-without-{key}" for k, _a, _r, _w, key in _DROPS])
def test_a_record_missing_one_key_is_refused_naming_it(kind, attr, rel, what, key, tmp_path,
                                                        monkeypatch, capsys):
    # before the repair: `KeyError: '<key>'`, a traceback, exit 1
    d = _substitute(monkeypatch, tmp_path, attr)
    _rewrite(d / rel, lambda rec: {k: v for k, v in rec.items() if k != key})
    code, out = _main(capsys)
    assert code == 2 and "REFUSED" in out and what in out and f"'{key}'" in out, out


@pytest.mark.parametrize("kind,attr,rel,keys,what", _RECORDS, ids=[r[0] for r in _RECORDS])
def test_an_empty_record_is_refused_naming_every_key(kind, attr, rel, keys, what, tmp_path,
                                                     monkeypatch, capsys):
    # #1620's own input, `publication.json` and `report.json` both `{}`: every
    # key is named, so a record is not regenerated once per key
    d = _substitute(monkeypatch, tmp_path, attr)
    _rewrite(d / rel, lambda rec: {})
    code, out = _main(capsys)
    assert code == 2 and "REFUSED" in out and what in out, out
    assert all(f"'{k}'" in out for k in keys), out


@pytest.mark.parametrize("kind,attr,rel,keys,what", _RECORDS, ids=[r[0] for r in _RECORDS])
def test_a_record_carrying_null_for_a_key_is_refused_naming_it(kind, attr, rel, keys, what,
                                                                tmp_path, monkeypatch, capsys):
    # a present-but-null key reads as a VALUE before the repair: `None` is
    # compared, dereferenced or reported where the record should be refused
    d = _substitute(monkeypatch, tmp_path, attr)
    _rewrite(d / rel, lambda rec: {**rec, keys[0]: None})
    code, out = _main(capsys)
    assert code == 2 and "REFUSED" in out and what in out and f"'{keys[0]}'" in out, out


@pytest.mark.parametrize("record", [[], None], ids=["list", "null"])
@pytest.mark.parametrize("kind,attr,rel,keys,what", _RECORDS, ids=[r[0] for r in _RECORDS])
def test_a_record_that_is_not_an_object_is_refused(kind, attr, rel, keys, what, record,
                                                   tmp_path, monkeypatch, capsys):
    d = _substitute(monkeypatch, tmp_path, attr)
    (d / rel).write_text(json.dumps(record))
    code, out = _main(capsys)
    assert code == 2 and "REFUSED" in out and what in out and "not a JSON object" in out, out


def test_publication_rewritten_after_the_derivation_is_refused(tmp_path, monkeypatch, capsys):
    """tree_drift() re-reads publication.json AFTER build() has bound it, and
    check() calls it outside its handler. A publication rewritten between the
    two reads (a concurrent publish) was a KeyError out of check() itself:
    the read carries its own guard, and check() reports that refusal."""
    d = _substitute(monkeypatch, tmp_path, "PUB_DIR")
    m = tmp_path / "m.json"
    m.write_text(json.dumps(rm.build()))        # the copy's own manifest: the BOUND path
    build = rm.build

    def build_then_rewrite():
        out = build()
        _rewrite(d / "publication.json",
                 lambda rec: {k: v for k, v in rec.items() if k != "source_sha256"})
        return out
    monkeypatch.setattr(rm, "build", build_then_rewrite)
    code = rm.main(["--manifest", str(m)])
    out = capsys.readouterr().out
    assert code == 2 and "REFUSED" in out and "the publication at" in out \
        and "'source_sha256'" in out, out

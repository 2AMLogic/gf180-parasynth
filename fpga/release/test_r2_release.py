"""The R2 release manifest (fpga/release/r2_release.py): BOUND to the built
image, with R1 BOUND as its rollback; every refusal is for its reason."""
import contextlib
import io
import json
import shutil
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import r2_release as r2r                                   # noqa: E402


def test_the_committed_r2_manifest_is_bound():
    verdict, detail = r2r.check()
    assert verdict == "BOUND", detail


def test_r2_manifest_names_its_configuration_override_and_rollback():
    m = json.loads(r2r.MANIFEST.read_text())
    assert m["configuration"] == {"OSC2X": 1, "FILTER2X": 1, "PULSE2X": 1}
    assert m["image"]["image"] == "r2"
    assert "OVERRIDE" in m["status"] and "NOT ACCEPTED" in m["operator_override"]["rule_verdict"]
    assert len(m["operator_override"]["known_limitations"]) == 6
    assert m["rollback"]["name"] == "R1"
    assert sorted(m["image"]["differs_from_r1_freeze"]) == [
        "fpga/boards/arty-a7-100.xdc", "rtl-sketch/ladder_dp_n.v",
        "rtl-sketch/polyblep_saw_pair.v", "rtl-sketch/voice_dp.v"]
    assert all(c.get("equals_r1_bytes") for n, c in m["host"]["commands"].items()
               if n != "live-midi")


@pytest.mark.parametrize("case", ["image", "host"])
def test_stale_control_is_caught_at_exactly_its_fields(tmp_path, case):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = r2r.stale_control(case, tmp_path)
    assert rc == 1, out.getvalue()
    assert "stale_control: STALE" in out.getvalue()


def test_an_unbound_rollback_refuses(monkeypatch):
    monkeypatch.setattr(r2r.r1r, "check", lambda *a, **k: ("STALE", "moved"))
    verdict, detail = r2r.check()
    assert verdict == "REFUSED" and "rollback R1 is not BOUND" in detail, detail


def test_a_run_without_the_pulse2x_define_is_not_r2_evidence(tmp_path, monkeypatch):
    src = r2r.EVIDENCE_DIR
    dst = tmp_path / "r2"
    shutil.copytree(src, dst)
    ident = next(dst.rglob("*run_identity.json"))
    rec = json.loads(ident.read_text())
    rec["identity"]["defines"] = [d for d in rec["identity"]["defines"]
                                  if d != r2r.PULSE2X_DEFINE]
    ident.write_text(json.dumps(rec))
    monkeypatch.setattr(r2r, "EVIDENCE_DIR", dst)
    with pytest.raises(r2r.Refused, match="lack VOICE_PULSE_2X"):
        r2r.evidence()


def test_r2_does_not_write_r1_or_r0_records():
    src = (HERE / "r2_release.py").read_text()
    for name in ("r1-2025.1.json", "r1-candidate.json", "baseline-2025.1.json"):
        assert f'{name}").write' not in src


# ---- the frozen sources: r2_candidate's derivation, never the working tree (#1392)
def test_the_real_working_tree_is_never_read_for_the_binding(monkeypatch):
    """The release's frozen sources must not consult build_arty.sources()/roms(),
    the working tree's path lists, anywhere on the way to a verdict."""
    import build_arty as ba

    def boom():
        raise AssertionError("working-tree path list consulted")
    monkeypatch.setattr(ba, "sources", boom)
    monkeypatch.setattr(ba, "roms", boom)
    verdict, detail = r2r.check()
    assert verdict == "BOUND", detail


def test_a_post_r2_source_or_rom_in_the_tree_stays_bound(monkeypatch):
    """build_arty.roms() is the glob of rtl-sketch/*.hex and build_arty.sources()
    the working tree's SRCS: that is how a file added after R2 (a correction ROM
    for #257, a new module) appears in the tree. It must not reach R2's release."""
    import build_arty as ba
    roms, sources = ba.roms(), ba.sources()          # the tree's own listings, taken now
    extra_rom, extra_src = (r2r.ROOT / "rtl-sketch/post_r2_corr.hex",
                            r2r.ROOT / "rtl-sketch/post_r2_new.v")
    monkeypatch.setattr(ba, "roms", lambda: roms + [extra_rom])
    monkeypatch.setattr(ba, "sources", lambda: sources + [extra_src])
    assert extra_rom in ba.roms() and extra_src in ba.sources()      # the tree lists both
    verdict, detail = r2r.check()
    assert verdict == "BOUND", detail


def test_a_candidate_refusal_is_reported_as_the_releases_own(monkeypatch):
    """r2_candidate.Refused and r2_release.Refused are siblings under
    refusal.Refused. Untranslated, the candidate's escapes check() as a
    traceback instead of the REFUSED, with its reason, that it is."""
    import r2_candidate as r2c
    pub = json.loads((r2r.PUB_DIR / "publication.json").read_text())

    def refuse(*commit):
        raise r2c.Refused("injected: candidate refusal")
    monkeypatch.setattr(r2c, "rtl", refuse)
    monkeypatch.setattr(r2c, "_frozen_blob", refuse)
    with pytest.raises(r2r.Refused, match="injected: candidate refusal"):
        r2r.frozen_rtl()
    with pytest.raises(r2r.Refused, match="injected: candidate refusal"):
        r2r.constraint_binding(r2r.PUB_DIR, pub)
    assert r2r.check() == ("REFUSED", "injected: candidate refusal")


def test_an_unreadable_freeze_refuses_not_a_traceback(monkeypatch):
    monkeypatch.setattr(r2r, "R2_FROZEN_AT", "0" * 40)
    verdict, detail = r2r.check()
    assert verdict == "REFUSED" and "shallow clone" in detail, detail


def test_an_empty_freeze_refuses_by_name(monkeypatch):
    monkeypatch.setattr(r2r, "R2_FROZEN_AT", "")
    assert r2r.check() == ("REFUSED", "R2_FROZEN_AT is not set: no R2 image has been built")


def test_image_identity_threads_the_freeze_into_the_constraint_binding(monkeypatch):
    """The XDC the #315 reports are checked against is the one at the commit the
    sources were read at, handed over by the caller, not the module's default
    read a second time."""
    seen = []
    real = r2r.constraint_binding

    def spy(pub_dir, pub, frozen_at=None):
        seen.append(frozen_at)
        return real(pub_dir, pub, frozen_at)
    monkeypatch.setattr(r2r, "constraint_binding", spy)
    r2r.image_identity()
    assert seen == [r2r.R2_FROZEN_AT]


def test_the_constraint_binding_reads_the_xdc_at_the_freeze_it_is_given(monkeypatch):
    freeze, pub = r2r.R2_FROZEN_AT, json.loads((r2r.PUB_DIR / "publication.json").read_text())
    monkeypatch.setattr(r2r, "R2_FROZEN_AT", "0" * 40)       # the module default: unreadable
    got = r2r.constraint_binding(r2r.PUB_DIR, pub, frozen_at=freeze)
    assert got == json.loads(r2r.MANIFEST.read_text())["image"]["constraint_binding"]


def test_a_fresh_derivation_is_the_committed_manifest_byte_for_byte():
    """What --write emits is the committed file, key order included. check()
    compares values; this compares the bytes."""
    fresh = (json.dumps(r2r.build(), indent=2) + "\n").encode()
    assert fresh == r2r.MANIFEST.read_bytes()

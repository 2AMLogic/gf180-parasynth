"""Tier-3 empty-record verdict and assertion audit (#1469, from #1466).

Exercises:
1. `tools/probes/estimator_ground_truth_controls.py` directly and via its
   Makefile caller `estimator_ground_truth.py controls`: empty populations and
   the Rule-8 zero-answered-pairs input refuse with exit 2; populated controls
   pass (0) when mutants fire and fail (1) when a mutant stays green.
2. Every unreachable tier-3 candidate's guard or constant population, executed
   directly (no live Vivado, VST3 plugin, or unmounted corpus required), plus
   the census reconciliation at `origin/main`.
"""
from __future__ import annotations

import ast
import io
import json
import pathlib
import re
import sys
import zipfile

import numpy as np
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
for _rel in ("tools", "model", "tools/probes", "tools/probes/hihat",
             "pnr/shuttle", "pnr/klt/ladder_dp", "spec/reference"):
    _p = str(ROOT / _rel)
    if _p not in sys.path:
        sys.path.insert(0, _p)

import audio_measure as am                                              # noqa: E402
import estimator_fixtures as ef                                         # noqa: E402
import estimator_ground_truth as egt                                    # noqa: E402
import estimator_ground_truth_controls as egtc                          # noqa: E402


# ===========================================================================
# 1. estimator_ground_truth_controls.py (via estimator_ground_truth.py controls)
# ===========================================================================

def _one_sine_fixture():
    sr = 48000
    t = np.arange(4800) / sr
    x = 0.5 * np.sin(2 * np.pi * 440.0 * t)
    return ef.Fixture("damped_sine", "sine440", x, sr,
                      {"rms": float(np.sqrt(np.mean(x * x))), "f": 440.0})


def test_egtc_empty_fixtures_and_mutants_refuses_via_caller(monkeypatch, capsys):
    """#1469: `fixtures=[]` and `MUTANTS=[]` ran 0 clean checks and 0 mutants
    and exited 0 ('controls: PASS -- 0 mutants each reddened...')."""
    monkeypatch.setattr(egtc, "control_fixtures", lambda: [])
    monkeypatch.setattr(egtc, "MUTANTS", [])
    rc = egt.main(["controls"])
    out = capsys.readouterr().out
    assert rc == 2
    assert "REFUSED" in out and "controls: PASS" not in out


@pytest.mark.parametrize("empty_which", ["fixtures", "mutants", "answered"])
def test_egtc_partial_empty_or_unanswered_population_refuses(
        monkeypatch, capsys, empty_which):
    """Refuses exit 2 when fixtures are empty, MUTANTS are empty, or (Rule 8)
    both are non-empty while zero (estimator, family) pairs answered."""
    fx = _one_sine_fixture()
    good_mutant = [("rms x2", "rms", lambda: (lambda x: 2.0 * float(np.sqrt(np.mean(x * x)))),
                    ("rms", "damped_sine"))]
    if empty_which == "fixtures":
        monkeypatch.setattr(egtc, "control_fixtures", lambda: [])
        monkeypatch.setattr(egtc, "MUTANTS", good_mutant)
    elif empty_which == "mutants":
        monkeypatch.setattr(egtc, "control_fixtures", lambda: [fx])
        monkeypatch.setattr(egtc, "MUTANTS", [])
    else:
        monkeypatch.setattr(egtc, "control_fixtures", lambda: [fx])
        monkeypatch.setattr(egtc, "MUTANTS", good_mutant)
        monkeypatch.setattr(egt, "SPECS", [])
    rc = egt.main(["controls"])
    out = capsys.readouterr().out
    assert rc == 2
    assert "REFUSED" in out and "controls: PASS" not in out


def test_egtc_measured_clean_failure_takes_precedence(monkeypatch, capsys):
    monkeypatch.setattr(egtc, "control_fixtures", lambda: [_one_sine_fixture()])
    monkeypatch.setattr(egt, "SPECS", [next(s for s in egt.SPECS if s.name == "rms")])
    monkeypatch.setattr(am, "rms", lambda x: 0.0)
    assert egt.main(["controls"]) == 1
    out = capsys.readouterr().out
    assert "FAIL clean run" in out and "nothing was measured" not in out


def test_egtc_populated_good_and_bad_cases_via_caller(monkeypatch, capsys):
    fx = _one_sine_fixture()
    rms_spec = next(s for s in egt.SPECS if s.name == "rms")
    monkeypatch.setattr(egtc, "control_fixtures", lambda: [fx])
    monkeypatch.setattr(egt, "SPECS", [rms_spec])

    # Keep the individual verdicts from the same run that grades the mutant.
    verdicts = []
    run = egt.run

    def record_run(*args, **kwargs):
        rows = run(*args, **kwargs)
        verdicts.extend(rows)
        return rows

    monkeypatch.setattr(egt, "run", record_run)
    original_rms = am.rms
    # Populated good case: mutant doubles rms -> caught, stubs redden rms -> exit 0
    monkeypatch.setattr(egtc, "MUTANTS", [
        ("rms x2", "rms", lambda: (lambda x: 2.0 * original_rms(x)), ("rms", "damped_sine")),
    ])
    assert egt.main(["controls"]) == 0
    assert "controls: PASS" in capsys.readouterr().out

    assert any(v.status == egt.FAIL and v.detail == "got 0.707107 want 0.353553 (+100.00 %)"
               for v in verdicts)

    # Populated bad case: no-op mutant leaves rms intact -> NOT-CAUGHT -> exit 1
    monkeypatch.setattr(egtc, "MUTANTS", [
        ("rms noop", "rms", lambda: am.rms, ("rms", "damped_sine")),
    ])
    assert egt.main(["controls"]) == 1
    assert "controls: FAIL" in capsys.readouterr().out

    # Reinstate the recursive replacement that used to earn credit for a crash.
    monkeypatch.setattr(egtc, "MUTANTS", [
        ("recursive rms", "rms", lambda: (lambda x: am.rms(x)), ("rms", "damped_sine")),
    ])
    assert egt.main(["controls"]) == 1
    assert "NOT-CAUGHT" in capsys.readouterr().out


# ===========================================================================
# 2. Pinned refusals and constant populations on unreachable tier-3 files
# ===========================================================================

def test_census_reconciles_against_known_tier3_set():
    dirs = ("tools", "rtl-sketch", "model", "fpga", "pnr", "spec")
    shape = re.compile(r"^\s*if (not )?(fails|failures|bad|errors|reds|mismatches"
                       r"|problems|defects|violations)\b", re.M)
    handled = {"tools/probes/verify_109_claims.py",
               "tools/probes/estimator_domains.py",
               "tools/probes/estimator_ground_truth.py"}
    gates = ["Makefile"] + [str(p.relative_to(ROOT))
                            for p in sorted((ROOT / ".github/workflows").glob("*.yml"))]
    texts = {g: (ROOT / g).read_text(errors="replace") for g in gates}
    tools, tests = [], []
    for d in dirs:
        for p in sorted((ROOT / d).rglob("*.py")):
            rel = p.relative_to(ROOT).as_posix()
            if rel in handled or not shape.search(p.read_text(errors="replace")):
                continue
            g = ",".join(pathlib.Path(k).name for k, t in texts.items() if p.name in t) or "-"
            (tests if p.name.startswith("test_") else tools).append((rel, g))
    non_gated = {r for r, g in tools if g == "-"}
    table = ROOT / "docs/tier3-empty-records.md"
    assert table.is_file(), "missing tier-3 disposition table"
    paths = re.findall(r"^\| `([^`]+\.py)` · ", table.read_text(), re.M)
    assert len(paths) == len(set(paths)) == 48, "expected one disposition per audited path"
    historical = "fpga/testdata/mutate_pnr_report.py"  # 'if bad:' became 'if survivors:'
    current = non_gated | {path for path, _ in tests}
    assert historical not in current
    assert current | {historical} == set(paths), "census differs from disposition table"


@pytest.mark.parametrize("with_table", [False, True])
def test_two_tools_cannot_satisfy_the_census(monkeypatch, tmp_path, with_table):
    (tmp_path / "tools").mkdir()
    (tmp_path / "Makefile").touch()
    for name in ("refaudio_member_list.py", "check_reference_freeze.py"):
        (tmp_path / "tools" / name).write_text("if bad: pass\n")
    if with_table:
        table = pathlib.Path("docs/tier3-empty-records.md")
        (tmp_path / table.parent).mkdir()
        (tmp_path / table).write_bytes((ROOT / table).read_bytes())
    monkeypatch.setattr(sys.modules[__name__], "ROOT", tmp_path)
    with pytest.raises(AssertionError, match="disposition table"):
        test_census_reconciles_against_known_tier3_set()


def test_unreachable_refusals_and_constant_populations_hold(tmp_path, monkeypatch, capsys):
    # model/drum_fit.py: constant SELF_TEST_TARGETS, --self-test runs all 8
    import drum_fit as df
    assert len(df.SELF_TEST_TARGETS) == 8
    assert df.main(["--self-test"]) == 0

    # model/moog_probe.py: scan(empty_dir) raises ScanRefused
    import moog_probe as mp
    with pytest.raises(mp.ScanRefused, match="no usable recording"):
        mp.scan(str(tmp_path))

    # pnr/klt/ladder_dp/check_route_drc_extract.py: verify({},{},{}) reports problems; CLI passes
    import check_route_drc_extract as crde
    assert len(crde.verify({}, {}, {})) >= 6
    assert crde.main([]) == 0

    # pnr/shuttle/check_route.py: refuses empty run_dir & 0-component DEF; fails empty census
    import check_route as cr
    empty_run = tmp_path / "shuttle-empty"
    empty_run.mkdir()
    with pytest.raises(cr.Refusal, match="no .def"):
        cr.verify(str(empty_run), {"flops_declared_per_module": {}}, 1, 5)
    step = empty_run / "50-openroad-detailedrouting"
    step.mkdir()
    (step / "chip_top.def").write_text(
        "VERSION 5.8 ;\nDESIGN chip_top ;\nCOMPONENTS 0 ;\nEND COMPONENTS\nEND DESIGN\n")
    with pytest.raises(cr.Refusal, match="no COMPONENTS"):
        cr.verify(str(empty_run), {"flops_declared_per_module": {}}, 1, 5)
    (step / "chip_top.def").write_text(
        "VERSION 5.8 ;\nDESIGN chip_top ;\nCOMPONENTS 1 ;\n"
        "- c0 gf180mcu_fd_sc_mcu7t5v0__nand2_1 + PLACED ( 0 0 ) N ;\n"
        "END COMPONENTS\nNETS 0 ;\nEND NETS\nEND DESIGN\n")
    assert cr.verify(str(empty_run), {"flops_declared_per_module": {}}, 1, 5) == 1

    # spec/reference/gen_tables.py: 10 constant tables + contract + tanh ROM
    import gen_tables as gt_mod
    assert len(gt_mod.tables()) == 10
    assert gt_mod.main(["--check"]) == 0

    # tools/analyse_m1a_phase_cycle.py: constant ODD=('h5','h7','h9','h11'), --control passes
    import analyse_m1a_phase_cycle as ampc
    assert len(ampc.ODD) == 4
    with monkeypatch.context() as m:
        m.setattr(sys, "argv", ["analyse_m1a_phase_cycle.py", "--control"])
        assert ampc.main() == 0

    # tools/bd_pitch_predeclaration.py: check({}) flags empty conditions; CLI check passes
    import bd_pitch_predeclaration as bdp
    assert any("non-empty" in s for s in bdp.check({}))
    assert bdp.main(["check"]) == 0

    # tools/capture_m1a_phase_cycle.py: reproduce_frozen appends 2 unconditional controls
    import capture_m1a_phase_cycle as cmpc
    zeros = np.zeros(4800, dtype=np.float32)
    with monkeypatch.context() as m:
        m.setattr(cmpc.m1a, "render", lambda overrides=None: (zeros, 48000))
        m.setattr(cmpc.wavfile, "read", lambda p: (48000, zeros))
        rows = cmpc.reproduce_frozen({"renders": [], "controls": {
            "osc1_open": {"file": "a.wav"}, "osc2_open": {"file": "b.wav"}}})
    assert len(rows) == 2

    # tools/check_m1a_selection.py: straight-line check() over committed records
    import check_m1a_selection as cms
    assert cms.main([]) == 0

    # tools/cymbal_tone_render.py: owned_addresses_check iterates constant 16 SOUND_NAMES
    import cymbal_tone_render as ctr
    assert len(ctr.dx.SOUND_NAMES) == 16
    assert ctr.owned_addresses_check("50", "50") == {"stray": {}, "status": "PASS"}

    # tools/deadline_binding_probe.py: classify() returns 2 (NO VERDICT) on empty trace
    import deadline_binding_probe as dbp
    assert dbp.classify("refused", [], [], False)[0] == 2

    # tools/dphase_floor_census.py: check_corpus refuses an empty corpus dir (exit 2)
    import dphase_floor_census as dfc
    assert dfc.main(["--refs", str(empty_run)]) == 2

    # tools/drum_render_hashes.py: constant 16 SOUNDS16
    import perceptual_gate as pg
    assert len(pg.SOUNDS16) == 16

    # tools/gate_calibrate.py: calibrate({}) refuses
    import gate_calibrate as gc
    assert str(gc.calibrate({})["status"]).startswith("REFUSED")

    # tools/measure_m5_operating_level.py: bad-drive guard rejects out-of-budget drives
    import measure_m5_operating_level as mmol
    with pytest.raises(SystemExit, match="REFUSED"):
        mmol.main(["--out", str(tmp_path / "m5.json"), "--drives", "99.0"])

    # tools/measure_mono_case.py: 12-harmonic range check
    import measure_mono_case as mmc
    sine = 1e-4 * np.sin(2 * np.pi * 220.0 * np.arange(int(0.7 * mmc.SR)) / mmc.SR)
    assert mmc._measure(sine, 220.0)["f0_hz"] == pytest.approx(220.0, abs=0.5)

    # tools/measure_saw_alias_after_2x.py: report-only annotation, exit codes {0, 2}
    src_msa = (ROOT / "tools/measure_saw_alias_after_2x.py").read_text()
    fn_msa = next(n for n in ast.walk(ast.parse(src_msa))
                  if isinstance(n, ast.FunctionDef) and n.name == "main")
    rets = sorted({n.value.value for n in ast.walk(fn_msa)
                   if isinstance(n, ast.Return) and isinstance(n.value, ast.Constant)})
    assert rets == [0, 2]

    # tools/noise_stage_attribution.py: 3 unconditional known-answer checks
    import noise_stage_attribution as nsa
    assert len(nsa.qualify()) == 3

    # tools/performance_suite.py: _check_automation refuses < 2 events
    import performance_suite as ps

    class _P:
        events = []

    with pytest.raises(ps.Refused, match="at least two"):
        ps._check_automation(0, {"kind": "centroid", "events": [],
                                 "direction": 1, "min_ratio": 2.0}, _P())

    # tools/preparation_contract.py: empty signal returns shape violation
    import preparation_contract as pc
    _st, viol = pc.side_state(pc.Side("ours", [], 48000),
                              lead_samples=lambda sr: 1,
                              trim_samples=lambda sr: 1, onset_frac=0.5)
    assert viol and viol[0][0] == "shape"

    # tools/probe_orphan_reap.py: prints explicit denominator on no-worktree root
    import probe_orphan_reap as por
    capsys.readouterr()
    assert por.main(["--repo-root", str(empty_run)]) == 0
    assert "no issue worktrees" in capsys.readouterr().out

    # tools/refaudio_member_list.py: folder with 0 WAVs refuses with exit 2
    import refaudio_member_list as rml
    zpath = tmp_path / "empty_folder.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("other/readme.txt", "hi")
    with monkeypatch.context() as m:
        m.setattr(rml.s3, "preconditions",
                  lambda a: ("s3://fake", {"archive_sha256": "0" * 64}))
        m.setattr(rml.s3, "download", lambda r, a, rec: zpath)
        assert rml.main(["pack.zip", "4x4", "--keep-archive"]) == rml.REFUSED

    # tools/probes/balance_input_invariance.py: collect() builds 41 constant-keyed images
    import balance_input_invariance as bii
    assert len(bii.collect()["images"]) >= 41

    # tools/probes/dc_blocker.py: constant SUBJECTS + CONTROLS
    import dc_blocker as dcb
    assert len(dcb.SUBJECTS + dcb.CONTROLS) > 0

    # tools/probes/estimator_defects.py: refuses missing and empty REFS with exit 2
    import estimator_defects as ed
    with monkeypatch.context() as m:
        m.setattr(sys, "argv", ["estimator_defects.py"])
        m.setattr(ed, "REFS", tmp_path / "no-such-refs")
        assert ed.main() == 2
        m.setattr(ed, "REFS", empty_run)
        assert ed.main() == 2

    # tools/probes/f1_calibration_headroom.py: constant 5 CONDITIONS
    import f1_calibration_headroom as f1ch
    assert len(f1ch.CONDITIONS) == 5

    # tools/probes/noise_fixture_duration.py: decide() rejects empty rows
    import noise_fixture_duration as nfd
    empty_side = {"rows": {}, "select": {"control": {}, "neighbour": {}, "clean": {}},
                  "select_groups": 0, "recal": [], nfd.pd.CAUGHT: {},
                  nfd.CONTROL[0]: {"state": nfd.pd.CAUGHT, "margin": 1.0}}
    cand_side = {**empty_side, nfd.CONTROL[0]: {"state": nfd.pd.CAUGHT, "margin": 2.0}}
    ok, _why = nfd.decide(empty_side, cand_side)
    assert not ok

    # tools/probes/residual_dc.py: verdicts([]) and floor_verdicts([]) refuse
    import residual_dc as rdc
    assert rdc.verdicts([])[0] == "REFUSED"
    assert rdc.floor_verdicts([])[0] == "REFUSED"

    # tools/probes/tom_pitch_drop_fischer.py: fit({}) refuses on min_dev_rows
    import tom_pitch_drop_fischer as tpdf
    assert tpdf.fit({}, {"split_of": {"LT50": "development"},
                         "rules": {"min_dev_rows": 3}})["status"] == "REFUSED"

    # tools/probes/hihat/hh_probe5.py: 2 unconditional datapath checks
    import hh_probe5 as hhp5
    out_hh = {}
    hhp5.assert_datapath(out_hh)
    assert out_hh["datapath"]["src_bits"] == 5

    # Test modules: assert_same_preparation (3 unconditional checks) and _pulse2x_step_set_gated
    import test_discrimination as td
    good_prep = dict(n=1, sr=48000, lead_ms=0.0, first=0.0, peak=1.0, dur_ms=1.0)
    assert td.assert_same_preparation(good_prep, good_prep)
    with pytest.raises(AssertionError, match="sr"):
        td.assert_same_preparation(good_prep, {**good_prep, "sr": 44100})

    import test_setup_ci_verilator as tscv
    ok_v, why_v = tscv._pulse2x_step_set_gated({"steps": []})
    assert not ok_v and "vacuous" in why_v

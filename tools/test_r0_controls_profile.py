"""tools/r0_controls_profile.py: the instrument that times T-PHYSICAL's control.

An instrument that answers about the wrong run looks exactly like one that
answered about the right one, so each test here is one way for it to be wrong:

- `verify` accepts the run the test asserts and refuses each way that run can
  differ from it (a PASS, a broken receipt, a child without its timings,
  sessions that do not add up to the child's own seconds), and reports -- never
  refuses -- a control that missed a defect, because that is a finding;
- the profiler must not rebind `analyse`: run_controls dispatches on
  `analyser is analyse`, so a wrapped `analyse` would route every session
  through the entry point that refuses synthetic sessions, refuse all thirteen,
  and time that;
- `--smoke` exercises every path the measurement takes (the launcher the trial
  starts, the three children, the per-session rows) and says it is not a
  measurement;
- the kernel tables `--bench` times must compute the kernel they stand for.
"""
from __future__ import annotations

import json
import pathlib
import sys

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import r0_capture as rc                                   # noqa: E402
import r0_controls_profile as prof                        # noqa: E402


def _timed_children():
    return {
        "r0-capture": {"secs": 1.0, "main_s": 0.9, "functions": {"main": [1, 0.9, 0.9]},
                       "sessions": []},
        "synthetic-defects": {"secs": 100.0, "main_s": 99.0, "functions": {"main": [1, 99.5, 0.5]},
                              "sessions": [{"name": "clean", "synth_s": 30.0, "analyse_s": 69.0}]},
        "cross-image-refusal": {"secs": 5.0, "main_s": 4.9, "functions": {"main": [1, 4.9, 0.1]},
                                "sessions": [{"name": "c1", "synth_s": 2.0, "analyse_s": 0.0}]},
    }


def _the_test_s_run():
    """What tools/trial.py records when the test's assertions all hold."""
    rec = {"execution": {"status": "complete"}, "verdict": prof.trial.NO_VERDICT,
           "children": [{"id": "r0-capture", "verdict": prof.trial.NO_VERDICT,
                         "reasons": ["the session is synthetic (r0_capture.py synth): ..."],
                         "execution": {"state": "ok", "rc": 2, "secs": 1.0}}],
           "controls": [{"id": "synthetic-defects", "caught": True,
                         "execution": {"state": "ok", "rc": 0, "secs": 100.0}},
                        {"id": "cross-image-refusal", "caught": True,
                         "execution": {"state": "ok", "rc": 0, "secs": 5.0}}]}
    ctl = {"clean": {"verdict": "PASS", "ok": True, "known_answers": {"ppm": [35.0, 35.1]}},
           "all_caught": True,
           "defects": {"gain": {"intended": "gain", "verdict": "FAIL", "caught": True,
                                "failed": ["gain"], "outcome": "caught"},
                       "swap": {"intended": None, "verdict": "PASS", "caught": True,
                                "failed": [], "outcome": "BLIND as declared"}}}
    return rec, ctl, _timed_children()


def _verified(rec, ctl, children, **kw):
    return prof.verify(rec, ctl, children, receipt_ok=True, receipt_problems=[], **kw)


def test_the_run_the_test_asserts_is_accepted():
    assert _verified(*_the_test_s_run()) == ([], [])


def _set(root, path, value):
    obj = root
    for key in path[:-1]:
        obj = obj[key]
    obj[path[-1]] = value


@pytest.mark.parametrize("path, value, needle", [
    (("rec", "verdict"), "PASS", "T-PHYSICAL PASSed"),
    (("rec", "execution", "status"), "timeout", "execution timeout"),
    (("rec", "children", 0, "verdict"), "FAIL", "r0-capture FAIL"),
    (("rec", "children", 0, "reasons"), ["capture metadata incomplete"], "synthetic"),
    (("rec", "children", 0, "execution", "rc"), 0, "exit 0"),
    (("rec", "controls", 0, "caught"), False, "synthetic-defects not caught"),
    (("rec", "controls", 1, "caught"), False, "cross-image-refusal not caught"),
    (("rec", "controls", 0, "execution", "rc"), 1, "exit 1"),
    (("ctl", "all_caught"), False, "controls.json says NOT ALL CAUGHT"),
    (("children", "synthetic-defects", "sessions", 0, "synth_s"), 1.0, "account for"),
    (("children", "synthetic-defects", "functions"), {}, "no timings"),
    (("children",), {"r0-capture": {"functions": {}, "sessions": []}}, "no timings"),
])
def test_a_run_that_is_not_the_test_s_is_refused(path, value, needle):
    roots = dict(zip(("rec", "ctl", "children"), _the_test_s_run()))
    _set(roots, path, value)
    apparatus, _ = _verified(roots["rec"], roots["ctl"], roots["children"])
    assert any(needle in p for p in apparatus), apparatus


def test_a_receipt_that_does_not_check_is_refused():
    rec, ctl, children = _the_test_s_run()
    apparatus, _ = prof.verify(rec, ctl, children, receipt_ok=False,
                               receipt_problems=["artifact missing: controls.json"])
    assert any("receipt" in p and "artifact missing" in p for p in apparatus), apparatus


def test_a_defect_missing_from_the_record_is_refused():
    rec, ctl, children = _the_test_s_run()
    apparatus, _ = _verified(rec, ctl, children, expect_defects=["gain", "swap", "dropout"])
    assert any("dropout" in p for p in apparatus), apparatus


def test_a_control_that_missed_a_defect_is_reported_never_refused():
    """The control's own record explaining a miss is a finding; the same miss
    with no record behind it is the apparatus (above, `not caught`)."""
    rec, ctl, children = _the_test_s_run()
    ctl["defects"]["gain"].update(caught=False, verdict="PASS", failed=[], outcome="NOT caught")
    ctl["all_caught"] = False
    rec["controls"][0].update(caught=False, execution={"state": "ok", "rc": 1, "secs": 100.0})
    apparatus, control = _verified(rec, ctl, children)
    assert apparatus == []
    assert any("gain" in p and "NOT caught" in p for p in control), control
    assert any("did not catch everything" in p for p in control), control


def test_a_defect_caught_for_another_property_is_reported():
    rec, ctl, children = _the_test_s_run()
    ctl["defects"]["gain"]["failed"] = ["residual"]
    _, control = _verified(rec, ctl, children)
    assert any("gain" in p and "residual" in p for p in control), control


def test_a_clean_run_that_recovered_nothing_is_reported():
    rec, ctl, children = _the_test_s_run()
    ctl["clean"].update(ok=False, verdict="FAIL")
    _, control = _verified(rec, ctl, children)
    assert any("clean" in p for p in control), control


def test_the_profiler_does_not_rebind_the_analyser_the_control_dispatches_on(tmp_path,
                                                                            monkeypatch):
    """run_controls calls `_run(analyser, ...)`, which passes allow_synthetic
    only when `analyser is analyse`. A profiler that wrapped `analyse` in place
    would send all thirteen sessions down the other branch, refuse each as
    synthetic, and time that instead of the control."""
    analyse, synth_session = rc.analyse, rc.synth_session
    monkeypatch.setattr(rc, "REQUIRED_COMMANDS", ("demo",))      # so a 3-take session analyses
    takes = (("silence-1", "silence"), ("demo-1", "demo"), ("demo-2", "demo"))
    with prof.Profiler(rc) as p:
        assert rc.analyse is analyse
        d = rc.synth_session(tmp_path / "s", takes=takes)
        rec = rc._run(rc.analyse, d, rc.REFERENCES)
        f = p.stats
        assert [s[0] for s in p.sessions] == ["synth_session", "_run"]
        assert p.sessions[0][2] > 0 and p.sessions[1][2] > 0
    assert rc.synth_session is synth_session and rc.analyse is analyse
    assert "synthetic" not in " ".join(rec["reasons"]), rec["reasons"]   # allow_synthetic passed
    assert rec["verdict"] == rc.PASS, rec["reasons"]                  # and it analysed
    assert f["synth_session"][0] == 1 and f["_run"][0] == 1
    assert f["analyse_take"][0] == 3 and f["calibrate"][0] == 1
    assert f["unwarp_capture"][0] == 2 and "analyse" not in f
    assert f["interp"][0] > 10 and "tone_f0" not in f
    # inclusive time is self plus the wrapped functions called inside
    assert f["analyse_take"][1] < f["_run"][1]
    assert 0 < f["_run"][2] < f["_run"][1]
    # interp runs inside both the synthesis and the analysis: its inclusive time
    # is bounded by their sum, and it is not bounded by either alone
    assert f["_run"][1] < f["interp"][1] < f["synth_session"][1] + f["_run"][1]


def test_smoke_mode_exercises_every_child_and_says_it_is_not_a_measurement(tmp_path, capsys,
                                                                          monkeypatch):
    for k in ("R0_CAPTURE_BUNDLE", "R0_PROFILE_DIR"):
        monkeypatch.setenv(k, "unset-by-the-test")
    out = tmp_path / "profile"
    assert prof.main(["--out", str(out), "--smoke"]) == 0
    r = json.loads((out / "report.json").read_text())
    assert r["smoke"] is True and r["verdict"] == "SMOKE"
    assert set(r["children"]) == {"r0-capture", "synthetic-defects", "cross-image-refusal"}
    d = r["children"]["synthetic-defects"]
    assert [s["name"] for s in d["sessions"]] == ["clean", "gain", "swap"]
    assert all(s["synth_s"] > 0 and s["analyse_s"] > 0 for s in d["sessions"])
    assert d["functions"]["synth_take"][0] == 6                 # three sessions, two takes
    assert d["functions"]["interp"][0] == 3        # one warp each: silence has nothing to warp
    assert r["children"]["cross-image-refusal"]["functions"]["synth_session"][0] == 3
    assert r["phases"]["trial_s"] > 0 and r["phases"]["receipt_s"] >= 0
    assert "NOT A MEASUREMENT" in capsys.readouterr().out


@pytest.mark.parametrize("order, n, bound, measured", [
    (3, 256, 2e-10, 1.1e-10),      # 16x finer than this per doubling, away from the edges
    (3, 1024, 4e-12, 2.2e-12),     # the window's edge taps converge at 4x per doubling
    (3, 4096, 2e-13, 1.4e-13),
    (1, 16384, 2e-9, 1.5e-9),
])
def test_kernel_tables_agree_with_the_direct_formula(order, n, bound, measured):
    """The alternatives --bench times must compute the kernel they stand for
    before their seconds mean anything: each table, over a dense scan of the
    fractional part, against sinc x Kaiser evaluated directly. The kernel's
    peak is 1; the least significant bit of the 24-bit takes is 1.2e-7."""
    t = np.linspace(0.0, 1.0, 200_001)
    tt = t[:, None] - np.arange(-31, 33)
    direct = np.sinc(tt) * rc._kaiser(tt, 32)
    err = float(np.max(np.abs(prof.table_weights(prof.kernel_table(32, n, order), t, n, order)
                              - direct)))
    assert err <= bound, (order, n, err)
    assert err > measured / 4, (order, n, err)          # and the bound is not loose by 4x


def test_the_alternative_kernels_reproduce_direct_on_a_reference():
    """What --bench reports as a difference must be a difference of the kernel
    and not of the plumbing around it: on the real reference, warped as the
    control warps it, with positions before the delay and past the end, `lean`
    is `direct` bit for bit and the tables are inside their kernel error."""
    x = rc.load_reference(rc.REFERENCES, "held-m5a-saw")["x"]
    pos = (np.arange(-200, x.size + 70000) - 22222.75) * (1 + 35e-6)
    pos[5000] = np.nextafter(pos[5000], np.inf)               # a fractional part at the cell's edge
    want = prof.direct(x, pos)
    assert np.array_equal(prof.lean(x, pos), want)
    for n, order, bound in ((1024, 3, 1e-11), (4096, 3, 1e-12), (16384, 1, 1e-8)):
        got = prof.table(x, pos, 32, n, order)
        assert float(np.max(np.abs(got - want))) <= bound, (n, order)
    assert np.count_nonzero(want[:200 - 1] == 0) == 200 - 1     # before the delay: silence


def test_a_coarse_table_is_seen_to_differ_the_direct_formula_is_the_control():
    """The bounds above are only a control if a worse table fails them."""
    t = np.linspace(0.0, 1.0, 200_001)
    tt = t[:, None] - np.arange(-31, 33)
    direct = np.sinc(tt) * rc._kaiser(tt, 32)
    coarse = prof.table_weights(prof.kernel_table(32, 256, 1), t, 256, 1)
    assert float(np.max(np.abs(coarse - direct))) > 1e-6

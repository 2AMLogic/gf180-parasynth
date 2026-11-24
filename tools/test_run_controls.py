"""#1126: `make controls` must tie each RTL mutant to a clean run of its config.

Two halves. The orchestration tests drive the real CLI (`run_controls.main`)
with real subprocesses, so exit statuses and the JSON report are the shipped
ones. The wiring tests read the live `controls` recipe, because a runner nobody
calls proves nothing ("check that the thing you are testing is the thing that
ships").
"""
from __future__ import annotations

import json
import re
import shlex
import sys

import pytest

import run_controls
from makefile_recipe import makefile_recipe

PY = shlex.quote(sys.executable)


def sh(code: int) -> str:
    return f"{PY} -c 'import sys; sys.exit({code})'"


def run(tmp_path, cmds, **kw):
    report = tmp_path / "controls.json"
    argv = ["--json", str(report), *[a for k, v in kw.items() for a in (f"--{k}", str(v))], *cmds]
    rc = run_controls.main(argv)
    data = json.loads(report.read_text()) if report.exists() else None
    return rc, data


# -- orchestration -----------------------------------------------------------

def test_always_mismatching_bench_cannot_credit_a_mutant(tmp_path):
    """The #1126 reproduction: the bench is red clean (exit 1) and `--expect-fail`
    turns the mutant's exit-1 into exit 0 -- 'CAUGHT'. Behind the baseline that
    PASS must never be reported."""
    rc, rep = run(tmp_path, [f"@baseline=spi {sh(1)}", f"@after=spi {sh(0)} # SPI_ADDR7"])
    assert rc == 1
    base, mut = rep
    assert base["state"] == "FAIL" and base["role"] == "baseline"
    assert mut["state"] == "NO-VERDICT" and mut["skipped"] and mut["baseline"] == "spi"
    assert "SPI_ADDR7" in mut["cmd"], "a skipped mutant must stay identifiable"
    assert "spi" in mut["out"]


def test_clean_pass_detected_mutant_passes(tmp_path):
    rc, rep = run(tmp_path, [f"@baseline=a {sh(0)}", f"@after=a {sh(0)} # m1"])
    assert rc == 0 and [r["state"] for r in rep] == ["PASS", "PASS"]


def test_clean_pass_ineffective_mutant_still_fails(tmp_path):
    rc, rep = run(tmp_path, [f"@baseline=a {sh(0)}", f"@after=a {sh(1)} # noop"])
    assert rc == 1
    assert rep[0]["state"] == "PASS" and rep[1]["state"] == "FAIL" and not rep[1]["skipped"]


def test_two_mutants_share_one_baseline_and_one_failing_group_is_isolated(tmp_path):
    cmds = [f"@baseline=a {sh(0)} # clean a", f"@after=a {sh(0)} # a1", f"@after=a {sh(0)} # a2",
            f"@baseline=b {sh(1)} # clean b", f"@after=b {sh(0)} # b1"]
    rc, rep = run(tmp_path, cmds)
    by = {r["cmd"].split("# ")[-1]: r for r in rep}
    assert rc == 1
    assert by["a1"]["state"] == by["a2"]["state"] == "PASS"
    assert by["clean b"]["state"] == "FAIL"
    assert by["b1"]["state"] == "NO-VERDICT" and by["b1"]["skipped"]
    assert [r["cmd"] for r in rep][0].endswith("clean a"), "report keeps recipe order"


def test_refused_baseline_gives_dependents_no_verdict(tmp_path):
    rc, rep = run(tmp_path, [f"@baseline=a {sh(2)}", f"@after=a {sh(0)}"])
    assert rc == 1 and rep[0]["state"] == "NO-VERDICT" and rep[1]["state"] == "NO-VERDICT"
    assert rep[1]["skipped"]


def test_timed_out_baseline_gives_dependents_no_verdict(tmp_path):
    slow = f"{PY} -c 'import time; time.sleep(30)'"
    rc, rep = run(tmp_path, [f"@baseline=a {slow}", f"@after=a {sh(0)}"], timeout=1)
    assert rc == 1
    assert rep[0]["state"] == "NO-VERDICT" and rep[0]["rc"] is None
    assert rep[1]["state"] == "NO-VERDICT" and rep[1]["skipped"]


def test_unlaunchable_baseline_gives_dependents_no_verdict(tmp_path):
    rc, rep = run(tmp_path, ["@baseline=a /nonexistent/definitely-not-here", f"@after=a {sh(0)}"])
    assert rc == 1 and rep[1]["state"] == "NO-VERDICT" and rep[1]["skipped"]


def test_stale_pass_is_replaced_by_the_failed_run(tmp_path):
    (tmp_path / "controls.json").write_text(json.dumps(
        [{"cmd": f"{sh(0)} # SPI_ADDR7", "state": "PASS", "rc": 0, "out": "", "secs": 1.0}]))
    rc, rep = run(tmp_path, [f"@baseline=spi {sh(1)}", f"@after=spi {sh(0)} # SPI_ADDR7"])
    assert rc == 1
    assert all(r["state"] != "PASS" for r in rep)


def test_report_is_pending_before_anything_finishes(tmp_path):
    """A killed run must not leave the previous PASS standing."""
    seen = []
    report = tmp_path / "controls.json"
    real = run_controls.run_one

    def spy(cmd, timeout=None, **kw):
        seen.append([r["state"] for r in json.loads(report.read_text())])
        return real(cmd, timeout, **kw)
    report.write_text("[]")
    run_controls.run_one = spy
    try:
        run_controls.main(["--json", str(report), "--serial", sh(0)])
    finally:
        run_controls.run_one = real
    assert seen[0] == ["PENDING"]


def test_plain_commands_behave_as_run_all(tmp_path):
    rc, rep = run(tmp_path, [sh(0), sh(1)])
    assert rc == 1 and [r["state"] for r in rep] == ["PASS", "FAIL"]


@pytest.mark.parametrize("cmds", [
    [f"@after=x {sh(0)}"],                                              # no baseline
    [f"@baseline=x {sh(0)}"],                                           # nothing depends on it
    [f"@baseline=x {sh(0)}", f"@baseline=x {sh(0)} #2", f"@after=x {sh(0)} #3"],  # duplicate
    [f"@baseline=x {sh(0)} --inject FOO", f"@after=x {sh(0)} #m"],      # baseline is a mutant
    [f"@baseline=x {sh(0)} --expect-fail", f"@after=x {sh(0)} #m"],
    [f"@baseline=x {sh(0)} --define INJECT_BUG_Y", f"@after=x {sh(0)} #m"],
    [f"@baseline=x {sh(0)} --outdir o", f"@after=x {sh(0)} --outdir o"],  # shared outdir
    [f"@baseline=x {sh(0)}", f"@after=x {sh(0)}"],                      # identical command
    ["@bogus=x true"],
])
def test_malformed_specs_are_refused_and_nothing_runs(tmp_path, cmds):
    rc, rep = run(tmp_path, cmds)
    assert rc == 2 and rep is None


# -- wiring: the live `controls` recipe -------------------------------------

def _entries():
    recipe = makefile_recipe("controls")
    out = []
    for ln in recipe.split("\n"):
        m = re.search(r'"(.*?)"', ln)
        if m:
            out.append(m.group(1))
    return out


def test_controls_runs_through_the_baseline_runner():
    recipe = makefile_recipe("controls")
    assert "tools/run_controls.py" in recipe and "$(RUN)" not in recipe


def test_live_recipe_parses_and_validates():
    specs = run_controls.parse(_entries())
    run_controls.validate(specs)
    base = [s for s in specs if s["role"] == "baseline"]
    mut = [s for s in specs if s["role"] == "mutant"]
    assert len(base) == 16 and len(mut) == 48


VERIFIERS = ("verify_voice.py", "verify_ctl.py", "verify_synth_top.py", "verify_drums.py",
             "verify_fixture.py", "verify_rate_conv_2x.py", "f1_rtl_filter_path.py")
FLAGS = ("--inject", "--expect-fail", "--expect-mismatch", "--wrong", "--inject-clamp", "--define")


def test_every_flagged_rtl_control_is_behind_a_baseline():
    """No bare mutant: any RTL verifier line carrying an injection/expect flag
    must be `@after=`. The scoped voice control and I2S are deliberately exempt
    (their own pinned signature / channel check; see the Makefile comment)."""
    exempt = ("verify_voice_scoped.py", "verify_i2s_tx.py")
    bare = []
    for e in _entries():
        if e.startswith("@") or any(x in e for x in exempt):
            continue
        if any(v in e for v in VERIFIERS) and any(f in e.split() for f in FLAGS):
            bare.append(e)
    assert not bare, f"mutants with no clean baseline in make controls: {bare}"


_INJECTION_FLAGS = {"--inject", "--expect-fail", "--expect-mismatch", "--wrong",
                    "--inject-clamp", "--define"}
_NON_CONFIG = {"--outdir"}
# Flags a mutant may carry that its baseline lacks because they SELECT the
# defect under test rather than describe the scenario. `--link dr7rev1` is the
# only live one (verify_ctl's reversed-link mutant). Anything else a mutant adds
# (e.g. `--osc2x`) is a different configuration and must be refused.
_MUTANT_ONLY_CONFIG = {"--link"}


def _config_pairs(cmd: str) -> tuple[str, set[tuple[str, str | None]]]:
    """(verifier script, {(flag, value-or-None)}) for the configuration flags of
    a verifier command: every `--flag [value]` except outdir and the injection
    flags. Values are kept, so `--set quick` != `--set full`."""
    toks = shlex.split(cmd)
    script = next(t for t in toks if t.endswith(".py"))
    pairs: set[tuple[str, str | None]] = set()
    i = 0
    while i < len(toks):
        t = toks[i]
        if t.startswith("--"):
            val = toks[i + 1] if i + 1 < len(toks) and not toks[i + 1].startswith("--") else None
            if t in _INJECTION_FLAGS:
                # an injection flag's value (the bug name) is not configuration
                i += 2 if val is not None else 1
                continue
            if t not in _NON_CONFIG:
                pairs.add((t, val))
            i += 2 if val is not None else 1
            continue
        i += 1
    return script, pairs


def _mutant_matches_baseline(base_cmd: str, mut_cmd: str) -> bool:
    """Same verifier and the same configuration, values included: every
    (flag, value) the baseline names appears in the mutant, and the mutant adds
    only defect-selecting flags (`_MUTANT_ONLY_CONFIG`). A mutant adding
    `--osc2x` its baseline lacks, dropping a baseline flag, or changing a value
    is a different scenario."""
    bs, bp = _config_pairs(base_cmd)
    ms, mp = _config_pairs(mut_cmd)
    return bs == ms and bp <= mp and all(f in _MUTANT_ONLY_CONFIG for f, _ in mp - bp)


def test_a_baseline_is_the_clean_config_of_its_mutants():
    """Each group's mutants share the baseline's verifier and its EXACT
    configuration (flags and flag values, both directions)."""
    specs = run_controls.parse(_entries())
    base = {s["group"]: s for s in specs if s["role"] == "baseline"}
    for s in specs:
        if s["role"] != "mutant":
            continue
        assert _mutant_matches_baseline(base[s["group"]]["cmd"], s["cmd"]), (
            s["group"], base[s["group"]]["cmd"], s["cmd"])


@pytest.mark.parametrize("base,mut", [
    # flag VALUE differs: quick/gate baseline credits a quick/default mutant
    ("verify_voice.py --set quick --only gate --outdir b",
     "verify_voice.py --set quick --only default --inject X --expect-fail --outdir m"),
    ("verify_voice.py --set quick --only gate --outdir b",
     "verify_voice.py --set full --only gate --inject X --expect-fail --outdir m"),
    # empty baseline config must NOT vacuously accept a configured mutant
    ("verify_synth_top.py --outdir b",
     "verify_synth_top.py --osc2x --inject X --expect-fail --outdir m"),
    # mutant drops a baseline config flag
    ("verify_synth_top.py --osc2x --outdir b",
     "verify_synth_top.py --inject X --expect-fail --outdir m"),
    # different verifier
    ("verify_voice.py --set quick --outdir b",
     "verify_ctl.py --set quick --inject X --expect-fail --outdir m"),
])
def test_the_baseline_config_guard_rejects_a_mismatched_mutant(base, mut):
    """Injected-bug control for the guard above (verification-rules rule 8)."""
    assert not _mutant_matches_baseline(base, mut)


def test_the_baseline_config_guard_accepts_a_matching_mutant():
    assert _mutant_matches_baseline(
        "verify_synth_top.py --osc2x --outdir b",
        "verify_synth_top.py --osc2x --inject X --expect-fail --outdir m")
    assert _mutant_matches_baseline(
        "verify_ctl.py --outdir b",
        "verify_ctl.py --link dr7rev1 --expect-fail --outdir m")

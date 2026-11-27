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
import shutil
import sys
from pathlib import Path

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


@pytest.mark.parametrize("cmd", ["   ", "# only a comment", "@after=a # disabled"])
def test_empty_shell_command_cannot_supply_a_control(tmp_path, cmd, capsys):
    cmds = [f"@baseline=a {sh(0)}", cmd] if cmd.startswith("@after") else [cmd]
    code, report = run(tmp_path, cmds)
    assert code == 2
    assert "empty command" in capsys.readouterr().out
    assert report is None


def test_plain_commands_behave_as_run_all(tmp_path):
    rc, rep = run(tmp_path, [sh(0), sh(1)])
    assert rc == 1 and [r["state"] for r in rep] == ["PASS", "FAIL"]


@pytest.mark.parametrize("cmd", ["\\\n# disabled", "CONTROL_CASE=",
                                 "A='value with spaces'; B=x # no control", "> /dev/null",
                                 "${OPTIONAL_CHECK:-}", "<<'END'\nignored\nEND"])
@pytest.mark.parametrize("role", ["plain", "baseline", "mutant"])
def test_shell_syntax_without_a_command_is_refused(tmp_path, capsys, monkeypatch, cmd, role):
    monkeypatch.delenv("OPTIONAL_CHECK", raising=False)
    commands = {"plain": [cmd], "baseline": [f"@baseline=a {cmd}", "@after=a true"],
                "mutant": ["@baseline=a exit 0", f"@after=a {cmd}"]}
    rc, report = run(tmp_path, commands[role])
    assert rc == 2 and report is None
    assert f"empty command for {role}" in capsys.readouterr().out


def test_shell_assignment_and_continuation_can_prefix_a_real_command(tmp_path):
    cmd = "# comment ending in a backslash \\\nCONTROL_CASE='a # b' \\\nsh -c 'test \"$CONTROL_CASE\" = \"a # b\"'"
    rc, report = run(tmp_path, [cmd])
    assert rc == 0 and report[0]["state"] == "PASS"


def test_assignment_substitution_runs_its_check_and_preserves_its_status(tmp_path):
    marker = tmp_path / "checked"
    cmd = f'RESULT="$(touch {shlex.quote(str(marker))} && test 1 = 0)"'
    rc, report = run(tmp_path, ["@baseline=g exit 0", f"@after=g {cmd}"])
    assert marker.exists()
    assert rc == 1 and report[1]["state"] == "FAIL" and report[1]["rc"] == 1


@pytest.mark.parametrize("cmd", [
    "2>/dev/null", "check() { touch unused; }", "for i in; do true; done",
    'RESULT="$(${OPTIONAL_CHECK:-})"', "RESULT=${OPTIONAL_CHECK:- no command}",
    "RESULT='$(test 1 = 0)'",
])
def test_shell_declarations_and_data_are_not_invocations(tmp_path, monkeypatch, cmd):
    monkeypatch.delenv("OPTIONAL_CHECK", raising=False)
    assert run(tmp_path, [cmd]) == (2, None)


def test_a_named_command_can_read_a_heredoc(tmp_path):
    rc, report = run(tmp_path, ["cat <<'END'\ncontrol data\nEND"])
    assert rc == 0 and report[0]["out"].strip() == "control data"


@pytest.mark.parametrize("cmd", ["# disabled", "@bogus=x true"])
def test_a_refusal_invalidates_an_existing_report(tmp_path, cmd):
    assert run(tmp_path, ["true"])[1][0]["state"] == "PASS"
    rc, report = run(tmp_path, [cmd])
    assert rc == 2 and report is None


def test_no_control_arguments_invalidate_an_existing_report(tmp_path):
    assert run(tmp_path, ["true"])[1][0]["state"] == "PASS"
    assert run(tmp_path, []) == (2, None)


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
            out.append(m.group(1).replace("$(PY)", PY))  # make expands PY before the CLI sees it
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


# -- close-out: the mixed-baseline run with REAL verifiers, not stand-ins ------

ROOT = Path(run_controls.__file__).resolve().parents[1]
CTL = f"{PY} {shlex.quote(str(ROOT / 'rtl-sketch' / 'verify_ctl.py'))}"

# The green half is the shipped pair: the clean rev2 link, then the SPI_ADDR7
# mutant, properly credited only because its baseline passed. The red half's
# BASELINE is `--link dr7rev1` with no --expect-fail: the standing
# negative-control receiver, a real bench that is genuinely red before any
# injection flag (106 of 209 addresses moved, measured 2026-11-24) -- the
# #1126 defect shape. Its dependent is the shipped rev1 mutant, which exits 0
# ("CAUGHT") when run bare and must get NO verdict behind a failed baseline.
# The report is preseeded with a stale PASS for that mutant: a red run must
# replace it, not leave it standing.
def _have_sim_tools() -> bool:
    """The verifier's own discovery (PATH, then $OSS_CAD_SUITE/bin) for BOTH
    tools: verify_ctl refuses unless iverilog AND vvp resolve that way."""
    sys.path.insert(0, str(ROOT / "rtl-sketch"))
    from verify_ladder import tool
    return bool(tool("iverilog") and tool("vvp"))


@pytest.mark.skipif(not _have_sim_tools(),
                    reason="iverilog/vvp not discoverable by the verifier: the real bench cannot run")
def test_mixed_baselines_with_a_real_always_red_verifier(tmp_path):
    report = tmp_path / "controls.json"
    report.write_text(json.dumps(
        [{"cmd": f"{CTL} --link dr7rev1 --expect-fail --outdir {tmp_path}/m",
          "state": "PASS", "rc": 0, "out": "stale", "secs": 1.0}]))
    rc = run_controls.main([
        "--json", str(report), "--timeout", "300",
        f"@baseline=ctl-green {CTL} --outdir {tmp_path}/clean",
        f"@after=ctl-green {CTL} --inject SPI_ADDR7 --expect-fail --outdir {tmp_path}/addr7",
        f"@baseline=ctl-rev1 {CTL} --link dr7rev1 --outdir {tmp_path}/rev1-clean",
        f"@after=ctl-rev1 {CTL} --link dr7rev1 --expect-fail --outdir {tmp_path}/rev1-mut"])
    rep = json.loads(report.read_text())
    assert rc == 1, "the red baseline must fail the whole invocation"
    assert rep[0]["state"] == "PASS" and rep[0]["role"] == "baseline"
    assert rep[1]["state"] == "PASS" and rep[1]["baseline"] == "ctl-green"
    assert rep[2]["state"] == "FAIL" and rep[2]["role"] == "baseline"
    # The positive population the evidence rests on, not just the outcomes: the
    # SPI_ADDR7 mutant must be CAUGHT *because the address property moved*, and
    # the red baseline must be red *because the addresses are wrong*. A run that
    # exits PASS/FAIL for any other reason (a flipped datum, a traceback)
    # satisfies the four outcomes above and none of this.
    m = re.search(r"MOVED\s+address\s+(\d+) of (\d+)", rep[1]["out"])
    assert m and int(m.group(1)) > 0 and int(m.group(2)) > 0, rep[1]["out"]
    m = re.search(r"wrong address (\d+),", rep[2]["out"])
    assert m and int(m.group(1)) > 0, rep[2]["out"]
    assert rep[3]["state"] == "NO-VERDICT" and rep[3]["skipped"]
    assert "ctl-rev1" in rep[3]["out"]
    assert "--link dr7rev1 --expect-fail" in rep[3]["cmd"], "skipped, still identifiable"
    assert not any(r["state"] == "PASS" and "rev1" in r["cmd"] for r in rep), \
        "the preseeded PASS must not survive the failed run"

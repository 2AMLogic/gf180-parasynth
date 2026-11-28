"""A run whose bench fails is REFUSED, and never leaves an earlier PASS on disk (#1662).

`fpga/verify_sd_dac.py` promises that a run which cannot measure is REFUSED (exit
2), never reported. Before #1662 that promise stopped at the estimator check: a
bench that failed -- `run_bench()` raising `RuntimeError`, an `iverilog` compile
that failed, a `vvp` run that hit its timeout -- left `main()` as a traceback at
exit 1 and wrote no `verification.json` for that run. Whatever an earlier run had
left in `--outdir` stayed there, and `fpga/build_arty_sd.py` reads that file by
default and builds a demo image over a PASS.

No `iverilog` or `vvp` runs here. `stimuli`, `check_estimator`, `compile_bench`
and `run_bench` are replaced, so each test runs the stretch of `main()` that was
broken -- from the estimator check to the verdict -- without a simulator. The
mocked streams are the tool's own ideal output (`reference()`) plus one known
defect each: +1 every 4096 cycles, which every bound admits (153 dB and above,
silence -157.9 dBFS); a lag of one cycle (65.8 dB on sine-6, next to the 65.7
the tool's docstring records for a one-cycle misalignment); and a DC offset of
one unit (-105.3 dBFS: red for silence and green for every signal case, the
SD_DC_BIAS control's own shape). What the benches measure from the RTL is the
tool's own business, under `make verify`.

What the shipped `run_bench` and `compile_bench` raise is asserted here, against
a replaced `subprocess.run`: the handler catches by class, so a bench that
raised anything else would be an execution error, not a refusal, and every
other test in this file replaces those two functions.

Started red, on `origin/main` at 5dc0e75d, the tool as it was before the fix:
18 failed, 6 passed. Every refusal test died with its planted failure escaping
`main()` or found the earlier PASS still on disk, the CLI tests exited 1 with a
traceback, and the killed run left the PASS where it was. The six that passed
are the unchanged behaviour -- the clean run, a disagreeing measurement, a
missing tool, the shipped compile_bench -- and pass before and after by design.
"""
import json
import re
import select
import signal
import subprocess
import sys
from types import SimpleNamespace

import numpy as np
import pytest

import build_arty_sd as sd
import verify_sd_dac as vsd

ROOT = vsd.ROOT
TOOL = ROOT / "fpga" / "verify_sd_dac.py"
INCOMPLETE = "run did not complete"       # what the record says until there is a verdict
BENCH, COMPILE = "run_bench", "compile_bench"

t = np.arange(512)


def sine(db, peak, hz) -> np.ndarray:
    return np.round(10 ** (-db / 20) * peak * np.sin(2 * np.pi * t * hz / 2048)).astype(np.int64)


STIM = {"sine-6": sine(6, 32768, 43), "held": sine(6, 16384, 110),
        "square": np.where((t // 24) % 2, 32767, -32768).astype(np.int64),
        "silence": np.zeros(128, dtype=np.int64)}

#: where the failure is raised, the failure as source (also given to the CLI
#: driver below, since a CalledProcessError's repr is not its constructor), and
#: the text a reader must be given
FAILED = [
    pytest.param(BENCH, 'RuntimeError("bench failed: INJECTED-BENCH-FAILURE")',
                 "INJECTED-BENCH-FAILURE", id="RuntimeError"),
    pytest.param(COMPILE, 'subprocess.CalledProcessError(2, ["iverilog", "-o", "tb_clean.vvp"], '
                 'stderr="iverilog: INJECTED-COMPILE-FAILURE near \'x\'")',
                 "INJECTED-COMPILE-FAILURE", id="CalledProcessError"),
    pytest.param(BENCH, 'subprocess.TimeoutExpired(["vvp", "tb_clean_sine-6.vvp"], 1800)',
                 "timed out after 1800 seconds", id="TimeoutExpired"),
]


def failure(code: str) -> BaseException:
    return eval(code, {"subprocess": subprocess})


def reference(samples, lag=vsd.LAG_CYCLES) -> np.ndarray:
    """The tool's ideal output for `samples`, one unit high every 4096 cycles."""
    ideal = vsd.reference(samples, (len(samples) + 2) * vsd.OSR, lag)
    ideal[::4096] += 1
    return ideal


def stale_pass() -> dict:
    """A verification.json that fpga/build_arty_sd.py accepts: an earlier run's PASS."""
    return {"state": "PASS",
            "controls": {name: {"caught": True} for name in vsd.CONTROLS},
            "source_sha256": {str(p.relative_to(ROOT)): vsd.sha(p) for p in vsd.SOURCES},
            "clean": {case: {"snr_db": 100, "noise_dbfs": -120, "exact_zero_error": False,
                             "pass": True} for case in vsd.BOUNDS}}


class Bench:
    """The tool's collaborators, replaced. `streams[build]` makes the stream a
    bench of that build returns (None is the clean build); `fail_bench` and
    `fail_compile` plant failures; `calls` is what was asked of the benches and
    `seen` is what verification.json said each time one was entered."""

    def __init__(self, outdir):
        self.outdir, self.record = outdir, outdir / "verification.json"
        self.streams = {None: reference}
        for inject in vsd.CONTROLS:
            self.streams[inject] = lambda s: reference(s, vsd.LAG_CYCLES + 1)
        self.streams["SD_DC_BIAS"] = lambda s: reference(s) + 1
        self.fail_bench, self.fail_compile = {}, {}
        self.calls, self.seen = [], []

    def install(self, monkeypatch):
        for name in ("stimuli", "check_estimator", "compile_bench", "run_bench"):
            monkeypatch.setattr(vsd, name, getattr(self, name))

    def on_disk(self):
        return json.loads(self.record.read_text()) if self.record.exists() else None

    def stimuli(self):
        self.seen.append(("stimuli", self.on_disk()))
        return STIM

    def check_estimator(self, samples):
        return {"known_inband_db": 70.0}

    def compile_bench(self, work, inject):
        self.calls.append((COMPILE, inject))
        if inject in self.fail_compile:
            raise self.fail_compile[inject]
        return work / f"tb_{inject or 'clean'}.vvp"

    def run_bench(self, exe, work, case, samples):
        build = exe.stem.removeprefix("tb_")
        build = None if build == "clean" else build
        self.calls.append((BENCH, build, case))
        self.seen.append((BENCH, self.on_disk()))
        if (build, case) in self.fail_bench:
            raise self.fail_bench[(build, case)]
        return self.streams[build](samples)

    def run(self):
        return vsd.main(["--outdir", str(self.outdir)])


@pytest.fixture
def bench(tmp_path, monkeypatch):
    b = Bench(tmp_path / "out")
    b.install(monkeypatch)
    return b


def seed_an_earlier_pass(bench):
    bench.record.parent.mkdir(parents=True)
    bench.record.write_text(json.dumps(stale_pass()))
    sd.validate_sd_verification(bench.record)        # left alone, this builds an image


def refused(bench, capsys, named) -> int:
    try:
        code = bench.run()
    except Exception as exc:
        pytest.fail(f"main() let the failure escape as a {type(exc).__name__} instead of refusing: "
                    "no exit code, no record")
    assert code == 2
    out = capsys.readouterr().out
    assert out.startswith("verify_sd_dac: REFUSED -- ") and named in out, out
    record = json.loads(bench.record.read_text())
    assert record["state"] == "REFUSED" and named in record["reason"], record
    with pytest.raises(ValueError, match="is not a PASS"):
        sd.validate_sd_verification(bench.record)
    return code


def test_the_mocked_defects_move_what_the_clean_run_tests_assume():
    assert list(STIM) == list(vsd.BOUNDS)
    for case, samples in STIM.items():
        good, biased, late = (reference(samples), reference(samples) + 1,
                              reference(samples, vsd.LAG_CYCLES + 1))
        assert vsd.verdict(case, vsd.measure(good, samples)), case
        assert vsd.verdict(case, vsd.measure(biased, samples)) == (case != "silence"), case
        assert vsd.verdict(case, vsd.measure(late, samples)) == (case == "silence"), case
    assert vsd.lag_search(reference(STIM["sine-6"]), STIM["sine-6"]) == vsd.LAG_CYCLES


def with_fake_subprocess(monkeypatch, reply):
    """`subprocess` as `run_bench` and `compile_bench` see it: `run` returns
    `reply` and honours check= as the real one does. Returns what each call asked."""
    asked = []

    def run(cmd, **kw):
        asked.append((cmd, kw))
        if kw.get("check") and reply.returncode:
            raise subprocess.CalledProcessError(reply.returncode, cmd, reply.stdout, reply.stderr)
        return reply

    monkeypatch.setattr(vsd, "subprocess", SimpleNamespace(
        run=run, CalledProcessError=subprocess.CalledProcessError,
        TimeoutExpired=subprocess.TimeoutExpired))
    return asked


BENCH_BITS = (len(STIM["sine-6"]) + 2) * vsd.OSR     # what tb_sd_dac writes for sine-6


@pytest.mark.parametrize("reply, bits, said", [
    pytest.param((1, "TB_SD_DAC DONE\n", "vvp: INJECTED-VVP-FAILURE"), None,
                 ("failed", "INJECTED-VVP-FAILURE"), id="vvp exits non-zero"),
    pytest.param((0, "", ""), None, ("failed",), id="no DONE marker"),
    pytest.param((0, "TB_SD_DAC DONE\n", ""), b"0" * (BENCH_BITS - 1) + b"x",
                 ("frames of 0/1",), id="a byte that is not 0 or 1"),
    pytest.param((0, "TB_SD_DAC DONE\n", ""), b"0" * 10, ("frames of 0/1",), id="stream too short"),
])
def test_the_shipped_run_bench_raises_the_runtime_error_the_handler_names(
        monkeypatch, tmp_path, reply, bits, said):
    (tmp_path / "tb_clean_sine-6.bits").write_bytes(b"0" * BENCH_BITS if bits is None else bits)
    asked = with_fake_subprocess(monkeypatch, subprocess.CompletedProcess("vvp", *reply))
    with pytest.raises(RuntimeError) as exc:
        vsd.run_bench(tmp_path / "tb_clean.vvp", tmp_path, "sine-6", STIM["sine-6"])
    assert type(exc.value) is RuntimeError, repr(exc.value)
    assert "tb_clean/sine-6" in str(exc.value), str(exc.value)
    assert all(s in str(exc.value) for s in said), str(exc.value)
    assert asked[0][1]["timeout"] == 1800     # so a hang is the TimeoutExpired main() names


def test_the_shipped_compile_bench_raises_the_called_process_error_the_handler_names(
        monkeypatch, tmp_path):
    reply = subprocess.CompletedProcess("iverilog", 2, "", "iverilog: INJECTED-COMPILE-FAILURE")
    asked = with_fake_subprocess(monkeypatch, reply)
    with pytest.raises(subprocess.CalledProcessError) as exc:
        vsd.compile_bench(tmp_path, None)
    assert (exc.value.stderr, exc.value.stdout) == (reply.stderr, reply.stdout)
    cmd, kw = asked[0]
    assert (kw["check"], kw["capture_output"], kw["text"]) == (True, True, True)
    assert cmd[-4:] == [str(p) for p in vsd.SOURCES]


def test_a_clean_run_passes_and_its_record_still_reads_as_the_demo_build_requires(bench, capsys):
    assert bench.run() == 0
    lines = capsys.readouterr().out.splitlines()
    for ln, case in zip(lines, vsd.BOUNDS):
        key, op, bound = vsd.BOUNDS[case]
        assert re.fullmatch(rf"PASS {case}\s+{key} .+ \(bound {op} {bound}\)", ln), ln
    assert all(ln.startswith(f"CAUGHT {inject}: ") for ln, inject in zip(lines[4:8], vsd.CONTROLS))
    assert lines[8:] == ["verify_sd_dac: PASS"]
    record = json.loads(bench.record.read_text())
    assert list(record) == ["state", "bounds", "lag_cycles_expected", "source_sha256", "estimator",
                            "lag_cycles_measured", "clean", "controls"]
    assert record["state"] == "PASS" and "reason" not in record
    assert record["lag_cycles_measured"] == vsd.LAG_CYCLES
    assert sd.validate_sd_verification(bench.record)["record_sha256"] == vsd.sha(bench.record)


@pytest.mark.parametrize("what", ["missed control", "failed clean case"])
def test_a_disagreeing_measurement_is_a_result_not_a_refusal(bench, capsys, what):
    seed_an_earlier_pass(bench)
    if what == "missed control":
        bench.streams["I2S_RX_SHIFT"] = reference            # the injection moves nothing any more
    else:
        bench.streams[None] = lambda s: reference(s) + 1     # silence's bound is missed
    assert bench.run() == 1
    lines = capsys.readouterr().out.splitlines()
    assert lines[-1] == "verify_sd_dac: FAIL"
    assert any(ln.startswith("MISSED I2S_RX_SHIFT: ") for ln in lines) == (what == "missed control")
    assert any(ln.startswith("FAIL silence ") for ln in lines) == (what == "failed clean case")
    record = json.loads(bench.record.read_text())
    assert record["state"] == "FAIL" and "reason" not in record
    with pytest.raises(ValueError, match="is not a PASS"):
        sd.validate_sd_verification(bench.record)


@pytest.mark.parametrize("seed", ["stale", "fresh"])
@pytest.mark.parametrize("where, code, named", FAILED)
def test_a_failed_bench_is_refused_and_replaces_the_earlier_pass(
        bench, capsys, seed, where, code, named):
    if seed == "stale":
        seed_an_earlier_pass(bench)
    if where == BENCH:
        bench.fail_bench[("I2S_RX_SHIFT", "square")] = failure(code)   # the 15th of 20 jobs
    else:
        bench.fail_compile["SD_NO_SAT"] = failure(code)               # the 3rd of 5 builds
    refused(bench, capsys, named)
    # the planted failure was raised where it was planted, through pool.map
    if where == BENCH:
        assert (BENCH, "I2S_RX_SHIFT", "square") in bench.calls
    else:
        assert (COMPILE, "SD_NO_SAT") in bench.calls and BENCH not in [c[0] for c in bench.calls]


def test_the_refusal_carries_what_the_compiler_said(bench):
    exc = subprocess.CalledProcessError(2, ["iverilog"],
                                        stderr="iverilog: INJECTED-COMPILE-FAILURE near 'x'")
    # str() names the command, not its error
    assert "INJECTED-COMPILE-FAILURE" not in str(exc)
    bench.fail_compile[None] = exc
    assert bench.run() == 2
    assert "INJECTED-COMPILE-FAILURE" in json.loads(bench.record.read_text())["reason"]


@pytest.mark.parametrize("how", ["unnamed exception", "missing source"])
def test_what_the_handler_does_not_name_surfaces_and_leaves_the_record_refused(
        bench, monkeypatch, how):
    seed_an_earlier_pass(bench)
    if how == "unnamed exception":
        bench.fail_bench[(None, "held")] = OSError("vvp: INJECTED-UNNAMED-FAILURE")
    else:
        monkeypatch.setattr(vsd, "SOURCES", [*vsd.SOURCES, ROOT / "fpga" / "rtl" / "absent.v"])
    with pytest.raises(OSError if how == "unnamed exception" else FileNotFoundError):
        bench.run()                     # an execution error is never relabelled as a refusal
    record = json.loads(bench.record.read_text())
    assert (record["state"], record["reason"]) == ("REFUSED", INCOMPLETE), record


def test_the_record_on_disk_is_a_refusal_for_the_whole_run(bench):
    seed_an_earlier_pass(bench)
    assert bench.run() == 0
    benches = (len(vsd.CONTROLS) + 1) * len(STIM)
    assert [stage for stage, _ in bench.seen] == ["stimuli"] + [BENCH] * benches
    for _, seen in bench.seen:
        assert (seen["state"], seen["reason"]) == ("REFUSED", INCOMPLETE), seen
    final = json.loads(bench.record.read_text())
    assert final["state"] == "PASS" and "reason" not in final


def test_a_missing_tool_is_refused_before_any_bench_and_replaces_the_earlier_pass(
        bench, capsys, monkeypatch):
    seed_an_earlier_pass(bench)
    monkeypatch.setattr(vsd.shutil, "which", lambda tool: None)
    assert bench.run() == 2
    assert capsys.readouterr().out == "verify_sd_dac: REFUSED -- iverilog not on PATH\n"
    record = json.loads(bench.record.read_text())
    assert (record["state"], record["reason"]) == ("REFUSED", "iverilog not on PATH")
    assert bench.calls == []
    with pytest.raises(ValueError, match="is not a PASS"):
        sd.validate_sd_verification(bench.record)


#: the shipped entry point with the benches replaced: the tool is loaded from
#: disk and ends in `raise SystemExit(main())`, as its __main__ block does
#: (@@EXC@@ and @@WHERE@@ are substituted, so no brace in here needs escaping)
DRIVER = """
import importlib.util, subprocess, sys
import numpy as np
spec = importlib.util.spec_from_file_location("verify_sd_dac", sys.argv[1])
vsd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vsd)
t = np.arange(256)
vsd.stimuli = lambda: {"sine-6": t * 0, "held": t * 0, "square": t * 0, "silence": t[:64] * 0}
vsd.check_estimator = lambda samples: {"known_inband_db": 70.0}
vsd.compile_bench = lambda work, inject: work / f"tb_{inject or 'clean'}.vvp"
"""

FAILING = DRIVER + """
def failed(*args):
    raise @@EXC@@
setattr(vsd, @@WHERE@@, failed)
raise SystemExit(vsd.main(["--outdir", sys.argv[2]]))
"""

HANGING = DRIVER + """
import time
def bench(*args):
    print("bench entered", flush=True)
    time.sleep(3600)
vsd.run_bench = bench
raise SystemExit(vsd.main(["--outdir", sys.argv[2]]))
"""


@pytest.mark.parametrize("where, code, named", FAILED)
def test_the_cli_exits_2_without_a_traceback(tmp_path, where, code, named):
    record = tmp_path / "verification.json"
    record.write_text(json.dumps(stale_pass()))
    driver = FAILING.replace("@@EXC@@", code).replace("@@WHERE@@", repr(where))
    p = subprocess.run([sys.executable, "-c", driver, str(TOOL), str(tmp_path)],
                       cwd=ROOT, capture_output=True, text=True, check=False)
    assert p.returncode == 2, p.stderr
    assert "Traceback" not in p.stderr
    assert p.stdout.startswith("verify_sd_dac: REFUSED -- ") and named in p.stdout, p.stdout
    assert json.loads(record.read_text())["state"] == "REFUSED"


def test_a_killed_run_leaves_the_refusal_not_the_earlier_pass(tmp_path):
    """The stand-in is on disk before the first bench is entered, so a run that
    is killed there -- no handler runs, nothing is written -- is still not an
    earlier PASS."""
    record = tmp_path / "verification.json"
    record.write_text(json.dumps(stale_pass()))
    p = subprocess.Popen([sys.executable, "-c", HANGING, str(TOOL), str(tmp_path)],
                         cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        ready = select.select([p.stdout], [], [], 120)[0]
        first = p.stdout.readline().strip() if ready else ""
    finally:
        p.kill()                 # first, and in every path: stderr is read only once it is gone
        p.wait()
    err = p.stderr.read()
    assert first == "bench entered", (f"the run never reached a bench; exit {p.returncode}; "
                                      f"stderr:\n{err}")
    assert p.returncode == -signal.SIGKILL, p.returncode
    on_disk = json.loads(record.read_text())
    assert (on_disk["state"], on_disk["reason"]) == ("REFUSED", INCOMPLETE), on_disk

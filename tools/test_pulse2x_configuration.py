"""Exercise the FPGA configuration parser without synthesizing another netlist."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def config(*settings):
    return subprocess.run(["make", "-s", "--no-print-directory", "-f", "fpga/Makefile",
                           "-f", "-", "print-pulse-config", *settings], cwd=ROOT,
                          input="print-pulse-config: ; @echo $(RTL_DEFS)\n",
                          capture_output=True, text=True)


def test_candidate_selects_all_three_paths_explicitly():
    result = config("OSC2X=1", "FILTER2X=1", "PULSE2X=1")
    assert result.returncode == 0, result.stderr
    assert set(result.stdout.split()) == {"-DVOICE_OSC_2X", "-DVOICE_FILTER_2X", "-DVOICE_PULSE_2X"}


def test_pulse_requires_oscillator_chain_and_is_not_implicitly_enabled():
    refused = config("OSC2X=0", "PULSE2X=1")
    assert refused.returncode != 0 and "requires OSC2X=1" in refused.stderr
    baseline = config("OSC2X=1", "FILTER2X=1")
    assert baseline.returncode == 0
    assert "-DVOICE_PULSE_2X" not in baseline.stdout

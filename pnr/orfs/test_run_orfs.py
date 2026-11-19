"""Exercise the launcher's host-to-container argument boundary with GNU Make."""
import os
import subprocess
from pathlib import Path


RUNNER = Path(__file__).with_name("run-orfs.sh").resolve()


def test_quarter_slot_and_voice_selection_reach_make(tmp_path):
    # Stand in for Docker's image filesystem only. Run the actual inner Bash
    # command and Make so a value split into extra make goals fails here.
    docker = tmp_path / "docker"
    docker.write_text(
        "#!/usr/bin/env python3\n"
        "import os, sys\n"
        "args = sys.argv[sys.argv.index('bash'):]\n"
        "args[2] = args[2].replace('/OpenROAD-flow-scripts/env.sh', '/dev/null')\n"
        "os.execvp(args[0], args)\n"
    )
    docker.chmod(0o755)
    (tmp_path / "Makefile").write_text(
        "place:\n"
        "\t@echo '$(DIE_AREA)|$(CORE_AREA)|$(VERILOG_TOP_PARAMS)|$(MAKECMDGOALS)'\n"
    )
    result = subprocess.run(
        [RUNNER, "synth_top", "DIE_AREA=0 0 1314.88 1317.12",
         "CORE_AREA=10.64 11.76 1304.24 1305.36",
         "VERILOG_TOP_PARAMS=WITH_DRUMS 0", "place"],
        cwd=tmp_path,
        env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}",
             "ORFS_WORK": str(tmp_path / "run")},
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    expected = "0 0 1314.88 1317.12|10.64 11.76 1304.24 1305.36|WITH_DRUMS 0|place"
    assert expected in result.stdout.splitlines()

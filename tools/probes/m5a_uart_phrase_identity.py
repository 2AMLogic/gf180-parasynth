"""Compare the R0 player phrase with the digital bench before UART simulation.

Run with the project's Python dependencies. The frozen R0 command digest
identifies the player stimulus. Each event carries a 48-bit register payload
and a 16-bit due frame. The comparison allows one common start offset.
"""
import hashlib
import json
from itertools import pairwise
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "fpga"))

import verify_uart_bridge as bench


def events(packets):
    return [(int.from_bytes(p[1:3], "little"), p[3:9]) for p in packets
            if p[0] == ord("E")]


def gaps(rows):
    return [later[0] - earlier[0] for earlier, later in pairwise(rows)]


def main():
    # Verify both signs of the comparison. Moving a whole phrase is allowed.
    # Moving only its second event leaves payload identity intact.
    sample = [(10, b"a"), (11, b"b"), (20, b"c")]
    assert gaps(sample) == gaps([(t + 30, p) for t, p in sample])
    assert gaps(sample) != gaps([(10, b"a"), (12, b"b"), (20, b"c")])

    manifest = json.loads((ROOT / "fpga/release/baseline-2025.1.json").read_text())
    pin = manifest["commands"]["phrase-m5a"]
    with tempfile.TemporaryDirectory() as tmp:
        prefix = Path(tmp) / "play"
        subprocess.run([sys.executable, str(ROOT / "fpga/uart_host.py"), "play",
                        "--fixture", "m5a", "--dry-run", "--capture", str(prefix)],
                       stdout=subprocess.DEVNULL, check=True)
        capture = prefix.with_suffix(".cmds").read_bytes()
    if hashlib.sha256(capture).hexdigest() != pin["cmds_sha256"]:
        raise SystemExit("REFUSED: player bytes differ from the R0 phrase-m5a pin")
    player = events([bytes.fromhex(line.split(maxsplit=2)[2])
                     for line in capture.decode().splitlines()])
    items, _ = bench.SCENARIOS["phrase"]()
    _, segments, _ = bench._lay_out(items)
    scripted = events([row.packet for segment in segments for row in segment])
    if len(player) != pin["packets"] or [p for _, p in player] != [p for _, p in scripted]:
        raise SystemExit("REFUSED: unequal or incomplete phrase payloads")
    player_gaps, bench_gaps = gaps(player), gaps(scripted)
    differences = sum(a != b for a, b in zip(player_gaps, bench_gaps))
    print(f"{len(player)} matching event payloads, {differences}/{len(player_gaps)} "
          f"adjacent gaps differ. First gap: player {player_gaps[0]}, "
          f"bench {bench_gaps[0]} (both in frames).")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""fpga/bringup_arty.py -- bring the Arty up to first audio, asserting every
precondition at the point of use.

WHY THIS EXISTS. On 2026-09-28 a bring-up session had a correct design, a
correct bitstream and a correct host, and produced no sound for a long time.
Nothing was broken. Four preconditions were assumed instead of asserted, and
every instrument we had stopped at the FPGA pins, so every instrument said
"fine":

  1. TWO FT2232H devices were on the bus (an Arty and an unrelated
     "Dual RS232-HS"). `openFPGALoader --detect` without `--ftdi-serial` can
     address the wrong one. Four /dev/cu.usbserial-* ports existed.
  2. THE WRONG IMAGE WAS FLASHED. R0 (integrated-baseline) drives
     JA1=BCK, JA2=WSEL, JA3=DIN and *nothing on JA4*. The operator's purple
     PCM5102 plugged straight into JA's top row expects SCK/BCK/DIN/LCK, so it
     never received a word clock. #408's sd-demo image is the one laid out for
     that breakout.
  3. THE WRONG CONTRACT REVISION. `uart_host`'s default `--image release` is
     revision 11; the sd-demo bitstream is revision 14. Notes "delivered"
     with drops 0 while the patch bytes addressed the wrong image.
  4. THE DAC'S CONTROL PINS FLOATED. XSMT (soft mute) has no internal pull-up
     on the PCM5102A; floating reads low and hard-mutes the part while every
     digital check passes. `ARTY.md`'s "leave MU/FM unconnected" describes
     Adafruit 6250's onboard resistors, not a generic breakout (#460).

Each is a state, not a bug, and each is cheap to assert and expensive to
discover. So this script asserts them and REFUSES rather than reporting.

REFUSED (exit 2) is a first-class outcome here, distinct from pass (0) and
fail (1). A tool that answers when it cannot is worse than one that is absent,
because its output looks exactly like data.

WRONG-THEN-RIGHT, from the session that produced this file:
  * "no sound" was attributed to note length (40 ms), then to wiring, then to
    the image, then to the contract revision. Three of those were real and
    none was the last one. The order was wrong; the controls were what kept it
    honest -- `held_note_audible.py` reported a decoded I2S peak of 12,760 LSB
    (floor 1,024) with its --legacy-image control correctly returning 0, which
    is what proved the fault lay past the pins and stopped us rewriting RTL.
  * A frame-counter delta was first read as "the device stopped" (what
    uart_host reports, #459). Measured properly it was 48 kHz exactly. This
    file's `unwrap_forward` REFUSES an ambiguous interval instead of guessing
    its sign, which is the bug #459 describes.

USAGE
    .venv/bin/python fpga/bringup_arty.py --bit <path> --sha256 <hex> \
        [--image r1] [--notes 45,52,57,64] [--no-flash]
"""
from __future__ import annotations

import argparse
import hashlib
import os
import pathlib
import re
import subprocess
import sys
import time

SR = 48000.0                      # audio frames per second
WRAP = 0x10000                    # the device's frame counter is 16-bit
HORIZON = 0x8000                  # past this, a 16-bit delta's sign is a guess


class Refused(Exception):
    """A precondition failed. Distinct from a failure of the thing measured."""


# --------------------------------------------------------------------------
# pure helpers (tested in fpga/test_bringup_arty.py)
# --------------------------------------------------------------------------
def unwrap_forward(samples: list, *, horizon: int = HORIZON) -> int:
    """Total forward progress of a 16-bit counter across `samples`
    [(t, frame16), ...], assuming it only counts up.

    REFUSES an interval whose delta reaches `horizon`, because at that point
    "advanced by horizon" and "went backwards" are the same 16 bits and the
    answer would be a guess. uart_host silently treats it as negative and then
    blames the device's clock (#459)."""
    if len(samples) < 2:
        raise Refused("need at least two samples to measure progress")
    total = 0
    for (t0, f0), (t1, f1) in zip(samples, samples[1:]):
        step = (f1 - f0) & 0xFFFF
        if step >= horizon:
            raise Refused(
                f"interval {t0:.3f}->{t1:.3f}s has a 16-bit delta of {step} "
                f"(>= horizon {horizon}): the counter's direction is not "
                f"recoverable from these samples, so the rate is unknowable. "
                f"Sample faster than {horizon / SR:.3f}s.")
        total += step
    return total


def frame_rate_hz(samples: list) -> float:
    """Measured counter rate. The nominal answer, 48 kHz, is known
    independently of anything this repo models, which is what makes it a
    usable check on the design rather than on our own arithmetic."""
    elapsed = samples[-1][0] - samples[0][0]
    if elapsed <= 0:
        raise Refused("samples are not ordered in time")
    return unwrap_forward(samples) / elapsed


def classify(rc: int, out: str) -> str:
    """What a uart_host invocation actually did. `wrap-guard` is #458: a raw
    ValueError whose occurrence depends on the free-running anchor, so a retry
    at a fresh anchor is the documented workaround."""
    if "inside the wrap guard" in out:
        return "wrap-guard"
    if rc == 0 and "done;" in out:
        return "played"
    if "REFUSED" in out:
        return "refused"
    return "error"


def parse_digilent_serials(ioreg_text: str) -> list:
    """Serial numbers of Digilent USB devices, from `ioreg -p IOUSB -l -w 0`.

    More than one FTDI device on the bus is the normal case on a developer's
    machine, not an exotic one, and an unscoped openFPGALoader may address
    either."""
    serials, pending = [], None
    for line in ioreg_text.splitlines():
        if '"USB Vendor Name" = "Digilent"' in line or \
           '"USB Product Name" = "Digilent USB Device"' in line:
            pending = True
        m = re.search(r'"USB Serial Number" = "([0-9A-Za-z]+)"', line)
        if m and pending:
            serials.append(m.group(1))
            pending = None
    return serials


# --------------------------------------------------------------------------
# preconditions
# --------------------------------------------------------------------------
def require_tools() -> None:
    for tool in ("openFPGALoader",):
        if subprocess.run(["which", tool], capture_output=True).returncode:
            raise Refused(f"{tool} is not on PATH (brew install openfpgaloader)")
    try:
        import serial          # noqa: F401
    except ImportError:
        raise Refused("pyserial is not importable by this interpreter")


def require_bitstream(path: pathlib.Path, want_sha: str) -> str:
    if not path.exists():
        raise Refused(f"no bitstream at {path}")
    got = hashlib.sha256(path.read_bytes()).hexdigest()
    if got != want_sha:
        raise Refused(f"bitstream sha256 {got} != expected {want_sha} -- "
                      f"this is not the image whose evidence you are citing")
    return got


def require_one_board(serial: str = None) -> str:
    """Exactly one candidate, or REFUSE. Picking 'the first one' is how a
    session addresses the wrong board and believes the result."""
    if serial:
        return serial
    ioreg = subprocess.run(["ioreg", "-p", "IOUSB", "-l", "-w", "0"],
                           capture_output=True, text=True).stdout
    found = parse_digilent_serials(ioreg)
    if not found:
        raise Refused("no Digilent USB device on the bus -- is the cable in J10, "
                      "and is it a data cable rather than charge-only?")
    if len(found) > 1:
        raise Refused(f"{len(found)} Digilent devices present ({', '.join(found)}); "
                      f"pass --serial to say which")
    return found[0]


def require_port(serial: str, timeout_s: float = 30.0) -> str:
    """The UART is FT2232H channel B, the port whose name ends in 1."""
    port = f"/dev/cu.usbserial-{serial}1"
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if os.path.exists(port):
            return port
        time.sleep(1.0)
    raise Refused(f"{port} never appeared within {timeout_s:.0f}s")


# --------------------------------------------------------------------------
# steps
# --------------------------------------------------------------------------
def flash(bit: pathlib.Path, serial: str) -> None:
    p = subprocess.run(["openFPGALoader", "-b", "arty_a7_100t",
                        "--ftdi-serial", serial, str(bit)],
                       capture_output=True, text=True, timeout=600)
    out = p.stdout + p.stderr
    if p.returncode != 0:
        print(out.strip()[-500:])
        raise SystemExit(f"FAIL -- openFPGALoader exited {p.returncode}")
    if "done 1" not in out:
        raise Refused("openFPGALoader exited 0 but never reported 'done 1'; "
                      "the FPGA may not be configured")
    print("flash: done 1")


def status_samples(repo: pathlib.Path, port: str, n: int = 6) -> list:
    """Sample the device's own frame counter. This is the design telling us it
    is running; the LEDs and this agree or something is wrong."""
    samples = []
    for _ in range(n):
        p = subprocess.run([sys.executable, "fpga/uart_host.py",
                            "--port", port, "status"],
                           capture_output=True, text=True, timeout=60, cwd=repo)
        m = re.search(r"STATUS frame (\d+)", p.stdout + p.stderr)
        if not m:
            raise Refused("no STATUS reply from the device; the link is down "
                          "(check the bitstream, A9/D10 and the baud)")
        samples.append((time.time(), int(m.group(1))))
    return samples


def play_note(repo: pathlib.Path, port: str, image: str, note: int,
              hold: int, tries: int = 10) -> bool:
    """One held note, retrying past #458's anchor-dependent wrap guard."""
    for t in range(1, tries + 1):
        p = subprocess.run([sys.executable, "fpga/uart_host.py", "--port", port,
                            "--image", image, "run", "--note", str(note),
                            "--hold-frames", str(hold)],
                           capture_output=True, text=True, timeout=180, cwd=repo)
        out = p.stdout + p.stderr
        verdict = classify(p.returncode, out)
        if verdict == "played":
            print(f"  note {note}: played (try {t})")
            return True
        if verdict == "wrap-guard":
            continue
        why = [l for l in out.splitlines() if "REFUSED" in l]
        print(f"  note {note}: {verdict}  {why[0][:120] if why else ''}")
        return False
    print(f"  note {note}: never cleared the wrap guard in {tries} tries (#458)")
    return False


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bit", required=True, type=pathlib.Path)
    ap.add_argument("--sha256", required=True,
                    help="the hash the evidence you cite is bound to")
    ap.add_argument("--serial", default=None)
    ap.add_argument("--image", default="r1",
                    choices=["r1", "release", "tree"],
                    help="patch-image revision; MUST match the bitstream's "
                         "contract revision (r1 = revision 14)")
    ap.add_argument("--notes", default="45,52,57,64")
    ap.add_argument("--hold-frames", type=int, default=24000,
                    help="0.5 s at 48 kHz; large values sit in the wrap guard")
    ap.add_argument("--no-flash", action="store_true")
    ap.add_argument("--repo", type=pathlib.Path,
                    default=pathlib.Path(__file__).resolve().parent.parent)
    a = ap.parse_args(argv)

    try:
        require_tools()
        sha = require_bitstream(a.bit, a.sha256)
        print(f"bitstream: {a.bit.name} sha256 {sha[:12]}... OK")
        serial = require_one_board(a.serial)
        print(f"board: Digilent {serial}")
        port = require_port(serial)
        print(f"port: {port}")

        if not a.no_flash:
            flash(a.bit, serial)
            time.sleep(1.5)

        samples = status_samples(a.repo, port)
        rate = frame_rate_hz(samples)
        print(f"frame counter: {rate:,.0f} Hz measured over "
              f"{samples[-1][0] - samples[0][0]:.2f}s")
        if not 0.98 * SR <= rate <= 1.02 * SR:
            raise Refused(f"frame rate {rate:,.0f} Hz is not within 2% of "
                          f"{SR:,.0f} Hz; the audio clock is wrong")
    except Refused as exc:
        print(f"REFUSED -- {exc}")
        return 2

    notes = [int(x) for x in a.notes.split(",") if x.strip()]
    print(f"playing {len(notes)} notes, {a.hold_frames / SR:.2f}s each, "
          f"--image {a.image}")
    ok = sum(play_note(a.repo, port, a.image, n, a.hold_frames) for n in notes)
    print(f"--- {ok}/{len(notes)} notes delivered ---")
    print("NOTE: delivery is not audibility. The DAC's XSMT must be high and "
          "FMT low (#460); nothing here can see past the FPGA pins.")
    return 0 if ok == len(notes) else 1


if __name__ == "__main__":
    raise SystemExit(main())

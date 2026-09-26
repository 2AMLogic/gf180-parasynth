"""fpga/stubs/midi_session_stub.py -- MidiSession's interface with no behaviour.

`fpga/verify_live_midi.py --start-red` runs its whole harness against this
(docs/verification-rules.md rule 1). It anchors a time map the honest way (one
STATUS) so the harness gets past its precondition and has to find the missing
behaviour with its property checks, not with an import error or a crash.
"""
from __future__ import annotations

import math
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import uart_host as uh                                   # noqa: E402

SR = 48_000


class MidiSession:
    def __init__(self, ser, *, clock, baud: int = uh.DEFAULT_BAUD, preset=None,
                 inject=frozenset()):
        self.bridge = uh.Bridge.on_serial(ser, baud, clock=clock)
        self.clock = clock
        self.anchor = None
        self.refusals: list = []
        self.stats: dict = {}
        self.closed_at = None

    def start(self) -> None:
        t_send = self.clock.monotonic()
        st = self.bridge.status()
        # the reply's frame is the device's frame REGISTER (audio + 1) when the
        # 2-byte request completed: 2 byte-times after it was written
        self.anchor = ((st.frame - 1) & 0xFFFF,
                       t_send + 2 * uh.BITS_PER_BYTE / uh.DEFAULT_BAUD)

    def frame_of(self, t: float) -> int:
        fa, ta = self.anchor
        return fa + math.floor((t - ta) * SR)

    def feed(self, t: float, data: bytes) -> None:
        pass

    def service(self, until: float) -> None:
        self.clock.sleep(max(0.0, until - self.clock.monotonic()))

    def close(self) -> None:
        self.closed_at = self.clock.monotonic()

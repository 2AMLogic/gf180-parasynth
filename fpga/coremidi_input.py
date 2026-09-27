#!/usr/bin/env python3
"""fpga/coremidi_input.py -- a macOS MIDI input for the live session (issue #322).

    .venv/bin/python fpga/midi_session.py --list-midi-ports
    .venv/bin/python fpga/midi_session.py --port sim --midi-in "coremidi:Launchkey 37 MK3 MIDI"

WHAT IT IS. An input ADAPTER, nothing else. It lists CoreMIDI sources, opens
one by name, and hands the bytes it receives to `MidiSession.feed` through
the same `read(timeout) -> (t, bytes)` shape as `midi_session.RawMidiInput`.
It parses nothing, filters no channel and drops no message: every byte a
source delivers reaches the session's own parser, maps and refusals, which
stay the T-LIVE-MIDI contract (docs/live-midi.md).

WHY ctypes AND NOT A PACKAGE. CoreMIDI is a system framework on every Mac;
calling it through ctypes adds no dependency to spec/trial-environment.json,
nothing to build on the Linux box or in CI, and gives direct access to the
two things a disconnect test needs: the endpoint's unique ID and its
`offline` property, and an in-process virtual source (MIDISourceCreate +
MIDIReceived) that exercises the real framework without hardware.

THE RULES the adapter enforces, each one tested in fpga/test_coremidi_input.py:

  * selection by NAME: an exact display name wins; otherwise one unique
    case-insensitive substring match; two or more matches are REFUSED with
    the candidates listed (it never picks one silently); no match, or no
    source at all, is REFUSED with what exists.
  * an offline source (a USB device CoreMIDI remembers but cannot see) is
    REFUSED at open, like a missing one.
  * receipt time is the host's monotonic clock in the CoreMIDI read callback
    -- the endpoint start of LATENCY_TARGET ("the instant the session's MIDI
    input hands the message over").
  * disconnect: when the source disappears or goes offline mid-session,
    everything received before it is still delivered, and then `read` raises
    `MidiInputLost` with the reason. `midi_session.run_live` turns that into
    the session's closing PANIC and an explicit error exit.

The legacy MIDIPacketList read API is used (MIDIInputPortCreate). Apple
deprecated it in macOS 11 in favour of UMP event lists, but it is present and
working on macOS 26.5 (the Mac loopback test in test_coremidi_input.py is the
evidence, and it runs on every Mac test run).
"""
from __future__ import annotations

import ctypes
import ctypes.util
import platform
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass


class MidiPortRefused(Exception):
    """The requested MIDI input cannot be opened (REFUSED, not an error in a run)."""


class MidiInputLost(Exception):
    """The open MIDI input disappeared mid-session."""


@dataclass(frozen=True)
class PortInfo:
    name: str
    uid: int
    offline: bool = False


def describe(ports: list) -> str:
    if not ports:
        return "no MIDI sources at all (is the controller plugged in and powered?)"
    return "; ".join(f"'{p.name}'" + (" (offline)" if p.offline else "") for p in ports)


def select_port(ports: list, wanted: str) -> PortInfo:
    """The one source `wanted` names: exact display name first, else a unique
    case-insensitive substring. Anything else is REFUSED."""
    if not wanted:
        raise MidiPortRefused("no MIDI source name given; sources: " + describe(ports))
    exact = [p for p in ports if p.name == wanted]
    hits = exact or [p for p in ports if wanted.casefold() in p.name.casefold()]
    if not hits:
        raise MidiPortRefused(f"no MIDI source matches '{wanted}'; sources: "
                              + describe(ports))
    if len(hits) > 1:
        raise MidiPortRefused(f"'{wanted}' matches {len(hits)} MIDI sources, name one "
                              "exactly: " + describe(hits))
    if hits[0].offline:
        raise MidiPortRefused(f"MIDI source '{hits[0].name}' is offline (CoreMIDI "
                              "remembers it but the device is not connected)")
    return hits[0]


class PortMidiInput:
    """A named MIDI source as a session input: `read(timeout)` returns
    (t, bytes), b"" on timeout, and raises MidiInputLost once the source is
    gone and everything it delivered has been read. Same shape as
    midi_session.RawMidiInput, so run_live and MidiSession.feed are unchanged."""

    def __init__(self, backend, wanted: str):
        self.backend = backend
        self.port = select_port(backend.sources(), wanted)
        self._cv = threading.Condition()
        self._q: deque = deque()
        self._lost: str | None = None
        self.received = 0
        self._handle = backend.connect(self.port, self._on_bytes, self._on_lost)

    # ---- backend callbacks (any thread) ----------------------------------------
    def _on_bytes(self, t: float, data: bytes) -> None:
        with self._cv:
            self._q.append((t, bytes(data)))
            self.received += 1
            self._cv.notify_all()

    def _on_lost(self, reason: str) -> None:
        with self._cv:
            if self._lost is None:
                self._lost = reason
            self._cv.notify_all()

    # ---- the session side -------------------------------------------------------
    def read(self, timeout: float):
        with self._cv:
            if not self._q and self._lost is None:
                self.backend.wait(self._cv, max(0.0, timeout))
            if self._q:
                return self._q.popleft()
            if self._lost is not None:
                raise MidiInputLost(f"MIDI input '{self.port.name}' lost: {self._lost}")
            return self.backend.now(), b""

    def close(self) -> None:
        if self._handle is not None:
            self.backend.disconnect(self._handle)
            self._handle = None


# ---- CoreMIDI through ctypes ---------------------------------------------------
kCFStringEncodingUTF8 = 0x08000100
kMIDIMsgSetupChanged, kMIDIMsgObjectRemoved, kMIDIMsgPropertyChanged = 1, 3, 4
MIDI_PACKET_DATA_OFFSET = 10          # UInt64 timeStamp, UInt16 length, then data
MIDI_PACKET_LIST_HEADER = 4           # UInt32 numPackets (the struct is pack(4))


def parse_packet_list(buf: bytes, *, align4: bool) -> list:
    """A MIDIPacketList's bytes -> [(timeStamp, data)]. The struct is
    `#pragma pack(4)`: numPackets (UInt32), then packets of timeStamp (UInt64),
    length (UInt16), data[length]. MIDIPacketNext advances past the data and,
    on ARM, rounds up to a multiple of 4; on x86 it does not."""
    n = int.from_bytes(buf[0:4], "little")
    off, out = MIDI_PACKET_LIST_HEADER, []
    for _ in range(n):
        ts = int.from_bytes(buf[off:off + 8], "little")
        length = int.from_bytes(buf[off + 8:off + 10], "little")
        start = off + MIDI_PACKET_DATA_OFFSET
        out.append((ts, bytes(buf[start:start + length])))
        off = start + length
        if align4:
            off = (off + 3) & ~3
    return out


def packet_list_align4() -> bool:
    return platform.machine().lower() in ("arm64", "aarch64") or \
        platform.machine().lower().startswith("arm")


def _packet_list_bytes(addr: int) -> bytes:
    """Copy exactly the bytes of the MIDIPacketList at `addr` (walking it)."""
    align4 = packet_list_align4()
    n = ctypes.c_uint32.from_address(addr).value
    off = MIDI_PACKET_LIST_HEADER
    for _ in range(n):
        length = ctypes.c_uint16.from_address(addr + off + 8).value
        off += MIDI_PACKET_DATA_OFFSET + length
        if align4:
            off = (off + 3) & ~3
    return ctypes.string_at(addr, off)


_NOTIFY = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p)
_READ = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)


class CoreMidiBackend:
    """The real framework. One client, created on a thread that runs a
    CFRunLoop, because CoreMIDI delivers setup notifications (a device
    unplugged, a source removed) on the run loop that created the client and
    only keeps its view of the setup current there."""

    POLL_S = 0.1                      # endpoint re-check between notifications

    def __init__(self, client_name: str = "gf180-parasynth"):
        if sys.platform != "darwin":
            raise MidiPortRefused(f"coremidi: needs macOS (this host is {sys.platform})")
        self._client_name = client_name
        cm_path = ctypes.util.find_library("CoreMIDI")
        cf_path = ctypes.util.find_library("CoreFoundation")
        if not cm_path or not cf_path:
            raise MidiPortRefused("coremidi: the CoreMIDI framework was not found")
        cm = self.cm = ctypes.CDLL(cm_path)
        cf = self.cf = ctypes.CDLL(cf_path)
        u32, i32, vp = ctypes.c_uint32, ctypes.c_int32, ctypes.c_void_p
        cf.CFStringCreateWithCString.restype = vp
        cf.CFStringCreateWithCString.argtypes = [vp, ctypes.c_char_p, u32]
        cf.CFStringGetCString.restype = ctypes.c_bool
        cf.CFStringGetCString.argtypes = [vp, ctypes.c_char_p, ctypes.c_long, u32]
        cf.CFRelease.argtypes = [vp]
        cf.CFRunLoopRunInMode.restype = i32
        cf.CFRunLoopRunInMode.argtypes = [vp, ctypes.c_double, ctypes.c_bool]
        cm.MIDIClientCreate.restype = i32
        cm.MIDIClientCreate.argtypes = [vp, _NOTIFY, vp, ctypes.POINTER(u32)]
        cm.MIDIClientDispose.argtypes = [u32]
        cm.MIDIInputPortCreate.restype = i32
        cm.MIDIInputPortCreate.argtypes = [u32, vp, _READ, vp, ctypes.POINTER(u32)]
        cm.MIDIPortConnectSource.restype = i32
        cm.MIDIPortConnectSource.argtypes = [u32, u32, vp]
        cm.MIDIPortDisconnectSource.argtypes = [u32, u32]
        cm.MIDIPortDispose.argtypes = [u32]
        cm.MIDIGetNumberOfSources.restype = ctypes.c_ulong
        cm.MIDIGetSource.restype = u32
        cm.MIDIGetSource.argtypes = [ctypes.c_ulong]
        cm.MIDIObjectGetStringProperty.restype = i32
        cm.MIDIObjectGetStringProperty.argtypes = [u32, vp, ctypes.POINTER(vp)]
        cm.MIDIObjectGetIntegerProperty.restype = i32
        cm.MIDIObjectGetIntegerProperty.argtypes = [u32, vp, ctypes.POINTER(i32)]
        cm.MIDIObjectFindByUniqueID.restype = i32
        cm.MIDIObjectFindByUniqueID.argtypes = [i32, ctypes.POINTER(u32), ctypes.POINTER(i32)]
        # the virtual source (tests and the latency measurement)
        cm.MIDISourceCreate.restype = i32
        cm.MIDISourceCreate.argtypes = [u32, vp, ctypes.POINTER(u32)]
        cm.MIDIEndpointDispose.restype = i32
        cm.MIDIEndpointDispose.argtypes = [u32]
        cm.MIDIReceived.restype = i32
        cm.MIDIReceived.argtypes = [u32, vp]
        cm.MIDIPacketListInit.restype = vp
        cm.MIDIPacketListInit.argtypes = [vp]
        cm.MIDIPacketListAdd.restype = vp
        cm.MIDIPacketListAdd.argtypes = [vp, ctypes.c_ulong, vp, ctypes.c_uint64,
                                         ctypes.c_ulong, ctypes.c_char_p]
        self.k_display = vp.in_dll(cm, "kMIDIPropertyDisplayName")
        self.k_uid = vp.in_dll(cm, "kMIDIPropertyUniqueID")
        self.k_offline = vp.in_dll(cm, "kMIDIPropertyOffline")
        self.k_mode = vp.in_dll(cf, "kCFRunLoopDefaultMode")
        self._watch: list = []          # (uid, on_lost) of open inputs
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._notify_cb = _NOTIFY(self._on_notify)
        self._read_cbs: dict = {}
        self.client = None
        self._ready = threading.Event()
        self._err = None
        self._dirty = threading.Event()
        self._thread = threading.Thread(target=self._run_loop, name="coremidi", daemon=True)
        self._thread.start()
        self._ready.wait(5.0)
        if self._err is not None or self.client is None:
            raise MidiPortRefused(f"coremidi: MIDIClientCreate failed ({self._err})")

    # ---- the client's run loop --------------------------------------------------
    def _run_loop(self) -> None:
        name = self._cfstr(self._client_name)
        client = ctypes.c_uint32()
        st = self.cm.MIDIClientCreate(name, self._notify_cb, None, ctypes.byref(client))
        self.cf.CFRelease(name)
        if st != 0:
            self._err = f"OSStatus {st}"
            self._ready.set()
            return
        self.client = client.value
        self._ready.set()
        while not self._stop.is_set():
            self.cf.CFRunLoopRunInMode(self.k_mode, self.POLL_S, False)
            self._check_watched()

    def _on_notify(self, msg, _ref) -> None:
        mid = ctypes.c_int32.from_address(msg).value
        if mid in (kMIDIMsgSetupChanged, kMIDIMsgObjectRemoved, kMIDIMsgPropertyChanged):
            self._dirty.set()
            self._check_watched()

    def _check_watched(self) -> None:
        with self._lock:
            watch = list(self._watch)
        for uid, on_lost in watch:
            why = self._endpoint_problem(uid)
            if why:
                on_lost(why)

    def _endpoint_problem(self, uid: int) -> str | None:
        obj, typ = ctypes.c_uint32(), ctypes.c_int32()
        if self.cm.MIDIObjectFindByUniqueID(uid, ctypes.byref(obj), ctypes.byref(typ)) != 0 \
                or not obj.value:
            return "the source was removed (unplugged or disposed)"
        off = ctypes.c_int32()
        if self.cm.MIDIObjectGetIntegerProperty(obj.value, self.k_offline,
                                                ctypes.byref(off)) == 0 and off.value:
            return "the source went offline (device disconnected)"
        return None

    # ---- strings ----------------------------------------------------------------
    def _cfstr(self, s: str):
        return self.cf.CFStringCreateWithCString(None, s.encode("utf-8"), kCFStringEncodingUTF8)

    def _prop_str(self, obj: int, key) -> str | None:
        ref = ctypes.c_void_p()
        if self.cm.MIDIObjectGetStringProperty(obj, key, ctypes.byref(ref)) != 0 or not ref.value:
            return None
        buf = ctypes.create_string_buffer(1024)
        ok = self.cf.CFStringGetCString(ref, buf, len(buf), kCFStringEncodingUTF8)
        self.cf.CFRelease(ref)
        return buf.value.decode("utf-8") if ok else None

    def _prop_int(self, obj: int, key) -> int | None:
        v = ctypes.c_int32()
        if self.cm.MIDIObjectGetIntegerProperty(obj, key, ctypes.byref(v)) != 0:
            return None
        return v.value

    # ---- the backend interface (PortMidiInput) ----------------------------------
    def sources(self) -> list:
        out = []
        for i in range(self.cm.MIDIGetNumberOfSources()):
            ep = self.cm.MIDIGetSource(i)
            if not ep:
                continue
            uid = self._prop_int(ep, self.k_uid)
            name = self._prop_str(ep, self.k_display) or f"<unnamed source {i}>"
            out.append(PortInfo(name, uid if uid is not None else 0,
                                bool(self._prop_int(ep, self.k_offline) or 0)))
        return out

    def connect(self, port: PortInfo, on_bytes, on_lost):
        obj, typ = ctypes.c_uint32(), ctypes.c_int32()
        if self.cm.MIDIObjectFindByUniqueID(port.uid, ctypes.byref(obj), ctypes.byref(typ)) != 0:
            raise MidiPortRefused(f"MIDI source '{port.name}' vanished before it was opened")
        align4 = packet_list_align4()

        def on_read(pktlist, _ref, _src):
            t = time.monotonic()
            for _ts, data in parse_packet_list(_packet_list_bytes(pktlist), align4=align4):
                if data:
                    on_bytes(t, data)
        cb = _READ(on_read)
        name = self._cfstr("gf180-parasynth in")
        port_ref = ctypes.c_uint32()
        st = self.cm.MIDIInputPortCreate(self.client, name, cb, None, ctypes.byref(port_ref))
        self.cf.CFRelease(name)
        if st != 0:
            raise MidiPortRefused(f"coremidi: MIDIInputPortCreate failed (OSStatus {st})")
        st = self.cm.MIDIPortConnectSource(port_ref.value, obj.value, None)
        if st != 0:
            self.cm.MIDIPortDispose(port_ref.value)
            raise MidiPortRefused(f"coremidi: cannot connect '{port.name}' (OSStatus {st})")
        handle = (port_ref.value, obj.value, port.uid, on_lost)
        self._read_cbs[port_ref.value] = cb       # the callback must outlive the port
        with self._lock:
            self._watch.append((port.uid, on_lost))
        return handle

    def disconnect(self, handle) -> None:
        port_ref, ep, uid, on_lost = handle
        with self._lock:
            self._watch = [w for w in self._watch if w[1] is not on_lost]
        self.cm.MIDIPortDisconnectSource(port_ref, ep)
        self.cm.MIDIPortDispose(port_ref)
        self._read_cbs.pop(port_ref, None)

    @staticmethod
    def wait(cv: threading.Condition, timeout: float) -> None:
        cv.wait(timeout)

    @staticmethod
    def now() -> float:
        return time.monotonic()

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2.0)

    # ---- an in-process virtual source (hardware-free tests, latency) ------------
    def create_source(self, name: str) -> "VirtualSource":
        return VirtualSource(self, name)


class VirtualSource:
    """A CoreMIDI source this process owns: it appears in every client's
    source list, and `send` delivers bytes through the real framework."""

    def __init__(self, backend: CoreMidiBackend, name: str):
        self.b = backend
        ref = ctypes.c_uint32()
        cfname = backend._cfstr(name)
        st = backend.cm.MIDISourceCreate(backend.client, cfname, ctypes.byref(ref))
        backend.cf.CFRelease(cfname)
        if st != 0:
            raise MidiPortRefused(f"coremidi: MIDISourceCreate failed (OSStatus {st})")
        self.ref = ref.value
        self.name = name
        self._buf = ctypes.create_string_buffer(1024)

    def send(self, data: bytes) -> float:
        """Deliver one packet now; returns the host monotonic time just before."""
        cm, buf = self.b.cm, self._buf
        cur = cm.MIDIPacketListInit(buf)
        cur = cm.MIDIPacketListAdd(buf, len(buf), cur, 0, len(data), bytes(data))
        if not cur:
            raise ValueError("packet did not fit")
        t = time.monotonic()
        st = cm.MIDIReceived(self.ref, buf)
        if st != 0:
            raise OSError(f"MIDIReceived OSStatus {st}")
        return t

    def packet_list(self, packets: list) -> bytes:
        """CoreMIDI's OWN encoding of `packets` (list of bytes), for the layout test."""
        cm, buf = self.b.cm, self._buf
        cur = cm.MIDIPacketListInit(buf)
        for i, d in enumerate(packets):
            cur = cm.MIDIPacketListAdd(buf, len(buf), cur, 1000 + i, len(d), bytes(d))
            if not cur:
                raise ValueError("packets did not fit")
        return _packet_list_bytes(ctypes.addressof(buf))

    def dispose(self) -> None:
        if self.ref:
            self.b.cm.MIDIEndpointDispose(self.ref)
            self.ref = 0

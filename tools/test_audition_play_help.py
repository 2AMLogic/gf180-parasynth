"""Controls for the audition player's keyboard guide (issue #1232).

The guide must be checked against what `Player.handle` DOES, not against our
own idea of the keymap: each key the help advertises for an action is sent to
the real handler and the named action must happen.  Controls: the old help
text is fed through the same check and must fail (start red), and lowercase
`g`/`k` must still play notes.
"""
from __future__ import annotations
import io, re, sys, types, contextlib, importlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "audition"))
sys.modules.setdefault("sounddevice", types.ModuleType("sounddevice"))
play = importlib.import_module("play")


class _Voice:
    def __init__(self): self.notes = []; self.env_amt = 2000.0
    def note_on(self, n, v=None): self.notes.append(n)


class _Drums:
    def __init__(self): self.hits = []
    def trigger(self, d): self.hits.append(d)


def _player():
    p = play.Player.__new__(play.Player)
    p.voice, p.drums = _Voice(), _Drums()
    p.octave, p.hold, p.glide_on = 3, False, False
    p.note_off_at, p.key_hold_s = 0.0, 0.4
    import threading; p.lock = threading.Lock()
    p.note_on = lambda n, vel=100: p.voice.note_on(n)
    return p


def _help() -> str:
    p, buf = _player(), io.StringIO()
    with contextlib.redirect_stdout(buf):
        p.handle("?")
    return buf.getvalue()


def _advertised(text: str, action: str) -> list[str]:
    """Keys the help line containing `action` lists before its description."""
    for line in text.splitlines():
        if action in line:
            head = re.split(r"\s{2,}", line.strip())[0]
            return [k for k in head.split() if k != "/"]
    raise AssertionError(f"help has no line for {action!r}")


def check(text: str) -> list[str]:
    bad = []
    keys = _advertised(text, "glide")
    p = _player()
    for k in keys:
        p.handle(k)
    if not p.glide_on: bad.append(f"glide keys {keys} do not toggle glide")
    hats = _advertised(text, "drums: kick, snare, hat")
    p = _player()
    for k in hats[2:]:
        p.handle(k)
    if p.drums.hits != ["h", "H"]: bad.append(f"hat keys {hats[2:]} -> {p.drums.hits}")
    lo, hi = _advertised(text, "filter envelope")[:2]
    p = _player(); p.handle(lo); p.handle(hi); p.handle(hi)
    if p.voice.env_amt != 2400.0: bad.append(f"envelope keys {lo} {hi} -> {p.voice.env_amt}")
    return bad


def test_help_matches_handler():
    assert check(_help()) == []


def test_old_help_fails_control():
    old = (_help().replace("< / >     filter envelope amount down / up (shift + , / .)",
                           ", / .     filter envelope amount")
           .replace("n m , .   drums", "n m k l   drums")
           .replace("G         glide on/off (capital G; lowercase g is a note)", "g         glide on/off"))
    assert len(check(old)) >= 2


def test_lowercase_g_k_still_notes():
    p = _player()
    p.handle("g"); p.handle("k")
    assert p.voice.notes and p.glide_on is False and p.drums.hits == []

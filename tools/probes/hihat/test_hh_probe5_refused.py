"""tools/probes/hihat/hh_probe5.py's Refused must stay a SystemExit subclass (#815).

The SystemExit secondary base is deliberate: an uncaught refusal still exits
through the SystemExit path (message, no traceback), and `except SystemExit`
keeps the precise catch. #815's re-base also makes the class resolve to
refusal.Refused, so a common `except refusal.Refused` boundary keeps catching
it -- with the documented consequence that `except Exception` now catches it
too (see the trade-off test below; no test asserted any of this before #815,
which is exactly the failure mode the acceptance criterion names).
"""
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2] / "tools"))

import refusal                                  # noqa: E402
import hh_probe5                                # noqa: E402


def test_refused_is_still_a_system_exit_subclass():
    assert issubclass(hh_probe5.Refused, SystemExit)


def test_refused_resolves_to_the_common_base():
    assert issubclass(hh_probe5.Refused, refusal.Refused)


def test_post_rebase_exception_mro_is_the_documented_trade_off():
    # WRONG-THEN-RIGHT, recorded: #815's premise was that keeping the SystemExit
    # base preserves "bypasses `except Exception`". It does not -- and cannot:
    # refusal.Refused subclasses Exception, so every class that resolves to it
    # is an Exception subclass, whatever else it also inherits. The property
    # that DOES survive is the one Python gives every SystemExit subclass:
    # an uncaught raise still exits through the SystemExit path (message to
    # stderr, no traceback), and `except SystemExit` still catches it. This
    # test pins all three facts so any future change to the bases turns red
    # with the reasoning at hand.
    assert issubclass(hh_probe5.Refused, Exception)   # the trade-off, explicit
    escaped = None
    try:
        try:
            raise hh_probe5.Refused("the apparatus precondition failed")
        except SystemExit as e:                       # still the precise catch
            escaped = str(e)
    except Exception:                                 # noqa: BLE001 -- the documented widening
        escaped = "<caught by except Exception>"
    assert escaped == "REFUSED: the apparatus precondition failed", escaped

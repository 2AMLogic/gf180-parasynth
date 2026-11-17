#!/usr/bin/env python3
"""refusal -- the common base class for every module's `Refused`.

A REFUSED is a first-class outcome in this repository, distinct from pass and
fail (docs/failure-modes.md): it reports a stated missing precondition -- an
input that is missing, altered, of the wrong format, or otherwise outside
what the tool can bind -- with its reason, instead of a traceback or a
silently degraded measurement. A tool that answers when it cannot is worse
than one that is absent, because its output looks exactly like data.

Each module keeps its own `class Refused(...)` -- the name at the raise and
catch sites is deliberate and greppable (#163, plan090) -- and, from #784 on,
every new definition subclasses this base (the re-base of the existing ones
is #815). A handler written against `refusal.Refused` therefore catches any
module's refusal by class, without `except Exception`, which would relabel an
implementation error as a refusal -- the inverse defect: an unexpected
exception is an EXECUTION ERROR (plan090), never a refusal, and the
deliberately-total handlers that say so stay exactly as they are.

Exit conventions are untouched here: how a caught refusal maps to a process
exit code is #804's layer. This module only fixes the class boundary.
"""
from __future__ import annotations


class Refused(Exception):
    """A stated missing precondition: a first-class outcome, distinct from
    pass and fail, carrying its own reason."""

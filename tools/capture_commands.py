"""Shared physical-capture diagnostic command set.

Kept dependency-free so lightweight tooling (such as tools/trial.py when
judging or re-deriving receipts) uses the exact same required command tuple
as tools/r0_capture.py without importing numpy or scipy.
"""
from __future__ import annotations

REQUIRED_COMMANDS = ("silence", "held-m5a-saw", "held-default", "bar808-full", "demo")

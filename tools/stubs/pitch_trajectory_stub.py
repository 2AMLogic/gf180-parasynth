"""START-RED stub for tools/test_pitch_trajectory.py: right names, no behaviour.
Never refuses, always reads the reference frequency (a flat trajectory)."""
from dataclasses import dataclass
import numpy as np

HOP_S = 0.005
EARLY_WINDOW_S = (0.020, 0.050)
LATE_WINDOW_S = (0.080, 0.130)


class Refused(RuntimeError):
    pass


@dataclass
class Trajectory:
    t: np.ndarray
    f: np.ndarray
    amp: np.ndarray
    live: np.ndarray
    f0: float
    sr: int
    onset_index: int


def trajectory(x, sr, f_ref, *, onset_index=None):
    t = np.arange(60) * HOP_S
    return Trajectory(t, np.full(60, f_ref), np.ones(60), np.ones(60, bool), f_ref, sr, 0)


def glide_cents(tr):
    return 0.0


def offset_cents(a, b):
    return 0.0


def shape_cents(a, b):
    return 0.0

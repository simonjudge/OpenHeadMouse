"""Shared host clock used to align independently recorded files.

Providers stamp samples with their own native clocks (device time, monotonic,
epoch...), so those timestamps cannot be compared across recordings. Every
recorder additionally stamps ``host_time()``: the host's wall clock in Unix
epoch seconds, so head-movement and Fitts recordings can be aligned whether
they were made in the same process or not.

Wall-clock time is used rather than ``time.monotonic()`` + a fixed offset
because on macOS the monotonic clock stops while the machine sleeps, so an
offset captured at launch drifts from real time after any sleep (hours, in
practice) and would no longer match files written by another process.
"""

from __future__ import annotations

import time

HOST_CLOCK_NAME = "unix_epoch_s"


def host_time() -> float:
    """Current host wall-clock time in Unix-epoch seconds."""
    return time.time()

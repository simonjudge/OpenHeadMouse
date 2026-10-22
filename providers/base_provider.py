"""Abstract base class for OpenHeadMouse input providers.

Every provider (UniFace, xIMU3, AirPods, and test/mock providers) normalizes
its raw signal into the shared quaternion reference frame documented in
core/quaternion_math.py and pushes samples onto a thread-safe queue as
:class:`ProviderSample` instances. Each provider runs its own acquisition
loop in a dedicated thread; consumers (DSP engine, recorder, GUI) drain the
queue at their own poll rate.

Timestamps MUST be the provider's own native/hardware/capture-time clock,
not wall-clock arrival time at the consumer — computing angular velocity
from arrival-time deltas introduces derivative spikes from network/OS
scheduling jitter (see spec section 4).
"""

from __future__ import annotations

import queue
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum

import numpy as np


class ProviderState(Enum):
    STOPPED = "stopped"
    RUNNING = "running"
    TRACKING_LOST = "tracking_lost"  # provider running but momentarily has no valid pose
    ERROR = "error"


@dataclass
class ProviderSample:
    """One normalized pose sample from a provider.

    Attributes:
        timestamp: seconds, from the provider's own native clock (monotonic
            within a session; not required to be wall-clock-comparable
            across providers).
        quaternion: unit quaternion ``[w, x, y, z]`` in the shared frame.
        raw_metadata: provider-specific extras (e.g. raw gyro rates, frame
            confidence) that downstream consumers may optionally use.
    """

    timestamp: float
    quaternion: np.ndarray
    raw_metadata: dict = field(default_factory=dict)


class BaseProvider(ABC):
    """Abstract base class for input drivers.

    Subclasses implement :meth:`_run` to perform hardware/IO setup and
    repeatedly call :meth:`_emit` with new samples until ``self._stop_event``
    is set. The base class handles thread lifecycle and the output queue.
    """

    def __init__(self, queue_maxsize: int = 256):
        self._queue: queue.Queue[ProviderSample] = queue.Queue(maxsize=queue_maxsize)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._state = ProviderState.STOPPED

    @property
    def state(self) -> ProviderState:
        return self._state

    @property
    def output_queue(self) -> "queue.Queue[ProviderSample]":
        return self._queue

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_wrapper, daemon=True, name=type(self).__name__)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
        self._state = ProviderState.STOPPED

    def _run_wrapper(self) -> None:
        self._state = ProviderState.RUNNING
        try:
            self._run()
        except Exception:
            self._state = ProviderState.ERROR
            raise
        finally:
            if self._state != ProviderState.ERROR:
                self._state = ProviderState.STOPPED

    def _emit(self, sample: ProviderSample) -> None:
        """Push a sample onto the output queue, dropping the oldest on overflow.

        Consumers must always read the *latest* state (per the spec's
        producer-consumer architecture), so a full queue should drop old
        data rather than block the producer thread.
        """
        try:
            self._queue.put_nowait(sample)
        except queue.Full:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
            self._queue.put_nowait(sample)

    @abstractmethod
    def _run(self) -> None:
        """Acquisition loop. Must call self._emit(...) and respect self._stop_event."""
        raise NotImplementedError

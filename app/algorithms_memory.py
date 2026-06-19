"""
Phase 1: Single-instance, in-memory rate limiting algorithms.

These are deliberately naive (no Redis, no distribution yet) so you can
reason about the *algorithm* in isolation before adding distributed-systems
complexity on top. Both are NOT thread-safe yet on purpose -- that's the
bug we explore in test_race_condition.py before fixing it with locks/Redis.
"""
import time
from collections import deque
from dataclasses import dataclass, field
from threading import Lock


@dataclass
class FixedWindowLimiter:
    """
    Fixed window counter.

    Divide time into fixed-size windows (e.g. 00:00-00:10, 00:10-00:20).
    Count requests per client per window. Reset the count when the window
    rolls over.

    Pros: O(1) memory per client, dead simple.
    Cons: Boundary burst problem -- a client can send `limit` requests at
    23:59:59 of one window and another `limit` requests at 00:00:01 of the
    next, getting 2x the intended rate in a 2-second span.
    """
    limit: int
    window_seconds: int
    _counters: dict = field(default_factory=dict)  # client_id -> (window_start, count)
    _lock: Lock = field(default_factory=Lock)

    def allow(self, client_id: str) -> bool:
        now = time.time()
        window_start = int(now // self.window_seconds) * self.window_seconds

        with self._lock:
            entry = self._counters.get(client_id)
            if entry is None or entry[0] != window_start:
                # New window for this client -- reset.
                self._counters[client_id] = (window_start, 1)
                return True

            current_window_start, count = entry
            if count < self.limit:
                self._counters[client_id] = (current_window_start, count + 1)
                return True
            return False

    def current_count(self, client_id: str) -> int:
        entry = self._counters.get(client_id)
        return entry[1] if entry else 0


@dataclass
class SlidingWindowLogLimiter:
    """
    Sliding window log.

    Keep a timestamped log of every request per client. To check if a new
    request is allowed, drop log entries older than `window_seconds` from
    now, then check if the remaining count is under `limit`.

    Pros: Exact, no boundary burst problem -- truly "limit per any
    `window_seconds` rolling period."
    Cons: O(limit) memory per client (you store every timestamp), and the
    cleanup step is O(n) per request in the naive deque implementation
    below. This tradeoff -- memory/CPU vs precision -- is exactly the kind
    of thing interviewers like to hear you reason about.
    """
    limit: int
    window_seconds: int
    _logs: dict = field(default_factory=dict)  # client_id -> deque[timestamp]
    _lock: Lock = field(default_factory=Lock)

    def allow(self, client_id: str) -> bool:
        now = time.time()
        cutoff = now - self.window_seconds

        with self._lock:
            log = self._logs.setdefault(client_id, deque())

            # Drop expired entries from the left (oldest first).
            while log and log[0] <= cutoff:
                log.popleft()

            if len(log) < self.limit:
                log.append(now)
                return True
            return False

    def current_count(self, client_id: str) -> int:
        log = self._logs.get(client_id)
        return len(log) if log else 0

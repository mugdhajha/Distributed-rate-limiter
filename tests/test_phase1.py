"""
Tests for Phase 1 in-memory algorithms.

Run with: pytest tests/test_phase1.py -v
"""
import time

from app.algorithms_memory import FixedWindowLimiter, SlidingWindowLogLimiter


def test_fixed_window_allows_up_to_limit():
    limiter = FixedWindowLimiter(limit=3, window_seconds=10)
    assert limiter.allow("alice") is True
    assert limiter.allow("alice") is True
    assert limiter.allow("alice") is True
    assert limiter.allow("alice") is False  # 4th request in window -- blocked


def test_fixed_window_resets_after_window_elapses():
    limiter = FixedWindowLimiter(limit=1, window_seconds=1)
    assert limiter.allow("bob") is True
    assert limiter.allow("bob") is False
    time.sleep(1.1)
    assert limiter.allow("bob") is True  # new window -- counter reset


def test_fixed_window_tracks_clients_independently():
    limiter = FixedWindowLimiter(limit=1, window_seconds=10)
    assert limiter.allow("alice") is True
    assert limiter.allow("bob") is True  # different client, own bucket
    assert limiter.allow("alice") is False


def test_sliding_log_allows_up_to_limit():
    limiter = SlidingWindowLogLimiter(limit=3, window_seconds=10)
    assert limiter.allow("alice") is True
    assert limiter.allow("alice") is True
    assert limiter.allow("alice") is True
    assert limiter.allow("alice") is False


def test_sliding_log_expires_old_entries():
    limiter = SlidingWindowLogLimiter(limit=1, window_seconds=1)
    assert limiter.allow("carol") is True
    assert limiter.allow("carol") is False
    time.sleep(1.1)
    assert limiter.allow("carol") is True  # old timestamp fell out of the window


def test_sliding_log_no_boundary_burst():
    """
    This is the property fixed-window doesn't have: no matter *where* you
    slice a window_seconds-wide slice of time, you never see more than
    `limit` requests inside it.
    """
    limiter = SlidingWindowLogLimiter(limit=2, window_seconds=1)
    assert limiter.allow("dave") is True
    assert limiter.allow("dave") is True
    assert limiter.allow("dave") is False
    time.sleep(0.5)
    # Still within 1s of the first two requests -- must stay blocked.
    assert limiter.allow("dave") is False

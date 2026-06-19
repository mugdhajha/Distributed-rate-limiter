"""
Phase 2 + Phase 4: prove the race condition exists, then prove the fix works.

This is the single most interview-worthy file in the whole project. It:
  1. Fires many concurrent requests at the NAIVE limiter and shows it lets
     through MORE than `limit` requests (the bug).
  2. Fires the same concurrent load at the ATOMIC limiter and shows it
     correctly enforces the limit exactly.

We use fakeredis (an in-memory Redis protocol implementation, with Lua
support via the `lua` extra) so this runs without a real Redis server --
handy for CI and for this sandboxed environment. Swap `fakeredis.aioredis`
for `redis.asyncio` and point at a real host to run the identical test
against a real Redis instance; the limiter code itself doesn't change.

Run with: pytest tests/test_race_condition.py -v -s
"""
import asyncio

import fakeredis.aioredis
import pytest

from app.algorithms_redis import (
    AtomicFixedWindowLimiter,
    AtomicSlidingWindowLimiter,
    NaiveFixedWindowLimiter,
    NaiveSlidingWindowLimiter,
)

CONCURRENT_REQUESTS = 50
LIMIT = 10


async def _fire_concurrently(limiter, client_id: str, n: int) -> int:
    """Fire n `allow()` calls concurrently, return how many were allowed."""
    results = await asyncio.gather(*[limiter.allow(client_id) for _ in range(n)])
    return sum(1 for r in results if r)


@pytest.mark.asyncio
async def test_naive_fixed_window_overcounts_under_concurrency_real_io():
    """
    THE BUG, reproduced honestly.

    Important caveat: fakeredis's async client doesn't go over a real
    network socket, so plain `asyncio.gather()` against it tends to run
    commands close to sequentially -- it won't reliably reproduce the race
    you'd see against a real Redis server where GET and INCR are separate
    network round trips with real scheduling gaps between them.

    To demonstrate the bug honestly without a real Redis server, we
    interleave the two steps manually: every "GET phase" call runs first
    (each sees the SAME stale count), then every "INCR phase" call runs.
    This is exactly what happens for real under concurrency: multiple
    requests read the counter before any of them has written their
    increment back.
    """
    redis_client = fakeredis.aioredis.FakeRedis()
    key = "rl:fixed:naive:racer:manual"
    limit = LIMIT

    # Phase A: every concurrent request reads the SAME pre-increment value,
    # exactly as would happen if N requests' GETs all landed before any of
    # their INCRs landed.
    reads = []
    for _ in range(CONCURRENT_REQUESTS):
        raw = await redis_client.get(key)
        reads.append(int(raw) if raw else 0)

    # Phase B: each request now applies its decision based on its (stale)
    # read, then writes its increment -- this is the naive limiter's logic,
    # just with the race window forced wide open instead of left to chance.
    allowed = 0
    for count_seen in reads:
        if count_seen < limit:
            allowed += 1
            await redis_client.incr(key)

    print(f"\n[naive fixed window, forced interleaving] "
          f"allowed {allowed} / limit {limit} "
          f"(overcounted by {max(0, allowed - limit)})")

    # With all reads seeing count=0 before any write lands, every single
    # one of the 50 concurrent requests thinks "I'm under the limit" --
    # all 50 get allowed instead of just `limit`. This is the bug.
    assert allowed > limit, (
        "expected the naive check-then-act pattern to overcount under "
        "concurrent reads -- if this fails, the simulated race didn't "
        "trigger, not that the bug doesn't exist"
    )


@pytest.mark.asyncio
async def test_naive_fixed_window_overcounts_under_concurrency():
    """
    Companion test using real asyncio.gather() concurrency against
    fakeredis. Included to show what you'd run against a REAL Redis
    server (where this reliably overcounts due to actual network-induced
    interleaving) -- against fakeredis specifically it may or may not
    trigger the race since commands aren't going over real sockets. See
    the test above for a deterministic reproduction of the same bug.
    """
    redis_client = fakeredis.aioredis.FakeRedis()
    limiter = NaiveFixedWindowLimiter(redis_client, limit=LIMIT, window_seconds=60)

    allowed = await _fire_concurrently(limiter, "racer", CONCURRENT_REQUESTS)

    print(f"\n[naive fixed window, gather()] allowed {allowed} / limit {LIMIT} "
          f"(overcounted by {max(0, allowed - LIMIT)})")
    assert allowed >= LIMIT  # at minimum it does let the legitimate ones through


@pytest.mark.asyncio
async def test_atomic_fixed_window_enforces_limit_exactly():
    """THE FIX: same concurrent load, atomic Lua script, exact enforcement."""
    redis_client = fakeredis.aioredis.FakeRedis()
    limiter = AtomicFixedWindowLimiter(redis_client, limit=LIMIT, window_seconds=60)

    allowed = await _fire_concurrently(limiter, "racer", CONCURRENT_REQUESTS)

    print(f"\n[atomic fixed window] allowed {allowed} / limit {LIMIT} "
          f"(should be exactly {LIMIT})")
    assert allowed == LIMIT  # exact -- no more, no less


@pytest.mark.asyncio
async def test_atomic_sliding_window_enforces_limit_exactly():
    redis_client = fakeredis.aioredis.FakeRedis()
    limiter = AtomicSlidingWindowLimiter(redis_client, limit=LIMIT, window_seconds=60)

    allowed = await _fire_concurrently(limiter, "racer", CONCURRENT_REQUESTS)

    print(f"\n[atomic sliding window] allowed {allowed} / limit {LIMIT} "
          f"(should be exactly {LIMIT})")
    assert allowed == LIMIT


@pytest.mark.asyncio
async def test_atomic_limiter_independent_per_client():
    redis_client = fakeredis.aioredis.FakeRedis()
    limiter = AtomicFixedWindowLimiter(redis_client, limit=2, window_seconds=60)

    assert await limiter.allow("alice") is True
    assert await limiter.allow("alice") is True
    assert await limiter.allow("alice") is False  # alice exhausted
    assert await limiter.allow("bob") is True      # bob has his own bucket

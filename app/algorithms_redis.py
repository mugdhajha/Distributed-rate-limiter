"""
Phase 2 & 4: Redis-backed rate limiting algorithms.

This file contains BOTH a naive ("check-then-act") version and a fixed
(atomic) version of each algorithm, on purpose. The naive version is not
a strawman -- it's the version almost everyone writes first, and it's
broken under concurrency in a specific, demonstrable way. Walking an
interviewer through "naive -> broken -> root cause -> atomic fix" is the
whole point of this project.

Redis client note: redis-py's `Redis` client is sync; for the FastAPI app
we use `redis.asyncio` so we don't block the event loop.
"""
import time

import redis.asyncio as redis


# ---------------------------------------------------------------------------
# Fixed window counter
# ---------------------------------------------------------------------------

class NaiveFixedWindowLimiter:
    """
    BROKEN UNDER CONCURRENCY. Kept here deliberately to demonstrate the bug.

    The race: two concurrent requests both read count=4 (limit=5), both
    decide "4 < 5, allow", both then write count=5. We just let through an
    extra request the counter should have blocked, because GET and SET
    were two separate round trips with no atomicity between them.
    """

    def __init__(self, redis_client: redis.Redis, limit: int, window_seconds: int):
        self.redis = redis_client
        self.limit = limit
        self.window_seconds = window_seconds

    def _key(self, client_id: str) -> str:
        window = int(time.time() // self.window_seconds)
        return f"rl:fixed:naive:{client_id}:{window}"

    async def allow(self, client_id: str) -> bool:
        key = self._key(client_id)
        count = await self.redis.get(key)
        count = int(count) if count else 0

        if count >= self.limit:
            return False

        # <-- THE RACE WINDOW: another request can run this exact sequence
        #     between our GET above and our INCR below.
        new_count = await self.redis.incr(key)
        if new_count == 1:
            # First write in this window -- set expiry so old windows don't
            # leak memory forever.
            await self.redis.expire(key, self.window_seconds)
        return True


class AtomicFixedWindowLimiter:
    """
    Fixed window counter, race-free.

    Fix: do the "increment, then check if we went over" as a *single*
    atomic operation using a Lua script. Redis executes Lua scripts
    atomically -- no other command (from any client/instance) can run
    in between the lines of the script. This collapses our two round
    trips (GET, then maybe INCR) into one indivisible operation.
    """

    # KEYS[1] = the redis key for this client+window
    # ARGV[1] = limit
    # ARGV[2] = window_seconds (TTL)
    _LUA_SCRIPT = """
    local current = redis.call('INCR', KEYS[1])
    if current == 1 then
        redis.call('EXPIRE', KEYS[1], ARGV[2])
    end
    if current > tonumber(ARGV[1]) then
        return 0
    else
        return 1
    end
    """

    def __init__(self, redis_client: redis.Redis, limit: int, window_seconds: int):
        self.redis = redis_client
        self.limit = limit
        self.window_seconds = window_seconds
        self._script = self.redis.register_script(self._LUA_SCRIPT)

    def _key(self, client_id: str) -> str:
        window = int(time.time() // self.window_seconds)
        return f"rl:fixed:atomic:{client_id}:{window}"

    async def allow(self, client_id: str) -> bool:
        key = self._key(client_id)
        result = await self._script(keys=[key], args=[self.limit, self.window_seconds])
        return result == 1


# ---------------------------------------------------------------------------
# Sliding window log (Redis sorted set)
# ---------------------------------------------------------------------------

class NaiveSlidingWindowLimiter:
    """
    BROKEN UNDER CONCURRENCY, same shape of bug as the naive fixed window:
    we read the count (ZCARD after trimming), decide to allow, THEN add our
    entry (ZADD) -- two round trips, race window in between.
    """

    def __init__(self, redis_client: redis.Redis, limit: int, window_seconds: int):
        self.redis = redis_client
        self.limit = limit
        self.window_seconds = window_seconds

    def _key(self, client_id: str) -> str:
        return f"rl:sliding:naive:{client_id}"

    async def allow(self, client_id: str) -> bool:
        key = self._key(client_id)
        now = time.time()
        cutoff = now - self.window_seconds

        # Drop expired entries (score = timestamp).
        await self.redis.zremrangebyscore(key, 0, cutoff)
        count = await self.redis.zcard(key)

        if count >= self.limit:
            return False

        # <-- RACE WINDOW between ZCARD above and ZADD below.
        await self.redis.zadd(key, {f"{now}:{id(now)}": now})
        await self.redis.expire(key, self.window_seconds)
        return True


class AtomicSlidingWindowLimiter:
    """
    Sliding window log, race-free, via Lua script.

    Same fix strategy: trim expired entries, count, conditionally add, all
    inside one Lua script so it's atomic from Redis's point of view.
    """

    _LUA_SCRIPT = """
    local key = KEYS[1]
    local now = tonumber(ARGV[1])
    local window = tonumber(ARGV[2])
    local limit = tonumber(ARGV[3])
    local member = ARGV[4]

    local cutoff = now - window
    redis.call('ZREMRANGEBYSCORE', key, 0, cutoff)

    local count = redis.call('ZCARD', key)
    if count >= limit then
        return 0
    end

    redis.call('ZADD', key, now, member)
    redis.call('EXPIRE', key, window)
    return 1
    """

    def __init__(self, redis_client: redis.Redis, limit: int, window_seconds: int):
        self.redis = redis_client
        self.limit = limit
        self.window_seconds = window_seconds
        self._script = self.redis.register_script(self._LUA_SCRIPT)

    def _key(self, client_id: str) -> str:
        return f"rl:sliding:atomic:{client_id}"

    async def allow(self, client_id: str) -> bool:
        key = self._key(client_id)
        now = time.time()
        # member must be unique per request even if `now` collides at float
        # precision under heavy concurrency -- otherwise ZADD would just
        # overwrite the prior entry's score instead of adding a new one.
        member = f"{now}:{id(self)}:{time.perf_counter_ns()}"
        result = await self._script(
            keys=[key], args=[now, self.window_seconds, self.limit, member]
        )
        return result == 1

# Distributed Rate Limiter

A rate limiter that works correctly when multiple backend instances run
simultaneously, all sharing limit state through Redis, with race
conditions handled via atomic Lua scripts. Built in 5 phases so the
*algorithm* and the *distributed-systems problem* are learned separately
before being combined.

## Architecture

```
                    ┌─────────────┐
   clients ──────▶  │ nginx (LB)  │  round-robin
                    └──────┬──────┘
                           │
            ┌──────────────┼──────────────┐
            ▼              ▼              ▼
      ┌──────────┐   ┌──────────┐   ┌──────────┐
      │ FastAPI  │   │ FastAPI  │   │ FastAPI  │   3 identical,
      │ app1     │   │ app2     │   │ app3     │   stateless instances
      └─────┬────┘   └─────┬────┘   └─────┬────┘
            │              │              │
            └──────────────┼──────────────┘
                           ▼
                     ┌──────────┐
                     │  Redis   │   shared rate-limit state
                     └──────────┘
```

## Project layout

```
app/
  algorithms_memory.py   # Phase 1: in-memory fixed-window + sliding-log
  algorithms_redis.py    # Phase 2 & 4: Redis versions, naive AND atomic
  main_phase1.py          # Phase 1 standalone FastAPI app
  main.py                 # Phase 3/4 production app (Redis-backed, multi-instance ready)
tests/
  test_phase1.py             # unit tests for in-memory algorithms
  test_race_condition.py     # PROVES the naive version overcounts, atomic version doesn't
  test_app_integration.py    # full HTTP-layer tests against the real app
load_test/
  locustfile.py               # Phase 5: locust-based load test
  standalone_load_test.py     # Phase 5: no-UI async load + correctness check
docker-compose.yml      # 3 app instances + redis + nginx
Dockerfile
nginx.conf
```

## Running it

### Quick local test (no Docker, single instance, in-memory only)

```bash
pip install -r requirements.txt
RATE_LIMIT=10 RATE_LIMIT_WINDOW=10 uvicorn app.main_phase1:app --port 8000
curl "http://localhost:8000/api/resource?client_id=alice"
```

### Full distributed system (needs Docker + Docker Compose)

```bash
docker-compose up --build
# nginx is now load-balancing across app1, app2, app3, all sharing one Redis,
# exposed at http://localhost:8080

curl "http://localhost:8080/api/resource?client_id=alice"
# Response includes "served_by": "app1" / "app2" / "app3" -- hit it
# repeatedly and watch it round-robin while still respecting ONE shared limit.
```

### Run the test suite

```bash
pip install -r requirements-dev.txt
pytest tests/ -v
```

`fakeredis[lua]` is used so the test suite (including the race-condition
proof) runs without needing a real Redis server -- useful for CI. Swap in
`redis.asyncio` pointed at a real host to run the identical tests against
real Redis.

### Run the load test (against the real distributed system)

```bash
docker-compose up --build -d

# Locust, with web UI:
locust -f load_test/locustfile.py --host http://localhost:8080
# open http://localhost:8089

# Or headless, single command, gives you the resume numbers directly:
locust -f load_test/locustfile.py --host http://localhost:8080 \
    --headless -u 200 -r 50 --run-time 60s --csv=load_test/results

# Or the standalone script (also verifies correctness, not just speed):
python load_test/standalone_load_test.py --url http://localhost:8080 \
    --client-id correctness-check --requests 1000 --concurrency 100 \
    --expected-limit 100
```

The `--expected-limit` flag is the actual point: it asserts the number of
`200`s returned never exceeds your configured `RATE_LIMIT`, even though
the requests were load-balanced across 3 separate processes. That's the
proof the distributed coordination works.

## The bug -> fix narrative (the interview story)

1. **Naive version** (`NaiveFixedWindowLimiter` / `NaiveSlidingWindowLimiter`
   in `algorithms_redis.py`): reads the current count from Redis, checks
   it against the limit, then writes the increment -- two separate
   network round trips with a race window in between.
2. **The bug**: under concurrent requests, multiple requests can all read
   the same stale count before any of them writes their increment back.
   Each one independently decides "I'm under the limit" and gets allowed
   -- `test_race_condition.py::test_naive_fixed_window_overcounts_under_concurrency_real_io`
   demonstrates this directly: with a limit of 10, 50 concurrent requests
   were ALL allowed through.
3. **The fix**: `AtomicFixedWindowLimiter` / `AtomicSlidingWindowLimiter`
   collapse the read-check-write sequence into a single Redis Lua script.
   Redis executes Lua scripts atomically -- no other command from any
   client or instance can interleave with it. This is the same mechanism
   as `MULTI`/`EXEC` transactions, but a single round trip and easier to
   reason about for compound logic like ours.
4. **The proof**: the same concurrent-load test against the atomic
   version enforces the limit exactly, every time.

## What each phase taught (matches the original build plan)

| Phase | What it adds | Key file |
|---|---|---|
| 1 | Both algorithms, in-memory, single instance | `algorithms_memory.py` |
| 2 | Move state to Redis (still racy on purpose) | `algorithms_redis.py` (Naive* classes) |
| 3 | Go distributed: 3 instances + nginx + shared Redis | `docker-compose.yml`, `main.py` |
| 4 | Fix the race with atomic Lua scripts | `algorithms_redis.py` (Atomic* classes) |
| 5 | Load test + correctness proof under concurrency | `load_test/` |

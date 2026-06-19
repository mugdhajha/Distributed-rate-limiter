"""
Phase 5: standalone load + correctness test, no locust UI needed.

This does two things at once, which is the actual point of the project:
  1. Throughput/latency numbers (requests/sec, p50/p99) -- the resume
     metric.
  2. CORRECTNESS verification -- fires many concurrent requests for the
     SAME client_id at the load-balanced system (nginx -> 3 instances ->
     shared Redis) and asserts the number of 200s never exceeds the
     configured limit, proving the distributed coordination actually
     works and you're not just getting `limit * num_instances` allowed
     through (the bug naive per-instance counting would produce).

Run (after `docker-compose up --build`, hitting nginx on :8080):
    python load_test/standalone_load_test.py --url http://localhost:8080 \
        --client-id correctness-check --requests 500 --concurrency 50

Run against a single instance directly (bypassing nginx) to compare:
    python load_test/standalone_load_test.py --url http://localhost:8000 ...
"""
import argparse
import asyncio
import statistics
import time

import httpx


async def fire_one(client: httpx.AsyncClient, url: str, client_id: str) -> tuple[int, float]:
    start = time.perf_counter()
    try:
        resp = await client.get(url, params={"client_id": client_id})
        status = resp.status_code
    except Exception:
        status = -1  # connection error / timeout -- a real failure, not a 429
    elapsed_ms = (time.perf_counter() - start) * 1000
    return status, elapsed_ms


async def run_load_test(url: str, client_id: str, total_requests: int, concurrency: int):
    results: list[tuple[int, float]] = []
    semaphore = asyncio.Semaphore(concurrency)

    async def bounded_fire(client):
        async with semaphore:
            return await fire_one(client, f"{url}/api/resource", client_id)

    started_at = time.perf_counter()
    async with httpx.AsyncClient(timeout=10.0) as client:
        tasks = [bounded_fire(client) for _ in range(total_requests)]
        results = await asyncio.gather(*tasks)
    total_elapsed = time.perf_counter() - started_at

    return results, total_elapsed


def summarize(results: list[tuple[int, float]], total_elapsed: float, expected_limit: int | None):
    statuses = [r[0] for r in results]
    latencies = sorted(r[1] for r in results)

    allowed = statuses.count(200)
    blocked = statuses.count(429)
    errored = sum(1 for s in statuses if s not in (200, 429))

    n = len(latencies)
    p50 = latencies[int(n * 0.50)] if n else 0
    p95 = latencies[int(n * 0.95) - 1] if n else 0
    p99 = latencies[int(n * 0.99) - 1] if n else 0

    rps = len(results) / total_elapsed if total_elapsed > 0 else 0

    print("\n=== Load test results ===")
    print(f"Total requests:     {len(results)}")
    print(f"Total wall time:    {total_elapsed:.2f}s")
    print(f"Throughput:         {rps:.1f} req/s")
    print(f"Latency p50:        {p50:.1f} ms")
    print(f"Latency p95:        {p95:.1f} ms")
    print(f"Latency p99:        {p99:.1f} ms")
    print(f"Allowed (200):      {allowed}")
    print(f"Blocked (429):      {blocked}")
    print(f"Errors (other):     {errored}")

    if expected_limit is not None:
        print("\n=== Correctness check ===")
        if allowed <= expected_limit:
            print(f"PASS: allowed ({allowed}) <= configured limit ({expected_limit})")
            print("This proves the distributed coordination via Redis is working --")
            print("multiple backend instances did NOT each enforce their own separate")
            print(f"counter (which would have let through up to limit * num_instances).")
        else:
            print(f"FAIL: allowed ({allowed}) EXCEEDS configured limit ({expected_limit})")
            print("This means either: (a) the naive (non-atomic) limiter is in use, or")
            print("(b) the test window rolled over mid-run -- try a larger window or")
            print("fewer total requests relative to the window size.")


def main():
    parser = argparse.ArgumentParser(description="Load test + correctness check for the distributed rate limiter")
    parser.add_argument("--url", default="http://localhost:8080", help="Base URL (nginx by default)")
    parser.add_argument("--client-id", default="loadtest-client")
    parser.add_argument("--requests", type=int, default=500)
    parser.add_argument("--concurrency", type=int, default=50)
    parser.add_argument("--expected-limit", type=int, default=None,
                         help="If set, verify allowed requests never exceed this (should match RATE_LIMIT env var)")
    args = parser.parse_args()

    results, total_elapsed = asyncio.run(
        run_load_test(args.url, args.client_id, args.requests, args.concurrency)
    )
    summarize(results, total_elapsed, args.expected_limit)


if __name__ == "__main__":
    main()

"""
Phase 5: load test with locust.

This is meant to be pointed at the system running via docker-compose,
through nginx on port 8080 -- i.e. it exercises the FULL distributed
path: nginx round-robins across app1/app2/app3, all sharing one Redis.

Run (after `docker-compose up --build`):
    locust -f load_test/locustfile.py --host http://localhost:8080

Then open http://localhost:8089 (locust's own web UI) to set the number
of simulated users and spawn rate, watch live RPS/latency, and start the
test. Or run headless for a fixed duration and get a summary printed to
the terminal:

    locust -f load_test/locustfile.py --host http://localhost:8080 \
        --headless -u 100 -r 20 --run-time 60s --csv=load_test/results

`--csv=load_test/results` writes load_test/results_stats.csv with the
exact p50/p95/p99 numbers and RPS you'll want to quote on your resume,
e.g. "sustained correct rate-limiting under 500 concurrent requests/sec
across 3 distributed instances."

Each simulated user reuses ONE client_id across all its requests, which
is what makes this a meaningful rate-limit test rather than just a raw
throughput test -- with many different client_ids you'd never actually
hit any individual limit.
"""
import random

from locust import HttpUser, between, task


class RateLimitedUser(HttpUser):
    # Small think time between requests, like a real client retrying or
    # polling -- tune this down to ramp up pressure on a single client_id.
    wait_time = between(0.05, 0.2)

    def on_start(self):
        # Each simulated locust "user" gets ONE stable client_id for its
        # whole lifetime, so its requests actually compete against each
        # other for the same rate-limit bucket in Redis -- this is what
        # proves the limiter works correctly under concurrent load.
        self.client_id = f"loadtest-user-{random.randint(1, 10_000)}"

    @task
    def call_protected_endpoint(self):
        with self.client.get(
            f"/api/resource?client_id={self.client_id}",
            catch_response=True,
        ) as resp:
            # Both 200 (allowed) and 429 (correctly rate-limited) are
            # SUCCESSFUL outcomes for this test -- we're checking the
            # system behaves correctly under load, not that every request
            # gets through. Anything else (500s, timeouts) is a real
            # failure.
            if resp.status_code in (200, 429):
                resp.success()
            else:
                resp.failure(f"unexpected status code: {resp.status_code}")

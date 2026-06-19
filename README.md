# Distributed Rate Limiter

A production-inspired distributed rate limiting service built with FastAPI, Redis, Docker, and Nginx.

This project explores how large-scale systems enforce request limits consistently across multiple application instances. It demonstrates distributed state management, concurrency control, atomic operations using Redis Lua scripts, load balancing, and correctness verification under concurrent load.

---

## Features

### Rate Limiting Algorithms

* Fixed Window Counter
* Sliding Window Log
* Redis-backed distributed state
* Atomic Redis Lua Script implementation
* Demonstration of naive vs race-free implementations

### Distributed Architecture

* Multiple FastAPI service instances
* Nginx load balancer
* Shared Redis datastore
* Stateless application servers
* Health monitoring endpoints

### Testing & Validation

* Unit tests
* Integration tests
* Race condition verification
* Concurrent load testing
* Distributed correctness validation

---

## Architecture

```text
                ┌─────────────┐
                │    Nginx    │
                │ Load Balancer
                └──────┬──────┘
                       │
        ┌──────────────┼──────────────┐
        │              │              │
        ▼              ▼              ▼
     FastAPI        FastAPI        FastAPI
      App1           App2           App3
        │              │              │
        └──────────────┼──────────────┘
                       │
                       ▼
                  Redis Server
             (Shared Rate Limit State)
```

Every application instance is stateless.

All rate limiting data is stored in Redis, allowing consistent enforcement across multiple service instances.

---

## Why Redis Lua Scripts?

A naive implementation often performs:

```python
count = redis.get(key)

if count < limit:
    redis.incr(key)
```

This introduces a race condition:

1. Request A reads count = 99
2. Request B reads count = 99
3. Both requests decide to allow
4. Both increment

Result:

```text
Allowed Requests > Configured Limit
```

To eliminate this issue, the project uses Redis Lua scripts.

Redis executes Lua scripts atomically, ensuring no other command can interleave between operations.

This guarantees correctness even under high concurrency and across multiple application instances.

---

## Project Structure

```text
rate-limiter/
│
├── app/
│   ├── algorithms_memory.py
│   ├── algorithms_redis.py
│   ├── main_phase1.py
│   └── main.py
│
├── load_test/
│   ├── locustfile.py
│   └── standalone_load_test.py
│
├── templates/
│   ├── index.html
│   └── health.html
│
├── tests/
│   ├── test_phase1.py
│   ├── test_race_condition.py
│   └── test_app_integration.py
│
├── Dockerfile
├── docker-compose.yml
├── nginx.conf
└── README.md
```

---

## Running the Project

### Clone Repository

```bash
git clone <repo-url>
cd rate-limiter
```

### Start Services

```bash
docker compose up --build
```

Services:

* Nginx → http://localhost:8080
* Redis → localhost:6379

---

## API Usage

### Request

```http
GET /api/resource?client_id=user123
```

### Successful Response

```json
{
  "status": "ok",
  "client_id": "user123",
  "served_by": "app1"
}
```

### Rate Limited Response

```json
{
  "error": "rate limit exceeded",
  "limit": 100,
  "window_seconds": 60
}
```

---

## Health Monitoring

```http
GET /healthz
```

Returns:

```json
{
  "status": "healthy",
  "instance_id": "app1",
  "redis_connected": true
}
```

---

## Load Testing

Run concurrent load tests:

```bash
python load_test/standalone_load_test.py \
  --url http://localhost:8080 \
  --client-id benchmark \
  --requests 500 \
  --concurrency 50 \
  --expected-limit 100
```

Example Result:

```text
=== Load test results ===

Total requests: 500
Throughput: 111.9 req/s

Allowed: 100
Blocked: 400

PASS: allowed requests never exceeded configured limit
```

This verifies that the distributed system correctly enforces a global limit across multiple service instances.

---

## Key Concepts Demonstrated

* Distributed Systems
* Concurrency Control
* Race Conditions
* Atomic Operations
* Redis Lua Scripting
* Horizontal Scaling
* Load Balancing
* Performance Testing
* System Reliability
* Backend Architecture

---

## Future Improvements

* Token Bucket Algorithm
* Prometheus Metrics
* Grafana Dashboards
* Kubernetes Deployment
* Distributed Tracing
* Adaptive Rate Limiting

---

## Tech Stack

**Backend**

* FastAPI
* Python
* Redis

**Infrastructure**

* Docker
* Docker Compose
* Nginx

**Testing**

* Pytest
* Locust
* AsyncIO
* HTTPX

---

## Lessons Learned

This project highlights an important distributed systems principle:

> Correctness is often harder than implementation.

A rate limiter that works on a single machine can fail under concurrency or horizontal scaling. By moving shared state into Redis and using atomic Lua scripts, the service maintains correctness even when requests are processed by multiple application instances simultaneously.

"""
Integration test for app/main.py using fakeredis instead of a real Redis
server. This validates the actual HTTP layer (FastAPI routes, status
codes, response shape) end-to-end -- not just the limiter classes in
isolation like test_phase1.py and test_race_condition.py do.

This does NOT require Docker or a real Redis. Two things make that work:
  1. We override the app's lifespan context manager so startup never
     tries to connect to a real Redis at REDIS_URL -- it wires up
     fakeredis instead.
  2. httpx's ASGITransport does NOT run ASGI lifespan events on its own
     (unlike Starlette's TestClient), so we drive the lifespan manually
     with `async with LifespanManager(...)`-equivalent logic: call
     `app.router.lifespan_context(app)` ourselves as an async context
     manager around each test.

Run with: pytest tests/test_app_integration.py -v
"""
from contextlib import asynccontextmanager

import fakeredis.aioredis
import pytest
from httpx import ASGITransport, AsyncClient

import app.main as main_module
from app.algorithms_redis import AtomicFixedWindowLimiter


@asynccontextmanager
async def fake_lifespan(app):
    main_module.redis_client = fakeredis.aioredis.FakeRedis()
    main_module.limiter = AtomicFixedWindowLimiter(
        main_module.redis_client, limit=3, window_seconds=60
    )
    yield
    await main_module.redis_client.close()


@pytest.fixture
async def client():
    main_module.app.router.lifespan_context = fake_lifespan

    # Manually drive the lifespan startup/shutdown since ASGITransport
    # doesn't do this automatically the way a real ASGI server would.
    async with fake_lifespan(main_module.app):
        transport = ASGITransport(app=main_module.app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac


@pytest.mark.asyncio
async def test_allows_requests_up_to_limit(client):
    for _ in range(3):
        resp = await client.get("/api/resource", params={"client_id": "alice"})
        assert resp.status_code == 200
        assert resp.json()["client_id"] == "alice"


@pytest.mark.asyncio
async def test_blocks_requests_over_limit(client):
    for _ in range(3):
        await client.get("/api/resource", params={"client_id": "alice"})

    resp = await client.get("/api/resource", params={"client_id": "alice"})
    assert resp.status_code == 429
    body = resp.json()
    assert body["detail"]["error"] == "rate limit exceeded"
    # Asserts against the limiter's actual configured limit (3, set above)
    # -- not a module-level global that could drift from it.
    assert body["detail"]["limit"] == 3


@pytest.mark.asyncio
async def test_healthz_reports_redis_connected(client):
    resp = await client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "healthy"
    assert body["redis_connected"] is True


@pytest.mark.asyncio
async def test_clients_have_independent_limits(client):
    for _ in range(3):
        resp = await client.get("/api/resource", params={"client_id": "alice"})
        assert resp.status_code == 200

    # bob hasn't made any requests yet -- shouldn't be blocked by alice's usage
    resp = await client.get("/api/resource", params={"client_id": "bob"})
    assert resp.status_code == 200

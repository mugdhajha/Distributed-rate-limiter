"""
Phase 3 & 4 entrypoint: the real distributed-ready app.

Every instance of this app is identical and stateless -- all rate-limit
state lives in Redis, shared across however many instances you run. This
is the file that gets run inside each Docker container in docker-compose.

Configuration is via environment variables so docker-compose can spin up
N identical containers that all behave the same way and all share one
Redis.

Run locally (single instance, needs a real Redis running on localhost:6379):
    REDIS_URL=redis://localhost:6379 uvicorn app.main:app --port 8000

Run distributed: see docker-compose.yml (3 instances + nginx + redis).
"""
import os
import socket
import time
from contextlib import asynccontextmanager

import redis.asyncio as redis
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.algorithms_redis import AtomicFixedWindowLimiter, AtomicSlidingWindowLimiter

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")
ALGO = os.environ.get("RATE_LIMIT_ALGO", "fixed_window")  # or "sliding_log"
LIMIT = int(os.environ.get("RATE_LIMIT", "100"))
WINDOW_SECONDS = int(os.environ.get("RATE_LIMIT_WINDOW", "60"))
INSTANCE_ID = os.environ.get("INSTANCE_ID", socket.gethostname())

redis_client: redis.Redis | None = None
limiter = None
templates = Jinja2Templates(directory="templates")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global redis_client, limiter
    redis_client = redis.from_url(REDIS_URL, decode_responses=True)
    if ALGO == "sliding_log":
        limiter = AtomicSlidingWindowLimiter(redis_client, limit=LIMIT, window_seconds=WINDOW_SECONDS)
    else:
        limiter = AtomicFixedWindowLimiter(redis_client, limit=LIMIT, window_seconds=WINDOW_SECONDS)
    yield
    if redis_client is not None:
        await redis_client.close()


app = FastAPI(title="Distributed Rate Limiter", lifespan=lifespan)


@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/api/resource")
async def get_resource(client_id: str = Query(..., description="Identifies the caller")):
    allowed = await limiter.allow(client_id)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "rate limit exceeded",
                # Read from the limiter instance, not the module-level LIMIT
                # global -- otherwise swapping in a differently-configured
                # limiter (e.g. in tests) silently reports the wrong limit.
                "limit": limiter.limit,
                "window_seconds": limiter.window_seconds,
                "algorithm": ALGO,
                "served_by": INSTANCE_ID,  # prove which instance handled this
            },
        )
    return {
        "status": "ok",
        "client_id": client_id,
        "served_by": INSTANCE_ID,  # this is how you'll prove load balancing + shared state works
        "served_at": time.time(),
    }


@app.get("/healthz", response_class=HTMLResponse)
async def healthz(request: Request):
    try:
        await redis_client.ping()
        redis_ok = True
    except Exception:
        redis_ok = False
    
    data = {
        "status": "healthy" if redis_ok else "degraded",
        "instance_id": INSTANCE_ID,
        "redis_connected": redis_ok,
        "algorithm": ALGO,
        "limit": limiter.limit if limiter else LIMIT,
        "window_seconds": limiter.window_seconds if limiter else WINDOW_SECONDS,
    }

    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse(data)
    
    return templates.TemplateResponse("health.html", {"request": request, "data": data})

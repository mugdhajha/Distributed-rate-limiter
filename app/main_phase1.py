"""
Phase 1 entrypoint: single FastAPI instance, in-memory rate limiting.

Run with:
    uvicorn app.main_phase1:app --reload --port 8000

Try it:
    for i in {1..15}; do curl -s -o /dev/null -w "%{http_code}\n" \
        "http://localhost:8000/api/resource?client_id=alice"; done

You should see 200s up to the limit, then 429s.
"""
import os

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse

from app.algorithms_memory import FixedWindowLimiter, SlidingWindowLogLimiter

ALGO = os.environ.get("RATE_LIMIT_ALGO", "fixed_window")  # or "sliding_log"
LIMIT = int(os.environ.get("RATE_LIMIT", "10"))
WINDOW_SECONDS = int(os.environ.get("RATE_LIMIT_WINDOW", "10"))

app = FastAPI(title="Rate Limiter - Phase 1 (in-memory, single instance)")

if ALGO == "sliding_log":
    limiter = SlidingWindowLogLimiter(limit=LIMIT, window_seconds=WINDOW_SECONDS)
else:
    limiter = FixedWindowLimiter(limit=LIMIT, window_seconds=WINDOW_SECONDS)


@app.get("/api/resource")
async def get_resource(client_id: str = Query(..., description="Identifies the caller")):
    if not limiter.allow(client_id):
        raise HTTPException(
            status_code=429,
            detail={
                "error": "rate limit exceeded",
                "limit": LIMIT,
                "window_seconds": WINDOW_SECONDS,
                "algorithm": ALGO,
            },
        )
    return {"status": "ok", "client_id": client_id, "served_at": __import__("time").time()}


@app.get("/healthz")
async def healthz():
    return JSONResponse({"status": "healthy", "algorithm": ALGO})


@app.get("/debug/count")
async def debug_count(client_id: str):
    """Inspect current counter state for a client -- handy while learning."""
    return {"client_id": client_id, "count": limiter.current_count(client_id)}

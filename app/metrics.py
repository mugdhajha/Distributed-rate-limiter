from prometheus_client import Counter, Histogram

allowed_requests_total = Counter(
    "allowed_requests_total",
    "Total number of allowed requests",
    ["instance"]
)

blocked_requests_total = Counter(
    "blocked_requests_total",
    "Total number of blocked requests",
    ["instance"]
)

request_latency_seconds = Histogram(
    "request_latency_seconds",
    "Request processing latency",
    ["instance"]
)
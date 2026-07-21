from collections import defaultdict, deque
from threading import Lock
from time import monotonic

from fastapi import HTTPException, Request, status


class SlidingWindowRateLimiter:
    def __init__(self, limit: int, window_seconds: int):
        self.limit = limit
        self.window_seconds = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def check(self, key: str) -> int | None:
        now = monotonic()
        window_start = now - self.window_seconds

        with self._lock:
            hits = self._hits[key]
            while hits and hits[0] <= window_start:
                hits.popleft()

            if len(hits) >= self.limit:
                retry_after = max(1, int(self.window_seconds - (now - hits[0])))
                return retry_after

            hits.append(now)
            return None

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


rsvp_ip_rate_limiter = SlidingWindowRateLimiter(limit=30, window_seconds=60)
rsvp_phone_rate_limiter = SlidingWindowRateLimiter(limit=5, window_seconds=600)
login_ip_rate_limiter = SlidingWindowRateLimiter(limit=20, window_seconds=300)
login_username_rate_limiter = SlidingWindowRateLimiter(limit=20, window_seconds=300)


def client_ip(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",", maxsplit=1)[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


def _raise_rate_limited(retry_after: int | None) -> None:
    headers = {}
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="Too many requests. Please try again later.",
        headers=headers,
    )


def enforce_rsvp_rate_limit(request: Request, phone: str) -> None:
    ip = client_ip(request)
    retry_after = rsvp_ip_rate_limiter.check(f"rsvp:ip:{ip}")
    if retry_after is not None:
        _raise_rate_limited(retry_after)

    retry_after = rsvp_phone_rate_limiter.check(f"rsvp:phone:{phone}")
    if retry_after is not None:
        _raise_rate_limited(retry_after)


def enforce_login_rate_limit(request: Request, username: str) -> None:
    ip = client_ip(request)
    normalized_username = username.strip().lower()

    retry_after = login_ip_rate_limiter.check(f"login:ip:{ip}")
    if retry_after is not None:
        _raise_rate_limited(retry_after)

    retry_after = login_username_rate_limiter.check(
        f"login:username:{normalized_username}",
    )
    if retry_after is not None:
        _raise_rate_limited(retry_after)


def reset_rate_limiters() -> None:
    rsvp_ip_rate_limiter.reset()
    rsvp_phone_rate_limiter.reset()
    login_ip_rate_limiter.reset()
    login_username_rate_limiter.reset()

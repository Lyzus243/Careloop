from typing import Any, Callable

from fastapi import APIRouter
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address, default_limits=["100/minute"])

# Attribute used to carry a per-route override onto the endpoint function.
RATE_LIMIT_ATTR = "_careloop_rate_limit"


def rate_limit(limit_value: str) -> Callable:
    """Override the router's default limit for a single endpoint.

    Must be applied *below* the route decorator so the marker is set before
    the route is registered:

        @router.post("/login")
        @rate_limit("10/minute")
        async def login(request: Request, ...):
            ...
    """
    def decorator(func: Callable) -> Callable:
        setattr(func, RATE_LIMIT_ATTR, limit_value)
        return func
    return decorator


class RateLimitedRouter(APIRouter):
    """APIRouter that actually applies a rate limit to every route it registers.

    slowapi requires the endpoint to declare a `request: Request` parameter; a
    route without one raises at import time rather than silently going
    unlimited.
    """

    def __init__(self, limit: str = "30/minute", **kwargs: Any):
        super().__init__(**kwargs)
        self.default_limit = limit

    def add_api_route(self, path: str, endpoint: Callable, **kwargs: Any) -> None:
        limit_value = getattr(endpoint, RATE_LIMIT_ATTR, self.default_limit)
        endpoint = limiter.limit(limit_value)(endpoint)
        return super().add_api_route(path, endpoint, **kwargs)


def add_rate_limit_exception_handler(app):
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

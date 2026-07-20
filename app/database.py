import time
from functools import lru_cache
from typing import Protocol, TypeVar

import httpx
from supabase import Client, create_client

from app.config import settings


READ_RETRY_DELAYS_SECONDS = (0.1, 0.2)
ReadResponse = TypeVar("ReadResponse", covariant=True)


class ReadQuery(Protocol[ReadResponse]):
    def execute(self) -> ReadResponse: ...


def execute_read(query: ReadQuery[ReadResponse]) -> ReadResponse:
    """Execute an idempotent Supabase query with bounded transport retries."""
    for delay in READ_RETRY_DELAYS_SECONDS:
        try:
            return query.execute()
        except httpx.TransportError:
            time.sleep(delay)

    return query.execute()


@lru_cache
def get_supabase() -> Client:
    return create_client(
        settings.supabase_url,
        settings.supabase_service_role_key,
    )

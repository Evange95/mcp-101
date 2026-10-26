"""HTTP details shared by every upstream adapter, with no domain knowledge."""

import time
from email.utils import parsedate_to_datetime

import httpx


def retry_after_seconds(response: httpx.Response, *, now: float | None = None) -> float | None:
    """How long the upstream asked us to wait (`Retry-After`), in seconds, if it said.

    The header is either a number of seconds or an HTTP date; anything else is ignored.
    """
    value = response.headers.get("Retry-After", "").strip()
    if not value:
        return None
    if value.isdigit():
        return float(value)
    try:
        moment = parsedate_to_datetime(value)
    except TypeError, ValueError:
        return None
    current = time.time() if now is None else now
    return max(0.0, moment.timestamp() - current)

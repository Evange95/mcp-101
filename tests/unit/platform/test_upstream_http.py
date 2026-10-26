"""Reading `Retry-After` from an upstream response."""

import httpx
import pytest

from pokedex_mcp.platform.upstream.http import retry_after_seconds


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ({"Retry-After": "3"}, 3.0),
        ({"Retry-After": "Thu, 01 Jan 1970 00:00:10 GMT"}, 7.0),  # 10 s epoch, "now" is 3 s
        ({"Retry-After": "Thu, 01 Jan 1970 00:00:01 GMT"}, 0.0),  # already past
        ({"Retry-After": "soon"}, None),
        ({}, None),
    ],
)
def test_retry_after_accepts_seconds_or_an_http_date(header, expected):
    response = httpx.Response(429, headers=header)

    assert retry_after_seconds(response, now=3.0) == expected

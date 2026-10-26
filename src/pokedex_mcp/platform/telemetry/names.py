"""Telemetry names shared by every catalog: one dashboard query covers them all.

Signals carry `catalog.name` ("pokedex", "digimon", ...) instead of a per-catalog prefix,
so `sum by (catalog_name) (catalog_lookups_total)` compares catalogs side by side. Every
catalog's probe adapters must take their names from here, never spell their own.
"""

# Attributes on spans, log records and (the low-cardinality ones) metrics.
CATALOG = "catalog.name"
LOOKUP = "catalog.lookup"
OUTCOME = "catalog.outcome"
RESOURCE = "catalog.resource"
CACHE_RESULT = "catalog.cache.result"
# Spans and logs only: unbounded values never go on metrics.
QUERY = "catalog.query"
KEY = "catalog.key"
REJECTION = "catalog.rejection"
RETRYABLE = "catalog.retryable"
RETRY_ATTEMPT = "catalog.retry.attempt"
RETRY_WAIT_SECONDS = "catalog.retry.wait_seconds"
RETRY_REASON = "catalog.retry.reason"

# Span events.
RETRY_EVENT = "catalog.retry"
UNEXPECTED_PAYLOAD_EVENT = "catalog.upstream.unexpected_payload"

# Metrics.
LOOKUPS = "catalog.lookups"
LOOKUP_DURATION = "catalog.lookup.duration"
CACHE_REQUESTS = "catalog.cache.requests"
CACHE_EVICTIONS = "catalog.cache.evictions"
SOURCE_RETRIES = "catalog.source.retries"
UNEXPECTED_PAYLOADS = "catalog.upstream.unexpected_payloads"

"""Source adapters.

Each adapter yields plain dicts shaped like `records` rows plus an optional
`curated` mapping of axis -> [terms] taken from structured source fields.
Adapters do no axis matching themselves; that is `normalize.annotate`'s job,
so the same rules apply to every source.
"""

from . import demo, geo, openalex  # noqa: F401

ADAPTERS = {
    "openalex": openalex,
    "geo": geo,
    "demo": demo,
}

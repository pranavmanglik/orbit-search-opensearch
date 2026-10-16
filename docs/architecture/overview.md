# Orbit OpenSearch: architecture and boundaries

## Responsibility

`orbit-search-opensearch` is the optional OpenSearch adapter for `orbit-search`. Install the capability
and this provider explicitly:

```bash
python -m pip install 'orbit-search' 'orbit-search-opensearch[opensearch]'
```

```python
from orbit_search_opensearch import OpenSearchStore
from orbit_search import SearchQuery

store = OpenSearchStore.connect(
    ["https://search.example.internal:9200"],
    http_auth=(username, password),  # load both values from a secret manager
    ca_certs="/etc/ssl/certs/search-ca.pem",
    cursor_secret=cursor_secret_from_secret_manager,
)
try:
    page = await store.search("articles", SearchQuery(text="async", fields=("title",)))
finally:
    await store.aclose()
```

The adapter translates portable full-text and exact-match filters into OpenSearch Query DSL. It
uses asynchronous `opensearch-py`, PIT plus `search_after` for a consistent bounded cursor, signs
opaque cursors with the configured shared secret, limits each page to 100 hits, refuses partial/time-out
results, sanitizes provider errors, propagates cancellation, and drains active searches before
closing its owned client. PITs expire after one minute; all workers serving a cursor must share the same high-entropy secret from a secret manager. Do not log cursors because they contain snapshot identifiers.

TLS certificate verification is enabled by default. Supply credentials through a secret manager;
there are no default credentials. For local development, HTTP/TLS overrides must be explicit.
The adapter does not provision indexes, ingest documents, manage mappings, retry searches, or
claim live-cluster compatibility. Validate permissions and the supported server/client versions
in your deployment environment before release.

This package is in alpha and supports Python 3.11–3.14. Its opt-in live integration check passed
against OpenSearch 2.19.1 with `opensearch-py` 3.2.0. That local check validates this combination;
it does not establish compatibility with other server/client versions or production topology.
The package suite passes on Python 3.11–3.14; on Python 3.12–3.14 the opt-in live test skips when no
service endpoint is configured.
OpenSearch's official async client guide recommends `opensearch-py[async]` and explicit client
closure; the provider APIs and lifecycle are owned by this package.

## Declared dependencies

The following dependency declarations come from the checked-in manifests. Optional groups and development dependencies are called out separately.

### `pyproject.toml`
- `orbit-search>=0.1.0a1,<0.2`
- Optional `opensearch` group: `opensearch-py[async]>=2.7,<4`.
- Optional `dev` group: `pytest>=8,<10`, `pytest-asyncio>=0.24,<2`, `ruff>=0.8,<1`, `mypy>=1.13,<2`.

Declared dependencies do not mean that optional providers or services are bundled with this package.

## Implementation layout

Representative implementation files in this checkout:

- `src/orbit_search_opensearch/__init__.py`
- `src/orbit_search_opensearch/store.py`

## Contract mapping and ownership

For each `SearchStore.search` call, this adapter validates the collection and query, translates the
portable query to OpenSearch request fields, opens or resumes a PIT, validates returned hits, and
normalizes provider errors to `orbit-search` error types. It requests one extra result to decide
whether a continuation cursor is needed, then returns only the requested number of hits.

The adapter owns its async client and closes it in `aclose()`. It has no implicit relationship to
Orbit Core lifecycle management; applications must arrange adapter shutdown. PIT management,
OpenSearch SDK types, sort strategy, and provider error translation stay in this repository.

The cursor is an adapter-private base64url payload containing PIT and sort state plus a query
fingerprint, authenticated by HMAC-SHA256. It is signed but not encrypted.

## Boundary rules

Keep OpenSearch SDK types, transport behavior, credentials, PIT lifecycle, sort details, and provider
error translation here. Keep portable query/result models in `orbit-search`. Do not add OpenSearch
query DSL to that shared contract.

# Orbit OpenSearch

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

## Documentation

The package-specific guides cover [architecture](docs/architecture/overview.md), [operations and security](docs/operations/README.md), and [development](docs/development/README.md), with [security guidance](docs/security/overview.md). The [documentation index](docs/README.md) links to the full package overview and project policies.

## Development

```bash
python -m pip install -e '.[dev,opensearch]'
pytest
ruff check src tests
ruff format --check src tests
mypy
```

Run the opt-in live test against a disposable local OpenSearch service. It provisions and removes one
uniquely named index and checks exact filters, point-in-time pagination, and client cleanup:

```bash
ORBIT_OPENSEARCH_URL='http://127.0.0.1:19201' pytest -q tests/test_live_opensearch.py
```

For remote test services, set `ORBIT_OPENSEARCH_ALLOW_REMOTE_TESTS=1`; optionally pass
`ORBIT_OPENSEARCH_USERNAME`, `ORBIT_OPENSEARCH_PASSWORD`, and `ORBIT_OPENSEARCH_CA_CERTS`.

Licensed under Apache-2.0.

